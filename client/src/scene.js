// The 3D rack: five labelled plates standing on a shelf, each with a presence
// dot on its face. Pure decoration — the drop targets are the DOM overlay
// panes (panes.js), not these meshes (SPEC.md §5.1).
import { INSTRUMENTS, RACK, plateX, plateY } from "./layout.js";

function labelTexture(THREE, text) {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 128;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#10141b";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.font = "600 44px system-ui, sans-serif";
  ctx.fillStyle = "#dfe6f0";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, canvas.width / 2, canvas.height / 2);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

export function buildRack(THREE) {
  const group = new THREE.Group();
  const dots = new Map();

  const shelfWidth =
    INSTRUMENTS.length * RACK.plateWidth + (INSTRUMENTS.length + 1) * RACK.gap;
  const shelf = new THREE.Mesh(
    new THREE.BoxGeometry(shelfWidth, RACK.shelfThickness, RACK.shelfDepth),
    new THREE.MeshStandardMaterial({ color: 0x20262f, roughness: 0.6, metalness: 0.15 }),
  );
  shelf.position.y = RACK.shelfY;
  shelf.castShadow = true;
  shelf.receiveShadow = true;
  group.add(shelf);

  INSTRUMENTS.forEach((instrument, index) => {
    const x = plateX(index);
    const y = plateY();

    const plate = new THREE.Mesh(
      new THREE.BoxGeometry(RACK.plateWidth, RACK.plateHeight, RACK.plateDepth),
      new THREE.MeshStandardMaterial({ color: 0x171b22, roughness: 0.4, metalness: 0.4 }),
    );
    plate.position.set(x, y, 0);
    plate.castShadow = true;
    plate.receiveShadow = true;
    group.add(plate);

    const label = new THREE.Mesh(
      new THREE.PlaneGeometry(RACK.plateWidth * 0.72, RACK.labelTextHeight),
      new THREE.MeshBasicMaterial({ map: labelTexture(THREE, instrument.label), toneMapped: false }),
    );
    label.position.set(x, y + RACK.plateHeight * 0.18, RACK.plateDepth / 2 + 0.5);
    group.add(label);

    const dot = new THREE.Mesh(
      new THREE.CircleGeometry(RACK.dotRadius, 32),
      new THREE.MeshBasicMaterial({ color: 0x7a1f1f }),
    );
    dot.position.set(
      x - RACK.plateWidth / 2 + RACK.dotRadius + 6,
      y + RACK.plateHeight / 2 - RACK.dotRadius - 6,
      RACK.plateDepth / 2 + 0.6,
    );
    group.add(dot);
    dots.set(instrument.id, dot);
  });

  return { group, dots };
}
