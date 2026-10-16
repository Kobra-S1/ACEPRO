"""ACE_FEED/RETRACT refusals and the dryer start/stop message cycle."""
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

    def get_int(self, name, default=_REQUIRED, **_limits):
        if name not in self.params:
            if default is _REQUIRED:
                raise CommandError(f"Error on '{name}': missing")
            return default
        return int(self.params[name])

    def messages(self):
        return [c.args[0] for c in self.respond_info.call_args_list]


class FakeAce:
    """An ACE whose dryer answers every request at once with code 0."""
    SLOT_COUNT = 4
    feed_speed = 60
    retract_speed = 50
    max_dryer_temperature = 60

    def __init__(self, instance_num=0):
        self.instance_num = instance_num
        self._feed = Mock()
        self._stop_feed = Mock()
        self._retract = Mock()
        self._stop_retract = Mock()

    def start_drying(self, temperature, duration, callback):
        callback({"code": 0})

    def stop_drying(self, callback):
        callback({"code": 0})


@pytest.fixture
def one_ace():
    ace_unit = FakeAce()
    manager = Mock(_ace_pro_enabled=True, instances={0: ace_unit})
    ACE_INSTANCES[0] = ace_unit
    INSTANCE_MANAGERS[0] = manager
    yield ace_unit
    ACE_INSTANCES.clear()
    INSTANCE_MANAGERS.clear()


# --- Dryer -------------------------------------------------------------

def test_stop_for_all_instances_lets_the_next_start_report_again(one_ace):
    gcmd = FakeGcmd(TEMP=50)

    ace.commands.cmd_ACE_START_DRYING(gcmd)
    ace.commands.cmd_ACE_STOP_DRYING(FakeGcmd())
    ace.commands.cmd_ACE_START_DRYING(gcmd)

    started = [m for m in gcmd.messages() if "Dryer started" in m]
    assert len(started) == 2


def test_stop_for_one_instance_lets_the_next_start_report_again(one_ace):
    gcmd = FakeGcmd(INSTANCE=0, TEMP=50)

    ace.commands.cmd_ACE_START_DRYING(gcmd)
    ace.commands.cmd_ACE_STOP_DRYING(FakeGcmd(INSTANCE=0))
    ace.commands.cmd_ACE_START_DRYING(gcmd)

    started = [m for m in gcmd.messages() if "Dryer started" in m]
    assert len(started) == 2


def test_a_failed_stop_keeps_the_start_reported(one_ace):
    one_ace.stop_drying = lambda callback: callback({"code": 1, "msg": "busy"})
    gcmd = FakeGcmd(TEMP=50)

    ace.commands.cmd_ACE_START_DRYING(gcmd)
    stop = FakeGcmd()
    ace.commands.cmd_ACE_STOP_DRYING(stop)
    ace.commands.cmd_ACE_START_DRYING(gcmd)

    assert any("Dryer stop failed: busy" in m for m in stop.messages())
    started = [m for m in gcmd.messages() if "Dryer started" in m]
    assert len(started) == 1


# --- Feed / retract refusals ------------------------------------------

MOVES = [
    (ace.commands.cmd_ACE_FEED, "ACE_FEED", "_feed"),
    (ace.commands.cmd_ACE_RETRACT, "ACE_RETRACT", "_retract"),
]
STOPS = [
    (ace.commands.cmd_ACE_STOP_FEED, "ACE_STOP_FEED", "_stop_feed"),
    (ace.commands.cmd_ACE_STOP_RETRACT, "ACE_STOP_RETRACT", "_stop_retract"),
]


@pytest.mark.parametrize("command, name, action", MOVES)
def test_a_move_on_a_slot_out_of_range_is_an_error(one_ace, command, name, action):
    with pytest.raises(CommandError, match=f"{name} error: Invalid slot 4"):
        command(FakeGcmd(INSTANCE=0, INDEX=4, LENGTH=100))
    getattr(one_ace, action).assert_not_called()


@pytest.mark.parametrize("length", [0, -5])
@pytest.mark.parametrize("command, name, action", MOVES)
def test_a_move_without_a_positive_length_is_an_error(one_ace, command, name,
                                                      action, length):
    with pytest.raises(CommandError, match=f"{name} error: LENGTH must be positive"):
        command(FakeGcmd(INSTANCE=0, INDEX=1, LENGTH=length))
    getattr(one_ace, action).assert_not_called()


@pytest.mark.parametrize("command, name, action", MOVES)
def test_a_move_in_range_reaches_the_ace(one_ace, command, name, action):
    command(FakeGcmd(INSTANCE=0, INDEX=3, LENGTH=100, SPEED=20))
    getattr(one_ace, action).assert_called_once_with(3, 100, 20)


@pytest.mark.parametrize("command, name, action", STOPS)
def test_a_stop_on_a_slot_out_of_range_is_an_error(one_ace, command, name, action):
    with pytest.raises(CommandError, match=f"{name} error: Invalid slot 4"):
        command(FakeGcmd(INSTANCE=0, INDEX=4))
    getattr(one_ace, action).assert_not_called()


@pytest.mark.parametrize("command, name, action", STOPS)
def test_a_stop_in_range_reaches_the_ace(one_ace, command, name, action):
    command(FakeGcmd(INSTANCE=0, INDEX=3))
    getattr(one_ace, action).assert_called_once_with(3)
