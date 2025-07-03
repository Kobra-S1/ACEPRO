// ACE Pro HMI UI Design Document
// Modern Industrial Touchscreen Interface Design

## 1. OVERALL DESIGN PHILOSOPHY

### Visual Design Principles
- **Clean Industrial Aesthetic**: Minimalist design with clear visual hierarchy
- **High Contrast**: Light blue text on black background for optimal visibility
- **Touch-Friendly**: Large buttons (minimum 40px height) with adequate spacing
- **Consistent Theming**: Unified color palette throughout all screens
- **Status-First**: Critical information always visible

### Color Palette
```cpp
#define THEME_BG_PRIMARY     0x000000  // Pure black background
#define THEME_BG_SECONDARY   0x0d47a1  // Dark blue panels
#define THEME_BG_ACCENT      0x1976d2  // Medium blue buttons
#define THEME_TEXT_PRIMARY   0xe3f2fd  // Light blue text
#define THEME_TEXT_SECONDARY 0x90caf9  // Medium light blue
#define THEME_BORDER         0x42a5f5  // Light blue borders
#define THEME_HIGHLIGHT      0x00e5ff  // Cyan highlights
#define THEME_ERROR          0x3949ab  // Dark blue errors
#define THEME_SUCCESS        0x0288d1  // Light blue success
```

## 2. SCREEN LAYOUT SYSTEM

### Screen Structure (480x320)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ STATUS BAR (480x40) - Connection, Status, Time                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ MAIN CONTENT AREA (480x230)                                                 │
│                                                                              │
│ [Screen-specific content goes here]                                          │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│ NAVIGATION BAR (480x50) - 6 main sections                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Navigation Structure
1. **Overview** - Main dashboard with 4-slot material display
2. **Material** - Detailed material management and editing
3. **Settings** - System configuration and preferences
4. **Network** - WiFi and connectivity settings
5. **Dryer** - Filament drying control
6. **Diagnostics** - System status and troubleshooting

## 3. INDIVIDUAL SCREEN DESIGNS

### 3.1 Overview Screen (Main Dashboard)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Status: Ready        │ WiFi: Connected      │ 12:34                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                    ACE Pro Multi-Material Control                            │
│                                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────┐ │
│  │ Slot 1          │  │ Slot 2          │  │ Slot 3          │  │ Slot 4  │ │
│  │ PLA             │  │ PETG            │  │ ABS             │  │ Empty   │ │
│  │ Red             │  │ Blue            │  │ Black           │  │         │ │
│  │ 210°C           │  │ 240°C           │  │ 250°C           │  │         │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  └─────────┘ │
│                                                                              │
│  [Load]  [Unload]  [Change Tool]  [Emergency Stop]                         │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│[Overview][Material][Settings][Network][Dryer][Diagnostics]                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Material Detail Screen
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Status: Ready        │ WiFi: Connected      │ 12:34                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                        Material Slot 1 - PLA Red                           │
│                                                                              │
│  Material Type: [PLA          ▼]    Color: [Red           ▼]               │
│  Temperature:   [210°C        ▼]    Length: [850mm        ]               │
│  Status:        [Loaded        ]    Last Used: [2h ago    ]               │
│                                                                              │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐          │
│  │   Load      │ │   Unload    │ │   Purge     │ │   Clear     │          │
│  │   100mm     │ │   100mm     │ │   20mm      │ │   Slot      │          │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘          │
│                                                                              │
│  Advanced: [Feed Assist: ON] [Runout Detection: ON] [Save Changes]         │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│[Overview][Material][Settings][Network][Dryer][Diagnostics]                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Settings Screen
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Status: Ready        │ WiFi: Connected      │ 12:34                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                             System Settings                                 │
│                                                                              │
│  ┌─ Endless Spool ──────────────────────────────────────────────────────┐   │
│  │ Enable: [ON ] Auto-switch on runout: [ON ] Slot Order: [1→2→3→4→1]  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─ Display ────────────────────────────────────────────────────────────┐   │
│  │ Brightness: [████████░░] Sleep Timer: [5min] Theme: [Dark Blue]      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─ Filament Handling ──────────────────────────────────────────────────┐   │
│  │ Load Speed: [50mm/s] Unload Speed: [50mm/s] Purge Length: [20mm]     │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  [Save All Settings]  [Reset to Defaults]  [Backup Settings]               │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│[Overview][Material][Settings][Network][Dryer][Diagnostics]                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.4 Network Screen
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Status: Ready        │ WiFi: Connected      │ 12:34                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                           Network Configuration                              │
│                                                                              │
│  ┌─ WiFi Connection ────────────────────────────────────────────────────┐   │
│  │ Network: [space           ] Signal: [████████░░] IP: [10.9.9.42]     │   │
│  │ Status: [Connected        ] Uptime: [2h 34m]      [Reconnect]        │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─ Klipper Connection ─────────────────────────────────────────────────┐   │
│  │ Host: [10.9.9.155        ] Port: [7125] Status: [Connected]         │   │
│  │ API Key: [Optional       ] Ping: [12ms] [Test Connection]           │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─ Updates ────────────────────────────────────────────────────────────┐   │
│  │ Auto-Update: [OFF] Interval: [1s] Last Update: [2s ago]             │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│[Overview][Material][Settings][Network][Dryer][Diagnostics]                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.5 Dryer Control Screen
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Status: Ready        │ WiFi: Connected      │ 12:34                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                          Filament Dryer Control                             │
│                                                                              │
│  ┌─ Current Status ──────────────────────────────────────────────────────┐  │
│  │ Status: [Stopped] Temperature: [25°C] Remaining: [--:--]             │  │
│  │ Target: [--°C  ] Humidity: [45%    ] Total Time: [--:--]             │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌─ Drying Presets ──────────────────────────────────────────────────────┐  │
│  │ [PLA: 40°C/4h] [PETG: 65°C/6h] [ABS: 80°C/8h] [TPU: 50°C/12h]      │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌─ Custom Settings ─────────────────────────────────────────────────────┐  │
│  │ Temperature: [45°C ▲▼] Duration: [4h 30m ▲▼] [Start Custom]         │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  [Emergency Stop]  [Pause/Resume]  [Add 30min]  [Cool Down]               │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│[Overview][Material][Settings][Network][Dryer][Diagnostics]                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.6 Diagnostics Screen
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Status: Ready        │ WiFi: Connected      │ 12:34                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                          System Diagnostics                                 │
│                                                                              │
│  ┌─ System Health ───────────────────────────────────────────────────────┐  │
│  │ CPU: [45%] RAM: [67%] Storage: [23%] Uptime: [2d 14h 23m]           │  │
│  │ WiFi: [OK] Klipper: [OK] ACE: [OK] Touch: [OK] Display: [OK]        │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌─ Sensor Status ───────────────────────────────────────────────────────┐  │
│  │ Slot 1: [OK] Slot 2: [OK] Slot 3: [OK] Slot 4: [OK]                 │  │
│  │ Toolhead: [OK] Bowden: [OK] Extruder: [OK] Temp: [25°C]             │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌─ Test Functions ──────────────────────────────────────────────────────┐  │
│  │ [Test Sensors] [Test Motors] [Test Runout] [Calibrate] [Reset]       │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  [Export Logs]  [Clear Logs]  [Factory Reset]  [Reboot System]            │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│[Overview][Material][Settings][Network][Dryer][Diagnostics]                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 4. INTERACTION PATTERNS

