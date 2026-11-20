"""The device tree — one JSON file that names every instrument and its role.

TODO_1.txt item 6: devices are added by editing a single JSON tree, and the
global / app knobs live in that same tree. This module loads and validates
``server/devices.json`` and exposes typed views the rest of the server reads:

  * ``load(path)`` — parse + validate an arbitrary tree (tests inject a temp file)
  * ``tree()``    — the default tree, cached
  * ``APP`` / ``DEVICE_ORDER`` / ``LABELS`` — convenience views of the default

Capability flags (TODO_1.txt items 1 + 4):

  * ``sink``          — the device can receive samples (write to it)
  * ``source``        — the device can serve samples (read from it)
  * ``is_flashable``  — changes need a firmware flash (Wave Bard, FX Wizard)
  * ``accepts``       — the source types this sink will take ("sample", "rhythm")

Sink-only  = sink and not source   (Spotykach, Wave Bard, FX Wizard)
Source-only = source and not sink  (none today, but the model allows it)
Sink-source = sink and source      (OP-1, SP-404: EXPORT is the source dir,
                                    IMPORT is the sink dir)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "devices.json"


class DeviceTreeError(ValueError):
    """Raised when the device tree JSON is missing or malformed."""


@dataclass(frozen=True)
class Device:
    id: str
    label: str
    sink: bool = True
    source: bool = False
    is_flashable: bool = False
    accepts: tuple[str, ...] = ("sample",)
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "sink": self.sink,
            "source": self.source,
            "is_flashable": self.is_flashable,
            "accepts": list(self.accepts),
            "params": self.params,
        }


@dataclass(frozen=True)
class DeviceTree:
    app: dict
    devices: tuple[Device, ...]

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(d.id for d in self.devices)

    @property
    def labels(self) -> dict[str, str]:
        return {d.id: d.label for d in self.devices}

    def by_id(self, device_id: str) -> Device:
        for device in self.devices:
            if device.id == device_id:
                return device
        raise KeyError(device_id)

    def sink(self, device_id: str) -> bool:
        return self.by_id(device_id).sink

    def source(self, device_id: str) -> bool:
        return self.by_id(device_id).source

    def is_flashable(self, device_id: str) -> bool:
        return self.by_id(device_id).is_flashable


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DeviceTreeError(message)


def _parse_device(raw: dict) -> Device:
    _require(
        isinstance(raw.get("id"), str) and raw["id"], "each device needs a non-empty 'id'"
    )
    label = raw.get("label")
    _require(isinstance(label, str) and label, f"device {raw['id']!r} needs a 'label'")
    for flag in ("sink", "source", "is_flashable"):
        if flag in raw:
            _require(isinstance(raw[flag], bool), f"device {raw['id']!r}: '{flag}' must be a boolean")
    params = raw.get("params", {})
    _require(isinstance(params, dict), f"device {raw['id']!r}: 'params' must be an object")
    accepts = raw.get("accepts")
    if accepts is None:
        accepts = ("sample",)
    else:
        msg = f"device {raw['id']!r}: 'accepts' must be a non-empty list of strings"
        _require(isinstance(accepts, list), msg)
        _require(bool(accepts), msg)
        _require(all(isinstance(a, str) for a in accepts), msg)
        accepts = tuple(accepts)
    return Device(
        id=raw["id"],
        label=label,
        sink=raw.get("sink", True),
        source=raw.get("source", False),
        is_flashable=raw.get("is_flashable", False),
        accepts=accepts,
        params=dict(params),
    )


def load(path: Path | str | None = None) -> DeviceTree:
    """Parse + validate a device tree JSON file."""
    tree_path = Path(path) if path is not None else DEFAULT_PATH
    try:
        raw = json.loads(tree_path.read_text())
    except OSError as exc:
        raise DeviceTreeError(f"cannot read device tree {tree_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DeviceTreeError(f"invalid JSON in {tree_path}: {exc}") from exc

    _require(isinstance(raw, dict), "device tree root must be a JSON object")
    app = raw.get("app", {})
    _require(isinstance(app, dict), "'app' must be a JSON object")
    raw_devices = raw.get("devices")
    _require(isinstance(raw_devices, list) and raw_devices, "'devices' must be a non-empty list")

    devices: list[Device] = []
    seen: set[str] = set()
    for entry in raw_devices:
        _require(isinstance(entry, dict), "each device entry must be a JSON object")
        device = _parse_device(entry)
        _require(device.id not in seen, f"duplicate device id {device.id!r}")
        seen.add(device.id)
        devices.append(device)
    return DeviceTree(app=app, devices=tuple(devices))


def _default_path() -> Path:
    override = os.environ.get("SAMPLE_HUB_DEVICES")
    return Path(override).expanduser() if override else DEFAULT_PATH


_default: DeviceTree | None = None


def tree() -> DeviceTree:
    """The default device tree, loaded once and cached."""
    global _default
    if _default is None:
        _default = load(_default_path())
    return _default


def reset() -> None:
    """Forget the cached tree so the next ``tree()`` re-reads the file."""
    global _default
    _default = None


APP = tree().app
DEVICE_ORDER = tree().order
LABELS = tree().labels

__all__ = [
    "Device",
    "DeviceTree",
    "DeviceTreeError",
    "DEFAULT_PATH",
    "load",
    "tree",
    "reset",
    "APP",
    "DEVICE_ORDER",
    "LABELS",
]
