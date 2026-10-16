"""Commands that change persisted ACE state: print end, smart unload,
slot inventory, shared-bus bindings and the debug overrides."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import ace.commands
from ace.config import ACE_INSTANCES, INSTANCE_MANAGERS


class CommandError(Exception):
    """Klipper's gcmd.error: an exception class carrying the message."""


_REQUIRED = object()


class FakeGcmd:
    def __init__(self, **params):
        self.params = {k: str(v) for k, v in params.items()}
        self.error = CommandError
        self.respond_info = Mock()

    def get_command_parameters(self):
        return self.params

    def _value(self, name, default, convert):
        if name not in self.params:
            if default is _REQUIRED:
                raise CommandError(f"Error on '{name}': missing")
            return default
        return convert(self.params[name])

    def get(self, name, default=_REQUIRED):
        return self._value(name, default, str)

    def get_int(self, name, default=_REQUIRED, **_limits):
        return self._value(name, default, int)

    def get_float(self, name, default=_REQUIRED, **_limits):
        return self._value(name, default, float)

    def messages(self):
        return [c.args[0] for c in self.respond_info.call_args_list]


class FakeState:
    """PersistentState over a dict; records every write and flush."""

    def __init__(self, **variables):
        self.variables = dict(variables)
        self.writes = []
        self.flushes = 0
        self.has_pending = False

    def get(self, key, default=None):
        return self.variables.get(key, default)

    def get_all(self):
        return dict(self.variables)

    def set(self, key, value):
        self.writes.append(key)
        self.variables[key] = value

    def set_and_save(self, key, value):
        self.set(key, value)

    def flush(self):
        self.flushes += 1


def _ace_unit(instance_num=0):
    return SimpleNamespace(
        instance_num=instance_num,
        SLOT_COUNT=4,
        inventory=[{"status": "empty", "color": [0, 0, 0], "material": "",
                    "temp": 0, "rfid": False} for _ in range(4)],
        _feed_assist_index=0,
        _disable_feed_assist=Mock(),
    )


@pytest.fixture
def ace_setup():
    """One ACE, its manager with a dict-backed state; tool T2 loaded."""
    unit = _ace_unit()
    manager = Mock()
    manager.get_ace_global_enabled.return_value = True
    manager.state = FakeState(ace_current_index=2, ace_target_index=-1,
                              ace_filament_pos="nozzle")
    manager.instances = {0: unit}
    manager.smart_unload = Mock(return_value=False)
    ACE_INSTANCES[0] = unit
    INSTANCE_MANAGERS[0] = manager
    yield manager, unit
    ACE_INSTANCES.clear()
    INSTANCE_MANAGERS.clear()


# --- ACE_HANDLE_PRINT_END ---------------------------------------------

def test_a_failed_print_end_unload_keeps_the_tool_and_its_assist(ace_setup):
    manager, unit = ace_setup

    ace.commands.cmd_ACE_HANDLE_PRINT_END(FakeGcmd())

    manager.smart_unload.assert_called_once_with(2, prepare_toolhead=True)
    assert manager.state.variables["ace_current_index"] == 2
    unit._disable_feed_assist.assert_not_called()
    assert manager.state.flushes == 1
    manager._sync_moonraker_lane_data.assert_called_once_with(
        force=True, reason="print_end")


def test_a_successful_print_end_unload_clears_the_tool_and_its_assist(ace_setup):
    manager, unit = ace_setup
    manager.smart_unload.return_value = True

    ace.commands.cmd_ACE_HANDLE_PRINT_END(FakeGcmd())

    assert manager.state.variables["ace_current_index"] == -1
    unit._disable_feed_assist.assert_called_once_with(0)
    assert manager.state.flushes == 1


def test_print_end_with_ace_disabled_unloads_nothing(ace_setup):
    manager, unit = ace_setup
    manager.get_ace_global_enabled.return_value = False

    ace.commands.cmd_ACE_HANDLE_PRINT_END(FakeGcmd())

    manager.smart_unload.assert_not_called()
    unit._disable_feed_assist.assert_not_called()
    assert manager.state.writes == []


# --- ACE_SMART_UNLOAD -------------------------------------------------

def test_smart_unload_without_tool_frees_the_path_of_the_current_tool(ace_setup):
    manager, _ = ace_setup

    ace.commands.cmd_ACE_SMART_UNLOAD(FakeGcmd())

    manager.smart_unload.assert_called_once_with(2, cycle_on_blocked=True)


def test_smart_unload_of_a_named_tool_does_not_cycle_slots(ace_setup):
    manager, _ = ace_setup

    ace.commands.cmd_ACE_SMART_UNLOAD(FakeGcmd(TOOL=1))

    manager.smart_unload.assert_called_once_with(1, cycle_on_blocked=False)


def test_a_failed_smart_unload_keeps_the_current_tool(ace_setup):
    manager, _ = ace_setup

    ace.commands.cmd_ACE_SMART_UNLOAD(FakeGcmd())

    assert manager.state.variables["ace_current_index"] == 2
    assert "ace_current_index" not in manager.state.writes


# --- ACE_SET_SLOT -----------------------------------------------------

@pytest.mark.parametrize("params", [
    dict(INDEX=1, COLOR="RED", MATERIAL="PLA", TEMP=301),
    dict(INDEX=1, COLOR="RED", MATERIAL="PLA", TEMP=0),
    dict(INDEX=4, COLOR="RED", MATERIAL="PLA", TEMP=200),
    dict(INDEX=-1, COLOR="RED", MATERIAL="PLA", TEMP=200),
    dict(INDEX=1, COLOR="RED", TEMP=200),
], ids=["temp-above-300", "temp-zero", "slot-above-range", "slot-below-range",
        "no-material"])
