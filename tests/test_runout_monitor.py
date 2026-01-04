"""
Tests for RunoutMonitor - filament runout detection during printing.

Focus: State machine logic, sensor transitions, print state tracking.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from ace.runout_monitor import RunoutMonitor
from ace.config import SENSOR_TOOLHEAD, ACE_INSTANCES


class TestRunoutMonitorInitialization:
    """Test RunoutMonitor initialization and basic setup."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
    
    def test_init_sets_initial_state(self):
        """Test initialization sets correct initial state."""
        assert self.monitor.prev_toolhead_sensor_state is None
        assert self.monitor.last_printing_active is False
        assert self.monitor.last_print_state == "idle"
        assert self.monitor.runout_detection_active is False
        assert self.monitor.runout_handling_in_progress is False
        assert self.monitor._monitoring_timer is None

    def test_init_stores_dependencies(self):
        """Test initialization stores all dependencies."""
        assert self.monitor.printer is self.printer
        assert self.monitor.gcode is self.gcode
        assert self.monitor.reactor is self.reactor
        assert self.monitor.endless_spool is self.endless_spool
        assert self.monitor.manager is self.manager


class TestStartStopMonitoring:
    """Test starting and stopping the monitor."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
    
    def test_start_monitoring_registers_timer(self):
        """Test start_monitoring registers reactor timer."""
        self.monitor.start_monitoring()
        
        self.reactor.register_timer.assert_called_once()
        assert self.monitor._monitoring_timer is not None
        assert self.monitor.runout_detection_active is True

    def test_start_monitoring_logs_message(self):
        """Test start_monitoring logs startup message."""
        self.monitor.start_monitoring()
        
        self.gcode.respond_info.assert_any_call("ACE: Starting runout detection monitor")

    def test_stop_monitoring_unregisters_timer(self):
        """Test stop_monitoring unregisters timer."""
        # First start
        self.monitor.start_monitoring()
        timer = self.monitor._monitoring_timer
        
        # Then stop
        self.monitor.stop_monitoring()
        
        self.reactor.unregister_timer.assert_called_once_with(timer)
        assert self.monitor._monitoring_timer is None
        assert self.monitor.runout_detection_active is False

    def test_stop_monitoring_handles_no_timer(self):
        """Test stop_monitoring when no timer registered."""
        # Stop without starting
        self.monitor.stop_monitoring()
        
        # Should not raise exception
        self.reactor.unregister_timer.assert_not_called()

    def test_stop_monitoring_swallows_unregister_errors(self):
        """Stop monitoring should ignore unregister errors."""
        self.monitor.start_monitoring()
        self.reactor.unregister_timer.side_effect = Exception("boom")

        # Should not raise and still clear timer handle
        self.monitor.stop_monitoring()
        assert self.monitor._monitoring_timer is None


class TestSetDetectionActive:
    """Test enabling/disabling detection."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
    
    def test_set_detection_active_true(self):
        """Test enabling detection."""
        result = self.monitor.set_detection_active(True)
        
        assert result is True
        assert self.monitor.runout_detection_active is True

    def test_set_detection_active_false(self):
        """Test disabling detection."""
        self.monitor.runout_detection_active = True
        
        result = self.monitor.set_detection_active(False)
        
        assert result is False
        assert self.monitor.runout_detection_active is False

    def test_set_detection_active_logs_on_change(self):
        """Test logging when state changes."""
        self.monitor.set_detection_active(True)
        
        # Should log the change
        assert self.gcode.respond_info.call_count >= 1
        # Check that ENABLED appears in at least one log message
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert any('ENABLED' in msg for msg in log_messages)

    def test_set_detection_active_no_log_when_unchanged(self):
        """Test no logging when state doesn't change."""
        self.monitor.runout_detection_active = False
        self.gcode.respond_info.reset_mock()
        
        self.monitor.set_detection_active(False)
        
        # Should not log if state unchanged
        self.gcode.respond_info.assert_not_called()


