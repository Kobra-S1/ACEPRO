"""Simulation: dual-ACE2 shared-bus binding integrity across updates.

Investigates the Gwebster report (Spoolman mapping broke after a repo update
on a two-ACE2 setup): the unit<->instance mapping lives in persisted
``ace2_bus_bindings_<group>`` variables whose key encodes the bus-group
composition. These tests characterize what happens when the persisted key no
longer matches the current group (as seen with stale ``_1``/``_1_2`` variants
on real hardware) and whether discovery reply order can influence assignment.
"""

import unittest

from tests.test_manager import TestSharedAce2Transport


class TestDualAce2BindingIntegrity(TestSharedAce2Transport):
    def _run_discovery(self, manager, replies):
        shared_serial_mgr = manager.instances[0].serial_mgr
        responses = iter(replies)

        def send_high_prio_request(request, callback):
            callback(next(responses))

        shared_serial_mgr.send_high_prio_request.side_effect = send_high_prio_request
        manager._initialize_shared_bus_transport(manager.instances[0])
        return shared_serial_mgr

    def test_stale_group_key_bindings_are_orphaned(self):
        """Bindings persisted under an outdated group key are silently ignored.

        A previous install persisted unit (44,55,66) as instance 1 under the
        old single-shared-instance key. After an update the group is {0,1},
        so the code looks only for ace2_bus_bindings_0_1 - the old binding
        no longer participates and units are re-assigned from scratch.
        """
        # Persisted intent: instance 1 = the LOWER-uid unit (11,22,33).
        # UID-order fresh assignment would give instance 1 the HIGHER uid,
        # so honored vs. orphaned bindings produce different outcomes here.
        self.variables["ace2_bus_bindings_1"] = {"1": [11, 22, 33]}

        manager = self._build_manager()
        self._run_discovery(manager, [
            {"result": {"uid1": 44, "uid2": 55, "uid3": 66}},
            {"result": {"uid1": 11, "uid2": 22, "uid3": 33}},
            {"code": 0, "msg": "SUCCESS"},
            {"code": 0, "msg": "SUCCESS"},
        ])

        bus_session = manager.instances[0].bus_session
        instance1_unit = bus_session.get_device_for_instance(1)
        self.assertEqual(
            instance1_unit.identity.uid_tuple,
            (44, 55, 66),
            "expected stale-key bindings to be orphaned (instance 1 "
            "re-assigned by UID order); if this fails, the code honored "
            "the old key after all",
        )

    def test_discovery_reply_order_does_not_affect_assignment(self):
        """Fresh assignment must be UID-ordered, not reply-ordered."""
        manager = self._build_manager()
        self._run_discovery(manager, [
            {"result": {"uid1": 44, "uid2": 55, "uid3": 66}},
            {"result": {"uid1": 11, "uid2": 22, "uid3": 33}},
            {"code": 0, "msg": "SUCCESS"},
            {"code": 0, "msg": "SUCCESS"},
        ])

        bus_session = manager.instances[0].bus_session
        self.assertEqual(
            bus_session.get_device_for_instance(0).identity.uid_tuple,
            (11, 22, 33),
            "instance 0 should get the lowest UID regardless of reply order",
        )


if __name__ == "__main__":
    unittest.main()
