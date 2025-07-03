// Example configuration for ACE Pro HMI
// Copy this to include/config.h and modify as needed

#ifndef CONFIG_H
#define CONFIG_H

// ========================
// WiFi Configuration
// ========================
// Replace with your WiFi network credentials
#define WIFI_SSID "space"
#define WIFI_PASSWORD "Krisz1978"

// ========================
// Klipper/Moonraker Configuration
// ========================
// Replace with your Klipper host details
#define MOONRAKER_HOST "10.9.9.155"  // Your Klipper host IP address
#define MOONRAKER_PORT 7125              // Default Moonraker port
#define MOONRAKER_API_KEY ""             // Optional API key if configured

// ========================
// Display Configuration
// ========================
// SC01 ESP32-S3 with 3.5" display settings
#define SCREEN_WIDTH 480
#define SCREEN_HEIGHT 320
#define SCREEN_ROTATION 3  // 0=0°, 1=90°, 2=180°, 3=270°

// ========================
// Hardware Pin Configuration
// ========================
// These are the default pins for SC01 - modify if using different hardware

// TFT Display Pins
#define TFT_MOSI 11
#define TFT_MISO 13
#define TFT_SCLK 12
#define TFT_CS   10
#define TFT_DC   14
#define TFT_RST  21
#define TFT_BL   46  // Backlight control

// Touch Controller Pins - DISABLED for 8-bit parallel mode
// #define TOUCH_CS 16
// #define TOUCH_IRQ 15

// ========================
// ACE Pro Configuration
// ========================
#define ACE_SLOT_COUNT 4                    // Number of material slots
#define MAX_MATERIAL_NAME_LENGTH 20         // Maximum characters for material names
#define MAX_COLOR_NAME_LENGTH 15            // Maximum characters for color names

// ========================
// UI and Network Timing
// ========================
#define UI_UPDATE_INTERVAL 1000             // UI refresh rate (ms)
#define STATUS_UPDATE_INTERVAL 2000         // ACE status polling rate (ms)
#define CONNECTION_RETRY_INTERVAL 5000      // WiFi reconnection interval (ms)
#define HTTP_TIMEOUT 5000                   // HTTP request timeout (ms)
#define CONNECTION_TIMEOUT 10000            // Connection establishment timeout (ms)

// ========================
// Debug Configuration
// ========================
#define DEBUG_ENABLED 1                     // Enable debug output
#define SERIAL_BAUD 115200                  // Serial communication speed

// Debug macros
#if DEBUG_ENABLED
  #define DEBUG_PRINT(x) Serial.print(x)
  #define DEBUG_PRINTLN(x) Serial.println(x)
  #define DEBUG_PRINTF(fmt, ...) Serial.printf(fmt, __VA_ARGS__)
#else
  #define DEBUG_PRINT(x)
  #define DEBUG_PRINTLN(x)
  #define DEBUG_PRINTF(fmt, ...)
#endif

// ========================
// Advanced Settings
// ========================
// Uncomment and modify these if needed

// Custom material slot colors (RGB values)
// #define SLOT_EMPTY_COLOR 0x808080        // Gray for empty slots
// #define SLOT_ERROR_COLOR 0xFF0000        // Red for error state

// Custom UI colors
// #define UI_PRIMARY_COLOR LV_COLOR_BLUE
// #define UI_SECONDARY_COLOR LV_COLOR_GREY
// #define UI_ACCENT_COLOR LV_COLOR_GREEN

// Memory optimization
// #define LVGL_BUFFER_SIZE (SCREEN_WIDTH * SCREEN_HEIGHT / 10)
// #define MAX_CONCURRENT_REQUESTS 3

// Power management
// #define SLEEP_TIMEOUT 300000             // Auto-sleep after 5 minutes (ms)
// #define BACKLIGHT_DIM_LEVEL 50           // Dimmed backlight level (0-255)

#endif // CONFIG_H

/*
 * SETUP INSTRUCTIONS:
 * 
 * 1. Update WiFi credentials above
 * 2. Set correct Moonraker host IP address
 * 3. Verify hardware pin assignments match your board
 * 4. Build and upload to your ESP32-S3
 * 5. Monitor serial output for connection status
 * 
 * NETWORK REQUIREMENTS:
 * - ESP32 and Klipper host must be on same network
 * - Ensure ace.py is loaded in Klipper configuration
 * - Moonraker must be accessible on specified port
 * 
 * TROUBLESHOOTING:
 * - Enable DEBUG_ENABLED and check serial monitor
 * - Verify network connectivity with ping test
 * - Check Moonraker logs for API access
 * - Ensure ACE Pro is properly configured in Klipper
 */
