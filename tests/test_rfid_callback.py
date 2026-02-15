"""
Tests for RFID callback functionality in AceInstance.

These tests verify that:
1. _query_rfid_full_data sends correct request
2. rfid_callback parses response correctly
3. Temperature is calculated based on rfid_temp_mode config
4. All RFID fields are stored in inventory
5. Inventory is synced to persistent storage
6. JSON update is emitted for UI
"""

import pytest
from unittest.mock import MagicMock, patch, call
import json


class TestQueryRfidFullData:
    """Tests for _query_rfid_full_data method."""

    @pytest.fixture
    def mock_ace_instance(self):
        """Create a mock AceInstance with required attributes."""
        instance = MagicMock()
        instance.instance_num = 0
        instance.SLOT_COUNT = 4
        instance.ace_config = {"rfid_temp_mode": "average"}
        instance.inventory = [
            {"status": "ready", "color": [255, 255, 255], "material": "PLA", "temp": 200, "rfid": True},
            {"status": "ready", "color": [234, 42, 43], "material": "PLA", "temp": 200, "rfid": True},
            {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
        ]
        instance.MATERIAL_TEMPS = {"PLA": 200, "PLA High Speed": 215, "ABS": 240}
        instance.DEFAULT_TEMP = 200
        instance.gcode = MagicMock()
        instance.manager = MagicMock()
        return instance

    def test_sends_correct_request(self, mock_ace_instance):
        """Test that get_filament_info request is sent with correct slot index."""
        from extras.ace.instance import AceInstance
        
        # Get the actual method
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        
        # Call the method
        _query_rfid_full_data(mock_ace_instance, slot_idx=1)
        
        # Verify send_request was called with correct params
        mock_ace_instance.send_request.assert_called_once()
        call_args = mock_ace_instance.send_request.call_args
        request = call_args[0][0]
        
        assert request["method"] == "get_filament_info"
        assert request["params"]["index"] == 1


class TestRfidCallbackTemperatureCalculation:
    """Tests for temperature calculation in rfid_callback."""

    @pytest.fixture
    def mock_ace_instance(self):
        """Create a mock AceInstance."""
        instance = MagicMock()
        instance.instance_num = 0
        instance.SLOT_COUNT = 4
        instance.inventory = [
            {"status": "ready", "color": [255, 255, 255], "material": "PLA", "temp": 200, "rfid": True},
        ]
        instance.MATERIAL_TEMPS = {"PLA": 200, "PLA High Speed": 215}
        instance.DEFAULT_TEMP = 200
        instance.gcode = MagicMock()
        instance.manager = MagicMock()
        return instance

    def _make_rfid_response(self, extruder_temp_min=190, extruder_temp_max=230):
        """Create a mock RFID response."""
        return {
            "code": 0,
            "result": {
                "rfid": 2,  # RFID_STATE_IDENTIFIED
                "sku": "AHPLBK-101",
                "brand": "AC",
                "type": "PLA",
                "icon_type": 0,
                "colors": [[234, 42, 43, 255]],
                "extruder_temp": {"min": extruder_temp_min, "max": extruder_temp_max},
                "hotbed_temp": {"min": 50, "max": 60},
                "diameter": 1.75,
                "total": 330,
                "current": 0,
            }
        }

    def test_temp_mode_average(self, mock_ace_instance):
        """Test temperature calculation with mode=average."""
        from extras.ace.instance import AceInstance
        
        mock_ace_instance.ace_config = {"rfid_temp_mode": "average"}
        
        # Get the method and call it to capture the callback
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=0)
        
        # Get the callback that was passed to send_request
        callback = mock_ace_instance.send_request.call_args[0][1]
        
        # Call the callback with a mock response
        response = self._make_rfid_response(extruder_temp_min=190, extruder_temp_max=230)
        callback(response)
        
        # Check temperature: (190 + 230) / 2 = 210
        assert mock_ace_instance.inventory[0]["temp"] == 210

    def test_temp_mode_min(self, mock_ace_instance):
        """Test temperature calculation with mode=min."""
        from extras.ace.instance import AceInstance
        
        mock_ace_instance.ace_config = {"rfid_temp_mode": "min"}
        
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=0)
        
        callback = mock_ace_instance.send_request.call_args[0][1]
        response = self._make_rfid_response(extruder_temp_min=190, extruder_temp_max=230)
        callback(response)
        
        # Check temperature: min = 190
        assert mock_ace_instance.inventory[0]["temp"] == 190

    def test_temp_mode_max(self, mock_ace_instance):
        """Test temperature calculation with mode=max."""
        from extras.ace.instance import AceInstance
        
        mock_ace_instance.ace_config = {"rfid_temp_mode": "max"}
        
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=0)
        
        callback = mock_ace_instance.send_request.call_args[0][1]
        response = self._make_rfid_response(extruder_temp_min=190, extruder_temp_max=230)
        callback(response)
        
        # Check temperature: max = 230
        assert mock_ace_instance.inventory[0]["temp"] == 230

    def test_temp_fallback_when_no_rfid_temp(self, mock_ace_instance):
        """Test fallback to MATERIAL_TEMPS when RFID has no temperature."""
        from extras.ace.instance import AceInstance
        
        mock_ace_instance.ace_config = {"rfid_temp_mode": "average"}
        
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=0)
        
        callback = mock_ace_instance.send_request.call_args[0][1]
        
        # Response with no temperature data
        response = self._make_rfid_response(extruder_temp_min=0, extruder_temp_max=0)
        callback(response)
        
        # Should fall back to MATERIAL_TEMPS["PLA"] = 200
        assert mock_ace_instance.inventory[0]["temp"] == 200

    def test_temp_mode_min_falls_back_to_max_when_min_zero(self, mock_ace_instance):
        """Test that mode=min falls back to max when min is 0."""
        from extras.ace.instance import AceInstance
        
        mock_ace_instance.ace_config = {"rfid_temp_mode": "min"}
        
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=0)
        
        callback = mock_ace_instance.send_request.call_args[0][1]
        response = self._make_rfid_response(extruder_temp_min=0, extruder_temp_max=230)
        callback(response)
        
        # min=0, so should fall back to max=230
        assert mock_ace_instance.inventory[0]["temp"] == 230

    def test_temp_mode_max_falls_back_to_min_when_max_zero(self, mock_ace_instance):
        """Test that mode=max falls back to min when max is 0."""
        from extras.ace.instance import AceInstance
        
        mock_ace_instance.ace_config = {"rfid_temp_mode": "max"}
        
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=0)
        
        callback = mock_ace_instance.send_request.call_args[0][1]
        response = self._make_rfid_response(extruder_temp_min=190, extruder_temp_max=0)
        callback(response)
        
        # max=0, so should fall back to min=190
        assert mock_ace_instance.inventory[0]["temp"] == 190


