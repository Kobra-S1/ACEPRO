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
| **test_commands.py** | 88 | All G-code command handlers, instance/slot resolution, parameter validation |
| **test_manager.py** | 122 | AceManager core logic, sensor management, tool changes, state tracking, edge cases |
| **test_instance.py** | 42 | AceInstance initialization, configuration, serial communication, RFID query tracking, non-RFID default handling, JSON emission |
| **test_config_utils.py** | 30 | Configuration parsing, tool mapping, inventory creation |
| **test_serial_manager.py** | 34 | USB location parsing, CRC calculation, frame parsing, status change detection |

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

**Total: 435 tests** across 13 test modules


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
test_cmd_ACE_SET_SLOT_rgb_values_not_validated  # Documents current behavior
test_cmd_ACE_SET_SLOT_rgb_with_whitespace
test_cmd_ACE_SET_SLOT_rgb_wrong_count
test_cmd_ACE_SAVE_INVENTORY
test_cmd_ACE_RESET_PERSISTENT_INVENTORY

# Instance/slot resolution (ace_get_instance_and_slot)
test_with_t_param_returns_instance_and_slot  # T=2 → instance 0, slot 2
test_with_t_param_tool_0                      # T=0 → slot 0
test_with_t_param_tool_3                      # T=3 → slot 3 (last on instance 0)
test_with_instance_and_index_params           # INSTANCE=0 INDEX=2
test_raises_error_when_neither_provided
test_raises_error_for_instance_only           # INSTANCE without INDEX
test_raises_error_for_index_only              # INDEX without INSTANCE
test_t_param_unmanaged_tool_raises_error      # T=99 → error
test_t_param_takes_priority_over_instance_index

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

### Serial Manager Tests (`test_serial_manager.py`)

Low-level serial protocol and pure logic functions:

```python
# USB location parsing
test_parse_simple_location              # "1-1.4" → (1, 1, 4)
test_parse_complex_location_with_colon  # "1-1.4.3:1.0" → (1, 1, 4, 3)
test_parse_acm_fallback                 # "acm.2" → (999998, 2)
test_parse_acm_fallback_zero            # "acm.0" → (999998, 0)
test_acm_sorts_after_usb_before_unknown # USB < ACM < unknown
test_parse_empty_string_returns_high_value
test_parse_none_returns_high_value
test_parse_invalid_location_returns_high_value
test_sorting_order_is_correct           # Multi-device sort validation

# CRC calculation
test_crc_empty_buffer                   # Initial value 0xFFFF
test_crc_deterministic                  # Same input → same CRC
test_crc_different_for_different_input  # Different payloads differ
test_crc_is_16bit                       # Result fits in 16 bits

# Frame parsing
test_parse_valid_frame                  # Valid frame decoded
test_parse_multiple_frames              # Multiple frames in buffer
test_incomplete_frame_stays_in_buffer   # Partial frame preserved
test_invalid_crc_rejected               # Bad CRC discarded
test_resync_on_junk_data                # Recovers after junk bytes
test_invalid_terminator_resyncs         # Bad terminator → resync

# Status change detection
test_detects_status_change              # Status transitions logged
test_no_log_when_status_unchanged       # No noise on same state
test_detects_slot_status_change         # Per-slot tracking
test_detects_dryer_status_change        # Dryer state monitoring
test_detects_significant_temp_change    # ≥5°C temp changes logged
test_ignores_small_temp_change          # <5°C ignored
test_handles_missing_result             # Graceful error response
test_handles_empty_response             # None/empty handled

# Queue management
test_high_priority_dequeued_first       # HP before normal
test_clear_queues_empties_all           # Clear works completely
test_has_pending_requests_detects_queued
test_has_pending_requests_detects_inflight

# Response dispatch
test_dispatch_returns_callback_for_known_id
test_dispatch_returns_none_for_unsolicited
test_dispatch_handles_missing_id
```

**Bug fixed**: ACM fallback locations now sort correctly as `(999998, n)` instead of incorrectly returning `(999999,)` for all ACM devices.

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
