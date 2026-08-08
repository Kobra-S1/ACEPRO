"""
Tests for AceSerialManager pure logic functions.

Focus: Testing actual production code without heavy I/O mocking.
- USB location parsing
- CRC calculation  
- Frame parsing
- Status update change detection
"""
import pytest
import queue
from types import SimpleNamespace
import struct
import json
from unittest.mock import Mock, patch

from ace.protocol import AceTransportSpec, parse_usb_location


class TestParseUsbLocation:
    """Test the shared USB location parser (protocol.parse_usb_location).

    serial_manager.py no longer keeps its own copy of this logic - it reuses
    protocol.parse_usb_location directly so there is a single source of
    truth for USB daisy-chain sort order.
    """

    def test_parse_simple_location(self):
        """Test parsing simple USB location like '1-1.4'."""
        result = parse_usb_location("1-1.4")
        assert result == (1, 1, 4)

    def test_parse_complex_location_with_colon(self):
        """Test parsing location with colon interface suffix."""
        # "1-1.4.3:1.0" - the :1.0 is the USB interface, should be stripped
        result = parse_usb_location("1-1.4.3:1.0")
        # After split(':')[0] → "1-1.4.3", replace('-','.') → "1.1.4.3"
        assert result == (1, 1, 4, 3)

    def test_parse_acm_fallback(self):
        """Test parsing ACM device fallback format."""
        result = parse_usb_location("acm.2")
        # ACM devices sort after USB (999998) but before unknown (999999)
        assert result == (999998, 2)

    def test_parse_acm_fallback_zero(self):
        """Test parsing ACM0 fallback format."""
        result = parse_usb_location("acm.0")
        assert result == (999998, 0)

    def test_parse_acm_invalid_returns_high_value(self):
        """Non-numeric ACM suffix should fall back to high sort key."""
        result = parse_usb_location("acm.bad")
        assert result == (999999,)

    def test_parse_invalid_token_raises_value_error_branch(self):
        """Mixed tokens causing ValueError should fall back to high sort key."""
        result = parse_usb_location("1-1.a.3")
        assert result == (999999,)

    def test_acm_sorts_after_usb_before_unknown(self):
        """ACM devices should sort after USB but before unknowns."""
        usb = parse_usb_location("1-1.4.3:1.0")
        acm = parse_usb_location("acm.2")
        unknown = parse_usb_location("garbage")

        assert usb < acm < unknown

    def test_parse_empty_string_returns_high_value(self):
        """Empty string should sort to end."""
        result = parse_usb_location("")
        assert result == (999999,)

    def test_parse_none_returns_high_value(self):
        """None should sort to end."""
        result = parse_usb_location(None)
        assert result == (999999,)

    def test_parse_invalid_location_returns_high_value(self):
        """Invalid non-numeric location should sort to end."""
        result = parse_usb_location("invalid-text-here")
        assert result == (999999,)

    def test_parse_mixed_numeric_and_text_returns_high_value(self):
        """Mixed segments with text should not raise and should sort to end."""
        result = parse_usb_location("1-foo.3")
        assert result == (999999,)

    def test_invalid_locations_sort_after_valid(self):
        """Ensure invalid locations compare after valid ones without exceptions."""
        valid = parse_usb_location("1-1.2")
        invalid = parse_usb_location("garbage")
        assert valid < invalid

    def test_sorting_order_is_correct(self):
        """Verify locations sort in expected USB topology order."""
        locations = [
            "1-1.4.3:1.0",  # Should be (1, 1, 4, 3)
            "1-1.2:1.0",    # Should be (1, 1, 2)
            "1-1.4.1:1.0",  # Should be (1, 1, 4, 1)
            "2-1:1.0",      # Should be (2, 1)
        ]

        parsed = [parse_usb_location(loc) for loc in locations]
        sorted_parsed = sorted(parsed)

        assert sorted_parsed == [
            (1, 1, 2),
            (1, 1, 4, 1),
            (1, 1, 4, 3),
            (2, 1),
        ]


