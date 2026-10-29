"""ACE tools numbered from a base other than 0.

Behind a printer-side tool changer that puts its own tools first (Kobra X:
three direct inlets, then the ACE) the first ACE's slot 0 is printer T3.
Every tool number ACEPRO takes or shows is that printer number, so the code
that does the numbering arithmetic itself, instead of through
ace.config's mapping, must follow the base too.
"""
from unittest.mock import Mock

import pytest

import ace.commands
from ace.config import ACE_INSTANCES, INSTANCE_MANAGERS, SLOTS_PER_ACE, set_tool_base
from ace.endless_spool import EndlessSpool
from ace.runout_monitor import RunoutMonitor

BASE = 3


def _instance(instance_num, inventory=None):
    instance = Mock()
    instance.instance_num = instance_num
    instance.SLOT_COUNT = SLOTS_PER_ACE
    instance.inventory = inventory or [
        {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0}
        for _ in range(SLOTS_PER_ACE)
    ]
    instance.serial_mgr.is_connected.return_value = True
    return instance


@pytest.fixture(autouse=True)
def one_ace_after_three_tools():
    ACE_INSTANCES.clear()
    INSTANCE_MANAGERS.clear()
    ACE_INSTANCES[0] = _instance(0)
    INSTANCE_MANAGERS[0] = Mock()
    INSTANCE_MANAGERS[0].state.get = Mock(return_value=-1)
    set_tool_base(BASE)
    yield
    ACE_INSTANCES.clear()
    INSTANCE_MANAGERS.clear()


def _gcmd(**params):
    gcmd = Mock()
    gcmd.get_command_parameters = Mock(return_value=params)
    gcmd.get_int = Mock(side_effect=lambda name, default=None, **kw:
                        params.get(name, default))
    gcmd.error = Exception
    return gcmd


def _said(gcmd):
    return "\n".join(c[0][0] for c in gcmd.respond_info.call_args_list)


class TestQuerySlots:
    def test_the_table_names_the_printer_tool(self):
        ACE_INSTANCES[0].inventory[2] = {
            "status": "ready", "color": [255, 0, 0], "material": "ABS", "temp": 240}
        gcmd = _gcmd()
        ace.commands.cmd_ACE_QUERY_SLOTS(gcmd)
        rows = [line for line in _said(gcmd).splitlines()
                if line.strip()[:2] in ("[0", "[1", "[2", "[3")]
        assert [row.split("|")[0].strip() for row in rows] == [
            "[0] T3", "[1] T4", "[2] T5", "[3] T6"], rows


class TestDebugIndexRange:
    @pytest.mark.parametrize("command", [
        ace.commands.cmd_ACE_DEBUG_SET_CURRENT_INDEX,
        ace.commands.cmd_ACE_DEBUG_SET_TARGET_INDEX,
    ])
    def test_an_ace_tool_is_accepted(self, command):
        command(_gcmd(TOOL=5))
        INSTANCE_MANAGERS[0].state.set_and_save.assert_any_call(
            command.__name__.replace("cmd_ACE_DEBUG_SET_", "ace_").lower(), 5)

    @pytest.mark.parametrize("command", [
        ace.commands.cmd_ACE_DEBUG_SET_CURRENT_INDEX,
        ace.commands.cmd_ACE_DEBUG_SET_TARGET_INDEX,
    ])
    @pytest.mark.parametrize("tool", [2, 7])
    def test_a_tool_outside_the_ace_is_refused(self, command, tool):
        # T2 is the tool changer's own; T7 would be a second ACE.
        with pytest.raises(Exception) as error:
            command(_gcmd(TOOL=tool))
        assert "T3" in str(error.value) and "T6" in str(error.value)
        assert not INSTANCE_MANAGERS[0].state.set_and_save.called


