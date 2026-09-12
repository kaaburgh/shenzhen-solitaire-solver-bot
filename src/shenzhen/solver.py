"""Search for a winning line of play.

Weighted A* over canonical positions.  The weight biases the search hard
towards the goal -- we want *a* win quickly, not the shortest one -- while the
``g`` term still keeps the returned line from wandering.

Three outcomes matter to the bot and are reported separately:

``SOLVED``      a full winning line was found
``UNSOLVABLE``  the search exhausted every reachable position; the deal is dead
``UNKNOWN``     the node or time budget ran out first
"""

from __future__ import annotations

import heapq
import time
from dataclasses import dataclass, field
from enum import StrEnum

from ._solver_successors import (
    _materialize_prepared,
    _search_key_parts,
    prepared_successors_from_settled,
)
from .cards import SUITS, is_locked, locked_colour
from .game import Move, State, auto_resolve, successors

DEFAULT_MAX_NODES = 400_000
DEFAULT_TIME_LIMIT = 20.0
# Tuned on 200 random deals: at 8 the worst case drops from ~10s to ~2s while
# the returned lines stay within a move of what a weight of 3 finds.
HEURISTIC_WEIGHT = 8


class Status(StrEnum):
    SOLVED = "solved"
    UNSOLVABLE = "unsolvable"
    UNKNOWN = "unknown"


@dataclass
class Step:
    """One player move plus whatever the game collected right after it."""

    move: Move
    state_before: State
    state_after: State
    collected: tuple[int, ...] = ()


@dataclass
class SolveResult:
    status: Status
    steps: list[Step] = field(default_factory=list)
    nodes: int = 0
    elapsed: float = 0.0

    @property
    def solved(self) -> bool:
        return self.status is Status.SOLVED


def heuristic(state: State) -> int:
    """Optimistic-ish estimate of the work left.

    Not admissible, and deliberately so: it is a ranking function, and the
    penalties are what stop the search from burying the cards it needs.
    """
    foundations = state.foundations
    score = 2 * (27 - sum(foundations))

    # The three cards the foundations want next, as raw ids.
    wanted = {suit * 9 + top for suit, top in enumerate(foundations) if top < 9}
    for col in state.columns:
        depth = len(col)
        for idx, card in enumerate(col):
            if card in wanted:
                # Every card sitting on top of a card we want next costs a move.
                score += depth - idx - 1

    collapsed = {locked_colour(cell) for cell in state.free if is_locked(cell)}
    score += 2 * len(set(SUITS) - collapsed)

    score += sum(1 for cell in state.free if cell is not None and not is_locked(cell))
    if not state.flower:
        score += 1

    return score


def _wanted_in_column(column: tuple[int, ...], foundations: tuple[int, int, int]) -> int:
    """How many of the three foundation-next cards are in ``column``."""
    f0, f1, f2 = foundations
    return (
        (f0 < 9 and f0 in column)
        + (f1 < 9 and 9 + f1 in column)
        + (f2 < 9 and 18 + f2 in column)
    )


def _heuristic_after_move(
    before: State,
    before_score: int,
    move: Move,
    after: State,
    collected: tuple[int, ...],
) -> int:
    """Update the heuristic from a generated move when that is cheaper.

    With no automatic collection and unchanged foundations, tableau moves have
    a very small exact delta.  Moving ``n`` cards removes ``n`` blockers from
    every wanted card left in the source and adds ``n`` blockers to every
    wanted card already in the destination.  Moving through a free cell adds
    or removes its one occupied-cell penalty as well.

    Foundation moves, dragon collapse, and automatic collection can change
    more of the score at once, so those comparatively rare children keep the
    full calculation.
    """
    if collected or before.foundations != after.foundations:
        return heuristic(after)

    kind = move.kind
    foundations = before.foundations
    if kind == "tt":
        n = move.n
        return before_score + n * (
            _wanted_in_column(before.columns[move.b], foundations)
            - _wanted_in_column(after.columns[move.a], foundations)
        )
    if kind == "tf":
        return before_score + 1 - _wanted_in_column(after.columns[move.a], foundations)
    if kind == "ft":
        return before_score - 1 + _wanted_in_column(before.columns[move.b], foundations)

    return heuristic(after)


