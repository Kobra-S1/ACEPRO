"""
Test repeated status updates to ensure change detection works correctly.

This test file specifically addresses the bug where DEFAULT_TEMP=0 caused
an endless loop of "Slot X ready with no metadata" messages because the
code kept detecting temp=0 as "missing metadata" and re-applying defaults.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
from ace.instance import AceInstance, INSTANCE_MANAGERS


class TestRepeatedStatusUpdates(unittest.TestCase):
    """Test that repeated identical status updates don't trigger change detection."""

    def setUp(self):
        """Set up test fixtures."""
        INSTANCE_MANAGERS.clear()
        self.mock_printer = Mock()
        self.mock_printer.lookup_object = Mock(return_value=Mock())
        
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
            'status_debug_logging': True,
            'feed_length': 500,
            'retract_length': 550,
            'long_retract_length': 600,
            'pre_cut_retract_length': 50,
            'assist_motor_active_time': 2.0,
        }

    @patch('ace.instance.AceSerialManager')
    def test_non_rfid_defaults_applied_once_not_repeatedly(self, mock_serial_mgr_class):
        """Verify defaults are applied once, then repeated status updates don't trigger changes."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        # Mock the gcode.respond_info to track log messages
        log_messages = []
        instance.gcode.respond_info = Mock(side_effect=lambda msg: log_messages.append(msg))
        
        # Simulate first status update - slot ready with no metadata
        response1 = {
            'result': {
                'slots': [
                    {'index': 0, 'status': 'ready', 'rfid': 0}
                ]
            }
        }
        instance._status_update_callback(response1)
        
        # First update should apply defaults and log
        slot = instance.inventory[0]
        self.assertEqual(slot['material'], 'Unknown')
        self.assertEqual(slot['temp'], 0)
        self.assertEqual(slot['color'], [0, 0, 0])
        self.assertEqual(slot['status'], 'ready')
        
        # Check that default message was logged
        default_messages = [msg for msg in log_messages if 'ready with no metadata' in msg]
        self.assertEqual(len(default_messages), 1, "Default message should be logged once")
        
        # Clear log messages
        log_messages.clear()
        
        # Simulate second identical status update (simulating continuous polling)
        response2 = {
            'result': {
                'slots': [
                    {'index': 0, 'status': 'ready', 'rfid': 0}
                ]
            }
        }
        instance._status_update_callback(response2)
        
        # Values should remain the same
        slot = instance.inventory[0]
        self.assertEqual(slot['material'], 'Unknown')
        self.assertEqual(slot['temp'], 0)
        self.assertEqual(slot['color'], [0, 0, 0])
        self.assertEqual(slot['status'], 'ready')
        
        # Check that NO additional default message was logged
        default_messages = [msg for msg in log_messages if 'ready with no metadata' in msg]
        self.assertEqual(len(default_messages), 0, 
                        "Default message should NOT be logged on second identical status update")
        
        # Simulate third identical update to ensure stability
        log_messages.clear()
        instance._status_update_callback(response2)
        
        default_messages = [msg for msg in log_messages if 'ready with no metadata' in msg]
        self.assertEqual(len(default_messages), 0,
                        "Default message should NOT be logged on third identical status update")

    @patch('ace.instance.AceSerialManager')
    def test_repeated_status_with_defaults_stable(self, mock_serial_mgr_class):
        """Verify repeated status updates with default values remain stable."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        log_messages = []
        instance.gcode.respond_info = Mock(side_effect=lambda msg: log_messages.append(msg))
        
        # First update - applies defaults
        response = {
            'result': {
                'slots': [
                    {'index': 0, 'status': 'ready', 'rfid': 0}
                ]
            }
        }
        instance._status_update_callback(response)
        
        # Verify defaults applied
        slot = instance.inventory[0]
        self.assertEqual(slot['temp'], 0)
        self.assertEqual(slot['color'], [0, 0, 0])
        self.assertEqual(slot['material'], 'Unknown')
        
        # Count initial messages
        initial_count = len([msg for msg in log_messages if 'ready with no metadata' in msg])
        self.assertEqual(initial_count, 1, "Should log once on first update")
        
        log_messages.clear()
        
        # Simulate 5 more repeated status updates (like continuous polling)
        for i in range(5):
            instance._status_update_callback(response)
            
        # Verify NO additional "ready with no metadata" messages
        repeated_count = len([msg for msg in log_messages if 'ready with no metadata' in msg])
        self.assertEqual(repeated_count, 0,
                        f"After 5 repeated status updates, should have 0 default messages, got {repeated_count}")

    @patch('ace.instance.AceSerialManager')
    def test_empty_to_ready_logs_once_then_stable(self, mock_serial_mgr_class):
        """Verify empty->ready transition logs once, then stable."""
        INSTANCE_MANAGERS.clear()
        instance = AceInstance(0, self.ace_config, self.mock_printer)
        INSTANCE_MANAGERS[0] = Mock()
        
        log_messages = []
        instance.gcode.respond_info = Mock(side_effect=lambda msg: log_messages.append(msg))
        
        # Start with empty slot
        response_empty = {
            'result': {
                'slots': [
                    {'index': 0, 'status': 'empty', 'rfid': 0}
                ]
            }
        }
        instance._status_update_callback(response_empty)
        self.assertEqual(instance.inventory[0]['status'], 'empty')
        
        log_messages.clear()
        
        # Transition to ready
        response_ready = {
            'result': {
                'slots': [
                    {'index': 0, 'status': 'ready', 'rfid': 0}
                ]
            }
        }
        instance._status_update_callback(response_ready)
        
        # Should log default application
        default_logs = [msg for msg in log_messages if 'ready with no metadata' in msg]
        self.assertGreater(len(default_logs), 0, "Should log when transitioning to ready")
        
        log_messages.clear()
        
        # Repeat ready status - should NOT log again
        instance._status_update_callback(response_ready)
        default_logs = [msg for msg in log_messages if 'ready with no metadata' in msg]
        self.assertEqual(len(default_logs), 0, "Should not log on repeated ready status")


if __name__ == '__main__':
    unittest.main()
