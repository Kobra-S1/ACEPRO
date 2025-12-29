# ACE Pro Architecture Overview

## System Overview

The ACE Pro is a multi-material filament management system for Klipper-based 3D printers. This implementation supports multiple ACE Pro units.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Klipper Printer                         │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                      AceManager                            │ │
│  │  - Coordinates multiple ACE instances                      │ │
│  │  - Manages global filament position state                  │ │
│  │  - Handles runout detection & monitoring                   │ │
│  │  │  Sensors: toolhead_sensor, return_module_sensor         │ │
│  │  - Orchestrates tool changes (T0-Tn)                       │ │
│  └──┬────────────────────────────────────────────┬────────────┘ │
│     │                                            │              │
│     ▼                                            ▼              │
│  ┌──────────────────────┐            ┌──────────────────────┐   │
│  │   AceInstance[0]     │            │   AceInstance[1]     │   │
│  │   Tools: T0-T3       │            │   Tools: T4-T7       │   │
│  │   ┌────────────────┐ │            │   ┌────────────────┐ │   │
│  │   │ Slot 0: PLA    │ │            │   │ Slot 0: PETG   │ │   │
│  │   │ Slot 1: ABS    │ │            │   │ Slot 1: PLA    │ │   │
│  │   │ Slot 2: PETG   │ │            │   │ Slot 2: PLA    │ │   │
│  │   │ Slot 3: Empty  │ │            │   │ Slot 3: Nylon  │ │   │
│  │   └────────────────┘ │            │   └────────────────┘ │   │
│  │ Serial: /dev/ttyACM0 |            │ Serial: /dev/ttyACM1 │   │
│  └──────────────────────┘            └──────────────────────┘   │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              EndlessSpool Handler                         │  │
│  │  - Material/color (or "next") matching for runout swaps   │  │
│  │  - Executes automatic tool swap when triggered            │  │
│  │  - No sensor polling (RunoutMonitor handles detection)    │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. AceManager (`manager.py`)

**Primary Responsibilities:**
- One AceManager orchestrates all ACE instances
- **Tool Mapping**: Maps global tool indices (T0-T<n>) to instance/slot pairs
  - Instance 0: T0-T3 (slots 0-3)
  - Instance 1: T4-T7 (slots 0-3)
  - Instance 2: T8-T11 (slots 0-3)
  - Instance 3: T12-T15 (slots 0-3)
  - Instance N: ...
- **Global State Management**:
  - `ace_filament_pos`: Tracks filament position ("splitter", "bowden", "toolhead", "nozzle")
  - `ace_current_index`: Currently active tool (-1 = none)
  - `ace_endless_spool_enabled`: Endless spool active/inactive
  - `ace_global_enabled`: ACE system master enable
- **Sensor Management**: Manages shared sensors (toolhead, RDM)
- **Smart Operations**: `smart_unload()`, `smart_load()` with sensor-aware fallback
- **Tool Change Orchestration**: `perform_tool_change()` coordinates unload/load across instances
- **Runout Detection**: Creates `RunoutMonitor` to poll sensors (50ms interval) and raise events

**Toolchange Guard Decorator:**
```python
@toolchange_in_progress_guard
def perform_tool_change(self, current_tool, target_tool, is_endless_spool=False):
    # Protected method - runout detection blocked during execution
    ...
```
The decorator sets `toolchange_in_progress=True` during execution and ensures it's cleared even on exceptions.

**Key Methods:**
```python
# Core Operations
smart_unload(tool_index)                    # Intelligent unload with fallback strategies
smart_load()                                # Load all non-empty slots to RDM sensor
perform_tool_change(current, target)        # Complete tool change sequence
execute_coordinated_retraction(...)         # Synchronized ACE + extruder retraction

# Sensor Management
get_switch_state(sensor_name)               # Query sensor state
is_filament_path_free()                     # Check if bowden path is clear (toolhead + RDM)
has_rdm_sensor()                            # Check if RDM sensor is configured

# Toolhead Preparation
prepare_toolhead_for_filament_retraction(tool_index)  # Heat and prepare for unload
check_and_wait_for_spool_ready(tool)        # Wait for spool motor stability (with timeout)

# State Management
set_and_save_variable(varname, value)       # Set and persist variable to saved_variables.cfg
update_ace_support_active_state()           # Sync ACE enable/disable state from output pin

# Runout Handling (via RunoutMonitor)
runout_monitor.start_monitoring()           # Begin sensor polling
runout_monitor.stop_monitoring()            # Stop sensor polling
set_runout_detection_active(active)         # Enable/disable detection

# Connection Health Monitoring
_check_connection_health(eventtime)         # Check all instances for stable connections
_handle_connection_issue(instances, time)   # Pause print (if printing) and show dialog
_show_connection_issue_dialog(instances, is_printing)  # Mainsail dialog with details
_close_connection_dialog()                  # Close dialog when connection restored
```

