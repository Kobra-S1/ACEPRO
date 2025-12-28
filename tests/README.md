# ACE Pro Test Suite

Test suite for the ACE Pro Klipper driver using pytest.

## Overview

The test suite provides unit and integration testing for the ACE Pro multi-material system. Tests are designed to run without actual hardware by mocking Klipper internals and serial communication with ACE devices.

### Test Philosophy

- **Hardware Independence**: All tests use mocks for Klipper objects and serial communication
- **Isolation**: Each test is independent and can run in any order
- **Coverage**: Tests cover commands, configuration, state management, endless spool, runout detection, and tool changes
- **Fast Execution**: Full Test suite should execute in a couple of seconds, keep that in mind if new tests are added

### Key Components

- **`conftest.py`**: Pytest configuration with shared fixtures (mock config, printer, ACE instances)
- **Mock Strategy**: Serial module and Klipper objects are mocked before any imports
- **Fixtures**: Reusable test components for setting up ACE managers, instances, and commands

## Test Files

### Core Functionality Tests

| File | Tests | Description |
|------|-------|-------------|
| **test_commands.py** | 75 | All G-code command handlers (ACE_GET_STATUS, ACE_FEED, ACE_CHANGE_TOOL, etc.) |
| **test_manager.py** | 122 | AceManager core logic, sensor management, tool changes, state tracking, edge cases |
| **test_instance.py** | 37 | AceInstance initialization, configuration, serial communication, RFID query tracking, JSON emission |
| **test_config_utils.py** | 30 | Configuration parsing, tool mapping, inventory creation |

### Feature-Specific Tests

| File | Tests | Description |
|------|-------|-------------|
| **test_endless_spool.py** | 20 | Endless spool match modes (exact/material/next), runout handling |
| **test_endless_spool_swap.py** | 7 | Endless spool swap execution, retry logic, candidate search validation |
| **test_runout_monitor.py** | 51 | Runout detection, sensor polling, toolchange interaction, error handling |
| **test_retry_logic.py** | 8 | Feed/retract retry behavior, FORBIDDEN handling, backoff logic |
| **test_set_and_save.py** | 15 | Persistent variable storage to saved_variables.cfg |
| **test_toolchange_integration.py** | 7 | End-to-end tool change scenarios across multiple ACE units |
| **test_inventory_persistence.py** | 20 | Inventory loading from save_variables, backward compatibility |
| **test_rfid_callback.py** | 19 | RFID callback functionality, temperature calculation, field storage |

**Total: 384 tests** across 12 test modules

## Critical Regression Tests

### Dryer Status Field Validation

Tests `test_cmd_ACE_GET_STATUS_includes_dryer_status` and `test_cmd_ACE_GET_STATUS_dryer_status_empty_when_stopped` validate that the ACE_GET_STATUS command includes dryer status information in its JSON response.

**Why this is critical:**
- KlipperScreen dryer panel relies on these fields to display live temperature and remaining time
- The `dryer_status` field was added to support real-time dryer monitoring
- Without these fields, the UI will show zeros and incorrect status

**Validated fields:**
```json
{
  "instance": 0,
  "status": "ready",
  "temp": 45,  // Current dryer temperature (required for UI)
  "dryer_status": {  // Required field for dryer panel
    "status": "drying",  // Dryer state: "drying"/"stop"/"heater_err"
    "target_temp": 45,  // Target temperature setting
    "duration": 240,  // Total dry duration in minutes
    "remain_time": 14000  // Remaining seconds (for countdown display)
  }
}
```

**What breaks without these tests:**
- Future code changes might remove `dryer_status` from the response thinking it's unused
- KlipperScreen dryer panel would show "stopped" and 0°C for all dryers
- Users would lose visibility into dryer operation status

**Test coverage:**
- `test_cmd_ACE_GET_STATUS_includes_dryer_status`: Validates full structure when dryer is active
- `test_cmd_ACE_GET_STATUS_dryer_status_empty_when_stopped`: Validates field exists even when dryer is stopped

These tests ensure the critical communication path between Klipper and KlipperScreen dryer UI remains functional.

### RFID Query Tracking Tests

Tests in `TestRfidQueryTracking` class (`test_instance.py`) validate that RFID data queries are sent exactly once per spool insertion, preventing console spam while ensuring data is always fetched when needed.

