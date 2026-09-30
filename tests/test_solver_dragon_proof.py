import time

import shenzhen.solver as solver_mod
from shenzhen.cards import GREEN, RED, make_dragon
from shenzhen.game import Move, State
from shenzhen.solver import (
    Status,
    _dragon_collapse_reachable,
    _has_storage_pressure_for_dragon_proof,
)


def test_dragon_collapse_probe_finds_an_immediately_available_collapse():
    dragon = make_dragon(GREEN)
    state = State(
        columns=((dragon,), (dragon,), (), (), (), (), (), ()),
        free=(dragon, dragon, None),
        foundations=(0, 0, 0),
        flower=True,
    )

    reachable, nodes = _dragon_collapse_reachable(
        state, max_nodes=100, deadline=time.monotonic() + 10
    )

    assert reachable is True
    assert nodes == 1


def test_dragon_collapse_probe_can_exhaust_a_graph_without_a_collapse():
    dragon = make_dragon(GREEN)
    state = State(
        columns=((dragon,), (), (), (), (), (), (), ()),
        free=(None, None, None),
        foundations=(0, 0, 0),
        flower=True,
    )

    reachable, nodes = _dragon_collapse_reachable(
        state, max_nodes=100, deadline=time.monotonic() + 10
    )

    assert reachable is False
    assert nodes > 0


def test_dragon_collapse_probe_budget_exhaustion_proves_nothing():
    dragon = make_dragon(GREEN)
    state = State(
        columns=((dragon,), (), (), (), (), (), (), ()),
        free=(None, None, None),
        foundations=(0, 0, 0),
        flower=True,
    )

    reachable, nodes = _dragon_collapse_reachable(
        state, max_nodes=0, deadline=time.monotonic() + 10
    )

    assert reachable is None
    assert nodes == 0


def test_storage_pressure_trigger_requires_matching_free_dragons():
    green = make_dragon(GREEN)
    red = make_dragon(RED)

    matching = State(((),) * 8, (green, green, None), (0, 0, 0), True)
    mixed = State(((),) * 8, (green, red, None), (0, 0, 0), True)

    assert _has_storage_pressure_for_dragon_proof(matching)
    assert not _has_storage_pressure_for_dragon_proof(mixed)



def _trigger_state() -> State:
    green = make_dragon(GREEN)
    red = make_dragon(RED)
    return State(
        columns=((red,), (), (), (), (), (), (), ()),
        free=(green, green, None),
        foundations=(0, 0, 0),
        flower=True,
    )


def _one_astar_child(parent: State, calls: list[tuple]):
    child = State(
        columns=((),) * 8,
        free=parent.free,
        foundations=parent.foundations,
        flower=parent.flower,
    )

    def successors(state, key, run_cache=None):
        calls.append(key)
        if len(calls) > 1:
            raise AssertionError("A* exceeded its remaining node budget")
        yield Move("tf", 0, 0), solver_mod._search_key(child), child, (), None

    return successors


def test_inconclusive_proof_nodes_count_toward_global_budget(monkeypatch):
    state = _trigger_state()
    calls: list[tuple] = []

    monkeypatch.setattr(
        solver_mod,
        "_dragon_collapse_reachable",
        lambda *args, **kwargs: (None, 2),
    )
    monkeypatch.setattr(
        solver_mod,
        "prepared_successors_from_settled",
        _one_astar_child(state, calls),
    )

    result = solver_mod.solve(state, max_nodes=3, time_limit=10.0)

    assert result.status is Status.UNKNOWN
    assert result.nodes == 3
    assert len(calls) == 1


def test_proof_can_exhaust_the_whole_global_node_budget(monkeypatch):
    state = _trigger_state()

    monkeypatch.setattr(
        solver_mod,
        "_dragon_collapse_reachable",
        lambda *args, **kwargs: (None, 3),
    )

    def unexpected_astar(*args, **kwargs):
        raise AssertionError("A* must not run with no global node budget left")

    monkeypatch.setattr(
        solver_mod,
        "prepared_successors_from_settled",
        unexpected_astar,
    )

    result = solver_mod.solve(state, max_nodes=3, time_limit=10.0)

    assert result.status is Status.UNKNOWN
    assert result.nodes == 3


def test_reachable_collapse_leaves_only_remaining_budget_for_astar(monkeypatch):
    state = _trigger_state()
    calls: list[tuple] = []

    monkeypatch.setattr(
        solver_mod,
        "_dragon_collapse_reachable",
        lambda *args, **kwargs: (True, 2),
    )
    monkeypatch.setattr(
        solver_mod,
        "prepared_successors_from_settled",
        _one_astar_child(state, calls),
    )

    result = solver_mod.solve(state, max_nodes=3, time_limit=10.0)

    assert result.status is Status.UNKNOWN
    assert result.nodes == 3
    assert len(calls) == 1
