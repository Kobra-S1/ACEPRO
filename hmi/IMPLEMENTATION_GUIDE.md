// Implementation step-by-step guide for expanding your ACE Pro UI

## Step 1: Update your current ui_ace_pro.cpp to support multiple screens

// Add these new static variables at the top of your ui_ace_pro.cpp:
static UIScreen currentScreen = UIScreen::MAIN_OVERVIEW;
static lv_obj_t * screens[6] = {NULL}; // Array to hold all screens
static lv_obj_t * status_bar = NULL;
static lv_obj_t * navigation_bar = NULL;
static lv_obj_t * nav_buttons[6] = {NULL};
static int selectedSlot = -1;

## Step 2: Add the enhanced screen management functions

void AceProUI::showScreen(UIScreen screen) {
    // Hide all screens first
    for (int i = 0; i < 6; i++) {
        if (screens[i]) {
            lv_obj_add_flag(screens[i], LV_OBJ_FLAG_HIDDEN);
        }
    }
    
    // Show the requested screen
    int screenIndex = static_cast<int>(screen);
    if (screens[screenIndex]) {
        lv_obj_clear_flag(screens[screenIndex], LV_OBJ_FLAG_HIDDEN);
        lv_scr_load(screens[screenIndex]);
    }
    
    currentScreen = screen;
    updateNavigationButtons();
}

void AceProUI::updateNavigationButtons() {
    // Update navigation button states
    for (int i = 0; i < 6; i++) {
        if (nav_buttons[i]) {
            if (i == static_cast<int>(currentScreen)) {
                // Highlight current screen button
                lv_obj_set_style_bg_color(nav_buttons[i], lv_color_hex(0x00e5ff), LV_PART_MAIN);
                lv_obj_set_style_border_width(nav_buttons[i], 2, LV_PART_MAIN);
            } else {
                // Normal state
                lv_obj_set_style_bg_color(nav_buttons[i], lv_color_hex(0x1976d2), LV_PART_MAIN);
                lv_obj_set_style_border_width(nav_buttons[i], 1, LV_PART_MAIN);
            }
        }
    }
}

## Step 3: Create the navigation callback function

void AceProUI::navigationCallback(lv_event_t * e) {
    int nav_index = (int)(intptr_t)lv_event_get_user_data(e);
    
    UIScreen targetScreen = static_cast<UIScreen>(nav_index);
    showScreen(targetScreen);
    
    Serial.printf("Navigation to screen: %d\n", nav_index);
}

## Step 4: Enhance your material detail screen

void AceProUI::showMaterialDetail(int slot) {
    selectedSlot = slot;
    showScreen(UIScreen::MATERIAL_DETAIL);
    
    // Update material detail screen with slot information
    // This is where you'd populate the material detail form with current slot data
    Serial.printf("Showing material detail for slot %d\n", slot + 1);
}

## Step 5: Add progress dialog functionality

void AceProUI::showProgressDialog(const String& title, const String& message) {
    if (!progress_dialog) {
        createProgressDialog();
    }
    
    // Update dialog content
    lv_obj_t* title_label = lv_obj_get_child(progress_dialog, 0);
    lv_obj_t* message_label = lv_obj_get_child(progress_dialog, 1);
    
    lv_label_set_text(title_label, title.c_str());
    lv_label_set_text(message_label, message.c_str());
    
    // Show dialog
    lv_obj_clear_flag(progress_dialog, LV_OBJ_FLAG_HIDDEN);
    isProgressShowing = true;
}

void AceProUI::hideProgressDialog() {
    if (progress_dialog) {
        lv_obj_add_flag(progress_dialog, LV_OBJ_FLAG_HIDDEN);
        isProgressShowing = false;
    }
}

## Step 6: Create the progress dialog

