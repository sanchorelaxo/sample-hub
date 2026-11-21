# Sample Hub — cross-instrument sample workstation

> **Goal:** one localhost web interface that moves a sample between every USB
> instrument on the desk, doing the format conversion for each destination so a
> drop always lands in a file the target can actually use.

**Status:** implemented through milestone 11 (sink/source redesign plus local
`.uf2` bake and dry-run flash staging for Wave Bard and FX Wizard, §8).
Remaining: the FX-vs-Bard UI toggle, and wiring
`build_uf2` into the draft path with a flash endpoint. This file is the
contract the implementation is built against.

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
    uf2.py                UF2 read/generate/validate/merge (Kastle 2 RP2040)
    pattern.py            rhythm/scale bitmask model + Bastl validation
    devices/
      op1.py              reuse op1-web AIFC path
      spotykach.py        reuse spottytube transport
      wave_bard.py        k2wb .uf2 baker + draft staging (samples + rhythms + scales)
      fx_wizard.py        k2fx .uf2 baker + draft staging (rhythms + step count)
      sp404.py            SD IMPORT folder writer
      flashable.py        RP2040 boot-volume discovery + dry-run-gated stage_uf2
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
      usage.js            Bastl memory-usage mirror (calculateUsage.js) + budget
      status.js           presence dot polling (20 s)
    styles.css
  tests/
    ...
