"""Focused tests for protocol and ACE2 shared-bus scaffolding."""

import struct

import pytest

from ace.ace2_bus import Ace2BusSession
from ace.protocol import resolve_protocol_name, transport_description_matches
from ace.protocol_ace2 import ACE2_COMMAND_CATALOG, AceProtoProtocolAdapter


def _calc_crc(buffer):
    """Match the production CRC-16 implementation used on the wire."""
    crc = 0xFFFF
    for byte in buffer:
        data = byte
        data ^= crc & 0xFF
        data ^= (data & 0x0F) << 4
        crc = ((data << 8) | (crc >> 8)) ^ (data >> 4) ^ (data << 3)
    return crc


def _pb_varint(value):
    """Encode a protobuf varint for focused protocol-frame tests."""
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value & 0x7F)
    return bytes(encoded)


def _pb_uint(field, value):
    """Encode a uint32 protobuf field for test payload assembly."""
    return _pb_varint((field << 3) | 0) + _pb_varint(value)


def _pb_bool(field, value):
    """Encode a bool protobuf field for test payload assembly."""
    return _pb_uint(field, 1 if value else 0)


def _pb_bytes(field, payload):
    """Encode a nested protobuf message field for test payload assembly."""
    return _pb_varint((field << 3) | 2) + _pb_varint(len(payload)) + payload


