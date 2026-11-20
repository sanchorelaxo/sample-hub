// Client shell entry: renderer, camera, OrbitControls, the rack, and the
// wiring for presence-dot polling + library listing + drag/drop (SPEC.md §5.1).
import * as THREE from "../vendor/three.module.js";
import { OrbitControls } from "../vendor/OrbitControls.js";
import { RoomEnvironment } from "../vendor/RoomEnvironment.js";
import { buildRack } from "./scene.js";
import { buildPanes, renderLibrary, setPresence } from "./panes.js";
import { wireDragDrop } from "./dragdrop.js";
import { pollStatus, colorFor } from "./status.js";

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

const rack = buildRack(THREE);
scene.add(rack.group);

const handle = buildPanes(document.getElementById("panes"));
wireDragDrop(handle);

function applyStatus(payload) {
  for (const device of payload.devices || []) {
    setPresence(handle, device);
    const dot = rack.dots.get(device.id);
    if (dot) dot.material.color.setHex(colorFor(device.state).hex);
  }
}

async function refreshLibrary() {
  try {
    const res = await fetch("/api/library");
    if (res.ok) renderLibrary(handle, await res.json());
  } catch (err) {
    console.warn("library refresh failed", err);
  }
}

pollStatus(applyStatus);
refreshLibrary();

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
window.samplehub = { renderer, scene, camera, controls, rack };
