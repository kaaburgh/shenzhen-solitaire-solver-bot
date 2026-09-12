import random

import shenzhen.solver as solver_module
from shenzhen._solver_successors import (
    _materialize_prepared,
    prepared_successors_from_settled,
    successors_from_settled,
)
from shenzhen.cards import BLACK, FLOWER, GREEN, RED, make_card
from shenzhen.game import Move, State, apply_move, deal, legal_moves, successors


def _materialized_prepared_successors(state: State):
    items = []
    parent_key = solver_module._search_key(state)
    for move, key, nxt, collected, parts in prepared_successors_from_settled(
        state, parent_key
    ):
        if nxt is None:
            assert parts is not None
            nxt = _materialize_prepared(parts)
        assert key == solver_module._search_key(nxt)
        items.append((move, nxt, collected))
    return items


def test_solver_successors_match_full_resolution_on_settled_walks():
    rng = random.Random(0)

    for seed in range(20):
        state = deal(seed)
        for _ in range(30):
            expected = list(successors(state))
            assert list(successors_from_settled(state)) == expected
            assert _materialized_prepared_successors(state) == expected

            moves = legal_moves(state)
            if not moves:
                break
            state, _ = apply_move(state, rng.choice(moves))
            if state.is_won:
                break


def test_prepared_successor_defers_a_stable_child_state():
    state = State(
        columns=(
            (make_card(RED, 5),),
            (make_card(BLACK, 6),),
            (),
            (),
            (),
            (),
            (),
            (),
        ),
        free=(None, None, None),
        foundations=(0, 0, 0),
        flower=True,
    )
    move = Move("tt", 0, 1)

    prepared = next(
        item
        for item in prepared_successors_from_settled(state, solver_module._search_key(state))
        if item[0] == move
    )
    _, key, nxt, collected, parts = prepared

    assert nxt is None
    assert collected == ()
    assert parts is not None
    nxt = _materialize_prepared(parts)
    expected = next(item for item in successors(state) if item[0] == move)
    assert (move, nxt, collected) == expected
    assert key == solver_module._search_key(nxt)


def test_solver_successors_resolve_a_newly_exposed_source_top():
    state = State(
        columns=(
            (make_card(GREEN, 1), make_card(RED, 5)),
            (make_card(BLACK, 6),),
            (),
            (),
            (),
            (),
            (),
            (),
        ),
        free=(None, None, None),
        foundations=(0, 0, 0),
        flower=True,
    )
    move = Move("tt", 0, 1)

    actual = next(item for item in successors_from_settled(state) if item[0] == move)
    expected = next(item for item in successors(state) if item[0] == move)
    prepared = next(
        item
        for item in prepared_successors_from_settled(state, solver_module._search_key(state))
        if item[0] == move
    )

    assert actual == expected
    assert prepared[2] is not None
    assert actual[1].foundations[GREEN] == 1


def test_solver_successors_resolve_a_flower_moved_out_of_a_free_cell():
    state = State(
        columns=((), (), (), (), (), (), (), ()),
        free=(FLOWER, None, None),
        foundations=(9, 9, 9),
        flower=False,
    )
    move = Move("ft", 0, 0)

    actual = next(item for item in successors_from_settled(state) if item[0] == move)
    expected = next(item for item in successors(state) if item[0] == move)
    prepared = next(
        item
        for item in prepared_successors_from_settled(state, solver_module._search_key(state))
        if item[0] == move
    )

    assert actual == expected
    assert prepared[2] is not None
    assert actual[1].flower is True


def test_solve_uses_general_successors_for_an_unsettled_initial_state(monkeypatch):
    state = State(
        columns=((make_card(GREEN, 1),), (), (), (), (), (), (), ()),
        free=(None, None, None),
        foundations=(0, 0, 0),
        flower=True,
    )
    calls: list[str] = []

    monkeypatch.setattr(
        solver_module,
        "successors",
        lambda current: calls.append("general") or iter(()),
    )
    monkeypatch.setattr(
        solver_module,
        "prepared_successors_from_settled",
        lambda current, key: calls.append("prepared") or iter(()),
    )

    solver_module.solve(state, max_nodes=1, time_limit=60)

    assert calls == ["general"]