### 2. AceInstance (`instance.py`)

**Primary Responsibilities:**
- **Single Physical Unit**: Manages one ACE Pro hardware unit (4 slots)
- **Local Operations**: Feed, retract, feed assist for its 4 slots
- **Serial Communication**: Via AceSerialManager (request/response protocol)
- **Inventory Tracking**: Per-slot metadata (material, color, temp, status)
- **Toolhead Integration**: Extruder moves, filament feeding to nozzle

**Key Attributes:**
```python
instance_num: int                   # 0, 1, 2, 3...
tool_offset: int                    # First tool: 0, 4, 8, 12...
SLOT_COUNT = 4                      # Fixed per ACE unit
inventory: List[Dict]               # Per-slot: material, color, temp, status
serial_mgr: AceSerialManager        # Communication handler

# Defaults for non-RFID spools (applied when slot becomes ready with no metadata)
DEFAULT_MATERIAL = "Unknown"       # Won't match in endless spool exact/material modes
DEFAULT_COLOR = [128, 128, 128]     # Gray - matches UI empty slot color
DEFAULT_TEMP = 225                  # Safe middle-ground temperature
```

**Key Methods:**
```python
# Feed/Retract Operations
_feed(slot, length, speed, callback)         # Feed filament from slot (async)
_retract(slot, length, speed, on_retract_started, on_wait_for_ready)
                                             # Retract filament to slot with callbacks
_stop_feed(slot)                             # Stop active feed operation
_stop_retract(slot)                          # Stop active retract operation
_feed_sync(slot, length, speed)              # Synchronous feed with blocking wait

# Toolhead Operations
_feed_filament_into_toolhead(tool)           # Load filament to nozzle (multi-stage)
_feed_filament_to_verification_sensor(slot)  # Feed to RDM/toolhead sensor only
_smart_unload_slot(slot, length)             # Unload with sensor validation and retry
rmd_triggered_unload_slot(...)               # RDM-triggered unload with coordinated retraction

# Feed Assist
_enable_feed_assist(slot)                    # Auto-push filament on detection
_disable_feed_assist(slot)                   # Disable auto-push
_update_feed_assist(slot)                    # Update active feed assist slot
_get_current_feed_assist_index()             # Query current feed assist slot
_on_ace_connect()                            # Restore feed assist after reconnection

# Sensor Monitoring (New in 2024-12)
_make_sensor_trigger_monitor(sensor_type)    # Create sensor state change monitor
                                             # Returns: monitor function with timing data

# Serial Communication
send_request(request, callback)              # Queue normal request
send_high_prio_request(request, callback)    # Queue priority request
wait_ready(on_wait_cycle)                    # Block until ACE is ready (with optional callback)
is_ready()                                   # Check if ACE is ready (non-blocking)

# Property Accessors
@property
manager                                      # Get AceManager for this instance (via registry)

# Status & Inventory
get_status(eventtime)                        # Get ACE hardware status (copy)
reset_persistent_inventory()                 # Clear all slot metadata
reset_feed_assist_state()                    # Reset feed assist to disabled

# Utility
_change_retract_speed(slot, speed)           # Dynamically adjust retract speed
_change_feed_speed(slot, speed)              # Dynamically adjust feed speed
_wait_for_condition(condition_fn, timeout)   # Generic blocking wait helper
dwell(delay, verbose)                        # Reactor-based sleep with timing info
_extruder_move(length, speed, wait)          # Extruder motion via toolhead
```

**Sensor Trigger Monitor (Advanced Feature):**
The `_make_sensor_trigger_monitor()` creates a closure-based monitor for tracking sensor state changes during operations:

```python
monitor = instance._make_sensor_trigger_monitor(SENSOR_TOOLHEAD)

# Use in retract operation
instance._retract(slot, length, speed, on_wait_for_ready=monitor)

# Query results
timing = monitor.get_timing()        # Time to sensor trigger (seconds)
count = monitor.get_call_count()     # Number of sensor polls
state = monitor.state_data           # Raw state data
```

