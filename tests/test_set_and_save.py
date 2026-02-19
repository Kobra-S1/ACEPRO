"""
Tests for AceManager.set_and_save_variable() method.

Verifies:
1. In-memory dict update (self.variables)
2. save_vars.allVariables update
3. Proper SAVE_VARIABLE command formatting on flush
4. Reference synchronization between self.variables and allVariables
5. Deferred-flush dirty-flag semantics
"""

import pytest
from unittest.mock import Mock
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

    # ------------------------------------------------------------------
    # In-memory update tests (no flush needed)
    # ------------------------------------------------------------------

    def test_set_string_variable_in_memory(self, manager):
        """set_and_save updates RAM immediately -- no flush needed for reads."""
        manager.set_and_save_variable("ace_filament_pos", FILAMENT_STATE_NOZZLE)
        assert manager.variables["ace_filament_pos"] == FILAMENT_STATE_NOZZLE

    def test_set_integer_variable_in_memory(self, manager):
        manager.set_and_save_variable("ace_current_index", 0)
        assert manager.variables["ace_current_index"] == 0

    def test_variables_reference_update(self, manager):
        """Test that self.variables reference is updated after save."""
        save_vars = manager.printer.lookup_object("save_variables")
        manager.set_and_save_variable("ace_filament_pos", "toolhead")
        assert manager.variables is save_vars.allVariables
        assert manager.variables["ace_filament_pos"] == "toolhead"

    def test_multiple_updates_maintain_sync(self, manager):
        """Test multiple updates keep self.variables in sync."""
        manager.set_and_save_variable("ace_filament_pos", FILAMENT_STATE_NOZZLE)
        assert manager.variables["ace_filament_pos"] == FILAMENT_STATE_NOZZLE

        manager.set_and_save_variable("ace_current_index", 2)
        assert manager.variables["ace_current_index"] == 2

        manager.set_and_save_variable("ace_filament_pos", FILAMENT_STATE_SPLITTER)
        assert manager.variables["ace_filament_pos"] == FILAMENT_STATE_SPLITTER

        assert manager.variables["ace_current_index"] == 2
        assert manager.variables["ace_filament_pos"] == FILAMENT_STATE_SPLITTER

    def test_overwrite_existing_variable(self, manager):
        """Test overwriting an existing variable value."""
        manager.set_and_save_variable("ace_current_index", 0)
        assert manager.variables["ace_current_index"] == 0

        manager.set_and_save_variable("ace_current_index", 3)
        assert manager.variables["ace_current_index"] == 3
        assert manager.variables["ace_current_index"] != 0

    def test_monitor_sees_updated_value(self, manager):
        """Monitor sees RAM update immediately -- no flush needed."""
        manager.variables["ace_current_index"] = -1
        manager.set_and_save_variable("ace_current_index", 0)

        current_tool = manager.variables.get("ace_current_index", -1)
        assert current_tool == 0
        assert current_tool != -1

    # ------------------------------------------------------------------
    # Flush -> disk-write formatting tests
    # ------------------------------------------------------------------

    def test_flush_string_variable(self, manager, mock_gcode):
        """String values are single-quote + double-quote wrapped on flush."""
        manager.set_and_save_variable("ace_filament_pos", FILAMENT_STATE_NOZZLE)
        # Not yet written to disk
        mock_gcode.run_script_from_command.assert_not_called()

        manager.state.flush()

        expected_cmd = f'SAVE_VARIABLE VARIABLE=ace_filament_pos VALUE=\'"{FILAMENT_STATE_NOZZLE}"\''
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_flush_integer_variable(self, manager, mock_gcode):
        manager.set_and_save_variable("ace_current_index", 0)
        manager.state.flush()

        expected_cmd = 'SAVE_VARIABLE VARIABLE=ace_current_index VALUE=0'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_flush_dict_variable(self, manager, mock_gcode):
        test_dict = {"slot": 0, "status": "ready", "temp": 220}
        manager.set_and_save_variable("ace_inventory_0", test_dict)
        manager.state.flush()

        import json
        expected_payload = (
            json.dumps(test_dict)
            .replace("true", "True")
            .replace("false", "False")
            .replace("null", "None")
        )
        expected_cmd = f"SAVE_VARIABLE VARIABLE=ace_inventory_0 VALUE='{expected_payload}'"
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_flush_special_characters_in_string(self, manager, mock_gcode):
        special_value = "test\"value'with\\quotes"
        manager.set_and_save_variable("test_var", special_value)
        assert manager.variables["test_var"] == special_value

        manager.state.flush()

        expected_cmd = f'SAVE_VARIABLE VARIABLE=test_var VALUE=\'"{special_value}"\''
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_flush_boolean_variable(self, manager, mock_gcode):
        manager.set_and_save_variable("ace_global_enabled", True)
        assert manager.variables["ace_global_enabled"] is True

        manager.state.flush()

        expected_cmd = 'SAVE_VARIABLE VARIABLE=ace_global_enabled VALUE=True'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_flush_list_variable(self, manager, mock_gcode):
        test_list = [1, 2, 3, "test"]
        manager.set_and_save_variable("test_list", test_list)
        assert manager.variables["test_list"] == test_list

        manager.state.flush()

        import json
        expected_payload = (
            json.dumps(test_list)
            .replace("true", "True")
            .replace("false", "False")
            .replace("null", "None")
        )
        expected_cmd = f"SAVE_VARIABLE VARIABLE=test_list VALUE='{expected_payload}'"
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_flush_empty_string(self, manager, mock_gcode):
        manager.set_and_save_variable("test_var", "")
        assert manager.variables["test_var"] == ""

        manager.state.flush()

        expected_cmd = 'SAVE_VARIABLE VARIABLE=test_var VALUE=\'""\''
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_flush_none_value(self, manager, mock_gcode):
        manager.set_and_save_variable("test_var", None)
        assert manager.variables["test_var"] is None

        manager.state.flush()

        expected_cmd = 'SAVE_VARIABLE VARIABLE=test_var VALUE=None'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_flush_zero_value(self, manager, mock_gcode):
        manager.set_and_save_variable("ace_current_index", 0)
        assert manager.variables["ace_current_index"] == 0

        manager.state.flush()

        expected_cmd = 'SAVE_VARIABLE VARIABLE=ace_current_index VALUE=0'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_flush_negative_value(self, manager, mock_gcode):
        manager.set_and_save_variable("ace_current_index", -1)
        assert manager.variables["ace_current_index"] == -1

        manager.state.flush()

        expected_cmd = 'SAVE_VARIABLE VARIABLE=ace_current_index VALUE=-1'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    def test_flush_float_value(self, manager, mock_gcode):
        manager.set_and_save_variable("test_float", 3.14159)
        assert manager.variables["test_float"] == 3.14159

        manager.state.flush()

        import json
        expected_value = json.dumps(3.14159)
        expected_cmd = f'SAVE_VARIABLE VARIABLE=test_float VALUE={expected_value}'
        mock_gcode.run_script_from_command.assert_called_with(expected_cmd)

    # ------------------------------------------------------------------
    # Deferred-flush dirty-flag tests
    # ------------------------------------------------------------------

    def test_set_and_save_marks_dirty(self, manager):
        """set_and_save marks the variable dirty."""
        assert not manager.state.has_pending
        manager.set_and_save_variable("ace_current_index", 5)
        assert manager.state.has_pending
        assert "ace_current_index" in manager.state._dirty

    def test_set_does_mark_dirty(self, manager):
        """Plain set() marks dirty (deferred flush)."""
        manager.state.set("ace_current_index", 5)
        assert manager.state.has_pending

    def test_flush_clears_dirty(self, manager, mock_gcode):
        """After flush the dirty set is empty."""
        manager.set_and_save_variable("ace_current_index", 5)
        assert manager.state.has_pending
        manager.state.flush()
        assert not manager.state.has_pending

    def test_flush_no_op_when_clean(self, manager, mock_gcode):
        """Flush with nothing dirty is a no-op -- no gcode calls."""
        manager.state.flush()
        mock_gcode.run_script_from_command.assert_not_called()

    def test_duplicate_set_and_save_flushes_once(self, manager, mock_gcode):
        """Multiple writes to same variable produce only one flush write."""
        manager.set_and_save_variable("ace_current_index", 1)
        manager.set_and_save_variable("ace_current_index", 2)
        manager.set_and_save_variable("ace_current_index", 3)
        manager.state.flush()

        # Only the latest value (3) should be flushed
        assert manager.variables["ace_current_index"] == 3
        mock_gcode.run_script_from_command.assert_called_once_with(
            'SAVE_VARIABLE VARIABLE=ace_current_index VALUE=3'
        )

    def test_multiple_variables_flush_all(self, manager, mock_gcode):
        """Flush writes all dirty variables."""
        manager.set_and_save_variable("ace_current_index", 2)
        manager.set_and_save_variable("ace_filament_pos", "nozzle")
        manager.state.flush()

        assert mock_gcode.run_script_from_command.call_count == 2

    def test_set_and_save_defers_gcode(self, manager, mock_gcode):
        """set_and_save does NOT issue gcode immediately."""
        manager.set_and_save_variable("ace_current_index", 42)
        mock_gcode.run_script_from_command.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
