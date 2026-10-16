"""AceManager guards around persisted tool state: stale-index reconciliation
and the perform_tool_change checks that must stop a toolchange before it
moves filament."""

from unittest.mock import Mock

import pytest

from ace.config import (
    FILAMENT_STATE_BOWDEN,
    FILAMENT_STATE_NOZZLE,
    FILAMENT_STATE_SPLITTER,
    SENSOR_RDM,
    SENSOR_TOOLHEAD,
)
from tests import test_manager


class Rig:
    """One ACE with four ready slots behind a mock printer."""

    def __init__(self):
        self.fixture = test_manager._ManagerCycleFixture()
        self.fixture.setUp()
        self.variables = self.fixture.variables
        self.gcode = self.fixture.mock_gcode
        self.objects = {}
        base_lookup = self.fixture.mock_printer.lookup_object.side_effect

        def lookup(name, default=None):
            if name in self.objects:
                return self.objects[name]
            return base_lookup(name, default)

        self.fixture.mock_printer.lookup_object.side_effect = lookup
        self.instance = self.fixture._make_instance()
        self.instance._feed_filament_into_toolhead = Mock(return_value=0.0)
        self.manager = self.fixture._build_manager(lambda *a, **k: self.instance)
        self.manager.instances[0].inventory = [
            {"status": "ready"} for _ in range(4)]

    def set_print_state(self, state):
        print_stats = Mock()
        print_stats.get_status.return_value = {"state": state}
        self.objects["print_stats"] = print_stats

    def set_extruder(self, temp, min_extrude_temp=170.0):
        heater = Mock(min_extrude_temp=min_extrude_temp)
        heater.get_temp.return_value = (temp, 0.0)
        self.objects["extruder"] = Mock(get_heater=Mock(return_value=heater))

    def scripts(self):
        return [c.args[0] for c in self.gcode.run_script_from_command.call_args_list]


@pytest.fixture
def rig():
    return Rig()


# --- reconcile_stale_current_index ----------------------------------------

@pytest.fixture
def stale_rig(rig):
    rig.variables["ace_current_index"] = 2
    rig.variables["ace_filament_pos"] = FILAMENT_STATE_NOZZLE
    rig.set_print_state("standby")
    return rig


def assert_state_untouched(rig):
    assert rig.variables["ace_current_index"] == 2
    assert rig.variables["ace_filament_pos"] == FILAMENT_STATE_NOZZLE


def test_empty_slot_of_the_current_tool_clears_the_stale_index(stale_rig):
    stale_rig.manager.reconcile_stale_current_index(2)

    assert stale_rig.variables["ace_current_index"] == -1
    assert stale_rig.variables["ace_filament_pos"] == FILAMENT_STATE_BOWDEN


def test_empty_slot_of_another_tool_keeps_the_index(stale_rig):
    stale_rig.manager.reconcile_stale_current_index(3)

    assert_state_untouched(stale_rig)


def test_empty_slot_during_a_toolchange_keeps_the_index(stale_rig):
    stale_rig.manager.toolchange_in_progress = True

    stale_rig.manager.reconcile_stale_current_index(2)

    assert_state_untouched(stale_rig)


def test_empty_slot_with_a_toolchange_pending_keeps_the_index(stale_rig):
    stale_rig.variables["ace_target_index"] = 1

    stale_rig.manager.reconcile_stale_current_index(2)

    assert_state_untouched(stale_rig)


@pytest.mark.parametrize("print_state", ["printing", "paused"])
def test_empty_slot_during_a_print_keeps_the_index(stale_rig, print_state):
    stale_rig.set_print_state(print_state)

    stale_rig.manager.reconcile_stale_current_index(2)

    assert_state_untouched(stale_rig)


# --- perform_tool_change guards --------------------------------------------

def test_cold_extruder_without_target_temp_aborts_before_feeding(rig):
    rig.variables["ace_filament_pos"] = FILAMENT_STATE_BOWDEN
    rig.manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
    rig.manager.check_and_wait_for_spool_ready = Mock(return_value=True)
    rig.set_extruder(temp=25.0)

    with pytest.raises(Exception, match="too cold"):
        rig.manager.perform_tool_change(current_tool=-1, target_tool=0)

    rig.instance._feed_filament_into_toolhead.assert_not_called()
    assert not any(s.startswith("M109") for s in rig.scripts())


def test_splitter_state_with_toolhead_triggered_unloads_as_nozzle(rig):
    rig.variables["ace_current_index"] = 1
    rig.variables["ace_filament_pos"] = FILAMENT_STATE_SPLITTER
    rig.manager._sensor_override = {SENSOR_TOOLHEAD: True, SENSOR_RDM: False}
    pos_at_unload = []

    def smart_unload(**kwargs):
        pos_at_unload.append(rig.variables["ace_filament_pos"])
        return False

    rig.manager.smart_unload = Mock(side_effect=smart_unload)

    with pytest.raises(Exception, match="Failed to unload tool 1"):
        rig.manager.perform_tool_change(current_tool=1, target_tool=2)

    assert pos_at_unload == [FILAMENT_STATE_NOZZLE]
    rig.instance._feed_filament_into_toolhead.assert_not_called()


def test_reselection_with_jammed_path_raises_and_loads_nothing(rig):
    rig.variables["ace_current_index"] = 1
    rig.variables["ace_filament_pos"] = FILAMENT_STATE_SPLITTER
    rig.manager._sensor_override = {SENSOR_TOOLHEAD: False, SENSOR_RDM: False}
    rig.manager.is_filament_path_free = Mock(return_value=False)
    rig.manager.smart_unload = Mock(return_value=False)

    with pytest.raises(Exception, match="jammed"):
        rig.manager.perform_tool_change(current_tool=1, target_tool=1)

    rig.manager.smart_unload.assert_called_once()
    rig.instance._feed_filament_into_toolhead.assert_not_called()


def test_unknown_state_with_toolhead_triggered_raises_when_unload_fails(rig):
    rig.variables["ace_current_index"] = 1
    rig.variables["ace_filament_pos"] = "garbled"
    rig.manager._sensor_override = {SENSOR_TOOLHEAD: True, SENSOR_RDM: False}
    rig.manager.smart_unload = Mock(return_value=False)

    with pytest.raises(Exception, match="Failed to unload tool 1"):
        rig.manager.perform_tool_change(current_tool=1, target_tool=2)

    rig.manager.smart_unload.assert_called_once()
    rig.instance._feed_filament_into_toolhead.assert_not_called()
