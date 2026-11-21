// Bastl firmware authoring editor (SPEC.md §5.2).
//
// FX Wizard: rhythm editor + sequencer length. Wave Bard adds a scales editor,
// a process-samples selector, a quality selector and sample banks. The editor
// is a singleton overlay: openEditor(device, seed) shows it, and the Save
// button serializes the state and hands it to the draft submitter set via
// setDraftSubmitter().
//
// Steps are 64-bit-safe: represented as decimal strings backed by BigInt so a
// full 64-step mask round-trips through JSON, matching server/pattern.py's
// integer bitmask (first step = most significant bit).

import { BASTL_MEMORY_SIZE, formatMiB, waveBardUsage } from "./usage.js";

const DROP_TYPE = "application/x-sample-hub";

const SEQUENCER_CLASSICS = [8, 16, 32, 64];
const SEQUENCER_OTHERS = [
  5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 17, 20, 21, 24, 36, 42, 48, 54, 60, 63,
];
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const BLACK_KEYS = new Set([1, 3, 6, 8, 10]);
const WAVE_BARD_RATES = { HIGH: 44100, LOW: 22050, TELEPHONE: 11025 };
const PROCESS_OPTIONS = [
  { key: "fadeIn", label: "Fade in 0.005 s" },
  { key: "fadeOut", label: "Fade out 0.005 s" },
  { key: "trimQuiet", label: "Trim quiet (< -48 dB)" },
  { key: "maximize", label: "Maximize to -0.1 dB" },
];

// Loader limits (mirror server/pattern.py).
const FX_WIZARD_RHYTHMS = [3, 64];
const WAVE_BARD_BANKS = [1, 32];
const WAVE_BARD_SAMPLES_PER_BANK = [3, 32];
const WAVE_BARD_SCALES = [3, 32];
const WAVE_BARD_RHYTHMS = [3, 32];
const FX_WIZARD_DEFAULT_LENGTH = 8;
const WAVE_BARD_DEFAULT_LENGTH = 16;

let device = null;
let state = null;
let selectedRhythm = 0;
let selectedScale = 0;
let onApply = null;
let defaultApply = null;
let els = null;
let bound = false;

function isWaveBard() {
  return device && device.id === "wave_bard";
}

// ---- step / semitone bit helpers (BigInt for 64-step safety) ------------

function stepActive(steps, index, length) {
  return (BigInt(steps) & (1n << BigInt(length - 1 - index))) !== 0n;
}

function toggleStep(steps, index, length) {
  const bit = 1n << BigInt(length - 1 - index);
  return (BigInt(steps) ^ bit).toString();
}

function semitoneActive(mask, index) {
  return ((mask >> index) & 1) === 1;
}

function toggleSemitone(mask, index) {
  return mask ^ (1 << index);
}

function activeCount(steps, length) {
  let n = 0;
  for (let i = 0; i < length; i += 1) if (stepActive(steps, i, length)) n += 1;
  return n;
}

// ---- state --------------------------------------------------------------

function emptyState(dev) {
  const wave = dev.id === "wave_bard";
  const base = {
    sequencerLength: wave ? WAVE_BARD_DEFAULT_LENGTH : FX_WIZARD_DEFAULT_LENGTH,
    rhythms: [],
  };
  if (wave) {
    base.sampleRate = WAVE_BARD_RATES.HIGH;
    base.audioProcessing = { fadeIn: false, fadeOut: false, trimQuiet: false, maximize: false };
    base.banks = [];
    base.scales = [];
  }
  return base;
}

function newRhythm(length) {
  return {
    name: `rhythm ${state.rhythms.length + 1}`,
    steps: (1n << BigInt(length - 1)).toString(),
  };
}

function newScale() {
  return { name: `scale ${state.scales.length + 1}`, semitones: 1 };
}

function leafName(ref) {
  const parts = String(ref).split("/");
  return parts[parts.length - 1] || "sample";
}

