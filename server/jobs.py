"""Convert dispatch + per-instrument job queue.

``run_conversion`` is the one place a cached source (canonical sample bytes or a
step/gate dict) becomes a device transfer. It refuses wrong asset types inline
with the SPEC.md §6 reasons before any writer runs, then hands off to the
target's writer. ``JobQueue`` runs jobs on one worker thread per instrument so
different instruments convert in parallel while drops on the same instrument
stay serialized (SPEC.md §5.1 "serialized per instrument").

No module here touches live hardware by itself: every transfer takes an
injected root (``sp404_card_root``, ``wave_bard_out``, ``fx_wizard_out``,
``op1_mount``) supplied by the caller. The OP-1 and Spotykach writers need a
real mount / USB link, so they are only exercised under ``--real-devices``.
"""

from __future__ import annotations

import itertools
import queue
import threading
from collections.abc import Callable

from server.devices import fx_wizard, op1, sp404, spotykach, wave_bard
from server.device_tree import tree
from server.status import default_staging_dir

_REJECT_NO_SAMPLE = "rhythms only — no sample input"
_REJECT_NO_RHYTHM = "samples only"


class RejectError(ValueError):
    """A drop the hub refuses before any transfer (wrong asset type, red pane)."""


def reject_reason(source_type: str, target: str) -> str | None:
    """The SPEC.md §6 reason to refuse this drop, or None when it is allowed.

    Target eligibility is read from the device tree (TODO_1.txt item 6): a
    device's ``accepts`` list says which source types it will take, so adding a
    device to ``server/devices.json`` is enough to change the conversion matrix.
    """
    try:
        device = tree().by_id(target)
    except KeyError:
        return f"unknown target {target!r}"
    if not device.sink:
        return f"{target!r} is not a sink"
    accepts = device.accepts
    if source_type in accepts:
        return None
    if source_type == "sample" and "rhythm" in accepts:
        return _REJECT_NO_SAMPLE
    if source_type == "rhythm" and "sample" in accepts:
        return _REJECT_NO_RHYTHM
    return f"{target!r} does not accept {source_type}"


def run_conversion(
    source_name: str,
    source_type: str,
    source_payload,
    target: str,
    params: dict | None = None,
    roots: dict | None = None,
) -> dict:
    """Convert + transfer one cached source to one instrument.

    Returns a JSON-serializable dict ``{"target", "source", "source_type",
    "result"}`` where ``result`` is the writer's own report. Raises
    ``RejectError`` for a drop the hub refuses, and lets any writer failure
    propagate (the job queue records it as an error).
    """
    params = params or {}
    roots = roots or {}
    reason = reject_reason(source_type, target)
    if reason is not None:
        raise RejectError(reason)

    if target == "op1":
        result = op1.write_drum_kit(
            source_payload,
            filename=params.get("filename", source_name),
            mount=roots.get("op1_mount"),
            force=bool(params.get("force", False)),
        )
    elif target == "spotykach":
        result = spotykach.write_sample(
            source_payload,
            tape=params.get("tape", spotykach.TAPE_ORDER[0]),
            slot=int(params.get("slot", 1)),
            preferred=params.get("preferred", "usb"),
            card_root=roots.get("spotykach_card_root"),
        )
    elif target == "sp404":
        result = sp404.stage_import(
            source_payload,
            filename=params.get("filename", source_name),
            card_root=roots.get("sp404_card_root"),
            force=bool(params.get("force", False)),
        )
    elif target == "wave_bard":
        samples = (
            [{"name": source_name, "data": source_payload}]
            if source_type == "sample"
            else []
        )
        rhythms = [source_payload] if source_type == "rhythm" else []
        result = wave_bard.build_draft(
            bank_name=params.get("bank", "bank-a"),
            samples=samples,
            rhythms=rhythms,
            scales=[],
            out_dir=roots.get("wave_bard_out")
            or default_staging_dir() / "wave-bard",
        )
    elif target == "fx_wizard":
        result = fx_wizard.build_draft(
            source_payload,
            out_dir=roots.get("fx_wizard_out"),
        )
    else:
        raise RejectError(reason)

    return {
        "target": target,
        "source": source_name,
        "source_type": source_type,
        "result": result,
    }


_SENTINEL = object()


class JobQueue:
    """A per-instrument FIFO job runner.

    Each instrument gets its own worker thread and its own queue, so jobs on
    different instruments proceed in parallel while jobs on the same instrument
    run strictly in submission order. ``submit`` returns a job id; ``status``
    reads the live ``{"id", "status", "instrument", "result", "error"}`` record.
    """

    def __init__(self) -> None:
        self._queues: dict[str, queue.Queue] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._jobs: dict[str, dict] = {}
        self._guard = threading.Lock()
        self._counter = itertools.count(1)

    def submit(self, instrument: str, task: Callable, *args, **kwargs) -> str:
        with self._guard:
            job_id = f"job-{next(self._counter)}"
            self._jobs[job_id] = {
                "id": job_id,
                "status": "queued",
                "instrument": instrument,
                "result": None,
                "error": None,
            }
            self._ensure_worker(instrument)
            self._queues[instrument].put((job_id, task, args, kwargs))
        return job_id

    def status(self, job_id: str) -> dict:
        return self._jobs[job_id]

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._guard:
            for instrument_queue in self._queues.values():
                instrument_queue.put((_SENTINEL, None, (), {}))
        for worker in self._threads.values():
            worker.join(timeout=timeout)

    def _ensure_worker(self, instrument: str) -> None:
        worker = self._threads.get(instrument)
        if worker is not None and worker.is_alive():
            return
        instrument_queue = self._queues.setdefault(instrument, queue.Queue())
        worker = threading.Thread(
            target=self._worker,
            args=(instrument, instrument_queue),
            name=f"job-{instrument}",
            daemon=True,
        )
        self._threads[instrument] = worker
        worker.start()

    def _worker(self, instrument: str, instrument_queue: queue.Queue) -> None:
        while True:
            job_id, task, args, kwargs = instrument_queue.get()
            if job_id is _SENTINEL:
                return
            self._jobs[job_id]["status"] = "running"
            try:
                self._jobs[job_id]["result"] = task(*args, **kwargs)
                self._jobs[job_id]["status"] = "done"
            except Exception as exc:
                self._jobs[job_id]["status"] = "error"
                self._jobs[job_id]["error"] = str(exc)
            finally:
                instrument_queue.task_done()