This enables precise timing measurements for:
- Retraction efficiency analysis
- Detecting stuck filament (late sensor triggers)
- Optimizing movement speeds
- Diagnosing mechanical issues

### 3. EndlessSpool (`endless_spool.py`)

**Primary Responsibilities:**
- **Material Matching**: Find matches across all slots
- **Match Modes**: 
   - `"exact"` (default): Match material AND color
   - `"material"`: Match material only, ignore color
   - `"next"`: Take the first ready spool, ignoring material/color
- **Automatic Swap**: Execute tool change on runout (pause → swap → resume)
- **Intelligent Fallback**: Retry with next match if feed fails
- **User Prompts**: Show interactive Mainsail prompts on failures

**Architecture Note:**
Runout detection and pausing are handled by `RunoutMonitor`. 
EndlessSpool focuses purely on:
1. Finding matches (based on match mode)
2. Executing swaps (tool changes)
3. Handling swap failures with user feedback

**Key Methods:**
```python
get_match_mode()                    # Get match mode ("exact", "material", "next") from saved_variables

find_exact_match(current_tool)      # Search for a match across all slots (mode-aware search)

execute_swap(from_tool, to_tool)    # Execute automatic tool swap with fallback
                                                      # Coordinates:
                                                      # - Pause print (if not already paused)
                                                      # - Mark old slot empty (status="empty", preserves
                                                      #   color/material/temp, clears RFID fields)
                                                      # - Execute tool change (skip unload)
                                                      # - 1.5x purge on new tool when endless spool
                                                      # - Resume print automatically
                                                      # Max attempts: 3 (retry with next match on fail)

_show_swap_failed_prompt(...)       # User prompt on failed swap

get_status()                        # Return endless spool status dict (currently empty)
```

**Match Mode Behavior:**
```
Mode     | Material | Color | Example
─────────┼──────────┼───────┼────────────────────────
"exact"  | Must     | Must  | PLA + RGB(255,0,0) → match only identical red PLA
"material"| Must    | Any   | PLA + any color → match any PLA regardless of color
"next"   | Any      | Any   | First ready spool, ignore material/color
```

**Swap Failure Retry Logic:**
```
1. Try to feed from candidate_tool
2. On failure → Smart unload failed tool
3. Find next matching spool
4. Retry swap with new candidate
5. Max 3 attempts before giving up
6. Show prompt for user intervention
```

### 4. RunoutMonitor (`runout_monitor.py`)

**Primary Responsibilities:**
- **Filament Runout Detection**: Monitor toolhead sensor during printing
- **State Change Detection**: Detect sensor present → absent transitions
- **Print State Tracking**: Know when printing is active (vs idle/paused)
- **Runout Coordination**: Trigger endless spool or show prompts
- **Print Start Baseline**: Re-initialize sensor baseline when print starts

**Architecture:**
RunoutMonitor is purely an observer - it does NOT change state directly. Instead:
- Detects runout events
- Calls `EndlessSpool.find_exact_match()` to find a swap candidate
- If match found: Calls `EndlessSpool.execute_swap()`
- If no match: Shows user prompt and pauses

**Key Methods:**
```python
start_monitoring()                  # Start runout detection monitor loop
                                    # Registers with reactor for periodic polling (50ms)

stop_monitoring()                   # Stop runout monitoring
                                    # Unregisters timer, stops polling

set_detection_active(active: bool)  # Enable/disable runout detection
                                    # Can disable during maintenance
                                    # Returns: new active state

_monitor_runout(eventtime)          # Main monitoring loop (50ms interval)
                                    # Responsibilities:
                                    # - Get current print state (idle, paused, printing, etc.)
                                    # - Get current tool index from saved_variables
                                    # - Get toolhead sensor state from manager
                                    # - Detect state changes (present → absent)
                                    # - Guard: skip if toolchange in progress
                                    # - Guard: skip if detection disabled
                                    # - Detect print start, re-initialize baseline
                                    # - On runout: call _handle_runout_detected()
                                    # Returns: next callback time (eventtime + interval)

_show_runout_prompt(tool_index, instance_num, local_slot, material, color)
                                    # Show Mainsail prompt for runout
                                    # Displays: tool, instance, slot, material, color
                                    # Buttons: RESUME, CANCEL_PRINT

_handle_runout_detected(tool_index) # Process detected runout
                                    # 1. Set runout_handling_in_progress flag
                                    # 2. Pause print
                                    # 3. Check endless spool enabled?
                                    # 4a. If disabled: Show runout prompt, wait for user
                                    # 4b. If enabled: Find match, auto-swap, resume
                                    # 5. Clear handling flag

_pause_for_runout()                 # Execute PAUSE command via gcode
```

