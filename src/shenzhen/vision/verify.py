"""Whether a card is worth a human's eyes, and if so which one.

The deck does most of this job already.  :func:`~shenzhen.game.validate`
demands exactly the forty cards of a real deck, no more and no fewer, so any
reading the resolver accepts is deck-complete -- and that one fact rules out
almost everything a spot check used to exist for.  A screenshot of something
else, a card dropped because its glyph would not classify, a grid whose
columns came out shuffled: each of those loses or duplicates cards, and none
of them survives the count.  Asking a human to re-check a reading the deck has
already proved is asking them to repeat work that was done properly.

What the deck cannot see is a reading where two mistakes cancel -- and the
reader's own scores are what point at those.  Every card arrives with two
independent verdicts on it: the template match, and the deck arithmetic that
had to fit it alongside thirty-nine others.  Where both say the same thing
there is nothing left to ask.  Where the deck *overruled* the matcher, one
card is worth a look, and it is a specific card rather than a sample.

So a clean read goes through without a word, and a read the deck had to
correct puts up exactly one slot -- with a picture of it, since the point of
asking is that the user looks at that card rather than at the whole
screenshot.

The one failure the deck genuinely cannot catch is geometric.  When the layout
pass cannot find the dragon buttons it falls back to guessing which grid slot
the leftmost column occupies, and a wrong guess slides every column sideways:
all forty cards are present, the deck is content, and every column is
mislabelled.  That is what the control card is for -- a card the matcher won
outright, named by the column it sits in, so that saying the column out loud
is what catches the shift.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    """One slot to look at, and what the bot thinks is in it."""

    #: position in the read list, so the caller can cut a picture of the slot
    index: int
    where: str
    card: int
    #: how many cards the slot's column holds, so the bottom card of a stack
    #: can be named rather than counted down to
    depth_total: int | None = None


def column_depths(reads: Sequence) -> dict[str, int]:
    """Column tag -> how deep the reader saw that column go.

    Taken from the reads rather than from the skeleton because a glyph the
    reader failed to classify is dropped from the skeleton but still counted in
    the ``where`` tags of the cards below it -- and the tags are what has to be
    described back to someone looking at the screen.
    """
    totals: dict[str, int] = {}
    for read in reads:
        column, _, depth = read.where.partition(".")
        if depth.isdigit():
            totals[column] = max(totals.get(column, 0), int(depth))
    return totals


def _group(where: str) -> str:
    """Which column (or lone slot) a read belongs to."""
    return where.partition(".")[0]


def _in_tableau(where: str) -> bool:
    return "." in where


def _quality(read) -> tuple[float, float]:
    """How well this read won, from the matcher's point of view."""
    guess = getattr(read, "guess", None)
    if guess is None:  # pragma: no cover -- every real read carries one
        return (1.0, 1.0)
    return (getattr(guess, "margin", 1.0), getattr(guess, "confidence", 1.0))


def spot_check(
    reads: Sequence,
    resolution=None,
    pinned: dict[int, int] | None = None,
    *,
    grid_anchored: bool = True,
) -> Check | None:
    """The one slot worth confirming, or ``None`` when the reading proved itself.

    ``pinned`` are the reads the user has already answered a question about;
    those are never put back up, since asking someone to confirm what they
    just typed tests nothing.  Neither is a card that is still open -- the bot
    is about to ask about that one properly, with its alternatives, and a
    confirmation of a card it has not settled on would say less than the
    question does.

    ``grid_anchored`` says whether the dragon buttons established the
    horizontal slot numbering.  Only an unanchored grid needs a control card:
    ordinary layout warnings can describe a local problem without making the
    position of every other column doubtful.

    ``None`` also comes back for a position that was typed out rather than
    read off a picture, which is the caller's cue to show the board itself.
    """
    if resolution is None or not reads:
        return None
    pinned = dict(pinned or {})
    if resolution.open:
        return None

    index = _overruled(reads, resolution, pinned)
    if index is None and not grid_anchored:
        index = _control(reads, pinned)
    if index is None:
        return None

    read = reads[index]
    return Check(
        index=index,
        where=read.where,
        card=resolution.settled.get(index, read.card),
        depth_total=column_depths(reads).get(_group(read.where)),
    )


def _overruled(reads: Sequence, resolution, pinned: dict[int, int]) -> int | None:
    """The slot where the deck threw out the matcher's own winner.

    That is the only place the two verdicts disagree, so it is the only place
    a human adds information.  Where several disagree, the one the matcher was
    surest of: the deck is right far more often than not, and the sharpest
    disagreement is where "far more often" is doing the most work.
    """
    best: tuple[float, float, int] | None = None
    choice = None
    for index, card in resolution.settled.items():
        if index in pinned or card == reads[index].card:
            continue
        key = (*_quality(reads[index]), -index)
        if best is None or key > best:
            best, choice = key, index
    return choice


def _control(reads: Sequence, pinned: dict[int, int]) -> int | None:
    """A card the matcher won outright, to hang the geometry on.

    From the tableau, because the failure it is here for -- a grid anchored
    one slot over -- shows up as a column that is not the column it is called,
    and only a tableau card is named by its column.  ``None`` when nothing was
    read confidently, which cannot happen on a board that resolved at all.
    """
    best: tuple[float, float, int] | None = None
    control = None
    for index, read in enumerate(reads):
        if index in pinned or not read.confident or not _in_tableau(read.where):
            continue
        key = (*_quality(read), -index)
        if best is None or key > best:
            best, control = key, index
    return control


__all__ = ["Check", "column_depths", "spot_check"]
