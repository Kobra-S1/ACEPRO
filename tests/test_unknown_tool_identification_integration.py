"""
Integration tests for unknown-tool identification (plausibility mismatch).

Reproduces the KS1 field failure end-to-end with a small stateful
filament-path simulator instead of expectation mocks: the REAL
smart_unload → _identify_and_unload_by_cycling →
_cycle_slots_with_sensor_check → execute_coordinated_retraction chain
runs against sensors whose state is derived from a simulated filament
tip position, so the tests fail for physical reasons, not mock wiring.

Geometry (KS1-like, mm of filament path measured from the nozzle):
- GEAR_EXIT   = 40  : tip past this point → extruder gears have no grip
                      (toolhead_retraction_length is dimensioned for this)
- SENSOR_POS  = 95  : toolhead sensor
                      (extruder_feeding_length 10 + toolhead_full_purge_length 85)
- RDM_POS     = 850 : RDM sensor
                      (parkposition_to_toolhead 1000 − parkposition_to_rdm 150)

Physics rules (matching the real machine):
- The extruder can move the filament only while the tip is below
  GEAR_EXIT (gears gripping).
- An ACE slot motor can move the filament only for the loaded slot, and
  only once the extruder has released it (otherwise the gears block).
"""

import re
import unittest
from unittest.mock import Mock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ace.manager import AceManager
from ace.config import (
    FILAMENT_STATE_BOWDEN,
    FILAMENT_STATE_NOZZLE,
)

GEAR_EXIT = 40.0
SENSOR_POS = 95.0
RDM_POS = 850.0
UNLOADED_TIP = 2000.0  # past every sensor


class FilamentPathSimulator:
    """Tracks one loaded filament's tip position and derives sensor states."""

    def __init__(self, loaded_tool, tip=0.0):
        self.loaded_tool = loaded_tool  # global tool number, -1 = none
        self.tip = tip                  # mm from nozzle (0 = fully loaded)
        self.toolhead_stuck = False     # simulate a mechanically stuck sensor

    def toolhead_present(self):
        if self.toolhead_stuck:
            return True
        return self.loaded_tool >= 0 and self.tip < SENSOR_POS

    def rdm_present(self):
        return self.loaded_tool >= 0 and self.tip < RDM_POS

    def extruder_retract(self, length):
        """Extruder pull: only moves the filament while gears grip it."""
        if self.loaded_tool < 0:
            return
        movable = max(0.0, GEAR_EXIT - self.tip)
        self.tip += min(length, movable)

    def ace_retract(self, tool, length, early_stop_callback=None, step=5.0):
        """ACE slot pull: only the loaded slot, only after extruder release.

        Mirrors the real _retract contract: the early_stop_callback is
        polled during the motion; a non-None return stops the retraction.
        """
        moves = tool == self.loaded_tool and self.tip >= GEAR_EXIT
        remaining = length
        while remaining > 0:
            if early_stop_callback is not None:
                if early_stop_callback() is not None:
                    return
            if moves:
                self.tip += min(step, remaining)
            remaining -= step
        if early_stop_callback is not None:
            early_stop_callback()

    def full_unload(self):
        self.tip = UNLOADED_TIP


