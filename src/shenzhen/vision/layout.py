"""Finding the cards in a screenshot.

The board is laid out on a single horizontal grid of eight slots. The tableau
columns occupy slots 0-7; along the top, slots 0-2 hold the free cells, slot 3
is where the three dragon buttons sit, slot 4 holds the flower and slots 5-7
the foundations.  Recovering that grid is most of the work:

1. threshold the image -- card faces are far brighter and far less saturated
   than the felt -- and take the connected components;
2. read the card width off them (every card is drawn the same size);
3. cut out what the phone drew over the game, which is anything wider than a
   card, and join back up the columns it was lying across;
4. locate the dragon buttons by their tan colour.  They are always drawn, they
   never move, and they anchor the grid to slot 3, which is what makes the
   assignment work when the leftmost slots happen to be empty;
5. split the top row from the tableau by the vertical gap between them;
6. cut each tableau column into individual cards.

That last step needs care.  Overlapping cards do not separate into distinct
components -- a column comes out as one tall blob -- and the seam between two
of them is not dark enough to find by thresholding.  What does show up is
brightness: each card is drawn with a top-to-bottom gradient, so where one
card ends and the next begins there is a sharp *step up* in row brightness.
A seam steps up right across the width of the card, which is what tells it
from the edges of the glyphs printed on the card; the steps that remain land
on a regular lattice, and fitting the lattice is what says where the column
stops.

Everything is expressed in fractions of the measured card width, so the same
code works at any resolution.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

NUM_COLUMNS = 8
NUM_FREE_CELLS = 3

#: grid slot the dragon buttons sit in
BUTTON_SLOT = 3
#: grid slot the flower sits in
FLOWER_SLOT = 4
#: grid slot the first foundation sits in
FIRST_FOUNDATION_SLOT = 5


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
    """Tunables.  Lengths are fractions of the measured card width unless the
    name says otherwise."""

    #: components smaller than this fraction of the frame are noise
    min_area_ratio: float = 0.0004
    #: a full card is this tall relative to its width
    card_aspect: float = 1.91
    #: horizontal distance between two grid slots
    slot_pitch: float = 1.25

    # -- dragon buttons ----------------------------------------------------
    #: hue range of the buttons' tan face, in OpenCV's 0..179 scale
    tan_hue: tuple[int, int] = (8, 32)
    #: button size, as a fraction of the card width
    button_w: tuple[float, float] = (0.55, 0.90)
    button_h: tuple[float, float] = (0.33, 0.70)
    #: where the buttons sit relative to slot 3's left edge, in slot pitches.
    #: Only used to pick the nearest slot, so it has half a slot of slack.
    button_offset: float = 0.27

    # -- what the phone draws over the game ---------------------------------
    #: a horizontal run at least this many card widths long is not the board.
    #: Nothing the game draws is wider than one card, and neighbouring slots
    #: are a quarter of a card apart, so there is a clear band between the two
    #: for this to sit in.
    overlay_width: float = 1.30
    #: how much of a column's width an erased overlay has to span before the
    #: pieces either side of it count as one column
    overlay_cover: float = 0.50

    # -- splitting a column into cards -------------------------------------
    #: ignore this much of a column's width at each side when profiling
    profile_inset: float = 0.08
    #: how far the width of a column has to step up in brightness, in grey
    #: levels, before the row counts as a card boundary.  The one number here
    #: that is not a length, and so the one that does not scale: the game
    #: draws the seam the same way whatever size it draws the board at, and it
    #: measures 13-26 levels across the real screenshots against 2 or so for a
    #: glyph.  Anywhere in 4.5-7.5 reads every fixture right, at every squeeze
    #: they survive at all.
    min_step: float = 6.0
    #: how far a step may sit from where the lattice expects it
    jump_tolerance: float = 0.30
    #: the stacking offset the game uses, as a fraction of the card width.
    #: Measured at 0.243 on two different board scales; the game does not
    #: compress it for long columns, it just lets them run off the screen.
    nominal_offset: float = 0.243
    #: how far the measured offset may stray from the nominal one
    offset_range: tuple[float, float] = (0.80, 1.30)

    # -- card faces --------------------------------------------------------
    #: the glyph strip, as a fraction of the card width.  Taken from the card
    #: width rather than from the measured stacking offset so that a board
    #: with nothing stacked on it -- where there is no offset to measure --
    #: still crops its cards exactly like the templates were cropped.
    corner_x: float = 0.02
    corner_w: float = 0.42
    corner_h: float = 0.24
    #: a free cell holding four collapsed dragons shows a patterned back;
    #: this much of it reads as green, against almost none of a card face
    back_green_fraction: float = 0.25


@dataclass
class BoardLayout:
    card_w: int
    card_h: int
    offset: int
    free_cells: list[Box | None] = field(default_factory=lambda: [None] * NUM_FREE_CELLS)
    locked_cells: list[bool] = field(default_factory=lambda: [False] * NUM_FREE_CELLS)
    flower: Box | None = None
    foundations: list[Box | None] = field(default_factory=lambda: [None] * 3)
    columns: list[list[Box]] = field(default_factory=lambda: [[] for _ in range(NUM_COLUMNS)])
    warnings: list[str] = field(default_factory=list)


class LayoutError(RuntimeError):
    """The screenshot does not look like a Shenzhen Solitaire board."""


# --- masks -----------------------------------------------------------------


def card_mask(image: np.ndarray) -> np.ndarray:
    """A binary mask of the card faces.

    Card faces are bright and nearly neutral.  The felt is a saturated green
    and the UI chrome is dark, so brightness and low saturation together pick
    out the cards and nothing else.
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


