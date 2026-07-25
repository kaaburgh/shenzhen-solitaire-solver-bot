"""Rules engine for Shenzhen Solitaire.

The board:

* 8 tableau columns (40 cards dealt 5 per column);
* 3 free cells, each holding a single card, or locked by four collapsed
  dragons;
* 1 flower slot;
* 3 foundations, one per suit, built up from 1 to 9.

Player moves are: tableau -> tableau (a single card or a valid run), tableau
-> free cell, free cell -> tableau, a card -> its foundation, and collapsing
four exposed dragons of one colour into a free cell.

The game itself performs two things automatically, and so does this engine
(see :func:`auto_resolve`): the flower jumps to its slot as soon as it is
exposed, and a card is collected onto its foundation as soon as doing so
cannot possibly cost the player a move later.  Those never appear in the move
list the solver returns -- they are not things a player does.
"""

from __future__ import annotations

from typing import Iterator, NamedTuple, Sequence

from .cards import (
    DRAGON_BASE,
    FLOWER,
    FULL_DECK,
    SUITS,
    is_locked,
    is_suit_card,
    locked_cell,
    locked_colour,
    make_dragon,
    rank_of,
    suit_of,
)

NUM_COLUMNS = 8
NUM_FREE_CELLS = 3

# Sort key for an empty free cell; larger than any card id so that the
# canonical ordering puts empty cells last.
_EMPTY_SORT_KEY = 99


class Move(NamedTuple):
    """A single player move.

    ``kind`` is one of:

    ``tt``  tableau -> tableau; ``a`` source column, ``b`` destination column,
            ``n`` number of cards moved
    ``tf``  tableau -> free cell; ``a`` source column
    ``ft``  free cell -> tableau; ``a`` cell index, ``b`` destination column
    ``tF``  tableau -> foundation; ``a`` source column
    ``fF``  free cell -> foundation; ``a`` cell index
    ``dr``  collapse dragons; ``a`` dragon colour
    """

    kind: str
    a: int = 0
    b: int = 0
    n: int = 1


class State(NamedTuple):
    """An immutable board position.

    Within a column, index ``0`` is the card at the back of the stack (drawn
    highest on screen) and index ``-1`` is the exposed card the player can
    grab.

    A plain tuple rather than a dataclass: the solver creates hundreds of
    thousands of these, and tuple construction is several times cheaper.
    """

    columns: tuple[tuple[int, ...], ...]
    free: tuple[int | None, ...]
    foundations: tuple[int, int, int]
    flower: bool

    # -- canonical form ----------------------------------------------------

    def key(self) -> tuple:
        """Hashable identity of the position.

        Columns are interchangeable and so are free cells, so both are sorted:
        two positions that differ only by which column a stack sits in are the
        same position as far as search is concerned.
        """
        free = tuple(sorted(_EMPTY_SORT_KEY if c is None else c for c in self.free))
        return (tuple(sorted(self.columns)), free, self.foundations, self.flower)

    # -- queries -----------------------------------------------------------

    @property
    def is_won(self) -> bool:
        return (
            self.foundations == (9, 9, 9)
            and self.flower
            and all(is_locked(c) for c in self.free)
        )

    def cards_left(self) -> int:
        """Number of cards still in play (columns + unlocked free cells)."""
        return sum(len(c) for c in self.columns) + sum(
            1 for c in self.free if c is not None and not is_locked(c)
        )


# --- construction ----------------------------------------------------------


def empty_state() -> State:
    return State(
        columns=tuple(() for _ in range(NUM_COLUMNS)),
        free=(None,) * NUM_FREE_CELLS,
        foundations=(0, 0, 0),
        flower=False,
    )


def deal(seed: int | None = None) -> State:
    """Deal a fresh game.  Used by the tests and the ``--selftest`` CLI."""
    import random

    deck = list(FULL_DECK)
    random.Random(seed).shuffle(deck)
    columns = tuple(tuple(deck[i * 5 : (i + 1) * 5]) for i in range(NUM_COLUMNS))
    return auto_resolve(
        State(columns=columns, free=(None,) * NUM_FREE_CELLS, foundations=(0, 0, 0), flower=False)
    )[0]


class InvalidBoard(ValueError):
    """The described position could not exist in a real game."""


