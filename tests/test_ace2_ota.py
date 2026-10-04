"""Tests for ace.ace2_ota: image checks, chunk plan, the IAP state machine.

Stock reference for the expected sequence: Kobra X avata_main 2.0.2.2,
OTA_FILAMENT_HUB_START (0x01330048). The synthetic box at the bottom parses
the real frames the ACE2 adapter serializes and rebuilds the staged image,
so the end-to-end tests prove the bytes that would reach the flash.
"""

import random
import struct
from types import SimpleNamespace

import pytest

from ace import ace2_ota
from ace.ace2_ota import (
    CHUNK_SIZE_BYTES,
    MAX_ATTEMPTS,
    REBOOT_WAIT_S,
    STAGING_BASE_ADDRESS,
    STEP_WATCHDOG_S,
    VERIFY_ATTEMPTS,
    Ace2FirmwareImage,
    Ace2FirmwareUpdater,
    FirmwareImageError,
    UpdateOutcome,
    firmware_image_from_bytes,
    load_firmware_image,
    parse_firmware_filename,
    plan_chunks,
    update_refusal,
)
from ace.protocol import crc16_mcrf4xx
from ace.protocol_ace1 import AceJsonProtocolAdapter
from ace.protocol_ace2 import AceProtoProtocolAdapter

# Observed on ACE2_V1.1.34_20260430.bin (72872 bytes): initial SP 0x20009B48,
# reset handler 0x08008245. The synthetic images copy that shape.
REAL_IMAGE_SIZE = 72872
REAL_INITIAL_SP = 0x20009B48
REAL_RESET_HANDLER = 0x08008245
FILENAME = "ACE2_V1.1.34_20260430.bin"


def make_image_bytes(size=REAL_IMAGE_SIZE, seed=1):
    rng = random.Random(seed)
    body = bytes(rng.randrange(256) for _ in range(size - 8))
    return struct.pack("<II", REAL_INITIAL_SP, REAL_RESET_HANDLER) + body


def make_image(size=REAL_IMAGE_SIZE, filename=FILENAME):
    return firmware_image_from_bytes(filename, make_image_bytes(size))


@pytest.fixture(autouse=True)
def _no_leftover_update():
    """The one-update-per-process slot is class state; no test may leak it."""
    Ace2FirmwareUpdater._active = None
    yield
    Ace2FirmwareUpdater._active = None


# ---------------------------------------------------------------------------
# CRC
# ---------------------------------------------------------------------------

class TestCrc16Mcrf4xx:
    def test_catalogue_check_value(self):
        # CRC-16/MCRF4XX check value from the CRC catalogue (reveng).
        assert crc16_mcrf4xx(b"123456789") == 0x6F91

    def test_empty_input_is_the_init_value(self):
        assert crc16_mcrf4xx(b"") == 0xFFFF

    def test_serial_manager_frames_use_the_same_crc(self):
        from ace.serial_manager import AceSerialManager
        data = make_image_bytes(300)
        assert AceSerialManager._calc_crc(None, data) == crc16_mcrf4xx(data)


# ---------------------------------------------------------------------------
# File name and image
# ---------------------------------------------------------------------------

class TestParseFirmwareFilename:
    def test_stock_name_gives_v_prefixed_version_and_date(self):
        assert parse_firmware_filename(FILENAME) == ("V1.1.34", "20260430")

    def test_suffix_after_version_is_allowed(self):
        assert parse_firmware_filename("ACE2_V1.0.1rc_20240104.bin") == ("V1.0.1", "20240104")

    @pytest.mark.parametrize("name", [
        "ACE2_1.1.34_20260430.bin",       # no V
        "ACE2_V1.1_20260430.bin",         # two-part version
        "ACE2_V1.1.34_2026043.bin",       # seven-digit date
        "ACE2_V1.1.34_20260430.swu",      # update package, not the image
        "ace2_V1.1.34_20260430.bin",      # stock's pattern is case-sensitive
        "x_ACE2_V1.1.34_20260430.bin",    # prefix
        "ACE2_V1.1.34_20260430.bin.bak",  # suffix
        "",
    ])
    def test_rejects_other_names(self, name):
        with pytest.raises(FirmwareImageError, match="does not match"):
            parse_firmware_filename(name)


