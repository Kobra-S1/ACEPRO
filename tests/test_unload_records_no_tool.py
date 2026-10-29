"""After a confirmed unload no tool is recorded anywhere.

Three records say which tool is loaded: ace_current_index, ace_target_index
(a change in flight) and the _ACE_STATE macro's `active`, which the printer
macros read. ACE_CHANGE_TOOL TOOL=-1 cleared only the first; the unload a
tool change does cleared all three.
"""
from unittest.mock import Mock, patch

import ace.commands
from ace.manager import AceManager
from tests import test_manager


class FakeGcmd:
    def __init__(self, **params):
        self.params = params
        self.error = RuntimeError
        self.respond_info = Mock()

    def get_int(self, name, default=None, **_limits):
        return self.params.get(name, default)


def _manager(current_tool, target_tool):
    setup = test_manager.TestAceManagerInitialization()
    setup.setUp()
    setup.variables.update({"ace_current_index": current_tool,
                            "ace_target_index": target_tool})
    with patch("ace.manager.AceInstance"):
        manager = AceManager(setup.mock_config)
    return manager, setup.mock_gcode


def _active_writes(gcode):
    return [c.args[0] for c in gcode.run_script_from_command.call_args_list
            if "MACRO=_ACE_STATE VARIABLE=active" in c.args[0]]


def test_an_unload_command_clears_every_record():
    manager, gcode = _manager(current_tool=2, target_tool=2)
    manager.smart_unload = Mock(return_value=True)

    ace.commands.cmd_ACE_CHANGE_TOOL(manager, FakeGcmd(TOOL=-1), -1)

    assert manager.state.get("ace_current_index") == -1
    assert manager.state.get("ace_target_index") == -1
    assert _active_writes(gcode) == [
        "SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE=-1"]


def test_a_park_command_clears_every_record():
    manager, gcode = _manager(current_tool=2, target_tool=2)
    manager.park_tool = Mock(return_value=True)

    ace.commands.cmd_ACE_CHANGE_TOOL(manager, FakeGcmd(TOOL=-1, PARK=1), -1)

    assert manager.state.get("ace_current_index") == -1
    assert manager.state.get("ace_target_index") == -1
    assert len(_active_writes(gcode)) == 1


def test_a_failed_unload_command_clears_nothing():
    manager, gcode = _manager(current_tool=2, target_tool=2)
    manager.smart_unload = Mock(return_value=False)

    try:
        ace.commands.cmd_ACE_CHANGE_TOOL(manager, FakeGcmd(TOOL=-1), -1)
    except RuntimeError:
        pass

    assert manager.state.get("ace_current_index") == 2
    assert manager.state.get("ace_target_index") == 2
    assert _active_writes(gcode) == []
