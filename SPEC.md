# Sample Hub — cross-instrument sample workstation

> **Goal:** one localhost web interface that moves a sample between every USB
> instrument on the desk, doing the format conversion for each destination so a
> drop always lands in a file the target can actually use.

**Status:** specification only. No code yet. This file is the contract the
implementation is built against.

---

## 1. Problem

Five devices can each hold or shape a sample, but no two of them speak the same
format or accept data the same way. Today a sample has to be manually exported
from one, converted by hand, and re-imported into the next. This app makes that
a drag-and-drop: pick a source, drop it on a destination, and the conversion and
transfer happen behind one gesture, with the result reported back in the same
pane.

The app is an orchestrator, not a rewrite. It reuses the two working tools
already on this machine — `op1-web` (OP-1 disk mount + AIFC conversion) and
`spottytube-ext` (Spotykach USB transfer) — and adds only what is missing: the
conversion matrix, the routing, and one UI to hold them all.

---

## 2. The instruments, as they actually are

This is the grounding. Every design decision below follows from these facts.

| Instrument | On USB hub? | How it receives a sample | Target format | Length cap |
|---|---|---|---|---|
| **OP-1** (disk mode) | yes | filesystem mount; write files into `drum/user/`, synth, tape folders | AIFC (`.aif`): drum = mono 44.1 kHz; synth/tape = 44.1 kHz | drum ≤ 12 s |
| **Spotykach** | yes | USB CDC transfer mode (SysEx handshake → `/dev/ttyACM*`) | 48 kHz stereo float32 WAV | ≤ 42 s stored |
| **Wave Bard** | yes | generate `.uf2` firmware → flash in boot mode (SHIFT+power) | loader takes WAV/MP3/OGG/AAC/M4A/AIFF; samples tuned to C, silence trimmed | flash time 2–5 min |
| **SP-404MK2** | **no** (manual forbids hub) | SD card `IMPORT` folder, then manual `SHIFT+pad13 → IMPORT from SD-CARD` | 16-bit linear WAV (also AIFF/MP3) | ~16 min / 185 MB |
| **FX Wizard** | yes | — none — | — | — |

Consequences, stated plainly:

- **FX Wizard is not a sample destination.** It is a 9-mode FX processor with no
  sample storage. It is out of scope for the sample-routing panes in v1; if it
  ever appears it is as a *process* node ("route this sample through FX before
  it lands"), not a *store* node. Do not fake a sample path for it.
- **Wave Bard is staged, not live.** A drop cannot land instantly; it queues a
  bank, and the user runs the boot-mode flash when ready. The pane must say
  "ready to flash" rather than pretend it is instant.
- **SP-404 is a two-hop path.** The app can only write the SD card's `IMPORT`
  folder (via a card reader mounted on the host); the unit-side import is
  manual. The app's job is to put a correctly-formed 16-bit WAV in the right
  folder and tell the user exactly what to press next.
- **SP-404 must be on a direct port, not the hub.** The manual says data cannot
  be transferred through a hub. The UI should flag a hub-connected SP-404 as
  "replug direct" if it can be detected.

---

## 3. Workflow

The end-to-end flow a user follows, and the state machine the UI mirrors.

1. **Source** — pick a sample from the Library pane (host cache) or pull one
   back off an instrument (OP-1 disk entry, Spotykach slot, SP-404 SD card).
2. **Edit (optional)** — trim, normalize, retime, tune-to-C. Runs once against
   the canonical copy; destinations never re-edit.
3. **Drop** — drag onto a destination pane. One drop = one job.
4. **Convert** — the hub renders the canonical copy into the target format
   using the matrix in §6. Conversion happens in the host cache first, before
   any device is touched.
5. **Transfer** — the target-specific transport fires (filesystem copy for
   OP-1/SP-404-SD, CDC transfer for Spotykach, `.uf2` staging for Wave Bard).
6. **Report** — the pane shows the on-unit path (`G/2.WAV`, `drum/user/1.aif`,
   `IMPORT/foo.WAV`) or a "ready to flash" state, not a generic "done".

The invariant that makes drag-and-drop safe: **convert first, transfer second,
report last.** A device is only touched after the bytes for it exist in the
cache, so a failed conversion never leaves a half-written device.

---

## 4. Architecture

Single deploy, loopback only. Matches the two existing projects rather than
introducing a new stack.

- **Backend:** Python 3, stdlib only (`ThreadingHTTPServer`, same as
  `op1-web`). Binds `127.0.0.1`, serves the client with `Cache-Control:
  no-store`.
- **Frontend:** vanilla JS + HTML5 Drag and Drop API. No build step, no
  framework. The existing projects are already stdlib-Python + vanilla-JS and
  this stays consistent.
- **Reuse, not rewrite:**
  - OP-1 conversion: call the `op1-web` proxy's existing routes, or import its
    AIFC converter.
  - Spotykach transfer: call the `spottytube` bridge (`POST /sample`, `GET
    /jobs/<id>`, `/status`) or import `tools/sk_usb.py` + `tools/sk_install.py`.
  - Wave Bard and SP-404-SD: new, small, per-target writers (see §6).
- **Canonical working copy:** every sample is normalized once to 48 kHz stereo
  float32 WAV in the hub cache (matches the existing `wav_builder` output), and
  every destination transform emits from that copy. No destination gets the
  raw source.
- **Jobs:** one job queue, serialized per instrument. Two drops on the same
  Spotykach never run concurrently — two transfers fight over one tape chain.
  A job lives as long as the process.

### Directory layout (proposed)

```
sample-hub/
  SPEC.md                 this file
  server/
    hub_server.py         HTTP server, stdlib only, loopback bind
    library.py            host cache + canonical-format normalization
    convert.py            the conversion matrix (§6), one fn per target
    devices/
      op1.py              reuse op1-web AIFC path
      spotykach.py        reuse spottytube transport
      wave_bard.py        .uf2 staging writer
      sp404.py            SD IMPORT folder writer
    status.py             per-instrument presence probe
  client/
    index.html
    app.js                panes, drag/drop, job polling
    styles.css
  tests/
    ...
```

### API surface (proposed)

```
GET  /api/health            {"ok":true,"version":"0.1.0"}
GET  /api/status            per-instrument presence + transport state
GET  /api/library           cached canonical samples
POST /api/convert           {source_id, target, params} -> {job_id}
GET  /api/jobs/<id>         {status:"queued|running|done|error", result, error}
GET  /api/audio             convert canonical -> playable WAV (range requests)
POST /api/restart           reload code on disk (execve, same as op1-web)
```

`/api/convert` is the one gesture. It resolves the source, normalizes to
canonical, converts for the target, transfers, and returns the on-device
result.

---

## 5. Multi-pane GUI

The drag-and-drop is the product. Layout is a source pane plus one pane per
instrument, all visible at once so a drop never hides its destination.

### Panes

```
+------------------+  +-----------+  +------------+  +---------+  +------------+  +----------+
|   LIBRARY        |  |  OP-1     |  |  SPOTYKACH |  |  WAVE   |  |  SP-404    |  |  FX      |
|  (host cache)    |  |  (disk)   |  |  (usb cdc) |  |  BARD   |  |  (sd card) |  |  WIZARD  |
|                  |  |           |  |            |  | (.uf2)  |  |            |  | (no drop)|
|  * sample_a.wav  |  |  drum/user|  |  B/1..6    |  |  bank 1  |  |  IMPORT/   |  |          |
|  * sample_b.wav  |  |  synth    |  |  G/1..6    |  |  bank 2  |  |  ...       |  |  FX      |
|  * sample_c.wav  |  |  tape     |  |  ...        |  |  (staged)|  |  (staged)  |  |  modes   |
+------------------+  +-----------+  +------------+  +---------+  +------------+  +----------+
```

### Presence dot (top-right of each pane)

- **green** — device present and its transport usable.
- **red** — absent, or transport unusable (no mount, no `/dev/ttyACM*`, no card).
- **amber** — present but blocked on a user step (Wave Bard "ready to flash",
  SP-404 "replug direct — on hub").

The dot re-checks every 20 s, same cadence as the `spottytube` widget. A drop on
a red pane is refused with the reason, never queued blindly.

### Drag-and-drop semantics

- **Draggable sources:** Library entries; OP-1 disk entries; Spotykach slots;
  SP-404 SD entries. (Wave Bard and FX Wizard are not sources in v1.)
- **Valid targets:** OP-1, Spotykach, Wave Bard, SP-404. FX Wizard is not a
  target (shows "no sample input" on hover).
- **Drop = one conversion job.** The pane shows a job row with a spinner and
  the target format being produced (e.g. "→ 48k float32 · slot G/2").
- **Invalid drops** (over length cap, FX Wizard, red pane) are rejected inline
  with the specific reason — "42 s stored cap", "no sample input", "replug
  direct" — not a generic error.
- **No deletes, ever.** Bump/append on Spotykach archives the overflow; OP-1
  and SP-404 writes are additive. The UI offers no delete affordance in v1.
  (This mirrors the existing tools' read-back-and-archive discipline.)

### Conversion matrix shown to the user

On hover over a target pane, a small readout states what the drop will produce,
so the conversion is never a surprise:

| Target | Result of a drop |
|---|---|
| OP-1 drum | mono 44.1 kHz AIFC, ≤ 12 s, `drum/user/<name>.aif` |
| OP-1 synth/tape | 44.1 kHz AIFC, target folder chosen by drop zone |
| Spotykach | 48 kHz stereo float32 WAV, next slot (bump/append chosen by drop zone) |
| Wave Bard | tuned-to-C mono sample staged into a bank; "generate firmware" button |
| SP-404 | 16-bit linear 44.1 kHz WAV into SD `IMPORT/`; manual import steps shown |

---

## 6. Conversion matrix (the engine)

One canonical form in, one specific form out. Each row is a pure function of
(source bytes, target, params) with no device I/O, so it is unit-testable
without hardware.

| Target | Input | Output | Transform |
|---|---|---|---|
| OP-1 drum kit | 48k f32 stereo | AIFC, mono, 44.1 kHz, ≤ 12 s | downmix → downsample → write AIFC container (reuse op1-web) |
| OP-1 synth/tape | 48k f32 stereo | AIFC, 44.1 kHz | downsample → write AIFC |
| Spotykach | 48k f32 stereo | 48k f32 stereo WAV ≤ 42 s stored | retime if needed (speed pass), else pass-through |
| Wave Bard | 48k f32 stereo | mono WAV, tuned C, silence trimmed | downmix → pitch-to-C → trim → feed loader inputs |
| SP-404 | 48k f32 stereo | 16-bit linear WAV | downsample to 44.1 k → dither to 16-bit |

Each transform is a standalone function with a known input/output pair, so the
whole matrix is testable against generated WAVs before any device is involved.

---

## 7. Safety and idempotency

Borrowed directly from the two tools that already solved these problems:

- **Convert before transfer.** Prepare the target bytes in the cache first;
  only then open the device. A Spotykach in transfer mode times out on silence,
  so the link opens immediately before the write, never across a download or
  ffmpeg pass.
- **Read back and compare.** Spotykach bump moves run far-end-backwards with a
  read-back per move; a dropped transfer after bytes hit the tape is reported,
  never re-run (the slot shift is not idempotent).
- **Archive, never delete.** Overflow goes to card ROOT or host cache; nothing
  is silently discarded.
- **Loopback only.** `127.0.0.1`, JSON POSTs, no external bind.

---

## 8. Milestones (TDD, smallest first)

Each milestone is testable without hardware. Real code, no mocks, following the
existing projects' test style.

1. **Canonical normalizer** — a WAV in any supported form → 48k f32 stereo WAV.
   Test: decode the bytes ffmpeg produced, not a mock.
2. **Conversion matrix, device-free** — every §6 row as a pure function.
   Test: feed generated WAVs, assert rate/channels/depth/container per target.
3. **Spotykach writer** — wrap the existing `sk_install`/`sk_usb` path; the
   matrix output → slot. Test against the existing tape-chain test suite.
4. **OP-1 writer** — reuse `op1-web`'s AIFC path; matrix output → `drum/user/`.
5. **SP-404 writer** — 16-bit WAV → SD `IMPORT/` folder. Test the bytes, not a
   real card.
6. **Wave Bard stager** — bank JSON + samples staged, "generate firmware"
   state; hand-off to the Bastl loader for the actual `.uf2` in v1.
7. **Status probe** — presence/transport detection per instrument (mount,
   `/dev/ttyACM*`, card, hub check for SP-404).
8. **Server + client** — the panes, drag/drop, job queue, presence dots.

**First concrete step:** milestone 1, a failing test for the canonical
normalizer (`tests/test_normalize.py`), then the smallest implementation that
passes.

---

## 9. Open questions / evidence needed

- **OP-1 synth-patch format** is more complex than a plain AIFC file; confirm
  the exact patch container before promising synth/tape drops in v1. Drum kits
  are already proven in `op1-web`.
- **SP-404 exact `IMPORT` folder path** (SD card sub-structure) should be
  confirmed from the unit before wiring the writer. Current spec assumes the
  top-level `IMPORT/` folder the manual names.
- **Wave Bard `.uf2` generation offline** — v1 hands off to the Bastl web
  loader. Generating `.uf2` locally is a later milestone gated on the
  `bastl-instruments/kastle2` toolchain being scriptable.
- **SP-404 hub detection** — whether a hub-connected unit can be reliably
  detected (vs. simply absent) needs a probe; the UI treats it as "replug
  direct" until proven otherwise.

---

## 10. References

- `op1-web` — `/home/rjodouin/Documents/git/op1-web/` (OP-1 disk mount + AIFC conversion, port 8770)
- `spottytube-ext` — `/home/rjodouin/Documents/git/spottytube-ext/` (Spotykach USB transfer bridge, port 8765)
- Spotykach USB transfer protocol — skill `spotykach/references/usb-transfer.md`
- Wave Bard sample loader — https://apps.bastl-instruments.com/wave-bard-sample-loader/
- Wave Bard web app reference — skill `wave-bard/references/web-app.md`
- SP-404MK2 reference manual — https://static.roland.com/manuals/sp-404mk2_reference/eng/17805250.html
- SP-404 USB/MIDI (Python) — https://github.com/blessdog/SP-404
- SP-404 TUI (Go) — https://github.com/moahbeatlab/404tui
- Bastl Kastle 2 platform — https://github.com/bastl-instruments/kastle2