class TestAceProtoProtocolAdapter:
    """Test the dormant ACE2 protocol scaffold."""

    def setup_method(self):
        self.adapter = AceProtoProtocolAdapter()

    def test_transport_spec_uses_shared_bus(self):
        transport = self.adapter.get_transport_spec()

        assert transport.shared_bus is True
        assert transport.topology_validation is False
        assert transport.mode == "rs485-bus"

    def test_build_discover_device_request(self):
        request = self.adapter.build_discover_device_request()

        assert request == {"command": "DISCOVER_DEVICE", "params": {}}

    def test_build_assign_device_id_request(self):
        request = self.adapter.build_assign_device_id_request(1, 2, 3, 4)

        assert request == {
            "command": "ASSIGN_DEVICE_ID",
            "params": {"uid1": 1, "uid2": 2, "uid3": 3, "device_id": 4},
        }

    def test_build_get_status_request(self):
        request = self.adapter.build_get_status_request()

        assert request == {"command": "GET_STATUS", "params": {}}

    def test_build_start_feed_assist_request(self):
        request = self.adapter.build_start_feed_assist_request(2)

        assert request == {
            "command": "FEED_OR_ROLLBACK",
            "params": {"index": 2, "speed": 10, "length": 0, "mode": 2},
        }

    def test_build_stop_feed_assist_request(self):
        request = self.adapter.build_stop_feed_assist_request(2)

        assert request == {
            "command": "STOP_FEED_OR_ROLLBACK",
            "params": {"index": 2},
        }

    def test_build_stop_drying_request(self):
        request = self.adapter.build_stop_drying_request()

        assert request == {
            "command": "DRYING",
            "params": {"temp": 0, "duration": 0, "auto_roll": False},
        }

    def test_build_debug_request_rejects_unknown_command(self):
        with pytest.raises(ValueError, match="Unsupported ACE2 command"):
            self.adapter.build_debug_request("NOT_REAL")

    def test_serialize_request_frame_uses_ace2_wire_format(self):
        request = self.adapter.build_discover_device_request()
        request["id"] = 5

        frame = self.adapter.serialize_request_frame(request, _calc_crc)

        assert frame == b"\xFF\xAA\x00\x05\x00\x00\x00" + struct.pack("<H", _calc_crc(b"\x00\x05\x00\x00\x00")) + b"\xFE"

    def test_serialize_request_frame_targets_assigned_device(self):
        request = self.adapter.build_get_status_request()
        request["id"] = 9
        request["target_device_id"] = 3

        frame = self.adapter.serialize_request_frame(request, _calc_crc)

        assert frame == b"\xFF\xAA\x03\x09\x00\x06\x00" + struct.pack("<H", _calc_crc(b"\x03\x09\x00\x06\x00")) + b"\xFE"

    def test_serialize_request_frame_rejects_unaddressed_runtime_command(self):
        request = self.adapter.build_get_status_request()
        request["id"] = 9

        with pytest.raises(ValueError, match="target_device_id must be between 1 and"):
            self.adapter.serialize_request_frame(request, _calc_crc)

    def test_extract_responses_decodes_discover_device_frame(self):
        payload = b"\x08\x0B\x10\x16\x18\x21"
        inner = b"\x80\x07\x00\x00\x06" + payload
        frame = b"\xFF\xAA" + inner + struct.pack("<H", _calc_crc(inner)) + b"\xFE"

        responses, remaining, notices = self.adapter.extract_responses(bytearray(frame), _calc_crc)

        assert notices == []
        assert remaining == bytearray()
        assert responses == [
            {
                "id": 7,
                "command": "DISCOVER_DEVICE",
                "flags": 0x80,
                "result": {"uid1": 11, "uid2": 22, "uid3": 33},
            }
        ]

    def test_extract_responses_normalizes_status_for_instance_callbacks(self):
        dry_status = _pb_uint(1, 2) + _pb_uint(2, 45) + _pb_uint(4, 90)
        slot_ready = _pb_uint(1, 0) + _pb_uint(2, 2)
        slot_feeding = _pb_uint(1, 1) + _pb_uint(2, 2)
        payload = (
            _pb_uint(1, 1)
            + _pb_bytes(2, dry_status)
            + _pb_uint(3, 31)
            + _pb_uint(4, 40)
            + _pb_uint(7, 3)
            + _pb_uint(8, 12)
            + _pb_bytes(9, slot_ready)
            + _pb_bytes(9, slot_feeding)
        )
        inner = b"\x80\x09\x00\x06" + bytes([len(payload)]) + payload
        frame = b"\xFF\xAA" + inner + struct.pack("<H", _calc_crc(inner)) + b"\xFE"

        responses, remaining, notices = self.adapter.extract_responses(bytearray(frame), _calc_crc)

        assert notices == []
        assert remaining == bytearray()
        assert responses == [
            {
                "id": 9,
                "command": "GET_STATUS",
                "flags": 0x80,
                "code": 0,
                "msg": "SUCCESS",
                "result": {
                    "status": "ready",
                    "status_code": 1,
                    "dryer_status": {
                        "status": "drying",
                        "state_detail": "keeping",
                        "state_code": 2,
                        "target_temp": 45,
                        "duration": 0,
                        "remain_time": 90,
                    },
                    "temp": 31,
                    "humidity": 40,
                    "feed_assist_count": 3,
                    "cont_assist_time": 12,
                    "slots": [
                        {
                            "index": 0,
                            "status": "ready",
                            "status_detail": "ready",
                            "status_code": 0,
                            "rfid": 2,
                        },
                        {
                            "index": 1,
                            "status": "feeding",
                            "status_detail": "feeding",
                            "status_code": 1,
                            "rfid": 2,
                        },
                    ],
                },
            }
        ]

    def test_extract_responses_status_uses_unknown_fallbacks(self):
        dry_status = _pb_uint(1, 99) + _pb_uint(2, 40)
        slot_unknown = _pb_uint(1, 140) + _pb_uint(2, 1)
        payload = (
            _pb_uint(1, 77)
            + _pb_bytes(2, dry_status)
            + _pb_bytes(9, slot_unknown)
        )
        inner = b"\x80\x09\x00\x06" + bytes([len(payload)]) + payload
        frame = b"\xFF\xAA" + inner + struct.pack("<H", _calc_crc(inner)) + b"\xFE"

        responses, remaining, notices = self.adapter.extract_responses(bytearray(frame), _calc_crc)

        assert notices == []
        assert remaining == bytearray()
        assert responses == [
            {
                "id": 9,
                "command": "GET_STATUS",
                "flags": 0x80,
                "code": 0,
                "msg": "SUCCESS",
                "result": {
                    "status": "unknown",
                    "status_code": 77,
                    "dryer_status": {
                        "status": "unknown",
                        "state_detail": "unknown",
                        "state_code": 99,
                        "target_temp": 40,
                        "duration": 0,
                        "remain_time": 0,
                    },
                    "temp": 0,
                    "humidity": 0,
                    "feed_assist_count": 0,
                    "cont_assist_time": 0,
                    "slots": [
                        {
                            "index": 0,
                            "status": "gear_err",
                            "status_detail": "unknown",
                            "status_code": 140,
                            "rfid": 1,
                        }
                    ],
                },
            }
        ]

    def test_extract_responses_normalizes_generic_response_code(self):
        payload = _pb_uint(1, 2)
        inner = b"\x80\x0C\x00\x01" + bytes([len(payload)]) + payload
        frame = b"\xFF\xAA" + inner + struct.pack("<H", _calc_crc(inner)) + b"\xFE"

        responses, _, _ = self.adapter.extract_responses(bytearray(frame), _calc_crc)

        assert responses == [
            {
                "id": 12,
                "command": "ASSIGN_DEVICE_ID",
                "flags": 0x80,
                "code": 2,
                "msg": "FORBIDDEN",
            }
        ]

    def test_extract_responses_keeps_target_device_id_from_flags(self):
        payload = _pb_uint(1, 0)
        inner = b"\x83\x11\x00\x01" + bytes([len(payload)]) + payload
        frame = b"\xFF\xAA" + inner + struct.pack("<H", _calc_crc(inner)) + b"\xFE"

        responses, _, _ = self.adapter.extract_responses(bytearray(frame), _calc_crc)

        assert responses == [
            {
                "id": 17,
                "command": "ASSIGN_DEVICE_ID",
                "flags": 0x83,
                "device_id": 3,
                "code": 0,
                "msg": "SUCCESS",
            }
        ]

    def test_serialize_request_frame_supports_all_catalog_commands(self):
        command_params = {
            "FEED_OR_ROLLBACK": {"index": 0, "speed": 10, "length": 5, "mode": 0},
            "STOP_FEED_OR_ROLLBACK": {"index": 0},
            "UPDATE_SPEED": {"index": 0, "speed": 15},
            "DRYING": {"temp": 45, "duration": 30, "auto_roll": False},
            "SET_DRY_TEMP": {"temp": 45},
            "GET_FILAMENT_INFO": {"index": 0},
            "SET_RFID_ENABLE": {"index": 0, "enable": True},
            "LINEAR_KEY_CALIBRATE": {"id": 0, "type": 1},
            "SET_FEED_CHECK": {"check_length": 200, "error_length": 30},
            "SET_DRY_POWER": {"power": 1},
            "SET_VALVE": {"valve1": True, "valve2": False},
            "FILAMENT_IDENTIFY": {"index": 0},
            "RFID_TEST": {"enable": True},
            "FLASH_LED": {
                "components": 1,
                "loop": 2,
                "quick1": 3,
                "slow1": 4,
                "quick2": 5,
                "slow2": 6,
            },
            "SET_FAN": {"speed": 80, "fan1": True, "fan2": False},
            "SET_OUTPUT": {"components": 3, "state": 1},
            "SET_PTC_TEMP": {"temp": 50},
        }

        request_id = 1
        for spec in ACE2_COMMAND_CATALOG:
            if spec.name == "DISCOVER_DEVICE":
                request = self.adapter.build_discover_device_request()
            elif spec.name == "ASSIGN_DEVICE_ID":
                request = self.adapter.build_assign_device_id_request(1, 2, 3, 4)
            else:
                request = self.adapter.build_debug_request(
                    spec.name,
                    command_params.get(spec.name, {}),
                )

            request["id"] = request_id
            request_id += 1
            if request["command"] not in {"DISCOVER_DEVICE", "ASSIGN_DEVICE_ID"}:
                request["target_device_id"] = 1

            frame = self.adapter.serialize_request_frame(request, _calc_crc)
            assert frame.startswith(b"\xFF\xAA")
            assert frame.endswith(b"\xFE")


