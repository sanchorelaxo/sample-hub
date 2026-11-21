# Sample Hub

> One localhost web interface that moves a sample between every USB instrument
> on the desk, doing the format conversion for each destination so a drop
> always lands in a file the target can actually use.

Status: milestone 11 — local `.uf2` bake (Wave Bard `k2wb`, FX Wizard `k2fx`) +
dry-run-gated UF2 flash staging. See SPEC.md for the full contract.

![rack client](docs/screenshot.png)

The rack with the desk instruments attached — OP-1, Spotykach, SP-404MK2 and
FX Wizard show a presence dot (green ready, yellow update-mode). Each
instrument is classified sink-only, source-only or sink-source; source devices
(OP-1, SP-404 export dir) list their files, each draggable onto a legal sink.

## Run

    .venv/bin/python -m server --port 8766

Then open http://127.0.0.1:8766/. Loopback only, stdlib only, no build step —
the client is plain ES modules with three.js vendored under `client/vendor/`.

The launcher wires the live hardware: every status poll auto-mounts a visible
but unmounted OP-1 / SD-card / RPI-RP2 volume via `udisksctl` (no sudo), and
each convert resolves its transfer roots from the same probe, so a drop lands
on the device that is actually present now. `SAMPLE_HUB_DATA` moves the
source/staging root (default `~/.sample-hub`).

## Milestone 10: sink/source redesign

Instruments are described by a single JSON tree, `server/devices.json`, which
the server validates and both sides consume. It carries pane order, labels,
the app version / poll cadence / mount-retry interval, and per-device
capabilities: `sink`, `source`, `is_flashable`, and `accepts` (which asset
types a sink will take). Adding a device to the tree is enough to extend the
rack, the conversion matrix, and the client — no other edit needed.

The client has no library pane. Source devices list their content via
`GET /api/devices/<id>/source` and each file is draggable. Drag-and-drop is
legal only between a source and a sink that accepts its type; illegal targets
show a red ✗ and refuse the drop (the server stays authoritative via HTTP 422).
Drops are staged, not sent: each sink lists its pending transfers, and Apply /
Cancel / Undo live in the lower-right bar. Apply commits everything (labelled
"Apply (and flash)" when any staged sink is flashable), Cancel clears all, Undo
removes only the last change.

The SP-404 source reads both `EXPORT/SAMPLE/*.WAV` and
`EXPORT/PROJECT/*/SMPL/*.SMP`. SMP is the MK2 `RFWV` container, decoded by
`server/smp.py` (512-byte header + big-endian 16-bit 48 kHz PCM, MD5-verified)
into canonical float32 WAV before conversion — verified against a live MK2 SD
card.

Flashable Bastl sinks (Wave Bard, FX Wizard) are not drop-and-send targets:
dropping onto one opens a firmware editor overlay (`client/src/editor.js`) that
reproduces the Bastl web loaders — a rhythm editor + sequencer-length selector,
and for Wave Bard a scales editor, a process-samples selector, quality, and
sample banks. Save POSTs the full state to `/api/draft`, which stages a
firmware draft (a `manifest.json` plus the resolved sample WAVs) via the
per-target `build_draft` writers in `server/devices/` (the rhythm/scale
bitmask model lives in `server/pattern.py`). The `.uf2` bake is the next
milestone (below).

## Milestone 11: local .uf2 bake + dry-run flash staging

Flashable Bastl firmware no longer needs the Bastl web loaders. `server/uf2.py`
ports Bastl's `uf2Utils.js`: it reads, generates, validates, and merges the
RP2040 UF2 block format (256-byte payload per 512-byte block, family
`0xE48BFF56`, user data at `0x10080000`). `server/devices/wave_bard.py` adds
the `k2wb` baker — `build_binary` (full editor state → the `k2wb` container,
port of `waveBardBinaryFormat.js`) and `build_uf2` (state → `k2wb` → UF2 blocks
merged onto the vendor `*-no-samples.uf2` with `fill_gaps=True`), with samples
quantized to Int16 by `convert.to_wave_bard_pcm16`. Verified against a real
`kastle2-wave-bard-1.7-no-samples.uf2`: the base program region ends ~86 KB
below the user-data region and the gap is zero-filled, so the merged image
round-trips exactly. `server/devices/fx_wizard.py` adds the `k2fx` baker the
same way — `build_binary` (editor state → the `k2fx` container, port of
`fxWizardBinaryFormat.js`: magic `k2fx`, a rhythm count + sequencer length, one
u32 `steps` mask per rhythm, `ahoj` end marker) and `build_uf2` (state → `k2fx`
→ UF2 blocks merged onto the vendor `kastle2-fx-wizard-*.uf2`). FX Wizard is
rhythm-only, so the container carries no audio samples.

