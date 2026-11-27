"""The spool screen's Full Unload with no slot selected asks to unload every
slot (ACE_FULL_UNLOAD TOOL=ALL) instead of refusing: the screen offers no
other way to select all. A selected slot still unloads just that slot.
Imported with GTK and KlipperScreen stubbed out.
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
    spec = importlib.util.spec_from_file_location("acepro_full_unload_all", PANEL_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


def make_panel(acepro, selected):
    panel = acepro.Panel.__new__(acepro.Panel)
    panel._screen = MagicMock()
    panel._gtk = MagicMock()
    panel.spool_selected_tool = selected
    panel.sent = []
    panel._send_gcode = lambda script: panel.sent.append(script) or True
    return panel


def answer_dialog(acepro, panel, response):
    """Press a button of the dialog the panel opened."""
    title, buttons, _label, on_response = panel._gtk.Dialog.call_args.args
    on_response(MagicMock(), response)
    return title, buttons


def test_selected_slot_unloads_only_that_slot(acepro):
    panel = make_panel(acepro, selected=5)

    panel.spool_full_unload(None)

    assert panel.sent == ["ACE_FULL_UNLOAD TOOL=5"]
    panel._gtk.Dialog.assert_not_called()


def test_no_selection_asks_before_anything_is_sent(acepro):
    panel = make_panel(acepro, selected=None)

    panel.spool_full_unload(None)

    assert panel.sent == []
    panel._gtk.Dialog.assert_called_once()


def test_confirming_unloads_all_slots(acepro):
    panel = make_panel(acepro, selected=None)
    panel.spool_full_unload(None)

    _title, buttons = answer_dialog(acepro, panel, acepro.Gtk.ResponseType.OK)

    assert panel.sent == ["ACE_FULL_UNLOAD TOOL=ALL"]
    assert [b["name"] for b in buttons] == ["Cancel", "Unload all"]


def test_cancel_sends_nothing(acepro):
    panel = make_panel(acepro, selected=None)
    panel.spool_full_unload(None)

    answer_dialog(acepro, panel, acepro.Gtk.ResponseType.CANCEL)

    assert panel.sent == []


def test_either_answer_closes_the_dialog(acepro):
    for response in (acepro.Gtk.ResponseType.OK, acepro.Gtk.ResponseType.CANCEL):
        panel = make_panel(acepro, selected=None)
        panel.spool_full_unload(None)

        answer_dialog(acepro, panel, response)

        panel._gtk.remove_dialog.assert_called_once()