class TestFirmwareImage:
    def test_image_announces_size_crc_and_version(self):
        data = make_image_bytes()
        image = firmware_image_from_bytes(FILENAME, data)
        assert image.size == REAL_IMAGE_SIZE
        assert image.crc == crc16_mcrf4xx(data)
        assert image.version == "V1.1.34"
        assert image.build_date == "20260430"

    def test_empty_file_is_refused(self):
        with pytest.raises(FirmwareImageError, match="too short"):
            firmware_image_from_bytes(FILENAME, b"")

    def test_truncated_vector_table_is_refused(self):
        with pytest.raises(FirmwareImageError, match="too short"):
            firmware_image_from_bytes(FILENAME, make_image_bytes(64)[:7])

    def test_stack_pointer_outside_ram_is_refused(self):
        data = struct.pack("<II", 0x08009B48, REAL_RESET_HANDLER) + bytes(56)
        with pytest.raises(FirmwareImageError, match="not in RAM"):
            firmware_image_from_bytes(FILENAME, data)

    def test_reset_vector_outside_flash_is_refused(self):
        data = struct.pack("<II", REAL_INITIAL_SP, 0x20008245) + bytes(56)
        with pytest.raises(FirmwareImageError, match="reset vector"):
            firmware_image_from_bytes(FILENAME, data)

    def test_reset_vector_without_thumb_bit_is_refused(self):
        data = struct.pack("<II", REAL_INITIAL_SP, 0x08008244) + bytes(56)
        with pytest.raises(FirmwareImageError, match="reset vector"):
            firmware_image_from_bytes(FILENAME, data)

    def test_update_package_bytes_are_refused(self):
        # A gzip'd setup.tar renamed to the image name must not reach the box.
        data = b"\x1f\x8b\x08\x00" + bytes(60)
        with pytest.raises(FirmwareImageError):
            firmware_image_from_bytes(FILENAME, data)

    def test_load_reads_file_and_takes_version_from_its_name(self, tmp_path):
        path = tmp_path / "ACE2_V1.2.3_20270101.bin"
        path.write_bytes(make_image_bytes(200))
        image = load_firmware_image(str(path))
        assert image.version == "V1.2.3"
        assert image.data == make_image_bytes(200)

    def test_load_missing_file_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            load_firmware_image(str(tmp_path / FILENAME))


# ---------------------------------------------------------------------------
# Chunk plan
# ---------------------------------------------------------------------------

class TestPlanChunks:
    def test_real_image_size_gives_1139_chunks_last_one_zero_padded(self):
        image = make_image()
        chunks = plan_chunks(image)
        assert len(chunks) == 1139
        last_address, last_data = chunks[-1]
        assert last_address == STAGING_BASE_ADDRESS + 1138 * 64
        assert len(last_data) == 64
        assert last_data[:40] == image.data[-40:]
        assert last_data[40:] == bytes(24)

    def test_every_chunk_is_64_bytes_at_base_plus_offset(self):
        chunks = plan_chunks(make_image())
        for index, (address, data) in enumerate(chunks):
            assert address == 0x08024000 + index * 64
            assert len(data) == CHUNK_SIZE_BYTES

    def test_chunks_carry_the_image_unchanged(self):
        image = make_image()
        joined = b"".join(data for _, data in plan_chunks(image))
        assert joined[:image.size] == image.data

    def test_exact_multiple_of_64_adds_no_empty_chunk(self):
        chunks = plan_chunks(make_image(size=128))
        assert [address for address, _ in chunks] == [0x08024000, 0x08024040]

    def test_image_shorter_than_one_chunk_is_one_padded_chunk(self):
        image = make_image(size=9)
        chunks = plan_chunks(image)
        assert chunks == [(0x08024000, image.data + bytes(55))]


# ---------------------------------------------------------------------------
# Wire encoding of the IAP commands
# ---------------------------------------------------------------------------

