import random

import shenzhen.solver as solver_module
from shenzhen._solver_successors import successors_from_settled
from shenzhen.cards import BLACK, FLOWER, GREEN, RED, make_card
from shenzhen.game import Move, State, apply_move, deal, legal_moves, successors


def test_solver_successors_match_full_resolution_on_settled_walks():
    rng = random.Random(0)

    for seed in range(20):
        state = deal(seed)
        for _ in range(30):
            assert list(successors_from_settled(state)) == list(successors(state))

            moves = legal_moves(state)
            if not moves:
                break
            state, _ = apply_move(state, rng.choice(moves))
            if state.is_won:
                break


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

    assert actual == expected
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

    assert actual == expected
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
        "successors_from_settled",
        lambda current: calls.append("settled") or iter(()),
    )

    solver_module.solve(state, max_nodes=1, time_limit=60)

    assert calls == ["general"]
