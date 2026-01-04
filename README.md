# ACE PRO KOBRA-S1 Anycubic Color Engine Pro Driver for Kobra-S1 (vanilla) Klipper (based on SV08 ACE PRO)

A Klipper driver for the Anycubic Color Engine Pro multi-material unit(s), optimized for Kobra-S1 3D printer.

NOTE: You are here on the "main" branch, with the latest stable version.

The "dev" branch contains the latest,more feature rich, version, which has some quite signifcant changes in code as well as the configuration files.
It will be merged to the main branch when it has proven to be stable enough.

If you are here to installing from scratch, consider using the dev-branch, as it can be a little bit of a headache to migrate manually from the old configuration files and MACROS to the currently used in the "dev" branch.

Dev version also has now a installer script. So its the most simple version to get the driver up&running on supported printers.

If that version still makes you trouble, you can still return here and use the current main branch version.

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
- **Adaptive Purge Volume support (for Orcaslicer) via gcode postprocessing script
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
### 2.1 Update your printer.cfg Kobra S1 G9111 macro to support initial tool parameter
```
# =======================
# G9111 — Startup Macro for Anycubic slicer compatibility
# =======================
[gcode_macro G9111]
description: "Startup: G9111 BEDTEMP=<°C> EXTRUDERTEMP=<°C> [WIPETEMP=<°C>] [TOOL=inital tool index]"
variable_wipe_temp: 150
variable_heat_pos_x: 40
variable_heat_pos_y: 276
variable_heat_pos_z: 5
variable_travel_speed: 200  # mm/s

gcode:
  # Helper: strip one leading '=' if present, then float
  {% set bed_raw = (params.BEDTEMP | default(params.S) | default("60")) | string %}
  {% set noz_raw = (params.EXTRUDERTEMP | default(params.T) | default("200")) | string %}
  {% set wipe_raw = (params.WIPETEMP | default(printer["gcode_macro G9111"].wipe_temp) | string) %}

  {% set TOOL = ((params.TOOL | default(params.tool) | default(params.T) | default(params.t) | default("-1")) | string | replace('=', '', 1) | int) %}  

  {% set BEDTEMP = (bed_raw | replace('=', '', 1)) | float %}
  {% set EXTRUDERTEMP = (noz_raw | replace('=', '', 1)) | float %}
  {% set WIPETEMP = (wipe_raw | replace('=', '', 1)) | float %}
  
  {% set f_travel = (printer["gcode_macro G9111"].travel_speed | float) * 60 %}

  { action_respond_info(
      "G9111: bed=%.1f°C, wipe=%.1f°C, final nozzle=%.1f°C, travel_speed=%.1f tool=%d"
      % (BEDTEMP, WIPETEMP, EXTRUDERTEMP, f_travel, TOOL)
    ) }
    
  # Set temps
  RESPOND TYPE=echo MSG="No wait pre-heat for nozzle wipe"
  M104 S{WIPETEMP}
  M140 S{BEDTEMP}

  RESPOND TYPE=echo MSG="Home Y,X"
  G28 Y X

  RESPOND TYPE=echo MSG="Wait for pre-heat temp reached"
  TO_THROW_POSITION
  # Wait to wipe temp
  M109 S{WIPETEMP}
  RESPOND TYPE=echo MSG="Wipe nozzle"
  WIPE_ENTER
  WIPE_NOZZLE
  WIPE_STOP
  WIPE_EXIT

  RESPOND TYPE=echo MSG="Homing"
  G90
  G28 Z
  M106 S255
 
  RESPOND TYPE=echo MSG="(Adaptive) bed mesh"
  BED_MESH_CALIBRATE PROFILE=adaptive ADAPTIVE=1
    
  # Final nozzle temp (wait)
  RESPOND TYPE=echo MSG="Heat nozzle to print temperature"
  M106 S0
  MOVE_HEAT_POS
  M109 S{EXTRUDERTEMP}

  #Inform ace that this is initial startup toolchange. This avoids double wipping from this macro and the toolchange macro
  {% if printer["gcode_macro _ACE_STATE"] is defined
   and printer["gcode_macro _ACE_STATE"].startup_toolchange is defined %}
    SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=startup_toolchange VALUE=1
  {% endif %}
  
  ; if a tool index was passed, activate it
  {% if TOOL >= 0 %}
      RESPOND TYPE=echo MSG="Selecting passed active tool"
      T{TOOL}
  {% else %}
    # Assure tool active / filament loaded from ACE slot
    {% set current_extruder = printer.toolhead.extruder %}
    {% set suffix = current_extruder | replace('extruder','') %}
    {% set tool_num = suffix if suffix|length > 0 else '0' %}
    RESPOND TYPE=echo MSG="Re-selecting active tool"
    T{tool_num}
  {% endif %}

  RESPOND TYPE=echo MSG="Prime nozzle"
  G92 E0
  G1 E50 F400
  G1 E60 F150
  G1 E90 F150
  G92 E0

  RESPOND TYPE=echo MSG="Wipe nozzle after purge"
  TO_FRONT_OF_THROW_BLADE
  TO_BACK_OF_THROW_BLADE
  TO_FRONT_OF_THROW_BLADE
  TO_BACK_OF_THROW_BLADE

  WIPE_ENTER
  WIPE_NOZZLE
  WIPE_STOP
  WIPE_EXIT

  RESPOND TYPE=echo MSG="Purge line"
  G91
  G1 Z2 F1000  
  UNDERLINE

  RESPOND TYPE=echo MSG="G9111 complete."
```
### 2.2 Update in Orca slicer your start machine-gcode to provide the initial_tool to G9111 macro
```
G9111 bedTemp=[first_layer_bed_temperature] extruderTemp=[first_layer_temperature[initial_tool]] tool=[initial_tool]
```
### 2.3 (Optional) To support colorchange adaptive purge-volume, add postprocessing script call to your Orca Slicer Profile
- In Orca Global Process settings select the "Others" tab.
- Scroll down to "Post-processings Scripts"
- Add the below line
```/usr/bin/python3 /home/YourUserNameHere/ACEPRO/orca_flush_to_purgelength.py```
 You need to adapt the path to the python interpreter as also to the script to you local setup.
 For linux environment, dont use relative path like "~/", only absolute path work properly in Orca.
 
