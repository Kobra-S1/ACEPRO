"""Intake-gated toolhead transfer: the load and unload motion for a toolhead
whose extruder gear sits between two filament sensors.

- The *intake* sensor counts filament movement in front of the gear: its
  first count says the ACE has brought the tip to the gear.
- The *toolhead* sensor (the one every strategy uses) sits behind the gear:
  only the extruder can move filament to it or off it.

So a load is "ACE feeds to the intake, extruder pulls to the toolhead
sensor", and an unload is "extruder retracts until the toolhead sensor
clears and on until the intake stops counting, then the ACE pulls the
freed filament back". Both stop on the sensors; the configured lengths are
limits. This is the sequence Anycubic's
firmware runs on the Kobra X (ACE feed and unwind state machines).

Selected with ``toolhead_strategy: intake_gated``; the default,
``sensor_push``, is the fixed-length motion in instance.py / manager.py.
"""

from .config import SENSOR_TOOLHEAD

STRATEGY_SENSOR_PUSH = "sensor_push"
STRATEGY_INTAKE_GATED = "intake_gated"
STRATEGIES = (STRATEGY_SENSOR_PUSH, STRATEGY_INTAKE_GATED)

# toolhead_paths: whether a filament parked at the intake is in the way of
# another tool. "shared": the tools meet in one tube in front of the
# toolhead. "per_tool": every tool has its own tube to its own intake.
PATHS_SHARED = "shared"
PATHS_PER_TOOL = "per_tool"
PATHS = (PATHS_SHARED, PATHS_PER_TOOL)

# The ACE feed is polled for the intake count at this rate.
INTAKE_POLL_S = 0.05
# Extruder motion is queued this far ahead of each sensor read, so the move
# stays continuous; it is also how far the extruder runs past the sensor.
EXTRUDER_STEP_S = 0.1
SPEED_CHANGE_TRIES = 3
# A parked filament stands in the gear. Pulled this far, the intake has
# counted if the gear moves it (several counts' worth); if not, it is not
# there.
RESUME_PROBE_MM = 15.0


def resolve_intake_sensor(printer, name):
    """The printer object named by ``filament_intake_sensor_name``.

    It must offer ``intake_edges()``: a count that only grows, of filament
    movement in the path currently in front of the extruder.
    """
    sensor = printer.lookup_object(name, None)
    if sensor is None:
        raise ValueError(
            f"ACE: filament_intake_sensor_name '{name}' is not a printer "
            f"object (toolhead_strategy: {STRATEGY_INTAKE_GATED} needs one)"
        )
    if not callable(getattr(sensor, "intake_edges", None)):
        raise ValueError(
            f"ACE: filament_intake_sensor_name '{name}' has no "
            f"intake_edges() and cannot serve as intake sensor"
        )
    return sensor


