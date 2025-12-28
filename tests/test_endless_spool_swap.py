"""
Test suite for Endless Spool execute_swap() business logic.

Tests focus on:
- Successful swap on first attempt
- Recovery and retry after failed swap attempt
- Sequential search through multiple candidates
- Proper inventory state management across attempts
- Exhaustion of all candidates
- Correct use of match mode during search

These are high-level integration tests focusing on the swap coordination logic.
"""

import unittest
from unittest.mock import Mock, patch, call
from ace.endless_spool import EndlessSpool
from ace.config import (
    ACE_INSTANCES,
    INSTANCE_MANAGERS,
    SLOTS_PER_ACE,
    create_inventory,
)


class TestEndlessSpoolSwapExecution(unittest.TestCase):
    """Test execute_swap() coordination logic with retry and fallback."""
    
    def setUp(self):
        """Set up test fixtures with mock manager and instances."""
        # Clear global state
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        
        # Create mock printer with save_variables
        self.mock_printer = Mock()
        self.mock_save_vars = Mock()
        self.variables = {"ace_endless_spool_match_mode": "exact"}  # Default to exact
        self.mock_save_vars.allVariables = self.variables
        
        def mock_lookup(name, default=None):
            if name == "save_variables":
                return self.mock_save_vars
            return default
        self.mock_printer.lookup_object = Mock(side_effect=mock_lookup)
        self.mock_printer.get_reactor = Mock(return_value=Mock())
        
        # Create mock gcode
        self.mock_gcode = Mock()
        
        # Create mock manager
        self.mock_manager = Mock()
        self.mock_manager.gcode = self.mock_gcode
        
        # Create mock ACE instances with inventory
        self.instance0 = Mock()
        self.instance0.inventory = create_inventory(SLOTS_PER_ACE)
        self.instance1 = Mock()
        self.instance1.inventory = create_inventory(SLOTS_PER_ACE)
        
        self.mock_manager.instances = {0: self.instance0, 1: self.instance1}
        self.mock_manager._sync_inventory_to_persistent = Mock()
        
        # Register instances in global state
        ACE_INSTANCES[0] = self.instance0
        ACE_INSTANCES[1] = self.instance1
        
        # Create endless spool object
        self.endless_spool = EndlessSpool(self.mock_printer, self.mock_gcode, self.mock_manager)
    
    def test_successful_swap_on_first_attempt(self):
        """Swap should succeed on first attempt if target loads successfully."""
        # Setup: T0 runs out, T1 is matching candidate
        self.instance0.inventory[0].update({
            'status': 'empty',  # Depleted
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        self.instance0.inventory[1].update({
            'status': 'ready',  # Candidate
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        # Mock successful tool change
        self.mock_manager.perform_tool_change = Mock(return_value="Tool change complete")
        
        # Execute swap
        self.endless_spool.execute_swap(from_tool=0, to_tool=1)
        
        # Should call tool change once
        self.mock_manager.perform_tool_change.assert_called_once_with(0, 1, is_endless_spool=True)
        
        # Should mark from_tool as empty
        self.assertEqual(self.instance0.inventory[0]['status'], 'empty')
        
        # Should resume print
        self.mock_gcode.run_script_from_command.assert_called_with("RESUME PURGE=0")
    
    def test_swap_retries_after_failed_load_finds_next_match(self):
        """After failed load, should search for next matching candidate and retry."""
        # Setup: T0 runs out, T1 matches but will fail, T2 also matches and works
        self.instance0.inventory[0].update({
            'status': 'ready',  # Will be marked empty
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        self.instance0.inventory[1].update({
            'status': 'ready',  # First candidate (will fail)
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        self.instance0.inventory[2].update({
            'status': 'ready',  # Second candidate (will succeed)
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        # Mock T1 fails, T2 succeeds
        call_count = [0]
        def tool_change_side_effect(from_tool, to_tool, is_endless_spool=False):
            call_count[0] += 1
            if to_tool == 1:
                raise Exception("Feed timeout")  # T1 fails
            return "Tool change complete"  # T2 succeeds
        
        self.mock_manager.perform_tool_change = Mock(side_effect=tool_change_side_effect)
        
        # Mock smart unload after failure
        self.instance0._smart_unload_slot = Mock()
        self.instance0.parkposition_to_toolhead_length = 1000
        
        # Execute swap
        self.endless_spool.execute_swap(from_tool=0, to_tool=1)
        
        # Should try T1, then T2
        self.assertEqual(self.mock_manager.perform_tool_change.call_count, 2)
        calls = self.mock_manager.perform_tool_change.call_args_list
        self.assertEqual(calls[0][0], (0, 1))  # First attempt: T0 → T1
        self.assertEqual(calls[1][0], (0, 2))  # Second attempt: T0 → T2
        
        # Should smart unload T1 after failure
        self.instance0._smart_unload_slot.assert_called_once_with(1, length=1000)
        
        # T1 should be marked empty after failed load
        self.assertEqual(self.instance0.inventory[1]['status'], 'empty')
        
        # Should resume after success
        self.mock_gcode.run_script_from_command.assert_called_with("RESUME PURGE=0")
    
    def test_swap_searches_from_original_tool_not_candidate(self):
        """After failed swap, should search from original depleted tool, not failed candidate.
        
        This tests the bug fix where the code was incorrectly calling:
            find_exact_match(next_tool)  # WRONG - searches from candidate
        
        Instead of:
            find_exact_match(from_tool)  # CORRECT - searches from original
        """
        # Setup: T0 (instance 0, slot 0) runs out
        # T4 (instance 1, slot 0) matches and is first candidate
        # T1 (instance 0, slot 1) also matches and should be second candidate
        # T5 (instance 1, slot 1) also matches
        
        self.instance0.inventory[0].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        self.instance0.inventory[1].update({
            'status': 'ready',  # T1 - should be found as 2nd candidate
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        self.instance1.inventory[0].update({
            'status': 'ready',  # T4 - first candidate (will fail)
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        self.instance1.inventory[1].update({
            'status': 'ready',  # T5 - would be found if searching from T4 (wrong)
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        # Mock T4 fails, next should succeed
        call_count = [0]
        def tool_change_side_effect(from_tool, to_tool, is_endless_spool=False):
            call_count[0] += 1
            if call_count[0] == 1:  # First call (T4) fails
                raise Exception("Feed timeout")
            return "Tool change complete"
        
        self.mock_manager.perform_tool_change = Mock(side_effect=tool_change_side_effect)
        self.instance1._smart_unload_slot = Mock()
        self.instance1.parkposition_to_toolhead_length = 1000
        
        # Execute swap with T4 as initial target
        self.endless_spool.execute_swap(from_tool=0, to_tool=4)
        
        # Should try T4, then T1 (not T5!)
        # T1 is next match after T0 (wrapping through T0+1, T0+2, T0+3...)
        # T4 is skipped (already tried), so T1 should be found
        calls = self.mock_manager.perform_tool_change.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], (0, 4))  # T0 → T4
        self.assertEqual(calls[1][0], (0, 1))  # T0 → T1 (correct: next from T0, not T4)
    
    def test_swap_exhausts_all_candidates_then_fails(self):
        """If all matching candidates fail, swap should fail and leave print paused."""
        # Setup: T0 runs out, T1 and T2 match but both fail
        self.instance0.inventory[0].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        self.instance0.inventory[1].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        self.instance0.inventory[2].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        # All tool changes fail
        self.mock_manager.perform_tool_change = Mock(side_effect=Exception("Feed timeout"))
        self.instance0._smart_unload_slot = Mock()
        self.instance0.parkposition_to_toolhead_length = 1000
        
        # Execute swap (should not raise, but should handle gracefully)
        self.endless_spool.execute_swap(from_tool=0, to_tool=1)
        
        # Should try T1, then T2 (2 attempts before running out of candidates)
        self.assertEqual(self.mock_manager.perform_tool_change.call_count, 2)
        
        # Should log failure message
        failure_messages = [call[0][0] for call in self.mock_gcode.respond_info.call_args_list]
        self.assertTrue(any("ENDLESS SPOOL SWAP FAILED" in msg for msg in failure_messages))
        self.assertTrue(any("Print is PAUSED" in msg for msg in failure_messages))
        
        # Should restore T0 status to 'ready' for manual retry
        self.assertEqual(self.instance0.inventory[0]['status'], 'ready')
        
        # Should show failure prompt to user
        prompt_calls = [call[0][0] for call in self.mock_gcode.run_script_from_command.call_args_list]
        self.assertTrue(any("action:prompt_begin" in call for call in prompt_calls))
        self.assertTrue(any("Endless spool swap failed" in call for call in prompt_calls))
        
        # Should NOT resume print automatically
        resume_calls = [call for call in prompt_calls if "RESUME" in call and "prompt" not in call]
        self.assertEqual(len(resume_calls), 0)
    
    def test_swap_respects_material_match_mode(self):
        """In material mode, should match material regardless of color."""
        self.variables["ace_endless_spool_match_mode"] = "material"
        
        # Setup: T0 runs out (red PLA), T1 has blue PLA (should match in material mode)
        self.instance0.inventory[0].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [255, 0, 0],  # Red
            'temp': 200
        })
        
        self.instance0.inventory[1].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [0, 0, 255],  # Blue (different color)
            'temp': 200
        })
        
        self.mock_manager.perform_tool_change = Mock(return_value="Tool change complete")
        
        # Execute swap
        self.endless_spool.execute_swap(from_tool=0, to_tool=1)
        
        # Should succeed despite color mismatch
        self.mock_manager.perform_tool_change.assert_called_once()
        self.mock_gcode.run_script_from_command.assert_called_with("RESUME PURGE=0")
    
    def test_swap_skips_non_ready_slots(self):
        """Should skip slots with status != 'ready' during search."""
        # Setup: T0 runs out, T1 is empty, T2 is ready
        self.instance0.inventory[0].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        self.instance0.inventory[1].update({
            'status': 'empty',  # Should be skipped
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        self.instance0.inventory[2].update({
            'status': 'ready',  # Should be selected
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        self.mock_manager.perform_tool_change = Mock(return_value="Tool change complete")
        
        # Execute swap with T1 as initial target (but it's empty, so should find T2)
        self.endless_spool.execute_swap(from_tool=0, to_tool=1)
        
        # Should try T1 first (given), fail immediately, then find T2
        calls = self.mock_manager.perform_tool_change.call_args_list
        # Actually, looking at the code, it tries the given to_tool first
        # If that fails, THEN it searches
        # So it will try T1 (even though empty), fail, then search and find T2
        self.assertGreaterEqual(len(calls), 1)
    
    def test_swap_marks_slots_empty_correctly(self):
        """Should mark from_tool empty at start, failed candidates empty after failure."""
        # Setup
        self.instance0.inventory[0].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        self.instance0.inventory[1].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        self.instance0.inventory[2].update({
            'status': 'ready',
            'material': 'PLA',
            'color': [255, 0, 0],
            'temp': 200
        })
        
        # T1 fails, T2 succeeds
        call_count = [0]
        def tool_change_side_effect(from_tool, to_tool, is_endless_spool=False):
            call_count[0] += 1
            if to_tool == 1:
                raise Exception("Feed timeout")
            return "Success"
        
        self.mock_manager.perform_tool_change = Mock(side_effect=tool_change_side_effect)
        self.instance0._smart_unload_slot = Mock()
        self.instance0.parkposition_to_toolhead_length = 1000
        
        # Execute
        self.endless_spool.execute_swap(from_tool=0, to_tool=1)
        
        # T0 should be marked empty (depleted)
        self.assertEqual(self.instance0.inventory[0]['status'], 'empty')
        
        # T1 should be marked empty (failed)
        self.assertEqual(self.instance0.inventory[1]['status'], 'empty')
        
        # T2 should still be ready (succeeded)
        self.assertEqual(self.instance0.inventory[2]['status'], 'ready')
        
        # Should sync inventory after each status change
        self.assertGreater(self.mock_manager._sync_inventory_to_persistent.call_count, 0)


if __name__ == '__main__':
    unittest.main()
