"""A picture of the one card being asked about.

Asking "column 6, the bottom card -- is that the white dragon?" is a question
the user can only answer by going back to the game, or by squinting at a
thumbnail of the screenshot they sent a moment ago.  Cutting the slot out of
that screenshot and sending it alongside the question turns it into something
answerable in the chat, at a glance.

Three things make the crop worth looking at:

* **context.** A card on its own is unmoored -- the point of the question is
  partly *which* slot it is, so the crop keeps most of a card width of board
  around it and the neighbours that come with it.
* **a mark.** Which card in that crop is the one being asked about has to be
  unambiguous, so the slot is outlined in a colour the game does not use.
* **size.** The screenshots that produce questions are exactly the ones that
  came in small, so the crop is blown up until the card is comfortably
  readable rather than handed over at the size that caused the problem.

Cut from the picture as it arrived rather than from the enlarged copy the
reader worked on: the enlargement exists to help template matching land on the
right pixels, and it is not what a human wants to look at.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .layout import Box

#: board kept around the slot, in card widths of it
CONTEXT = 0.55

#: and the crop is grown to at least this tall, in card widths, so that a card
#: buried in a stack -- of which only a narrow strip is visible -- still comes
#: with enough around it to place
MIN_HEIGHT = 1.25

#: blow the crop up until the card is at least this wide
VIEW_CARD_W = 260

#: ceiling on the enlarged crop, so a nonsense card width cannot ask for a
#: nonsense amount of memory
MAX_VIEW_PIXELS = 4_000_000

#: BGR magenta.  The board is green felt, cream cards and green/red/black ink;
#: nothing on it is this colour, so the outline cannot be mistaken for part of
#: the game.
MARK = (255, 0, 255)

JPEG_QUALITY = 90


def card_crop(image: np.ndarray, box: Box, card_w: int) -> bytes | None:
    """The slot at ``box``, with its surroundings, outlined and enlarged.

    ``image`` and ``box`` are both in the coordinates of the picture the user
    sent.  Comes back as encoded JPEG ready to hand to Telegram, or ``None``
    if the box does not land on the picture at all -- a crop is a nicety, and
    nothing about it is worth failing a question over.
    """
    if image is None or image.size == 0 or card_w <= 0:
        return None

    height, width = image.shape[:2]
    pad = max(1, round(CONTEXT * card_w))
    x0, x1 = box.x - pad, box.x + box.w + pad
    y0, y1 = box.y - pad, box.y + box.h + pad

    wanted = round(MIN_HEIGHT * card_w)
    if y1 - y0 < wanted:
        grow = (wanted - (y1 - y0)) // 2
        y0, y1 = y0 - grow, y1 + grow

    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, width), min(y1, height)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None

    view = image[y0:y1, x0:x1]
    scale = _view_scale(card_w, view.shape[0], view.shape[1])
    if scale > 1:
        # Cubic here, unlike the reader's own enlargement: overshoot sharpens
        # edges, which is wrong for template matching and right for eyes.
        view = cv2.resize(view, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    else:
        view = view.copy()

    cv2.rectangle(
        view,
        ((box.x - x0) * scale, (box.y - y0) * scale),
        ((box.x + box.w - x0) * scale, (box.y + box.h - y0) * scale),
        MARK,
        max(2, round(card_w * scale / 40)),
    )

    ok, buffer = cv2.imencode(".jpg", view, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return buffer.tobytes() if ok else None


def _view_scale(card_w: int, height: int, width: int) -> int:
    """Whole-number enlargement that gets the card to a readable width."""
    scale = max(1, math.ceil(VIEW_CARD_W / card_w))
    while scale > 1 and scale * scale * height * width > MAX_VIEW_PIXELS:
        scale -= 1
    return scale


__all__ = ["CONTEXT", "MARK", "VIEW_CARD_W", "card_crop"]
