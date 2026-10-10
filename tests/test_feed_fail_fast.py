"""Tests for fail-fast on firmware-reported slot errors during load feeds.

ACE2 firmware aborts a blocked feed by itself after ~18 s (slot ->
gear_err, status_detail feed_error), while the
driver previously waited blind through a 66 s sensor timeout plus 60 s of
extruder-assist grinding — which can chew the filament and leave broken
fragments in the toolhead (exactly what happened during live testing).
"""
from unittest.mock import Mock

import pytest

from ace.config import INSTANCE_MANAGERS, SENSOR_TOOLHEAD, SENSOR_RDM
from ace.instance import AceInstance


def _bare_instance(slot_status="ready", status_detail=None):
    inst = object.__new__(AceInstance)
    inst.instance_num = 1
    inst.gcode = Mock()
    INSTANCE_MANAGERS[1] = Mock()
    INSTANCE_MANAGERS[1].get_switch_state.return_value = False
    slot = {"index": 0, "status": slot_status}
    if status_detail is not None:
        slot["status_detail"] = status_detail
    inst._info = {"slots": [slot, {"index": 1, "status": "ready"}]}
    inst.dwell = Mock()
    inst._stop_feed = Mock()
    inst._feed = Mock()
    inst._disable_feed_assist = Mock()
    inst._enable_feed_assist = Mock()
    inst.execute_feed_with_retries = Mock()
    inst.wait_ready = Mock()
    inst.timeout_multiplier = 2
    inst.feed_speed = 60.0
    # No grace period in tests - fail on the first poll
    inst.FEED_ERROR_GRACE_S = -1.0
    return inst


class TestGetSlotFeedError:
    def test_ready_slot_returns_none(self):
        inst = _bare_instance(slot_status="ready")
        assert inst._get_slot_feed_error(0) is None

    def test_gear_err_returns_detail(self):
        inst = _bare_instance(slot_status="gear_err", status_detail="feed_error")
        assert inst._get_slot_feed_error(0) == "feed_error"

    def test_gear_err_without_detail_returns_status(self):
        inst = _bare_instance(slot_status="gear_err")
        assert inst._get_slot_feed_error(0) == "gear_err"

    def test_ace1_numeric_gear_err_code_normalized(self):
        # ACE1 reports the slot state machine numerically; code 5 = gear_err
        inst = _bare_instance(slot_status=5)
        assert inst._get_slot_feed_error(0) == "gear_err"

    def test_unknown_slot_returns_none(self):
        inst = _bare_instance(slot_status="gear_err", status_detail="feed_error")
        assert inst._get_slot_feed_error(3) is None

    def test_feeding_slot_returns_none(self):
        inst = _bare_instance(slot_status="feeding")
        assert inst._get_slot_feed_error(0) is None


class TestToolheadFeedFailFast:
    def test_firmware_error_aborts_wait_and_skips_extruder_assist(self):
        inst = _bare_instance(slot_status="gear_err", status_detail="feed_error")
        with pytest.raises(ValueError, match="feed_error"):
            inst._feed_to_toolhead_with_extruder_assist(
                0, feed_length=100.0, feed_speed=50.0,
                extruder_feeding_length=1, extruder_feeding_speed=5,
            )
        inst._stop_feed.assert_called_once_with(0)
        # The 60s extruder-assist grind must never start against a blocked path
        inst._enable_feed_assist.assert_not_called()

    def test_tangled_error_detail_appears_in_message(self):
        inst = _bare_instance(slot_status="gear_err", status_detail="tangled_error")
        with pytest.raises(ValueError, match="tangled_error"):
            inst._feed_to_toolhead_with_extruder_assist(
                0, feed_length=100.0, feed_speed=50.0,
                extruder_feeding_length=1, extruder_feeding_speed=5,
            )

    def test_healthy_slot_does_not_false_trigger(self):
        # Sensor triggers on the 3rd poll; slot stays healthy throughout
        inst = _bare_instance(slot_status="feeding")
        INSTANCE_MANAGERS[1].get_switch_state.side_effect = [
            False, False, True,  # wait loop
            True,                # final sanity check
        ]
        inst._change_feed_speed = Mock(return_value=True)
        inst._extruder_move = Mock()
        inst.extruder_feeding_length = 1
        result = inst._feed_to_toolhead_with_extruder_assist(
            0, feed_length=100.0, feed_speed=50.0,
            extruder_feeding_length=1, extruder_feeding_speed=5,
        )
        inst._enable_feed_assist.assert_called_once_with(0)
        assert result == inst.extruder_feeding_length


class TestVerificationFeedFailFast:
    def test_firmware_error_aborts_wait(self):
        inst = _bare_instance(slot_status="gear_err", status_detail="feed_error")
        with pytest.raises(ValueError, match="feed_error"):
            inst._feed_filament_to_verification_sensor(
                0, SENSOR_RDM, feed_length=150.0
            )
        inst._stop_feed.assert_called_once_with(0)

    def test_firmware_error_aborts_incremental_feeding(self):
        # Main wait exits via timeout (healthy), error appears before the
        # incremental loop pushes more filament
        inst = _bare_instance(slot_status="gear_err", status_detail="stuck_error")
        inst.timeout_multiplier = 0  # immediate timeout of the main wait
        inst.FEED_ERROR_GRACE_S = 10**9  # keep main-wait check out of the way
        inst.total_max_feeding_length = 500
        inst.incremental_feeding_length = 50
        inst.incremental_feeding_speed = 30
        with pytest.raises(ValueError, match="stuck_error"):
            inst._feed_filament_to_verification_sensor(
                0, SENSOR_TOOLHEAD, feed_length=150.0
            )
        # No incremental feed was pushed against the blocked path
        inst._feed.assert_called_once()  # only the initial feed
