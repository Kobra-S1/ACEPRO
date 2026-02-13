"""
Tests for tangle detection in RunoutMonitor.

Tangle detection fires when ALL of these hold simultaneously:
    1. Print state is "printing"
    2. ACE feed-assist is active
    3. RDM detect pin shows filament present
    4. Nozzle sensor shows filament present
    5. Extruder moved >= TANGLE_DETECTION_LENGTH since window reset
    6. RDM encoder pulse count has NOT changed since window reset

Each test disables exactly one condition and verifies no false positive,
then a positive test confirms detection when all conditions are true.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch, call
from ace.runout_monitor import RunoutMonitor
from ace.config import SENSOR_TOOLHEAD, SENSOR_RDM


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_monitor(tangle_detection=True):
    """Create a RunoutMonitor wired for tangle detection tests.

    Returns (monitor, printer, gcode, reactor, manager) so tests can
    configure behaviour on the mocks.
    """
    printer = Mock()
    gcode = Mock()
    reactor = Mock()
    reactor.NOW = 0.0
    reactor.NEVER = float("inf")
    endless_spool = Mock()
    manager = Mock()
    manager.toolchange_in_progress = False

    monitor = RunoutMonitor(
        printer, gcode, reactor, endless_spool, manager,
        runout_debounce_count=1,
        tangle_detection=tangle_detection,
    )
    return monitor, printer, gcode, reactor, manager


def _setup_printing_state(monitor, printer, manager, gcode,
                          feed_assist=True,
                          rdm_present=True,
                          nozzle_present=True,
                          encoder_pulse=100):
    """Configure mocks so the monitor loop reaches tangle checking.

    Sets print state to "printing", sensor states, feed_assist, and
    the encoder pulse count.  Also puts the monitor in a state where
    it has already established a baseline (prev_toolhead_sensor_state
    is set) so the loop doesn't short-circuit.
    """
    # print_stats returns "printing"
    stats_obj = Mock()
    stats_obj.get_status.return_value = {"state": "printing"}
    printer.lookup_object.side_effect = _printer_lookup(stats_obj, encoder_pulse)

    # save_variables
    save_vars = Mock()
    save_vars.allVariables = {"ace_current_index": 0}
    # Override lookup_object for save_variables
    original_side_effect = printer.lookup_object.side_effect

    def lookup(name, default=None):
        if name == "print_stats":
            return stats_obj
        if name == "save_variables":
            return save_vars
        if name == "extruder":
            ext = Mock()
            ext.find_past_position = Mock(return_value=0.0)
            return ext
        if name == "mcu":
            mcu = Mock()
            mcu.estimated_print_time = Mock(return_value=0.0)
            return mcu
        if default is not None:
            return default
        raise Exception(f"Object {name} not found")

    printer.lookup_object.side_effect = lookup

    # Sensor states
    def get_switch(sensor_name):
        if sensor_name == SENSOR_RDM:
            return rdm_present
        if sensor_name == SENSOR_TOOLHEAD:
            return nozzle_present
        return False

    manager.get_switch_state.side_effect = get_switch
    manager.is_feed_assist_active.return_value = feed_assist
    manager.get_rdm_encoder_pulse.return_value = encoder_pulse

    # Pre-set monitor state so it doesn't short-circuit on baseline init
    monitor.prev_toolhead_sensor_state = nozzle_present
    monitor.last_printing_active = True
    monitor.last_print_state = "printing"
    monitor.runout_detection_active = True
    monitor.runout_handling_in_progress = False


def _printer_lookup(stats_obj, encoder_pulse):
    """Build a printer.lookup_object side_effect function."""
    def lookup(name, default=None):
        if name == "print_stats":
            return stats_obj
        if name == "save_variables":
            sv = Mock()
            sv.allVariables = {"ace_current_index": 0}
            return sv
        if name == "extruder":
            ext = Mock()
            ext.find_past_position = Mock(return_value=0.0)
            return ext
        if name == "mcu":
            mcu = Mock()
            mcu.estimated_print_time = Mock(return_value=0.0)
            return mcu
        if default is not None:
            return default
        raise Exception(f"Object {name} not found")
    return lookup


# ── Initialization ───────────────────────────────────────────────────────

class TestTangleDetectionInit:
    """Verify tangle_detection config is handled correctly."""

    def test_tangle_disabled_by_default(self):
        """Default: tangle detection off."""
        monitor, *_ = _make_monitor(tangle_detection=False)
        assert monitor.tangle_detection_enabled is False
        assert monitor._tangle_runout_pos is None
        assert monitor._tangle_encoder_snapshot is None

    def test_tangle_enabled(self):
        """Explicitly enabling tangle detection."""
        monitor, *_ = _make_monitor(tangle_detection=True)
        assert monitor.tangle_detection_enabled is True

    def test_default_detection_length(self):
        """Default detection length is DEFAULT_TANGLE_DETECTION_LENGTH."""
        monitor, *_ = _make_monitor()
        assert monitor.tangle_detection_length == RunoutMonitor.DEFAULT_TANGLE_DETECTION_LENGTH

    def test_custom_detection_length(self):
        """tangle_detection_length overrides the default."""
        printer = Mock()
        gcode = Mock()
        reactor = Mock()
        reactor.NOW = 0.0
        reactor.NEVER = float("inf")
        monitor = RunoutMonitor(
            printer, gcode, reactor, Mock(), Mock(),
            tangle_detection=True,
            tangle_detection_length=25.0,
        )
        assert monitor.tangle_detection_length == 25.0


# ── Condition gates (each disables one condition) ────────────────────────

class TestTangleConditionGates:
    """Each test ensures that removing one condition prevents detection."""

    def setup_method(self):
        """Create a tangle-enabled monitor."""
        (self.monitor, self.printer, self.gcode,
         self.reactor, self.manager) = _make_monitor(tangle_detection=True)

    def _run_check(self, extruder_pos=0.0):
        """Run _check_tangle with the given extruder position."""
        # Resolve extruder so _check_tangle can work
        ext = Mock()
        ext.find_past_position = Mock(return_value=extruder_pos)
        mcu = Mock()
        mcu.estimated_print_time = Mock(return_value=0.0)
        self.monitor._extruder = ext
        self.monitor._estimated_print_time = mcu.estimated_print_time

        self.monitor._check_tangle(1000.0, current_tool=0)

    def test_no_tangle_when_feed_assist_off(self):
        """Condition 2 fails: feed-assist not active."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode,
            feed_assist=False, encoder_pulse=100)

        # Set a window manually
        self.monitor._tangle_runout_pos = 10.0
        self.monitor._tangle_encoder_snapshot = 100

        self._run_check(extruder_pos=20.0)  # Past window

        # Should have reset window, not fired
        assert self.monitor._tangle_runout_pos is None
        self.gcode.run_script_from_command.assert_not_called()

    def test_no_tangle_when_rdm_absent(self):
        """Condition 3 fails: RDM shows no filament."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode,
            rdm_present=False, encoder_pulse=100)

        self.monitor._tangle_runout_pos = 10.0
        self.monitor._tangle_encoder_snapshot = 100

        self._run_check(extruder_pos=20.0)

        assert self.monitor._tangle_runout_pos is None
        self.gcode.run_script_from_command.assert_not_called()

    def test_no_tangle_when_nozzle_absent(self):
        """Condition 4 fails: nozzle sensor shows no filament."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode,
            nozzle_present=False, encoder_pulse=100)

        self.monitor._tangle_runout_pos = 10.0
        self.monitor._tangle_encoder_snapshot = 100

        self._run_check(extruder_pos=20.0)

        assert self.monitor._tangle_runout_pos is None
        self.gcode.run_script_from_command.assert_not_called()

    def test_no_tangle_when_extruder_below_window(self):
        """Condition 5 fails: extruder hasn't moved enough."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode,
            encoder_pulse=100)

        # Window at 15.0, extruder at 5.0
        self.monitor._tangle_runout_pos = 15.0
        self.monitor._tangle_encoder_snapshot = 100

        self._run_check(extruder_pos=5.0)

        # No detection, window still active
        assert self.monitor._tangle_runout_pos == 15.0
        self.gcode.run_script_from_command.assert_not_called()

    def test_no_tangle_when_encoder_moves(self):
        """Condition 6 fails: encoder pulse count changed → filament is flowing."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode,
            encoder_pulse=105)  # Changed from snapshot

        self.monitor._tangle_runout_pos = 10.0
        self.monitor._tangle_encoder_snapshot = 100  # Different from 105

        self._run_check(extruder_pos=20.0)

        # Window should have been reset (not fired)
        # encoder moved → _reset_tangle_window was called
        assert self.monitor._tangle_encoder_snapshot == 105
        self.gcode.run_script_from_command.assert_not_called()

    def test_no_tangle_when_disabled(self):
        """tangle_detection: False → _check_tangle never runs."""
        monitor, printer, gcode, reactor, manager = _make_monitor(
            tangle_detection=False)
        _setup_printing_state(
            monitor, printer, manager, gcode, encoder_pulse=100)

        # Even with a rigged window it should never fire because the
        # calling code checks tangle_detection_enabled before calling
        assert monitor.tangle_detection_enabled is False

    def test_no_tangle_when_rdm_not_tracker(self):
        """If RDM is a plain switch sensor, encoder is None → skip."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode,
            encoder_pulse=100)

        # Override: encoder pulse returns None (not a tracker)
        self.manager.get_rdm_encoder_pulse.return_value = None

        self.monitor._tangle_runout_pos = 10.0
        self.monitor._tangle_encoder_snapshot = 100

        self._run_check(extruder_pos=20.0)

        # Should not fire, should not crash
        self.gcode.run_script_from_command.assert_not_called()


# ── Positive detection ──────────────────────────────────────────────────

class TestTanglePositiveDetection:
    """Verify tangle fires when all 6 conditions hold."""

    def setup_method(self):
        (self.monitor, self.printer, self.gcode,
         self.reactor, self.manager) = _make_monitor(tangle_detection=True)

    def test_tangle_fires_when_all_conditions_met(self):
        """All conditions true → pause + prompt."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode,
            feed_assist=True, rdm_present=True, nozzle_present=True,
            encoder_pulse=100)

        # Set window: runout pos = 10, snapshot = 100
        self.monitor._tangle_runout_pos = 10.0
        self.monitor._tangle_encoder_snapshot = 100

        # Resolve extruder
        ext = Mock()
        ext.find_past_position = Mock(return_value=20.0)  # Past 10.0
        mcu = Mock()
        mcu.estimated_print_time = Mock(return_value=0.0)
        self.monitor._extruder = ext
        self.monitor._estimated_print_time = mcu.estimated_print_time

        self.monitor._check_tangle(1000.0, current_tool=0)

        # Should have called PAUSE
        pause_calls = [
            c for c in self.gcode.run_script_from_command.call_args_list
            if "PAUSE" in str(c)
        ]
        assert len(pause_calls) > 0, "PAUSE should have been called"

        # Should have shown a tangle prompt
        prompt_calls = [
            c for c in self.gcode.run_script_from_command.call_args_list
            if "Spool Tangle" in str(c)
        ]
        assert len(prompt_calls) > 0, "Tangle prompt should have been shown"

        # Window should be reset after detection
        assert self.monitor._tangle_runout_pos is None

    def test_tangle_logs_warning(self):
        """Tangle detection logs a warning with details."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode,
            feed_assist=True, rdm_present=True, nozzle_present=True,
            encoder_pulse=50)

        self.monitor._tangle_runout_pos = 10.0
        self.monitor._tangle_encoder_snapshot = 50

        ext = Mock()
        ext.find_past_position = Mock(return_value=25.0)
        mcu = Mock()
        mcu.estimated_print_time = Mock(return_value=0.0)
        self.monitor._extruder = ext
        self.monitor._estimated_print_time = mcu.estimated_print_time

        self.monitor._check_tangle(1000.0, current_tool=2)

        # respond_info should mention tangle
        info_calls = [
            str(c) for c in self.gcode.respond_info.call_args_list
        ]
        tangle_msgs = [c for c in info_calls if "tangle" in c.lower()]
        assert len(tangle_msgs) > 0


# ── Window management ────────────────────────────────────────────────────

class TestTangleWindowManagement:
    """Verify the tangle detection window resets correctly."""

    def setup_method(self):
        (self.monitor, self.printer, self.gcode,
         self.reactor, self.manager) = _make_monitor(tangle_detection=True)

    def test_window_initializes_on_first_check(self):
        """First tangle check with no window → creates window."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode,
            encoder_pulse=42)

        assert self.monitor._tangle_runout_pos is None

        # Resolve extruder for reset
        ext = Mock()
        ext.find_past_position = Mock(return_value=100.0)
        mcu = Mock()
        mcu.estimated_print_time = Mock(return_value=0.0)
        self.monitor._extruder = ext
        self.monitor._estimated_print_time = mcu.estimated_print_time

        self.monitor._check_tangle(1000.0, current_tool=0)

        assert self.monitor._tangle_runout_pos == 100.0 + self.monitor.tangle_detection_length
        assert self.monitor._tangle_encoder_snapshot == 42

    def test_window_resets_on_encoder_activity(self):
        """Encoder pulse change → window resets to new position."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode,
            encoder_pulse=55)

        self.monitor._tangle_runout_pos = 50.0
        self.monitor._tangle_encoder_snapshot = 50  # Different from 55

        ext = Mock()
        ext.find_past_position = Mock(return_value=80.0)
        mcu = Mock()
        mcu.estimated_print_time = Mock(return_value=0.0)
        self.monitor._extruder = ext
        self.monitor._estimated_print_time = mcu.estimated_print_time

        self.monitor._check_tangle(1000.0, current_tool=0)

        # Window reset to current extruder pos + detection length
        assert self.monitor._tangle_runout_pos == 80.0 + self.monitor.tangle_detection_length
        assert self.monitor._tangle_encoder_snapshot == 55

    def test_toolchange_resets_window(self):
        """Toolchange in progress → window cleared."""
        self.monitor._tangle_runout_pos = 100.0
        self.monitor._tangle_encoder_snapshot = 50

        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode)
        self.manager.toolchange_in_progress = True

        self.monitor._monitor_runout(1000.0)

        assert self.monitor._tangle_runout_pos is None

    def test_print_stop_resets_window(self):
        """Print stopping resets the tangle window."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode)

        self.monitor._tangle_runout_pos = 100.0
        self.monitor._tangle_encoder_snapshot = 50

        # Simulate print just stopped
        self.monitor.last_printing_active = True

        # Override print_stats to return "complete"
        stats_obj = Mock()
        stats_obj.get_status.return_value = {"state": "complete"}

        def lookup(name, default=None):
            if name == "print_stats":
                return stats_obj
            if name == "save_variables":
                sv = Mock()
                sv.allVariables = {"ace_current_index": 0}
                return sv
            if default is not None:
                return default
            raise Exception(f"Object {name} not found")

        self.printer.lookup_object.side_effect = lookup

        self.monitor._monitor_runout(1000.0)

        assert self.monitor._tangle_runout_pos is None

    def test_pause_resets_window(self):
        """Paused state resets the tangle window."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode)

        self.monitor._tangle_runout_pos = 100.0

        # Override print_stats to return "paused"
        stats_obj = Mock()
        stats_obj.get_status.return_value = {"state": "paused"}

        def lookup(name, default=None):
            if name == "print_stats":
                return stats_obj
            if name == "save_variables":
                sv = Mock()
                sv.allVariables = {"ace_current_index": 0}
                return sv
            if default is not None:
                return default
            raise Exception(f"Object {name} not found")

        self.printer.lookup_object.side_effect = lookup

        self.monitor._monitor_runout(1000.0)

        assert self.monitor._tangle_runout_pos is None


# ── Integration with monitor loop ────────────────────────────────────────

class TestTangleInMonitorLoop:
    """Verify _check_tangle is called from the main monitor loop."""

    def setup_method(self):
        (self.monitor, self.printer, self.gcode,
         self.reactor, self.manager) = _make_monitor(tangle_detection=True)

    def test_check_tangle_called_during_normal_printing(self):
        """_check_tangle runs on each normal monitoring cycle."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode,
            encoder_pulse=100)

        with patch.object(self.monitor, '_check_tangle') as mock_check:
            self.monitor._monitor_runout(1000.0)
            mock_check.assert_called_once()

    def test_check_tangle_not_called_when_disabled(self):
        """tangle_detection=False → _check_tangle never called."""
        monitor, printer, gcode, reactor, manager = _make_monitor(
            tangle_detection=False)
        _setup_printing_state(
            monitor, printer, manager, gcode, encoder_pulse=100)

        with patch.object(monitor, '_check_tangle') as mock_check:
            monitor._monitor_runout(1000.0)
            mock_check.assert_not_called()

    def test_check_tangle_not_called_during_runout_handling(self):
        """Active runout handling suppresses tangle checks."""
        _setup_printing_state(
            self.monitor, self.printer, self.manager, self.gcode,
            encoder_pulse=100)
        self.monitor.runout_handling_in_progress = True

        with patch.object(self.monitor, '_check_tangle') as mock_check:
            self.monitor._monitor_runout(1000.0)
            mock_check.assert_not_called()


