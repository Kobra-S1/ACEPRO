"""Where the intake-gated strategy replaces the default motion, and that
the default stays in place without it: the manager's unload, the route
before a load, the instance's load, and the setup from the config."""

from unittest.mock import Mock

import pytest

from ace.config import FEEDER_ACE, FEEDER_EXTRUDER, INSTANCE_MANAGERS
from ace.instance import AceInstance
from ace.intake_gated import IntakeGatedTransfer
from ace.manager import AceManager
from tests.test_unload_rdm_guard import TestSmartUnloadRdmEarlyStopGuard as UnloadFixture


class TestUnloadUsesTheTransfer:
    def _unload(self, rdm_has_filament):
        fixture = UnloadFixture()
        self.instance = fixture._instance()
        manager = fixture._manager(self.instance, rdm_has_filament)
        self.transfer = Mock()
        self.transfer.unload.return_value = True
        manager.transfers = {0: self.transfer}
        self.manager = manager
        return fixture._unload(manager)

    def test_the_transfer_unloads_with_the_extruder_limits(self):
        assert self._unload(rdm_has_filament=False) is True

        self.transfer.unload.assert_called_once_with(
            1, extruder_limit=40.0, extruder_speed=10.0, park=None)
        self.instance._smart_unload_slot.assert_not_called()
        self.manager._extruder_move.assert_not_called()

    def test_with_filament_at_the_rdm_the_park_step_stops_on_it(self):
        self._unload(rdm_has_filament=True)

        park = self.transfer.unload.call_args.kwargs["park"]
        park()
        self.instance.rmd_triggered_unload_slot.assert_called_once_with(
            self.manager, 1, length=800.0, overshoot_length=50.0)

    def test_a_failed_park_step_fails_the_unload(self):
        fixture = UnloadFixture()
        instance = fixture._instance()
        manager = fixture._manager(instance, rdm_has_filament=False)
        transfer = Mock()
        transfer.unload.return_value = False
        manager.transfers = {0: transfer}

        with pytest.raises(Exception, match="Unload failed"):
            fixture._unload(manager)


class TestUnloadWithTheSensorAlreadyClear:
    """Nothing at the toolhead sensor says nothing about the gear in front
    of it, and the extruder may be cold: the ACE pulls alone, so the route
    has to free the filament for it first."""

    def _unload(self, transfers):
        fixture = UnloadFixture()
        instance = fixture._instance()
        manager = fixture._manager(instance, rdm_has_filament=False)
        manager.get_instant_switch_state = Mock(return_value=False)
        manager.get_switch_state = Mock(return_value=False)
        manager.transfers = transfers
        self.order = []
        manager.route_to_tool = Mock(
            side_effect=lambda *args: self.order.append(("route",) + args))
        instance._smart_unload_slot.side_effect = (
            lambda *args, **kwargs: self.order.append(("ace retract",)) or True)
        instance.extruder_feeding_length = 80.0
        manager.has_rdm_sensor = Mock(return_value=False)
        self.instance = instance
        fixture._unload(manager)

    def test_the_route_frees_the_filament_before_the_ace_pulls(self):
        self._unload({0: Mock()})

        assert ("route", 1, FEEDER_ACE) in self.order
        assert (self.order.index(("route", 1, FEEDER_ACE))
                < self.order.index(("ace retract",)))

    def test_the_ace_pulls_the_gear_stretch_too(self):
        """Parked, the tip stands in the gear. The extruder does not drive
        it out here, so the ACE pull covers that stretch as well - short of
        it, the next feed pushes against a gear already holding the tip."""
        self._unload({0: Mock()})

        self.instance._smart_unload_slot.assert_called_once_with(
            1, length=800.0 + 80.0)

    def test_the_default_strategy_routes_and_pulls_as_before(self):
        self._unload({})

        assert ("route", 1, FEEDER_ACE) not in self.order
        self.instance._smart_unload_slot.assert_called_once_with(1, length=800.0)


class TestLoadFeeder:
    def _instance(self):
        instance = object.__new__(AceInstance)
        instance.transfer = None
        return instance

    def test_the_ace_feeds_a_default_load(self):
        assert self._instance().load_feeder() == FEEDER_ACE

    def test_the_extruder_feeds_an_intake_gated_load(self):
        instance = self._instance()
        instance.transfer = Mock()

        assert instance.load_feeder() == FEEDER_EXTRUDER


