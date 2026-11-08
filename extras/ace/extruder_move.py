"""The ACE driver's own extruder moves, sent straight to the motion planner
(no G-code). Shared by AceManager and AceInstance."""


def extruder_move(printer, gcode, length, speed, wait_for_move_end=False):
    """Move the extruder by `length` mm (relative) at `speed` mm/s.

    A zero-length move is skipped with a console note. Without
    `wait_for_move_end` the call returns as soon as the move is queued.
    """
    if length == 0:
        gcode.respond_info("ACE: Skipping zero-length extruder move")
        return

    toolhead = printer.lookup_object('toolhead')
    cur_pos = list(toolhead.get_position())  # [X, Y, Z, E]

    new_pos = cur_pos[:]
    new_pos[3] += length

    toolhead.move(new_pos, speed)
    # This move bypasses G-code: without the reset the next G1 drives
    # the extruder back to the E position G-code still remembers.
    printer.lookup_object('gcode_move').reset_last_position()
    if wait_for_move_end:
        toolhead.wait_moves()
