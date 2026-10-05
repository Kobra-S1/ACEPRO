"""The intake-gated toolhead transfer (extras/ace/intake_gated.py).

For a toolhead whose extruder gear sits between two filament sensors: the
intake sensor (an encoder, counts once the tip arrives at the gear) and the
toolhead sensor (a switch behind the gear). The ACE cannot push through the
gear, and a pull against it is wasted, so both moves stop on the sensors
instead of running fixed lengths.

The fakes play the filament: the ACE feed reaches the intake after a number
of polls, and the toolhead sensor follows the extruder's position.
"""

import pytest

from ace.intake_gated import IntakeGatedTransfer, resolve_intake_sensor

SLOT = 2


class FakeIntake:
    def __init__(self):
        self.edges = 7

    def intake_edges(self):
        return self.edges


class FakeFilament:
    """Position of the tip relative to the toolhead sensor (mm, positive is
    past it), moved by the extruder; the sensor reads `tip > 0`. The gear
    sits GEAR_ABOVE_SENSOR in front of the sensor: retracted that far, the
    tip is out of it and the extruder turns without moving the filament.
    Movement turns the intake encoder, one count per MM_PER_EDGE."""

    MM_PER_EDGE = 5.0

    def __init__(self, tip, gear_above_sensor=10.0):
        self.tip = tip
        self.gear_above_sensor = gear_above_sensor
        self.gripped = True
        # False: parked out of the gear's reach, until the ACE brings it.
        self.at_gear = True
        self.intake = None
        self._uncounted = 0.0

    def moved(self, length):
        if not (self.gripped and self.at_gear):
            return
        if length < 0:
            length = -min(-length, max(0.0, self.tip + self.gear_above_sensor))
        self.tip += length
        self._uncounted += abs(length)
        while self._uncounted >= self.MM_PER_EDGE:
            self._uncounted -= self.MM_PER_EDGE
            self.intake.edges += 1

    @property
    def at_sensor(self):
        return self.tip > 0

    @property
    def in_gear(self):
        return self.tip > -self.gear_above_sensor


class FakeInstance:
    """What IntakeGatedTransfer uses of an AceInstance, recorded in
    `steps`. `polls_to_intake` ACE-feed polls bring the tip to the gear;
    None is a feed that never arrives."""

    instance_num = 0
    FEED_ERROR_GRACE_S = 2.0
    timeout_multiplier = 2
    toolchange_load_length = 300.0
    intake_feed_speed = 30.0
    extruder_feeding_length = 80.0
    extruder_feeding_speed = 5.0
    parkposition_to_toolhead_length = 90.0
    retract_speed = 50.0
    intake_clear_length = 15.0

    def __init__(self, filament, polls_to_intake=3):
        self.filament = filament
        self.polls_to_intake = polls_to_intake
        self.intake = FakeIntake()
        filament.intake = self.intake
        self.steps = []
        self.feeding = False
        self.slot_error = None
        self.speed_change_ok = True
        self.gcode = self
        self.manager = self

    # --- gcode / manager roles ---
    def respond_info(self, message):
        pass

    def get_switch_state(self, sensor):
        return self.filament.at_sensor

    get_instant_switch_state = get_switch_state

    def _wait_toolhead_move_finished(self):
        self.steps.append("wait moves")

    # --- instance ---
    def dwell(self, delay=1.0):
        if self.feeding and self.polls_to_intake is not None:
            self.polls_to_intake -= 1
            if self.polls_to_intake == 0:
                self.intake.edges += 1
                self.filament.at_gear = True

    def execute_feed_with_retries(self, slot, length, speed):
        self.steps.append(f"ace feed {slot} {length:g}@{speed:g}")
        self.feeding = True

    def _change_feed_speed(self, slot, speed):
        self.steps.append(f"ace speed {speed:g}")
        return self.speed_change_ok

    def _stop_feed(self, slot):
        self.steps.append("ace stop")
        self.feeding = False

    def wait_ready(self):
        pass

    def _disable_feed_assist(self, slot):
        self.steps.append("assist off")

    def _enable_feed_assist(self, slot):
        self.steps.append("assist on")

    def _get_slot_feed_error(self, slot):
        return self.slot_error

    def _extruder_move(self, length, speed, wait_for_move_end=False):
        self.steps.append(f"extruder {length:+g}@{speed:g}")
        self.filament.moved(length)

    def _retract(self, slot, length, speed):
        self.steps.append(f"ace retract {slot} {length:g}@{speed:g}")

    def extruder_travel(self):
        return sum(float(s.split()[1].split("@")[0])
                   for s in self.steps if s.startswith("extruder"))

    def without_extruder(self):
        return [s for s in self.steps if not s.startswith("extruder")]


def transfer_for(instance):
    return IntakeGatedTransfer(instance, instance.intake)


# --- load ---------------------------------------------------------------

