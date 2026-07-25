"""Turning a cropped card corner into a card.

Two signals are used.  The ink colour of the glyph narrows the answer to one
suit (green, red or black) on its own, and template matching against a bank of
reference crops picks the rank within that suit.

The bank is not shipped: the reference crops are cut from real screenshots by
``python -m shenzhen.vision.calibrate build``.  See ``docs/calibration.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..cards import (
    BLACK,
    FLOWER,
    GREEN,
    RED,
    SUITS,
    card_code,
    colour_of,
    make_card,
    make_dragon,
    parse_card,
)

#: reference crops are normalised to this size before matching
PATCH_SIZE = (40, 40)

# Thresholds for "read this one back to the user before trusting it".  The
# numbers below come from 1594 reads of synthetic boards, where correct
# matches never scored under 0.85 and 99% of them beat the runner-up by more
# than 0.039.  Re-check them against real screenshots -- the game's glyphs are
# far more distinctive than the stand-ins, so these should be conservative.
#: below this correlation the match is a guess worth confirming
MIN_CONFIDENCE = 0.62
#: and below this margin over the runner-up it is ambiguous
MIN_MARGIN = 0.03

_ALL_CARDS: tuple[int, ...] = tuple(
    [make_card(s, r) for s in SUITS for r in range(1, 10)]
    + [make_dragon(c) for c in SUITS]
    + [FLOWER]
)


@dataclass
class Guess:
    card: int
    confidence: float
    margin: float
    colour: int | None

    @property
    def is_confident(self) -> bool:
        return self.confidence >= MIN_CONFIDENCE and self.margin >= MIN_MARGIN


class TemplateBank:
    """Reference crops, one per distinct card face."""

    def __init__(self, templates: dict[int, np.ndarray] | None = None) -> None:
        self.templates: dict[int, np.ndarray] = dict(templates or {})

    def __len__(self) -> int:
        return len(self.templates)

    def __contains__(self, card: int) -> bool:
        return card in self.templates

    @property
    def missing(self) -> list[int]:
        return [c for c in _ALL_CARDS if c not in self.templates]

    @classmethod
    def load(cls, path: str | Path) -> "TemplateBank":
        directory = Path(path)
        if not directory.is_dir():
            raise FileNotFoundError(f"no template bank at {directory}")
        templates: dict[int, np.ndarray] = {}
        for file in sorted(directory.glob("*.png")):
            name = re.sub(r"[^A-Za-z0-9]", "", file.stem)
            try:
                card = parse_card(name)
            except ValueError:
                continue
            image = cv2.imread(str(file), cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue
            templates[card] = normalise(image)
        return cls(templates)

    def save(self, path: str | Path) -> None:
        directory = Path(path)
        directory.mkdir(parents=True, exist_ok=True)
        for card, patch in self.templates.items():
            # Normalised patches are zero-mean floats; rescale for storage.
            scaled = cv2.normalize(patch, None, 0, 255, cv2.NORM_MINMAX)
            cv2.imwrite(str(directory / f"{card_code(card)}.png"), scaled.astype(np.uint8))

    def add(self, card: int, patch: np.ndarray) -> None:
        self.templates[card] = normalise(patch)


def normalise(patch: np.ndarray) -> np.ndarray:
    """Grayscale, resize and zero-mean/unit-variance a crop.

    Removing the mean and scale is what makes matching survive the brightness
    differences between a card in the tableau and the same card in a cell.
    """
    if patch.ndim == 3:
        patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(patch, PATCH_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)
    resized -= resized.mean()
    deviation = float(resized.std())
    if deviation > 1e-6:
        resized /= deviation
    return resized


def ink_colour(patch: np.ndarray) -> int | None:
    """The colour the glyph is printed in, or ``None`` if the crop is blank.

    A blank crop means an empty slot, which the caller needs to distinguish
    from a card it simply failed to read.
    """
    if patch.ndim != 3:
        return None
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    minimum = max(6, patch.size // 400)

    # Coloured ink is picked out by saturation, not by brightness: red print
    # is barely darker than the cream card behind it, so a "darker than the
    # background" test misses it entirely.
    coloured = (saturation > 90) & (value > 60)
    if int(coloured.sum()) >= minimum:
        # Hue is circular, so average it as angles rather than as numbers.
        angles = hue[coloured].astype(np.float32) * (2 * np.pi / 180.0)
        mean_hue = (
            np.degrees(np.arctan2(np.sin(angles).mean(), np.cos(angles).mean())) / 2.0
        ) % 180.0
        return GREEN if 25.0 <= mean_hue <= 95.0 else RED

    # Nothing saturated: either black print, or an empty slot.
    dark = value < float(value.mean()) * 0.78
    if int(dark.sum()) >= minimum:
        return BLACK
    return None


def candidates_for_colour(colour: int | None) -> tuple[int, ...]:
    if colour is None:
        return _ALL_CARDS
    if colour == GREEN:
        return tuple(c for c in _ALL_CARDS if c != FLOWER and colour_of(c) == GREEN)
    if colour == BLACK:
        return tuple(c for c in _ALL_CARDS if c != FLOWER and colour_of(c) == BLACK)
    # The flower is printed in red, so it stays in the red shortlist.
    return tuple(c for c in _ALL_CARDS if colour_of(c) == RED)


def match(patch: np.ndarray, bank: TemplateBank, colour: int | None = None) -> Guess | None:
    """Best template for ``patch``, restricted to one ink colour if known."""
    if not bank.templates:
        raise ValueError("the template bank is empty; run the calibrate command first")

    target = normalise(patch)
    shortlist = [c for c in candidates_for_colour(colour) if c in bank.templates]
    if not shortlist:
        # The colour reading disagrees with the bank; fall back to everything.
        shortlist = list(bank.templates)
    if not shortlist:
        return None

    pixels = float(target.size)
    scores = sorted(
        ((float((target * bank.templates[c]).sum() / pixels), c) for c in shortlist),
        reverse=True,
    )
    best_score, best_card = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else -1.0
    return Guess(card=best_card, confidence=best_score, margin=best_score - runner_up, colour=colour)


def classify(patch: np.ndarray, bank: TemplateBank) -> Guess | None:
    """Read one card crop.  ``None`` means the crop is blank -- an empty slot."""
    colour = ink_colour(patch)
    if colour is None:
        return None
    return match(patch, bank, colour)
