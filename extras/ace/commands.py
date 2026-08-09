"""
Gcode command handlers for ACE Pro module.

All commands are registered globally and dispatched to appropriate
instances based on INSTANCE parameter or tool mapping.
"""

import json
import traceback
import logging

from .version import get_driver_version
from .config import (
    ACE_INSTANCES,
    INSTANCE_MANAGERS,
    SENSOR_TOOLHEAD,
    SENSOR_RDM,
    FILAMENT_STATE_BOWDEN,
    FILAMENT_STATE_SPLITTER,
    FILAMENT_STATE_TOOLHEAD,
    FILAMENT_STATE_NOZZLE,
    RFID_STATE_NO_INFO,
    RFID_STATE_FAILED,
    RFID_STATE_IDENTIFIED,
    RFID_STATE_IDENTIFYING,
    get_instance_from_tool,
    get_local_slot,
    OVERRIDABLE_PARAMS,
)


def get_printer():
    """Get the Klipper printer object from the first AceManager."""
    if len(INSTANCE_MANAGERS) == 0:
        raise RuntimeError("No ACE instances available to get printer")
    manager = list(INSTANCE_MANAGERS.values())[0]
    return manager.get_printer()


def ace_get_instance(gcmd):
    """
    Resolve which ACE instance to use for a command.

    Priority:
    1. INSTANCE= parameter if given
    2. TOOL= parameter to map to instance
    3. Fallback to instance 0
    """
    params = gcmd.get_command_parameters()

    if "INSTANCE" in params:
        instance_id = gcmd.get_int("INSTANCE")
        ace = ACE_INSTANCES.get(instance_id)
        if ace is None:
            raise gcmd.error(f"No ACE instance {instance_id}")
        return ace

    if "TOOL" in params:
        tool = gcmd.get_int("TOOL")
        inst_num = get_instance_from_tool(tool)
        if inst_num < 0:
            raise gcmd.error(f"No ACE instance manages tool {tool}")
        ace = ACE_INSTANCES.get(inst_num)
        if ace is None:
            raise gcmd.error(f"Instance {inst_num} not available")
        return ace

    ace = ACE_INSTANCES.get(0)
    if ace is None:
        raise gcmd.error("No ACE instances configured")
    return ace


def rgb_to_mainsail_color(r, g, b):
    """
    Map RGB values to closest Mainsail color name using HSV heuristics.

    Returns one of: 'primary', 'secondary', 'accent', 'info', 'success', 'error', 'warning'

    Algorithm:
    1. Check for grayscale/low saturation → 'secondary'
    2. Use Hue to determine color family:
       - Red (0-30°, 330-360°) → 'error'
       - Orange/Yellow (30-60°) → 'warning'
       - Green (60-150°) → 'success'
       - Cyan (150-210°) → 'info'
       - Blue (210-270°) → 'primary'
       - Magenta (270-330°) → 'accent'
    """
    # Normalize to 0-1 range
    r_norm = r / 255.0
    g_norm = g / 255.0
    b_norm = b / 255.0

    # Calculate HSV
    max_val = max(r_norm, g_norm, b_norm)
    min_val = min(r_norm, g_norm, b_norm)
    delta = max_val - min_val

    # Value (brightness)
    value = max_val

    # Saturation
    saturation = 0 if max_val == 0 else delta / max_val

    # Check for grayscale (low saturation)
    # Increased threshold to 30% to catch bluish-grey, greenish-grey, etc.
    if saturation < 0.30:
        # Very bright = white (no color class, shows as terminal default)
        if value > 0.85:
            return None  # White - use terminal default color
        # Very dark = black
        elif value < 0.15:
            return 'secondary'  # Black/dark gray
        # Mid-range = gray
        else:
            return 'secondary'  # Gray

    # Calculate Hue (0-360)
    if delta == 0:
        hue = 0
    elif max_val == r_norm:
        hue = 60 * (((g_norm - b_norm) / delta) % 6)
    elif max_val == g_norm:
        hue = 60 * (((b_norm - r_norm) / delta) + 2)
    else:
        hue = 60 * (((r_norm - g_norm) / delta) + 4)

    # Map hue to Mainsail color
    if hue < 30 or hue >= 330:
        return 'error'      # Red
    elif hue < 60:
        return 'warning'    # Orange/Yellow
    elif hue < 150:
        return 'success'    # Green
    elif hue < 210:
        return 'info'       # Cyan
    elif hue < 270:
        return 'primary'    # Blue
    else:
        return 'accent'     # Magenta/Pink


def ace_get_manager(instance_num=0):
    """Get the AceManager for an instance."""
    return INSTANCE_MANAGERS[instance_num]


def for_each_instance(callback):
    """
    Execute callback for each ACE instance.

    Args:
        callback: Function(inst_num, manager, instance) to execute for each instance
    """
    for inst_num in sorted(INSTANCE_MANAGERS.keys()):
        manager = INSTANCE_MANAGERS[inst_num]
        instance = manager.instances[inst_num]
        callback(inst_num, manager, instance)


def validate_feed_and_retract_arguments(gcmd, ace, slot, length, speed):
    """Validate common arguments for feed and retract operations."""
    if not (0 <= slot < ace.SLOT_COUNT):
        raise gcmd.error(f"Invalid slot {slot}")

    if length <= 0:
        raise gcmd.error(f"LENGTH must be positive, got {length}")

    if speed <= 0:
        raise gcmd.error(f"SPEED must be positive, got {speed}")


def safe_gcode_command(func):
    """
    Decorator: Wrap gcode commands to catch all exceptions.

    Prevents Klipper shutdown by catching exceptions and logging them.
    """
    def wrapper(gcmd):
        try:
            return func(gcmd)
        except Exception as e:
            traceback.print_exc()
            error_msg = f"ACE ERROR: {type(e).__name__}: {e}"
            gcmd.respond_info(error_msg)

            try:
                printer = get_printer()
                gcode = printer.lookup_object("gcode")

                gcode.run_script_from_command(
                    'RESPOND TYPE=command MSG="action:prompt_begin ACE Error"'
                )

                error_text = str(e).replace('"', '\\"')
                exception_type = type(e).__name__
                prompt_text = (
                    f"An error occurred in ACE command: {func.__name__} - Error Type: {exception_type} - "
                    f"{error_text} - Check the console for detailed traceback."
                )

                gcode.run_script_from_command(
                    f'RESPOND TYPE=command MSG="action:prompt_text {prompt_text}"'
                )

                gcode.run_script_from_command(
                    'RESPOND TYPE=command MSG="action:prompt_footer_button Continue|'
                    'RESPOND TYPE=command MSG=action:prompt_end|secondary"'
                )

                gcode.run_script_from_command(
                    'RESPOND TYPE=command MSG="action:prompt_show"'
                )

            except Exception as dialog_error:
                print(f"Failed to show error dialog: {dialog_error}")

    return wrapper


def cmd_ACE_GET_STATUS(gcmd):
    """Query ACE status. INSTANCE= or TOOL= (omit to query all instances). VERBOSE=1 for detailed output."""
    try:
        instance_num = gcmd.get_int("INSTANCE", None)
        verbose = gcmd.get_int("VERBOSE", 0)

        def build_status_request(ace_instance):
            """Build one protocol-correct status request without leaking transport details here."""
            return ace_instance.protocol.build_get_status_request()

        def format_verbose_status(result, inst_num, ace_instance=None):
            """Format all status information in a readable, grouped format."""
            lines = []
            lines.append(f"=== ACE Instance {inst_num} Status ===")

            # Track which keys we've explicitly handled
            handled_keys = set()

            # ACE2 responses carry raw protobuf debug data (bytes values,
            # not JSON-serializable and unreadable in gcode output)
            handled_keys.add('raw_fields')

            # Main status and temperature
            lines.append(f"Status: {result.get('status', 'unknown')}")
            handled_keys.add('status')
            lines.append(f"Current Temperature: {result.get('temp', 0)}°C")
            handled_keys.add('temp')

            # Dryer status (all on one line) - check both 'dryer' and 'dryer_status'
            dryer = result.get('dryer_status') or result.get('dryer', {})
            if dryer:
                handled_keys.add('dryer_status')
                handled_keys.add('dryer')
                dryer_parts = []
                dryer_handled = set()

                # Known dryer fields
                if 'status' in dryer:
                    dryer_parts.append(f"status={dryer['status']}")
                    dryer_handled.add('status')
                if 'target_temp' in dryer:
                    dryer_parts.append(f"target_temp={dryer['target_temp']}°C")
                    dryer_handled.add('target_temp')
                if 'duration' in dryer:
                    dryer_parts.append(f"duration={dryer['duration']}min")
                    dryer_handled.add('duration')
                if 'remain_time' in dryer:
                    dryer_parts.append(f"remain_time={dryer['remain_time']}min")
                    dryer_handled.add('remain_time')

                # Any unknown dryer fields
                for key, value in dryer.items():
                    if key not in dryer_handled:
                        dryer_parts.append(f"{key}={value}")

                dryer_line = f"Dryer: {', '.join(dryer_parts)}"
                lines.append(dryer_line)

            # RFID and fan status
            if 'enable_rfid' in result:
                lines.append(f"RFID Enabled: {result['enable_rfid']}")
                handled_keys.add('enable_rfid')
            if 'fan_speed' in result:
                lines.append(f"Fan Speed: {result['fan_speed']} RPM")
                handled_keys.add('fan_speed')

            # Feed assist status
            if 'feed_assist_count' in result:
                lines.append(f"Feed Assist Count: {result['feed_assist_count']}")
                handled_keys.add('feed_assist_count')
            if 'cont_assist_time' in result:
                lines.append(f"Continuous Assist Time: {result['cont_assist_time']}s")
                handled_keys.add('cont_assist_time')

            # Slot information (one line per slot)
            slots = result.get('slots', [])
            if slots:
                handled_keys.add('slots')
                lines.append(f"\nSlots ({len(slots)} total):")
                for slot in slots:
                    slot_handled = set()
                    slot_parts = []

                    # Get slot index for inventory lookup
                    slot_idx = slot.get('index')
                    inv_data = {}
                    if ace_instance is not None and slot_idx is not None:
                        if 0 <= slot_idx < len(ace_instance.inventory):
                            inv_data = ace_instance.inventory[slot_idx]

                    # Known slot fields in preferred order
                    if 'index' in slot:
                        idx = slot['index']
                        slot_parts.append(f"index={idx}")
                        slot_handled.add('index')
                    if 'status' in slot:
                        slot_parts.append(f"status={slot['status']}")
                        slot_handled.add('status')
                    if 'sku' in slot:
                        sku = slot['sku']
                        if sku:  # Only show if not empty
                            slot_parts.append(f"sku={sku}")
                        slot_handled.add('sku')
                    if 'type' in slot:
                        slot_type = slot['type']
                        if slot_type:  # Only show if not empty
                            slot_parts.append(f"type={slot_type}")
                        slot_handled.add('type')
                    if 'color' in slot:
                        color = slot['color']
                        if isinstance(color, list) and len(color) >= 3:
                            slot_parts.append(f"color=RGB({color[0]},{color[1]},{color[2]})")
                        else:
                            slot_parts.append(f"color={color}")
                        slot_handled.add('color')
                    if 'rfid' in slot:
                        rfid_val = slot['rfid']
                        # Format RFID nicely with named constants
                        rfid_labels = {
                            RFID_STATE_NO_INFO: "no_tag",
                            RFID_STATE_FAILED: "failed",
                            RFID_STATE_IDENTIFIED: "identified",
                            RFID_STATE_IDENTIFYING: "identifying"
                        }
                        rfid_str = rfid_labels.get(rfid_val, str(rfid_val))
                        slot_parts.append(f"rfid={rfid_str}")
                        slot_handled.add('rfid')
                    if 'icon_type' in slot:
                        slot_parts.append(f"icon_type={slot['icon_type']}")
                        slot_handled.add('icon_type')
                    if 'colors' in slot:
                        # colors is an array of RGBA arrays - format compactly
                        colors = slot['colors']
                        if isinstance(colors, list) and colors:
                            if len(colors) == 1 and len(colors[0]) >= 3:
                                # Single color - show as RGBA
                                c = colors[0]
                                slot_parts.append(f"rgba=RGBA({c[0]},{c[1]},{c[2]},{c[3] if len(c) > 3 else 255})")
                            else:
                                # Multiple colors - show count
                                slot_parts.append(f"colors={len(colors)}_colors")
                        slot_handled.add('colors')

                    # Add RFID temperature data from inventory (if available)
                    if inv_data:
                        extruder_temp = inv_data.get('extruder_temp')
                        if extruder_temp and isinstance(extruder_temp, dict):
                            t_min = extruder_temp.get('min', 0)
                            t_max = extruder_temp.get('max', 0)
                            if t_min > 0 or t_max > 0:
                                slot_parts.append(f"extruder_temp={t_min}-{t_max}°C")

                        hotbed_temp = inv_data.get('hotbed_temp')
                        if hotbed_temp and isinstance(hotbed_temp, dict):
                            b_min = hotbed_temp.get('min', 0)
                            b_max = hotbed_temp.get('max', 0)
                            if b_min > 0 or b_max > 0:
                                slot_parts.append(f"hotbed_temp={b_min}-{b_max}°C")

                        diameter = inv_data.get('diameter')
                        if diameter is not None and diameter > 0:
                            slot_parts.append(f"diameter={diameter}mm")

                        total = inv_data.get('total')
                        current = inv_data.get('current')
                        if total is not None and total > 0:
                            if current is not None:
                                slot_parts.append(f"spool={current}/{total}m")
                            else:
                                slot_parts.append(f"spool_total={total}m")

                    # Any unknown slot fields (default=repr: ACE2 payloads
                    # may contain non-JSON-serializable values like bytes)
                    for key, value in slot.items():
                        if key not in slot_handled:
                            if isinstance(value, (dict, list)):
                                slot_parts.append(f"{key}={json.dumps(value, default=repr)}")
                            else:
                                slot_parts.append(f"{key}={value}")

                    slot_line = f"  Slot: {', '.join(slot_parts)}"
                    lines.append(slot_line)

            # Catch any unknown top-level keys (default=repr: ACE2 payloads
            # may contain non-JSON-serializable values like bytes)
            unknown_keys = []
            for key, value in result.items():
                if key not in handled_keys:
                    if isinstance(value, (dict, list)):
                        unknown_keys.append(f"{key}={json.dumps(value, default=repr)}")
                    else:
                        unknown_keys.append(f"{key}={value}")

            if unknown_keys:
                lines.append("\nAdditional Fields:")
                for item in unknown_keys:
                    lines.append(f"  {item}")

            return "\n".join(lines)

        if instance_num is not None:
            ace = ace_get_instance(gcmd)

            def status_callback(response):
                if response and response.get("code") == 0:
                    result = response.get("result", {})
                    inst_num = ace.instance_num if hasattr(ace, "instance_num") else 0

                    if verbose:
                        # Verbose output: all information nicely formatted
                        formatted = format_verbose_status(result, inst_num, ace)
                        gcmd.respond_info(formatted)
                    else:
                        # Standard output: compact JSON
                        # Check both 'dryer_status' and 'dryer' for backward compatibility
                        dryer_data = result.get("dryer_status") or result.get("dryer", {})
                        status_info = {
                            "instance": inst_num,
                            "status": result.get("status", "unknown"),
                            "temp": result.get("temp", 0),
                            "dryer_status": dryer_data
                        }
                        gcmd.respond_info(f"// {json.dumps(status_info)}")
                else:
                    msg = response.get("msg") if response else "No response"
                    gcmd.respond_info(f"Status query failed: {msg}")

            ace.send_request(build_status_request(ace), status_callback)
        else:
            if verbose:
                gcmd.respond_info("=== ACE Status (All Instances - Verbose) ===\n")
            else:
                gcmd.respond_info("=== ACE Status (All Instances) ===")

            def query_instance(inst_num, manager, ace):
                def status_callback(response):
                    if response and response.get("code") == 0:
                        result = response.get("result", {})

                        if verbose:
                            # Verbose output: all information nicely formatted
                            formatted = format_verbose_status(result, inst_num, ace)
                            gcmd.respond_info(formatted + "\n")
                        else:
                            # Standard output: compact JSON
                            # Check both 'dryer_status' and 'dryer' for backward compatibility
                            dryer_data = result.get("dryer_status") or result.get("dryer", {})
                            status_info = {
                                "instance": inst_num,
                                "status": result.get("status", "unknown"),
                                "temp": result.get("temp", 0),
                                "dryer_status": dryer_data
                            }
                            gcmd.respond_info(f"// {json.dumps(status_info)}")
                    else:
                        msg = response.get("msg") if response else "No response"
                        gcmd.respond_info(f"Instance {inst_num}: Status query failed: {msg}")

                ace.send_request(build_status_request(ace), status_callback)

            for_each_instance(query_instance)

    except Exception as e:
        gcmd.respond_info(f"ACE_GET_STATUS error: {e}")


