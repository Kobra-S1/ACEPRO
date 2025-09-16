import serial, threading, time, logging, json, struct, queue, traceback, re
from serial import SerialException
import serial.tools.list_ports

class BunnyAce:
    def __init__(self, config):
        self._connected = False
        self._serial = None
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self._name = config.get_name()
        self.lock = False
        self.send_time = None
        self.read_buffer = bytearray()
        if self._name.startswith('ace '):
            self._name = self._name[4:]
        self.variables = self.printer.lookup_object('save_variables').allVariables

        self.serial_name = config.get('serial', '/dev/ttyACM0')
        self.baud = config.getint('baud', 115200)
        extruder_sensor_pin = config.get('extruder_sensor_pin', None)
        toolhead_sensor_pin = config.get('toolhead_sensor_pin', None)
        self.feed_speed = config.getint('feed_speed', 50)
        self.retract_speed = config.getint('retract_speed', 50)
        self.toolchange_retract_length = config.getint('toolchange_retract_length', 150)
        self.toolchange_load_length = config.getint('toolchange_load_length', 630)
        self.toolhead_sensor_to_nozzle_length = config.getint('toolhead_sensor_to_nozzle', 0)
        
        # How much filament to feed incremental after initial 'toolchange_load_length' feeding.
        # Incremental feeding happend until hotend filaemnt-runoutsensor triggers after a finished increment
        self.incremental_feeding_length = config.getint('incremental_feeding_length', 50)
        
        self.incremental_feeding_speed = config.getint('incremental_feeding_speed', 30) # Speed for above incremental feeding
        
        # While ACE feeds, extruder should grab the filament when it reaches the printhead,
        # hence it engages at every feeding cycle a little bit to assure that
        self.extruder_feeding_lenght = config.getint('extruder_feeding_lenght', 1) 
        self.extruder_feeding_speed = config.getint('extruder_feeding_speed', 5) 
        
        # Incremental length which currently loaded filament will be pulled back by ACE until it passes the return-module filament detection switch
        # Important is to make it so long that one pull more after triggering is retracting the fillament enough to be pulled out of the 4-in-1 splitter
        # to avoid collision with new feeded filament afterwards
        self.incremental_retraction_length = config.getint('incremental_retraction_length', 100) 
        self.incremental_retraction_speed = config.getint('incremental_retraction_speed', 50) 
        
        #How much to retract at the toolhead extruder, once at filament change, to pull out the cut filament out of extruder,
        # so ACE is able to rectract the loaded filament
        self.extruder_retraction_lenght = config.getint('extruder_retraction_lenght', -50) 
        self.extruder_retraction_speed = config.getint('extruder_retraction_speed', 10) 
        
        # self.extruder_to_blade_length = config.getint('extruder_to_blade', None)

        self.max_dryer_temperature = config.getint('max_dryer_temperature', 55)

        # Endless spool configuration - load from persistent variables if available
        saved_endless_spool_enabled = self.variables.get('ace_endless_spool_enabled', False)
        
        self.endless_spool_enabled = config.getboolean('endless_spool', saved_endless_spool_enabled)
        self.endless_spool_in_progress = False
        self.endless_spool_runout_detected = False

        self._callback_map = {}
        self.park_hit_count = 5
        self._feed_assist_index = -1
        self._request_id = 0
        self._last_assist_count = 0
        self._assist_hit_count = 0
        self._park_in_progress = False
        self._park_is_toolchange = False
        self._park_previous_tool = -1
        self._park_index = -1
        self.endstops = {}

        # Default data to prevent exceptions
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
                    'index': 0,
                    'status': 'empty',
                    'sku': '',
                    'type': '',
                    'color': [0, 0, 0]
                },
                {
                    'index': 1,
                    'status': 'empty',
                    'sku': '',
                    'type': '',
                    'color': [0, 0, 0]
                },
                {
                    'index': 2,
                    'status': 'empty',
                    'sku': '',
                    'type': '',
                    'color': [0, 0, 0]
                },
                {
                    'index': 3,
                    'status': 'empty',
                    'sku': '',
                    'type': '',
                    'color': [0, 0, 0]
                }
            ]
        }

        # Add inventory for 4 slots - load from persistent variables if available
        saved_inventory = self.variables.get('ace_inventory', None)
        if saved_inventory:
            self.inventory = saved_inventory
        else:
            self.inventory = [
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0} for _ in range(4)
            ]
        # Register inventory commands
        self.gcode.register_command(
            'ACE_SET_SLOT', self.cmd_ACE_SET_SLOT,
            desc="Set slot inventory: INDEX= COLOR= MATERIAL= TEMP= | Set status to empty with EMPTY=1"
        )
        self.gcode.register_command(
            'ACE_QUERY_SLOTS', self.cmd_ACE_QUERY_SLOTS,
            desc="Query all slot inventory as JSON"
        )

        self._create_mmu_sensor(config, extruder_sensor_pin, "extruder_sensor")
        self._create_mmu_sensor(config, toolhead_sensor_pin, "toolhead_sensor")
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:disconnect', self._handle_disconnect)
        self.gcode.register_command(
            'ACE_DEBUG', self.cmd_ACE_DEBUG,
            desc='self.cmd_ACE_DEBUG_help')
        self.gcode.register_command(
            'ACE_START_DRYING', self.cmd_ACE_START_DRYING,
            desc=self.cmd_ACE_START_DRYING_help)
        self.gcode.register_command(
            'ACE_STOP_DRYING', self.cmd_ACE_STOP_DRYING,
            desc=self.cmd_ACE_STOP_DRYING_help)
        self.gcode.register_command(
            'ACE_ENABLE_FEED_ASSIST', self.cmd_ACE_ENABLE_FEED_ASSIST,
            desc=self.cmd_ACE_ENABLE_FEED_ASSIST_help)
        self.gcode.register_command(
            'ACE_DISABLE_FEED_ASSIST', self.cmd_ACE_DISABLE_FEED_ASSIST,
            desc=self.cmd_ACE_DISABLE_FEED_ASSIST_help)
        self.gcode.register_command(
            'ACE_FEED', self.cmd_ACE_FEED,
            desc=self.cmd_ACE_FEED_help)
        self.gcode.register_command(
            'ACE_RETRACT', self.cmd_ACE_RETRACT,
            desc=self.cmd_ACE_RETRACT_help)
        self.gcode.register_command(
            'ACE_CHANGE_TOOL', self.cmd_ACE_CHANGE_TOOL,
            desc=self.cmd_ACE_CHANGE_TOOL_help)
        self.gcode.register_command(
            'ACE_ENABLE_ENDLESS_SPOOL', self.cmd_ACE_ENABLE_ENDLESS_SPOOL,
            desc=self.cmd_ACE_ENABLE_ENDLESS_SPOOL_help)
        self.gcode.register_command(
            'ACE_DISABLE_ENDLESS_SPOOL', self.cmd_ACE_DISABLE_ENDLESS_SPOOL,
            desc=self.cmd_ACE_DISABLE_ENDLESS_SPOOL_help)
        self.gcode.register_command(
            'ACE_ENDLESS_SPOOL_STATUS', self.cmd_ACE_ENDLESS_SPOOL_STATUS,
            desc=self.cmd_ACE_ENDLESS_SPOOL_STATUS_help)
        self.gcode.register_command(
            'ACE_SAVE_INVENTORY', self.cmd_ACE_SAVE_INVENTORY,
            desc=self.cmd_ACE_SAVE_INVENTORY_help)
        self.gcode.register_command(
            'ACE_TEST_RUNOUT_SENSOR', self.cmd_ACE_TEST_RUNOUT_SENSOR,
            desc=self.cmd_ACE_TEST_RUNOUT_SENSOR_help)
        self.gcode.register_command(
            'ACE_GET_CURRENT_INDEX', self.cmd_ACE_GET_CURRENT_INDEX,
            desc=self.cmd_ACE_GET_CURRENT_INDEX_help)


    def _calc_crc(self, buffer):
        _crc = 0xffff
        for byte in buffer:
            data = byte
            data ^= _crc & 0xff
            data ^= (data & 0x0f) << 4
            _crc = ((data << 8) | (_crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return _crc

    def _send_request(self, request):
        if not 'id' in request:
            request['id'] = self._request_id
            self._request_id += 1

        payload = json.dumps(request)
        payload = bytes(payload, 'utf-8')

        data = bytes([0xFF, 0xAA])
        data += struct.pack('@H', len(payload))
        data += payload
        data += struct.pack('@H', self._calc_crc(payload))
        data += bytes([0xFE])
        self._serial.write(data)


    def _reader(self, eventtime):

        if self.lock and (self.reactor.monotonic() - self.send_time) > 2:
            self.lock = False
            self.read_buffer = bytearray()
            self.gcode.respond_info(f"timeout {self.reactor.monotonic()}")

        buffer = bytearray()
        try:
            raw_bytes = self._serial.read(size=4096)
        except SerialException:
            self.gcode.respond_info("Unable to communicate with the ACE PRO" + traceback.format_exc())
            self.lock = False
            self.gcode.respond_info('Try reconnecting')
            self._serial_disconnect()
            self.connect_timer = self.reactor.register_timer(self._connect, self.reactor.NOW)
            return self.reactor.NEVER

        if len(raw_bytes):
            #self.gcode.respond_info(f'ADBG: _read: {raw_bytes}')
            
            text_buffer = self.read_buffer + raw_bytes
            i = text_buffer.find(b'\xfe')
            if i >= 0:
                buffer = text_buffer
                self.read_buffer = bytearray()
            else:
                self.read_buffer += raw_bytes
                return eventtime + 0.1

        else:
            return eventtime + 0.1

        if len(buffer) < 7:
            return eventtime + 0.1

        if buffer[0:2] != bytes([0xFF, 0xAA]):
            self.lock = False
            self.gcode.respond_info("Invalid data from ACE PRO (head bytes)")
            self.gcode.respond_info(str(buffer))
            return eventtime + 0.1

        payload_len = struct.unpack('<H', buffer[2:4])[0]
        logging.info(str(buffer))
        payload = buffer[4:4 + payload_len]

        crc_data = buffer[4 + payload_len:4 + payload_len + 2]
        crc = struct.pack('@H', self._calc_crc(payload))

        if len(buffer) < (4 + payload_len + 2 + 1):
            self.lock = False
            self.gcode.respond_info(f"Invalid data from ACE PRO (len) {payload_len} {len(buffer)} {crc}")
            self.gcode.respond_info(str(buffer))
            return eventtime + 0.1

        if crc_data != crc:
            self.lock = False
            self.gcode.respond_info('Invalid data from ACE PRO (CRC)')

        ret = json.loads(payload.decode('utf-8'))
        id = ret['id']
        if id in self._callback_map:
            callback = self._callback_map.pop(id)
            callback(self=self, response=ret)
            self.lock = False
        return eventtime + 0.1

    def _writer(self, eventtime):
        #self.gcode.respond_info("ADBG: _writer")
        try:
            def callback(self, response):
                if response is not None:
                    self._info = response['result']
            if not self.lock:
                if not self._queue.empty():
                    task = self._queue.get()
                    if task is not None:
                        id = self._request_id
                        self._request_id += 1
                        self._callback_map[id] = task[1]
                        task[0]['id'] = id

                        self._send_request(task[0])
                        self.send_time = eventtime
                        self.lock = True
                else:
                    id = self._request_id
                    self._request_id += 1
                    self._callback_map[id] = callback
                    self._send_request({"id": id, "method": "get_status"})
                    self.send_time = eventtime
                    self.lock = True
        except serial.serialutil.SerialException as e:
            logging.info('ACE error: ' + traceback.format_exc())
            self.lock = False
            self.gcode.respond_info('Try reconnecting')
            self._serial_disconnect()
            self.connect_timer = self.reactor.register_timer(self._connect, self.reactor.NOW)
            return self.reactor.NEVER
        except Exception as e:
            self.gcode.respond_info(str(e))
            logging.info('ACE: Write error ' + str(e))
        return eventtime + 0.5

    def _handle_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        logging.info('ACE: Connecting to ' + self.serial_name)
        # We can catch timing where ACE reboots itself when no data is available from host. We're avoiding it with this hack
        self._connected = False
        self._queue = queue.Queue()
        self._main_queue = queue.Queue()
        self.connect_timer = self.reactor.register_timer(self._connect, self.reactor.NOW)
        # Start endless spool monitoring timer
        if hasattr(self, 'endless_spool_enabled'):
            self.endless_spool_timer = self.reactor.register_timer(self._endless_spool_monitor, self.reactor.NOW)
            # Hook into gcode move events for broader extruder monitoring
            self.printer.register_event_handler('toolhead:move', self._on_toolhead_move)


    def _handle_disconnect(self):
        logging.info('ACE: Closing connection to ' + self.serial_name)
        self.gcode.respond_info('ADBG:Closing connection to ' + self.serial_name)
        self._serial.close()
        self._connected = False
        self.reactor.unregister_timer(self.writer_timer)
        self.reactor.unregister_timer(self.reader_timer)
        # Stop endless spool monitoring
        if hasattr(self, 'endless_spool_timer'):
            self.reactor.unregister_timer(self.endless_spool_timer)

        self._queue = None
        self._main_queue = None

    def dwell(self, delay = 1.):
        currTs = self.reactor.monotonic()
        self.reactor.pause(currTs + delay)

    def send_request(self, request, callback):
        #self.gcode.respond_info(f"ADBG: send_request request={request}")
        self._info['status'] = 'busy'
        self._queue.put([request, callback])

    def wait_ace_ready(self):
        self.gcode.respond_info(f"ADBG: wait_ace_ready()-> status={self._info['status']}...")
        while self._info['status'] != 'ready':
            currTs = self.reactor.monotonic()
            self.reactor.pause(currTs + .5)
            self.gcode.respond_info(f"ADBG: wait_ace_ready()-> status={self._info['status']}")

    def _extruder_move(self, length, speed):
        pos = self.toolhead.get_position()
        pos[3] += length
        self.toolhead.move(pos, speed)
        
        return pos[3]

    def _endless_spool_monitor(self, eventtime):
        """Monitor for runout detection during printing"""
        if not self.endless_spool_enabled or self._park_in_progress or self.endless_spool_in_progress:
            return eventtime + 0.1

        # Only monitor if we have an active tool and we're not already in runout state
        current_tool = self.variables.get('ace_current_index', -1)
        if current_tool == -1:
            return eventtime + 0.1

        # Check if we're currently printing - be more aggressive about detecting print state
        try:
            # Check multiple indicators that we might be printing
            toolhead = self.printer.lookup_object('toolhead')
            print_stats = self.printer.lookup_object('print_stats', None)
            
            is_printing = False
            
            # Method 1: Check if toolhead is moving
            if hasattr(toolhead, 'get_status'):
                toolhead_status = toolhead.get_status(eventtime)
                if 'homed_axes' in toolhead_status and toolhead_status['homed_axes']:
                    is_printing = True
            
            # Method 2: Check print stats if available
            if print_stats:
                stats = print_stats.get_status(eventtime)
                if stats.get('state') in ['printing']:
                    is_printing = True
            
            # Method 3: Check idle timeout state
            try:
                printer_idle = self.printer.lookup_object('idle_timeout')
                idle_state = printer_idle.get_status(eventtime)['state']
                if idle_state in ['Printing', 'Ready']:  # Ready means potentially printing
                    is_printing = True
            except:
                # If idle_timeout doesn't exist, assume we might be printing
                is_printing = True

            # Always check for runout if endless spool is enabled and we have an active tool
            # Don't rely only on print state detection
            if current_tool >= 0:
                self._endless_spool_runout_handler()
            
            # Adjust monitoring frequency based on state
            if is_printing:
                return eventtime + 0.05  # Check every 50ms during printing
            else:
                return eventtime + 0.2   # Check every 200ms when idle
                
        except Exception as e:
            logging.info(f'ACE: Endless spool monitor error: {str(e)}')
            return eventtime + 0.1

    def _on_toolhead_move(self, print_time, newpos, oldpos):
        """Monitor toolhead moves for extruder movement during printing - removed distance tracking"""
        # This method is kept for potential future use but distance tracking removed
        pass

    def _create_mmu_sensor(self, config, pin, name):
        section = "filament_switch_sensor %s" % name
        config.fileconfig.add_section(section)
        config.fileconfig.set(section, "switch_pin", pin)
        config.fileconfig.set(section, "pause_on_runout", "False")
        fs = self.printer.load_object(config, section)

        ppins = self.printer.lookup_object('pins')
        pin_params = ppins.parse_pin(pin, True, True)
        share_name = "%s:%s" % (pin_params['chip_name'], pin_params['pin'])
        ppins.allow_multi_use_pin(share_name)
        mcu_endstop = ppins.setup_pin('endstop', pin)

        query_endstops = self.printer.load_object(config, "query_endstops")
        query_endstops.register_endstop(mcu_endstop, share_name)
        self.endstops[name] = mcu_endstop

    def _check_endstop_state(self, name):
        print_time = self.toolhead.get_last_move_time()
        return bool(self.endstops[name].query_endstop(print_time))

    def _serial_disconnect(self):

        if self._serial is not None and self._serial.isOpen():
            self._serial.close()
            self._connected = False

        self.reactor.unregister_timer(self.reader_timer)
        self.reactor.unregister_timer(self.writer_timer)

    def _connect(self, eventtime):

        try:
            port = self.find_com_port('ACE')
            if port is None:
                return eventtime + 1
            self.gcode.respond_info('Try connecting')
            self._serial = serial.Serial(
                port=port,
                baudrate=self.baud,
                timeout=0,
                write_timeout=0)

            if self._serial.isOpen():
                self._connected = True
                logging.info('ACE: Connected to ' + port)
                self.gcode.respond_info(f'ACE: Connected to {port} {eventtime}')
                self.writer_timer = self.reactor.register_timer(self._writer, self.reactor.NOW)
                self.reader_timer = self.reactor.register_timer(self._reader, self.reactor.NOW)
                self.send_request(request={"method": "get_info"},
                                  callback=lambda self, response: self.gcode.respond_info(str(response)))
                # --- Added: Check ace_current_index and enable feed assist if needed ---
                ace_current_index = self.variables.get('ace_current_index', -1)
                if ace_current_index != -1:
                    self.gcode.respond_info(f'ACE: Re-enabling feed assist on reconnect for index {ace_current_index}')
                    self._enable_feed_assist(ace_current_index)
                # ---------------------------------------------------------------
                self.reactor.unregister_timer(self.connect_timer)
                return self.reactor.NEVER
        except serial.serialutil.SerialException:
            self._serial = None
        return eventtime + 1


    cmd_ACE_START_DRYING_help = 'Starts ACE Pro dryer'

    def cmd_ACE_START_DRYING(self, gcmd):
        temperature = gcmd.get_int('TEMP')
        duration = gcmd.get_int('DURATION', 240)

        if duration <= 0:
            raise gcmd.error('Wrong duration')
        if temperature <= 0 or temperature > self.max_dryer_temperature:
            raise gcmd.error('Wrong temperature')

        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])

            self.gcode.respond_info('Started ACE drying')

        self.send_request(
            request={"method": "drying", "params": {"temp": temperature, "fan_speed": 7000, "duration": duration}},
            callback=callback)

    cmd_ACE_STOP_DRYING_help = 'Stops ACE Pro dryer'

    def cmd_ACE_STOP_DRYING(self, gcmd):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])

            self.gcode.respond_info('Stopped ACE drying')

        self.send_request(request={"method": "drying_stop"}, callback=callback)

    def _enable_feed_assist(self, index):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE Error: " + response['msg'])
            else:
                self._feed_assist_index = index
                self.gcode.respond_info(str(response))

        self.send_request(request={"method": "start_feed_assist", "params": {"index": index}}, callback=callback)
        self.dwell(delay=0.7)

    cmd_ACE_ENABLE_FEED_ASSIST_help = 'Enables ACE feed assist'

    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        index = gcmd.get_int('INDEX')

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')

        self._enable_feed_assist(index)

    def _disable_feed_assist(self, index):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE Error: " + response['msg'])

            self._feed_assist_index = -1
            self.gcode.respond_info('Disabled ACE feed assist')

        self.send_request(request={"method": "stop_feed_assist", "params": {"index": index}}, callback=callback)
        self.dwell(0.3)

    cmd_ACE_DISABLE_FEED_ASSIST_help = 'Disables ACE feed assist'

    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        if self._feed_assist_index != -1:
            index = gcmd.get_int('INDEX', self._feed_assist_index)
        else:
            index = gcmd.get_int('INDEX')

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')

        self._disable_feed_assist(index)

    def _feed(self, index, length, speed):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE Error: " + response['msg'])

        self.gcode.respond_info(f"ADBG: _feed(index={index}, length={length}, speed={speed})")
        
        self.send_request(
            request={"method": "feed_filament", "params": {"index": index, "length": length, "speed": speed}},
            callback=callback)
        self.dwell(delay=(length / speed) + 0.1)

    cmd_ACE_FEED_help = 'Feeds filament from ACE'

    def cmd_ACE_FEED(self, gcmd):
        index = gcmd.get_int('INDEX')
        length = gcmd.get_int('LENGTH')
        speed = gcmd.get_int('SPEED', self.feed_speed)

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')
        if length <= 0:
            raise gcmd.error('Wrong length')
        if speed <= 0:
            raise gcmd.error('Wrong speed')

        self._feed(index, length, speed)

    def _retract(self, index, length, speed):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE Error: " + response['msg'])

        self.gcode.respond_info(f"ADBG: _retract(index={index}, length={length}, speed={speed})")

        self.send_request(
            request={"method": "unwind_filament", "params": {"index": index, "length": length, "speed": speed}},
            callback=callback)
        self.dwell(delay=(length / speed) + 0.1)

    cmd_ACE_RETRACT_help = 'Retracts filament back to ACE'

    def cmd_ACE_RETRACT(self, gcmd):
        index = gcmd.get_int('INDEX')
        length = gcmd.get_int('LENGTH')
        speed = gcmd.get_int('SPEED', self.retract_speed)

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')
        if length <= 0:
            raise gcmd.error('Wrong length')
        if speed <= 0:
            raise gcmd.error('Wrong speed')

        self._retract(index, length, speed)

    def _park_to_toolhead(self, tool):

        sensor_extruder = self.printer.lookup_object("filament_switch_sensor %s" % "extruder_sensor", None)

        self.wait_ace_ready()

        self._feed(tool, self.toolchange_load_length, self.retract_speed)
        self.variables['ace_filament_pos'] = "bowden"

        self.wait_ace_ready()

        self._enable_feed_assist(tool)

        self.gcode.respond_info(f"ADBG: _park_to_toolhead() Wait for sensor_extruder.runout_helper.filament_present ")

        while not bool(sensor_extruder.runout_helper.filament_present):
            self.dwell(delay=0.1)

        if not bool(sensor_extruder.runout_helper.filament_present):
            raise ValueError("Filament stuck " + str(bool(sensor_extruder.runout_helper.filament_present)))
        else:
            self.variables['ace_filament_pos'] = "spliter"

        self.gcode.respond_info(f"ADBG: _park_to_toolhead() _check_endstop_state('toolhead_sensor')")

        w_dbg_cnt=0
        while not self._check_endstop_state('toolhead_sensor'):
            self.gcode.respond_info(f"ADBG: _park_to_toolhead() _feed({self.incremental_feeding_length},{self.incremental_feeding_speed})")
            self.gcode.respond_info(f"ADBG: _park_to_toolhead() _extruder_move({self.extruder_feeding_lenght}, {self.extruder_feeding_speed})")

            self._feed(tool,self.incremental_feeding_length,self.incremental_feeding_speed)
            self._extruder_move(self.extruder_feeding_lenght, self.extruder_feeding_speed)
            self.dwell(delay=0.01)
        
        self.gcode.respond_info(f"ADBG: _park_to_toolhead() self._check_endstop_state('toolhead_sensor') finished")
        
        self.variables['ace_filament_pos'] = "toolhead"

        self._extruder_move(self.toolhead_sensor_to_nozzle_length, 5)
        self.variables['ace_filament_pos'] = "nozzle"
        self.gcode.respond_info(f"ADBG: _park_to_toolhead: _ace_fpos set to:{self.variables['ace_filament_pos']}")

    cmd_ACE_CHANGE_TOOL_help = 'Changes tool'

    def cmd_ACE_CHANGE_TOOL(self, gcmd):
        tool = gcmd.get_int('TOOL')
        sensor_extruder = self.printer.lookup_object("filament_switch_sensor %s" % "extruder_sensor", None)

        if tool < -1 or tool >= 4:
            raise gcmd.error('Wrong tool')

        was = self.variables.get('ace_current_index', -1)
        if was == tool:
            _ace_fpos = self.variables.get('ace_filament_pos', "spliter")
            self.gcode.respond_info(f"ADBG: cmd_ACE_CHANGE_TOOL: _ace_fpos={_ace_fpos}")            
            gcmd.respond_info('ACE: Not changing tool, current index already ' + str(tool))
            
            if self.variables.get('ace_filament_pos', "spliter") == "nozzle" and not bool(sensor_extruder.runout_helper.filament_present):
                gcmd.respond_info('ACE: Tool' + str(tool)+ 'in nozzle state, but no filament present. Executing_park_to_toolhead_to_feed filament to nozzle')
                self._park_to_toolhead(tool)
            return

        if tool != -1:
            status = self._info['slots'][tool]['status']
            if status != 'ready':
                self.gcode.run_script_from_command('_ACE_ON_EMPTY_ERROR INDEX=' + str(tool))
                return
        
        # Temporarily disable endless spool during manual toolchange
        endless_spool_was_enabled = self.endless_spool_enabled
        if endless_spool_was_enabled:
            self.endless_spool_enabled = False
            self.endless_spool_runout_detected = False
        self._park_in_progress = True
        self.gcode.respond_info(f"ADBG: cmd_ACE_CHANGE_TOOL: running _ACE_PRE_TOOLCHANGE MACRO now")
        self.gcode.run_script_from_command('_ACE_PRE_TOOLCHANGE FROM=' + str(was) + ' TO=' + str(tool))
        self.gcode.respond_info(f"ADBG: cmd_ACE_CHANGE_TOOL:"+ 'ACE: Toolchange ' + str(was) + ' => ' + str(tool))
        logging.info('ACE: Toolchange ' + str(was) + ' => ' + str(tool))
        if was != -1:
            self._disable_feed_assist(was)
            self.wait_ace_ready()
            _ace_fpos = self.variables.get('ace_filament_pos', "spliter")
            self.gcode.respond_info(f"ADBG: cmd_ACE_CHANGE_TOOL: _ace_fpos={_ace_fpos}")
            
            if self.variables.get('ace_filament_pos', "spliter") == "nozzle":
                self.gcode.run_script_from_command('CUT_TIP')
                self.variables['ace_filament_pos'] = "toolhead"
                self.gcode.respond_info(f"ADBG: cmd_ACE_CHANGE_TOOL: _ace_fpos set to:{self.variables['ace_filament_pos']}")

            if self.variables.get('ace_filament_pos', "spliter") == "toolhead":
                self.gcode.respond_info(f"ADBG: cmd_ACE_CHANGE_TOOL: _ace_fpos sis toolhead, looping until sensor_extruder.runout_helper.filament_present")
                while bool(sensor_extruder.runout_helper.filament_present):
                    self._extruder_move(self.extruder_retraction_lenght, self.extruder_retraction_speed)
                    self.gcode.respond_info(f"ADBG: cmd_ACE_CHANGE_TOOL: retract {self.incremental_retraction_length} @ {self.incremental_retraction_speed} until filament_present")
                    self._retract(was, self.incremental_retraction_length, self.incremental_retraction_speed)
                    self.wait_ace_ready()
                self.variables['ace_filament_pos'] = "bowden"
                self.gcode.respond_info(f"ADBG: cmd_ACE_CHANGE_TOOL: _ace_fpos set to:{self.variables['ace_filament_pos']}")

            self.wait_ace_ready()
            self._retract(was, self.toolchange_retract_length, self.retract_speed)
            self.wait_ace_ready()
            self.variables['ace_filament_pos'] = "spliter"
            self.gcode.respond_info(f"ADBG: cmd_ACE_CHANGE_TOOL: _ace_fpos set to:{self.variables['ace_filament_pos']}")
            if tool != -1:
                self._park_to_toolhead(tool)
        else:
            self._park_to_toolhead(tool)

        self.gcode.respond_info(f"ADBG: cmd_ACE_CHANGE_TOOL: 1st. reset_last_position")
        gcode_move = self.printer.lookup_object('gcode_move')
        gcode_move.reset_last_position()

        self.gcode.run_script_from_command('_ACE_POST_TOOLCHANGE FROM=' + str(was) + ' TO=' + str(tool))
        self.variables['ace_current_index'] = tool
        
        
        self.gcode.respond_info(f"ADBG: cmd_ACE_CHANGE_TOOL: 2nd reset_last_position")
        gcode_move.reset_last_position()
        
        # Force save to disk
        self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_current_index VALUE=' + str(tool))
        self.gcode.run_script_from_command(
            f"""SAVE_VARIABLE VARIABLE=ace_filament_pos VALUE='"{self.variables['ace_filament_pos']}"'""")
        self._park_in_progress = False
        
        # Re-enable endless spool if it was enabled before
        if endless_spool_was_enabled:
            self.endless_spool_enabled = True
        
        #self.gcode.run_script_from_command("G92 E0")            
        gcmd.respond_info(f"Tool {tool} load")

    def _find_next_available_slot(self, current_slot):
        """Find the next available slot with filament for endless spool"""
        for i in range(4):
            next_slot = (current_slot + 1 + i) % 4
            if next_slot != current_slot:
                # Check both inventory and ACE status
                if (self.inventory[next_slot]["status"] == "ready" and 
                    self._info['slots'][next_slot]['status'] == 'ready'):
                    return next_slot
        return -1  # No available slots

    def _endless_spool_runout_handler(self):
        """Handle runout detection for endless spool"""
        if not self.endless_spool_enabled or self.endless_spool_in_progress:
            return

        current_tool = self.variables.get('ace_current_index', -1)
        if current_tool == -1:
            return

        try:
            sensor_extruder = self.printer.lookup_object("filament_switch_sensor extruder_sensor", None)
            if sensor_extruder:
                # Check both runout helper and direct endstop state
                runout_helper_present = bool(sensor_extruder.runout_helper.filament_present)
                endstop_triggered = self._check_endstop_state('extruder_sensor')
                
                # Log sensor states for debugging (remove after testing)
                # logging.info(f"ACE Debug: runout_helper={runout_helper_present}, endstop={endstop_triggered}")
                
                # Runout detected if filament is not present
                if not runout_helper_present or not endstop_triggered:
                    if not self.endless_spool_runout_detected:  # Only trigger once
                        self.endless_spool_runout_detected = True
                        self.gcode.respond_info("ACE: Endless spool runout detected, switching immediately")
                        logging.info(f"ACE: Runout detected - runout_helper={runout_helper_present}, endstop={endstop_triggered}")
                        # Execute endless spool change immediately
                        self._execute_endless_spool_change()
        except Exception as e:
            logging.info(f'ACE: Runout detection error: {str(e)}')

    def _execute_endless_spool_change(self):
        """Execute the endless spool toolchange - simplified for extruder sensor only"""
        if self.endless_spool_in_progress:
            return

        current_tool = self.variables.get('ace_current_index', -1)
        next_tool = self._find_next_available_slot(current_tool)
        
        if next_tool == -1:
            self.gcode.respond_info("ACE: No available slots for endless spool, pausing print")
            self.gcode.run_script_from_command('PAUSE')
            self.endless_spool_runout_detected = False
            return

        self.endless_spool_in_progress = True
        self.endless_spool_runout_detected = False
        
        self.gcode.respond_info(f"ACE: Endless spool changing from slot {current_tool} to slot {next_tool}")
        
        # Mark current slot as empty in inventory
        if current_tool >= 0:
            self.inventory[current_tool] = {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0}
            # Save updated inventory to persistent variables
            self.variables['ace_inventory'] = self.inventory
            self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_inventory VALUE=\'{json.dumps(self.inventory)}\'')
        
        try:
            # Direct endless spool change - no toolchange macros needed for runout response
            
            # Step 1: Disable feed assist on empty slot
            if current_tool != -1:
                self._disable_feed_assist(current_tool)
                self.wait_ace_ready()

            # Step 2: Feed filament from next slot until it reaches extruder sensor
            sensor_extruder = self.printer.lookup_object("filament_switch_sensor extruder_sensor", None)
            
            # Feed filament from new slot until extruder sensor triggers
            self._feed(next_tool, self.toolchange_load_length, self.retract_speed)
            self.wait_ace_ready()

            # Wait for filament to reach extruder sensor
            while not bool(sensor_extruder.runout_helper.filament_present):
                self.dwell(delay=0.1)

            if not bool(sensor_extruder.runout_helper.filament_present):
                raise ValueError("Filament stuck during endless spool change")

            # Step 3: Enable feed assist for new slot
            self._enable_feed_assist(next_tool)

            # Step 4: Update current index and save state
            self.variables['ace_current_index'] = next_tool
            self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_current_index VALUE=' + str(next_tool))
            
            self.endless_spool_in_progress = False
            
            self.gcode.respond_info(f"ACE: Endless spool completed, now using slot {next_tool}")
            
        except Exception as e:
            self.gcode.respond_info(f"ACE: Endless spool change failed: {str(e)}")
            self.gcode.run_script_from_command('PAUSE')
            self.endless_spool_in_progress = False

    cmd_ACE_ENABLE_ENDLESS_SPOOL_help = 'Enable endless spool feature'

    cmd_ACE_ENABLE_ENDLESS_SPOOL_help = 'Enable endless spool feature'

    def cmd_ACE_ENABLE_ENDLESS_SPOOL(self, gcmd):
        self.endless_spool_enabled = True
        
        # Save to persistent variables
        self.variables['ace_endless_spool_enabled'] = True
        self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_endless_spool_enabled VALUE=True')
        
        gcmd.respond_info("ACE: Endless spool enabled (immediate switching on runout, saved to persistent variables)")

    cmd_ACE_DISABLE_ENDLESS_SPOOL_help = 'Disable endless spool feature'

    def cmd_ACE_DISABLE_ENDLESS_SPOOL(self, gcmd):
        self.endless_spool_enabled = False
        self.endless_spool_runout_detected = False
        self.endless_spool_in_progress = False
        
        # Save to persistent variables
        self.variables['ace_endless_spool_enabled'] = False
        self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_endless_spool_enabled VALUE=False')
        
        gcmd.respond_info("ACE: Endless spool disabled (saved to persistent variables)")

    cmd_ACE_ENDLESS_SPOOL_STATUS_help = 'Show endless spool status'

    def cmd_ACE_ENDLESS_SPOOL_STATUS(self, gcmd):
        status = self.get_status()['endless_spool']
        saved_enabled = self.variables.get('ace_endless_spool_enabled', False)
        
        gcmd.respond_info(f"ACE: Endless spool status:")
        gcmd.respond_info(f"  - Currently enabled: {status['enabled']}")
        gcmd.respond_info(f"  - Saved enabled: {saved_enabled}")
        gcmd.respond_info(f"  - Mode: Immediate switching on runout detection")
        
        if status['enabled']:
            gcmd.respond_info(f"  - Runout detected: {status['runout_detected']}")
            gcmd.respond_info(f"  - In progress: {status['in_progress']}")

    def find_com_port(self, device_name):
        com_ports = serial.tools.list_ports.comports()
        for port, desc, hwid in com_ports:
            if device_name in desc:
                return port
        return None

    def cmd_ACE_DEBUG(self, gcmd):
        method = gcmd.get('METHOD')
        params = gcmd.get('PARAMS', '{}')

        try:
            def callback(self, response):
                self.gcode.respond_info(str(response))

            self.send_request(request = {"method": method, "params": json.loads(params)}, callback = callback)
        except Exception as e:
            self.gcode.respond_info('Error: ' + str(e))
        #self.gcode.respond_info(str(self.find_com_port('ACE')))


    def get_status(self, eventtime=None):
        status = self._info.copy()
        status['endless_spool'] = {
            'enabled': self.endless_spool_enabled,
            'runout_detected': self.endless_spool_runout_detected,
            'in_progress': self.endless_spool_in_progress
        }
        return status

    def cmd_ACE_SET_SLOT(self, gcmd):
        idx = gcmd.get_int('INDEX')
        if idx < 0 or idx >= 4:
            raise gcmd.error('Invalid slot index')
        if gcmd.get_int('EMPTY', 0):
            self.inventory[idx] = {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0}
            # Save to persistent variables
            self.variables['ace_inventory'] = self.inventory
            self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_inventory VALUE=\'{json.dumps(self.inventory)}\'')
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
        # Save to persistent variables
        self.variables['ace_inventory'] = self.inventory
        self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_inventory VALUE=\'{json.dumps(self.inventory)}\'')
        gcmd.respond_info(f"Slot {idx} set: color={color}, material={material}, temp={temp}")

    def cmd_ACE_QUERY_SLOTS(self, gcmd):
        import json
        gcmd.respond_info(json.dumps(self.inventory))

    cmd_ACE_SAVE_INVENTORY_help = 'Manually save current inventory to persistent storage'

    def cmd_ACE_SAVE_INVENTORY(self, gcmd):
        self.variables['ace_inventory'] = self.inventory
        self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_inventory VALUE=\'{json.dumps(self.inventory)}\'')
        gcmd.respond_info("ACE: Inventory saved to persistent storage")

    cmd_ACE_TEST_RUNOUT_SENSOR_help = 'Test and display runout sensor states'

    def cmd_ACE_TEST_RUNOUT_SENSOR(self, gcmd):
        try:
            sensor_extruder = self.printer.lookup_object("filament_switch_sensor extruder_sensor", None)
            if sensor_extruder:
                runout_helper_present = bool(sensor_extruder.runout_helper.filament_present)
                endstop_triggered = self._check_endstop_state('extruder_sensor')
                
                gcmd.respond_info(f"ACE: Extruder sensor states:")
                gcmd.respond_info(f"  - Runout helper filament present: {runout_helper_present}")
                gcmd.respond_info(f"  - Endstop triggered: {endstop_triggered}")
                gcmd.respond_info(f"  - Endless spool enabled: {self.endless_spool_enabled}")
                gcmd.respond_info(f"  - Current tool: {self.variables.get('ace_current_index', -1)}")
                gcmd.respond_info(f"  - Runout detected: {self.endless_spool_runout_detected}")
                
                # Test runout detection logic
                would_trigger = not runout_helper_present or not endstop_triggered
                gcmd.respond_info(f"  - Would trigger runout: {would_trigger}")
            else:
                gcmd.respond_info("ACE: Extruder sensor not found")
        except Exception as e:
            gcmd.respond_info(f"ACE: Error testing sensor: {str(e)}")

    cmd_ACE_GET_CURRENT_INDEX_help = 'Get the currently loaded slot index'

    def cmd_ACE_GET_CURRENT_INDEX(self, gcmd):
        current_index = self.variables.get('ace_current_index', -1)
        gcmd.respond_info(str(current_index))

    def _on_toolhead_move(self, event):
        """Event handler for toolhead move, used for monitoring extruder movement"""
        if not self.endless_spool_enabled or self._park_in_progress or self.endless_spool_in_progress:
            return

        # Check for runout during any move
        self._endless_spool_runout_handler()
        
        # If runout is detected, track extruder distance
        if hasattr(event, 'newpos') and hasattr(event, 'oldpos'):
            newpos = event.newpos
            oldpos = event.oldpos
            if len(newpos) > 3 and len(oldpos) > 3:
                e_move = newpos[3] - oldpos[3]
                if e_move > 0 and self.endless_spool_runout_detected:
                    self._endless_spool_check_distance(e_move)



def load_config(config):
    return BunnyAce(config)


