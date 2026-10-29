"""Rollback assist: the ACE follows while the extruder pulls its filament back.

An ACE2 clamps the filament whenever it is neither feeding nor assisting, so
an extruder-only retract slips against it (on a Kobra X, 100 mm of retract
moved the filament about 40 mm and pulled on the toolhead's inlet fitting).
The protocol says whether a unit needs this: an ACE1 lets go by itself.
"""
import logging
from contextlib import contextmanager

# How long the ACE has to acknowledge the start or the stop.
ACK_TIMEOUT_S = 5.0
ACK_POLL_S = 0.1


@contextmanager
def rollback_assist(instance, slot):
    """While the block runs, `instance`'s ACE follows a pull on `slot`'s
    filament. Raises ValueError when the ACE refuses to start, before the
    block runs; the assist is stopped however the block ends. A stop that
    fails raises ValueError too, unless the block failed: its error is the
    one raised, the stop's goes to the console."""
    protocol = instance.protocol
    start = protocol.build_start_rollback_assist_request(slot)
    if start is None:
        yield
        return
    name = f"ACE[{instance.instance_num}]"
    _send_and_confirm(instance, start, f"start rollback assist on slot {slot}",
                      "the extruder would slip against the ACE's clamp")
    instance.gcode.respond_info(
        f"{name}: Rollback assist on slot {slot} - the ACE follows the "
        f"extruder's retract")
    try:
        yield
    except Exception:
        # The block's error is the cause; a stop failing after it (the ACE
        # gone from USB) must not replace it.
        try:
            _stop(instance, slot)
        except Exception as e:
            logging.exception("%s: stopping rollback assist after a failed"
                              " retract", name)
            instance.gcode.respond_info(f"{name}: {e}")
        raise
    _stop(instance, slot)


def _stop(instance, slot):
    _send_and_confirm(instance,
                      instance.protocol.build_stop_unwind_filament_request(slot),
                      f"stop rollback assist on slot {slot}",
                      "it may still be assisting")
    # The ACE2 is busy while assisting; the next command waits for it.
    instance.wait_ready()
    instance.gcode.respond_info(
        f"ACE[{instance.instance_num}]: Rollback assist on slot {slot} stopped")


def _send_and_confirm(instance, request, what, consequence):
    reply = {}
    instance.send_request(request, lambda response: reply.setdefault("r", response))
    reactor = instance.reactor
    deadline = reactor.monotonic() + ACK_TIMEOUT_S
    while "r" not in reply and reactor.monotonic() < deadline:
        reactor.pause(reactor.monotonic() + ACK_POLL_S)
    response = reply.get("r")
    if not response or response.get("code") != 0:
        detail = response.get("msg", "no message") if response else "no answer"
        raise ValueError(
            f"ACE[{instance.instance_num}]: could not {what} ({detail}) - "
            f"{consequence}")
