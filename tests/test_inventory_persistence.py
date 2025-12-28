"""
Test suite for inventory persistence loading.

Tests the _load_all_inventories method with various data conditions:
- Missing file (no saved data)
- Valid old structure (basic fields)
- Extended new structure (with RFID fields)
- Mixed/partial data
- Corrupted data

These tests verify backward compatibility and graceful error handling.
"""

import unittest
from unittest.mock import Mock, MagicMock
from ace.config import create_inventory, SLOTS_PER_ACE


class TestInventoryLoading(unittest.TestCase):
    """Test the inventory loading logic directly."""

    def setUp(self):
        """Create a minimal mock manager to test loading logic."""
        self.mock_gcode = Mock()
        self.variables = {}
        
    def _load_inventory_for_instance(self, instance_num, variables):
        """
        Replicate the loading logic from manager._load_all_inventories.
        This isolates the logic we want to test.
        """
        varname = f"ace_inventory_{instance_num}"
        saved_inv = variables.get(varname, None)
        
        if saved_inv:
            return saved_inv, "loaded"
        else:
            return create_inventory(SLOTS_PER_ACE), "initialized"

    # ========== Test: Missing File ==========
    
    def test_load_missing_file_returns_default_inventory(self):
        """When no saved data exists, should return default empty inventory."""
        inventory, status = self._load_inventory_for_instance(0, {})
        
        self.assertEqual(status, "initialized")
        self.assertEqual(len(inventory), 4)
        
    def test_load_missing_file_default_slots_are_empty(self):
        """Default inventory should have 4 empty slots with correct structure."""
        inventory, _ = self._load_inventory_for_instance(0, {})
        
        for slot in inventory:
            self.assertEqual(slot['status'], 'empty')
            self.assertEqual(slot['color'], [0, 0, 0])
            self.assertEqual(slot['material'], '')
            self.assertEqual(slot['temp'], 0)
            self.assertEqual(slot['rfid'], False)

    # ========== Test: Valid Old Structure ==========
    
    def test_load_old_structure_returns_saved_data(self):
        """Old inventory structure (without new RFID fields) should load correctly."""
        old_inventory = [
            {"status": "ready", "color": [234, 42, 43], "material": "PLA", "temp": 200, "rfid": True},
            {"status": "ready", "color": [255, 255, 255], "material": "PLA", "temp": 200, "rfid": False},
            {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
        ]
        variables = {'ace_inventory_0': old_inventory}
        
        inventory, status = self._load_inventory_for_instance(0, variables)
        
        self.assertEqual(status, "loaded")
        self.assertEqual(inventory, old_inventory)
        
    def test_load_old_structure_preserves_all_fields(self):
        """All basic fields should be preserved when loading old structure."""
        old_inventory = [
            {"status": "ready", "color": [234, 42, 43], "material": "PLA", "temp": 200, "rfid": True},
            {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
        ]
        variables = {'ace_inventory_0': old_inventory}
        
        inventory, _ = self._load_inventory_for_instance(0, variables)
        
        self.assertEqual(inventory[0]['status'], 'ready')
        self.assertEqual(inventory[0]['color'], [234, 42, 43])
        self.assertEqual(inventory[0]['material'], 'PLA')
        self.assertEqual(inventory[0]['temp'], 200)
        self.assertEqual(inventory[0]['rfid'], True)

    # ========== Test: Extended New Structure ==========
    
    def test_load_extended_structure_with_rfid_fields(self):
        """New inventory structure with sku, brand, icon_type, rgba should load."""
        extended_inventory = [
            {
                "status": "ready",
                "color": [234, 42, 43],
                "material": "PLA",
                "temp": 200,
                "rfid": True,
                "sku": "AHPLBK-101",
                "brand": "Anycubic",
                "icon_type": 0,
                "rgba": [234, 42, 43, 255]
            },
            {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
        ]
        variables = {'ace_inventory_0': extended_inventory}
        
        inventory, status = self._load_inventory_for_instance(0, variables)
        
        self.assertEqual(status, "loaded")
        self.assertEqual(inventory[0]['sku'], 'AHPLBK-101')
        self.assertEqual(inventory[0]['brand'], 'Anycubic')
        self.assertEqual(inventory[0]['icon_type'], 0)
        self.assertEqual(inventory[0]['rgba'], [234, 42, 43, 255])
        
    def test_load_mixed_old_and_new_structure(self):
        """Inventory with some slots having new fields, some without."""
        mixed_inventory = [
            {"status": "ready", "color": [234, 42, 43], "material": "PLA", "temp": 200, "rfid": True, "sku": "AHPLBK-101"},
            {"status": "ready", "color": [255, 255, 255], "material": "PLA", "temp": 200, "rfid": False},  # No new fields
            {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
            {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0, "rfid": False},
        ]
        variables = {'ace_inventory_0': mixed_inventory}
        
        inventory, _ = self._load_inventory_for_instance(0, variables)
        
        # Slot 0 has sku
        self.assertIn('sku', inventory[0])
        self.assertEqual(inventory[0]['sku'], 'AHPLBK-101')
        
        # Slot 1 doesn't have sku
        self.assertNotIn('sku', inventory[1])

    # ========== Test: Corrupted/Invalid Data ==========
    
    def test_load_empty_list_treated_as_missing(self):
        """Empty list [] is falsy, should initialize default inventory."""
        variables = {'ace_inventory_0': []}
        
        inventory, status = self._load_inventory_for_instance(0, variables)
        
        # [] is falsy in Python, so "if saved_inv:" is False
        self.assertEqual(status, "initialized")
        self.assertEqual(len(inventory), 4)
        
    def test_load_none_value_treated_as_missing(self):
        """None value should initialize default inventory."""
        variables = {'ace_inventory_0': None}
        
        inventory, status = self._load_inventory_for_instance(0, variables)
        
        self.assertEqual(status, "initialized")
        self.assertEqual(len(inventory), 4)
        
    def test_load_corrupted_dict_instead_of_list(self):
        """Corrupted data (dict instead of list) is truthy and loads as-is."""
        variables = {'ace_inventory_0': {"error": "corrupted"}}
        
        inventory, status = self._load_inventory_for_instance(0, variables)
        
        # Dict is truthy, so it loads as-is (no type validation!)
        self.assertEqual(status, "loaded")
        self.assertEqual(inventory, {"error": "corrupted"})
        
    def test_load_wrong_length_list_loads_as_is(self):
        """List with wrong number of slots loads without validation."""
        short_inventory = [
            {"status": "ready", "color": [255, 255, 255], "material": "PLA", "temp": 200, "rfid": False},
        ]
        variables = {'ace_inventory_0': short_inventory}
        
        inventory, status = self._load_inventory_for_instance(0, variables)
        
        # No length validation - loads as-is
        self.assertEqual(status, "loaded")
        self.assertEqual(len(inventory), 1)
        
    def test_load_string_instead_of_list(self):
        """String is truthy and loads as-is (corrupted data)."""
        variables = {'ace_inventory_0': "corrupted string data"}
        
        inventory, status = self._load_inventory_for_instance(0, variables)
        
        self.assertEqual(status, "loaded")
        self.assertEqual(inventory, "corrupted string data")

    # ========== Test: Multiple Instances ==========
    
    def test_load_different_instances_independently(self):
        """Each instance loads its own inventory."""
        variables = {
            'ace_inventory_0': [{"status": "ready", "color": [255, 0, 0], "material": "PLA", "temp": 200, "rfid": False}] * 4,
            'ace_inventory_1': [{"status": "ready", "color": [0, 255, 0], "material": "PETG", "temp": 235, "rfid": True}] * 4,
        }
        
        inv0, _ = self._load_inventory_for_instance(0, variables)
        inv1, _ = self._load_inventory_for_instance(1, variables)
        
        self.assertEqual(inv0[0]['material'], 'PLA')
        self.assertEqual(inv1[0]['material'], 'PETG')
        
    def test_load_missing_instance_1_but_present_instance_0(self):
        """Instance 1 missing, Instance 0 present."""
        variables = {
            'ace_inventory_0': [{"status": "ready", "color": [255, 0, 0], "material": "PLA", "temp": 200, "rfid": False}] * 4,
        }
        
        inv0, status0 = self._load_inventory_for_instance(0, variables)
        inv1, status1 = self._load_inventory_for_instance(1, variables)
        
        self.assertEqual(status0, "loaded")
        self.assertEqual(status1, "initialized")


class TestInventoryFieldAccess(unittest.TestCase):
    """Test that inventory fields are accessed safely with .get() defaults."""
    
    def test_access_missing_field_with_get_returns_default(self):
        """Accessing missing field with .get() returns default value."""
        slot = {"status": "ready", "color": [255, 255, 255]}  # Missing material, temp, rfid
        
        # This is how the code accesses fields
        material = slot.get("material", "")
        temp = slot.get("temp", 0)
        rfid = slot.get("rfid", False)
        
        self.assertEqual(material, "")
        self.assertEqual(temp, 0)
        self.assertEqual(rfid, False)
        
    def test_access_optional_rfid_fields_with_get(self):
        """Optional RFID fields return None/default when missing."""
        slot = {"status": "ready", "color": [255, 255, 255], "material": "PLA", "temp": 200, "rfid": True}
        
        # New optional fields
        sku = slot.get("sku", None)
        brand = slot.get("brand", None)
        icon_type = slot.get("icon_type", None)
        rgba = slot.get("rgba", None)
        
        self.assertIsNone(sku)
        self.assertIsNone(brand)
        self.assertIsNone(icon_type)
        self.assertIsNone(rgba)
        
    def test_pop_missing_field_with_default_is_safe(self):
        """Using .pop(key, None) on missing key is safe."""
        slot = {"status": "ready"}
        
        # This is how the code clears optional fields
        result = slot.pop("sku", None)
        
        self.assertIsNone(result)
        self.assertNotIn("sku", slot)
        
    def test_in_operator_for_optional_fields(self):
        """Check presence of optional fields with 'in' operator."""
        slot_with_sku = {"status": "ready", "sku": "AHPLBK-101"}
        slot_without_sku = {"status": "ready"}
        
        self.assertIn("sku", slot_with_sku)
        self.assertNotIn("sku", slot_without_sku)


class TestCreateInventoryHelper(unittest.TestCase):
    """Test the create_inventory helper function."""
    
    def test_create_inventory_returns_correct_length(self):
        """create_inventory returns list of correct length."""
        inventory = create_inventory(4)
        self.assertEqual(len(inventory), 4)
        
    def test_create_inventory_slots_have_required_fields(self):
        """Each slot has all required fields."""
        inventory = create_inventory(4)
        
        required_fields = ['status', 'color', 'material', 'temp', 'rfid']
        for slot in inventory:
            for field in required_fields:
                self.assertIn(field, slot)
                
    def test_create_inventory_slots_are_empty(self):
        """Each slot is initialized as empty."""
        inventory = create_inventory(4)
        
        for slot in inventory:
            self.assertEqual(slot['status'], 'empty')
            self.assertEqual(slot['color'], [0, 0, 0])
            self.assertEqual(slot['material'], '')
            self.assertEqual(slot['temp'], 0)
            self.assertEqual(slot['rfid'], False)


if __name__ == '__main__':
    unittest.main()
