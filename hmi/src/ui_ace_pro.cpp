#include "ui_ace_pro.h"
#include <Arduino.h>

// Global UI elements for ACE Pro interface
static lv_obj_t * main_screen = NULL;
static lv_obj_t * status_label = NULL;
static lv_obj_t * slot_buttons[8] = {NULL};
static lv_obj_t * action_buttons[4] = {NULL};

// Color definitions for material types - Blue theme
static const uint32_t material_colors[] = {
    0x0d47a1, // Empty - Dark Blue
    0x1a237e, // Red -> Dark Indigo
    0x0277bd, // Green -> Medium Blue  
    0x1976d2, // Blue -> Blue
    0x1565c0, // Yellow -> Dark Blue
    0x303f9f, // Magenta -> Indigo
    0x0288d1, // Cyan -> Light Blue
    0x42a5f5  // White -> Light Blue
};

void AceProUI::init() {
    Serial.println("Initializing ACE Pro UI...");
    createMainScreen();
}

void AceProUI::createMainScreen() {
    // Create main screen with pure black background
    main_screen = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(main_screen, lv_color_hex(0x000000), LV_PART_MAIN);
    
    // Title bar with dark blue color
    lv_obj_t * title_bar = lv_obj_create(main_screen);
    lv_obj_set_size(title_bar, LV_HOR_RES, 50);
    lv_obj_align(title_bar, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_set_style_bg_color(title_bar, lv_color_hex(0x1a237e), LV_PART_MAIN); // Dark blue
    lv_obj_set_style_border_width(title_bar, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(title_bar, 0, LV_PART_MAIN);
    
    lv_obj_t * title = lv_label_create(title_bar);
    lv_label_set_text(title, "ACE Pro Multi-Material Control");
    lv_obj_set_style_text_color(title, lv_color_hex(0xe3f2fd), LV_PART_MAIN); // Light blue text
    lv_obj_set_style_text_font(title, &lv_font_montserrat_18, LV_PART_MAIN); // Larger, more readable font
    lv_obj_align(title, LV_ALIGN_LEFT_MID, 10, 0);
    
    // Status display with blue color scheme
    status_label = lv_label_create(main_screen);
    lv_label_set_text(status_label, "Status: Ready");
    lv_obj_set_style_text_color(status_label, lv_color_hex(0x42a5f5), LV_PART_MAIN); // Blue
    lv_obj_set_style_text_font(status_label, &lv_font_montserrat_14, LV_PART_MAIN); // Better font
    lv_obj_align(status_label, LV_ALIGN_TOP_RIGHT, -10, 60);
    
    // Material slots grid
    createMaterialSlots();
    
    // Action buttons
    createActionButtons();
    
    // Back button with blue color scheme
    lv_obj_t * btn_back = lv_btn_create(main_screen);
    lv_obj_set_size(btn_back, 80, 40);
    lv_obj_align(btn_back, LV_ALIGN_BOTTOM_LEFT, 10, -10);
    lv_obj_set_style_bg_color(btn_back, lv_color_hex(0x1565c0), LV_PART_MAIN); // Blue
    lv_obj_set_style_border_color(btn_back, lv_color_hex(0x42a5f5), LV_PART_MAIN);
    lv_obj_set_style_border_width(btn_back, 1, LV_PART_MAIN);
    lv_obj_add_event_cb(btn_back, [](lv_event_t * e) {
        Serial.println("Back button pressed");
        // TODO: Return to main screen
    }, LV_EVENT_CLICKED, NULL);
    
    lv_obj_t * back_label = lv_label_create(btn_back);
    lv_label_set_text(back_label, "Back");
    lv_obj_set_style_text_color(back_label, lv_color_hex(0xe3f2fd), LV_PART_MAIN);
    lv_obj_set_style_text_font(back_label, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_center(back_label);
}

void AceProUI::createMaterialSlots() {
    // Create 4 material slot buttons in 1x4 grid (horizontal layout) with blue theme
    for (int i = 0; i < 4; i++) {
        slot_buttons[i] = lv_btn_create(main_screen);
        lv_obj_set_size(slot_buttons[i], 110, 100); // Larger size for 4 slots
        lv_obj_align(slot_buttons[i], LV_ALIGN_TOP_LEFT, 10 + i * 120, 80); // Horizontal layout
        
        // Set initial color (empty slot) with dark blue theme
        lv_obj_set_style_bg_color(slot_buttons[i], lv_color_hex(0x0d47a1), LV_PART_MAIN); // Dark blue
        lv_obj_set_style_border_color(slot_buttons[i], lv_color_hex(0x42a5f5), LV_PART_MAIN); // Light blue border
        lv_obj_set_style_border_width(slot_buttons[i], 2, LV_PART_MAIN);
        lv_obj_set_style_radius(slot_buttons[i], 8, LV_PART_MAIN); // Rounded corners
        
        // Add click event with slot index
        lv_obj_add_event_cb(slot_buttons[i], slotButtonCallback, LV_EVENT_CLICKED, (void*)(intptr_t)i);
        
        // Create label for slot with better font
        lv_obj_t * slot_label = lv_label_create(slot_buttons[i]);
        lv_label_set_text_fmt(slot_label, "Slot %d\nEmpty", i + 1);
        lv_obj_set_style_text_color(slot_label, lv_color_hex(0xe3f2fd), LV_PART_MAIN); // Light blue text
        lv_obj_set_style_text_font(slot_label, &lv_font_montserrat_14, LV_PART_MAIN); // Better font
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
    const uint32_t button_colors[] = {0x1976d2, 0x303f9f, 0x0288d1, 0x1565c0}; // Various shades of blue
    
    for (int i = 0; i < 4; i++) {
        action_buttons[i] = lv_btn_create(main_screen);
        lv_obj_set_size(action_buttons[i], 100, 50);
        lv_obj_align(action_buttons[i], LV_ALIGN_BOTTOM_LEFT, 120 + i * 110, -60);
        lv_obj_set_style_bg_color(action_buttons[i], lv_color_hex(button_colors[i]), LV_PART_MAIN);
        lv_obj_set_style_border_color(action_buttons[i], lv_color_hex(0x42a5f5), LV_PART_MAIN);
        lv_obj_set_style_border_width(action_buttons[i], 1, LV_PART_MAIN);
        lv_obj_set_style_radius(action_buttons[i], 6, LV_PART_MAIN); // Rounded corners
        
        // Add click event with action index
        lv_obj_add_event_cb(action_buttons[i], actionButtonCallback, LV_EVENT_CLICKED, (void*)(intptr_t)i);
        
        lv_obj_t * action_label = lv_label_create(action_buttons[i]);
        lv_label_set_text(action_label, button_labels[i]);
        lv_obj_set_style_text_color(action_label, lv_color_hex(0xe3f2fd), LV_PART_MAIN); // Light blue text
        lv_obj_set_style_text_font(action_label, &lv_font_montserrat_14, LV_PART_MAIN); // Better font
        lv_obj_center(action_label);
    }
}

void AceProUI::slotButtonCallback(lv_event_t * e) {
    int slot_index = (int)(intptr_t)lv_event_get_user_data(e);
    Serial.printf("Material slot %d selected\n", slot_index + 1);
    
    // TODO: Show slot details or material selection
    // For now, just highlight the selected slot with cyan color
    for (int i = 0; i < 4; i++) { // Only handle 4 slots now
        if (slot_buttons[i] != NULL) {
            if (i == slot_index) {
                lv_obj_set_style_border_color(slot_buttons[i], lv_color_hex(0x00e5ff), LV_PART_MAIN); // Cyan highlight
                lv_obj_set_style_border_width(slot_buttons[i], 3, LV_PART_MAIN);
            } else {
                lv_obj_set_style_border_color(slot_buttons[i], lv_color_hex(0x42a5f5), LV_PART_MAIN); // Light blue
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
        uint32_t color = isError ? 0x3949ab : 0x42a5f5; // Dark blue for error, Light blue for OK
        lv_obj_set_style_text_color(status_label, lv_color_hex(color), LV_PART_MAIN);
    }
}

void AceProUI::updateMaterialSlot(int slot, const String& material, const String& color, bool isEmpty) {
    if (slot < 0 || slot >= 4 || !slot_buttons[slot]) return; // Only handle 4 slots
    
    lv_obj_t * slot_label = lv_obj_get_child(slot_buttons[slot], 0);
    
    if (isEmpty) {
        lv_label_set_text_fmt(slot_label, "Slot %d\nEmpty", slot + 1);
        lv_obj_set_style_bg_color(slot_buttons[slot], lv_color_hex(0x0d47a1), LV_PART_MAIN); // Dark blue
    } else {
        lv_label_set_text_fmt(slot_label, "Slot %d\n%s\n%s", slot + 1, material.c_str(), color.c_str());
        
        // Set button color based on material color using blue shades
        uint32_t slot_color = 0x0d47a1; // Default dark blue
        if (color.equalsIgnoreCase("red")) slot_color = 0x1a237e;     // Dark indigo
        else if (color.equalsIgnoreCase("green")) slot_color = 0x0277bd;   // Medium blue
        else if (color.equalsIgnoreCase("blue")) slot_color = 0x1976d2;    // Blue
        else if (color.equalsIgnoreCase("yellow")) slot_color = 0x1565c0;  // Dark blue
        else if (color.equalsIgnoreCase("white")) slot_color = 0x42a5f5;   // Light blue
        else if (color.equalsIgnoreCase("black")) slot_color = 0x0d47a1;   // Very dark blue
        
        lv_obj_set_style_bg_color(slot_buttons[slot], lv_color_hex(slot_color), LV_PART_MAIN);
    }
}
