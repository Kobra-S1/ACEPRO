"""What the printer's tool changer tells this driver. The manager looks
it up by the [ace] tool_changer name, never imports it; every answer has a
default for printers without one (tool_changer None).

Where the ACE's tool numbering starts, set once at klippy:connect: a
printer-side tool changer that puts its own tools first (Kobra X: the
direct-fed inlets of [kx_toolchanger]) owns the number of the first ACE's
slot 0 and publishes it as ace_tool_offset(). Without one the ACE starts at
T0. Tool numbers saved under an earlier base are moved to the current one,
so a loaded or parked tool survives a change of the tool changer's layout.

Whether the RDM sits in the ACE's path (ace_rdm_in_path()): a tool changer
whose layout routes the ACE past no RDM takes the configured one out of
use; without an answer the [ace] config decides.
"""
import logging

from .config import set_tool_base

BASE_KEY = "ace_tool_base"
# Saved state holding global tool numbers; -1 means none.
TOOL_KEYS = ("ace_current_index", "ace_target_index")
TOOL_LIST_KEYS = ("ace_parked_tools",)


def tool_base_of(tool_changer):
    if tool_changer is None:
        return 0
    return tool_changer.ace_tool_offset()


def rdm_in_ace_path(tool_changer):
    """Whether the configured RDM sensor is in the path the ACE feeds
    through. True without a tool changer, or with one that cannot say."""
    answer = getattr(tool_changer, "ace_rdm_in_path", None)
    if answer is None:
        return True
    return answer() is not False


def apply_tool_base(tool_changer, state):
    """Set the base from the printer's tool changer and move saved tool
    numbers onto it, in memory (flushed with the next state flush, the base
    with them). Returns the base they were moved from, or None."""
    base = tool_base_of(tool_changer)
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
