import logging
import json

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

from ks_includes.screen_panel import ScreenPanel  # noqa: E402
from ks_includes.widgets.keypad import Keypad  # noqa: E402


class Panel(ScreenPanel):
    FILAMENT_TEMP_DEFAULTS = {
        "PLA": 200,
        "PETG": 235,
        "ABS": 240,
        "ASA": 245,
        "PVA": 185,
        "HIPS": 230,
        "PC": 260,
        "PLA+": 210,
        "PLA Glow": 210,
        "PLA High Speed": 215,
        "PLA Marble": 205,
        "PLA Matte": 205,
        "PLA SE": 210,
        "PLA Silk": 215,
        "TPU": 210,
    }

    def material_options(self):
        """Single source of truth for selectable materials."""
        return ["Empty"] + list(self.FILAMENT_TEMP_DEFAULTS.keys())

    def __init__(self, screen, title):
        super().__init__(screen, title)

        # Detect ACE instances
        self.ace_instances = self._detect_ace_instances()
        self.total_slots = len(self.ace_instances) * 4

        # Per-instance data storage
        self.instance_data = {}
        for instance_id in self.ace_instances:
            self.instance_data[instance_id] = {
                'connection_state': None,
                'inventory': [{
                    "material": "Empty",
                    "color": [0, 0, 0],
                    "temp": 0,
                    "status": "empty",
                    "rfid": False
                } for _ in range(4)],
                'tool_offset': instance_id * 4,
                'slot_buttons': [],
                'slot_labels': [],
                'slot_color_boxes': [],
                'slot_tool_labels': []
            }

        # Global state
        self.current_loaded_slot = -1
        self.endless_spool_enabled = False
        self.dryer_enabled = False
        self.ace_pro_enabled = True   # assume enabled until first RPC update
        self.toolhead_sensor = None   # True/False when known, None = sensor not configured
        self.rdm_sensor = None        # True/False when known, None = sensor not configured
        self.numpad_visible = False
        self.current_instance = 0  # Currently displayed instance
        self.selected_dryer_instance = "ALL"  # Selected dryer instance: 0, 1, or "ALL"
        self.rfid_sync_enabled = True  # Track RFID sync toggle locally

        # Initialize attributes used in various methods
        self._activation_timeout_id = None
        self._conn_poll_timer = None
        self.current_config_instance = None
        self.current_config_slot = None
        self.config_material = None
        self.config_color = None
        self.config_temp = None

        # UI elements created in dialogs
        self.material_btn = None
        self.config_color_preview = None
        self.color_btn = None
        self.temp_btn = None
        self.right_box = None
        self.right_color_preview = None
        self.rgb_display = None
        self.color_sliders = None
        self.config_keypad = None
        self.spool_tool_combo = None
        self.spool_selected_tool = None
        self.spool_keypad = None
        self.current_view = "main"  # Track current view for back handling

        # Create main screen
        self.create_main_screen()
        self.add_custom_css()

        self.initialize_loaded_slot()

        if self._printer.state == "ready":
            logging.info("ACE: Printer ready, querying immediately")
            GLib.idle_add(self.refresh_all_instances)
        else:
            logging.info(f"ACE: Printer not ready (state={self._printer.state}), waiting...")

    def activate(self):
        """Called when panel becomes active"""
        logging.info("ACE: Panel activated, refreshing data")

        if self._activation_timeout_id is not None:
            GLib.source_remove(self._activation_timeout_id)

        # If we were left on a sub-view (e.g., spool), rebuild the main ACE UI
        if getattr(self, "current_view", "main") != "main":
            self.return_to_main_screen()

        self._activation_timeout_id = GLib.timeout_add(
            200, self._do_activation_refresh
        )

        # Start connection status poll if not already running
        if self._conn_poll_timer is None:
            # One-shot immediate query, then repeating every 3s
            GLib.timeout_add(100, self._poll_connection_status_once)
            self._conn_poll_timer = GLib.timeout_add_seconds(3, self._poll_connection_status)

    def _do_activation_refresh(self):
        self._activation_timeout_id = None
        self.refresh_all_instances()
        return False

    def _poll_connection_status_once(self):
        """One-shot initial connection state query (called via timeout_add)."""
        self._poll_connection_status()
        return False  # do not repeat

    def _poll_connection_status(self):
        """Query connection_state for each ACE instance directly from Klipper."""
        try:
            direct_ws = getattr(
                getattr(getattr(self, "_screen", None), "_ws", None),
                "klippy", None
            )
            direct_ws = getattr(direct_ws, "_ws", None)
            if not callable(getattr(direct_ws, "send_method", None)):
                return True

            objects = {f"ace_instance_{i}": ["connection_state"] for i in self.ace_instances}

            def _cb(response, *_):
                try:
                    result = (response or {}).get("result", {})
                    status = (result or {}).get("status", {})
                    for instance_id in self.ace_instances:
                        key = f"ace_instance_{instance_id}"
                        val = (status.get(key) or {}).get("connection_state")
                        if val is not None:
                            self.instance_data[instance_id]["connection_state"] = str(val)
                    self._update_connection_status_label()
                except Exception as e:
                    logging.debug(f"ACE: connection poll callback error: {e}")

            direct_ws.send_method("printer.objects.query", {"objects": objects}, _cb)
        except Exception as e:
            logging.debug(f"ACE: connection poll error: {e}")
        return True  # keep GLib timer running

    def _update_connection_status_label(self):
        """Update top-bar label from per-instance connection_state strings."""
        if not hasattr(self, "conn_status_label") or self.conn_status_label is None:
            return
        first_issue = None
        any_unknown = False
        for instance_id in self.ace_instances:
            state = self.instance_data.get(instance_id, {}).get("connection_state", None)
            if state is None:
                any_unknown = True
            elif state != "connected":
                first_issue = first_issue or f"ACE[{instance_id}]: {state}"
        if first_issue is not None:
            self.conn_status_label.set_markup(
                f'<span foreground="orange"><b>{first_issue}</b></span>'
            )
        elif any_unknown:
            self.conn_status_label.set_markup(
                '<span foreground="gray">Checking...</span>'
            )
        else:
            self.conn_status_label.set_markup(
                '<span foreground="green"><b>Connection: OK</b></span>'
            )

    def _detect_ace_instances(self):
        """Detect available ACE instances from Klipper config, using ace_count if present"""
        instances = []
        try:
            ace_count = None
            if hasattr(self, '_screen') and hasattr(self._screen, 'printer'):
                # Try to get ace_count from config
                if hasattr(self._screen.printer, 'get_config_section'):
                    ace_section = self._screen.printer.get_config_section('ace')
                    if ace_section and 'ace_count' in ace_section:
                        try:
                            ace_count = int(ace_section['ace_count'])
                        except Exception:
                            ace_count = None
                # Fallback: scan config sections
                if ace_count is None and hasattr(self._screen.printer, 'get_config_section_list'):
                    config_sections = self._screen.printer.get_config_section_list()
                    for section in config_sections:
                        if section == 'ace':
                            instances.append(0)
                        elif section.startswith('ace '):
                            try:
                                instance_num = int(section.split()[1])
                                instances.append(instance_num)
                            except (ValueError, IndexError):
                                logging.warning(f"ACE: Could not parse instance number from '{section}'")
                elif ace_count is not None:
                    instances = list(range(ace_count))
        except Exception as e:
            logging.error(f"ACE: Error detecting instances: {e}", exc_info=True)
        if not instances:
            instances = [0]
            logging.info("ACE: No instances detected, using default [0]")
        else:
            logging.info(f"ACE: Detected instances: {instances}")
        return sorted(instances)

    def _send_gcode(self, gcode_command):
        """Safely send gcode command with connection validation"""
        try:
            if not hasattr(self, '_screen'):
                logging.error("ACE: _screen not available for gcode")
                return False

            if not hasattr(self._screen, '_ws'):
                logging.error("ACE: _ws not available for gcode")
                return False

            if not hasattr(self._screen._ws, 'klippy'):
                logging.error("ACE: klippy not available for gcode")
                return False

            self._screen._ws.klippy.gcode_script(gcode_command)
            return True
        except Exception as e:
            logging.error(f"ACE: Error sending gcode '{gcode_command}': {e}")
            self._screen.show_popup_message(
                "Connection error. Check Klipper status."
            )
            return False

    def _try_refresh_via_rpc(self):
        """
        Attempt to refresh ACE data via Moonraker JSON-RPC (objects.query).
        Returns True if data was fetched and processed, False to fall back to gcode.
        """
        try:
            ws = getattr(self, "_screen", None)
            klippy = getattr(getattr(ws, "_ws", None), "klippy", None)
            query_fn = None
            logging.debug(
                f"ACE: Klippy RPC methods (query/object): "
                f"{[m for m in dir(klippy) if 'query' in m or 'object' in m]}"
            )
            for candidate in ("query_objects", "objects_query", "queryObjects"):
                fn = getattr(klippy, candidate, None)
                if callable(fn):
                    query_fn = fn
                    break

            if not callable(query_fn):
                logging.debug("ACE: JSON-RPC query method not available; trying direct websocket query")
                # Try direct printer.objects.query via websocket send_method
                direct_ws = getattr(klippy, "_ws", None)
                if callable(getattr(direct_ws, "send_method", None)):
                    ace_fields = [
                        "slots",
                        "status",
                        "dryer",
                        "dryer_status",
                        "temp",
                        "enable_rfid",
                        "fan_speed",
                        "feed_assist_count",
                        "cont_assist_time",
                    ]
                    objects = {"ace_state": ["ace_instances", "current_index", "endless_spool_enabled", "endless_spool_match_mode", "ace_pro_enabled", "toolhead_sensor", "rdm_sensor"]}
                    for instance_id in self.ace_instances:
                        key = f"ace_instance_{instance_id}"
                        objects[key] = ace_fields

                    def _query_cb(response, *_):
                        try:
                            result = response.get("result") if isinstance(response, dict) else None
                            if isinstance(result, dict) and "status" in result:
                                status_payload = result.get("status", {})
                                logging.info(f"ACE: printer.objects.query result keys={list(status_payload.keys())}")
                                # Debug instance payloads for visibility
                                for instance_id in self.ace_instances:
                                    key = f"ace_instance_{instance_id}"
                                    if key in status_payload:
                                        logging.debug(f"ACE: payload[{key}]={status_payload.get(key)}")
                                self._process_rpc_status(status_payload)
                                logging.info("ACE: Refreshed via direct printer.objects.query")
                            else:
                                logging.debug(f"ACE: printer.objects.query unexpected result: {result}")
                        except Exception as e:
                            logging.debug(f"ACE: printer.objects.query callback error: {e}")

                    sent = direct_ws.send_method(
                        "printer.objects.query",
                        {"objects": objects},
                        _query_cb
                    )
                    if sent:
                        # Assume async callback will update; skip gcode
                        return True

                # Try to use cached subscription data before falling back to gcode
                if hasattr(self, "_printer") and hasattr(self._printer, "data"):
                    try:
                        pdata = self._printer.data or {}
                        payload = {}
                        ace_state = pdata.get("ace_state")
                        if isinstance(ace_state, dict):
                            payload["ace_state"] = ace_state
                        for instance_id in self.ace_instances:
                            key = f"ace_instance_{instance_id}"
                            if key in pdata:
                                payload[key] = pdata.get(key, {})
                            else:
                                # Backward compatibility with legacy object names
                                legacy_key = "ace" if instance_id == 0 else f"ace {instance_id}"
                                if legacy_key in pdata:
                                    payload[key] = pdata.get(legacy_key, {})
                        if payload:
                            self._process_rpc_status(payload)
                            logging.info("ACE: Used cached subscription data for refresh")
                            return True
                    except Exception as e:
                        logging.debug(f"ACE: Failed to use cached subscription data: {e}")
                return False

            ace_fields = [
                "slots",
                "status",
                "dryer",
                "dryer_status",
                "temp",
                "enable_rfid",
                "fan_speed",
                "feed_assist_count",
                "cont_assist_time",
            ]
            objects = {"ace_state": ["ace_instances", "current_index", "endless_spool_enabled", "endless_spool_match_mode", "ace_pro_enabled", "toolhead_sensor", "rdm_sensor"]}
            for instance_id in self.ace_instances:
                key = f"ace_instance_{instance_id}"
                objects[key] = ace_fields

            result = query_fn(objects)
            # Expect Moonraker-style {"status": {<object>: {...}}, "eventtime": ...}
            if isinstance(result, dict):
                payload = result.get("status") or result.get("result") or result
                if isinstance(payload, dict):
                    self._process_rpc_status(payload)
                    logging.info("ACE: Refreshed via JSON-RPC")
                    return True

            logging.debug(f"ACE: Unexpected JSON-RPC response: {result}")
        except Exception as e:
            logging.warning(f"ACE: JSON-RPC query failed, fallback to gcode: {e}")

        return False

    def _process_rpc_status(self, status_payload):
        """Process Moonraker objects.query payload to update UI without gcode chatter."""
        try:
            if not hasattr(self, "_ace_debug_logged"):
                self._ace_debug_logged = set()

            # Global/manager state
            ace_state = status_payload.get("ace_state", {})
            if isinstance(ace_state, dict) and "current_index" in ace_state:
                current_index = ace_state.get("current_index", -1)
                if current_index != self.current_loaded_slot:
                    old_slot = self.current_loaded_slot
                    self.current_loaded_slot = current_index
                    logging.info(f"ACE: (RPC) Updated current_loaded_slot: {old_slot} → {current_index}")
                    self.update_slot_loaded_states()

                # Endless spool toggle
                endless_enabled = ace_state.get("endless_spool_enabled")
                if isinstance(endless_enabled, bool) and hasattr(self, "endless_spool_switch"):
                    try:
                        self.endless_spool_switch.handler_block_by_func(self.on_endless_spool_toggled)
                        self.endless_spool_switch.set_active(endless_enabled)
                        self.endless_spool_switch.handler_unblock_by_func(self.on_endless_spool_toggled)
                        self.endless_spool_status.set_markup(
                            '<span foreground="green"><b>On</b></span>' if endless_enabled
                            else '<span foreground="gray">Off</span>'
                        )
                        self.endless_spool_enabled = endless_enabled
                        self._update_match_mode_sensitivity()
                    except Exception:
                        # Switch not connected yet; skip quietly
                        pass

                match_mode = ace_state.get("endless_spool_match_mode")
                if isinstance(match_mode, str):
                    self._set_match_mode_ui(match_mode)

                # ACE Pro enabled flag
                ace_pro_enabled = ace_state.get("ace_pro_enabled")
                if isinstance(ace_pro_enabled, bool):
                    if hasattr(self, "ace_pro_switch"):
                        try:
                            self.ace_pro_switch.handler_block_by_func(self.on_ace_pro_toggled)
                            self.ace_pro_switch.set_active(ace_pro_enabled)
                            self.ace_pro_switch.handler_unblock_by_func(self.on_ace_pro_toggled)
                            self.ace_pro_status.set_markup(
                                '<span foreground="green"><b>On</b></span>' if ace_pro_enabled
                                else '<span foreground="red">Off</span>'
                            )
                        except Exception:
                            pass
                    self.ace_pro_enabled = ace_pro_enabled
                    self._update_ace_pro_sensitivity(ace_pro_enabled)

                # Sensor states: True/False = filament present/absent; None = sensor not available
                toolhead_sensor = ace_state.get("toolhead_sensor")
                if toolhead_sensor is not None or "toolhead_sensor" in ace_state:
                    self.toolhead_sensor = toolhead_sensor

                rdm_sensor = ace_state.get("rdm_sensor")
                if rdm_sensor is not None or "rdm_sensor" in ace_state:
                    self.rdm_sensor = rdm_sensor

                logging.debug(
                    f"ACE: state update — ace_pro_enabled={self.ace_pro_enabled} "
                    f"toolhead_sensor={self.toolhead_sensor} rdm_sensor={self.rdm_sensor}"
                )

            # Per-instance slots
            for instance_id in self.ace_instances:
                key = f"ace_instance_{instance_id}"
                instance_status = status_payload.get(key, {})
                if not isinstance(instance_status, dict):
                    logging.debug(f"ACE: No status dict for {key} in payload keys={list(status_payload.keys())}")
                    continue

                raw_slots = instance_status.get("slots")
                slots_list = None
                if isinstance(raw_slots, list):
                    slots_list = raw_slots
                elif isinstance(raw_slots, dict):
                    slots_list = list(raw_slots.values())

                if isinstance(slots_list, list):
                    if slots_list and len(slots_list) > 0 and not isinstance(slots_list[0], dict):
                        logging.debug(f"ACE: Unexpected slot element type for {key}: {type(slots_list[0])}")
                    self.update_slots_from_data(instance_id, slots_list)
                else:
                    # Partial update (e.g., temp-only); skip without logging spam
                    continue

                # Dryer status handling for RPC path
                if self.current_view == "dryer" and hasattr(self, "_dryer_controls"):
                    controls = getattr(self, "_dryer_controls", {}).get(instance_id)
                    if controls:
                        dryer_data = instance_status.get("dryer_status") or instance_status.get("dryer") or {}
                        target_temp = dryer_data.get("target_temp", 0)
                        dryer_state = dryer_data.get("status", "stop")
                        remain_time = dryer_data.get("remain_time", 0)
                        current_temp = instance_status.get("temp", 0)

                        if dryer_state == "drying":
                            hours = remain_time // 3600
                            minutes = (remain_time % 3600) // 60
                            time_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
                            controls['status_label'].set_markup(f'<span foreground="green"><b>DRYING ({time_str})</b></span>')
                        elif dryer_state == "stop":
                            controls['status_label'].set_markup('<span foreground="gray"><b>STOPPED</b></span>')
                        else:
                            controls['status_label'].set_markup(f'<span foreground="red"><b>{dryer_state.upper()}</b></span>')

                        controls['target_label'].set_text(f"Target: {target_temp}°C")
                        controls['current_label'].set_text(f"Current: {current_temp}°C")
        except Exception as e:
            logging.error(f"ACE: Error processing JSON-RPC status payload: {e}", exc_info=True)

    def load_inventory_from_saved_variables(self):
        pass

    def add_custom_css(self):
        """Add custom CSS for ACE panel elements"""
        css = b"""
        .ace_color_preview {
            border: 2px solid #ffffff;
            border-radius: 5px;
            min-width: 30px;
            min-height: 30px;
        }
        .ace_color_preview_large {
            border: 3px solid #ffffff;
            border-radius: 8px;
            min-width: 50px;
            min-height: 50px;
        }
        .ace_instance_indicator {
            background: linear-gradient(to bottom, #2a2a2a 0%, #1a1a1a 100%);
            border: 2px solid #4a9eff;
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 16px;
            font-weight: bold;
            color: #4a9eff;
        }
        .ace_numpad_button {
            border: 1px solid #888;
            border-radius: 6px;
            background: #1f1f1f;
            color: #f5f5f5;
            padding: 2px 2px;
        }
        .ace_numpad_button:active {
            background: #2a2a2a;
        }
        .ace_tool_selector_button {
            padding: 10px 14px;
            font-size: 16px;
            font-weight: 700;
            min-width: 200px;
            min-height: 48px;
        }
        """

        style_provider = Gtk.CssProvider()
        style_provider.load_from_data(css)

        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            style_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def get_current_loaded_slot(self):
        return getattr(self, 'current_loaded_slot', -1)

    def initialize_loaded_slot(self):
        """Initialize the loaded slot from Klipper"""
        self.current_loaded_slot = self.get_current_loaded_slot()
        logging.info(f"ACE: Initialized loaded slot: {self.current_loaded_slot}")
        self.update_slot_loaded_states()

    def create_main_screen(self):
        """Create the main ACE panel screen layout"""
        self.current_view = "main"
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main_box.set_margin_left(10)
        main_box.set_margin_right(10)
        main_box.set_margin_top(10)
        main_box.set_margin_bottom(10)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        self.status_label = Gtk.Label(label="ACE System: Ready")
        self.status_label.get_style_context().add_class("temperature_entry")
        self.status_label.set_halign(Gtk.Align.START)
        top_row.pack_start(self.status_label, False, False, 0)

        # Connection state indicator — centered, updated by poll timer
        self.conn_status_label = Gtk.Label()
        self.conn_status_label.get_style_context().add_class("description")
        self.conn_status_label.set_halign(Gtk.Align.CENTER)
        self.conn_status_label.set_hexpand(True)
        self.conn_status_label.set_markup('<span foreground="gray">Checking...</span>')
        top_row.pack_start(self.conn_status_label, True, True, 0)

        # Utilities entry point (compact)
        spool_btn = Gtk.Button(label="Utilities")
        spool_btn.set_size_request(150, 38)
        spool_btn.set_relief(Gtk.ReliefStyle.NORMAL)
        spool_btn.connect("clicked", lambda w: self.show_spool_panel(w, reset_selection=True))
        top_row.pack_end(spool_btn, False, False, 0)
        self.utilities_btn = spool_btn

        main_box.pack_start(top_row, False, False, 0)

        # Endless Spool row with three controls: ACE Pro | Endless Spool | Match Mode
        endless_spool_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        endless_spool_row.set_margin_top(5)

        # Far left: ACE Pro Enable/Disable
        ace_pro_control = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        ace_pro_label = Gtk.Label(label="ACE Pro:")
        ace_pro_label.get_style_context().add_class("description")
        ace_pro_label.set_halign(Gtk.Align.START)
        ace_pro_control.pack_start(ace_pro_label, False, False, 0)

        self.ace_pro_switch = Gtk.Switch()
        self.ace_pro_switch.set_active(self.ace_pro_enabled)
        self.ace_pro_switch.connect("notify::active", self.on_ace_pro_toggled)
        ace_pro_control.pack_start(self.ace_pro_switch, False, False, 0)

        self.ace_pro_status = Gtk.Label()
        self.ace_pro_status.get_style_context().add_class("description")
        if self.ace_pro_enabled:
            self.ace_pro_status.set_markup('<span foreground="green"><b>On</b></span>')
        else:
            self.ace_pro_status.set_markup('<span foreground="red">Off</span>')
        ace_pro_control.pack_start(self.ace_pro_status, False, False, 0)

        endless_spool_row.pack_start(ace_pro_control, True, True, 0)

        # Middle: Endless Spool Enable/Disable
        endless_spool_control = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        endless_spool_label = Gtk.Label(label="Endless Spool:")
        endless_spool_label.get_style_context().add_class("description")
        endless_spool_label.set_halign(Gtk.Align.START)
        endless_spool_control.pack_start(endless_spool_label, False, False, 0)

        # Toggle switch - will be updated after querying Klipper
        self.endless_spool_switch = Gtk.Switch()
        self.endless_spool_switch.set_active(self.endless_spool_enabled)
        self.endless_spool_switch.connect("notify::active", self.on_endless_spool_toggled)
        endless_spool_control.pack_start(self.endless_spool_switch, False, False, 0)

        # Status indicator
        self.endless_spool_status = Gtk.Label(
            label="Off" if not self.endless_spool_enabled else "On"
        )
        self.endless_spool_status.get_style_context().add_class("description")
        if self.endless_spool_enabled:
            self.endless_spool_status.set_markup('<span foreground="green"><b>On</b></span>')
        else:
            self.endless_spool_status.set_markup('<span foreground="gray">Off</span>')
        endless_spool_control.pack_start(self.endless_spool_status, False, False, 0)

        endless_spool_row.pack_start(endless_spool_control, True, True, 0)
        self.endless_spool_control = endless_spool_control

        # Right side: Match Mode selector
        match_mode_control = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        match_mode_label = Gtk.Label()
        match_mode_label.set_markup('<span foreground="white">Match Mode:</span>')
        match_mode_label.get_style_context().add_class("description")
        match_mode_label.set_halign(Gtk.Align.START)
        match_mode_control.pack_start(match_mode_label, False, False, 0)

        # Match mode selector (Exact / Material Only / Next Ready)
        self.match_mode_button = Gtk.MenuButton()
        self.match_mode_button.set_relief(Gtk.ReliefStyle.NORMAL)
        self.match_mode_button.get_style_context().add_class("color3")
        self.match_mode_button.set_size_request(170, 36)
        self._build_match_mode_popover()
        match_mode_control.pack_start(self.match_mode_button, False, False, 0)

        # Mode indicator label
        # Show active mode directly on the button; no extra label to avoid duplication
        self.match_mode_status = None
        self._set_match_mode_ui("exact", update_widget=True)
        self._update_match_mode_sensitivity()

        endless_spool_row.pack_start(match_mode_control, True, True, 0)
        self.match_mode_control = match_mode_control

        main_box.pack_start(endless_spool_row, False, False, 0)

        self.content_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main_box.pack_start(self.content_area, True, True, 0)

        bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        bottom_box.set_homogeneous(True)

        # Use theme icon name (refresh); file extension is resolved by the icon loader
        refresh_btn = self._gtk.Button("refresh", "Refresh All", "color3")
        refresh_btn.set_size_request(-1, 50)
        refresh_btn.connect("clicked", self.refresh_status)
        bottom_box.pack_start(refresh_btn, True, True, 0)
        self.refresh_btn = refresh_btn

        if len(self.ace_instances) > 1:
            # Use shuffle icon to indicate cycling through instances
            total = len(self.ace_instances)
            label = f"ACE {self.current_instance} ({self.current_instance + 1} of {total})"
            cycle_btn = self._gtk.Button("shuffle", label, "color3")
            cycle_btn.set_size_request(-1, 50)
            cycle_btn.connect("clicked", self.cycle_instance)
            self.instance_toggle_btn = cycle_btn
            bottom_box.pack_start(cycle_btn, True, True, 0)

        self.dryer_btn = self._gtk.Button("heat-up", "Dryer Control", "color2")
        self.dryer_btn.set_size_request(-1, 50)
        self.dryer_btn.connect("clicked", self.show_dryer_panel)
        
        bottom_box.pack_start(self.dryer_btn, True, True, 0)

        main_box.pack_start(bottom_box, False, False, 0)

        self.content.add(main_box)
        self.display_current_instance()
        self.content.show_all()
        self._update_ace_pro_sensitivity(self.ace_pro_enabled)

    def _update_ace_pro_sensitivity(self, enabled):
        """Gray out / restore all controls except the ACE Pro switch itself."""
        if not enabled and getattr(self, "current_view", "main") != "main":
            self.return_to_main_screen()

        for attr in ("endless_spool_control", "match_mode_control",
                     "content_area", "dryer_btn", "utilities_btn",
                     "refresh_btn", "instance_toggle_btn"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.set_sensitive(bool(enabled))

    def _build_match_mode_popover(self):
        """Create popover for match mode selection (touch-friendly)."""
        # Destroy previous popover to avoid stale widgets
        if getattr(self, 'match_mode_popover', None):
            try:
                self.match_mode_popover.destroy()
            except Exception:
                pass

        popover = Gtk.Popover.new(self.match_mode_button)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.set_modal(True)  # Keep it open during touch

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_border_width(6)

        def add_row(mode_id, label_text):
            btn = Gtk.Button.new_with_label(label_text)
            btn.set_halign(Gtk.Align.FILL)
            btn.set_hexpand(True)
            btn.connect("clicked", lambda _btn: self.on_match_mode_selected(mode_id))
            vbox.pack_start(btn, True, True, 0)

        add_row("exact", "Exact (Material + Color)")
        add_row("material", "Material Only")
        add_row("next", "Next Ready Spool")

        popover.add(vbox)
        popover.show_all()
        popover.popdown()  # Ensure it starts closed

        self.match_mode_popover = popover
        self.match_mode_button.set_popover(popover)

    def _set_match_mode_ui(self, mode, update_widget=True):
        """Set match mode UI (button label + status) in one place."""
        valid_modes = {"exact", "material", "next"}
        if mode not in valid_modes:
            mode = "exact"

        color_map = {"exact": "blue", "material": "orange", "next": "green"}
        label_map = {"exact": "Exact", "material": "Material", "next": "Next Ready"}

        if update_widget and hasattr(self, 'match_mode_button'):
            self.match_mode_button.set_label(label_map.get(mode, "Exact"))
            self.match_mode_button.set_size_request(170, 36)
            # Only rebuild popover if it doesn't exist yet
            if not hasattr(self, 'match_mode_popover') or self.match_mode_popover is None:
                self._build_match_mode_popover()

        # No separate status label; button already shows current mode

    def _update_match_mode_sensitivity(self):
        """Enable/disable match mode controls when endless spool is off."""
        if hasattr(self, "match_mode_button") and self.match_mode_button:
            self.match_mode_button.set_sensitive(bool(self.endless_spool_enabled))
            if not self.endless_spool_enabled and getattr(self, "match_mode_popover", None):
                try:
                    self.match_mode_popover.popdown()
                except Exception:
                    pass

    def on_match_mode_selected(self, mode):
        """Handle Match Mode selection change from popover."""
        if hasattr(self, 'match_mode_popover'):
            self.match_mode_popover.popdown()

        # Update button label immediately so UI reflects the active mode
        self._set_match_mode_ui(mode, update_widget=True)

        if mode == "exact":
            self._send_gcode("ACE_SET_ENDLESS_SPOOL_MODE MODE=exact")
            self._screen.show_popup_message("Match Mode: Exact (Material + Color)", 1)
            logging.info("ACE: Match mode set to EXACT (material + color)")
        elif mode == "material":
            self._send_gcode("ACE_SET_ENDLESS_SPOOL_MODE MODE=material")
            self._screen.show_popup_message("Match Mode: Material Only", 1)
            logging.info("ACE: Match mode set to MATERIAL (ignore color)")
        else:
            self._send_gcode("ACE_SET_ENDLESS_SPOOL_MODE MODE=next")
            self._screen.show_popup_message("Match Mode: Next Ready Spool", 1)
            logging.info("ACE: Match mode set to NEXT READY (ignore material/color)")

    def on_ace_pro_toggled(self, switch, gparam):
        """Handle ACE Pro enable/disable toggle."""
        self.ace_pro_enabled = switch.get_active()
        if self.ace_pro_enabled:
            self.ace_pro_status.set_markup('<span foreground="green"><b>On</b></span>')
            self._send_gcode("SET_PIN PIN=ACE_Pro VALUE=1")
            self._screen.show_popup_message("ACE Pro Enabled", 1)
            logging.info("ACE: ACE Pro enabled via KlipperScreen")
        else:
            self.ace_pro_status.set_markup('<span foreground="red">Off</span>')
            self._send_gcode("SET_PIN PIN=ACE_Pro VALUE=0")
            self._screen.show_popup_message("ACE Pro Disabled", 1)
            logging.info("ACE: ACE Pro disabled via KlipperScreen")
        self._update_ace_pro_sensitivity(self.ace_pro_enabled)

    def on_endless_spool_toggled(self, switch, gparam):
        """Handle Endless Spool toggle"""
        self.endless_spool_enabled = switch.get_active()

        # Update status label
        if self.endless_spool_enabled:
            self.endless_spool_status.set_markup('<span foreground="green"><b>On</b></span>')
            # Send gcode to enable endless spool for all instances
            for instance_id in self.ace_instances:
                instance_param = f" INSTANCE={instance_id}" if instance_id > 0 else ""
                self._send_gcode(f"ACE_ENABLE_ENDLESS_SPOOL{instance_param}")
            self._screen.show_popup_message("Endless Spool Enabled", 1)
            logging.info("ACE: Endless Spool enabled for all instances")
        else:
            self.endless_spool_status.set_markup('<span foreground="gray">Off</span>')
            # Send gcode to disable endless spool for all instances
            for instance_id in self.ace_instances:
                instance_param = f" INSTANCE={instance_id}" if instance_id > 0 else ""
                self._send_gcode(f"ACE_DISABLE_ENDLESS_SPOOL{instance_param}")
            self._screen.show_popup_message("Endless Spool Disabled", 1)
            logging.info("ACE: Endless Spool disabled for all instances")

        self._update_match_mode_sensitivity()

    def cycle_instance(self, widget):
        """Cycle to next ACE instance in order"""
        if len(self.ace_instances) > 1:
            if hasattr(self, 'current_config_instance'):
                self.current_config_instance = None
            if hasattr(self, 'current_config_slot'):
                self.current_config_slot = None

            current_idx = self.ace_instances.index(self.current_instance)
            next_idx = (current_idx + 1) % len(self.ace_instances)
            self.current_instance = self.ace_instances[next_idx]

            # Update button label
            if hasattr(self, 'instance_toggle_btn'):
                total = len(self.ace_instances)
                label = f"ACE {self.current_instance} ({self.current_instance + 1} of {total})"
                self.instance_toggle_btn.set_label(label)

            self.display_current_instance()

    def prev_instance(self, widget):
        if len(self.ace_instances) > 1:
            if hasattr(self, 'current_config_instance'):
                self.current_config_instance = None
            if hasattr(self, 'current_config_slot'):
                self.current_config_slot = None

            current_idx = self.ace_instances.index(self.current_instance)
            prev_idx = (current_idx - 1) % len(self.ace_instances)
            self.current_instance = self.ace_instances[prev_idx]
            self.display_current_instance()

    def next_instance(self, widget):
        if len(self.ace_instances) > 1:
            if hasattr(self, 'current_config_instance'):
                self.current_config_instance = None
            if hasattr(self, 'current_config_slot'):
                self.current_config_slot = None

            current_idx = self.ace_instances.index(self.current_instance)
            next_idx = (current_idx + 1) % len(self.ace_instances)
            self.current_instance = self.ace_instances[next_idx]
            self.display_current_instance()

    def display_current_instance(self):
        for child in self.content_area.get_children():
            self.content_area.remove(child)

        if hasattr(self, 'instance_label'):
            self.instance_label.set_text(f"ACE {self.current_instance}")

        instance = self.instance_data[self.current_instance]
        instance['slot_buttons'].clear()
        instance['slot_labels'].clear()
        instance['slot_color_boxes'].clear()
        instance['slot_tool_labels'].clear()

        instance_ui = self._create_instance_ui(self.current_instance)
        self.content_area.pack_start(instance_ui, True, True, 0)
        self.content_area.show_all()
        self.update_slot_loaded_states()

    def _create_instance_ui(self, instance_id):
        instance_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)

        slots_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        slots_box.set_homogeneous(True)

        instance = self.instance_data[instance_id]
        instance['slot_buttons'] = []
        instance['slot_labels'] = []
        instance['slot_color_boxes'] = []
        instance['slot_tool_labels'] = []
        instance['slot_gear_buttons'] = []

        for local_slot in range(4):
            global_tool = instance['tool_offset'] + local_slot
            slot_btn = self._create_slot_button(instance_id, local_slot, global_tool)
            slots_box.pack_start(slot_btn, True, True, 0)

        instance_box.pack_start(slots_box, False, False, 0)

        return instance_box

    def _create_slot_button(self, instance_id, local_slot, global_tool):
        slot_btn = Gtk.EventBox()
        slot_btn.get_style_context().add_class("ace_slot_button")

        slot_data = self.instance_data[instance_id]['inventory'][local_slot]

        if slot_data['status'] == 'empty':
            slot_btn.get_style_context().add_class("ace_slot_empty")

        slot_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        slot_box.set_margin_left(5)
        slot_box.set_margin_right(5)
        slot_box.set_margin_top(5)
        slot_box.set_margin_bottom(5)

        # Header row: tool label + compact gear button for config
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        tool_label = Gtk.Label(label=self._format_tool_label(global_tool, slot_data))
        tool_label.get_style_context().add_class("description")
        tool_label.set_halign(Gtk.Align.START)
        header_row.pack_start(tool_label, True, True, 0)

        # Use bundled filament icon to indicate load/unload action
        gear_btn = self._gtk.Button("filament", None, "color2")
        gear_btn.set_size_request(32, 32)
        gear_btn.set_halign(Gtk.Align.END)
        # Gear now triggers load/unload (previous slot tap behavior)
        gear_btn.connect("clicked", self.on_slot_gear_clicked, instance_id, local_slot)
        header_row.pack_start(gear_btn, False, False, 0)

        slot_box.pack_start(header_row, False, False, 0)

        # Color indicator (use gray for empty slots to keep alignment)
        color_box = Gtk.EventBox()
        color_box.set_size_request(60, 40)
        color_box.get_style_context().add_class("ace_color_preview")
        display_color = slot_data['color'] if slot_data['status'] == 'ready' else [0, 0, 0]
        self.set_slot_color(color_box, display_color)
        slot_box.pack_start(color_box, False, False, 0)

        # Material/Temp label - READ FROM INVENTORY!
        slot_label = Gtk.Label()
        slot_label.set_line_wrap(True)
        slot_label.set_justify(Gtk.Justification.CENTER)

        # Set initial text from inventory
        if slot_data['status'] == 'ready' and slot_data['material']:
            slot_label.set_text(f"{slot_data['material']}\n{slot_data['temp']}°C")
        else:
            # Keep two lines so rows stay aligned with loaded slots
            slot_label.set_text("Empty\n ")

        slot_box.pack_start(slot_label, False, False, 0)

        slot_btn.add(slot_box)
        # Slot tap now opens config (previous gear behavior)
        slot_btn.connect(
            "button-press-event",
            lambda _w, _e, iid=instance_id, lslot=local_slot: self._open_slot_settings(iid, lslot),
        )

        # Store references
        self.instance_data[instance_id]['slot_buttons'].append(slot_btn)
        self.instance_data[instance_id]['slot_labels'].append(slot_label)
        self.instance_data[instance_id]['slot_color_boxes'].append(color_box)
        self.instance_data[instance_id]['slot_tool_labels'].append(tool_label)
        if 'slot_gear_buttons' not in self.instance_data[instance_id]:
            self.instance_data[instance_id]['slot_gear_buttons'] = []
        self.instance_data[instance_id]['slot_gear_buttons'].append(gear_btn)

        # Dim empty slots so they appear less prominent and disable load/unload on empties
        is_ready = slot_data['status'] == 'ready' and slot_data['material']
        self._set_slot_visual_state(instance_id, local_slot, is_ready)

        return slot_btn

    def set_slot_color(self, color_box, rgb_color):
        """Set the color of a slot's color indicator"""
        r, g, b = rgb_color
        color = Gdk.RGBA(r/255.0, g/255.0, b/255.0, 1.0)
        color_box.override_background_color(Gtk.StateFlags.NORMAL, color)

    def _format_tool_label(self, global_tool, slot_data):
        """Return tool label with RFID marker when applicable."""
        suffix = " (RFID)" if slot_data.get('rfid') else ""
        return f"T{global_tool}{suffix}"

    def _set_slot_visual_state(self, instance_id, local_slot, is_ready):
        """Dim empty slots to make them less prominent."""
        try:
            opacity = 1.0 if is_ready else 0.45
            labels = self.instance_data[instance_id].get('slot_labels', [])
            colors = self.instance_data[instance_id].get('slot_color_boxes', [])
            tool_labels = self.instance_data[instance_id].get('slot_tool_labels', [])
            gear_buttons = self.instance_data[instance_id].get('slot_gear_buttons', [])

            if local_slot < len(labels) and labels[local_slot]:
                labels[local_slot].set_opacity(opacity)

            if local_slot < len(colors) and colors[local_slot]:
                colors[local_slot].set_opacity(opacity)

            if local_slot < len(tool_labels) and tool_labels[local_slot]:
                tool_labels[local_slot].set_opacity(opacity)

            if local_slot < len(gear_buttons) and gear_buttons[local_slot]:
                gear_buttons[local_slot].set_opacity(opacity)
                gear_buttons[local_slot].set_sensitive(is_ready)
        except Exception:
            logging.debug("ACE: Failed to set slot visual state", exc_info=True)

    def update_slot_loaded_states(self):
        """Update all slot loaded states based on ace_current_index"""
        current_loaded = self.get_current_loaded_slot()
        logging.info(f"ACE: Updating loaded states, current: {current_loaded}")

        for instance_id in self.ace_instances:
            instance = self.instance_data[instance_id]

            # Skip if UI hasn't been created yet
            if not instance['slot_buttons']:
                logging.debug(f"ACE: Skipping instance {instance_id} - UI not created yet")
                continue

            for local_slot in range(4):
                global_tool = instance['tool_offset'] + local_slot
                slot_btn = instance['slot_buttons'][local_slot]

                # Remove all state classes
                slot_btn.get_style_context().remove_class("ace_slot_loaded")
                slot_btn.get_style_context().remove_class("ace_slot_empty")

                # Add appropriate class
                if current_loaded == global_tool:
                    slot_btn.get_style_context().add_class("ace_slot_loaded")
                    logging.info(f"ACE: Marked T{global_tool} as LOADED")
                else:
                    slot_data = instance['inventory'][local_slot]
                    if slot_data['status'] == 'empty':
                        slot_btn.get_style_context().add_class("ace_slot_empty")

        # Update status label (if it exists)
        if hasattr(self, 'status_label'):
            if current_loaded != -1:
                self.status_label.set_text(f"ACE: Tool T{current_loaded} Loaded")
            else:
                self.status_label.set_text("ACE: No Tool Loaded")

    def on_slot_clicked(self, widget, event, instance_id, local_slot):
        """Handle slot button clicks (legacy load/unload behavior)."""
        if not self._require_ready_slot(instance_id, local_slot):
            return
        global_tool = self.instance_data[instance_id]['tool_offset'] + local_slot
        current_loaded = self.get_current_loaded_slot()

        if current_loaded == global_tool:
            self.show_unload_confirmation(instance_id, global_tool)
        else:
            self.show_load_confirmation(instance_id, global_tool)

    def on_slot_gear_clicked(self, widget, instance_id, local_slot):
        """Gear click should perform load/unload (was slot tap)."""
        if not self._require_ready_slot(instance_id, local_slot):
            return
        self.on_slot_clicked(widget, None, instance_id, local_slot)

    def _open_slot_settings(self, instance_id, local_slot):
        """Slot tap should open config (was gear)."""
        if not self._require_ready_slot(instance_id, local_slot):
            return
        self.show_slot_settings(None, instance_id, local_slot)

    def _slot_is_ready(self, instance_id, local_slot):
        """Return True if slot has material and is marked ready."""
        try:
            inv = self.instance_data[instance_id]['inventory'][local_slot]
            return (
                inv.get('status') == 'ready'
                and bool(inv.get('material', '').strip())
            )
        except Exception:
            return False

    def _require_ready_slot(self, instance_id, local_slot):
        """Guard actions that need a populated slot; show a friendly hint if empty."""
        if not self._slot_is_ready(instance_id, local_slot):
            self._screen.show_popup_message("Load filament into this slot first.", 1)
            return False
        return True

    def show_load_confirmation(self, instance_id, global_tool):
        """Show confirmation dialog to load a tool"""
        local_slot = global_tool - self.instance_data[instance_id]['tool_offset']

        # Validate UI is initialized before accessing
        instance = self.instance_data[instance_id]
        if (not instance['slot_labels'] or
                local_slot >= len(instance['slot_labels'])):
            self._screen.show_popup_message(
                "Slot data not ready. Please wait and try again."
            )
            logging.warning(
                f"ACE: UI not ready for slot {local_slot} on instance "
                f"{instance_id}"
            )
            return

        slot_info = instance['slot_labels'][local_slot].get_text()

        if slot_info == "Empty":
            self._screen.show_popup_message(
                "Slot is empty. Configure it first."
            )
            return

        current_loaded = self.get_current_loaded_slot()
        message = f"Load Tool T{global_tool}?\n\n{slot_info}"
        if current_loaded != -1:
            message += f"\n\nCurrent: T{current_loaded}"

        label = Gtk.Label(label=message)
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)

        buttons = [
            {"name": "Cancel", "response": Gtk.ResponseType.CANCEL},
            {"name": "Load", "response": Gtk.ResponseType.OK}
        ]

        def load_response(dialog, response_id):
            self._gtk.remove_dialog(dialog)
            if response_id == Gtk.ResponseType.OK:
                self._send_gcode(f"T{global_tool}")
                self._screen.show_popup_message(
                    f"Loading T{global_tool}...", 1
                )

        self._gtk.Dialog(f"Load Tool T{global_tool}", buttons, label, load_response)

    def _sensors_indicate_no_filament(self):
        """Return True if any configured sensor reports filament absent.

        Checks toolhead_sensor and rdm_sensor (both updated from Klipper via RPC).
        A value of None means the sensor is not configured — it is ignored.
        Returns False when no sensors are configured (can't determine anything).
        """
        any_configured = False
        for val in (self.toolhead_sensor, self.rdm_sensor):
            if val is None:
                continue          # sensor not fitted — skip
            any_configured = True
            if val is False:      # sensor present but reads clear
                return True
        return False              # all configured sensors say filament present (or none fitted)

    def show_unload_confirmation(self, instance_id, global_tool):
        """Show confirmation dialog to unload a tool.

        If the sensor state contradicts the loaded-tool state (i.e. the system
        thinks T{global_tool} is loaded but sensors report no filament) an
        inconsistency warning is shown instead, giving the user three options:
          • Cancel — do nothing
          • Load   — treat ACE state as stale and load the tool fresh
          • Unload — force a smart-unload to clean up ACE state
        """
        local_slot = global_tool - self.instance_data[instance_id]['tool_offset']

        # Validate UI is initialized before accessing
        instance = self.instance_data[instance_id]
        if (not instance['slot_labels'] or
                local_slot >= len(instance['slot_labels'])):
            self._screen.show_popup_message(
                "Slot data not ready. Please wait and try again."
            )
            logging.warning(
                f"ACE: UI not ready for slot {local_slot} on instance "
                f"{instance_id}"
            )
            return

        slot_info = instance['slot_labels'][local_slot].get_text()

        # Build a human-readable summary of the current sensor readings.
        def _sensor_summary():
            parts = []
            if self.toolhead_sensor is not None:
                parts.append(f"Toolhead: {'present' if self.toolhead_sensor else 'clear'}")
            if self.rdm_sensor is not None:
                parts.append(f"RDM: {'present' if self.rdm_sensor else 'clear'}")
            return ", ".join(parts) if parts else "no sensors"

        if self._sensors_indicate_no_filament():
            # Inconsistency: ACE thinks tool is loaded but sensors say no filament
            logging.warning(
                f"ACE: Inconsistency detected — T{global_tool} marked loaded "
                f"but sensors report clear ({_sensor_summary()})"
            )
            message = (
                f"⚠ Inconsistent state for T{global_tool}\n\n"
                f"{slot_info}\n\n"
                f"ACE tool state is indicatingloaded, but the filament sensors "
                f"report no filament ({_sensor_summary()}).\n\n"
                f"Filament may have been removed manually.\n\n"
                f"What would you like to do?"
            )
            label = Gtk.Label(label=message)
            label.set_line_wrap(True)
            label.set_justify(Gtk.Justification.CENTER)

            # Three-way choice via distinct response codes
            _RESP_LOAD   = Gtk.ResponseType.YES
            _RESP_UNLOAD = Gtk.ResponseType.OK

            buttons = [
                {"name": "Cancel", "response": Gtk.ResponseType.CANCEL},
                {"name": "Load",   "response": _RESP_LOAD},
                {"name": "Unload", "response": _RESP_UNLOAD},
            ]

            def inconsistency_response(dialog, response_id):
                self._gtk.remove_dialog(dialog)
                if response_id == _RESP_LOAD:
                    self._send_gcode(f"T{global_tool}")
                    self._screen.show_popup_message(f"Loading T{global_tool}...", 1)
                elif response_id == _RESP_UNLOAD:
                    self._send_gcode("TR")
                    self._screen.show_popup_message("Unloading...", 1)

            self._gtk.Dialog(
                f"Inconsistent State — T{global_tool}",
                buttons, label, inconsistency_response
            )
            return

        # Normal case: sensors confirm filament is present — straightforward unload
        message = f"Unload Tool T{global_tool}?\n\n{slot_info}"

        label = Gtk.Label(label=message)
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)

        buttons = [
            {"name": "Cancel", "response": Gtk.ResponseType.CANCEL},
            {"name": "Unload", "response": Gtk.ResponseType.OK}
        ]

        def unload_response(dialog, response_id):
            self._gtk.remove_dialog(dialog)
            if response_id == Gtk.ResponseType.OK:
                self._send_gcode("TR")
                self._screen.show_popup_message("Unloading...", 1)

        self._gtk.Dialog(f"Unload Tool T{global_tool}", buttons, label, unload_response)

    def show_slot_settings(self, widget, instance_id, local_slot):
        """Show slot configuration screen"""
        self.current_config_instance = instance_id
        self.current_config_slot = local_slot
        self.show_slot_config_screen(instance_id, local_slot)

    def refresh_all_instances(self):
        """Query slot data from all ACE instances"""
        logging.info("ACE: Refreshing all instances...")
        self._try_refresh_via_rpc()
        # No gcode fallback; RPC/subscription drives updates

    def refresh_status(self, widget):
        """Manual refresh button - query all instances"""
        self.refresh_all_instances()
        self._screen.show_popup_message("Refreshing ACE data...", 1)

    def process_update(self, action, data):
        """Process updates from Klipper"""
        try:
            if action == "notify_status_update":
                if isinstance(data, dict):
                    payload = data.get("status") or data
                    if isinstance(payload, dict):
                        # output_pin ACE_Pro is in the global KlipperScreen
                        # subscription (all output_pins are subscribed by
                        # screen.py). ace_state is a custom object that is
                        # NOT in the global subscription, so we catch the pin
                        # change here directly and sync the switch immediately.
                        pin_data = payload.get("output_pin ace_pro") or payload.get("output_pin ACE_Pro")
                        if isinstance(pin_data, dict) and "value" in pin_data:
                            enabled = bool(pin_data["value"])
                            if enabled != self.ace_pro_enabled:
                                self.ace_pro_enabled = enabled
                                if hasattr(self, "ace_pro_switch"):
                                    try:
                                        self.ace_pro_switch.handler_block_by_func(self.on_ace_pro_toggled)
                                        self.ace_pro_switch.set_active(enabled)
                                        self.ace_pro_switch.handler_unblock_by_func(self.on_ace_pro_toggled)
                                        self.ace_pro_status.set_markup(
                                            '<span foreground="green"><b>On</b></span>' if enabled
                                            else '<span foreground="red">Off</span>'
                                        )
                                    except Exception:
                                        pass
                                self._update_ace_pro_sensitivity(enabled)
                                logging.info(f"ACE: ace_pro_enabled updated from pin subscription: {enabled}")

                        self._process_rpc_status(payload)
                        return

            # Legacy gcode response parsing removed - all data now comes via RPC subscriptions
            # (status, slots, current_index, endless_spool state, match_mode)

        except Exception as e:
            logging.error(f"ACE: Error in process_update: {e}", exc_info=True)

    def update_slots_from_data(self, instance_id, slot_data):
        """Update slot display from ACE_QUERY_SLOTS data"""
        logging.info(f"ACE: Updating slots for instance {instance_id}")
        logging.info(f"ACE: Raw slot_data: {slot_data}")

        instance = self.instance_data[instance_id]

        for i, slot in enumerate(slot_data):
            # Validate slot index is within expected range
            if i >= 4:
                logging.warning(f"ACE: Skipping slot {i} - out of range")
                continue

            # Ensure slot dict has RFID flag for UI formatting
            if 'rfid' not in slot:
                slot['rfid'] = False

            material = slot.get('material', '')
            temp = slot.get('temp', 0)
            color = slot.get('color', [0, 0, 0])
            status = slot.get('status', 'empty')

            logging.info(
                f"ACE: Slot {i} raw data - status='{status}', "
                f"material='{material}', temp={temp}, color={color}"
            )

            # Update internal inventory FIRST
            instance['inventory'][i] = slot

            # Update UI ONLY if this instance is displayed AND UI exists
            if (instance_id == self.current_instance and
                    instance['slot_labels'] and
                    i < len(instance['slot_labels']) and
                    i < len(instance['slot_color_boxes'])):

                # Check explicitly
                has_valid_data = status not in ("empty", "")
                logging.info(
                    f"ACE: Slot {i} has_valid_data={has_valid_data} "
                    f"(status={status}, material='{material}', temp={temp})"
                )

                # Update tool label to show RFID marker when applicable
                if (instance['slot_tool_labels'] and
                        i < len(instance['slot_tool_labels'])):
                    instance['slot_tool_labels'][i].set_text(
                        self._format_tool_label(
                            instance['tool_offset'] + i,
                            slot
                        )
                    )

                if has_valid_data:
                    material_stripped = material.strip()
                    display_material = material_stripped or status.replace("_", " ").title()
                    temp_text = f"{temp}°C" if temp else ""
                    status_suffix = ""
                    # Only show status suffix if we're showing actual material (not status fallback)
                    if status not in ("ready", "", "empty") and material_stripped:
                        status_suffix = f" ({status.replace('_', ' ').title()})"
                    second_line = f"{temp_text}{status_suffix}".strip()
                    if not second_line:
                        second_line = " "
                    label_text = f"{display_material}\n{second_line}"
                    instance['slot_labels'][i].set_text(label_text)
                    self.set_slot_color(
                        instance['slot_color_boxes'][i], color
                    )
                    self._set_slot_visual_state(instance_id, i, True)
                    logging.info(
                        f"ACE: ✓ Updated UI slot {i}: "
                        f"{material} {temp}°C"
                    )
                else:
                    # Keep two-line text and gray color for empty slots
                    instance['slot_labels'][i].set_text("Empty\n ")
                    self.set_slot_color(
                        instance['slot_color_boxes'][i], [0, 0, 0]
                    )
                    self._set_slot_visual_state(instance_id, i, False)
                    logging.info(f"ACE: ✗ Slot {i} marked Empty in UI")

        # Update loaded states after data update
        self.update_slot_loaded_states()

    def show_dryer_panel(self, widget):
        """Show dryer control panel for all ACE instances"""
        self.current_view = "dryer"
        
        logging.info(f"ACE: Opening dryer panel. Instances detected: {self.ace_instances}")
        
        # Store references to dryer controls for updates
        self._dryer_controls = {}
        
        # Clear content
        for child in self.content.get_children():
            self.content.remove(child)

        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main_box.set_margin_left(10)
        main_box.set_margin_right(10)
        main_box.set_margin_top(10)
        main_box.set_margin_bottom(10)

        # Title
        title_label = Gtk.Label(label="ACE Dryer Control")
        title_label.get_style_context().add_class("description")
        title_label.set_markup("<big><b>ACE Dryer Control</b></big>")
        main_box.pack_start(title_label, False, False, 5)

        # Per-instance dryer controls
        instances_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=15
        )
        instances_box.set_margin_top(5)
        instances_box.set_margin_bottom(5)

        for instance_id in self.ace_instances:
            instance_frame = self._create_dryer_instance_control(instance_id)
            instances_box.pack_start(instance_frame, False, False, 0)

        main_box.pack_start(instances_box, False, False, 0)

        # Bottom buttons
        button_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10
        )
        button_box.set_homogeneous(True)

        # Instance Selection button
        selection_label = self._get_selection_label()
        self._dryer_selection_btn = self._gtk.Button(
            "arrow-right", selection_label, "color2"
        )
        self._dryer_selection_btn.set_size_request(-1, 50)
        self._dryer_selection_btn.connect("clicked", self.cycle_selected_dryer_instance)
        button_box.pack_start(self._dryer_selection_btn, True, True, 0)

        # Start Dryer button
        start_btn = self._gtk.Button(
            "heat-up", "Start Dryer", "color1"
        )
        start_btn.set_size_request(-1, 50)
        start_btn.connect("clicked", self.start_selected_dryer)
        button_box.pack_start(start_btn, True, True, 0)

        # Stop Dryer button
        stop_btn = self._gtk.Button("cancel", "Stop Dryer", "color4")
        stop_btn.set_size_request(-1, 50)
        stop_btn.connect("clicked", self.stop_selected_dryer)
        button_box.pack_start(stop_btn, True, True, 0)

        # Back button
        back_btn = self._gtk.Button("arrow-left", "Back", "color3")
        back_btn.set_size_request(-1, 50)
        back_btn.connect("clicked", lambda w: self.return_to_main_screen())
        button_box.pack_start(back_btn, True, True, 0)

        main_box.pack_start(button_box, False, False, 0)

        self.content.add(main_box)
        self.content.show_all()
        
        # Update frame labels to show current selection
        self._update_dryer_frame_labels()
        logging.info(f"ACE: Dryer panel shown. Controls created for: {list(self._dryer_controls.keys())}")
        
        # Query status immediately via RPC
        self._try_refresh_via_rpc()
        
        # Start periodic refresh of dryer status (every 3 seconds) via RPC
        if hasattr(self, '_dryer_refresh_timer') and self._dryer_refresh_timer:
            GLib.source_remove(self._dryer_refresh_timer)
        self._dryer_refresh_timer = GLib.timeout_add_seconds(3, self._periodic_dryer_refresh)

    def _periodic_dryer_refresh(self):
        """Periodically refresh dryer panel labels (not full rebuild)"""
        if self.current_view == "dryer":
            self._try_refresh_via_rpc()
            return True  # Continue timer
        else:
            # Stop timer if we left the dryer panel
            self._dryer_refresh_timer = None
            return False

    def show_spool_panel(self, widget, reset_selection=True):
        """Show utilities panel"""
        self.current_view = "spool"
        # Clear selection only on fresh entry (e.g., from main menu)
        if reset_selection:
            self.spool_selected_tool = None
            self.pending_spool_tool = None
        for child in self.content.get_children():
            self.content.remove(child)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main_box.set_margin_left(10)
        main_box.set_margin_right(10)
        main_box.set_margin_top(10)
        main_box.set_margin_bottom(10)

        title_label = Gtk.Label(label="Utilities")
        title_label.get_style_context().add_class("description")
        title_label.set_markup("<big><b>Utilities</b></big>")
        main_box.pack_start(title_label, False, False, 5)

        # Tool selection row
        selector_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        selector_row.set_homogeneous(False)

        selector_label = Gtk.Label(label="Tool:")
        selector_label.set_halign(Gtk.Align.START)
        selector_row.pack_start(selector_label, False, False, 0)

        # Touch-friendly popover for tool selection (avoids auto-closing combo on touch)
        if getattr(self, "spool_tool_popover", None):
            try:
                self.spool_tool_popover.destroy()
            except Exception:
                pass

        tool_button = Gtk.MenuButton()
        tool_button.set_relief(Gtk.ReliefStyle.NORMAL)
        tool_button.get_style_context().add_class("color3")
        tool_button.get_style_context().add_class("ace_tool_selector_button")
        tool_button.set_size_request(200, 48)

        popover = Gtk.Popover.new(tool_button)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.set_modal(True)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        listbox.set_activate_on_single_click(True)

        active_index = self.spool_selected_tool if isinstance(self.spool_selected_tool, int) and 0 <= self.spool_selected_tool < self.total_slots else -1

        # Add an explicit "Not selected" entry at the top
        none_row = Gtk.ListBoxRow()
        none_row.set_margin_top(6)
        none_row.set_margin_bottom(6)
        none_label = Gtk.Label()
        none_label.set_markup("<span size='large'><b>Not selected</b></span>")
        none_label.set_xalign(0)
        none_label.set_margin_left(10)
        none_label.set_margin_right(10)
        none_row.add(none_label)
        none_row.tool_index = None
        listbox.add(none_row)
        if active_index < 0:
            listbox.select_row(none_row)

        for tool_index in range(self.total_slots):
            instance_id = tool_index // 4
            local_slot = tool_index % 4
            slot_status = self.instance_data.get(instance_id, {}).get('inventory', [{}] * 4)[local_slot].get('status', 'ready')
            is_empty = slot_status == 'empty'
            label_text = f"T{tool_index}" if not is_empty else f"T{tool_index} (empty)"
            markup = label_text if not is_empty else f"<span foreground='gray'>{label_text}</span>"

            row = Gtk.ListBoxRow()
            row.set_margin_top(6)
            row.set_margin_bottom(6)
            label = Gtk.Label()
            label.set_markup(f"<span size='large'><b>{markup}</b></span>")
            label.set_xalign(0)
            label.set_margin_left(10)
            label.set_margin_right(10)
            row.add(label)
            row.tool_index = tool_index

            if is_empty:
                row.set_sensitive(False)

            listbox.add(row)

            if tool_index == active_index and row.get_sensitive():
                listbox.select_row(row)

        listbox.connect("row-activated", self.on_spool_tool_row_activated)

        popover.add(listbox)
        popover.show_all()
        popover.popdown()
        tool_button.set_popover(popover)
        tool_button.set_label("Not selected" if active_index < 0 else f"T{active_index}")

        self.spool_tool_button = tool_button
        self.spool_tool_popover = popover
        self.spool_selected_tool = active_index if active_index >= 0 else None

        selector_row.pack_start(tool_button, False, False, 0)

        main_box.pack_start(selector_row, False, False, 0)

        # Action buttons grid (wrapped in scroll to ensure accessibility on small screens)
        actions_grid = Gtk.Grid()
        actions_grid.set_column_spacing(12)
        actions_grid.set_row_spacing(12)
        actions_grid.set_column_homogeneous(True)
        actions_grid.set_row_homogeneous(False)
        actions_grid.set_margin_top(4)
        actions_grid.set_margin_bottom(4)
        actions_grid.set_margin_left(4)
        actions_grid.set_margin_right(4)

        def add_action(button, col, row, color_class="color1"):
            button.set_size_request(180, 84)
            button.set_hexpand(True)
            button.get_style_context().add_class(color_class)
            actions_grid.attach(button, col, row, 1, 1)

        # Row 0
        btn_feed = self._gtk.Button(None, "Feed...", "color1")
        btn_feed.connect("clicked", self.show_spool_feed_input)
        add_action(btn_feed, 0, 0, "color1")

        btn_retract = self._gtk.Button(None, "Retract...", "color2")
        btn_retract.connect("clicked", self.show_spool_retract_input)
        add_action(btn_retract, 1, 0, "color2")

        btn_full_unload = self._gtk.Button(None, "Full Unload", "color3")
        btn_full_unload.connect("clicked", self.spool_full_unload)
        add_action(btn_full_unload, 2, 3, "color3")

        # Row 1
        btn_stop_feed = self._gtk.Button(None, "Stop Feed", "color1")
        btn_stop_feed.connect("clicked", self.spool_stop_feed)
        add_action(btn_stop_feed, 0, 1, "color1")

        btn_stop_retract = self._gtk.Button(None, "Stop Retract", "color2")
        btn_stop_retract.connect("clicked", self.spool_stop_retract)
        add_action(btn_stop_retract, 1, 1, "color2")

        btn_rfid_enable = self._gtk.Button(None, "Enable RFID", "color4")
        btn_rfid_enable.connect("clicked", self.spool_enable_rfid_sync)
        add_action(btn_rfid_enable, 2, 1, "color4")

        # Row 2
        btn_feed_assist_on = self._gtk.Button(None, "Enable Feed Assist", "color4")
        btn_feed_assist_on.connect("clicked", self.spool_enable_feed_assist)
        add_action(btn_feed_assist_on, 0, 2, "color4")

        btn_smart_load = self._gtk.Button(None, "Smart Load All", "color3")
        btn_smart_load.connect("clicked", self.spool_smart_load)
        add_action(btn_smart_load, 1, 2, "color3")

        btn_rfid_disable = self._gtk.Button(None, "Disable RFID", "color4")
        btn_rfid_disable.connect("clicked", self.spool_disable_rfid_sync)
        add_action(btn_rfid_disable, 2, 2, "color4")

        # Row 3
        btn_feed_assist_off = self._gtk.Button(None, "Disable Feed Assist", "color4")
        btn_feed_assist_off.connect("clicked", self.spool_disable_feed_assist)
        add_action(btn_feed_assist_off, 0, 3, "color4")

        btn_smart_unload = self._gtk.Button(None, "Smart Unload", "color3")
        btn_smart_unload.connect("clicked", self.spool_smart_unload)
        add_action(btn_smart_unload, 1, 3, "color3")

        btn_reconnect = self._gtk.Button(None, "ACE Reconnect", "color3")
        btn_reconnect.connect("clicked", self.spool_reconnect)
        add_action(btn_reconnect, 2, 0, "color3")

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
        scroll.set_overlay_scrolling(False)
        scroll.set_propagate_natural_width(True)
        scroll.set_min_content_height(400)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        scroll.add(actions_grid)
        main_box.pack_start(scroll, True, True, 5)

        # Bottom navigation
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        nav_box.set_homogeneous(True)

        back_btn = self._gtk.Button("arrow-left", "Back", "color3")
        back_btn.set_size_request(-1, 50)
        back_btn.connect("clicked", lambda w: self.return_to_main_screen())
        nav_box.pack_start(back_btn, True, True, 0)

        main_box.pack_end(nav_box, False, False, 0)

        self.content.add(main_box)
        self.content.show_all()

    def return_to_spool_panel(self):
        """Return from sub-screen back to spool panel"""
        self.current_view = "spool"
        self.pending_spool_tool = None
        # Drop keypad so a fresh instance is created next time we open feed/retract
        if hasattr(self, "spool_keypad") and self.spool_keypad is not None:
            try:
                parent = self.spool_keypad.get_parent()
                if parent:
                    parent.remove(self.spool_keypad)
                self.spool_keypad.destroy()
            except Exception:
                pass
            self.spool_keypad = None
        for child in self.content.get_children():
            self.content.remove(child)
        self.show_spool_panel(None, reset_selection=False)

    def on_spool_tool_row_activated(self, listbox, row):
        if not row:
            return

        if not row.get_sensitive():
            return

        tool_index = getattr(row, "tool_index", None)
        if tool_index is None:
            self.spool_selected_tool = None
            if getattr(self, "spool_tool_button", None):
                self.spool_tool_button.set_label("Not selected")
        else:
            self.spool_selected_tool = tool_index
            if getattr(self, "spool_tool_button", None):
                self.spool_tool_button.set_label(f"T{self.spool_selected_tool}")

        if getattr(self, "spool_tool_popover", None):
            self.spool_tool_popover.popdown()

    def _spool_selected_tool_or_popup(self):
        tool = self.spool_selected_tool
        if tool is None:
            self._screen.show_popup_message("Select a tool first")
            return None
        return tool

    def _spool_selected_tool_optional(self):
        """Return selected tool or None without prompting."""
        tool = self.spool_selected_tool
        if isinstance(tool, int) and tool >= 0:
            return tool
        return None

    def spool_smart_unload(self, widget):
        tool = self._spool_selected_tool_optional()
        cmd = "ACE_SMART_UNLOAD" if tool is None else f"ACE_SMART_UNLOAD TOOL={tool}"
        sent = self._send_gcode(cmd)
        if sent:
            logging.info(f"ACE: Sent {cmd}")
            msg = "Smart unload" if tool is None else f"Smart unload T{tool}"
            self._screen.show_popup_message(msg, 1)
        else:
            logging.error(f"ACE: Failed to send {cmd}")
            self._screen.show_popup_message("Failed to send smart unload", 2)

    def spool_smart_load(self, widget):
        self._send_gcode("ACE_SMART_LOAD")
        self._screen.show_popup_message("Smart load all slots", 1)

    def spool_full_unload(self, widget):
        tool = self._spool_selected_tool_or_popup()
        if tool is None:
            return
        self._send_gcode(f"ACE_FULL_UNLOAD TOOL={tool}")
        self._screen.show_popup_message(f"Full unload T{tool}", 1)

    def spool_enable_feed_assist(self, widget):
        tool = self._spool_selected_tool_or_popup()
        if tool is None:
            return
        self._send_gcode(f"ACE_ENABLE_FEED_ASSIST T={tool}")
        self._screen.show_popup_message(f"Feed assist ON T{tool}", 1)

    def spool_disable_feed_assist(self, widget):
        tool = self._spool_selected_tool_or_popup()
        if tool is None:
            return
        self._send_gcode(f"ACE_DISABLE_FEED_ASSIST T={tool}")
        self._screen.show_popup_message(f"Feed assist OFF T{tool}", 1)

    def spool_stop_feed(self, widget):
        tool = self._spool_selected_tool_or_popup()
        if tool is None:
            return
        self._send_gcode(f"ACE_STOP_FEED T={tool}")
        self._screen.show_popup_message(f"Stop feed T{tool}", 1)

    def spool_stop_retract(self, widget):
        tool = self._spool_selected_tool_or_popup()
        if tool is None:
            return
        self._send_gcode(f"ACE_STOP_RETRACT T={tool}")
        self._screen.show_popup_message(f"Stop retract T{tool}", 1)

    def spool_enable_rfid_sync(self, widget):
        self.rfid_sync_enabled = True
        self._send_gcode("ACE_ENABLE_RFID_SYNC")
        self._screen.show_popup_message("RFID sync enabled", 1)

    def spool_disable_rfid_sync(self, widget):
        self.rfid_sync_enabled = False
        self._send_gcode("ACE_DISABLE_RFID_SYNC")
        self._screen.show_popup_message("RFID sync disabled", 1)

    def spool_reconnect(self, widget):
        self._send_gcode("ACE_RECONNECT")
        self._screen.show_popup_message("ACE reconnect sent", 1)

    def show_spool_feed_input(self, widget):
        tool = self._spool_selected_tool_or_popup()
        if tool is None:
            return
        self.pending_spool_tool = tool
        self._show_spool_amount_input("Feed", self.handle_spool_feed_amount)

    def show_spool_retract_input(self, widget):
        tool = self._spool_selected_tool_or_popup()
        if tool is None:
            return
        self.pending_spool_tool = tool
        self._show_spool_amount_input("Retract", self.handle_spool_retract_amount)

    def _show_spool_amount_input(self, title, callback):
        self.current_view = "spool_keypad"
        for child in self.content.get_children():
            self.content.remove(child)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        main_box.set_margin_left(10)
        main_box.set_margin_right(10)
        main_box.set_margin_top(10)
        main_box.set_margin_bottom(10)

        title_label = Gtk.Label(label=f"{title} Amount (mm)")
        title_label.get_style_context().add_class("description")
        title_label.set_markup(f"<big><b>{title} Amount (mm)</b></big>")
        main_box.pack_start(title_label, False, False, 5)

        # Always create a fresh keypad to avoid stale GTK parentage when re-entering
        if hasattr(self, "spool_keypad") and self.spool_keypad is not None:
            try:
                parent = self.spool_keypad.get_parent()
                if parent:
                    parent.remove(self.spool_keypad)
                self.spool_keypad.destroy()
            except Exception:
                pass

        self.spool_keypad = Keypad(
            self._screen,
            callback,
            None,
            self.return_to_spool_panel,
        )
        self.spool_keypad.clear()
        self.spool_keypad.labels['entry'].set_text("0")
        self.spool_keypad.show_pid(False)

        main_box.pack_start(self.spool_keypad, True, True, 0)

        # Back button
        back_btn = self._gtk.Button("arrow-left", "Back", "color3")
        back_btn.set_size_request(-1, 50)
        back_btn.connect("clicked", lambda w: self.return_to_spool_panel())
        main_box.pack_end(back_btn, False, False, 0)

        self.content.add(main_box)
        self.content.show_all()

    def handle_spool_feed_amount(self, amount):
        self._handle_spool_amount(amount, is_feed=True)

    def handle_spool_retract_amount(self, amount):
        self._handle_spool_amount(amount, is_feed=False)

    def _handle_spool_amount(self, amount, is_feed=True):
        try:
            length_val = float(amount)
            if length_val <= 0:
                raise ValueError("Amount must be positive")
            length = int(round(length_val))
            if length <= 0:
                raise ValueError("Amount must be positive")
        except Exception:
            self._screen.show_popup_message("Enter a valid positive amount")
            return

        tool = self._spool_selected_tool_or_popup()
        if tool is None:
            return

        cmd = "ACE_FEED" if is_feed else "ACE_RETRACT"
        self._send_gcode(f"{cmd} T={tool} LENGTH={length}")
        action = "Feeding" if is_feed else "Retracting"
        self._screen.show_popup_message(f"{action} {length}mm on T{tool}", 1)
        GLib.timeout_add(200, self.return_to_spool_panel)

    def _create_dryer_instance_control(self, instance_id):
        """Create dryer control UI for a single ACE instance"""
        frame = Gtk.Frame()
        frame_label = Gtk.Label()
        frame_label.set_use_markup(True)  # Enable markup rendering
        initial_markup = f"<b>ACE {instance_id}</b>"
        frame_label.set_markup(initial_markup)
        frame.set_label_widget(frame_label)
        frame.get_style_context().add_class("frame-item")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_left(10)
        box.set_margin_right(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Status display
        status_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10
        )

        status_label_prefix = Gtk.Label(label="Status:")
        status_label_prefix.set_halign(Gtk.Align.START)
        status_box.pack_start(status_label_prefix, False, False, 0)

        # Get dryer status from printer data
        dryer_status = self._get_dryer_status(instance_id)
        status_text = dryer_status.get('status', 'unknown')

        # Create status indicator - store reference for updates
        status_indicator = Gtk.Label(label=status_text.upper())
        status_indicator.set_halign(Gtk.Align.START)

        if status_text == 'drying':
            status_indicator.set_markup(
                '<span foreground="green"><b>DRYING</b></span>'
            )
        elif status_text == 'stop':
            status_indicator.set_markup(
                '<span foreground="gray"><b>STOPPED</b></span>'
            )
        else:
            # Show any other status (errors, unknown states) in red
            status_indicator.set_markup(
                f'<span foreground="red"><b>{status_text.upper()}</b></span>'
            )

        status_box.pack_start(status_indicator, False, False, 0)

        # Target temperature display - store reference
        target_temp = dryer_status.get('target_temp', 0)
        target_label = Gtk.Label(
            label=f"Target: {target_temp}°C"
        )
        target_label.set_halign(Gtk.Align.START)
        status_box.pack_start(target_label, False, False, 0)
        
        # Current temperature display - store reference
        current_temp = dryer_status.get('current_temp', 0)
        current_label = Gtk.Label(
            label=f"Current: {current_temp}°C"
        )
        current_label.set_halign(Gtk.Align.START)
        status_box.pack_start(current_label, False, False, 0)

        box.pack_start(status_box, False, False, 0)

        # Temperature setting
        temp_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        temp_label = Gtk.Label(label="Target Temp:")
        temp_label.set_size_request(100, -1)
        temp_label.set_halign(Gtk.Align.START)
        temp_box.pack_start(temp_label, False, False, 0)

        # Temperature scale (25°C to 55°C for ACE dryer)
        temp_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 25, 55, 5
        )
        temp_scale.set_value(45)  # Default 45°C
        temp_scale.set_draw_value(True)
        temp_scale.set_value_pos(Gtk.PositionType.RIGHT)
        temp_scale.set_size_request(200, -1)
        temp_box.pack_start(temp_scale, True, True, 0)

        box.pack_start(temp_box, False, False, 0)

        # Duration setting
        duration_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10
        )

        duration_label = Gtk.Label(label="Duration (min):")
        duration_label.set_size_request(100, -1)
        duration_label.set_halign(Gtk.Align.START)
        duration_box.pack_start(duration_label, False, False, 0)

        # Duration scale (30min to 480min / 8 hours)
        duration_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 30, 480, 30
        )
        duration_scale.set_value(240)  # Default 4 hours
        duration_scale.set_draw_value(True)
        duration_scale.set_value_pos(Gtk.PositionType.RIGHT)
        duration_scale.set_size_request(200, -1)
        duration_box.pack_start(duration_scale, True, True, 0)

        box.pack_start(duration_box, False, False, 0)

        frame.add(box)
        
        # Store references for updates without rebuilding
        self._dryer_controls[instance_id] = {
            'frame': frame,  # Store frame itself for show/hide
            'frame_label': frame_label,
            'status_label': status_indicator,
            'target_label': target_label,
            'current_label': current_label,
            'temp_scale': temp_scale,
            'duration_scale': duration_scale
        }
        
        return frame

    def _get_dryer_status(self, instance_id):
        """Get dryer status from printer data for specific ACE instance"""
        try:
            # Try to get status from printer object
            ace_name = f"ace_instance_{instance_id}"

            if hasattr(self._printer, 'lookup_object'):
                ace_obj = self._printer.lookup_object(ace_name, None)
                if ace_obj and hasattr(ace_obj, 'get_status'):
                    status = ace_obj.get_status()
                    logging.info(f"ACE: Got status from {ace_name}: {status}")
                    # Get dryer_status dict and current temp from main status
                    dryer_status = status.get('dryer_status', {})
                    current_temp = status.get('temp', 0)  # Current dryer temperature
                    
                    # Combine dryer_status with current temp
                    result = dict(dryer_status)
                    result['current_temp'] = current_temp
                    logging.info(f"ACE: Dryer status result: {result}")
                    return result
                else:
                    logging.warning(f"ACE: Could not get ACE object or get_status for {ace_name}")

            # Fallback: try from printer.data
            if hasattr(self._printer, 'data'):
                ace_data = self._printer.data.get(ace_name, {})
                if 'dryer_status' in ace_data:
                    result = dict(ace_data['dryer_status'])
                    result['current_temp'] = ace_data.get('temp', 0)
                    logging.info(f"ACE: Dryer status from printer.data: {result}")
                    return result
                else:
                    logging.warning(f"ACE: No dryer_status in printer.data for {ace_name}")
        except Exception as e:
            logging.error(f"ACE: Error getting dryer status: {e}", exc_info=True)

        logging.warning(f"ACE: Returning default dryer status for instance {instance_id}")
        return {'status': 'stop', 'target_temp': 0, 'current_temp': 0, 'remain_time': 0}

    def start_single_dryer(
        self, widget, instance_id, temp_scale, duration_scale
    ):
        """Start dryer for a single ACE instance"""
        temp = int(temp_scale.get_value())
        duration = int(duration_scale.get_value())

        instance_param = (
            f" INSTANCE={instance_id}" if instance_id > 0 else ""
        )
        gcode = (
            f"ACE_START_DRYING{instance_param} "
            f"TEMP={temp} DURATION={duration}"
        )

        self._send_gcode(gcode)
        self._screen.show_popup_message(
            f"Starting dryer on ACE {instance_id}\n"
            f"{temp}°C for {duration} minutes", 2
        )

    def stop_single_dryer(self, widget, instance_id):
        """Stop dryer for a single ACE instance"""
        instance_param = (
            f" INSTANCE={instance_id}" if instance_id > 0 else ""
        )
        self._send_gcode(f"ACE_STOP_DRYING{instance_param}")
        self._screen.show_popup_message(
            f"Stopping dryer on ACE {instance_id}", 1
        )

    def _get_selection_label(self):
        """Get label text for current dryer selection"""
        if self.selected_dryer_instance == "ALL":
            return "Selected: ALL"
        else:
            return f"Selected: ACE {self.selected_dryer_instance}"
    
    def _update_dryer_frame_labels(self):
        """Update frame visibility and labels based on which instance is selected"""
        for instance_id in self.ace_instances:
            if instance_id in self._dryer_controls:
                frame = self._dryer_controls[instance_id].get('frame')
                frame_label = self._dryer_controls[instance_id]['frame_label']
                
                if self.selected_dryer_instance == "ALL":
                    # When ALL is selected, show only ACE 0's controls
                    if instance_id == 0:
                        if frame:
                            frame.show_all()
                        frame_label.set_markup("<b>ALL ACEs (Temp of ACE 0 shown)</b>")
                    else:
                        if frame:
                            frame.hide()
                elif self.selected_dryer_instance == instance_id:
                    # Show only selected frame with highlight
                    if frame:
                        frame.show_all()
                    frame_label.set_markup(f"<span foreground='#4a9eff'><b>ACE {instance_id}</b></span>")
                else:
                    # Hide non-selected frames
                    if frame:
                        frame.hide()
    
    def cycle_selected_dryer_instance(self, widget):
        """Cycle through dryer instance selection: ACE 0 -> ACE 1 -> ALL -> ACE 0..."""
        if self.selected_dryer_instance == "ALL":
            # Go to first instance
            self.selected_dryer_instance = self.ace_instances[0]
        elif self.selected_dryer_instance in self.ace_instances:
            # Find current position and go to next
            current_idx = self.ace_instances.index(self.selected_dryer_instance)
            if current_idx < len(self.ace_instances) - 1:
                # Go to next instance
                self.selected_dryer_instance = self.ace_instances[current_idx + 1]
            else:
                # Wrapped around - go to ALL
                self.selected_dryer_instance = "ALL"
        else:
            # Fallback to ALL if something is wrong
            self.selected_dryer_instance = "ALL"
        
        # Update button label
        if hasattr(self, '_dryer_selection_btn'):
            new_label = self._get_selection_label()
            self._dryer_selection_btn.set_label(new_label)
        
        # Update frame labels to show selection
        self._update_dryer_frame_labels()

    def start_selected_dryer(self, widget):
        """Start dryer on selected ACE instance(s)"""
        if self.selected_dryer_instance == "ALL":
            # Start all dryers using their individual slider settings
            for instance_id in self.ace_instances:
                # Get slider values for this instance
                if instance_id in self._dryer_controls:
                    controls = self._dryer_controls[instance_id]
                    temp = int(controls['temp_scale'].get_value())
                    duration = int(controls['duration_scale'].get_value())
                else:
                    # Fallback to defaults if sliders not found
                    temp = 45
                    duration = 240
                
                instance_param = (
                    f" INSTANCE={instance_id}" if instance_id > 0 else ""
                )
                self._send_gcode(
                    f"ACE_START_DRYING{instance_param} TEMP={temp} DURATION={duration}"
                )

            self._screen.show_popup_message(
                f"Starting all {len(self.ace_instances)} dryers", 1
            )
        else:
            # Start single selected instance
            instance_id = self.selected_dryer_instance
            if instance_id in self._dryer_controls:
                controls = self._dryer_controls[instance_id]
                temp = int(controls['temp_scale'].get_value())
                duration = int(controls['duration_scale'].get_value())
            else:
                temp = 45
                duration = 240
            
            instance_param = (
                f" INSTANCE={instance_id}" if instance_id > 0 else ""
            )
            self._send_gcode(
                f"ACE_START_DRYING{instance_param} TEMP={temp} DURATION={duration}"
            )
            
            self._screen.show_popup_message(
                f"Starting dryer on ACE {instance_id}\n{temp}°C for {duration} minutes", 2
            )

    def stop_selected_dryer(self, widget):
        """Stop dryer on selected ACE instance(s)"""
        if self.selected_dryer_instance == "ALL":
            # Stop all dryers
            for instance_id in self.ace_instances:
                instance_param = (
                    f" INSTANCE={instance_id}" if instance_id > 0 else ""
                )
                self._send_gcode(f"ACE_STOP_DRYING{instance_param}")

            self._screen.show_popup_message(
                f"Stopping all {len(self.ace_instances)} dryers", 1
            )
        else:
            # Stop single selected instance
            instance_id = self.selected_dryer_instance
            instance_param = (
                f" INSTANCE={instance_id}" if instance_id > 0 else ""
            )
            self._send_gcode(f"ACE_STOP_DRYING{instance_param}")
            
            self._screen.show_popup_message(
                f"Stopping dryer on ACE {instance_id}", 1
            )

    def show_slot_config_screen(self, instance_id, local_slot):
        """Create compact two-column configuration screen that fits 480px"""
        self.current_view = "slot_config"
        global_tool = self.instance_data[instance_id]['tool_offset'] + local_slot
        slot_data = self.instance_data[instance_id]['inventory'][local_slot]
        slot_is_rfid = bool(slot_data.get("rfid"))
        rfid_locked = slot_is_rfid and self.rfid_sync_enabled

        # Load current values from slot data
        # Check if slot is truly empty (status='empty' or material is Empty/blank)
        slot_status = slot_data.get("status", "empty")
        current_material = slot_data.get("material", "")

        # Track whether this slot should allow selecting "Empty" in the material list
        self.config_slot_allows_empty = (
            slot_status == "empty" or current_material in ["Empty", "", None]
        )

        if (slot_status == "empty" or
                current_material in ["Empty", "", None]):
            # Empty slot - set defaults
            self.config_material = "Empty"
            self.config_color = [255, 255, 255]
            self.config_temp = 0
        else:
            # Load existing configuration
            self.config_material = current_material
            self.config_color = slot_data.get("color", [255, 255, 255])[:]
            self.config_temp = slot_data.get("temp", 200)

        logging.info(
            f"ACE: Loading T{global_tool} config - "
            f"Material: {self.config_material}, "
            f"Color: {self.config_color}, Temp: {self.config_temp}"
        )

        # Clear current content and create two-column layout
        for child in self.content.get_children():
            self.content.remove(child)

        # Create main grid with two columns - compact spacing
        main_grid = Gtk.Grid()
        main_grid.set_column_homogeneous(True)
        main_grid.set_row_spacing(2)
        main_grid.set_column_spacing(1)
        main_grid.set_margin_left(5)
        main_grid.set_margin_right(10)
        main_grid.set_margin_top(2)
        main_grid.set_margin_bottom(1)

        # Left column - Configuration options (compact)
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        # Compact title for left column
        config_title = Gtk.Label(label=f"Configure T{global_tool}")
        config_title.get_style_context().add_class("description")
        left_box.pack_start(config_title, False, False, 0)

        if rfid_locked:
            lock_label = Gtk.Label(
                label="RFID spool detected. Disable RFID sync to edit material/color."
            )
            lock_label.get_style_context().add_class("description")
            lock_label.set_line_wrap(True)
            lock_label.set_max_width_chars(30)
            left_box.pack_start(lock_label, False, False, 0)

        # Material selection button
        self.material_btn = self._gtk.Button("filament", f"Material: {self.config_material}", "color1")
        self.material_btn.set_size_request(-1, 45)
        self.material_btn.connect("clicked", self.show_material_selection)
        self.material_btn.set_sensitive(not rfid_locked)
        left_box.pack_start(self.material_btn, False, False, 0)

        # Color selection button with preview
        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        # Color preview with current color
        self.config_color_preview = Gtk.EventBox()
        self.config_color_preview.set_size_request(30, 30)
        self.config_color_preview.get_style_context().add_class("ace_color_preview")
        self.set_slot_color(self.config_color_preview, self.config_color)
        color_box.pack_start(self.config_color_preview, False, False, 0)

        # Color button
        self.color_btn = self._gtk.Button("palette", "Select Color", "color2")
        self.color_btn.set_size_request(-1, 45)
        self.color_btn.connect("clicked", self.show_color_selection)
        self.color_btn.set_sensitive(not rfid_locked)
        color_box.pack_start(self.color_btn, True, True, 0)

        left_box.pack_start(color_box, False, False, 0)

        # Temperature selection button - show "Not Set" if 0
        temp_display = (
            f"Temperature: {self.config_temp}°C"
            if self.config_temp > 0
            else "Temperature: Not Set"
        )
        self.temp_btn = self._gtk.Button(
            "heat-up", temp_display, "color3"
        )
        self.temp_btn.set_size_request(-1, 45)
        self.temp_btn.connect("clicked", self.show_temperature_selection)
        left_box.pack_start(self.temp_btn, False, False, 0)

        # Compact action buttons
        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        action_box.set_homogeneous(True)

        # Save button
        save_btn = self._gtk.Button("complete", "Save", "color1")
        save_btn.set_size_request(-1, 40)
        save_btn.connect("clicked", self.save_slot_config, instance_id, local_slot, global_tool)
        action_box.pack_start(save_btn, True, True, 0)

        # Cancel button
        cancel_btn = self._gtk.Button("cancel", "Cancel", "color4")
        cancel_btn.set_size_request(-1, 40)
        cancel_btn.connect("clicked", self.cancel_slot_config)
        action_box.pack_start(cancel_btn, True, True, 0)

        left_box.pack_end(action_box, False, False, 0)

        # Right column - Selection panels (compact)
        self.right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.right_box.set_hexpand(True)
        self.right_box.set_vexpand(True)

        # Compact welcome message for right column
        welcome_label = Gtk.Label(label="Select an option from the left\nto configure the slot")
        welcome_label.set_justify(Gtk.Justification.CENTER)
        welcome_label.get_style_context().add_class("description")
        self.right_box.pack_start(welcome_label, True, True, 0)

        # Add columns to main grid
        main_grid.attach(left_box, 0, 0, 1, 1)
        main_grid.attach(self.right_box, 1, 0, 1, 1)

        self.content.add(main_grid)
        self.content.show_all()

    def show_material_selection(self, widget):
        """Show compact material selection in right column"""
        # Clear right column
        for child in self.right_box.get_children():
            self.right_box.remove(child)

        # Compact material selection title
        title = Gtk.Label(label="Select Material")
        title.get_style_context().add_class("description")
        self.right_box.pack_start(title, False, False, 0)

        # Scrollable material list with Empty option
        materials = self.material_options()
        if not getattr(self, "config_slot_allows_empty", True):
            materials = [m for m in materials if m != "Empty"]

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
        scrolled.set_overlay_scrolling(False)
        scrolled.set_min_content_height(260)
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_size_request(-1, 320)

        material_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        material_list.set_margin_top(5)
        material_list.set_margin_bottom(5)

        for material in materials:
            material_btn = self._gtk.Button("filament", material, "color2")
            material_btn.set_size_request(-1, 35)
            material_btn.connect("clicked", self.select_material, material)

            # Highlight current selection
            if material == self.config_material:
                material_btn.get_style_context().add_class("button_active")

            material_list.pack_start(material_btn, False, False, 3)

        scrolled.add(material_list)
        self.right_box.pack_start(scrolled, True, True, 0)

        self.right_box.show_all()

    def select_material(self, widget, material):
        """Handle material selection"""
        # Check if material actually changed (not same selection again)
        previous_material = self.config_material
        self.config_material = material
        self.material_btn.set_label(f"Material: {material}")

        # Auto-populate temperature whenever material actually changes
        if previous_material != material:
            if material in self.FILAMENT_TEMP_DEFAULTS:
                new_temp = self.FILAMENT_TEMP_DEFAULTS[material]
                self.config_temp = new_temp
                self.temp_btn.set_label(f"Temperature: {new_temp}°C")
            elif material == "Empty":
                self.config_temp = 0
                self.temp_btn.set_label("Temperature: Not Set")
                logging.info(
                    f"ACE: Auto-set temperature to {new_temp}°C "
                    f"for {material}"
                )
                self._screen.show_popup_message(
                    f"Temperature set to {new_temp}°C for {material}", 1
                )

        self.clear_right_column()

    def show_color_selection(self, widget):
        """Show compact color picker in right column"""
        # Clear right column
        for child in self.right_box.get_children():
            self.right_box.remove(child)

        # Compact current color preview and RGB display
        preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        preview_box.set_halign(Gtk.Align.CENTER)

        self.right_color_preview = Gtk.EventBox()
        self.right_color_preview.set_size_request(40, 40)
        self.right_color_preview.get_style_context().add_class("ace_color_preview")
        self.set_slot_color(self.right_color_preview, self.config_color)
        preview_box.pack_start(self.right_color_preview, False, False, 0)

        self.rgb_display = Gtk.Label(label=f"RGB: {self.config_color[0]},{self.config_color[1]},{self.config_color[2]}")
        self.rgb_display.get_style_context().add_class("description")
        preview_box.pack_start(self.rgb_display, False, False, 0)

        self.right_box.pack_start(preview_box, False, False, 5)

        # RGB sliders
        slider_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)

        self.color_sliders = {}
        for i, color_name in enumerate(['Red', 'Green', 'Blue']):
            color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            label = Gtk.Label(label=f"{color_name[0]}:")
            label.set_size_request(20, -1)
            color_row.pack_start(label, False, False, 0)

            slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 255, 1)
            slider.set_value(self.config_color[i])
            slider.set_size_request(150, 25)
            slider.set_draw_value(True)
            slider.set_value_pos(Gtk.PositionType.RIGHT)
            slider.connect("value-changed", self.on_color_slider_changed, i)
            self.color_sliders[i] = slider
            color_row.pack_start(slider, True, True, 0)

            slider_box.pack_start(color_row, False, False, 0)

        self.right_box.pack_start(slider_box, False, False, 5)

        # Color presets - common filament colors
        preset_colors = [
            ("White", [255, 255, 255]),
            ("Black", [0, 0, 0]),
            ("Gray", [128, 128, 128]),
            ("Red", [255, 0, 0]),
            ("Green", [0, 255, 0]),
            ("Blue", [0, 0, 255]),
            ("Yellow", [255, 255, 0]),
            ("Orange", [255, 165, 0]),
            ("Purple", [128, 0, 128]),
            ("Cyan", [0, 255, 255]),
            ("Magenta", [255, 0, 255]),
            ("Brown", [139, 69, 19])
        ]

        preset_grid = Gtk.Grid()
        preset_grid.set_row_spacing(3)
        preset_grid.set_column_spacing(3)
        preset_grid.set_halign(Gtk.Align.CENTER)

        for i, (name, rgb) in enumerate(preset_colors):
            preset_btn = Gtk.Button(label=name)
            preset_btn.set_size_request(60, 25)

            color = Gdk.RGBA(rgb[0]/255.0, rgb[1]/255.0, rgb[2]/255.0, 1.0)
            preset_btn.override_background_color(Gtk.StateFlags.NORMAL, color)

            brightness = (rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114)
            text_color = Gdk.RGBA(0, 0, 0, 1) if brightness > 128 else Gdk.RGBA(1, 1, 1, 1)
            preset_btn.override_color(Gtk.StateFlags.NORMAL, text_color)

            preset_btn.connect("clicked", self.select_color_preset, rgb[:])
            preset_grid.attach(preset_btn, i % 3, i // 3, 1, 1)

        self.right_box.pack_start(preset_grid, False, False, 5)

        # Apply button
        apply_btn = self._gtk.Button("complete", "Apply Color", "color1")
        apply_btn.set_size_request(-1, 30)
        apply_btn.connect("clicked", self.apply_color_selection)
        self.right_box.pack_end(apply_btn, False, False, 0)

        self.right_box.show_all()

    def on_color_slider_changed(self, slider, color_index):
        """Handle color slider changes"""
        value = int(slider.get_value())
        self.config_color[color_index] = value
        self.set_slot_color(self.right_color_preview, self.config_color)
        self.rgb_display.set_text(f"RGB: {self.config_color[0]},{self.config_color[1]},{self.config_color[2]}")

    def select_color_preset(self, widget, rgb):
        """Handle color preset selection"""
        self.config_color = rgb[:]
        for i, value in enumerate(rgb):
            self.color_sliders[i].set_value(value)
        self.set_slot_color(self.right_color_preview, self.config_color)

    def apply_color_selection(self, widget):
        """Apply selected color"""
        self.set_slot_color(self.config_color_preview, self.config_color)
        self.clear_right_column()

    def show_temperature_selection(self, widget):
        """Show temperature selection without using the shared Keypad widget."""
        for child in self.right_box.get_children():
            self.right_box.remove(child)

        title = Gtk.Label(label="Set Temperature")
        title.get_style_context().add_class("temperature_entry")
        self.right_box.pack_start(title, False, False, 0)

        entry = Gtk.Entry()
        entry.set_max_length(5)
        entry.set_alignment(0.5)
        if getattr(self, "config_temp", None) is not None:
            entry.set_text(str(self.config_temp))

        def apply_temperature(_widget=None):
            self.handle_temperature_input(entry.get_text())

        def add_char(char):
            # Replace selection when present, otherwise append
            text = entry.get_text()
            bounds = entry.get_selection_bounds()
            if bounds:
                start, end = bounds
                text = text[:start] + char + text[end:]
                cursor_pos = start + len(char)
            else:
                cursor_pos = len(text) + len(char)
                text = text + char
            entry.set_text(text)
            entry.set_position(cursor_pos)

        def backspace(_widget=None):
            text = entry.get_text()
            bounds = entry.get_selection_bounds()
            if bounds:
                start, end = bounds
                text = text[:start] + text[end:]
                entry.set_text(text)
                entry.set_position(start)
            elif text:
                text = text[:-1]
                entry.set_text(text)
                entry.set_position(len(text))

        entry.connect("activate", apply_temperature)
        entry.grab_focus()
        entry.select_region(0, -1)

        # On-screen numpad to support touch entry
        numpad = Gtk.Grid(row_homogeneous=False, column_homogeneous=False)
        keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'B', '0', '.']

        # Scale sizes based on screen height to adapt to different resolutions
        screen = Gdk.Screen.get_default()
        screen_h = screen.get_height() if screen else 480
        # Target ~12% of screen height per key, with sane bounds
        base_size = int(max(52, min(screen_h * 0.12, 82)))
        label_size = int(max(16000, min(base_size * 400, 30000)))
        grid_spacing = max(int(base_size * 0.05), 1)
        for i, label in enumerate(keys):
            if label == 'B':
                btn = Gtk.Button(label="⌫")
                btn.connect('clicked', lambda _w: backspace())
                label_text = "⌫"
            else:
                btn = Gtk.Button(label=label)
                btn.connect('clicked', lambda _w, ch=label: add_char(ch))
                label_text = label
            btn.get_style_context().add_class("ace_numpad_button")
            btn.set_size_request(base_size, base_size)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            child = btn.get_child()
            if isinstance(child, Gtk.Label):
                child.set_markup(f"<span size='{label_size}'>{GLib.markup_escape_text(label_text)}</span>")
            numpad.attach(btn, i % 3, i // 3, 1, 1)

        btn_row = Gtk.Box(spacing=6)
        cancel_btn = self._gtk.Button('cancel', scale=.66)
        cancel_btn.connect("clicked", self.clear_right_column)
        apply_btn = self._gtk.Button('complete', style="color1")
        apply_btn.connect("clicked", apply_temperature)

        btn_row.pack_start(cancel_btn, False, False, 0)
        btn_row.pack_end(apply_btn, False, False, 0)

        # Keep a compact vertical stack
        entry.set_size_request(-1, int(base_size * 0.65))
        numpad.set_row_spacing(grid_spacing)
        numpad.set_column_spacing(grid_spacing)
        numpad.set_halign(Gtk.Align.CENTER)
        numpad.set_valign(Gtk.Align.START)

        self.right_box.pack_start(entry, False, False, 4)
        self.right_box.pack_start(numpad, False, False, 4)
        self.right_box.pack_start(btn_row, False, False, 6)
        self.right_box.show_all()

    def handle_temperature_input(self, temp):
        """Handle temperature input from keypad"""
        logging.info(f"ACE: handle_temperature_input called with temp='{temp}' (type: {type(temp)})")
        try:
            temp_value = int(float(temp))
            logging.info(f"ACE: Parsed temp_value={temp_value}")

            if 0 <= temp_value <= 300:
                self.config_temp = temp_value
                self.temp_btn.set_label(f"Temperature: {temp_value}°C")
                logging.info(f"ACE: ✓ Set self.config_temp={self.config_temp}")
                self.clear_right_column()
            else:
                self._screen.show_popup_message("Temperature must be between 0-300°C")
                logging.error(f"ACE: Temp {temp_value} out of range")
        except (ValueError, TypeError) as e:
            self._screen.show_popup_message("Invalid temperature value")
            logging.error(f"ACE: Failed to parse temp '{temp}': {e}")

    def clear_right_column(self, widget=None):
        """Clear right column and show welcome message"""
        for child in self.right_box.get_children():
            self.right_box.remove(child)

        welcome_label = Gtk.Label(label="Select an option from the left\nto configure the slot")
        welcome_label.set_justify(Gtk.Justification.CENTER)
        welcome_label.get_style_context().add_class("description")
        self.right_box.pack_start(welcome_label, True, True, 0)
        self.right_box.show_all()

    def save_slot_config(self, widget, instance_id, local_slot, global_tool):
        """Save slot configuration"""
        material = self.config_material
        color_str = (
            f"{self.config_color[0]},"
            f"{self.config_color[1]},"
            f"{self.config_color[2]}"
        )
        temp = self.config_temp

        # Validate data before saving
        logging.info(
            f"ACE: Attempting save - material='{material}', "
            f"temp={temp}, color={color_str}"
        )

        if not material or material.strip() == '' or material == 'Empty':
            self._screen.show_popup_message(
                "ERROR: Please select a material first!"
            )
            logging.error("ACE: Save blocked - no material selected")
            return

        if temp < 0 or temp > 300:
            self._screen.show_popup_message(
                f"ERROR: Temperature must be between 0-300°C!\nCurrent: {temp}°C"
            )
            logging.error(f"ACE: Save blocked - invalid temp={temp}")
            return

        # Log what we're about to save
        logging.info(f"ACE: ✓ Validation passed - Saving slot {local_slot}")

        # Update local data IMMEDIATELY for responsive UI
        self.instance_data[instance_id]['inventory'][local_slot] = {
            'material': material,
            'temp': temp,
            'color': self.config_color[:],
            'status': 'ready',
            'rfid': False
        }

        # Send save command to Klipper (async - no waiting)
        gcode = (
            f'ACE_SET_SLOT INDEX={local_slot} INSTANCE={instance_id} '
            f'MATERIAL="{material}" TEMP={temp} COLOR="{color_str}"'
        )
        logging.info(f"ACE: Sending gcode: {gcode}")
        self._send_gcode(gcode)

        self._screen.show_popup_message(
            f"✓ T{global_tool} saved: {material} {temp}°C", 1
        )

        # Return to main after brief delay
        GLib.timeout_add(300, self.return_to_main_screen)

    def cancel_slot_config(self, widget):
        """Cancel configuration and return to main screen"""
        # Reset config variables to prevent stale data
        if hasattr(self, 'config_material'):
            self.config_material = None
        if hasattr(self, 'config_color'):
            self.config_color = None
        if hasattr(self, 'config_temp'):
            self.config_temp = None
        if hasattr(self, 'current_config_instance'):
            self.current_config_instance = None
        if hasattr(self, 'current_config_slot'):
            self.current_config_slot = None

        self.return_to_main_screen()

    def return_to_main_screen(self):
        """Recreate main screen after config"""
        self.current_view = "main"
        
        # Stop dryer refresh timer if active
        if hasattr(self, '_dryer_refresh_timer') and self._dryer_refresh_timer:
            GLib.source_remove(self._dryer_refresh_timer)
            self._dryer_refresh_timer = None
        
        # Clear all content
        for child in self.content.get_children():
            self.content.remove(child)

        # Recreate main screen
        self.create_main_screen()

        # Return False to prevent timeout from repeating
        return False

    def on_back(self):
        """Handle KlipperScreen back: leave spool views back to ACE main"""
        if self.current_view.startswith("spool"):
            self.return_to_main_screen()
            return True  # handled here
        return False
