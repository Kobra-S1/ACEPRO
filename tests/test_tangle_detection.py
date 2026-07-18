"""Tests for ACE pump_time tangle detection in RunoutMonitor."""
from unittest.mock import Mock, patch
from ace.runout_monitor import RunoutMonitor


def _make_instance(protocol_name="ace1_json", feed_assist_index=0,
                   cont_assist_time=None, slot_empty=False):
    """Mock ACE instance.  cont_assist_time=None → field absent.

    slot_empty models the device-reported slot presence state; flip
    inst._slot_empty_flag mid-test to simulate a spool running out.
    """
    inst = Mock()
    inst.protocol_name = protocol_name
    inst._feed_assist_index = feed_assist_index
    info = {}
    if cont_assist_time is not None:
        info["cont_assist_time"] = cont_assist_time
    inst._info = info
    inst._slot_empty_flag = slot_empty
    inst._is_slot_empty = lambda idx: inst._slot_empty_flag
    return inst


def _make_monitor(tangle_detection=True, tangle_pump_time=None,
                  instances=None, feed_assist_active=True,
                  tangle_verify_time=0.0, tangle_pump_time_hard=None):
    """tangle_verify_time defaults to 0 (immediate pause at threshold) so
    detection-core tests stay verdict-free; verdict tests pass a window."""
    printer = Mock()
    gcode = Mock()
    reactor = Mock()
    reactor.NOW = 0.0
    reactor.NEVER = float("inf")
    reactor.monotonic = Mock(return_value=0.0)
    endless_spool = Mock()
    manager = Mock()
    manager.toolchange_in_progress = False
    manager.state = Mock()
    manager.state.get = Mock(return_value=-1)
    manager.is_feed_assist_active.return_value = feed_assist_active
    manager.instances = instances if instances is not None else [_make_instance()]

    monitor = RunoutMonitor(
        printer, gcode, reactor, endless_spool, manager,
        runout_debounce_count=1,
        tangle_detection=tangle_detection,
        tangle_pump_time=tangle_pump_time,
        tangle_verify_time=tangle_verify_time,
        tangle_pump_time_hard=tangle_pump_time_hard,
    )
    return monitor, manager, gcode


def _pause_calls(gcode):
    return [
        c for c in gcode.run_script_from_command.call_args_list
        if "PAUSE" in str(c)
    ]


def _setup_printing_state(monitor, manager, gcode):
    """Drive the monitor loop to the point where _check_tangle is called."""
    stats_obj = Mock()
    stats_obj.get_status.return_value = {"state": "printing"}
    save_vars = Mock()
    save_vars.allVariables = {"ace_current_index": 0}
    manager.state = Mock()
    manager.state.get = lambda k, d=None: save_vars.allVariables.get(k, d)

    def lookup(name, default=None):
        if name == "print_stats":
            return stats_obj
        if name == "save_variables":
            return save_vars
        if default is not None:
            return default
        raise Exception(name)
    monitor.printer.lookup_object.side_effect = lookup

    manager.get_switch_state.return_value = True
    monitor.prev_toolhead_sensor_state = True
    monitor.last_printing_active = True
    monitor.last_print_state = "printing"
    monitor.runout_detection_active = True
    monitor.runout_handling_in_progress = False


