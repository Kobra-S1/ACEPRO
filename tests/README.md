# ACE Pro Test Suite

Test suite for the ACE Pro Klipper driver using pytest.

## Quick Start

### Syntax Validation (Fast Check)

Before deploying or running full tests, validate all Python files compile:

```bash
# Quick syntax check (runs in seconds)
./tests/check_syntax.sh

# Or run directly with Python
python3 tests/check_syntax.py
```

This catches:
- Syntax errors (unterminated strings, invalid syntax)
- Multi-line f-string issues (Klipper parser compatibility)
- Import errors and compilation failures

**Always run this before `git push` or Klipper restart!**

### Full Test Suite

```bash
# Run all tests with coverage
./tests/run_all_tests.sh

# Run specific test file
pytest tests/test_commands.py -v

# Run specific test
pytest tests/test_commands.py::TestSafeWrapper::test_safe_wrapper_success -v
```

## Overview

The test suite provides unit and integration testing for the ACE Pro multi-material system. Tests are designed to run without actual hardware by mocking Klipper internals and serial communication with ACE devices.

### Test Philosophy

- **Hardware Independence**: All tests use mocks for Klipper objects and serial communication - no physical ACE hardware required
- **Isolation**: Each test is independent and can run in any order without side effects
- **Coverage**: Tests cover commands, configuration, state management, endless spool, runout detection, tool changes, retry logic, and color conversion
- **Fast Execution**: Full test suite executes in ~9 seconds (874 tests), enabling rapid development iteration
- **Realistic Mocking**: Mocks simulate actual ACE behavior including timing, state transitions, and error conditions

### Key Components

- **`conftest.py`**: Pytest configuration with shared fixtures (mock config, printer, ACE instances)
- **Mock Strategy**: Serial module and Klipper objects are mocked before any imports
- **Fixtures**: Reusable test components for setting up ACE managers, instances, and commands

## Test Files

### Core Functionality Tests

| File | Tests | Description |
|------|-------|-------------|
| **test_commands.py** | 173 | G-code command handlers, safe error wrapper, instance/slot resolution, parameter validation, verbose status formatting, tool-change paths, sensor debug, color name parsing |
| **test_manager.py** | 138 | AceManager core logic, sensor management, tool changes, state tracking, edge cases, invalid state detection, plausibility checks |
| **test_instance.py** | 105 | AceInstance initialization, configuration, serial communication, RFID query tracking, non-RFID default handling, inventory write optimization, deferred feed assist restoration, JSON emission, feed/retract operations |
| **test_init_module.py** | 2 | Module entrypoint `load_config` wiring: manager creation, command registration, object attachment with/without instances |
| **test_config_utils.py** | 51 | Configuration parsing, tool mapping, inventory creation, instance-specific overrides |
| **test_serial_manager.py** | 162 | USB location parsing, CRC calculation, connection lifecycle/backoff, request windowing, status change detection, resync logic, ACM fallback, frame parsing, timeout handling |
| **test_topology_validation.py** | 24 | USB topology position calculation, enumeration validation, instance binding to physical ACEs, feed assist restoration with topology checks, reconnection scenarios |

### Feature-Specific Tests

| File | Tests | Description |
|------|-------|-------------|
| **test_endless_spool.py** | 33 | Endless spool match modes (exact/material/next), runout handling, cross-instance matching, priority logic |
| **test_endless_spool_swap.py** | 7 | Endless spool swap execution, retry logic, candidate search validation, inventory state updates |
| **test_runout_monitor.py** | 61 | Runout detection, sensor polling, toolchange interaction, error handling, auto-recovery, pause/resume, baseline establishment |
| **test_retry_logic.py** | 8 | Feed/retract retry behavior using MAX_RETRIES constant, FORBIDDEN handling, backoff delays between attempts |
| **test_set_and_save.py** | 15 | Persistent variable storage to saved_variables.cfg, type conversion (bool/str/dict/list/number) |
| **test_toolchange_integration.py** | 7 | End-to-end tool change scenarios across multiple ACE units, cross-instance changes, sensor validation |
| **test_inventory_persistence.py** | 20 | Inventory loading from save_variables, backward compatibility, old structure migration, field handling |
| **test_inventory_restart_scenarios.py** | 9 | Inventory persistence across restarts, empty/RFID/manual slot preservation, state transitions |
| **test_repeated_status_updates.py** | 3 | Status update handling, multiple consecutive updates, stability |
| **test_rfid_callback.py** | 19 | RFID callback functionality, temperature calculation modes (min/max/average), field storage, sync to persistent storage |
| **test_rgb_to_mainsail_color.py** | 37 | RGB to Mainsail color name mapping using HSV conversion, grayscale handling, hue boundaries, saturation thresholds |