class TestIapWireFormat:
    def setup_method(self):
        self.adapter = AceProtoProtocolAdapter()

    def _frame(self, request, request_id=0x0102, device_id=2):
        request = dict(request, id=request_id, target_device_id=device_id)
        return self.adapter.serialize_request_frame(request, crc16_mcrf4xx)

    def test_upgrade_payload_is_size_crc_version(self):
        frame = self._frame(self.adapter.build_iap_upgrade_request(300, 0xC995, "V1.1.34"))
        payload = frame[7:-3]
        # field 1 varint 300, field 2 varint 0xC995, field 3 string
        assert payload == bytes([0x08, 0xAC, 0x02, 0x10, 0x95, 0x93, 0x03, 0x1A, 0x07]) + b"V1.1.34"
        assert frame[5] == 2

    def test_firmware_payload_is_address_and_bytes(self):
        chunk = bytes(range(64))
        frame = self._frame(self.adapter.build_iap_firmware_request(0x08024000, chunk))
        payload = frame[7:-3]
        # 0x08024000 as a varint is 80 80 89 40
        assert payload == bytes([0x08, 0x80, 0x80, 0x89, 0x40, 0x12, 0x40]) + chunk
        assert frame[5] == 3
        assert frame[6] == len(payload) == 71

    def test_finish_has_empty_payload(self):
        frame = self._frame(self.adapter.build_iap_finish_request())
        assert frame[5] == 4
        assert frame[6] == 0

    def test_frames_carry_the_target_device_id_and_crc(self):
        frame = self._frame(self.adapter.build_iap_finish_request(), request_id=0x0304, device_id=5)
        assert frame[:2] == b"\xFF\xAA"
        assert frame[2] == 5
        assert frame[3:5] == b"\x04\x03"
        assert frame[-3:-1] == struct.pack("<H", crc16_mcrf4xx(frame[2:-3]))
        assert frame[-1] == 0xFE

    def test_unaddressed_iap_frame_is_refused(self):
        request = dict(self.adapter.build_iap_finish_request(), id=1)
        with pytest.raises(ValueError, match="target_device_id"):
            self.adapter.serialize_request_frame(request, crc16_mcrf4xx)

    @pytest.mark.parametrize("code,msg", [(0, "SUCCESS"), (3, "FAILED")])
    def test_iap_reply_decodes_generic_code(self, code, msg):
        payload = bytes([0x08, code]) if code else b""
        inner = bytes([0x82, 0x01, 0x00, 3, len(payload)]) + payload
        frame = b"\xFF\xAA" + inner + struct.pack("<H", crc16_mcrf4xx(inner)) + b"\xFE"
        responses, _, _ = self.adapter.extract_responses(bytearray(frame), crc16_mcrf4xx)
        assert responses[0]["command"] == "IAP_FIRMWARE"
        assert responses[0]["code"] == code
        assert responses[0]["msg"] == msg

    @pytest.mark.parametrize("name", ["IAP_UPGRADE", "iap_firmware", "IAP_UPGRADE_FINISH"])
    def test_debug_requests_cannot_send_iap_commands(self, name):
        with pytest.raises(ValueError, match="ACE_FIRMWARE_UPDATE"):
            self.adapter.build_debug_request(name, {})

    def test_debug_requests_still_reach_other_commands(self):
        assert self.adapter.build_debug_request("GET_INFO")["command"] == "GET_INFO"

    def test_only_ace2_supports_firmware_update(self):
        assert self.adapter.supports_firmware_update()
        assert not AceJsonProtocolAdapter().supports_firmware_update()


# ---------------------------------------------------------------------------
# State machine against a scripted bus
# ---------------------------------------------------------------------------

class ScriptedBus:
    """Records requests; replies are delivered by the test, like the reactor would."""

    def __init__(self):
        self.sent = []  # (request, callback)
        self.later = []  # (delay_s, fn)
        self.reports = []
        self.polling = []
        self.results = []

    def send(self, request, callback):
        self.sent.append((dict(request), callback))

    def call_later(self, delay_s, fn):
        self.later.append((delay_s, fn))

    def updater(self, image, protocol=None):
        return Ace2FirmwareUpdater(
            image,
            protocol=protocol or AceProtoProtocolAdapter(),
            send=self.send,
            call_later=self.call_later,
            report=self.reports.append,
            set_polling_suspended=self.polling.append,
            on_finished=self.results.append,
            label="ACE[1]",
        )

    def commands(self):
        return [request["command"] for request, _ in self.sent]

    def reply_last(self, response):
        self.sent[-1][1](response=response)

    def run_timers(self, delay=None):
        """Fire scheduled callbacks (optionally only those of one delay)."""
        due = [item for item in self.later if delay is None or item[0] == delay]
        self.later = [item for item in self.later if item not in due]
        for _, fn in due:
            fn()

    def run_to_end(self, responder):
        """Answer each request with responder(request) until nothing is pending."""
        answered = 0
        while not self.results:
            if answered < len(self.sent):
                request, callback = self.sent[answered]
                answered += 1
                callback(response=responder(request))
            elif self.later:
                self.run_timers(REBOOT_WAIT_S if any(d == REBOOT_WAIT_S for d, _ in self.later)
                                else None)
            else:
                raise AssertionError("update stalled with nothing pending")


