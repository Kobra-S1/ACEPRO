"""RESUME reloads the tool the printer has loaded.

Behind a printer-side tool changer that owns T<n> (Kobra X) the changer's
record is the loaded tool: it loads its direct tools without the ACE
driver, whose record then still names the last ACE tool. RESUME finds the
changer by the name the driver publishes ([ace] tool_changer, in the
ace_state status), never by a fixed object name.

The macro is rendered from the shipped config with Klipper's template
delimiters; printer objects the tool choice does not read are left
undefined.
"""
import jinja2

from tests.test_cut_retract import macro_gcode


def render_resume(printer):
    env = jinja2.Environment("{%", "%}", "{", "}",
                             undefined=jinja2.ChainableUndefined)
    base = {
        "pause_resume": {"is_paused": 1},
        "output_pin ACE_Pro": {"value": 1},
        "gcode_macro _ACE_STATE": {"active": 5},
        "save_variables": {"variables": {"ace_target_index": -1}},
    }
    out = env.from_string(macro_gcode("printer_generic_macros.cfg", "RESUME")).render(
        printer=dict(base, **printer), params={"PURGE": "0"}, etemp=0, zhop=0,
        action_respond_info=lambda msg: "")
    return [line.strip() for line in out.splitlines() if line.strip()]


def tool_loads(lines):
    return [line for line in lines if line[:1] == "T" and line[1:].isdigit()]


def test_resume_reloads_the_tool_changers_tool():
    lines = render_resume({
        "ace_state": {"tool_changer": "my_changer"},
        "my_changer": {"tool": 1},
    })
    assert tool_loads(lines) == ["T1"], lines


def test_resume_without_a_tool_changer_reloads_the_ace_tool():
    lines = render_resume({"ace_state": {"tool_changer": ""}})
    assert tool_loads(lines) == ["T5"], lines


def test_resume_without_a_tool_changer_retries_an_unconfirmed_change():
    lines = render_resume({
        "ace_state": {"tool_changer": ""},
        "save_variables": {"variables": {"ace_target_index": 6}},
    })
    assert tool_loads(lines) == ["T6"], lines