def _search_key(state: State) -> tuple:
    """Canonical position identity shaped for the solver's hot dictionaries."""
    return _search_key_parts(state.columns, state.free, state.foundations, state.flower)


def solve(
    state: State,
    *,
    max_nodes: int = DEFAULT_MAX_NODES,
    time_limit: float = DEFAULT_TIME_LIMIT,
) -> SolveResult:
    """Search for a win from ``state``."""
    started = time.monotonic()

    if state.is_won:
        return SolveResult(Status.SOLVED, [], 0, 0.0)

    start_key = _search_key(state)
    # The normal bot/parser path hands us a settled position.  Keep solve()'s
    # old behavior for a direct caller that does not: use the general successor
    # path for that first expansion, after which every generated child is
    # settled and can use the solver fast path.
    start_settled = auto_resolve(state)[0] is state

    # key -> (cost so far, parent key, move that got here, state, collected)
    seen: dict[tuple, tuple[int, tuple | None, Move | None, State, tuple[int, ...]]] = {
        start_key: (0, None, None, state, ())
    }
    # A canonical position has the same successors regardless of how cheaply
    # it was reached.  Once expanded, reopening it can only regenerate work we
    # have already done; keep lower-cost updates only while it is still queued.
    expanded: set[tuple] = set()
    counter = 0
    start_h = heuristic(state)
    queue: list[tuple[int, int, int, tuple]] = [
        (start_h * HEURISTIC_WEIGHT, 0, 0, start_key)
    ]
    # Column tuples are immutable and recur across many canonical positions.
    # Keep this cache local to one solve so it cannot retain old games.
    run_cache: dict[tuple[int, ...], int] = {}

    nodes = 0
    exhausted = True

    while queue:
        if nodes >= max_nodes or time.monotonic() - started > time_limit:
            exhausted = False
            break

        priority, g, _, key = heapq.heappop(queue)
        if key in expanded:
            continue
        entry = seen[key]
        if g > entry[0]:
            continue  # stale queue entry
        expanded.add(key)
        current = entry[3]
        current_h = (priority - g) // HEURISTIC_WEIGHT
        nodes += 1

        if key == start_key and not start_settled:
            child_iter = (
                (move, _search_key(nxt), nxt, collected, None)
                for move, nxt, collected in successors(current)
            )
        else:
            child_iter = prepared_successors_from_settled(current, key, run_cache)

        for move, nxt_key, nxt, collected, parts in child_iter:
            cost = g + 1
            if nxt_key in expanded:
                continue
            known = seen.get(nxt_key)
            if known is not None and known[0] <= cost:
                continue

            # Stable generated moves carry only the components needed for their
            # canonical key until they survive deduplication. This is where the
            # majority of rejected children avoid constructing a State.
            if nxt is None:
                assert parts is not None
                nxt = _materialize_prepared(parts)

            seen[nxt_key] = (cost, key, move, nxt, collected)

            if nxt.is_won:
                return SolveResult(
                    Status.SOLVED,
                    _reconstruct(seen, nxt_key),
                    nodes,
                    time.monotonic() - started,
                )

            counter += 1
            nxt_h = _heuristic_after_move(current, current_h, move, nxt, collected)
            heapq.heappush(queue, (cost + HEURISTIC_WEIGHT * nxt_h, cost, counter, nxt_key))

    status = Status.UNSOLVABLE if exhausted else Status.UNKNOWN
    return SolveResult(status, [], nodes, time.monotonic() - started)


def _reconstruct(seen: dict, key: tuple) -> list[Step]:
    steps: list[Step] = []
    while True:
        cost, parent_key, move, state, collected = seen[key]
        if parent_key is None or move is None:
            break
        parent_state = seen[parent_key][3]
        steps.append(Step(move=move, state_before=parent_state, state_after=state, collected=collected))
        key = parent_key
    steps.reverse()
    return steps
