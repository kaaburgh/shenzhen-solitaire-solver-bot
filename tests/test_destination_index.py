from shenzhen.cards import BLACK, GREEN, RED, make_card
from shenzhen.game import Move, State, legal_moves


def test_indexed_tableau_destinations_keep_left_to_right_move_order():
    source = (
        make_card(GREEN, 5),
        make_card(RED, 4),
        make_card(BLACK, 3),
    )
    state = State(
        columns=(
            (make_card(GREEN, 4),),
            source,
            (),
            (make_card(BLACK, 5),),
            (make_card(RED, 6),),
            (),
            (),
            (),
        ),
        free=(make_card(BLACK, 3), None, None),
        foundations=(0, 0, 0),
        flower=False,
    )

    moves = legal_moves(state)
    tableau = [move for move in moves if move.kind == "tt" and move.a == 1]
    free_cell = [move for move in moves if move.kind == "ft" and move.a == 0]

    assert tableau == [
        Move("tt", 1, 0, 1),
        Move("tt", 1, 2, 1),
        Move("tt", 1, 2, 2),
        Move("tt", 1, 3, 2),
        Move("tt", 1, 4, 3),
    ]
    assert free_cell == [Move("ft", 0, 0), Move("ft", 0, 2)]
