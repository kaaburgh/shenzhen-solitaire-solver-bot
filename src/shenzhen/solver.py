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

from .cards import NUM_CARD_IDS, SUITS, is_locked, locked_colour
from .game import Move, State, successors

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


def _search_key(state: State) -> tuple:
    """Canonical position identity shaped for the solver's hot dictionaries.

    ``State.key`` groups the same components into nested tuples.  Flattening
    them means dict/set hashing does not re-enter wrapper tuples around the
    columns, free cells and foundations for every lookup.  Free cells are
    always three entries, so a tiny sorting network also avoids allocating the
    generator and temporary tuple used by the general-purpose state key.
    """
    a, b, c = state.free
    a = NUM_CARD_IDS if a is None else a
    b = NUM_CARD_IDS if b is None else b
    c = NUM_CARD_IDS if c is None else c
    if a > b:
        a, b = b, a
    if b > c:
        b, c = c, b
    if a > b:
        a, b = b, a
    return (*sorted(state.columns), a, b, c, *state.foundations, state.flower)


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
    # key -> (cost so far, parent key, move that got here, state, collected)
    seen: dict[tuple, tuple[int, tuple | None, Move | None, State, tuple[int, ...]]] = {
        start_key: (0, None, None, state, ())
    }
    # A canonical position has the same successors regardless of how cheaply
    # it was reached.  Once expanded, reopening it can only regenerate work we
    # have already done; keep lower-cost updates only while it is still queued.
    expanded: set[tuple] = set()
    counter = 0
    queue: list[tuple[int, int, int, tuple]] = [(heuristic(state) * HEURISTIC_WEIGHT, 0, 0, start_key)]

    nodes = 0
    exhausted = True

    while queue:
        if nodes >= max_nodes or time.monotonic() - started > time_limit:
            exhausted = False
            break

        _, g, _, key = heapq.heappop(queue)
        if key in expanded:
            continue
        entry = seen[key]
        if g > entry[0]:
            continue  # stale queue entry
        expanded.add(key)
        current = entry[3]
        nodes += 1

        for move, nxt, collected in successors(current):
            nxt_key = _search_key(nxt)
            cost = g + 1
            if nxt_key in expanded:
                continue
            known = seen.get(nxt_key)
            if known is not None and known[0] <= cost:
                continue
            seen[nxt_key] = (cost, key, move, nxt, collected)

            if nxt.is_won:
                return SolveResult(
                    Status.SOLVED,
                    _reconstruct(seen, nxt_key),
                    nodes,
                    time.monotonic() - started,
                )

            counter += 1
            heapq.heappush(
                queue, (cost + HEURISTIC_WEIGHT * heuristic(nxt), cost, counter, nxt_key)
            )

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
