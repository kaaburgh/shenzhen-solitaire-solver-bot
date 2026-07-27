"""Deck arithmetic over the cards the reader was unsure about.

The reader flags a card whenever its best template does not win by enough --
a blurred 3 against an 8, say.  Asking the user about every one of those is
the obvious thing to do and mostly a waste of their time, because the deck is
a very strong constraint: each of the 27 suit cards exists exactly once, there
are four dragons of each colour and one flower, and a board holds all forty.

So most flagged cards are not actually open questions.  If thirty-nine cards
are read confidently, the fortieth is whatever is left over -- there is
nothing to ask.  More often a handful are flagged and only one of them is
genuinely undetermined; answering that one pins the rest by elimination.

This module makes that concrete.  Each unsure read becomes a variable with a
shortlist of candidates, every combination is tried against
:func:`~shenzhen.game.validate`, and what comes out is the set of boards the
screenshot could be showing.  A card every surviving board agrees on is
settled and never asked about.  A card they disagree on becomes one question,
and the answer re-runs the whole thing -- which is what makes the second and
third questions usually evaporate.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ..cards import (
    FLOWER,
    FULL_DECK,
    SUITS,
    dragon_colour,
    is_dragon,
    is_suit_card,
    locked_cell,
    rank_of,
    suit_of,
)
from ..game import InvalidBoard, State, auto_resolve, validate

#: how many complete boards to keep.  Only used to decide what is settled and
#: what to ask, so a cap costs nothing once it is comfortably past the point
#: where everything is ambiguous anyway.
MAX_POSSIBILITIES = 400

#: ceiling on backtracking steps, so a hopeless screenshot cannot spin.
MAX_STEPS = 400_000

#: past this many unsure reads the picture is the problem, not the reading of
#: it, and an interview is the wrong response.
MAX_UNKNOWNS = 14

#: and past this many questions still standing after the deck has done its
#: work, typing the position out is less effort than answering them.
MAX_QUESTIONS = 6


@dataclass(frozen=True)
class Level:
    """How hard to look for a board that fits the deck.

    Escalated only when the level before it found nothing.  ``promote`` lets
    reads the classifier called *confident* become variables too: measuring
    against the fixtures, 96% of wrong reads are flagged unsure, and this is
    for the other 4%.
    """

    cap: int
    promote: int


# How far down the ranking to look.  Chosen from where the true card actually
# lands on 155 unsure reads off degraded fixtures -- by rank, not by score,
# because the scores bunch up: even a window of 0.06 below the winner lets in
# a median of seven cards, so it discriminates nothing worth having.
#
#     top-4    76% overall, but 97-100% once cards are >=100px wide
#     top-12   98.7%
#     top-16   100% of everything seen
#
# The first level is the cheap one that answers the common case; the rest cost
# more branching and only ever run when the one before found nothing at all.
# Widening in steps rather than in one jump matters: going straight from four
# candidates to twelve turns "no legal board" into "four hundred of them",
# which is no more use to the person waiting.
LEVELS: tuple[Level, ...] = (
    Level(cap=4, promote=0),
    Level(cap=8, promote=0),
    Level(cap=12, promote=0),
    Level(cap=16, promote=3),
)


@dataclass(frozen=True)
class Skeleton:
    """Which read sits where on the board, by index into the read list.

    Separating this from the cards themselves is the whole trick: it lets the
    same board be rebuilt under a different set of answers without going back
    to the image.
    """

    columns: tuple[tuple[int, ...], ...]
    free: tuple[int | None, ...]
    #: free cells showing a card back -- four collapsed dragons, colour deduced
    locked: tuple[int, ...]
    flower_slot: bool
    foundations: tuple[int | None, ...]

    def build(self, cards: Sequence[int]) -> State:
        """The board that results from reading ``cards[i]`` at read ``i``.

        Raises :class:`InvalidBoard` if that reading could not be a real
        position -- which is exactly what makes it usable as the test inside
        the search below.
        """
        columns = tuple(tuple(cards[i] for i in group) for group in self.columns)
        free: list[int | None] = [
            None if i is None else cards[i] for i in self.free
        ]

        foundations = [0, 0, 0]
        seen_suits = set()
        for slot, index in enumerate(self.foundations):
            if index is None:
                continue
            card = cards[index]
            if not is_suit_card(card):
                raise InvalidBoard(f"foundation {slot + 1}: {card} cannot sit there")
            suit = suit_of(card)
            if suit in seen_suits:
                raise InvalidBoard(f"two foundations of the same suit ({suit})")
            seen_suits.add(suit)
            foundations[suit] = rank_of(card)

        _colour_locked_cells(columns, free, self.locked)

        on_table = any(FLOWER in column for column in columns) or FLOWER in free
        state = State(
            columns=columns,
            free=tuple(free),
            foundations=(foundations[0], foundations[1], foundations[2]),
            flower=self.flower_slot or not on_table,
        )
        validate(state)
        # Validate against the board as drawn, then hand back the board as the
        # game would leave it: anything the screenshot caught mid-animation is
        # already on its way to a foundation.
        return auto_resolve(state)[0]

    def consumed(self, index: int, card: int) -> Counter[int]:
        """What reading ``card`` at read ``index`` takes out of the deck.

        A foundation showing G3 has G1 and G2 underneath it, so it accounts
        for three cards rather than one.  Locked cells are left out on
        purpose: their four dragons depend on what the rest of the board turns
        out to be, and under-counting only ever makes the search look at more
        combinations, never at fewer -- :func:`validate` has the last word.
        """
        if index in self._foundation_reads and is_suit_card(card):
            suit = suit_of(card)
            return Counter(
                {suit * 9 + rank - 1: 1 for rank in range(1, rank_of(card) + 1)}
            )
        return Counter({card: 1})

    @property
    def _foundation_reads(self) -> frozenset[int]:
        return frozenset(i for i in self.foundations if i is not None)

    def placed(self, cards: Sequence[int], indices: Iterable[int]) -> Counter[int]:
        """What the reads in ``indices`` take out of the deck, together."""
        counts: Counter[int] = Counter()
        for index in indices:
            counts.update(self.consumed(index, cards[index]))
        return counts


def _colour_locked_cells(
    columns: Sequence[Sequence[int]],
    free: list[int | None],
    locked: Sequence[int],
) -> None:
    """Work out which dragons went into each locked cell.

    The card back is the same whichever colour was collapsed, so it has to be
    deduced: a colour is collapsed exactly when none of its four dragons is
    anywhere on the board.
    """
    if not locked:
        return

    visible = dict.fromkeys(SUITS, 0)
    for column in columns:
        for card in column:
            if is_dragon(card):
                visible[dragon_colour(card)] += 1
    for cell in free:
        if cell is not None and is_dragon(cell):
            visible[dragon_colour(cell)] += 1

    collapsed = [colour for colour in SUITS if visible[colour] == 0]
    if len(collapsed) != len(locked):
        raise InvalidBoard(
            f"{len(locked)} free cell(s) hold collapsed dragons, but "
            f"{len(collapsed)} dragon colour(s) are missing from the board"
        )
    for slot, colour in zip(locked, collapsed):
        free[slot] = locked_cell(colour)


@dataclass(frozen=True)
class Unknown:
    """One read the deck might not pin down on its own."""

    index: int
    where: str
    candidates: tuple[int, ...]
    #: the reader's own winner, kept so a question can show it first
    read: int


@dataclass(frozen=True)
class Possibility:
    cards: tuple[int, ...]
    state: State
    score: float


@dataclass
class Resolution:
    """What the screenshot could be showing, once the deck has had its say."""

    state: State
    unknowns: tuple[Unknown, ...]
    possibilities: tuple[Possibility, ...]
    level: int
    #: unknown index -> the only card that fits.  Settled without asking.
    settled: dict[int, int] = field(default_factory=dict)
    #: unknown indices the surviving boards still disagree about
    open: tuple[int, ...] = ()
    #: the search hit its ceiling, so it did not see every legal board.  What
    #: it did find is still legal, but "every board agrees on this card" is no
    #: longer something it can honestly claim -- so neither `settled` nor a
    #: question built from `open` means anything here.
    truncated: bool = False

    @property
    def certain(self) -> bool:
        return len(self.possibilities) == 1 and not self.truncated

    @property
    def interviewable(self) -> bool:
        """Is asking about the open cards worth the user's time?"""
        return not self.truncated and len(self.open) <= MAX_QUESTIONS

    def options(self, index: int) -> tuple[int, ...]:
        """The cards read ``index`` could still be, likeliest first."""
        position = [u.index for u in self.unknowns].index(index)
        seen: dict[int, float] = {}
        for possibility in self.possibilities:
            card = possibility.cards[position]
            seen[card] = max(seen.get(card, -1e9), possibility.score)
        return tuple(sorted(seen, key=lambda c: -seen[c]))

    def next_question(self) -> int | None:
        """Which read to ask about, of those still open.

        Whichever answer splits the surviving boards most evenly, since that
        is what leaves the fewest of them standing whatever the answer turns
        out to be.  With two boards it makes no difference; with a dozen it is
        the difference between one question and three.
        """
        best: tuple[int, int, int] | None = None
        choice = None
        for unknown in self.unknowns:
            if unknown.index not in self.open:
                continue
            position = [u.index for u in self.unknowns].index(unknown.index)
            groups = Counter(p.cards[position] for p in self.possibilities)
            key = (max(groups.values()), -len(groups), unknown.index)
            if best is None or key < best:
                best, choice = key, unknown.index
        return choice


