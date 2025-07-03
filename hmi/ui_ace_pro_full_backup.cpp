#include "ui_ace_pro.h"
// #include "ace_api.h"  // Commented out until ArduinoJson is available
#include <Arduino.h>

// Temporary simple struct definitions for UI (until ace_api.h is properly implemented)
struct MaterialSlot {
    int index;
    String status;
    String material;
    String color;
    int temp;
    uint32_t colorRGB;
};

struct AceStatus {
    String status;
    int currentTool;
    float temperature;
    bool endlessSpoolEnabled;
    bool runoutDetected;
    bool inProgress;
    MaterialSlot slots[4];
    String lastError;
    unsigned long lastUpdate;
};

// Constants
#define ACE_SLOT_COUNT 4

// Global UI elements for ACE Pro interface
static lv_obj_t * main_screen = NULL;
static lv_obj_t * material_detail_screen = NULL;
static lv_obj_t * settings_screen = NULL;
static lv_obj_t * network_screen = NULL;
static lv_obj_t * dryer_screen = NULL;
static lv_obj_t * diagnostics_screen = NULL;

// UI Components
static lv_obj_t * status_bar = NULL;
static lv_obj_t * navigation_bar = NULL;
static lv_obj_t * status_label = NULL;
static lv_obj_t * connection_label = NULL;
static lv_obj_t * time_label = NULL;
static lv_obj_t * slot_buttons[4] = {NULL};
static lv_obj_t * action_buttons[4] = {NULL};
static lv_obj_t * nav_buttons[6] = {NULL};

// Dialog objects
static lv_obj_t * progress_dialog = NULL;
static lv_obj_t * error_dialog = NULL;
static lv_obj_t * progress_bar = NULL;
static lv_obj_t * progress_label = NULL;

// UI State
UIScreen AceProUI::currentScreen = UIScreen::MAIN_OVERVIEW;
int AceProUI::selectedSlot = -1;
bool AceProUI::isProgressShowing = false;
bool AceProUI::isErrorShowing = false;

// Color theme definitions
#define THEME_BG_PRIMARY     0x000000  // Pure black
#define THEME_BG_SECONDARY   0x0d47a1  // Dark blue
#define THEME_BG_ACCENT      0x1976d2  // Medium blue
#define THEME_TEXT_PRIMARY   0xe3f2fd  // Light blue
#define THEME_TEXT_SECONDARY 0x90caf9  // Medium light blue
#define THEME_BORDER         0x42a5f5  // Light blue
#define THEME_HIGHLIGHT      0x00e5ff  // Cyan
#define THEME_ERROR          0x3949ab  // Dark blue (error)
#define THEME_SUCCESS        0x0288d1  // Light blue (success)

void AceProUI::init() {
    Serial.println("Initializing ACE Pro UI...");
    createMainScreen();
    createMaterialDetailScreen();
    createSettingsScreen();
    createNetworkScreen();
    createDryerScreen();
    createDiagnosticsScreen();
    createProgressDialog();
    createErrorDialog();
    
    // Start with main screen
    showScreen(UIScreen::MAIN_OVERVIEW);
}

