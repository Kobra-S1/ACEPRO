"""Tests for the fast disconnect pause and resume feed-assist verification.

A dead ACE2 clamps the filament and starves
the extruder within seconds, while the instability-count supervision path
takes 60-90 s to pause; and an ACE power cycle / klippy restart while paused
can silently lose feed assist so the resumed print extrudes nothing.
"""
from unittest.mock import Mock, patch

from ace.config import ACE_INSTANCES, INSTANCE_MANAGERS
from ace.manager import AceManager
from ace.instance import AceInstance
from ace.runout_monitor import RunoutMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bare_manager(current_tool=4, print_state="printing"):
    """AceManager with only the attrs the fast-pause path touches."""
    mgr = object.__new__(AceManager)
    mgr.state = Mock()
    mgr.state.get = Mock(return_value=current_tool)
    mgr.gcode = Mock()
    mgr._fast_disconnect_pause_fired = None
    mgr._connection_issue_shown = False
    mgr._pause_for_connection_issue = Mock()

    stats = Mock()
    stats.get_status.return_value = {"state": print_state}
    mgr.printer = Mock()
    mgr.printer.lookup_object.return_value = stats
    return mgr


def _instance_mock(protocol_name="ace2_proto", instance_num=1,
                   connected=False, disconnected_for=0.0,
                   config_timeout=-1.0, protocol_default=5.0):
    inst = Mock()
    inst.instance_num = instance_num
    inst.protocol_name = protocol_name
    inst.disconnect_pause_timeout = config_timeout
    inst.protocol.default_disconnect_pause_timeout.return_value = protocol_default
    inst.serial_mgr.get_connection_status.return_value = {
        "connected": connected,
        "disconnected_for": disconnected_for,
        "recent_reconnects": 0,
        "time_connected": 0.0,
    }
    return inst


# ---------------------------------------------------------------------------
# _resolve_disconnect_pause_timeout
# ---------------------------------------------------------------------------

class TestResolveDisconnectPauseTimeout:
    def test_negative_config_uses_protocol_default(self):
        mgr = _bare_manager()
        inst = _instance_mock(config_timeout=-1.0, protocol_default=5.0)
        assert mgr._resolve_disconnect_pause_timeout(inst) == 5.0

    def test_explicit_config_wins_over_protocol(self):
        mgr = _bare_manager()
        inst = _instance_mock(config_timeout=12.0, protocol_default=5.0)
        assert mgr._resolve_disconnect_pause_timeout(inst) == 12.0

    def test_zero_config_disables(self):
        mgr = _bare_manager()
        inst = _instance_mock(config_timeout=0.0)
        assert mgr._resolve_disconnect_pause_timeout(inst) == 0.0

    def test_garbage_config_falls_back_to_protocol_default(self):
        mgr = _bare_manager()
        inst = _instance_mock(config_timeout="nonsense", protocol_default=30.0)
        assert mgr._resolve_disconnect_pause_timeout(inst) == 30.0

    def test_protocol_error_falls_back_to_ace1_default(self):
        mgr = _bare_manager()
        inst = _instance_mock(config_timeout=-1.0)
        inst.protocol.default_disconnect_pause_timeout.side_effect = RuntimeError
        assert mgr._resolve_disconnect_pause_timeout(inst) == 30.0


# ---------------------------------------------------------------------------
# _check_fast_disconnect_pause
# ---------------------------------------------------------------------------

