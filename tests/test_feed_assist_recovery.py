"""Tests for feed assist recovery after device-side deactivation.

Inserting a spool into T0 while printing with
T1 made the ACE preload the new slot (single gear assembly) and silently
drop feed assist on T1 — the print then relied on the extruder dragging
filament through a passive ACE, with tangle detection blind (no pumping,
no cont_assist_time signal).

Two recovery layers:
1. Slot-ready restore (both gens): any slot transitioning INTO ready marks
   a finished (pre)load cycle and re-enables the remembered assist slot.
   Previously only a direct empty→ready transition qualified, which the
   1 Hz heartbeat almost never observes (it samples preload/identifying).
2. Device-state reconciliation (ACE2 only): work status 'ready' while the
   driver believes assist is on means the firmware dropped it.
"""
import unittest
from unittest.mock import Mock, patch

from ace.config import ACE_INSTANCES
from ace.instance import AceInstance, INSTANCE_MANAGERS
from ace.manager import AceManager


ACE_CONFIG = {
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
    'status_debug_logging': False,
    'feed_length': 500,
    'retract_length': 550,
    'long_retract_length': 600,
    'pre_cut_retract_length': 50,
    'assist_motor_active_time': 2.0,
    'rdm_overshoot_length': 50.0,
}


class TestSlotReadyAssistRestore(unittest.TestCase):
    """Restore must fire on any transition into ready, not just empty→ready."""

    def setUp(self):
        INSTANCE_MANAGERS.clear()
        self.mock_printer = Mock()
        self.mock_printer.lookup_object = Mock(return_value=Mock())
        with patch('ace.instance.AceSerialManager'):
            self.instance = AceInstance(0, ACE_CONFIG, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        # Printing with T1 (slot 1), feed assist active on it
        self.instance.inventory[1]['status'] = 'ready'
        self.instance._feed_assist_index = 1
        self.instance._enable_feed_assist = Mock()

    def _status(self, slot0_status):
        return {
            'result': {
                'slots': [
                    {'index': 0, 'status': slot0_status, 'rfid': 0},
                    {'index': 1, 'status': 'ready', 'rfid': 0},
                ]
            }
        }

    def test_preload_then_ready_queues_restore(self):
        # Spool inserted into slot 0: heartbeat samples the intermediate
        # preload state, so empty→ready is never observed directly
        self.instance._status_update_callback(self._status('preload'))
        assert self.instance._pending_feed_assist_restore == -1

        self.instance._status_update_callback(self._status('ready'))
        assert self.instance._pending_feed_assist_restore == 1

    def test_direct_empty_to_ready_still_queues_restore(self):
        self.instance._status_update_callback(self._status('ready'))
        assert self.instance._pending_feed_assist_restore == 1

    def test_no_restore_without_active_assist(self):
        self.instance._feed_assist_index = -1
        self.instance._status_update_callback(self._status('preload'))
        self.instance._status_update_callback(self._status('ready'))
        assert self.instance._pending_feed_assist_restore == -1

    def test_no_restore_on_unchanged_status(self):
        self.instance.inventory[0]['status'] = 'ready'
        self.instance._status_update_callback(self._status('ready'))
        assert self.instance._pending_feed_assist_restore == -1

    def test_already_queued_restore_not_replaced(self):
        self.instance._pending_feed_assist_restore = 3
        self.instance._status_update_callback(self._status('ready'))
        assert self.instance._pending_feed_assist_restore == 3

    def test_no_queue_during_toolchange(self):
        # The toolchange owns assist state - a slot-ready restore mid-swap
        # re-arms the OLD tool's assist in parallel with the new tool's
        # (spool end flapping at the sensor).
        INSTANCE_MANAGERS[0].toolchange_in_progress = True  # literal True
        self.instance._status_update_callback(self._status('preload'))
        self.instance._status_update_callback(self._status('ready'))
        assert self.instance._pending_feed_assist_restore == -1

    def test_ace2_does_not_queue_from_slot_ready(self):
        # ACE2 stays 'busy' while assist is active - if assist survived the
        # slot load, a queued restore would hammer wait_ready against a
        # busy-by-design device. The status reconciliation path owns ACE2.
        self.instance.protocol = Mock()
        self.instance.protocol.feed_assist_causes_busy.return_value = True
        self.instance._status_update_callback(self._status('preload'))
        self.instance._status_update_callback(self._status('ready'))
        assert self.instance._pending_feed_assist_restore == -1


class TestAce2AssistReconciliation(unittest.TestCase):
    """ACE2: work status 'ready' while assist believed on = assist dropped."""

    def _bare(self, causes_busy=True, assist_index=0, status="ready"):
        inst = object.__new__(AceInstance)
        inst.instance_num = 1
        inst.gcode = Mock()
        inst._feed_assist_index = assist_index
        inst._pending_feed_assist_restore = -1
        inst._feed_assist_restore_attempts = 5
        inst._assist_lost_streak = 0
        inst.protocol = Mock()
        inst.protocol.feed_assist_causes_busy.return_value = causes_busy
        inst._info = {"status": status}
        return inst

    def test_two_ready_heartbeats_queue_restore(self):
        inst = self._bare()
        inst._reconcile_feed_assist_state()
        assert inst._pending_feed_assist_restore == -1  # first strike only
        inst._reconcile_feed_assist_state()
        assert inst._pending_feed_assist_restore == 0
        assert inst._feed_assist_restore_attempts == 0  # retry budget reset

    def test_busy_heartbeat_resets_streak(self):
        inst = self._bare()
        inst._reconcile_feed_assist_state()
        inst._info["status"] = "busy"
        inst._reconcile_feed_assist_state()
        assert inst._assist_lost_streak == 0
        inst._info["status"] = "ready"
        inst._reconcile_feed_assist_state()
        assert inst._pending_feed_assist_restore == -1  # streak restarted

    def test_ace1_never_reconciles(self):
        inst = self._bare(causes_busy=False)
        inst._reconcile_feed_assist_state()
        inst._reconcile_feed_assist_state()
        assert inst._pending_feed_assist_restore == -1

    def test_no_assist_no_reconcile(self):
        inst = self._bare(assist_index=-1)
        inst._assist_lost_streak = 1
        inst._reconcile_feed_assist_state()
        assert inst._assist_lost_streak == 0
        assert inst._pending_feed_assist_restore == -1

    def test_pending_restore_not_requeued(self):
        inst = self._bare()
        inst._pending_feed_assist_restore = 2
        inst._reconcile_feed_assist_state()
        inst._reconcile_feed_assist_state()
        assert inst._pending_feed_assist_restore == 2


class TestEmptySlotAssistRestoreSkip(unittest.TestCase):
    """Never restore assist onto a slot the device reports empty.

    In an endless-spool runout the firmware
    dropping assist on a depleted slot is correct behavior, and
    re-enabling it just pokes the starved retry cycling (~4 s pump ramps,
    forever, work status toggling busy/ready — which is exactly what the
    ACE2 reconcile check samples).  Only DEVICE-CONFIRMED emptiness may
    skip: the initialized _info defaults every slot to 'empty'.
    """

    def _bare_reconcile(self, slot_status, status_seen=True):
        inst = object.__new__(AceInstance)
        inst.instance_num = 1
        inst.gcode = Mock()
        inst._feed_assist_index = 1
        inst._pending_feed_assist_restore = -1
        inst._feed_assist_restore_attempts = 5
        inst._assist_lost_streak = 1  # one strike already
        inst._device_status_seen = status_seen
        inst.protocol = Mock()
        inst.protocol.feed_assist_causes_busy.return_value = True
        inst._info = {
            "status": "ready",
            "slots": [{"index": 1, "status": slot_status, "rfid": 0}],
        }
        return inst

    def _bare_restore(self, slot_status, status_seen=True):
        inst = object.__new__(AceInstance)
        inst.instance_num = 1
        inst.gcode = Mock()
        inst._pending_feed_assist_restore = 1
        inst._feed_assist_restore_attempts = 0
        inst._feed_assist_topology_position = None
        inst._device_status_seen = status_seen
        inst.serial_mgr = Mock()
        inst.serial_mgr.get_usb_topology_position.return_value = 0
        inst.wait_ready = Mock()
        inst.protocol = Mock()
        inst.send_request = Mock()
        inst._info = {
            "status": "ready",
            "slots": [{"index": 1, "status": slot_status, "rfid": 0}],
        }
        return inst

    def test_reconcile_skips_empty_slot(self):
        inst = self._bare_reconcile("empty")
        inst._reconcile_feed_assist_state()  # would be second strike
        assert inst._pending_feed_assist_restore == -1
        assert inst._assist_lost_streak == 0  # streak reset, not counting

    def test_reconcile_queues_on_ready_slot(self):
        inst = self._bare_reconcile("ready")
        inst._reconcile_feed_assist_state()
        assert inst._pending_feed_assist_restore == 1

    def test_restore_skips_empty_slot(self):
        inst = self._bare_restore("empty")
        inst._maybe_restore_pending_feed_assist()
        assert not inst.send_request.called
        assert inst._pending_feed_assist_restore == -1  # dropped, not retried
        assert inst._feed_assist_restore_attempts == 0  # no attempt consumed

    def test_restore_proceeds_on_ready_slot(self):
        inst = self._bare_restore("ready")
        inst._maybe_restore_pending_feed_assist()
        assert inst.send_request.called

    def test_initialized_default_empty_not_trusted(self):
        # Before the first real heartbeat, _info is the initialized
        # default (all slots 'empty') — restores must NOT be suppressed.
        inst = self._bare_restore("empty", status_seen=False)
        inst._maybe_restore_pending_feed_assist()
        assert inst.send_request.called


class TestRestoreOwnershipInvariant(unittest.TestCase):
    """Single-assist invariant at restore time.

    All assist restores funnel through _maybe_restore_pending_feed_assist;
    only the slot holding the globally current tool may be restored —
    anything else is stale state (e.g. left behind by a failed toolchange)
    and restoring it runs a second assist in parallel with the loaded
    tool's.  Restores are also deferred (kept queued) while a toolchange
    runs.
    """

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

    def tearDown(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

    def _bare(self, pending_slot=1, current_tool=5):
        # instance_num=1, slot 1 -> owned by global tool T5
        inst = object.__new__(AceInstance)
        inst.instance_num = 1
        inst.gcode = Mock()
        inst._pending_feed_assist_restore = pending_slot
        inst._feed_assist_restore_attempts = 0
        inst._feed_assist_index = pending_slot
        inst._feed_assist_topology_position = 0
        inst._device_status_seen = True
        inst.serial_mgr = Mock()
        inst.serial_mgr.get_usb_topology_position.return_value = 0
        inst.wait_ready = Mock()
        inst.protocol = Mock()
        inst.send_request = Mock()
        inst._info = {
            "status": "ready",
            "slots": [{"index": 1, "status": "ready", "rfid": 0}],
        }
        # instance.state resolves to manager.state via the registry
        state_values = {"ace_current_index": current_tool}
        mgr = Mock()
        mgr.toolchange_in_progress = False  # literal bool like the real one
        mgr.state.get = lambda k, d=None: state_values.get(k, d)
        mgr.state.set = Mock()
        INSTANCE_MANAGERS[1] = mgr
        ACE_INSTANCES[1] = inst
        return inst

    def test_restore_proceeds_for_current_tools_slot(self):
        inst = self._bare(pending_slot=1, current_tool=5)  # T5 = inst1/slot1
        inst._maybe_restore_pending_feed_assist()
        assert inst.send_request.called

    def test_restore_dropped_when_no_tool_loaded(self):
        inst = self._bare(pending_slot=1, current_tool=-1)
        inst._maybe_restore_pending_feed_assist()
        assert not inst.send_request.called
        assert inst._feed_assist_index == -1  # stale state cleared
        INSTANCE_MANAGERS[1].state.set.assert_called_with(
            "ace_feed_assist_index_1", -1)

    def test_restore_dropped_for_wrong_slot(self):
        # Current tool T4 = instance 1 slot 0; pending restore is slot 1
        inst = self._bare(pending_slot=1, current_tool=4)
        inst._maybe_restore_pending_feed_assist()
        assert not inst.send_request.called
        assert inst._feed_assist_index == -1

    def test_restore_dropped_for_other_instances_tool(self):
        # Current tool T1 lives on instance 0 (not registered) - any assist
        # on this instance is stale
        inst = self._bare(pending_slot=1, current_tool=1)
        inst._maybe_restore_pending_feed_assist()
        assert not inst.send_request.called

    def test_restore_deferred_while_toolchange_runs(self):
        inst = self._bare(pending_slot=1, current_tool=5)
        mgr = INSTANCE_MANAGERS[1]
        mgr.toolchange_in_progress = True  # literal True required
        inst._maybe_restore_pending_feed_assist()
        assert not inst.send_request.called
        # Kept queued for the next heartbeat, not dropped
        assert inst._pending_feed_assist_restore == 1
        # After the toolchange, an owned restore proceeds
        mgr.toolchange_in_progress = False
        inst._maybe_restore_pending_feed_assist()
        assert inst.send_request.called

    def test_restore_fail_open_when_state_unreadable(self):
        inst = self._bare(pending_slot=1, current_tool=5)
        INSTANCE_MANAGERS[1].state.get = Mock(
            side_effect=RuntimeError("state gone"))
        inst._maybe_restore_pending_feed_assist()
        assert inst.send_request.called


class TestDisableFeedAssistForTool(unittest.TestCase):
    """Assist disable for paths that skip the normal unload sequence.

    Used by the endless-spool skip-unload (leftover assist kept
    starved-cycling the empty slot and its stale index made tangle
    detection watch the wrong instance - a
    tangle undetected) and by the toolhead runout handler (a surviving
    ACE2 assist keeps the device busy-by-design and deadlocked a resume
    reload's wait_ready for 60 s).
    """

    def _mgr(self):
        mgr = object.__new__(AceManager)
        mgr.gcode = Mock()
        return mgr

    def test_disables_when_driver_tracks_tools_slot(self):
        mgr = self._mgr()
        inst = Mock()
        inst._feed_assist_index = 3
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, 3),
        ):
            mgr.disable_feed_assist_for_tool(3, "test reason")
        inst._disable_feed_assist.assert_called_once_with(3)

    def test_noop_when_assist_not_on_tools_slot(self):
        # Assist already off (or on another slot): nothing to disable -
        # never touch assist state that is not this tool's
        mgr = self._mgr()
        inst = Mock()
        inst._feed_assist_index = -1
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, 3),
        ):
            mgr.disable_feed_assist_for_tool(3, "test reason")
        inst._disable_feed_assist.assert_not_called()

    def test_failure_reported_never_raised(self):
        # Callers (endless-spool recovery, runout handler) must proceed
        # even if the disable fails
        mgr = self._mgr()
        inst = Mock()
        inst._feed_assist_index = 3
        inst._disable_feed_assist.side_effect = RuntimeError("io error")
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, 3),
        ):
            mgr.disable_feed_assist_for_tool(3, "test reason")  # no raise
        warnings = [
            c for c in mgr.gcode.respond_info.call_args_list
            if "could not disable" in str(c)
        ]
        assert warnings


if __name__ == '__main__':
    unittest.main()