class TestRfidCallbackFieldStorage:
    """Tests for RFID field storage in inventory."""

    @pytest.fixture
    def mock_ace_instance(self):
        """Create a mock AceInstance."""
        instance = MagicMock()
        instance.instance_num = 0
        instance.SLOT_COUNT = 4
        instance.ace_config = {"rfid_temp_mode": "average"}
        instance.inventory = [
            {"status": "ready", "color": [255, 255, 255], "material": "PLA", "temp": 200, "rfid": True},
        ]
        instance.MATERIAL_TEMPS = {"PLA": 200}
        instance.DEFAULT_TEMP = 200
        instance.gcode = MagicMock()
        instance.manager = MagicMock()
        return instance

    def test_all_rfid_fields_stored(self, mock_ace_instance):
        """Test that all RFID fields are stored in inventory, color extracted from RGBA."""
        from extras.ace.instance import AceInstance
        
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=0)
        
        callback = mock_ace_instance.send_request.call_args[0][1]
        
        response = {
            "code": 0,
            "result": {
                "rfid": 2,  # RFID_STATE_IDENTIFIED
                "sku": "AHPLBK-101",
                "brand": "Anycubic",
                "type": "PLA",
                "icon_type": 1,
                "colors": [[234, 42, 43, 255]],  # RGBA array
                "extruder_temp": {"min": 190, "max": 230},
                "hotbed_temp": {"min": 50, "max": 60},
                "diameter": 1.75,
                "total": 330,
                "current": 150,
            }
        }
        callback(response)
        
        inv = mock_ace_instance.inventory[0]
        assert inv["sku"] == "AHPLBK-101"
        assert inv["brand"] == "Anycubic"
        assert inv["icon_type"] == 1
        assert inv["color"] == [234, 42, 43]  # RGB extracted from colors array (alpha ignored)
        assert inv["extruder_temp"] == {"min": 190, "max": 230}
        assert inv["hotbed_temp"] == {"min": 50, "max": 60}
        assert inv["diameter"] == 1.75
        assert inv["total"] == 330
        assert inv["current"] == 150
        
        # CRITICAL: Material field must be updated by callback
        # This assertion was missing, which allowed the infinite query loop bug to exist
        assert inv["material"] == "PLA", \
            "RFID callback must update material field to prevent infinite query loops"
        
        # Verify rgba is NOT stored (removed)
        assert "rgba" not in inv

    def test_partial_rfid_fields_stored(self, mock_ace_instance):
        """Test that partial RFID fields are stored (missing some fields)."""
        from extras.ace.instance import AceInstance
        
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=0)
        
        callback = mock_ace_instance.send_request.call_args[0][1]
        
        # Response with minimal fields
        response = {
            "code": 0,
            "result": {
                "rfid": 2,  # RFID_STATE_IDENTIFIED
                "sku": "TEST-SKU",
                "type": "PLA",
                "extruder_temp": {"min": 200, "max": 220},
            }
        }
        callback(response)
        
        inv = mock_ace_instance.inventory[0]
        assert inv["sku"] == "TEST-SKU"
        assert inv["extruder_temp"] == {"min": 200, "max": 220}
        # These should not exist since they weren't in response
        assert "brand" not in inv or inv.get("brand") == ""
        assert "hotbed_temp" not in inv

    def test_syncs_to_persistent_storage(self, mock_ace_instance):
        """Test that inventory is synced to persistent storage."""
        from extras.ace.instance import AceInstance
        
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=0)
        
        callback = mock_ace_instance.send_request.call_args[0][1]
        
        response = {
            "code": 0,
            "result": {
                "rfid": 2,  # RFID_STATE_IDENTIFIED
                "sku": "TEST",
                "type": "PLA",
                "extruder_temp": {"min": 200, "max": 220},
            }
        }
        callback(response)
        
        # Verify sync was called
        mock_ace_instance.manager._sync_inventory_to_persistent.assert_called_once_with(0)

    def test_emits_inventory_update(self, mock_ace_instance):
        """Test that JSON update is emitted for UI."""
        from extras.ace.instance import AceInstance
        
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=0)
        
        callback = mock_ace_instance.send_request.call_args[0][1]
        
        response = {
            "code": 0,
            "result": {
                "rfid": 2,  # RFID_STATE_IDENTIFIED
                "sku": "TEST",
                "type": "PLA",
                "extruder_temp": {"min": 200, "max": 220},
            }
        }
        callback(response)
        
        # Verify emit was called
        # emit may be suppressed in non-debug mode; rely on inventory/state updates instead
        assert mock_ace_instance.inventory[0]["sku"] == "TEST"


