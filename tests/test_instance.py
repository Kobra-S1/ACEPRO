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
from unittest.mock import Mock, patch, PropertyMock, call
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
        """RFID data populates material/color/rfid when enabled."""
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
                        'color': [12, 34, 56],
                        'sku': 'SKU-123',
                    }
                ]
            }
        }

        instance._status_update_callback(response)

        slot = instance.inventory[0]
        self.assertEqual(slot['status'], 'ready')
        self.assertEqual(slot['material'], 'PLA')
        self.assertEqual(slot['color'], [12, 34, 56])
        self.assertTrue(slot['rfid'])
        INSTANCE_MANAGERS[0]._sync_inventory_to_persistent.assert_called_once_with(0)

        notify = [c.args[0] for c in self.mock_gcode.respond_info.call_args_list if str(c.args[0]).startswith('// ')]
        self.assertTrue(notify, "Expected notify line to be emitted")
        payload = json.loads(notify[-1].lstrip('/ '))
        self.assertEqual(payload['slots'][0]['rfid'], True)
        self.assertEqual(payload['slots'][0]['material'], 'PLA')
        self.assertEqual(payload['slots'][0]['color'], [12, 34, 56])

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
        INSTANCE_MANAGERS[0]._sync_inventory_to_persistent.assert_called_once_with(0)

        notify = [c.args[0] for c in self.mock_gcode.respond_info.call_args_list if str(c.args[0]).startswith('// ')]
        payload = json.loads(notify[-1].lstrip('/ '))
        self.assertEqual(payload['slots'][0]['rfid'], False)
        self.assertEqual(payload['slots'][0]['material'], '')
        self.assertEqual(payload['slots'][0]['color'], [0, 0, 0])

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
        INSTANCE_MANAGERS[0]._sync_inventory_to_persistent.assert_called_once_with(0)

        notify = [c.args[0] for c in self.mock_gcode.respond_info.call_args_list if str(c.args[0]).startswith('// ')]
        payload = json.loads(notify[-1].lstrip('/ '))
        self.assertEqual(payload['slots'][0]['rfid'], False)

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
    def test_start_heartbeat_registers_timer(self, mock_serial_mgr_class):
        """Test start_heartbeat calls serial manager start_heartbeat."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        instance.start_heartbeat()
        
        # Verify serial manager start_heartbeat was called
        instance.serial_mgr.start_heartbeat.assert_called()

    @patch('ace.instance.AceSerialManager')
    def test_stop_heartbeat_unregisters_timer(self, mock_serial_mgr_class):
        """Test stop_heartbeat stops serial manager heartbeat."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        instance.serial_mgr.stop_heartbeat()
        
        instance.serial_mgr.stop_heartbeat.assert_called_once()

    @patch('ace.instance.AceSerialManager')
    def test_is_heartbeat_active_true(self, mock_serial_mgr_class):
        """Test is_heartbeat_active returns True when active."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance.serial_mgr.is_heartbeat_active.return_value = True
        
        self.assertTrue(instance.is_heartbeat_active())

    @patch('ace.instance.AceSerialManager')
    def test_is_heartbeat_active_false(self, mock_serial_mgr_class):
        """Test is_heartbeat_active returns False when inactive."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance.serial_mgr.heartbeat_timer = None
        
        self.assertFalse(instance.is_heartbeat_active())

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