function readDragData(event) {
  const raw = event.dataTransfer.getData(DROP_TYPE);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

// ---- element lookup -----------------------------------------------------

function ensureEls() {
  if (els) return els;
  els = {
    overlay: document.getElementById("editor"),
    title: document.getElementById("editor-title"),
    length: document.getElementById("editor-length"),
    rhythmGrid: document.getElementById("editor-rhythm-grid"),
    rhythmList: document.getElementById("editor-rhythm-list"),
    addRhythm: document.getElementById("editor-add-rhythm"),
    scales: document.getElementById("editor-section-scales"),
    scaleGrid: document.getElementById("editor-scale-grid"),
    scaleList: document.getElementById("editor-scale-list"),
    addScale: document.getElementById("editor-add-scale"),
    process: document.getElementById("editor-section-process"),
    processBoxes: document.getElementById("editor-process"),
    quality: document.getElementById("editor-quality"),
    banks: document.getElementById("editor-section-banks"),
    bankList: document.getElementById("editor-banks"),
    addBank: document.getElementById("editor-add-bank"),
    status: document.getElementById("editor-status"),
    save: document.getElementById("editor-save"),
    close: document.getElementById("editor-close"),
  };
  return els;
}

// ---- rendering ----------------------------------------------------------

function render() {
  const el = ensureEls();
  const wave = isWaveBard();
  el.title.textContent = device.label;
  el.length.value = String(state.sequencerLength);

  renderRhythmGrid();
  renderRhythmList();

  el.scales.hidden = !wave;
  el.process.hidden = !wave;
  el.banks.hidden = !wave;
  if (wave) {
    renderScaleGrid();
    renderScaleList();
    el.quality.value = String(state.sampleRate);
    for (const opt of PROCESS_OPTIONS) {
      el.processBoxes.querySelector(`[data-process="${opt.key}"]`).checked =
        Boolean(state.audioProcessing[opt.key]);
    }
    renderBanks();
  }
  updateStatus();
}

function renderRhythmGrid() {
  const el = ensureEls();
  el.rhythmGrid.textContent = "";
  const rhythm = state.rhythms[selectedRhythm];
  const length = state.sequencerLength;
  if (!rhythm) {
    const empty = document.createElement("p");
    empty.className = "editor-empty";
    empty.textContent = "add a rhythm to start editing";
    el.rhythmGrid.appendChild(empty);
    return;
  }
  for (let i = 0; i < length; i += 1) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "step";
    if (stepActive(rhythm.steps, i, length)) btn.classList.add("active");
    btn.title = `step ${i + 1}`;
    btn.addEventListener("click", () => {
      rhythm.steps = toggleStep(rhythm.steps, i, length);
      render();
    });
    el.rhythmGrid.appendChild(btn);
  }
}

function renderRhythmList() {
  const el = ensureEls();
  el.rhythmList.textContent = "";
  state.rhythms.forEach((rhythm, i) => {
    const li = document.createElement("li");
    li.className = "editor-list-item" + (i === selectedRhythm ? " selected" : "");

    const name = document.createElement("input");
    name.type = "text";
    name.value = rhythm.name;
    name.addEventListener("input", () => {
      rhythm.name = name.value;
    });

    const count = document.createElement("span");
    count.className = "editor-count";
    count.textContent = `${activeCount(rhythm.steps, state.sequencerLength)}/${state.sequencerLength}`;

    const del = document.createElement("button");
    del.type = "button";
    del.className = "editor-del";
    del.textContent = "×";
    del.title = "remove rhythm";
    del.addEventListener("click", () => {
      state.rhythms.splice(i, 1);
      selectedRhythm = Math.min(selectedRhythm, state.rhythms.length - 1);
      render();
    });

    li.append(name, count, del);
    li.addEventListener("click", (event) => {
      if (event.target === name || event.target === del) return;
      selectedRhythm = i;
      render();
    });
    el.rhythmList.appendChild(li);
  });
}

