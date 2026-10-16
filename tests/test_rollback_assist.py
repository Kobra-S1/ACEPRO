"""Rollback assist: an ACE that holds its filament while idle follows the
extruder's retract.

An ACE2 clamps the filament whenever it is neither feeding nor assisting.
The intake-gated unload and park retract with the extruder alone, so on an
ACE2 the extruder slipped against the clamp: on a Kobra X, 100 mm of
retract moved the filament about 40 mm, the toolhead sensor never cleared,
and the toolhead's inlet fitting was pulled on. Anycubic's firmware wraps
that retract in FEED_OR_ROLLBACK mode 3 ("rollback_assist", length and
speed 0) and stops it with STOP_FEED_OR_ROLLBACK (avata_main 2.0.2.2,
0x01257778 -> 0x01273f10 -> 0x013392f0). An ACE1 lets go by itself and
gets no such request.
"""
import pytest

from ace.protocol_ace1 import AceJsonProtocolAdapter
from ace.protocol_ace2 import AceProtoProtocolAdapter
from tests.test_intake_gated import SLOT, FakeFilament, FakeInstance, transfer_for


class ClampedFilament(FakeFilament):
    """Held by an idle ACE2: a retract moves it only while the ACE follows."""

    def __init__(self, tip):
        super().__init__(tip)
        self.ace_follows = False

    def moved(self, length):
        if length < 0 and not self.ace_follows:
            return
        super().moved(length)


class FakeProtocol:
    def __init__(self, clamps):
        self.clamps = clamps

    def build_start_rollback_assist_request(self, slot):
        return {"start rollback assist": slot} if self.clamps else None

    def build_stop_unwind_filament_request(self, slot):
        return {"stop": slot}

    def slow_first_unwind(self):
        return (40.0, 4.0) if self.clamps else None


class FakeReactor:
    def monotonic(self):
        return 0.0

    def pause(self, until):
        pass


class Ace2Instance(FakeInstance):
    """An ACE whose requests are answered at once; `answer` is the reply."""

    def __init__(self, filament, clamps=True):
        super().__init__(filament)
        self.protocol = FakeProtocol(clamps)
        self.reactor = FakeReactor()
        self.answer = {"code": 0, "msg": "SUCCESS"}
        self.ready_waits = 0

    def send_request(self, request, callback):
        if "start rollback assist" in request:
            self.steps.append("rollback assist on")
            self.filament.ace_follows = self.answer is not None
        else:
            self.steps.append("rollback assist off")
            self.filament.ace_follows = False
        callback(self.answer)

    def wait_ready(self):
        self.ready_waits += 1


def unload(instance, extruder_limit=100.0):
    return transfer_for(instance).unload(
        SLOT, extruder_limit=extruder_limit, extruder_speed=15.0)


def park(instance):
    return transfer_for(instance).park(
        SLOT, extruder_limit=100.0, extruder_speed=15.0)


def test_a_clamping_ace_follows_the_unloads_extruder_retract():
    instance = Ace2Instance(ClampedFilament(tip=30.0))

    unload(instance)

    assert not instance.filament.in_gear
    steps = instance.steps
    first_extruder = min(i for i, s in enumerate(steps) if s.startswith("extruder"))
    last_extruder = max(i for i, s in enumerate(steps) if s.startswith("extruder"))
    assert steps.index("rollback assist on") < first_extruder
    # Off before the ACE pulls: its retract is a rollback of its own.
    assert last_extruder < steps.index("rollback assist off") \
        < steps.index("ace retract 2 90@50")


def test_a_clamping_ace_follows_the_parks_extruder_retract():
    instance = Ace2Instance(ClampedFilament(tip=30.0))

    park(instance)

    assert not instance.filament.at_sensor
    assert instance.steps.count("rollback assist on") == 1
    assert instance.steps.count("rollback assist off") == 1


def test_an_ace_that_lets_go_by_itself_gets_no_rollback_assist():
    instance = Ace2Instance(FakeFilament(tip=30.0), clamps=False)

    unload(instance)

    assert "rollback assist on" not in instance.steps
    assert "rollback assist off" not in instance.steps


def test_the_rollback_assist_is_stopped_when_the_retract_fails():
    instance = Ace2Instance(ClampedFilament(tip=30.0))

    with pytest.raises(ValueError, match="still sees filament"):
        unload(instance, extruder_limit=20.0)

    assert instance.steps[-1] == "rollback assist off"
    assert instance.ready_waits >= 1


def test_a_refused_rollback_assist_stops_before_the_extruder_pulls():
    instance = Ace2Instance(ClampedFilament(tip=30.0))
    instance.answer = {"code": 1, "msg": "FORBIDDEN"}

    with pytest.raises(ValueError, match="rollback assist"):
        unload(instance)

    assert not any(s.startswith("extruder") for s in instance.steps)


def test_the_ace2_request_is_feed_or_rollback_mode_3():
    request = AceProtoProtocolAdapter().build_start_rollback_assist_request(SLOT)

    assert request["command"] == "FEED_OR_ROLLBACK"
    assert request["params"] == {"index": SLOT, "speed": 0, "length": 0, "mode": 3}


def test_an_ace1_has_no_rollback_assist_request():
    assert AceJsonProtocolAdapter().build_start_rollback_assist_request(SLOT) is None


def test_the_console_shows_when_the_rollback_assist_runs():
    instance = Ace2Instance(ClampedFilament(tip=30.0))

    unload(instance)

    lines = [m for m in instance.messages if "rollback assist" in m.lower()]
    assert len(lines) == 2
    assert all(f"slot {SLOT}" in m for m in lines)


# --- the slow first pull ------------------------------------------------
# Stock pulls an ACE2's filament out of the inlet slowly before the
# full-speed pull back: "low speed unwind" 40 mm at 4 mm/s, then "high speed
# unwind" at 50 mm/s (avata_main 2.0.2.2, 0x0125d6a8 and 0x0125f5fc).


def test_an_ace2_unload_pulls_slowly_first_then_at_full_speed():
    instance = Ace2Instance(ClampedFilament(tip=30.0))

    unload(instance)

    steps = instance.steps
    assert steps.index("rollback assist off") < steps.index("ace retract 2 40@4") \
        < steps.index("ace retract 2 90@50")


def test_an_ace1_unload_has_no_slow_first_pull():
    instance = Ace2Instance(FakeFilament(tip=30.0), clamps=False)

    unload(instance)

    assert [s for s in instance.steps if s.startswith("ace retract")] == \
        ["ace retract 2 90@50"]


def test_a_park_makes_no_ace_pull_at_all():
    instance = Ace2Instance(ClampedFilament(tip=30.0))

    park(instance)

    assert not any(s.startswith("ace retract") for s in instance.steps)


def test_the_ace2_slow_first_pull_is_stocks():
    assert AceProtoProtocolAdapter().slow_first_unwind() == (40.0, 4.0)
    assert AceJsonProtocolAdapter().slow_first_unwind() is None


def test_the_console_shows_the_slow_first_pull():
    instance = Ace2Instance(ClampedFilament(tip=30.0))

    unload(instance)

    lines = [m for m in instance.messages if "slow" in m.lower()]
    assert len(lines) == 1
    assert "40" in lines[0] and "4" in lines[0] and f"slot {SLOT}" in lines[0]