def find_overlays(mask: np.ndarray, card_w: int, config: LayoutConfig) -> np.ndarray:
    """What the phone drew on top of the game, as a mask of its own.

    A screenshot is of the whole screen, and the phone has furniture of its
    own on it: on an iPhone a pale home indicator lies across the bottom,
    right over whichever column happens to run that far down.  It is pale and
    bright, so the card mask takes it for a card face -- and because it
    touches the column, it is not a stray blob that could simply be ignored.
    It fuses with the column into one component three cards wide, which is
    then thrown out as not card-shaped, taking the column with it.  That is a
    whole column missing from the reading rather than a card read wrong, and
    the deck check cannot recover from it.

    What separates the two is length.  Nothing the game draws is wider than a
    card, and the columns are set a quarter of a card apart, so a horizontal
    run longer than one card belongs to something else whatever it is.
    Opening the mask with a flat kernel that long keeps exactly those runs and
    nothing else.  They come back rather than being simply deleted because
    where they were is worth knowing: cutting one out leaves the column it lay
    on in two pieces, and it is the cut-out itself that says those pieces
    belong together.  See :func:`join_fragments`.
    """
    length = max(2, int(round(config.overlay_width * card_w)))
    kernel = np.ones((1, length), np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)


def join_fragments(
    boxes: list[Box], overlay: np.ndarray, card_w: int, config: LayoutConfig
) -> list[Box]:
    """Put a column's pieces back together where an overlay was cut out of it.

    Two blobs are one column when they line up horizontally and what lies
    between them is the thing that was erased.  That last condition is what
    makes this safe to do before the board has been laid out at all: the gap
    between the top row and the tableau is a quarter of a card of bare felt,
    and bare felt is not an overlay, so the two rows are never fused.

    The join is a bounding box, and the span it closes over is card rather
    than background -- the overlay was drawn on top of the column, not in
    place of it.  Cutting a column into cards reads the seams off the picture
    and not off the mask, so the joined blob splits exactly as the column
    would have had the phone drawn nothing.
    """
    if len(boxes) < 2 or not overlay.any():
        return list(boxes)

    order = sorted(boxes, key=lambda b: (b.x, b.y))
    joined: list[Box] = []
    for box in order:
        last = joined[-1] if joined else None
        if last is not None and _bridged(last, box, overlay, card_w, config):
            x = min(last.x, box.x)
            right = max(last.x + last.w, box.x + box.w)
            joined[-1] = Box(x, last.y, right - x, max(last.bottom, box.bottom) - last.y)
        else:
            joined.append(box)
    return joined