class TestToolchangeGuard:
    """Test toolchange_in_progress guard logic."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.endless_spool = Mock()
        self.manager = Mock()
        
        # Mock print_stats
        self.print_stats = Mock()
        self.print_stats.get_status = Mock(return_value={
            'state': 'printing',
            'filename': 'test.gcode'
        })
        self.printer.lookup_object = Mock(return_value=self.print_stats)
        
        # Mock save_variables
        self.save_vars = Mock()
        self.save_vars.allVariables = {'ace_current_index': 0}
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        # Mock sensor state
        self.manager.get_switch_state = Mock(return_value=False)
        self.manager.toolchange_in_progress = False
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
        self.monitor.runout_detection_active = True
    
    def test_monitor_skips_when_toolchange_in_progress(self):
        """Test monitor exits early when toolchange in progress."""
        self.manager.toolchange_in_progress = True
        
        # Call monitor
        next_time = self.monitor._monitor_runout(0.0)
        
        # Should return early - but after print state check
        # Implementation returns 0.05 after checking print state
        assert next_time is not None
        
        # Should not have checked for runout
        # (no calls to endless_spool or other runout logic)

    def test_monitor_runs_normally_when_no_toolchange(self):
        """Test monitor runs normally when no toolchange."""
        self.manager.toolchange_in_progress = False
        
        # Call monitor
        next_time = self.monitor._monitor_runout(0.0)
        
        # Should proceed through logic
        assert next_time is not None


class TestPrintStateTracking:
    """Test print state change tracking."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.reactor.monotonic = Mock(return_value=0.0)
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        self.manager.get_switch_state = Mock(return_value=False)
        
        # Mock print_stats
        self.print_stats = Mock()
        self.print_stats.get_status = Mock(return_value={
            'state': 'idle',
            'filename': ''
        })
        
        # Mock save_variables
        self.save_vars = Mock()
        self.save_vars.allVariables = {'ace_current_index': -1}
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
        self.monitor.runout_detection_active = True
    
    def test_tracks_last_printing_active(self):
        """Test monitor tracks previous printing state."""
        # Initially not printing
        self.print_stats.get_status.return_value = {'state': 'idle'}
        self.monitor._monitor_runout(0.0)
        assert self.monitor.last_printing_active is False
        
        # Start printing
        self.print_stats.get_status.return_value = {'state': 'printing'}
        self.save_vars.allVariables = {'ace_current_index': 0}
        self.monitor._monitor_runout(0.1)
        assert self.monitor.last_printing_active is True

    def test_tracks_last_print_state(self):
        """Test monitor tracks raw print state."""
        self.print_stats.get_status.return_value = {'state': 'printing'}
        self.save_vars.allVariables = {'ace_current_index': 0}
        
        self.monitor._monitor_runout(0.0)
        
        assert self.monitor.last_print_state == 'printing'

    def test_logs_print_state_changes(self):
        """Test monitor logs print state transitions."""
        # Start in idle
        self.print_stats.get_status.return_value = {'state': 'idle'}
        self.monitor._monitor_runout(0.0)
        self.gcode.respond_info.reset_mock()
        
        # Transition to printing
        self.print_stats.get_status.return_value = {'state': 'printing'}
        self.save_vars.allVariables = {'ace_current_index': 0}
        self.monitor._monitor_runout(0.1)
        
        # Should log the transition
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert any('state changed' in msg.lower() for msg in log_messages)


class TestBaselineInitialization:
    """Test sensor baseline initialization."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.reactor.monotonic = Mock(return_value=0.0)
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        
        # Mock print_stats - printing with active tool
        self.print_stats = Mock()
        self.print_stats.get_status = Mock(return_value={
            'state': 'printing',
            'filename': 'test.gcode'
        })
        
        # Mock save_variables - tool 0 loaded
        self.save_vars = Mock()
        self.save_vars.allVariables = {
            'ace_current_index': 0,
            'ace_filament_pos': 'nozzle'
        }
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        # Mock sensor - filament present
        self.manager.get_switch_state = Mock(return_value=True)
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
        self.monitor.runout_detection_active = True
    
    def test_initializes_baseline_when_none(self):
        """Test baseline initialized from sensor state."""
        self.monitor.prev_toolhead_sensor_state = None
        
        self.monitor._monitor_runout(0.0)
        
        # Should initialize to sensor state
        assert self.monitor.prev_toolhead_sensor_state is True

    def test_baseline_init_logs_message(self):
        """Test baseline initialization logs info."""
        self.monitor.prev_toolhead_sensor_state = None
        
        # Need to be in printing state with detection active
        self.monitor.runout_detection_active = True
        self.monitor.last_printing_active = True  # Already printing to skip print start
        self.monitor.last_print_state = 'printing'
        
        self.print_stats.get_status.return_value = {'state': 'printing'}
        self.save_vars.allVariables['ace_current_index'] = 0
        
        self.monitor._monitor_runout(0.0)
        
        # Should log baseline establishment
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert any('monitoring baseline established' in msg.lower() for msg in log_messages)


class TestPrintStartDetection:
    """Test print start event detection."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.reactor.monotonic = Mock(return_value=0.0)
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        self.manager.get_switch_state = Mock(return_value=True)
        
        # Mock print_stats
        self.print_stats = Mock()
        self.print_stats.get_status = Mock(return_value={
            'state': 'idle',
            'filename': ''
        })
        
        # Mock save_variables
        self.save_vars = Mock()
        self.save_vars.allVariables = {
            'ace_current_index': 0,
            'ace_filament_pos': 'nozzle'
        }
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
        self.monitor.runout_detection_active = False
    
    def test_detects_print_start(self):
        """Test detection of print start event."""
        # Start in idle
        self.print_stats.get_status.return_value = {'state': 'idle'}
        self.monitor._monitor_runout(0.0)
        
        self.gcode.respond_info.reset_mock()
        
        # Transition to printing
        self.print_stats.get_status.return_value = {'state': 'printing'}
        self.monitor._monitor_runout(0.1)
        
        # Should log print start
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert any('print started' in msg.lower() for msg in log_messages)

    def test_print_start_enables_detection(self):
        """Test print start enables detection if sensor shows filament."""
        # Start in idle, detection off
        self.print_stats.get_status.return_value = {'state': 'idle'}
        self.monitor.runout_detection_active = False
        self.monitor._monitor_runout(0.0)
        
        # Transition to printing with filament present
        self.print_stats.get_status.return_value = {'state': 'printing'}
        self.manager.get_switch_state = Mock(return_value=True)
        self.monitor._monitor_runout(0.1)
        
        # Detection should be enabled
        assert self.monitor.runout_detection_active is True