**Total: 874 tests** across 18 test modules


## Running Tests

### Prerequisites

```bash
cd /home/janni/git/ACEPRO

# Install test dependencies (one time)
pip install -r tests/requirements-test.txt
```

### Run All Tests

```bash
# From project root (recommended)
python -m pytest tests/

# Using the shell script (includes PEP 8 checks)
bash tests/run_all_tests.sh

# Quick run without coverage
python -m pytest tests/ -v
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

### Command Tests (`test_commands.py` - 173 tests)

Tests for all G-code commands including color name parsing:

```python
# Status and query commands
test_cmd_ACE_GET_STATUS_single_instance
test_cmd_ACE_GET_STATUS_all_instances
test_cmd_ACE_GET_STATUS_includes_dryer_status
test_cmd_ACE_GET_STATUS_verbose_formatting
test_cmd_ACE_GET_CURRENT_INDEX
test_cmd_ACE_QUERY_SLOTS_all
test_cmd_ACE_QUERY_SLOTS_disconnected_cached_data
test_cmd_ACE_GET_CONNECTION_STATUS

# Feed/retract operations
test_cmd_ACE_FEED
test_cmd_ACE_STOP_FEED
test_cmd_ACE_RETRACT
test_cmd_ACE_STOP_RETRACT
test_cmd_ACE_SET_FEED_SPEED
test_cmd_ACE_SET_RETRACT_SPEED

# Smart operations
test_cmd_ACE_SMART_LOAD
test_cmd_ACE_SMART_UNLOAD
test_cmd_ACE_FULL_UNLOAD_single
test_cmd_ACE_FULL_UNLOAD_all

# Tool changes
test_cmd_ACE_CHANGE_TOOL_WRAPPER
test_change_tool_disabled_global
test_change_tool_unload_path_success
test_change_tool_perform_success_with_homing

# Inventory management with color names
test_cmd_ACE_SET_SLOT_empty
test_cmd_ACE_SET_SLOT_with_material
test_cmd_ACE_SET_SLOT_named_color_red         # Named color: RED
test_cmd_ACE_SET_SLOT_named_color_blue        # Named color: BLUE
test_cmd_ACE_SET_SLOT_named_color_orange      # Named color: ORANGE
test_cmd_ACE_SET_SLOT_named_color_orca        # Named color: ORCA (teal)
test_cmd_ACE_SET_SLOT_named_color_case_insensitive  # "red" = "RED"
test_cmd_ACE_SET_SLOT_rgb_fallback            # Numeric R,G,B still works
test_cmd_ACE_SET_SLOT_invalid_named_color     # Unknown name → error
test_cmd_ACE_SAVE_INVENTORY
test_cmd_ACE_RESET_PERSISTENT_INVENTORY
test_cmd_ACE_RESET_ACTIVE_TOOLHEAD

# Feed assist
test_cmd_ACE_ENABLE_FEED_ASSIST
test_cmd_ACE_DISABLE_FEED_ASSIST

# Dryer control
test_cmd_ACE_START_DRYING
test_cmd_ACE_STOP_DRYING

# Instance/slot resolution
test_with_t_param_returns_instance_and_slot
test_with_instance_and_index_params
test_t_param_takes_priority_over_instance_index
test_raises_error_when_neither_provided

