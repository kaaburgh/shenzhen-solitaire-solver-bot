import pytest

from shenzhen.cards import (
    BLACK,
    FLOWER,
    GREEN,
    RED,
    card_code,
    locked_cell,
    make_card,
    make_dragon,
    parse_card,
    parse_cell,
)
from shenzhen.game import (
    InvalidBoard,
    Move,
    State,
    apply_move,
    auto_resolve,
    can_collapse_dragons,
    collectable_ids,
    deal,
    legal_moves,
    max_run_length,
    validate,
)
from shenzhen.textio import parse_board


def board(text: str) -> State:
    return parse_board(text, settle=False)


# --- cards -----------------------------------------------------------------


def test_card_codes_round_trip():
    for code in ["G1", "G9", "R4", "B7", "DG", "DR", "DB", "F"]:
        assert card_code(parse_card(code)) == code


def test_parse_card_rejects_nonsense():
    for bad in ["G0", "Z3", "G10", "D", "DX", ""]:
        with pytest.raises(ValueError):
            parse_card(bad)


def test_parse_cell_handles_empty_and_locked():
    assert parse_cell(".") is None
    assert parse_cell("XR") == locked_cell(RED)
    assert parse_cell("DG") == make_dragon(GREEN)


# --- runs ------------------------------------------------------------------


def test_run_descends_and_alternates_suits():
    # B5 G4 R3 is a run; the B6 above it repeats B5's suit and breaks it.
    column = (make_card(BLACK, 6), make_card(BLACK, 5), make_card(GREEN, 4), make_card(RED, 3))
    assert max_run_length(column) == 3

    # Alternating all the way up is one long run.
    column = (make_card(GREEN, 6), make_card(BLACK, 5), make_card(GREEN, 4), make_card(RED, 3))
    assert max_run_length(column) == 4


def test_run_stops_at_a_same_suit_neighbour():
    column = (make_card(GREEN, 5), make_card(GREEN, 4))
    assert max_run_length(column) == 1


def test_dragons_never_join_a_run():
    column = (make_card(GREEN, 5), make_dragon(RED))
    assert max_run_length(column) == 1


# --- automatic behaviour ---------------------------------------------------


def test_flower_leaves_on_its_own():
    state = board(
        """
        free: . . .
        flower: 0
        foundations: 0 0 0
        1: F
        2: G1 G2 G3 G4 G5 G6 G7 G8 G9
        3: R1 R2 R3 R4 R5 R6 R7 R8 R9
        4: B1 B2 B3 B4 B5 B6 B7 B8 B9
        5: DG DG DG DG
        6: DR DR DR DR
        7: DB DB DB DB
        8:
        """
    )
    settled, collected = auto_resolve(state)
    assert settled.flower is True
    assert FLOWER in collected
    assert settled.columns[0] == ()


def test_aces_and_twos_go_up_immediately():
    assert make_card(GREEN, 1) in collectable_ids((0, 0, 0))
    assert make_card(GREEN, 2) in collectable_ids((1, 0, 0))


def test_a_three_waits_for_the_other_suits():
    # G3 would still be needed to hold a red or black 2.
    assert make_card(GREEN, 3) not in collectable_ids((2, 0, 0))
    assert make_card(GREEN, 3) in collectable_ids((2, 2, 2))


def test_autocollect_cascades():
    state = board(
        """
        free: . . .
        flower: 1
        foundations: 0 0 0
        1: G9 G8 G7 G6 G5 G4 G3 G2 G1
        2: R9 R8 R7 R6 R5 R4 R3 R2 R1
        3: B9 B8 B7 B6 B5 B4 B3 B2 B1
        4: DG DG DG DG
        5: DR DR DR DR
        6: DB DB DB DB
        7:
        8:
        """
    )
    settled, collected = auto_resolve(state)
    assert settled.foundations == (9, 9, 9)
    assert len(collected) == 27
    assert not settled.is_won  # the dragons still have to be collapsed


# --- dragons ---------------------------------------------------------------


