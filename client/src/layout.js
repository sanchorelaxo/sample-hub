// Rack geometry and pane order. One number per instrument position, in
// millimetres, so mesh code never hardcodes a position. The order is the
// SPEC.md §5.1 pane order, left to right, and must stay in sync with
// server/status.py DEVICE_ORDER (pinned by tests/test_client_shell.py).
export const INSTRUMENTS = [
  { id: "op1", label: "OP-1" },
  { id: "spotykach", label: "Spotykach" },
  { id: "wave_bard", label: "Wave Bard" },
  { id: "sp404", label: "SP-404MK2" },
  { id: "fx_wizard", label: "FX Wizard" },
];

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
const TOTAL =
  INSTRUMENTS.length * RACK.plateWidth + (INSTRUMENTS.length - 1) * RACK.gap;

export function plateX(index) {
  return index * PITCH - TOTAL / 2 + RACK.plateWidth / 2;
}

export function plateY() {
  return RACK.shelfY + RACK.shelfThickness + RACK.plateHeight / 2;
}
