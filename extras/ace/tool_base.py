"""Where the ACE's tool numbering starts, set once at klippy:connect.

A printer-side tool changer that puts its own tools first (Kobra X: the
direct-fed inlets of [kx_toolchanger]) owns the number of the first ACE's
slot 0 and publishes it as ace_tool_offset(). Without one the ACE starts at
T0. Tool numbers saved under an earlier base are moved to the current one,
so a loaded or parked tool survives a change of the tool changer's layout.
"""
import logging

from .config import set_tool_base

TOOL_CHANGER = "kx_toolchanger"
BASE_KEY = "ace_tool_base"
# Saved state holding global tool numbers; -1 means none.
TOOL_KEYS = ("ace_current_index", "ace_target_index")
TOOL_LIST_KEYS = ("ace_parked_tools",)


def tool_base_of(printer):
    tool_changer = printer.lookup_object(TOOL_CHANGER, None)
    if tool_changer is None:
        return 0
    return tool_changer.ace_tool_offset()


def apply_tool_base(printer, state):
    """Set the base from the printer's tool changer and move saved tool
    numbers onto it, in memory (flushed with the next state flush, the base
    with them). Returns the base they were moved from, or None."""
    base = tool_base_of(printer)
    set_tool_base(base)
    saved_base = state.get(BASE_KEY, 0)
    shift = base - saved_base
    if not shift:
        return None
    for key in TOOL_KEYS:
        tool = state.get(key, -1)
        if isinstance(tool, int) and tool >= 0:
            state.set(key, tool + shift)
    for key in TOOL_LIST_KEYS:
        tools = state.get(key, None)
        if tools:
            state.set(key, [tool + shift for tool in tools])
    state.set(BASE_KEY, base)
    logging.info("ACE: saved tool numbers moved from base %d to %d",
                 saved_base, base)
    return saved_base
