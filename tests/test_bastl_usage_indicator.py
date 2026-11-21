"""Browser-side Bastl memory-usage indicator (TODO_1.txt lines 50-51).

The server already enforces the 7.5 MiB budget in both bakers; this half is
the client indicator that mirrors the Bastl loaders' ``memorySize`` guard
(``config.js`` ``memorySize = 7.5 * 1024 * 1024`` and ``calculateUsage.js``).
The client cannot know the exact Int16 PCM size (that conversion happens
server-side), so ``usage.js`` mirrors the loader's ``calculateMemoryUsage``
formula using each sample's source byte size as a conservative upper bound;
the server's exact guard stays authoritative.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"


def _usage_json(expr: str):
    """Evaluate ``expr`` inside the usage module under node, returning JSON."""
    usage = CLIENT_DIR / "src/usage.js"
    script = (
        f"const m = await import({json.dumps(usage.as_uri())});\n"
        f"console.log(JSON.stringify({expr}));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_budget_matches_loaders():
    assert _usage_json("m.BASTL_MEMORY_SIZE") == 7.5 * 1024 * 1024
    assert _usage_json("m.BASTL_MEMORY_SIZE") == 7_864_320


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_fx_wizard_usage_matches_binary_layout():
    # server encode_k2fx: 20-byte header + 4 bytes per rhythm mask + 4-byte ahoj.
    assert _usage_json("m.fxWizardUsage({ rhythms: [{}, {}, {}] })") == 20 + 4 * 3 + 4
    assert _usage_json("m.fxWizardUsage({ rhythms: Array.from({ length: 64 }) })") == 280
    assert _usage_json("m.fxWizardUsage({ rhythms: [] })") == 24


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_wave_bard_usage_matches_loader_formula():
    # calculateUsage.js: 20 + 4*scales + 4*rhythms + sum(12 + sum(16 + data)) + 4.
    one_bank = {
        "scales": [{}, {}, {}],
        "rhythms": [{}, {}, {}],
        "banks": [{"samples": [{"bytes": 1000}]}],
    }
    assert _usage_json(f"m.waveBardUsage({json.dumps(one_bank)})") == 1076

    two_banks = {
        "scales": [{}, {}],
        "rhythms": [{}, {}],
        "banks": [
            {"samples": [{"bytes": 100}, {"bytes": 200}]},
            {"samples": [{"bytes": 300}, {"bytes": 400}]},
        ],
    }
    assert _usage_json(f"m.waveBardUsage({json.dumps(two_banks)})") == 1128


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_format_mib_one_decimal():
    assert _usage_json("m.formatMiB(7864320)") == "7.5 MiB"
    assert _usage_json("m.formatMiB(0)") == "0.0 MiB"


def test_usage_indicator_is_wired_into_client():
    editor = (CLIENT_DIR / "src/editor.js").read_text()
    assert "./usage.js" in editor, "editor must import the usage mirror"
    assert "BASTL_MEMORY_SIZE" in editor, "editor must reference the shared budget"
    assert "memory budget" in editor, "editor must report the over-budget condition"
    assert "formatMiB" in editor, "editor must render a MiB readout"

    panes = (CLIENT_DIR / "src/panes.js").read_text()
    assert "sourceBytes" in panes, "source list must expose byte size to the indicator"

    dragdrop = (CLIENT_DIR / "src/dragdrop.js").read_text()
    assert "sourceBytes" in dragdrop, "drag payload must carry byte size to the editor"