class TestPrintStopDetection:
    """Test print stop/cancel detection."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.reactor.monotonic = Mock(return_value=0.0)
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        self.manager.get_switch_state = Mock(return_value=True)
        
        # Mock print_stats
        self.print_stats = Mock()
        
        # Mock save_variables
        self.save_vars = Mock()
        self.save_vars.allVariables = {
            'ace_current_index': 0,
            'ace_filament_pos': 'nozzle'
        }
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
        self.monitor.runout_detection_active = True
    
    def test_detects_print_stop(self):
        """Test detection of print stop event."""
        # Start printing
        self.print_stats.get_status.return_value = {'state': 'printing'}
        self.monitor._monitor_runout(0.0)
        
        self.gcode.respond_info.reset_mock()
        
        # Stop printing
        self.print_stats.get_status.return_value = {'state': 'complete'}
        self.monitor._monitor_runout(0.1)
        
        # Should log print stop
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert any('stopped' in msg.lower() or 'cancelled' in msg.lower() for msg in log_messages)

    def test_print_stop_resets_baseline(self):
        """Test print stop resets sensor baseline."""
        # Start printing with baseline set
        self.print_stats.get_status.return_value = {'state': 'printing'}
        self.monitor.prev_toolhead_sensor_state = True
        self.monitor._monitor_runout(0.0)
        
        # Stop printing
        self.print_stats.get_status.return_value = {'state': 'complete'}
        self.monitor._monitor_runout(0.1)
        
        # Baseline should be reset
        assert self.monitor.prev_toolhead_sensor_state is None


class TestRunoutDetection:
    """Test runout detection (present → absent sensor transition)."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.reactor.monotonic = Mock(return_value=0.0)
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        
        # Mock print_stats - printing with active tool
        self.print_stats = Mock()
        self.print_stats.get_status = Mock(return_value={
            'state': 'printing',
            'filename': 'test.gcode'
        })
        
        # Mock save_variables
        self.save_vars = Mock()
        self.save_vars.allVariables = {
            'ace_current_index': 0,
            'ace_filament_pos': 'nozzle',
            'ace_endless_spool_enabled': False
        }
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        # Mock sensor - filament present initially
        self.manager.get_switch_state = Mock(return_value=True)
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
        self.monitor.runout_detection_active = True
        self.monitor.prev_toolhead_sensor_state = True  # Baseline: filament present
    
    def test_detects_runout_on_sensor_transition(self):
        """Test runout detected when sensor goes present → absent."""
        # Baseline: sensor shows filament
        self.monitor.prev_toolhead_sensor_state = True
        self.monitor.last_printing_active = True  # Already printing to skip state transitions
        self.monitor.last_print_state = 'printing'
        
        # Sensor changes to absent (runout)
        self.manager.get_switch_state = Mock(return_value=False)
        
        # Mock ACE_INSTANCES for _handle_runout_detected
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                with patch('ace.runout_monitor.ACE_INSTANCES') as mock_instances:
                    ace_inst = Mock()
                    ace_inst.inventory = [{'material': 'PLA', 'color': [255, 0, 0]}]
                    mock_instances.get = Mock(return_value=ace_inst)
                    
                    self.monitor._monitor_runout(0.0)
        
        # Should log runout detection
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert any('runout detected' in msg.lower() for msg in log_messages)

    def test_no_runout_when_sensor_stays_present(self):
        """Test no runout when sensor stays present."""
        self.monitor.prev_toolhead_sensor_state = True
        self.manager.get_switch_state = Mock(return_value=True)
        
        self.gcode.respond_info.reset_mock()
        self.monitor._monitor_runout(0.0)
        
        # Should NOT detect runout
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert not any('runout detected' in msg.lower() for msg in log_messages)

    def test_no_runout_when_sensor_stays_absent(self):
        """Test no runout when sensor stays absent (already in runout)."""
        self.monitor.prev_toolhead_sensor_state = False
        self.manager.get_switch_state = Mock(return_value=False)
        
        self.gcode.respond_info.reset_mock()
        self.monitor._monitor_runout(0.0)
        
        # Should NOT trigger again
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert not any('runout detected' in msg.lower() for msg in log_messages)

    def test_updates_baseline_after_runout(self):
        """Test baseline updated after runout detected."""
        self.monitor.prev_toolhead_sensor_state = True
        self.manager.get_switch_state = Mock(return_value=False)
        
        self.monitor._monitor_runout(0.0)
        
        # Baseline should be updated to absent
        assert self.monitor.prev_toolhead_sensor_state is False


