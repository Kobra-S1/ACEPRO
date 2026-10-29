"""The runout monitor's events - runout, tangle, a resume without filament -
handled as a queued G-code command rather than on the monitor's timer.

run_script_from_command from a timer skips the G-code lock, so the PAUSE,
dialogs and endless-spool swap ran inside whatever command was executing.
As Klipper's filament sensors do, the timer only stops the print file
(send_pause_command runs no G-code) and queues the handling, which waits
for the running command and drops the event if the print ended meanwhile.
"""
EVENT_COMMAND = "_ACE_MONITOR_EVENT"
HANDLERS = {
    "runout": "_handle_runout_detected",
    "tangle": "_handle_tangle_detected",
    "dry_resume": "_handle_resume_without_filament",
}


def queue(monitor, event, tool):
    """From the monitor timer: stop the file now and queue the handling of
    `event` for `tool`. Further detections are held off until it ran."""
    pause_resume = monitor.printer.lookup_object("pause_resume", None)
    if pause_resume is not None:
        pause_resume.send_pause_command()
    monitor.runout_handling_in_progress = True
    script = "%s EVENT=%s TOOL=%d" % (EVENT_COMMAND, event, tool)

    def send(eventtime):
        try:
            monitor.gcode.run_script(script)
        except Exception as e:
            monitor.runout_handling_in_progress = False
            monitor.gcode.respond_info(
                f"ACE: handling the {event} on T{tool} failed: {e}")

    monitor.reactor.register_callback(send)


def run(monitor, event, tool):
    """The queued command's body. Raises ValueError for an unknown event."""
    if event not in HANDLERS:
        raise ValueError(f"unknown monitor event '{event}'")
    try:
        print_stats = monitor.printer.lookup_object("print_stats", None)
        state = ""
        if print_stats is not None:
            status = print_stats.get_status(monitor.reactor.monotonic())
            state = (status.get("state") or "").lower()
        if state not in ("printing", "paused"):
            monitor.gcode.respond_info(
                f"ACE: {event} on T{tool} not handled, the print is"
                f" {state or 'not running'}")
            return
        getattr(monitor, HANDLERS[event])(tool)
    finally:
        monitor.runout_handling_in_progress = False
