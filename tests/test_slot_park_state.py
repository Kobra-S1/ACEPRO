"""
Tests for the per-slot park state exposed as slots[i]["park_state"].

The state records where the driver last left each slot's filament:
- "unknown": startup, spool change, or any feed/retract not yet confirmed
- "parked":  retracted to the park position by a sensor-verified unload/smart load
- "loaded":  fed through the path to the nozzle by a completed tool load
"""

import unittest
from unittest.mock import Mock, patch, PropertyMock

from ace.instance import AceInstance
from ace.config import (
    ACE_INSTANCES,
    INSTANCE_MANAGERS,
    SLOTS_PER_ACE,
    SLOT_PARK_STATE_UNKNOWN,
    SLOT_PARK_STATE_PARKED,
    SLOT_PARK_STATE_LOADED,
    SLOT_PARK_STATES,
)


class TestSlotParkState(unittest.TestCase):

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_toolhead = Mock()
        self.mock_save_vars = Mock()
        self.mock_save_vars.allVariables = {}

        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = lambda name, default=None: {
            'gcode': self.mock_gcode,
            'save_variables': self.mock_save_vars,
            'toolhead': self.mock_toolhead,
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
            'rdm_overshoot_length': 50.0,
        }

    def _time_generator(self, start=0.0, step=0.5):
        current = start
        while True:
            current += step
            yield current

    # --- basics ----------------------------------------------------------------

    @patch('ace.instance.AceSerialManager')
    def test_defaults_to_unknown_and_is_exposed_in_status(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)

        self.assertEqual(instance.slot_park_state, [SLOT_PARK_STATE_UNKNOWN] * SLOTS_PER_ACE)
        slots = instance.get_status()["slots"]
        self.assertEqual(len(slots), SLOTS_PER_ACE)
        for slot in slots:
            self.assertEqual(slot["park_state"], SLOT_PARK_STATE_UNKNOWN)

    @patch('ace.instance.AceSerialManager')
    def test_status_reflects_each_slot_independently(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance.set_slot_park_state(1, SLOT_PARK_STATE_PARKED)
        instance.set_slot_park_state(2, SLOT_PARK_STATE_LOADED)

        states = [s["park_state"] for s in instance.get_status()["slots"]]
        self.assertEqual(states, [SLOT_PARK_STATE_UNKNOWN, SLOT_PARK_STATE_PARKED,
                                  SLOT_PARK_STATE_LOADED, SLOT_PARK_STATE_UNKNOWN])

    @patch('ace.instance.AceSerialManager')
    def test_rejects_invalid_state_and_ignores_out_of_range_slot(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)

        with self.assertRaises(ValueError):
            instance.set_slot_park_state(0, "somewhere")
        instance.set_slot_park_state(SLOTS_PER_ACE, SLOT_PARK_STATE_PARKED)
        instance.set_slot_park_state(-1, SLOT_PARK_STATE_PARKED)
        self.assertEqual(instance.slot_park_state, [SLOT_PARK_STATE_UNKNOWN] * SLOTS_PER_ACE)
        self.assertEqual(set(SLOT_PARK_STATES),
                         {SLOT_PARK_STATE_UNKNOWN, SLOT_PARK_STATE_PARKED, SLOT_PARK_STATE_LOADED})

    # --- every motion request invalidates the known position ---------------------

    @patch('ace.instance.AceSerialManager')
    def test_feed_request_marks_slot_unknown(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._ensure_feed_assist_off_for_motion = Mock()
        instance.send_request = Mock()
        instance.set_slot_park_state(1, SLOT_PARK_STATE_PARKED)

        instance._feed(1, 20, 50)

        self.assertEqual(instance.slot_park_state[1], SLOT_PARK_STATE_UNKNOWN)
        instance.send_request.assert_called_once()

    @patch('ace.instance.AceSerialManager')
    def test_retract_request_marks_slot_unknown(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance._ensure_feed_assist_off_for_motion = Mock()
        instance.wait_ready = Mock()
        instance._is_slot_empty = Mock(return_value=True)  # take the early "slot empty" exit
        instance.set_slot_park_state(2, SLOT_PARK_STATE_LOADED)

        instance._retract(2, 100, 50)

        self.assertEqual(instance.slot_park_state[2], SLOT_PARK_STATE_UNKNOWN)

    # --- sensor-verified operations set a known position ---------------------------

    @patch('ace.instance.AceSerialManager')
    def test_rdm_triggered_unload_success_marks_parked(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = True
        manager.get_instant_switch_state = Mock(return_value=False)  # RDM clears
        instance._disable_feed_assist = Mock()
        instance._get_current_feed_assist_index = Mock(return_value=-1)
        instance._update_feed_assist = Mock()

        def mock_retract(slot, length, speed, early_stop_callback=None):
            instance.set_slot_park_state(slot, SLOT_PARK_STATE_UNKNOWN)  # like the real _retract
            if early_stop_callback:
                early_stop_callback()
            return {'code': 0, 'msg': 'OK'}
        instance._retract = Mock(side_effect=mock_retract)

        times = self._time_generator(step=0.4)
        with patch('ace.instance.time.time', side_effect=lambda: next(times)):
            result = instance.rmd_triggered_unload_slot(manager, slot=1, length=100, overshoot_length=20)

        self.assertTrue(result)
        self.assertEqual(instance.slot_park_state[1], SLOT_PARK_STATE_PARKED)

    @patch('ace.instance.AceSerialManager')
    def test_rdm_triggered_unload_failure_does_not_mark_parked(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = True
        manager.get_instant_switch_state = Mock(return_value=True)  # RDM never clears
        instance._disable_feed_assist = Mock()
        instance._get_current_feed_assist_index = Mock(return_value=-1)
        instance._update_feed_assist = Mock()

        def mock_retract(slot, length, speed, early_stop_callback=None):
            instance.set_slot_park_state(slot, SLOT_PARK_STATE_UNKNOWN)
            return {'code': 0, 'msg': 'OK'}
        instance._retract = Mock(side_effect=mock_retract)

        times = self._time_generator(step=1.0)
        with patch('ace.instance.time.time', side_effect=lambda: next(times)):
            result = instance.rmd_triggered_unload_slot(manager, slot=2, length=50, overshoot_length=10)

        self.assertFalse(result)
        self.assertEqual(instance.slot_park_state[2], SLOT_PARK_STATE_UNKNOWN)

    @patch('ace.instance.AceSerialManager')
    def test_rdm_triggered_unload_on_empty_slot_is_not_parked(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = True
        manager.get_instant_switch_state = Mock(return_value=True)
        instance._disable_feed_assist = Mock()
        instance._get_current_feed_assist_index = Mock(return_value=-1)
        instance._update_feed_assist = Mock()

        def mock_retract(slot, length, speed, early_stop_callback=None):
            instance.set_slot_park_state(slot, SLOT_PARK_STATE_UNKNOWN)
            return {'code': 0, 'msg': 'Retract skipped: slot empty'}
        instance._retract = Mock(side_effect=mock_retract)

        result = instance.rmd_triggered_unload_slot(manager, slot=0, length=50, overshoot_length=10)

        self.assertTrue(result)
        self.assertEqual(instance.slot_park_state[0], SLOT_PARK_STATE_UNKNOWN)

    @patch('ace.instance.AceSerialManager')
    def test_completed_tool_load_marks_loaded(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = False
        manager.get_switch_state.return_value = False
        instance.wait_ready = Mock()
        instance._extruder_move = Mock()

        def mock_feed_to_toolhead(local_slot, *args):
            instance.set_slot_park_state(local_slot, SLOT_PARK_STATE_UNKNOWN)  # feeds go through _feed
            return 50
        instance._feed_to_toolhead_with_extruder_assist = Mock(side_effect=mock_feed_to_toolhead)

        with patch.object(AceInstance, 'manager', new_callable=PropertyMock, return_value=manager):
            instance._feed_filament_into_toolhead(3)

        self.assertEqual(instance.slot_park_state[3], SLOT_PARK_STATE_LOADED)

    @patch('ace.instance.AceSerialManager')
    def test_failed_tool_load_is_not_marked_loaded(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        manager = Mock()
        manager.has_rdm_sensor.return_value = False
        manager.get_switch_state.return_value = False
        instance.wait_ready = Mock()
        instance._retract = Mock(side_effect=lambda slot, *a, **k: instance.set_slot_park_state(
            slot, SLOT_PARK_STATE_UNKNOWN))
        instance._feed_to_toolhead_with_extruder_assist = Mock(side_effect=ValueError("sensor timeout"))
        instance.set_slot_park_state(0, SLOT_PARK_STATE_PARKED)

        with patch.object(AceInstance, 'manager', new_callable=PropertyMock, return_value=manager):
            with self.assertRaises(ValueError):
                instance._feed_filament_into_toolhead(0)

        self.assertEqual(instance.slot_park_state[0], SLOT_PARK_STATE_UNKNOWN)

    # --- spool changes reported by the ACE -----------------------------------------

    @patch('ace.instance.AceSerialManager')
    def test_slot_status_change_resets_to_unknown(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        instance.inventory[0].update({'status': 'ready', 'material': 'PLA', 'color': [1, 2, 3], 'temp': 210})
        instance.inventory[1].update({'status': 'ready', 'material': 'PLA', 'color': [1, 2, 3], 'temp': 210})
        instance.set_slot_park_state(0, SLOT_PARK_STATE_PARKED)
        instance.set_slot_park_state(1, SLOT_PARK_STATE_PARKED)

        response = {'result': {'slots': [
            {'index': 0, 'status': 'empty'},   # spool ran out / removed
            {'index': 1, 'status': 'ready'},   # unchanged
        ]}}
        instance._status_update_callback(response)

        self.assertEqual(instance.slot_park_state[0], SLOT_PARK_STATE_UNKNOWN)
        self.assertEqual(instance.slot_park_state[1], SLOT_PARK_STATE_PARKED)


if __name__ == '__main__':
    unittest.main()