def cmd_ACE_GET_CONNECTION_STATUS(gcmd):
    """Get connection status for all ACE instances."""
    try:
        lines = []
        lines.append("=== ACE Connection Status ===")

        for inst_num in sorted(ACE_INSTANCES.keys()):
            ace = ACE_INSTANCES[inst_num]
            status = ace.serial_mgr.get_connection_status()

            # Build status line
            if status["connected"]:
                if status["stable"]:
                    conn_state = "Connected (stable)"
                else:
                    conn_state = f"Connected (stabilizing, {status['time_connected']:.0f}s)"
            else:
                conn_state = "Disconnected"

            # Port and topology info
            port = status.get("port", "unknown")
            topology = status.get("usb_topology", "unknown")
            port_info = f" (port={port}, usb={topology})"

            lines.append(f"ACE[{inst_num}]: {conn_state}{port_info}")

            # Layer 1: Serial Communication Health Supervision
            sup = status.get("supervision", {})
            timeout_cnt = sup.get("timeout_count", 0)
            timeout_thr = sup.get("timeout_threshold", 0)
            unsol_cnt = sup.get("unsolicited_count", 0)
            unsol_thr = sup.get("unsolicited_threshold", 0)
            sup_window = sup.get("window_seconds", 30)
            sup_enabled = ace.serial_mgr._supervision_enabled

            health_status = "healthy" if (timeout_cnt < timeout_thr or unsol_cnt < unsol_thr) else "UNHEALTHY"
            sup_status = "enabled" if sup_enabled else "disabled"
            # Duplicate replies = two units answering one device_id (identity
            # collision); only shown when present so the healthy case stays clean
            dup_cnt = sup.get("duplicate_count", 0)
            dup_info = f", {dup_cnt} duplicates (device_id collision!)" if dup_cnt else ""
            lines.append(
                f"  ├─ Layer 1 - Serial Health: {health_status} ({sup_status}) - "
                f"{timeout_cnt}/{timeout_thr} timeouts, {unsol_cnt}/{unsol_thr} unsolicited (last {int(sup_window)}s){dup_info}"
            )

            # Layer 2: Exponential Backoff
            reconnects = status["recent_reconnects"]
            backoff = ace.serial_mgr._reconnect_backoff
            backoff_min = ace.serial_mgr.RECONNECT_BACKOFF_MIN
            backoff_max = ace.serial_mgr.RECONNECT_BACKOFF_MAX
            backoff_window = ace.serial_mgr.INSTABILITY_WINDOW

            if status["connected"]:
                backoff_status = f"current={backoff:.1f}s (reset on next failure)"
            else:
                backoff_status = f"next retry in {backoff:.1f}s (min={backoff_min:.0f}s, max={backoff_max:.0f}s)"

            lines.append(
                f"  ├─ Layer 2 - Backoff: {backoff_status}, "
                f"{reconnects} failures (last {int(backoff_window)}s)"
            )

            # Layer 3: Manager Stability Supervision
            threshold = ace.serial_mgr.INSTABILITY_THRESHOLD
            grace_period = ace.serial_mgr.STABILITY_GRACE_PERIOD

            if reconnects >= threshold:
                stability_status = f"UNSTABLE ({reconnects}/{threshold} reconnects)"
            elif status["connected"] and status["time_connected"] < grace_period:
                stability_status = f"stabilizing ({status['time_connected']:.0f}s/{int(grace_period)}s)"
            elif status["connected"]:
                stability_status = "stable"
            else:
                stability_status = f"disconnected ({reconnects}/{threshold} reconnects)"

            lines.append(
                f"  └─ Layer 3 - Manager: {stability_status}"
            )

        gcmd.respond_info("\n".join(lines))

    except Exception as e:
        gcmd.respond_info(f"ACE_GET_CONNECTION_STATUS error: {e}")


def cmd_ACE_RECONNECT(gcmd):
    """Reconnect ACE serial connection. [INSTANCE=] [DELAY=5] - omit INSTANCE to reconnect all."""
    try:
        instance_num = gcmd.get_int("INSTANCE", None)
        delay = gcmd.get_float("DELAY", 5.0)

        if instance_num is not None:
            ace = ace_get_instance(gcmd)
            ace.serial_mgr.reconnect(delay=delay)
            gcmd.respond_info(f"ACE[{instance_num}]: Disconnected, reconnecting in {delay:.1f}s...")
        else:
            # Shared-bus ACE2 instances may reuse the same serial manager.
            # Reconnect each transport once to avoid duplicate schedule spam.
            seen_transports = set()
            for inst_num in sorted(ACE_INSTANCES.keys()):
                ace = ACE_INSTANCES[inst_num]
                serial_mgr = getattr(ace, "serial_mgr", None)
                transport_id = id(serial_mgr)
                if transport_id in seen_transports:
                    continue
                seen_transports.add(transport_id)
                serial_mgr.reconnect(delay=delay)
                gcmd.respond_info(
                    f"ACE[{inst_num}]: Disconnected, reconnecting in {delay:.1f}s..."
                )

    except Exception as e:
        gcmd.respond_info(f"ACE_RECONNECT error: {e}")


def ace_get_instance_and_slot(gcmd):
    """
    Resolve ACE instance and slot index from command parameters.

    Priority:
    1. T= parameter (maps tool to instance + local slot)
    2. INSTANCE= + INDEX= parameters (both required)
    3. Error if neither option provided

    Returns:
        tuple: (ace_instance, slot_index)
    """
    params = gcmd.get_command_parameters()

    # Option 1: T= parameter (tool number)
    if "T" in params:
        tool = gcmd.get_int("T")

        # Map tool to instance
        inst_num = get_instance_from_tool(tool)
        if inst_num < 0:
            raise gcmd.error(f"No ACE instance manages tool T{tool}")

        ace = ACE_INSTANCES.get(inst_num)
        if ace is None:
            raise gcmd.error(f"Instance {inst_num} not available")

        # Get local slot index
        slot = get_local_slot(tool, inst_num)

        return ace, slot

    # Option 2: INSTANCE= + INDEX= parameters
    if "INSTANCE" in params and "INDEX" in params:
        ace = ace_get_instance(gcmd)
        slot = gcmd.get_int("INDEX")
        return ace, slot

    # Error: neither provided
    raise gcmd.error("Must specify either T=<tool> or INSTANCE=<inst> INDEX=<slot>")


def cmd_ACE_FEED(gcmd):
    """Feed filament from slot."""
    try:
        ace, slot = ace_get_instance_and_slot(gcmd)
        length = gcmd.get_int("LENGTH")
        speed = gcmd.get_int("SPEED", ace.feed_speed)

        validate_feed_and_retract_arguments(gcmd, ace, slot, length, speed)
        ace._feed(slot, length, speed)
    except Exception as e:
        gcmd.respond_info(f"ACE_FEED error: {e}")


def cmd_ACE_STOP_FEED(gcmd):
    """Stop feeding filament."""
    try:
        ace, slot = ace_get_instance_and_slot(gcmd)

        if not (0 <= slot < ace.SLOT_COUNT):
            raise gcmd.error(f"Invalid slot {slot}")

        ace._stop_feed(slot)
    except Exception as e:
        gcmd.respond_info(f"ACE_STOP_FEED error: {e}")


def cmd_ACE_RETRACT(gcmd):
    """Retract filament."""
    try:
        ace, slot = ace_get_instance_and_slot(gcmd)
        length = gcmd.get_int("LENGTH")
        speed = gcmd.get_int("SPEED", ace.retract_speed)

        validate_feed_and_retract_arguments(gcmd, ace, slot, length, speed)
        ace._retract(slot, length, speed)
    except Exception as e:
        gcmd.respond_info(f"ACE_RETRACT error: {e}")


def cmd_ACE_STOP_RETRACT(gcmd):
    """Stop retracting filament."""
    try:
        ace, slot = ace_get_instance_and_slot(gcmd)

        if not (0 <= slot < ace.SLOT_COUNT):
            raise gcmd.error(f"Invalid slot {slot}")

        ace._stop_retract(slot)
    except Exception as e:
        gcmd.respond_info(f"ACE_STOP_RETRACT error: {e}")


