import pytest

from shenzhen.cards import BLACK, GREEN, RED, SUITS, make_card, make_dragon
from shenzhen.game import State, can_collapse_dragons, legal_moves


@pytest.mark.parametrize("colour", SUITS)
@pytest.mark.parametrize("slot", ["empty", "matching-dragon", "blocked"])
def test_generated_dragon_collapses_match_rule_helper(colour, slot):
    dragon = make_dragon(colour)
    if slot == "empty":
        free = (None, None, None)
        exposed_columns = 4
    elif slot == "matching-dragon":
        free = (dragon, make_card(RED, 9), make_card(BLACK, 9))
        exposed_columns = 3
    else:
        free = (make_card(GREEN, 9), make_card(RED, 9), make_card(BLACK, 9))
        exposed_columns = 4

    columns = tuple((dragon,) for _ in range(exposed_columns)) + tuple(
        () for _ in range(8 - exposed_columns)
    )
    state = State(columns=columns, free=free, foundations=(0, 0, 0), flower=False)

    generated = {move.a for move in legal_moves(state) if move.kind == "dr"}
    expected = {candidate for candidate in SUITS if can_collapse_dragons(state, candidate)}
    assert generated == expected
