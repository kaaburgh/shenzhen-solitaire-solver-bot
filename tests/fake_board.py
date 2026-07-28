"""A synthetic board renderer.

The real card art cannot be shipped here, but the *structure* of the screen
can be reproduced: one grid of eight slots, free cells in slots 0-2, the three
dragon buttons in slot 3, the flower in slot 4, foundations in slots 5-7, the
tableau below, cards stacked at a constant offset with the identity printed in
the top-left corner, and a patterned back on a cell locked by collapsed
dragons.

Two details matter to the pipeline and so are reproduced deliberately: cards
are drawn with a top-to-bottom brightness gradient, which is what makes the
boundary between two overlapping cards findable at all, and the dragon
buttons are drawn in the game's tan, which is what anchors the grid.

This complements the screenshot fixtures rather than replacing them: it can
produce positions no screenshot happens to show -- an empty tableau, three
locked cells -- but only real screenshots can say whether the thresholds suit
the real artwork.
"""

from __future__ import annotations

import cv2
import numpy as np

from shenzhen.cards import (
    BLACK,
    FLOWER,
    GREEN,
    RED,
    dragon_colour,
    is_dragon,
    is_flower,
    is_locked,
    suit_of,
)
from shenzhen.game import State

CARD_W, CARD_H = 190, 363  # aspect 1.91, as the game draws it
PITCH = 238  # 1.25 card widths between slots
OFFSET = 46  # vertical step between two stacked cards
MARGIN_X, TOP_Y, TABLEAU_Y = 95, 60, 520

FELT = (58, 84, 46)  # BGR, a saturated dark green
FACE_TOP = (238, 244, 246)  # cards are lighter at the top ...
FACE_BOTTOM = (206, 214, 219)  # ... and darker at the bottom
# Light, because the game outlines a card in a hairline rather than in black.
# It is the seam between two stacked cards, and how far it steps the brightness
# up -- some tens of levels, not the two hundred a black outline would give --
# is what the column splitter has to tell from the strokes of a glyph.  Drawn
# too strongly, a splitter that could not tell them apart would still pass.
BORDER = (190, 190, 190)
BUTTON = (120, 190, 226)  # the tan of the dragon buttons
BACK_LIGHT = (232, 240, 235)
# Muted enough to still read as a card rather than as felt, which is how the
# game draws the back too.
BACK_GREEN = (120, 165, 130)

INK = {GREEN: (40, 130, 40), RED: (40, 40, 190), BLACK: (35, 35, 35)}

BUTTON_SLOT, FLOWER_SLOT, FIRST_FOUNDATION_SLOT = 3, 4, 5


def _marks() -> dict[int, int]:
    """One 4x4 bit pattern per card face, all far apart from each other.

    A digit font is the wrong stand-in for the game's glyphs: the shapes it
    gives a 4 and a 9 differ in a corner, so matching them measures the font
    rather than the pipeline.  The real glyphs are nothing like each other, and
    these patterns reproduce that by construction -- any two differ in at least
    six of their sixteen cells.
    """
    chosen: list[int] = []
    for value in range(1, 1 << 16):
        if bin(value).count("1") not in (6, 7, 8, 9, 10):
            continue
        if all(bin(value ^ other).count("1") >= 6 for other in chosen):
            chosen.append(value)
        if len(chosen) == 31:
            break
    assert len(chosen) == 31, "not enough well-separated patterns"
    return {card: chosen[index] for index, card in enumerate(FULL_DECK_FACES)}


FULL_DECK_FACES: tuple[int, ...] = tuple(range(27)) + (27, 28, 29, FLOWER)
MARKS = _marks()


def slot_x(slot: int) -> int:
    return MARGIN_X + slot * PITCH


