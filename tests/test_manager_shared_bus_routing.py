"""AceManager shared ACE2 bus: reply routing, connect success, ACE1 demotion."""

from unittest.mock import Mock

import pytest

from tests import test_manager

UID_A = (11, 22, 33)
UID_B = (44, 55, 66)


@pytest.fixture
def harness():
    fixture = test_manager.TestSharedAce2Transport()
    fixture.setUp()
    return fixture


@pytest.fixture
def manager(harness):
    return harness._build_manager(ace_count=2)


def bind_both_units(bus_session):
    bus_session.bind_logical_instance(0, *UID_A)
    bus_session.assign_device_id(*UID_A, 1)
    bus_session.bind_logical_instance(1, *UID_B)
    bus_session.assign_device_id(*UID_B, 2)


def give_each_instance_its_own_protocol(manager):
    for instance in manager.instances:
        instance.protocol = Mock()
        instance.protocol.handle_bound_shared_bus_unsolicited.return_value = True


def test_unsolicited_reply_reaches_only_the_instance_bound_to_its_device_id(manager):
    bus_session = manager.instances[0].bus_session
    bind_both_units(bus_session)
    give_each_instance_its_own_protocol(manager)
    response = {"device_id": 2, "command": "GET_STATUS", "result": {}}

    handled = manager._handle_shared_bus_unsolicited(bus_session, response)

    assert handled is True
    manager.instances[1].protocol.handle_bound_shared_bus_unsolicited.assert_called_once_with(
        manager.instances[1], response)
    manager.instances[0].protocol.handle_bound_shared_bus_unsolicited.assert_not_called()


def test_unsolicited_reply_with_unknown_device_id_is_not_handled(manager):
    bus_session = manager.instances[0].bus_session
    bind_both_units(bus_session)
    give_each_instance_its_own_protocol(manager)

    handled = manager._handle_shared_bus_unsolicited(
        bus_session, {"device_id": 7, "command": "GET_STATUS"})

    assert handled is False
    for instance in manager.instances:
        instance.protocol.handle_bound_shared_bus_unsolicited.assert_not_called()


def test_connect_with_all_declared_units_found_queues_setup_and_starts_polling(manager):
    bus_session = manager.instances[0].bus_session
    shared_serial_mgr = manager.instances[0].serial_mgr
    replies = iter([
        {"result": dict(zip(("uid1", "uid2", "uid3"), UID_A))},
        {"result": dict(zip(("uid1", "uid2", "uid3"), UID_B))},
        {"code": 0, "msg": "SUCCESS"},
        {"code": 0, "msg": "SUCCESS"},
    ])
    shared_serial_mgr.send_high_prio_request.side_effect = (
        lambda request, callback: callback(next(replies)))

    manager._on_shared_bus_connected(bus_session)

    for instance in manager.instances:
        commands = [c.args[0]["command"]
                    for c in instance.send_high_prio_request.call_args_list]
        assert commands == ["SET_RFID_ENABLE", "SET_RFID_ENABLE", "SET_FEED_CHECK"]
        instance.request_shared_bus_info_refresh.assert_called_once_with()
        instance.start_shared_bus_heartbeat.assert_called_once_with()
    assert id(bus_session) not in manager._shared_bus_retry_timers


def test_demoting_a_shared_bus_instance_drops_its_ace2_bus_location(manager):
    instance = manager.instances[0]
    instance.rebind_transport = Mock()
    manager._topology_resolution = {
        0: {"target_location": "1-1.3", "shared_bus": True}}

    manager._retype_instance_to_ace1(instance)

    kwargs = instance.rebind_transport.call_args.kwargs
    assert kwargs["protocol_name"] == "ace1_json"
    assert kwargs["target_usb_location"] is None


def test_demoting_a_dedicated_instance_keeps_its_usb_location(manager):
    instance = manager.instances[0]
    instance.rebind_transport = Mock()
    manager._topology_resolution = {
        0: {"target_location": "1-1.2", "shared_bus": False}}

    manager._retype_instance_to_ace1(instance)

    kwargs = instance.rebind_transport.call_args.kwargs
    assert kwargs["target_usb_location"] == "1-1.2"
