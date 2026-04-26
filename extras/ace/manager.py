from .config import (
    ACE_INSTANCES,
    INSTANCE_MANAGERS,
    SLOTS_PER_ACE,
    SENSOR_TOOLHEAD,
    SENSOR_RDM,
    FILAMENT_STATE_SPLITTER,
    FILAMENT_STATE_BOWDEN,
    FILAMENT_STATE_NOZZLE,
    FILAMENT_STATE_TOOLHEAD,
    OVERRIDABLE_PARAMS,
    CHOICE_OVERRIDABLE_PARAMS,
    get_instance_from_tool,
    get_local_slot,
    get_tool_offset,
    get_ace_instance_and_slot_for_tool,
    parse_instance_config,
    parse_instance_baud_config,
    parse_instance_choice_config,
    create_inventory,
)
from .persistent_state import PersistentState

from .instance import AceInstance
from .ace2_bus import Ace2BusSession
from .endless_spool import EndlessSpool
from .runout_monitor import RunoutMonitor
from .moonraker_lane_sync import MoonrakerLaneSyncAdapter
from . import commands
from .config import read_ace_config
from .protocol import create_protocol_adapter, resolve_protocol_name
from .serial_manager import AceSerialManager
import logging
import serial
import time


class FilamentTrackerAdapter:
    """Thin shim that exposes a filament_tracker's RunoutHelper.

    The ACE manager stores sensors[SENSOR_TOOLHEAD] and expects the
    RunoutHelper interface (.filament_present, .sensor_enabled).  For a
    standard filament_switch_sensor we store its .runout_helper directly.
    For a filament_tracker (which now *has* a RunoutHelper) we do the
    same, but keep the full tracker reference so callers can still reach
    encoder-specific data if needed.
    """

    def __init__(self, tracker):
        self._tracker = tracker
        # Expose the embedded RunoutHelper so the manager can treat
        # this object identically to a plain RunoutHelper.
        self.runout_helper = tracker.runout_helper

    # Delegate the two attributes the manager touches directly.
    @property
    def filament_present(self):
        return self.runout_helper.filament_present

    @property
    def sensor_enabled(self):
        return self.runout_helper.sensor_enabled

    @sensor_enabled.setter
    def sensor_enabled(self, value):
        self.runout_helper.sensor_enabled = value

    def is_instantly_clear(self):
        """Instantaneous raw channel state — no absence timeout.

        Returns True when both encoder channels are currently open,
        bypassing the normal absence_timeout delay.  Use during active
        retraction where the caller *knows* filament was recently moving.

        Returns:
            bool: True if the sensor reads clear right now.
            None:  If the underlying tracker does not support this query
                   (graceful fallback — caller should use filament_present).
        """
        if hasattr(self._tracker, 'are_both_channels_open'):
            return self._tracker.are_both_channels_open
        return None


def toolchange_in_progress_guard(method):
    """
    Decorator: Increment/decrement toolchange depth counter.
    Supports nested toolchange operations - flag stays True until all nested calls complete.
    """
    def wrapper(self, *args, **kwargs):
        self._toolchange_depth = getattr(self, '_toolchange_depth', 0) + 1
        self.toolchange_in_progress = True
        try:
            return method(self, *args, **kwargs)
        finally:
            self._toolchange_depth -= 1
            if self._toolchange_depth == 0:
                self.toolchange_in_progress = False
    return wrapper


