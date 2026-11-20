// HTML5 drag-and-drop: a library item dropped on an instrument pane submits
// the source/target pair to POST /api/convert, then polls the job to
// completion. The client mirrors the server's asset-type matrix for instant
// inline refusal, but the server stays authoritative (HTTP 422, SPEC.md §6).
import { INSTRUMENTS } from "./layout.js";
import { addJob, settleJob, flashRefused } from "./panes.js";

// Mirrors server/jobs.py: sample targets refuse rhythms, fx_wizard refuses
// samples, and wave_bard accepts both.
const SAMPLES_ONLY = new Set(["op1", "spotykach", "sp404"]);
const RHYTHMS_ONLY = new Set(["fx_wizard"]);

const DROP_TYPE = "application/x-sample-hub";
const DROP_TYPE_KIND = "application/x-sample-hub-type";
const POLL_INTERVAL_MS = 500;
const POLL_LIMIT = 600; // ~5 minutes before a job row gives up

export function rejectReason(sourceType, target) {
  if (RHYTHMS_ONLY.has(target) && sourceType === "sample") {
    return "rhythms only — no sample input";
  }
  if (SAMPLES_ONLY.has(target) && sourceType === "rhythm") {
    return "samples only";
  }
  return null;
}

export function wireDragDrop(handle) {
  const { panes, libraryList } = handle;

  libraryList.addEventListener("dragstart", (event) => {
    const item = event.target.closest(".library-item");
    if (!item) return;
    event.dataTransfer.setData(DROP_TYPE, item.dataset.sourceId);
    event.dataTransfer.setData(DROP_TYPE_KIND, item.dataset.sourceType);
    event.dataTransfer.effectAllowed = "copy";
  });

  for (const instrument of INSTRUMENTS) {
    const pane = panes.get(instrument.id);
    const { el } = pane;

    el.addEventListener("dragover", (event) => {
      if (!event.dataTransfer.types.includes(DROP_TYPE)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      el.classList.add("drop-target");
    });

    el.addEventListener("dragleave", () => el.classList.remove("drop-target"));

    el.addEventListener("drop", (event) => {
      el.classList.remove("drop-target");
      if (!event.dataTransfer.types.includes(DROP_TYPE)) return;
      event.preventDefault();

      const sourceId = event.dataTransfer.getData(DROP_TYPE);
      const sourceType = event.dataTransfer.getData(DROP_TYPE_KIND);
      if (!sourceId) return;

      if (el.dataset.state !== "green") {
        flashRefused(pane, "instrument not ready");
        return;
      }
      const reason = rejectReason(sourceType, instrument.id);
      if (reason) {
        flashRefused(pane, reason);
        return;
      }
      submitConvert(handle, sourceId, instrument.id, pane);
    });
  }
}

async function submitConvert(handle, sourceId, target, pane) {
  let jobId;
  try {
    const res = await fetch("/api/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_id: sourceId, target }),
    });
    const payload = await res.json();
    if (!res.ok) {
      flashRefused(pane, payload.error || `convert refused (${res.status})`);
      return;
    }
    jobId = payload.job_id;
  } catch (err) {
    flashRefused(pane, "server unreachable");
    return;
  }

  const row = addJob(handle, target, jobId, `${target} · ${jobId}`);
  await pollJob(jobId, row);
}

async function pollJob(jobId, row) {
  for (let attempt = 0; attempt < POLL_LIMIT; attempt += 1) {
    if (!row.isConnected) return;
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    let state;
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      state = await res.json();
    } catch (err) {
      settleJob(row, false, "server unreachable");
      return;
    }
    if (state.status === "done") {
      settleJob(row, true, state.result?.result?.rel || "done");
      return;
    }
    if (state.status === "error") {
      settleJob(row, false, state.error || "failed");
      return;
    }
  }
  settleJob(row, false, "timed out");
}
