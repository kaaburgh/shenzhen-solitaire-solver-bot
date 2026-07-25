"""A synthetic board renderer.

The real card art cannot be shipped here, but the *geometry* of the screen can
be reproduced exactly: one card size, a constant stacking offset, a row of
slots along the top, eight columns below, and the card's identity printed in
its top-left corner in the suit's ink colour.

That is everything the layout and classification passes rely on, so rendering
a known board and reading it back is a real test of the pipeline.  It does not
prove the thresholds are right for the actual game -- only screenshots can do
that.
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
    locked_colour,
    make_dragon,
    rank_of,
    suit_of,
)
from shenzhen.game import State

CARD_W, CARD_H = 120, 168
OFFSET = 42
MARGIN_X, TABLEAU_Y, TOP_Y = 60, 300, 40
COLUMN_STEP = 150

FELT = (58, 74, 46)  # BGR, a dark green
FACE = (222, 232, 238)  # cream
BORDER = (40, 40, 40)

INK = {
    GREEN: (40, 130, 40),
    RED: (40, 40, 190),
    BLACK: (35, 35, 35),
}


def _glyph(card: int) -> str:
    if is_flower(card):
        return "F"
    if is_dragon(card):
        return "D"
    return str(rank_of(card))


def _ink(card: int) -> tuple[int, int, int]:
    if is_flower(card):
        return INK[RED]
    if is_dragon(card):
        return INK[dragon_colour(card)]
    return INK[suit_of(card)]


def draw_card(canvas: np.ndarray, card: int, x: int, y: int, height: int = CARD_H) -> None:
    cv2.rectangle(canvas, (x, y), (x + CARD_W, y + height), FACE, -1)
    cv2.rectangle(canvas, (x, y), (x + CARD_W, y + height), BORDER, 2)

    # The identity goes in the top-left corner, inside the strip that stays
    # visible when another card is laid on top.
    cv2.putText(
        canvas,
        _glyph(card),
        (x + 8, y + 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.3,
        _ink(card),
        3,
        cv2.LINE_AA,
    )


def render(state: State, width: int = 1420, height: int = 900) -> np.ndarray:
    """Render a position the way the game lays it out."""
    canvas = np.full((height, width, 3), FELT, dtype=np.uint8)

    for index, cell in enumerate(state.free):
        if cell is None:
            continue
        x = MARGIN_X + index * COLUMN_STEP
        card = make_dragon(locked_colour(cell)) if is_locked(cell) else cell
        draw_card(canvas, card, x, TOP_Y)

    if state.flower:
        draw_card(canvas, FLOWER, width // 2 - CARD_W // 2, TOP_Y)

    slot = 0
    for suit, top in enumerate(state.foundations):
        if top == 0:
            continue
        x = width - MARGIN_X - (3 - slot) * COLUMN_STEP
        draw_card(canvas, suit * 9 + top - 1, x, TOP_Y)
        slot += 1

    for index, column in enumerate(state.columns):
        x = MARGIN_X + index * COLUMN_STEP
        for depth, card in enumerate(column):
            y = TABLEAU_Y + depth * OFFSET
            # Every card but the last is clipped by the one below it.
            visible = OFFSET if depth < len(column) - 1 else CARD_H
            draw_card(canvas, card, x, y, height=max(visible, OFFSET))

    return canvas


def encode(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return buffer.tobytes()
