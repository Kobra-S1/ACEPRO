"""
Test suite for ace.manager module.

Tests AceManager class which orchestrates multiple ACE Pro units:
- Multi-instance management and coordination
- Tool mapping (T0-T3 -> instance 0, T4-T7 -> instance 1)
- Sensor state monitoring and debouncing
- Runout detection and handling
- Global enable/disable state
- Config resolution and per-instance overrides
"""

import unittest
from unittest.mock import Mock, patch, PropertyMock, call
import time

from ace.manager import AceManager, toolchange_in_progress_guard
from ace.config import (
    ACE_INSTANCES,
    INSTANCE_MANAGERS,
    SLOTS_PER_ACE,
    SENSOR_TOOLHEAD,
    SENSOR_RDM,
    FILAMENT_STATE_SPLITTER,
    FILAMENT_STATE_BOWDEN,
    FILAMENT_STATE_TOOLHEAD,
    FILAMENT_STATE_NOZZLE,
    create_inventory,
)


class TestAceManagerInitialization(unittest.TestCase):
    """Test AceManager initialization and configuration."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        
        self.variables = {
            'ace_global_enabled': True,
            'ace_current_index': -1,
            'ace_filament_pos': FILAMENT_STATE_SPLITTER,
        }
        self.mock_save_vars.allVariables = self.variables
        
        # Mock config getters
        self.mock_config.get.side_effect = self._mock_config_get
        self.mock_config.getint.side_effect = self._mock_config_getint
        self.mock_config.getfloat.side_effect = self._mock_config_getfloat
        self.mock_config.getboolean.side_effect = self._mock_config_getboolean

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        elif name == 'output_pin ACE_Pro':
            mock_pin = Mock()
            mock_pin.get_status = Mock(return_value={'value': 1})
            return mock_pin
        return default

    def _mock_config_get(self, key, default=None):
        """Mock config.get()."""
        config_values = {
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
        }
        return config_values.get(key, default)

    def _mock_config_getint(self, key, default=None):
        """Mock config.getint()."""
        config_values = {
            'ace_count': 2,
            'feed_speed': '100',
            'retract_speed': '100',
        }
        val = config_values.get(key, default)
        return int(val) if val is not None else default

    def _mock_config_getfloat(self, key, default=None):
        """Mock config.getfloat()."""
        config_values = {
            'feed_speed': '100.0',
            'retract_speed': '100.0',
            'toolhead_retraction_speed': '300.0',
            'toolhead_retraction_length': '10.0',
            'default_color_change_purge_length': '50.0',
            'default_color_change_purge_speed': '400.0',
            'timeout_multiplier': '2.0',
            'total_max_feeding_length': '1000.0',
            'parkposition_to_toolhead_length': '500.0',
            'toolchange_load_length': '480.0',
            'parkposition_to_rdm_length': '350.0',
            'incremental_feeding_length': '10.0',
            'incremental_feeding_speed': '50.0',
            'extruder_feeding_length': '50.0',
            'extruder_feeding_speed': '5.0',
            'toolhead_slow_loading_speed': '10.0',
            'heartbeat_interval': '1.0',
            'max_dryer_temperature': '70.0',
            'toolhead_full_purge_length': '100.0',
            'feed_assist_active_after_ace_connect': '1',
        }
        val = config_values.get(key, default)
        return float(val) if val is not None else default

    def _mock_config_getboolean(self, key, default=None):
        """Mock config.getboolean()."""
        config_values = {
            'feed_assist_active_after_ace_connect': True,
            'rfid_inventory_sync_enabled': True,
            # Disable Moonraker lane sync during tests to avoid live Moonraker writes
            'moonraker_lane_sync_enabled': False,
        }
        val = config_values.get(key, default)
        return bool(val) if val is not None else (default if default is not None else False)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_manager_creates_multiple_instances(self, mock_endless_spool, mock_ace_instance):
        """Test manager creates correct number of instances."""
        manager = AceManager(self.mock_config)
        
        self.assertEqual(manager.ace_count, 2)
        self.assertEqual(mock_ace_instance.call_count, 2)
        self.assertEqual(len(manager.instances), 2)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_manager_initializes_with_config(self, mock_endless_spool, mock_ace_instance):
        """Test manager loads configuration correctly."""
        manager = AceManager(self.mock_config)
        
        # Just verify that config attributes exist and are set
        self.assertIsNotNone(manager.toolhead_retraction_speed)
        self.assertIsNotNone(manager.toolhead_retraction_length)
        self.assertIsNotNone(manager.default_color_change_purge_length)
        self.assertIsNotNone(manager.default_color_change_purge_speed)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_manager_registers_in_global_dict(self, mock_endless_spool, mock_ace_instance):
        """Test manager registers itself in INSTANCE_MANAGERS."""
        manager = AceManager(self.mock_config)
        
        self.assertIn(0, INSTANCE_MANAGERS)
        self.assertEqual(INSTANCE_MANAGERS[0], manager)


class TestGlobalEnableDisable(unittest.TestCase):
    """Test global ACE Pro enable/disable functionality."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        
        self.variables = {
            'ace_global_enabled': True,
        }
        self.mock_save_vars.allVariables = self.variables
        
        self.mock_config.get.side_effect = self._mock_config_get
        self.mock_config.getint.side_effect = self._mock_config_getint
        self.mock_config.getfloat.side_effect = self._mock_config_getfloat

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        elif name == 'output_pin ACE_Pro':
            mock_pin = Mock()
            mock_pin.get_status = Mock(return_value={'value': 1})
            return mock_pin
        return default

    def _mock_config_get(self, key, default=None):
        config_values = {
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
        }
        return config_values.get(key, default)

    def _mock_config_getint(self, key, default=None):
        config_values = {'ace_count': '1'}
        val = config_values.get(key, default)
        return int(val) if val is not None else default

    def _mock_config_getfloat(self, key, default=None):
        config_values = {
            'toolhead_retraction_speed': '300.0',
            'toolhead_retraction_length': '10.0',
            'default_color_change_purge_length': '50.0',
            'default_color_change_purge_speed': '400.0',
            'timeout_multiplier': '2.0',
            'total_max_feeding_length': '1000.0',
            'parkposition_to_toolhead_length': '500.0',
            'toolchange_load_length': '480.0',
            'parkposition_to_rdm_length': '350.0',
            'incremental_feeding_length': '10.0',
            'incremental_feeding_speed': '50.0',
            'extruder_feeding_length': '50.0',
            'extruder_feeding_speed': '5.0',
            'toolhead_slow_loading_speed': '10.0',
            'heartbeat_interval': '1.0',
            'max_dryer_temperature': '70.0',
            'toolhead_full_purge_length': '100.0',
            'feed_assist_active_after_ace_connect': '1',
        }
        val = config_values.get(key, default)
        return float(val) if val is not None else default

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_get_ace_global_enabled_true(self, mock_endless_spool, mock_ace_instance):
        """Test getting global enabled state when True."""
        manager = AceManager(self.mock_config)
        
        self.assertTrue(manager.get_ace_global_enabled())

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_get_ace_global_enabled_false(self, mock_endless_spool, mock_ace_instance):
        """Test getting global enabled state when False."""
        self.variables['ace_global_enabled'] = False
        manager = AceManager(self.mock_config)
        
        self.assertFalse(manager.get_ace_global_enabled())

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_set_ace_global_enabled(self, mock_endless_spool, mock_ace_instance):
        """Test setting global enabled state."""
        manager = AceManager(self.mock_config)
        manager.state.set_and_save = Mock()
        
        manager.set_ace_global_enabled(False)
        
        manager.state.set_and_save.assert_called_with(
            'ace_global_enabled',
            False
        )
        self.assertFalse(manager._ace_pro_enabled)


class TestHandleReady(unittest.TestCase):
    """Coverage for _handle_ready lifecycle hook."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_toolhead = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {
            "ace_global_enabled": True,
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN,
        }
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "toolhead":
                return self.mock_toolhead
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "baud": 115200}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            val = {"purge_multiplier": "1.0"}.get(key, default)
            return float(val) if val is not None else default

        def get(key, default=None):
            return {
                "filament_runout_sensor_name_rdm": "return_module",
                "filament_runout_sensor_name_nozzle": "toolhead_sensor",
            }.get(key, default)

        def getboolean(key, default=None):
            return {
                "feed_assist_active_after_ace_connect": True,
                "rfid_inventory_sync_enabled": True,
                "ace_connection_supervision": True,
                "moonraker_lane_sync_enabled": False,  # avoid live Moonraker writes in tests
            }.get(key, default if default is not None else False)

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = get
        self.mock_config.getboolean.side_effect = getboolean

    def _instance_factory(self, instance_num, instance_config, printer, ace_enabled):
        inst = Mock()
        inst.instance_num = instance_num
        inst.SLOT_COUNT = SLOTS_PER_ACE
        inst.tool_offset = instance_num * SLOTS_PER_ACE
        inst.serial_mgr = Mock(connect_to_ace=Mock(), disconnect=Mock())
        inst.filament_runout_sensor_name_nozzle = "toolhead_sensor"
        inst.filament_runout_sensor_name_rdm = "return_module"
        return inst

    def _build_manager(self):
        with patch('ace.manager.AceInstance', side_effect=self._instance_factory), \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'):
            return AceManager(self.mock_config, dummy_ace_count=1)

    def test_enabled_path_sets_toolhead_and_connects(self):
        manager = self._build_manager()
        manager._setup_sensors = Mock()
        manager._start_monitoring = Mock()

        manager._handle_ready()

        self.assertIs(manager.instances[0].toolhead, self.mock_toolhead)
        self.mock_gcode.run_script_from_command.assert_any_call("SET_PIN PIN=ACE_Pro VALUE=1.0")
        manager.instances[0].serial_mgr.connect_to_ace.assert_called_once_with(115200, 2)
        manager._setup_sensors.assert_called_once()
        manager._start_monitoring.assert_called_once()

    def test_disabled_path_skips_connections(self):
        self.variables["ace_global_enabled"] = False
        manager = self._build_manager()
        manager._setup_sensors = Mock()
        manager._start_monitoring = Mock()

        manager._handle_ready()

        self.mock_gcode.run_script_from_command.assert_any_call("SET_PIN PIN=ACE_Pro VALUE=0.0")
        manager.instances[0].serial_mgr.connect_to_ace.assert_not_called()
        manager._setup_sensors.assert_not_called()
        manager._start_monitoring.assert_called_once()


class TestPrepareToolheadForFilamentRetraction(unittest.TestCase):
    """Coverage for prepare_toolhead_for_filament_retraction."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {
            "ace_global_enabled": True,
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN,
        }
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {
                "ace_count": 1,
                "baud": 115200,
                "pre_cut_retract_length": 2,
            }
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            val = {"purge_multiplier": "1.0"}.get(key, default)
            return float(val) if val is not None else default

        def get(key, default=None):
            return {
                "filament_runout_sensor_name_rdm": "return_module",
                "filament_runout_sensor_name_nozzle": "toolhead_sensor",
            }.get(key, default)

        def getboolean(key, default=None):
            return {
                "feed_assist_active_after_ace_connect": True,
                "rfid_inventory_sync_enabled": True,
                "ace_connection_supervision": True,
                "moonraker_lane_sync_enabled": False,  # avoid live Moonraker writes in tests
            }.get(key, default if default is not None else False)

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = get
        self.mock_config.getboolean.side_effect = getboolean

    def _instance_factory(self, instance_num, instance_config, printer, ace_enabled):
        inst = Mock()
        inst.instance_num = instance_num
        inst.SLOT_COUNT = SLOTS_PER_ACE
        inst.tool_offset = instance_num * SLOTS_PER_ACE
        inst.inventory = [{"status": "ready"} for _ in range(SLOTS_PER_ACE)]
        return inst

    def _build_manager(self):
        with patch('ace.manager.AceInstance', side_effect=self._instance_factory), \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'):
            return AceManager(self.mock_config, dummy_ace_count=1)

    def test_skips_when_no_filament(self):
        manager = self._build_manager()
        manager.get_switch_state = Mock(return_value=False)

        result = manager.prepare_toolhead_for_filament_retraction()

        self.assertFalse(result)
        manager.get_switch_state.assert_called_once_with(SENSOR_TOOLHEAD)
        self.mock_gcode.respond_info.assert_any_call("ACE: No filament at toolhead, skipping prep")

    def test_runs_macro_with_default_temp(self):
        manager = self._build_manager()
        manager.get_switch_state = Mock(return_value=True)

        result = manager.prepare_toolhead_for_filament_retraction(tool_index=-1)

        self.assertTrue(result)
        self.mock_gcode.run_script_from_command.assert_called_once_with(
            "_ACE_PREPARE_FOR_RETRACTION TARGET_TEMP=0 PRE_CUT_RETRACT=2.0"
        )

    def test_uses_inventory_temp_when_available(self):
        manager = self._build_manager()
        manager.get_switch_state = Mock(return_value=True)
        manager.instances[0].inventory[1]["temp"] = 210

        manager.prepare_toolhead_for_filament_retraction(tool_index=1)

        self.mock_gcode.run_script_from_command.assert_called_once_with(
            "_ACE_PREPARE_FOR_RETRACTION TARGET_TEMP=210 PRE_CUT_RETRACT=2.0"
        )

    def test_handles_macro_failure(self):
        manager = self._build_manager()
        manager.get_switch_state = Mock(return_value=True)
        self.mock_gcode.run_script_from_command.side_effect = Exception("boom")

        result = manager.prepare_toolhead_for_filament_retraction(tool_index=0)

        self.assertFalse(result)


class TestSensorMonitoring(unittest.TestCase):
    """Test sensor state monitoring and debouncing."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        
        self.variables = {'ace_global_enabled': True}
        self.mock_save_vars.allVariables = self.variables
        
        self.mock_config.get.side_effect = self._mock_config_get
        self.mock_config.getint.side_effect = self._mock_config_getint
        self.mock_config.getfloat.side_effect = self._mock_config_getfloat
        
        # Mock sensors
        self.sensor_states = {
            SENSOR_TOOLHEAD: False,
            SENSOR_RDM: False,
        }

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        elif name == 'output_pin ACE_Pro':
            mock_pin = Mock()
            mock_pin.get_status = Mock(return_value={'value': 1})
            return mock_pin
        elif name.startswith('filament_switch_sensor'):
            sensor = Mock()
            sensor_name = name.split(' ', 1)[1] if ' ' in name else name
            sensor_type = SENSOR_TOOLHEAD if 'toolhead' in sensor_name else SENSOR_RDM
            
            runout_helper = Mock()
            runout_helper.sensor_enabled = True
            # Use a lambda to defer evaluation of sensor state
            type(runout_helper).filament_present = PropertyMock(
                side_effect=lambda st=sensor_type: self.sensor_states.get(st, False)
            )
            sensor.runout_helper = runout_helper
            return sensor
        return default

    def _mock_config_get(self, key, default=None):
        config_values = {
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
        }
        return config_values.get(key, default)

    def _mock_config_getint(self, key, default=None):
        config_values = {'ace_count': '1'}
        val = config_values.get(key, default)
        return int(val) if val is not None else default

    def _mock_config_getfloat(self, key, default=None):
        config_values = {
            'toolhead_retraction_speed': '300.0',
            'toolhead_retraction_length': '10.0',
            'default_color_change_purge_length': '50.0',
            'default_color_change_purge_speed': '400.0',
            'timeout_multiplier': '2.0',
            'total_max_feeding_length': '1000.0',
            'parkposition_to_toolhead_length': '500.0',
            'toolchange_load_length': '480.0',
            'parkposition_to_rdm_length': '350.0',
            'incremental_feeding_length': '10.0',
            'incremental_feeding_speed': '50.0',
            'extruder_feeding_length': '50.0',
            'extruder_feeding_speed': '5.0',
            'toolhead_slow_loading_speed': '10.0',
            'heartbeat_interval': '1.0',
            'max_dryer_temperature': '70.0',
            'toolhead_full_purge_length': '100.0',
            'feed_assist_active_after_ace_connect': '1',
        }
        val = config_values.get(key, default)
        return float(val) if val is not None else default

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_get_switch_state_toolhead_clear(self, mock_endless_spool, mock_ace_instance):
        """Test reading toolhead sensor state when clear."""
        manager = AceManager(self.mock_config)
        self.sensor_states[SENSOR_TOOLHEAD] = False
        
        state = manager.get_switch_state(SENSOR_TOOLHEAD)
        
        self.assertFalse(state)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_get_switch_state_toolhead_triggered(self, mock_endless_spool, mock_ace_instance):
        """Test reading toolhead sensor state when triggered."""
        manager = AceManager(self.mock_config)
        manager._sensor_override = {SENSOR_TOOLHEAD: True}
        
        state = manager.get_switch_state(SENSOR_TOOLHEAD)
        
        self.assertTrue(state)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_is_filament_path_free_both_clear(self, mock_endless_spool, mock_ace_instance):
        """Test path free check when both sensors clear."""
        manager = AceManager(self.mock_config)
        self.sensor_states[SENSOR_TOOLHEAD] = False
        self.sensor_states[SENSOR_RDM] = False
        
        self.assertTrue(manager.is_filament_path_free())

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_is_filament_path_free_toolhead_triggered(self, mock_endless_spool, mock_ace_instance):
        """Test path free check when toolhead sensor triggered."""
        manager = AceManager(self.mock_config)
        manager._sensor_override = {SENSOR_TOOLHEAD: True, SENSOR_RDM: False}
        
        self.assertFalse(manager.is_filament_path_free())


class TestToolchangeGuardDecorator(unittest.TestCase):
    """Test toolchange_in_progress_guard decorator."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_self = Mock()
        self.mock_self.toolchange_in_progress = False
        self.mock_self._toolchange_depth = 0
        self.mock_self.gcode = Mock()

    def test_decorator_sets_flag(self):
        """Test decorator sets toolchange_in_progress flag and increments depth."""
        @toolchange_in_progress_guard
        def test_method(obj):
            assert obj.toolchange_in_progress is True
            assert obj._toolchange_depth == 1
        
        test_method(self.mock_self)
        
        # After method exits, flag should be reset and depth back to 0
        self.assertFalse(self.mock_self.toolchange_in_progress)
        self.assertEqual(self.mock_self._toolchange_depth, 0)

    def test_decorator_resets_flag_on_success(self):
        """Test decorator resets flag after successful execution."""
        @toolchange_in_progress_guard
        def test_method(self):
            pass
        
        test_method(self.mock_self)
        
        self.assertFalse(self.mock_self.toolchange_in_progress)
        self.assertEqual(self.mock_self._toolchange_depth, 0)

    def test_decorator_resets_flag_on_exception(self):
        """Test decorator resets flag even on exception."""
        @toolchange_in_progress_guard
        def test_method(self):
            raise ValueError("Test error")
        
        with self.assertRaises(ValueError):
            test_method(self.mock_self)
        
        self.assertFalse(self.mock_self.toolchange_in_progress)
        self.assertEqual(self.mock_self._toolchange_depth, 0)


