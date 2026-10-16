"""Feed assist pauses while the printer is idle.

An assist left on with nothing extruding keeps the ACE motor pushing
against a standing filament: the motor warms up and the ACE fan starts.
"""
from unittest.mock import Mock

from ace.config import ACE_INSTANCES, set_tool_base
from ace.idle_assist import IdleFeedAssist

TOOL = 5            # slot 2 of the first ACE behind three direct inlets
SLOT = 2


class FakeInstance:
    def __init__(self, assisting_slot):
        self._feed_assist_index = assisting_slot


class FakePrinter:
    def __init__(self):
        self.handlers = {}
        self.callbacks = []
        reactor = Mock()
        reactor.register_callback = Mock(
            side_effect=lambda cb, *a: self.callbacks.append(cb))
        self.reactor = reactor

    def register_event_handler(self, event, handler):
        self.handlers[event] = handler

    def get_reactor(self):
        return self.reactor

    def send(self, event):
        self.handlers[event](0.0)
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback(0.0)


class FakeManager:
    """The two manager calls the hook drives; each records and acts like
    the real one on the instance's assist index."""

    def __init__(self, instance, current_tool=TOOL):
        self.instance = instance
        self.state = Mock()
        self.load(current_tool)
        self.toolchange_in_progress = False
        self.disabled = []
        self.verified = []

    def load(self, tool):
        self.state.get = Mock(side_effect=lambda key, default=None: {
            "ace_current_index": tool}.get(key, default))

    def disable_feed_assist_for_tool(self, tool, reason):
        self.disabled.append(tool)
        self.instance._feed_assist_index = -1

    def verify_feed_assist_for_tool(self, tool):
        self.verified.append(tool)
        self.instance._feed_assist_index = SLOT


def _setup(assisting_slot=SLOT, current_tool=TOOL):
    set_tool_base(3)
    instance = FakeInstance(assisting_slot)
    manager = FakeManager(instance, current_tool)
    ACE_INSTANCES.clear()
    ACE_INSTANCES[0] = instance
    printer = FakePrinter()
    IdleFeedAssist(printer, manager)
    return printer, manager, instance


def _teardown():
    ACE_INSTANCES.clear()
    set_tool_base(0)


def test_idle_stops_the_loaded_tools_assist_and_printing_restores_it():
    printer, manager, instance = _setup()
    try:
        printer.send("idle_timeout:idle")
        assert manager.disabled == [TOOL]
        assert instance._feed_assist_index == -1

        printer.send("idle_timeout:printing")
        assert manager.verified == [TOOL]
    finally:
        _teardown()


def test_restores_only_once_per_idle_period():
    printer, manager, _ = _setup()
    try:
        printer.send("idle_timeout:idle")
        printer.send("idle_timeout:printing")
        printer.send("idle_timeout:printing")
        assert manager.verified == [TOOL]
    finally:
        _teardown()


def test_printing_without_an_idle_stop_leaves_assist_alone():
    printer, manager, _ = _setup()
    try:
        printer.send("idle_timeout:printing")
        assert manager.verified == []
    finally:
        _teardown()


def test_idle_without_assist_stops_nothing_and_restores_nothing():
    printer, manager, _ = _setup(assisting_slot=-1)
    try:
        printer.send("idle_timeout:idle")
        printer.send("idle_timeout:printing")
        assert manager.disabled == []
        assert manager.verified == []
    finally:
        _teardown()


def test_idle_with_a_direct_fed_tool_loaded_stops_nothing():
    printer, manager, _ = _setup(current_tool=1)
    try:
        printer.send("idle_timeout:idle")
        assert manager.disabled == []
    finally:
        _teardown()


def test_idle_during_a_tool_change_leaves_assist_to_the_change():
    printer, manager, _ = _setup()
    manager.toolchange_in_progress = True
    try:
        printer.send("idle_timeout:idle")
        assert manager.disabled == []
    finally:
        _teardown()


def test_no_restore_once_another_tool_was_loaded():
    printer, manager, _ = _setup()
    try:
        printer.send("idle_timeout:idle")
        manager.load(4)
        printer.send("idle_timeout:printing")
        assert manager.verified == []
    finally:
        _teardown()
