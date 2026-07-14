# Spec: Safe recovery of a mis-typed instance protocol (late-CH340 problem)

## 1. Problem & root cause

`AceManager.__init__` resolves each `auto` instance's protocol **once**, from a USB
scan at config-load time. If the CH340 RS-485 adapter (ACE2 shared bus,
`"USB Single Serial"`, VID `1A86:55D3`) has not USB-enumerated yet, the affected
instance falls back to `ace1_json` and thereafter searches only for an
`"ACE"`-description port at its index — never the CH340. It is frozen for the
whole session: `No ACE device found`, ACE2 unreachable.

## 2. Why the naive fix failed (must not repeat)

The reverted `c89b426` re-resolved topology at `klippy:ready` and rebound
mis-typed instances. It misbound in the field because:

- The **ACE1 watchdog resets every 2–3 s when idle**, so at startup (before
  anything talks to it) the ACE1 is *frequently invisible*.
- When the rescan sees only the CH340, the topology rule *"a shared bus backs all
  remaining instances"* yields `{0:ace2, 1:ace2}` — a **complete** map that
  passes the len check — so instance 0 (the ACE1) gets rebound onto the ACE2
  bus. Both instances then contend for one CH340 (`found 1/2 expected`, forever).

**Two lessons that constrain any fix:**
1. A single USB scan cannot distinguish *"the ACE1 is briefly mid-reset"* from
   *"instance 0 is a second ACE2 on the bus."* Inference from port presence
   alone is unsafe.
2. Transient absence is the **norm**, not the exception, for an idle ACE1.

## 3. Design invariants (must always hold)

- **INV-1 — No shared-bus over-subscription.** The number of logical instances
  bound to a shared bus must never exceed the number of ACE2 units *actually
  discoverable on that bus* (`DISCOVER_DEVICE`). Discovery is the only ground
  truth; port presence is not.
- **INV-2 — Flicker immunity.** No re-typing may be driven by a condition that a
  2–3 s watchdog flicker can produce. Every trigger is gated by a sustained
  duration ≫ the watchdog window.
- **INV-3 — Never disturb active work.** No transport change while a toolchange
  is in progress or a print is active/paused.
- **INV-4 — Convergence, no thrash.** Every re-type has a cooldown; the system
  must reach a stable mapping and stop acting.
- **INV-5 — Identity preserved.** Re-typing swaps only protocol/transport/baud on
  the existing `AceInstance` (via `rebind_transport`, salvaged from the revert);
  inventory, tool mapping, sensors and monitors keep referencing the same object.

## 4. Core idea — discovery-driven transport reconciliation

Replace "rescan → rebind" with a periodic **reconciliation pass** that compares,
per shared bus, the **ground truth** (units the bus can actually discover, `K`)
against the **current binding** (logical instances assigned to it, `N`), and
reconciles in whichever direction is under/over, gated by sustained thresholds.

Two directions:

### Direction A — under-subscribed / stuck-as-ace1 (the primary fix)
An `auto` instance is stuck (disconnected, `find_connection_port` returning None)
for ≥ `RETYPE_FAILURE_GRACE_S`, **and** a CH340 (ace2) adapter is visible. Before
committing anything, **probe** the bus: create/reuse a shared session, connect,
`DISCOVER_DEVICE`, compute `unbound = discovered_UIDs − already_bound_UIDs`.
- `unbound` non-empty ⇒ a real, free ACE2 unit exists ⇒ re-type the eligible
  stuck instance to `ace2_proto`, bind it to an unbound unit, assign device-id,
  start runtime.
- `unbound` empty ⇒ the bus is full; the stuck instance is a **missing ACE1**, not
  an ACE2 ⇒ **do nothing**, keep it `ace1_json`, keep retrying its dedicated port.

This is exactly what makes it safe: in the misbind scenario (instance 0's ACE1
genuinely gone, instance 1 already the one ACE2), discovery finds 1 unit / 1 bound
⇒ `unbound` empty ⇒ instance 0 is *not* absorbed.

### Direction B — over-subscribed / stuck-on-shared-but-really-ace1 (self-heal)
For each shared bus where `N > K` persistently (≥ `OVERSUBSCRIBE_GRACE_S`), the
surplus instances were wrongly placed on the bus. Re-type the surplus
**lowest-numbered** instances (ACE2 belongs to the *highest* instances per the
daisy-chain rule) back to `ace1_json` and let them find their dedicated port by
location/index.