class TestConfigResolution(unittest.TestCase):
    """Test per-instance configuration resolution."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        
        self.variables = {'ace_global_enabled': True}
        self.mock_save_vars.allVariables = self.variables
        
        self.mock_config.get.side_effect = self._mock_config_get
        self.mock_config.getint.side_effect = self._mock_config_getint
        self.mock_config.getfloat.side_effect = self._mock_config_getfloat

    def _mock_lookup_object(self, name, default=None):
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        elif name == 'output_pin ACE_Pro':
            mock_pin = Mock()
            mock_pin.get_status = Mock(return_value={'value': 1})
            return mock_pin
        return default

    def _mock_config_get(self, key, default=None):
        config_values = {
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
        }
        return config_values.get(key, default)

    def _mock_config_getint(self, key, default=None):
        config_values = {'ace_count': '2'}
        val = config_values.get(key, default)
        return int(val) if val is not None else default

    def _mock_config_getfloat(self, key, default=None):
        # Instance 0 gets 100, instance 1 gets 200
        config_values = {
            'toolhead_retraction_speed': '300.0',
            'toolhead_retraction_length': '10.0',
            'default_color_change_purge_length': '50.0',
            'default_color_change_purge_speed': '400.0',
            'timeout_multiplier': '2.0',
            'total_max_feeding_length': '1000.0',
            'parkposition_to_toolhead_length': '500.0',
            'toolchange_load_length': '480.0',
            'parkposition_to_rdm_length': '350.0',
            'incremental_feeding_length': '10.0',
            'incremental_feeding_speed': '50.0',
            'extruder_feeding_length': '50.0',
            'extruder_feeding_speed': '5.0',
            'toolhead_slow_loading_speed': '10.0',
            'heartbeat_interval': '1.0',
            'max_dryer_temperature': '70.0',
            'toolhead_full_purge_length': '100.0',
            'feed_speed': '100,1:200',  # Override for instance 1
            'retract_speed': '100',
            'feed_assist_active_after_ace_connect': '1',
        }
        val = config_values.get(key, default)
        return val if val is not None else default

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_resolve_instance_config_with_override(self, mock_endless_spool, mock_ace_instance):
        """Test config resolution with per-instance override."""
        manager = AceManager(self.mock_config)
        
        # Check that _resolve_instance_config was called for each instance
        # This would resolve the "100,1:200" override for feed_speed
        self.assertEqual(manager.ace_count, 2)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_resolve_instance_config_overridable_params(self, mock_endless_spool, mock_ace_instance):
        """Test that overridable params are resolved per instance."""
        manager = AceManager(self.mock_config)
        
        # Test that feed_speed from _mock_config_get is "60" (default from _read_ace_config)
        # but the test config actually returns the floats directly, so check actual config
        resolved_inst0 = manager._resolve_instance_config(0)
        resolved_inst1 = manager._resolve_instance_config(1)
        
        # Check that feed_speed and retract_speed are resolved to numbers
        # The actual values come from the config, which may have defaults
        self.assertIsInstance(resolved_inst0['feed_speed'], (int, float))
        self.assertIsInstance(resolved_inst1['feed_speed'], (int, float))
        self.assertIsInstance(resolved_inst0['retract_speed'], (int, float))
        self.assertIsInstance(resolved_inst1['retract_speed'], (int, float))
        
        # Non-overridable params should be same
        self.assertEqual(resolved_inst0['baud'], resolved_inst1['baud'])

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_resolve_instance_config_singleton_params(self, mock_endless_spool, mock_ace_instance):
        """Test that singleton params are NOT resolved per instance."""
        manager = AceManager(self.mock_config)
        
        resolved_inst0 = manager._resolve_instance_config(0)
        resolved_inst1 = manager._resolve_instance_config(1)
        
        # Singleton params should be identical (not instance-specific)
        # Note: baud is stored as int, ace_count as int, others might be strings or numbers
        singleton_params = ['baud', 'ace_count', 'parkposition_to_toolhead_length']
        for param in singleton_params:
            if param in resolved_inst0 and param in resolved_inst1:
                self.assertEqual(resolved_inst0[param], resolved_inst1[param],
                               f"Singleton param {param} should be identical for all instances")

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_resolve_instance_config_all_overridable_params(self, mock_endless_spool, mock_ace_instance):
        """Test resolution of all OVERRIDABLE_PARAMS."""
        from ace.config import OVERRIDABLE_PARAMS
        
        # Create config with overrides for all overridable params
        test_config = Mock()
        test_config.get_printer.return_value = self.mock_printer
        
        def mock_getint_with_overrides(key, default=None):
            overrides = {
                'ace_count': 2,
            }
            return overrides.get(key, default)
        
        def mock_get_with_overrides(key, default=None):
            # Set different values for each overridable param
            overrides = {
                'serial': '/dev/ttyACM0,/dev/ttyACM1',
                'baud': '115200',
                'feed_speed': '60,1:80',  # Instance 1 faster
                'retract_speed': '50,1:45',  # Instance 1 slower
                'total_max_feeding_length': '1000,1:1200',
                'toolchange_load_length': '500,1:550',
                'incremental_feeding_length': '100,1:120',
                'incremental_feeding_speed': '300,1:350',
                'heartbeat_interval': '1.0,1:0.5',  # Instance 1 more frequent
                'max_dryer_temperature': '70,1:60',  # Instance 1 lower temp
                'toolchange_purge_length': '50.0',
                'toolchange_purge_speed': '400.0',
                'extruder_feeding_speed': '5.0',
                'toolhead_slow_loading_speed': '10.0',
                'toolhead_full_purge_length': '100.0',
                'feed_assist_active_after_ace_connect': '1',
            }
            return overrides.get(key, default)
        
        test_config.getint = Mock(side_effect=mock_getint_with_overrides)
        test_config.get = Mock(side_effect=mock_get_with_overrides)
        test_config.getfloat = Mock(side_effect=lambda k, d=None: float(mock_get_with_overrides(k, d)))
        test_config.getboolean = Mock(side_effect=lambda k, d=False: bool(int(mock_get_with_overrides(k, str(int(d))))))
        
        manager = AceManager(test_config)
        
        resolved_inst0 = manager._resolve_instance_config(0)
        resolved_inst1 = manager._resolve_instance_config(1)
        
        # Verify all overridable params were resolved
        expected_inst0 = {
            'feed_speed': 60,
            'retract_speed': 50,
            'total_max_feeding_length': 1000,
            'toolchange_load_length': 500,
            'incremental_feeding_length': 100,
            'incremental_feeding_speed': 300,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
        }
        
        expected_inst1 = {
            'feed_speed': 80,
            'retract_speed': 45,
            'total_max_feeding_length': 1200,
            'toolchange_load_length': 550,
            'incremental_feeding_length': 120,
            'incremental_feeding_speed': 350,
            'heartbeat_interval': 0.5,
            'max_dryer_temperature': 60,
        }
        
        for param, expected_val in expected_inst0.items():
            self.assertEqual(resolved_inst0[param], expected_val, 
                           f"Instance 0 {param} should be {expected_val}")
        
        for param, expected_val in expected_inst1.items():
            self.assertEqual(resolved_inst1[param], expected_val,
                           f"Instance 1 {param} should be {expected_val}")


class TestRunoutDetection(unittest.TestCase):
    """Test runout detection functionality."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        
        self.variables = {
            'ace_global_enabled': True,
            'ace_current_index': 0,
        }
        self.mock_save_vars.allVariables = self.variables
        
        self.mock_config.get.side_effect = self._mock_config_get
        self.mock_config.getint.side_effect = self._mock_config_getint
        self.mock_config.getfloat.return_value = 1.0

    def _mock_lookup_object(self, name, default=None):
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        elif name == 'output_pin ACE_Pro':
            mock_pin = Mock()
            mock_pin.get_status = Mock(return_value={'value': 1})
            return mock_pin
        return default

    def _mock_config_get(self, key, default=None):
        return default

    def _mock_config_getint(self, key, default=None):
        return 1 if key == 'ace_count' else default

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_set_runout_detection_active(self, mock_endless_spool, mock_ace_instance):
        """Test enabling/disabling runout detection."""
        manager = AceManager(self.mock_config)
        
        manager.set_runout_detection_active(True)
        self.assertTrue(manager.runout_monitor.runout_detection_active)
        
        manager.set_runout_detection_active(False)
        self.assertFalse(manager.runout_monitor.runout_detection_active)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_has_rdm_sensor_true(self, mock_endless_spool, mock_ace_instance):
        """Test has_rdm_sensor returns True when sensor exists."""
        manager = AceManager(self.mock_config)
        manager.sensors[SENSOR_RDM] = Mock()
        
        self.assertTrue(manager.has_rdm_sensor())

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_has_rdm_sensor_false(self, mock_endless_spool, mock_ace_instance):
        """Test has_rdm_sensor returns False when sensor missing."""
        manager = AceManager(self.mock_config)
        manager.sensors = {}
        
        self.assertFalse(manager.has_rdm_sensor())


class TestAceEnableState(unittest.TestCase):
    """Test ACE enable state management."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        
        self.variables = {'ace_global_enabled': True}
        self.mock_save_vars.allVariables = self.variables
        
        # Mock config getters
        self.mock_config.get.side_effect = self._mock_config_get
        self.mock_config.getint.side_effect = self._mock_config_getint
        self.mock_config.getfloat.side_effect = self._mock_config_getfloat

    def _mock_config_get(self, key, default=None):
        """Mock config.get()."""
        config_values = {
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
        }
        return config_values.get(key, default)

    def _mock_config_getint(self, key, default=None):
        """Mock config.getint()."""
        config_values = {
            'ace_count': 2,
            'feed_speed': '100',
            'retract_speed': '100',
        }
        val = config_values.get(key, default)
        return int(val) if val is not None else default

    def _mock_config_getfloat(self, key, default=None):
        """Mock config.getfloat()."""
        config_values = {
            'feed_speed': '100.0',
            'retract_speed': '100.0',
            'toolhead_retraction_speed': '300.0',
            'toolhead_retraction_length': '10.0',
            'default_color_change_purge_length': '50.0',
            'default_color_change_purge_speed': '400.0',
            'timeout_multiplier': '2.0',
            'total_max_feeding_length': '1000.0',
            'parkposition_to_toolhead_length': '500.0',
            'toolchange_load_length': '480.0',
            'parkposition_to_rdm_length': '350.0',
            'incremental_feeding_length': '10.0',
            'incremental_feeding_speed': '50.0',
            'extruder_feeding_length': '50.0',
            'extruder_feeding_speed': '5.0',
            'toolhead_slow_loading_speed': '10.0',
            'heartbeat_interval': '1.0',
            'max_dryer_temperature': '70.0',
            'toolhead_full_purge_length': '100.0',
            'feed_assist_active_after_ace_connect': '1',
        }
        val = config_values.get(key, default)
        return float(val) if val is not None else default

    def _mock_lookup_object(self, name, default=None):
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        elif name == 'output_pin ACE_Pro':
            mock_pin = Mock()
            mock_pin.get_status = Mock(return_value={'value': 1})
            return mock_pin
        return default

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_is_ace_enabled_true(self, mock_endless_spool, mock_ace_instance):
        """Test is_ace_enabled returns True when enabled."""
        manager = AceManager(self.mock_config)
        manager._ace_pro_enabled = True
        
        self.assertTrue(manager.is_ace_enabled())

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_is_ace_enabled_false(self, mock_endless_spool, mock_ace_instance):
        """Test is_ace_enabled returns False when disabled."""
        # Mock output pin to return False
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
            'output_pin ACE_Pro': Mock(get_status=Mock(return_value={'value': 0})),
        }.get(name, default)
        
        manager = AceManager(self.mock_config)
        
        self.assertFalse(manager.is_ace_enabled())

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_update_ace_support_active_state(self, mock_endless_spool, mock_ace_instance):
        """Test updating ACE support active state."""
        manager = AceManager(self.mock_config)
        
        manager.update_ace_support_active_state()
        
        # Verify method executes without error
        self.assertIsNotNone(manager)


class TestInventorySync(unittest.TestCase):
    """Test inventory synchronization."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        
        self.variables = {'ace_global_enabled': True}
        self.mock_save_vars.allVariables = self.variables
        
        # Mock config getters
        self.mock_config.get.side_effect = self._mock_config_get
        self.mock_config.getint.side_effect = self._mock_config_getint
        self.mock_config.getfloat.side_effect = self._mock_config_getfloat

    def _mock_config_get(self, key, default=None):
        """Mock config.get()."""
        config_values = {
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
        }
        return config_values.get(key, default)

    def _mock_config_getint(self, key, default=None):
        """Mock config.getint()."""
        config_values = {
            'ace_count': 2,
            'feed_speed': '100',
            'retract_speed': '100',
        }
        val = config_values.get(key, default)
        return int(val) if val is not None else default

    def _mock_config_getfloat(self, key, default=None):
        """Mock config.getfloat()."""
        config_values = {
            'feed_speed': '100.0',
            'retract_speed': '100.0',
            'toolhead_retraction_speed': '300.0',
            'toolhead_retraction_length': '10.0',
            'default_color_change_purge_length': '50.0',
            'default_color_change_purge_speed': '400.0',
            'timeout_multiplier': '2.0',
            'total_max_feeding_length': '1000.0',
            'parkposition_to_toolhead_length': '500.0',
            'toolchange_load_length': '480.0',
            'parkposition_to_rdm_length': '350.0',
            'incremental_feeding_length': '10.0',
            'incremental_feeding_speed': '50.0',
            'extruder_feeding_length': '50.0',
            'extruder_feeding_speed': '5.0',
            'toolhead_slow_loading_speed': '10.0',
            'heartbeat_interval': '1.0',
            'max_dryer_temperature': '70.0',
            'toolhead_full_purge_length': '100.0',
            'feed_assist_active_after_ace_connect': '1',
        }
        val = config_values.get(key, default)
        return float(val) if val is not None else default

    def _mock_lookup_object(self, name, default=None):
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        elif name == 'output_pin ACE_Pro':
            mock_pin = Mock()
            mock_pin.get_status = Mock(return_value={'value': 1})
            return mock_pin
        return default

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_sync_inventory_to_persistent(self, mock_endless_spool, mock_ace_instance):
        """Test syncing inventory to persistent storage."""
        manager = AceManager(self.mock_config)
        test_inventory = {0: {'color': 'FFFFFF', 'material': 'PLA'}}
        manager.instances[0].inventory = test_inventory
        
        manager._sync_inventory_to_persistent(0)
        
        # Verify inventory was written to variables
        self.assertEqual(self.variables['ace_inventory_0'], test_inventory)


