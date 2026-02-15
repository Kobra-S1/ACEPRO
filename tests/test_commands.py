"""
Test suite for ace.commands module.

Smoke tests for all command functions to ensure they can be called
without critical errors. Uses mocks for hardware dependencies.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
import ace.commands
from ace.config import ACE_INSTANCES, INSTANCE_MANAGERS


class TestCommandsModuleStructure:
    """Test ace.commands module structure and exports."""

    def test_module_imports(self):
        """Test that ace.commands module can be imported."""
        assert ace.commands is not None

    def test_has_get_printer_function(self):
        """Test that get_printer function exists."""
        assert hasattr(ace.commands, 'get_printer')
        assert callable(ace.commands.get_printer)

    def test_has_ace_get_instance_function(self):
        """Test that ace_get_instance function exists."""
        assert hasattr(ace.commands, 'ace_get_instance')
        assert callable(ace.commands.ace_get_instance)

    def test_has_for_each_instance_function(self):
        """Test that for_each_instance helper function exists."""
        assert hasattr(ace.commands, 'for_each_instance')
        assert callable(ace.commands.for_each_instance)


@pytest.fixture
def mock_gcmd():
    """Create a mock GCode command object."""
    gcmd = Mock()
    gcmd.get_command_parameters = Mock(return_value={})
    gcmd.get = Mock(return_value=None)
    gcmd.get_int = Mock(return_value=0)
    gcmd.get_float = Mock(return_value=0.0)
    gcmd.respond_info = Mock()
    gcmd.error = Mock(side_effect=Exception)
    return gcmd


@pytest.fixture
def mock_ace_instance():
    """Create a mock ACE instance."""
    instance = Mock()
    instance.instance_num = 0
    instance.tool_offset = 0
    instance.SLOT_COUNT = 4
    instance.feed_speed = 60
    instance.retract_speed = 50
    instance.max_dryer_temperature = 60
    instance.inventory = [
        {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0}
        for _ in range(4)
    ]
    instance.send_request = Mock()
    instance.serial_mgr = Mock()
    instance.serial_mgr.reconnect = Mock()
    instance._feed = Mock()
    instance._stop_feed = Mock()
    instance._retract = Mock()
    instance._stop_retract = Mock()
    instance._enable_feed_assist = Mock()
    instance._disable_feed_assist = Mock()
    instance._dryer_active = False
    instance._dryer_temperature = 0
    instance._dryer_duration = 0
    instance.reset_persistent_inventory = Mock()
    instance.reset_feed_assist_state = Mock()
    return instance


@pytest.fixture
def mock_ace_manager():
    """Create a mock ACE manager."""
    manager = Mock()
    manager.ace_count = 1
    manager.runout_monitor = Mock()
    manager.runout_monitor.runout_detection_active = False
    manager.toolchange_purge_length = 50.0
    manager.toolchange_purge_speed = 400.0
    manager.get_ace_global_enabled = Mock(return_value=True)
    manager.get_switch_state = Mock(return_value=False)
    manager.is_filament_path_free = Mock(return_value=True)
    manager.smart_unload = Mock(return_value=True)
    manager.smart_load = Mock(return_value=True)
    manager.full_unload_slot = Mock(return_value=True)
    manager.perform_tool_change = Mock(return_value="Success")
    manager.check_and_wait_for_spool_ready = Mock()
    manager.set_and_save_variable = Mock()
    manager._sync_inventory_to_persistent = Mock()
    manager._sensor_override = None
    
    mock_printer = Mock()
    mock_printer.lookup_object = Mock(return_value=Mock())
    mock_printer.get_reactor = Mock(return_value=Mock())
    manager.get_printer = Mock(return_value=mock_printer)
    manager.printer = mock_printer
    manager.gcode = Mock()
    manager.gcode.respond_info = Mock()
    manager.gcode.run_script_from_command = Mock()
    
    return manager


@pytest.fixture
def setup_mocks(mock_ace_instance, mock_ace_manager):
    """Setup global mocks for ACE instances and managers."""
    ACE_INSTANCES[0] = mock_ace_instance
    INSTANCE_MANAGERS[0] = mock_ace_manager
    mock_ace_manager.instances = {0: mock_ace_instance}
    
    yield
    
    # Cleanup
    ACE_INSTANCES.clear()
    INSTANCE_MANAGERS.clear()


class TestCommandSmoke:
    """Smoke tests for all ACE commands."""

    def test_cmd_ACE_GET_STATUS_single_instance(self, mock_gcmd, setup_mocks):
        """Test ACE_GET_STATUS with specific instance."""
        mock_gcmd.get_int = Mock(return_value=0)
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        assert mock_gcmd.respond_info.called or ACE_INSTANCES[0].send_request.called

    def test_cmd_ACE_GET_STATUS_all_instances(self, mock_gcmd, setup_mocks):
        """Test ACE_GET_STATUS for all instances."""
        mock_gcmd.get_int = Mock(return_value=None)
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_GET_STATUS_includes_dryer_status(self, mock_gcmd, setup_mocks):
        """Test that ACE_GET_STATUS response includes dryer_status field."""
        import json
        mock_gcmd.get_int = Mock(return_value=0)
        
        # Mock the ACE instance to return dryer_status in response
        mock_response = {
            "code": 0,
            "result": {
                "status": "ready",
                "temp": 45,
                "dryer_status": {
                    "status": "drying",
                    "target_temp": 45,
                    "duration": 240,
                    "remain_time": 14000
                }
            }
        }
        
        # Capture the callback function that will be called
        captured_callback = None
        def capture_callback(request, callback):
            nonlocal captured_callback
            captured_callback = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        
        # Execute the command
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        
        # Verify send_request was called
        assert ACE_INSTANCES[0].send_request.called
        assert captured_callback is not None
        
        # Execute the callback with our mock response
        captured_callback(mock_response)
        
        # Verify respond_info was called with JSON containing dryer_status
        assert mock_gcmd.respond_info.called
        call_args = mock_gcmd.respond_info.call_args_list
        
        # Find the JSON response
        json_response = None
        for call in call_args:
            arg = call[0][0]
            if arg.startswith('// {'):
                json_str = arg[3:]  # Remove '// ' prefix
                json_response = json.loads(json_str)
                break
        
        # Verify the JSON response contains all required fields
        assert json_response is not None, "No JSON response found"
        assert 'instance' in json_response
        assert 'status' in json_response
        assert 'temp' in json_response
        assert 'dryer_status' in json_response, "dryer_status field is missing from response"
        
        # Verify dryer_status structure
        dryer_status = json_response['dryer_status']
        assert 'status' in dryer_status
        assert 'target_temp' in dryer_status
        assert 'duration' in dryer_status
        assert 'remain_time' in dryer_status
        
        # Verify values match the mock
        assert json_response['temp'] == 45
        assert dryer_status['status'] == 'drying'
        assert dryer_status['target_temp'] == 45
        assert dryer_status['duration'] == 240
        assert dryer_status['remain_time'] == 14000

    def test_cmd_ACE_GET_STATUS_dryer_status_empty_when_stopped(self, mock_gcmd, setup_mocks):
        """Test that ACE_GET_STATUS includes empty dryer_status when dryer is stopped."""
        import json
        mock_gcmd.get_int = Mock(return_value=0)
        
        # Mock response with dryer stopped
        mock_response = {
            "code": 0,
            "result": {
                "status": "ready",
                "temp": 25,
                "dryer_status": {}  # Empty when stopped
            }
        }
        
        captured_callback = None
        def capture_callback(request, callback):
            nonlocal captured_callback
            captured_callback = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        
        assert captured_callback is not None
        captured_callback(mock_response)
        
        # Verify dryer_status field exists even when empty
        call_args = mock_gcmd.respond_info.call_args_list
        json_response = None
        for call in call_args:
            arg = call[0][0]
            if arg.startswith('// {'):
                json_str = arg[3:]
                json_response = json.loads(json_str)
                break
        
        assert json_response is not None
        assert 'dryer_status' in json_response, "dryer_status field must be present even when empty"
        assert isinstance(json_response['dryer_status'], dict)

    def test_cmd_ACE_GET_STATUS_verbose_rfid_labels(self, mock_gcmd, setup_mocks):
        """Test that ACE_GET_STATUS VERBOSE=1 displays RFID states with correct text labels."""
        import json
        from ace.config import RFID_STATE_NO_INFO, RFID_STATE_FAILED, RFID_STATE_IDENTIFIED, RFID_STATE_IDENTIFYING
        
        # Mock parameters to request verbose output
        mock_gcmd.get_int = Mock(side_effect=lambda key, default=None: {
            "INSTANCE": 0,
            "VERBOSE": 1
        }.get(key, default))
        
        # Mock response with all RFID states
        mock_response = {
            "code": 0,
            "result": {
                "status": "ready",
                "temp": 25,
                "dryer_status": {},
                "slots": [
                    {"index": 0, "status": "ready", "rfid": RFID_STATE_NO_INFO},
                    {"index": 1, "status": "ready", "rfid": RFID_STATE_FAILED},
                    {"index": 2, "status": "ready", "rfid": RFID_STATE_IDENTIFIED},
                    {"index": 3, "status": "ready", "rfid": RFID_STATE_IDENTIFYING}
                ]
            }
        }
        
        captured_callback = None
        def capture_callback(request, callback):
            nonlocal captured_callback
            captured_callback = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        
        # Execute the command
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        
        # Verify and execute callback
        assert captured_callback is not None
        captured_callback(mock_response)
        
        # Verify verbose output contains text labels, not numbers
        call_args = mock_gcmd.respond_info.call_args_list
        verbose_output = "\n".join([call[0][0] for call in call_args])
        
        # Check for text labels (not numeric values)
        assert "rfid=no_tag" in verbose_output, "RFID_STATE_NO_INFO should display as 'no_tag'"
        assert "rfid=failed" in verbose_output, "RFID_STATE_FAILED should display as 'failed'"
        assert "rfid=identified" in verbose_output, "RFID_STATE_IDENTIFIED should display as 'identified'"
        assert "rfid=identifying" in verbose_output, "RFID_STATE_IDENTIFYING should display as 'identifying'"
        
        # Ensure numeric values are NOT present
        assert "rfid=0" not in verbose_output
        assert "rfid=1" not in verbose_output
        assert "rfid=2" not in verbose_output
        assert "rfid=3" not in verbose_output

    def test_cmd_ACE_GET_STATUS_verbose_slot_formatting(self, mock_gcmd, setup_mocks):
        """Test that ACE_GET_STATUS VERBOSE=1 formats slot data correctly."""
        mock_gcmd.get_int = Mock(side_effect=lambda key, default=None: {
            "INSTANCE": 0,
            "VERBOSE": 1
        }.get(key, default))
        
        # Mock response with diverse slot data
        mock_response = {
            "code": 0,
            "result": {
                "status": "ready",
                "temp": 30,
                "dryer_status": {},
                "slots": [
                    {
                        "index": 0,
                        "status": "ready",
                        "sku": "PLA-001",
                        "type": "PLA",
                        "color": [255, 0, 0],
                        "rfid": 2,
                        "icon_type": 1
                    }
                ]
            }
        }
        
        # Mock inventory with RFID temperature data
        ACE_INSTANCES[0].inventory = [
            {
                "status": "ready",
                "extruder_temp": {"min": 200, "max": 220},
                "hotbed_temp": {"min": 60, "max": 80},
                "diameter": 1.75,
                "total": 1000,
                "current": 750
            }
        ]
        
        captured_callback = None
        def capture_callback(request, callback):
            nonlocal captured_callback
            captured_callback = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        
        assert captured_callback is not None
        captured_callback(mock_response)
        
        # Verify verbose output contains all expected fields
        call_args = mock_gcmd.respond_info.call_args_list
        verbose_output = "\n".join([call[0][0] for call in call_args])
        
        assert "index=0" in verbose_output
        assert "status=ready" in verbose_output
        assert "sku=PLA-001" in verbose_output
        assert "type=PLA" in verbose_output
        assert "color=RGB(255,0,0)" in verbose_output
        assert "extruder_temp=200-220°C" in verbose_output
        assert "hotbed_temp=60-80°C" in verbose_output
        assert "diameter=1.75mm" in verbose_output
        assert "spool=750/1000m" in verbose_output

    def test_cmd_ACE_GET_STATUS_verbose_dryer_formatting(self, mock_gcmd, setup_mocks):
        """Test that ACE_GET_STATUS VERBOSE=1 formats dryer status correctly."""
        mock_gcmd.get_int = Mock(side_effect=lambda key, default=None: {
            "INSTANCE": 0,
            "VERBOSE": 1
        }.get(key, default))
        
        mock_response = {
            "code": 0,
            "result": {
                "status": "ready",
                "temp": 45,
                "dryer_status": {
                    "status": "drying",
                    "target_temp": 45,
                    "duration": 240,
                    "remain_time": 14000
                },
                "slots": []
            }
        }
        
        captured_callback = None
        def capture_callback(request, callback):
            nonlocal captured_callback
            captured_callback = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        
        assert captured_callback is not None
        captured_callback(mock_response)
        
        call_args = mock_gcmd.respond_info.call_args_list
        verbose_output = "\n".join([call[0][0] for call in call_args])
        
        # Dryer status should be formatted on one line
        assert "Dryer:" in verbose_output
        assert "status=drying" in verbose_output
        assert "target_temp=45°C" in verbose_output
        assert "duration=240min" in verbose_output
        assert "remain_time=14000min" in verbose_output

    def test_cmd_ACE_GET_STATUS_verbose_all_instances(self, mock_gcmd, setup_mocks):
        """Test that ACE_GET_STATUS VERBOSE=1 queries all instances correctly."""
        mock_gcmd.get_int = Mock(side_effect=lambda key, default=None: {
            "INSTANCE": None,
            "VERBOSE": 1
        }.get(key, default))
        
        mock_response = {
            "code": 0,
            "result": {
                "status": "ready",
                "temp": 25,
                "dryer_status": {},
                "slots": []
            }
        }
        
        captured_callbacks = []
        def capture_callback(request, callback):
            captured_callbacks.append(callback)
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        
        # Execute all callbacks
        for callback in captured_callbacks:
            callback(mock_response)
        
        call_args = mock_gcmd.respond_info.call_args_list
        verbose_output = "\n".join([call[0][0] for call in call_args])
        
        # Should have verbose header
        assert "ACE Status (All Instances - Verbose)" in verbose_output
        # Should have instance status
        assert "ACE Instance" in verbose_output

    def test_cmd_ACE_GET_STATUS_verbose_unknown_fields(self, mock_gcmd, setup_mocks):
        """Test that ACE_GET_STATUS VERBOSE=1 displays unknown fields under Additional Fields."""
        mock_gcmd.get_int = Mock(side_effect=lambda key, default=None: {
            "INSTANCE": 0,
            "VERBOSE": 1
        }.get(key, default))
        
        mock_response = {
            "code": 0,
            "result": {
                "status": "ready",
                "temp": 25,
                "dryer_status": {},
                "slots": [],
                "unknown_field_1": "test_value",
                "unknown_field_2": 42
            }
        }
        
        captured_callback = None
        def capture_callback(request, callback):
            nonlocal captured_callback
            captured_callback = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        
        assert captured_callback is not None
        captured_callback(mock_response)
        
        call_args = mock_gcmd.respond_info.call_args_list
        verbose_output = "\n".join([call[0][0] for call in call_args])
        
        # Unknown fields should appear under "Additional Fields"
        assert "Additional Fields:" in verbose_output
        assert "unknown_field_1=test_value" in verbose_output
        assert "unknown_field_2=42" in verbose_output

    def test_cmd_ACE_GET_STATUS_callback_error_response(self, mock_gcmd, setup_mocks):
        """Test ACE_GET_STATUS handles error response from device."""
        mock_gcmd.get_int = Mock(return_value=0)
        
        # Mock error response
        mock_response = {
            "code": 1,
            "msg": "Device communication error"
        }
        
        captured_callback = None
        def capture_callback(request, callback):
            nonlocal captured_callback
            captured_callback = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        
        assert captured_callback is not None
        captured_callback(mock_response)
        
        # Verify error message was displayed
        call_args = mock_gcmd.respond_info.call_args_list
        error_output = "\n".join([call[0][0] for call in call_args])
        assert "Status query failed" in error_output
        assert "Device communication error" in error_output

    def test_cmd_ACE_GET_STATUS_callback_no_response(self, mock_gcmd, setup_mocks):
        """Test ACE_GET_STATUS handles None response (timeout/disconnected)."""
        mock_gcmd.get_int = Mock(return_value=0)
        
        captured_callback = None
        def capture_callback(request, callback):
            nonlocal captured_callback
            captured_callback = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        
        assert captured_callback is not None
        captured_callback(None)  # Simulate no response
        
        # Verify appropriate error message
        call_args = mock_gcmd.respond_info.call_args_list
        error_output = "\n".join([call[0][0] for call in call_args])
        assert "Status query failed" in error_output
        assert "No response" in error_output

    def test_cmd_ACE_GET_STATUS_backward_compat_dryer_field(self, mock_gcmd, setup_mocks):
        """Test ACE_GET_STATUS handles legacy 'dryer' field (backward compatibility)."""
        import json
        mock_gcmd.get_int = Mock(return_value=0)
        
        # Mock response with old 'dryer' field instead of 'dryer_status'
        mock_response = {
            "code": 0,
            "result": {
                "status": "ready",
                "temp": 45,
                "dryer": {  # Old field name
                    "status": "drying",
                    "target_temp": 45,
                    "duration": 240
                }
            }
        }
        
        captured_callback = None
        def capture_callback(request, callback):
            nonlocal captured_callback
            captured_callback = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        
        assert captured_callback is not None
        captured_callback(mock_response)
        
        # Verify dryer data is still included in response
        call_args = mock_gcmd.respond_info.call_args_list
        json_response = None
        for call in call_args:
            arg = call[0][0]
            if arg.startswith('// {'):
                json_str = arg[3:]
                json_response = json.loads(json_str)
                break
        
        assert json_response is not None
        assert 'dryer_status' in json_response
        assert json_response['dryer_status']['status'] == 'drying'
        assert json_response['dryer_status']['target_temp'] == 45

    def test_cmd_ACE_GET_STATUS_all_instances_error_one_instance(self, mock_gcmd, setup_mocks):
        """Test ACE_GET_STATUS all instances when one instance fails."""
        mock_gcmd.get_int = Mock(return_value=None)
        
        # Mock mixed responses - one success, one error
        responses = [
            {"code": 0, "result": {"status": "ready", "temp": 25, "dryer_status": {}}},
        ]
        
        captured_callbacks = []
        def capture_callback(request, callback):
            captured_callbacks.append(callback)
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        
        # Execute callback with success
        if len(captured_callbacks) > 0:
            captured_callbacks[0](responses[0])
        
        call_args = mock_gcmd.respond_info.call_args_list
        output = "\n".join([call[0][0] for call in call_args])
        
        # Should have the header
        assert "ACE Status (All Instances)" in output

    def test_cmd_ACE_GET_STATUS_verbose_slot_with_empty_sku(self, mock_gcmd, setup_mocks):
        """Test verbose mode doesn't show empty SKU."""
        mock_gcmd.get_int = Mock(side_effect=lambda key, default=None: {
            "INSTANCE": 0,
            "VERBOSE": 1
        }.get(key, default))
        
        mock_response = {
            "code": 0,
            "result": {
                "status": "ready",
                "temp": 25,
                "dryer_status": {},
                "slots": [
                    {
                        "index": 0,
                        "status": "empty",
                        "sku": "",  # Empty SKU should not appear in output
                        "type": ""
                    }
                ]
            }
        }
        
        captured_callback = None
        def capture_callback(request, callback):
            nonlocal captured_callback
            captured_callback = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        
        assert captured_callback is not None
        captured_callback(mock_response)
        
        call_args = mock_gcmd.respond_info.call_args_list
        verbose_output = "\n".join([call[0][0] for call in call_args])
        
        # Empty SKU should not be in output (SKU field shows when sku is not empty)
        slot_line = [line for line in verbose_output.split('\n') if 'index=0' in line][0]
        # If sku is empty, it shouldn't appear
        assert slot_line.count("sku=") == 0 or "sku=" not in slot_line

    def test_cmd_ACE_GET_STATUS_exception_handling(self, mock_gcmd, setup_mocks):
        """Test ACE_GET_STATUS catches and reports exceptions."""
        mock_gcmd.get_int = Mock(side_effect=Exception("Test exception"))
        
        # Should not raise exception, should report error
        ace.commands.cmd_ACE_GET_STATUS(mock_gcmd)
        
        # Verify error was reported
        assert mock_gcmd.respond_info.called
        call_args = mock_gcmd.respond_info.call_args_list
        output = "\n".join([call[0][0] for call in call_args])
        assert "ACE_GET_STATUS error" in output

    def test_cmd_ACE_RECONNECT_single(self, mock_gcmd, setup_mocks):
        """Test ACE_RECONNECT with specific instance."""
        mock_gcmd.get_int = Mock(return_value=0)
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0})
        ace.commands.cmd_ACE_RECONNECT(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_RECONNECT_all(self, mock_gcmd, setup_mocks):
        """Test ACE_RECONNECT for all instances."""
        mock_gcmd.get_int = Mock(return_value=None)
        ace.commands.cmd_ACE_RECONNECT(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_GET_CURRENT_INDEX(self, mock_gcmd, setup_mocks):
        """Test ACE_GET_CURRENT_INDEX."""
        with patch('ace.commands.get_printer') as mock_get_printer:
            mock_printer = Mock()
            mock_save_vars = Mock()
            mock_save_vars.allVariables = {"ace_current_index": 0}
            mock_printer.lookup_object = Mock(return_value=mock_save_vars)
            mock_get_printer.return_value = mock_printer
            
            ace.commands.cmd_ACE_GET_CURRENT_INDEX(mock_gcmd)
            assert mock_gcmd.respond_info.called

    def test_cmd_ACE_FEED(self, mock_gcmd, setup_mocks):
        """Test ACE_FEED command."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 100, 60])
        
        ace.commands.cmd_ACE_FEED(mock_gcmd)
        assert ACE_INSTANCES[0]._feed.called

    def test_cmd_ACE_STOP_FEED(self, mock_gcmd, setup_mocks):
        """Test ACE_STOP_FEED command."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0])
        
        ace.commands.cmd_ACE_STOP_FEED(mock_gcmd)
        assert ACE_INSTANCES[0]._stop_feed.called

    def test_cmd_ACE_RETRACT(self, mock_gcmd, setup_mocks):
        """Test ACE_RETRACT command."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 100, 50])
        
        ace.commands.cmd_ACE_RETRACT(mock_gcmd)
        assert ACE_INSTANCES[0]._retract.called

    def test_cmd_ACE_STOP_RETRACT(self, mock_gcmd, setup_mocks):
        """Test ACE_STOP_RETRACT command."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0])
        
        ace.commands.cmd_ACE_STOP_RETRACT(mock_gcmd)
        assert ACE_INSTANCES[0]._stop_retract.called

    def test_cmd_ACE_SET_SLOT_empty(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT with EMPTY=1."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 1])
        
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_SET_SLOT_with_material(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT with material info."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 0, 210])
        mock_gcmd.get = Mock(side_effect=["255,0,0", "PLA"])
        
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_SET_SLOT_named_color_red(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT with named color RED."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 0, 210])
        mock_gcmd.get = Mock(side_effect=["RED", "PLA"])
        
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        assert mock_gcmd.respond_info.called
        # Verify color was set to [255, 0, 0]
        assert ACE_INSTANCES[0].inventory[0]["color"] == [255, 0, 0]

    def test_cmd_ACE_SET_SLOT_named_color_blue(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT with named color BLUE."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 1})
        mock_gcmd.get_int = Mock(side_effect=[0, 1, 0, 240])
        mock_gcmd.get = Mock(side_effect=["BLUE", "PETG"])
        
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        assert mock_gcmd.respond_info.called
        # Verify color was set to [0, 0, 255]
        assert ACE_INSTANCES[0].inventory[1]["color"] == [0, 0, 255]

    def test_cmd_ACE_SET_SLOT_named_color_case_insensitive(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT with lowercase named color."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 0, 200])
        mock_gcmd.get = Mock(side_effect=["green", "PLA"])
        
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        assert mock_gcmd.respond_info.called
        # Verify color was set to [0, 255, 0] (GREEN)
        assert ACE_INSTANCES[0].inventory[0]["color"] == [0, 255, 0]

    def test_cmd_ACE_SET_SLOT_named_color_orange(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT with named color ORANGE."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 2})
        mock_gcmd.get_int = Mock(side_effect=[0, 2, 0, 220])
        mock_gcmd.get = Mock(side_effect=["ORANGE", "ABS"])
        
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        assert mock_gcmd.respond_info.called
        # Verify color was set to [235, 128, 66]
        assert ACE_INSTANCES[0].inventory[2]["color"] == [235, 128, 66]

    def test_cmd_ACE_SET_SLOT_named_color_orca(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT with named color ORCA."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 3})
        mock_gcmd.get_int = Mock(side_effect=[0, 3, 0, 230])
        mock_gcmd.get = Mock(side_effect=["ORCA", "PETG"])
        
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        assert mock_gcmd.respond_info.called
        # Verify color was set to [0, 150, 136]
        assert ACE_INSTANCES[0].inventory[3]["color"] == [0, 150, 136]

    def test_cmd_ACE_SET_SLOT_rgb_fallback_still_works(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT still accepts R,G,B format."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 0, 215])
        mock_gcmd.get = Mock(side_effect=["128,64,192", "TPU"])
        
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        assert mock_gcmd.respond_info.called
        # Verify color was parsed from R,G,B
        assert ACE_INSTANCES[0].inventory[0]["color"] == [128, 64, 192]

    def test_cmd_ACE_SET_SLOT_invalid_named_color(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT with invalid named color that also fails R,G,B parsing."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 0, 210])
        # "PURPLE" is not a valid named color and will fail R,G,B parsing too
        mock_gcmd.get = Mock(side_effect=["PURPLE", "PLA"])
        
        # Mock error to raise exception (will be caught and converted to respond_info)
        def error_func(msg):
            raise Exception(msg)
        mock_gcmd.error = error_func
        
        # The function catches all exceptions and calls respond_info with error message
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        
        # Verify respond_info was called with error message
        assert mock_gcmd.respond_info.called
        call_args = str(mock_gcmd.respond_info.call_args)
        # Check that error message mentions color format
        assert "COLOR" in call_args or "named color" in call_args.lower()

    def test_cmd_ACE_SET_SLOT_rgb_values_clamped_to_valid_range(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT clamps out-of-range RGB values to 0-255."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 0, 210])
        mock_gcmd.get = Mock(side_effect=["500,0,-10", "PLA"])  # Invalid values
        
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        
        # Values should be clamped: 500→255, 0→0, -10→0
        assert ACE_INSTANCES[0].inventory[0]["color"] == [255, 0, 0]

    def test_cmd_ACE_SET_SLOT_rgb_with_whitespace(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT RGB parsing handles whitespace."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 0, 210])
        mock_gcmd.get = Mock(side_effect=["128, 64, 192", "PLA"])  # Spaces in RGB
        
        # Current behavior: spaces may cause parsing failure
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        
        # The int() conversion should handle leading/trailing spaces
        assert ACE_INSTANCES[0].inventory[0]["color"] == [128, 64, 192]

    def test_cmd_ACE_SET_SLOT_rgb_wrong_count(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT rejects RGB with wrong number of values."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 0, 210])
        mock_gcmd.get = Mock(side_effect=["128,64", "PLA"])  # Only 2 values
        
        def error_func(msg):
            raise Exception(msg)
        mock_gcmd.error = error_func
        
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        
        # Should have reported an error
        assert mock_gcmd.respond_info.called
        call_args = str(mock_gcmd.respond_info.call_args)
        assert "COLOR" in call_args or "error" in call_args.lower()

    def test_cmd_ACE_SAVE_INVENTORY(self, mock_gcmd, setup_mocks):
        """Test ACE_SAVE_INVENTORY."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0})
        ace.commands.cmd_ACE_SAVE_INVENTORY(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_START_DRYING_single(self, mock_gcmd, setup_mocks):
        """Test ACE_START_DRYING for single instance."""
        mock_gcmd.get_int = Mock(side_effect=[0, 50, 240])
        ace.commands.cmd_ACE_START_DRYING(mock_gcmd)
        assert ACE_INSTANCES[0].send_request.called

    def test_cmd_ACE_START_DRYING_all(self, mock_gcmd, setup_mocks):
        """Test ACE_START_DRYING for all instances."""
        mock_gcmd.get_int = Mock(side_effect=[None, 50, 240])
        ace.commands.cmd_ACE_START_DRYING(mock_gcmd)
        assert ACE_INSTANCES[0].send_request.called

    def test_cmd_ACE_STOP_DRYING_single(self, mock_gcmd, setup_mocks):
        """Test ACE_STOP_DRYING for single instance."""
        mock_gcmd.get_int = Mock(return_value=0)
        ace.commands.cmd_ACE_STOP_DRYING(mock_gcmd)
        assert ACE_INSTANCES[0].send_request.called

    def test_cmd_ACE_STOP_DRYING_all(self, mock_gcmd, setup_mocks):
        """Test ACE_STOP_DRYING for all instances."""
        mock_gcmd.get_int = Mock(return_value=None)
        ace.commands.cmd_ACE_STOP_DRYING(mock_gcmd)
        assert ACE_INSTANCES[0].send_request.called


class TestDryingCommands:
    """Comprehensive tests for ACE drying commands."""

    def test_start_drying_temperature_validation_zero(self, mock_gcmd, setup_mocks):
        """Test START_DRYING rejects temperature <= 0."""
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 240])  # instance=0, temp=0, duration=240
        mock_gcmd.error = Mock(side_effect=Exception("TEMP must be between 1 and 70°C"))
        
        ace.commands.cmd_ACE_START_DRYING(mock_gcmd)
        
        # Should log error message
        calls = [str(call) for call in mock_gcmd.respond_info.call_args_list]
        assert any("error" in str(call).lower() for call in calls)

    def test_start_drying_temperature_validation_negative(self, mock_gcmd, setup_mocks):
        """Test START_DRYING rejects negative temperature."""
        mock_gcmd.get_int = Mock(side_effect=[0, -10, 240])
        mock_gcmd.error = Mock(side_effect=Exception("TEMP must be between 1 and 70°C"))
        
        ace.commands.cmd_ACE_START_DRYING(mock_gcmd)
        
        # Should log error message
        calls = [str(call) for call in mock_gcmd.respond_info.call_args_list]
        assert any("error" in str(call).lower() for call in calls)

    def test_start_drying_temperature_validation_above_max(self, mock_gcmd, setup_mocks):
        """Test START_DRYING rejects temperature above max_dryer_temperature."""
        mock_gcmd.get_int = Mock(side_effect=[0, 100, 240])  # 100 > 70 (default max)
        mock_gcmd.error = Mock(side_effect=Exception("TEMP must be between 1 and 70°C"))
        
        ace.commands.cmd_ACE_START_DRYING(mock_gcmd)
        
        # Should log error message
        calls = [str(call) for call in mock_gcmd.respond_info.call_args_list]
        assert any("error" in str(call).lower() for call in calls)

    def test_start_drying_duration_validation_zero(self, mock_gcmd, setup_mocks):
        """Test START_DRYING rejects duration <= 0."""
        mock_gcmd.get_int = Mock(side_effect=[0, 50, 0])  # duration=0
        mock_gcmd.error = Mock(side_effect=Exception("DURATION must be positive"))
        
        ace.commands.cmd_ACE_START_DRYING(mock_gcmd)
        
        # Should log error message
        calls = [str(call) for call in mock_gcmd.respond_info.call_args_list]
        assert any("error" in str(call).lower() for call in calls)

    def test_start_drying_duration_validation_negative(self, mock_gcmd, setup_mocks):
        """Test START_DRYING rejects negative duration."""
        mock_gcmd.get_int = Mock(side_effect=[0, 50, -10])
        mock_gcmd.error = Mock(side_effect=Exception("DURATION must be positive"))
        
        ace.commands.cmd_ACE_START_DRYING(mock_gcmd)
        
        # Should log error message
        calls = [str(call) for call in mock_gcmd.respond_info.call_args_list]
        assert any("error" in str(call).lower() for call in calls)

    def test_start_drying_callback_success(self, mock_gcmd, setup_mocks):
        """Test START_DRYING callback handles successful response."""
        mock_gcmd.get_int = Mock(side_effect=[0, 50, 240])
        
        # Capture the callback function
        callback_func = None
        def capture_callback(request, callback):
            nonlocal callback_func
            callback_func = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ACE_INSTANCES[0]._dryer_start_logged = False
        
        ace.commands.cmd_ACE_START_DRYING(mock_gcmd)
        
        # Verify callback was captured
        assert callback_func is not None
        
        # Simulate successful response
        callback_func({"code": 0, "result": {}})
        
        # Should log success message
        assert mock_gcmd.respond_info.called
        calls = [str(call) for call in mock_gcmd.respond_info.call_args_list]
        assert any("Dryer started" in str(call) for call in calls)

    def test_start_drying_callback_failure(self, mock_gcmd, setup_mocks):
        """Test START_DRYING callback handles failed response."""
        mock_gcmd.get_int = Mock(side_effect=[0, 50, 240])
        
        callback_func = None
        def capture_callback(request, callback):
            nonlocal callback_func
            callback_func = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_START_DRYING(mock_gcmd)
        
        # Simulate failed response
        callback_func({"code": 1, "msg": "Hardware error"})
        
        # Should log failure message
        calls = [str(call) for call in mock_gcmd.respond_info.call_args_list]
        assert any("failed" in str(call) for call in calls)

    def test_start_drying_sets_instance_state(self, mock_gcmd, setup_mocks):
        """Test START_DRYING sets dryer state on instance."""
        mock_gcmd.get_int = Mock(side_effect=[0, 55, 180])
        
        ace.commands.cmd_ACE_START_DRYING(mock_gcmd)
        
        # Check state was set
        assert ACE_INSTANCES[0]._dryer_active is True
        assert ACE_INSTANCES[0]._dryer_temperature == 55
        assert ACE_INSTANCES[0]._dryer_duration == 180

    def test_start_drying_all_instances_temp_validation(self, mock_gcmd, setup_mocks):
        """Test START_DRYING for all instances skips invalid temperatures."""
        mock_gcmd.get_int = Mock(side_effect=[None, 100, 240])  # temp=100 exceeds max
        
        ace.commands.cmd_ACE_START_DRYING(mock_gcmd)
        
        # Should report skipping due to out of range
        calls = [str(call) for call in mock_gcmd.respond_info.call_args_list]
        assert any("out of range" in str(call) for call in calls)

    def test_start_drying_dryer_start_logged_flag(self, mock_gcmd, setup_mocks):
        """Test START_DRYING only logs once using _dryer_start_logged flag."""
        mock_gcmd.get_int = Mock(side_effect=[0, 50, 240])
        
        # First call - should log
        callback_func = None
        def capture_callback(request, callback):
            nonlocal callback_func
            callback_func = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ACE_INSTANCES[0]._dryer_start_logged = False
        
        ace.commands.cmd_ACE_START_DRYING(mock_gcmd)
        callback_func({"code": 0, "result": {}})
        
        assert ACE_INSTANCES[0]._dryer_start_logged is True
        first_call_count = mock_gcmd.respond_info.call_count
        
        # Second callback - should not log again
        callback_func({"code": 0, "result": {}})
        assert mock_gcmd.respond_info.call_count == first_call_count  # No new calls

    def test_stop_drying_callback_success(self, mock_gcmd, setup_mocks):
        """Test STOP_DRYING callback handles successful response."""
        mock_gcmd.get_int = Mock(return_value=0)
        
        callback_func = None
        def capture_callback(request, callback):
            nonlocal callback_func
            callback_func = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_STOP_DRYING(mock_gcmd)
        
        # Simulate successful response
        callback_func({"code": 0, "result": {}})
        
        # Should log success message
        calls = [str(call) for call in mock_gcmd.respond_info.call_args_list]
        assert any("Dryer stopped" in str(call) for call in calls)

    def test_stop_drying_callback_failure(self, mock_gcmd, setup_mocks):
        """Test STOP_DRYING callback handles failed response."""
        mock_gcmd.get_int = Mock(return_value=0)
        
        callback_func = None
        def capture_callback(request, callback):
            nonlocal callback_func
            callback_func = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_STOP_DRYING(mock_gcmd)
        
        # Simulate failed response
        callback_func({"code": 1, "msg": "Communication error"})
        
        # Should log failure message
        calls = [str(call) for call in mock_gcmd.respond_info.call_args_list]
        assert any("failed" in str(call) for call in calls)

    def test_stop_drying_clears_instance_state(self, mock_gcmd, setup_mocks):
        """Test STOP_DRYING clears dryer state on instance."""
        mock_gcmd.get_int = Mock(return_value=0)
        
        # Set initial state
        ACE_INSTANCES[0]._dryer_active = True
        ACE_INSTANCES[0]._dryer_temperature = 50
        ACE_INSTANCES[0]._dryer_duration = 240
        
        ace.commands.cmd_ACE_STOP_DRYING(mock_gcmd)
        
        # Check state was cleared
        assert ACE_INSTANCES[0]._dryer_active is False
        assert ACE_INSTANCES[0]._dryer_temperature == 0
        assert ACE_INSTANCES[0]._dryer_duration == 0

    def test_stop_drying_resets_logged_flag(self, mock_gcmd, setup_mocks):
        """Test STOP_DRYING resets _dryer_start_logged flag."""
        mock_gcmd.get_int = Mock(return_value=0)
        
        ACE_INSTANCES[0]._dryer_start_logged = True
        
        callback_func = None
        def capture_callback(request, callback):
            nonlocal callback_func
            callback_func = callback
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_callback)
        ace.commands.cmd_ACE_STOP_DRYING(mock_gcmd)
        
        # Simulate successful response
        callback_func({"code": 0, "result": {}})
        
        # Flag should be reset
        assert ACE_INSTANCES[0]._dryer_start_logged is False

    def test_start_drying_sends_correct_request(self, mock_gcmd, setup_mocks):
        """Test START_DRYING sends properly formatted request."""
        mock_gcmd.get_int = Mock(side_effect=[0, 60, 300])
        
        captured_request = None
        def capture_request(request, callback):
            nonlocal captured_request
            captured_request = request
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_request)
        ace.commands.cmd_ACE_START_DRYING(mock_gcmd)
        
        assert captured_request is not None
        assert captured_request["method"] == "drying"
        assert captured_request["params"]["temp"] == 60
        assert captured_request["params"]["duration"] == 300

    def test_stop_drying_sends_correct_request(self, mock_gcmd, setup_mocks):
        """Test STOP_DRYING sends properly formatted request."""
        mock_gcmd.get_int = Mock(return_value=0)
        
        captured_request = None
        def capture_request(request, callback):
            nonlocal captured_request
            captured_request = request
        
        ACE_INSTANCES[0].send_request = Mock(side_effect=capture_request)
        ace.commands.cmd_ACE_STOP_DRYING(mock_gcmd)
        
        assert captured_request is not None
        assert captured_request["method"] == "drying_stop"


    def test_cmd_ACE_ENABLE_FEED_ASSIST(self, mock_gcmd, setup_mocks):
        """Test ACE_ENABLE_FEED_ASSIST."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0])
        
        ace.commands.cmd_ACE_ENABLE_FEED_ASSIST(mock_gcmd)
        assert ACE_INSTANCES[0]._enable_feed_assist.called

    def test_cmd_ACE_DISABLE_FEED_ASSIST(self, mock_gcmd, setup_mocks):
        """Test ACE_DISABLE_FEED_ASSIST."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0])
        
        ace.commands.cmd_ACE_DISABLE_FEED_ASSIST(mock_gcmd)
        assert ACE_INSTANCES[0]._disable_feed_assist.called

    def test_cmd_ACE_SET_PURGE_AMOUNT(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_PURGE_AMOUNT."""
        mock_gcmd.get_float = Mock(side_effect=[50.0, 400.0])
        
        ace.commands.cmd_ACE_SET_PURGE_AMOUNT(mock_gcmd)
        assert INSTANCE_MANAGERS[0].toolchange_purge_length == 50.0
        assert INSTANCE_MANAGERS[0].toolchange_purge_speed == 400.0

    def test_cmd_ACE_QUERY_SLOTS_all(self, mock_gcmd, setup_mocks):
        """Test ACE_QUERY_SLOTS for all instances."""
        mock_gcmd.get_command_parameters = Mock(return_value={})
        ace.commands.cmd_ACE_QUERY_SLOTS(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_QUERY_SLOTS_single(self, mock_gcmd, setup_mocks):
        """Test ACE_QUERY_SLOTS for single instance."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0})
        ace.commands.cmd_ACE_QUERY_SLOTS(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_ENABLE_ENDLESS_SPOOL(self, mock_gcmd, setup_mocks):
        """Test ACE_ENABLE_ENDLESS_SPOOL."""
        with patch('ace.commands.get_printer') as mock_get_printer:
            mock_printer = Mock()
            mock_save_vars = Mock()
            mock_save_vars.allVariables = {}
            mock_printer.lookup_object = Mock(return_value=mock_save_vars)
            mock_get_printer.return_value = mock_printer
            
            ace.commands.cmd_ACE_ENABLE_ENDLESS_SPOOL(mock_gcmd)
            assert mock_save_vars.allVariables.get("ace_endless_spool_enabled") is True

    def test_cmd_ACE_DISABLE_ENDLESS_SPOOL(self, mock_gcmd, setup_mocks):
        """Test ACE_DISABLE_ENDLESS_SPOOL."""
        with patch('ace.commands.get_printer') as mock_get_printer:
            mock_printer = Mock()
            mock_save_vars = Mock()
            mock_save_vars.allVariables = {}
            mock_printer.lookup_object = Mock(return_value=mock_save_vars)
            mock_get_printer.return_value = mock_printer
            
            ace.commands.cmd_ACE_DISABLE_ENDLESS_SPOOL(mock_gcmd)
            assert mock_save_vars.allVariables.get("ace_endless_spool_enabled") is False

    def test_cmd_ACE_ENDLESS_SPOOL_STATUS(self, mock_gcmd, setup_mocks):
        """Test ACE_ENDLESS_SPOOL_STATUS."""
        with patch('ace.commands.get_printer') as mock_get_printer:
            mock_printer = Mock()
            mock_save_vars = Mock()
            mock_save_vars.allVariables = {"ace_endless_spool_enabled": True}
            mock_printer.lookup_object = Mock(return_value=mock_save_vars)
            mock_get_printer.return_value = mock_printer
            
            ace.commands.cmd_ACE_ENDLESS_SPOOL_STATUS(mock_gcmd)
            assert mock_gcmd.respond_info.called

    def test_cmd_ACE_DEBUG(self, mock_gcmd, setup_mocks):
        """Test ACE_DEBUG command."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0})
        mock_gcmd.get = Mock(side_effect=["get_status", "{}"])
        
        ace.commands.cmd_ACE_DEBUG(mock_gcmd)
        assert ACE_INSTANCES[0].send_request.called

    def test_cmd_ACE_SMART_UNLOAD(self, mock_gcmd, setup_mocks):
        """Test ACE_SMART_UNLOAD."""
        mock_gcmd.get_int = Mock(return_value=0)
        
        with patch('ace.commands.get_variable', return_value=0):
            ace.commands.cmd_ACE_SMART_UNLOAD(mock_gcmd)
            assert INSTANCE_MANAGERS[0].smart_unload.called

    def test_cmd_ACE_HANDLE_PRINT_END(self, mock_gcmd, setup_mocks):
        """Test ACE_HANDLE_PRINT_END with CUT_TIP=1 (default)."""
        mock_gcmd.get_int = Mock(return_value=1)  # CUT_TIP=1
        with patch('ace.commands.get_variable', return_value=0):
            ace.commands.cmd_ACE_HANDLE_PRINT_END(mock_gcmd)
            assert INSTANCE_MANAGERS[0].smart_unload.called

    def test_cmd_ACE_HANDLE_PRINT_END_skip_cut(self, mock_gcmd, setup_mocks):
        """Test ACE_HANDLE_PRINT_END with CUT_TIP=0 skips unload."""
        mock_gcmd.get_int = Mock(return_value=0)  # CUT_TIP=0
        with patch('ace.commands.get_variable', return_value=0):
            ace.commands.cmd_ACE_HANDLE_PRINT_END(mock_gcmd)
            assert not INSTANCE_MANAGERS[0].smart_unload.called

    def test_cmd_ACE_SMART_LOAD(self, mock_gcmd, setup_mocks):
        """Test ACE_SMART_LOAD."""
        ace.commands.cmd_ACE_SMART_LOAD(mock_gcmd)
        assert INSTANCE_MANAGERS[0].smart_load.called

    def test_cmd_ACE_RESET_PERSISTENT_INVENTORY(self, mock_gcmd, setup_mocks):
        """Test ACE_RESET_PERSISTENT_INVENTORY."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0})
        ace.commands.cmd_ACE_RESET_PERSISTENT_INVENTORY(mock_gcmd)
        assert ACE_INSTANCES[0].reset_persistent_inventory.called

    def test_cmd_ACE_RESET_ACTIVE_TOOLHEAD(self, mock_gcmd, setup_mocks):
        """Test ACE_RESET_ACTIVE_TOOLHEAD."""
        ace.commands.cmd_ACE_RESET_ACTIVE_TOOLHEAD(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_DEBUG_SENSORS(self, mock_gcmd, setup_mocks):
        """Test ACE_DEBUG_SENSORS basic functionality."""
        ace.commands.cmd_ACE_DEBUG_SENSORS(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_DEBUG_SENSORS_with_rdm(self, mock_gcmd, setup_mocks):
        """Test ACE_DEBUG_SENSORS with RDM sensor present."""
        from ace.config import SENSOR_TOOLHEAD, SENSOR_RDM
        
        # Mock manager with RDM sensor
        manager = INSTANCE_MANAGERS[0]
        manager.has_rdm_sensor = Mock(return_value=True)
        manager.get_switch_state = Mock(side_effect=lambda sensor: {
            SENSOR_TOOLHEAD: True,
            SENSOR_RDM: False
        }.get(sensor, False))
        manager.is_filament_path_free = Mock(return_value=True)
        
        ace.commands.cmd_ACE_DEBUG_SENSORS(mock_gcmd)
        
        # Verify all sensors were queried
        assert manager.get_switch_state.call_count == 2  # Toolhead + RDM
        manager.get_switch_state.assert_any_call(SENSOR_TOOLHEAD)
        manager.get_switch_state.assert_any_call(SENSOR_RDM)
        assert manager.is_filament_path_free.called
        
        # Check output includes RDM
        call_args = [call[0][0] for call in mock_gcmd.respond_info.call_args_list]
        output = "\n".join(call_args)
        assert "Toolhead Sensor:" in output
        assert "RDM Sensor:" in output
        assert "Path Free:" in output

    def test_cmd_ACE_DEBUG_SENSORS_without_rdm(self, mock_gcmd, setup_mocks):
        """Test ACE_DEBUG_SENSORS without RDM sensor (should skip RDM output)."""
        from ace.config import SENSOR_TOOLHEAD, SENSOR_RDM
        
        # Mock manager without RDM sensor
        manager = INSTANCE_MANAGERS[0]
        manager.has_rdm_sensor = Mock(return_value=False)
        manager.get_switch_state = Mock(side_effect=lambda sensor: {
            SENSOR_TOOLHEAD: True
        }.get(sensor, False))
        manager.is_filament_path_free = Mock(return_value=False)
        
        ace.commands.cmd_ACE_DEBUG_SENSORS(mock_gcmd)
        
        # Verify only toolhead sensor was queried, not RDM
        assert manager.get_switch_state.call_count == 1  # Only Toolhead
        manager.get_switch_state.assert_called_with(SENSOR_TOOLHEAD)
        assert manager.is_filament_path_free.called
        
        # Check output does NOT include RDM
        call_args = [call[0][0] for call in mock_gcmd.respond_info.call_args_list]
        output = "\n".join(call_args)
        assert "Toolhead Sensor:" in output
        assert "RDM Sensor:" not in output  # Should not be present
        assert "Path Free:" in output

    def test_cmd_ACE_DEBUG_SENSORS_sensor_states(self, mock_gcmd, setup_mocks):
        """Test ACE_DEBUG_SENSORS shows correct sensor states."""
        from ace.config import SENSOR_TOOLHEAD, SENSOR_RDM
        
        # Mock specific sensor states
        manager = INSTANCE_MANAGERS[0]
        manager.has_rdm_sensor = Mock(return_value=True)
        manager.get_switch_state = Mock(side_effect=lambda sensor: {
            SENSOR_TOOLHEAD: False,  # CLEAR
            SENSOR_RDM: True  # TRIGGERED
        }.get(sensor, False))
        manager.is_filament_path_free = Mock(return_value=True)  # YES
        
        ace.commands.cmd_ACE_DEBUG_SENSORS(mock_gcmd)
        
        call_args = [call[0][0] for call in mock_gcmd.respond_info.call_args_list]
        output = "\n".join(call_args)
        
        # Verify correct state labels
        assert "Toolhead Sensor: CLEAR" in output
        assert "RDM Sensor: TRIGGERED" in output
        assert "Path Free: YES" in output

    def test_cmd_ACE_DEBUG_STATE(self, mock_gcmd, setup_mocks):
        """Test ACE_DEBUG_STATE."""
        ace.commands.cmd_ACE_DEBUG_STATE(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_DEBUG_CHECK_SPOOL_READY(self, mock_gcmd, setup_mocks):
        """Test ACE_DEBUG_CHECK_SPOOL_READY."""
        mock_gcmd.get_int = Mock(return_value=0)
        ace.commands.cmd_ACE_DEBUG_CHECK_SPOOL_READY(mock_gcmd)
        assert INSTANCE_MANAGERS[0].check_and_wait_for_spool_ready.called

    def test_cmd_ACE_ENABLE_RFID_SYNC_single(self, mock_gcmd, setup_mocks):
        """Test ACE_ENABLE_RFID_SYNC for single instance."""
        mock_gcmd.get_int = Mock(return_value=0)
        ace.commands.cmd_ACE_ENABLE_RFID_SYNC(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_ENABLE_RFID_SYNC_all(self, mock_gcmd, setup_mocks):
        """Test ACE_ENABLE_RFID_SYNC for all instances."""
        mock_gcmd.get_int = Mock(return_value=None)
        ace.commands.cmd_ACE_ENABLE_RFID_SYNC(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_DISABLE_RFID_SYNC_single(self, mock_gcmd, setup_mocks):
        """Test ACE_DISABLE_RFID_SYNC for single instance."""
        mock_gcmd.get_int = Mock(return_value=0)
        ace.commands.cmd_ACE_DISABLE_RFID_SYNC(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_DISABLE_RFID_SYNC_all(self, mock_gcmd, setup_mocks):
        """Test ACE_DISABLE_RFID_SYNC for all instances."""
        mock_gcmd.get_int = Mock(return_value=None)
        ace.commands.cmd_ACE_DISABLE_RFID_SYNC(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_RFID_SYNC_STATUS_single(self, mock_gcmd, setup_mocks):
        """Test ACE_RFID_SYNC_STATUS for single instance."""
        mock_gcmd.get_int = Mock(return_value=0)
        ace.commands.cmd_ACE_RFID_SYNC_STATUS(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_RFID_SYNC_STATUS_all(self, mock_gcmd, setup_mocks):
        """Test ACE_RFID_SYNC_STATUS for all instances."""
        mock_gcmd.get_int = Mock(return_value=None)
        ace.commands.cmd_ACE_RFID_SYNC_STATUS(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_DEBUG_INJECT_SENSOR_STATE_reset(self, mock_gcmd, setup_mocks):
        """Test ACE_DEBUG_INJECT_SENSOR_STATE with RESET."""
        mock_gcmd.get_int = Mock(side_effect=[1, None, None])
        ace.commands.cmd_ACE_DEBUG_INJECT_SENSOR_STATE(mock_gcmd)
        assert INSTANCE_MANAGERS[0]._sensor_override is None

    def test_cmd_ACE_DEBUG_INJECT_SENSOR_STATE_set(self, mock_gcmd, setup_mocks):
        """Test ACE_DEBUG_INJECT_SENSOR_STATE setting sensor states."""
        mock_gcmd.get_int = Mock(side_effect=[0, 1, 0])
        ace.commands.cmd_ACE_DEBUG_INJECT_SENSOR_STATE(mock_gcmd)
        assert INSTANCE_MANAGERS[0]._sensor_override is not None

    def test_cmd_ACE_SET_ENDLESS_SPOOL_MODE_exact(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_ENDLESS_SPOOL_MODE with exact mode."""
        mock_gcmd.get = Mock(return_value="exact")
        ace.commands.cmd_ACE_SET_ENDLESS_SPOOL_MODE(mock_gcmd)
        assert INSTANCE_MANAGERS[0].set_and_save_variable.called

    def test_cmd_ACE_SET_ENDLESS_SPOOL_MODE_material(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_ENDLESS_SPOOL_MODE with material mode."""
        mock_gcmd.get = Mock(return_value="material")
        ace.commands.cmd_ACE_SET_ENDLESS_SPOOL_MODE(mock_gcmd)
        assert INSTANCE_MANAGERS[0].set_and_save_variable.called

    def test_cmd_ACE_SET_ENDLESS_SPOOL_MODE_next(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_ENDLESS_SPOOL_MODE with next-ready mode."""
        mock_gcmd.get = Mock(return_value="next")
        ace.commands.cmd_ACE_SET_ENDLESS_SPOOL_MODE(mock_gcmd)
        assert INSTANCE_MANAGERS[0].set_and_save_variable.called

    def test_cmd_ACE_SET_ENDLESS_SPOOL_MODE_invalid(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_ENDLESS_SPOOL_MODE with invalid mode."""
        mock_gcmd.get = Mock(return_value="invalid")
        ace.commands.cmd_ACE_SET_ENDLESS_SPOOL_MODE(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_GET_ENDLESS_SPOOL_MODE(self, mock_gcmd, setup_mocks):
        """Test ACE_GET_ENDLESS_SPOOL_MODE."""
        with patch('ace.commands.get_printer') as mock_get_printer:
            mock_printer = Mock()
            mock_save_vars = Mock()
            mock_save_vars.allVariables = {"ace_endless_spool_match_mode": "exact"}
            mock_printer.lookup_object = Mock(return_value=mock_save_vars)
            mock_get_printer.return_value = mock_printer
            
            ace.commands.cmd_ACE_GET_ENDLESS_SPOOL_MODE(mock_gcmd)
            assert mock_gcmd.respond_info.called

    def test_cmd_ACE_GET_ENDLESS_SPOOL_MODE_next(self, mock_gcmd, setup_mocks):
        """Test ACE_GET_ENDLESS_SPOOL_MODE for next-ready mode."""
        with patch('ace.commands.get_printer') as mock_get_printer:
            mock_printer = Mock()
            mock_save_vars = Mock()
            mock_save_vars.allVariables = {"ace_endless_spool_match_mode": "next"}
            mock_printer.lookup_object = Mock(return_value=mock_save_vars)
            mock_get_printer.return_value = mock_printer

            ace.commands.cmd_ACE_GET_ENDLESS_SPOOL_MODE(mock_gcmd)
            assert mock_gcmd.respond_info.called

    def test_cmd_ACE_CHANGE_TOOL_WRAPPER(self, mock_gcmd, setup_mocks):
        """Test ACE_CHANGE_TOOL_WRAPPER."""
        mock_gcmd.get_int = Mock(return_value=0)
        
        with patch('ace.commands.get_printer') as mock_get_printer:
            mock_printer = Mock()
            mock_printer.lookup_object = Mock(return_value=Mock())
            mock_printer.get_reactor = Mock(return_value=Mock())
            mock_get_printer.return_value = mock_printer
            
            with patch('ace.commands.get_variable', return_value=-1):
                ace.commands.cmd_ACE_CHANGE_TOOL_WRAPPER(mock_gcmd)
                assert INSTANCE_MANAGERS[0].perform_tool_change.called or mock_gcmd.respond_info.called

    def test_cmd_ACE_FULL_UNLOAD_single(self, mock_gcmd, setup_mocks):
        """Test ACE_FULL_UNLOAD for single tool."""
        mock_gcmd.get = Mock(return_value="0")
        mock_gcmd.get_int = Mock(return_value=0)
        
        ace.commands.cmd_ACE_FULL_UNLOAD(mock_gcmd)
        assert INSTANCE_MANAGERS[0].full_unload_slot.called

    def test_cmd_ACE_FULL_UNLOAD_all(self, mock_gcmd, setup_mocks):
        """Test ACE_FULL_UNLOAD for all tools."""
        mock_gcmd.get = Mock(return_value="ALL")
        
        # Setup some non-empty slots
        ACE_INSTANCES[0].inventory[0] = {"status": "ready", "color": [255,0,0], "material": "PLA", "temp": 210}
        
        ace.commands.cmd_ACE_FULL_UNLOAD(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_SHOW_INSTANCE_CONFIG_single(self, mock_gcmd, setup_mocks):
        """Test ACE_SHOW_INSTANCE_CONFIG for single instance."""
        mock_gcmd.get_int = Mock(return_value=0)
        
        # Add required attributes to mock instance
        ACE_INSTANCES[0].baud = 115200
        ACE_INSTANCES[0].filament_runout_sensor_name_rdm = "sensor_rdm"
        ACE_INSTANCES[0].filament_runout_sensor_name_nozzle = "sensor_nozzle"
        ACE_INSTANCES[0].feed_assist_active_after_ace_connect = False
        ACE_INSTANCES[0].rfid_inventory_sync_enabled = False
        
        ace.commands.cmd_ACE_SHOW_INSTANCE_CONFIG(mock_gcmd)
        assert mock_gcmd.respond_info.called

    def test_cmd_ACE_SHOW_INSTANCE_CONFIG_all(self, mock_gcmd, setup_mocks):
        """Test ACE_SHOW_INSTANCE_CONFIG for all instances."""
        mock_gcmd.get_int = Mock(return_value=None)
        
        # Add required attributes to mock instance
        ACE_INSTANCES[0].baud = 115200
        ACE_INSTANCES[0].filament_runout_sensor_name_rdm = "sensor_rdm"
        ACE_INSTANCES[0].filament_runout_sensor_name_nozzle = "sensor_nozzle"
        ACE_INSTANCES[0].feed_assist_active_after_ace_connect = False
        ACE_INSTANCES[0].rfid_inventory_sync_enabled = False
        
        ace.commands.cmd_ACE_SHOW_INSTANCE_CONFIG(mock_gcmd)
        assert mock_gcmd.respond_info.called


class TestHelperFunctions:
    """Test helper functions in commands module."""

    def test_validate_feed_and_retract_arguments_valid(self, mock_gcmd, setup_mocks):
        """Test validate_feed_and_retract_arguments with valid inputs."""
        instance = ACE_INSTANCES[0]
        # Should not raise any exception
        ace.commands.validate_feed_and_retract_arguments(mock_gcmd, instance, 0, 100, 60)

    def test_validate_feed_and_retract_arguments_invalid_slot(self, mock_gcmd, setup_mocks):
        """Test validate_feed_and_retract_arguments with invalid slot."""
        instance = ACE_INSTANCES[0]
        # gcmd.error() creates an exception that gets raised
        mock_gcmd.error = Mock(side_effect=lambda msg: Exception(msg))
        
        with pytest.raises(Exception, match="Invalid slot"):
            ace.commands.validate_feed_and_retract_arguments(mock_gcmd, instance, 10, 100, 60)

    def test_validate_feed_and_retract_arguments_negative_length(self, mock_gcmd, setup_mocks):
        """Test validate_feed_and_retract_arguments with negative length."""
        instance = ACE_INSTANCES[0]
        mock_gcmd.error = Mock(side_effect=lambda msg: Exception(msg))
        
        with pytest.raises(Exception, match="LENGTH must be positive"):
            ace.commands.validate_feed_and_retract_arguments(mock_gcmd, instance, 0, -10, 60)

    def test_for_each_instance_callback(self, setup_mocks):
        """Test for_each_instance executes callback for all instances."""
        call_count = 0
        
        def callback(inst_num, manager, instance):
            nonlocal call_count
            call_count += 1
            assert inst_num == 0
            assert manager == INSTANCE_MANAGERS[0]
            assert instance == ACE_INSTANCES[0]
        
        ace.commands.for_each_instance(callback)
        assert call_count == 1

    def test_validate_feed_and_retract_arguments_zero_speed(self, mock_gcmd, setup_mocks):
        """Test validate_feed_and_retract_arguments rejects zero speed."""
        instance = ACE_INSTANCES[0]
        mock_gcmd.error = Mock(side_effect=lambda msg: Exception(msg))
        
        with pytest.raises(Exception, match="SPEED must be positive"):
            ace.commands.validate_feed_and_retract_arguments(mock_gcmd, instance, 0, 100, 0)

    def test_validate_feed_and_retract_arguments_negative_speed(self, mock_gcmd, setup_mocks):
        """Test validate_feed_and_retract_arguments rejects negative speed."""
        instance = ACE_INSTANCES[0]
        mock_gcmd.error = Mock(side_effect=lambda msg: Exception(msg))
        
        with pytest.raises(Exception, match="SPEED must be positive"):
            ace.commands.validate_feed_and_retract_arguments(mock_gcmd, instance, 0, 100, -50)


class TestGetPrinter:
    """Test get_printer helper function."""

    def test_get_printer_no_instances(self):
        """Test get_printer raises error when no instances available."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        with pytest.raises(RuntimeError, match="No ACE instances available"):
            ace.commands.get_printer()

    def test_get_printer_returns_printer(self, setup_mocks):
        """Test get_printer returns printer object from manager."""
        mock_printer = Mock()
        INSTANCE_MANAGERS[0].get_printer = Mock(return_value=mock_printer)
        
        result = ace.commands.get_printer()
        
        assert result == mock_printer
        INSTANCE_MANAGERS[0].get_printer.assert_called_once()


class TestAceGetInstance:
    """Test ace_get_instance parameter resolution."""

    def test_ace_get_instance_with_instance_param(self, mock_gcmd, setup_mocks):
        """Test ace_get_instance uses INSTANCE parameter."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": "0"})
        mock_gcmd.get_int = Mock(return_value=0)
        
        result = ace.commands.ace_get_instance(mock_gcmd)
        
        assert result == ACE_INSTANCES[0]

    def test_ace_get_instance_invalid_instance(self, mock_gcmd, setup_mocks):
        """Test ace_get_instance raises error for invalid instance."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": "99"})
        mock_gcmd.get_int = Mock(return_value=99)
        mock_gcmd.error = Mock(side_effect=lambda msg: Exception(msg))
        
        with pytest.raises(Exception, match="No ACE instance 99"):
            ace.commands.ace_get_instance(mock_gcmd)

    def test_ace_get_instance_with_tool_param(self, mock_gcmd, setup_mocks):
        """Test ace_get_instance uses TOOL parameter to map to instance."""
        mock_gcmd.get_command_parameters = Mock(return_value={"TOOL": "0"})
        mock_gcmd.get_int = Mock(return_value=0)
        
        result = ace.commands.ace_get_instance(mock_gcmd)
        
        assert result == ACE_INSTANCES[0]

    def test_ace_get_instance_tool_not_managed(self, mock_gcmd, setup_mocks):
        """Test ace_get_instance raises error for unmanaged tool."""
        mock_gcmd.get_command_parameters = Mock(return_value={"TOOL": "999"})
        mock_gcmd.get_int = Mock(return_value=999)
        mock_gcmd.error = Mock(side_effect=lambda msg: Exception(msg))
        
        with pytest.raises(Exception, match="No ACE instance manages tool"):
            ace.commands.ace_get_instance(mock_gcmd)

    def test_ace_get_instance_defaults_to_zero(self, mock_gcmd, setup_mocks):
        """Test ace_get_instance defaults to instance 0."""
        mock_gcmd.get_command_parameters = Mock(return_value={})
        
        result = ace.commands.ace_get_instance(mock_gcmd)
        
        assert result == ACE_INSTANCES[0]

    def test_ace_get_instance_no_instances_configured(self, mock_gcmd):
        """Test ace_get_instance raises error when no instances configured."""
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        mock_gcmd.get_command_parameters = Mock(return_value={})
        mock_gcmd.error = Mock(side_effect=lambda msg: Exception(msg))
        
        with pytest.raises(Exception, match="No ACE instances configured"):
            ace.commands.ace_get_instance(mock_gcmd)


class TestAceGetInstanceAndSlot:
    """Test ace_get_instance_and_slot parameter resolution."""

    def test_with_t_param_returns_instance_and_slot(self, mock_gcmd, setup_mocks):
        """Test T= parameter correctly maps to instance and local slot."""
        mock_gcmd.get_command_parameters = Mock(return_value={"T": "2"})
        mock_gcmd.get_int = Mock(return_value=2)
        
        ace_instance, slot = ace.commands.ace_get_instance_and_slot(mock_gcmd)
        
        assert ace_instance == ACE_INSTANCES[0]
        assert slot == 2  # T2 on instance 0 is local slot 2

    def test_with_t_param_tool_0(self, mock_gcmd, setup_mocks):
        """Test T=0 returns instance 0 and slot 0."""
        mock_gcmd.get_command_parameters = Mock(return_value={"T": "0"})
        mock_gcmd.get_int = Mock(return_value=0)
        
        ace_instance, slot = ace.commands.ace_get_instance_and_slot(mock_gcmd)
        
        assert ace_instance == ACE_INSTANCES[0]
        assert slot == 0

    def test_with_t_param_tool_3(self, mock_gcmd, setup_mocks):
        """Test T=3 returns instance 0 and slot 3 (last slot on first instance)."""
        mock_gcmd.get_command_parameters = Mock(return_value={"T": "3"})
        mock_gcmd.get_int = Mock(return_value=3)
        
        ace_instance, slot = ace.commands.ace_get_instance_and_slot(mock_gcmd)
        
        assert ace_instance == ACE_INSTANCES[0]
        assert slot == 3

    def test_with_instance_and_index_params(self, mock_gcmd, setup_mocks):
        """Test INSTANCE= + INDEX= parameters work correctly."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": "0", "INDEX": "2"})
        mock_gcmd.get_int = Mock(side_effect=[0, 2])
        
        ace_instance, slot = ace.commands.ace_get_instance_and_slot(mock_gcmd)
        
        assert ace_instance == ACE_INSTANCES[0]
        assert slot == 2

    def test_raises_error_when_neither_provided(self, mock_gcmd, setup_mocks):
        """Test error when neither T= nor INSTANCE=+INDEX= provided."""
        mock_gcmd.get_command_parameters = Mock(return_value={})
        mock_gcmd.error = Mock(side_effect=lambda msg: Exception(msg))
        
        with pytest.raises(Exception, match="Must specify either T="):
            ace.commands.ace_get_instance_and_slot(mock_gcmd)

    def test_raises_error_for_instance_only(self, mock_gcmd, setup_mocks):
        """Test error when only INSTANCE= provided without INDEX=."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": "0"})
        mock_gcmd.error = Mock(side_effect=lambda msg: Exception(msg))
        
        with pytest.raises(Exception, match="Must specify either T="):
            ace.commands.ace_get_instance_and_slot(mock_gcmd)

    def test_raises_error_for_index_only(self, mock_gcmd, setup_mocks):
        """Test error when only INDEX= provided without INSTANCE=."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INDEX": "2"})
        mock_gcmd.error = Mock(side_effect=lambda msg: Exception(msg))
        
        with pytest.raises(Exception, match="Must specify either T="):
            ace.commands.ace_get_instance_and_slot(mock_gcmd)

    def test_t_param_unmanaged_tool_raises_error(self, mock_gcmd, setup_mocks):
        """Test T= parameter with unmanaged tool number raises error."""
        mock_gcmd.get_command_parameters = Mock(return_value={"T": "99"})
        mock_gcmd.get_int = Mock(return_value=99)
        mock_gcmd.error = Mock(side_effect=lambda msg: Exception(msg))
        
        with pytest.raises(Exception, match="No ACE instance manages tool"):
            ace.commands.ace_get_instance_and_slot(mock_gcmd)

    def test_t_param_takes_priority_over_instance_index(self, mock_gcmd, setup_mocks):
        """Test T= parameter is checked before INSTANCE=+INDEX=."""
        # If T is in params, INSTANCE+INDEX should be ignored
        mock_gcmd.get_command_parameters = Mock(return_value={"T": "1", "INSTANCE": "0", "INDEX": "3"})
        mock_gcmd.get_int = Mock(return_value=1)
        
        ace_instance, slot = ace.commands.ace_get_instance_and_slot(mock_gcmd)
        
        # Should use T=1 -> slot 1, not INDEX=3
        assert slot == 1


class TestSafeGcodeCommand:
    """Coverage for safe_gcode_command decorator error handling."""

    def test_wrapper_shows_prompt_on_exception(self, capsys):
        gcmd = Mock()
        gcmd.respond_info = Mock()
        printer = Mock()
        gcode = Mock()
        printer.lookup_object.return_value = gcode

        with patch('ace.commands.get_printer', return_value=printer):
            @ace.commands.safe_gcode_command
            def boom(_g):
                raise ValueError("bad")

            boom(gcmd)

        gcmd.respond_info.assert_called()
        assert gcode.run_script_from_command.call_count >= 3

    def test_wrapper_handles_dialog_error(self, capsys):
        gcmd = Mock()
        gcmd.respond_info = Mock()

        with patch('ace.commands.get_printer', side_effect=RuntimeError("no printer")):
            @ace.commands.safe_gcode_command
            def boom(_g):
                raise RuntimeError("failure")

            boom(gcmd)

        gcmd.respond_info.assert_called()
        out = capsys.readouterr().out
        assert "Failed to show error dialog" in out


class TestAceChangeTool:
    def _make_printer(self, homed_axes="xyz", printing=False):
        printer = Mock()
        gcode = Mock()
        printer.lookup_object = Mock(return_value=gcode)
        toolhead = Mock()
        kin = Mock()
        kin.get_status = Mock(return_value={"homed_axes": homed_axes})
        toolhead.get_kinematics = Mock(return_value=kin)
        printer.lookup_object.side_effect = lambda name, default=None: {"toolhead": toolhead, "gcode": gcode, "print_stats": Mock(get_status=Mock(return_value={"state": "printing" if printing else "idle"}))}.get(name, gcode)
        reactor = Mock()
        reactor.monotonic = Mock(return_value=0.0)
        printer.get_reactor = Mock(return_value=reactor)
        return printer, gcode

    def test_change_tool_disabled_global(self, mock_gcmd, setup_mocks):
        manager = INSTANCE_MANAGERS[0]
        manager.get_ace_global_enabled.return_value = False

        ace.commands.cmd_ACE_CHANGE_TOOL(manager, mock_gcmd, tool_index=1)

        manager.perform_tool_change.assert_not_called()
        mock_gcmd.respond_info.assert_any_call("ACE: Global ACE Pro support disabled - tool change ignored")

    def test_change_tool_unload_path_success(self, mock_gcmd, setup_mocks):
        manager = INSTANCE_MANAGERS[0]
        manager.get_ace_global_enabled.return_value = True
        manager.smart_unload.return_value = True
        manager.set_and_save_variable = Mock()
        with patch('ace.commands.get_variable', return_value=3):
            ace.commands.cmd_ACE_CHANGE_TOOL(manager, mock_gcmd, tool_index=-1)
        manager.smart_unload.assert_called_once_with(3)
        manager.set_and_save_variable.assert_called_with("ace_current_index", -1)

    def test_change_tool_perform_success_with_homing(self, mock_gcmd, setup_mocks):
        manager = INSTANCE_MANAGERS[0]
        manager.get_ace_global_enabled.return_value = True
        manager.perform_tool_change = Mock(return_value="OK")
        manager.set_and_save_variable = Mock()
        printer, gcode = self._make_printer(homed_axes="xy")  # missing z -> triggers homing

        with patch('ace.commands.get_variable', return_value=0), \
             patch('ace.commands.get_printer', return_value=printer):
            ace.commands.cmd_ACE_CHANGE_TOOL(manager, mock_gcmd, tool_index=2)

        manager.perform_tool_change.assert_called_once_with(0, 2)
        # Homing warning should have triggered a G28
        gcode.run_script_from_command.assert_any_call("G28")


class TestAceSetRetractSpeed:
    def test_set_retract_speed_success(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_RETRACT_SPEED with valid parameters."""
        from ace.config import ACE_INSTANCES
        
        # Setup mock to return success
        ACE_INSTANCES[0]._change_retract_speed = Mock(return_value=True)
        
        # Configure gcmd for INSTANCE=0, INDEX=0, SPEED=25.0
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0])  # INSTANCE, INDEX
        mock_gcmd.get_float = Mock(return_value=25.0)  # SPEED
        
        ace.commands.cmd_ACE_SET_RETRACT_SPEED(mock_gcmd)
        
        # Verify _change_retract_speed was called
        ACE_INSTANCES[0]._change_retract_speed.assert_called_once_with(0, 25.0)
        
        # Verify success message was sent
        assert mock_gcmd.respond_info.called
        call_args = [call[0][0] for call in mock_gcmd.respond_info.call_args_list]
        assert any("Retract speed updated" in msg for msg in call_args)

    def test_set_retract_speed_validation_errors(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_RETRACT_SPEED validation errors."""
        from ace.config import ACE_INSTANCES
        
        ACE_INSTANCES[0]._change_retract_speed = Mock()
        
        # Test invalid slot
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 9})
        mock_gcmd.get_int = Mock(side_effect=[0, 9])  # INSTANCE, INDEX (invalid)
        mock_gcmd.get_float = Mock(return_value=10.0)
        mock_gcmd.error = Mock(side_effect=Exception)
        
        with pytest.raises(Exception):
            ace.commands.cmd_ACE_SET_RETRACT_SPEED(mock_gcmd)
        
        # Test negative speed
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0])  # INSTANCE, INDEX
        mock_gcmd.get_float = Mock(return_value=-5.0)  # Invalid negative speed
        mock_gcmd.error = Mock(side_effect=Exception)
        
        with pytest.raises(Exception):
            ace.commands.cmd_ACE_SET_RETRACT_SPEED(mock_gcmd)


class TestToolChangeIntegration:
    """Integration tests for ACE_CHANGE_TOOL - the most critical business logic."""

    def test_cmd_ACE_CHANGE_TOOL_unload_tool_success(self, mock_gcmd, setup_mocks):
        """Test tool unload (TOOL=-1) - happy path."""
        mock_gcmd.get_int = Mock(return_value=-1)
        
        # Mock get_variable to return current tool
        with patch('ace.commands.get_variable', return_value=0):
            ace.commands.cmd_ACE_CHANGE_TOOL(INSTANCE_MANAGERS[0], mock_gcmd, -1)
            
            # Verify smart_unload was called
            assert INSTANCE_MANAGERS[0].smart_unload.called
            INSTANCE_MANAGERS[0].smart_unload.assert_called_with(0)
            
            # Verify state was cleared
            assert INSTANCE_MANAGERS[0].set_and_save_variable.called

    def test_cmd_ACE_CHANGE_TOOL_unload_failure(self, mock_gcmd, setup_mocks):
        """Test tool unload failure - error handling."""
        mock_gcmd.get_int = Mock(return_value=-1)
        
        # Mock smart_unload to fail
        INSTANCE_MANAGERS[0].smart_unload = Mock(return_value=False)
        
        with patch('ace.commands.get_variable', return_value=0):
            ace.commands.cmd_ACE_CHANGE_TOOL(INSTANCE_MANAGERS[0], mock_gcmd, -1)
            
            # Verify failure message was sent
            assert mock_gcmd.respond_info.called
            call_args = [call[0][0] for call in mock_gcmd.respond_info.call_args_list]
            assert any("failed" in msg.lower() for msg in call_args)

    def test_cmd_ACE_CHANGE_TOOL_successful_change(self, mock_gcmd, setup_mocks):
        """Test successful tool change - happy path."""
        mock_gcmd.get_int = Mock(return_value=1)
        
        # Mock printer objects
        mock_printer = Mock()
        mock_toolhead = Mock()
        mock_kinematics = Mock()
        mock_reactor = Mock()
        mock_gcode = Mock()
        
        # Mock homing status
        mock_kinematics.get_status = Mock(return_value={'homed_axes': 'xyz'})
        mock_toolhead.get_kinematics = Mock(return_value=mock_kinematics)
        mock_reactor.monotonic = Mock(return_value=1.0)
        mock_printer.get_reactor = Mock(return_value=mock_reactor)
        mock_printer.lookup_object = Mock(side_effect=lambda obj: {
            'toolhead': mock_toolhead,
            'gcode': mock_gcode
        }.get(obj))
        
        # Mock perform_tool_change to succeed
        INSTANCE_MANAGERS[0].perform_tool_change = Mock(return_value="Success")
        
        with patch('ace.commands.get_printer', return_value=mock_printer), \
             patch('ace.commands.get_variable', return_value=0):
            ace.commands.cmd_ACE_CHANGE_TOOL(INSTANCE_MANAGERS[0], mock_gcmd, 1)
            
            # Verify perform_tool_change was called
            assert INSTANCE_MANAGERS[0].perform_tool_change.called
            INSTANCE_MANAGERS[0].perform_tool_change.assert_called_with(0, 1)
            
            # Verify idle timeout was reset
            assert mock_gcode.run_script_from_command.called
            calls = [call[0][0] for call in mock_gcode.run_script_from_command.call_args_list]
            assert any("SET_IDLE_TIMEOUT" in c for c in calls)

    def test_cmd_ACE_CHANGE_TOOL_homing_check(self, mock_gcmd, setup_mocks):
        """Test that printer is homed before tool change."""
        mock_gcmd.get_int = Mock(return_value=1)
        
        # Mock printer objects
        mock_printer = Mock()
        mock_toolhead = Mock()
        mock_kinematics = Mock()
        mock_reactor = Mock()
        mock_gcode = Mock()
        
        # Mock NOT homed
        mock_kinematics.get_status = Mock(return_value={'homed_axes': 'x'})
        mock_toolhead.get_kinematics = Mock(return_value=mock_kinematics)
        mock_reactor.monotonic = Mock(return_value=1.0)
        mock_printer.get_reactor = Mock(return_value=mock_reactor)
        mock_printer.lookup_object = Mock(side_effect=lambda obj: {
            'toolhead': mock_toolhead,
            'gcode': mock_gcode
        }.get(obj))
        
        INSTANCE_MANAGERS[0].perform_tool_change = Mock(return_value="Success")
        
        with patch('ace.commands.get_printer', return_value=mock_printer), \
             patch('ace.commands.get_variable', return_value=0):
            ace.commands.cmd_ACE_CHANGE_TOOL(INSTANCE_MANAGERS[0], mock_gcmd, 1)
            
            # Verify G28 (homing) was called
            assert mock_gcode.run_script_from_command.called
            calls = [call[0][0] for call in mock_gcode.run_script_from_command.call_args_list]
            assert any("G28" in c for c in calls)

    def test_cmd_ACE_CHANGE_TOOL_failure_during_print_pauses(self, mock_gcmd, setup_mocks):
        """Test tool change failure during print - should PAUSE and show dialog."""
        mock_gcmd.get_int = Mock(return_value=1)
        
        # Mock printer objects
        mock_printer = Mock()
        mock_toolhead = Mock()
        mock_kinematics = Mock()
        mock_reactor = Mock()
        mock_gcode = Mock()
        mock_print_stats = Mock()
        
        # Mock homed
        mock_kinematics.get_status = Mock(return_value={'homed_axes': 'xyz'})
        mock_toolhead.get_kinematics = Mock(return_value=mock_kinematics)
        mock_reactor.monotonic = Mock(return_value=1.0)
        mock_printer.get_reactor = Mock(return_value=mock_reactor)
        
        # Mock print_stats to indicate printing
        mock_print_stats.get_status = Mock(return_value={'state': 'printing'})
        
        def lookup_side_effect(obj, default=None):
            if obj == 'toolhead':
                return mock_toolhead
            elif obj == 'gcode':
                return mock_gcode
            elif obj == 'print_stats':
                return mock_print_stats
            return default if default is not None else Mock()
        
        mock_printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        # Mock perform_tool_change to raise exception
        INSTANCE_MANAGERS[0].perform_tool_change = Mock(side_effect=Exception("Sensor blocked"))
        
        with patch('ace.commands.get_printer', return_value=mock_printer), \
             patch('ace.commands.get_variable', return_value=0), \
             patch('ace.commands.set_and_save_variable'):
            ace.commands.cmd_ACE_CHANGE_TOOL(INSTANCE_MANAGERS[0], mock_gcmd, 1)
            
            # Verify PAUSE was called
            assert mock_gcode.run_script_from_command.called
            calls = [call[0][0] for call in mock_gcode.run_script_from_command.call_args_list]
            assert any("PAUSE" == c for c in calls)
            
            # Verify error dialog was shown
            assert any("action:prompt_begin" in c for c in calls)
            assert any("Tool Change Failed" in c for c in calls)
            assert any("action:prompt_show" in c for c in calls)
            
            # Verify retry button was added
            assert any("Retry T1" in c for c in calls)
            
            # Verify Resume and Cancel buttons were added
            assert any("Resume|RESUME" in c for c in calls)
            assert any("Cancel Print|CANCEL_PRINT" in c for c in calls)

    def test_cmd_ACE_CHANGE_TOOL_failure_not_printing_keeps_current_tool_and_turns_off_heater(self, mock_gcmd, setup_mocks):
        """Test idle/startup failure keeps current tool state and turns off heater."""
        mock_gcmd.get_int = Mock(return_value=1)
        mock_gcmd.error = Exception  # Make gcmd.error return an Exception class
        
        # Mock printer objects
        mock_printer = Mock()
        mock_toolhead = Mock()
        mock_kinematics = Mock()
        mock_reactor = Mock()
        mock_gcode = Mock()
        mock_print_stats = Mock()
        
        # Mock homed
        mock_kinematics.get_status = Mock(return_value={'homed_axes': 'xyz'})
        mock_toolhead.get_kinematics = Mock(return_value=mock_kinematics)
        mock_reactor.monotonic = Mock(return_value=1.0)
        mock_printer.get_reactor = Mock(return_value=mock_reactor)
        
        # Mock print_stats to indicate NOT printing
        mock_print_stats.get_status = Mock(return_value={'state': 'ready'})
        
        def lookup_side_effect(obj, default=None):
            if obj == 'toolhead':
                return mock_toolhead
            elif obj == 'gcode':
                return mock_gcode
            elif obj == 'print_stats':
                return mock_print_stats
            return default if default is not None else Mock()
        
        mock_printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        # Mock perform_tool_change to raise exception
        INSTANCE_MANAGERS[0].perform_tool_change = Mock(side_effect=Exception("Load failed"))
        
        mock_set_and_save = Mock()

        with patch('ace.commands.get_printer', return_value=mock_printer), \
             patch('ace.commands.get_variable', return_value=0), \
             patch('ace.commands.set_and_save_variable', mock_set_and_save):
            # Should raise exception for non-printing startup failure
            with pytest.raises(Exception) as exc_info:
                ace.commands.cmd_ACE_CHANGE_TOOL(INSTANCE_MANAGERS[0], mock_gcmd, 1)
            
            assert "failed during startup" in str(exc_info.value) or "Load failed" in str(exc_info.value)
            
            # Verify M104 S0 (heater off) was called before exception
            assert mock_gcode.run_script_from_command.called
            calls = [call[0][0] for call in mock_gcode.run_script_from_command.call_args_list]
            assert any("M104 S0" in c for c in calls)

            # Verify idle/startup failure keeps the previous active tool (0), not target (1)
            mock_set_and_save.assert_called_with(mock_printer, mock_gcode, "ace_current_index", 0)
            assert any("SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE=0" in c for c in calls)

    def test_cmd_ACE_CHANGE_TOOL_failure_not_printing_after_unload_sets_no_active_tool(self, mock_gcmd, setup_mocks):
        """Test idle/startup failure sets active tool to -1 when filament is already in bowden."""
        mock_gcmd.get_int = Mock(return_value=1)
        mock_gcmd.error = Exception

        # Mock printer objects
        mock_printer = Mock()
        mock_toolhead = Mock()
        mock_kinematics = Mock()
        mock_reactor = Mock()
        mock_gcode = Mock()
        mock_print_stats = Mock()
        mock_save_vars = Mock()
        mock_save_vars.allVariables = {"ace_filament_pos": "bowden"}

        # Mock homed
        mock_kinematics.get_status = Mock(return_value={'homed_axes': 'xyz'})
        mock_toolhead.get_kinematics = Mock(return_value=mock_kinematics)
        mock_reactor.monotonic = Mock(return_value=1.0)
        mock_printer.get_reactor = Mock(return_value=mock_reactor)

        # Mock print_stats to indicate NOT printing
        mock_print_stats.get_status = Mock(return_value={'state': 'ready'})

        def lookup_side_effect(obj, default=None):
            if obj == 'toolhead':
                return mock_toolhead
            elif obj == 'gcode':
                return mock_gcode
            elif obj == 'print_stats':
                return mock_print_stats
            elif obj == 'save_variables':
                return mock_save_vars
            return default if default is not None else Mock()

        mock_printer.lookup_object = Mock(side_effect=lookup_side_effect)

        INSTANCE_MANAGERS[0].perform_tool_change = Mock(side_effect=Exception("Load failed after unload"))

        mock_set_and_save = Mock()

        with patch('ace.commands.get_printer', return_value=mock_printer), \
             patch('ace.commands.get_variable', return_value=0), \
             patch('ace.commands.set_and_save_variable', mock_set_and_save):
            with pytest.raises(Exception):
                ace.commands.cmd_ACE_CHANGE_TOOL(INSTANCE_MANAGERS[0], mock_gcmd, 1)

            # Idle/startup + bowden state: clear active tool.
            mock_set_and_save.assert_called_with(mock_printer, mock_gcode, "ace_current_index", -1)
            calls = [call[0][0] for call in mock_gcode.run_script_from_command.call_args_list]
            assert any("SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE=-1" in c for c in calls)

    def test_cmd_ACE_CHANGE_TOOL_failure_updates_state_before_dialog(self, mock_gcmd, setup_mocks):
        """Test that tool state is updated even when tool change fails during printing."""
        mock_gcmd.get_int = Mock(return_value=2)
        
        # Mock printer objects
        mock_printer = Mock()
        mock_toolhead = Mock()
        mock_kinematics = Mock()
        mock_reactor = Mock()
        mock_gcode = Mock()
        mock_print_stats = Mock()
        
        # Mock homed
        mock_kinematics.get_status = Mock(return_value={'homed_axes': 'xyz'})
        mock_toolhead.get_kinematics = Mock(return_value=mock_kinematics)
        mock_reactor.monotonic = Mock(return_value=1.0)
        mock_printer.get_reactor = Mock(return_value=mock_reactor)
        
        # Mock print_stats to indicate PRINTING (so dialog is shown instead of exception)
        mock_print_stats.get_status = Mock(return_value={'state': 'printing'})
        
        def lookup_side_effect(obj, default=None):
            if obj == 'toolhead':
                return mock_toolhead
            elif obj == 'gcode':
                return mock_gcode
            elif obj == 'print_stats':
                return mock_print_stats
            return default if default is not None else Mock()
        
        mock_printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        # Mock perform_tool_change to raise exception
        INSTANCE_MANAGERS[0].perform_tool_change = Mock(side_effect=Exception("Test failure"))
        
        mock_set_and_save = Mock()
        
        with patch('ace.commands.get_printer', return_value=mock_printer), \
             patch('ace.commands.get_variable', return_value=0), \
             patch('ace.commands.set_and_save_variable', mock_set_and_save):
            ace.commands.cmd_ACE_CHANGE_TOOL(INSTANCE_MANAGERS[0], mock_gcmd, 2)
            
            # Verify state was updated to tool 2 even though it failed
            assert mock_set_and_save.called
            mock_set_and_save.assert_called_with(mock_printer, mock_gcode, "ace_current_index", 2)
            
            # Verify SET_GCODE_VARIABLE was called
            assert mock_gcode.run_script_from_command.called
            calls = [call[0][0] for call in mock_gcode.run_script_from_command.call_args_list]
            assert any("SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE=2" in c for c in calls)

    def test_cmd_ACE_CHANGE_TOOL_disabled_ace_global(self, mock_gcmd, setup_mocks):
        """Test that tool change is ignored when ACE global is disabled."""
        mock_gcmd.get_int = Mock(return_value=1)
        
        # Mock ACE global disabled
        INSTANCE_MANAGERS[0].get_ace_global_enabled = Mock(return_value=False)
        
        ace.commands.cmd_ACE_CHANGE_TOOL(INSTANCE_MANAGERS[0], mock_gcmd, 1)
        
        # Verify no tool change was performed
        assert not INSTANCE_MANAGERS[0].perform_tool_change.called
        
        # Verify message about ACE being disabled
        assert mock_gcmd.respond_info.called
        call_args = [call[0][0] for call in mock_gcmd.respond_info.call_args_list]
        assert any("disabled" in msg.lower() for msg in call_args)

    def test_cmd_ACE_CHANGE_TOOL_WRAPPER(self, mock_gcmd, setup_mocks):
        """Test the wrapper function calls the main function correctly."""
        mock_gcmd.get_int = Mock(return_value=3)
        
        with patch('ace.commands.cmd_ACE_CHANGE_TOOL') as mock_change_tool:
            ace.commands.cmd_ACE_CHANGE_TOOL_WRAPPER(mock_gcmd)
            
            # Verify it called the main function with correct params
            assert mock_change_tool.called
            mock_change_tool.assert_called_with(INSTANCE_MANAGERS[0], mock_gcmd, 3)

    def test_cmd_ACE_CHANGE_TOOL_error_dialog_escapes_quotes(self, mock_gcmd, setup_mocks):
        """Test that error messages with quotes are properly escaped in dialog."""
        mock_gcmd.get_int = Mock(return_value=1)
        
        # Mock printer objects
        mock_printer = Mock()
        mock_toolhead = Mock()
        mock_kinematics = Mock()
        mock_reactor = Mock()
        mock_gcode = Mock()
        mock_print_stats = Mock()
        
        mock_kinematics.get_status = Mock(return_value={'homed_axes': 'xyz'})
        mock_toolhead.get_kinematics = Mock(return_value=mock_kinematics)
        mock_reactor.monotonic = Mock(return_value=1.0)
        mock_printer.get_reactor = Mock(return_value=mock_reactor)
        
        # Mock print_stats to indicate PRINTING (so dialog is shown instead of exception)
        mock_print_stats.get_status = Mock(return_value={'state': 'printing'})
        
        def lookup_side_effect(obj, default=None):
            if obj == 'toolhead':
                return mock_toolhead
            elif obj == 'gcode':
                return mock_gcode
            elif obj == 'print_stats':
                return mock_print_stats
            return default if default is not None else Mock()
        
        mock_printer.lookup_object = Mock(side_effect=lookup_side_effect)
        
        # Error message with quotes
        INSTANCE_MANAGERS[0].perform_tool_change = Mock(
            side_effect=Exception('Sensor "toolhead" blocked')
        )
        
        with patch('ace.commands.get_printer', return_value=mock_printer), \
             patch('ace.commands.get_variable', return_value=0), \
             patch('ace.commands.set_and_save_variable'):
            ace.commands.cmd_ACE_CHANGE_TOOL(INSTANCE_MANAGERS[0], mock_gcmd, 1)
            
            # Verify quotes were escaped in prompt_text
            assert mock_gcode.run_script_from_command.called
            calls = [call[0][0] for call in mock_gcode.run_script_from_command.call_args_list]
            
            # Find the prompt_text command
            prompt_cmds = [c for c in calls if "action:prompt_text" in c]
            assert len(prompt_cmds) > 0
            
            # Verify quotes are escaped with backslash
            assert '\\"toolhead\\"' in prompt_cmds[0]


class TestSpeedChangeCommands:
    """Tests for runtime speed adjustment commands."""

    def test_cmd_ACE_SET_RETRACT_SPEED_success(self, mock_gcmd, setup_mocks):
        """Test successful retract speed change."""
        mock_gcmd.get_float = Mock(return_value=80.0)
        
        # Mock _change_retract_speed to succeed
        ACE_INSTANCES[0]._change_retract_speed = Mock(return_value=True)
        
        with patch('ace.commands.ace_get_instance_and_slot', return_value=(ACE_INSTANCES[0], 1)):
            ace.commands.cmd_ACE_SET_RETRACT_SPEED(mock_gcmd)
            
            # Verify the speed change was called
            ACE_INSTANCES[0]._change_retract_speed.assert_called_with(1, 80.0)
            
            # Verify success message
            assert mock_gcmd.respond_info.called
            call_args = str(mock_gcmd.respond_info.call_args_list)
            assert "updated" in call_args.lower()
            assert "80" in call_args

    def test_cmd_ACE_SET_RETRACT_SPEED_failure(self, mock_gcmd, setup_mocks):
        """Test retract speed change failure."""
        mock_gcmd.get_float = Mock(return_value=50.0)
        
        # Mock _change_retract_speed to fail
        ACE_INSTANCES[0]._change_retract_speed = Mock(return_value=False)
        
        with patch('ace.commands.ace_get_instance_and_slot', return_value=(ACE_INSTANCES[0], 2)):
            ace.commands.cmd_ACE_SET_RETRACT_SPEED(mock_gcmd)
            
            # Verify failure message
            assert mock_gcmd.respond_info.called
            call_args = str(mock_gcmd.respond_info.call_args_list)
            assert "failed" in call_args.lower()

    def test_cmd_ACE_SET_RETRACT_SPEED_negative_speed(self, mock_gcmd, setup_mocks):
        """Test retract speed validation rejects negative speed."""
        mock_gcmd.get_float = Mock(return_value=-10.0)
        mock_gcmd.error = Mock(side_effect=Exception("SPEED must be positive"))
        
        with patch('ace.commands.ace_get_instance_and_slot', return_value=(ACE_INSTANCES[0], 0)):
            with pytest.raises(Exception, match="SPEED must be positive"):
                ace.commands.cmd_ACE_SET_RETRACT_SPEED(mock_gcmd)

    def test_cmd_ACE_SET_RETRACT_SPEED_zero_speed(self, mock_gcmd, setup_mocks):
        """Test retract speed validation rejects zero speed."""
        mock_gcmd.get_float = Mock(return_value=0.0)
        mock_gcmd.error = Mock(side_effect=Exception("SPEED must be positive"))
        
        with patch('ace.commands.ace_get_instance_and_slot', return_value=(ACE_INSTANCES[0], 0)):
            with pytest.raises(Exception, match="SPEED must be positive"):
                ace.commands.cmd_ACE_SET_RETRACT_SPEED(mock_gcmd)

    def test_cmd_ACE_SET_RETRACT_SPEED_invalid_slot(self, mock_gcmd, setup_mocks):
        """Test retract speed validation rejects invalid slot."""
        mock_gcmd.get_float = Mock(return_value=50.0)
        mock_gcmd.error = Mock(side_effect=Exception("Invalid slot"))
        
        # Slot 10 is out of range (SLOT_COUNT = 4)
        with patch('ace.commands.ace_get_instance_and_slot', return_value=(ACE_INSTANCES[0], 10)):
            with pytest.raises(Exception, match="Invalid slot"):
                ace.commands.cmd_ACE_SET_RETRACT_SPEED(mock_gcmd)

    def test_cmd_ACE_SET_FEED_SPEED_success(self, mock_gcmd, setup_mocks):
        """Test successful feed speed change."""
        mock_gcmd.get_float = Mock(return_value=120.0)
        
        # Mock _change_feed_speed to succeed
        ACE_INSTANCES[0]._change_feed_speed = Mock(return_value=True)
        
        with patch('ace.commands.ace_get_instance_and_slot', return_value=(ACE_INSTANCES[0], 3)):
            ace.commands.cmd_ACE_SET_FEED_SPEED(mock_gcmd)
            
            # Verify the speed change was called
            ACE_INSTANCES[0]._change_feed_speed.assert_called_with(3, 120.0)
            
            # Verify success message
            assert mock_gcmd.respond_info.called
            call_args = str(mock_gcmd.respond_info.call_args_list)
            assert "updated" in call_args.lower()
            assert "120" in call_args

    def test_cmd_ACE_SET_FEED_SPEED_failure(self, mock_gcmd, setup_mocks):
        """Test feed speed change failure."""
        mock_gcmd.get_float = Mock(return_value=90.0)
        
        # Mock _change_feed_speed to fail
        ACE_INSTANCES[0]._change_feed_speed = Mock(return_value=False)
        
        with patch('ace.commands.ace_get_instance_and_slot', return_value=(ACE_INSTANCES[0], 1)):
            ace.commands.cmd_ACE_SET_FEED_SPEED(mock_gcmd)
            
            # Verify failure message
            assert mock_gcmd.respond_info.called
            call_args = str(mock_gcmd.respond_info.call_args_list)
            assert "failed" in call_args.lower()

    def test_cmd_ACE_SET_FEED_SPEED_negative_speed(self, mock_gcmd, setup_mocks):
        """Test feed speed validation rejects negative speed."""
        mock_gcmd.get_float = Mock(return_value=-20.0)
        mock_gcmd.error = Mock(side_effect=Exception("SPEED must be positive"))
        
        with patch('ace.commands.ace_get_instance_and_slot', return_value=(ACE_INSTANCES[0], 0)):
            with pytest.raises(Exception, match="SPEED must be positive"):
                ace.commands.cmd_ACE_SET_FEED_SPEED(mock_gcmd)


class TestFullUnloadCommand:
    """Integration tests for ACE_FULL_UNLOAD - especially the TOOL=ALL scenario."""

    def test_cmd_ACE_FULL_UNLOAD_single_tool_success(self, mock_gcmd, setup_mocks):
        """Test full unload of a single tool - happy path."""
        mock_gcmd.get = Mock(return_value=None)
        mock_gcmd.get_int = Mock(return_value=2)
        
        # Mock full_unload_slot to succeed
        INSTANCE_MANAGERS[0].full_unload_slot = Mock(return_value=True)
        
        mock_printer = Mock()
        mock_gcode = Mock()
        INSTANCE_MANAGERS[0].printer = mock_printer
        INSTANCE_MANAGERS[0].gcode = mock_gcode
        
        with patch('ace.commands.set_and_save_variable') as mock_save:
            ace.commands.cmd_ACE_FULL_UNLOAD(mock_gcmd)
            
            # Verify full_unload_slot was called
            INSTANCE_MANAGERS[0].full_unload_slot.assert_called_with(2)
            
            # Verify state was cleared
            assert mock_save.called
            mock_save.assert_called_with(mock_printer, mock_gcode, "ace_current_index", -1)
            
            # Verify success message
            assert mock_gcmd.respond_info.called
            call_args = str(mock_gcmd.respond_info.call_args_list)
            assert "fully unloaded" in call_args.lower()

    def test_cmd_ACE_FULL_UNLOAD_single_tool_failure(self, mock_gcmd, setup_mocks):
        """Test full unload of a single tool - failure."""
        mock_gcmd.get = Mock(return_value=None)
        mock_gcmd.get_int = Mock(return_value=1)
        
        # Mock full_unload_slot to fail
        INSTANCE_MANAGERS[0].full_unload_slot = Mock(return_value=False)
        
        INSTANCE_MANAGERS[0].printer = Mock()
        INSTANCE_MANAGERS[0].gcode = Mock()
        
        ace.commands.cmd_ACE_FULL_UNLOAD(mock_gcmd)
        
        # Verify failure message (state should NOT be cleared)
        assert mock_gcmd.respond_info.called
        call_args = str(mock_gcmd.respond_info.call_args_list)
        assert "failed" in call_args.lower() or "incomplete" in call_args.lower()

    def test_cmd_ACE_FULL_UNLOAD_uses_current_tool_when_not_specified(self, mock_gcmd, setup_mocks):
        """Test that TOOL defaults to current tool when not specified."""
        mock_gcmd.get = Mock(return_value=None)
        mock_gcmd.get_int = Mock(return_value=-1)  # No TOOL specified
        
        # Mock save_vars to return current tool
        mock_save_vars = Mock()
        mock_save_vars.allVariables = {"ace_current_index": 3}
        mock_printer = Mock()
        mock_printer.lookup_object = Mock(return_value=mock_save_vars)
        
        INSTANCE_MANAGERS[0].printer = mock_printer
        INSTANCE_MANAGERS[0].gcode = Mock()
        INSTANCE_MANAGERS[0].full_unload_slot = Mock(return_value=True)
        
        with patch('ace.commands.set_and_save_variable'):
            ace.commands.cmd_ACE_FULL_UNLOAD(mock_gcmd)
            
            # Verify it used the current tool (3)
            INSTANCE_MANAGERS[0].full_unload_slot.assert_called_with(3)

    def test_cmd_ACE_FULL_UNLOAD_all_tools_empty_slots_skipped(self, mock_gcmd, setup_mocks):
        """Test TOOL=ALL skips empty slots."""
        from ace.config import FILAMENT_STATE_BOWDEN
        
        mock_gcmd.get = Mock(return_value="ALL")
        
        # Setup inventory with some empty slots
        ACE_INSTANCES[0].inventory = [
            {"status": "ready"},    # Slot 0: ready
            {"status": "empty"},    # Slot 1: empty - should skip
            {"status": "loaded"},   # Slot 2: loaded
            {"status": "empty"},    # Slot 3: empty - should skip
        ]
        
        mock_save_vars = Mock()
        mock_save_vars.allVariables = {
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN
        }
        mock_printer = Mock()
        mock_printer.lookup_object = Mock(return_value=mock_save_vars)
        
        INSTANCE_MANAGERS[0].printer = mock_printer
        INSTANCE_MANAGERS[0].gcode = Mock()
        INSTANCE_MANAGERS[0].full_unload_slot = Mock(return_value=True)
        
        with patch('ace.commands.set_and_save_variable'):
            ace.commands.cmd_ACE_FULL_UNLOAD(mock_gcmd)
            
            # Should only unload slots 0 and 2 (not 1 and 3 which are empty)
            assert INSTANCE_MANAGERS[0].full_unload_slot.call_count == 2
            calls = [call[0][0] for call in INSTANCE_MANAGERS[0].full_unload_slot.call_args_list]
            assert 0 in calls  # Tool 0 (slot 0)
            assert 2 in calls  # Tool 2 (slot 2)

    def test_cmd_ACE_FULL_UNLOAD_all_tools_skips_nozzle_loaded(self, mock_gcmd, setup_mocks):
        """Test TOOL=ALL skips tool currently loaded at nozzle."""
        from ace.config import FILAMENT_STATE_NOZZLE
        
        mock_gcmd.get = Mock(return_value="ALL")
        
        # All slots ready
        ACE_INSTANCES[0].inventory = [
            {"status": "ready"},
            {"status": "ready"},
            {"status": "ready"},
            {"status": "ready"},
        ]
        
        # Tool 1 is currently loaded at nozzle
        mock_save_vars = Mock()
        mock_save_vars.allVariables = {
            "ace_current_index": 1,
            "ace_filament_pos": FILAMENT_STATE_NOZZLE
        }
        mock_printer = Mock()
        mock_printer.lookup_object = Mock(return_value=mock_save_vars)
        
        INSTANCE_MANAGERS[0].printer = mock_printer
        INSTANCE_MANAGERS[0].gcode = Mock()
        INSTANCE_MANAGERS[0].full_unload_slot = Mock(return_value=True)
        
        with patch('ace.commands.set_and_save_variable'):
            ace.commands.cmd_ACE_FULL_UNLOAD(mock_gcmd)
            
            # Should unload 0, 2, 3 but NOT 1 (loaded at nozzle)
            assert INSTANCE_MANAGERS[0].full_unload_slot.call_count == 3
            calls = [call[0][0] for call in INSTANCE_MANAGERS[0].full_unload_slot.call_args_list]
            assert 0 in calls
            assert 2 in calls
            assert 3 in calls
            assert 1 not in calls  # Should be skipped

    def test_cmd_ACE_FULL_UNLOAD_all_tools_tracks_failures(self, mock_gcmd, setup_mocks):
        """Test TOOL=ALL tracks and reports failures correctly."""
        from ace.config import FILAMENT_STATE_BOWDEN
        
        mock_gcmd.get = Mock(return_value="ALL")
        
        ACE_INSTANCES[0].inventory = [
            {"status": "ready"},
            {"status": "ready"},
            {"status": "ready"},
            {"status": "empty"},
        ]
        
        mock_save_vars = Mock()
        mock_save_vars.allVariables = {
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN
        }
        mock_printer = Mock()
        mock_printer.lookup_object = Mock(return_value=mock_save_vars)
        
        INSTANCE_MANAGERS[0].printer = mock_printer
        INSTANCE_MANAGERS[0].gcode = Mock()
        
        # Slot 1 will fail, others succeed
        def mock_unload(tool):
            return tool != 1  # Fail tool 1
        
        INSTANCE_MANAGERS[0].full_unload_slot = Mock(side_effect=mock_unload)
        
        with patch('ace.commands.set_and_save_variable'):
            ace.commands.cmd_ACE_FULL_UNLOAD(mock_gcmd)
            
            # Verify summary was reported
            assert mock_gcmd.respond_info.called
            call_args = "\n".join([str(call[0][0]) for call in mock_gcmd.respond_info.call_args_list])
            
            # Should show 3 processed, 2 succeeded, 1 failed
            assert "3" in call_args  # Total processed
            assert "2" in call_args  # Succeeded
            assert "Failed: 1" in call_args or "T1" in call_args  # Failed list

    def test_cmd_ACE_FULL_UNLOAD_all_clears_state_on_full_success(self, mock_gcmd, setup_mocks):
        """Test TOOL=ALL clears current tool state when all slots succeed."""
        from ace.config import FILAMENT_STATE_BOWDEN
        
        mock_gcmd.get = Mock(return_value="ALL")
        
        ACE_INSTANCES[0].inventory = [
            {"status": "ready"},
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        
        mock_save_vars = Mock()
        mock_save_vars.allVariables = {
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN
        }
        mock_printer = Mock()
        mock_gcode = Mock()
        mock_printer.lookup_object = Mock(return_value=mock_save_vars)
        
        INSTANCE_MANAGERS[0].printer = mock_printer
        INSTANCE_MANAGERS[0].gcode = mock_gcode
        INSTANCE_MANAGERS[0].full_unload_slot = Mock(return_value=True)
        
        with patch('ace.commands.set_and_save_variable') as mock_save:
            ace.commands.cmd_ACE_FULL_UNLOAD(mock_gcmd)
            
            # Verify state was cleared
            assert mock_save.called
            mock_save.assert_called_with(mock_printer, mock_gcode, "ace_current_index", -1)

    def test_cmd_ACE_FULL_UNLOAD_all_handles_exceptions(self, mock_gcmd, setup_mocks):
        """Test TOOL=ALL handles exceptions from individual unloads."""
        from ace.config import FILAMENT_STATE_BOWDEN
        
        mock_gcmd.get = Mock(return_value="ALL")
        
        ACE_INSTANCES[0].inventory = [
            {"status": "ready"},
            {"status": "ready"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        
        mock_save_vars = Mock()
        mock_save_vars.allVariables = {
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN
        }
        mock_printer = Mock()
        mock_printer.lookup_object = Mock(return_value=mock_save_vars)
        
        INSTANCE_MANAGERS[0].printer = mock_printer
        INSTANCE_MANAGERS[0].gcode = Mock()
        
        # Slot 0 raises exception, slot 1 succeeds
        def mock_unload(tool):
            if tool == 0:
                raise Exception("Sensor blocked")
            return True
        
        INSTANCE_MANAGERS[0].full_unload_slot = Mock(side_effect=mock_unload)
        
        with patch('ace.commands.set_and_save_variable'):
            ace.commands.cmd_ACE_FULL_UNLOAD(mock_gcmd)
            
            # Should report the error
            assert mock_gcmd.respond_info.called
            call_args = "\n".join([str(call[0][0]) for call in mock_gcmd.respond_info.call_args_list])
            assert "Error" in call_args or "Failed" in call_args


class TestResetPersistentInventory:
    """Integration tests for ACE_RESET_PERSISTENT_INVENTORY command."""

    def test_cmd_ACE_RESET_PERSISTENT_INVENTORY_single_instance(self, mock_gcmd, setup_mocks):
        """Test reset inventory for a specific instance."""
        mock_gcmd.get_int = Mock(return_value=0)
        
        # Setup mock inventory with non-empty data
        ACE_INSTANCES[0].inventory = [
            {"status": "ready", "material": "PLA"},
            {"status": "loaded", "material": "PETG"},
            {"status": "empty"},
            {"status": "ready", "material": "ABS"},
        ]
        
        # Mock reset_persistent_inventory
        ACE_INSTANCES[0].reset_persistent_inventory = Mock()
        
        # Mock manager sync
        INSTANCE_MANAGERS[0]._sync_inventory_to_persistent = Mock()
        
        with patch('ace.commands.ace_get_instance', return_value=ACE_INSTANCES[0]):
            ace.commands.cmd_ACE_RESET_PERSISTENT_INVENTORY(mock_gcmd)
            
            # Verify reset was called
            assert ACE_INSTANCES[0].reset_persistent_inventory.called
            
            # Verify sync was called with correct instance
            INSTANCE_MANAGERS[0]._sync_inventory_to_persistent.assert_called_with(0)
            
            # Verify user feedback
            assert mock_gcmd.respond_info.called
            call_args = str(mock_gcmd.respond_info.call_args_list)
            assert "ACE[0]" in call_args
            assert "reset" in call_args.lower()

    def test_cmd_ACE_RESET_PERSISTENT_INVENTORY_all_instances(self, mock_gcmd, setup_mocks):
        """Test reset inventory for ALL instances."""
        mock_gcmd.get_int = Mock(return_value=None)
        
        # Create second instance for multi-instance test
        instance1 = Mock()
        instance1.instance_num = 1
        instance1.SLOT_COUNT = 4
        instance1.inventory = [
            {"status": "ready", "material": "ABS"},
            {"status": "ready", "material": "TPU"},
            {"status": "ready", "material": "Nylon"},
            {"status": "empty"},
        ]
        instance1.reset_persistent_inventory = Mock()
        
        ACE_INSTANCES[1] = instance1
        INSTANCE_MANAGERS[0].instances[1] = instance1
        
        # Setup instance 0 inventory
        ACE_INSTANCES[0].inventory = [
            {"status": "ready", "material": "PLA"},
            {"status": "ready", "material": "PETG"},
            {"status": "ready", "material": "TPU"},
            {"status": "ready", "material": "ABS"},
        ]
        ACE_INSTANCES[0].reset_persistent_inventory = Mock()
        
        INSTANCE_MANAGERS[0]._sync_inventory_to_persistent = Mock()
        
        try:
            ace.commands.cmd_ACE_RESET_PERSISTENT_INVENTORY(mock_gcmd)
            
            # Verify reset was called on ALL instances
            assert ACE_INSTANCES[0].reset_persistent_inventory.called
            assert ACE_INSTANCES[1].reset_persistent_inventory.called
            
            # Verify sync was called for each instance
            assert INSTANCE_MANAGERS[0]._sync_inventory_to_persistent.call_count == 2
            calls = [call[0][0] for call in INSTANCE_MANAGERS[0]._sync_inventory_to_persistent.call_args_list]
            assert 0 in calls
            assert 1 in calls
            
            # Verify user feedback mentions all instances
            assert mock_gcmd.respond_info.called
            call_args = "\n".join([str(call[0][0]) for call in mock_gcmd.respond_info.call_args_list])
            assert "ACE[0]" in call_args
            assert "ACE[1]" in call_args
            assert "All 2 ACE instances reset" in call_args
        finally:
            # Cleanup
            if 1 in ACE_INSTANCES:
                del ACE_INSTANCES[1]
            if 1 in INSTANCE_MANAGERS[0].instances:
                del INSTANCE_MANAGERS[0].instances[1]

    def test_cmd_ACE_RESET_PERSISTENT_INVENTORY_verifies_empty_state(self, mock_gcmd, setup_mocks):
        """Test that reset actually sets inventory to empty state."""
        mock_gcmd.get_int = Mock(return_value=0)
        
        # Start with loaded inventory
        ACE_INSTANCES[0].inventory = [
            {"status": "ready", "material": "PLA", "color": [255, 0, 0]},
            {"status": "loaded", "material": "PETG", "color": [0, 255, 0]},
            {"status": "ready", "material": "TPU", "color": [0, 0, 255]},
            {"status": "ready", "material": "ABS", "color": [255, 255, 0]},
        ]
        
        # Use real reset implementation
        def real_reset():
            ACE_INSTANCES[0].inventory = [
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            ]
        
        ACE_INSTANCES[0].reset_persistent_inventory = Mock(side_effect=real_reset)
        INSTANCE_MANAGERS[0]._sync_inventory_to_persistent = Mock()
        
        with patch('ace.commands.ace_get_instance', return_value=ACE_INSTANCES[0]):
            ace.commands.cmd_ACE_RESET_PERSISTENT_INVENTORY(mock_gcmd)
            
            # Verify inventory is now empty
            for slot in ACE_INSTANCES[0].inventory:
                assert slot["status"] == "empty"
                assert slot["material"] == ""
                assert slot["color"] == [0, 0, 0]

    def test_cmd_ACE_RESET_PERSISTENT_INVENTORY_invalid_instance(self, mock_gcmd, setup_mocks):
        """Test error handling when invalid instance specified."""
        mock_gcmd.get_int = Mock(return_value=99)
        mock_gcmd.error = Mock(side_effect=Exception("No ACE instance 99"))
        
        with patch('ace.commands.ace_get_instance', side_effect=Exception("No ACE instance 99")):
            with pytest.raises(Exception, match="No ACE instance 99"):
                ace.commands.cmd_ACE_RESET_PERSISTENT_INVENTORY(mock_gcmd)

    def test_cmd_ACE_RESET_PERSISTENT_INVENTORY_preserves_other_instances(self, mock_gcmd, setup_mocks):
        """Test that resetting one instance doesn't affect others."""
        mock_gcmd.get_int = Mock(return_value=0)
        
        # Create second instance
        instance1 = Mock()
        instance1.instance_num = 1
        instance1.SLOT_COUNT = 4
        instance1.inventory = [
            {"status": "loaded", "material": "ABS"},
            {"status": "ready", "material": "TPU"},
            {"status": "ready", "material": "Nylon"},
            {"status": "empty"},
        ]
        instance1.reset_persistent_inventory = Mock()
        
        ACE_INSTANCES[1] = instance1
        INSTANCE_MANAGERS[0].instances[1] = instance1
        
        # Setup instance 0 inventory
        ACE_INSTANCES[0].inventory = [
            {"status": "ready", "material": "PLA"},
            {"status": "ready", "material": "PETG"},
            {"status": "empty"},
            {"status": "empty"},
        ]
        
        # Copy instance 1 inventory for comparison
        instance1_inventory_before = ACE_INSTANCES[1].inventory.copy()
        
        ACE_INSTANCES[0].reset_persistent_inventory = Mock()
        INSTANCE_MANAGERS[0]._sync_inventory_to_persistent = Mock()
        
        try:
            with patch('ace.commands.ace_get_instance', return_value=ACE_INSTANCES[0]):
                ace.commands.cmd_ACE_RESET_PERSISTENT_INVENTORY(mock_gcmd)
                
                # Verify only instance 0 was reset
                assert ACE_INSTANCES[0].reset_persistent_inventory.called
                assert not ACE_INSTANCES[1].reset_persistent_inventory.called
                
                # Verify instance 1 inventory unchanged
                assert ACE_INSTANCES[1].inventory == instance1_inventory_before
        finally:
            # Cleanup
            if 1 in ACE_INSTANCES:
                del ACE_INSTANCES[1]
            if 1 in INSTANCE_MANAGERS[0].instances:
                del INSTANCE_MANAGERS[0].instances[1]



class TestConnectionStatusCommand:
    """Tests for cmd_ACE_GET_CONNECTION_STATUS."""

    def setup_method(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        # Setup mock instance with serial manager
        self.mock_instance = Mock()
        self.mock_instance.instance_num = 0
        self.mock_instance.serial_mgr = Mock()
        # Set real numeric values (not Mocks) for attributes accessed by getattr
        self.mock_instance.serial_mgr.INSTABILITY_THRESHOLD = 3
        self.mock_instance.serial_mgr.INSTABILITY_WINDOW = 60.0
        self.mock_instance.serial_mgr.STABILITY_GRACE_PERIOD = 30.0
        self.mock_instance.serial_mgr._reconnect_backoff = 5.0
        self.mock_instance.serial_mgr.RECONNECT_BACKOFF_MIN = 5.0
        self.mock_instance.serial_mgr.RECONNECT_BACKOFF_MAX = 30.0
        self.mock_instance.serial_mgr._supervision_enabled = True
        
        ACE_INSTANCES[0] = self.mock_instance
        
        self.mock_gcmd = Mock()
        self.mock_gcmd.respond_info = Mock()

    def teardown_method(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

    def test_connected_stable_status(self):
        """Test status display when connection is stable."""
        self.mock_instance.serial_mgr.get_connection_status = Mock(return_value={
            "connected": True,
            "stable": True,
            "time_connected": 120.0,
            "recent_reconnects": 0,
            "port": "/dev/ttyACM0",
            "usb_topology": "2-1.1",
            "supervision": {
                "timeout_count": 0,
                "timeout_threshold": 15,
                "unsolicited_count": 0,
                "unsolicited_threshold": 15,
                "window_seconds": 30,
            }
        })
        
        ace.commands.cmd_ACE_GET_CONNECTION_STATUS(self.mock_gcmd)
        
        # Verify output
        calls = [str(call) for call in self.mock_gcmd.respond_info.call_args_list]
        output = "\n".join(calls)
        assert "Connected (stable)" in output
        assert "ACE[0]:" in output

    def test_connected_stabilizing_status(self):
        """Test status display when connection is stabilizing."""
        self.mock_instance.serial_mgr.get_connection_status = Mock(return_value={
            "connected": True,
            "stable": False,
            "time_connected": 25.5,
            "recent_reconnects": 0,
            "port": "/dev/ttyACM0",
            "usb_topology": "2-1.1",
            "supervision": {
                "timeout_count": 0,
                "timeout_threshold": 15,
                "unsolicited_count": 0,
                "unsolicited_threshold": 15,
                "window_seconds": 30,
            }
        })
        
        ace.commands.cmd_ACE_GET_CONNECTION_STATUS(self.mock_gcmd)
        
        calls = [str(call) for call in self.mock_gcmd.respond_info.call_args_list]
        output = "\n".join(calls)
        assert "stabilizing" in output
        assert "25s" in output or "26s" in output  # Rounded time

    def test_disconnected_status_with_backoff(self):
        """Test status display when disconnected with backoff info."""
        self.mock_instance.serial_mgr.get_connection_status = Mock(return_value={
            "connected": False,
            "stable": False,
            "time_connected": 0.0,
            "recent_reconnects": 2,
            "port": "unknown",
            "usb_topology": "unknown",
            "supervision": {
                "timeout_count": 0,
                "timeout_threshold": 15,
                "unsolicited_count": 0,
                "unsolicited_threshold": 15,
                "window_seconds": 30,
            }
        })
        self.mock_instance.serial_mgr._reconnect_backoff = 10.0
        
        ace.commands.cmd_ACE_GET_CONNECTION_STATUS(self.mock_gcmd)
        
        calls = [str(call) for call in self.mock_gcmd.respond_info.call_args_list]
        output = "\n".join(calls)
        assert "Disconnected" in output
        assert "next retry in 10" in output or "10.0s" in output

    def test_with_recent_reconnects(self):
        """Test status display with recent reconnect attempts."""
        self.mock_instance.serial_mgr.get_connection_status = Mock(return_value={
            "connected": True,
            "stable": False,
            "time_connected": 15.0,
            "recent_reconnects": 2,
            "port": "/dev/ttyACM0",
            "usb_topology": "2-1.1",
            "supervision": {
                "timeout_count": 0,
                "timeout_threshold": 15,
                "unsolicited_count": 0,
                "unsolicited_threshold": 15,
                "window_seconds": 30,
            }
        })
        
        ace.commands.cmd_ACE_GET_CONNECTION_STATUS(self.mock_gcmd)
        
        calls = [str(call) for call in self.mock_gcmd.respond_info.call_args_list]
        output = "\n".join(calls)
        assert "2 failures" in output  # Changed from "2/3 reconnects in 60s"

    def test_multiple_instances(self):
        """Test status display for multiple instances."""
        # Add second instance
        mock_instance1 = Mock()
        mock_instance1.instance_num = 1
        mock_instance1.serial_mgr = Mock()
        mock_instance1.serial_mgr.INSTABILITY_THRESHOLD = 3
        mock_instance1.serial_mgr.INSTABILITY_WINDOW = 60.0
        mock_instance1.serial_mgr.STABILITY_GRACE_PERIOD = 30.0
        mock_instance1.serial_mgr._reconnect_backoff = 2.0
        mock_instance1.serial_mgr.RECONNECT_BACKOFF_MIN = 5.0
        mock_instance1.serial_mgr.RECONNECT_BACKOFF_MAX = 30.0
        mock_instance1.serial_mgr._supervision_enabled = True
        mock_instance1.serial_mgr.get_connection_status = Mock(return_value={
            "connected": False,
            "stable": False,
            "time_connected": 0.0,
            "recent_reconnects": 1,
            "port": "unknown",
            "usb_topology": "unknown",
            "supervision": {
                "timeout_count": 0,
                "timeout_threshold": 15,
                "unsolicited_count": 0,
                "unsolicited_threshold": 15,
                "window_seconds": 30,
            }
        })
        
        ACE_INSTANCES[1] = mock_instance1
        
        self.mock_instance.serial_mgr.get_connection_status = Mock(return_value={
            "connected": True,
            "stable": True,
            "time_connected": 300.0,
            "recent_reconnects": 0,
            "port": "/dev/ttyACM0",
            "usb_topology": "2-1.1",
            "supervision": {
                "timeout_count": 0,
                "timeout_threshold": 15,
                "unsolicited_count": 0,
                "unsolicited_threshold": 15,
                "window_seconds": 30,
            }
        })
        
        ace.commands.cmd_ACE_GET_CONNECTION_STATUS(self.mock_gcmd)
        
        calls = [str(call) for call in self.mock_gcmd.respond_info.call_args_list]
        output = "\n".join(calls)
        assert "ACE[0]:" in output
        assert "ACE[1]:" in output
        assert "Connected (stable)" in output
        assert "Disconnected" in output

    def test_error_handling(self):
        """Test error handling when status check fails."""
        self.mock_instance.serial_mgr.get_connection_status = Mock(side_effect=Exception("Connection error"))
        
        ace.commands.cmd_ACE_GET_CONNECTION_STATUS(self.mock_gcmd)
        
        # Should catch exception and report error
        calls = [str(call) for call in self.mock_gcmd.respond_info.call_args_list]
        output = "\n".join(calls)
        assert "error" in output.lower()


class TestQuerySlotsCommand:
    """Tests for cmd_ACE_QUERY_SLOTS."""

    def setup_method(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        self.mock_instance = Mock()
        self.mock_instance.instance_num = 0
        self.mock_instance.inventory = [
            {
                "status": "ready",
                "sku": "PLA-001",
                "material": "PLA",
                "color": [255, 0, 0],
                "temp": 210,
                "rfid": True,
                "icon_type": "material_pla",
            },
            {
                "status": "ready",
                "material": "PETG",
                "color": [0, 255, 0],
                "temp": 240,
                "extruder_temp": {"min": 230, "max": 250},
                "hotbed_temp": {"min": 70, "max": 90},
                "diameter": 1.75,
            },
            {
                "status": "empty",
                "color": [0, 0, 0],
                "temp": 0,
            },
            {
                "status": "ready",
                "material": "ABS",
                "color": [0, 0, 255],
                "temp": 250,
                "total": 1000,
                "current": 750,
            },
        ]
        
        ACE_INSTANCES[0] = self.mock_instance
        
        self.mock_gcmd = Mock()
        self.mock_gcmd.respond_info = Mock()

    def teardown_method(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

    def test_query_all_instances_formatting(self):
        """Test formatting of slot information for all instances."""
        self.mock_gcmd.get_command_parameters = Mock(return_value={})
        
        ace.commands.cmd_ACE_QUERY_SLOTS(self.mock_gcmd)
        
        calls = [str(call) for call in self.mock_gcmd.respond_info.call_args_list]
        output = "\n".join(calls)
        
        # Verify header
        assert "ACE Instance 0 Slots" in output
        
        # Verify slot 0 (PLA with all fields) - new format with tool number
        assert "[0] T0" in output
        assert "ready" in output
        assert "sku=PLA-001" in output
        assert "PLA" in output
        assert "RGB(255,0,0)" in output
        assert "210°C" in output
        assert "RFID" in output
        assert "icon_type=material_pla" in output
        
        # Verify slot 1 (PETG with temperature ranges)
        assert "[1] T1" in output
        assert "PETG" in output
        assert "RGB(0,255,0)" in output
        assert "extruder_temp=" in output
        assert "hotbed_temp=" in output
        assert "diameter=1.75" in output
        
        # Verify slot 2 (empty) - now shows -----
        assert "[2] T2" in output
        assert "-----" in output
        
        # Verify slot 3 (ABS with spool info)
        assert "[3] T3" in output
        assert "ABS" in output
        assert "total=1000" in output
        assert "current=750" in output

    def test_query_specific_instance(self):
        """Test querying specific instance by INSTANCE parameter."""
        self.mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0})
        self.mock_gcmd.get_int = Mock(return_value=0)
        
        with patch('ace.commands.ace_get_instance', return_value=self.mock_instance):
            ace.commands.cmd_ACE_QUERY_SLOTS(self.mock_gcmd)
        
        calls = [str(call) for call in self.mock_gcmd.respond_info.call_args_list]
        output = "\n".join(calls)
        assert "ACE Instance 0 Slots" in output
        assert "[0] T0" in output

    def test_query_by_tool_parameter(self):
        """Test querying by TOOL parameter."""
        self.mock_gcmd.get_command_parameters = Mock(return_value={"TOOL": 0})
        
        with patch('ace.commands.ace_get_instance', return_value=self.mock_instance):
            ace.commands.cmd_ACE_QUERY_SLOTS(self.mock_gcmd)
        
        calls = [str(call) for call in self.mock_gcmd.respond_info.call_args_list]
        output = "\n".join(calls)
        assert "ACE Instance 0 Slots" in output

    def test_slot_without_optional_fields(self):
        """Test formatting slot without optional fields (sku, rfid, etc)."""
        self.mock_instance.inventory = [
            {
                "status": "ready",
                "material": "Basic PLA",
                "color": [0, 0, 0],
                "temp": 200,
            }
        ]
        
        self.mock_gcmd.get_command_parameters = Mock(return_value={})
        
        ace.commands.cmd_ACE_QUERY_SLOTS(self.mock_gcmd)
        
        calls = [str(call) for call in self.mock_gcmd.respond_info.call_args_list]
        output = "\n".join(calls)
        
        # Should have basic fields in new format
        assert "[0] T0" in output
        assert "ready" in output
        assert "Basic PLA" in output
        assert "RGB(0,0,0)" in output
        assert "200°C" in output
        
        # Should not have optional fields
        assert "sku=" not in output

    def test_empty_slot_formatting(self):
        """Test formatting of empty slots."""
        self.mock_instance.inventory = [
            {"status": "empty", "color": [0, 0, 0], "temp": 0}
        ]
        
        self.mock_gcmd.get_command_parameters = Mock(return_value={})
        
        ace.commands.cmd_ACE_QUERY_SLOTS(self.mock_gcmd)
        
        calls = [str(call) for call in self.mock_gcmd.respond_info.call_args_list]
        output = "\n".join(calls)
        
        assert "[0] T0" in output
        assert "-----" in output  # Empty slots show as -----
        assert "RGB(0,0,0)" in output
        assert "0°C" in output

    def test_multiple_instances_query(self):
        """Test querying multiple instances."""
        # Add second instance
        mock_instance1 = Mock()
        mock_instance1.instance_num = 1
        mock_instance1.inventory = [
            {"status": "ready", "material": "TPU", "color": [200, 100, 50], "temp": 230}
        ]
        
        ACE_INSTANCES[1] = mock_instance1
        
        self.mock_gcmd.get_command_parameters = Mock(return_value={})
        
        ace.commands.cmd_ACE_QUERY_SLOTS(self.mock_gcmd)
        
        calls = [str(call) for call in self.mock_gcmd.respond_info.call_args_list]
        output = "\n".join(calls)
        
        # Should show both instances
        assert "ACE Instance 0 Slots" in output
        assert "ACE Instance 1 Slots" in output
        assert "PLA" in output  # From instance 0
        assert "TPU" in output  # From instance 1

    def test_slot_with_rgba_field(self):
        """Test formatting slot - rgba field removed, only RGB color remains."""
        self.mock_instance.inventory = [
            {
                "status": "ready",
                "material": "ColorMix",
                "color": [255, 255, 0],
                "temp": 220,
                # rgba field removed - now only color (RGB) is used
            }
        ]
        
        self.mock_gcmd.get_command_parameters = Mock(return_value={})
        
        ace.commands.cmd_ACE_QUERY_SLOTS(self.mock_gcmd)
        
        calls = [str(call) for call in self.mock_gcmd.respond_info.call_args_list]
        output = "\n".join(calls)
        
        # Verify color is shown in RGB format (new display format)
        assert "[0] T0" in output
        assert "RGB(255,255,0)" in output
        assert "ColorMix" in output
        # Verify rgba field is NOT in output (removed)
        assert "rgba=" not in output