**State Tracking:**
```python
prev_toolhead_sensor_state          # Last known sensor state (for transition detection)
last_printing_active                # Was printing active last cycle?
last_print_state                    # Last raw print state ("idle", "printing", "paused")
runout_detection_active             # Is runout detection enabled?
runout_handling_in_progress         # Are we handling a runout now?
monitor_debug_counter               # For periodic debug logging (~15 min interval)
```

**Runout Detection Logic:**
```
Print State Check
├─ Not printing? → Skip detection
├─ Toolchange in progress? → Skip detection (guard)
├─ Detection disabled? → Skip (guard)
└─ Printing? Continue...

Sensor State Check
├─ First cycle (print just started)?
│  └─ Initialize baseline (record current sensor state)
├─ Sensor state same as previous?
│  └─ No transition detected → Skip
└─ Sensor state CHANGED?
   ├─ Is new state = TRIGGERED (filament present)?
   │  └─ Likely sensor toggle noise → Reset baseline → Skip
   └─ Is new state = CLEAR (filament absent)?
      └─ THIS IS RUNOUT! Call _handle_runout_detected(tool_index)
```

**Print Start Detection:**
- Detects: `is_printing=True` and `was_printing_active=False`
- Action: Re-initialize sensor baseline to current state
- Purpose: Prevent false runout detection if print starts with wrong baseline

### 5. AceSerialManager (`serial_manager.py`)

**Primary Responsibilities:**
- **Serial Communication**: Connect/disconnect to ACE Pro hardware
- **Request/Response Queue**: Sliding window protocol (4 concurrent requests)
- **CRC Validation**: Frame integrity checking
- **Port Detection**: Automatic USB port discovery by topology
- **Heartbeat**: Periodic status updates (1 Hz)

**Protocol:**
- Binary frames with CRC-16
- Request ID tracking for callback dispatch
- High-priority queue for time-sensitive operations
- Automatic retry on timeout/failure

**Key Methods:**
```python
# Connection Management
connect(port, baud)                      # Establish serial connection
connect_to_ace(baud, delay)              # Connect with delayed initialization
auto_connect(instance, baud)             # Auto-detect and connect to ACE by instance
reconnect(delay)                         # Reconnect after disconnect
disconnect()                             # Close serial connection
is_connected()                           # Check connection status

# Port Detection
find_com_port(device_name, instance)     # Auto-detect ACE port by USB topology

# Request Management
send_request(request, callback)          # Queue normal request
send_high_prio_request(req, cb)          # Queue priority request (skip queue)
has_pending_requests()                   # Check if requests are queued
get_pending_request()                    # Get next request from queue
clear_queues()                           # Clear all pending requests

# Heartbeat & Status
set_heartbeat_callback(callback)         # Register status update callback
set_on_connect_callback(callback)        # Register callback for successful (re)connection
start_heartbeat()                        # Start periodic status requests (1Hz)
stop_heartbeat()                         # Stop heartbeat
_send_heartbeat_request()                # Internal heartbeat implementation

# Connection Stability
is_connection_stable()                   # Check if connected and not in reconnect loop
get_connection_status()                  # Get detailed status dict:
                                         #   connected: bool - currently connected
                                         #   stable: bool - connected 30s+ and <6 reconnects in 180s
                                         #   recent_reconnects: int - reconnects in last 180s
                                         #   time_connected: float - seconds since last connect

# Stability Constants (in __init__):
#   INSTABILITY_WINDOW = 180.0           # Look at reconnects in last 3 minutes
#   INSTABILITY_THRESHOLD = 6            # 6+ reconnects in window = unstable
#   STABILITY_GRACE_PERIOD = 30.0        # Must stay connected 30s to be "stable"
#   RECONNECT_BACKOFF_MIN = 5.0          # Initial retry delay
#   RECONNECT_BACKOFF_MAX = 30.0         # Maximum retry delay (cyclic)
#   RECONNECT_BACKOFF_FACTOR = 1.5       # Multiply delay on each failure

# Protocol
_calc_crc(buffer)                        # Calculate CRC-16 for frame
_send_frame(request)                     # Send binary frame with CRC
read_frames(eventtime)                   # Read and parse incoming frames
dispatch_response(response)              # Route response to callback

# ACE Enable/Disable Support
enable_ace_pro()                         # Enable reconnection attempts
disable_ace_pro()                        # Disable reconnection attempts
is_ace_pro_enabled()                     # Check if ACE Pro is enabled
```