**Why this is critical:**
- ACE heartbeat runs at 1Hz - without guards, RFID queries would flood the console every second
- Async callbacks complete after next heartbeat starts - need to track in-flight queries
- Failed queries must not cause infinite retry loops
- Spool removal/reinsertion must allow fresh queries

**Problem solved:**
```
# Without tracking (BAD - floods console):
ACE[0]: Slot 0 querying full RFID data...  # Every heartbeat!
ACE[0]: Slot 1 querying full RFID data...
ACE[0]: Slot 2 querying full RFID data...
ACE[0]: Slot 3 querying full RFID data...
# Repeats forever...

# With tracking (GOOD - one query per spool):
ACE[0]: Slot 0 querying full RFID data...  # Once at startup
ACE[0]: Slot 0 RFID full data -> sku=..., temp=215°C
# No more queries until spool is removed and reinserted
```

**Test coverage (10 tests):**

| Test | What it proves |
|------|----------------|
| `test_init_creates_empty_tracking_sets` | Both tracking sets start empty |
| `test_rfid_query_only_sent_once_per_slot` | First heartbeat triggers query, subsequent don't spam |
| `test_query_not_sent_when_already_pending` | No duplicate queries while one is in-flight |
| `test_empty_slot_clears_query_tracking` | Removing spool clears both tracking sets |
| `test_reinserted_spool_triggers_new_query` | After empty→ready, query is allowed again |
| `test_rfid_query_not_blocked_after_callback_completes` | System doesn't get stuck after success |
| `test_rfid_query_not_blocked_after_failed_callback` | System doesn't get stuck after failure |
| `test_query_skipped_when_already_has_rfid_data` | No unnecessary queries if data present |
| `test_multiple_slots_tracked_independently` | Slot 0 tracking doesn't affect slot 1 |
| `test_query_rfid_full_data_guards_prevent_duplicate` | Direct test of pending guard |

**What breaks without these tests:**
- Code changes might remove tracking sets thinking they're unused → console spam returns
- Changes to callback logic might leave pending flag set → queries never happen
- Changes to empty slot handling might not clear flags → stale data on spool swap

## Running Tests

### Prerequisites

```bash
cd /home/pi/ACEPRO

# Install test dependencies (one time)
pip install -r tests/requirements-test.txt
```

### Run All Tests

```bash
# From project root
python -m pytest tests/

# From tests directory
cd tests
pytest

# Using the shell script
./tests/run_all_tests.sh
```

### Run Specific Test File

```bash
# Single file
python -m pytest tests/test_commands.py

# Multiple files
python -m pytest tests/test_commands.py tests/test_manager.py
```

### Run Specific Test Class or Function

```bash
# Run all tests in a class
python -m pytest tests/test_commands.py::TestCommandsModuleStructure

# Run a specific test method
python -m pytest tests/test_commands.py::TestCommandsModuleStructure::test_module_imports

# Run tests matching a pattern
python -m pytest tests/ -k "endless_spool"
python -m pytest tests/ -k "test_cmd_ACE"
```

### Verbose Output

```bash
# Show each test name as it runs
python -m pytest tests/ -v

# Show print statements
python -m pytest tests/ -s

# Show detailed failure info
python -m pytest tests/ -vv

# Combine flags
python -m pytest tests/ -vvs
```

### Run Tests by Category

```bash
# All command tests
python -m pytest tests/test_commands.py -v

# All manager tests
python -m pytest tests/test_manager.py -v

# All endless spool tests
python -m pytest tests/test_endless_spool.py -v

# All runout detection tests
python -m pytest tests/test_runout_monitor.py -v

# All toolchange integration tests
python -m pytest tests/test_toolchange_integration.py -v
```

### Fast Fail

```bash
# Stop on first failure
python -m pytest tests/ -x

# Stop after N failures
python -m pytest tests/ --maxfail=3
```

### Show Test Summary

```bash
# Show slowest tests
python -m pytest tests/ --durations=10

# Show only failed tests
python -m pytest tests/ --tb=short

# Show only test names (quiet)
python -m pytest tests/ -q
```

## Test Coverage Examples

### Command Tests (`test_commands.py`)

