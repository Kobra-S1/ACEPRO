"""
Test suite for ace.instance module.

Tests AceInstance class which manages a single physical ACE Pro unit:
- Instance initialization and configuration
- Serial communication (feed/retract/stop operations)
- Feed assist enable/disable
- Inventory management
- Sensor-aware unload operations
- Status callbacks and heartbeat handling
"""

import json
import unittest
from unittest.mock import Mock, patch, PropertyMock, call, ANY
import time

from ace.instance import AceInstance
from ace.config import (
    ACE_INSTANCES,
    INSTANCE_MANAGERS,
    SLOTS_PER_ACE,
    SENSOR_TOOLHEAD,
    SENSOR_RDM,
    FILAMENT_STATE_BOWDEN,
    FILAMENT_STATE_TOOLHEAD,
    FILAMENT_STATE_NOZZLE,
    FILAMENT_STATE_SPLITTER,
    RFID_STATE_NO_INFO,
    RFID_STATE_IDENTIFIED,
    create_inventory,
)


class TestAceInstance(unittest.TestCase):
    """Test AceInstance initialization and basic operations."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        
        self.variables = {}
        self.mock_save_vars.allVariables = self.variables
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
            'status_debug_logging': True,
        }

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        return default

    @patch('ace.instance.AceSerialManager')
    def test_instance_initialization(self, mock_serial_mgr_class):
        """Test AceInstance initializes with correct parameters."""
        instance = AceInstance(0, self.ace_config, self.mock_printer, ace_enabled=True)
        
        # Verify basic attributes
        self.assertEqual(instance.instance_num, 0)
        self.assertEqual(instance.SLOT_COUNT, SLOTS_PER_ACE)
        self.assertEqual(instance.baud, 115200)
        self.assertEqual(instance.feed_speed, 100.0)
        self.assertEqual(instance.retract_speed, 100.0)
        self.assertEqual(instance.tool_offset, 0)
        
        # Verify serial manager was created
        mock_serial_mgr_class.assert_called_once()
        
        # Verify inventory initialized
        self.assertEqual(len(instance.inventory), SLOTS_PER_ACE)
        for slot in instance.inventory:
            self.assertEqual(slot['status'], 'empty')

    @patch('ace.instance.AceSerialManager')
    def test_instance_second_unit(self, mock_serial_mgr_class):
        """Test second instance has correct tool offset."""
        instance = AceInstance(1, self.ace_config, self.mock_printer)
        
        self.assertEqual(instance.instance_num, 1)
        self.assertEqual(instance.tool_offset, 4)  # Second unit starts at T4

    @patch('ace.instance.AceSerialManager')
    def test_send_request(self, mock_serial_mgr_class):
        """Test send_request delegates to serial manager."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        mock_serial = instance.serial_mgr
        
        request = {'method': 'get_status'}
        callback = Mock()
        
        instance.send_request(request, callback)
        
        mock_serial.send_request.assert_called_once_with(request, callback)

    @patch('ace.instance.AceSerialManager')
    def test_send_high_prio_request(self, mock_serial_mgr_class):
        """Test high priority request delegates to serial manager."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        mock_serial = instance.serial_mgr
        
        request = {'method': 'stop_feed', 'params': {'index': 0}}
        callback = Mock()
        
        instance.send_high_prio_request(request, callback)
        
        mock_serial.send_high_prio_request.assert_called_once_with(request, callback)


class TestRegisterToolMacros(unittest.TestCase):
    """Test _register_tool_macros success and failure paths."""

    def setUp(self):
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)
        self.mock_reactor.monotonic.return_value = 0.0

        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
            'status_debug_logging': True,
        }

    @patch('ace.instance.AceSerialManager')
    def test_registers_all_tool_macros(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        self.mock_gcode.register_command = Mock()
        self.mock_gcode.respond_info = Mock()

        instance._register_tool_macros()

        # Expect one register per slot
        self.assertEqual(self.mock_gcode.register_command.call_count, SLOTS_PER_ACE)
        registered_commands = [c.args[0] for c in self.mock_gcode.register_command.call_args_list]
        self.assertEqual(registered_commands, ['T0', 'T1', 'T2', 'T3'])
        # Success log emitted
        success_msgs = [c.args[0] for c in self.mock_gcode.respond_info.call_args_list]
        self.assertTrue(any("Registered tool macros T0-T3" in str(m) for m in success_msgs))

    @patch('ace.instance.AceSerialManager')
    def test_logs_failure_on_exception(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        self.mock_gcode.register_command = Mock(side_effect=RuntimeError("boom"))
        self.mock_gcode.respond_info = Mock()

        instance._register_tool_macros()

        self.mock_gcode.register_command.assert_called_once()  # stopped after failure
        failure_msgs = [c.args[0] for c in self.mock_gcode.respond_info.call_args_list]
        self.assertTrue(any("Failed to register tool macros" in str(m) for m in failure_msgs))


class TestWaitReady(unittest.TestCase):
    """Branch coverage for wait_ready."""

    def setUp(self):
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.pause = Mock()

        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
            'status_debug_logging': True,
        }

    @patch('ace.instance.AceSerialManager')
    def test_wait_ready_returns_when_ready(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._info['status'] = 'ready'

        instance.wait_ready()

        self.mock_reactor.pause.assert_not_called()

    @patch('ace.instance.AceSerialManager')
    def test_wait_ready_times_out(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._info['status'] = 'not_ready'

        # advance time to exceed timeout
        with patch('ace.instance.time.time', side_effect=[0, 30, 61]):
            with self.assertRaises(TimeoutError):
                instance.wait_ready(timeout_s=1.0)

    @patch('ace.instance.AceSerialManager')
    def test_wait_ready_calls_on_wait_cycle_and_triggers_status_request(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._info['status'] = 'not_ready'
        instance.send_high_prio_request = Mock()

        call_counter = {'count': 0}

        def on_wait():
            call_counter['count'] += 1
            # make ready after enough cycles to trigger status refresh
            if call_counter['count'] >= 60:
                instance._info['status'] = 'ready'

        instance.wait_ready(on_wait_cycle=on_wait, timeout_s=60.0)

        self.assertGreaterEqual(call_counter['count'], 60)
        # Should have asked for status once when waited >= 25s
        instance.send_high_prio_request.assert_called()


class TestIsSlotEmpty(unittest.TestCase):
    """Branch coverage for _is_slot_empty."""

    def setUp(self):
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.pause = Mock()

        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    @patch('ace.instance.AceSerialManager')
    def test_slot_empty_true(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._info['slots'] = [{'index': 1, 'status': 'empty'}]

        self.assertTrue(instance._is_slot_empty(1))

    @patch('ace.instance.AceSerialManager')
    def test_slot_empty_false(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._info['slots'] = [{'index': 2, 'status': 'ready'}]

        self.assertFalse(instance._is_slot_empty(2))

    @patch('ace.instance.AceSerialManager')
    def test_slot_empty_missing_entry(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._info['slots'] = [{'index': 0, 'status': 'empty'}]

        self.assertFalse(instance._is_slot_empty(3))


class TestRetract(unittest.TestCase):
    """Branch coverage for _retract."""

    def setUp(self):
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.pause = Mock()

        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    def _time_generator(self, start=0.0, step=0.5):
        current = start
        while True:
            current += step
            yield current

    @patch('ace.instance.AceSerialManager')
    def test_retract_skips_when_slot_empty(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._info['slots'] = [{'index': 1, 'status': 'empty'}]
        instance.wait_ready = Mock()
        instance.serial_mgr.send_request = Mock()

        result = instance._retract(1, length=10, speed=5)

        self.assertEqual(result, {"code": 0, "msg": "Retract skipped: slot empty"})
        instance.wait_ready.assert_called_once()
        instance.serial_mgr.send_request.assert_not_called()

    @patch('ace.instance.AceSerialManager')
    def test_retract_success_with_callbacks(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._info['slots'] = [{'index': 0, 'status': 'ready'}]
        instance.wait_ready = Mock()
        instance.reactor.pause = Mock()
        instance.serial_mgr.send_request = Mock(side_effect=lambda req, cb: cb({"code": 0, "msg": "ok"}))

        on_retract_started = Mock()
        on_wait_for_ready = Mock()

        times = self._time_generator(step=1.0)
        with patch('ace.instance.time.time', side_effect=lambda: next(times)):
            result = instance._retract(0, length=2, speed=1, on_retract_started=on_retract_started, on_wait_for_ready=on_wait_for_ready)

        self.assertEqual(result, {"code": 0, "msg": "ok"})
        on_retract_started.assert_called_once()
        self.assertTrue(on_wait_for_ready.call_count > 0)
        instance.serial_mgr.send_request.assert_called_once()
        # wait_ready called at least twice: initial + post dwell
        self.assertTrue(instance.wait_ready.call_count >= 2)

    @patch('ace.instance.AceSerialManager')
    def test_retract_retries_on_missing_response(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._info['slots'] = [{'index': 0, 'status': 'ready'}]
        instance.wait_ready = Mock()
        instance.reactor.pause = Mock()

        responses = [None, {"code": 0, "msg": "ok"}]

        def send_request(req, cb):
            resp = responses.pop(0)
            if resp is not None:
                cb(resp)

        instance.serial_mgr.send_request = Mock(side_effect=send_request)

        times = self._time_generator(step=3.0)  # fast-forward to exceed first timeout
        with patch('ace.instance.time.time', side_effect=lambda: next(times)):
            result = instance._retract(0, length=1, speed=1)

        self.assertEqual(result, {"code": 0, "msg": "ok"})
        self.assertEqual(instance.serial_mgr.send_request.call_count, 2)
        # Retry path should pause between attempts
        self.assertTrue(instance.reactor.pause.call_count >= 1)

    @patch('ace.instance.AceSerialManager')
    def test_retract_stops_early_when_slot_becomes_empty(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        # Simulate slot becomes empty during retract
        instance._is_slot_empty = Mock(side_effect=[False, False, True, True])
        instance.wait_ready = Mock()
        instance.reactor.pause = Mock()
        instance._stop_retract = Mock()
        instance.serial_mgr.send_request = Mock(side_effect=lambda req, cb: cb({"code": 0, "msg": "ok"}))

        times = self._time_generator(step=0.5)
        with patch('ace.instance.time.time', side_effect=lambda: next(times)):
            result = instance._retract(0, length=2, speed=1)

        self.assertEqual(result, {"code": 0, "msg": "Retract stopped early: slot empty"})
        instance._stop_retract.assert_called_once_with(0)
        self.assertTrue(instance.wait_ready.call_count >= 2)


class TestFeedFilamentIntoToolhead(unittest.TestCase):
    """Branch coverage for _feed_filament_into_toolhead."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
            'toolhead': Mock(wait_moves=Mock()),
        }.get(name, default)
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.pause = Mock()

        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    @patch('ace.instance.AceSerialManager')
    def test_successful_feed_sets_positions_and_moves_extruder(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = False
        manager.get_switch_state.return_value = False
        INSTANCE_MANAGERS[0] = manager

        instance.wait_ready = Mock()
        instance._feed_to_toolhead_with_extruder_assist = Mock(return_value=None)
        instance._extruder_move = Mock()

        result = instance._feed_filament_into_toolhead(1)

        self.assertEqual(result, instance.toolhead_full_purge_length)
        instance._feed_to_toolhead_with_extruder_assist.assert_called_once()
        instance._extruder_move.assert_called_once_with(
            instance.toolhead_full_purge_length,
            instance.toolhead_slow_loading_speed
        )
        # Toolhead wait_moves called once
        toolhead = self.mock_printer.lookup_object('toolhead')
        toolhead.wait_moves.assert_called_once()
        # Two position updates: toolhead (set) then nozzle (set)
        manager.state.set.assert_any_call("ace_filament_pos", FILAMENT_STATE_TOOLHEAD)
        manager.state.set.assert_any_call("ace_filament_pos", FILAMENT_STATE_NOZZLE)
        # G92 command sent
        self.mock_gcode.run_script_from_command.assert_called_once_with("G92 E0")

    @patch('ace.instance.AceSerialManager')
    def test_precondition_blocks_on_rdm_sensor(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = True
        manager.get_switch_state.side_effect = [True]  # RDM triggered
        INSTANCE_MANAGERS[0] = manager

        with self.assertRaises(ValueError) as ctx:
            instance._feed_filament_into_toolhead(0, check_pre_condition=True)

        self.assertIn("stuck in RMS", str(ctx.exception))
        manager.state.set_and_save.assert_not_called()

    @patch('ace.instance.AceSerialManager')
    def test_feed_exception_triggers_retract_and_reraises(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = False
        manager.get_switch_state.return_value = False
        INSTANCE_MANAGERS[0] = manager

        instance.wait_ready = Mock()
        instance._feed_to_toolhead_with_extruder_assist = Mock(side_effect=ValueError("fail"))
        instance._retract = Mock()
        instance._extruder_move = Mock()

        with self.assertRaises(ValueError):
            instance._feed_filament_into_toolhead(0)

        instance._retract.assert_called_once_with(0, 150, instance.retract_speed)
        manager.state.set_and_save.assert_not_called()

    @patch('ace.instance.AceSerialManager')
    def test_precondition_skipped_when_flag_false(self, mock_serial_mgr_class):
        """Sensors ignored when check_pre_condition is False."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = True
        manager.get_switch_state = Mock(return_value=True)  # would block if checked
        INSTANCE_MANAGERS[0] = manager

        instance.wait_ready = Mock()
        instance._feed_to_toolhead_with_extruder_assist = Mock(return_value=None)
        instance._extruder_move = Mock()

        result = instance._feed_filament_into_toolhead(0, check_pre_condition=False)

        self.assertEqual(result, instance.toolhead_full_purge_length)
        manager.get_switch_state.assert_not_called()
        manager.state.set.assert_called_once_with("ace_filament_pos", FILAMENT_STATE_TOOLHEAD)
        manager.state.set_and_save.assert_called_once_with("ace_filament_pos", FILAMENT_STATE_NOZZLE)

    @patch('ace.instance.AceSerialManager')
    def test_precondition_blocks_toolhead_when_rdm_available(self, mock_serial_mgr_class):
        """Toolhead sensor triggers error even when RDM available and clear."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = True
        manager.get_switch_state.side_effect = [False, True]  # RDM clear, toolhead triggered
        INSTANCE_MANAGERS[0] = manager

        with self.assertRaises(ValueError) as ctx:
            instance._feed_filament_into_toolhead(0, check_pre_condition=True)

        self.assertIn("filament in nozzle", str(ctx.exception).lower())
        manager.state.set_and_save.assert_not_called()
        self.assertEqual(manager.get_switch_state.call_args_list, [call(SENSOR_RDM), call(SENSOR_TOOLHEAD)])

    @patch('ace.instance.AceSerialManager')
    def test_success_with_rdm_available_checks_both_sensors(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = True
        manager.get_switch_state.side_effect = [False, False]  # RDM clear, toolhead clear
        INSTANCE_MANAGERS[0] = manager

        instance.wait_ready = Mock()
        instance._feed_to_toolhead_with_extruder_assist = Mock(return_value=None)
        instance._extruder_move = Mock()

        instance._feed_filament_into_toolhead(2, check_pre_condition=True)

        # Both sensors should be checked in order when RDM available
        self.assertEqual(manager.get_switch_state.call_args_list, [call(SENSOR_RDM), call(SENSOR_TOOLHEAD)])

    @patch('ace.instance.AceSerialManager')
    def test_rdm_available_does_not_record_splitter_state(self, mock_serial_mgr_class):
        """Feeding to toolhead must not write splitter state even when RDM exists."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = True
        manager.get_switch_state.side_effect = [False, False]  # RDM clear, toolhead clear
        INSTANCE_MANAGERS[0] = manager

        instance.wait_ready = Mock()
        instance._feed_to_toolhead_with_extruder_assist = Mock(return_value=None)
        instance._extruder_move = Mock()

        instance._feed_filament_into_toolhead(1, check_pre_condition=True)

        # Only toolhead and nozzle states should be recorded
        recorded_states = (
            [c.args[1] for c in manager.state.set.call_args_list]
            + [c.args[1] for c in manager.state.set_and_save.call_args_list]
        )
        assert FILAMENT_STATE_TOOLHEAD in recorded_states
        assert FILAMENT_STATE_NOZZLE in recorded_states
        assert FILAMENT_STATE_SPLITTER not in recorded_states


class TestFeedAndStopHelpers(unittest.TestCase):
    """Branch coverage for feed/stop helpers and related utilities."""

    def setUp(self):
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.pause = Mock()

        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    @patch('ace.instance.AceSerialManager')
    def test_feed_default_callback_handles_forbidden_and_error(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        responses = [
            {"code": 0, "msg": "FORBIDDEN"},
            {"code": 5, "msg": "bad"},
        ]

        def send_request(req, cb):
            cb(responses.pop(0))

        instance.send_request = Mock(side_effect=send_request)
        instance._feed(1, 10, 5)  # FORBIDDEN
        instance._feed(1, 10, 5)  # error code

        # Two calls to respond_info for the errors + send request logs
        error_msgs = [str(c.args[0]) for c in self.mock_gcode.respond_info.call_args_list]
        self.assertTrue(any("Feed forbidden" in msg for msg in error_msgs))
        self.assertTrue(any("Feed error" in msg for msg in error_msgs))

    @patch('ace.instance.AceSerialManager')
    def test_stop_feed_success_and_error(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        responses = [
            {"code": 1, "msg": "err"},
            {"code": 0, "msg": "ok"},
        ]

        def send_high(req, cb):
            cb(responses.pop(0))

        instance.send_high_prio_request = Mock(side_effect=send_high)

        instance._stop_feed(0)  # error
        instance._stop_feed(0)  # success

        msgs = [str(c.args[0]) for c in self.mock_gcode.respond_info.call_args_list]
        self.assertTrue(any("Stop feed error" in m for m in msgs))
        self.assertTrue(any("Stop feed successful" in m for m in msgs))

    @patch('ace.instance.AceSerialManager')
    def test_stop_retract_success_and_error(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        responses = [
            {"code": 2, "msg": "oops"},
            {"code": 0, "msg": "ok"},
        ]

        def send_request(req, cb):
            cb(responses.pop(0))

        instance.send_request = Mock(side_effect=send_request)

        instance._stop_retract(1)
        instance._stop_retract(1)

        msgs = [str(c.args[0]) for c in self.mock_gcode.respond_info.call_args_list]
        self.assertTrue(any("Stop retract error" in m for m in msgs))
        self.assertTrue(any("Stop retract successful" in m for m in msgs))

    @patch('ace.instance.AceSerialManager')
    def test_change_feed_speed_branches(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)

        # Success path
        instance.send_request = Mock(side_effect=lambda req, cb: cb({"code": 0, "msg": "ok"}))
        self.assertTrue(instance._change_feed_speed(0, 20))

        # Failure code
        instance.send_request = Mock(side_effect=lambda req, cb: cb({"code": 1, "msg": "bad"}))
        self.assertFalse(instance._change_feed_speed(0, 20))

        # No response path
        instance.send_request = Mock()  # never calls back
        self.assertFalse(instance._change_feed_speed(0, 20))

        # Exception path
        def raise_err(req, cb):
            raise RuntimeError("boom")
        instance.send_request = Mock(side_effect=raise_err)
        self.assertFalse(instance._change_feed_speed(0, 20))

    @patch('ace.instance.AceSerialManager')
    def test_make_sensor_trigger_monitor_records_timing(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.get_switch_state.side_effect = [False, False, True]
        INSTANCE_MANAGERS[0] = manager
        instance.manager  # property uses INSTANCE_MANAGERS

        times = [0.0]
        def fake_time():
            times[0] += 0.5
            return times[0]

        monitor = instance._make_sensor_trigger_monitor(SENSOR_TOOLHEAD)
        with patch('ace.instance.time.time', side_effect=fake_time):
            monitor()
            monitor()
            monitor()

        self.assertEqual(monitor.get_call_count(), 3)
        self.assertAlmostEqual(monitor.get_timing(), 0.5, places=3)

    @patch('ace.instance.AceSerialManager')
    def test_change_retract_speed_branches(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)

        # Success
        instance.send_request = Mock(side_effect=lambda req, cb: cb({"code": 0, "msg": "ok"}))
        self.assertTrue(instance._change_retract_speed(0, 15))

        # Failure code
        instance.send_request = Mock(side_effect=lambda req, cb: cb({"code": 2, "msg": "bad"}))
        self.assertFalse(instance._change_retract_speed(0, 15))

        # No response
        instance.send_request = Mock()  # never invokes callback
        self.assertFalse(instance._change_retract_speed(0, 15))

        # Exception path
        def raise_err(req, cb):
            raise RuntimeError("boom")
        instance.send_request = Mock(side_effect=raise_err)
        self.assertFalse(instance._change_retract_speed(0, 15))
class TestFeedAssist(unittest.TestCase):
    """Test feed assist enable/disable functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        
        self.variables = {}
        self.mock_save_vars.allVariables = self.variables
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        return default

    @patch('ace.instance.AceSerialManager')
    def test_get_current_feed_assist_index_initial(self, mock_serial_mgr_class):
        """Test feed assist index starts at -1 (disabled)."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        self.assertEqual(instance._get_current_feed_assist_index(), -1)

    @patch('ace.instance.AceSerialManager')
    def test_update_feed_assist_enable(self, mock_serial_mgr_class):
        """Test _update_feed_assist enables for valid slot."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._info['status'] = 'ready'
        
        # Mock serial response
        instance.serial_mgr.send_request = Mock(
            side_effect=lambda req, cb: cb({'code': 0})
        )
        
        instance._update_feed_assist(2)
        
        self.assertEqual(instance._get_current_feed_assist_index(), 2)

    @patch('ace.instance.AceSerialManager')
    def test_update_feed_assist_disable(self, mock_serial_mgr_class):
        """Test _update_feed_assist disables for slot -1."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._feed_assist_index = 2
        
        # Mock serial response
        instance.serial_mgr.send_request = Mock(
            side_effect=lambda req, cb: cb({'code': 0})
        )
        
        instance._update_feed_assist(-1)
        
        # Note: _disable_feed_assist only works if current index matches
        # Since we're testing with index 2 active, passing -1 won't match
        # This tests the branching logic

    @patch('ace.instance.AceSerialManager')
    def test_on_ace_connect_defers_feed_assist_restore(self, mock_serial_mgr_class):
        """Test feed assist restoration is deferred until first heartbeat."""
        # Setup
        self.ace_config['feed_assist_active_after_ace_connect'] = True
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._feed_assist_index = 2  # Feed assist was active on slot 2
        
        # Track send_request calls
        sent_requests = []
        instance.serial_mgr.send_request = Mock(
            side_effect=lambda req, cb: sent_requests.append(req)
        )
        
        # Call _on_ace_connect
        instance._on_ace_connect()
        
        # Verify no request sent yet - just pending
        self.assertEqual(len(sent_requests), 0)
        self.assertEqual(instance._pending_feed_assist_restore, 2)

    @patch('ace.instance.AceSerialManager')
    def test_on_ace_connect_restores_feed_assist_after_heartbeat(self, mock_serial_mgr_class):
        """Test feed assist is restored after first successful heartbeat."""
        # Setup
        self.ace_config['feed_assist_active_after_ace_connect'] = True
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._feed_assist_index = 2  # Feed assist was active on slot 2
        
        # Pre-populate inventory to prevent RFID queries during test
        for i in range(4):
            instance.inventory[i]["status"] = "empty"
        
        # Track send_request calls
        sent_requests = []
        instance.serial_mgr.send_request = Mock(
            side_effect=lambda req, cb: sent_requests.append(req)
        )
        
        # Call _on_ace_connect to set pending restore
        instance._on_ace_connect()
        self.assertEqual(instance._pending_feed_assist_restore, 2)
        
        # Simulate first successful heartbeat (all empty slots to avoid RFID queries)
        heartbeat_response = {
            "code": 0,
            "result": {
                "status": "ready",
                "slots": [
                    {"index": 0, "status": "empty"},
                    {"index": 1, "status": "empty"},
                    {"index": 2, "status": "empty"},
                    {"index": 3, "status": "empty"},
                ]
            }
        }
        instance._on_heartbeat_response(heartbeat_response)
        
        # Now feed assist should be restored plus 4 RFID queries (one per slot)
        self.assertEqual(len(sent_requests), 5)
        # First 4 requests should be RFID queries
        for i in range(4):
            self.assertEqual(sent_requests[i]['method'], 'get_filament_info')
            self.assertEqual(sent_requests[i]['params']['index'], i)
        # Last request should be feed assist restore
        self.assertEqual(sent_requests[4]['method'], 'start_feed_assist')
        self.assertEqual(sent_requests[4]['params']['index'], 2)
        # Pending should be cleared
        self.assertEqual(instance._pending_feed_assist_restore, -1)

    @patch('ace.instance.AceSerialManager')
    def test_on_ace_connect_skips_when_disabled(self, mock_serial_mgr_class):
        """Test feed assist restoration is skipped when config disabled."""
        # Setup
        self.ace_config['feed_assist_active_after_ace_connect'] = False
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._feed_assist_index = 2  # Feed assist was active
        
        # Track send_request calls
        sent_requests = []
        instance.serial_mgr.send_request = Mock(
            side_effect=lambda req, cb: sent_requests.append(req)
        )
        
        # Call _on_ace_connect
        instance._on_ace_connect()
        
        # Verify no requests were sent
        self.assertEqual(len(sent_requests), 0)

    @patch('ace.instance.AceSerialManager')
    def test_on_ace_connect_skips_when_no_previous_feed_assist(self, mock_serial_mgr_class):
        """Test feed assist restoration is skipped when no previous feed assist."""
        # Setup
        self.ace_config['feed_assist_active_after_ace_connect'] = True
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._feed_assist_index = -1  # No feed assist was active
        
        # Track send_request calls
        sent_requests = []
        instance.serial_mgr.send_request = Mock(
            side_effect=lambda req, cb: sent_requests.append(req)
        )
        
        # Call _on_ace_connect
        instance._on_ace_connect()
        
        # Verify no requests were sent
        self.assertEqual(len(sent_requests), 0)

    @patch('ace.instance.AceSerialManager')
    def test_enable_feed_assist_success(self, mock_serial_mgr_class):
        """Enable feed assist stores index and persists variable on success."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        instance._info['status'] = 'ready'
        instance.serial_mgr.get_usb_topology_position = Mock(return_value=7)
        instance.send_request = Mock(side_effect=lambda req, cb: cb({'code': 0}))
        instance.wait_ready = Mock()

        instance._enable_feed_assist(2)

        self.assertEqual(instance._feed_assist_index, 2)
        instance.serial_mgr.get_usb_topology_position.assert_called_once()
        instance.send_request.assert_called_once_with(
            {"method": "start_feed_assist", "params": {"index": 2}},
            ANY
        )
        INSTANCE_MANAGERS[0].state.set.assert_called_once_with(
            "ace_feed_assist_index_0",
            2
        )
        self.assertEqual(instance.wait_ready.call_count, 2)

    @patch('ace.instance.AceSerialManager')
    def test_enable_feed_assist_logs_failure_without_persist(self, mock_serial_mgr_class):
        """Failure path should not persist feed assist variable."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        instance._info['status'] = 'ready'
        instance.serial_mgr.get_usb_topology_position = Mock(return_value=3)
        instance.send_request = Mock(side_effect=lambda req, cb: cb({'code': 1, 'msg': 'oops'}))
        instance.wait_ready = Mock()

        instance._enable_feed_assist(1)

        self.assertEqual(instance._feed_assist_index, 1)
        instance.serial_mgr.get_usb_topology_position.assert_called_once()
        INSTANCE_MANAGERS[0].state.set.assert_not_called()
        self.assertEqual(instance.wait_ready.call_count, 2)

    @patch('ace.instance.AceSerialManager')
    def test_disable_feed_assist_happy_path(self, mock_serial_mgr_class):
        """Disable feed assist when active calls stop and persists -1."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        instance._feed_assist_index = 1
        instance.serial_mgr.get_usb_topology_position = Mock(return_value=5)
        instance.send_request = Mock(side_effect=lambda req, cb: cb({'code': 0}))
        instance.wait_ready = Mock()
        instance.dwell = Mock()

        instance._disable_feed_assist(1)

        self.assertEqual(instance._feed_assist_index, -1)
        self.assertIsNone(instance._feed_assist_topology_position)
        instance.wait_ready.assert_called()
        instance.send_request.assert_called_once_with(
            {"method": "stop_feed_assist", "params": {"index": 1}},
            ANY
        )
        INSTANCE_MANAGERS[0].state.set.assert_called_once_with(
            "ace_feed_assist_index_0",
            -1
        )

    @patch('ace.instance.AceSerialManager')
    def test_disable_feed_assist_mismatched_slot_noop(self, mock_serial_mgr_class):
        """Disable on wrong slot logs warning and returns without sending."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        instance._feed_assist_index = 2
        instance.send_request = Mock()
        instance.wait_ready = Mock()

        instance._disable_feed_assist(-1)

        instance.send_request.assert_not_called()
        INSTANCE_MANAGERS[0].state.set_and_save.assert_not_called()


class TestInventoryManagement(unittest.TestCase):
    """Test inventory operations."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        
        self.variables = {}
        self.mock_save_vars.allVariables = self.variables
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        return default

    @patch('ace.instance.AceSerialManager')
    def test_reset_persistent_inventory(self, mock_serial_mgr_class):
        """Test resetting inventory to empty state."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Set some inventory data
        instance.inventory[0] = {
            'status': 'ready',
            'color': [255, 0, 0],
            'material': 'PLA',
            'temp': 210
        }
        
        instance.reset_persistent_inventory()
        
        # Verify all slots are empty
        for slot in instance.inventory:
            self.assertEqual(slot['status'], 'empty')
            self.assertEqual(slot['color'], [0, 0, 0])
            self.assertEqual(slot['material'], '')
            self.assertEqual(slot['temp'], 0)

    @patch('ace.instance.AceSerialManager')
    def test_reset_feed_assist_state(self, mock_serial_mgr_class):
        """Test resetting feed assist state."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._feed_assist_index = 3
        
        instance.reset_feed_assist_state()
        
        self.assertEqual(instance._feed_assist_index, -1)


class TestStatusCallbacks(unittest.TestCase):
    """Test status update and heartbeat callbacks."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        
        self.variables = {}
        self.mock_save_vars.allVariables = self.variables
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        return default

    @patch('ace.instance.AceSerialManager')
    def test_status_update_rfid_sync_enabled_updates_inventory(self, mock_serial_mgr_class):
        """RFID data populates material with gray placeholder color (actual color from callback)."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        response = {
            'result': {
                'slots': [
                    {
                        'index': 0,
                        'status': 'ready',
                        'rfid': 2,
                        'type': 'PLA',
                        'color': [12, 34, 56],  # Ignored for RFID spools
                        'sku': 'SKU-123',
                    }
                ]
            }
        }

        instance._status_update_callback(response)

        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'ready')
        # Material stays Unknown until get_filament_info callback fires (new architecture)
        self.assertEqual(slot['material'], 'Unknown')
        self.assertEqual(slot['color'], [0, 0, 0])  # Default color
        self.assertTrue(slot['rfid'])
        INSTANCE_MANAGERS[0]._sync_inventory_to_persistent.assert_called_once_with(0, flush=False)

        status = instance.get_status()
        slot0 = status['slots'][0]
        self.assertTrue(slot0['rfid'])
        # Material from inventory (defaults until callback)
        self.assertEqual(slot0['material'], 'Unknown')
        self.assertEqual(slot0['color'], [0, 0, 0])  # Default color

    @patch('ace.instance.AceSerialManager')
    def test_status_update_rfid_sync_disabled_skips_rfid_data(self, mock_serial_mgr_class):
        """RFID data is ignored when sync flag disabled."""
        INSTANCE_MANAGERS.clear()
        ace_config = dict(self.ace_config)
        ace_config['rfid_inventory_sync_enabled'] = False
        instance = AceInstance(0, ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        response = {
            'result': {
                'slots': [
                    {
                        'index': 0,
                        'status': 'ready',
                        'rfid': 2,
                        'type': 'PLA',
                        'color': [200, 100, 50],
                    }
                ]
            }
        }

        instance._status_update_callback(response)

        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'ready')
        self.assertEqual(slot['material'], '')
        self.assertEqual(slot['color'], [0, 0, 0])
        self.assertFalse(slot['rfid'])
        INSTANCE_MANAGERS[0]._sync_inventory_to_persistent.assert_called_once_with(0, flush=False)

        status = instance.get_status()
        slot0 = status['slots'][0]
        self.assertFalse(slot0['rfid'])
        self.assertEqual(slot0['material'], '')
        self.assertEqual(slot0['color'], [0, 0, 0])

    @patch('ace.instance.AceSerialManager')
    def test_status_update_empty_slot_clears_rfid(self, mock_serial_mgr_class):
        """Empty status clears previously set RFID marker."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        instance.inventory[0].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [10, 20, 30],
            'rfid': True,
        })

        response = {
            'result': {
                'slots': [
                    {
                        'index': 0,
                        'status': 'empty',
                        'rfid': 0,
                    }
                ]
            }
        }

        instance._status_update_callback(response)

        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'empty')
        self.assertFalse(slot['rfid'])
        INSTANCE_MANAGERS[0]._sync_inventory_to_persistent.assert_called_once_with(0, flush=False)

        status = instance.get_status()
        slot0 = status['slots'][0]
        self.assertFalse(slot0['rfid'])

    @patch('ace.instance.AceSerialManager')
    def test_empty_slot_clears_material_when_not_printing(self, mock_serial_mgr_class):
        """Empty slot clears material/color/temp when NOT printing or paused."""
        INSTANCE_MANAGERS.clear()
        
        # Mock print_stats to return 'standby' (not printing/paused)
        mock_print_stats = Mock()
        mock_print_stats.get_status.return_value = {'state': 'standby'}
        
        def lookup_with_print_stats(name, default=None):
            if name == 'print_stats':
                return mock_print_stats
            return self._mock_lookup_object(name, default)
        
        self.mock_printer.lookup_object = lookup_with_print_stats
        
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        # Pre-fill with material/color/temp
        instance.inventory[0].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [255, 0, 0],  # Red
            'temp': 210,
            'rfid': True,
        })

        # Slot becomes empty
        response = {
            'result': {
                'slots': [
                    {
                        'index': 0,
                        'status': 'empty',
                        'rfid': 0,
                    }
                ]
            }
        }

        instance._status_update_callback(response)

        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'empty')
        self.assertEqual(slot['material'], '', "Material should be cleared when not printing")
        self.assertEqual(slot['color'], [0, 0, 0], "Color should be reset to black when not printing")
        self.assertEqual(slot['temp'], 0, "Temp should be reset to 0 when not printing")
        self.assertFalse(slot['rfid'])

    @patch('ace.instance.AceSerialManager')
    def test_empty_slot_preserves_material_when_printing(self, mock_serial_mgr_class):
        """Empty slot preserves material/color/temp when printing (for endless spool)."""
        INSTANCE_MANAGERS.clear()
        
        # Mock print_stats to return 'printing'
        mock_print_stats = Mock()
        mock_print_stats.get_status.return_value = {'state': 'printing'}
        
        def lookup_with_print_stats(name, default=None):
            if name == 'print_stats':
                return mock_print_stats
            return self._mock_lookup_object(name, default)
        
        self.mock_printer.lookup_object = lookup_with_print_stats
        
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        # Pre-fill with material/color/temp
        instance.inventory[0].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [255, 0, 0],  # Red
            'temp': 210,
            'rfid': True,
        })

        # Slot becomes empty during printing
        response = {
            'result': {
                'slots': [
                    {
                        'index': 0,
                        'status': 'empty',
                        'rfid': 0,
                    }
                ]
            }
        }

        instance._status_update_callback(response)

        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'empty')
        self.assertEqual(slot['material'], 'PLA', "Material should be preserved during printing")
        self.assertEqual(slot['color'], [255, 0, 0], "Color should be preserved during printing")
        self.assertEqual(slot['temp'], 210, "Temp should be preserved during printing")
        self.assertFalse(slot['rfid'])

    @patch('ace.instance.AceSerialManager')
    def test_empty_slot_preserves_material_when_paused(self, mock_serial_mgr_class):
        """Empty slot preserves material/color/temp when paused (for endless spool)."""
        INSTANCE_MANAGERS.clear()
        
        # Mock print_stats to return 'paused'
        mock_print_stats = Mock()
        mock_print_stats.get_status.return_value = {'state': 'paused'}
        
        def lookup_with_print_stats(name, default=None):
            if name == 'print_stats':
                return mock_print_stats
            return self._mock_lookup_object(name, default)
        
        self.mock_printer.lookup_object = lookup_with_print_stats
        
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        # Pre-fill with material/color/temp
        instance.inventory[0].update({
            'status': 'ready',
            'material': 'PETG',
            'color': [0, 255, 0],  # Green
            'temp': 240,
            'rfid': True,
        })

        # Slot becomes empty while paused
        response = {
            'result': {
                'slots': [
                    {
                        'index': 0,
                        'status': 'empty',
                        'rfid': 0,
                    }
                ]
            }
        }

        instance._status_update_callback(response)

        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'empty')
        self.assertEqual(slot['material'], 'PETG', "Material should be preserved when paused")
        self.assertEqual(slot['color'], [0, 255, 0], "Color should be preserved when paused")
        self.assertEqual(slot['temp'], 240, "Temp should be preserved when paused")
        self.assertFalse(slot['rfid'])

    @patch('ace.instance.AceSerialManager')
    def test_heartbeat_callback_updates_status(self, mock_serial_mgr_class):
        """Test heartbeat callback updates internal status."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        response = {
            'code': 0,
            'result': {
                'status': 'ready',
                'temp': 25,
                'slots': [
                    {'index': 0, 'status': 'empty'},
                    {'index': 1, 'status': 'ready'},
                    {'index': 2, 'status': 'empty'},
                    {'index': 3, 'status': 'ready'},
                ]
            }
        }
        
        instance._on_heartbeat_response(response)
        
        self.assertEqual(instance._info['status'], 'ready')
        self.assertEqual(instance._info['temp'], 25)

    @patch('ace.instance.AceSerialManager')
    def test_is_ready_true(self, mock_serial_mgr_class):
        """Test is_ready returns True when status is ready."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._info['status'] = 'ready'
        
        self.assertTrue(instance.is_ready())

    @patch('ace.instance.AceSerialManager')
    def test_is_ready_false(self, mock_serial_mgr_class):
        """Test is_ready returns False when status is not ready."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._info['status'] = 'busy'
        
        self.assertFalse(instance.is_ready())

    @patch('ace.instance.AceSerialManager')
    def test_non_rfid_spool_gets_default_material_and_temp(self, mock_serial_mgr_class):
        """Non-RFID spool (rfid=0) in empty slot gets default material/temp/color."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        # Slot starts empty with no metadata
        self.assertEqual(instance.inventory[0]['status'], 'empty')
        self.assertEqual(instance.inventory[0]['material'], '')
        self.assertEqual(instance.inventory[0]['color'], [0, 0, 0])
        self.assertEqual(instance.inventory[0]['temp'], 0)

        # Non-RFID spool inserted (rfid=0)
        response = {
            'result': {
                'slots': [
                    {
                        'index': 0,
                        'status': 'ready',
                        'rfid': 0,  # Non-RFID spool
                    }
                ]
            }
        }

        instance._status_update_callback(response)

        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'ready')
        # Should get defaults: Unknown, 0°C, black
        self.assertEqual(slot['material'], 'Unknown')
        self.assertEqual(slot['temp'], 0)
        self.assertEqual(slot['color'], [0, 0, 0])
        self.assertFalse(slot['rfid'])

    @patch('ace.instance.AceSerialManager')
    def test_non_rfid_spool_no_rfid_key_gets_defaults(self, mock_serial_mgr_class):
        """Spool with no rfid key in response gets default material/temp/color."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        # Non-RFID spool inserted (no rfid key at all)
        response = {
            'result': {
                'slots': [
                    {
                        'index': 0,
                        'status': 'ready',
                        # No 'rfid' key - simulates older firmware or non-RFID detection
                    }
                ]
            }
        }

        instance._status_update_callback(response)

        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'ready')
        self.assertEqual(slot['material'], 'Unknown')
        self.assertEqual(slot['temp'], 0)
        self.assertEqual(slot['color'], [0, 0, 0])

    @patch('ace.instance.AceSerialManager')
    def test_empty_to_ready_preserves_saved_metadata(self, mock_serial_mgr_class):
        """Slot with saved metadata preserves it when RFID spool reinserted (identifying)."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        # Pre-populate inventory with saved metadata (simulates persistence)
        instance.inventory[0].update({
            'status': 'empty',
            'material': 'PETG',
            'color': [100, 150, 200],
            'temp': 240,
            'rfid': False,
        })

        # RFID spool inserted (identifying state - not yet identified)
        response = {
            'result': {
                'slots': [
                    {
                        'index': 0,
                        'status': 'ready',
                        'rfid': 3,  # RFID_STATE_IDENTIFYING
                    }
                ]
            }
        }

        instance._status_update_callback(response)

        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'ready')
        # Should preserve saved values when RFID spool is identifying
        self.assertEqual(slot['material'], 'PETG')
        self.assertEqual(slot['temp'], 240)
        self.assertEqual(slot['color'], [100, 150, 200])

    @patch('ace.instance.AceSerialManager')
    def test_empty_to_ready_clears_old_rfid_data_for_non_rfid_spool(self, mock_serial_mgr_class):
        """Non-RFID spool replacing RFID spool clears RFID fields but preserves material/color/temp."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        # Pre-populate inventory with old RFID spool metadata
        instance.inventory[0].update({
            'status': 'empty',
            'material': 'ABS',  # Old RFID material - should be PRESERVED
            'color': [255, 0, 0],  # Red from old RFID - should be PRESERVED
            'temp': 250,  # High temp from old RFID - should be PRESERVED
            'rfid': True,  # Was RFID before going empty
            'sku': 'OLD-SKU-123',
            'brand': 'OldBrand',
        })

        # Non-RFID spool inserted (rfid=0)
        response = {
            'result': {
                'slots': [
                    {
                        'index': 0,
                        'status': 'ready',
                        'rfid': 0,  # RFID_STATE_NO_INFO - non-RFID spool
                    }
                ]
            }
        }

        instance._status_update_callback(response)

        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'ready')
        # Should PRESERVE material/color/temp (user may have refilled with matching non-RFID spool)
        self.assertEqual(slot['material'], 'ABS', "Should preserve material for print continuity")
        self.assertEqual(slot['temp'], 250, "Should preserve temp for print continuity")
        self.assertEqual(slot['color'], [255, 0, 0], "Should preserve color for print continuity")
        self.assertFalse(slot['rfid'], "RFID flag should be False")
        # Old RFID-specific fields should be cleared
        self.assertNotIn('sku', slot, "Old SKU should be cleared")
        self.assertNotIn('brand', slot, "Old brand should be cleared")

    @patch('ace.instance.AceSerialManager')
    def test_swap_rfid_to_non_rfid_during_printing(self, mock_serial_mgr_class):
        """Swapping RFID → non-RFID spool during printing clears RFID fields but preserves material/color/temp."""
        INSTANCE_MANAGERS.clear()
        
        # Mock print_stats to return 'printing'
        mock_print_stats = Mock()
        mock_print_stats.get_status.return_value = {'state': 'printing'}
        
        def lookup_with_print_stats(name, default=None):
            if name == 'print_stats':
                return mock_print_stats
            return self._mock_lookup_object(name, default)
        
        self.mock_printer.lookup_object = lookup_with_print_stats
        
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        # Start with RFID spool loaded
        instance.inventory[0].update({
            'status': 'ready',
            'material': 'ABS',
            'color': [255, 0, 0],
            'temp': 250,
            'rfid': True,
            'sku': 'RFID-SKU-999',
            'brand': 'RFIDBrand',
        })

        # Spool removed (becomes empty) - RFID fields cleared, material/temp preserved during printing
        response_empty = {
            'result': {
                'slots': [
                    {
                        'index': 0,
                        'status': 'empty',
                        'rfid': 0,
                    }
                ]
            }
        }
        instance._status_update_callback(response_empty)

        # Verify RFID flag and fields cleared but material/temp preserved
        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'empty')
        self.assertFalse(slot['rfid'], "RFID flag should be cleared")
        self.assertNotIn('sku', slot, "SKU should be cleared")
        self.assertEqual(slot['material'], 'ABS', "Material preserved during printing")
        self.assertEqual(slot['temp'], 250, "Temp preserved during printing")

        # Non-RFID spool inserted
        response_ready = {
            'result': {
                'slots': [
                    {
                        'index': 0,
                        'status': 'ready',
                        'rfid': 0,  # RFID_STATE_NO_INFO
                    }
                ]
            }
        }
        instance._status_update_callback(response_ready)

        # Verify RFID fields cleared but material/color/temp preserved for print continuity
        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'ready')
        self.assertEqual(slot['material'], 'ABS', "Should preserve material for print continuity")
        self.assertEqual(slot['temp'], 250, "Should preserve temp for print continuity")
        self.assertEqual(slot['color'], [255, 0, 0], "Should preserve color for print continuity")
        self.assertFalse(slot['rfid'])
        self.assertNotIn('sku', slot, "Old SKU should stay cleared")
        self.assertNotIn('brand', slot, "Old brand should stay cleared")

    @patch('ace.instance.AceSerialManager')
    def test_rfid_sync_disabled_with_non_rfid_spool_gets_defaults(self, mock_serial_mgr_class):
        """Non-RFID spool still gets defaults even when RFID sync is disabled."""
        INSTANCE_MANAGERS.clear()
        ace_config = dict(self.ace_config)
        ace_config['rfid_inventory_sync_enabled'] = False
        instance = AceInstance(0, ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        # Non-RFID spool inserted (rfid=0)
        response = {
            'result': {
                'slots': [
                    {
                        'index': 0,
                        'status': 'ready',
                        'rfid': 0,  # Non-RFID spool
                    }
                ]
            }
        }

        instance._status_update_callback(response)

        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'ready')
        # Should still get defaults because it's NOT an RFID spool
        self.assertEqual(slot['material'], 'Unknown')
        self.assertEqual(slot['temp'], 0)
        self.assertEqual(slot['color'], [0, 0, 0])

    @patch('ace.instance.AceSerialManager')
    def test_default_values_match_class_constants(self, mock_serial_mgr_class):
        """Verify default values match AceInstance class constants."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        # Verify the class constants
        self.assertEqual(instance.DEFAULT_MATERIAL, 'Unknown')
        self.assertEqual(instance.DEFAULT_TEMP, 0)
        self.assertEqual(instance.DEFAULT_COLOR, [0, 0, 0])

        # Insert non-RFID spool and verify defaults are applied
        response = {
            'result': {
                'slots': [
                    {'index': 0, 'status': 'ready', 'rfid': 0}
                ]
            }
        }
        instance._status_update_callback(response)

        slot = instance.inventory[0]
        self.assertEqual(slot['material'], instance.DEFAULT_MATERIAL)
        self.assertEqual(slot['temp'], instance.DEFAULT_TEMP)
        self.assertEqual(slot['color'], list(instance.DEFAULT_COLOR))

    @patch('ace.instance.AceSerialManager')
    def test_inventory_write_only_on_change(self, mock_serial_mgr_class):
        """Verify inventory behavior with RFID spools - gray placeholder when material unchanged."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        # Set up initial state with RFID spool (already has extruder_temp so won't trigger new query)
        instance.inventory[0] = {
            'status': 'ready',
            'color': [255, 0, 0],
            'material': 'PLA',
            'temp': 200,
            'rfid': True,
            'extruder_temp': {'min': 190, 'max': 230},
            'hotbed_temp': {'min': 50, 'max': 60}
        }

        # Send identical material status - RFID already detected, no requery
        response = {
            'result': {
                'slots': [
                    {'index': 0, 'status': 'ready', 'rfid': 2, 'type': 'PLA', 'color': [255, 0, 0]}
                ]
            }
        }
        instance._status_update_callback(response)

        # Status and material unchanged (RFID not queried - already detected)
        self.assertEqual(instance.inventory[0]['status'], 'ready')
        self.assertEqual(instance.inventory[0]['color'], [255, 0, 0])  # Unchanged - RFID already detected
        self.assertEqual(instance.inventory[0]['material'], 'PLA')
        self.assertEqual(instance.inventory[0]['temp'], 200)
        self.assertEqual(instance.inventory[0]['rfid'], True)

    @patch('ace.instance.AceSerialManager')
    def test_inventory_write_all_fields_on_change(self, mock_serial_mgr_class):
        """Verify ALL inventory fields are written correctly when RFID detected."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        # Set up initial empty state
        instance.inventory[0] = {
            'status': 'empty',
            'color': [0, 0, 0],
            'material': '',
            'temp': 0,
            'rfid': False
        }

        # Send status with new RFID spool - color from get_status is ignored for RFID spools
        response = {
            'result': {
                'slots': [
                    {'index': 0, 'status': 'ready', 'rfid': 2, 'type': 'PETG', 'color': [0, 255, 0]}
                ]
            }
        }
        instance._status_update_callback(response)

        # Verify fields: RFID detected but material stays Unknown until get_filament_info callback
        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'ready')  # status updated
        self.assertEqual(slot['color'], [0, 0, 0])  # Default color until callback
        self.assertEqual(slot['material'], 'Unknown')  # Default material until callback
        self.assertEqual(slot['temp'], 0)  # Default temp
        self.assertEqual(slot['rfid'], True)  # rfid flag updated

    @patch('ace.instance.AceSerialManager')
    def test_inventory_write_detects_each_field_change(self, mock_serial_mgr_class):
        """Verify each individual field change triggers a proper write."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()

        # Test status change
        instance.inventory[0] = {'status': 'empty', 'color': [0,0,0], 'material': '', 'temp': 0, 'rfid': False}
        response = {'result': {'slots': [{'index': 0, 'status': 'ready', 'rfid': 0}]}}
        instance._status_update_callback(response)
        self.assertEqual(instance.inventory[0]['status'], 'ready')

        # Test color change - RFID spools keep default color until get_filament_info callback
        instance.inventory[0] = {'status': 'ready', 'color': [0,0,0], 'material': 'PLA', 'temp': 200, 'rfid': False}
        instance.inventory[0]['color'] = [100, 100, 100]  # Set initial color
        response = {'result': {'slots': [{'index': 0, 'status': 'ready', 'rfid': 2, 'type': 'PLA', 'color': [200, 200, 200]}]}}
        instance._status_update_callback(response)
        # Color from get_status is ignored for RFID spools - keeps inventory value
        self.assertEqual(instance.inventory[0]['color'], [100, 100, 100])

        # Test material change - RFID spools don't read material from get_status
        instance.inventory[0] = {'status': 'ready', 'color': [255,0,0], 'material': 'PLA', 'temp': 200, 'rfid': True}
        response = {'result': {'slots': [{'index': 0, 'status': 'ready', 'rfid': 2, 'type': 'ABS', 'color': [255, 0, 0]}]}}
        instance._status_update_callback(response)
        # Material stays in inventory (not updated from get_status)
        self.assertEqual(instance.inventory[0]['material'], 'PLA')

        # Test temp change - RFID spools don't read temp from get_status
        instance.inventory[0] = {'status': 'ready', 'color': [255,0,0], 'material': 'PLA', 'temp': 200, 'rfid': True}
        response = {'result': {'slots': [{'index': 0, 'status': 'ready', 'rfid': 2, 'type': 'PETG', 'color': [255, 0, 0]}]}}
        instance._status_update_callback(response)
        # Temp stays in inventory (not updated from get_status)
        self.assertEqual(instance.inventory[0]['temp'], 200)

        # NOTE: RFID flag updates are triggered by get_status but actual update happens in get_filament_info callback.
        # The test would need to mock the callback to verify this behavior.