void AceProUI::createProgressDialog() {
    // Create modal dialog
    progress_dialog = lv_obj_create(lv_scr_act());
    lv_obj_set_size(progress_dialog, 300, 150);
    lv_obj_center(progress_dialog);
    lv_obj_set_style_bg_color(progress_dialog, lv_color_hex(0x0d47a1), LV_PART_MAIN);
    lv_obj_set_style_border_color(progress_dialog, lv_color_hex(0x42a5f5), LV_PART_MAIN);
    lv_obj_set_style_border_width(progress_dialog, 2, LV_PART_MAIN);
    lv_obj_set_style_radius(progress_dialog, 10, LV_PART_MAIN);
    lv_obj_add_flag(progress_dialog, LV_OBJ_FLAG_HIDDEN); // Initially hidden
    
    // Title
    lv_obj_t* title = lv_label_create(progress_dialog);
    lv_label_set_text(title, "Processing...");
    lv_obj_set_style_text_color(title, lv_color_hex(0xe3f2fd), LV_PART_MAIN);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 20);
    
    // Message
    lv_obj_t* message = lv_label_create(progress_dialog);
    lv_label_set_text(message, "Please wait...");
    lv_obj_set_style_text_color(message, lv_color_hex(0x90caf9), LV_PART_MAIN);
    lv_obj_set_style_text_font(message, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_align(message, LV_ALIGN_CENTER, 0, 0);
    
    // Progress bar
    progress_bar = lv_bar_create(progress_dialog);
    lv_obj_set_size(progress_bar, 250, 20);
    lv_obj_align(progress_bar, LV_ALIGN_BOTTOM_MID, 0, -20);
    lv_obj_set_style_bg_color(progress_bar, lv_color_hex(0x1a237e), LV_PART_MAIN);
    lv_obj_set_style_bg_color(progress_bar, lv_color_hex(0x42a5f5), LV_PART_INDICATOR);
    lv_bar_set_value(progress_bar, 0, LV_ANIM_OFF);
}

## Step 7: Update your action button callbacks to use the new dialogs