function renderScaleGrid() {
  const el = ensureEls();
  el.scaleGrid.textContent = "";
  const scale = state.scales[selectedScale];
  if (!scale) {
    const empty = document.createElement("p");
    empty.className = "editor-empty";
    empty.textContent = "add a scale to start editing";
    el.scaleGrid.appendChild(empty);
    return;
  }
  NOTE_NAMES.forEach((note, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "step scale-step";
    if (BLACK_KEYS.has(i)) btn.classList.add("black");
    if (semitoneActive(scale.semitones, i)) btn.classList.add("active");
    btn.textContent = note;
    btn.title = note;
    btn.addEventListener("click", () => {
      scale.semitones = toggleSemitone(scale.semitones, i);
      render();
    });
    el.scaleGrid.appendChild(btn);
  });
}

function renderScaleList() {
  const el = ensureEls();
  el.scaleList.textContent = "";
  state.scales.forEach((scale, i) => {
    const li = document.createElement("li");
    li.className = "editor-list-item" + (i === selectedScale ? " selected" : "");

    const name = document.createElement("input");
    name.type = "text";
    name.value = scale.name;
    name.addEventListener("input", () => {
      scale.name = name.value;
    });

    const count = document.createElement("span");
    count.className = "editor-count";
    count.textContent = `${semitoneCount(scale.semitones)} notes`;

    const del = document.createElement("button");
    del.type = "button";
    del.className = "editor-del";
    del.textContent = "×";
    del.title = "remove scale";
    del.addEventListener("click", () => {
      state.scales.splice(i, 1);
      selectedScale = Math.min(selectedScale, state.scales.length - 1);
      render();
    });

    li.append(name, count, del);
    li.addEventListener("click", (event) => {
      if (event.target === name || event.target === del) return;
      selectedScale = i;
      render();
    });
    el.scaleList.appendChild(li);
  });
}

function semitoneCount(mask) {
  let n = 0;
  for (let i = 0; i < 12; i += 1) if (semitoneActive(mask, i)) n += 1;
  return n;
}

function renderBanks() {
  const el = ensureEls();
  el.bankList.textContent = "";
  state.banks.forEach((bank, bi) => {
    const block = document.createElement("div");
    block.className = "bank";

    const head = document.createElement("div");
    head.className = "bank-head";
    const name = document.createElement("input");
    name.type = "text";
    name.value = bank.name;
    name.addEventListener("input", () => {
      bank.name = name.value;
    });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "editor-del";
    del.textContent = "×";
    del.title = "remove bank";
    del.addEventListener("click", () => {
      state.banks.splice(bi, 1);
      render();
    });
    head.append(name, del);

    const list = document.createElement("ul");
    list.className = "bank-samples";
    if (bank.samples.length === 0) {
      const hint = document.createElement("li");
      hint.className = "editor-empty";
      hint.textContent = "drop sample files here";
      list.appendChild(hint);
    }
    bank.samples.forEach((sample, si) => {
      const li = document.createElement("li");
      li.className = "bank-sample";
      const badge = document.createElement("span");
      badge.className = "badge sample";
      badge.textContent = "sample";
      li.append(badge, document.createTextNode(sample.name));
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "editor-del";
      rm.textContent = "×";
      rm.title = "remove sample";
      rm.addEventListener("click", () => {
        bank.samples.splice(si, 1);
        render();
      });
      li.appendChild(rm);
      list.appendChild(li);
    });
    wireBankDrop(list, bank);

    block.append(head, list);
    el.bankList.appendChild(block);
  });
}

