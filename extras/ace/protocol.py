"""Protocol adapters for ACE device communication.

Base adapter, shared types, factory functions, and protocol resolution live
here.  The concrete adapters are in ``protocol_ace1`` and ``protocol_ace2``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Tuple


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AceCommandSpec:
    """Describe a protocol command without binding callers to wire details."""

    name: str
    code: int | None
    tier: str
    request_type: str | None = None
    response_type: str | None = None


@dataclass(frozen=True)
class AceTransportSpec:
    """Describe how a protocol reaches physical devices."""

    mode: str
    port_description: str
    shared_bus: bool = False
    topology_validation: bool = True


DEFAULT_BAUD_BY_PROTOCOL = {
    "ace1_json": 115200,
    "ace2_proto": 230400,
}


# ---------------------------------------------------------------------------
# Protocol name resolution
# ---------------------------------------------------------------------------

def normalize_protocol_name(protocol_name: str | None) -> str:
    """Normalize config aliases to stable internal protocol names."""
    normalized = str(protocol_name or "auto").strip().lower()
    normalized = normalized.replace("-", "_").replace(" ", "_")

    aliases = {
        "auto": "auto",
        "ace1": "ace1_json",
        "ace1_json": "ace1_json",
        "json": "ace1_json",
        "ace2": "ace2_proto",
        "ace2_proto": "ace2_proto",
        "proto": "ace2_proto",
    }
    if normalized not in aliases:
        raise ValueError(f"Unsupported ACE protocol '{protocol_name}'")
    return aliases[normalized]


def _normalize_transport_description(description: str | None) -> str:
    """Normalize transport descriptions so matching survives spacing and punctuation."""
    return "".join(char for char in str(description or "").lower() if char.isalnum())


def transport_description_matches(expected_description: str, actual_description: str | None) -> bool:
    """Return True when one serial description matches one transport signature."""
    expected = _normalize_transport_description(expected_description)
    actual = _normalize_transport_description(actual_description)
    if not expected or not actual:
        return False

    if expected == "ace":
        return "ace" in actual and not actual.startswith("ace2")

    return expected in actual


def resolve_protocol_name(
    protocol_name: str | None,
    instance_num: int | None = None,
    available_port_descriptions: Iterable[str] | None = None,
) -> str:
    """Resolve the active protocol name from config.

    `auto` prefers dedicated ACE1 ports for lower instance numbers, then falls
    back to ACE2 shared-bus transport when an ACE2 adapter is present.
    """
    from .protocol_ace1 import AceJsonProtocolAdapter
    from .protocol_ace2 import AceProtoProtocolAdapter

    normalized = normalize_protocol_name(protocol_name)
    if normalized != "auto":
        return normalized

    if available_port_descriptions is None:
        return "ace1_json"

    ace1_transport = AceJsonProtocolAdapter().get_transport_spec()
    ace2_transport = AceProtoProtocolAdapter().get_transport_spec()
    ace1_ports = [
        description
        for description in available_port_descriptions
        if transport_description_matches(ace1_transport.port_description, description)
    ]
    ace2_present = any(
        transport_description_matches(ace2_transport.port_description, description)
        for description in available_port_descriptions
    )
    if instance_num is not None and instance_num < len(ace1_ports):
        return "ace1_json"
    if ace2_present:
        return "ace2_proto"

    return "ace1_json"


def create_protocol_adapter(protocol_name: str | None) -> "AceProtocolAdapter":
    """Create the configured protocol adapter."""
    from .protocol_ace1 import AceJsonProtocolAdapter
    from .protocol_ace2 import AceProtoProtocolAdapter

    active_protocol = resolve_protocol_name(protocol_name)
    if active_protocol == "ace1_json":
        return AceJsonProtocolAdapter()
    if active_protocol == "ace2_proto":
        return AceProtoProtocolAdapter()
    raise ValueError(f"Unsupported ACE protocol '{protocol_name}'")


def get_default_baud_for_protocol(protocol_name: str | None) -> int:
    """Return the default baud rate for the resolved protocol."""
    active_protocol = resolve_protocol_name(protocol_name)
    return DEFAULT_BAUD_BY_PROTOCOL[active_protocol]


# ---------------------------------------------------------------------------
# Base adapter (abstract interface)
# ---------------------------------------------------------------------------

class AceProtocolAdapter:
    """Base adapter for protocol-specific request construction."""

    def serialize_request_frame(self, request, crc_calculator) -> bytes:
        """Serialize one logical request into a transport frame."""
        raise NotImplementedError()

    def extract_responses(
        self,
        buffer: bytearray,
        crc_calculator,
    ) -> tuple[list[dict[str, Any]], bytearray, list[str]]:
        """Extract complete response objects from a raw serial buffer."""
        raise NotImplementedError()

    def build_discover_device_request(self) -> Dict[str, Any]:
        """Build a request that discovers devices on a shared transport bus."""
        raise NotImplementedError()

    def build_assign_device_id_request(
        self,
        uid1: int,
        uid2: int,
        uid3: int,
        device_id: int,
    ) -> Dict[str, Any]:
        """Build a request that assigns a bus address to a discovered device."""
        raise NotImplementedError()

    def get_transport_spec(self) -> AceTransportSpec:
        """Return how this protocol maps logical instances to physical transport."""
        raise NotImplementedError()

    def handle_bound_shared_bus_unsolicited(self, instance, response) -> bool:
        """Handle one unmatched shared-bus response already bound to one instance."""
        return False

    def build_debug_request(
        self,
        command_name: str,
        params: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Build a protocol-specific debug request."""
        raise NotImplementedError()

    def get_command_catalog(self) -> Tuple[AceCommandSpec, ...]:
        """Return the command catalog exposed by this protocol."""
        raise NotImplementedError()

    def normalize_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Return a transport-ready copy of a logical request."""
        raise NotImplementedError()

    def build_get_info_request(self) -> Dict[str, Any]:
        """Build a logical request for device information."""
        raise NotImplementedError()

    def build_get_status_request(self) -> Dict[str, Any]:
        """Build a logical request for device status."""
        raise NotImplementedError()

    def build_start_feed_assist_request(self, slot_index: int) -> Dict[str, Any]:
        """Build a request to enable feed assist for a slot."""
        raise NotImplementedError()

    def build_stop_feed_assist_request(self, slot_index: int) -> Dict[str, Any]:
        """Build a request to disable feed assist for a slot."""
        raise NotImplementedError()

    def feed_assist_causes_busy(self) -> bool:
        """Return True if activating feed assist transitions the device to a non-ready status.

        On ACE1 the device stays 'ready' while feed assist is active, so wait_ready()
        works normally before and after feed assist commands.
        On ACE2 the device transitions to 'busy' the moment feed assist starts and only
        returns to 'ready' when STOP_FEED_ASSIST is explicitly acknowledged.  Any
        wait_ready() issued while feed assist is active on ACE2 will therefore time out.
        """
        return False

    def build_feed_filament_request(
        self,
        slot: int,
        length: float,
        speed: float,
    ) -> Dict[str, Any]:
        """Build a request to feed filament from a slot."""
        raise NotImplementedError()

    def build_stop_feed_filament_request(self, slot: int) -> Dict[str, Any]:
        """Build a request to stop active filament feeding."""
        raise NotImplementedError()

    def build_unwind_filament_request(
        self,
        slot: int,
        length: float,
        speed: float,
    ) -> Dict[str, Any]:
        """Build a request to retract or unwind filament."""
        raise NotImplementedError()

    def build_stop_unwind_filament_request(self, slot: int) -> Dict[str, Any]:
        """Build a request to stop active retract or unwind motion."""
        raise NotImplementedError()

    def build_update_unwinding_speed_request(
        self,
        slot: int,
        speed: float,
    ) -> Dict[str, Any]:
        """Build a request to update retract speed."""
        raise NotImplementedError()

    def build_update_feeding_speed_request(
        self,
        slot: int,
        speed: float,
    ) -> Dict[str, Any]:
        """Build a request to update feed speed."""
        raise NotImplementedError()

    def build_get_filament_info_request(self, slot: int) -> Dict[str, Any]:
        """Build a request for full RFID and filament metadata."""
        raise NotImplementedError()

    def build_start_drying_request(
        self,
        temp: int,
        duration: int,
    ) -> Dict[str, Any]:
        """Build a request to start the dryer."""
        raise NotImplementedError()

    def build_stop_drying_request(self) -> Dict[str, Any]:
        """Build a request to stop the dryer."""
        raise NotImplementedError()
