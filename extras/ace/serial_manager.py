"""
AceSerialManager: Handles all serial communication with ACE Pro units.

Responsibilities:
- Serial port connect/disconnect
- Request/response queueing with sliding window
- CRC calculation and frame parsing
- Callback dispatch
- Port detection and enumeration
"""

import serial
import json
import threading
import queue
import logging
import traceback
import re
from types import SimpleNamespace
from serial import SerialException
import serial.tools.list_ports

from .protocol import transport_description_matches, parse_usb_location
from .protocol_ace1 import AceJsonProtocolAdapter

# Process-global registry of physical serial ports currently claimed by a
# connected AceSerialManager instance (device path -> instance_num). Used to
# stop a second logical instance from opening a port another instance is
# already actively using - e.g. when an instance's known/persisted USB
# location is momentarily invisible (mid ACE watchdog reset) and its
# index-based fallback would otherwise pick a port that's already in use.
# This is intentionally module-level (not per-instance) since all
# AceSerialManager instances share one Klipper process.
_CONNECTED_PORTS = {}
_CONNECTED_PORTS_LOCK = threading.Lock()


class AceSerialManager:
    """Manages serial communication with a single ACE Pro unit."""

    QUEUE_MAXSIZE = 1024
    WINDOW_SIZE = 4
    DEFAULT_TIMEOUT_S = 5.0

    def __init__(
            self,
            gcode,
            reactor,
            instance_num=0,
            ace_enabled=True,
            status_debug_logging=False,
            supervision_enabled=True,
            protocol=None,
            target_usb_location=None):
        """
        Initialize serial manager.

        Args:
            gcode: Klipper gcode object
            reactor: Klipper reactor for async operations
            instance_num: ACE instance number for logging
            ace_enabled: Initial ACE Pro enabled state
            status_debug_logging: Enable detailed status logging for debugging
            supervision_enabled: Enable communication health supervision
            target_usb_location: Physical USB location (e.g. "6-1.3") this
                instance is bound to, as resolved once from daisy-chain
                topology order by AceManager. When set, port lookup finds
                whichever device currently sits at this exact location -
                immune to /dev/ttyACMx renumbering across resets. When None
                (e.g. direct/unit-tested construction), legacy index-based
                matching is used, and the location is learned automatically
                after the first successful connect.
        """
        self._port = None
        self._usb_location = None
        self._target_usb_location = target_usb_location
        self._port_description = None
        self._baud = None
        self.serial_name = None
        # Monotonic time this manager first failed to find ANY port for its
        # current protocol since its last successful connect (None while
        # connected or never-yet-missed). Used by AceManager's re-detection to
        # tell a genuinely mis-typed instance (fails to find a port
        # indefinitely) from a normal ACE1 watchdog flicker (reconnects within
        # a few seconds), so protocol re-typing is only ever considered after a
        # sustained failure far longer than the ~2-3s watchdog window.
        self._first_port_miss_time = None

        self.gcode = gcode
        self.reactor = reactor
        self.instance_num = instance_num
        self.protocol = protocol or AceJsonProtocolAdapter()

        self._serial = None
        self._connected = False
        self._lock = threading.RLock()
        self._serial_lock = threading.Lock()

        self._request_id = 1
        self._callback_map = {}
        self.inflight = {}

        self._hp_queue = queue.Queue(maxsize=self.QUEUE_MAXSIZE)
        self._queue = queue.Queue(maxsize=self.QUEUE_MAXSIZE)

        self.read_buffer = bytearray()
        self.send_time = None

        self.writer_timer = None
        self.reader_timer = None
        self.heartbeat_timer = None
        self.connect_timer = None

        self._last_status_request_time = 0
        self.heartbeat_interval = 1.0
        self.heartbeat_callback = None
        self.on_connect_callback = None
        self.on_connect_callbacks = []
        self.unsolicited_response_callback = None

        self.timeout_s = self.DEFAULT_TIMEOUT_S
        self.timeout_multiplier = 2

        self.last_status = None
        self.last_action = None
        self.last_slot_states = {}
        self.last_slot_payloads = {}
        self.last_dryer_status = None
        self.last_temp = None
        self.last_feed_assist_count = None
        self.last_cont_assist_time = None
        self.last_raw_fields = None
        # Shared-bus responses are tagged with a device_id; their debug
        # change-detection state lives here per device so the interleaved
        # status streams of multiple units don't false-flip against one
        # flat last-state. Untagged (ACE1) responses keep the flat
        # attributes above.
        self._device_debug_states = {}

        self._ace_pro_enabled = ace_enabled
        self._status_debug_logging = bool(status_debug_logging)
        self._supervision_enabled = bool(supervision_enabled)
        # Human-readable connection state for KlipperScreen UI
        self.connection_state = "disabled" if not ace_enabled else "initializing"
        # Latest device info response (model/firmware/etc.)
        self.device_info = {}

        # Connection stability tracking
        # Rate-based detection: unstable if too many reconnects in short window
        self.INSTABILITY_WINDOW = 180.0      # Look at reconnects in last 3 minutes
        self.INSTABILITY_THRESHOLD = 6       # 6+ reconnects in window = unstable
        self.STABILITY_GRACE_PERIOD = 30.0   # Must stay connected 30s to be "stable"
        self.COUNTER_RESET_PERIOD = 180.0    # Reset counter after 3 min of stability

        self._reconnect_timestamps = []      # List of monotonic times of reconnect attempts
        self._last_connected_time = 0.0      # Monotonic time of last successful connect
        # Monotonic time the current outage began (first disconnect; None while connected)
        self._disconnected_since = None
        self._counter_reset_time = 0.0       # Time when counter was last reset
        self.RECONNECT_BACKOFF_MIN = 5.0     # Minimum backoff delay (location not yet known)
        self.RECONNECT_BACKOFF_MAX = 30.0    # Maximum backoff delay (30 seconds)
        self.RECONNECT_BACKOFF_FACTOR = 1.5  # Multiply backoff on each failure
        # Once we know the exact physical USB location to reconnect to, we no
        # longer need a long delay to let ambiguous enumeration settle - we
        # only need to catch the ACE's ~2-3s watchdog-reset window, so retry
        # much faster.
        self.LOCATION_KNOWN_RECONNECT_BACKOFF_MIN = 1.0
        self._reconnect_backoff = self._reconnect_backoff_floor()  # Current backoff delay (increases on failure)

        # Communication health supervision
        # Track timeouts and unsolicited messages to detect out-of-sync communication
        self.COMM_SUPERVISION_WINDOW = 30.0     # Monitor last 30 seconds
        self.COMM_TIMEOUT_THRESHOLD = 15        # 15+ timeouts in window (AND condition)
        self.COMM_UNSOLICITED_THRESHOLD = 15    # 15+ unsolicited in window (AND condition)
        self._comm_timeout_timestamps = []      # List of timeout event times
        self._comm_unsolicited_timestamps = []  # List of unsolicited message times
        self._last_supervision_check = 0.0      # Last time we checked health
        self.SUPERVISION_CHECK_INTERVAL = 5.0   # Check every 5 seconds

        # Duplicate-response detection: a reply for a request id that was
        # already answered can only come from a second device answering on
        # the same bus identity (ACE2 device_id collision) or a firmware
        # retransmit. Such replies must never reach runtime state - which of
        # the two devices they came from is unknowable.
        self.RECENT_DISPATCH_WINDOW = 30.0      # Remember answered ids this long
        self.DUPLICATE_WARN_INTERVAL = 60.0     # Rate limit for collision warning
        self._recently_dispatched_ids = {}      # rid -> monotonic dispatch time
        self._comm_duplicate_timestamps = []    # Duplicate reply times (health stats)
        self._last_duplicate_warn_time = None

    def enable_ace_pro(self):
        """Enable ACE Pro and reconnect if not connected."""
        was_disabled = not self._ace_pro_enabled
        self._ace_pro_enabled = True

        if was_disabled:
            self.connection_state = "connecting"
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: ACE Pro enabled - reconnecting"
            )
            baud = self._baud if self._baud else 115200
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Using baud rate: {baud}"
            )
            self.connect_to_ace(baud, delay=0.5)

    def disable_ace_pro(self):
        """Disable ACE Pro and disconnect immediately."""
        self._ace_pro_enabled = False
        self.connection_state = "disabled"
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: ACE Pro disabled - disconnecting"
        )
        self.disconnect()

    def is_ace_pro_enabled(self):
        """Check if ACE Pro is enabled."""
        return self._ace_pro_enabled

    def _reconnect_backoff_floor(self):
        """
        Minimum reconnect backoff delay.

        Once the physical USB location to reconnect to is known (either
        supplied by AceManager's topology resolution, or learned after a
        prior successful connect), we no longer need a long delay to let
        ambiguous multi-device enumeration settle - we only need to retry
        fast enough to catch the ACE's ~2-3s watchdog-reset window.
        """
        if self._target_usb_location is not None:
            return self.LOCATION_KNOWN_RECONNECT_BACKOFF_MIN
        return self.RECONNECT_BACKOFF_MIN

    # ========== Serial Port Detection ==========

    def find_com_port(self, device_name, instance=0, ports=None):
        """
        Find serial port for device, sorted by USB topology.

        Returns the nth matching port (instance index) sorted by physical
        USB daisy-chain position, using the single shared
        ``protocol.parse_usb_location`` implementation (depth-first, then
        lexicographic) - the same sort AceManager's topology resolution
        uses. This is only a fallback for when ``_target_usb_location``
        isn't known yet (e.g. very first boot, before any location has
        been learned); once known, ``find_connection_port`` matches by
        exact location instead of re-deriving order here.

        Args:
            device_name: Device identifier in port description
            instance: Which matching port to return (0=first, 1=second, etc)
            ports: Pre-fetched serial.tools.list_ports.comports() result to
                reuse (avoids a redundant synchronous port enumeration).
                When None, comports() is queried directly.

        Returns:
            str: Serial device path or None if not found
        """
        matches = []

        if ports is None:
            ports = serial.tools.list_ports.comports()

        # Ports already opened by a DIFFERENT logical instance must never be
        # selected here - stealing an in-use port causes interleaved frames
        # on both instances (garbled comms, identical duplicated status on
        # two ACE[n] logs). Shared-bus transports are exempt since multiple
        # instances legitimately share one physical port by design.
        shared_bus = self.protocol.get_transport_spec().shared_bus
        claimed_elsewhere = set()
        if not shared_bus:
            with _CONNECTED_PORTS_LOCK:
                claimed_elsewhere = {
                    dev for dev, owner in _CONNECTED_PORTS.items()
                    if owner != self.instance_num
                }

        for portinfo in ports:
            if not transport_description_matches(device_name, portinfo.description):
                continue

            if portinfo.device in claimed_elsewhere:
                logging.info(
                    f"ACE[{self.instance_num}] Skipping {portinfo.device} - "
                    f"already claimed by another instance"
                )
                continue

            # Extract USB location from hwid
            location = None
            m = re.search(r'LOCATION=([-\w\.]+)', portinfo.hwid)
            if m:
                location = m.group(1)
            else:
                # Fallback: extract ACM number
                m2 = re.search(r'ACM(\d+)', portinfo.device)
                if m2:
                    location = f"acm.{m2.group(1)}"
                else:
                    location = portinfo.device

            location_key = parse_usb_location(location)
            sort_key = (len(location_key), location_key)
            matches.append((sort_key, location, portinfo.device))

            logging.info(
                f"ACE[{self.instance_num}] USB device found: {portinfo.device} "
                f"at location '{location}' (sort_key={sort_key})"
            )

        # Sort by physical daisy-chain position (depth-first, then location)
        matches.sort(key=lambda x: x[0])

        logging.info(f"ACE[{self.instance_num}] USB enumeration order:")
        for idx, (sort_key, loc, dev) in enumerate(matches):
            marker = " <- SELECTED" if idx == instance else ""
            logging.info(f"  [{idx}] {dev} at {loc}{marker}")

        if len(matches) > instance:
            return matches[instance][2]
        return None

    def find_port_by_location(self, device_name, target_location, ports=None):
        """
        Find the serial port for `device_name` whose USB location exactly
        matches `target_location`.

        Unlike index-based matching, this is immune to /dev/ttyACMx
        renumbering: it doesn't matter how many other ACE-like ports are
        currently visible or which index this unit ends up at after a
        reset - only its fixed physical daisy-chain position (LOCATION=)
        matters.

        Returns:
            str: Serial device path, or None if that location isn't
                currently visible (e.g. the unit is mid-reset).
        """
        if target_location is None:
            return None

        if ports is None:
            ports = serial.tools.list_ports.comports()

        for portinfo in ports:
            if not transport_description_matches(device_name, portinfo.description):
                continue

            m = re.search(r'LOCATION=([-\w\.]+)', portinfo.hwid)
            if m:
                location = m.group(1)
            else:
                m2 = re.search(r'ACM(\d+)', portinfo.device)
                location = f"acm.{m2.group(1)}" if m2 else portinfo.device

            if location == target_location:
                return portinfo.device

        return None

    def find_connection_port(self, instance=0, ports=None):
        """Find the physical serial port for this logical ACE instance."""
        transport = self.protocol.get_transport_spec()

        if self._target_usb_location is not None:
            port = self.find_port_by_location(
                transport.port_description,
                self._target_usb_location,
                ports=ports,
            )
            if port is not None:
                if not transport.shared_bus:
                    with _CONNECTED_PORTS_LOCK:
                        owner = _CONNECTED_PORTS.get(port)
                    if owner is not None and owner != self.instance_num:
                        logging.info(
                            f"ACE[{self.instance_num}] Known location "
                            f"{self._target_usb_location} resolved to {port}, "
                            f"but it's already claimed by instance {owner} - "
                            f"treating as unavailable"
                        )
                        return None
                return port
            # The known physical location isn't visible right now (e.g. the
            # unit is mid-reset/re-enumerating) - fall back to legacy
            # index+description matching rather than refusing to connect.

        port_index = 0 if transport.shared_bus else instance
        return self.find_com_port(transport.port_description, port_index, ports=ports)

    def _get_usb_location_for_port(self, port, ports=None):
        """Get USB location string for a specific port."""
        if ports is None:
            ports = serial.tools.list_ports.comports()
        for portinfo in ports:
            if portinfo.device == port:
                m = re.search(r'LOCATION=([-\w\.]+)', portinfo.hwid)
                if m:
                    return m.group(1)
                # Fallback
                m2 = re.search(r'ACM(\d+)', portinfo.device)
                if m2:
                    return f"acm.{m2.group(1)}"
                return portinfo.device
        return None

    def _get_port_description_for_port(self, port, ports=None):
        """Get human-readable USB port description for a specific port."""
        if ports is None:
            ports = serial.tools.list_ports.comports()
        for portinfo in ports:
            if portinfo.device == port:
                return str(portinfo.description or "")
        return None

    def get_usb_location(self):
        """Get current USB location."""
        return getattr(self, '_usb_location', None)

    def get_usb_topology_position(self):
        """
        Get normalized topology position (depth in daisy chain).
        Returns the number of hops from root, ignoring which root port.

        Examples:
            "2-2.3" -> 2 (root -> hub -> port)
            "2-2.4.3" -> 3 (root -> hub -> port -> port)
            "1-3.2" -> 2 (root -> port -> port)
        """
        location = self.get_usb_location()
        if not location:
            return None

        # Count the number of dots/hyphens = depth in USB tree
        # Strip the controller number prefix (before first hyphen)
        if '-' in location:
            topo = location.split('-', 1)[1]  # e.g., "2.3" or "2.4.3"
            depth = topo.count('.') + 1  # Count ports in chain
            return depth

        return None

    # ========== Serial Connection Management ==========

    def connect_to_ace(self, baud, delay=2):
        """Start connection attempts (only if ACE enabled)."""
        if not self._ace_pro_enabled:
            self.gcode.respond_info(
                f'ACE[{self.instance_num}]: ACE Pro disabled - '
                f'not starting connection attempts'
            )
            return

        self._baud = baud

        def connect_callback(eventtime):
            if not self._ace_pro_enabled:
                self.gcode.respond_info(
                    f'ACE[{self.instance_num}]: ACE Pro disabled during connection attempt'
                )
                return self.reactor.NEVER

            if self.auto_connect(self.instance_num, self._baud):
                logging.info(f'ACE[{self.instance_num}]: Connected')
                # Reset backoff on successful connect
                self._reconnect_backoff = self._reconnect_backoff_floor()
                return self.reactor.NEVER
            else:
                # Track failed connection attempt for stability detection
                # (only track failures, not the initial attempt)
                now = self.reactor.monotonic()
                self._reconnect_timestamps.append(now)

                # Prune old timestamps outside the instability window
                cutoff = now - self.INSTABILITY_WINDOW
                self._reconnect_timestamps = [t for t in self._reconnect_timestamps if t > cutoff]

                # Increase backoff delay on failure (exponential backoff)
                current_backoff = self._reconnect_backoff
                next_backoff = self._reconnect_backoff * self.RECONNECT_BACKOFF_FACTOR
                if next_backoff >= self.RECONNECT_BACKOFF_MAX:
                    # Reset to min after hitting max (cyclic backoff)
                    self._reconnect_backoff = self._reconnect_backoff_floor()
                else:
                    self._reconnect_backoff = next_backoff
                recent_count = len(self._reconnect_timestamps)
                self.gcode.respond_info(
                    f'ACE[{self.instance_num}]: Retry in {current_backoff:.0f}s '
                    f'({recent_count} attempts in last {int(self.INSTABILITY_WINDOW)}s)'
                )
                return eventtime + current_backoff

        initial_delay = self._reconnect_backoff
        logging.info(
            f'ACE[{self.instance_num}]: Starting connection (first attempt in {initial_delay:.0f}s)'
        )
        self.connect_timer = self.reactor.register_timer(
            connect_callback,
            self.reactor.monotonic() + initial_delay
        )

    def reconnect(self, delay=None):
        """Disconnect and schedule reconnection (only if ACE enabled)."""
        if not self._ace_pro_enabled:
            self.gcode.respond_info(
                f'ACE[{self.instance_num}]: ACE Pro disabled - not reconnecting'
            )
            return

        # Record this connection loss for instability detection. Counting
        # only failed connect attempts (as the callbacks below do) misses the
        # dominant storm pattern: the device re-enumerates and every attempt
        # succeeds on the first try, so the counter would stay at 0 through
        # 90 reconnects/hour and instability detection would never trigger.
        now = self.reactor.monotonic()
        self._reconnect_timestamps.append(now)
        cutoff = now - self.INSTABILITY_WINDOW
        self._reconnect_timestamps = [t for t in self._reconnect_timestamps if t > cutoff]

        recent_count = len(self._reconnect_timestamps)
        self.gcode.respond_info(
            f'ACE[{self.instance_num}]: (Re)connecting '
            f'({recent_count} reconnects in last {int(self.INSTABILITY_WINDOW)}s)'
        )
        self.connection_state = "reconnecting"
        self.disconnect()

        # Use provided delay parameter, or default to current backoff
        initial_delay = delay if delay is not None else self._reconnect_backoff
        self.gcode.respond_info(f'ACE[{self.instance_num}]: Scheduling reconnect in {initial_delay:.0f}s')

        def _reconnect_callback(eventtime):
            if not self._ace_pro_enabled:
                self.gcode.respond_info(
                    f'ACE[{self.instance_num}]: ACE Pro disabled during reconnect attempt'
                )
                return self.reactor.NEVER

            if self.auto_connect(self.instance_num, self._baud):
                self.gcode.respond_info(f'ACE[{self.instance_num}]: Connected')
                # Reset backoff on successful connect
                self._reconnect_backoff = self._reconnect_backoff_floor()
                return self.reactor.NEVER
            else:
                # Track failed connection attempt for stability detection
                now = self.reactor.monotonic()
                self._reconnect_timestamps.append(now)

                # Prune old timestamps outside the instability window
                cutoff = now - self.INSTABILITY_WINDOW
                self._reconnect_timestamps = [t for t in self._reconnect_timestamps if t > cutoff]

                # Increase backoff delay on failure (exponential backoff)
                current_backoff = self._reconnect_backoff
                next_backoff = self._reconnect_backoff * self.RECONNECT_BACKOFF_FACTOR
                if next_backoff >= self.RECONNECT_BACKOFF_MAX:
                    # Reset to min after hitting max (cyclic backoff)
                    self._reconnect_backoff = self._reconnect_backoff_floor()
                else:
                    self._reconnect_backoff = next_backoff
                recent_count = len(self._reconnect_timestamps)
                self.gcode.respond_info(
                    f'ACE[{self.instance_num}]: Retry in {current_backoff:.0f}s '
                    f'({recent_count} attempts in last {int(self.INSTABILITY_WINDOW)}s)'
                )
                return eventtime + current_backoff

        self.connect_timer = self.reactor.register_timer(
            _reconnect_callback,
            self.reactor.monotonic() + initial_delay
        )

    def ensure_connect_timer(self):
        """Ensure a reconnect timer is scheduled if disconnected."""
        if self._ace_pro_enabled and not self.is_connected() and self.connect_timer is None:
            self.gcode.respond_info(
                f'ACE[{self.instance_num}]: No active connect timer, scheduling reconnect'
            )
            self.reconnect(self._reconnect_backoff)

    def dwell(self, delay=1.0):
        """Sleep in reactor time."""
        currTs = self.reactor.monotonic()
        self.reactor.pause(currTs + delay)

    def auto_connect(self, instance, baud):
        """Attempt to connect to ACE device."""
        transport = self.protocol.get_transport_spec()
        # Enumerate serial ports once and reuse the result for port lookup,
        # USB location, and description - comports() does synchronous
        # filesystem/sysfs I/O and this method runs on the reactor thread
        # (via connect_to_ace's connect_callback), so calling it repeatedly
        # per attempt adds avoidable reactor latency.
        ports = list(serial.tools.list_ports.comports())
        port = self.find_connection_port(instance, ports=ports)
        if port is None:
            self.gcode.respond_info(f'ACE[{instance}]: No ACE device found')
            if self._first_port_miss_time is None:
                try:
                    self._first_port_miss_time = self.reactor.monotonic()
                except Exception:
                    self._first_port_miss_time = None
            return False

        self._port = port
        self._baud = baud
        self._usb_location = self._get_usb_location_for_port(port, ports=ports)
        self._port_description = self._get_port_description_for_port(port, ports=ports)

        logging.info('Try connecting to ' + str(port))
        connected = self.connect(port, baud)
        self.serial_name = port

        if not connected:
            self.gcode.respond_info(
                f'ACE[{instance}]: auto_connect: Failed to connect to {port}, retrying in 1s'
            )
            return False

        logging.info(
            f'ACE[{instance}]: auto_connect: Connected to {port}, sending get_info request'
        )

        # Learn this unit's physical USB location if we weren't already
        # bound to one (e.g. manager didn't resolve topology yet, or this
        # manager was constructed directly/for tests). Future reconnects
        # then target this exact location - immune to /dev/ttyACMx
        # renumbering - and use the faster location-known backoff floor.
        if self._target_usb_location is None and self._usb_location is not None:
            self._target_usb_location = self._usb_location

        # Shared-bus transports defer info queries to the bus session
        if not transport.shared_bus:
            self.send_request(
                request=self.protocol.build_get_info_request(),
                callback=lambda response: self._log_info_response(response)
            )

        return True

    def _log_info_response(self, response):
        """
        Log get_info response with port and USB topology context.
        """
        port = getattr(self, "serial_name", None) or self._port or "unknown"
        topo = self._usb_location or "unknown"
        raw_info = json.dumps(response, sort_keys=True, default=str)
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: GET_INFO raw_info: {raw_info} (port={port}, usb={topo})"
        )

        result = response.get("result", {}) if isinstance(response, dict) else {}
        if not isinstance(result, dict):
            result = {}

        raw_fields = result.get("raw_fields")
        if raw_fields is not None:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: GET_INFO raw_fields: {raw_fields}"
            )

        # Normalize keys across ACE1/ACE2 payload variants.
        model = result.get("model") or "n/a"
        firmware = result.get("firmware") or result.get("version") or "n/a"
        boot_firmware = result.get("boot_firmware") or result.get("boot_version") or "n/a"
        code = response.get("code", "n/a") if isinstance(response, dict) else "n/a"
        msg = response.get("msg", "n/a") if isinstance(response, dict) else "n/a"

        self.gcode.respond_info(
            "ACE[%s]: GET_INFO summary: model=%s fw=%s boot=%s code=%s msg=%s (port=%s usb=%s)"
            % (
                self.instance_num,
                model,
                firmware,
                boot_firmware,
                code,
                msg,
                port,
                topo,
            )
        )

        try:
            self.device_info = result if isinstance(result, dict) else {}
        except Exception:
            self.device_info = {}

    def handle_info_response(self, response):
        """Handle one get_info response and store normalized device metadata."""
        self._log_info_response(response)

    def connect(self, port, baud):
        """
            port: Serial port path (e.g., "/dev/ttyACM0")
            baud: Baud rate

        Returns:

        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: GET_INFO raw_info: {response} (port={port}, usb={topo})"
        )

        raw_fields = result.get("raw_fields")
        if raw_fields is not None:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: GET_INFO raw_fields: {raw_fields}"
            )
            bool: True if successfully connected
        """
        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=baud,
                timeout=0,
                write_timeout=0.1
            )
            if self._serial.is_open:
                self._connected = True
                self.connection_state = "connected"
                # Found and opened a port -> clear any sustained-miss tracking.
                self._first_port_miss_time = None
                logging.info(f'ACE[{self.instance_num}]: Serial port {port} opened')
                # Claim this port so no other instance's fallback port
                # selection can pick it while we're using it. Release any
                # stale claim we held on a different port first (e.g. after
                # reconnecting to a location that changed device path).
                with _CONNECTED_PORTS_LOCK:
                    stale = [
                        dev for dev, owner in _CONNECTED_PORTS.items()
                        if owner == self.instance_num and dev != port
                    ]
                    for dev in stale:
                        del _CONNECTED_PORTS[dev]
                    _CONNECTED_PORTS[port] = self.instance_num
                # DON'T reset _request_id on reconnect - old responses may still arrive
                # Resetting to 0 would cause ID collisions with stale ACE responses

                # Flush buffers to discard any stale data from previous session
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()

                if self.writer_timer is None:
                    self.writer_timer = self.reactor.register_timer(self._writer, self.reactor.NOW)
                if self.reader_timer is None:
                    self.reader_timer = self.reactor.register_timer(self._reader, self.reactor.NOW)

                if self.connect_timer is not None:
                    self.reactor.unregister_timer(self.connect_timer)
                    self.connect_timer = None

                self.start_heartbeat()

                # Record connection time for stability grace period tracking
                self._last_connected_time = self.reactor.monotonic()
                self._disconnected_since = None

                # Clear supervision counters on successful connection
                self._comm_timeout_timestamps = []
                self._comm_unsolicited_timestamps = []
                self._comm_duplicate_timestamps = []
                self._recently_dispatched_ids = {}

                # Call on_connect callback if registered
                callbacks = []
                if self.on_connect_callback is not None:
                    callbacks.append(self.on_connect_callback)
                for callback in self.on_connect_callbacks:
                    if callback not in callbacks:
                        callbacks.append(callback)

                for callback in callbacks:
                    try:
                        callback()
                    except Exception as e:
                        logging.warning(
                            "ACE[%s]: on_connect callback error: %s",
                            self.instance_num,
                            e,
                        )

                return True
        except SerialException as e:
            self.gcode.respond_info(f"ACE[{self.instance_num}]: Connection failed: {e}")
            self._serial = None
        return False

    def disconnect(self):
        """Close serial connection and stop all timers."""
        self.stop_heartbeat()

        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception as e:
                logging.error(f"ACE[{self.instance_num}]: Error closing serial: {e}")

        # Release our claim on the port so other instances can use it again
        # (e.g. after a topology change or if this instance's real physical
        # unit later re-enumerates on this same device path).
        with _CONNECTED_PORTS_LOCK:
            if self._port is not None and _CONNECTED_PORTS.get(self._port) == self.instance_num:
                del _CONNECTED_PORTS[self._port]

        # Keep the FIRST disconnect time of the current outage so
        # disconnected_for measures the continuous outage duration even
        # when the reconnect loop calls disconnect() repeatedly.
        if self._connected and self._disconnected_since is None:
            try:
                self._disconnected_since = self.reactor.monotonic()
            except Exception:
                pass

        self._connected = False
        self.read_buffer = bytearray()
        self.clear_queues()

        # Clear supervision counters on disconnect
        self._comm_timeout_timestamps = []
        self._comm_unsolicited_timestamps = []
        self._comm_duplicate_timestamps = []
        self._recently_dispatched_ids = {}

        # Stop writer timer
        if self.writer_timer:
            try:
                self.reactor.unregister_timer(self.writer_timer)
            except Exception:
                pass
            self.writer_timer = None

        # Stop reader timer
        if self.reader_timer:
            try:
                self.reactor.unregister_timer(self.reader_timer)
            except Exception:
                pass
            self.reader_timer = None

        if self.connect_timer:
            try:
                self.reactor.unregister_timer(self.connect_timer)
            except Exception:
                pass
            self.connect_timer = None

        logging.info(
            f"ACE[{self.instance_num}]: Disconnected - all timers stopped"
        )

    def is_connected(self):
        """Check if serial connection is active."""
        return self._connected and self._serial and self._serial.is_open

    def sustained_port_miss_s(self, now=None):
        """Seconds this manager has continuously failed to find any port.

        Returns 0.0 while connected or when no miss has been recorded yet.
        Used to distinguish a genuinely mis-typed protocol (misses forever)
        from a transient ACE1 watchdog reset (reappears within ~2-3s).
        """
        if self._first_port_miss_time is None:
            return 0.0
        if now is None:
            try:
                now = self.reactor.monotonic()
            except Exception:
                return 0.0
        return max(0.0, now - self._first_port_miss_time)

    def _get_recent_reconnect_count(self):
        """
        Get number of reconnects within the instability window.

        Also prunes old timestamps and resets counter after stability period.
        """
        now = self.reactor.monotonic()

        # Prune old timestamps
        cutoff = now - self.INSTABILITY_WINDOW
        self._reconnect_timestamps = [t for t in self._reconnect_timestamps if t > cutoff]

        # Reset counter after extended stability period
        if (self._last_connected_time > 0 and
                (now - self._last_connected_time) > self.COUNTER_RESET_PERIOD and
                len(self._reconnect_timestamps) == 0):
            if self._counter_reset_time < self._last_connected_time:
                self._counter_reset_time = now

        return len(self._reconnect_timestamps)

    def is_connection_stable(self):
        """
        Check if connection is stable using rate-based detection.

        Stable means:
        - Currently connected
        - Connected for at least STABILITY_GRACE_PERIOD
        - Fewer than INSTABILITY_THRESHOLD reconnects in INSTABILITY_WINDOW

        Returns:
            bool: True if connected and stable
        """
        if not self.is_connected():
            return False

        now = self.reactor.monotonic()

        # Check grace period: must be connected for at least 30 seconds
        time_connected = now - self._last_connected_time
        if time_connected < self.STABILITY_GRACE_PERIOD:
            return False

        # Check reconnect rate: less than threshold in window
        recent_count = self._get_recent_reconnect_count()
        if recent_count >= self.INSTABILITY_THRESHOLD:
            return False

        return True

    def _track_comm_timeout(self):
        """Record a timeout event for communication health supervision."""
        now = self.reactor.monotonic()
        self._comm_timeout_timestamps.append(now)
        # Prune old timestamps outside window
        cutoff = now - self.COMM_SUPERVISION_WINDOW
        self._comm_timeout_timestamps = [t for t in self._comm_timeout_timestamps if t > cutoff]

    def _track_comm_unsolicited(self):
        """Record an unsolicited message event for communication health supervision."""
        now = self.reactor.monotonic()
        self._comm_unsolicited_timestamps.append(now)
        # Prune old timestamps outside window
        cutoff = now - self.COMM_SUPERVISION_WINDOW
        self._comm_unsolicited_timestamps = [t for t in self._comm_unsolicited_timestamps if t > cutoff]

    def _check_communication_health(self):
        """
        Check if communication is healthy based on recent timeouts and unsolicited messages.

        Returns:
            tuple: (is_healthy, reason) where is_healthy is bool and reason is string
        """
        now = self.reactor.monotonic()

        # Prune old events
        cutoff = now - self.COMM_SUPERVISION_WINDOW
        self._comm_timeout_timestamps = [t for t in self._comm_timeout_timestamps if t > cutoff]
        self._comm_unsolicited_timestamps = [t for t in self._comm_unsolicited_timestamps if t > cutoff]

        timeout_count = len(self._comm_timeout_timestamps)
        unsolicited_count = len(self._comm_unsolicited_timestamps)

        # Check thresholds - BOTH conditions must be met
        if timeout_count >= self.COMM_TIMEOUT_THRESHOLD and unsolicited_count >= self.COMM_UNSOLICITED_THRESHOLD:
            return False, f"{timeout_count} timeouts AND {unsolicited_count} unsolicited messages in last {self.COMM_SUPERVISION_WINDOW}s"

        return True, "healthy"

    def _supervision_check_and_recover(self):
        """
        Periodically check communication health and force reconnection if unhealthy.
        Called from writer timer.
        """
        # Skip if supervision is disabled
        if not self._supervision_enabled:
            return

        now = self.reactor.monotonic()

        # Only check at intervals to avoid too frequent checks
        if now - self._last_supervision_check < self.SUPERVISION_CHECK_INTERVAL:
            return

        self._last_supervision_check = now

        # Only supervise if connected
        if not self.is_connected():
            return

        is_healthy, reason = self._check_communication_health()

        if not is_healthy:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Communication unhealthy ({reason}), forcing reconnection"
            )
            # Clear the tracking counters before reconnecting
            self._comm_timeout_timestamps = []
            self._comm_unsolicited_timestamps = []
            # Force disconnect and let auto-reconnect handle it
            self.disconnect()

    def get_connection_status(self):
        """
        Get detailed connection status for monitoring.

        Returns:
            dict: Connection status with keys:
                - connected: bool - currently connected
                - stable: bool - stable per rate-based detection
                - recent_reconnects: int - reconnects in last 60s
                - time_connected: float - seconds since last connect
                - last_connected_time: float (monotonic)
        """
        # If we are disconnected and somehow have no reconnect timer (e.g. after
        # an exception path), make sure a timer is scheduled so we don't get
        # stuck showing a static "next retry" message.
        self.ensure_connect_timer()

        now = self.reactor.monotonic()
        recent_count = self._get_recent_reconnect_count()

        time_connected = 0.0
        if self._last_connected_time > 0:
            time_connected = now - self._last_connected_time

        # Continuous outage duration (0.0 while connected or before the
        # first-ever successful connect)
        disconnected_for = 0.0
        if not self.is_connected() and self._disconnected_since is not None:
            disconnected_for = max(0.0, now - self._disconnected_since)

        # Get supervision health statistics
        now = self.reactor.monotonic()
        cutoff = now - self.COMM_SUPERVISION_WINDOW
        self._comm_timeout_timestamps = [t for t in self._comm_timeout_timestamps if t > cutoff]
        self._comm_unsolicited_timestamps = [t for t in self._comm_unsolicited_timestamps if t > cutoff]

        timeout_count = len(self._comm_timeout_timestamps)
        unsolicited_count = len(self._comm_unsolicited_timestamps)
        self._comm_duplicate_timestamps = [t for t in self._comm_duplicate_timestamps if t > cutoff]
        duplicate_count = len(self._comm_duplicate_timestamps)
        time_since_check = now - self._last_supervision_check

        return {
            "connected": self.is_connected(),
            "stable": self.is_connection_stable(),
            "recent_reconnects": recent_count,
            "time_connected": time_connected,
            "disconnected_for": disconnected_for,
            "last_connected_time": self._last_connected_time,
            "next_retry": self._reconnect_backoff if not self.is_connected() else 0.0,
            "port": self._port or "unknown",
            "usb_topology": self._usb_location or "unknown",
            "supervision": {
                "timeout_count": timeout_count,
                "timeout_threshold": self.COMM_TIMEOUT_THRESHOLD,
                "unsolicited_count": unsolicited_count,
                "unsolicited_threshold": self.COMM_UNSOLICITED_THRESHOLD,
                "duplicate_count": duplicate_count,
                "window_seconds": self.COMM_SUPERVISION_WINDOW,
                "check_interval": self.SUPERVISION_CHECK_INTERVAL,
                "time_since_check": time_since_check,
            }
        }

    # ========== CRC Calculation ==========

    def _calc_crc(self, buffer):
        """Calculate CRC-16 for payload."""
        _crc = 0xffff
        for byte in buffer:
            data = byte
            data ^= _crc & 0xff
            data ^= (data & 0x0f) << 4
            _crc = ((data << 8) | (_crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return _crc

    # ========== Request/Response Queuing ==========

    def send_request(self, request, callback):
        """
        Queue a normal-priority request.

        Args:
            request: Dict with JSON-serializable request
            callback: Callable(response=dict) or Callable(response=None) on timeout
        """
        if not self._ace_pro_enabled:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Dropping request — ACE Pro is disabled"
            )
            return
        try:
            normalized_request = self.protocol.normalize_request(request)
            self._queue.put([normalized_request, callback], timeout=1)
        except queue.Full:
            self.gcode.respond_info(f"ACE[{self.instance_num}]: Request queue full!")

    def send_high_prio_request(self, request, callback):
        """
        Queue a high-priority request (processed before normal queue).

        Args:
            request: Dict with JSON-serializable request
            callback: Callable as in send_request
        """
        if not self._ace_pro_enabled:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Dropping high-priority request — ACE Pro is disabled"
            )
            return
        try:
            normalized_request = self.protocol.normalize_request(request)
            self._hp_queue.put([normalized_request, callback], timeout=1)
        except queue.Full:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: High-priority queue full!"
            )

    def clear_queues(self):
        """Clear all pending requests."""
        self._clear_queue(self._queue)
        self._clear_queue(self._hp_queue)
        with self._lock:
            self._callback_map.clear()
            self.inflight.clear()

    def _clear_queue(self, q):
        """Remove all items from queue."""
        if q is None:
            return
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass

    # ========== Low-Level Frame Sending ==========

    def _send_frame(self, request):
        """Send a serialized request frame."""
        if not self.is_connected():
            self.gcode.respond_info(f"ACE[{self.instance_num}]: Serial not connected, skipping send")
            return

        with self._lock:
            if 'id' not in request:
                request['id'] = self._request_id
                next_id = self._request_id + 1
                self._request_id = next_id if next_id <= 0xFFFF else 1

        data = self.protocol.serialize_request_frame(request, self._calc_crc)

        try:
            with self._serial_lock:
                self._serial.write(data)
        except serial.SerialTimeoutException as e:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Serial write timeout: {e} (clearing inflight)"
            )
            with self._lock:
                rid = request.get('id')
                if rid in self.inflight:
                    self.inflight.pop(rid, None)
                    cb = self._callback_map.pop(rid, None)
                    if cb:
                        try:
                            cb(response=None)
                        except Exception as cb_e:
                            self.gcode.respond_info(
                                f"ACE[{self.instance_num}]: Timeout callback error: {cb_e}"
                            )
        except Exception as e:
            self.gcode.respond_info(f"ACE[{self.instance_num}]: Serial write error: {e}")
            with self._lock:
                rid = request.get('id')
                if rid in self.inflight:
                    self.inflight.pop(rid, None)
                    cb = self._callback_map.pop(rid, None)
                    if cb:
                        try:
                            cb(response=None)
                        except Exception as cb_e:
                            self.gcode.respond_info(
                                f"ACE[{self.instance_num}]: Error callback error: {cb_e}"
                            )

    # ========== Frame Reading and Parsing ==========

    # ========== Processing Loop Integration ==========

    def has_pending_requests(self):
        """Check if any requests are queued or in-flight."""
        with self._lock:
            return len(self.inflight) > 0 or not self._queue.empty() or not self._hp_queue.empty()

    def get_pending_request(self):
        """
        Get next request to send (respecting priority).

        Returns:
            tuple: (request_dict, callback) or (None, None) if no requests
        """
        if not self._hp_queue.empty():
            try:
                return self._hp_queue.get_nowait()
            except queue.Empty:
                pass

        if not self._queue.empty():
            try:
                return self._queue.get_nowait()
            except queue.Empty:
                pass

        return None, None

    def dispatch_response(self, response):
        """
        Dispatch response to callback if present, else treat as unsolicited.

        Args:
            response: Response dict

        Returns:
            tuple: (callback, was_solicited) or (None, False) if unsolicited
        """
        rid = response.get('id')
        cb = None

        with self._lock:
            if rid is not None:
                cb = self._callback_map.pop(rid, None)
                if cb:
                    self.inflight.pop(rid, None)

        if cb is not None:
            now = self.reactor.monotonic()
            cutoff = now - self.RECENT_DISPATCH_WINDOW
            self._recently_dispatched_ids = {
                r: t for r, t in self._recently_dispatched_ids.items() if t > cutoff
            }
            self._recently_dispatched_ids[rid] = now

        return cb, cb is not None

    def _handle_duplicate_response(self, response):
        """Detect and absorb a second reply to an already-answered request.

        Returns True if the response duplicates a recently dispatched request
        id. On an ACE2 shared bus this is the signature of two units holding
        the same device_id (bus identity collision): both answer every
        targeted request, the first reply consumes the callback and the
        second lands here. The duplicate is dropped - its payload may come
        from either unit, so routing it into runtime state would corrupt
        status/inventory with the ghost unit's data.
        """
        rid = response.get('id')
        if rid is None:
            return False

        # DISCOVER_DEVICE is a broadcast: every unit on the bus answers the
        # SAME request id, so a second reply is expected discovery data (the
        # race loser), not an identity collision. It must flow on to the
        # unsolicited demultiplexer, which records it as a present unit -
        # dropping it here would blind discovery to all but the race winner.
        if response.get('command') == 'DISCOVER_DEVICE':
            return False

        now = self.reactor.monotonic()
        cutoff = now - self.RECENT_DISPATCH_WINDOW
        self._recently_dispatched_ids = {
            r: t for r, t in self._recently_dispatched_ids.items() if t > cutoff
        }
        if rid not in self._recently_dispatched_ids:
            return False

        self._comm_duplicate_timestamps.append(now)
        comm_cutoff = now - self.COMM_SUPERVISION_WINDOW
        self._comm_duplicate_timestamps = [
            t for t in self._comm_duplicate_timestamps if t > comm_cutoff
        ]
        # A duplicate is also an out-of-sync signal for Layer-1 supervision
        self._track_comm_unsolicited()

        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: DUPLICATE response "
            f"(ID={rid}, command={response.get('command', 'n/a')}): "
            f"request was already answered - dropping"
        )
        if (self._last_duplicate_warn_time is None
                or now - self._last_duplicate_warn_time >= self.DUPLICATE_WARN_INTERVAL):
            self._last_duplicate_warn_time = now
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: WARNING: duplicate responses mean "
                f"two ACE units are answering on the same bus device_id "
                f"(identity collision) - check that 'ace_count' matches the "
                f"number of connected ACE units"
            )
        return True

    def set_heartbeat_callback(self, callback):
        """
        Set the callback for heartbeat responses.

        Args:
            callback: Function(response) to handle status updates
        """
        self.heartbeat_callback = callback

    def set_on_connect_callback(self, callback):
        """
        Set the callback for successful ACE connection/reconnection.

        Args:
            callback: Function() called after ACE connects
        """
        self.on_connect_callback = callback
        if callback and callback not in self.on_connect_callbacks:
            self.on_connect_callbacks.append(callback)

    def set_unsolicited_response_callback(self, callback):
        """Set callback for handling unsolicited responses."""
        self.unsolicited_response_callback = callback

    def start_heartbeat(self):
        """
        Start the heartbeat timer to send periodic status requests.

        First request sent immediately, then repeated at heartbeat_interval.
        """
        if self.protocol.get_transport_spec().shared_bus:
            # Not a missing capability: responses ARE demultiplexed here, by
            # request id in dispatch_response().  A shared bus just has no
            # single unit to poll, so each logical instance runs its own
            # addressed heartbeat via AceInstance.start_shared_bus_heartbeat().
            logging.info(
                "ACE[%s]: Transport-level heartbeat not used on a shared bus - "
                "each instance heartbeats its own device id",
                self.instance_num,
            )
            return
        if self.heartbeat_timer is None:
            # Send first status request immediately
            self._send_heartbeat_request()
            # Register timer for periodic requests
            self.heartbeat_timer = self.reactor.register_timer(
                self._heartbeat_tick,
                self.reactor.NOW
            )
            logging.info(
                f"ACE[{self.instance_num}]: Heartbeat started "
                f"(interval={self.heartbeat_interval}s)"
            )

    def stop_heartbeat(self):
        """Stop the heartbeat timer."""
        if self.heartbeat_timer is not None:
            try:
                self.reactor.unregister_timer(self.heartbeat_timer)
            except Exception as e:
                logging.warning(
                    f"ACE[{self.instance_num}]: Error stopping heartbeat: {e}"
                )
            self.heartbeat_timer = None
            logging.info(
                f"ACE[{self.instance_num}]: Heartbeat stopped"
            )

    def _heartbeat_tick(self, eventtime):
        """Timer callback for periodic heartbeat requests."""
        try:
            now = self.reactor.monotonic()
            self._send_heartbeat_request()
            self._last_status_request_time = now

            return eventtime + self.heartbeat_interval
        except Exception as e:
            logging.warning(
                f"ACE[{self.instance_num}]: Heartbeat tick error: {e}"
            )
            return eventtime + self.heartbeat_interval

    def _send_heartbeat_request(self):
        """Send a status request to the ACE device via the queue."""
        request = self.protocol.build_get_status_request()

        def _heartbeat_response(response):
            if self.heartbeat_callback:
                try:
                    self.heartbeat_callback(response)
                except Exception as e:
                    logging.warning(
                        f"ACE[{self.instance_num}]: Heartbeat callback error: {e}"
                    )

        self.send_high_prio_request(request, _heartbeat_response)

    def _writer(self, eventtime):
        """Timer callback: send requests from queue, handle timeouts, fill window."""
        try:
            now = self.reactor.monotonic()

            with self._lock:
                for rid, t0 in list(self.inflight.items()):
                    elapsed = now - t0
                    if elapsed > self.timeout_s:
                        self.gcode.respond_info(
                            f"ACE[{self.instance_num}]: Request ID={rid} TIMEOUT after {elapsed:.1f}s"
                        )
                        # Track timeout for communication health supervision
                        self._track_comm_timeout()
                        cb = self._callback_map.pop(rid, None)
                        if cb:
                            try:
                                cb(response=None)
                            except Exception as e:
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Callback error: {e}"
                                )
                        self.inflight.pop(rid, None)

            # Fill window with new requests
            while True:
                with self._lock:
                    if len(self.inflight) >= self.WINDOW_SIZE:
                        break

                req, cb = self.get_pending_request()
                if req is None:
                    # No pending requests - writer loop idle
                    # Heartbeat timer handles periodic status updates
                    break

                with self._lock:
                    rid = self._request_id
                    next_id = self._request_id + 1
                    self._request_id = next_id if next_id <= 0xFFFF else 1
                    req['id'] = rid
                    self._callback_map[rid] = cb
                    self.inflight[rid] = now

                self._send_frame(req)
        except Exception as e:
            logging.info(f'ACE[{self.instance_num}]: Write error {str(e)}')
            self.gcode.respond_info(str(e))

        # Check communication health and force reconnection if needed
        try:
            self._supervision_check_and_recover()
        except Exception as e:
            logging.warning(f"ACE[{self.instance_num}]: Supervision check error: {e}")

        return eventtime + 0.1

    def _reader(self, eventtime):
        """Timer callback: read frames from serial, dispatch responses."""
        try:
            raw = self._serial.read(size=4096)
        except SerialException:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Unable to communicate with ACE\n" +
                traceback.format_exc()
            )

            if not self._ace_pro_enabled:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: ACE Pro disabled - not scheduling reconnect"
                )
                return self.reactor.NEVER  # Stop this timer too

            # Try to reconnect
            if self.connect_timer is None:
                self.gcode.respond_info(f"ACE[{self.instance_num}]: Scheduling reconnect")
                self.reconnect()
                return self.reactor.NOW + 1.5
            else:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Scheduling reconnect (already scheduled)"
                )
            return self.reactor.NEVER

        if raw:
            self.read_buffer += raw
        else:
            return eventtime + 0.05

        responses, remaining_buffer, notices = self.protocol.extract_responses(
            self.read_buffer,
            self._calc_crc,
        )
        self.read_buffer = remaining_buffer

        for notice in notices:
            self.gcode.respond_info(f"ACE[{self.instance_num}]: {notice}")

        for ret in responses:
            if self._status_debug_logging:
                self._status_update_callback(ret)

            cb, _ = self.dispatch_response(ret)
            if cb:
                try:
                    cb(response=ret)
                except Exception as e:
                    self.gcode.respond_info(f"ACE[{self.instance_num}]: Callback error: {e}")
            else:
                # A second reply to an already-answered request must never be
                # routed anywhere (device_id collision - sender unknowable)
                if self._handle_duplicate_response(ret):
                    continue
                # Try unsolicited callback first
                if self.unsolicited_response_callback and self.unsolicited_response_callback(ret):
                    continue
                # Log unsolicited messages (no matching callback found)
                response_id = ret.get('id', 'no-id')
                response_str = json.dumps(ret)
                self.gcode.respond_info(f"ACE[{self.instance_num}]: UNSOLICITED (ID={response_id}, current_id={self._request_id}): {response_str}")
                # Track unsolicited message for communication health supervision
                self._track_comm_unsolicited()

        return eventtime + 0.05

    def _get_status_debug_state(self, device_id):
        """Return the change-detection state holder for one status stream.

        Shared-bus responses are tagged with a device_id: each device gets
        its own state so the interleaved streams of multiple units don't
        false-flip against one flat last-state (two healthy units with
        different slot occupancy would otherwise log a slot change on every
        heartbeat). Untagged (ACE1) responses keep using the flat attributes
        on self.
        """
        if device_id is None:
            return self
        state = self._device_debug_states.get(device_id)
        if state is None:
            state = SimpleNamespace(
                last_status=None,
                last_action=None,
                last_slot_states={},
                last_slot_payloads={},
                last_dryer_status=None,
                last_temp=None,
                last_feed_assist_count=None,
                last_cont_assist_time=None,
                last_raw_fields=None,
            )
            self._device_debug_states[device_id] = state
        return state

    def _status_update_callback(self, response):
        """
        Handle status updates with detailed change detection.

        Tracks changes in:
        - Overall status (busy/ready)
        - Action (feeding/retracting/etc)
        - Individual slot status
        - Dryer status
        - Temperature changes

        Change detection is tracked per shared-bus device_id (see
        _get_status_debug_state).
        """
        if not response or "result" not in response:
            return

        result = response.get("result")
        if not result:
            return

        device_id = response.get("device_id")
        state = self._get_status_debug_state(device_id)
        label = f"ACE[{self.instance_num}]"
        if device_id is not None:
            label = f"ACE[{self.instance_num}][dev {device_id}]"

        # Extract current state
        current_status = result.get("status")
        current_action = result.get("action", "none")
        current_temp = result.get("temp", 0)
        dryer_status = result.get("dryer_status", {})
        feed_assist_count = result.get("feed_assist_count")
        cont_assist_time = result.get("cont_assist_time")
        raw_fields = result.get("raw_fields")
        slots = result.get("slots", [])

        if current_status is None:
            return

        # Change-gated: at heartbeat rate an unconditional dump floods the
        # gcode response pipe (field-observed BlockingIOError in _respond_raw
        # with two ACE2 units at 1 Hz each)
        if raw_fields is not None and raw_fields != state.last_raw_fields:
            self.gcode.respond_info(
                f"{label}: GET_STATUS raw_fields: {raw_fields}"
            )
            state.last_raw_fields = raw_fields

        # Detect overall status/action change
        status_changed = (current_status != state.last_status or
                          current_action != state.last_action)

        if status_changed:
            last_display = f"{state.last_status}/{state.last_action}" if state.last_status else 'unknown'
            self.gcode.respond_info(
                f"{label}: STATUS CHANGE: "
                f"'{last_display}' -> '{current_status}/{current_action}'"
            )
            state.last_status = current_status
            state.last_action = current_action

        # Detect feed assist counters
        if feed_assist_count is not None and feed_assist_count != state.last_feed_assist_count:
            self.gcode.respond_info(
                f"{label}: FEED ASSIST COUNT: "
                f"'{state.last_feed_assist_count}' -> '{feed_assist_count}'"
            )
            state.last_feed_assist_count = feed_assist_count

        if cont_assist_time is not None and cont_assist_time != state.last_cont_assist_time:
            self.gcode.respond_info(
                f"{label}: CONT ASSIST TIME: "
                f"'{state.last_cont_assist_time}' -> '{cont_assist_time}'"
            )
            state.last_cont_assist_time = cont_assist_time

        # Detect slot status changes
        for slot in slots:
            slot_idx = slot.get("index")
            slot_status = slot.get("status", "unknown")

            if slot_idx is not None:
                last_slot_status = state.last_slot_states.get(slot_idx)

                if slot_status != last_slot_status:
                    last_display = last_slot_status if last_slot_status else 'unknown'
                    self.gcode.respond_info(
                        f"{label}: SLOT[{slot_idx}] CHANGE: "
                        f"'{last_display}' -> '{slot_status}'"
                    )
                    state.last_slot_states[slot_idx] = slot_status

            # Detect any slot field change and dump full slot payload
            if slot_idx is not None:
                last_payload = state.last_slot_payloads.get(slot_idx)
                if last_payload != slot:
                    slot_dump = json.dumps(slot, sort_keys=True)
                    self.gcode.respond_info(
                        f"{label}: SLOT[{slot_idx}] DATA: {slot_dump}"
                    )
                    state.last_slot_payloads[slot_idx] = slot

        # Detect dryer status changes
        dryer_state = dryer_status.get("status", "stop")
        if dryer_state != state.last_dryer_status:
            if dryer_state != "stop":
                target_temp = dryer_status.get("target_temp", 0)
                remain_time = dryer_status.get("remain_time", 0)
                self.gcode.respond_info(
                    f"{label}: DRYER: "
                    f"'{state.last_dryer_status or 'stop'}' -> '{dryer_state}' "
                    f"(target={target_temp}°C, remaining={remain_time}s)"
                )
            else:
                self.gcode.respond_info(
                    f"{label}: DRYER: stopped"
                )
            state.last_dryer_status = dryer_state

        # Detect significant temperature changes (>5°C)
        if state.last_temp is not None:
            temp_delta = abs(current_temp - state.last_temp)
            if temp_delta >= 5:
                self.gcode.respond_info(
                    f"{label}: TEMP CHANGE: "
                    f"{state.last_temp}°C -> {current_temp}°C "
                    f"(Δ{temp_delta:+.1f}°C)"
                )
        state.last_temp = current_temp
