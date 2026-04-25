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

    def reset(self) -> None:
        """Clear runtime discovery and binding state before a fresh bus scan."""
        self._devices_by_identity.clear()
        self._identity_by_instance.clear()

    def record_discovered_device(self, uid1: int, uid2: int, uid3: int) -> Ace2BusDevice:
        """Add or return a discovered ACE2 device by UID."""
        identity = Ace2DeviceIdentity(uid1, uid2, uid3)
        device = self._devices_by_identity.get(identity)
        if device is None:
            device = Ace2BusDevice(identity=identity)
            self._devices_by_identity[identity] = device
        return device

    def bind_logical_instance(self, instance_num: int, uid1: int, uid2: int, uid3: int) -> Ace2BusDevice:
        """Bind a discovered ACE2 device to a logical ACE instance number."""
        device = self.record_discovered_device(uid1, uid2, uid3)
        previous_identity = self._identity_by_instance.get(instance_num)
        if previous_identity and previous_identity != device.identity:
            self._devices_by_identity[previous_identity].logical_instance = None
        device.logical_instance = instance_num
        self._identity_by_instance[instance_num] = device.identity
        return device

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

    def build_assignment_plan(self, start_device_id: int = 1) -> List[Ace2BusDevice]:
        """Assign device IDs to known devices lacking one, preferring bound instances first."""
        ordered_devices = sorted(
            self._devices_by_identity.values(),
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