Direction B is the automatic self-heal for the exact `1/2 expected` state that
bit us: once the real ACE1 re-appears, the wrongly-absorbed instance is handed
back to it.

## 5. Trigger, gating, cadence

- Periodic timer `_reconcile_transports`, every `RECONCILE_INTERVAL_S` (10 s),
  registered at `klippy:ready`, unregistered at disconnect.
- Skips entirely if `not _ace_pro_enabled`, `toolchange_in_progress`, or printing
  (INV-3).
- Per-instance `_last_retype_attempt` cooldown `RETYPE_COOLDOWN_S` (60 s) (INV-4).
- Constants (initial): `RETYPE_FAILURE_GRACE_S = 30`, `OVERSUBSCRIBE_GRACE_S = 30`,
  `RECONCILE_INTERVAL_S = 10`, `RETYPE_COOLDOWN_S = 60`. All ≫ 2–3 s watchdog (INV-2).

## 6. Algorithm (pseudocode)

```python
def _reconcile_transports(eventtime):
    if not self._ace_pro_enabled or self.toolchange_in_progress or self._is_printing():
        return eventtime + RECONCILE_INTERVAL_S
    now = reactor.monotonic()
    ports = list_ports.comports()

    # Direction A: adopt an orphaned/free ACE2 unit for a genuinely stuck instance
    stuck = [i for i in self.instances
             if i.configured_protocol_name == "auto"
             and not i.serial_mgr.is_connected()
             and i.serial_mgr.sustained_port_miss_s(now) >= RETYPE_FAILURE_GRACE_S
             and now - self._last_retype_attempt.get(i.instance_num, 0) >= RETYPE_COOLDOWN_S]
    if stuck and _ace2_adapter_visible(ports):
        unbound = self._probe_shared_bus_unbound_units(ports)   # DISCOVER_DEVICE ground truth
        for inst in self._select_ace2_targets(stuck, unbound):  # highest-numbered first
            self._retype_instance_to_ace2(inst)                 # rebind + bind + assign + connect
            self._last_retype_attempt[inst.instance_num] = now

    # Direction B: hand a persistently over-subscribed (surplus) instance back to ACE1
    for bus in self._unique_bus_sessions():
        bound = self._instances_on_bus(bus)
        K = self._last_discovered_unit_count(bus)               # from bus's own discovery
        if K is not None and len(bound) > K:
            if self._oversubscribed_elapsed(bus, now) >= OVERSUBSCRIBE_GRACE_S:
                for inst in sorted(bound, key=lambda x: x.instance_num)[:len(bound) - K]:
                    if _ace1_port_candidate(ports):             # an "ACE" port exists to take
                        self._retype_instance_to_ace1(inst)     # rebind to dedicated + connect
                        self._last_retype_attempt[inst.instance_num] = now
        else:
            self._clear_oversubscribed_timer(bus)
    return eventtime + RECONCILE_INTERVAL_S
```

## 7. Components to add / change

| Component | Change |
|---|---|
| `AceSerialManager` | Add `_first_port_miss_time` (set when `find_connection_port` returns None in `auto_connect`, cleared on connect) + `sustained_port_miss_s(now)`. |
| `AceInstance.rebind_transport(...)` | **Re-add** the salvaged method from the revert (protocol/transport/baud swap + callback re-wire). It was correct; only its *caller* was wrong. |
| `AceManager._probe_shared_bus_unbound_units(ports)` | Create/reuse a shared session on the visible CH340, connect, `DISCOVER_DEVICE`, return `discovered − bound` UID set. Read-only w.r.t. bindings. |
| `AceManager._retype_instance_to_ace2 / _to_ace1` | Build protocol + transport kwargs, call `rebind_transport`, bind/assign (ace2) or connect-by-location (ace1). |
| `AceManager._reconcile_transports` | The periodic pass above; register at ready, unregister at disconnect. |
| `AceManager` bus bookkeeping | `_last_discovered_unit_count(bus)` (record `len(discovered_devices)` from `_initialize_shared_bus_transport`), `_oversubscribed_elapsed(bus, now)` timer. |
| `commands.py` | `ACE_REDETECT` — run one reconciliation pass on demand (same safe core). |