# Predefined color names mapping (0.0-1.0 float range converted to 0-255 RGB)
COLOR_NAMES = {
    "BLACK": [0, 0, 0],
    "BLUE": [0, 0, 255],
    "BLUEISH": [128, 128, 255],
    "CYAN": [0, 255, 255],
    "DARK_GRAY": [64, 64, 64],
    "DARK_YELLOW": [128, 128, 0],
    "GRAY": [128, 128, 128],
    "GREEN": [0, 255, 0],
    "GREENISH": [128, 255, 128],
    "LIGHT_GRAY": [191, 191, 191],
    "MAGENTA": [255, 0, 255],
    "ORANGE": [235, 128, 66],
    "RED": [255, 0, 0],
    "REDISH": [255, 128, 128],
    "YELLOW": [255, 255, 0],
    "WHITE": [255, 255, 255],
    "ORCA": [0, 150, 136],
}


def cmd_ACE_SET_SLOT(gcmd):
    """Set slot inventory information."""
    try:
        ace, idx = ace_get_instance_and_slot(gcmd)

        if not (0 <= idx < ace.SLOT_COUNT):
            raise gcmd.error(f"Invalid slot {idx}")

        if gcmd.get_int("EMPTY", 0):
            ace.inventory[idx] = {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False}
            manager = ace_get_manager(ace.instance_num)
            manager._sync_inventory_to_persistent(ace.instance_num)
            gcmd.respond_info(f"Slot {idx} set to empty")
            return

        color_str = gcmd.get("COLOR", None)
        material = gcmd.get("MATERIAL", "")
        temp = gcmd.get_int("TEMP", 0)

        if not color_str or not material or temp <= 0 or temp > 300:
            raise gcmd.error("COLOR, MATERIAL, and TEMP (1-300) must be set unless EMPTY=1")

        # Parse color - check for named color first, then R,G,B format
        color_upper = color_str.upper()
        if color_upper in COLOR_NAMES:
            color = COLOR_NAMES[color_upper]
        else:
            try:
                color = [int(x) for x in color_str.split(",")]
                if len(color) != 3:
                    raise ValueError()
                # Clamp RGB values to valid 0-255 range
                color = [max(0, min(255, c)) for c in color]
            except (ValueError, AttributeError):
                raise gcmd.error(
                    f"COLOR must be a named color ({', '.join(COLOR_NAMES.keys())}) or R,G,B format"
                )

        ace.inventory[idx] = {"status": "ready", "color": color, "material": material, "temp": temp, "rfid": False}
        manager = ace_get_manager(ace.instance_num)
        manager._sync_inventory_to_persistent(ace.instance_num)
        gcmd.respond_info(f"Slot {idx}: color={color}, material={material}, temp={temp}")
    except Exception as e:
        gcmd.respond_info(f"ACE_SET_SLOT error: {e}")


def cmd_ACE_SAVE_INVENTORY(gcmd):
    """Save inventory to persistent storage."""
    ace = ace_get_instance(gcmd)
    manager = ace_get_manager(ace.instance_num)
    manager._sync_inventory_to_persistent(ace.instance_num)
    gcmd.respond_info("Inventory saved")


def cmd_ACE_START_DRYING(gcmd):
    """Start filament dryer. [INSTANCE=] TEMP= [DURATION=240] - omit INSTANCE to affect all."""
    try:
        manager = ace_get_manager()
        if not manager._ace_pro_enabled:
            raise gcmd.error("ACE Pro is disabled — cannot start dryer")

        instance_num = gcmd.get_int("INSTANCE", None)
        temperature = gcmd.get_int("TEMP")
        duration = gcmd.get_int("DURATION", 240)

        if duration <= 0:
            raise gcmd.error("DURATION must be positive")

        if instance_num is not None:
            ace = ace_get_instance(gcmd)

            if temperature <= 0 or temperature > ace.max_dryer_temperature:
                raise gcmd.error(f"TEMP must be between 1 and {ace.max_dryer_temperature}°C")

            def callback(response):
                if response and response.get("code") == 0:
                    if not getattr(ace, "_dryer_start_logged", False):
                        gcmd.respond_info(f"ACE[{instance_num}]: Dryer started: {temperature}°C for {duration}min")
                        ace._dryer_start_logged = True
                else:
                    msg = response.get("msg", "Unknown error") if response else ""
                    gcmd.respond_info(f"ACE[{instance_num}]: Dryer start failed: {msg}")

            ace.start_drying(temperature, duration, callback)
        else:
            if len(ACE_INSTANCES) > 1:
                gcmd.respond_info(
                    "ACE_START_DRYING: INSTANCE not provided - applying to all ACE instances"
                )

            def start_dryer(inst_num, manager, ace):
                if temperature <= 0 or temperature > ace.max_dryer_temperature:
                    gcmd.respond_info(f"ACE[{inst_num}]: Temp {temperature}°C out of range, skipping")
                    return

                def callback(response):
                    if response and response.get("code") == 0:
                        if not getattr(ace, "_dryer_start_logged", False):
                            gcmd.respond_info(f"ACE[{inst_num}]: Dryer started: {temperature}°C for {duration}min")
                            ace._dryer_start_logged = True
                    else:
                        msg = response.get("msg", "Unknown error") if response else ""
                        gcmd.respond_info(f"ACE[{inst_num}]: Dryer start failed: {msg}")

                ace.start_drying(temperature, duration, callback)

            for_each_instance(start_dryer)

    except Exception as e:
        gcmd.respond_info(f"ACE_START_DRYING error: {e}")


def cmd_ACE_STOP_DRYING(gcmd):
    """Stop filament dryer. [INSTANCE=] - omit to affect all instances."""
    try:
        instance_num = gcmd.get_int("INSTANCE", None)

        if instance_num is not None:
            ace = ace_get_instance(gcmd)

            def callback(response):
                if response and response.get("code") == 0:
                    gcmd.respond_info(f"ACE[{instance_num}]: Dryer stopped")
                    ace._dryer_start_logged = False
                else:
                    msg = response.get("msg", "Unknown error") if response else ""
                    gcmd.respond_info(f"ACE[{instance_num}]: Dryer stop failed: {msg}")

            ace.stop_drying(callback)
        else:
            if len(ACE_INSTANCES) > 1:
                gcmd.respond_info(
                    "ACE_STOP_DRYING: INSTANCE not provided - applying to all ACE instances"
                )

            def stop_dryer(inst_num, manager, ace):
                def callback(response):
                    if response and response.get("code") == 0:
                        gcmd.respond_info(f"ACE[{inst_num}]: Dryer stopped")
                    else:
                        msg = response.get("msg", "Unknown error") if response else ""
                        gcmd.respond_info(f"ACE[{inst_num}]: Dryer stop failed: {msg}")

                ace.stop_drying(callback)

            for_each_instance(stop_dryer)

    except Exception as e:
        gcmd.respond_info(f"ACE_STOP_DRYING error: {e}")


def cmd_ACE_ENABLE_FEED_ASSIST(gcmd):
    """Enable filament feed assist for smooth loading. T=<tool> or INSTANCE= INDEX="""
    ace, slot = ace_get_instance_and_slot(gcmd)

    if not (0 <= slot < ace.SLOT_COUNT):
        raise gcmd.error(f"Invalid slot {slot}")

    ace._enable_feed_assist(slot)
    gcmd.respond_info(f"Feed assist enabled for slot {slot}")


def cmd_ACE_DISABLE_FEED_ASSIST(gcmd):
    """Disable filament feed assist. T=<tool> or INSTANCE= INDEX="""
    ace, slot = ace_get_instance_and_slot(gcmd)

    if not (0 <= slot < ace.SLOT_COUNT):
        raise gcmd.error(f"Invalid slot {slot}")

    ace._disable_feed_assist(slot)
    gcmd.respond_info(f"Feed assist disabled for slot {slot}")


def cmd_ACE_SET_PURGE_AMOUNT(gcmd):
    """
    Set purge amount and speed for next toolchange.

    Usage: ACE_SET_PURGE_AMOUNT PURGELENGTH=<mm> [PURGESPEED=<mm/min>] [INSTANCE=]

    PURGESPEED is optional, defaults to default_color_change_purge_speed from config (mm/min).
    INSTANCE is optional, defaults to instance 0.
    """
    try:
        purge_length = gcmd.get_float('PURGELENGTH', None)

        if purge_length is None:
            raise gcmd.error("PURGELENGTH parameter is required")

        # Get manager (instance 0 by default, or specified)
        instance_num = gcmd.get_int('INSTANCE', 0)
        manager = ace_get_manager(instance_num)

        # Allow PURGESPEED to be optional - use default if not specified
        purge_speed = gcmd.get_float(
            'PURGESPEED',
            manager.default_color_change_purge_speed
        )

        manager.toolchange_purge_length = purge_length
        manager.toolchange_purge_speed = purge_speed

        gcmd.respond_info(
            f"ACE: Purge settings updated - "
            f"Length: {purge_length}mm, Speed: {purge_speed}mm/min"
        )

    except Exception as e:
        gcmd.respond_info(f"ACE_SET_PURGE_AMOUNT error: {e}")


def cmd_ACE_QUERY_SLOTS(gcmd):
    """Query slot inventory. [INSTANCE=] or [TOOL=] - omit both to query all instances. VERBOSE=1 for full details."""
    params = gcmd.get_command_parameters()
    verbose = gcmd.get_int("VERBOSE", 0)

    def format_slot(idx, slot, verbose, inst_num=0, ace_connected=True):
        status = slot.get('status', 'empty')
        material = slot.get('material', '')
        color = slot.get("color", [0, 0, 0])
        temp = slot.get('temp', 0)
        rfid = slot.get('rfid', False)
        sku = slot.get('sku', '')
        brand = slot.get('brand', '')
        extruder_temp = slot.get('extruder_temp', {})
        hotbed_temp = slot.get('hotbed_temp', {})

        # If ACE is not connected, show ??? for non-empty slots to indicate uncertain status
        if not ace_connected and status != 'empty':
            status = '???'

        # Calculate tool number: instance 0 slot 0 = T0, instance 1 slot 0 = T4, etc.
        tool_num = (inst_num * 4) + idx

        # Plain text values for padding calculations
        status_text = '-----' if status == 'empty' else status
        # Show "???" for missing/unknown material on loaded slots, "Empty" only for empty slots
        if status == 'empty':
            material_text = 'Empty'
        elif not material or material == 'Unknown':
            material_text = '???'
        else:
            material_text = material
        rfid_text = "RFID" if rfid else "----"
        sku_text = sku if sku else '---'
        brand_text = brand if brand else '---'
        r, g, b = color[0], color[1], color[2]
        rgb_text = f"RGB({r},{g},{b})"
        # Map RGB to closest Mainsail color for circle indicator
        color_name = rgb_to_mainsail_color(r, g, b)
        temp_text = f"{temp}°C"

        # Format temperature ranges
        if extruder_temp and isinstance(extruder_temp, dict):
            ext_min = extruder_temp.get('min', 0)
            ext_max = extruder_temp.get('max', 0)
            extruder_range = f"{ext_min}-{ext_max}°C" if ext_min or ext_max else "---"
        else:
            extruder_range = "---"

        if hotbed_temp and isinstance(hotbed_temp, dict):
            bed_min = hotbed_temp.get('min', 0)
            bed_max = hotbed_temp.get('max', 0)
            bed_range = f"{bed_min}-{bed_max}°C" if bed_min or bed_max else "---"
        else:
            bed_range = "---"

        # Apply padding to plain text first
        status_padded = status_text.ljust(6)
        rfid_padded = rfid_text.ljust(8)
        sku_padded = sku_text.ljust(12)
        brand_padded = brand_text.ljust(12)
        material_padded = material_text.ljust(15)
        # Add colored circle before RGB text (circle + space + RGB = needs less padding)
        rgb_with_circle = f"⬤ {rgb_text}"
        rgb_padded = rgb_with_circle.ljust(18)  # Extra space for circle
        temp_padded = temp_text.rjust(6)
        extruder_padded = extruder_range.ljust(12)
        bed_padded = bed_range.ljust(12)

        # Wrap padded text with color tags (preserving the padding)
        if status == 'empty':
            status_display = f'<span class=secondary--text>{status_padded}</span>'
        elif status == 'ready':
            status_display = f'<span class=success--text>{status_padded}</span>'
        elif status == 'active':
            status_display = f'<span class=info--text>{status_padded}</span>'
        else:
            status_display = status_padded

        if status == 'empty':
            material_display = f'<span class=secondary--text>{material_padded}</span>'
        elif material.startswith('PLA'):
            material_display = f'<span class=success--text>{material_padded}</span>'
        elif material in ('PETG',):
            material_display = f'<span class=info--text>{material_padded}</span>'
        elif material in ('ABS', 'ASA'):
            material_display = f'<span class=warning--text>{material_padded}</span>'
        elif material in ('TPU',):
            material_display = f'<span class=accent--text>{material_padded}</span>'
        elif material == 'Unknown':
            # Bright warning color for missing material data
            material_display = f'<span class=warning--text>{material_padded}</span>'
        else:
            material_display = material_padded

        rfid_display = f'<span class=accent--text>{rfid_padded}</span>' if rfid else rfid_padded

        # Color the RGB display with circle using mapped color
        if status == 'empty':
            rgb_display = f'<span class=secondary--text>{rgb_padded}</span>'
        elif color_name is None:
            # No color class = white, use terminal default
            rgb_display = rgb_padded
        else:
            rgb_display = f'<span class={color_name}--text>{rgb_padded}</span>'

        if temp == 0:
            temp_display = f'<span class=info--text>{temp_padded}</span>'
        else:
            temp_display = f'<span class=error--text>{temp_padded}</span>'

        # Format with consistent column widths
        line1 = (
            f"  [{idx}] T{tool_num} | {status_display} | {rfid_display} | {sku_padded} | "
            f"{brand_padded} | {material_display} | {rgb_display} | {temp_display} | "
            f"{extruder_padded} | {bed_padded}"
        )

        # Line 2: Additional RFID metadata (only if VERBOSE=1 and data present)
        if verbose:
            extra_parts = []
            if "sku" in slot:
                extra_parts.append(f"sku={slot.get('sku')}")
            if "brand" in slot:
                extra_parts.append(f"brand={slot.get('brand')}")
            for key in ("icon_type", "extruder_temp", "hotbed_temp", "diameter", "total", "current"):
                if key in slot:
                    extra_parts.append(f"{key}={slot.get(key)}")

            if extra_parts:
                line2 = "     " + ", ".join(extra_parts)
                return line1 + "\n" + line2

        return line1

    # Table header and separator (defined once)
    header_line = ("  [#] T# | Status | RFID     | SKU          | Brand        | "
                   "Material        |         Color       |   Temp | Extruder     | Bed")
    separator_line = ("  -------+--------+----------+--------------+--------------+"
                      "-----------------+---------------------+--------+--------------+------------")

    def format_instance_slots(ace, inst_num):
        """Format slots for a single ACE instance."""
        ace_connected = ace.serial_mgr.is_connected() if hasattr(ace, 'serial_mgr') else False
        conn_indicator = "" if ace_connected else " <span class=error--text>[DISCONNECTED - cached data]</span>"

        lines = []
        lines.append(f"<span class=warning--text>=== ACE Instance {inst_num} Slots ===</span>{conn_indicator}")
        lines.append(header_line)
        lines.append(separator_line)
        for idx, slot in enumerate(ace.inventory):
            lines.append(format_slot(idx, slot, verbose, inst_num, ace_connected))
        return lines

    if "INSTANCE" not in params and "TOOL" not in params:
        # Query all instances
        lines = []
        for inst_num in sorted(ACE_INSTANCES.keys()):
            ace = ACE_INSTANCES[inst_num]
            lines.extend(format_instance_slots(ace, inst_num))
        gcmd.respond_info("\n".join(lines))
    else:
        # Query single instance
        ace = ace_get_instance(gcmd)
        inst_num = ace.instance_num if hasattr(ace, "instance_num") else 0
        lines = format_instance_slots(ace, inst_num)
        gcmd.respond_info("\n".join(lines))