# Endless spool
test_cmd_ACE_ENABLE_ENDLESS_SPOOL
test_cmd_ACE_DISABLE_ENDLESS_SPOOL
test_cmd_ACE_SET_ENDLESS_SPOOL_MODE
test_cmd_ACE_GET_ENDLESS_SPOOL_MODE
test_cmd_ACE_ENDLESS_SPOOL_STATUS

# RFID sync
test_cmd_ACE_ENABLE_RFID_SYNC_single
test_cmd_ACE_ENABLE_RFID_SYNC_all
test_cmd_ACE_DISABLE_RFID_SYNC_single
test_cmd_ACE_RFID_SYNC_STATUS

# Debug commands
test_cmd_ACE_DEBUG
test_cmd_ACE_DEBUG_SENSORS
test_cmd_ACE_DEBUG_STATE
test_cmd_ACE_DEBUG_INJECT_SENSOR_STATE
test_cmd_ACE_DEBUG_CHECK_SPOOL_READY

# Error handling
test_safe_gcode_command_prompt_on_exception
test_safe_gcode_command_handles_dialog_error
```

### Init Module Tests (`test_init_module.py`)

Entrypoint wiring:

```python
test_load_config_registers_commands_and_instances  # Adds manager instances to printer, registers commands
test_load_config_handles_no_instances              # Safe when manager reports no instances
```

### Manager Tests (`test_manager.py` - 138 tests)

Core ACE manager functionality including state validation:

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
test_has_rdm_sensor_detection

# Tool mapping
test_tool_to_instance_mapping
test_get_instance_from_tool
test_get_local_slot
test_invalid_tool_index_handling

# State management
test_set_and_save_variable
test_get_variable
test_filament_position_tracking
test_current_tool_tracking

# Smart operations
test_smart_unload_success
test_smart_unload_fallback
test_smart_load_all_slots
test_smart_unload_sensor_validation

# Tool changes with validation
test_perform_tool_change_t0_to_t1
test_perform_tool_change_same_tool
test_perform_tool_change_unload
test_perform_tool_change_cross_instance
test_perform_tool_change_with_homing

# State validation and plausibility checks
test_invalid_state_nozzle_filled_rdm_empty_raises  # Detects stuck filament
test_state_mismatch_nozzle_state_but_sensor_empty_corrects_to_bowden
test_state_mismatch_nozzle_state_but_sensor_empty_corrects_to_splitter
test_target_tool_not_managed_raises
test_unload_failure_raises_exception
test_reselection_with_path_blocked_clears_then_loads
test_plausibility_mismatch_unload_fails_raises
test_unknown_filament_pos_with_sensor_triggered_unloads
test_unknown_filament_pos_sensor_clear_corrects_state
```

### Endless Spool Tests (`test_endless_spool.py` - 33 tests)

Automatic filament switching on runout:

```python
# Match modes
test_exact_match_material_and_color
test_material_match_ignore_color
test_next_ready_ignore_all
test_next_mode_prioritizes_same_instance

# Runout scenarios
test_runout_finds_exact_match
test_runout_falls_back_to_material_match
test_runout_uses_next_ready
test_no_match_stays_paused
test_runout_skips_empty_slots
test_runout_skips_current_tool

# Cross-instance matching
test_match_across_multiple_ace_units
test_prioritize_same_instance
test_cross_instance_fallback

# Edge cases
test_disabled_endless_spool_stays_paused
test_endless_spool_during_toolchange_ignored
test_match_respects_color_tolerance
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

### Retry Logic Tests (`test_retry_logic.py` - 8 tests)

Feed and retract retry behavior using MAX_RETRIES constant:

```python
# Feed retries
test_feed_succeeds_on_first_attempt
test_feed_retries_on_forbidden_then_succeeds  # FORBIDDEN → wait 1s → retry
test_feed_exhausts_retries_on_repeated_forbidden  # MAX_RETRIES attempts then fail
test_feed_fails_immediately_on_non_forbidden_error  # Other errors don't retry

