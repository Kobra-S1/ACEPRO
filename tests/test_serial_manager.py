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


class TestParseUsbLocation:
    """Test USB location string parsing for device sorting."""

    def setup_method(self):
        """Create serial manager with minimal mocking."""
        with patch('ace.serial_manager.serial'):
            import ace.serial_manager as sm
            DummySerialException = type("DummySerialException", (Exception,), {})
            sm.SerialException = DummySerialException  # ensure catchable exception type
            from ace.serial_manager import AceSerialManager
            sm.SerialException = DummySerialException  # also override in module after import
            assert issubclass(sm.SerialException, BaseException)
            self.SerialException = sm.SerialException
            
            mock_gcode = Mock()
            mock_reactor = Mock()
            
            self.manager = AceSerialManager(
                gcode=mock_gcode,
                reactor=mock_reactor,
                instance_num=0,
                ace_enabled=False  # Don't try to connect
            )

    def test_parse_simple_location(self):
        """Test parsing simple USB location like '1-1.4'."""
        result = self.manager._parse_usb_location("1-1.4")
        assert result == (1, 1, 4)

    def test_parse_complex_location_with_colon(self):
        """Test parsing location with colon interface suffix."""
        # "1-1.4.3:1.0" - the :1.0 is the USB interface, should be stripped
        result = self.manager._parse_usb_location("1-1.4.3:1.0")
        # After split(':')[0] → "1-1.4.3", replace('-','.') → "1.1.4.3"
        assert result == (1, 1, 4, 3)

    def test_parse_acm_fallback(self):
        """Test parsing ACM device fallback format."""
        result = self.manager._parse_usb_location("acm.2")
        # ACM devices sort after USB (999998) but before unknown (999999)
        assert result == (999998, 2)

    def test_parse_acm_fallback_zero(self):
        """Test parsing ACM0 fallback format."""
        result = self.manager._parse_usb_location("acm.0")
        assert result == (999998, 0)

    def test_parse_acm_invalid_returns_high_value(self):
        """Non-numeric ACM suffix should fall back to high sort key."""
        result = self.manager._parse_usb_location("acm.bad")
        assert result == (999999,)

    def test_parse_invalid_token_raises_value_error_branch(self):
        """Mixed tokens causing ValueError should fall back to high sort key."""
        result = self.manager._parse_usb_location("1-1.a.3")
        assert result == (999999,)

    def test_acm_sorts_after_usb_before_unknown(self):
        """ACM devices should sort after USB but before unknowns."""
        usb = self.manager._parse_usb_location("1-1.4.3:1.0")
        acm = self.manager._parse_usb_location("acm.2")
        unknown = self.manager._parse_usb_location("garbage")
        
        assert usb < acm < unknown

    def test_parse_empty_string_returns_high_value(self):
        """Empty string should sort to end."""
        result = self.manager._parse_usb_location("")
        assert result == (999999,)

    def test_parse_none_returns_high_value(self):
        """None should sort to end."""
        result = self.manager._parse_usb_location(None)
        assert result == (999999,)

    def test_parse_invalid_location_returns_high_value(self):
        """Invalid non-numeric location should sort to end."""
        result = self.manager._parse_usb_location("invalid-text-here")
        assert result == (999999,)

    def test_parse_mixed_numeric_and_text_returns_high_value(self):
        """Mixed segments with text should not raise and should sort to end."""
        result = self.manager._parse_usb_location("1-foo.3")
        assert result == (999999,)

    def test_invalid_locations_sort_after_valid(self):
        """Ensure invalid locations compare after valid ones without exceptions."""
        valid = self.manager._parse_usb_location("1-1.2")
        invalid = self.manager._parse_usb_location("garbage")
        assert valid < invalid

    def test_sorting_order_is_correct(self):
        """Verify locations sort in expected USB topology order."""
        locations = [
            "1-1.4.3:1.0",  # Should be (1, 1, 4, 3)
            "1-1.2:1.0",    # Should be (1, 1, 2)
            "1-1.4.1:1.0",  # Should be (1, 1, 4, 1)
            "2-1:1.0",      # Should be (2, 1)
        ]

        parsed = [self.manager._parse_usb_location(loc) for loc in locations]
        sorted_parsed = sorted(parsed)

        assert sorted_parsed == [
            (1, 1, 2),
            (1, 1, 4, 1),
            (1, 1, 4, 3),
            (2, 1),
        ]


