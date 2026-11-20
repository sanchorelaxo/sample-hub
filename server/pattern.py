"""Rhythm + scale pattern model for the Bastl Kastle 2 editors.

These are the pure data helpers shared by the server draft builders and the
client editor. The wire format mirrors the Bastl web editors:

  * rhythm  : ``{"name", "steps"}``      ``steps`` is an integer bitmask.
             Step ``p`` (0-based, first step = most significant) is active iff
             bit ``(length - 1 - p)`` is set. ``0`` means "empty" (invalid).
  * scale   : ``{"name", "semitones"}``  ``semitones`` is a 12-bit mask, bit
             ``n`` == chromatic semitone ``n`` (0 == C, 11 == B). ``0`` == empty.

Keeping these pure and testable lets the client and server stay in lockstep
without sharing a runtime.
"""

from __future__ import annotations

# Sequencer length options the loaders expose, Classics first then "Others".
SEQUENCER_CLASSICS = (8, 16, 32, 64)
SEQUENCER_OTHERS = (
    5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 17, 20, 21, 24, 36, 42, 48, 54, 60, 63,
)
SEQUENCER_LENGTHS = SEQUENCER_CLASSICS + SEQUENCER_OTHERS

# Chromatic indices of the black keys (C# D# F# G# A#).
BLACK_KEYS = frozenset({1, 3, 6, 8, 10})

# Wave Bard sample-load "quality" options -> sample rate in Hz.
WAVE_BARD_RATES = {"HIGH": 44100, "LOW": 22050, "TELEPHONE": 11025}
WAVE_BARD_DEFAULT_RATE = WAVE_BARD_RATES["HIGH"]

# Wave Bard "process samples" toggles (ordered as the loader lists them).
PROCESS_OPTIONS = ("fadeIn", "fadeOut", "trimQuiet", "maximize")

# Loader validation limits (minimum, maximum).
FX_WIZARD_RHYTHMS = (3, 64)
WAVE_BARD_BANKS = (1, 32)
WAVE_BARD_SAMPLES_PER_BANK = (3, 32)
WAVE_BARD_SCALES = (3, 32)
WAVE_BARD_RHYTHMS = (3, 32)

# Default sequencer length per editor.
FX_WIZARD_DEFAULT_LENGTH = 8
WAVE_BARD_DEFAULT_LENGTH = 16


def step_active(steps: int, index: int, length: int) -> bool:
    """True when step ``index`` is set in a ``length``-step bitmask."""
    return bool(steps & (1 << (length - 1 - index)))


def toggle_step(steps: int, index: int, length: int) -> int:
    """Flip step ``index`` in a ``length``-step bitmask."""
    return steps ^ (1 << (length - 1 - index))


def steps_to_pattern(steps: int, length: int) -> list[int]:
    """Return the active (0-based, MSB-first) step indices of a bitmask."""
    return [p for p in range(length) if step_active(steps, p, length)]


def pattern_to_steps(pattern: list[int], length: int) -> int:
    """Encode active step indices as a ``length``-step bitmask."""
    mask = 0
    for p in pattern:
        mask |= 1 << (length - 1 - p)
    return mask


def semitone_active(semitones: int, index: int) -> bool:
    """True when chromatic semitone ``index`` (0 == C) is set."""
    return bool(semitones & (1 << index))


def toggle_semitone(semitones: int, index: int) -> int:
    """Flip chromatic semitone ``index`` in a 12-bit mask."""
    return semitones ^ (1 << index)


def validate_rhythms(rhythms: list[dict], minimum: int, maximum: int) -> str | None:
    if not (minimum <= len(rhythms) <= maximum):
        return f"rhythms must be between {minimum} and {maximum} (got {len(rhythms)})"
    for rhythm in rhythms:
        if not rhythm.get("steps", 0):
            return f"rhythm {rhythm.get('name', '?')!r} is empty"
    return None


def validate_scales(scales: list[dict], minimum: int, maximum: int) -> str | None:
    if not (minimum <= len(scales) <= maximum):
        return f"scales must be between {minimum} and {maximum} (got {len(scales)})"
    for scale in scales:
        if not scale.get("semitones", 0):
            return f"scale {scale.get('name', '?')!r} is empty"
    return None


def validate_fx_wizard(rhythms: list[dict]) -> str | None:
    """Return an error string, or None when the FX Wizard rhythm set is valid."""
    return validate_rhythms(rhythms, *FX_WIZARD_RHYTHMS)


def validate_wave_bard(state: dict) -> str | None:
    """Return an error string, or None when a Wave Bard draft is valid."""
    banks = state.get("banks", [])
    if not (WAVE_BARD_BANKS[0] <= len(banks) <= WAVE_BARD_BANKS[1]):
        return f"banks must be between {WAVE_BARD_BANKS[0]} and {WAVE_BARD_BANKS[1]} (got {len(banks)})"

    counts = {len(bank.get("samples", [])) for bank in banks}
    if any(not (WAVE_BARD_SAMPLES_PER_BANK[0] <= c <= WAVE_BARD_SAMPLES_PER_BANK[1]) for c in counts):
        return f"each bank must have between {WAVE_BARD_SAMPLES_PER_BANK[0]} and {WAVE_BARD_SAMPLES_PER_BANK[1]} samples"
    if len(counts) > 1:
        return "all banks must have the same number of samples"

    err = validate_scales(state.get("scales", []), *WAVE_BARD_SCALES)
    if err:
        return err
    return validate_rhythms(state.get("rhythms", []), *WAVE_BARD_RHYTHMS)
