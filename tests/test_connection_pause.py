"""The pause for an ACE connection issue, which the monitor timer decides.

A G-code command run from a timer with run_script_from_command skips the
G-code lock: PAUSE then ran in the middle of the CANCEL_PRINT that was
executing, and M84 disabled the steppers while PAUSE's moves were queued -
the step that re-enabled them was scheduled in the past and the MCU shut
down ("Timer too close"). The pause is queued instead, runs once the running
command ends, and only while the print is still printing; and only the ACE
feeding the active tool pauses a print.
"""
from unittest.mock import Mock

from ace import connection_pause
from ace.config import set_tool_base
from ace.manager import AceManager


class FakeReactor:
    def __init__(self):
        self.callbacks = []

    def monotonic(self):
        return 0.0

    def register_callback(self, callback):
        self.callbacks.append(callback)

    def run_callbacks(self):
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback(0.0)


def make_manager(current_tool=0, print_state="printing"):
    """An AceManager with only what the connection-issue path touches."""
    manager = object.__new__(AceManager)
    manager.state = Mock()
    manager.state.get = Mock(
        side_effect=lambda key, default=None:
            current_tool if key == "ace_current_index" else default)
    manager.gcode = Mock()
    manager.reactor = FakeReactor()
    manager._connection_issue_shown = False
    manager._pending_connection_issue = None
    manager._show_connection_issue_dialog = Mock()
    stats = Mock()
    stats.get_status.return_value = {"state": print_state}
    manager.printer = Mock()
    manager.printer.lookup_object = Mock(
        side_effect=lambda name, default=None:
            stats if name == "print_stats" else default)
    manager.stats = stats
    return manager


def issue(instance=0):
    return [{"instance": instance, "connected": False,
             "recent_reconnects": 5, "time_connected": 0,
             "window_seconds": 180}]


def inline_pauses(manager):
    return [c for c in manager.gcode.run_script_from_command.call_args_list
            if c.args and c.args[0] == "PAUSE"]


def teardown_function(function):
    set_tool_base(0)


def test_the_timer_queues_the_pause_instead_of_running_it():
    manager = make_manager()
    manager._handle_connection_issue(issue(), eventtime=0.0)
    assert inline_pauses(manager) == []
    manager.gcode.run_script.assert_not_called()
    manager.reactor.run_callbacks()
    manager.gcode.run_script.assert_called_once_with(
        connection_pause.PAUSE_COMMAND)


def test_the_queued_pause_pauses_a_print_still_printing():
    manager = make_manager()
    manager._handle_connection_issue(issue(), eventtime=0.0)
    connection_pause.run_pause(manager)
    assert len(inline_pauses(manager)) == 1
    manager._show_connection_issue_dialog.assert_called_once_with(
        issue(), is_printing=True)


def test_a_print_cancelled_before_the_queued_pause_ran_is_not_paused():
    manager = make_manager()
    manager._handle_connection_issue(issue(), eventtime=0.0)
    manager.stats.get_status.return_value = {"state": "cancelled"}
    connection_pause.run_pause(manager)
    assert inline_pauses(manager) == []


def test_the_queued_pause_runs_once():
    manager = make_manager()
    manager._handle_connection_issue(issue(), eventtime=0.0)
    connection_pause.run_pause(manager)
    connection_pause.run_pause(manager)
    assert len(inline_pauses(manager)) == 1


def test_a_print_on_a_direct_tool_is_not_paused():
    # A tool changer's own tool is loaded: the ACE has no tool on record.
    set_tool_base(3)
    manager = make_manager(current_tool=-1)
    manager._handle_connection_issue(issue(), eventtime=0.0)
    manager.reactor.run_callbacks()
    manager.gcode.run_script.assert_not_called()
    manager._show_connection_issue_dialog.assert_called_once_with(
        issue(), is_printing=False)


def test_an_issue_of_another_unit_does_not_pause_the_print():
    # T5 is on unit 1 (base 0); unit 0 is the one dropping out.
    manager = make_manager(current_tool=5)
    manager._handle_connection_issue(issue(instance=0), eventtime=0.0)
    manager.reactor.run_callbacks()
    manager.gcode.run_script.assert_not_called()


def test_the_active_tools_unit_is_found_past_a_tool_base():
    # Shared topology: ACE tools from T3, so T6 is unit 0's last slot
    # (counted from T0 it would be unit 1's).
    set_tool_base(3)
    manager = make_manager(current_tool=6)
    manager._handle_connection_issue(issue(instance=0), eventtime=0.0)
    manager.reactor.run_callbacks()
    manager.gcode.run_script.assert_called_once_with(
        connection_pause.PAUSE_COMMAND)


def test_a_paused_print_gets_the_dialog_without_another_pause():
    manager = make_manager(print_state="paused")
    manager._handle_connection_issue(issue(), eventtime=0.0)
    manager.reactor.run_callbacks()
    manager.gcode.run_script.assert_not_called()
    manager._show_connection_issue_dialog.assert_called_once_with(
        issue(), is_printing=True)
