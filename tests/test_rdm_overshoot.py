"""The RDM-monitored unload's overshoot is a move of its own.

The unload retracts at most `length` and stops once the RDM clears; the
overshoot then pulls the tip `rdm_overshoot_length` further, past the
4-in-1's merge point. When the RDM clears only after that retract has
ended - on the Kobra X the toolhead-to-RDM path is about as long as the
maximum - there is no running move left to extend, and the tip stayed at
the switch, in the merge, blocking the next slot.

The ACE's retract (_retract, the serial boundary) is replaced by a fake
that records the moves and reports the RDM state the scenario sets.
"""
from unittest.mock import Mock

from ace.instance import AceInstance

SLOT = 1
SPEED = 50.0


class FakeAce:
    """Records retracts; the RDM clears when `clears_after` retracts ended,
    or never when it is None."""

    def __init__(self, clears_after):
        self.moves = []
        self.clears_after = clears_after
        self.manager = Mock()
        self.manager.has_rdm_sensor.return_value = True
        self.manager.get_instant_switch_state.side_effect = self.rdm_present

    def rdm_present(self, sensor):
        return self.clears_after is None or len(self.moves) < self.clears_after

    def retract(self, slot, length, speed, early_stop_callback=None, **kwargs):
        self.moves.append((slot, length, speed))
        # The move runs to its end, then the callback sees the sensor.
        if early_stop_callback is not None:
            reason = early_stop_callback()
            if reason:
                return {"code": 0, "msg": "Retract stopped early: %s" % reason}
        return {"code": 0, "msg": "success"}


def _instance(fake):
    inst = object.__new__(AceInstance)
    inst.instance_num = 0
    inst.retract_speed = SPEED
    inst.gcode = Mock()
    inst.reactor = Mock()
    inst.reactor.monotonic.return_value = 0.0
    inst._get_current_feed_assist_index = Mock(return_value=-1)
    inst._disable_feed_assist = Mock()
    inst._restore_assist_after_unload = Mock()
    inst._retract = fake.retract
    return inst


def test_the_overshoot_is_retracted_when_the_rdm_clears_after_the_move():
    fake = FakeAce(clears_after=1)
    ok = _instance(fake).rmd_triggered_unload_slot(
        fake.manager, SLOT, length=650, overshoot_length=200)
    assert ok
    assert fake.moves == [(SLOT, 650, SPEED), (SLOT, 200, SPEED)]


def test_no_overshoot_when_the_rdm_never_clears():
    fake = FakeAce(clears_after=None)
    ok = _instance(fake).rmd_triggered_unload_slot(
        fake.manager, SLOT, length=650, overshoot_length=200)
    assert not ok
    assert fake.moves == [(SLOT, 650, SPEED)]


def test_a_zero_overshoot_adds_no_move():
    fake = FakeAce(clears_after=1)
    ok = _instance(fake).rmd_triggered_unload_slot(
        fake.manager, SLOT, length=650, overshoot_length=0)
    assert ok
    assert fake.moves == [(SLOT, 650, SPEED)]
