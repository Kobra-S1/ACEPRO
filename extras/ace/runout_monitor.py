"""
Runout and tangle monitoring for ACE Pro.

Runout: toolhead sensor present → absent transition, coordinated with
endless spool for automatic material swapping.

Tangle (optional): watches the ACE-reported ``cont_assist_time``
field; pauses the print when it stays above ``tangle_pump_time``
seconds.  Covers both generations — ACE2 firmware only self-detects
blocked *commanded* feeds (slot goes ``gear_err``); during feed assist
(the mid-print case) it pumps against a tangle indefinitely, exactly
like ACE1.  Protocol decoders normalize ``cont_assist_time`` to seconds
(ACE2 reports milliseconds on the wire).

A spool running out AT the ACE produces the same continuous-pumping
signature as a tangle ("nothing left to push" vs "can't push"), so a
threshold crossing alone must not pause the print — the remaining
filament in the bowden must keep printing until the toolhead sensor
triggers normal runout/endless-spool handling.  The slot presence
state disambiguates, with generation-specific timing:

- ACE2 reports the slot ``empty`` immediately at sensor-clear, ~100 s
  BEFORE its starved assist starts cycling (pump ramps that self-reset
  at ~3.9 s, forever).  The empty-slot gate suppresses the detector for
  the whole tail transit.
- ACE1 keeps reporting ``ready`` until its starved assist gives up:
  continuous pump ~5 s -> ``unwinding`` (resets the counter) ->
  ``empty`` — i.e. the empty report arrives ~4 s AFTER the threshold
  crossing.  The suspect window bridges that lag: a crossing arms a
  verdict window (``tangle_verify_time``) instead of pausing; slot
  going empty within it means runout (no pause), expiry with the slot
  still non-empty means a real tangle (pause).  There is deliberately
  NO "counter dropped" exit: ACE1's give-up unwind and ACE2's retry
  cycling both reset the counter mid-runout, and real-tangle ramps can
  dip — a drop proves nothing.
"""

import logging

from .config import (
    SENSOR_TOOLHEAD,
    SLOTS_PER_ACE,
    get_instance_from_tool,
    get_local_slot,
    ACE_INSTANCES,
)


