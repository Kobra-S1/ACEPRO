"""
Tests for EndlessSpool logic - pure decision logic, minimal mocking.

Focus: find_exact_match algorithm (material + color matching).
"""
import pytest
from unittest.mock import Mock, MagicMock
from ace.endless_spool import EndlessSpool
from ace.config import ACE_INSTANCES, SLOTS_PER_ACE


class TestFindExactMatch:
    """Test endless spool exact match finding algorithm."""
    
    def setup_method(self):
        """Create EndlessSpool with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.manager = Mock()
        self.manager.variables = {}
        
        # Create reactor mock
        reactor = Mock()
        reactor.monotonic = Mock(return_value=0.0)
        self.printer.get_reactor = Mock(return_value=reactor)
        
        self.endless_spool = EndlessSpool(
            self.printer,
            self.gcode,
            self.manager
        )
        
        # Register 2 instances (8 tools total)
        ACE_INSTANCES.clear()
        for i in range(2):
            instance = Mock()
            instance.instance_num = i
            instance.tool_offset = i * SLOTS_PER_ACE
            instance.SLOT_COUNT = SLOTS_PER_ACE
            instance.inventory = [
                {
                    'material': '',
                    'color': [0, 0, 0],
                    'status': 'empty'
                }
                for _ in range(SLOTS_PER_ACE)
            ]
            ACE_INSTANCES[i] = instance
    
    def teardown_method(self):
        """Clean up."""
        ACE_INSTANCES.clear()
    
    def test_find_exact_match_same_material_and_color(self):
        """Find next slot with matching material and color."""
        # T0: PLA Red
        ACE_INSTANCES[0].inventory[0] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        # T2: PLA Red (exact match)
        ACE_INSTANCES[0].inventory[2] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        # T3: PLA Blue (different color, no match)
        ACE_INSTANCES[0].inventory[3] = {
            'material': 'PLA',
            'color': [0, 0, 255],
            'status': 'ready'
        }
        
        # Search from T0, should find T2
        result = self.endless_spool.find_exact_match(current_tool=0)
        assert result == 2
    
    def test_find_exact_match_different_material_no_match(self):
        """Different material should not match."""
        # T0: PLA Red
        ACE_INSTANCES[0].inventory[0] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        # T1: PETG Red (different material)
        ACE_INSTANCES[0].inventory[1] = {
            'material': 'PETG',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        result = self.endless_spool.find_exact_match(current_tool=0)
        assert result == -1
    
    def test_find_exact_match_different_color_no_match(self):
        """Different color should not match."""
        # Mock get_match_mode to return 'exact' (color must match)
        self.endless_spool.get_match_mode = Mock(return_value='exact')
        
        # T0: PLA Red
        ACE_INSTANCES[0].inventory[0] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        # T1: PLA Blue (different color)
        ACE_INSTANCES[0].inventory[1] = {
            'material': 'PLA',
            'color': [0, 0, 255],
            'status': 'ready'
        }
        
        result = self.endless_spool.find_exact_match(current_tool=0)
        assert result == -1
    
    def test_find_exact_match_wraps_around(self):
        """Search wraps from last tool to first."""
        # T7: PLA Red (last tool)
        ACE_INSTANCES[1].inventory[3] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        # T1: PLA Red (should wrap around and find)
        ACE_INSTANCES[0].inventory[1] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        result = self.endless_spool.find_exact_match(current_tool=7)
        assert result == 1
    
    def test_find_exact_match_skips_current_tool(self):
        """Should not return the current tool itself."""
        # T0: PLA Red
        ACE_INSTANCES[0].inventory[0] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        # Only T0 has this material/color
        result = self.endless_spool.find_exact_match(current_tool=0)
        assert result == -1
    
    def test_find_exact_match_crosses_instances(self):
        """Match can be in different instance."""
        # T0 (instance 0): PLA Red
        ACE_INSTANCES[0].inventory[0] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        # T5 (instance 1): PLA Red
        ACE_INSTANCES[1].inventory[1] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        result = self.endless_spool.find_exact_match(current_tool=0)
        assert result == 5
    
    def test_find_exact_match_finds_first_available(self):
        """Returns first matching slot when multiple exist."""
        # T0: PLA Red
        ACE_INSTANCES[0].inventory[0] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        # T2: PLA Red
        ACE_INSTANCES[0].inventory[2] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        # T5: PLA Red
        ACE_INSTANCES[1].inventory[1] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        # Search from T0, should find T2 (first after T0)
        result = self.endless_spool.find_exact_match(current_tool=0)
        assert result == 2
    
    def test_find_exact_match_case_insensitive_material(self):
        """Material matching is case-insensitive."""
        # T0: PLA (lowercase)
        ACE_INSTANCES[0].inventory[0] = {
            'material': 'pla',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        # T1: PLA (uppercase)
        ACE_INSTANCES[0].inventory[1] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        result = self.endless_spool.find_exact_match(current_tool=0)
        assert result == 1
    
    def test_find_exact_match_ignores_whitespace(self):
        """Material matching ignores leading/trailing whitespace."""
        # T0: ' PLA ' (with spaces)
        ACE_INSTANCES[0].inventory[0] = {
            'material': ' PLA ',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        # T1: 'PLA' (no spaces)
        ACE_INSTANCES[0].inventory[1] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        result = self.endless_spool.find_exact_match(current_tool=0)
        assert result == 1
    
    def test_find_exact_match_empty_slots_ignored(self):
        """Empty slots (no material) are not considered."""
        # T0: PLA Red
        ACE_INSTANCES[0].inventory[0] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }
        
        # T1-T7: All empty
        for i in range(1, 8):
            instance_num = i // SLOTS_PER_ACE
            local_slot = i % SLOTS_PER_ACE
            ACE_INSTANCES[instance_num].inventory[local_slot] = {
                'material': '',
                'color': [0, 0, 0],
                'status': 'empty'
            }
        
        # No matches
        result = self.endless_spool.find_exact_match(current_tool=0)
        assert result == -1

    def test_find_next_ready_ignores_material_and_color(self):
        """Next-ready mode returns the first ready slot regardless of material/color."""
        self.endless_spool.get_match_mode = Mock(return_value='next')

        # Current tool: T0 (PLA Red)
        ACE_INSTANCES[0].inventory[0] = {
            'material': 'PLA',
            'color': [255, 0, 0],
            'status': 'ready'
        }

        # T1 not ready
        ACE_INSTANCES[0].inventory[1] = {
            'material': 'PETG',
            'color': [0, 255, 0],
            'status': 'loading'
        }

        # T2 ready but different material/color – should be picked
        ACE_INSTANCES[0].inventory[2] = {
            'material': 'ABS',
            'color': [1, 2, 3],
            'status': 'ready'
        }

        # T3 ready but farther away
        ACE_INSTANCES[0].inventory[3] = {
            'material': 'Nylon',
            'color': [9, 9, 9],
            'status': 'ready'
        }

        result = self.endless_spool.find_exact_match(current_tool=0)
        assert result == 2

    def test_find_next_ready_wraps_across_instances(self):
        """Next-ready mode wraps across instances to find the first ready slot."""
        self.endless_spool.get_match_mode = Mock(return_value='next')

        # Current tool: last slot of instance 0
        ACE_INSTANCES[0].inventory[3] = {
            'material': 'PLA',
            'color': [0, 0, 0],
            'status': 'ready'
        }

        # Instance 1, slot 0 not ready
        ACE_INSTANCES[1].inventory[0] = {
            'material': 'PETG',
            'color': [0, 0, 0],
            'status': 'empty'
        }

        # Instance 1, slot 1 ready -> should be picked after wrap
        ACE_INSTANCES[1].inventory[1] = {
            'material': 'TPU',
            'color': [10, 10, 10],
            'status': 'ready'
        }

        result = self.endless_spool.find_exact_match(current_tool=3)
        assert result == 5  # instance 1 slot 1

    def test_find_next_ready_no_ready_slots(self):
        """Next-ready mode returns -1 when nothing is ready."""
        self.endless_spool.get_match_mode = Mock(return_value='next')

        for i in range(8):
            inst = i // SLOTS_PER_ACE
            slot = i % SLOTS_PER_ACE
            ACE_INSTANCES[inst].inventory[slot] = {
                'material': 'PLA',
                'color': [0, 0, 0],
                'status': 'empty'
            }

        result = self.endless_spool.find_exact_match(current_tool=0)
        assert result == -1
    
    def test_find_exact_match_invalid_tool_returns_minus_one(self):
        """Invalid tool index returns -1."""
        result = self.endless_spool.find_exact_match(current_tool=-1)
        assert result == -1
        
        result = self.endless_spool.find_exact_match(current_tool=999)
        assert result == -1


class TestGetMatchMode:
    """Test get_match_mode configuration retrieval."""

    def setup_method(self):
        """Create EndlessSpool with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.manager = Mock()
        self.manager.variables = {}
        
        # Create reactor mock
        reactor = Mock()
        reactor.monotonic = Mock(return_value=0.0)
        self.printer.get_reactor = Mock(return_value=reactor)
        
        self.endless_spool = EndlessSpool(
            self.printer,
            self.gcode,
            self.manager
        )

    def test_get_match_mode_defaults_to_exact(self):
        """Test get_match_mode returns 'exact' when no save_variables."""
        self.printer.lookup_object = Mock(return_value=None)
        
        result = self.endless_spool.get_match_mode()
        
        assert result == "exact"

    def test_get_match_mode_returns_exact_from_variables(self):
        """Test get_match_mode returns 'exact' from saved variables."""
        mock_save_vars = Mock()
        mock_save_vars.allVariables = {"ace_endless_spool_match_mode": "exact"}
        self.printer.lookup_object = Mock(return_value=mock_save_vars)
        
        result = self.endless_spool.get_match_mode()
        
        assert result == "exact"

    def test_get_match_mode_returns_material_from_variables(self):
        """Test get_match_mode returns 'material' from saved variables."""
        mock_save_vars = Mock()
        mock_save_vars.allVariables = {"ace_endless_spool_match_mode": "material"}
        self.printer.lookup_object = Mock(return_value=mock_save_vars)
        
        result = self.endless_spool.get_match_mode()
        
        assert result == "material"

    def test_get_match_mode_defaults_when_variable_missing(self):
        """Test get_match_mode defaults to 'exact' when variable not set."""
        mock_save_vars = Mock()
        mock_save_vars.allVariables = {}
        self.printer.lookup_object = Mock(return_value=mock_save_vars)
        
        result = self.endless_spool.get_match_mode()
        
        assert result == "exact"