def _sync_tangle_detection(manager, enabled):
    """Set both the python flag and [output_pin TANGLE_DETECTION] (if configured) so the slider never lies about the runtime state."""
    try:
        pin = manager.printer.lookup_object(
            "output_pin TANGLE_DETECTION", None
        )
    except Exception:
        pin = None
    if pin is not None:
        manager.gcode.run_script_from_command(
            f"SET_PIN PIN=TANGLE_DETECTION VALUE={1.0 if enabled else 0.0}"
        )
    manager.runout_monitor.set_tangle_detection_enabled(enabled)


def cmd_ACE_TANGLE_DETECTION(gcmd):
    """Toggle tangle detection live.  ENABLE=0/1 (no arg → query)."""
    manager = ace_get_manager(0)
    monitor = manager.runout_monitor

    enable_arg = gcmd.get_int("ENABLE", default=None, minval=0, maxval=1)
    if enable_arg is None:
        active = monitor._is_tangle_detection_active()
        state = "enabled" if active else "disabled"
        gcmd.respond_info(
            f"ACE: tangle detection {state} "
            f"(threshold {monitor.tangle_pump_time:.1f}s)"
        )
        return

    _sync_tangle_detection(manager, bool(enable_arg))
    state = "ENABLED" if enable_arg else "DISABLED"
    gcmd.respond_info(f"ACE: tangle detection {state}")
    logging.info("ACE: tangle detection %s via gcode", state)


def cmd__ACE_TANGLE_DISABLE_AND_RESUME(gcmd):
    """Internal: disable tangle detection and resume.  Wired to the
    "Disable & Resume" button on the tangle-detected prompt."""
    manager = ace_get_manager(0)
    _sync_tangle_detection(manager, False)
    gcmd.respond_info("ACE: tangle detection DISABLED, resuming")
    logging.info("ACE: tangle detection disabled + resume via prompt button")
    manager.gcode.run_script_from_command("RESUME")


def cmd_ACE_ENABLE_ENDLESS_SPOOL(gcmd):
    """Enable endless spool (automatic material matching on runout)."""
    manager = ace_get_manager(0)
    manager.state.set_and_save("ace_endless_spool_enabled", True)

    logging.info("ACE: Endless spool ENABLED (persisted)")


def cmd_ACE_DISABLE_ENDLESS_SPOOL(gcmd):
    """Disable endless spool (stop automatic material matching on runout)."""
    manager = ace_get_manager(0)
    manager.state.set_and_save("ace_endless_spool_enabled", False)

    logging.info("ACE: Endless spool DISABLED (persisted)")


def cmd_ACE_RESET_PERSISTENT_INVENTORY(gcmd):
    """Reset persistent filament inventory to empty slots. INSTANCE= for single, omit for all."""
    instance_num = gcmd.get_int("INSTANCE", default=None)

    if instance_num is not None:
        # Reset specific instance
        ace = ace_get_instance(gcmd)
        ace.reset_persistent_inventory()
        manager = ace_get_manager(ace.instance_num)
        manager._sync_inventory_to_persistent(ace.instance_num)
        gcmd.respond_info(f"ACE[{ace.instance_num}]: Inventory reset to empty")
    else:
        # Reset ALL instances
        manager = ace_get_manager()
        for inst_num in sorted(ACE_INSTANCES.keys()):
            ace = ACE_INSTANCES[inst_num]
            ace.reset_persistent_inventory()
            manager._sync_inventory_to_persistent(inst_num, flush=False)
            gcmd.respond_info(f"ACE[{inst_num}]: Inventory reset to empty")
        manager.state.flush()
        gcmd.respond_info(f"All {len(ACE_INSTANCES)} ACE instances reset")


def cmd_ACE_REDETECT(gcmd):
    """Re-detect instances mis-typed by an incomplete startup USB scan.

    Recovers an instance frozen on the wrong protocol - typically one stuck on
    ace1_json because its ACE2 RS-485 adapter (CH340) enumerated after protocol
    resolution, so it never finds the ACE2. Adoption is gated on ACE2
    DISCOVER_DEVICE proving an unbound unit exists, so it can never over-
    subscribe the shared bus (safe to run even if an ACE1 is mid-reset).

    Run this after confirming all ACE units are powered and enumerated.
    """
    manager = ace_get_manager(0)
    manager.redetect_transports(gcmd=gcmd)


def cmd_ACE_CLEANUP_STALE_VARS(gcmd):
    """List or remove obsolete ACE-owned save_variables keys.

    Dry run by default: lists stale binding and inventory keys. CONFIRM=1
    deletes them - required because stale ace_inventory_N keys hold
    user-entered spool data that cannot be rebuilt (startup only ever
    auto-removes rebuildable binding keys).
    """
    manager = ace_get_manager(0)
    manager.cleanup_stale_persistent_vars(gcmd)


def cmd_ACE_RESET_SHARED_BUS_BINDINGS(gcmd):
    """Clear persisted ACE2 shared-bus instance↔UID bindings.

    Optional args:
      RECONNECT=1/0 (default 1): reconnect shared transports after clearing.
      DELAY=<seconds> (default 2.0): reconnect delay when RECONNECT=1.
    """
    manager = ace_get_manager(0)
    reconnect = bool(gcmd.get_int("RECONNECT", 1, minval=0, maxval=1))
    delay = gcmd.get_float("DELAY", 2.0, minval=0.0)

    variables = manager.state.get_all()
    binding_keys = sorted(
        key for key in variables.keys() if key.startswith("ace2_bus_bindings_")
    )

    if not binding_keys:
        gcmd.respond_info("ACE: No persisted shared-bus bindings found")
    else:
        for key in binding_keys:
            manager.state.set(key, {})
            gcmd.respond_info(f"ACE: Cleared {key}")
        manager.state.flush()
        gcmd.respond_info(
            f"ACE: Cleared {len(binding_keys)} shared-bus binding entr{'y' if len(binding_keys) == 1 else 'ies'}"
        )

    if not reconnect:
        gcmd.respond_info("ACE: Shared-bus reconnect skipped (RECONNECT=0)")
        return

    # Reconnect each shared transport once.
    seen_transports = set()
    reconnected = 0
    for inst_num in sorted(ACE_INSTANCES.keys()):
        ace = ACE_INSTANCES[inst_num]
        if getattr(ace, "bus_session", None) is None:
            continue
        serial_mgr = getattr(ace, "serial_mgr", None)
        transport_id = id(serial_mgr)
        if transport_id in seen_transports:
            continue
        seen_transports.add(transport_id)
        serial_mgr.reconnect(delay=delay)
        reconnected += 1
        gcmd.respond_info(
            f"ACE[{inst_num}]: Shared-bus transport reconnect scheduled in {delay:.1f}s"
        )

    if reconnected == 0:
        gcmd.respond_info("ACE: No active shared-bus transport found to reconnect")


def cmd_ACE_RESET_ACTIVE_TOOLHEAD(gcmd):
    """Reset all feed assist states and clear current tool index. Affects all instances.

    Also clears ace_target_index: this is a full "no tool is active/pending"
    reset, so any in-flight/unconfirmed toolchange attempt tracked separately
    from ace_current_index must be cleared too, or KlipperScreen (and other
    consumers) would keep reporting a stale "unconfirmed" toolchange after
    this reset.
    """
    for inst_num in sorted(ACE_INSTANCES.keys()):
        ace = ACE_INSTANCES[inst_num]
        ace.reset_feed_assist_state()

    manager = ace_get_manager()
    manager.state.set("ace_current_index", -1)
    manager.state.set("ace_target_index", -1)
    manager.state.set_and_save("ace_filament_pos", FILAMENT_STATE_BOWDEN)

    manager.gcode.run_script_from_command(
        "SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE=-1"
    )

    gcmd.respond_info(f"ACE[{ace.instance_num}]: Active toolhead state reset")


def cmd_ACE_DEBUG_SET_CURRENT_INDEX(gcmd):
    """Manually override ace_current_index.

    TOOL=-1  (or omit TOOL) to set 'no tool loaded'.
    TOOL=<n> to declare tool <n> as the currently loaded tool.

    This only updates the saved-state variable — it does NOT physically
    move or retract any filament.  Use it to correct a stale persisted
    value after manual intervention.

    Also clears ace_target_index to -1: manually asserting the confirmed
    tool means there is no toolchange attempt still in flight/unconfirmed.
    """
    manager = ace_get_manager()

    # Default -1 = no tool loaded
    tool = gcmd.get_int("TOOL", default=-1)

    # Validate range
    total_tools = len(ACE_INSTANCES) * 4
    if tool != -1 and not (0 <= tool < total_tools):
        raise gcmd.error(
            f"TOOL={tool} is out of range. Valid range: -1 (none) to {total_tools - 1}"
        )

    prev = manager.state.get("ace_current_index", -1)
    manager.state.set_and_save("ace_current_index", tool)
    manager.state.set_and_save("ace_target_index", -1)

    if tool == -1:
        gcmd.respond_info(
            f"ACE: ace_current_index reset to -1 (no tool loaded). "
            f"Was: T{prev}. ace_target_index cleared to -1."
        )
    else:
        gcmd.respond_info(
            f"ACE: ace_current_index set to T{tool}. Was: T{prev}. "
            f"ace_target_index cleared to -1."
        )


