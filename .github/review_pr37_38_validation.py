"""Independent validation for stacked PRs #37 and #38.

Review-only artifact. This branch must not be merged.
"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

import shenzhen.solver as solver_mod
from shenzhen._solver_successors import (
    prepared_successors_from_settled,
    proof_successor_keys_from_key,
)
from shenzhen.cards import NUM_CARD_IDS
from shenzhen.game import State, deal
from shenzhen.solver import (
    Status,
    _dragon_collapse_reachable,
    _has_storage_pressure_for_dragon_proof,
    _search_key,
    solve,
)
from shenzhen.textio import parse_board


HARD_PATH = Path("tests/solver_cases/hard_dragon_deadlock.txt")


def _state_from_key(key: tuple) -> State:
    free = tuple(None if cell == NUM_CARD_IDS else cell for cell in key[8:11])
    return State(key[:8], free, key[11:14], key[14])


def validate_hard_graph_successor_equivalence() -> None:
    state = parse_board(HARD_PATH.read_text())
    start_key = _search_key(state)
    seen = {start_key}
    stack = [start_key]
    general_cache: dict[tuple[int, ...], int] = {}
    proof_cache: dict[tuple[int, ...], int] = {}
    nodes = 0
    started = time.monotonic()

    while stack:
        key = stack.pop()
        current = _state_from_key(key)
        assert _search_key(current) == key

        general = list(prepared_successors_from_settled(current, key, general_cache))
        expected = [
            None if move.kind == "dr" else child_key
            for move, child_key, _, _, _ in general
        ]
        got = list(proof_successor_keys_from_key(key, proof_cache))
        assert Counter(got) == Counter(expected), (
            f"successor mismatch at hard-graph node {nodes}: "
            f"expected={Counter(expected)} got={Counter(got)}"
        )
        assert None not in got, f"unexpected collapse in hard graph at node {nodes}"

        nodes += 1
        for child_key in got:
            assert child_key is not None
            if child_key not in seen:
                seen.add(child_key)
                stack.append(child_key)

    elapsed = time.monotonic() - started
    assert nodes == 287_972, nodes
    print(
        "HARD_EQUIVALENCE "
        f"nodes={nodes} exact_successor_multisets=yes elapsed={elapsed:.6f}s"
    )


def collect_solvable_trigger_states() -> list[tuple[int, State]]:
    trigger_states: dict[tuple, tuple[int, State]] = {}
    solved = unsolvable = unknown = 0
    started = time.monotonic()

    for seed in range(200):
        result = solve(deal(seed), max_nodes=400_000, time_limit=20.0)
        if result.status is Status.SOLVED:
            solved += 1
            states = [result.steps[0].state_before] if result.steps else [deal(seed)]
            states.extend(step.state_after for step in result.steps)
            for state in states:
                if _has_storage_pressure_for_dragon_proof(state):
                    trigger_states.setdefault(_search_key(state), (seed, state))
        elif result.status is Status.UNSOLVABLE:
            unsolvable += 1
        else:
            unknown += 1

    print(
        "DEAL200 "
        f"solved={solved} unsolvable={unsolvable} unknown={unknown} "
        f"unique_solvable_trigger_states={len(trigger_states)} "
        f"elapsed={time.monotonic() - started:.6f}s"
    )
    return list(trigger_states.values())


def validate_solvable_trigger_states(states: list[tuple[int, State]]) -> None:
    if not states:
        raise AssertionError("200 solved-deal paths produced no trigger states")

    sampled = states[:60]
    solved = unknown = 0
    proof_true = proof_unknown = 0
    max_proof_nodes = 0
    max_solve_elapsed = 0.0
    started = time.monotonic()

    for seed, state in sampled:
        proof_started = time.monotonic()
        reachable, proof_nodes = _dragon_collapse_reachable(
            state,
            max_nodes=300_000,
            deadline=proof_started + 120.0,
        )
        if reachable is False:
            raise AssertionError(
                f"false UNSOLVABLE proof on known-solvable trigger state from seed {seed}"
            )
        if reachable is True:
            proof_true += 1
        else:
            proof_unknown += 1
        max_proof_nodes = max(max_proof_nodes, proof_nodes)

        result = solve(state, max_nodes=400_000, time_limit=120.0)
        if result.status is Status.UNSOLVABLE:
            raise AssertionError(
                f"solve returned false UNSOLVABLE on known-solvable trigger state from seed {seed}"
            )
        if result.status is Status.SOLVED:
            solved += 1
        else:
            unknown += 1
        max_solve_elapsed = max(max_solve_elapsed, result.elapsed)

    print(
        "SOLVABLE_TRIGGER_SAMPLE "
        f"sampled={len(sampled)} proof_reachable={proof_true} "
        f"proof_unknown={proof_unknown} solved={solved} unknown={unknown} "
        f"max_proof_nodes={max_proof_nodes} "
        f"max_solve_elapsed={max_solve_elapsed:.6f}s "
        f"elapsed={time.monotonic() - started:.6f}s"
    )


def validate_budget_semantics() -> None:
    state = parse_board(HARD_PATH.read_text())
    original = solver_mod._dragon_collapse_reachable
    observed: list[tuple[bool | None, int]] = []

    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        observed.append(result)
        return result

    solver_mod._dragon_collapse_reachable = wrapped
    try:
        result = solver_mod.solve(state, max_nodes=100, time_limit=120.0)
    finally:
        solver_mod._dragon_collapse_reachable = original

    assert observed == [(None, 100)], observed
    assert result.status is Status.UNKNOWN, result
    assert result.nodes == 100, result
    print(
        "NODE_BUDGET_OBSERVATION "
        f"proof_nodes={observed[0][1]} astar_reported_nodes={result.nodes} "
        f"configured_max_nodes=100 effective_expansions_at_least="
        f"{observed[0][1] + result.nodes}"
    )

    observed.clear()
    solver_mod._dragon_collapse_reachable = wrapped
    try:
        timed = solver_mod.solve(state, max_nodes=400_000, time_limit=0.01)
    finally:
        solver_mod._dragon_collapse_reachable = original

    assert timed.status is Status.UNKNOWN, timed
    assert observed and observed[0][0] is None, observed
    print(
        "TIME_BUDGET_OBSERVATION "
        f"status={timed.status.value} proof_nodes={observed[0][1]} "
        f"astar_reported_nodes={timed.nodes} elapsed={timed.elapsed:.6f}s"
    )


def main() -> None:
    validate_hard_graph_successor_equivalence()
    states = collect_solvable_trigger_states()
    validate_solvable_trigger_states(states)
    validate_budget_semantics()


if __name__ == "__main__":
    main()