function wireBankDrop(list, bank) {
  list.addEventListener("dragover", (event) => {
    if (!event.dataTransfer.types.includes(DROP_TYPE)) return;
    const data = readDragData(event);
    if (!data || data.sourceType !== "sample") return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    list.classList.add("bank-drop");
  });
  list.addEventListener("dragleave", () => list.classList.remove("bank-drop"));
  list.addEventListener("drop", (event) => {
    list.classList.remove("bank-drop");
    if (!event.dataTransfer.types.includes(DROP_TYPE)) return;
    event.preventDefault();
    const data = readDragData(event);
    if (!data || data.sourceType !== "sample") return;
    bank.samples.push({
      name: leafName(data.sourceRef),
      source_device: data.sourceDevice,
      source_ref: data.sourceRef,
      bytes: Number(data.sourceBytes) || 0,
    });
    render();
  });
}

// ---- validation (client mirror of server/pattern.py) --------------------

function checkList(items, [min, max], kind, maskOf) {
  if (items.length < min) return `${kind}s need at least ${min} (have ${items.length})`;
  if (items.length > max) return `at most ${max} ${kind}s`;
  for (const item of items) {
    const mask = maskOf(item);
    if (!mask || BigInt(mask) === 0n) return `${kind} "${item.name}" is empty`;
  }
  return null;
}

function validateState() {
  const wave = isWaveBard();
  if (wave) {
    if (state.banks.length < WAVE_BARD_BANKS[0]) {
      return `need at least ${WAVE_BARD_BANKS[0]} bank (have ${state.banks.length})`;
    }
    if (state.banks.length > WAVE_BARD_BANKS[1]) {
      return `at most ${WAVE_BARD_BANKS[1]} banks`;
    }
    const counts = new Set(state.banks.map((b) => b.samples.length));
    if (counts.size > 1) return "all banks must have the same number of samples";
    const count = state.banks[0] ? state.banks[0].samples.length : 0;
    if (count < WAVE_BARD_SAMPLES_PER_BANK[0]) {
      return `each bank needs ${WAVE_BARD_SAMPLES_PER_BANK[0]}..${WAVE_BARD_SAMPLES_PER_BANK[1]} samples (have ${count})`;
    }
    if (count > WAVE_BARD_SAMPLES_PER_BANK[1]) {
      return `each bank needs ${WAVE_BARD_SAMPLES_PER_BANK[0]}..${WAVE_BARD_SAMPLES_PER_BANK[1]} samples`;
    }
    const scaleErr = checkList(state.scales, WAVE_BARD_SCALES, "scale", (s) => s.semitones);
    if (scaleErr) return scaleErr;
    return checkList(state.rhythms, WAVE_BARD_RHYTHMS, "rhythm", (r) => r.steps);
  }
  return checkList(state.rhythms, FX_WIZARD_RHYTHMS, "rhythm", (r) => r.steps);
}

function memoryUsage() {
  if (!isWaveBard()) return 0;
  return waveBardUsage({
    scales: state.scales,
    rhythms: state.rhythms,
    banks: state.banks,
  });
}

function updateStatus() {
  const el = ensureEls();
  const err = validateState();
  if (err) {
    el.status.textContent = err;
    el.status.className = "editor-status error";
    el.save.disabled = true;
    return;
  }
  if (isWaveBard()) {
    const n = state.banks.length;
    const used = memoryUsage();
    const readout = `${formatMiB(used)} / ${formatMiB(BASTL_MEMORY_SIZE)}`;
    if (used > BASTL_MEMORY_SIZE) {
      el.status.textContent = `${readout} — over the ${formatMiB(BASTL_MEMORY_SIZE)} memory budget`;
      el.status.className = "editor-status error";
      el.save.disabled = true;
      return;
    }
    el.status.textContent =
      `${state.rhythms.length} rhythms · ${state.scales.length} scales · ` +
      `${n} bank${n === 1 ? "" : "s"} · ${readout} · ready`;
  } else {
    el.status.textContent = `${state.rhythms.length} rhythms · ready`;
  }
  el.status.className = "editor-status ok";
  el.save.disabled = false;
}

// ---- serialize / save ---------------------------------------------------

