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

# Thresholds for "read this one back to the user before trusting it".  Across
# the screenshot fixtures -- 205 reads, two devices, three board scales --
# every correct match scored at least 0.89 and beat the runner-up by at least
# 0.06, so both of these sit well clear of anything seen in practice.
#: below this correlation the match is a guess worth confirming
MIN_CONFIDENCE = 0.62
#: and below this margin over the runner-up it is ambiguous
MIN_MARGIN = 0.04

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
    #: every card in the bank scored against this crop, best first.  Kept so
    #: that a read the bot is unsure about can offer runners-up rather than
    #: only its winner -- see :mod:`shenzhen.vision.resolve`.  It spans the
    #: whole bank rather than the ink colour's shortlist because a misread
    #: glyph sometimes takes its colour down with it.
    ranking: tuple[tuple[int, float], ...] = ()
    #: whether the ink colour was a clear-cut call.  A glyph matched against
    #: the wrong colour's templates can win its shortlist by a mile and still
    #: be the wrong card, so a shaky colour has to survive as doubt about the
    #: whole read rather than being resolved into a confident mistake.
    colour_certain: bool = True

    @property
    def is_confident(self) -> bool:
        return (
            self.colour_certain
            and self.confidence >= MIN_CONFIDENCE
            and self.margin >= MIN_MARGIN
        )


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
    def load(cls, path: str | Path) -> TemplateBank:
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
            templates[card] = standardise(image)
        return cls(templates)

    def save(self, path: str | Path) -> None:
        directory = Path(path)
        directory.mkdir(parents=True, exist_ok=True)
        for card, patch in self.templates.items():
            # Normalised patches are zero-mean floats; rescale for storage.
            scaled = cv2.normalize(patch, None, 0, 255, cv2.NORM_MINMAX)
            cv2.imwrite(str(directory / f"{card_code(card)}.png"), scaled.astype(np.uint8))

    def add(self, card: int, patch: np.ndarray) -> None:
        self.templates[card] = standardise(patch)


def standardise(patch: np.ndarray) -> np.ndarray:
    """Resize to the reference size and remove brightness and contrast.

    Removing the mean and scale is what makes matching survive the difference
    between a card in the tableau and the same card in a free cell.
    """
    if patch.ndim == 3:
        patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(
        patch.astype(np.float32), PATCH_SIZE, interpolation=cv2.INTER_AREA
    )
    resized -= resized.mean()
    deviation = float(resized.std())
    if deviation > 1e-6:
        resized /= deviation
    return resized


#: below this saturation a pixel is not counted as coloured ink.  Low enough
#: that green ink survives JPEG chroma subsampling -- which discards colour
#: detail far more aggressively than brightness detail, so a small green
#: glyph in a compressed screenshot can read barely more saturated than the
#: cream card behind it even though the same glyph is unambiguous in a PNG.
COLOUR_SATURATION = 50

#: how much of a crop's border is excluded from the ink statistics, as a
#: fraction of each side, and never less than a pixel.  A card's box is
#: occasionally off by one -- more likely on a JPEG's softer edges -- and a
#: sliver of green felt caught at the border reads as saturated ink. It is a
#: thin minority of the crop, so leaving it out costs nothing; but at a low
#: saturation threshold it is exactly as "coloured" as genuine but washed-out
#: ink, so it has to be kept out geometrically rather than filtered by degree.
#: A fraction rather than a pixel count because enlarging the picture spreads
#: that one-pixel sliver over as many pixels as it spreads everything else.
EDGE_MARGIN = 0.04

#: this fraction of a crop has to read as coloured ink before its glyph is
#: called green or red rather than black.  A fraction rather than a pixel
#: count so that the same call comes out of the same card whatever size it is
#: drawn at -- enlarging a picture must not be able to change its colours.
COLOUR_FRACTION = 0.0075

#: how far either side of that the reading is too close to call.  Both kinds
#: of mistake live in this band and nowhere else: washed-out green ink on a
#: twice-compressed screenshot ends up just under the line, and the chroma
#: fringing along the white dragon's black rectangle ends up just over it.
#: Neither is worth trying to separate by tightening the threshold -- there is
#: no value that has them on opposite sides.  What they have in common is that
#: the deck settles both, given they are handed on as doubt rather than
#: swallowed as a decision.
COLOUR_UNCERTAIN = 2.0


