"""Parking a filament at the toolhead's intake instead of unloading it.

A parked filament is cut and pulled back only until the toolhead sensor
clears: it stays in its tube, tip at the gear, and its next load is a short
extruder pull. Whether that is in another tool's way is the tube layout:

* ``toolhead_paths: per_tool`` - every tool has its own tube to its own
  intake; any number of tools may stay parked.
* ``toolhead_paths: shared`` (default) - the tools meet in one tube (a hub
  in front of the toolhead); a parked filament is pulled back out of it
  before another tool loads.
"""

from unittest.mock import Mock, patch

import pytest

from ace.config import FILAMENT_STATE_BOWDEN, SENSOR_TOOLHEAD
from ace.intake_gated import PATHS_PER_TOOL, PATHS_SHARED
from tests.test_unload_rdm_guard import TestSmartUnloadRdmEarlyStopGuard as UnloadFixture


def manager_with(paths, toolhead_has_filament=True, transfer=True):
    fixture = UnloadFixture()
    instance = fixture._instance()
    instance.extruder_feeding_length = 80.0
    manager = fixture._manager(instance, rdm_has_filament=False)
    manager.has_rdm_sensor = Mock(return_value=False)
    manager.get_switch_state = Mock(return_value=toolhead_has_filament)
    manager.get_instant_switch_state = Mock(return_value=toolhead_has_filament)
    manager.toolhead_paths = paths
    manager.pre_cut_retract_length = 2.0
    variables = {"ace_current_index": 1}
    manager.state.get = Mock(
        side_effect=lambda key, default=None: variables.get(key, default))
    manager.state.set = Mock(
        side_effect=lambda key, value: variables.__setitem__(key, value))
    manager.variables = variables
    manager.transfers = {0: Mock()} if transfer else {}
    return manager, instance


def tool_lookups(instance):
    return (patch("ace.manager.get_instance_from_tool", return_value=0),
            patch("ace.manager.get_local_slot",
                  side_effect=lambda tool, instance_num: tool),
            patch("ace.manager.get_ace_instance_and_slot_for_tool",
                  side_effect=lambda tool: (instance, tool)))


def run(instance, call):
    first, second, third = tool_lookups(instance)
    with first, second, third:
        return call()


class TestParkTool:
    def test_a_park_cuts_then_retracts_only_to_the_sensor(self):
        manager, instance = manager_with(PATHS_PER_TOOL)
        instance._feed_assist_index = 1

        assert run(instance, lambda: manager.park_tool(1)) is True

        manager.gcode.run_script_from_command.assert_any_call(
            "_ACE_PREPARE_FOR_RETRACTION TARGET_TEMP=0 PRE_CUT_RETRACT=2.0")
        manager.transfers[0].park.assert_called_once_with(
            1, extruder_limit=40.0, extruder_speed=10.0)
        instance._smart_unload_slot.assert_not_called()
        instance._retract.assert_not_called()
        assert manager.parked_tools() == [1]
        assert manager.variables["ace_filament_pos"] == FILAMENT_STATE_BOWDEN

    def test_feed_assist_stops_while_the_cut_runs(self):
        """The ACE needs a second or more to stop assisting; the cut takes
        longer than that anyway."""
        manager, instance = manager_with(PATHS_PER_TOOL)
        instance._feed_assist_index = 1
        order = []
        instance._send_feed_assist_stop.side_effect = (
            lambda slot: order.append("stop sent"))
        manager.gcode.run_script_from_command.side_effect = (
            lambda script: order.append(script.split()[0]))
        instance._await_feed_assist_stopped.side_effect = (
            lambda: order.append("stopped"))
        manager.transfers[0].park.side_effect = (
            lambda *args, **kwargs: order.append("retract"))

        run(instance, lambda: manager.park_tool(1))

        assert order[:4] == ["stop sent", "_ACE_PREPARE_FOR_RETRACTION",
                             "stopped", "retract"]
        instance._send_feed_assist_stop.assert_called_once_with(1)

    def test_a_failed_cut_leaves_feed_assist_on(self):
        """A print pauses on a failed cut and goes on with this filament
        once the user has resumed it."""
        manager, instance = manager_with(PATHS_PER_TOOL)
        instance._feed_assist_index = 1
        manager.gcode.run_script_from_command.side_effect = Exception("no cut")

        with pytest.raises(Exception, match="no cut"):
            run(instance, lambda: manager.park_tool(1))

        instance._enable_feed_assist.assert_called_once_with(1)
        manager.transfers[0].park.assert_not_called()
        assert manager.parked_tools() == []

    def test_feed_assist_on_another_slot_is_left_alone(self):
        manager, instance = manager_with(PATHS_PER_TOOL)
        instance._feed_assist_index = 3
        manager.gcode.run_script_from_command.side_effect = Exception("no cut")

        with pytest.raises(Exception, match="no cut"):
            run(instance, lambda: manager.park_tool(1))

        instance._send_feed_assist_stop.assert_not_called()
        instance._enable_feed_assist.assert_not_called()

    def test_a_park_leaves_the_heater_target_as_it_found_it(self):
        manager, instance = manager_with(PATHS_PER_TOOL)
        manager._extruder_target = Mock(return_value=250.0)

        run(instance, lambda: manager.park_tool(1))

        assert manager._heater_target_before == 250.0
        manager._restore_heater_if_idle.assert_called_once_with()

    def test_a_failed_park_is_not_recorded(self):
        manager, instance = manager_with(PATHS_PER_TOOL)
        manager.transfers[0].park.side_effect = ValueError("still sees filament")

        with pytest.raises(ValueError):
            run(instance, lambda: manager.park_tool(1))

        assert manager.parked_tools() == []

    def test_without_an_intake_to_park_at_the_tool_is_unloaded(self):
        manager, instance = manager_with(PATHS_PER_TOOL, transfer=False)
        manager.smart_unload = Mock(return_value=True)

        assert run(instance, lambda: manager.park_tool(1)) is True

        manager.smart_unload.assert_called_once_with(1)
        assert manager.parked_tools() == []

    def test_a_filament_already_clear_of_the_sensor_is_recorded_as_it_is(self):
        manager, instance = manager_with(
            PATHS_PER_TOOL, toolhead_has_filament=False)

        run(instance, lambda: manager.park_tool(1))

        manager.transfers[0].park.assert_not_called()
        assert manager.parked_tools() == [1]


