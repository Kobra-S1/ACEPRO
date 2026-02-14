import io

import pytest
from urllib import error

from ace.moonraker_lane_sync import MoonrakerLaneSyncAdapter


class DummyGCode:
    def __init__(self):
        self.messages = []

    def respond_info(self, msg):
        self.messages.append(msg)


class DummyInstance:
    def __init__(self, tool_offset, inventory):
        self.tool_offset = tool_offset
        self.inventory = inventory


class DummyManager:
    def __init__(self, instances):
        self.instances = instances


def make_adapter(instances, enabled=True, **overrides):
    gcode = DummyGCode()
    manager = DummyManager(instances)
    config = {
        "moonraker_lane_sync_enabled": enabled,
        "moonraker_lane_sync_url": "http://127.0.0.1:7125",
        "moonraker_lane_sync_namespace": "lane_data",
        "moonraker_lane_sync_api_key": None,
        "moonraker_lane_sync_timeout": 0.1,
        "moonraker_lane_sync_unknown_material_mode": "passthrough",
        "moonraker_lane_sync_unknown_material_markers": "???,unknown,n/a,none",
        "moonraker_lane_sync_unknown_material_map_to": "",
    }
    config.update(overrides)
    return MoonrakerLaneSyncAdapter(gcode, manager, config), gcode


def test_build_lane_payload_maps_slots_to_global_lanes():
    instances = [
        DummyInstance(
            0,
            [
                {"status": "ready", "material": "PLA", "color": [255, 16, 0], "temp": 210, "hotbed_temp": {"max": 60}},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
                {"status": "ready", "material": "PETG", "color": [0, 127, 255], "temp": 240},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
            ],
        ),
    ]
    adapter, _ = make_adapter(instances, enabled=True)

    lanes = adapter._build_lane_payload()
    assert lanes["lane1"]["lane"] == "0"
    assert lanes["lane1"]["material"] == "PLA"
    assert lanes["lane1"]["color"] == "#FF1000"
    assert lanes["lane1"]["nozzle_temp"] == 210
    assert lanes["lane1"]["bed_temp"] == 60

    assert lanes["lane2"]["lane"] == "1"
    assert lanes["lane2"]["material"] == ""
    assert lanes["lane2"]["color"] == ""

    assert lanes["lane3"]["lane"] == "2"
    assert lanes["lane3"]["material"] == "PETG"
    assert lanes["lane3"]["color"] == "#007FFF"


def test_sync_now_deletes_stale_lane_keys_and_debounces_identical_payload(monkeypatch):
    instances = [
        DummyInstance(
            0,
            [
                {"status": "ready", "material": "PLA", "color": [255, 0, 0], "temp": 210},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
            ],
        ),
    ]
    adapter, _ = make_adapter(instances, enabled=True)

    calls = {"set": [], "delete": []}

    existing = {"lane1": adapter._build_lane_payload()["lane1"], "lane99": {"lane": "98"}}
    monkeypatch.setattr(adapter, "_get_namespace_items", lambda: existing)
    monkeypatch.setattr(adapter, "_set_item", lambda k, v: calls["set"].append((k, v)))
    monkeypatch.setattr(adapter, "_delete_item", lambda k: calls["delete"].append(k))

    assert adapter.sync_now(force=False, reason="test_first") is True
    assert "lane99" in calls["delete"]
    # lane1 was unchanged, but lane2-lane4 should be posted
    posted_keys = {k for k, _ in calls["set"]}
    assert {"lane2", "lane3", "lane4"}.issubset(posted_keys)

    calls["set"].clear()
    calls["delete"].clear()
    assert adapter.sync_now(force=False, reason="test_second") is False
    assert calls["set"] == []
    assert calls["delete"] == []


