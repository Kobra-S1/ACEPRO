"""
Tests for instant (raw channel) sensor detection.

Option 3: ``are_both_channels_open`` on FilamentTracker, exposed via
``FilamentTrackerAdapter.is_instantly_clear()`` and
``AceManager.get_instant_switch_state()``.

These tests verify:
- FilamentTrackerAdapter delegates correctly to the tracker property.
- Graceful fallback when the underlying sensor has no instant query.
- get_instant_switch_state uses instant path when available.
- is_filament_path_free_instant uses instant reads for both sensors.
"""
import pytest
from unittest.mock import Mock, MagicMock, PropertyMock
from ace.manager import FilamentTrackerAdapter
from ace.config import SENSOR_TOOLHEAD, SENSOR_RDM


# ── FilamentTrackerAdapter.is_instantly_clear() ──────────────────────────

class TestFilamentTrackerAdapterInstant:
    """Tests for is_instantly_clear() on the adapter shim."""

    def _make_adapter(self, both_open=None):
        """Build a FilamentTrackerAdapter with a mocked tracker.

        Args:
            both_open: Value for are_both_channels_open property.
                       None → attribute not present (simulate old tracker).
        """
        tracker = Mock()
        tracker.runout_helper = Mock()
        tracker.runout_helper.filament_present = True
        tracker.runout_helper.sensor_enabled = True
        if both_open is not None:
            type(tracker).are_both_channels_open = PropertyMock(
                return_value=both_open)
        else:
            # Simulate a tracker that does NOT have the property
            if hasattr(tracker, 'are_both_channels_open'):
                delattr(tracker, 'are_both_channels_open')
        return FilamentTrackerAdapter(tracker), tracker

    def test_returns_true_when_both_open(self):
        """Tracker says both channels open → is_instantly_clear() True."""
        adapter, _ = self._make_adapter(both_open=True)
        assert adapter.is_instantly_clear() is True

    def test_returns_false_when_not_both_open(self):
        """At least one channel closed → is_instantly_clear() False."""
        adapter, _ = self._make_adapter(both_open=False)
        assert adapter.is_instantly_clear() is False

    def test_returns_none_when_no_property(self):
        """Tracker without are_both_channels_open → returns None (fallback)."""
        tracker = Mock(spec=[])  # empty spec → no attributes
        tracker.runout_helper = Mock()
        tracker.runout_helper.filament_present = True
        tracker.runout_helper.sensor_enabled = True
        adapter = FilamentTrackerAdapter(tracker)
        assert adapter.is_instantly_clear() is None

    def test_filament_present_still_works(self):
        """Adapter still exposes filament_present from RunoutHelper."""
        adapter, tracker = self._make_adapter(both_open=True)
        tracker.runout_helper.filament_present = False
        assert adapter.filament_present is False


# ── Manager: get_instant_switch_state ────────────────────────────────────