# Retract retries
test_retract_succeeds_on_first_attempt
test_retract_retries_on_no_response  # Timeout → wait 2s → retry up to MAX_RETRIES
test_retract_succeeds_on_second_attempt_after_forbidden
test_retract_waits_between_retries  # Validates 2s delay between attempts
```

**Note**: Tests use `MAX_RETRIES` constant from config (currently 6), not hardcoded values.

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

### Instance Tests (`test_instance.py` - 105 tests)

AceInstance core functionality and feed/retract operations:

```python
# Initialization
test_instance_initialization
test_instance_second_unit
test_manager_property_returns_manager
test_manager_property_returns_none_if_not_registered

# Status callback handling
test_status_update_inventory_sync_enabled
test_status_update_rfid_sync_disabled_defaults_and_clears
test_status_update_default_fill_when_missing_metadata
test_heartbeat_callback_updates_status
test_is_ready_true
test_is_ready_false

# Non-RFID default handling
test_non_rfid_spool_gets_default_material_and_temp
test_non_rfid_spool_no_rfid_key_gets_defaults
test_empty_to_ready_preserves_saved_metadata
test_empty_to_ready_clears_old_rfid_data_for_non_rfid_spool
test_rfid_sync_disabled_with_non_rfid_spool_gets_defaults
test_default_values_match_class_constants

# Inventory write optimization
test_inventory_write_only_on_change
test_inventory_write_all_fields_on_change
test_inventory_write_detects_each_field_change

# Empty slot handling during printing
test_empty_slot_preserves_material_when_printing
test_empty_slot_preserves_material_when_paused
test_empty_slot_clears_material_when_not_printing
test_swap_rfid_to_non_rfid_during_printing

# Feed/retract operations
test_retract_skips_when_slot_empty
test_retract_success_with_callbacks
test_retract_stops_early_when_slot_becomes_empty
test_retract_retries_on_missing_response
test_feed_sends_request
test_stop_feed_sends_command
test_change_retract_speed_branches
test_change_feed_speed_branches

# Feed assist
test_enable_feed_assist_success
test_disable_feed_assist_happy_path
test_disable_feed_assist_mismatched_slot_noop
test_get_current_feed_assist_index_initial
test_update_feed_assist_enable
test_update_feed_assist_disable
test_on_ace_connect_defers_feed_assist_restore
test_on_ace_connect_restores_feed_assist_after_heartbeat
test_on_ace_connect_skips_when_disabled
test_on_ace_connect_skips_when_no_previous_feed_assist

# Feed to toolhead
test_feed_filament_into_toolhead_success_flow
test_feed_filament_into_toolhead_validates_tool
test_feed_filament_into_toolhead_sensor_check
test_feed_filament_into_toolhead_rdm_sensor_check
test_feed_filament_into_toolhead_skips_precondition_check
test_feed_filament_into_toolhead_exception_with_cleanup

# Sensor monitoring
test_make_sensor_trigger_monitor_records_timing
test_sensor_monitor_initialization
test_sensor_monitor_detects_trigger
test_sensor_monitor_no_state_change
test_sensor_monitor_call_count_increments

# Smart unload with sensors
test_rdm_mode_clear_path_returns_true
test_rdm_mode_raises_when_path_stays_blocked
test_rdm_mode_extra_retract_and_rdm_recovery
test_no_rdm_toolhead_clear
test_no_rdm_toolhead_blocked_returns_false
test_smart_unload_unexpected_error_calls_stop_and_reraises

# Inventory management
test_reset_persistent_inventory
test_reset_feed_assist_state
test_get_status_contains_expected_keys
test_get_status_returns_copy

# Feed filament to verification sensor
test_feed_raises_if_sensor_already_triggered
test_feed_raises_when_sensor_never_triggers
test_feed_reaches_rdm_and_sets_splitter_state
test_incremental_feed_reaches_toolhead

# Extruder operations
test_moves_and_waits_when_requested
test_moves_without_wait_by_default
test_skips_zero_length_move

# RDM triggered unload
test_returns_false_when_no_rdm_sensor
test_successful_clear_stops_and_restores_feed_assist
test_timeout_returns_false_and_restores_feed_assist