def _bridged(
    upper: Box, lower: Box, overlay: np.ndarray, card_w: int, config: LayoutConfig
) -> bool:
    """Are these two blobs one column, with an erased overlay between them?"""
    left = max(upper.x, lower.x)
    right = min(upper.x + upper.w, lower.x + lower.w)
    if right - left < 0.5 * card_w:  # not stacked one above the other
        return False
    gap = range(upper.bottom, lower.y)
    # An empty gap is not an overlay to bridge, and one deeper than a card is
    # further apart than any overlay is thick.
    if not 0 < len(gap) <= card_w:
        return False
    return all(
        overlay[row, left:right].mean() >= config.overlay_cover * 255 for row in gap
    )


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
    widths = np.sort(np.array([b.w for b in boxes]))
    best_count, best_value = 0, int(widths[len(widths) // 2])
    for value in widths:
        near = widths[(widths >= value * 0.93) & (widths <= value * 1.07)]
        if len(near) > best_count:
            best_count, best_value = len(near), int(np.median(near))
    return best_value


# --- the grid --------------------------------------------------------------


def find_dragon_buttons(image: np.ndarray, card_w: int, config: LayoutConfig) -> list[Box]:
    """The three dragon buttons, by their tan face.

    They are found by colour rather than brightness because a button whose
    dragons cannot be collapsed right now is drawn darkened, and a brightness
    mask loses it.
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    low, high = config.tan_hue
    mask = (
        (hue >= low) & (hue <= high) & (saturation > 50) & (value > 55)
    ).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    buttons = []
    for box in _components(mask, min_area=0.1 * card_w * card_w):
        if not config.button_w[0] * card_w <= box.w <= config.button_w[1] * card_w:
            continue
        if not config.button_h[0] * card_w <= box.h <= config.button_h[1] * card_w:
            continue
        buttons.append(box)
    return sorted(buttons, key=lambda b: b.y)


def _estimate_pitch(boxes: list[Box], card_w: int, config: LayoutConfig) -> float:
    """Horizontal distance between two neighbouring grid slots."""
    nominal = config.slot_pitch * card_w
    xs = sorted({b.x for b in boxes})
    gaps = [
        b - a
        for a, b in zip(xs, xs[1:])
        if 0.85 * nominal <= b - a <= 1.15 * nominal
    ]
    return float(np.median(gaps)) if gaps else nominal


def _slot_of(x: float, origin: float, pitch: float) -> int:
    return int(round((x - origin) / pitch))


# --- splitting a column ----------------------------------------------------


def _brightness_steps(gray: np.ndarray, blob: Box, config: LayoutConfig) -> list[int]:
    """Rows inside a column blob where the whole width steps up in brightness.

    A card boundary is one horizontal edge right across the card, so every
    pixel column of the blob steps up at the same row.  The glyphs printed on
    the card do not: however dark the ink is, it covers only part of the
    width.  Taking the *median* of the step across the width is what separates
    the two -- anything narrower than half the column cannot move a median,
    where a mean it moves as far as a seam does, which is what used to cut a
    card in two at the rank glyph in its corner.
    """
    inset = max(1, int(blob.w * config.profile_inset))
    region = gray[blob.y : blob.bottom, blob.x + inset : blob.x + blob.w - inset]
    if region.shape[0] < 5 or region.shape[1] < 1:
        return []

    # Two rows either side rather than one: the seam is a sharp edge in the
    # picture the game drew, but scaling a screenshot down to a photo and back
    # up again spreads it over a couple of rows.
    region = region.astype(np.float32)
    above = (region[:-3] + region[1:-2]) / 2
    below = (region[2:-1] + region[3:]) / 2
    step = np.median(below - above, axis=1)

    rows: list[int] = []
    for index, value in enumerate(step):
        if value <= config.min_step:
            continue
        row = index + 1
        if rows and row - rows[-1] <= 3:
            if value > step[rows[-1] - 1]:
                rows[-1] = row
        else:
            rows.append(row)
    return rows


def _estimate_offset(
    gray: np.ndarray, blobs: list[Box], card_w: int, config: LayoutConfig
) -> int:
    """The constant vertical step between two stacked cards.

    Every candidate step is scored by how much of a regular lattice it
    explains across all the columns at once, which is what keeps a column
    holding a single card -- where the only steps come from its glyphs --
    from dragging the estimate down.

    Candidates are confined to a band around the offset the game is known to
    use, and that same value is the answer when nothing on the board is
    stacked and there is genuinely nothing to measure.
    """
    nominal = max(1, int(round(config.nominal_offset * card_w)))
    low = config.offset_range[0] * nominal
    high = config.offset_range[1] * nominal

    steps = {blob: _brightness_steps(gray, blob, config) for blob in blobs}
    candidates = sorted(
        {row for rows in steps.values() for row in rows if low <= row <= high}
    )
    if not candidates:
        return nominal

    def score(step: float) -> int:
        total = 0
        for blob, rows in steps.items():
            position = 0.0
            while True:
                position += step
                if position > blob.h:
                    break
                if not any(abs(row - position) <= config.jump_tolerance * step for row in rows):
                    break
                total += 1
        return total

    best = max(candidates, key=score)
    return int(best) if score(best) else nominal


def _split_column(
    gray: np.ndarray, blob: Box, offset: int, config: LayoutConfig
) -> list[Box]:
    """Cut one column blob into the cards stacked in it.

    Walks the lattice from the top of the blob, and stops at the first
    expected position with no brightness step to back it up -- which is how
    the bottom card, the only one shown in full, ends the column.
    """
    rows = _brightness_steps(gray, blob, config)
    tolerance = config.jump_tolerance * offset

    tops = [0]
    while True:
        expected = tops[-1] + offset
        if expected > blob.h - 1:
            break
        nearby = [row for row in rows if abs(row - expected) <= tolerance]
        if not nearby:
            break
        tops.append(min(nearby, key=lambda row: abs(row - expected)))

    cards = []
    for index, top in enumerate(tops):
        end = tops[index + 1] if index + 1 < len(tops) else blob.h
        cards.append(Box(blob.x, blob.y + top, blob.w, end - top))
    return cards


# --- the whole board -------------------------------------------------------


def detect_layout(image: np.ndarray, config: LayoutConfig | None = None) -> BoardLayout:
    """Locate every visible card in a screenshot of the board."""
    config = config or LayoutConfig()
    if image.ndim != 3:
        raise LayoutError("expected a colour image")

    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = card_mask(image)

    min_area = config.min_area_ratio * height * width
    boxes = _components(mask, min_area=min_area)
    if not boxes:
        raise LayoutError("no card-like shapes found; is this a screenshot of the board?")

    # Measured before anything is stripped, because stripping needs a length to
    # measure against.  Safe to do in that order: the width is the dominant one
    # among a dozen-odd blobs, and an overlay can only ever fuse with a few of
    # them, so the cards still outvote whatever it made of those.
    card_w = _estimate_card_width(boxes)
    overlay = find_overlays(mask, card_w, config)
    if overlay.any():
        boxes = _components(cv2.bitwise_and(mask, cv2.bitwise_not(overlay)), min_area)
        if not boxes:
            raise LayoutError("no card-like shapes found; is this a screenshot of the board?")
        card_w = _estimate_card_width(boxes)

    boxes = [b for b in boxes if 0.85 * card_w <= b.w <= 1.15 * card_w]
    boxes = join_fragments(boxes, overlay, card_w, config)
    if not boxes:
        raise LayoutError("nothing card-shaped found; is this a screenshot of the board?")
    card_h = int(round(card_w * config.card_aspect))

    warnings: list[str] = []
    pitch = _estimate_pitch(boxes, card_w, config)

    buttons = find_dragon_buttons(image, card_w, config)
    if buttons:
        button_x = float(np.median([b.x + b.w / 2 for b in buttons]))
        # The buttons sit in slot 3; back out where slot 0 starts.
        origin = button_x - (BUTTON_SLOT + config.button_offset) * pitch - card_w / 2
    else:
        warnings.append(
            "could not find the dragon buttons; assuming the leftmost card is in the first slot"
        )
        origin = float(min(b.x for b in boxes))

    top_boxes, tableau_boxes = _split_rows(boxes, min_gap=0.5 * card_h)

    layout = BoardLayout(card_w=card_w, card_h=card_h, offset=0, warnings=warnings)

    offset = _estimate_offset(gray, tableau_boxes, card_w, config)
    layout.offset = offset

    for blob in tableau_boxes:
        slot = _slot_of(blob.x, origin, pitch)
        if not 0 <= slot < NUM_COLUMNS:
            warnings.append(f"a column at x={blob.x} falls outside the board; ignored")
            continue
        layout.columns[slot].extend(_split_column(gray, blob, offset, config))

    _assign_top_row(image, top_boxes, origin, pitch, layout, config)
    return layout


def _split_rows(boxes: list[Box], min_gap: float) -> tuple[list[Box], list[Box]]:
    """Tell the row of slots along the top of the screen from the tableau.

    Every tableau column starts at the same y whatever it holds, so the two
    rows are separated by a gap of at least half a card.  When the top row is
    empty there is no gap to find and everything below is tableau -- hence the
    floor, rather than just splitting at the largest gap.
    """
    if not boxes:
        return [], []
    order = sorted(boxes, key=lambda b: b.y)
    gaps = [(order[i + 1].y - order[i].y, i) for i in range(len(order) - 1)]
    if not gaps:
        return [], order
    biggest, at = max(gaps)
    if biggest < min_gap:
        return [], order
    return order[: at + 1], order[at + 1 :]


def _assign_top_row(
    image: np.ndarray,
    boxes: list[Box],
    origin: float,
    pitch: float,
    layout: BoardLayout,
    config: LayoutConfig,
) -> None:
    for box in sorted(boxes, key=lambda b: b.x):
        slot = _slot_of(box.x, origin, pitch)
        if 0 <= slot < NUM_FREE_CELLS:
            layout.free_cells[slot] = box
            layout.locked_cells[slot] = is_card_back(image, box, config)
        elif slot == FLOWER_SLOT:
            layout.flower = box
        elif FIRST_FOUNDATION_SLOT <= slot < FIRST_FOUNDATION_SLOT + 3:
            layout.foundations[slot - FIRST_FOUNDATION_SLOT] = box
        else:
            layout.warnings.append(
                f"a card at x={box.x} sits in slot {slot}, where nothing belongs; ignored"
            )


def is_card_back(image: np.ndarray, box: Box, config: LayoutConfig) -> bool:
    """Is this free cell locked by four collapsed dragons?

    A locked cell shows a card back rather than a face.  The back is a green
    check pattern, so nearly half of it reads as green; a card face is bright
    and neutral and reads as almost none.
    """
    region = image[box.y : box.bottom, box.x : box.x + box.w]
    if region.size == 0:
        return False
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    green = (hsv[:, :, 0] >= 30) & (hsv[:, :, 0] <= 90) & (hsv[:, :, 1] > 40)
    return float(green.mean()) >= config.back_green_fraction


def corner_patch(image: np.ndarray, box: Box, card_w: int, config: LayoutConfig) -> np.ndarray:
    """Crop the strip along the top of a card that carries its rank glyph.

    The crop is sized from the card width, so a card buried in the tableau, the
    same card sitting in a free cell and the reference cut during calibration
    all come out framed identically -- which is the whole basis of matching
    them against each other.
    """
    x0 = int(box.x + config.corner_x * card_w)
    x1 = int(x0 + config.corner_w * card_w)
    y0 = box.y
    y1 = y0 + min(box.h, int(round(config.corner_h * card_w)))

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
                out, f"{index + 1}.{depth + 1}", (box.x + 4, box.y + 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2, cv2.LINE_AA,
            )
    labelled: list[tuple[str, Box | None]] = [
        *[
            (f"cell{i + 1}" + (" LOCKED" if layout.locked_cells[i] else ""), b)
            for i, b in enumerate(layout.free_cells)
        ],
        ("flower", layout.flower),
        *[(f"found{i + 1}", b) for i, b in enumerate(layout.foundations)],
    ]
    for name, box in labelled:
        if box is None:
            continue
        cv2.rectangle(out, (box.x, box.y), (box.x + box.w, box.bottom), (0, 255, 0), 2)
        cv2.putText(
            out, name, (box.x + 4, box.y + 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 128, 0), 2, cv2.LINE_AA,
        )
    return out
