"""Feed assist pauses while the printer is idle.

An assist left on with nothing extruding keeps the ACE motor pushing a
standing filament: the motor warms up and the ACE fan starts. Klipper's
idle timeout (motors off) stops the loaded tool's assist; the next toolhead
activity restores it on the same tool.
"""
from .config import get_ace_instance_and_slot_for_tool


class IdleFeedAssist:
    def __init__(self, printer, manager):
        self.manager = manager
        self.reactor = printer.get_reactor()
        # The tool whose assist the idle timeout stopped; None when this
        # hook stopped nothing.
        self.paused_tool = None
        printer.register_event_handler("idle_timeout:idle", self._handle_idle)
        printer.register_event_handler(
            "idle_timeout:printing", self._handle_printing)

    # Both events arrive inside a reactor timer or a toolhead sync; stopping
    # and starting assist wait on the ACE, so they run as callbacks.
    def _handle_idle(self, print_time):
        self.reactor.register_callback(lambda eventtime: self._stop())

    def _handle_printing(self, print_time):
        if self.paused_tool is not None:
            self.reactor.register_callback(lambda eventtime: self._restore())

    def _stop(self):
        if self.manager.toolchange_in_progress:
            return
        tool = self.manager.state.get("ace_current_index", -1)
        instance, slot = get_ace_instance_and_slot_for_tool(tool)
        if instance is None or instance._feed_assist_index != slot:
            return
        self.manager.disable_feed_assist_for_tool(tool, "printer idle")
        self.paused_tool = tool

    def _restore(self):
        tool, self.paused_tool = self.paused_tool, None
        if tool is None:
            return
        # A tool change since the stop owns the new tool's assist.
        if self.manager.state.get("ace_current_index", -1) != tool:
            return
        self.manager.verify_feed_assist_for_tool(tool)