class TestParkedFilamentBeforeAnotherLoad:
    def _parked(self, paths, parked):
        manager, instance = manager_with(paths, toolhead_has_filament=False)
        manager.variables["ace_parked_tools"] = list(parked)
        manager.smart_unload = Mock(return_value=True)
        return manager, instance

    def test_in_a_shared_tube_it_is_pulled_out_first(self):
        manager, instance = self._parked(PATHS_SHARED, [1])

        run(instance, lambda: manager._clear_parked_for(3))

        manager.smart_unload.assert_called_once_with(1, keep_heater=True)

    def test_with_a_tube_per_tool_it_stays(self):
        manager, instance = self._parked(PATHS_PER_TOOL, [1])

        run(instance, lambda: manager._clear_parked_for(3))

        manager.smart_unload.assert_not_called()
        assert manager.parked_tools() == [1]

    def test_the_tool_being_loaded_stays_parked_for_its_resume(self):
        manager, instance = self._parked(PATHS_SHARED, [3])

        run(instance, lambda: manager._clear_parked_for(3))

        manager.smart_unload.assert_not_called()
        assert manager.parked_tools() == [3]

    def test_a_parked_tool_whose_slot_is_empty_is_forgotten(self):
        manager, instance = self._parked(PATHS_SHARED, [1])
        instance.inventory[1] = {"status": "empty"}

        run(instance, lambda: manager._clear_parked_for(3))

        manager.smart_unload.assert_not_called()
        assert manager.parked_tools() == []

    def test_a_pull_out_that_fails_stops_the_load(self):
        manager, instance = self._parked(PATHS_SHARED, [1])
        manager.smart_unload.return_value = False

        with pytest.raises(Exception, match="T1"):
            run(instance, lambda: manager._clear_parked_for(3))


class TestUnknownFilamentAtTheToolhead:
    """With the gear between ACE and toolhead sensor, the extruder clears
    the sensor whichever slot is tried: a test retract identifies nothing
    and drags the tried slot's own filament back."""

    def unload_unknown(self, manager, instance):
        manager.state.get = Mock(side_effect=lambda key, default=None: {
            "ace_current_index": -1}.get(key, default))
        manager.prepare_toolhead_for_filament_retraction = Mock()
        manager._cycling_unload_fallback = Mock(return_value=True)
        return run(instance, lambda: manager.smart_unload(-1))

    def test_it_is_not_guessed_by_test_retracts(self):
        manager, instance = manager_with(PATHS_PER_TOOL)

        with pytest.raises(Exception, match="cannot tell"):
            self.unload_unknown(manager, instance)

        manager._cycling_unload_fallback.assert_not_called()
        instance._retract.assert_not_called()

    def test_a_toolhead_the_ace_pushes_into_is_still_cycled(self):
        manager, instance = manager_with(PATHS_PER_TOOL, transfer=False)

        assert self.unload_unknown(manager, instance) is True

        manager._cycling_unload_fallback.assert_called_once()


class TestParkedRecord:
    def test_a_full_unload_ends_the_park(self):
        manager, instance = manager_with(
            PATHS_PER_TOOL, toolhead_has_filament=False)
        manager.variables["ace_parked_tools"] = [1, 2]
        manager.is_filament_path_free_instant = Mock(return_value=True)
        manager.route_to_tool = Mock()

        run(instance, lambda: manager.smart_unload(tool_index=1,
                                                   prepare_toolhead=False))

        assert manager.parked_tools() == [2]

    def test_is_parked_answers_per_tool(self):
        manager, _instance = manager_with(PATHS_PER_TOOL)
        manager.variables["ace_parked_tools"] = [2]

        assert manager.is_parked(2) is True
        assert manager.is_parked(1) is False


class TestRunoutMonitorStaysQuiet:
    """The runout monitor reads the toolhead sensor during a print and
    takes present -> absent for a runout unless a tool change is flagged as
    running. An unload and a park clear that sensor on purpose."""

    def test_a_park_is_flagged_as_a_tool_change_while_it_runs(self):
        manager, instance = manager_with(PATHS_PER_TOOL)
        seen = []
        manager.transfers[0].park.side_effect = (
            lambda *args, **kwargs: seen.append(manager.toolchange_in_progress))

        run(instance, lambda: manager.park_tool(1))

        assert seen == [True]
        assert manager.toolchange_in_progress is False

    def test_an_unload_is_flagged_as_a_tool_change_while_it_runs(self):
        manager, instance = manager_with(PATHS_PER_TOOL)
        seen = []
        manager._smart_unload = Mock(
            side_effect=lambda *args: seen.append(manager.toolchange_in_progress) or True)

        run(instance, lambda: manager.smart_unload(1))

        assert seen == [True]
        assert manager.toolchange_in_progress is False

    def test_the_flag_is_dropped_when_the_park_fails(self):
        manager, instance = manager_with(PATHS_PER_TOOL)
        manager.transfers[0].park.side_effect = ValueError("still sees filament")

        with pytest.raises(ValueError):
            run(instance, lambda: manager.park_tool(1))

        assert manager.toolchange_in_progress is False
