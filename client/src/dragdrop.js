// HTML5 drag-and-drop between device sources and sinks (TODO_1.txt items 2/3).
// A source file dropped on a legal sink is STAGED, not sent (item 5). Illegal
// targets show a red ✗ and refuse the drop; the server stays authoritative on
// asset-type legality (HTTP 422, SPEC.md §6).
import { stage } from "./staging.js";
import { flashRefused } from "./panes.js";
import { openEditor } from "./editor.js";

const DROP_TYPE = "application/x-sample-hub";

function readDrag(event) {
  const raw = event.dataTransfer.getData(DROP_TYPE);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

// Mirrors server/jobs.py reject_reason, driven by the device tree's accepts
// list rather than a hardcoded matrix (TODO_1.txt item 6).
export function rejectReason(sourceType, device) {
  if (!device.sink) return "not a sink";
  const accepts = device.accepts || [];
  if (accepts.includes(sourceType)) return null;
  if (sourceType === "sample" && accepts.includes("rhythm")) {
    return "rhythms only — no sample input";
  }
  if (sourceType === "rhythm" && accepts.includes("sample")) {
    return "samples only";
  }
  return `${device.id} does not accept ${sourceType}`;
}

export function wireDragDrop(handle, onChanged) {
  for (const pane of handle.panes.values()) {
    if (pane.device.source) {
      pane.source.addEventListener("dragstart", (event) => {
        const item = event.target.closest(".source-item");
        if (!item) return;
        event.dataTransfer.setData(
          DROP_TYPE,
          JSON.stringify({
            sourceDevice: item.dataset.sourceDevice,
            sourceRef: item.dataset.sourceRef,
            sourceType: item.dataset.sourceType || "sample",
          }),
        );
        event.dataTransfer.effectAllowed = "copy";
      });
    }

    if (!pane.device.sink) continue;
    const { el } = pane;

    el.addEventListener("dragover", (event) => {
      if (!event.dataTransfer.types.includes(DROP_TYPE)) return;
      const data = readDrag(event);
      if (!data) return;
      event.preventDefault();
      const reason =
        data.sourceDevice === pane.device.id
          ? "same device"
          : rejectReason(data.sourceType, pane.device);
      if (reason) {
        event.dataTransfer.dropEffect = "none";
        el.classList.add("illegal");
        el.classList.remove("drop-target");
      } else {
        event.dataTransfer.dropEffect = "copy";
        el.classList.add("drop-target");
        el.classList.remove("illegal");
      }
    });

    const clear = () => el.classList.remove("drop-target", "illegal");
    el.addEventListener("dragleave", clear);

    el.addEventListener("drop", (event) => {
      clear();
      if (!event.dataTransfer.types.includes(DROP_TYPE)) return;
      event.preventDefault();
      const data = readDrag(event);
      if (!data) return;
      if (data.sourceDevice === pane.device.id) {
        flashRefused(pane, "same device");
        return;
      }
      const reason = rejectReason(data.sourceType, pane.device);
      if (reason) {
        flashRefused(pane, reason);
        return;
      }
      if (pane.device.is_flashable) {
        // Flashable Bastl sinks open the authoring editor instead of staging a
        // one-shot transfer: a dropped sample seeds a Wave Bard bank, a dropped
        // rhythm seeds the rhythm list (SPEC.md §5.2).
        openEditor(
          pane.device,
          data.sourceType === "sample"
            ? { kind: "sample", sourceDevice: data.sourceDevice, sourceRef: data.sourceRef }
            : { kind: "rhythm", name: data.sourceRef },
        );
        return;
      }
      if (el.dataset.state !== "green") {
        flashRefused(pane, "instrument not ready");
        return;
      }
      stage(data.sourceDevice, data.sourceRef, data.sourceType, pane.device.id);
      onChanged();
    });
  }
}
