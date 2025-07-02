# ACE Pro HMI - ESP32 Touchscreen Interface

A comprehensive touchscreen interface for the Anycubic ACE Pro multi-material unit, designed for the SC01 ESP32 development board with integrated 3.5" TFT display.

## Features

### 🎛️ **Complete ACE Pro Control**
- Real-time status monitoring
- Material inventory management with visual indicators
- Filament load/unload operations
- Tool changing functionality
- Endless spool configuration
- Dryer control with temperature and duration settings

### 🖥️ **Modern Touch Interface**
- LVGL-powered responsive UI
- 4-slot material overview with color-coded indicators
- Intuitive navigation between screens
- Progress feedback for operations
- Error handling and status messages

### 🌐 **Network Integration**
- WiFi connectivity with auto-reconnection
- Moonraker API communication
- Real-time status updates
- Background operation monitoring

### ⚙️ **Hardware Optimized**
- Designed for SC01 ESP32-S3 with 3.5" capacitive touch display
- Efficient memory usage with PSRAM support
- Multi-core task management
- Hardware-accelerated graphics

## Hardware Requirements

### SC01 ESP32-S3 Development Board
- **MCU**: ESP32-S3 with WiFi
- **Display**: 3.5" TFT LCD (480x320)
- **Touch**: Capacitive touch panel
- **Memory**: 8MB PSRAM, 16MB Flash
- **Connectivity**: WiFi 802.11 b/g/n

### Connection to ACE Pro System
- **Network**: WiFi connection to Klipper host
- **Protocol**: HTTP/HTTPS via Moonraker API
- **Commands**: Full ace.py command set support

## Software Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Main Application                     │
├─────────────────────────────────────────────────────────┤
│  UI Manager (LVGL)  │  ACE API Client  │ Network Manager │
├─────────────────────────────────────────────────────────┤
│              Display Driver (TFT_eSPI)                  │
├─────────────────────────────────────────────────────────┤
│                ESP32-S3 Hardware                        │
└─────────────────────────────────────────────────────────┘
```

## Installation & Setup

### 1. Hardware Setup
1. Connect your SC01 ESP32-S3 board to your computer
2. Ensure the display and touch panel are properly connected

### 2. Software Configuration

#### Configure WiFi and Moonraker Connection
Edit `include/config.h`:
```cpp
#define WIFI_SSID "your_wifi_network"
#define WIFI_PASSWORD "your_wifi_password"
#define MOONRAKER_HOST "192.168.1.100"  // Your Klipper host IP
#define MOONRAKER_PORT 7125
```

#### TFT_eSPI Configuration
The project includes TFT_eSPI configuration for SC01. Verify settings in your TFT_eSPI User_Setup.h or create a custom setup file.

### 3. Build and Upload

#### Using PlatformIO (Recommended)
```bash
# Install PlatformIO if not already installed
pip install platformio

# Build the project
pio run

# Upload to ESP32
pio run --target upload

# Monitor serial output
pio device monitor
```

#### Using Arduino IDE
1. Install ESP32 board support
2. Install required libraries:
   - LVGL (v8.3.11)
   - ArduinoJson (v7.0.4)
   - TFT_eSPI
   - AsyncTCP
3. Set board to "ESP32S3 Dev Module"
4. Configure partition scheme to "Huge APP"
5. Upload the sketch

## Usage

### Main Screen
- **4-Slot Overview**: Visual representation of all material slots
- **Color Indicators**: RGB color representation of loaded materials
- **Quick Actions**: Load, Unload, and Select buttons for each slot
- **Status Bar**: Connection status, current tool, and system time

### Inventory Management
- **Material Database**: Store material type, color, and temperature settings
- **Slot Configuration**: Assign materials to specific slots
- **Persistent Storage**: Inventory saved to Klipper's variable system

### Settings
- **Endless Spool**: Enable/disable automatic filament switching
- **Display Settings**: Brightness control and sleep settings
- **Network Settings**: WiFi configuration and connection management
- **Dryer Control**: Temperature and duration settings for filament drying

### Status Monitor
- **Real-time Data**: Live ACE Pro status and sensor readings
- **Error Display**: System errors and diagnostic information
- **Connection Health**: Network status and API connectivity

## API Commands Supported

The HMI supports all ACE Pro commands via Moonraker API:

### Material Operations
- `ACE_FEED` - Load filament into bowden tube
- `ACE_RETRACT` - Retract filament back to ACE
- `ACE_CHANGE_TOOL` - Complete tool change operation
- `ACE_ENABLE_FEED_ASSIST` / `ACE_DISABLE_FEED_ASSIST` - Control feed assistance

### Inventory Management
- `ACE_SET_SLOT` - Set material information for a slot
- `ACE_QUERY_SLOTS` - Query current inventory status
- `ACE_SAVE_INVENTORY` - Save inventory to persistent storage

### Endless Spool
- `ACE_ENABLE_ENDLESS_SPOOL` / `ACE_DISABLE_ENDLESS_SPOOL` - Control endless spool
- `ACE_ENDLESS_SPOOL_STATUS` - Query endless spool status

### Dryer Control
- `ACE_START_DRYING` - Start filament drying with temperature and duration
- `ACE_STOP_DRYING` - Stop drying operation

### Diagnostics
- `ACE_TEST_RUNOUT_SENSOR` - Test runout sensor functionality
- `ACE_DEBUG` - Execute debug commands

## Configuration

### Network Settings
```cpp
// WiFi Configuration
#define WIFI_SSID "your_network"
#define WIFI_PASSWORD "your_password"

