# Connection Supervision & Recovery

The ACE module implements multiple layers of supervision and automatic recovery mechanisms to maintain reliable communication with ACE hardware.

## Overview: Three Supervision Layers

1. **Serial Manager Supervision** (serial_manager.py) - Detects communication degradation and forces reconnection
2. **Exponential Backoff** (serial_manager.py) - Prevents connection spam after failures
3. **Manager Supervision** (manager.py) - Monitors overall stability and pauses prints if needed

---

## Layer 1: Serial Communication Health Supervision

**What it monitors**: Communication quality (timeouts and unsolicited messages)  
**Trigger condition**: BOTH conditions must be met:
- 15+ request timeouts in last 30 seconds **AND**
- 15+ unsolicited messages in last 30 seconds

**What happens when triggered**:
1. Logs: `"ACE[X]: Communication unhealthy (N timeouts AND M unsolicited in last 30.0s), forcing reconnection"`
2. Clears timeout and unsolicited message counters
3. Calls `disconnect()` - closes serial port, stops all timers
4. Auto-reconnect mechanism takes over (uses exponential backoff)

**Check frequency**: Every 5 seconds (only when connected)

**Purpose**: Detects when serial communication is out of sync (ID mismatches, lost frames) and forces clean reconnection before communication completely breaks down.

**Configuration**:
```ini
[ace]
ace_connection_supervision: False  # Disables this AND manager supervision
```

**Reset conditions**:
- Counters cleared on successful connection
- Counters cleared on disconnect
- Old events (>30s) automatically pruned

---

## Layer 2: Exponential Backoff (Connection Retry)

**What it monitors**: Failed connection attempts  
**Tracking window**: 180 seconds (3 minutes)

**Backoff pattern**:
- **Initial delay**: 5s (RECONNECT_BACKOFF_MIN)
- **Growth factor**: 1.5x per failure
- **Maximum delay**: 30s (RECONNECT_BACKOFF_MAX)
- **Pattern**: 5s → 7.5s → 11.25s → 16.875s → 25.3s → 30s → **5s** (cycles back)
- **Reset**: Successful connection resets backoff to 5s

**What happens on connection failure**:
1. Timestamps the failure: `_reconnect_timestamps.append(now)`
2. Prunes old timestamps (>180s)
3. Increases backoff delay (1.5x current, or cycle back to 5s if at max)
4. Logs: `"ACE[X]: Retry in Ys (N attempts in last 180s)"`
5. Schedules reconnection timer with backoff delay

**Purpose**: Prevents log spam and reduces system load when ACE hardware is unavailable.

**Manual override**: `ACE_RECONNECT DELAY=10` bypasses backoff and uses specified delay

---

## Layer 3: Manager Connection Stability Supervision

**What it monitors**: Reconnection frequency (instability detection)  
**Trigger condition**: 6+ reconnection attempts within 3 minutes

**What happens when triggered**:
- **During printing**: 
  1. Pauses print
  2. Shows dialog: `"ACE X: unstable (N reconnects in 60s)"`
  3. User can resume (after fixing) or cancel print
- **When idle**:
  1. Shows informational dialog
  2. No print interruption

**Recovery**:
- Connection must stay connected for 30+ seconds to be considered "stable"
- Dialog auto-closes when ALL ACE instances are stable
- Reconnect counter resets after 3 minutes of stability

**Purpose**: Alerts user to chronic connection issues that indicate hardware problems (bad cable, power issues, etc.)

**Configuration**: Same flag as Layer 1 (`ace_connection_supervision`)

---

## Connection Lifecycle Flow

```
┌─────────────────────────────────────────────────────────┐
│ DISCONNECTED / INITIAL STATE                            │
│ - connect_timer = None                                  │
│ - _reconnect_backoff = 5.0s                            │
└────────────────┬────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────┐
│ ATTEMPTING CONNECTION                                   │
│ - Scheduled with backoff delay                          │
│ - find_com_port() → find ACE device                    │
│ - USB topology validation                               │
│ - connect() → open serial port                          │
└────────┬────────────────────────────┬────────────────────┘
         │                            │
         │ SUCCESS                    │ FAILURE
         ▼                            ▼
┌────────────────────────┐  ┌──────────────────────────────┐
│ CONNECTED              │  │ RECONNECT SCHEDULED          │
│ - Reset backoff to 5s  │  │ - Timestamp failure          │
│ - Clear supervision    │  │ - Increase backoff (1.5x)    │
│   counters             │  │ - Log retry delay            │
│ - Start heartbeat      │  │ - Schedule connect_timer     │
│ - Start reader/writer  │  └──────────────────────────────┘
│ - Call on_connect      │           │                       
│   callback             │           └──────┐               
└────────┬───────────────┘                  │               
         │                                  │               
         ▼                                  │               
┌────────────────────────┐                  │               
│ MONITORING             │                  │               
│ Every 5s:              │                  │               
│ - Check timeouts       │                  │               
│ - Check unsolicited    │                  │               
│ - If unhealthy →       │                  │               
│   FORCE DISCONNECT     │                  │               
└────────┬───────────────┘                  │               
         │ Communication                    │               
         │ unhealthy                        │               
         ▼                                  │               
┌────────────────────────┐                  │               
│ DISCONNECT()           │◄─────────────────┘               
│ - Close serial port    │                                  
│ - Stop all timers      │                                  
│ - Clear queues         │                                  
│ - Clear supervision    │                                  
│   counters             │                                  
└────────────────────────┘                                  
         │                                                   
         └────► Back to RECONNECT SCHEDULED                 
```

