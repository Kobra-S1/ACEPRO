<div align="center">

# ACE Pro - A Klipper driver for the Anycubic Color Engine Pro

</div>

<p align="center">
  <img src="img/ACEPro.png" alt="Overview" width="30%">
</p>

Based on the great work of utkabobr ([DuckACE](https://github.com/utkabobr/DuckACE)) and szkrisz ([ACEPROSV08](https://github.com/szkrisz/ACEPROSV08)).
This is a fork of szkrisz' ACEPRO Klipper driver.

This Anycubic-centric fork has structurally diverged from the original and focuses on:
- Supporting multiple ACE units, assigns ACE instance IDs based on USB topology
- Adds RFID support (to automatically populate inventory)
- Adds more Endless-Spool matching modes (exact, material only or just use the next available spool)
- Splitting functionality into focused modules (instead of one large file)
- Shortening load/unload times with revised feed sequences
- Purge sequence optimizations (avoids purging too much without intermediate flushing)
- Adds more graceful error-handling if ACE rejects commands
- Adding many console commands for experimentation ;)
- Providing ready-to-use printer and ACE configs for Anycubic Kobra S1 and K3 (vanilla Klipper on USB-OTG SBCs like RPi4/5)
- Expanding controls/panels in the ACE KlipperScreen panel

The provided configurations are tailored for use with Kobra-S1 and Kobra-3 printers (I have only those, so it's also only tested with those printers).

In general other (non-)Anycubic printers are possible to use, but adaptations of the feed/retract lengths and cut tip and wipe macros, etc. will be necessary.
If your printer has only one filament-sensor at the toolhead, use Kobra-3 config files as reference/starting point.
In case your printer has two sensors (one at toolhead, one before that/outside the print chamber), use the KS1 config.

## 📋 Table of Contents

- [Features](#-features)
- [Architecture](#-architecture)
- [Hardware Requirements](#-hardware-requirements)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Commands Reference](#-commands-reference)
- [Endless Spool Feature](#-endless-spool-feature)
- [Inventory Management](#-inventory-management)
- [Contributing](#-contributing)
- [Credits](#-credits)

## ✨ Features

### Core Functionality
- ✅ **Multi-ACE Pro Support**: Multiple ACE units support (tested with 3 ACEPRO units for 12-color printing, but more should be possible)
- ✅ **Endless Spool**: Automatic filament switching with exact/material/next-ready match modes
- ✅ **Persistent State**: Inventory and settings saved across restarts
- ✅ **Runout Detection**: Real-time state-change detection
- ✅ **RFID Inventory Sync**: Reads tag material/color on ready state and syncs into Klipper inventory/UI
- ✅ **Multiple-ACE Pro inventory support**: Keeps track of spool data over several ACE units
- ✅ **Connection Supervision**: Monitors ACE connection stability, pauses print and shows dialog if unstable
- ✅ **Klipper Screen ACE-Pro panel enhancements**: Multiple-ACE support, RFID state, extra utilities commands, etc

## 📖 Documentation

**Start here for complete information:**

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design, components, data flow
- **[example_cmds.txt](example_cmds.txt)** - G-code command examples and usage
- **[tests/README.md](tests/README.md)** - Test suite documentation

## 🏗️ Architecture

This implementation is organized into separate modules:

```
ace/
├── __init__.py           # Module initialization
├── manager.py            # AceManager - orchestrates all ACE units
├── instance.py           # AceInstance - per-unit handler
├── endless_spool.py      # Automatic filament switching logic
├── runout_monitor.py     # Filament runout detection during printing
├── serial_manager.py     # USB communication protocol
├── commands.py           # G-code command handlers
└── config.py             # Configuration constants and helpers

config/
├── ace_K3.cfg            # Kobra 3 ACE configuration
├── ace_KS1.cfg           # Kobra S1 ACE configuration
├── printer_K3.cfg        # Kobra 3 printer macros
├── printer_KS1.cfg       # Kobra S1 printer macros
├── printer_generic_macros.cfg # Shared pause/resume/velocity/purge macros
└── ace_macros_generic.cfg # Shared ACE helper macros


├── ARCHITECTURE.md       
├── example_cmds.txt
└── README.md             # This file
```

📖 **For Detailed Architecture Documentation, see [ARCHITECTURE.md](ARCHITECTURE.md)**

## 🔧 Hardware Requirements

### Required Components
- 1 or more **Anycubic Color Engine Pro** units
- **Filament Sensors** (required): 
  - Toolhead sensor (close to the hotend) - for runout detection
  - Optional: RMS sensor (return module in Anycubic terms) - for jam detection and path validation
- **Hotend**: Recommended: Having there a Filament cutter
- **ACE Adapter**: Adapter which converts the Ace-Pro conector to standard USB


### ACE Pro USB Pin Configuration / Adapter
![Connector Pinout](/img/connector.png)
Connect the ACE Pro to a regular USB port and configure the sensor pins according to your board layout.
![USB Adapter ((c) Gwebster)](/img/Ace2USB_gwebster.png)

Other variations to get a standard USB connection to the ACE can be found on printables.com:

https://www.printables.com/model/1163780-anycubic-ace-pro-usb-a-adapter

https://www.printables.com/model/1227630-anycubic-acepro-back-cover-for-usb-c

## 📦 Installation

### Prerequisites

1. **Clone Repository**
```bash
cd ~
git clone https://github.com/Kobra-S1/ACEPRO.git
git checkout dev # For the development branch with the latest update
```

2. **Install Python Dependencies (if not already installed)**
```bash
# Activate Klipper virtual environment
source ~/klippy-env/bin/activate

# Install required packages
pip3 install pyserial --upgrade
```

### Option 1: Automatic Installation (Recommended)

Use the interactive installer script for automated setup:

```bash
cd ~/ACEPRO
chmod +x installer.sh
./installer.sh
```

The script will:
- Prompt for your printer model (Kobra 3 or Kobra S1)
- Create symlinks for the ACE module
- Backup and install printer configuration
- Copy printer_generic_macros.cfg to your config folder (with backup prompt)
- Link ACE configuration and macro files
- Optional: Install KlipperScreen panel
- Optional: Restart Klipper service

**Recommended for most users** - handles all steps including backups and conflict detection.

### Option 2: Manual Installation

If you prefer manual setup or the automatic installer doesn't work for your setup:

#### Step 1: Create Required Symlinks

```bash
# Link the ACE module folder to Klipper extras
ln -sf ~/ACEPRO/extras/ace ~/klipper/klippy/extras/ace

# Link the virtual_pins helper used by ACE
ln -sf ~/ACEPRO/extras/virtual_pins.py ~/klipper/klippy/extras/virtual_pins.py
```

#### Step 2: Backup and Install Printer Configuration
```bash
# Backup your current printer configuration
cp ~/printer_data/config/printer.cfg ~/printer_data/config/printer.cfg.backup

# Choose your printer variant and copy the appropriate configuration
# IMPORTANT: Some macros in printer.cfg are also used by the ACE Pro module.
# The provided configuration includes all necessary macros.
# If you have custom modifications in your original printer.cfg,
# merge them into the new configuration after installation.

# For Kobra 3:
cp ~/ACEPRO/config/printer_K3.cfg ~/printer_data/config/printer.cfg
cp ~/ACEPRO/config/printer_generic_macros.cfg ~/printer_data/config/printer_generic_macros.cfg
cp ~/ACEPRO/config/ace_K3.cfg ~/printer_data/config/ace_K3.cfg
cp ~/ACEPRO/config/ace_macros_generic.cfg ~/printer_data/config/ace_macros_generic.cfg

# For Kobra S1:
cp ~/ACEPRO/config/printer_KS1.cfg ~/printer_data/config/printer.cfg
cp ~/ACEPRO/config/printer_generic_macros.cfg ~/printer_data/config/printer_generic_macros.cfg
cp ~/ACEPRO/config/ace_KS1.cfg ~/printer_data/config/ace_KS1.cfg
cp ~/ACEPRO/config/ace_macros_generic.cfg ~/printer_data/config/ace_macros_generic.cfg
```


### Post-Installation Configuration

#### Orca Slicer Integration

1. **Update Start G-code**

Update your Orca slicer start machine-gcode to provide the initial_tool parameter to G9111 macro:
```
G9111 bedTemp=[first_layer_bed_temperature] extruderTemp=[first_layer_temperature[initial_tool]] tool=[initial_tool] SKIP_PURGE_FOR_ALREADY_LOADED_TOOL=1
```

**Parameters:**
- `bedTemp` - Bed temperature
- `extruderTemp` - Nozzle temperature for the initial tool
- `tool` - Initial tool index (from `[initial_tool]` Orca variable)
- `SKIP_PURGE_FOR_ALREADY_LOADED_TOOL` - (Optional) Skip purge if the same tool is already loaded and detected at nozzle. This saves time on print restarts.Set to `0` to always purge.

2. **Update End G-code**

Update your Orca slicer end machine-gcode to call PRINT_END macro:
```
PRINT_END CUT_TIP=1
```
This will ensure that at print end, ACE Pro driver (if available) gets informed of the print end, as also filament is cut and retracted and printhead moves to park position.
If you prefer to NOT get the filament cut at print end, change CUT_TIP argument to zero:
```
PRINT_END CUT_TIP=0
```

⚠️ Place the "PRINT_END" call near the bottom of your machine end gcode section, but above any gcode to disable the motors (as the MACRO potentially needs to move printhead for cutting or retracting filament)

3. **(Optional) Add Adaptive Purge Volume Post-processing**

   To support color change adaptive purge volume:
- In Orca Global Process settings, select the **"Others"** tab
- Scroll down to **"Post-processing Scripts"**
- Add the following line (adapt path to your setup):
```
/usr/bin/python3 /home/YourUserNameHere/ACEPRO/slicer/orca_flush_to_purgelength.py
```
**Note**: Use absolute paths only (not `~/` relative paths) - Orca requires full paths for post-processing scripts.

4. **Optional: Add command to also start filament dryer at print start**

If you want to always dry your filament at print start, use the ACE_START_DRYING command in your start gcode:
```
ACE_START_DRYING TEMP=55 DURATION=240
```
Check the documentation for the possible parameters; you can dry all ACEs or target a specific instance.
Tip: Combined with the G4 dwell G-code, you can start drying first and wait a predefined time before continuing the print.


#### KlipperScreen Panel (Optional, only needed if you chose manual installation)

If you have KlipperScreen installed, link the ACE Pro panel:
```bash
ln -sf ~/ACEPRO/KlipperScreen/acepro.py ~/KlipperScreen/panels/acepro.py
sudo systemctl restart KlipperScreen
```
and then add the add the panel to your KlipperScreen configuration, e.g. add:
```
[menu __main acepro]
name: ACE Pro
icon: settings
panel: acepro
```
to your config/main_menu.conf
## ⚙️ Configuration

### Configuration File Structure

Configuration files are located in the `config/` folder. Choose the appropriate files for your printer model.

```
config/
├── ace_K3.cfg                  # Kobra 3 ACE configuration
├── ace_KS1.cfg                 # Kobra S1 ACE configuration
├── ace_macros_generic.cfg      # Shared ACE macros for all printers
├── printer_generic_macros.cfg  # Shared printer macros (pause/resume/velocity/purge)
├── printer_K3.cfg              # Kobra 3 printer macros & settings
└── printer_KS1.cfg             # Kobra S1 printer macros & settings
```

### Include Hierarchy

The configuration uses a modular include structure. The `printer_KS1.cfg` or `printer_K3.cfg` files **are your main printer configuration** - simply copy the appropriate file to `printer.cfg`:

**For Anycubic Kobra S1:**
```
printer.cfg (copy from printer_KS1.cfg)
  ├─ [include printer_generic_macros.cfg]
  │   └─ PAUSE/RESUME, velocity stack, purge helpers, wipe/throw moves
  └─ [include ace_KS1.cfg]
      ├─ [include ace_macros_generic.cfg]
      │   └─ ACE helper macros (toolchange hooks, safety wrappers)
      ├─ [save_variables]
      └─ [ace] section with ACE configuration parameters
```

**For Anycubic Kobra 3:**
```
printer.cfg (copy from printer_K3.cfg)
  ├─ [include printer_generic_macros.cfg]
  │   └─ PAUSE/RESUME, velocity stack, purge helpers, wipe/throw moves
  └─ [include ace_K3.cfg]
      ├─ [include ace_macros_generic.cfg]
      │   └─ ACE helper macros (toolchange hooks, safety wrappers)
      ├─ [save_variables]
      └─ [ace] section with ACE configuration parameters
```

#### Printer Configuration Files

| File | Purpose | Size | Printer |
|------|---------|------|---------|
| `printer_K3.cfg` | Kobra 3 printer macros & settings | - | Anycubic Kobra 3 |
| `printer_KS1.cfg` | Kobra S1 printer macros & settings | - | Anycubic Kobra S1 |
| `printer_generic_macros.cfg` | Shared printer macros (pause/resume, velocity stack, purge helpers) | - | All printers |

### Configuration Setup by Printer Model

#### For Anycubic Kobra 3

Copy the configuration files:

```bash
# From ~/ACEPRO directory
cp ~/ACEPRO/config/printer_K3.cfg ~/printer_data/config/printer.cfg
cp ~/ACEPRO/config/printer_generic_macros.cfg ~/printer_data/config/printer_generic_macros.cfg
cp ~/ACEPRO/config/ace_K3.cfg ~/printer_data/config/ace_K3.cfg
ln -sf ~/ACEPRO/config/ace_macros_generic.cfg ~/printer_data/config/ace_macros_generic.cfg
```

If you build your own `printer.cfg`, include the shared files in this order:
```ini
[include printer_generic_macros.cfg]
[include ace_K3.cfg]
# ace_K3.cfg includes ace_macros_generic.cfg for you
```

#### For Anycubic Kobra S1 / S1 Pro

```bash
cp ~/ACEPRO/config/printer_KS1.cfg ~/printer_data/config/printer.cfg
cp ~/ACEPRO/config/printer_generic_macros.cfg ~/printer_data/config/printer_generic_macros.cfg
cp ~/ACEPRO/config/ace_KS1.cfg ~/printer_data/config/ace_KS1.cfg
ln -sf ~/ACEPRO/config/ace_macros_generic.cfg ~/printer_data/config/ace_macros_generic.cfg
```

If you build your own `printer.cfg`, include the shared files in this order:
```ini
[include printer_generic_macros.cfg]
[include ace_KS1.cfg]
# ace_KS1.cfg includes ace_macros_generic.cfg for you
```

### Multi-Unit Configuration

For multiple ACE units, simply set `ace_count`:

```ini
[ace]
ace_count: 2    # Instance 0 (T0-T3) + Instance 1 (T4-T7)
baud: 115200
feed_speed: 60
# ... rest of config shared by all instances
```

Each unit is automatically detected by USB topology and assigned:
- **Instance 0**: T0-T3 (first ACE connected)
- **Instance 1**: T4-T7 (second ACE connected)
- **Instance 2**: T8-T11 (third ACE connected)
- **Instance 3**: T12-T15 (fourth ACE connected)

**Multiple Instance Example:**
```ini
[ace]
ace_count: 4    # e.g. for four ACE units (16 tools total: T0-T15)
baud: 115200
feed_speed: 60
retract_speed: 50
```

### Sensor Configuration

**Filament Runout Sensors:**
`filament_runout_sensor_name_nozzle` is required.
`filament_runout_sensor_name_rdm` is optional and helps verify the filament has fully retracted to the hub.

```ini
[ace]
# Enable RDM (Return Module) sensor monitoring
filament_runout_sensor_name_rdm: filament_runout_rdm

# Enable nozzle/toolhead sensor monitoring
filament_runout_sensor_name_nozzle: filament_runout_nozzle
```

The RunoutMonitor component will pause printing if filament runs out during a job when these sensors are configured (detected by filament_runout_sensor_name_nozzle).

#### Understanding Sensor States

The filament runout sensors in Mainsail/Fluidd show different states depending on filament position:

- **"Runout Nozzle"** - Sensor at the toolhead (close to hotend)
- **"Runout Rdm"** - Sensor at the back of printer (after the 4-in-1 / 8-in-1 filament hub)

**Normal States:**

| Filament State | Runout Nozzle | Runout Rdm | Meaning |
|----------------|---------------|------------|---------|
| Fully unloaded | empty | empty | No filament in path - ready to load |
| Loaded to nozzle | **detected** | **detected** | Filament loaded from ACE into toolhead |

**Troubleshooting:**

- ⚠️ **Both show "detected" when fully unloaded?**
  - Sensors may have stuck/broken filament debris
  - Clean sensors before attempting load/unload operations
  - Use `ACE_DEBUG_SENSORS` to verify sensor states

- ⚠️ **"Detected" at nozzle but "empty" at RDM?**
  - Broken filament path (filament stuck at nozzle but path is broken)
  - System will **refuse to load** in this state to prevent jams
  - Manually retract stuck filament or use `ACE_CHANGE_TOOL TOOL=-1` to force unload

**Debug Command:**
```gcode
ACE_DEBUG_SENSORS  # Shows current state of all sensors
```

### Per-Instance Configuration Overrides

Some configuration parameters can be overridden per ACE instance, allowing different settings for each ACE unit. This is useful when ACE units have different tube lengths or require different speeds.

**Overridable Parameters:**

The following parameters can be set globally (applies to all instances) or overridden per instance:

- `feed_speed` - Default feed speed (mm/s)
- `retract_speed` - Default retract speed (mm/s)
- `total_max_feeding_length` - Safety limit for feeding (mm)
- `toolchange_load_length` - Distance from ACE to splitter (mm)
- `incremental_feeding_length` - Retry feed length (mm)
- `incremental_feeding_speed` - Retry feed speed (mm/s)
- `heartbeat_interval` - Status check interval (seconds)
- `max_dryer_temperature` - Maximum dryer temp (°C)

**Configuration Syntax:**

Per-instance overrides use a comma-separated format with colon notation:

```ini
parameter: default_value                    # Simple: same value for all instances
parameter: default_value,instance:override  # Override specific instance(s)
parameter: 0:value0,1:value1,2:value2      # Explicit value for each instance
```

**Examples:**

**Example 1: Same configuration for all instances**
```ini
[ace]
ace_count: 3
feed_speed: 60
retract_speed: 50
# All 3 instances use feed_speed=60, retract_speed=50
```

**Example 2: Override specific instance(s)**
```ini
[ace]
ace_count: 3
feed_speed: 60,2:45       # Default 60 for instances 0,1; instance 2 uses 45
# Instance 0: 60 mm/s
# Instance 1: 60 mm/s
# Instance 2: 45 mm/s
```

**Example 3: Multiple overrides**
```ini
[ace]
ace_count: 4
toolchange_load_length: 2000,1:2500,3:1800
# Instance 0: 2000 (default)
# Instance 1: 2500 (override)
# Instance 2: 2000 (default)
# Instance 3: 1800 (override)
```

**Example 4: Explicit per-instance values (no default)**
```ini
[ace]
ace_count: 3
feed_speed: 0:60,1:55,2:50   # Each instance has explicit value
# Instance 0: 60 mm/s
# Instance 1: 55 mm/s
# Instance 2: 50 mm/s
```

**Example 5: Multiple parameters with overrides**
```ini
[ace]
ace_count: 2
feed_speed: 60,1:45                      # Instance 1 slower
retract_speed: 50,1:40                   # Instance 1 slower
toolchange_load_length: 2000,1:2500      # Instance 1 longer tube
total_max_feeding_length: 2600,1:3000    # Instance 1 higher limit
```

**Verification:**

Use `ACE_SHOW_INSTANCE_CONFIG` to verify resolved configuration:

```gcode
ACE_SHOW_INSTANCE_CONFIG           # Compare all instances
ACE_SHOW_INSTANCE_CONFIG INSTANCE=0  # Show specific instance
```

### Customization

To customize settings for your specific hardware:

1. Copy the appropriate config file for your printer
2. Modify parameters based on your:
   - Tube lengths between components
   - Desired feed/retract speeds
   - Sensor configuration
   - Printer-specific movement parameters
3. Use per-instance overrides if your ACE units have different characteristics
4. Test with help of the T0, T1, etc. toolchange console commands first before full print cycles

For detailed command examples with config parameters, see [example_cmds.txt](example_cmds.txt)


## 🎯 Commands Reference

**For complete command documentation with examples, see [example_cmds.txt](example_cmds.txt)**

This driver provides **37 GCode commands** organized by category:

### Tool Selection

| Command | Description |
|---------|-------------|
| `T0` - `Tn` | Change to tool (auto-registered based on `ace_count`) |
| `ACE_GET_CURRENT_INDEX` | Query currently loaded tool index |
| `ACE_CHANGE_TOOL` | Execute tool change with validation |

**Custom Tool Macro Support:**

ACE automatically detects if you have defined custom `[gcode_macro T<n>]` macros in your printer.cfg and will **not** auto-register those tools. This allows you to implement custom tool change logic for integrations like [Spoolman](https://github.com/Donkie/Spoolman).

**Example for Spoolman integration:**
```ini
# Custom T0 macro with Spoolman support
[gcode_macro T0]
gcode:
    # Set active spool in Spoolman
    SET_ACTIVE_SPOOL ID=1
    # Delegate to ACE for actual tool change
    ACE_CHANGE_TOOL TOOL=0
```

See commented examples in `ace_K3.cfg` and `ace_KS1.cfg` for reference.
(I don't use Spoolman, so this feature is not thoroughly tested.)

### Smart Operations (Recommended)

| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_SMART_UNLOAD` | Intelligent unload with multi-slot fallback | `[TOOL=<index>]` |
| `ACE_SMART_LOAD` | Load all non-empty slots to RMS sensor | - |
| `ACE_FULL_UNLOAD` | Complete unload until slot empty | `TOOL=<index>` or `TOOL=ALL` |
| `_ACE_HANDLE_PRINT_END` | End-of-print cleanup (disable runout, optionally unload) | `[CUT_TIP=1]` (1=unload+cut, 0=keep loaded) |

### Manual Feed/Retract Operations

| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_FEED` | Feed filament from slot | `T=<tool>` or `INSTANCE=<0-3> INDEX=<0-3>`, `LENGTH=<mm> [SPEED=<mm/s>]` |
| `ACE_RETRACT` | Retract filament to slot | `T=<tool>` or `INSTANCE=<0-3> INDEX=<0-3>`, `LENGTH=<mm> [SPEED=<mm/s>]` |
| `ACE_STOP_FEED` | Stop active feed operation | `T=<tool>` or `INSTANCE=<0-3> INDEX=<0-3>` |
| `ACE_STOP_RETRACT` | Stop active retract operation | `T=<tool>` or `INSTANCE=<0-3> INDEX=<0-3>` |
| `ACE_SET_FEED_SPEED` | Dynamically update feed speed | `T=<tool>` or `INSTANCE=<0-3> INDEX=<0-3>`, `SPEED=<mm/s>` |
| `ACE_SET_RETRACT_SPEED` | Dynamically update retract speed | `T=<tool>` or `INSTANCE=<0-3> INDEX=<0-3>`, `SPEED=<mm/s>` |

### Feed Assist Control

| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_ENABLE_FEED_ASSIST` | Enable auto-push on spool detection | `T=<tool>` or `INSTANCE=<0-3> INDEX=<0-3>` |
| `ACE_DISABLE_FEED_ASSIST` | Disable auto-push | `T=<tool>` or `INSTANCE=<0-3> INDEX=<0-3>` |

### Inventory Management (5 commands)

| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_SET_SLOT` | Set slot metadata | `T=<tool>` or `INSTANCE=<0-3> INDEX=<0-3>`, `COLOR=R,G,B MATERIAL=<name> TEMP=<°C>` |
| `ACE_SET_SLOT` | Mark slot empty | `T=<tool>` or `INSTANCE=<0-3> INDEX=<0-3>`, `EMPTY=1` |
| `ACE_QUERY_SLOTS` | Query all slots across instances | `[INSTANCE=<0-3>]` omit for all |
| `ACE_SAVE_INVENTORY` | Persist inventory to saved_variables.cfg | `[INSTANCE=<0-3>]` |
| `ACE_RESET_PERSISTENT_INVENTORY` | Clear all slot metadata | `INSTANCE=<0-3>` |

### RFID Inventory Sync (3 commands)

| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_ENABLE_RFID_SYNC` | Enable RFID-driven inventory updates | `[INSTANCE=<0-3>]` omit for all |
| `ACE_DISABLE_RFID_SYNC` | Disable RFID-driven inventory updates | `[INSTANCE=<0-3>]` omit for all |
| `ACE_RFID_SYNC_STATUS` | Show RFID sync enablement state | `[INSTANCE=<0-3>]` omit for all |

### Endless Spool Feature (5 commands)

| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_ENABLE_ENDLESS_SPOOL` | Enable automatic filament swap on runout | - |
| `ACE_DISABLE_ENDLESS_SPOOL` | Disable endless spool | - |
| `ACE_ENDLESS_SPOOL_STATUS` | Query endless spool status | - |
| `ACE_SET_ENDLESS_SPOOL_MODE` | Set match mode (exact, material, or next-ready) | `MODE=exact\|material\|next` |
| `ACE_GET_ENDLESS_SPOOL_MODE` | Query current match mode | - |


### Dryer Control (2 commands)

| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_START_DRYING` | Start filament dryer | `[INSTANCE=<0-3>] TEMP=<°C> [DURATION=<minutes>]` |
| `ACE_STOP_DRYING` | Stop dryer | `[INSTANCE=<0-3>]` |

### Configuration & Purge

| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_SET_PURGE_AMOUNT` | Override purge for next tool change | `PURGELENGTH=<mm> PURGESPEED=<mm/min> [INSTANCE=<0-3>]` |
| `ACE_RESET_ACTIVE_TOOLHEAD` | Reset active tool to -1 | `INSTANCE=<0-3>` |

### System & Diagnostics (8 commands)

| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_GET_STATUS` | Query ACE hardware status | `[INSTANCE=<0-3>] [VERBOSE=1]` - omit INSTANCE for all, VERBOSE=1 for detailed output |
| `ACE_GET_CONNECTION_STATUS` | Query connection stability for all instances | - |
| `ACE_RECONNECT` | Manually reconnect serial | `[INSTANCE=<0-3>]` or omit for all |
| `ACE_DEBUG_SENSORS` | Print all sensor states | - |
| `ACE_DEBUG_STATE` | Print manager and instance state | - |
| `ACE_DEBUG` | Send raw debug request to hardware | `INSTANCE=<0-3> METHOD=<name> [PARAMS=<json>]` |
| `ACE_DEBUG_CHECK_SPOOL_READY` | Test spool ready check with timeout | `TOOL=<0-15> [TIMEOUT=<sec>]` |
| `ACE_SHOW_INSTANCE_CONFIG` | Display resolved configuration | `[INSTANCE=<0-3>]` |

### Testing & Advanced

| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_DEBUG_INJECT_SENSOR_STATE` | Inject sensor state for testing | `TOOLHEAD=0\|1 RMS=0\|1` or `RESET=1` |

**Command Parameter Resolution:**
- **Priority 1**: `INSTANCE=<n> INDEX=<n>` (explicit slot)
- **Priority 2**: `T=<tool>` or `TOOL=<tool>` (mapped to instance/slot)
- **Priority 3**: Fallback to instance 0 if neither specified


### System Control

The ACE Pro switch in Mainsail (Miscellaneous section) enables or disables the system. The setting is **persistent**, so the next restart follows the last state of the switch.

## 📊 Inventory Management

Track filament materials, colors, and temperatures for intelligent toolchanges and endless spool.

**RFID handling:** When a slot transitions to `ready` and ACE reports `rfid=2` with non-empty material and color, the driver copies that material/color into the slot inventory and automatically sets temperature based on material type (e.g., PLA→200°C, PLA High Speed→215°C, ABS→240°C), persists it, and emits a `// {"instance":..., "slots":...}` notify line. KlipperScreen listens for those notify lines and refreshes immediately, so the panel updates without pressing Refresh. RFID sync can be toggled with `ACE_ENABLE_RFID_SYNC` / `ACE_DISABLE_RFID_SYNC` (per-instance or all). Black RFID tag colors (0,0,0) are accepted and overwrite manual values; when RFID sync is enabled and a slot has an RFID spool, the KlipperScreen config dialog locks manual material/color changes.

**Non-RFID spools:** When a spool without an RFID tag is inserted into an empty slot, the driver automatically applies default metadata to make the slot immediately usable:
- **Material**: `Unknown` - clearly indicates unconfigured spool, won't match in endless spool "exact" or "material" modes
- **Color**: Gray `[128, 128, 128]` - matches UI empty slot color, visually indicates unconfigured
- **Temperature**: 225°C - safe middle-ground for most materials, its your responsibility to set here the right values for your loaded filament.

These defaults are applied only when the slot has no saved metadata. If you remove and reinsert the same spool, saved metadata is restored instead of applying defaults.

For detailed inventory examples, see [example_cmds.txt](example_cmds.txt#inventory-management).

### Set Slot Information

```gcode
# Set slot with filament
ACE_SET_SLOT INSTANCE=0 INDEX=0 COLOR=255,0,0 MATERIAL=PLA TEMP=210

# Set multiple slots
ACE_SET_SLOT INSTANCE=0 INDEX=1 COLOR=0,255,0 MATERIAL=PETG TEMP=240
ACE_SET_SLOT INSTANCE=1 INDEX=0 COLOR=255,0,0 MATERIAL=PLA TEMP=210

# Mark slot as empty
ACE_SET_SLOT INSTANCE=0 INDEX=3 EMPTY=1
```

### Query Inventory

```gcode
# Query all instances
ACE_QUERY_SLOTS

# Query specific instance
ACE_QUERY_SLOTS INSTANCE=0

```

### Empty Slot Data Retention

When a slot becomes empty (runout, manual `EMPTY=1`, spool removed), the driver:
- **Preserves**: `color`, `material`, `temp` - allows auto-restore if the same spool is reinserted
- **Clears**: `rfid` flag and all RFID-specific fields (`extruder_temp`, `hotbed_temp`, `diameter`, `sku`, `brand`, etc.)

This means if you remove and reinsert a spool, the slot automatically restores to `ready` with its previous color/material/temp settings.

## � Connection Supervision

Monitors ACE connection stability and automatically pauses prints if connection becomes unstable.

### How It Works

1. **Reconnect Tracking** → Each failed connection attempt is timestamped
2. **Health Monitoring** → Connection status checked every 2 seconds (low overhead)
3. **Instability Detection** → If 6+ reconnects occur within 3 minutes, connection is flagged as unstable
4. **During Print** → Print is paused, dialog shown informing user to fix the issue
5. **When Idle** → Informational dialog shown (no pause)
6. **Recovery** → Dialog auto-closes when connection stabilizes (connected for 30+ seconds)

### Retry Backoff

Failed connection attempts use exponential backoff to avoid log spam:
- **Pattern**: 5s → 8s → 11s → 17s → 25s → 30s → 5s (cyclic)
- Backoff resets to 5s after successful connection

### Configuration

Connection supervision is **enabled by default** and checks every 2 seconds. To disable:

```ini
[ace]
ace_connection_supervision: False
```
### Feed Assist Restoration

When ACE reconnects (after power cycle or USB disconnect), feed assist is automatically restored if it was previously enabled. The restoration is **deferred until after the first successful heartbeat** to ensure the connection is stable before sending commands. This prevents "No response" errors during initial connection negotiation.

```gcode
# Log sequence during reconnection:
# ACE[0]: Connected - will restore feed assist on slot 2 after heartbeat
# ... (heartbeat succeeds) ...
# ACE[0]: Restoring feed assist on slot 2
# ACE[0]: Feed assist restored on slot 2
```

To disable automatic feed assist restoration:

```ini
[ace]
feed_assist_active_after_ace_connect: False
```

### Status Command

```gcode
ACE_GET_CONNECTION_STATUS

# Example output:
# === ACE Connection Status ===
# ACE[0]: Connected (stable)
# ACE[1]: Disconnected, 4/6 reconnects in 180s, next retry: 17s
```
### Troubleshooting Connection Issues

If you see the connection issue dialog during a print, follow these steps:

1. **Check Physical Connections**
   - Verify USB cable is securely connected to both ACE and Raspberry Pi
   - Try a different USB port if available
   - Check if USB cable is damaged or too long (use high-quality cable ≤ 1m)

2. **Power Cycle ACE Unit**
   - Turn off ACE power switch
   - Wait 10 seconds
   - Turn ACE power back on
   - Wait for ACE to fully boot (LEDs stabilize)

3. **Verify Connection Status**
   ```gcode
   ACE_GET_CONNECTION_STATUS
   
   # Look for "Connected (stable)" - this means good to continue:
   # ACE[0]: Connected (stable)
   
   # If you see "stabilizing" or reconnects, wait 30 seconds and check again:
   # ACE[0]: Connected (stabilizing, 23s), 6/6 reconnects in 180s  ← WAIT
   ```

4. **When to Resume**
   - ✅ **Safe to resume**: `Connected (stable)` with 0 reconnects
   - ⚠️ **Wait**: `Connected (stabilizing, XXs)` - check again after 30 seconds
   - ❌ **Not ready**: `Disconnected` or high reconnect count - repeat steps 1-2

5. **Repeat if Necessary**
   - Try steps 1-4 multiple times (2-3 attempts) before canceling print
   - Connection may stabilize after a few minutes
   - If problem persists after 3 attempts, concider canceling the print and investigate

6. **Resume or Cancel**
   - Once connection shows `stable`, dismiss the dialog and run: `RESUME`
   - If unable to stabilize after multiple attempts: `CANCEL_PRINT`

**Tip**: Keep the console/Mainsail terminal open to monitor connection status messages in real-time.
### Persistent USB Issues?

If you're experiencing **repeated USB disconnections** that you didn't have before enabling connection supervision:

1. **Disable Connection Supervision**
   ```ini
   [ace]
   ace_connection_supervision: False
   ```
   Then restart Klipper: `sudo systemctl restart klipper`

2. **Check current usage**
   - Ensure Raspberry Pi power supply provides sufficient current (3A+ recommended)
   - Check if other USB devices are drawing significant power

3. **USB Cable Quality**
   - Use high-quality USB cables (≤ 1m length)
   - Avoid USB hubs with poor power delivery
   - Try different USB ports on the Raspberry Pi

## �🔄 Endless Spool Feature

Automatically switches to a matching spool when filament runs out, enabling continuous multi-day prints.

📖 **For complete endless spool documentation, see [ARCHITECTURE.md - EndlessSpool Section](ARCHITECTURE.md#3-endless-spool-endless_spoolpy)**

### How It Works

1. **Runout Detected** → Toolhead sensor detects filament absence
2. **Jam Detection** → RMS sensor distinguishes true runout from jam
3. **Material Matching** → Searches all slots for a compatible replacement
  - **Match Mode "exact"**: Material **and** color must match
  - **Match Mode "material"**: Material must match, color is ignored
  - **Match Mode "next"**: First **ready** spool in round-robin order, ignoring material/color
4. **Automatic Swap** → Marks old slot empty, changes to matching tool
5. **Resume Print** → Continues printing without user intervention (or pauses if no match)

### Detection Logic

| Toolhead Sensor | RMS Sensor | Diagnosis |
|----------------|------------|-----------|
| CLEAR | CLEAR | **True Runout** → Find match & swap |
| CLEAR | TRIGGERED | **Jam/Break** → Pause & notify |
| TRIGGERED | TRIGGERED | Normal (filament present) |

### Enable/Disable

```gcode
# Enable endless spool (global setting)
ACE_ENABLE_ENDLESS_SPOOL

# Disable endless spool
ACE_DISABLE_ENDLESS_SPOOL

# Check status
ACE_ENDLESS_SPOOL_STATUS

# Set search mode (global)
ACE_SET_ENDLESS_SPOOL_MODE MODE=exact      # Match material AND color (default)
ACE_SET_ENDLESS_SPOOL_MODE MODE=material   # Match material only
ACE_SET_ENDLESS_SPOOL_MODE MODE=next       # Take the next ready slot (ignores material/color)

# Query current mode
ACE_GET_ENDLESS_SPOOL_MODE
```

### Match Mode Examples

- **Exact**: T0 (red PLA) runs out → chooses the next **red PLA** slot; ignores blue PLA.
- **Material**: T0 (red PLA) runs out → chooses any **PLA** slot (color ignored); still skips PETG.
- **Next**: T0 runs out → picks the next **ready** slot in tool order (T1→T2→…), regardless of material/color; skips non-ready slots.

### Behavior

- **Match Found**: Automatic tool change, print continues
- **No Match**: Print pauses, user notification with RESUME/CANCEL buttons
- **Jam Detected**: Immediate pause, notify user of jam location
- **Search Order**: Starts at the next tool index and wraps around; `next` mode still requires slot status `ready`.
- **Timeout Protection**: 5-minute safety timeout for swap operations

### Requirements

- Inventory should include material/color for best matching (required for `exact`, optional for `next`)
- Matching is **case-insensitive** and **whitespace-trimmed**
- `exact` requires material **and** color; `material` ignores color; `next` ignores both
- Only `ready` slots are considered for swapping in all modes

### Persistent Storage

All inventory and state is automatically saved to `saved_variables.cfg`:
- Slot metadata (material, color, temp, status)
- Current tool index
- Filament position (splitter, bowden, toolhead, nozzle)
- Endless spool enabled state
- Match mode configuration

📖 **See [ARCHITECTURE.md - Runout Detection Flow](ARCHITECTURE.md#runout-detection-flow) for detailed sequence diagram**

## ⚠️ Error Recovery & Toolchange Failure Handling

ACE Pro includes error handling for toolchange failures with interactive recovery dialogs.

### Toolchange Failure Dialog

When a tool change fails (e.g., filament loading issue, sensor timeout, path blocked), ACE automatically:

1. **Pauses the print** (if printing)
2. **Shows an interactive dialog** with the error details
3. **Provides recovery options:**
   - **Retry T<n>** - Retry the failed toolchange
   - **Extrude 100mm** - Manually push filament through (useful for clearing blockages)
   - **Retract 100mm** - Manually pull filament back (useful for clearing jams)
   - **Resume** (during print) - Continue printing once tool is loaded
   - **Cancel Print** (during print) - Abort the print job
   - **Continue** (not printing) - Dismiss dialog and continue

**If not printing:** Extruder heater is automatically turned off for safety.
If printing, the idle time of the printer in PAUSE mode (before shutting down) is extended to a couple of hours to survive overnight pauses.
Heatbed is left ON, but nozzle temp is reduced in PAUSE mode, so resuming / retry toolchange can take few minutes until the nozzle reaches print temperature again.
### Recommended Recovery Procedure

When a toolchange fails:

1. **Check the filament path is clear:**
   - Use `ACE_DEBUG_SENSORS` to verify sensor states
   - Ensure toolhead sensor shows CLEAR (no filament blocking)
   - Check RMS sensor is also CLEAR (if available)
   - Look for physical obstructions (tangled filament, jam at splitter, etc.)

2. **Clear any obstructions:**
   - If path is blocked, use **"Retract 100mm"** button to pull back filament
   - Manually remove any tangled or jammed filament
   - Check that splitter/bowden tube is not clogged

3. **Retry the toolchange:**
   - Press **"Retry T<n>"** button to reattempt loading the tool
   - May need multiple retry attempts depending on issue
   - Each retry will re-run the full load sequence

4. **Verify filament extrusion:**
   - Once tool loads successfully, filament should come out of the hotend by priming operation. You can also use **"Extrude 100mm"** to verify filament is flowing
   - Check that filament is actually coming out of the nozzle
   - Ensure consistent extrusion before resuming print

5. **Resume print:**
   - Only press **"Resume"** when you've confirmed:
     - Tool is fully loaded to nozzle
     - Filament is extruding properly
     - No obstructions remain in path
   - Print will continue from pause point


## 🧪 Testing

### Debug Commands

```gcode
ACE_DEBUG_SENSORS                   # Print all sensor states (toolhead, RMS, path-free)
ACE_DEBUG_STATE                     # Print manager state (tool mapping, filament position)
ACE_GET_STATUS INSTANCE=0           # Query ACE hardware status (compact JSON)
ACE_GET_STATUS INSTANCE=0 VERBOSE=1 # Query ACE hardware (detailed, all fields)
ACE_QUERY_SLOTS                     # Check inventory (all instances)
ACE_QUERY_SLOTS INSTANCE=0          # Check inventory (instance 0 only)
ACE_SHOW_INSTANCE_CONFIG            # Display resolved configuration
```

### Sensor Validation

For testing sensor functionality, DON'T use it if you don't know what you are doing:

```gcode
# Manual sensor state injection (for testing)
ACE_DEBUG_INJECT_SENSOR_STATE TOOLHEAD=1 RMS=0   # Simulate runout
ACE_DEBUG_INJECT_SENSOR_STATE TOOLHEAD=0 RMS=1   # Simulate jam
ACE_DEBUG_INJECT_SENSOR_STATE RESET=1             # Use real sensors again
```


## 🖥️ KlipperScreen Integration (Optional)

KlipperScreen ships with a dedicated ACE Pro panel. The panel stays usable on small touch screens and mirrors the driver features:
- **Endless spool + match mode**: Toggle endless spool for all instances and pick match mode (exact/material/next). Match mode selection is disabled while endless spool is off.
- **Instance cycling**: A shuffle button cycles ACE instances and shows the active one (e.g., "ACE 1 (2 of 3)").
- **Refresh & utilities**: A refresh button pulls inventory + active tool; a Utilities button opens feed/retract controls.
- **Slots UI**: Gear button runs load/unload; tapping a slot opens config. RFID-tagged slots display “(RFID)”. If RFID sync is enabled and a slot has an RFID tag, material/color editing is locked. Saving requires a material and temperature; “Empty” is not saved as ready. Material picks auto-fill a default temperature; color picker offers sliders and presets.
- **Utilities panel**: Tool selector disables empty slots. Actions include feed/retract with keypad amount, stop feed/retract, smart load all, smart unload (optional tool), full unload (selected tool), feed-assist on/off, RFID sync on/off, and ACE reconnect.
- **Dryer control**: Per-instance controls with temp slider (25–55°C, default 45°C) and duration slider (30–480 min, default 240). Buttons: start/stop per instance, toggle current instance, start all dryers, stop all dryers.

### Panel Screenshots

**Main ACE Pro Panel**

![KlipperScreen Main Panel](/img/ks_main_ace_pro.png)

The main panel shows all configured tools with their colors, materials, and temperatures. Toggle endless spool and match mode directly from the interface.

**Tool Configuration**

![Spool Configuration](/img/ks_spool_config.png)

Configure each tool slot by setting material, color, and printing temperature.

**Material Selection**

![Material Selection](/img/ks_material_selection.png)

Choose from predefined materials (PLA, PETG, ABS, etc.)

**Color Picker**

![Color Picker](/img/ks_color_picker.png)

RGB color picker with preset color buttons for quick selection. Color is used for endless spool matching.

**Spool Load/Unload Operations**

![Spool Load/Unload](/img/ks_spool_load_unload.png)

Manual operations including Smart Unload, Smart Load All, Full Unload, and Feed Assist control.

**Dryer Control**

![Dryer Control](/img/ks_dryer.png)

Set target temperature and duration for the ACE Pro's built-in filament dryer. Control individual ACE units or start all dryers simultaneously.

### Prerequisites

- **KlipperScreen** must be installed and running
- This panel is **optional** — ACE Pro functions via klipper console commands regardless

### Installation

1. **Verify KlipperScreen is installed:**
   ```bash
   # Check if KlipperScreen directory exists
   ls -la ~/KlipperScreen/panels/
   ```

2. **Link the ACE Pro panel into KlipperScreen:**
   ```bash
   # Create symlink to the panel file
   ln -sf ~/ACEPRO/KlipperScreen/acepro.py ~/KlipperScreen/panels/acepro.py
   ```

3. **Restart KlipperScreen service:**
   ```bash
   # Restart the KlipperScreen service
   sudo systemctl restart KlipperScreen
   
   # Or if using supervisor (check your setup)
   sudo supervisorctl restart klipperscreen
   ```

4. **Verify installation:**
   - Open KlipperScreen
   - Look for "ACE Pro" panel in the menu
   - Panel should appear with inventory slots and controls

### Panel Features

**Inventory Management:**
- View all loaded materials and colors
- Set material/color/temperature for each slot
- Mark slots as empty
- Persist inventory to `saved_variables.cfg`

**Tool Control:**
- Quick tool selection (T0-T15)
- View current active tool
- Manual feed/retract operations

**Endless Spool:**
- Toggle endless spool on/off
- Set match mode (exact material+color or material only)
- View current endless spool status

**Dryer Control:**
- Start/stop filament dryer
- Set temperature and duration

## 🔌 Hardware Setup

### Sensor Installation (Required)

1. **Toolhead Sensor**: Before hotend entry (runout detection)
2. **RMS Sensor**: At splitter/return module (jam detection, path validation)
3. **Wiring**: Connect to configured pins with proper pullup/pulldown

### USB Connection

- Connect ACE Pro unit(s) to host computer via USB
- Driver auto-detects by USB topology (consistent ordering)
- For multiple units: daisy-chain via ACE rear USB ports

### Splitter Configuration

Use an N-in-1 splitter matching your total tool count:
- 1 ACE (4 tools) → 4-in-1 splitter
- 2 ACE (8 tools) → 8-in-1 splitter
- 3 ACE (12 tools) → 12-in-1 splitter
- 4 ACE (16 tools) → 16-in-1 splitter




## 📜 Credits

This project builds upon excellent prior work:

- **[ACEPROSV08](https://github.com/szkrisz/ACEPROSV08)** - ACEPRO SV08 driver implementation (szkriz)
- **[ACEResearch](https://github.com/printers-for-people/ACEResearch)** - Original ACE Pro research
- **[DuckACE](https://github.com/utkabobr/DuckACE)** - Base driver implementation

Special thanks to the Klipper community and all contributors!

## 📄 License

This project is licensed under the same terms as the original projects it's based on.

---







