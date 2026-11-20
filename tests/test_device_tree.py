"""Device tree — the JSON single source of truth (TODO_1.txt item 6).

The default ``server/devices.json`` is pinned here for its capability
classification (items 1 + 4), and ``load(path)`` is exercised against temp
trees so validation errors and defaults are covered without touching the real
file.
"""

from __future__ import annotations

import json

import pytest

from server import device_tree as DT
from server.device_tree import DeviceTreeError


def test_default_tree_classifies_instruments():
    tree = DT.tree()
    caps = {d.id: (d.sink, d.source, d.is_flashable, tuple(d.accepts)) for d in tree.devices}
    assert caps == {
        "op1": (True, True, False, ("sample",)),        # sink-source
        "spotykach": (True, False, False, ("sample",)),  # sink-only
        "wave_bard": (True, False, True, ("sample", "rhythm")),   # sink-only, flashable, both types
        "sp404": (True, True, False, ("sample",)),       # sink-source
        "fx_wizard": (True, False, True, ("rhythm",)),   # sink-only, flashable, rhythm only
    }


def test_default_tree_order_and_labels():
    tree = DT.tree()
    assert tree.order == ("op1", "spotykach", "wave_bard", "sp404", "fx_wizard")
    assert tree.labels["sp404"] == "SP-404MK2"
    assert tree.labels["op1"] == "OP-1"


def test_default_tree_exposes_app_globals():
    tree = DT.tree()
    assert isinstance(tree.app.get("poll_ms"), int)
    assert isinstance(tree.app.get("mount_retry_seconds"), (int, float))


def test_module_level_views_match_default_tree():
    assert DT.DEVICE_ORDER == DT.tree().order
    assert DT.LABELS == DT.tree().labels
    assert DT.APP == DT.tree().app


def test_load_from_path_orders_and_helpers(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({
        "app": {"poll_ms": 1234},
        "devices": [
            {"id": "a", "label": "A", "sink": True, "source": False},
            {"id": "b", "label": "B", "sink": False, "source": True, "is_flashable": True},
        ],
    }))
    tree = DT.load(path)
    assert tree.order == ("a", "b")
    assert tree.app["poll_ms"] == 1234
    assert tree.sink("a") is True
    assert tree.source("a") is False
    assert tree.sink("b") is False
    assert tree.source("b") is True
    assert tree.is_flashable("b") is True


def test_defaults_for_omitted_capability_flags(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"devices": [{"id": "x", "label": "X"}]}))
    device = DT.load(path).by_id("x")
    assert device.sink is True
    assert device.source is False
    assert device.is_flashable is False
    assert device.accepts == ("sample",)
    assert device.params == {}


def test_load_rejects_missing_devices(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"app": {}}))
    with pytest.raises(DeviceTreeError):
        DT.load(path)


def test_load_rejects_empty_devices(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"devices": []}))
    with pytest.raises(DeviceTreeError):
        DT.load(path)


def test_load_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({
        "devices": [
            {"id": "a", "label": "A"},
            {"id": "a", "label": "A2"},
        ],
    }))
    with pytest.raises(DeviceTreeError):
        DT.load(path)


def test_load_rejects_missing_id(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"devices": [{"label": "no-id"}]}))
    with pytest.raises(DeviceTreeError):
        DT.load(path)


def test_load_rejects_missing_label(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"devices": [{"id": "a"}]}))
    with pytest.raises(DeviceTreeError):
        DT.load(path)


def test_load_rejects_non_boolean_flag(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"devices": [{"id": "a", "label": "A", "sink": "yes"}]}))
    with pytest.raises(DeviceTreeError):
        DT.load(path)


def test_load_rejects_bad_accepts(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"devices": [{"id": "a", "label": "A", "accepts": "sample"}]}))
    with pytest.raises(DeviceTreeError):
        DT.load(path)


def test_load_rejects_empty_accepts(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"devices": [{"id": "a", "label": "A", "accepts": []}]}))
    with pytest.raises(DeviceTreeError):
        DT.load(path)


def test_by_id_raises_for_unknown():
    with pytest.raises(KeyError):
        DT.tree().by_id("nope")