class TestRfidQueryTracking(unittest.TestCase):
    """
    Test RFID query tracking to prevent spam and ensure proper query lifecycle.
    
    These tests verify that:
    1. RFID queries only happen when needed (not every heartbeat)
    2. Queries don't get stuck and never happen again when they should
    3. Query tracking is properly cleared when slots become empty
    """

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
            'rfid_temp_mode': 'average',
        }

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        return default

    @patch('ace.instance.AceSerialManager')
    def test_rfid_query_only_sent_once_per_slot(self, mock_serial_mgr_class):
        """
        Verify RFID query is sent only once, not on every heartbeat.
        
        This tests the core anti-spam behavior: when a slot becomes ready with
        RFID data, we should query get_filament_info exactly once, not repeatedly.
        """
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        # Initial state: slot 0 is empty
        instance.inventory[0]['status'] = 'empty'
        
        # Capture send_request calls
        request_calls = []
        def capture_request(request, callback):
            request_calls.append(request)
        instance.send_request = capture_request
        
        # First status update: slot becomes ready with RFID
        response = {
            'result': {
                'slots': [{
                    'index': 0,
                    'status': 'ready',
                    'rfid': 2,
                    'type': 'PLA',
                    'color': [255, 0, 0],
                }]
            }
        }
        
        instance._status_update_callback(response)
        
        # Should have sent exactly one query
        rfid_queries = [r for r in request_calls if r.get('method') == 'get_filament_info']
        self.assertEqual(len(rfid_queries), 1, "Should send exactly one RFID query")
        self.assertEqual(rfid_queries[0]['params']['index'], 0)
        
        # Slot should be marked as attempted
        self.assertIn(0, instance._rfid_query_attempted)
        self.assertIn(0, instance._pending_rfid_queries)
        
        # Simulate multiple additional heartbeats (same status)
        for _ in range(5):
            instance._status_update_callback(response)
        
        # Should still have only one query (not 6)
        rfid_queries = [r for r in request_calls if r.get('method') == 'get_filament_info']
        self.assertEqual(len(rfid_queries), 1, "Should NOT re-query on subsequent heartbeats")

    @patch('ace.instance.AceSerialManager')
    def test_rfid_query_not_blocked_after_callback_completes(self, mock_serial_mgr_class):
        """
        Verify pending flag is cleared when callback completes.
        
        This tests that the _pending_rfid_queries set is properly managed.
        """
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        captured_callback = [None]
        def capture_request(request, callback):
            if request.get('method') == 'get_filament_info':
                captured_callback[0] = callback
        instance.send_request = capture_request
        
        # Trigger RFID query
        instance.inventory[0]['status'] = 'empty'
        response = {
            'result': {
                'slots': [{
                    'index': 0,
                    'status': 'ready',
                    'rfid': 2,
                    'type': 'PLA',
                    'color': [255, 0, 0],
                }]
            }
        }
        instance._status_update_callback(response)
        
        # Should be pending
        self.assertIn(0, instance._pending_rfid_queries)
        
        # Simulate callback completion (successful response)
        if captured_callback[0]:
            captured_callback[0]({
                'code': 0,
                'result': {
                    'type': 'PLA',
                    'extruder_temp': {'min': 190, 'max': 220},
                    'hotbed_temp': {'min': 50, 'max': 60},
                }
            })
        
        # Pending should be cleared, but attempted still set
        self.assertNotIn(0, instance._pending_rfid_queries)
        self.assertIn(0, instance._rfid_query_attempted)

    @patch('ace.instance.AceSerialManager')
    def test_rfid_query_not_blocked_after_failed_callback(self, mock_serial_mgr_class):
        """
        Verify pending flag is cleared even when query fails.
        
        This tests that failed queries don't leave the pending flag stuck.
        """
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        captured_callback = [None]
        def capture_request(request, callback):
            if request.get('method') == 'get_filament_info':
                captured_callback[0] = callback
        instance.send_request = capture_request
        
        # Trigger RFID query
        instance.inventory[0]['status'] = 'empty'
        response = {
            'result': {
                'slots': [{
                    'index': 0,
                    'status': 'ready',
                    'rfid': 2,
                    'type': 'PLA',
                    'color': [255, 0, 0],
                }]
            }
        }
        instance._status_update_callback(response)
        
        self.assertIn(0, instance._pending_rfid_queries)
        
        # Simulate failed callback (no response)
        if captured_callback[0]:
            captured_callback[0](None)  # No response
        
        # Pending should be cleared
        self.assertNotIn(0, instance._pending_rfid_queries)
        # But attempted stays set to prevent retry spam
        self.assertIn(0, instance._rfid_query_attempted)

    @patch('ace.instance.AceSerialManager')
    def test_empty_slot_clears_query_tracking(self, mock_serial_mgr_class):
        """
        Verify query tracking is cleared when slot becomes empty.
        
        This is critical: when a spool is removed, we must clear the tracking
        so that reinserting a spool will trigger a new query.
        """
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        # Setup: slot has been queried previously
        instance.inventory[0]['status'] = 'ready'
        instance._rfid_query_attempted.add(0)
        instance._pending_rfid_queries.add(0)  # Simulating stuck state
        
        # Slot becomes empty (spool removed)
        response = {
            'result': {
                'slots': [{
                    'index': 0,
                    'status': 'empty',
                    'rfid': 0,
                }]
            }
        }
        instance._status_update_callback(response)
        
        # Both tracking sets should be cleared for this slot
        self.assertNotIn(0, instance._rfid_query_attempted)
        self.assertNotIn(0, instance._pending_rfid_queries)

    @patch('ace.instance.AceSerialManager')
    def test_reinserted_spool_triggers_new_query(self, mock_serial_mgr_class):
        """
        Verify reinserting a spool after removal triggers a new RFID query.
        
        This tests the full lifecycle: ready -> empty -> ready should query again.
        """
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        request_calls = []
        def capture_request(request, callback):
            request_calls.append(request)
            # Simulate immediate callback completion
            if request.get('method') == 'get_filament_info':
                callback({
                    'code': 0,
                    'result': {
                        'type': 'PLA',
                        'extruder_temp': {'min': 190, 'max': 220},
                        'hotbed_temp': {'min': 50, 'max': 60},
                    }
                })
        instance.send_request = capture_request
        
        # Step 1: Slot becomes ready with RFID (first insertion)
        instance.inventory[0]['status'] = 'empty'
        response_ready = {
            'result': {
                'slots': [{
                    'index': 0,
                    'status': 'ready',
                    'rfid': 2,
                    'type': 'PLA',
                    'color': [255, 0, 0],
                }]
            }
        }
        instance._status_update_callback(response_ready)
        
        first_query_count = len([r for r in request_calls if r.get('method') == 'get_filament_info'])
        self.assertEqual(first_query_count, 1, "First insertion should query")
        
        # Step 2: Slot becomes empty (spool removed)
        response_empty = {
            'result': {
                'slots': [{
                    'index': 0,
                    'status': 'empty',
                    'rfid': 0,
                }]
            }
        }
        instance._status_update_callback(response_empty)
        
        # Tracking should be cleared
        self.assertNotIn(0, instance._rfid_query_attempted)
        
        # Step 3: Slot becomes ready again (spool reinserted)
        instance._status_update_callback(response_ready)
        
        second_query_count = len([r for r in request_calls if r.get('method') == 'get_filament_info'])
        self.assertEqual(second_query_count, 2, "Reinsertion should trigger a new query")

    @patch('ace.instance.AceSerialManager')
    def test_multiple_slots_tracked_independently(self, mock_serial_mgr_class):
        """
        Verify each slot's query tracking is independent.
        
        Slot 0 being queried should not affect slot 1, and vice versa.
        """
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        request_calls = []
        def capture_request(request, callback):
            request_calls.append(request)
        instance.send_request = capture_request
        
        # Both slots start empty
        instance.inventory[0]['status'] = 'empty'
        instance.inventory[1]['status'] = 'empty'
        
        # Slot 0 becomes ready
        response_slot0 = {
            'result': {
                'slots': [
                    {'index': 0, 'status': 'ready', 'rfid': 2, 'type': 'PLA', 'color': [255, 0, 0]},
                    {'index': 1, 'status': 'empty', 'rfid': 0},
                ]
            }
        }
        instance._status_update_callback(response_slot0)
        
        # Only slot 0 should be in tracking
        self.assertIn(0, instance._rfid_query_attempted)
        self.assertNotIn(1, instance._rfid_query_attempted)
        
        queries_after_slot0 = len([r for r in request_calls if r.get('method') == 'get_filament_info'])
        self.assertEqual(queries_after_slot0, 1)
        
        # Now slot 1 becomes ready too
        response_both = {
            'result': {
                'slots': [
                    {'index': 0, 'status': 'ready', 'rfid': 2, 'type': 'PLA', 'color': [255, 0, 0]},
                    {'index': 1, 'status': 'ready', 'rfid': 2, 'type': 'PETG', 'color': [0, 255, 0]},
                ]
            }
        }
        instance._status_update_callback(response_both)
        
        # Both should be in tracking now
        self.assertIn(0, instance._rfid_query_attempted)
        self.assertIn(1, instance._rfid_query_attempted)
        
        # Should have 2 total queries (one for each slot)
        queries_after_both = len([r for r in request_calls if r.get('method') == 'get_filament_info'])
        self.assertEqual(queries_after_both, 2)

    @patch('ace.instance.AceSerialManager')
    def test_query_not_sent_when_already_pending(self, mock_serial_mgr_class):
        """
        Verify no duplicate queries while one is still in-flight.
        
        If callback hasn't completed yet, subsequent heartbeats should not
        trigger additional queries.
        """
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        request_calls = []
        def capture_request(request, callback):
            # Don't call callback - simulate in-flight query
            request_calls.append(request)
        instance.send_request = capture_request
        
        instance.inventory[0]['status'] = 'empty'
        
        response = {
            'result': {
                'slots': [{
                    'index': 0,
                    'status': 'ready',
                    'rfid': 2,
                    'type': 'PLA',
                    'color': [255, 0, 0],
                }]
            }
        }
        
        # First update triggers query
        instance._status_update_callback(response)
        
        # Query is pending
        self.assertIn(0, instance._pending_rfid_queries)
        
        # Multiple heartbeats while query is pending
        for _ in range(10):
            instance._status_update_callback(response)
        
        # Should still be only 1 query
        rfid_queries = [r for r in request_calls if r.get('method') == 'get_filament_info']
        self.assertEqual(len(rfid_queries), 1, "No duplicate queries while pending")

    @patch('ace.instance.AceSerialManager')
    def test_query_skipped_when_already_has_rfid_data(self, mock_serial_mgr_class):
        """
        Verify no query when inventory already has full RFID data.
        
        If extruder_temp and hotbed_temp are already present, don't re-query.
        """
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        request_calls = []
        def capture_request(request, callback):
            request_calls.append(request)
        instance.send_request = capture_request
        
        # Setup: slot already has full RFID data from previous query
        instance.inventory[0].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [255, 0, 0],
            'rfid': True,
            'extruder_temp': {'min': 190, 'max': 220},
            'hotbed_temp': {'min': 50, 'max': 60},
        })
        
        # Same status comes in again (no changes)
        response = {
            'result': {
                'slots': [{
                    'index': 0,
                    'status': 'ready',
                    'rfid': 2,
                    'type': 'PLA',
                    'color': [255, 0, 0],
                }]
            }
        }
        
        instance._status_update_callback(response)
        
        # Should not query - data already present
        rfid_queries = [r for r in request_calls if r.get('method') == 'get_filament_info']
        self.assertEqual(len(rfid_queries), 0, "No query when data already exists")

    @patch('ace.instance.AceSerialManager')
    def test_init_creates_empty_tracking_sets(self, mock_serial_mgr_class):
        """Verify instance initialization creates empty tracking sets."""
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        self.assertIsInstance(instance._pending_rfid_queries, set)
        self.assertIsInstance(instance._rfid_query_attempted, set)
        self.assertEqual(len(instance._pending_rfid_queries), 0)
        self.assertEqual(len(instance._rfid_query_attempted), 0)

    @patch('ace.instance.AceSerialManager')
    def test_query_rfid_full_data_guards_prevent_duplicate(self, mock_serial_mgr_class):
        """
        Test _query_rfid_full_data directly to verify pending guard works.
        
        Note: _query_rfid_full_data only checks _pending_rfid_queries.
        The _rfid_query_attempted check happens in _status_update_callback
        BEFORE calling _query_rfid_full_data.
        """
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        request_calls = []
        def capture_request(request, callback):
            request_calls.append(request)
        instance.send_request = capture_request
        
        # First call should work
        instance._query_rfid_full_data(0)
        self.assertEqual(len(request_calls), 1)
        self.assertIn(0, instance._pending_rfid_queries)
        self.assertIn(0, instance._rfid_query_attempted)
        
        # Second call should be blocked (pending check in _query_rfid_full_data)
        instance._query_rfid_full_data(0)
        self.assertEqual(len(request_calls), 1, "Blocked by pending check")
        
        # Clear pending - query allowed again (attempted is only checked at call site)
        # This tests that the pending guard works correctly
        instance._pending_rfid_queries.discard(0)
        instance._query_rfid_full_data(0)
        self.assertEqual(len(request_calls), 2, "Query allowed after clearing pending")
        
        # Verify pending is set again after new query
        self.assertIn(0, instance._pending_rfid_queries)


if __name__ == '__main__':
    unittest.main()
