"""
Tests for the endless_spool_next_tool field of AceManager.get_status()
(the ace_state printer object that Moonraker clients and panels read).

Kept apart from test_manager.py, which is far past the test file size cap.
"""

import unittest
from unittest.mock import patch

from tests import test_manager

from ace.manager import AceManager


class TestEndlessSpoolNextTool(unittest.TestCase):
    """get_status publishes the predicted swap target for the loaded tool."""

    def setUp(self):
        # Reuse the manager fixture of test_manager.TestGetStatus; reached
        # through the module so pytest does not collect that class twice.
        self.fixture = test_manager.TestGetStatus()
        self.fixture.setUp()
        self.variables = self.fixture.variables
        self.variables["ace_current_index"] = 1
        self.variables["ace_endless_spool_enabled"] = True

    def _manager(self):
        patches = [patch("ace.manager.AceInstance"),
                   patch("ace.manager.EndlessSpool")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        manager = AceManager(self.fixture.mock_config)
        manager.endless_spool.predict_next_tool.return_value = 6
        return manager

    def test_reports_the_predicted_tool_for_the_loaded_tool(self):
        manager = self._manager()

        status = manager.get_status()

        self.assertEqual(status["endless_spool_next_tool"], 6)
        manager.endless_spool.predict_next_tool.assert_called_once_with(1)

    def test_minus_one_while_endless_spool_is_disabled(self):
        self.variables["ace_endless_spool_enabled"] = False
        manager = self._manager()

        self.assertEqual(manager.get_status()["endless_spool_next_tool"], -1)

    def test_minus_one_during_a_toolchange(self):
        """Slots read 'searching'/'empty' mid-swap; no flickering guess."""
        manager = self._manager()
        manager.toolchange_in_progress = True

        self.assertEqual(manager.get_status()["endless_spool_next_tool"], -1)

    def test_minus_one_in_the_fallback_status(self):
        manager = self._manager()
        manager.endless_spool.predict_next_tool.side_effect = RuntimeError("x")

        status = manager.get_status()

        self.assertEqual(status["endless_spool_next_tool"], -1)
        self.assertEqual(status["current_index"], -1)  # proves the fallback ran


if __name__ == "__main__":
    unittest.main()
