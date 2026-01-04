"""
Test suite for USB topology validation and feed assist restoration.

Tests the critical logic that ensures ACE instances connect to correct
physical hardware and feed assist is only restored on the right device.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, PropertyMock
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from extras.ace.serial_manager import AceSerialManager
from extras.ace.instance import AceInstance


class TestUSBTopologyPositionCalculation:
    """Test USB topology position parsing and depth calculation."""
    
    def test_topology_position_two_levels(self):
        """Test position calculation for 2-level topology (root->port)."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 0)
        mgr._usb_location = "2-2.3"
        
        position = mgr.get_usb_topology_position()
        assert position == 2, "2-2.3 should be depth 2 (root->hub->port)"
    
    def test_topology_position_three_levels(self):
        """Test position calculation for 3-level topology (daisy-chained)."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 0)
        mgr._usb_location = "2-2.4.3"
        
        position = mgr.get_usb_topology_position()
        assert position == 3, "2-2.4.3 should be depth 3 (root->hub->port->port)"
    
    def test_topology_position_four_levels(self):
        """Test position calculation for 4-level topology."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 0)
        mgr._usb_location = "1-3.2.4.1"
        
        position = mgr.get_usb_topology_position()
        assert position == 4, "1-3.2.4.1 should be depth 4"
    
    def test_topology_position_different_root_same_depth(self):
        """Test that different root controllers give same depth."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        
        # Test root controller 1
        mgr1 = AceSerialManager(mock_gcode, mock_reactor, 0)
        mgr1._usb_location = "1-3.2"
        pos1 = mgr1.get_usb_topology_position()
        
        # Test root controller 2
        mgr2 = AceSerialManager(mock_gcode, mock_reactor, 0)
        mgr2._usb_location = "2-2.3"
        pos2 = mgr2.get_usb_topology_position()
        
        assert pos1 == pos2 == 2, "Different root controllers should give same depth"
    
    def test_topology_position_no_location(self):
        """Test position calculation when no location is set."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 0)
        mgr._usb_location = None
        
        position = mgr.get_usb_topology_position()
        assert position is None, "Should return None when no location set"
    
    def test_topology_position_invalid_format(self):
        """Test position calculation with invalid location format."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 0)
        mgr._usb_location = "invalid"
        
        position = mgr.get_usb_topology_position()
        assert position is None, "Should return None for invalid format"


class TestUSBEnumerationValidation:
    """Test USB enumeration with insufficient devices."""
    
    @patch('extras.ace.serial_manager.serial.tools.list_ports.comports')
    def test_enumeration_insufficient_devices_instance_0(self, mock_comports):
        """Test that instance 0 can connect with only 1 device."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 0)
        
        # Mock 1 ACE device
        mock_port = Mock()
        mock_port.description = "ACE"
        mock_port.device = "/dev/ttyACM0"
        mock_port.hwid = "USB VID:PID=1234:5678 LOCATION=2-2.3"
        mock_comports.return_value = [mock_port]
        
        # Instance 0 should find device
        result = mgr.find_com_port('ACE', instance=0)
        assert result == "/dev/ttyACM0", "Instance 0 should connect with 1 device"
    
    @patch('extras.ace.serial_manager.serial.tools.list_ports.comports')
    def test_enumeration_insufficient_devices_instance_1(self, mock_comports):
        """Test that instance 1 refuses to connect with only 1 device."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 1)
        
        # Mock 1 ACE device
        mock_port = Mock()
        mock_port.description = "ACE"
        mock_port.device = "/dev/ttyACM0"
        mock_port.hwid = "USB VID:PID=1234:5678 LOCATION=2-2.3"
        mock_comports.return_value = [mock_port]
        
        # Instance 1 should refuse (needs at least 2 devices)
        result = mgr.find_com_port('ACE', instance=1)
        assert result is None, "Instance 1 should refuse with only 1 device"
        
        # Verify warning was logged (note the exact format from code)
        found_warning = False
        for call in mock_gcode.respond_info.call_args_list:
            if "WARNING - Only 1 ACE(s) found" in str(call):
                found_warning = True
                break
        assert found_warning, "Should log warning about insufficient devices"
    
    @patch('extras.ace.serial_manager.serial.tools.list_ports.comports')
    def test_enumeration_sufficient_devices_instance_1(self, mock_comports):
        """Test that instance 1 connects when 2 devices are available."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 1)
        
        # Mock 2 ACE devices
        mock_port0 = Mock()
        mock_port0.description = "ACE"
        mock_port0.device = "/dev/ttyACM0"
        mock_port0.hwid = "USB VID:PID=1234:5678 LOCATION=2-2.3"
        
        mock_port1 = Mock()
        mock_port1.description = "ACE"
        mock_port1.device = "/dev/ttyACM1"
        mock_port1.hwid = "USB VID:PID=1234:5678 LOCATION=2-2.4.3"
        
        mock_comports.return_value = [mock_port0, mock_port1]
        
        # Instance 1 should select second device
        result = mgr.find_com_port('ACE', instance=1)
        assert result == "/dev/ttyACM1", "Instance 1 should select second device"
    
    @patch('extras.ace.serial_manager.serial.tools.list_ports.comports')
    def test_enumeration_topology_sorting(self, mock_comports):
        """Test that devices are sorted by topology depth."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 0)
        
        # Mock 2 ACE devices in WRONG ORDER (deeper first)
        mock_port0 = Mock()
        mock_port0.description = "ACE"
        mock_port0.device = "/dev/ttyACM0"
        mock_port0.hwid = "USB VID:PID=1234:5678 LOCATION=2-2.4.3"  # Deeper
        
        mock_port1 = Mock()
        mock_port1.description = "ACE"
        mock_port1.device = "/dev/ttyACM1"
        mock_port1.hwid = "USB VID:PID=1234:5678 LOCATION=2-2.3"  # Shallower
        
        mock_comports.return_value = [mock_port0, mock_port1]
        
        # Instance 0 should select SHALLOWER device (sorted first)
        result = mgr.find_com_port('ACE', instance=0)
        assert result == "/dev/ttyACM1", "Should sort by topology and select shallower"


class TestTopologyValidation:
    """Test topology validation during connection."""
    
    def test_validation_first_connection_no_stored_topology(self):
        """Test validation passes on first connection (no stored topology yet)."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 0)
        mgr._usb_location = "2-2.3"
        mgr._expected_topology_positions = None  # First connection
        
        result = mgr._validate_topology_position(0)
        assert result is True, "First connection should always pass validation"
    
    def test_validation_matching_topology(self):
        """Test validation passes when topology matches."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 0)
        mgr._usb_location = "2-2.3"
        mgr._expected_topology_positions = [(2, 2, 3), (2, 2, 4, 3)]
        
        result = mgr._validate_topology_position(0)
        assert result is True, "Should pass when topology matches"
        assert mgr._topology_validation_failed_count == 0
    
    def test_validation_mismatched_topology(self):
        """Test validation clears expectations when topology doesn't match (threshold=1)."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 0)
        mgr._usb_location = "2-2.4.3"  # Wrong topology
        mgr._expected_topology_positions = [(2, 2, 3), (2, 2, 4, 3)]
        
        result = mgr._validate_topology_position(0)
        assert result is False, "Should clear expectations and fail when topology doesn't match"
        assert mgr._topology_validation_failed_count == 0  # Reset after clearing
        assert mgr._expected_topology_positions is None  # Cleared for re-enumeration
        
        # Verify errors were logged
        assert mock_gcode.respond_info.call_count == 2
        calls = [call[0][0] for call in mock_gcode.respond_info.call_args_list]
        assert "Topology mismatch" in calls[0]
        assert "expected (2, 2, 3)" in calls[0]
        assert "got (2, 2, 4, 3)" in calls[0]
        assert "Topology expectations cleared" in calls[1]
    
    def test_validation_failure_counter_increments(self):
        """Test that failure counter increments until threshold, then clears expectations."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 0)
        mgr.TOPOLOGY_RELEARN_THRESHOLD = 2  # Set higher for this test
        mgr._usb_location = "2-2.4.3"
        mgr._expected_topology_positions = [(2, 2, 3), (2, 2, 4, 3)]
        
        # First failure
        result = mgr._validate_topology_position(0)
        assert result is False  # Returns False on mismatch
        assert mgr._topology_validation_failed_count == 1
        
        # Second failure - reaches threshold, clears expectations
        result = mgr._validate_topology_position(0)
        # After threshold is reached the expectations are cleared and counter resets
        assert result is False
        assert mgr._topology_validation_failed_count == 0
        assert mgr._expected_topology_positions is None  # Cleared
    
    def test_validation_failure_counter_resets_on_success(self):
        """Test that failure counter resets when validation succeeds."""
        mock_gcode = Mock()
        mock_reactor = Mock()
        mgr = AceSerialManager(mock_gcode, mock_reactor, 0)
        mgr.TOPOLOGY_RELEARN_THRESHOLD = 3  # Set higher to allow multiple failures
        mgr._expected_topology_positions = [(2, 2, 3), (2, 2, 4, 3)]
        
        # Fail a few times
        mgr._usb_location = "2-2.4.3"
        mgr._validate_topology_position(0)
        mgr._validate_topology_position(0)
        assert mgr._topology_validation_failed_count == 2
        
        # Now succeed
        mgr._usb_location = "2-2.3"
        result = mgr._validate_topology_position(0)
        assert result is True
        assert mgr._topology_validation_failed_count == 0  # Resets on success, "Counter should reset on success"


class TestFeedAssistTopologyTracking:
    """Test feed assist topology position tracking."""
    
    @pytest.fixture
    def mock_instance(self):
        """Create a mock ACE instance with necessary dependencies."""
        mock_printer = Mock()
        mock_printer.get_reactor = Mock(return_value=Mock())
        mock_printer.lookup_object = Mock(return_value=Mock())
        
        mock_gcode = Mock()
        mock_reactor = Mock()
        
        ace_config = {
            "baud": 115200,
            "filament_runout_sensor_name_rdm": "rdm_sensor",
            "filament_runout_sensor_name_nozzle": "nozzle_sensor",
            "feed_speed": 8.0,
            "retract_speed": 10.0,
            "total_max_feeding_length": 900.0,
            "parkposition_to_toolhead_length": 650.0,
            "toolchange_load_length": 70.0,
            "parkposition_to_rdm_length": 550.0,
            "incremental_feeding_length": 10.0,
            "incremental_feeding_speed": 5.0,
            "extruder_feeding_length": 85.0,
            "extruder_feeding_speed": 2.5,
            "toolhead_slow_loading_speed": 2.5,
            "heartbeat_interval": 1.0,
            "max_dryer_temperature": 70.0,
            "toolhead_full_purge_length": 150.0,
            "timeout_multiplier": 1.0,
        }
        
        instance = AceInstance(0, ace_config, mock_printer)
        return instance
    
    def test_feed_assist_topology_stored_on_enable(self, mock_instance):
        """Test that topology position is stored when feed assist is enabled."""
        # Mock serial manager to return a topology position
        mock_instance.serial_mgr.get_usb_topology_position = Mock(return_value=2)
        mock_instance.serial_mgr.send_request = Mock()
        mock_instance.serial_mgr.is_ready = Mock(return_value=True)
        mock_instance.reactor.pause = Mock()
        
        # Enable feed assist
        mock_instance._enable_feed_assist(0)
        
        assert mock_instance._feed_assist_index == 0
        assert mock_instance._feed_assist_topology_position == 2
    
    def test_feed_assist_topology_cleared_on_disable(self, mock_instance):
        """Test that topology position is cleared when feed assist is disabled."""
        # Set up initial state
        mock_instance._feed_assist_index = 0
        mock_instance._feed_assist_topology_position = 2
        mock_instance.serial_mgr.send_request = Mock()
        mock_instance.serial_mgr.is_ready = Mock(return_value=True)
        mock_instance.reactor.pause = Mock()
        mock_instance.reactor.monotonic = Mock(return_value=1000.0)  # Mock time
        
        # Disable feed assist
        mock_instance._disable_feed_assist(0)
        
        assert mock_instance._feed_assist_index == -1
        assert mock_instance._feed_assist_topology_position is None


class TestFeedAssistRestoration:
    """Test feed assist restoration logic with topology validation."""
    
    @pytest.fixture
    def mock_instance(self):
        """Create a mock ACE instance."""
        mock_printer = Mock()
        mock_printer.get_reactor = Mock(return_value=Mock())
        mock_printer.lookup_object = Mock(return_value=Mock())
        
        ace_config = {
            "baud": 115200,
            "filament_runout_sensor_name_rdm": "rdm_sensor",
            "filament_runout_sensor_name_nozzle": "nozzle_sensor",
            "feed_speed": 8.0,
            "retract_speed": 10.0,
            "total_max_feeding_length": 900.0,
            "parkposition_to_toolhead_length": 650.0,
            "toolchange_load_length": 70.0,
            "parkposition_to_rdm_length": 550.0,
            "incremental_feeding_length": 10.0,
            "incremental_feeding_speed": 5.0,
            "extruder_feeding_length": 85.0,
            "extruder_feeding_speed": 2.5,
            "toolhead_slow_loading_speed": 2.5,
            "heartbeat_interval": 1.0,
            "max_dryer_temperature": 70.0,
            "toolhead_full_purge_length": 150.0,
            "timeout_multiplier": 1.0,
            "feed_assist_active_after_ace_connect": True,
        }
        
        instance = AceInstance(0, ace_config, mock_printer)
        return instance
    
    def test_restoration_skipped_no_pending(self, mock_instance):
        """Test that restoration is skipped when nothing is pending."""
        mock_instance._pending_feed_assist_restore = -1
        mock_instance.serial_mgr = Mock()  # Mock the entire serial manager
        mock_instance.serial_mgr.send_request = Mock()
        
        # Should do nothing
        mock_instance._maybe_restore_pending_feed_assist()
        
        # Verify no commands sent
        assert mock_instance.serial_mgr.send_request.call_count == 0
    
    def test_restoration_proceeds_matching_topology(self, mock_instance):
        """Test that restoration proceeds when topology matches."""
        mock_instance._pending_feed_assist_restore = 0
        mock_instance._feed_assist_topology_position = 2
        mock_instance.serial_mgr.get_usb_topology_position = Mock(return_value=2)
        mock_instance.serial_mgr.send_request = Mock()
        
        mock_instance._maybe_restore_pending_feed_assist()
        
        # Verify restoration command was sent
        assert mock_instance.serial_mgr.send_request.call_count == 1
        call_args = mock_instance.serial_mgr.send_request.call_args[0][0]
        assert call_args["method"] == "start_feed_assist"
        assert call_args["params"]["index"] == 0
    
    def test_restoration_skipped_mismatched_topology(self, mock_instance):
        """Test that restoration is skipped when topology doesn't match."""
        mock_instance._pending_feed_assist_restore = 0
        mock_instance._feed_assist_topology_position = 2  # Was at depth 2
        mock_instance.serial_mgr.get_usb_topology_position = Mock(return_value=3)  # Now at depth 3
        mock_instance.serial_mgr.send_request = Mock()
        
        mock_instance._maybe_restore_pending_feed_assist()
        
        # Verify no restoration command sent
        assert mock_instance.serial_mgr.send_request.call_count == 0
        
        # Verify state was cleared
        assert mock_instance._feed_assist_index == -1
        assert mock_instance._feed_assist_topology_position is None
        
        # Verify warning was logged
        mock_instance.gcode.respond_info.assert_any_call(
            "ACE[0]: Skipping feed assist restore - "
            "different chain position (was: 2, now: 3)"
        )
    
    def test_restoration_proceeds_first_time_no_stored_position(self, mock_instance):
        """Test that restoration proceeds when no position was stored (backward compat)."""
        mock_instance._pending_feed_assist_restore = 0
        mock_instance._feed_assist_topology_position = None  # No stored position
        mock_instance.serial_mgr.get_usb_topology_position = Mock(return_value=2)
        mock_instance.serial_mgr.send_request = Mock()
        
        mock_instance._maybe_restore_pending_feed_assist()
        
        # Should proceed (backward compatibility)
        assert mock_instance.serial_mgr.send_request.call_count == 1


