"""
Tests for configuration utility functions.

These are pure functions with no hardware dependencies - easy to test!
"""
import pytest
from ace.config import (
    get_tool_offset,
    get_instance_from_tool,
    get_local_slot,
    parse_instance_number,
    parse_instance_config,
    create_empty_inventory_slot,
    create_inventory,
    SLOTS_PER_ACE,
    ACE_INSTANCES,
    OVERRIDABLE_PARAMS,
)


class TestGetToolOffset:
    """Test tool offset calculation."""
    
    def test_instance_0_offset_is_0(self):
        """Instance 0 tools start at T0."""
        assert get_tool_offset(0) == 0
    
    def test_instance_1_offset_is_4(self):
        """Instance 1 tools start at T4."""
        assert get_tool_offset(1) == 4
    
    def test_instance_2_offset_is_8(self):
        """Instance 2 tools start at T8."""
        assert get_tool_offset(2) == 8
    
    def test_formula_works_for_any_instance(self):
        """Verify formula: offset = instance_num * SLOTS_PER_ACE."""
        for instance_num in range(10):
            expected = instance_num * SLOTS_PER_ACE
            assert get_tool_offset(instance_num) == expected


class TestGetInstanceFromTool:
    """Test tool to instance mapping."""
    
    def setup_method(self):
        """Register instances in global registry for tests."""
        ACE_INSTANCES.clear()
        # Create mock instances 0-4
        for i in range(5):
            ACE_INSTANCES[i] = {'instance_num': i}
    
    def teardown_method(self):
        """Clean up after tests."""
        ACE_INSTANCES.clear()
    
    def test_t0_to_t3_maps_to_instance_0(self):
        """T0-T3 belong to instance 0."""
        assert get_instance_from_tool(0) == 0
        assert get_instance_from_tool(1) == 0
        assert get_instance_from_tool(2) == 0
        assert get_instance_from_tool(3) == 0
    
    def test_t4_to_t7_maps_to_instance_1(self):
        """T4-T7 belong to instance 1."""
        assert get_instance_from_tool(4) == 1
        assert get_instance_from_tool(5) == 1
        assert get_instance_from_tool(6) == 1
        assert get_instance_from_tool(7) == 1
    
    def test_t8_to_t11_maps_to_instance_2(self):
        """T8-T11 belong to instance 2."""
        assert get_instance_from_tool(8) == 2
        assert get_instance_from_tool(9) == 2
        assert get_instance_from_tool(10) == 2
        assert get_instance_from_tool(11) == 2
    
    def test_negative_tool_returns_minus_one(self):
        """Invalid negative tool index returns -1."""
        assert get_instance_from_tool(-1) == -1
        assert get_instance_from_tool(-5) == -1
    
    def test_formula_works_for_high_tool_numbers(self):
        """Verify formula works for multi-instance setups."""
        # T15 should be instance 3 (15 // 4 = 3)
        assert get_instance_from_tool(15) == 3
        # T16 should be instance 4
        assert get_instance_from_tool(16) == 4
    
    def test_unregistered_instance_returns_minus_one(self):
        """Tool for unregistered instance returns -1."""
        ACE_INSTANCES.clear()
        # No instances registered
        assert get_instance_from_tool(0) == -1
        assert get_instance_from_tool(4) == -1


class TestGetLocalSlot:
    """Test global tool to local slot conversion."""
    
    def test_instance_0_tools(self):
        """T0-T3 map to local slots 0-3 in instance 0."""
        assert get_local_slot(0, instance_num=0) == 0
        assert get_local_slot(1, instance_num=0) == 1
        assert get_local_slot(2, instance_num=0) == 2
        assert get_local_slot(3, instance_num=0) == 3
    
    def test_instance_1_tools(self):
        """T4-T7 map to local slots 0-3 in instance 1."""
        assert get_local_slot(4, instance_num=1) == 0
        assert get_local_slot(5, instance_num=1) == 1
        assert get_local_slot(6, instance_num=1) == 2
        assert get_local_slot(7, instance_num=1) == 3
    
    def test_instance_2_tools(self):
        """T8-T11 map to local slots 0-3 in instance 2."""
        assert get_local_slot(8, instance_num=2) == 0
        assert get_local_slot(9, instance_num=2) == 1
        assert get_local_slot(10, instance_num=2) == 2
        assert get_local_slot(11, instance_num=2) == 3
    
    def test_wrong_instance_returns_minus_one(self):
        """Tool from different instance returns -1."""
        # T4 doesn't belong to instance 0
        assert get_local_slot(4, instance_num=0) == -1
        # T0 doesn't belong to instance 1
        assert get_local_slot(0, instance_num=1) == -1
    
    def test_negative_tool_returns_minus_one(self):
        """Invalid tool index returns -1."""
        assert get_local_slot(-1, instance_num=0) == -1
    
    def test_formula_tool_minus_offset(self):
        """Verify formula: local_slot = tool_index - (instance_num * SLOTS_PER_ACE)."""
        # T5 in instance 1: 5 - (1 * 4) = 1
        assert get_local_slot(5, instance_num=1) == 1
        # T10 in instance 2: 10 - (2 * 4) = 2
        assert get_local_slot(10, instance_num=2) == 2


