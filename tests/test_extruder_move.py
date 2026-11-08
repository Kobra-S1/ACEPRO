"""extruder_move: the ACE driver's own extruder moves through the planner."""

from unittest.mock import Mock

from ace.extruder_move import extruder_move


def printer_with(toolhead, gcode_move):
    printer = Mock()
    printer.lookup_object.side_effect = lambda name, default=None: {
        "toolhead": toolhead, "gcode_move": gcode_move}.get(name, default)
    return printer


def test_moves_relative_to_the_current_position():
    toolhead = Mock()
    toolhead.get_position.return_value = [1, 2, 3, 4]

    extruder_move(printer_with(toolhead, Mock()), Mock(), 5, 50)

    toolhead.move.assert_called_once_with([1, 2, 3, 9], 50)
    toolhead.wait_moves.assert_not_called()


def test_waits_for_the_move_when_asked():
    toolhead = Mock()
    toolhead.get_position.return_value = [10, 20, 30, 40]

    extruder_move(printer_with(toolhead, Mock()), Mock(), 3, 15,
                  wait_for_move_end=True)

    toolhead.move.assert_called_once_with([10, 20, 30, 43], 15)
    toolhead.wait_moves.assert_called_once()


def test_skips_a_zero_length_move():
    toolhead = Mock()
    gcode = Mock()

    extruder_move(printer_with(toolhead, Mock()), gcode, 0, 25,
                  wait_for_move_end=True)

    toolhead.move.assert_not_called()
    assert "zero-length" in gcode.respond_info.call_args.args[0]


def test_the_gcode_position_follows_the_move():
    # The move bypasses G-code. Left behind, the next G1 "returns" the
    # extruder to the old E: after an unload retract, a fast forward
    # spin of the whole retract length with no filament in the gear.
    toolhead = Mock()
    toolhead.get_position.return_value = [1, 2, 3, 4]
    order = Mock()
    toolhead.move = order.move
    gcode_move = Mock()
    gcode_move.reset_last_position = order.reset

    extruder_move(printer_with(toolhead, gcode_move), Mock(), -80, 15)

    assert [c[0] for c in order.mock_calls] == ["move", "reset"]


def test_a_move_not_waited_for_is_handed_to_the_steppers_at_once():
    # Klipper parks a queued move in its look-ahead, up to a second's worth
    # after an idle toolhead. A caller that then blocks on the ACE (a
    # retract, a sensor poll) would run against an extruder that has not
    # started: the ACE pulls against the gears, a sensor stop overshoots.
    toolhead = Mock()
    toolhead.get_position.return_value = [1, 2, 3, 4]
    order = Mock()
    toolhead.move = order.move
    toolhead.get_last_move_time = order.flush

    extruder_move(printer_with(toolhead, Mock()), Mock(), 5, 50)

    assert [c[0] for c in order.mock_calls] == ["move", "flush"]