class RunoutMonitor:
    """Periodic monitor for filament runout and ACE-side tangle detection."""

    # pump_time defaults.  The default and floor are hardware-derived, not
    # tuning headroom: ACE2 firmware's STARVED assist retry cycle (spool ran
    # out at the ACE) self-resets at ~3.9 s (observed on fw V1.1.31), so any
    # threshold at or below that band would cross on every ACE2 runout and
    # only the empty-slot gate would stand between that and a false pause.
    # Real tangles grow without bound on both generations, so a higher
    # threshold costs only its own value in detection latency (1 s of
    # cont_assist_time growth per wall-clock second).  Re-measure the retry
    # cap before lowering these if a firmware update changes it.
    DEFAULT_TANGLE_PUMP_TIME = 5.0
    TANGLE_PUMP_TIME_FLOOR = 3.0
    # Verdict window after a threshold crossing (see _check_tangle).  ACE1
    # reports the slot empty ~4 s after the crossing (firmware-fixed give-up
    # sequence, print-speed independent) — 7 s covers it ~2x over.  The
    # window is a fallback: most real tangles exit earlier via the hard
    # ceiling below, and ACE2 pauses at the crossing itself.
    DEFAULT_TANGLE_VERIFY_TIME = 7.0
    # Hard ceiling on continuous pumping: STARVED pumping is firmware-capped
    # (ACE1 gives up and unwind-resets the counter at ~5-6 s, ACE2's retry
    # cycle self-resets at ~3.9 s), so a counter at/above this value can only
    # mean a real blockage with filament present.  Fires immediately, without
    # waiting out the verdict window.  The floor keeps it above ACE1's
    # give-up cap.
    DEFAULT_TANGLE_HARD_LIMIT = 8.0
    TANGLE_HARD_LIMIT_FLOOR = 6.5

    def __init__(self, printer, gcode, reactor, endless_spool, manager,
                 runout_debounce_count=1, tangle_detection=False,
                 tangle_pump_time=None, tangle_verify_time=None,
                 tangle_pump_time_hard=None):
        """Initialize runout monitor.

        Args:
            printer, gcode, reactor: Klipper objects.
            endless_spool: EndlessSpool instance for automatic swapping.
            manager: AceManager (sensor queries, state, ACE instances).
            runout_debounce_count: Consecutive sensor-absent reads before
                confirming a runout (default 1 = no debounce).
            tangle_detection: Enable pump_time tangle detection (ACE Gen 1).
            tangle_pump_time: cont_assist_time threshold in seconds for
                tangle trigger (default DEFAULT_TANGLE_PUMP_TIME, clamped
                to TANGLE_PUMP_TIME_FLOOR).
            tangle_verify_time: Seconds to wait after a threshold crossing
                for the slot-empty runout verdict before pausing as a tangle
                (default DEFAULT_TANGLE_VERIFY_TIME; 0 pauses immediately at
                the threshold like the pre-verdict behavior, trading false
                pauses on ACE1 runouts for the fastest possible tangle stop).
            tangle_pump_time_hard: Continuous-pumping ceiling in seconds —
                a fresh sample at/above it pauses immediately, bypassing
                the verdict window (default DEFAULT_TANGLE_HARD_LIMIT,
                clamped to TANGLE_HARD_LIMIT_FLOOR: starved pumping is
                firmware-capped below the floor, so only a real blockage
                can reach it).
        """
        self.printer = printer
        self.gcode = gcode
        self.reactor = reactor
        self.endless_spool = endless_spool
        self.manager = manager

        self.runout_debounce_count = max(1, int(runout_debounce_count))
        self._runout_false_count = 0

        self.prev_toolhead_sensor_state = None
        self.last_printing_active = False
        self.last_print_state = "idle"
        self.monitor_debug_counter = 0

        self.runout_detection_active = False
        self.runout_handling_in_progress = False
        self._monitoring_timer = None

        # Tangle detection (pump_time)
        self.tangle_detection_enabled = bool(tangle_detection)
        threshold = float(
            tangle_pump_time if tangle_pump_time is not None
            else self.DEFAULT_TANGLE_PUMP_TIME
        )
        if threshold < self.TANGLE_PUMP_TIME_FLOOR:
            logging.warning(
                "ACE: tangle_pump_time=%.1f below floor %.1f; clamping",
                threshold, self.TANGLE_PUMP_TIME_FLOOR,
            )
            threshold = self.TANGLE_PUMP_TIME_FLOOR
        self.tangle_pump_time = threshold
        verify = float(
            tangle_verify_time if tangle_verify_time is not None
            else self.DEFAULT_TANGLE_VERIFY_TIME
        )
        self.tangle_verify_time = max(0.0, verify)
        hard = float(
            tangle_pump_time_hard if tangle_pump_time_hard is not None
            else self.DEFAULT_TANGLE_HARD_LIMIT
        )
        if hard < self.TANGLE_HARD_LIMIT_FLOOR:
            logging.warning(
                "ACE: tangle_pump_time_hard=%.1f below floor %.1f "
                "(ACE1's starved give-up reaches ~6s); clamping",
                hard, self.TANGLE_HARD_LIMIT_FLOOR,
            )
            hard = self.TANGLE_HARD_LIMIT_FLOOR
        self.tangle_pump_time_hard = hard
        # pump_time state — see _check_tangle for semantics
        self._pt_last_value_s = 0.0
        self._pt_phase_armed = False
        # Wall-clock (reactor) time the verdict window was armed, or None
        self._pt_suspect_since = None
        # One-shot latch for the empty-slot info message per depletion
        self._pt_empty_notified = False
        self._pt_unsupported_logged = False
        # Last _is_tangle_detection_active() result seen by the monitor
        # loop; used to clear stale phase state on an off→on edge (the
        # dashboard pin can be flipped without going through
        # set_tangle_detection_enabled).
        self._tangle_was_active = False
        # One-shot latch for the resume-without-filament net: warn+pause
        # only once per dry resume, so a deliberate second resume proceeds.
        self._resume_no_filament_warned = False

    def start_monitoring(self):
        """Start runout detection monitor loop."""
        self.gcode.respond_info("ACE: Starting runout detection monitor")
        self.set_detection_active(True)
        self._monitoring_timer = self.reactor.register_timer(
            self._monitor_runout,
            self.reactor.NOW
        )

    def stop_monitoring(self):
        """Stop runout monitoring."""
        self.gcode.respond_info("ACE: Stopping runout detection monitor")
        self.set_detection_active(False)
        if self._monitoring_timer:
            try:
                self.reactor.unregister_timer(self._monitoring_timer)
            except Exception:
                pass
            self._monitoring_timer = None

    def set_detection_active(self, active):
        """
        Enable/disable runout detection with tracing.

        Args:
            active: True to enable detection, False to disable

        Returns:
            bool: The new active state
        """
        old_state = self.runout_detection_active
        self.runout_detection_active = active

        if old_state != active:
            state_str = 'ENABLED' if active else 'DISABLED'
            self.gcode.respond_info(
                f"ACE: Runout detection {state_str} "
                f"(was: {old_state}, now: {active}, "
                f"toolchange_in_progress={self.manager.toolchange_in_progress})"
            )

        return active

    def _monitor_runout(self, eventtime):
        """
        Monitor filament runout during printing.

        This is the main monitoring loop that runs periodically via reactor timer.
        It tracks print state, sensor states, and detects runout events.

        Args:
            eventtime: Current event time from reactor

        Returns:
            float: Next callback time (eventtime + interval)
        """
        # Get current state
        print_stats = self.printer.lookup_object("print_stats", None)
        is_printing = False
        raw_print_state = ""
        if print_stats:
            try:
                stats = print_stats.get_status(eventtime)
                raw_print_state = (stats.get("state") or "").lower()
                is_printing = raw_print_state == "printing"
            except Exception:
                is_printing = False
                raw_print_state = ""

        current_tool = self.manager.state.get("ace_current_index", -1)
        current_sensor_state = self.manager.get_switch_state(SENSOR_TOOLHEAD)

        # Track state changes for logging
        old_printing_active = self.last_printing_active
        old_print_state = self.last_print_state
        self.last_printing_active = is_printing
        self.last_print_state = raw_print_state

        if old_print_state != raw_print_state:
            self.gcode.respond_info(f"ACE: Print state changed: {old_print_state} → {raw_print_state}")
            # Resume safety net: re-verify feed assist on the loaded tool.
            # An ACE power cycle or klippy restart while paused can silently
            # lose feed assist — resuming then prints nothing (ACE2 clamps
            # filament entirely when not feeding).
            if (
                old_print_state == "paused"
                and raw_print_state == "printing"
                and current_tool >= 0
            ):
                self._verify_feed_assist_on_resume(current_tool)
                self._check_resume_without_filament(
                    current_tool, current_sensor_state
                )

        # Detect print start and force initialize
        print_just_started = (
            is_printing and
            not old_printing_active and
            raw_print_state == "printing" and
            current_tool >= 0
        )

        if print_just_started:
            self.gcode.respond_info("ACE: Print started - initializing runout detection")

            # Force initialize baseline
            self.prev_toolhead_sensor_state = current_sensor_state

            # Enable detection immediately if sensor shows filament
            if current_sensor_state:
                self.set_detection_active(True)
                self.gcode.respond_info(
                    f"ACE: Runout detection ENABLED at print start "
                    f"(sensor: True, tool: T{current_tool})"
                )
            else:
                self.gcode.respond_info(
                    f"ACE: Runout detection WAITING at print start "
                    f"(sensor: False, tool: T{current_tool})"
                )

            # Sync macro state
            try:
                self.gcode.run_script_from_command(
                    f"SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE={current_tool}"
                )
            except Exception as e:
                self.gcode.respond_info(f"ACE: Could not sync macro state: {e}")

            return eventtime + 0.05

        # DEBUG LOGGING every ~15 minutes
        self.monitor_debug_counter += 1
        if self.monitor_debug_counter >= 1200 * 15:
            self.monitor_debug_counter = 0
            self.gcode.respond_info(
                f"ACE: Monitor - Tool: T{current_tool}, "
                f"Printing: {is_printing} ({raw_print_state}), "
                f"Prev sensor: {self.prev_toolhead_sensor_state}, "
                f"Current sensor: {current_sensor_state}, "
                f"Detection active: {self.runout_detection_active}, "
                f"Toolchange: {self.manager.toolchange_in_progress}, "
                f"Runout handling: {self.runout_handling_in_progress}, "
                f"Debounce: {self._runout_false_count}/{self.runout_debounce_count}"
            )

            # For debugging: Auto-recovery check
            # WARN if detection should be active but isn't
            if (is_printing and
                    current_sensor_state and
                    not self.runout_detection_active and
                    current_tool >= 0 and
                    not self.manager.toolchange_in_progress and
                    not self.runout_handling_in_progress):

                self.gcode.respond_info(
                    "ACE: Autorecovery: ⚠ WARNING - Runout detection should be active but is disabled! "
                    "Attempting to enable..."
                )

                # Try to recover
                self.prev_toolhead_sensor_state = current_sensor_state
                self.set_detection_active(True)

                self.gcode.respond_info(
                    f"ACE: Autorecovery: Auto-recovery attempted - detection re-enabled "
                    f"(sensor: {current_sensor_state}, tool: T{current_tool})"
                )

        # Early exit if detection disabled or toolchange in progress
        if not self.runout_detection_active or self.manager.toolchange_in_progress:
            self._reset_tangle_phase()
            return eventtime + 0.2

        try:
            if current_tool < 0:
                # No active tool - nothing to monitor
                self.prev_toolhead_sensor_state = None
                self._runout_false_count = 0
                return eventtime + 0.1

            print_just_stopped = old_printing_active and (not is_printing) and (raw_print_state != "paused")

            # PRINT STOPPED - clean up state
            if print_just_stopped:
                self.gcode.respond_info("ACE: Print stopped/cancelled - resetting monitor baseline")
                self.prev_toolhead_sensor_state = None
                self._runout_false_count = 0
                self._reset_tangle_phase()
                self._resume_no_filament_warned = False
                self.runout_handling_in_progress = False

                if not self.runout_detection_active:
                    self.gcode.respond_info("ACE: Restoring runout monitoring after print stop")
                    self.set_detection_active(True)

                try:
                    self.gcode.run_script_from_command(
                        "SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE=-1"
                    )
                except Exception as e:
                    self.gcode.respond_info(f"ACE: Could not sync macro state on print stop: {e}")

                return eventtime + 0.2

            # PAUSED or NOT PRINTING - sleep/relax monitoring
            if raw_print_state == "paused" or not is_printing:
                self.prev_toolhead_sensor_state = None
                self._runout_false_count = 0
                self._reset_tangle_phase()
                return eventtime + 0.2

            # Enhanced baseline initialization
            if self.prev_toolhead_sensor_state is None:
                self.prev_toolhead_sensor_state = current_sensor_state
                self._runout_false_count = 0
                filament_pos = self.manager.state.get("ace_filament_pos", "bowden")

                self.gcode.respond_info(
                    f"ACE: Monitoring baseline established. "
                    f"Sensor: {'present' if current_sensor_state else 'absent'}, "
                    f"Tool: T{current_tool}, State: {filament_pos}"
                )

                # If sensor has filament and we're printing, enable detection immediately
                if current_sensor_state and is_printing and current_tool >= 0:
                    if not self.runout_detection_active:
                        self.set_detection_active(True)
                        self.gcode.respond_info("ACE: Runout detection enabled (baseline init)")

                # Sync macro state
                try:
                    self.gcode.run_script_from_command(
                        f"SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE={current_tool}"
                    )
                except Exception as e:
                    self.gcode.respond_info(f"ACE: Could not sync macro state: {e}")

                return eventtime + 0.05

            # ===== RUNOUT DETECTION - detect present → absent transition =====
            if self.prev_toolhead_sensor_state is True and current_sensor_state is False:
                # Sensor went absent - increment debounce counter
                self._runout_false_count += 1

                if self._runout_false_count < self.runout_debounce_count:
                    # Not yet confirmed - keep prev as True, poll again quickly
                    return eventtime + 0.05

                # Debounce threshold reached - confirmed runout
                self._runout_false_count = 0

                if self.runout_handling_in_progress:
                    self.gcode.respond_info("ACE: Runout detection suppressed (already handling runout)")
                    self.prev_toolhead_sensor_state = current_sensor_state
                    return eventtime + 0.2

                self.gcode.respond_info(
                    f"ACE: Runout detected on T{current_tool} "
                    f"(sensor: present → absent, confirmed after "
                    f"{self.runout_debounce_count} readings)"
                )

                self._handle_runout_detected(current_tool)

                self.prev_toolhead_sensor_state = current_sensor_state
                return eventtime + 0.2

            # Sensor is present (or was already absent) - reset debounce counter
            if self._runout_false_count > 0:
                self._runout_false_count = 0

            # ===== TANGLE DETECTION (optional) =====
            tangle_active = self._is_tangle_detection_active()
            if tangle_active and not self._tangle_was_active:
                # Re-enabled mid-print (possibly via the dashboard pin,
                # which bypasses set_tangle_detection_enabled): clear
                # stale phase state so the detector re-arms from scratch.
                self._reset_tangle_phase()
            self._tangle_was_active = tangle_active
            if tangle_active and not self.runout_handling_in_progress:
                self._check_tangle(current_tool, eventtime)

            # Update previous state for next cycle
            self.prev_toolhead_sensor_state = current_sensor_state
            return eventtime + 0.05

        except self.printer.command_error as e:
            # Klipper printer error
            error_msg = str(e)
            if "shutdown" in error_msg.lower() or "lost communication" in error_msg.lower():
                self.gcode.respond_info("ACE: Monitor stopped due to printer shutdown/MCU disconnect")
                self.set_detection_active(False)
                self.runout_handling_in_progress = False
                return self.reactor.NEVER
            else:
                self.gcode.respond_info(f"ACE: Monitor command error: {e}")
                return eventtime + 1.0

        except Exception as e:
            self.gcode.respond_info(f"ACE: Monitor error: {e}")
            return eventtime + 1.0

    def _check_resume_without_filament(self, tool_index, sensor_present):
        """Pause a resume that has no filament at the toolhead.

        Safety net for any path that resumes a print with a tool recorded
        as loaded but nothing at the toolhead sensor (e.g. a failed tool
        reload inside the RESUME macro that gets swallowed, letting
        BASE_RESUME print into air — runout detection cannot catch this, it
        needs a present→absent transition and the baseline is already
        absent).  One-shot per dry resume: after the warning pause, a
        deliberate second RESUME proceeds unhindered.
        """
        if sensor_present:
            self._resume_no_filament_warned = False
            return
        if self._resume_no_filament_warned:
            return  # user chose to continue without filament
        if getattr(self.manager, "toolchange_in_progress", False) is True:
            return  # a toolchange is still loading; not a dry resume
        self._resume_no_filament_warned = True
        self.gcode.respond_info(
            f"ACE: Print resumed but NO filament at the toolhead sensor "
            f"(tool T{tool_index} recorded as loaded) - pausing to protect "
            f"the print"
        )
        try:
            self._pause_for_runout()
            self.gcode.run_script_from_command(
                'RESPOND TYPE=command MSG="action:prompt_begin '
                'Resumed Without Filament"'
            )
            self.gcode.run_script_from_command(
                f'RESPOND TYPE=command MSG="action:prompt_text '
                f'The print resumed but the toolhead sensor sees no '
                f'filament for T{tool_index}. Retry the load, or resume '
                f'again to continue anyway."'
            )
            self.gcode.run_script_from_command(
                f'RESPOND TYPE=command MSG="action:prompt_button '
                f'Retry T{tool_index}|T{tool_index}|primary"'
            )
            self.gcode.run_script_from_command(
                'RESPOND TYPE=command MSG="action:prompt_footer_button '
                'Resume anyway|RESUME|secondary"'
            )
            self.gcode.run_script_from_command(
                'RESPOND TYPE=command MSG="action:prompt_footer_button '
                'Cancel Print|CANCEL_PRINT|error"'
            )
            self.gcode.run_script_from_command(
                'RESPOND TYPE=command MSG="action:prompt_show"'
            )
        except Exception as e:
            self.gcode.respond_info(
                f"ACE: Resume-without-filament handling error: {e}"
            )

    def _verify_feed_assist_on_resume(self, tool_index):
        """Schedule the manager's feed assist verification off-timer.

        verify_feed_assist_for_tool() blocks on wait_ready(); running it
        inside this reactor timer callback would stall the monitor loop,
        so hand it to a reactor callback greenlet instead.
        """
        verify = getattr(self.manager, "verify_feed_assist_for_tool", None)
        if not callable(verify):
            return
        self.reactor.register_callback(lambda eventtime: verify(tool_index))

    # ========== Tangle Detection (pump_time) ==========

    def set_tangle_detection_enabled(self, enabled):
        """Live toggle of tangle detection mid-print.

        Clears in-flight phase tracking on toggle so a stale
        cont_assist_time value cannot fire immediately on re-enable.
        """
        self.tangle_detection_enabled = bool(enabled)
        self._reset_tangle_phase()

    def _reset_tangle_phase(self):
        """Clear all in-flight tangle tracking (arming, verdict window).

        Called whenever monitoring context is lost — pause, print stop,
        toolchange, detection toggle — so no stale sample or half-elapsed
        verdict window survives into the next monitored stretch.
        """
        self._pt_phase_armed = False
        self._pt_last_value_s = 0.0
        self._pt_suspect_since = None
        self._pt_empty_notified = False

    def _is_tangle_detection_active(self):
        """True when the detector should run this cycle.

        [output_pin TANGLE_DETECTION] is authoritative when configured —
        the slider IS the runtime control, so command and slider stay
        consistent regardless of which side toggles.  Without the pin,
        the python flag (tangle_detection config / ACE_TANGLE_DETECTION
        command) is the source of truth.
        """
        try:
            pin = self.printer.lookup_object(
                "output_pin TANGLE_DETECTION", None
            )
        except Exception:
            pin = None
        if pin is not None:
            try:
                value = pin.get_status(
                    self.reactor.monotonic()
                ).get("value", 1)
                return bool(int(round(float(value))))
            except Exception:
                pass  # read failure → fall through to flag
        return self.tangle_detection_enabled

    def _check_tangle(self, current_tool, eventtime=None):
        """Watch cont_assist_time and classify sustained pumping.

        Called from the monitor loop only while actively printing —
        the dispatcher gates on is_printing, toolchange_in_progress,
        and runout_handling_in_progress.  Noops further unless some
        instance is actively pumping (feed assist enabled).  Covers
        ACE1 and ACE2: ACE2's device-native gear_err detection only
        fires on commanded feeds, never during feed assist (verified
        on hardware), so pump-time monitoring is needed there too.

        Detection: tracks monotonic growth of cont_assist_time.  The
        first growing reading only arms the phase, a value drop disarms
        it, and a growing value at or above the threshold while armed
        crosses into the verdict stage.  Crossing requires two
        *distinct* growing samples: the monitor ticks every 50 ms but
        the heartbeat refreshes cont_assist_time at only 1 Hz, so an
        unchanged value is a re-read of the same sample — a single
        stale sample (e.g. right after re-enabling detection) cannot
        trigger.  The duration itself is the device-reported
        cont_assist_time — no local elapsed-time tracking.

        Verdict: sustained pumping means EITHER a tangle (can't push)
        OR the spool ran out at the ACE (nothing left to push) — the
        slot presence state disambiguates (see module docstring for
        the per-generation hardware timing):
        - Assist slot already reported empty → runout in transit:
          suppress entirely while it stays empty (covers ACE2, whose
          empty report precedes the pumping anomaly by ~100 s, and the
          minutes-long tail transit on both generations).
        - Threshold crossed with the slot non-empty: on ACE2 that IS a
          tangle (its slot state is sensor-live — a runout reports empty
          long before its starved pumping starts): pause immediately.
          On ACE1 (lagging slot state machine) arm a verdict window of
          tangle_verify_time seconds instead of pausing (0 = pause
          immediately).  Slot goes empty within it → runout, no pause.
          A fresh sample at/above tangle_pump_time_hard → tangle, pause
          now (starved pumping is firmware-capped below it).  Window
          expires with the slot still non-empty → tangle, pause.
          Counter DROPS during the window are ignored: ACE1's give-up
          unwind and ACE2's starved retry cycle both reset the counter
          mid-runout, and real-tangle ramps can dip — a drop proves
          nothing either way.
        """
        if eventtime is None:
            eventtime = self.reactor.monotonic()

        inst = self._get_active_assist_instance(current_tool)
        if inst is None:
            self._pt_phase_armed = False
            self._pt_suspect_since = None
            return

        # Runout gate / runout verdict: the device reports the pumping
        # slot as empty — sustained pumping is starvation, not a tangle.
        if self._assist_slot_empty(inst):
            if self._pt_suspect_since is not None:
                self.gcode.respond_info(
                    f"ACE: Not a tangle — T{current_tool} spool ran out at "
                    f"the ACE (slot empty). Print continues on the remaining "
                    f"filament; runout handling engages at the toolhead sensor."
                )
            elif not self._pt_empty_notified:
                self.gcode.respond_info(
                    f"ACE: T{current_tool} spool ran out at the ACE (slot "
                    f"empty) — tangle detection suspended while the remaining "
                    f"filament prints out."
                )
            self._pt_empty_notified = True
            self._pt_phase_armed = False
            self._pt_last_value_s = 0.0
            self._pt_suspect_since = None
            return
        self._pt_empty_notified = False

        info = getattr(inst, "_info", None) or {}
        val = info.get("cont_assist_time") if isinstance(info, dict) else None
        if val is None:
            if not self._pt_unsupported_logged:
                self._pt_unsupported_logged = True
                logging.info(
                    "ACE: tangle detection requested but firmware does not "
                    "report cont_assist_time — detector disabled"
                )
            return
        current = float(val)

        prev = self._pt_last_value_s
        self._pt_last_value_s = current

        # Verdict window armed: the slot-empty gate above, the hard
        # ceiling, or expiry may end it — counter DROPS prove nothing here
        # (ACE1's give-up unwind and ACE2's retry cycle both reset the
        # counter mid-runout).
        if self._pt_suspect_since is not None:
            # Hard ceiling: starved pumping is firmware-capped well below
            # this (ACE1 give-up ~5-6 s, ACE2 retry cap ~3.9 s), so a fresh
            # growing sample at/above it proves filament is present and
            # blocked — pause now instead of waiting out the window.
            if current > prev and current >= self.tangle_pump_time_hard:
                self._fire_tangle(current_tool, current)
                return
            if eventtime - self._pt_suspect_since >= self.tangle_verify_time:
                self._fire_tangle(current_tool, current)
            return

        # Cycle ended or idle: reset phase
        if current < prev or current <= 0.0:
            self._pt_phase_armed = False
            return

        # Unchanged value = re-read of the same heartbeat sample —
        # no new information, keep the armed state but never fire.
        if current == prev:
            return

        # First fresh growing sample: arm the phase, wait for the next
        if not self._pt_phase_armed:
            self._pt_phase_armed = True
            return

        # Threshold crossed → verdict stage (or straight to pause when
        # the verify window is disabled)
        if current >= self.tangle_pump_time:
            if self.tangle_verify_time <= 0.0:
                self._fire_tangle(current_tool, current)
                return
            # Already at/above the hard ceiling on the crossing sample
            # (e.g. detection re-enabled mid-tangle): no verdict needed.
            if current >= self.tangle_pump_time_hard:
                self._fire_tangle(current_tool, current)
                return
            # ACE2's slot state is sensor-live: a runout reports 'empty'
            # ~100 s BEFORE its starved pumping even starts, and that
            # pumping self-caps below the threshold (~3.9 s) — so on ACE2 a
            # crossing with a non-empty slot (the gate above already
            # returned for empty) IS a tangle.  No verdict window needed;
            # pause ~10 s sooner.  ACE1 keeps reporting 'ready' until ~4 s
            # after the crossing and must wait for the slot verdict.
            if self._slot_state_is_sensor_live(inst):
                self._fire_tangle(current_tool, current)
                return
            self._pt_suspect_since = eventtime
            self.gcode.respond_info(
                f"ACE: Possible tangle on T{current_tool} (ACE pumping "
                f"{current:.1f}s) — verifying for up to "
                f"{self.tangle_verify_time:.0f}s. A spool runout reports the "
                f"slot empty and resumes silently; a real tangle will pause."
            )

    def _slot_state_is_sensor_live(self, inst):
        """True when the instance reports slot presence live (ACE2-family).

        ACE2 firmware flips the slot to 'empty' the moment its presence
        sensor clears; ACE1's slot state is a lagging state machine.
        feed_assist_causes_busy() doubles as the generation marker.
        Strict `is True` so mocks and read failures fall back to the
        windowed (ACE1) verdict path.
        """
        try:
            return inst.protocol.feed_assist_causes_busy() is True
        except Exception:
            return False

    def _fire_tangle(self, current_tool, current):
        """Confirmed tangle: log, wipe phase state, pause + prompt."""
        logging.warning(
            "ACE: TANGLE DETECTED on T%d — cont_assist_time=%.1fs "
            "(threshold %.1fs, hard limit %.1fs, verify window %.0fs) "
            "with the slot never reporting empty",
            current_tool, current, self.tangle_pump_time,
            self.tangle_pump_time_hard, self.tangle_verify_time,
        )
        self._reset_tangle_phase()
        self._handle_tangle_detected(current_tool)

    def _assist_slot_empty(self, inst):
        """True when the device reports the pumping slot as empty.

        Fail-closed to False (= keep tangle detection active): degraded
        or missing slot status must never mute the detector, only a
        positive device-side "empty" may.
        """
        try:
            slot = getattr(inst, "_feed_assist_index", -1)
            if slot is None or slot < 0:
                return False
            return inst._is_slot_empty(slot) is True
        except Exception:
            return False

    def _get_active_assist_instance(self, current_tool=-1):
        """Return the instance whose feed assist should be monitored.

        With a loaded tool, ONLY that tool's instance qualifies, and only
        while its assist is on that tool's slot — under the single-assist
        invariant any other instance's assist is stale state, and
        monitoring it points the detector (and its empty-slot gate) at the
        wrong hardware: a stale assist index left on the outgoing ACE by an
        endless-spool swap would make a first-match scan monitor that
        instance, whose empty slot then suppresses detection and blinds it
        to a tangle on the loaded tool's instance.  Without a resolvable
        tool (no print, tool outside the configured instances) fall back to
        the first pumping instance.

        Both generations are monitored: ACE2's native gear_err detection
        only covers commanded feeds — during feed assist it pumps against
        a tangle indefinitely, same as ACE1.  Each protocol decoder
        normalizes cont_assist_time to seconds.
        """
        try:
            instances = getattr(self.manager, "instances", None) or []
            if current_tool is not None and current_tool >= 0:
                # Pure arithmetic on purpose: get_instance_from_tool()
                # validates against the global ACE_INSTANCES registry and
                # returns -1 when it is inconsistent — which would silently
                # fall through to the first-match scan below, i.e. exactly
                # the wrong-instance behavior this resolution exists to
                # prevent.  manager.instances is the authority here.
                inst_num = current_tool // SLOTS_PER_ACE
                if inst_num < len(instances):
                    inst = instances[inst_num]
                    slot = current_tool % SLOTS_PER_ACE
                    if getattr(inst, "_feed_assist_index", -1) == slot:
                        return inst
                    # Assist not on the loaded tool's slot: nothing valid
                    # to monitor — never fall back to another instance's
                    # (stale) assist.
                    return None
            for inst in instances:
                if getattr(inst, "_feed_assist_index", -1) < 0:
                    continue
                return inst
        except Exception:
            pass
        return None

    def _handle_tangle_detected(self, tool_index):
        """Pause the print and prompt the user to clear the tangle."""
        self.runout_handling_in_progress = True
        self._reset_tangle_phase()

        try:
            self.gcode.respond_info(
                f"ACE: Spool tangle detected on T{tool_index}! "
                f"ACE pumping continuously without delivering filament. "
                f"Pausing print."
            )
            self._pause_for_runout()

            # Build prompt
            self.gcode.run_script_from_command(
                'RESPOND TYPE=command MSG="action:prompt_begin Spool Tangle Detected"'
            )
            self.gcode.run_script_from_command(
                f'RESPOND TYPE=command MSG="action:prompt_text '
                f'Spool tangle detected on T{tool_index}! '
                f'ACE can\'t push filament. Check the spool, then resume."'
            )
            self.gcode.run_script_from_command(
                'RESPOND TYPE=command MSG="action:prompt_footer_button '
                'Resume|RESUME|primary"'
            )
            self.gcode.run_script_from_command(
                'RESPOND TYPE=command MSG="action:prompt_footer_button '
                'Disable Det. & Resume|_ACE_TANGLE_DISABLE_AND_RESUME|secondary"'
            )
            self.gcode.run_script_from_command(
                'RESPOND TYPE=command MSG="action:prompt_footer_button '
                'Cancel Print|CANCEL_PRINT|error"'
            )
            self.gcode.run_script_from_command(
                'RESPOND TYPE=command MSG="action:prompt_show"'
            )
        except Exception as e:
            self.gcode.respond_info(f"ACE: Tangle handling error: {e}")
        finally:
            self.runout_handling_in_progress = False

    def _show_runout_prompt(self, tool_index, instance_num, local_slot, material, color):
        """
        Show simple Mainsail prompt for runout with CANCEL/RESUME buttons.

        Args:
            tool_index: Global tool index (e.g., 0-7)
            instance_num: ACE instance number
            local_slot: Local slot number on instance
            material: Material type (e.g., "PLA")
            color: RGB color array [r, g, b]
        """
        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_begin Filament Runout"'
        )

        color_str = f"RGB({color[0]},{color[1]},{color[2]})"
        prompt_text = (
            f"Filament runout detected on Tool T{tool_index}! "
            f"Please refill ACE {instance_num} Slot {local_slot} with {material} filament "
            f"(Color: {color_str})."
        )

        self.gcode.run_script_from_command(
            f'RESPOND TYPE=command MSG="action:prompt_text {prompt_text}"'
        )

        self.gcode.run_script_from_command(
            f'RESPOND TYPE=command MSG="action:prompt_button Retry T{tool_index}|T{tool_index}|primary"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_button Extrude 100mm|'
            '_EXTRUDE LENGTH=100 SPEED=300|secondary"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_button Retract 100mm|'
            '_RETRACT LENGTH=100 SPEED=300|secondary"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_footer_button Resume|RESUME|primary"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_footer_button Cancel Print|CANCEL_PRINT|error"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_show"'
        )

    def _handle_runout_detected(self, tool_index):
        """
        Handle filament runout detection.

        Flow:
        1. Pause the print immediately
        2. Show interactive prompt with CANCEL/RESUME options
        3. Check if endless spool is enabled
        4. If enabled: try to find exact material/color match in other slots
        5. If match found: close prompt, perform automatic tool swap and resume
        6. If no match or endless spool disabled: stay paused (user must refill)

        Resets sensor tracking to prevent repeated triggers.

        Args:
            tool_index: Tool index where runout was detected
        """
        self.gcode.respond_info(f"ACE: Runout detected on T{tool_index}")
        self.runout_handling_in_progress = True
        self.prev_toolhead_sensor_state = None
        self._runout_false_count = 0

        try:
            # Step 1: PAUSE immediately
            self._pause_for_runout()

            # Step 2: the runout tool's assist is useless now (the tail is
            # past the toolhead, nothing left to push) and actively harmful
            # on ACE2, where a surviving assist keeps the device
            # busy-by-design and deadlocks the wait_ready of any subsequent
            # reload.
            try:
                disable = getattr(
                    self.manager, "disable_feed_assist_for_tool", None
                )
                if callable(disable):
                    disable(tool_index, "runout confirmed at toolhead sensor")
            except Exception as e:
                self.gcode.respond_info(
                    f"ACE: Warning - could not disable assist after "
                    f"runout: {e}"
                )

            # Get runout details for prompt
            instance_num = get_instance_from_tool(tool_index)
            material = "unknown"
            color = [0, 0, 0]
            local_slot = -1

            if instance_num >= 0:
                local_slot = get_local_slot(tool_index, instance_num)
                ace_inst = ACE_INSTANCES.get(instance_num)
                if ace_inst and 0 <= local_slot < len(ace_inst.inventory):
                    inv = ace_inst.inventory[local_slot]
                    material = inv.get("material", "unknown")
                    color = inv.get("color", [0, 0, 0])
                    self.gcode.respond_info(
                        f"ACE: Runout on T{tool_index}: {material} "
                        f"RGB({color[0]},{color[1]},{color[2]})"
                    )

            # Step 3: Show simple interactive prompt
            self._show_runout_prompt(tool_index, instance_num, local_slot, material, color)

            # Step 4: Check if endless spool is enabled
            endless_spool_enabled = self.manager.state.get("ace_endless_spool_enabled", False)

            if not endless_spool_enabled:
                self.gcode.respond_info(
                    "ACE: Endless spool disabled. Staying paused. "
                    "Refill spool and resume manually."
                )
                return

            # Step 5: Try to find exact material/color match
            next_tool = self.endless_spool.find_exact_match(tool_index)
            if next_tool < 0:
                self.gcode.respond_info(
                    f"ACE: No endless spool match found for T{tool_index}. "
                    f"Staying paused. Refill spool or load matching material."
                )
                return

            # Step 6: Match found - close prompt and execute automatic swap
            self.gcode.respond_info(
                f"ACE: Endless spool match found: T{tool_index} → T{next_tool}"
            )

            # Close prompt before auto-swap (since we're handling it automatically)
            self.gcode.run_script_from_command(
                'RESPOND TYPE=command MSG="action:prompt_end"'
            )

            self.endless_spool.execute_swap(tool_index, next_tool)

        except Exception as e:
            self.gcode.respond_info(f"ACE: Runout handling error: {e}")
        finally:
            self.runout_handling_in_progress = False

    def _pause_for_runout(self):
        """
        Pause the print for runout handling.

        Uses Klipper's PAUSE command to stop the print and move
        toolhead to safe position.
        """
        try:
            self.gcode.respond_info("ACE: Pausing print")
            self.gcode.run_script_from_command("PAUSE")
        except Exception as e:
            self.gcode.respond_info(f"ACE: Error pausing print: {e}")
