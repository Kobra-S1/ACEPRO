#ifndef UI_ACE_PRO_H
#define UI_ACE_PRO_H

#include <lvgl.h>

// Public function declarations
void ui_ace_pro_init();
void ui_ace_pro_update(const char* ip, const char* ace_pro_status, const char* printer_status);

#endif // UI_ACE_PRO_H
