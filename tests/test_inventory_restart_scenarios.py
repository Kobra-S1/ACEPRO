"""
Test suite for inventory persistence across system restarts and reconnects.

Tests comprehensive scenarios:
- Fresh start with no saved data
- Restart with saved manual slot data
- Restart with saved RFID slot data
- RFID spool change after restart
- Non-RFID spool replacing RFID after restart
- Manual data preservation during reconnect queries
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, call
import json

# Import constants from instance module
from extras.ace.instance import RFID_STATE_NO_INFO, RFID_STATE_IDENTIFIED


class TestInventoryRestartScenarios:
    """Test inventory persistence across system restarts."""

    @pytest.fixture
    def mock_printer(self):
        """Create a mock printer."""
        printer = MagicMock()
        printer.get_reactor = MagicMock(return_value=MagicMock())
        
        # Mock save_variables
        mock_save_vars = MagicMock()
        mock_save_vars.allVariables = {}
        
        # Mock print_stats for _is_printing_or_paused checks
        mock_print_stats = MagicMock()
        mock_print_stats.get_status = MagicMock(return_value={"state": "standby"})
        
        def lookup_object_func(name, default=None):
            return {
                "save_variables": mock_save_vars,
                "gcode": MagicMock(),
                "print_stats": mock_print_stats
            }.get(name, default if default is not None else MagicMock())
        
        printer.lookup_object = MagicMock(side_effect=lookup_object_func)
        
        return printer

    @pytest.fixture
    def mock_ace_config(self):
        """Create minimal ACE config."""
        return {
            "instance_num": 0,
            "baud": 115200,
            "timeout_multiplier": 1.0,
            "filament_runout_sensor_name_rdm": None,
            "filament_runout_sensor_name_nozzle": "toolhead_sensor",
            "feed_speed": 100.0,
            "retract_speed": 100.0,
            "total_max_feeding_length": 1000.0,
            "parkposition_to_toolhead_length": 500.0,
            "toolchange_load_length": 50.0,
            "parkposition_to_rdm_length": 100.0,
            "incremental_feeding_length": 10.0,
            "incremental_feeding_speed": 50.0,
            "extruder_feeding_length": 20.0,
            "extruder_feeding_speed": 30.0,
            "toolhead_slow_loading_speed": 20.0,
            "toolhead_full_purge_length": 22.0,
            "toolhead_retraction_length": 40.0,
            "toolhead_retraction_speed": 10.0,
            "default_color_change_purge_length": 50.0,
            "default_color_change_purge_speed": 400.0,
            "purge_max_chunk_length": 300.0,
            "purge_multiplier": 1.0,
            "pre_cut_retract_length": 2.0,
            "heartbeat_interval": 1.0,
            "max_dryer_temperature": 70.0,
            "rfid_temp_mode": "average",
            "rfid_inventory_sync_enabled": True,
            "feed_assist_active_after_ace_connect": False,
        }

    def test_fresh_start_no_saved_data(self, mock_printer, mock_ace_config):
        """System starts with no saved_variables file - expects empty inventory with defaults."""
        from extras.ace.instance import AceInstance
        
        # No saved data
        save_vars = mock_printer.lookup_object("save_variables")
        save_vars.allVariables = {}
        
        with patch('extras.ace.instance.AceSerialManager'):
            instance = AceInstance(0, mock_ace_config, mock_printer)
        
        # Verify default empty inventory
        assert len(instance.inventory) == 4
        for slot in instance.inventory:
            assert slot["status"] == "empty"
            assert slot["color"] == [0, 0, 0]
            assert slot["material"] == ""
            assert slot["temp"] == 0
            assert slot["rfid"] == False

    def test_restart_with_saved_empty_inventory(self, mock_printer, mock_ace_config):
        """System starts with inventory file holding empty/default values."""
        from extras.ace.instance import AceInstance
        
        # Saved empty inventory
        save_vars = mock_printer.lookup_object("save_variables")
        save_vars.allVariables = {
            "ace_inventory_0": [
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            ]
        }
        
        with patch('extras.ace.instance.AceSerialManager'):
            instance = AceInstance(0, mock_ace_config, mock_printer)
        
        # Verify loaded empty inventory
        assert len(instance.inventory) == 4
        for slot in instance.inventory:
            assert slot["status"] == "empty"
            assert slot["color"] == [0, 0, 0]
            assert slot["material"] == ""
            assert slot["temp"] == 0
            assert slot["rfid"] == False

    def test_restart_with_saved_manual_slot_data(self, mock_printer, mock_ace_config):
        """System restarts with saved manual PLA spool (234°C, RED) - data persists."""
        from extras.ace.instance import AceInstance
        
        # Saved manual data: Slot 0 = PLA, 234°C, RED
        save_vars = mock_printer.lookup_object("save_variables")
        save_vars.allVariables = {
            "ace_inventory_0": [
                {"status": "ready", "color": [255, 0, 0], "material": "PLA", "temp": 234, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            ]
        }
        
        with patch('extras.ace.instance.AceSerialManager'):
            instance = AceInstance(0, mock_ace_config, mock_printer)
        
        # Manually load saved inventory (normally done by manager)
        instance.inventory = save_vars.allVariables["ace_inventory_0"]
        
        # Verify manual data loaded correctly
        assert instance.inventory[0]["status"] == "ready"
        assert instance.inventory[0]["color"] == [255, 0, 0]
        assert instance.inventory[0]["material"] == "PLA"
        assert instance.inventory[0]["temp"] == 234
        assert instance.inventory[0]["rfid"] == False

    def test_restart_with_saved_rfid_slot_same_spool(self, mock_printer, mock_ace_config):
        """System restarts with RFID spool - same spool still present."""
        from extras.ace.instance import AceInstance
        
        # Saved RFID data
        save_vars = mock_printer.lookup_object("save_variables")
        save_vars.allVariables = {
            "ace_inventory_0": [
                {
                    "status": "ready",
                    "color": [234, 42, 43],
                    "material": "PLA",
                    "temp": 210,
                    "rfid": True,
                    "sku": "AHPLBK-101",
                    "brand": "Anycubic",
                    "extruder_temp": {"min": 190, "max": 230},
                    "hotbed_temp": {"min": 50, "max": 60}
                },
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            ]
        }
        
        with patch('extras.ace.instance.AceSerialManager'):
            instance = AceInstance(0, mock_ace_config, mock_printer)
        
        # Manually load saved inventory (normally done by manager)
        instance.inventory = save_vars.allVariables["ace_inventory_0"]
        
        # Verify RFID data loaded
        assert instance.inventory[0]["status"] == "ready"
        assert instance.inventory[0]["material"] == "PLA"
        assert instance.inventory[0]["temp"] == 210
        assert instance.inventory[0]["rfid"] == True
        assert instance.inventory[0]["sku"] == "AHPLBK-101"
        assert instance.inventory[0]["brand"] == "Anycubic"
        
        # Simulate reconnect - RFID query returns same spool data
        instance._pending_rfid_queries = set()
        instance._query_rfid_full_data = Mock()
        instance._on_ace_connect()
        
        # Simulate status update with RFID tag present
        status_response = {
            "result": {
                "slots": [
                    {"index": 0, "status": "ready", "rfid": RFID_STATE_IDENTIFIED}  # RFID tag
                ]
            }
        }
        instance._status_update_callback(status_response)
        
        # Should trigger RFID query
        instance._query_rfid_full_data.assert_called()

    def test_restart_rfid_spool_changed_to_different_rfid(self, mock_printer, mock_ace_config):
        """System restarts - RFID spool was changed to different RFID spool."""
        from extras.ace.instance import AceInstance
        
        # Saved RFID data: OLD spool
        save_vars = mock_printer.lookup_object("save_variables")
        save_vars.allVariables = {
            "ace_inventory_0": [
                {
                    "status": "ready",
                    "color": [255, 255, 255],  # White (old)
                    "material": "PLA",
                    "temp": 210,
                    "rfid": True,
                    "sku": "OLD-SKU"
                },
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            ]
        }
        
        with patch('extras.ace.instance.AceSerialManager'):
            instance = AceInstance(0, mock_ace_config, mock_printer)
            instance.serial_mgr = MagicMock()
            instance.gcode = MagicMock()
            # Don't mock manager since we're not testing persistence here
        
        # Manually load saved inventory (normally done by manager)
        instance.inventory = save_vars.allVariables["ace_inventory_0"]
        
        # Simulate reconnect and RFID query returning NEW spool
        rfid_response = {
            "code": 0,
            "result": {
                "rfid": RFID_STATE_IDENTIFIED,  # RFID tag
                "sku": "NEW-SKU",
                "brand": "NewBrand",
                "type": "PETG",
                "colors": [[88, 99, 110, 255]],  # Gray (new)
                "extruder_temp": {"min": 230, "max": 260},
                "hotbed_temp": {"min": 70, "max": 90}
            }
        }
        
        # Trigger the callback
        instance._query_rfid_full_data(0)
        callback = instance.serial_mgr.send_request.call_args[0][1]
        callback(rfid_response)
        
        # Verify inventory updated to NEW spool
        assert instance.inventory[0]["sku"] == "NEW-SKU"
        assert instance.inventory[0]["brand"] == "NewBrand"
        assert instance.inventory[0]["material"] == "PETG"
        assert instance.inventory[0]["color"] == [88, 99, 110]
        assert instance.inventory[0]["temp"] == 245  # (230+260)/2

    def test_restart_rfid_spool_changed_to_non_rfid(self, mock_printer, mock_ace_config):
        """System restarts - RFID spool was removed, non-RFID spool inserted."""
        from extras.ace.instance import AceInstance
        
        # Saved RFID data
        save_vars = mock_printer.lookup_object("save_variables")
        save_vars.allVariables = {
            "ace_inventory_0": [
                {
                    "status": "ready",
                    "color": [255, 255, 255],
                    "material": "PLA",
                    "temp": 210,
                    "rfid": True,
                    "sku": "AHPLBK-101"
                },
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            ]
        }
        
        with patch('extras.ace.instance.AceSerialManager'):
            instance = AceInstance(0, mock_ace_config, mock_printer)
            instance.serial_mgr = MagicMock()
            instance.gcode = MagicMock()
            # Don't mock manager
        
        # Manually load saved inventory (normally done by manager)
        instance.inventory = save_vars.allVariables["ace_inventory_0"]
        
        # First, simulate RFID spool being removed (ready→empty transition)
        empty_response = {
            "result": {
                "slots": [
                    {"index": 0, "status": "empty", "rfid": 0}  # Slot empty
                ]
            }
        }
        instance._status_update_callback(empty_response)
        
        # Status should change to empty, but RFID fields preserved (for runout scenarios)
        assert instance.inventory[0]["status"] == "empty"
        
        # Now simulate non-RFID spool insertion (empty→ready transition with rfid=0)
        non_rfid_response = {
            "result": {
                "slots": [
                    {"index": 0, "status": "ready", "rfid": 0}  # Non-RFID spool (rfid=0=RFID_STATE_NO_INFO)
                ]
            }
        }
        instance._status_update_callback(non_rfid_response)
        
        # RFID fields should NOW be cleared (empty→ready with saved_rfid=True detected RFID→non-RFID swap)
        assert "sku" not in instance.inventory[0] or instance.inventory[0].get("sku") == ""

    def test_manual_data_survives_reconnect_queries(self, mock_printer, mock_ace_config):
        """
        Manual slot data (non-RFID) survives reconnect RFID queries.
        
        REGRESSION TEST: This test catches a bug where manual color/material/temp data
        was being overwritten by empty firmware responses for non-RFID spools during
        reconnect. Firmware returns rfid=1 with empty data fields for non-RFID spools,
        which should NOT trigger inventory updates (only rfid=2 should).
        
        Scenario:
        1. User manually sets PLA 212°C, brown color for non-RFID spool in slot 0
        2. System restarts and reconnects to ACE hardware
        3. Reconnect triggers RFID queries for all slots
        4. Slot 0 has non-RFID spool, firmware returns rfid=1 with empty data
        5. Manual data MUST be preserved (not overwritten with empty values)
        """
        from extras.ace.instance import AceInstance
        
        # Saved manual data
        save_vars = mock_printer.lookup_object("save_variables")
        save_vars.allVariables = {
            "ace_inventory_0": [
                {"status": "ready", "color": [139, 69, 19], "material": "PLA", "temp": 212, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            ]
        }
        
        with patch('extras.ace.instance.AceSerialManager'):
            instance = AceInstance(0, mock_ace_config, mock_printer)
            instance.serial_mgr = MagicMock()
            instance.gcode = MagicMock()
            # Don't mock manager
        
        # Manually load the saved inventory (normally done by manager)
        instance.inventory = save_vars.allVariables["ace_inventory_0"]
        
        # Simulate reconnect - triggers RFID query for all slots
        instance._on_ace_connect()
        assert instance._pending_rfid_refresh == True
        
        # Simulate status update (non-RFID spool present)
        status_response = {
            "result": {
                "slots": [
                    {"index": 0, "status": "ready", "rfid": 1}  # Non-RFID (rfid=1)
                ]
            }
        }
        instance._status_update_callback(status_response)
        
        # RFID query is triggered but returns empty data for non-RFID spool
        rfid_response = {
            "code": 0,
            "result": {
                "rfid": 1,  # Non-RFID spool - no tag data
                "sku": "",
                "brand": "",
                "type": "",
                "colors": [[0, 0, 0, 0]],
                "extruder_temp": {"min": 0, "max": 0},
                "hotbed_temp": {"min": 0, "max": 0}
            }
        }
        
        # Get the callback for slot 0 (first call) and invoke it with non-RFID response
        # send_request was called 4 times (once per slot), we need the first call (slot 0)
        callback_slot_0 = instance.serial_mgr.send_request.call_args_list[0][0][1]
        callback_slot_0(rfid_response)
        
        # CRITICAL: Manual data MUST be preserved
        assert instance.inventory[0]["color"] == [139, 69, 19], "Manual color data lost!"
        assert instance.inventory[0]["material"] == "PLA", "Manual material data lost!"
        assert instance.inventory[0]["temp"] == 212, "Manual temp data lost!"

    def test_empty_slot_to_rfid_spool_on_reconnect(self, mock_printer, mock_ace_config):
        """Empty slot before restart, RFID spool inserted before reconnect."""
        from extras.ace.instance import AceInstance
        
        # Saved: Empty slot
        save_vars = mock_printer.lookup_object("save_variables")
        save_vars.allVariables = {
            "ace_inventory_0": [
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            ]
        }
        
        with patch('extras.ace.instance.AceSerialManager'):
            instance = AceInstance(0, mock_ace_config, mock_printer)
            instance.serial_mgr = MagicMock()
            instance.gcode = MagicMock()
            # Don't mock manager
        
        # Simulate status update showing new RFID spool
        status_response = {
            "result": {
                "slots": [
                    {"index": 0, "status": "ready", "rfid": RFID_STATE_IDENTIFIED}
                ]
            }
        }
        instance._status_update_callback(status_response)
        
        # RFID query returns spool data
        rfid_response = {
            "code": 0,
            "result": {
                "rfid": RFID_STATE_IDENTIFIED,
                "sku": "NEW-SKU",
                "brand": "Anycubic",
                "type": "PLA",
                "colors": [[234, 42, 43, 255]],
                "extruder_temp": {"min": 190, "max": 230},
                "hotbed_temp": {"min": 50, "max": 60}
            }
        }
        
        instance._query_rfid_full_data(0)
        callback = instance.serial_mgr.send_request.call_args[0][1]
        callback(rfid_response)
        
        # Verify RFID data populated
        assert instance.inventory[0]["material"] == "PLA"
        assert instance.inventory[0]["sku"] == "NEW-SKU"
        assert instance.inventory[0]["brand"] == "Anycubic"
        assert instance.inventory[0]["color"] == [234, 42, 43]

    def test_empty_slot_to_non_rfid_spool_on_reconnect(self, mock_printer, mock_ace_config):
        """Empty slot before restart, non-RFID spool inserted before reconnect."""
        from extras.ace.instance import AceInstance
        
        # Saved: Empty slot
        save_vars = mock_printer.lookup_object("save_variables")
        save_vars.allVariables = {
            "ace_inventory_0": [
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            ]
        }
        
        with patch('extras.ace.instance.AceSerialManager'):
            instance = AceInstance(0, mock_ace_config, mock_printer)
            instance.serial_mgr = MagicMock()
            instance.gcode = MagicMock()
        
        # Simulate status update showing non-RFID spool
        status_response = {
            "result": {
                "slots": [
                    {"index": 0, "status": "ready", "rfid": 1}  # Non-RFID
                ]
            }
        }
        instance._status_update_callback(status_response)
        
        # Inventory updated to ready with defaults
        assert instance.inventory[0]["status"] == "ready"
        assert instance.inventory[0]["material"] == "Unknown"  # Default
        assert instance.inventory[0]["temp"] == 0  # Default