class TestTangleDetectionInit:
    def test_tangle_disabled_by_default(self):
        monitor, *_ = _make_monitor(tangle_detection=False)
        assert monitor.tangle_detection_enabled is False
        assert monitor._pt_phase_armed is False
        assert monitor._pt_last_value_s == 0.0
        assert monitor._pt_unsupported_logged is False

    def test_tangle_enabled(self):
        monitor, *_ = _make_monitor(tangle_detection=True)
        assert monitor.tangle_detection_enabled is True

    def test_default_threshold(self):
        monitor, *_ = _make_monitor()
        assert monitor.tangle_pump_time == RunoutMonitor.DEFAULT_TANGLE_PUMP_TIME

    def test_custom_threshold_passes_through(self):
        monitor, *_ = _make_monitor(tangle_pump_time=6.0)
        assert monitor.tangle_pump_time == 6.0

    def test_threshold_clamped_below_floor(self):
        monitor, *_ = _make_monitor(tangle_pump_time=0.5)
        assert monitor.tangle_pump_time == RunoutMonitor.TANGLE_PUMP_TIME_FLOOR

    def test_default_verify_time(self):
        monitor, *_ = _make_monitor(tangle_verify_time=None)
        assert monitor.tangle_verify_time == \
            RunoutMonitor.DEFAULT_TANGLE_VERIFY_TIME

    def test_verify_time_clamped_to_zero(self):
        monitor, *_ = _make_monitor(tangle_verify_time=-3.0)
        assert monitor.tangle_verify_time == 0.0


class TestActiveInstanceGate:
    def test_noop_when_no_instances(self):
        monitor, _m, gcode = _make_monitor(instances=[])
        monitor._check_tangle(current_tool=0)
        assert not gcode.run_script_from_command.called

    def test_gen2_with_assist_is_monitored(self):
        # ACE2 feed assist never self-errors on tangle
        # → pump-time monitoring must cover Gen 2 too.
        inst = _make_instance(protocol_name="ace2_proto", cont_assist_time=1.0)
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        assert monitor._get_active_assist_instance() is inst
        monitor._check_tangle(current_tool=4)  # arms
        inst._info["cont_assist_time"] = 5.0   # fresh growing sample
        monitor._check_tangle(current_tool=4)
        pause_calls = [
            c for c in gcode.run_script_from_command.call_args_list
            if "PAUSE" in str(c)
        ]
        assert pause_calls, "expected PAUSE for tangled Gen 2 instance"

    def test_monitors_gen2_when_mixed_fleet_and_gen2_is_active(self):
        gen1_idle = _make_instance(
            protocol_name="ace1_json", feed_assist_index=-1,
            cont_assist_time=0.0,
        )
        gen2_active = _make_instance(
            protocol_name="ace2_proto", feed_assist_index=0,
            cont_assist_time=1.0,
        )
        monitor, _m, _g = _make_monitor(instances=[gen1_idle, gen2_active])
        assert monitor._get_active_assist_instance() is gen2_active

    def test_monitors_gen1_when_mixed_fleet_and_gen1_is_active(self):
        gen1_active = _make_instance(
            protocol_name="ace1_json", feed_assist_index=0,
            cont_assist_time=1.0,
        )
        gen2_idle = _make_instance(
            protocol_name="ace2_proto", feed_assist_index=-1,
            cont_assist_time=99.0,
        )
        monitor, _m, _g = _make_monitor(instances=[gen2_idle, gen1_active])
        assert monitor._get_active_assist_instance() is gen1_active


class TestFeedAssistGate:
    def test_noop_when_no_instance_pumps(self):
        inst = _make_instance(
            feed_assist_index=-1, cont_assist_time=99.0)
        monitor, _m, gcode = _make_monitor(instances=[inst])
        monitor._check_tangle(current_tool=0)
        assert not gcode.run_script_from_command.called

    def test_phase_resets_when_pump_stops(self):
        inst = _make_instance(cont_assist_time=2.0)
        monitor, _m, _g = _make_monitor(instances=[inst])
        monitor._check_tangle(current_tool=0)
        assert monitor._pt_phase_armed is True
        inst._feed_assist_index = -1
        monitor._check_tangle(current_tool=0)
        assert monitor._pt_phase_armed is False


