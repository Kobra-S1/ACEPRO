// TFT_eSPI configuration for SC01 ESP32-S3 with 3.5" display
// Place this in your TFT_eSPI/User_Setups directory or modify User_Setup.h

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
#define TFT_BACKLIGHT_ON HIGH

// Touch settings - DISABLED for 8-bit parallel mode
// #define TOUCH_CS 16  // Disabled for 8-bit parallel

// SPI frequency settings - reduced for 8-bit parallel
#define SPI_FREQUENCY  27000000
#define SPI_READ_FREQUENCY  20000000
// #define SPI_TOUCH_FREQUENCY  2500000  // Disabled with touch

// Color order
#define TFT_RGB_ORDER TFT_BGR

// Features
#define LOAD_GLCD
#define LOAD_FONT2
#define LOAD_FONT4
#define LOAD_FONT6
#define LOAD_FONT7
#define LOAD_FONT8
#define LOAD_GFXFF

#define SMOOTH_FONT