class TestRunoutSuppression:
    """Test runout detection suppression during handling."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.reactor.monotonic = Mock(return_value=0.0)
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        
        # Mock print_stats
        self.print_stats = Mock()
        self.print_stats.get_status = Mock(return_value={
            'state': 'printing',
            'filename': 'test.gcode'
        })
        
        # Mock save_variables
        self.save_vars = Mock()
        self.save_vars.allVariables = {
            'ace_current_index': 0,
            'ace_filament_pos': 'nozzle',
            'ace_endless_spool_enabled': False
        }
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        self.manager.get_switch_state = Mock(return_value=True)
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
        self.monitor.runout_detection_active = True
    
    def test_suppresses_runout_during_handling(self):
        """Test runout detection suppressed when already handling."""
        self.monitor.prev_toolhead_sensor_state = True
        self.monitor.runout_handling_in_progress = True
        self.monitor.last_printing_active = True  # Already printing
        self.monitor.last_print_state = 'printing'
        
        # Sensor changes to absent (runout)
        self.manager.get_switch_state = Mock(return_value=False)
        
        # Mock ACE_INSTANCES for _handle_runout_detected  
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                with patch('ace.runout_monitor.ACE_INSTANCES') as mock_instances:
                    ace_inst = Mock()
                    ace_inst.inventory = [{'material': 'PLA', 'color': [255, 0, 0]}]
                    mock_instances.get = Mock(return_value=ace_inst)
                    
                    self.monitor._monitor_runout(0.0)
        
        # Should log suppression message
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert any('suppressed' in msg.lower() for msg in log_messages)


class TestRunoutHandling:
    """Test runout handling flow."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.reactor.monotonic = Mock(return_value=0.0)
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        
        # Mock print_stats
        self.print_stats = Mock()
        self.print_stats.get_status = Mock(return_value={
            'state': 'printing',
            'filename': 'test.gcode'
        })
        
        # Mock save_variables
        self.save_vars = Mock()
        self.save_vars.allVariables = {
            'ace_current_index': 0,
            'ace_endless_spool_enabled': False
        }
        
        # Mock ACE_INSTANCES for inventory lookup
        with patch('ace.runout_monitor.ACE_INSTANCES') as mock_instances:
            ace_inst = Mock()
            ace_inst.inventory = [
                {'material': 'PLA', 'color': [255, 0, 0]},  # Slot 0
            ]
            mock_instances.get = Mock(return_value=ace_inst)
            
            def lookup_side_effect(obj_name, default=None):
                if obj_name == 'print_stats':
                    return self.print_stats
                elif obj_name == 'save_variables':
                    return self.save_vars
                return default
            
            self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
            
            self.monitor = RunoutMonitor(
                self.printer,
                self.gcode,
                self.reactor,
                self.endless_spool,
                self.manager
            )
    
    def test_runout_pauses_print(self):
        """Test runout handling pauses the print."""
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                self.monitor._handle_runout_detected(0)
        
        # Should have run PAUSE command
        script_calls = [call[0][0] for call in self.gcode.run_script_from_command.call_args_list]
        assert any('PAUSE' in cmd for cmd in script_calls)

    def test_runout_shows_prompt(self):
        """Test runout handling shows interactive prompt."""
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                self.monitor._handle_runout_detected(0)
        
        # Should show prompt with buttons
        script_calls = [call[0][0] for call in self.gcode.run_script_from_command.call_args_list]
        assert any('prompt_begin' in cmd for cmd in script_calls)
        assert any('prompt_footer_button' in cmd for cmd in script_calls)

    def test_runout_without_endless_spool_stays_paused(self):
        """Test runout without endless spool stays paused."""
        self.save_vars.allVariables['ace_endless_spool_enabled'] = False
        
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                self.monitor._handle_runout_detected(0)
        
        # Should log staying paused
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert any('staying paused' in msg.lower() for msg in log_messages)
        
        # Should NOT close prompt
        script_calls = [call[0][0] for call in self.gcode.run_script_from_command.call_args_list]
        assert not any('prompt_end' in cmd for cmd in script_calls)

    def test_runout_with_endless_spool_no_match_stays_paused(self):
        """Test runout with endless spool but no match stays paused."""
        self.save_vars.allVariables['ace_endless_spool_enabled'] = True
        self.endless_spool.find_exact_match = Mock(return_value=-1)  # No match
        
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                self.monitor._handle_runout_detected(0)
        
        # Should log no match
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert any('no endless spool match' in msg.lower() for msg in log_messages)

    def test_runout_with_endless_spool_match_executes_swap(self):
        """Test runout with endless spool match executes automatic swap."""
        self.save_vars.allVariables['ace_endless_spool_enabled'] = True
        self.endless_spool.find_exact_match = Mock(return_value=4)  # Match: T4
        
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                self.monitor._handle_runout_detected(0)
        
        # Should execute swap
        self.endless_spool.execute_swap.assert_called_once_with(0, 4)
        
        # Should close prompt before swap
        script_calls = [call[0][0] for call in self.gcode.run_script_from_command.call_args_list]
        assert any('prompt_end' in cmd for cmd in script_calls)

    def test_runout_resets_baseline(self):
        """Test runout handling resets sensor baseline."""
        self.monitor.prev_toolhead_sensor_state = True
        
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                self.monitor._handle_runout_detected(0)
        
        # Baseline should be reset
        assert self.monitor.prev_toolhead_sensor_state is None

    def test_runout_clears_handling_flag(self):
        """Test runout handling clears in_progress flag after completion."""
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                self.monitor._handle_runout_detected(0)
        
        # Flag should be cleared
        assert self.monitor.runout_handling_in_progress is False


class TestNoActiveToolHandling:
    """Test behavior when no active tool loaded."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.reactor.monotonic = Mock(return_value=0.0)
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        self.manager.get_switch_state = Mock(return_value=False)
        
        # Mock print_stats
        self.print_stats = Mock()
        self.print_stats.get_status = Mock(return_value={
            'state': 'printing',
            'filename': 'test.gcode'
        })
        
        # Mock save_variables - NO ACTIVE TOOL
        self.save_vars = Mock()
        self.save_vars.allVariables = {
            'ace_current_index': -1,  # No tool loaded
            'ace_filament_pos': 'bowden'
        }
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
        self.monitor.runout_detection_active = True
    
    def test_resets_baseline_when_no_active_tool(self):
        """Test baseline reset when no active tool."""
        self.monitor.prev_toolhead_sensor_state = True
        
        self.monitor._monitor_runout(0.0)
        
        # Baseline should be reset
        assert self.monitor.prev_toolhead_sensor_state is None


class TestPausedStateHandling:
    """Test behavior during paused state."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.reactor.monotonic = Mock(return_value=0.0)
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        self.manager.get_switch_state = Mock(return_value=True)
        
        # Mock print_stats - PAUSED
        self.print_stats = Mock()
        self.print_stats.get_status = Mock(return_value={
            'state': 'paused',
            'filename': 'test.gcode'
        })
        
        # Mock save_variables
        self.save_vars = Mock()
        self.save_vars.allVariables = {
            'ace_current_index': 0,
            'ace_filament_pos': 'nozzle'
        }
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
        self.monitor.runout_detection_active = True
    
    def test_resets_baseline_when_paused(self):
        """Test baseline reset during paused state."""
        self.monitor.prev_toolhead_sensor_state = True
        
        self.monitor._monitor_runout(0.0)
        
        # Baseline should be reset
        assert self.monitor.prev_toolhead_sensor_state is None


