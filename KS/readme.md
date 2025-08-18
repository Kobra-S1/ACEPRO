# ACE Pro Panel Installation Guide

A custom KlipperScreen panel for controlling the Anycubic Color Engine Pro (ACE Pro) multimaterial unit. This panel provides a touchscreen interface for managing filament slots, endless spool functionality, and dryer controls.

## Features

- **Slot Management**: Configure and control 4 filament slots with material type, color, and temperature settings
- **Visual Status**: Real-time display of loaded slot with color-coded indicators
- **Endless Spool**: Global enable/disable control for automatic filament switching
- **Two-Column Configuration**: Compact interface optimized for 480px height touchscreens
- **Material Selection**: Support for PLA, ABS, PETG, TPU, ASA, PVA, HIPS, and PC
- **Color Picker**: RGB color selection with presets and sliders
- **Temperature Control**: Keypad input for precise temperature settings
- **Dryer Control**: Start/stop filament drying with temperature selection
- **Load/Unload**: Direct slot loading and unloading with confirmation dialogs

## Prerequisites

1. **Klipper with ACE Pro Driver**: The ACEPROSV08 driver must be installed and configured in your Klipper setup
2. **KlipperScreen**: A working KlipperScreen installation
3. **saved_variables**: The `saved_variables.cfg` file must be configured in Klipper (required by ACE driver)

## Installation

### Step 1: Copy the Panel File

Copy the `acepro.py` file to your KlipperScreen panels directory:

```bash
# Navigate to KlipperScreen directory
cd ~/KlipperScreen

# Copy the panel file
cp /path/to/acepro.py panels/
```

### Step 2: Configure KlipperScreen Menu

Add the ACE Pro panel to your KlipperScreen configuration. Edit your `KlipperScreen.conf` file:

```bash
nano ~/printer_data/config/KlipperScreen.conf
```

Add the following section:

```ini
[menu __main acepro]
name: ACE Pro
icon: filament
panel: acepro
```

### Step 3: Ensure ACE Driver Commands

Make sure your Klipper configuration includes the ACE Pro driver with these required commands:
- `ACE_QUERY_SLOTS`: Returns slot configuration data
- `ACE_GET_CURRENT_INDEX`: Returns currently loaded slot (0-3) or -1 if none
- `ACE_SET_SLOT`: Configure slot parameters
- `ACE_CHANGE_TOOL`: Load/unload slots
- `ACE_ENABLE_ENDLESS_SPOOL`: Enable endless spool
- `ACE_DISABLE_ENDLESS_SPOOL`: Disable endless spool
- `ACE_ENDLESS_SPOOL_STATUS`: Get endless spool status
- `ACE_START_DRYING`: Start filament drying
- `ACE_STOP_DRYING`: Stop filament drying

### Step 4: Configure saved_variables

Ensure your `printer.cfg` includes the saved_variables section:

```ini
[save_variables]
filename: ~/printer_data/config/saved_variables.cfg
```

### Step 5: Restart KlipperScreen

Restart KlipperScreen to load the new panel:

```bash
sudo systemctl restart KlipperScreen
```

## Usage

### Main Screen

The main screen displays:
- **Status Bar**: Shows current ACE status and loaded slot
- **Endless Spool Switch**: Toggle endless spool functionality (top right)
- **Slot Buttons**: Four slots showing material, temperature, and color
- **Settings Buttons**: Configuration gear icons below each slot
- **Control Buttons**: Refresh and Dryer controls at the bottom

### Slot Configuration

Click the settings gear below any slot to configure:

1. **Left Panel Controls**:
   - Material selection (PLA, ABS, PETG, etc.)
   - Color picker with preview
   - Temperature setting with keypad input
   - Save/Cancel buttons

2. **Right Panel**: Dynamic content area showing:
   - Material selection grid
   - Color picker with RGB sliders and presets
   - Temperature keypad input

### Loading/Unloading Slots

- **Click a slot** to load it (if configured) or unload it (if currently loaded)
- **Confirmation dialogs** appear for load/unload operations
- **Visual feedback** shows the currently loaded slot with white background

### Endless Spool

- **Toggle switch** in top-right corner
- **Global control** affects all slots
- **Status synchronization** with ACE driver state

## Command Reference

### ACE Driver Commands Used

| Command | Purpose | Response Format |
|---------|---------|-----------------|
| `ACE_QUERY_SLOTS` | Get all slot data | `// [{"material":"PLA","temp":200,"color":[255,255,255],"status":"ready"},...]` |
| `ACE_GET_CURRENT_INDEX` | Get loaded slot | `// 0` (slot 0) or `// -1` (none) |
| `ACE_SET_SLOT INDEX=0 MATERIAL=PLA COLOR=255,255,255 TEMP=200` | Configure slot | Standard gcode response |
| `ACE_CHANGE_TOOL TOOL=0` | Load slot 0 | Standard gcode response |
| `ACE_CHANGE_TOOL TOOL=-1` | Unload current | Standard gcode response |
| `ACE_ENABLE_ENDLESS_SPOOL` | Enable endless spool | Standard gcode response |
| `ACE_DISABLE_ENDLESS_SPOOL` | Disable endless spool | Standard gcode response |
| `ACE_ENDLESS_SPOOL_STATUS` | Get endless spool status | `// - Currently enabled: True/False` |

## Troubleshooting

### Panel Not Appearing
- Check that `acepro.py` is in the correct `panels/` directory
- Verify `KlipperScreen.conf` menu configuration
- Restart KlipperScreen service

### Slot Status Not Updating
- Ensure ACE driver is properly installed
- Check that `saved_variables.cfg` exists and is writable
- Verify `ACE_GET_CURRENT_INDEX` command works in console

### Endless Spool Switch Not Working
- Check `ACE_ENDLESS_SPOOL_STATUS` command output format
- Ensure response includes `"// - Currently enabled: True/False"`

### Configuration Screen Too Large
- Panel is optimized for 480px height screens
- For smaller screens, consider adjusting spacing values in the code

## File Structure

```
KlipperScreen/
├── panels/
│   └── acepro.py          # Main panel file
├── KlipperScreen.conf     # Configuration file
└── README.md              # This file
```

## Dependencies

- **Python 3**: Required for KlipperScreen
- **GTK 3.0**: GUI framework (gi.repository.Gtk)
- **KlipperScreen Framework**: Base panel classes and utilities

## Compatibility

- **KlipperScreen**: Tested with recent versions
- **Screen Resolution**: Optimized for 480px height touchscreens
- **Klipper**: Requires ACEPROSV08 driver installation

## Support

For issues related to:
- **Panel functionality**: Check this repository's issues
- **ACE Driver**: Refer to ACEPROSV08 driver documentation
- **KlipperScreen**: Refer to official KlipperScreen documentation

## License

This panel is provided as-is for use with KlipperScreen and the Anycubic Color Engine Pro system.