def ack(request):
    if request["command"] == "GET_INFO":
        return {"code": 0, "result": {"version": "V1.1.34"}}
    return {"code": 0, "msg": "SUCCESS"}


class TestUpdaterHappyPath:
    def test_sequence_is_announce_all_chunks_finish_then_version_check(self):
        bus = ScriptedBus()
        image = make_image()
        bus.updater(image).start()
        bus.run_to_end(ack)

        commands = bus.commands()
        assert commands[0] == "IAP_UPGRADE"
        assert commands[1:1140] == ["IAP_FIRMWARE"] * 1139
        assert commands[1140:] == ["IAP_UPGRADE_FINISH", "GET_INFO"]
        assert bus.results[0].outcome is UpdateOutcome.VERIFIED

    def test_announce_carries_size_crc_and_v_version(self):
        bus = ScriptedBus()
        image = make_image()
        bus.updater(image).start()
        assert bus.sent[0][0]["params"] == {
            "size": REAL_IMAGE_SIZE, "crc": image.crc, "version": "V1.1.34"}

    def test_chunks_are_sent_in_address_order_with_padding(self):
        bus = ScriptedBus()
        image = make_image()
        bus.updater(image).start()
        bus.run_to_end(ack)
        chunks = [r["params"] for r, _ in bus.sent if r["command"] == "IAP_FIRMWARE"]
        assert [(c["address"], c["data"]) for c in chunks] == plan_chunks(image)

    def test_polling_is_suspended_for_the_run_and_resumed_at_the_end(self):
        bus = ScriptedBus()
        bus.updater(make_image(size=256)).start()
        assert bus.polling == [True]
        bus.run_to_end(ack)
        assert bus.polling == [True, False]

    def test_version_is_only_asked_after_the_reboot_wait(self):
        bus = ScriptedBus()
        bus.updater(make_image(size=64)).start()
        bus.reply_last(ack(bus.sent[-1][0]))  # UPGRADE
        bus.reply_last(ack(bus.sent[-1][0]))  # the one chunk
        bus.reply_last(ack(bus.sent[-1][0]))  # FINISH
        assert bus.commands()[-1] == "IAP_UPGRADE_FINISH"
        bus.run_timers(REBOOT_WAIT_S)
        assert bus.commands()[-1] == "GET_INFO"

    def test_progress_is_reported_in_ten_percent_steps(self):
        bus = ScriptedBus()
        bus.updater(make_image()).start()
        bus.run_to_end(ack)
        progress = [r for r in bus.reports if "% (" in r]
        assert [r.split("update: ")[1].split("%")[0] for r in progress] == [
            str(p) for p in range(10, 101, 10)]

    def test_version_without_v_counts_as_the_same(self):
        bus = ScriptedBus()
        bus.updater(make_image(size=64)).start()

        def responder(request):
            if request["command"] == "GET_INFO":
                return {"code": 0, "result": {"version": "1.1.34"}}
            return ack(request)
        bus.run_to_end(responder)
        assert bus.results[0].outcome is UpdateOutcome.VERIFIED

    def test_on_finished_is_called_exactly_once(self):
        bus = ScriptedBus()
        bus.updater(make_image(size=64)).start()
        bus.run_to_end(ack)
        bus.run_timers()  # leftover watchdogs of answered requests
        assert len(bus.results) == 1


