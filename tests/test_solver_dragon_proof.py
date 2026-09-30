import time

from shenzhen.cards import GREEN, RED, make_dragon
from shenzhen.game import State
from shenzhen.solver import _dragon_collapse_reachable, _has_storage_pressure_for_dragon_proof


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
