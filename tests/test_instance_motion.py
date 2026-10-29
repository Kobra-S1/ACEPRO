"""Tests for AceInstance motion primitives (_retract, _feed) against a fake ACE.

FakeAce stands in for the two boundaries the primitives talk to: the serial
link (answers every request with code 0) and the reactor clock (pause()
advances simulated time).  The device reports 'busy' while a retract runs,
so _retract's dwell and its wait-for-ready phase both execute for real.
"""
import unittest
from unittest.mock import Mock, patch

from ace.config import FeedAbortedByFirmware, FILAMENT_STATE_TOOLHEAD
from ace.instance import AceInstance, INSTANCE_MANAGERS
from tests.test_feed_assist_recovery import ACE_CONFIG

STOP_SETTLE_S = 3.0  # time the fake ACE keeps reporting busy after a stop


class FakeAce:
    def __init__(self, retract_busy_extra_s=0.0):
        self.now = 0.0
        self.sent = []
        self.instance = None
        self.slot_status_schedule = []  # (time, slot, status), applied in order
        self._retract_busy_extra_s = retract_busy_extra_s
        self._busy_until = None

    # reactor
    def monotonic(self):
        return self.now

    def pause(self, until):
        self.now = max(self.now, until)
        self._advance()

    # serial
    def send_request(self, request, callback):
        self.sent.append(request)
        method = request["method"]
        params = request.get("params", {})
        if method == "unwind_filament":
            self._set_busy(params["length"] / params["speed"]
                           + self._retract_busy_extra_s)
        elif method == "stop_unwind_filament" and self._busy_until is not None:
            self._busy_until = min(self._busy_until, self.now + STOP_SETTLE_S)
        callback({"code": 0, "msg": "success"})

    def methods(self):
        return [request["method"] for request in self.sent]

    def _set_busy(self, duration_s):
        self.instance._info["status"] = "busy"
        self._busy_until = self.now + duration_s

    def _advance(self):
        while self.slot_status_schedule and self.slot_status_schedule[0][0] <= self.now:
            _, slot, status = self.slot_status_schedule.pop(0)
            self.instance._info["slots"][slot]["status"] = status
        if self._busy_until is not None and self.now >= self._busy_until:
            self.instance._info["status"] = "ready"
            self._busy_until = None


class MotionTestBase(unittest.TestCase):
    retract_busy_extra_s = 0.0

    def setUp(self):
        INSTANCE_MANAGERS.clear()
        self.ace = FakeAce(self.retract_busy_extra_s)
        printer = Mock()
        printer.get_reactor.return_value = self.ace
        printer.lookup_object = Mock(return_value=Mock())
        with patch('ace.instance.AceSerialManager'):
            self.instance = AceInstance(0, ACE_CONFIG, printer)
        INSTANCE_MANAGERS[0] = Mock()
        self.ace.instance = self.instance
        self.instance.serial_mgr.send_request = self.ace.send_request
        self.instance.serial_mgr.send_high_prio_request = self.ace.send_request
        self.instance._info["status"] = "ready"
        self.instance._info["slots"] = [
            {"index": i, "status": "ready"} for i in range(4)
        ]
        clock = patch('ace.instance.time.time', side_effect=lambda: self.ace.now)
        clock.start()
        self.addCleanup(clock.stop)

    def stop_count(self):
        return self.ace.methods().count("stop_unwind_filament")


