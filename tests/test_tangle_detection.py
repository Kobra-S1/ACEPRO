"""Tests for ACE pump_time tangle detection in RunoutMonitor."""
from unittest.mock import Mock, patch
from ace.runout_monitor import RunoutMonitor


def _make_instance(protocol_name="ace1_json", feed_assist_index=0,
                   cont_assist_time=None):
    """Mock ACE instance.  cont_assist_time=None → field absent."""
    inst = Mock()
    inst.protocol_name = protocol_name
    inst._feed_assist_index = feed_assist_index
    info = {}
    if cont_assist_time is not None:
        info["cont_assist_time"] = cont_assist_time
    inst._info = info
    return inst


def _make_monitor(tangle_detection=True, tangle_pump_time=None,
                  instances=None, feed_assist_active=True):
    printer = Mock()
    gcode = Mock()
    reactor = Mock()
    reactor.NOW = 0.0
    reactor.NEVER = float("inf")
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
    )
    return monitor, manager, gcode


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
        assert monitor._pt_phase_start_eventtime is None
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


class TestGen1Gate:
    def test_noop_when_no_instances(self):
        monitor, _m, gcode = _make_monitor(instances=[])
        monitor._check_tangle(100.0, current_tool=0)
        assert not gcode.run_script_from_command.called

    def test_noop_when_gen2_only(self):
        inst = _make_instance(protocol_name="ace2_proto", cont_assist_time=99.0)
        monitor, _m, gcode = _make_monitor(instances=[inst])
        monitor._check_tangle(100.0, current_tool=0)
        assert not gcode.run_script_from_command.called

    def test_noop_when_mixed_fleet_and_gen2_is_active(self):
        # Gen1 idle, Gen2 actively pumping → must NOT fire
        # (Gen2 has device-native tangle reporting)
        gen1_idle = _make_instance(
            protocol_name="ace1_json", feed_assist_index=-1,
            cont_assist_time=0.0,
        )
        gen2_active = _make_instance(
            protocol_name="ace2_proto", feed_assist_index=0,
            cont_assist_time=99.0,
        )
        monitor, _m, gcode = _make_monitor(instances=[gen1_idle, gen2_active])
        monitor._check_tangle(100.0, current_tool=4)
        assert not gcode.run_script_from_command.called
        assert monitor._pt_phase_start_eventtime is None

    def test_fires_when_mixed_fleet_and_gen1_is_active(self):
        # Gen1 actively pumping, Gen2 idle → must monitor Gen1's pump time
        gen1_active = _make_instance(
            protocol_name="ace1_json", feed_assist_index=0,
            cont_assist_time=1.0,
        )
        gen2_idle = _make_instance(
            protocol_name="ace2_proto", feed_assist_index=-1,
            cont_assist_time=99.0,
        )
        monitor, _m, _g = _make_monitor(instances=[gen2_idle, gen1_active])
        assert monitor._get_active_gen1_instance() is gen1_active


class TestFeedAssistGate:
    def test_noop_when_no_instance_pumps(self):
        inst = _make_instance(
            feed_assist_index=-1, cont_assist_time=99.0)
        monitor, _m, gcode = _make_monitor(instances=[inst])
        monitor._check_tangle(100.0, current_tool=0)
        assert not gcode.run_script_from_command.called

    def test_phase_resets_when_pump_stops(self):
        inst = _make_instance(cont_assist_time=2.0)
        monitor, _m, _g = _make_monitor(instances=[inst])
        monitor._check_tangle(100.0, current_tool=0)
        assert monitor._pt_phase_start_eventtime is not None
        inst._feed_assist_index = -1
        monitor._check_tangle(101.0, current_tool=0)
        assert monitor._pt_phase_start_eventtime is None