class TestGetInstantSwitchState:
    """Tests for AceManager.get_instant_switch_state()."""

    def _make_manager(self, sensors=None):
        """Build a minimal mock manager with sensors dict and methods.

        We don't instantiate AceManager (too many deps); instead we
        build a lightweight object with the relevant methods patched in.
        """
        from ace.manager import AceManager

        manager = Mock(spec=AceManager)
        manager.sensors = sensors or {}
        # Bind the real methods
        manager.get_instant_switch_state = (
            AceManager.get_instant_switch_state.__get__(manager))
        manager.get_switch_state = (
            AceManager.get_switch_state.__get__(manager))
        manager.has_rdm_sensor = (
            AceManager.has_rdm_sensor.__get__(manager))
        manager.is_filament_path_free_instant = (
            AceManager.is_filament_path_free_instant.__get__(manager))
        # No test override by default
        if hasattr(manager, '_sensor_override'):
            del manager._sensor_override
        return manager

    def _make_tracker_sensor(self, instantly_clear=None, filament_present=True):
        """Build a mock sensor that behaves like FilamentTrackerAdapter."""
        sensor = Mock()
        sensor.filament_present = filament_present
        if instantly_clear is not None:
            sensor.is_instantly_clear = Mock(return_value=instantly_clear)
        else:
            # No is_instantly_clear method → fallback to filament_present
            del sensor.is_instantly_clear
        return sensor

    def _make_switch_sensor(self, filament_present=True):
        """Build a mock sensor without is_instantly_clear (plain switch)."""
        sensor = Mock(spec=['filament_present'])
        sensor.filament_present = filament_present
        return sensor

    # --- Core behavior ---

    def test_uses_instant_when_available_clear(self):
        """Sensor with is_instantly_clear=True → returns False (absent)."""
        sensor = self._make_tracker_sensor(
            instantly_clear=True, filament_present=True)
        manager = self._make_manager({SENSOR_TOOLHEAD: sensor})
        # Instant says clear → filament absent → False
        assert manager.get_instant_switch_state(SENSOR_TOOLHEAD) is False

    def test_uses_instant_when_available_not_clear(self):
        """Sensor with is_instantly_clear=False → returns True (present)."""
        sensor = self._make_tracker_sensor(
            instantly_clear=False, filament_present=True)
        manager = self._make_manager({SENSOR_TOOLHEAD: sensor})
        assert manager.get_instant_switch_state(SENSOR_TOOLHEAD) is True

    def test_falls_back_to_filament_present(self):
        """Plain switch sensor (no is_instantly_clear) → uses filament_present."""
        sensor = self._make_switch_sensor(filament_present=False)
        manager = self._make_manager({SENSOR_TOOLHEAD: sensor})
        assert manager.get_instant_switch_state(SENSOR_TOOLHEAD) is False

    def test_falls_back_when_instant_returns_none(self):
        """Sensor where is_instantly_clear() returns None → falls back."""
        sensor = self._make_tracker_sensor(
            instantly_clear=None, filament_present=True)
        # is_instantly_clear exists but returns None
        sensor.is_instantly_clear = Mock(return_value=None)
        manager = self._make_manager({SENSOR_TOOLHEAD: sensor})
        assert manager.get_instant_switch_state(SENSOR_TOOLHEAD) is True

    def test_missing_sensor_returns_false(self):
        """Unknown sensor name → returns False."""
        manager = self._make_manager({})
        assert manager.get_instant_switch_state(SENSOR_TOOLHEAD) is False

    def test_sensor_override_takes_priority(self):
        """Test override still works for instant path."""
        sensor = self._make_tracker_sensor(
            instantly_clear=True, filament_present=True)
        manager = self._make_manager({SENSOR_TOOLHEAD: sensor})
        manager._sensor_override = {SENSOR_TOOLHEAD: True}
        # Override wins over instant detection
        assert manager.get_instant_switch_state(SENSOR_TOOLHEAD) is True


# ── Manager: is_filament_path_free_instant ───────────────────────────────

class TestIsFilamentPathFreeInstant:
    """Tests for is_filament_path_free_instant()."""

    def _make_manager(self, toolhead_clear, rdm_clear=None, has_rdm=True):
        """Build a manager mock with instant sensors.

        Args:
            toolhead_clear: is_instantly_clear value for toolhead sensor.
            rdm_clear: is_instantly_clear value for RDM sensor (None = no RDM).
            has_rdm: Whether RDM sensor is present.
        """
        from ace.manager import AceManager

        sensors = {}
        toolhead_sensor = Mock()
        toolhead_sensor.filament_present = not toolhead_clear
        toolhead_sensor.is_instantly_clear = Mock(return_value=toolhead_clear)
        sensors[SENSOR_TOOLHEAD] = toolhead_sensor

        if has_rdm and rdm_clear is not None:
            rdm_sensor = Mock()
            rdm_sensor.filament_present = not rdm_clear
            rdm_sensor.is_instantly_clear = Mock(return_value=rdm_clear)
            sensors[SENSOR_RDM] = rdm_sensor

        manager = Mock(spec=AceManager)
        manager.sensors = sensors
        manager.get_instant_switch_state = (
            AceManager.get_instant_switch_state.__get__(manager))
        manager.has_rdm_sensor = (
            AceManager.has_rdm_sensor.__get__(manager))
        manager.is_filament_path_free_instant = (
            AceManager.is_filament_path_free_instant.__get__(manager))
        if hasattr(manager, '_sensor_override'):
            del manager._sensor_override
        return manager

    def test_both_clear_returns_true(self):
        """Both sensors clear → path free."""
        manager = self._make_manager(toolhead_clear=True, rdm_clear=True)
        assert manager.is_filament_path_free_instant() is True

    def test_toolhead_blocked_returns_false(self):
        """Toolhead blocked → path not free."""
        manager = self._make_manager(toolhead_clear=False, rdm_clear=True)
        assert manager.is_filament_path_free_instant() is False

    def test_rdm_blocked_returns_false(self):
        """RDM blocked → path not free."""
        manager = self._make_manager(toolhead_clear=True, rdm_clear=False)
        assert manager.is_filament_path_free_instant() is False

    def test_no_rdm_only_toolhead(self):
        """No RDM sensor → only toolhead checked."""
        manager = self._make_manager(
            toolhead_clear=True, rdm_clear=None, has_rdm=False)
        assert manager.is_filament_path_free_instant() is True

    def test_no_rdm_toolhead_blocked(self):
        """No RDM, toolhead blocked → not free."""
        manager = self._make_manager(
            toolhead_clear=False, rdm_clear=None, has_rdm=False)
        assert manager.is_filament_path_free_instant() is False
