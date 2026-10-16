"""ACE serial writes must never block Klipper's reactor.

The writer runs as a reactor timer. An ACE that stops taking data (mid
watchdog reset, USB dropping) used to hold each write for the 0.1 s write
timeout, twice per tick with two requests queued - long enough to make a
homing move's steps late. Frames now go through a buffer that is written
only while the port accepts data, and a port that accepts nothing for
TX_STALL_LIMIT_S is treated as a lost connection.

The fake port is a real pipe, so "accepts data" is the kernel's answer.
"""

import fcntl
import os
from unittest.mock import Mock, patch

import pytest


class PipePort:
    """A serial port whose output side is a non-blocking pipe."""

    def __init__(self):
        self.read_fd, self.write_fd = os.pipe()
        for fd in (self.read_fd, self.write_fd):
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.is_open = True
        self.accept_per_write = None
        self.writes = 0

    def fileno(self):
        return self.write_fd

    def write(self, data):
        self.writes += 1
        if self.accept_per_write is not None:
            data = data[:self.accept_per_write]
        return os.write(self.write_fd, data)

    def drained(self):
        """Everything the far end received since the last call."""
        received = bytearray()
        try:
            while True:
                chunk = os.read(self.read_fd, 65536)
                if not chunk:
                    break
                received += chunk
        except BlockingIOError:
            pass
        return bytes(received)

    def close(self):
        os.close(self.read_fd)
        os.close(self.write_fd)


@pytest.fixture
def port():
    pipe_port = PipePort()
    yield pipe_port
    pipe_port.close()


@pytest.fixture
def manager(port):
    from ace.serial_manager import AceSerialManager

    reactor = Mock()
    reactor.NOW = 0.0
    reactor.NEVER = 999.0
    reactor.monotonic.return_value = 100.0
    serial_manager = AceSerialManager(
        gcode=Mock(), reactor=reactor, instance_num=0, ace_enabled=True)
    serial_manager._serial = port
    serial_manager._connected = True
    serial_manager.reconnect = Mock()
    return serial_manager


def frame_of(manager, request):
    return manager.protocol.serialize_request_frame(
        dict(request), manager._calc_crc)


def test_a_frame_goes_out_whole_when_the_port_takes_it(manager, port):
    manager._send_frame({"id": 1, "method": "get_status"})

    assert port.drained() == frame_of(manager, {"id": 1, "method": "get_status"})


def test_a_stalled_port_is_not_written_to_and_the_frame_is_kept(manager, port):
    full(port)

    manager._send_frame({"id": 1, "method": "get_status"})

    assert port.writes == 0
    port.drained()
    manager._writer(100.1)
    assert port.drained() == frame_of(manager, {"id": 1, "method": "get_status"})


def test_frames_queued_during_a_stall_leave_in_order(manager, port):
    full(port)
    manager._send_frame({"id": 1, "method": "first"})
    manager._send_frame({"id": 2, "method": "second"})
    port.drained()

    manager._writer(100.1)

    assert port.drained() == (frame_of(manager, {"id": 1, "method": "first"})
                              + frame_of(manager, {"id": 2, "method": "second"}))


def test_a_partial_write_sends_the_rest_on_the_next_tick(manager, port):
    port.accept_per_write = 5
    expected = frame_of(manager, {"id": 1, "method": "get_status"})

    manager._send_frame({"id": 1, "method": "get_status"})
    assert port.drained() == expected[:5]

    port.accept_per_write = None
    manager._writer(100.1)
    assert port.drained() == expected[5:]


def test_a_port_stalled_past_the_limit_is_reconnected_once(manager, port):
    from ace.serial_manager import AceSerialManager

    full(port)
    manager._send_frame({"id": 1, "method": "get_status"})
    manager.reactor.monotonic.return_value = (
        100.0 + AceSerialManager.TX_STALL_LIMIT_S - 0.1)
    manager._writer(0)
    manager.reconnect.assert_not_called()

    manager.reactor.monotonic.return_value = (
        100.0 + AceSerialManager.TX_STALL_LIMIT_S + 0.1)
    manager._writer(0)
    manager._writer(0)

    manager.reconnect.assert_called_once()
    assert port.writes == 0


def test_a_stall_that_clears_does_not_count_towards_the_limit(manager, port):
    from ace.serial_manager import AceSerialManager

    full(port)
    manager._send_frame({"id": 1, "method": "get_status"})
    port.drained()
    manager._writer(0)

    full(port)
    manager.reactor.monotonic.return_value = (
        100.0 + AceSerialManager.TX_STALL_LIMIT_S + 0.1)
    manager._send_frame({"id": 2, "method": "get_status"})
    manager._writer(0)

    manager.reconnect.assert_not_called()


def test_disconnect_drops_unsent_bytes(manager, port):
    full(port)
    manager._send_frame({"id": 1, "method": "get_status"})
    port.close = Mock()

    manager.disconnect()
    port.drained()
    manager._connected = True
    manager._send_frame({"id": 2, "method": "get_info"})

    assert port.drained() == frame_of(manager, {"id": 2, "method": "get_info"})


def test_the_port_is_opened_non_blocking():
    with patch("ace.serial_manager.serial") as serial_mod:
        from ace.serial_manager import AceSerialManager

        reactor = Mock()
        reactor.monotonic.return_value = 0.0
        serial_manager = AceSerialManager(
            gcode=Mock(), reactor=reactor, instance_num=0, ace_enabled=True)
        serial_manager.connect("/dev/ttyACM0", 115200)

        assert serial_mod.Serial.call_args.kwargs["write_timeout"] == 0


def test_a_port_that_vanishes_while_being_opened_is_a_failed_connect():
    """The ACE re-enumerates between the port scan and the open: pyserial's
    DTR ioctl then raises a plain OSError, in a reactor timer, where an
    escaping exception shuts Klipper down."""
    serial_error = type("SerialException", (Exception,), {})
    with patch("ace.serial_manager.serial") as serial_mod, \
            patch("ace.serial_manager.SerialException", serial_error):
        from ace.serial_manager import AceSerialManager

        serial_mod.Serial.side_effect = OSError(5, "Input/output error")
        gcode = Mock()
        serial_manager = AceSerialManager(
            gcode=gcode, reactor=Mock(), instance_num=0, ace_enabled=True)

        assert serial_manager.connect("/dev/ttyACM0", 115200) is False
        assert "Input/output error" in gcode.respond_info.call_args[0][0]


def full(port):
    """Leave the port taking nothing more."""
    try:
        while True:
            os.write(port.write_fd, b"\0" * 4096)
    except BlockingIOError:
        pass
