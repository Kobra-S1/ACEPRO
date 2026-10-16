"""The tools a G-code file uses and whether the ACE can feed each of them;
ACE_CHECK_PRINT_TOOLS (commands.py) runs it first in the start macro, so a
print that needs an empty slot ends before anything moves.

The file parsing follows kx_print_tools.py of the Kobra Klipper fork, which
does the same check for the Kobra X's own tools.
"""
import re

from .config import (
    ACE_INSTANCES,
    SLOTS_PER_ACE,
    get_instance_from_tool,
    get_local_slot,
    get_tool_offset,
    normalize_ace_slot_state,
)

# Orca writes its summary about 30 KB before the end, its config after it.
SUMMARY_BYTES = 128 * 1024
# Bounds the time between two reactor turns while scanning a file without
# a summary (a few ms per chunk on a Pi-class host).
CHUNK_BYTES = 64 * 1024
FILAMENT_USED = re.compile(r'^;\s*filament used \[mm\]\s*=\s*(.+?)\s*$', re.M)
TOOL_LINE = re.compile(r'^[ \t]*T(\d+)(?=[ \t;]|$)', re.M)

# Filament present, or a spool the ACE is taking in right now.
USABLE_SLOT_STATES = ("ready", "preload", "identifying")


def tools_from_summary(text):
    """The filaments with a length above 0 in the last '; filament used
    [mm] = a, b, ...' line; None without one or with a value that is no
    number."""
    found = FILAMENT_USED.findall(text)
    if not found:
        return None
    try:
        lengths = [float(value) for value in found[-1].split(',')]
    except ValueError:
        return None
    return [tool for tool, length in enumerate(lengths) if length > 0.]


def tools_used(path, pause, chunk_bytes=CHUNK_BYTES):
    """Sorted tool numbers the file at `path` uses. The slicer's summary
    decides when the file has one; else every T<n> line, read in chunks
    with `pause()` between them so a long file does not stall the reactor."""
    with open(path, 'rb') as handle:
        handle.seek(0, 2)
        handle.seek(max(0, handle.tell() - SUMMARY_BYTES))
        summary = tools_from_summary(handle.read().decode('utf-8', 'replace'))
        if summary is not None:
            return summary
        handle.seek(0)
        tools = set()
        partial = ''
        while True:
            data = handle.read(chunk_bytes)
            if not data:
                break
            text = partial + data.decode('utf-8', 'replace')
            # A line cut by the read is finished by the next one.
            text, _, partial = text.rpartition('\n')
            tools.update(int(n) for n in TOOL_LINE.findall(text))
            pause()
        tools.update(int(n) for n in TOOL_LINE.findall(partial))
    return sorted(tools)


def unusable_tools(used):
    """(tool, reason) for each tool in `used` the ACE cannot feed, from the
    driver's inventory and the ACE's own slot state; no motion. Tools below
    the tool base belong to a printer-side tool changer and are skipped."""
    first = get_tool_offset(0)
    last = first + len(ACE_INSTANCES) * SLOTS_PER_ACE - 1
    problems = []
    for tool in used:
        if tool < first:
            continue
        instance_num = get_instance_from_tool(tool)
        if instance_num < 0:
            problems.append((tool, f"beyond the ACE tools T{first}-T{last}"))
            continue
        instance = ACE_INSTANCES[instance_num]
        slot = get_local_slot(tool, instance_num)
        if not instance.serial_mgr.is_connected():
            problems.append((tool, f"ACE {instance_num} is not connected"))
            continue
        inventory = normalize_ace_slot_state(instance.inventory[slot].get("status"))
        device_slots = instance._info.get("slots", [])
        device = normalize_ace_slot_state(
            device_slots[slot].get("status") if slot < len(device_slots) else None)
        if inventory not in USABLE_SLOT_STATES or device not in USABLE_SLOT_STATES:
            problems.append((tool, f"ACE {instance_num} slot {slot} is {device}"
                                   f" (inventory: {inventory})"))
    return problems


def tool_names(tools):
    return ", ".join(f"T{tool}" for tool in tools)