class TestRetractEarlyStop(MotionTestBase):
    # The ACE stays busy long after the dwell (length / speed), so the
    # post-dwell wait-for-ready phase runs.
    retract_busy_extra_s = 20.0

    def reason_from(self, start_s):
        return lambda: "sensor clear" if self.ace.now >= start_s else None

    def test_reason_during_dwell_stops_once_and_reports_it(self):
        result = self.instance._retract(
            0, 10, 1, early_stop_callback=self.reason_from(3.0))

        self.assertEqual(self.stop_count(), 1)
        self.assertEqual(result["msg"], "Retract stopped early: sensor clear")

    def test_reason_after_dwell_stops_once_and_reports_it(self):
        result = self.instance._retract(
            0, 2, 1, early_stop_callback=self.reason_from(5.0))

        self.assertEqual(self.stop_count(), 1)
        self.assertEqual(result["msg"], "Retract stopped early: sensor clear")

    def test_slot_empty_twice_after_dwell_stops_once(self):
        self.ace.slot_status_schedule = [
            (5.0, 0, "empty"), (6.0, 0, "ready"), (7.0, 0, "empty"),
        ]

        result = self.instance._retract(0, 2, 1)

        self.assertEqual(self.stop_count(), 1)
        self.assertEqual(result["msg"], "Retract stopped early: slot empty")
        self.assertTrue(self.instance._last_retract_early_stopped)

    def test_slot_empty_after_reason_sends_no_second_stop(self):
        self.ace.slot_status_schedule = [(6.0, 0, "empty")]

        self.instance._retract(0, 2, 1, early_stop_callback=self.reason_from(5.0))

        self.assertEqual(self.stop_count(), 1)

    def test_no_reason_returns_the_accept_response(self):
        result = self.instance._retract(
            0, 2, 1, early_stop_callback=lambda: None)

        self.assertEqual(self.stop_count(), 0)
        self.assertEqual(result, {"code": 0, "msg": "success"})


class TestFeedAssistOffBeforeMotion(MotionTestBase):

    def assert_assist_stopped_before(self, motion_method, assist_slot):
        methods = self.ace.methods()
        self.assertIn("stop_feed_assist", methods)
        self.assertIn(motion_method, methods)
        self.assertLess(methods.index("stop_feed_assist"), methods.index(motion_method))
        stop = self.ace.sent[methods.index("stop_feed_assist")]
        self.assertEqual(stop["params"]["index"], assist_slot)
        self.assertEqual(self.instance._feed_assist_index, -1)

    def test_retract_disables_assist_on_another_slot_first(self):
        self.instance._feed_assist_index = 2

        self.instance._retract(0, 2, 1)

        self.assert_assist_stopped_before("unwind_filament", 2)

    def test_retract_disables_assist_on_its_own_slot_first(self):
        self.instance._feed_assist_index = 0

        self.instance._retract(0, 2, 1)

        self.assert_assist_stopped_before("unwind_filament", 0)

    def test_feed_disables_assist_on_another_slot_first(self):
        self.instance._feed_assist_index = 2

        self.instance._feed(0, 50, 10)

        self.assert_assist_stopped_before("feed_filament", 2)

    def test_feed_disables_assist_on_its_own_slot_first(self):
        self.instance._feed_assist_index = 0

        self.instance._feed(0, 50, 10)

        self.assert_assist_stopped_before("feed_filament", 0)

    def test_no_assist_sends_no_stop(self):
        self.instance._feed(0, 50, 10)

        self.assertNotIn("stop_feed_assist", self.ace.methods())


class TestToolheadLoadRetryAfterFirmwareAbort(MotionTestBase):

    def test_firmware_abort_is_retried_without_pull_back(self):
        self.instance.toolhead_load_retries = 1
        steps = []
        manager = INSTANCE_MANAGERS[0]
        manager.route_to_tool = Mock(
            side_effect=lambda tool, feeder: steps.append(f"route {tool} {feeder}"))
        outcomes = [FeedAbortedByFirmware("feed_error"), None]

        def feed_to_sensor(*args):
            steps.append("feed")
            outcome = outcomes.pop(0)
            if outcome is not None:
                raise outcome

        self.instance._feed_to_toolhead_with_extruder_assist = Mock(
            side_effect=feed_to_sensor)
        toolhead = Mock(get_position=Mock(return_value=[0., 0., 0., 0.]))
        self.instance.printer.lookup_object = Mock(return_value=toolhead)

        self.instance._feed_filament_into_toolhead(2, check_pre_condition=False)

        self.assertEqual(
            steps, ["feed", f"route 2 {self.instance.load_feeder()}", "feed"])
        self.assertNotIn("unwind_filament", self.ace.methods())
        manager.state.set.assert_any_call("ace_filament_pos", FILAMENT_STATE_TOOLHEAD)


if __name__ == '__main__':
    unittest.main()
