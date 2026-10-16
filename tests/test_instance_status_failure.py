"""Tests for AceInstance's heartbeat failure streak and the link recovery it
triggers once status_failure_threshold failed polls follow each other."""
import unittest
from unittest.mock import Mock, patch

from ace.instance import AceInstance, INSTANCE_MANAGERS
from tests.test_feed_assist_recovery import ACE_CONFIG

THRESHOLD = 2


class StatusFailureTestBase(unittest.TestCase):

    def setUp(self):
        INSTANCE_MANAGERS.clear()
        printer = Mock()
        printer.lookup_object = Mock(return_value=Mock())
        config = dict(ACE_CONFIG, status_failure_threshold=THRESHOLD)
        with patch('ace.instance.AceSerialManager'):
            self.instance = AceInstance(0, config, printer)
        self.link = self.instance.serial_mgr

    def fail_polls(self, count):
        for _ in range(count):
            self.instance._on_heartbeat_response({"code": 1, "msg": "busy"})


class TestHeartbeatErrorReplies(StatusFailureTestBase):

    def test_error_replies_below_threshold_do_not_reconnect(self):
        self.fail_polls(THRESHOLD - 1)

        self.assertEqual(self.instance._status_failure_streak, THRESHOLD - 1)
        self.link.reconnect.assert_not_called()

    def test_error_replies_at_threshold_reconnect(self):
        self.fail_polls(THRESHOLD)

        self.link.reconnect.assert_called_once_with()


class TestRecoveryFallbacks(StatusFailureTestBase):

    def test_failed_reconnect_falls_back_to_disconnect(self):
        self.link.reconnect.side_effect = OSError("port gone")

        self.fail_polls(THRESHOLD)

        self.link.disconnect.assert_called_once_with()

    def test_failed_reconnect_and_disconnect_raise_nothing(self):
        self.link.reconnect.side_effect = OSError("port gone")
        self.link.disconnect.side_effect = OSError("close failed")

        self.fail_polls(THRESHOLD)

        self.assertFalse(self.instance._status_recovery_in_progress)

    def test_next_failure_retries_when_no_recovery_path_ran(self):
        self.link.reconnect.side_effect = [OSError("port gone"), None]
        self.link.disconnect.side_effect = OSError("close failed")

        self.fail_polls(THRESHOLD + 1)

        self.assertEqual(self.link.reconnect.call_count, 2)


if __name__ == '__main__':
    unittest.main()