class TestGetStatus(unittest.TestCase):
    """Test status reporting."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        
        self.variables = {
            'ace_global_enabled': True,
            'ace_current_index': 1,
        }
        self.mock_save_vars.allVariables = self.variables
        
        # Mock config getters
        self.mock_config.get.side_effect = self._mock_config_get
        self.mock_config.getint.side_effect = self._mock_config_getint
        self.mock_config.getfloat.side_effect = self._mock_config_getfloat

    def _mock_config_get(self, key, default=None):
        """Mock config.get()."""
        config_values = {
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
        }
        return config_values.get(key, default)

    def _mock_config_getint(self, key, default=None):
        """Mock config.getint()."""
        config_values = {
            'ace_count': 2,
            'feed_speed': '100',
            'retract_speed': '100',
        }
        val = config_values.get(key, default)
        return int(val) if val is not None else default

    def _mock_config_getfloat(self, key, default=None):
        """Mock config.getfloat()."""
        config_values = {
            'feed_speed': '100.0',
            'retract_speed': '100.0',
            'toolhead_retraction_speed': '300.0',
            'toolhead_retraction_length': '10.0',
            'default_color_change_purge_length': '50.0',
            'default_color_change_purge_speed': '400.0',
            'timeout_multiplier': '2.0',
            'total_max_feeding_length': '1000.0',
            'parkposition_to_toolhead_length': '500.0',
            'toolchange_load_length': '480.0',
            'parkposition_to_rdm_length': '350.0',
            'incremental_feeding_length': '10.0',
            'incremental_feeding_speed': '50.0',
            'extruder_feeding_length': '50.0',
            'extruder_feeding_speed': '5.0',
            'toolhead_slow_loading_speed': '10.0',
            'heartbeat_interval': '1.0',
            'max_dryer_temperature': '70.0',
            'toolhead_full_purge_length': '100.0',
            'feed_assist_active_after_ace_connect': '1',
        }
        val = config_values.get(key, default)
        return float(val) if val is not None else default

    def _mock_lookup_object(self, name, default=None):
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        elif name == 'output_pin ACE_Pro':
            mock_pin = Mock()
            mock_pin.get_status = Mock(return_value={'value': 1})
            return mock_pin
        return default

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_get_status_returns_dict(self, mock_endless_spool, mock_ace_instance):
        """Test get_status returns status dictionary."""
        manager = AceManager(self.mock_config)
        
        status = manager.get_status()
        
        self.assertIsInstance(status, dict)
        self.assertIn('ace_instances', status)
        self.assertEqual(status['ace_instances'], 2)


class TestPerformToolChange(unittest.TestCase):
    """Test perform_tool_change functionality with comprehensive branch coverage."""

    def setUp(self):
        """Set up test fixtures with extensive mocking."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_gcode_move = Mock()
        
        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        
        self.variables = {
            'ace_global_enabled': True,
            'ace_current_index': -1,
            'ace_filament_pos': FILAMENT_STATE_SPLITTER,
        }
        self.mock_save_vars.allVariables = self.variables
        
        self.mock_config.get.side_effect = self._mock_config_get
        self.mock_config.getint.side_effect = self._mock_config_getint
        self.mock_config.getfloat.side_effect = self._mock_config_getfloat

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        elif name == 'gcode_move':
            return self.mock_gcode_move
        elif name == 'output_pin ACE_Pro':
            mock_pin = Mock()
            mock_pin.get_status = Mock(return_value={'value': 1})
            return mock_pin
        return default

    def _mock_config_get(self, key, default=None):
        config_values = {
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
        }
        return config_values.get(key, default)

    def _mock_config_getint(self, key, default=None):
        config_values = {'ace_count': '2'}
        val = config_values.get(key, default)
        return int(val) if val is not None else default

    def _mock_config_getfloat(self, key, default=None):
        config_values = {
            'toolhead_retraction_speed': '300.0',
            'toolhead_retraction_length': '10.0',
            'default_color_change_purge_length': '50.0',
            'default_color_change_purge_speed': '400.0',
            'timeout_multiplier': '2.0',
            'total_max_feeding_length': '1000.0',
            'parkposition_to_toolhead_length': '500.0',
            'toolchange_load_length': '480.0',
            'parkposition_to_rdm_length': '350.0',
            'incremental_feeding_length': '10.0',
            'incremental_feeding_speed': '50.0',
            'extruder_feeding_length': '50.0',
            'extruder_feeding_speed': '5.0',
            'toolhead_slow_loading_speed': '10.0',
            'heartbeat_interval': '1.0',
            'max_dryer_temperature': '70.0',
            'toolhead_full_purge_length': '100.0',
            'feed_assist_active_after_ace_connect': '1',
        }
        val = config_values.get(key, default)
        return float(val) if val is not None else default

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    @patch('ace.manager.get_ace_instance_and_slot_for_tool')
    def test_tool_reselection_already_loaded(self, mock_get_ace, mock_endless_spool, mock_ace_instance):
        """Test reselecting same tool when already loaded at nozzle."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_NOZZLE
        manager._sensor_override = {SENSOR_TOOLHEAD: True, SENSOR_RDM: False}
        
        # Mock get_ace_instance_and_slot_for_tool for target_temp lookup
        mock_instance = Mock()
        mock_instance.inventory = {1: {'status': 'loaded', 'temp': 0}}
        mock_instance._enable_feed_assist = Mock()
        mock_get_ace.return_value = (mock_instance, 1)
        
        # Set up manager.instances to contain the mock_instance at index 0 (tool 1 maps to instance 0, slot 1)
        manager.instances[0] = mock_instance
        
        # Reselect same tool
        result = manager.perform_tool_change(current_tool=1, target_tool=1)
        
        self.assertIn("already loaded", result)
        # CRITICAL: Feed assist MUST be re-enabled even for reselection (e.g., after ACE power cycle)
        mock_instance._enable_feed_assist.assert_called_once_with(1)
        # Should return early without calling load/unload or macros
        # Verify no POST_TOOLCHANGE was called (early return bypasses it)
        post_calls = [call for call in self.mock_gcode.run_script_from_command.call_args_list 
                     if '_ACE_POST_TOOLCHANGE' in str(call)]
        self.assertEqual(len(post_calls), 0, "Should not call POST_TOOLCHANGE for reselection")

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    @patch('ace.manager.get_ace_instance_and_slot_for_tool')
    def test_tool_reselection_state_correction(self, mock_get_ace, mock_endless_spool, mock_ace_instance):
        """Test reselecting same tool when state is nozzle but sensor is empty."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_NOZZLE
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        
        # Mock instance for load operation
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {1: {'status': 'loaded', 'temp': 0}}  # Fixed: key should be 1
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        mock_get_ace.return_value = (mock_instance, 1)
        manager.check_and_wait_for_spool_ready = Mock(return_value=True)
        
        result = manager.perform_tool_change(current_tool=1, target_tool=1)
        
        # Should have corrected state and performed load
        self.assertIn("1", result)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_plausibility_check_sensor_mismatch(self, mock_endless_spool, mock_ace_instance):
        """Test plausibility check detects sensor/state mismatch."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_BOWDEN
        manager._sensor_override = {SENSOR_TOOLHEAD: True, SENSOR_RDM: False}
        manager.smart_unload = Mock(return_value=True)
        
        # Mock instance for subsequent load
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {1: {'status': 'loaded', 'temp': 0}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 1)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            result = manager.perform_tool_change(current_tool=-1, target_tool=1)
            
            # Should have called smart_unload to clear path
            manager.smart_unload.assert_called_once()
#TODO: SPLITTER
    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_filament_pos_splitter_does_unload_before_target_tool_load(self, mock_endless_spool, mock_ace_instance):
        """Test plausibility check detects sensor/state mismatch."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_SPLITTER
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: True}
        manager.smart_unload = Mock(return_value=True)
        
        # Mock instance for subsequent load
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {1: {'status': 'loaded', 'temp': 0}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.has_rdm_sensor = Mock(return_value=True)

        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 1)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            result = manager.perform_tool_change(current_tool=-1, target_tool=1)
            
            # Should have called smart_unload to clear path
            manager.smart_unload.assert_called_once()
            self.assertIn('Tool -1 → 1 (ACE[0])', result)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_filament_pos_splitter_does_unload_with_unload_failure_throws_exception(self, mock_endless_spool, mock_ace_instance):
        """Test plausibility check detects sensor/state mismatch."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_SPLITTER
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: True}
        manager.smart_unload = Mock(return_value=False)
        
        # Mock instance for subsequent load
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {1: {'status': 'loaded', 'temp': 0}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.has_rdm_sensor = Mock(return_value=True)

        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 1)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)

            with self.assertRaises(Exception) as context:
                manager.perform_tool_change(current_tool=-1, target_tool=1)
            
            self.assertIn("Failed to clear RMS filament path", str(context.exception))
            # Should have called smart_unload to clear path
            manager.smart_unload.assert_called_once()

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_unload_current_tool_at_nozzle(self, mock_endless_spool, mock_ace_instance):
        """Test unloading current tool when at nozzle position."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_NOZZLE
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        manager.smart_unload = Mock(return_value=True)
        
        # Mock instance for load
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {2: {'status': 'loaded', 'temp': 0}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 2)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            result = manager.perform_tool_change(current_tool=1, target_tool=2)
            
            # Should have called smart_unload for current tool
            manager.smart_unload.assert_called_with(tool_index=1)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_skip_unload_at_bowden(self, mock_endless_spool, mock_ace_instance):
        """Test skipping unload when filament already at bowden."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_BOWDEN
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        manager.smart_unload = Mock(return_value=True)
        
        # Mock instance for load
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {2: {'status': 'loaded', 'temp': 220}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 2)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            result = manager.perform_tool_change(current_tool=1, target_tool=2)
            
            # Should NOT have called smart_unload (already at splitter)
            manager.smart_unload.assert_not_called()
            # Should have read target temp from inventory
            self.mock_gcode.run_script_from_command.assert_any_call(
                "_ACE_PRE_TOOLCHANGE FROM=1 TO=2 TARGET_TEMP=220"
            )
            
            self.assertIn("Tool 1 → 2 (ACE[0])", result)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_endless_spool_mode(self, mock_endless_spool, mock_ace_instance):
        """Test endless spool mode skips unload of empty current tool."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_NOZZLE
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        manager.smart_unload = Mock(return_value=True)
        
        # Mock instance for load
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {3: {'status': 'loaded', 'temp': 0}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 3)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            result = manager.perform_tool_change(current_tool=1, target_tool=3, is_endless_spool=True)
            
            # Should NOT have called smart_unload (endless spool mode)
            manager.smart_unload.assert_not_called()
            # Should have set filament_pos to bowden (unloaded state for empty tool)
            self.assertEqual(self.variables['ace_filament_pos'], FILAMENT_STATE_BOWDEN)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_unload_only_no_target(self, mock_endless_spool, mock_ace_instance):
        """Test unloading without loading new tool (target=-1)."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_NOZZLE
        self.variables['ace_current_index'] = 1
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        manager.smart_unload = Mock(return_value=True)
        
        result = manager.perform_tool_change(current_tool=1, target_tool=-1)
        
        # Should have unloaded but not loaded
        manager.smart_unload.assert_called_once_with(tool_index=1)
        self.assertEqual(self.variables['ace_current_index'], -1)
        self.assertIn("Unloaded", result)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_load_new_tool_from_empty(self, mock_endless_spool, mock_ace_instance):
        """Test loading new tool when no current tool loaded."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_SPLITTER
        self.variables['ace_current_index'] = -1
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        
        # Mock instance for load
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {2: {'status': 'loaded', 'temp': 0}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=7.5)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 2)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            result = manager.perform_tool_change(current_tool=-1, target_tool=2)
            
            # Should have loaded new tool
            mock_instance._feed_filament_into_toolhead.assert_called_once()
            mock_instance._enable_feed_assist.assert_called_once_with(2)
            self.assertEqual(self.variables['ace_current_index'], 2)
            # Should have passed purged amount to POST macro (note: values are floats with .0)
            self.mock_gcode.run_script_from_command.assert_any_call(
                "_ACE_POST_TOOLCHANGE FROM=-1 TO=2 PURGELENGTH=50.0 PURGESPEED=400.0 TARGET_TEMP=0 PURGED_AMOUNT=7.5 PURGE_MAX_CHUNK_LENGTH=300.0"
            )

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_endless_spool_increased_purge(self, mock_endless_spool, mock_ace_instance):
        """Test endless spool increases purge length by 1.5x."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_SPLITTER
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        manager.toolchange_purge_length = 50
        
        # Mock instance for load
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {3: {'status': 'loaded', 'temp': 0}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 3)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            result = manager.perform_tool_change(current_tool=1, target_tool=3, is_endless_spool=True)
            
            # Should use 1.5x purge length (50 * 1.5 = 75, note: values are floats with .0)
            self.mock_gcode.run_script_from_command.assert_any_call(
                "_ACE_POST_TOOLCHANGE FROM=1 TO=3 PURGELENGTH=75.0 PURGESPEED=400.0 TARGET_TEMP=0 PURGED_AMOUNT=5.0 PURGE_MAX_CHUNK_LENGTH=300.0"
            )

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_spool_not_ready_raises_error(self, mock_endless_spool, mock_ace_instance):
        """Test error when target spool not ready."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_SPLITTER
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        manager.check_and_wait_for_spool_ready = Mock(return_value=False)
        
        # Mock instance to verify feed assist is NOT called on error
        mock_instance = Mock()
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with self.assertRaises(Exception) as context:
            manager.perform_tool_change(current_tool=-1, target_tool=2)
        
        self.assertIn("not ready", str(context.exception))
        # CRITICAL: Feed assist must NOT be enabled on error
        mock_instance._enable_feed_assist.assert_not_called()

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_runout_detection_reinitialized_after_load(self, mock_endless_spool, mock_ace_instance):
        """Test runout detection baseline is reset after successful load."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_SPLITTER
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}  # Changed: sensors clear to avoid smart_unload
        manager.runout_monitor.runout_detection_active = False
        manager.runout_monitor.prev_toolhead_sensor_state = None
        
        # Mock instance for load
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {1: {'status': 'loaded', 'temp': 0}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 1)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            manager.perform_tool_change(current_tool=-1, target_tool=1)
            
            # Runout detection should be re-enabled
            self.assertTrue(manager.runout_monitor.runout_detection_active)
            # Baseline should be initialized (sensor is False, so baseline is False)
            self.assertFalse(manager.runout_monitor.prev_toolhead_sensor_state)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_toolchange_decorator_sets_flag(self, mock_endless_spool, mock_ace_instance):
        """Test that toolchange_in_progress flag is managed by decorator."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_SPLITTER
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        
        # Mock instance
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {1: {'status': 'loaded', 'temp': 0}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 1)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            # Initially false
            self.assertFalse(manager.toolchange_in_progress)
            
            manager.perform_tool_change(current_tool=-1, target_tool=1)
            
            # Should be reset to false after completion
            self.assertFalse(manager.toolchange_in_progress)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_invalid_state_nozzle_filled_rdm_empty_raises(self, mock_endless_spool, mock_ace_instance):
        """Test invalid state: filament at nozzle but RDM empty (broken filament path)."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_NOZZLE
        # Toolhead has filament, but RDM is empty - indicates broken filament!
        manager._sensor_override = {SENSOR_TOOLHEAD: True, SENSOR_RDM: False}
        # Mock has_rdm_sensor to return True (sensors dict isn't populated without _handle_ready)
        manager.has_rdm_sensor = Mock(return_value=True)
        
        with self.assertRaises(Exception) as context:
            manager.perform_tool_change(current_tool=1, target_tool=1)
        
        self.assertIn("Invalid filament state", str(context.exception))
        self.assertIn("RDM sensor is empty", str(context.exception))
        self.assertIn("manual intervention", str(context.exception))

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_state_mismatch_nozzle_state_but_sensor_empty_corrects_to_bowden(self, mock_endless_spool, mock_ace_instance):
        """Test state correction: state says nozzle but sensor is empty, no RDM filament."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_NOZZLE
        # Both sensors empty - state is wrong, should correct to bowden
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {1: {'status': 'ready', 'temp': 200}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 1)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            result = manager.perform_tool_change(current_tool=1, target_tool=1)
            
            # State should have been corrected to bowden (no RDM filament)
            # Then proceeded with load
            self.assertIn("1", result)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_state_mismatch_nozzle_state_but_sensor_empty_corrects_to_splitter(self, mock_endless_spool, mock_ace_instance):
        """Test state correction: state says nozzle but sensor empty, RDM has filament."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_NOZZLE
        # Toolhead empty but RDM has filament - correct to splitter
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: True}
        
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {1: {'status': 'ready', 'temp': 200}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 1)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            result = manager.perform_tool_change(current_tool=1, target_tool=1)
            
            # Should have corrected state and continued with load
            self.assertIn("1", result)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_target_tool_not_managed_raises(self, mock_endless_spool, mock_ace_instance):
        """Test error when target tool is not managed by any ACE instance."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_SPLITTER
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        manager.check_and_wait_for_spool_ready = Mock(return_value=True)
        
        # Mock instance to verify feed assist is NOT called on error
        mock_instance = Mock()
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (None, -1)  # Tool not managed
            
            with self.assertRaises(Exception) as context:
                manager.perform_tool_change(current_tool=-1, target_tool=99)
            
            self.assertIn("not managed", str(context.exception))
            # CRITICAL: Feed assist must NOT be enabled when tool not managed
            mock_instance._enable_feed_assist.assert_not_called()

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_unload_failure_raises_exception(self, mock_endless_spool, mock_ace_instance):
        """Test exception raised when unload fails."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_NOZZLE
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        manager.smart_unload = Mock(return_value=False)  # Unload fails!
        
        # Mock instance to verify feed assist is NOT called on error
        mock_instance = Mock()
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with self.assertRaises(Exception) as context:
            manager.perform_tool_change(current_tool=1, target_tool=2)
        
        self.assertIn("Failed to unload", str(context.exception))
        # CRITICAL: Feed assist must NOT be enabled on unload failure
        mock_instance._enable_feed_assist.assert_not_called()

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_reselection_with_path_blocked_clears_then_loads(self, mock_endless_spool, mock_ace_instance):
        """Test reselection when path is blocked triggers smart_unload first."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_SPLITTER
        # Sensors clear so we don't hit plausibility check, but is_filament_path_free returns False
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        
        # Path is blocked (sensor override says clear, but is_filament_path_free says blocked)
        # Need to make is_filament_path_free return False first, then True after unload
        call_count = [0]
        def path_free_side_effect():
            call_count[0] += 1
            return call_count[0] > 1  # First call blocked, subsequent calls clear
        
        manager.is_filament_path_free = Mock(side_effect=path_free_side_effect)
        manager.smart_unload = Mock(return_value=True)
        
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {1: {'status': 'ready', 'temp': 0}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 1)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            result = manager.perform_tool_change(current_tool=1, target_tool=1)
            
            # Should have attempted smart_unload to clear path
            manager.smart_unload.assert_called()

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_plausibility_mismatch_toolhead_sensor_unload_fails_raises(self, mock_endless_spool, mock_ace_instance):
        """Test plausibility check failure when smart_unload fails."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_BOWDEN
        # Sensors show filament but state says bowden - plausibility mismatch
        manager._sensor_override = {SENSOR_TOOLHEAD: True, SENSOR_RDM: False}
        manager.smart_unload = Mock(return_value=False)  # Unload fails!
        
        with self.assertRaises(Exception) as context:
            manager.perform_tool_change(current_tool=-1, target_tool=1)
        
        self.assertIn("Failed to clear filament path", str(context.exception))

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_plausibility_mismatch_rdm_sensor_unload_fails_raises(self, mock_endless_spool, mock_ace_instance):
        """Test plausibility check failure when smart_unload fails."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_BOWDEN
        # Sensors show filament but state says bowden - plausibility mismatch
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: True}
        manager.smart_unload = Mock(return_value=False)  # Unload fails!
        manager.has_rdm_sensor = Mock(return_value=True)
        
        with self.assertRaises(Exception) as context:
            manager.perform_tool_change(current_tool=-1, target_tool=1)
        
        self.assertIn("Failed to clear filament path", str(context.exception))

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_plausibility_mismatch_rdm_sensor_unload_success(self, mock_endless_spool, mock_ace_instance):
        """Test plausibility check when smart_unload succeeds."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_BOWDEN
        # Sensors show filament but state says bowden - plausibility mismatch
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: True}
        manager.smart_unload = Mock(return_value=True)  # Unload succeeds
        manager.check_and_wait_for_spool_ready = Mock(return_value=True)
        manager.has_rdm_sensor = Mock(return_value=True)
        manager.instances[0]._feed_filament_into_toolhead.return_value = 10.0
        result = manager.perform_tool_change(current_tool=-1, target_tool=1)
        
        self.assertIn("Tool -1 → 1", result)
                
    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_unknown_filament_pos_with_sensor_triggered_unloads(self, mock_endless_spool, mock_ace_instance):
        """Test handling of unknown filament_pos when sensor is triggered."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = 'unknown_garbage'  # Invalid state
        manager._sensor_override = {SENSOR_TOOLHEAD: True, SENSOR_RDM: False}  # Sensor triggered
        manager.smart_unload = Mock(return_value=True)
        
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {2: {'status': 'ready', 'temp': 0}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 2)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            result = manager.perform_tool_change(current_tool=1, target_tool=2)
            
            # Should have called smart_unload for unknown state with triggered sensor
            manager.smart_unload.assert_called()

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_unknown_filament_pos_sensor_clear_corrects_state(self, mock_endless_spool, mock_ace_instance):
        """Test handling of unknown filament_pos when sensor is clear - corrects state."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = 'garbage_state'  # Invalid state
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}  # Sensor clear
        
        mock_instance = Mock()
        mock_instance.instance_num = 0
        mock_instance.inventory = {2: {'status': 'ready', 'temp': 0}}
        mock_instance._feed_filament_into_toolhead = Mock(return_value=5.0)
        mock_instance._enable_feed_assist = Mock()
        manager.instances[0] = mock_instance
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (mock_instance, 2)
            manager.check_and_wait_for_spool_ready = Mock(return_value=True)
            
            result = manager.perform_tool_change(current_tool=1, target_tool=2)
            
            # Should have completed without unload (sensor clear = no filament)
            self.assertIn("2", result)


class TestConfigForTool(unittest.TestCase):
    """Test _get_config_for_tool method and error handling."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        
        self.variables = {
            'ace_global_enabled': True,
            'ace_current_index': -1,
            'ace_filament_pos': FILAMENT_STATE_SPLITTER,
        }
        self.mock_save_vars.allVariables = self.variables
        
        # Mock config getters
        self.mock_config.get.side_effect = self._mock_config_get
        self.mock_config.getint.side_effect = self._mock_config_getint
        self.mock_config.getfloat.side_effect = self._mock_config_getfloat

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        elif name == 'output_pin ACE_Pro':
            mock_pin = Mock()
            mock_pin.get_status = Mock(return_value={'value': 1})
            return mock_pin
        return default

    def _mock_config_get(self, key, default=None):
        """Mock config.get()."""
        config_values = {
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
        }
        return config_values.get(key, default)

    def _mock_config_getint(self, key, default=None):
        """Mock config.getint()."""
        config_values = {
            'ace_count': 2,
        }
        val = config_values.get(key, default)
        return int(val) if val is not None else default

    def _mock_config_getfloat(self, key, default=None):
        """Mock config.getfloat()."""
        config_values = {
            'feed_speed': '100.0',
            'retract_speed': '100.0',
            'toolhead_retraction_speed': '300.0',
            'toolhead_retraction_length': '10.0',
            'default_color_change_purge_length': '50.0',
            'default_color_change_purge_speed': '5.0',
            'total_max_feeding_length': '600.0',
            'parkposition_to_toolhead_length': '500.0',
            'toolchange_load_length': '400.0',
            'parkposition_to_rdm_length': '100.0',
            'incremental_feeding_length': '50.0',
            'incremental_feeding_speed': '20.0',
            'extruder_feeding_length': '50.0',
            'extruder_feeding_speed': '10.0',
            'toolhead_slow_loading_speed': '5.0',
            'heartbeat_interval': '5.0',
            'max_dryer_temperature': '70.0',
            'toolhead_full_purge_length': '80.0',
            'baud': '115200',
            'timeout_multiplier': '1.5',
        }
        return float(config_values.get(key, default or 0))

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_get_config_for_tool_valid(self, mock_endless_spool, mock_ace_instance):
        """Test _get_config_for_tool returns correct value for valid tool."""
        manager = AceManager(self.mock_config)
        
        # Mock instance with feed_speed attribute
        manager.instances[0].feed_speed = 150.0
        
        result = manager._get_config_for_tool(0, 'feed_speed')
        
        self.assertEqual(result, 150.0)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_get_config_for_tool_invalid_tool_index(self, mock_endless_spool, mock_ace_instance):
        """Test _get_config_for_tool raises exception for invalid tool index."""
        manager = AceManager(self.mock_config)
        
        # Tool index 999 is out of range
        with self.assertRaises(Exception) as context:
            manager._get_config_for_tool(999, 'feed_speed')
        
        self.assertIn("Invalid tool index", str(context.exception))

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_get_config_for_tool_invalid_param_name(self, mock_endless_spool, mock_ace_instance):
        """Test _get_config_for_tool raises exception for non-existent parameter."""
        manager = AceManager(self.mock_config)
        
        # Configure mock to raise AttributeError for nonexistent_param
        del manager.instances[0].nonexistent_param
        type(manager.instances[0]).nonexistent_param = PropertyMock(
            side_effect=AttributeError("'AceInstance' object has no attribute 'nonexistent_param'")
        )
        
        # 'nonexistent_param' doesn't exist on instance
        with self.assertRaises(Exception) as context:
            manager._get_config_for_tool(0, 'nonexistent_param')
        
        self.assertIn("not found", str(context.exception))