class TestSetup:
    def _manager(self, **ace_config):
        manager = object.__new__(AceManager)
        manager.ace_config = ace_config
        manager.config = Mock()
        manager.config.error = ValueError
        manager.gcode = Mock()
        manager.printer = Mock()
        self.intake = Mock()
        manager.printer.lookup_object.side_effect = (
            lambda name, default=None: self.intake if name == "intake" else default)
        instance = Mock()
        instance.instance_num = 0
        instance.transfer = None
        manager.instances = [instance]
        manager.transfers = {}
        return manager, instance

    def test_the_default_strategy_sets_nothing_up(self):
        manager, instance = self._manager()

        manager._setup_toolhead_strategy()

        assert manager.transfers == {} and instance.transfer is None

    def test_intake_gated_gives_each_instance_its_transfer(self):
        manager, instance = self._manager(
            toolhead_strategy="intake_gated", filament_intake_sensor_name="intake")

        manager._setup_toolhead_strategy()

        assert isinstance(instance.transfer, IntakeGatedTransfer)
        assert instance.transfer.intake is self.intake
        assert manager.transfers == {0: instance.transfer}

    def test_intake_gated_without_a_sensor_name_is_a_config_error(self):
        manager, _ = self._manager(toolhead_strategy="intake_gated")

        with pytest.raises(ValueError, match="filament_intake_sensor_name"):
            manager._setup_toolhead_strategy()

    def test_an_unknown_sensor_name_is_a_config_error(self):
        manager, _ = self._manager(
            toolhead_strategy="intake_gated", filament_intake_sensor_name="nope")

        with pytest.raises(ValueError, match="nope"):
            manager._setup_toolhead_strategy()

    def test_an_unknown_strategy_is_a_config_error(self):
        manager, _ = self._manager(toolhead_strategy="magic")

        with pytest.raises(ValueError, match="magic"):
            manager._setup_toolhead_strategy()

    def test_the_tools_share_one_tube_unless_the_config_says_otherwise(self):
        manager, _ = self._manager()
        manager._setup_toolhead_strategy()
        assert manager.toolhead_paths == "shared"

        manager, _ = self._manager(toolhead_paths="per_tool")
        manager._setup_toolhead_strategy()
        assert manager.toolhead_paths == "per_tool"

    def test_the_layout_can_be_asked_of_a_printer_object(self):
        """toolhead_paths: <object name> - the printer's tool changer owns
        which inlets the ACE feeds, so rearranging the tubes is its config
        change alone."""
        for shares, expected in ((True, "shared"), (False, "per_tool")):
            manager, instance = self._manager(toolhead_paths="changer")
            changer = Mock()
            changer.tools_share_path.return_value = shares
            manager.printer.lookup_object.side_effect = (
                lambda name, default=None: changer if name == "changer" else default)

            manager._setup_toolhead_strategy()

            assert manager.toolhead_paths == expected
            instance.apply_toolhead_paths.assert_called_once_with(expected)

    def test_the_same_object_is_asked_which_tool_the_toolhead_holds(self):
        manager, _ = self._manager(toolhead_paths="changer")
        changer = Mock()
        changer.tools_share_path.return_value = False
        changer.ace_tool_at_toolhead.return_value = 3
        manager.printer.lookup_object.side_effect = (
            lambda name, default=None: changer if name == "changer" else default)
        manager._setup_toolhead_strategy()

        assert manager.tool_at_toolhead() == 3

        changer.ace_tool_at_toolhead.return_value = None
        assert manager.tool_at_toolhead() == -1

    def test_nobody_to_ask_means_the_tool_is_unknown(self):
        manager, _ = self._manager(toolhead_paths="per_tool")
        manager._setup_toolhead_strategy()

        assert manager.tool_at_toolhead() == -1

    def test_an_object_that_cannot_say_is_a_config_error(self):
        manager, _ = self._manager(toolhead_paths="changer")
        manager.printer.lookup_object.side_effect = (
            lambda name, default=None: object() if name == "changer" else default)

        with pytest.raises(ValueError, match="tools_share_path"):
            manager._setup_toolhead_strategy()

    def test_an_unknown_path_layout_is_a_config_error(self):
        manager, _ = self._manager(toolhead_paths="star")

        with pytest.raises(ValueError, match="star"):
            manager._setup_toolhead_strategy()
