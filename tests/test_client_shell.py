"""Milestone 9d client shell: vendored three.js + the rack client.

These tests serve the real ``client/`` directory and assert three things that
keep a no-build, fully-vendored browser client honest:

1. Every shell asset is served with the right type and ``Cache-Control:
   no-store`` (SPEC.md §4), so a stale rack can never linger.
2. Every module import in the client source resolves to a real local file and
   never to a bare/CDN specifier — the three.js bundle is vendored, not fetched.
3. Every client source module parses as an ES module (``node --check``).

The drag/drop contract itself is exercised end-to-end by the HTTP tests in
``test_hub_server.py``; this file pins the shell's shape, not browser behaviour.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from server import hub_server

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"
DEVICES_JSON = Path(__file__).resolve().parent.parent / "server" / "devices.json"
PHOTOS_DIR = CLIENT_DIR / "assets" / "photos"


def _device_ids() -> list[str]:
    return [device["id"] for device in json.loads(DEVICES_JSON.read_text())["devices"]]

VENDOR_FILES = [
    "vendor/three.core.js",
    "vendor/three.module.js",
    "vendor/OrbitControls.js",
    "vendor/RoomEnvironment.js",
]

SRC_FILES = [
    "src/main.js",
    "src/scene.js",
    "src/layout.js",
    "src/panes.js",
    "src/dragdrop.js",
    "src/editor.js",
    "src/usage.js",
    "src/status.js",
    "src/staging.js",
]


def _request(port, path):
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10.0)
    connection.request("GET", path)
    response = connection.getresponse()
    data = response.read()
    headers = dict(response.getheaders())
    connection.close()
    return response.status, headers, data


@pytest.fixture
def shell(tmp_path):
    """A hub server serving the real client directory, with injected roots so
    nothing reads live hardware."""
    library_dir = tmp_path / "library"
    probe_kwargs = {
        "block_dir": tmp_path / "sys-block",
        "tty_dir": tmp_path / "sys-tty",
        "usb_dir": tmp_path / "sys-usb",
        "mounts_path": tmp_path / "proc-mounts",
        "staging_dir": tmp_path / "staging",
    }
    for key in ("block_dir", "tty_dir", "usb_dir", "staging_dir"):
        Path(probe_kwargs[key]).mkdir()

    server = hub_server.make_server(
        "127.0.0.1", 0,
        library_dir=library_dir,
        static_dir=CLIENT_DIR,
        probe_kwargs=probe_kwargs,
        roots={},
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    thread.join(timeout=5.0)


def _specifiers(text: str) -> list[str]:
    """Relative import/export specifiers from an ES module source."""
    return [
        match.group(2)
        for match in re.finditer(
            r"(?:from\s*|import\s*(?:\(\s*)?)(['\"])(\.{1,2}/[^'\"]+)\1", text
        )
    ]


def _bare_specifiers(text: str) -> list[str]:
    """Any import/export specifier that is NOT relative (a CDN/bare module)."""
    return [
        match.group(2)
        for match in re.finditer(
            r"(?:from\s*|import\s*(?:\(\s*)?)(['\"])([^'\"]+)\1", text
        )
        if not match.group(2).startswith(".")
    ]


def _css_rules(css: str) -> list[tuple[str, str]]:
    """Yield ``(selector, declarations)`` for each top-level CSS rule.

    Strips comments, then brace-matches at depth 0. ``styles.css`` has no
    braces inside strings, so a naive match is safe after the strip."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []
    i, n = 0, len(css)
    while i < n:
        open_brace = css.find("{", i)
        if open_brace == -1:
            break
        selector = css[i:open_brace].strip()
        depth, j = 0, open_brace
        while j < n:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        rules.append((selector, css[open_brace + 1 : j]))
        i = j + 1
    return rules