class TestFastDisconnectPause:
    def _run(self, mgr, inst):
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, 0),
        ):
            mgr._check_fast_disconnect_pause(100.0)

    def test_pauses_when_active_instance_dead_past_timeout(self):
        mgr = _bare_manager(current_tool=4)
        inst = _instance_mock(disconnected_for=6.0, protocol_default=5.0)
        self._run(mgr, inst)
        mgr._pause_for_connection_issue.assert_called_once()
        assert mgr._fast_disconnect_pause_fired == inst.instance_num
        assert mgr._connection_issue_shown is True

    def test_no_pause_below_timeout(self):
        mgr = _bare_manager(current_tool=4)
        inst = _instance_mock(disconnected_for=3.0, protocol_default=5.0)
        self._run(mgr, inst)
        mgr._pause_for_connection_issue.assert_not_called()

    def test_no_pause_when_no_active_tool(self):
        mgr = _bare_manager(current_tool=-1)
        inst = _instance_mock(disconnected_for=999.0)
        self._run(mgr, inst)
        mgr._pause_for_connection_issue.assert_not_called()

    def test_no_pause_when_connected(self):
        mgr = _bare_manager(current_tool=4)
        inst = _instance_mock(connected=True)
        self._run(mgr, inst)
        mgr._pause_for_connection_issue.assert_not_called()

    def test_no_pause_when_not_printing(self):
        mgr = _bare_manager(current_tool=4, print_state="paused")
        inst = _instance_mock(disconnected_for=999.0)
        self._run(mgr, inst)
        mgr._pause_for_connection_issue.assert_not_called()

    def test_fires_once_per_outage(self):
        mgr = _bare_manager(current_tool=4)
        inst = _instance_mock(disconnected_for=6.0, protocol_default=5.0)
        self._run(mgr, inst)
        self._run(mgr, inst)
        assert mgr._pause_for_connection_issue.call_count == 1

    def test_rearms_after_reconnect(self):
        mgr = _bare_manager(current_tool=4)
        inst = _instance_mock(disconnected_for=6.0, protocol_default=5.0)
        self._run(mgr, inst)
        assert mgr._fast_disconnect_pause_fired == inst.instance_num

        inst.serial_mgr.get_connection_status.return_value = {
            "connected": True,
            "disconnected_for": 0.0,
            "recent_reconnects": 1,
            "time_connected": 5.0,
        }
        self._run(mgr, inst)
        assert mgr._fast_disconnect_pause_fired is None

        # Second outage fires again
        inst.serial_mgr.get_connection_status.return_value = {
            "connected": False,
            "disconnected_for": 7.0,
            "recent_reconnects": 1,
            "time_connected": 0.0,
        }
        self._run(mgr, inst)
        assert mgr._pause_for_connection_issue.call_count == 2

    def test_zero_timeout_disables_fast_path(self):
        mgr = _bare_manager(current_tool=4)
        inst = _instance_mock(disconnected_for=999.0, config_timeout=0.0)
        self._run(mgr, inst)
        mgr._pause_for_connection_issue.assert_not_called()


# ---------------------------------------------------------------------------
# verify_feed_assist_for_tool (resume safety net)
# ---------------------------------------------------------------------------

