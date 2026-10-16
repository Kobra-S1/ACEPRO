"""ACE_CHECK_PRINT_TOOLS: the tools a G-code file uses, and whether the
ACE can feed each of them, checked at print start before anything moves."""
from unittest.mock import Mock

import pytest

import ace.commands
from ace import print_tools
from ace.config import ACE_INSTANCES, INSTANCE_MANAGERS, SLOTS_PER_ACE, set_tool_base


def _instance(instance_num, statuses, connected=True):
    instance = Mock()
    instance.instance_num = instance_num
    instance.inventory = [{"status": s} for s in statuses]
    instance._info = {"slots": [{"status": s} for s in statuses]}
    instance.serial_mgr.is_connected.return_value = connected
    return instance


@pytest.fixture(autouse=True)
def clean_registry():
    set_tool_base(0)
    ACE_INSTANCES.clear()
    yield
    set_tool_base(0)
    ACE_INSTANCES.clear()


def _register(*instances):
    for instance in instances:
        ACE_INSTANCES[instance.instance_num] = instance


class TestToolsFromSummary:
    def test_filaments_with_a_length_are_the_tools(self):
        text = "; filament used [mm] = 120.5, 0.00, 33.1\n"
        assert print_tools.tools_from_summary(text) == [0, 2]

    def test_the_last_summary_line_wins(self):
        text = ("; filament used [mm] = 1, 0\n"
                "; filament used [mm] = 0, 5\n")
        assert print_tools.tools_from_summary(text) == [1]

    def test_no_summary_is_none(self):
        assert print_tools.tools_from_summary("G1 X1\nT0\n") is None

    def test_a_value_that_is_no_number_is_none(self):
        assert print_tools.tools_from_summary("; filament used [mm] = 1, abc\n") is None


class TestToolsUsed:
    def test_the_summary_decides_over_the_t_lines(self, tmp_path):
        path = tmp_path / "a.gcode"
        path.write_text("T0\nT3\nG1 X1\n; filament used [mm] = 0, 12\n")
        assert print_tools.tools_used(str(path), Mock()) == [1]

    def test_without_a_summary_the_t_lines_count(self, tmp_path):
        path = tmp_path / "a.gcode"
        path.write_text("T0\nG1 X1\n  T2 ; change\nT2\nM104 T1 S200\nT12\n")
        assert print_tools.tools_used(str(path), Mock()) == [0, 2, 12]

    def test_a_t_line_cut_by_a_chunk_is_read_whole(self, tmp_path):
        path = tmp_path / "a.gcode"
        path.write_text("G1 X1\nT13\nG1 X2\n")
        # 8-byte chunks cut "T13" after "T1".
        assert print_tools.tools_used(str(path), Mock(), chunk_bytes=8) == [13]

    def test_the_reactor_gets_a_turn_between_chunks(self, tmp_path):
        path = tmp_path / "a.gcode"
        path.write_text("G1 X1\n" * 100)
        pause = Mock()
        print_tools.tools_used(str(path), pause, chunk_bytes=64)
        assert pause.call_count >= 600 // 64

    def test_an_empty_file_uses_no_tool(self, tmp_path):
        path = tmp_path / "a.gcode"
        path.write_text("")
        assert print_tools.tools_used(str(path), Mock()) == []


