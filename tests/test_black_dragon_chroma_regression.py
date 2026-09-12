"""Regression samples for black-dragon JPEG chroma fringing.

The saturated BGR values below are the exact pixels that current ``ink_reading``
counts as coloured in two Telegram-recompressed black-dragon crops. Rebuilding
the 57x99 patch from that measured fringe keeps the regression small and
self-contained while preserving the failure's colour statistics.
"""

import numpy as np
import pytest

from shenzhen.cards import BLACK
from shenzhen.vision.classify import ink_reading

# (B, G, R, count) for every pixel currently admitted by the saturation mask.
FRINGE_PIXELS = [
    [
        (47, 61, 60, 4), (47, 61, 63, 1), (48, 61, 62, 1), (48, 62, 61, 2),
        (49, 63, 62, 1), (50, 63, 65, 1), (50, 64, 63, 3), (51, 65, 64, 1),
        (52, 65, 66, 1), (52, 66, 65, 2), (53, 66, 68, 1), (54, 68, 67, 5),
        (55, 68, 69, 1), (55, 68, 70, 1), (55, 69, 68, 2), (55, 69, 71, 1),
        (56, 69, 70, 1), (56, 70, 69, 4), (57, 69, 72, 1), (58, 71, 74, 1),
        (58, 72, 75, 1), (59, 72, 75, 2), (62, 75, 78, 1), (74, 88, 93, 1),
        (76, 90, 95, 1),
    ],
    [
        (47, 61, 60, 2), (47, 65, 64, 1), (48, 62, 61, 2), (48, 63, 62, 1),
        (48, 66, 65, 3), (49, 63, 62, 3), (50, 65, 64, 2), (51, 65, 64, 3),
        (51, 68, 67, 1), (52, 66, 65, 11), (53, 67, 66, 1), (53, 71, 70, 2),
        (54, 68, 67, 7), (54, 71, 70, 2), (55, 69, 68, 8), (55, 72, 71, 1),
        (56, 70, 69, 3), (56, 72, 71, 1), (60, 77, 76, 1), (61, 79, 78, 1),
        (64, 81, 80, 1),
    ],
]


def _measured_black_dragon_patch(fringe: list[tuple[int, int, int, int]]) -> np.ndarray:
    patch = np.full((57, 99, 3), (182, 196, 195), dtype=np.uint8)
    inner = np.full((53 * 91, 3), (182, 196, 195), dtype=np.uint8)
    inner[:520] = (60, 60, 60)  # black dragon ink, comfortably above dark threshold
    offset = 520
    for blue, green, red, count in fringe:
        inner[offset : offset + count] = (blue, green, red)
        offset += count
    patch[2:-2, 4:-4] = inner.reshape(53, 91, 3)
    return patch


@pytest.mark.parametrize("fringe", FRINGE_PIXELS)
def test_black_dragon_chroma_fringe_reads_as_black(fringe) -> None:
    """Low-chroma JPEG fringe around black ink must not make the card green."""
    patch = _measured_black_dragon_patch(fringe)
    assert ink_reading(patch) == (BLACK, True)
