"""Milestone 9 HTTP layer — a stdlib-only hub server.

Serves the rack client (static files) and a small JSON API:

  GET  /api/health                      {"ok": true, "version": "0.1.0"}
  GET  /api/status                      presence dots for the five panes
  GET  /api/library                     cached samples + rhythms (metadata)
  POST /api/library/samples?name=...    raw WAV body -> canonical cache entry
  POST /api/library/rhythms?name=...    JSON step/gate body -> rhythm entry
  GET  /api/library/samples/<id>        canonical WAV bytes
  GET  /api/library/rhythms/<id>        step/gate JSON
  POST /api/convert                     {source_id, target, params?} -> job id
  GET  /api/jobs/<id>                   job status/result

Wrong asset types are refused inline with the SPEC.md §6 reason (HTTP 422)
before a job is queued. Every hardware root is injected through ``probe_kwargs``
and ``roots`` so the whole server is testable without touching live devices.
"""

from __future__ import annotations

import json
import mimetypes
import tempfile
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from server.jobs import JobQueue, reject_reason, run_conversion
from server.library import (
    LibraryError,
    NormalizeError,
    add_rhythm,
    add_sample,
    default_library_dir,
    list_entries,
    resolve,
)
from server.status import status_payload
from server.device_tree import APP, tree
from server.sources import list_source

VERSION = APP.get("version", "0.1.0")


