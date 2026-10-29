"""AceManager unload and teardown paths: feed assist and retract cleanup
around unloads, and state persistence when Klipper disconnects."""

import configparser
import logging
from unittest.mock import Mock

import pytest

from ace.config import SENSOR_TOOLHEAD
from tests import test_manager


@pytest.fixture
def fixture():
    fx = test_manager._ManagerCycleFixture()
    fx.setUp()
    return fx


def build(fixture):
    instance = fixture._make_instance()
    manager = fixture._build_manager(lambda *a, **k: instance)
    manager.instances[0].inventory = [
        {"status": "ready"},
        {"status": "ready"},
        {"status": "empty"},
        {"status": "empty"},
    ]
    return manager, instance


# --- coordinated retract ----------------------------------------------------

def coordinated_unload(fixture, feed_assist_slot):
    """smart_unload of T0 with the toolhead sensor triggered; returns the
    order of feed-assist and motion calls."""
    manager, instance = build(fixture)
    events = []
    instance._feed_assist_index = feed_assist_slot
    instance.parkposition_to_toolhead_length = 500.0
    instance.protocol.feed_assist_causes_busy = Mock(return_value=True)
    instance._disable_feed_assist = Mock(
        side_effect=lambda slot: events.append(("disable_feed_assist", slot)))
    instance._smart_unload_slot = Mock(
        side_effect=lambda slot, **kw: events.append(("ace_retract", slot)) or True)
    manager._extruder_move = Mock(
        side_effect=lambda *a, **kw: events.append(("extruder_retract", None)))
    manager._wait_toolhead_move_finished = Mock()
    manager.get_instant_switch_state = Mock(side_effect=lambda name: name == SENSOR_TOOLHEAD)
    manager.is_filament_path_free_instant = Mock(return_value=True)
    manager._restore_heater_if_idle = Mock()

    assert manager.smart_unload(tool_index=0, prepare_toolhead=False)
    return events


def test_coordinated_retract_stops_feed_assist_on_the_unloaded_slot_first(fixture):
    events = coordinated_unload(fixture, feed_assist_slot=0)

    assert events[0] == ("disable_feed_assist", 0)
    assert ("extruder_retract", None) in events
    assert ("ace_retract", 0) in events


def test_coordinated_retract_leaves_feed_assist_on_another_slot(fixture):
    events = coordinated_unload(fixture, feed_assist_slot=1)

    assert not any(name == "disable_feed_assist" for name, _ in events)
    assert ("ace_retract", 0) in events


# --- slot cycling: a failed retract is stopped -------------------------------

def cycle(manager, use_extruder):
    return manager._cycle_slots_with_sensor_check(
        current_tool_index=-1,
        attempted_tool_index=-1,
        retract_length=10,
        retract_speed=5,
        retract_speed_mmmin=600,
        full_unload_length=500,
        sensor_name=SENSOR_TOOLHEAD,
        use_extruder=use_extruder,
        sensor_to_parking_length=None,
    )


@pytest.mark.parametrize("use_extruder", [True, False],
                         ids=["extruder_continuation", "ace_only"])
def test_retract_error_while_cycling_stops_that_slots_retract(fixture, use_extruder):
    manager, instance = build(fixture)
    manager.execute_coordinated_retraction = Mock()
    manager.get_instant_switch_state = Mock(return_value=True)
    manager.is_filament_path_free = Mock(return_value=False)
    instance._retract.side_effect = RuntimeError("ACE refused retract")

    result = cycle(manager, use_extruder)

    assert result is False
    assert [c.args[0] for c in instance._stop_retract.call_args_list] == [0, 1]


# --- disconnect ---------------------------------------------------------------

def test_disconnect_after_shutdown_writes_dirty_state_to_the_file(fixture, tmp_path):
    saved = tmp_path / "saved_variables.cfg"
    fixture.mock_save_vars.filename = str(saved)
    fixture.mock_printer.is_shutdown = Mock(return_value=True)
    manager, _ = build(fixture)
    manager.state.set("ace_current_index", 3)
    fixture.mock_gcode.run_script_from_command.reset_mock()

    manager._handle_disconnect()

    cfg = configparser.ConfigParser()
    cfg.read(saved)
    assert cfg.get("Variables", "ace_current_index") == "3"
    fixture.mock_gcode.run_script_from_command.assert_not_called()


def test_disconnect_while_running_saves_dirty_state_through_gcode(fixture, tmp_path):
    saved = tmp_path / "saved_variables.cfg"
    fixture.mock_save_vars.filename = str(saved)
    fixture.mock_printer.is_shutdown = Mock(return_value=False)
    manager, _ = build(fixture)
    manager.state.set("ace_current_index", 3)
    fixture.mock_gcode.run_script_from_command.reset_mock()

    manager._handle_disconnect()

    scripts = [c.args[0] for c in fixture.mock_gcode.run_script_from_command.call_args_list]
    assert "SAVE_VARIABLE VARIABLE=ace_current_index VALUE=3" in scripts
    assert not saved.exists()


def test_failing_lane_sync_shutdown_does_not_abort_disconnect(fixture, caplog):
    fixture.mock_printer.is_shutdown = Mock(return_value=False)
    manager, instance = build(fixture)
    manager._moonraker_lane_sync = Mock()
    manager._moonraker_lane_sync.shutdown.side_effect = RuntimeError("moonraker gone")

    with caplog.at_level(logging.WARNING):
        manager._handle_disconnect()

    instance.serial_mgr.disconnect.assert_called_once_with()
    assert "moonraker gone" in caplog.text


def test_failing_transport_disconnect_does_not_abort_disconnect(fixture, caplog):
    fixture.mock_printer.is_shutdown = Mock(return_value=False)
    manager, instance = build(fixture)
    instance.serial_mgr.disconnect.side_effect = RuntimeError("port gone")
    manager._moonraker_lane_sync = Mock()

    with caplog.at_level(logging.ERROR):
        manager._handle_disconnect()

    manager._moonraker_lane_sync.shutdown.assert_called_once_with()
    assert "port gone" in caplog.text
