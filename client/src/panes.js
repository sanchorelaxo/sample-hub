// DOM overlay panes: a library pane plus one pane per instrument, positioned
// over the rack plates. Each instrument pane carries a presence dot, a
// transport readout, a drop zone and a job list (SPEC.md §5.1).
import { INSTRUMENTS } from "./layout.js";
import { colorFor } from "./status.js";

export function buildPanes(container) {
  const library = document.createElement("section");
  library.className = "pane library";
  library.innerHTML = `
    <h2 class="pane-label">Library</h2>
    <ul class="library-list"></ul>
    <p class="empty">nothing cached yet</p>`;
  container.appendChild(library);

  const panes = new Map();
  for (const instrument of INSTRUMENTS) {
    const pane = document.createElement("section");
    pane.className = "pane instrument";
    pane.dataset.target = instrument.id;
    pane.innerHTML = `
      <h2 class="pane-label"><span class="dot red" data-role="dot"></span>${instrument.label}</h2>
      <p class="detail" data-role="detail">—</p>
      <ul class="jobs" data-role="jobs"></ul>`;
    container.appendChild(pane);
    panes.set(instrument.id, {
      el: pane,
      dot: pane.querySelector('[data-role="dot"]'),
      detail: pane.querySelector('[data-role="detail"]'),
      jobs: pane.querySelector('[data-role="jobs"]'),
    });
  }

  return {
    library,
    libraryList: library.querySelector(".library-list"),
    empty: library.querySelector(".empty"),
    panes,
  };
}

export function renderLibrary(handle, entries) {
  const { libraryList, empty } = handle;
  libraryList.textContent = "";
  const items = [
    ...(entries.samples || []).map((s) => ({ id: s.id, name: s.name, type: "sample" })),
    ...(entries.rhythms || []).map((r) => ({ id: r.id, name: r.name, type: "rhythm" })),
  ];
  empty.hidden = items.length > 0;
  for (const item of items) {
    const li = document.createElement("li");
    li.className = "library-item";
    li.draggable = true;
    li.dataset.sourceId = item.id;
    li.dataset.sourceType = item.type;
    const badge = document.createElement("span");
    badge.className = `badge ${item.type}`;
    badge.textContent = item.type === "sample" ? "wav" : "rhythm";
    li.appendChild(badge);
    li.appendChild(document.createTextNode(item.name));
    libraryList.appendChild(li);
  }
}

export function setPresence(handle, device) {
  const pane = handle.panes.get(device.id);
  if (!pane) return;
  const color = colorFor(device.state);
  pane.dot.className = `dot ${device.state}`;
  pane.el.dataset.state = device.state;
  pane.el.classList.toggle("present", Boolean(device.present));
  pane.detail.textContent = device.detail || "";
  return color;
}

export function addJob(handle, target, jobId, label) {
  const pane = handle.panes.get(target);
  if (!pane) return null;
  const li = document.createElement("li");
  li.className = "job running";
  li.dataset.jobId = jobId;
  li.innerHTML = `<span class="spinner"></span><span class="job-label">${label}</span>`;
  pane.jobs.appendChild(li);
  return li;
}

export function settleJob(row, ok, message) {
  row.classList.remove("running");
  row.classList.add(ok ? "done" : "failed");
  row.querySelector(".job-label").textContent = message;
  const spinner = row.querySelector(".spinner");
  if (spinner) spinner.remove();
}

export function flashRefused(pane, message) {
  pane.detail.textContent = message;
  pane.el.classList.add("refused");
  setTimeout(() => pane.el.classList.remove("refused"), 1500);
}
