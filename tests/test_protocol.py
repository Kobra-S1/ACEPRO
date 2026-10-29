"""Focused tests for protocol and ACE2 shared-bus scaffolding."""

import json
import struct

import pytest

from ace.ace2_bus import Ace2BusSession
from ace.protocol import resolve_protocol_name, transport_description_matches
from ace.protocol_ace1 import AceJsonProtocolAdapter
from ace.protocol_ace2 import (
    ACE2_COMMAND_CATALOG,
    AceProtoProtocolAdapter,
    _pb_bytes as ace2_pb_bytes,
    _pb_decode,
    _pb_uint32 as ace2_pb_uint32,
)


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

    def test_extract_responses_decodes_get_info_raw_fields(self):
        payload = _pb_bytes(1, b"1.2.3") + _pb_bytes(2, b"0.9.1") + _pb_bool(3, True)
        inner = b"\x80\x08\x00\x07" + bytes([len(payload)]) + payload
        frame = b"\xFF\xAA" + inner + struct.pack("<H", _calc_crc(inner)) + b"\xFE"

        responses, remaining, notices = self.adapter.extract_responses(bytearray(frame), _calc_crc)

        assert notices == []
        assert remaining == bytearray()
        assert responses == [
            {
                "id": 8,
                "command": "GET_INFO",
                "flags": 0x80,
                "code": 0,
                "msg": "SUCCESS",
                "result": {
                    "version": "1.2.3",
                    "boot_version": "0.9.1",
                    "first_request": True,
                    "raw_fields": {
                        1: [(2, b"1.2.3")],
                        2: [(2, b"0.9.1")],
                        3: [(0, 1)],
                    },
                },
            }
        ]

    def test_extract_responses_decodes_get_feed_info(self):
        # Slot 0: a 723mm retract measured as 733mm delivered (negative = retract),
        # observed on hardware. Slot 1: a 100mm feed measured as 100mm.
        slot0 = (
            _pb_uint(1, 8928)
            + _pb_uint(2, 723)
            + _pb_uint(3, (1 << 64) - 733)  # two's-complement -733
        )
        slot1 = _pb_uint(1, 1237) + _pb_uint(2, 100) + _pb_uint(3, 100)
        payload = _pb_bytes(1, slot0) + _pb_bytes(1, slot1)
        inner = b"\x80\x0B\x00\x4C" + bytes([len(payload)]) + payload
        frame = b"\xFF\xAA" + inner + struct.pack("<H", _calc_crc(inner)) + b"\xFE"

        responses, remaining, notices = self.adapter.extract_responses(bytearray(frame), _calc_crc)

        assert notices == []
        assert remaining == bytearray()
        assert responses == [
            {
                "id": 11,
                "command": "GET_FEED_INFO",
                "flags": 0x80,
                "code": 0,
                "msg": "SUCCESS",
                "result": {
                    "slots": [
                        {"motor_counts": 8928, "magnitude_mm": 723, "moved_mm": -733},
                        {"motor_counts": 1237, "magnitude_mm": 100, "moved_mm": 100},
                    ]
                },
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
                    "action": "feeding",
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
                    "cont_assist_time": 0.012,
                    "raw_fields": {
                        1: [(0, 1)],
                        2: [(2, b"\x08\x02\x10- Z")],
                        3: [(0, 31)],
                        4: [(0, 40)],
                        7: [(0, 3)],
                        8: [(0, 12)],
                        9: [(2, b"\x08\x00\x10\x02"), (2, b"\x08\x01\x10\x02")],
                    },
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

    def test_dryer_duration_is_reported_in_minutes_like_ace1(self):
        # Captured from an ACE 2 (fw V1.1.31) after ACE_START_DRYING
        # DURATION=30: it echoes the duration in seconds, remain_time in
        # seconds as well.
        dry_status = _pb_uint(1, 2) + _pb_uint(2, 40) + _pb_uint(3, 1800) + _pb_uint(4, 1793)
        payload = _pb_uint(1, 1) + _pb_bytes(2, dry_status)
        inner = b"\x80\x09\x00\x06" + bytes([len(payload)]) + payload
        frame = b"\xFF\xAA" + inner + struct.pack("<H", _calc_crc(inner)) + b"\xFE"

        responses, _, _ = self.adapter.extract_responses(bytearray(frame), _calc_crc)

        dryer = responses[0]["result"]["dryer_status"]
        assert dryer["duration"] == 30
        assert dryer["remain_time"] == 1793

    def _filament_info_frame(self, payload):
        inner = b"\x80\x0b\x00\x0d" + bytes([len(payload)]) + payload
        return b"\xFF\xAA" + inner + struct.pack("<H", _calc_crc(inner)) + b"\xFE"

    def test_a_filament_info_reply_with_a_tag_is_identified(self):
        extruder = _pb_uint(1, 190) + _pb_uint(2, 230)
        payload = (
            _pb_uint(1, 2)
            + _pb_bytes(3, b"AHPLBK-101")
            + _pb_bytes(4, b"PLA")
            + _pb_bytes(6, extruder)
            + _pb_uint(12, 0)
        )

        responses, _, notices = self.adapter.extract_responses(
            bytearray(self._filament_info_frame(payload)), _calc_crc)

        assert notices == []
        result = responses[0]["result"]
        assert (result["sku"], result["type"], result["rfid"]) == ("AHPLBK-101", "PLA", 2)

    def test_a_filament_info_reply_keeps_its_raw_fields_for_debugging(self):
        # Fields the decoder does not map (a brand, a colour in another
        # number) stay visible to ACE_DEBUG, so a box's reply can be read
        # off the printer instead of guessed.
        payload = (_pb_uint(1, 2) + _pb_bytes(3, b"AHPLBK-101")
                   + _pb_bytes(13, b"AC") + _pb_uint(12, 0))

        responses, _, _ = self.adapter.extract_responses(
            bytearray(self._filament_info_frame(payload)), _calc_crc)

        raw = responses[0]["result"]["raw_fields"]
        assert raw[13][0][1] == b"AC", raw

    def test_a_filament_info_reply_without_a_tag_is_not_identified(self):
        # An ACE2 answers the query for a tagless spool with success and
        # every field empty; that is no identification.
        payload = _pb_uint(1, 2) + _pb_uint(12, 0)

        responses, _, notices = self.adapter.extract_responses(
            bytearray(self._filament_info_frame(payload)), _calc_crc)

        assert notices == []
        assert responses[0]["code"] == 0
        assert responses[0]["result"]["rfid"] == 0

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
                    "action": "none",
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
                    "cont_assist_time": 0.0,
                    "raw_fields": {
                        1: [(0, 77)],
                        2: [(2, b"\x08c\x10(")],
                        9: [(2, b"\x08\x8c\x01\x10\x01")],
                    },
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

    def _status_action(self, *slot_state_codes):
        """Decode a GET_STATUS frame whose loaded slots carry the given states."""
        payload = _pb_uint(1, 1)
        for code in slot_state_codes:
            payload += _pb_bytes(9, _pb_uint(1, code) + _pb_uint(2, 2))
        inner = b"\x80\x09\x00\x06" + bytes([len(payload)]) + payload
        frame = b"\xFF\xAA" + inner + struct.pack("<H", _calc_crc(inner)) + b"\xFE"
        responses, _, _ = self.adapter.extract_responses(bytearray(frame), _calc_crc)
        return responses[0]["result"]["action"]

    def test_status_action_is_none_while_every_slot_is_idle(self):
        assert self._status_action(0, 0) == "none"

    def test_status_action_reports_the_unwinding_slot(self):
        assert self._status_action(0, 2) == "unwinding"

    def test_status_action_ignores_an_assist_hold(self):
        assert self._status_action(0, 3) == "none"

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
            elif spec.name == "IAP_UPGRADE":
                request = self.adapter.build_iap_upgrade_request(64, 0x1234, "V1.0.0")
            elif spec.name == "IAP_FIRMWARE":
                request = self.adapter.build_iap_firmware_request(0x08024000, bytes(64))
            elif spec.name == "IAP_UPGRADE_FINISH":
                request = self.adapter.build_iap_finish_request()
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

    def test_assignment_plan_skips_ids_already_held(self):
        session = Ace2BusSession(port="/dev/ttyUSB0")
        session.bind_logical_instance(0, 10, 10, 10)
        session.assign_device_id(10, 10, 10, 1)
        session.note_present_device(20, 20, 20)

        plan = session.build_assignment_plan()

        newcomer = session.record_discovered_device(20, 20, 20)
        assert newcomer.device_id is not None
        assert newcomer.device_id != 1
        assert sorted(device.device_id for device in plan) == [1, newcomer.device_id]

    def test_reset_clears_runtime_state(self):
        session = Ace2BusSession(port="/dev/ttyUSB0")
        session.bind_logical_instance(1, 10, 20, 30)
        session.assign_device_id(10, 20, 30, 7)

        session.reset()

        assert list(session.iter_discovered_devices()) == []
        assert session.get_device_for_instance(1) is None

    def test_unbind_logical_instance_frees_unit_without_touching_others(self):
        session = Ace2BusSession(port="/dev/ttyUSB0")
        session.bind_logical_instance(0, 11, 22, 33)
        session.bind_logical_instance(1, 44, 55, 66)

        session.unbind_logical_instance(0)

        # instance 0's binding is gone and its unit is available again
        assert session.get_device_for_instance(0) is None
        assert session.record_discovered_device(11, 22, 33).logical_instance is None
        # instance 1 is untouched
        assert session.get_device_for_instance(1).identity.uid_tuple == (44, 55, 66)
        assert session.export_bindings() == {1: (44, 55, 66)}

    def test_unbind_unknown_instance_is_noop(self):
        session = Ace2BusSession(port="/dev/ttyUSB0")
        session.bind_logical_instance(1, 44, 55, 66)

        session.unbind_logical_instance(5)  # never bound -> no error, no change

        assert session.export_bindings() == {1: (44, 55, 66)}


class TestTransportDescriptionMatching:
    """Test transport description matching and protocol auto-resolution."""

    def test_ace2_transport_matches_usb_single_serial(self):
        assert transport_description_matches("USB Single Serial", "USB Single Serial") is True

    def test_auto_protocol_resolves_to_ace2_for_usb_single_serial(self):
        resolved = resolve_protocol_name(
            "auto",
            instance_num=0,
            available_port_descriptions=["USB Single Serial"],
        )

        assert resolved == "ace2_proto"

    def test_auto_protocol_resolves_to_ace2_for_real_device_description(self):
        # Real-world PySerial description for QinHeng CH343 USB-RS485 adapter
        # as enumerated on Linux: lsusb shows "USB Single Serial" (VID=1A86:PID=55D3)
        resolved = resolve_protocol_name(
            "auto",
            instance_num=0,
            available_port_descriptions=["USB Single Serial"],
        )

        assert resolved == "ace2_proto"

    def test_auto_protocol_resolves_mixed_ace1_and_ace2_by_instance(self):
        """Mixed topology should map instance 0 to ACE1 and instance 1 to ACE2."""
        available = ["ACE", "USB Single Serial"]

        resolved_instance0 = resolve_protocol_name(
            "auto",
            instance_num=0,
            available_port_descriptions=available,
        )
        resolved_instance1 = resolve_protocol_name(
            "auto",
            instance_num=1,
            available_port_descriptions=available,
        )

        assert resolved_instance0 == "ace1_json"
        assert resolved_instance1 == "ace2_proto"


class TestAceProtoResyncPreservesFrameBoundaries:
    """A corrupted frame must not take the *next* reply down with it.

    ``read()`` returns whatever bytes happen to have arrived, so a frame
    header (0xFF 0xAA) is regularly split across two reads.  Both resync
    paths in ``extract_responses`` discard the entire working buffer when no
    complete header is found, which eats the leading 0xFF of a frame that is
    still arriving - and the following, perfectly valid, response is then
    unparseable too.

    On the wire this is what turns one corruption event into two lost
    requests: klippy logs show adjacent-id timeout pairs (20 of 41 timeouts
    in one session) each immediately preceded by a resync notice.
    """

    def setup_method(self):
        self.adapter = AceProtoProtocolAdapter()

    @staticmethod
    def _discover_frame(request_id):
        """Build a valid DISCOVER_DEVICE response frame."""
        payload = b"\x08\x0B\x10\x16\x18\x21"
        inner = bytes(
            [0x80, request_id & 0xFF, (request_id >> 8) & 0xFF, 0x00, len(payload)]
        ) + payload
        return b"\xFF\xAA" + inner + struct.pack("<H", _calc_crc(inner)) + b"\xFE"

    def test_invalid_tail_keeps_split_header_byte(self):
        """A frame with a destroyed terminator must not consume the first
        byte of the next frame's header."""
        corrupt = bytearray(self._discover_frame(request_id=8))
        corrupt[-1] = 0x00  # destroy the 0xFE terminator

        # Only the first byte of the next frame's header has arrived so far.
        responses, remaining, notices = self.adapter.extract_responses(
            bytearray(bytes(corrupt) + b"\xFF"), _calc_crc
        )
        assert responses == []
        assert any("resync" in notice.lower() for notice in notices)

        # Second read delivers the rest of a perfectly valid frame.
        good = self._discover_frame(request_id=9)
        responses, _, _ = self.adapter.extract_responses(
            remaining + bytearray(good[1:]), _calc_crc
        )
        assert [response["id"] for response in responses] == [9]

    def test_junk_scan_keeps_split_header_byte(self):
        """Dropping unparseable junk must likewise preserve a trailing 0xFF
        that starts the next frame."""
        junk = bytes(range(0x10, 0x30))  # 32 bytes, contains no 0xFF 0xAA

        responses, remaining, notices = self.adapter.extract_responses(
            bytearray(junk + b"\xFF"), _calc_crc
        )
        assert responses == []
        assert any("junk" in notice.lower() for notice in notices)

        good = self._discover_frame(request_id=11)
        responses, _, _ = self.adapter.extract_responses(
            remaining + bytearray(good[1:]), _calc_crc
        )
        assert [response["id"] for response in responses] == [11]

    def test_invalid_tail_still_recovers_a_complete_following_frame(self):
        """Regression guard: the already-working case must keep working."""
        corrupt = bytearray(self._discover_frame(request_id=12))
        corrupt[-1] = 0x00
        good = self._discover_frame(request_id=13)

        responses, _, notices = self.adapter.extract_responses(
            bytearray(bytes(corrupt) + good), _calc_crc
        )
        assert [response["id"] for response in responses] == [13]
        assert any("resync" in notice.lower() for notice in notices)


class TestAceJsonResyncPreservesFrameBoundaries:
    """Same split-header guarantees for the ACE1 JSON adapter.

    The ACE1 wire format shares the 0xFF 0xAA header, so both resync paths
    have the same failure mode the ACE2 adapter was fixed for: clearing the
    whole buffer eats the leading 0xFF of a frame still arriving, and one
    corruption event costs two replies.  Gen1 units are the ones actually
    producing resync bursts in the field (735 junk drops in one klippy.log
    session), so the fix matters most here.
    """

    def setup_method(self):
        self.adapter = AceJsonProtocolAdapter()

    @staticmethod
    def _json_frame(request_id):
        """Build a valid ACE1 JSON response frame."""
        payload = json.dumps({"id": request_id, "result": {}}).encode("utf-8")
        return (
            b"\xFF\xAA"
            + struct.pack("<H", len(payload))
            + payload
            + struct.pack("<H", _calc_crc(payload))
            + b"\xFE"
        )

    def test_invalid_tail_keeps_split_header_byte(self):
        """A frame with a destroyed terminator must not consume the first
        byte of the next frame's header."""
        corrupt = bytearray(self._json_frame(request_id=8))
        corrupt[-1] = 0x00  # destroy the 0xFE terminator

        # Only the first byte of the next frame's header has arrived so far.
        responses, remaining, notices = self.adapter.extract_responses(
            bytearray(bytes(corrupt) + b"\xFF"), _calc_crc
        )
        assert responses == []
        assert any("resync" in notice.lower() for notice in notices)

        # Second read delivers the rest of a perfectly valid frame.
        good = self._json_frame(request_id=9)
        responses, _, _ = self.adapter.extract_responses(
            remaining + bytearray(good[1:]), _calc_crc
        )
        assert [response["id"] for response in responses] == [9]

    def test_junk_scan_keeps_split_header_byte(self):
        """Dropping unparseable junk must likewise preserve a trailing 0xFF
        that starts the next frame."""
        junk = bytes(range(0x10, 0x30))  # 32 bytes, contains no 0xFF 0xAA

        responses, remaining, notices = self.adapter.extract_responses(
            bytearray(junk + b"\xFF"), _calc_crc
        )
        assert responses == []
        assert any("junk" in notice.lower() for notice in notices)

        good = self._json_frame(request_id=11)
        responses, _, _ = self.adapter.extract_responses(
            remaining + bytearray(good[1:]), _calc_crc
        )
        assert [response["id"] for response in responses] == [11]

    def test_invalid_tail_still_recovers_a_complete_following_frame(self):
        """Regression guard: the already-working case must keep working."""
        corrupt = bytearray(self._json_frame(request_id=12))
        corrupt[-1] = 0x00
        good = self._json_frame(request_id=13)

        responses, _, notices = self.adapter.extract_responses(
            bytearray(bytes(corrupt) + good), _calc_crc
        )
        assert [response["id"] for response in responses] == [13]
        assert any("resync" in notice.lower() for notice in notices)


class TestAceJsonExtractResponses:
    """Parser kit for the ACE1 JSON frame reader."""

    def setup_method(self):
        self.adapter = AceJsonProtocolAdapter()

    @staticmethod
    def _json_frame(request_id):
        """Build a valid ACE1 JSON response frame."""
        payload = json.dumps({"id": request_id, "result": {}}).encode("utf-8")
        return (
            b"\xFF\xAA"
            + struct.pack("<H", len(payload))
            + payload
            + struct.pack("<H", _calc_crc(payload))
            + b"\xFE"
        )

    def test_an_implausible_length_does_not_stall_the_following_frame(self):
        """A corrupted u16 length must not make the reader wait for 64 KiB
        that will never come: the valid frame behind it is decoded now."""
        corrupt_header = b"\xFF\xAA" + struct.pack("<H", 0xFFFF) + b"\x00\x00\x00"
        good = self._json_frame(request_id=21)

        responses, remaining, notices = self.adapter.extract_responses(
            bytearray(corrupt_header + good), _calc_crc
        )

        assert [response["id"] for response in responses] == [21]
        assert remaining == bytearray()
        assert any("65535" in notice for notice in notices)

    def test_a_frame_split_across_two_reads_is_decoded_once_complete(self):
        good = self._json_frame(request_id=31)
        first_read, second_read = good[:10], good[10:]

        responses, remaining, notices = self.adapter.extract_responses(
            bytearray(first_read), _calc_crc
        )
        assert (responses, notices) == ([], [])
        assert remaining == bytearray(first_read)

        responses, remaining, notices = self.adapter.extract_responses(
            remaining + bytearray(second_read), _calc_crc
        )
        assert [response["id"] for response in responses] == [31]
        assert remaining == bytearray()
        assert notices == []

    def test_junk_without_a_header_is_dropped(self):
        responses, remaining, notices = self.adapter.extract_responses(
            bytearray(b"\x01" * 10), _calc_crc
        )

        assert responses == []
        assert remaining == bytearray()
        assert any("junk" in notice.lower() for notice in notices)

    def test_a_short_header_is_kept_for_the_next_read(self):
        responses, remaining, notices = self.adapter.extract_responses(
            bytearray(b"\xFF\xAA\x05"), _calc_crc
        )

        assert (responses, notices) == ([], [])
        assert remaining == bytearray(b"\xFF\xAA\x05")

    @pytest.mark.parametrize("payload", [b"[1, 2]", b"null", b'"x"', b"7"])
    def test_a_payload_that_is_no_object_is_dropped_with_a_notice(self, payload):
        # Every consumer reads a reply as a dict (response.get).
        frame = (b"\xFF\xAA" + struct.pack("<H", len(payload)) + payload
                 + struct.pack("<H", _calc_crc(payload)) + b"\xFE")

        responses, remaining, notices = self.adapter.extract_responses(
            bytearray(frame), _calc_crc)

        assert (responses, remaining) == ([], bytearray())
        assert any("not a JSON object" in notice for notice in notices)


def _ace2_frame(command_code, payload, request_id=1, flags=0x80):
    """Build a valid ACE2 response frame around a protobuf payload."""
    inner = bytes(
        [flags, request_id & 0xFF, (request_id >> 8) & 0xFF, command_code, len(payload)]
    ) + payload
    return b"\xFF\xAA" + inner + struct.pack("<H", _calc_crc(inner)) + b"\xFE"


class TestAceProtoExtractResponses:
    """Parser kit for the ACE2 frame reader."""

    GET_STATUS = 6
    GET_FILAMENT_INFO = 13

    def setup_method(self):
        self.adapter = AceProtoProtocolAdapter()

    def test_a_frame_split_across_two_reads_is_decoded_once_complete(self):
        payload = _pb_uint(1, 1) + _pb_uint(3, 31) + _pb_uint(4, 40)
        good = _ace2_frame(self.GET_STATUS, payload, request_id=41)
        # Past the 10-byte minimum, so the reader knows the frame length.
        first_read, second_read = good[:12], good[12:]

        responses, remaining, notices = self.adapter.extract_responses(
            bytearray(first_read), _calc_crc
        )
        assert (responses, notices) == ([], [])
        assert remaining == bytearray(first_read)

        responses, remaining, notices = self.adapter.extract_responses(
            remaining + bytearray(second_read), _calc_crc
        )
        assert [response["id"] for response in responses] == [41]
        assert remaining == bytearray()
        assert notices == []

    def test_junk_before_a_header_is_skipped(self):
        good = _ace2_frame(self.GET_STATUS, b"", request_id=42)

        responses, remaining, notices = self.adapter.extract_responses(
            bytearray(b"\x01\x02\x03" + good), _calc_crc
        )

        assert [response["id"] for response in responses] == [42]
        assert remaining == bytearray()
        assert any("3 bytes" in notice for notice in notices)

    def test_a_short_header_is_kept_for_the_next_read(self):
        responses, remaining, notices = self.adapter.extract_responses(
            bytearray(b"\xFF\xAA\x80\x01"), _calc_crc
        )

        assert (responses, notices) == ([], [])
        assert remaining == bytearray(b"\xFF\xAA\x80\x01")

    def test_a_crc_mismatch_drops_the_frame_with_a_notice(self):
        corrupt = bytearray(_ace2_frame(self.GET_STATUS, b"", request_id=43))
        corrupt[-2] ^= 0xFF

        responses, remaining, notices = self.adapter.extract_responses(
            corrupt, _calc_crc
        )

        assert responses == []
        assert remaining == bytearray()
        assert any("crc" in notice.lower() for notice in notices)

    def test_a_status_slot_without_filament_is_empty(self):
        slot_empty = _pb_uint(1, 0) + _pb_uint(2, 0)
        payload = _pb_uint(1, 1) + _pb_bytes(9, slot_empty)

        responses, _, _ = self.adapter.extract_responses(
            bytearray(_ace2_frame(self.GET_STATUS, payload)), _calc_crc
        )

        slot = responses[0]["result"]["slots"][0]
        assert (slot["status"], slot["status_detail"], slot["rfid"]) == (
            "empty", "empty", 0)

    def test_a_filament_info_colour_decodes_to_rgba(self):
        # PROTOCOL.md: colors are [[R, G, B, A]]; the ACE2 packs one colour
        # as a uint32 with red in the most significant byte.
        colour = ace2_pb_uint32(1, 0x11223344)
        payload = ace2_pb_uint32(1, 0) + ace2_pb_bytes(5, colour)

        responses, _, _ = self.adapter.extract_responses(
            bytearray(_ace2_frame(self.GET_FILAMENT_INFO, payload)), _calc_crc
        )

        assert responses[0]["result"]["colors"] == [[0x11, 0x22, 0x33, 0x44]]

    GET_INFO = 7

    @pytest.mark.parametrize("command_code, payload", [
        # GET_INFO version as a varint instead of a string
        (GET_INFO, _pb_uint(1, 5)),
        # GET_STATUS slot as a varint instead of a message
        (GET_STATUS, _pb_uint(1, 1) + _pb_uint(9, 3)),
        # GET_FILAMENT_INFO colour as bytes instead of a varint
        (GET_FILAMENT_INFO, _pb_uint(1, 0) + _pb_bytes(5, _pb_bytes(1, b"x"))),
    ])
    def test_a_field_of_the_wrong_wire_type_drops_the_frame_with_a_notice(
            self, command_code, payload):
        good = _ace2_frame(self.GET_STATUS, _pb_uint(1, 1), request_id=42)
        frames = _ace2_frame(command_code, payload, request_id=41) + good

        responses, remaining, notices = self.adapter.extract_responses(
            bytearray(frames), _calc_crc)

        assert [r["id"] for r in responses] == [42]
        assert remaining == bytearray()
        assert any("cannot decode" in notice for notice in notices), notices


class TestAceProtoRequestBuilders:
    """Fields each ACE2 request builder emits."""

    def setup_method(self):
        self.adapter = AceProtoProtocolAdapter()

    def test_feed_uses_mode_0_with_whole_numbers(self):
        request = self.adapter.build_feed_filament_request(1, 50.7, 20.0)

        assert request == {
            "command": "FEED_OR_ROLLBACK",
            "params": {"index": 1, "length": 50, "speed": 20, "mode": 0},
        }

    def test_stop_feed_and_stop_rollback_name_the_slot(self):
        assert self.adapter.build_stop_feed_filament_request(3) == {
            "command": "STOP_FEED_OR_ROLLBACK", "params": {"index": 3}}
        assert self.adapter.build_stop_unwind_filament_request(2) == {
            "command": "STOP_FEED_OR_ROLLBACK", "params": {"index": 2}}

    def test_both_speed_updates_send_slot_and_whole_speed(self):
        assert self.adapter.build_update_feeding_speed_request(1, 25.9) == {
            "command": "UPDATE_SPEED", "params": {"index": 1, "speed": 25}}
        assert self.adapter.build_update_unwinding_speed_request(2, 30.0) == {
            "command": "UPDATE_SPEED", "params": {"index": 2, "speed": 30}}

    def test_get_filament_info_names_the_slot(self):
        assert self.adapter.build_get_filament_info_request(3) == {
            "command": "GET_FILAMENT_INFO", "params": {"index": 3}}

    def test_start_drying_sends_temperature_and_duration(self):
        assert self.adapter.build_start_drying_request(55, 240) == {
            "command": "DRYING",
            "params": {"temp": 55, "duration": 240, "auto_roll": False},
        }


class TestAceProtoPbDecode:
    """The minimal protobuf decoder never raises on malformed payloads."""

    def test_a_truncated_varint_ends_the_decode(self):
        fields = _pb_decode(b"\x08\x96")

        assert list(fields) == [1]

    def test_a_fixed32_field_decodes_as_float(self):
        fields = _pb_decode(b"\x0d" + struct.pack("<f", 1.5))

        assert fields == {1: [(5, 1.5)]}

    def test_a_truncated_fixed32_field_decodes_as_zero(self):
        fields = _pb_decode(b"\x0d\x00")

        assert fields == {1: [(5, 0)]}

    def test_an_unknown_wire_type_stops_but_keeps_earlier_fields(self):
        fields = _pb_decode(b"\x08\x01" + b"\x13\x00\x00")

        assert fields == {1: [(0, 1)]}
