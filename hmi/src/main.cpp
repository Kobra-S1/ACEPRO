/*
 * SC01 Plus Display Test with LovyanGFX
 * Based on working configuration from CarloFalco/SC01Plus-platformio-display
 */

#define LGFX_USE_V1
#include <Arduino.h>
#include <LovyanGFX.hpp>
#include <lvgl.h>
#include "ui_ace_pro.h"

// SC01 Plus display configuration using LovyanGFX
class LGFX : public lgfx::LGFX_Device
{
    lgfx::Panel_ST7796 _panel_instance;
    lgfx::Bus_Parallel8 _bus_instance;   
    lgfx::Light_PWM _light_instance;
    lgfx::Touch_FT5x06 _touch_instance; // FT6336U is compatible

public:
    LGFX(void)
    {
        { // Configure parallel bus
            auto cfg = _bus_instance.config();

            cfg.freq_write = 20000000;    // Reduced frequency for stability
            cfg.freq_read = 8000000;      // Add read frequency
            cfg.pin_wr = 47; // WR pin
            cfg.pin_rd = -1; // RD pin (not used)
            cfg.pin_rs = 0;  // RS(D/C) pin
            cfg.pin_d0 = 9;  // D0
            cfg.pin_d1 = 46; // D1
            cfg.pin_d2 = 3;  // D2
            cfg.pin_d3 = 8;  // D3
            cfg.pin_d4 = 18; // D4
            cfg.pin_d5 = 17; // D5
            cfg.pin_d6 = 16; // D6
            cfg.pin_d7 = 15; // D7

            _bus_instance.config(cfg);
            _panel_instance.setBus(&_bus_instance);
        }

        { // Configure display panel
            auto cfg = _panel_instance.config();

            cfg.pin_cs = -1;   // CS pin (not used)
            cfg.pin_rst = 4;   // Reset pin
            cfg.pin_busy = -1; // Busy pin (not used)

            cfg.memory_width = 320;  // Driver IC width
            cfg.memory_height = 480; // Driver IC height
            cfg.panel_width = 320;   // Actual width
            cfg.panel_height = 480;  // Actual height
            cfg.offset_x = 0;
            cfg.offset_y = 0;
            cfg.offset_rotation = 0;
            cfg.dummy_read_pixel = 8;
            cfg.dummy_read_bits = 1;
            cfg.readable = true;
            cfg.invert = true;
            cfg.rgb_order = false;
            cfg.dlen_16bit = false;
            cfg.bus_shared = true;

            _panel_instance.config(cfg);
        }

        { // Configure backlight
            auto cfg = _light_instance.config();

            cfg.pin_bl = 45;     // Backlight pin
            cfg.invert = false;
            cfg.freq = 44100;     // Lower PWM frequency to reduce noise
            cfg.pwm_channel = 0; // Use channel 1 instead of 0

            _light_instance.config(cfg);
            _panel_instance.setLight(&_light_instance);
        }

        { // Configure touch (FT6336U)
            auto cfg = _touch_instance.config();

            cfg.x_min = 0;
            cfg.x_max = 319;
            cfg.y_min = 0;
            cfg.y_max = 479;
            cfg.pin_int = 7;
            cfg.bus_shared = false;
            cfg.offset_rotation = 0;

            // I2C configuration
            cfg.i2c_port = 0;
            cfg.i2c_addr = 0x38;
            cfg.pin_sda = 6;
            cfg.pin_scl = 5;
            cfg.freq = 400000;

            _touch_instance.config(cfg);
            _panel_instance.setTouch(&_touch_instance);
        }

        setPanel(&_panel_instance);
    }
};

LGFX tft;

// Display configuration
#define SCREEN_WIDTH  480  // SC01 Plus is 480x320 in landscape
#define SCREEN_HEIGHT 320

// LVGL configuration
static lv_disp_draw_buf_t draw_buf;
static lv_color_t buf[SCREEN_WIDTH * 10];
static lv_disp_drv_t disp_drv;
static lv_indev_drv_t indev_drv;

