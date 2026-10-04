"""
The optional _ACE_ROUTE_TOOL printer hook.

A toolhead with several filament paths (the Kobra X turret) has to bring one
path in front of the extruder and the toolhead sensor before the driver moves
that tool's filament. The driver announces the tool; the printer config
decides what that means. Without the macro nothing is announced, which is
every single-path printer.
"""

import unittest
from unittest.mock import Mock, patch

from ace.manager import AceManager
from ace.config import (
    FILAMENT_STATE_BOWDEN,
    FILAMENT_STATE_NOZZLE,
    FILAMENT_STATE_SPLITTER,
    SENSOR_RDM,
    SENSOR_TOOLHEAD,
)
from tests import test_manager

ROUTE = "_ACE_ROUTE_TOOL"


class _HookFixture:
    """The printer side of a test_manager fixture, with the hook macro
    defined or not, and every G-code script recorded in `self.order`
    together with the driver steps a test registers through `step`."""

    hook_defined = True

    def _install_hook(self, lookup):
        def lookup_with_hook(name, default=None):
            if name == f"gcode_macro {ROUTE}":
                return Mock() if self.hook_defined else default
            return lookup(name, default)

        self.mock_printer.lookup_object.side_effect = lookup_with_hook
        self.order = []
        self.mock_gcode.run_script_from_command.side_effect = self.order.append

    def step(self, name, return_value=None):
        def record(*args, **kwargs):
            self.order.append(name)
            return return_value

        return Mock(side_effect=record)

    def routes(self):
        return [entry for entry in self.order if entry.startswith(ROUTE)]


class TestToolChangeRoutes(_HookFixture, unittest.TestCase):
    _mock_config_get = test_manager.TestPerformToolChange._mock_config_get
    _mock_config_getint = test_manager.TestPerformToolChange._mock_config_getint
    _mock_config_getfloat = test_manager.TestPerformToolChange._mock_config_getfloat
    _mock_lookup_object = test_manager.TestPerformToolChange._mock_lookup_object

    def setUp(self):
        test_manager.TestPerformToolChange.setUp(self)
        self._install_hook(self._mock_lookup_object)

    def _change(self, current_tool, target_tool, filament_pos, toolhead):
        with patch("ace.manager.AceInstance"), patch("ace.manager.EndlessSpool"):
            manager = AceManager(self.mock_config)
        self.variables["ace_filament_pos"] = filament_pos
        manager._sensor_override = {SENSOR_TOOLHEAD: toolhead, SENSOR_RDM: False}
        manager.smart_unload = self.step("unload", True)
        manager.check_and_wait_for_spool_ready = Mock(return_value=True)
        instance = Mock()
        instance.instance_num = 0
        instance.inventory = {target_tool: {"status": "loaded", "temp": 0}}
        instance._feed_filament_into_toolhead = self.step("feed", 5.0)
        manager.instances[0] = instance
        with patch("ace.manager.get_ace_instance_and_slot_for_tool") as get_ace:
            get_ace.return_value = (instance, target_tool)
            manager.perform_tool_change(
                current_tool=current_tool, target_tool=target_tool
            )
        return instance

    def test_a_load_routes_to_its_tool_before_feeding(self):
        self._change(-1, 2, FILAMENT_STATE_SPLITTER, toolhead=False)

        self.assertEqual(self.routes()[-1], f"{ROUTE} TOOL=2")
        self.assertLess(self.order.index(f"{ROUTE} TOOL=2"), self.order.index("feed"))

    def test_a_change_routes_to_the_loaded_tool_then_to_the_new_one(self):
        self._change(1, 2, FILAMENT_STATE_NOZZLE, toolhead=True)

        steps = [e for e in self.order if e in ("unload", "feed") or e.startswith(ROUTE)]
        self.assertEqual(
            steps, [f"{ROUTE} TOOL=1", "unload", f"{ROUTE} TOOL=2", "feed"]
        )

    def test_no_hook_macro_means_no_route_command(self):
        self.hook_defined = False

        instance = self._change(1, 2, FILAMENT_STATE_NOZZLE, toolhead=True)

        self.assertEqual(self.routes(), [])
        instance._feed_filament_into_toolhead.assert_called_once()

    def test_a_refused_route_stops_the_change_before_any_feed(self):
        def refuse(script):
            if script.startswith(ROUTE):
                raise RuntimeError("turret is on a loaded filament")

        self.mock_gcode.run_script_from_command.side_effect = refuse

        with self.assertRaises(RuntimeError):
            self._change(-1, 2, FILAMENT_STATE_SPLITTER, toolhead=False)

        self.assertNotIn("feed", self.order)


