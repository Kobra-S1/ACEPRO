#V0.1
import serial
import threading
import time
import logging
import json
import struct
import queue
import traceback
from serial import SerialException
import serial.tools.list_ports
import re

# Global registry for AcePro instances
ACE_INSTANCES = {}

def ace_get_instance(gcmd):
    if 'INSTANCE' in gcmd.get_command_parameters():
        instance_id = gcmd.get_int('INSTANCE')
        ace = ACE_INSTANCES.get(instance_id)
        if ace is None:
            gcmd.respond_info(f"ACE[{instance_id}]: No instance with id {instance_id}")
            raise gcmd.error(f"ACE[{instance_id}]: No instance with id {instance_id}")
        return ace
    # If TOOL is given, find the instance that manages it
    if 'TOOL' in gcmd.get_command_parameters():
        tool = gcmd.get_int('TOOL')
        for ace in ACE_INSTANCES.values():
            local_slot = tool - ace.tool_offset
            if 0 <= local_slot < ace.SLOT_COUNT:
                return ace
        gcmd.respond_info(f"No ACE instance manages tool {tool}")
        raise gcmd.error(f"No ACE instance manages tool {tool}")
    # Fallback to instance 0
    ace = ACE_INSTANCES.get(0)
    if ace is None:
        gcmd.respond_info("ACE[0]: No ace INSTANCE parameter given, using fallback INSTANCE=0 (which may, or may not is the right one)")
        raise gcmd.error("ACE[0]: No instance with id 0")
    return ace

def set_ace_global_enabled(printer, enabled):
    variables = printer.lookup_object('save_variables').allVariables
    variables['ace_global_enabled'] = enabled
    printer.lookup_object('gcode').run_script_from_command(
        f"SAVE_VARIABLE VARIABLE=ace_global_enabled VALUE={str(enabled)}"
    )

    for ace in ACE_INSTANCES.values():
        ace._ace_pro_enabled = enabled

def get_ace_global_enabled(printer):
    variables = printer.lookup_object('save_variables').allVariables
    return bool(variables.get('ace_global_enabled', True))

def runout_detection_disabled(method):
    """
    Decorator: Temporarily disable self.runout_detection_active
    for the duration of the method call, then restore the original value.
    """
    def wrapper(self, *args, **kwargs):
        original_value = self.runout_detection_active
        self.set_runout_detection_active(False)
        try:
            return method(self, *args, **kwargs)
        finally:
            self.set_runout_detection_active(original_value)
    return wrapper