`server/devices/flashable.py` stages the actual write: `find_boot_volumes()`
discovers RP2040 UF2 boot volumes (`INFO_UF2.TXT`), and `stage_uf2(...)` copies
a `.uf2` onto one. Writing flashes and resets the unit, so `stage_uf2` always
runs `dry_run=True` first and only writes when explicitly set to
`dry_run=False`.

Pending: wiring `build_uf2` into the draft path, a flash endpoint and the
base-firmware source, the Bastl memory-usage budget (7.5 MB), and the FX-vs-Bard
UI toggle.

## Milestone 9: server + rack client

`server/hub_server.py` is the HTTP layer: `GET /api/health`, `GET /api/status`
(presence dots), `GET /api/devices` (the tree), `GET /api/devices/<id>/source`,
`POST /api/convert` (one transfer = one job), `GET /api/jobs/<id>`, plus static
serving of `client/` with `Cache-Control: no-store`. Jobs are queued one worker
thread per instrument.

`client/` is the front end: a flat shaded rack (`src/scene.js`, geometry in
`src/layout.js`, one number per instrument position in mm), DOM overlay panes
(`src/panes.js`) for each instrument, presence-dot polling on the tree's
`app.poll_ms` cadence (`src/status.js`), legal-only HTML5 drag-and-drop with
staging (`src/dragdrop.js`, `src/staging.js`). The 3D layer is presentation
only; all device I/O stays behind the API.

## Milestone 1: canonical normalizer

`server/library.py` exposes `normalize(src, dst)`: it turns any supported WAV
into the hub's one working form — 48 kHz, stereo, 32-bit IEEE float — the same
shape `wav_builder` already produces for the Spotykach, written with the plain
IEEE-float tag `0x0003` (not `WAVE_FORMAT_EXTENSIBLE`) so the Spotykach
firmware reads it. Every destination transform renders from this copy.

Two properties are pinned by tests:

- stateless / parallel-safe — ffmpeg is NOT a singleton; each `normalize` call
  runs its own subprocess with no shared lock or temp file, so parallel jobs
  (one per instrument) never contend.
- refuses what it cannot decode — a bad source raises `NormalizeError` instead
  of writing a corrupt file.

## Tests

    uv venv --python 3.11      # once
    uv pip install pytest
    .venv/bin/python -m pytest tests/ -q

Tests generate their source WAVs with ffmpeg and decode the normalized bytes
back, so every assertion runs against real bytes, never a mock. Client-shell
tests serve the real `client/` directory and assert the assets are served with
`no-store`, that every module import is relative and resolves (no CDN), that
each source module parses as an ES module, and that the client derives its
track from `/api/devices` rather than a hardcoded list.

The UF2 bake is covered device-free: `test_uf2.py` (block format, byte-exact),
`test_wave_bard_bake.py` (`k2wb` encode + merge onto a base firmware), and
`test_flashable.py` (`stage_uf2` dry-run + write against a fake boot volume).
The real-device flash test (`test_bastl_flash.py`) is read-only — it discovers
and validates the boot volume but never copies a `.uf2` onto it.

Real-device tests are gated off by default. Tests that talk to a physical USB
instrument are marked `@pytest.mark.real_devices` and only run when you opt in:

    .venv/bin/python -m pytest tests/ --real-devices

## Layout

    server/hub_server.py   HTTP layer (milestone 9c)
    server/__main__.py     launcher: python -m server (milestone 9d)
    server/devices.json    the device tree: order, labels, capabilities (10)
    server/device_tree.py  validated loader + typed views of devices.json (10)
    server/sources.py      source-device listing + read-back (10)
    server/library.py      host cache + canonical normalizer (milestones 1, 9a)
    server/convert.py      conversion matrix, one fn per target
    server/uf2.py          UF2 read/generate/validate/merge (11)
    server/devices/        per-target writers + flashable.py (flash staging) (11)
    server/jobs.py         convert dispatch + per-instrument job queue (9b)
    server/status.py       per-instrument presence probe (milestone 8)
    server/pattern.py      rhythm/scale bitmask model + Bastl validation (10)
    client/                three.js rack client, no build step (milestones 9d, 10)
    SPEC.md                the contract this is built against
