"""Tests for the device's reply to a pending feed assist restore.

A refused (NAK) or unanswered restore request must stay queued for the next
heartbeat, the same way a busy device (wait_ready timeout) re-queues it,
until FEED_ASSIST_RESTORE_MAX_ATTEMPTS is used up.
"""
import unittest
from unittest.mock import Mock, patch

from ace.instance import AceInstance, INSTANCE_MANAGERS
from tests.test_feed_assist_recovery import ACE_CONFIG


class TestFeedAssistRestoreResponse(unittest.TestCase):

    def setUp(self):
        INSTANCE_MANAGERS.clear()
        printer = Mock()
        printer.lookup_object = Mock(return_value=Mock())
        with patch('ace.instance.AceSerialManager'):
            self.instance = AceInstance(0, ACE_CONFIG, printer)
        self.instance._feed_assist_index = -1
        self.instance._pending_feed_assist_restore = -1
        self.instance._feed_assist_restore_attempts = 1

    def test_nak_requeues_restore_and_leaves_assist_index(self):
        self.instance._on_feed_assist_restore_response(
            {'code': 1, 'msg': 'busy'}, 2)

        self.assertEqual(self.instance._pending_feed_assist_restore, 2)
        self.assertEqual(self.instance._feed_assist_index, -1)

    def test_missing_response_requeues_restore(self):
        self.instance._on_feed_assist_restore_response(None, 2)

        self.assertEqual(self.instance._pending_feed_assist_restore, 2)
        self.assertEqual(self.instance._feed_assist_index, -1)

    def test_nak_at_max_attempts_is_not_requeued(self):
        self.instance._feed_assist_restore_attempts = (
            AceInstance.FEED_ASSIST_RESTORE_MAX_ATTEMPTS)

        self.instance._on_feed_assist_restore_response(
            {'code': 1, 'msg': 'busy'}, 2)

        self.assertEqual(self.instance._pending_feed_assist_restore, -1)

    def test_accepted_restore_sets_index_and_clears_attempts(self):
        self.instance._on_feed_assist_restore_response({'code': 0}, 2)

        self.assertEqual(self.instance._feed_assist_index, 2)
        self.assertEqual(self.instance._pending_feed_assist_restore, -1)
        self.assertEqual(self.instance._feed_assist_restore_attempts, 0)


if __name__ == '__main__':
    unittest.main()
