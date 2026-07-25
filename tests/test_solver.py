import pytest

from shenzhen.game import apply_move, deal
from shenzhen.solver import Status, solve
from shenzhen.textio import parse_board


def test_solves_a_typical_deal():
    result = solve(deal(0))
    assert result.status is Status.SOLVED
    assert result.steps


@pytest.mark.parametrize("seed", range(12))
def test_the_returned_line_actually_wins(seed):
    """Replay the moves against a fresh board and check we end up winning.

    This is the test that matters: the bot hands these moves to a person who
    will play them one at a time, so each one has to be legal in the position
    it is played from.
    """
    state = deal(seed)
    result = solve(state)
    if result.status is not Status.SOLVED:
        pytest.skip(f"deal {seed} was not solved within the budget")

    for index, step in enumerate(result.steps):
        assert step.state_before == state, f"step {index + 1} starts from a different position"
        state, collected = apply_move(state, step.move, checked=True)
        assert collected == step.collected
        assert state == step.state_after
    assert state.is_won


def test_an_already_won_board_needs_no_moves():
    state = parse_board(
        """
        free: XG XR XB
        flower: 1
        foundations: 9 9 9
        1:
        2:
        3:
        4:
        5:
        6:
        7:
        8:
        """
    )
    result = solve(state)
    assert result.status is Status.SOLVED
    assert result.steps == []


def test_a_dead_position_is_reported_as_unsolvable():
    """A position with no legal move at all.

    Every column is capped by a dragon, so nothing stacks and nothing reaches
    a foundation.  No colour has all four dragons exposed, so none can be
    collapsed.  The cells are full of nines, which fit nowhere.  The search
    therefore runs out of positions rather than out of time, and the bot is
    entitled to say "not winnable" instead of "I could not find anything".
    """
    state = parse_board(
        """
        free: G9 R9 B9
        flower: 1
        foundations: 0 0 0
        1: G1 DG G2 G3 DG
        2: G4 G5 G6 DG
        3: G7 G8 R1 DG
        4: R2 DR R3 R4 DR
        5: R5 R6 R7 DR
        6: R8 B1 B2 DR
        7: B3 DB B4 B5 DB
        8: B6 DB B7 B8 DB
        """
    )
    result = solve(state, max_nodes=200_000, time_limit=30)
    assert result.status is Status.UNSOLVABLE


def test_budget_exhaustion_is_not_reported_as_unsolvable():
    result = solve(deal(0), max_nodes=1, time_limit=60)
    assert result.status is Status.UNKNOWN


def test_solvable_rate_over_many_deals():
    """Shenzhen deals are almost always winnable; a regression that breaks the
    rules tends to show up here as a pile of 'unsolvable' verdicts."""
    outcomes = [solve(deal(seed), max_nodes=120_000, time_limit=10).status for seed in range(40)]
    assert outcomes.count(Status.SOLVED) >= 36
