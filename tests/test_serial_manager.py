"""
Tests for AceSerialManager pure logic functions.

Focus: Testing actual production code without heavy I/O mocking.
- USB location parsing
- CRC calculation  
- Frame parsing
- Status update change detection
"""
import pytest
import struct
import json
from unittest.mock import Mock, patch


class TestParseUsbLocation:
    """Test USB location string parsing for device sorting."""

    def setup_method(self):
        """Create serial manager with minimal mocking."""
        with patch('ace.serial_manager.serial'):
            from ace.serial_manager import AceSerialManager
            
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


class TestFrameParsing:
    """Test frame parsing from raw bytes."""

    def setup_method(self):
        """Create serial manager for frame testing."""
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
            # Mock the serial object for read_frames
            self.manager._serial = Mock()
            self.manager._serial.read = Mock(return_value=b'')

    def _build_frame(self, payload_dict):
        """Build a valid frame from payload dict."""
        payload = json.dumps(payload_dict).encode('utf-8')
        data = bytearray([0xFF, 0xAA])  # Header
        data += struct.pack('<H', len(payload))  # Length
        data += payload
        data += struct.pack('<H', self.manager._calc_crc(payload))  # CRC
        data += b'\xFE'  # Terminator
        return bytes(data)

    def test_parse_valid_frame(self):
        """Parse a valid complete frame."""
        payload = {"id": 1, "result": {"status": "ready"}}
        frame = self._build_frame(payload)
        
        self.manager.read_buffer = bytearray(frame)
        self.manager._serial.read = Mock(return_value=b'')
        
        frames = self.manager.read_frames(0.0)
        
        assert len(frames) == 1
        assert frames[0] == payload

    def test_parse_multiple_frames(self):
        """Parse multiple complete frames in buffer."""
        payload1 = {"id": 1, "result": "ok"}
        payload2 = {"id": 2, "result": "done"}
        
        frame1 = self._build_frame(payload1)
        frame2 = self._build_frame(payload2)
        
        self.manager.read_buffer = bytearray(frame1 + frame2)
        self.manager._serial.read = Mock(return_value=b'')
        
        frames = self.manager.read_frames(0.0)
        
        assert len(frames) == 2
        assert frames[0] == payload1
        assert frames[1] == payload2

    def test_incomplete_frame_stays_in_buffer(self):
        """Incomplete frame should remain in buffer for later."""
        payload = {"id": 1, "result": "ok"}
        frame = self._build_frame(payload)
        
        # Only provide partial frame
        partial = frame[:len(frame) // 2]
        self.manager.read_buffer = bytearray(partial)
        self.manager._serial.read = Mock(return_value=b'')
        
        frames = self.manager.read_frames(0.0)
        
        assert len(frames) == 0
        assert len(self.manager.read_buffer) == len(partial)  # Buffer preserved

    def test_invalid_crc_rejected(self):
        """Frame with bad CRC should be rejected."""
        payload = {"id": 1, "result": "ok"}
        frame = bytearray(self._build_frame(payload))
        
        # Corrupt the CRC
        frame[-2] ^= 0xFF
        
        self.manager.read_buffer = bytearray(frame)
        self.manager._serial.read = Mock(return_value=b'')
        
        frames = self.manager.read_frames(0.0)
        
        assert len(frames) == 0

    def test_resync_on_junk_data(self):
        """Parser should resync after junk data."""
        payload = {"id": 1, "result": "ok"}
        frame = self._build_frame(payload)
        
        # Junk before frame
        junk = b'\x00\x01\x02\x03'
        self.manager.read_buffer = bytearray(junk + frame)
        self.manager._serial.read = Mock(return_value=b'')
        
        frames = self.manager.read_frames(0.0)
        
        assert len(frames) == 1
        assert frames[0] == payload

    def test_invalid_terminator_resyncs(self):
        """Invalid terminator should trigger resync."""
        payload = {"id": 1, "result": "ok"}
        frame = bytearray(self._build_frame(payload))
        
        # Corrupt terminator
        frame[-1] = 0x00
        
        self.manager.read_buffer = bytearray(frame)
        self.manager._serial.read = Mock(return_value=b'')
        
        frames = self.manager.read_frames(0.0)
        
        assert len(frames) == 0


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


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