class TestGetPrinter(unittest.TestCase):
    """Test get_printer method."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        
        self.variables = {
            'ace_global_enabled': True,
            'ace_current_index': -1,
            'ace_filament_pos': FILAMENT_STATE_SPLITTER,
        }
        self.mock_save_vars.allVariables = self.variables
        
        # Mock config getters
        self.mock_config.get.side_effect = self._mock_config_get
        self.mock_config.getint.side_effect = self._mock_config_getint
        self.mock_config.getfloat.side_effect = self._mock_config_getfloat

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        elif name == 'output_pin ACE_Pro':
            mock_pin = Mock()
            mock_pin.get_status = Mock(return_value={'value': 1})
            return mock_pin
        return default

    def _mock_config_get(self, key, default=None):
        """Mock config.get()."""
        config_values = {
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
        }
        return config_values.get(key, default)

    def _mock_config_getint(self, key, default=None):
        """Mock config.getint()."""
        config_values = {
            'ace_count': 2,
        }
        val = config_values.get(key, default)
        return int(val) if val is not None else default

    def _mock_config_getfloat(self, key, default=None):
        """Mock config.getfloat()."""
        config_values = {
            'feed_speed': '100.0',
            'retract_speed': '100.0',
            'toolhead_retraction_speed': '300.0',
            'toolhead_retraction_length': '10.0',
            'default_color_change_purge_length': '50.0',
            'default_color_change_purge_speed': '5.0',
            'total_max_feeding_length': '600.0',
            'parkposition_to_toolhead_length': '500.0',
            'toolchange_load_length': '400.0',
            'parkposition_to_rdm_length': '100.0',
            'incremental_feeding_length': '50.0',
            'incremental_feeding_speed': '20.0',
            'extruder_feeding_length': '50.0',
            'extruder_feeding_speed': '10.0',
            'toolhead_slow_loading_speed': '5.0',
            'heartbeat_interval': '5.0',
            'max_dryer_temperature': '70.0',
            'toolhead_full_purge_length': '80.0',
            'baud': '115200',
            'timeout_multiplier': '1.5',
        }
        return float(config_values.get(key, default or 0))

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_get_printer_returns_printer_object(self, mock_endless_spool, mock_ace_instance):
        """Test get_printer returns the correct printer object."""
        manager = AceManager(self.mock_config)
        
        result = manager.get_printer()
        
        self.assertIs(result, self.mock_printer)


class TestIsFilamentPathFreeNoRDM(unittest.TestCase):
    """Test is_filament_path_free without RDM sensor."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        
        self.variables = {
            'ace_global_enabled': True,
            'ace_current_index': -1,
            'ace_filament_pos': FILAMENT_STATE_SPLITTER,
        }
        self.mock_save_vars.allVariables = self.variables
        
        # Mock config getters
        self.mock_config.get.side_effect = self._mock_config_get
        self.mock_config.getint.side_effect = self._mock_config_getint
        self.mock_config.getfloat.side_effect = self._mock_config_getfloat

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        elif name == 'output_pin ACE_Pro':
            mock_pin = Mock()
            mock_pin.get_status = Mock(return_value={'value': 1})
            return mock_pin
        return default

    def _mock_config_get(self, key, default=None):
        """Mock config.get()."""
        config_values = {
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
        }
        return config_values.get(key, default)

    def _mock_config_getint(self, key, default=None):
        """Mock config.getint()."""
        config_values = {
            'ace_count': 2,
        }
        val = config_values.get(key, default)
        return int(val) if val is not None else default

    def _mock_config_getfloat(self, key, default=None):
        """Mock config.getfloat()."""
        config_values = {
            'feed_speed': '100.0',
            'retract_speed': '100.0',
            'toolhead_retraction_speed': '300.0',
            'toolhead_retraction_length': '10.0',
            'default_color_change_purge_length': '50.0',
            'default_color_change_purge_speed': '5.0',
            'total_max_feeding_length': '600.0',
            'parkposition_to_toolhead_length': '500.0',
            'toolchange_load_length': '400.0',
            'parkposition_to_rdm_length': '100.0',
            'incremental_feeding_length': '50.0',
            'incremental_feeding_speed': '20.0',
            'extruder_feeding_length': '50.0',
            'extruder_feeding_speed': '10.0',
            'toolhead_slow_loading_speed': '5.0',
            'heartbeat_interval': '5.0',
            'max_dryer_temperature': '70.0',
            'toolhead_full_purge_length': '80.0',
            'baud': '115200',
            'timeout_multiplier': '1.5',
        }
        return float(config_values.get(key, default or 0))

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_is_filament_path_free_no_rdm_clear(self, mock_endless_spool, mock_ace_instance):
        """Test is_filament_path_free returns True when toolhead clear (no RDM sensor)."""
        manager = AceManager(self.mock_config)
        
        # Mock sensors: no RDM sensor configured
        manager.sensors = {
            SENSOR_TOOLHEAD: Mock(filament_present=False)
        }
        
        result = manager.is_filament_path_free()
        
        self.assertTrue(result)

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_is_filament_path_free_no_rdm_blocked(self, mock_endless_spool, mock_ace_instance):
        """Test is_filament_path_free returns False when toolhead blocked (no RDM sensor)."""
        manager = AceManager(self.mock_config)
        
        # Mock sensors: no RDM sensor configured, toolhead blocked
        manager.sensors = {
            SENSOR_TOOLHEAD: Mock(filament_present=True)
        }
        
        result = manager.is_filament_path_free()
        
        self.assertFalse(result)


class TestSmartUnload(unittest.TestCase):
    """Branch coverage for smart_unload."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {
            "ace_global_enabled": True,
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN,
        }
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "feed_speed": "100", "retract_speed": "100"}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            vals = {
                "feed_speed": "100.0",
                "retract_speed": "100.0",
                "toolhead_retraction_speed": "300.0",
                "toolhead_retraction_length": "10.0",
                "default_color_change_purge_length": "50.0",
                "default_color_change_purge_speed": "400.0",
                "timeout_multiplier": "2.0",
                "total_max_feeding_length": "1000.0",
                "parkposition_to_toolhead_length": "500.0",
                "toolchange_load_length": "480.0",
                "parkposition_to_rdm_length": "350.0",
                "incremental_feeding_length": "10.0",
                "incremental_feeding_speed": "50.0",
                "extruder_feeding_length": "50.0",
                "extruder_feeding_speed": "5.0",
                "toolhead_slow_loading_speed": "10.0",
                "heartbeat_interval": "1.0",
                "max_dryer_temperature": "70.0",
                "toolhead_full_purge_length": "100.0",
                "feed_assist_active_after_ace_connect": "1",
            }
            val = vals.get(key, default)
            return float(val) if val is not None else default

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = lambda k, default=None: {
            "filament_runout_sensor_name_rdm": "return_module",
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
        }.get(k, default)

    def _make_instance(self, instance_num=0):
        inst = Mock()
        inst.instance_num = instance_num
        inst.SLOT_COUNT = SLOTS_PER_ACE
        inst.tool_offset = instance_num * SLOTS_PER_ACE
        inst.inventory = [
            {"status": "ready"},
            {"status": "ready"},
            {"status": "ready"},
            {"status": "ready"},
        ]
        inst.parkposition_to_toolhead_length = 500
        inst.parkposition_to_rdm_length = 350
        inst.feed_speed = 100
        inst._smart_unload_slot = Mock(return_value=True)
        inst.wait_ready = Mock()
        return inst

    def _build_manager(self, instance_factory):
        with patch('ace.manager.AceInstance', side_effect=instance_factory), \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'):
            return AceManager(self.mock_config, dummy_ace_count=1)

    def test_known_tool_sensor_clear_success(self):
        """Sensor clear, no RDM: full parkposition_to_toolhead_length safety retract."""
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        # Reset inventory to ready after manager init overwrote it
        for slot in manager.instances[0].inventory:
            slot["status"] = "ready"
        manager.prepare_toolhead_for_filament_retraction = Mock()
        # Sensors not registered → get_instant_switch_state returns False (clear),
        # is_filament_path_free_instant returns True (path free), has_rdm_sensor False
        manager.get_switch_state = Mock(return_value=False)
        manager.is_filament_path_free = Mock(return_value=True)

        result = manager.smart_unload(tool_index=1, prepare_toolhead=False)

        self.assertTrue(result)
        instance._smart_unload_slot.assert_called_once()
        manager.state.set.assert_called_with("ace_filament_pos", FILAMENT_STATE_BOWDEN)

    def test_known_tool_manually_removed_path_free_with_rdm(self):
        """Toolhead clear + path fully free WITH RDM: only parkposition_to_rdm_length retract.

        Covers the case where filament was manually pulled out of the printer
        while the tool was still marked as loaded.  With RDM present and both
        sensors reading clear the code must use the shorter safety-retract
        distance (parkposition_to_rdm_length) instead of the full bowden length.
        """
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        for slot in manager.instances[0].inventory:
            slot["status"] = "ready"
        # Toolhead clear (not triggered)
        manager.get_instant_switch_state = Mock(return_value=False)
        # Entire path is free
        manager.is_filament_path_free_instant = Mock(return_value=True)
        # RDM sensor is present
        manager.has_rdm_sensor = Mock(return_value=True)

        result = manager.smart_unload(tool_index=1, prepare_toolhead=False)

        self.assertTrue(result)
        # Must use the short RDM-distance retract, NOT the full toolhead length
        expected_length = instance.parkposition_to_rdm_length  # 350 in test setup
        instance._smart_unload_slot.assert_called_once_with(
            local_slot=1, length=expected_length
        ) if False else instance._smart_unload_slot.assert_called_once()
        args, kwargs = instance._smart_unload_slot.call_args
        actual_length = kwargs.get("length", args[1] if len(args) > 1 else None)
        self.assertEqual(actual_length, expected_length,
                         "Safety retract length should be parkposition_to_rdm_length when RDM is present")
        manager.state.set.assert_called_with("ace_filament_pos", FILAMENT_STATE_BOWDEN)

    def test_known_tool_toolhead_clear_rdm_triggered_full_retract(self):
        """Toolhead clear but RDM still triggered: fall through to full parkposition_to_toolhead_length retract.

        Covers the case where the toolhead sensor clears (e.g. filament tip
        passed back past the toolhead sensor) but the RDM sensor still detects
        filament in the bowden tube.  Full retract length required.
        """
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        for slot in manager.instances[0].inventory:
            slot["status"] = "ready"
        # Toolhead clear
        manager.get_instant_switch_state = Mock(return_value=False)
        # First call: RDM still triggered → path NOT fully free → do full retract
        # Second call: post-retract validation → path is now free
        manager.is_filament_path_free_instant = Mock(side_effect=[False, True])
        manager.has_rdm_sensor = Mock(return_value=True)

        result = manager.smart_unload(tool_index=1, prepare_toolhead=False)

        self.assertTrue(result)
        # Must use the full toolhead distance, NOT the short RDM-only safety length
        args, kwargs = instance._smart_unload_slot.call_args
        actual_length = kwargs.get("length", args[1] if len(args) > 1 else None)
        expected_length = instance.parkposition_to_toolhead_length  # 500 in test setup
        self.assertEqual(actual_length, expected_length,
                         "Full retract length required when RDM sensor is still triggered")
        manager.state.set.assert_called_with("ace_filament_pos", FILAMENT_STATE_BOWDEN)

    def test_known_tool_empty_slot_raises(self):
        instance = self._make_instance()
        instance.inventory[1]["status"] = "empty"
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        manager.get_switch_state = Mock(return_value=False)

        with self.assertRaises(Exception):
            manager.smart_unload(tool_index=1, prepare_toolhead=False)

        manager.state.set.assert_not_called()

    def test_known_tool_sensor_triggered_path(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        for slot in manager.instances[0].inventory:
            slot["status"] = "ready"
        manager.prepare_toolhead_for_filament_retraction = Mock()
        manager.get_switch_state = Mock(return_value=True)
        manager.is_filament_path_free = Mock(return_value=True)
        manager._extruder_move = Mock()
        manager._wait_toolhead_move_finished = Mock()

        result = manager.smart_unload(tool_index=0, prepare_toolhead=False)

        self.assertTrue(result)
        instance._smart_unload_slot.assert_called_once()
        manager.state.set.assert_called_with("ace_filament_pos", FILAMENT_STATE_BOWDEN)

    def test_known_tool_invalid_instance_raises(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.get_switch_state = Mock(return_value=False)

        with self.assertRaises(Exception):
            manager.smart_unload(tool_index=20, prepare_toolhead=False)

    def test_unknown_tool_sensor_triggered_cycles(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set_and_save = Mock()
        self.variables["ace_current_index"] = -1
        manager.get_switch_state = Mock(side_effect=[True, False])  # toolhead triggered
        manager.has_rdm_sensor = Mock(return_value=False)
        manager._identify_and_unload_by_cycling = Mock(return_value=True)
        manager.prepare_toolhead_for_filament_retraction = Mock()

        result = manager.smart_unload(tool_index=-1, prepare_toolhead=True)

        self.assertTrue(result)
        manager._identify_and_unload_by_cycling.assert_called_once()
        manager.state.set_and_save.assert_not_called()

    def test_unknown_tool_sensors_clear_no_current_tool(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        self.variables["ace_current_index"] = -1
        manager.get_switch_state = Mock(return_value=False)
        manager.has_rdm_sensor = Mock(return_value=False)

        result = manager.smart_unload(tool_index=-1, prepare_toolhead=False)

        self.assertTrue(result)
        manager.state.set.assert_called_with("ace_filament_pos", FILAMENT_STATE_BOWDEN)

    def test_unknown_tool_sensors_clear_with_current_tool(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        self.variables["ace_current_index"] = 0
        manager.get_switch_state = Mock(return_value=False)
        manager.has_rdm_sensor = Mock(return_value=False)

        result = manager.smart_unload(tool_index=-1, prepare_toolhead=False)

        self.assertTrue(result)
        manager.state.set.assert_any_call("ace_current_index", -1)
        manager.state.set.assert_any_call("ace_filament_pos", FILAMENT_STATE_BOWDEN)

    def test_unknown_tool_invalid_state_raises(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set_and_save = Mock()
        self.variables["ace_current_index"] = -1
        manager.get_switch_state = Mock(return_value=False)
        manager.has_rdm_sensor = Mock(return_value=False)

        with self.assertRaises(Exception):
            manager.smart_unload(tool_index=99, prepare_toolhead=False)


class TestUpdateAceSupportActiveState(unittest.TestCase):
    """Coverage for update_ace_support_active_state and _sync_inventory_to_persistent."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {
            "ace_global_enabled": True,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN,
        }
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "feed_speed": "100", "retract_speed": "100"}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            vals = {"feed_speed": "100.0", "retract_speed": "100.0", "purge_multiplier": "1.0"}
            val = vals.get(key, default)
            return float(val) if val is not None else default

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = lambda k, default=None: {
            "filament_runout_sensor_name_rdm": "return_module",
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
        }.get(k, default)
        self.mock_config.getboolean.side_effect = lambda k, default=None: True

        with patch('ace.manager.AceInstance') as mock_inst_class, \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'):
            inst = Mock()
            inst.serial_mgr = Mock(enable_ace_pro=Mock(), disable_ace_pro=Mock())
            inst.inventory = create_inventory(SLOTS_PER_ACE)
            inst.instance_num = 0
            inst.tool_offset = 0
            mock_inst_class.return_value = inst
            self.manager = AceManager(self.mock_config, dummy_ace_count=1)
            self.instance = self.manager.instances[0]

    def test_disable_path_restores_sensors_and_disables_mgrs(self):
        self.manager._ace_pro_enabled = True
        self.manager.is_ace_enabled = Mock(return_value=False)
        self.manager._restore_sensors = Mock()
        self.manager.set_ace_global_enabled = Mock()

        self.manager.update_ace_support_active_state()

        self.manager._restore_sensors.assert_called_once()
        self.manager.set_ace_global_enabled.assert_called_once_with(False)
        self.instance.serial_mgr.disable_ace_pro.assert_called_once()
        self.assertFalse(self.manager._ace_pro_enabled)

    def test_enable_path_sets_up_sensors_and_enables_mgrs(self):
        self.manager._ace_pro_enabled = False
        self.manager.is_ace_enabled = Mock(return_value=True)
        self.manager._setup_sensors = Mock()
        self.manager._disable_all_sensor_detection = Mock()
        self.manager.set_ace_global_enabled = Mock()

        self.manager.update_ace_support_active_state()

        self.manager._setup_sensors.assert_called_once()
        self.manager._disable_all_sensor_detection.assert_called_once()
        self.manager.set_ace_global_enabled.assert_called_once_with(True)
        self.instance.serial_mgr.enable_ace_pro.assert_called_once()
        self.assertTrue(self.manager._ace_pro_enabled)

    def test_sync_inventory_single_instance(self):
        self.instance.inventory[0]["status"] = "ready"
        save_vars = self.mock_printer.lookup_object("save_variables")
        self.manager.gcode.run_script_from_command = Mock()

        self.manager._sync_inventory_to_persistent(instance_num=0)

        varname = "ace_inventory_0"
        self.assertIn(varname, save_vars.allVariables)
        self.assertEqual(save_vars.allVariables[varname], self.instance.inventory)
        # Default persistence_mode is "deferred": set_and_save() marks dirty
        # but does NOT issue a SAVE_VARIABLE GCode command immediately.
        self.assertTrue(self.manager.state.has_pending)
        self.manager.gcode.run_script_from_command.assert_not_called()

    def test_sync_inventory_all_instances(self):
        self.instance.inventory[1]["status"] = "ready"
        self.manager.gcode.run_script_from_command = Mock()
        # Add a second instance
        second = Mock()
        second.instance_num = 1
        second.inventory = create_inventory(SLOTS_PER_ACE)
        self.manager.instances.append(second)

        self.manager._sync_inventory_to_persistent()

        save_vars = self.mock_printer.lookup_object("save_variables")
        self.assertIn("ace_inventory_0", save_vars.allVariables)
        self.assertIn("ace_inventory_1", save_vars.allVariables)
        self.assertEqual(save_vars.allVariables["ace_inventory_1"], second.inventory)
        # Default persistence_mode is "deferred": set_and_save() marks dirty
        # but does NOT issue SAVE_VARIABLE GCode commands immediately.
        self.assertTrue(self.manager.state.has_pending)
        self.manager.gcode.run_script_from_command.assert_not_called()

    def test_flush_if_idle_flushes_when_not_printing(self):
        """Dirty state is flushed by _flush_if_idle when printer is idle."""
        self.manager.state.set("ace_current_index", 3)
        self.assertTrue(self.manager.state.has_pending)

        # print_stats returns 'standby' → idle
        mock_print_stats = Mock()
        mock_print_stats.get_status.return_value = {"state": "standby"}
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: (
            mock_print_stats if name == "print_stats" else
            self.mock_printer.lookup_object.return_value
        )

        self.manager._flush_if_idle(0.0)
        self.assertFalse(self.manager.state.has_pending)

    def test_flush_if_idle_skips_when_printing(self):
        """Dirty state is NOT flushed by _flush_if_idle during a print."""
        self.manager.state.set("ace_current_index", 3)
        self.assertTrue(self.manager.state.has_pending)

        # print_stats returns 'printing'
        mock_print_stats = Mock()
        mock_print_stats.get_status.return_value = {"state": "printing"}
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: (
            mock_print_stats if name == "print_stats" else
            self.mock_printer.lookup_object.return_value
        )

        self.manager._flush_if_idle(0.0)
        self.assertTrue(self.manager.state.has_pending)

    def test_flush_if_idle_noop_when_clean(self):
        """_flush_if_idle does nothing when there are no dirty vars."""
        self.assertFalse(self.manager.state.has_pending)
        self.manager.gcode.run_script_from_command = Mock()
        self.manager._flush_if_idle(0.0)
        self.manager.gcode.run_script_from_command.assert_not_called()