```

### API surface (proposed)

```
GET  /api/health                       {"ok":true,"version":"0.1.0"}
GET  /api/status                       per-instrument presence + transport state
GET  /api/devices                      the device tree (single source of truth)
GET  /api/devices/<id>/source          read a file back off a source instrument
GET  /api/library                      cached canonical samples + rhythms (metadata)
POST /api/library/samples?name=...     raw WAV body -> canonical cache entry
POST /api/library/rhythms?name=...     step/gate JSON -> rhythm entry
GET  /api/library/samples/<id>         canonical WAV bytes
GET  /api/library/rhythms/<id>         step/gate JSON
POST /api/convert                      {source_id, target, params?} -> {job_id}
POST /api/draft                        full editor state -> firmware draft job id
GET  /api/jobs/<id>                    {status:"queued|running|done|error", result, error}
```

`/api/convert` is the one-shot gesture: it resolves the source, normalizes to
canonical, converts for the target, transfers, and returns the on-device
result. `/api/draft` is the authoring path for flashable Bastl sinks: it takes
the whole editor state (rhythms, sequencer length, and for Wave Bard scales,
process toggles, quality, and sample banks), resolves each bank sample to
canonical WAV bytes server-side, stages a firmware draft (a `manifest.json`
plus the resolved sample WAVs via the target's `build_draft`), and returns a
job id. The `.uf2` bake itself is `build_binary`/`build_uf2` — implemented for
Wave Bard and device-free tested, but not yet wired to an endpoint. Directly
converting to `wave_bard` or `fx_wizard` via `/api/convert` is refused — those
targets are authored in the editor, not dropped as one-shot jobs.

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

### 5.2 Firmware editor (Wave Bard / FX Wizard)

A drop onto a flashable Bastl sink (Wave Bard or FX Wizard) does not stage a
one-shot conversion — it opens a full-screen authoring overlay, and the "edit"
affordance on those panes opens the same editor empty. The editor reproduces
the two Bastl web loaders:

- **Rhythm editor + sequencer (both).** Each rhythm is a list item with a name
  and a step grid. The sequencer-length selector offers the loader's options:
  Classics `8/16/32/64`, Others `5,6,7,9,10,11,12,13,14,15,17,20,21,24,36,42,48,54,60,63`.
  A dropped rhythm seeds a new entry.
- **Scales editor (Wave Bard only).** Each scale is a 12-note chromatic grid
  (black keys shaded); a set semitone selects the note.
- **Process samples (Wave Bard only).** Four toggles — Fade in / Fade out
  (0.005 s), Trim quiet (< -48 dB), Maximize to -0.1 dB.
- **Quality (Wave Bard only).** HIGH 44.1 kHz / LOW 22.05 kHz / TELEPHONE
  11.025 kHz.
- **Sample banks (Wave Bard only).** 1–32 banks, 3–32 samples each (uniform);
  a dropped sample seeds the first bank, and each bank is a drop zone for more.

Save ("Apply (and flash)") serializes the state and POSTs `/api/draft`, which
stages a firmware draft (manifest + resolved samples) and returns a job id; the
pane shows the job like any other transfer. The client validates locally before
submitting (rhythm and scale counts, uniform bank sizes) so the save button
stays disabled until the draft is legal — mirroring `validate_wave_bard` /
`validate_fx_wizard` on the server. The footer also shows a live memory readout
(`<used> / 7.5 MiB`) computed from the same `calculateUsage.js` formula the
Bastl loaders use, and blocks the save with an "over the memory budget" error
when the draft would exceed the 7.5 MiB `memorySize` guard (server-side
`BASTL_MEMORY_SIZE` stays authoritative for the exact Int16 PCM size).

Step and semitone data are integer bitmasks. A 64-step mask overflows a JSON
number, so the client stores `steps` as a decimal string backed by BigInt and
the server coerces it back with `int(...)`. The first step is the most
significant bit: step `p` (0-based) is bit `(length - 1 - p)`.

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
- **amber** — present but blocked on a user step (Wave Bard / FX Wizard draft not
  staged, or no board in update mode; SP-404 "replug direct — on hub").

The dot re-checks every 20 s, same cadence as the `spottytube` widget. A drop on
a red pane is refused with the reason, never queued blindly.

**Auto-mount.** A removable volume that is visible but unmounted is not left
red. On each poll the backend auto-mounts a detected OP-1 disk, SD card, or
RPI-RP2 update-mode volume via `udisksctl mount -b` (no sudo, authorized by
the session polkit agent), so a card that is inserted, ejected, and re-inserted
becomes usable on the next poll with no manual step. A device that refuses to
mount reads amber with a `udisksctl mount -b <device>` hint to run by hand; a
failed mount is retried at most once per 10 s window.

**Wave Bard and FX Wizard are one board.** Both are the same RP2040 (Bastl
Kastle 2); in update mode each mounts as an identical RPI-RP2 volume with the
same VID/PID (`2e8a:0003`) and the same USB serial, so no hardware signal tells
a Wave Bard from an FX Wizard. Bastl's own loaders never detect either — they
just generate a `.uf2` for the user to copy onto whatever board is in update
mode. The probe therefore treats every board in update mode as a shared "ready
to flash" signal and auto-mounts each RPI-RP2 volume (two at once mount as
`RPI-RP2` and `RPI-RP21`). A pane goes green only when its draft is staged
*and* a board is actually in update mode to receive the flash.

### Drag-and-drop semantics

- **Draggable sources:** Library entries (samples and rhythms); OP-1 disk
  entries; Spotykach slots; SP-404 SD entries.
- **SP-404 source formats:** `EXPORT/SAMPLE/*.WAV` and
  `EXPORT/PROJECT/*/SMPL/*.SMP`. SMP is the MK2 `RFWV` container (512-byte
  header + big-endian 16-bit 48 kHz PCM, MD5-digest verified); `server/smp.py`
  decodes it to WAV and it normalizes like any other source.
- **Valid targets, by asset type:**
  - *samples* → OP-1, Spotykach, Wave Bard, SP-404.
  - *rhythms* → Wave Bard, FX Wizard.
  - FX Wizard refuses a sample ("rhythms only — no sample input"); SP-404,
    OP-1 and Spotykach refuse a rhythm ("samples only").
- **Drop = one conversion job** — except on a flashable Bastl sink, where a
  drop opens the firmware editor instead (§5.2). For a normal sink the pane
  shows a job row with a spinner and the target format being produced (e.g.
  "→ 48k float32 · slot G/2", "→ rhythm .uf2 · 16 steps").
- **Invalid drops** (wrong asset type, over length cap, red pane) are rejected
  inline with the specific reason — "42 s stored cap", "rhythms only",
  "replug direct" — not a generic error.
- **No deletes, ever.** Spotykach drops append to the first empty slot (nothing
  shifts or falls off); OP-1 and SP-404 writes are additive. The UI offers no
  delete affordance in v1. (This mirrors the existing tools' read-back
  discipline.)

### Conversion matrix shown to the user

On hover over a target pane, a small readout states what the drop will produce,
so the conversion is never a surprise:

| Target | Result of a drop |
|---|---|
| OP-1 drum | mono 44.1 kHz AIFC, ≤ 12 s, `drum/user/<name>.aif` |
| OP-1 synth/tape | 44.1 kHz AIFC, target folder chosen by drop zone |
| Spotykach | 48 kHz stereo float32 WAV, first empty slot (append) |
| Wave Bard (sample) | tuned-to-C mono sample staged into a bank |
| Wave Bard (rhythm) | rhythm pattern + step count staged into the firmware draft |
| FX Wizard (rhythm) | rhythm pattern + step count → `.uf2`, "generate firmware" button |
| SP-404 | 16-bit linear 48 kHz WAV into SD `IMPORT/`; manual import steps shown |

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
| Wave Bard | 48k f32 stereo | mono Int16 PCM at the selected quality rate, packed into the `k2wb` image | downmix → process (fade/trim/maximize) → quantize to Int16 → pack `k2wb` binary |
| SP-404 | 48k f32 stereo | 16-bit linear WAV | keep 48 kHz (device-native rate) → dither to 16-bit |

> SP-404 rate reference: the authoritative source is
> [TuscanPL/SparkyMK2](https://github.com/TuscanPL/SparkyMK2) —
> `crates/sp404-formats/src/smp.rs` sets `SAMPLE_RATE = 48_000`, and the README
> states the MK2 imports WAV/AIFF/FLAC/MP3 at any rate and stores at 48 kHz
> 16-bit internally. The earlier "44.1 kHz" assumption was wrong; the target is
> 48 kHz 16-bit.

### Rhythm patterns (canonical: step/gate JSON)

| Target | Input | Output | Transform |
|---|---|---|---|
| Wave Bard | step/gate JSON | rhythm bank + step count in firmware draft | map to loader rhythm slots |
| FX Wizard | step/gate JSON | rhythm pattern + step count → `.uf2` | map to Chamber rhythm/step format |

A rhythm is `{"name", "steps"}` and a scale is `{"name", "semitones"}`, both
integer bitmasks with the first step/note as the most significant bit. The
client sends a 64-step `steps` mask as a decimal string (BigInt) so it survives
JSON; the server coerces with `int(...)` before validating. `steps == 0` or
`semitones == 0` is empty and rejected.

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
- **Read back and compare.** A Spotykach append writes one slot and reads it
  back to verify; a dropped transfer after bytes hit the tape is reported,
  never re-run (a retry would append to the next empty slot, so it is not
  idempotent).
- **Never delete.** Spotykach append stops at a full chain with an error
  ("free a slot on the unit") rather than overwriting or discarding; nothing is
  ever silently discarded.
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
6. **Wave Bard stager + local bake** — bank JSON + samples + rhythms staged via
   `build_draft`, then baked locally into a `k2wb` binary and merged onto the
   base firmware as a `.uf2` (`build_binary`/`build_uf2` in
   `server/devices/wave_bard.py` + `server/uf2.py`). No Bastl-loader handoff.
7. **FX Wizard stager** — rhythm pattern + step count → firmware draft
   (`build_draft`) and the `k2fx` binary baker (`build_binary`/`build_uf2`).
8. **Status probe** — presence/transport detection per instrument (mount,
   `/dev/ttyACM*`, card, hub check for SP-404, RPI-RP2 update-mode detection
   for Wave Bard / FX Wizard), plus auto-mount of a visible but unmounted OP-1
   disk / SD card / RPI-RP2 volume via `udisksctl` so re-inserted devices are
   usable on the next poll.
9. **Server + three.js rack client** — the `three` rack scene, DOM overlay
   panes, drag/drop, job queue, presence dots. No per-instrument 3D models yet
   (that is the next phase, §5.1).
10. **Sink/source redesign** — the single JSON device tree (`server/devices.json`)
    as the source of truth for order/labels/capabilities, source panes that list
    each source device's files, legal-only drag-and-drop with no library pane,
    and the staged-transfer Apply / Cancel / Undo bar. Adding a device to the
    tree is enough to extend the rack, the conversion matrix, and the client.
11. **Local `.uf2` bake + dry-run flash staging** — `server/uf2.py` (UF2
    read/generate/validate/merge), the Wave Bard `k2wb` baker (`build_binary`/
    `build_uf2` + `to_wave_bard_pcm16`), and `server/devices/flashable.py`
    (`find_boot_volumes` + `stage_uf2`, whose `dry_run=True` must always run
    before any real write). Done for both Wave Bard (`k2wb`) and FX Wizard
    (`k2fx`).

**First concrete step:** milestone 1, a failing test for the canonical
normalizer (`tests/test_normalize.py`), then the smallest implementation that
passes.

---

## 9. Open questions / evidence needed

- **OP-1 synth-patch format** is more complex than a plain AIFC file; confirm
  the exact patch container before promising synth/tape drops in v1. Drum kits
  are already proven in `op1-web`.
- **SP-404 exact `IMPORT` folder path** — RESOLVED (2026-11-20). The SP-404MK2
  must format its own SD card (FAT32), never a computer: `SHIFT+pad14` (or
  `SHIFT+pad13 → UTILITY → IMPORT`) opens the IMPORT/EXPORT MENU, then
  `FORMAT SD-CARD`. The unit creates top-level `IMPORT/` and `EXPORT/`; samples
  go directly into `IMPORT/` (subfolders inside it are shown on the unit).
  Unit-side import is `SHIFT+pad13 → UTILITY → IMPORT → IMPORT from SD-CARD →
  SAMPLE`. Writer targets top-level `IMPORT/` — confirmed correct.
- **Wave Bard and FX Wizard `.uf2` generation offline** — RESOLVED (both,
  2026-11-20). The `.uf2` is now baked locally: `server/uf2.py` ports Bastl's
  `uf2Utils.js` (RP2040 UF2 blocks at `USER_DATA_BEGIN = 0x10080000`, family
  `0xE48BFF56`) and `server/devices/wave_bard.py` ports `waveBardBinaryFormat.js`
  (the `k2wb` container: 20-byte header, then scales/rhythms/banks/samples as
  Int16 PCM, `ahoj` end marker), merged onto the vendor `*-no-samples.uf2` with
  `fill_gaps=True`. The `bastl-instruments/kastle2` toolchain was NOT required —
  the formats are a straight port of the loaders' JS. FX Wizard's `k2fx`
  container (`fxWizardBinaryFormat.js`) is the same shape (rhythms only), baked
  by `server/devices/fx_wizard.py` (`build_binary`/`build_uf2`). Remaining: wire
  `build_uf2` into the draft path and a flash endpoint, and resolve the
  base-firmware source (vendor file vs runtime fetch). Both loaders save/load a
  "draft file", which the hub should read and write so a user can round-trip
  their banks and rhythms.
- **SP-404 hub detection** — implemented in the M8 status probe: a hub-backed
  card reader reports amber "replug direct" (SPEC §2). Confirmed by synthetic
  sysfs tests; not yet verified against the real unit.

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