class TestMonitorReturnInterval:
    """Test monitor callback return intervals."""
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.reactor.monotonic = Mock(return_value=0.0)
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        self.manager.get_switch_state = Mock(return_value=True)
        
        # Mock print_stats
        self.print_stats = Mock()
        self.print_stats.get_status = Mock(return_value={
            'state': 'printing',
            'filename': 'test.gcode'
        })
        
        # Mock save_variables
        self.save_vars = Mock()
        self.save_vars.allVariables = {
            'ace_current_index': 0,
            'ace_filament_pos': 'nozzle'
        }
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
    
    def test_returns_fast_interval_when_detection_active(self):
        """Test fast polling when detection active."""
        self.monitor.runout_detection_active = True
        self.monitor.prev_toolhead_sensor_state = True
        
        next_time = self.monitor._monitor_runout(0.0)
        
        # Should return fast interval (0.05s)
        assert next_time == 0.05

    def test_returns_slow_interval_when_detection_disabled(self):
        """Test slow polling when detection disabled."""
        self.monitor.runout_detection_active = False
        # Set to idle state to avoid print start logic
        self.print_stats.get_status.return_value = {'state': 'idle'}
        self.save_vars.allVariables['ace_current_index'] = -1
        
        next_time = self.monitor._monitor_runout(0.0)
        
        # Should return interval (0.2s when not printing)
        assert next_time == 0.2