Tests for all 37 G-code commands:

```python
# Status and query commands
test_cmd_ACE_GET_STATUS_single_instance
test_cmd_ACE_GET_STATUS_all_instances
test_cmd_ACE_GET_STATUS_includes_dryer_status  # CRITICAL: Validates dryer_status field
test_cmd_ACE_GET_STATUS_dryer_status_empty_when_stopped  # CRITICAL: Validates field exists when stopped
test_cmd_ACE_GET_CURRENT_INDEX
test_cmd_ACE_QUERY_SLOTS_all
test_cmd_ACE_QUERY_SLOTS_single

# Feed/retract operations
test_cmd_ACE_FEED
test_cmd_ACE_STOP_FEED
test_cmd_ACE_RETRACT
test_cmd_ACE_STOP_RETRACT

# Smart operations
test_cmd_ACE_SMART_LOAD
test_cmd_ACE_SMART_UNLOAD
test_cmd_ACE_FULL_UNLOAD_single
test_cmd_ACE_FULL_UNLOAD_all

# Tool changes
test_cmd_ACE_CHANGE_TOOL_WRAPPER

# Inventory management
test_cmd_ACE_SET_SLOT_empty
test_cmd_ACE_SET_SLOT_with_material
test_cmd_ACE_SET_SLOT_named_color_red
test_cmd_ACE_SET_SLOT_named_color_blue
test_cmd_ACE_SET_SLOT_named_color_case_insensitive
test_cmd_ACE_SET_SLOT_named_color_orange
test_cmd_ACE_SET_SLOT_named_color_orca
test_cmd_ACE_SET_SLOT_rgb_fallback_still_works
test_cmd_ACE_SET_SLOT_invalid_named_color
test_cmd_ACE_SET_SLOT_emits_inventory_notification
test_cmd_ACE_SET_SLOT_empty_emits_notification
test_cmd_ACE_SAVE_INVENTORY
test_cmd_ACE_RESET_PERSISTENT_INVENTORY

# Endless spool
test_cmd_ACE_ENABLE_ENDLESS_SPOOL
test_cmd_ACE_DISABLE_ENDLESS_SPOOL
test_cmd_ACE_SET_ENDLESS_SPOOL_MODE_exact
test_cmd_ACE_SET_ENDLESS_SPOOL_MODE_material
test_cmd_ACE_SET_ENDLESS_SPOOL_MODE_next
test_cmd_ACE_GET_ENDLESS_SPOOL_MODE

# RFID sync
test_cmd_ACE_ENABLE_RFID_SYNC_single
test_cmd_ACE_DISABLE_RFID_SYNC_single
test_cmd_ACE_RFID_SYNC_STATUS_single

# Debug commands
test_cmd_ACE_DEBUG
test_cmd_ACE_DEBUG_SENSORS
test_cmd_ACE_DEBUG_STATE
test_cmd_ACE_DEBUG_INJECT_SENSOR_STATE_reset
test_cmd_ACE_DEBUG_INJECT_SENSOR_STATE_set
```

### Manager Tests (`test_manager.py`)

Core ACE manager functionality:

```python
# Initialization and setup
test_initialization_single_ace
test_initialization_multiple_ace
test_printer_reference
test_config_validation

# Sensor management
test_get_switch_state_toolhead
test_get_switch_state_rdm
test_is_filament_path_free
test_sensor_override_injection

# Tool mapping
test_tool_to_instance_mapping
test_get_instance_from_tool
test_get_local_slot

# State management
test_set_and_save_variable
test_get_variable
test_filament_position_tracking
test_current_tool_tracking

# Smart operations
test_smart_unload_success
test_smart_unload_fallback
test_smart_load_all_slots

# Tool changes
test_perform_tool_change_t0_to_t1
test_perform_tool_change_same_tool
test_perform_tool_change_unload
test_perform_tool_change_cross_instance

# Tool change edge cases (NEW)
test_invalid_state_nozzle_filled_rdm_empty_raises  # Stuck filament detection
test_state_mismatch_nozzle_state_but_sensor_empty_corrects_to_bowden
test_state_mismatch_nozzle_state_but_sensor_empty_corrects_to_splitter
test_target_tool_not_managed_raises  # Invalid tool index handling
test_unload_failure_raises_exception  # Failed unload error path
test_reselection_with_path_blocked_clears_then_loads
test_plausibility_mismatch_unload_fails_raises
test_unknown_filament_pos_with_sensor_triggered_unloads
test_unknown_filament_pos_sensor_clear_corrects_state
```