void AceProUI::actionButtonCallback(lv_event_t * e) {
    int action_index = (int)(intptr_t)lv_event_get_user_data(e);
    const char* actions[] = {"Load", "Unload", "Change", "Settings"};
    
    Serial.printf("Action button pressed: %s\n", actions[action_index]);
    
    switch (action_index) {
        case 0: // Load
            if (selectedSlot >= 0) {
                showProgressDialog("Loading Filament", "Loading filament into slot " + String(selectedSlot + 1));
                // Here you would call your ACE API to load filament
                // AceAPI::loadFilament(selectedSlot);
            } else {
                showErrorDialog("Please select a slot first");
            }
            break;
            
        case 1: // Unload
            if (selectedSlot >= 0) {
                showProgressDialog("Unloading Filament", "Unloading filament from slot " + String(selectedSlot + 1));
                // AceAPI::unloadFilament(selectedSlot);
            } else {
                showErrorDialog("Please select a slot first");
            }
            break;
            
        case 2: // Change Tool
            if (selectedSlot >= 0) {
                showProgressDialog("Changing Tool", "Changing to slot " + String(selectedSlot + 1));
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

## Step 8: Add the error dialog

void AceProUI::showErrorDialog(const String& error) {
    if (!error_dialog) {
        createErrorDialog();
    }
    
    // Update error message
    lv_obj_t* error_label = lv_obj_get_child(error_dialog, 1);
    lv_label_set_text(error_label, error.c_str());
    
    // Show dialog
    lv_obj_clear_flag(error_dialog, LV_OBJ_FLAG_HIDDEN);
    isErrorShowing = true;
}

void AceProUI::createErrorDialog() {
    // Create modal error dialog
    error_dialog = lv_obj_create(lv_scr_act());
    lv_obj_set_size(error_dialog, 350, 200);
    lv_obj_center(error_dialog);
    lv_obj_set_style_bg_color(error_dialog, lv_color_hex(0x3949ab), LV_PART_MAIN);
    lv_obj_set_style_border_color(error_dialog, lv_color_hex(0xff5252), LV_PART_MAIN);
    lv_obj_set_style_border_width(error_dialog, 3, LV_PART_MAIN);
    lv_obj_set_style_radius(error_dialog, 10, LV_PART_MAIN);
    lv_obj_add_flag(error_dialog, LV_OBJ_FLAG_HIDDEN);
    
    // Error title
    lv_obj_t* title = lv_label_create(error_dialog);
    lv_label_set_text(title, "Error");
    lv_obj_set_style_text_color(title, lv_color_hex(0xff5252), LV_PART_MAIN);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 20);
    
    // Error message
    lv_obj_t* message = lv_label_create(error_dialog);
    lv_label_set_text(message, "An error occurred");
    lv_obj_set_style_text_color(message, lv_color_hex(0xe3f2fd), LV_PART_MAIN);
    lv_obj_set_style_text_font(message, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_align(message, LV_ALIGN_CENTER, 0, 0);
    lv_label_set_long_mode(message, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(message, 300);
    
    // OK button
    lv_obj_t* ok_btn = lv_btn_create(error_dialog);
    lv_obj_set_size(ok_btn, 100, 40);
    lv_obj_align(ok_btn, LV_ALIGN_BOTTOM_MID, 0, -20);
    lv_obj_set_style_bg_color(ok_btn, lv_color_hex(0x1976d2), LV_PART_MAIN);
    lv_obj_add_event_cb(ok_btn, [](lv_event_t * e) {
        lv_obj_add_flag(error_dialog, LV_OBJ_FLAG_HIDDEN);
        isErrorShowing = false;
    }, LV_EVENT_CLICKED, NULL);
    
    lv_obj_t* ok_label = lv_label_create(ok_btn);
    lv_label_set_text(ok_label, "OK");
    lv_obj_set_style_text_color(ok_label, lv_color_hex(0xe3f2fd), LV_PART_MAIN);
    lv_obj_center(ok_label);
}

## Step 9: Update your slot button callback to show selection and enable detail view

void AceProUI::slotButtonCallback(lv_event_t * e) {
    int slot_index = (int)(intptr_t)lv_event_get_user_data(e);
    selectedSlot = slot_index;
    
    Serial.printf("Material slot %d selected\n", slot_index + 1);
    
    // Update visual selection
    for (int i = 0; i < 4; i++) {
        if (slot_buttons[i] != NULL) {
            if (i == slot_index) {
                lv_obj_set_style_border_color(slot_buttons[i], lv_color_hex(0x00e5ff), LV_PART_MAIN);
                lv_obj_set_style_border_width(slot_buttons[i], 3, LV_PART_MAIN);
            } else {
                lv_obj_set_style_border_color(slot_buttons[i], lv_color_hex(0x42a5f5), LV_PART_MAIN);
                lv_obj_set_style_border_width(slot_buttons[i], 2, LV_PART_MAIN);
            }
        }
    }
    
    // Long press to show detail screen
    static uint32_t last_press = 0;
    uint32_t current_time = lv_tick_get();
    if (current_time - last_press < 300) {
        // Double tap detected - show material detail
        showMaterialDetail(slot_index);
    }
    last_press = current_time;
}

## Step 10: Add this to your main.cpp setup() function to test the new UI

void setup() {
    // ... existing setup code ...
    
    // Initialize the expanded UI
    AceProUI::init();
    AceProUI::show();
    
    // Test the progress dialog
    delay(2000);
    AceProUI::showProgressDialog("System Startup", "Initializing ACE Pro connection...");
    delay(3000);
    AceProUI::hideProgressDialog();
    
    // Add material data as before
    AceProUI::updateMaterialSlot(0, "PLA", "Red", false);
    AceProUI::updateMaterialSlot(1, "PETG", "Blue", false);
    AceProUI::updateMaterialSlot(2, "ABS", "Black", false);
    AceProUI::updateMaterialSlot(3, "TPU", "Green", false);
    
    AceProUI::updateStatus("Status: 4 materials loaded, Ready");
}

## Implementation Priority

1. **Phase 1**: Add navigation bar and basic screen switching
2. **Phase 2**: Implement progress and error dialogs
3. **Phase 3**: Create material detail screen with editing capabilities
4. **Phase 4**: Add settings screen with configuration options
5. **Phase 5**: Implement network status and dryer control screens
6. **Phase 6**: Add diagnostics and system monitoring

This step-by-step approach will give you a professional, multi-screen UI that's easy to extend and maintain.