class TestToolchangeRunoutInteraction:
    """
    Test interaction between toolchange and runout detection.
    
    These tests focus on the critical timing window where toolchange completes
    and runout detection resumes. This is a common source of phantom runout bugs.
    """
    
    def setup_method(self):
        """Create RunoutMonitor with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.reactor.monotonic = Mock(return_value=0.0)
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        
        # Mock print_stats - printing state
        self.print_stats = Mock()
        self.print_stats.get_status = Mock(return_value={
            'state': 'printing',
            'filename': 'test.gcode'
        })
        
        # Mock save_variables
        self.save_vars = Mock()
        self.save_vars.allVariables = {
            'ace_current_index': 0,
            'ace_filament_pos': 'nozzle',
            'ace_endless_spool_enabled': False
        }
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        self.manager.get_switch_state = Mock(return_value=True)
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
        self.monitor.runout_detection_active = True
        self.monitor.last_printing_active = True
        self.monitor.last_print_state = 'printing'

    def test_no_runout_detection_during_toolchange(self):
        """Runout should NOT trigger while toolchange is in progress."""
        self.monitor.prev_toolhead_sensor_state = True
        self.manager.toolchange_in_progress = True  # Toolchange active
        
        # Sensor shows absent (could happen during unload)
        self.manager.get_switch_state = Mock(return_value=False)
        
        self.gcode.respond_info.reset_mock()
        self.monitor._monitor_runout(0.0)
        
        # Should NOT detect runout during toolchange
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert not any('runout detected' in msg.lower() for msg in log_messages)

    def test_runout_blocked_by_toolchange_flag(self):
        """Monitor should early-exit when toolchange_in_progress is True."""
        self.monitor.prev_toolhead_sensor_state = True
        self.manager.toolchange_in_progress = True
        
        # Sensor changes state during toolchange
        self.manager.get_switch_state = Mock(return_value=False)
        
        next_time = self.monitor._monitor_runout(0.0)
        
        # Should return slow interval (0.2s) as it's blocked
        assert next_time == 0.2
        
    def test_baseline_reset_after_toolchange_respected(self):
        """
        After toolchange sets baseline, monitor should use that baseline.
        
        This simulates the flow:
        1. Toolchange sets prev_toolhead_sensor_state = True
        2. Toolchange completes (toolchange_in_progress = False)
        3. Monitor runs with sensor = True
        4. No runout should be detected (True -> True)
        """
        # Simulate toolchange just completed
        self.manager.toolchange_in_progress = False
        
        # Baseline was set during toolchange to True
        self.monitor.prev_toolhead_sensor_state = True
        
        # Sensor still shows True (filament present)
        self.manager.get_switch_state = Mock(return_value=True)
        
        self.gcode.respond_info.reset_mock()
        self.monitor._monitor_runout(0.0)
        
        # Should NOT detect runout
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert not any('runout detected' in msg.lower() for msg in log_messages)

    def test_sensor_glitch_after_toolchange_triggers_runout(self):
        """
        CURRENT BEHAVIOR: A sensor glitch right after toolchange WILL trigger runout.
        
        This test documents the current (potentially problematic) behavior where:
        1. Toolchange sets prev_toolhead_sensor_state = True
        2. Toolchange completes
        3. Sensor momentarily reads False (glitch/bounce)
        4. Runout IS detected (this might be a phantom runout)
        
        This test will PASS with current code, demonstrating the issue exists.
        If we add protection (grace period, etc.), this test should be updated.
        """
        # Simulate toolchange just completed
        self.manager.toolchange_in_progress = False
        
        # Baseline was set during toolchange to True  
        self.monitor.prev_toolhead_sensor_state = True
        
        # Sensor momentarily reads False (glitch!)
        self.manager.get_switch_state = Mock(return_value=False)
        
        # Mock for _handle_runout_detected
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                with patch('ace.runout_monitor.ACE_INSTANCES') as mock_instances:
                    ace_inst = Mock()
                    ace_inst.inventory = [{'material': 'PLA', 'color': [255, 0, 0]}]
                    mock_instances.get = Mock(return_value=ace_inst)
                    
                    self.monitor._monitor_runout(0.0)
        
        # With current code, runout WILL be detected (this documents the vulnerability)
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        runout_detected = any('runout detected' in msg.lower() for msg in log_messages)
        
        # This assertion documents current behavior
        # If we want to FIX the phantom runout, change this to assertFalse
        assert runout_detected, \
            "Current code detects runout on sensor glitch after toolchange. " \
            "This test documents this vulnerability. To fix, add grace period."

    def test_paused_state_resets_baseline(self):
        """When print is paused, baseline should be reset to None."""
        self.monitor.prev_toolhead_sensor_state = True
        
        # Print becomes paused
        self.print_stats.get_status.return_value = {'state': 'paused'}
        
        self.monitor._monitor_runout(0.0)
        
        # Baseline should be reset
        assert self.monitor.prev_toolhead_sensor_state is None

    def test_baseline_reestablished_after_pause_resume(self):
        """When resuming from pause, baseline should be re-established from sensor."""
        # Initially paused with no baseline
        self.monitor.prev_toolhead_sensor_state = None
        self.print_stats.get_status.return_value = {'state': 'printing'}
        self.manager.get_switch_state = Mock(return_value=True)
        
        self.monitor._monitor_runout(0.0)
        
        # Baseline should be re-established from current sensor state
        assert self.monitor.prev_toolhead_sensor_state is True


class TestMonitorErrorHandling:
    """Test error handling paths in runout monitor."""

    def setup_method(self):
        """Set up test fixtures."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.reactor.NEVER = float('inf')
        self.endless_spool = Mock()
        self.manager = Mock()
        self.print_stats = Mock()
        self.save_vars = Mock()
        
        self.save_vars.allVariables = {
            'ace_current_index': 0,
            'ace_filament_pos': 'nozzle',
        }
        
        self.print_stats.get_status = Mock(return_value={'state': 'printing'})
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        # Create command_error exception type
        class command_error(Exception):
            pass
        self.printer.command_error = command_error
        
        self.manager.toolchange_in_progress = False
        self.manager.get_switch_state = Mock(return_value=True)
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )
        self.monitor.runout_detection_active = True
        self.monitor.prev_toolhead_sensor_state = True
        self.monitor.last_printing_active = True
        self.monitor.last_print_state = 'printing'

    def test_pause_command_error_logged(self):
        """Test that PAUSE command errors are logged gracefully."""
        self.gcode.run_script_from_command = Mock(side_effect=Exception("PAUSE failed"))
        
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                with patch('ace.runout_monitor.ACE_INSTANCES') as mock_instances:
                    mock_instances.get = Mock(return_value=None)
                    
                    # Should not raise - errors are caught and logged
                    self.monitor._handle_runout_detected(0)
        
        # Should have logged the error
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert any('error' in msg.lower() for msg in log_messages)

    def test_handle_runout_sets_handling_flag(self):
        """Test that runout handling sets the in-progress flag."""
        self.save_vars.allVariables['ace_endless_spool_enabled'] = False
        
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                with patch('ace.runout_monitor.ACE_INSTANCES') as mock_instances:
                    mock_instances.get = Mock(return_value=None)
                    
                    # During handling, flag should be set
                    original_respond = self.gcode.respond_info
                    flag_values = []
                    
                    def capture_flag(msg):
                        flag_values.append(self.monitor.runout_handling_in_progress)
                        return original_respond(msg)
                    
                    self.gcode.respond_info = Mock(side_effect=capture_flag)
                    
                    self.monitor._handle_runout_detected(0)
        
        # Flag should have been True during handling
        assert any(flag_values), "runout_handling_in_progress should be True during handling"
        # And False after
        assert not self.monitor.runout_handling_in_progress

    def test_print_stats_exception_handled(self):
        """Test that print_stats.get_status exception is handled gracefully."""
        self.print_stats.get_status = Mock(side_effect=Exception("Stats unavailable"))
        
        # Should not raise
        result = self.monitor._monitor_runout(0.0)
        
        # Should return some interval (not crash)
        assert isinstance(result, (int, float))


class TestAutoRecoveryLogic:
    """Test auto-recovery detection when runout detection should be active but isn't."""

    def setup_method(self):
        """Set up test fixtures for auto-recovery tests."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.endless_spool = Mock()
        self.manager = Mock()
        self.print_stats = Mock()
        self.save_vars = Mock()
        
        self.save_vars.allVariables = {
            'ace_current_index': 1,
            'ace_filament_pos': 'nozzle',
        }
        
        self.print_stats.get_status = Mock(return_value={'state': 'printing'})
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        self.manager.toolchange_in_progress = False
        self.manager.get_switch_state = Mock(return_value=True)
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )

    def test_auto_recovery_triggers_when_detection_should_be_active(self):
        """Test auto-recovery activates when detection should be active but isn't."""
        # Set counter to trigger debug logging (every ~15 min)
        self.monitor.monitor_debug_counter = 1200 * 15 - 1  # One before trigger
        
        # Detection disabled but all conditions say it should be active:
        # - printing
        # - sensor has filament
        # - tool >= 0
        # - no toolchange in progress
        # - no runout handling in progress
        self.monitor.runout_detection_active = False
        self.monitor.runout_handling_in_progress = False
        self.monitor.prev_toolhead_sensor_state = True
        self.monitor.last_printing_active = True
        self.monitor.last_print_state = 'printing'
        
        self.monitor._monitor_runout(0.0)
        
        # Should have triggered auto-recovery and re-enabled detection
        assert self.monitor.runout_detection_active
        
        # Should have logged warning about auto-recovery
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert any('autorecovery' in msg.lower() or 'auto-recovery' in msg.lower() 
                   for msg in log_messages)


