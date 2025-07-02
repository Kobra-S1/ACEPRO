#ifndef DISPLAY_DRIVER_H
#define DISPLAY_DRIVER_H

#include <lvgl.h>
#include <SPI.h>
#include "config.h"

class DisplayDriver {
private:
  static const int BUFFER_SIZE = SCREEN_WIDTH * SCREEN_HEIGHT / 10;
  static lv_disp_draw_buf_t draw_buf;
  static lv_color_t buf[BUFFER_SIZE];
  static lv_disp_drv_t disp_drv;
  // Touch disabled for 8-bit parallel mode
  
  // Display callback functions
  static void disp_flush(lv_disp_drv_t * disp, const lv_area_t * area, lv_color_t * color_p);
  // Touch functions removed for 8-bit parallel mode
  
  // Hardware initialization
  void initTFT();
  // Touch initialization removed for 8-bit parallel mode
  
public:
  DisplayDriver();
  void init();
  void setBrightness(uint8_t brightness);
  // Touch functions disabled for 8-bit parallel mode
  void sleep();
  void wake();
};

#endif // DISPLAY_DRIVER_H