def validate(state: State) -> None:
    """Check that a position uses exactly the 40 cards of the deck."""
    counts: dict[int, int] = {}

    def bump(card: int) -> None:
        counts[card] = counts.get(card, 0) + 1

    for col in state.columns:
        for card in col:
            bump(card)
    for cell in state.free:
        if cell is None:
            continue
        if is_locked(cell):
            for _ in range(4):
                bump(make_dragon(locked_colour(cell)))
        else:
            bump(cell)
    for suit, top in zip(SUITS, state.foundations):
        if not 0 <= top <= 9:
            raise InvalidBoard(f"foundation out of range: {top}")
        for rank in range(1, top + 1):
            bump(suit * 9 + rank - 1)
    if state.flower:
        bump(FLOWER)

    expected: dict[int, int] = {}
    for card in FULL_DECK:
        expected[card] = expected.get(card, 0) + 1

    if counts != expected:
        from .cards import card_code

        missing = [
            f"{card_code(c)}x{expected[c] - counts.get(c, 0)}"
            for c in sorted(expected)
            if counts.get(c, 0) < expected[c]
        ]
        extra = [
            f"{card_code(c)}x{counts[c] - expected.get(c, 0)}"
            for c in sorted(counts)
            if counts[c] > expected.get(c, 0)
        ]
        parts = []
        if missing:
            parts.append("missing " + ", ".join(missing))
        if extra:
            parts.append("duplicated " + ", ".join(extra))
        raise InvalidBoard("; ".join(parts))


# --- automatic behaviour ---------------------------------------------------


def collectable_ids(foundations: Sequence[int]) -> set[int]:
    """The (at most three) cards the game would pick up by itself right now.

    Aces and twos always go up.  Anything higher waits until every foundation
    has reached ``rank - 1``, at which point no card of rank ``rank - 1`` is
    left on the table and nothing can still need to be stacked onto this card.

    Returning the set rather than testing card by card keeps the hot loops to
    a set lookup.
    """
    lowest = min(foundations)
    ready = set()
    for suit in (0, 1, 2):
        top = foundations[suit]
        if top >= 9:
            continue
        rank = top + 1
        if rank <= 2 or lowest >= rank - 1:
            ready.add(suit * 9 + top)  # the card of that suit and rank
    return ready


def is_safe_collect(card: int, foundations: Sequence[int]) -> bool:
    """Would the game itself move ``card`` onto its foundation?"""
    return card in collectable_ids(foundations)