class TestUpdaterRetries:
    def _start(self, size=640):
        bus = ScriptedBus()
        updater = bus.updater(make_image(size=size))
        updater.start()
        return bus, updater

    def _ack_until_chunk(self, bus, chunk_index):
        bus.reply_last(ack(bus.sent[-1][0]))  # UPGRADE
        for _ in range(chunk_index):
            bus.reply_last(ack(bus.sent[-1][0]))

    def test_timed_out_chunk_is_resent_to_the_same_address(self):
        bus, _ = self._start()
        self._ack_until_chunk(bus, 4)
        address = bus.sent[-1][0]["params"]["address"]
        bus.reply_last(None)
        assert bus.sent[-1][0]["command"] == "IAP_FIRMWARE"
        assert bus.sent[-1][0]["params"]["address"] == address

    def test_rejected_chunk_counts_as_a_failed_attempt(self):
        bus, _ = self._start()
        self._ack_until_chunk(bus, 2)
        address = bus.sent[-1][0]["params"]["address"]
        bus.reply_last({"code": 3, "msg": "FAILED"})
        assert bus.sent[-1][0]["params"]["address"] == address

    def test_two_failures_then_success_continues(self):
        bus, _ = self._start()
        self._ack_until_chunk(bus, 1)
        address = bus.sent[-1][0]["params"]["address"]
        bus.reply_last(None)
        bus.reply_last(None)
        bus.reply_last(ack(bus.sent[-1][0]))
        assert bus.sent[-1][0]["params"]["address"] == address + 64
        assert not bus.results

    def test_chunk_failing_every_attempt_aborts_without_finish(self):
        bus, _ = self._start()
        self._ack_until_chunk(bus, 3)
        for _ in range(MAX_ATTEMPTS):
            bus.reply_last(None)
        assert bus.results[0].outcome is UpdateOutcome.FAILED
        assert "chunk 4/10 (0x080240C0)" in bus.results[0].message
        assert "IAP_UPGRADE_FINISH" not in bus.commands()
        assert bus.polling == [True, False]
        assert Ace2FirmwareUpdater.active() is None

    def test_announce_failing_every_attempt_sends_no_chunk(self):
        bus, _ = self._start()
        for _ in range(MAX_ATTEMPTS):
            bus.reply_last(None)
        assert bus.commands() == ["IAP_UPGRADE"] * MAX_ATTEMPTS
        assert bus.results[0].outcome is UpdateOutcome.FAILED

    def test_finish_failing_every_attempt_still_checks_the_version(self):
        # Stock logs a failed FINISH and carries on: the box may reboot first.
        bus, _ = self._start(size=64)
        bus.reply_last(ack(bus.sent[-1][0]))
        bus.reply_last(ack(bus.sent[-1][0]))
        for _ in range(MAX_ATTEMPTS):
            bus.reply_last(None)
        bus.run_timers(REBOOT_WAIT_S)
        bus.reply_last(ack(bus.sent[-1][0]))
        result = bus.results[0]
        assert result.outcome is UpdateOutcome.VERIFIED
        assert "IAP_UPGRADE_FINISH no answer" in result.message

    def test_watchdog_replaces_a_callback_that_never_comes(self):
        bus, _ = self._start()
        bus.reply_last(ack(bus.sent[-1][0]))  # UPGRADE
        sent_before = len(bus.sent)
        bus.run_timers(STEP_WATCHDOG_S)  # all watchdogs; only the pending one counts
        assert len(bus.sent) == sent_before + 1
        assert bus.sent[-1][0]["params"]["address"] == STAGING_BASE_ADDRESS

    def test_late_reply_after_a_retry_does_not_advance(self):
        bus, _ = self._start()
        bus.reply_last(ack(bus.sent[-1][0]))  # UPGRADE
        first_try_callback = bus.sent[-1][1]
        bus.reply_last(None)                  # chunk 1 times out, retry sent
        first_try_callback(response={"code": 0})  # the late reply to try 1
        assert bus.sent[-1][0]["params"]["address"] == STAGING_BASE_ADDRESS
        assert len(bus.sent) == 3

    def test_same_reply_delivered_twice_advances_once(self):
        bus, _ = self._start()
        bus.reply_last(ack(bus.sent[-1][0]))  # UPGRADE
        callback = bus.sent[-1][1]
        callback(response={"code": 0})
        callback(response={"code": 0})
        assert [r["params"]["address"] for r, _ in bus.sent[1:]] == [
            STAGING_BASE_ADDRESS, STAGING_BASE_ADDRESS + 64]


class TestUpdaterVerification:
    def _flash(self, info_response):
        bus = ScriptedBus()
        bus.updater(make_image(size=64)).start()
        for _ in range(3):
            bus.reply_last(ack(bus.sent[-1][0]))
        bus.run_timers(REBOOT_WAIT_S)
        for _ in range(VERIFY_ATTEMPTS):
            bus.reply_last(info_response)
            bus.run_timers()
        return bus

    def test_old_version_after_reboot_is_unverified(self):
        bus = self._flash({"code": 0, "result": {"version": "V1.1.31"}})
        result = bus.results[0]
        assert result.outcome is UpdateOutcome.UNVERIFIED
        assert "V1.1.31" in result.message and "V1.1.34" in result.message
        assert bus.commands().count("GET_INFO") == VERIFY_ATTEMPTS

    def test_silent_box_after_reboot_points_to_reconnect(self):
        bus = self._flash(None)
        assert bus.results[0].outcome is UpdateOutcome.UNVERIFIED
        assert "ACE_RECONNECT" in bus.results[0].message
        assert bus.polling == [True, False]


