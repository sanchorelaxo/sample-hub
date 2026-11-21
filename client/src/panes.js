// DOM overlay panes: one pane per instrument (no library pane — TODO_1.txt
// item 3). A sink pane lists staged (pending) transfers and running jobs; a
// source pane lists its draggable files (item 2). The presence readout is
// shared by every pane.
import { colorFor } from "./status.js";

export function buildPanes(container, devices) {
  const panes = new Map();
  for (const device of devices) {
    const pane = document.createElement("section");
    pane.className = "pane instrument";
    pane.dataset.target = device.id;
    pane.dataset.sink = device.sink ? "1" : "";
    pane.dataset.source = device.source ? "1" : "";
    pane.dataset.flashable = device.is_flashable ? "1" : "";
    pane.innerHTML = `
      <h2 class="pane-label">
        <span class="dot red" data-role="dot"></span>${device.label}
        ${device.is_flashable ? '<button class="pane-edit" data-role="edit" type="button" title="edit firmware">edit</button>' : ""}
      </h2>
      <p class="detail" data-role="detail">—</p>
      <ul class="source-list" data-role="source"></ul>
      <p class="empty" data-role="source-empty" hidden>no files</p>
      <ul class="pending" data-role="pending"></ul>
      <ul class="jobs" data-role="jobs"></ul>`;
    container.appendChild(pane);
    panes.set(device.id, {
      el: pane,
      dot: pane.querySelector('[data-role="dot"]'),
      detail: pane.querySelector('[data-role="detail"]'),
      source: pane.querySelector('[data-role="source"]'),
      sourceEmpty: pane.querySelector('[data-role="source-empty"]'),
      pending: pane.querySelector('[data-role="pending"]'),
      jobs: pane.querySelector('[data-role="jobs"]'),
      edit: pane.querySelector('[data-role="edit"]'),
      device,
    });
  }
  return { panes };
}

export function renderSource(handle, deviceId, entries) {
  const pane = handle.panes.get(deviceId);
  if (!pane) return;
  const list = pane.source;
  list.textContent = "";
  const items = entries || [];
  pane.sourceEmpty.hidden = items.length !== 0;
  for (const entry of items) {
    const li = document.createElement("li");
    li.className = "source-item";
    li.draggable = true;
    li.dataset.sourceDevice = deviceId;
    li.dataset.sourceRef = entry.rel;
    li.dataset.sourceType = entry.type || "sample";
    li.dataset.sourceBytes = entry.bytes ?? "";
    const badge = document.createElement("span");
    badge.className = "badge sample";
    badge.textContent = (entry.rel.split(".").pop() || "wav").toLowerCase();
    li.appendChild(badge);
    li.appendChild(document.createTextNode(entry.rel));
    list.appendChild(li);
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