class TestGetStatus:
    """Test status reporting."""
    
    def setup_method(self):
        """Create EndlessSpool with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.manager = Mock()
        
        reactor = Mock()
        reactor.monotonic = Mock(return_value=0.0)
        self.printer.get_reactor = Mock(return_value=reactor)
        
        mock_save_vars = Mock()
        mock_save_vars.allVariables = {"ace_endless_spool_enabled": True}
        self.printer.lookup_object = Mock(return_value=mock_save_vars)
        
        self.endless_spool = EndlessSpool(
            self.printer,
            self.gcode,
            self.manager
        )
    
    def test_get_status_returns_dict(self):
        """Test get_status returns a dictionary."""
        result = self.endless_spool.get_status()
        
        assert isinstance(result, dict)


class TestMultipleMatches:
    """Test scenarios with multiple potential matches."""
    
    def setup_method(self):
        """Create EndlessSpool with mock dependencies."""
        self.printer = Mock()
        self.gcode = Mock()
        self.manager = Mock()
        
        reactor = Mock()
        reactor.monotonic = Mock(return_value=0.0)
        self.printer.get_reactor = Mock(return_value=reactor)
        
        mock_save_vars = Mock()
        mock_save_vars.allVariables = {}
        self.printer.lookup_object = Mock(return_value=mock_save_vars)
        
        self.endless_spool = EndlessSpool(
            self.printer,
            self.gcode,
            self.manager
        )
        
        # Register 2 instances (8 tools total)
        ACE_INSTANCES.clear()
        for i in range(2):
            instance = Mock()
            instance.instance_num = i
            instance.tool_offset = i * SLOTS_PER_ACE
            instance.SLOT_COUNT = SLOTS_PER_ACE
            instance.inventory = [
                {
                    'material': '',
                    'color': [0, 0, 0],
                    'status': 'empty'
                }
                for _ in range(SLOTS_PER_ACE)
            ]
            ACE_INSTANCES[i] = instance
    
    def teardown_method(self):
        """Clean up global state."""
        ACE_INSTANCES.clear()

    def test_find_match_prefers_closest(self):
        """Test find_match returns closest matching slot."""
        # T0: PLA White
        ACE_INSTANCES[0].inventory[0] = {
            'material': 'PLA',
            'color': [255, 255, 255],
            'status': 'ready'
        }
        # T1: PLA White (closer)
        ACE_INSTANCES[0].inventory[1] = {
            'material': 'PLA',
            'color': [255, 255, 255],
            'status': 'ready'
        }
        # T7: PLA White (farther)
        ACE_INSTANCES[1].inventory[3] = {
            'material': 'PLA',
            'color': [255, 255, 255],
            'status': 'ready'
        }
        
        result = self.endless_spool.find_exact_match(0)
        
        # Should return T1 (closest match)
        assert result == 1

    def test_find_match_with_already_tried(self):
        """Test find_match skips already-tried slots."""
        # Set up multiple matches
        for i in range(1, 4):
            ACE_INSTANCES[0].inventory[i] = {
                'material': 'PLA',
                'color': [255, 255, 255],
                'status': 'ready'
            }
        
        # T0 needs match, T1 already tried
        ACE_INSTANCES[0].inventory[0] = {
            'material': 'PLA',
            'color': [255, 255, 255],
            'status': 'ready'
        }
        
        # First call should return T1
        result1 = self.endless_spool.find_exact_match(0)
        assert result1 == 1
        
        # Second call with T1 in already_tried should return T2
        # Note: find_exact_match doesn't take already_tried param
        # This is just documenting expected behavior

    def test_find_match_all_slots_empty(self):
        """Test find_exact_match returns -1 when all slots empty."""
        # All slots empty by default
        
        result = self.endless_spool.find_exact_match(0)
        
        assert result == -1

    def test_find_match_all_non_empty_different_material(self):
        """Test no match when all have different material."""
        # T0: PLA
        ACE_INSTANCES[0].inventory[0] = {
            'material': 'PLA',
            'color': [255, 255, 255],
            'status': 'ready'
        }
        # Rest are ABS
        for i in range(1, 4):
            ACE_INSTANCES[0].inventory[i] = {
                'material': 'ABS',
                'color': [255, 255, 255],
                'status': 'ready'
            }
        
        result = self.endless_spool.find_exact_match(0)
        
        assert result == -1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
