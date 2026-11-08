"""The ACE driver's own extruder moves, sent straight to the motion planner
(no G-code). Shared by AceManager and AceInstance."""


def extruder_move(printer, gcode, length, speed, wait_for_move_end=False):
    """Move the extruder by `length` mm (relative) at `speed` mm/s.

    A zero-length move is skipped with a console note. The move is handed
    to the steppers before this returns; only `wait_for_move_end` also
    waits for it to finish.
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
    else:
        # Klipper otherwise holds the newest moves back for its look-ahead,
        # up to a second's worth after an idle toolhead: a caller that then
        # blocks on the ACE would run against an extruder that has not
        # started (the ACE pulls against the gears, a sensor stop overshoots).
        toolhead.get_last_move_time()
