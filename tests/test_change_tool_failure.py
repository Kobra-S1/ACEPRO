"""A failed ACE_CHANGE_TOOL reaches its caller as a G-code error.

Behind a printer-side tool changer that owns T<n> (register_tool_macros:
False - Kobra X: kx_toolchanger) the caller records the tool and owns the
recovery (holding the print, its retry dialog). A failure that ends in a
console line, or in a pause and prompt of our own, let it record a tool
that never loaded: T4 failed three times and kx_toolchanger reported T4.
Without a tool changer the pause-and-prompt recovery mid-print stays.
"""
from unittest.mock import Mock, patch

import pytest

import ace.commands
from ace.config import INSTANCE_MANAGERS


class CommandError(Exception):
    """Klipper's gcmd.error: an exception class carrying the message."""


def _gcmd(tool=None):
    gcmd = Mock()
    gcmd.error = CommandError
    gcmd.get_int = Mock(side_effect=lambda name, default=None, **kw:
                        tool if name == "TOOL" else (default or 0))
    return gcmd


def _printer(print_state):
    printer = Mock()
    gcode = Mock()
    toolhead = Mock()
    toolhead.get_kinematics.return_value.get_status.return_value = {
        "homed_axes": "xyz"}
    stats = Mock(get_status=Mock(return_value={"state": print_state}))
    printer.lookup_object = Mock(side_effect=lambda name, default=None: {
        "toolhead": toolhead, "gcode": gcode, "print_stats": stats,
    }.get(name, gcode))
    printer.get_reactor.return_value.monotonic.return_value = 0.0
    return printer, gcode


def _manager(owned_by_tool_changer, enabled=True):
    manager = Mock()
    manager.tool_changer_owns_tools = owned_by_tool_changer
    manager.get_ace_global_enabled.return_value = enabled
    manager.perform_tool_change = Mock(side_effect=RuntimeError(
        "Filament of slot 1 did not reach the intake sensor"))
    manager.state.get = Mock(return_value=-1)
    return manager


def _scripts(gcode):
    return [c.args[0] for c in gcode.run_script_from_command.call_args_list]


def test_a_tool_changers_failed_change_mid_print_is_an_error_not_a_pause():
    manager = _manager(owned_by_tool_changer=True)
    printer, gcode = _printer("printing")
    with patch("ace.commands.get_printer", return_value=printer):
        with pytest.raises(CommandError, match="T4"):
            ace.commands.cmd_ACE_CHANGE_TOOL(manager, _gcmd(), 4)
    assert "PAUSE" not in _scripts(gcode)
    assert not any("prompt_begin" in s for s in _scripts(gcode))


def test_without_a_tool_changer_mid_print_still_pauses_and_prompts():
    manager = _manager(owned_by_tool_changer=False)
    printer, gcode = _printer("printing")
    with patch("ace.commands.get_printer", return_value=printer):
        ace.commands.cmd_ACE_CHANGE_TOOL(manager, _gcmd(), 4)   # no raise
    assert "PAUSE" in _scripts(gcode)


def test_the_command_passes_an_idle_failure_on_to_its_caller():
    INSTANCE_MANAGERS[0] = _manager(owned_by_tool_changer=True)
    printer, gcode = _printer("standby")
    try:
        with patch("ace.commands.get_printer", return_value=printer):
            with pytest.raises(CommandError, match="T4"):
                ace.commands.cmd_ACE_CHANGE_TOOL_WRAPPER(_gcmd(tool=4))
    finally:
        INSTANCE_MANAGERS.clear()


def test_disabled_ace_support_refuses_a_tool_changers_change():
    manager = _manager(owned_by_tool_changer=True, enabled=False)
    with pytest.raises(CommandError, match="disabled"):
        ace.commands.cmd_ACE_CHANGE_TOOL(manager, _gcmd(), 4)
    manager.perform_tool_change.assert_not_called()


def _registered(name):
    """The handler Klipper calls for `name`, as register_all_commands
    hands it over - with the wrapping a direct call skips."""
    handlers = {}
    gcode = Mock()
    gcode.register_command = Mock(
        side_effect=lambda cmd, handler, desc=None: handlers.update({cmd: handler}))
    printer = Mock()
    printer.lookup_object = Mock(return_value=gcode)
    ace.commands.register_all_commands(printer)
    return handlers[name]


def test_the_registered_command_passes_the_failure_on():
    # Every ACE command is registered wrapped; the wrapper turned this
    # error into a console line and a dialog, and kx_toolchanger saw success.
    INSTANCE_MANAGERS[0] = _manager(owned_by_tool_changer=True, enabled=False)
    try:
        with patch("ace.commands.get_printer", return_value=_printer("standby")[0]):
            with pytest.raises(CommandError, match="disabled"):
                _registered("ACE_CHANGE_TOOL")(_gcmd(tool=4))
    finally:
        INSTANCE_MANAGERS.clear()


def test_an_unexpected_exception_is_still_caught_by_the_registered_command():
    # Anything but a G-code error escaping a command shuts Klipper down.
    INSTANCE_MANAGERS[0] = _manager(owned_by_tool_changer=True)
    gcmd = _gcmd(tool=4)
    gcmd.get_int = Mock(side_effect=KeyError("boom"))
    try:
        with patch("ace.commands.get_printer", return_value=_printer("standby")[0]):
            _registered("ACE_QUERY_SLOTS")(gcmd)   # no raise
    finally:
        INSTANCE_MANAGERS.clear()
