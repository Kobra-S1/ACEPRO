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


def test_handle_status_request_keeps_default_current_index_fallback_without_instance_param():
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

    result = asyncio.run(comp.handle_status_request(_DummyWebRequest(instance=None)))

    assert result["instance_index"] == 1
    assert result["temp"] == 45