class TestVerifyFeedAssistForTool:
    def _mgr(self):
        mgr = object.__new__(AceManager)
        mgr.gcode = Mock()
        return mgr

    def _inst(self, assist_index, connected=True):
        inst = Mock()
        inst.instance_num = 1
        inst.serial_mgr.is_connected.return_value = connected
        inst._get_current_feed_assist_index.return_value = assist_index
        return inst

    def test_reenables_when_assist_lost(self):
        mgr = self._mgr()
        inst = self._inst(assist_index=-1)
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, 2),
        ):
            assert mgr.verify_feed_assist_for_tool(6) is True
        inst._enable_feed_assist.assert_called_once_with(2)

    def test_noop_when_assist_already_active(self):
        mgr = self._mgr()
        inst = self._inst(assist_index=2)
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, 2),
        ):
            assert mgr.verify_feed_assist_for_tool(6) is True
        inst._enable_feed_assist.assert_not_called()

    def test_skips_when_disconnected(self):
        mgr = self._mgr()
        inst = self._inst(assist_index=-1, connected=False)
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, 2),
        ):
            assert mgr.verify_feed_assist_for_tool(6) is False
        inst._enable_feed_assist.assert_not_called()

    def test_enable_failure_reported_not_raised(self):
        mgr = self._mgr()
        inst = self._inst(assist_index=-1)
        inst._enable_feed_assist.side_effect = RuntimeError("boom")
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, 2),
        ):
            assert mgr.verify_feed_assist_for_tool(6) is False

    # -- loaded-plausibility guard (resume after a
    #    failed swap re-enabled assist on a never-loaded tool) --

    def _mgr_with_state(self, pos="bowden", sensor=False, target=-1,
                        toolchange=False):
        mgr = self._mgr()
        mgr.toolchange_in_progress = toolchange
        state_values = {"ace_target_index": target, "ace_filament_pos": pos}
        mgr.state = Mock()
        mgr.state.get = lambda k, d=None: state_values.get(k, d)
        mgr.get_switch_state = Mock(return_value=sensor)
        return mgr

    def test_skips_reenable_when_tool_not_loaded(self):
        # Failed load: pos=bowden, toolhead sensor clear -> no assist
        mgr = self._mgr_with_state(pos="bowden", sensor=False)
        inst = self._inst(assist_index=-1)
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, 2),
        ):
            assert mgr.verify_feed_assist_for_tool(6) is False
        inst._enable_feed_assist.assert_not_called()

    def test_skips_when_unconfirmed_toolchange_pending(self):
        mgr = self._mgr_with_state(pos="nozzle", sensor=True, target=7)
        inst = self._inst(assist_index=-1)
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, 2),
        ):
            assert mgr.verify_feed_assist_for_tool(6) is False
        inst._enable_feed_assist.assert_not_called()

    def test_skips_during_toolchange(self):
        mgr = self._mgr_with_state(pos="nozzle", sensor=True,
                                   toolchange=True)
        inst = self._inst(assist_index=-1)
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, 2),
        ):
            assert mgr.verify_feed_assist_for_tool(6) is False
        inst._enable_feed_assist.assert_not_called()

    def test_reenables_when_pos_shows_loaded(self):
        mgr = self._mgr_with_state(pos="nozzle", sensor=False)
        inst = self._inst(assist_index=-1)
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, 2),
        ):
            assert mgr.verify_feed_assist_for_tool(6) is True
        inst._enable_feed_assist.assert_called_once_with(2)

    def test_reenables_when_sensor_shows_loaded_despite_stale_pos(self):
        mgr = self._mgr_with_state(pos="bowden", sensor=True)
        inst = self._inst(assist_index=-1)
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, 2),
        ):
            assert mgr.verify_feed_assist_for_tool(6) is True
        inst._enable_feed_assist.assert_called_once_with(2)


# ---------------------------------------------------------------------------
# ensure_tool_slot_loaded (empty-slot load guard)
# ---------------------------------------------------------------------------

class TestEnsureToolSlotLoaded:
    """ACE2 ACKs feeds on empty slots and spins for minutes — loads toward
    an empty slot must be blocked before homing/heating/feeding."""

    def _mgr(self):
        return object.__new__(AceManager)

    def _inst(self, live_empty, inv_status):
        inst = Mock()
        inst.instance_num = 1
        inst._is_slot_empty.return_value = live_empty
        inst.inventory = [{"status": inv_status}] * 4
        return inst

    def _check(self, inst, tool=4, slot=0):
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(inst, slot),
        ):
            self._mgr().ensure_tool_slot_loaded(tool)

    def test_loaded_slot_passes(self):
        self._check(self._inst(live_empty=False, inv_status="ready"))

    def test_device_reported_empty_raises(self):
        import pytest
        with pytest.raises(ValueError, match="EMPTY \\(device-reported\\)"):
            self._check(self._inst(live_empty=True, inv_status="ready"))

    def test_inventory_empty_raises(self):
        import pytest
        with pytest.raises(ValueError, match="EMPTY \\(inventory-reported\\)"):
            self._check(self._inst(live_empty=False, inv_status="empty"))

    def test_unload_tool_is_noop(self):
        # -1 = unload; must never raise regardless of slot states
        self._mgr().ensure_tool_slot_loaded(-1)

    def test_unresolvable_tool_is_noop(self):
        with patch(
            "ace.manager.get_ace_instance_and_slot_for_tool",
            return_value=(None, None),
        ):
            self._mgr().ensure_tool_slot_loaded(99)


