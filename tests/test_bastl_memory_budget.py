"""Bastl memory-usage budget guard (mirrors the loaders' memorySize).

Both the Wave Bard and FX Wizard loaders cap the user-data binary at
``memorySize = 7.5 * 1024 * 1024`` bytes (7,864,320) and disable generation when
``calculateMemoryUsage(...)`` exceeds it. The server mirrors that guard against
the exact ``k2wb``/``k2fx`` binary length: ``encode_binary`` must refuse a build
whose encoded size overruns the budget.

FX Wizard is rhythm-only (max 64 rhythms -> 20 + 256 + 4 = 284 bytes), so its
payload can never reach the budget; the guard is still applied to both targets
for parity, and the max-rhythm case is asserted to stay under budget.
"""

import pytest

from server.devices.fx_wizard import encode_binary as encode_k2fx
from server.devices.wave_bard import encode_binary as encode_k2wb
from server.pattern import BASTL_MEMORY_SIZE


def _wave_bard_bank(data: bytes) -> list[dict]:
    return [
        {
            "label": "bank 1",
            "color": "#FFFFFF",
            "samples": [{"label": "sample 1", "channels": 1, "data": data}],
        }
    ]


def test_k2wb_encode_rejects_binary_over_memory_budget():
    # A single sample whose data alone equals the whole budget pushes the
    # 20-byte header + 12-byte bank header + 16-byte sample header + 4-byte end
    # marker well past 7,864,320 bytes.
    data = b"\x00" * BASTL_MEMORY_SIZE
    with pytest.raises(ValueError, match="memory"):
        encode_k2wb(_wave_bard_bank(data), [], [], 16, 44100)


def test_k2wb_encode_allows_binary_under_memory_budget():
    binary = encode_k2wb(_wave_bard_bank(b"\x00" * 4), [], [], 16, 44100)
    assert len(binary) <= BASTL_MEMORY_SIZE


def test_k2fx_encode_max_rhythms_stays_under_memory_budget():
    # 64 rhythms is the loader's ceiling; the resulting binary is tiny and must
    # pass the shared guard untouched.
    binary = encode_k2fx(list(range(64)), 16)
    assert len(binary) == 20 + 4 * 64 + 4
    assert len(binary) <= BASTL_MEMORY_SIZE
