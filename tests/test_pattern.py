"""Rhythm + scale pattern model shared by the Bastl editors and draft builders.

The Bastl web editors store a rhythm as a ``steps`` bitmask (step ``p`` set iff
bit ``length-1-p`` is 1, ``0`` == empty) and a scale as a 12-bit ``semitones``
mask (bit ``n`` == chromatic semitone ``n``, 0 == empty). These tests pin that
model and the loader validation limits so the client editor and the server
draft builders cannot drift apart.
"""

from __future__ import annotations

from server.pattern import (
    BLACK_KEYS,
    FX_WIZARD_RHYTHMS,
    PROCESS_OPTIONS,
    SEQUENCER_LENGTHS,
    WAVE_BARD_BANKS,
    WAVE_BARD_RHYTHMS,
    WAVE_BARD_SAMPLES_PER_BANK,
    WAVE_BARD_SCALES,
    pattern_to_steps,
    semitone_active,
    step_active,
    steps_to_pattern,
    toggle_semitone,
    toggle_step,
    validate_fx_wizard,
    validate_wave_bard,
)


def test_sequencer_lengths_match_bastl_selector():
    # Classics first, then the "Others" list the loaders offer.
    assert SEQUENCER_LENGTHS[:4] == (8, 16, 32, 64)
    others = SEQUENCER_LENGTHS[4:]
    assert 5 in others and 63 in others and 42 in others
    assert others == tuple(sorted(others))
    # No duplicates across the two groups.
    assert len(SEQUENCER_LENGTHS) == len(set(SEQUENCER_LENGTHS))


def test_step_bitmask_is_msb_first():
    # 8 steps, first and last active -> bits 7 and 0.
    assert pattern_to_steps([0, 7], 8) == 0b10000001
    assert step_active(0b10000001, 0, 8) is True
    assert step_active(0b10000001, 7, 8) is True
    assert step_active(0b10000001, 3, 8) is False


def test_toggle_step_flips_one_bit():
    mask = pattern_to_steps([2], 16)
    assert toggle_step(mask, 2, 16) == 0
    assert toggle_step(0, 2, 16) == mask


def test_steps_round_trip():
    for length, active in ((16, [0, 4, 15]), (5, [1, 3]), (64, [0, 63])):
        assert steps_to_pattern(pattern_to_steps(active, length), length) == active


def test_semitone_mask_is_chromatic():
    # bit 0 == C, bit 1 == C#, bit 11 == B.
    assert semitone_active(0b100000000001, 0) is True
    assert semitone_active(0b100000000001, 11) is True
    assert semitone_active(0b100000000001, 1) is False
    assert toggle_semitone(0, 3) == 0b1000


def test_black_keys_are_the_chromatic_accidentals():
    assert BLACK_KEYS == frozenset({1, 3, 6, 8, 10})


def test_process_options_are_the_loader_toggles():
    assert PROCESS_OPTIONS == ("fadeIn", "fadeOut", "trimQuiet", "maximize")


def test_fx_wizard_limits():
    assert FX_WIZARD_RHYTHMS == (3, 64)


def test_wave_bard_limits():
    assert WAVE_BARD_BANKS == (1, 32)
    assert WAVE_BARD_SAMPLES_PER_BANK == (3, 32)
    assert WAVE_BARD_SCALES == (3, 32)
    assert WAVE_BARD_RHYTHMS == (3, 32)


def test_validate_fx_wizard_rejects_empty_or_short():
    assert validate_fx_wizard([]) is not None
    assert validate_fx_wizard([{"name": "a", "steps": 0}]) is not None
    assert validate_fx_wizard([{"name": "a", "steps": 1}]) is not None  # 1 < 3
    three = [{"name": n, "steps": 1} for n in "abc"]
    assert validate_fx_wizard(three) is None


def test_steps_mask_is_64_bit_safe():
    # A 64-step mask overflows a JSON number, so the client sends it as a
    # decimal string; the validator must accept that form.
    mask = 1 << 63
    assert steps_to_pattern(mask, 64) == [0]
    assert pattern_to_steps([0], 64) == mask
    assert validate_fx_wizard([
        {"name": "hi", "steps": str(mask)},
        {"name": "mid", "steps": "1"},
        {"name": "lo", "steps": "1"},
    ]) is None


def test_validate_rejects_string_zero_masks():
    assert validate_fx_wizard([
        {"name": "a", "steps": "0"},
        {"name": "b", "steps": "1"},
        {"name": "c", "steps": "1"},
    ]) is not None


def test_validate_wave_bard_requires_uniform_banks_and_valid_counts():
    bank = {"name": "b", "samples": [{"name": f"s{i}"} for i in range(3)]}
    scales = [{"name": n, "semitones": 1} for n in "abc"]
    rhythms = [{"name": n, "steps": 1} for n in "abc"]
    state = {"banks": [bank], "scales": scales, "rhythms": rhythms}
    assert validate_wave_bard(state) is None

    # banks must be uniform (both counts in-range but different).
    four = {"name": "c", "samples": [{"name": f"s{i}"} for i in range(4)]}
    uneven = {"banks": [bank, four], "scales": scales, "rhythms": rhythms}
    assert "same number" in validate_wave_bard(uneven)

    # a bank below the minimum sample count is its own error.
    short = {"banks": [bank, {"name": "c", "samples": []}],
             "scales": scales, "rhythms": rhythms}
    assert "between 3 and 32" in validate_wave_bard(short)

    # scales/rhythms still need 3 each.
    assert validate_wave_bard({"banks": [bank], "scales": [], "rhythms": rhythms}) is not None
    assert validate_wave_bard({"banks": [bank], "scales": scales, "rhythms": []}) is not None
