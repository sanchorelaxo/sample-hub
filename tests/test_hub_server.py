"""Milestone 9 HTTP layer: stdlib server exposing status, library, jobs, static.

Every test starts a real ``ThreadingHTTPServer`` on an ephemeral port and talks
to it with ``http.client``. The hardware roots are injected (an empty sysfs
tree, an injected library dir, per-target transfer roots), so no test reads the
live machine.
"""

from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from server import hub_server

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def make_wav_bytes(tmp_path: Path, name: str) -> bytes:
    src = tmp_path / f"{name}-src.wav"
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=330:duration=0.25",
            "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    return src.read_bytes()


def gates() -> list[bool]:
    return [True, False, True, False, True, False, True, False,
            True, False, True, False, True, False, True, False]


@pytest.fixture
def hub(tmp_path):
    static_dir = tmp_path / "www"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>rack</html>")

    library_dir = tmp_path / "library"
    card_root = tmp_path / "card"

    probe_kwargs = {
        "block_dir": tmp_path / "sys-block",
        "tty_dir": tmp_path / "sys-tty",
        "usb_dir": tmp_path / "sys-usb",
        "mounts_path": tmp_path / "proc-mounts",
        "staging_dir": tmp_path / "staging",
    }
    for key in ("block_dir", "tty_dir", "usb_dir", "staging_dir"):
        Path(probe_kwargs[key]).mkdir()

    roots = {
        "sp404": {"sp404_card_root": card_root},
        "wave_bard": {"wave_bard_out": tmp_path / "staging" / "wave-bard"},
        "fx_wizard": {"fx_wizard_out": tmp_path / "staging" / "fx-wizard"},
        "op1": {"op1_mount": tmp_path / "op1"},
        "spotykach": {},
    }

    server = hub_server.make_server(
        "127.0.0.1", 0,
        library_dir=library_dir,
        static_dir=static_dir,
        probe_kwargs=probe_kwargs,
        roots=roots,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {
        "port": server.server_address[1],
        "library_dir": library_dir,
        "card_root": card_root,
        "tmp_path": tmp_path,
    }
    server.shutdown()
    server.server_close()
    thread.join(timeout=5.0)


def request(port, method, path, body=None, content_type=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10.0)
    headers = {}
    if body is not None:
        headers["Content-Length"] = str(len(body))
    if content_type is not None:
        headers["Content-Type"] = content_type
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, dict(response.getheaders()), data


def request_json(port, method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    status, _, data = request(port, method, path, body, "application/json")
    parsed = json.loads(data.decode("utf-8")) if data else None
    return status, parsed


def upload_sample(port, name, wav):
    status, _, raw = request(
        port, "POST", f"/api/library/samples?name={name}",
        wav, "application/octet-stream",
    )
    return status, json.loads(raw.decode("utf-8")) if raw else None


def poll_job(port, job_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, payload = request_json(port, "GET", f"/api/jobs/{job_id}")
        assert status == 200
        if payload["status"] in ("done", "error"):
            return payload
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


def test_health_endpoint(hub):
    status, payload = request_json(hub["port"], "GET", "/api/health")
    assert status == 200
    assert payload == {"ok": True, "version": hub_server.VERSION}


def test_status_endpoint(hub):
    status, payload = request_json(hub["port"], "GET", "/api/status")
    assert status == 200
    ids = [device["id"] for device in payload["devices"]]
    assert ids == ["op1", "spotykach", "wave_bard", "sp404", "fx_wizard"]
    assert isinstance(payload["generated_at"], (int, float))


def test_devices_endpoint(hub):
    status, payload = request_json(hub["port"], "GET", "/api/devices")
    assert status == 200
    assert isinstance(payload, dict)
    assert set(payload) == {"app", "devices"}
    ids = [device["id"] for device in payload["devices"]]
    assert ids == ["op1", "spotykach", "wave_bard", "sp404", "fx_wizard"]
    by_id = {d["id"]: d for d in payload["devices"]}
    assert by_id["op1"]["sink"] is True and by_id["op1"]["source"] is True
    assert by_id["spotykach"]["source"] is False and by_id["spotykach"]["sink"] is True
    assert by_id["wave_bard"]["is_flashable"] is True
    assert by_id["sp404"]["source"] is True
    assert by_id["fx_wizard"]["is_flashable"] is True


def test_device_source_endpoint(hub):
    mount = hub["tmp_path"] / "op1"
    (mount / "synth" / "user").mkdir(parents=True)
    (mount / "synth" / "user" / "pad.aif").write_bytes(b"x")
    status, payload = request_json(hub["port"], "GET", "/api/devices/op1/source")
    assert status == 200
    assert isinstance(payload, dict)
    rels = [entry["rel"] for entry in payload["entries"]]
    assert "synth/user/pad.aif" in rels


def test_device_source_sink_only_422(hub):
    status, payload = request_json(hub["port"], "GET", "/api/devices/spotykach/source")
    assert status == 422
    assert isinstance(payload, dict)
    assert "not a source" in payload["error"]


def test_device_source_unknown_device_404(hub):
    status, payload = request_json(hub["port"], "GET", "/api/devices/nope/source")
    assert status == 404
    assert isinstance(payload, dict)


def test_library_list_empty(hub):
    status, payload = request_json(hub["port"], "GET", "/api/library")
    assert status == 200
    assert payload == {"samples": [], "rhythms": []}


def test_upload_sample_list_fetch(hub):
    wav = make_wav_bytes(hub["tmp_path"], "kick")
    status, meta = upload_sample(hub["port"], "kick.wav", wav)
    assert status == 201, meta
    assert meta["type"] == "sample"
    assert meta["rate"] == 48000
    assert meta["channels"] == 2
    sample_id = meta["id"]

    status, listing = request_json(hub["port"], "GET", "/api/library")
    assert [entry["id"] for entry in listing["samples"]] == [sample_id]

    status, _, data = request(hub["port"], "GET", f"/api/library/samples/{sample_id}")
    assert status == 200
    assert data == (hub["library_dir"] / "samples" / sample_id).read_bytes()


def test_save_rhythm_list_fetch(hub):
    step_data = {"steps": 16, "gates": gates()}
    status, meta = request_json(
        hub["port"], "POST", "/api/library/rhythms?name=hats", payload=step_data
    )
    assert status == 201, meta
    assert meta["type"] == "rhythm"
    assert meta["steps"] == 16
    rhythm_id = meta["id"]

    status, listing = request_json(hub["port"], "GET", "/api/library")
    assert [entry["id"] for entry in listing["rhythms"]] == [rhythm_id]

    status, fetched = request_json(
        hub["port"], "GET", f"/api/library/rhythms/{rhythm_id}"
    )
    assert status == 200
    assert fetched["steps"] == 16
    assert fetched["gates"] == gates()


def test_upload_rhythm_requires_name(hub):
    status, payload = request_json(
        hub["port"], "POST", "/api/library/rhythms", payload={"steps": 8, "gates": []}
    )
    assert status == 400
    assert "name" in payload["error"]


def test_submit_sp404_job_and_poll(hub):
    wav = make_wav_bytes(hub["tmp_path"], "kick")
    _, meta = upload_sample(hub["port"], "kick.wav", wav)
    sample_id = meta["id"]

    status, accepted = request_json(
        hub["port"], "POST", "/api/convert",
        payload={"source_id": sample_id, "target": "sp404"},
    )
    assert status == 202, accepted
    job_id = accepted["job_id"]

    done = poll_job(hub["port"], job_id)
    assert done["status"] == "done"
    assert done["result"]["result"]["rel"] == f"IMPORT/{sample_id}"
    assert (hub["card_root"] / "IMPORT" / sample_id).is_file()


def test_submit_job_rejected_inline(hub):
    step_data = {"steps": 16, "gates": gates()}
    _, meta = request_json(
        hub["port"], "POST", "/api/library/rhythms?name=hats", payload=step_data
    )
    rhythm_id = meta["id"]

    status, payload = request_json(
        hub["port"], "POST", "/api/convert",
        payload={"source_id": rhythm_id, "target": "sp404"},
    )
    assert status == 422
    assert payload["error"] == "samples only"


def test_submit_job_unknown_source(hub):
    status, payload = request_json(
        hub["port"], "POST", "/api/convert",
        payload={"source_id": "nope.wav", "target": "sp404"},
    )
    assert status == 404


def test_job_status_unknown_id(hub):
    status, _ = request_json(hub["port"], "GET", "/api/jobs/job-999")
    assert status == 404


def test_unknown_route_404(hub):
    status, _ = request_json(hub["port"], "GET", "/api/nope")
    assert status == 404


def test_static_index(hub):
    status, headers, data = request(hub["port"], "GET", "/")
    assert status == 200
    assert "text/html" in headers.get("Content-Type", "")
    assert data == b"<html>rack</html>"