class TestPumpTimeDetection:
    def test_no_trigger_below_threshold(self):
        inst = _make_instance(cont_assist_time=2.5)
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        monitor._check_tangle(current_tool=0)
        assert not gcode.run_script_from_command.called

    def test_trigger_at_threshold_after_growth_phase(self):
        inst = _make_instance(cont_assist_time=1.0)
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        monitor._check_tangle(current_tool=0)
        inst._info["cont_assist_time"] = 4.0
        monitor._check_tangle(current_tool=0)
        pause_calls = [
            c for c in gcode.run_script_from_command.call_args_list
            if "PAUSE" in str(c)
        ]
        assert pause_calls, "expected PAUSE on threshold cross"

    def test_first_growth_tick_only_marks_phase(self):
        inst = _make_instance(cont_assist_time=5.0)
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        monitor._check_tangle(current_tool=0)
        assert monitor._pt_phase_armed is True
        assert not gcode.run_script_from_command.called

    def test_value_drop_resets_phase(self):
        inst = _make_instance(cont_assist_time=2.0)
        monitor, _m, _g = _make_monitor(instances=[inst])
        monitor._check_tangle(current_tool=0)
        assert monitor._pt_phase_armed is True
        inst._info["cont_assist_time"] = 0.0
        monitor._check_tangle(current_tool=0)
        assert monitor._pt_phase_armed is False
        assert monitor._pt_last_value_s == 0.0

    def test_field_absent_logs_once_then_silent(self):
        inst = _make_instance(cont_assist_time=None)
        monitor, _m, gcode = _make_monitor(instances=[inst])
        assert monitor._pt_unsupported_logged is False
        with patch("ace.runout_monitor.logging") as log:
            monitor._check_tangle(current_tool=0)
            assert monitor._pt_unsupported_logged is True
            log.info.assert_called_once()
            log.info.reset_mock()
            monitor._check_tangle(current_tool=0)
            log.info.assert_not_called()
        assert not gcode.run_script_from_command.called

    def test_after_trigger_state_wiped(self):
        inst = _make_instance(cont_assist_time=1.0)
        monitor, _m, _g = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        monitor._check_tangle(current_tool=0)
        inst._info["cont_assist_time"] = 5.0
        monitor._check_tangle(current_tool=0)
        assert monitor._pt_phase_armed is False
        assert monitor._pt_last_value_s == 0.0


class TestEmptySlotGate:
    """Assist slot device-reported empty = spool runout, never a tangle."""

    def test_gate_suppresses_before_arming(self):
        inst = _make_instance(cont_assist_time=1.0, slot_empty=True)
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        monitor._check_tangle(current_tool=0)
        inst._info["cont_assist_time"] = 5.0
        monitor._check_tangle(current_tool=0)
        inst._info["cont_assist_time"] = 6.0
        monitor._check_tangle(current_tool=0)
        assert not _pause_calls(gcode)
        assert monitor._pt_phase_armed is False

    def test_gate_message_once_per_depletion(self):
        inst = _make_instance(cont_assist_time=1.0, slot_empty=True)
        monitor, _m, gcode = _make_monitor(instances=[inst])
        monitor._check_tangle(current_tool=0)
        monitor._check_tangle(current_tool=0)
        msgs = [
            c for c in gcode.respond_info.call_args_list
            if "ran out at the ACE" in str(c)
        ]
        assert len(msgs) == 1

    def test_gate_rearms_after_refill(self):
        inst = _make_instance(cont_assist_time=1.0, slot_empty=True)
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        monitor._check_tangle(current_tool=0)
        # Refill: slot ready again, real tangle develops
        inst._slot_empty_flag = False
        inst._info["cont_assist_time"] = 1.0
        monitor._check_tangle(current_tool=0)   # arms
        inst._info["cont_assist_time"] = 4.5
        monitor._check_tangle(current_tool=0)   # fires (verify window 0)
        assert _pause_calls(gcode)

    def test_gate_failure_fails_closed_to_detection(self):
        # Broken slot-status read must keep the detector alive, not mute it
        inst = _make_instance(cont_assist_time=1.0)
        inst._is_slot_empty = Mock(side_effect=RuntimeError("boom"))
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        monitor._check_tangle(current_tool=0)
        inst._info["cont_assist_time"] = 4.5
        monitor._check_tangle(current_tool=0)
        assert _pause_calls(gcode)


