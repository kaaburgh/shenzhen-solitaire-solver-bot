"""What a winning line is *for*.

The solver returns moves; a hard position needs something else first.  Working
out which moves are *available* is rarely the difficulty -- the board shows
that much.  The difficulty is which way to set off: dig the ace out, go and
collapse a dragon colour, or run a suit into the foundations to unload the
table so that a column can be emptied at all.  Get that choice wrong and the
next ten moves are wasted, and a numbered list of thirty-four moves does not
say which choice it made.

So the line is cut into *phases*, each one ending at the moment it achieves
something a player would have set out to achieve:

``DRAGONS``  four dragons of one colour collapse
``ACE``      a suit's foundation opens -- its 1 is out from under the pile
``COLUMN``   a column comes empty, and more of them are empty than ever before
``COLLECT``  a suit is run up into its foundation
``FINISH``   nothing structural left; the rest of the line only collects

The first three are landmarks in a strong sense: no later move can take them
back.  A dragon colour is off the board for good, a foundation never goes down,
and "more columns empty than at any earlier point" is a running maximum -- the
column itself may refill, but the record it set stands.  That is what makes them
usable as boundaries: they cannot be reordered, so the moves between two of them
are exactly the work the second one cost.  Foundations creeping up and columns
that empty and fill again ride along inside a phase rather than delimiting one --
a column that comes empty and is used again three moves later was a step, not a
destination.

``COLLECT`` is the exception, and it exists for one failure: twenty-two moves
under the single heading "collapse the white dragons" say nothing at all about
the next five.  A stretch that long is nearly always spent feeding one suit into
its foundation before the dragons can be reached, so :func:`_divide` cuts it in
two and names both halves.

The phases are read off the line the solver already found, not searched for.
See ``docs/plan.md`` for why: reordering a solved line to group its moves by
goal is possible and verifiable, and on measurement it buys almost nothing,
because a near-minimal line is already dependency-tight.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

from .cards import SUITS, make_card, make_dragon
from .game import NUM_COLUMNS, State
from .solver import Step

#: How many lines the plan is allowed to be.  It is meant to be taken in at a
#: glance before playing anything; past half a dozen goals it stops being a
#: shape and turns into a second move list.
MAX_PHASES = 6

#: A phase longer than this says too little about the moves it covers, and is
#: broken up if there is a sensible place to break it.
LONG_PHASE = 8

#: How far past an emptied column to look for what goes into it.  Beyond this
#: the connection is too loose to claim.
LANDS_WINDOW = 6


class GoalKind(StrEnum):
    DRAGONS = "dragons"
    ACE = "ace"
    COLUMN = "column"
    COLLECT = "collect"
    FINISH = "finish"


@dataclass(frozen=True)
class Goal:
    """One thing the player is playing towards.

    Beyond the goal itself, the fields carry the bit of evidence that makes it
    concrete -- how deeply the ace is buried, which columns a dragon collapse
    clears -- so that the phase can be stated as something checkable against
    the screen rather than as an intention the code has invented.
    """

    kind: GoalKind
    #: suit for ``ACE``/``COLLECT``, dragon colour for ``DRAGONS``
    suit: int | None = None
    #: the column to empty (``COLUMN``), or the one the ace is buried in
    #: (``ACE``); ``None`` when the ace is in a free cell
    column: int | None = None
    #: the columns the dragons are sitting in (``DRAGONS``)
    columns: tuple[int, ...] = ()
    #: the rank the foundation reaches (``COLLECT``)
    rank: int | None = None
    #: how many cards lie between the ace and the open end of its column
    buried: int | None = None
    #: the card that goes into the emptied column next, when one does
    lands: int | None = None


@dataclass(frozen=True)
class Phase:
    """A run of moves and the goal it reaches."""

    goal: Goal
    #: indices into the step list, inclusive
    start: int
    end: int
    #: what else the phase achieves on the way
    also: tuple[Goal, ...] = ()

    @property
    def length(self) -> int:
        return self.end - self.start + 1


# --- what each move achieves ----------------------------------------------


class _Beat(NamedTuple):
    """The landmarks one move produced, the automatic collections included."""

    #: dragon colour collapsed by this move
    dragons: int | None
    #: ``(suit, top before, top after)`` for every foundation that advanced
    collect: tuple[tuple[int, int, int], ...]
    #: columns this move left empty
    emptied: tuple[int, ...]
    #: more columns are empty than after any earlier move
    record: bool


def _beats(steps: Sequence[Step]) -> list[_Beat]:
    best = sum(1 for col in steps[0].state_before.columns if not col)
    out: list[_Beat] = []
    for step in steps:
        before, after = step.state_before, step.state_after
        collect = tuple(
            (suit, before.foundations[suit], after.foundations[suit])
            for suit in SUITS
            if after.foundations[suit] > before.foundations[suit]
        )
        emptied = tuple(
            i
            for i in range(NUM_COLUMNS)
            if before.columns[i] and not after.columns[i]
        )
        empty_now = sum(1 for col in after.columns if not col)
        record = empty_now > best
        best = max(best, empty_now)
        out.append(
            _Beat(
                dragons=step.move.a if step.move.kind == "dr" else None,
                collect=collect,
                emptied=emptied,
                record=record,
            )
        )
    return out


def _is_landmark(beat: _Beat) -> bool:
    if beat.dragons is not None:
        return True
    if any(before == 0 for _, before, _ in beat.collect):
        return True
    return beat.record and bool(beat.emptied)


#: how much a landmark is worth keeping as a phase of its own, when there are
#: more of them than the plan has room for
_WEIGHT = {GoalKind.DRAGONS: 3, GoalKind.ACE: 2, GoalKind.COLUMN: 1}


def _headline_kind(beat: _Beat) -> GoalKind:
    if beat.dragons is not None:
        return GoalKind.DRAGONS
    if any(before == 0 for _, before, _ in beat.collect):
        return GoalKind.ACE
    if beat.record and beat.emptied:
        return GoalKind.COLUMN
    if beat.collect:
        return GoalKind.COLLECT
    return GoalKind.FINISH


# --- evidence -------------------------------------------------------------
#
# Every goal is stated with the bit of the position that makes it concrete, and
# all of it is read off the board as it stands *when the phase begins* -- which
# is where the player is standing when they read the line.  Describing a dragon
# collapse by the columns the dragons sit in at the moment of the collapse
# would be just as true and no use at all: that is ten moves away, and the
# board in front of them says something else.


def _buried(state: State, suit: int) -> tuple[int | None, int | None]:
    """Where the ace of ``suit`` is and how much sits between it and the hand.

    The blocking cards are the ones drawn *below* it on screen: within a column
    the exposed card is the last one, so everything after the ace in the column
    is in the way.

    An ace anywhere but in a column is collected by the game the moment it is
    exposed, so a phase that sets out to dig one out always finds it in one; the
    ``None`` is there so that a position which somehow disagrees loses the
    detail rather than the answer.
    """
    ace = make_card(suit, 1)
    for index, col in enumerate(state.columns):
        for depth, card in enumerate(col):
            if card == ace:
                return index, len(col) - 1 - depth
    return None, None


def _sitting(state: State, colour: int) -> tuple[int, ...]:
    """The columns the dragons of ``colour`` are in, wherever in the stack.

    Those are the columns that have to be dug down to; one of the four may be
    in a free cell already, in which case it needs nothing and is not named.
    """
    dragon = make_dragon(colour)
    return tuple(i for i, col in enumerate(state.columns) if dragon in col)


def _lands(steps: Sequence[Step], after: int, column: int) -> int | None:
    """The first card to be played into ``column`` once it is empty."""
    for step in steps[after + 1 : after + 1 + LANDS_WINDOW]:
        move = step.move
        if move.kind == "tt" and move.b == column:
            return step.state_before.columns[move.a][-move.n]
        if move.kind == "ft" and move.b == column:
            return step.state_before.free[move.a]
    return None


# --- phases ---------------------------------------------------------------


def _headline(steps: Sequence[Step], beats: Sequence[_Beat], start: int, end: int) -> Goal:
    beat = beats[end]
    kind = _headline_kind(beat)

    if kind is GoalKind.DRAGONS:
        colour = beat.dragons
        assert colour is not None
        return Goal(
            GoalKind.DRAGONS,
            suit=colour,
            columns=_sitting(steps[start].state_before, colour),
        )

    if kind is GoalKind.ACE:
        suit = next(s for s, before, _ in beat.collect if before == 0)
        column, buried = _buried(steps[start].state_before, suit)
        return Goal(GoalKind.ACE, suit=suit, column=column, buried=buried)

    if kind is GoalKind.COLUMN:
        column = beat.emptied[0]
        return Goal(
            GoalKind.COLUMN, column=column, lands=_lands(steps, end, column)
        )

    if kind is GoalKind.COLLECT:
        # The suit the phase moved furthest, out of the ones it finished on --
        # the last move of a cut like this lands several foundations at once and
        # the one that climbed is the one the moves were for.
        climb = _climb(beats, start, end)
        suit = max(
            (s for s, _, _ in beat.collect), key=lambda s: climb[s][1] - climb[s][0]
        )
        return Goal(GoalKind.COLLECT, suit=suit, rank=climb[suit][1])

    return Goal(GoalKind.FINISH)


def _climb(beats: Sequence[_Beat], start: int, end: int) -> dict[int, tuple[int, int]]:
    """How far each foundation moves over a run of moves: suit -> (from, to)."""
    climb: dict[int, tuple[int, int]] = {}
    for beat in beats[start : end + 1]:
        for suit, before, after in beat.collect:
            low, high = climb.get(suit, (before, after))
            climb[suit] = (min(low, before), max(high, after))
    return climb


def _also(beats: Sequence[_Beat], start: int, end: int, headline: Goal) -> tuple[Goal, ...]:
    """What the phase achieves besides its goal, worth a glance and no more."""
    extra: list[Goal] = []

    for offset, beat in enumerate(beats[start : end + 1]):
        if beat.dragons is None:
            continue
        if start + offset == end and headline.kind is GoalKind.DRAGONS:
            continue
        extra.append(Goal(GoalKind.DRAGONS, suit=beat.dragons))

    for suit, (low, high) in sorted(_climb(beats, start, end).items()):
        # A foundation that crept up one rank is not news; one that opened at
        # all is, because that is a suit unblocked.
        if high - low < 2 and low != 0:
            continue
        if suit == headline.suit:
            if headline.kind is GoalKind.COLLECT:
                continue
            # Aces and twos are collected by the game itself, so a phase that
            # got the ace out is not also reporting that it reached the two.
            if headline.kind is GoalKind.ACE and high <= 2:
                continue
        extra.append(Goal(GoalKind.COLLECT, suit=suit, rank=high))

    # A phase that finishes all three foundations is the cascade, and listing
    # it suit by suit buries that under arithmetic.
    if sum(1 for goal in extra if goal.kind is GoalKind.COLLECT and goal.rank == 9) == 3:
        return (Goal(GoalKind.FINISH),)

    return tuple(extra[:2])


def _bounds(beats: Sequence[_Beat]) -> list[int]:
    """Which landmarks get a phase of their own.

    Every landmark is a candidate; the plan has room for
    :data:`MAX_PHASES`, so the cheapest are given up first -- and among
    equals, the one that cost the fewest moves, since a landmark that fell out
    on the way to another is the one least worth announcing.  Dropping a
    boundary does not lose the landmark: it reappears under ``also`` of the
    phase that swallowed it.
    """
    last = len(beats) - 1
    candidates = [i for i, beat in enumerate(beats) if _is_landmark(beat) and i != last]
    keep = candidates + [last]

    while len(keep) > MAX_PHASES:
        worst = None
        for position, index in enumerate(keep[:-1]):  # the last one is mandatory
            previous = keep[position - 1] if position else -1
            rank = (_WEIGHT.get(_headline_kind(beats[index]), 0), index - previous)
            if worst is None or rank < worst[0]:
                worst = (rank, position)
        assert worst is not None
        keep.pop(worst[1])

    return keep


def _split_point(beats: Sequence[_Beat], start: int, end: int) -> int | None:
    """Where a long phase divides into "run a suit up" and "then the landmark".

    Twenty moves under one heading say almost nothing about the next five, and
    a stretch that long is nearly always spent feeding one suit into its
    foundation -- which is a goal in its own right, and the one the player is
    actually working on for most of those moves.  So the phase is cut where
    that suit last moves, leaving the landmark to the shorter half.
    """
    climb = _climb(beats, start, end)
    if not climb:
        return None
    suit, (low, high) = max(climb.items(), key=lambda item: item[1][1] - item[1][0])
    if high - low < 3:
        return None
    advances = [
        i
        for i in range(start, end)  # the phase's own last move is not a split
        if any(s == suit for s, _, _ in beats[i].collect)
    ]
    return advances[-1] if advances else None


def _divide(beats: Sequence[_Beat], keep: list[int]) -> list[int]:
    """Break up phases that cover more moves than they can account for.

    One cut per phase, longest phase first.  :data:`MAX_PHASES` gives way for a
    phase twice over the limit: twenty moves under one heading are worse than a
    seventh line, and there is a ceiling either way.
    """
    candidates = []
    start = 0
    for end in keep:
        if end - start + 1 > LONG_PHASE:
            point = _split_point(beats, start, end)
            if point is not None:
                candidates.append((end - start + 1, point))
        start = end + 1

    for length, point in sorted(candidates, reverse=True):
        if len(keep) >= MAX_PHASES + 2:
            break
        if len(keep) >= MAX_PHASES and length <= 2 * LONG_PHASE:
            continue
        keep.append(point)

    keep.sort()
    return keep


def plan(steps: Sequence[Step]) -> list[Phase]:
    """Cut a solved line into the goals it is made of.

    Empty for a line with no moves in it -- there is nothing to explain about
    a position that is already won.
    """
    if not steps:
        return []

    beats = _beats(steps)
    phases: list[Phase] = []
    start = 0
    for end in _divide(beats, _bounds(beats)):
        headline = _headline(steps, beats, start, end)
        also = _also(beats, start, end, headline)
        # A column coming empty on the last move of the line is not what the
        # line was for: by then every column is coming empty.
        if headline.kind is GoalKind.COLUMN and end == len(steps) - 1:
            headline = Goal(GoalKind.FINISH)
            also = ()
        phases.append(Phase(goal=headline, start=start, end=end, also=also))
        start = end + 1

    return phases


def phase_of(phases: Sequence[Phase], index: int) -> Phase | None:
    """The phase step ``index`` belongs to."""
    for phase in phases:
        if phase.start <= index <= phase.end:
            return phase
    return None
