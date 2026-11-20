// Client shell entry: renderer, camera, OrbitControls, the rack, and the
// wiring for device-tree bootstrapping, presence polling, source listing,
// staged transfers and drag/drop (SPEC.md §5.1, TODO_1.txt items 2/3/5/6).
import * as THREE from "../vendor/three.module.js";
import { OrbitControls } from "../vendor/OrbitControls.js";
import { RoomEnvironment } from "../vendor/RoomEnvironment.js";
import { buildRack } from "./scene.js";
import {
  buildPanes,
  renderSource,
  setPresence,
  addJob,
  settleJob,
  flashRefused,
} from "./panes.js";
import { wireDragDrop } from "./dragdrop.js";
import { pollStatus, colorFor } from "./status.js";
import {
  pending,
  pendingFor,
  undoLast,
  cancelAll,
  applyChanges,
} from "./staging.js";

const canvas = document.getElementById("scene");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d0f13);
scene.fog = new THREE.Fog(0x0d0f13, 500, 1500);

const camera = new THREE.PerspectiveCamera(
  50,
  window.innerWidth / window.innerHeight,
  1,
  3000,
);
camera.position.set(0, 280, 640);

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.06;
controls.target.set(0, 20, 0);
controls.minDistance = 240;
controls.maxDistance = 1400;
controls.maxPolarAngle = Math.PI * 0.49;
controls.update();

const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

scene.add(new THREE.HemisphereLight(0xb9c6da, 0x14161a, 0.6));

const key = new THREE.DirectionalLight(0xffffff, 2.0);
key.position.set(-160, 320, 220);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
key.shadow.camera.near = 40;
key.shadow.camera.far = 900;
key.shadow.camera.left = -480;
key.shadow.camera.right = 480;
key.shadow.camera.top = 480;
key.shadow.camera.bottom = -480;
key.shadow.bias = -0.0008;
scene.add(key);

const floor = new THREE.Mesh(
  new THREE.PlaneGeometry(2400, 2400),
  new THREE.MeshStandardMaterial({ color: 0x14181f, roughness: 0.95, metalness: 0 }),
);
floor.rotation.x = -Math.PI / 2;
floor.position.y = -80;
floor.receiveShadow = true;
scene.add(floor);

const devicesById = new Map();
let rack = null;
let handle = null;

function applyStatus(payload) {
  for (const device of payload.devices || []) {
    setPresence(handle, device);
    const dot = rack && rack.dots.get(device.id);
    if (dot) dot.material.color.setHex(colorFor(device.state).hex);
  }
}

function refreshSources() {
  if (!handle) return;
  for (const device of devicesById.values()) {
    if (!device.source) continue;
    fetch(`/api/devices/${device.id}/source`)
      .then((res) => (res.ok ? res.json() : null))
      .then((payload) => {
        if (payload) renderSource(handle, device.id, payload.entries || []);
      })
      .catch((err) => console.warn(`source refresh failed for ${device.id}`, err));
  }
}

function renderPending() {
  for (const pane of handle.panes.values()) {
    const list = pane.pending;
    list.textContent = "";
    for (const change of pendingFor(pane.device.id)) {
      const li = document.createElement("li");
      li.className = "pending-item";
      li.textContent = change.sourceRef;
      list.appendChild(li);
    }
  }
}

function renderActions() {
  const actions = document.getElementById("actions");
  const apply = document.getElementById("apply");
  const changes = pending();
  actions.hidden = changes.length === 0;
  const flash = changes.some(
    (change) => devicesById.get(change.target)?.is_flashable,
  );
  apply.textContent = flash ? "Apply (and flash)" : "Apply";
  renderPending();
}

async function submitConvert(change) {
  const pane = handle.panes.get(change.target);
  if (!pane) return;
  let jobId;
  try {
    const res = await fetch("/api/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_device: change.sourceDevice,
        source_ref: change.sourceRef,
        target: change.target,
      }),
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
  const row = addJob(
    handle,
    change.target,
    jobId,
    `${change.sourceRef} → ${change.target}`,
  );
  await pollJob(jobId, row);
}

async function pollJob(jobId, row) {
  for (let attempt = 0; attempt < 600; attempt += 1) {
    if (!row.isConnected) return;
    await new Promise((resolve) => setTimeout(resolve, 500));
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

function apply() {
  const batch = applyChanges();
  renderActions();
  for (const change of batch) submitConvert(change);
}

async function start() {
  let tree;
  try {
    const res = await fetch("/api/devices");
    tree = await res.json();
  } catch (err) {
    console.error("failed to load device tree", err);
    return;
  }
  const devices = tree.devices || [];
  for (const device of devices) devicesById.set(device.id, device);
  const pollMs = tree.app?.poll_ms ?? 20000;

  rack = buildRack(THREE, devices);
  scene.add(rack.group);
  handle = buildPanes(document.getElementById("panes"), devices);
  wireDragDrop(handle, renderActions);

  document.getElementById("apply").addEventListener("click", apply);
  document.getElementById("cancel").addEventListener("click", () => {
    cancelAll();
    renderActions();
  });
  document.getElementById("undo").addEventListener("click", () => {
    undoLast();
    renderActions();
  });

  renderActions();
  pollStatus(applyStatus, { intervalMs: pollMs });
  refreshSources();
  setInterval(refreshSources, pollMs);
}

start();

document.getElementById("camera-reset").addEventListener("click", () => {
  camera.position.set(0, 280, 640);
  controls.target.set(0, 20, 0);
  controls.update();
});

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});

// Debug handle, mirrors the op1-web convention.
window.samplehub = { renderer, scene, camera, controls, rack, handle };