class TestParseInstanceNumber:
    """Test instance number extraction from strings."""
    
    def test_parse_ace_defaults_to_zero(self):
        """Parse 'ace' returns 0."""
        assert parse_instance_number('ace') == 0
        assert parse_instance_number('ACE') == 0
    
    def test_parse_ace_with_space(self):
        """Parse 'ace 1' notation."""
        assert parse_instance_number('ace 0') == 0
        assert parse_instance_number('ace 1') == 1
        assert parse_instance_number('ace 2') == 2
    
    def test_parse_ace_with_number(self):
        """Parse 'ace1', 'ace2' notation."""
        assert parse_instance_number('ace1') == 1
        assert parse_instance_number('ace2') == 2
        assert parse_instance_number('ace10') == 10
    
    def test_parse_ace_with_underscore(self):
        """Parse 'ace_1', 'ace_2' notation."""
        assert parse_instance_number('ace_1') == 1
        assert parse_instance_number('ace_2') == 2
    
    def test_parse_ace_no_number_suffix(self):
        """Parse 'ace' with no number suffix returns 0."""
        # This tests the regex match but with no group(1) - line 150-153
        assert parse_instance_number('ace') == 0
        assert parse_instance_number('ACE') == 0
    
    def test_invalid_format_returns_zero(self):
        """Invalid format defaults to 0."""
        assert parse_instance_number('invalid') == 0
        assert parse_instance_number('xyz') == 0
        assert parse_instance_number('') == 0
    
    def test_none_returns_zero(self):
        """None input returns 0."""
        assert parse_instance_number(None) == 0


class TestInventoryHelpers:
    """Test inventory creation helpers."""
    
    def test_create_empty_inventory_slot(self):
        """Empty slot has default values."""
        slot = create_empty_inventory_slot()
        assert slot['material'] == ''
        assert slot['color'] == [0, 0, 0]
        assert slot['temp'] == 0
        assert slot['status'] == 'empty'
    
    def test_create_inventory_creates_4_slots(self):
        """Inventory has SLOTS_PER_ACE (4) empty slots."""
        inventory = create_inventory(SLOTS_PER_ACE)
        assert len(inventory) == 4
        
        # All slots should be empty by default
        for slot in inventory:
            assert slot['status'] == 'empty'
            assert slot['material'] == ''
            assert slot['color'] == [0, 0, 0]
    
    def test_create_inventory_custom_count(self):
        """Can create inventory with custom slot count."""
        inventory = create_inventory(8)
        assert len(inventory) == 8
        
        # All should be empty
        for slot in inventory:
            assert slot['status'] == 'empty'
    
    def test_create_status_dict(self):
        """Create status dict with default values."""
        from ace.config import create_status_dict
        
        status = create_status_dict(SLOTS_PER_ACE)
        assert status['status'] == 'ready'
        assert status['dryer']['status'] == 'stop'
        assert status['dryer']['target_temp'] == 0
        assert status['dryer']['duration'] == 0
        assert status['dryer']['remain_time'] == 0
        assert status['temp'] == 0
        assert status['enable_rfid'] == 1
        assert status['fan_speed'] == 7000
        assert status['feed_assist_count'] == 0
        assert status['cont_assist_time'] == 0.0
        assert len(status['slots']) == 4
        
        # Check slot structure
        for i, slot in enumerate(status['slots']):
            assert slot['index'] == i
            assert slot['status'] == 'empty'
            assert slot['sku'] == ''
            assert slot['type'] == ''
            assert slot['color'] == [0, 0, 0]
    
    def test_create_status_dict_custom_count(self):
        """Create status dict with custom slot count."""
        from ace.config import create_status_dict
        
        status = create_status_dict(8)
        assert len(status['slots']) == 8