class TestUpdaterGuards:
    def test_second_update_is_refused_while_one_runs(self):
        first, second = ScriptedBus(), ScriptedBus()
        first.updater(make_image(size=64)).start()
        with pytest.raises(RuntimeError, match="already running"):
            second.updater(make_image(size=64)).start()
        assert second.sent == [] and second.polling == []

    def test_a_new_update_may_start_after_the_last_one_ended(self):
        first, second = ScriptedBus(), ScriptedBus()
        first.updater(make_image(size=64)).start()
        first.run_to_end(ack)
        second.updater(make_image(size=64)).start()
        assert second.commands() == ["IAP_UPGRADE"]

    def test_an_updater_runs_only_once(self):
        bus = ScriptedBus()
        updater = bus.updater(make_image(size=64))
        updater.start()
        bus.run_to_end(ack)
        with pytest.raises(RuntimeError, match="already run"):
            updater.start()

    def test_send_raising_ends_the_run_and_releases_everything(self):
        bus = ScriptedBus()

        def broken_send(request, callback):
            raise RuntimeError("Shared-bus request requires an assigned target device_id")
        bus.send = broken_send
        bus.updater(make_image(size=64)).start()
        assert bus.results[0].outcome is UpdateOutcome.FAILED
        assert "device_id" in bus.results[0].message
        assert bus.polling == [True, False]
        assert Ace2FirmwareUpdater.active() is None


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------

