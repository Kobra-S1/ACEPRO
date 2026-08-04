"""Regression test: the Feed/Retract amount dialog must build a visible keypad.

Exercises the real ``Panel._show_spool_amount_input`` against the *installed*
KlipperScreen ``Keypad`` widget, so an API drift between the two (which
previously left an empty black page on the touchscreen) turns this test red.

Only the KlipperScreen host objects (screen, theme/gtk helper) are faked --
they are the external boundary; the dialog builder and the Keypad widget
under test are real.

Requires a KlipperScreen checkout at ~/KlipperScreen (override with
KLIPPERSCREEN_DIR) and a working GTK display; skips otherwise.
"""

import builtins
import importlib
import os
import sys
from pathlib import Path

import pytest

KLIPPERSCREEN_DIR = Path(os.environ.get("KLIPPERSCREEN_DIR", Path.home() / "KlipperScreen"))
PANEL_DIR = Path(__file__).resolve().parent.parent / "KlipperScreen"

pytestmark = pytest.mark.skipif(
    not KLIPPERSCREEN_DIR.is_dir(),
    reason=f"KlipperScreen not found at {KLIPPERSCREEN_DIR} (set KLIPPERSCREEN_DIR)",
)


def _load_gtk_or_skip():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    initialized, _argv = Gtk.init_check()
    if not initialized:
        pytest.skip("no GTK display available")
    return Gtk


def _import_panel_module():
    """Import acepro.py with the installed KlipperScreen on the path."""
    for path in (str(KLIPPERSCREEN_DIR), str(PANEL_DIR)):
        if path not in sys.path:
            sys.path.insert(0, path)
    # KlipperScreen installs gettext's ``_`` at startup; the keypad module
    # relies on it at import time.
    if not hasattr(builtins, "_"):
        builtins._ = lambda text: text
    return importlib.import_module("acepro")


class FakeKlippyGtk:
    """Stand-in for ks_includes.KlippyGtk: returns real Gtk buttons."""

    font_size = 16
    bsidescale = 0.65

    def __init__(self, gtk_module):
        self._gtk_module = gtk_module

    def Button(self, image_name=None, label=None, style=None, scale=None,
               position=None, lines=None, **kwargs):
        button = self._gtk_module.Button(label=label or kwargs.get("label", ""))
        return button


class FakeScreen:
    """Stand-in for KlipperScreen's Screen object."""

    vertical_mode = False

    def __init__(self, gtk_module):
        self.gtk = FakeKlippyGtk(gtk_module)
        self.popup_messages = []

    def show_popup_message(self, message, level=3):
        self.popup_messages.append(message)


def _make_panel(acepro, gtk_module):
    """Panel instance with only the state _show_spool_amount_input needs."""
    panel = acepro.Panel.__new__(acepro.Panel)
    panel._screen = FakeScreen(gtk_module)
    panel._gtk = panel._screen.gtk
    panel.content = gtk_module.Box()
    panel.spool_keypad = None
    return panel


def test_retract_amount_dialog_builds_keypad():
    gtk_module = _load_gtk_or_skip()
    acepro = _import_panel_module()
    panel = _make_panel(acepro, gtk_module)

    panel._show_spool_amount_input("Retract", lambda value: None)

    children = panel.content.get_children()
    assert children, (
        "content area is empty after opening the Retract dialog -- this is "
        "the black page the user sees on the touchscreen"
    )
    assert isinstance(panel.spool_keypad, acepro.Keypad)


def test_amount_dialog_ok_reaches_callback():
    """The OK path must deliver the entered value to the panel callback."""
    gtk_module = _load_gtk_or_skip()
    acepro = _import_panel_module()
    panel = _make_panel(acepro, gtk_module)
    received = []

    panel._show_spool_amount_input("Feed", received.append)
    panel.spool_keypad.entry.set_text("25")
    panel.spool_keypad.ok_btn.clicked()

    assert received == [25.0]