class TestFeedRetractOperations(unittest.TestCase):
    """Test feed and retract operations."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)
        
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    @patch('ace.instance.AceSerialManager')
    def test_feed_sends_request(self, mock_serial_mgr_class):
        """Test _feed sends correct serial request."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        callback = Mock()
        
        instance._feed(1, 50.0, 100, callback)
        
        instance.serial_mgr.send_request.assert_called_once()

    @patch('ace.instance.AceSerialManager')
    def test_stop_feed_sends_command(self, mock_serial_mgr_class):
        """Test _stop_feed sends stop command."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        instance._stop_feed(2)
        
        instance.serial_mgr.send_high_prio_request.assert_called_once()

    @patch('ace.instance.AceSerialManager')
    def test_retract_sends_request(self, mock_serial_mgr_class):
        """Test _retract sends correct serial request."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._info["slots"][0]["status"] = "ready"
        
        # Mock send_request to invoke callback immediately
        def mock_send_request(request, callback):
            callback({'code': 0, 'msg': 'ok'})
        
        instance.serial_mgr.send_request.side_effect = mock_send_request
        
        instance._retract(0, 30.0, 80)
        
        # _retract sends one request
        instance.serial_mgr.send_request.assert_called_once()

    @patch('ace.instance.AceSerialManager')
    def test_retract_skips_when_slot_empty(self, mock_serial_mgr_class):
        """Retract returns early when slot is already empty."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)

        response = instance._retract(0, 30.0, 80)

        instance.serial_mgr.send_request.assert_not_called()
        self.assertEqual(response.get("code"), 0)
        self.assertIn("slot empty", response.get("msg", "").lower())

    @patch('ace.instance.AceSerialManager')
    def test_change_retract_speed(self, mock_serial_mgr_class):
        """Test changing retract speed."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        instance._change_retract_speed(1, 120)
        
        instance.serial_mgr.send_request.assert_called_once()


