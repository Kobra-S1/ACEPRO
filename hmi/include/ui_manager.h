#ifndef UI_MANAGER_H
#define UI_MANAGER_H

#include <lvgl.h>
#include "config.h"
#include "ace_api.h"

enum class UIScreen {
  MAIN_SCREEN,
  INVENTORY_SCREEN,
  SETTINGS_SCREEN,
  STATUS_SCREEN,
  LOADING_SCREEN
};

class UIManager {
private:
  AceAPI* aceAPI;
  UIScreen currentScreen;
  
  // Main UI objects
  lv_obj_t* mainContainer;
  lv_obj_t* statusBar;
  lv_obj_t* contentArea;
  lv_obj_t* navigationBar;
  
  // Status bar elements
  lv_obj_t* wifiIcon;
  lv_obj_t* aceStatusLabel;
  lv_obj_t* currentToolLabel;
  lv_obj_t* timeLabel;
  
  // Navigation buttons
  lv_obj_t* navBtnMain;
  lv_obj_t* navBtnInventory;
  lv_obj_t* navBtnSettings;
  lv_obj_t* navBtnStatus;
  
  // Screen objects
  lv_obj_t* mainScreen;
  lv_obj_t* inventoryScreen;
  lv_obj_t* settingsScreen;
  lv_obj_t* statusScreen;
  
  // Material slot UI elements
  lv_obj_t* slotCards[ACE_SLOT_COUNT];
  lv_obj_t* slotButtons[ACE_SLOT_COUNT];
  lv_obj_t* slotLabels[ACE_SLOT_COUNT];
  lv_obj_t* slotColorIndicators[ACE_SLOT_COUNT];
  
  // Control buttons
  lv_obj_t* loadButtons[ACE_SLOT_COUNT];
  lv_obj_t* unloadButtons[ACE_SLOT_COUNT];
  lv_obj_t* toolChangeButtons[ACE_SLOT_COUNT];
  
  // Settings elements
  lv_obj_t* endlessSpoolSwitch;
  lv_obj_t* dryerTempSlider;
  lv_obj_t* dryerDurationSlider;
  lv_obj_t* brightnessSlider;
  
  // Timers
  lv_timer_t* uiUpdateTimer;
  unsigned long lastUIUpdate;
  
  // Theme and styling
  lv_theme_t* theme;
  lv_style_t styleCard;
  lv_style_t styleButton;
  lv_style_t styleSlotActive;
  lv_style_t styleSlotEmpty;
  
  // Private methods
  void initTheme();
  void createStatusBar();
  void createNavigationBar();
  void createMainScreen();
  void createInventoryScreen();
  void createSettingsScreen();
  void createStatusScreen();
  void createSlotCard(int slot, lv_obj_t* parent);
  void createMaterialEditDialog(int slot);
  
  // Event handlers
  static void navigationEventHandler(lv_event_t* e);
  static void slotButtonEventHandler(lv_event_t* e);
  static void loadButtonEventHandler(lv_event_t* e);
  static void unloadButtonEventHandler(lv_event_t* e);
  static void toolChangeButtonEventHandler(lv_event_t* e);
  static void settingsEventHandler(lv_event_t* e);
  static void materialEditEventHandler(lv_event_t* e);
  static void uiUpdateTimerCallback(lv_timer_t* timer);
  
  // UI update methods
  void updateStatusBar();
  void updateSlotDisplays();
  void updateMainScreen();
  void updateInventoryScreen();
  void updateSettingsScreen();
  void updateStatusScreen();
  
  // Helper methods
  void showScreen(UIScreen screen);
  void showMessage(const String& title, const String& message, bool isError = false);
  void showProgress(const String& message);
  void hideProgress();
  lv_color_t rgbToLvColor(uint32_t rgb);
  String formatTemperature(float temp);
  String formatTime(unsigned long timestamp);
  
public:
  UIManager();
  void init(AceAPI* api);
  void update();
  
  // Screen navigation
  void showMainScreen();
  void showInventoryScreen();
  void showSettingsScreen();
  void showStatusScreen();
  
  // Message display
  void showError(const String& message);
  void showSuccess(const String& message);
  void showInfo(const String& message);
  
  // Progress indication
  void showLoadingScreen(const String& message = "Loading...");
  void hideLoadingScreen();
  
  // Settings
  void setBrightness(uint8_t brightness);
  uint8_t getBrightness() const;
  
  // Status
  UIScreen getCurrentScreen() const { return currentScreen; }
  bool isUIReady() const;
};

#endif // UI_MANAGER_H
