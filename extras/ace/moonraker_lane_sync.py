"""
Moonraker lane_data adapter for Orca filament sync.

This module writes ACE slot inventory into Moonraker's database namespace,
using the structure expected by OrcaSlicer's MoonrakerPrinterAgent.
"""

import json
import logging
import threading
from urllib import error, parse, request

from .config import SLOTS_PER_ACE


class MoonrakerLaneSyncAdapter:
    """Push ACE inventory into Moonraker DB namespace as lane_data."""

    def __init__(self, gcode, manager, ace_config):
        self.gcode = gcode
        self.manager = manager
        self.enabled = bool(ace_config.get("moonraker_lane_sync_enabled", True))
        self.base_url = str(ace_config.get("moonraker_lane_sync_url", "http://127.0.0.1:7125")).rstrip("/")
        self.namespace = str(ace_config.get("moonraker_lane_sync_namespace", "lane_data"))
        self.api_key = ace_config.get("moonraker_lane_sync_api_key")
        self.timeout_s = float(ace_config.get("moonraker_lane_sync_timeout", 2.0))
        self.unknown_material_mode = str(
            ace_config.get("moonraker_lane_sync_unknown_material_mode", "empty")
        ).strip().lower()
        self.unknown_material_map_to = str(
            ace_config.get("moonraker_lane_sync_unknown_material_map_to", "") or ""
        ).strip()
        self.unknown_material_markers = self._parse_markers(
            ace_config.get("moonraker_lane_sync_unknown_material_markers", "???,unknown,n/a,none")
        )
        self._last_payload = None
        self._warned_unavailable = False

        # Worker thread state
        self._pending_sync = threading.Event()
        self._pending_force = False
        self._pending_reason = "manual"
        self._shutdown = threading.Event()
        self._lock = threading.Lock()

        # Start persistent worker thread
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="MoonrakerLaneSyncWorker",
            daemon=True
        )
        self._worker_thread.start()

    def shutdown(self):
        """Signal worker thread to exit and wait for it to finish.
        Gives the worker up to 3 seconds to complete any in-progress sync,
        then forcefully terminates via daemon thread cleanup.
        """
        self._shutdown.set()
        self._pending_sync.set()  # Wake up worker if waiting
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
            if self._worker_thread.is_alive():
                logging.warning("ACE: Moonraker sync worker did not exit cleanly")

    def _worker_loop(self):
        """Background worker that processes sync requests."""
        while not self._shutdown.is_set():
            # Wait for sync request or shutdown (check every 1s for shutdown)
            triggered = self._pending_sync.wait(timeout=1.0)
            if self._shutdown.is_set():
                break
            if not triggered:
                continue

            # Grab pending request parameters
            with self._lock:
                self._pending_sync.clear()
                force = self._pending_force
                reason = self._pending_reason
                self._pending_force = False
                self._pending_reason = "manual"

            # Perform the actual sync (blocking HTTP, but in background thread)
            # Check shutdown flag one more time before expensive operation
            if not self._shutdown.is_set():
                self._do_sync(force=force, reason=reason)

    def _is_printing_or_paused(self):
        """Check if printer is in a printing or paused state.

        Returns:
            bool: True if printing or paused, False otherwise
        """
        try:
            printer = getattr(self.manager, "printer", None)
            if not printer:
                return False
            print_stats = printer.lookup_object("print_stats", None)
            if not print_stats:
                return False
            reactor = getattr(self.manager, "reactor", None)
            if not reactor:
                return False
            stats = print_stats.get_status(reactor.monotonic())
            state = (stats.get("state") or "").lower()
            return state in ["printing", "paused"]
        except Exception:
            return False

    def sync_now(self, force=False, reason="manual"):
        """Queue a sync request to the background worker.

        This method returns immediately without blocking. The actual HTTP
        operations are performed by the worker thread.

        Args:
            force: If True, sync even during print and ignore debounce.
            reason: Descriptive reason for logging.

        Returns:
            bool: True if request was queued, False if disabled.
        """
        if not self.enabled:
            return False

        with self._lock:
            # Force flag is sticky - if any request wants force, we force
            self._pending_force = self._pending_force or force
            self._pending_reason = reason
            self._pending_sync.set()

        return True

    def _do_sync(self, force=False, reason="manual"):
        """Perform the actual sync (called by worker thread).

        This method does blocking HTTP I/O and should only be called
        from the background worker thread.
        """
        if not force and self._is_printing_or_paused():
            logging.debug("ACE: Skipping Moonraker lane sync during print (%s)", reason)
            return False

        lanes = self._build_lane_payload()
        serialized = json.dumps(lanes, sort_keys=True, separators=(",", ":"))
        if not force and serialized == self._last_payload:
            return False

        try:
            existing = self._get_namespace_items()
            # Remove malformed lane keys (e.g., from mocked tool offsets)
            invalid_keys = [
                key for key in existing.keys()
                if isinstance(key, str) and key.startswith("lane") and not self._is_lane_key(key)
            ]
            for key in invalid_keys:
                self._delete_item(key)
                existing.pop(key, None)
            if invalid_keys:
                msg = f"ACE: Removed invalid Moonraker lane keys: {', '.join(invalid_keys)}"
                logging.warning(msg)
                try:
                    self.gcode.respond_info(msg)
                except Exception:
                    pass
            # Clean up malformed lane keys (e.g., from mocked tool offsets)
            for key in list(existing.keys()):
                if key.startswith("lane") and not self._is_lane_key(key):
                    self._delete_item(key)
                    existing.pop(key, None)

            for key, value in lanes.items():
                if not force and existing.get(key) == value:
                    continue
                self._set_item(key, value)

            for key in existing.keys():
                if self._is_lane_key(key) and key not in lanes:
                    self._delete_item(key)
        except Exception as e:
            if not self._warned_unavailable:
                self._warned_unavailable = True
                try:
                    self.gcode.respond_info(
                        f"ACE: Moonraker lane sync unavailable ({e})"
                    )
                except Exception:
                    pass
            return False

        self._warned_unavailable = False
        self._last_payload = serialized
        logging.info("ACE: Moonraker lane sync updated (%s, lanes=%d)", reason, len(lanes))
        return True

    def _build_lane_payload(self):
        lanes = {}
        for instance in self.manager.instances:
            tool_offset = getattr(instance, "tool_offset", None)
            if not isinstance(tool_offset, int):
                # Skip unknown/mocked instances to avoid emitting invalid lane keys
                continue

            for local_slot in range(SLOTS_PER_ACE):
                lane_index = tool_offset + local_slot
                lane_key = f"lane{lane_index + 1}"

                inv = {}
                if local_slot < len(instance.inventory):
                    inv = instance.inventory[local_slot] or {}

                status = str(inv.get("status", "empty"))
                material = self._normalize_material(inv.get("material", ""))
                has_filament = status == "ready" and bool(material)

                entry = {
                    "lane": str(lane_index),
                    "material": material if has_filament else "",
                    "color": self._rgb_to_hex(inv.get("color")) if has_filament else "",
                    "scan_time": "",
                    "td": "",
                }

                nozzle_temp = self._safe_temp(inv.get("temp"))
                if nozzle_temp is not None:
                    entry["nozzle_temp"] = nozzle_temp

                bed_temp = self._extract_bed_temp(inv.get("hotbed_temp"))
                if bed_temp is not None:
                    entry["bed_temp"] = bed_temp

                spool_id = self._extract_spool_id(inv)
                if has_filament and inv.get("brand"):
                    entry["vendor"] = str(inv.get("brand"))
                if has_filament and inv.get("sku"):
                    entry["sku"] = str(inv.get("sku"))

                if has_filament and spool_id is not None:
                    entry["spool_id"] = spool_id

                lanes[lane_key] = entry

        return lanes

    @staticmethod
    def _parse_markers(raw):
        if raw is None:
            return set()
        if isinstance(raw, str):
            parts = raw.split(",")
        elif isinstance(raw, (list, tuple, set)):
            parts = list(raw)
        else:
            parts = [str(raw)]
        markers = set()
        for item in parts:
            marker = str(item or "").strip()
            if marker:
                markers.add(marker.upper())
        return markers

    def _normalize_material(self, material):
        value = str(material or "").strip()
        if not value:
            return ""
        mode = self.unknown_material_mode
        if mode not in ("passthrough", "empty", "map"):
            mode = "empty"
        if value.upper() not in self.unknown_material_markers:
            return value
        if mode == "empty":
            return ""
        if mode == "map":
            return self.unknown_material_map_to
        return value

    @staticmethod
    def _is_lane_key(key):
        if not isinstance(key, str):
            return False
        if not key.startswith("lane"):
            return False
        suffix = key[4:]
        return suffix.isdigit()

    @staticmethod
    def _safe_temp(value):
        try:
            temp = int(float(value))
            if temp > 0:
                return temp
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_bed_temp(hotbed_temp):
        if isinstance(hotbed_temp, dict):
            for key in ("max", "min"):
                value = hotbed_temp.get(key)
                try:
                    temp = int(float(value))
                    if temp > 0:
                        return temp
                except Exception:
                    continue
        return None

    @staticmethod
    def _extract_spool_id(inv):
        sku = inv.get("sku")
        if isinstance(sku, int):
            return sku
        if isinstance(sku, str) and sku.isdigit():
            try:
                return int(sku)
            except Exception:
                return None
        return None

    @staticmethod
    def _rgb_to_hex(rgb):
        if not isinstance(rgb, (list, tuple)) or len(rgb) < 3:
            return ""
        try:
            r = max(0, min(255, int(rgb[0])))
            g = max(0, min(255, int(rgb[1])))
            b = max(0, min(255, int(rgb[2])))
            return "#{:02X}{:02X}{:02X}".format(r, g, b)
        except Exception:
            return ""

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        return headers

    def _http_json(self, method, path, payload=None):
        url = self.base_url + path
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=data, headers=self._headers(), method=method)

        with request.urlopen(req, timeout=self.timeout_s) as resp:
            body = resp.read()
            if not body:
                return {}
            return json.loads(body.decode("utf-8", errors="replace"))

    def _get_namespace_items(self):
        query = parse.urlencode({"namespace": self.namespace})
        try:
            res = self._http_json("GET", f"/server/database/item?{query}")
        except error.HTTPError as e:
            if e.code == 404:
                return {}
            raise
        value = res.get("result", {}).get("value", {})
        return value if isinstance(value, dict) else {}

    def _set_item(self, key, value):
        payload = {"namespace": self.namespace, "key": key, "value": value}
        self._http_json("POST", "/server/database/item", payload=payload)

    def _delete_item(self, key):
        query = parse.urlencode({"namespace": self.namespace, "key": key})
        self._http_json("DELETE", f"/server/database/item?{query}")
