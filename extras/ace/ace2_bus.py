"""Shared-bus session scaffolding for ACE2 RS-485 transports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple


@dataclass(frozen=True)
class Ace2DeviceIdentity:
    """Stable ACE2 identity derived from the discovery UID triplet."""

    uid1: int
    uid2: int
    uid3: int

    @property
    def uid_tuple(self) -> Tuple[int, int, int]:
        """Return the UID as a tuple for deterministic sorting and lookup."""
        return (self.uid1, self.uid2, self.uid3)


@dataclass
class Ace2BusDevice:
    """Track one discovered ACE2 device on a shared RS-485 bus."""

    identity: Ace2DeviceIdentity
    logical_instance: int | None = None
    device_id: int | None = None


class Ace2BusSession:
    """Track discovered ACE2 devices and their logical-instance bindings."""

    def __init__(self, port: str, baud: int = 230400) -> None:
        self.port = port
        self.baud = baud
        self._devices_by_identity: Dict[Ace2DeviceIdentity, Ace2BusDevice] = {}
        self._identity_by_instance: Dict[int, Ace2DeviceIdentity] = {}
        # Identities that actually answered discovery in the current scan
        # cycle. Distinct from _devices_by_identity, which also holds
        # persisted-but-currently-absent units restored via
        # bind_persisted_instances() - those must not be treated as present
        # (they cannot be addressed, must not count as "ready", and must not
        # be handed a device id) until they answer discovery again.
        self._present_identities: set = set()

    def reset(self) -> None:
        """Clear runtime discovery and binding state before a fresh bus scan."""
        self._devices_by_identity.clear()
        self._identity_by_instance.clear()
        self._present_identities.clear()

    def record_discovered_device(self, uid1: int, uid2: int, uid3: int) -> Ace2BusDevice:
        """Add or return a discovered ACE2 device by UID.

        This only registers the identity; it does NOT mark the device present
        for this scan cycle (persisted-binding restore reuses this path). Use
        ``note_present_device`` for units that actually answered discovery.
        """
        identity = Ace2DeviceIdentity(uid1, uid2, uid3)
        device = self._devices_by_identity.get(identity)
        if device is None:
            device = Ace2BusDevice(identity=identity)
            self._devices_by_identity[identity] = device
        return device

    def note_present_device(self, uid1: int, uid2: int, uid3: int) -> Ace2BusDevice:
        """Register a unit that answered discovery this scan cycle."""
        device = self.record_discovered_device(uid1, uid2, uid3)
        self._present_identities.add(device.identity)
        return device

    def is_present(self, identity: Ace2DeviceIdentity) -> bool:
        """Return True if this identity answered discovery in the current cycle."""
        return identity in self._present_identities

    def iter_present_devices(self) -> Iterable[Ace2BusDevice]:
        """Yield devices that answered discovery this cycle, in UID order."""
        for identity in sorted(self._present_identities, key=lambda item: item.uid_tuple):
            yield self._devices_by_identity[identity]

    def bind_logical_instance(self, instance_num: int, uid1: int, uid2: int, uid3: int) -> Ace2BusDevice:
        """Bind a discovered ACE2 device to a logical ACE instance number."""
        device = self.record_discovered_device(uid1, uid2, uid3)
        previous_identity = self._identity_by_instance.get(instance_num)
        if previous_identity and previous_identity != device.identity:
            self._devices_by_identity[previous_identity].logical_instance = None
        device.logical_instance = instance_num
        self._identity_by_instance[instance_num] = device.identity
        return device

    def unbind_logical_instance(self, instance_num: int) -> None:
        """Remove a logical-instance binding.

        Used when an instance is handed back to a dedicated ACE1 transport
        during over-subscription self-heal. Clears both the instance→identity
        map and the device's back-reference, so the freed ACE2 unit becomes
        available to another logical instance again.
        """
        identity = self._identity_by_instance.pop(instance_num, None)
        if identity is None:
            return
        device = self._devices_by_identity.get(identity)
        if device is not None and device.logical_instance == instance_num:
            device.logical_instance = None

    def assign_device_id(self, uid1: int, uid2: int, uid3: int, device_id: int) -> Ace2BusDevice:
        """Store the assigned bus device id for a discovered ACE2 unit."""
        device = self.record_discovered_device(uid1, uid2, uid3)
        device.device_id = device_id
        return device

    def bind_persisted_instances(self, mapping: Dict[int, Tuple[int, int, int]]) -> None:
        """Restore logical-instance bindings from persisted UID mappings."""
        for instance_num, uid_tuple in sorted(mapping.items()):
            uid1, uid2, uid3 = uid_tuple
            self.bind_logical_instance(instance_num, uid1, uid2, uid3)

    def export_bindings(self) -> Dict[int, Tuple[int, int, int]]:
        """Export current logical-instance bindings as a serialisable mapping."""
        return {
            instance_num: identity.uid_tuple
            for instance_num, identity in sorted(self._identity_by_instance.items())
        }

    def get_device_for_instance(self, instance_num: int) -> Ace2BusDevice | None:
        """Return the bus device bound to a logical ACE instance."""
        identity = self._identity_by_instance.get(instance_num)
        if identity is None:
            return None
        return self._devices_by_identity.get(identity)

    def get_device_for_device_id(self, device_id: int) -> Ace2BusDevice | None:
        """Return the discovered ACE2 device currently using one bus device id."""
        for device in self._devices_by_identity.values():
            if device.device_id == device_id:
                return device
        return None

    def iter_discovered_devices(self) -> Iterable[Ace2BusDevice]:
        """Yield discovered devices in deterministic UID order."""
        for identity in sorted(self._devices_by_identity, key=lambda item: item.uid_tuple):
            yield self._devices_by_identity[identity]

    def build_assignment_plan(
        self,
        start_device_id: int = 1,
        present_only: bool = False,
    ) -> List[Ace2BusDevice]:
        """Assign device IDs to known devices lacking one, preferring bound instances first.

        When ``present_only`` is True, only units that answered discovery this
        cycle (see ``note_present_device``) are given a device id. A
        persisted-but-absent unit is then left without one so it does not count
        as "ready" - forcing the caller's discovery retry to keep hunting for
        it instead of silently treating a missing spool bay as available.
        """
        candidate_devices = self._devices_by_identity.values()
        if present_only:
            candidate_devices = [
                device for device in candidate_devices
                if device.identity in self._present_identities
            ]

        ordered_devices = sorted(
            candidate_devices,
            key=lambda device: (
                device.logical_instance is None,
                device.logical_instance if device.logical_instance is not None else 9999,
                device.identity.uid_tuple,
            ),
        )

        next_device_id = start_device_id
        for device in ordered_devices:
            if device.device_id is None:
                device.device_id = next_device_id
                next_device_id += 1
        return ordered_devices
