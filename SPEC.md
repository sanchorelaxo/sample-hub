# Sample Hub — cross-instrument sample workstation

> **Goal:** one localhost web interface that moves a sample between every USB
> instrument on the desk, doing the format conversion for each destination so a
> drop always lands in a file the target can actually use.

**Status:** specification only. No code yet. This file is the contract the
implementation is built against.

---

## 1. Problem

Five devices can each hold or shape audio, but no two of them speak the same
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

| Instrument | On USB hub? | How it receives data | Payload | Flash time |
|---|---|---|---|---|
| **OP-1** (disk mode) | yes | filesystem mount; write files into `drum/user/`, synth, tape folders | AIFC (`.aif`): drum = mono 44.1 kHz; synth/tape = 44.1 kHz | — |
| **Spotykach** | yes | USB CDC transfer mode (SysEx handshake → `/dev/ttyACM*`) | 48 kHz stereo float32 WAV | — |
| **Wave Bard** | yes | generate `.uf2` firmware → flash in boot mode (SHIFT+power) | samples + scales + rhythms (loader takes WAV/MP3/OGG/AAC/M4A/AIFF; samples tuned to C, silence trimmed) | 2–5 min |
| **SP-404MK2** | **no** (manual forbids hub) | SD card `IMPORT` folder, then manual `SHIFT+pad13 → IMPORT from SD-CARD` | 16-bit linear WAV (also AIFF/MP3) | — |
| **FX Wizard** | yes | generate `.uf2` firmware → flash in boot mode (SHIFT+power) | rhythm patterns (GATE/CV) + sequencer step count, firmware 1.6+ | 2–5 min |

Two asset types flow through the hub, and the UI keeps them visibly separate:

- **Audio samples** → OP-1, Spotykach, Wave Bard, SP-404.
- **Rhythm patterns** (gate/CV sequences + step count) → FX Wizard, Wave Bard.

Consequences, stated plainly:

- **Two delivery models, not five.** OP-1 and Spotykach land a file instantly
  (filesystem copy / CDC transfer). Wave Bard and FX Wizard bake a `.uf2`
  firmware that must be flashed in boot mode — their panes say "ready to flash",
  never "done". SP-404 is a two-hop path (app writes the SD `IMPORT` folder,
  the unit-side import is manual).
- **FX Wizard is a rhythm destination, not a sample destination.** It is a
  9-mode FX processor; it never stores audio samples. But via the FX Wizard
  Chamber web app (firmware 1.6+) it accepts custom rhythm patterns and a
  sequencer step count, baked into a `.uf2` firmware. A sample dropped on FX
  Wizard is refused with the reason "rhythms only — no sample input". Its pane
  is a rhythm editor, not a sample target.
- **Wave Bard is dual-purpose.** Its loader carries samples *and* rhythms *and*
  scales in one firmware. It is the only destination that takes both asset
  types.
- **SP-404 must be on a direct port, not the hub.** The manual says data cannot
  be transferred through a hub. The UI should flag a hub-connected SP-404 as
  "replug direct" if it can be detected.

---

## 3. Workflow

The end-to-end flow a user follows, and the state machine the UI mirrors.

1. **Source** — pick an audio sample from the Library pane (host cache), pull
   one back off an instrument (OP-1 disk entry, Spotykach slot, SP-404 SD
   card), or author a rhythm pattern in a rhythm editor pane.
2. **Edit (optional)** — trim, normalize, retime, tune-to-C for samples; draw
   steps and pick a step count for rhythms. Runs once against the canonical
   copy; destinations never re-edit.
3. **Drop** — drag onto a destination pane. One drop = one job. A sample on a
   rhythm-only target (or vice-versa) is refused inline with the reason.
4. **Convert** — the hub renders the canonical copy into the target format
   using the matrix in §6. Conversion happens in the host cache first, before
   any device is touched.
5. **Transfer** — the target-specific transport fires (filesystem copy for
   OP-1/SP-404-SD, CDC transfer for Spotykach, `.uf2` staging for Wave Bard /
   FX Wizard).
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
- **Frontend:** Three.js scene + DOM overlay panes. No build step, no npm
  packages — plain ES modules with `three` vendored under `client/vendor/`,
  exactly the house rule already proven in `op1-web` and `queneo-editor`.
  The 3D scene is the visual frame; the panes that carry lists, drag/drop and
  the conversion readouts are DOM overlays on top of it (the `queneo-editor`
  split: a `three` canvas for the device, DOM `pane.js`/`toolbar.js` for the
  data). The scene is a flat instrument rack in v1; per-instrument 3D models
  come in the next phase (§5.1).
- **Reuse, not rewrite:**
  - OP-1 conversion: call the `op1-web` proxy's existing routes, or import its
    AIFC converter.
  - Spotykach transfer: call the `spottytube` bridge (`POST /sample`, `GET
    /jobs/<id>`, `/status`) or import `tools/sk_usb.py` + `tools/sk_install.py`.
  - Wave Bard and FX Wizard: new, small, per-target writers that emit `.uf2`
    (see §6). SP-404-SD: new writer that emits 16-bit WAV into `IMPORT/`.