class Unresolvable(InvalidBoard):
    """No reading of the unsure cards produces a legal position."""


def resolve(
    skeleton: Skeleton,
    reads: Sequence,
    pinned: dict[int, int] | None = None,
    *,
    level: int | None = None,
) -> Resolution:
    """Find every board the screenshot could be showing.

    ``reads`` are :class:`~shenzhen.vision.recognize.ReadCard` objects -- only
    ``.card``, ``.where`` and ``.guess`` are touched, so anything shaped like
    one will do.  ``pinned`` maps a read index to a card the user has told us
    about, which is how an answered question narrows the field.

    Raises :class:`Unresolvable` when nothing fits, carrying the deck
    complaint from the reader's own best guess so the message stays the
    specific one.
    """
    pinned = dict(pinned or {})
    levels = LEVELS if level is None else (LEVELS[level],)
    offset = 0 if level is None else level

    for step, current in enumerate(levels):
        unknowns = _unknowns(reads, current, pinned)
        if unknowns is None:
            continue
        possibilities, truncated = _search(skeleton, reads, unknowns, pinned)
        if possibilities:
            return _summarise(unknowns, possibilities, offset + step, truncated)

    raise Unresolvable(str(_complaint(skeleton, reads, pinned)))


def _complaint(skeleton: Skeleton, reads: Sequence, pinned: dict[int, int]) -> InvalidBoard:
    """Why the reader's own best guess is not a legal position."""
    cards = [pinned.get(i, read.card) for i, read in enumerate(reads)]
    try:
        skeleton.build(cards)
    except InvalidBoard as exc:
        return exc
    return InvalidBoard("no legal reading of this board")  # pragma: no cover