def draw_mark(canvas: np.ndarray, card: int, x: int, y: int) -> None:
    """Print a card's identity in the corner strip.

    Kept as narrow as the game's own glyph: a mark covering more than half the
    column's width would step its median brightness up the way a card boundary
    does, and the column splitter would cut cards in half at every glyph.
    """
    ink = _ink(card)
    left = x + int(CARD_W * 0.05)
    top = y + int(OFFSET * 0.12)
    cell_w = max(2, int(CARD_W * 0.028))
    cell_h = int(OFFSET * 0.19)

    bits = MARKS[card]
    for index in range(16):
        if not bits >> index & 1:
            continue
        row, col = divmod(index, 4)
        x0 = left + col * cell_w
        y0 = top + row * cell_h
        cv2.rectangle(canvas, (x0, y0), (x0 + cell_w - 2, y0 + cell_h - 2), ink, -1)


def _ink(card: int) -> tuple[int, int, int]:
    if is_flower(card):
        return INK[RED]
    if is_dragon(card):
        return INK[dragon_colour(card)]
    return INK[suit_of(card)]


def _gradient(height: int) -> np.ndarray:
    """A card's vertical shading, as a (height, 1, 3) image."""
    ramp = np.linspace(0.0, 1.0, max(height, 1), dtype=np.float32)[:, None]
    top = np.array(FACE_TOP, dtype=np.float32)
    bottom = np.array(FACE_BOTTOM, dtype=np.float32)
    return (top * (1 - ramp) + bottom * ramp)[:, None, :]


def draw_card(canvas: np.ndarray, card: int, x: int, y: int, height: int = CARD_H) -> None:
    """Draw a card, or the part of one that is not covered by the next.

    Each card gets the full-height gradient and is then clipped, so a buried
    card shows the *top* of the ramp -- exactly as the game draws it, and the
    reason a boundary reads as a step up in brightness.
    """
    full = np.repeat(_gradient(CARD_H), CARD_W, axis=1)
    canvas[y : y + height, x : x + CARD_W] = full[:height].astype(np.uint8)
    cv2.rectangle(canvas, (x, y), (x + CARD_W - 1, y + height - 1), BORDER, 1)
    draw_mark(canvas, card, x, y)


def draw_back(canvas: np.ndarray, x: int, y: int) -> None:
    """The patterned back shown by a free cell holding collapsed dragons."""
    tile = 12
    for row in range(0, CARD_H, tile):
        for col in range(0, CARD_W, tile):
            colour = BACK_GREEN if (row // tile + col // tile) % 2 == 0 else BACK_LIGHT
            canvas[y + row : y + min(row + tile, CARD_H), x + col : x + min(col + tile, CARD_W)] = colour
    cv2.rectangle(canvas, (x, y), (x + CARD_W - 1, y + CARD_H - 1), BUTTON, 3)


def draw_buttons(canvas: np.ndarray) -> None:
    """The three dragon buttons, in slot 3.  Always drawn, whatever the state."""
    x = slot_x(BUTTON_SLOT) + 20
    width, height = int(CARD_W * 0.70), int(CARD_W * 0.50)
    for index in range(3):
        y = TOP_Y + index * (height + 12)
        cv2.rectangle(canvas, (x, y), (x + width, y + height), BUTTON, -1)


def render(state: State, width: int = 2050, height: int = 1330) -> np.ndarray:
    """Render a position the way the game lays it out."""
    canvas = np.full((height, width, 3), FELT, dtype=np.uint8)
    draw_buttons(canvas)

    for index, cell in enumerate(state.free):
        if cell is None:
            continue
        x = slot_x(index)
        if is_locked(cell):
            draw_back(canvas, x, TOP_Y)
        else:
            draw_card(canvas, cell, x, TOP_Y)

    if state.flower:
        draw_card(canvas, FLOWER, slot_x(FLOWER_SLOT), TOP_Y)

    slot = FIRST_FOUNDATION_SLOT
    for suit, top in enumerate(state.foundations):
        if top == 0:
            continue
        draw_card(canvas, suit * 9 + top - 1, slot_x(slot), TOP_Y)
        slot += 1

    for index, column in enumerate(state.columns):
        x = slot_x(index)
        for depth, card in enumerate(column):
            y = TABLEAU_Y + depth * OFFSET
            visible = OFFSET if depth < len(column) - 1 else CARD_H
            draw_card(canvas, card, x, y, height=min(visible, height - y))

    return canvas


def encode(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return buffer.tobytes()
