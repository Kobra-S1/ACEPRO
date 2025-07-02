#ifndef UI_ACE_PRO_H
#define UI_ACE_PRO_H

#include <lvgl.h>
#include <Arduino.h>

class AceProUI {
public:
    static void init();
    static void show();
    static void updateStatus(const String& status, bool isError = false);
    static void updateMaterialSlot(int slot, const String& material, const String& color, bool isEmpty = false);
    
private:
    static void createMainScreen();
    static void createMaterialSlots();
    static void createActionButtons();
    static void slotButtonCallback(lv_event_t * e);
    static void actionButtonCallback(lv_event_t * e);
};

#endif // UI_ACE_PRO_H
