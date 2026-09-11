"""Solver-only successor generation for positions already settled by the game.

The ordinary :func:`shenzhen.game.successors` path is deliberately general: it
runs the full automatic-resolution scan after every move.  Solver states are
already settled, so most moves only need to inspect the one newly exposed top
before we can prove there is nothing automatic to do.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from .cards import DRAGON_BASE, FLOWER
from .game import Move, State, apply_move, auto_resolve, legal_moves


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
    source's new top.  A free-cell-to-tableau move changes only the destination
    top.  Manual foundation moves do not raise the minimum foundation from a
    settled state: if rank ``r`` was not already safe to collect then some
    other foundation is below ``r - 1``, and remains there after the move.

    Dragon collapse can expose several columns at once, so that rare move
    keeps the general full scan.  When a dirty top really is automatic, the
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


def successors_from_settled(
    state: State,
) -> Iterator[tuple[Move, State, tuple[int, ...]]]:
    """Generate successors when ``state`` is already auto-resolved.

    This is the solver hot path.  ``apply_move(auto=False)`` performs only the
    requested player move; ``_settle_generated_move`` then checks the small set
    of positions that can have become newly automatic.
    """
    for move in legal_moves(state):
        nxt, _ = apply_move(state, move, auto=False, checked=False)
        nxt, collected = _settle_generated_move(nxt, move)
        yield move, nxt, collected