# Tool macros
test_registers_all_tool_macros
test_logs_failure_on_exception

# Wait operations
test_wait_ready_returns_when_ready
test_wait_ready_times_out
test_wait_ready_calls_on_wait_cycle_and_triggers_status_request
test_wait_for_condition_success
test_wait_for_condition_timeout
test_dwell_pauses_reactor

# Feed with wait for response
test_success_returns_response
test_timeout_returns_error
test_done_without_response_uses_fallback

# Feed to toolhead with extruder assist
test_happy_path_stops_on_sensor_and_changes_speed
test_speed_change_failure_raises_after_retries
test_feed_assist_timeout_raises_when_sensor_never_triggers
```

### Serial Manager Tests (`test_serial_manager.py` - 162 tests)

Low-level serial protocol and connection management:

```python
# USB location parsing and sorting
test_parse_simple_location              # "1-1.4" → (1, 1, 4)
test_parse_complex_location_with_colon  # "1-1.4.3:1.0" → (1, 1, 4, 3)
test_parse_acm_fallback                 # "acm.2" → (999998, 2)
test_parse_acm_fallback_zero            # "acm.0" → (999998, 0)
test_parse_acm_invalid_returns_high_value  # non-numeric → (999999,)
test_acm_sorts_after_usb_before_unknown # USB < ACM < unknown
test_parse_empty_string_returns_high_value
test_sorting_order_is_correct           # Multi-device sort validation

# Port discovery and topology
test_returns_none_when_no_matches
test_warns_when_insufficient_devices_for_instance
test_stores_topology_and_returns_sorted_device
test_uses_existing_topology_on_subsequent_calls
test_falls_back_to_device_when_no_location_or_acm
test_skips_devices_with_mismatched_description

# Topology validation
test_first_connection_stores_topology
test_out_of_range_instance_returns_true
test_mismatch_increments_counter_and_returns_false

# CRC calculation
test_crc_empty_buffer                   # Initial value 0xFFFF
test_crc_deterministic                  # Same input → same CRC
test_crc_different_for_different_input
test_crc_is_16bit                       # Result fits in 16 bits

# Frame parsing and resync
test_parse_valid_frame
test_parse_multiple_frames
test_incomplete_frame_stays_in_buffer
test_invalid_crc_rejected
test_resync_on_junk_data                # Recovers after junk bytes
test_invalid_terminator_resyncs
test_json_decode_error_handled

# Status change detection
test_detects_status_change
test_no_log_when_status_unchanged
test_detects_slot_status_change
test_detects_dryer_status_change
test_detects_significant_temp_change    # ≥5°C logged
test_ignores_small_temp_change          # <5°C ignored
test_handles_missing_result
test_handles_empty_response

# Connection lifecycle
test_enable_disable_toggle
test_connect_to_ace_disabled_logs
test_connect_retry_backoff_cycles       # Exponential backoff
test_reconnect_success_resets_backoff
test_serial_exception_schedules_reconnect
test_connect_topology_failure_triggers_disconnect
test_disconnect_handles_unregister_errors

# Request windowing and timeouts
test_high_priority_dequeued_first
test_window_full_skips_send
test_timeouts_call_callbacks_and_remove
test_timeout_callback_error_logged
test_has_pending_requests_detects_queued
test_has_pending_requests_detects_inflight

# Response dispatch
test_dispatch_returns_callback_for_known_id
test_dispatch_returns_none_for_unsolicited
test_unsolicited_message_logged

# Reader/writer loop coverage
test_empty_read_reschedules_timer
test_process_valid_frame_calls_callback
test_send_frame_not_connected
test_send_frame_timeout_clears_inflight
test_fills_window_and_sends_requests
test_idle_status_request_when_no_pending
```

### Topology Validation Tests (`test_topology_validation.py`)

USB topology validation and feed assist restoration logic:

```python
# USB topology position calculation
test_topology_position_two_levels                    # "2-2.3" → depth 2
test_topology_position_three_levels                  # "2-2.4.3" → depth 3
test_topology_position_four_levels                   # "1-3.2.4.1" → depth 4
test_topology_position_different_root_same_depth     # Different controllers, same depth
test_topology_position_no_location                   # None location → None result
test_topology_position_invalid_format                # Invalid format → None result