class TestVerdictWindow:
    """Threshold crossing arms a verdict window instead of pausing."""

    def _cross_threshold(self, monitor, inst, t0=100.0):
        """Drive two growing samples to cross the 4.0 threshold at t0."""
        inst._info["cont_assist_time"] = 1.0
        monitor._check_tangle(current_tool=0, eventtime=t0 - 1.0)
        inst._info["cont_assist_time"] = 4.2
        monitor._check_tangle(current_tool=0, eventtime=t0)

    def test_crossing_arms_window_without_pause(self):
        inst = _make_instance()
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0, tangle_verify_time=10.0)
        self._cross_threshold(monitor, inst)
        assert monitor._pt_suspect_since == 100.0
        assert not _pause_calls(gcode)
        msgs = [
            c for c in gcode.respond_info.call_args_list
            if "Possible tangle" in str(c)
        ]
        assert len(msgs) == 1

    def test_runout_verdict_slot_empty_within_window(self):
        inst = _make_instance()
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0, tangle_verify_time=10.0)
        self._cross_threshold(monitor, inst)
        inst._slot_empty_flag = True
        inst._info["cont_assist_time"] = 0.0
        monitor._check_tangle(current_tool=0, eventtime=104.0)
        assert monitor._pt_suspect_since is None
        assert not _pause_calls(gcode)
        msgs = [
            c for c in gcode.respond_info.call_args_list
            if "Not a tangle" in str(c)
        ]
        assert len(msgs) == 1

    def test_expiry_with_slot_nonempty_pauses(self):
        # Fallback exit: pumping stalls BELOW the hard ceiling (counter
        # frozen at its last value) and the slot never reports empty —
        # only window expiry can resolve this as a tangle.
        inst = _make_instance()
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0, tangle_verify_time=10.0)
        self._cross_threshold(monitor, inst)
        inst._info["cont_assist_time"] = 6.0   # grows, but stays sub-ceiling
        monitor._check_tangle(current_tool=0, eventtime=103.0)
        assert not _pause_calls(gcode)
        monitor._check_tangle(current_tool=0, eventtime=109.9)  # plateau
        assert not _pause_calls(gcode)   # window still open
        monitor._check_tangle(current_tool=0, eventtime=110.0)
        assert _pause_calls(gcode)
        assert monitor._pt_suspect_since is None

    def test_counter_drop_does_not_exit_window(self):
        # ACE1's give-up unwind resets the counter mid-runout and real
        # tangle ramps can dip — a drop must neither clear the window
        # nor prevent the expiry pause.
        inst = _make_instance()
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0, tangle_verify_time=10.0)
        self._cross_threshold(monitor, inst)
        inst._info["cont_assist_time"] = 0.0
        monitor._check_tangle(current_tool=0, eventtime=105.0)
        assert monitor._pt_suspect_since == 100.0
        monitor._check_tangle(current_tool=0, eventtime=110.5)
        assert _pause_calls(gcode)

    def test_window_cleared_by_live_toggle(self):
        inst = _make_instance()
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0, tangle_verify_time=10.0)
        self._cross_threshold(monitor, inst)
        monitor.set_tangle_detection_enabled(True)
        assert monitor._pt_suspect_since is None
        monitor._check_tangle(current_tool=0, eventtime=115.0)
        assert not _pause_calls(gcode)

    def test_zero_window_pauses_immediately(self):
        inst = _make_instance()
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0, tangle_verify_time=0.0)
        self._cross_threshold(monitor, inst)
        assert _pause_calls(gcode)