# ---------------------------------------------------------------------------
# RunoutMonitor: paused → printing edge schedules the verification
# ---------------------------------------------------------------------------

class TestResumeEdgeTriggersVerification:
    def _make_monitor(self, old_state, new_state, current_tool=4):
        printer = Mock()
        gcode = Mock()
        reactor = Mock()
        reactor.NOW = 0.0
        manager = Mock()
        manager.toolchange_in_progress = False
        manager.state.get = Mock(return_value=current_tool)

        monitor = RunoutMonitor(
            printer, gcode, reactor, Mock(), manager,
        )
        monitor.last_print_state = old_state
        monitor.last_printing_active = old_state == "printing"
        monitor.runout_detection_active = False  # early-exit after the edge check

        stats = Mock()
        stats.get_status.return_value = {"state": new_state}
        printer.lookup_object.return_value = stats
        return monitor, manager, reactor

    def test_paused_to_printing_schedules_verify(self):
        monitor, manager, reactor = self._make_monitor("paused", "printing")
        monitor._monitor_runout(100.0)
        assert reactor.register_callback.called
        # Executing the scheduled callback must invoke the manager verify
        cb = reactor.register_callback.call_args[0][0]
        cb(101.0)
        manager.verify_feed_assist_for_tool.assert_called_once_with(4)

    def test_no_verify_without_tool(self):
        monitor, manager, reactor = self._make_monitor(
            "paused", "printing", current_tool=-1
        )
        monitor._monitor_runout(100.0)
        manager.verify_feed_assist_for_tool.assert_not_called()

    def test_no_verify_on_other_transitions(self):
        monitor, manager, reactor = self._make_monitor("standby", "printing")
        monitor._monitor_runout(100.0)
        manager.verify_feed_assist_for_tool.assert_not_called()


# ---------------------------------------------------------------------------
# AceInstance: feed assist restore retry + persisted-index recovery
# ---------------------------------------------------------------------------

def _bare_instance(pending_slot=2, attempts=0):
    inst = object.__new__(AceInstance)
    inst.instance_num = 1
    inst.SLOT_COUNT = 4
    inst.gcode = Mock()
    # instance.state is a property delegating to INSTANCE_MANAGERS[n].state
    INSTANCE_MANAGERS[1] = Mock()
    inst.serial_mgr = Mock()
    inst.serial_mgr.get_usb_topology_position.return_value = "3-1"
    inst.protocol = Mock()
    inst._pending_feed_assist_restore = pending_slot
    inst._feed_assist_restore_attempts = attempts
    inst._feed_assist_topology_position = None
    inst._feed_assist_index = -1
    inst.send_request = Mock()
    inst.wait_ready = Mock()
    return inst


class TestFeedAssistRestoreRetry:
    def test_busy_requeues_for_retry(self):
        inst = _bare_instance(pending_slot=2)
        inst.wait_ready.side_effect = TimeoutError
        inst._maybe_restore_pending_feed_assist()
        # Previously the pending flag was cleared and the promised retry
        # never happened — now the slot must be re-queued.
        assert inst._pending_feed_assist_restore == 2
        assert inst._feed_assist_restore_attempts == 1
        inst.send_request.assert_not_called()

    def test_gives_up_after_max_attempts(self):
        inst = _bare_instance(
            pending_slot=2,
            attempts=AceInstance.FEED_ASSIST_RESTORE_MAX_ATTEMPTS,
        )
        inst._maybe_restore_pending_feed_assist()
        assert inst._pending_feed_assist_restore == -1
        inst.send_request.assert_not_called()
        inst.wait_ready.assert_not_called()

    def test_success_sends_start_request(self):
        inst = _bare_instance(pending_slot=2)
        inst._maybe_restore_pending_feed_assist()
        assert inst._pending_feed_assist_restore == -1
        inst.send_request.assert_called_once()

    def test_restore_response_syncs_runtime_index(self):
        inst = _bare_instance()
        inst._on_feed_assist_restore_response({"code": 0}, 3)
        assert inst._feed_assist_index == 3
        assert inst._feed_assist_restore_attempts == 0


