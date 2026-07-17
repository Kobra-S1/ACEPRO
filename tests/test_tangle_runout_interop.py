"""Tangle detection vs endless-spool runout interop.

Each test replays a hardware-recorded heartbeat trace (BIQU printer,
captured from klippy.log with status_debug_logging) through _check_tangle
at the 1 Hz heartbeat cadence, asserting the verdict the fix must reach:

- ACE1 spool runout: slot stays 'ready' until the firmware's starved
  give-up (~5 s pump -> 'unwinding' resets the counter -> 'empty'), so
  the threshold crossing happens ~4 s BEFORE the empty report.  Must
  NOT pause (the false pause that motivated the fix).
- ACE1 real tangle: slot never leaves 'ready', pumping grows unbounded.
  Must pause at verdict-window expiry.
- ACE2 spool runout: slot reports 'empty' immediately at sensor-clear,
  ~100 s before the starved retry cycling (ramps to ~3.92 s, self-reset,
  forever).  Must not pause even with the threshold at its floor, where
  every retry ramp crosses it.
- ACE2 real tangle: unbounded growth with the slot non-empty.  Must
  pause at verdict-window expiry.
"""
from unittest.mock import Mock
from ace.runout_monitor import RunoutMonitor


def _make_instance(protocol_name="ace1_json", slot_empty=False):
    inst = Mock()
    inst.protocol_name = protocol_name
    inst._feed_assist_index = 2
    inst._info = {"cont_assist_time": 0.0}
    inst._slot_empty_flag = slot_empty
    inst._is_slot_empty = lambda idx: inst._slot_empty_flag
    return inst


def _make_monitor(insts, tangle_pump_time, tangle_verify_time=10.0):
    printer = Mock()
    gcode = Mock()
    reactor = Mock()
    reactor.NOW = 0.0
    reactor.NEVER = float("inf")
    reactor.monotonic = Mock(return_value=0.0)
    manager = Mock()
    manager.toolchange_in_progress = False
    manager.instances = insts if isinstance(insts, list) else [insts]
    monitor = RunoutMonitor(
        printer, gcode, reactor, Mock(), manager,
        tangle_detection=True,
        tangle_pump_time=tangle_pump_time,
        tangle_verify_time=tangle_verify_time,
    )
    return monitor, gcode


def _pause_calls(gcode):
    return [
        c for c in gcode.run_script_from_command.call_args_list
        if "PAUSE" in str(c)
    ]


def _replay(monitor, inst, samples, tool=2, t0=100.0):
    """Feed (cont_assist_time, slot_empty) heartbeat samples at 1 Hz.

    Returns the eventtime of the first PAUSE, or None.
    """
    gcode = monitor.gcode
    for i, (value, slot_empty) in enumerate(samples):
        inst._info["cont_assist_time"] = value
        inst._slot_empty_flag = slot_empty
        monitor._check_tangle(tool, eventtime=t0 + i)
        if _pause_calls(gcode):
            return t0 + i
    return None


class TestAce1SpoolRunout:
    """klippy_biqu2.log t=3767.9-3776: ramp fires at 4.2 s while the slot
    still reads 'ready'; unwind resets the counter; 'empty' arrives ~4 s
    after the crossing."""

    TRACE = [
        (0.1, False), (0.0, False),          # normal assist pulse
        (0.2, False), (1.2, False),          # tail leaves gears, ramp
        (2.2, False), (3.2, False),
        (4.2, False),                        # threshold crossing (old: PAUSE)
        (0.0, False),                        # firmware gives up: unwinding
        (0.0, False),                        # still unwinding
        (0.0, True),                         # slot reports empty
    ] + [(0.0, True)] * 30                   # minutes of tail transit

    def test_no_pause_and_runout_verdict(self):
        inst = _make_instance()
        monitor, gcode = _make_monitor(inst, tangle_pump_time=4.0)
        paused_at = _replay(monitor, inst, self.TRACE)
        assert paused_at is None, f"false tangle pause at t={paused_at}"
        assert monitor._pt_suspect_since is None
        msgs = [
            c for c in gcode.respond_info.call_args_list
            if "Not a tangle" in str(c)
        ]
        assert len(msgs) == 1


class TestAce1RealTangle:
    """klippy_biqu3.log t=5517.7-5531.7: noisy ramp (partial slips), then
    unbounded growth with the slot 'ready' throughout.  The hard ceiling
    (8.0 s) fires as soon as the counter proves filament is present and
    blocked — starved pumping is firmware-capped at ~5-6 s, so it can
    never reach the ceiling.  Field pain this fixes: the pure-window
    verdict paused only after 15.4 s of air-printing."""

    TRACE = [
        (0.8, False), (0.2, False),          # tangle tightening, slips
        (1.3, False), (2.3, False),
        (3.3, False), (4.3, False),          # threshold crossing -> window
    ] + [(4.3 + i, False) for i in range(1, 15)]   # 5.3 ... 18.3, growing

    def test_pause_via_hard_ceiling_before_window_expiry(self):
        inst = _make_instance()
        monitor, gcode = _make_monitor(
            inst, tangle_pump_time=4.0, tangle_verify_time=10.0)
        paused_at = _replay(monitor, inst, self.TRACE)
        # Crossing at index 5 (t=105); first sample >= 8.0 is 8.3 at
        # t=109 -> ceiling pause, 6 s before the old expiry (t=115)
        assert paused_at == 109.0
        prompts = [
            c for c in gcode.run_script_from_command.call_args_list
            if "Spool Tangle Detected" in str(c)
        ]
        assert prompts, "expected the tangle prompt after the pause"