class TestHardCeiling:
    """Continuous pumping at/above tangle_pump_time_hard is a tangle NOW.

    Starved pumping is firmware-capped below the ceiling (ACE1 give-up
    ~5-6 s, ACE2 retry cap ~3.9 s), so reaching it
    proves filament is present and blocked; no need to wait out the
    verdict window (cuts ACE1 real-tangle latency from ~15 s to ~8 s).
    """

    def _cross(self, monitor, inst, t0=100.0):
        inst._info["cont_assist_time"] = 1.0
        monitor._check_tangle(current_tool=0, eventtime=t0 - 1.0)
        inst._info["cont_assist_time"] = 4.2
        monitor._check_tangle(current_tool=0, eventtime=t0)

    def test_ceiling_fires_inside_window_before_expiry(self):
        inst = _make_instance()
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0, tangle_verify_time=10.0)
        self._cross(monitor, inst)
        inst._info["cont_assist_time"] = 8.3
        monitor._check_tangle(current_tool=0, eventtime=103.0)  # 7s early
        assert _pause_calls(gcode)
        assert monitor._pt_suspect_since is None

    def test_ceiling_at_crossing_fires_without_window(self):
        # Detection re-enabled mid-tangle: the crossing sample is already
        # above the ceiling - pause immediately, no verdict window.
        inst = _make_instance(cont_assist_time=7.0)
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0, tangle_verify_time=10.0)
        monitor._check_tangle(current_tool=0, eventtime=100.0)  # arms only
        assert not _pause_calls(gcode)
        inst._info["cont_assist_time"] = 8.5   # fresh growing sample
        monitor._check_tangle(current_tool=0, eventtime=101.0)
        assert _pause_calls(gcode)
        assert monitor._pt_suspect_since is None

    def test_plateau_above_ceiling_does_not_fire_early(self):
        # A frozen counter re-read (same 1 Hz sample) proves nothing even
        # above the ceiling - the two-distinct-samples rule holds; expiry
        # remains the exit.
        inst = _make_instance()
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0, tangle_verify_time=10.0)
        self._cross(monitor, inst)
        inst._info["cont_assist_time"] = 8.3
        monitor._check_tangle(current_tool=0, eventtime=101.0)  # fires
        gcode.run_script_from_command.reset_mock()
        # New detector lifecycle after the fire: re-enable, stale sample 9.0
        monitor.set_tangle_detection_enabled(True)
        inst._info["cont_assist_time"] = 9.0
        monitor._check_tangle(current_tool=0, eventtime=102.0)  # arms
        monitor._check_tangle(current_tool=0, eventtime=102.1)  # re-read
        monitor._check_tangle(current_tool=0, eventtime=102.2)  # re-read
        assert not _pause_calls(gcode)

    def test_ceiling_floor_clamped(self):
        monitor, *_ = _make_monitor()
        assert monitor.tangle_pump_time_hard == \
            RunoutMonitor.DEFAULT_TANGLE_HARD_LIMIT
        low, *_ = _make_monitor(tangle_pump_time_hard=2.0)
        assert low.tangle_pump_time_hard == \
            RunoutMonitor.TANGLE_HARD_LIMIT_FLOOR


class TestAce2ImmediateVerdict:
    """ACE2's slot state is sensor-live: a runout reports 'empty' long
    before its starved pumping starts, and that pumping self-caps below
    the threshold — so a crossing with a non-empty slot IS a tangle and
    pauses at the crossing, ~verify-window seconds sooner than ACE1."""

    def test_sensor_live_generation_fires_at_crossing(self):
        inst = _make_instance(protocol_name="ace2_proto")
        inst.protocol.feed_assist_causes_busy = lambda: True
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0, tangle_verify_time=10.0)
        inst._info["cont_assist_time"] = 1.0
        monitor._check_tangle(current_tool=0, eventtime=100.0)
        inst._info["cont_assist_time"] = 4.2
        monitor._check_tangle(current_tool=0, eventtime=101.0)
        assert _pause_calls(gcode)
        assert monitor._pt_suspect_since is None

    def test_unknown_generation_falls_back_to_window(self):
        # Protocol read failure must not fast-pause: the windowed (ACE1)
        # path is the safe default because it cannot false-pause a runout.
        inst = _make_instance()
        inst.protocol.feed_assist_causes_busy = Mock(
            side_effect=RuntimeError("boom"))
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0, tangle_verify_time=10.0)
        inst._info["cont_assist_time"] = 1.0
        monitor._check_tangle(current_tool=0, eventtime=100.0)
        inst._info["cont_assist_time"] = 4.2
        monitor._check_tangle(current_tool=0, eventtime=101.0)
        assert not _pause_calls(gcode)
        assert monitor._pt_suspect_since == 101.0

    def test_ace2_runout_never_reaches_the_immediate_verdict(self):
        # Empty slot -> the gate returns before the crossing logic, so the
        # sensor-live fast path cannot false-pause a runout even with the
        # threshold below the starved retry cap.
        inst = _make_instance(protocol_name="ace2_proto", slot_empty=True)
        inst.protocol.feed_assist_causes_busy = lambda: True
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=3.0, tangle_verify_time=10.0)
        for i, v in enumerate([0.9, 1.91, 2.91, 3.92, 0.0] * 2):
            inst._info["cont_assist_time"] = v
            monitor._check_tangle(current_tool=0, eventtime=100.0 + i)
        assert not _pause_calls(gcode)