class TestUnknownToolIdentification(unittest.TestCase):

    def setUp(self):
        self.config_values = {
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
            'toolchange_load_length': 2000,
            'default_color_change_purge_length': 50,
            'default_color_change_purge_speed': 400,
            'purge_max_chunk_length': 300,
            'purge_multiplier': 1.0,
            'incremental_feeding_length': 100,
            'incremental_feeding_speed': 60,
            'extruder_feeding_length': 10,
            'extruder_feeding_speed': 8,
            'feed_assist_active_after_ace_connect': False,
            'heartbeat_interval': 1.0,
            'toolhead_retraction_speed': 10,
            'toolhead_retraction_length': 40,
            'toolhead_full_purge_length': 85,
            'toolhead_slow_loading_speed': 5,
            'pre_cut_retract_length': 2,
            'max_dryer_temperature': 60,
            'rfid_inventory_sync_enabled': True,
        }

        self.mock_config = Mock()
        self.mock_config.get = lambda k, d=None: self.config_values.get(k, d)
        self.mock_config.getint = lambda k, d=None: (
            int(self.config_values.get(k, d)) if self.config_values.get(k, d) is not None else d)
        self.mock_config.getfloat = lambda k, d=None: (
            float(self.config_values.get(k, d)) if self.config_values.get(k, d) is not None else d)
        self.mock_config.getboolean = lambda k, d=None: (
            bool(self.config_values.get(k, d)) if self.config_values.get(k, d) is not None else d)
        self.mock_config.error = Exception

        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_reactor.monotonic = Mock(return_value=1000.0)
        self.mock_reactor.pause = Mock()
        self.mock_reactor.NOW = 0.0
        self.mock_reactor.register_timer = Mock()
        self.mock_gcode = Mock()
        self.mock_toolhead = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_printer.lookup_object.side_effect = self._lookup_object

        self.variables = {
            'ace_filament_pos': FILAMENT_STATE_BOWDEN,
            'ace_current_index': -1,
            'ace_global_enabled': True,
        }
        self.mock_save_vars.allVariables = self.variables

        self.sim = None  # set per test before _create_manager
        self.gcode_commands = []
        self.respond_messages = []
        self.mock_gcode.run_script_from_command.side_effect = self._run_gcode
        self.mock_gcode.respond_info.side_effect = self.respond_messages.append

    # ----- boundary fakes -------------------------------------------------

    def _make_runout_helper(self, present_fn):
        helper = Mock()
        helper.sensor_enabled = True
        type(helper).filament_present = property(lambda _self: present_fn())
        # Explicit: a bare Mock auto-creates is_instantly_clear and its
        # truthy return would read as CLEAR in get_instant_switch_state.
        helper.is_instantly_clear = lambda: not present_fn()
        return helper

    def _lookup_object(self, name, default=None):
        if name == 'gcode':
            return self.mock_gcode
        if name == 'save_variables':
            return self.mock_save_vars
        if name == 'toolhead':
            return self.mock_toolhead
        if name == 'output_pin ACE_Pro':
            pin = Mock()
            pin.get_value = Mock(return_value=1)
            pin.get_status = Mock(return_value={'value': 1})
            return pin
        if name == 'filament_switch_sensor filament_runout_nozzle':
            sensor = Mock()
            sensor.runout_helper = self._make_runout_helper(
                lambda: self.sim.toolhead_present())
            return sensor
        if name == 'filament_switch_sensor filament_runout_rdm':
            sensor = Mock()
            sensor.runout_helper = self._make_runout_helper(
                lambda: self.sim.rdm_present())
            return sensor
        if name == 'gcode_move':
            return Mock()
        if name == 'extruder':
            extruder = Mock()
            extruder.get_status = Mock(return_value={'temperature': 210})
            heater = Mock()
            heater.get_temp = Mock(return_value=(210.0, 210.0))
            heater.min_extrude_temp = 170.0
            extruder.get_heater = Mock(return_value=heater)
            return extruder
        if name == 'print_stats':
            stats = Mock()
            stats.get_status = Mock(return_value={'state': 'standby'})
            return stats
        return default

    def _run_gcode(self, command):
        """Track gcode and drive the simulator for extruder E moves."""
        self.gcode_commands.append(command)
        match = re.search(r'G1 E(-?\d+(?:\.\d+)?)', command)
        if match:
            length = float(match.group(1))
            if length < 0:
                self.sim.extruder_retract(-length)

    def _create_manager(self):
        with patch('ace.manager.AceInstance') as instance_cls, \
                patch('ace.manager.EndlessSpool'), \
                patch('ace.manager.RunoutMonitor'):
            instance = Mock()
            instance.instance_num = 0
            instance.tool_offset = 0
            instance.SLOT_COUNT = 4
            instance.filament_runout_sensor_name_nozzle = 'filament_runout_nozzle'
            instance.filament_runout_sensor_name_rdm = 'filament_runout_rdm'
            instance.inventory = [
                {'status': 'ready', 'temp': 210, 'material': 'PLA'}
                for _ in range(4)
            ]
            instance._info = {'slots': [{'status': 'ready'} for _ in range(4)]}
            instance._feed_assist_index = -1
            instance.rdm_overshoot_length = 50.0
            instance.extruder_feeding_length = 10.0
            instance.toolhead_full_purge_length = 85.0
            instance.parkposition_to_toolhead_length = 1000.0
            instance.parkposition_to_rdm_length = 150.0
            instance.feed_speed = 60.0
            instance.wait_ready = Mock()
            instance.dwell = Mock()
            instance._disable_feed_assist = Mock()
            instance._enable_feed_assist = Mock()
            instance._stop_retract = Mock()

            def fake_ace_retract(slot, length, speed=None, early_stop_callback=None):
                self.sim.ace_retract(
                    instance.tool_offset + slot, length,
                    early_stop_callback=early_stop_callback)
            instance._retract = Mock(side_effect=fake_ace_retract)

            def fake_full_unload(slot, length=None):
                if instance.tool_offset + slot == self.sim.loaded_tool:
                    self.sim.full_unload()
                return True
            instance._smart_unload_slot = Mock(side_effect=fake_full_unload)

            def fake_feed_into_toolhead(tool, check_pre_condition=True):
                self.sim.loaded_tool = tool
                self.sim.tip = 0.0
                manager.state.set_and_save(
                    'ace_filament_pos', FILAMENT_STATE_NOZZLE)
                return 50.0
            instance._feed_filament_into_toolhead = Mock(
                side_effect=fake_feed_into_toolhead)

            instance_cls.side_effect = [instance]
            manager = AceManager(self.mock_config, dummy_ace_count=1)

        # Manager init reloads persisted (empty) inventory - restore
        for slot in manager.instances[0].inventory:
            slot.update({'status': 'ready', 'temp': 210, 'material': 'PLA'})

        # Sensor registration normally happens on klippy:ready
        manager._setup_sensors()

        # Heating/cutting is macro-driven (no-op through tracked gcode);
        # the simulator's tip position is the test's given.
        manager.prepare_toolhead_for_filament_retraction = Mock()
        manager.check_and_wait_for_spool_ready = Mock(return_value=True)
        return manager

    def _messages(self):
        return "\n".join(self.respond_messages)

    # ----- scenarios ------------------------------------------------------

    def test_field_scenario_continuation_identifies_loaded_slot(self):
        """KS1 klippy(21) log: stale state (bowden/T-1) with T0 physically
        loaded.  The coordinated 40mm retract releases the filament from the
        extruder but stops below the sensor; the ACE-only continuation must
        clear the sensor and identify T0, and the toolchange must recover
        instead of cancelling the print."""
        self.sim = FilamentPathSimulator(loaded_tool=0, tip=0.0)
        manager = self._create_manager()

        status = manager.perform_tool_change(current_tool=-1, target_tool=0)

        self.assertIn('→ 0', status)
        self.assertEqual(self.variables['ace_current_index'], 0)
        self.assertEqual(self.variables['ace_filament_pos'], FILAMENT_STATE_NOZZLE)
        self.assertIn('continuing with ACE-only retraction', self._messages())
        self.assertIn('T0 identified', self._messages())

    def test_wrong_slot_first_is_not_misidentified(self):
        """T1 loaded but slot 0 is cycled first: slot 0's test releases the
        filament from the extruder (real physics) but its continuation must
        NOT clear the sensor - only slot 1's ACE can move the filament."""
        self.sim = FilamentPathSimulator(loaded_tool=1, tip=0.0)
        manager = self._create_manager()

        status = manager.perform_tool_change(current_tool=-1, target_tool=0)

        self.assertIn('→ 0', status)
        self.assertIn('T1 identified', self._messages())
        self.assertNotIn('T0 identified', self._messages())
        # The loaded filament was fully unloaded via slot 1
        unload_slots = [
            call.args[0] for call
            in manager.instances[0]._smart_unload_slot.call_args_list
        ]
        self.assertIn(1, unload_slots)

    def test_stuck_toolhead_sensor_escalates_to_rdm_cycling(self):
        """A toolhead sensor that never clears must not dead-end: with the
        RDM triggered, cycling escalates to RDM-monitored identification,
        which unloads the real slot.  (The final path-free verification
        still fails on the stuck sensor - correct, the path cannot be
        proven clear - but the escalation and RDM unload must happen.)"""
        self.sim = FilamentPathSimulator(loaded_tool=0, tip=0.0)
        self.sim.toolhead_stuck = True
        manager = self._create_manager()

        result = manager.smart_unload(tool_index=-1, keep_heater=True)

        self.assertFalse(result)
        self.assertIn('escalating to RDM-monitored cycling', self._messages())
        self.assertIn('identified via', self._messages())
        # The RDM pass really pulled the loaded filament out
        self.assertGreaterEqual(self.sim.tip, RDM_POS)


if __name__ == '__main__':
    unittest.main()