class TestEndlessSpool:
    def _spool(self):
        printer = Mock()
        printer.get_reactor.return_value.monotonic.return_value = 0.0
        manager = Mock()
        manager.variables = {}
        manager.state.get = Mock(side_effect=lambda key, default=None: (
            "exact" if key == "ace_endless_spool_match_mode" else default))
        return EndlessSpool(printer, Mock(), manager)

    def _ready(self, slot):
        ACE_INSTANCES[0].inventory[slot] = {
            "status": "ready", "color": [255, 0, 0], "material": "PLA"}

    def test_a_match_further_up_is_found(self):
        self._ready(1)          # T4 runs out
        self._ready(3)          # T6 matches
        assert self._spool().find_exact_match(4) == 6

    def test_the_search_wraps_to_the_first_ace_tool_not_to_t0(self):
        self._ready(3)          # T6 runs out
        self._ready(1)          # T4 matches
        assert self._spool().find_exact_match(6) == 4


class TestRdmInAcePath:
    """The RDM sits in the ACE path unless the printer's tool changer says
    it does not (Kobra X combo mode: a tube per inlet passes no RDM). Plain
    objects, not Mock: a tool changer without the answer must read as one."""

    def test_without_a_tool_changer_the_config_decides(self):
        from ace.tool_base import rdm_in_ace_path
        assert rdm_in_ace_path(None) is True

    def test_a_tool_changer_without_the_answer_leaves_the_rdm(self):
        from ace.tool_base import rdm_in_ace_path

        class Older:
            pass

        assert rdm_in_ace_path(Older()) is True

    def test_the_tool_changer_takes_the_rdm_out_of_the_path(self):
        from ace.tool_base import rdm_in_ace_path

        class Combo:
            def ace_rdm_in_path(self):
                return False

        assert rdm_in_ace_path(Combo()) is False

    def test_the_tool_changer_keeps_the_rdm(self):
        from ace.tool_base import rdm_in_ace_path

        class Shared:
            def ace_rdm_in_path(self):
                return True

        assert rdm_in_ace_path(Shared()) is True


class TestRunoutMonitor:
    def test_the_loaded_tools_unit_is_the_one_monitored(self):
        first = _instance(0)
        first._feed_assist_index = 2
        second = _instance(1)
        second._feed_assist_index = -1
        manager = Mock()
        manager.instances = [first, second]
        reactor = Mock()
        monitor = RunoutMonitor(Mock(), Mock(), reactor, Mock(), manager,
                                runout_debounce_count=1)
        assert monitor._get_active_assist_instance(current_tool=5) is first


class TestBaseAtStartup:
    """The base comes from the printer's tool changer; saved tool numbers
    written under an earlier base are moved to the current one."""

    def _tool_changer(self, offset):
        tool_changer = Mock()
        tool_changer.ace_tool_offset.return_value = offset
        return tool_changer

    def _start(self, variables, tool_changer=None):
        from ace.persistent_state import PersistentState
        from ace.tool_base import apply_tool_base
        printer = Mock()
        save_variables = Mock()
        save_variables.allVariables = variables
        printer.lookup_object = Mock(
            side_effect=lambda name, default=None: {
                "save_variables": save_variables}.get(name, default))
        state = PersistentState(printer, Mock())
        return apply_tool_base(tool_changer, state), state

    def test_the_tool_changer_sets_the_base(self):
        from ace.config import get_tool_base
        self._start({}, self._tool_changer(3))
        assert get_tool_base() == 3

    def test_without_a_tool_changer_the_base_is_zero(self):
        from ace.config import get_tool_base
        variables = {"ace_current_index": 2}
        moved_from, state = self._start(variables)
        assert get_tool_base() == 0 and moved_from is None
        assert variables == {"ace_current_index": 2}

    def test_tools_saved_before_the_base_move_with_it(self):
        variables = {"ace_current_index": 2, "ace_target_index": -1,
                     "ace_parked_tools": [0, 1]}
        moved_from, state = self._start(variables, self._tool_changer(3))
        assert moved_from == 0
        assert variables["ace_current_index"] == 5
        assert variables["ace_target_index"] == -1
        assert variables["ace_parked_tools"] == [3, 4]
        assert variables["ace_tool_base"] == 3
        assert {"ace_current_index", "ace_parked_tools",
                "ace_tool_base"} <= state._dirty

    def test_a_second_start_moves_nothing(self):
        variables = {"ace_current_index": 5, "ace_tool_base": 3}
        moved_from, state = self._start(variables, self._tool_changer(3))
        assert moved_from is None and variables["ace_current_index"] == 5
        assert not state._dirty

    def test_a_changed_tool_changer_moves_the_tools_again(self):
        # Three direct inlets became two: printer T5 is now T4.
        variables = {"ace_current_index": 5, "ace_tool_base": 3}
        moved_from, state = self._start(variables, self._tool_changer(2))
        assert moved_from == 3
        assert variables["ace_current_index"] == 4
        assert variables["ace_tool_base"] == 2


