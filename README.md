# ACE PRO KOBRA-S1 Anycubic Color Engine Pro Driver for Kobra-S1 (vanilla) Klipper (based on SV08 ACE PRO)

A Klipper driver for the Anycubic Color Engine Pro multi-material unit(s), optimized for Kobra-S1 3D printer.

## 📋 Table of Contents

- [Features](#-features)
- [Hardware Requirements](#-hardware-requirements)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Commands Reference](#-commands-reference)
- [Endless Spool Feature](#-endless-spool-feature)
- [Inventory Management](#-inventory-management)
- [Hardware Setup](#-hardware-setup)
- [Contributing](#-contributing)
- [Credits](#-credits)

## ✨ Features
- **Multi-ACE Pro Support**: Supports multiple ACE-PRO Units
- **Multi-Material Support**: Full 4-slot filament management
- **Persistent State**: Settings and inventory saved across restarts
- **Feed Assist**: Advanced filament feeding control
- **Runout Detection**: Dual sensor runout detection system
- **Inventory Tracking**: Material type, color, and temperature management
- **Debug Tools**: Comprehensive diagnostic commands
- **Seamless Integration**: Native Klipper integration

TODO:
- **Endless Spool**: Automatic filament switching on runout

## 🔧 Hardware Requirements

### Required Components
- One or more **Anycubic Color Engine Pro** multi-material unit
- **Filament Sensors**: 
  - RMS sensor (at splitter exit)
  - Toolhead sensor (before hotend)
- **Hotend**: Compatible with filament cutting (recommended)

## 📦 Installation

### 1. Clone Repository
```bash
cd ~
git clone https://github.com/Kobra-S1/ACEPRO.git
```

### 2. Create Symbolic Links
```bash
# Link the driver to Klipper extras
ln -sf ~/ACEPRO/extras/ace.py ~/klipper/klippy/extras/ace.py
ln -sf ~/ACEPRO/extras/virtual_pins.py ~/klipper/klippy/extras/virtual_pins.py

# Link the configuration file
ln -sf ~/ACEPRO/ace.cfg ~/printer_data/config/ace.cfg
```

### 3. Update Python Dependencies
```bash
# Activate Klipper virtual environment
source ~/klippy-env/bin/activate

# Update pyserial to version 4.5 or higher
pip3 install pyserial --upgrade
```

### 4. Update Printer Configuration
Add to your `printer.cfg`:
```ini
[include ace.cfg]
```

## ⚙️ Configuration

### Basic Configuration (ace.cfg)
```ini
[save_variables]
filename: ~/printer_data/config/saved_variables.cfg

# Hack to get a on/off switch to be able to switch between ACEPro and external spool print
[virtual_pins]

[output_pin ACE_Pro]
pin: virtual_pin:ace_pro
pwm: False
value:1
shutdown_value:0

[respond]

[ace]
#serial: /dev/ttyACM0
baud: 115200

standard_filament_runout_detection: True
#filament_runout_sensor_name_rdm: filament_runout_rdm
#filament_runout_sensor_name_nozzle: filament_runout_nozzle

feed_assist_active_after_ace_connect: False
feed_speed: 60
retract_speed: 50
total_max_feeding_length:3000
parkposition_to_toolhead_length:1000
parkposition_to_rms_sensor_length: 100 #170
toolhead_sensor_to_cutter: 22
toolhead_cutter_to_nozzle: 60
toolhead_nozzle_purge: 1
toolhead_fast_loading_speed: 15
toolhead_slow_loading_speed: 5
toolchange_load_length: 3000 # Should be >= the lenght between ACE and the printers 4in1 splitter.
max_dryer_temperature: 55
extruder_feeding_length: 1 
extruder_feeding_speed: 5
extruder_retraction_length: -50
extruder_retraction_speed: 10
default_color_change_purge_length: 50
default_color_change_purge_speed: 400
incremental_feeding_length: 100 
incremental_feeding_speed: 60 
```

### Pin Configuration
![Connector Pinout](/img/connector.png)

Connect the ACE Pro to a regular USB port and configure the sensor pins according to your board layout.

## 🎯 Commands Reference

### Basic Operations
Most commands support a INSTANCE=n parameter.
This allows to select to which ACE-pro Unit the commands shall be send, if none is given instance 0 (first ACEPRO) is assumed.
| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_CHANGE_TOOL` | Manual tool change | `TOOL=<0-3\|-1>` |
| `ACE_FEED` | Feed filament | `INDEX=<0-3> LENGTH=<mm> [SPEED=<mm/s>]` |
| `ACE_RETRACT` | Retract filament | `INDEX=<0-3> LENGTH=<mm> [SPEED=<mm/s>]` |
| `ACE_GET_CURRENT_INDEX` | Get current slot | Returns: `-1, 0, 1, 2, 3` |

### Feed Assist
| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_ENABLE_FEED_ASSIST` | Enable feed assist | `INDEX=<0-3>` |
| `ACE_DISABLE_FEED_ASSIST` | Disable feed assist | `INDEX=<0-3>` |

### Inventory Management
| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_SET_SLOT` | Set slot info | `INDEX=<0-3> COLOR=<R,G,B> MATERIAL=<name> TEMP=<°C>` |
| `ACE_SET_SLOT` | Set slot empty | `INDEX=<0-3> EMPTY=1` |
| `ACE_QUERY_SLOTS` | Get all slots | Returns JSON |
| `ACE_SAVE_INVENTORY` | Save inventory | Manual save trigger |

### Diagnostics
| Command | Description |
|---------|-------------|
| `ACE_TEST_RUNOUT_SENSOR` | Test sensor states |
| `ACE_DEBUG` | Debug ACE communication |

### Dryer Control
| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_START_DRYING` | Start dryer | `TEMP=<°C> [DURATION=<minutes>]` |
| `ACE_STOP_DRYING` | Stop dryer | - |


## 📊 Inventory Management

Track filament materials, colors, and printing temperatures for each slot.

### Set Slot Information
```gcode
# Set slot with filament
ACE_SET_SLOT INDEX=0 COLOR=255,0,0 MATERIAL=PLA TEMP=210

# Set slot as empty
ACE_SET_SLOT INDEX=1 EMPTY=1
```

### Query Inventory
```gcode
# Get all slots as JSON
ACE_QUERY_SLOTS

# Example response:
# [
#   {"status": "ready", "color": [255,0,0], "material": "PLA", "temp": 210},
#   {"status": "empty", "color": [0,0,0], "material": "", "temp": 0},
#   {"status": "ready", "color": [0,255,0], "material": "PETG", "temp": 240},
#   {"status": "empty", "color": [0,0,0], "material": "", "temp": 0}
# ]
```

################### TODO, currently not supported: ##################
### Endless Spool
| Command | Description |
|---------|-------------|
| `ACE_ENABLE_ENDLESS_SPOOL` | Enable endless spool |
| `ACE_DISABLE_ENDLESS_SPOOL` | Disable endless spool |
| `ACE_ENDLESS_SPOOL_STATUS` | Show endless spool status |

## 🔄 Endless Spool Feature

The endless spool feature automatically switches to the next available filament slot when runout is detected, enabling continuous printing across multiple spools.

### How It Works
1. **Runout Detection** → Immediate response (no delay)
2. **Disable Feed Assist** → Stop feeding from empty slot
3. **Switch Filament** → Feed from next available slot
4. **Enable Feed Assist** → Resume normal operation
5. **Update State** → Save new slot index
6. **Continue Printing** → Seamless continuation

### Enable/Disable
```gcode
# Enable endless spool
ACE_ENABLE_ENDLESS_SPOOL

# Disable endless spool
ACE_DISABLE_ENDLESS_SPOOL

# Check status
ACE_ENDLESS_SPOOL_STATUS
```

### Behavior
- **Enabled**: Automatic switching on runout
- **Disabled**: Print pauses on runout (standard behavior)
- **No Available Slots**: Print pauses automatically
- 
### Persistent Storage
- Inventory is automatically saved to Klipper's `save_variables`
- Restored on restart
- Manual save: `ACE_SAVE_INVENTORY`

## 🔌 Hardware Setup

### Sensor Installation
1. **Extruder Sensor**: Install at the splitter exit point
2. **Toolhead Sensor**: Install before the hotend entry
3. **Wiring**: Connect sensors to configured pins with pullup resistors

### USB Connection
Connect the ACE Pro unit to your printer's host computer via USB. The driver will automatically detect the device.
If multiple ACE PRO units are used, daisy-chain the units (same setup as with Kobra-S1 stock FW)

### Splitter Configuration
Use a x-in-1 splitter, matching to your numbers or total spools in your ACE-Pro units

## 🤝 Contributing

Contributions are welcome! Please feel free to submit issues, feature requests, or pull requests.

### Development Setup
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📜 Credits

This project is based on excellent work from:

- **[ACEResearch](https://github.com/printers-for-people/ACEResearch.git)** - Original ACE Pro research
- **[DuckACE](https://github.com/utkabobr/DuckACE.git)** - Base driver implementation
- **[ACEPROSV08](https://github.com/szkrisz/ACEPROSV08))** - ACEPRO SOVOL08 driver implementation from szkriz

## 📄 License

This project is licensed under the same terms as the original projects it's based on.

---

**⚠️ Note**: This is a work-in-progress driver. Please test thoroughly and report any issues you encounter.






