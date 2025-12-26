"""
Tests for AceManager.set_and_save_variable() method.

Verifies:
1. In-memory dict update (self.variables)
2. save_vars.allVariables update
3. Proper SAVE_VARIABLE command formatting for strings vs non-strings
4. Reference synchronization between self.variables and allVariables
"""

import pytest
from unittest.mock import Mock, MagicMock, call
from ace.manager import AceManager
from ace.config import FILAMENT_STATE_SPLITTER, FILAMENT_STATE_NOZZLE


class TestSetAndSaveVariable:
    """Test set_and_save_variable functionality."""

    @pytest.fixture
    def mock_gcode(self):
        """Create mock gcode object."""
        gcode = Mock()
        gcode.register_command = Mock()
        gcode.run_script_from_command = Mock()
        gcode.respond_info = Mock()
        return gcode

    @pytest.fixture
    def manager(self, mock_gcode, mock_config):
        """Create AceManager instance for testing."""
        # Create a real dict for allVariables (not a Mock)
        all_variables = {
            "ace_filament_pos": FILAMENT_STATE_SPLITTER,
            "ace_current_index": -1,
            "ace_global_enabled": True
        }
        
        save_vars = Mock()
        save_vars.allVariables = all_variables
        
        # Get the printer from mock_config (created in conftest.py)
        mock_printer = mock_config.get_printer()
        mock_printer.lookup_object = Mock(side_effect=lambda name, default=None: {
            "gcode": mock_gcode,
            "save_variables": save_vars,
            "output_pin ACE_Pro": Mock(get_status=Mock(return_value={'value': 1}))
        }.get(name, default))
        
        manager = AceManager(mock_config, dummy_ace_count=1)
        manager.gcode = mock_gcode
        manager.printer = mock_printer
        
        return manager

    def test_set_string_variable(self, manager, mock_gcode):
        """Test setting a string variable."""
        # Set a string value
        manager.set_and_save_variable("ace_filament_pos", FILAMENT_STATE_NOZZLE)
        
        # Verify in-memory update
        assert manager.variables["ace_filament_pos"] == FILAMENT_STATE_NOZZLE
        
        # Verify SAVE_VARIABLE command with proper string quoting
        expected_cmd = f'SAVE_VARIABLE VARIABLE=ace_filament_pos VALUE=\'"{FILAMENT_STATE_NOZZLE}"\''
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_set_integer_variable(self, manager, mock_gcode):
        """Test setting an integer variable."""
        # Set an integer value
        manager.set_and_save_variable("ace_current_index", 0)
        
        # Verify in-memory update
        assert manager.variables["ace_current_index"] == 0
        
        # Verify SAVE_VARIABLE command with json.dumps for non-string
        expected_cmd = 'SAVE_VARIABLE VARIABLE=ace_current_index VALUE=0'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_set_dict_variable(self, manager, mock_gcode):
        """Test setting a dict variable."""
        test_dict = {"slot": 0, "status": "ready", "temp": 220}
        
        # Set a dict value
        manager.set_and_save_variable("ace_inventory_0", test_dict)
        
        # Verify in-memory update
        assert manager.variables["ace_inventory_0"] == test_dict
        
        # Verify SAVE_VARIABLE command with json.dumps
        import json
        expected_value = json.dumps(test_dict)
        expected_cmd = f'SAVE_VARIABLE VARIABLE=ace_inventory_0 VALUE={expected_value}'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_variables_reference_update(self, manager):
        """Test that self.variables reference is updated after save."""
        # Get initial reference
        save_vars = manager.printer.lookup_object("save_variables")
        initial_all_vars = save_vars.allVariables
        
        # Set a variable
        manager.set_and_save_variable("ace_filament_pos", "toolhead")
        
        # Verify self.variables points to updated allVariables
        assert manager.variables is save_vars.allVariables
        assert manager.variables["ace_filament_pos"] == "toolhead"

    def test_multiple_updates_maintain_sync(self, manager):
        """Test multiple updates keep self.variables in sync."""
        # Update multiple variables
        manager.set_and_save_variable("ace_filament_pos", FILAMENT_STATE_NOZZLE)
        assert manager.variables["ace_filament_pos"] == FILAMENT_STATE_NOZZLE
        
        manager.set_and_save_variable("ace_current_index", 2)
        assert manager.variables["ace_current_index"] == 2
        
        manager.set_and_save_variable("ace_filament_pos", FILAMENT_STATE_SPLITTER)
        assert manager.variables["ace_filament_pos"] == FILAMENT_STATE_SPLITTER
        
        # All updates should be visible in self.variables
        assert manager.variables["ace_current_index"] == 2
        assert manager.variables["ace_filament_pos"] == FILAMENT_STATE_SPLITTER

    def test_special_characters_in_string_value(self, manager, mock_gcode):
        """Test setting string with special characters."""
        special_value = "test\"value'with\\quotes"
        
        manager.set_and_save_variable("test_var", special_value)
        
        # Verify value stored correctly
        assert manager.variables["test_var"] == special_value
        
        # Verify command was called (escaping handled by Klipper)
        expected_cmd = f'SAVE_VARIABLE VARIABLE=test_var VALUE=\'"{special_value}"\''
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_boolean_variable(self, manager, mock_gcode):
        """Test setting boolean variable."""
        manager.set_and_save_variable("ace_global_enabled", True)
        
        # Verify in-memory update
        assert manager.variables["ace_global_enabled"] is True
        
        # Verify SAVE_VARIABLE command uses Python bool (True/False)
        expected_cmd = 'SAVE_VARIABLE VARIABLE=ace_global_enabled VALUE=True'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_list_variable(self, manager, mock_gcode):
        """Test setting list variable."""
        test_list = [1, 2, 3, "test"]
        
        manager.set_and_save_variable("test_list", test_list)
        
        # Verify in-memory update
        assert manager.variables["test_list"] == test_list
        
        # Verify SAVE_VARIABLE command uses json.dumps
        import json
        expected_value = json.dumps(test_list)
        expected_cmd = f'SAVE_VARIABLE VARIABLE=test_list VALUE={expected_value}'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_overwrite_existing_variable(self, manager):
        """Test overwriting an existing variable value."""
        # Set initial value
        manager.set_and_save_variable("ace_current_index", 0)
        assert manager.variables["ace_current_index"] == 0
        
        # Overwrite with new value
        manager.set_and_save_variable("ace_current_index", 3)
        assert manager.variables["ace_current_index"] == 3
        
        # Verify old value is gone
        assert manager.variables["ace_current_index"] != 0

    def test_monitor_sees_updated_value(self, manager):
        """
        Test that monitor can immediately see updated values after set_and_save_variable.
        
        This simulates the runout monitor reading ace_current_index after a toolchange.
        """
        # Initial state
        manager.variables["ace_current_index"] = -1
        
        # Simulate toolchange setting current tool
        manager.set_and_save_variable("ace_current_index", 0)
        
        # Monitor should see updated value immediately
        current_tool = manager.variables.get("ace_current_index", -1)
        assert current_tool == 0
        assert current_tool != -1

    def test_empty_string_value(self, manager, mock_gcode):
        """Test setting empty string."""
        manager.set_and_save_variable("test_var", "")
        
        assert manager.variables["test_var"] == ""
        
        expected_cmd = 'SAVE_VARIABLE VARIABLE=test_var VALUE=\'""\''  # Empty string becomes double quotes
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_none_value(self, manager, mock_gcode):
        """Test setting None value."""
        manager.set_and_save_variable("test_var", None)
        
        assert manager.variables["test_var"] is None
        
        # None becomes the string "None" (not JSON null)
        expected_cmd = 'SAVE_VARIABLE VARIABLE=test_var VALUE=None'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_zero_value(self, manager, mock_gcode):
        """Test setting zero as integer."""
        manager.set_and_save_variable("ace_current_index", 0)
        
        assert manager.variables["ace_current_index"] == 0
        
        expected_cmd = 'SAVE_VARIABLE VARIABLE=ace_current_index VALUE=0'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_negative_value(self, manager, mock_gcode):
        """Test setting negative integer."""
        manager.set_and_save_variable("ace_current_index", -1)
        
        assert manager.variables["ace_current_index"] == -1
        
        expected_cmd = 'SAVE_VARIABLE VARIABLE=ace_current_index VALUE=-1'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_float_value(self, manager, mock_gcode):
        """Test setting float value."""
        manager.set_and_save_variable("test_float", 3.14159)
        
        assert manager.variables["test_float"] == 3.14159
        
        import json
        expected_value = json.dumps(3.14159)
        expected_cmd = f'SAVE_VARIABLE VARIABLE=test_float VALUE={expected_value}'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])