// Display flush callback for LVGL
void my_disp_flush(lv_disp_drv_t *disp, const lv_area_t *area, lv_color_t *color_p)
{
    uint32_t w = (area->x2 - area->x1 + 1);
    uint32_t h = (area->y2 - area->y1 + 1);

    tft.startWrite();
    tft.setAddrWindow(area->x1, area->y1, w, h);
    tft.writePixels((lgfx::rgb565_t*)&color_p->full, w * h);
    tft.endWrite();

    lv_disp_flush_ready(disp);
}

// Touch input callback for LVGL
void my_touch_read(lv_indev_drv_t *indev_driver, lv_indev_data_t *data)
{
    uint16_t touchX, touchY;
    bool touched = tft.getTouch(&touchX, &touchY);

    if (touched) {
        data->state = LV_INDEV_STATE_PR;
        data->point.x = touchX;
        data->point.y = touchY;
    } else {
        data->state = LV_INDEV_STATE_REL;
    }
}

void setup()
{
    Serial.begin(115200);
    Serial.println("\n=== SC01 Plus Display Test with LovyanGFX ===");
    
    // Initialize display
    Serial.println("Initializing display...");
    tft.begin();
    tft.setRotation(3); // Landscape mode (480x320)
    tft.setBrightness(200); // Slightly lower brightness to reduce noise
    
    // Additional display settings for stability
    tft.setColorDepth(16); // Ensure 16-bit color
    
    Serial.printf("Display initialized: %dx%d\n", tft.width(), tft.height());
    
    // Test basic colors with smoother transitions
    Serial.println("Testing basic colors...");
    tft.fillScreen(0x1800); // Dark red
    delay(1000);
    tft.fillScreen(0x0320); // Dark green
    delay(1000);
    tft.fillScreen(0x0018); // Dark blue
    delay(1000);
    tft.fillScreen(TFT_BLACK);
    
    // Test text and shapes with better contrast
    Serial.println("Testing text and shapes...");
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.setTextSize(2);
    tft.setCursor(20, 20);
    tft.println("SC01 Plus Test");
    tft.setCursor(20, 50);
    tft.printf("Resolution: %dx%d", tft.width(), tft.height());
    tft.setCursor(20, 80);
    tft.println("LovyanGFX + ST7796");
    tft.setCursor(20, 110);
    tft.println("Optimized Timing");
    
    // Draw some shapes with softer colors
    tft.fillRect(350, 50, 100, 50, 0x8000); // Darker red
    tft.fillCircle(400, 150, 30, 0x0400);   // Darker green
    tft.drawLine(20, 200, 460, 200, 0x0010); // Darker blue
    
    delay(3000);
    
    // Initialize LVGL
    Serial.println("Initializing LVGL...");
    lv_init();
    
    // Initialize display buffer
    lv_disp_draw_buf_init(&draw_buf, buf, NULL, SCREEN_WIDTH * 10);
    
    // Initialize display driver
    lv_disp_drv_init(&disp_drv);
    disp_drv.hor_res = SCREEN_WIDTH;
    disp_drv.ver_res = SCREEN_HEIGHT;
    disp_drv.flush_cb = my_disp_flush;
    disp_drv.draw_buf = &draw_buf;
    lv_disp_drv_register(&disp_drv);
    
    // Initialize input device driver
    lv_indev_drv_init(&indev_drv);
    indev_drv.type = LV_INDEV_TYPE_POINTER;
    indev_drv.read_cb = my_touch_read;
    lv_indev_drv_register(&indev_drv);
    
    // Start directly with ACE Pro interface
    Serial.println("Initializing ACE Pro interface...");
    AceProUI::init();
    AceProUI::show();
    
    // Add some demo material data
    delay(100); // Small delay to ensure UI is loaded
    AceProUI::updateMaterialSlot(0, "PLA", "Red", false);
    AceProUI::updateMaterialSlot(1, "PETG", "Blue", false);
    AceProUI::updateMaterialSlot(2, "ABS", "Black", false);
    AceProUI::updateMaterialSlot(3, "TPU", "Green", false);
    // All 4 slots filled
    AceProUI::updateStatus("Status: 4 materials loaded, Ready");
    
    Serial.println("Setup complete!");
}

void loop()
{
    lv_timer_handler();
    delay(5);
}
