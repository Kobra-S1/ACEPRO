"""ACE2 protobuf protocol adapter, command catalog, and wire helpers."""

from __future__ import annotations

from copy import deepcopy
import struct
from typing import Any, Dict, Mapping, Tuple

from .protocol import AceCommandSpec, AceProtocolAdapter, AceTransportSpec


# ---------------------------------------------------------------------------
# ACE2 command catalog
# ---------------------------------------------------------------------------

def _build_ace2_command_catalog() -> Tuple[AceCommandSpec, ...]:
    """Return the proto-derived ACE2 command catalog grouped by support tier."""
    return (
        AceCommandSpec("DISCOVER_DEVICE", 0, "diagnostic", response_type="DiscoverDeviceResponse"),
        AceCommandSpec("ASSIGN_DEVICE_ID", 1, "diagnostic", "AssignDeviceIdRequest", "GenericResponse"),
        AceCommandSpec("GET_STATUS", 6, "operational", response_type="StatusResponse"),
        AceCommandSpec("GET_INFO", 7, "operational", response_type="InfoResponse"),
        AceCommandSpec("FEED_OR_ROLLBACK", 8, "operational", "FeedOrRollbackRequest", "GenericResponse"),
        AceCommandSpec("STOP_FEED_OR_ROLLBACK", 9, "operational", "StopFeedOrRollbackRequest", "GenericResponse"),
        AceCommandSpec("UPDATE_SPEED", 10, "operational", "UpdateSpeedRequest", "GenericResponse"),
        AceCommandSpec("DRYING", 11, "operational", "DryingRequest", "GenericResponse"),
        AceCommandSpec("SET_DRY_TEMP", 12, "diagnostic", "SetDryTempRequest", "GenericResponse"),
        AceCommandSpec("GET_FILAMENT_INFO", 13, "operational", "RfidRequest", "FilamentInfoResponse"),
        AceCommandSpec("SET_RFID_ENABLE", 14, "diagnostic", "SetRfidEnableRequest", "GenericResponse"),
        AceCommandSpec("LINEAR_KEY_CALIBRATE", 15, "debug", "LinearCalibrationRequest", "GenericResponse"),
        AceCommandSpec("SET_FEED_CHECK", 19, "diagnostic", "SetFeedCheckRequest", "GenericResponse"),
        AceCommandSpec("GET_TEMP", 64, "diagnostic", response_type="GetTempResponse"),
        AceCommandSpec("SET_DRY_POWER", 65, "debug", "SetDryPowerRequest", "GenericResponse"),
        AceCommandSpec("SET_VALVE", 66, "debug", "SetValveRequest", "GenericResponse"),
        AceCommandSpec("FILAMENT_IDENTIFY", 68, "diagnostic", "RfidRequest", "GenericResponse"),
        AceCommandSpec("RFID_TEST", 69, "debug", "RfidTestRequest", "GenericResponse"),
        AceCommandSpec("FLASH_LED", 70, "debug", "FlashLedRequest", "GenericResponse"),
        AceCommandSpec("SET_FAN", 71, "debug", "SetFanRequest", "GenericResponse"),
        AceCommandSpec("SET_OUTPUT", 72, "debug", "SetOutputRequest", "GenericResponse"),
        AceCommandSpec("GET_KEY_STATE", 73, "diagnostic", response_type="KeyStateResponse"),
        AceCommandSpec("SET_PTC_TEMP", 75, "debug", "SetDryTempRequest", "GenericResponse"),
        AceCommandSpec("GET_FEED_INFO", 76, "diagnostic", response_type="FeedInfoResponse"),
    )


