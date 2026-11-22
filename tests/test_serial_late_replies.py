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