### 4.1 Touch Interactions
- **Tap**: Primary action (button press, selection)
- **Long Press**: Secondary action (context menu, advanced options)
- **Swipe**: Navigate between screens (left/right)
- **Double Tap**: Quick access (emergency stop, home)

### 4.2 Visual Feedback
- **Button Press**: Color change + slight scale animation
- **Selection**: Cyan border highlight
- **Loading**: Progress bar with percentage
- **Error**: Red border flash + error dialog
- **Success**: Green border flash + success message

### 4.3 Navigation Flow
```
Overview ←→ Material ←→ Settings ←→ Network ←→ Dryer ←→ Diagnostics
    ↑           ↑           ↑          ↑        ↑          ↑
    └─────────────────────────────────────────────────────────┘
```

## 5. RESPONSIVE DESIGN CONSIDERATIONS

### 5.1 Touch Targets
- Minimum button size: 40x40px
- Recommended button size: 60x40px
- Minimum spacing between buttons: 8px
- Large buttons for critical actions: 100x50px

### 5.2 Text Sizing
- Title text: 16px (montserrat_16)
- Body text: 14px (montserrat_14)
- Small text: 12px (montserrat_12)
- Status text: 10px (montserrat_10)

### 5.3 Information Density
- Maximum 4-5 main UI elements per screen
- Group related functions in panels
- Use consistent spacing and alignment
- Prioritize most important information

## 6. IMPLEMENTATION TIPS

### 6.1 Memory Management
- Use object pooling for frequently created/destroyed elements
- Implement lazy loading for secondary screens
- Cache frequently accessed UI elements
- Use PSRAM for large buffers

### 6.2 Performance Optimization
- Limit screen updates to 30fps
- Use partial screen updates when possible
- Implement smooth animations (200-300ms duration)
- Background tasks for API calls

### 6.3 Error Handling
- Always provide user feedback for actions
- Implement timeout handling for network operations
- Show clear error messages with suggested actions
- Provide recovery options for failed operations

This design provides a comprehensive, professional-grade UI that's both functional and visually appealing for industrial use.