### 6. Configuration (`config.py`)

**Global State & Constants:**
```python
# Filament Position States
FILAMENT_STATE_SPLITTER = "splitter"    # At 4-in-1 splitter (unloaded)
FILAMENT_STATE_BOWDEN = "bowden"        # In bowden tube
FILAMENT_STATE_TOOLHEAD = "toolhead"    # At toolhead sensor
FILAMENT_STATE_NOZZLE = "nozzle"        # In hotend/nozzle

# Sensor Names
SENSOR_TOOLHEAD = 'toolhead_sensor'
SENSOR_RDM = 'return_module'

# Registry (populated at runtime)
ACE_INSTANCES = {}                      # instance_num → AceInstance
INSTANCE_MANAGERS = {}                  # instance_num → AceManager
```

**Helper Functions:**
```python
# Tool Mapping
get_tool_offset(instance_num)                        # → instance_num * 4
get_instance_from_tool(tool_index)                   # T7 → instance 1
get_local_slot(tool_index, instance)                 # T7, instance 1 → slot 3
get_ace_instance_and_slot_for_tool(tool)             # T7 → (instance_obj, slot 3)

# Configuration Parsing
parse_instance_number(name)                          # "ace 2" → 2
parse_instance_config(config_value, instance, param) # "60,1:80" → 80 for instance 1

# Inventory Management
create_empty_inventory_slot()                        # Create empty slot dict
create_inventory(slot_count)                         # Create full inventory array
create_status_dict(slot_count)                       # Create ACE status dict

# Variable Persistence
set_and_save_variable(printer, gcode, var, value)    # Set and persist to saved_variables.cfg
```

### 7. Commands (`commands.py`)

**GCode Command Handlers:**

All commands are table-driven and globally registered. Commands use flexible parameter resolution:

**Core Operations:**
```
ACE_GET_STATUS [INSTANCE=<n>|TOOL=<n>] [VERBOSE=1]
                                           # Query ACE hardware status
                                           # Without INSTANCE/TOOL: all instances
                                           # VERBOSE=1: detailed output (all fields)
                                           # VERBOSE=0 (default): compact JSON
                                           
ACE_RECONNECT [INSTANCE=<n>]               # Reconnect serial connection(s)
                                           # Without INSTANCE: reconnect all instances

ACE_FEED [T=<tool>|INSTANCE=<n> INDEX=<n>] LENGTH=<mm> [SPEED=<mm/s>]
                                           # Feed filament from slot
                                           
ACE_STOP_FEED [T=<tool>|INSTANCE=<n> INDEX=<n>]
                                           # Stop active feed

ACE_RETRACT [T=<tool>|INSTANCE=<n> INDEX=<n>] LENGTH=<mm> [SPEED=<mm/s>]
                                           # Retract filament to slot
                                           
ACE_STOP_RETRACT [T=<tool>|INSTANCE=<n> INDEX=<n>]
                                           # Stop active retract
```

**Tool Change & Loading:**
```
ACE_SMART_UNLOAD [TOOL=<n>]                # Intelligent unload with fallback strategies
                                           # Tries current, then other slots, then cross-instance

ACE_SMART_LOAD                             # Load all non-empty slots to verification sensor (toolhead)

ACE_CHANGE_TOOL TOOL=<n>                   # Execute tool change T<n>
                                           # TOOL=-1: unload current tool
                                           
ACE_FULL_UNLOAD [TOOL=<n>|TOOL=ALL]        # Full unload until slot empty
                                           # TOOL=ALL: unload all non-empty slots
                                           # No TOOL: unload current tool
                                           # Clears tool index on success
```