def ink_reading(patch: np.ndarray) -> tuple[int | None, bool]:
    """The colour the glyph is printed in, and whether that was clear-cut.

    ``None`` means the crop is blank -- an empty slot, which the caller needs
    to distinguish from a card it simply failed to read.
    """
    if patch.ndim != 3:
        return None, True
    height, width = patch.shape[:2]
    margin_y = max(1, round(height * EDGE_MARGIN))
    margin_x = max(1, round(width * EDGE_MARGIN))
    if height > 2 * margin_y and width > 2 * margin_x:
        patch = patch[margin_y:-margin_y, margin_x:-margin_x]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    pixels = patch.shape[0] * patch.shape[1]
    minimum = max(6, COLOUR_FRACTION * pixels)

    # Coloured ink is picked out by saturation, not by brightness: red print
    # is barely darker than the cream card behind it, so a "darker than the
    # background" test misses it entirely.
    coloured = (saturation > COLOUR_SATURATION) & (value > 60)
    found = int(coloured.sum())
    certain = not minimum / COLOUR_UNCERTAIN <= found <= minimum * COLOUR_UNCERTAIN

    if found >= minimum:
        # Hue is circular, so average it as angles rather than as numbers.
        angles = hue[coloured].astype(np.float32) * (2 * np.pi / 180.0)
        mean_hue = (
            np.degrees(np.arctan2(np.sin(angles).mean(), np.cos(angles).mean())) / 2.0
        ) % 180.0
        return (GREEN if 25.0 <= mean_hue <= 95.0 else RED), certain

    # Nothing saturated: either black print, or an empty slot.
    dark = value < float(value.mean()) * 0.78
    if int(dark.sum()) >= minimum:
        return BLACK, certain
    return None, True


def ink_colour(patch: np.ndarray) -> int | None:
    """The colour the glyph is printed in, or ``None`` if the crop is blank."""
    return ink_reading(patch)[0]


def candidates_for_colour(colour: int | None) -> tuple[int, ...]:
    if colour is None:
        return _ALL_CARDS
    if colour == BLACK:
        return tuple(c for c in _ALL_CARDS if c != FLOWER and colour_of(c) == BLACK)
    # The flower is drawn in red and green at once, so whichever of the two
    # wins the hue average, it has to stay on the shortlist.
    return tuple(c for c in _ALL_CARDS if c == FLOWER or colour_of(c) == colour)


def match(
    patch: np.ndarray,
    bank: TemplateBank,
    colour: int | None = None,
    colour_certain: bool = True,
) -> Guess | None:
    """Best template for ``patch``, restricted to one ink colour if known.

    Every template is scored, but the winner and its margin are decided within
    the ink colour's shortlist as they always were.  The full ranking rides
    along on the :class:`Guess` for the benefit of the resolver, which needs
    somewhere to look when the winner turns out not to fit the deck.
    """
    if not bank.templates:
        raise ValueError("the template bank is empty; run the calibrate command first")

    target = standardise(patch)
    pixels = float(target.size)
    scores = sorted(
        (
            (float((target * template).sum() / pixels), card)
            for card, template in bank.templates.items()
        ),
        reverse=True,
    )
    if not scores:
        return None
    ranking = tuple((card, score) for score, card in scores)

    allowed = set(candidates_for_colour(colour))
    shortlist = [(score, card) for score, card in scores if card in allowed]
    if not shortlist:
        # The colour reading disagrees with the bank; fall back to everything.
        shortlist = scores

    best_score, best_card = shortlist[0]
    runner_up = shortlist[1][0] if len(shortlist) > 1 else -1.0
    return Guess(
        card=best_card,
        confidence=best_score,
        margin=best_score - runner_up,
        colour=colour,
        ranking=ranking,
        colour_certain=colour_certain,
    )


def classify(patch: np.ndarray, bank: TemplateBank) -> Guess | None:
    """Read one card crop.  ``None`` means the crop is blank -- an empty slot."""
    colour, certain = ink_reading(patch)
    if colour is None:
        return None
    return match(patch, bank, colour, certain)