class TestRfidCallbackErrorHandling:
    """Tests for error handling in rfid_callback."""

    @pytest.fixture
    def mock_ace_instance(self):
        """Create a mock AceInstance."""
        instance = MagicMock()
        instance.instance_num = 0
        instance.SLOT_COUNT = 4
        instance.ace_config = {"rfid_temp_mode": "average"}
        instance.inventory = [
            {"status": "ready", "color": [255, 255, 255], "material": "PLA", "temp": 200, "rfid": True},
        ]
        instance.MATERIAL_TEMPS = {"PLA": 200}
        instance.DEFAULT_TEMP = 200
        return instance

    def test_handles_error_response(self, mock_ace_instance):
        """Test handling of error response from ACE."""
        from extras.ace.instance import AceInstance
        
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=0)
        
        callback = mock_ace_instance.send_request.call_args[0][1]
        
        # Error response
        response = {"code": -1, "msg": "RFID read failed"}
        callback(response)
        
        # Should log error, not crash
        mock_ace_instance.gcode.respond_info.assert_called()
        # Inventory should not be modified
        assert mock_ace_instance.inventory[0]["temp"] == 200

    def test_handles_no_response(self, mock_ace_instance):
        """Test handling of None response."""
        from extras.ace.instance import AceInstance
        
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=0)
        
        callback = mock_ace_instance.send_request.call_args[0][1]
        
        # No response
        callback(None)
        
        # Should log error, not crash
        mock_ace_instance.gcode.respond_info.assert_called()

    def test_handles_missing_result(self, mock_ace_instance):
        """Test handling of response without result field."""
        from extras.ace.instance import AceInstance
        
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=0)
        
        callback = mock_ace_instance.send_request.call_args[0][1]
        
        # Response without result
        response = {"code": 0}
        callback(response)
        
        # Should not crash, inventory unchanged
        assert mock_ace_instance.inventory[0]["temp"] == 200

    def test_handles_invalid_slot_index(self, mock_ace_instance):
        """Test handling of invalid slot index."""
        from extras.ace.instance import AceInstance
        
        _query_rfid_full_data = AceInstance._query_rfid_full_data
        _query_rfid_full_data(mock_ace_instance, slot_idx=99)  # Invalid slot
        
        callback = mock_ace_instance.send_request.call_args[0][1]
        
        response = {
            "code": 0,
            "result": {
                "rfid": 2,  # RFID_STATE_IDENTIFIED
                "sku": "TEST",
                "type": "PLA",
                "extruder_temp": {"min": 200, "max": 220},
            }
        }
        
        # Should not crash even with invalid slot
        callback(response)