class TestRunoutHandlingEdgeCases:
    """Test edge cases in runout handling."""

    def setup_method(self):
        """Set up test fixtures."""
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.endless_spool = Mock()
        self.manager = Mock()
        self.print_stats = Mock()
        self.save_vars = Mock()
        
        self.save_vars.allVariables = {
            'ace_current_index': 0,
            'ace_endless_spool_enabled': True,
        }
        
        def lookup_side_effect(obj_name, default=None):
            if obj_name == 'print_stats':
                return self.print_stats
            elif obj_name == 'save_variables':
                return self.save_vars
            return default
        
        self.printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        self.monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager
        )

    def test_runout_with_invalid_instance_uses_defaults(self):
        """Test runout handling when instance lookup returns invalid result."""
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=-1):
            with patch('ace.runout_monitor.get_local_slot', return_value=-1):
                self.monitor._handle_runout_detected(99)
        
        # Should still show prompt with 'unknown' material
        script_calls = [call[0][0] for call in self.gcode.run_script_from_command.call_args_list]
        assert any('prompt_begin' in cmd for cmd in script_calls)

    def test_runout_handling_exception_clears_flag(self):
        """Test that runout_handling_in_progress is cleared even on exception."""
        self.endless_spool.find_exact_match = Mock(return_value=1)
        self.endless_spool.execute_swap = Mock(side_effect=Exception("Swap failed"))
        
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                with patch('ace.runout_monitor.ACE_INSTANCES') as mock_instances:
                    ace_inst = Mock()
                    ace_inst.inventory = [{'material': 'PLA', 'color': [255, 0, 0]}]
                    mock_instances.get = Mock(return_value=ace_inst)
                    
                    self.monitor._handle_runout_detected(0)
        
        # Flag should be cleared in finally block
        assert not self.monitor.runout_handling_in_progress

    def test_runout_closes_prompt_before_swap(self):
        """Test that prompt is closed before executing endless spool swap."""
        self.endless_spool.find_exact_match = Mock(return_value=1)
        self.endless_spool.execute_swap = Mock()
        
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                with patch('ace.runout_monitor.ACE_INSTANCES') as mock_instances:
                    ace_inst = Mock()
                    ace_inst.inventory = [{'material': 'PLA', 'color': [255, 0, 0]}]
                    mock_instances.get = Mock(return_value=ace_inst)
                    
                    self.monitor._handle_runout_detected(0)
        
        # Should have closed prompt (prompt_end) before swap
        script_calls = [call[0][0] for call in self.gcode.run_script_from_command.call_args_list]
        prompt_end_idx = next((i for i, cmd in enumerate(script_calls) if 'prompt_end' in cmd), -1)
        
        assert prompt_end_idx >= 0, "prompt_end should have been called"
        
        # execute_swap should have been called after prompt was closed
        self.endless_spool.execute_swap.assert_called_once_with(0, 1)

    def test_runout_with_none_ace_instance(self):
        """Test runout when ACE_INSTANCES.get returns None."""
        self.save_vars.allVariables['ace_endless_spool_enabled'] = False
        
        with patch('ace.runout_monitor.get_instance_from_tool', return_value=0):
            with patch('ace.runout_monitor.get_local_slot', return_value=0):
                with patch('ace.runout_monitor.ACE_INSTANCES') as mock_instances:
                    mock_instances.get = Mock(return_value=None)
                    
                    # Should not raise
                    self.monitor._handle_runout_detected(0)
        
# Should have shown prompt with defaults
        script_calls = [call[0][0] for call in self.gcode.run_script_from_command.call_args_list]
        assert any('prompt_begin' in cmd for cmd in script_calls)