---

## Configuration Summary

| Parameter | Default | Description |
|-----------|---------|-------------|
| `INSTABILITY_WINDOW` | 180s | Track reconnects in this window |
| `INSTABILITY_THRESHOLD` | 6 | Reconnects to trigger unstable state |
| `STABILITY_GRACE_PERIOD` | 30s | Must be connected this long to be "stable" |
| `COUNTER_RESET_PERIOD` | 180s | Reset counters after this stability |
| `RECONNECT_BACKOFF_MIN` | 5s | Minimum reconnection delay |
| `RECONNECT_BACKOFF_MAX` | 30s | Maximum reconnection delay |
| `RECONNECT_BACKOFF_FACTOR` | 1.5 | Backoff multiplier |
| `COMM_SUPERVISION_WINDOW` | 30s | Monitor last N seconds for health |
| `COMM_TIMEOUT_THRESHOLD` | 15 | Timeouts to trigger reconnection |
| `COMM_UNSOLICITED_THRESHOLD` | 15 | Unsolicited msgs to trigger reconnection |
| `SUPERVISION_CHECK_INTERVAL` | 5s | How often to check communication health |

---

## Example Scenarios

### Scenario 1: Communication Degrades (ID Mismatch)

```
Time  Event
----  -----
10:00 ACE connected, working normally
10:05 Serial communication gets out of sync
10:05-10:06 15+ timeouts + 15+ unsolicited messages accumulate
10:06 Supervision check detects unhealthy state
10:06 "ACE[0]: Communication unhealthy (17 timeouts AND 16 unsolicited in last 30.0s), forcing reconnection"
10:06 disconnect() called - clears counters
10:06 Reconnection scheduled (5s backoff)
10:11 Connection attempt succeeds
10:11 Backoff reset to 5s, supervision counters cleared
```

### Scenario 2: ACE Powered Off (Multiple Failures)

```
Time  Event
----  -----
10:00 ACE disconnects (power loss)
10:00 Reconnection attempt #1 fails, backoff = 5s
10:05 Reconnection attempt #2 fails, backoff = 7.5s
10:13 Reconnection attempt #3 fails, backoff = 11.25s
10:24 Reconnection attempt #4 fails, backoff = 16.875s
10:41 Reconnection attempt #5 fails, backoff = 25.3s
11:06 Reconnection attempt #6 fails, backoff = 30s (max)
11:36 Reconnection attempt #7 fails, backoff = 5s (cycled)
      ... (6 attempts within 3 minutes)
11:40 Manager supervision detects instability (6+ reconnects)
11:40 Print paused, dialog shown to user
----  User fixes power issue
11:45 Connection succeeds, backoff reset to 5s
11:45-12:15 After 30s connected, marked as "stable"
12:15 Dialog auto-closes, user can resume
```

### Scenario 3: Manual Reconnect Override

```
User: ACE_RECONNECT INSTANCE=0 DELAY=2

Time  Event
----  -----
10:00 disconnect() called
10:00 Reconnection scheduled with custom 2s delay (overrides backoff)
10:02 Connection attempt
10:02 Success - backoff reset to 5s
```

---

## Monitoring Health

Use `ACE_GET_CONNECTION_STATUS` to check current supervision state:

```
=== ACE Connection Status ===
ACE[0]: Connected (stable) (port=/dev/ttyACM0, usb=2-2.3)
  ├─ Layer 1 - Serial Health: healthy (enabled) - 3/15 timeouts, 7/15 unsolicited (last 30s)
  ├─ Layer 2 - Backoff: current=5.0s (reset on next failure), 0 failures (last 180s)
  └─ Layer 3 - Manager: stable
ACE[1]: Connected (stabilizing, 18s) (port=/dev/ttyACM1, usb=2-2.4.3)
  ├─ Layer 1 - Serial Health: healthy (enabled) - 0/15 timeouts, 0/15 unsolicited (last 30s)
  ├─ Layer 2 - Backoff: current=5.0s (reset on next failure), 2 failures (last 180s)
  └─ Layer 3 - Manager: stabilizing (18s/30s)
```

**Interpretation**:
- **ACE[0]**: All layers healthy, stable connection
- **ACE[1]**: Connected but stabilizing (needs 12 more seconds), had 2 recent failures

---

## Disabling Supervision

To disable **both** serial health supervision and manager stability monitoring:

```ini
[ace]
ace_connection_supervision: False
```

**Warning**: Disabling supervision means:
- No automatic recovery from communication degradation
- No print pause on chronic reconnection issues
- Manual intervention required if ACE communication fails