export function serialize() {
  const body = {
    target: device.id,
    sequencerLength: state.sequencerLength,
    rhythms: state.rhythms.map((r) => ({ name: r.name, steps: r.steps })),
  };
  if (isWaveBard()) {
    body.sampleRate = state.sampleRate;
    body.audioProcessing = { ...state.audioProcessing };
    body.banks = state.banks.map((bank) => ({
      name: bank.name,
      samples: bank.samples.map((s) => ({
        name: s.name,
        source_device: s.source_device,
        source_ref: s.source_ref,
      })),
    }));
    body.scales = state.scales.map((s) => ({ name: s.name, semitones: s.semitones }));
  }
  return body;
}

async function saveDraft() {
  const el = ensureEls();
  if (validateState() || memoryUsage() > BASTL_MEMORY_SIZE || !onApply) return;
  el.save.disabled = true;
  el.save.textContent = "applying…";
  const ok = await onApply(serialize());
  if (ok) {
    closeEditor();
  } else {
    el.save.disabled = false;
    el.save.textContent = "Apply (and flash)";
  }
}

// ---- seed ---------------------------------------------------------------

function applySeed(seed) {
  if (!seed) return;
  if (seed.kind === "sample" && isWaveBard()) {
    if (state.banks.length === 0) state.banks.push({ name: "bank 1", samples: [] });
    state.banks[0].samples.push({
      name: leafName(seed.sourceRef),
      source_device: seed.sourceDevice,
      source_ref: seed.sourceRef,
      bytes: Number(seed.sourceBytes) || 0,
    });
  } else if (seed.kind === "rhythm") {
    state.rhythms.push({
      name: seed.name || `rhythm ${state.rhythms.length + 1}`,
      steps: seed.steps || (1n << BigInt(state.sequencerLength - 1)).toString(),
    });
    selectedRhythm = state.rhythms.length - 1;
  }
}

// ---- lifecycle ----------------------------------------------------------

function bind() {
  if (bound) return;
  bound = true;
  const el = ensureEls();
  el.close.addEventListener("click", closeEditor);
  el.overlay.addEventListener("click", (event) => {
    if (event.target === el.overlay) closeEditor();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && els && !els.overlay.hidden) closeEditor();
  });
  el.length.addEventListener("change", () => {
    state.sequencerLength = Number(el.length.value);
    selectedRhythm = Math.min(selectedRhythm, state.rhythms.length - 1);
    render();
  });
  el.addRhythm.addEventListener("click", () => {
    state.rhythms.push(newRhythm(state.sequencerLength));
    selectedRhythm = state.rhythms.length - 1;
    render();
  });
  el.addScale.addEventListener("click", () => {
    state.scales.push(newScale());
    selectedScale = state.scales.length - 1;
    render();
  });
  el.quality.addEventListener("change", () => {
    state.sampleRate = Number(el.quality.value);
  });
  el.addBank.addEventListener("click", () => {
    state.banks.push({ name: `bank ${state.banks.length + 1}`, samples: [] });
    render();
  });
  for (const opt of PROCESS_OPTIONS) {
    const input = el.processBoxes.querySelector(`[data-process="${opt.key}"]`);
    input.addEventListener("change", () => {
      state.audioProcessing[opt.key] = input.checked;
    });
  }
  el.save.addEventListener("click", saveDraft);
}

export function setDraftSubmitter(fn) {
  defaultApply = fn;
}

export function openEditor(dev, seed = null, applyCb = null) {
  if (!dev) return;
  device = dev;
  onApply = applyCb || defaultApply;
  state = emptyState(dev);
  selectedRhythm = 0;
  selectedScale = 0;
  ensureEls();
  bind();
  applySeed(seed);
  render();
  els.overlay.hidden = false;
}

export function closeEditor() {
  if (!els || els.overlay.hidden) return;
  els.overlay.hidden = true;
  device = null;
  state = null;
  onApply = null;
  selectedRhythm = 0;
  selectedScale = 0;
}