class TestAce2SpoolRunout:
    """klippy_biqu4.log t=6782-6900: 'empty' reported immediately at
    sensor-clear, normal pulses continue ~100 s, then starved retry
    cycling ramps to 3.92 s and self-resets, forever."""

    CYCLE = [(0.9, True), (1.91, True), (2.91, True), (3.92, True),
             (0.0, True)]
    TRACE = [
        (0.47, False),                       # normal printing
        (0.0, True),                         # slot empty at sensor-clear
        (0.58, True), (0.71, True),          # tail still in gears, pulses
    ] + CYCLE * 5                            # starved retry cycling

    def test_no_pause_with_default_threshold(self):
        inst = _make_instance(protocol_name="ace2_proto")
        monitor, gcode = _make_monitor(inst, tangle_pump_time=5.0)
        assert _replay(monitor, inst, self.TRACE) is None
        msgs = [
            c for c in gcode.respond_info.call_args_list
            if "ran out at the ACE" in str(c)
        ]
        assert len(msgs) == 1, "one info line per depletion, not per cycle"

    def test_no_pause_even_at_threshold_floor(self):
        # Every 3.92 s retry ramp crosses a floor-level threshold — only
        # the empty-slot gate stands between that and a pause loop.
        inst = _make_instance(protocol_name="ace2_proto")
        monitor, gcode = _make_monitor(
            inst, tangle_pump_time=RunoutMonitor.TANGLE_PUMP_TIME_FLOOR)
        assert _replay(monitor, inst, self.TRACE) is None
        assert not _pause_calls(gcode)


class TestAce2RealTangle:
    """Against real resistance ACE2 pumps
    indefinitely, cont_assist_time grows unbounded, slot stays non-empty.
    ACE2's slot state is sensor-live (a runout reports 'empty' ~100 s
    before starved pumping starts, and that pumping caps at ~3.9 s), so a
    crossing with a non-empty slot IS a tangle — pause at the crossing.
    Field pain this fixes: a held-spool tangle on T4 ran undetected 31 s
    (that was the wrong-instance bug) and the windowed verdict would have
    added 10 s on top of the threshold even once seen."""

    TRACE = [(0.9 + i, False) for i in range(0, 20)]   # 0.9, 1.9, ... 19.9

    def test_pause_at_crossing_when_generation_known(self):
        inst = _make_instance(protocol_name="ace2_proto")
        inst.protocol.feed_assist_causes_busy = lambda: True
        monitor, _gcode = _make_monitor(
            inst, tangle_pump_time=5.0, tangle_verify_time=10.0)
        paused_at = _replay(monitor, inst, self.TRACE)
        # 0.9 arms; first armed growing sample >= 5.0 is 5.9 (index 5,
        # t=105) -> immediate verdict, no window
        assert paused_at == 105.0

    def test_ceiling_fallback_when_generation_unknown(self):
        # Unreadable protocol capability: safe windowed path, but the
        # hard ceiling still caps the wait (8.9 at t=108, not expiry 115)
        inst = _make_instance(protocol_name="ace2_proto")
        inst.protocol.feed_assist_causes_busy = Mock(
            side_effect=RuntimeError("boom"))
        monitor, _gcode = _make_monitor(
            inst, tangle_pump_time=5.0, tangle_verify_time=10.0)
        paused_at = _replay(monitor, inst, self.TRACE)
        assert paused_at == 108.0


class TestStaleOutgoingAssistDoesNotBlindDetector:
    """Stale-outgoing-assist regression: after the
    T3→T4 endless swap, the outgoing ACE[0] kept a stale assist index on
    its genuinely empty slot.  The first-match instance scan monitored
    ACE[0]; the empty-slot gate then (correctly, for that slot) suppressed
    detection — and a 31 s held-filament tangle on the freshly loaded T4
    (ACE[1]) went completely undetected.  The detector must monitor the
    CURRENT tool's instance."""

    def test_tangle_on_loaded_tool_detected_despite_stale_outgoing(self):
        stale_ace0 = _make_instance(slot_empty=True)
        stale_ace0._feed_assist_index = 3           # T3 leftover
        stale_ace0._info["cont_assist_time"] = 0.0
        tangled_ace1 = _make_instance(protocol_name="ace2_proto")
        tangled_ace1._feed_assist_index = 0          # T4 = inst 1, slot 0
        tangled_ace1.protocol.feed_assist_causes_busy = lambda: True
        monitor, gcode = _make_monitor(
            [stale_ace0, tangled_ace1],
            tangle_pump_time=5.0, tangle_verify_time=10.0)

        # Replay ACE[1]'s held-filament growth (field log reached 31.7 s)
        for i, value in enumerate([0.9, 1.9, 2.9, 3.9, 4.9, 5.9, 6.9]):
            tangled_ace1._info["cont_assist_time"] = value
            monitor._check_tangle(4, eventtime=100.0 + i)

        assert _pause_calls(gcode), (
            "tangle on the loaded tool must fire even with a stale assist "
            "index on the outgoing instance")
        # And the stale instance's empty slot must not produce the
        # misleading 'spool ran out' suppression message for T4
        msgs = [
            c for c in gcode.respond_info.call_args_list
            if "ran out at the ACE" in str(c)
        ]
        assert not msgs