- **Canonical working copy:** every audio sample is normalized once to 48 kHz
  stereo float32 WAV in the hub cache (matches the existing `wav_builder`
  output), and every destination transform emits from that copy. No destination
  gets the raw source. Rhythm patterns are stored canonically as a step/gate
  JSON, one form for both rhythm destinations.
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
      wave_bard.py        .uf2 staging writer (samples + rhythms + scales)
      fx_wizard.py        .uf2 staging writer (rhythms + step count)
      sp404.py            SD IMPORT folder writer
    status.py             per-instrument presence probe
  client/
    index.html
    vendor/               vendored three (module + OrbitControls + RoomEnvironment)
    src/
      main.js             renderer, camera, OrbitControls, RAF loop
      scene.js            the instrument rack (flat plates in v1)
      layout.js           rack geometry, one number per instrument position (mm)
      panes.js            DOM overlay panes (library, per-instrument, readouts)
      dragdrop.js         HTML5 DnD -> /api/convert
      status.js           presence dot polling (20 s)
    styles.css
  tests/
    ...
```

### API surface (proposed)

```
GET  /api/health            {"ok":true,"version":"0.1.0"}
GET  /api/status            per-instrument presence + transport state
GET  /api/library           cached canonical samples + rhythm patterns
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

### 5.1 Three.js scene (v1 rack, next-phase 3D models)

The client is a `three` canvas with DOM overlay panes. What each phase puts in
the canvas is the only part that changes.

