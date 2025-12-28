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
    @patch('ace.manager.set_and_save_variable')
    def test_set_ace_global_enabled(self, mock_set_var, mock_endless_spool, mock_ace_instance):
        """Test setting global enabled state."""
        manager = AceManager(self.mock_config)
        
        manager.set_ace_global_enabled(False)
        
        mock_set_var.assert_called_with(
            self.mock_printer,
            self.mock_gcode,
            'ace_global_enabled',
            False
        )
        self.assertFalse(manager._ace_pro_enabled)


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
        mock_get_ace.return_value = (mock_instance, 1)
        
        # Reselect same tool
        result = manager.perform_tool_change(current_tool=1, target_tool=1)
        
        self.assertIn("already loaded", result)
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
        self.variables['ace_filament_pos'] = FILAMENT_STATE_SPLITTER
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
    def test_skip_unload_at_splitter(self, mock_endless_spool, mock_ace_instance):
        """Test skipping unload when filament already at splitter."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_SPLITTER
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
        
        with self.assertRaises(Exception) as context:
            manager.perform_tool_change(current_tool=-1, target_tool=2)
        
        self.assertIn("not ready", str(context.exception))

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
        
        with patch('ace.manager.get_ace_instance_and_slot_for_tool') as mock_get_ace:
            mock_get_ace.return_value = (None, -1)  # Tool not managed
            
            with self.assertRaises(Exception) as context:
                manager.perform_tool_change(current_tool=-1, target_tool=99)
            
            self.assertIn("not managed", str(context.exception))

    @patch('ace.manager.AceInstance')
    @patch('ace.manager.EndlessSpool')
    def test_unload_failure_raises_exception(self, mock_endless_spool, mock_ace_instance):
        """Test exception raised when unload fails."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_NOZZLE
        manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
        manager.smart_unload = Mock(return_value=False)  # Unload fails!
        
        with self.assertRaises(Exception) as context:
            manager.perform_tool_change(current_tool=1, target_tool=2)
        
        self.assertIn("Failed to unload", str(context.exception))

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
    def test_plausibility_mismatch_unload_fails_raises(self, mock_endless_spool, mock_ace_instance):
        """Test plausibility check failure when smart_unload fails."""
        manager = AceManager(self.mock_config)
        self.variables['ace_filament_pos'] = FILAMENT_STATE_SPLITTER
        # Sensors show filament but state says splitter - plausibility mismatch
        manager._sensor_override = {SENSOR_TOOLHEAD: True, SENSOR_RDM: False}
        manager.smart_unload = Mock(return_value=False)  # Unload fails!
        
        with self.assertRaises(Exception) as context:
            manager.perform_tool_change(current_tool=-1, target_tool=1)
        
        self.assertIn("Failed to clear filament path", str(context.exception))

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


if __name__ == '__main__':
    unittest.main()