### Endless Spool Tests (`test_endless_spool.py`)

Automatic filament switching:

```python
# Match modes
test_exact_match_material_and_color
test_material_match_ignore_color
test_next_ready_ignore_all

# Runout scenarios
test_runout_finds_exact_match
test_runout_falls_back_to_material_match
test_runout_uses_next_ready
test_no_match_stays_paused

# Cross-instance
test_match_across_multiple_ace_units
test_prioritize_same_instance
```

### Endless Spool Swap Tests (`test_endless_spool_swap.py`)

Endless spool swap execution and retry logic:

```python
# Successful swaps
test_successful_swap_on_first_attempt  # Happy path - T0 → T1 works
test_swap_marks_slots_empty_correctly  # Inventory state updates

# Retry and recovery
test_swap_retries_after_failed_load_finds_next_match  # T0 → T1 fails → T2 succeeds
test_swap_searches_from_original_tool_not_candidate  # Bug fix: search from T0, not failed T1
test_swap_skips_non_ready_slots  # Only attempts 'ready' tools
test_swap_exhausts_all_candidates_then_fails  # No matches left → show prompt

# Match mode respect
test_swap_respects_material_match_mode  # Material-only matching works
```

### Retry Logic Tests (`test_retry_logic.py`)

Feed and retract retry behavior:

```python
# Feed retries
test_feed_retries_on_forbidden_response  # FORBIDDEN → retry after 2s
test_feed_no_retry_on_non_forbidden  # Other errors → immediate fail
test_feed_no_retry_on_no_response  # No response → immediate fail
test_feed_retries_until_max_attempts  # Max 3 attempts with backoff

# Retract retries
test_retract_retries_on_forbidden_response  # FORBIDDEN → retry after 2s
test_retract_no_retry_on_non_forbidden  # Other errors → immediate fail
test_retract_no_retry_on_no_response  # No response → immediate fail
test_retract_retries_until_max_attempts  # Max 3 attempts with backoff
```

### Runout Monitor Tests (`test_runout_monitor.py`)

Filament detection:

```python
# Detection
test_runout_detection_enabled
test_runout_detection_disabled
test_sensor_state_change_triggers_runout
test_baseline_establishment

# Toolchange interaction
test_no_runout_detection_during_toolchange
test_runout_blocked_by_toolchange_flag
test_baseline_reset_after_toolchange_respected
test_sensor_glitch_after_toolchange_triggers_runout

# Pause/resume
test_paused_state_resets_baseline
test_baseline_reestablished_after_pause_resume

# Error handling (NEW)
test_pause_command_error_logged  # PAUSE failure graceful handling
test_handle_runout_sets_handling_flag  # Flag management
test_print_stats_exception_handled  # Graceful stats error recovery

# Auto-recovery (NEW)
test_auto_recovery_triggers_when_detection_should_be_active

# Edge cases (NEW)
test_runout_with_invalid_instance_uses_defaults  # Invalid tool lookup
test_runout_handling_exception_clears_flag  # Flag cleared in finally
test_runout_closes_prompt_before_swap  # Prompt lifecycle
test_runout_with_none_ace_instance  # None instance handling
```

### Toolchange Integration Tests (`test_toolchange_integration.py`)

End-to-end scenarios:

```python
# Basic tool changes
test_load_tool_ace0_from_empty
test_load_tool_ace1_from_empty
test_reactivate_same_tool

# Cross-ACE changes
test_toolchange_ace0_to_ace1
test_toolchange_ace1_to_ace0

# With validation
test_toolchange_with_sensor_validation

# Disabled state
test_ace_disabled_no_toolchange
```

### Inventory Persistence Tests (`test_inventory_persistence.py`)

Loading and saving inventory to persistent storage:

```python
# File handling
test_load_inventory_missing_file
test_load_inventory_file_exists_but_empty
test_load_inventory_corrupted_json

# Structure compatibility
test_load_inventory_old_structure_no_rfid_field
test_load_inventory_old_structure_all_slots
test_load_inventory_extended_structure_with_sku_brand

# Field handling
test_load_inventory_missing_optional_fields_in_slot
test_inventory_preserves_sku_brand_through_restart
test_new_fields_dont_break_backward_compat

# Edge cases
test_partial_inventory_missing_slots
test_inventory_with_extra_unknown_fields
test_load_inventory_instance_not_present
```

### RFID Callback Tests (`test_rfid_callback.py`)

RFID data capture and processing:

```python
# Request sending
test_sends_correct_request

# Temperature calculation modes
test_temp_mode_average
test_temp_mode_min
test_temp_mode_max
test_temp_fallback_when_no_rfid_temp
test_temp_mode_min_falls_back_to_max_when_min_zero
test_temp_mode_max_falls_back_to_min_when_max_zero

# Field storage
test_all_rfid_fields_stored
test_partial_rfid_fields_stored
test_syncs_to_persistent_storage
test_emits_inventory_update

# Error handling
test_handles_error_response
test_handles_no_response
test_handles_missing_result
test_handles_invalid_slot_index

# Config parsing
test_config_default_is_average
test_config_accepts_min
test_config_accepts_max
test_config_invalid_value_defaults_to_average
```

## Development Workflow

### Adding New Tests

1. Choose appropriate test file based on functionality
2. Add test method to existing test class or create new class
3. Use fixtures from `conftest.py` for setup
4. Mock external dependencies (serial, sensors, Klipper objects)
5. Assert expected behavior
6. Run test to verify: `pytest tests/test_yourfile.py::TestClass::test_new_feature -v`

### Test Structure Template

```python
class TestNewFeature:
    """Test description."""
    
    def test_feature_basic_case(self):
        """Test basic functionality."""
        # Setup
        manager = create_mock_manager()
        
        # Execute
        result = manager.some_operation()
        
        # Assert
        assert result == expected_value
    
    def test_feature_edge_case(self):
        """Test edge case handling."""
        # Setup with edge condition
        # Execute
        # Assert error handling or fallback
```

### Debugging Failed Tests

```bash
# Show full output for specific test
python -m pytest tests/test_manager.py::TestManager::test_smart_unload -vvs

# Drop into debugger on failure
python -m pytest tests/ --pdb

# Show local variables on failure
python -m pytest tests/ -l
```

## Configuration Files

- **`pytest.ini`**: Pytest configuration (test discovery patterns, output options)
- **`.coveragerc`**: Code coverage configuration
- **`.flake8`**: Code style checking configuration
- **`requirements-test.txt`**: Test dependencies (pytest, pytest-mock, coverage)

## CI/CD Integration

Tests are designed to run in CI/CD pipelines:

```bash
# Quick smoke test (stops on first failure)
pytest tests/ -x -q

# Full test with coverage report
pytest tests/ --cov=ace --cov-report=term-missing

# Generate XML report for CI
pytest tests/ --junitxml=test-results.xml
```

## Troubleshooting

### Import Errors

If you see import errors:
```bash
# Ensure you're in project root
cd /home/pi/ACEPRO

# Run with python -m pytest (not pytest alone)
python -m pytest tests/
```

### Serial Module Errors

Tests mock the serial module in `conftest.py`. If you see serial-related errors, ensure:
- You're running tests through pytest (not directly with python)
- `conftest.py` is in the tests directory
- Mocks are configured before imports

### Klipper Object Errors

Tests provide mock Klipper objects. Real Klipper is not required for testing.

## Performance

- **Full test suite**: ~5 seconds (340 tests)
- **Single test file**: ~0.5-1 second
- **Single test**: <0.1 second

Fast execution enables rapid development iteration.

## Contributing

When contributing new functionality:

1. Write tests first (TDD approach recommended)
2. Ensure all existing tests still pass
3. Add tests for new features and bug fixes
4. Aim for >80% code coverage for new code
5. Run full test suite before submitting: `pytest tests/ -v`

## Resources

- [Pytest Documentation](https://docs.pytest.org/)
- [unittest.mock Guide](https://docs.python.org/3/library/unittest.mock.html)
- [ACE Pro Architecture](../ARCHITECTURE.md)
- [Example Commands](../example_cmds.txt)
