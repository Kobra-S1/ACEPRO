import serial, threading, time, logging, json, struct, queue, traceback, re
from serial import SerialException
import serial.tools.list_ports


class DuckAce:
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
        
        self.serial_name = config.get('serial', '/dev/ttyACM2')
        self.cut_position_x1 = config.get('cut_position_x1', 19)
        self.cut_position_y1 = config.get('cut_position_y1', 0)
        self.cut_position_x2 = config.get('cut_position_x2', 9)
        self.cut_position_y2 = config.get('cut_position_y2', 0)
        self.cut_speed = config.get('cut_speed', 60)
        self.purge_extrude = config.get('purge_extrude',100 )
        self.unload_extrude = config.get('unload_extrude', -100 )
        self.baud = config.getint('baud', 115200)
        self.feed_speed = config.getint('feed_speed', 80)
        self.retract_speed = config.getint('retract_speed', 50)
        self.toolchange_retract_length = config.getint('toolchange_retract_length', 200)
        self.park_hit_count = config.getint('park_hit_count', 5)
        self.max_dryer_temperature = config.getint('max_dryer_temperature', 55)
        self.disable_assist_after_toolchange = config.getboolean('disable_assist_after_toolchange', True)

        extruder_sensor_pin = config.get('extruder_sensor_pin', None)
        toolhead_sensor_pin = config.get('toolhead_sensor_pin', None)


        self._callback_map = {}
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
            'enable_rfid': 0,
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

        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:disconnect', self._handle_disconnect)

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
            'ACE_PARK_TO_TOOLHEAD', self.cmd_ACE_PARK_TO_TOOLHEAD,
            desc=self.cmd_ACE_PARK_TO_TOOLHEAD_help)
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
            'ACE_FILAMENT_INFO', self.cmd_ACE_FILAMENT_INFO,
            desc=self.cmd_ACE_FILAMENT_INFO_help)
        self.gcode.register_command(
            'ACE_STATUS', self.cmd_ACE_STATUS,
            desc=self.cmd_ACE_STATUS_help)
        self.gcode.register_command(
            'ACE_DEBUG', self.cmd_ACE_DEBUG,
            desc=self.cmd_ACE_DEBUG_help)


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


    def _main_eval(self, eventtime):
        while not self._main_queue.empty():
            task = self._main_queue.get_nowait()
            if task is not None:
                task()
        
        return eventtime + 0.25

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


    def _handle_disconnect(self):
        logging.info('ACE: Closing connection to ' + self.serial_name)
        self._serial.close()
        self._connected = False
        self.reactor.unregister_timer(self.writer_timer)
        self.reactor.unregister_timer(self.reader_timer)

        self._queue = None
        self._main_queue = None

    def send_request(self, request, callback):
        self._queue.put([request, callback])

    def wait_ace_ready(self):
        while self._info['status'] != 'ready':
            currTs = self.reactor.monotonic()
            self.reactor.pause(currTs + .5)

    def dwell(self, delay = 1.):
        currTs = self.reactor.monotonic()
        self.reactor.pause(currTs + delay)

    def _extruder_move(self, length, speed):
        self.wait_ace_ready()
        pos = self.toolhead.get_position()
        pos[3] += length
        self.gcode.respond_info('ACE: Start Move Extruder')
        self.toolhead.move(pos, speed)
        self.toolhead.wait_moves()
        self.gcode.respond_info('ACE: Finish Move Extruder')
        return pos[3]


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
                self.reactor.unregister_timer(self.connect_timer)
                return self.reactor.NEVER
        except serial.serialutil.SerialException:
            self._serial = None
        return eventtime + 1


    def _extruder_park(self, x=None, y=None, z=None, speed=None):
        # Get current position
        current_pos = self.toolhead.get_position()
        
        # Create new position by updating only the values that were provided
        new_pos = list(current_pos)
        if x is not None:
            new_pos[0] = x
        if y is not None:
            new_pos[1] = y
        if z is not None:
            new_pos[2] = z
        
        # Move to the new position
        if speed is None:
            speed = 400
        self.gcode.respond_info('ACE: Move PARK')

        self.toolhead.manual_move(new_pos, speed)
        # Wait for the move to complete
        self.toolhead.wait_moves()


    def _extruder_cut(self, x1=None, y1=None, x2=None, y2=None):
        # Get current position
        current_pos = self.toolhead.get_position()
        new_pos = list(current_pos)
        new_pos[0] = x1
        new_pos[1] = y1
        new_pos[2] = None
        speed = int(self.cut_speed)
        self.gcode.respond_info('ACE: Move CUT')
        self.toolhead.manual_move(new_pos, speed)
        # Wait for the move to complete
        self.toolhead.wait_moves()
        new_pos[0] = x2
        new_pos[1] = y2
        new_pos[2] = None
        speed = 100
        self.toolhead.manual_move(new_pos, speed)
        # Wait for the move to complete
        self.toolhead.wait_moves()
        new_pos[0] = x1
        new_pos[1] = y1
        new_pos[2] = None
        speed = 100
        self.gcode.respond_info('ACE: Move CUT')
        self.toolhead.manual_move(new_pos, speed)
        # Wait for the move to complete
        self.toolhead.wait_moves()
        new_pos[0] = x2
        new_pos[1] = y2
        new_pos[2] = None
        speed = 100
        self.toolhead.manual_move(new_pos, speed)
        # Wait for the move to complete
        self.toolhead.wait_moves()
        new_pos[0] = x1
        new_pos[1] = y1
        new_pos[2] = None
        speed = 100
        self.toolhead.manual_move(new_pos, speed)
        # Wait for the move to complete
        self.toolhead.wait_moves()


    cmd_ACE_START_DRYING_help = 'Starts ACE Pro dryer'
    def cmd_ACE_START_DRYING(self, gcmd):
        temperature = gcmd.get_int('TEMPERATURE')
        duration = gcmd.get_int('DURATION', 240)

        if duration <= 0:
            raise gcmd.error('Wrong duration')
        if temperature <= 0 or temperature > self.max_dryer_temperature:
            raise gcmd.error('Wrong temperature')

        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])
            
            self.gcode.respond_info('Started ACE drying at:' + str(temperature))
        
        self.send_request(request = {"method": "drying", "params": {"temp":temperature, "fan_speed": 7000, "duration": duration}}, callback = callback)


    cmd_ACE_STOP_DRYING_help = 'Stops ACE Pro dryer'
    def cmd_ACE_STOP_DRYING(self, gcmd):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])
            
            self.gcode.respond_info('Stopped ACE drying')
        
        self.send_request(request = {"method":"drying_stop"}, callback = callback)


    def _enable_feed_assist(self, index):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE Error: " + response['msg'])
            else:
                self._feed_assist_index = index
                self.gcode.respond_info(str(response))

        self.send_request(request = {"method": "start_feed_assist", "params": {"index": index}}, callback = callback)
        self.wait_ace_ready()
        self.dwell(delay = 0.7)

    cmd_ACE_ENABLE_FEED_ASSIST_help = 'Enables ACE feed assist'
    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        index = gcmd.get_int('INDEX')

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')

        self._enable_feed_assist(index)


    def _disable_feed_assist(self, index):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])

            self._feed_assist_index = -1
            self.gcode.respond_info('Disabled ACE feed assist')

        self.send_request(request = {"method": "stop_feed_assist", "params": {"index": index}}, callback = callback)
        self.wait_ace_ready()
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

        self.send_request(request = {"method": "feed_filament", "params": {"index": index, "length": length, "speed": speed}}, callback = callback)
        self.dwell(delay = (length / speed) + 0.1)

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
        self.wait_ace_ready()


    def _retract(self, index, length, speed):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE Error: " + response['msg'])

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
        self.wait_ace_ready()

    def _park_to_toolhead(self, tool):
        self._assist_hit_count = 0
        self._last_assist_count = 0
        self._park_in_progress = True
        self._park_index = tool
        self._enable_feed_assist(tool)
        while self._park_in_progress:
             self.gcode.respond_info('ACE: Loading to toolhead')
             self.dwell(delay=1)
        
        self.wait_ace_ready()
        self.gcode.respond_info('ACE: Finished loading')

    cmd_ACE_PARK_TO_TOOLHEAD_help = 'Parks filament from ACE to the toolhead'
    def cmd_ACE_PARK_TO_TOOLHEAD(self, gcmd):
        index = gcmd.get_int('INDEX')

        if self._park_in_progress:
            raise gcmd.error('Already parking to the toolhead')

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')
        
        status = self._info['slots'][index]['status']
        if status != 'ready':
            self.gcode.run_script_from_command('_ACE_ON_EMPTY_ERROR INDEX=' + str(index))
            return

        self._park_to_toolhead(index)
        self.wait_ace_ready()

    cmd_ACE_CHANGE_TOOL_help = 'Changes tool'
    def cmd_ACE_CHANGE_TOOL(self, gcmd):
        tool = gcmd.get_int('TOOL')
        if tool < -1 or tool >= 4:
            raise gcmd.error('Wrong tool')

        was = self.variables.get('ace_current_index', -1)
        if was == tool:
            logging.info('ACE: Not changing tool, current index already ' + str(tool))
            self._enable_feed_assist(tool)
            return
        
        if tool != -1:
            status = self._info['slots'][tool]['status']
            if status != 'ready':
                self.gcode.run_script_from_command('_ACE_ON_EMPTY_ERROR INDEX=' + str(tool))
                return
        self._park_in_progress = True
        self.gcode.run_script_from_command('_ACE_PRE_TOOLCHANGE FROM=' + str(was) + ' TO=' + str(tool))
        #up a bit and move to park position save state

        self._park_previous_tool = was

        #def callback(self, response):
        #    if 'code' in response and response['code'] != 0:
        #        raise gcmd.error("ACE Error: " + response['msg'])
        
        logging.info('ACE: Toolchange ' + str(was) + ' => ' + str(tool))
        if was != -1:
            self._disable_feed_assist(was)
            self.wait_ace_ready()
            self._extruder_park(x=25, y=360, z=None, speed=400)
            self._extruder_cut(x1=int(self.cut_position_x1), y1=int(self.cut_position_y1), x2=int(self.cut_position_x2), y2=int(self.cut_position_y2))
            self._extruder_park(x=25, y=360, z=None, speed=400)
            self._extruder_move(int(self.unload_extrude), 5)
            self._retract(was, self.toolchange_retract_length, self.retract_speed)

            self.wait_ace_ready()
            
            self.dwell(delay = 0.25)
            if tool != -1:
                self._park_to_toolhead(tool)
                self.wait_ace_ready()
                #self._extruder_move(int(self.purge_extrude), 5)
                self.gcode.respond_info('ACE: Finish extrude')

        else:
            self._extruder_park(x=25, y=360, z=None, speed=400)
            self._park_to_toolhead(tool)
            self.wait_ace_ready()
            self._extruder_move(int(self.purge_extrude), 5)
            self.gcode.respond_info('ACE: Finish extrude')

        self.wait_ace_ready()
        gcode_move = self.printer.lookup_object('gcode_move')
        gcode_move.reset_last_position()
        self.gcode.run_script_from_command('_ACE_POST_TOOLCHANGE FROM=' + str(was) + ' TO=' + str(tool))
        #Get back to print
        self.variables['ace_current_index'] = tool
        gcode_move.reset_last_position()
        # Force save to disk
        self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_current_index VALUE=' + str(tool))
        self._park_in_progress = False
        gcmd.respond_info(f"Tool {tool} load")


    cmd_ACE_FILAMENT_INFO_help = 'ACE_FILAMENT_INFO INDEX='
    def cmd_ACE_FILAMENT_INFO(self, gcmd):
        index = gcmd.get('INDEX')
        try:
            def callback(self, response):
                self.gcode.respond_info(str(response))
                logging.info('ACE: FILAMENT SLOT STATUS: ' + str(response))
                self.gcode.respond_info('ACE:'+ str(response))


            self.send_request(request = {"method": "get_filament_info", "index": index}, callback = callback)
        except Exception as e:
            self.gcode.respond_info('Error: ' + str(e))

    cmd_ACE_STATUS_help = 'ACE_STATUS'
    def cmd_ACE_STATUS(self, gcmd):
        try:
            def callback(self, response):
                resp = response['result']['status']
                logging.info('ACE: STATUS: ' + str(resp))
                self.gcode.respond_info(str(resp))
                if str(resp) == "busy":
                    self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_status VALUE=1')
                else:
                    self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_status VALUE=0')

            self.send_request(request = {"method": "get_status"}, callback = callback)
        except Exception as e:
            self.gcode.respond_info('Error: ' + str(e))


    def find_com_port(self, device_name):
        com_ports = serial.tools.list_ports.comports()
        for port, desc, hwid in com_ports:
            if device_name in desc:
                return port
        return None


    cmd_ACE_DEBUG_help = 'ACE Debug'
    def cmd_ACE_DEBUG(self, gcmd):
        method = gcmd.get('METHOD')
        params = gcmd.get('PARAMS', '{}')

        try:
            def callback(self, response):
                self.gcode.respond_info(str(response))
                logging.info('ACE: Response: ' + str(response))


            self.send_request(request = {"method": method, "params": json.loads(params)}, callback = callback)
            #self.send_request(request = {"method": "get_status"}, callback = callback)
        except Exception as e:
            self.gcode.respond_info('Error: ' + str(e))


def load_config(config):
    return DuckAce(config)
