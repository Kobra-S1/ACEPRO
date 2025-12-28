"""
Test suite for retry logic in ACE instance operations.

Tests focus on:
- Retry behavior on FORBIDDEN/error responses
- Backoff timing between retries
- Success after N attempts
- Proper error messages on exhaustion
- State consistency across retries
"""

import unittest
from unittest.mock import Mock, patch, call
import time

from ace.instance import AceInstance
from ace.config import (
    INSTANCE_MANAGERS,
    FILAMENT_STATE_SPLITTER,
)


class TestFeedRetryLogic(unittest.TestCase):
    """Test execute_feed_with_retries retry behavior."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        self.mock_reactor.monotonic.return_value = 0.0
        
        self.variables = {}
        self.mock_save_vars.allVariables = self.variables
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
            'rfid_temp_mode': 'average',
        }

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        return default

    @patch('ace.instance.AceSerialManager')
    def test_feed_succeeds_on_first_attempt(self, mock_serial_mgr_class):
        """Feed should succeed immediately if first attempt returns success."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock successful response on first attempt
        instance.feed_filament_with_wait_for_response = Mock(return_value={'code': 0, 'msg': 'OK'})
        instance.wait_ready = Mock()
        
        # Should not raise
        instance.execute_feed_with_retries(0, 100, 50)
        
        # Should only call once
        instance.feed_filament_with_wait_for_response.assert_called_once_with(0, 100, 50)
        
        # Should save filament position
        self.assertIn('ace_filament_pos', self.variables)
        self.assertEqual(self.variables['ace_filament_pos'], FILAMENT_STATE_SPLITTER)

    @patch('ace.instance.AceSerialManager')
    def test_feed_retries_on_forbidden_then_succeeds(self, mock_serial_mgr_class):
        """Feed should retry on FORBIDDEN and succeed on second attempt."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock FORBIDDEN then success
        instance.feed_filament_with_wait_for_response = Mock(side_effect=[
            {'code': 1, 'msg': 'FORBIDDEN'},
            {'code': 0, 'msg': 'OK'}
        ])
        instance.wait_ready = Mock()
        instance.dwell = Mock()
        
        instance.execute_feed_with_retries(0, 100, 50)
        
        # Should call twice
        self.assertEqual(instance.feed_filament_with_wait_for_response.call_count, 2)
        
        # Should dwell between attempts
        instance.dwell.assert_called_once_with(delay=1.0)
        
        # Should save position after success
        self.assertEqual(self.variables['ace_filament_pos'], FILAMENT_STATE_SPLITTER)

    @patch('ace.instance.AceSerialManager')
    def test_feed_exhausts_retries_on_repeated_forbidden(self, mock_serial_mgr_class):
        """Feed should raise ValueError after 3 FORBIDDEN responses."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock all FORBIDDEN
        instance.feed_filament_with_wait_for_response = Mock(return_value={'code': 1, 'msg': 'FORBIDDEN'})
        instance.wait_ready = Mock()
        instance.dwell = Mock()
        
        with self.assertRaises(ValueError) as ctx:
            instance.execute_feed_with_retries(0, 100, 50)
        
        # Should try 3 times
        self.assertEqual(instance.feed_filament_with_wait_for_response.call_count, 3)
        
        # Should dwell 2 times (between attempt 1-2 and 2-3, but not after 3rd)
        self.assertEqual(instance.dwell.call_count, 2)
        
        # Error message should mention FORBIDDEN
        self.assertIn('FORBIDDEN', str(ctx.exception))
        
        # Should NOT save position on failure
        self.assertNotIn('ace_filament_pos', self.variables)

    @patch('ace.instance.AceSerialManager')
    def test_feed_fails_immediately_on_non_forbidden_error(self, mock_serial_mgr_class):
        """Feed should fail immediately on non-FORBIDDEN error (no retry)."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        
        # Mock error that's not FORBIDDEN
        instance.feed_filament_with_wait_for_response = Mock(return_value={'code': 2, 'msg': 'SENSOR_ERROR'})
        instance.wait_ready = Mock()
        instance.dwell = Mock()
        
        with self.assertRaises(ValueError) as ctx:
            instance.execute_feed_with_retries(0, 100, 50)
        
        # Should try only once (no retry for non-FORBIDDEN)
        instance.feed_filament_with_wait_for_response.assert_called_once()
        
        # Should not dwell
        instance.dwell.assert_not_called()
        
        # Error message should include the actual error
        self.assertIn('SENSOR_ERROR', str(ctx.exception))


class TestRetractRetryLogic(unittest.TestCase):
    """Test _retract retry behavior."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._mock_lookup_object
        
        # Track reactor pause calls for timing verification
        self.pause_calls = []
        def track_pause(wake_time):
            self.pause_calls.append(wake_time)
        self.mock_reactor.pause.side_effect = track_pause
        self.mock_reactor.monotonic.return_value = 100.0  # Fixed start time
        
        self.variables = {}
        self.mock_save_vars.allVariables = self.variables
        
        self.ace_config = {
            'baud': 115200,
            'timeout_multiplier': 2.0,
            'filament_runout_sensor_name_rdm': 'return_module',
            'filament_runout_sensor_name_nozzle': 'toolhead_sensor',
            'feed_speed': 100,
            'retract_speed': 100,
            'total_max_feeding_length': 1000,
            'parkposition_to_toolhead_length': 500,
            'toolchange_load_length': 480,
            'parkposition_to_rdm_length': 350,
            'incremental_feeding_length': 10,
            'incremental_feeding_speed': 50,
            'extruder_feeding_length': 50,
            'extruder_feeding_speed': 5,
            'toolhead_slow_loading_speed': 10,
            'heartbeat_interval': 1.0,
            'max_dryer_temperature': 70,
            'toolhead_full_purge_length': 100,
            'rfid_inventory_sync_enabled': True,
            'rfid_temp_mode': 'average',
        }

    def _mock_lookup_object(self, name, default=None):
        """Mock printer lookup_object."""
        if name == 'gcode':
            return self.mock_gcode
        elif name == 'save_variables':
            return self.mock_save_vars
        return default

    @patch('ace.instance.time')
    @patch('ace.instance.AceSerialManager')
    def test_retract_succeeds_on_first_attempt(self, mock_serial_mgr_class, mock_time):
        """Retract should succeed immediately if first attempt works."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance.wait_ready = Mock()
        instance._is_slot_empty = Mock(return_value=False)
        
        # Mock time progression for dwell loop
        # Expected time = 50mm / 100mm/s = 0.5s, need to provide time values for the dwell loop
        time_values = [100.0, 100.0]  # Initial times
        time_values.extend([100.0 + i*0.05 for i in range(1, 15)])  # Dwell loop iterations
        time_values.extend([100.6, 100.6, 100.7])  # After dwell, for wait_ready
        mock_time.time.side_effect = time_values
        
        # Mock successful response
        def send_with_response(request, callback):
            callback({'code': 0, 'msg': 'OK'})
        instance.serial_mgr.send_request = Mock(side_effect=send_with_response)
        
        response = instance._retract(0, 50, 100)
        
        # Should succeed
        self.assertEqual(response['code'], 0)
        
        # Should only try once
        instance.serial_mgr.send_request.assert_called_once()

    @patch('ace.instance.time')
    @patch('ace.instance.AceSerialManager')
    def test_retract_retries_on_no_response(self, mock_serial_mgr_class, mock_time):
        """Retract should retry up to 3 times if no response received."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance.wait_ready = Mock()
        instance._is_slot_empty = Mock(return_value=False)
        
        # Mock time progression - timeout on each attempt
        mock_time.time.side_effect = [
            # Attempt 1
            100.0, 100.0, *[100.0 + i*0.1 for i in range(1, 60)],  # Timeout after 5s
            # Attempt 2
            105.0, 105.0, *[105.0 + i*0.1 for i in range(1, 60)],  # Timeout after 5s
            # Attempt 3
            110.0, 110.0, *[110.0 + i*0.1 for i in range(1, 60)],  # Timeout after 5s
        ]
        
        # No response callback (simulates timeout)
        instance.serial_mgr.send_request = Mock()
        
        with self.assertRaises(ValueError) as ctx:
            instance._retract(0, 50, 100)
        
        # Should try 3 times
        self.assertEqual(instance.serial_mgr.send_request.call_count, 3)
        
        # Error message should mention attempts
        self.assertIn('no response after 3 attempts', str(ctx.exception))

    @patch('ace.instance.time')
    @patch('ace.instance.AceSerialManager')
    def test_retract_succeeds_on_second_attempt_after_forbidden(self, mock_serial_mgr_class, mock_time):
        """Retract should retry and succeed after FORBIDDEN response."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance.wait_ready = Mock()
        instance._is_slot_empty = Mock(return_value=False)
        
        # Mock time progression
        time_counter = [100.0]
        def time_advance():
            time_counter[0] += 0.1
            return time_counter[0]
        mock_time.time.side_effect = lambda: time_advance()
        
        # Mock FORBIDDEN then success
        attempt = [0]
        def send_with_response(request, callback):
            if attempt[0] == 0:
                attempt[0] += 1
                callback({'code': 1, 'msg': 'FORBIDDEN'})
            else:
                callback({'code': 0, 'msg': 'OK'})
        instance.serial_mgr.send_request = Mock(side_effect=send_with_response)
        
        response = instance._retract(0, 50, 100)
        
        # Should succeed on second attempt
        self.assertEqual(response['code'], 0)
        self.assertEqual(instance.serial_mgr.send_request.call_count, 2)

    @patch('ace.instance.time')
    @patch('ace.instance.AceSerialManager')
    def test_retract_waits_between_retries(self, mock_serial_mgr_class, mock_time):
        """Retract should wait 2s between retry attempts."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        instance.wait_ready = Mock()
        instance._is_slot_empty = Mock(return_value=False)
        
        # Track pause calls
        pause_delays = []
        original_monotonic = self.mock_reactor.monotonic
        def track_pause(wake_time):
            current_time = original_monotonic()
            delay = wake_time - current_time
            pause_delays.append(delay)
        self.mock_reactor.pause = Mock(side_effect=track_pause)
        
        # Mock time
        time_counter = [100.0]
        def time_advance():
            time_counter[0] += 0.1
            return time_counter[0]
        mock_time.time.side_effect = lambda: time_advance()
        
        # Mock all FORBIDDEN to force retries
        def send_forbidden(request, callback):
            callback({'code': 1, 'msg': 'FORBIDDEN'})
        instance.serial_mgr.send_request = Mock(side_effect=send_forbidden)
        
        try:
            instance._retract(0, 50, 100)
        except ValueError:
            pass  # Expected to fail after retries
        
        # Should have 2s pauses between attempts
        # Find the 2s pauses (retry delays)
        retry_pauses = [d for d in pause_delays if abs(d - 2.0) < 0.01]
        self.assertEqual(len(retry_pauses), 2, f"Expected 2 retry pauses of 2s, got: {pause_delays}")


if __name__ == '__main__':
    unittest.main()
