"""Screenshot in, board position out."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..cards import (
    FLOWER,
    SUIT_LETTERS,
    card_code,
    dragon_colour,
    is_dragon,
    is_suit_card,
    locked_cell,
    rank_of,
    suit_of,
)
from ..game import NUM_COLUMNS, NUM_FREE_CELLS, InvalidBoard, State, auto_resolve, validate
from .classify import Guess, TemplateBank, classify
from .layout import BoardLayout, Box, LayoutConfig, LayoutError, corner_patch, detect_layout


class RecognitionError(RuntimeError):
    """The screenshot was read, but the result is not a legal position."""

    def __init__(self, message: str, *, reads: list["ReadCard"] | None = None) -> None:
        super().__init__(message)
        self.reads = reads or []


@dataclass
class ReadCard:
    """One card the recogniser read, and how sure it is."""

    card: int
    guess: Guess
    where: str
    box: Box

    @property
    def confident(self) -> bool:
        return self.guess.is_confident


@dataclass
class Recognition:
    state: State
    reads: list[ReadCard] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    layout: BoardLayout | None = None

    @property
    def uncertain(self) -> list[ReadCard]:
        return [r for r in self.reads if not r.confident]

    @property
    def confident(self) -> bool:
        return not self.uncertain and not self.warnings


def load_image(data: bytes) -> np.ndarray:
    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise LayoutError("could not decode the image")
    return image


def recognize(
    image: np.ndarray,
    bank: TemplateBank,
    config: LayoutConfig | None = None,
) -> Recognition:
    """Read a board from a screenshot.

    Raises :class:`LayoutError` if the picture does not look like the game and
    :class:`RecognitionError` if it does but the cards read off it could not
    have come from a real deck.
    """
    config = config or LayoutConfig()
    layout = detect_layout(image, config)
    warnings = list(layout.warnings)
    reads: list[ReadCard] = []

    def read(box: Box, where: str) -> ReadCard | None:
        patch = corner_patch(image, box, layout.card_w, config, max_h=layout.offset)
        guess = classify(patch, bank)
        if guess is None:
            return None
        entry = ReadCard(card=guess.card, guess=guess, where=where, box=box)
        reads.append(entry)
        return entry

    # --- tableau ---------------------------------------------------------
    columns: list[tuple[int, ...]] = []
    for index, group in enumerate(layout.columns[:NUM_COLUMNS]):
        cards: list[int] = []
        for depth, box in enumerate(group):
            entry = read(box, f"{index + 1}.{depth + 1}")
            if entry is None:
                warnings.append(f"column {index + 1}, card {depth + 1}: could not read the glyph")
                continue
            cards.append(entry.card)
        columns.append(tuple(cards))
    while len(columns) < NUM_COLUMNS:
        columns.append(())

    # --- free cells ------------------------------------------------------
    free: list[int | None] = [None] * NUM_FREE_CELLS
    for index, box in enumerate(layout.free_cells[:NUM_FREE_CELLS]):
        if box is None:
            continue
        entry = read(box, f"free{index + 1}")
        if entry is not None:
            free[index] = entry.card

    # --- flower ----------------------------------------------------------
    flower_collected = False
    if layout.flower is not None:
        entry = read(layout.flower, "flower")
        flower_collected = entry is not None

    # --- foundations -----------------------------------------------------
    foundations = [0, 0, 0]
    for index, box in enumerate(layout.foundations[:3]):
        if box is None:
            continue
        entry = read(box, f"foundation{index + 1}")
        if entry is None:
            continue
        if not is_suit_card(entry.card):
            warnings.append(
                f"foundation {index + 1}: read {card_code(entry.card)}, which cannot sit there"
            )
            continue
        foundations[suit_of(entry.card)] = rank_of(entry.card)

    free = _resolve_locked_cells(columns, free, warnings)

    state = State(
        columns=tuple(columns),
        free=tuple(free),
        foundations=(foundations[0], foundations[1], foundations[2]),
        flower=flower_collected or not _flower_on_table(columns, free),
    )

    try:
        validate(state)
    except InvalidBoard as exc:
        raise RecognitionError(str(exc), reads=reads) from exc

    settled, _ = auto_resolve(state)
    return Recognition(state=settled, reads=reads, warnings=warnings, layout=layout)


def _flower_on_table(columns: list[tuple[int, ...]], free: list[int | None]) -> bool:
    return any(FLOWER in col for col in columns) or FLOWER in free


def _resolve_locked_cells(
    columns: list[tuple[int, ...]], free: list[int | None], warnings: list[str]
) -> list[int | None]:
    """Tell a dragon parked in a free cell from four collapsed ones.

    Both look like a single dragon face in the cell, but the rest of the deck
    settles it: four dragons of a colour are still in play if they are still
    visible, and exactly one is visible once they have been collapsed.
    """
    counts: dict[int, int] = {}
    for col in columns:
        for card in col:
            if is_dragon(card):
                counts[dragon_colour(card)] = counts.get(dragon_colour(card), 0) + 1
    for cell in free:
        if cell is not None and is_dragon(cell):
            counts[dragon_colour(cell)] = counts.get(dragon_colour(cell), 0) + 1

    resolved: list[int | None] = list(free)
    for index, cell in enumerate(free):
        if cell is None or not is_dragon(cell):
            continue
        colour = dragon_colour(cell)
        visible = counts.get(colour, 0)
        if visible == 1:
            resolved[index] = locked_cell(colour)
        elif visible != 4:
            warnings.append(
                f"free cell {index + 1}: {visible} {SUIT_LETTERS[colour]} dragons visible, "
                "expected 1 (collapsed) or 4 (still in play)"
            )
    return resolved


def load_bank(path: str | Path | None) -> TemplateBank | None:
    """Load the template bank, or return ``None`` when none is installed."""
    if path is None:
        return None
    directory = Path(path)
    if not directory.is_dir():
        return None
    bank = TemplateBank.load(directory)
    return bank if bank.templates else None