class AcePro:
    SLOT_COUNT = 4
    QUEUE_MAXSIZE = 1024
    def _get_inventory_varname(self):
        return f"ace_inventory_{self.instance_number}"

    def _save_inventory(self):
        varname = self._get_inventory_varname()
        self.variables[varname] = self.inventory
        self.gcode.run_script_from_command(f"SAVE_VARIABLE VARIABLE={varname} VALUE='{json.dumps(self.inventory)}'")

    def _load_inventory(self):
        varname = self._get_inventory_varname()
        saved_inventory = self.variables.get(varname, None)
        if saved_inventory:
            self.inventory = saved_inventory
        else:
            self.inventory = [
                {
                    "status": "empty",
                    "color": [0, 0, 0],
                    "material": "",
                    "temp": 0
                } for _ in range(self.SLOT_COUNT)
            ]

    def __init__(self, config):
        self._connected = False
        self._serial = None
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        
        self._name = config.get_name()
        # --- Instance number parsing ---
        self.instance_number = 0  # default
        if self._name.strip().lower() == "ace":
            self.instance_number = 0
        else:
            m = re.match(r'^ace(?:[\s_]+)?(\d+)?$', self._name.strip().lower())
            if m:
                suffix = m.group(1)
                if suffix is not None:
                    self.instance_number = int(suffix)
        self.tool_offset = self.instance_number * self.SLOT_COUNT
        # --- end instance number parsing ---

        self._prev_rms_runout_gcode = None
        self.writer_timer = None
        self.reader_timer = None
        self.inflight = {}  # id -> send_time
        self.WINDOW_SIZE = 2
        self.timeout_s = 2.0
        self._lock = threading.Lock()
        self._hp_queue = queue.Queue(maxsize=self.QUEUE_MAXSIZE)
        self._queue = queue.Queue(maxsize=self.QUEUE_MAXSIZE)
        self._last_is_printing = None
        
        self.ace_pin = self.printer.lookup_object('output_pin ACE_Pro')  # cache
        self._ace_pro_enabled = get_ace_global_enabled(self.printer)
        self.ace_pin.get_status(self.printer.get_reactor().monotonic())['value']
        self.send_time = None
        self.read_buffer = bytearray()
        if self._name.startswith('ace '):
            self._name = self._name[4:]
        self.variables = self.printer.lookup_object('save_variables').allVariables

        self.serial_name = config.get('serial',None)
        self.baud = config.getint('baud', 115200)

        self.timeout_multiplier = config.getint('timeout_multiplier', 2)
        self.standard_filament_runout_detection = config.getboolean('standard_filament_runout_detection', True)
        self.filament_runout_sensor_name_rdm = config.get('filament_runout_sensor_name_rdm', 'filament_runout_rdm')
        self.filament_runout_sensor_name_nozzle = config.get('filament_runout_sensor_name_nozzle', 'filament_runout_nozzle')
        self.endless_spool_scope = config.get('endless_spool_scope', 'all')  # "all" or "local"
        self.feed_speed = config.getint('feed_speed', 50)
        self.retract_speed = config.getint('retract_speed', 50)
        self.total_max_feeding_length = config.getint('total_max_feeding_length', 2500)
        self.parkposition_to_toolhead_length = config.getint('parkposition_to_toolhead_length', 1000)
        self.parkposition_to_rms_sensor_length = config.getint('parkposition_to_rms_sensor_length', 150)
        if self.parkposition_to_rms_sensor_length > self.parkposition_to_toolhead_length:
            raise config.error(
                "parkposition_to_rms_sensor_length must be <= parkposition_to_toolhead_length"
            )
            
        self.toolchange_load_length = config.getint('toolchange_load_length', 3000)

        self.toolhead_sensor_to_cutter_length = config.getint(
            'toolhead_sensor_to_cutter', 22
        )

        self.toolhead_cutter_to_nozzle_length = config.getint(
            'toolhead_cutter_to_nozzle', 60
        )

        self.toolhead_nozzle_purge_length = config.getint(
            'toolhead_nozzle_purge', 1
        )

        self.default_color_change_purge_length = config.getint(
            'default_color_change_purge_length', 50
        )

        self.default_color_change_purge_speed = config.getint(
            'default_color_change_purge_speed', 400
        )
        
        self.toolchange_purge_length = self.default_color_change_purge_length
        self.toolchange_purge_speed = self.default_color_change_purge_speed

        self.toolhead_fast_loading_speed = config.getint('toolhead_fast_loading_speed', 15)
        self.toolhead_slow_loading_speed = config.getint('toolhead_slow_loading_speed', 5)

        
        self.incremental_feeding_length = config.getint('incremental_feeding_length', 50)
        self.incremental_feeding_speed = config.getint('incremental_feeding_speed', 30)
        self.extruder_feeding_length = config.getint('extruder_feeding_length', 1)
        self.extruder_feeding_speed = config.getint('extruder_feeding_speed', 5)

        self.extruder_retraction_length = config.getint('extruder_retraction_length', -50)
        self.extruder_retraction_speed = config.getint('extruder_retraction_speed', 10)
        self.max_dryer_temperature = config.getint('max_dryer_temperature', 55)

        # saved_endless_spool_enabled = self.variables.get(
        #     'ace_endless_spool_enabled', False
        # )
        # self.endless_spool_enabled = config.getboolean(
        #     'endless_spool', saved_endless_spool_enabled
        # )

        self.feed_assist_active_after_ace_connect = config.getboolean(
            'feed_assist_active_after_ace_connect', False
        )
        
        # self.endless_spool_in_progress = False
        # self.endless_spool_runout_detected = False
        self.runout_detection_active = False
        
        self._callback_map = {}
        self._feed_assist_index = -1
        self._request_id = 0
        self._last_assist_count = 0
        self._assist_hit_count = 0
        self._park_in_progress = False
        self._park_is_toolchange = False
        self._park_previous_tool = -1
        self._park_index = -1

        #Filament sensors dictionary, maps 'sensor name' -> sensor object
        self.sensors = {}
        self._prev_sensors_enabled_state = {}
        
        self._info = {
            'status': 'ready',
            'dryer': {
                'status': 'stop',
                'target_temp': 0,
                'duration': 0,
                'remain_time': 0
            },
            'temp': 0,
            'enable_rfid': 1,
            'fan_speed': 7000,
            'feed_assist_count': 0,
            'cont_assist_time': 0.0,
            'slots': [
                {
                    'index': i,
                    'status': 'empty',
                    'sku': '',
                    'type': '',
                    'color': [0, 0, 0]
                } for i in range(self.SLOT_COUNT)
            ]
        }

        # Add inventory for 4 slots - load from persistent variables if available
        saved_inventory = self.variables.get('ace_inventory', None)
        if saved_inventory:
            self.inventory = saved_inventory
        else:
            self.inventory = [
                {
                    "status": "empty",
                    "color": [0, 0, 0],
                    "material": "",
                    "temp": 0
                } for _ in range(self.SLOT_COUNT)
            ]

        self.create_mmu_sensors(config)
        
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:disconnect', self._handle_disconnect)

        # Register this instance
        ACE_INSTANCES[self.instance_number] = self
       
        # Register original T<n> macros for this instance (always use default purge settings)
        for i in range(self.SLOT_COUNT):
            tool_index = self.tool_offset + i
            macro_name = f"T{tool_index}"
            def make_simple_tool_macro(idx, macro_name=macro_name):
                def tool_macro(gcmd):
                    self.gcode.respond_info(f"ACE[{self.instance_number}]: Tool macro {macro_name} called, will select tool {idx}")
                    purgelength = self.toolchange_purge_length
                    purgespeed = self.toolchange_purge_speed
                    class DummyGcmd:
                        def get_int(self, key, default=None):
                            if key == 'TOOL':
                                return idx
                            return gcmd.get_int(key, default)
                        def get_float(self, key, default=None):
                            return gcmd.get_float(key, default)
                        def get(self, key, default=None):
                            if key == 'TOOL':
                                return str(idx)
                            return gcmd.get(key, default)
                        def respond_info(self, msg):
                            gcmd.respond_info(msg)
                        def error(self, msg):
                            return gcmd.error(msg)
                    self.cmd_ACE_CHANGE_TOOL(DummyGcmd(), purgelength, purgespeed)
                return tool_macro
            self.gcode.register_command(
                macro_name,
                make_simple_tool_macro(tool_index),
                desc=f"Select tool {tool_index} (ACE instance {self.instance_number}) [always uses default purge settings]"
            )
            self._last_status_request_time = 0

    @staticmethod
    def get_all_available_slots():
        """
        Return a list of all available tool indices (slots) across all ACE instances.
        """
        slots = []
        for ace in ACE_INSTANCES.values():
            slots.extend([ace.tool_offset + i for i in range(ace.SLOT_COUNT)])
        return slots
    
    def _find_output_pin(self, section_name="ace_pro"):
        """Return the output_pin object for the given section, or None."""
        want = f"output_pin {section_name}".lower()
        for name, obj in self.printer.lookup_objects():
            if name.lower() == want:
                return obj
        return None
    
    def _dump_object_names(self):
        names = [name for name, _ in self.printer.lookup_objects()]
        self.gcode.respond_info("Objects: " + ", ".join(names))

    def is_ace_enabled(self):
        gv = getattr(self.ace_pin, 'get_value', None)
        if callable(gv):
            return bool(gv())
        return bool(self.ace_pin.get_status(self.printer.get_reactor().monotonic())['value'])
    
    def _handle_unsolicited(self, message):
        """Handle unsolicited messages from the device."""
        # If it's a status/result message, process it as a status update
        if "result" in message:
            msg_type = message.get("method") or message.get("type") or "result"
            self.gcode.respond_info(f"ACE[{self.instance_number}] unsolicited ({getattr(self, 'unsolicited_count', '?')}): {msg_type} {message}")
        else:
            msg_type = message.get("method") or message.get("type")
            self.gcode.respond_info(f"ACE[{self.instance_number}] unsolicited ({getattr(self, 'unsolicited_count', '?')}): {msg_type} {message}")
            
    def _calc_crc(self, buffer):
        _crc = 0xffff
        for byte in buffer:
            data = byte
            data ^= _crc & 0xff
            data ^= (data & 0x0f) << 4
            _crc = ((data << 8) | (_crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return _crc

    def _send_request(self, request):
        with self._lock:
            if 'id' not in request:
                request['id'] = self._request_id
                self._request_id += 1
        payload = json.dumps(request).encode('utf-8')
        data = bytearray([0xFF, 0xAA])
        data += struct.pack('<H', len(payload))     # little-endian
        data += payload
        data += struct.pack('<H', self._calc_crc(payload))  # little-endian
        data += b'\xFE'
        try:
            self._serial.write(data)
        except Exception as e:
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Serial write error: {e}")

    def _reader(self, eventtime):
        now = self.reactor.monotonic()
        with self._lock:
            inflight_items = list(self.inflight.items())
        for rid, t0 in inflight_items:
            if (now - t0) > self.timeout_s:
                pass

        try:
            raw = self._serial.read(size=4096)
        except SerialException:
            self.gcode.respond_info("[{self.instance_number}] Unable to communicate with the ACE PRO\n" + traceback.format_exc())
            self._serial_disconnect()
            if self.connect_timer is None:
                self.connect_timer = self.reactor.register_timer(self._connect,  self.reactor.monotonic() + 1.0) #self.reactor.NOW)
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
                    self.gcode.respond_info(f"[{self.instance_number}] Resync: dropped junk ({len(buf)} bytes)")
                    self.read_buffer = bytearray()
                    break
                else:
                    self.gcode.respond_info(f"[{self.instance_number}] Resync: skipping {hdr} bytes")
                    self.read_buffer = buf[hdr:]
                    buf = self.read_buffer
                    if len(buf) < 7:
                        break

            payload_len = struct.unpack('<H', buf[2:4])[0]
            frame_len = 2 + 2 + payload_len + 2 + 1
            if len(buf) < frame_len:
                break

            # Find the terminator index
            terminator_idx = 4 + payload_len + 2
            if buf[terminator_idx] != 0xFE:
                # Search for the next possible frame start
                next_hdr = buf.find(bytes([0xFF, 0xAA]), 1)
                if next_hdr == -1:
                    self.read_buffer = bytearray()
                else:
                    self.read_buffer = buf[next_hdr:]
                self.gcode.respond_info(f"[{self.instance_number}] Invalid frame tail, resyncing")
                continue

            frame = bytes(buf[:frame_len])
            self.read_buffer = bytearray(buf[frame_len:])

            payload = frame[4:4+payload_len]
            crc_rx = frame[4+payload_len:4+payload_len+2]
            crc_calc = struct.pack('<H', self._calc_crc(payload))
            if crc_rx != crc_calc:
                self.gcode.respond_info("Invalid CRC")
                continue

            try:
                ret = json.loads(payload.decode('utf-8'))
            except Exception as e:
                self.gcode.respond_info(f"[{self.instance_number}]JSON decode error: {e}")
                continue

            rid = ret.get('id')
            cb = None
            with self._lock:
                if rid is not None:
                    cb = self._callback_map.pop(rid, None)
                    if cb:
                        self.inflight.pop(rid, None)
            if cb:
                try:
                    cb(response=ret)
                except Exception as e:
                    logging.info("[{self.instance_number}]Callback error: " + str(e))
                    stack = ''.join(traceback.format_stack(limit=10))
                    self.gcode.respond_info(f"ADBG[{self.instance_number}]: Callstack={stack}")
            else:
                self._handle_unsolicited(ret)
        return eventtime + 0.05

    def _writer(self, eventtime):
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
                                self.gcode.respond_info(f"ACE[{self.instance_number}]: Callback error: {e}")
                        self.inflight.pop(rid, None)
            # fill window
            while True:
                with self._lock:
                    if len(self.inflight) >= self.WINDOW_SIZE:
                        break
                task = None
                if self._hp_queue and not self._hp_queue.empty():
                    task = self._hp_queue.get()
                elif self._queue and not self._queue.empty():
                    task = self._queue.get()
                else:
                    # opportunistic status ONLY if absolutely idle
                    now = time.monotonic()
                    if not self.inflight and (now - self._last_status_request_time > 1.5):  # 2s between status requests
                        def _status_cb(response):
                            if response is not None:
                                with self._lock:
                                    self._info = response.get('result')
                                    slots = self._info.get('slots', [])
                                    for slot in slots:
                                        idx = slot.get('index')
                                        if idx is not None and 0 <= idx < self.SLOT_COUNT:
                                            self.inventory[idx]['status'] = slot.get('status', 'empty')
                        task = ({"method": "get_status"}, _status_cb)
                        now_str = time.strftime("%H:%M:%S", time.localtime()) + f".{int((time.time()*1000)%1000):03d}"
                        #self.gcode.respond_info(f"{now_str} ACE[{self.instance_number}]: IDLE -> Requesting status update")
                        self._last_status_request_time = now
                    else:
                        break

                if task:
                    req, cb = task
                    with self._lock:
                        rid = self._request_id
                        self._request_id += 1
                        req['id'] = rid
                        self._callback_map[rid] = cb
                        self.inflight[rid] = now
                    self._send_request(req)
        except Exception as e:
            logging.info('ACE[{self.instance_number}]: Write error ' + str(e))
            self.gcode.respond_info(str(e))
        return eventtime + 0.1  # tick faster since we pipeline

    def _handle_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self._connected = False

        # --- Clear feed assist index on startup ---
        self._feed_assist_index = -1
        self.variables[f'ace_feed_assist_index_{self.instance_number}'] = -1
        self.gcode.run_script_from_command(
            f"SAVE_VARIABLE VARIABLE=ace_feed_assist_index_{self.instance_number} VALUE=-1"
        )

        # Drain and clear all queues before re-initializing
        self._clear_queue(self._queue)
        self._clear_queue(self._hp_queue)
        self._queue = queue.Queue(maxsize=self.QUEUE_MAXSIZE)
        self._hp_queue = queue.Queue(maxsize=self.QUEUE_MAXSIZE)
        self.connect_timer = self.reactor.register_timer(self._connect, self.reactor.NOW)

        self.gcode.respond_info('ADBG[{self.instance_number}]: register monitoring timer')
        if self.instance_number == 0:
            self._monitoring_timer = self.reactor.register_timer(self._monitor, self.reactor.NOW)

           
    def _handle_disconnect(self):
        logging.info('ACE[{self.instance_number}]: Closing connection to ' + self.serial_name)
        self.gcode.respond_info('ADBG:Closing connection to ' + self.serial_name)
        try:
            if self._serial:
                self._serial.close()
        except Exception as e:
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Error closing serial: {e}")
        self._connected = False
        try:
            self.reactor.unregister_timer(self.writer_timer)
            self.reactor.unregister_timer(self.reader_timer)
            if self._monitoring_timer:
                self.gcode.respond_info('ADBG:Unregister monitoring timer ')
                self.reactor.unregister_timer(self._monitoring_timer)
                self._monitoring_timer = None
        except Exception as e:
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Error unregistering timers: {e}")

        self.restore_filament_runout_sensor_state()
            
        # Drain and clear all queues on disconnect
        self._clear_queue(self._queue)
        self._clear_queue(self._hp_queue)
        self._queue = queue.Queue(maxsize=self.QUEUE_MAXSIZE)
        self._hp_queue = queue.Queue(maxsize=self.QUEUE_MAXSIZE)

        with self._lock:
            self._callback_map.clear()
            self.inflight.clear()
        # self.endless_spool_in_progress = False
        # self.endless_spool_runout_detected = False
        # self._park_in_progress = False

    def restore_filament_runout_sensor_state(self):
        rms_sensor = self.printer.lookup_object(f"filament_switch_sensor {self.filament_runout_sensor_name_rdm}")
        if self._prev_rms_runout_gcode and rms_sensor:
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Restoring runout gcode: {self._prev_rms_runout_gcode}")
            rms_sensor.runout_helper.runout_gcode = self._prev_rms_runout_gcode
            self._prev_rms_runout_gcode = None

        for intern_name, sensor in self.sensors.items():
            if intern_name in self._prev_sensors_enabled_state:
                prev_state = self._prev_sensors_enabled_state[intern_name]
                sensor.sensor_enabled = prev_state
                self.gcode.respond_info(f"ACE[{self.instance_number}]: Restoring sensor '{intern_name}' enabled state to {prev_state}")  

    def dwell(self, delay = 1.):
        currTs = self.reactor.monotonic()
        self.reactor.pause(currTs + delay)

    def send_request(self, request, callback):
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: send_request request={request}")
        self._info['status'] = 'busy'
        try:
            self._queue.put([request, callback], timeout=1)
        except queue.Full:
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Request queue is full!")

    def send_high_prio_request(self, request, callback):
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: send_high_prio_request request={request}")
        self._info['status'] = 'busy'
        try:
            self._hp_queue.put([request, callback], timeout=1)
        except queue.Full:
            self.gcode.respond_info("ACE[{self.instance_number}]: High-priority request queue is full!")

    def is_ace_ready(self):
        return self._info['status'] == 'ready'
    
    def wait_ace_ready(self):
        waited = 0.0
        interval = 0.5
        while not self.is_ace_ready():
            currTs = self.reactor.monotonic()
            self.reactor.pause(currTs + interval)
            waited += interval
            if waited >= 25.0:
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: send_high_prio_request _info:\n{self._info}")
                # Log the call stack
                stack = ''.join(traceback.format_stack(limit=10))
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: send_high_prio_request callstack:\n{stack}")
                
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: wait_ace_ready() requesting status after {waited:.1f}s")
                
                # --- Send startup status update to all ACE instances ---
                if ACE_INSTANCES is None or len(ACE_INSTANCES) == 0:
                    self.gcode.respond_info("ADBG: No ACE instances found in ACE_INSTANCES!")
                else:
                    for ace_instance in ACE_INSTANCES.values():
                        ace_instance.send_high_prio_request(
                            request={"method": "get_status"},
                            callback=ace_instance._status_update_callback
                        )
                waited = 0.0
            
    def _extruder_move(self, length, speed):
        pos = self.toolhead.get_position()
        pos[3] += length
        self.toolhead.move(pos, speed)
        
        return pos[3]


    def determine_printing_state(self, eventtime, toolhead, print_stats):
        is_printing = False
            
        if hasattr(toolhead, 'get_status'):
            toolhead_status = toolhead.get_status(eventtime)
            if 'homed_axes' in toolhead_status and toolhead_status['homed_axes']:
                is_printing = True
            
        if print_stats:
            stats = print_stats.get_status(eventtime)
            if stats.get('state') in ['printing']:
                is_printing = True
            
        try:
            is_printing = False
            if print_stats and print_stats.get_status(eventtime).get('state') == 'printing':
                is_printing = True                  
        except:
            is_printing = True
        return is_printing


    
    def create_mmu_sensors(self, config):
        # Register endstops using existing filament_switch_sensor objects
        try:
            intern_name = 'toolhead_sensor'
            toolhead_sensor = self.printer.lookup_object(f"filament_switch_sensor {self.filament_runout_sensor_name_nozzle}")
            # Disable automatic pause logic
            self._prev_sensors_enabled_state[intern_name] = toolhead_sensor.runout_helper
            self.sensors[intern_name] = toolhead_sensor.runout_helper

        except Exception as e:
            print(e)
            raise config.error(
                "You must have a filament_switch_sensor at your toolhead in your printer.cfg"
            )
        try:

            import jinja2
            intern_name = 'return_module'
            rms_sensor = self.printer.lookup_object(f"filament_switch_sensor {self.filament_runout_sensor_name_rdm}")
            self._prev_sensors_enabled_state[intern_name] = rms_sensor.runout_helper
                       
            if self._prev_rms_runout_gcode is None:
                self._prev_rms_runout_gcode = rms_sensor.runout_helper.runout_gcode
            # Create a Jinja template for the runout_gcode
            runout_gcode_template = jinja2.Template('PAUSE CUT_FILAMENT=1\n')
            rms_sensor.runout_helper.runout_gcode = runout_gcode_template
            self.sensors[intern_name] = rms_sensor.runout_helper
        except Exception:
            raise config.error(
                "You must have a filament_switch_sensor for your filament-hub named in your printer.cfg"
            )
        
        # Intial disable standard runout detection, it will be enabled automatically when needed
        self.store_previous_runout_sensor_state()
        self.disable_standard_runout_detection_for_all_sensors()
    
    def store_previous_runout_sensor_state(self):
        for intern_name, sensor in self.sensors.items():
            self._prev_sensors_enabled_state[intern_name] = sensor.sensor_enabled
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Storing sensor '{intern_name}' enabled state as {sensor.sensor_enabled}")
            
    def disable_standard_runout_detection_for_all_sensors(self):
        for intern_name, sensor in self.sensors.items():
            if sensor.sensor_enabled:
                self.gcode.respond_info(f"ACE[{self.instance_number}]: Disabling standard runout detection for sensor '{intern_name}'")
                sensor.sensor_enabled = False
        
    def _get_switch_state(self, name):
        sensor = self.sensors[name]
        return bool(sensor.filament_present)

    def _serial_disconnect(self):
        if self._serial is not None and self._serial.is_open:
            self._serial.close()
            self._connected = False

        if self.reader_timer:
            self.reactor.unregister_timer(self.reader_timer)
            self.reader_timer = None

        if self.writer_timer:
            self.reactor.unregister_timer(self.writer_timer)
            self.writer_timer = None


    def _monitor(self, eventtime):
        """Monitor for runout detection during printing"""
        if self._ace_pro_enabled and self.is_ace_enabled() == False:
            self.restore_filament_runout_sensor_state()
            
            set_ace_global_enabled(self.printer, False)
            self.variables['ace_current_index'] = -1

            # --- Clear feed assist index on reset ---
            self._feed_assist_index = -1
            self.variables[f'ace_feed_assist_index_{self.instance_number}'] = -1
            self.gcode.run_script_from_command(
                f"SAVE_VARIABLE VARIABLE=ace_feed_assist_index_{self.instance_number} VALUE=-1"
            )                    
            self.gcode.respond_info("ACE[{self.instance_number}]: ACE Pro disabled")
        elif not self._ace_pro_enabled and self.is_ace_enabled():
            self.store_previous_runout_sensor_state()
            self.disable_standard_runout_detection_for_all_sensors()
            set_ace_global_enabled(self.printer, True)
            self.gcode.respond_info("ACE[{self.instance_number}]: ACE Pro enabled")
 
        # if not self.endless_spool_enabled or self._park_in_progress or self.endless_spool_in_progress:
        #     return eventtime + 0.1

        current_tool = self.variables.get('ace_current_index', -1)
        if current_tool == -1:
            return eventtime + 0.1

        try:
            toolhead = self.printer.lookup_object('toolhead')
            print_stats = self.printer.lookup_object('print_stats', None)
            
            is_printing = False
            
            if hasattr(toolhead, 'get_status'):
                toolhead_status = toolhead.get_status(eventtime)
                if 'homed_axes' in toolhead_status and toolhead_status['homed_axes']:
                    is_printing = True
            
            if print_stats:
                stats = print_stats.get_status(eventtime)
                if stats.get('state') in ['printing']:
                    is_printing = True
            
            # try:
            #     printer_idle = self.printer.lookup_object('idle_timeout')
            #     idle_state = printer_idle.get_status(eventtime)['state']
            #     if idle_state in ['Printing', 'Ready']:
            #         is_printing = True
            # except:
            #     is_printing = True

            # if current_tool >= 0:
            #     self._endless_spool_runout_handler()
            
            if is_printing:
                return eventtime + 0.05  # Check every 50ms during printing
            else:
                return eventtime + 0.2   # Check every 200ms when idle
                
        except Exception as e:
            logging.info(f'ACE[{self.instance_number}]: monitor error: {str(e)}')
            return eventtime + 0.1

    def _connect(self, eventtime):
        try:
            port = self.find_com_port('ACE', self.instance_number)
            self.serial_name = port
            if port is None:
                return eventtime + 1
            self.gcode.respond_info('Try connecting')
            self._serial = serial.Serial(
                port=port,
                baudrate=self.baud,
                timeout=0,
                write_timeout=0)

            if self._serial.is_open:
                self._connected = True
                logging.info(f'ACE[{self.instance_number}]: Connected to ' + port)
                self.gcode.respond_info(f'ACE[{self.instance_number}]: Connected to {port} {eventtime}')
                self.writer_timer = self.reactor.register_timer(self._writer, self.reactor.NOW)
                self.reader_timer = self.reactor.register_timer(self._reader, self.reactor.NOW)
                self.send_request(request={"method": "get_info"},
                                  callback=lambda response: self.gcode.respond_info(f"ACE[{self.instance_number}]:"+str(response)))


                ace_current_index = self.variables.get('ace_current_index', -1)
                global_tool_index = self.tool_offset + ace_current_index

                if self.feed_assist_active_after_ace_connect:
                    if ace_current_index != -1:
                       self.gcode.respond_info(f'ACE[{self.instance_number}]: Enabling feed assist on reconnect for index {ace_current_index} T{global_tool_index}')
                       self._enable_feed_assist(ace_current_index)
                else:
                    # Restore feed assist index if needed
                    feed_assist_var = f'ace_feed_assist_index_{self.instance_number}'
                    feed_assist_index = self.variables.get(feed_assist_var, -1)
                    if feed_assist_index != -1:
                        self.gcode.respond_info(f'ACE[{self.instance_number}]: Re-enabling feed assist on reconnect for index {feed_assist_index}')
                        self._enable_feed_assist(feed_assist_index)
                    else:
                        self.gcode.respond_info(f'ACE[{self.instance_number}]: Feed assist not activated on reconnect for index {feed_assist_index} T{self.tool_offset + feed_assist_index}')

                if self.connect_timer is not None:
                    self.reactor.unregister_timer(self.connect_timer)
                    self.connect_timer = None
                return self.reactor.NEVER
        except serial.serialutil.SerialException:
            self._serial = None
        return eventtime + 1

    def cmd_ACE_SET_PURGE_AMOUNT(self, gcmd):
        self.toolchange_purge_length = gcmd.get_float('PURGELENGTH', self.default_color_change_purge_length)
        self.toolchange_purge_speed = gcmd.get_float('PURGESPEED', self.default_color_change_purge_speed )
        self.gcode.respond_info(f'ACE[{self.instance_number}]: toolchange_purge_length={self.toolchange_purge_length} purge_speed={self.toolchange_purge_speed}')       
    
    cmd_ACE_GET_STATUS_help = "Query status from ACE"
    
    def cmd_ACE_GET_STATUS(self, gcmd):
        self.send_high_prio_request(request={"method": "get_status"},
                            callback=lambda  response: self.gcode.respond_info(f"ACE[{self.instance_number}]:"+str(response)))
        
    cmd_ACE_RECONNECT_help = 'Close and re-open connection to ACE'

    def cmd_ACE_RECONNECT(self, gcmd):
        self._serial_disconnect()
        self.connect_timer = self.reactor.register_timer(self._connect, self.reactor.NOW)

    cmd_ACE_RESET_PERSISTENT_INVENTORY_help = 'Reset persitent filament inventory'

    def cmd_ACE_RESET_PERSISTENT_INVENTORY(self, gcmd):
        self.inventory = [
            {
                "status": "empty",
                "color": [0, 0, 0],
                "material": "",
                "temp": 0
            } for _ in range(self.SLOT_COUNT)
        ]
        self._save_inventory()
        self.variables['ace_current_index'] = -1

    cmd_ACE_RESET_ACTIVE_TOOLHEAD_help = 'Reset active toolhead information'
        
    def cmd_ACE_RESET_ACTIVE_TOOLHEAD(self, gcmd):
        self.variables['ace_current_index'] = -1
        # --- Clear feed assist index on reset ---
        self._feed_assist_index = -1
        self.variables[f'ace_feed_assist_index_{self.instance_number}'] = -1
        self.gcode.run_script_from_command(
            f"SAVE_VARIABLE VARIABLE=ace_feed_assist_index_{self.instance_number} VALUE=-1"
        )        

    cmd_ACE_START_DRYING_help = 'Starts ACE Pro dryer'

    def cmd_ACE_START_DRYING(self, gcmd):
        temperature = gcmd.get_int('TEMP')
        duration = gcmd.get_int('DURATION', 240)

        if duration <= 0:
            raise gcmd.error('Wrong duration')
        if temperature <= 0 or temperature > self.max_dryer_temperature:
            raise gcmd.error('Wrong temperature')

        def callback(response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])

            self.gcode.respond_info('Started ACE drying')

        self.send_request(
            request={"method": "drying", "params": {"temp": temperature, "fan_speed": 7000, "duration": duration}},
            callback=callback)

    cmd_ACE_STOP_DRYING_help = 'Stops ACE Pro dryer'

    def cmd_ACE_STOP_DRYING(self, gcmd):
        def callback(response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])

            self.gcode.respond_info('Stopped ACE drying')
        
        self.send_request(request={"method": "drying_stop"}, callback=callback)

    def _enable_feed_assist(self, index):
        def callback(response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE Error: " + response['msg'])
            elif 'msg' in response and (response['msg'] != "success"):
                raise ValueError("ACE Error: " + response['msg'])
            else:
                self.gcode.respond_info(f'Enable feed-assist respone for index={index} == {str(response)}')
                self._feed_assist_index = index
                self.variables[f'ace_feed_assist_index_{self.instance_number}'] = index
                self.gcode.run_script_from_command(
                    f"SAVE_VARIABLE VARIABLE=ace_feed_assist_index_{self.instance_number} VALUE={index}"
                )
                self.gcode.respond_info(str(response))
        self.wait_ace_ready()
        self.send_request(request={"method": "start_feed_assist", "params": {"index": index}}, callback=callback)
        self.dwell(delay=0.7)
        self.gcode.respond_info(f'Enabled ACE feed assist index={index}')

    cmd_ACE_ENABLE_FEED_ASSIST_help = 'Enables ACE feed assist'

    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        index = gcmd.get_int('INDEX')

        if index < 0 or index >= self.SLOT_COUNT:
            raise gcmd.error('Wrong index')

        self._enable_feed_assist(index)

    def _disable_feed_assist(self, index):
        def callback(response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE Error: " + response['msg'])

            self._feed_assist_index = -1
            self.variables[f'ace_feed_assist_index_{self.instance_number}'] = -1
            self.gcode.run_script_from_command(
                f"SAVE_VARIABLE VARIABLE=ace_feed_assist_index_{self.instance_number} VALUE=-1"
            )
            self.gcode.respond_info(f'Disabled ACE feed assist index={index}')
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: _disable_feed_assist waiting")
        self.dwell(0.3)
        self.wait_ace_ready()
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: _disable_feed_assist finished")
        self.send_request(request={"method": "stop_feed_assist", "params": {"index": index}}, callback=callback)
        self.dwell(0.3)

    cmd_ACE_DISABLE_FEED_ASSIST_help = 'Disables ACE feed assist'

    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        if self._feed_assist_index != -1:
            index = gcmd.get_int('INDEX', self._feed_assist_index)
        else:
            index = gcmd.get_int('INDEX')

        if index < 0 or index >= self.SLOT_COUNT:
            raise gcmd.error('Wrong index')

        self._disable_feed_assist(index)

    def _feed(self, index, length, speed, callback=None):
        request = {"method": "feed_filament", "params": {"index": index, "length": length, "speed": speed}}
        if callback is None:
            def callback(response, request=request):  # capture request in closure
                # Print request if forbidden, even if code==0
                if response.get('msg') == 'FORBIDDEN' and response.get('code', 0) == 0:
                    self.gcode.respond_info(f"ACE[{self.instance_number}]: _feed forbidden: {response} (request={request})")
                elif 'code' in response and response['code'] != 0:
                    self.gcode.respond_info(f"ACE[{self.instance_number}]: _feed error: {response['msg']} (request={request})")
                    raise ValueError("ACE Error: " + response['msg'])
                else:
                    self.gcode.respond_info(f"ADBG[{self.instance_number}]: _feed response={response}")
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: _feed(index={index}, length={length}, speed={speed})")
        self.send_request(
            request=request,
            callback=callback
        )
    cmd_ACE_FEED_help = 'Feeds filament from ACE'

    def cmd_ACE_FEED(self, gcmd):
        index = gcmd.get_int('INDEX')
        length = gcmd.get_int('LENGTH')
        speed = gcmd.get_int('SPEED', self.feed_speed)

        if index < 0 or index >= self.SLOT_COUNT:
            raise gcmd.error('Wrong index')
        if length <= 0:
            raise gcmd.error('Wrong length')
        if speed <= 0:
            raise gcmd.error('Wrong speed')

        self._feed(index, length, speed)

    def _stop_feed(self, index):
        def callback(response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE Error: " + response['msg'])
            else:
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: _stop_feed response={response}")
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: _stop_feed(index={index})")
        self.send_high_prio_request(
            request={"method": "stop_feed_filament", "params": {"index": index}},
            callback=callback)

    def cmd_ACE_STOP_FEED(self, gcmd):
        index = gcmd.get_int('INDEX')

        if index < 0 or index >= self.SLOT_COUNT:
            raise gcmd.error('Wrong index')

        self._stop_feed(index)

    cmd_ACE_STOP_FEED_help = 'Stops feeding filament from ACE'
        
    def _retract(self, index, length, speed):
        def callback(response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE Error: " + response['msg'])
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: _retract(index={index}, length={length}, speed={speed})")

        self.send_request(
            request={"method": "unwind_filament", "params": {"index": index, "length": length, "speed": speed}},
            callback=callback)

    cmd_ACE_RETRACT_help = 'Retracts filament back to ACE'

    def cmd_ACE_RETRACT(self, gcmd):
        index = gcmd.get_int('INDEX')
        length = gcmd.get_int('LENGTH')
        speed = gcmd.get_int('SPEED', self.retract_speed)

        if index < 0 or index >= self.SLOT_COUNT:
            raise gcmd.error('Wrong index')
        if length <= 0:
            raise gcmd.error('Wrong length')
        if speed <= 0:
            raise gcmd.error('Wrong speed')

        self._retract(index, length, speed)

    cmd_ACE_STOP_RETRACT_help = "Stops any running retraction for given slot index"
    def _stop_retract(self, index):
        def callback(response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE Error: " + response['msg'])

        self.gcode.respond_info(f"ADBG[{self.instance_number}]: _stop_retract(index={index})")

        self.send_request(
            request={"method": "stop_unwind_filament", "params": {"index": index}},
            callback=callback)
        self.dwell(delay=(0.1))

    def cmd_ACE_STOP_RETRACT(self, gcmd):
        index = gcmd.get_int('INDEX')

        if index < 0 or index >= self.SLOT_COUNT:
            raise gcmd.error('Wrong index')

        self._stop_retract(index)
 
    def _feed_filament_into_toolhead(self, tool, check_pre_condition = True):
        self.wait_ace_ready()
        local_slot = tool - self.tool_offset
        if local_slot < 0 or local_slot >= self.SLOT_COUNT:
            raise ValueError(f"Tool {tool} not managed by this ACE instance.")
        if check_pre_condition:
            if self._get_switch_state('return_module'):
                raise ValueError("Cant feed, still filament stuck in return module? " + str(bool(self._get_switch_state('return_module'))))
            if self._get_switch_state('toolhead_sensor'):
                raise ValueError("Cant feed, still filament in nozzle? ")

        self.variables['ace_filament_pos'] = "bowden"
        self._feed(local_slot, self.toolchange_load_length, self.feed_speed)

        expected_time = self.toolchange_load_length / self.feed_speed
        timeout_s = expected_time * self.timeout_multiplier

        start_time = time.monotonic()
        while not self._get_switch_state('toolhead_sensor'):
            if time.monotonic() - start_time > timeout_s:
                self.gcode.respond_info(f"ACE[{self.instance_number}]: Feed timeout after {timeout_s} seconds!")
                break
            self.dwell(delay=0.01)
            
        self._stop_feed(local_slot)

        if not self._get_switch_state('toolhead_sensor'):
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: Feed ended, but no filament reached toolhead nozzle?")
            accumulated_feed_length = self.toolchange_load_length
            while not self._get_switch_state('toolhead_sensor') and (accumulated_feed_length < self.total_max_feeding_length):
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: _park_to_toolhead() _feed({self.incremental_feeding_length},{self.incremental_feeding_speed})")
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: _park_to_toolhead() _extruder_move({self.extruder_feeding_length}, {self.extruder_feeding_speed})")
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: accumulated_feed_length={accumulated_feed_length}, {self.total_max_feeding_length})")
                self._feed(local_slot, self.incremental_feeding_length, self.incremental_feeding_speed)
                self._extruder_move(self.extruder_feeding_length, self.extruder_feeding_speed)
                self.gcode.run_script_from_command("G92 E0")
                accumulated_feed_length = accumulated_feed_length + self.incremental_feeding_length
                self.dwell(delay=(self.incremental_feeding_length / self.incremental_feeding_speed) + 0.1)
                
            if not self._get_switch_state('toolhead_sensor'):
                    self.gcode.respond_info(f"ADBG[{self.instance_number}]: accumulated_feed_length={accumulated_feed_length}, {self.total_max_feeding_length})")                
                    raise ValueError(f"ADBG[{self.instance_number}]: feeded {accumulated_feed_length}, but nozzle filament sensor not reached!")

        self.gcode.respond_info(f"ADBG[{self.instance_number}]: _feed_filament_into_toolhead() Stopped feeding")        

        self.wait_ace_ready()
        self._enable_feed_assist(local_slot)

        self.variables['ace_filament_pos'] = "toolhead"
        self.gcode.run_script_from_command("G92 E0")

        self.gcode.respond_info(f"ADBG[{self.instance_number}]: feeding from sensor to noozle...")
        
        self._extruder_move(self.toolhead_sensor_to_cutter_length, self.toolhead_fast_loading_speed)
        self.gcode.run_script_from_command("G92 E0")
        self._extruder_move(self.toolhead_cutter_to_nozzle_length, self.toolhead_slow_loading_speed)
        self.gcode.run_script_from_command("G92 E0")
        self._extruder_move(self.toolhead_nozzle_purge_length, self.toolhead_slow_loading_speed)
        self.gcode.run_script_from_command("G92 E0")
                
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: feeding from sensor to noozle finished.")
        
        self.variables['ace_filament_pos'] = "nozzle"
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: _feed_filament_into_toolhead: _ace_fpos set to:{self.variables['ace_filament_pos']}")       

    cmd_ACE_CHANGE_TOOL_help = 'Changes tool'

    def set_runout_detection_active(self, active: bool):
        self.runout_detection_active = active
        self.sensors['return_module'].sensor_enabled = active
        self.sensors['return_module'].pause_on_runout = active
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: set_runout_detection_active={self.runout_detection_active}")
    
    def _sensor_bounded_toolhead_pullback(self, max_pull_mm=None, step_mm=5, speed=None):
        """
        Retract at the toolhead in small steps until the toolhead sensor clears,
        capped at max_pull_mm. Uses relative E move via self._extruder_move().
        """
        if max_pull_mm is None:
            max_pull_mm = abs(self.extruder_retraction_length)
        if speed is None:
            speed = self.extruder_retraction_speed

        pulled = 0
        # self.gcode.run_script_from_command("M83")
        while self._get_switch_state('toolhead_sensor') and pulled < max_pull_mm:
            step = min(step_mm, max_pull_mm - pulled)
            self._extruder_move(-step, speed)  # negative = retract
            pulled += step
            # give the sensor a moment
            self.dwell(0.02)

        self.gcode.respond_info(f"ADBG[{self.instance_number}]: toolhead pullback completed, pulled={pulled}mm, sensor_present={self._get_switch_state('toolhead_sensor')}")
        return pulled
        
    def cmd_ACE_CHANGE_TOOL(self, gcmd, purgelength, purgespeed):
        if not get_ace_global_enabled(self.printer):
            self.gcode.respond_info("ACE: Disabled globally, skipping tool change/feed.")
            return
        try:
            self.gcode.respond_info("TOOL: self.standard_filament_runout_detection=" + str(self.standard_filament_runout_detection))
            global_tool_index = gcmd.get_int('TOOL')
            was = self.variables.get('ace_current_index', -1)

            available_slots = AcePro.get_all_available_slots()
            if global_tool_index < -1 or global_tool_index not in available_slots:
                raise gcmd.error(f'Wrong tool: {global_tool_index}, available: {available_slots}')

            self.set_runout_detection_active(False)
            self.gcode.run_script_from_command(
                f"SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE={global_tool_index}")

            # For loading&purging purposes, set the target temp to the one stored in inventory    
            self.update_macro_temperature_variables(global_tool_index, was)

            # --- FIX: Convert to local slot index before calling per-instance methods ---
            local_slot = global_tool_index - self.tool_offset
            if 0 <= local_slot < self.SLOT_COUNT:
                self._disable_feed_assist(local_slot)
            else:
                self.gcode.respond_info(f"ACE[{self.instance_number}]: Tool {global_tool_index} not managed by this ACE instance.")

            if global_tool_index != -1:
                self.check_ace_spool_status(global_tool_index)

            if was == global_tool_index:
                self.gcode.run_script_from_command('_ACE_PRE_TOOLCHANGE FROM=' + str(was) + ' TO=' + str(global_tool_index))
                self.handle_reactivating_active_tool(gcmd, global_tool_index)                   
                self.set_runout_detection_active(True)
                self.update_macro_temperature_variables(global_tool_index, global_tool_index)
                return

            self.wait_ace_ready()

            if global_tool_index != -1:
                self.check_ace_spool_status(global_tool_index)

            self.gcode.run_script_from_command('_ACE_PRE_TOOLCHANGE FROM=' + str(was) + ' TO=' + str(global_tool_index))
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: cmd_ACE_CHANGE_TOOL: ACE[{self.instance_number}]: Executing Toolchange {was} => {global_tool_index}")
            logging.info(f'ACE[{self.instance_number}]: Executing Toolchange {was} => {global_tool_index}')

            # --- Handle previous tool (was) on the correct ACE instance ---
            if was != -1:
                for ace in ACE_INSTANCES.values():
                    local_was = was - ace.tool_offset
                    if 0 <= local_was < ace.SLOT_COUNT:
                        ace.gcode.respond_info(f"ADBG[{ace.instance_number}]: ace= {str(ace)}")
                        ace._disable_feed_assist(local_was)
                        ace.wait_ace_ready()
                        _ace_fpos = ace.variables.get('ace_filament_pos', "spliter")
                        ace.gcode.respond_info(f"ADBG[{ace.instance_number}]: cmd_ACE_CHANGE_TOOL: _ace_fpos={_ace_fpos}")

                        if ace.variables.get('ace_filament_pos', "spliter") == "nozzle":
                            ace.gcode.run_script_from_command('CUT_TIP')
                            ace.variables['ace_filament_pos'] = "toolhead"
                            ace.wait_ace_ready()
                        if ace.variables.get('ace_filament_pos', "spliter") == "toolhead":
                            ace._sensor_bounded_toolhead_pullback(
                                max_pull_mm=abs(ace.extruder_retraction_length),
                                step_mm=5,
                                speed=ace.extruder_retraction_speed
                            )
                            ace._smart_unload_slot(local_was, ace.parkposition_to_toolhead_length, ace.parkposition_to_rms_sensor_length)
                            ace.wait_ace_ready()
                        break
                else:
                    self.gcode.respond_info(f"ACE[{self.instance_number}]: Previous tool {was} not managed by any ACE instance.")
            else:
                self.gcode.respond_info(f"ACE[{self.instance_number}]: No previous tool to unload")
                
            self.wait_ace_ready()
            self.variables['ace_filament_pos'] = "bowden"
            if global_tool_index != -1:
                self._feed_filament_into_toolhead(global_tool_index)
            else:
                self.variables['ace_filament_pos'] = "bowden"

            self.gcode.respond_info(f"ADBG[{self.instance_number}]: cmd_ACE_CHANGE_TOOL: 1st. reset_last_position")
            gcode_move = self.printer.lookup_object('gcode_move')
            gcode_move.reset_last_position()

            self.gcode.run_script_from_command('_ACE_POST_TOOLCHANGE FROM=' + str(was) + ' T=' + str(global_tool_index) + f' PURGELENGTH={purgelength} PURGESPEED={purgespeed}')
            self.variables['ace_current_index'] = global_tool_index

            self.gcode.respond_info(f"ADBG[{self.instance_number}]: cmd_ACE_CHANGE_TOOL: 2nd reset_last_position")
            gcode_move.reset_last_position()
            self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_current_index VALUE=' + str(global_tool_index))
            self.gcode.run_script_from_command(
                f"""SAVE_VARIABLE VARIABLE=ace_filament_pos VALUE='"{self.variables['ace_filament_pos']}"'""")

            if 0 <= local_slot < self.SLOT_COUNT:
                self._enable_feed_assist(local_slot)
            else:
                self.gcode.respond_info(f"ACE[{self.instance_number}]: Tool {global_tool_index} not managed by this ACE instance.")

            self.set_runout_detection_active(True)
            self.update_macro_temperature_variables(global_tool_index, global_tool_index)
            gcmd.respond_info(f"Tool {global_tool_index} load")

        except Exception as e:
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Exception occurred: {e}. Pausing print.")
            self.gcode.run_script_from_command('PAUSE')
            return
    
    def check_ace_spool_status(self, tool):
        local_slot = tool - self.tool_offset
        if local_slot < 0 or local_slot >= self.SLOT_COUNT:
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Tool {tool} not managed by this ACE instance.")
            return
        status = self._info['slots'][local_slot]['status']
        if status == 'empty':
            self.gcode.respond_info(f"ACE slot {tool} empty, load filament and RESUME.")
            self.gcode.respond_info(f"***** WARNING: ACE[{self.instance_number}]: Tool {tool} empty ***********")
        elif status != 'ready':
            self.gcode.respond_info(f"***** WARNING: ACE[{self.instance_number}]: Tool {tool} status not ready: {status} ***********")

    def handle_reactivating_active_tool(self, gcmd, tool):
        _ace_fpos = self.variables.get('ace_filament_pos', "spliter")
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: cmd_ACE_CHANGE_TOOL: _ace_fpos={_ace_fpos}")            
        gcmd.respond_info(f'ACE[{self.instance_number}]: Not changing tool, current index already {tool}')
        
        local_slot = tool - self.tool_offset
        if not (0 <= local_slot < self.SLOT_COUNT):
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Tool {tool} not managed by this ACE instance.")
            return

        if not self._get_switch_state('toolhead_sensor'):
            gcmd.respond_info(f'ACE[{self.instance_number}]: Tool:{tool} is active tool, but no filament present. Unplausible state, trying to feed filament into toolhead.')
            self._feed_filament_into_toolhead(tool, check_pre_condition=False) 
        else:
            gcmd.respond_info(f'ACE[{self.instance_number}]: Tool:{tool} is active tool, and filament present.')
            self._extruder_move(self.toolhead_nozzle_purge_length, self.toolhead_slow_loading_speed)
            self.gcode.run_script_from_command("G92 E0")
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: feeding to prime nozzle finished")
        
        self.wait_ace_ready()
        self._enable_feed_assist(local_slot)
        
    def update_macro_temperature_variables(self, tool, was):
        local_slot = tool - self.tool_offset
        if tool >= 0 and 0 <= local_slot < self.SLOT_COUNT:
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: inventory = {self.inventory[local_slot]}")
            heating_temperature = self.inventory[local_slot]['temp']
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: inventory heating_temperature = {heating_temperature}")
        else:
            heating_temperature = 0
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: No tool selected, setting heating_temperature = {heating_temperature}")
        self.gcode.run_script_from_command(
                f"SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=heating_temperature VALUE={heating_temperature}")
                
        local_was = was - self.tool_offset
        if was != -1 and 0 <= local_was < self.SLOT_COUNT:
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: inventory prev.spool = {self.inventory[local_was]}")
            heating_temperature_was = self.inventory[local_was]['temp']
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: inventory prev_tool_heating_temperature = {heating_temperature_was}")
        else:
            heating_temperature_was = 0
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: No prev tool was selected, setting prev_tool_heating_temperature = {heating_temperature_was}")
        self.gcode.run_script_from_command(
                f"SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=prev_tool_heating_temperature VALUE={heating_temperature_was}")

    def find_next_available_global_slot(self, current_slot):
        """
        Find the next available slot with filament across all ACE instances.
        Prefer same color & filament type, then same type, then any ready spool.
        Returns the global tool index, or -1 if none found.
        """
        # Get current slot's color and material
        current_color = None
        current_material = None
        for ace in ACE_INSTANCES.values():
            local_slot = current_slot - ace.tool_offset
            if 0 <= local_slot < ace.SLOT_COUNT:
                inv = ace.inventory[local_slot]
                current_color = tuple(inv.get("color", [None, None, None]))
                current_material = inv.get("material", "")
                break

        # Build a sorted list of all (global_tool_index, ace_instance, local_slot_index)
        all_slots = []
        for ace in sorted(ACE_INSTANCES.values(), key=lambda a: a.instance_number):
            for local_slot in range(ace.SLOT_COUNT):
                global_tool_index = ace.tool_offset + local_slot
                inv = ace.inventory[local_slot]
                info_status = ace._info['slots'][local_slot]['status']
                all_slots.append((global_tool_index, ace, local_slot, inv, info_status))

        # Find the position of current_slot in the global list
        try:
            current_pos = next(i for i, (gidx, _, _, _, _) in enumerate(all_slots) if gidx == current_slot)
        except StopIteration:
            current_pos = -1

        n = len(all_slots)
        # 1. Prefer same color & material
        for i in range(1, n):
            idx = (current_pos + i) % n
            global_tool_index, ace, local_slot, inv, info_status = all_slots[idx]
            if (inv["status"] == "ready" and info_status == "ready" and
                inv.get("material", "") and current_material and
                inv.get("material", "").lower() == current_material.lower() and
                tuple(inv.get("color", [None, None, None])) == current_color):
                return global_tool_index
        # 2. Next, same material (type), any color
        for i in range(1, n):
            idx = (current_pos + i) % n
            global_tool_index, ace, local_slot, inv, info_status = all_slots[idx]
            if (inv["status"] == "ready" and info_status == "ready" and
                inv.get("material", "") and current_material and
                inv.get("material", "").lower() == current_material.lower()):
                return global_tool_index
        # 3. Finally, any ready spool
        for i in range(1, n):
            idx = (current_pos + i) % n
            global_tool_index, ace, local_slot, inv, info_status = all_slots[idx]
            if inv["status"] == "ready" and info_status == "ready":
                return global_tool_index
        return -1  # No available slots

    # def _execute_endless_spool_change(self):
    #     """Execute the endless spool toolchange - simplified for return module sensor only"""
    #     if self.endless_spool_in_progress:
    #         return

    #     current_tool = self.variables.get('ace_current_index', -1)
    #     next_tool = self._find_next_available_slot(current_tool)
        
    #     if next_tool == -1:
    #         self.gcode.respond_info("ACE[{self.instance_number}]: No available slots for endless spool, pausing print")
    #         self.gcode.run_script_from_command('PAUSE')
    #         self.endless_spool_runout_detected = False
    #         return

    #     self.endless_spool_in_progress = True
    #     self.endless_spool_runout_detected = False
        
    #     self.gcode.respond_info(f"ACE[{self.instance_number}]: Endless spool changing from slot {current_tool} to slot {next_tool}")

    #     # Mark current slot as empty in inventory
    #     if current_tool >= 0:
    #         local_slot = current_tool - self.tool_offset
    #         if 0 <= local_slot < self.SLOT_COUNT:
    #             self.inventory[local_slot] = {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0}
    #             self._save_inventory()
    #     try:
    #         # Step 1: Disable feed assist on empty slot
    #         for ace in ACE_INSTANCES.values():
    #             local_slot = current_tool - ace.tool_offset
    #             if 0 <= local_slot < ace.SLOT_COUNT:
    #                 ace._disable_feed_assist(local_slot)
    #                 ace.wait_ace_ready()
    #                 break
    #         # Step 2: Feed filament from next slot until it reaches return module sensor
    #         # Feed filament from new slot until return module sensor triggers
            
    #         self._feed(next_tool, self.toolchange_load_length, self.retract_speed)
    #         self.wait_ace_ready()
    #         # Wait for filament to reach return module sensor

    #         #TODO: Add timeout
    #         while not self._get_switch_state("return_module"):
    #             self.dwell(delay=0.1)

    #         if not self._get_switch_state("return_module"):
    #             raise ValueError("Filament stuck during endless spool change")
    #         # Step 3: Enable feed assist for new slot
    #         self._enable_feed_assist(next_tool)
    #         # Step 4: Update current index and save state

    #         self.variables['ace_current_index'] = next_tool
    #         self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_current_index VALUE=' + str(next_tool))
            
    #         self.endless_spool_in_progress = False
            
    #         self.gcode.respond_info(f"ACE[{self.instance_number}]: Endless spool completed, now using slot {next_tool}")
            
    #     except Exception as e:
    #         self.gcode.respond_info(f"ACE[{self.instance_number}]: Endless spool change failed: {str(e)}")
    #         self.gcode.run_script_from_command('PAUSE')
    #         self.endless_spool_in_progress = False

    # cmd_ACE_ENABLE_ENDLESS_SPOOL_help = 'Enable endless spool feature'

    # def cmd_ACE_ENABLE_ENDLESS_SPOOL(self, gcmd):
    #     self.endless_spool_enabled = True
        
    #     # Save to persistent variables

    #     self.variables['ace_endless_spool_enabled'] = True
    #     self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_endless_spool_enabled VALUE=True')
        
    #     gcmd.respond_info("ACE[{self.instance_number}]: Endless spool enabled (immediate switching on runout, saved to persistent variables)")

    # cmd_ACE_DISABLE_ENDLESS_SPOOL_help = 'Disable endless spool feature'

    # def cmd_ACE_DISABLE_ENDLESS_SPOOL(self, gcmd):
    #     self.endless_spool_enabled = False
    #     self.endless_spool_runout_detected = False
    #     self.endless_spool_in_progress = False
        
    #     self.variables['ace_endless_spool_enabled'] = False
    #     self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_endless_spool_enabled VALUE=False')
        
    #     gcmd.respond_info("ACE[{self.instance_number}]: Endless spool disabled (saved to persistent variables)")

    # cmd_ACE_ENDLESS_SPOOL_STATUS_help = 'Show endless spool status'

    # def cmd_ACE_ENDLESS_SPOOL_STATUS(self, gcmd):
    #     status = self.get_status()['endless_spool']
    #     saved_enabled = self.variables.get('ace_endless_spool_enabled', False)
        
    #     gcmd.respond_info(f"ACE[{self.instance_number}]: Endless spool status:")
    #     gcmd.respond_info(f"  - Currently enabled: {status['enabled']}")
    #     gcmd.respond_info(f"  - Saved enabled: {saved_enabled}")
    #     gcmd.respond_info(f"  - Mode: Immediate switching on runout detection")
        
    #     if status['enabled']:
    #         gcmd.respond_info(f"  - Runout detected: {status['runout_detected']}")
    #         gcmd.respond_info(f"  - In progress: {status['in_progress']}")

    def find_com_port(self, device_name, instance=0):
        """
        Find the serial port for the given device_name and instance index,
        sorted by USB bus and device number (topology order).
        """
        matches = []
        for portinfo in serial.tools.list_ports.comports():
            if device_name in portinfo.description:
                # Example hwid: 'USB VID:PID=28E9:018A SER=1 LOCATION=1-1.4.4.3:1.0'
                # Extract LOCATION if available, else use device number
                location = None
                m = re.search(r'LOCATION=([-\w\.]+)', portinfo.hwid)
                if m:
                    location = m.group(1)
                else:
                    # Fallback: try to extract device number from /dev/ttyACM*
                    m2 = re.search(r'ACM(\d+)', portinfo.device)
                    location = m2.group(1) if m2 else portinfo.device
                matches.append((location, portinfo.device))
        # Sort by location string (USB topology order)
        matches.sort()
        if len(matches) > instance:
            return matches[instance][1]
        return None

    def cmd_ACE_DEBUG(self, gcmd):
        method = gcmd.get('METHOD')
        params = gcmd.get('PARAMS', '{}')

        try:
            def callback(response):
                self.gcode.respond_info(str(response))

            self.send_request(request = {"method": method, "params": json.loads(params)}, callback = callback)
        except Exception as e:
            self.gcode.respond_info('Error: ' + str(e))


    def get_status(self, eventtime=None):
        status = self._info.copy()
        # status['endless_spool'] = {
        #     'enabled': self.endless_spool_enabled,
        #     'runout_detected': self.endless_spool_runout_detected,
        #     'in_progress': self.endless_spool_in_progress,
        #     'standard_filament_runout_detection_enabled': self.standard_filament_runout_detection,
        # }
        return status

    def cmd_ACE_SET_SLOT(self, gcmd):
        idx = gcmd.get_int('INDEX')
        if idx < 0 or idx >= self.SLOT_COUNT:
            raise gcmd.error('Invalid slot index')
        if gcmd.get_int('EMPTY', 0):
            self.inventory[idx] = {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0}
            self._save_inventory()
            gcmd.respond_info(f"Slot {idx} set to empty")
            return
        color_str = gcmd.get('COLOR', None)
        material = gcmd.get('MATERIAL', "")
        temp = gcmd.get_int('TEMP', 0)
        if not color_str or not material or temp <= 0:
            raise gcmd.error('COLOR, MATERIAL, and TEMP must be set unless EMPTY=1')
        color = [int(x) for x in color_str.split(',')]
        if len(color) != 3:
            raise gcmd.error('COLOR must be R,G,B')
        self.inventory[idx] = {
            "status": "ready",
            "color": color,
            "material": material,
            "temp": temp
        }
        self._save_inventory()
        gcmd.respond_info(f"Slot {idx} set: color={color}, material={material}, temp={temp}")

    def cmd_ACE_SAVE_INVENTORY(self, gcmd):
        try:
            self._save_inventory()
            gcmd.respond_info(f"ACE[{self.instance_number}]: Inventory saved to persistent storage")
        except Exception as e:
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Exception in save inventory: {e}")

    cmd_ACE_TEST_RUNOUT_SENSOR_help = 'Test and display runout sensor states'

    def cmd_ACE_TEST_RUNOUT_SENSOR(self, gcmd):
        try:
            endstop_triggered = self._get_switch_state('return_module')
            
            gcmd.respond_info(f"ACE[{self.instance_number}]: Return module sensor states:")
            gcmd.respond_info(f"  - Endstop triggered: {endstop_triggered}")
            # gcmd.respond_info(f"  - Endless spool enabled: {self.endless_spool_enabled}")
            gcmd.respond_info(f"  - Current tool: {self.variables.get('ace_current_index', -1)}")
            # gcmd.respond_info(f"  - Runout detected: {self.endless_spool_runout_detected}")
            # Test runout detection logic
            
            would_trigger = not not endstop_triggered
            gcmd.respond_info(f"  - Would trigger runout: {would_trigger}")
        except Exception as e:
            gcmd.respond_info(f"ACE[{self.instance_number}]: Error testing sensor: {str(e)}")

    cmd_ACE_GET_CURRENT_INDEX_help = 'Get the currently loaded slot index'

    def cmd_ACE_GET_CURRENT_INDEX(self, gcmd):
        try:
            current_index = self.variables.get('ace_current_index', -1)
            gcmd.respond_info(str(current_index))
        except Exception as e:
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Exception in gettinx index: {e}")

    cmd_ACE_SMART_UNLOAD_help = 'Check if filament sensors are triggered and retracts all slots until sensors are free; if not triggered, it retracts only to safe distance before return'
    
    def cmd_ACE_SMART_UNLOAD(self, gcmd):
        try:
            # Prefer TOOL parameter if given, else use ace_current_index from global variables
            if 'TOOL' in gcmd.get_command_parameters():
                current_tool = gcmd.get_int('TOOL')
            else:
                current_tool = self.variables.get('ace_current_index', -1)
            if current_tool == -1:
                current_tool = 0
            # Find the ACE instance that manages current_tool
            for aceIdx, ace in enumerate(ACE_INSTANCES.values()):
                local_slot = current_tool - ace.tool_offset
                if 0 <= local_slot < ace.SLOT_COUNT:
                    ace.smart_unload(local_slot)
                    return
            self.gcode.respond_info(f"ACE: No ACE instance manages tool {current_tool}")
        except Exception as e:
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Exception in smart_unload: {e} current_tool={current_tool} aceIdx={aceIdx} local_slot={local_slot}")
            
    def cmd_ACE_SMART_LOAD(self, gcmd):
        try:
            for ace in ACE_INSTANCES.values():
                ace.smart_load()
        except Exception as e:
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Exception in smart_load: {e}")
        
    def cmd_ACE_QUERY_SLOTS(self, gcmd):
        import json
        for ace in ACE_INSTANCES.values():
            gcmd.respond_info(f"ACE[{ace.instance_number}]: {json.dumps(ace.inventory)}")

    cmd_ACE_SAVE_INVENTORY_help = 'Manually save current inventory to persistent storage'

    def cmd_ACE_SET_ENDLESS_SPOOL_SCOPE(self, gcmd):
        scope = gcmd.get('SCOPE', 'all')
        if scope not in ("all", "local"):
            raise gcmd.error("SCOPE must be 'all' or 'local'")
        self.endless_spool_scope = scope
        gcmd.respond_info(f"ACE[{self.instance_number}]: Endless spool scope set to {scope}")
                
    def _feed_filament_into_rms(self, tool):
        self.wait_ace_ready()

        # Validate sensors are not reporting any filament detected
        if self._get_switch_state("return_module"):
            raise ValueError("_feed_filament_into_rms: Cant feed, still filament stuck in return module? rms=" + str(bool(sensor_return_module.runout_helper.filament_present)))
        if self._get_switch_state('toolhead_sensor'):
            raise ValueError("Cant feed, there should be no filament in the nozzle yet.")
        self.variables['ace_filament_pos'] = "bowden"

        # Use a flag to detect feed completion via status message
        self._feed(tool, self.toolchange_load_length, self.feed_speed)

        # Wait for either endstop or feed completion
        expected_time = self.toolchange_load_length / self.feed_speed
        timeout_s = expected_time * self.timeout_multiplier       

        start_time = time.monotonic()
        while not self._get_switch_state("return_module"):
            if time.monotonic() - start_time > timeout_s:
                self.gcode.respond_info(f"ACE[{self.instance_number}]: Feed timeout after {timeout_s} seconds!")
                break
            self.dwell(delay=0.01)
            
        self._stop_feed(tool)

        if not self._get_switch_state("return_module"):
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: Feed ended, but no filament reached rms?")
            accumulated_feed_length = self.toolchange_load_length
            while not self._get_switch_state('return_module') and (accumulated_feed_length < self.total_max_feeding_length):
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: _feed_filament_into_rms() _feed({self.incremental_feeding_length},{self.incremental_feeding_speed})")
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: _feed_filament_into_rms() _extruder_move({self.extruder_feeding_length}, {self.extruder_feeding_speed})")
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: accumulated_feed_length={accumulated_feed_length}, {self.total_max_feeding_length})")
                self._feed(tool,self.incremental_feeding_length,self.incremental_feeding_speed)
                accumulated_feed_length = accumulated_feed_length + self.incremental_feeding_length
                self.dwell(delay=(self.incremental_feeding_length / self.incremental_feeding_speed) + 0.1)
                
            if not self._get_switch_state('return_module'):
                    self.gcode.respond_info(f"ADBG[{self.instance_number}]: accumulated_feed_length={accumulated_feed_length}, {self.total_max_feeding_length})")                
                    raise ValueError(f"ADBG[{self.instance_number}]: feeded {accumulated_feed_length}, but rms sensor not triggered!")

        self.gcode.respond_info(f"ADBG[{self.instance_number}]: _feed_filament_into_rms() Stopped feeding")        
        
        self.wait_ace_ready()
        self.variables['ace_filament_pos'] = "spliter"
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: _feed_filament_into_rms: _ace_fpos set to:{self.variables['ace_filament_pos']}")

    @runout_detection_disabled
    def smart_load(self):
        if not self._is_filament_path_free():
            self.gcode.respond_info("ADBG[{self.instance_number}]: Filament path is not free, please try SMART_UNLOAD before.")
            return

        for slot in range(self.SLOT_COUNT):
            if self._info['slots'][slot]['status'] != 'empty':
                self._feed_filament_into_rms(slot)
                self.wait_ace_ready()
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: Trying to unload slot {slot} to park position.")
                self._retract(slot, self.parkposition_to_rms_sensor_length, self.retract_speed)
                self.wait_ace_ready()
                if not self._is_filament_path_free():
                    raise ValueError(f"ADBG[{self.instance_number}]: Filament path is not free after retraction?")
                
        self.variables['ace_filament_pos'] = "bowden"
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: _feed_filament_into_rms: _ace_fpos set to:{self.variables['ace_filament_pos']}")
        

        if self._is_filament_path_free():
            self.gcode.respond_info("ADBG[{self.instance_number}]: Filament path is free after smart load finished")
        else:
            self.gcode.respond_info("ACE[{self.instance_number}]: WARNING - Filament path is still not free after trying all slots!")


    def _is_filament_path_free(self):
        return not (
            self._get_switch_state('toolhead_sensor') or self._get_switch_state('return_module')
        )
    
    @runout_detection_disabled
    def _smart_unload_slot(self, slot, length, overshoot_length=0):
        self.wait_ace_ready()
        global_tool_index = self.tool_offset + slot
        # Check if slot is empty before trying to retract
        if self._info['slots'][slot]['status'] == 'empty':
            self.gcode.respond_info(f"ACE[{self.instance_number}]: Slot {slot} (Tool {global_tool_index}) is empty, skipping retraction.")
            return

        self.gcode.respond_info(f"ADBG[{self.instance_number}]: _smart_unload_slot({slot} / Tool {global_tool_index} overshoot_length={overshoot_length})")

        self._retract(slot, length+overshoot_length, self.retract_speed)

        expected_time = (length+overshoot_length) / self.retract_speed
        timeout_s = expected_time * self.timeout_multiplier

        start_time = time.monotonic()
        elapsed = 0
        while not self._is_filament_path_free():
            elapsed = time.monotonic() - start_time
            if elapsed > timeout_s:
                self.gcode.respond_info(f"ACE[{self.instance_number}]: Retract timeout after {timeout_s} seconds!")
                break
            
            if self._info['slots'][slot]['status'] == 'empty':
                self.gcode.respond_info(f"ACE slot {slot} empty, stopping retract")
                self._stop_retract(slot)
                break
            
            self.dwell(delay=0.02)
        
        if self._is_filament_path_free():
            expected_oveshoot_retract_time = (overshoot_length) / self.retract_speed
            self.dwell(delay=expected_oveshoot_retract_time*self.timeout_multiplier)
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: Filament path is free after {elapsed:.2f}s, expected overshoot retract time was {expected_oveshoot_retract_time:.2f}s")
        else:
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: Filament path is still not free after {elapsed:.2f}s, expected retract time was {expected_time:.2f}s")
            
        self._stop_retract(slot)
        self.wait_ace_ready()

        # Final check, if path is still not free try to pull a little bit more out of the return module
        if not self._is_filament_path_free():
            self.gcode.respond_info(f"ACE[{self.instance_number}]: WARNING - Filament path is still not free after retracting slot {slot} by {length+overshoot_length}mm!")
            if overshoot_length > 0 and self._info['slots'][slot]['status'] != 'empty':
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: Trying overshoot retraction of {overshoot_length:.2f}mm")
                self._retract(slot, overshoot_length, self.retract_speed)
                self.dwell(delay=(overshoot_length / self.retract_speed) + 0.1)
                self.wait_ace_ready()
            else:
                self.gcode.respond_info(
                    f"ADBG[{self.instance_number}]: Not trying overshoot length retraction (overshoot_length={overshoot_length})"
                )
        #self._stop_retract(slot)
        self.gcode.respond_info(
            f"ADBG[{self.instance_number}]: path_free={self._is_filament_path_free()}"
        )


    def _try_unload_other_slots(self, tool, length):
        # Try all other slots if path is still not free
        for slot in range(self.SLOT_COUNT):
            if slot == tool:
                continue
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: Trying to unload slot {slot}...")
            self._smart_unload_slot(slot, length, self.parkposition_to_rms_sensor_length)
            self.wait_ace_ready()
            if self._is_filament_path_free():
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: Filament path is free after unloading slot {slot}.")
                break
    
    def prepare_toolhead_for_filament_retraction(self,min_extrude_temp=200):
        wasHeatingNeeded = False
        # 1. Check toolhead filament sensor (replace with your sensor logic)
        filament_present = self._get_switch_state('toolhead_sensor') 
        if filament_present:
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: Filament in toolhead detected, heating up to allow cut&retraction.")
            # 2. Heat hotend if needed
            extruder = self.printer.lookup_object("extruder")
            eventtime = self.reactor.monotonic()
            status = extruder.get_status(eventtime)
            if status.get('temperature', 0) < min_extrude_temp:
                self.gcode.run_script_from_command(f"M109 S{min_extrude_temp}")
                wasHeatingNeeded = True
       
            # 3. Cut tip
            self.gcode.run_script_from_command("CUT_TIP")
    
            # 4. Retract by extruder_retraction_length at extruder_retraction_speed
            self.gcode.run_script_from_command("M83")  # Relative mode
            self.gcode.run_script_from_command(f"G1 E{self.extruder_retraction_length} F{self.extruder_retraction_speed * 60}")
            self.gcode.run_script_from_command("M82")  # Absolute mode
    
        else:
            self.gcode.respond_info("No filament detected at toolhead, skipping unload steps.")    
        return wasHeatingNeeded
    
    @runout_detection_disabled
    def smart_unload(self, tool):
        """
        Try to unload filament from the current slot first.
        If the path is not free, try all other slots until the path is free.
        """
        
        was = self.variables.get('ace_current_index', -1)

        if was != -1:
            # Find the ACE instance and local slot for the global index 'was'
            for ace in ACE_INSTANCES.values():
                local_was = was - ace.tool_offset
                if 0 <= local_was < ace.SLOT_COUNT:
                    ace.gcode.respond_info(f"ADBG[{ace.instance_number}]: inventory prev.spool = {ace.inventory[local_was]}")
                    heating_temperature_was = ace.inventory[local_was]['temp']
                    if heating_temperature_was <= 150:
                        heating_temperature_was = 250
                        ace.gcode.respond_info(f"ADBG[{ace.instance_number}]: inventory prev_tool_heating_temperature too low, setting to {heating_temperature_was}")
                    ace.gcode.respond_info(f"ADBG[{ace.instance_number}]: inventory prev_tool_heating_temperature = {heating_temperature_was}")
                    break
            else:
                heating_temperature_was = 250
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: No ACE instance manages tool {was}, setting prev_tool_heating_temperature = {heating_temperature_was}")
        else:
            heating_temperature_was = 250
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: No prev tool was selected, setting prev_tool_heating_temperature = {heating_temperature_was}")
            
        wasHeatingNeeded = self.prepare_toolhead_for_filament_retraction(heating_temperature_was)
        
        if self._is_filament_path_free():
            self.gcode.respond_info("ADBG[{self.instance_number}]: Filament path is free, retracting only all slots a bit")
            for ace in ACE_INSTANCES.values():            
                for slot in range(self.SLOT_COUNT):
                    status = ace._info['slots'][slot]['status']
                    if status != 'empty':
                        ace.gcode.respond_info(f"ADBG[{ace.instance_number}]: Trying to unload slot {slot}...")
                        ace._retract(slot, self.parkposition_to_rms_sensor_length, self.retract_speed)
                        ace.wait_ace_ready()
                    else:
                        ace.gcode.respond_info(f"ADBG[{ace.instance_number}]: Slot {slot} empty, skipping.")
            return
        
        self._smart_unload_slot(tool,self.parkposition_to_toolhead_length,  self.parkposition_to_rms_sensor_length)
        self.wait_ace_ready()

        if self._is_filament_path_free():
            self.gcode.respond_info("ADBG[{self.instance_number}]: Filament path is free after unloading current slot.")
            if wasHeatingNeeded:
                self.gcode.run_script_from_command("M104 S0") # turn off hotend if we heated it
            return

        # If toolhead sensor triggered, retract the full distance from toolhead to filament parkposition
        if self._get_switch_state('toolhead_sensor'):
            self.gcode.respond_info("ADBG[{self.instance_number}]: Toolhead sensor still triggered, trying full retract to parkposition.")
            self._try_unload_other_slots(tool, self.parkposition_to_toolhead_length)
        elif self._get_switch_state('return_module'):
            self.gcode.respond_info("ADBG[{self.instance_number}]: RMS sensor still triggered, trying short retract to safe distance.")
            # If only rms is triggered, try first short distance retraction to safe time
            self._try_unload_other_slots(tool,self.parkposition_to_rms_sensor_length)
            # check if that was enough. if not retract the full length.
            if not self._is_filament_path_free():
                self.gcode.respond_info("ACE[{self.instance_number}]: WARNING - Filament sensor still triggered after rms retraction.Retry will parkposition length)")
                self._try_unload_other_slots(tool, self.parkposition_to_toolhead_length-self.parkposition_to_rms_sensor_length)

        if not self._is_filament_path_free():
            self.gcode.respond_info("ACE[{self.instance_number}]: WARNING - Filament path is still not free after trying all slots!")
            
        if wasHeatingNeeded:
            self.gcode.run_script_from_command("M104 S0") # turn off hotend if we heated it

    def _clear_queue(self, q):
        """Remove all items from a queue."""
        if q is None:
            return
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass

    def _status_update_callback(self, response):
        # Check for a valid status message
        if response and 'result' in response:
            # Update self._info
            with self._lock:
                self._info = response['result']

                # Update self.inventory to match slot status
                slots = self._info.get('slots', [])
                for slot in slots:
                    idx = slot.get('index')
                    if idx is not None and 0 <= idx < self.SLOT_COUNT:
                        self.inventory[idx]['status'] = slot.get('status', 'empty')
                        # self.inventory[idx]['color'] = slot.get('color', [0, 0, 0])
                        # self.inventory[idx]['material'] = slot.get('type', '')
                        # self.inventory[idx]['temp'] = slot.get('temp', 0)

    def _handle_print_end(self):
        self.gcode.respond_info(f"ACE[{self.instance_number}]: _handle_print_end)")
        was = self.variables.get('ace_current_index', -1)
        if was != -1:
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: _handle_print_end: Active tool:{was}")
            local_was = was - self.tool_offset
            if self.variables.get('ace_filament_pos', "spliter") == "nozzle":
                self.gcode.run_script_from_command('CUT_TIP')
                self.variables['ace_filament_pos'] = "toolhead"
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: _handle_print_end(nozzle pos):_ace_fpos set to:{self.variables['ace_filament_pos']}")
            if self.variables.get('ace_filament_pos', "spliter") == "toolhead":
                self._sensor_bounded_toolhead_pullback(
                    max_pull_mm=abs(self.extruder_retraction_length),
                    step_mm=5,
                    speed=self.extruder_retraction_speed
                )
                if 0 <= local_was < self.SLOT_COUNT:
                    self._smart_unload_slot(local_was, self.parkposition_to_toolhead_length, self.parkposition_to_rms_sensor_length)
                else:
                    self.gcode.respond_info(f"ACE[{self.instance_number}]: _handle_print_end: Tool {was} not managed by this ACE instance.")
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: _handle_print_end(rms pos):_ace_fpos set to:{self.variables['ace_filament_pos']}")
                self.wait_ace_ready()
                self.variables['ace_filament_pos'] = "bowden"
                self.gcode.respond_info(f"ADBG[{self.instance_number}]: _handle_print_end: _ace_fpos set to:{self.variables['ace_filament_pos']}")
        else:
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: _handle_print_end: No tool loaded")
        self.set_runout_detection_active(False)
        self.toolchange_purge_length = self.default_color_change_purge_length
        self.toolchange_purge_speed = self.default_color_change_purge_speed
        # --- Clear feed assist index on print end ---
        self._feed_assist_index = -1
        self.variables[f'ace_feed_assist_index_{self.instance_number}'] = -1
        self.gcode.run_script_from_command(
            f"SAVE_VARIABLE VARIABLE=ace_feed_assist_index_{self.instance_number} VALUE=-1"
        )

            
    def _ACE_HANDLE_PRINT_END(self, gcmd):
        self.gcode.respond_info(f"ADBG[{self.instance_number}]: _ACE_HANDLE_PRINT_END")
        tool = self.variables.get('ace_current_index', -1)
        local_tool = tool - self.tool_offset
        if tool >= 0 and 0 <= local_tool < self.SLOT_COUNT:
            self.gcode.respond_info(f"ADBG[{self.instance_number}]: _disable_feed_assist for tool:{tool}")
            self._disable_feed_assist(tool)
        self.set_runout_detection_active(False)

        
def load_config(config):
    ace = AcePro(config)
    register_commands(ace)
    return ace

def load_config_prefix(config):
    ace = AcePro(config)
    register_commands(ace)
    return ace

def register_commands(ace):
    gcode = ace.gcode
    if not hasattr(gcode, "_ace_commands_registered"):
        gcode.register_command(
            "_ACE_HANDLE_PRINT_END", lambda gcmd: ace_get_instance(gcmd)._ACE_HANDLE_PRINT_END(gcmd),
            desc="(private)Call ACE print end handler. INSTANCE="
        )

        gcode.register_command(
            "ACE_DEBUG", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_DEBUG(gcmd),
            desc="Debug ACE instance. INSTANCE= METHOD= PARAMS="
        )
        gcode.register_command(
            "ACE_START_DRYING", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_START_DRYING(gcmd),
            desc="Start ACE dryer. INSTANCE= TEMP= DURATION="
        )
        gcode.register_command(
            "ACE_STOP_DRYING", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_STOP_DRYING(gcmd),
            desc="Stop ACE dryer. INSTANCE="
        )
        gcode.register_command(
            "ACE_ENABLE_FEED_ASSIST", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_ENABLE_FEED_ASSIST(gcmd),
            desc="Enable feed assist. INSTANCE= INDEX="
        )
        gcode.register_command(
            "ACE_DISABLE_FEED_ASSIST", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_DISABLE_FEED_ASSIST(gcmd),
            desc="Disable feed assist. INSTANCE= INDEX="
        )
        gcode.register_command(
            "ACE_FEED", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_FEED(gcmd),
            desc="Feed filament. INSTANCE= INDEX= LENGTH= [SPEED=]"
        )
        gcode.register_command(
            "ACE_STOP_FEED", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_STOP_FEED(gcmd),
            desc="Stop feeding filament. INSTANCE= INDEX="
        )
        gcode.register_command(
            "ACE_RETRACT", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_RETRACT(gcmd),
            desc="Retract filament. INSTANCE= INDEX= LENGTH= [SPEED=]"
        )
        gcode.register_command(
            "ACE_STOP_RETRACT", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_STOP_RETRACT(gcmd),
            desc="Stop retraction. INSTANCE= INDEX="
        )
        # gcode.register_command(
        #     "ACE_CHANGE_TOOL", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_CHANGE_TOOL(gcmd),
        #     desc="Change tool. INSTANCE= TOOL="
        #)
        # gcode.register_command(
        #     "ACE_ENABLE_ENDLESS_SPOOL", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_ENABLE_ENDLESS_SPOOL(gcmd),
        #     desc="Enable endless spool. INSTANCE="
        # )
        # gcode.register_command(
        #     "ACE_DISABLE_ENDLESS_SPOOL", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_DISABLE_ENDLESS_SPOOL(gcmd),
        #     desc="Disable endless spool. INSTANCE="
        # )
        # gcode.register_command(
        #     "ACE_ENDLESS_SPOOL_STATUS", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_ENDLESS_SPOOL_STATUS(gcmd),
        #     desc="Show endless spool status. INSTANCE="
        # )
        gcode.register_command(
            "ACE_SAVE_INVENTORY", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_SAVE_INVENTORY(gcmd),
            desc="Save inventory. INSTANCE="
        )
        gcode.register_command(
            "ACE_TEST_RUNOUT_SENSOR", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_TEST_RUNOUT_SENSOR(gcmd),
            desc="Test runout sensor. INSTANCE="
        )
        gcode.register_command(
            "ACE_GET_CURRENT_INDEX", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_GET_CURRENT_INDEX(gcmd),
            desc="Get current slot index. INSTANCE="
        )
        gcode.register_command(
            "ACE_RESET_PERSISTENT_INVENTORY", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_RESET_PERSISTENT_INVENTORY(gcmd),
            desc="Reset persistent inventory. INSTANCE="
        )
        gcode.register_command(
            "ACE_RESET_ACTIVE_TOOLHEAD", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_RESET_ACTIVE_TOOLHEAD(gcmd),
            desc="Reset active toolhead. INSTANCE="
        )
        gcode.register_command(
            "ACE_SMART_UNLOAD", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_SMART_UNLOAD(gcmd),
            desc="Smart unload. INSTANCE="
        )
        gcode.register_command(
            "ACE_SMART_LOAD", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_SMART_LOAD(gcmd),
            desc="Smart load. INSTANCE="
        )
        gcode.register_command(
            "ACE_RECONNECT", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_RECONNECT(gcmd),
            desc="Reconnect ACE. INSTANCE="
        )
        gcode.register_command(
            "ACE_GET_STATUS", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_GET_STATUS(gcmd),
            desc="Get ACE status. INSTANCE="
        )        
        
        gcode.register_command(
            "ACE_SET_SLOT", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_SET_SLOT(gcmd),
            desc="Set slot inventory for ACE instance: INSTANCE= INDEX= COLOR= MATERIAL= TEMP= | Set status to empty with EMPTY=1"
        )
        gcode.register_command(
            "ACE_QUERY_SLOTS", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_QUERY_SLOTS(gcmd),
            desc="Query all slot inventory as JSON for ACE instance: INSTANCE="
        )        

        gcode.register_command(
            "ACE_SET_PURGE_AMOUNT", lambda gcmd: ace_get_instance(gcmd).cmd_ACE_SET_PURGE_AMOUNT(gcmd),
            desc=f"ACE purge amount to use for next toolchange: PURGELENGTH=<float> PURGESPEED=<float>"
        )
                
        # gcode.register_command(
        #     "ACE_SET_ENDLESS_SPOOL_SCOPE",
        #     lambda gcmd: ace_get_instance(gcmd).cmd_ACE_SET_ENDLESS_SPOOL_SCOPE(gcmd),
        #     desc="Set endless spool search scope. INSTANCE= SCOPE=all|local"
        # )       
        
        gcode._ace_commands_registered = True