def test_a_load_feeds_to_the_intake_then_pulls_to_the_toolhead_sensor():
    instance = FakeInstance(FakeFilament(tip=-20.0))

    transfer_for(instance).load(SLOT)

    assert instance.without_extruder() == [
        "assist off", "ace feed 2 300@30", "ace speed 5", "wait moves",
        "ace stop", "assist on",
    ]
    assert instance.filament.at_sensor
    # Stops on the sensor: the 20 mm to it, and at most the motion queued
    # ahead of one poll (0.1 s at 5 mm/s) more.
    assert 20.0 <= instance.extruder_travel() <= 20.0 + 0.5 + 1e-9


def test_the_extruder_stays_still_until_the_intake_counts():
    instance = FakeInstance(FakeFilament(tip=-20.0), polls_to_intake=5)

    transfer_for(instance).load(SLOT)

    first_extruder = next(i for i, s in enumerate(instance.steps)
                          if s.startswith("extruder"))
    assert instance.steps.index("ace speed 5") < first_extruder


def test_filament_already_at_the_toolhead_sensor_is_not_fed_again():
    instance = FakeInstance(FakeFilament(tip=3.0))

    transfer_for(instance).load(SLOT)

    assert instance.steps == ["assist off", "assist on"]


def test_a_feed_that_never_reaches_the_intake_stops_the_ace_and_raises():
    instance = FakeInstance(FakeFilament(tip=-20.0), polls_to_intake=None)

    with pytest.raises(ValueError, match="intake"):
        transfer_for(instance).load(SLOT)

    assert instance.steps[-1] == "ace stop"
    assert instance.extruder_travel() == 0


def test_a_firmware_feed_error_ends_the_wait_at_once():
    instance = FakeInstance(FakeFilament(tip=-20.0), polls_to_intake=None)
    instance.FEED_ERROR_GRACE_S = -1.0
    instance.slot_error = "feed_error"

    with pytest.raises(ValueError, match="feed_error"):
        transfer_for(instance).load(SLOT)

    assert instance.steps == ["assist off", "ace feed 2 300@30", "ace stop"]


def test_a_pull_that_never_reaches_the_sensor_gives_the_filament_back():
    filament = FakeFilament(tip=-20.0)
    filament.gripped = False          # the gear turns, the filament does not
    instance = FakeInstance(filament)

    with pytest.raises(ValueError, match="toolhead sensor"):
        transfer_for(instance).load(SLOT)

    # The whole pull limit was tried, then turned back so the ACE can take
    # the tip out of the gear; the ACE is stopped before that.
    assert instance.extruder_travel() == pytest.approx(0.0)
    pulls = [s for s in instance.steps if s.startswith("extruder +")]
    assert sum(float(s.split()[1].split("@")[0]) for s in pulls) == pytest.approx(80.0)
    assert instance.steps.index("ace stop") < instance.steps.index("extruder -80@5")
    assert "assist on" not in instance.steps


def test_an_ace_that_will_not_slow_down_stops_the_load():
    instance = FakeInstance(FakeFilament(tip=-20.0))
    instance.speed_change_ok = False

    with pytest.raises(ValueError, match="speed"):
        transfer_for(instance).load(SLOT)

    assert instance.steps[-1] == "ace stop"
    assert instance.extruder_travel() == 0


# --- unload -------------------------------------------------------------

def unload(instance, extruder_limit=100.0):
    return transfer_for(instance).unload(
        SLOT, extruder_limit=extruder_limit, extruder_speed=15.0)


def test_an_unload_drives_the_filament_out_of_the_gear_then_the_ace_parks_it():
    instance = FakeInstance(FakeFilament(tip=30.0))

    unload(instance)

    assert instance.without_extruder()[-1] == "ace retract 2 90@50"
    assert not instance.filament.in_gear
    # 30 mm to the sensor and 10 mm more out of the gear, then the extruder
    # turns until the intake has not counted for intake_clear_length.
    assert -(40.0 + 15.0 + 5.0) <= instance.extruder_travel() <= -(40.0 + 10.0)


def test_filament_past_the_sensor_but_still_in_the_gear_is_driven_out_first():
    """The toolhead sensor clearing does not mean the gear has let go: an
    ACE pulling then grinds the filament and leaves it in the gear, where
    the next feed cannot move it either."""
    instance = FakeInstance(FakeFilament(tip=-5.0))

    unload(instance)

    assert not instance.filament.in_gear
    last_extruder = max(i for i, s in enumerate(instance.steps)
                        if s.startswith("extruder"))
    assert instance.steps.index("ace retract 2 90@50") > last_extruder


def test_a_gear_that_never_lets_go_raises_before_the_ace_pulls():
    instance = FakeInstance(FakeFilament(tip=30.0, gear_above_sensor=500.0))

    with pytest.raises(ValueError, match="intake"):
        unload(instance)

    assert not any(s.startswith("ace retract") for s in instance.steps)


def test_the_quiet_length_before_the_ace_pulls_is_the_configured_one():
    short = FakeInstance(FakeFilament(tip=-10.0))
    long = FakeInstance(FakeFilament(tip=-10.0))
    long.intake_clear_length = 30.0

    unload(short)
    unload(long)

    assert short.extruder_travel() == pytest.approx(-15.0)
    assert long.extruder_travel() == pytest.approx(-30.0)


