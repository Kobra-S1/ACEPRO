"""
Moonraker API extension for the ACE status/command surface.

Install:
  - Symlink this file into Moonraker's components dir, e.g.:
      ln -s /path/to/repo/ace_status_integration/moonraker/ace_status.py ~/moonraker/moonraker/components/ace_status.py
  - Add to moonraker.conf:
      [ace_status]
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from . import klippy_apis

    APIComp = klippy_apis.KlippyAPI


SAFE_COMMAND_RE = re.compile(r"^[A-Z0-9_]+$")
SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _sanitize_key(key: Any) -> Optional[str]:
    key_str = str(key).strip()
    if SAFE_KEY_RE.match(key_str):
        return key_str
    return None


def _sanitize_value(val: Any) -> str:
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    text = str(val)
    # Guard against line breaks or control characters in the G-code string
    return text.replace("\n", " ").replace("\r", " ").strip()


class AceStatus:
    def __init__(self, config: ConfigHelper):
        self.confighelper = config
        self.server = config.get_server()
        self.logger = logging.getLogger(__name__)

        # Get klippy_apis component
        self.klippy_apis: APIComp = self.server.lookup_component("klippy_apis")

        # Register API endpoints
        self.server.register_endpoint(
            "/server/ace/status", ["GET"], self.handle_status_request
        )
        self.server.register_endpoint(
            "/server/ace/slots", ["GET"], self.handle_slots_request
        )
        self.server.register_endpoint(
            "/server/ace/command", ["POST"], self.handle_command_request
        )

        # Subscribe to printer status updates
        self.server.register_event_handler(
            "server:status_update", self._handle_status_update
        )

        # Cache last known status
        self._last_status: Optional[Dict[str, Any]] = None

        self.logger.info("ACE Status API extension loaded")

    async def _query_ace_instances(self) -> Dict[str, Any]:
        """Query Moonraker for ace manager + all ace_instance_X objects."""
        ace_mgr: Optional[Dict[str, Any]] = None
        instance_data: Dict[int, Dict[str, Any]] = {}

        try:
            base = await self.klippy_apis.query_objects({"ace": None})
            if isinstance(base, dict) and isinstance(base.get("ace"), dict):
                ace_mgr = base.get("ace")
        except Exception as exc:
            self.logger.debug("query_objects (ace) failed: %s", exc)

        # Determine how many instances to fetch
        instance_count = 1
        if isinstance(ace_mgr, dict):
            try:
                instance_count = max(1, int(ace_mgr.get("ace_instances", 1)))
            except Exception:
                instance_count = 1

        # Build query for all instances + manager
        query = {f"ace_instance_{i}": None for i in range(instance_count)}
        query["ace"] = None

        try:
            all_data = await self.klippy_apis.query_objects(query)
            if isinstance(all_data, dict):
                if isinstance(all_data.get("ace"), dict):
                    ace_mgr = all_data.get("ace")
                for i in range(instance_count):
                    key = f"ace_instance_{i}"
                    if isinstance(all_data.get(key), dict):
                        instance_data[i] = all_data[key]
        except Exception as exc:
            self.logger.debug("query_objects (instances) failed: %s", exc)

        return {
            "manager": ace_mgr or {},
            "instances": instance_data,
            "count": instance_count,
        }

    async def handle_status_request(self, webrequest: WebRequest) -> Dict[str, Any]:
        """Handle ACE status request."""
        try:
            instance_idx = None
            try:
                # allow ?instance=1 to select a specific unit
                inst_str = webrequest.get_str("instance", None)
                if inst_str is not None:
                    instance_idx = int(inst_str)
            except Exception:
                instance_idx = None

            query_result = await self._query_ace_instances()
            ace_mgr = query_result["manager"]
            instances: Dict[int, Dict[str, Any]] = query_result["instances"]
            instance_count = query_result["count"]

            # Choose which instance to expose at top-level (default: current_index or 0)
            chosen_idx = 0
            if instance_idx is not None and instance_idx in instances:
                chosen_idx = instance_idx
            elif isinstance(ace_mgr, dict):
                try:
                    current_idx = int(ace_mgr.get("current_index", -1))
                    if current_idx in instances:
                        chosen_idx = current_idx
                except Exception:
                    pass

            ace_data = instances.get(chosen_idx)

            if ace_data and isinstance(ace_data, dict):
                payload = dict(ace_data)
                payload["instance_index"] = chosen_idx
                payload["instances"] = [
                    {"index": idx, **data} for idx, data in sorted(instances.items())
                ]
                payload["ace_manager"] = ace_mgr or {}
                payload["ace_instance_count"] = instance_count
                self._last_status = payload
                return payload

            if self._last_status:
                self.logger.debug("Returning cached ACE status")
                return self._last_status

            self.logger.warning("No ACE data available, returning default structure")
            return {
                "status": "unknown",
                "model": "Anycubic Color Engine Pro",
                "firmware": "Unknown",
                "dryer": {
                    "status": "stop",
                    "target_temp": 0,
                    "duration": 0,
                    "remain_time": 0,
                },
                "temp": 0,
                "fan_speed": 0,
                "enable_rfid": 0,
                "slots": [
                    {
                        "index": i,
                        "status": "unknown",
                        "type": "",
                        "color": [0, 0, 0],
                        "sku": "",
                        "rfid": 0,
                    }
                    for i in range(4)
                ],
            }

        except Exception as exc:  # noqa: BLE001
            self.logger.error("Error getting ACE status: %s", exc, exc_info=True)
            return {"error": str(exc)}

    async def handle_slots_request(self, webrequest: WebRequest) -> Dict[str, Any]:
        """Handle slot info request."""
        status = await self.handle_status_request(webrequest)
        if "error" in status:
            return status
        return {"slots": status.get("slots", [])}

    async def handle_command_request(self, webrequest: WebRequest) -> Dict[str, Any]:
        """Handle ACE command execution."""
        try:
            json_body: Optional[Dict[str, Any]] = None
            try:
                payload = await webrequest.get_json()
                if isinstance(payload, dict):
                    json_body = payload
            except Exception:
                json_body = None

            # Command extraction
            command = webrequest.get_str("command", None)
            if not command and json_body:
                command = json_body.get("command")  # type: ignore[arg-type]

            if not command:
                return {"error": "Command parameter is required"}

            command = str(command).strip().upper()
            if not SAFE_COMMAND_RE.match(command):
                return {"error": "Invalid command format"}

            params: Dict[str, Any] = {}

            # Params from JSON body
            if json_body and isinstance(json_body.get("params"), dict):
                params.update(json_body["params"])

            # Params from query string
            try:
                args = webrequest.get_args()
            except Exception:
                args = {}

            if args:
                qp_params = args.get("params")
                if qp_params:
                    if isinstance(qp_params, str):
                        try:
                            parsed = json.loads(qp_params)
                            if isinstance(parsed, dict):
                                params.update(parsed)
                        except Exception:
                            self.logger.debug(
                                "Ignoring unparsable params query string: %s", qp_params
                            )
                    elif isinstance(qp_params, dict):
                        params.update(qp_params)

                for key, value in args.items():
                    if key in ("command", "params"):
                        continue
                    params[key] = value

            # Build safe G-code command string
            formatted_params = []
            for key, value in params.items():
                safe_key = _sanitize_key(key)
                if not safe_key:
                    self.logger.debug("Skipping unsafe param key: %s", key)
                    continue
                formatted_params.append(f"{safe_key}={_sanitize_value(value)}")

            gcode_cmd = f"{command} {' '.join(formatted_params)}".strip()

            try:
                await self.klippy_apis.run_gcode(gcode_cmd)
                return {
                    "success": True,
                    "message": f"Command {command} executed successfully",
                    "command": gcode_cmd,
                }
            except Exception as exc:
                self.logger.error("Error executing ACE command %s: %s", gcode_cmd, exc)
                return {"success": False, "error": str(exc), "command": gcode_cmd}

        except Exception as exc:  # noqa: BLE001
            self.logger.error("Error handling ACE command request: %s", exc)
            return {"error": str(exc)}

    async def _handle_status_update(self, status: Dict[str, Any]) -> None:
        """Handle printer status updates."""
        try:
            ace_data = status.get("ace")
            if ace_data:
                self._last_status = ace_data
                self.server.send_event("ace:status_update", ace_data)
        except Exception as exc:
            self.logger.debug("Error handling status update: %s", exc)


def load_component(config: ConfigHelper) -> AceStatus:
    return AceStatus(config)