def cmd_ACE_DEBUG_SET_TARGET_INDEX(gcmd):
    """Manually override ace_target_index.

    TOOL=-1  (or omit TOOL) to clear the in-flight toolchange attempt.
    TOOL=<n> to declare tool <n> as the attempted (unconfirmed) target.

    ace_target_index tracks a toolchange attempt that is still in flight or
    was left unconfirmed by a failure -- distinct from ace_current_index,
    which only reflects the last CONFIRMED physically loaded tool. This
    command only updates the saved-state variable — it does NOT physically
    move or retract any filament. Use it to correct a stale persisted value
    after manual intervention.
    """
    manager = ace_get_manager()

    # Default -1 = no toolchange in flight
    tool = gcmd.get_int("TOOL", default=-1)

    # Validate range
    total_tools = len(ACE_INSTANCES) * 4
    if tool != -1 and not (0 <= tool < total_tools):
        raise gcmd.error(
            f"TOOL={tool} is out of range. Valid range: -1 (none) to {total_tools - 1}"
        )

    prev = manager.state.get("ace_target_index", -1)
    manager.state.set_and_save("ace_target_index", tool)

    if tool == -1:
        gcmd.respond_info(
            f"ACE: ace_target_index cleared to -1 (no toolchange in flight). "
            f"Was: T{prev}"
        )
    else:
        gcmd.respond_info(
            f"ACE: ace_target_index set to T{tool}. Was: T{prev}"
        )


def cmd_ACE_DEBUG_SET_FILAMENT_STATE(gcmd):
    """Manually override ace_filament_pos.

    STATE=bowden|splitter|toolhead|nozzle

    Valid states:
      bowden   - filament retracted to park position (default 'unloaded')
      splitter - filament tip is at the RDM / 4-in-1 splitter
      toolhead - filament tip is at the toolhead sensor
      nozzle   - filament loaded all the way into the nozzle

    This only updates the saved-state variable — it does NOT physically
    move any filament.  Use it to correct a stale persisted value after
    manual intervention.
    """
    manager = ace_get_manager()

    valid_states = {
        "bowden":   FILAMENT_STATE_BOWDEN,
        "splitter": FILAMENT_STATE_SPLITTER,
        "toolhead": FILAMENT_STATE_TOOLHEAD,
        "nozzle":   FILAMENT_STATE_NOZZLE,
    }

    raw = gcmd.get("STATE", default=None)
    if raw is None:
        # Show current value and valid options when called without STATE=
        current = manager.state.get("ace_filament_pos", FILAMENT_STATE_BOWDEN)
        gcmd.respond_info(
            f"ACE: Current filament state: '{current}'\n"
            f"Valid values: {', '.join(valid_states.keys())}"
        )
        return

    state_key = raw.strip().lower()
    if state_key not in valid_states:
        raise gcmd.error(
            f"Invalid STATE='{raw}'. "
            f"Valid values: {', '.join(valid_states.keys())}"
        )

    new_state = valid_states[state_key]
    prev = manager.state.get("ace_filament_pos", FILAMENT_STATE_BOWDEN)
    manager.state.set_and_save("ace_filament_pos", new_state)

    gcmd.respond_info(
        f"ACE: ace_filament_pos set to '{new_state}'. Was: '{prev}'"
    )


def cmd_ACE_GET_CURRENT_INDEX(gcmd):
    """Query currently loaded tool index from persistent storage (returns -1 if no tool loaded).

    Also reports ace_target_index: a tool index other than -1 there means a
    toolchange to that tool is still in flight or was left unconfirmed by a
    failed attempt (distinct from the confirmed current_index).
    """
    manager = ace_get_manager(0)
    current_index = manager.state.get("ace_current_index", -1)
    target_index = manager.state.get("ace_target_index", -1)
    gcmd.respond_info(f"Current tool index: {current_index}")
    if target_index != -1:
        gcmd.respond_info(
            f"Target tool index: {target_index} (toolchange unconfirmed)"
        )
    else:
        gcmd.respond_info("Target tool index: -1 (no toolchange in flight)")


def cmd_ACE_ENDLESS_SPOOL_STATUS(gcmd):
    """Show endless spool status (enabled/disabled and scope across all instances)."""
    try:
        manager = ace_get_manager(0)
        endless_spool_enabled = manager.state.get("ace_endless_spool_enabled", False)

        gcmd.respond_info("=== ACE Endless Spool Status ===")
        gcmd.respond_info(f"Endless spool enabled: {endless_spool_enabled}")
        gcmd.respond_info("Mode: Automatic switching on runout detection")
        gcmd.respond_info("Scope: Global (searches across all ACE instances)")
        gcmd.respond_info(f"Total ACE instances: {len(INSTANCE_MANAGERS)}")

    except Exception as e:
        gcmd.respond_info(f"ACE_ENDLESS_SPOOL_STATUS error: {e}")


def cmd_ACE_DEBUG(gcmd):
    """Send debug request to ACE device. METHOD= [PARAMS=] [INSTANCE=] - PARAMS must be JSON."""
    ace = ace_get_instance(gcmd)

    method = gcmd.get("METHOD", "get_status")
    params_str = gcmd.get("PARAMS", "{}")

    try:
        params = json.loads(params_str)
    except json.JSONDecodeError:
        raise gcmd.error(f"Invalid JSON in PARAMS: {params_str}")

    def callback(response):
        # Some protocol debug payloads contain raw bytes (e.g. raw_fields).
        # Use default=str so debug output never crashes callback handling.
        gcmd.respond_info(f"Debug response: {json.dumps(response, default=str)}")

    request = ace.protocol.build_debug_request(method, params)
    ace.send_request(request, callback)


def cmd_ACE_SMART_UNLOAD(gcmd):
    """Unload with sensor-aware fallback strategy. [TOOL=] - omit for current tool."""
    manager = ace_get_manager(0)
    if not manager.get_ace_global_enabled():
        gcmd.respond_info("ACE: Global ACE Pro support disabled - smart unload ignored")
        return

    explicit_tool = gcmd.get_int("TOOL", -1)
    tool_index = explicit_tool
    if tool_index < 0:
        tool_index = manager.state.get("ace_current_index", -1)

    gcmd.respond_info(f"ACE: Smart unload tool {tool_index}")

    try:
        # Without an explicit TOOL= this is the operator-level "free my
        # filament path" command: it may escalate to cycling all slots when
        # the direct unload leaves the path blocked. With TOOL= (and for all
        # internal callers) a blocked path stays a hard error.
        success = manager.smart_unload(
            tool_index, cycle_on_blocked=(explicit_tool < 0)
        )
        if success:
            gcmd.respond_info("ACE: Smart unload succeeded")
            manager.state.set_and_save("ace_current_index", -1)
        else:
            gcmd.respond_info("ACE: Smart unload failed - path blocked")
    except Exception as e:
        gcmd.respond_info(f"ACE: Smart unload error: {e}")


def cmd_ACE_HANDLE_PRINT_END(gcmd):
    """Handle print end - unload filament, clear active tool, disable runout detection."""
    manager = ace_get_manager(0)
    if not manager.get_ace_global_enabled():
        gcmd.respond_info("ACE: Global ACE Pro support disabled - print_end handler does nothing")
        return

    manager.runout_monitor.runout_detection_active = False
    logging.info("ACE: Runout detection disabled for print end")

    do_cut = gcmd.get_int('CUT_TIP', 1)

    if not do_cut:
        gcmd.respond_info("ACE: Print end - skipping unload (CUT_TIP=0), tool remains loaded")

        # Disable feed assist on all instances when print ends.
        tool_index = manager.state.get("ace_current_index", -1)
        if tool_index >= 0:
            for_each_instance(lambda inst_num, mgr, instance: instance._disable_feed_assist(instance._feed_assist_index))
            gcmd.respond_info(
                "ACE: Feed assist disabled for all instances")

        # Refresh Orca lane_data snapshot after print end even when keeping filament loaded
        manager._sync_moonraker_lane_data(force=True, reason="print_end_skip_cut")
        # Flush deferred state to disk
        manager.state.flush()
        return

    try:
        tool_index = manager.state.get("ace_current_index", -1)

        if tool_index < 0:
            gcmd.respond_info("ACE: No active tool for print end")
            return

        gcmd.respond_info(f"ACE: PRINT_END: unloading tool T{tool_index}")

        success = manager.smart_unload(tool_index, prepare_toolhead=True)
        if success:
            gcmd.respond_info(f"ACE: Tool T{tool_index} successfully unloaded")
            manager.state.set("ace_current_index", -1)
            for_each_instance(lambda inst_num, mgr, instance: instance._disable_feed_assist(instance._feed_assist_index))
        else:
            gcmd.respond_info(f"ACE: WARNING - Tool T{tool_index} unload may have failed")

    except Exception as e:
        gcmd.respond_info(f"ACE: PRINT_END error: {e}")
    finally:
        # Flush all deferred state changes to disk now that the print is over.
        try:
            manager.state.flush()
        except Exception:
            logging.exception("ACE: Failed to flush state at print end")
        # Always refresh Moonraker lane_data so Orca sees the latest inventory post-print.
        manager._sync_moonraker_lane_data(force=True, reason="print_end")


def cmd_ACE_SMART_LOAD(gcmd):
    """Load all non-empty slots to verification sensor (toolhead) for inventory verification."""
    manager = ace_get_manager(0)
    if not manager.get_ace_global_enabled():
        gcmd.respond_info("ACE: Global ACE Pro support disabled - smart load ignored")
        return

    gcmd.respond_info("ACE: Smart load - cycling all slots to verification sensor")

    try:
        success = manager.smart_load()
        if success:
            gcmd.respond_info("ACE: Smart load succeeded")
        else:
            gcmd.respond_info("ACE: Smart load failed")
    except Exception as e:
        gcmd.respond_info(f"ACE: Smart load error: {e}")