def ready_instance(**overrides):
    fields = dict(
        protocol=AceProtoProtocolAdapter(),
        serial_mgr=SimpleNamespace(is_connected=lambda: True),
        _is_printing_or_paused=lambda: False,
        _feed_assist_index=-1,
        _info={"status": "ready"},
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestUpdateRefusal:
    def test_idle_connected_ace2_box_may_be_flashed(self):
        assert update_refusal(ready_instance()) is None

    @pytest.mark.parametrize("overrides,reason", [
        ({"protocol": AceJsonProtocolAdapter()}, "ACE2 protocol"),
        ({"serial_mgr": SimpleNamespace(is_connected=lambda: False)}, "not connected"),
        ({"_is_printing_or_paused": lambda: True}, "print"),
        ({"_feed_assist_index": 2}, "feed assist is active on slot 2"),
        ({"_info": {"status": "busy"}}, "'busy'"),
    ])
    def test_refuses(self, overrides, reason):
        assert reason in update_refusal(ready_instance(**overrides))


# ---------------------------------------------------------------------------
# End to end against a synthetic ACE2 box
# ---------------------------------------------------------------------------

def _varint(data, pos):
    value, shift = 0, 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _fields(payload):
    """Independent protobuf reader (varint and length-delimited only)."""
    fields, pos = {}, 0
    while pos < len(payload):
        tag, pos = _varint(payload, pos)
        number, wire_type = tag >> 3, tag & 7
        if wire_type == 0:
            fields[number], pos = _varint(payload, pos)
        elif wire_type == 2:
            length, pos = _varint(payload, pos)
            fields[number] = bytes(payload[pos:pos + length])
            pos += length
        else:
            raise AssertionError(f"unexpected wire type {wire_type}")
    return fields


class SyntheticAce2Box:
    """What the box's IAP handler is known to need, and nothing more.

    Parses raw frames, answers only frames addressed to its device id,
    stages IAP_FIRMWARE data by address, checks the announced CRC on FINISH
    and then runs the announced version.
    """

    def __init__(self, device_id=2, version="V1.1.31"):
        self.device_id = device_id
        self.version = version
        self.announced = None
        self.staging = bytearray()
        self.chunk_lengths = set()
        self.drop_reply_for = set()  # frame sequence numbers to leave unanswered
        self.frames_seen = 0

    def handle(self, frame):
        assert frame[:2] == b"\xFF\xAA" and frame[-1] == 0xFE
        inner = frame[2:-3]
        assert frame[-3:-1] == struct.pack("<H", crc16_mcrf4xx(inner))
        device_id, seq, command, length = inner[0], inner[1] | inner[2] << 8, inner[3], inner[4]
        payload = inner[5:5 + length]
        assert len(payload) == length
        self.frames_seen += 1
        if device_id != self.device_id or self.frames_seen in self.drop_reply_for:
            return None
        reply = self._execute(command, _fields(payload))
        header = bytes([0x80 | self.device_id, seq & 0xFF, seq >> 8, command, len(reply)])
        out = header + reply
        return b"\xFF\xAA" + out + struct.pack("<H", crc16_mcrf4xx(out)) + b"\xFE"

    def _execute(self, command, fields):
        if command == 2:
            self.announced = (fields[1], fields[2], fields[3].decode())
            self.staging = bytearray(b"\xFF" * (fields[1] + CHUNK_SIZE_BYTES))
            return b""
        if command == 3:
            assert self.announced is not None, "chunk before IAP_UPGRADE"
            offset = fields[1] - STAGING_BASE_ADDRESS
            self.chunk_lengths.add(len(fields[2]))
            self.staging[offset:offset + len(fields[2])] = fields[2]
            return b""
        if command == 4:
            size, crc, version = self.announced
            if crc16_mcrf4xx(bytes(self.staging[:size])) != crc:
                return bytes([0x08, 3])  # FAILED; keep running the old image
            self.version = version
            return b""
        if command == 7:
            return b"\x0a" + bytes([len(self.version)]) + self.version.encode()
        raise AssertionError(f"unexpected command {command}")


class WiredBus(ScriptedBus):
    """Serializes through the real adapter, parses replies with it too."""

    def __init__(self, box, target_device_id=None):
        super().__init__()
        self.box = box
        self.target_device_id = target_device_id or box.device_id
        self.adapter = AceProtoProtocolAdapter()
        self._next_id = 1

    def send(self, request, callback):
        framed = dict(request, id=self._next_id, target_device_id=self.target_device_id)
        self._next_id += 1
        reply = self.box.handle(self.adapter.serialize_request_frame(framed, crc16_mcrf4xx))
        response = None
        if reply is not None:
            responses, _, _ = self.adapter.extract_responses(bytearray(reply), crc16_mcrf4xx)
            response = responses[0]
        super().send(request, lambda response=None, _r=response: callback(response=_r))


def run_wired(box, image):
    bus = WiredBus(box)
    bus.updater(image).start()
    bus.run_to_end(lambda request: None)  # replies were bound at send time
    return bus


class TestEndToEndAgainstSyntheticBox:
    def test_box_ends_up_with_the_exact_image_and_new_version(self):
        box = SyntheticAce2Box()
        image = make_image()
        bus = run_wired(box, image)
        assert bytes(box.staging[:image.size]) == image.data
        assert box.announced == (image.size, image.crc, "V1.1.34")
        assert box.chunk_lengths == {64}
        assert box.version == "V1.1.34"
        assert bus.results[0].outcome is UpdateOutcome.VERIFIED

    def test_dropped_replies_are_retried_and_the_image_still_matches(self):
        box = SyntheticAce2Box()
        box.drop_reply_for = {2, 500, 501}  # first chunk; two tries of one chunk
        image = make_image()
        bus = run_wired(box, image)
        assert bytes(box.staging[:image.size]) == image.data
        assert bus.results[0].outcome is UpdateOutcome.VERIFIED

    def test_box_with_another_device_id_is_never_answered_and_the_run_fails(self):
        box = SyntheticAce2Box(device_id=4)
        bus = WiredBus(box, target_device_id=3)
        bus.updater(make_image(size=256)).start()
        bus.run_to_end(lambda request: None)
        assert bus.results[0].outcome is UpdateOutcome.FAILED
        assert box.version == "V1.1.31"

    def test_corrupted_transfer_is_caught_by_the_box_crc_and_reported(self):
        box = SyntheticAce2Box()
        image = make_image(size=256)
        corrupt = Ace2FirmwareImage(
            data=image.data, version=image.version, build_date=image.build_date,
            crc=image.crc ^ 0x0001)
        bus = run_wired(box, corrupt)
        assert box.version == "V1.1.31"  # commit refused, old image runs
        assert bus.results[0].outcome is UpdateOutcome.UNVERIFIED
        assert "FINISH" in bus.results[0].message