class IntakeGatedTransfer:
    """Load and unload motion of one AceInstance. Runs inside the gcode
    command that asked for it, like the instance's other moves."""

    def __init__(self, instance, intake):
        self.instance = instance
        self.intake = intake

    def _at_toolhead_sensor(self):
        return self.instance.manager.get_switch_state(SENSOR_TOOLHEAD)

    def _clear_of_toolhead_sensor(self):
        return not self.instance.manager.get_instant_switch_state(SENSOR_TOOLHEAD)

    def _extruder_until(self, done, direction, speed, limit):
        """Turn the extruder until ``done()``, at most ``limit`` mm; returns
        the distance turned. Queued in short moves so it can stop."""
        instance = self.instance
        step = speed * EXTRUDER_STEP_S
        travelled = 0.0
        while travelled < limit and not done():
            length = min(step, limit - travelled)
            instance._extruder_move(direction * length, speed)
            travelled += length
            instance.dwell(length / speed)
        instance.manager._wait_toolhead_move_finished()
        return travelled

    # --- load ------------------------------------------------------------
    def load(self, local_slot, parked=False):
        """Bring the slot's filament to the toolhead sensor and leave feed
        assist on. ``parked``: park() left it at the gear, so the extruder
        pulls first; not found there, it is fed like any other. Raises
        ValueError, with the ACE stopped and the extruder's pull turned
        back, when it does not arrive."""
        instance = self.instance
        instance._disable_feed_assist(local_slot)
        if not self._at_toolhead_sensor():
            if not (parked and self._resume(local_slot)):
                self._feed_to_intake(local_slot)
                self._pull_to_toolhead_sensor(local_slot)
            instance._stop_feed(local_slot)
            instance.wait_ready()
        instance._enable_feed_assist(local_slot)

    def _resume(self, local_slot):
        """Pull a parked filament the short way back to the toolhead sensor,
        the ACE following at the extruder's speed; returns whether it came.
        A fast ACE feed is no use here: the gear holds the filament."""
        instance = self.instance
        speed = instance.extruder_feeding_speed
        limit = instance.extruder_feeding_length
        mark = self.intake.intake_edges()
        instance.execute_feed_with_retries(local_slot, limit, speed)
        pulled = self._extruder_until(self._at_toolhead_sensor, 1, speed,
                                      min(RESUME_PROBE_MM, limit))
        if self._at_toolhead_sensor():
            return True
        moving = self.intake.intake_edges() != mark
        if moving:
            # Parked further back than the probe: a park overshoots the
            # sensor by the motion still queued when it cleared.
            pulled += self._extruder_until(self._at_toolhead_sensor, 1, speed,
                                           limit - pulled)
            if self._at_toolhead_sensor():
                return True
        instance._stop_feed(local_slot)
        instance._extruder_move(-pulled, speed, wait_for_move_end=True)
        if moving:
            # In the gear, so an ACE feed could only push against it.
            raise ValueError(
                f"ACE[{instance.instance_num}]: The parked filament of slot "
                f"{local_slot} moves but did not reach the toolhead sensor "
                f"after {limit:.0f}mm of extruder pull."
            )
        return False

    def _feed_to_intake(self, local_slot):
        instance = self.instance
        name = f"ACE[{instance.instance_num}]"
        length = instance.toolchange_load_length
        speed = instance.intake_feed_speed
        mark = self.intake.intake_edges()
        instance.execute_feed_with_retries(local_slot, length, speed)
        timeout_s = length / speed * instance.timeout_multiplier
        waited = 0.0
        while self.intake.intake_edges() == mark and not self._at_toolhead_sensor():
            error = None
            if waited > instance.FEED_ERROR_GRACE_S:
                slot_error = instance._get_slot_feed_error(local_slot)
                if slot_error is not None:
                    error = (f"{name}: Firmware aborted the feed on slot "
                             f"{local_slot}: {slot_error}. Filament cannot "
                             f"advance - check spool, slot outlet and filament path.")
            if error is None and waited > timeout_s:
                error = (f"{name}: Filament of slot {local_slot} did not reach "
                         f"the intake sensor within {length:.0f}mm of feed.")
            if error is not None:
                instance._stop_feed(local_slot)
                raise ValueError(error)
            instance.dwell(INTAKE_POLL_S)
            waited += INTAKE_POLL_S

        slow = instance.extruder_feeding_speed
        for _ in range(SPEED_CHANGE_TRIES):
            if instance._change_feed_speed(local_slot, slow):
                return
            instance.dwell(0.2)
        instance._stop_feed(local_slot)
        raise ValueError(
            f"{name}: Failed to change feed speed to {slow}mm/s for the "
            f"extruder to take the filament over"
        )

    def _pull_to_toolhead_sensor(self, local_slot):
        instance = self.instance
        speed = instance.extruder_feeding_speed
        limit = instance.extruder_feeding_length
        pulled = self._extruder_until(self._at_toolhead_sensor, 1, speed, limit)
        if self._at_toolhead_sensor():
            return
        instance._stop_feed(local_slot)
        # Out of the gear again, or the ACE could not take the filament back.
        instance._extruder_move(-pulled, speed, wait_for_move_end=True)
        raise ValueError(
            f"ACE[{instance.instance_num}]: Filament of slot {local_slot} "
            f"reached the intake but not the toolhead sensor after "
            f"{limit:.0f}mm of extruder pull."
        )

    # --- park and unload -------------------------------------------------
    def park(self, local_slot, extruder_limit, extruder_speed):
        """Take the slot's cut filament out of the hotend and no further:
        the extruder retracts until the toolhead sensor clears (at most
        ``extruder_limit`` mm) and the ACE does not pull. The tip stays in
        the gear, for load(parked=True). Raises ValueError when the sensor
        does not clear."""
        self._extruder_until(self._clear_of_toolhead_sensor, -1,
                             extruder_speed, extruder_limit)
        if not self._clear_of_toolhead_sensor():
            raise ValueError(
                f"ACE[{self.instance.instance_num}]: Toolhead sensor still "
                f"sees filament after {extruder_limit:.0f}mm of extruder "
                f"retract."
            )

    def unload(self, local_slot, extruder_limit, extruder_speed, park=None):
        """Take the slot's cut filament out of the toolhead: the extruder
        retracts until the toolhead sensor clears (at most
        ``extruder_limit`` mm) and on until the gear has let go, then the
        ACE pulls it back - by parkposition_to_toolhead_length, or as
        ``park()`` does it (a setup with a sensor on the way back stops on
        that). The ACE does not pull while the gear still holds the
        filament. Returns whether the park step succeeded; raises
        ValueError when the filament does not leave sensor or gear."""
        instance = self.instance
        self._extruder_until(self._clear_of_toolhead_sensor, -1,
                             extruder_speed, extruder_limit)
        if not self._clear_of_toolhead_sensor():
            raise ValueError(
                f"ACE[{instance.instance_num}]: Toolhead sensor still sees "
                f"filament after {extruder_limit:.0f}mm of extruder retract."
            )
        self._retract_out_of_gear(extruder_speed)
        if park is not None:
            return park()
        instance._retract(local_slot, instance.parkposition_to_toolhead_length,
                          instance.retract_speed)
        return True

    def _retract_out_of_gear(self, speed):
        """Keep retracting until the intake has not counted for
        intake_clear_length: the toolhead sensor is behind the gear, so its
        clearing leaves the tip still in the gear. Out of it, the extruder
        turns and nothing moves."""
        instance = self.instance
        limit = instance.extruder_feeding_length
        clear_length = instance.intake_clear_length
        step = speed * EXTRUDER_STEP_S
        mark = self.intake.intake_edges()
        travelled = quiet = 0.0
        while quiet < clear_length and travelled < limit:
            length = min(step, limit - travelled)
            instance._extruder_move(-length, speed)
            travelled += length
            instance.dwell(length / speed)
            edges = self.intake.intake_edges()
            if edges != mark:
                mark = edges
                quiet = 0.0
            else:
                quiet += length
        instance.manager._wait_toolhead_move_finished()
        if quiet < clear_length:
            raise ValueError(
                f"ACE[{instance.instance_num}]: The intake sensor still "
                f"counts after {limit:.0f}mm of extruder retract past the "
                f"toolhead sensor - the filament has not left the gear."
            )