class TestDetectorInstanceResolution:
    """The detector must watch the CURRENT tool's instance, never a stale
    assist on another instance (a stale outgoing
    index pointed the detector - and its empty-slot gate - at the wrong
    ACE, blinding it to a 31 s tangle on the loaded tool)."""

    def test_prefers_current_tools_instance_over_stale(self):
        stale = _make_instance(feed_assist_index=3, cont_assist_time=0.0)
        active = _make_instance(
            protocol_name="ace2_proto", feed_assist_index=0,
            cont_assist_time=1.0)
        monitor, *_ = _make_monitor(instances=[stale, active])
        # T4 = instance 1, slot 0
        assert monitor._get_active_assist_instance(current_tool=4) is active

    def test_no_fallback_to_stale_when_current_assist_off(self):
        current = _make_instance(feed_assist_index=-1)
        stale = _make_instance(feed_assist_index=3, cont_assist_time=9.9)
        monitor, *_ = _make_monitor(instances=[current, stale])
        # T0 = instance 0, whose assist is off: nothing valid to monitor
        assert monitor._get_active_assist_instance(current_tool=0) is None

    def test_wrong_slot_on_current_instance_not_monitored(self):
        inst = _make_instance(feed_assist_index=2, cont_assist_time=9.9)
        monitor, *_ = _make_monitor(instances=[inst])
        # T0 = slot 0, but assist sits on slot 2: stale, don't monitor
        assert monitor._get_active_assist_instance(current_tool=0) is None

    def test_unresolvable_tool_falls_back_to_scan(self):
        inst = _make_instance(feed_assist_index=0, cont_assist_time=1.0)
        monitor, *_ = _make_monitor(instances=[inst])
        # T4 needs instance 1 which is not configured: legacy scan
        assert monitor._get_active_assist_instance(current_tool=4) is inst
        assert monitor._get_active_assist_instance() is inst


class TestHelpers:
    def test_get_active_assist_returns_pumping_instance(self):
        inst = _make_instance(cont_assist_time=2.5)
        monitor, *_ = _make_monitor(instances=[inst])
        assert monitor._get_active_assist_instance() is inst

    def test_get_active_assist_skips_inactive_instances(self):
        inactive = _make_instance(feed_assist_index=-1, cont_assist_time=99.0)
        active = _make_instance(feed_assist_index=0, cont_assist_time=2.0)
        monitor, *_ = _make_monitor(instances=[inactive, active])
        assert monitor._get_active_assist_instance() is active

    def test_get_active_assist_accepts_gen2_instances(self):
        gen2 = _make_instance(
            protocol_name="ace2_proto", feed_assist_index=0,
            cont_assist_time=99.0,
        )
        monitor, *_ = _make_monitor(instances=[gen2])
        assert monitor._get_active_assist_instance() is gen2

    def test_get_active_assist_returns_none_when_no_instances(self):
        monitor, *_ = _make_monitor(instances=[])
        assert monitor._get_active_assist_instance() is None


