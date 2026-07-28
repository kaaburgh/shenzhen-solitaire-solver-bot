"""Which handful of cards is worth a human's eyes.

Reading the whole position back is a poor way to have it checked.  The board
is eight vertical stacks on screen and eight horizontal lines in a message, so
confirming it means walking forty codes against forty pictures in a layout
that does not match -- and the effort is spread evenly over cards that deserve
none of it.  A card the matcher won by a mile is not where the mistake will
be, and forty of those crowd out the two that are.

So only a few slots go up.  Three of them are the reads the matcher was least
sure of, in the order it was unsure about them.  The fourth is the one it was
*most* sure of, and that one is the point of the exercise: the shaky cards
catch a misread glyph, while the confident one catches everything a list of
doubts cannot see -- a screenshot of a different deal, a grid anchored one
slot over, a board that moved on between the screenshot and now.  Those
failures get every card wrong at once, so none of them looks individually
suspicious, and the only way to notice is to check a card the bot claims to be
certain about.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: how many shaky reads to put up alongside the control card
DOUBTS = 3


@dataclass(frozen=True)
class Check:
    """One slot to look at, and what the bot thinks is in it."""

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


def _quality(read) -> tuple[float, float]:
    """How well this read won, from the matcher's point of view."""
    guess = getattr(read, "guess", None)
    if guess is None:  # pragma: no cover -- every real read carries one
        return (1.0, 1.0)
    return (getattr(guess, "margin", 1.0), getattr(guess, "confidence", 1.0))


def _tier(index: int, read, resolution) -> int:
    """How much doubt is left on a read once the deck has had its say.

    Three grades, because they fail differently.  A read the surviving boards
    still disagree about is a coin toss the bot has called.  A read the
    matcher flagged and the deck then settled is only as sound as the forty
    cards it was settled against.  Everything else is a read that stood on its
    own.
    """
    if resolution is not None and index in resolution.open:
        return 0
    if not read.confident:
        return 1
    return 2


def _resolved(index: int, read, resolution, pinned: dict[int, int]) -> int:
    """The card the bot ended up with for this read."""
    if index in pinned:
        return pinned[index]
    if resolution is not None:
        if index in resolution.settled:
            return resolution.settled[index]
        if index in resolution.open:
            options = resolution.options(index)
            if options:
                return options[0]
    return read.card


def spot_checks(
    reads: Sequence,
    resolution=None,
    pinned: dict[int, int] | None = None,
    *,
    doubts: int = DOUBTS,
) -> tuple[Check, ...]:
    """The few slots to have checked, in the order they sit on the board.

    ``pinned`` are the reads the user has already answered a question about;
    those are excluded, since asking someone to confirm what they just typed
    tests nothing.  So is the flower slot, which cannot hold anything else.

    Comes back empty when there is nothing to sample -- a position that was
    typed out rather than read off a picture -- which is the caller's cue to
    show the board itself instead.
    """
    pinned = dict(pinned or {})
    candidates = [
        index
        for index, read in enumerate(reads)
        if read.resolvable and index not in pinned
    ]
    if not candidates:
        return ()

    def shakiness(index: int) -> tuple[int, float, float]:
        return (_tier(index, reads[index], resolution), *_quality(reads[index]))

    remaining = sorted(candidates, key=lambda i: (shakiness(i), i))
    chosen: list[int] = []
    used: set[str] = set()
    while remaining and len(chosen) < doubts:
        # Among the reads tied for shakiest -- which on a board that read
        # cleanly is most of them -- take one from a column not looked at yet,
        # so the sample walks the board rather than stacking up in one corner
        # of it.
        front = shakiness(remaining[0])
        tied = [i for i in remaining if shakiness(i) == front]
        pick = min(tied, key=lambda i: (_group(reads[i].where) in used, i))
        chosen.append(pick)
        used.add(_group(reads[pick].where))
        remaining.remove(pick)

    control = _control(reads, resolution, candidates, chosen, used)
    if control is not None:
        chosen.append(control)

    depths = column_depths(reads)
    return tuple(
        Check(
            where=reads[index].where,
            card=_resolved(index, reads[index], resolution, pinned),
            depth_total=depths.get(_group(reads[index].where)),
        )
        # Board order, not doubt order: the eye works down the screen, and it
        # also keeps the control card from announcing itself by its position.
        for index in sorted(chosen)
    )


def _control(
    reads: Sequence,
    resolution,
    candidates: Sequence[int],
    chosen: Sequence[int],
    used: set[str],
) -> int | None:
    """The read to offer as the canary: the one the matcher won most clearly.

    Preferably from a column none of the shaky cards came from, so that a grid
    anchored on the wrong slot cannot happen to line up on the only part of
    the board being looked at.  ``None`` when every read is already up for
    checking, which only happens on a board with almost nothing on it.
    """
    best: tuple[bool, float, float, int] | None = None
    control = None
    for index in candidates:
        if index in chosen or _tier(index, reads[index], resolution) != 2:
            continue
        margin, confidence = _quality(reads[index])
        key = (_group(reads[index].where) in used, -margin, -confidence, index)
        if best is None or key < best:
            best, control = key, index
    return control


__all__ = ["DOUBTS", "Check", "column_depths", "spot_checks"]