**Inventory Management:**
```
ACE_SET_SLOT [T=<tool>|INSTANCE=<n> INDEX=<n>] COLOR=<name>|R,G,B MATERIAL=<name> TEMP=<°C>
             or EMPTY=1                    # Set slot metadata or clear
                                           # COLOR can be named (e.g. RED, BLUE) or R,G,B

ACE_QUERY_SLOTS [INSTANCE=<n>]             # Query slots
                                           # Without INSTANCE: all instances

ACE_SAVE_INVENTORY [INSTANCE=<n>]          # Persist inventory to saved_variables
                                           # If INSTANCE specified, saves that instance

ACE_RESET_PERSISTENT_INVENTORY INSTANCE=<n>
                                           # Clear all slot metadata for instance

ACE_RESET_ACTIVE_TOOLHEAD INSTANCE=<n>    # Reset active tool index to -1
```

**Feed Assist Control:**
```
ACE_ENABLE_FEED_ASSIST [T=<tool>|INSTANCE=<n> INDEX=<n>]
                                           # Enable auto-push filament on detection

ACE_DISABLE_FEED_ASSIST [T=<tool>|INSTANCE=<n> INDEX=<n>]
                                           # Disable auto-push

ACE_SET_FEED_SPEED [T=<tool>|INSTANCE=<n> INDEX=<n>] SPEED=<mm/s>
                                           # Dynamically adjust feed speed

ACE_SET_RETRACT_SPEED [T=<tool>|INSTANCE=<n> INDEX=<n>] SPEED=<mm/s>
                                           # Dynamically adjust retract speed
```

**Endless Spool:**
```
ACE_ENABLE_ENDLESS_SPOOL                   # Enable auto-swap on runout

ACE_DISABLE_ENDLESS_SPOOL                  # Disable auto-swap

ACE_ENDLESS_SPOOL_STATUS                   # Query endless spool configuration

ACE_SET_ENDLESS_SPOOL_MODE MODE=exact|material|next
                                           # Set match mode:
                                           # "exact": match material AND color (default)
                                           # "material": match material only
                                           # "next": use next ready slot (ignore material/color)

ACE_GET_ENDLESS_SPOOL_MODE                 # Query current match mode
```

**RFID Inventory Sync:**
```
ACE_ENABLE_RFID_SYNC [INSTANCE=<n>]        # Enable auto-sync RFID to inventory

ACE_DISABLE_RFID_SYNC [INSTANCE=<n>]       # Disable auto-sync

ACE_RFID_SYNC_STATUS [INSTANCE=<n>]        # Query RFID sync status
```

**Dryer Control:**
```
ACE_START_DRYING [INSTANCE=<n>] TEMP=<°C> [DURATION=<min>]
                                           # Start filament drying (default 240 min)

ACE_STOP_DRYING [INSTANCE=<n>]             # Stop drying
```

**Configuration & Purge:**
```
ACE_SET_PURGE_AMOUNT PURGELENGTH=<mm> PURGESPEED=<mm/min> [INSTANCE=<n>]
                                           # Set purge parameters for tool changes
```

**Lifecycle Hooks:**
```
_ACE_HANDLE_PRINT_END                      # Called at print end (cleanup sequence)
```

**Debug & Testing Commands:**
```
ACE_GET_CURRENT_INDEX                      # Query currently loaded tool index

ACE_DEBUG_SENSORS                          # Print all sensor states
                                           # (toolhead, RDM, path-free status)

ACE_DEBUG_STATE                            # Print manager & instance state
                                           # (tool mapping, filament position)

ACE_DEBUG INSTANCE=<n> METHOD=<name> [PARAMS=<json>]
                                           # Send raw debug request to hardware

ACE_DEBUG_CHECK_SPOOL_READY TOOL=<n>       # Test spool ready check
                                           # Verifies slot is ready and available

ACE_DEBUG_INJECT_SENSOR_STATE TOOLHEAD=0|1 RDM=0|1 or RESET=1
                                           # Inject sensor state (testing)

ACE_SHOW_INSTANCE_CONFIG [INSTANCE=<n>]    # Display resolved config for instance(s)
                                           # Without INSTANCE: compare all instances
```

**Tool Selection (Dynamic):**
```
T<0-N>                                    # Per-tool commands (auto-registered)
                                           # Count depends on ace_count:
                                           # ace_count=1: T0-T3
                                           # ace_count=2: T0-T7
                                           # ace_count=3: T0-T11
                                           # ace_count=4: T0-T15
```

