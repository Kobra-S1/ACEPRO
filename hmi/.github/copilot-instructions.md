<!-- Use this file to provide workspace-specific custom instructions to Copilot. For more details, visit https://code.visualstudio.com/docs/copilot/copilot-customization#_use-a-githubcopilotinstructionsmd-file -->

# ACE Pro HMI Project Instructions

This is an ESP32-based touchscreen HMI project for controlling the Anycubic ACE Pro multi-material unit.

## Project Overview
- **Hardware**: SC01 ESP32 touchscreen display
- **Framework**: Arduino/PlatformIO with LVGL for UI
- **Communication**: WiFi connection to Klipper host via Moonraker API
- **Purpose**: Control ACE Pro filament operations, inventory management, and settings

## Key Components
- Material inventory display and editing interface
- Real-time status monitoring
- Filament load/unload controls
- Settings configuration
- Network connectivity management

## Development Guidelines
- Use LVGL for all UI components
- Implement proper error handling for network operations
- Follow modular code structure for maintainability
- Use async operations for API calls to maintain UI responsiveness
- Include proper memory management for ESP32 limitations

## API Communication
- All ACE Pro operations should use the existing ace.py commands via Moonraker API
- Implement proper JSON parsing for API responses
- Handle network timeouts and connection errors gracefully
