"""The pause for an ACE connection issue, decided on the manager's monitor
timer and run as a queued G-code command.

run_script_from_command from a timer skips the G-code lock, so a PAUSE sent
that way ran inside whatever command was executing - inside CANCEL_PRINT it
interleaved its moves with the cancel's motor-off and shut the MCU down. The
queued command runs once the running one ends, and pauses only a print that
is still printing then.
"""
from .config import SLOTS_PER_ACE, get_tool_base

PAUSE_COMMAND = "_ACE_CONNECTION_PAUSE"


def active_instance(manager):
    """The unit feeding the loaded ACE tool, or None when no ACE tool is
    loaded (a printer's own tool changer may have one of its own)."""
    tool = manager.state.get("ace_current_index", -1)
    base = get_tool_base()
    if tool is None or tool < base:
        return None
    return (tool - base) // SLOTS_PER_ACE


def print_state(manager):
    print_stats = manager.printer.lookup_object("print_stats", None)
    if print_stats is None:
        return ""
    status = print_stats.get_status(manager.reactor.monotonic())
    return (status.get("state") or "").lower()


def queue(manager, issue):
    """Queue the pause for `issue` (the unstable instances); from a timer.
    A later queue before it ran replaces the issue it shows."""
    manager._pending_connection_issue = issue

    def send(eventtime):
        try:
            manager.gcode.run_script(PAUSE_COMMAND)
        except Exception as e:
            manager.gcode.respond_info(
                f"ACE: Error pausing print for the connection issue: {e}")

    manager.reactor.register_callback(send)


def run_pause(manager):
    """The queued command's body: pause the print and show the dialog, if it
    is still printing; a paused print gets the dialog alone."""
    issue = manager._pending_connection_issue
    manager._pending_connection_issue = None
    if issue is None:
        return
    state = print_state(manager)
    if state == "printing":
        manager.gcode.respond_info("ACE: Pausing print due to connection issue")
        try:
            manager.gcode.run_script_from_command("PAUSE")
        except Exception as e:
            # The dialog still says why the print stopped.
            manager.gcode.respond_info(
                f"ACE: Error pausing print for the connection issue: {e}")
    elif state != "paused":
        manager.gcode.respond_info(
            f"ACE: connection issue pause dropped, the print is {state or 'not running'}")
        return
    manager._show_connection_issue_dialog(issue, is_printing=True)