# ── Extruder resolution ─────────────────────────────────────────────────

class TestExtruderResolution:
    """Verify lazy extruder/MCU lookup works correctly."""

    def setup_method(self):
        (self.monitor, self.printer, self.gcode,
         self.reactor, self.manager) = _make_monitor(tangle_detection=True)

    def test_resolve_extruder_success(self):
        """Extruder and MCU resolve on first call."""
        ext = Mock()
        mcu = Mock()
        mcu.estimated_print_time = Mock(return_value=0.0)

        def lookup(name, default=None):
            if name == "extruder":
                return ext
            if name == "mcu":
                return mcu
            raise Exception(f"Not found: {name}")

        self.printer.lookup_object.side_effect = lookup

        assert self.monitor._resolve_extruder() is True
        assert self.monitor._extruder is ext
        assert self.monitor._estimated_print_time is mcu.estimated_print_time

    def test_resolve_extruder_failure(self):
        """Missing extruder → returns False, no crash."""
        self.printer.lookup_object.side_effect = Exception("not found")

        assert self.monitor._resolve_extruder() is False
        assert self.monitor._extruder is None

    def test_resolve_extruder_cached(self):
        """Second call doesn't re-lookup."""
        ext = Mock()
        self.monitor._extruder = ext
        self.monitor._estimated_print_time = Mock()

        result = self.monitor._resolve_extruder()

        assert result is True
        # lookup_object should NOT have been called
        self.printer.lookup_object.assert_not_called()
