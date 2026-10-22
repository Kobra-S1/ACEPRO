"""Tests for Moonraker ACE status component instance routing behavior."""

import asyncio
from unittest.mock import Mock

from ace_status_integration.moonraker.ace_status import AceStatus


class _DummyWebRequest:
    def __init__(self, instance=None):
        self._instance = instance

    def get_str(self, key, default=None):
        if key == "instance" and self._instance is not None:
            return str(self._instance)
        return default


def _build_component():
    server = Mock()
    klippy_apis = Mock()
    server.lookup_component.return_value = klippy_apis

    config = Mock()
    config.get_server.return_value = server

    comp = AceStatus(config)
    return comp


def test_handle_status_request_returns_requested_instance_when_available():
    comp = _build_component()

    async def _query():
        return {
            "manager": {"current_index": 1},
            "instances": {
                0: {"temp": 25, "status": "ready"},
                1: {"temp": 45, "status": "busy"},
            },
            "count": 2,
        }

    comp._query_ace_instances = _query

    result = asyncio.run(comp.handle_status_request(_DummyWebRequest(instance=0)))

    assert result["instance_index"] == 0
    assert result["temp"] == 25


def test_handle_status_request_does_not_fallback_for_unavailable_requested_instance():
    comp = _build_component()

    async def _query():
        return {
            "manager": {"current_index": 1},
            "instances": {
                1: {"temp": 45, "status": "busy"},
            },
            "count": 2,
        }

    comp._query_ace_instances = _query

    result = asyncio.run(comp.handle_status_request(_DummyWebRequest(instance=0)))

    assert "error" in result
    assert result["instance_index"] == 0
    assert result["available_instances"] == [1]


def _unit(first_tool, temp):
    """An ace_instance_N status: its slots carry their tool numbers."""
    return {"temp": temp, "status": "ready",
            "slots": [{"index": i, "tool": first_tool + i} for i in range(4)]}


def _default_unit(current_index, instances):
    comp = _build_component()

    async def _query():
        return {"manager": {"current_index": current_index},
                "instances": instances, "count": len(instances)}

    comp._query_ace_instances = _query
    return asyncio.run(comp.handle_status_request(_DummyWebRequest()))


def test_without_instance_param_the_unit_holding_the_loaded_tool_is_shown():
    # T5 is the second unit's slot 1; T1 is the first unit's.
    two_units = {0: _unit(0, 25), 1: _unit(4, 45)}
    assert _default_unit(5, two_units)["instance_index"] == 1
    assert _default_unit(5, two_units)["temp"] == 45
    assert _default_unit(1, two_units)["instance_index"] == 0


def test_the_loaded_tool_is_found_behind_a_tool_base():
    # Kobra X, three direct inlets first: the units hold T3-T6 and T7-T10.
    two_units = {0: _unit(3, 25), 1: _unit(7, 45)}
    assert _default_unit(8, two_units)["instance_index"] == 1
    assert _default_unit(1, two_units)["instance_index"] == 0   # a direct tool


def test_without_a_loaded_tool_the_first_unit_is_shown():
    two_units = {0: _unit(0, 25), 1: _unit(4, 45)}
    assert _default_unit(-1, two_units)["instance_index"] == 0
