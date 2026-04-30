"""
Pytest configuration for ACE Pro tests
Handles mocking of external dependencies before imports
"""

import sys
import pytest
from unittest.mock import MagicMock, Mock

# Mock the serial module BEFORE any imports
# This must be done at module load time
mock_serial = MagicMock()
mock_serial.tools = MagicMock()
sys.modules['serial'] = mock_serial
sys.modules['serial.tools'] = mock_serial.tools
sys.modules['serial.tools.list_ports'] = MagicMock()

def pytest_configure(config):
    """Configure pytest - run before collection starts"""
    # Ensure serial is mocked
    mock_serial = MagicMock()
    mock_serial.tools = MagicMock()
    sys.modules['serial'] = mock_serial
    sys.modules['serial.tools'] = mock_serial.tools
    sys.modules['serial.tools.list_ports'] = MagicMock()


@pytest.fixture
def mock_config():
    """Create mock config object for AceManager."""
    config = Mock()
    
    # Setup config methods that manager uses
    ace_config_dict = {
        'ace_count': 1,
        'baud': 115200,
        'feed_speed': 60,
        'retract_speed': 50,
        'timeout_multiplier': 2,
        'filament_runout_sensor_name_rdm': 'filament_runout_rdm',
        'filament_runout_sensor_name_nozzle': 'filament_runout_nozzle',
        'total_max_feeding_length': 2500,
        'parkposition_to_toolhead_length': 1000,
        'parkposition_to_rdm_length': 150,
        'toolchange_load_length': 3000,
        'default_color_change_purge_length': 50,
        'default_color_change_purge_speed': 400,
        'purge_max_chunk_length': 300,
        'purge_multiplier': 1.0,
        'incremental_feeding_length': 50,
        'incremental_feeding_speed': 30,
        'extruder_feeding_length': 1,
        'extruder_feeding_speed': 5,
        'feed_assist_active_after_ace_connect': False,
        'heartbeat_interval': 1.0,
        'toolhead_retraction_speed': 10,
        'toolhead_retraction_length': 40,
        'toolhead_full_purge_length': 22,
        'toolhead_slow_loading_speed': 5,
        'pre_cut_retract_length': 2,
        'max_dryer_temperature': 60,
        'rfid_inventory_sync_enabled': True,
        # Disable Moonraker lane sync in tests to avoid hitting live Moonraker
        # and to prevent MagicMocks from leaking into lane keys.
        'moonraker_lane_sync_enabled': False,
    }
    
    # Mock config methods
    def mock_get(key, default=None):
        return ace_config_dict.get(key, default)
    
    def mock_getint(key, default=None):
        val = ace_config_dict.get(key, default)
        return int(val) if val is not None else default
    
    def mock_getfloat(key, default=None):
        val = ace_config_dict.get(key, default)
        return float(val) if val is not None else default
    
    def mock_getboolean(key, default=None):
        val = ace_config_dict.get(key, default)
        return bool(val) if val is not None else default
    
    config.get = mock_get
    config.getint = mock_getint
    config.getfloat = mock_getfloat
    config.getboolean = mock_getboolean
    config.error = Exception
    
    # Mock get_printer for access in tests
    mock_printer = Mock()
    mock_printer.get_reactor = Mock(return_value=Mock())
    config.get_printer = Mock(return_value=mock_printer)
    
    return config
