import random

from shenzhen._solver_successors import successors_from_settled
from shenzhen.game import Move, apply_move, deal, legal_moves
from shenzhen.solver import _heuristic_after_move, heuristic


def test_move_local_heuristic_matches_full_recompute_on_settled_walks():
    rng = random.Random(0)

    for seed in range(20):
        state = deal(seed)
        for _ in range(30):
            score = heuristic(state)
            for move, nxt, collected in successors_from_settled(state):
                assert _heuristic_after_move(state, score, move, nxt, collected) == heuristic(nxt)

            moves = legal_moves(state)
            if not moves:
                break
            state, _ = apply_move(state, rng.choice(moves))
            if state.is_won:
                break


def test_move_local_heuristic_falls_back_when_foundations_change():
    state = deal(0)
    after = state._replace(foundations=(1, state.foundations[1], state.foundations[2]))

    assert _heuristic_after_move(state, heuristic(state), Move("tF"), after, ()) == heuristic(after)
