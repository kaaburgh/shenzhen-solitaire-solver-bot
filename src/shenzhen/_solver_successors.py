"""Solver-only successor generation for positions already settled by the game.

The ordinary :func:`shenzhen.game.successors` path is deliberately general: it
runs the full automatic-resolution scan after every move. Solver states are
already settled, so most moves only need to inspect the one newly exposed top
before we can prove there is nothing automatic to do.

The search also rejects most generated children by canonical key. For stable
moves, this module can therefore prepare the exact child key and state
components without constructing a :class:`State`; the solver materializes only
children that survive deduplication.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from .cards import DRAGON_BASE, FLOWER, NUM_CARD_IDS
from .game import (
    Move,
    State,
    _with_foundation,
    _with_free_cell,
    apply_move,
    auto_resolve,
    legal_moves,
)

_PreparedParts = tuple[
    Sequence[tuple[int, ...]],
    tuple[int | None, ...],
    tuple[int, int, int],
    bool,
]
_PreparedSuccessor = tuple[
    Move,
    tuple,
    State | None,
    tuple[int, ...],
    _PreparedParts | None,
]


def _canonical_free(free: Sequence[int | None]) -> tuple[int, int, int]:
    """Canonical three-cell form used by the solver search key."""
    a, b, c = free
    a = NUM_CARD_IDS if a is None else a
    b = NUM_CARD_IDS if b is None else b
    c = NUM_CARD_IDS if c is None else c
    if a > b:
        a, b = b, a
    if b > c:
        b, c = c, b
    if a > b:
        a, b = b, a
    return a, b, c


def _search_key_parts(
    columns: Sequence[tuple[int, ...]],
    free: Sequence[int | None],
    foundations: tuple[int, int, int],
    flower: bool,
) -> tuple:
    """Canonical solver key from state components, without requiring a State."""
    return (*sorted(columns), *_canonical_free(free), *foundations, flower)


def _top_needs_auto(card: int, foundations: Sequence[int]) -> bool:
    """Whether an exposed tableau card would make ``auto_resolve`` do work."""
    if card == FLOWER:
        return True
    if not 0 <= card < DRAGON_BASE:
        return False

    suit = card // 9
    top = foundations[suit]
    if card != suit * 9 + top:
        return False

    rank = top + 1
    return rank <= 2 or min(foundations) >= rank - 1


def _settle_generated_move(state: State, move: Move) -> tuple[State, tuple[int, ...]]:
    """Settle one generated child of an already-settled solver state.

    A tableau-source move can only create new automatic work by exposing the
    source's new top. A free-cell-to-tableau move changes only the destination
    top. Manual foundation moves do not raise the minimum foundation from a
    settled state: if rank ``r`` was not already safe to collect then some
    other foundation is below ``r - 1``, and remains there after the move.

    Dragon collapse can expose several columns at once, so that rare move
    keeps the general full scan. When a dirty top really is automatic, the
    general resolver also remains responsible for the complete cascade and its
    collection order.
    """
    kind = move.kind

    if kind in ("tt", "tf", "tF"):
        source = state.columns[move.a]
        if source and _top_needs_auto(source[-1], state.foundations):
            return auto_resolve(state)
        return state, ()

    if kind == "ft":
        target = state.columns[move.b]
        if target and _top_needs_auto(target[-1], state.foundations):
            return auto_resolve(state)
        return state, ()

    if kind == "fF":
        return state, ()

    # Dragon collapse (and any future multi-site move) can expose more than one
    # place, so keep the conservative general resolver here.
    return auto_resolve(state)


def _fully_prepare_generated_move(
    state: State, move: Move
) -> tuple[tuple, State, tuple[int, ...], None]:
    """Materialize a child when automatic resolution may have real work."""
    nxt, _ = apply_move(state, move, auto=False, checked=False)
    nxt, collected = _settle_generated_move(nxt, move)
    return (
        _search_key_parts(nxt.columns, nxt.free, nxt.foundations, nxt.flower),
        nxt,
        collected,
        None,
    )


def _prepare_generated_move(
    state: State, parent_key: tuple, move: Move
) -> tuple[tuple, State | None, tuple[int, ...], _PreparedParts | None]:
    """Prepare one generated child, deferring State construction when safe."""
    columns = state.columns
    free = state.free
    foundations = state.foundations
    kind = move.kind

    if kind == "tt":
        src, dst, n = move.a, move.b, move.n
        source = columns[src]
        target = columns[dst]
        new_source = source[:-n]
        new_target = target + source[-n:]
        if new_source and _top_needs_auto(new_source[-1], foundations):
            return _fully_prepare_generated_move(state, move)

        new_columns = list(columns)
        new_columns[src] = new_source
        new_columns[dst] = new_target
        return (
            (*sorted(new_columns), *parent_key[8:]),
            None,
            (),
            (new_columns, free, foundations, state.flower),
        )

    if kind == "tf":
        src = move.a
        source = columns[src]
        new_source = source[:-1]
        # Generated tf moves always name the first interchangeable empty cell.
        new_free = _with_free_cell(free, move.b, source[-1])
        if new_source and _top_needs_auto(new_source[-1], foundations):
            return _fully_prepare_generated_move(state, move)

        new_columns = list(columns)
        new_columns[src] = new_source
        return (
            (*sorted(new_columns), *_canonical_free(new_free), *foundations, state.flower),
            None,
            (),
            (new_columns, new_free, foundations, state.flower),
        )

    if kind == "ft":
        slot, dst = move.a, move.b
        card = free[slot]
        target = columns[dst]
        new_target = target + (card,)
        new_free = _with_free_cell(free, slot, None)

        # A suit card in a settled free cell cannot already be auto-collectable;
        # foundations do not change here. The flower is different: it only
        # jumps automatically after being exposed on the tableau.
        if card == FLOWER:
            return _fully_prepare_generated_move(state, move)

        new_columns = list(columns)
        new_columns[dst] = new_target
        return (
            (*sorted(new_columns), *_canonical_free(new_free), *foundations, state.flower),
            None,
            (),
            (new_columns, new_free, foundations, state.flower),
        )

    if kind == "tF":
        src = move.a
        source = columns[src]
        card = source[-1]
        new_source = source[:-1]
        new_foundations = _with_foundation(foundations, card // 9, card % 9 + 1)
        if new_source and _top_needs_auto(new_source[-1], new_foundations):
            return _fully_prepare_generated_move(state, move)

        new_columns = list(columns)
        new_columns[src] = new_source
        return (
            (*sorted(new_columns), *parent_key[8:11], *new_foundations, state.flower),
            None,
            (),
            (new_columns, free, new_foundations, state.flower),
        )

    if kind == "fF":
        slot = move.a
        card = free[slot]
        new_free = _with_free_cell(free, slot, None)
        new_foundations = _with_foundation(foundations, card // 9, card % 9 + 1)
        return (
            (*parent_key[:8], *_canonical_free(new_free), *new_foundations, state.flower),
            None,
            (),
            (columns, new_free, new_foundations, state.flower),
        )

    # Dragon collapse can expose several columns at once. Keep it, and any
    # future move kind, on the conservative materialized path.
    return _fully_prepare_generated_move(state, move)


def prepared_successors_from_settled(
    state: State, parent_key: tuple
) -> Iterator[_PreparedSuccessor]:
    """Generate exact child keys before constructing stable child States.

    ``parent_key`` must be the canonical solver key for ``state``. Stable moves
    yield ``nxt=None`` plus components sufficient to materialize the State if
    the search accepts the key. Moves that can trigger automatic resolution
    are materialized immediately and carry ``parts=None``.
    """
    for move in legal_moves(state):
        key, nxt, collected, parts = _prepare_generated_move(state, parent_key, move)
        yield move, key, nxt, collected, parts


def _materialize_prepared(parts: _PreparedParts) -> State:
    """Construct a State for a prepared child that survived deduplication."""
    columns, free, foundations, flower = parts
    return State(tuple(columns), free, foundations, flower)


def successors_from_settled(
    state: State,
) -> Iterator[tuple[Move, State, tuple[int, ...]]]:
    """Generate fully materialized successors for callers outside the search."""
    parent_key = _search_key_parts(state.columns, state.free, state.foundations, state.flower)
    for move, _, nxt, collected, parts in prepared_successors_from_settled(state, parent_key):
        if nxt is None:
            assert parts is not None
            nxt = _materialize_prepared(parts)
        yield move, nxt, collected