**Command Resolution Priority:**
```python
def ace_get_instance(gcmd):
    # Priority:
    # 1. INSTANCE=<n> parameter (explicit instance)
    # 2. T=<tool> or TOOL=<tool> parameter (map tool to instance)
    # 3. Fallback to instance 0 if neither specified

def ace_get_instance_and_slot(gcmd):
    # Resolves both instance and slot:
    # 1. T=<tool> parameter → instance + slot
    # 2. INSTANCE=<n> INDEX=<n> parameters → explicit slot
```

### 8. Macros (`ace.cfg`)

**Key Macros:**

```gcode
[gcode_macro _ACE_PRE_TOOLCHANGE]
# Pre-toolchange preparation:
# - Z-hop for safety
# - Ensure homed
# - Heat to appropriate temperature
# - Move to throw position (if heating needed during print)

[gcode_macro _ACE_POST_TOOLCHANGE]
# Post-toolchange finalization:
# - Purge new filament
# - Wipe nozzle
# - Restore temperature
# - Resume moves

[gcode_macro CUT_TIP]
# Cut filament at cutter (Kobra 3 Combo):
# - CRITICAL: Z-lift BEFORE Y movement (prevents collision)
# - Uses G91 (relative) for Z-lift to avoid absolute position issues
# - Move to cutter position (X=0, Y=260)
# - Multiple extruder jabs to ensure clean cut (-2mm/+2mm cycles)
# - Move to flush position after cut
# - Safety: M400 waits ensure moves complete before next operation
# 
# Bug Fix (2024-12-07): Added G91/G90 Z-lift sequence to prevent
# toolhead collision with cutter arm during print toolchanges

[gcode_macro RESUME]
# Resume after pause:
# - Check filament position
# - Reload tool only if needed (filament at splitter/bowden)
# - Restore position and continue
```

## Data Flow

### Tool Change Sequence

```
1. User Command: T3
   ↓
2. AceManager.perform_tool_change(current=-1, target=3)
   ↓
3. _ACE_PRE_TOOLCHANGE macro
   - Z-hop
   - Heat to target temp
   - Move to throw position (if heating needed)
   ↓
4. Unload Current Tool (if any)
   - AceManager.smart_unload(current_tool)
   - Cut filament (CUT_TIP macro)
   - Retract to splitter
   - Validate sensors clear
   ↓
5. Load Target Tool
   - Find instance managing T3 (instance 0)
   - Check spool ready
   - Feed from slot 3 → toolhead sensor
   - Feed toolhead sensor → nozzle
   - Update ace_filament_pos = "nozzle"
   ↓
6. _ACE_POST_TOOLCHANGE macro
   - Purge filament
   - Wipe nozzle
   - Update state
   ↓
7. Set ace_current_index = 3
```

### Runout Detection Flow

```
1. Toolhead Sensor Triggers (filament absent)
   ↓
2. RunoutMonitor._monitor_runout() (50ms interval)
   - Detects state change (present → absent)
   - Guards: not during toolchange, printing active, detection enabled
   - Tracks previous sensor state for transition detection
   ↓
3. RunoutMonitor._handle_runout_detected(tool_index)
   - Sets runout_handling_in_progress flag
   - Resets sensor baseline to prevent repeated triggers
   ↓
4. RunoutMonitor._pause_for_runout()
   - Execute PAUSE command (Klipper pause macro)
   ↓
5. Show Interactive Mainsail Prompt
   - Display runout details (instance, slot, material, color)
   - Buttons: RESUME, CANCEL_PRINT
   ↓
6. Check Endless Spool Enabled
   - Query ace_endless_spool_enabled from saved_variables
   ↓
7a. If Endless Spool DISABLED:
   - Stay paused, wait for user to refill spool
   - User must click RESUME after refilling
   ↓
7b. If Endless Spool ENABLED:
   - EndlessSpool.find_exact_match(tool_index) (mode-aware: exact/material/next)
   - Search all instances according to match mode
   ↓
8a. If NO MATCH Found:
   - Stay paused, prompt remains visible
   - User must refill or load matching material
   ↓
8b. If MATCH Found:
   - Close prompt automatically
   - EndlessSpool.execute_swap(from_tool, to_tool)
   - Mark old slot empty (status="empty", preserves color/material/temp)
   - Execute tool change with is_endless_spool=True
   - Skip unload (already empty), perform 1.5x purge
   - Resume print automatically
   ↓
9. Finally: Clear runout_handling_in_progress flag
```

## State Management

### Global State (saved_variables.cfg)