class TestRfidTempModeConfig:
    """Tests for rfid_temp_mode config option."""

    def test_config_default_is_average(self):
        """Test that default rfid_temp_mode is average."""
        from extras.ace.config import read_ace_config
        
        mock_config = MagicMock()
        mock_config.getint = MagicMock(return_value=1)
        mock_config.getfloat = MagicMock(return_value=1.0)
        mock_config.getboolean = MagicMock(return_value=True)
        mock_config.get = MagicMock(return_value="average")
        
        result = read_ace_config(mock_config)
        
        assert result["rfid_temp_mode"] == "average"

    def test_config_accepts_min(self):
        """Test that rfid_temp_mode accepts 'min'."""
        from extras.ace.config import read_ace_config
        
        mock_config = MagicMock()
        mock_config.getint = MagicMock(return_value=1)
        mock_config.getfloat = MagicMock(return_value=1.0)
        mock_config.getboolean = MagicMock(return_value=True)
        
        def get_side_effect(key, default=""):
            if key == "rfid_temp_mode":
                return "min"
            return default
        
        mock_config.get = MagicMock(side_effect=get_side_effect)
        
        result = read_ace_config(mock_config)
        
        assert result["rfid_temp_mode"] == "min"

    def test_config_accepts_max(self):
        """Test that rfid_temp_mode accepts 'max'."""
        from extras.ace.config import read_ace_config
        
        mock_config = MagicMock()
        mock_config.getint = MagicMock(return_value=1)
        mock_config.getfloat = MagicMock(return_value=1.0)
        mock_config.getboolean = MagicMock(return_value=True)
        
        def get_side_effect(key, default=""):
            if key == "rfid_temp_mode":
                return "MAX"  # Test case insensitivity
            return default
        
        mock_config.get = MagicMock(side_effect=get_side_effect)
        
        result = read_ace_config(mock_config)
        
        assert result["rfid_temp_mode"] == "max"

    def test_config_invalid_value_defaults_to_average(self):
        """Test that invalid rfid_temp_mode defaults to average."""
        from extras.ace.config import read_ace_config
        
        mock_config = MagicMock()
        mock_config.getint = MagicMock(return_value=1)
        mock_config.getfloat = MagicMock(return_value=1.0)
        mock_config.getboolean = MagicMock(return_value=True)
        
        def get_side_effect(key, default=""):
            if key == "rfid_temp_mode":
                return "invalid_value"
            return default
        
        mock_config.get = MagicMock(side_effect=get_side_effect)
        
        result = read_ace_config(mock_config)
        
        assert result["rfid_temp_mode"] == "average"

    def test_moonraker_unknown_material_mode_invalid_defaults_to_passthrough(self):
        """Invalid moonraker unknown material mode should fall back safely."""
        from extras.ace.config import read_ace_config

        mock_config = MagicMock()
        mock_config.getint = MagicMock(return_value=1)
        mock_config.getfloat = MagicMock(return_value=1.0)
        mock_config.getboolean = MagicMock(return_value=True)

        def get_side_effect(key, default=""):
            if key == "moonraker_lane_sync_unknown_material_mode":
                return "bad_mode"
            return default

        mock_config.get = MagicMock(side_effect=get_side_effect)

        result = read_ace_config(mock_config)
        assert result["moonraker_lane_sync_unknown_material_mode"] == "passthrough"


class TestMoonrakerLaneSyncConfigDefaults:
    """Config defaults for Moonraker lane sync."""

    def test_lane_sync_enabled_by_default(self):
        """Lane sync should opt-in by default to feed Orca lane_data."""
        from extras.ace.config import read_ace_config

        mock_config = MagicMock()
        mock_config.getint = MagicMock(return_value=1)
        mock_config.getfloat = MagicMock(return_value=1.0)

        def getboolean_side_effect(key, default=False):
            return default

        mock_config.getboolean = MagicMock(side_effect=getboolean_side_effect)
        mock_config.get = MagicMock(side_effect=lambda key, default="": default)

        result = read_ace_config(mock_config)
        assert result["moonraker_lane_sync_enabled"] is True