void AceProUI::createMainScreen() {
    Serial.println("Creating main screen...");
    
    // Create main screen with pure black background
    main_screen = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(main_screen, lv_color_hex(THEME_BG_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_pad_all(main_screen, 0, LV_PART_MAIN);
    
    // Create status bar at top
    createStatusBar();
    
    // Create navigation bar at bottom
    createNavigationBar();
    
    // Create main content area
    lv_obj_t * content_area = lv_obj_create(main_screen);
    lv_obj_set_size(content_area, 480, 200);
    lv_obj_align(content_area, LV_ALIGN_CENTER, 0, -10);
    lv_obj_set_style_bg_color(content_area, lv_color_hex(THEME_BG_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_border_width(content_area, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(content_area, 10, LV_PART_MAIN);
    
    // Title
    lv_obj_t * title = lv_label_create(content_area);
    lv_label_set_text(title, "ACE Pro Multi-Material Control");
    lv_obj_set_style_text_color(title, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);
    
    // Material slots
    createMaterialSlots();
    
    // Action buttons
    createActionButtons();
}

void AceProUI::createStatusBar() {
    // Status bar at top
    status_bar = lv_obj_create(main_screen);
    lv_obj_set_size(status_bar, 480, 40);
    lv_obj_align(status_bar, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_set_style_bg_color(status_bar, lv_color_hex(THEME_BG_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_border_width(status_bar, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(status_bar, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(status_bar, 5, LV_PART_MAIN);
    
    // Status label (left)
    status_label = lv_label_create(status_bar);
    lv_label_set_text(status_label, "Status: Ready");
    lv_obj_set_style_text_color(status_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(status_label, &lv_font_montserrat_12, LV_PART_MAIN);
    lv_obj_align(status_label, LV_ALIGN_LEFT_MID, 5, 0);
    
    // Connection status (center)
    connection_label = lv_label_create(status_bar);
    lv_label_set_text(connection_label, "WiFi: Connected");
    lv_obj_set_style_text_color(connection_label, lv_color_hex(THEME_SUCCESS), LV_PART_MAIN);
    lv_obj_set_style_text_font(connection_label, &lv_font_montserrat_12, LV_PART_MAIN);
    lv_obj_align(connection_label, LV_ALIGN_CENTER, 0, 0);
    
    // Time label (right)
    time_label = lv_label_create(status_bar);
    lv_label_set_text(time_label, "12:34");
    lv_obj_set_style_text_color(time_label, lv_color_hex(THEME_TEXT_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(time_label, &lv_font_montserrat_12, LV_PART_MAIN);
    lv_obj_align(time_label, LV_ALIGN_RIGHT_MID, -5, 0);
}

void AceProUI::createNavigationBar() {
    // Navigation bar at bottom
    navigation_bar = lv_obj_create(main_screen);
    lv_obj_set_size(navigation_bar, 480, 50);
    lv_obj_align(navigation_bar, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_set_style_bg_color(navigation_bar, lv_color_hex(THEME_BG_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_border_width(navigation_bar, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(navigation_bar, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(navigation_bar, 5, LV_PART_MAIN);
    
    // Navigation buttons
    const char* nav_labels[] = {"Overview", "Material", "Settings", "Network", "Dryer", "Diagnostics"};
    int button_width = 75;
    int button_height = 35;
    int spacing = 2;
    
    for (int i = 0; i < 6; i++) {
        nav_buttons[i] = lv_btn_create(navigation_bar);
        lv_obj_set_size(nav_buttons[i], button_width, button_height);
        lv_obj_align(nav_buttons[i], LV_ALIGN_LEFT_MID, 5 + i * (button_width + spacing), 0);
        
        // Set button style
        lv_obj_set_style_bg_color(nav_buttons[i], lv_color_hex(THEME_BG_ACCENT), LV_PART_MAIN);
        lv_obj_set_style_border_color(nav_buttons[i], lv_color_hex(THEME_BORDER), LV_PART_MAIN);
        lv_obj_set_style_border_width(nav_buttons[i], 1, LV_PART_MAIN);
        lv_obj_set_style_radius(nav_buttons[i], 4, LV_PART_MAIN);
        
        // Add callback
        lv_obj_add_event_cb(nav_buttons[i], navigationCallback, LV_EVENT_CLICKED, (void*)(intptr_t)i);
        
        // Button label
        lv_obj_t * label = lv_label_create(nav_buttons[i]);
        lv_label_set_text(label, nav_labels[i]);
        lv_obj_set_style_text_color(label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
        lv_obj_set_style_text_font(label, &lv_font_montserrat_12, LV_PART_MAIN);
        lv_obj_center(label);
    }
    
    updateNavigationButtons();
}

void AceProUI::createMaterialSlots() {
    // Create 4 material slot buttons in 2x2 grid for better use of space
    for (int i = 0; i < 4; i++) {
        int row = i / 2;
        int col = i % 2;
        
        slot_buttons[i] = lv_btn_create(main_screen);
        lv_obj_set_size(slot_buttons[i], 180, 80);
        lv_obj_align(slot_buttons[i], LV_ALIGN_TOP_LEFT, 60 + col * 200, 80 + row * 90);
        
        // Set initial style
        applyTheme(slot_buttons[i], false);
        
        // Add click event
        lv_obj_add_event_cb(slot_buttons[i], slotButtonCallback, LV_EVENT_CLICKED, (void*)(intptr_t)i);
        
        // Create label
        lv_obj_t * slot_label = lv_label_create(slot_buttons[i]);
        lv_label_set_text_fmt(slot_label, "Slot %d\nEmpty", i + 1);
        lv_obj_set_style_text_color(slot_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
        lv_obj_set_style_text_font(slot_label, &lv_font_montserrat_12, LV_PART_MAIN);
        lv_obj_set_style_text_align(slot_label, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
        lv_obj_center(slot_label);
    }
}

void AceProUI::createActionButtons() {
    const char* button_labels[] = {"Load", "Unload", "Change", "Settings"};
    const uint32_t button_colors[] = {THEME_SUCCESS, THEME_ERROR, THEME_BG_ACCENT, THEME_BG_SECONDARY};
    
    for (int i = 0; i < 4; i++) {
        action_buttons[i] = lv_btn_create(main_screen);
        lv_obj_set_size(action_buttons[i], 100, 40);
        lv_obj_align(action_buttons[i], LV_ALIGN_BOTTOM_LEFT, 40 + i * 110, -70);
        
        lv_obj_set_style_bg_color(action_buttons[i], lv_color_hex(button_colors[i]), LV_PART_MAIN);
        lv_obj_set_style_border_color(action_buttons[i], lv_color_hex(THEME_BORDER), LV_PART_MAIN);
        lv_obj_set_style_border_width(action_buttons[i], 1, LV_PART_MAIN);
        lv_obj_set_style_radius(action_buttons[i], 6, LV_PART_MAIN);
        
        lv_obj_add_event_cb(action_buttons[i], actionButtonCallback, LV_EVENT_CLICKED, (void*)(intptr_t)i);
        
        lv_obj_t * action_label = lv_label_create(action_buttons[i]);
        lv_label_set_text(action_label, button_labels[i]);
        lv_obj_set_style_text_color(action_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
        lv_obj_set_style_text_font(action_label, &lv_font_montserrat_12, LV_PART_MAIN);
        lv_obj_center(action_label);
    }
}

// Helper method to apply consistent theme
void AceProUI::applyTheme(lv_obj_t* obj, bool isPrimary) {
    uint32_t bg_color = isPrimary ? THEME_BG_ACCENT : THEME_BG_SECONDARY;
    
    lv_obj_set_style_bg_color(obj, lv_color_hex(bg_color), LV_PART_MAIN);
    lv_obj_set_style_border_color(obj, lv_color_hex(THEME_BORDER), LV_PART_MAIN);
    lv_obj_set_style_border_width(obj, 2, LV_PART_MAIN);
    lv_obj_set_style_radius(obj, 8, LV_PART_MAIN);
}

// Callback implementations
void AceProUI::navigationCallback(lv_event_t * e) {
    int nav_index = (int)(intptr_t)lv_event_get_user_data(e);
    UIScreen targetScreen = static_cast<UIScreen>(nav_index);
    showScreen(targetScreen);
    Serial.printf("Navigation to screen: %d\n", nav_index);
}

void AceProUI::slotButtonCallback(lv_event_t * e) {
    int slot_index = (int)(intptr_t)lv_event_get_user_data(e);
    selectedSlot = slot_index;
    Serial.printf("Material slot %d selected\n", slot_index + 1);
    
    // Update visual selection
    for (int i = 0; i < 4; i++) {
        if (slot_buttons[i] != NULL) {
            if (i == slot_index) {
                lv_obj_set_style_border_color(slot_buttons[i], lv_color_hex(THEME_HIGHLIGHT), LV_PART_MAIN);
                lv_obj_set_style_border_width(slot_buttons[i], 3, LV_PART_MAIN);
            } else {
                lv_obj_set_style_border_color(slot_buttons[i], lv_color_hex(THEME_BORDER), LV_PART_MAIN);
                lv_obj_set_style_border_width(slot_buttons[i], 2, LV_PART_MAIN);
            }
        }
    }
    
    // Double tap to show material detail
    static uint32_t last_press = 0;
    uint32_t current_time = lv_tick_get();
    if (current_time - last_press < 300) {
        showMaterialDetail(slot_index);
    }
    last_press = current_time;
}

void AceProUI::actionButtonCallback(lv_event_t * e) {
    int action_index = (int)(intptr_t)lv_event_get_user_data(e);
    const char* actions[] = {"Load", "Unload", "Change", "Settings"};
    
    Serial.printf("Action button pressed: %s\n", actions[action_index]);
    
    switch (action_index) {
        case 0: // Load
            if (selectedSlot >= 0) {
                showProgressDialog("Loading Filament", "Loading filament into slot " + String(selectedSlot + 1));
                // TODO: Call ACE API to load filament
                // AceAPI::loadFilament(selectedSlot);
            } else {
                showErrorDialog("Please select a slot first");
            }
            break;
            
        case 1: // Unload
            if (selectedSlot >= 0) {
                showProgressDialog("Unloading Filament", "Unloading filament from slot " + String(selectedSlot + 1));
                // TODO: Call ACE API to unload filament
                // AceAPI::unloadFilament(selectedSlot);
            } else {
                showErrorDialog("Please select a slot first");
            }
            break;
            
        case 2: // Change Tool
            if (selectedSlot >= 0) {
                showProgressDialog("Changing Tool", "Changing to slot " + String(selectedSlot + 1));
                // TODO: Call ACE API to change tool
                // AceAPI::changeTool(selectedSlot);
            } else {
                showErrorDialog("Please select a slot first");
            }
            break;
            
        case 3: // Settings
            showScreen(UIScreen::SETTINGS);
            break;
    }
}

// Screen management functions
void AceProUI::showScreen(UIScreen screen) {
    // Hide current screen
    switch (currentScreen) {
        case UIScreen::MAIN_OVERVIEW:
            if (main_screen) lv_obj_add_flag(main_screen, LV_OBJ_FLAG_HIDDEN);
            break;
        case UIScreen::MATERIAL_DETAIL:
            if (material_detail_screen) lv_obj_add_flag(material_detail_screen, LV_OBJ_FLAG_HIDDEN);
            break;
        case UIScreen::SETTINGS:
            if (settings_screen) lv_obj_add_flag(settings_screen, LV_OBJ_FLAG_HIDDEN);
            break;
        case UIScreen::NETWORK_STATUS:
            if (network_screen) lv_obj_add_flag(network_screen, LV_OBJ_FLAG_HIDDEN);
            break;
        case UIScreen::DRYER_CONTROL:
            if (dryer_screen) lv_obj_add_flag(dryer_screen, LV_OBJ_FLAG_HIDDEN);
            break;
        case UIScreen::DIAGNOSTICS:
            if (diagnostics_screen) lv_obj_add_flag(diagnostics_screen, LV_OBJ_FLAG_HIDDEN);
            break;
    }
    
    // Show new screen
    switch (screen) {
        case UIScreen::MAIN_OVERVIEW:
            if (main_screen) {
                lv_obj_clear_flag(main_screen, LV_OBJ_FLAG_HIDDEN);
                lv_scr_load(main_screen);
            }
            break;
        case UIScreen::MATERIAL_DETAIL:
            if (material_detail_screen) {
                lv_obj_clear_flag(material_detail_screen, LV_OBJ_FLAG_HIDDEN);
                lv_scr_load(material_detail_screen);
            }
            break;
        case UIScreen::SETTINGS:
            if (settings_screen) {
                lv_obj_clear_flag(settings_screen, LV_OBJ_FLAG_HIDDEN);
                lv_scr_load(settings_screen);
            }
            break;
        case UIScreen::NETWORK_STATUS:
            if (network_screen) {
                lv_obj_clear_flag(network_screen, LV_OBJ_FLAG_HIDDEN);
                lv_scr_load(network_screen);
            }
            break;
        case UIScreen::DRYER_CONTROL:
            if (dryer_screen) {
                lv_obj_clear_flag(dryer_screen, LV_OBJ_FLAG_HIDDEN);
                lv_scr_load(dryer_screen);
            }
            break;
        case UIScreen::DIAGNOSTICS:
            if (diagnostics_screen) {
                lv_obj_clear_flag(diagnostics_screen, LV_OBJ_FLAG_HIDDEN);
                lv_scr_load(diagnostics_screen);
            }
            break;
    }
    
    currentScreen = screen;
    updateNavigationButtons();
}

void AceProUI::updateNavigationButtons() {
    for (int i = 0; i < 6; i++) {
        if (nav_buttons[i]) {
            if (i == static_cast<int>(currentScreen)) {
                lv_obj_set_style_bg_color(nav_buttons[i], lv_color_hex(THEME_HIGHLIGHT), LV_PART_MAIN);
                lv_obj_set_style_border_width(nav_buttons[i], 2, LV_PART_MAIN);
            } else {
                lv_obj_set_style_bg_color(nav_buttons[i], lv_color_hex(THEME_BG_ACCENT), LV_PART_MAIN);
                lv_obj_set_style_border_width(nav_buttons[i], 1, LV_PART_MAIN);
            }
        }
    }
}

// Material detail screen
void AceProUI::createMaterialDetailScreen() {
    material_detail_screen = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(material_detail_screen, lv_color_hex(THEME_BG_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_pad_all(material_detail_screen, 0, LV_PART_MAIN);
    
    // Status bar
    lv_obj_t * detail_status_bar = lv_obj_create(material_detail_screen);
    lv_obj_set_size(detail_status_bar, 480, 40);
    lv_obj_align(detail_status_bar, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_set_style_bg_color(detail_status_bar, lv_color_hex(THEME_BG_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_border_width(detail_status_bar, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(detail_status_bar, 0, LV_PART_MAIN);
    
    lv_obj_t * detail_title = lv_label_create(detail_status_bar);
    lv_label_set_text(detail_title, "Material Detail - Slot 1");
    lv_obj_set_style_text_color(detail_title, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(detail_title, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_align(detail_title, LV_ALIGN_CENTER, 0, 0);
    
    // Content area
    lv_obj_t * content = lv_obj_create(material_detail_screen);
    lv_obj_set_size(content, 460, 180);
    lv_obj_align(content, LV_ALIGN_CENTER, 0, -5);
    lv_obj_set_style_bg_color(content, lv_color_hex(THEME_BG_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_border_width(content, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(content, 10, LV_PART_MAIN);
    
    // Material type dropdown
    lv_obj_t * material_label = lv_label_create(content);
    lv_label_set_text(material_label, "Material Type:");
    lv_obj_set_style_text_color(material_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(material_label, &lv_font_montserrat_12, LV_PART_MAIN);
    lv_obj_align(material_label, LV_ALIGN_TOP_LEFT, 0, 0);
    
    lv_obj_t * material_dropdown = lv_dropdown_create(content);
    lv_dropdown_set_options(material_dropdown, "PLA\nPETG\nABS\nTPU\nASA\nPC\nCustom");
    lv_obj_set_size(material_dropdown, 150, 35);
    lv_obj_align(material_dropdown, LV_ALIGN_TOP_LEFT, 120, -5);
    lv_obj_set_style_bg_color(material_dropdown, lv_color_hex(THEME_BG_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_text_color(material_dropdown, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    
    // Color selection
    lv_obj_t * color_label = lv_label_create(content);
    lv_label_set_text(color_label, "Color:");
    lv_obj_set_style_text_color(color_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(color_label, &lv_font_montserrat_12, LV_PART_MAIN);
    lv_obj_align(color_label, LV_ALIGN_TOP_LEFT, 280, 0);
    
    lv_obj_t * color_dropdown = lv_dropdown_create(content);
    lv_dropdown_set_options(color_dropdown, "Red\nGreen\nBlue\nYellow\nWhite\nBlack\nOrange\nPurple");
    lv_obj_set_size(color_dropdown, 120, 35);
    lv_obj_align(color_dropdown, LV_ALIGN_TOP_LEFT, 330, -5);
    lv_obj_set_style_bg_color(color_dropdown, lv_color_hex(THEME_BG_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_text_color(color_dropdown, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    
    // Temperature setting
    lv_obj_t * temp_label = lv_label_create(content);
    lv_label_set_text(temp_label, "Temperature:");
    lv_obj_set_style_text_color(temp_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(temp_label, &lv_font_montserrat_12, LV_PART_MAIN);
    lv_obj_align(temp_label, LV_ALIGN_TOP_LEFT, 0, 40);
    
    lv_obj_t * temp_spinbox = lv_spinbox_create(content);
    lv_spinbox_set_range(temp_spinbox, 150, 300);
    lv_spinbox_set_value(temp_spinbox, 210);
    lv_obj_set_size(temp_spinbox, 100, 35);
    lv_obj_align(temp_spinbox, LV_ALIGN_TOP_LEFT, 120, 35);
    lv_obj_set_style_bg_color(temp_spinbox, lv_color_hex(THEME_BG_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_text_color(temp_spinbox, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    
    // Action buttons
    const char* detail_actions[] = {"Load", "Unload", "Purge", "Save"};
    for (int i = 0; i < 4; i++) {
        lv_obj_t * btn = lv_btn_create(content);
        lv_obj_set_size(btn, 80, 35);
        lv_obj_align(btn, LV_ALIGN_TOP_LEFT, 20 + i * 90, 85);
        lv_obj_set_style_bg_color(btn, lv_color_hex(THEME_BG_ACCENT), LV_PART_MAIN);
        lv_obj_set_style_border_color(btn, lv_color_hex(THEME_BORDER), LV_PART_MAIN);
        lv_obj_set_style_border_width(btn, 1, LV_PART_MAIN);
        lv_obj_set_style_radius(btn, 4, LV_PART_MAIN);
        
        lv_obj_add_event_cb(btn, materialDetailCallback, LV_EVENT_CLICKED, (void*)(intptr_t)i);
        
        lv_obj_t * btn_label = lv_label_create(btn);
        lv_label_set_text(btn_label, detail_actions[i]);
        lv_obj_set_style_text_color(btn_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
        lv_obj_set_style_text_font(btn_label, &lv_font_montserrat_12, LV_PART_MAIN);
        lv_obj_center(btn_label);
    }
    
    // Navigation bar
    lv_obj_t * detail_nav_bar = lv_obj_create(material_detail_screen);
    lv_obj_set_size(detail_nav_bar, 480, 50);
    lv_obj_align(detail_nav_bar, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_set_style_bg_color(detail_nav_bar, lv_color_hex(THEME_BG_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_border_width(detail_nav_bar, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(detail_nav_bar, 0, LV_PART_MAIN);
    
    // Back button
    lv_obj_t * back_btn = lv_btn_create(detail_nav_bar);
    lv_obj_set_size(back_btn, 80, 35);
    lv_obj_align(back_btn, LV_ALIGN_LEFT_MID, 10, 0);
    lv_obj_set_style_bg_color(back_btn, lv_color_hex(THEME_BG_ACCENT), LV_PART_MAIN);
    lv_obj_add_event_cb(back_btn, [](lv_event_t * e) {
        showScreen(UIScreen::MAIN_OVERVIEW);
    }, LV_EVENT_CLICKED, NULL);
    
    lv_obj_t * back_label = lv_label_create(back_btn);
    lv_label_set_text(back_label, "Back");
    lv_obj_set_style_text_color(back_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_center(back_label);
    
    lv_obj_add_flag(material_detail_screen, LV_OBJ_FLAG_HIDDEN);
}

void AceProUI::materialDetailCallback(lv_event_t * e) {
    int action = (int)(intptr_t)lv_event_get_user_data(e);
    const char* actions[] = {"Load", "Unload", "Purge", "Save"};
    
    Serial.printf("Material detail action: %s\n", actions[action]);
    
    switch (action) {
        case 0: // Load
            showProgressDialog("Loading Material", "Loading material for slot " + String(selectedSlot + 1));
            break;
        case 1: // Unload
            showProgressDialog("Unloading Material", "Unloading material from slot " + String(selectedSlot + 1));
            break;
        case 2: // Purge
            showProgressDialog("Purging Material", "Purging material from slot " + String(selectedSlot + 1));
            break;
        case 3: // Save
            showProgressDialog("Saving Settings", "Saving material settings for slot " + String(selectedSlot + 1));
            break;
    }
}

// Settings screen
void AceProUI::createSettingsScreen() {
    settings_screen = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(settings_screen, lv_color_hex(THEME_BG_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_pad_all(settings_screen, 0, LV_PART_MAIN);
    
    // Status bar
    lv_obj_t * settings_status_bar = lv_obj_create(settings_screen);
    lv_obj_set_size(settings_status_bar, 480, 40);
    lv_obj_align(settings_status_bar, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_set_style_bg_color(settings_status_bar, lv_color_hex(THEME_BG_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_border_width(settings_status_bar, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(settings_status_bar, 0, LV_PART_MAIN);
    
    lv_obj_t * settings_title = lv_label_create(settings_status_bar);
    lv_label_set_text(settings_title, "System Settings");
    lv_obj_set_style_text_color(settings_title, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(settings_title, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_align(settings_title, LV_ALIGN_CENTER, 0, 0);
    
    // Content area
    lv_obj_t * settings_content = lv_obj_create(settings_screen);
    lv_obj_set_size(settings_content, 460, 180);
    lv_obj_align(settings_content, LV_ALIGN_CENTER, 0, -5);
    lv_obj_set_style_bg_color(settings_content, lv_color_hex(THEME_BG_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_border_width(settings_content, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(settings_content, 10, LV_PART_MAIN);
    
    // Endless spool settings
    lv_obj_t * endless_panel = lv_obj_create(settings_content);
    lv_obj_set_size(endless_panel, 440, 60);
    lv_obj_align(endless_panel, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_set_style_bg_color(endless_panel, lv_color_hex(THEME_BG_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_border_color(endless_panel, lv_color_hex(THEME_BORDER), LV_PART_MAIN);
    lv_obj_set_style_border_width(endless_panel, 1, LV_PART_MAIN);
    lv_obj_set_style_radius(endless_panel, 6, LV_PART_MAIN);
    
    lv_obj_t * endless_label = lv_label_create(endless_panel);
    lv_label_set_text(endless_label, "Endless Spool");
    lv_obj_set_style_text_color(endless_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(endless_label, &lv_font_montserrat_12, LV_PART_MAIN);
    lv_obj_align(endless_label, LV_ALIGN_TOP_LEFT, 10, 5);
    
    lv_obj_t * endless_switch = lv_switch_create(endless_panel);
    lv_obj_align(endless_switch, LV_ALIGN_TOP_RIGHT, -10, 5);
    lv_obj_set_style_bg_color(endless_switch, lv_color_hex(THEME_BG_ACCENT), LV_PART_MAIN);
    lv_obj_set_style_bg_color(endless_switch, lv_color_hex(THEME_SUCCESS), LV_PART_INDICATOR | LV_STATE_CHECKED);
    
    // Display settings
    lv_obj_t * display_panel = lv_obj_create(settings_content);
    lv_obj_set_size(display_panel, 440, 60);
    lv_obj_align(display_panel, LV_ALIGN_TOP_MID, 0, 70);
    lv_obj_set_style_bg_color(display_panel, lv_color_hex(THEME_BG_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_border_color(display_panel, lv_color_hex(THEME_BORDER), LV_PART_MAIN);
    lv_obj_set_style_border_width(display_panel, 1, LV_PART_MAIN);
    lv_obj_set_style_radius(display_panel, 6, LV_PART_MAIN);
    
    lv_obj_t * display_label = lv_label_create(display_panel);
    lv_label_set_text(display_label, "Display Brightness");
    lv_obj_set_style_text_color(display_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(display_label, &lv_font_montserrat_12, LV_PART_MAIN);
    lv_obj_align(display_label, LV_ALIGN_TOP_LEFT, 10, 5);
    
    lv_obj_t * brightness_slider = lv_slider_create(display_panel);
    lv_obj_set_size(brightness_slider, 200, 20);
    lv_obj_align(brightness_slider, LV_ALIGN_TOP_RIGHT, -10, 25);
    lv_obj_set_style_bg_color(brightness_slider, lv_color_hex(THEME_BG_ACCENT), LV_PART_MAIN);
    lv_obj_set_style_bg_color(brightness_slider, lv_color_hex(THEME_SUCCESS), LV_PART_INDICATOR);
    lv_slider_set_value(brightness_slider, 80, LV_ANIM_OFF);
    
    // Action buttons
    const char* settings_actions[] = {"Save", "Reset", "Backup", "Restore"};
    for (int i = 0; i < 4; i++) {
        lv_obj_t * btn = lv_btn_create(settings_content);
        lv_obj_set_size(btn, 80, 30);
        lv_obj_align(btn, LV_ALIGN_BOTTOM_LEFT, 20 + i * 90, -10);
        lv_obj_set_style_bg_color(btn, lv_color_hex(THEME_BG_ACCENT), LV_PART_MAIN);
        lv_obj_set_style_border_color(btn, lv_color_hex(THEME_BORDER), LV_PART_MAIN);
        lv_obj_set_style_border_width(btn, 1, LV_PART_MAIN);
        lv_obj_set_style_radius(btn, 4, LV_PART_MAIN);
        
        lv_obj_add_event_cb(btn, settingsCallback, LV_EVENT_CLICKED, (void*)(intptr_t)i);
        
        lv_obj_t * btn_label = lv_label_create(btn);
        lv_label_set_text(btn_label, settings_actions[i]);
        lv_obj_set_style_text_color(btn_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
        lv_obj_set_style_text_font(btn_label, &lv_font_montserrat_12, LV_PART_MAIN);
        lv_obj_center(btn_label);
    }
    
    // Navigation bar
    lv_obj_t * settings_nav_bar = lv_obj_create(settings_screen);
    lv_obj_set_size(settings_nav_bar, 480, 50);
    lv_obj_align(settings_nav_bar, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_set_style_bg_color(settings_nav_bar, lv_color_hex(THEME_BG_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_border_width(settings_nav_bar, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(settings_nav_bar, 0, LV_PART_MAIN);
    
    // Back button
    lv_obj_t * settings_back_btn = lv_btn_create(settings_nav_bar);
    lv_obj_set_size(settings_back_btn, 80, 35);
    lv_obj_align(settings_back_btn, LV_ALIGN_LEFT_MID, 10, 0);
    lv_obj_set_style_bg_color(settings_back_btn, lv_color_hex(THEME_BG_ACCENT), LV_PART_MAIN);
    lv_obj_add_event_cb(settings_back_btn, [](lv_event_t * e) {
        showScreen(UIScreen::MAIN_OVERVIEW);
    }, LV_EVENT_CLICKED, NULL);
    
    lv_obj_t * settings_back_label = lv_label_create(settings_back_btn);
    lv_label_set_text(settings_back_label, "Back");
    lv_obj_set_style_text_color(settings_back_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_center(settings_back_label);
    
    lv_obj_add_flag(settings_screen, LV_OBJ_FLAG_HIDDEN);
}

void AceProUI::settingsCallback(lv_event_t * e) {
    int action = (int)(intptr_t)lv_event_get_user_data(e);
    const char* actions[] = {"Save", "Reset", "Backup", "Restore"};
    
    Serial.printf("Settings action: %s\n", actions[action]);
    
    switch (action) {
        case 0: // Save
            showProgressDialog("Saving Settings", "Saving configuration to flash...");
            break;
        case 1: // Reset
            showProgressDialog("Resetting Settings", "Restoring factory defaults...");
            break;
        case 2: // Backup
            showProgressDialog("Backup Settings", "Creating settings backup...");
            break;
        case 3: // Restore
            showProgressDialog("Restore Settings", "Restoring settings from backup...");
            break;
    }
}

// Stub implementations for remaining screens
void AceProUI::createNetworkScreen() {
    network_screen = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(network_screen, lv_color_hex(THEME_BG_PRIMARY), LV_PART_MAIN);
    
    lv_obj_t * network_label = lv_label_create(network_screen);
    lv_label_set_text(network_label, "Network Screen - Under Development");
    lv_obj_set_style_text_color(network_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_center(network_label);
    
    lv_obj_add_flag(network_screen, LV_OBJ_FLAG_HIDDEN);
}

void AceProUI::createDryerScreen() {
    dryer_screen = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(dryer_screen, lv_color_hex(THEME_BG_PRIMARY), LV_PART_MAIN);
    
    lv_obj_t * dryer_label = lv_label_create(dryer_screen);
    lv_label_set_text(dryer_label, "Dryer Control - Under Development");
    lv_obj_set_style_text_color(dryer_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_center(dryer_label);
    
    lv_obj_add_flag(dryer_screen, LV_OBJ_FLAG_HIDDEN);
}

void AceProUI::createDiagnosticsScreen() {
    diagnostics_screen = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(diagnostics_screen, lv_color_hex(THEME_BG_PRIMARY), LV_PART_MAIN);
    
    lv_obj_t * diagnostics_label = lv_label_create(diagnostics_screen);
    lv_label_set_text(diagnostics_label, "Diagnostics - Under Development");
    lv_obj_set_style_text_color(diagnostics_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_center(diagnostics_label);
    
    lv_obj_add_flag(diagnostics_screen, LV_OBJ_FLAG_HIDDEN);
}

void AceProUI::networkCallback(lv_event_t * e) {
    // Placeholder for network callbacks
    Serial.println("Network callback - Not implemented");
}

void AceProUI::dryerCallback(lv_event_t * e) {
    // Placeholder for dryer callbacks
    Serial.println("Dryer callback - Not implemented");
}

// Dialog implementations
void AceProUI::createProgressDialog() {
    progress_dialog = lv_obj_create(NULL);
    lv_obj_set_size(progress_dialog, 320, 160);
    lv_obj_center(progress_dialog);
    lv_obj_set_style_bg_color(progress_dialog, lv_color_hex(THEME_BG_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_border_color(progress_dialog, lv_color_hex(THEME_BORDER), LV_PART_MAIN);
    lv_obj_set_style_border_width(progress_dialog, 2, LV_PART_MAIN);
    lv_obj_set_style_radius(progress_dialog, 10, LV_PART_MAIN);
    lv_obj_set_style_pad_all(progress_dialog, 20, LV_PART_MAIN);
    
    // Title
    lv_obj_t * progress_title = lv_label_create(progress_dialog);
    lv_label_set_text(progress_title, "Processing...");
    lv_obj_set_style_text_color(progress_title, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(progress_title, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_align(progress_title, LV_ALIGN_TOP_MID, 0, 0);
    
    // Message
    progress_label = lv_label_create(progress_dialog);
    lv_label_set_text(progress_label, "Please wait...");
    lv_obj_set_style_text_color(progress_label, lv_color_hex(THEME_TEXT_SECONDARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(progress_label, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_align(progress_label, LV_ALIGN_CENTER, 0, 0);
    lv_label_set_long_mode(progress_label, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(progress_label, 280);
    
    // Progress bar
    progress_bar = lv_bar_create(progress_dialog);
    lv_obj_set_size(progress_bar, 280, 20);
    lv_obj_align(progress_bar, LV_ALIGN_BOTTOM_MID, 0, -20);
    lv_obj_set_style_bg_color(progress_bar, lv_color_hex(THEME_BG_ACCENT), LV_PART_MAIN);
    lv_obj_set_style_bg_color(progress_bar, lv_color_hex(THEME_SUCCESS), LV_PART_INDICATOR);
    lv_bar_set_value(progress_bar, 0, LV_ANIM_OFF);
    
    // Cancel button
    lv_obj_t * cancel_btn = lv_btn_create(progress_dialog);
    lv_obj_set_size(cancel_btn, 80, 30);
    lv_obj_align(cancel_btn, LV_ALIGN_BOTTOM_RIGHT, -10, -5);
    lv_obj_set_style_bg_color(cancel_btn, lv_color_hex(THEME_ERROR), LV_PART_MAIN);
    lv_obj_add_event_cb(cancel_btn, [](lv_event_t * e) {
        hideProgressDialog();
    }, LV_EVENT_CLICKED, NULL);
    
    lv_obj_t * cancel_label = lv_label_create(cancel_btn);
    lv_label_set_text(cancel_label, "Cancel");
    lv_obj_set_style_text_color(cancel_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(cancel_label, &lv_font_montserrat_12, LV_PART_MAIN);
    lv_obj_center(cancel_label);
    
    lv_obj_add_flag(progress_dialog, LV_OBJ_FLAG_HIDDEN);
}

void AceProUI::createErrorDialog() {
    error_dialog = lv_obj_create(NULL);
    lv_obj_set_size(error_dialog, 350, 200);
    lv_obj_center(error_dialog);
    lv_obj_set_style_bg_color(error_dialog, lv_color_hex(THEME_ERROR), LV_PART_MAIN);
    lv_obj_set_style_border_color(error_dialog, lv_color_hex(0xff5252), LV_PART_MAIN);
    lv_obj_set_style_border_width(error_dialog, 3, LV_PART_MAIN);
    lv_obj_set_style_radius(error_dialog, 10, LV_PART_MAIN);
    lv_obj_set_style_pad_all(error_dialog, 20, LV_PART_MAIN);
    
    // Error icon/title
    lv_obj_t * error_title = lv_label_create(error_dialog);
    lv_label_set_text(error_title, "⚠ Error");
    lv_obj_set_style_text_color(error_title, lv_color_hex(0xff5252), LV_PART_MAIN);
    lv_obj_set_style_text_font(error_title, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_align(error_title, LV_ALIGN_TOP_MID, 0, 0);
    
    // Error message
    lv_obj_t * error_message = lv_label_create(error_dialog);
    lv_label_set_text(error_message, "An error occurred");
    lv_obj_set_style_text_color(error_message, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(error_message, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_align(error_message, LV_ALIGN_CENTER, 0, 0);
    lv_label_set_long_mode(error_message, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(error_message, 310);
    
    // OK button
    lv_obj_t * ok_btn = lv_btn_create(error_dialog);
    lv_obj_set_size(ok_btn, 100, 40);
    lv_obj_align(ok_btn, LV_ALIGN_BOTTOM_MID, 0, -10);
    lv_obj_set_style_bg_color(ok_btn, lv_color_hex(THEME_BG_ACCENT), LV_PART_MAIN);
    lv_obj_add_event_cb(ok_btn, [](lv_event_t * e) {
        lv_obj_add_flag(error_dialog, LV_OBJ_FLAG_HIDDEN);
        isErrorShowing = false;
    }, LV_EVENT_CLICKED, NULL);
    
    lv_obj_t * ok_label = lv_label_create(ok_btn);
    lv_label_set_text(ok_label, "OK");
    lv_obj_set_style_text_color(ok_label, lv_color_hex(THEME_TEXT_PRIMARY), LV_PART_MAIN);
    lv_obj_set_style_text_font(ok_label, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_center(ok_label);
    
    lv_obj_add_flag(error_dialog, LV_OBJ_FLAG_HIDDEN);
}

// Public interface methods
void AceProUI::show() {
    showScreen(UIScreen::MAIN_OVERVIEW);
    Serial.println("ACE Pro UI loaded");
}

void AceProUI::showMaterialDetail(int slot) {
    selectedSlot = slot;
    showScreen(UIScreen::MATERIAL_DETAIL);
    Serial.printf("Showing material detail for slot %d\n", slot + 1);
}

void AceProUI::showProgressDialog(const String& title, const String& message) {
    if (progress_dialog) {
        lv_obj_t* title_label = lv_obj_get_child(progress_dialog, 0);
        lv_obj_t* message_label = lv_obj_get_child(progress_dialog, 1);
        
        lv_label_set_text(title_label, title.c_str());
        lv_label_set_text(message_label, message.c_str());
        
        lv_obj_clear_flag(progress_dialog, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(progress_dialog);
        isProgressShowing = true;
    }
}

void AceProUI::hideProgressDialog() {
    if (progress_dialog) {
        lv_obj_add_flag(progress_dialog, LV_OBJ_FLAG_HIDDEN);
        isProgressShowing = false;
    }
}

void AceProUI::showErrorDialog(const String& error) {
    if (error_dialog) {
        lv_obj_t* error_message = lv_obj_get_child(error_dialog, 1);
        lv_label_set_text(error_message, error.c_str());
        
        lv_obj_clear_flag(error_dialog, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(error_dialog);
        isErrorShowing = true;
    }
}

void AceProUI::updateStatus(const String& status, bool isError) {
    if (status_label) {
        lv_label_set_text(status_label, status.c_str());
        uint32_t color = isError ? THEME_ERROR : THEME_SUCCESS;
        lv_obj_set_style_text_color(status_label, lv_color_hex(color), LV_PART_MAIN);
    }
}

void AceProUI::updateMaterialSlot(int slot, const String& material, const String& color, bool isEmpty) {
    if (slot < 0 || slot >= 4 || !slot_buttons[slot]) return;
    
    lv_obj_t * slot_label = lv_obj_get_child(slot_buttons[slot], 0);
    
    if (isEmpty) {
        lv_label_set_text_fmt(slot_label, "Slot %d\nEmpty", slot + 1);
        lv_obj_set_style_bg_color(slot_buttons[slot], lv_color_hex(THEME_BG_SECONDARY), LV_PART_MAIN);
    } else {
        lv_label_set_text_fmt(slot_label, "Slot %d\n%s\n%s", slot + 1, material.c_str(), color.c_str());
        
        // Set button color based on material color using blue shades
        uint32_t slot_color = THEME_BG_SECONDARY;
        if (color.equalsIgnoreCase("red")) slot_color = 0x1a237e;
        else if (color.equalsIgnoreCase("green")) slot_color = 0x0277bd;
        else if (color.equalsIgnoreCase("blue")) slot_color = 0x1976d2;
        else if (color.equalsIgnoreCase("yellow")) slot_color = 0x1565c0;
        else if (color.equalsIgnoreCase("white")) slot_color = 0x42a5f5;
        else if (color.equalsIgnoreCase("black")) slot_color = 0x0d47a1;
        
        lv_obj_set_style_bg_color(slot_buttons[slot], lv_color_hex(slot_color), LV_PART_MAIN);
    }
}

void AceProUI::updateFullStatus(const AceStatus& status) {
    // Update overall status
    updateStatus("Status: " + status.status, false);
    
    // Update material slots
    for (int i = 0; i < 4; i++) {
        if (i < ACE_SLOT_COUNT) {
            updateMaterialSlot(i, status.slots[i].material, status.slots[i].color, 
                             status.slots[i].status == "empty");
        }
    }
}
