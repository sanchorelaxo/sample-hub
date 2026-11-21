"""Device source read-back — list + read the samples a source device serves.

TODO_1.txt item 2: sources display their content and each file is draggable.
This is the server half of that: ``list_source`` enumerates a device's files
and ``read_source`` decodes one of them to the canonical 48 kHz stereo float32
form the convert matrix already consumes (via ``library.normalize_to_bytes``),
so a device-to-device transfer flows through the same path as a library drop.

Sink-only devices have no source: ``list_source`` returns [] and ``read_source``
raises ``SourceError``. Which devices are sources comes from the device tree,
never hardcoded here — add a device to ``server/devices.json`` and it is a
source as soon as ``"source": true``.
"""

from __future__ import annotations

from pathlib import Path

from server.device_tree import tree
from server.library import normalize_bytes, normalize_to_bytes
from server.smp import smp_to_wav

_OP1_BANKS = ("synth", "drum", "tape", "album")
_OP1_SUFFIXES = {".aif", ".aiff"}
_SP404_EXPORT_SUFFIXES = {".wav", ".aif", ".aiff", ".smp"}


class SourceError(LookupError):
    """Raised when a device source file cannot be found or read."""


def _is_source(device_id: str) -> bool:
    try:
        return tree().source(device_id)
    except KeyError:
        return False


def list_source(device_id: str, roots: dict | None = None) -> list[dict]:
    """Enumerate a source device's draggable files ([] for sink-only devices).

    Each entry carries the fields the client needs to render a draggable row:
    ``id`` (== ``rel``), ``name``, ``rel`` (device-relative path), ``bytes``,
    and ``kind`` (which OP-1 bank, or ``"export"`` for the SP-404).
    """
    roots = roots or {}
    if not _is_source(device_id):
        return []
    if device_id == "op1":
        return _list_op1(roots.get("op1_mount"))
    if device_id == "sp404":
        return _list_sp404(roots.get("sp404_card_root"))
    return []


def read_source(device_id: str, source_ref: str, roots: dict | None = None) -> bytes:
    """Decode one source file (by ``rel``) to canonical WAV bytes."""
    roots = roots or {}
    if not _is_source(device_id):
        raise SourceError(f"{device_id!r} is not a source")
    if device_id == "op1":
        path = _resolve_inside(roots.get("op1_mount"), source_ref)
        return normalize_to_bytes(path)
    if device_id == "sp404":
        path = _resolve_inside(roots.get("sp404_card_root"), source_ref)
        if path.suffix.lower() == ".smp":
            return normalize_bytes(smp_to_wav(path.read_bytes()))
        return normalize_to_bytes(path)
    raise SourceError(f"no source reader for {device_id!r}")


def _list_op1(mount) -> list[dict]:
    if mount is None:
        return []
    base = Path(mount)
    entries: list[dict] = []
    for bank in _OP1_BANKS:
        folder = base / bank
        if not folder.is_dir():
            continue
        for p in sorted(folder.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in _OP1_SUFFIXES:
                continue
            rel = p.relative_to(base).as_posix()
            entries.append(
                {"id": rel, "name": p.name, "rel": rel, "bytes": p.stat().st_size, "kind": bank}
            )
    return entries


def _list_sp404(card_root) -> list[dict]:
    if card_root is None:
        return []
    base = Path(card_root)
    export_dir = base / "EXPORT"
    if not export_dir.is_dir():
        return []
    entries: list[dict] = []
    for p in sorted(export_dir.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in _SP404_EXPORT_SUFFIXES:
            continue
        rel = p.relative_to(base).as_posix()
        entries.append(
            {"id": rel, "name": p.name, "rel": rel, "bytes": p.stat().st_size, "kind": "export"}
        )
    return entries


def _resolve_inside(root, source_ref: str) -> Path:
    if root is None:
        raise SourceError(f"{source_ref!r}: no source root mounted")
    base = Path(root).resolve()
    target = (base / source_ref).resolve()
    if target != base and base not in target.parents:
        raise SourceError(f"{source_ref!r} escapes the source root")
    if not target.is_file():
        raise SourceError(f"{source_ref!r}: not found")
    return target