def make_server(
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    library_dir: Path | str | None = None,
    static_dir: Path | str | None = None,
    probe_kwargs: dict | None = None,
    roots: dict | Callable[[str], dict] | None = None,
) -> ThreadingHTTPServer:
    """Build a threaded hub server. ``roots`` maps a target instrument to its
    transfer roots, or is a callable ``target -> roots`` for the live server."""
    library_dir = Path(library_dir) if library_dir is not None else default_library_dir()
    static_dir = Path(static_dir) if static_dir is not None else None
    probe_kwargs = dict(probe_kwargs or {})

    def root_resolver(target: str) -> dict:
        resolver = roots
        if callable(resolver):
            return resolver(target)
        if resolver is None:
            return {}
        return resolver.get(target, {})

    job_queue = JobQueue()

    class HubHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "SampleHub/1.0"

        def log_message(self, format, *args):
            pass

        def _send_json(self, status: int, payload) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, status: int, content_type: str, body: bytes, extra_headers=None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _read_json(self) -> dict:
            raw = self._read_body()
            if not raw:
                raise json.JSONDecodeError("empty body", "", 0)
            return json.loads(raw.decode("utf-8"))

        @staticmethod
        def _query_value(query: dict, key: str):
            values = query.get(key)
            return values[0] if values else None

        def _serve_static(self, rel_path: str) -> None:
            if static_dir is None:
                self._send_json(404, {"error": "not found"})
                return
            base = static_dir.resolve()
            candidate = (base / rel_path).resolve()
            if candidate != base and base not in candidate.parents:
                self._send_json(404, {"error": "not found"})
                return
            if not candidate.is_file():
                self._send_json(404, {"error": "not found"})
                return
            content_type, _ = mimetypes.guess_type(str(candidate))
            self._send_bytes(
                200,
                content_type or "application/octet-stream",
                candidate.read_bytes(),
                {"Cache-Control": "no-store"},
            )

        def _handle_health(self) -> None:
            self._send_json(200, {"ok": True, "version": VERSION})

        def _handle_status(self) -> None:
            self._send_json(200, status_payload(**probe_kwargs))

        def _handle_device_source(self, device_id: str) -> None:
            try:
                device = tree().by_id(device_id)
            except KeyError:
                self._send_json(404, {"error": "unknown device"})
                return
            if not device.source:
                self._send_json(422, {"error": f"{device_id} is not a source"})
                return
            self._send_json(200, {
                "device": device_id,
                "entries": list_source(device_id, root_resolver(device_id)),
            })

        def _handle_library_list(self) -> None:
            self._send_json(200, list_entries(library_dir))

        def _handle_sample_upload(self, query: dict) -> None:
            name = self._query_value(query, "name")
            body = self._read_body()
            if not body:
                self._send_json(400, {"error": "empty upload"})
                return
            upload_dir = library_dir / "_tmp"
            upload_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=upload_dir, suffix=".wav", delete=False
            ) as handle:
                handle.write(body)
                temp_path = Path(handle.name)
            try:
                meta = add_sample(temp_path, name=name, library_dir=library_dir)
            except NormalizeError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            finally:
                temp_path.unlink(missing_ok=True)
            self._send_json(201, meta)

        def _handle_rhythm_save(self, query: dict) -> None:
            name = self._query_value(query, "name")
            if not name:
                self._send_json(400, {"error": "name query parameter is required"})
                return
            try:
                step_data = self._read_json()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._send_json(400, {"error": f"invalid JSON: {exc}"})
                return
            if not isinstance(step_data, dict):
                self._send_json(400, {"error": "rhythm body must be a JSON object"})
                return
            self._send_json(201, add_rhythm(step_data, name, library_dir))

        def _handle_sample_fetch(self, source_id: str) -> None:
            try:
                kind, payload = resolve(source_id, library_dir)
            except LibraryError:
                self._send_json(404, {"error": "unknown source"})
                return
            if kind != "sample":
                self._send_json(404, {"error": "not a sample"})
                return
            assert isinstance(payload, bytes)
            self._send_bytes(200, "audio/wav", payload)

        def _handle_rhythm_fetch(self, source_id: str) -> None:
            try:
                kind, payload = resolve(source_id, library_dir)
            except LibraryError:
                self._send_json(404, {"error": "unknown source"})
                return
            if kind != "rhythm":
                self._send_json(404, {"error": "not a rhythm"})
                return
            self._send_json(200, payload)

        def _handle_job_submit(self) -> None:
            try:
                body = self._read_json()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._send_json(400, {"error": f"invalid JSON: {exc}"})
                return
            source_id = body.get("source_id")
            target = body.get("target")
            if not source_id or not target:
                self._send_json(400, {"error": "source_id and target are required"})
                return
            try:
                kind, payload = resolve(source_id, library_dir)
            except LibraryError:
                self._send_json(404, {"error": "unknown source"})
                return
            reason = reject_reason(kind, target)
            if reason is not None:
                self._send_json(422, {"error": reason})
                return
            params = body.get("params") or {}
            transfer_roots = root_resolver(target)
            job_id = job_queue.submit(
                target, run_conversion, source_id, kind, payload, target, params, transfer_roots
            )
            self._send_json(202, {"job_id": job_id})

        def _handle_job_status(self, job_id: str) -> None:
            try:
                state = job_queue.status(job_id)
            except KeyError:
                self._send_json(404, {"error": "unknown job"})
                return
            self._send_json(200, state)

        def do_GET(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/":
                self._serve_static("index.html")
            elif path == "/api/health":
                self._handle_health()
            elif path == "/api/status":
                self._handle_status()
            elif path == "/api/devices":
                t = tree()
                self._send_json(200, {"app": t.app, "devices": [d.to_dict() for d in t.devices]})
            elif path.startswith("/api/devices/") and path.endswith("/source"):
                self._handle_device_source(path[len("/api/devices/"):-len("/source")])
            elif path == "/api/library":
                self._handle_library_list()
            elif path.startswith("/api/library/samples/"):
                self._handle_sample_fetch(path.rsplit("/", 1)[1])
            elif path.startswith("/api/library/rhythms/"):
                self._handle_rhythm_fetch(path.rsplit("/", 1)[1])
            elif path.startswith("/api/jobs/"):
                self._handle_job_status(path.rsplit("/", 1)[1])
            elif path.startswith("/api/"):
                self._send_json(404, {"error": "not found"})
            else:
                self._serve_static(path.lstrip("/"))

        def do_POST(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/api/library/samples":
                self._handle_sample_upload(query)
            elif path == "/api/library/rhythms":
                self._handle_rhythm_save(query)
            elif path == "/api/convert":
                self._handle_job_submit()
            else:
                self._send_json(404, {"error": "not found"})

    server = ThreadingHTTPServer((host, port), HubHandler)
    server.daemon_threads = True
    return server


__all__ = ["make_server"]
