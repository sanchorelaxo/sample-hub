// Rack geometry. Pure geometry: plate positions and pitch are derived from the
// device count, which the client reads from GET /api/devices (server/devices.json).
// No device list is hardcoded here (TODO_1.txt item 6).
export const RACK = {
  plateWidth: 150, // mm, each instrument plate
  plateHeight: 92, // mm
  plateDepth: 6, // mm
  gap: 22, // mm between plates
  shelfThickness: 10, // mm
  shelfDepth: 44, // mm, how far the shelf reaches toward the viewer
  shelfY: -6, // mm, shelf top just under the plate centres
  dotRadius: 5, // mm, presence dot on the plate face
  labelTextHeight: 26, // mm, label plane height
};

const PITCH = RACK.plateWidth + RACK.gap;

export function plateX(index, count) {
  const total = count * RACK.plateWidth + (count - 1) * RACK.gap;
  return index * PITCH - total / 2 + RACK.plateWidth / 2;
}

export function plateY() {
  return RACK.shelfY + RACK.shelfThickness + RACK.plateHeight / 2;
}