class TestLiveToggle:
    def test_enable_sets_flag_and_clears_state(self):
        monitor, *_ = _make_monitor(tangle_detection=False)
        monitor._pt_last_value_s = 2.5
        monitor._pt_phase_armed = True
        monitor.set_tangle_detection_enabled(True)
        assert monitor.tangle_detection_enabled is True
        assert monitor._pt_phase_armed is False
        assert monitor._pt_last_value_s == 0.0

    def test_disable_sets_flag_and_clears_state(self):
        monitor, *_ = _make_monitor(tangle_detection=True)
        monitor._pt_last_value_s = 1.5
        monitor._pt_phase_armed = True
        monitor.set_tangle_detection_enabled(False)
        assert monitor.tangle_detection_enabled is False
        assert monitor._pt_phase_armed is False
        assert monitor._pt_last_value_s == 0.0


class TestTangleInMonitorLoop:
    def test_check_tangle_called_during_normal_printing(self):
        monitor, manager, gcode = _make_monitor(tangle_detection=True)
        _setup_printing_state(monitor, manager, gcode)
        with patch.object(monitor, "_check_tangle") as mock_check:
            monitor._monitor_runout(1000.0)
            mock_check.assert_called_once()

    def test_check_tangle_not_called_when_disabled(self):
        monitor, manager, gcode = _make_monitor(tangle_detection=False)
        _setup_printing_state(monitor, manager, gcode)
        with patch.object(monitor, "_check_tangle") as mock_check:
            monitor._monitor_runout(1000.0)
            mock_check.assert_not_called()

    def test_check_tangle_not_called_during_runout_handling(self):
        monitor, manager, gcode = _make_monitor(tangle_detection=True)
        _setup_printing_state(monitor, manager, gcode)
        monitor.runout_handling_in_progress = True
        with patch.object(monitor, "_check_tangle") as mock_check:
            monitor._monitor_runout(1000.0)
            mock_check.assert_not_called()


class TestStaleSampleGuard:
    """Firing requires two distinct growing samples — re-reads of the
    same 1 Hz heartbeat value (50 ms monitor ticks) must never fire."""

    def test_same_sample_above_threshold_does_not_fire(self):
        inst = _make_instance(cont_assist_time=5.0)
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        monitor._check_tangle(current_tool=0)  # arms on first read
        monitor._check_tangle(current_tool=0)  # same sample re-read
        monitor._check_tangle(current_tool=0)  # still the same sample
        assert not gcode.run_script_from_command.called
        assert monitor._pt_phase_armed is True

    def test_fresh_growing_sample_after_plateau_fires(self):
        inst = _make_instance(cont_assist_time=5.0)
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        monitor._check_tangle(current_tool=0)  # arms
        monitor._check_tangle(current_tool=0)  # same sample, no fire
        inst._info["cont_assist_time"] = 6.0   # fresh heartbeat, still growing
        monitor._check_tangle(current_tool=0)
        pause_calls = [
            c for c in gcode.run_script_from_command.call_args_list
            if "PAUSE" in str(c)
        ]
        assert pause_calls, "expected PAUSE on fresh growing sample"