class TestAce2BusSession:
    """Test shared-bus device tracking for ACE2 scaffolding."""

    def test_bind_and_lookup_instance(self):
        session = Ace2BusSession(port="/dev/ttyUSB0")

        session.bind_logical_instance(2, 11, 22, 33)
        device = session.get_device_for_instance(2)

        assert device is not None
        assert device.logical_instance == 2
        assert device.identity.uid_tuple == (11, 22, 33)

    def test_assignment_plan_prefers_bound_instances(self):
        session = Ace2BusSession(port="/dev/ttyUSB0")
        session.record_discovered_device(30, 30, 30)
        session.bind_logical_instance(1, 10, 10, 10)

        plan = session.build_assignment_plan(start_device_id=7)

        assert [device.device_id for device in plan] == [7, 8]
        assert [device.identity.uid_tuple for device in plan] == [
            (10, 10, 10),
            (30, 30, 30),
        ]

    def test_reset_clears_runtime_state(self):
        session = Ace2BusSession(port="/dev/ttyUSB0")
        session.bind_logical_instance(1, 10, 20, 30)
        session.assign_device_id(10, 20, 30, 7)

        session.reset()

        assert list(session.iter_discovered_devices()) == []
        assert session.get_device_for_instance(1) is None


class TestTransportDescriptionMatching:
    """Test transport description matching and protocol auto-resolution."""

    def test_ace2_transport_matches_usb_single_serial_alias(self):
        assert transport_description_matches("ACE2-USB-RS485", "USB Single Serial") is True

    def test_auto_protocol_resolves_to_ace2_for_usb_single_serial(self):
        resolved = resolve_protocol_name(
            "auto",
            instance_num=0,
            available_port_descriptions=["USB Single Serial"],
        )

        assert resolved == "ace2_proto"