class TestFindComPort:
    """Tests for find_com_port covering enumeration branches.

    find_com_port() is a location-based, stateless fallback: it always
    re-derives order from currently-visible USB locations (depth-first,
    then lexicographic) and returns matches[instance]. It no longer
    remembers "expected topology" across calls - that self-learning
    validator was redundant with (and could disagree with) AceManager's
    authoritative _resolve_daisy_chain_topology(), which resolves each
    instance's target_usb_location once and matches by exact location
    string via find_port_by_location() instead.
    """

    def setup_method(self):
        self.serial_patch = patch('ace.serial_manager.serial')
        self.serial_mod = self.serial_patch.start()
        self.serial_mod.Serial = object
        self.serial_mod.SerialException = Exception
        self.serial_mod.SerialTimeoutException = Exception
        self.serial_mod.tools = SimpleNamespace(list_ports=SimpleNamespace(comports=lambda: []))
        from ace.serial_manager import AceSerialManager
        self.gcode = Mock()
        self.manager = AceSerialManager(gcode=self.gcode, reactor=Mock(), ace_enabled=False)
        self.manager.instance_num = 0

    def teardown_method(self):
        self.serial_patch.stop()

    def test_returns_none_when_no_matches(self):
        self.serial_mod.tools.list_ports.comports = lambda: []

        assert self.manager.find_com_port("ACE", 0) is None

    def test_returns_none_when_insufficient_devices_for_instance(self):
        ports = [SimpleNamespace(device="/dev/ttyUSB0", description="ACE", hwid="LOCATION=1-1.1")]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=1)

        assert result is None

    def test_returns_sorted_device(self):
        ports = [
            SimpleNamespace(device="/dev/ttyUSB1", description="ACE", hwid="LOCATION=1-1.2"),
            SimpleNamespace(device="/dev/ttyUSB0", description="ACE", hwid="LOCATION=1-1.1"),
        ]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=0)

        # Sorted by topology so /dev/ttyUSB0 (1-1.1) should be chosen first
        assert result == "/dev/ttyUSB0"

    def test_reresolves_order_on_every_call(self):
        """Unlike the old self-learning validator, order is re-derived from
        current ports on every call - there is no stale cached state to
        disagree with reality."""
        ports = [SimpleNamespace(device="/dev/ttyUSB5", description="ACE", hwid="LOCATION=1-1.9")]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=0)

        assert result == "/dev/ttyUSB5"

    def test_falls_back_to_device_when_no_location_or_acm(self):
        ports = [SimpleNamespace(device="/dev/ttyXYZ", description="ACE", hwid="NOLOC")]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=0)

        assert result == "/dev/ttyXYZ"

    def test_skips_devices_with_mismatched_description(self):
        ports = [SimpleNamespace(device="/dev/ttyUSB9", description="OTHER", hwid="LOCATION=1-1.1")]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=0)
        assert result is None

    def test_does_not_match_ace2_transport_when_looking_for_ace1(self):
        ports = [SimpleNamespace(device="/dev/ttyUSB9", description="ACE2 USB-RS485", hwid="LOCATION=1-1.1")]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=0)

        assert result is None

    def test_mixed_ports_continue_on_non_match_then_selects_match(self):
        ports = [
            SimpleNamespace(device="/dev/ttyIGNORE", description="OTHER", hwid="LOCATION=1-1.1"),
            SimpleNamespace(device="/dev/ttyACM3", description="ACE", hwid="NOLC"),
        ]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=0)
        # Should skip the first port and pick the ACE device
        assert result == "/dev/ttyACM3"

    def test_acm_fallback_location_parsing(self):
        ports = [SimpleNamespace(device="/dev/ttyACM3", description="ACE", hwid="NOLC")]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=0)
        assert result == "/dev/ttyACM3"

    def test_deeper_daisy_chain_position_selected_for_higher_instance(self):
        # Instance 1 (second physical unit) must resolve to the deeper
        # daisy-chain location, purely from current sort order - no stored
        # "expected topology" needed or consulted.
        ports = [
            SimpleNamespace(device="/dev/ttyUSB0", description="ACE", hwid="LOCATION=1-1.2"),      # shallower
            SimpleNamespace(device="/dev/ttyUSB1", description="ACE", hwid="LOCATION=1-1.4.3"),    # deeper
        ]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=1)
        assert result == "/dev/ttyUSB1"

    def test_find_com_port_detects_ace2_usb_single_serial_real_hwid(self):
        # Simulates the exact PySerial portinfo for a QinHeng CH343 adapter
        # as reported by the kernel: VID=1A86 PID=55D3, description="USB Single Serial"
        # Serial number anonymised; location 1-1.4 is the real USB topology.
        ports = [
            SimpleNamespace(
                device="/dev/ttyACM0",
                description="USB Single Serial",
                hwid="USB VID:PID=1A86:55D3 SER=ANONACE2SN LOCATION=1-1.4:1.0",
            )
        ]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("USB Single Serial", instance=0)

        assert result == "/dev/ttyACM0"

    def test_find_com_port_detects_mixed_ace1_ace2_real_topology(self):
        """Mixed enumeration should keep ACE1 and ACE2 transports distinct."""
        ports = [
            SimpleNamespace(
                device="/dev/ttyACM0",
                description="ACE",
                hwid="USB VID:PID=28E9:018A SER=ANONACE1SN LOCATION=1-1.4.3:1.0",
            ),
            SimpleNamespace(
                device="/dev/ttyACM1",
                description="USB Single Serial",
                hwid="USB VID:PID=1A86:55D3 SER=ANONACE2SN2 LOCATION=1-1.4.4:1.0",
            ),
        ]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        ace1_port = self.manager.find_com_port("ACE", instance=0)
        ace2_port = self.manager.find_com_port("USB Single Serial", instance=0)

        assert ace1_port == "/dev/ttyACM0"
        assert ace2_port == "/dev/ttyACM1"

    def test_handles_more_aces_than_configured_instances(self):
        # Three devices visible (two ACE1 + one unrelated); only instance 0
        # requested, must resolve to the physically-closest ACE1 unit.
        ports = [
            SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=2-2.3"),
            SimpleNamespace(device="/dev/ttyACM1", description="ACE", hwid="LOCATION=2-2.4.3"),
            SimpleNamespace(device="/dev/ttyACM9", description="OTHER", hwid="LOCATION=9-9"),  # ignore
        ]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=0)
        assert result == "/dev/ttyACM0"

    def test_skips_port_claimed_by_another_instance(self):
        """A port already opened by a different logical instance must never
        be selected by another instance's fallback lookup - stealing an
        in-use port causes interleaved frames on both (see ACE[0]/ACE[1]
        garbled-comms bug reports)."""
        from ace import serial_manager

        ports = [
            SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=2-2.3"),
            SimpleNamespace(device="/dev/ttyACM1", description="ACE", hwid="LOCATION=2-2.4.3"),
        ]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        serial_manager._CONNECTED_PORTS["/dev/ttyACM0"] = 5  # claimed by instance 5

        result = self.manager.find_com_port("ACE", instance=0)

        # instance 0 must skip the claimed port and fall back to the next
        # candidate rather than opening a port instance 5 already owns.
        assert result == "/dev/ttyACM1"

    def test_does_not_skip_port_claimed_by_self(self):
        """A port this same instance already claimed (e.g. re-resolving
        after a transient blip) must remain selectable."""
        from ace import serial_manager

        ports = [SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=2-2.3")]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        serial_manager._CONNECTED_PORTS["/dev/ttyACM0"] = self.manager.instance_num

        result = self.manager.find_com_port("ACE", instance=0)

        assert result == "/dev/ttyACM0"



class TestGetUsbLocationForPort:
    """Tests for _get_usb_location_for_port."""

    def setup_method(self):
        self.serial_patch = patch('ace.serial_manager.serial')
        self.serial_mod = self.serial_patch.start()
        self.serial_mod.Serial = object
        self.serial_mod.SerialException = Exception
        self.serial_mod.SerialTimeoutException = Exception
        self.serial_mod.tools = SimpleNamespace(list_ports=SimpleNamespace(comports=lambda: []))
        from ace.serial_manager import AceSerialManager
        self.manager = AceSerialManager(gcode=Mock(), reactor=Mock(), ace_enabled=False)

    def teardown_method(self):
        self.serial_patch.stop()

    def test_returns_location_from_hwid(self):
        ports = [SimpleNamespace(device="/dev/ttyUSB0", hwid="XYZ LOCATION=1-2.3", description="ACE")]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager._get_usb_location_for_port("/dev/ttyUSB0")
        assert result == "1-2.3"

    def test_returns_acm_fallback_when_no_hwid(self):
        ports = [SimpleNamespace(device="/dev/ttyACM2", hwid="NOLOC", description="ACE")]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager._get_usb_location_for_port("/dev/ttyACM2")
        assert result == "acm.2"

    def test_returns_device_when_no_location_match(self):
        ports = [SimpleNamespace(device="/dev/ttyUSB1", hwid="NOLOC", description="ACE")]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager._get_usb_location_for_port("/dev/ttyUSB1")
        assert result == "/dev/ttyUSB1"

    def test_returns_none_when_port_not_found(self):
        self.serial_mod.tools.list_ports.comports = lambda: []
        assert self.manager._get_usb_location_for_port("/dev/missing") is None

    def test_returns_none_when_port_not_in_list(self):
        ports = [SimpleNamespace(device="/dev/ttyUSB1", hwid="LOCATION=1-1.1", description="ACE")]
        self.serial_mod.tools.list_ports.comports = lambda: ports
        assert self.manager._get_usb_location_for_port("/dev/ttyUSB9") is None


class TestGetUsbTopologyPosition:
    """Tests for get_usb_topology_position."""

    def setup_method(self):
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager
            self.manager = AceSerialManager(gcode=Mock(), reactor=Mock(), ace_enabled=False)

    def test_returns_none_when_no_location(self):
        """Should return None when USB location is not set."""
        self.manager._usb_location = None
        assert self.manager.get_usb_topology_position() is None

    def test_returns_none_when_no_hyphen(self):
        """Should return None when location doesn't contain hyphen."""
        self.manager._usb_location = "acm.0"
        assert self.manager.get_usb_topology_position() is None

    def test_calculates_depth_simple(self):
        """Should calculate depth for simple USB location."""
        self.manager._usb_location = "2-2.3"
        assert self.manager.get_usb_topology_position() == 2

    def test_calculates_depth_complex(self):
        """Should calculate depth for multi-level USB location."""
        self.manager._usb_location = "2-2.4.3"
        assert self.manager.get_usb_topology_position() == 3

    def test_calculates_depth_single_port(self):
        """Should calculate depth for single port."""
        self.manager._usb_location = "1-3"
        assert self.manager.get_usb_topology_position() == 1


class TestDwell:
    """Tests for dwell method."""

    def setup_method(self):
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager
            self.mock_reactor = Mock()
            self.mock_reactor.monotonic.return_value = 100.0
            self.manager = AceSerialManager(gcode=Mock(), reactor=self.mock_reactor, ace_enabled=False)

    def test_dwell_pauses_reactor(self):
        """Test dwell calls reactor.pause with correct delay."""
        self.manager.dwell(delay=2.5)

        self.mock_reactor.pause.assert_called_once_with(102.5)

    def test_dwell_default_delay(self):
        """Test dwell uses default delay of 1.0."""
        self.manager.dwell()

        self.mock_reactor.pause.assert_called_once_with(101.0)


class TestSustainedPortMiss:
    """Tracking how long a manager has failed to find any port."""

    def setup_method(self):
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager
            self.mock_reactor = Mock()
            self.mock_reactor.monotonic.return_value = 100.0
            self.manager = AceSerialManager(gcode=Mock(), reactor=self.mock_reactor, ace_enabled=False)

    def test_zero_when_never_missed(self):
        assert self.manager.sustained_port_miss_s(now=100.0) == 0.0

    def test_measures_elapsed_since_first_miss(self):
        # Simulate the auto_connect "no port found" branch recording the miss.
        self.manager._first_port_miss_time = 100.0
        assert self.manager.sustained_port_miss_s(now=135.0) == 35.0

    def test_uses_reactor_clock_when_now_omitted(self):
        self.manager._first_port_miss_time = 100.0
        self.mock_reactor.monotonic.return_value = 142.0
        assert self.manager.sustained_port_miss_s() == 42.0

    def test_reset_to_zero_after_clear(self):
        self.manager._first_port_miss_time = 100.0
        self.manager._first_port_miss_time = None  # cleared on successful connect
        assert self.manager.sustained_port_miss_s(now=200.0) == 0.0


class TestCrcCalculation:
    """Test CRC-16 calculation."""

    def setup_method(self):
        """Create serial manager for CRC testing."""
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager
            
            mock_gcode = Mock()
            mock_reactor = Mock()
            
            self.manager = AceSerialManager(
                gcode=mock_gcode,
                reactor=mock_reactor,
                instance_num=0,
                ace_enabled=False
            )

    def test_crc_empty_buffer(self):
        """CRC of empty buffer."""
        result = self.manager._calc_crc(b'')
        assert result == 0xFFFF  # Initial value, no bytes processed

    def test_crc_deterministic(self):
        """Same input should always produce same CRC."""
        payload = b'{"method":"get_status"}'
        crc1 = self.manager._calc_crc(payload)
        crc2 = self.manager._calc_crc(payload)
        assert crc1 == crc2

    def test_crc_different_for_different_input(self):
        """Different payloads should produce different CRCs."""
        crc1 = self.manager._calc_crc(b'{"method":"get_status"}')
        crc2 = self.manager._calc_crc(b'{"method":"get_info"}')
        assert crc1 != crc2

    def test_crc_is_16bit(self):
        """CRC result should fit in 16 bits."""
        payload = b'test payload data here'
        crc = self.manager._calc_crc(payload)
        assert 0 <= crc <= 0xFFFF


class TestReader:
    """Branch coverage for _reader."""

    def setup_method(self):
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager, SerialException

            self.mock_gcode = Mock()
            self.mock_reactor = Mock()
            self.mock_reactor.NOW = 10.0
            self.mock_reactor.NEVER = 999.0
            self.mock_reactor.register_timer = Mock()
            self.mock_reactor.pause = Mock()
            self.mock_reactor.monotonic = Mock(return_value=0.0)
            self.SerialException = SerialException

            self.manager = AceSerialManager(
                gcode=self.mock_gcode,
                reactor=self.mock_reactor,
                instance_num=0,
                ace_enabled=True
            )
        self.manager._serial = Mock()
        self.manager.reconnect = Mock()
        self.manager.dispatch_response = Mock(return_value=(None, False))
        self.manager._status_update_callback = Mock()
        self.manager.read_buffer = bytearray()

    def _make_frame(self, payload_dict):
        payload = json.dumps(payload_dict).encode('utf-8')
        payload_len = len(payload)
        crc = struct.pack('<H', self.manager._calc_crc(payload))
        return b'\xFF\xAA' + struct.pack('<H', payload_len) + payload + crc + b'\xFE'

    def test_serial_exception_disabled_stops_timer(self):
        self.manager._ace_pro_enabled = False
        import ace.serial_manager as sm
        sm.SerialException = BaseException
        def boom(size=None):
            raise BaseException("boom")
        self.manager._serial.read = Mock(side_effect=boom)

        ret = self.manager._reader(eventtime=0.0)

        assert ret == self.mock_reactor.NEVER
        assert any("ACE Pro disabled" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)
        self.manager.reconnect.assert_not_called()

    def test_serial_exception_schedules_reconnect(self):
        self.manager._ace_pro_enabled = True
        import ace.serial_manager as sm
        sm.SerialException = BaseException
        def boom(size=None):
            raise BaseException("boom")
        self.manager._serial.read = Mock(side_effect=boom)
        self.manager.connect_timer = None

        ret = self.manager._reader(eventtime=0.0)

        assert ret == self.mock_reactor.NOW + 1.5
        self.manager.reconnect.assert_called_once_with()
        assert any("Scheduling reconnect" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_serial_exception_already_scheduled_logs_and_stops(self):
        self.manager._ace_pro_enabled = True
        import ace.serial_manager as sm
        sm.SerialException = BaseException
        def boom(size=None):
            raise BaseException("boom")
        self.manager._serial.read = Mock(side_effect=boom)
        self.manager.connect_timer = Mock()  # Not None, so already scheduled

        ret = self.manager._reader(eventtime=0.0)

        assert ret == self.mock_reactor.NEVER
        self.manager.reconnect.assert_not_called()
        assert any("Scheduling reconnect (already scheduled)" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_empty_read_reschedules_timer(self):
        self.manager._serial.read.return_value = b""

        ret = self.manager._reader(eventtime=1.0)

        assert ret == 1.0 + 0.05

    def test_process_valid_frame_calls_callback(self):
        frame = self._make_frame({"ok": 1})
        self.manager._serial.read.return_value = frame
        cb = Mock()
        self.manager.dispatch_response.return_value = (cb, True)
        self.manager._status_debug_logging = True

        ret = self.manager._reader(eventtime=2.0)

        assert ret == 2.0 + 0.05
        cb.assert_called_once()
        self.manager._status_update_callback.assert_called_once()

    def test_resync_skips_junk(self):
        frame = self._make_frame({"ok": 1})
        raw = b"\x00\x01\x02" + frame
        self.manager._serial.read.return_value = raw

        ret = self.manager._reader(eventtime=3.0)

        assert ret == 3.0 + 0.05
        assert any("Resync: skipping" in args[0] or "Resync: dropped junk" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_invalid_tail_resyncs(self):
        payload = b'{"ok":1}'
        payload_len = len(payload)
        bad_tail = b'\xFF\xAA' + struct.pack('<H', payload_len) + payload + struct.pack('<H', self.manager._calc_crc(payload)) + b'\x00'
        self.manager._serial.read.return_value = bad_tail + b'\xFF'  # extra data to keep loop running

        ret = self.manager._reader(eventtime=4.0)

        assert ret == 4.0 + 0.05
        assert any("Invalid frame tail" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_json_decode_error(self):
        payload = b'{"not json'  # malformed
        crc = struct.pack('<H', self.manager._calc_crc(payload))
        frame = b'\xFF\xAA' + struct.pack('<H', len(payload)) + payload + crc + b'\xFE'
        self.manager._serial.read.return_value = frame

        self.manager._reader(eventtime=5.0)

        assert any("JSON decode error" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_unsolicited_message_logged(self):
        frame = self._make_frame({"id": 99})
        self.manager._serial.read.return_value = frame
        self.manager.dispatch_response.return_value = (None, False)

        self.manager._reader(eventtime=6.0)

        # Check for new unsolicited format: "UNSOLICITED (ID=99, current_id=...)"
        assert any("UNSOLICITED" in args[0] and "ID=99" in args[0] 
                  for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_unsolicited_callback_handles_response_without_logging(self):
        frame = self._make_frame({"id": 77, "result": "ok"})
        self.manager._serial.read.return_value = frame
        self.manager.dispatch_response.return_value = (None, False)
        self.manager.unsolicited_response_callback = Mock(return_value=True)
        self.manager._track_comm_unsolicited = Mock()

        self.manager._reader(eventtime=6.0)

        self.manager.unsolicited_response_callback.assert_called_once_with({"id": 77, "result": "ok"})
        self.manager._track_comm_unsolicited.assert_not_called()
        assert not any("UNSOLICITED" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_new_response_after_disconnect_logs_warning(self):
        """Responses without callback should be logged as unsolicited."""
        # Response with ID 55 arrives (no callback)
        frame = self._make_frame({"id": 55, "result": "ok"})
        self.manager._serial.read.return_value = frame
        self.manager.dispatch_response.return_value = (None, False)
        
        self.manager._reader(eventtime=6.0)
        
        # Should log unsolicited message with new format
        assert any("UNSOLICITED" in args[0] and "ID=55" in args[0]
                  for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_invalid_crc_resets_frame(self):
        payload = b'{"ok":1}'
        payload_len = len(payload)
        bad_crc_frame = b'\xFF\xAA' + struct.pack('<H', payload_len) + payload + b'\x00\x00' + b'\xFE'
        self.manager._serial.read.return_value = bad_crc_frame

        ret = self.manager._reader(eventtime=3.0)

        assert ret == 3.0 + 0.05
        assert any("Invalid CRC" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)
        self.manager.dispatch_response.assert_not_called()


class TestWriter:
    """Branch coverage for _writer."""

    def setup_method(self):
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager

            self.mock_gcode = Mock()
            self.mock_reactor = Mock()
            self.mock_reactor.NOW = 10.0
            self.mock_reactor.NEVER = 999.0
            self.mock_reactor.monotonic.return_value = 0.0
            self.mock_reactor.register_timer = Mock()
            self.mock_reactor.pause = Mock()

            self.manager = AceSerialManager(
                gcode=self.mock_gcode,
                reactor=self.mock_reactor,
                instance_num=0,
                ace_enabled=True
            )
        self.manager._send_frame = Mock()
        self.manager.get_pending_request = Mock(return_value=(None, None))

    def test_timeouts_call_callbacks_and_remove(self):
        calls = []
        def cb(response=None):
            calls.append(response)
        self.manager.inflight = {1: 0.0}
        self.manager._callback_map = {1: cb}
        self.manager.timeout_s = 1.0
        self.mock_reactor.monotonic.return_value = 2.0

        ret = self.manager._writer(eventtime=0.0)

        assert ret == 0.0 + 0.1
        assert calls == [None]
        assert 1 not in self.manager.inflight

    def test_timeout_callback_error_logged(self):
        def cb(response=None):
            raise RuntimeError("fail")
        self.manager.inflight = {1: 0.0}
        self.manager._callback_map = {1: cb}
        self.manager.timeout_s = 1.0
        self.mock_reactor.monotonic.return_value = 2.0

        self.manager._writer(eventtime=0.0)

        assert any("Callback error" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_window_full_skips_send(self):
        self.manager.inflight = {i: 0.0 for i in range(self.manager.WINDOW_SIZE)}
        self.manager.get_pending_request = Mock()

        self.manager._writer(eventtime=0.0)

        self.manager.get_pending_request.assert_not_called()
        self.manager._send_frame.assert_not_called()

    def test_idle_not_long_enough_skips_status(self):
        self.manager.inflight = {}
        self.manager._last_status_request_time = 0.9
        self.manager.get_pending_request.return_value = (None, None)
        self.mock_reactor.monotonic.return_value = 1.0

        self.manager._writer(eventtime=0.0)

        self.manager._send_frame.assert_not_called()


class TestConnectionLifecycle:
    """Additional coverage for connection, queues, and send logic."""

    def setup_method(self):
        self.serial_patch = patch('ace.serial_manager.serial')
        self.serial_mod = self.serial_patch.start()
        self.serial_mod.SerialTimeoutException = type("Timeout", (Exception,), {})
        self.serial_mod.SerialException = Exception
        self.serial_mod.tools = Mock()
        self.serial_mod.tools.list_ports = Mock()

        from ace.serial_manager import AceSerialManager

        self.mock_gcode = Mock()
        self.mock_reactor = Mock()
        self.mock_reactor.NOW = 0.0
        self.mock_reactor.NEVER = 999.0
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.pause = Mock()
        self.mock_reactor.register_timer = Mock()
        self.mock_reactor.unregister_timer = Mock()

        self.manager = AceSerialManager(
            gcode=self.mock_gcode,
            reactor=self.mock_reactor,
            instance_num=0,
            ace_enabled=True
        )
        self.manager._serial = Mock()
        self.manager._serial_lock = Mock(__enter__=Mock(return_value=None), __exit__=Mock(return_value=False))
        self.manager._send_frame = Mock()

    def teardown_method(self):
        self.serial_patch.stop()

    def test_enable_disable_toggle(self):
        self.manager._ace_pro_enabled = False
        self.manager._baud = 57600
        self.manager.connect_to_ace = Mock()

        self.manager.enable_ace_pro()

        self.manager.connect_to_ace.assert_called_once()
        assert self.manager._ace_pro_enabled is True

        self.manager.disconnect = Mock()
        self.manager.disable_ace_pro()
        self.manager.disconnect.assert_called_once()
        assert self.manager._ace_pro_enabled is False

    def test_enable_ace_pro_when_already_enabled_no_reconnect(self):
        self.manager._ace_pro_enabled = True
        self.manager.connect_to_ace = Mock()

        self.manager.enable_ace_pro()

        self.manager.connect_to_ace.assert_not_called()

    def test_connect_to_ace_disabled_logs(self):
        self.manager._ace_pro_enabled = False
        self.manager.connect_to_ace(115200)
        assert any("ACE Pro disabled" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_connect_retry_backoff_cycles(self):
        captured = {}
        def register_timer(cb, when):
            captured["cb"] = cb
            captured["when"] = when
            return "timer"
        self.mock_reactor.register_timer.side_effect = register_timer
        self.manager._reconnect_backoff = 5.0
        self.manager.auto_connect = Mock(return_value=False)
        self.mock_reactor.monotonic.return_value = 10.0

        self.manager.connect_to_ace(115200)
        cb = captured["cb"]
        ret1 = cb(0.0)
        assert ret1 > 0.0
        assert self.manager._reconnect_backoff > 5.0
        assert len(self.manager._reconnect_timestamps) == 1

    def test_find_com_port_requires_enough_devices(self):
        # Only one device but instance 1 requested -> returns None
        ports = [SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=2-2.3")]
        self.serial_mod.tools.list_ports.comports = lambda: ports  # Correct mock: target the actual method used

        result = self.manager.find_com_port("ACE", 1)

        assert result is None

    def test_find_com_port_selects_by_current_sort_order(self):
        # Two devices; order is re-derived from current USB locations on
        # every call (no persisted "expected topology" needed or consulted).
        p0 = SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=2-2.3")
        p1 = SimpleNamespace(device="/dev/ttyACM1", description="ACE", hwid="LOCATION=2-2.4.3")
        self.serial_mod.tools.list_ports.comports = lambda: [p1, p0]

        result = self.manager.find_com_port("ACE", 1)

        assert result == "/dev/ttyACM1"

    def test_reconnect_disabled_skips(self):
        self.manager._ace_pro_enabled = False
        self.manager.reconnect()
        assert any("not reconnecting" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_reconnect_success_resets_backoff(self):
        captured = {}
        def register_timer(cb, when):
            captured["cb"] = cb
            return "timer"
        self.mock_reactor.register_timer.side_effect = register_timer
        self.manager.auto_connect = Mock(return_value=True)
        self.manager._reconnect_backoff = 10.0
        self.manager.reconnect()
        ret = captured["cb"](0.0)
        assert ret == self.mock_reactor.NEVER
        assert self.manager._reconnect_backoff == self.manager.RECONNECT_BACKOFF_MIN
    
    def test_ensure_connect_timer_schedules_when_needed(self):
        self.manager._ace_pro_enabled = True
        self.manager._connected = False
        self.manager.connect_timer = None
        self.manager.reconnect = Mock()

        self.manager.ensure_connect_timer()

        self.manager.reconnect.assert_called_once()

    def test_send_request_queue_full_logs(self):
        self.manager._queue = Mock()
        self.manager._queue.put.side_effect = queue.Full
        self.manager.send_request({"m": 1}, lambda r: None)
        assert any("Request queue full" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_send_request_normalizes_via_protocol(self):
        protocol = Mock()
        protocol.get_transport_spec.return_value = AceTransportSpec(
            mode="usb-topology",
            port_description="ACE",
        )
        protocol.normalize_request.return_value = {"method": "normalized"}
        self.manager.protocol = protocol

        callback = Mock()
        self.manager.send_request({"method": "original"}, callback)

        protocol.normalize_request.assert_called_once_with({"method": "original"})
        request, queued_callback = self.manager.get_pending_request()
        assert request == {"method": "normalized"}
        assert queued_callback is callback

    def test_send_frame_not_connected(self):
        self.manager._connected = False
        self.manager._send_frame({"method": "ping"})
        self.manager._serial.write.assert_not_called()

    def test_send_frame_timeout_clears_inflight(self):
        import ace.serial_manager as sm
        timeout_exc = type("Timeout", (Exception,), {})
        sm.serial.SerialTimeoutException = timeout_exc
        self.manager._connected = True
        self.manager._serial.write.side_effect = timeout_exc("boom")
        cb = Mock()
        self.manager.inflight = {1: 0.0}
        self.manager._callback_map = {1: cb}
        from ace.serial_manager import AceSerialManager
        AceSerialManager._send_frame(self.manager, {"id": 1, "method": "ping"})
        cb.assert_called_once_with(response=None)

    def test_send_frame_timeout_callback_error_logged(self):
        import ace.serial_manager as sm
        timeout_exc = type("Timeout", (Exception,), {})
        sm.serial.SerialTimeoutException = timeout_exc
        self.manager._connected = True
        self.manager._serial.write.side_effect = timeout_exc("boom")
        cb = Mock(side_effect=RuntimeError("cb boom"))
        self.manager.inflight = {1: 0.0}
        self.manager._callback_map = {1: cb}
        from ace.serial_manager import AceSerialManager
        AceSerialManager._send_frame(self.manager, {"id": 1, "method": "ping"})
        assert any("Timeout callback error" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_send_frame_generic_error_clears_inflight(self):
        self.manager._connected = True
        self.manager._serial.write.side_effect = RuntimeError("write fail")
        cb = Mock(side_effect=RuntimeError("cb fail"))
        self.manager.inflight = {2: 0.0}
        self.manager._callback_map = {2: cb}
        from ace.serial_manager import AceSerialManager
        AceSerialManager._send_frame(self.manager, {"id": 2, "method": "pong"})
        assert any("Serial write error" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)
        assert any("Error callback error" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_send_frame_success_writes_data(self):
        """Test _send_frame happy path - successfully sends data."""
        # Restore real _send_frame method for this test
        from ace.serial_manager import AceSerialManager
        self.manager._send_frame = AceSerialManager._send_frame.__get__(self.manager, AceSerialManager)
        
        self.manager._connected = True
        self.manager._serial = Mock()
        self.manager._serial.is_open = True
        self.manager._serial.write = Mock()
        self.manager._request_id = 10
        
        request = {"method": "ping"}
        self.manager._send_frame(request)
        
        # Verify write was called with properly formatted frame
        self.manager._serial.write.assert_called_once()
        sent_data = self.manager._serial.write.call_args[0][0]
        
        # Verify frame structure: header (2) + len (2) + payload + crc (2) + terminator (1)
        assert sent_data[0:2] == bytes([0xFF, 0xAA])  # Header
        assert sent_data[-1:] == b'\xFE'  # Terminator
        
        # Verify request got an ID assigned
        assert request['id'] == 10
        assert self.manager._request_id == 11

    def test_send_frame_with_existing_id_preserves_it(self):
        """Test _send_frame doesn't overwrite existing request ID."""
        # Restore real _send_frame method for this test
        from ace.serial_manager import AceSerialManager
        self.manager._send_frame = AceSerialManager._send_frame.__get__(self.manager, AceSerialManager)
        
        self.manager._connected = True
        self.manager._serial = Mock()
        self.manager._serial.is_open = True
        self.manager._serial.write = Mock()
        self.manager._request_id = 10
        
        request = {"method": "ping", "id": 99}
        self.manager._send_frame(request)
        
        # Verify ID was preserved
        assert request['id'] == 99
        assert self.manager._request_id == 10  # Not incremented

    def test_send_frame_request_id_wraps_at_16bit(self):
        """Test _request_id wraps from 0xFFFF back to 1 and callback_map key matches wire ID."""
        from ace.serial_manager import AceSerialManager
        self.manager._send_frame = AceSerialManager._send_frame.__get__(self.manager, AceSerialManager)

        self.manager._connected = True
        self.manager._serial = Mock()
        self.manager._serial.is_open = True
        self.manager._serial.write = Mock()

        # Set counter to max uint16 value
        self.manager._request_id = 0xFFFF

        request = {"method": "ping"}
        self.manager._send_frame(request)

        # ID assigned must equal 0xFFFF (lower 16 bits = 0xFFFF)
        assert request['id'] == 0xFFFF
        assert self.manager._request_id == 1

    def test_send_frame_request_id_after_wrap_stays_in_16bit(self):
        """Test IDs after rollover stay within 16-bit range and match wire encoding."""
        from ace.serial_manager import AceSerialManager
        self.manager._send_frame = AceSerialManager._send_frame.__get__(self.manager, AceSerialManager)

        self.manager._connected = True
        self.manager._serial = Mock()
        self.manager._serial.is_open = True
        self.manager._serial.write = Mock()

        # Simulate overflow: start just past uint16 boundary (should never happen now,
        # but verify the mask still protects against it defensively)
        for start_id in (0xFFFE, 0xFFFF, 1):
            self.manager._request_id = start_id
            request = {}
            self.manager._send_frame(request)
            assert 1 <= request['id'] <= 0xFFFF
            assert 1 <= self.manager._request_id <= 0xFFFF

    def test_writer_request_id_wraps_at_16bit(self):
        """Test _writer loop wraps _request_id correctly and callback_map key matches response ID."""
        from ace.serial_manager import AceSerialManager
        self.manager._writer = AceSerialManager._writer.__get__(self.manager, AceSerialManager)
        self.manager._send_frame = Mock()  # Don't actually write to serial

        self.manager._connected = True
        self.manager._serial = Mock()
        self.manager._serial.is_open = True
        self.manager._request_id = 0xFFFF

        cb = Mock()
        self.manager._queue.put([{"method": "ping"}, cb])

        self.manager._writer(0)

        # callback_map key must be 0xFFFF (the wire ID sent)
        assert 0xFFFF in self.manager._callback_map
        assert self.manager._request_id == 1

    def test_connect_handles_serial_exception(self):
        # Force SerialException path
        import ace.serial_manager as sm
        sm.serial.SerialException = Exception
        sm.serial.Serial = Mock(side_effect=sm.serial.SerialException("fail"))
        self.manager._serial = None
        self.manager.gcode.respond_info.reset_mock()
        self.manager.connect("bad", 115200)
        assert any("Connection failed" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)
    
    def test_auto_connect_no_port(self):
        self.serial_mod.tools.list_ports.comports = lambda: []
        self.manager.find_com_port = Mock(return_value=None)
        ok = self.manager.auto_connect(0, 115200)
        assert ok is False
        assert any("No ACE device found" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_auto_connect_success_returns_true(self):
        """Test successful auto_connect path - finds port, connects, sends get_info."""
        self.serial_mod.tools.list_ports.comports = lambda: []
        self.manager.find_com_port = Mock(return_value="/dev/ttyACM0")
        self.manager.connect = Mock(return_value=True)
        self.manager._get_usb_location_for_port = Mock(return_value="2-2.3")
        self.manager._get_port_description_for_port = Mock(return_value="ACE")
        self.manager.send_request = Mock()

        ok = self.manager.auto_connect(0, 115200)

        assert ok is True
        self.manager.find_com_port.assert_called_once_with('ACE', 0, ports=[])
        self.manager.connect.assert_called_once_with("/dev/ttyACM0", 115200)
        self.manager.send_request.assert_called_once()
        # Verify get_info request structure
        call_args = self.manager.send_request.call_args
        assert call_args[1]['request'] == {"method": "get_info"}
        assert callable(call_args[1]['callback'])

    def test_find_connection_port_uses_shared_bus_index_zero(self):
        self.manager.find_com_port = Mock(return_value="/dev/ttyUSB-bus")
        protocol = Mock()
        protocol.get_transport_spec.return_value = AceTransportSpec(
            mode="rs485-bus",
            port_description="USB Single Serial",
            shared_bus=True,
            topology_validation=False,
        )
        self.manager.protocol = protocol

        port = self.manager.find_connection_port(instance=3)

        assert port == "/dev/ttyUSB-bus"
        self.manager.find_com_port.assert_called_once_with("USB Single Serial", 0, ports=None)

    def test_auto_connect_enumerates_ports_only_once(self):
        """auto_connect() must enumerate serial ports via comports() exactly ONCE
        per attempt and reuse the result for port lookup, USB location, and
        description - instead of calling the (blocking, synchronous) comports()
        three separate times. Redundant enumeration runs on the reactor thread
        during every reconnect attempt and can stall step generation, which is
        implicated in an observed "Timer too close" MCU shutdown during a live
        ACE reconnect storm.
        """
        call_count = {"n": 0}
        ports = [SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=2-2.3")]

        def counting_comports():
            call_count["n"] += 1
            return ports

        self.serial_mod.tools.list_ports.comports = counting_comports

        # Use the *real* find_com_port / _get_usb_location_for_port /
        # _get_port_description_for_port implementations - only connect() is
        # stubbed, since it doesn't touch comports().
        self.manager.connect = Mock(return_value=True)
        self.manager.send_request = Mock()
        protocol = Mock()
        protocol.get_transport_spec.return_value = AceTransportSpec(
            mode="usb-topology",
            port_description="ACE",
        )
        self.manager.protocol = protocol

        ok = self.manager.auto_connect(0, 115200)

        assert ok is True
        assert call_count["n"] == 1, (
            f"expected exactly 1 comports() enumeration per auto_connect(), got {call_count['n']}"
        )

    def test_auto_connect_shared_bus_defers_get_info_to_bus_session(self):
        self.serial_mod.tools.list_ports.comports = lambda: []
        self.manager.find_connection_port = Mock(return_value="/dev/ttyUSB-bus")
        self.manager.connect = Mock(return_value=True)
        self.manager._get_usb_location_for_port = Mock(return_value="2-2.5")
        self.manager._get_port_description_for_port = Mock(return_value="USB Single Serial")
        self.manager.send_request = Mock()
        protocol = Mock()
        protocol.get_transport_spec.return_value = AceTransportSpec(
            mode="rs485-bus",
            port_description="USB Single Serial",
            shared_bus=True,
            topology_validation=False,
        )
        protocol.build_get_info_request.return_value = {"method": "get_info"}
        self.manager.protocol = protocol

        ok = self.manager.auto_connect(2, 230400)

        assert ok is True
        self.manager.connect.assert_called_once_with("/dev/ttyUSB-bus", 230400)
        self.manager.send_request.assert_not_called()

    def test_auto_connect_connect_failure_returns_false(self):
        """Test auto_connect when connect() fails."""
        self.serial_mod.tools.list_ports.comports = lambda: []
        self.manager.find_com_port = Mock(return_value="/dev/ttyACM0")
        self.manager.connect = Mock(return_value=False)
        self.manager._get_usb_location_for_port = Mock(return_value="2-2.3")
        self.manager._get_port_description_for_port = Mock(return_value="ACE")

        ok = self.manager.auto_connect(0, 115200)

        assert ok is False
        assert any("Failed to connect" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_connect_on_connect_callback_error_logged(self):
        # Cover on_connect callback exception path
        with patch('ace.serial_manager.serial') as mock_serial_mod, \
             patch('ace.serial_manager.logging') as mock_logging:
            mock_serial_mod.SerialTimeoutException = type("Timeout", (Exception,), {})
            mock_serial_mod.SerialException = Exception
            mock_serial_mod.Serial.return_value.is_open = True
            from ace.serial_manager import AceSerialManager
            mgr = AceSerialManager(self.mock_gcode, self.mock_reactor, 0, True)
            mgr.on_connect_callback = Mock(side_effect=RuntimeError("boom"))
            mgr.connect("/dev/ttyACM0", 115200)
            assert mgr.on_connect_callback.called
            mock_logging.warning.assert_called_once()
            args, kwargs = mock_logging.warning.call_args
            assert "on_connect callback error" in args[0]

    def test_connect_registers_timers_and_heartbeat(self):
        with patch('ace.serial_manager.serial') as mock_serial_mod:
            mock_serial_mod.SerialTimeoutException = type("Timeout", (Exception,), {})
            mock_serial_mod.SerialException = Exception
            mock_serial_mod.Serial.return_value.is_open = True
            from ace.serial_manager import AceSerialManager
            mgr = AceSerialManager(self.mock_gcode, self.mock_reactor, 0, True)
            mgr.start_heartbeat = Mock()
            mgr.connect("/dev/ttyACM0", 115200)
            assert mgr.writer_timer is not None
            assert mgr.reader_timer is not None
            mgr.start_heartbeat.assert_called_once()

    def test_fills_window_and_sends_requests(self):
        req = {"method": "ping"}
        cb = Mock()
        self.manager.get_pending_request = Mock(side_effect=[(req, cb), (None, None)])
        self.mock_reactor.monotonic.return_value = 0.0

        self.manager._writer(eventtime=1.0)

        self.manager._send_frame.assert_called_once()
        sent_req = self.manager._send_frame.call_args[0][0]
        assert sent_req["method"] == "ping"
        assert "id" in sent_req
        assert self.manager._callback_map[sent_req["id"]] == cb

    def test_find_com_port_selects_correct_device_by_topology_order(self):
        # With multiple devices, instance 0 selects the root/first in
        # topology order regardless of enumeration/connection order, and
        # instance 1 selects the next - purely re-derived from current USB
        # locations each call, no persisted state involved.
        p0 = SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=1-1.2")  # Root
        p1 = SimpleNamespace(device="/dev/ttyACM1", description="ACE", hwid="LOCATION=1-1.4.3")  # Deeper
        self.serial_mod.tools.list_ports.comports = lambda: [p1, p0]  # Ports returned in reverse order

        result0 = self.manager.find_com_port("ACE", 0)
        assert result0 == "/dev/ttyACM0"  # Root device

        result1 = self.manager.find_com_port("ACE", 1)
        assert result1 == "/dev/ttyACM1"  # Deeper device

    def test_find_com_port_handles_swapped_acm_assignments(self):
        # Enumeration order (/dev/ttyACMx) must not affect which physical
        # unit maps to which instance - only USB LOCATION= does.
        p0 = SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=1-1.4")  # Deeper, but ACM0
        p1 = SimpleNamespace(device="/dev/ttyACM1", description="ACE", hwid="LOCATION=1-1.2")  # Root, but ACM1
        self.serial_mod.tools.list_ports.comports = lambda: [p0, p1]

        result0 = self.manager.find_com_port("ACE", 0)
        assert result0 == "/dev/ttyACM1"  # Root location, regardless of ACM number

        result1 = self.manager.find_com_port("ACE", 1)
        assert result1 == "/dev/ttyACM0"  # Deeper location, regardless of ACM number

    def test_find_com_port_re_resolves_after_enumeration_change(self):
        # If the same physical unit re-enumerates at a different /dev/ttyACMx,
        # find_com_port must still resolve correctly on the next call since
        # order is always re-derived, never cached.
        p0 = SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=1-1.2")
        self.serial_mod.tools.list_ports.comports = lambda: [p0]
        result_before = self.manager.find_com_port("ACE", 0)
        assert result_before == "/dev/ttyACM0"

        p0_renumbered = SimpleNamespace(device="/dev/ttyACM7", description="ACE", hwid="LOCATION=1-1.2")
        self.serial_mod.tools.list_ports.comports = lambda: [p0_renumbered]
        result_after = self.manager.find_com_port("ACE", 0)
        assert result_after == "/dev/ttyACM7"

    def test_writer_exception_reports_error(self):
        self.manager.get_pending_request = Mock(side_effect=RuntimeError("boom"))

        ret = self.manager._writer(eventtime=3.0)

        assert ret == 3.0 + 0.1
        assert any("Write error" in args[0] or "boom" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_connect_callback_respects_disable(self):
        self.manager._ace_pro_enabled = True
        self.manager._reconnect_backoff = 0
        callbacks = {}
        def register_timer(cb, when):
            callbacks['cb'] = cb
            return "timer"
        self.mock_reactor.register_timer.side_effect = register_timer
        self.manager.auto_connect = Mock(return_value=False)
        self.manager.connect_to_ace(115200)
        cb = callbacks['cb']
        self.manager._ace_pro_enabled = False
        self.mock_reactor.NEVER = "never"
        assert cb(0.0) == "never"

    def test_reconnect_callback_respects_disable(self):
        callbacks = {}
        def register_timer(cb, when):
            callbacks['cb'] = cb
            return "timer"
        self.mock_reactor.register_timer.side_effect = register_timer
        self.manager._ace_pro_enabled = True
        self.manager._reconnect_backoff = 0
        self.mock_reactor.NEVER = "never"
        self.manager.reconnect()
        cb = callbacks['cb']
        self.manager._ace_pro_enabled = False
        assert cb(0.0) == "never"

    def test_connect_success_unregisters_connect_timer_and_calls_on_connect(self):
        self.manager.connect_timer = "connect_timer"
        self.manager.writer_timer = None
        self.manager.reader_timer = None
        self.manager.on_connect_callback = Mock()
        self.manager.reactor.register_timer.side_effect = ["writer", "reader"]
        self.manager.start_heartbeat = Mock()
        # Patch serial.Serial to return mock with is_open True
        serial_obj = Mock(is_open=True)
        with patch('ace.serial_manager.SerialException', Exception):
            with patch('ace.serial_manager.serial.Serial', return_value=serial_obj):
                connected = self.manager.connect("/dev/ttyACM0", 115200)

        assert connected is True
        self.manager.reactor.unregister_timer.assert_called_with("connect_timer")
        self.manager.on_connect_callback.assert_called_once()

    def test_connect_success_claims_port_in_registry(self):
        from ace import serial_manager

        self.manager.writer_timer = None
        self.manager.reader_timer = None
        self.manager.reactor.register_timer.side_effect = ["writer", "reader"]
        self.manager.start_heartbeat = Mock()
        serial_obj = Mock(is_open=True)
        with patch('ace.serial_manager.SerialException', Exception):
            with patch('ace.serial_manager.serial.Serial', return_value=serial_obj):
                connected = self.manager.connect("/dev/ttyACM0", 115200)

        assert connected is True
        assert serial_manager._CONNECTED_PORTS["/dev/ttyACM0"] == self.manager.instance_num

    def test_disconnect_releases_claimed_port(self):
        from ace import serial_manager

        self.manager._port = "/dev/ttyACM0"
        serial_manager._CONNECTED_PORTS["/dev/ttyACM0"] = self.manager.instance_num
        self.manager._serial = Mock(is_open=True)
        self.manager.writer_timer = None
        self.manager.reader_timer = None
        self.manager.connect_timer = None

        self.manager.disconnect()

        assert "/dev/ttyACM0" not in serial_manager._CONNECTED_PORTS

    def test_disconnect_does_not_release_port_claimed_by_another_instance(self):
        """Guards against a race where instance A's disconnect() runs after
        instance B has already re-claimed the same port path."""
        from ace import serial_manager

        self.manager._port = "/dev/ttyACM0"
        serial_manager._CONNECTED_PORTS["/dev/ttyACM0"] = 99  # owned by instance 99
        self.manager._serial = Mock(is_open=True)
        self.manager.writer_timer = None
        self.manager.reader_timer = None
        self.manager.connect_timer = None

        self.manager.disconnect()

        assert serial_manager._CONNECTED_PORTS["/dev/ttyACM0"] == 99

    def test_connect_failure_returns_false(self):
        class DummyExc(Exception):
            pass
        with patch('ace.serial_manager.SerialException', DummyExc):
            with patch('ace.serial_manager.serial.Serial', side_effect=DummyExc("fail")):
                connected = self.manager.connect("/dev/ttyBAD", 115200)
        assert connected is False
        assert self.manager._serial is None or not getattr(self.manager._serial, "is_open", False)

    def test_disconnect_handles_unregister_errors(self):
        self.manager._serial = Mock(is_open=True)
        self.manager.writer_timer = "w"
        self.manager.reader_timer = "r"
        self.manager.connect_timer = "c"
        self.manager.reactor.unregister_timer.side_effect = [Exception("w"), Exception("r"), Exception("c")]

        self.manager.disconnect()

        assert self.manager.writer_timer is None
        assert self.manager.reader_timer is None
        assert self.manager.connect_timer is None

    def test_recent_reconnect_counter_resets_after_stability(self):
        self.manager._reconnect_timestamps = [0.0]
        self.manager._last_connected_time = 1.0
        self.manager._counter_reset_time = -1
        self.mock_reactor.monotonic.return_value = self.manager.COUNTER_RESET_PERIOD + 5 + self.manager._last_connected_time

        count = self.manager._get_recent_reconnect_count()

        assert count == 0
        assert self.manager._counter_reset_time == self.mock_reactor.monotonic.return_value


class TestStatusUpdateChangeDetection:
    """Test status update change detection logic."""

    def setup_method(self):
        """Create serial manager for status update testing."""
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager
            
            self.mock_gcode = Mock()
            self.mock_reactor = Mock()
            
            self.manager = AceSerialManager(
                gcode=self.mock_gcode,
                reactor=self.mock_reactor,
                instance_num=0,
                ace_enabled=False,
                status_debug_logging=True
            )

    def test_detects_status_change(self):
        """Status change should be logged."""
        self.manager.last_status = "ready"
        self.manager.last_action = "none"
        
        response = {
            "result": {
                "status": "busy",
                "action": "feeding",
                "temp": 25,
                "slots": []
            }
        }
        
        self.manager._status_update_callback(response)
        
        assert self.manager.last_status == "busy"
        assert self.manager.last_action == "feeding"
        
        # Should have logged the change
        log_calls = [call[0][0] for call in self.mock_gcode.respond_info.call_args_list]
        assert any("STATUS CHANGE" in msg for msg in log_calls)

    def test_logs_get_status_raw_fields_when_present(self):
        """GET_STATUS raw_fields should be logged for ACE2 debugging."""
        response = {
            "result": {
                "status": "ready",
                "action": "none",
                "temp": 25,
                "raw_fields": {3: [(0, 25)], 4: [(0, 40)]},
                "slots": [],
            }
        }

        self.manager._status_update_callback(response)

        log_calls = [call[0][0] for call in self.mock_gcode.respond_info.call_args_list]
        assert any("GET_STATUS raw_fields" in msg for msg in log_calls)

    def test_no_log_when_status_unchanged(self):
        """No logging when status hasn't changed."""
        self.manager.last_status = "ready"
        self.manager.last_action = "none"
        self.manager.last_temp = 25
        
        response = {
            "result": {
                "status": "ready",
                "action": "none", 
                "temp": 25,
                "slots": []
            }
        }
        
        self.manager._status_update_callback(response)
        
        # Should not log status change
        log_calls = [call[0][0] for call in self.mock_gcode.respond_info.call_args_list]
        assert not any("STATUS CHANGE" in msg for msg in log_calls)

    def test_detects_slot_status_change(self):
        """Slot status change should be logged."""
        self.manager.last_status = "ready"
        self.manager.last_action = "none"
        self.manager.last_slot_states = {0: "empty"}
        
        response = {
            "result": {
                "status": "ready",
                "action": "none",
                "temp": 25,
                "slots": [{"index": 0, "status": "ready"}]
            }
        }
        
        self.manager._status_update_callback(response)
        
        assert self.manager.last_slot_states[0] == "ready"
        
        log_calls = [call[0][0] for call in self.mock_gcode.respond_info.call_args_list]
        assert any("SLOT[0] CHANGE" in msg for msg in log_calls)

    def test_detects_dryer_status_change(self):
        """Dryer status change should be logged."""
        self.manager.last_status = "ready"
        self.manager.last_action = "none"
        self.manager.last_dryer_status = "stop"
        
        response = {
            "result": {
                "status": "ready",
                "action": "none",
                "temp": 25,
                "slots": [],
                "dryer_status": {
                    "status": "drying",
                    "target_temp": 50,
                    "remain_time": 3600
                }
            }
        }
        
        self.manager._status_update_callback(response)
        
        assert self.manager.last_dryer_status == "drying"
        
        log_calls = [call[0][0] for call in self.mock_gcode.respond_info.call_args_list]
        assert any("DRYER" in msg for msg in log_calls)

    def test_detects_significant_temp_change(self):
        """Temperature change ≥5°C should be logged."""
        self.manager.last_status = "ready"
        self.manager.last_action = "none"
        self.manager.last_temp = 25
        
        response = {
            "result": {
                "status": "ready",
                "action": "none",
                "temp": 35,  # +10°C
                "slots": []
            }
        }
        
        self.manager._status_update_callback(response)
        
        log_calls = [call[0][0] for call in self.mock_gcode.respond_info.call_args_list]
        assert any("TEMP CHANGE" in msg for msg in log_calls)

    def test_ignores_small_temp_change(self):
        """Temperature change <5°C should not be logged."""
        self.manager.last_status = "ready"
        self.manager.last_action = "none"
        self.manager.last_temp = 25
        
        response = {
            "result": {
                "status": "ready",
                "action": "none",
                "temp": 27,  # +2°C
                "slots": []
            }
        }
        
        self.manager._status_update_callback(response)
        
        log_calls = [call[0][0] for call in self.mock_gcode.respond_info.call_args_list]
        assert not any("TEMP CHANGE" in msg for msg in log_calls)

    def test_handles_missing_result(self):
        """Callback should handle response without result gracefully."""
        response = {"error": "timeout"}
        
        # Should not raise
        self.manager._status_update_callback(response)

    def test_handles_empty_response(self):
        """Callback should handle empty response gracefully."""
        self.manager._status_update_callback({})
        self.manager._status_update_callback(None)


class TestQueueManagement:
    """Test request queue management."""

    def setup_method(self):
        """Create serial manager for queue testing."""
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager
            
            self.mock_gcode = Mock()
            self.mock_reactor = Mock()
            
            self.manager = AceSerialManager(
                gcode=self.mock_gcode,
                reactor=self.mock_reactor,
                instance_num=0,
                ace_enabled=False
            )
            # Enable so queue tests can actually enqueue requests
            self.manager._ace_pro_enabled = True

    def test_high_priority_dequeued_first(self):
        """High priority requests should be processed before normal."""
        normal_req = {"method": "get_status"}
        normal_cb = Mock()
        hp_req = {"method": "stop_feed"}
        hp_cb = Mock()
        
        # Queue normal first, then high priority
        self.manager.send_request(normal_req, normal_cb)
        self.manager.send_high_prio_request(hp_req, hp_cb)
        
        # Get should return high priority first
        req1, cb1 = self.manager.get_pending_request()
        req2, cb2 = self.manager.get_pending_request()
        
        assert req1["method"] == "stop_feed"
        assert req2["method"] == "get_status"

    def test_clear_queues_empties_all(self):
        """Clear queues should empty all pending requests."""
        self.manager.send_request({"method": "a"}, Mock())
        self.manager.send_request({"method": "b"}, Mock())
        self.manager.send_high_prio_request({"method": "c"}, Mock())
        
        self.manager.clear_queues()
        
        req, cb = self.manager.get_pending_request()
        assert req is None
        assert cb is None

    def test_clear_queue_handles_none(self):
        """_clear_queue should handle None queue gracefully."""
        # Should not raise exception
        self.manager._clear_queue(None)
        # Verify it returns early without error
        assert True

    def test_has_pending_requests_detects_queued(self):
        """has_pending_requests should detect queued items."""
        assert not self.manager.has_pending_requests()
        
        self.manager.send_request({"method": "test"}, Mock())
        
        assert self.manager.has_pending_requests()

    def test_has_pending_requests_detects_inflight(self):
        """has_pending_requests should detect in-flight items."""
        assert not self.manager.has_pending_requests()
        
        with self.manager._lock:
            self.manager.inflight[1] = 0.0
        
        assert self.manager.has_pending_requests()


class TestDispatchResponse:
    """Test response dispatching logic."""

    def setup_method(self):
        """Create serial manager for dispatch testing."""
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager

            self.mock_gcode = Mock()
            self.mock_reactor = Mock()
            self.mock_reactor.monotonic = Mock(return_value=0.0)

            self.manager = AceSerialManager(
                gcode=self.mock_gcode,
                reactor=self.mock_reactor,
                instance_num=0,
                ace_enabled=False
            )

    def test_dispatch_returns_callback_for_known_id(self):
        """Dispatch should return callback for matching request ID."""
        mock_cb = Mock()
        
        with self.manager._lock:
            self.manager._callback_map[42] = mock_cb
            self.manager.inflight[42] = 0.0
        
        response = {"id": 42, "result": "ok"}
        cb, was_solicited = self.manager.dispatch_response(response)
        
        assert cb == mock_cb
        assert was_solicited is True
        
        # Should be removed from maps
        assert 42 not in self.manager._callback_map
        assert 42 not in self.manager.inflight

    def test_dispatch_returns_none_for_unsolicited(self):
        """Dispatch should return None for unsolicited response."""
        response = {"id": 99, "result": "ok"}
        cb, was_solicited = self.manager.dispatch_response(response)
        
        assert cb is None
        assert was_solicited is False

    def test_dispatch_handles_missing_id(self):
        """Dispatch should handle response without ID."""
        response = {"result": "ok"}  # No ID
        cb, was_solicited = self.manager.dispatch_response(response)

        assert cb is None
        assert was_solicited is False


class TestDuplicateResponseDetection:
    """
    Regression (2xACE2 field log): two units sharing one bus device_id both
    answer every targeted request. The first reply consumes the callback; the
    second reply for the SAME request id used to be routed as a generic
    unsolicited response - feeding contradictory status/inventory from the
    ghost unit into runtime state (slot flip-flop, RFID churn) and hiding the
    identity collision. A reply for an already-answered request id must be
    recognized as a duplicate, dropped (never routed), and surfaced as a
    device_id-collision warning.
    """

    def setup_method(self):
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager

            self.mock_gcode = Mock()
            self.mock_reactor = Mock()
            self.mock_reactor.NOW = 10.0
            self.mock_reactor.NEVER = 999.0
            self.mock_reactor.register_timer = Mock()
            self.mock_reactor.monotonic = Mock(return_value=100.0)

            self.manager = AceSerialManager(
                gcode=self.mock_gcode,
                reactor=self.mock_reactor,
                instance_num=0,
                ace_enabled=True
            )
        self.manager._serial = Mock()
        self.manager._status_update_callback = Mock()
        self.manager.read_buffer = bytearray()
        # get_connection_status() calls ensure_connect_timer(); a scheduled
        # timer keeps it from disconnect()ing (which wipes the counters)
        self.manager.connect_timer = Mock()

    def _make_frame(self, payload_dict):
        payload = json.dumps(payload_dict).encode('utf-8')
        crc = struct.pack('<H', self.manager._calc_crc(payload))
        return b'\xFF\xAA' + struct.pack('<H', len(payload)) + payload + crc + b'\xFE'

    def _read_frames(self, *payloads):
        frames = b''.join(self._make_frame(p) for p in payloads)
        self.manager._serial.read.return_value = frames
        self.manager._reader(eventtime=1.0)

    def _log_lines(self):
        return [args[0] for args, _ in self.mock_gcode.respond_info.call_args_list]

    def test_duplicate_reply_is_dropped_and_flagged(self):
        cb = Mock()
        with self.manager._lock:
            self.manager._callback_map[7] = cb
            self.manager.inflight[7] = 0.0
        unsolicited_cb = Mock(return_value=True)
        self.manager.set_unsolicited_response_callback(unsolicited_cb)

        # Both units answer request 7: first reply solicited, second duplicate
        self._read_frames(
            {"id": 7, "command": "GET_STATUS", "result": "unit-a"},
            {"id": 7, "command": "GET_STATUS", "result": "unit-b"},
        )

        cb.assert_called_once()
        unsolicited_cb.assert_not_called()
        assert any("DUPLICATE" in line and "ID=7" in line for line in self._log_lines()), \
            "duplicate reply was not flagged as DUPLICATE"

    def test_duplicate_reply_warns_about_device_id_collision(self):
        cb = Mock()
        with self.manager._lock:
            self.manager._callback_map[7] = cb
            self.manager.inflight[7] = 0.0

        self._read_frames(
            {"id": 7, "command": "GET_STATUS", "result": "unit-a"},
            {"id": 7, "command": "GET_STATUS", "result": "unit-b"},
        )

        assert any("device_id" in line and "collision" in line for line in self._log_lines()), \
            "no identity-collision warning was emitted for a duplicate reply"

    def test_duplicate_replies_count_toward_comm_supervision(self):
        cb = Mock()
        with self.manager._lock:
            self.manager._callback_map[7] = cb
            self.manager.inflight[7] = 0.0

        self._read_frames(
            {"id": 7, "command": "GET_STATUS", "result": "unit-a"},
            {"id": 7, "command": "GET_STATUS", "result": "unit-b"},
        )

        assert len(self.manager._comm_unsolicited_timestamps) == 1, \
            "duplicate reply must still count toward Layer-1 comm supervision"
        status = self.manager.get_connection_status()
        assert status["supervision"]["duplicate_count"] == 1

    def test_broadcast_discover_reply_is_not_treated_as_duplicate(self):
        """
        Regression (hardware test log klippy(9)): DISCOVER_DEVICE is a
        broadcast - every unit on the bus answers the SAME request id, so a
        second reply is expected discovery data (the race loser), not an
        identity collision. The duplicate filter ran before the unsolicited
        router and swallowed exactly the reply the discovery capture needs:
        with ace_count=2 the discovery loop then sat at 'found 1/2 ... will
        retry' forever whenever one unit won both broadcasts.
        """
        cb = Mock()
        with self.manager._lock:
            self.manager._callback_map[3] = cb
            self.manager.inflight[3] = 0.0
        unsolicited_cb = Mock(return_value=True)
        self.manager.set_unsolicited_response_callback(unsolicited_cb)

        loser_reply = {
            "id": 3,
            "command": "DISCOVER_DEVICE",
            "result": {"uid1": 44, "uid2": 55, "uid3": 66},
        }
        self._read_frames(
            {"id": 3, "command": "DISCOVER_DEVICE",
             "result": {"uid1": 11, "uid2": 22, "uid3": 33}},
            loser_reply,
        )

        cb.assert_called_once()
        unsolicited_cb.assert_called_once_with(loser_reply)
        assert not any("DUPLICATE" in line for line in self._log_lines()), \
            "broadcast race-loser reply was dropped as a duplicate instead " \
            "of reaching the discovery capture"

    def test_never_dispatched_id_stays_generic_unsolicited(self):
        """A late reply for a timed-out request has no dispatched id on
        record - it must keep today's UNSOLICITED handling, not be
        misreported as a collision."""
        self._read_frames({"id": 55, "command": "GET_STATUS", "result": "late"})

        lines = self._log_lines()
        assert any("UNSOLICITED" in line and "ID=55" in line for line in lines)
        assert not any("DUPLICATE" in line for line in lines)

    def test_dispatched_id_expires_from_duplicate_window(self):
        cb = Mock()
        with self.manager._lock:
            self.manager._callback_map[7] = cb
            self.manager.inflight[7] = 0.0

        self._read_frames({"id": 7, "command": "GET_STATUS", "result": "unit-a"})

        # Second reply arrives long after the duplicate-detection window
        self.mock_reactor.monotonic.return_value = 100.0 + self.manager.RECENT_DISPATCH_WINDOW + 1.0
        self._read_frames({"id": 7, "command": "GET_STATUS", "result": "unit-b"})

        lines = self._log_lines()
        assert any("UNSOLICITED" in line and "ID=7" in line for line in lines)
        assert not any("DUPLICATE" in line for line in lines)


class TestSharedBusStatusDebugDemux:
    """
    Regression (2xACE2 field log #2, ace_count=2): on a shared bus the debug
    status tracker receives the interleaved GET_STATUS streams of every unit
    but kept ONE flat last-state - two healthy units with different slot
    occupancy produced a false 'SLOT CHANGE ready <-> empty' flip on every
    heartbeat (126 flips in ~5 min), labeled as one device. Change detection
    must be keyed by the response's device_id.
    """

    def setup_method(self):
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager

            self.mock_gcode = Mock()
            self.mock_reactor = Mock()
            self.mock_reactor.monotonic = Mock(return_value=100.0)

            self.manager = AceSerialManager(
                gcode=self.mock_gcode,
                reactor=self.mock_reactor,
                instance_num=0,
                ace_enabled=True
            )

    def _status_response(self, device_id, slot2_status):
        return {
            "id": 1,
            "command": "GET_STATUS",
            "device_id": device_id,
            "result": {
                "status": "ready",
                "action": "none",
                "temp": 25,
                "slots": [{"index": 2, "status": slot2_status}],
            },
        }

    def _change_lines(self):
        return [
            args[0] for args, _ in self.mock_gcode.respond_info.call_args_list
            if "SLOT[2] CHANGE" in args[0]
        ]

    def test_interleaved_device_status_streams_do_not_flip_flop(self):
        # Two heartbeat rounds of two healthy units with different occupancy
        for _ in range(2):
            self.manager._status_update_callback(self._status_response(1, "ready"))
            self.manager._status_update_callback(self._status_response(2, "empty"))

        changes = self._change_lines()
        assert len(changes) == 2, (
            "expected one initial SLOT CHANGE per device (baseline), got "
            f"{len(changes)}: interleaved device streams are tracked as one "
            f"device and flip-flop on every heartbeat: {changes}"
        )

    def test_raw_fields_dump_is_change_gated(self):
        """
        Regression (2xACE2 field log #2): the GET_STATUS raw_fields dump was
        logged for EVERY response - with two units that is 2 respond_info
        lines/sec forever, which (together with the flip-flop lines) saturated
        the gcode response pipe until BlockingIOError [Errno 11] in
        gcode._respond_raw. The dump must only be emitted when the payload
        actually changed for that device.
        """
        response = self._status_response(1, "ready")
        response["result"]["raw_fields"] = {1: [(0, 1)], 9: [(2, b'\x10\x02')]}

        for _ in range(3):
            self.manager._status_update_callback(response)

        raw_lines = [
            args[0] for args, _ in self.mock_gcode.respond_info.call_args_list
            if "raw_fields" in args[0]
        ]
        assert len(raw_lines) == 1, (
            f"unchanged raw_fields dumped {len(raw_lines)} times - floods the "
            f"console/response pipe at heartbeat rate"
        )

        # A real change must still be dumped
        changed = self._status_response(1, "ready")
        changed["result"]["raw_fields"] = {1: [(0, 1)], 9: [(2, b'')]}
        self.manager._status_update_callback(changed)

        raw_lines = [
            args[0] for args, _ in self.mock_gcode.respond_info.call_args_list
            if "raw_fields" in args[0]
        ]
        assert len(raw_lines) == 2

    def test_untagged_responses_keep_flat_state_tracking(self):
        """ACE1 responses carry no device_id - the legacy flat attributes
        must keep working (existing tests and tools poke them directly)."""
        response = {
            "id": 1,
            "result": {
                "status": "busy",
                "action": "feeding",
                "temp": 25,
                "slots": [],
            },
        }
        self.manager.last_status = "ready"
        self.manager.last_action = "none"

        self.manager._status_update_callback(response)

        assert self.manager.last_status == "busy"
        assert self.manager.last_action == "feeding"


class TestOnConnectCallback:
    """Test on_connect_callback functionality."""

    def setup_method(self):
        """Create serial manager for callback testing."""
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager
            
            self.mock_gcode = Mock()
            self.mock_reactor = Mock()
            
            self.manager = AceSerialManager(
                gcode=self.mock_gcode,
                reactor=self.mock_reactor,
                instance_num=0,
                ace_enabled=True
            )

    def test_on_connect_callback_initially_none(self):
        """on_connect_callback should start as None."""
        assert self.manager.on_connect_callback is None

    def test_set_on_connect_callback(self):
        """set_on_connect_callback should store the callback."""
        mock_callback = Mock()
        self.manager.set_on_connect_callback(mock_callback)
        assert self.manager.on_connect_callback == mock_callback

    def test_set_unsolicited_response_callback(self):
        """set_unsolicited_response_callback should store callback."""
        mock_callback = Mock()
        self.manager.set_unsolicited_response_callback(mock_callback)
        assert self.manager.unsolicited_response_callback == mock_callback

    @patch('ace.serial_manager.serial')
    def test_on_connect_callback_called_on_successful_connect(self, mock_serial_module):
        """on_connect_callback should be called after successful connection."""
        # Set up mock serial
        mock_serial = Mock()
        mock_serial.is_open = True
        mock_serial_module.Serial.return_value = mock_serial
        
        # Register callback
        mock_callback = Mock()
        self.manager.set_on_connect_callback(mock_callback)
        
        # Mock reactor timer registration
        self.manager.reactor.NOW = 0.0
        self.manager.reactor.register_timer = Mock(return_value="timer_handle")
        
        # Attempt connection
        result = self.manager.connect("/dev/ttyACM0", 115200)
        
        assert result is True
        mock_callback.assert_called_once()


class TestConnectionStability:
    """Test rate-based connection stability tracking."""

    def setup_method(self):
        """Create serial manager for stability testing."""
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager
            
            self.mock_gcode = Mock()
            self.mock_reactor = Mock()
            self.mock_reactor.monotonic.return_value = 1000.0
            
            self.manager = AceSerialManager(
                gcode=self.mock_gcode,
                reactor=self.mock_reactor,
                instance_num=0,
                ace_enabled=True
            )

    def test_initial_state_no_reconnects(self):
        """Initial state should have no reconnect timestamps."""
        assert len(self.manager._reconnect_timestamps) == 0
        assert self.manager._last_connected_time == 0.0

    def test_reconnect_adds_timestamp(self):
        """reconnect() records each connection loss, not just failed retries.

        If only failed connect attempts were counted, a link that dies every
        40s but reconnects successfully on the first try every time would
        keep the counter at 0 forever (observed in the field: 90 reconnects
        in an hour, all logged as "0 reconnects in last 180s"), so
        instability detection never triggered.
        """
        self.manager.reactor.register_timer = Mock(return_value="timer")
        self.manager.reactor.NOW = 0.0
        self.manager.reactor.monotonic.return_value = 1000.0

        self.manager.reconnect(delay=1)

        assert len(self.manager._reconnect_timestamps) == 1

    def test_succeed_then_die_storm_marks_connection_unstable(self):
        """A reconnect storm where every attempt succeeds must still trip
        instability detection once the threshold is reached."""
        self.manager.reactor.register_timer = Mock(return_value="timer")
        self.manager.reactor.NOW = 0.0
        # Every reconnect attempt succeeds instantly (device re-enumerated)
        self.manager.auto_connect = Mock(return_value=True)

        # Connection dies every 20s; reconnect succeeds each time
        threshold = self.manager.INSTABILITY_THRESHOLD
        for i in range(threshold):
            now = 1000.0 + i * 20.0
            self.manager.reactor.monotonic.return_value = now
            self.manager.reconnect(delay=1)

        # Currently connected and past the grace period, but the storm is
        # within the instability window - must NOT be considered stable
        last_death = 1000.0 + (threshold - 1) * 20.0
        self.manager._connected = True
        self.manager._serial = Mock()
        self.manager._serial.is_open = True
        self.manager._last_connected_time = last_death + 1.0
        self.manager.reactor.monotonic.return_value = (
            last_death + 1.0 + self.manager.STABILITY_GRACE_PERIOD + 5.0
        )

        assert self.manager._get_recent_reconnect_count() >= threshold
        assert self.manager.is_connection_stable() is False

    def test_callback_failures_tracked(self):
        """Failed callback retries should add timestamps."""
        # Capture callback
        captured_callback = None
        def capture_timer(callback, when):
            nonlocal captured_callback
            captured_callback = callback
            return "timer"
        
        self.manager.reactor.register_timer = capture_timer
        self.manager.reactor.NOW = 0.0
        self.manager.auto_connect = Mock(return_value=False)
        
        # Start reconnect (adds 1 timestamp for the connection loss itself)
        self.manager.reconnect(delay=1)

        # Simulate 3 failed callbacks
        for t in [1000.0, 1010.0, 1020.0]:
            self.manager.reactor.monotonic.return_value = t
            captured_callback(t)

        # 1 connection loss + 3 failed attempts
        assert len(self.manager._reconnect_timestamps) == 4

    def test_old_timestamps_pruned_during_callback(self):
        """Timestamps older than INSTABILITY_WINDOW should be pruned on reconnect."""
        # Capture callback
        captured_callback = None
        def capture_timer(callback, when):
            nonlocal captured_callback
            captured_callback = callback
            return "timer"
        
        self.manager.reactor.register_timer = capture_timer
        self.manager.reactor.NOW = 0.0
        self.manager.auto_connect = Mock(return_value=False)
        
        # First reconnect at 1000 (1 timestamp) plus a failed attempt (1 more)
        self.manager.reconnect(delay=1)
        self.manager.reactor.monotonic.return_value = 1000.0
        captured_callback(1000.0)

        assert len(self.manager._reconnect_timestamps) == 2

        # Another failed attempt 200 seconds later (window is 180s)
        self.manager.reactor.monotonic.return_value = 1200.0
        captured_callback(1200.0)

        # Old timestamps should be pruned, only new one remains
        assert len(self.manager._reconnect_timestamps) == 1
        assert self.manager._reconnect_timestamps[0] == 1200.0

    def test_is_connection_stable_requires_connected(self):
        """is_connection_stable should return False if not connected."""
        self.manager._connected = False
        self.manager._last_connected_time = 1000.0
        
        assert self.manager.is_connection_stable() is False

    def test_is_connection_stable_requires_grace_period(self):
        """is_connection_stable requires 30s grace period after connect."""
        self.manager._connected = True
        self.manager._serial = Mock()
        self.manager._serial.is_open = True
        self.manager._last_connected_time = 990.0  # Connected 10s ago
        self.manager.reactor.monotonic.return_value = 1000.0
        
        # Only 10 seconds connected, need 30
        assert self.manager.is_connection_stable() is False
        
        # After 30 seconds
        self.manager.reactor.monotonic.return_value = 1025.0
        assert self.manager.is_connection_stable() is True

    def test_is_connection_stable_fails_at_threshold_reconnects(self):
        """4 reconnects in the window must flag the connection unstable.

        Threshold is 4 because a link that dies every ~45s (observed field
        failure) only accumulates 3-5 events per 180s window - a threshold
        of 6 was never reached and sustained flapping went unreported.
        """
        self.manager._connected = True
        self.manager._serial = Mock()
        self.manager._serial.is_open = True
        self.manager._last_connected_time = 900.0  # Connected long ago
        self.manager.reactor.monotonic.return_value = 1000.0

        # 4 recent reconnects (at threshold)
        self.manager._reconnect_timestamps = [945.0, 950.0, 960.0, 970.0]

        assert self.manager.is_connection_stable() is False

    def test_is_connection_stable_with_few_reconnects(self):
        """is_connection_stable should be True below the reconnect threshold."""
        self.manager._connected = True
        self.manager._serial = Mock()
        self.manager._serial.is_open = True
        self.manager._last_connected_time = 900.0  # Connected long ago
        self.manager.reactor.monotonic.return_value = 1000.0

        # Only 3 reconnects (below threshold of 4)
        self.manager._reconnect_timestamps = [950.0, 960.0, 970.0]

        assert self.manager.is_connection_stable() is True

    @patch('ace.serial_manager.serial')
    def test_successful_connect_records_time(self, mock_serial_module):
        """Successful connection should record connect time."""
        mock_serial = Mock()
        mock_serial.is_open = True
        mock_serial_module.Serial.return_value = mock_serial
        
        self.manager.reactor.NOW = 0.0
        self.manager.reactor.register_timer = Mock(return_value="timer")
        self.manager.reactor.monotonic.return_value = 2000.0
        
        result = self.manager.connect("/dev/ttyACM0", 115200)
        
        assert result is True
        assert self.manager._last_connected_time == 2000.0

    def test_get_connection_status_returns_all_fields(self):
        """get_connection_status should return complete status dict."""
        self.manager._connected = True
        self.manager._serial = Mock()
        self.manager._serial.is_open = True
        self.manager._last_connected_time = 900.0
        self.manager._reconnect_timestamps = [950.0, 960.0]
        self.manager.reactor.monotonic.return_value = 1000.0
        
        status = self.manager.get_connection_status()
        
        assert status["connected"] is True
        assert status["stable"] is True  # 2 reconnects < 3 threshold, 100s > 30s grace
        assert status["recent_reconnects"] == 2
        assert status["time_connected"] == 100.0
        assert status["last_connected_time"] == 900.0
    
    def test_get_connection_status_schedules_timer_when_disconnected(self):
        """get_connection_status should ensure a reconnect timer when disconnected."""
        self.manager._connected = False
        self.manager._serial = None
        self.manager.connect_timer = None
        self.manager.ensure_connect_timer = Mock()
        
        self.manager.get_connection_status()
        
        self.manager.ensure_connect_timer.assert_called_once()

    def test_recent_reconnect_count_resets_counter_time(self):
        """_get_recent_reconnect_count should set reset time after stability."""
        self.manager._last_connected_time = 0.0
        self.manager._reconnect_timestamps = []
        self.manager._counter_reset_time = -1
        self.manager.reactor.monotonic.return_value = 200.0
        # Simulate long stable period
        self.manager._last_connected_time = 10.0
        self.manager.COUNTER_RESET_PERIOD = 50.0

        self.manager._get_recent_reconnect_count()

        assert self.manager._counter_reset_time == 200.0


class TestRetryLoopTimestampTracking:
    """Test that retry callbacks properly track timestamps."""

    def setup_method(self):
        """Create serial manager for retry loop testing."""
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager
            
            self.mock_gcode = Mock()
            self.mock_reactor = Mock()
            self.mock_reactor.monotonic.return_value = 1000.0
            self.mock_reactor.NOW = 0.0
            self.mock_reactor.NEVER = float('inf')
            
            self.manager = AceSerialManager(
                gcode=self.mock_gcode,
                reactor=self.mock_reactor,
                instance_num=0,
                ace_enabled=True
            )
            self.manager._baud = 115200

    def test_connect_to_ace_retry_loop_tracks_timestamps(self):
        """Each failed retry in connect_to_ace callback should add a timestamp."""
        # Capture the callback when register_timer is called
        captured_callback = None
        def capture_timer(callback, when):
            nonlocal captured_callback
            captured_callback = callback
            return "timer"
        
        self.manager.reactor.register_timer = capture_timer
        
        # Mock auto_connect to always fail
        self.manager.auto_connect = Mock(return_value=False)
        
        # Start connection
        self.manager.connect_to_ace(115200)
        
        assert captured_callback is not None
        
        # Simulate multiple retry callbacks (each one should add a timestamp)
        for i in range(6):
            self.mock_reactor.monotonic.return_value = 1000.0 + (i * 10)
            captured_callback(1000.0 + (i * 10))
        
        # Should have 6 timestamps from 6 failed attempts
        assert len(self.manager._reconnect_timestamps) == 6

    def test_reconnect_retry_loop_tracks_timestamps(self):
        """Each failed retry in reconnect callback should add a timestamp."""
        # Capture the callback when register_timer is called
        captured_callback = None
        def capture_timer(callback, when):
            nonlocal captured_callback
            captured_callback = callback
            return "timer"
        
        self.manager.reactor.register_timer = capture_timer
        
        # Mock auto_connect to always fail
        self.manager.auto_connect = Mock(return_value=False)
        
        # Initial reconnect call records the connection loss itself
        self.mock_reactor.monotonic.return_value = 1000.0
        self.manager.reconnect(delay=5)

        assert captured_callback is not None
        initial_count = len(self.manager._reconnect_timestamps)
        assert initial_count == 1  # the connection loss that triggered reconnect

        # Simulate 6 retry callbacks (each failure adds a timestamp)
        for i in range(6):
            self.mock_reactor.monotonic.return_value = 1010.0 + (i * 10)
            captured_callback(1010.0 + (i * 10))

        # 1 connection loss + 6 failed retries
        assert len(self.manager._reconnect_timestamps) == 7

    def test_successful_connect_stops_retry_loop(self):
        """Successful auto_connect should return NEVER to stop timer."""
        captured_callback = None
        def capture_timer(callback, when):
            nonlocal captured_callback
            captured_callback = callback
            return "timer"
        
        self.manager.reactor.register_timer = capture_timer
        
        # First 3 attempts fail, then succeed
        attempt_count = [0]
        def mock_auto_connect(instance, baud):
            attempt_count[0] += 1
            return attempt_count[0] >= 4  # Success on 4th attempt
        
        self.manager.auto_connect = mock_auto_connect
        
        self.manager.connect_to_ace(115200)
        
        # Simulate retries
        for i in range(3):
            self.mock_reactor.monotonic.return_value = 1000.0 + (i * 10)
            result = captured_callback(1000.0 + (i * 10))
            assert result != self.mock_reactor.NEVER  # Should continue
        
        # 4th attempt should succeed
        self.mock_reactor.monotonic.return_value = 1030.0
        result = captured_callback(1030.0)
        assert result == self.mock_reactor.NEVER  # Should stop

    def test_backoff_resets_on_successful_connect(self):
        """Backoff should reset to MIN after successful connect."""
        captured_callback = None
        def capture_timer(callback, when):
            nonlocal captured_callback
            captured_callback = callback
            return "timer"
        
        self.manager.reactor.register_timer = capture_timer
        
        # Set backoff to high value
        self.manager._reconnect_backoff = 30.0
        
        # Mock successful connect
        self.manager.auto_connect = Mock(return_value=True)
        
        self.manager.connect_to_ace(115200)
        captured_callback(1000.0)
        
        # Backoff should reset to MIN
        assert self.manager._reconnect_backoff == self.manager.RECONNECT_BACKOFF_MIN


class TestCommunicationSupervision:
    """Test communication health supervision functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        from ace.serial_manager import AceSerialManager
        
        self.mock_gcode = Mock()
        self.mock_reactor = Mock()
        self.mock_reactor.monotonic = Mock(return_value=100.0)
        self.mock_reactor.NOW = 10.0
        
        self.manager = AceSerialManager(
            gcode=self.mock_gcode,
            reactor=self.mock_reactor,
            instance_num=0,
            ace_enabled=True,
            supervision_enabled=True
        )
    
    def test_track_comm_timeout_records_timestamp(self):
        """Test that timeout tracking records timestamp and prunes old entries."""
        self.mock_reactor.monotonic.return_value = 100.0
        
        self.manager._track_comm_timeout()
        
        assert len(self.manager._comm_timeout_timestamps) == 1
        assert self.manager._comm_timeout_timestamps[0] == 100.0
    
    def test_track_comm_timeout_prunes_old_entries(self):
        """Test that timeout tracking prunes entries outside window."""
        # Add old timestamps
        self.manager._comm_timeout_timestamps = [50.0, 60.0, 70.0]
        
        # Current time = 100.0, window = 30.0, cutoff = 70.0
        self.mock_reactor.monotonic.return_value = 100.0
        self.manager._track_comm_timeout()
        
        # Should keep only entries > 70.0 (cutoff) plus new entry
        assert len(self.manager._comm_timeout_timestamps) == 1
        assert self.manager._comm_timeout_timestamps[0] == 100.0
    
    def test_track_comm_unsolicited_records_timestamp(self):
        """Test that unsolicited tracking records timestamp."""
        self.mock_reactor.monotonic.return_value = 200.0
        
        self.manager._track_comm_unsolicited()
        
        assert len(self.manager._comm_unsolicited_timestamps) == 1
        assert self.manager._comm_unsolicited_timestamps[0] == 200.0
    
    def test_track_comm_unsolicited_prunes_old_entries(self):
        """Test that unsolicited tracking prunes entries outside window."""
        # Add old timestamps
        self.manager._comm_unsolicited_timestamps = [150.0, 160.0, 170.0]
        
        # Current time = 200.0, window = 30.0, cutoff = 170.0
        self.mock_reactor.monotonic.return_value = 200.0
        self.manager._track_comm_unsolicited()
        
        # Should keep only entries > 170.0 plus new entry
        assert len(self.manager._comm_unsolicited_timestamps) == 1
        assert self.manager._comm_unsolicited_timestamps[0] == 200.0
    
    def test_check_communication_health_healthy_no_events(self):
        """Test that health check returns healthy with no events."""
        self.mock_reactor.monotonic.return_value = 100.0
        
        is_healthy, reason = self.manager._check_communication_health()
        
        assert is_healthy is True
        assert reason == "healthy"
    
    def test_check_communication_health_healthy_below_thresholds(self):
        """Test that health check returns healthy when below thresholds."""
        self.mock_reactor.monotonic.return_value = 100.0
        
        # Add events below thresholds (need 15 each)
        self.manager._comm_timeout_timestamps = [90.0] * 10
        self.manager._comm_unsolicited_timestamps = [90.0] * 10
        
        is_healthy, reason = self.manager._check_communication_health()
        
        assert is_healthy is True
        assert reason == "healthy"
    
    def test_check_communication_health_healthy_only_timeouts(self):
        """Test that health check returns healthy with only timeouts (AND condition)."""
        self.mock_reactor.monotonic.return_value = 100.0
        
        # 20 timeouts but only 5 unsolicited - NOT unhealthy
        self.manager._comm_timeout_timestamps = [90.0] * 20
        self.manager._comm_unsolicited_timestamps = [90.0] * 5
        
        is_healthy, reason = self.manager._check_communication_health()
        
        assert is_healthy is True
        assert reason == "healthy"
    
    def test_check_communication_health_healthy_only_unsolicited(self):
        """Test that health check returns healthy with only unsolicited (AND condition)."""
        self.mock_reactor.monotonic.return_value = 100.0
        
        # 20 unsolicited but only 5 timeouts - NOT unhealthy
        self.manager._comm_timeout_timestamps = [90.0] * 5
        self.manager._comm_unsolicited_timestamps = [90.0] * 20
        
        is_healthy, reason = self.manager._check_communication_health()
        
        assert is_healthy is True
        assert reason == "healthy"
    
    def test_check_communication_health_unhealthy_both_thresholds(self):
        """Test that health check returns unhealthy when BOTH thresholds exceeded."""
        self.mock_reactor.monotonic.return_value = 100.0
        
        # 15+ of each - should be unhealthy
        self.manager._comm_timeout_timestamps = [90.0] * 16
        self.manager._comm_unsolicited_timestamps = [90.0] * 17
        
        is_healthy, reason = self.manager._check_communication_health()
        
        assert is_healthy is False
        assert "16 timeouts AND 17 unsolicited" in reason
    
    def test_check_communication_health_prunes_old_entries(self):
        """Test that health check prunes old entries before counting."""
        self.mock_reactor.monotonic.return_value = 100.0
        
        # Add old entries (before cutoff) and recent entries
        # Cutoff = 100 - 30 = 70.0
        self.manager._comm_timeout_timestamps = [50.0, 60.0, 80.0, 85.0, 90.0, 95.0]
        self.manager._comm_unsolicited_timestamps = [55.0, 65.0, 82.0, 87.0, 92.0, 97.0]
        
        is_healthy, reason = self.manager._check_communication_health()
        
        # Should only count entries > 70.0: 4 timeouts, 4 unsolicited
        assert is_healthy is True
        assert len(self.manager._comm_timeout_timestamps) == 4
        assert len(self.manager._comm_unsolicited_timestamps) == 4
    
    def test_supervision_check_skips_when_disabled(self):
        """Test that supervision check does nothing when disabled."""
        self.manager._supervision_enabled = False
        self.manager.is_connected = Mock(return_value=True)
        self.manager._check_communication_health = Mock()
        
        self.manager._supervision_check_and_recover()
        
        self.manager._check_communication_health.assert_not_called()
    
    def test_supervision_check_skips_when_disconnected(self):
        """Test that supervision check does nothing when disconnected."""
        self.manager.is_connected = Mock(return_value=False)
        self.manager._check_communication_health = Mock()
        
        self.manager._supervision_check_and_recover()
        
        self.manager._check_communication_health.assert_not_called()
    
    def test_supervision_check_respects_interval(self):
        """Test that supervision check only runs at intervals."""
        self.manager.is_connected = Mock(return_value=True)
        self.manager._check_communication_health = Mock()
        
        # First check at time 100
        self.mock_reactor.monotonic.return_value = 100.0
        self.manager._last_supervision_check = 100.0
        
        # Try to check at 102 (< 5 second interval)
        self.mock_reactor.monotonic.return_value = 102.0
        self.manager._supervision_check_and_recover()
        
        self.manager._check_communication_health.assert_not_called()
    
    def test_supervision_check_runs_after_interval(self):
        """Test that supervision check runs after interval elapsed."""
        self.manager.is_connected = Mock(return_value=True)
        self.manager._check_communication_health = Mock(return_value=(True, "healthy"))
        
        # Last check at time 100
        self.manager._last_supervision_check = 100.0
        
        # Check at 106 (> 5 second interval)
        self.mock_reactor.monotonic.return_value = 106.0
        self.manager._supervision_check_and_recover()
        
        self.manager._check_communication_health.assert_called_once()
        assert self.manager._last_supervision_check == 106.0
    
    def test_supervision_check_disconnects_on_unhealthy(self):
        """Test that supervision check disconnects when communication unhealthy."""
        self.manager.is_connected = Mock(return_value=True)
        self.manager._check_communication_health = Mock(
            return_value=(False, "15 timeouts AND 15 unsolicited messages in last 30.0s")
        )
        self.manager.disconnect = Mock()
        
        # Populate tracking arrays
        self.manager._comm_timeout_timestamps = [100.0] * 15
        self.manager._comm_unsolicited_timestamps = [100.0] * 15
        
        self.mock_reactor.monotonic.return_value = 106.0
        self.manager._last_supervision_check = 0.0
        
        self.manager._supervision_check_and_recover()
        
        # Should log warning
        assert any("Communication unhealthy" in str(args) 
                   for args, _ in self.mock_gcode.respond_info.call_args_list)
        
        # Should clear tracking arrays
        assert len(self.manager._comm_timeout_timestamps) == 0
        assert len(self.manager._comm_unsolicited_timestamps) == 0
        
        # Should disconnect
        self.manager.disconnect.assert_called_once()
    
    def test_supervision_disabled_by_constructor(self):
        """Test that supervision can be disabled via constructor."""
        from ace.serial_manager import AceSerialManager
        
        manager_disabled = AceSerialManager(
            gcode=self.mock_gcode,
            reactor=self.mock_reactor,
            instance_num=1,
            supervision_enabled=False
        )
        
        assert manager_disabled._supervision_enabled is False
    
    def test_supervision_enabled_by_default(self):
        """Test that supervision is enabled by default."""
        from ace.serial_manager import AceSerialManager
        
        manager_default = AceSerialManager(
            gcode=self.mock_gcode,
            reactor=self.mock_reactor,
            instance_num=1
        )
        
        assert manager_default._supervision_enabled is True


class TestHeartbeat:
    """Test heartbeat functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        from ace.serial_manager import AceSerialManager
        
        self.mock_gcode = Mock()
        self.mock_reactor = Mock()
        self.mock_reactor.monotonic = Mock(return_value=100.0)
        self.mock_reactor.NOW = 10.0
        self.mock_reactor.unregister_timer = Mock()
        
        self.manager = AceSerialManager(
            gcode=self.mock_gcode,
            reactor=self.mock_reactor,
            instance_num=0,
            ace_enabled=True
        )
    
    def test_stop_heartbeat_when_timer_exists(self):
        """Test stopping heartbeat when timer is registered."""
        original_timer = Mock()
        self.manager.heartbeat_timer = original_timer
        
        self.manager.stop_heartbeat()
        
        self.mock_reactor.unregister_timer.assert_called_once_with(original_timer)
        assert self.manager.heartbeat_timer is None
    
    def test_stop_heartbeat_when_timer_none(self):
        """Test stopping heartbeat when timer is already None."""
        self.manager.heartbeat_timer = None
        
        self.manager.stop_heartbeat()
        
        self.mock_reactor.unregister_timer.assert_not_called()
        assert self.manager.heartbeat_timer is None
    
    def test_stop_heartbeat_handles_exception(self):
        """Test stopping heartbeat handles exception from unregister_timer."""
        original_timer = Mock()
        self.manager.heartbeat_timer = original_timer
        self.mock_reactor.unregister_timer.side_effect = Exception("timer error")
        
        # Should not raise exception
        self.manager.stop_heartbeat()
        
        # Timer should be cleared anyway
        assert self.manager.heartbeat_timer is None
    
    def test_heartbeat_tick_sends_request(self):
        """Test heartbeat tick sends status request."""
        self.manager._send_heartbeat_request = Mock()
        self.manager.heartbeat_interval = 2.5
        
        result = self.manager._heartbeat_tick(eventtime=50.0)
        
        self.manager._send_heartbeat_request.assert_called_once()
        assert result == 50.0 + 2.5
    
    def test_heartbeat_tick_updates_last_request_time(self):
        """Test heartbeat tick updates last status request time."""
        self.manager._send_heartbeat_request = Mock()
        self.mock_reactor.monotonic.return_value = 123.5
        
        self.manager._heartbeat_tick(eventtime=50.0)
        
        assert self.manager._last_status_request_time == 123.5
    
    def test_heartbeat_tick_handles_exception(self):
        """Test heartbeat tick handles exception and continues."""
        self.manager._send_heartbeat_request = Mock(side_effect=Exception("heartbeat error"))
        self.manager.heartbeat_interval = 1.0
        
        # Should not raise exception
        result = self.manager._heartbeat_tick(eventtime=50.0)
        
        # Should still return next event time
        assert result == 50.0 + 1.0
    
    def test_send_heartbeat_request_creates_correct_request(self):
        """Test send heartbeat request creates get_status request."""
        self.manager.send_high_prio_request = Mock()
        
        self.manager._send_heartbeat_request()
        
        # Check that send_high_prio_request was called
        assert self.manager.send_high_prio_request.call_count == 1
        call_args = self.manager.send_high_prio_request.call_args
        request = call_args[0][0]
        
        assert request == {"method": "get_status"}

    def test_send_heartbeat_request_uses_protocol_builder(self):
        """Heartbeat requests should come from the protocol adapter."""
        protocol = Mock()
        protocol.build_get_status_request.return_value = {"method": "status_via_protocol"}
        self.manager.protocol = protocol
        self.manager.send_high_prio_request = Mock()

        self.manager._send_heartbeat_request()

        protocol.build_get_status_request.assert_called_once_with()
        request = self.manager.send_high_prio_request.call_args[0][0]
        assert request == {"method": "status_via_protocol"}
    
    def test_send_heartbeat_request_calls_callback(self):
        """Test send heartbeat request invokes callback on response."""
        self.manager.send_high_prio_request = Mock()
        mock_heartbeat_callback = Mock()
        self.manager.heartbeat_callback = mock_heartbeat_callback
        
        self.manager._send_heartbeat_request()
        
        # Extract the callback passed to send_high_prio_request
        call_args = self.manager.send_high_prio_request.call_args
        response_callback = call_args[0][1]
        
        # Call the callback with a mock response
        test_response = {"result": {"status": "ready"}}
        response_callback(test_response)
        
        # Verify heartbeat_callback was called
        mock_heartbeat_callback.assert_called_once_with(test_response)
    
    def test_send_heartbeat_request_no_callback_registered(self):
        """Test send heartbeat request when no callback is registered."""
        self.manager.send_high_prio_request = Mock()
        self.manager.heartbeat_callback = None
        
        self.manager._send_heartbeat_request()
        
        # Extract the callback passed to send_high_prio_request
        call_args = self.manager.send_high_prio_request.call_args
        response_callback = call_args[0][1]
        
        # Call the callback with a mock response - should not raise
        test_response = {"result": {"status": "ready"}}
        response_callback(test_response)
        
        # No exception should be raised
    
    def test_send_heartbeat_request_handles_callback_exception(self):
        """Test send heartbeat request handles exception in callback."""
        self.manager.send_high_prio_request = Mock()
        mock_heartbeat_callback = Mock(side_effect=Exception("callback error"))
        self.manager.heartbeat_callback = mock_heartbeat_callback
        
        self.manager._send_heartbeat_request()
        
        # Extract the callback passed to send_high_prio_request
        call_args = self.manager.send_high_prio_request.call_args
        response_callback = call_args[0][1]
        
        # Call the callback with a mock response - should not raise
        test_response = {"result": {"status": "ready"}}
        response_callback(test_response)
        
        # Exception should be caught and logged, not raised
        mock_heartbeat_callback.assert_called_once_with(test_response)
    
    def test_send_heartbeat_uses_high_priority_queue(self):
        """Test send heartbeat request uses high-priority queue."""
        self.manager.send_high_prio_request = Mock()
        self.manager.send_request = Mock()
        
        self.manager._send_heartbeat_request()
        
        # Should use high-priority, not normal
        self.manager.send_high_prio_request.assert_called_once()
        self.manager.send_request.assert_not_called()


class TestHandleInfoResponse:
    """Tests for get_info callback handling through public helper."""

    def setup_method(self):
        from ace.serial_manager import AceSerialManager

        self.mock_gcode = Mock()
        self.mock_reactor = Mock()
        self.manager = AceSerialManager(
            gcode=self.mock_gcode,
            reactor=self.mock_reactor,
            instance_num=0,
            ace_enabled=False,
        )

    def test_handle_info_response_updates_device_info(self):
        self.manager.handle_info_response(
            {
                "result": {
                    "version": "1.2.3",
                    "boot_version": "0.9.1",
                    "raw_fields": {1: [(2, b"1.2.3")]},
                }
            }
        )

        assert self.manager.device_info == {
            "version": "1.2.3",
            "boot_version": "0.9.1",
            "raw_fields": {1: [(2, b"1.2.3")]},
        }
        assert self.mock_gcode.respond_info.call_count == 3
        logged_lines = [call.args[0] for call in self.mock_gcode.respond_info.call_args_list]
        assert any("GET_INFO raw_info:" in line for line in logged_lines)
        assert any("GET_INFO raw_fields:" in line for line in logged_lines)
        assert any("GET_INFO summary:" in line for line in logged_lines)

    def test_handle_info_response_handles_malformed_response(self):
        self.manager.device_info = {"version": "stale"}

        self.manager.handle_info_response(None)

        assert self.manager.device_info == {}
        assert self.mock_gcode.respond_info.call_count == 2
        logged_lines = [call.args[0] for call in self.mock_gcode.respond_info.call_args_list]
        assert any("GET_INFO raw_info:" in line for line in logged_lines)
        assert any("GET_INFO summary:" in line for line in logged_lines)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
