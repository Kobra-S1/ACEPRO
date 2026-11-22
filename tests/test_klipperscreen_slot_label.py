"""A slot's material/temperature label reads the same whether the slot view
was just built (switching to another ACE) or refreshed by a status update.

Field symptom: switching units briefly showed "Unknown 0°C" on every slot
without RFID data (temp 0) until the next update rewrote it without the
temperature. Imported with GTK and KlipperScreen stubbed out.
"""
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

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
    spec = importlib.util.spec_from_file_location("acepro_slot_label", PANEL_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    # Each Gtk.Label() a separate mock, so the slot label can be told apart
    module.Gtk.Label = Mock(side_effect=lambda *a, **k: Mock())
    return module


# Shapes seen in ace_instance_N.slots (192.168.1.20, 2026-11-22) plus an
# RFID spool and a slot busy feeding.
SLOTS = {
    "no_rfid": {"status": "ready", "material": "Unknown", "temp": 0,
                "color": [0, 0, 0], "rfid": False},
    "rfid_pla": {"status": "ready", "material": "PLA", "temp": 210,
                 "color": [255, 0, 0], "rfid": True},
    "empty": {"status": "empty", "material": "", "temp": 0,
              "color": [0, 0, 0], "rfid": False},
    "feeding": {"status": "feeding", "material": "PETG", "temp": 240,
                "color": [0, 0, 255], "rfid": False},
}


def make_panel(acepro, slot, portrait):
    panel = acepro.Panel.__new__(acepro.Panel)
    panel._gtk = MagicMock()
    panel.current_instance = 0
    panel._is_portrait = lambda: portrait
    panel.set_slot_color = Mock()
    panel.update_slot_loaded_states = Mock()
    panel.instance_data = {0: {
        "inventory": [dict(slot) for _ in range(4)],
        "tool_offset": 0,
        "slot_buttons": [], "slot_labels": [], "slot_color_boxes": [],
        "slot_tool_labels": [], "slot_gear_buttons": [],
    }}
    return panel


def built_label(panel, portrait):
    panel._create_slot_button(0, 0, 0, portrait=portrait)
    return panel.instance_data[0]["slot_labels"][-1].set_text.call_args.args[0]


def updated_label(panel, slot):
    panel.update_slots_from_data(0, [dict(slot) for _ in range(4)])
    return panel.instance_data[0]["slot_labels"][0].set_text.call_args.args[0]


@pytest.mark.parametrize("portrait", [False, True])
def test_switching_to_a_slot_without_temperature_shows_no_0_degrees(acepro, portrait):
    panel = make_panel(acepro, SLOTS["no_rfid"], portrait)

    assert "0°C" not in built_label(panel, portrait)


@pytest.mark.parametrize("portrait", [False, True])
@pytest.mark.parametrize("name", sorted(SLOTS))
def test_built_and_updated_label_agree(acepro, name, portrait):
    panel = make_panel(acepro, SLOTS[name], portrait)
    built = built_label(panel, portrait)

    assert updated_label(panel, SLOTS[name]) == built


@pytest.mark.parametrize("name, portrait, expected", [
    ("rfid_pla", False, "PLA\n210°C"),
    ("rfid_pla", True, "PLA  210°C"),
    ("no_rfid", False, "Unknown\n "),
    ("no_rfid", True, "Unknown"),
    ("empty", False, "Empty\n "),
    ("empty", True, "Empty"),
    ("feeding", False, "PETG\n240°C (Feeding)"),
])
def test_label_text(acepro, name, portrait, expected):
    panel = make_panel(acepro, SLOTS[name], portrait)

    assert built_label(panel, portrait) == expected