class TestPinReEnableEdge:
    """Flipping [output_pin TANGLE_DETECTION] directly (SET_PIN from the
    dashboard) bypasses set_tangle_detection_enabled — the monitor loop
    must clear stale phase state on the off→on edge itself."""

    def _mount_pin_in_loop(self, monitor, pin_value):
        pin = Mock()
        pin.get_status.side_effect = lambda _et: dict(pin_value)
        orig = monitor.printer.lookup_object.side_effect
        monitor.printer.lookup_object.side_effect = lambda name, default=None: (
            pin if name == "output_pin TANGLE_DETECTION"
            else orig(name, default)
        )

    def test_pin_off_freezes_state_pin_on_edge_clears_it(self):
        monitor, manager, gcode = _make_monitor(tangle_detection=True)
        _setup_printing_state(monitor, manager, gcode)
        pin_value = {"value": 0}
        self._mount_pin_in_loop(monitor, pin_value)

        # Stale phase from before the slider was flipped off
        monitor._pt_phase_armed = True
        monitor._pt_last_value_s = 3.0

        monitor._monitor_runout(1000.0)
        assert monitor._pt_phase_armed is True  # frozen, not cleared

        pin_value["value"] = 1
        monitor._monitor_runout(1000.05)  # off→on edge
        assert monitor._pt_phase_armed is False
        assert monitor._pt_last_value_s == 0.0

    def test_no_fire_on_first_tick_after_pin_reenable(self):
        inst = _make_instance(cont_assist_time=4.2)
        monitor, manager, gcode = _make_monitor(
            tangle_detection=True, instances=[inst], tangle_pump_time=4.0)
        _setup_printing_state(monitor, manager, gcode)
        pin_value = {"value": 0}
        self._mount_pin_in_loop(monitor, pin_value)

        # Stale armed phase from a previous pump cycle
        monitor._pt_phase_armed = True
        monitor._pt_last_value_s = 3.0

        monitor._monitor_runout(1000.0)
        pin_value["value"] = 1
        # Two ticks on the same stale 4.2 sample: arm, then plateau skip
        monitor._monitor_runout(1000.05)
        monitor._monitor_runout(1000.10)
        pause_calls = [
            c for c in gcode.run_script_from_command.call_args_list
            if "PAUSE" in str(c)
        ]
        assert not pause_calls, "stale sample must not fire after re-enable"

        # A fresh, still-growing heartbeat sample confirms a real tangle
        inst._info["cont_assist_time"] = 5.3
        monitor._monitor_runout(1000.15)
        pause_calls = [
            c for c in gcode.run_script_from_command.call_args_list
            if "PAUSE" in str(c)
        ]
        assert pause_calls, "fresh growing sample must fire"


class TestOutputPinGate:
    """[output_pin TANGLE_DETECTION] is authoritative when present; flag is fallback."""

    def _mount_pin(self, monitor, value):
        """Attach a fake output_pin via printer.lookup_object."""
        pin = Mock()
        pin.get_status.return_value = {"value": value}
        monitor.printer.lookup_object.side_effect = lambda name, default=None: (
            pin if name == "output_pin TANGLE_DETECTION" else default
        )
        return pin

    def test_no_pin_uses_flag_on(self):
        monitor, *_ = _make_monitor(tangle_detection=True)
        monitor.printer.lookup_object.side_effect = (
            lambda name, default=None: default
        )
        assert monitor._is_tangle_detection_active() is True

    def test_no_pin_uses_flag_off(self):
        monitor, *_ = _make_monitor(tangle_detection=False)
        monitor.printer.lookup_object.side_effect = (
            lambda name, default=None: default
        )
        assert monitor._is_tangle_detection_active() is False

    def test_pin_on_with_flag_on(self):
        monitor, *_ = _make_monitor(tangle_detection=True)
        self._mount_pin(monitor, value=1)
        assert monitor._is_tangle_detection_active() is True

    def test_pin_off_disables_even_when_flag_on(self):
        monitor, *_ = _make_monitor(tangle_detection=True)
        self._mount_pin(monitor, value=0)
        assert monitor._is_tangle_detection_active() is False

    def test_pin_on_overrides_flag_off(self):
        # Pin wins: user added the slider and flipped it on, detector runs
        # even though the static config flag is False.
        monitor, *_ = _make_monitor(tangle_detection=False)
        self._mount_pin(monitor, value=1)
        assert monitor._is_tangle_detection_active() is True

    def test_pin_read_failure_falls_back_to_flag(self):
        monitor, *_ = _make_monitor(tangle_detection=True)
        pin = Mock()
        pin.get_status.side_effect = RuntimeError("boom")
        monitor.printer.lookup_object.side_effect = lambda name, default=None: (
            pin if name == "output_pin TANGLE_DETECTION" else default
        )
        assert monitor._is_tangle_detection_active() is True