def _decls_for(css: str, selector: str) -> str | None:
    """Declaration body of the first rule whose selector list contains
    ``selector`` as a standalone selector (comma-split)."""
    for sel, body in _css_rules(css):
        if any(part.strip() == selector for part in sel.split(",")):
            return body
    return None


def test_serves_shell_assets_no_store(shell):
    cases = [
        ("/", "text/html"),
        ("/styles.css", "text/css"),
        ("/src/main.js", "javascript"),
        ("/vendor/three.module.js", "javascript"),
        ("/vendor/three.core.js", "javascript"),
        ("/vendor/OrbitControls.js", "javascript"),
        ("/vendor/RoomEnvironment.js", "javascript"),
    ]
    for path, needle in cases:
        status, headers, data = _request(shell, path)
        assert status == 200, f"{path} -> {status}"
        assert needle in headers.get("Content-Type", ""), f"{path} wrong type"
        assert headers.get("Cache-Control") == "no-store", f"{path} missing no-store"
        assert data, f"{path} served empty"


def test_vendor_bundle_is_complete(shell):
    """three.core.js is the full build; a truncated copy would shrink it."""
    for rel in VENDOR_FILES:
        path = CLIENT_DIR / rel
        assert path.is_file(), f"missing vendored file {rel}"
    # The core build is ~1.4 MB; anything under 1 MB is a partial copy.
    assert (CLIENT_DIR / "vendor/three.core.js").stat().st_size > 1_000_000


def test_client_imports_are_relative_and_resolve():
    for rel in SRC_FILES:
        path = CLIENT_DIR / rel
        assert path.is_file(), f"missing client source {rel}"
        text = path.read_text()
        assert not _bare_specifiers(text), f"{rel} has a bare/CDN import"
        for spec in _specifiers(text):
            target = (path.parent / spec).resolve()
            assert target.is_file(), f"{rel} imports missing {spec}"


def test_vendor_addons_import_the_local_shim():
    for rel in ("vendor/OrbitControls.js", "vendor/RoomEnvironment.js"):
        text = (CLIENT_DIR / rel).read_text()
        assert "./three.module.js" in _specifiers(text), f"{rel} lost its import"
    shim = (CLIENT_DIR / "vendor/three.module.js").read_text()
    assert "./three.core.js" in _specifiers(shim), "three.module.js lost its core import"


def test_client_derives_device_order_from_server():
    """TODO_1.txt item 6: the client must not hardcode the rack. It reads the
    device tree from GET /api/devices, and layout.js stays geometry-only."""
    main = (CLIENT_DIR / "src/main.js").read_text()
    assert "/api/devices" in main, "main.js must fetch the device tree"
    layout = (CLIENT_DIR / "src/layout.js").read_text()
    assert not re.search(r'id:\s*"', layout), "layout.js must stay geometry-only (no device list)"


def test_dragover_permits_drop_without_getdata():
    """Browsers protect the DataTransfer payload during dragover — getData()
    returns "" until drop. So the dragover handler must read the payload from
    the dragstart capture (activeDrag), never from readDrag/getData, or it will
    bail before preventDefault() and the drop event never fires (silent no-op)."""
    source = (CLIENT_DIR / "src/dragdrop.js").read_text()
    assert 'addEventListener("dragover"' in source
    # Slice the dragover listener body (up to the next listener registration).
    dragover = source.split('addEventListener("dragover"')[1].split(
        'addEventListener("dragleave"'
    )[0]
    assert "readDrag" not in dragover, "dragover must not read the payload via getData"
    assert "activeDrag" in dragover, "dragover must read the dragstart-captured payload"
    assert "event.preventDefault()" in dragover, "dragover must permit the drop"