def _unknowns(
    reads: Sequence, level: Level, pinned: dict[int, int]
) -> tuple[Unknown, ...] | None:
    """The reads to treat as variables, and what each is allowed to be.

    ``None`` means there are too many of them to interview a human about.
    """
    variable = [i for i, read in enumerate(reads) if read.resolvable and not read.confident]
    if level.promote:
        confident = [
            i for i, read in enumerate(reads) if read.resolvable and read.confident
        ]
        confident.sort(key=lambda i: (reads[i].guess.margin, reads[i].guess.confidence, i))
        variable = sorted(set(variable) | set(confident[: level.promote]))

    if len(variable) > MAX_UNKNOWNS:
        return None

    unknowns = []
    for index in variable:
        read = reads[index]
        if index in pinned:
            candidates: tuple[int, ...] = (pinned[index],)
        else:
            candidates = _candidates(read, level)
        unknowns.append(
            Unknown(index=index, where=read.where, candidates=candidates, read=read.card)
        )
    return tuple(unknowns)


def _candidates(read, level: Level) -> tuple[int, ...]:
    """The cards a single unsure read could plausibly be.

    Taken from the whole bank rather than the ink colour's shortlist: a glyph
    blurred enough to be misread is sometimes blurred enough to lose its
    colour too, and on the degraded fixtures the true card fell outside the
    shortlist about a fifth of the time.
    """
    ranking = read.guess.ranking if read.guess else ()
    if not ranking:
        return (read.card,)
    picked = [card for card, _ in ranking[: level.cap]]
    if read.card not in picked:
        picked.insert(0, read.card)
    return tuple(picked)


