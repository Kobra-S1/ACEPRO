"""The panel shows and sends printer tool numbers, not ACE tool numbers.

On most printers they are the same. Behind a printer-side tool changer that
puts other tools first (Kobra X: three direct inlets, then the ACE) ACE tool 0
is printer T3, and the panel's "T0" would load the wrong spool. The tool
changer publishes the offset in its status; without that object it is 0.

The panel is imported with GTK and KlipperScreen stubbed out: only its
number handling runs here, no widget.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PANEL_PATH = Path(__file__).resolve().parent.parent / "KlipperScreen" / "acepro.py"


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


def make_panel(acepro):
    panel = acepro.Panel.__new__(acepro.Panel)
    panel.ace_instances = []
    panel.instance_data = {}
    panel.current_loaded_slot = -1
    panel.target_tool_index = -1
    panel.printer_tool_offset = 0
    panel.printer_tools_known = False
    panel.sent = []
    panel._send_gcode = panel.sent.append
    panel._screen = MagicMock()
    panel.status_label = MagicMock()
    return panel


def behind_three_direct_inlets(acepro):
    panel = make_panel(acepro)
    panel._process_rpc_status({"kx_toolchanger": {"ace_tool_offset": 3}})
    return panel


def test_a_slot_is_labelled_with_its_printer_tool(acepro):
    panel = behind_three_direct_inlets(acepro)

    assert panel._format_tool_label(0, {}) == "T3"
    assert panel._format_tool_label(5, {"rfid": True}) == "T8 (RFID)"
    assert "T3" in panel._format_tool_label_markup(0, {}, loaded=True)
    assert "T3" in panel._format_tool_label_markup(0, {"rfid": True})


def test_loading_a_slot_sends_its_printer_tool(acepro):
    panel = behind_three_direct_inlets(acepro)

    panel._load_tool(0)

    assert panel.sent == ["T3"]
    panel._screen.show_popup_message.assert_called_once_with("Loading T3...", 1)


def test_the_loaded_tool_is_named_by_its_printer_tool(acepro):
    panel = behind_three_direct_inlets(acepro)
    panel.current_loaded_slot = 1

    panel.update_slot_loaded_states()

    panel.status_label.set_text.assert_called_with("ACE: Tool T4 Loaded")


# What Klipper answers when the queried object does not exist: every asked
# field, as None (klippy/webhooks.py, QueryStatusHelper._do_query).
NO_TOOL_CHANGER = {"kx_toolchanger": {"ace_tool_offset": None}}


def test_without_a_printer_tool_changer_the_numbers_are_the_aces(acepro):
    panel = make_panel(acepro)
    panel._process_rpc_status(NO_TOOL_CHANGER)

    panel._load_tool(2)

    assert panel._format_tool_label(2, {}) == "T2"
    assert panel.sent == ["T2"]


def test_a_missing_tool_changer_does_not_stop_the_status_update(acepro):
    panel = make_panel(acepro)

    panel._process_rpc_status(
        dict(NO_TOOL_CHANGER, ace_state={"current_index": 2})
    )

    assert panel.current_loaded_slot == 2


def tap_slot(panel, local_slot=0):
    panel.instance_data = {0: {"tool_offset": 0}}
    panel._require_ready_slot = MagicMock(return_value=True)
    panel.show_load_confirmation = MagicMock()
    panel.on_slot_clicked(None, None, 0, local_slot)
    return panel.show_load_confirmation


def test_a_slot_tap_waits_for_the_first_status_reply(acepro):
    # Until then the offset is unknown and the tap would name the wrong tool.
    panel = make_panel(acepro)

    tap_slot(panel).assert_not_called()

    panel._screen.show_popup_message.assert_called_once()


def test_a_slot_tap_works_after_the_first_status_reply(acepro):
    for reply in (NO_TOOL_CHANGER, {"kx_toolchanger": {"ace_tool_offset": 3}}):
        panel = make_panel(acepro)
        panel._process_rpc_status(reply)

        tap_slot(panel, local_slot=1).assert_called_once_with(0, 1)


def test_a_reply_without_the_tool_changer_keeps_the_offset(acepro):
    # Cached printer data carries ace_state only.
    panel = behind_three_direct_inlets(acepro)

    panel._process_rpc_status({"ace_state": {"current_index": 1}})

    assert panel.printer_tool_offset == 3


def test_a_changed_offset_redraws_the_slot_labels(acepro):
    panel = make_panel(acepro)
    panel.update_slot_loaded_states = MagicMock()

    panel._process_rpc_status({"kx_toolchanger": {"ace_tool_offset": 3}})
    panel._process_rpc_status({"kx_toolchanger": {"ace_tool_offset": 3}})

    panel.update_slot_loaded_states.assert_called_once_with()