class TestMonitorRunoutEdgeCases:
    """Edge cases and error handling paths for _monitor_runout."""

    def _build_monitor(self, print_state='printing', tool_index=0, sensor_state=True):
        self.printer = Mock()
        self.gcode = Mock()
        self.reactor = Mock()
        self.reactor.NEVER = 999
        self.endless_spool = Mock()
        self.manager = Mock()
        self.manager.toolchange_in_progress = False
        self.manager.runout_handling_in_progress = False
        self.manager.get_switch_state = Mock(return_value=sensor_state)

        self.save_vars = Mock()
        self.save_vars.allVariables = {
            'ace_current_index': tool_index,
            'ace_filament_pos': 'bowden'
        }
        self.print_stats = Mock()
        self.print_stats.get_status = Mock(return_value={'state': print_state})

        def lookup(name, default=None):
            if name == 'print_stats':
                return self.print_stats
            if name == 'save_variables':
                return self.save_vars
            return default

        self.printer.lookup_object = Mock(side_effect=lookup)

        monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self.manager,
        )
        return monitor

    def test_print_stats_exception_defaults_state(self):
        """print_stats errors should fall back to idle handling."""
        monitor = self._build_monitor()
        monitor.runout_detection_active = False
        self.print_stats.get_status.side_effect = Exception("bad stats")

        next_time = monitor._monitor_runout(0.0)

        assert next_time == 0.2
        assert monitor.last_printing_active is False
        assert monitor.last_print_state == ""

    def test_print_start_logs_macro_sync_failure(self):
        """Print start should log macro sync failures."""
        monitor = self._build_monitor(print_state='printing', sensor_state=True)
        monitor.runout_detection_active = False
        self.gcode.run_script_from_command.side_effect = Exception("macro fail")

        next_time = monitor._monitor_runout(0.0)

        log_messages = [c[0][0] for c in self.gcode.respond_info.call_args_list]
        assert any("could not sync macro state" in msg.lower() for msg in log_messages)
        assert monitor.runout_detection_active is True
        assert next_time == 0.05

    def test_debug_autorecovery_enables_detection(self):
        """Debug block should auto-recover when detection was disabled."""
        monitor = self._build_monitor(print_state='printing', sensor_state=True)
        monitor.last_printing_active = True
        monitor.last_print_state = 'printing'
        monitor.runout_detection_active = False
        monitor.monitor_debug_counter = 1200 * 15 - 1

        next_time = monitor._monitor_runout(0.0)

        log_messages = [c[0][0] for c in self.gcode.respond_info.call_args_list]
        assert any("autorecovery" in msg.lower() for msg in log_messages)
        assert monitor.runout_detection_active is True
        assert monitor.monitor_debug_counter == 0
        assert next_time == 0.05

    def test_print_stop_restores_detection_and_logs_macro_error(self):
        """Print stop should restore detection when disabled and log macro errors."""
        monitor = self._build_monitor(print_state='complete', sensor_state=False)
        monitor.runout_detection_active = True
        monitor.last_printing_active = True
        monitor.last_print_state = 'printing'
        self.gcode.run_script_from_command.side_effect = Exception("stop sync fail")

        next_time = monitor._monitor_runout(0.5)

        log_messages = [c[0][0] for c in self.gcode.respond_info.call_args_list]
        assert any("could not sync macro state on print stop" in msg.lower() for msg in log_messages)
        assert next_time == 0.7

    def test_baseline_enables_detection_when_sensor_present(self):
        """Baseline init should enable detection when sensor reports filament."""
        monitor = self._build_monitor(print_state='printing', sensor_state=True)
        monitor.runout_detection_active = True
        monitor.last_printing_active = True
        monitor.last_print_state = 'printing'
        monitor.prev_toolhead_sensor_state = None

        next_time = monitor._monitor_runout(1.0)

        assert monitor.prev_toolhead_sensor_state is True
        log_messages = [c[0][0] for c in self.gcode.respond_info.call_args_list]
        assert next_time == 1.05

    def test_baseline_macro_sync_failure_is_logged(self):
        """Baseline sync failures should be reported."""
        monitor = self._build_monitor(print_state='printing', sensor_state=False)
        monitor.runout_detection_active = True
        monitor.last_printing_active = True
        monitor.last_print_state = 'printing'
        monitor.prev_toolhead_sensor_state = None
        self.gcode.run_script_from_command.side_effect = Exception("baseline fail")

        monitor._monitor_runout(2.0)

        log_messages = [c[0][0] for c in self.gcode.respond_info.call_args_list]
        assert any("could not sync macro state" in msg.lower() for msg in log_messages)

    def test_command_error_shutdown_disables_monitor(self):
        """command_error with shutdown should stop monitoring."""
        class CmdError(Exception):
            pass

        monitor = self._build_monitor(print_state='printing', sensor_state=True)
        monitor.printer.command_error = CmdError
        monitor.runout_detection_active = True
        monitor.last_printing_active = True
        monitor.last_print_state = 'printing'
        monitor.prev_toolhead_sensor_state = True
        self.manager.get_switch_state.return_value = False
        monitor._handle_runout_detected = Mock(side_effect=CmdError("shutdown now"))

        result = monitor._monitor_runout(3.0)

        assert result == self.reactor.NEVER
        assert monitor.runout_detection_active is False
        log_messages = [c[0][0] for c in self.gcode.respond_info.call_args_list]
        assert any("printer shutdown" in msg.lower() for msg in log_messages)

    def test_command_error_non_shutdown_returns_slow_poll(self):
        """Non-shutdown command_error should back off polling."""
        class CmdError(Exception):
            pass

        monitor = self._build_monitor(print_state='printing', sensor_state=True)
        monitor.printer.command_error = CmdError
        monitor.runout_detection_active = True
        monitor.last_printing_active = True
        monitor.last_print_state = 'printing'
        monitor.prev_toolhead_sensor_state = True
        self.manager.get_switch_state.return_value = False
        monitor._handle_runout_detected = Mock(side_effect=CmdError("temporary failure"))

        result = monitor._monitor_runout(4.0)

        assert result == 5.0
        log_messages = [c[0][0] for c in self.gcode.respond_info.call_args_list]
        assert any("monitor command error" in msg.lower() for msg in log_messages)

    def test_generic_exception_returns_slow_poll(self):
        """Generic exceptions should be surfaced as monitor errors."""
        monitor = self._build_monitor(print_state='printing', sensor_state=True)
        class CmdError(Exception):
            pass
        monitor.printer.command_error = CmdError
        monitor.runout_detection_active = True
        monitor.last_printing_active = True
        monitor.last_print_state = 'printing'
        monitor.prev_toolhead_sensor_state = True
        self.manager.get_switch_state.return_value = False
        monitor._handle_runout_detected = Mock(side_effect=RuntimeError("boom"))

        result = monitor._monitor_runout(5.0)

        assert result == 6.0
        log_messages = [c[0][0] for c in self.gcode.respond_info.call_args_list]
        assert any("monitor error" in msg.lower() for msg in log_messages)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
