"""Polls that do not stack, and late replies that are not UNSOLICITED.

Replays a field failure (klippy.log of 2026-07-17 on a dual-ACE printer):
while an ACE preloads newly inserted filament it answers nothing for 6-7 s,
then answers every queued request at once. With a 1 s heartbeat and a 5 s
timeout that produced three timed-out polls per stall, three replies with
no callback logged as UNSOLICITED, and finally a forced reconnect from
communication supervision that made the ACE re-enumerate.

The serial port is a fake that records written frames and hands the
reader injected bytes; the reactor clock is set by the test.
"""

import json
import os
import struct
from unittest.mock import Mock, patch

import pytest

from ace.serial_manager import AceSerialManager

HEARTBEAT_INTERVAL_S = 1.0


class FakeAcePort:
    """Serial port: writes are recorded, reads return injected replies.

    select() needs a real descriptor; the write end of a pipe is always
    writable, so the TX path sends every frame at once.
    """

    def __init__(self):
        self._read_fd, self._write_fd = os.pipe()
        self.is_open = True
        self.sent = bytearray()
        self.rx = bytearray()

    def fileno(self):
        return self._write_fd

    def write(self, data):
        self.sent += data
        return len(data)

    def read(self, size=4096):
        data = bytes(self.rx[:size])
        del self.rx[:size]
        return data

    def close(self):
        os.close(self._read_fd)
        os.close(self._write_fd)


def _frames(buffer):
    """Decode request frames: FF AA <len16> <json> <crc16> FE."""
    requests = []
    pos = 0
    while pos < len(buffer):
        assert buffer[pos:pos + 2] == b'\xFF\xAA'
        length = struct.unpack('<H', buffer[pos + 2:pos + 4])[0]
        requests.append(json.loads(buffer[pos + 4:pos + 4 + length]))
        pos += 4 + length + 3
    return requests


class Rig:
    """A connected AceSerialManager driven by a test-owned clock."""

    def __init__(self):
        self.now = 0.0
        self.reactor = Mock()
        self.reactor.NOW = 0.0
        self.reactor.NEVER = 9e9
        self.reactor.monotonic = lambda: self.now
        self.gcode = Mock()
        with patch('ace.serial_manager.serial'):
            self.mgr = AceSerialManager(
                gcode=self.gcode,
                reactor=self.reactor,
                instance_num=0,
                ace_enabled=True,
            )
        self.port = FakeAcePort()
        self.mgr._serial = self.port
        self.mgr._connected = True
        self.statuses = []
        self.mgr.set_heartbeat_callback(self.statuses.append)

    def close(self):
        self.port.close()

    def sent_requests(self):
        return _frames(self.port.sent)

    def run_heartbeats(self, until_s):
        """One heartbeat per second plus writer ticks every 0.1 s."""
        next_beat = self.now
        while self.now <= until_s + 1e-9:
            if self.now >= next_beat - 1e-9:
                self.mgr._heartbeat_tick(self.now)
                next_beat += HEARTBEAT_INTERVAL_S
            self.mgr._writer(self.now)
            self.now = round(self.now + 0.1, 3)

    def reply(self, rid, result=None, msg="success"):
        payload = json.dumps({
            "id": rid, "code": 0, "msg": msg,
            "result": result if result is not None else {"status": "busy",
                                                          "action": "preload"},
        }).encode()
        crc = struct.pack('<H', self.mgr._calc_crc(payload))
        self.port.rx += (b'\xFF\xAA' + struct.pack('<H', len(payload))
                         + payload + crc + b'\xFE')
        self.mgr._reader(self.now)

    def console(self):
        return [c.args[0] for c in self.gcode.respond_info.call_args_list]


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def _ids(requests, method):
    return [r["id"] for r in requests if r.get("method") == method]


class TestHeartbeatDoesNotStack:

    def test_answered_poll_lets_the_next_one_out(self, rig):
        rig.run_heartbeats(until_s=0.0)
        first = _ids(rig.sent_requests(), "get_status")
        rig.reply(first[0], result={"status": "ready"})
        rig.run_heartbeats(until_s=1.0)

        assert len(_ids(rig.sent_requests(), "get_status")) == 2
        assert len(rig.statuses) == 1

    def test_unanswered_poll_blocks_further_polls(self, rig):
        rig.run_heartbeats(until_s=4.0)

        assert len(_ids(rig.sent_requests(), "get_status")) == 1

    def test_timed_out_poll_releases_the_guard(self, rig):
        rig.run_heartbeats(until_s=rig.mgr.timeout_s + 1.5)

        assert len(_ids(rig.sent_requests(), "get_status")) == 2
        assert rig.statuses == [None]

    def test_clear_queues_releases_the_guard(self, rig):
        rig.run_heartbeats(until_s=0.0)
        rig.mgr.clear_queues()
        rig.run_heartbeats(until_s=1.0)

        assert len(_ids(rig.sent_requests(), "get_status")) == 2

    def test_poll_keys_are_independent(self, rig):
        """Shared-bus instances each poll their own device id."""
        assert rig.mgr.send_poll_request(1, {"method": "get_status"}, Mock())
        assert rig.mgr.send_poll_request(2, {"method": "get_status"}, Mock())
        assert not rig.mgr.send_poll_request(1, {"method": "get_status"}, Mock())

    def test_poll_not_queued_while_disabled_does_not_block(self, rig):
        rig.mgr._ace_pro_enabled = False
        assert not rig.mgr.send_poll_request(1, {"method": "get_status"}, Mock())
        rig.mgr._ace_pro_enabled = True
        assert rig.mgr.send_poll_request(1, {"method": "get_status"}, Mock())