### 3. Update Python Dependencies
```bash
# Activate Klipper virtual environment
source ~/klippy-env/bin/activate

# Update pyserial to version 4.5 or higher
pip3 install pyserial --upgrade
```

### 4. Update Printer Configuration
Add to your `printer.cfg` the [ace.cfg].
IMPORTANT: Move the existing filament_switch_sensor entries ABOVE the ace.cfg include, as they are needed by the ace driver, otherwise you get a error message which reminds you in case you forget. ;)
Example:

```ini
### Filament runout sensor must be defined before ace.cfg +++++++++++++++
[filament_switch_sensor filament_runout_nozzle]
pause_on_runout: True
runout_gcode:
  TO_THROW_POSITION
insert_gcode:
  WIPE_ENTER
  WIPE_NOZZLE
  WIPE_STOP
  WIPE_EXIT
event_delay: 3.0
pause_delay: 0.5
switch_pin: nozzle_mcu:PB0

# ACE Pro return detection module (on the back of KS1 printer)
[filament_switch_sensor filament_runout_rdm]
pause_on_runout: False  # Enable this only if ACE is used to feed filament
switch_pin: PB0

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

If you have multiple ACE PRO units, copy and paste your existing [ace] section below it and rename [ace] to [ace 1] for the second unit, [ace 2] for the third, etc.
[ace] or [ace 0] has to be always there and refers to the first ACE PRO unit (which is directly connected to your computer).

### Pin Configuration
![Connector Pinout](/img/connector.png)

Connect the ACE Pro to a regular USB port and configure the sensor pins according to your board layout.

## 🎯 Commands Reference

### Basic Operations
Most commands support a INSTANCE=n parameter.
This allows to select to which ACE-pro Unit the commands shall be send, if none is given instance 0 (first ACEPRO) is assumed.
The direct to your computer connected ACE PRO get INSTANCE id 0 and gets the tool indicies 0-3 assigned (T0,T1,T2,T3), the next (daisy-chained)
connected ACE PRO Unit will be the INSTANCE id 1 and gets the tools indicies 4-7 assigned (T4,T5,T6,T7) and so on.

NOTE: ACE PRO units sometimes tend to have (usb) connection issues and can disappear&reappear mid-print, the driver trys to reconnect, but that might or may not work
properly without PAUSING the print. If print gets PAUSE and a lot of serial connection errors show up in the console output, you may have to power the ACE Units OFF/ON and/or replug the USB connection to the PC to get them in a usable state gain. Wait then until console shows the firmware info for each connected ACE once again before trying to resume the print with "RESUME" command in console/mainsail.

Example trace output to look for, for two connected ACE PROs:
```
ACE[1]:{'id': 0, 'code': 0, 'result': {'id': 1, 'slots': 4, 'model': 'Anycubic Color Engine Pro', 'firmware': 'V1.3.863', 'boot_firmware': 'V1.0.1', 'structure_version': '0'}, 'msg': 'success'}
ACE[0]:{'id': 0, 'code': 0, 'result': {'id': 1, 'slots': 4, 'model': 'Anycubic Color Engine Pro', 'firmware': 'V1.3.863', 'boot_firmware': 'V1.0.1', 'structure_version': '0'}, 'msg': 'success'}
```

By default ACE PRO driver is enabled if you include [ace.cfg] in printer.cfg.
Either comment that out if you dont want to use ACE Pro for printing, or keep it and use the "ACE Pro" switch in mainsail, you find it in the "Miscellaneous" section of mainsail (above the filament runout sensor states). This settings is not persistent, so after restart ACE Pros are automatically active again.

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

### Loading/Unloading helper
| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_SMART_UNLOAD` | Unload filament(s) to free filament path |
| `ACE_SMART_LOAD` | Feeds all slots (on all ACEPro) once to the RMS and then back to parkposition  | - |

### Loading/Unloading helper
| Command | Description | Parameters |
|---------|-------------|------------|
| `ACE_SET_PURGE_AMOUNT` | Sets the length of Filament to extrude for purging for the next comming toolchange |`PURGELENGTH=<mm> [PURGESPEED=<mm/s>]` |

### Toolchange commands
| Command | Description | Parameters |
|---------|-------------|------------|
| `T<n>` | Changes to other filament spool, automatically generated for each connected ACE Pro unit |`The tool index` |

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

- **[ACEPROSV08](https://github.com/szkrisz/ACEPROSV08)** - ACEPRO SOVOL08 driver implementation from szkriz
- **[ACEResearch](https://github.com/printers-for-people/ACEResearch.git)** - Original ACE Pro research
- **[DuckACE](https://github.com/utkabobr/DuckACE.git)** - Base driver implementation

## 📄 License

This project is licensed under the same terms as the original projects it's based on.

---

**⚠️ Note**: This is a work-in-progress driver. Please test thoroughly and report any issues you encounter.






