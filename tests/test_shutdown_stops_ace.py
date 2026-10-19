"""
Tests for stopping ACE-side activity on a Klipper shutdown (M112 / emergency
stop, fatal MCU error).

A shutdown halts every Klipper MCU, but the ACE is a separate USB device: an
active feed assist kept pushing filament into the halted toolhead, and the
driver restored it again after FIRMWARE_RESTART. On shutdown the driver now
queues stop requests for feed assist, any feed/retract the ACE reports as
running and, optionally, the dryer.
"""

import unittest
from unittest.mock import Mock, patch

from ace.instance import AceInstance
from ace.manager import AceManager
from ace.config import ACE_INSTANCES, INSTANCE_MANAGERS, read_ace_config
from ace.persistent_state import PersistentState


class TestStopActivityOnShutdown(unittest.TestCase):

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

        # Real PersistentState over the mocked save_variables dict.
        self.mock_manager = Mock()
        self.mock_manager.state = PersistentState(self.mock_printer, self.mock_gcode)
        INSTANCE_MANAGERS[0] = self.mock_manager

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

    def tearDown(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

    def _make_instance(self, **overrides):
        config = dict(self.ace_config, **overrides)
        instance = AceInstance(0, config, self.mock_printer)
        instance.serial_mgr.is_connected.return_value = True
        instance._info["slots"] = [
            {"index": i, "status": "ready"} for i in range(4)
        ]
        return instance

    def _sent_methods(self, instance):
        sent = instance.serial_mgr.send_high_prio_request.call_args_list
        return [(c.args[0]["method"], c.args[0].get("params", {})) for c in sent]

    # --- defaults ------------------------------------------------------------

    @patch('ace.instance.AceSerialManager')
    def test_defaults(self, mock_serial_mgr_class):
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        self.assertTrue(instance.stop_ace_on_shutdown)
        self.assertFalse(instance.stop_dryer_on_shutdown)

    @patch('ace.instance.AceSerialManager')
    def test_nothing_active_sends_nothing(self, mock_serial_mgr_class):
        instance = self._make_instance()

        self.assertEqual(instance.stop_activity_on_shutdown(), [])
        instance.serial_mgr.send_high_prio_request.assert_not_called()

    # --- feed assist -----------------------------------------------------------

    @patch('ace.instance.AceSerialManager')
    def test_stops_active_feed_assist_and_forgets_it(self, mock_serial_mgr_class):
        instance = self._make_instance()
        instance._feed_assist_index = 2
        self.mock_save_vars.allVariables["ace_feed_assist_index_0"] = 2

        stopped = instance.stop_activity_on_shutdown()

        self.assertEqual(stopped, ["feed assist on slot 2"])
        self.assertEqual(self._sent_methods(instance),
                         [("stop_feed_assist", {"index": 2})])
        self.assertEqual(instance._feed_assist_index, -1)
        # Persisted as -1 so the reconnect after FIRMWARE_RESTART does not
        # restore it.
        self.assertEqual(self.mock_save_vars.allVariables["ace_feed_assist_index_0"], -1)
        self.assertIn("ace_feed_assist_index_0", instance.state._dirty)

    @patch('ace.instance.AceSerialManager')
    def test_does_not_wait_or_dwell(self, mock_serial_mgr_class):
        instance = self._make_instance()
        instance._feed_assist_index = 0
        instance._info["status"] = "busy"

        instance.stop_activity_on_shutdown()

        self.mock_reactor.pause.assert_not_called()
        self.mock_gcode.run_script_from_command.assert_not_called()

    # --- running feed / retract ------------------------------------------------

    @patch('ace.instance.AceSerialManager')
    def test_stops_running_feed_and_retract(self, mock_serial_mgr_class):
        instance = self._make_instance()
        instance._info["slots"][1]["status"] = "feeding"
        instance._info["slots"][3]["status"] = "unwinding"

        stopped = instance.stop_activity_on_shutdown()

        self.assertEqual(stopped, ["feed on slot 1", "retract on slot 3"])
        self.assertEqual(self._sent_methods(instance), [
            ("stop_feed_filament", {"index": 1}),
            ("stop_unwind_filament", {"index": 3}),
        ])

    @patch('ace.instance.AceSerialManager')
    def test_feed_assist_slot_reported_feeding_is_stopped_once(self, mock_serial_mgr_class):
        instance = self._make_instance()
        instance._feed_assist_index = 1
        instance._info["slots"][1]["status"] = "feeding"

        instance.stop_activity_on_shutdown()

        self.assertEqual(self._sent_methods(instance),
                         [("stop_feed_assist", {"index": 1})])

    # --- dryer -----------------------------------------------------------------

    @patch('ace.instance.AceSerialManager')
    def test_dryer_left_running_by_default(self, mock_serial_mgr_class):
        instance = self._make_instance()
        instance._info["dryer_status"] = {"status": "drying"}

        self.assertEqual(instance.stop_activity_on_shutdown(), [])
        instance.serial_mgr.send_high_prio_request.assert_not_called()

    @patch('ace.instance.AceSerialManager')
    def test_dryer_stopped_when_enabled(self, mock_serial_mgr_class):
        instance = self._make_instance(stop_dryer_on_shutdown=True)
        instance._info["dryer_status"] = {"status": "drying"}

        self.assertEqual(instance.stop_activity_on_shutdown(), ["dryer"])
        self.assertEqual([m for m, _ in self._sent_methods(instance)], ["drying_stop"])

    @patch('ace.instance.AceSerialManager')
    def test_idle_dryer_not_stopped_when_enabled(self, mock_serial_mgr_class):
        instance = self._make_instance(stop_dryer_on_shutdown=True)
        instance._info["dryer_status"] = {"status": "stop"}

        self.assertEqual(instance.stop_activity_on_shutdown(), [])

    # --- guards ------------------------------------------------------------------

    @patch('ace.instance.AceSerialManager')
    def test_disabled_by_config(self, mock_serial_mgr_class):
        instance = self._make_instance(stop_ace_on_shutdown=False)
        instance._feed_assist_index = 0
        instance._info["slots"][2]["status"] = "feeding"

        self.assertEqual(instance.stop_activity_on_shutdown(), [])
        instance.serial_mgr.send_high_prio_request.assert_not_called()
        self.assertEqual(instance._feed_assist_index, 0)

    @patch('ace.instance.AceSerialManager')
    def test_not_connected_sends_nothing(self, mock_serial_mgr_class):
        instance = self._make_instance()
        instance._feed_assist_index = 0
        instance.serial_mgr.is_connected.return_value = False

        self.assertEqual(instance.stop_activity_on_shutdown(), [])
        instance.serial_mgr.send_high_prio_request.assert_not_called()

    @patch('ace.instance.AceSerialManager')
    def test_queue_error_does_not_raise(self, mock_serial_mgr_class):
        instance = self._make_instance()
        instance._feed_assist_index = 0
        instance._info["slots"][2]["status"] = "unwinding"
        instance.serial_mgr.send_high_prio_request.side_effect = [
            RuntimeError("queue broken"), None,
        ]

        self.assertEqual(instance.stop_activity_on_shutdown(), ["retract on slot 2"])

    @patch('ace.instance.AceSerialManager')
    def test_callbacks_handle_success_failure_and_timeout(self, mock_serial_mgr_class):
        instance = self._make_instance()
        instance._feed_assist_index = 0

        instance.stop_activity_on_shutdown()

        callback = instance.serial_mgr.send_high_prio_request.call_args.args[1]
        callback({"code": 0})
        callback({"code": 1, "msg": "FORBIDDEN"})
        callback(None)


class TestManagerShutdownHandler(unittest.TestCase):

    def _make_manager(self, instances):
        manager = AceManager.__new__(AceManager)
        manager.instances = instances
        manager.gcode = Mock()
        manager.state = Mock()
        manager.state._dirty = set()
        return manager

    def test_stops_every_instance_before_flushing(self):
        order = []
        inst0 = Mock(instance_num=0)
        inst0.stop_activity_on_shutdown.side_effect = lambda: order.append("stop0") or ["feed assist on slot 1"]
        inst1 = Mock(instance_num=1)
        inst1.stop_activity_on_shutdown.side_effect = lambda: order.append("stop1") or []
        manager = self._make_manager([inst0, inst1])
        manager.state.flush_direct.side_effect = lambda: order.append("flush")

        manager._handle_shutdown()

        self.assertEqual(order, ["stop0", "stop1", "flush"])
        manager.gcode.respond_info.assert_called_once_with(
            "ACE[0]: Shutdown - stopping feed assist on slot 1"
        )

    def test_instance_error_does_not_block_flush(self):
        inst0 = Mock(instance_num=0)
        inst0.stop_activity_on_shutdown.side_effect = RuntimeError("boom")
        inst1 = Mock(instance_num=1)
        inst1.stop_activity_on_shutdown.return_value = []
        manager = self._make_manager([inst0, inst1])

        manager._handle_shutdown()

        inst1.stop_activity_on_shutdown.assert_called_once()
        manager.state.flush_direct.assert_called_once()


def test_read_ace_config_shutdown_defaults(mock_config):
    ace_config = read_ace_config(mock_config)
    assert ace_config["stop_ace_on_shutdown"] is True
    assert ace_config["stop_dryer_on_shutdown"] is False


if __name__ == "__main__":
    unittest.main()