class AceManager:
    """
    Main orchestrator for multiple ACE Pro units.

    Responsibilities:
    - Create and manage multiple AceInstance objects (1 per ACE unit)
    - Tool mapping: T0-T3 → instance 0, T4-T7 → instance 1, etc.
    - Global filament runout monitoring and endless spool coordination
    - Sensor management and state tracking
    - Tool change coordination including unload/load/cut/store sequences
    - Inventory management with persistent storage
    - Register T<n> tool macro commands
    - Register ACE_* gcode commands
    - Manage lifecycle: startup, printing, shutdown

    DESIGN: Single AceManager creates N AceInstance objects (one per physical ACE unit).
    """
    def __init__(self, config, dummy_ace_count=1):
        """
        Initialize THE AceManager.

        Called ONCE from load_config() with total ACE count.
        Creates all AceInstance objects internally.

        Args:
            config: Klipper config object
        """
        self.config = config
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.ace_config = read_ace_config(config)

        self.toolhead_retraction_speed = float(self.ace_config["toolhead_retraction_speed"])
        self.toolhead_retraction_length = float(self.ace_config["toolhead_retraction_length"])
        self.default_color_change_purge_length = float(self.ace_config["default_color_change_purge_length"])
        self.default_color_change_purge_speed = float(self.ace_config["default_color_change_purge_speed"])
        self.toolchange_purge_length = self.default_color_change_purge_length
        self.toolchange_purge_speed = self.default_color_change_purge_speed
        self.purge_max_chunk_length = float(self.ace_config["purge_max_chunk_length"])
        self.pre_cut_retract_length = float(self.ace_config["pre_cut_retract_length"])
        self.ace_count = self.ace_config["ace_count"]
        self.purge_multiplier = float(self.ace_config.get("purge_multiplier", 1.0))

        if self.ace_count < 1:
            raise config.error(f"ace_count must be >= 1, got {self.ace_count}")

        self.gcode.respond_info(f"ACE: Creating {self.ace_count} instance(s) with single AceManager")

        persistence_mode = self.ace_config.get("persistence_mode", "deferred")
        self.state = PersistentState(self.printer, self.gcode, persistence_mode=persistence_mode)
        self.gcode.respond_info(f"ACE: Persistence mode: {persistence_mode}")
        self.variables = self.state.get_all()
        initial_ace_enabled = bool(self.state.get("ace_global_enabled", True))

        self.gcode.respond_info(
            f"ACE: Initializing with ace_global_enabled={initial_ace_enabled} "
            f"(from saved variables)"
        )

        self._ace_pro_enabled = initial_ace_enabled
        self._shared_transport_contexts = {}

        # Create all AceInstance objects
        self.instances = []
        for instance_num in range(self.ace_count):
            instance_config = self._resolve_instance_config(instance_num)
            protocol = self._create_instance_protocol(instance_config)
            shared_kwargs = self._build_shared_transport_kwargs(
                instance_num,
                instance_config,
                initial_ace_enabled,
                protocol,
            )

            instance = AceInstance(
                instance_num,
                instance_config,
                self.printer,
                ace_enabled=initial_ace_enabled,  # Pass initial state
                protocol=protocol,
                active_protocol_name=instance_config["active_protocol_name"],
                **shared_kwargs,
            )

            self.instances.append(instance)

            # Register in global registry
            ACE_INSTANCES[instance_num] = instance
            INSTANCE_MANAGERS[instance_num] = self

            # Register tool macros for this instance
            self.register_tool_macros(instance_num)

            self.gcode.respond_info(
                f"ACE[{instance_num}]: Loaded instance (T{instance.tool_offset}-T{instance.tool_offset + 3})"
            )

        # Load persisted inventory for all instances
        self._load_all_inventories()

        # Optional adapter for Orca/Moonraker filament sync.
        self._moonraker_lane_sync = MoonrakerLaneSyncAdapter(
            self.gcode, self, self.ace_config
        )

        self._ace_state_timer = None

        # Initialize global filament position
        # Tracks physical filament location: 'bowden', 'splitter',
        # 'toolhead', or 'nozzle'
        if self.state.get("ace_filament_pos") is None:
            self.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)

        self.ace_pin = self.printer.lookup_object("output_pin ACE_Pro")

        self.sensors = {}
        self._prev_sensors_enabled_state = {}

        # Create endless spool handler (passing self for sensor access)
        self.endless_spool = EndlessSpool(self.printer, self.gcode, self)

        # Create runout monitor (passing self for sensor queries)
        self.runout_monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self,  # Pass manager for sensor access and state
            runout_debounce_count=self.ace_config.get("runout_debounce_count", 1),
            tangle_detection=self.ace_config.get("tangle_detection", False),
            tangle_detection_length=self.ace_config.get("tangle_detection_length", 15.0)
        )

        self.toolchange_in_progress = False

        # Expose manager state for Moonraker/KlipperScreen JSON-RPC queries
        # (distinct from per-instance printer objects).
        try:
            self.printer.add_object("ace_state", self)
        except Exception:
            # Non-fatal; fall back to per-instance status only.
            pass

        # Connection health monitoring state
        self._connection_supervision_enabled = self.ace_config.get(
            "ace_connection_supervision", True
        )
        self._connection_issue_shown = False  # Track if dialog is currently shown
        self._last_connection_status = {}     # Track per-instance connection state
        self._shared_bus_last_connected_time = {}
        self._shared_bus_retry_timers = {}
        self._shared_bus_retry_delays = {}
        self._shared_bus_retry_min_delay = 2.0
        self._shared_bus_retry_max_delay = 15.0

        # Register event handlers
        handler = self.printer.register_event_handler
        handler("klippy:ready", self._handle_ready)
        handler("klippy:disconnect", self._handle_disconnect)
        handler("klippy:shutdown", self._handle_shutdown)

    def _get_config_for_tool(self, tool_index, param_name):
        """
        Get config value for a specific tool (resolves to correct instance).

        Args:
            tool_index: Global tool index (e.g., 0-11)
            param_name: Config parameter name

        Returns:
            Config value for the instance managing this tool

        Raises:
            Exception if tool_index is invalid or param not found
        """
        instance_num = get_instance_from_tool(tool_index)
        if instance_num < 0 or instance_num >= len(self.instances):
            raise Exception(f"Invalid tool index {tool_index}")

        instance = self.instances[instance_num]

        # Access instance's resolved config
        if not hasattr(instance, param_name):
            raise Exception(f"Config parameter '{param_name}' not found for instance {instance_num}")

        return getattr(instance, param_name)

    def get_printer(self):
        """Get the printer object (Klipper API)."""
        return self.printer

    # ========== Lifecycle ==========

    def _handle_ready(self):
        """
        Called when Klipper is ready.
        Sets up toolhead reference, connects ACE instances, initializes sensors, starts monitoring.
        """

        # Set toolhead on all instances
        toolhead = self.printer.lookup_object("toolhead")
        for instance in self.instances:
            instance.toolhead = toolhead

        self.gcode.respond_info(
            f"ACE: Syncing virtual pin to saved state: {self._ace_pro_enabled}"
        )
        pin_value = 1.0 if self._ace_pro_enabled else 0.0
        self.gcode.run_script_from_command(f"SET_PIN PIN=ACE_Pro VALUE={pin_value}")

        if self._ace_pro_enabled:
            for instance in self._iter_unique_transport_instances():
                instance.serial_mgr.connect_to_ace(instance.baud, 2)
                if instance.bus_session is not None and instance.serial_mgr._port:
                    instance.bus_session.port = instance.serial_mgr._port
            self._setup_sensors()
        else:
            self.gcode.respond_info("ACE: ACE Pro disabled on startup - skipping connections")

        # Validate persisted tool state against live sensor readings.
        # Catches stale state from manual filament removal while powered off.
        # self._validate_startup_tool_state()  # disabled pending timing-free rewrite

        # Publish initial lane_data snapshot for Orca pull-mode sync.
        self._sync_moonraker_lane_data(force=True, reason="klippy_ready")

        self._start_monitoring()

    def _handle_shutdown(self):
        """Called on Klipper emergency stop or fatal shutdown.

        Uses ``flush_direct()`` to write directly to ``saved_variables.cfg``
        without going through the GCode queue, which is unavailable (or
        rejected with "Printer is shutdown") at this point.
        """
        pending = list(self.state._dirty)
        logging.info(
            "ACE: klippy:shutdown received — flushing %d dirty variable(s) directly: %s",
            len(pending), pending
        )
        try:
            self.state.flush_direct()
        except Exception:
            logging.exception("ACE: Failed to flush state on shutdown")

    def _handle_disconnect(self):
        """Called on Klipper disconnect. Stops monitoring and disconnects all ACE instances."""
        self.gcode.respond_info("ACE: Disconnecting")

        # Flush any dirty persistent state to disk before we tear down.
        try:
            self.state.flush()
        except Exception:
            logging.exception("ACE: Failed to flush state on disconnect")

        for instance in self._iter_unique_transport_instances():
            instance.serial_mgr.disconnect()

        self._stop_monitoring()
        self._restore_sensors()
        adapter = getattr(self, "_moonraker_lane_sync", None)
        if adapter:
            try:
                adapter.shutdown()
            except Exception as e:
                logging.warning("ACE: Moonraker lane sync shutdown failed: %s", e)

    def _validate_startup_tool_state(self):
        """Validate persisted tool state against live sensor readings at startup.

        If ``ace_current_index`` indicates a tool is loaded and
        ``ace_filament_pos`` is not ``"bowden"``, but **all** filament
        sensors report clear and the printer is idle, the persisted state
        is stale (e.g. user manually removed filament while powered off).

        In that case, reset both variables so that the next toolchange
        does not attempt a phantom retraction on a tool that is no longer
        physically loaded.

        .. note:: Klipper's ``RunoutHelper`` initialises ``filament_present``
            to ``False`` and only updates it once the MCU button-state
            callbacks have completed — two asynchronous reactor hops after
            the MCU reports the initial pin state.  Reading the sensors
            *without* first yielding to the reactor can therefore produce a
            false "all-clear" result even when filament is physically present.
            ``reactor.pause()`` is called below specifically to drain those
            pending callbacks before the sensor values are read.
        """
        current_index = self.state.get("ace_current_index", -1)
        filament_pos = self.state.get("ace_filament_pos", FILAMENT_STATE_BOWDEN)

        # Nothing to validate when no tool is recorded as loaded.
        if current_index < 0:
            return

        # Position already "bowden" means state considers filament retracted.
        if filament_pos == FILAMENT_STATE_BOWDEN:
            return

        # Do not touch state during an active or paused print — the
        # persisted values may be intentionally set by the print flow.
        try:
            print_stats = self.printer.lookup_object("print_stats", None)
            if print_stats:
                stats = print_stats.get_status(self.reactor.monotonic())
                state = (stats.get("state") or "").lower()
                if state in ("printing", "paused"):
                    self.gcode.respond_info(
                        f"ACE: Startup validation skipped — printer is {state}"
                    )
                    return
        except Exception:
            pass  # If print_stats unavailable, assume idle.

        # Sensors may not be set up (ACE Pro disabled).  When no sensors
        # are registered we cannot validate, so bail out.
        if SENSOR_TOOLHEAD not in self.sensors:
            return

        # Klipper's RunoutHelper initialises filament_present=False and updates
        # it through two layers of reactor async callbacks:
        #   MCU → register_async_callback → DebounceButton.button_handler
        #       → register_callback(_debounce_event) → note_filament_present
        # Both hops must complete before we read the sensors.  At klippy:ready
        # time those callbacks may still be queued.  reactor.pause() yields
        # back to the reactor loop for 0.5 s, which is more than enough for
        # any pending MCU button-state callbacks to drain and update
        # filament_present before we read it.
        self.reactor.pause(self.reactor.monotonic() + 0.5)

        toolhead_has_filament = self.get_switch_state(SENSOR_TOOLHEAD)
        rdm_has_filament = (
            self.get_switch_state(SENSOR_RDM)
            if self.has_rdm_sensor()
            else False
        )

        if toolhead_has_filament or rdm_has_filament:
            # At least one sensor confirms filament — state looks plausible.
            self.gcode.respond_info(
                f"ACE: Startup validation — T{current_index} state "
                f"(filament_pos='{filament_pos}') confirmed by sensors "
                f"(toolhead={'present' if toolhead_has_filament else 'clear'}, "
                f"rdm={'present' if rdm_has_filament else 'clear'})"
            )
            return

        # All sensors are clear while state claims a tool is loaded.
        self.gcode.respond_info(
            f"ACE: \u26a0 STARTUP VALIDATION — Saved state indicated "
            f"T{current_index} was loaded (filament_pos='{filament_pos}'), "
            f"but ALL filament sensors report CLEAR and printer is idle. "
            f"Resetting ace_current_index to -1 and ace_filament_pos to "
            f"'bowden'. (Likely cause: filament was manually removed "
            f"while printer was off)"
        )
        self.state.set_and_save("ace_current_index", -1)
        self.state.set_and_save("ace_filament_pos", FILAMENT_STATE_BOWDEN)

    def _setup_sensors(self):
        """
        Register shared sensor access (done ONCE).

        All instances share the same sensors (toolhead + optional RDM).
        Manager owns the sensors, not instances.

        Toolhead sensor: looked up by the configured name in two forms:
            1. filament_switch_sensor <name>  (standard Klipper sensor)
            2. filament_tracker <name>        (encoder-based tracker)
        No implicit fallbacks — the name must match a section in printer.cfg.
        """
        instance = self.instances[0]

        # --- Toolhead sensor ---
        toolhead_sensor_name = instance.filament_runout_sensor_name_nozzle
        toolhead_resolved = False

        # Try standard filament_switch_sensor <name>
        try:
            toolhead_sensor = self.printer.lookup_object(
                f"filament_switch_sensor {toolhead_sensor_name}")
            self.sensors[SENSOR_TOOLHEAD] = toolhead_sensor.runout_helper
            self._prev_sensors_enabled_state[SENSOR_TOOLHEAD] = (
                toolhead_sensor.runout_helper.sensor_enabled)
            toolhead_resolved = True
            self.gcode.respond_info(
                f"ACE: Toolhead sensor '{toolhead_sensor_name}' "
                f"(filament_switch_sensor)")
        except Exception:
            pass

        # Try filament_tracker <name>
        if not toolhead_resolved:
            try:
                tracker = self.printer.lookup_object(
                    f"filament_tracker {toolhead_sensor_name}")
                adapter = FilamentTrackerAdapter(tracker)
                self.sensors[SENSOR_TOOLHEAD] = adapter
                self._prev_sensors_enabled_state[SENSOR_TOOLHEAD] = (
                    adapter.sensor_enabled)
                toolhead_resolved = True
                self.gcode.respond_info(
                    f"ACE: Toolhead sensor '{toolhead_sensor_name}' "
                    f"(filament_tracker)")
            except Exception:
                pass

        if not toolhead_resolved:
            self.gcode.respond_info(
                f"ACE: ERROR - No toolhead sensor '{toolhead_sensor_name}' "
                f"found in printer.cfg (tried [filament_switch_sensor "
                f"{toolhead_sensor_name}] and [filament_tracker "
                f"{toolhead_sensor_name}])")
            raise self.config.error(
                f"Missing sensor '{toolhead_sensor_name}' in printer.cfg. "
                f"Add [filament_switch_sensor {toolhead_sensor_name}] or "
                f"[filament_tracker {toolhead_sensor_name}].")

        # --- RDM sensor (optional) ---
        if instance.filament_runout_sensor_name_rdm is not None:
            rms_sensor_name = instance.filament_runout_sensor_name_rdm
            rdm_resolved = False

            # Try standard filament_switch_sensor <name>
            try:
                rms_sensor = self.printer.lookup_object(
                    f"filament_switch_sensor {rms_sensor_name}")
                self.sensors[SENSOR_RDM] = rms_sensor.runout_helper
                self._prev_sensors_enabled_state[SENSOR_RDM] = (
                    rms_sensor.runout_helper.sensor_enabled)
                rdm_resolved = True
                self.gcode.respond_info(
                    f"ACE: RDM sensor '{rms_sensor_name}' "
                    f"(filament_switch_sensor)")
            except Exception:
                pass

            # Try filament_tracker <name>
            if not rdm_resolved:
                try:
                    rdm_tracker = self.printer.lookup_object(
                        f"filament_tracker {rms_sensor_name}")
                    adapter = FilamentTrackerAdapter(rdm_tracker)
                    self.sensors[SENSOR_RDM] = adapter
                    self._prev_sensors_enabled_state[SENSOR_RDM] = (
                        adapter.sensor_enabled)
                    rdm_resolved = True
                    self.gcode.respond_info(
                        f"ACE: RDM sensor '{rms_sensor_name}' "
                        f"(filament_tracker)")
                except Exception:
                    pass

            if not rdm_resolved:
                self.gcode.respond_info(
                    f"ACE: WARNING - No RDM sensor '{rms_sensor_name}' "
                    f"found (tried [filament_switch_sensor "
                    f"{rms_sensor_name}] and [filament_tracker "
                    f"{rms_sensor_name}]). "
                    f"No RDM consistency check will be performed.")

        # Disable standard runout detection
        self._disable_all_sensor_detection()

    def _disable_all_sensor_detection(self):
        """Disable automatic pause for all sensors."""
        for name, sensor in self.sensors.items():
            if sensor.sensor_enabled:
                self.gcode.respond_info(f"ACE: Disabling runout detection for {name}")
                sensor.sensor_enabled = False

    def _restore_sensors(self):
        """Restore original sensor state."""
        for name, sensor in self.sensors.items():
            if name in self._prev_sensors_enabled_state:
                prev_state = self._prev_sensors_enabled_state[name]
                sensor.sensor_enabled = prev_state
                self.gcode.respond_info(f"ACE: Restored sensor {name} to enabled={prev_state}")

    # ========== Sensor Query Methods ==========

    def get_switch_state(self, sensor_name):
        """
        Get sensor state directly from Klipper.

        Supports sensor state injection for testing (via _sensor_override).

        Args:
            sensor_name: SENSOR_TOOLHEAD or SENSOR_RDM

        Returns:
            bool: True if filament is present (sensor triggered)
        """
        # Check for injected override (for testing)
        if hasattr(self, '_sensor_override') and self._sensor_override:
            if sensor_name in self._sensor_override:
                return self._sensor_override[sensor_name]

        if sensor_name not in self.sensors:
            return False

        sensor = self.sensors[sensor_name]
        return bool(sensor.filament_present)

    def get_instant_switch_state(self, sensor_name):
        """Get sensor state using instantaneous raw channel reading.

        Like :meth:`get_switch_state`, but bypasses the absence_timeout
        delay on filament_tracker sensors by reading the raw channel state.
        Falls back to the normal debounced ``filament_present`` when the
        sensor does not support instantaneous queries (e.g. a plain
        filament_switch_sensor).

        Use this during active retraction / unloading where the caller
        knows filament was recently moving and needs the fastest possible
        response.

        Args:
            sensor_name: SENSOR_TOOLHEAD or SENSOR_RDM

        Returns:
            bool: True if filament is present (sensor triggered)
        """
        # Check for injected override (for testing)
        if hasattr(self, '_sensor_override') and self._sensor_override:
            if sensor_name in self._sensor_override:
                return self._sensor_override[sensor_name]

        if sensor_name not in self.sensors:
            return False

        sensor = self.sensors[sensor_name]
        if hasattr(sensor, 'is_instantly_clear'):
            instant = sensor.is_instantly_clear()
            if instant is not None:
                return not instant  # clear=True means filament absent
        return bool(sensor.filament_present)

    def is_filament_path_free_instant(self):
        """Check if filament path is clear using instantaneous sensor reads.

        Same as :meth:`is_filament_path_free` but uses
        :meth:`get_instant_switch_state` for faster response during
        active retraction.

        Returns:
            bool: True if path is clear (no filament detected)
        """
        toolhead_blocked = self.get_instant_switch_state(SENSOR_TOOLHEAD)

        if self.has_rdm_sensor():
            rdm_blocked = self.get_instant_switch_state(SENSOR_RDM)
            return not (toolhead_blocked or rdm_blocked)
        else:
            return not toolhead_blocked

    def is_filament_path_free(self):
        """
        Check if filament path is clear.

        If RDM sensor available: checks both toolhead + RDM
        If RDM unavailable: checks only toolhead

        Returns:
            bool: True if path is clear (no filament detected)
        """
        toolhead_blocked = self.get_switch_state(SENSOR_TOOLHEAD)

        if self.has_rdm_sensor():
            rdm_blocked = self.get_switch_state(SENSOR_RDM)
            return not (toolhead_blocked or rdm_blocked)
        else:
            # RDM not available - check only toolhead
            return not toolhead_blocked

    def prepare_toolhead_for_filament_retraction(self, tool_index=-1):
        """
        Prepare toolhead (extruder/nozzle) for filament retraction.

        If filament is present at toolhead (sensor triggered):
        1. Call _ACE_PREPARE_FOR_RETRACTION macro (macro handles heating)

        Args:
            tool_index: Tool to prepare for retraction (-1 = unknown tool)

        Returns:
            bool: True if filament was present and handling succeeded,
                  False if no filament present or operation completed
        """
        if not self.get_switch_state(SENSOR_TOOLHEAD):
            self.gcode.respond_info("ACE: No filament at toolhead, skipping prep")
            return False

        target_temp = 0
        if tool_index >= 0:
            target_ace, target_slot = get_ace_instance_and_slot_for_tool(tool_index)
            if target_ace is not None:
                inv_temp = target_ace.inventory[target_slot].get("temp", 0)
                if inv_temp > 0:
                    target_temp = inv_temp
                    self.gcode.respond_info(
                        f"ACE: Using inventory temp for T{tool_index}: {target_temp}°C"
                    )

        self.gcode.respond_info(
            f"ACE: Filament at toolhead, preparing for retraction "
            f"(target_temp={target_temp}°C) pre_cut_retract={self.pre_cut_retract_length}mm"
        )

        try:
            # Call macro to handle heating + CUT_TIP
            self.gcode.run_script_from_command(
                f"_ACE_PREPARE_FOR_RETRACTION TARGET_TEMP={target_temp} PRE_CUT_RETRACT={self.pre_cut_retract_length}"
            )
            return True

        except Exception as e:
            self.gcode.respond_info(f"ACE: Error preparing toolhead for retraction: {e}")
            return False

    def execute_coordinated_retraction(self, retract_length, retract_speed, retract_speed_mmmin, current_tool):
        """
        Perform coordinated retraction of ACE and extruder.

        Waits for ACE motion to fully complete before returning, ensuring
        sensor readings are accurate after retraction.

        Args:
            retract_length: Length to retract (mm)
            retract_speed: ACE retraction speed (mm/s)
            retract_speed_mmmin: Extruder retraction speed (mm/min)
            current_tool: Tool index to retract
        """
        instance_num = get_instance_from_tool(current_tool)
        if instance_num >= 0:
            ace_inst = self.instances[instance_num]
            local_slot = get_local_slot(current_tool, instance_num)

            self.gcode.respond_info(
                f"ACE: Synchronized retraction: extruder + ACE[{instance_num}] slot {local_slot}, "
                f"{retract_length:.2f}mm at {retract_speed:.2f}mm/s"
            )

            # Disable feed assist BEFORE retraction — feed assist pushes
            # filament forward, which fights the retraction and can cause jams.
            if ace_inst._feed_assist_index == local_slot:
                self.gcode.respond_info(
                    f"ACE[{instance_num}]: Disabling feed assist on slot {local_slot} before retraction"
                )
                ace_inst._disable_feed_assist(local_slot)

            ace_inst.wait_ready()
            ace_inst._retract(local_slot, length=retract_length, speed=retract_speed)

            self.gcode.run_script_from_command("M83")  # Relative extrusion
            self.gcode.run_script_from_command(f"G1 E-{retract_length} F{retract_speed_mmmin}")

            ace_inst.wait_ready()
            motion_time = retract_length / retract_speed
            safety_margin = 1.0  # 1 second extra
            total_wait_time = motion_time + safety_margin

            self.gcode.respond_info(
                f"ACE[{instance_num}]: Waiting {total_wait_time:.1f}s for retraction to complete "
                f"({motion_time:.1f}s motion + {safety_margin:.1f}s margin)"
            )
            ace_inst.dwell(total_wait_time)

            max_status_wait = 5.0  # Max 5 seconds to wait for status update
            status_check_start = self.reactor.monotonic()

            while True:
                slot_status = ace_inst.inventory[local_slot].get("status", "unknown")
                if slot_status == "ready":
                    self.gcode.respond_info(
                        f"ACE[{instance_num}]: Slot {local_slot} confirmed ready after retraction"
                    )
                    break

                elapsed = self.reactor.monotonic() - status_check_start
                if elapsed > max_status_wait:
                    self.gcode.respond_info(
                        f"ACE[{instance_num}]: WARNING - Slot {local_slot} status still '{slot_status}' "
                        f"after {elapsed:.1f}s (expected 'ready')"
                    )
                    break

                self.reactor.pause(self.reactor.monotonic() + 0.1)

            self.gcode.run_script_from_command("G92 E0")  # Reset extruder position
            self.gcode.respond_info(
                f"ACE[M]: CUT DONE + retraction {retract_length}mm"
            )
        else:
            self.gcode.respond_info(
                f"ACE: Warning - current tool {current_tool} has no instance, "
                f"cannot coordinate ACE retraction"
            )

    def _wait_toolhead_move_finished(self):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()

    def _extruder_move(self, length, speed, wait_for_move_end=False):
        """Move extruder (relative) via motion planner, synchronously."""
        if length == 0:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: _extruder_move() -> Skipping zero-length move"
            )
            return

        toolhead = self.printer.lookup_object('toolhead')
        cur_pos = list(toolhead.get_position())  # [X, Y, Z, E]

        new_pos = cur_pos[:]
        new_pos[3] += length

        toolhead.move(new_pos, speed)
        if wait_for_move_end:
            toolhead.wait_moves()

    @toolchange_in_progress_guard
    def smart_unload(self, tool_index=-1, prepare_toolhead=True):
        """
        Unload with slot cycling when tool is unknown.

        USE CASE for cycling:
        - Current tool is unknown (-1)
        - Toolhead sensor is triggered
        - Need to identify which tool is loaded

        ALL OTHER CASES: Direct unload or fail with error.
        """
        current_tool_index = self.state.get("ace_current_index", -1)

        self.gcode.respond_info(f"ACE: Smart unload tool {tool_index} (current: {current_tool_index})")

        tool_for_temp = tool_index if tool_index >= 0 else current_tool_index
        if prepare_toolhead:
            self.gcode.respond_info("ACE: Preparing toolhead")
            self.prepare_toolhead_for_filament_retraction(tool_index=tool_for_temp)

        retract_length = self.toolhead_retraction_length
        retract_speed = self.toolhead_retraction_speed

        # ===== CASE 1: Tool is known - direct unload =====
        if tool_index >= 0:
            instance_num = get_instance_from_tool(tool_index)
            if instance_num < 0:
                raise Exception(f"Tool {tool_index} not managed by any ACE instance")

            instance = self.instances[instance_num]
            local_slot = get_local_slot(tool_index, instance_num)
            slot_status = instance.inventory[local_slot].get("status", "empty")

            # Check if slot is empty BEFORE attempting unload
            if slot_status == "empty":
                raise Exception(
                    f"Cannot unload T{tool_index} - ACE slot {local_slot} is EMPTY.\n"
                )

            # Sensor already clear - choose retract distance based on path state
            if not self.get_instant_switch_state(SENSOR_TOOLHEAD):
                if self.is_filament_path_free_instant():
                    # All sensors clear: filament was likely manually removed.
                    # A short safety retract is enough to pull back any tip sitting
                    # just inside the ACE hub above the sensor boundary.
                    param = "parkposition_to_rdm_length" if self.has_rdm_sensor() \
                        else "parkposition_to_toolhead_length"
                    retract_dist = self._get_config_for_tool(tool_index, param)
                    self.gcode.respond_info(
                        f"ACE: Filament path fully free for T{tool_index} "
                        f"(filament may have been manually removed) - "
                        f"short safety retract of {retract_dist}mm"
                    )
                else:
                    # Toolhead clear but RDM still triggered: full retract needed.
                    retract_dist = self._get_config_for_tool(
                        tool_index, "parkposition_to_toolhead_length"
                    )
                    self.gcode.respond_info(
                        f"ACE: Toolhead clear, RDM triggered - full retract of T{tool_index} ({retract_dist}mm)"
                    )

                instance._smart_unload_slot(local_slot, length=retract_dist)

                if self.is_filament_path_free_instant():
                    self.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
                    self.gcode.respond_info(f"ACE: Tool {tool_index} unloaded successfully")
                    return True
                else:
                    raise Exception(f"Path still blocked after unload of T{tool_index}")

            # Sensor triggered - coordinated retraction
            try:
                instance.wait_ready()
                parkposition_to_toolhead_length = self._get_config_for_tool(
                    tool_index, "parkposition_to_toolhead_length"
                )

                # Disable feed assist BEFORE any motion — feed assist pushes
                # filament forward and would fight both the extruder retract
                # and the ACE retract that follow.
                if instance._feed_assist_index == local_slot:
                    self.gcode.respond_info(
                        f"ACE: Disabling feed assist on slot {local_slot} before coordinated retract"
                    )
                    instance._disable_feed_assist(local_slot)

                self.gcode.respond_info(
                    f"ACE: Retracting T{tool_index} "
                    f"({retract_length:.3f}mm at {retract_speed:.3f}mm/s)"
                )

                # Start extruder retraction (10% faster for slack)
                self._extruder_move(-abs(retract_length), retract_speed * 1.10, wait_for_move_end=False)

                # Start ACE retraction
                unload_ok = instance._smart_unload_slot(
                    local_slot,
                    length=parkposition_to_toolhead_length + retract_length,
                )

                # Wait for extruder to finish
                self._wait_toolhead_move_finished()

                if unload_ok and self.is_filament_path_free_instant():
                    self.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
                    self.gcode.respond_info(f"ACE: Tool {tool_index} unloaded successfully")
                    return True
                else:
                    raise Exception(f"Unload failed for T{tool_index}")

            except Exception as e:
                self.gcode.respond_info(f"ACE: Error during unload: {e}")
                raise
            finally:
                self.gcode.run_script_from_command("G92 E0")
                self.gcode.run_script_from_command("G90")

        # ===== CASE 2: Given toolindex is set to unknown
        # + any sensor triggered (toolhead or RDM) => CYCLE TO IDENTIFY =====
        toolhead_triggered = self.get_switch_state(SENSOR_TOOLHEAD)
        rdm_triggered = self.get_switch_state(SENSOR_RDM) if self.has_rdm_sensor() else False

        # If any sensor is triggered, we need to cycle to identify the tool,
        # we start with cycling with current_tool_index
        if toolhead_triggered or rdm_triggered:
            sensor_desc = "toolhead" if toolhead_triggered else "RDM"
            self.gcode.respond_info(
                f"ACE: Current tool unknown but {sensor_desc} sensor triggered - cycling slots to identify loaded tool"
            )

            # Distances for completing unload after identification
            park_to_toolhead_len = self._get_config_for_tool(
                0, "parkposition_to_toolhead_length"
            )
            park_to_rdm_len = (
                self._get_config_for_tool(0, "parkposition_to_rdm_length")
                if self.has_rdm_sensor() else park_to_toolhead_len
            )

            retract_speed_mmmin = retract_speed * 60

            # Use unified cycling that also handles RDM-only trigger
            full_unload_length = (
                park_to_rdm_len
                if (rdm_triggered and not toolhead_triggered and self.has_rdm_sensor())
                else park_to_toolhead_len
            )

            return self._identify_and_unload_by_cycling(
                current_tool_index,
                tool_index,
                retract_length,
                retract_speed,
                retract_speed_mmmin,
                full_unload_length
            )
        else:
            self.gcode.respond_info(
                "ACE: Not cycling - no sensor triggered")

        # ===== Normal case if no tool was loaded, nothing to do here
        if current_tool_index == -1 and not (toolhead_triggered or rdm_triggered):
            self.gcode.respond_info(
                "ACE: No tool loaded and sensor clear - nothing to unload"
            )
            self.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
            return True

        if current_tool_index >= 0 and not (toolhead_triggered or rdm_triggered):
            self.gcode.respond_info(
                f"ACE: Unplausible state in smart_unload detected. "
                f"Current_tool_index={current_tool_index} but sensor clear - "
                f"assuming already unloaded, updating state accordingly."
            )
            self.state.set("ace_current_index", -1)
            self.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
            return True

        # ===== Something is strange... Shouldn't reach here =====
        self.gcode.respond_info(
            f"ACE: Invalid state: current_tool_index={current_tool_index} "
            f"tool_index={tool_index} toolhead_triggered={toolhead_triggered} "
            f"rdm_triggered={rdm_triggered}"
        )
        raise Exception("Unexpected state in smart_unload")

    def _identify_and_unload_by_cycling(
        self,
        current_tool_index,
        attempted_tool_index,
        retract_length,
        retract_speed,
        retract_speed_mmmin,
        full_unload_length
    ):
        """
        Identify loaded tool with three-case sensor strategy.

        CASE 1: No sensors triggered → path clear, no unload needed
        CASE 2: Toolhead sensor triggered → cycle with extruder retractions
        CASE 3: RDM triggered (toolhead clear) → cycle with RDM monitoring
        """

        toolhead_triggered = self.get_switch_state(SENSOR_TOOLHEAD)
        rdm_triggered = self.get_switch_state(SENSOR_RDM) if self.has_rdm_sensor() else False

        # CASE 1: No sensors triggered - path is clear
        if not toolhead_triggered and not rdm_triggered:
            self.gcode.respond_info(
                "ACE: No sensors triggered - path clear, no unload needed"
            )
            self.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
            return True

        # CASE 2: Toolhead sensor triggered - cycle with extruder retractions to identify tool
        if toolhead_triggered:
            self.gcode.respond_info(
                f"ACE: Toolhead sensor triggered - cycling slots with test retractions "
                f"({retract_length}mm at {retract_speed}mm/s) to identify loaded tool"
            )

            # Use existing cycling logic
            return self._cycle_slots_with_sensor_check(
                current_tool_index,
                attempted_tool_index,
                retract_length,
                retract_speed,
                retract_speed_mmmin,
                full_unload_length,
                sensor_name=SENSOR_TOOLHEAD,
                use_extruder=True
            )

        # CASE 3: RDM triggered but toolhead clear - monitor RDM during ACE-only retraction
        if rdm_triggered and self.has_rdm_sensor():
            # Get RDM-specific config
            tool_for_config = attempted_tool_index if attempted_tool_index >= 0 else current_tool_index
            if tool_for_config < 0:
                parkposition_to_rdm_length = self.instances[0].parkposition_to_rdm_length
                parkposition_to_toolhead_length = self.instances[0].parkposition_to_toolhead_length
                rdm_retract_speed = self.instances[0].feed_speed  # Use faster feed_speed for long RDM retraction
            else:
                parkposition_to_rdm_length = self._get_config_for_tool(
                    tool_for_config, "parkposition_to_rdm_length"
                )
                parkposition_to_toolhead_length = self._get_config_for_tool(
                    tool_for_config, "parkposition_to_toolhead_length"
                )

                rdm_retract_speed = self._get_config_for_tool(
                    tool_for_config, "feed_speed"
                )
            # Filament could be just before toolhead - ensure full unload length covers toolhead to rdm sensor
            full_unload_length = parkposition_to_toolhead_length

            self.gcode.respond_info(
                "ACE: RDM sensor triggered but toolhead sensor not - "
                f"Retracting and monitoring RDM sensor during ACE-only retraction "
                f"({full_unload_length}mm at {rdm_retract_speed}mm/s)"
            )

            return self._cycle_slots_with_sensor_check(
                current_tool_index,
                attempted_tool_index,
                retract_length,
                rdm_retract_speed,  # Use feed_speed instead of retract_speed
                retract_speed_mmmin,
                full_unload_length,
                sensor_name=SENSOR_RDM,
                use_extruder=False,
                sensor_to_parking_length=parkposition_to_rdm_length
            )

        # Should never reach here
        self.gcode.respond_info("ACE: Unexpected sensor state in cycling")
        return False

    def _cycle_slots_with_sensor_check(
        self,
        current_tool_index,
        attempted_tool_index,
        retract_length,
        retract_speed,
        retract_speed_mmmin,
        full_unload_length,
        sensor_name,
        use_extruder,
        sensor_to_parking_length=None
    ):
        """
        Unified slot cycling with sensor monitoring.

        Args:
            current_tool_index: Current tool (-1 if unknown)
            attempted_tool_index: Target tool for direct attempt (-1 for full cycle)
            retract_length: Test retraction length (mm)
            retract_speed: ACE retraction speed (mm/s)
            retract_speed_mmmin: Extruder retraction speed (mm/min)
            full_unload_length: Total length to max. unload
            sensor_name: SENSOR_TOOLHEAD or SENSOR_RDM
            use_extruder: If True, use coordinated extruder+ACE retractions
                        If False, use ACE-only with sensor monitoring
            sensor_to_parking_length: Distance from sensor to parking position (for RDM mode)

        Returns:
            bool: True if tool identified and unloaded successfully
        """

        # Build slot list (prioritize current_tool if different from attempted)
        slots_to_try = []
        if current_tool_index >= 0 and current_tool_index != attempted_tool_index:
            start_instance_num = get_instance_from_tool(current_tool_index)
            start_slot = get_local_slot(current_tool_index, start_instance_num)
            if start_instance_num >= 0 and 0 <= start_slot < self.instances[start_instance_num].SLOT_COUNT:
                start_instance = self.instances[start_instance_num]
                start_slot_status = start_instance.inventory[start_slot].get("status", "empty")
                if start_slot_status == "empty":
                    self.gcode.respond_info(
                        f"ACE[{start_instance_num}]: Skipping prioritized current tool "
                        f"slot {start_slot} (T{current_tool_index}) - empty"
                    )
                else:
                    slots_to_try.append((start_instance_num, start_slot, current_tool_index))

        # Add all other non-empty slots
        for instance_num, instance in enumerate(self.instances):
            for slot in range(instance.SLOT_COUNT):
                tool_num = instance.tool_offset + slot
                if (instance_num, slot, tool_num) in slots_to_try:
                    continue
                slot_status = instance.inventory[slot].get("status", "empty")
                if slot_status == "empty":
                    self.gcode.respond_info(
                        f"ACE[{instance_num}]: Skipping slot {slot} (T{tool_num}) - empty"
                    )
                    continue
                slots_to_try.append((instance_num, slot, tool_num))

        # Cycle and test each slot
        identified_tool = None

        for instance_num, slot, tool_num in slots_to_try:
            self.gcode.respond_info(
                f"ACE[{instance_num}]: Testing slot {slot} (T{tool_num}) via {sensor_name}"
            )

            instance = self.instances[instance_num]

            try:
                if use_extruder:
                    # CASE 2: Coordinated extruder+ACE retraction
                    self.execute_coordinated_retraction(
                        retract_length, retract_speed, retract_speed_mmmin, tool_num
                    )

                    # Wait for motion to settle
                    settle_time = max(0.2, min((retract_length / retract_speed) * 0.1, 1.0))
                    self.reactor.pause(self.reactor.monotonic() + settle_time)

                    # Check sensor with multiple readings for stability
                    sensor_readings = []
                    for i in range(3):
                        sensor_readings.append(self.get_instant_switch_state(sensor_name))
                        if i < 2:
                            self.reactor.pause(self.reactor.monotonic() + 0.1)

                    sensor_state = sensor_readings[-1]
                    self.gcode.respond_info(
                        f"ACE[{instance_num}]: Sensor {sensor_name} after retraction: "
                        f"readings={sensor_readings}, final={'TRIGGERED' if sensor_state else 'CLEAR'}"
                    )

                    if not sensor_state:
                        self.gcode.respond_info(
                            f"ACE[{instance_num}]: ✓ Sensor cleared! T{tool_num} identified"
                        )
                        identified_tool = (instance_num, slot, tool_num)
                        break

                else:
                    # CASE 3: ACE-only retraction with sensor monitoring
                    # Disable feed assist BEFORE retraction — it pushes forward
                    # which fights the retraction.
                    if instance._feed_assist_index == slot:
                        self.gcode.respond_info(
                            f"ACE[{instance_num}]: Disabling feed assist on slot {slot} before retraction"
                        )
                        instance._disable_feed_assist(slot)

                    instance.wait_ready()
                    instance._retract(slot, length=full_unload_length, speed=retract_speed)

                    # Monitor sensor during retraction
                    start_time = self.reactor.monotonic()
                    max_wait = (full_unload_length / retract_speed) + 2.0
                    trigger_time = None
                    delay_after_trigger = sensor_to_parking_length / retract_speed if sensor_to_parking_length else 0.5

                    while True:
                        elapsed = self.reactor.monotonic() - start_time
                        if elapsed > max_wait:
                            self.gcode.respond_info(
                                f"ACE[{instance_num}]: Timeout waiting for {sensor_name} change on slot {slot}"
                            )
                            break

                        sensor_state = self.get_instant_switch_state(sensor_name)

                        # Detect sensor clearing (triggered → clear)
                        if not sensor_state and trigger_time is None:
                            trigger_time = self.reactor.monotonic()
                            self.gcode.respond_info(
                                f"ACE[{instance_num}]: {sensor_name} cleared! "
                                f"Waiting {delay_after_trigger:.2f}s before stopping"
                            )

                        # Wait for delay after trigger
                        if trigger_time is not None:
                            time_since_trigger = self.reactor.monotonic() - trigger_time
                            if time_since_trigger >= delay_after_trigger:
                                self.gcode.respond_info(
                                    f"ACE[{instance_num}]: ✓ T{tool_num} identified via {sensor_name} monitoring"
                                )
                                instance._stop_feed(slot)
                                identified_tool = (instance_num, slot, tool_num)
                                break

                        self.reactor.pause(self.reactor.monotonic() + 0.05)

                    if identified_tool is not None:
                        break

            except Exception as e:
                self.gcode.respond_info(
                    f"ACE[{instance_num}]: Error testing slot {slot} via {sensor_name}: {e}"
                )
                continue

        if identified_tool is None:
            self.gcode.respond_info(f"ACE: Failed to identify loaded tool via {sensor_name}")
            return False

        # Complete unload if using extruder mode (CASE 2)
        if use_extruder:
            instance_num, slot, tool_num = identified_tool
            remaining_length = full_unload_length - retract_length
            instance = self.instances[instance_num]

            try:
                self.gcode.respond_info(
                    f"ACE[{instance_num}]: Completing unload of T{tool_num} "
                    f"(remaining: {remaining_length}mm)"
                )
                instance._smart_unload_slot(slot, length=remaining_length)
            except Exception as e:
                self.gcode.respond_info(f"ACE[{instance_num}]: Error during full unload: {e}")
                return False

        # Verify path is clear
        if self.is_filament_path_free():
            self.state.set(
                "ace_filament_pos", FILAMENT_STATE_BOWDEN
            )
            self.gcode.respond_info(
                f"ACE: Tool {identified_tool[2]} identified and unloaded successfully"
            )
            return True
        else:
            self.gcode.respond_info("ACE: Path still blocked after unload")
            return False

    def smart_load(self):
        """
        Load all non-empty slots to verification sensor.

        If RDM sensor available: feeds to RDM sensor (shorter distance)
        If RDM unavailable: feeds to toolhead sensor (original behavior)

        For each ACE instance and each non-empty slot:
        1. Feed filament to verification sensor (RDM if available, else toolhead)
        2. Verify sensor triggered
        3. Retract to park position
        4. Verify path is clear

        Result: All filament parked at bowden position, ready for tool selection

        Returns:
            bool: True if all slots loaded successfully, False otherwise
        """
        if not self.is_filament_path_free():
            self.gcode.respond_info("ACE: Cannot start smart_load - " "filament path is blocked")
            return False

        # Determine which sensor to use for verification
        use_rdm = self.has_rdm_sensor()
        verification_sensor = SENSOR_RDM if use_rdm else SENSOR_TOOLHEAD
        sensor_name = "RDM" if use_rdm else "toolhead"

        self.gcode.respond_info(
            f"ACE: Smart load using {sensor_name} sensor for verification"
        )

        success_count = 0
        total_slots = 0

        for instance in self.instances:
            # Use toolchange_load_length for feeding (sensor will stop it when reached)
            feed_length = instance.toolchange_load_length

            # For each non-empty slot
            for slot in range(instance.SLOT_COUNT):
                # Check if slot has filament
                slot_status = instance.inventory[slot].get("status", "empty")
                if slot_status == "empty":
                    continue  # Skip empty slots

                total_slots += 1
                tool_num = instance.tool_offset + slot

                self.gcode.respond_info(f"ACE[{instance.instance_num}]: " f"Loading slot {slot} (T{tool_num})")

                try:
                    # Step 1: Feed to verification sensor
                    self.gcode.respond_info(f"ACE: Feeding slot {slot} to {sensor_name} sensor")
                    instance._feed_filament_to_verification_sensor(
                        slot,
                        verification_sensor,
                        feed_length
                    )

                    # Check if verify sensor triggered or not
                    if not self.get_switch_state(verification_sensor):
                        # Failure case
                        self.gcode.respond_info(
                            f"ACE[{instance.instance_num}]: "
                            f"{sensor_name} sensor not triggered after "
                            f"feeding slot {slot}"
                        )
                        instance._stop_feed(slot)

                        # We dont know how far the filament has moved, try retract to park directly to avoid jams
                        if use_rdm:
                            park_distance = instance.parkposition_to_rdm_length
                        else:
                            park_distance = instance.parkposition_to_toolhead_length

                        self.gcode.respond_info(
                            f"ACE: Safety retracting slot {slot} to park position to avoid jams"
                            f"({park_distance}mm)"
                        )
                        instance._retract(slot, length=park_distance, speed=instance.retract_speed)
                        continue

                    # Happy path
                    self.gcode.respond_info(f"ACE: {sensor_name} sensor triggered for slot {slot}")

                    if use_rdm:
                        self.state.set("ace_filament_pos", FILAMENT_STATE_SPLITTER)
                    else:
                        self.state.set("ace_filament_pos", FILAMENT_STATE_TOOLHEAD)

                    # Step 2: Retract to park position
                    # Use appropriate park distance based on sensor
                    if use_rdm:
                        park_distance = instance.parkposition_to_rdm_length
                    else:
                        park_distance = instance.parkposition_to_toolhead_length

                    self.gcode.respond_info(
                        f"ACE: Retracting slot {slot} to park "
                        f"({park_distance}mm from {sensor_name})"
                    )
                    instance._retract(slot, length=park_distance, speed=instance.retract_speed)

                    # Step 3: Verify path is still clear
                    if not self.is_filament_path_free():
                        self.gcode.respond_info(
                            f"ACE[{instance.instance_num}]: " f"Path not clear after parking slot {slot}"
                        )
                        continue

                    self.gcode.respond_info(f"ACE[{instance.instance_num}]: " f"Slot {slot} loaded successfully")
                    success_count += 1

                except Exception as e:
                    self.gcode.respond_info(f"ACE[{instance.instance_num}]: " f"Error loading slot {slot}: {e}")

        if success_count > 0:
            self.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
            self.state.set("ace_current_index", -1)
            self.gcode.respond_info(f"ACE: Smart load complete - {success_count}/{total_slots} " f"slots loaded")
            return success_count == total_slots
        else:
            self.gcode.respond_info("ACE: Smart load - no slots loaded")
            return False

    # ========== Inventory Management (Manager owns persistence) ==========

    def _load_all_inventories(self):
        """
        Load persisted inventory for all instances.

        Called on startup. Manager owns the persistent variables,
        not instances. Instances get their inventory set here.
        """
        for instance in self.instances:
            varname = f"ace_inventory_{instance.instance_num}"
            saved_inv = self.state.get(varname, None)
            if saved_inv:
                # Clean up legacy rgba field from saved inventory
                for slot in saved_inv:
                    slot.pop("rgba", None)
                instance.inventory = saved_inv
                self.gcode.respond_info(f"ACE[{instance.instance_num}]: Loaded persisted inventory")
            else:
                instance.inventory = create_inventory(SLOTS_PER_ACE)
                self.gcode.respond_info(f"ACE[{instance.instance_num}]: " f"Initialized new inventory")

    def _sync_inventory_to_persistent(self, instance_num=None, flush=True):
        """
        Sync instance inventory to persistent storage.

        Manager owns the persistent variables. Instances modify
        their inventory in-memory, then manager persists changes.

        Args:
            instance_num: Specific instance to sync, or None to sync all
            flush: If True (default), call set_and_save() for immediate
                   disk write.  Pass False for mid-print / batch paths
                   where the caller will flush() later.
        """

        if instance_num is not None:
            if instance_num >= len(self.instances):
                self.gcode.respond_info(f"ACE: Invalid instance number {instance_num}")
                return

            instance = self.instances[instance_num]
            varname = f"ace_inventory_{instance_num}"
            if flush:
                self.state.set_and_save(varname, instance.inventory)
            else:
                self.state.set(varname, instance.inventory)
            self._sync_moonraker_lane_data(
                force=False, reason=f"inventory_update_instance_{instance_num}"
            )

            # self.gcode.respond_info(f"ACE[{instance_num}]: Inventory synced to persistent")
        else:
            # Sync all instances
            for inst in self.instances:
                self._sync_inventory_to_persistent(inst.instance_num, flush=flush)

    def _sync_moonraker_lane_data(self, force=False, reason="manual"):
        """Push ACE slot metadata to Moonraker DB lane_data for Orca sync."""
        adapter = getattr(self, "_moonraker_lane_sync", None)
        if not adapter:
            return False
        try:
            return adapter.sync_now(force=force, reason=reason)
        except Exception as e:
            logging.warning("ACE: Moonraker lane sync failed (%s): %s", reason, e)
            return False

    def _start_monitoring(self):
        """Start runout detection monitor loop."""
        self.runout_monitor.start_monitoring()

        self.gcode.respond_info("ACE: Starting ACE support state monitor")
        self._ace_state_timer = self.reactor.register_timer(self._monitor_ace_state, self.reactor.NOW)

    def _stop_monitoring(self):
        """Stop runout monitoring."""
        self.runout_monitor.stop_monitoring()

        # Stop ACE support state monitoring timer
        if hasattr(self, "_ace_state_timer") and self._ace_state_timer:
            try:
                self.reactor.unregister_timer(self._ace_state_timer)
            except Exception:
                pass
            self._ace_state_timer = None

    def set_runout_detection_active(self, active):
        """Enable/disable runout detection (delegates to monitor)."""
        return self.runout_monitor.set_detection_active(active)

    def set_ace_global_enabled(self, enabled):
        """Set global ACE Pro enabled state and persist it."""
        self.state.set_and_save("ace_global_enabled", enabled)
        self._ace_pro_enabled = enabled

    def get_ace_global_enabled(self):
        """Get global ACE Pro enabled state from persistent storage."""
        return bool(self.state.get("ace_global_enabled", True))

    def is_ace_enabled(self):
        """Check if ACE Pro unit is enabled via output pin."""
        try:
            # Get pin status from Klipper
            status = self.ace_pin.get_status(self.reactor.monotonic())
            return bool(status.get("value", 0))
        except Exception as e:
            self.gcode.respond_info(f"ACE: Error reading ACE_Pro pin: {e}")
            return False

    def update_ace_support_active_state(self):
        """
        Update ACE support state based on ACE_Pro pin.

        Also propagates enable/disable state to all serial managers
        to control reconnection behavior.
        """
        if self._ace_pro_enabled and not self.is_ace_enabled():
            self._restore_sensors()
            self.set_ace_global_enabled(False)

            # Disable reconnection attempts in all serial managers
            for instance in self.instances:
                instance.serial_mgr.disable_ace_pro()

            self.gcode.respond_info(
                "ACE: ACE Pro disabled - Standard Klipper sensors restored"
            )
            self._ace_pro_enabled = False

        elif not self._ace_pro_enabled and self.is_ace_enabled():
            self._setup_sensors()
            self._disable_all_sensor_detection()
            self.set_ace_global_enabled(True)

            # Enable reconnection attempts in all serial managers
            for instance in self.instances:
                instance.serial_mgr.enable_ace_pro()

            self.gcode.respond_info(
                "ACE: ACE Pro enabled - ACE runout monitoring active"
            )
            self._ace_pro_enabled = True

    def _monitor_ace_state(self, eventtime):
        """
        Monitor ACE Pro enable/disable state and connection health (2 second interval).

        Checks if ACE Pro unit is enabled/disabled via output pin and
        updates sensor state accordingly. Also monitors connection stability
        and pauses print if connection is unstable during printing.
        Flushes any pending dirty state to disk when idle.

        """
        try:
            self.update_ace_support_active_state()
            self._monitor_transport_reconnects()

            # Check connection health for all instances (if supervision enabled)
            if self._ace_pro_enabled and self._connection_supervision_enabled:
                self._check_connection_health(eventtime)

            # Safety net: flush deferred state to disk when not printing.
            # This catches any gcode command that used set() without a
            # matching flush() — the dirty vars will be persisted within
            # 2 seconds of the command completing.
            self._flush_if_idle(eventtime)

        except Exception as e:
            self.gcode.respond_info(f"ACE: Error in ACE state monitor: {e}")

        # Return next check time (2 seconds)
        return eventtime + 2.0

    def _flush_if_idle(self, eventtime):
        """Flush dirty persistent state to disk when the printer is idle.

        Called from the 2-second monitor timer.  During a print the
        explicit flush points (print-end, disconnect) are responsible;
        this method only acts as a safety net for idle / standalone
        commands that left variables dirty.
        """
        if not self.state.has_pending:
            return

        # Check whether a print is in progress — if so, leave dirty
        # state alone so we don't block the reactor with disk I/O
        # mid-print.  print-end / disconnect will handle it.
        print_stats = self.printer.lookup_object("print_stats", None)
        if print_stats:
            try:
                stats = print_stats.get_status(eventtime)
                state = (stats.get("state") or "").lower()
                if state in ("printing", "paused"):
                    return
            except Exception:
                pass

        try:
            self.state.flush()
        except Exception:
            logging.exception("ACE: Idle flush failed")

    def _check_connection_health(self, eventtime):
        """
        Check connection stability for all ACE instances.

        If any instance has an unstable connection:
        - During printing: Pause print and show dialog with resume/cancel
        - When idle: Show informational dialog
        """
        unstable_instances = []

        for instance in self.instances:
            status = instance.serial_mgr.get_connection_status()
            instance_num = instance.instance_num

            # Track if connection state changed
            prev_status = self._last_connection_status.get(instance_num, {})
            was_stable = prev_status.get("stable", True)
            is_stable = status["stable"]

            self._last_connection_status[instance_num] = status

            # Detect instability - only flag as unstable when reconnect threshold exceeded
            # This avoids false alarms for brief disconnects that quickly recover
            reconnect_threshold = instance.serial_mgr.INSTABILITY_THRESHOLD

            if status["recent_reconnects"] >= reconnect_threshold:
                unstable_instances.append({
                    "instance": instance_num,
                    "connected": status["connected"],
                    "recent_reconnects": status["recent_reconnects"],
                    "time_connected": status["time_connected"],
                })

            # Log when connection becomes stable again
            if is_stable and not was_stable and prev_status:
                self.gcode.respond_info(
                    f"ACE[{instance_num}]: Connection stabilized "
                    f"(connected for {status['time_connected']:.0f}s)"
                )
                # Clear dialog if all instances are now stable
                if self._connection_issue_shown:
                    all_stable = all(
                        self._last_connection_status.get(i.instance_num, {}).get("stable", True)
                        for i in self.instances
                    )
                    if all_stable:
                        self._close_connection_dialog()
                        self._connection_issue_shown = False

        # If we have unstable instances and haven't shown dialog yet
        if unstable_instances and not self._connection_issue_shown:
            self._handle_connection_issue(unstable_instances, eventtime)

    def _handle_connection_issue(self, unstable_instances, eventtime):
        """
        Handle detected connection issues.

        Args:
            unstable_instances: List of dicts with instance connection info
            eventtime: Current event time
        """
        # Check if we're printing
        print_stats = self.printer.lookup_object("print_stats", None)
        is_printing = False
        if print_stats:
            try:
                stats = print_stats.get_status(eventtime)
                state = (stats.get("state") or "").lower()
                is_printing = state in ["printing", "paused"]
            except Exception:
                pass

        # Build message
        instance_details = []
        for info in unstable_instances:
            if not info["connected"]:
                status = "disconnected"
            elif info["recent_reconnects"] >= 3:
                status = f"unstable ({info['recent_reconnects']} reconnects in 60s)"
            else:
                status = f"stabilizing ({info['time_connected']:.0f}s connected)"
            instance_details.append(f"ACE {info['instance']}: {status}")

        details_str = ", ".join(instance_details)

        if is_printing:
            # Pause print and show dialog with resume/cancel
            self.gcode.respond_info(
                f"ACE: Connection issue detected during print - {details_str}"
            )
            self._pause_for_connection_issue(unstable_instances)
        else:
            # Just show informational dialog
            self.gcode.respond_info(
                f"ACE: Connection issue detected - {details_str}"
            )
            self._show_connection_issue_dialog(unstable_instances, is_printing=False)

        self._connection_issue_shown = True

    def _pause_for_connection_issue(self, unstable_instances):
        """Pause print due to ACE connection issue."""
        try:
            self.gcode.respond_info("ACE: Pausing print due to connection issue")
            self.gcode.run_script_from_command("PAUSE")
        except Exception as e:
            self.gcode.respond_info(f"ACE: Error pausing print: {e}")

        self._show_connection_issue_dialog(unstable_instances, is_printing=True)

    def _show_connection_issue_dialog(self, unstable_instances, is_printing):
        """
        Show Mainsail dialog for connection issue.

        Args:
            unstable_instances: List of instances with connection issues
            is_printing: If True, show resume/cancel buttons; if False, just info
        """
        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_begin ACE Connection Issue"'
        )

        # Build instance details
        instance_details = []
        for info in unstable_instances:
            if not info["connected"]:
                status = "disconnected"
            elif info["recent_reconnects"] >= 3:
                status = f"unstable ({info['recent_reconnects']} reconnects/min)"
            else:
                status = f"stabilizing ({info['time_connected']:.0f}s)"
            instance_details.append(f"ACE {info['instance']}: {status}")

        if is_printing:
            prompt_text = (
                f"Print paused: ACE connection unstable. {' | '.join(instance_details)}. "
                f"Please fix the issue, then use RESUME to continue or CANCEL_PRINT to abort."
            )
        else:
            prompt_text = (
                f"ACE connection issue detected. {' | '.join(instance_details)}. "
                f"Please check connections and verify ACE unit is powered on."
            )

        self.gcode.run_script_from_command(
            f'RESPOND TYPE=command MSG="action:prompt_text {prompt_text}"'
        )

        # Just a dismiss button for all cases
        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_footer_button Dismiss|'
            'RESPOND TYPE=command MSG=action:prompt_end|secondary"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_show"'
        )

    def _close_connection_dialog(self):
        """Close the connection issue dialog."""
        try:
            self.gcode.run_script_from_command(
                'RESPOND TYPE=command MSG="action:prompt_end"'
            )
            self.gcode.respond_info("ACE: Connection restored - dialog closed")
        except Exception as e:
            self.gcode.respond_info(f"ACE: Error closing dialog: {e}")

    @toolchange_in_progress_guard
    def perform_tool_change(self, current_tool, target_tool, is_endless_spool=False):
        """
        Execute complete tool change sequence.

        Args:
            current_tool: Current tool (-1 if none loaded)
            target_tool: Target tool (-1 to unload only)
            is_endless_spool: If True, skip unload of current tool (already empty)
        """
        status = None
        gcode_move = self.printer.lookup_object("gcode_move")

        toolhead_sensor = self.get_switch_state(SENSOR_TOOLHEAD)
        rdm_sensor = self.get_switch_state(SENSOR_RDM) if self.has_rdm_sensor() else False
        filament_pos = self.state.get("ace_filament_pos", FILAMENT_STATE_BOWDEN)

        logging.info(
            f"ACE: Toolchange plausibility check - "
            f"Sensors: toolhead={toolhead_sensor}, rdm={'N/A (no RDM)' if not self.has_rdm_sensor() else rdm_sensor}, "
            f"State: filament_pos='{filament_pos}', current_tool=T{current_tool}"
        )

        if (toolhead_sensor or rdm_sensor) and (filament_pos == FILAMENT_STATE_BOWDEN):
            self.gcode.respond_info(
                f"ACE: PLAUSIBILITY MISMATCH - Sensors show filament present "
                f"but state='{filament_pos}'. Performing smart_unload to clear path. May help or not..."
            )

            success = self.smart_unload(tool_index=current_tool if current_tool >= 0 else -1)
            if not success:
                raise Exception("Failed to clear filament path - plausibility check failed")
            current_tool = -1

        if not toolhead_sensor and rdm_sensor and (filament_pos == FILAMENT_STATE_SPLITTER):
            self.gcode.respond_info(
                f"ACE: WARNING: Toolhead clear, but filament detected at RDM, "
                f"state='{filament_pos}'. Performing smart_unload to clear path."
            )

            success = self.smart_unload(tool_index=current_tool if current_tool >= 0 else -1)
            if not success:
                raise Exception("Failed to clear RMS filament path")
            current_tool = -1

        target_temp = 0
        if target_tool >= 0:
            target_ace, target_slot = get_ace_instance_and_slot_for_tool(target_tool)
            if target_ace is not None:
                inv_temp = target_ace.inventory[target_slot].get("temp", 0)
                if inv_temp > 0:
                    target_temp = inv_temp
                    self.gcode.respond_info(
                        f"ACE: Target tool T{target_tool} inventory temp: {target_temp}°C"
                    )

        # ===== HANDLE TOOL RESELECTION =====
        if current_tool == target_tool:
            filament_pos = self.state.get("ace_filament_pos", FILAMENT_STATE_BOWDEN)

            sensor_has_filament = self.get_switch_state(SENSOR_TOOLHEAD)

            if self.has_rdm_sensor():
                rdm_has_filament = self.get_switch_state(SENSOR_RDM)

                # ===== DETECT INVALID STATE: Nozzle has filament but RDM is empty =====
                if filament_pos == FILAMENT_STATE_NOZZLE and sensor_has_filament and not rdm_has_filament:
                    self.gcode.respond_info(
                        f"ACE: ⚠ INVALID STATE DETECTED - Tool {target_tool} marked as loaded\n"
                        f"  State: filament_pos='nozzle'\n"
                        f"  Toolhead sensor: {'TRIGGERED' if sensor_has_filament else 'clear'}\n"
                        f"  RDM sensor: {'TRIGGERED' if rdm_has_filament else 'CLEAR'}\n"
                        f"  PROBLEM: Filament stuck at nozzle but path is broken (no filament in RDM)\n"
                        f"  This indicates incomplete unload or broken filament in path.\n"
                        f"  SOLUTION: Manually unload/retract stuck filament, then retry toolchange."
                    )

                    raise Exception(
                        f"Invalid filament state for T{target_tool}: "
                        f"Filament stuck at nozzle but RDM sensor is empty. "
                        f"Cannot proceed - manual intervention required. "
                        f"Use ACE_CHANGE_TOOL TOOL=-1 to force unload, or manually clear the path."
                    )

            if filament_pos == FILAMENT_STATE_NOZZLE:
                if sensor_has_filament:
                    # State matches sensor - tool is truly loaded
                    # Ensure feed assist is active for this tool (may have been lost after ACE power cycle)
                    target_instance = get_instance_from_tool(target_tool)
                    target_local_slot = get_local_slot(target_tool, target_instance)
                    target_ace = self.instances[target_instance] if target_instance < len(self.instances) else None

                    if target_ace:
                        self.gcode.respond_info(
                            f"ACE: Tool {target_tool} already loaded - "
                            f"re-enabling feed assist on slot {target_local_slot}"
                        )
                        target_ace._enable_feed_assist(target_local_slot)

                    return f"Tool {target_tool} (already loaded)"
                else:
                    # State says loaded but sensor is EMPTY - state is WRONG
                    self.gcode.respond_info(
                        "ACE: ✗ STATE MISMATCH - filament_pos='nozzle' but sensor is EMPTY! "
                        "Correcting state and proceeding with normal load."
                    )
                    if self.get_switch_state(SENSOR_RDM):
                        filament_pos = FILAMENT_STATE_SPLITTER
                        self.state.set("ace_filament_pos", filament_pos)
                    else:
                        filament_pos = FILAMENT_STATE_BOWDEN
                        self.state.set("ace_filament_pos", filament_pos)
                    self.gcode.respond_info(
                        f"ACE: filament_pos for Tool {target_tool} changed to "
                        f"assumed filament_pos='{filament_pos}'"
                    )
                    # Fall through to normal toolchange logic below

            # So, filament state is not NOZZLE if we reach this point, but we have
            # assumingly an active/current tool loaded. Try to find out the real state
            self.gcode.respond_info(
                f"ACE: Tool {target_tool} marked as current but "
                f"filament_pos='{filament_pos}', checking sensors..."
            )

            # Check toolhead sensor, if it shows filament we assume tool is loaded and persiststate was wrong
            if sensor_has_filament:
                self.gcode.respond_info(
                    "ACE: Toolhead sensor triggered - filament present. Correcting state to 'nozzle'"
                )
                self.state.set("ace_filament_pos", FILAMENT_STATE_NOZZLE)
                return f"Tool {target_tool} (state corrected)"
            else:
                # Again path check, if RDM sensor exists it will be used there as well
                # If either sensor shows filament, we assume tool is loaded
                if not self.is_filament_path_free():
                    self.gcode.respond_info(
                        f"ACE: WARNING - Tool {target_tool} marked as current but "
                        f"state is:'{filament_pos} and sensor report path is blocked. "
                        f"Attempting to clear path."
                    )
                    success = self.smart_unload(tool_index=-1)
                    if not success:
                        raise Exception(
                            f"Cannot proceed with tool {target_tool} - filament path is jammed. "
                            f"Manual intervention required."
                        )

                self.gcode.respond_info(
                    f"ACE: Tool {target_tool} path cleared, proceeding with normal load."
                )

        # ===== PRE-TOOLCHANGE (Macro handles heating) =====
        self.gcode.run_script_from_command(
            f"_ACE_PRE_TOOLCHANGE FROM={current_tool} TO={target_tool} TARGET_TEMP={target_temp}"
        )

        # ===== UNLOAD CURRENT TOOL =====
        if current_tool != -1 and not is_endless_spool:
            filament_pos = self.state.get("ace_filament_pos", FILAMENT_STATE_BOWDEN)
            self.gcode.respond_info(f"ACE: Current filament_pos before unload: {filament_pos}")
            if (filament_pos in [FILAMENT_STATE_NOZZLE, FILAMENT_STATE_SPLITTER]):
                # Trust sensors over persisted state.
                if self.is_filament_path_free():
                    # All sensors report clear - state is stale/wrong, correct it.
                    self.gcode.respond_info(
                        f"ACE: WARNING: State says '{filament_pos}' but sensors report path CLEAR - "
                        f"trusting sensors, correcting state to bowden, skipping unload."
                    )
                    self.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
                else:
                    # Sensors confirm filament is in the path - proceed with unload.
                    # Also fix state if toolhead sensor shows filament has advanced further.
                    if filament_pos == FILAMENT_STATE_SPLITTER and self.get_switch_state(SENSOR_TOOLHEAD):
                        self.gcode.respond_info(
                            f"ACE: State was '{FILAMENT_STATE_SPLITTER}' but toolhead sensor is "
                            f"TRIGGERED - correcting to '{FILAMENT_STATE_NOZZLE}' before unload."
                        )
                        filament_pos = FILAMENT_STATE_NOZZLE
                        self.state.set("ace_filament_pos", FILAMENT_STATE_NOZZLE)

                    self.gcode.respond_info(f"ACE: Tool {current_tool} marked as loaded, performing unload")
                    success = self.smart_unload(tool_index=current_tool)
                    if not success:
                        raise Exception(f"Failed to unload tool {current_tool}")
                    self.gcode.respond_info(f"ACE: Tool {current_tool} unloaded successfully")

            elif filament_pos == FILAMENT_STATE_BOWDEN:
                self.gcode.respond_info(
                    f"ACE: Tool {current_tool} not loaded (filament_pos='{filament_pos}'), skipping unload"
                )

            else:
                self.gcode.respond_info(f"ACE: Unknown filament_pos='{filament_pos}', checking sensors...")
                if self.get_switch_state(SENSOR_TOOLHEAD):
                    self.gcode.respond_info("ACE: Toolhead sensor triggered, performing unload")
                    success = self.smart_unload(tool_index=current_tool)
                    if not success:
                        raise Exception(f"Failed to unload tool {current_tool}")
                else:
                    self.gcode.respond_info("ACE: No filament at toolhead, correcting state to bowden (unloaded)")
                    self.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)

        elif current_tool == -1:
            self.gcode.respond_info("ACE: No current tool loaded, skipping unload")
        elif is_endless_spool:
            self.gcode.respond_info(
                f"ACE: Endless spool mode - skipping unload of tool {current_tool} (already empty)"
            )
            self.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)

        # ===== LOAD NEW TOOL =====
        if target_tool != -1:
            if not self.check_and_wait_for_spool_ready(target_tool):
                raise Exception(f"Tool {target_tool} is not ready. Please check the spool and try again.")

            target_ace, target_slot = get_ace_instance_and_slot_for_tool(target_tool)

            if target_ace is None:
                raise Exception(f"Tool {target_tool} not managed by any ACE instance")

            self.gcode.respond_info(f"ACE[{target_ace.instance_num}]: Loading tool {target_tool}...")

            # Capture the amount purged during loading
            purged_amount = target_ace._feed_filament_into_toolhead(target_tool, check_pre_condition=False)

            self.state.set("ace_current_index", target_tool)
            self.gcode.run_script_from_command(
                f"SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE={target_tool}"
            )
            self.gcode.respond_info(f"// Current tool index: {target_tool}")
            self.gcode.respond_info(f"ACE: State updated - current tool marked as T{target_tool}")

            gcode_move.reset_last_position()

            target_ace._enable_feed_assist(target_slot)

            # Re-initialize runout detection baseline after successful load
            self.runout_monitor.prev_toolhead_sensor_state = self.get_switch_state(SENSOR_TOOLHEAD)
            logging.info(
                f"ACE: Runout detection baseline reset after load - "
                f"sensor: {'present' if self.runout_monitor.prev_toolhead_sensor_state else 'absent'}, "
                f"tool: T{target_tool}"
            )

            # Re-enable detection (in case it was disabled)
            if not self.runout_monitor.runout_detection_active:
                self.set_runout_detection_active(True)
                self.gcode.respond_info("ACE: Runout detection re-enabled after toolchange")

            toolchange_purge_length = self.toolchange_purge_length
            toolchange_purge_speed = self.toolchange_purge_speed

            if is_endless_spool and current_tool != -1:
                purge_length = int(toolchange_purge_length * 1.5)
            else:
                purge_length = toolchange_purge_length

            final_purge_length = purge_length * self.purge_multiplier

            self.gcode.respond_info("ACE: Applying purge multiplier "
                                    f"{self.purge_multiplier:.2f} to purge length {purge_length}mm, "
                                    f"final purge length: {final_purge_length}mm")

            self.gcode.run_script_from_command(
                f"_ACE_POST_TOOLCHANGE FROM={current_tool} TO={target_tool} "
                f"PURGELENGTH={final_purge_length} PURGESPEED={toolchange_purge_speed} "
                f"TARGET_TEMP={target_temp} PURGED_AMOUNT={purged_amount:.1f} "
                f"PURGE_MAX_CHUNK_LENGTH={self.purge_max_chunk_length}"
            )

            gcode_move.reset_last_position()
            status = f"Tool {current_tool} → {target_tool} (ACE[{target_ace.instance_num}])"
        else:
            status = f"Unloaded tool {current_tool}"

        gcode_move.reset_last_position()

        if target_tool == -1:
            self.state.set("ace_current_index", -1)
            self.gcode.run_script_from_command(
                "SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE=-1"
            )
            self.gcode.respond_info("ACE: State updated - no tool currently loaded")

        return status

    def register_tool_macros(self, instance_num):
        """
        Register T<n> commands for given instance.

        Instance 0: T0, T1, T2, T3
        Instance 1: T4, T5, T6, T7
        Etc.

        If user has defined a gcode_macro for a tool (e.g., for Spoolman integration),
        skip auto-registration to allow user's macro to take precedence.
        """
        for local_slot in range(SLOTS_PER_ACE):
            global_tool = get_tool_offset(instance_num) + local_slot
            macro_name = f"T{global_tool}"

            # Check if user has defined this macro (e.g., for Spoolman support)
            existing_macro = self.printer.lookup_object(f"gcode_macro {macro_name}", None)
            if existing_macro is not None:
                # User defined their own macro - skip auto-registration
                continue

            def make_tool_macro(tool_idx):
                def tool_macro(gcmd):
                    # Delegate to command handler
                    commands.cmd_ACE_CHANGE_TOOL(self, gcmd, tool_idx)

                return tool_macro

            desc = f"Select tool {global_tool} " f"(ACE instance {instance_num})"
            self.gcode.register_command(macro_name, make_tool_macro(global_tool), desc=desc)

    # ========== Status and Reporting ==========

    def get_status(self, eventtime=None):
        try:
            # Sensor states: True/False when sensor is registered, None when unavailable.
            toolhead_sensor = (
                self.get_switch_state(SENSOR_TOOLHEAD)
                if SENSOR_TOOLHEAD in self.sensors
                else None
            )
            rdm_sensor = (
                self.get_switch_state(SENSOR_RDM)
                if SENSOR_RDM in self.sensors
                else None
            )
            return {
                "ace_instances": len(self.instances),
                "current_index": self.state.get("ace_current_index", -1),
                "endless_spool_enabled": bool(
                    self.state.get("ace_endless_spool_enabled", False)
                ),
                "endless_spool_match_mode": self.state.get(
                    "ace_endless_spool_match_mode", "exact"
                ),
                "ace_pro_enabled": bool(self._ace_pro_enabled),
                "toolhead_sensor": toolhead_sensor,
                "rdm_sensor": rdm_sensor,
            }
        except Exception:
            return {
                "ace_instances": len(self.instances),
                "current_index": -1,
                "endless_spool_enabled": False,
                "endless_spool_match_mode": "exact",
                "ace_pro_enabled": False,
                "toolhead_sensor": None,
                "rdm_sensor": None,
            }

    def _resolve_instance_config(self, instance_num):
        """
        Resolve per-instance config by parsing override syntax.

        All keys from self.ace_config are copied, and only the keys
        listed in OVERRIDABLE_PARAMS are instance-resolved.
        """
        # Start with a shallow copy of the global ACE config
        resolved = dict(self.ace_config)

        # Resolve overridable params for this instance
        for param in OVERRIDABLE_PARAMS:
            if param in self.ace_config:
                raw_value = self.ace_config[param]
                resolved[param] = parse_instance_config(raw_value, instance_num, param)

        for param in CHOICE_OVERRIDABLE_PARAMS:
            if param in self.ace_config:
                raw_value = self.ace_config[param]
                resolved[param] = parse_instance_choice_config(raw_value, instance_num, param)

        resolved["active_protocol_name"] = resolve_protocol_name(
            resolved.get("protocol", "auto"),
            instance_num=instance_num,
            available_port_descriptions=self._get_available_port_descriptions(),
        )

        direct_mode_raw = resolved.get("ace2_single_direct_mode", "false")
        resolved["ace2_single_direct_mode"] = str(direct_mode_raw).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        if "baud" in self.ace_config:
            resolved["baud"] = parse_instance_baud_config(
                self.ace_config["baud"],
                instance_num,
                resolved["active_protocol_name"],
            )

        return resolved

    def _create_instance_protocol(self, instance_config):
        """Create protocol adapter, including ACE2 single-device direct fallback mode."""
        active_protocol_name = instance_config["active_protocol_name"]
        if (
            active_protocol_name == "ace2_proto"
            and bool(instance_config.get("ace2_single_direct_mode", False))
        ):
            from .protocol_ace2 import AceProtoProtocolAdapter

            return AceProtoProtocolAdapter(single_device_direct_mode=True)
        return create_protocol_adapter(active_protocol_name)

    def _get_available_port_descriptions(self):
        """List visible serial-port signatures for protocol auto-selection."""
        try:
            signatures = []
            for portinfo in serial.tools.list_ports.comports():
                signature = getattr(portinfo, "description", "")
                if not signature:
                    signature = getattr(portinfo, "product", "")
                if not signature:
                    signature = getattr(portinfo, "interface", "")
                if not signature:
                    signature = getattr(portinfo, "hwid", "")
                signatures.append(signature)
            return signatures
        except Exception:
            return []

    def _iter_unique_transport_instances(self):
        """Yield one representative instance per underlying serial transport."""
        seen_managers = set()
        for instance in self.instances:
            serial_mgr = getattr(instance, "serial_mgr", None)
            serial_mgr_id = id(serial_mgr)
            if serial_mgr_id in seen_managers:
                continue
            seen_managers.add(serial_mgr_id)
            yield instance

    def _get_instances_for_bus_session(self, bus_session):
        """Return logical instances that share one ACE2 bus session."""
        return [
            instance for instance in self.instances
            if getattr(instance, "bus_session", None) is bus_session
        ]

    def _get_shared_bus_bindings_varname(self, shared_instances):
        """Build stable persistent-state variable name for one ACE2 bus group."""
        instance_ids = "_".join(
            str(instance.instance_num)
            for instance in sorted(shared_instances, key=lambda item: item.instance_num)
        )
        return f"ace2_bus_bindings_{instance_ids}"

    def _load_shared_bus_bindings(self, bus_session, shared_instances):
        """Restore persisted ACE2 UID bindings for one shared bus group."""
        raw_mapping = self.state.get(
            self._get_shared_bus_bindings_varname(shared_instances),
            {},
        )
        normalized_mapping = {}
        for instance_num, uid_tuple in dict(raw_mapping or {}).items():
            try:
                normalized_instance = int(instance_num)
                uid1, uid2, uid3 = uid_tuple
                normalized_mapping[normalized_instance] = (
                    int(uid1),
                    int(uid2),
                    int(uid3),
                )
            except (TypeError, ValueError):
                continue
        bus_session.bind_persisted_instances(normalized_mapping)

    def _persist_shared_bus_bindings(self, bus_session, shared_instances):
        """Store current ACE2 UID bindings for one shared bus group."""
        self.state.set(
            self._get_shared_bus_bindings_varname(shared_instances),
            bus_session.export_bindings(),
        )

    def _get_shared_bus_ready_instances(self, bus_session):
        """Return shared-bus instances that currently have an assigned target device id."""
        ready_instances = []
        for instance in self._get_instances_for_bus_session(bus_session):
            device = bus_session.get_device_for_instance(instance.instance_num)
            if device is not None and device.device_id is not None:
                ready_instances.append(instance)
        return ready_instances

    def _cancel_shared_bus_retry(self, bus_session):
        """Cancel pending discovery retry timer for one shared bus."""
        bus_key = id(bus_session)
        timer = self._shared_bus_retry_timers.pop(bus_key, None)
        if timer is not None:
            try:
                self.reactor.unregister_timer(timer)
            except Exception:
                pass
        self._shared_bus_retry_delays.pop(bus_key, None)

    def _schedule_shared_bus_retry(self, bus_session, reason):
        """Schedule a bounded backoff retry for ACE2 shared-bus discovery."""
        bus_key = id(bus_session)
        if self._shared_bus_retry_timers.get(bus_key) is not None:
            return

        delay = self._shared_bus_retry_delays.get(bus_key, self._shared_bus_retry_min_delay)
        next_delay = min(delay * 2.0, self._shared_bus_retry_max_delay)
        self._shared_bus_retry_delays[bus_key] = next_delay

        shared_instances = self._get_instances_for_bus_session(bus_session)
        if not shared_instances:
            return
        lead_instance = sorted(shared_instances, key=lambda item: item.instance_num)[0]
        self.gcode.respond_info(
            f"ACE[{lead_instance.instance_num}]: ACE2 bus discovery retry in {delay:.1f}s ({reason})"
        )

        def _retry_callback(eventtime):
            self._shared_bus_retry_timers.pop(bus_key, None)
            shared = sorted(
                self._get_instances_for_bus_session(bus_session),
                key=lambda item: item.instance_num,
            )
            if not shared:
                return self.reactor.NEVER
            lead = shared[0]
            is_connected = getattr(lead.serial_mgr, "is_connected", None)
            if callable(is_connected):
                try:
                    if not is_connected():
                        return self.reactor.NEVER
                except Exception:
                    return self.reactor.NEVER
            self._on_shared_bus_connected(bus_session)
            return self.reactor.NEVER

        self._shared_bus_retry_timers[bus_key] = self.reactor.register_timer(
            _retry_callback,
            self.reactor.monotonic() + delay,
        )

    def _start_shared_bus_runtime(self, bus_session):
        """Start per-instance ACE2 status polling for one shared bus group."""
        for instance in self._get_shared_bus_ready_instances(bus_session):
            instance.request_shared_bus_info_refresh()
            instance.start_shared_bus_heartbeat()

    def _queue_shared_bus_instance_setup(self, bus_session):
        """Queue ACE2 startup setup commands for each logical instance on one bus."""
        shared_instances = sorted(
            self._get_instances_for_bus_session(bus_session),
            key=lambda item: item.instance_num,
        )
        for instance in shared_instances:
            device = bus_session.get_device_for_instance(instance.instance_num)
            if device is None or device.device_id is None:
                logging.info(
                    "ACE[%s]: skipping ACE2 setup requests until shared-bus device_id is assigned",
                    instance.instance_num,
                )
                continue

            protocol = getattr(instance, "protocol", None)
            if protocol is None:
                continue

            rfid_enable = bool(getattr(instance, "rfid_inventory_sync_enabled", True))
            requests = (
                protocol.build_debug_request(
                    "SET_RFID_ENABLE",
                    {"index": 0, "enable": rfid_enable},
                ),
                protocol.build_debug_request(
                    "SET_RFID_ENABLE",
                    {"index": 2, "enable": rfid_enable},
                ),
                protocol.build_debug_request(
                    "SET_FEED_CHECK",
                    {
                        "check_length": int(self.ace_config.get("ace2_feed_check_length", 110)),
                        "error_length": int(self.ace_config.get("ace2_feed_error_length", 100)),
                    },
                ),
            )

            for request in requests:
                try:
                    instance.send_high_prio_request(request, lambda response: response)
                except Exception as exc:
                    logging.warning(
                        "ACE[%s]: failed to queue ACE2 setup request %s: %s",
                        instance.instance_num,
                        request.get("command"),
                        exc,
                    )

    def _get_transport_last_connected_time(self, serial_mgr):
        """Return last successful connect timestamp for one serial manager."""
        get_status = getattr(serial_mgr, "get_connection_status", None)
        if not callable(get_status):
            return None

        try:
            status = get_status() or {}
            last_connected_time = status.get("last_connected_time")
            if isinstance(last_connected_time, (int, float)) and last_connected_time > 0:
                return float(last_connected_time)
        except Exception:
            return None
        return None

    def _monitor_transport_reconnects(self):
        """Keep reconnect timers alive and reinitialize shared buses after reconnect."""
        for instance in self._iter_unique_transport_instances():
            serial_mgr = getattr(instance, "serial_mgr", None)
            if serial_mgr is None:
                continue

            ensure_connect_timer = getattr(serial_mgr, "ensure_connect_timer", None)
            if callable(ensure_connect_timer):
                try:
                    ensure_connect_timer()
                except Exception as exc:
                    logging.debug(
                        "ACE[%s]: ensure_connect_timer failed: %s",
                        instance.instance_num,
                        exc,
                    )

            bus_session = getattr(instance, "bus_session", None)
            if bus_session is None:
                continue

            is_connected = getattr(serial_mgr, "is_connected", None)
            if not callable(is_connected):
                continue
            try:
                if not is_connected():
                    continue
            except Exception:
                continue

            last_connected_time = self._get_transport_last_connected_time(serial_mgr)
            if last_connected_time is None:
                continue

            bus_key = id(bus_session)
            if self._shared_bus_last_connected_time.get(bus_key) == last_connected_time:
                continue

            if getattr(serial_mgr, "_port", None):
                bus_session.port = serial_mgr._port
            self._shared_bus_last_connected_time[bus_key] = last_connected_time
            self._on_shared_bus_connected(bus_session)

    def _handle_shared_bus_unsolicited(self, bus_session, response):
        """Route unmatched ACE2 shared-bus responses to their logical instance."""
        device_id = response.get("device_id")
        if not device_id:
            return False

        device = bus_session.get_device_for_device_id(device_id)
        if device is None or device.logical_instance is None:
            return False

        for instance in self._get_instances_for_bus_session(bus_session):
            if instance.instance_num == device.logical_instance:
                return bool(instance.protocol.handle_bound_shared_bus_unsolicited(instance, response))

        return False

    def _send_shared_bus_request(self, instance, request, timeout_s=5.0):
        """Send one manager-owned request over shared ACE2 transport and wait for reply."""
        response_container = {"done": False, "response": None}

        def callback(response):
            response_container["response"] = response
            response_container["done"] = True

        instance.serial_mgr.send_high_prio_request(request, callback)

        timeout_at = time.monotonic() + timeout_s
        while not response_container["done"] and time.monotonic() < timeout_at:
            self.reactor.pause(self.reactor.monotonic() + 0.05)

        return response_container["response"]

    def _initialize_shared_bus_transport(self, instance):
        """Discover and assign ACE2 devices on a shared bus transport."""
        bus_session = getattr(instance, "bus_session", None)
        if bus_session is None:
            return 0

        shared_instances = self._get_instances_for_bus_session(bus_session)
        if not shared_instances:
            return 0

        bus_session.reset()
        self._load_shared_bus_bindings(bus_session, shared_instances)

        protocol = getattr(instance.serial_mgr, "protocol", None)
        if protocol is None:
            return 0

        discovered_devices = []
        for _ in range(len(shared_instances)):
            response = self._send_shared_bus_request(
                instance,
                protocol.build_discover_device_request(),
            )
            if not response or "result" not in response:
                break

            result = response["result"]
            device = bus_session.record_discovered_device(
                result.get("uid1", 0),
                result.get("uid2", 0),
                result.get("uid3", 0),
            )
            discovered_devices.append(device)

        if not discovered_devices:
            self.gcode.respond_info(
                f"ACE[{instance.instance_num}]: ACE2 discovery returned no devices on shared bus"
            )
            return 0

        ordered_instances = sorted(shared_instances, key=lambda item: item.instance_num)
        ordered_devices = list(bus_session.iter_discovered_devices())
        for logical_instance, device in zip(ordered_instances, ordered_devices):
            if device.logical_instance is None:
                bus_session.bind_logical_instance(
                    logical_instance.instance_num,
                    device.identity.uid1,
                    device.identity.uid2,
                    device.identity.uid3,
                )

        for device in bus_session.build_assignment_plan(start_device_id=1):
            response = self._send_shared_bus_request(
                instance,
                protocol.build_assign_device_id_request(
                    device.identity.uid1,
                    device.identity.uid2,
                    device.identity.uid3,
                    device.device_id,
                ),
            )
            if not response or response.get("code") != 0:
                self.gcode.respond_info(
                    f"ACE[{instance.instance_num}]: ACE2 device-id assignment failed for UID={device.identity.uid_tuple}: {response}"
                )

        self._persist_shared_bus_bindings(bus_session, shared_instances)
        return len(self._get_shared_bus_ready_instances(bus_session))

    def _on_shared_bus_connected(self, bus_session):
        """Reinitialize and restart one ACE2 shared bus after connect."""
        shared_instances = self._get_instances_for_bus_session(bus_session)
        if not shared_instances:
            return

        instance = sorted(shared_instances, key=lambda item: item.instance_num)[0]
        if instance.serial_mgr._port:
            bus_session.port = instance.serial_mgr._port
        last_connected_time = self._get_transport_last_connected_time(instance.serial_mgr)
        if last_connected_time is not None:
            self._shared_bus_last_connected_time[id(bus_session)] = last_connected_time

        ready_count = self._initialize_shared_bus_transport(instance)
        if ready_count <= 0:
            self._schedule_shared_bus_retry(
                bus_session,
                "discovery returned no assignable devices",
            )
            return

        self._cancel_shared_bus_retry(bus_session)
        self._queue_shared_bus_instance_setup(bus_session)
        self._start_shared_bus_runtime(bus_session)

    def _build_shared_transport_kwargs(self, instance_num, instance_config, ace_enabled, protocol):
        """Create or reuse transport objects for protocols that share a physical bus."""
        transport_spec = protocol.get_transport_spec()
        if not transport_spec.shared_bus:
            return {}

        transport_key = (
            instance_config["active_protocol_name"],
            instance_config["baud"],
            transport_spec.port_description,
        )
        context = self._shared_transport_contexts.get(transport_key)
        if context is None:
            serial_mgr = AceSerialManager(
                self.gcode,
                self.reactor,
                instance_num,
                ace_enabled=ace_enabled,
                status_debug_logging=bool(instance_config.get("status_debug_logging", False)),
                supervision_enabled=bool(instance_config.get("ace_connection_supervision", True)),
                protocol=protocol,
            )
            bus_session = Ace2BusSession(port="", baud=instance_config["baud"])
            context = {
                "serial_mgr": serial_mgr,
                "bus_session": bus_session,
            }
            serial_mgr.set_on_connect_callback(
                lambda bus_session=bus_session: self._on_shared_bus_connected(bus_session)
            )
            serial_mgr.set_unsolicited_response_callback(
                lambda response, bus_session=bus_session: self._handle_shared_bus_unsolicited(
                    bus_session,
                    response,
                )
            )
            self._shared_transport_contexts[transport_key] = context

        return dict(context)

    def check_and_wait_for_spool_ready(self, target_tool, timeout_s=300, check_interval_s=1.0, stable_ready_s=3.0):
        """
        Check if the spool for target_tool is ready before feeding.

        Waits until the status is continuously 'ready' for at least stable_ready_s seconds,
        but only if the spool was not ready when the method was entered. If it was already
        ready initially, returns True immediately without waiting.

        Args:
            target_tool: Global tool index to check
            timeout_s: Maximum time to wait (default 300s / 5min)
            check_interval_s: How often to re-check status (default 1s)
            stable_ready_s: Time the status must stay 'ready' continuously (default 3s)

        Returns:
            bool: True if spool is ready (stably or initially), False if timeout without stability
        """
        # Find the ACE instance managing this tool
        instance_num = get_instance_from_tool(target_tool)
        if instance_num < 0 or instance_num >= len(self.instances):
            self.gcode.respond_info(f"ACE: Tool {target_tool} not managed by any ACE instance")
            return False

        instance = self.instances[instance_num]
        local_slot = get_local_slot(target_tool, instance_num)

        # Initial check
        instance.wait_ready()
        inventory_status = instance.inventory[local_slot].get("status", "empty")
        ace_status = instance._info.get("slots", [{}] * instance.SLOT_COUNT)[local_slot].get("status", "empty")
        was_initially_ready = inventory_status == "ready" and ace_status == "ready"

        if was_initially_ready:
            return True
        else:
            self.gcode.respond_info(
                f"****************************************\n"
                f"* ACE[{instance_num}]: Spool for tool {target_tool} (slot {local_slot}) is not ready *\n"
                f"* (inventory: {inventory_status}, ACE: {ace_status}) *\n"
                f"* Please reload spool on ACE {instance_num}, index {local_slot} *\n"
                f"****************************************"
            )

            # Show Mainsail dialog prompt
            self._show_spool_not_ready_prompt(target_tool, instance_num, local_slot, inventory_status, ace_status)

        start_time = self.reactor.monotonic()
        ready_start_time = None  # Time when it first became ready

        while True:
            elapsed = self.reactor.monotonic() - start_time
            if elapsed > timeout_s:
                self.gcode.respond_info(
                    f"ACE[{instance_num}]: Timeout waiting for stable ready state on ACE {instance_num}, "
                    f"slot {local_slot} (waited {elapsed:.1f}s). Aborting spool check."
                )
                # Close the prompt on timeout
                self.gcode.run_script_from_command('RESPOND TYPE=command MSG="action:prompt_end"')
                return False

            # Re-check status
            inventory_status = instance.inventory[local_slot].get("status", "empty")
            ace_status = instance._info.get("slots", [{}] * instance.SLOT_COUNT)[local_slot].get("status", "empty")
            is_ready = inventory_status == "ready" and ace_status == "ready"

            if is_ready:
                if ready_start_time is None:
                    # First time becoming ready - start the stability timer
                    ready_start_time = self.reactor.monotonic()
                    self.gcode.respond_info(
                        f"ACE[{instance_num}]: First time ready detected, waiting {stable_ready_s}s for stability..."
                    )
                else:
                    # Check if stable for required duration
                    time_ready = self.reactor.monotonic() - ready_start_time
                    if time_ready >= stable_ready_s:
                        self.gcode.respond_info(
                            f"ACE[{instance_num}]: Spool for tool {target_tool} (slot {local_slot}) "
                            f"stable and ready (waited {time_ready:.1f}s)"
                        )
                        # Close the prompt on success
                        self.gcode.run_script_from_command('RESPOND TYPE=command MSG="action:prompt_end"')
                        return True
            else:
                # Status changed back to not ready - reset timer
                if ready_start_time is not None:
                    self.gcode.respond_info(
                        f"ACE[{instance_num}]: Status changed back to not ready "
                        f"(inventory: {inventory_status}, ACE: {ace_status}), resetting stability timer"
                    )
                ready_start_time = None

            # Wait before next check
            self.reactor.pause(self.reactor.monotonic() + check_interval_s)

    def _show_spool_not_ready_prompt(self, tool_index, instance_num, local_slot, inventory_status, ace_status):
        """
        Show Mainsail prompt when spool is not ready.

        Args:
            tool_index: Global tool index
            instance_num: ACE instance number
            local_slot: Local slot number on instance
            inventory_status: Current inventory status
            ace_status: Current ACE hardware status
        """
        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_begin Spool Not Ready"'
        )

        prompt_text = (
            f"Spool not ready! ACE {instance_num}, Slot {local_slot} (Tool T{tool_index}) - "
            f"Status: inventory={inventory_status}, ACE={ace_status} - "
            f"Please reload the spool on ACE {instance_num}, slot {local_slot}. "
            f"The system will automatically continue when the spool is detected and stable."
        )

        self.gcode.run_script_from_command(
            f'RESPOND TYPE=command MSG="action:prompt_text {prompt_text}"'
        )

        # Add a cancel button for emergency abort
        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_footer_button Cancel Print|CANCEL_PRINT|error"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_show"'
        )

    def set_and_save_variable(self, varname, value):
        """
        Set a variable in persistent storage (deferred flush).

        Convenience wrapper around ``self.state.set()``.
        Kept so that callers (commands, instances) that hold a manager
        reference can use ``manager.set_and_save_variable(...)``.

        Args:
            varname: Variable name (string)
            value: Value to save (any JSON-serializable type)
        """
        self.state.set(varname, value)

    def has_rdm_sensor(self):
        """Check if RDM sensor is configured and available."""
        return SENSOR_RDM in self.sensors and self.sensors[SENSOR_RDM] is not None

    def is_feed_assist_active(self):
        """Check if any ACE instance has feed assist active.

        Returns:
            bool: True if at least one instance has feed_assist enabled.
        """
        for instance in self.instances:
            if instance._feed_assist_index >= 0:
                return True
        return False

    def get_rdm_encoder_pulse(self):
        """Return the RDM filament_tracker encoder_pulse count, or None.

        Only works when the RDM sensor is a filament_tracker wrapped
        in a FilamentTrackerAdapter.  Returns None if the sensor is a
        plain filament_switch_sensor or not configured.
        """
        if not self.has_rdm_sensor():
            return None
        sensor = self.sensors[SENSOR_RDM]
        if isinstance(sensor, FilamentTrackerAdapter):
            return sensor._tracker.tracker_status.encoder_pulse
        return None

    def full_unload_slot(self, tool_index):
        """
        Fully unload a slot using fixed-length retraction.

        **FIXED-LENGTH MODE:**
        - Retracts exactly total_max_feeding_length
        - No status polling during retraction
        - Uses time-based dwell + wait_ready (via _retract)
        - Validates with sensors after completion (if available)

        Args:
            tool_index: Global tool index to unload

        Returns:
            bool: True if unload successful and path clear
        """
        instance_num = get_instance_from_tool(tool_index)
        if instance_num < 0:
            self.gcode.respond_info(f"ACE: Tool {tool_index} not managed by any ACE instance")
            return False

        instance = self.instances[instance_num]
        local_slot = get_local_slot(tool_index, instance_num)

        # Check BOTH inventory AND hardware status
        inventory_status = instance.inventory[local_slot].get("status", "empty")
        hw_status = instance._info.get("slots", [{}] * instance.SLOT_COUNT)[local_slot].get("status", "empty")

        if inventory_status == "empty" and hw_status == "empty":
            self.gcode.respond_info(
                f"ACE[{instance_num}]: Slot {local_slot} already empty, skipping full unload"
            )
            return True

        if instance._feed_assist_index == local_slot:
            self.gcode.respond_info(
                f"ACE[{instance_num}]: Disabling feed assist on slot {local_slot}"
            )
            instance._disable_feed_assist(local_slot)

        total_length = instance.total_max_feeding_length
        retract_speed = instance.retract_speed

        self.gcode.respond_info(
            f"ACE[{instance_num}]: Full unload slot {local_slot} (fixed-length mode):\n"
            f"  Retracting: {total_length}mm\n"
            f"  Speed: {retract_speed}mm/s\n"
            f"  Expected time: {(total_length / retract_speed):.1f}s"
        )

        try:
            instance.wait_ready()
            instance._retract(local_slot, length=total_length, speed=retract_speed)

            self.gcode.respond_info(
                f"ACE[{instance_num}]: Retraction completed"
            )

            # **CONSISTENCY CHECK: Validate final state**
            has_rdm = self.has_rdm_sensor()

            if has_rdm:
                # Both sensors available - check both
                toolhead_clear = not self.get_instant_switch_state(SENSOR_TOOLHEAD)
                rdm_clear = not self.get_instant_switch_state(SENSOR_RDM)
                path_clear = toolhead_clear and rdm_clear

                if path_clear:
                    self.gcode.respond_info(
                        f"ACE[{instance_num}]: ✓ Full unload successful - path clear (both sensors)"
                    )
                    self.state.set(
                        "ace_filament_pos", FILAMENT_STATE_BOWDEN
                    )
                    return True
                else:
                    self.gcode.respond_info(
                        f"ACE[{instance_num}]: ⚠ Path still blocked after full unload:\n"
                        f"  Toolhead: {'BLOCKED' if not toolhead_clear else 'clear'}\n"
                        f"  RDM: {'BLOCKED' if not rdm_clear else 'clear'}"
                    )
                    return False
            else:
                # RDM not available - check only toolhead
                toolhead_clear = not self.get_instant_switch_state(SENSOR_TOOLHEAD)

                if toolhead_clear:
                    self.gcode.respond_info(
                        f"ACE[{instance_num}]: ✓ Full unload complete - toolhead sensor clear"
                    )
                    self.state.set(
                        "ace_filament_pos", FILAMENT_STATE_BOWDEN
                    )
                    return True
                else:
                    self.gcode.respond_info(
                        f"ACE[{instance_num}]: ⚠ Toolhead sensor still triggered after {total_length}mm retraction\n"
                        f"  (No RDM sensor for additional validation)"
                    )
                    return False

        except Exception as e:
            self.gcode.respond_info(
                f"ACE[{instance_num}]: Full unload failed: {e}"
            )
            return False
