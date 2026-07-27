"""Screenshot in, board position out."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..game import NUM_COLUMNS, NUM_FREE_CELLS, State
from .classify import Guess, TemplateBank, classify
from .layout import BoardLayout, Box, LayoutConfig, LayoutError, corner_patch, detect_layout
from .resolve import Resolution, Skeleton, Unresolvable, resolve

# Below this card width the rank glyph is too few pixels across to tell apart
# reliably -- 3 from 8, 6 from 2 -- whatever the thresholds are set to. Measured
# by degrading the screenshot fixtures and reading them back against their known
# positions:
#
#     card width   cards read right   whole boards right
#     >= 150px           100%                100%
#     120-149px          99.4%               81-92%
#     105-119px          98.5%               61%
#     90-104px           89.9%               18%
#     75-89px            77.8%               0%
#
# A phone screenshot sent through Telegram as a photo rather than a file lands
# around 97px, which is why it reads as a board that cannot exist rather than
# as the board on screen.
MIN_RELIABLE_CARD_WIDTH = 150


class RecognitionError(RuntimeError):
    """The screenshot was read, but the result is not a legal position."""

    def __init__(
        self,
        message: str,
        *,
        reads: list[ReadCard] | None = None,
        card_w: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reads = reads or []
        self.card_w = card_w

    @property
    def likely_rescaled(self) -> bool:
        """Is the picture simply too small to read, rather than not a board?

        Worth separating: one is answered by sending the screenshot as a file,
        the other by sending a different picture.
        """
        return self.card_w is not None and self.card_w < MIN_RELIABLE_CARD_WIDTH


@dataclass
class ReadCard:
    """One card the recogniser read, and how sure it is."""

    card: int
    guess: Guess
    where: str
    box: Box
    #: whether the deck resolver may second-guess this read.  False for the
    #: flower slot, which can only ever hold the flower, so a shaky reading of
    #: it is not a question worth putting to anyone.
    resolvable: bool = True

    @property
    def confident(self) -> bool:
        return self.guess.is_confident


@dataclass
class Recognition:
    state: State
    reads: list[ReadCard] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    layout: BoardLayout | None = None
    #: how the board was arrived at, and what is still open about it.  Carried
    #: so the bot can ask about the open cards and rebuild the board from the
    #: answers without holding on to the screenshot.
    skeleton: Skeleton | None = None
    resolution: Resolution | None = None

    @property
    def uncertain(self) -> list[ReadCard]:
        """Reads the deck did not pin down on its own.

        Note this is not the same as the reads the classifier was unsure
        about: most of those are settled by elimination and never reach here.
        """
        if self.resolution is None:
            return [r for r in self.reads if not r.confident and r.resolvable]
        open_indices = set(self.resolution.open)
        return [self.reads[i] for i in sorted(open_indices)]

    @property
    def deduced(self) -> int:
        """How many shaky reads the deck settled without needing to ask."""
        return 0 if self.resolution is None else len(self.resolution.settled)

    @property
    def narrow(self) -> int | None:
        """The card width, when it came in under what reads reliably.

        Not fatal -- the deck settles most of these boards regardless, and one
        it settles is proved rather than guessed.  But it is worth passing on,
        so it is a number the caller can put in its own words rather than a
        sentence of English in the middle of a Russian reply.
        """
        if self.layout is None or self.layout.card_w >= MIN_RELIABLE_CARD_WIDTH:
            return None
        return self.layout.card_w

    @property
    def confident(self) -> bool:
        return not self.uncertain and not self.warnings and self.narrow is None


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

    def read(box: Box, where: str, *, resolvable: bool = True) -> int | None:
        """Classify one slot, returning its index in ``reads``."""
        patch = corner_patch(image, box, layout.card_w, config)
        guess = classify(patch, bank)
        if guess is None:
            return None
        reads.append(
            ReadCard(
                card=guess.card, guess=guess, where=where, box=box, resolvable=resolvable
            )
        )
        return len(reads) - 1

    # --- tableau ---------------------------------------------------------
    columns: list[tuple[int, ...]] = []
    for index, group in enumerate(layout.columns[:NUM_COLUMNS]):
        found: list[int] = []
        for depth, box in enumerate(group):
            entry = read(box, f"{index + 1}.{depth + 1}")
            if entry is None:
                warnings.append(f"column {index + 1}, card {depth + 1}: could not read the glyph")
                continue
            found.append(entry)
        columns.append(tuple(found))
    while len(columns) < NUM_COLUMNS:
        columns.append(())

    # --- free cells ------------------------------------------------------
    # A cell locked by four collapsed dragons shows a card back, which the
    # layout pass recognises; its colour is worked out by the resolver, since
    # the back does not say which dragons went into it.
    free: list[int | None] = [None] * NUM_FREE_CELLS
    locked_slots: list[int] = []
    for index, box in enumerate(layout.free_cells[:NUM_FREE_CELLS]):
        if box is None:
            continue
        if layout.locked_cells[index]:
            locked_slots.append(index)
            continue
        free[index] = read(box, f"free{index + 1}")

    # --- flower ----------------------------------------------------------
    # An empty flower slot is a watermark on the felt, too dim to show up as a
    # card at all, so the slot holding anything means the flower is gone.  The
    # slot cannot hold anything else, so however the glyph reads there is
    # nothing here to ask about.
    if layout.flower is not None:
        read(layout.flower, "flower", resolvable=False)

    # --- foundations -----------------------------------------------------
    foundations: list[int | None] = [None, None, None]
    for index, box in enumerate(layout.foundations[:3]):
        if box is None:
            continue
        foundations[index] = read(box, f"foundation{index + 1}")

    skeleton = Skeleton(
        columns=tuple(columns),
        free=tuple(free),
        locked=tuple(locked_slots),
        flower_slot=layout.flower is not None,
        foundations=tuple(foundations),
    )

    try:
        resolution = resolve(skeleton, reads)
    except Unresolvable as exc:
        raise RecognitionError(str(exc), reads=reads, card_w=layout.card_w) from exc

    if layout.card_w < MIN_RELIABLE_CARD_WIDTH and not resolution.interviewable:
        # A picture this small is only worth trusting when the deck could
        # check it -- and here it could not: either too many readings survive
        # to enumerate, or too many cards are still open to be worth asking
        # about one at a time.  Guessing at the likeliest of hundreds of
        # boards would waste more of the user's time than resending the
        # screenshot as a file, which fixes the cause rather than the symptom.
        raise RecognitionError(
            f"too many cards are unreadable at {layout.card_w}px to pin the board down",
            reads=reads,
            card_w=layout.card_w,
        )

    return Recognition(
        state=resolution.state,
        reads=reads,
        warnings=warnings,
        layout=layout,
        skeleton=skeleton,
        resolution=resolution,
    )


def load_bank(path: str | Path | None) -> TemplateBank | None:
    """Load the template bank, or return ``None`` when none is installed."""
    if path is None:
        return None
    directory = Path(path)
    if not directory.is_dir():
        return None
    bank = TemplateBank.load(directory)
    return bank if bank.templates else None
