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
| **test_commands.py** | 64 | All G-code command handlers (ACE_GET_STATUS, ACE_FEED, ACE_CHANGE_TOOL, etc.) |
| **test_manager.py** | 113 | AceManager core logic, sensor management, tool changes, state tracking |
| **test_instance.py** | 25 | AceInstance initialization, configuration, serial communication |
| **test_config_utils.py** | 30 | Configuration parsing, tool mapping, inventory creation |

### Feature-Specific Tests

| File | Tests | Description |
|------|-------|-------------|
| **test_endless_spool.py** | 20 | Endless spool match modes (exact/material/next), runout handling |
| **test_runout_monitor.py** | 26 | Runout detection, sensor polling, toolchange interaction |
| **test_set_and_save.py** | 15 | Persistent variable storage to saved_variables.cfg |
| **test_toolchange_integration.py** | 7 | End-to-end tool change scenarios across multiple ACE units |

**Total: 293 tests** across 8 test modules

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

- **Full test suite**: ~5 seconds (293 tests)
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
