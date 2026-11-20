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

import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from server import hub_server

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"

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
    "src/status.js",
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


def test_rack_order_matches_server_device_order():
    from server.status import DEVICE_ORDER

    layout = (CLIENT_DIR / "src/layout.js").read_text()
    ids = re.findall(r'id:\s*"([a-z0-9_]+)"', layout)
    assert ids == list(DEVICE_ORDER)


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