class TestFindComPort:
    """Tests for find_com_port covering enumeration branches."""

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
        assert self.manager._expected_topology_positions is None

    def test_warns_when_insufficient_devices_for_instance(self):
        ports = [SimpleNamespace(device="/dev/ttyUSB0", description="ACE", hwid="LOCATION=1-1.1")]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=1)

        assert result is None
        # Should have logged the warning about insufficient devices
        log_messages = [call[0][0] for call in self.gcode.respond_info.call_args_list]
        assert any("only 1 ace" in msg.lower() for msg in log_messages)

    def test_stores_topology_and_returns_sorted_device(self):
        ports = [
            SimpleNamespace(device="/dev/ttyUSB1", description="ACE", hwid="LOCATION=1-1.2"),
            SimpleNamespace(device="/dev/ttyUSB0", description="ACE", hwid="LOCATION=1-1.1"),
        ]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=0)

        # Sorted by topology so /dev/ttyUSB0 (1-1.1) should be chosen first
        assert result == "/dev/ttyUSB0"
        assert self.manager._expected_topology_positions == [(1, 1, 1), (1, 1, 2)]

    def test_uses_existing_topology_on_subsequent_calls(self):
        self.manager._expected_topology_positions = [(1, 1, 1)]
        ports = [SimpleNamespace(device="/dev/ttyUSB5", description="ACE", hwid="LOCATION=1-1.9")]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=0)

        assert result == "/dev/ttyUSB5"
        # Should not overwrite expected topology when already set
        assert self.manager._expected_topology_positions == [(1, 1, 1)]

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

    def test_prefers_port_matching_expected_topology(self):
        # Stored topology expects the deeper device, even though sorting would pick the first
        self.manager._expected_topology_positions = [(1, 1, 4, 3)]
        ports = [
            SimpleNamespace(device="/dev/ttyUSB0", description="ACE", hwid="LOCATION=1-1.2"),      # would sort first
            SimpleNamespace(device="/dev/ttyUSB1", description="ACE", hwid="LOCATION=1-1.4.3"),    # expected match
        ]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=0)
        assert result == "/dev/ttyUSB1"

    def test_fallbacks_to_sorted_when_no_topology_match(self):
        # Expected topology doesn't exist in current ports -> fallback to sorted order (instance index)
        self.manager._expected_topology_positions = [(1, 1, 9)]
        ports = [
            SimpleNamespace(device="/dev/ttyUSB2", description="ACE", hwid="LOCATION=1-1.5"),
            SimpleNamespace(device="/dev/ttyUSB3", description="ACE", hwid="LOCATION=1-1.6"),
        ]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=0)
        assert result == "/dev/ttyUSB2"

    def test_handles_more_aces_than_configured_instances(self):
        # With two ACEs on the bus but only instance 0 requested, it should still pick the matching expected topo
        self.manager._expected_topology_positions = [(2, 2, 4, 3)]
        ports = [
            SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=2-2.3"),
            SimpleNamespace(device="/dev/ttyACM1", description="ACE", hwid="LOCATION=2-2.4.3"),
            SimpleNamespace(device="/dev/ttyACM9", description="OTHER", hwid="LOCATION=9-9"),  # ignore
        ]
        self.serial_mod.tools.list_ports.comports = lambda: ports

        result = self.manager.find_com_port("ACE", instance=0)
        assert result == "/dev/ttyACM1"


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

    def test_sorting_order_is_correct(self):
        """Verify locations sort in expected USB topology order."""
        locations = [
            "1-1.4.3:1.0",  # Should be (1, 1, 4, 3)
            "1-1.2:1.0",    # Should be (1, 1, 2)
            "1-1.4.1:1.0",  # Should be (1, 1, 4, 1)
            "2-1:1.0",      # Should be (2, 1)
        ]
        
        parsed = [self.manager._parse_usb_location(loc) for loc in locations]
        sorted_parsed = sorted(parsed)

        # Expected order: 1-1.2 < 1-1.4.1 < 1-1.4.3 < 2-1
        assert sorted_parsed == [
            (1, 1, 2),
            (1, 1, 4, 1),
            (1, 1, 4, 3),
            (2, 1),
        ]


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


