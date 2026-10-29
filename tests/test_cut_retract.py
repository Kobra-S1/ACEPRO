"""The retract before a cut: one owner per printer, relative whatever the file.

ACEPRO retracts pre_cut_retract_length before it calls the printer's
CUT_TIP. A CUT_TIP that retracts itself must not get a second retract on
top: 4 mm pulls molten tip up into the heatbreak. Both retracts are E
moves inside macros a file in absolute extrusion (M82) can reach, where a
bare `G1 E-2` means "to E -2".

The macros are rendered with Klipper's template delimiters from the
shipped config files.
"""
import configparser
import re
from pathlib import Path

import jinja2
import pytest

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def macro_gcode(cfg_file, name):
    """The gcode: block of [gcode_macro <name>] in cfg_file."""
    text = (CONFIG_DIR / cfg_file).read_text()
    match = re.search(r"^\[gcode_macro %s\]\n(.*?)(?=^\[|\Z)" % re.escape(name),
                      text, re.S | re.M)
    assert match, "%s defines no [gcode_macro %s]" % (cfg_file, name)
    body = match.group(1)
    gcode = body.split("gcode:\n", 1)[1]
    return "\n".join(line for line in gcode.splitlines()
                     if not line.lstrip().startswith("#"))


def render(gcode, params=None, temperature=220.):
    """Rendered lines, as Klipper's gcode_macro renders a template."""
    env = jinja2.Environment("{%", "%}", "{", "}")
    printer = {
        "extruder": {"temperature": temperature, "target": temperature},
        "configfile": {"settings": {"extruder": {"min_extrude_temp": 170}}},
        "toolhead": {"homed_axes": "xyz"},
        "gcode_macro CUT_TIP": {},
    }
    out = env.from_string(gcode).render(
        printer=printer, params=params or {},
        action_respond_info=lambda msg: "")
    return [line.strip() for line in out.splitlines() if line.strip()]


def assert_e_moves_relative_and_restored(lines):
    """Each G1 E runs after M83 inside a saved G-code state that is
    restored before the macro goes on: the caller's mode comes back."""
    e_moves = [i for i, line in enumerate(lines) if line.startswith("G1 E")]
    assert e_moves, "no E move rendered:\n%s" % "\n".join(lines)
    for i in e_moves:
        before = lines[:i]
        saves = [j for j, line in enumerate(before)
                 if line.startswith("SAVE_GCODE_STATE")]
        assert saves, "G1 E with no saved state before it:\n%s" % "\n".join(lines)
        assert "M83" in before[saves[-1]:], \
            "G1 E not in relative extrusion:\n%s" % "\n".join(lines)
        after = lines[i + 1:]
        restore = next((j for j, line in enumerate(after)
                        if line.startswith("RESTORE_GCODE_STATE")), None)
        assert restore is not None, \
            "state not restored after G1 E:\n%s" % "\n".join(lines)
        assert not any(line.startswith(("CUT_TIP", "G1 X", "G1 Y"))
                       for line in after[:restore]), \
            "the restore comes after the cut moves:\n%s" % "\n".join(lines)


def test_ace_pre_cut_retract_runs_relative_whatever_the_file():
    lines = render(macro_gcode("ace_macros_generic.cfg",
                               "_ACE_PREPARE_FOR_RETRACTION"),
                   params={"PRE_CUT_RETRACT": "2.0"})
    assert "G1 E-2.0 F600" in lines, lines
    assert_e_moves_relative_and_restored(lines)


def test_ace_pre_cut_retract_of_zero_moves_no_filament():
    lines = render(macro_gcode("ace_macros_generic.cfg",
                               "_ACE_PREPARE_FOR_RETRACTION"),
                   params={"PRE_CUT_RETRACT": "0.0"})
    assert not any(line.startswith("G1 E") for line in lines), lines
    assert "CUT_TIP" in lines


@pytest.mark.parametrize("printer", ["K3", "K3M"])
def test_k3_cut_tip_retract_runs_relative_whatever_the_file(printer):
    lines = render(macro_gcode("printer_%s.cfg" % printer, "CUT_TIP"))
    assert "G1 E-2 F300" in lines, lines
    assert_e_moves_relative_and_restored(lines)


def ace_setting(printer, key):
    parser = configparser.ConfigParser(inline_comment_prefixes=("#",),
                                       strict=False, interpolation=None)
    parser.read(CONFIG_DIR / ("ace_%s.cfg" % printer))
    return float(parser["ace"][key])


def cut_tip_retracts(printer):
    """Whether the printer's own CUT_TIP has an extruder retract, read from
    the text: rendering the KS1's needs every one of its own variables."""
    return any(line.strip().startswith("G1 E-")
               for line in macro_gcode("printer_%s.cfg" % printer,
                                       "CUT_TIP").splitlines())


@pytest.mark.parametrize("printer", ["K3", "K3M", "KS1", "KS1M"])
def test_one_retract_before_the_cut(printer):
    retract = ace_setting(printer, "pre_cut_retract_length")
    if cut_tip_retracts(printer):
        assert retract == 0, \
            "%s: CUT_TIP retracts, and ACEPRO adds %s mm" % (printer, retract)
    else:
        assert retract > 0, "%s: nothing retracts before the cut" % printer


def test_kobra_x_cut_tip_owns_the_retract():
    # The Kobra X's CUT_TIP lives in its Klipper fork and retracts 2 mm
    # before the cut (the stock firmware's own prepare retract).
    assert ace_setting("KX", "pre_cut_retract_length") == 0