def test_a_refused_set_slot_changes_and_saves_nothing(ace_setup, params):
    manager, unit = ace_setup
    before = [dict(slot) for slot in unit.inventory]
    gcmd = FakeGcmd(INSTANCE=0, **params)

    with pytest.raises(CommandError, match="ACE_SET_SLOT error"):
        ace.commands.cmd_ACE_SET_SLOT(gcmd)

    assert unit.inventory == before
    manager._sync_inventory_to_persistent.assert_not_called()


def test_a_valid_set_slot_changes_and_saves_the_slot(ace_setup):
    manager, unit = ace_setup

    ace.commands.cmd_ACE_SET_SLOT(
        FakeGcmd(INSTANCE=0, INDEX=1, COLOR="RED", MATERIAL="PLA", TEMP=300))

    assert unit.inventory[1]["material"] == "PLA"
    assert unit.inventory[1]["temp"] == 300
    manager._sync_inventory_to_persistent.assert_called_once_with(0)


# --- ACE_RESET_SHARED_BUS_BINDINGS ------------------------------------

@pytest.fixture
def shared_bus(ace_setup):
    """ACE 0 and 1 share one bus transport; ACE 2 has its own port."""
    manager, _ = ace_setup
    manager.state.variables.update({
        "ace2_bus_bindings_ttyACM0": {"0": "uid-a", "1": "uid-b"},
        "ace2_bus_bindings_ttyACM1": {"0": "uid-c"},
    })
    shared = Mock()
    for inst_num in (0, 1):
        ACE_INSTANCES[inst_num] = SimpleNamespace(bus_session=Mock(), serial_mgr=shared)
    ACE_INSTANCES[2] = SimpleNamespace(serial_mgr=Mock())
    return manager, shared


def test_resetting_bus_bindings_clears_only_the_bindings(shared_bus):
    manager, _ = shared_bus

    ace.commands.cmd_ACE_RESET_SHARED_BUS_BINDINGS(FakeGcmd(RECONNECT=0))

    assert manager.state.variables["ace2_bus_bindings_ttyACM0"] == {}
    assert manager.state.variables["ace2_bus_bindings_ttyACM1"] == {}
    assert manager.state.variables["ace_current_index"] == 2
    assert manager.state.variables["ace_filament_pos"] == "nozzle"
    assert sorted(manager.state.writes) == ["ace2_bus_bindings_ttyACM0",
                                            "ace2_bus_bindings_ttyACM1"]
    assert manager.state.flushes == 1


def test_resetting_bus_bindings_reconnects_each_shared_transport_once(shared_bus):
    _, shared = shared_bus

    ace.commands.cmd_ACE_RESET_SHARED_BUS_BINDINGS(FakeGcmd(DELAY=3.5))

    shared.reconnect.assert_called_once_with(delay=3.5)
    ACE_INSTANCES[2].serial_mgr.reconnect.assert_not_called()


def test_resetting_bus_bindings_without_reconnect_leaves_transports_alone(shared_bus):
    _, shared = shared_bus

    ace.commands.cmd_ACE_RESET_SHARED_BUS_BINDINGS(FakeGcmd(RECONNECT=0))

    shared.reconnect.assert_not_called()


# --- ACE_DEBUG_SET_FILAMENT_STATE -------------------------------------

def test_filament_state_is_normalized_and_saved(ace_setup):
    manager, _ = ace_setup
    manager.state.variables["ace_filament_pos"] = "bowden"

    ace.commands.cmd_ACE_DEBUG_SET_FILAMENT_STATE(FakeGcmd(STATE=" Nozzle "))

    assert manager.state.variables["ace_filament_pos"] == "nozzle"


def test_an_unknown_filament_state_is_an_error_and_not_saved(ace_setup):
    manager, _ = ace_setup

    with pytest.raises(CommandError, match="Invalid STATE"):
        ace.commands.cmd_ACE_DEBUG_SET_FILAMENT_STATE(FakeGcmd(STATE="hotend"))

    assert manager.state.writes == []


def test_filament_state_without_state_only_reports(ace_setup):
    manager, _ = ace_setup
    gcmd = FakeGcmd()

    ace.commands.cmd_ACE_DEBUG_SET_FILAMENT_STATE(gcmd)

    assert manager.state.writes == []
    assert any("'nozzle'" in m for m in gcmd.messages())


# --- ACE_FLUSH --------------------------------------------------------

def test_flush_writes_pending_state(ace_setup):
    manager, _ = ace_setup
    manager.state.has_pending = True

    ace.commands.cmd_ACE_FLUSH(FakeGcmd())

    assert manager.state.flushes == 1


def test_flush_without_pending_state_writes_nothing(ace_setup):
    manager, _ = ace_setup

    ace.commands.cmd_ACE_FLUSH(FakeGcmd())

    assert manager.state.flushes == 0


# --- ACE_DEBUG_SET_CURRENT_INDEX --------------------------------------

def test_setting_no_current_tool_clears_current_and_target(ace_setup):
    manager, _ = ace_setup
    manager.state.variables["ace_target_index"] = 3

    ace.commands.cmd_ACE_DEBUG_SET_CURRENT_INDEX(FakeGcmd(TOOL=-1))

    assert manager.state.variables["ace_current_index"] == -1
    assert manager.state.variables["ace_target_index"] == -1
