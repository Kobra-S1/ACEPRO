"""The slot's Load and Unload dialogs offer a third choice, Force Unload:
ACE_SMART_UNLOAD TOOL=<n> for a filament the driver has no tool on record
for. "Empty" left the gear screen's material list, which Save never
accepted. Imported with GTK and KlipperScreen stubbed out.
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
    spec = importlib.util.spec_from_file_location("acepro_force_unload", PANEL_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


def make_panel(acepro):
    panel = acepro.Panel.__new__(acepro.Panel)
    panel._screen = MagicMock()
    panel.sent = []
    panel._send_gcode = lambda script: panel.sent.append(script) or True
    return panel


def test_force_unload_sends_the_printer_tool(acepro):
    panel = make_panel(acepro)

    panel._force_unload(5)

    assert panel.sent == ["ACE_SMART_UNLOAD TOOL=5"]


@pytest.mark.parametrize("action", ["Load", "Unload"])
def test_the_slot_dialogs_offer_a_force_unload_as_third_choice(acepro, action):
    panel = make_panel(acepro)

    names = [b["name"] for b in panel._slot_dialog_buttons(action)]

    assert names == ["Cancel", action, "Force Unload"]


def test_empty_is_no_longer_a_selectable_material(acepro):
    panel = make_panel(acepro)

    options = panel.material_options()

    assert "Empty" not in options
    assert options == list(acepro.Panel.FILAMENT_TEMP_DEFAULTS.keys())
