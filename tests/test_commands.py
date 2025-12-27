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

    def test_cmd_ACE_SET_SLOT_emits_inventory_notification(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT emits inventory notification for KlipperScreen."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 0, 210])
        mock_gcmd.get = Mock(side_effect=["RED", "PLA"])
        
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        
        # Verify respond_info was called at least twice (status + notification)
        assert mock_gcmd.respond_info.call_count >= 2
        
        # Get all respond_info calls
        calls = [str(call) for call in mock_gcmd.respond_info.call_args_list]
        
        # Verify one call contains JSON notification with instance and slots
        notification_found = False
        for call in calls:
            if '// {"instance":' in call and '"slots":' in call:
                notification_found = True
                break
        
        assert notification_found, f"Inventory notification not found in calls: {calls}"

    def test_cmd_ACE_SET_SLOT_empty_emits_notification(self, mock_gcmd, setup_mocks):
        """Test ACE_SET_SLOT with EMPTY=1 also emits inventory notification."""
        mock_gcmd.get_command_parameters = Mock(return_value={"INSTANCE": 0, "INDEX": 0})
        mock_gcmd.get_int = Mock(side_effect=[0, 0, 1])
        
        ace.commands.cmd_ACE_SET_SLOT(mock_gcmd)
        
        # Verify respond_info was called at least twice
        assert mock_gcmd.respond_info.call_count >= 2
        
        # Get all respond_info calls
        calls = [str(call) for call in mock_gcmd.respond_info.call_args_list]
        
        # Verify notification is emitted
        notification_found = False
        for call in calls:
            if '// {"instance":' in call and '"slots":' in call:
                notification_found = True
                break
        
        assert notification_found, f"Inventory notification not found in calls: {calls}"

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
        """Test ACE_HANDLE_PRINT_END."""
        with patch('ace.commands.get_variable', return_value=0):
            ace.commands.cmd_ACE_HANDLE_PRINT_END(mock_gcmd)
            assert INSTANCE_MANAGERS[0].smart_unload.called

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
        """Test ACE_DEBUG_SENSORS."""
        ace.commands.cmd_ACE_DEBUG_SENSORS(mock_gcmd)
        assert mock_gcmd.respond_info.called

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