class TestParseInstanceConfig:
    """Test multi-instance configuration parsing.
    
    Configuration values can be specified in several formats:
    - Simple: "1000" → same value for all instances
    - Global + override: "1000,2:500" → 1000 for all except instance 2
    - Explicit all: "0:1000,1:400,2:2000" → per-instance values
    """
    
    def test_simple_value_integer(self):
        """Simple integer value applies to all instances."""
        assert parse_instance_config("1000", 0, "test_param") == 1000
        assert parse_instance_config("1000", 1, "test_param") == 1000
        assert parse_instance_config("1000", 5, "test_param") == 1000
    
    def test_simple_value_float(self):
        """Simple float value applies to all instances."""
        assert parse_instance_config("1.5", 0, "test_param") == 1.5
        assert parse_instance_config("100.25", 1, "test_param") == 100.25
        assert parse_instance_config("3.14159", 2, "test_param") == 3.14159
    
    def test_global_with_single_override(self):
        """Global default with one instance override."""
        # "1000,2:500" means 1000 for all except instance 2
        assert parse_instance_config("1000,2:500", 0, "test_param") == 1000
        assert parse_instance_config("1000,2:500", 1, "test_param") == 1000
        assert parse_instance_config("1000,2:500", 2, "test_param") == 500
        assert parse_instance_config("1000,2:500", 3, "test_param") == 1000
    
    def test_global_with_multiple_overrides(self):
        """Global default with multiple instance overrides."""
        # "100,0:50,2:200" means 100 for all except instance 0 (50) and 2 (200)
        assert parse_instance_config("100,0:50,2:200", 0, "test_param") == 50
        assert parse_instance_config("100,0:50,2:200", 1, "test_param") == 100
        assert parse_instance_config("100,0:50,2:200", 2, "test_param") == 200
        assert parse_instance_config("100,0:50,2:200", 3, "test_param") == 100
    
    def test_explicit_per_instance_values(self):
        """Explicit value for each instance."""
        # "0:1000,1:400,2:2000" specifies value for each instance
        assert parse_instance_config("0:1000,1:400,2:2000", 0, "test_param") == 1000
        assert parse_instance_config("0:1000,1:400,2:2000", 1, "test_param") == 400
        assert parse_instance_config("0:1000,1:400,2:2000", 2, "test_param") == 2000
    
    def test_explicit_per_instance_with_float(self):
        """Explicit float values per instance."""
        assert parse_instance_config("0:1.5,1:2.5,2:3.5", 0, "test_param") == 1.5
        assert parse_instance_config("0:1.5,1:2.5,2:3.5", 1, "test_param") == 2.5
        assert parse_instance_config("0:1.5,1:2.5,2:3.5", 2, "test_param") == 3.5
    
    def test_whitespace_handling(self):
        """Whitespace is properly trimmed."""
        assert parse_instance_config(" 1000 ", 0, "test_param") == 1000
        assert parse_instance_config(" 1000 , 2 : 500 ", 2, "test_param") == 500
        assert parse_instance_config("0 : 100 , 1 : 200", 1, "test_param") == 200
    
    def test_missing_instance_with_no_default(self):
        """Requesting instance without value and no default raises error."""
        with pytest.raises(ValueError, match="No value found for instance 3"):
            parse_instance_config("0:100,1:200", 3, "test_param")
    
    def test_invalid_value_format(self):
        """Invalid value format raises error."""
        with pytest.raises(ValueError, match="Invalid config value"):
            parse_instance_config("not_a_number", 0, "test_param")
    
    def test_invalid_override_format(self):
        """Invalid override format raises error."""
        with pytest.raises(ValueError, match="Invalid instance override"):
            parse_instance_config("1000,bad:500", 0, "test_param")
        
        with pytest.raises(ValueError, match="Invalid instance override"):
            parse_instance_config("1000,2:bad", 0, "test_param")
    
    def test_invalid_global_default_format(self):
        """Invalid global default value raises error."""
        # "not_a_number,0:100" has invalid global default
        with pytest.raises(ValueError, match="Invalid global default"):
            parse_instance_config("not_a_number,0:100", 0, "test_param")
    
    def test_multiple_global_defaults_error(self):
        """Multiple global defaults raise error."""
        # "100,200,0:50" has two non-override values
        with pytest.raises(ValueError, match="Multiple global defaults"):
            parse_instance_config("100,200,0:50", 0, "test_param")
    
    def test_real_world_feed_speed(self):
        """Real example: feed_speed with different values."""
        # Instance 0 and 2 use 60mm/s, instance 1 uses 80mm/s
        config = "60,1:80"
        assert parse_instance_config(config, 0, "feed_speed") == 60
        assert parse_instance_config(config, 1, "feed_speed") == 80
        assert parse_instance_config(config, 2, "feed_speed") == 60
    
    def test_real_world_retract_speed(self):
        """Real example: retract_speed all instances same."""
        config = "50"
        assert parse_instance_config(config, 0, "retract_speed") == 50
        assert parse_instance_config(config, 1, "retract_speed") == 50
        assert parse_instance_config(config, 2, "retract_speed") == 50
    
    def test_real_world_max_dryer_temp(self):
        """Real example: different dryer temps per instance."""
        # Instance 0: 70°C, Instance 1: 60°C, Instance 2: 65°C
        config = "0:70,1:60,2:65"
        assert parse_instance_config(config, 0, "max_dryer_temperature") == 70
        assert parse_instance_config(config, 1, "max_dryer_temperature") == 60
        assert parse_instance_config(config, 2, "max_dryer_temperature") == 65