def auto_resolve(state: State) -> tuple[State, tuple[int, ...]]:
    """Apply the game's automatic moves until the position is stable.

    Returns the settled position and the cards that were collected, in order,
    so the bot can mention them ("autocollect: G3, R4").
    """
    # Most positions are already settled; skip the copying entirely for those.
    ready = collectable_ids(state.foundations)
    pending = False
    for col in state.columns:
        if col:
            top = col[-1]
            if top == FLOWER or top in ready:
                pending = True
                break
    if not pending:
        for cell in state.free:
            if cell is not None and cell in ready:
                pending = True
                break
    if not pending:
        return state, ()

    columns = [list(c) for c in state.columns]
    free = list(state.free)
    foundations = list(state.foundations)
    flower = state.flower
    collected: list[int] = []

    changed = True
    while changed:
        changed = False
        ready = collectable_ids(foundations)

        for col in columns:
            if col and col[-1] == FLOWER:
                col.pop()
                flower = True
                collected.append(FLOWER)
                changed = True

        for col in columns:
            while col and col[-1] in ready:
                card = col.pop()
                foundations[card // 9] = card % 9 + 1
                collected.append(card)
                changed = True
                ready = collectable_ids(foundations)

        for i, cell in enumerate(free):
            if cell is not None and cell in ready:
                foundations[cell // 9] = cell % 9 + 1
                collected.append(cell)
                free[i] = None
                changed = True
                ready = collectable_ids(foundations)

    settled = State(
        columns=tuple(tuple(c) for c in columns),
        free=tuple(free),
        foundations=(foundations[0], foundations[1], foundations[2]),
        flower=flower,
    )
    return settled, tuple(collected)


# --- move generation -------------------------------------------------------


def max_run_length(column: Sequence[int]) -> int:
    """How many cards can be picked up from the exposed end of ``column``.

    A run descends by one rank per card and never repeats a suit between
    neighbours.  Dragons and the flower only ever move on their own.
    """
    n = len(column)
    if n == 0:
        return 0
    lower = column[-1]
    if lower >= DRAGON_BASE:
        return 1
    length = 1
    while length < n:
        upper = column[n - 1 - length]
        if upper >= DRAGON_BASE:
            break
        # one rank higher and a different suit
        if upper % 9 != lower % 9 + 1 or upper // 9 == lower // 9:
            break
        lower = upper
        length += 1
    return length


def can_stack(card: int, onto: int) -> bool:
    return (
        is_suit_card(card)
        and is_suit_card(onto)
        and rank_of(onto) == rank_of(card) + 1
        and suit_of(onto) != suit_of(card)
    )


def can_collapse_dragons(state: State, colour: int) -> bool:
    dragon = make_dragon(colour)
    exposed = sum(1 for cell in state.free if cell == dragon)
    exposed += sum(1 for col in state.columns if col and col[-1] == dragon)
    if exposed != 4:
        return False
    return any(cell is None or cell == dragon for cell in state.free)


def legal_moves(state: State) -> list[Move]:
    """Every move the player could make from ``state``."""
    moves: list[Move] = []
    columns = state.columns
    free = state.free
    foundations = state.foundations

    # Collapsing dragons frees a cell and removes four cards; try it first.
    for colour in SUITS:
        if can_collapse_dragons(state, colour):
            moves.append(Move("dr", colour))

    # Manual foundation moves.  The game only collects automatically when it
    # is provably safe, but the player may always drag a card up by hand.
    # (``foundations[suit] == rank - 1`` is ``foundations[card // 9] == card % 9``.)
    for i, col in enumerate(columns):
        if col:
            top = col[-1]
            if top < DRAGON_BASE and foundations[top // 9] == top % 9:
                moves.append(Move("tF", i))
    for i, cell in enumerate(free):
        if cell is not None and 0 <= cell < DRAGON_BASE and foundations[cell // 9] == cell % 9:
            moves.append(Move("fF", i))

    # Empty columns are interchangeable; only the first one is a destination.
    first_empty_column = next((i for i, c in enumerate(columns) if not c), None)

    # Per column: the run that can be picked up, and what the exposed card
    # accepts.  Computed once instead of inside the src x dst loop below.
    runs: list[int] = []
    accepts: list[tuple[int, int] | None] = []  # (required rank, forbidden suit)
    for col in columns:
        if not col:
            runs.append(0)
            accepts.append(None)
            continue
        runs.append(max_run_length(col))
        top = col[-1]
        if top < DRAGON_BASE:
            accepts.append((top % 9, top // 9))  # rank_of(top) - 1, suit_of(top)
        else:
            accepts.append(None)

    # Tableau -> tableau.
    for src, col in enumerate(columns):
        run = runs[src]
        if run == 0:
            continue
        length = len(col)
        top = col[-1]
        # A run's ranks increase by one per extra card taken, so the number of
        # cards needed to reach a given rank follows directly -- no scanning.
        base_rank = top % 9 + 1 if top < DRAGON_BASE else None

        for dst in range(NUM_COLUMNS):
            if dst == src:
                continue
            target = columns[dst]
            if target:
                need = accepts[dst]
                if need is None or base_rank is None:
                    continue
                need_rank, forbidden_suit = need
                n = need_rank - base_rank + 1
                if 1 <= n <= run:
                    head = col[length - n]
                    if head // 9 != forbidden_suit:
                        moves.append(Move("tt", src, dst, n))
            elif dst == first_empty_column:
                for n in range(1, run + 1):
                    # Relocating a whole column into an empty one changes
                    # nothing but the column index.
                    if n != length:
                        moves.append(Move("tt", src, dst, n))

    # Free cell -> tableau.
    for i, cell in enumerate(free):
        if cell is None or cell < 0:
            continue
        cell_rank = cell % 9 + 1 if cell < DRAGON_BASE else None
        cell_suit = cell // 9
        for dst in range(NUM_COLUMNS):
            if not columns[dst]:
                if dst == first_empty_column:
                    moves.append(Move("ft", i, dst))
                continue
            need = accepts[dst]
            if need is None or cell_rank is None:
                continue
            if cell_rank == need[0] and cell_suit != need[1]:
                moves.append(Move("ft", i, dst))

    # Tableau -> free cell.  All empty cells are interchangeable, so only the
    # first one is offered.
    empty_cell = next((i for i, c in enumerate(free) if c is None), None)
    if empty_cell is not None:
        for src, col in enumerate(columns):
            if col:
                moves.append(Move("tf", src, empty_cell))

    return moves


class IllegalMove(ValueError):
    pass


def _with_foundation(foundations: tuple[int, int, int], suit: int, rank: int) -> tuple[int, int, int]:
    if suit == 0:
        return (rank, foundations[1], foundations[2])
    if suit == 1:
        return (foundations[0], rank, foundations[2])
    return (foundations[0], foundations[1], rank)


def apply_move(
    state: State, move: Move, *, auto: bool = True, checked: bool = True
) -> tuple[State, tuple[int, ...]]:
    """Apply ``move`` and settle the board.  Returns (new state, autocollected).

    Only the columns and cells the move touches are rebuilt; everything else
    is shared with ``state``.

    ``checked=False`` skips re-verifying legality and is only for moves that
    came straight out of :func:`legal_moves`; the search runs millions of
    these and the checks are the expensive part.
    """
    columns = list(state.columns)
    free = list(state.free)
    foundations = state.foundations

    if move.kind == "tt":
        src, dst, n = move.a, move.b, move.n
        source = columns[src]
        target = columns[dst]
        if checked:
            if n < 1 or len(source) < n:
                raise IllegalMove(f"column {src} has fewer than {n} cards")
            if n > max_run_length(source):
                raise IllegalMove("those cards do not form a movable run")
            if target and not can_stack(source[-n], target[-1]):
                raise IllegalMove("cards do not stack there")
        columns[src] = source[:-n]
        columns[dst] = target + source[-n:]

    elif move.kind == "tf":
        src = move.a
        source = columns[src]
        if not source:
            raise IllegalMove(f"column {src} is empty")
        slot = next((i for i, c in enumerate(free) if c is None), None)
        if slot is None:
            raise IllegalMove("no free cell available")
        free[slot] = source[-1]
        columns[src] = source[:-1]

    elif move.kind == "ft":
        slot, dst = move.a, move.b
        card = free[slot]
        target = columns[dst]
        if checked:
            if card is None or is_locked(card):
                raise IllegalMove(f"free cell {slot} holds nothing movable")
            if target and not can_stack(card, target[-1]):
                raise IllegalMove("card does not stack there")
        columns[dst] = target + (card,)
        free[slot] = None

    elif move.kind == "tF":
        src = move.a
        source = columns[src]
        if not source:
            raise IllegalMove(f"column {src} is empty")
        card = source[-1]
        if not is_suit_card(card) or foundations[suit_of(card)] != rank_of(card) - 1:
            raise IllegalMove("card cannot go to its foundation yet")
        columns[src] = source[:-1]
        foundations = _with_foundation(foundations, suit_of(card), rank_of(card))

    elif move.kind == "fF":
        slot = move.a
        card = free[slot]
        if card is None or is_locked(card) or not is_suit_card(card):
            raise IllegalMove(f"free cell {slot} holds nothing collectable")
        if foundations[suit_of(card)] != rank_of(card) - 1:
            raise IllegalMove("card cannot go to its foundation yet")
        free[slot] = None
        foundations = _with_foundation(foundations, suit_of(card), rank_of(card))

    elif move.kind == "dr":
        colour = move.a
        if checked and not can_collapse_dragons(state, colour):
            raise IllegalMove("those dragons cannot be collapsed")
        dragon = make_dragon(colour)
        slot = next((i for i, c in enumerate(free) if c == dragon), None)
        if slot is None:
            slot = next(i for i, c in enumerate(free) if c is None)
        for i, cell in enumerate(free):
            if cell == dragon:
                free[i] = None
        for i, col in enumerate(columns):
            if col and col[-1] == dragon:
                columns[i] = col[:-1]
        free[slot] = locked_cell(colour)

    else:  # pragma: no cover -- guarded by the Move constructors above
        raise IllegalMove(f"unknown move kind: {move.kind!r}")

    nxt = State(
        columns=tuple(columns),
        free=tuple(free),
        foundations=foundations,
        flower=state.flower,
    )
    if auto:
        return auto_resolve(nxt)
    return nxt, ()


def successors(state: State) -> Iterator[tuple[Move, State, tuple[int, ...]]]:
    for move in legal_moves(state):
        nxt, collected = apply_move(state, move, checked=False)
        yield move, nxt, collected