Note: `_resolve_daisy_chain_topology` is **unchanged**. No blind rescan drives
any rebind — only discovery ground-truth does.

## 8. Edge cases

- **Pure ACE2, CH340 late:** both instances stuck `ace1_json`; probe discovers 2
  units, 0 bound ⇒ both adopted. Correct.
- **ACE1 genuinely removed (not flicker):** stuck > 30 s; probe finds bus full
  (0 unbound) ⇒ instance stays `ace1_json`, keeps retrying. It reconnects if the
  ACE1 returns. No false adoption.
- **Misbind already present at startup (both on bus, 1/2):** Direction B demotes
  the lower instance back to `ace1_json` after 30 s ⇒ self-heals.
- **Explicit user trigger during flicker:** `ACE_REDETECT` uses the same
  discovery gate, so it can't over-subscribe even if the ACE1 is mid-reset.
- **Multiple ACE2 on one bus (RS-485 chain):** `K` = discovered count naturally
  handles 1–4 units; Direction A/B reconcile to it.

## 9. Test plan

**Unit (offline, mocked discovery — no hardware):**
1. A-adopt: stuck ace1 instance + probe→1 unbound ⇒ re-typed to ace2, bound, connected.
2. A-refuse: stuck instance + probe→0 unbound (bus full) ⇒ **not** re-typed (INV-1).
3. Flicker: instance failing < grace ⇒ no action (INV-2).
4. B-demote: bus with 2 bound / K=1 for ≥ grace ⇒ lowest instance re-typed to ace1.
5. B-hold: over-subscribed < grace ⇒ no action.
6. Guards: toolchange/print active ⇒ pass is a no-op (INV-3).
7. Cooldown: two passes within cooldown ⇒ at most one re-type per instance (INV-4).
8. `rebind_transport`: swaps protocol/baud, re-wires callbacks, no heartbeat on shared bus.

**Hardware validation protocol (on the printer, after unit tests green):**
- **Repro the original race:** boot Klipper with ACE2 powered off ⇒ confirm
  instance 1 stuck `ace1_json`. Power ACE2 on ⇒ within ~30–40 s Direction A
  adopts it; verify instance 0 (ACE1) untouched.
- **Flicker safety:** boot with ACE1 idle-flickering ⇒ confirm instance 0 is
  **never** re-typed to ace2 across several minutes.
- **Self-heal:** force the misbind (boot with ACE1 absent so both → shared) ⇒
  confirm Direction B returns instance 0 to `ace1_json` once the ACE1 returns.
- Re-run the reconnect stress test (already passing) to confirm no regression.

## 10. Phasing & status

- **Phase 1 (manual) — DONE, hardware-validated.** Sustained-failure tracking +
  `rebind_transport` + discovery-verified `_retype_instance_to_ace2` + the
  `ACE_REDETECT` command. Validated on the printer: mis-typed instance adopted
  when a unit is genuinely free; **refused** (INV-1) when the bus is full and an
  ACE1 is merely disconnected.
- **Phase 2 (automatic) — DONE.** Direction A runs automatically from the 2 s
  state monitor, rate-limited to `RECONCILE_INTERVAL_S` and gated by the
  `REDETECT_FAILURE_GRACE_S` sustained-miss threshold (a flickering ACE1 never
  triggers it). Direction B (`_reconcile_oversubscribed_buses`) hands a
  persistently over-subscribed instance (`bound > discovered units`, held for
  `OVERSUBSCRIBE_GRACE_S`) back to a dedicated ACE1 transport — auto-fixing the
  `1/2 expected` state an incomplete startup scan can create. Both skip during
  toolchange/print.

The discovery gate (INV-1) is the whole safety argument and was hardware-verified
in Phase 1 before Phase 2's automation was enabled.

## 11. Alternatives rejected
- **Ready-time blind rescan + rebind** (the reverted approach): watchdog flicker →
  over-subscription misbind. Rejected.
- **Longer/again startup wait:** fights the 2–3 s watchdog; can't bound arbitrary
  enumeration latency; still races. Rejected as a standalone fix.
- **Explicit rescan without a discovery gate:** same over-expansion risk if run
  during a flicker. Rejected — `ACE_REDETECT` must use the discovery gate.
```
