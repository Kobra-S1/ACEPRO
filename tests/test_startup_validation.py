"""
Test suite for startup tool-state validation.

Tests AceManager._validate_startup_tool_state() which cross-references
the persisted ace_current_index / ace_filament_pos against live sensor
readings at startup to detect stale state caused by manual filament
removal while the printer was powered off.
"""

import unittest
from unittest.mock import Mock, patch

from ace.manager import AceManager
from ace.config import (
    ACE_INSTANCES,
    INSTANCE_MANAGERS,
    SLOTS_PER_ACE,
    SENSOR_TOOLHEAD,
    SENSOR_RDM,
    FILAMENT_STATE_BOWDEN,
    FILAMENT_STATE_SPLITTER,
    FILAMENT_STATE_TOOLHEAD,
    FILAMENT_STATE_NOZZLE,
)


class TestValidateStartupToolState(unittest.TestCase):
    """Tests for _validate_startup_tool_state."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)

        self.variables = {
            "ace_global_enabled": True,
            "ace_current_index": -1,
            "ace_filament_pos": FILAMENT_STATE_BOWDEN,
        }
        self.mock_save_vars.allVariables = self.variables

        # print_stats defaults to idle
        self.mock_print_stats = Mock()
        self.mock_print_stats.get_status.return_value = {"state": "standby"}

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={"value": 1})
                return pin
            if name == "print_stats":
                return self.mock_print_stats
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 1, "baud": 115200}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            val = {"purge_multiplier": "1.0"}.get(key, default)
            return float(val) if val is not None else default

        def get(key, default=None):
            return {
                "filament_runout_sensor_name_rdm": "return_module",
                "filament_runout_sensor_name_nozzle": "toolhead_sensor",
            }.get(key, default)

        def getboolean(key, default=None):
            return {
                "feed_assist_active_after_ace_connect": True,
                "rfid_inventory_sync_enabled": True,
                "ace_connection_supervision": True,
                "moonraker_lane_sync_enabled": False,
            }.get(key, default if default is not None else False)

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = get
        self.mock_config.getboolean.side_effect = getboolean

    def _instance_factory(self, instance_num, instance_config, printer, ace_enabled):
        inst = Mock()
        inst.instance_num = instance_num
        inst.SLOT_COUNT = SLOTS_PER_ACE
        inst.tool_offset = instance_num * SLOTS_PER_ACE
        inst.serial_mgr = Mock()
        inst.filament_runout_sensor_name_nozzle = "toolhead_sensor"
        inst.filament_runout_sensor_name_rdm = "return_module"
        inst.inventory = [{"status": "ready"} for _ in range(SLOTS_PER_ACE)]
        return inst

    def _build_manager(self):
        with patch("ace.manager.AceInstance", side_effect=self._instance_factory), \
             patch("ace.manager.EndlessSpool"), \
             patch("ace.manager.RunoutMonitor"):
            return AceManager(self.mock_config, dummy_ace_count=1)

    # ------------------------------------------------------------------
    # No-op early returns
    # ------------------------------------------------------------------

    def test_noop_when_no_tool_loaded(self):
        """No action when ace_current_index is -1."""
        self.variables["ace_current_index"] = -1
        self.variables["ace_filament_pos"] = FILAMENT_STATE_BOWDEN
        manager = self._build_manager()
        # Provide a sensor so we know the guard isn't just the sensor check
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)

        manager._validate_startup_tool_state()

        # State unchanged
        self.assertEqual(self.variables["ace_current_index"], -1)
        self.assertEqual(self.variables["ace_filament_pos"], FILAMENT_STATE_BOWDEN)

    def test_noop_when_filament_pos_is_bowden(self):
        """No action when filament_pos already 'bowden' even with tool set."""
        self.variables["ace_current_index"] = 3
        self.variables["ace_filament_pos"] = FILAMENT_STATE_BOWDEN
        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)

        manager._validate_startup_tool_state()

        # Tool index not touched
        self.assertEqual(self.variables["ace_current_index"], 3)

    def test_noop_when_no_sensors_registered(self):
        """No action when sensors dict is empty (ACE disabled)."""
        self.variables["ace_current_index"] = 5
        self.variables["ace_filament_pos"] = FILAMENT_STATE_NOZZLE
        manager = self._build_manager()
        # sensors dict stays empty — simulates ACE Pro disabled path
        manager.sensors.clear()

        manager._validate_startup_tool_state()

        # State must NOT be touched
        self.assertEqual(self.variables["ace_current_index"], 5)
        self.assertEqual(self.variables["ace_filament_pos"], FILAMENT_STATE_NOZZLE)

    # ------------------------------------------------------------------
    # Skip during active/paused print
    # ------------------------------------------------------------------

    def test_skipped_when_printing(self):
        """No state reset while printer is actively printing."""
        self.variables["ace_current_index"] = 2
        self.variables["ace_filament_pos"] = FILAMENT_STATE_NOZZLE
        self.mock_print_stats.get_status.return_value = {"state": "printing"}

        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)

        manager._validate_startup_tool_state()

        self.assertEqual(self.variables["ace_current_index"], 2)
        self.assertEqual(self.variables["ace_filament_pos"], FILAMENT_STATE_NOZZLE)
        self.mock_gcode.respond_info.assert_any_call(
            "ACE: Startup validation skipped — printer is printing"
        )

    def test_skipped_when_paused(self):
        """No state reset while printer is paused."""
        self.variables["ace_current_index"] = 1
        self.variables["ace_filament_pos"] = FILAMENT_STATE_SPLITTER
        self.mock_print_stats.get_status.return_value = {"state": "paused"}

        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)

        manager._validate_startup_tool_state()

        self.assertEqual(self.variables["ace_current_index"], 1)
        self.assertEqual(self.variables["ace_filament_pos"], FILAMENT_STATE_SPLITTER)
        self.mock_gcode.respond_info.assert_any_call(
            "ACE: Startup validation skipped — printer is paused"
        )

    # ------------------------------------------------------------------
    # Sensor confirms filament → state is plausible, no reset
    # ------------------------------------------------------------------

    def test_no_reset_when_toolhead_sensor_triggered(self):
        """Toolhead sensor shows filament → state is valid."""
        self.variables["ace_current_index"] = 5
        self.variables["ace_filament_pos"] = FILAMENT_STATE_NOZZLE
        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=True)

        manager._validate_startup_tool_state()

        self.assertEqual(self.variables["ace_current_index"], 5)
        self.assertEqual(self.variables["ace_filament_pos"], FILAMENT_STATE_NOZZLE)

    def test_no_reset_when_rdm_sensor_triggered(self):
        """RDM sensor shows filament (toolhead clear) → state is valid."""
        self.variables["ace_current_index"] = 3
        self.variables["ace_filament_pos"] = FILAMENT_STATE_SPLITTER
        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)
        manager.sensors[SENSOR_RDM] = Mock(filament_present=True)

        manager._validate_startup_tool_state()

        self.assertEqual(self.variables["ace_current_index"], 3)
        self.assertEqual(self.variables["ace_filament_pos"], FILAMENT_STATE_SPLITTER)

    def test_no_reset_when_both_sensors_triggered(self):
        """Both sensors show filament → state is valid."""
        self.variables["ace_current_index"] = 0
        self.variables["ace_filament_pos"] = FILAMENT_STATE_NOZZLE
        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=True)
        manager.sensors[SENSOR_RDM] = Mock(filament_present=True)

        manager._validate_startup_tool_state()

        self.assertEqual(self.variables["ace_current_index"], 0)

    # ------------------------------------------------------------------
    # Core: stale state reset when all sensors clear
    # ------------------------------------------------------------------

    def test_resets_state_when_all_sensors_clear_nozzle(self):
        """Stale nozzle state reset when both sensors are clear."""
        self.variables["ace_current_index"] = 5
        self.variables["ace_filament_pos"] = FILAMENT_STATE_NOZZLE
        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)
        manager.sensors[SENSOR_RDM] = Mock(filament_present=False)

        manager._validate_startup_tool_state()

        self.assertEqual(self.variables["ace_current_index"], -1)
        self.assertEqual(self.variables["ace_filament_pos"], FILAMENT_STATE_BOWDEN)

    def test_resets_state_when_all_sensors_clear_splitter(self):
        """Stale splitter state reset when both sensors are clear."""
        self.variables["ace_current_index"] = 2
        self.variables["ace_filament_pos"] = FILAMENT_STATE_SPLITTER
        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)
        manager.sensors[SENSOR_RDM] = Mock(filament_present=False)

        manager._validate_startup_tool_state()

        self.assertEqual(self.variables["ace_current_index"], -1)
        self.assertEqual(self.variables["ace_filament_pos"], FILAMENT_STATE_BOWDEN)

    def test_resets_state_when_all_sensors_clear_toolhead(self):
        """Stale toolhead state reset when both sensors are clear."""
        self.variables["ace_current_index"] = 7
        self.variables["ace_filament_pos"] = FILAMENT_STATE_TOOLHEAD
        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)
        manager.sensors[SENSOR_RDM] = Mock(filament_present=False)

        manager._validate_startup_tool_state()

        self.assertEqual(self.variables["ace_current_index"], -1)
        self.assertEqual(self.variables["ace_filament_pos"], FILAMENT_STATE_BOWDEN)

    def test_resets_state_toolhead_only_no_rdm(self):
        """Reset works when only toolhead sensor exists (no RDM)."""
        self.variables["ace_current_index"] = 0
        self.variables["ace_filament_pos"] = FILAMENT_STATE_NOZZLE
        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)
        # No RDM sensor registered at all

        manager._validate_startup_tool_state()

        self.assertEqual(self.variables["ace_current_index"], -1)
        self.assertEqual(self.variables["ace_filament_pos"], FILAMENT_STATE_BOWDEN)

    def test_emits_warning_message_on_reset(self):
        """Verify the user-visible warning message is emitted."""
        self.variables["ace_current_index"] = 5
        self.variables["ace_filament_pos"] = FILAMENT_STATE_NOZZLE
        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)
        manager.sensors[SENSOR_RDM] = Mock(filament_present=False)

        manager._validate_startup_tool_state()

        # Check that at least one respond_info call mentions the warning
        found = False
        for call_args in self.mock_gcode.respond_info.call_args_list:
            msg = call_args[0][0]
            if "STARTUP VALIDATION" in msg and "T5" in msg and "bowden" in msg.lower():
                found = True
                break
        self.assertTrue(found, "Expected startup validation warning message not found")

    def test_emits_confirmation_when_sensors_agree(self):
        """Verify confirmation log when sensors agree with state."""
        self.variables["ace_current_index"] = 3
        self.variables["ace_filament_pos"] = FILAMENT_STATE_NOZZLE
        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=True)

        manager._validate_startup_tool_state()

        found = False
        for call_args in self.mock_gcode.respond_info.call_args_list:
            msg = call_args[0][0]
            if "confirmed by sensors" in msg and "T3" in msg:
                found = True
                break
        self.assertTrue(found, "Expected sensor confirmation message not found")

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_print_stats_unavailable_assumes_idle(self):
        """When print_stats lookup returns None, treat as idle and validate."""
        self.variables["ace_current_index"] = 1
        self.variables["ace_filament_pos"] = FILAMENT_STATE_NOZZLE

        def lookup_no_print_stats(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={"value": 1})
                return pin
            if name == "print_stats":
                return None
            return default

        self.mock_printer.lookup_object.side_effect = lookup_no_print_stats

        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)

        manager._validate_startup_tool_state()

        # Should still reset since we assume idle
        self.assertEqual(self.variables["ace_current_index"], -1)
        self.assertEqual(self.variables["ace_filament_pos"], FILAMENT_STATE_BOWDEN)

    def test_print_stats_exception_assumes_idle(self):
        """When print_stats throws, treat as idle and validate."""
        self.variables["ace_current_index"] = 4
        self.variables["ace_filament_pos"] = FILAMENT_STATE_NOZZLE
        self.mock_print_stats.get_status.side_effect = Exception("boom")

        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)

        manager._validate_startup_tool_state()

        self.assertEqual(self.variables["ace_current_index"], -1)
        self.assertEqual(self.variables["ace_filament_pos"], FILAMENT_STATE_BOWDEN)

    def test_standby_state_allows_validation(self):
        """Explicit 'standby' print state should allow validation."""
        self.variables["ace_current_index"] = 6
        self.variables["ace_filament_pos"] = FILAMENT_STATE_NOZZLE
        self.mock_print_stats.get_status.return_value = {"state": "standby"}

        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)
        manager.sensors[SENSOR_RDM] = Mock(filament_present=False)

        manager._validate_startup_tool_state()

        self.assertEqual(self.variables["ace_current_index"], -1)

    def test_complete_state_allows_validation(self):
        """'complete' print state (print finished) should allow validation."""
        self.variables["ace_current_index"] = 3
        self.variables["ace_filament_pos"] = FILAMENT_STATE_TOOLHEAD
        self.mock_print_stats.get_status.return_value = {"state": "complete"}

        manager = self._build_manager()
        manager.sensors[SENSOR_TOOLHEAD] = Mock(filament_present=False)

        manager._validate_startup_tool_state()

        self.assertEqual(self.variables["ace_current_index"], -1)

    def test_handle_ready_calls_validate(self):
        """Verify _handle_ready invokes _validate_startup_tool_state."""
        manager = self._build_manager()
        manager._setup_sensors = Mock()
        manager._start_monitoring = Mock()
        manager._validate_startup_tool_state = Mock()

        # Need toolhead for the ready handler
        mock_toolhead = Mock()

        def lookup_with_toolhead(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={"value": 1})
                return pin
            if name == "toolhead":
                return mock_toolhead
            if name == "print_stats":
                return self.mock_print_stats
            return default

        self.mock_printer.lookup_object.side_effect = lookup_with_toolhead

        manager._handle_ready()

        manager._validate_startup_tool_state.assert_called_once()

    def test_handle_ready_disabled_still_calls_validate(self):
        """Even when ACE Pro is disabled, _validate_startup_tool_state is called.

        The method itself will bail out because no sensors are registered.
        """
        self.variables["ace_global_enabled"] = False
        manager = self._build_manager()
        manager._setup_sensors = Mock()
        manager._start_monitoring = Mock()
        manager._validate_startup_tool_state = Mock()

        mock_toolhead = Mock()

        def lookup_with_toolhead(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={"value": 0})
                return pin
            if name == "toolhead":
                return mock_toolhead
            if name == "print_stats":
                return self.mock_print_stats
            return default

        self.mock_printer.lookup_object.side_effect = lookup_with_toolhead

        manager._handle_ready()

        manager._validate_startup_tool_state.assert_called_once()


if __name__ == "__main__":
    unittest.main()