class TestUnusableTools:
    def test_ready_and_transient_slots_are_usable(self):
        _register(_instance(0, ["ready", "preload", "identifying", "empty"]))
        assert print_tools.unusable_tools([0, 1, 2]) == []

    def test_an_empty_slot_is_named(self):
        _register(_instance(0, ["ready", "empty", "ready", "ready"]))
        problems = print_tools.unusable_tools([0, 1])
        assert [tool for tool, _ in problems] == [1]
        assert "empty" in problems[0][1]

    def test_the_inventory_must_agree_with_the_device(self):
        instance = _instance(0, ["ready"] * SLOTS_PER_ACE)
        _register(instance)
        instance.inventory[2]["status"] = "empty"
        assert [t for t, _ in print_tools.unusable_tools([2])] == [2]

    def test_a_numeric_device_state_is_normalized(self):
        instance = _instance(0, ["ready"] * SLOTS_PER_ACE)
        _register(instance)
        instance._info["slots"][0]["status"] = 1  # ready
        instance._info["slots"][1]["status"] = 0  # empty
        assert [t for t, _ in print_tools.unusable_tools([0, 1])] == [1]

    def test_a_tool_beyond_the_ace_tools_is_named(self):
        _register(_instance(0, ["ready"] * SLOTS_PER_ACE),
                  _instance(1, ["ready"] * SLOTS_PER_ACE))
        problems = print_tools.unusable_tools([7, 8])
        assert [tool for tool, _ in problems] == [8]
        assert "T0-T7" in problems[0][1]

    def test_the_second_ace_serves_t4_to_t7(self):
        _register(_instance(0, ["ready"] * SLOTS_PER_ACE),
                  _instance(1, ["ready", "empty", "ready", "ready"]))
        assert [t for t, _ in print_tools.unusable_tools([4, 5])] == [5]

    def test_a_disconnected_ace_is_named(self):
        _register(_instance(0, ["ready"] * SLOTS_PER_ACE, connected=False))
        problems = print_tools.unusable_tools([0])
        assert "not connected" in problems[0][1]

    def test_tools_below_the_tool_base_belong_to_the_tool_changer(self):
        set_tool_base(3)
        _register(_instance(0, ["ready", "empty", "ready", "ready"]))
        assert [t for t, _ in print_tools.unusable_tools([0, 1, 2, 3, 4])] == [4]


class TestCommand:
    @pytest.fixture
    def setup(self, tmp_path):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()
        ACE_INSTANCES[0] = _instance(0, ["ready", "empty", "ready", "ready"])
        manager = Mock()
        manager.get_ace_global_enabled.return_value = True
        sdcard = Mock()
        manager.get_printer.return_value.lookup_object.return_value = sdcard
        INSTANCE_MANAGERS[0] = manager
        yield manager, sdcard, tmp_path
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

    def _gcmd(self):
        gcmd = Mock()
        gcmd.error = Exception
        return gcmd

    def test_a_print_using_an_empty_slot_is_refused(self, setup):
        _, sdcard, tmp_path = setup
        path = tmp_path / "a.gcode"
        path.write_text("; filament used [mm] = 5, 5\n")
        sdcard.file_path.return_value = str(path)
        with pytest.raises(Exception, match="T1"):
            ace.commands.cmd_ACE_CHECK_PRINT_TOOLS(self._gcmd())

    def test_a_print_using_ready_slots_passes(self, setup):
        _, sdcard, tmp_path = setup
        path = tmp_path / "a.gcode"
        path.write_text("; filament used [mm] = 5, 0, 5\n")
        sdcard.file_path.return_value = str(path)
        gcmd = self._gcmd()
        ace.commands.cmd_ACE_CHECK_PRINT_TOOLS(gcmd)
        assert "T0, T2" in gcmd.respond_info.call_args[0][0]

    def test_no_file_printing_checks_nothing(self, setup):
        _, sdcard, _ = setup
        sdcard.file_path.return_value = None
        gcmd = self._gcmd()
        ace.commands.cmd_ACE_CHECK_PRINT_TOOLS(gcmd)
        assert "no file" in gcmd.respond_info.call_args[0][0]

    def test_with_the_ace_disabled_nothing_is_checked(self, setup):
        manager, sdcard, tmp_path = setup
        manager.get_ace_global_enabled.return_value = False
        path = tmp_path / "a.gcode"
        path.write_text("; filament used [mm] = 5, 5\n")
        sdcard.file_path.return_value = str(path)
        gcmd = self._gcmd()
        ace.commands.cmd_ACE_CHECK_PRINT_TOOLS(gcmd)
        assert "disabled" in gcmd.respond_info.call_args[0][0]

    def test_the_command_is_registered(self):
        assert "ACE_CHECK_PRINT_TOOLS" in [name for name, _, _ in ace.commands.ACE_COMMANDS]