def test_dragons_collapse_when_all_four_are_exposed():
    state = board(
        """
        free: . . .
        flower: 1
        foundations: 9 9 9
        1: DG
        2: DG
        3: DG
        4: DG
        5: DR DR DR DR
        6: DB DB DB DB
        7:
        8:
        """
    )
    assert can_collapse_dragons(state, GREEN)
    assert not can_collapse_dragons(state, RED)  # buried under each other

    after, _ = apply_move(state, Move("dr", GREEN))
    assert after.free.count(locked_cell(GREEN)) == 1
    assert all(column == () for column in after.columns[:4])


def test_dragons_need_a_cell_to_collapse_into():
    state = board(
        """
        free: R9 B9 G9
        flower: 1
        foundations: 8 8 8
        1: DG
        2: DG
        3: DG
        4: DG
        5: DR DR DR DR
        6: DB DB DB DB
        7:
        8:
        """
    )
    assert not can_collapse_dragons(state, GREEN)


def test_a_dragon_already_in_a_cell_counts_and_can_be_collapsed_into():
    state = board(
        """
        free: DG R9 B9
        flower: 1
        foundations: 8 8 8
        1: DG
        2: DG
        3: DG
        4: DR DR DR DR
        5: DB DB DB DB
        6: G9
        7:
        8:
        """
    )
    assert can_collapse_dragons(state, GREEN)


# --- moves -----------------------------------------------------------------


def test_generated_moves_are_all_legal():
    for seed in range(20):
        state = deal(seed)
        for move in legal_moves(state):
            apply_move(state, move, checked=True)  # raises if not


def test_unchecked_apply_matches_checked_apply():
    import random

    rng = random.Random(0)
    for seed in range(20):
        state = deal(seed)
        for _ in range(30):
            moves = legal_moves(state)
            if not moves:
                break
            move = rng.choice(moves)
            assert apply_move(state, move, checked=True) == apply_move(
                state, move, checked=False
            )
            state, _ = apply_move(state, move)
            if state.is_won:
                break


def test_a_whole_column_does_not_shuffle_between_empty_columns():
    state = board(
        """
        free: . . .
        flower: 1
        foundations: 9 9 9
        1: DG DG DG DG
        2: DR DR DR DR
        3: DB DB DB DB
        4:
        5:
        6:
        7:
        8:
        """
    )
    # Column 1 is a single dragon run of length 1 per card; moving all four is
    # impossible, but moving the whole of a one-card column would be a no-op.
    moves = [m for m in legal_moves(state) if m.kind == "tt"]
    for move in moves:
        assert move.n != len(state.columns[move.a])


def test_empty_columns_are_offered_only_once():
    state = deal(0)
    state, _ = apply_move(state, Move("tf", 0))
    destinations = {m.b for m in legal_moves(state) if m.kind == "tt" and not state.columns[m.b]}
    assert len(destinations) <= 1


# --- validation ------------------------------------------------------------


def test_validate_accepts_a_real_deal():
    validate(deal(0))


def test_validate_rejects_a_duplicated_card():
    with pytest.raises(InvalidBoard) as excinfo:
        board(
            """
            free: . . .
            flower: 1
            foundations: 0 0 0
            1: G1 G1
            2:
            3:
            4:
            5:
            6:
            7:
            8:
            """
        )
    assert "G1" in str(excinfo.value)


def test_locked_cells_count_as_four_dragons():
    state = board(
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
    validate(state)
    assert state.is_won


# --- canonical form --------------------------------------------------------


def test_column_order_does_not_change_identity():
    a = deal(5)
    reordered = State(
        columns=tuple(reversed(a.columns)),
        free=a.free,
        foundations=a.foundations,
        flower=a.flower,
    )
    assert a.key() == reordered.key()


def test_free_cell_order_does_not_change_identity():
    base = deal(5)
    a = State(base.columns, (make_dragon(RED), None, None), base.foundations, base.flower)
    b = State(base.columns, (None, None, make_dragon(RED)), base.foundations, base.flower)
    assert a.key() == b.key()