def cmd_ACE_CHANGE_TOOL(manager, gcmd, tool_index):
    """Handle tool change command."""
    if not manager.get_ace_global_enabled():
        gcmd.respond_info("ACE: Global ACE Pro support disabled - tool change ignored")
        return

    printer = get_printer()

    if tool_index == -1:
        current_tool = manager.state.get("ace_current_index", -1)

        try:
            success = manager.smart_unload(current_tool)
            if success:
                # gcmd.respond_info(f"ACE: Tool {current_tool} unloaded successfully")
                manager.state.set("ace_current_index", -1)
            else:
                gcmd.respond_info(f"ACE: Smart unload of tool {current_tool} failed")
        except Exception as e:
            gcmd.respond_info(f"ACE: Smart unload error: {e}")

        return

    printer = get_printer()

    try:
        current_tool = manager.state.get("ace_current_index", -1)

        # Empty-slot guard BEFORE homing/heating/feeding: ACE2 firmware ACKs
        # a feed on an empty slot and spins for minutes until the toolhead
        # sensor timeout. Raising routes into the failure handling below
        # (pause + Retry prompt mid-print, macro abort at startup, plain
        # message when idle).
        manager.ensure_tool_slot_loaded(tool_index)

        try:
            toolhead = printer.lookup_object('toolhead')
            reactor = printer.get_reactor()
            kin_status = toolhead.get_kinematics().get_status(reactor.monotonic())
            homed_axes = kin_status.get('homed_axes', '')

            if 'xyz' not in homed_axes:
                gcode = printer.lookup_object("gcode")
                gcode.respond_info("ACE: Printer not homed, homing now...")
                gcode.run_script_from_command("G28")

        except Exception as e:
            gcode = printer.lookup_object("gcode")
            gcode.respond_info(f"ACE: Warning - could not verify homing: {e}")

        status = manager.perform_tool_change(current_tool, tool_index)
        printer.lookup_object("gcode").respond_info(f"ACE: perform_tool_change result status: {status}")
        printer.lookup_object("gcode").run_script_from_command("SET_IDLE_TIMEOUT")
        printer.lookup_object("gcode").respond_info(status)

    except Exception as e:
        gcode = printer.lookup_object("gcode")

        print_stats = printer.lookup_object('print_stats', None)
        is_printing = False
        print_state = ''

        if print_stats:
            reactor = printer.get_reactor()
            stats = print_stats.get_status(reactor.monotonic())
            print_state = stats.get('state') or ''
            is_printing = print_state in ['printing', 'paused']

        # Check if this is a startup toolchange (G9111) vs regular print toolchange.
        # During startup we must NOT pin the requested tool on failure just because
        # print_stats already reports "printing".
        ace_state = printer.lookup_object('gcode_macro _ACE_STATE', None)
        is_startup = False
        if ace_state and hasattr(ace_state, 'variables'):
            is_startup = ace_state.variables.get('startup_toolchange', 0) == 1

        # Decide which tool is physically in the path after the failure —
        # in every context the deciding evidence is filament_pos: "nozzle"/
        # "splitter" means the previous tool never left the path.
        fallback_tool = current_tool if 'current_tool' in locals() else manager.state.get("ace_current_index", -1)
        filament_pos = None
        try:
            filament_pos = manager.state.get("ace_filament_pos")
        except Exception:
            filament_pos = None

        if is_printing and not is_startup:
            # Mid-print failure. Pinning the REQUESTED tool as current is only
            # valid when the sequence got past the unload (target filament may
            # be partially in the path, e.g. "feed to toolhead failed").
            # If filament_pos still reads "nozzle"/"splitter", the failure
            # happened BEFORE the previous tool was unloaded (e.g. the
            # empty-slot pre-flight guard) — the previous tool is still
            # physically loaded, so it must stay the current tool. Otherwise
            # the Retry prompt re-runs T<n> with current==target and lands in
            # the "already loaded" reselection path, which trusts the poisoned
            # index, skips the real toolchange, and enables a second feed
            # assist in parallel with the still-loaded previous tool's.
            if filament_pos in (FILAMENT_STATE_NOZZLE, FILAMENT_STATE_SPLITTER):
                active_tool = fallback_tool
            else:
                active_tool = tool_index
        else:
            # Idle/startup failure. Decide which tool is physically in the path.
            if filament_pos == FILAMENT_STATE_BOWDEN:
                # The unload of the previous tool completed (filament_pos only
                # becomes "bowden" after a successful retract). If the sensors
                # still report filament, the tool we just tried to LOAD is stuck
                # in the path, so it must become the current tool — otherwise the
                # next toolchange loses track of it and cycles blindly through
                # parked slots (which can pull an innocent slot out of the ACE).
                if manager.is_filament_path_free_instant():
                    active_tool = -1
                else:
                    active_tool = tool_index
            else:
                # The unload did not complete (filament_pos still "nozzle"/
                # "splitter") — the previous tool is still in the path.
                active_tool = fallback_tool

        manager.state.set(
            "ace_current_index",
            active_tool
        )

        gcode.run_script_from_command(
            f"SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE={active_tool}"
        )

        gcode.respond_info(f"ACE: Tool change to T{tool_index} FAILED: {e}")
        if is_printing:
            if active_tool == tool_index:
                gcode.respond_info(
                    f"ACE: State updated - current tool marked as T{tool_index} (failed load, print recovery)"
                )
            else:
                gcode.respond_info(
                    f"ACE: State preserved - current tool remains T{active_tool} "
                    f"(failed before unload, previous tool still loaded)"
                )
        else:
            if active_tool == -1 and filament_pos == FILAMENT_STATE_BOWDEN:
                gcode.respond_info("ACE: State updated - no active tool (idle/startup failure after unload)")
            else:
                gcode.respond_info(
                    f"ACE: State preserved - current tool remains T{active_tool} (idle/startup failure)"
                )

        # During G9111: startup_toolchange=1 -> raise exception to abort macro
        # During print: startup_toolchange=0 -> pause and show dialog for recovery

        # instance_num = get_instance_from_tool(tool_index)
        # slot_index = get_local_slot(tool_index, instance_num)

        if is_printing and not is_startup:
            gcode.run_script_from_command('PAUSE')

            try:
                gcode.run_script_from_command(
                    'RESPOND TYPE=command MSG="action:prompt_begin Tool Change Failed"'
                )

                error_text = str(e).split('\n')[0].replace('"', '\\"')
                prompt_text = f"Tool change to T{tool_index} failed! Error: {error_text}"

                gcode.run_script_from_command(
                    f'RESPOND TYPE=command MSG="action:prompt_text {prompt_text}"'
                )

                gcode.run_script_from_command(
                    f'RESPOND TYPE=command MSG="action:prompt_button Retry T{tool_index}|T{tool_index}|primary"'
                )

                gcode.run_script_from_command(
                    'RESPOND TYPE=command MSG="action:prompt_button Extrude 100mm|'
                    '_EXTRUDE LENGTH=100 SPEED=300|secondary"'
                )

                gcode.run_script_from_command(
                    'RESPOND TYPE=command MSG="action:prompt_button Retract 100mm|'
                    '_RETRACT LENGTH=100 SPEED=300|secondary"'
                )

                gcode.run_script_from_command(
                    'RESPOND TYPE=command MSG="action:prompt_footer_button Resume|RESUME|primary"'
                )

                gcode.run_script_from_command(
                    'RESPOND TYPE=command MSG="action:prompt_footer_button Cancel Print|CANCEL_PRINT|error"'
                )

                gcode.run_script_from_command(
                    'RESPOND TYPE=command MSG="action:prompt_show"'
                )

            except Exception as dialog_error:
                gcode.respond_info(f"Failed to show error dialog: {dialog_error}")

            # A failure while print_stats still reports PAUSED means this
            # toolchange was issued by a resume-in-flight (the RESUME macro
            # re-activating the tool before BASE_RESUME).  The PAUSE above
            # is then a no-op - the printer never left the paused state -
            # and returning normally would let the RESUME macro continue:
            # purge with no filament, BASE_RESUME, and printing into air.
            # Raising aborts the remaining macro lines; the Retry prompt
            # above stays up and the printer stays paused.
            if print_state == 'paused':
                raise gcmd.error(
                    f"Tool change to T{tool_index} failed - aborting resume, "
                    f"printer stays paused. Use the prompt to retry or cancel."
                )
        else:
            # No RESUME cycle will follow this failure (idle/manual toolchange,
            # or startup toolchange that aborts the macro via gcmd.error below)
            # -- the RESUME macro's use of ace_target_index only applies to the
            # is_printing PAUSE/prompt branch above. Clear it here so callers
            # (KlipperScreen, ACE_GET_CURRENT_INDEX, etc.) stop reporting a
            # permanently "unconfirmed" toolchange for an attempt that has
            # already been resolved (ace_current_index already reflects the
            # outcome above).
            manager.state.set("ace_target_index", -1)

            gcode.run_script_from_command("M104 S0")

            if is_startup:
                gcode.respond_info(
                    "ACE: Initial toolchange failed, cancel print and switching extruder heater off"
                )
                raise gcmd.error(
                    f"Tool change to T{tool_index} failed during startup: {str(e)}"
                )

            # Plain idle/manual toolchange - no print is running, nothing to cancel
            gcode.respond_info("ACE: Toolchange failed, switching extruder heater off")
            raise gcmd.error(f"Tool change to T{tool_index} failed: {str(e)}")


def cmd_ACE_SET_RETRACT_SPEED(gcmd):
    """Update retract speed during operation. T=<tool> or INSTANCE= INDEX=, SPEED= required."""
    ace, slot = ace_get_instance_and_slot(gcmd)
    speed = gcmd.get_float("SPEED")

    if not (0 <= slot < ace.SLOT_COUNT):
        raise gcmd.error(f"Invalid slot {slot}")
    if speed <= 0:
        raise gcmd.error(f"SPEED must be positive, got {speed}")

    if ace._change_retract_speed(slot, speed):
        gcmd.respond_info(
            f"ACE[{ace.instance_num}]: Retract speed updated to {speed}mm/s on slot {slot}"
        )
    else:
        gcmd.respond_info(
            f"ACE[{ace.instance_num}]: Retract speed update to {speed}mm/s on slot {slot} failed"
        )


def cmd_ACE_SET_FEED_SPEED(gcmd):
    """Update feed speed during operation. T=<tool> or INSTANCE= INDEX=, SPEED= required."""
    ace, slot = ace_get_instance_and_slot(gcmd)
    speed = gcmd.get_float("SPEED")

    if not (0 <= slot < ace.SLOT_COUNT):
        raise gcmd.error(f"Invalid slot {slot}")
    if speed <= 0:
        raise gcmd.error(f"SPEED must be positive, got {speed}")

    if ace._change_feed_speed(slot, speed):
        gcmd.respond_info(
            f"ACE[{ace.instance_num}]: Feed speed updated to {speed}mm/s on slot {slot}"
        )
    else:
        gcmd.respond_info(
            f"ACE[{ace.instance_num}]: Feed speed update to {speed}mm/s on slot {slot} failed"
        )


def cmd_ACE_DEBUG_SENSORS(gcmd):
    """Debug: Print all sensor states (toolhead, RDM, path-free) - sensors are shared across all ACE instances."""
    try:
        if not INSTANCE_MANAGERS:
            gcmd.respond_info("ACE: No instances configured")
            return

        gcmd.respond_info("=== Printer Filament Sensor Debug Info ===")

        # Get any manager - sensors are shared across all instances
        manager = list(INSTANCE_MANAGERS.values())[0]

        try:
            toolhead_state = manager.get_switch_state(SENSOR_TOOLHEAD)
            state_str = "TRIGGERED" if toolhead_state else "CLEAR"
            gcmd.respond_info(f"Toolhead Sensor: {state_str}")
        except Exception as e:
            gcmd.respond_info(f"Toolhead Sensor: ERROR - {e}")

        if manager.has_rdm_sensor():
            try:
                rdm_state = manager.get_switch_state(SENSOR_RDM)
                state_str = "TRIGGERED" if rdm_state else "CLEAR"
                gcmd.respond_info(f"RDM Sensor: {state_str}")
            except Exception as e:
                gcmd.respond_info(f"RDM Sensor: ERROR - {e}")

        try:
            path_free = manager.is_filament_path_free()
            state_str = "YES" if path_free else "NO"
            gcmd.respond_info(f"Path Free: {state_str}")
        except Exception as e:
            gcmd.respond_info(f"Path Free: ERROR - {e}")

        gcmd.respond_info("\n=== End Sensor Debug ===")

    except Exception as e:
        gcmd.respond_info(f"ACE_DEBUG_SENSORS error: {e}")


def cmd_ACE_DEBUG_STATE(gcmd):
    """Debug: Print manager and instance state information for all instances."""
    try:
        if not INSTANCE_MANAGERS:
            gcmd.respond_info("ACE: No instances configured")
            return

        gcmd.respond_info("=== ACE State Debug Info ===")
        gcmd.respond_info(f"ACEPRO driver: {get_driver_version()}")

        def debug_instance_state(instance_num, mgr, instance):
            gcmd.respond_info(f"\nACE Manager[{instance_num}]:")

            try:
                gcmd.respond_info(f"  ACE Count: {mgr.ace_count}")
                runout_active = mgr.runout_monitor.runout_detection_active
                gcmd.respond_info(f"  Runout Detection: {runout_active}")
                current_index = mgr.state.get("ace_current_index", -1)
                target_index = mgr.state.get("ace_target_index", -1)
                gcmd.respond_info(f"  Current Tool Index: {current_index}")
                gcmd.respond_info(f"  Target Tool Index: {target_index}")
            except Exception as e:
                gcmd.respond_info(f"  Manager state ERROR: {e}")

            try:
                if hasattr(mgr, "instances"):
                    for inst in mgr.instances:
                        if hasattr(inst, "instance_num"):
                            inst_num = inst.instance_num
                        else:
                            inst_num = "?"
                        gcmd.respond_info(f"\n  Instance[{inst_num}]:")

                        try:
                            if hasattr(inst, "tool_offset"):
                                offset = inst.tool_offset
                                gcmd.respond_info(f"    Tool: T{offset}-T{offset + 3}")
                        except Exception:
                            pass

                        try:
                            if hasattr(inst, "current_slot"):
                                slot = inst.current_slot
                                gcmd.respond_info(f"    Current Slot: {slot}")
                        except Exception:
                            pass

                        try:
                            if hasattr(inst, "is_connected"):
                                connected = inst.is_connected
                                gcmd.respond_info(f"    Connected: {connected}")
                        except Exception:
                            pass

                        try:
                            if hasattr(inst, "inventory"):
                                loaded = sum(1 for item in inst.inventory if item and item.get("status") == "loaded")
                                gcmd.respond_info(f"    Inventory: {loaded}/4 loaded")
                        except Exception:
                            pass

            except Exception as e:
                gcmd.respond_info(f"  Instance ERROR: {e}")

        for_each_instance(debug_instance_state)

        gcmd.respond_info("\n=== End State Debug ===")

    except Exception as e:
        gcmd.respond_info(f"ACE_DEBUG_STATE error: {e}")


def cmd_ACE_DEBUG_CHECK_SPOOL_READY(gcmd):
    """Debug: Test check_and_wait_for_spool_ready. TOOL= required."""
    try:
        tool = gcmd.get_int("TOOL")
        manager = ace_get_manager(0)

        gcmd.respond_info(f"ACE: Testing spool readiness for tool {tool}...")
        manager.check_and_wait_for_spool_ready(tool)
        gcmd.respond_info(f"ACE: Spool for tool {tool} is ready and available.")

    except Exception as e:
        gcmd.respond_info(f"ACE_DEBUG_CHECK_SPOOL_READY error: {e}")


def _set_rfid_sync_for_instance_or_all(gcmd, enabled):
    """Helper: Set RFID sync state for specific instance or all instances."""
    instance_num = gcmd.get_int("INSTANCE", None)
    state_name = "ENABLED" if enabled else "DISABLED"

    if instance_num is not None:
        if instance_num not in INSTANCE_MANAGERS:
            gcmd.respond_info(f"ERROR: Instance {instance_num} not found")
            return

        manager = INSTANCE_MANAGERS[instance_num]
        instance = manager.instances[instance_num]
        instance.rfid_inventory_sync_enabled = enabled
        gcmd.respond_info(f"ACE[{instance_num}]: RFID inventory sync {state_name}")
    else:
        count = 0

        def set_rfid(inst_num, manager, instance):
            nonlocal count
            instance.rfid_inventory_sync_enabled = enabled
            count += 1

        for_each_instance(set_rfid)
        gcmd.respond_info(f"ACE: RFID inventory sync {state_name} for all {count} instances")