class TestUnloadAndPreloadRoutes(_HookFixture, unittest.TestCase):
    _make_instance = test_manager.TestSmartUnload._make_instance
    _build_manager = test_manager.TestSmartUnload._build_manager

    def setUp(self):
        test_manager.TestSmartUnload.setUp(self)
        self._install_hook(self.mock_printer.lookup_object.side_effect)

    def _manager(self):
        self.instance = self._make_instance()
        manager = self._build_manager(lambda *a, **k: self.instance)
        for slot in manager.instances[0].inventory:
            slot["status"] = "ready"
        manager.prepare_toolhead_for_filament_retraction = self.step("prepare")
        manager.get_switch_state = Mock(return_value=False)
        manager.is_filament_path_free = Mock(return_value=True)
        return manager

    def test_unloading_a_known_tool_routes_to_it_before_the_cut(self):
        manager = self._manager()

        manager.smart_unload(tool_index=1)

        self.assertEqual(self.routes(), [f"{ROUTE} TOOL=1"])
        self.assertLess(self.order.index(f"{ROUTE} TOOL=1"), self.order.index("prepare"))

    def test_unloading_an_unknown_tool_routes_nowhere(self):
        manager = self._manager()
        manager._identify_and_unload_by_cycling = Mock(return_value=True)

        manager.smart_unload(tool_index=-1)

        self.assertEqual(self.routes(), [])

    def test_preloading_to_the_toolhead_sensor_routes_each_slot(self):
        manager = self._manager()
        manager.has_rdm_sensor = Mock(return_value=False)
        self.instance.inventory[2]["status"] = "empty"
        self.instance._feed_filament_to_verification_sensor = self.step("feed")

        manager.smart_load()

        self.assertEqual(
            self.routes(), [f"{ROUTE} TOOL={tool}" for tool in (0, 1, 3)]
        )
        self.assertEqual(self.order[:2], [f"{ROUTE} TOOL=0", "feed"])

    def test_preloading_to_the_rdm_sensor_leaves_the_toolhead_alone(self):
        manager = self._manager()
        manager.has_rdm_sensor = Mock(return_value=True)

        manager.smart_load()

        self.assertEqual(self.routes(), [])

    def test_fully_unloading_the_loaded_tool_routes_to_it(self):
        manager = self._manager()
        self.variables["ace_current_index"] = 1
        self.variables["ace_filament_pos"] = FILAMENT_STATE_NOZZLE
        self.instance._info = {"slots": [{"status": "ready"}] * 4}
        manager._extruder_move = Mock()

        manager.full_unload_slot(1)

        self.assertEqual(self.routes(), [f"{ROUTE} TOOL=1"])
        self.assertLess(self.order.index(f"{ROUTE} TOOL=1"), self.order.index("prepare"))

    def test_fully_unloading_a_parked_tool_leaves_the_toolhead_alone(self):
        manager = self._manager()
        self.variables["ace_current_index"] = 0
        self.variables["ace_filament_pos"] = FILAMENT_STATE_BOWDEN
        self.instance._info = {"slots": [{"status": "ready"}] * 4}
        self.instance.total_max_feeding_length = 1000.0
        self.instance.retract_speed = 100.0

        manager.full_unload_slot(1)

        self.assertEqual(self.routes(), [])


if __name__ == "__main__":
    unittest.main()