def test_card_overflow_scrolls_within_viewport():
    """TODO_1.txt (card overflow): a card whose list has many entries must
    scroll internally, not run off the top of the screen. The pane is capped
    to the viewport (max-height bound to 100vh) so a tall card's top stays on
    screen, and the source/pending/job lists are the scrollable region
    (overflow-y: auto) so the device label stays pinned while entries scroll."""
    css = (CLIENT_DIR / "styles.css").read_text()
    pane = _decls_for(css, ".pane")
    assert pane is not None, "no .pane rule in styles.css"
    assert re.search(r"max-height\s*:\s*[^;}]*100vh", pane), (
        ".pane must cap its height to the viewport so a tall card's top stays "
        "on screen"
    )
    for selector in (".source-list", ".pending", ".jobs"):
        body = _decls_for(css, selector)
        assert body is not None, f"no {selector} rule in styles.css"
        assert re.search(r"overflow-y\s*:\s*auto", body), (
            f"{selector} must scroll (overflow-y: auto) when the card overflows"
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_client_source_parses_as_esm():
    for rel in SRC_FILES:
        result = subprocess.run(
            ["node", "--check", str(CLIENT_DIR / rel)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{rel} failed to parse:\n{result.stderr}"


def test_launcher_serves_client(tmp_path):
    import importlib

    launcher = importlib.import_module("server.__main__")
    assert launcher.CLIENT_DIR == CLIENT_DIR
    server = launcher.build_server("127.0.0.1", 0, probe_kwargs={}, roots={})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, data = _request(server.server_address[1], "/")
        assert status == 200
        assert b"Sample Hub" in data
        assert "text/html" in headers.get("Content-Type", "")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


# --- Instrument photos on the 3D rack --------------------------------------


def test_rack_photo_matched_by_device_id():
    """Each plate's photo is resolved from the device id, so a device added to
    server/devices.json picks up assets/photos/<id>.jpg with no client edit."""
    scene = (CLIENT_DIR / "src/scene.js").read_text()
    assert "assets/photos/" in scene, "scene.js must load photos from assets/photos/"
    assert "${device.id}.jpg" in scene, (
        "photo filename must be derived from device.id (no hardcoded id map)"
    )


def test_rack_photo_sized_to_plate_width():
    """The photo's width is the plate width; its height follows the source
    image's aspect ratio."""
    scene = (CLIENT_DIR / "src/scene.js").read_text()
    assert re.search(r"PlaneGeometry\(\s*RACK\.plateWidth", scene), (
        "photo plane width must be RACK.plateWidth"
    )
    assert "aspect" in scene, "photo height must follow the image aspect ratio"


def test_z_order_dot_label_photo():
    """Depth order is dot > label > photo, so the indicator stays visible and
    the instrument name sits in front of its photo."""
    scene = (CLIENT_DIR / "src/scene.js").read_text()

    def offset(name):
        match = re.search(rf"{name}_Z_OFFSET\s*=\s*([\d.]+)", scene)
        assert match, f"missing {name}_Z_OFFSET"
        return float(match.group(1))

    assert offset("DOT") > offset("LABEL") > offset("PHOTO"), (
        "dot must be in front of label, which is in front of photo"
    )


def test_label_bold_teal():
    """The instrument name renders in bold teal."""
    scene = (CLIENT_DIR / "src/scene.js").read_text()
    assert "700" in scene, "label font weight must be bold (700)"
    color = re.search(r'LABEL_COLOR\s*=\s*"([#0-9a-fA-F]+)"', scene)
    assert color, "label color must be a named constant"
    assert color.group(1) == "#2dd4bf", "label color must be teal"


def test_photo_asset_for_each_device():
    for device_id in _device_ids():
        assert (PHOTOS_DIR / f"{device_id}.jpg").is_file(), (
            f"missing photo asset for device {device_id!r}"
        )


def test_photo_assets_served_no_store(shell):
    for device_id in _device_ids():
        status, headers, data = _request(shell, f"/assets/photos/{device_id}.jpg")
        assert status == 200, f"/assets/photos/{device_id}.jpg -> {status}"
        assert headers.get("Content-Type", "").startswith("image/"), (
            f"{device_id} photo served with wrong content type"
        )
        assert headers.get("Cache-Control") == "no-store"
        assert data