def cmd_ACE_ENABLE_RFID_SYNC(gcmd):
    """Enable RFID inventory sync. [INSTANCE=] - omit to enable all instances."""
    try:
        _set_rfid_sync_for_instance_or_all(gcmd, True)
    except Exception as e:
        gcmd.respond_info(f"ACE_ENABLE_RFID_SYNC error: {e}")


def cmd_ACE_DISABLE_RFID_SYNC(gcmd):
    """Disable RFID inventory sync. [INSTANCE=] - omit to disable all instances."""
    try:
        _set_rfid_sync_for_instance_or_all(gcmd, False)
    except Exception as e:
        gcmd.respond_info(f"ACE_DISABLE_RFID_SYNC error: {e}")


def cmd_ACE_RFID_SYNC_STATUS(gcmd):
    """Query RFID inventory sync status. [INSTANCE=] - omit to show all instances."""
    try:
        instance_num = gcmd.get_int("INSTANCE", None)

        if instance_num is not None:
            if instance_num not in INSTANCE_MANAGERS:
                gcmd.respond_info(f"ERROR: Instance {instance_num} not found")
                return

            manager = INSTANCE_MANAGERS[instance_num]
            instance = manager.instances[instance_num]
            status = "ENABLED" if instance.rfid_inventory_sync_enabled else "DISABLED"
            gcmd.respond_info(f"ACE[{instance_num}]: RFID inventory sync is {status}")
        else:
            gcmd.respond_info("=== ACE RFID Inventory Sync Status ===")

            def show_rfid_status(inst_num, manager, instance):
                status = "ENABLED" if instance.rfid_inventory_sync_enabled else "DISABLED"
                gcmd.respond_info(f"  Instance {inst_num}: {status}")

            for_each_instance(show_rfid_status)

    except Exception as e:
        gcmd.respond_info(f"ACE_RFID_SYNC_STATUS error: {e}")


def cmd_ACE_DEBUG_INJECT_SENSOR_STATE(gcmd):
    """Debug: Inject sensor state for testing. TOOLHEAD=0/1 RDM=0/1 or RESET=1. All optional."""
    try:
        manager = ace_get_manager(0)

        if gcmd.get_int("RESET", 0):
            manager._sensor_override = None
            gcmd.respond_info("ACE: Sensor override DISABLED - using real sensors")
            return

        toolhead = gcmd.get_int("TOOLHEAD", None)
        rdm = gcmd.get_int("RDM", None)

        if toolhead is None and rdm is None:
            gcmd.respond_info("ACE: No sensor state specified. Use TOOLHEAD=0/1, RDM=0/1, or RESET=1")
            return

        override = {
            **({SENSOR_TOOLHEAD: bool(toolhead)} if toolhead is not None else {}),
            **({SENSOR_RDM: bool(rdm)} if rdm is not None else {})
        }

        manager._sensor_override = override

        def format_sensor_state(sensor):
            value = override.get(sensor, None)
            if value is True:
                return "TRIGGERED"
            elif value is False:
                return "CLEAR"
            else:
                return "NOT SET"

        gcmd.respond_info("ACE: Sensor override ENABLED:")
        gcmd.respond_info(f"  Toolhead: {format_sensor_state(SENSOR_TOOLHEAD)}")
        gcmd.respond_info(f"  RDM: {format_sensor_state(SENSOR_RDM)}")

    except Exception as e:
        gcmd.respond_info(f"ACE_DEBUG_INJECT_SENSOR_STATE error: {e}")


def cmd_ACE_SET_ENDLESS_SPOOL_MODE(gcmd):
    """Set endless spool match mode. MODE=exact|material|next"""
    try:
        mode = gcmd.get("MODE", "exact").lower()
        valid_modes = {"exact": "EXACT", "material": "MATERIAL", "next": "NEXT READY"}

        if mode not in valid_modes:
            gcmd.respond_info("ACE: Invalid mode. Use MODE=exact, MODE=material, or MODE=next")
            return

        manager = ace_get_manager(0)
        manager.state.set_and_save(
            "ace_endless_spool_match_mode", mode
        )
        gcmd.respond_info(f"ACE: Endless spool mode set to: {valid_modes[mode]}")

    except Exception as e:
        gcmd.respond_info(f"ACE_SET_ENDLESS_SPOOL_MODE error: {e}")


def cmd_ACE_GET_ENDLESS_SPOOL_MODE(gcmd):
    """Query endless spool match mode (EXACT, MATERIAL, or NEXT READY)."""
    try:
        manager = ace_get_manager(0)
        mode = manager.state.get("ace_endless_spool_match_mode", "exact")
        mode_display = {"exact": "EXACT", "material": "MATERIAL", "next": "NEXT READY"}.get(mode, "EXACT")

        gcmd.respond_info(f"ACE: Endless spool mode: {mode_display}")

    except Exception as e:
        gcmd.respond_info(f"ACE_GET_ENDLESS_SPOOL_MODE error: {e}")


def cmd_ACE_CHANGE_TOOL_WRAPPER(gcmd):
    """Change tool or unload. TOOL=<index> to change tool, or TOOL=-1 to unload."""
    try:
        tool_index = gcmd.get_int("TOOL")
        manager = ace_get_manager(0)
        cmd_ACE_CHANGE_TOOL(manager, gcmd, tool_index)
    except Exception as e:
        gcmd.respond_info(f"ACE_CHANGE_TOOL error: {e}")


def cmd_ACE_FULL_UNLOAD(gcmd):
    """Full unload - retract until slot empty. TOOL=<index> or TOOL=ALL or [no TOOL=current]. Clears tool on success."""
    try:
        manager = ace_get_manager(0)

        # Check if TOOL=ALL was specified
        tool_param = gcmd.get("TOOL", None)

        if tool_param and str(tool_param).upper() == "ALL":
            # Get current state
            current_tool_index = manager.state.get("ace_current_index", -1)
            filament_pos = manager.state.get("ace_filament_pos", FILAMENT_STATE_BOWDEN)

            # Full unload all non-empty slots across all instances
            gcmd.respond_info("ACE: Full unload ALL - processing all non-empty slots")
            gcmd.respond_info(f"ACE: Current tool: T{current_tool_index}, Position: {filament_pos}")

            total_slots = 0
            success_count = 0
            failed_slots = []
            skipped_slots = []

            def unload_instance_slots(instance_num, mgr, instance):
                nonlocal total_slots, success_count

                gcmd.respond_info(f"\nACE[{instance_num}]: Checking slots...")

                for local_slot in range(instance.SLOT_COUNT):
                    slot_status = instance.inventory[local_slot].get("status", "empty")
                    tool_num = instance.tool_offset + local_slot

                    # Skip if slot is empty
                    if slot_status == "empty":
                        gcmd.respond_info(
                            f"  T{tool_num} (slot {local_slot}): Empty, skipping"
                        )
                        continue

                    # Skip if this is the current loaded tool at nozzle
                    if tool_num == current_tool_index and filament_pos == FILAMENT_STATE_NOZZLE:
                        skipped_slots.append(f"T{tool_num}")
                        gcmd.respond_info(
                            f"  T{tool_num} (slot {local_slot}): Currently loaded at nozzle, skipping"
                        )
                        continue

                    total_slots += 1
                    gcmd.respond_info(
                        f"  T{tool_num} (slot {local_slot}): Status '{slot_status}', unloading..."
                    )

                    try:
                        success = manager.full_unload_slot(tool_num)

                        if success:
                            success_count += 1
                            gcmd.respond_info(
                                f"  T{tool_num} (slot {local_slot}): ✓ Successfully unloaded"
                            )
                        else:
                            failed_slots.append(f"T{tool_num}")
                            gcmd.respond_info(
                                f"  T{tool_num} (slot {local_slot}): ✗ Unload failed or incomplete"
                            )

                    except Exception as e:
                        failed_slots.append(f"T{tool_num}")
                        gcmd.respond_info(
                            f"  T{tool_num} (slot {local_slot}): ✗ Error: {e}"
                        )

            for_each_instance(unload_instance_slots)

            gcmd.respond_info("\n=== Full Unload ALL Summary ===")
            gcmd.respond_info(f"Total slots processed: {total_slots}")
            gcmd.respond_info(f"Successfully unloaded: {success_count}")

            if skipped_slots:
                gcmd.respond_info(f"Skipped (loaded at nozzle): {len(skipped_slots)} ({', '.join(skipped_slots)})")

            if failed_slots:
                gcmd.respond_info(f"Failed: {len(failed_slots)} ({', '.join(failed_slots)})")
            else:
                gcmd.respond_info("Failed: 0")

            # Clear current tool index if all successful
            if success_count == total_slots and total_slots > 0:
                manager.state.set("ace_current_index", -1)
                gcmd.respond_info("\nACE: All slots fully unloaded - current tool cleared")

            manager.state.flush()
            return

        tool = gcmd.get_int('TOOL', -1)

        if tool < 0:
            tool = manager.state.get("ace_current_index", -1)

        if tool < 0:
            raise gcmd.error("No tool specified and no current tool set. Use TOOL=<index> or TOOL=ALL")

        success = manager.full_unload_slot(tool)

        if success:
            manager.state.set_and_save("ace_current_index", -1)
            gcmd.respond_info(f"ACE: Tool {tool} fully unloaded")
        else:
            gcmd.respond_info(f"ACE: Tool {tool} full unload failed or incomplete")

    except Exception as e:
        gcmd.respond_info(f"ACE_FULL_UNLOAD error: {e}")