class TestPumpTimeDetection:
    def test_no_trigger_below_threshold(self):
        inst = _make_instance(cont_assist_time=2.5)
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        monitor._check_tangle(100.0, current_tool=0)
        assert not gcode.run_script_from_command.called

    def test_trigger_at_threshold_after_growth_phase(self):
        inst = _make_instance(cont_assist_time=1.0)
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        monitor._check_tangle(100.0, current_tool=0)
        inst._info["cont_assist_time"] = 4.0
        monitor._check_tangle(101.0, current_tool=0)
        pause_calls = [
            c for c in gcode.run_script_from_command.call_args_list
            if "PAUSE" in str(c)
        ]
        assert pause_calls, "expected PAUSE on threshold cross"

    def test_first_growth_tick_only_marks_phase(self):
        inst = _make_instance(cont_assist_time=5.0)
        monitor, _m, gcode = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        monitor._check_tangle(100.0, current_tool=0)
        assert monitor._pt_phase_start_eventtime == 100.0
        assert not gcode.run_script_from_command.called

    def test_value_drop_resets_phase(self):
        inst = _make_instance(cont_assist_time=2.0)
        monitor, _m, _g = _make_monitor(instances=[inst])
        monitor._check_tangle(100.0, current_tool=0)
        assert monitor._pt_phase_start_eventtime is not None
        inst._info["cont_assist_time"] = 0.0
        monitor._check_tangle(101.0, current_tool=0)
        assert monitor._pt_phase_start_eventtime is None
        assert monitor._pt_last_value_s == 0.0

    def test_field_absent_logs_once_then_silent(self):
        inst = _make_instance(cont_assist_time=None)
        monitor, _m, gcode = _make_monitor(instances=[inst])
        assert monitor._pt_unsupported_logged is False
        with patch("ace.runout_monitor.logging") as log:
            monitor._check_tangle(100.0, current_tool=0)
            assert monitor._pt_unsupported_logged is True
            log.info.assert_called_once()
            log.info.reset_mock()
            monitor._check_tangle(101.0, current_tool=0)
            log.info.assert_not_called()
        assert not gcode.run_script_from_command.called

    def test_after_trigger_state_wiped(self):
        inst = _make_instance(cont_assist_time=1.0)
        monitor, _m, _g = _make_monitor(
            instances=[inst], tangle_pump_time=4.0)
        monitor._check_tangle(100.0, current_tool=0)
        inst._info["cont_assist_time"] = 5.0
        monitor._check_tangle(101.0, current_tool=0)
        assert monitor._pt_phase_start_eventtime is None
        assert monitor._pt_last_value_s == 0.0


class TestHelpers:
    def test_get_active_gen1_returns_pumping_instance(self):
        inst = _make_instance(cont_assist_time=2.5)
        monitor, *_ = _make_monitor(instances=[inst])
        assert monitor._get_active_gen1_instance() is inst

    def test_get_active_gen1_skips_inactive_instances(self):
        inactive = _make_instance(feed_assist_index=-1, cont_assist_time=99.0)
        active = _make_instance(feed_assist_index=0, cont_assist_time=2.0)
        monitor, *_ = _make_monitor(instances=[inactive, active])
        assert monitor._get_active_gen1_instance() is active

    def test_get_active_gen1_skips_gen2_instances(self):
        gen2 = _make_instance(
            protocol_name="ace2_proto", feed_assist_index=0,
            cont_assist_time=99.0,
        )
        monitor, *_ = _make_monitor(instances=[gen2])
        assert monitor._get_active_gen1_instance() is None

    def test_get_active_gen1_returns_none_when_no_instances(self):
        monitor, *_ = _make_monitor(instances=[])
        assert monitor._get_active_gen1_instance() is None


class TestLiveToggle:
    def test_enable_sets_flag_and_clears_state(self):
        monitor, *_ = _make_monitor(tangle_detection=False)
        monitor._pt_last_value_s = 2.5
        monitor._pt_phase_start_eventtime = 100.0
        monitor.set_tangle_detection_enabled(True)
        assert monitor.tangle_detection_enabled is True
        assert monitor._pt_phase_start_eventtime is None
        assert monitor._pt_last_value_s == 0.0

    def test_disable_sets_flag_and_clears_state(self):
        monitor, *_ = _make_monitor(tangle_detection=True)
        monitor._pt_last_value_s = 1.5
        monitor._pt_phase_start_eventtime = 100.0
        monitor.set_tangle_detection_enabled(False)
        assert monitor.tangle_detection_enabled is False
        assert monitor._pt_phase_start_eventtime is None
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
