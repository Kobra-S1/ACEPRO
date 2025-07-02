# ACE Pro HMI - Quick Setup Guide

## Prerequisites

1. **Hardware**: SC01 ESP32-S3 development board with 3.5" TFT display
2. **Software**: 
   - VS Code with PlatformIO extension
   - Working Klipper installation with ace.py loaded
   - Moonraker API accessible

## Quick Setup Steps

### 1. Configure Your Settings

```bash
# Copy example config and edit for your setup
cp config_example.h include/config.h
```

Edit `include/config.h` with your specific settings:
- WiFi credentials
- Klipper host IP address
- Hardware pins (if different from SC01 defaults)

### 2. Build and Upload

```bash
# Build the project
pio run

# Upload to ESP32 (connect via USB)
pio run --target upload

# Monitor serial output
pio run --target monitor
```

### 3. First Boot Setup

1. **Connect Hardware**: Ensure ESP32 is connected via USB and powered
2. **Check Serial Output**: Should show WiFi connection and Moonraker connectivity
3. **Touch Screen Test**: Touch interface should be responsive
4. **API Connection**: Verify ACE commands are working

### 4. Configure ACE Pro Integration

Ensure your Klipper configuration includes:

```ini
[ace]
serial: /dev/ttyACM0
extruder_sensor_pin: ^!gpio_pin
toolhead_sensor_pin: ^!gpio_pin
# ... other ACE configuration
```

### 5. Test Basic Functions

1. **Status Display**: Check if ACE status shows correctly
2. **Slot Display**: Verify material slots appear properly
3. **Touch Controls**: Test load/unload/tool change buttons
4. **Settings**: Configure endless spool and other preferences

## Troubleshooting Quick Fixes

### WiFi Won't Connect
```cpp
// In config.h, verify:
#define WIFI_SSID "YourActualNetworkName"
#define WIFI_PASSWORD "YourActualPassword"
```

### Display Issues
- Check TFT_eSPI configuration in `lib/TFT_eSPI_Setup.h`
- Verify pin connections match your hardware
- Try different screen rotation setting

### ACE API Not Working
- Verify Moonraker is running: `http://your_ip:7125/printer/info`
- Check ace.py is loaded in Klipper
- Confirm ESP32 and Klipper are on same network

### Touch Not Working
- Check touch controller pins in config.h
- Verify capacitive vs resistive touch settings
- Try touch calibration if needed

## Advanced Configuration

### Custom Materials Database
Edit the material definitions in `ace_api.cpp` to include your preferred filament types and temperature settings.

### UI Customization
Modify colors, fonts, and layout in `ui_manager.cpp` to match your preferences.

### Network Optimization
Adjust polling intervals in config.h for your network speed and responsiveness preferences.

## Getting Help

1. **Serial Monitor**: Always check serial output first (115200 baud)
2. **Debug Mode**: Enable `DEBUG_ENABLED 1` in config.h for verbose logging
3. **Network Test**: Use `ping` to verify connectivity
4. **API Test**: Test Moonraker endpoints directly in browser

## Common Commands for Testing

### Test ACE Commands via Moonraker
```bash
# Test basic ACE status
curl http://your_ip:7125/printer/objects/query?ace

# Test filament operations
curl -X POST http://your_ip:7125/printer/gcode/script \
     -H "Content-Type: application/json" \
     -d '{"script":"ACE_TEST_RUNOUT_SENSOR"}'
```

### Monitor ESP32 Output
```bash
# Using PlatformIO
pio device monitor

# Using Arduino IDE Serial Monitor
# Set baud rate to 115200
```

That's it! Your ACE Pro HMI should now be fully functional. 🎉