class TestOverridableParams:
    """Test OVERRIDABLE_PARAMS configuration."""
    
    def test_overridable_params_list_exists(self):
        """OVERRIDABLE_PARAMS list is defined."""
        assert OVERRIDABLE_PARAMS is not None
        assert isinstance(OVERRIDABLE_PARAMS, list)
        assert len(OVERRIDABLE_PARAMS) > 0
    
    def test_expected_params_are_overridable(self):
        """Expected parameters are in OVERRIDABLE_PARAMS."""
        expected_params = [
            "feed_speed",
            "retract_speed",
            "total_max_feeding_length",
            "toolchange_load_length",
            "incremental_feeding_length",
            "incremental_feeding_speed",
            "heartbeat_interval",
            "max_dryer_temperature",
        ]
        for param in expected_params:
            assert param in OVERRIDABLE_PARAMS, f"{param} should be overridable"
    
    def test_all_params_are_strings(self):
        """All parameter names are strings."""
        for param in OVERRIDABLE_PARAMS:
            assert isinstance(param, str)
            assert len(param) > 0


class TestGetAceInstanceAndSlot:
    """Test combined instance and slot lookup."""
    
    def setup_method(self):
        """Register instances for tests."""
        ACE_INSTANCES.clear()
        for i in range(3):
            ACE_INSTANCES[i] = {'instance_num': i}
    
    def teardown_method(self):
        """Clean up after tests."""
        ACE_INSTANCES.clear()
    
    def test_valid_tool_returns_instance_and_slot(self):
        """Valid tool returns (instance, slot) tuple."""
        from ace.config import get_ace_instance_and_slot_for_tool
        
        ace, slot = get_ace_instance_and_slot_for_tool(5)
        assert ace is not None
        assert ace['instance_num'] == 1
        assert slot == 1
    
    def test_invalid_negative_tool_returns_none_minus_one(self):
        """Invalid negative tool returns (None, -1)."""
        from ace.config import get_ace_instance_and_slot_for_tool
        
        ace, slot = get_ace_instance_and_slot_for_tool(-1)
        assert ace is None
        assert slot == -1
    
    def test_tool_for_unregistered_instance_returns_none_minus_one(self):
        """Tool for unregistered instance returns (None, -1)."""
        from ace.config import get_ace_instance_and_slot_for_tool
        
        # T12 would be instance 3, which doesn't exist
        ace, slot = get_ace_instance_and_slot_for_tool(12)
        assert ace is None
        assert slot == -1


class TestRoundTripConversions:
    """Test that conversions work correctly in both directions."""
    
    def setup_method(self):
        """Register instances for tests."""
        ACE_INSTANCES.clear()
        for i in range(3):
            ACE_INSTANCES[i] = {'instance_num': i}
    
    def teardown_method(self):
        """Clean up after tests."""
        ACE_INSTANCES.clear()
    
    def test_tool_to_instance_to_local_slot(self):
        """Convert tool → instance + local_slot works correctly."""
        for tool in range(12):  # Test T0-T11 (3 instances)
            instance_num = get_instance_from_tool(tool)
            local_slot = get_local_slot(tool, instance_num)
            
            # Verify local_slot is valid (0-3)
            assert 0 <= local_slot < SLOTS_PER_ACE
            
            # Reconstruct tool index
            reconstructed = get_tool_offset(instance_num) + local_slot
            assert reconstructed == tool
    
    def test_instance_and_slot_to_tool(self):
        """Convert instance + local_slot → tool works correctly."""
        for instance_num in range(3):
            for local_slot in range(SLOTS_PER_ACE):
                tool = get_tool_offset(instance_num) + local_slot
                
                # Verify we get back the same values
                assert get_instance_from_tool(tool) == instance_num
                assert get_local_slot(tool, instance_num) == local_slot


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
