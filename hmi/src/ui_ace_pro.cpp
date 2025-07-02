#include "ui_ace_pro.h"
#include <Arduino.h>

// Global UI elements for ACE Pro interface
static lv_obj_t * main_screen = NULL;
static lv_obj_t * status_label = NULL;
static lv_obj_t * slot_buttons[8] = {NULL};
static lv_obj_t * action_buttons[4] = {NULL};

// Color definitions for material types
static const uint32_t material_colors[] = {
    0x000000, // Empty - Black
    0xFF0000, // Red
    0x00FF00, // Green  
    0x0000FF, // Blue
    0xFFFF00, // Yellow
    0xFF00FF, // Magenta
    0x00FFFF, // Cyan
    0xFFFFFF  // White
};

void AceProUI::init() {
    Serial.println("Initializing ACE Pro UI...");
    createMainScreen();
}

void AceProUI::createMainScreen() {
    // Create main screen
    main_screen = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(main_screen, lv_color_hex(0x1a1a1a), LV_PART_MAIN);
    
    // Title bar
    lv_obj_t * title_bar = lv_obj_create(main_screen);
    lv_obj_set_size(title_bar, LV_HOR_RES, 50);
    lv_obj_align(title_bar, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_set_style_bg_color(title_bar, lv_color_hex(0x003a57), LV_PART_MAIN);
    lv_obj_set_style_border_width(title_bar, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(title_bar, 0, LV_PART_MAIN);
    
    lv_obj_t * title = lv_label_create(title_bar);
    lv_label_set_text(title, "ACE Pro Multi-Material Control");
    lv_obj_set_style_text_color(title, lv_color_white(), LV_PART_MAIN);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_align(title, LV_ALIGN_LEFT_MID, 10, 0);
    
    // Status display
    status_label = lv_label_create(main_screen);
    lv_label_set_text(status_label, "Status: Ready");
    lv_obj_set_style_text_color(status_label, lv_color_hex(0x4CAF50), LV_PART_MAIN); // Green
    lv_obj_align(status_label, LV_ALIGN_TOP_RIGHT, -10, 60);
    
    // Material slots grid
    createMaterialSlots();
    
    // Action buttons
    createActionButtons();
    
    // Back button
    lv_obj_t * btn_back = lv_btn_create(main_screen);
    lv_obj_set_size(btn_back, 80, 40);
    lv_obj_align(btn_back, LV_ALIGN_BOTTOM_LEFT, 10, -10);
    lv_obj_set_style_bg_color(btn_back, lv_color_hex(0x757575), LV_PART_MAIN);
    lv_obj_add_event_cb(btn_back, [](lv_event_t * e) {
        Serial.println("Back button pressed");
        // TODO: Return to main screen
    }, LV_EVENT_CLICKED, NULL);
    
    lv_obj_t * back_label = lv_label_create(btn_back);
    lv_label_set_text(back_label, "Back");
    lv_obj_center(back_label);
}

void AceProUI::createMaterialSlots() {
    // Create 4 material slot buttons in 1x4 grid (horizontal layout)
    for (int i = 0; i < 4; i++) {
        slot_buttons[i] = lv_btn_create(main_screen);
        lv_obj_set_size(slot_buttons[i], 110, 100); // Larger size for 4 slots
        lv_obj_align(slot_buttons[i], LV_ALIGN_TOP_LEFT, 10 + i * 120, 80); // Horizontal layout
        
        // Set initial color (empty slot)
        lv_obj_set_style_bg_color(slot_buttons[i], lv_color_hex(0x424242), LV_PART_MAIN);
        lv_obj_set_style_border_color(slot_buttons[i], lv_color_hex(0x757575), LV_PART_MAIN);
        lv_obj_set_style_border_width(slot_buttons[i], 2, LV_PART_MAIN);
        
        // Add click event with slot index
        lv_obj_add_event_cb(slot_buttons[i], slotButtonCallback, LV_EVENT_CLICKED, (void*)(intptr_t)i);
        
        // Create label for slot
        lv_obj_t * slot_label = lv_label_create(slot_buttons[i]);
        lv_label_set_text_fmt(slot_label, "Slot %d\nEmpty", i + 1);
        lv_obj_set_style_text_color(slot_label, lv_color_white(), LV_PART_MAIN);
        lv_obj_set_style_text_align(slot_label, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
        lv_obj_center(slot_label);
    }
    
    // Initialize remaining slots as NULL
    for (int i = 4; i < 8; i++) {
        slot_buttons[i] = NULL;
    }
}

void AceProUI::createActionButtons() {
    const char* button_labels[] = {"Load", "Unload", "Status", "Settings"};
    const uint32_t button_colors[] = {0x4CAF50, 0xF44336, 0x2196F3, 0xFF9800}; // Green, Red, Blue, Orange
    
    for (int i = 0; i < 4; i++) {
        action_buttons[i] = lv_btn_create(main_screen);
        lv_obj_set_size(action_buttons[i], 100, 50);
        lv_obj_align(action_buttons[i], LV_ALIGN_BOTTOM_LEFT, 120 + i * 110, -60);
        lv_obj_set_style_bg_color(action_buttons[i], lv_color_hex(button_colors[i]), LV_PART_MAIN);
        
        // Add click event with action index
        lv_obj_add_event_cb(action_buttons[i], actionButtonCallback, LV_EVENT_CLICKED, (void*)(intptr_t)i);
        
        lv_obj_t * action_label = lv_label_create(action_buttons[i]);
        lv_label_set_text(action_label, button_labels[i]);
        lv_obj_set_style_text_color(action_label, lv_color_white(), LV_PART_MAIN);
        lv_obj_center(action_label);
    }
}

void AceProUI::slotButtonCallback(lv_event_t * e) {
    int slot_index = (int)(intptr_t)lv_event_get_user_data(e);
    Serial.printf("Material slot %d selected\n", slot_index + 1);
    
    // TODO: Show slot details or material selection
    // For now, just highlight the selected slot
    for (int i = 0; i < 4; i++) { // Only handle 4 slots now
        if (slot_buttons[i] != NULL) {
            if (i == slot_index) {
                lv_obj_set_style_border_color(slot_buttons[i], lv_color_hex(0xFFEB3B), LV_PART_MAIN); // Yellow highlight
                lv_obj_set_style_border_width(slot_buttons[i], 3, LV_PART_MAIN);
            } else {
                lv_obj_set_style_border_color(slot_buttons[i], lv_color_hex(0x757575), LV_PART_MAIN);
                lv_obj_set_style_border_width(slot_buttons[i], 2, LV_PART_MAIN);
            }
        }
    }
}

void AceProUI::actionButtonCallback(lv_event_t * e) {
    int action_index = (int)(intptr_t)lv_event_get_user_data(e);
    const char* actions[] = {"Load", "Unload", "Status", "Settings"};
    
    Serial.printf("Action button pressed: %s\n", actions[action_index]);
    
    switch (action_index) {
        case 0: // Load
            Serial.println("Starting filament load sequence...");
            // TODO: Implement load sequence
            break;
        case 1: // Unload
            Serial.println("Starting filament unload sequence...");
            // TODO: Implement unload sequence
            break;
        case 2: // Status
            Serial.println("Refreshing ACE Pro status...");
            // TODO: Query status from Moonraker
            break;
        case 3: // Settings
            Serial.println("Opening settings...");
            // TODO: Show settings screen
            break;
    }
}

void AceProUI::show() {
    if (main_screen) {
        lv_scr_load(main_screen);
        Serial.println("ACE Pro UI loaded");
    }
}

void AceProUI::updateStatus(const String& status, bool isError) {
    if (status_label) {
        lv_label_set_text(status_label, status.c_str());
        uint32_t color = isError ? 0xF44336 : 0x4CAF50; // Red for error, Green for OK
        lv_obj_set_style_text_color(status_label, lv_color_hex(color), LV_PART_MAIN);
    }
}

void AceProUI::updateMaterialSlot(int slot, const String& material, const String& color, bool isEmpty) {
    if (slot < 0 || slot >= 4 || !slot_buttons[slot]) return; // Only handle 4 slots
    
    lv_obj_t * slot_label = lv_obj_get_child(slot_buttons[slot], 0);
    
    if (isEmpty) {
        lv_label_set_text_fmt(slot_label, "Slot %d\nEmpty", slot + 1);
        lv_obj_set_style_bg_color(slot_buttons[slot], lv_color_hex(0x424242), LV_PART_MAIN);
    } else {
        lv_label_set_text_fmt(slot_label, "Slot %d\n%s\n%s", slot + 1, material.c_str(), color.c_str());
        
        // Set button color based on material color
        uint32_t slot_color = 0x424242; // Default gray
        if (color.equalsIgnoreCase("red")) slot_color = 0x8B0000;
        else if (color.equalsIgnoreCase("green")) slot_color = 0x006400;
        else if (color.equalsIgnoreCase("blue")) slot_color = 0x000080;
        else if (color.equalsIgnoreCase("yellow")) slot_color = 0x8B8000;
        else if (color.equalsIgnoreCase("white")) slot_color = 0x696969;
        else if (color.equalsIgnoreCase("black")) slot_color = 0x2F2F2F;
        
        lv_obj_set_style_bg_color(slot_buttons[slot], lv_color_hex(slot_color), LV_PART_MAIN);
    }
}
