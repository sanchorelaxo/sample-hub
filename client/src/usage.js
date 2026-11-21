// Bastl loader memory-usage mirror (TODO_1.txt lines 50-51).
//
// The server enforces the 7.5 MiB budget authoritatively in both bakers
// (server/pattern.py BASTL_MEMORY_SIZE). This module mirrors the Bastl loaders'
// calculateUsage.js so the editor can show a live usage readout and refuse an
// apply that would fail server-side. The client does not decode audio, so each
// sample contributes its source file byte size (list_source's "bytes") as a
// conservative upper bound on the eventual Int16 PCM payload; the server's
// exact guard stays authoritative.

export const BASTL_MEMORY_SIZE = 7.5 * 1024 * 1024; // 7,864,320 (memorySize)

const MI_B = 1024 * 1024;

export function fxWizardUsage({ rhythms = [] } = {}) {
  // encode_k2fx: 20-byte header + 4 bytes per rhythm mask + 4-byte ahoj.
  return 20 + 4 * rhythms.length + 4;
}

export function waveBardUsage({ scales = [], rhythms = [], banks = [] } = {}) {
  // calculateUsage.js: 20 + 4*scales + 4*rhythms + sum(12 + sum(16 + data)) + 4.
  let used = 20 + 4 * scales.length + 4 * rhythms.length;
  for (const bank of banks) {
    used += 12; // bank header
    for (const sample of bank.samples || []) {
      used += 16 + (Number(sample.bytes) || 0); // sample header + data
    }
  }
  return used + 4; // end marker
}

export function formatMiB(bytes) {
  return `${(bytes / MI_B).toFixed(1)} MiB`;
}
