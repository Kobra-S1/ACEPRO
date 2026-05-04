"""ACE Gen1 JSON protocol adapter."""

from __future__ import annotations

from copy import deepcopy
import json
import struct
from typing import Any, Dict, Mapping, Tuple

from .protocol import AceCommandSpec, AceProtocolAdapter, AceTransportSpec


class AceJsonProtocolAdapter(AceProtocolAdapter):
    """ACE Gen1 adapter using the current JSON method/params format."""

    def get_transport_spec(self) -> AceTransportSpec:
        """ACE1 uses one USB serial device per physical ACE unit."""
        return AceTransportSpec(
            mode="usb-topology",
            port_description="ACE",
            shared_bus=False,
            topology_validation=True,
        )

    def build_debug_request(
        self,
        command_name: str,
        params: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Build a JSON debug request while keeping callers transport-agnostic."""
        return {
            "method": command_name,
            "params": deepcopy(dict(params or {})),
        }

    def serialize_request_frame(self, request, crc_calculator) -> bytes:
        """Serialize an ACE1 JSON request into the current wire frame."""
        payload = json.dumps(request).encode("utf-8")
        data = bytearray([0xFF, 0xAA])
        data += struct.pack("<H", len(payload))
        data += payload
        data += struct.pack("<H", crc_calculator(payload))
        data += b"\xFE"
        return bytes(data)

    def extract_responses(
        self,
        buffer: bytearray,
        crc_calculator,
    ) -> tuple[list[dict[str, Any]], bytearray, list[str]]:
        """Parse ACE1 JSON frames from the input buffer."""
        responses: list[dict[str, Any]] = []
        notices: list[str] = []
        working = bytearray(buffer)

        while True:
            if len(working) < 7:
                break

            if not (working[0] == 0xFF and working[1] == 0xAA):
                header_idx = working.find(bytes([0xFF, 0xAA]))
                if header_idx == -1:
                    notices.append(f"Resync: dropped junk ({len(working)} bytes)")
                    working = bytearray()
                    break
                notices.append(f"Resync: skipping {header_idx} bytes")
                working = working[header_idx:]
                if len(working) < 7:
                    break

            payload_len = struct.unpack("<H", working[2:4])[0]
            frame_len = 2 + 2 + payload_len + 2 + 1
            if len(working) < frame_len:
                break

            terminator_idx = 4 + payload_len + 2
            if working[terminator_idx] != 0xFE:
                next_header = working.find(bytes([0xFF, 0xAA]), 1)
                working = bytearray() if next_header == -1 else working[next_header:]
                notices.append("Invalid frame tail, resyncing")
                continue

            frame = bytes(working[:frame_len])
            working = bytearray(working[frame_len:])
            payload = frame[4:4 + payload_len]
            crc_rx = frame[4 + payload_len:4 + payload_len + 2]
            crc_calc = struct.pack("<H", crc_calculator(payload))
            if crc_rx != crc_calc:
                notices.append("Invalid CRC")
                continue

            try:
                responses.append(json.loads(payload.decode("utf-8")))
            except (UnicodeDecodeError, ValueError) as exc:
                notices.append(f"JSON decode error: {exc}")

        return responses, working, notices

    def build_discover_device_request(self) -> Dict[str, Any]:
        """ACE1 does not support shared-bus discovery."""
        raise ValueError("ACE1 JSON protocol does not support device discovery")

    def build_assign_device_id_request(
        self,
        uid1: int,
        uid2: int,
        uid3: int,
        device_id: int,
    ) -> Dict[str, Any]:
        """ACE1 does not support shared-bus addressing."""
        raise ValueError("ACE1 JSON protocol does not support device ID assignment")

    def get_command_catalog(self) -> Tuple[AceCommandSpec, ...]:
        """Return the proto-derived ACE2 catalog for planning and tooling."""
        from .protocol_ace2 import ACE2_COMMAND_CATALOG
        return ACE2_COMMAND_CATALOG

    def normalize_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Return a deep copy so transport mutation does not affect callers."""
        return deepcopy(request)

    def build_get_info_request(self) -> Dict[str, Any]:
        """Build the current ACE1 get_info request."""
        return {"method": "get_info"}

    def build_get_status_request(self) -> Dict[str, Any]:
        """Build the current ACE1 get_status request."""
        return {"method": "get_status"}

    def build_start_feed_assist_request(self, slot_index: int) -> Dict[str, Any]:
        """Build the current ACE1 feed-assist enable request."""
        return {"method": "start_feed_assist", "params": {"index": slot_index}}

    def build_stop_feed_assist_request(self, slot_index: int) -> Dict[str, Any]:
        """Build the current ACE1 feed-assist disable request."""
        return {"method": "stop_feed_assist", "params": {"index": slot_index}}

    def build_feed_filament_request(
        self,
        slot: int,
        length: float,
        speed: float,
    ) -> Dict[str, Any]:
        """Build the current ACE1 feed request."""
        return {
            "method": "feed_filament",
            "params": {"index": slot, "length": length, "speed": speed},
        }

    def build_stop_feed_filament_request(self, slot: int) -> Dict[str, Any]:
        """Build the current ACE1 stop-feed request."""
        return {"method": "stop_feed_filament", "params": {"index": slot}}

    def build_unwind_filament_request(
        self,
        slot: int,
        length: float,
        speed: float,
    ) -> Dict[str, Any]:
        """Build the current ACE1 unwind request."""
        return {
            "method": "unwind_filament",
            "params": {"index": slot, "length": length, "speed": speed},
        }

    def build_stop_unwind_filament_request(self, slot: int) -> Dict[str, Any]:
        """Build the current ACE1 stop-unwind request."""
        return {"method": "stop_unwind_filament", "params": {"index": slot}}

    def build_update_unwinding_speed_request(
        self,
        slot: int,
        speed: float,
    ) -> Dict[str, Any]:
        """Build the current ACE1 retract-speed update request."""
        return {
            "method": "update_unwinding_speed",
            "params": {"index": slot, "speed": speed},
        }

    def build_update_feeding_speed_request(
        self,
        slot: int,
        speed: float,
    ) -> Dict[str, Any]:
        """Build the current ACE1 feed-speed update request."""
        return {
            "method": "update_feeding_speed",
            "params": {"index": slot, "speed": speed},
        }

    def build_get_filament_info_request(self, slot: int) -> Dict[str, Any]:
        """Build the current ACE1 RFID metadata request."""
        return {"method": "get_filament_info", "params": {"index": slot}}

    def build_start_drying_request(
        self,
        temp: int,
        duration: int,
    ) -> Dict[str, Any]:
        """Build the current ACE1 start-drying request."""
        return {"method": "drying", "params": {"temp": temp, "duration": duration}}

    def build_stop_drying_request(self) -> Dict[str, Any]:
        """Build the current ACE1 stop-drying request."""
        return {"method": "drying_stop"}