class TestReconnectionScenarios:
    """Test complete reconnection scenarios."""
    
    @pytest.fixture
    def mock_instance_with_feed_assist(self):
        """Create instance with feed assist active."""
        mock_printer = Mock()
        mock_printer.get_reactor = Mock(return_value=Mock())
        mock_printer.lookup_object = Mock(return_value=Mock())
        
        ace_config = {
            "baud": 115200,
            "filament_runout_sensor_name_rdm": "rdm_sensor",
            "filament_runout_sensor_name_nozzle": "nozzle_sensor",
            "feed_speed": 8.0,
            "retract_speed": 10.0,
            "total_max_feeding_length": 900.0,
            "parkposition_to_toolhead_length": 650.0,
            "toolchange_load_length": 70.0,
            "parkposition_to_rdm_length": 550.0,
            "incremental_feeding_length": 10.0,
            "incremental_feeding_speed": 5.0,
            "extruder_feeding_length": 85.0,
            "extruder_feeding_speed": 2.5,
            "toolhead_slow_loading_speed": 2.5,
            "heartbeat_interval": 1.0,
            "max_dryer_temperature": 70.0,
            "toolhead_full_purge_length": 150.0,
            "timeout_multiplier": 1.0,
            "feed_assist_active_after_ace_connect": True,
        }
        
        instance = AceInstance(0, ace_config, mock_printer)
        
        # Simulate feed assist was active at topology position 2
        instance._feed_assist_index = 0
        instance._feed_assist_topology_position = 2
        
        return instance
    
    def test_happy_path_reconnect_same_topology(self, mock_instance_with_feed_assist):
        """Test successful reconnection to same topology position."""
        instance = mock_instance_with_feed_assist
        
        # Simulate connection callback
        instance._on_ace_connect()
        
        # Should set pending restore
        assert instance._pending_feed_assist_restore == 0
        
        # Simulate heartbeat with matching topology
        instance.serial_mgr.get_usb_topology_position = Mock(return_value=2)
        instance.serial_mgr.send_request = Mock()
        
        instance._maybe_restore_pending_feed_assist()
        
        # Verify restoration proceeded
        assert instance.serial_mgr.send_request.call_count == 1
    
    def test_error_reconnect_wrong_topology(self, mock_instance_with_feed_assist):
        """Test reconnection to wrong topology position - should skip restoration."""
        instance = mock_instance_with_feed_assist
        
        # Simulate connection callback
        instance._on_ace_connect()
        assert instance._pending_feed_assist_restore == 0
        
        # Simulate heartbeat with WRONG topology (connected to different physical ACE)
        instance.serial_mgr.get_usb_topology_position = Mock(return_value=3)
        instance.serial_mgr.send_request = Mock()
        
        instance._maybe_restore_pending_feed_assist()
        
        # Verify restoration was SKIPPED
        assert instance.serial_mgr.send_request.call_count == 0
        assert instance._feed_assist_index == -1
        assert instance._feed_assist_topology_position is None
    
    def test_error_instance_swapped_on_reconnect(self):
        """Test scenario where instances swap physical ACEs on reconnect."""
        mock_printer = Mock()
        mock_printer.get_reactor = Mock(return_value=Mock())
        mock_printer.lookup_object = Mock(return_value=Mock())
        
        ace_config = {
            "baud": 115200,
            "filament_runout_sensor_name_rdm": "rdm_sensor",
            "filament_runout_sensor_name_nozzle": "nozzle_sensor",
            "feed_speed": 8.0,
            "retract_speed": 10.0,
            "total_max_feeding_length": 900.0,
            "parkposition_to_toolhead_length": 650.0,
            "toolchange_load_length": 70.0,
            "parkposition_to_rdm_length": 550.0,
            "incremental_feeding_length": 10.0,
            "incremental_feeding_speed": 5.0,
            "extruder_feeding_length": 85.0,
            "extruder_feeding_speed": 2.5,
            "toolhead_slow_loading_speed": 2.5,
            "heartbeat_interval": 1.0,
            "max_dryer_temperature": 70.0,
            "toolhead_full_purge_length": 150.0,
            "timeout_multiplier": 1.0,
            "feed_assist_active_after_ace_connect": True,
        }
        
        # Instance 0 was at topology 2, instance 1 was at topology 3
        instance0 = AceInstance(0, ace_config, mock_printer)
        instance0._feed_assist_index = 0
        instance0._feed_assist_topology_position = 2
        
        instance1 = AceInstance(1, ace_config, mock_printer)
        instance1._feed_assist_index = -1  # No feed assist
        instance1._feed_assist_topology_position = None
        
        # After power cycle, instance 0 connects to physical ACE 1 (topology 3)
        instance0.serial_mgr.get_usb_topology_position = Mock(return_value=3)
        instance0.serial_mgr.send_request = Mock()
        instance0._pending_feed_assist_restore = 0
        
        instance0._maybe_restore_pending_feed_assist()
        
        # Instance 0 should REFUSE to restore feed assist (wrong physical ACE)
        assert instance0.serial_mgr.send_request.call_count == 0
        assert instance0._feed_assist_index == -1
