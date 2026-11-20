// Presence-dot polling. GET /api/status on a fixed cadence and hand each
// device verdict to the caller, which paints both the DOM dot and the 3D dot.
// The state -> colour mapping lives here in one place so the two dots can
// never disagree. The cadence comes from the device tree's app.poll_ms
// (TODO_1.txt item 6).
export const PRESENCE = {
  green: { css: "#2ecc71", hex: 0x2ecc71 },
  amber: { css: "#f1c40f", hex: 0xf1c40f },
  red: { css: "#e74c3c", hex: 0xe74c3c },
};

export function colorFor(state) {
  return PRESENCE[state] || PRESENCE.red;
}

export function pollStatus(onUpdate, { signal, intervalMs } = {}) {
  const interval = intervalMs ?? 20000;

  async function tick() {
    try {
      const res = await fetch("/api/status", { signal });
      if (res.ok) onUpdate(await res.json());
    } catch (err) {
      if (err && err.name !== "AbortError") {
        console.warn("status poll failed", err);
      }
    }
  }

  tick();
  const timer = setInterval(tick, interval);
  if (signal) {
    signal.addEventListener("abort", () => clearInterval(timer), { once: true });
  }
  return timer;
}
