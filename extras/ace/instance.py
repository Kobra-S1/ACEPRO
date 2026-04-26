"""
AceInstance: Manages a single physical ACE Pro unit.
"""

import logging
import time
import json
import copy
from .config import (
    INSTANCE_MANAGERS,
    SLOTS_PER_ACE,
    AceSlotStateMachineState,
    SENSOR_RDM,
    SENSOR_TOOLHEAD,
    FILAMENT_STATE_SPLITTER,
    FILAMENT_STATE_TOOLHEAD,
    FILAMENT_STATE_NOZZLE,
    RFID_STATE_NO_INFO,
    RFID_STATE_IDENTIFIED,
    MAX_RETRIES,
    get_tool_offset,
    create_inventory,
    create_status_dict,
    normalize_ace_slot_state,
)
from .protocol import create_protocol_adapter, normalize_protocol_name, resolve_protocol_name
from .serial_manager import AceSerialManager


class AceInstance:
    """Manages a single physical ACE Pro unit with 4 slots."""

    # Defaults for slots that report ready but provide no metadata
    DEFAULT_MATERIAL = "Unknown"
    DEFAULT_COLOR = [0, 0, 0]
    DEFAULT_TEMP = 0

    # Material temperature defaults (from RFID tags)
    MATERIAL_TEMPS = {
        "PLA": 200,
        "PLA+": 210,
        "PLA Glow": 210,
        "PLA High Speed": 215,
        "PLA Marble": 205,
        "PLA Matte": 205,
        "PLA SE": 210,
        "PLA Silk": 215,
        "ABS": 240,
        "ASA": 245,
        "PETG": 235,
        "TPU": 210,
        "PVA": 185,
        "HIPS": 230,
        "PC": 260,
    }

    def __init__(
        self,
        instance_num,
        ace_config,
        printer,
        ace_enabled=True,
        protocol=None,
        active_protocol_name=None,
        serial_mgr=None,
        bus_session=None,
    ):
        """
        Initialize ACE instance.

        Args:
            instance_num: Instance number (0, 1, 2, ...)
            ace_config: Configuration dict
            printer: Klipper printer object
            ace_enabled: Initial ACE Pro enabled state
        """
        self.variables = {}
        self.SLOT_COUNT = SLOTS_PER_ACE
        self.instance_num = instance_num
        self.ace_config = ace_config  # Store for later access (e.g., rfid_temp_mode)
        self.baud = ace_config["baud"]
        self.printer = printer
        self.reactor = printer.get_reactor()
        self.gcode = printer.lookup_object("gcode")
        self.timeout_multiplier = ace_config["timeout_multiplier"]
        self.filament_runout_sensor_name_rdm = ace_config["filament_runout_sensor_name_rdm"]
        self.filament_runout_sensor_name_nozzle = ace_config["filament_runout_sensor_name_nozzle"]
        self.feed_speed = float(ace_config["feed_speed"])
        self.retract_speed = float(ace_config["retract_speed"])
        self.total_max_feeding_length = float(ace_config["total_max_feeding_length"])
        self.parkposition_to_toolhead_length = float(ace_config["parkposition_to_toolhead_length"])
        self.toolchange_load_length = float(ace_config["toolchange_load_length"])
        self.parkposition_to_rdm_length = float(ace_config["parkposition_to_rdm_length"])
        self.incremental_feeding_length = float(ace_config["incremental_feeding_length"])
        self.incremental_feeding_speed = float(ace_config["incremental_feeding_speed"])
        self.extruder_feeding_length = float(ace_config["extruder_feeding_length"])
        self.extruder_feeding_speed = float(ace_config["extruder_feeding_speed"])
        self.toolhead_slow_loading_speed = float(ace_config["toolhead_slow_loading_speed"])
        self.heartbeat_interval = float(ace_config["heartbeat_interval"])
        self.max_dryer_temperature = float(ace_config["max_dryer_temperature"])

        self.rfid_inventory_sync_enabled = ace_config.get("rfid_inventory_sync_enabled", True)
        self.feed_assist_active_after_ace_connect = ace_config.get(
            "feed_assist_active_after_ace_connect", True
        )

        # Not overridable per instance
        self.toolhead_full_purge_length = float(ace_config["toolhead_full_purge_length"])

        self.toolhead = None
        self._info = create_status_dict(self.SLOT_COUNT)
        self.inventory = create_inventory(self.SLOT_COUNT)
        self._feed_assist_index = -1
        self._feed_assist_topology_position = None  # Track chain position (0, 1, 2...)
        self._pending_feed_assist_restore = -1  # Slot to restore after first heartbeat
        self._pending_rfid_refresh = False  # Flag to refresh all RFID data after reconnect
        self._dryer_active = False
        self._dryer_temperature = 0
        self._dryer_duration = 0
        self._pending_rfid_queries = set()  # Track slots with in-flight RFID queries
        self.status_failure_threshold = max(
            1,
            int(ace_config.get("status_failure_threshold", 4)),
        )
        self._status_failure_streak = 0
        self._status_recovery_in_progress = False

        self.status_debug_logging = bool(ace_config.get("status_debug_logging", False))
        self.supervision_enabled = bool(ace_config.get("ace_connection_supervision", True))
        self.configured_protocol_name = normalize_protocol_name(
            ace_config.get("protocol", "auto")
        )
        self.protocol_name = active_protocol_name or resolve_protocol_name(self.configured_protocol_name)
        self.protocol = protocol or create_protocol_adapter(self.protocol_name)
        self.transport_spec = self.protocol.get_transport_spec()
        self.bus_session = bus_session

        self.serial_mgr = serial_mgr or AceSerialManager(
            self.gcode,
            self.reactor,
            instance_num,
            ace_enabled=ace_enabled,
            status_debug_logging=self.status_debug_logging,
            supervision_enabled=self.supervision_enabled,
            protocol=self.protocol,
        )
        self.tool_offset = get_tool_offset(self.instance_num)
        if not self.transport_spec.shared_bus:
            self.serial_mgr.set_heartbeat_callback(self._on_heartbeat_response)
        self.serial_mgr.set_on_connect_callback(self._on_ace_connect)
        self._dryer_start_logged = False  # prevent duplicate dryer start messages
        self._shared_bus_heartbeat_timer = None

    def _prepare_request(self, request):
        """Normalize request and attach ACE2 shared-bus target when known."""
        prepared_request = self.protocol.normalize_request(request)
        if not self.transport_spec.shared_bus or self.bus_session is None:
            return prepared_request

        command_name = str(prepared_request.get("command", "")).strip().upper()
        if command_name in {"DISCOVER_DEVICE", "ASSIGN_DEVICE_ID"}:
            return prepared_request

        device = self.bus_session.get_device_for_instance(self.instance_num)
        if device is None or device.device_id is None:
            raise RuntimeError(
                f"ACE[{self.instance_num}]: Shared-bus request '{command_name or 'UNKNOWN'}' "
                "requires an assigned target device_id"
            )

        prepared_request["target_device_id"] = device.device_id
        return prepared_request

    @property
    def manager(self):
        """Get the AceManager instance for this ACE unit."""
        return INSTANCE_MANAGERS.get(self.instance_num)

    @property
    def state(self):
        """Shortcut to the centralised :class:`PersistentState`."""
        return self.manager.state

    def _register_tool_macros(self):
        """Register T0-T3 (or T4-T7, etc.) macros for this instance."""
        try:
            for slot_idx in range(self.SLOT_COUNT):
                tool_num = self.tool_offset + slot_idx

                # Create closure to capture current slot_idx
                def make_tool_handler(idx):
                    def handler(gcmd):
                        gcmd.respond_info(
                            f"ACE: Tool change to T{tool_num} "
                            f"(slot {idx}, instance {self.instance_num})"
                        )
                    return handler

                desc = f"ACE tool macro - slot {slot_idx} of instance {self.instance_num}"
                self.gcode.register_command(f"T{tool_num}", make_tool_handler(slot_idx), desc=desc)

            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Registered tool macros "
                f"T{self.tool_offset}-T{self.tool_offset + self.SLOT_COUNT - 1}"
            )
        except Exception as e:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Failed to register tool macros: {e}"
            )

    def send_request(self, request, callback):
        """Queue a normal request."""
        self.serial_mgr.send_request(
            self._prepare_request(request),
            callback,
        )

    def send_high_prio_request(self, request, callback):
        """Queue high-priority request."""
        self.serial_mgr.send_high_prio_request(
            self._prepare_request(request),
            callback,
        )

    def _send_shared_bus_heartbeat_request(self):
        """Send one targeted ACE2 status poll over shared transport."""
        if not self.transport_spec.shared_bus or not self.serial_mgr.is_connected():
            return

        request = self.protocol.build_get_status_request()
        self.send_high_prio_request(request, self._on_heartbeat_response)

    def _shared_bus_heartbeat_tick(self, eventtime):
        """Periodically poll one ACE2 logical instance on shared transport."""
        try:
            self._send_shared_bus_heartbeat_request()
        except Exception as exc:
            logging.warning(
                "ACE[%s]: Shared-bus heartbeat error: %s",
                self.instance_num,
                exc,
            )
        return eventtime + self.heartbeat_interval

    def start_shared_bus_heartbeat(self):
        """Start or refresh shared-bus status polling after bus init completes."""
        if not self.transport_spec.shared_bus:
            return

        self._send_shared_bus_heartbeat_request()
        if self._shared_bus_heartbeat_timer is None:
            self._shared_bus_heartbeat_timer = self.reactor.register_timer(
                self._shared_bus_heartbeat_tick,
                self.reactor.NOW,
            )

    def request_shared_bus_info_refresh(self):
        """Refresh device info through targeted ACE2 get_info after bus init."""
        if not self.transport_spec.shared_bus or not self.serial_mgr.is_connected():
            return

        request = self.protocol.build_get_info_request()
        self.send_high_prio_request(request, self.serial_mgr.handle_info_response)

    def _handle_rfid_info_response(self, slot_idx, response):
        """Apply a get_filament_info response to the local inventory."""
        self._pending_rfid_queries.discard(slot_idx)

        if response and response.get("code") == 0 and "result" in response:
            result = response["result"]

            # Check if actual RFID tag is present (not just non-RFID spool)
            rfid_state = result.get("rfid", 0)

            # Don't overwrite manual data for non-RFID spools or empty slots.
            if rfid_state != RFID_STATE_IDENTIFIED:
                logging.info(
                    f"ACE[{self.instance_num}]: Slot {slot_idx} - No RFID tag (rfid={rfid_state}), "
                    f"skipping inventory update to preserve manual data"
                )
                return

            sku = result.get("sku", "")
            brand = result.get("brand", "")
            material = result.get("type", "")
            icon_type = result.get("icon_type")
            colors_array = result.get("colors")
            rfid_color = None
            if colors_array and len(colors_array) > 0:
                first_color = (
                    colors_array[0]
                    if isinstance(colors_array[0], (list, tuple))
                    else colors_array
                )
                if len(first_color) >= 3:
                    rfid_color = [first_color[0], first_color[1], first_color[2]]
            else:
                direct_color = result.get("color")
                if (direct_color and len(direct_color) >= 3
                        and any(component > 0 for component in direct_color[:3])):
                    rfid_color = [direct_color[0], direct_color[1], direct_color[2]]
            extruder_temp = result.get("extruder_temp", {})
            hotbed_temp = result.get("hotbed_temp", {})
            diameter = result.get("diameter")
            total = result.get("total")
            current = result.get("current")

            temp_min = extruder_temp.get("min", 0)
            temp_max = extruder_temp.get("max", 0)
            temp_mode = self.ace_config.get("rfid_temp_mode", "average")

            if temp_min > 0 or temp_max > 0:
                if temp_mode == "min" and temp_min > 0:
                    rfid_temp = temp_min
                elif temp_mode == "max" and temp_max > 0:
                    rfid_temp = temp_max
                elif temp_min > 0 and temp_max > 0:
                    rfid_temp = (temp_min + temp_max) // 2
                elif temp_max > 0:
                    rfid_temp = temp_max
                else:
                    rfid_temp = temp_min
            else:
                rfid_temp = self.MATERIAL_TEMPS.get(material, self.DEFAULT_TEMP)

            if 0 <= slot_idx < self.SLOT_COUNT:
                inv = self.inventory[slot_idx]

                if material:
                    inv["material"] = material

                old_temp = inv.get("temp", 0)
                if rfid_temp != old_temp:
                    inv["temp"] = rfid_temp

                if rfid_color:
                    inv["color"] = rfid_color

                if sku:
                    inv["sku"] = sku
                if brand:
                    inv["brand"] = brand
                if icon_type is not None:
                    inv["icon_type"] = icon_type
                if extruder_temp:
                    inv["extruder_temp"] = extruder_temp
                if hotbed_temp:
                    inv["hotbed_temp"] = hotbed_temp
                if diameter is not None:
                    inv["diameter"] = diameter
                if total is not None:
                    inv["total"] = total
                if current is not None:
                    inv["current"] = current

                color_str = (
                    f"RGB({rfid_color[0]},{rfid_color[1]},{rfid_color[2]})"
                    if rfid_color else "none"
                )
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Slot {slot_idx} RFID full data -> "
                    f"sku={sku}, temp={rfid_temp}°C (min={temp_min}, max={temp_max}), "
                    f"color={color_str}, hotbed={hotbed_temp}, brand={brand}"
                )

                if self.manager:
                    self.manager._sync_inventory_to_persistent(self.instance_num, flush=False)
        else:
            msg = response.get("msg", "Unknown") if response else "No response"
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: get_filament_info failed for slot {slot_idx}: {msg}"
            )

    def handle_shared_bus_filament_info_response(self, response):
        """Replay a late shared-bus get_filament_info reply if the slot is still pending."""
        if not self.transport_spec.shared_bus:
            return False

        result = response.get("result") or {}
        slot_idx = result.get("index")
        if not isinstance(slot_idx, int) or slot_idx not in self._pending_rfid_queries:
            return False

        self._handle_rfid_info_response(slot_idx, response)
        return True

    def wait_ready(self, on_wait_cycle=None, timeout_s=60.0):
        """Wait for ACE unit to be ready with a hard timeout."""
        waited = 0.0
        interval = 0.5
        total_wait = 0.0

        while self._info.get("status") != "ready":
            self.reactor.pause(self.reactor.monotonic() + interval)
            waited += interval
            total_wait += interval

            if waited >= 25.0:
                self.send_high_prio_request(
                    request=self.protocol.build_get_status_request(),
                    callback=self._status_update_callback
                )
                waited = 0.0

            if on_wait_cycle is not None:
                on_wait_cycle()

            if total_wait >= timeout_s:
                raise TimeoutError(
                    f"ACE[{self.instance_num}]: wait_ready timed out after {timeout_s:.0f}s"
                )

    def is_ready(self):
        """Check if ACE is ready."""
        return self._info.get("status") == "ready"

    def _update_feed_assist(self, slot_index):
        """Update feed assist state: enable if slot >= 0, disable if -1."""
        if slot_index == -1:
            self._disable_feed_assist(slot_index)
        else:
            self._enable_feed_assist(slot_index)

    def _get_current_feed_assist_index(self):
        """Get the current feed assist slot index (-1 if disabled)."""
        return self._feed_assist_index

    def _is_slot_empty(self, slot_index):
        """Return True if ACE reports the given slot as empty."""
        for slot in self._info.get("slots", []):
            if slot.get("index") == slot_index:
                slot_status = normalize_ace_slot_state(slot.get("status"))
                return slot_status == AceSlotStateMachineState.EMPTY.value
        return False

    def _is_printing_or_paused(self):
        """Check if printer is in a printing or paused state.

        Returns:
            bool: True if printing or paused, False otherwise
        """
        print_stats = self.printer.lookup_object("print_stats", None)
        if not print_stats:
            return False

        try:
            stats = print_stats.get_status(self.reactor.monotonic())
            state = (stats.get("state") or "").lower()
            return state in ["printing", "paused"]
        except Exception:
            return False

    def _enable_feed_assist(self, slot_index):
        """Enable feed assist for smooth filament loading."""
        self._feed_assist_index = slot_index
        self._feed_assist_topology_position = self.serial_mgr.get_usb_topology_position()

        def callback(response):
            if response and response.get("code") == 0:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Feed assist enabled on slot {slot_index}"
                )
                self.state.set(
                    f"ace_feed_assist_index_{self.instance_num}",
                    slot_index
                )
            else:
                msg = response.get("msg", "Unknown") if response else ""
                logging.warning(
                    f"ACE[{self.instance_num}]: Feed assist enable failed: {msg}"
                )

        self.wait_ready()
        request = self.protocol.build_start_feed_assist_request(slot_index)
        self.send_request(request, callback)
        self.wait_ready()

    def _disable_feed_assist(self, slot_index):
        """Disable feed assist."""
        if self._feed_assist_index != slot_index:
            logging.warning(
                f"ACE[{self.instance_num}]: Feed assist not active on slot {slot_index}"
            )
            return

        self._feed_assist_index = -1
        self._feed_assist_topology_position = None

        def callback(response):
            if response and response.get("code") == 0:
                logging.info(
                    f"ACE[{self.instance_num}]: Feed assist disabled for slot {slot_index}"
                )
                self.state.set(
                    f"ace_feed_assist_index_{self.instance_num}",
                    -1
                )
            else:
                msg = response.get("msg", "Unknown") if response else ""
                logging.warning(
                    f"ACE[{self.instance_num}]: Feed assist disable failed: {msg}"
                )

        self.wait_ready()
        request = self.protocol.build_stop_feed_assist_request(slot_index)
        self.send_request(request, callback)
        self.dwell(1.0)
        self.wait_ready()

    def _feed(self, slot, length, speed, callback=None):
        """Feed filament from slot."""
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: _feed() -> slot={slot}, "
            f"length={length}mm, speed={speed}mm/s"
        )

        if callback is None:
            def callback(response):
                if response and response.get("msg") == "FORBIDDEN":
                    msg = f"ACE[{self.instance_num}]: Feed forbidden"
                    self.gcode.respond_info(f"{msg}: {response}")
                elif response and response.get("code", 0) != 0:
                    msg = f"ACE[{self.instance_num}]: Feed error: {response.get('msg')}"
                    self.gcode.respond_info(msg)

        request = self.protocol.build_feed_filament_request(slot, length, speed)
        self.gcode.respond_info(f"ACE[{self.instance_num}]: Sending request: {request}")
        self.send_request(request, callback)

    def _stop_feed(self, slot):
        """Stop feeding filament."""
        def callback(response):
            if response and response.get("code") != 0:
                msg = response.get("msg", "Unknown error")
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Stop feed error: {msg}"
                )
            elif response:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Stop feed successful: {response}"
                )

        request = self.protocol.build_stop_feed_filament_request(slot)
        self.send_high_prio_request(request, callback)

    def _make_sensor_trigger_monitor(self, sensor_type):
        """
        Create a sensor trigger time monitor callback.

        Args:
            sensor_type: SENSOR_TOOLHEAD or SENSOR_RDM
            expected_length: Expected retraction distance (mm)
            expected_speed: Expected retraction speed (mm/s)

        Returns:
            Callable that monitors sensor state changes and returns timing data
        """
        state_data = {
            "start_time": None,
            "trigger_time": None,
            "initial_state": None,
            "call_count": 0
        }

        def monitor():
            state_data["call_count"] += 1
            current_state = self.manager.get_switch_state(sensor_type)

            # First call - record initial state and start time
            if state_data["start_time"] is None:
                state_data["start_time"] = time.time()
                state_data["initial_state"] = current_state
                return

            # Sensor state changed - record trigger time
            if state_data["trigger_time"] is None and current_state != state_data["initial_state"]:
                state_data["trigger_time"] = time.time()

        # Return both the monitor function and the state data
        monitor.get_timing = lambda: (
            state_data["trigger_time"] - state_data["start_time"]
            if state_data["trigger_time"] and state_data["start_time"]
            else None
        )
        monitor.get_call_count = lambda: state_data["call_count"]
        monitor.state_data = state_data

        return monitor

    def _retract(self, slot, length, speed, on_retract_started=None, on_wait_for_ready=None):
        """
        Retract filament from slot with automatic retry on FORBIDDEN errors.

        Args:
            slot: Local slot index (0-3)
            length: Distance to retract (mm)
            speed: Retract speed (mm/s)

        Returns:
            dict: Response from ACE

        Raises:
            ValueError: If retraction fails after all retries
        """
        max_retries = MAX_RETRIES
        retry_delay_s = 2.0

        self.wait_ready()

        # If the slot already reports empty, there is nothing to retract.
        if self._is_slot_empty(slot):
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Retract skipped - slot {slot} is empty"
            )
            return {"code": 0, "msg": "Retract skipped: slot empty"}

        for attempt in range(1, max_retries + 1):
            ace_status_before = self._info.get('status', 'unknown')
            retract_start_time = time.time()
            early_stop_state = {"triggered": False, "elapsed": None}

            if attempt > 1:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: _retract() attempt {attempt}/{max_retries}:\n"
                    f"  Slot: {slot}\n"
                    f"  Length: {length}mm\n"
                    f"  Speed: {speed}mm/s\n"
                    f"  ACE status before: {ace_status_before}"
                )

            request = self.protocol.build_unwind_filament_request(slot, length, speed)

            response_container = {"response": None, "done": False}

            def check_slot_empty():
                if early_stop_state["triggered"]:
                    return
                if self._is_slot_empty(slot):
                    early_stop_state["triggered"] = True
                    early_stop_state["elapsed"] = time.time() - retract_start_time
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: Retract stopped after "
                        f"{early_stop_state['elapsed']:.2f}s - slot {slot} reports empty"
                    )
                    self._stop_retract(slot)

            def callback(response):
                response_container["response"] = response
                response_container["done"] = True

            self.send_request(request, callback)

            timeout = time.time() + 5.0
            while not response_container["done"] and time.time() < timeout:
                self.reactor.pause(self.reactor.monotonic() + 0.1)

            response = response_container["response"]
            ace_status_after = self._info.get('status', 'unknown')

            if not response:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: _retract() attempt {attempt}/{max_retries} - "
                    f"No response from ACE"
                )
                if attempt < max_retries:
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: Waiting {retry_delay_s}s before retry..."
                    )
                    self.reactor.pause(self.reactor.monotonic() + retry_delay_s)
                    continue
                else:
                    raise ValueError(
                        f"ACE[{self.instance_num}]: Retract failed - no response after "
                        f"{max_retries} attempts. Check ACE connection"
                    )

            result_code = response.get('code', -1)
            result_msg = response.get('msg', 'unknown')

            if result_code == 0 and result_msg != 'FORBIDDEN':
                # self.gcode.respond_info(
                #     f"ACE[{self.instance_num}]: _retract() command accepted on attempt {attempt}"
                # )

                # Execute callback immediately after retract command accepted
                if on_retract_started is not None:
                    try:
                        on_retract_started()
                    except Exception as cb_error:
                        self.gcode.respond_info(
                            f"ACE[{self.instance_num}]: Retract started callback error: {cb_error}"
                        )

                expected_time_s = length / speed
                dwell_time_s = expected_time_s

                # Waiting 1/4 of the expected time before waiting for ready to avoid any
                # timing issues with the ACE reporting ready inbetween gear shifting
                # Call callback during dwell in case sensor changes early
                dwell_end = time.time() + (dwell_time_s)
                while time.time() < dwell_end:
                    if on_wait_for_ready is not None:
                        on_wait_for_ready()
                    check_slot_empty()
                    if early_stop_state["triggered"]:
                        self.wait_ready()
                        return {"code": 0, "msg": "Retract stopped early: slot empty"}
                    self.reactor.pause(self.reactor.monotonic() + 0.2)

                def wait_cycle():
                    if on_wait_for_ready is not None:
                        on_wait_for_ready()
                    check_slot_empty()

                self.wait_ready(on_wait_cycle=wait_cycle)

                if early_stop_state["triggered"]:
                    return {"code": 0, "msg": "Retract stopped early: slot empty"}

                return response

            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Retract rejected - Attempt {attempt}/{max_retries}\n"
                f"  Response Code: {result_code}\n"
                f"  Response Message: '{result_msg}'\n"
                f"  Slot: {slot}\n"
                f"  Length: {length}mm\n"
                f"  Speed: {speed}mm/s\n"
                f"  ACE status before: {ace_status_before}\n"
                f"  ACE status after: {ace_status_after}"
            )

            if attempt < max_retries:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Waiting {retry_delay_s}s for ACE to finish..."
                )
                self.reactor.pause(self.reactor.monotonic() + retry_delay_s)

                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Verifying ACE is ready for retry {attempt + 1}..."
                )
            else:
                total_wait = max_retries * retry_delay_s
                raise ValueError(
                    f"ACE[{self.instance_num}]: Retract failed after {max_retries} attempts\n"
                    f"  Final Code: {result_code}\n"
                    f"  Final Message: '{result_msg}'\n"
                    f"  Slot: {slot}\n"
                    f"  Length: {length}mm\n"
                    f"  Speed: {speed}mm/s\n"
                    f"  Total wait time: {total_wait}s\n"
                    f"  Final ACE status: {ace_status_after}"
                )

        raise ValueError(
            f"ACE[{self.instance_num}]: Retract logic error - exhausted retries without exception"
        )

    def _stop_retract(self, slot):
        """Stop retracting filament."""
        def callback(response):
            if response and response.get("code") != 0:
                msg = response.get("msg", "Unknown error")
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Stop retract error: {msg}"
                )
            elif response:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Stop retract successful: {response}"
                )

        request = self.protocol.build_stop_unwind_filament_request(slot)
        self.send_request(request, callback)

    def _feed_to_toolhead_with_extruder_assist(self, local_slot, feed_length, feed_speed,
                                               extruder_feeding_length, extruder_feeding_speed):
        """
        Feed filament to toolhead using feed_sync + extruder assist.

        This is the LEGACY implementation that:
        - Uses _feed_sync (blocking) to start ACE feed
        - Polls toolhead sensor while pushing extruder in timed chunks
        - Stops feed when sensor triggers

        Args:
            local_slot: Slot index to feed from

        Returns:
            float: Total extruder distance pushed during assist

        Raises:
            ValueError: If feed command fails or sensor times out
        """
        self._disable_feed_assist(local_slot)
        self.execute_feed_with_retries(local_slot, feed_length, feed_speed)

        expected_time = feed_length / feed_speed
        timeout_s = expected_time * self.timeout_multiplier

        # Coordinated extruder nudges during ACE feed
        start_time = time.time()

        while not self.manager.get_switch_state(SENSOR_TOOLHEAD):
            now = time.time()
            if now - start_time > timeout_s:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Feed timeout for {feed_length}mm after {timeout_s} seconds"
                )
                break
            self.dwell(0.1)

        # Final sanity check
        if not self.manager.get_switch_state(SENSOR_TOOLHEAD):
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Toolhead sensor not triggered after feed. "
                f"Running extruder assist for up-to 60s..."
            )
            self._enable_feed_assist(local_slot)

            timeout = time.time() + 60.0
            while not self.manager.get_switch_state(SENSOR_TOOLHEAD) and time.time() < timeout:
                self.dwell(1)
            self._disable_feed_assist(local_slot)

            if not self.manager.get_switch_state(SENSOR_TOOLHEAD):
                raise ValueError(
                    f"ACE[{self.instance_num}]: Feeding filament to toolhead failed. "
                    f"Toolhead filament sensor is not triggering. Filament may be jammed."
                )
            else:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Toolhead sensor finally triggered after "
                    f"running feed-assist for 60s. Continuing..."
                )
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Slowing feedspeed down {extruder_feeding_speed:.2f} for toolhead load"
        )

        max_speed_change_retries = 3
        speed_changed = False
        while not speed_changed and (max_speed_change_retries > 0):
            speed_changed = self._change_feed_speed(local_slot, extruder_feeding_speed)
            self.dwell(delay=0.2)
            max_speed_change_retries -= 1

        if not speed_changed:
            self._stop_feed(local_slot)
            raise ValueError(
                f"ACE[{self.instance_num}]: Failed to change feed speed to "
                f"{extruder_feeding_speed}mm/s after multiple attempts"
            )

        self._extruder_move(extruder_feeding_length, extruder_feeding_speed, wait_for_move_end=True)
        self._stop_feed(local_slot)
        self.wait_ready()
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Switching from feeding to feed_assist mode"
        )
        self._enable_feed_assist(local_slot)
        self.wait_ready()

        return self.extruder_feeding_length

    def execute_feed_with_retries(self, local_slot, feed_length, feed_speed):
        max_retries = MAX_RETRIES
        for attempt in range(max_retries):
            response = self.feed_filament_with_wait_for_response(local_slot, feed_length, feed_speed)

            if response.get("code") == 0:
                break
            elif response.get("msg") == "FORBIDDEN" and attempt < max_retries - 1:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Feed FORBIDDEN, waiting 1s before retry "
                    f"(attempt {attempt + 1}/{max_retries})..."
                )
                self.dwell(delay=1.0)
            else:
                raise ValueError(
                    f"ACE[{self.instance_num}]: Feed failed: {response.get('msg')}"
                )

    def _feed_filament_into_toolhead(self, tool, check_pre_condition=True):
        """Feed filament from slot to toolhead sensor, then extruder to nozzle."""
        self.wait_ready()
        local_slot = tool - self.tool_offset

        if local_slot < 0 or local_slot >= self.SLOT_COUNT:
            raise ValueError(f"Tool {tool} not managed by this ACE instance.")

        if check_pre_condition:
            has_rdm = self.manager.has_rdm_sensor()

            if has_rdm and self.manager.get_switch_state(SENSOR_RDM):
                raise ValueError("Cannot feed, filament stuck in RMS")

            if self.manager.get_switch_state(SENSOR_TOOLHEAD):
                raise ValueError("Cannot feed, filament in nozzle")

        try:
            self._feed_to_toolhead_with_extruder_assist(
                local_slot,
                self.toolchange_load_length,
                self.feed_speed,
                self.extruder_feeding_length,
                self.extruder_feeding_speed
            )
        except Exception as e:
            # Perform your custom action here, e.g., log, cleanup, etc.
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Exception during feed to toolhead: {e}, "
                f"retracting filament 150mm back in case it got squished and stuck "
                f"in the filament-hub"
            )
            self._retract(local_slot, 150, self.retract_speed)

            raise  # Re-raise the original exception

        self.state.set(
            "ace_filament_pos",
            FILAMENT_STATE_TOOLHEAD
        )

        self.gcode.run_script_from_command("G92 E0")
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Feeding from sensor to nozzle..."
        )

        self._extruder_move(
            self.toolhead_full_purge_length,
            self.toolhead_slow_loading_speed
        )

        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()

        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Feeding from sensor to nozzle "
            f"({self.toolhead_full_purge_length}mm) finished"
        )
        self.state.set(
            "ace_filament_pos", FILAMENT_STATE_NOZZLE
        )

        return self.toolhead_full_purge_length

    def _change_retract_speed(self, slot, retract_speed):
        """
        Request to update active retract speed while unwind command is running.

        Returns:
            bool: True if firmware acknowledged, False otherwise
        """
        logging.info(
            f"ACE[{self.instance_num}]: Requesting retract speed change to "
            f"{retract_speed}mm/s (slot {slot})"
        )

        request = self.protocol.build_update_unwinding_speed_request(slot, retract_speed)
        response_container = {"response": None}

        def callback(response):
            response_container["response"] = response

        try:
            self.send_request(request, callback)
        except Exception as exc:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Retract speed change request error: {exc}"
            )
            return False

        timeout = time.time() + 3.0
        while response_container["response"] is None and time.time() < timeout:
            self.dwell(0.05)

        response = response_container["response"]
        if not response:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Retract speed change returned no response"
            )
            return False

        code = response.get("code", -1)
        msg = response.get("msg", "Unknown")
        if code != 0:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Retract speed change failed: "
                f"code={code}, msg='{msg}'"
            )
            return False

        return True

    def _change_feed_speed(self, slot, retract_speed):
        """
        Request to update active feed speed while feeding  is running.

        Returns:
            bool: True if firmware acknowledged, False otherwise
        """
        logging.info(
            f"ACE[{self.instance_num}]: Requesting feed speed change to "
            f"{retract_speed}mm/s (slot {slot})"
        )

        request = self.protocol.build_update_feeding_speed_request(slot, retract_speed)
        response_container = {"response": None}

        def callback(response):
            response_container["response"] = response

        try:
            self.send_request(request, callback)
        except Exception as exc:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Feed speed change request error: {exc}"
            )
            return False

        timeout = time.time() + 1.0
        while response_container["response"] is None and time.time() < timeout:
            self.dwell(0.05)

        response = response_container["response"]
        if not response:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Feed speed change returned no response"
            )
            return False

        code = response.get("code", -1)
        msg = response.get("msg", "Unknown")
        if code != 0:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Feed speed change failed: "
                f"code={code}, msg='{msg}'"
            )
            return False

        return True

    def feed_filament_with_wait_for_response(self, slot, length, speed):
        """
        Synchronously feed filament and wait for response.

        Returns:
            dict: Response from ACE
        """
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: feed_filament_with_wait_for_response() -> slot={slot}, "
            f"length={length}mm, speed={speed}mm/s"
        )

        response_container = {"response": None, "done": False}

        def callback(response):
            response_container["response"] = response
            response_container["done"] = True
            # if response:
            #     code = response.get("code", -1)
            #     msg = response.get("msg", "Unknown")
            #     self.gcode.respond_info(
            #         f"ACE[{self.instance_num}]: feed_filament_with_wait_for_response() response -> "
            #         f"code={code}, msg='{msg}'"
            #     )

        request = self.protocol.build_feed_filament_request(slot, length, speed)
        # self.gcode.respond_info(
        #     f"ACE[{self.instance_num}]: Sending sync request: {request}"
        # )

        self.wait_ready()
        self.send_request(request, callback)

        timeout = time.time() + 10.0
        poll_count = 0
        while not response_container["done"] and time.time() < timeout:
            self.dwell(delay=0.2)
            poll_count += 1

        if not response_container["done"]:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: feed_filament_with_wait_for_response() TIMEOUT after "
                f"{poll_count * 0.2:.1f}s - no response received"
            )
            return {"code": -1, "msg": "Feed command timeout - no response"}

        final_response = response_container["response"] or {"code": -1, "msg": "No response"}
        logging.info(
            f"ACE[{self.instance_num}]: feed_filament_with_wait_for_response() completed -> "
            f"slot={slot}, length={length}mm, result_code={final_response.get('code')}"
        )

        return final_response

    def _wait_for_condition(self, condition_fn, timeout_s, poll_interval=0.02):
        """
        Poll condition_fn until it returns True or timeout expires.

        Returns:
            float | None: Time (s) until condition met, or None on timeout
        """
        start = time.time()
        deadline = start + timeout_s

        while time.time() < deadline:
            if condition_fn():
                return time.time() - start
            self.dwell(poll_interval)

        return None

    def rmd_triggered_unload_slot(self, manager, slot, length, overshoot_length):
        """Unload slot with RDM sensor monitoring and overshoot compensation."""
        if manager.has_rdm_sensor():
            f_index = self._get_current_feed_assist_index()
            self._disable_feed_assist(slot)

            timeout_seconds = (length / self.retract_speed) * self.timeout_multiplier
            start_time = time.time()
            sensor_clear_time = None
            # Start retraction
            self._retract(slot, length, self.retract_speed)

            poll_interval = 0.02

            while (time.time() - start_time) < timeout_seconds:
                if manager.is_filament_path_free():
                    if sensor_clear_time is None:
                        sensor_clear_time = time.time() - start_time
                        overshoot_time = (overshoot_length / self.retract_speed) * self.timeout_multiplier

                        self.gcode.respond_info(
                            f"ACE[{self.instance_num}]: Sensor cleared "
                            f"after {sensor_clear_time:.2f}s, "
                            f"waiting {overshoot_time:.2f}s for overshoot"
                        )

                        self.dwell(overshoot_time)

                    self._stop_retract(slot)

                    elapsed = time.time() - start_time
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: RMD triggered unload slot {slot} "
                        f"completed in {elapsed:.2f}s"
                    )
                    self._update_feed_assist(f_index)
                    return True

                self.dwell(poll_interval)

            self._stop_retract(slot)
            self._update_feed_assist(f_index)
        return False

    def _smart_unload_slot(self, slot, length=100, on_retract_started=None):
        """
        Fixed-length retraction with optional sensor validation.

        **SIMPLIFIED MODE:**
        - Always retracts exactly 'length' mm (ignores overshoot_length)
        - No sensor polling during retraction
        - RDM sensor used only for post-retraction validation (if available)

        Args:
            slot: Slot index to retract from
            length: Retraction length in mm (exact distance)
            on_retract_started: Optional callback after retract starts

        Returns:
            bool: True if retraction completed successfully

        Raises:
            ValueError: If path still blocked after retraction (RDM available only)
        """
        has_rdm = self.manager.has_rdm_sensor()

        timeout_seconds = (length / self.retract_speed) * self.timeout_multiplier

        mode_str = "with RDM validation" if has_rdm else "toolhead-only mode"
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Fixed-length unload slot {slot} ({mode_str}):\n"
            f"  Length: {length}mm\n"
            f"  Speed: {self.retract_speed}mm/s\n"
            f"  Timeout: {timeout_seconds:.1f}s"
        )

        try:
            self._disable_feed_assist(slot)

            # Create sensor monitor for toolhead sensor (primary sensor for retraction)
            sensor_monitor = self._make_sensor_trigger_monitor(
                SENSOR_TOOLHEAD
            )

            start_time = time.time()

            # Start retraction with sensor monitoring
            self._retract(
                slot,
                length,
                self.retract_speed,
                on_retract_started,
                on_wait_for_ready=sensor_monitor
            )

            elapsed_s = time.time() - start_time
            sensor_trigger_time = sensor_monitor.get_timing()
            call_count = sensor_monitor.get_call_count()
            expected_time = length / self.retract_speed

            # Log retraction results with timing data
            if sensor_trigger_time is not None:
                sensor_efficiency = (sensor_trigger_time / expected_time * 100) if expected_time > 0 else 0
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Retraction completed in {elapsed_s:.2f}s "
                    f"(sensor: {sensor_trigger_time:.2f}s, {sensor_efficiency:.1f}% of expected, "
                    f"{call_count} polls)"
                )

                if sensor_efficiency > 90:
                    extra_length = self.parkposition_to_toolhead_length
                    extra_speed = self.retract_speed / 2
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: Suspicious late sensor trigger - "
                        f"Retracting extra parkposition_to_toolhead_length to ensure clear path: "
                        f"(Length: {extra_length:.2f}mm, "
                        f"{extra_speed:.1f}mm/s)"
                    )

                    self._retract(
                        slot,
                        extra_length,
                        extra_speed
                    )

            else:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Retraction completed in {elapsed_s:.2f}s "
                    f"(sensor did not change state, {call_count} polls)"
                )

            # Give klipper time to process any pending state changes and avoid reporting not updated sensor state
            self.dwell(1)
            # Consistency check: Validate with sensors (if RDM available)
            toolhead_clear = not self.manager.get_switch_state(SENSOR_TOOLHEAD)

            if has_rdm:
                rdm_clear = not self.manager.get_switch_state(SENSOR_RDM)
                path_clear = toolhead_clear and rdm_clear

                if path_clear:
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: ✓ Path clear (both sensors)"
                    )
                    return True
                else:
                    if not self.rmd_triggered_unload_slot(self.manager, slot, length, self.parkposition_to_rdm_length):
                        slot_status = self.inventory[slot].get("status", "unknown")
                        raise ValueError(
                            "ACE[%d]: ✗ Retraction failed - path still blocked after %.1fmm\n"
                            "  Slot: %d\n"
                            "  Spool status: %s\n"
                            "  Time: %.2fs\n"
                            "  RDM sensor: %s\n"
                            "  Toolhead sensor: %s\n"
                            "  → Manual intervention required"
                            % (
                                self.instance_num,
                                float(length),
                                slot,
                                slot_status,
                                float(elapsed_s),
                                "BLOCKED" if not rdm_clear else "clear",
                                "BLOCKED" if not toolhead_clear else "clear",
                            )
                        )
                    else:
                        self.gcode.respond_info(
                            f"ACE[{self.instance_num}]: ✓ RDM-triggered unload succeeded"
                        )
                        return True
            else:
                # No RDM - validate with toolhead sensor only
                if toolhead_clear:
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: ✓ Toolhead sensor clear"
                    )
                    return True
                else:
                    self.gcode.respond_info(
                        "ACE[%d]: ⚠ WARNING - Toolhead sensor still triggered after %.1fmm retraction\n"
                        "  This may indicate:\n"
                        "  - Insufficient retraction length\n"
                        "  - Filament stuck in path\n"
                        "  - Sensor malfunction\n"
                        "  Proceeding anyway (no RDM sensor for validation)"
                        % (self.instance_num, float(length))
                    )
                    return False
        except ValueError as e:
            # Validation error - already logged
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Smart unload validation failed: {e}"
            )
            try:
                self._stop_retract(slot)
            except Exception:
                pass
            raise

        except Exception as e:
            # Unexpected error
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Unexpected error during unload: {e}"
            )
            try:
                self._stop_retract(slot)
            except Exception:
                pass
            raise

    def _query_rfid_full_data(self, slot_idx):
        """
        Query full RFID tag data via get_filament_info.

        This gets the complete RFID data including:
        - extruder_temp: {min, max}
        - hotbed_temp: {min, max}
        - colors (RGBA array)
        - icon_type
        - diameter, total, current
        """
        # Prevent duplicate queries while one is in-flight
        if slot_idx in self._pending_rfid_queries:
            return
        self._pending_rfid_queries.add(slot_idx)

        def rfid_callback(response):
            self._handle_rfid_info_response(slot_idx, response)

        request = self.protocol.build_get_filament_info_request(slot_idx)
        self.send_request(request, rfid_callback)

    def start_drying(self, temperature, duration, callback=None):
        """Start the ACE dryer using the active protocol adapter."""
        self._dryer_active = True
        self._dryer_temperature = temperature
        self._dryer_duration = duration

        request = self.protocol.build_start_drying_request(temperature, duration)
        self.send_request(request, callback or (lambda response: None))

    def stop_drying(self, callback=None):
        """Stop the ACE dryer using the active protocol adapter."""
        self._dryer_active = False
        self._dryer_temperature = 0
        self._dryer_duration = 0

        request = self.protocol.build_stop_drying_request()
        self.send_request(request, callback or (lambda response: None))

    def _emit_inventory_update(self):
        """Emit JSON inventory update for KlipperScreen."""
        try:
            slots_out = []
            for i in range(self.SLOT_COUNT):
                inv = self.inventory[i]
                slot_data = {
                    "status": inv.get("status"),
                    "color": inv.get("color"),
                    "material": inv.get("material"),
                    "temp": inv.get("temp"),
                    "rfid": inv.get("rfid", False),
                }
                # Include optional RFID metadata fields if present
                for key in [
                    "sku",
                    "brand",
                    "icon_type",
                    "rgba",
                    "extruder_temp",
                    "hotbed_temp",
                    "diameter",
                    "total",
                        "current"]:
                    if key in inv:
                        slot_data[key] = inv[key]
                slots_out.append(slot_data)
            self.gcode.respond_info("// " + json.dumps({"instance": self.instance_num, "slots": slots_out}))
        except Exception:
            pass

    def _status_update_callback(self, response):
        """Handle status updates from ACE hardware."""
        inventory_changed = False
        feed_assist_was_active = self._feed_assist_index
        filament_loaded = False

        # LOG RAW status RESPONSE (only slots data for readability)
        # if response and "result" in response:
        #     slots_data = response.get("result", {}).get("slots", [])
        #     if slots_data:
        #         for slot in slots_data:
        #             idx = slot.get("index")
        #             rfid_val = slot.get("rfid")
        #             status_val = slot.get("status")
        #             self.gcode.respond_info(
        #                 f"ACE[{self.instance_num}]: Slot {idx} status RAW -> rfid={rfid_val}, status={status_val}"
        #             )

        if response and "result" in response:
            self._info = response["result"]

            # Handle pending RFID refresh after reconnect
            if self._pending_rfid_refresh:
                self._pending_rfid_refresh = False
                if self.rfid_inventory_sync_enabled:
                    # Unconditionally query all slots to catch any spool changes during disconnect
                    for slot_idx in range(self.SLOT_COUNT):
                        logging.info(
                            f"ACE[{self.instance_num}]: Reconnect - querying RFID data for slot {slot_idx}"
                        )
                        self._query_rfid_full_data(slot_idx)

            slots = self._info.get("slots", [])
            for slot in slots:
                idx = slot.get("index")
                if idx is not None and 0 <= idx < self.SLOT_COUNT:
                    # Get saved metadata (material/color/temp)
                    saved_color = self.inventory[idx].get("color", [0, 0, 0])
                    saved_material = self.inventory[idx].get("material", "")
                    saved_temp = self.inventory[idx].get("temp", 0)
                    saved_rfid = self.inventory[idx].get("rfid", False)

                    updated_color = saved_color
                    updated_material = saved_material
                    updated_temp = saved_temp
                    updated_rfid = None  # Will be set based on transition or status

                    # Get current states
                    old_status = self.inventory[idx].get("status")
                    new_status = normalize_ace_slot_state(
                        slot.get("status"),
                        default=AceSlotStateMachineState.EMPTY.value,
                    )

                    # Detect state changes
                    if old_status != new_status:
                        inventory_changed = True

                        # Log the state transition
                        if (
                            old_status == AceSlotStateMachineState.EMPTY.value
                            and new_status == AceSlotStateMachineState.READY.value
                        ):
                            # Check if the new spool is non-RFID (RFID_STATE_NO_INFO = 0)
                            rfid_state = slot.get("rfid")
                            if rfid_state == RFID_STATE_NO_INFO and saved_rfid:
                                # RFID → non-RFID transition: clear RFID-specific fields only
                                # (User may have replaced empty RFID spool with matching non-RFID spool)
                                # Keep material/color/temp for print continuity
                                updated_rfid = False
                                # Clear all optional RFID-specific fields from inventory
                                for key in [
                                    "sku",
                                    "brand",
                                    "icon_type",
                                    "extruder_temp",
                                    "hotbed_temp",
                                    "diameter",
                                    "total",
                                        "current"]:
                                    self.inventory[idx].pop(key, None)
                                # Clear query state
                                self._pending_rfid_queries.discard(idx)
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Slot {idx} RFID→non-RFID swap detected -> "
                                    f"empty → ready (clearing RFID fields, preserving material={saved_material})"
                                )
                            else:
                                # RFID spool (identifying/identified) OR same non-RFID refilled: preserve metadata
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Slot {idx} auto-restored: "
                                    f"empty → ready (material={saved_material})"
                                )
                            filament_loaded = True

                        elif (
                            old_status == AceSlotStateMachineState.READY.value
                            and new_status == AceSlotStateMachineState.EMPTY.value
                        ):
                            self.gcode.respond_info(
                                f"ACE[{self.instance_num}]: Slot {idx} marked empty "
                                f"(was: material={saved_material})"
                            )

                    # If slot is empty, clear RFID marker and metadata
                    if new_status == AceSlotStateMachineState.EMPTY.value:
                        updated_rfid = False
                        # Clear all optional RFID fields
                        for key in [
                            "sku",
                            "brand",
                            "icon_type",
                            "extruder_temp",
                            "hotbed_temp",
                            "diameter",
                            "total",
                                "current"]:
                            self.inventory[idx].pop(key, None)
                        self._pending_rfid_queries.discard(idx)

                        # If NOT printing/paused, also clear material/color/temp to defaults
                        # (For endless spool/runout during printing, we preserve this info)
                        if not self._is_printing_or_paused():
                            updated_material = ""
                            updated_color = [0, 0, 0]
                            updated_temp = 0
                            inventory_changed = True  # Force persistence update

                    # Handle RFID tag detection - only query get_filament_info, don't use status metadata
                    elif new_status == AceSlotStateMachineState.READY.value:
                        rfid_state = slot.get("rfid")

                        # When RFID sync enabled: only use data from get_filament_info callback
                        # Don't read material/color/sku from get_status - it may be stale
                        if self.rfid_inventory_sync_enabled and rfid_state == RFID_STATE_IDENTIFIED:
                            # Query on RFID state transition (false → detected)
                            # Skip if query is already in-flight
                            query_pending = idx in self._pending_rfid_queries

                            if not saved_rfid and not query_pending:
                                updated_rfid = True
                                # Query get_filament_info - callback will populate all metadata
                                self._query_rfid_full_data(idx)
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Slot {idx} RFID detected -> "
                                    f"querying get_filament_info..."
                                )
                                inventory_changed = True
                            else:
                                # RFID already detected - keep existing data
                                if updated_rfid is None:
                                    updated_rfid = saved_rfid
                        else:
                            # RFID sync disabled or no RFID: keep inventory as source of truth
                            if updated_rfid is None:
                                updated_rfid = saved_rfid

                        # If still missing metadata, fall back to defaults for ready slots
                        missing_material = not updated_material.strip()
                        missing_temp = updated_temp <= 0
                        missing_color = (
                            not updated_color
                            or len(updated_color) < 3
                            or all(c == 0 for c in updated_color[:3])
                        )

                        # When RFID sync is disabled AND the slot reports RFID data (rfid_state != RFID_STATE_NO_INFO),
                        # do not auto-fill defaults; leave values as-is to satisfy "ignore RFID data".
                        allow_default_fill = not (
                            not self.rfid_inventory_sync_enabled and rfid_state not in (
                                None, RFID_STATE_NO_INFO))

                        # Never overwrite with defaults while an RFID query is in-flight;
                        # the callback will populate the real data when it arrives.
                        rfid_query_in_flight = idx in self._pending_rfid_queries

                        if allow_default_fill and not rfid_query_in_flight and (missing_material or missing_temp):
                            # Check if we're actually changing anything before setting inventory_changed
                            needs_update = False
                            if missing_material and updated_material != self.DEFAULT_MATERIAL:
                                updated_material = self.DEFAULT_MATERIAL
                                needs_update = True
                            elif missing_material:
                                updated_material = self.DEFAULT_MATERIAL

                            if missing_temp and updated_temp != self.DEFAULT_TEMP:
                                updated_temp = self.DEFAULT_TEMP
                                needs_update = True
                            elif missing_temp:
                                updated_temp = self.DEFAULT_TEMP

                            if missing_color and updated_color != self.DEFAULT_COLOR:
                                updated_color = list(self.DEFAULT_COLOR)
                                needs_update = True
                            elif missing_color:
                                updated_color = list(self.DEFAULT_COLOR)

                            if needs_update:
                                inventory_changed = True
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Slot {idx} ready with no metadata -> "
                                    f"defaulting to {updated_material} {updated_temp}C, color {updated_color}"
                                )
                    else:
                        # Don't overwrite updated_rfid if it was already set
                        if updated_rfid is None:
                            updated_rfid = saved_rfid

                    # Final safety check: ensure updated_rfid has a value
                    if updated_rfid is None:
                        updated_rfid = saved_rfid

                    # Only write to inventory if values actually changed
                    inv = self.inventory[idx]
                    if (inv.get("status") != new_status or
                        inv.get("color") != updated_color or
                        inv.get("material") != updated_material or
                        inv.get("temp") != updated_temp or
                            inv.get("rfid") != updated_rfid):
                        inv["status"] = new_status
                        inv["color"] = updated_color
                        inv["material"] = updated_material
                        inv["temp"] = updated_temp
                        inv["rfid"] = updated_rfid

        # Persist changes if any status changed (deferred; flushed at print end)
        if inventory_changed:
            if self.manager:
                self.manager._sync_inventory_to_persistent(self.instance_num, flush=False)

            # Emit inventory update for KlipperScreen
            # self._emit_inventory_update()

            # Restore feed assist if it was active before filament loading
            # (ACE hardware disables feed assist when loading filament on any slot)
            if filament_loaded and feed_assist_was_active >= 0:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Re-enabling feed assist on slot "
                    f"{feed_assist_was_active} after automatic disable during slot loading"
                )
                try:
                    self._enable_feed_assist(feed_assist_was_active)
                except Exception as e:
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: Failed to restore feed assist: {e}"
                    )

    def _on_heartbeat_response(self, response):
        """Handle heartbeat response."""
        if response is None:
            self._record_status_failure("no response")
            return

        if response.get("code") == 0 and "result" in response:
            self._reset_status_failure_tracking()
            self._status_update_callback(response)

            # Restore pending feed assist after first successful heartbeat
            self._maybe_restore_pending_feed_assist()
        else:
            msg = response.get("msg", "Unknown error")
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Heartbeat response error: {msg}"
            )
            self._record_status_failure(str(msg))

    def _reset_status_failure_tracking(self):
        """Clear heartbeat/status failure tracking after successful communication."""
        self._status_failure_streak = 0
        self._status_recovery_in_progress = False

    def _record_status_failure(self, reason):
        """Track one failed heartbeat/status response and trigger reconnect if needed."""
        self._status_failure_streak += 1
        if self._status_failure_streak < self.status_failure_threshold:
            return
        if self._status_recovery_in_progress:
            return

        self._status_recovery_in_progress = True
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Heartbeat/status failed "
            f"{self._status_failure_streak} times ({reason}) - reconnecting"
        )

        reconnect = getattr(self.serial_mgr, "reconnect", None)
        if callable(reconnect):
            try:
                reconnect()
                return
            except Exception as exc:
                logging.warning(
                    "ACE[%s]: reconnect() after status failures failed: %s",
                    self.instance_num,
                    exc,
                )

        disconnect = getattr(self.serial_mgr, "disconnect", None)
        if callable(disconnect):
            try:
                disconnect()
            except Exception as exc:
                logging.warning(
                    "ACE[%s]: disconnect() after status failures failed: %s",
                    self.instance_num,
                    exc,
                )

    def _maybe_restore_pending_feed_assist(self):
        """
        Restore feed assist if pending after reconnect.

        Called after first successful heartbeat to ensure connection is stable.
        Only restores if reconnecting to same position in daisy chain (topology).
        """
        if self._pending_feed_assist_restore < 0:
            return

        slot = self._pending_feed_assist_restore
        self._pending_feed_assist_restore = -1  # Clear before attempting

        # Check if we're at the same position in the daisy chain
        current_position = self.serial_mgr.get_usb_topology_position()

        if self._feed_assist_topology_position is not None and current_position != self._feed_assist_topology_position:
            # Connected to different position in chain, don't restore
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Skipping feed assist restore - "
                f"different chain position (was: {self._feed_assist_topology_position}, "
                f"now: {current_position})"
            )
            self._feed_assist_index = -1  # Clear stale state
            self._feed_assist_topology_position = None
            return

        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Restoring feed assist on slot {slot} "
            f"(chain position: {current_position})"
        )
        try:
            # Use short timeout - if ACE is busy (e.g., RFID read/preload), skip restoration
            # Feed assist can be restored later or manually if needed
            self.wait_ready(timeout_s=5.0)
            request = self.protocol.build_start_feed_assist_request(slot)
            self.send_request(
                request,
                lambda response: self._on_feed_assist_restore_response(response, slot)
            )
        except TimeoutError:
            # ACE busy (likely RFID read/preload) - skip restoration, don't cascade timeouts
            logging.info(
                f"ACE[{self.instance_num}]: Skipping feed assist restoration on slot {slot} "
                f"(ACE busy, will retry on next status update)"
            )
        except Exception as e:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Failed to restore feed assist: {e}"
            )

    def _on_ace_connect(self):
        """
        Handle ACE connection/reconnection.

        Refreshes RFID data for all slots with detected tags to catch any
        spool changes that occurred while disconnected.

        Defers feed assist restoration and RFID refresh until after first
        successful status update to ensure connection is stable.
        """
        self._reset_status_failure_tracking()

        # Set flag to refresh RFID data on next status update
        # This ensures we have current data if spools were changed during disconnect
        self._pending_rfid_refresh = True
        logging.info(
            f"ACE[{self.instance_num}]: Connected - will refresh RFID data after first status update"
        )

        if not self.feed_assist_active_after_ace_connect:
            logging.info(
                f"ACE[{self.instance_num}]: Connected - feed assist restoration disabled"
            )
            return

        # Check if feed assist was active before disconnect
        if self._feed_assist_index >= 0:
            slot = self._feed_assist_index
            # Defer restoration until after first heartbeat confirms communication
            self._pending_feed_assist_restore = slot
            logging.info(
                f"ACE[{self.instance_num}]: Connected - "
                f"will restore feed assist on slot {slot} after heartbeat"
            )
        else:
            logging.info(
                f"ACE[{self.instance_num}]: Connected - "
                f"no previous feed assist to restore"
            )

    def _on_feed_assist_restore_response(self, response, slot):
        """Handle response from feed assist restoration after reconnect."""
        if response and response.get("code") == 0:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Feed assist restored on slot {slot}"
            )
        else:
            msg = response.get("msg", "Unknown") if response else "No response"
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Feed assist restoration failed: {msg}"
            )

    def get_status(self, eventtime=None):
        """Return status dict for Klipper/Moonraker queries."""
        # Debug logging reserved for status_debug_logging; keep silent by default

        status = copy.deepcopy(self._info)
        status.pop("raw_fields", None)
        status["instance"] = self.instance_num
        status["protocol"] = self.protocol_name
        status["rfid_sync_enabled"] = bool(self.rfid_inventory_sync_enabled)
        status["feed_assist_slot"] = self._get_current_feed_assist_index()

        # Attach device info from last get_info response, if available
        device_info = getattr(self.serial_mgr, "device_info", {})
        if isinstance(device_info, dict):
            normalized_info = {}
            for key in ("model", "firmware", "boot_firmware", "structure_version"):
                if key in device_info and device_info[key] is not None:
                    normalized_info[key] = device_info[key]

            # ACE2 get_info currently reports version and boot_version keys.
            if "firmware" not in normalized_info and device_info.get("version"):
                normalized_info["firmware"] = device_info["version"]
            if "boot_firmware" not in normalized_info and device_info.get("boot_version"):
                normalized_info["boot_firmware"] = device_info["boot_version"]

            for key, value in normalized_info.items():
                status.setdefault(key, value)

        # Protocol-aware fallback label when the device does not report model metadata.
        if not status.get("model") and self.protocol_name == "ace2_proto":
            port_description = getattr(self.serial_mgr, "_port_description", None)
            if port_description:
                status["model"] = f"ACE2 ({port_description})"
            else:
                status["model"] = "ACE2 (Shared Bus)"
        # Attach connection info (port / usb path) for diagnostics
        port = getattr(self.serial_mgr, "serial_name", None) or getattr(self.serial_mgr, "_port", None)
        usb_path = getattr(self.serial_mgr, "_usb_location", None)
        if port:
            status.setdefault("usb_port", port)
        if usb_path:
            status.setdefault("usb_path", usb_path)

        # Expose UI-friendly slot inventory (material/color/temp/status/RFID metadata)
        slots_out = []
        for i in range(self.SLOT_COUNT):
            inv = self.inventory[i]
            slot_data = {
                "index": i,
                "tool": self.tool_offset + i,
                "status": inv.get("status"),
                "color": inv.get("color"),
                "material": inv.get("material"),
                "temp": inv.get("temp"),
                "rfid": inv.get("rfid", False),
            }
            for key in [
                "sku",
                "brand",
                "icon_type",
                "rgba",
                "extruder_temp",
                "hotbed_temp",
                "diameter",
                "total",
                "current",
            ]:
                if key in inv:
                    slot_data[key] = inv[key]
            slots_out.append(slot_data)

        status["slots"] = slots_out

        # Expose communication state machine for KlipperScreen connection indicator
        status["connection_state"] = getattr(self.serial_mgr, "connection_state", "unknown")

        return status

    def dwell(self, delay=1.0, verbose=False):
        """Sleep in reactor time."""
        start_time = time.time()
        currTs = self.reactor.monotonic()
        self.reactor.pause(currTs + delay)
        actual_delay = time.time() - start_time

        if verbose and delay > 0.5:  # Only log significant delays
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Dwell complete: "
                f"requested={delay:.2f}s, actual={actual_delay:.2f}s"
            )

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

    def reset_persistent_inventory(self):
        """Reset persistent inventory to empty slots."""
        self.inventory = create_inventory(self.SLOT_COUNT)
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Persistent inventory reset to empty"
        )

    def reset_feed_assist_state(self):
        """Reset feed assist state."""
        if self._feed_assist_index != -1:
            self._feed_assist_index = -1
            self.state.set(
                f"ace_feed_assist_index_{self.instance_num}",
                -1
            )

            self.gcode.respond_info(f"ACE[{self.instance_num}]: Feed assist state reset")

    def _feed_filament_to_verification_sensor(self, slot, target_sensor, feed_length):
        """
        Feed filament from slot until target sensor triggers.

        Args:
            slot: Slot index to feed from
            target_sensor: SENSOR_RDM or SENSOR_TOOLHEAD
            feed_length: Max feed distance to sensor (mm)

        Raises:
            ValueError: If feeding fails or sensors are in wrong state
        """
        target_sensor_name = "RDM" if target_sensor == SENSOR_RDM else "toolhead"

        logging.info(
            f"ACE[{self.instance_num}]: Feeding to {target_sensor_name} sensor "
            f"(length={feed_length}mm)"
        )

        self.wait_ready()

        # Pre-check: target sensor must be clear
        if self.manager.get_switch_state(target_sensor):
            raise ValueError(
                f"ACE[{self.instance_num}]: Cannot feed, filament already at {target_sensor_name} sensor"
            )

        # Start feeding
        self._feed(slot, feed_length, self.feed_speed)

        # Wait for target sensor to trigger
        expected_time = feed_length / self.feed_speed
        timeout_s = expected_time * self.timeout_multiplier
        start_time = time.time()

        while not self.manager.get_switch_state(target_sensor):
            if time.time() - start_time > timeout_s:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Feed timeout after {timeout_s:.1f}s "
                    f"(target: {target_sensor_name})"
                )
                break
            self.dwell(0.01)

        self._stop_feed(slot)

        # Incremental feeding if sensor not reached
        accumulated_feed_length = feed_length

        if not self.manager.get_switch_state(target_sensor):

            while (not self.manager.get_switch_state(target_sensor) and
                   accumulated_feed_length < self.total_max_feeding_length):
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Incremental feed to {target_sensor_name} "
                    f"({self.incremental_feeding_length}mm at "
                    f"{self.incremental_feeding_speed}mm/s)"
                )

                self._feed(slot, self.incremental_feeding_length, self.incremental_feeding_speed)

                accumulated_feed_length += self.incremental_feeding_length

                self.dwell((self.incremental_feeding_length / self.incremental_feeding_speed) + 0.1)

            if not self.manager.get_switch_state(target_sensor):
                raise ValueError(
                    f"ACE[{self.instance_num}]: Fed {accumulated_feed_length}mm, "
                    f"but {target_sensor_name} sensor not triggered"
                )

            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Filament reached {target_sensor_name} sensor after feeding "
                f"{accumulated_feed_length}mm. Consider updating 'feed_length' if needed."
            )

        self.wait_ready()

        # Set final filament position state
        if target_sensor == SENSOR_RDM:
            self.state.set(
                "ace_filament_pos",
                FILAMENT_STATE_SPLITTER
            )
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Filament position set to splitter (at RDM)"
            )
        else:
            self.state.set(
                "ace_filament_pos",
                FILAMENT_STATE_TOOLHEAD
            )
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Filament position set to toolhead"
            )
