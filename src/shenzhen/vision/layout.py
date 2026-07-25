"""Finding the cards in a screenshot.

The game draws every card at the same size and stacks the tableau with a
constant vertical offset, so the geometry can be recovered from the picture
itself rather than hard-coded per resolution:

1. threshold the image -- card faces are far brighter than the felt;
2. take the connected components and read the card width off them;
3. split the top row (free cells, flower, foundations) from the tableau by
   the vertical gap between them;
4. group the tableau boxes into eight columns by x, and cut each column into
   individual cards along the dark seams between overlapping cards.

Everything is expressed in fractions of the detected card width, so the same
code works at 1080p and at 4K.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

NUM_COLUMNS = 8
NUM_FREE_CELLS = 3


@dataclass(frozen=True)
class Box:
    """A rectangle in image coordinates."""

    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)


@dataclass
class LayoutConfig:
    """Tunables.  All lengths are fractions of the detected card width."""

    #: components smaller than this fraction of the frame are noise
    min_area_ratio: float = 0.0004
    #: a full card is this tall relative to its width
    card_aspect: float = 1.4
    #: rows covered less than this fraction of the column width are a seam
    seam_coverage: float = 0.55
    #: the glyph in a card's top-left corner, as fractions of the card width
    corner_x: float = 0.05
    corner_y: float = 0.04
    corner_w: float = 0.36
    corner_h: float = 0.36
    #: a stacked card must expose at least this much of itself to be counted
    min_exposed: float = 0.12


@dataclass
class BoardLayout:
    card_w: int
    card_h: int
    offset: int
    free_cells: list[Box | None] = field(default_factory=list)
    flower: Box | None = None
    foundations: list[Box | None] = field(default_factory=list)
    columns: list[list[Box]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class LayoutError(RuntimeError):
    """The screenshot does not look like a Shenzhen Solitaire board."""


def card_mask(image: np.ndarray) -> np.ndarray:
    """A binary mask of the card faces.

    Cards are bright and nearly unsaturated; the felt behind them is neither.
    Combining brightness with low saturation keeps coloured UI chrome out of
    the mask.
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    _, bright = cv2.threshold(value, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    pale = cv2.inRange(saturation, 0, 90)
    mask = cv2.bitwise_and(bright, pale)

    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    return mask


def _components(mask: np.ndarray, min_area: float) -> list[Box]:
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
    boxes = []
    for i in range(1, count):  # 0 is the background
        x, y, w, h, area = stats[i]
        if area < min_area:
            continue
        boxes.append(Box(int(x), int(y), int(w), int(h)))
    return boxes


def _estimate_card_width(boxes: list[Box]) -> int:
    """All cards share one width, so it is the dominant width in the picture."""
    if not boxes:
        raise LayoutError("no card-like shapes found in the screenshot")
    widths = np.array([b.w for b in boxes])
    # Cluster widths to within 5% of each other and take the biggest cluster.
    order = np.sort(widths)
    best_count, best_value = 0, int(order[len(order) // 2])
    for value in order:
        near = order[(order >= value * 0.93) & (order <= value * 1.07)]
        if len(near) > best_count:
            best_count, best_value = len(near), int(np.median(near))
    return best_value


def _split_rows(values: list[float], min_gap: float) -> tuple[list[int], list[int]]:
    """Tell the row of slots along the top of the screen from the tableau.

    Every tableau column starts at the same y whatever it holds, so the two
    rows are separated by a gap of at least a card's height.  A fresh deal has
    nothing in the top row at all, and then there is no gap to find and
    everything below is tableau -- hence the ``min_gap`` floor rather than
    just splitting at the largest gap.
    """
    if not values:
        return [], []
    order = sorted(range(len(values)), key=lambda i: values[i])
    gaps = [
        (values[order[i + 1]] - values[order[i]], i)
        for i in range(len(order) - 1)
    ]
    if not gaps:
        return [], order
    biggest, at = max(gaps)
    if biggest < min_gap:
        return [], order
    return order[: at + 1], order[at + 1 :]


def _group_by_x(boxes: list[Box], tolerance: float) -> list[list[Box]]:
    """Group boxes into columns by their centre x."""
    groups: list[list[Box]] = []
    for box in sorted(boxes, key=lambda b: b.cx):
        if groups and abs(box.cx - groups[-1][0].cx) <= tolerance:
            groups[-1].append(box)
        else:
            groups.append([box])
    for group in groups:
        group.sort(key=lambda b: b.y)
    return groups


def _seams(mask: np.ndarray, box: Box, config: LayoutConfig) -> list[int]:
    """Rows inside ``box`` where the card face is interrupted -- the border
    between two overlapping cards."""
    region = mask[box.y : box.bottom, box.x : box.x + box.w]
    if region.size == 0:
        return []
    coverage = region.mean(axis=1) / 255.0
    threshold = config.seam_coverage * float(coverage.max() or 1.0)

    seams: list[int] = []
    run_start: int | None = None
    for row, value in enumerate(coverage):
        if value < threshold:
            if run_start is None:
                run_start = row
        elif run_start is not None:
            seams.append((run_start + row) // 2)
            run_start = None
    if run_start is not None and run_start > 0:
        seams.append((run_start + len(coverage)) // 2)
    return seams


def _split_column(
    mask: np.ndarray, box: Box, card_w: int, card_h: int, offset: int, config: LayoutConfig
) -> list[Box]:
    """Cut one column blob into the individual cards stacked in it."""
    seams = _seams(mask, box, config)
    tops = [0] + [s for s in seams if s > 0]

    if len(tops) == 1 and box.h > card_h * 1.25 and offset > 0:
        # No seam survived thresholding -- fall back to the constant stacking
        # offset the game uses.
        count = max(1, round((box.h - card_h) / offset) + 1)
        tops = [round(i * (box.h - card_h) / max(count - 1, 1)) for i in range(count)]

    tops = sorted(set(tops))
    min_exposed = max(2, int(config.min_exposed * card_w))
    cards: list[Box] = []
    for i, top in enumerate(tops):
        end = tops[i + 1] if i + 1 < len(tops) else box.h
        if end - top < min_exposed and i + 1 < len(tops):
            continue
        cards.append(Box(box.x, box.y + top, box.w, end - top))
    return cards or [box]


def detect_layout(image: np.ndarray, config: LayoutConfig | None = None) -> BoardLayout:
    """Locate every visible card in a screenshot of the board."""
    config = config or LayoutConfig()
    if image.ndim != 3:
        raise LayoutError("expected a colour image")

    height, width = image.shape[:2]
    mask = card_mask(image)
    boxes = _components(mask, min_area=config.min_area_ratio * height * width)
    if len(boxes) < NUM_COLUMNS:
        raise LayoutError(
            f"found only {len(boxes)} card-like shapes; is this a screenshot of the board?"
        )

    card_w = _estimate_card_width(boxes)
    boxes = [b for b in boxes if 0.85 * card_w <= b.w <= 1.15 * card_w]

    full_heights = [b.h for b in boxes if card_w * 1.15 <= b.h <= card_w * 1.9]
    card_h = int(np.median(full_heights)) if full_heights else int(card_w * config.card_aspect)

    top_indices, tableau_indices = _split_rows([b.y for b in boxes], min_gap=card_h * 0.5)
    top_boxes = [boxes[i] for i in top_indices]
    tableau_boxes = [boxes[i] for i in tableau_indices]

    warnings: list[str] = []
    columns_raw = _group_by_x(tableau_boxes, tolerance=card_w * 0.45)
    if len(columns_raw) != NUM_COLUMNS:
        warnings.append(
            f"expected {NUM_COLUMNS} tableau columns, found {len(columns_raw)}"
        )

    offset = _estimate_offset(mask, columns_raw, card_w, card_h, config)

    columns: list[list[Box]] = []
    for group in columns_raw:
        cards: list[Box] = []
        for blob in group:
            cards.extend(_split_column(mask, blob, card_w, card_h, offset, config))
        columns.append(cards)

    free_cells, flower, foundations = _split_top_row(top_boxes, card_w, width, warnings)

    return BoardLayout(
        card_w=card_w,
        card_h=card_h,
        offset=offset,
        free_cells=free_cells,
        flower=flower,
        foundations=foundations,
        columns=columns,
        warnings=warnings,
    )


def _estimate_offset(
    mask: np.ndarray,
    columns: list[list[Box]],
    card_w: int,
    card_h: int,
    config: LayoutConfig,
) -> int:
    """The constant vertical step between two stacked cards.

    Depending on how dark the border between two overlapping cards comes out,
    thresholding either merges a column into one blob (and the step has to be
    read off the seams inside it) or splits it into one blob per card (and the
    step is the distance between consecutive blobs).  Both are measured.
    """
    gaps: list[int] = []
    for group in columns:
        for blob in group:
            seams = _seams(mask, blob, config)
            positions = [0] + [s for s in seams if s > 0]
            gaps.extend(
                positions[i + 1] - positions[i] for i in range(len(positions) - 1)
            )
        gaps.extend(group[i + 1].y - group[i].y for i in range(len(group) - 1))
    plausible = [g for g in gaps if 0.1 * card_w <= g <= 1.1 * card_h]
    if plausible:
        return int(np.median(plausible))
    return max(1, int(card_h * 0.35))


def _split_top_row(
    boxes: list[Box], card_w: int, width: int, warnings: list[str]
) -> tuple[list[Box | None], Box | None, list[Box | None]]:
    """Assign the slots along the top of the screen.

    The board puts three free cells on the left, the flower slot in the
    middle and three foundations on the right, so position across the frame
    is enough to tell them apart.
    """
    free_cells: list[Box | None] = [None] * NUM_FREE_CELLS
    foundations: list[Box | None] = [None] * 3
    flower: Box | None = None

    left = sorted([b for b in boxes if b.cx < width * 0.36], key=lambda b: b.cx)
    middle = [b for b in boxes if width * 0.36 <= b.cx <= width * 0.64]
    right = sorted([b for b in boxes if b.cx > width * 0.64], key=lambda b: b.cx)

    for i, box in enumerate(left[:NUM_FREE_CELLS]):
        free_cells[i] = box
    if len(left) > NUM_FREE_CELLS:
        warnings.append(f"{len(left)} shapes on the left, expected at most {NUM_FREE_CELLS}")

    if middle:
        flower = middle[0]
    if len(middle) > 1:
        warnings.append(f"{len(middle)} shapes around the flower slot, expected at most 1")

    # Foundations sit right to left in suit order on screen; keep them in x
    # order and let the classifier work out which suit each one holds.
    for i, box in enumerate(right[:3]):
        foundations[i] = box
    if len(right) > 3:
        warnings.append(f"{len(right)} shapes on the right, expected at most 3")

    return free_cells, flower, foundations


def corner_patch(
    image: np.ndarray,
    box: Box,
    card_w: int,
    config: LayoutConfig,
    max_h: int | None = None,
) -> np.ndarray:
    """Crop the rank/suit glyph from a card's top-left corner.

    Only the top strip of a stacked card is visible, and that strip is exactly
    where the game prints the card's identity.

    ``max_h`` caps the crop at the stacking offset so that a card buried under
    another one and the same card sitting fully visible in a free cell produce
    the same crop -- otherwise every template would have to exist twice.
    """
    x0 = int(box.x + config.corner_x * card_w)
    y0 = int(box.y + config.corner_y * card_w)
    x1 = int(x0 + config.corner_w * card_w)
    height = config.corner_h * card_w
    if max_h is not None:
        height = min(height, max(max_h - config.corner_y * card_w, 1))
    y1 = int(y0 + height)

    x0, y0 = max(x0, 0), max(y0, 0)
    x1 = min(x1, image.shape[1], box.x + box.w)
    y1 = min(y1, image.shape[0], box.bottom)
    if x1 <= x0 or y1 <= y0:
        raise LayoutError(f"card at {box.as_tuple()} is too small to read")
    return image[y0:y1, x0:x1]


def annotate(image: np.ndarray, layout: BoardLayout) -> np.ndarray:
    """Draw the detected geometry onto a copy of the screenshot.

    This is the tool for tuning :class:`LayoutConfig` against a real
    screenshot -- run the ``detect`` command and look at what it boxed.
    """
    out = image.copy()
    for index, group in enumerate(layout.columns):
        for depth, box in enumerate(group):
            cv2.rectangle(out, (box.x, box.y), (box.x + box.w, box.bottom), (0, 200, 255), 2)
            cv2.putText(
                out,
                f"{index + 1}.{depth + 1}",
                (box.x + 3, box.y + 18),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 0, 255),
                1,
                cv2.LINE_AA,
            )
    for name, box in (
        *[(f"free{i + 1}", b) for i, b in enumerate(layout.free_cells)],
        ("flower", layout.flower),
        *[(f"found{i + 1}", b) for i, b in enumerate(layout.foundations)],
    ):
        if box is None:
            continue
        cv2.rectangle(out, (box.x, box.y), (box.x + box.w, box.bottom), (0, 255, 0), 2)
        cv2.putText(
            out, name, (box.x + 3, box.y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
            (0, 128, 0), 1, cv2.LINE_AA,
        )
    return out