class TestIdentifyAndUnloadByCycling(unittest.TestCase):
    """Branch coverage for _identify_and_unload_by_cycling."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {"ace_global_enabled": True}
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "feed_speed": "100", "retract_speed": "100"}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            vals = {"feed_speed": "100.0", "retract_speed": "100.0", "purge_multiplier": "1.0"}
            val = vals.get(key, default)
            return float(val) if val is not None else default

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = lambda k, default=None: {
            "filament_runout_sensor_name_rdm": "return_module",
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
        }.get(k, default)
        self.mock_config.getboolean.side_effect = lambda k, default=None: True

    def _build_manager(self, instance_factory):
        with patch('ace.manager.AceInstance', side_effect=instance_factory), \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'):
            return AceManager(self.mock_config, dummy_ace_count=1)

    def _make_instance(self):
        inst = Mock()
        inst.instance_num = 0
        inst.SLOT_COUNT = SLOTS_PER_ACE
        inst.tool_offset = 0
        inst.inventory = [{"status": "ready"} for _ in range(SLOTS_PER_ACE)]
        inst.parkposition_to_rdm_length = 350
        inst.parkposition_to_toolhead_length = 500
        inst.feed_speed = 100
        return inst

    def test_path_clear_returns_true(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        manager.get_switch_state = Mock(return_value=False)
        manager.has_rdm_sensor = Mock(return_value=True)

        result = manager._identify_and_unload_by_cycling(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=5,
            retract_speed=5,
            retract_speed_mmmin=300,
            full_unload_length=20,
        )

        self.assertTrue(result)
        manager.state.set.assert_called_with("ace_filament_pos", FILAMENT_STATE_BOWDEN)

    def test_toolhead_triggered_uses_extruder_path(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.get_switch_state = Mock(side_effect=[True, False])
        manager.has_rdm_sensor = Mock(return_value=False)
        manager._cycle_slots_with_sensor_check = Mock(return_value=True)

        result = manager._identify_and_unload_by_cycling(
            current_tool_index=2,
            attempted_tool_index=-1,
            retract_length=5,
            retract_speed=5,
            retract_speed_mmmin=300,
            full_unload_length=20,
        )

        self.assertTrue(result)
        manager._cycle_slots_with_sensor_check.assert_called_once_with(
            2, -1, 5, 5, 300, 20, sensor_name=SENSOR_TOOLHEAD, use_extruder=True
        )

    def test_rdm_triggered_uses_ace_only(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.get_switch_state = Mock(side_effect=[False, True])
        manager.has_rdm_sensor = Mock(return_value=True)
        manager._cycle_slots_with_sensor_check = Mock(return_value=True)

        result = manager._identify_and_unload_by_cycling(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=5,
            retract_speed=5,
            retract_speed_mmmin=300,
            full_unload_length=20,
        )

        self.assertTrue(result)
        manager._cycle_slots_with_sensor_check.assert_called_once_with(
            -1, -1, 5, instance.feed_speed, 300, instance.parkposition_to_toolhead_length,
            sensor_name=SENSOR_RDM, use_extruder=False, sensor_to_parking_length=instance.parkposition_to_rdm_length
        )

    def test_unexpected_state_returns_false(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        # Simulate both sensors triggered but toolhead path already handled; ensures fall-through
        manager.get_switch_state = Mock(side_effect=[True, True])
        manager.has_rdm_sensor = Mock(return_value=True)
        manager._cycle_slots_with_sensor_check = Mock(return_value=False)

        result = manager._identify_and_unload_by_cycling(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=5,
            retract_speed=5,
            retract_speed_mmmin=300,
            full_unload_length=20,
        )

        self.assertFalse(result)


class TestConnectionIssueDialog(unittest.TestCase):
    """Coverage for _show_connection_issue_dialog."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {"ace_global_enabled": True}
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "feed_speed": "100", "retract_speed": "100"}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            vals = {"feed_speed": "100.0", "retract_speed": "100.0", "purge_multiplier": "1.0"}
            val = vals.get(key, default)
            return float(val) if val is not None else default

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = lambda k, default=None: {
            "filament_runout_sensor_name_rdm": "return_module",
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
        }.get(k, default)
        self.mock_config.getboolean.side_effect = lambda k, default=None: True

        with patch('ace.manager.AceInstance'), \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'):
            self.manager = AceManager(self.mock_config, dummy_ace_count=1)

    def test_dialog_includes_instance_details_and_printing_actions(self):
        unstable = [
            {"instance": 0, "connected": False, "recent_reconnects": 0, "time_connected": 0},
            {"instance": 1, "connected": True, "recent_reconnects": 4, "time_connected": 30},
            {"instance": 2, "connected": True, "recent_reconnects": 1, "time_connected": 5},
        ]

        self.manager._show_connection_issue_dialog(unstable, is_printing=True)

        calls = [args[0] for args, _ in self.mock_gcode.run_script_from_command.call_args_list]
        self.assertTrue(any("prompt_begin ACE Connection Issue" in c for c in calls))
        self.assertTrue(any("unstable (4 reconnects/min)" in c for c in calls))
        self.assertTrue(any("stabilizing (5s)" in c for c in calls))
        self.assertTrue(any("Print paused: ACE connection unstable." in c for c in calls))
        self.assertTrue(any("prompt_show" in c for c in calls))

    def test_dialog_non_printing_message(self):
        unstable = [{"instance": 0, "connected": True, "recent_reconnects": 0, "time_connected": 12}]

        self.manager._show_connection_issue_dialog(unstable, is_printing=False)

        calls = [args[0] for args, _ in self.mock_gcode.run_script_from_command.call_args_list]
        self.assertTrue(any("ACE connection issue detected." in c for c in calls))
        self.assertTrue(any("prompt_show" in c for c in calls))


