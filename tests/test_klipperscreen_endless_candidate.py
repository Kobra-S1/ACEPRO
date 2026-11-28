"""The panel marks the slot endless spool would swap to on a runout.

The driver publishes that prediction as ace_state.endless_spool_next_tool
while endless spool is on. The panel shows it as a badge on the candidate
slot, follows its own Endless Spool switch at once, and ignores a stale
"off" report that a poll delivers before the driver has run the switch's
command.

The panel is imported with GTK and KlipperScreen stubbed out; the slot
widgets and the Moonraker websocket are recording fakes.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PANEL_PATH = Path(__file__).resolve().parent.parent / "KlipperScreen" / "acepro.py"

CANDIDATE_TOOL = 2


@pytest.fixture(scope="module")
def acepro():
    screen_panel = types.ModuleType("ks_includes.screen_panel")
    screen_panel.ScreenPanel = type("ScreenPanel", (), {})
    stubs = {
        "gi": MagicMock(),
        "gi.repository": MagicMock(),
        "ks_includes": MagicMock(),
        "ks_includes.screen_panel": screen_panel,
        "ks_includes.widgets": MagicMock(),
        "ks_includes.widgets.keypad": MagicMock(),
    }
    spec = importlib.util.spec_from_file_location("acepro_under_test", PANEL_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class FakeWebsocket:
    """Records Moonraker requests; a test answers one by calling its callback."""

    def __init__(self):
        self.requests = []

    def send_method(self, method, params=None, callback=None, *args):
        self.requests.append((method, params, callback))
        return True

    def methods(self):
        return [method for method, _, _ in self.requests]


class Badge:
    def __init__(self):
        self.visible = False

    def set_visible(self, visible):
        self.visible = visible


def make_panel(acepro, endless_enabled, next_tool):
    panel = acepro.Panel.__new__(acepro.Panel)
    panel.ace_instances = [0]
    panel.instance_data = {0: {
        "tool_offset": 0,
        "slot_buttons": [MagicMock() for _ in range(4)],
        "endless_badges": [Badge() for _ in range(4)],
        "inventory": [{"status": "ready"} for _ in range(4)],
    }}
    panel.current_loaded_slot = 0
    panel.target_tool_index = -1
    panel.printer_tool_offset = 0
    panel.printer_tools_known = True
    panel.endless_spool_enabled = endless_enabled
    panel.endless_spool_next_tool = next_tool
    panel._pending_toggle_states = {}
    panel.endless_spool_status = MagicMock()
    panel.endless_spool_switch = MagicMock()
    panel._update_ace_pro_switch_lock = lambda: None
    panel.ws = FakeWebsocket()
    panel._screen = MagicMock()
    panel._screen._ws = panel.ws
    panel.update_slot_loaded_states()
    return panel


def switch_at(active):
    switch = MagicMock()
    switch.get_active.return_value = active
    return switch


def badges_shown(panel):
    return [tool for tool, badge in enumerate(panel.instance_data[0]["endless_badges"])
            if badge.visible]


def test_the_predicted_candidate_gets_the_badge(acepro):
    panel = make_panel(acepro, endless_enabled=False, next_tool=-1)

    panel._process_rpc_status({"ace_state": {
        "endless_spool_enabled": True, "endless_spool_next_tool": CANDIDATE_TOOL}})

    assert badges_shown(panel) == [CANDIDATE_TOOL]


def test_switching_off_hides_the_badge_before_the_driver_answers(acepro):
    panel = make_panel(acepro, endless_enabled=True, next_tool=CANDIDATE_TOOL)
    assert badges_shown(panel) == [CANDIDATE_TOOL]

    panel.on_endless_spool_toggled(switch_at(False), None)

    assert badges_shown(panel) == []


def test_switching_on_queries_the_prediction_once_the_command_ran(acepro):
    panel = make_panel(acepro, endless_enabled=False, next_tool=-1)

    panel.on_endless_spool_toggled(switch_at(True), None)

    assert panel.ws.methods() == ["printer.gcode.script"]
    _, params, gcode_done = panel.ws.requests[0]
    assert params == {"script": "ACE_ENABLE_ENDLESS_SPOOL"}
    assert gcode_done() is False  # an idle callback returning True repeats
    assert panel.ws.methods() == ["printer.gcode.script", "printer.objects.query"]

    _, _, query_done = panel.ws.requests[1]
    query_done({"result": {"status": {"ace_state": {
        "endless_spool_enabled": True, "endless_spool_next_tool": CANDIDATE_TOOL}}}})
    assert badges_shown(panel) == [CANDIDATE_TOOL]


def test_a_stale_off_report_after_switching_on_is_ignored(acepro):
    panel = make_panel(acepro, endless_enabled=False, next_tool=-1)
    panel.on_endless_spool_toggled(switch_at(True), None)

    panel._process_rpc_status({"ace_state": {
        "endless_spool_enabled": False, "endless_spool_next_tool": -1}})
    assert panel.endless_spool_enabled is True

    panel._process_rpc_status({"ace_state": {
        "endless_spool_enabled": True, "endless_spool_next_tool": CANDIDATE_TOOL}})
    assert badges_shown(panel) == [CANDIDATE_TOOL]


def test_the_switch_off_still_applies_when_no_toggle_is_pending(acepro):
    panel = make_panel(acepro, endless_enabled=True, next_tool=CANDIDATE_TOOL)

    panel._process_rpc_status({"ace_state": {
        "endless_spool_enabled": False, "endless_spool_next_tool": -1}})

    assert panel.endless_spool_enabled is False
    assert badges_shown(panel) == []