# USB enumeration validation
test_enumeration_insufficient_devices_instance_0     # Instance 0 connects with 1 device
test_enumeration_insufficient_devices_instance_1     # Instance 1 refuses with only 1 device
test_enumeration_sufficient_devices_instance_1       # Instance 1 connects with 2+ devices
test_enumeration_topology_sorting                    # Devices sorted by topology depth

# Topology validation during connection
test_validation_first_connection_no_stored_topology  # First connection always passes
test_validation_matching_topology                    # Reconnection to same position succeeds
test_validation_mismatched_topology                  # Wrong position fails validation
test_validation_failure_counter_increments           # Failure counter tracks mismatches
test_validation_failure_counter_resets_on_success    # Counter resets on correct connection

# Feed assist topology tracking
test_feed_assist_topology_stored_on_enable           # Topology saved when enabled
test_feed_assist_topology_cleared_on_disable         # Topology cleared when disabled

# Feed assist restoration with validation
test_restoration_skipped_no_pending                  # Skip when nothing pending
test_restoration_proceeds_matching_topology          # Restore on matching topology
test_restoration_skipped_mismatched_topology         # Skip on topology mismatch
test_restoration_proceeds_first_time_no_stored_position  # Backward compat: allow first time

# Complete reconnection scenarios
test_happy_path_reconnect_same_topology              # Successful reconnection and restoration
test_error_reconnect_wrong_topology                  # Refuse restoration on wrong ACE
test_error_instance_swapped_on_reconnect             # Detect and prevent instance swap
```

**Purpose**: Ensures ACE instances connect to correct physical hardware after power cycles, USB re-enumeration, or cable swaps. Prevents feed assist from activating on wrong physical ACE unit by validating USB topology position (depth in daisy chain).

**Key Logic**:
- Topology position = depth in USB daisy chain (count dots in `2-2.4.3` → position 3)
- Instance 0 → shallowest position, Instance 1 → deeper position
- Feed assist only restored if topology position matches when enabled
- First connection requires all expected ACEs enumerated before binding
- Reconnection validates topology matches expected, disconnects if wrong

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

- **Full test suite**: ~9 seconds (874 tests)
- **Single test file**: ~0.05-0.3 seconds  
- **Single test**: <0.01 second

Fast execution enables rapid development iteration and quick feedback during TDD.

## Code Cleanup History

### 2026-01-03: Added RGB Color Mapping Tests
Added comprehensive test suite for `rgb_to_mainsail_color()` function:
- **Tests added**: 37 new tests in `test_rgb_to_mainsail_color.py`
- **Coverage**: Pure colors, grayscale, hue boundaries, saturation thresholds, edge cases
- **Purpose**: Validates HSV-based color classification for Mainsail UI color names
- **Result**: All 874 tests pass with full color mapping validation

### 2026-01-03: MAX_RETRIES Configuration Constant
Centralized retry logic configuration:
- **Issue**: Magic number `3` hardcoded in retry logic across codebase
- **Resolution**: Created `MAX_RETRIES` constant in `config.py`, updated all usage
- **Tests updated**: `test_retry_logic.py` now uses constant instead of hardcoded expectations
- **Benefit**: Single source of truth for retry behavior, easily configurable
- **Current value**: `MAX_RETRIES = 6`

### 2026-01-02: Removed Dead Code
Removed unused `read_frames()` function and its associated tests from `test_serial_manager.py`:
- **Issue**: `read_frames()` was never called in production code (only `_reader()` timer callback used)
- **Impact**: Tests were validating code that didn't run in production, creating false confidence
- **Resolution**: Removed dead function and tests. Production frame parsing in `_reader()` remains tested via integration tests
- **Tests removed**: TestFrameParsing class (14 tests for parsing, resync, CRC, JSON errors, buffer management)
- **Net result**: Cleaner codebase, tests now reflect actual production behavior

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