class TestPersistedFeedAssistRecovery:
    def _connect_instance(self, in_memory_index, persisted_index,
                          current_tool="auto"):
        inst = _bare_instance(pending_slot=-1)
        inst.feed_assist_active_after_ace_connect = True
        inst._feed_assist_index = in_memory_index
        inst._pending_rfid_refresh = False
        inst._pending_rfid_refresh_slots = []
        inst._reset_status_failure_tracking = Mock()
        # The single-assist invariant resolves the current tool's instance
        # via the global registry - register this instance like production.
        ACE_INSTANCES[1] = inst
        if current_tool == "auto":
            # Default: the assist slot belongs to the current tool, so the
            # restore is legitimate (instance 1 manages tools 4..7).
            slot = in_memory_index
            if slot < 0:
                try:
                    slot = int(persisted_index)
                except (TypeError, ValueError):
                    slot = -1
            current_tool = 4 + slot if 0 <= slot < 4 else -1
        state_values = {
            "ace_current_index": current_tool,
            "ace_feed_assist_index_1": persisted_index,
        }
        INSTANCE_MANAGERS[1].state.get = Mock(
            side_effect=lambda key, default=-1: state_values.get(key, default)
        )
        INSTANCE_MANAGERS[1].state.set = Mock()
        return inst

    def test_klippy_restart_recovers_persisted_slot(self):
        # In-memory index lost (-1) but persisted variable knows slot 1
        inst = self._connect_instance(in_memory_index=-1, persisted_index=1)
        inst._on_ace_connect()
        assert inst._pending_feed_assist_restore == 1

    def test_in_memory_index_takes_precedence(self):
        inst = self._connect_instance(in_memory_index=3, persisted_index=1)
        inst._on_ace_connect()
        assert inst._pending_feed_assist_restore == 3

    def test_no_restore_when_nothing_persisted(self):
        inst = self._connect_instance(in_memory_index=-1, persisted_index=-1)
        inst._on_ace_connect()
        assert inst._pending_feed_assist_restore == -1

    def test_invalid_persisted_value_ignored(self):
        inst = self._connect_instance(in_memory_index=-1, persisted_index="junk")
        inst._on_ace_connect()
        assert inst._pending_feed_assist_restore == -1

    def test_restore_disabled_by_config(self):
        inst = self._connect_instance(in_memory_index=2, persisted_index=2)
        inst.feed_assist_active_after_ace_connect = False
        inst._on_ace_connect()
        assert inst._pending_feed_assist_restore == -1

    def test_stale_assist_cleared_when_other_tool_current(self):
        # Assist persisted on slot 1 (would be tool 5) but the current tool
        # is T0 on another instance - the single-assist invariant must NOT
        # restore it and must clear the stale persisted state.
        inst = self._connect_instance(
            in_memory_index=-1, persisted_index=1, current_tool=0
        )
        inst._on_ace_connect()
        assert inst._pending_feed_assist_restore == -1
        assert inst._feed_assist_index == -1
        INSTANCE_MANAGERS[1].state.set.assert_called_with(
            "ace_feed_assist_index_1", -1
        )

    def test_stale_assist_cleared_when_no_tool_loaded(self):
        # No tool loaded (ace_current_index=-1): any assist is stale and
        # must not survive the reconnect.
        inst = self._connect_instance(
            in_memory_index=2, persisted_index=2, current_tool=-1
        )
        inst._on_ace_connect()
        assert inst._pending_feed_assist_restore == -1
        assert inst._feed_assist_index == -1

    def test_restore_kept_when_state_unreadable(self):
        # Fail-open: if ace_current_index cannot be read at all, keep the
        # plain restore behavior instead of destroying assist state.
        inst = self._connect_instance(in_memory_index=2, persisted_index=2)
        INSTANCE_MANAGERS[1].state.get = Mock(side_effect=AttributeError)
        inst._on_ace_connect()
        assert inst._pending_feed_assist_restore == 2
