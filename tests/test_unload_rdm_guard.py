"""RDM early-stop must only be used when the RDM actually sees filament.

``smart_unload``'s coordinated-retraction branch picks between two ACE
retract strategies:

* ``rmd_triggered_unload_slot`` - retracts while polling the RDM sensor and
  stops as soon as it reads CLEAR (plus ``rdm_overshoot_length``).
* ``_smart_unload_slot`` - retracts a fixed park-to-toolhead distance.

The first is only meaningful when the RDM currently *has* filament.  If the
RDM already reads clear when the retract starts, its early-stop callback
fires on the very first sample and the retract terminates after nothing but
the overshoot - reporting success while ~800mm of filament is still sitting
in the bowden.

The sibling call site (toolhead clear, RDM triggered) guards on the live RDM
state, and ``_identify_and_unload_by_cycling`` gates its Case 3 the same way.
ARCHITECTURE.md documents only that guarded form.  These tests pin the
coordinated-retraction call site to the same contract.
"""

from unittest.mock import Mock, patch

from ace.manager import AceManager
from ace.config import SENSOR_TOOLHEAD


class TestSmartUnloadRdmEarlyStopGuard:
    def _instance(self):
        inst = Mock()
        inst.instance_num = 0
        inst.inventory = [{"status": "ready"} for _ in range(4)]
        inst.protocol.feed_assist_causes_busy.return_value = True
        inst._feed_assist_index = -1
        inst.rdm_overshoot_length = 50.0
        inst.rmd_triggered_unload_slot.return_value = True
        inst._smart_unload_slot.return_value = True
        return inst

    def _manager(self, instance, rdm_has_filament):
        mgr = object.__new__(AceManager)
        mgr.gcode = Mock()
        mgr.state = Mock()
        mgr.state.get = Mock(
            side_effect=lambda key, default=None: {
                "ace_current_index": 1
            }.get(key, default)
        )
        mgr.state.set = Mock()
        mgr.instances = [instance]
        mgr.toolhead_retraction_length = 40.0
        mgr.toolhead_retraction_speed = 10.0
        mgr.has_rdm_sensor = Mock(return_value=True)
        mgr._get_config_for_tool = Mock(return_value=800.0)
        mgr._extruder_move = Mock()
        mgr._wait_toolhead_move_finished = Mock()
        mgr.is_filament_path_free_instant = Mock(return_value=True)
        mgr._turn_off_heater_if_idle = Mock()

        # Toolhead sensor triggered -> take the coordinated-retraction branch.
        def read(sensor_name):
            if sensor_name == SENSOR_TOOLHEAD:
                return True
            return rdm_has_filament

        mgr.get_instant_switch_state = Mock(side_effect=read)
        mgr.get_switch_state = Mock(side_effect=read)
        return mgr

    def _unload(self, mgr):
        with patch("ace.manager.get_instance_from_tool", return_value=0), \
             patch("ace.manager.get_local_slot", return_value=1):
            return mgr.smart_unload(tool_index=1, prepare_toolhead=False)

    def test_rdm_clear_at_entry_uses_fixed_length_retract(self):
        """RDM already clear: the early-stop monitor would truncate the
        retract to just the overshoot, so the fixed-length retract must be
        used instead."""
        inst = self._instance()
        mgr = self._manager(inst, rdm_has_filament=False)

        assert self._unload(mgr) is True

        inst.rmd_triggered_unload_slot.assert_not_called()
        inst._smart_unload_slot.assert_called_once()

    def test_rdm_triggered_at_entry_uses_rdm_monitored_retract(self):
        """RDM sees filament: early-stop is meaningful and must be used, so
        the retract stops when the path clears instead of blindly pulling the
        full park-to-toolhead distance over the ACE entry sensor."""
        inst = self._instance()
        mgr = self._manager(inst, rdm_has_filament=True)

        assert self._unload(mgr) is True

        inst.rmd_triggered_unload_slot.assert_called_once()
        inst._smart_unload_slot.assert_not_called()
