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
import struct
import threading
import queue
import logging
import traceback
import re
from serial import SerialException
import serial.tools.list_ports


class AceSerialManager:
    """Manages serial communication with a single ACE Pro unit."""

    QUEUE_MAXSIZE = 1024
    WINDOW_SIZE = 4
    DEFAULT_TIMEOUT_S = 2.0

    def __init__(self, gcode, reactor, instance_num=0, ace_enabled=True, status_debug_logging=False):
        """
        Initialize serial manager.

        Args:
            gcode: Klipper gcode object
            reactor: Klipper reactor for async operations
            instance_num: ACE instance number for logging
            ace_enabled: Initial ACE Pro enabled state
            status_debug_logging: Enable detailed status logging for debugging
        """
        self._port = None
        self._baud = None

        self.gcode = gcode
        self.reactor = reactor
        self.instance_num = instance_num

        self._serial = None
        self._connected = False
        self._lock = threading.RLock()
        self._serial_lock = threading.Lock()

        self._request_id = 0
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

        self._ace_pro_enabled = ace_enabled
        self._status_debug_logging = bool(status_debug_logging)

    def enable_ace_pro(self):
        """Enable ACE Pro and reconnect if not connected."""
        was_disabled = not self._ace_pro_enabled
        self._ace_pro_enabled = True

        if was_disabled:
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
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: ACE Pro disabled - disconnecting"
        )
        self.disconnect()

    def is_ace_pro_enabled(self):
        """Check if ACE Pro is enabled."""
        return self._ace_pro_enabled

    # ========== Serial Port Detection ==========

    def _parse_usb_location(self, location_str):
        """
        Parse USB location string into tuple for natural sorting.

        Examples:
            "1-1.4.3:1.0" → (1, 1, 4, 3)
            "acm.2" → (999998, 2)
        
        ACM fallback locations sort after USB locations but before
        unrecognized devices (999999).
        """
        if not location_str:
            return (999999,)

        location_str = str(location_str)
        
        # Handle ACM fallback format (e.g., "acm.2")
        if location_str.startswith('acm.'):
            try:
                acm_num = int(location_str[4:])
                return (999998, acm_num)  # Sort after USB, before unknown
            except ValueError:
                return (999999,)
        
        # Strip interface suffix (e.g., ":1.0")
        location_str = location_str.split(':')[0]
        parts = location_str.replace('-', '.').split('.')

        try:
            return tuple(int(p) for p in parts)
        except ValueError:
            return (999999,)

    def find_com_port(self, device_name, instance=0):
        """
        Find serial port for device, sorted by USB topology.

        Returns the nth matching port (instance index) sorted by USB location,
        ensuring consistent ordering across hot-plugs.

        Args:
            device_name: Device identifier in port description
            instance: Which matching port to return (0=first, 1=second, etc)

        Returns:
            str: Serial device path or None if not found
        """
        matches = []

        for portinfo in serial.tools.list_ports.comports():
            if device_name not in portinfo.description:
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

            sort_key = self._parse_usb_location(location)
            matches.append((sort_key, location, portinfo.device))

            self.gcode.respond_info(
                f"ACE[{self.instance_num}] USB device found: {portinfo.device} "
                f"at location '{location}' (sort_key={sort_key})"
            )

        # Sort by location
        matches.sort(key=lambda x: x[0])

        if matches:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}] USB enumeration order:"
            )
            for idx, (sort_key, loc, dev) in enumerate(matches):
                marker = " <- SELECTED" if idx == instance else ""
                self.gcode.respond_info(
                    f"  [{idx}] {dev} at {loc}{marker}"
                )

        if len(matches) > instance:
            return matches[instance][2]
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
                self.gcode.respond_info(f'ACE[{self.instance_num}]: Ace state: Connected')
                return self.reactor.NEVER
            else:
                return eventtime + delay

        self.connect_timer = self.reactor.register_timer(
            connect_callback,
            self.reactor.NOW + delay
        )

    def reconnect(self, delay=5):
        """Disconnect and schedule reconnection (only if ACE enabled)."""
        if not self._ace_pro_enabled:
            self.gcode.respond_info(
                f'ACE[{self.instance_num}]: ACE Pro disabled - not reconnecting'
            )
            return

        self.gcode.respond_info(f'ACE[{self.instance_num}]: (Re)connecting')
        self.disconnect()

        def _reconnect_callback(eventtime):
            if not self._ace_pro_enabled:
                self.gcode.respond_info(
                    f'ACE[{self.instance_num}]: ACE Pro disabled during reconnect attempt'
                )
                return self.reactor.NEVER

            self.gcode.respond_info(f'ACE[{self.instance_num}]: _reconnect_callback')
            if self.auto_connect(self.instance_num, self._baud):
                self.gcode.respond_info(f'ACE[{self.instance_num}]: _reconnect_callback: Connected')
                return self.reactor.NEVER
            else:
                return eventtime + delay

        self.connect_timer = self.reactor.register_timer(
            _reconnect_callback,
            self.reactor.NOW + delay
        )

    def dwell(self, delay=1.0):
        """Sleep in reactor time."""
        currTs = self.reactor.monotonic()
        self.reactor.pause(currTs + delay)

    def auto_connect(self, instance, baud):
        """Attempt to connect to ACE device."""
        port = self.find_com_port('ACE', instance)
        if port is None:
            self.gcode.respond_info('No ACE device found')
            return False

        self._port = port
        self._baud = baud

        self.gcode.respond_info('Try connecting to ' + str(port))
        connected = self.connect(port, baud)
        self.serial_name = port

        if not connected:
            self.gcode.respond_info(
                f'ACE[{instance}]: auto_connect: Failed to connect to {port}, retrying in 1s'
            )
            return False

        self.gcode.respond_info(
            f'ACE[{instance}]: auto_connect: Connected to {port}, sending get_info request'
        )
        self.send_request(
            request={"method": "get_info"},
            callback=lambda response: self.gcode.respond_info(f"ACE[{instance}]: {response}")
        )

        return True

    def connect(self, port, baud):
        """
        Connect to serial device.

        Args:
            port: Serial port path (e.g., "/dev/ttyACM0")
            baud: Baud rate

        Returns:
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
                logging.info(f'ACE[{self.instance_num}]: Serial port {port} opened')
                self._request_id = 0

                if self.writer_timer is None:
                    self.writer_timer = self.reactor.register_timer(self._writer, self.reactor.NOW)
                if self.reader_timer is None:
                    self.reader_timer = self.reactor.register_timer(self._reader, self.reactor.NOW)

                if self.connect_timer is not None:
                    self.reactor.unregister_timer(self.connect_timer)
                    self.connect_timer = None

                self.start_heartbeat()

                # Call on_connect callback if registered
                if self.on_connect_callback:
                    try:
                        self.on_connect_callback()
                    except Exception as e:
                        logging.warning(
                            f"ACE[{self.instance_num}]: on_connect callback error: {e}"
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

        self._connected = False
        self.read_buffer = bytearray()
        self.clear_queues()

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

        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Disconnected - all timers stopped"
        )

    def is_connected(self):
        """Check if serial connection is active."""
        return self._connected and self._serial and self._serial.is_open

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
        try:
            self._queue.put([request, callback], timeout=1)
        except queue.Full:
            self.gcode.respond_info(f"ACE[{self.instance_num}]: Request queue full!")

    def send_high_prio_request(self, request, callback):
        """
        Queue a high-priority request (processed before normal queue).

        Args:
            request: Dict with JSON-serializable request
            callback: Callable as in send_request
        """
        try:
            self._hp_queue.put([request, callback], timeout=1)
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
                self._request_id += 1

        payload = json.dumps(request).encode('utf-8')
        data = bytearray([0xFF, 0xAA])
        data += struct.pack('<H', len(payload))
        data += payload
        data += struct.pack('<H', self._calc_crc(payload))
        data += b'\xFE'

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

    def read_frames(self, eventtime):
        """
        Read and parse frames from serial.

        Returns:
            list: Parsed frame dicts, or empty list if no complete frames
        """
        frames = []

        try:
            raw = self._serial.read(size=4096)
        except SerialException:
            self.gcode.respond_info(
                f"[{self.instance_num}] Unable to read from serial\n" +
                traceback.format_exc()
            )
            return frames

        if raw:
            self.read_buffer += raw

        # Parse complete frames from buffer
        while True:
            buf = self.read_buffer
            if len(buf) < 7:
                break

            # Check for frame header
            if not (buf[0] == 0xFF and buf[1] == 0xAA):
                hdr = buf.find(bytes([0xFF, 0xAA]))
                if hdr == -1:
                    self.gcode.respond_info(
                        f"[{self.instance_num}] Resync: dropped {len(buf)} bytes"
                    )
                    self.read_buffer = bytearray()
                    break
                else:
                    self.gcode.respond_info(f"[{self.instance_num}] Resync: skipping {hdr} bytes")
                    self.read_buffer = buf[hdr:]
                    buf = self.read_buffer
                    if len(buf) < 7:
                        break

            # Parse frame length
            payload_len = struct.unpack('<H', buf[2:4])[0]
            frame_len = 2 + 2 + payload_len + 2 + 1

            if len(buf) < frame_len:
                break

            # Validate terminator
            terminator_idx = 4 + payload_len + 2
            if buf[terminator_idx] != 0xFE:
                next_hdr = buf.find(bytes([0xFF, 0xAA]), 1)
                if next_hdr == -1:
                    self.read_buffer = bytearray()
                else:
                    self.read_buffer = buf[next_hdr:]
                self.gcode.respond_info(f"[{self.instance_num}] Invalid frame tail")
                continue

            # Extract and validate CRC
            frame = bytes(buf[:frame_len])
            self.read_buffer = bytearray(buf[frame_len:])

            payload = frame[4:4 + payload_len]
            crc_rx = frame[4 + payload_len:4 + payload_len + 2]
            crc_calc = struct.pack('<H', self._calc_crc(payload))

            if crc_rx != crc_calc:
                self.gcode.respond_info("ACE: Invalid CRC")
                continue

            # Parse JSON payload
            try:
                ret = json.loads(payload.decode('utf-8'))
                frames.append(ret)
            except Exception as e:
                self.gcode.respond_info(f"[{self.instance_num}] JSON decode error: {e}")

        return frames

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

        return cb, cb is not None

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

    def start_heartbeat(self):
        """
        Start the heartbeat timer to send periodic status requests.

        First request sent immediately, then repeated at heartbeat_interval.
        """
        if self.heartbeat_timer is None:
            # Send first status request immediately
            self._send_heartbeat_request()
            # Register timer for periodic requests
            self.heartbeat_timer = self.reactor.register_timer(
                self._heartbeat_tick,
                self.reactor.NOW
            )
            self.gcode.respond_info(
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
            self.gcode.respond_info(
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
        request = {"method": "get_status"}

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
                    if (now - t0) > self.timeout_s:
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
                    # Opportunistic status request if idle
                    now = self.reactor.monotonic()
                    if not self.inflight and (now - self._last_status_request_time > 1.5):
                        def _status_cb(response):
                            pass  # Optionally update status
                        req = {"method": "get_status"}
                        cb = _status_cb
                        self._last_status_request_time = now
                    else:
                        break

                with self._lock:
                    rid = self._request_id
                    self._request_id += 1
                    req['id'] = rid
                    self._callback_map[rid] = cb
                    self.inflight[rid] = now

                self._send_frame(req)
        except Exception as e:
            logging.info(f'ACE[{self.instance_num}]: Write error {str(e)}')
            self.gcode.respond_info(str(e))

        return eventtime + 0.1

    def _reader(self, eventtime):
        """Timer callback: read frames from serial, dispatch responses."""
        try:
            raw = self._serial.read(size=4096)
        except SerialException:
            self.gcode.respond_info(
                f"[{self.instance_num}] Unable to communicate with ACE\n" +
                traceback.format_exc()
            )

            if not self._ace_pro_enabled:
                self.gcode.respond_info(
                    f"[{self.instance_num}] ACE Pro disabled - not scheduling reconnect"
                )
                return self.reactor.NEVER  # Stop this timer too

            # Try to reconnect
            if self.connect_timer is None:
                self.gcode.respond_info(f"[{self.instance_num}] Scheduling reconnect")
                self.reconnect(5.0)
                return self.reactor.NOW + 1.5
            else:
                self.gcode.respond_info(
                    f"[{self.instance_num}] Scheduling reconnect (already scheduled)"
                )
            return self.reactor.NEVER

        if raw:
            self.read_buffer += raw
        else:
            return eventtime + 0.05

        while True:
            buf = self.read_buffer
            if len(buf) < 7:
                break

            if not (buf[0] == 0xFF and buf[1] == 0xAA):
                hdr = buf.find(bytes([0xFF, 0xAA]))
                if hdr == -1:
                    self.gcode.respond_info(
                        f"[{self.instance_num}] Resync: dropped junk ({len(buf)} bytes)"
                    )
                    self.read_buffer = bytearray()
                    break
                else:
                    self.gcode.respond_info(f"[{self.instance_num}] Resync: skipping {hdr} bytes")
                    self.read_buffer = buf[hdr:]
                    buf = self.read_buffer
                    if len(buf) < 7:
                        break

            payload_len = struct.unpack('<H', buf[2:4])[0]
            frame_len = 2 + 2 + payload_len + 2 + 1

            if len(buf) < frame_len:
                break

            terminator_idx = 4 + payload_len + 2
            if buf[terminator_idx] != 0xFE:
                next_hdr = buf.find(bytes([0xFF, 0xAA]), 1)
                if next_hdr == -1:
                    self.read_buffer = bytearray()
                else:
                    self.read_buffer = buf[next_hdr:]
                self.gcode.respond_info(f"[{self.instance_num}] Invalid frame tail, resyncing")
                continue

            frame = bytes(buf[:frame_len])
            self.read_buffer = bytearray(buf[frame_len:])

            payload = frame[4:4 + payload_len]
            crc_rx = frame[4 + payload_len:4 + payload_len + 2]
            crc_calc = struct.pack('<H', self._calc_crc(payload))

            if crc_rx != crc_calc:
                self.gcode.respond_info("Invalid CRC")
                continue

            try:
                ret = json.loads(payload.decode('utf-8'))
            except Exception as e:
                self.gcode.respond_info(f"[{self.instance_num}] JSON decode error: {e}")
                continue

            if self._status_debug_logging:
                self._status_update_callback(ret)

            cb, was_solicited = self.dispatch_response(ret)
            if cb:
                try:
                    cb(response=ret)
                except Exception as e:
                    logging.info(f"[{self.instance_num}] Callback error: {str(e)}")
            else:
                self.gcode.respond_info(f"ACE[{self.instance_num}] unsolicited: {ret}")

        return eventtime + 0.05

    def _status_update_callback(self, response):
        """
        Handle status updates with detailed change detection.

        Tracks changes in:
        - Overall status (busy/ready)
        - Action (feeding/retracting/etc)
        - Individual slot status
        - Dryer status
        - Temperature changes
        """
        if not response or "result" not in response:
            return

        result = response.get("result")
        if not result:
            return

        # Extract current state
        current_status = result.get("status")
        current_action = result.get("action", "none")
        current_temp = result.get("temp", 0)
        dryer_status = result.get("dryer_status", {})
        feed_assist_count = result.get("feed_assist_count")
        cont_assist_time = result.get("cont_assist_time")
        slots = result.get("slots", [])

        if current_status is None:
            return

        # Detect overall status/action change
        status_changed = (current_status != self.last_status or
                          current_action != self.last_action)

        if status_changed:
            last_display = f"{self.last_status}/{self.last_action}" if self.last_status else 'unknown'
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: STATUS CHANGE: "
                f"'{last_display}' -> '{current_status}/{current_action}'"
            )
            self.last_status = current_status
            self.last_action = current_action

        # Detect feed assist counters
        if feed_assist_count is not None and feed_assist_count != self.last_feed_assist_count:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: FEED ASSIST COUNT: "
                f"'{self.last_feed_assist_count}' -> '{feed_assist_count}'"
            )
            self.last_feed_assist_count = feed_assist_count

        if cont_assist_time is not None and cont_assist_time != self.last_cont_assist_time:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: CONT ASSIST TIME: "
                f"'{self.last_cont_assist_time}' -> '{cont_assist_time}'"
            )
            self.last_cont_assist_time = cont_assist_time

        # Detect slot status changes
        for slot in slots:
            slot_idx = slot.get("index")
            slot_status = slot.get("status", "unknown")

            if slot_idx is not None:
                last_slot_status = self.last_slot_states.get(slot_idx)

                if slot_status != last_slot_status:
                    last_display = last_slot_status if last_slot_status else 'unknown'
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: SLOT[{slot_idx}] CHANGE: "
                        f"'{last_display}' -> '{slot_status}'"
                    )
                    self.last_slot_states[slot_idx] = slot_status

            # Detect any slot field change and dump full slot payload
            if slot_idx is not None:
                last_payload = self.last_slot_payloads.get(slot_idx)
                if last_payload != slot:
                    slot_dump = json.dumps(slot, sort_keys=True)
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: SLOT[{slot_idx}] DATA: {slot_dump}"
                    )
                    self.last_slot_payloads[slot_idx] = slot

        # Detect dryer status changes
        dryer_state = dryer_status.get("status", "stop")
        if dryer_state != self.last_dryer_status:
            if dryer_state != "stop":
                target_temp = dryer_status.get("target_temp", 0)
                remain_time = dryer_status.get("remain_time", 0)
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: DRYER: "
                    f"'{self.last_dryer_status or 'stop'}' -> '{dryer_state}' "
                    f"(target={target_temp}°C, remaining={remain_time}s)"
                )
            else:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: DRYER: stopped"
                )
            self.last_dryer_status = dryer_state

        # Detect significant temperature changes (>5°C)
        if self.last_temp is not None:
            temp_delta = abs(current_temp - self.last_temp)
            if temp_delta >= 5:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: TEMP CHANGE: "
                    f"{self.last_temp}°C -> {current_temp}°C "
                    f"(Δ{temp_delta:+.1f}°C)"
                )
        self.last_temp = current_temp