ACE2_COMMAND_CATALOG = _build_ace2_command_catalog()
ACE2_COMMANDS_BY_NAME = {spec.name: spec for spec in ACE2_COMMAND_CATALOG}
ACE2_COMMANDS_BY_CODE = {
    spec.code: spec for spec in ACE2_COMMAND_CATALOG if spec.code is not None
}
ACE2_GENERIC_RESPONSE_COMMANDS = {
    spec.name
    for spec in ACE2_COMMAND_CATALOG
    if spec.response_type == "GenericResponse"
}
ACE2_BOUND_GENERIC_ACK_COMMANDS = {
    spec.name
    for spec in ACE2_COMMAND_CATALOG
    if spec.response_type == "GenericResponse"
    and spec.tier != "debug"
    and spec.name != "ASSIGN_DEVICE_ID"
}

# ---------------------------------------------------------------------------
# ACE2 protocol constants
# ---------------------------------------------------------------------------

ACE2_FLAG_RESPONSE = 0x80
ACE2_FLAG_DEVICE_ID_MASK = 0x7F
ACE2_RESPONSE_CODE_NAMES = {
    0: "SUCCESS",
    1: "PARAM_ERROR",
    2: "FORBIDDEN",
    3: "FAILED",
    4: "ANTICOLLISION",
    5: "NOTAG",
    6: "READFAILED",
    400: "UNSUPPORTED",
}
ACE2_WORK_STATUS_BY_CODE = {
    0: "init",
    1: "ready",
    2: "busy",
    3: "upgrading",
}
ACE2_DRY_STATUS_BY_CODE = {
    0: "stop",
    1: "drying",
    2: "drying",
    3: "stop",
    4: "error",
    5: "error",
}
ACE2_DRY_STATE_DETAIL_BY_CODE = {
    0: "stop",
    1: "starting",
    2: "keeping",
    3: "stopping",
    4: "ptc_error",
    5: "ntc_error",
}
ACE2_SLOT_STATUS_BY_CODE = {
    0: "ready",
    1: "feeding",
    2: "unwinding",
    3: "shifting",
    4: "shifting",
    5: "preload",
    6: "upgrading",
    129: "gear_err",
    130: "gear_err",
    131: "gear_err",
    132: "gear_err",
    133: "gear_err",
    134: "gear_err",
    135: "gear_err",
}
ACE2_SLOT_STATUS_DETAIL_BY_CODE = {
    0: "ready",
    1: "feeding",
    2: "rollback",
    3: "assisting",
    4: "rollback_assisting",
    5: "preloading",
    6: "upgrading",
    129: "feed_error",
    130: "rollback_error",
    131: "assist_error",
    132: "preload_error",
    133: "stuck_error",
    134: "tangled_error",
    135: "motor_error",
}
ACE2_FILAMENT_TO_RFID_STATE = {
    0: 0,
    1: 1,
    2: 2,
    3: 3,
}

# ---------------------------------------------------------------------------
# Minimal protobuf encode / decode helpers
# ---------------------------------------------------------------------------


def _pb_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _pb_uint32(field: int, value: int) -> bytes:
    """Encode a uint32 protobuf field."""
    return _pb_varint((field << 3) | 0) + _pb_varint(value)


def _pb_bool(field: int, value: bool) -> bytes:
    """Encode a bool protobuf field."""
    return _pb_varint((field << 3) | 0) + _pb_varint(1 if value else 0)


def _pb_bytes(field: int, value: bytes) -> bytes:
    """Encode a bytes protobuf field."""
    return _pb_varint((field << 3) | 2) + _pb_varint(len(value)) + value


def _pb_string(field: int, value: str) -> bytes:
    """Encode a string protobuf field."""
    return _pb_bytes(field, value.encode())


