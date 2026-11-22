// The 3D rack: plates standing on a shelf, each mounting the instrument's
// photo (assets/photos/<id>.jpg) with a bold teal nameplate and a presence dot
// floating over the face. Pure decoration — the drop targets are the DOM
// overlay panes (panes.js), not these meshes (SPEC.md §5.1). The plate list and
// each photo come from the device tree, so adding a device to
// server/devices.json extends the rack and picks up its photo with no
// client-side change.
import { RACK, plateX, plateY } from "./layout.js";

// Z-offsets in front of the plate face, back to front: the photo is mounted on
// the plate, the nameplate floats in front of it, and the presence dot floats
// furthest out so the indicator is never hidden.
const PHOTO_Z_OFFSET = 0.3;
const LABEL_Z_OFFSET = 0.4;
const DOT_Z_OFFSET = 0.5;
const LABEL_COLOR = "#2dd4bf";

function roundedRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function labelTexture(THREE, text) {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 128;
  const ctx = canvas.getContext("2d");
  // A translucent dark pill keeps the bold teal name readable over any photo.
  roundedRect(ctx, 8, 8, canvas.width - 16, canvas.height - 16, 18);
  ctx.fillStyle = "rgba(16, 20, 27, 0.8)";
  ctx.fill();
  ctx.font = "700 44px system-ui, sans-serif";
  ctx.fillStyle = LABEL_COLOR;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, canvas.width / 2, canvas.height / 2);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

function photoPlane(THREE, device, x, y) {
  const plane = new THREE.Mesh(
    new THREE.PlaneGeometry(RACK.plateWidth, RACK.plateHeight),
    new THREE.MeshBasicMaterial({ toneMapped: false }),
  );
  plane.position.set(x, y, RACK.plateDepth / 2 + PHOTO_Z_OFFSET);
  plane.visible = false;

  new THREE.TextureLoader().load(
    `assets/photos/${device.id}.jpg`,
    (texture) => {
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.anisotropy = 4;
      // "size photo to width": the plate width is authoritative, the height
      // follows the source image's aspect ratio.
      const aspect = texture.image.height / texture.image.width;
      plane.geometry.dispose();
      plane.geometry = new THREE.PlaneGeometry(RACK.plateWidth, RACK.plateWidth * aspect);
      plane.material.map = texture;
      plane.material.needsUpdate = true;
      plane.visible = true;
    },
    undefined,
    () => {
      // No photo for this id — the nameplate alone identifies the instrument.
      plane.visible = false;
    },
  );

  return plane;
}

export function buildRack(THREE, devices) {
  const group = new THREE.Group();
  const dots = new Map();
  const count = devices.length;

  const shelfWidth =
    count * RACK.plateWidth + (count + 1) * RACK.gap;
  const shelf = new THREE.Mesh(
    new THREE.BoxGeometry(shelfWidth, RACK.shelfThickness, RACK.shelfDepth),
    new THREE.MeshStandardMaterial({ color: 0x20262f, roughness: 0.6, metalness: 0.15 }),
  );
  shelf.position.y = RACK.shelfY;
  shelf.castShadow = true;
  shelf.receiveShadow = true;
  group.add(shelf);

  devices.forEach((device, index) => {
    const x = plateX(index, count);
    const y = plateY();

    const plate = new THREE.Mesh(
      new THREE.BoxGeometry(RACK.plateWidth, RACK.plateHeight, RACK.plateDepth),
      new THREE.MeshStandardMaterial({ color: 0x171b22, roughness: 0.4, metalness: 0.4 }),
    );
    plate.position.set(x, y, 0);
    plate.castShadow = true;
    plate.receiveShadow = true;
    group.add(plate);

    group.add(photoPlane(THREE, device, x, y));

    const label = new THREE.Mesh(
      new THREE.PlaneGeometry(RACK.plateWidth * 0.72, RACK.labelTextHeight),
      new THREE.MeshBasicMaterial({
        map: labelTexture(THREE, device.label),
        toneMapped: false,
        transparent: true,
      }),
    );
    label.position.set(x, y + RACK.plateHeight * 0.18, RACK.plateDepth / 2 + LABEL_Z_OFFSET);
    group.add(label);

    const dot = new THREE.Mesh(
      new THREE.CircleGeometry(RACK.dotRadius, 32),
      new THREE.MeshBasicMaterial({ color: 0x7a1f1f }),
    );
    dot.position.set(
      x - RACK.plateWidth / 2 + RACK.dotRadius + 6,
      y + RACK.plateHeight / 2 - RACK.dotRadius - 6,
      RACK.plateDepth / 2 + DOT_Z_OFFSET,
    );
    group.add(dot);
    dots.set(device.id, dot);
  });

  return { group, dots };
}
