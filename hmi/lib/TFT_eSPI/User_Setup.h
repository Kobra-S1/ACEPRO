// TFT_eSPI configuration for SC01 ESP32-S3 with 3.5" display
// This file overrides the default TFT_eSPI setup

#define USER_SETUP_ID 206

// Driver selection
#define ST7796_DRIVER

// ESP32-S3 specific settings
#define TFT_PARALLEL_8_BIT

// Pin definitions for SC01
#define TFT_CS   10
#define TFT_DC   14
#define TFT_RST  21

#define TFT_WR    12
#define TFT_RD    13

#define TFT_D0    11
#define TFT_D1    1
#define TFT_D2    2
#define TFT_D3    3
#define TFT_D4    4
#define TFT_D5    5
#define TFT_D6    6
#define TFT_D7    7

// Backlight control
#define TFT_BL   46

// Display dimensions
#define TFT_WIDTH  320
#define TFT_HEIGHT 480

// Touch screen settings - DISABLED for 8-bit parallel mode
// #define TOUCH_CS 16  // Commented out to disable touch

// Color depth
#define TFT_RGB_ORDER TFT_BGR  // Color order for BGR displays

// SPI frequency (not used for parallel but required for compilation)
#define SPI_FREQUENCY  80000000
