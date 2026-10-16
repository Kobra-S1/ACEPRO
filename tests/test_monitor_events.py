"""The runout monitor's events, run as queued G-code commands.

The monitor's timer ran PAUSE, dialogs and the endless-spool swap with
run_script_from_command, which skips the G-code lock: they ran inside
whatever command was executing (a CANCEL_PRINT, a tool change). As
Klipper's own runout sensor does, the timer now only stops the print file -
send_pause_command runs no G-code - and queues the handling behind the
running command, which drops it if the print ended meanwhile.
"""
from unittest.mock import Mock

import pytest

from ace import monitor_events


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


def make_monitor(print_state="printing"):
    monitor = Mock()
    monitor.reactor = FakeReactor()
    monitor.gcode = Mock()
    monitor.runout_handling_in_progress = False
    pause_resume = Mock()
    stats = Mock()
    stats.get_status.return_value = {"state": print_state}
    objects = {"pause_resume": pause_resume, "print_stats": stats}
    monitor.printer.lookup_object = Mock(
        side_effect=lambda name, default=None: objects.get(name, default))
    monitor.pause_resume = pause_resume
    monitor.stats = stats
    return monitor


def test_a_runout_stops_the_file_at_once_and_runs_no_gcode_inline():
    monitor = make_monitor()
    monitor_events.queue(monitor, "runout", 3)
    monitor.pause_resume.send_pause_command.assert_called_once_with()
    monitor.gcode.run_script_from_command.assert_not_called()
    monitor.gcode.run_script.assert_not_called()
    monitor.reactor.run_callbacks()
    monitor.gcode.run_script.assert_called_once_with(
        "_ACE_MONITOR_EVENT EVENT=runout TOOL=3")


def test_a_queued_event_holds_off_another_detection():
    monitor = make_monitor()
    monitor_events.queue(monitor, "tangle", 4)
    assert monitor.runout_handling_in_progress is True


@pytest.mark.parametrize("event, handler", [
    ("runout", "_handle_runout_detected"),
    ("tangle", "_handle_tangle_detected"),
    ("dry_resume", "_handle_resume_without_filament"),
])
def test_the_queued_command_runs_the_events_handler(event, handler):
    monitor = make_monitor()
    monitor.runout_handling_in_progress = True
    monitor_events.run(monitor, event, 3)
    getattr(monitor, handler).assert_called_once_with(3)
    assert monitor.runout_handling_in_progress is False


def test_a_print_that_ended_before_the_command_ran_is_left_alone():
    # Detected while a CANCEL_PRINT was executing; it finished first.
    monitor = make_monitor(print_state="cancelled")
    monitor.runout_handling_in_progress = True
    monitor_events.run(monitor, "runout", 3)
    monitor._handle_runout_detected.assert_not_called()
    assert monitor.runout_handling_in_progress is False


def test_a_paused_print_still_gets_the_handling():
    # Paused by hand meanwhile: PAUSE is a no-op, the dialog still matters.
    monitor = make_monitor(print_state="paused")
    monitor_events.run(monitor, "runout", 3)
    monitor._handle_runout_detected.assert_called_once_with(3)


def test_an_unknown_event_is_refused():
    monitor = make_monitor()
    with pytest.raises(ValueError):
        monitor_events.run(monitor, "flood", 3)


def test_a_queue_that_fails_reports_and_frees_the_monitor():
    monitor = make_monitor()
    monitor.gcode.run_script.side_effect = RuntimeError("printer is shutdown")
    monitor_events.queue(monitor, "runout", 3)
    monitor.reactor.run_callbacks()
    assert monitor.runout_handling_in_progress is False
    assert "printer is shutdown" in str(monitor.gcode.respond_info.call_args)