- **v1 — the rack.** A flat, shaded instrument rack built from `layout.js`
  geometry (one number per instrument position, in millimetres, so positions
  are never hardcoded in mesh code — the same discipline as `op1-web`'s
  `layout.js` and `queneo-editor`'s `LAYOUT.json`). Each instrument is a
  labelled plate with a presence-dot surface and a drop zone. The panes that
  carry lists and readouts are DOM overlays positioned over the plate, exactly
  like `queneo-editor`'s `pane.js`. `OrbitControls` lets the user tilt around
  the rack; a camera reset button returns to the straight-on view.
- **next phase — per-instrument 3D models.** Replace each plate with a real
  model in the style already proven in the two reference repos: the OP-1 as a
  measured `layout.js` + `model.js` + `screen.js` (from `op1-web`, whose
  `docs/HANDOFF_THREEJS.md` documents the measured panel truth and the
  `window.op1studio` debug handle), and the QuNeo-style boards from
  `queneo-editor`'s `board.js` + `geometry.js` + `materials.js`. The drop
  targets and presence dots move onto the model; the DOM panes stay, because
  list drag/drop and the conversion readout do not belong inside a mesh.

The invariant either way: **all device I/O lives behind `/api/convert`; the
scene never talks to a device directly.** The 3D layer is presentation only,
so the next phase can replace the rack with models without touching a line of
the backend or the conversion matrix.

### Panes

```
+------------------+  +-----------+  +------------+  +---------+  +------------+  +------------+
|   LIBRARY        |  |  OP-1     |  |  SPOTYKACH |  |  WAVE   |  |  SP-404    |  |  FX WIZARD |
|  (host cache)    |  |  (disk)   |  |  (usb cdc) |  |  BARD   |  |  (sd card) |  |  (rhythm)  |
|                  |  |           |  |            |  | (.uf2)  |  |            |  |            |
|  * sample_a.wav  |  |  drum/user|  |  B/1..6    |  |  bank 1  |  |  IMPORT/   |  |  step grid |
|  * sample_b.wav  |  |  synth    |  |  G/1..6    |  |  bank 2  |  |  ...       |  |  steps 5-64|
|  * rhythm_1.json |  |  tape     |  |  ...        |  |  (staged)|  |  (staged)  |  |  (staged)  |
+------------------+  +-----------+  +------------+  +---------+  +------------+  +------------+
```

### Presence dot (top-right of each pane)

- **green** — device present and its transport usable.
- **red** — absent, or transport unusable (no mount, no `/dev/ttyACM*`, no card).
- **amber** — present but blocked on a user step (Wave Bard / FX Wizard "ready
  to flash", SP-404 "replug direct — on hub").

The dot re-checks every 20 s, same cadence as the `spottytube` widget. A drop on
a red pane is refused with the reason, never queued blindly.

### Drag-and-drop semantics

- **Draggable sources:** Library entries (samples and rhythms); OP-1 disk
  entries; Spotykach slots; SP-404 SD entries.
- **Valid targets, by asset type:**
  - *samples* → OP-1, Spotykach, Wave Bard, SP-404.
  - *rhythms* → Wave Bard, FX Wizard.
  - FX Wizard refuses a sample ("rhythms only — no sample input"); SP-404,
    OP-1 and Spotykach refuse a rhythm ("samples only").
- **Drop = one conversion job.** The pane shows a job row with a spinner and
  the target format being produced (e.g. "→ 48k float32 · slot G/2", "→ rhythm
  .uf2 · 16 steps").
- **Invalid drops** (wrong asset type, over length cap, red pane) are rejected
  inline with the specific reason — "42 s stored cap", "rhythms only",
  "replug direct" — not a generic error.
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
| Wave Bard (sample) | tuned-to-C mono sample staged into a bank |
| Wave Bard (rhythm) | rhythm pattern + step count staged into the firmware draft |
| FX Wizard (rhythm) | rhythm pattern + step count → `.uf2`, "generate firmware" button |
| SP-404 | 16-bit linear 44.1 kHz WAV into SD `IMPORT/`; manual import steps shown |

---

## 6. Conversion matrix (the engine)

One canonical form in, one specific form out. Each row is a pure function of
(source bytes, target, params) with no device I/O, so it is unit-testable
without hardware.

### Audio samples (canonical: 48 kHz stereo float32 WAV)

| Target | Input | Output | Transform |
|---|---|---|---|
| OP-1 drum kit | 48k f32 stereo | AIFC, mono, 44.1 kHz, ≤ 12 s | downmix → downsample → write AIFC container (reuse op1-web) |
| OP-1 synth/tape | 48k f32 stereo | AIFC, 44.1 kHz | downsample → write AIFC |
| Spotykach | 48k f32 stereo | 48k f32 stereo WAV ≤ 42 s stored | retime if needed (speed pass), else pass-through |
| Wave Bard | 48k f32 stereo | mono WAV, tuned C, silence trimmed | downmix → pitch-to-C → trim → feed loader inputs |
| SP-404 | 48k f32 stereo | 16-bit linear WAV | downsample to 44.1 k → dither to 16-bit |

### Rhythm patterns (canonical: step/gate JSON)

| Target | Input | Output | Transform |
|---|---|---|---|
| Wave Bard | step/gate JSON | rhythm bank + step count in firmware draft | map to loader rhythm slots |
| FX Wizard | step/gate JSON | rhythm pattern + step count → `.uf2` | map to Chamber rhythm/step format |

Each transform is a standalone function with a known input/output pair, so the
whole matrix is testable against generated WAVs (or generated rhythm JSON)
before any device is involved.

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
   Test: feed generated WAVs (and rhythm JSON), assert rate/channels/depth/
   container per target.
3. **Spotykach writer** — wrap the existing `sk_install`/`sk_usb` path; the
   matrix output → slot. Test against the existing tape-chain test suite.
4. **OP-1 writer** — reuse `op1-web`'s AIFC path; matrix output → `drum/user/`.
5. **SP-404 writer** — 16-bit WAV → SD `IMPORT/` folder. Test the bytes, not a
   real card.
6. **Wave Bard stager** — bank JSON + samples + rhythms staged, "generate
   firmware" state; hand-off to the Bastl loader for the actual `.uf2` in v1.
7. **FX Wizard stager** — rhythm pattern + step count → firmware draft, same
   "generate firmware" state as Wave Bard.
8. **Status probe** — presence/transport detection per instrument (mount,
   `/dev/ttyACM*`, card, hub check for SP-404).
9. **Server + three.js rack client** — the `three` rack scene, DOM overlay
   panes, drag/drop, job queue, presence dots. No per-instrument 3D models yet
   (that is the next phase, §5.1).

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
- **Wave Bard and FX Wizard `.uf2` generation offline** — v1 hands off to the
  Bastl web loaders (sample loader and Chamber). Generating `.uf2` locally is a
  later milestone gated on the `bastl-instruments/kastle2` toolchain being
  scriptable. Both loaders save/load a "draft file", which the hub should read
  and write so a user can round-trip their banks and rhythms.
- **SP-404 hub detection** — whether a hub-connected unit can be reliably
  detected (vs. simply absent) needs a probe; the UI treats it as "replug
  direct" until proven otherwise.

---

## 10. References

- `op1-web` — `/home/rjodouin/Documents/git/op1-web/` (OP-1 disk mount + AIFC conversion, port 8770; three.js OP-1 model, see `docs/HANDOFF_THREEJS.md`)
- `spottytube-ext` — `/home/rjodouin/Documents/git/spottytube-ext/` (Spotykach USB transfer bridge, port 8765)
- `queneo-editor` — `/home/rjodouin/Documents/git/queneo-editor/` (three.js board client, see `src/queneo_editor/editor/3d-web/`)
- Spotykach USB transfer protocol — skill `spotykach/references/usb-transfer.md`
- Wave Bard sample loader — https://apps.bastl-instruments.com/wave-bard-sample-loader/
- Wave Bard web app reference — skill `wave-bard/references/web-app.md`
- FX Wizard Chamber (rhythm editor) — https://apps.bastl-instruments.com/fx-wizard-chamber/
- SP-404MK2 reference manual — https://static.roland.com/manuals/sp-404mk2_reference/eng/17805250.html
- SP-404 USB/MIDI (Python) — https://github.com/blessdog/SP-404
- SP-404 TUI (Go) — https://github.com/moahbeatlab/404tui
- Bastl Kastle 2 platform — https://github.com/bastl-instruments/kastle2
