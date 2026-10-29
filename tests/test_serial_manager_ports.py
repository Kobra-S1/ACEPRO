"""AceSerialManager port selection, port claims, connect re-entry and the
disabled-ACE request gate.

The serial module is the boundary: ports are listed from fake comports()
entries and opened as fake serial objects; the reactor records timers.
"""

import queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ace import serial_manager as serial_manager_module
from ace.protocol_ace2 import AceProtoProtocolAdapter
from ace.serial_manager import AceSerialManager


class FakeSerialPort:
    def __init__(self, port=None, **_kwargs):
        self.port = port
        self.is_open = True
        self.written = []

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def close(self):
        self.is_open = False


def ace_port(device, location=None, description="ACE"):
    hwid = f"USB VID:PID=28E9:018A LOCATION={location}" if location else "USB VID:PID=28E9:018A"
    return SimpleNamespace(device=device, description=description, hwid=hwid)


@pytest.fixture(autouse=True)
def isolated_port_claims():
    saved = dict(serial_manager_module._CONNECTED_PORTS)
    serial_manager_module._CONNECTED_PORTS.clear()
    yield serial_manager_module._CONNECTED_PORTS
    serial_manager_module._CONNECTED_PORTS.clear()
    serial_manager_module._CONNECTED_PORTS.update(saved)


@pytest.fixture
def visible_ports():
    """The list comports() returns; tests fill it."""
    ports = []
    with patch("ace.serial_manager.serial") as serial_mod:
        serial_mod.Serial.side_effect = FakeSerialPort
        serial_mod.tools.list_ports.comports.side_effect = lambda: list(ports)
        yield ports


@pytest.fixture
def reactor():
    fake = Mock()
    fake.NOW = 0.0
    fake.NEVER = 999.0
    fake.monotonic.return_value = 100.0
    return fake


def make_manager(reactor, instance_num=0, **kwargs):
    return AceSerialManager(
        gcode=Mock(), reactor=reactor, instance_num=instance_num,
        ace_enabled=kwargs.pop("ace_enabled", True), **kwargs)


def timers_registered_for(reactor, callback):
    return [c for c in reactor.register_timer.call_args_list if c.args[0] == callback]


# ---- find_connection_port with a known USB location ----

def test_known_location_claimed_by_another_instance_is_unavailable(reactor, isolated_port_claims):
    manager = make_manager(reactor, instance_num=0, target_usb_location="1-1.3")
    isolated_port_claims["/dev/ttyACM0"] = 1

    port = manager.find_connection_port(0, ports=[ace_port("/dev/ttyACM0", "1-1.3")])

    assert port is None


def test_known_location_claimed_by_this_instance_is_returned(reactor, isolated_port_claims):
    manager = make_manager(reactor, instance_num=0, target_usb_location="1-1.3")
    isolated_port_claims["/dev/ttyACM0"] = 0

    port = manager.find_connection_port(0, ports=[ace_port("/dev/ttyACM0", "1-1.3")])

    assert port == "/dev/ttyACM0"


def test_known_location_on_a_shared_bus_ignores_a_siblings_claim(reactor, isolated_port_claims):
    manager = make_manager(
        reactor, instance_num=1, target_usb_location="1-1.3",
        protocol=AceProtoProtocolAdapter())
    isolated_port_claims["/dev/ttyUSB0"] = 0
    bus = ace_port("/dev/ttyUSB0", "1-1.3", description="USB Single Serial")

    port = manager.find_connection_port(1, ports=[bus])

    assert port == "/dev/ttyUSB0"


# ---- port claims on connect ----

def test_connecting_to_a_new_path_releases_the_claim_on_the_old_one(
        visible_ports, reactor, isolated_port_claims):
    manager = make_manager(reactor, instance_num=0)
    isolated_port_claims["/dev/ttyACM0"] = 0
    isolated_port_claims["/dev/ttyACM5"] = 1

    assert manager.connect("/dev/ttyACM1", 115200) is True

    assert isolated_port_claims == {"/dev/ttyACM1": 0, "/dev/ttyACM5": 1}


# ---- auto_connect bookkeeping ----

def test_repeated_port_misses_measure_from_the_first_miss(visible_ports, reactor):
    manager = make_manager(reactor)

    reactor.monotonic.return_value = 100.0
    assert manager.auto_connect(0, 115200) is False
    reactor.monotonic.return_value = 130.0
    assert manager.auto_connect(0, 115200) is False

    assert manager.sustained_port_miss_s(now=160.0) == 60.0


def test_a_bound_location_is_kept_when_the_fallback_opens_another(visible_ports, reactor):
    manager = make_manager(reactor, target_usb_location="1-1.3")
    visible_ports.append(ace_port("/dev/ttyACM0", "1-1.4"))

    assert manager.auto_connect(0, 115200) is True

    assert manager.get_usb_location() == "1-1.4"
    assert manager._target_usb_location == "1-1.3"


# ---- re-entry ----

def test_connecting_twice_registers_reader_and_writer_once(visible_ports, reactor):
    manager = make_manager(reactor)

    manager.connect("/dev/ttyACM0", 115200)
    manager.connect("/dev/ttyACM0", 115200)

    assert len(timers_registered_for(reactor, manager._writer)) == 1
    assert len(timers_registered_for(reactor, manager._reader)) == 1


def test_starting_the_heartbeat_twice_runs_one_timer_and_one_poll(reactor):
    manager = make_manager(reactor)

    manager.start_heartbeat()
    manager.start_heartbeat()

    assert len(timers_registered_for(reactor, manager._heartbeat_tick)) == 1
    assert manager._hp_queue.qsize() == 1


# ---- disabled ACE ----

def test_a_disabled_ace_queues_and_sends_nothing(reactor):
    manager = make_manager(reactor, ace_enabled=False)
    port = FakeSerialPort()
    manager._serial = port
    manager._connected = True
    callback = Mock()

    manager.send_request({"method": "get_status"}, callback)
    manager.send_high_prio_request({"method": "get_status"}, callback)
    manager._writer(0)

    assert manager._queue.empty() and manager._hp_queue.empty()
    assert manager.inflight == {}
    assert port.written == []
    callback.assert_not_called()


def test_a_full_high_priority_queue_is_reported_not_raised(reactor):
    manager = make_manager(reactor)
    manager._hp_queue = Mock()
    manager._hp_queue.put.side_effect = queue.Full

    manager.send_high_prio_request({"method": "get_status"}, Mock())

    assert any("High-priority queue full" in c.args[0]
               for c in manager.gcode.respond_info.call_args_list)


# ---- find_port_by_location ----

def test_find_port_by_location_skips_a_foreign_device_at_that_location(reactor):
    manager = make_manager(reactor)
    ports = [ace_port("/dev/ttyUSB0", "1-1.3", description="CP2102 USB to UART")]

    assert manager.find_port_by_location("ACE", "1-1.3", ports=ports) is None


def test_find_port_by_location_matches_an_ace_without_location_by_acm_number(reactor):
    manager = make_manager(reactor)
    ports = [
        ace_port("/dev/ttyUSB0", "1-1.3", description="CP2102 USB to UART"),
        ace_port("/dev/ttyACM2"),
    ]

    assert manager.find_port_by_location("ACE", "acm.2", ports=ports) == "/dev/ttyACM2"