def cmd_ACE_SHOW_INSTANCE_CONFIG(gcmd):
    """Show resolved config for ACE instance(s). [INSTANCE=<num>] - omit to compare all instances."""
    try:
        instance_num = gcmd.get_int("INSTANCE", None)

        if instance_num is not None:
            # Show config for specific instance
            if instance_num not in INSTANCE_MANAGERS:
                gcmd.respond_info(f"ERROR: Instance {instance_num} not found")
                return

            manager = INSTANCE_MANAGERS[instance_num]
            instance = manager.instances[instance_num]

            gcmd.respond_info(f"=== ACE[{instance_num}] Configuration ===")
            gcmd.respond_info(f"Tool Range: T{instance.tool_offset}-T{instance.tool_offset + 3}")
            gcmd.respond_info("\nOverridable Parameters:")

            missing_params = []
            for param in OVERRIDABLE_PARAMS:
                if hasattr(instance, param):
                    value = getattr(instance, param)

                    # Format value based on type
                    if isinstance(value, float):
                        value_str = f"{value:.2f}"
                    else:
                        value_str = str(value)

                    # Add units for known parameters
                    if "length" in param or "feeding_length" in param:
                        unit = "mm"
                    elif "speed" in param and "multiplier" not in param:
                        unit = "mm/s" if "retract" in param or "feed" in param else "mm/min"
                    elif "temperature" in param or "temp" in param:
                        unit = "°C"
                    elif "heartbeat" in param:
                        unit = "s"
                    else:
                        unit = ""

                    unit_str = f" {unit}" if unit else ""
                    gcmd.respond_info(f"  {param:40s}: {value_str}{unit_str}")
                else:
                    missing_params.append(param)
                    gcmd.respond_info(f"  {param:40s}: ⚠ NOT SET (missing from instance)")

            if missing_params:
                gcmd.respond_info(f"\n⚠ WARNING: {len(missing_params)} parameters missing from instance:")
                gcmd.respond_info(f"  {', '.join(missing_params)}")
                gcmd.respond_info("  These should be added to AceInstance.__init__()")

            # Show non-overridable settings
            gcmd.respond_info("\nNon-Overridable Settings:")
            gcmd.respond_info(f"  {'baud':40s}: {instance.baud}")
            gcmd.respond_info(
                f"  {'filament_runout_sensor_name_rdm':40s}: {instance.filament_runout_sensor_name_rdm}"
            )
            gcmd.respond_info(
                f"  {'filament_runout_sensor_name_nozzle':40s}: {instance.filament_runout_sensor_name_nozzle}"
            )
            gcmd.respond_info(
                f"  {'feed_assist_active_after_ace_connect':40s}: {instance.feed_assist_active_after_ace_connect}"
            )
            gcmd.respond_info(
                f"  {'rfid_inventory_sync_enabled':40s}: {instance.rfid_inventory_sync_enabled}"
            )

        else:
            # Show config for all instances (compare values)
            gcmd.respond_info("=== ACE Configuration Comparison (All Instances) ===")

            missing_all = []
            for param in OVERRIDABLE_PARAMS:
                # Collect values from all instances
                values_by_instance = {}
                missing_count = 0

                def collect_param_value(inst_num, manager, instance):
                    nonlocal missing_count
                    if hasattr(instance, param):
                        value = getattr(instance, param)
                        values_by_instance[inst_num] = value
                    else:
                        missing_count += 1

                for_each_instance(collect_param_value)

                # Skip if ALL instances are missing it
                if missing_count == len(INSTANCE_MANAGERS):
                    missing_all.append(param)
                    continue

                # Check if all instances have same value
                unique_values = set(values_by_instance.values())

                if len(unique_values) == 1:
                    # All same - show once
                    value = list(unique_values)[0]

                    if isinstance(value, float):
                        value_str = f"{value:.2f}"
                    else:
                        value_str = str(value)

                    # Add units
                    if "length" in param or "feeding_length" in param:
                        unit = "mm"
                    elif "speed" in param and "multiplier" not in param:
                        unit = "mm/s" if "retract" in param or "feed" in param else "mm/min"
                    elif "temperature" in param or "temp" in param:
                        unit = "°C"
                    elif "heartbeat" in param:
                        unit = "s"
                    else:
                        unit = ""

                    unit_str = f" {unit}" if unit else ""
                    gcmd.respond_info(f"{param:40s}: {value_str}{unit_str} (all instances)")

                else:
                    # Different values - show per instance
                    gcmd.respond_info(f"{param}:")

                    def show_instance_value(inst_num, manager, instance):
                        if inst_num in values_by_instance:
                            value = values_by_instance[inst_num]

                            if isinstance(value, float):
                                value_str = f"{value:.2f}"
                            else:
                                value_str = str(value)

                            # Add units
                            if "length" in param or "feeding_length" in param:
                                unit = "mm"
                            elif "speed" in param and "multiplier" not in param:
                                unit = "mm/s" if "retract" in param or "feed" in param else "mm/min"
                            elif "temperature" in param or "temp" in param:
                                unit = "°C"
                            elif "heartbeat" in param:
                                unit = "s"
                            else:
                                unit = ""

                            unit_str = f" {unit}" if unit else ""
                            gcmd.respond_info(f"  Instance {inst_num}: {value_str}{unit_str}")
                        else:
                            gcmd.respond_info(f"  Instance {inst_num}: ⚠ NOT SET")

                    for_each_instance(show_instance_value)

            if missing_all:
                gcmd.respond_info(f"\n⚠ WARNING: {len(missing_all)} parameters missing from ALL instances:")
                for param in missing_all:
                    gcmd.respond_info(f"  {param:40s}: ⚠ NOT SET (add to AceInstance.__init__)")

            gcmd.respond_info("\n=== Non-Overridable Settings (Instance 0) ===")
            manager = INSTANCE_MANAGERS[0]
            instance = manager.instances[0]
            gcmd.respond_info(f"  {'baud':40s}: {instance.baud}")
            gcmd.respond_info(
                f"  {'filament_runout_sensor_name_rdm':40s}: {instance.filament_runout_sensor_name_rdm}"
            )
            gcmd.respond_info(
                f"  {'filament_runout_sensor_name_nozzle':40s}: {instance.filament_runout_sensor_name_nozzle}"
            )
            gcmd.respond_info(
                f"  {'feed_assist_active_after_ace_connect':40s}: {instance.feed_assist_active_after_ace_connect}"
            )
            gcmd.respond_info(f"  {'rfid_inventory_sync_enabled':40s}: {instance.rfid_inventory_sync_enabled}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        gcmd.respond_info(f"ACE_SHOW_INSTANCE_CONFIG error: {e}")


def cmd_ACE_FLUSH(gcmd):
    """Persist any pending variable changes to disk immediately."""
    manager = ace_get_manager(0)
    if manager.state.has_pending:
        manager.state.flush()
        gcmd.respond_info("ACE: Pending state flushed to disk")
    else:
        gcmd.respond_info("ACE: No pending state to flush")


ACE_COMMANDS = [
    ("ACE_GET_STATUS", cmd_ACE_GET_STATUS, "Query ACE status. INSTANCE= or TOOL=, VERBOSE=1 for detailed output"),
    ("ACE_GET_CONNECTION_STATUS", cmd_ACE_GET_CONNECTION_STATUS,
     "Get connection status for all ACE instances (connected, stable, retry info)"),
    ("ACE_RECONNECT", cmd_ACE_RECONNECT, "Reconnect ACE serial. INSTANCE= DELAY=5"),
    ("ACE_REDETECT", cmd_ACE_REDETECT,
     "Re-detect an instance mis-typed at startup (e.g. late ACE2 adapter). Discovery-gated, safe."),
    ("ACE_GET_CURRENT_INDEX", cmd_ACE_GET_CURRENT_INDEX, "Query currently loaded tool index"),
    ("ACE_FEED", cmd_ACE_FEED, "Feed filament. T=<tool> or INSTANCE= INDEX=, LENGTH=, [SPEED=]"),
    ("ACE_STOP_FEED", cmd_ACE_STOP_FEED, "Stop feeding. T=<tool> or INSTANCE= INDEX="),
    ("ACE_RETRACT", cmd_ACE_RETRACT, "Retract filament. T=<tool> or INSTANCE= INDEX=, LENGTH=, [SPEED=]"),
    ("ACE_STOP_RETRACT", cmd_ACE_STOP_RETRACT, "Stop retraction. T=<tool> or INSTANCE= INDEX="),
    ("ACE_SMART_UNLOAD", cmd_ACE_SMART_UNLOAD,
     "Unload filament, trys also other filament slots if sensor still triggers after unload of current tool [TOOL=]"),
    ("ACE_SMART_LOAD", cmd_ACE_SMART_LOAD, "Load all non-empty slots to verification sensor."),
    ("_ACE_HANDLE_PRINT_END", cmd_ACE_HANDLE_PRINT_END, "Execute print end sequence (retract, cut, store)"),
    ("ACE_SET_SLOT", cmd_ACE_SET_SLOT,
     "Set slot: T=<tool> or INSTANCE= INDEX=, COLOR=<name>|R,G,B MATERIAL= TEMP= or EMPTY=1"),
    ("ACE_SAVE_INVENTORY", cmd_ACE_SAVE_INVENTORY, "Save inventory. INSTANCE="),
    ("ACE_START_DRYING", cmd_ACE_START_DRYING, "Start dryer. [INSTANCE=] TEMP= [DURATION=240]"),
    ("ACE_STOP_DRYING", cmd_ACE_STOP_DRYING, "Stop dryer. [INSTANCE=]"),
    ("ACE_ENABLE_FEED_ASSIST", cmd_ACE_ENABLE_FEED_ASSIST, "Enable feed assist. T=<tool> or INSTANCE= INDEX="),
    ("ACE_DISABLE_FEED_ASSIST", cmd_ACE_DISABLE_FEED_ASSIST, "Disable feed assist. T=<tool> or INSTANCE= INDEX="),
    ("ACE_SET_PURGE_AMOUNT", cmd_ACE_SET_PURGE_AMOUNT, "Set purge parameters. PURGELENGTH= PURGESPEED= [INSTANCE=]"),
    ("ACE_QUERY_SLOTS", cmd_ACE_QUERY_SLOTS, "Query slots. INSTANCE= (omit to query all)"),
    ("ACE_ENABLE_ENDLESS_SPOOL", cmd_ACE_ENABLE_ENDLESS_SPOOL, "Enable endless spool"),
    ("ACE_DISABLE_ENDLESS_SPOOL", cmd_ACE_DISABLE_ENDLESS_SPOOL, "Disable endless spool"),
    ("ACE_ENDLESS_SPOOL_STATUS", cmd_ACE_ENDLESS_SPOOL_STATUS, "Query endless spool status."),
    ("ACE_ENABLE_RFID_SYNC", cmd_ACE_ENABLE_RFID_SYNC, "Enable RFID inventory sync. [INSTANCE=] optional"),
    ("ACE_DISABLE_RFID_SYNC", cmd_ACE_DISABLE_RFID_SYNC, "Disable RFID inventory sync. [INSTANCE=] optional"),
    ("ACE_DEBUG", cmd_ACE_DEBUG, "Send debug request to device. INSTANCE= METHOD= [PARAMS=]"),
    ("ACE_DEBUG_SENSORS", cmd_ACE_DEBUG_SENSORS, "Print all sensor states (toolhead, RDM, path-free)"),
    ("ACE_DEBUG_STATE", cmd_ACE_DEBUG_STATE, "Print manager and instance state information"),
    ("ACE_RESET_PERSISTENT_INVENTORY", cmd_ACE_RESET_PERSISTENT_INVENTORY, "Reset inventory to empty. INSTANCE="),
    ("ACE_RESET_SHARED_BUS_BINDINGS", cmd_ACE_RESET_SHARED_BUS_BINDINGS,
     "Clear persisted ACE2 shared-bus bindings. [RECONNECT=1|0] [DELAY=2.0]"),
    ("ACE_CLEANUP_STALE_VARS", cmd_ACE_CLEANUP_STALE_VARS,
     "List stale ACE saved variables; CONFIRM=1 deletes them (incl. old inventories)"),
    ("ACE_RESET_ACTIVE_TOOLHEAD", cmd_ACE_RESET_ACTIVE_TOOLHEAD, "Reset active toolhead state. INSTANCE="),
    ("ACE_DEBUG_SET_CURRENT_INDEX", cmd_ACE_DEBUG_SET_CURRENT_INDEX,
     "Override saved tool index. TOOL=<n> (0-based) or TOOL=-1 for no tool loaded"),
    ("ACE_DEBUG_SET_TARGET_INDEX", cmd_ACE_DEBUG_SET_TARGET_INDEX,
     "Override saved in-flight toolchange target. TOOL=<n> (0-based) or TOOL=-1 for none"),
    ("ACE_DEBUG_SET_FILAMENT_STATE", cmd_ACE_DEBUG_SET_FILAMENT_STATE,
     "Override saved filament position. STATE=bowden|splitter|toolhead|nozzle (omit STATE= to query)"),
    ("ACE_RFID_SYNC_STATUS", cmd_ACE_RFID_SYNC_STATUS, "Query RFID sync status. [INSTANCE=]"),
    ("ACE_DEBUG_INJECT_SENSOR_STATE", cmd_ACE_DEBUG_INJECT_SENSOR_STATE,
     "Inject sensor state for testing. TOOLHEAD=0/1 RDM=0/1 or RESET=1"),
    ("ACE_SET_ENDLESS_SPOOL_MODE", cmd_ACE_SET_ENDLESS_SPOOL_MODE,
     "Set endless spool match mode. MODE=exact|material|next"),
    ("ACE_GET_ENDLESS_SPOOL_MODE", cmd_ACE_GET_ENDLESS_SPOOL_MODE, "Query current match mode"),
    ("ACE_CHANGE_TOOL", cmd_ACE_CHANGE_TOOL_WRAPPER, "Change tool or unload. TOOL=<index> or TOOL=-1"),
    ("ACE_SET_RETRACT_SPEED", cmd_ACE_SET_RETRACT_SPEED,
     "Command to update retract speed. T=<tool> or INSTANCE= INDEX=, SPEED="),
    ("ACE_SET_FEED_SPEED", cmd_ACE_SET_FEED_SPEED,
     "Command to update feed speed. T=<tool> or INSTANCE= INDEX=, SPEED="),
    ("ACE_FULL_UNLOAD", cmd_ACE_FULL_UNLOAD,
     "Full unload until slot empty. TOOL=<index> or TOOL=ALL or [no TOOL=current]"),
    ("ACE_SHOW_INSTANCE_CONFIG", cmd_ACE_SHOW_INSTANCE_CONFIG,
     "Show resolved config for ACE instance(s). [INSTANCE=<num>]"),
    ("ACE_FLUSH", cmd_ACE_FLUSH,
     "Persist any pending variable changes to disk immediately"),
    ("ACE_TANGLE_DETECTION", cmd_ACE_TANGLE_DETECTION,
     "Toggle tangle detection live.  ENABLE=0/1 (no arg → query state)"),
    ("_ACE_TANGLE_DISABLE_AND_RESUME", cmd__ACE_TANGLE_DISABLE_AND_RESUME,
     "Internal: prompt button — disable tangle detection and resume"),
]


def register_all_commands(printer):
    """Register all ACE gcode commands with Klipper."""
    gcode = printer.lookup_object("gcode")

    for cmd_name, cmd_handler, cmd_desc in ACE_COMMANDS:
        gcode.register_command(cmd_name, safe_gcode_command(cmd_handler), desc=cmd_desc)
