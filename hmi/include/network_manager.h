#ifndef NETWORK_MANAGER_H
#define NETWORK_MANAGER_H

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "config.h"

enum class ConnectionState {
  DISCONNECTED,
  CONNECTING,
  CONNECTED,
  ERROR
};

class NetworkManager {
private:
  ConnectionState state;
  unsigned long lastConnectionAttempt;
  unsigned long lastHeartbeat;
  String moonrakerUrl;
  HTTPClient http;
  
  bool connectToWiFi();
  bool testMoonrakerConnection();
  
public:
  NetworkManager();
  void init();
  void handleRequests();
  
  // Connection status
  bool isConnected() const;
  bool isWiFiConnected() const;
  ConnectionState getState() const { return state; }
  String getLocalIP() const;
  int getSignalStrength() const;
  
  // HTTP requests to Moonraker
  bool sendGetRequest(const String& endpoint, String& response);
  bool sendPostRequest(const String& endpoint, const String& payload, String& response);
  
  // WebSocket support (for future real-time updates)
  void enableWebSocket();
  void disableWebSocket();
  
  // Network diagnostics
  bool pingHost();
  void reconnect();
  void disconnect();
  
  // Configuration
  void setMoonrakerHost(const String& host, int port = MOONRAKER_PORT);
  void setCredentials(const String& ssid, const String& password);
};

#endif // NETWORK_MANAGER_H