def test_sync_now_warns_once_when_unavailable(monkeypatch):
    instances = [DummyInstance(0, [{"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0}] * 4)]
    adapter, gcode = make_adapter(instances, enabled=True)

    monkeypatch.setattr(adapter, "_get_namespace_items", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    assert adapter.sync_now(force=True, reason="first") is False
    assert adapter.sync_now(force=True, reason="second") is False

    warnings = [m for m in gcode.messages if "Moonraker lane sync unavailable" in m]
    assert len(warnings) == 1


def test_unknown_material_passthrough_default():
    instances = [
        DummyInstance(
            0,
            [
                {"status": "ready", "material": "???", "color": [12, 34, 56], "temp": 205},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
            ],
        ),
    ]
    adapter, _ = make_adapter(instances, enabled=True)
    lane1 = adapter._build_lane_payload()["lane1"]
    assert lane1["material"] == "???"
    assert lane1["color"] == "#0C2238"


def test_unknown_material_mode_empty_clears_material_and_color():
    instances = [
        DummyInstance(
            0,
            [
                {"status": "ready", "material": "???", "color": [12, 34, 56], "temp": 205},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
            ],
        ),
    ]
    adapter, _ = make_adapter(
        instances,
        enabled=True,
        moonraker_lane_sync_unknown_material_mode="empty",
    )
    lane1 = adapter._build_lane_payload()["lane1"]
    assert lane1["material"] == ""
    assert lane1["color"] == ""


def test_unknown_material_mode_map_replaces_marker():
    instances = [
        DummyInstance(
            0,
            [
                {"status": "ready", "material": "???", "color": [12, 34, 56], "temp": 205},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
            ],
        ),
    ]
    adapter, _ = make_adapter(
        instances,
        enabled=True,
        moonraker_lane_sync_unknown_material_mode="map",
        moonraker_lane_sync_unknown_material_map_to="PLA",
    )
    lane1 = adapter._build_lane_payload()["lane1"]
    assert lane1["material"] == "PLA"
    assert lane1["color"] == "#0C2238"


def test_build_lane_payload_includes_spool_id_and_min_bed_temp_fallback():
    instances = [
        DummyInstance(
            0,
            [
                {
                    "status": "ready",
                    "material": "ABS",
                    "color": [16, 32, 48],
                    "temp": 245,
                    "hotbed_temp": {"max": 0, "min": 105},
                    "sku": "12345",
                },
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
                {"status": "empty", "material": "", "color": [0, 0, 0], "temp": 0},
            ],
        ),
    ]
    adapter, _ = make_adapter(instances, enabled=True)
    lane1 = adapter._build_lane_payload()["lane1"]

    assert lane1["spool_id"] == 12345
    assert lane1["bed_temp"] == 105


def test_lane_key_and_conversion_edge_cases():
    assert MoonrakerLaneSyncAdapter._is_lane_key(123) is False
    assert MoonrakerLaneSyncAdapter._is_lane_key("foo1") is False
    assert MoonrakerLaneSyncAdapter._is_lane_key("lane12") is True

    # bad temperature and invalid bed temp dict should fall back to None
    assert MoonrakerLaneSyncAdapter._safe_temp("NaN") is None
    assert MoonrakerLaneSyncAdapter._extract_bed_temp({"max": "bad", "min": None}) is None

    assert MoonrakerLaneSyncAdapter._extract_spool_id({"sku": 77}) == 77
    assert MoonrakerLaneSyncAdapter._extract_spool_id({"sku": "88"}) == 88
    # "²" is digit-like but not parseable by int(), exercises exception path
    assert MoonrakerLaneSyncAdapter._extract_spool_id({"sku": "²"}) is None

    assert MoonrakerLaneSyncAdapter._rgb_to_hex([1]) == ""
    assert MoonrakerLaneSyncAdapter._rgb_to_hex(["x", 1, 2]) == ""


def test_headers_include_api_key_when_set():
    gcode = DummyGCode()
    manager = DummyManager([])
    config = {
        "moonraker_lane_sync_enabled": True,
        "moonraker_lane_sync_url": "http://127.0.0.1:7125",
        "moonraker_lane_sync_namespace": "lane_data",
        "moonraker_lane_sync_api_key": "secret",
        "moonraker_lane_sync_timeout": 0.1,
        "moonraker_lane_sync_unknown_material_mode": "passthrough",
        "moonraker_lane_sync_unknown_material_markers": "???,unknown",
        "moonraker_lane_sync_unknown_material_map_to": "",
    }
    adapter = MoonrakerLaneSyncAdapter(gcode, manager, config)
    headers = adapter._headers()
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Api-Key"] == "secret"


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_http_json_handles_empty_and_json_body(monkeypatch):
    adapter, _ = make_adapter([], enabled=True)

    def fake_urlopen_empty(req, timeout):  # noqa: ARG001
        return _FakeResponse(b"")

    monkeypatch.setattr("ace.moonraker_lane_sync.request.urlopen", fake_urlopen_empty)
    assert adapter._http_json("GET", "/server/info") == {}

    def fake_urlopen_json(req, timeout):  # noqa: ARG001
        # verify POST payload path covers payload encoding branch
        body = req.data.decode("utf-8") if req.data else ""
        assert '"k"' in body and '"v"' in body
        return _FakeResponse(b'{"result":{"ok":true}}')

    monkeypatch.setattr("ace.moonraker_lane_sync.request.urlopen", fake_urlopen_json)
    assert adapter._http_json("POST", "/x", payload={"k": "v"}) == {"result": {"ok": True}}


def test_get_namespace_items_handles_404_and_non_dict_value(monkeypatch):
    adapter, _ = make_adapter([], enabled=True)

    def raise_404(method, path, payload=None):  # noqa: ARG001
        raise error.HTTPError(url="u", code=404, msg="not found", hdrs=None, fp=io.BytesIO())

    monkeypatch.setattr(adapter, "_http_json", raise_404)
    assert adapter._get_namespace_items() == {}

    monkeypatch.setattr(adapter, "_http_json", lambda method, path, payload=None: {"result": {"value": []}})
    assert adapter._get_namespace_items() == {}

    monkeypatch.setattr(adapter, "_http_json", lambda method, path, payload=None: {"result": {"value": {"lane1": {"lane": "0"}}}})
    assert adapter._get_namespace_items() == {"lane1": {"lane": "0"}}


def test_set_and_delete_item_call_http_json(monkeypatch):
    adapter, _ = make_adapter([], enabled=True)
    calls = []

    def record_call(method, path, payload=None):
        calls.append((method, path, payload))
        return {}

    monkeypatch.setattr(adapter, "_http_json", record_call)
    adapter._set_item("lane1", {"lane": "0"})
    adapter._delete_item("lane1")

    assert calls[0] == (
        "POST",
        "/server/database/item",
        {"namespace": "lane_data", "key": "lane1", "value": {"lane": "0"}},
    )
    assert calls[1][0] == "DELETE"
    assert calls[1][1].startswith("/server/database/item?")
