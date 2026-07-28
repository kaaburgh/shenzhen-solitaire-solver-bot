"""Screenshot in, board position out."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..cards import SUIT_LETTERS, card_code, is_suit_card, rank_of, suit_of
from ..game import NUM_COLUMNS, NUM_FREE_CELLS, DeckMismatch, State
from .classify import Guess, TemplateBank, classify
from .layout import BoardLayout, Box, LayoutConfig, LayoutError, corner_patch, detect_layout
from .resolve import Resolution, Skeleton, Unresolvable, resolve

# The width a card has to be drawn at before the reader trusts what it sees
# without help, and so the width :func:`enlarge` brings a smaller picture up
# to. Measured by degrading the screenshot fixtures and reading them back
# against their known positions, before there was any enlargement to help:
#
#     card width   cards read right   whole boards right
#     >= 150px           100%                100%
#     120-149px          99.4%               81-92%
#     105-119px          98.5%               61%
#     90-104px           89.9%               18%
#     75-89px            77.8%               0%
#
# A phone screenshot sent through Telegram as a photo rather than a file lands
# around 97px, which is why such a picture used to read as a board that cannot
# exist rather than as the board on screen.
MIN_RELIABLE_CARD_WIDTH = 150

# Ceiling on the enlarged copy, so that a picture the layout pass reads a
# nonsense card width off cannot ask for a nonsense amount of memory. A
# Telegram photo doubles to about 3 megapixels and the largest fixture is
# under 6, so nothing real comes anywhere near this; it is here because the
# scale factor is computed from measured data rather than chosen.
MAX_ENLARGED_PIXELS = 16_000_000


class RecognitionError(RuntimeError):
    """The screenshot was read, but the result is not a legal position."""

    def __init__(
        self,
        message: str,
        *,
        reads: list[ReadCard] | None = None,
        card_w: int | None = None,
        skeleton: Skeleton | None = None,
        deck: DeckMismatch | None = None,
    ) -> None:
        super().__init__(message)
        self.reads = reads or []
        self.card_w = card_w
        self.skeleton = skeleton
        #: the deck complaint behind this, when that is what went wrong, so
        #: the bot can say it in the user's language and in the card marks
        #: instead of re-wording the text it was raised with.
        self.deck = deck

    @property
    def narrow(self) -> bool:
        """Did the picture arrive below the width that reads reliably?

        Worth separating from the rest: it says whether a smaller picture is a
        plausible cause of the failure, which is the difference between "this
        is not the game" and "this came out garbled".
        """
        return self.card_w is not None and self.card_w < MIN_RELIABLE_CARD_WIDTH

    @property
    def draft(self) -> str | None:
        """The raw reading in the input notation, for the user to correct.

        It is not a legal position -- that is what went wrong -- but it is
        thirty-odd cards that are probably right and a handful that are not,
        which beats retyping the board from scratch.
        """
        if self.skeleton is None or not self.reads:
            return None
        return reading_to_text(self.skeleton, self.reads)


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
    #: card width in the picture as it arrived, before :func:`enlarge` had a
    #: go at it.  The layout describes the enlarged copy, so it is no longer
    #: the place to ask how small the thing the user actually sent was.
    source_card_w: int | None = None
    #: how the board was arrived at, and what is still open about it.  Carried
    #: so the bot can ask about the open cards and rebuild the board from the
    #: answers without holding on to the screenshot.
    skeleton: Skeleton | None = None
    resolution: Resolution | None = None

    @property
    def uncertain_indices(self) -> list[int]:
        """Positions in ``reads`` the deck did not pin down on its own.

        Note this is not the same as the reads the classifier was unsure
        about: most of those are settled by elimination and never reach here.
        """
        if self.resolution is None:
            return [
                i for i, r in enumerate(self.reads) if not r.confident and r.resolvable
            ]
        return sorted(set(self.resolution.open))

    @property
    def uncertain(self) -> list[ReadCard]:
        """The reads themselves, for callers that do not need the positions."""
        return [self.reads[i] for i in self.uncertain_indices]

    @property
    def deduced(self) -> int:
        """How many shaky reads the deck settled without needing to ask."""
        return 0 if self.resolution is None else len(self.resolution.settled)

    @property
    def narrow(self) -> int | None:
        """The card width, when it came in under what reads reliably.

        Not fatal -- the picture is enlarged before it is read and the deck
        settles what is left, and a card the deck settles is proved rather
        than guessed.  But it is worth passing on, so it is a number the
        caller can put in its own words rather than a sentence of English in
        the middle of a Russian reply.
        """
        if self.source_card_w is None or self.source_card_w >= MIN_RELIABLE_CARD_WIDTH:
            return None
        return self.source_card_w

    @property
    def confident(self) -> bool:
        return not self.uncertain and not self.warnings and self.narrow is None


def reading_to_text(skeleton: Skeleton, reads: Sequence[ReadCard]) -> str:
    """The reader's raw guess written out in the input notation.

    Deliberately does not go through :class:`~shenzhen.game.State`: this is
    for readings that are *not* legal positions, which is the only time
    anybody wants to see one. Where the reading contradicts itself -- two
    foundations claiming the same suit, say -- the later slot wins and the
    user fixes it, which is the whole point of handing it over.
    """
    cells = [
        "." if index is None else card_code(reads[index].card) for index in skeleton.free
    ]
    # Which colour collapsed into a locked cell is deduced from the rest of the
    # board, and the rest of the board is what has just failed to add up. Handing
    # out the colours in order is as good a guess as any and keeps the line
    # parseable -- no two cells claim the same colour -- for the user to correct
    # along with everything else.
    for slot, colour in zip(skeleton.locked, SUIT_LETTERS):
        cells[slot] = "X" + colour

    foundations = [0, 0, 0]
    for index in skeleton.foundations:
        if index is None:
            continue
        card = reads[index].card
        if is_suit_card(card):
            foundations[suit_of(card)] = rank_of(card)

    lines = [
        "free: " + " ".join(cells),
        "flower: " + ("1" if skeleton.flower_slot else "0"),
        "foundations: " + " ".join(str(top) for top in foundations),
    ]
    for number, group in enumerate(skeleton.columns, start=1):
        lines.append(f"{number}: " + " ".join(card_code(reads[i].card) for i in group))
    return "\n".join(lines)


def load_image(data: bytes) -> np.ndarray:
    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise LayoutError("could not decode the image")
    return image


def enlarge(image: np.ndarray, card_w: int) -> tuple[np.ndarray, int]:
    """Blow a small picture up until its cards are worth reading.

    No information is added by this -- a blurred 3 stays a blurred 3 -- and
    yet it is most of what makes a Telegram-compressed screenshot readable.
    What it buys is registration. Every crop the reader takes is cut on
    integer pixels off a lattice fitted to the columns, so at 97px cards the
    rank glyph lands as much as half a pixel out of where the templates
    expect it, on a glyph only ten pixels tall. Enlarging first divides that
    error by the scale factor, and it costs the same picture nothing.

    Whole-number factors on purpose: a fractional resize resamples onto a
    grid that beats against the original pixels, and which cards it smears
    depends on where they happen to sit. Bilinear on purpose too -- measured
    against the fixtures it beat both bicubic, whose overshoot sharpens JPEG
    ringing into strokes that are not there, and nearest, which preserves the
    registration error it is meant to remove.
    """
    if card_w >= MIN_RELIABLE_CARD_WIDTH:
        return image, 1
    height, width = image.shape[:2]
    scale = math.ceil(MIN_RELIABLE_CARD_WIDTH / max(card_w, 1))
    while scale > 1 and scale * scale * height * width > MAX_ENLARGED_PIXELS:
        scale -= 1
    if scale == 1:
        return image, 1
    enlarged = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    return enlarged, scale


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

    # The layout pass is what measures the card width, and the card width is
    # what says whether the picture needs enlarging -- so on a small picture
    # it runs twice, once to measure and once to lay out the enlarged copy.
    # It is a few tens of milliseconds against a screenshot that would
    # otherwise be unreadable.
    source_card_w = layout.card_w
    image, scale = enlarge(image, layout.card_w)
    if scale > 1:
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
                # The layout pass deliberately errs on the side of proposing a
                # card boundary: on a small image a stroke in the large glyph of
                # an exposed dragon can look like the edge of another card.  A
                # glyph-less proposal is therefore not a card read, and keeping
                # its provisional depth as a warning can contradict the legal
                # board below ("its only card", then "card 2 unreadable").  If
                # this really was a card, the deck check in ``resolve`` will
                # reject the incomplete skeleton; if that check succeeds, the
                # proposal was necessarily layout noise and there is nothing for
                # the user to verify.
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
        raise RecognitionError(
            str(exc),
            reads=reads,
            card_w=source_card_w,
            skeleton=skeleton,
            deck=exc.deck,
        ) from exc

    if resolution.truncated:
        # The search stopped before it had seen every legal reading, so its
        # best-scoring board is the pick of an arbitrary prefix rather than of
        # the field.  Nothing about that is worth showing, at any card width:
        # what makes a settled card trustworthy is having looked at all the
        # alternatives, which is exactly what did not happen here.
        raise RecognitionError(
            "too many readings of this board survive to tell them apart",
            reads=reads,
            card_w=source_card_w,
            skeleton=skeleton,
        )

    return Recognition(
        state=resolution.state,
        reads=reads,
        warnings=warnings,
        layout=layout,
        source_card_w=source_card_w,
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