def test_the_ace_does_not_pull_while_the_extruder_holds_the_filament():
    instance = FakeInstance(FakeFilament(tip=30.0))

    unload(instance)

    last_extruder = max(i for i, s in enumerate(instance.steps)
                        if s.startswith("extruder"))
    assert instance.steps.index("ace retract 2 90@50") > last_extruder


def test_an_unload_that_does_not_clear_the_sensor_raises_before_the_ace_pulls():
    instance = FakeInstance(FakeFilament(tip=30.0))

    with pytest.raises(ValueError, match="still sees filament"):
        unload(instance, extruder_limit=20.0)

    assert instance.extruder_travel() == pytest.approx(-20.0)
    assert not any(s.startswith("ace retract") for s in instance.steps)


def test_a_given_park_step_replaces_the_fixed_ace_pull():
    instance = FakeInstance(FakeFilament(tip=30.0))

    result = transfer_for(instance).unload(
        SLOT, extruder_limit=100.0, extruder_speed=15.0,
        park=lambda: instance.steps.append("park") or "parked")

    assert result == "parked"
    assert instance.without_extruder()[-1] == "park"


# --- parking at the intake ----------------------------------------------

def park(instance, extruder_limit=100.0):
    return transfer_for(instance).park(
        SLOT, extruder_limit=extruder_limit, extruder_speed=15.0)


def test_a_park_stops_when_the_sensor_clears_and_the_ace_does_not_pull():
    instance = FakeInstance(FakeFilament(tip=30.0))

    park(instance)

    assert instance.without_extruder() == ["wait moves"]
    assert not instance.filament.at_sensor
    # Still in the gear: the next load of this filament is a short pull.
    assert instance.filament.in_gear
    assert -30.0 - 1.5 - 1e-9 <= instance.extruder_travel() <= -30.0


def test_a_park_that_does_not_clear_the_sensor_raises():
    instance = FakeInstance(FakeFilament(tip=30.0))

    with pytest.raises(ValueError, match="still sees filament"):
        park(instance, extruder_limit=20.0)


def test_a_parked_filament_is_loaded_by_a_short_extruder_pull():
    instance = FakeInstance(FakeFilament(tip=-1.0))

    transfer_for(instance).load(SLOT, parked=True)

    assert instance.filament.at_sensor
    # The ACE only follows at the extruder's speed; no fast feed to the
    # intake, which a filament standing in the gear could not follow.
    assert instance.without_extruder() == [
        "assist off", "ace feed 2 80@5", "wait moves", "ace stop", "assist on"]
    assert 1.0 <= instance.extruder_travel() <= 1.0 + 0.5 + 1e-9


def test_a_parked_filament_further_back_is_pulled_on_while_it_moves():
    """A park overshoots the sensor by what was still queued when it
    cleared. A filament the gear moves is there: it is pulled on to the
    sensor. Putting it back and feeding from the ACE instead pushes against
    the gear that holds it."""
    instance = FakeInstance(FakeFilament(tip=-25.0))
    instance.filament.gear_above_sensor = 45.0

    transfer_for(instance).load(SLOT, parked=True)

    assert instance.filament.at_sensor
    assert "ace feed 2 300@30" not in instance.steps
    assert not any(s.startswith("extruder -") for s in instance.steps)
    assert 25.0 <= instance.extruder_travel() <= 25.0 + 0.5 + 1e-9


def test_a_parked_filament_that_moves_but_never_arrives_raises():
    filament = FakeFilament(tip=-500.0)
    instance = FakeInstance(filament)

    with pytest.raises(ValueError, match="parked"):
        transfer_for(instance).load(SLOT, parked=True)

    assert "ace feed 2 300@30" not in instance.steps
    assert instance.steps.index("ace stop") < instance.steps.index("extruder -80@5")
    assert "assist on" not in instance.steps


def test_a_parked_filament_out_of_the_gears_reach_is_fed_the_normal_way():
    filament = FakeFilament(tip=-20.0)
    filament.at_gear = False
    instance = FakeInstance(filament, polls_to_intake=100)

    transfer_for(instance).load(SLOT, parked=True)

    assert instance.filament.at_sensor
    steps = instance.without_extruder()
    assert steps.index("ace feed 2 80@5") < steps.index("ace feed 2 300@30")
    assert steps[-1] == "assist on"
    # The pull into nothing is turned back before the feed.
    assert instance.steps.index("extruder -15@5") < instance.steps.index(
        "ace feed 2 300@30")


# --- the intake sensor --------------------------------------------------

class FakePrinter:
    def __init__(self, objects):
        self.objects = objects

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)


def test_the_intake_sensor_is_found_by_its_object_name():
    intake = FakeIntake()

    assert resolve_intake_sensor(FakePrinter({"kx_clog_check": intake}),
                                 "kx_clog_check") is intake


def test_a_missing_intake_sensor_names_the_config_key():
    with pytest.raises(ValueError, match="filament_intake_sensor_name"):
        resolve_intake_sensor(FakePrinter({}), "nope")


def test_an_object_that_cannot_count_intake_is_refused():
    with pytest.raises(ValueError, match="intake_edges"):
        resolve_intake_sensor(FakePrinter({"fan": object()}), "fan")