class TestManagerWiring:
    """[ace] tool_changer names the printer object that owns the tool
    numbers; without it the driver asks no object."""

    def _connect(self, objects, tool_changer_option=None):
        from ace.manager import AceManager
        from tests import test_manager
        setup = test_manager.TestAceManagerInitialization()
        setup.setUp()
        lookup = setup.mock_printer.lookup_object.side_effect
        setup.mock_printer.lookup_object.side_effect = (
            lambda name, default=None: objects[name]
            if name in objects else lookup(name, default))
        config_get = setup.mock_config.get.side_effect
        setup.mock_config.get.side_effect = (
            lambda key, default=None: tool_changer_option
            if key == "tool_changer" else config_get(key, default))
        setup.mock_config.error = ValueError
        setup.variables["ace_current_index"] = 2

        manager = AceManager(setup.mock_config)
        handlers = dict(c[0] for c in
                        setup.mock_printer.register_event_handler.call_args_list)
        handlers["klippy:connect"]()
        return manager, setup.variables

    def _tool_changer(self, offset):
        tool_changer = Mock()
        tool_changer.ace_tool_offset.return_value = offset
        return tool_changer

    def test_the_manager_sets_the_base_at_connect(self):
        # Before every klippy:ready handler: the tool changer reads
        # ace_current_index at ready, in printer numbers.
        from ace.config import get_tool_base
        _, variables = self._connect(
            {"my_changer": self._tool_changer(3)}, "my_changer")

        assert get_tool_base() == 3
        assert variables["ace_current_index"] == 5

    def test_without_the_option_no_object_is_asked(self):
        from ace.config import get_tool_base
        tool_changer = self._tool_changer(3)
        _, variables = self._connect({"kx_toolchanger": tool_changer})

        assert get_tool_base() == 0
        assert variables["ace_current_index"] == 2
        tool_changer.ace_tool_offset.assert_not_called()

    def test_a_tool_changer_that_does_not_exist_is_a_config_error(self):
        with pytest.raises(ValueError, match="no_such_changer"):
            self._connect({}, "no_such_changer")

    def test_the_status_names_the_tool_changer_and_the_base(self):
        manager, _ = self._connect(
            {"my_changer": self._tool_changer(3)}, "my_changer")

        status = manager.get_status()

        assert (status["tool_changer"], status["tool_base"]) == ("my_changer", 3)

    def test_the_status_keeps_the_base_when_a_field_fails(self):
        # The panel refuses slot taps until it has the base.
        manager, _ = self._connect(
            {"my_changer": self._tool_changer(3)}, "my_changer")
        manager._box_tag_tools = Mock(side_effect=RuntimeError("no state"))

        status = manager.get_status()

        assert (status["tool_changer"], status["tool_base"]) == ("my_changer", 3)

    def test_the_status_without_a_tool_changer(self):
        manager, _ = self._connect({})

        status = manager.get_status()

        assert (status["tool_changer"], status["tool_base"]) == ("", 0)