class TestValidateTopologyPosition:
    def setup_method(self):
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager
            self.manager = AceSerialManager(gcode=Mock(), reactor=Mock(), instance_num=0, ace_enabled=False)

    def test_first_connection_stores_topology(self):
        self.manager._usb_location = "1-1.1"
        self.manager._expected_topology_positions = None

        assert self.manager._validate_topology_position(0) is True
        assert self.manager._topology_validation_failed_count == 0

    def test_out_of_range_instance_returns_true(self):
        self.manager._usb_location = "1-1.1"
        self.manager._expected_topology_positions = [(1, 1, 1)]

        assert self.manager._validate_topology_position(2) is True

    def test_mismatch_increments_counter_and_returns_false(self):
        self.manager._usb_location = "1-1.2"
        self.manager._expected_topology_positions = [(1, 1, 1)]
        self.manager._topology_validation_failed_count = 0

        # With threshold=1, mismatch clears expectations and returns False
        assert self.manager._validate_topology_position(0) is False
        assert self.manager._topology_validation_failed_count == 0  # Reset after clearing
        assert self.manager._expected_topology_positions is None  # Cleared for re-enumeration

    def test_relearn_topology_after_threshold_failures(self):
        """Test topology expectations cleared after mismatch at threshold."""
        self.manager._usb_location = "1-1.3"
        self.manager._expected_topology_positions = [(1, 1, 1)]
        self.manager._topology_validation_failed_count = self.manager.TOPOLOGY_RELEARN_THRESHOLD - 1

        result = self.manager._validate_topology_position(0)

        assert result is False  # Fail to trigger reconnect
        assert self.manager._expected_topology_positions is None  # Cleared for re-enumeration
        assert self.manager._topology_validation_failed_count == 0  # Reset

    def test_relearn_topology_pads_list_when_needed(self):
        """Test topology validation pads list if instance is out of range."""
        self.manager._usb_location = "1-1.5"
        self.manager._expected_topology_positions = [(1, 1, 1)]  # Only has instance 0
        # Trigger validation at instance 1 (which doesn't exist yet)
        # First it will pad with None, then on mismatch it should clear all
        self.manager._topology_validation_failed_count = 0

        # First call at instance 1 - pads with None, no mismatch since None accepts any
        result = self.manager._validate_topology_position(1)
        assert result is True  # None accepts anything
        assert len(self.manager._expected_topology_positions) == 2  # Padded
        
        # Now test the mismatch path with existing entry
        self.manager._usb_location = "1-1.5"
        self.manager._expected_topology_positions = [(1, 1, 1), (1, 1, 2)]
        self.manager._topology_validation_failed_count = self.manager.TOPOLOGY_RELEARN_THRESHOLD - 1
        
        result = self.manager._validate_topology_position(1)
        
        # Should clear all expectations and fail to trigger re-enumeration
        assert result is False
        assert self.manager._expected_topology_positions is None  # Cleared
        assert self.manager._topology_validation_failed_count == 0


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
        # Only one device but instance 1 requested -> returns None and warns
        ports = [SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=2-2.3")]
        self.serial_mod.tools.list_ports.comports = lambda: ports  # Correct mock: target the actual method used

        result = self.manager.find_com_port("ACE", 1)

        assert result is None
        log_messages = [call[0][0] for call in self.mock_gcode.respond_info.call_args_list]
        assert any("only 1 ace" in msg.lower() for msg in log_messages)

    def test_find_com_port_prefers_expected_topology(self):
        # Two devices, expected topology stored; should pick matching one
        p0 = SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=2-2.3")
        p1 = SimpleNamespace(device="/dev/ttyACM1", description="ACE", hwid="LOCATION=2-2.4.3")
        self.serial_mod.tools.list_ports.comports = lambda: [p1, p0]
        self.manager._expected_topology_positions = [(2, 2, 3), (2, 2, 4, 3)]

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
        self.manager.find_com_port = Mock(return_value=None)
        ok = self.manager.auto_connect(0, 115200)
        assert ok is False
        assert any("No ACE device found" in args[0] for args, _ in self.mock_gcode.respond_info.call_args_list)

    def test_auto_connect_topology_failure_disconnects(self):
        self.manager.find_com_port = Mock(return_value="/dev/ttyACM0")
        self.manager.connect = Mock(return_value=True)
        self.manager._get_usb_location_for_port = Mock(return_value="2-2.3")
        self.manager._validate_topology_position = Mock(return_value=False)
        self.manager.disconnect = Mock()

        ok = self.manager.auto_connect(0, 115200)

        assert ok is False
        self.manager.disconnect.assert_called_once()

    def test_auto_connect_success_returns_true(self):
        """Test successful auto_connect path - finds port, connects, validates topology, sends get_info."""
        self.manager.find_com_port = Mock(return_value="/dev/ttyACM0")
        self.manager.connect = Mock(return_value=True)
        self.manager._get_usb_location_for_port = Mock(return_value="2-2.3")
        self.manager._validate_topology_position = Mock(return_value=True)
        self.manager.send_request = Mock()

        ok = self.manager.auto_connect(0, 115200)

        assert ok is True
        self.manager.find_com_port.assert_called_once_with('ACE', 0)
        self.manager.connect.assert_called_once_with("/dev/ttyACM0", 115200)
        self.manager._validate_topology_position.assert_called_once_with(0)
        self.manager.send_request.assert_called_once()
        # Verify get_info request structure
        call_args = self.manager.send_request.call_args
        assert call_args[1]['request'] == {"method": "get_info"}
        assert callable(call_args[1]['callback'])

    def test_auto_connect_connect_failure_returns_false(self):
        """Test auto_connect when connect() fails."""
        self.manager.find_com_port = Mock(return_value="/dev/ttyACM0")
        self.manager.connect = Mock(return_value=False)
        self.manager._get_usb_location_for_port = Mock(return_value="2-2.3")

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

    def test_validate_topology_relearn_pads_list(self):
        # expected list shorter than instance index, ensure padding happens
        self.manager._expected_topology_positions = [(1,)]
        self.manager.TOPOLOGY_RELEARN_THRESHOLD = 1
        self.manager._usb_location = "2-2.4.3"
        current = self.manager._parse_usb_location(self.manager._usb_location)
        assert self.manager._validate_topology_position(1) is True  # Should pad and return True
        assert len(self.manager._expected_topology_positions) == 2
        assert self.manager._expected_topology_positions[1] is None

    def test_topology_validation_handles_reverse_instance_order(self):
        # Test that connecting higher instance first, then lower, works correctly
        # Simulate topology already stored from previous enumeration (e.g., instance 0 was connected before)
        self.manager._expected_topology_positions = [(1, 1, 2)]  # Only instance 0 stored
        
        # Instance 1 connects first (higher instance, pads the list)
        self.manager._usb_location = "1-1.4"
        result1 = self.manager._validate_topology_position(1)
        assert result1 is True
        assert len(self.manager._expected_topology_positions) == 2
        assert self.manager._expected_topology_positions[0] == (1, 1, 2)
        assert self.manager._expected_topology_positions[1] is None  # Padded with None
        
        # Now instance 0 connects (expected is not None, should validate normally)
        self.manager._usb_location = "1-1.2"
        result0 = self.manager._validate_topology_position(0)
        assert result0 is True
        # Should match, no failure
        assert self.manager._topology_validation_failed_count == 0
        # List unchanged
        assert len(self.manager._expected_topology_positions) == 2
        assert self.manager._expected_topology_positions[0] == (1, 1, 2)
        assert self.manager._expected_topology_positions[1] is None

    def test_find_com_port_selects_correct_device_by_topology_order(self):
        # Test that with multiple devices, instance 0 selects the root/first in topology order
        # regardless of connection order, and instance 1 selects the next
        p0 = SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=1-1.2")  # Root
        p1 = SimpleNamespace(device="/dev/ttyACM1", description="ACE", hwid="LOCATION=1-1.4.3")  # Deeper
        self.serial_mod.tools.list_ports.comports = lambda: [p1, p0]  # Ports returned in reverse order
        
        # Instance 0 should pick the first in sorted order (root)
        result0 = self.manager.find_com_port("ACE", 0)
        assert result0 == "/dev/ttyACM0"  # Root device
        
        # Now topology is stored: [(1,1,2), (1,1,4,3)]
        assert self.manager._expected_topology_positions == [(1, 1, 2), (1, 1, 4, 3)]
        
        # Instance 1 should pick the matching expected topology
        result1 = self.manager.find_com_port("ACE", 1)
        assert result1 == "/dev/ttyACM1"  # Deeper device
        
        # Test reverse: if we reset and call instance 1 first
        self.manager._expected_topology_positions = None
        result1_first = self.manager.find_com_port("ACE", 1)
        assert result1_first == "/dev/ttyACM1"  # Still picks by sorted order since no expected
        # Now expected is set
        assert self.manager._expected_topology_positions == [(1, 1, 2), (1, 1, 4, 3)]
        
        # Then instance 0 picks matching
        result0_after = self.manager.find_com_port("ACE", 0)
        assert result0_after == "/dev/ttyACM0"

    def test_topology_relearn_after_single_mismatch(self):
        # Test that with threshold=1, expectations cleared after 1 mismatch
        self.manager.TOPOLOGY_RELEARN_THRESHOLD = 1
        self.manager._expected_topology_positions = [(1, 1, 2)]  # Stored topology for instance 0
        self.manager._usb_location = "1-1.4"  # Mismatched location
        
        # First validation: mismatch, should clear expectations since threshold=1
        result = self.manager._validate_topology_position(0)
        assert result is False  # Fail to trigger reconnect
        assert self.manager._topology_validation_failed_count == 0  # Reset after clearing
        assert self.manager._expected_topology_positions is None  # Cleared for re-enumeration

    def test_topology_mismatch_due_to_swapped_acm_assignments(self):
        # Simulate swapped ACM: expected topology doesn't match current ports due to enumeration order
        # Ports: p0 has location for instance 1, p1 has location for instance 0
        p0 = SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=1-1.4")  # Deeper, but ACM0
        p1 = SimpleNamespace(device="/dev/ttyACM1", description="ACE", hwid="LOCATION=1-1.2")  # Root, but ACM1
        self.serial_mod.tools.list_ports.comports = lambda: [p0, p1]
        
        # Stored topology: instance 0 at (1,1,2), instance 1 at (1,1,4)
        self.manager._expected_topology_positions = [(1, 1, 2), (1, 1, 4)]
        
        # find_com_port for instance 0: prefers expected (1,1,2), finds p1 (/dev/ttyACM1)
        result0 = self.manager.find_com_port("ACE", 0)
        assert result0 == "/dev/ttyACM1"
        
        # Simulate auto_connect: set usb_location
        self.manager._usb_location = self.manager._get_usb_location_for_port(result0)
        assert self.manager._usb_location == "1-1.2"  # Correct
        
        # Validate: should match
        result = self.manager._validate_topology_position(0)
        assert result is True
        
        # Now for instance 1: prefers expected (1,1,4), finds p0 (/dev/ttyACM0)
        result1 = self.manager.find_com_port("ACE", 1)
        assert result1 == "/dev/ttyACM0"
        
        self.manager._usb_location = self.manager._get_usb_location_for_port(result1)
        assert self.manager._usb_location == "1-1.4"
        result = self.manager._validate_topology_position(1)
        assert result is True

    def test_topology_relearn_on_enumeration_change(self):
        # Test expectations cleared when enumeration changes cause mismatch
        # Initial: instance 0 at (1,1,2) on /dev/ttyACM0
        self.manager._expected_topology_positions = [(1, 1, 2)]
        
        # Simulate enumeration swap: now /dev/ttyACM0 has (1,1,4)
        p0 = SimpleNamespace(device="/dev/ttyACM0", description="ACE", hwid="LOCATION=1-1.4")
        self.serial_mod.tools.list_ports.comports = lambda: [p0]
        
        # find_com_port for instance 0: no match for expected, falls back to sorted (p0)
        result = self.manager.find_com_port("ACE", 0)
        assert result == "/dev/ttyACM0"
        
        # Set usb_location to the new one
        self.manager._usb_location = "1-1.4"
        
        # Validate: mismatch, should clear expectations since threshold=1 (default)
        result = self.manager._validate_topology_position(0)
        assert result is False  # Fail to trigger reconnect
        assert self.manager._expected_topology_positions is None  # Cleared for re-enumeration

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

    def test_connect_topology_failure_triggers_disconnect(self):
        from ace.serial_manager import AceSerialManager
        # Restore real auto_connect for this test
        self.manager.auto_connect = AceSerialManager.auto_connect.__get__(self.manager, AceSerialManager)
        self.manager.find_com_port = Mock(return_value="/dev/ttyUSB0")
        self.manager.connect = Mock(return_value=True)
        self.manager._validate_topology_position = Mock(return_value=False)
        self.manager.disconnect = Mock()
        self.manager.send_request = Mock()
        self.manager._get_usb_location_for_port = Mock(return_value="loc")
        # Ensure list_ports iteration is safe
        import ace.serial_manager as sm
        sm.serial.tools.list_ports.comports = lambda: []

        result = self.manager.auto_connect(instance=0, baud=115200)

        assert result is False
        assert self.manager.find_com_port.called
        assert self.manager.connect.called
        self.manager._validate_topology_position.assert_called_once()
        self.manager.disconnect.assert_called_once()
        self.manager.send_request.assert_not_called()

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

    def test_reconnect_does_not_add_timestamp(self):
        """reconnect() should not add timestamp - only callback failures do."""
        self.manager.reactor.register_timer = Mock(return_value="timer")
        self.manager.reactor.NOW = 0.0
        self.manager.reactor.monotonic.return_value = 1000.0
        
        self.manager.reconnect(delay=1)
        
        # reconnect() no longer adds timestamp - callback does on failure
        assert len(self.manager._reconnect_timestamps) == 0

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
        
        # Start reconnect
        self.manager.reconnect(delay=1)
        
        # Simulate 3 failed callbacks
        for t in [1000.0, 1010.0, 1020.0]:
            self.manager.reactor.monotonic.return_value = t
            captured_callback(t)
        
        assert len(self.manager._reconnect_timestamps) == 3

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
        
        # Start first reconnect and fail - adds timestamp at 1000
        self.manager.reconnect(delay=1)
        self.manager.reactor.monotonic.return_value = 1000.0
        captured_callback(1000.0)
        
        assert len(self.manager._reconnect_timestamps) == 1
        
        # Start second reconnect 200 seconds later (window is 180s)
        self.manager.reactor.monotonic.return_value = 1200.0
        captured_callback(1200.0)
        
        # Old timestamp should be pruned, only new one remains
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

    def test_is_connection_stable_fails_with_too_many_reconnects(self):
        """is_connection_stable should return False with 6+ reconnects in 60s."""
        self.manager._connected = True
        self.manager._serial = Mock()
        self.manager._serial.is_open = True
        self.manager._last_connected_time = 900.0  # Connected long ago
        self.manager.reactor.monotonic.return_value = 1000.0
        
        # Add 6 recent reconnects (threshold is 6)
        self.manager._reconnect_timestamps = [945.0, 950.0, 955.0, 960.0, 965.0, 970.0]
        
        assert self.manager.is_connection_stable() is False

    def test_is_connection_stable_with_few_reconnects(self):
        """is_connection_stable should be True with <6 reconnects in 60s."""
        self.manager._connected = True
        self.manager._serial = Mock()
        self.manager._serial.is_open = True
        self.manager._last_connected_time = 900.0  # Connected long ago
        self.manager.reactor.monotonic.return_value = 1000.0
        
        # Only 5 reconnects (below threshold of 6)
        self.manager._reconnect_timestamps = [950.0, 955.0, 960.0, 965.0, 970.0]
        
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
        
        # Initial reconnect call (does NOT add timestamp - only callback failures do)
        self.mock_reactor.monotonic.return_value = 1000.0
        self.manager.reconnect(delay=5)
        
        assert captured_callback is not None
        initial_count = len(self.manager._reconnect_timestamps)
        assert initial_count == 0  # reconnect() no longer adds timestamp
        
        # Simulate 6 retry callbacks (each failure adds a timestamp)
        for i in range(6):
            self.mock_reactor.monotonic.return_value = 1010.0 + (i * 10)
            captured_callback(1010.0 + (i * 10))
        
        # Should have 6 total timestamps (all from retries)
        assert len(self.manager._reconnect_timestamps) == 6

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


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