// Moonraker Settings
#define MOONRAKER_HOST "192.168.1.100"
#define MOONRAKER_PORT 7125
#define MOONRAKER_API_KEY ""  // Optional
```

### Display Settings
```cpp
// Screen Configuration
#define SCREEN_WIDTH 480
#define SCREEN_HEIGHT 320
#define SCREEN_ROTATION 1  // 90° rotation

// Update Intervals
#define UI_UPDATE_INTERVAL 1000     // UI refresh rate
#define STATUS_UPDATE_INTERVAL 2000 // Status polling rate
```

### Hardware Pins (SC01)
```cpp
// TFT Display
#define TFT_MOSI 11
#define TFT_MISO 13
#define TFT_SCLK 12
#define TFT_CS   10
#define TFT_DC   14
#define TFT_RST  21
#define TFT_BL   46

// Touch Controller
#define TOUCH_CS 16
#define TOUCH_IRQ 15
```

## Troubleshooting

### Common Issues

#### Display Not Working
- Check TFT_eSPI configuration
- Verify pin connections
- Ensure correct board selection

#### WiFi Connection Problems
- Verify SSID and password
- Check signal strength
- Ensure 2.4GHz network (ESP32 doesn't support 5GHz)

#### ACE API Connection Failed
- Verify Moonraker host IP and port
- Check if ace.py is loaded in Klipper
- Ensure network connectivity between ESP32 and Klipper host

#### Touch Not Responsive
- Check touch controller configuration
- Verify touch pin connections
- Calibrate touch if needed

### Debug Mode
Enable debug output by setting `DEBUG_ENABLED 1` in config.h and monitor serial output at 115200 baud.

## Development

### Project Structure
```
├── include/
│   ├── config.h           # Configuration constants
│   ├── display_driver.h   # Display and touch driver
│   ├── ace_api.h          # ACE Pro API client
│   ├── ui_manager.h       # LVGL UI management
│   └── network_manager.h  # WiFi and HTTP client
├── src/
│   ├── main.cpp           # Main application entry
│   ├── display_driver.cpp # Display implementation
│   ├── ace_api.cpp        # API implementation
│   ├── ui_manager.cpp     # UI implementation
│   └── network_manager.cpp# Network implementation
├── lib/                   # External libraries
└── platformio.ini         # Build configuration
```

### Adding New Features
1. **New API Commands**: Add methods to `AceAPI` class
2. **UI Components**: Extend `UIManager` with new screens/widgets
3. **Network Features**: Enhance `NetworkManager` for additional protocols

### Memory Optimization
- Use PSRAM for large buffers
- Optimize LVGL buffer sizes
- Implement lazy loading for UI elements

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## Support

For issues and questions:
- Check the troubleshooting section
- Review Klipper and ace.py documentation
- Open an issue on the project repository

## Acknowledgments

- **LVGL**: Powerful embedded graphics library
- **TFT_eSPI**: Excellent ESP32 display driver
- **Klipper**: Advanced 3D printer firmware
- **Moonraker**: Klipper API server