def _search(
    skeleton: Skeleton,
    reads: Sequence,
    unknowns: tuple[Unknown, ...],
    pinned: dict[int, int],
) -> tuple[tuple[Possibility, ...], bool]:
    """Every legal board reachable by choosing one candidate per unknown.

    Backtracking, with the two things that keep it from blowing up on the
    dozen-odd unknowns a badly compressed screenshot produces:

    * **forward checking.** After each placement, every unknown still to come
      must retain at least one candidate the deck can still supply. A branch
      that has quietly used up the last DR dies at the card that used it
      rather than eleven levels further down.
    * **most-constrained first.** Unknowns are visited fewest-candidates
      first, so the branching that does happen happens as deep as possible.

    Together these turned a search that ran out of budget on a 99px board into
    one that finishes it in a few thousand steps.
    """
    deck = Counter(FULL_DECK)
    cards = [pinned.get(i, read.card) for i, read in enumerate(reads)]

    variable = {u.index for u in unknowns}
    fixed = [i for i in range(len(reads)) if i not in variable]
    used = skeleton.placed(cards, fixed)
    if any(count > deck[card] for card, count in used.items()):
        # The confident reads alone already break the deck; only a level that
        # promotes some of them to variables can help, and it will have a
        # smaller `fixed` set when it does.
        return (), False

    # What each candidate would cost, worked out once.  A candidate the fixed
    # reads have already used up cannot appear in any solution, so it goes now
    # rather than being rediscovered at every node.
    options: list[list[tuple[int, Counter[int], float]]] = []
    for unknown in unknowns:
        scores = dict(reads[unknown.index].guess.ranking or ())
        viable = []
        for card in unknown.candidates:
            taken = skeleton.consumed(unknown.index, card)
            if any(used[c] + n > deck[c] for c, n in taken.items()):
                continue
            viable.append((card, taken, scores.get(card, 0.0)))
        if not viable:
            return (), False
        options.append(viable)

    order = sorted(range(len(unknowns)), key=lambda p: (len(options[p]), p))

    found: list[Possibility] = []
    steps = 0
    truncated = False

    def fits(taken: Counter[int]) -> bool:
        return all(used[c] + n <= deck[c] for c, n in taken.items())

    def descend(depth: int, score: float) -> None:
        nonlocal steps, truncated
        if len(found) >= MAX_POSSIBILITIES or steps >= MAX_STEPS:
            truncated = True
            return
        if depth == len(order):
            try:
                state = skeleton.build(cards)
            except InvalidBoard:
                return
            found.append(
                Possibility(
                    cards=tuple(cards[u.index] for u in unknowns),
                    state=state,
                    score=score,
                )
            )
            return

        position = order[depth]
        index = unknowns[position].index
        for card, taken, card_score in options[position]:
            steps += 1
            if steps >= MAX_STEPS:
                truncated = True
                return
            if not fits(taken):
                continue
            used.update(taken)
            cards[index] = card
            if all(
                any(fits(t) for _, t, _ in options[order[ahead]])
                for ahead in range(depth + 1, len(order))
            ):
                descend(depth + 1, score + card_score)
            used.subtract(taken)
        cards[index] = pinned.get(index, reads[index].card)

    descend(0, 0.0)
    found.sort(key=lambda p: -p.score)
    return tuple(found), truncated


def _summarise(
    unknowns: tuple[Unknown, ...],
    possibilities: tuple[Possibility, ...],
    level: int,
    truncated: bool,
) -> Resolution:
    settled: dict[int, int] = {}
    open_indices: list[int] = []
    for position, unknown in enumerate(unknowns):
        values = {p.cards[position] for p in possibilities}
        if len(values) == 1:
            settled[unknown.index] = next(iter(values))
        else:
            open_indices.append(unknown.index)

    return Resolution(
        state=possibilities[0].state,
        unknowns=unknowns,
        possibilities=possibilities,
        level=level,
        settled=settled,
        open=tuple(open_indices),
        truncated=truncated,
    )


def widest_options(
    skeleton: Skeleton,
    reads: Sequence,
    pinned: dict[int, int],
    index: int,
    *,
    level: int = 0,
) -> tuple[int, ...]:
    """Every card read ``index`` could be, ignoring how well it scored.

    The escape hatch behind "none of these": a question's shortlist comes from
    template scores, so a card that scored badly is not on it.  Only the asked
    read is opened up -- the others keep their usual candidates, which is both
    what "this one is something else" means and what keeps the search small.
    The deck rules out most of the pack, so this comes back short enough to
    put on buttons rather than being all forty cards.
    """
    others = {i: card for i, card in pinned.items() if i != index}
    unknowns = _unknowns(reads, LEVELS[level], others)
    if unknowns is None:
        return ()
    positions = [u.index for u in unknowns]
    if index not in positions:
        return ()
    unknowns = tuple(
        Unknown(u.index, u.where, tuple(sorted(set(FULL_DECK))), u.read)
        if u.index == index
        else u
        for u in unknowns
    )
    possibilities, _ = _search(skeleton, reads, unknowns, others)
    if not possibilities:
        return ()
    position = positions.index(index)
    seen: dict[int, float] = {}
    for possibility in possibilities:
        card = possibility.cards[position]
        seen[card] = max(seen.get(card, -1e9), possibility.score)
    return tuple(sorted(seen, key=lambda c: -seen[c]))


__all__ = [
    "MAX_QUESTIONS",
    "MAX_UNKNOWNS",
    "Possibility",
    "Resolution",
    "Skeleton",
    "Unknown",
    "Unresolvable",
    "resolve",
    "widest_options",
]