```python
ace_filament_pos: str               # "splitter" | "bowden" | "toolhead" | "nozzle"
ace_current_index: int              # Currently loaded tool (-1 = none)
ace_endless_spool_enabled: bool     # Endless spool active
ace_endless_spool_match_mode: str   # Match mode: "exact" | "material" | "next"
ace_global_enabled: bool            # ACE system enabled

# Per-instance inventory (persisted)
ace_inventory_0: List[Dict]         # Instance 0 slots
ace_inventory_1: List[Dict]         # Instance 1 slots
# ... etc
```

### Runtime State (AceManager)

```python
toolchange_in_progress: bool        # Tool change active (blocks runout)
runout_detection_active: bool       # Runout monitoring enabled
prev_toolhead_sensor_state: bool    # For detecting state changes
last_printing_state: bool           # Track print start/stop
sensors: Dict[str, Sensor]          # Sensor objects
```

### Runtime State (AceInstance)

```python
inventory: List[Dict]               # Slot metadata (runtime copy)
_feed_assist_index: int             # Current feed assist slot (-1 = none)
_info: Dict                         # ACE hardware status
serial_mgr: AceSerialManager        # Communication handler
feed_assist_active_after_ace_connect: bool  # Restore feed assist on reconnect (config)
```

### Inventory Slot Structure

Each slot in the inventory contains:

```python
{
    "status": str,      # "ready" | "empty" - hardware state
    "color": List[int], # [R, G, B] - preserved when empty
    "material": str,    # e.g. "PLA" - preserved when empty
    "temp": int,        # Print temperature - preserved when empty
    "rfid": bool,       # True if data came from RFID tag - cleared when empty
    
    # Optional RFID fields (cleared when slot becomes empty):
    "extruder_temp": Dict,  # {"min": int, "max": int}
    "hotbed_temp": Dict,    # {"min": int, "max": int}
    "diameter": float,      # Filament diameter in mm
    "sku": str,             # Spool SKU
    "brand": str,           # Brand name
    "total": int,           # Total spool length (mm)
    "current": int,         # Remaining length (mm)
}
```

### Slot Empty Transition Behavior

When a slot transitions from `ready` to `empty` (runout, manual EMPTY=1, etc.):

| Field | Behavior | Reason |
|-------|----------|--------|
| `status` | Set to `"empty"` | Hardware reports no filament |
| `color` | **Preserved** | Allows auto-restore if same spool reinserted |
| `material` | **Preserved** | Allows auto-restore if same spool reinserted |
| `temp` | **Preserved** | Allows auto-restore if same spool reinserted |
| `rfid` | Set to `False` | No RFID tag present |
| `extruder_temp` | **Cleared** | RFID data no longer valid |
| `hotbed_temp` | **Cleared** | RFID data no longer valid |
| `diameter` | **Cleared** | RFID data no longer valid |
| `sku`, `brand`, etc. | **Cleared** | RFID data no longer valid |

**Rationale**: Core metadata (color, material, temp) is preserved so that if the same
spool is reinserted, the slot auto-restores to `ready` with its previous settings.
RFID-specific fields are cleared because they only apply when an RFID-tagged spool
is physically present.


## Configuration Example
This example is just for reference; check printer_KS1.cfg / printer_K3.cfg for live values.

```ini
[ace]
ace_count: 1
baud: 115200

# Tube Lengths
parkposition_to_toolhead_length: 800
parkposition_to_rdm_length: 150
toolchange_load_length: 2000

# Feeding Speeds
feed_speed: 60
retract_speed: 50
incremental_feeding_length: 100
incremental_feeding_speed: 60
extruder_feeding_length: 10
extruder_feeding_speed: 8
toolhead_slow_loading_speed: 5
toolhead_full_purge_length: 85

# Purge Settings
default_color_change_purge_length: 50
default_color_change_purge_speed: 300
purge_max_chunk_length: 250
purge_multiplier: 1.0

# Safety & Misc
total_max_feeding_length: 2600
pre_cut_retract_length: 2
heartbeat_interval: 1.0
max_dryer_temperature: 55
feed_assist_active_after_ace_connect: True   # Restore feed assist after ACE reconnect
```
### Debug Commands

```gcode
ACE_DEBUG_SENSORS                  # Check sensor states
ACE_DEBUG_STATE                    # Check manager state
ACE_GET_STATUS INSTANCE=0          # Query ACE hardware (compact JSON)
ACE_GET_STATUS INSTANCE=0 VERBOSE=1 # Query ACE hardware (detailed output)
```
