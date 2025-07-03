#ifndef ACE_API_H
#define ACE_API_H

// Temporarily commenting out ArduinoJson dependency until library is installed
// #include <ArduinoJson.h>
#include "config.h"
// #include "network_manager.h"  // Also commenting out until implemented

// Data structures for ACE Pro status and inventory
struct MaterialSlot {
  int index;
  String status;  // "empty", "ready"
  String material;
  String color;
  int temp;
  uint32_t colorRGB;  // RGB color value for UI display
};

struct AceStatus {
  String status;  // "ready", "busy", "error"
  int currentTool;
  float temperature;
  bool endlessSpoolEnabled;
  bool runoutDetected;
  bool inProgress;
  MaterialSlot slots[ACE_SLOT_COUNT];
  String lastError;
  unsigned long lastUpdate;
};

// Temporarily simplified class definition until dependencies are resolved
/*
class AceAPI {
private:
  NetworkManager* networkManager;
  AceStatus currentStatus;
  bool isConnected;
  unsigned long lastStatusUpdate;
  unsigned long lastConnectionAttempt;
  
  // API endpoints
  String buildGcodeCommand(const String& command);
  bool sendGcodeCommand(const String& command, String& response);
  bool queryPrinterObjects(const String& objects, DynamicJsonDocument& response);
  
  // Status parsing
  void parseAceStatus(const DynamicJsonDocument& doc);
  void parseInventoryStatus(const DynamicJsonDocument& doc);
  uint32_t parseColorString(const String& colorStr);
  
public:
  AceAPI();
  void init(NetworkManager* netMgr);
  
  // Status and monitoring
  bool updateStatus();
  const AceStatus& getStatus() const { return currentStatus; }
  bool isApiConnected() const { return isConnected; }
  
  // Filament operations
  bool loadFilament(int slot, int length = 100, int speed = 50);
  bool unloadFilament(int slot, int length = 100, int speed = 50);
  bool changeTool(int slot);
  bool enableFeedAssist(int slot);
  bool disableFeedAssist(int slot);
  
  // Inventory management
  bool setSlotMaterial(int slot, const String& material, const String& color, int temp);
  bool setSlotEmpty(int slot);
  bool saveInventory();
  
  // Endless spool control
  bool enableEndlessSpool();
  bool disableEndlessSpool();
  bool getEndlessSpoolStatus();
  
  // Dryer control
  bool startDrying(int temp, int duration = 240);
  bool stopDrying();
  
  // Debug and testing
  bool testRunoutSensor();
  bool debugCommand(const String& method, const String& params = "{}");
  
  // Error handling
  String getLastError() const { return currentStatus.lastError; }
  void clearError();
};
*/

#endif // ACE_API_H