class TestHeartbeat(unittest.TestCase):
    """Test heartbeat functionality."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)
        
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.unregister_timer = Mock()
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    @patch('ace.instance.AceSerialManager')
    def test_dwell_pauses_reactor(self, mock_serial_mgr_class):
        """Test dwell pauses reactor for specified delay."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        instance.dwell(2.5)
        
        self.mock_reactor.pause.assert_called_once()


class TestFeedFilamentIntoToolhead(unittest.TestCase):
    """Test feeding filament into toolhead."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)
        
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    @patch('ace.instance.AceSerialManager')
    @patch('ace.instance.INSTANCE_MANAGERS')
    def test_feed_filament_into_toolhead_validates_tool(self, mock_managers, mock_serial_mgr_class):
        """Test feed validates tool index."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        mock_managers.get = Mock(return_value=Mock())
        
        # Test invalid tool index (out of range)
        with self.assertRaises(ValueError) as context:
            instance._feed_filament_into_toolhead(10)  # Tool 10 is out of range
        
        self.assertIn("not managed by this ACE instance", str(context.exception))

    @patch('ace.instance.AceSerialManager')
    @patch('ace.instance.INSTANCE_MANAGERS')
    def test_feed_filament_into_toolhead_sensor_check(self, mock_managers, mock_serial_mgr_class):
        """Test feed checks toolhead sensor state."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock manager
        mock_manager = Mock()
        mock_manager.get_switch_state = Mock(return_value=True)  # Sensor triggered
        mock_manager.has_rdm_sensor = Mock(return_value=False)
        mock_managers.get = Mock(return_value=mock_manager)
        
        # Should raise error when toolhead sensor triggered
        with self.assertRaises(ValueError) as context:
            instance._feed_filament_into_toolhead(1, check_pre_condition=True)
        
        self.assertIn("filament in nozzle", str(context.exception))

    @patch('ace.instance.AceSerialManager')
    @patch('ace.instance.INSTANCE_MANAGERS')
    def test_feed_filament_into_toolhead_success_flow(self, mock_managers, mock_serial_mgr_class):
        """Test successful feed flow from slot to nozzle."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock manager with clear sensors
        mock_manager = Mock()
        mock_manager.get_switch_state = Mock(return_value=False)  # Sensors clear
        mock_manager.has_rdm_sensor = Mock(return_value=True)
        mock_managers.get = Mock(return_value=mock_manager)
        
        # Mock successful feed operations
        instance._feed_to_toolhead_with_extruder_assist = Mock(return_value=10.0)
        instance._extruder_move = Mock()
        
        # Mock toolhead
        mock_toolhead = Mock()
        self.mock_printer.lookup_object = Mock(return_value=mock_toolhead)
        
        # Execute
        result = instance._feed_filament_into_toolhead(1, check_pre_condition=True)
        
        # Verify feed to toolhead was called
        instance._feed_to_toolhead_with_extruder_assist.assert_called_once_with(
            1,  # local_slot
            instance.toolchange_load_length,
            instance.feed_speed,
            instance.extruder_feeding_length,
            instance.extruder_feeding_speed
        )
        
        # Verify variable persistence: TOOLHEAD via set(), NOZZLE via set()
        mock_manager.state.set.assert_any_call("ace_filament_pos", FILAMENT_STATE_TOOLHEAD)
        mock_manager.state.set.assert_any_call("ace_filament_pos", FILAMENT_STATE_NOZZLE)
        
        # Verify G92 E0 was called
        self.mock_gcode.run_script_from_command.assert_called_with("G92 E0")
        
        # Verify extruder move to nozzle
        instance._extruder_move.assert_called_once_with(
            instance.toolhead_full_purge_length,
            instance.toolhead_slow_loading_speed
        )
        
        # Verify toolhead wait_moves
        mock_toolhead.wait_moves.assert_called_once()
        
        # Verify return value
        assert result == instance.toolhead_full_purge_length

    @patch('ace.instance.AceSerialManager')
    @patch('ace.instance.INSTANCE_MANAGERS')
    def test_feed_filament_into_toolhead_exception_with_cleanup(self, mock_managers, mock_serial_mgr_class):
        """Test exception handling triggers 150mm cleanup retract."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock manager
        mock_manager = Mock()
        mock_manager.get_switch_state = Mock(return_value=False)
        mock_manager.has_rdm_sensor = Mock(return_value=False)
        mock_managers.get = Mock(return_value=mock_manager)
        
        # Mock feed that raises exception
        test_exception = ValueError("Feed timeout test")
        instance._feed_to_toolhead_with_extruder_assist = Mock(side_effect=test_exception)
        instance._retract = Mock()
        
        # Execute and verify exception propagates
        with self.assertRaises(ValueError) as context:
            instance._feed_filament_into_toolhead(1)
        
        # Verify cleanup retract was called
        instance._retract.assert_called_once_with(1, 150, instance.retract_speed)
        
        # Verify original exception was re-raised
        assert str(context.exception) == "Feed timeout test"

    @patch('ace.instance.AceSerialManager')
    @patch('ace.instance.INSTANCE_MANAGERS')
    def test_feed_filament_into_toolhead_skips_precondition_check(self, mock_managers, mock_serial_mgr_class):
        """Test check_pre_condition=False skips sensor validation."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock manager with BLOCKED sensors (should be ignored)
        mock_manager = Mock()
        mock_manager.get_switch_state = Mock(return_value=True)  # Sensors triggered!
        mock_manager.has_rdm_sensor = Mock(return_value=True)
        mock_managers.get = Mock(return_value=mock_manager)
        
        # Mock successful operations
        instance._feed_to_toolhead_with_extruder_assist = Mock(return_value=10.0)
        instance._extruder_move = Mock()
        mock_toolhead = Mock()
        self.mock_printer.lookup_object = Mock(return_value=mock_toolhead)
        
        # Should NOT raise even though sensors are blocked
        result = instance._feed_filament_into_toolhead(1, check_pre_condition=False)
        
        # Verify feed proceeded
        instance._feed_to_toolhead_with_extruder_assist.assert_called_once()
        assert result == instance.toolhead_full_purge_length

    @patch('ace.instance.AceSerialManager')
    @patch('ace.instance.INSTANCE_MANAGERS')
    def test_feed_filament_into_toolhead_rdm_sensor_check(self, mock_managers, mock_serial_mgr_class):
        """Test RDM sensor blocking when present."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock manager with RDM sensor blocked
        mock_manager = Mock()
        def get_switch_state_side_effect(sensor_type):
            if sensor_type == SENSOR_RDM:
                return True  # RDM blocked
            return False  # Toolhead clear
        mock_manager.get_switch_state = Mock(side_effect=get_switch_state_side_effect)
        mock_manager.has_rdm_sensor = Mock(return_value=True)
        mock_managers.get = Mock(return_value=mock_manager)
        
        # Should raise error about RMS
        with self.assertRaises(ValueError) as context:
            instance._feed_filament_into_toolhead(1, check_pre_condition=True)
        
        self.assertIn("filament stuck in RMS", str(context.exception))


class TestSmartUnloadSlot(unittest.TestCase):
    """Test smart unload slot functionality."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)
        
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    @patch('ace.instance.AceSerialManager')
    @patch('ace.instance.INSTANCE_MANAGERS')
    def test_smart_unload_slot_has_manager_dependency(self, mock_managers, mock_serial_mgr_class):
        """Test smart unload requires manager."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock manager
        mock_manager = Mock()
        mock_manager.has_rdm_sensor = Mock(return_value=False)
        mock_managers.get = Mock(return_value=mock_manager)
        
        # Just verify it can be called (complex wait logic makes full test impractical)
        instance._info['slots'][1]['filament_state'] = FILAMENT_STATE_BOWDEN
        
        # Verify manager is accessed
        try:
            instance._smart_unload_slot(1, length=1)  # Short length
        except:
            pass  # Expected to fail without full mocking
        
        # Verify manager was accessed
        mock_managers.get.assert_called_with(0)


class TestStatusUpdateCallback(unittest.TestCase):
    """Branch coverage for _status_update_callback."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.pause = Mock()

        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    @patch('ace.instance.AceSerialManager')
    def test_status_update_inventory_sync_enabled(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        INSTANCE_MANAGERS[0] = manager


        response = {
            "result": {
                "slots": [
                    {"index": 0, "status": "ready", "rfid": RFID_STATE_IDENTIFIED, "type": "PLA", "color": [1, 2, 3], "sku": "sku1", "brand": "b"},
                    {"index": 1, "status": "empty"},
                ]
            }
        }

        instance._status_update_callback(response)

        inv0 = instance.inventory[0]
        self.assertEqual(inv0["status"], "ready")
        # Material/color/SKU/brand stay in inventory (not updated from get_status)
        self.assertEqual(inv0["material"], "Unknown")
        self.assertEqual(inv0["color"], [0, 0, 0])  # Default color
        self.assertTrue(inv0["rfid"])
        # SKU/brand not updated from get_status (only from get_filament_info callback)
        self.assertNotIn("sku", inv0)
        self.assertNotIn("brand", inv0)
        manager._sync_inventory_to_persistent.assert_called_once_with(0, flush=False)
        # Feed assist restore not triggered without filament_loaded flag
        self.assertFalse(instance._feed_assist_index >= 0 and instance._pending_feed_assist_restore != -1)

    @patch('ace.instance.AceSerialManager')
    def test_status_update_rfid_sync_disabled_defaults_and_clears(self, mock_serial_mgr_class):
        ace_config = dict(self.ace_config)
        ace_config['rfid_inventory_sync_enabled'] = False
        instance = AceInstance(0, ace_config, self.mock_printer)
        manager = Mock()
        INSTANCE_MANAGERS[0] = manager

        # Pre-fill inventory with metadata to ensure empty clears it
        instance.inventory[1].update({
            "status": "ready",
            "material": "ABS",
            "color": [9, 9, 9],
            "rfid": True,
            "sku": "old",
        })

        response = {
            "result": {
                "slots": [
                    {"index": 0, "status": "ready", "rfid": RFID_STATE_IDENTIFIED, "type": "PLA", "color": [0, 0, 0]},
                    {"index": 1, "status": "empty"},
                ]
            }
        }

        instance._status_update_callback(response)

        inv0 = instance.inventory[0]
        # With RFID sync disabled and RFID present, defaults should NOT be auto-filled
        self.assertEqual(inv0["material"], "")
        self.assertEqual(inv0["temp"], 0)
        self.assertEqual(inv0["color"], [0, 0, 0])
        inv1 = instance.inventory[1]
        self.assertEqual(inv1["status"], "empty")
        self.assertFalse(inv1.get("rfid"))
        self.assertNotIn("sku", inv1)
        manager._sync_inventory_to_persistent.assert_called_once_with(0, flush=False)

    @patch('ace.instance.AceSerialManager')
    def test_status_update_default_fill_when_missing_metadata(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)

        response = {
            "result": {
                "slots": [
                    {"index": 0, "status": "ready", "rfid": RFID_STATE_NO_INFO, "type": "", "color": [0, 0, 0]},
                ]
            }
        }

        instance._status_update_callback(response)

        inv0 = instance.inventory[0]
        self.assertEqual(inv0["material"], instance.DEFAULT_MATERIAL)
        self.assertEqual(inv0["temp"], instance.DEFAULT_TEMP)
        self.assertEqual(inv0["color"], list(instance.DEFAULT_COLOR))

    @patch('ace.instance.AceSerialManager')
    def test_status_update_handles_missing_result_and_no_changes(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        INSTANCE_MANAGERS[0] = manager
        manager._sync_inventory_to_persistent = Mock()

        # No result key should be a no-op
        instance._status_update_callback({})
        manager._sync_inventory_to_persistent.assert_not_called()

    @patch('ace.instance.AceSerialManager')
    def test_status_update_triggers_rfid_query_and_feed_assist_restore(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        INSTANCE_MANAGERS[0] = manager
        manager._sync_inventory_to_persistent = Mock()
        instance._query_rfid_full_data = Mock()
        instance._feed_assist_index = 2

        response = {
            "result": {
                "slots": [
                    {
                        "index": 0,
                        "status": "ready",
                        "rfid": RFID_STATE_IDENTIFIED,
                        "type": "PLA",
                        "color": [10, 20, 30],
                    }
                ]
            }
        }

        instance._status_update_callback(response)

        instance._query_rfid_full_data.assert_called_once_with(0)
        # Feed assist restore is attempted when filament loaded and feed assist was active
        # We can't assert internal _enable_feed_assist call easily without patching, but ensure feed_assist_index unchanged
        self.assertEqual(instance._feed_assist_index, 2)


class TestWaitForCondition(unittest.TestCase):
    """Test wait for condition utility."""

    def setUp(self):
        """Set up test fixtures."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)
        
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    @patch('ace.instance.AceSerialManager')
    @patch('time.time')
    def test_wait_for_condition_success(self, mock_time, mock_serial_mgr_class):
        """Test wait for condition returns True when condition met."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock time to progress slightly but not timeout
        mock_time.side_effect = [100.0, 100.1, 100.2]
        
        condition = Mock(return_value=True)
        result = instance._wait_for_condition(condition, timeout_s=5.0)
        
        self.assertTrue(result)
        condition.assert_called()

    @patch('ace.instance.AceSerialManager')
    @patch('time.time')
    def test_wait_for_condition_timeout(self, mock_time, mock_serial_mgr_class):
        """Test wait for condition returns False on timeout."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock time to simulate timeout
        mock_time.side_effect = [100.0, 106.0]  # Start, then past timeout
        
        condition = Mock(return_value=False)
        result = instance._wait_for_condition(condition, timeout_s=5.0)
        
        self.assertFalse(result)


class TestManagerProperty(unittest.TestCase):
    """Test manager property accessor."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        
        self.variables = {}
        self.mock_save_vars.allVariables = self.variables
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': None,
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        return default

    @patch('ace.instance.AceSerialManager')
    def test_manager_property_returns_manager(self, mock_serial_mgr_class):
        """Test manager property returns correct manager from registry."""
        # Register mock manager
        mock_manager = Mock()
        INSTANCE_MANAGERS[0] = mock_manager
        
        try:
            instance = AceInstance(0, self.ace_config, self.mock_printer)
            
            # Access manager property
            result = instance.manager
            
            self.assertEqual(result, mock_manager)
        finally:
            INSTANCE_MANAGERS.clear()

    @patch('ace.instance.AceSerialManager')
    def test_manager_property_returns_none_if_not_registered(self, mock_serial_mgr_class):
        """Test manager property returns None if instance not in registry."""
        INSTANCE_MANAGERS.clear()
        
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Access manager property - should return None via .get()
        result = instance.manager
        
        self.assertIsNone(result)


class TestSensorTriggerMonitor(unittest.TestCase):
    """Test _make_sensor_trigger_monitor() functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_manager = Mock()
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        
        self.variables = {}
        self.mock_save_vars.allVariables = self.variables
        
        # Register manager
        INSTANCE_MANAGERS[0] = self.mock_manager
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': None,
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    def tearDown(self):
        """Clean up after tests."""
        INSTANCE_MANAGERS.clear()

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        return default

    @patch('ace.instance.AceSerialManager')
    @patch('time.time')
    def test_sensor_monitor_initialization(self, mock_time, mock_serial_mgr_class):
        """Test sensor monitor initializes state on first call."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock sensor state
        self.mock_manager.get_switch_state.return_value = True  # Sensor triggered
        mock_time.return_value = 100.0
        
        # Create monitor
        monitor = instance._make_sensor_trigger_monitor(SENSOR_TOOLHEAD)
        
        # First call should initialize
        monitor()
        
        # Should have recorded call
        self.assertEqual(monitor.get_call_count(), 1)
        # No timing yet (state hasn't changed)
        self.assertIsNone(monitor.get_timing())

    @patch('ace.instance.AceSerialManager')
    @patch('time.time')
    def test_sensor_monitor_detects_trigger(self, mock_time, mock_serial_mgr_class):
        """Test sensor monitor detects sensor state change."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock sensor state transition: triggered → clear
        self.mock_manager.get_switch_state.side_effect = [True, False]
        mock_time.side_effect = [100.0, 100.5]  # 0.5s elapsed
        
        # Create monitor
        monitor = instance._make_sensor_trigger_monitor(SENSOR_TOOLHEAD)
        
        # First call: initialize
        monitor()
        self.assertEqual(monitor.get_call_count(), 1)
        self.assertIsNone(monitor.get_timing())
        
        # Second call: detect state change
        monitor()
        self.assertEqual(monitor.get_call_count(), 2)
        
        # Should have recorded timing
        timing = monitor.get_timing()
        self.assertIsNotNone(timing)
        self.assertAlmostEqual(timing, 0.5, places=1)

    @patch('ace.instance.AceSerialManager')
    @patch('time.time')
    def test_sensor_monitor_no_state_change(self, mock_time, mock_serial_mgr_class):
        """Test sensor monitor returns None if state doesn't change."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock sensor state: always triggered (no change)
        self.mock_manager.get_switch_state.return_value = True
        mock_time.side_effect = [100.0, 100.2, 100.4, 100.6]
        
        # Create monitor
        monitor = instance._make_sensor_trigger_monitor(SENSOR_TOOLHEAD)
        
        # Multiple calls without state change
        monitor()  # Initialize
        monitor()
        monitor()
        monitor()
        
        self.assertEqual(monitor.get_call_count(), 4)
        # No timing because state never changed
        self.assertIsNone(monitor.get_timing())

    @patch('ace.instance.AceSerialManager')
    @patch('time.time')
    def test_sensor_monitor_call_count_increments(self, mock_time, mock_serial_mgr_class):
        """Test sensor monitor correctly counts calls."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        self.mock_manager.get_switch_state.return_value = True
        mock_time.return_value = 100.0
        
        monitor = instance._make_sensor_trigger_monitor(SENSOR_TOOLHEAD)
        
        # Call multiple times
        for i in range(1, 11):
            monitor()
            self.assertEqual(monitor.get_call_count(), i)


class TestGetStatus(unittest.TestCase):
    """Test get_status() method."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        
        self.variables = {}
        self.mock_save_vars.allVariables = self.variables
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': None,
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        return default

    @patch('ace.instance.AceSerialManager')
    def test_get_status_returns_copy(self, mock_serial_mgr_class):
        """Test get_status returns a copy of internal status dict."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Get status
        status1 = instance.get_status()
        status2 = instance.get_status()
        
        # Should be copies (different objects)
        self.assertIsNot(status1, status2)
        self.assertIsNot(status1, instance._info)
        
        # But equal content
        self.assertEqual(status1, status2)

    @patch('ace.instance.AceSerialManager')
    def test_get_status_contains_expected_keys(self, mock_serial_mgr_class):
        """Test get_status returns dict with expected structure."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        status = instance.get_status()
        
        # Check for expected keys
        self.assertIn('status', status)
        self.assertIn('dryer', status)
        self.assertIn('temp', status)
        self.assertIn('slots', status)
        
        # Check slots structure
        self.assertEqual(len(status['slots']), SLOTS_PER_ACE)

class TestInventoryJsonEmission(unittest.TestCase):
    """Test JSON emission of inventory data for UI/KlipperScreen."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        
        self.variables = {}
        self.mock_save_vars.allVariables = self.variables
        
        self.ace_config = {
            'baud': 115200,
            'feed_speed': 60,
            'retract_speed': 60,
            'total_max_feeding_length': 1200,
            'parkposition_to_toolhead_length': 1000,
            'toolchange_load_length': 950,
            'parkposition_to_rdm_length': 150,
            'incremental_feeding_length': 50,
            'incremental_feeding_speed': 30,
            'extruder_feeding_length': 22,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 5,
            'heartbeat_interval': 1.0,
            'timeout_multiplier': 3.0,
            'max_dryer_temperature': 60,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'filament_runout_nozzle',
            'toolhead_full_purge_length': 22,
            'rfid_inventory_sync_enabled': True,
            'rfid_temp_mode': 'average',
            'status_debug_logging': True,
        }
    
    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        return default
    
    @patch('ace.instance.AceSerialManager')
    def test_emit_inventory_includes_optional_rfid_fields_when_present(self, mock_serial_mgr_class):
        """Verify JSON output includes optional RFID fields (sku, brand, icon_type, rgba) when present."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        # Set up inventory with RFID data in slot 0, non-RFID in slot 1
        instance.inventory[0].update({
            'status': 'ready',
            'color': [234, 42, 43],
            'material': 'PLA',
            'temp': 200,
            'rfid': True,
            'sku': 'AHPLBK-101',
            'brand': 'Some Brand',
            'icon_type': 0,
            'rgba': [234, 42, 43, 255],
            'extruder_temp': {'min': 190, 'max': 210},
            'hotbed_temp': {'min': 50, 'max': 60},
            'diameter': 1.75,
            'total': 1000,
            'current': 750
        })
        
        instance.inventory[1].update({
            'status': 'ready',
            'color': [255, 255, 255],
            'material': 'PLA',
            'temp': 200,
            'rfid': False  # No RFID, so optional fields should not appear
        })
        
        # Emit inventory update
        instance._emit_inventory_update()
        
        # Use get_status for assertions instead of trace output
        data = instance.get_status()
        
        # Verify structure
        self.assertIn('instance', data)
        self.assertIn('slots', data)
        self.assertEqual(len(data['slots']), 4)
        
        # Verify slot 0 (with RFID) includes all optional fields
        slot0 = data['slots'][0]
        self.assertEqual(slot0['status'], 'ready')
        self.assertTrue(slot0['rfid'])
        self.assertIn('sku', slot0)
        self.assertEqual(slot0['sku'], 'AHPLBK-101')
        self.assertIn('brand', slot0)
        self.assertEqual(slot0['brand'], 'Some Brand')
        self.assertIn('icon_type', slot0)
        self.assertEqual(slot0['icon_type'], 0)
        self.assertIn('rgba', slot0)
        self.assertEqual(slot0['rgba'], [234, 42, 43, 255])
        self.assertIn('extruder_temp', slot0)
        self.assertIn('hotbed_temp', slot0)
        self.assertIn('diameter', slot0)
        self.assertIn('total', slot0)
        self.assertIn('current', slot0)
        
        # Verify slot 1 (without RFID) excludes optional fields
        slot1 = data['slots'][1]
        self.assertEqual(slot1['status'], 'ready')
        self.assertFalse(slot1['rfid'])
        self.assertNotIn('sku', slot1)
        self.assertNotIn('brand', slot1)
        self.assertNotIn('icon_type', slot1)
        self.assertNotIn('rgba', slot1)
        self.assertNotIn('extruder_temp', slot1)
        self.assertNotIn('hotbed_temp', slot1)
        
        # Verify required fields always present
        for slot in data['slots']:
            self.assertIn('status', slot)
            self.assertIn('color', slot)
            self.assertIn('material', slot)
            self.assertIn('temp', slot)
            self.assertIn('rfid', slot)
    
    @patch('ace.instance.AceSerialManager')
    def test_emit_inventory_backward_compatibility(self, mock_serial_mgr_class):
        """Verify JSON output maintains backward compatibility with required fields."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        # Empty slots should still have all required fields
        instance._emit_inventory_update()
        
        data = instance.get_status()
        
        # All slots must have required fields
        for i, slot in enumerate(data['slots']):
            self.assertIn('status', slot, f"Slot {i} missing 'status'")
            self.assertIn('color', slot, f"Slot {i} missing 'color'")
            self.assertIn('material', slot, f"Slot {i} missing 'material'")
            self.assertIn('temp', slot, f"Slot {i} missing 'temp'")
            self.assertIn('rfid', slot, f"Slot {i} missing 'rfid'")


class TestFeedFilamentToVerificationSensor(unittest.TestCase):
    """Test feeding filament until verification sensors trigger."""

    def setUp(self):
        """Set up shared mocks."""
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.pause = Mock()

        self.variables = {}
        self.mock_save_vars.allVariables = self.variables

        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

        INSTANCE_MANAGERS.clear()

    def _mock_lookup_object(self, name, default=None):
        """Return mocked Klipper objects."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        return default

    def _fake_time(self, start=0.0, step=0.2):
        """Return a callable that increments time on each invocation."""
        current = start

        def _time():
            nonlocal current
            current += step
            return current

        return _time

    @patch('ace.instance.AceSerialManager')
    def test_feed_reaches_rdm_and_sets_splitter_state(self, mock_serial_mgr_class):
        """Feed stops once RDM sensor trips and saves splitter position."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._feed = Mock()
        instance._stop_feed = Mock()
        instance.dwell = Mock()

        manager = Mock()
        manager.get_switch_state = Mock(side_effect=[False, False, True, True])
        INSTANCE_MANAGERS[0] = manager

        with patch('ace.instance.time.time', side_effect=self._fake_time(step=0.2)):
            instance._feed_filament_to_verification_sensor(1, SENSOR_RDM, feed_length=50)

        instance._feed.assert_called_once_with(1, 50, instance.feed_speed)
        instance._stop_feed.assert_called_once_with(1)
        manager.state.set.assert_called_once_with(
            "ace_filament_pos",
            FILAMENT_STATE_SPLITTER
        )

    @patch('ace.instance.AceSerialManager')
    def test_incremental_feed_reaches_toolhead(self, mock_serial_mgr_class):
        """Incremental feeding continues until toolhead sensor is reached."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._feed = Mock()
        instance._stop_feed = Mock()
        instance.dwell = Mock()
        instance.total_max_feeding_length = 80  # Keep loop tight for test

        manager = Mock()
        # Sensor trips after the first incremental feed
        manager.get_switch_state = Mock(side_effect=[False, False, False, False, True, True])
        INSTANCE_MANAGERS[0] = manager

        with patch('ace.instance.time.time', side_effect=self._fake_time(step=1.1)):
            instance._feed_filament_to_verification_sensor(2, SENSOR_TOOLHEAD, feed_length=50)

        self.assertEqual(
            instance._feed.call_args_list,
            [
                call(2, 50, instance.feed_speed),
                call(2, instance.incremental_feeding_length, instance.incremental_feeding_speed),
            ],
        )
        instance._stop_feed.assert_called_once_with(2)
        manager.state.set.assert_called_once_with(
            "ace_filament_pos",
            FILAMENT_STATE_TOOLHEAD
        )

    @patch('ace.instance.AceSerialManager')
    def test_feed_raises_if_sensor_already_triggered(self, mock_serial_mgr_class):
        """Feeding aborts immediately when target sensor is already active."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._feed = Mock()
        instance._stop_feed = Mock()
        manager = Mock()
        manager.get_switch_state = Mock(return_value=True)
        INSTANCE_MANAGERS[0] = manager

        with self.assertRaises(ValueError) as ctx:
            instance._feed_filament_to_verification_sensor(0, SENSOR_RDM, feed_length=40)

        self.assertIn("filament already at rdm", str(ctx.exception).lower())
        instance._feed.assert_not_called()
        instance._stop_feed.assert_not_called()
        manager.state.set_and_save.assert_not_called()

    @patch('ace.instance.AceSerialManager')
    def test_feed_raises_when_sensor_never_triggers(self, mock_serial_mgr_class):
        """Error raised after exceeding maximum feed without sensor trigger."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._feed = Mock()
        instance._stop_feed = Mock()
        instance.dwell = Mock()
        instance.total_max_feeding_length = 60  # Force early failure

        manager = Mock()
        manager.get_switch_state = Mock(side_effect=[False, False, False, False, False, False])
        INSTANCE_MANAGERS[0] = manager

        with patch('ace.instance.time.time', side_effect=self._fake_time(step=1.1)):
            with self.assertRaises(ValueError) as ctx:
                instance._feed_filament_to_verification_sensor(3, SENSOR_TOOLHEAD, feed_length=50)

        self.assertIn("sensor not triggered", str(ctx.exception).lower())
        self.assertEqual(
            instance._feed.call_args_list,
            [
                call(3, 50, instance.feed_speed),
                call(3, instance.incremental_feeding_length, instance.incremental_feeding_speed),
            ],
        )
        instance._stop_feed.assert_called_once_with(3)
        manager.state.set_and_save.assert_not_called()


class TestExtruderMove(unittest.TestCase):
    """Test extruder move helper."""

    def setUp(self):
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_toolhead = Mock()

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0

        self.variables = {}
        self.mock_save_vars.allVariables = self.variables

        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    def _mock_lookup_object(self, name, default=None):
        if name == 'gcode':
            return self.mock_gcode
        if name == 'save_variables':
            return self.mock_save_vars
        if name == 'toolhead':
            return self.mock_toolhead
        return default

    @patch('ace.instance.AceSerialManager')
    def test_skips_zero_length_move(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        self.mock_toolhead.move = Mock()

        instance._extruder_move(0, 25, wait_for_move_end=True)

        self.mock_gcode.respond_info.assert_called_once()
        self.mock_toolhead.move.assert_not_called()

    @patch('ace.instance.AceSerialManager')
    def test_moves_without_wait_by_default(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        self.mock_toolhead.get_position.return_value = [1, 2, 3, 4]
        self.mock_toolhead.move = Mock()
        self.mock_toolhead.wait_moves = Mock()

        instance._extruder_move(5, 50)

        self.mock_toolhead.move.assert_called_once_with([1, 2, 3, 9], 50)
        self.mock_toolhead.wait_moves.assert_not_called()

    @patch('ace.instance.AceSerialManager')
    def test_moves_and_waits_when_requested(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        self.mock_toolhead.get_position.return_value = [10, 20, 30, 40]
        self.mock_toolhead.move = Mock()
        self.mock_toolhead.wait_moves = Mock()

        instance._extruder_move(3, 15, wait_for_move_end=True)

        self.mock_toolhead.move.assert_called_once_with([10, 20, 30, 43], 15)
        self.mock_toolhead.wait_moves.assert_called_once()


class TestSmartUnloadSlot(unittest.TestCase):
    """Branch coverage for _smart_unload_slot."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}
        self.mock_toolhead = Mock()

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._lookup
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.pause = Mock()

        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    def _lookup(self, name, default=None):
        return {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
            'toolhead': self.mock_toolhead,
        }.get(name, default)

    class _DummyMonitor:
        def __init__(self, timing=None):
            self.calls = 0
            self._timing = timing

        def __call__(self):
            self.calls += 1

        def get_timing(self):
            return self._timing

        def get_call_count(self):
            return self.calls

    @patch('ace.instance.AceSerialManager')
    def test_rdm_mode_clear_path_returns_true(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = True
        manager.get_switch_state.side_effect = lambda sensor: False  # both clear
        INSTANCE_MANAGERS[0] = manager

        monitor = self._DummyMonitor(timing=0.5)
        instance._make_sensor_trigger_monitor = Mock(return_value=monitor)
        instance._disable_feed_assist = Mock()
        instance._retract = Mock()
        instance.dwell = Mock()

        result = instance._smart_unload_slot(1, length=100)

        self.assertTrue(result)
        instance._disable_feed_assist.assert_called_once_with(1)
        instance._retract.assert_called_once_with(
            1, 100, instance.retract_speed, None, on_wait_for_ready=monitor
        )
        # Validation checked both sensors
        self.assertEqual(manager.get_switch_state.call_count, 2)

    @patch('ace.instance.AceSerialManager')
    def test_rdm_mode_extra_retract_and_rdm_recovery(self, mock_serial_mgr_class):
        """Late sensor trigger forces extra retract; RDM-assisted recovery succeeds."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = True
        manager.get_switch_state.side_effect = [True, True]  # blocked -> trigger fallback
        INSTANCE_MANAGERS[0] = manager

        monitor = self._DummyMonitor(timing=5.0)  # Efficiency >90%
        instance._make_sensor_trigger_monitor = Mock(return_value=monitor)
        instance._disable_feed_assist = Mock()
        instance._retract = Mock()
        instance.dwell = Mock()
        instance.rmd_triggered_unload_slot = Mock(return_value=True)

        result = instance._smart_unload_slot(2, length=120)

        self.assertTrue(result)
        # Initial retract + extra retract due to late sensor trigger
        self.assertEqual(
            instance._retract.call_args_list,
            [
                call(2, 120, instance.retract_speed, None, on_wait_for_ready=monitor),
                call(2, instance.parkposition_to_toolhead_length, instance.retract_speed / 2),
            ],
        )
        instance.rmd_triggered_unload_slot.assert_called_once_with(
            manager, 2, 120, instance.parkposition_to_rdm_length
        )

    @patch('ace.instance.AceSerialManager')
    def test_rdm_mode_raises_when_path_stays_blocked(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = True
        manager.get_switch_state.side_effect = [True, True]  # blocked
        INSTANCE_MANAGERS[0] = manager

        monitor = self._DummyMonitor(timing=None)  # No sensor timing recorded
        instance._make_sensor_trigger_monitor = Mock(return_value=monitor)
        instance._disable_feed_assist = Mock()
        instance._retract = Mock()
        instance.dwell = Mock()
        instance.rmd_triggered_unload_slot = Mock(return_value=False)
        instance._stop_retract = Mock()

        with self.assertRaises(ValueError):
            instance._smart_unload_slot(3, length=80)

        instance._stop_retract.assert_called_once_with(3)
        instance.rmd_triggered_unload_slot.assert_called_once()

    @patch('ace.instance.AceSerialManager')
    def test_no_rdm_toolhead_clear(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = False
        manager.get_switch_state.return_value = False
        INSTANCE_MANAGERS[0] = manager

        monitor = self._DummyMonitor(timing=None)
        instance._make_sensor_trigger_monitor = Mock(return_value=monitor)
        instance._disable_feed_assist = Mock()
        instance._retract = Mock()
        instance.dwell = Mock()

        result = instance._smart_unload_slot(0, length=60)

        self.assertTrue(result)
        manager.get_switch_state.assert_called_once_with(SENSOR_TOOLHEAD)

    @patch('ace.instance.AceSerialManager')
    def test_no_rdm_toolhead_blocked_returns_false(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = False
        manager.get_switch_state.return_value = True
        INSTANCE_MANAGERS[0] = manager

        monitor = self._DummyMonitor(timing=None)
        instance._make_sensor_trigger_monitor = Mock(return_value=monitor)
        instance._disable_feed_assist = Mock()
        instance._retract = Mock()
        instance.dwell = Mock()

        result = instance._smart_unload_slot(1, length=40)

        self.assertFalse(result)
        manager.get_switch_state.assert_called_once_with(SENSOR_TOOLHEAD)

    @patch('ace.instance.AceSerialManager')
    def test_smart_unload_unexpected_error_calls_stop_and_reraises(self, mock_serial_mgr_class):
        """Unexpected errors trigger stop_retract and bubble up."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = False
        manager.get_switch_state.return_value = False
        INSTANCE_MANAGERS[0] = manager

        monitor = self._DummyMonitor(timing=None)
        instance._make_sensor_trigger_monitor = Mock(return_value=monitor)
        instance._disable_feed_assist = Mock()
        instance._retract = Mock(side_effect=RuntimeError("boom"))
        instance._stop_retract = Mock()
        instance.dwell = Mock()

        with self.assertRaises(RuntimeError):
            instance._smart_unload_slot(2, length=50)

        instance._stop_retract.assert_called_once_with(2)


class TestRmdTriggeredUnloadSlot(unittest.TestCase):
    """Branch coverage for rmd_triggered_unload_slot."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)

        self.mock_reactor.pause = Mock()
        self.mock_reactor.monotonic.return_value = 0.0

        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    def _time_generator(self, start=0.0, step=0.5):
        current = start
        while True:
            current += step
            yield current

    @patch('ace.instance.AceSerialManager')
    def test_returns_false_when_no_rdm_sensor(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = False

        result = instance.rmd_triggered_unload_slot(manager, slot=0, length=50, overshoot_length=10)

        self.assertFalse(result)

    @patch('ace.instance.AceSerialManager')
    def test_successful_clear_stops_and_restores_feed_assist(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = True
        # Path becomes clear on third poll
        manager.is_filament_path_free.side_effect = [False, False, True]

        instance._disable_feed_assist = Mock()
        instance._get_current_feed_assist_index = Mock(return_value=2)
        instance._retract = Mock()
        instance._stop_retract = Mock()
        instance._update_feed_assist = Mock()
        instance.dwell = Mock()

        times = self._time_generator(step=0.4)
        with patch('ace.instance.time.time', side_effect=lambda: next(times)):
            result = instance.rmd_triggered_unload_slot(manager, slot=1, length=100, overshoot_length=20)

        self.assertTrue(result)
        instance._disable_feed_assist.assert_called_once_with(1)
        instance._retract.assert_called_once_with(1, 100, instance.retract_speed)
        instance._stop_retract.assert_called_once_with(1)
        instance._update_feed_assist.assert_called_once_with(2)
        # Overshoot dwell invoked once when clear detected
        instance.dwell.assert_called()

    @patch('ace.instance.AceSerialManager')
    def test_timeout_returns_false_and_restores_feed_assist(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = True
        manager.is_filament_path_free.return_value = False  # never clears

        instance._disable_feed_assist = Mock()
        instance._get_current_feed_assist_index = Mock(return_value=3)
        instance._retract = Mock()
        instance._stop_retract = Mock()
        instance._update_feed_assist = Mock()
        instance.dwell = Mock()

        times = self._time_generator(step=1.0)
        with patch('ace.instance.time.time', side_effect=lambda: next(times)):
            result = instance.rmd_triggered_unload_slot(manager, slot=2, length=50, overshoot_length=10)

        self.assertFalse(result)
        instance._stop_retract.assert_called_once_with(2)
        instance._update_feed_assist.assert_called_once_with(3)


class TestFeedFilamentWithWaitForResponse(unittest.TestCase):
    """Branch coverage for feed_filament_with_wait_for_response."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)

        self.mock_reactor.pause = Mock()
        self.mock_reactor.monotonic.return_value = 0.0

        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    def _time_generator(self, start=0.0, step=1.0):
        current = start
        while True:
            current += step
            yield current

    @patch('ace.instance.AceSerialManager')
    def test_success_returns_response(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance.wait_ready = Mock()
        instance.dwell = Mock()
        response = {"code": 0, "msg": "ok"}

        def send_request(req, cb):
            cb(response)

        instance.send_request = Mock(side_effect=send_request)

        result = instance.feed_filament_with_wait_for_response(1, 20, 30)

        instance.wait_ready.assert_called_once()
        instance.send_request.assert_called_once()
        self.assertEqual(result, response)

    @patch('ace.instance.AceSerialManager')
    def test_timeout_returns_error(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance.wait_ready = Mock()
        instance.dwell = Mock()
        instance.send_request = Mock()  # never triggers callback

        times = self._time_generator(step=2.5)
        with patch('ace.instance.time.time', side_effect=lambda: next(times)):
            result = instance.feed_filament_with_wait_for_response(0, 10, 5)

        self.assertEqual(result["code"], -1)
        self.assertIn("timeout", result["msg"].lower())
        instance.wait_ready.assert_called_once()
        instance.send_request.assert_called_once()
        instance.dwell.assert_called()  # polled during wait

    @patch('ace.instance.AceSerialManager')
    def test_done_without_response_uses_fallback(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance.wait_ready = Mock()
        instance.dwell = Mock()

        def send_request(req, cb):
            cb(None)  # done=True, response=None

        instance.send_request = Mock(side_effect=send_request)

        result = instance.feed_filament_with_wait_for_response(2, 15, 40)

        self.assertEqual(result, {"code": -1, "msg": "No response"})
        instance.wait_ready.assert_called_once()
        instance.send_request.assert_called_once()


class TestFeedToToolheadWithExtruderAssist(unittest.TestCase):
    """Branch coverage for _feed_to_toolhead_with_extruder_assist."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
        }.get(name, default)

        self.mock_reactor.pause = Mock()
        self.mock_reactor.monotonic.return_value = 0.0

        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
        }

    def _time_generator(self, start=0.0, step=0.2):
        current = start
        while True:
            current += step
            yield current

    @patch('ace.instance.AceSerialManager')
    def test_happy_path_stops_on_sensor_and_changes_speed(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.get_switch_state.side_effect = [False, True, True]
        INSTANCE_MANAGERS[0] = manager

        instance._disable_feed_assist = Mock()
        instance.execute_feed_with_retries = Mock()
        instance.dwell = Mock()
        instance._change_feed_speed = Mock(return_value=True)
        instance._extruder_move = Mock()
        instance._stop_feed = Mock()
        instance.wait_ready = Mock()
        instance._enable_feed_assist = Mock()

        times = self._time_generator(step=0.5)
        with patch('ace.instance.time.time', side_effect=lambda: next(times)):
            result = instance._feed_to_toolhead_with_extruder_assist(
                local_slot=1,
                feed_length=20,
                feed_speed=10,
                extruder_feeding_length=5,
                extruder_feeding_speed=2
            )

        self.assertEqual(result, instance.extruder_feeding_length)
        instance.execute_feed_with_retries.assert_called_once_with(1, 20, 10)
        instance._change_feed_speed.assert_called_once_with(1, 2)
        instance._extruder_move.assert_called_once_with(5, 2, wait_for_move_end=True)
        instance._stop_feed.assert_called_once_with(1)
        self.assertEqual(instance.wait_ready.call_count, 2)
        instance._enable_feed_assist.assert_called_once_with(1)

    @patch('ace.instance.AceSerialManager')
    def test_feed_assist_timeout_raises_when_sensor_never_triggers(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.get_switch_state.return_value = False  # never triggers
        INSTANCE_MANAGERS[0] = manager

        instance._disable_feed_assist = Mock()
        instance.execute_feed_with_retries = Mock()
        instance.dwell = Mock()
        instance._change_feed_speed = Mock()
        instance._extruder_move = Mock()
        instance._stop_feed = Mock()
        instance.wait_ready = Mock()
        instance._enable_feed_assist = Mock()

        times = self._time_generator(step=30.0)  # advance quickly to hit timeouts
        with patch('ace.instance.time.time', side_effect=lambda: next(times)):
            with self.assertRaises(ValueError):
                instance._feed_to_toolhead_with_extruder_assist(
                    local_slot=0,
                    feed_length=10,
                    feed_speed=5,
                    extruder_feeding_length=3,
                    extruder_feeding_speed=1
                )

        instance._change_feed_speed.assert_not_called()
        instance._stop_feed.assert_not_called()  # Stop only after speed change path
        instance._enable_feed_assist.assert_called()  # toggled during assist attempts

    @patch('ace.instance.AceSerialManager')
    def test_speed_change_failure_raises_after_retries(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.get_switch_state.side_effect = [False, True, True, True]
        INSTANCE_MANAGERS[0] = manager

        instance._disable_feed_assist = Mock()
        instance.execute_feed_with_retries = Mock()
        instance.dwell = Mock()
        instance._change_feed_speed = Mock(return_value=False)
        instance._extruder_move = Mock()
        instance._stop_feed = Mock()
        instance.wait_ready = Mock()
        instance._enable_feed_assist = Mock()

        times = self._time_generator(step=0.5)
        with patch('ace.instance.time.time', side_effect=lambda: next(times)):
            with self.assertRaises(ValueError):
                instance._feed_to_toolhead_with_extruder_assist(
                    local_slot=3,
                    feed_length=15,
                    feed_speed=8,
                    extruder_feeding_length=4,
                    extruder_feeding_speed=1.5
                )

        # Three attempts made then stop + raise
        self.assertEqual(instance._change_feed_speed.call_count, 3)
        instance._stop_feed.assert_called_once_with(3)
        instance._extruder_move.assert_not_called()


if __name__ == '__main__':
    unittest.main()
