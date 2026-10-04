"""The ACE Pro panel's main screen must fit the width KlipperScreen gives it.

Built for real: the installed KlipperScreen's KlippyGtk (its button and icon
sizes follow the screen size) and stylesheet, real Gtk widgets, only the
screen and printer objects faked. On a 480x320 display the panel asked for
523 px of the 432 px content area, and the Utilities button, the match mode
and the fourth slot were cut off at the right edge.

Needs python3-gi, a display and a KlipperScreen checkout at ~/KlipperScreen
(override with KLIPPERSCREEN_DIR); skips otherwise. The project venv has no
GTK, so run it with the system python as well:
    python3 tests/test_klipperscreen_panel_width.py
"""

import builtins
import importlib
import os
import sys
import types
import unittest
from pathlib import Path

KLIPPERSCREEN_DIR = Path(os.environ.get("KLIPPERSCREEN_DIR", Path.home() / "KlipperScreen"))
PANEL_DIR = Path(__file__).resolve().parent.parent / "KlipperScreen"


def _gtk_or_skip():
    if not KLIPPERSCREEN_DIR.is_dir():
        raise unittest.SkipTest(f"KlipperScreen not found at {KLIPPERSCREEN_DIR}")
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, Gtk
    except (ImportError, ValueError) as e:
        raise unittest.SkipTest(f"no GTK: {e}")
    if not Gtk.init_check()[0]:
        raise unittest.SkipTest("no GTK display available")
    return Gtk, Gdk


class _MainConfig:
    def get(self, key, default=None):
        return {"font_size": "small"}.get(key, default)

    def getboolean(self, key, fallback=False, **kwargs):
        return fallback


def _make_panel(width, height):
    """The real Panel on a fake screen of the given size, one ACE configured."""
    Gtk, Gdk = _gtk_or_skip()
    for path in (str(KLIPPERSCREEN_DIR), str(PANEL_DIR)):
        if path not in sys.path:
            sys.path.insert(0, path)
    if not hasattr(builtins, "_"):
        builtins._ = lambda text: text
    from ks_includes.KlippyGtk import KlippyGtk

    idle = types.SimpleNamespace(reset_timeout=lambda *args: None)
    screen = types.SimpleNamespace(
        width=width, height=height, vertical_mode=False, theme="z-bolt",
        files=None, screensaver=idle, lock_screen=idle,
        _config=types.SimpleNamespace(get_main_config=_MainConfig),
        printer=types.SimpleNamespace(
            state="ready", data={},
            get_config_section=lambda name: {"ace_count": "1"} if name == "ace" else None,
            get_config_section_list=lambda: ["ace"],
        ),
        _ws=types.SimpleNamespace(
            connected=True, send_method=lambda *args, **kwargs: True,
            api=types.SimpleNamespace(gcode_script=lambda script: None),
        ),
        show_popup_message=lambda *args, **kwargs: None,
    )
    screen.gtk = KlippyGtk(screen)
    # KlipperScreen's own stylesheet at this screen's font size: its paddings
    # and its wide touch scrollbar are part of what has to fit.
    css = (KLIPPERSCREEN_DIR / "styles" / "base.css").read_text()
    css = css.replace("KS_FONT_SIZE", str(round(screen.gtk.font_size)))
    provider = Gtk.CssProvider()
    provider.load_from_data(css.encode())
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    acepro = importlib.import_module("acepro")
    return acepro.Panel(screen, "ACE Pro"), screen, Gtk


def _labels(widget, Gtk):
    found = []
    if isinstance(widget, Gtk.Label):
        found.append(widget.get_text())
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found.extend(_labels(child, Gtk))
    return found


def test_the_main_screen_fits_a_480_wide_display():
    panel, screen, _gtk = _make_panel(480, 320)

    # The match mode button is relabelled when the mode changes.
    for mode in ("exact", "material", "next"):
        panel._set_match_mode_ui(mode)
        needed = panel.content.get_preferred_width().minimum_width

        assert needed <= screen.gtk.content_width, (
            f"with match mode '{mode}' the panel needs {needed} px, the "
            f"content area is {screen.gtk.content_width} px wide"
        )


def test_the_main_screen_needs_no_scrolling_on_a_320_high_display():
    # A scrollbar there costs 36 px of width for nothing. Fonts differ between
    # machines: 296 of 298 px fitted on the development host and still
    # scrolled on the printer's Pi, so the layout has to leave some room.
    headroom = 10
    panel, screen, _gtk = _make_panel(480, 320)
    for slot in panel.instance_data[0]["inventory"]:
        slot.update(status="ready", material="PLA", temp=200, color=[255, 255, 0])
    panel.return_to_main_screen()
    scrolled = panel.content.get_children()[0]

    needed = scrolled.get_child().get_preferred_height().minimum_height

    assert needed <= screen.gtk.content_height - headroom, (
        f"the main screen is {needed} px high, the content area "
        f"{screen.gtk.content_height:.0f} px"
    )


def test_the_other_views_fit_a_480_wide_display():
    panel, screen, _gtk = _make_panel(480, 320)
    views = {
        "utilities": lambda: panel.show_spool_panel(None, reset_selection=True),
        "dryer": lambda: panel.show_dryer_panel(None),
        "slot settings": lambda: panel.show_slot_settings(None, 0, 0),
    }

    for name, show in views.items():
        panel.return_to_main_screen()
        show()
        needed = panel.content.get_preferred_width().minimum_width

        assert needed <= screen.gtk.content_width, (
            f"the {name} view needs {needed} px, the content area is "
            f"{screen.gtk.content_width} px wide"
        )


def test_a_narrow_display_still_offers_every_control():
    panel, _screen, Gtk = _make_panel(480, 320)

    labels = _labels(panel.content, Gtk)

    assert "Utilities" in labels
    assert "ACE Pro:" in labels
    assert any(text.startswith("Endless") for text in labels), labels
    assert panel.match_mode_button.get_label() == "Exact"


def test_a_wide_display_keeps_the_full_labels():
    panel, screen, Gtk = _make_panel(800, 480)

    labels = _labels(panel.content, Gtk)

    assert "Endless Spool:" in labels and "Match Mode:" in labels, labels
    assert panel.content.get_preferred_width().minimum_width <= screen.gtk.content_width


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    try:
        for test in tests:
            test()
    except unittest.SkipTest as skip:
        print(f"skipped: {skip}")
    else:
        print(f"ok: {len(tests)} tests")