class TestHandleConnectionIssue(unittest.TestCase):
    """Coverage for _handle_connection_issue."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_print_stats = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {"ace_global_enabled": True}
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "print_stats":
                return self.mock_print_stats
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "feed_speed": "100", "retract_speed": "100"}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            vals = {"feed_speed": "100.0", "retract_speed": "100.0", "purge_multiplier": "1.0"}
            val = vals.get(key, default)
            return float(val) if val is not None else default

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = lambda k, default=None: {
            "filament_runout_sensor_name_rdm": "return_module",
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
        }.get(k, default)
        self.mock_config.getboolean.side_effect = lambda k, default=None: True

        with patch('ace.manager.AceInstance'), \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'):
            self.manager = AceManager(self.mock_config, dummy_ace_count=1)
        self.manager._pause_for_connection_issue = Mock()
        self.manager._show_connection_issue_dialog = Mock()

    def test_pauses_and_dialog_when_printing(self):
        unstable = [{"instance": 0, "connected": False, "recent_reconnects": 5, "time_connected": 0}]
        self.mock_print_stats.get_status.return_value = {"state": "printing"}

        self.manager._handle_connection_issue(unstable, eventtime=0.0)

        self.manager._pause_for_connection_issue.assert_called_once_with(unstable)
        self.assertTrue(self.manager._connection_issue_shown)

    def test_shows_dialog_only_when_not_printing(self):
        unstable = [{"instance": 1, "connected": True, "recent_reconnects": 1, "time_connected": 12}]
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: (
            self.mock_gcode if name == "gcode" else
            self.mock_save_vars if name == "save_variables" else
            self.mock_print_stats if name == "print_stats" else
            (Mock(get_status=Mock(side_effect=Exception("boom"))) if name == "print_stats" else None)
        )

        self.manager._handle_connection_issue(unstable, eventtime=0.0)

        self.manager._show_connection_issue_dialog.assert_called_once_with(unstable, is_printing=False)
        self.assertTrue(self.manager._connection_issue_shown)

class TestSmartLoad(unittest.TestCase):
    """Branch coverage for smart_load."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {
            "ace_global_enabled": True,
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN,
        }
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "feed_speed": "100", "retract_speed": "100"}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            vals = {
                "feed_speed": "100.0",
                "retract_speed": "100.0",
                "toolhead_retraction_speed": "300.0",
                "toolhead_retraction_length": "10.0",
                "default_color_change_purge_length": "50.0",
                "default_color_change_purge_speed": "400.0",
                "timeout_multiplier": "2.0",
                "total_max_feeding_length": "1000.0",
                "parkposition_to_toolhead_length": "500.0",
                "toolchange_load_length": "480.0",
                "parkposition_to_rdm_length": "350.0",
                "incremental_feeding_length": "10.0",
                "incremental_feeding_speed": "50.0",
                "extruder_feeding_length": "50.0",
                "extruder_feeding_speed": "5.0",
                "toolhead_slow_loading_speed": "10.0",
                "heartbeat_interval": "1.0",
                "max_dryer_temperature": "70.0",
                "toolhead_full_purge_length": "100.0",
                "feed_assist_active_after_ace_connect": "1",
            }
            val = vals.get(key, default)
            return float(val) if val is not None else default

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = lambda k, default=None: {
            "filament_runout_sensor_name_rdm": "return_module",
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
        }.get(k, default)

    def _make_instance(self, instance_num=0):
        inst = Mock()
        inst.instance_num = instance_num
        inst.SLOT_COUNT = SLOTS_PER_ACE
        inst.tool_offset = instance_num * SLOTS_PER_ACE
        inst.inventory = [
            {"status": "ready"},
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        inst.toolchange_load_length = 100
        inst.parkposition_to_toolhead_length = 50
        inst.parkposition_to_rdm_length = 30
        inst.retract_speed = 25
        inst._feed_filament_to_verification_sensor = Mock()
        inst._stop_feed = Mock()
        inst._retract = Mock()
        return inst

    def _build_manager(self, instance_factory):
        with patch('ace.manager.AceInstance', side_effect=instance_factory), \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'):
            return AceManager(self.mock_config, dummy_ace_count=1)

    def test_path_blocked_initially_returns_false(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.is_filament_path_free = Mock(return_value=False)

        result = manager.smart_load()

        self.assertFalse(result)
        instance._feed_filament_to_verification_sensor.assert_not_called()

    def test_rdm_successful_load_updates_positions(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        # Only slot 0 has filament
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        manager.has_rdm_sensor = Mock(return_value=True)
        manager.get_switch_state = Mock(return_value=True)  # sensor triggered
        manager.is_filament_path_free = Mock(return_value=True)

        result = manager.smart_load()

        self.assertTrue(result)
        instance._feed_filament_to_verification_sensor.assert_called_once_with(
            0, SENSOR_RDM, instance.toolchange_load_length
        )
        instance._retract.assert_called_once_with(0, length=instance.parkposition_to_rdm_length, speed=instance.retract_speed)
        # Positions updated: splitter via set() during loop, bowden via set() at end, index via set()
        manager.state.set.assert_any_call("ace_filament_pos", FILAMENT_STATE_SPLITTER)
        manager.state.set.assert_any_call("ace_filament_pos", FILAMENT_STATE_BOWDEN)
        manager.state.set.assert_any_call("ace_current_index", -1)

    def test_sensor_not_triggered_safety_retract(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set_and_save = Mock()
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        manager.has_rdm_sensor = Mock(return_value=False)
        manager.get_switch_state = Mock(side_effect=[False, False])  # sensor not triggered
        manager.is_filament_path_free = Mock(return_value=True)

        result = manager.smart_load()

        self.assertFalse(result)
        instance._stop_feed.assert_called_once_with(0)
        instance._retract.assert_called_once_with(0, length=instance.parkposition_to_toolhead_length, speed=instance.retract_speed)
        manager.state.set_and_save.assert_not_called()

    def test_exception_during_feed_returns_false(self):
        instance = self._make_instance()
        instance._feed_filament_to_verification_sensor.side_effect = RuntimeError("fail")
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set_and_save = Mock()
        manager.has_rdm_sensor = Mock(return_value=False)
        manager.get_switch_state = Mock(return_value=True)
        manager.is_filament_path_free = Mock(return_value=True)

        result = manager.smart_load()

        self.assertFalse(result)
        manager.state.set_and_save.assert_not_called()

    def test_toolhead_success_sets_positions(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        manager.has_rdm_sensor = Mock(return_value=False)
        manager.get_switch_state = Mock(return_value=True)
        manager.is_filament_path_free = Mock(side_effect=[True, True])

        result = manager.smart_load()

        self.assertTrue(result)
        manager.state.set.assert_any_call("ace_filament_pos", FILAMENT_STATE_TOOLHEAD)
        manager.state.set.assert_any_call("ace_filament_pos", FILAMENT_STATE_BOWDEN)
        manager.state.set.assert_any_call("ace_current_index", -1)

    def test_toolhead_path_never_sets_splitter_state(self):
        """Non-RDM setups must not write FILAMENT_STATE_SPLITTER."""
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        manager.state.set_and_save = Mock()
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        manager.has_rdm_sensor = Mock(return_value=False)
        manager.get_switch_state = Mock(return_value=True)
        manager.is_filament_path_free = Mock(side_effect=[True, True])

        result = manager.smart_load()

        self.assertTrue(result)
        all_state_calls = (
            list(manager.state.set.call_args_list)
            + list(manager.state.set_and_save.call_args_list)
        )
        self.assertTrue(
            all(call.args[1] != FILAMENT_STATE_SPLITTER for call in all_state_calls),
            "Unexpected splitter state recorded for non-RDM topology",
        )

    def test_path_not_clear_after_parking_skips_success(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        manager.has_rdm_sensor = Mock(return_value=False)
        manager.get_switch_state = Mock(return_value=True)
        manager.is_filament_path_free = Mock(side_effect=[True, False])

        result = manager.smart_load()

        self.assertFalse(result)
        instance._retract.assert_called_once()

class TestMonitorAceState(unittest.TestCase):
    """Coverage for _monitor_ace_state."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {
            "ace_global_enabled": True,
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN,
        }
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "feed_speed": "100", "retract_speed": "100"}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            vals = {
                "feed_speed": "100.0",
                "retract_speed": "100.0",
                "toolhead_retraction_speed": "300.0",
                "toolhead_retraction_length": "10.0",
                "default_color_change_purge_length": "50.0",
                "default_color_change_purge_speed": "400.0",
                "timeout_multiplier": "2.0",
                "total_max_feeding_length": "1000.0",
                "parkposition_to_toolhead_length": "500.0",
                "toolchange_load_length": "480.0",
                "parkposition_to_rdm_length": "350.0",
                "incremental_feeding_length": "10.0",
                "incremental_feeding_speed": "50.0",
                "extruder_feeding_length": "50.0",
                "extruder_feeding_speed": "5.0",
                "toolhead_slow_loading_speed": "10.0",
                "heartbeat_interval": "1.0",
                "max_dryer_temperature": "70.0",
                "toolhead_full_purge_length": "100.0",
                "feed_assist_active_after_ace_connect": "1",
            }
            val = vals.get(key, default)
            return float(val) if val is not None else default

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = lambda k, default=None: {
            "filament_runout_sensor_name_rdm": "return_module",
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
        }.get(k, default)

    def _build_manager(self):
        with patch('ace.manager.AceInstance'), patch('ace.manager.EndlessSpool'), patch('ace.manager.RunoutMonitor'):
            return AceManager(self.mock_config, dummy_ace_count=1)

    def test_calls_update_and_check_health(self):
        manager = self._build_manager()
        manager._ace_pro_enabled = True
        manager._connection_supervision_enabled = True
        manager.update_ace_support_active_state = Mock()
        manager._check_connection_health = Mock()

        next_time = manager._monitor_ace_state(5.0)

        manager.update_ace_support_active_state.assert_called_once()
        manager._check_connection_health.assert_called_once_with(5.0)
        self.assertEqual(next_time, 7.0)

    def test_skips_check_when_disabled(self):
        manager = self._build_manager()
        manager._ace_pro_enabled = False
        manager._connection_supervision_enabled = True
        manager.update_ace_support_active_state = Mock()
        manager._check_connection_health = Mock()

        next_time = manager._monitor_ace_state(10.0)

        manager.update_ace_support_active_state.assert_called_once()
        manager._check_connection_health.assert_not_called()
        self.assertEqual(next_time, 12.0)

    def test_exception_logged_and_returns_next_interval(self):
        manager = self._build_manager()
        manager.update_ace_support_active_state = Mock(side_effect=RuntimeError("boom"))
        manager._check_connection_health = Mock()

        next_time = manager._monitor_ace_state(2.5)

        manager._check_connection_health.assert_not_called()
        self.assertEqual(next_time, 4.5)


class TestFullUnloadSlot(unittest.TestCase):
    """Branch coverage for full_unload_slot."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {
            "ace_global_enabled": True,
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN,
        }
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "feed_speed": "100", "retract_speed": "100"}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            vals = {
                "feed_speed": "100.0",
                "retract_speed": "100.0",
                "toolhead_retraction_speed": "300.0",
                "toolhead_retraction_length": "10.0",
                "default_color_change_purge_length": "50.0",
                "default_color_change_purge_speed": "400.0",
                "timeout_multiplier": "2.0",
                "total_max_feeding_length": "1000.0",
                "parkposition_to_toolhead_length": "500.0",
                "toolchange_load_length": "480.0",
                "parkposition_to_rdm_length": "350.0",
                "incremental_feeding_length": "10.0",
                "incremental_feeding_speed": "50.0",
                "extruder_feeding_length": "50.0",
                "extruder_feeding_speed": "5.0",
                "toolhead_slow_loading_speed": "10.0",
                "heartbeat_interval": "1.0",
                "max_dryer_temperature": "70.0",
                "toolhead_full_purge_length": "100.0",
                "feed_assist_active_after_ace_connect": "1",
            }
            val = vals.get(key, default)
            return float(val) if val is not None else default

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = lambda k, default=None: {
            "filament_runout_sensor_name_rdm": "return_module",
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
        }.get(k, default)

    def _make_instance(self, instance_num=0):
        inst = Mock()
        inst.instance_num = instance_num
        inst.SLOT_COUNT = SLOTS_PER_ACE
        inst.tool_offset = instance_num * SLOTS_PER_ACE
        inst.inventory = [
            {"status": "ready"},
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        inst._info = {"slots": [{"status": "ready"} for _ in range(SLOTS_PER_ACE)]}
        inst.total_max_feeding_length = 1000
        inst.retract_speed = 50
        inst._feed_assist_index = -1
        inst._disable_feed_assist = Mock()
        inst.wait_ready = Mock()
        inst._retract = Mock()
        inst.parkposition_to_toolhead_length = 500
        inst.parkposition_to_rdm_length = 350
        return inst

    def _build_manager(self, instance_factory):
        with patch('ace.manager.AceInstance', side_effect=instance_factory), \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'):
            return AceManager(self.mock_config, dummy_ace_count=1)

    def test_full_unload_skips_empty(self):
        instance = self._make_instance()
        instance.inventory[1]["status"] = "empty"
        instance._info["slots"][1]["status"] = "empty"
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set_and_save = Mock()
        result = manager.full_unload_slot(1)
        self.assertTrue(result)
        instance._retract.assert_not_called()
        manager.state.set_and_save.assert_not_called()

    def test_full_unload_rdm_success(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        manager.get_switch_state = Mock(side_effect=[False, False])  # both sensors clear after retract
        manager.has_rdm_sensor = Mock(return_value=True)

        result = manager.full_unload_slot(0)

        self.assertTrue(result)
        instance._retract.assert_called_once_with(0, length=instance.total_max_feeding_length, speed=instance.retract_speed)
        manager.state.set.assert_called_with("ace_filament_pos", FILAMENT_STATE_BOWDEN)

    def test_full_unload_toolhead_only_blocked(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set_and_save = Mock()
        manager.get_instant_switch_state = Mock(return_value=True)  # toolhead still blocked
        manager.has_rdm_sensor = Mock(return_value=False)

        result = manager.full_unload_slot(0)

        self.assertFalse(result)
        instance._retract.assert_called_once()
        manager.state.set_and_save.assert_not_called()

    def test_full_unload_invalid_tool(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)

        result = manager.full_unload_slot(99)

        self.assertFalse(result)


class TestCheckAndWaitForSpoolReady(unittest.TestCase):
    """Branch coverage for check_and_wait_for_spool_ready."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {
            "ace_global_enabled": True,
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN,
        }
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "feed_speed": "100", "retract_speed": "100"}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            vals = {
                "feed_speed": "100.0",
                "retract_speed": "100.0",
                "toolhead_retraction_speed": "300.0",
                "toolhead_retraction_length": "10.0",
                "default_color_change_purge_length": "50.0",
                "default_color_change_purge_speed": "400.0",
                "timeout_multiplier": "2.0",
                "total_max_feeding_length": "1000.0",
                "parkposition_to_toolhead_length": "500.0",
                "toolchange_load_length": "480.0",
                "parkposition_to_rdm_length": "350.0",
                "incremental_feeding_length": "10.0",
                "incremental_feeding_speed": "50.0",
                "extruder_feeding_length": "50.0",
                "extruder_feeding_speed": "5.0",
                "toolhead_slow_loading_speed": "10.0",
                "heartbeat_interval": "1.0",
                "max_dryer_temperature": "70.0",
                "toolhead_full_purge_length": "100.0",
                "feed_assist_active_after_ace_connect": "1",
            }
            val = vals.get(key, default)
            return float(val) if val is not None else default

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = lambda k, default=None: {
            "filament_runout_sensor_name_rdm": "return_module",
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
        }.get(k, default)

    def _make_instance(self):
        inst = Mock()
        inst.instance_num = 0
        inst.SLOT_COUNT = SLOTS_PER_ACE
        inst.tool_offset = 0
        inst.inventory = [
            {"status": "ready"},
            {"status": "not_ready"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        inst._info = {"slots": [{"status": "ready"}, {"status": "not_ready"}, {"status": "empty"}, {"status": "empty"}]}
        inst.wait_ready = Mock()
        return inst

    def _build_manager(self, instance_factory):
        with patch('ace.manager.AceInstance', side_effect=instance_factory), \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'):
            return AceManager(self.mock_config, dummy_ace_count=1)

    def test_returns_false_for_invalid_tool(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        result = manager.check_and_wait_for_spool_ready(99)
        self.assertFalse(result)

    def test_returns_immediately_when_already_ready(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        # Restore ready status after manager init override
        manager.instances[0].inventory[0]["status"] = "ready"
        manager.instances[0]._info["slots"][0]["status"] = "ready"
        # Tool 0 -> slot 0 is ready; also ensure reactor.pause is harmless
        manager.reactor.pause = Mock()
        result = manager.check_and_wait_for_spool_ready(0)
        self.assertTrue(result)

    def test_waits_until_stable_ready(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory[1]["status"] = "not_ready"
        manager.instances[0]._info["slots"][1]["status"] = "not_ready"
        manager._show_spool_not_ready_prompt = Mock()
        manager.gcode.run_script_from_command = Mock()

        # Tool 1 starts not ready then becomes ready and stays ready
        states = [
            ("not_ready", "not_ready"),
            ("ready", "ready"),
            ("ready", "ready"),
            ("ready", "ready"),
        ]

        def advance_status():
            if states:
                status = states.pop(0)
                instance.inventory[1]["status"] = status[0]
                instance._info["slots"][1]["status"] = status[1]

        # Patch reactor.monotonic to advance time by 1s per call
        t = [0.0]
        def fake_monotonic():
            t[0] += 1.0
            return t[0]

        with patch.object(manager.reactor, "monotonic", side_effect=fake_monotonic):
            manager.reactor.pause = Mock(side_effect=lambda ts: advance_status())
            result = manager.check_and_wait_for_spool_ready(1, timeout_s=10, check_interval_s=1.0, stable_ready_s=2.0)

        self.assertTrue(result)
        manager._show_spool_not_ready_prompt.assert_called_once()
        manager.gcode.run_script_from_command.assert_any_call('RESPOND TYPE=command MSG="action:prompt_end"')


class TestCheckConnectionHealth(unittest.TestCase):
    """Branch coverage for _check_connection_health."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {
            "ace_global_enabled": True,
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN,
        }
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "feed_speed": "100", "retract_speed": "100"}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            vals = {
                "feed_speed": "100.0",
                "retract_speed": "100.0",
                "toolhead_retraction_speed": "300.0",
                "toolhead_retraction_length": "10.0",
                "default_color_change_purge_length": "50.0",
                "default_color_change_purge_speed": "400.0",
                "timeout_multiplier": "2.0",
                "total_max_feeding_length": "1000.0",
                "parkposition_to_toolhead_length": "500.0",
                "toolchange_load_length": "480.0",
                "parkposition_to_rdm_length": "350.0",
                "incremental_feeding_length": "10.0",
                "incremental_feeding_speed": "50.0",
                "extruder_feeding_length": "50.0",
                "extruder_feeding_speed": "5.0",
                "toolhead_slow_loading_speed": "10.0",
                "heartbeat_interval": "1.0",
                "max_dryer_temperature": "70.0",
                "toolhead_full_purge_length": "100.0",
                "feed_assist_active_after_ace_connect": "1",
            }
            val = vals.get(key, default)
            return float(val) if val is not None else default

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = lambda k, default=None: {
            "filament_runout_sensor_name_rdm": "return_module",
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
        }.get(k, default)

    def _build_manager(self):
        with patch('ace.manager.AceInstance'), patch('ace.manager.EndlessSpool'), patch('ace.manager.RunoutMonitor'):
            manager = AceManager(self.mock_config, dummy_ace_count=1)
        return manager

    def _make_instance_with_status(self, stable, reconnects, connected=True, time_connected=10):
        inst = Mock()
        inst.instance_num = 0
        inst.serial_mgr = Mock()
        inst.serial_mgr.INSTABILITY_THRESHOLD = 2
        inst.serial_mgr.get_connection_status.return_value = {
            "stable": stable,
            "recent_reconnects": reconnects,
            "connected": connected,
            "time_connected": time_connected,
        }
        return inst

    def test_handles_unstable_instances_when_not_shown(self):
        manager = self._build_manager()
        inst = self._make_instance_with_status(stable=False, reconnects=3)
        manager.instances = [inst]
        manager._connection_issue_shown = False
        manager._handle_connection_issue = Mock()

        manager._check_connection_health(eventtime=0.0)

        manager._handle_connection_issue.assert_called_once()

    def test_logs_stabilized_and_closes_dialog(self):
        manager = self._build_manager()
        inst = self._make_instance_with_status(stable=True, reconnects=0, time_connected=15)
        manager.instances = [inst]
        manager._last_connection_status = {0: {"stable": False}}
        manager._connection_issue_shown = True
        manager._close_connection_dialog = Mock()

        manager._check_connection_health(eventtime=5.0)

        msgs = [str(c.args[0]) for c in self.mock_gcode.respond_info.call_args_list]
        self.assertTrue(any("Connection stabilized" in m for m in msgs))
        manager._close_connection_dialog.assert_called_once()
        self.assertFalse(manager._connection_issue_shown)


class TestExecuteCoordinatedRetraction(unittest.TestCase):
    """Coverage for execute_coordinated_retraction."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {
            "ace_global_enabled": True,
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN,
        }
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "feed_speed": "100", "retract_speed": "100"}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            vals = {
                "feed_speed": "100.0",
                "retract_speed": "100.0",
                "toolhead_retraction_speed": "300.0",
                "toolhead_retraction_length": "10.0",
                "default_color_change_purge_length": "50.0",
                "default_color_change_purge_speed": "400.0",
                "timeout_multiplier": "2.0",
                "total_max_feeding_length": "1000.0",
                "parkposition_to_toolhead_length": "500.0",
                "toolchange_load_length": "480.0",
                "parkposition_to_rdm_length": "350.0",
                "incremental_feeding_length": "10.0",
                "incremental_feeding_speed": "50.0",
                "extruder_feeding_length": "50.0",
                "extruder_feeding_speed": "5.0",
                "toolhead_slow_loading_speed": "10.0",
                "heartbeat_interval": "1.0",
                "max_dryer_temperature": "70.0",
                "toolhead_full_purge_length": "100.0",
                "feed_assist_active_after_ace_connect": "1",
            }
            val = vals.get(key, default)
            return float(val) if val is not None else default

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = lambda k, default=None: {
            "filament_runout_sensor_name_rdm": "return_module",
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
        }.get(k, default)

    def _build_manager(self, instance_factory):
        with patch('ace.manager.AceInstance', side_effect=instance_factory), \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'):
            return AceManager(self.mock_config, dummy_ace_count=1)

    def _make_instance(self):
        inst = Mock()
        inst.instance_num = 0
        inst.SLOT_COUNT = SLOTS_PER_ACE
        inst.tool_offset = 0
        inst.inventory = [{"status": "ready"} for _ in range(SLOTS_PER_ACE)]
        inst.wait_ready = Mock()
        inst._retract = Mock()
        inst.dwell = Mock()
        return inst

    def test_valid_tool_retracts_and_waits(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory = [{"status": "ready"} for _ in range(SLOTS_PER_ACE)]
        manager.reactor.monotonic = Mock(return_value=0.0)

        manager.execute_coordinated_retraction(
            retract_length=10,
            retract_speed=5,
            retract_speed_mmmin=600,
            current_tool=0,
        )

        instance.wait_ready.assert_called()
        instance._retract.assert_called_once_with(0, length=10, speed=5)
        self.mock_gcode.run_script_from_command.assert_has_calls(
            [call("M83"), call("G1 E-10 F600"), call("G92 E0")]
        )
        instance.dwell.assert_called_once_with(3.0)

    def test_invalid_tool_emits_warning(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)

        manager.execute_coordinated_retraction(
            retract_length=10,
            retract_speed=5,
            retract_speed_mmmin=600,
            current_tool=-1,
        )

        instance._retract.assert_not_called()
        instance.dwell.assert_not_called()

    def test_status_timeout_warns_and_exits(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory[0]["status"] = "moving"

        # monotonic values: start=0, first elapsed=0.1, pause tick=0.2, then 5.2 to trigger timeout
        manager.reactor.monotonic = Mock(side_effect=[0.0, 0.1, 0.2, 5.2])

        manager.execute_coordinated_retraction(
            retract_length=10,
            retract_speed=5,
            retract_speed_mmmin=600,
            current_tool=0,
        )

        manager.reactor.pause.assert_called()  # ensure loop waited at least once

    def test_disables_feed_assist_before_retraction(self):
        """Feed assist must be disabled BEFORE retraction starts."""
        instance = self._make_instance()
        instance._feed_assist_index = 0  # Feed assist active on slot 0
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory = [{"status": "ready"} for _ in range(SLOTS_PER_ACE)]
        manager.reactor.monotonic = Mock(return_value=0.0)

        # Track call order to prove disable happens before retract
        call_order = []
        instance._disable_feed_assist.side_effect = lambda s: call_order.append('disable_feed_assist')
        instance._retract.side_effect = lambda *a, **k: call_order.append('retract')

        manager.execute_coordinated_retraction(
            retract_length=10,
            retract_speed=5,
            retract_speed_mmmin=600,
            current_tool=0,
        )

        instance._disable_feed_assist.assert_called_once_with(0)
        self.assertEqual(call_order, ['disable_feed_assist', 'retract'])

    def test_skips_feed_assist_disable_when_not_active(self):
        """No disable call when feed assist is not active on this slot."""
        instance = self._make_instance()
        instance._feed_assist_index = -1  # Feed assist not active
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory = [{"status": "ready"} for _ in range(SLOTS_PER_ACE)]
        manager.reactor.monotonic = Mock(return_value=0.0)

        manager.execute_coordinated_retraction(
            retract_length=10,
            retract_speed=5,
            retract_speed_mmmin=600,
            current_tool=0,
        )

        instance._disable_feed_assist.assert_not_called()
        instance._retract.assert_called_once()


class TestExtruderMove(unittest.TestCase):
    """Coverage for _extruder_move helper on manager."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_toolhead = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.mock_save_vars.allVariables = {"ace_global_enabled": True}

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "toolhead":
                return self.mock_toolhead
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "feed_speed": "100", "retract_speed": "100"}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            vals = {"feed_speed": "100.0", "retract_speed": "100.0", "purge_multiplier": "1.0"}
            val = vals.get(key, default)
            return float(val) if val is not None else default

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = lambda k, default=None: {
            "filament_runout_sensor_name_rdm": "return_module",
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
        }.get(k, default)
        self.mock_config.getboolean.side_effect = lambda k, default=None: True

    def _build_manager(self):
        with patch('ace.manager.AceInstance'), \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'):
            mgr = AceManager(self.mock_config, dummy_ace_count=1)
            mgr.instance_num = 0  # used in respond_info formatting
            return mgr

    def test_skip_zero_length_move(self):
        manager = self._build_manager()
        self.mock_toolhead.move = Mock()

        manager._extruder_move(0, 25, wait_for_move_end=True)

        # Last respond_info call should be the skip message
        self.assertIn(
            "Skipping zero-length move",
            self.mock_gcode.respond_info.call_args_list[-1][0][0],
        )
        self.mock_toolhead.move.assert_not_called()

    def test_moves_without_wait(self):
        manager = self._build_manager()
        self.mock_toolhead.get_position.return_value = [1, 2, 3, 4]
        self.mock_toolhead.move = Mock()
        self.mock_toolhead.wait_moves = Mock()

        manager._extruder_move(5, 50)

        self.mock_toolhead.move.assert_called_once_with([1, 2, 3, 9], 50)
        self.mock_toolhead.wait_moves.assert_not_called()

    def test_moves_and_waits_when_requested(self):
        manager = self._build_manager()
        self.mock_toolhead.get_position.return_value = [10, 20, 30, 40]
        self.mock_toolhead.move = Mock()
        self.mock_toolhead.wait_moves = Mock()

        manager._extruder_move(3, 15, wait_for_move_end=True)

        self.mock_toolhead.move.assert_called_once_with([10, 20, 30, 43], 15)
        self.mock_toolhead.wait_moves.assert_called_once()

class TestCycleSlotsWithSensorCheck(unittest.TestCase):
    """Branch coverage for _cycle_slots_with_sensor_check."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {
            "ace_global_enabled": True,
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN,
        }
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "feed_speed": "100", "retract_speed": "100"}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            vals = {
                "feed_speed": "100.0",
                "retract_speed": "100.0",
                "toolhead_retraction_speed": "300.0",
                "toolhead_retraction_length": "10.0",
                "default_color_change_purge_length": "50.0",
                "default_color_change_purge_speed": "400.0",
                "timeout_multiplier": "2.0",
                "total_max_feeding_length": "1000.0",
                "parkposition_to_toolhead_length": "500.0",
                "toolchange_load_length": "480.0",
                "parkposition_to_rdm_length": "350.0",
                "incremental_feeding_length": "10.0",
                "incremental_feeding_speed": "50.0",
                "extruder_feeding_length": "50.0",
                "extruder_feeding_speed": "5.0",
                "toolhead_slow_loading_speed": "10.0",
                "heartbeat_interval": "1.0",
                "max_dryer_temperature": "70.0",
                "toolhead_full_purge_length": "100.0",
                "feed_assist_active_after_ace_connect": "1",
            }
            val = vals.get(key, default)
            return float(val) if val is not None else default

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = lambda k, default=None: {
            "filament_runout_sensor_name_rdm": "return_module",
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
        }.get(k, default)

    def _build_manager(self, instance_factory):
        with patch('ace.manager.AceInstance', side_effect=instance_factory), \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'):
            return AceManager(self.mock_config, dummy_ace_count=1)

    def _make_instance(self):
        inst = Mock()
        inst.instance_num = 0
        inst.SLOT_COUNT = SLOTS_PER_ACE
        inst.tool_offset = 0
        inst.inventory = [
            {"status": "ready"},
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        inst._info = {"slots": [{"status": "ready"} for _ in range(SLOTS_PER_ACE)]}
        inst._smart_unload_slot = Mock()
        inst.wait_ready = Mock()
        inst._retract = Mock()
        inst._stop_feed = Mock()
        return inst

    def test_use_extruder_identifies_and_unloads(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        # Ensure only slot 0 has filament after manager init
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        manager.instances[0]._info = {"slots": [{"status": "ready"}, {"status": "empty"}, {"status": "empty"}, {"status": "empty"}]}
        manager.execute_coordinated_retraction = Mock()
        def sensor_state(_=None):
            # Return triggered twice, then clear for any remaining calls
            states = getattr(sensor_state, "states", [True, True, False])
            if not states:
                return False
            val = states.pop(0)
            sensor_state.states = states
            return val
        sensor_state.states = [True, True, False, False, False]
        manager.get_switch_state = Mock(side_effect=sensor_state)
        manager.reactor.pause = Mock()
        manager.is_filament_path_free = Mock(return_value=True)

        result = manager._cycle_slots_with_sensor_check(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=10,
            retract_speed=5,
            retract_speed_mmmin=600,
            full_unload_length=50,
            sensor_name=SENSOR_TOOLHEAD,
            use_extruder=True,
            sensor_to_parking_length=None,
        )

        self.assertTrue(result)
        instance._smart_unload_slot.assert_called_once_with(0, length=40)
        manager.state.set.assert_called_with("ace_filament_pos", FILAMENT_STATE_BOWDEN)

    def test_ace_only_sensor_clears_and_stops_feed(self):
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set = Mock()
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        manager.instances[0]._info = {"slots": [{"status": "ready"}, {"status": "empty"}, {"status": "empty"}, {"status": "empty"}]}
        def sensor_state(_=None):
            # First call triggered, subsequent calls clear
            first = getattr(sensor_state, "first", True)
            if first:
                sensor_state.first = False
                return True
            return False
        sensor_state.first = True
        manager.get_switch_state = Mock(side_effect=sensor_state)
        manager.is_filament_path_free = Mock(return_value=True)

        # monotonic increments to allow delay to elapse
        t = [0.0]
        def fake_monotonic():
            t[0] += 0.6
            return t[0]
        manager.reactor.monotonic = Mock(side_effect=fake_monotonic)
        manager.reactor.pause = Mock()

        result = manager._cycle_slots_with_sensor_check(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=5,
            retract_speed=5,
            retract_speed_mmmin=300,
            full_unload_length=20,
            sensor_name=SENSOR_RDM,
            use_extruder=False,
            sensor_to_parking_length=5,
        )

        self.assertTrue(result)
        instance._stop_feed.assert_called_once_with(0)
        manager.state.set.assert_called_with("ace_filament_pos", FILAMENT_STATE_BOWDEN)

    def test_ace_only_disables_feed_assist_before_retraction(self):
        """CASE 3: Feed assist must be disabled BEFORE ACE-only retraction."""
        instance = self._make_instance()
        instance._feed_assist_index = 0  # Feed assist active on slot 0
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set_and_save = Mock()
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]

        # Track call order
        call_order = []
        instance._disable_feed_assist.side_effect = lambda s: call_order.append('disable_feed_assist')
        instance._retract.side_effect = lambda *a, **k: call_order.append('retract')

        # Sensor clears immediately so we exit the loop
        manager.get_instant_switch_state = Mock(return_value=False)
        manager.is_filament_path_free = Mock(return_value=True)

        t = [0.0]
        def fake_monotonic():
            t[0] += 0.6
            return t[0]
        manager.reactor.monotonic = Mock(side_effect=fake_monotonic)
        manager.reactor.pause = Mock()

        result = manager._cycle_slots_with_sensor_check(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=5,
            retract_speed=5,
            retract_speed_mmmin=300,
            full_unload_length=20,
            sensor_name=SENSOR_RDM,
            use_extruder=False,
            sensor_to_parking_length=5,
        )

        instance._disable_feed_assist.assert_called_once_with(0)
        self.assertEqual(call_order, ['disable_feed_assist', 'retract'])

    def test_ace_only_skips_feed_assist_disable_when_not_active(self):
        """CASE 3: No disable call when feed assist is not active on tested slot."""
        instance = self._make_instance()
        instance._feed_assist_index = -1  # Not active
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]

        manager.get_instant_switch_state = Mock(return_value=False)
        manager.is_filament_path_free = Mock(return_value=True)

        t = [0.0]
        def fake_monotonic():
            t[0] += 0.6
            return t[0]
        manager.reactor.monotonic = Mock(side_effect=fake_monotonic)
        manager.reactor.pause = Mock()

        manager._cycle_slots_with_sensor_check(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=5,
            retract_speed=5,
            retract_speed_mmmin=300,
            full_unload_length=20,
            sensor_name=SENSOR_RDM,
            use_extruder=False,
            sensor_to_parking_length=5,
        )

        instance._disable_feed_assist.assert_not_called()
        instance._retract.assert_called_once()

    def test_returns_false_when_no_slots_identified(self):
        instance = self._make_instance()
        # Mark all slots empty to skip
        for slot in instance.inventory:
            slot["status"] = "empty"
        manager = self._build_manager(lambda *a, **k: instance)
        manager.is_filament_path_free = Mock(return_value=False)
        manager.get_switch_state = Mock(return_value=True)

        result = manager._cycle_slots_with_sensor_check(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=5,
            retract_speed=5,
            retract_speed_mmmin=300,
            full_unload_length=20,
            sensor_name=SENSOR_TOOLHEAD,
            use_extruder=True,
            sensor_to_parking_length=None,
        )

        self.assertFalse(result)

    def test_prioritizes_current_tool_index(self):
        """Test that current_tool_index is prioritized in slot list when different from attempted."""
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.state.set_and_save = Mock()
        manager.instances[0].inventory = [
            {"status": "ready"},  # T0
            {"status": "ready"},  # T1 - current_tool
            {"status": "ready"},  # T2
            {"status": "empty"},
        ]
        manager.instances[0]._info = {"slots": [{"status": "ready"}, {"status": "ready"}, {"status": "ready"}, {"status": "empty"}]}
        
        # Track which slot is tested first
        slots_tested = []
        original_respond = self.mock_gcode.respond_info
        def track_slot(msg):
            if "Testing slot" in msg and "via" in msg:
                # Extract slot number from message like "Testing slot 1 (T1) via toolhead_sensor"
                parts = msg.split("slot ")[1].split(" ")[0]
                slots_tested.append(int(parts))
            return original_respond(msg)
        self.mock_gcode.respond_info = Mock(side_effect=track_slot)
        
        manager.execute_coordinated_retraction = Mock()
        manager.get_switch_state = Mock(return_value=False)  # Sensor clears immediately
        manager.is_filament_path_free = Mock(return_value=True)
        manager.reactor.pause = Mock()

        result = manager._cycle_slots_with_sensor_check(
            current_tool_index=1,  # T1
            attempted_tool_index=0,  # T0
            retract_length=10,
            retract_speed=5,
            retract_speed_mmmin=600,
            full_unload_length=50,
            sensor_name=SENSOR_TOOLHEAD,
            use_extruder=True,
            sensor_to_parking_length=None,
        )

        self.assertTrue(result)
        # Verify T1 (slot 1) was tested first due to prioritization
        self.assertEqual(slots_tested[0], 1, "Current tool slot 1 should be tested first")
        instance._smart_unload_slot.assert_called_once_with(1, length=40)

    def test_use_extruder_sensor_stays_triggered_cycles_all_slots(self):
        """Test cycling continues when sensor doesn't clear during use_extruder mode."""
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        manager.execute_coordinated_retraction = Mock()
        # Sensor stays triggered for all slots
        manager.get_instant_switch_state = Mock(return_value=True)
        manager.reactor.pause = Mock()
        manager.is_filament_path_free_instant = Mock(return_value=False)

        result = manager._cycle_slots_with_sensor_check(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=10,
            retract_speed=5,
            retract_speed_mmmin=600,
            full_unload_length=50,
            sensor_name=SENSOR_TOOLHEAD,
            use_extruder=True,
            sensor_to_parking_length=None,
        )

        self.assertFalse(result)
        # Should try both ready slots
        self.assertEqual(manager.execute_coordinated_retraction.call_count, 2)

    def test_use_extruder_error_during_retraction_continues_to_next_slot(self):
        """Test error handling during coordinated retraction - continues to next slot."""
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        
        # First slot raises error, second slot succeeds
        call_count = [0]
        def retract_with_error(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Retraction failed")
        manager.execute_coordinated_retraction = Mock(side_effect=retract_with_error)
        
        # Sensor clears on second slot (after error on first)
        # Need to return False (clear) after the successful second retraction
        def sensor_sequence(_):
            # After second successful retraction, sensor should clear
            if call_count[0] >= 2:
                return False
            return True
        manager.get_switch_state = Mock(side_effect=sensor_sequence)
        manager.reactor.pause = Mock()
        manager.is_filament_path_free = Mock(return_value=True)

        result = manager._cycle_slots_with_sensor_check(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=10,
            retract_speed=5,
            retract_speed_mmmin=600,
            full_unload_length=50,
            sensor_name=SENSOR_TOOLHEAD,
            use_extruder=True,
            sensor_to_parking_length=None,
        )

        self.assertTrue(result)
        # First call fails, second succeeds
        self.assertEqual(manager.execute_coordinated_retraction.call_count, 2)
        # Should complete unload for slot 1
        instance._smart_unload_slot.assert_called_once_with(1, length=40)

    def test_use_extruder_error_during_full_unload_returns_false(self):
        """Test error during _smart_unload_slot after identification returns False."""
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        manager.execute_coordinated_retraction = Mock()
        manager.get_switch_state = Mock(return_value=False)  # Sensor clears
        manager.reactor.pause = Mock()
        manager.is_filament_path_free = Mock(return_value=True)
        
        # _smart_unload_slot raises error
        instance._smart_unload_slot.side_effect = Exception("Unload failed")

        result = manager._cycle_slots_with_sensor_check(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=10,
            retract_speed=5,
            retract_speed_mmmin=600,
            full_unload_length=50,
            sensor_name=SENSOR_TOOLHEAD,
            use_extruder=True,
            sensor_to_parking_length=None,
        )

        self.assertFalse(result)
        instance._smart_unload_slot.assert_called_once()

    def test_ace_only_timeout_waiting_for_sensor_change(self):
        """Test timeout scenario in ACE-only mode when sensor never changes."""
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        
        # Sensor always triggered (never clears)
        manager.get_instant_switch_state = Mock(return_value=True)
        
        # Time advances to trigger timeout
        time_val = [0.0]
        def advance_time():
            time_val[0] += 5.0  # Jump 5 seconds each call to trigger timeout
            return time_val[0]
        manager.reactor.monotonic = Mock(side_effect=advance_time)
        manager.reactor.pause = Mock()
        manager.is_filament_path_free_instant = Mock(return_value=False)

        result = manager._cycle_slots_with_sensor_check(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=5,
            retract_speed=5,
            retract_speed_mmmin=300,
            full_unload_length=20,
            sensor_name=SENSOR_RDM,
            use_extruder=False,
            sensor_to_parking_length=5,
        )

        self.assertFalse(result)
        # Verify timeout message was logged
        timeout_logged = any("Timeout waiting for" in str(call) for call in self.mock_gcode.respond_info.call_args_list)
        self.assertTrue(timeout_logged, "Timeout message should be logged")

    def test_ace_only_error_during_retract_continues_to_next_slot(self):
        """Test error during _retract in ACE-only mode continues to next slot."""
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        
        # First _retract raises error, second succeeds
        call_count = [0]
        def retract_with_error(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("ACE retract failed")
        instance._retract.side_effect = retract_with_error
        
        # Sensor clears on second attempt
        sensor_cleared = [False]
        time_val = [0.0]
        def sensor_state(_):
            if call_count[0] >= 2:  # Second slot
                sensor_cleared[0] = True
                return False
            return True
        
        def advance_time():
            time_val[0] += 0.6
            return time_val[0]
        
        manager.get_switch_state = Mock(side_effect=sensor_state)
        manager.reactor.monotonic = Mock(side_effect=advance_time)
        manager.reactor.pause = Mock()
        manager.is_filament_path_free = Mock(return_value=True)

        result = manager._cycle_slots_with_sensor_check(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=5,
            retract_speed=5,
            retract_speed_mmmin=300,
            full_unload_length=20,
            sensor_name=SENSOR_RDM,
            use_extruder=False,
            sensor_to_parking_length=5,
        )

        self.assertTrue(result)
        self.assertEqual(instance._retract.call_count, 2)

    def test_path_blocked_after_successful_identification_returns_false(self):
        """Test that False is returned when path is still blocked after successful unload."""
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        manager.execute_coordinated_retraction = Mock()
        manager.get_switch_state = Mock(return_value=False)  # Sensor clears
        manager.reactor.pause = Mock()
        
        # Path still blocked after unload
        manager.is_filament_path_free = Mock(return_value=False)

        result = manager._cycle_slots_with_sensor_check(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=10,
            retract_speed=5,
            retract_speed_mmmin=600,
            full_unload_length=50,
            sensor_name=SENSOR_TOOLHEAD,
            use_extruder=True,
            sensor_to_parking_length=None,
        )

        self.assertFalse(result)
        instance._smart_unload_slot.assert_called_once()
        # Verify error message logged
        error_logged = any("Path still blocked" in str(call) for call in self.mock_gcode.respond_info.call_args_list)
        self.assertTrue(error_logged, "Path blocked error should be logged")

    def test_skips_invalid_current_tool_instance(self):
        """Test that invalid instance number for current_tool is skipped gracefully."""
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        manager.execute_coordinated_retraction = Mock()
        manager.get_switch_state = Mock(return_value=False)
        manager.reactor.pause = Mock()
        manager.is_filament_path_free = Mock(return_value=True)

        # Use invalid current_tool_index (out of range)
        result = manager._cycle_slots_with_sensor_check(
            current_tool_index=99,  # Invalid tool number
            attempted_tool_index=-1,
            retract_length=10,
            retract_speed=5,
            retract_speed_mmmin=600,
            full_unload_length=50,
            sensor_name=SENSOR_TOOLHEAD,
            use_extruder=True,
            sensor_to_parking_length=None,
        )

        self.assertTrue(result)
        # Should still find and unload slot 0
        instance._smart_unload_slot.assert_called_once_with(0, length=40)

    def test_ace_only_uses_sensor_to_parking_length_for_delay(self):
        """Test ACE-only mode uses sensor_to_parking_length to calculate delay after trigger."""
        instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: instance)
        manager.instances[0].inventory = [
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        
        sensor_cleared = [False]
        def sensor_state(_):
            # Clear after first check
            if not sensor_cleared[0]:
                sensor_cleared[0] = True
                return True
            return False
        manager.get_switch_state = Mock(side_effect=sensor_state)
        
        # Track timing for delay calculation
        time_val = [0.0]
        pause_delays = []
        def advance_time():
            time_val[0] += 0.6
            return time_val[0]
        
        def track_pause(until_time):
            pause_delays.append(until_time - time_val[0])
            return Mock()
        
        manager.reactor.monotonic = Mock(side_effect=advance_time)
        manager.reactor.pause = Mock(side_effect=track_pause)
        manager.is_filament_path_free = Mock(return_value=True)

        result = manager._cycle_slots_with_sensor_check(
            current_tool_index=-1,
            attempted_tool_index=-1,
            retract_length=5,
            retract_speed=10,  # 10 mm/s
            retract_speed_mmmin=600,
            full_unload_length=50,
            sensor_name=SENSOR_RDM,
            use_extruder=False,
            sensor_to_parking_length=20,  # 20mm at 10mm/s = 2 second delay
        )

        self.assertTrue(result)
        instance._stop_feed.assert_called_once()


class TestSetupSensors(unittest.TestCase):
    """Tests for _setup_sensors function."""

    def setUp(self):
        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_gcode = Mock()
        self.mock_reactor = Mock()
        
        self.mock_config.get_printer = Mock(return_value=self.mock_printer)
        self.mock_printer.lookup_object = Mock(side_effect=self._lookup_object_side_effect)
        self.mock_printer.get_reactor = Mock(return_value=self.mock_reactor)
        self.mock_printer.register_event_handler = Mock()
        
        self.variables = {"ace_global_enabled": True}
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = self.variables
        
        self.ace_config = {
            "ace_count": 1,
            "baud": 115200,
            "toolhead_retraction_speed": 10.0,
            "toolhead_retraction_length": 50.0,
            "default_color_change_purge_length": 100.0,
            "default_color_change_purge_speed": 400.0,
            "purge_max_chunk_length": 50.0,
            "pre_cut_retract_length": 5.0,
            "ace_connection_supervision": True,
        }

    def _lookup_object_side_effect(self, obj_name, *args):
        """Mock lookup_object for various printer objects."""
        if obj_name == "gcode":
            return self.mock_gcode
        elif obj_name == "save_variables":
            return self.mock_save_vars
        elif obj_name == "output_pin ACE_Pro":
            mock_pin = Mock()
            mock_pin.get_status = Mock(return_value={"value": 1})
            return mock_pin
        elif obj_name.startswith("filament_switch_sensor"):
            mock_sensor = Mock()
            mock_sensor.runout_helper = Mock()
            mock_sensor.runout_helper.sensor_enabled = True
            mock_sensor.runout_helper.filament_present = False
            return mock_sensor
        return Mock()

    def _make_instance(self):
        """Create mock ACE instance."""
        instance = Mock()
        instance.instance_num = 0
        instance.tool_offset = 0
        instance.SLOT_COUNT = 4
        instance.filament_runout_sensor_name_nozzle = "toolhead_sensor"
        instance.filament_runout_sensor_name_rdm = None
        instance.serial_mgr = Mock()
        instance.serial_mgr.connect_to_ace = Mock()
        instance.serial_mgr.disconnect = Mock()
        instance.inventory = [{"status": "empty"} for _ in range(4)]
        return instance

    def _build_manager(self, instance_factory=None):
        """Build AceManager with mocked dependencies."""
        if instance_factory is None:
            instance_factory = lambda *a, **k: self._make_instance()
        
        with patch('ace.manager.AceInstance', side_effect=instance_factory), \
             patch('ace.manager.EndlessSpool'), \
             patch('ace.manager.RunoutMonitor'), \
             patch('ace.manager.read_ace_config', return_value=self.ace_config):
            return AceManager(self.mock_config, dummy_ace_count=1)

    def test_setup_sensors_registers_toolhead_sensor(self):
        """Test that toolhead sensor is registered correctly."""
        manager = self._build_manager()
        
        # Call _setup_sensors
        manager._setup_sensors()
        
        # Verify toolhead sensor was registered
        self.assertIn(SENSOR_TOOLHEAD, manager.sensors)
        self.assertIsNotNone(manager.sensors[SENSOR_TOOLHEAD])
        
        # Verify previous state was saved
        self.assertIn(SENSOR_TOOLHEAD, manager._prev_sensors_enabled_state)
        self.assertEqual(manager._prev_sensors_enabled_state[SENSOR_TOOLHEAD], True)
        
        # Verify lookup_object was called
        self.mock_printer.lookup_object.assert_any_call(
            "filament_switch_sensor toolhead_sensor"
        )

    def test_setup_sensors_registers_rdm_sensor_when_available(self):
        """Test that RDM sensor is registered when configured."""
        def instance_with_rdm(*a, **k):
            instance = self._make_instance()
            instance.filament_runout_sensor_name_rdm = "rdm_sensor"
            return instance
        
        manager = self._build_manager(instance_with_rdm)
        
        # Call _setup_sensors
        manager._setup_sensors()
        
        # Verify both sensors were registered
        self.assertIn(SENSOR_TOOLHEAD, manager.sensors)
        self.assertIn(SENSOR_RDM, manager.sensors)
        
        # Verify previous states saved
        self.assertIn(SENSOR_TOOLHEAD, manager._prev_sensors_enabled_state)
        self.assertIn(SENSOR_RDM, manager._prev_sensors_enabled_state)
        
        # Verify lookup_object was called for RDM
        self.mock_printer.lookup_object.assert_any_call(
            "filament_switch_sensor rdm_sensor"
        )

    def test_setup_sensors_skips_rdm_when_none(self):
        """Test that RDM sensor is not registered when None."""
        manager = self._build_manager()
        
        # Call _setup_sensors
        manager._setup_sensors()
        
        # Verify only toolhead sensor registered
        self.assertIn(SENSOR_TOOLHEAD, manager.sensors)
        self.assertNotIn(SENSOR_RDM, manager.sensors)

    def test_setup_sensors_raises_on_missing_toolhead_sensor(self):
        """Test that missing toolhead sensor raises config error."""
        def lookup_with_error(obj_name, *args):
            if obj_name == "filament_switch_sensor toolhead_sensor":
                raise Exception("Sensor not found")
            if obj_name == "filament_tracker toolhead_sensor":
                raise Exception("Tracker not found")
            return self._lookup_object_side_effect(obj_name, *args)
        
        self.mock_printer.lookup_object = Mock(side_effect=lookup_with_error)
        manager = self._build_manager()
        
        # Should raise config.error
        with self.assertRaises(Exception):
            manager._setup_sensors()
        
        # Verify error message was logged
        error_calls = [call for call in self.mock_gcode.respond_info.call_args_list 
                      if "No toolhead sensor" in str(call)]
        self.assertGreater(len(error_calls), 0)

    def test_setup_sensors_uses_named_filament_tracker(self):
        """filament_tracker <name> is used when filament_switch_sensor <name> is missing."""
        mock_tracker = Mock()
        mock_tracker.runout_helper = Mock()
        mock_tracker.runout_helper.filament_present = True
        mock_tracker.runout_helper.sensor_enabled = True

        def lookup_fallback(obj_name, *args):
            if obj_name == "filament_switch_sensor toolhead_sensor":
                raise Exception("Sensor not found")
            if obj_name == "filament_tracker toolhead_sensor":
                return mock_tracker
            return self._lookup_object_side_effect(obj_name, *args)

        self.mock_printer.lookup_object = Mock(side_effect=lookup_fallback)
        manager = self._build_manager()

        manager._setup_sensors()

        # Verify toolhead sensor was registered via adapter
        self.assertIn(SENSOR_TOOLHEAD, manager.sensors)
        sensor = manager.sensors[SENSOR_TOOLHEAD]
        self.assertTrue(sensor.filament_present)

        # Verify info message mentions the name and type
        tracker_calls = [call for call in self.mock_gcode.respond_info.call_args_list
                        if "filament_tracker" in str(call)]
        self.assertGreater(len(tracker_calls), 0)

    def test_setup_sensors_continues_on_missing_rdm_sensor(self):
        """Test that missing RDM sensor logs warning but continues."""
        def instance_with_rdm(*a, **k):
            instance = self._make_instance()
            instance.filament_runout_sensor_name_rdm = "rdm_sensor"
            return instance
        
        def lookup_with_rdm_error(obj_name, *args):
            if obj_name == "filament_switch_sensor rdm_sensor":
                raise Exception("RDM sensor not found")
            if obj_name == "filament_tracker rdm_sensor":
                raise Exception("RDM tracker not found")
            return self._lookup_object_side_effect(obj_name, *args)
        
        self.mock_printer.lookup_object = Mock(side_effect=lookup_with_rdm_error)
        manager = self._build_manager(instance_with_rdm)
        
        # Should NOT raise exception
        manager._setup_sensors()
        
        # Verify toolhead sensor was registered
        self.assertIn(SENSOR_TOOLHEAD, manager.sensors)
        
        # Verify RDM sensor was NOT registered
        self.assertNotIn(SENSOR_RDM, manager.sensors)
        
        # Verify warning was logged
        warning_calls = [call for call in self.mock_gcode.respond_info.call_args_list 
                        if "No RDM sensor" in str(call)]
        self.assertGreater(len(warning_calls), 0)

    def test_setup_sensors_uses_named_filament_tracker_for_rdm(self):
        """filament_tracker <name> is used for RDM when filament_switch_sensor is missing."""
        mock_tracker = Mock()
        mock_tracker.runout_helper = Mock()
        mock_tracker.runout_helper.filament_present = False
        mock_tracker.runout_helper.sensor_enabled = True

        def instance_with_rdm(*a, **k):
            instance = self._make_instance()
            instance.filament_runout_sensor_name_rdm = "rdm_sensor"
            return instance

        def lookup_rdm_tracker(obj_name, *args):
            if obj_name == "filament_switch_sensor rdm_sensor":
                raise Exception("RDM sensor not found")
            if obj_name == "filament_tracker rdm_sensor":
                return mock_tracker
            return self._lookup_object_side_effect(obj_name, *args)

        self.mock_printer.lookup_object = Mock(side_effect=lookup_rdm_tracker)
        manager = self._build_manager(instance_with_rdm)

        manager._setup_sensors()

        # Verify RDM sensor was registered via adapter
        self.assertIn(SENSOR_RDM, manager.sensors)

        # Verify info message mentions filament_tracker
        tracker_calls = [call for call in self.mock_gcode.respond_info.call_args_list
                        if "filament_tracker" in str(call) and "RDM" in str(call)]
        self.assertGreater(len(tracker_calls), 0)

    def test_setup_sensors_disables_all_sensors(self):
        """Test that _disable_all_sensor_detection is called."""
        manager = self._build_manager()
        manager._disable_all_sensor_detection = Mock()
        
        manager._setup_sensors()
        
        # Verify disable was called
        manager._disable_all_sensor_detection.assert_called_once()

    def test_setup_sensors_saves_enabled_state_when_disabled(self):
        """Test that previous state is saved even when sensor is disabled."""
        def lookup_disabled_sensor(obj_name, *args):
            if obj_name.startswith("filament_switch_sensor"):
                mock_sensor = Mock()
                mock_sensor.runout_helper = Mock()
                mock_sensor.runout_helper.sensor_enabled = False  # Disabled
                mock_sensor.runout_helper.filament_present = False
                return mock_sensor
            return self._lookup_object_side_effect(obj_name, *args)
        
        self.mock_printer.lookup_object = Mock(side_effect=lookup_disabled_sensor)
        manager = self._build_manager()
        
        manager._setup_sensors()
        
        # Verify disabled state was saved
        self.assertEqual(manager._prev_sensors_enabled_state[SENSOR_TOOLHEAD], False)

    def test_disable_all_sensor_detection_disables_enabled_sensors(self):
        """Test that enabled sensors are disabled."""
        manager = self._build_manager()
        
        # Setup mock sensors
        mock_sensor_enabled = Mock()
        mock_sensor_enabled.sensor_enabled = True
        
        mock_sensor_disabled = Mock()
        mock_sensor_disabled.sensor_enabled = False
        
        manager.sensors = {
            SENSOR_TOOLHEAD: mock_sensor_enabled,
            SENSOR_RDM: mock_sensor_disabled
        }
        
        manager._disable_all_sensor_detection()
        
        # Verify enabled sensor was disabled
        self.assertFalse(mock_sensor_enabled.sensor_enabled)
        
        # Verify already-disabled sensor unchanged
        self.assertFalse(mock_sensor_disabled.sensor_enabled)
        
        # Verify logging
        log_calls = [call for call in self.mock_gcode.respond_info.call_args_list 
                    if "Disabling runout detection" in str(call)]
        self.assertEqual(len(log_calls), 1)

    def test_restore_sensors_restores_previous_state(self):
        """Test that _restore_sensors restores original state."""
        manager = self._build_manager()
        
        # Setup mock sensors
        mock_sensor1 = Mock()
        mock_sensor1.sensor_enabled = False
        
        mock_sensor2 = Mock()
        mock_sensor2.sensor_enabled = False
        
        manager.sensors = {
            SENSOR_TOOLHEAD: mock_sensor1,
            SENSOR_RDM: mock_sensor2
        }
        
        # Set previous states (both were enabled)
        manager._prev_sensors_enabled_state = {
            SENSOR_TOOLHEAD: True,
            SENSOR_RDM: False
        }
        
        manager._restore_sensors()
        
        # Verify states were restored
        self.assertTrue(mock_sensor1.sensor_enabled)
        self.assertFalse(mock_sensor2.sensor_enabled)
        
        # Verify logging
        restore_calls = [call for call in self.mock_gcode.respond_info.call_args_list 
                        if "Restored sensor" in str(call)]
        self.assertEqual(len(restore_calls), 2)


class TestMonitoring(unittest.TestCase):
    """Tests for _start_monitoring and _stop_monitoring functions."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_gcode = Mock()
        self.mock_reactor = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer = Mock(return_value=self.mock_printer)
        self.mock_printer.get_reactor = Mock(return_value=self.mock_reactor)
        self.mock_printer.register_event_handler = Mock()
        
        self.mock_reactor.register_timer = Mock(return_value="timer_handle_123")
        self.mock_reactor.unregister_timer = Mock()
        self.mock_reactor.NOW = 0.0
        
        self.variables = {"ace_global_enabled": True}
        self.mock_save_vars.allVariables = self.variables

        self.ace_config = {
            "ace_count": 1,
            "baud": 115200,
            "toolhead_retraction_speed": 10.0,
            "toolhead_retraction_length": 50.0,
            "default_color_change_purge_length": 100.0,
            "default_color_change_purge_speed": 400.0,
            "purge_max_chunk_length": 50.0,
            "pre_cut_retract_length": 5.0,
            "ace_connection_supervision": True,
        }

        def lookup_object(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={'value': 1})
                return pin
            return default

        self.mock_printer.lookup_object = Mock(side_effect=lookup_object)

    def _make_instance(self):
        """Create mock ACE instance."""
        instance = Mock()
        instance.instance_num = 0
        instance.tool_offset = 0
        instance.SLOT_COUNT = 4
        instance.filament_runout_sensor_name_nozzle = "toolhead_sensor"
        instance.filament_runout_sensor_name_rdm = None
        instance.serial_mgr = Mock()
        instance.serial_mgr.connect_to_ace = Mock()
        instance.serial_mgr.disconnect = Mock()
        instance.inventory = [{"status": "empty"} for _ in range(4)]
        return instance

    def _build_manager(self, instance_factory=None):
        """Build AceManager with mocked dependencies."""
        if instance_factory is None:
            instance_factory = lambda *a, **k: self._make_instance()
        
        with patch('ace.manager.AceInstance', side_effect=instance_factory), \
             patch('ace.manager.EndlessSpool') as mock_endless, \
             patch('ace.manager.RunoutMonitor') as mock_runout, \
             patch('ace.manager.read_ace_config', return_value=self.ace_config):
            manager = AceManager(self.mock_config, dummy_ace_count=1)
            # Replace with fresh mocks to verify calls
            manager.runout_monitor = Mock()
            manager.runout_monitor.start_monitoring = Mock()
            manager.runout_monitor.stop_monitoring = Mock()
            return manager

    def test_start_monitoring_starts_runout_monitor(self):
        """Test that _start_monitoring starts the runout monitor."""
        manager = self._build_manager()
        
        manager._start_monitoring()
        
        manager.runout_monitor.start_monitoring.assert_called_once()

    def test_start_monitoring_registers_ace_state_timer(self):
        """Test that _start_monitoring registers ACE state monitoring timer."""
        manager = self._build_manager()
        
        manager._start_monitoring()
        
        self.mock_reactor.register_timer.assert_called_once_with(
            manager._monitor_ace_state, 
            self.mock_reactor.NOW
        )
        self.assertEqual(manager._ace_state_timer, "timer_handle_123")

    def test_start_monitoring_logs_message(self):
        """Test that _start_monitoring logs startup message."""
        manager = self._build_manager()
        
        manager._start_monitoring()
        
        log_calls = [str(call) for call in self.mock_gcode.respond_info.call_args_list]
        startup_logged = any("Starting ACE support state monitor" in call for call in log_calls)
        self.assertTrue(startup_logged, "Should log ACE state monitor startup")

    def test_stop_monitoring_stops_runout_monitor(self):
        """Test that _stop_monitoring stops the runout monitor."""
        manager = self._build_manager()
        manager._start_monitoring()
        
        manager._stop_monitoring()
        
        manager.runout_monitor.stop_monitoring.assert_called_once()

    def test_stop_monitoring_unregisters_timer(self):
        """Test that _stop_monitoring unregisters the ACE state timer."""
        manager = self._build_manager()
        manager._start_monitoring()
        
        manager._stop_monitoring()
        
        self.mock_reactor.unregister_timer.assert_called_once_with("timer_handle_123")
        self.assertIsNone(manager._ace_state_timer)

    def test_stop_monitoring_handles_no_timer(self):
        """Test that _stop_monitoring handles case when timer doesn't exist."""
        manager = self._build_manager()
        # Don't start monitoring, so no timer exists
        
        # Should not raise exception
        manager._stop_monitoring()
        
        manager.runout_monitor.stop_monitoring.assert_called_once()
        self.mock_reactor.unregister_timer.assert_not_called()

    def test_stop_monitoring_handles_timer_attribute_missing(self):
        """Test that _stop_monitoring handles missing _ace_state_timer attribute."""
        manager = self._build_manager()
        # Remove the attribute if it exists
        if hasattr(manager, "_ace_state_timer"):
            delattr(manager, "_ace_state_timer")
        
        # Should not raise exception
        manager._stop_monitoring()
        
        manager.runout_monitor.stop_monitoring.assert_called_once()

    def test_stop_monitoring_swallows_unregister_exception(self):
        """Test that _stop_monitoring handles unregister_timer exceptions gracefully."""
        manager = self._build_manager()
        manager._start_monitoring()
        
        # Make unregister_timer raise an exception
        self.mock_reactor.unregister_timer.side_effect = Exception("Timer error")
        
        # Should not propagate exception
        manager._stop_monitoring()
        
        # Timer should still be set to None
        self.assertIsNone(manager._ace_state_timer)

    def test_stop_monitoring_only_unregisters_if_timer_exists(self):
        """Test that _stop_monitoring only attempts unregister if timer is set."""
        manager = self._build_manager()
        manager._ace_state_timer = None
        
        manager._stop_monitoring()
        
        # Should not attempt to unregister None timer
        self.mock_reactor.unregister_timer.assert_not_called()

    def test_start_then_stop_monitoring_lifecycle(self):
        """Test complete start/stop lifecycle."""
        manager = self._build_manager()
        
        # Start
        manager._start_monitoring()
        self.assertIsNotNone(manager._ace_state_timer)
        manager.runout_monitor.start_monitoring.assert_called_once()
        
        # Stop
        manager._stop_monitoring()
        self.assertIsNone(manager._ace_state_timer)
        manager.runout_monitor.stop_monitoring.assert_called_once()


if __name__ == '__main__':
    unittest.main()