def _pb_decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a protobuf varint from a byte buffer."""
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
    return result, pos


def _pb_decode(data: bytes) -> dict[int, list[tuple[int, Any]]]:
    """Decode a minimal protobuf payload used by ACE2 messages."""
    fields: dict[int, list[tuple[int, Any]]] = {}
    pos = 0
    while pos < len(data):
        tag, pos = _pb_decode_varint(data, pos)
        field_num, wire_type = tag >> 3, tag & 7
        if wire_type == 0:
            value, pos = _pb_decode_varint(data, pos)
        elif wire_type == 2:
            length, pos = _pb_decode_varint(data, pos)
            value = data[pos:pos + length]
            pos += length
        elif wire_type == 5:
            value = struct.unpack_from("<f", data, pos)[0] if pos + 4 <= len(data) else 0
            pos += 4
        else:
            break
        fields.setdefault(field_num, []).append((wire_type, value))
    return fields


def _pb_first(fields: dict[int, list[tuple[int, Any]]], field: int, default: Any = 0) -> Any:
    """Return the first decoded protobuf field value or a default."""
    return fields.get(field, [(0, default)])[0][1]


# ---------------------------------------------------------------------------
# ACE2 protocol adapter
# ---------------------------------------------------------------------------


class AceProtoProtocolAdapter(AceProtocolAdapter):
    """ACE2 adapter scaffold using command/payload requests for shared-bus transport."""

    def get_transport_spec(self) -> AceTransportSpec:
        """ACE2 reaches logical devices via shared RS-485 bus."""
        return AceTransportSpec(
            mode="rs485-bus",
            port_description="USB Single Serial",
            shared_bus=True,
            topology_validation=False,
        )

    def handle_bound_shared_bus_unsolicited(self, instance, response) -> bool:
        """Route one bound shared-bus response without leaking ACE2 commands upward."""
        command = response.get("command")
        if command == "GET_STATUS":
            instance._on_heartbeat_response(response)
            return True
        if command == "GET_INFO":
            instance.serial_mgr.handle_info_response(response)
            return True
        if command == "GET_FILAMENT_INFO":
            return bool(instance.handle_shared_bus_filament_info_response(response))
        if command in ACE2_BOUND_GENERIC_ACK_COMMANDS:
            return True
        return False

    def _build_command_request(
        self,
        command_name: str,
        params: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Build a logical ACE2 command request without binding to frame encoding yet."""
        normalized_name = command_name.strip().upper()
        if normalized_name not in ACE2_COMMANDS_BY_NAME:
            raise ValueError(f"Unsupported ACE2 command '{command_name}'")
        return {
            "command": normalized_name,
            "params": deepcopy(dict(params or {})),
        }

    def _encode_request_payload(self, command_name: str, params: Mapping[str, Any]) -> bytes:
        """Encode supported ACE2 request payloads as protobuf bytes."""
        if command_name in {
            "DISCOVER_DEVICE",
            "GET_INFO",
            "GET_STATUS",
            "GET_TEMP",
            "GET_KEY_STATE",
            "GET_FEED_INFO",
        }:
            return b""
        if command_name == "ASSIGN_DEVICE_ID":
            return (
                _pb_uint32(1, int(params["uid1"]))
                + _pb_uint32(2, int(params["uid2"]))
                + _pb_uint32(3, int(params["uid3"]))
                + _pb_uint32(4, int(params["device_id"]))
            )
        if command_name in {"GET_FILAMENT_INFO", "STOP_FEED_OR_ROLLBACK"}:
            return _pb_uint32(1, int(params["index"]))
        if command_name == "UPDATE_SPEED":
            return _pb_uint32(1, int(params["index"])) + _pb_uint32(2, int(params["speed"]))
        if command_name == "FEED_OR_ROLLBACK":
            return (
                _pb_uint32(1, int(params["index"]))
                + _pb_uint32(2, int(params["speed"]))
                + _pb_uint32(3, int(params["length"]))
                + _pb_uint32(4, int(params["mode"]))
            )
        if command_name == "DRYING":
            return (
                _pb_uint32(1, int(params["temp"]))
                + _pb_uint32(2, int(params["duration"]))
                + _pb_bool(3, bool(params.get("auto_roll", False)))
            )
        if command_name in {"SET_DRY_TEMP", "SET_PTC_TEMP"}:
            return _pb_uint32(1, int(params["temp"]))
        if command_name == "SET_RFID_ENABLE":
            return _pb_uint32(1, int(params["index"])) + _pb_bool(2, bool(params["enable"]))
        if command_name == "LINEAR_KEY_CALIBRATE":
            return _pb_uint32(1, int(params["id"])) + _pb_uint32(2, int(params["type"]))
        if command_name == "SET_FEED_CHECK":
            return (
                _pb_uint32(1, int(params["check_length"]))
                + _pb_uint32(2, int(params["error_length"]))
            )
        if command_name == "SET_DRY_POWER":
            return _pb_uint32(1, int(params["power"]))
        if command_name == "SET_VALVE":
            return _pb_bool(1, bool(params["valve1"])) + _pb_bool(2, bool(params["valve2"]))
        if command_name == "FILAMENT_IDENTIFY":
            return _pb_uint32(1, int(params["index"]))
        if command_name == "RFID_TEST":
            return _pb_bool(1, bool(params["enable"]))
        if command_name == "FLASH_LED":
            return (
                _pb_uint32(1, int(params["components"]))
                + _pb_uint32(2, int(params["loop"]))
                + _pb_uint32(3, int(params["quick1"]))
                + _pb_uint32(4, int(params["slow1"]))
                + _pb_uint32(5, int(params["quick2"]))
                + _pb_uint32(6, int(params["slow2"]))
            )
        if command_name == "SET_FAN":
            return (
                _pb_uint32(1, int(params["speed"]))
                + _pb_bool(2, bool(params["fan1"]))
                + _pb_bool(3, bool(params["fan2"]))
            )
        if command_name == "SET_OUTPUT":
            return _pb_uint32(1, int(params["components"])) + _pb_uint32(2, int(params["state"]))
        raise NotImplementedError(
            f"ACE2 payload encoding is not implemented yet for command '{command_name}'"
        )

    def _decode_response_payload(self, command_name: str, payload: bytes) -> dict[str, Any]:
        """Decode supported ACE2 response payloads into logical response dicts."""
        fields = _pb_decode(payload)
        if command_name == "DISCOVER_DEVICE":
            return {
                "uid1": _pb_first(fields, 1),
                "uid2": _pb_first(fields, 2),
                "uid3": _pb_first(fields, 3),
            }
        if command_name in ACE2_GENERIC_RESPONSE_COMMANDS:
            code = _pb_first(fields, 1, 0)
            return {
                "code": code,
                "msg": ACE2_RESPONSE_CODE_NAMES.get(code, str(code)),
            }
        if command_name == "GET_INFO":
            return {
                "code": 0,
                "msg": ACE2_RESPONSE_CODE_NAMES[0],
                "result": {
                    "version": _pb_first(fields, 1, b"").decode(errors="ignore"),
                    "boot_version": _pb_first(fields, 2, b"").decode(errors="ignore"),
                    "first_request": bool(_pb_first(fields, 3, 0)),
                    "raw_fields": fields,
                },
            }
        if command_name == "GET_STATUS":
            dry_status_payload = _pb_first(fields, 2, b"")
            dry_status_fields = _pb_decode(dry_status_payload) if dry_status_payload else {}
            work_state_code = _pb_first(fields, 1, 0)
            dry_state_code = _pb_first(dry_status_fields, 1, 0)
            slots = []
            for index, (_, slot_payload) in enumerate(fields.get(9, [])):
                slot_fields = _pb_decode(slot_payload) if slot_payload else {}
                slot_state = _pb_first(slot_fields, 1, 0)
                filament_state = _pb_first(slot_fields, 2, 0)
                normalized_slot_state = "empty" if filament_state == 0 else "ready"
                if filament_state != 0:
                    normalized_slot_state = ACE2_SLOT_STATUS_BY_CODE.get(
                        slot_state,
                        "gear_err" if slot_state >= 129 else "ready",
                    )
                slot_status_detail = "empty" if filament_state == 0 else ACE2_SLOT_STATUS_DETAIL_BY_CODE.get(
                    slot_state,
                    "unknown",
                )
                slots.append(
                    {
                        "index": index,
                        "status": normalized_slot_state,
                        "status_detail": slot_status_detail,
                        "status_code": slot_state,
                        "rfid": ACE2_FILAMENT_TO_RFID_STATE.get(filament_state, 0),
                    }
                )
            return {
                "code": 0,
                "msg": ACE2_RESPONSE_CODE_NAMES[0],
                "result": {
                    "status": ACE2_WORK_STATUS_BY_CODE.get(work_state_code, "unknown"),
                    "status_code": work_state_code,
                    "dryer_status": {
                        "status": ACE2_DRY_STATUS_BY_CODE.get(dry_state_code, "unknown"),
                        "state_detail": ACE2_DRY_STATE_DETAIL_BY_CODE.get(dry_state_code, "unknown"),
                        "state_code": dry_state_code,
                        "target_temp": _pb_first(dry_status_fields, 2, 0),
                        "duration": _pb_first(dry_status_fields, 3, 0),
                        "remain_time": _pb_first(dry_status_fields, 4, 0),
                    },
                    "temp": _pb_first(fields, 3, 0),
                    "humidity": _pb_first(fields, 4, 0),
                    "feed_assist_count": _pb_first(fields, 7, 0),
                    "cont_assist_time": _pb_first(fields, 8, 0),
                    "raw_fields": fields,
                    "slots": slots,
                },
            }
        if command_name == "GET_FILAMENT_INFO":
            extruder_payload = _pb_first(fields, 6, b"")
            extruder_fields = _pb_decode(extruder_payload) if extruder_payload else {}
            hotbed_payload = _pb_first(fields, 7, b"")
            hotbed_fields = _pb_decode(hotbed_payload) if hotbed_payload else {}
            colors = []
            for _, color_payload in fields.get(5, []):
                color_fields = _pb_decode(color_payload) if color_payload else {}
                rgba = int(_pb_first(color_fields, 1, 0))
                colors.append([
                    (rgba >> 24) & 0xFF,
                    (rgba >> 16) & 0xFF,
                    (rgba >> 8) & 0xFF,
                    rgba & 0xFF,
                ])
            code = _pb_first(fields, 12, 0)
            return {
                "code": code,
                "msg": ACE2_RESPONSE_CODE_NAMES.get(code, str(code)),
                "result": {
                    "index": _pb_first(fields, 1, 0),
                    "version": _pb_first(fields, 2, 0),
                    "sku": _pb_first(fields, 3, b"").decode(errors="ignore"),
                    "type": _pb_first(fields, 4, b"").decode(errors="ignore"),
                    "colors": colors,
                    "extruder_temp": {
                        "min": _pb_first(extruder_fields, 1, 0),
                        "max": _pb_first(extruder_fields, 2, 0),
                        "min_speed": _pb_first(extruder_fields, 3, 0),
                        "max_speed": _pb_first(extruder_fields, 4, 0),
                    },
                    "hotbed_temp": {
                        "min": _pb_first(hotbed_fields, 1, 0),
                        "max": _pb_first(hotbed_fields, 2, 0),
                    },
                    "diameter": _pb_first(fields, 8, 0),
                    "total": _pb_first(fields, 9, 0),
                    "icon_type": _pb_first(fields, 10, 0),
                    "current": _pb_first(fields, 11, 0),
                    "rfid": 2 if code == 0 else 0,
                },
            }
        return {"raw_fields": fields}

    def build_discover_device_request(self) -> Dict[str, Any]:
        """Build the ACE2 shared-bus discovery request."""
        return self._build_command_request("DISCOVER_DEVICE")

    def build_assign_device_id_request(
        self,
        uid1: int,
        uid2: int,
        uid3: int,
        device_id: int,
    ) -> Dict[str, Any]:
        """Build the ACE2 bus-address assignment request."""
        return self._build_command_request(
            "ASSIGN_DEVICE_ID",
            {
                "uid1": uid1,
                "uid2": uid2,
                "uid3": uid3,
                "device_id": device_id,
            },
        )

    def build_debug_request(
        self,
        command_name: str,
        params: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Build an ACE2 debug request using command names from the proto catalog."""
        return self._build_command_request(command_name, params)

    def _build_frame_flags(self, request: Mapping[str, Any]) -> int:
        """Encode ACE2 bus targeting into frame flags for addressed commands."""
        command_name = request["command"]
        if command_name in {"DISCOVER_DEVICE", "ASSIGN_DEVICE_ID"}:
            return 0

        target_device_id = int(request.get("target_device_id", 0))
        if target_device_id <= 0 or target_device_id > ACE2_FLAG_DEVICE_ID_MASK:
            raise ValueError(
                f"ACE2 target_device_id must be between 1 and {ACE2_FLAG_DEVICE_ID_MASK}"
            )
        return target_device_id & ACE2_FLAG_DEVICE_ID_MASK

    def serialize_request_frame(self, request, crc_calculator) -> bytes:
        """Serialize an ACE2 command request into the RS-485 bridge frame format."""
        command_name = request["command"]
        command_spec = ACE2_COMMANDS_BY_NAME[command_name]
        params = request.get("params", {})
        payload = self._encode_request_payload(command_name, params)
        request_id = int(request.get("id", 0))
        flags = self._build_frame_flags(request)
        inner = bytearray(
            [flags, request_id & 0xFF, (request_id >> 8) & 0xFF, command_spec.code or 0, len(payload)]
        )
        inner.extend(payload)
        crc = struct.pack("<H", crc_calculator(bytes(inner)))
        return b"\xFF\xAA" + bytes(inner) + crc + b"\xFE"

    def extract_responses(
        self,
        buffer: bytearray,
        crc_calculator,
    ) -> tuple[list[dict[str, Any]], bytearray, list[str]]:
        """Parse ACE2 framed responses from a shared-bus serial buffer."""
        responses: list[dict[str, Any]] = []
        notices: list[str] = []
        working = bytearray(buffer)

        while True:
            if len(working) < 10:
                break

            if not (working[0] == 0xFF and working[1] == 0xAA):
                header_idx = working.find(bytes([0xFF, 0xAA]))
                if header_idx == -1:
                    notices.append(f"Resync: dropped junk ({len(working)} bytes)")
                    working = bytearray()
                    break
                notices.append(f"Resync: skipping {header_idx} bytes")
                working = working[header_idx:]
                if len(working) < 10:
                    break

            payload_len = working[6]
            frame_len = 2 + 1 + 2 + 1 + 1 + payload_len + 2 + 1
            if len(working) < frame_len:
                break

            terminator_idx = 2 + 1 + 2 + 1 + 1 + payload_len + 2
            if working[terminator_idx] != 0xFE:
                next_header = working.find(bytes([0xFF, 0xAA]), 1)
                working = bytearray() if next_header == -1 else working[next_header:]
                notices.append("Invalid frame tail, resyncing")
                continue

            frame = bytes(working[:frame_len])
            working = bytearray(working[frame_len:])

            flags = frame[2]
            request_id = frame[3] | (frame[4] << 8)
            command_code = frame[5]
            payload = frame[7:7 + payload_len]
            crc_rx = frame[7 + payload_len:7 + payload_len + 2]
            crc_calc = struct.pack("<H", crc_calculator(frame[2:7 + payload_len]))
            if crc_rx != crc_calc:
                notices.append("Invalid CRC")
                continue

            command_spec = ACE2_COMMANDS_BY_CODE.get(command_code)
            command_name = command_spec.name if command_spec else f"CMD_{command_code}"
            decoded = self._decode_response_payload(command_name, payload)
            response = {"id": request_id, "command": command_name, "flags": flags}
            device_id = flags & ACE2_FLAG_DEVICE_ID_MASK
            if device_id:
                response["device_id"] = device_id
            if command_name == "DISCOVER_DEVICE":
                response["result"] = decoded
            else:
                response.update(decoded)
            responses.append(response)

        return responses, working, notices

    def get_command_catalog(self) -> Tuple[AceCommandSpec, ...]:
        """Return the proto-derived ACE2 command catalog."""
        return ACE2_COMMAND_CATALOG

    def normalize_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Return a copy of the logical ACE2 request until binary framing is added."""
        return deepcopy(request)

    def build_get_info_request(self) -> Dict[str, Any]:
        """Build the ACE2 get-info request."""
        return self._build_command_request("GET_INFO")

    def build_get_status_request(self) -> Dict[str, Any]:
        """Build the ACE2 get-status request."""
        return self._build_command_request("GET_STATUS")

    def build_start_feed_assist_request(self, slot_index: int) -> Dict[str, Any]:
        """Build the ACE2 feed-assist start request."""
        return self._build_command_request(
            "FEED_OR_ROLLBACK",
            {
                "index": slot_index,
                "speed": 10,
                "length": 0,
                "mode": 2,
            },
        )

    def build_stop_feed_assist_request(self, slot_index: int) -> Dict[str, Any]:
        """Build the ACE2 feed-assist stop request."""
        return self._build_command_request(
            "STOP_FEED_OR_ROLLBACK",
            {"index": slot_index},
        )

    def build_feed_filament_request(
        self,
        slot: int,
        length: float,
        speed: float,
    ) -> Dict[str, Any]:
        """Build the ACE2 feed request using FEED_OR_ROLLBACK mode placeholders."""
        return self._build_command_request(
            "FEED_OR_ROLLBACK",
            {
                "index": slot,
                "length": int(length),
                "speed": int(speed),
                "mode": 0,
            },
        )

    def build_stop_feed_filament_request(self, slot: int) -> Dict[str, Any]:
        """Build the ACE2 stop-feed request."""
        return self._build_command_request("STOP_FEED_OR_ROLLBACK", {"index": slot})

    def build_unwind_filament_request(
        self,
        slot: int,
        length: float,
        speed: float,
    ) -> Dict[str, Any]:
        """Build the ACE2 rollback request using FEED_OR_ROLLBACK mode placeholders."""
        return self._build_command_request(
            "FEED_OR_ROLLBACK",
            {
                "index": slot,
                "length": int(length),
                "speed": int(speed),
                "mode": 1,
            },
        )

    def build_stop_unwind_filament_request(self, slot: int) -> Dict[str, Any]:
        """Build the ACE2 stop-rollback request."""
        return self._build_command_request("STOP_FEED_OR_ROLLBACK", {"index": slot})

    def build_update_unwinding_speed_request(
        self,
        slot: int,
        speed: float,
    ) -> Dict[str, Any]:
        """Build the ACE2 speed update request for rollback operations."""
        return self._build_command_request(
            "UPDATE_SPEED",
            {"index": slot, "speed": int(speed)},
        )

    def build_update_feeding_speed_request(
        self,
        slot: int,
        speed: float,
    ) -> Dict[str, Any]:
        """Build the ACE2 speed update request for feed operations."""
        return self._build_command_request(
            "UPDATE_SPEED",
            {"index": slot, "speed": int(speed)},
        )

    def build_get_filament_info_request(self, slot: int) -> Dict[str, Any]:
        """Build the ACE2 RFID metadata request."""
        return self._build_command_request("GET_FILAMENT_INFO", {"index": slot})

    def build_start_drying_request(
        self,
        temp: int,
        duration: int,
    ) -> Dict[str, Any]:
        """Build the ACE2 start-drying request."""
        return self._build_command_request(
            "DRYING",
            {"temp": temp, "duration": duration, "auto_roll": False},
        )

    def build_stop_drying_request(self) -> Dict[str, Any]:
        """Build the ACE2 stop-drying request."""
        return self._build_command_request(
            "DRYING",
            {"temp": 0, "duration": 0, "auto_roll": False},
        )