class TestLateReplies:

    def _stall_past_timeout(self, rig):
        """The field case: the ACE says nothing until the poll timed out."""
        rig.run_heartbeats(until_s=rig.mgr.timeout_s + 0.2)
        rid = _ids(rig.sent_requests(), "get_status")[0]
        assert any(f"Request ID={rid} TIMEOUT" in m for m in rig.console())
        return rid

    def test_late_status_reply_is_not_unsolicited(self, rig):
        rid = self._stall_past_timeout(rig)
        rig.reply(rid)

        assert not any("UNSOLICITED" in m for m in rig.console())
        assert rig.mgr._comm_unsolicited_timestamps == []

    def test_late_reply_still_counts_its_timeout(self, rig):
        rid = self._stall_past_timeout(rig)
        rig.reply(rid)

        assert len(rig.mgr._comm_timeout_timestamps) == 1

    def test_late_reply_to_a_command_is_reported(self, rig):
        """The ACE did what the driver already reported as failed."""
        rig.mgr.send_request({"method": "start_feed_assist",
                              "params": {"index": 1}}, Mock())
        rig.run_heartbeats(until_s=rig.mgr.timeout_s + 0.2)
        rid = _ids(rig.sent_requests(), "start_feed_assist")[0]
        rig.reply(rid, result={})

        late = [m for m in rig.console() if "late reply" in m]
        assert len(late) == 1
        assert "start_feed_assist" in late[0] and f"ID={rid}" in late[0]
        assert not any("UNSOLICITED" in m for m in rig.console())
        assert rig.mgr._comm_unsolicited_timestamps == []

    def test_late_status_reply_stays_off_the_console(self, rig):
        rid = self._stall_past_timeout(rig)
        rig.reply(rid)

        assert not any("late reply" in m for m in rig.console())

    def test_reply_to_an_id_never_sent_is_still_unsolicited(self, rig):
        rig.reply(4242)

        assert any("UNSOLICITED (ID=4242" in m for m in rig.console())
        assert len(rig.mgr._comm_unsolicited_timestamps) == 1

    def test_second_late_reply_to_the_same_id_is_unsolicited(self, rig):
        rid = self._stall_past_timeout(rig)
        rig.reply(rid)
        rig.reply(rid)

        assert sum("UNSOLICITED" in m for m in rig.console()) == 1

    def test_late_reply_after_the_memory_window_is_unsolicited(self, rig):
        rid = self._stall_past_timeout(rig)
        rig.now += rig.mgr.LATE_REPLY_WINDOW_S + 1.0
        rig.reply(rid)

        assert any(f"UNSOLICITED (ID={rid}" in m for m in rig.console())

    def test_reply_to_a_request_dropped_by_clear_queues_is_late(self, rig):
        """A reconnect forgets in-flight callbacks; ids are not reused."""
        rig.run_heartbeats(until_s=0.0)
        rid = _ids(rig.sent_requests(), "get_status")[0]
        rig.mgr.clear_queues()
        rig.reply(rid)

        assert not any("UNSOLICITED" in m for m in rig.console())

    def test_reply_to_a_request_failed_by_a_write_error_is_late(self, rig):
        """Bytes before a write error may have reached the ACE."""
        rig.run_heartbeats(until_s=0.0)
        rid = _ids(rig.sent_requests(), "get_status")[0]
        rig.mgr._fail_inflight()
        rig.reply(rid)

        assert not any("UNSOLICITED" in m for m in rig.console())

    def test_field_stalls_cost_one_timeout_each_and_no_unsolicited(self, rig):
        """Three 6.5 s preload stalls in a row, as in the field log: the
        ACE then answers every request it was sent."""
        replied = set()
        for _ in range(3):
            rig.run_heartbeats(until_s=rig.now + 6.5)
            for rid in _ids(rig.sent_requests(), "get_status"):
                if rid not in replied:
                    rig.reply(rid)
                    replied.add(rid)

        assert len(rig.mgr._comm_timeout_timestamps) == 3
        assert rig.mgr._comm_unsolicited_timestamps == []
        assert not any("UNSOLICITED" in m for m in rig.console())
