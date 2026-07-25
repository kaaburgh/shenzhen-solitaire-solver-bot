"""Screenshot in, board position out."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..cards import (
    FLOWER,
    SUITS,
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

    def __init__(self, message: str, *, reads: list[ReadCard] | None = None) -> None:
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
        patch = corner_patch(image, box, layout.card_w, config)
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
    # A cell locked by four collapsed dragons shows a card back, which the
    # layout pass recognises; its colour is worked out further down, since
    # the back does not say which dragons went into it.
    free: list[int | None] = [None] * NUM_FREE_CELLS
    locked_slots: list[int] = []
    for index, box in enumerate(layout.free_cells[:NUM_FREE_CELLS]):
        if box is None:
            continue
        if layout.locked_cells[index]:
            locked_slots.append(index)
            continue
        entry = read(box, f"free{index + 1}")
        if entry is not None:
            free[index] = entry.card

    # --- flower ----------------------------------------------------------
    # An empty flower slot is a watermark on the felt, too dim to show up as a
    # card at all, so the slot holding anything means the flower is gone.
    flower_collected = layout.flower is not None
    if layout.flower is not None:
        read(layout.flower, "flower")

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

    _colour_locked_cells(columns, free, locked_slots, warnings)

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


def _colour_locked_cells(
    columns: list[tuple[int, ...]],
    free: list[int | None],
    locked_slots: list[int],
    warnings: list[str],
) -> None:
    """Work out which dragons went into each locked cell.

    The card back a locked cell shows is the same whichever colour was
    collapsed, so it has to be deduced: a colour is collapsed exactly when
    none of its four dragons is anywhere on the board.
    """
    if not locked_slots:
        return

    visible = {colour: 0 for colour in SUITS}
    for col in columns:
        for card in col:
            if is_dragon(card):
                visible[dragon_colour(card)] += 1
    for cell in free:
        if cell is not None and is_dragon(cell):
            visible[dragon_colour(cell)] += 1

    collapsed = [colour for colour in SUITS if visible[colour] == 0]
    if len(collapsed) != len(locked_slots):
        warnings.append(
            f"{len(locked_slots)} free cell(s) look collapsed, but "
            f"{len(collapsed)} dragon colour(s) are missing from the board"
        )
    for slot, colour in zip(locked_slots, collapsed):
        free[slot] = locked_cell(colour)


def load_bank(path: str | Path | None) -> TemplateBank | None:
    """Load the template bank, or return ``None`` when none is installed."""
    if path is None:
        return None
    directory = Path(path)
    if not directory.is_dir():
        return None
    bank = TemplateBank.load(directory)
    return bank if bank.templates else None
