import pytest

from shenzhen.cards import RED, locked_cell, make_card, make_dragon
from shenzhen.game import InvalidBoard, deal
from shenzhen.notation import board_to_text, describe_move, render_board
from shenzhen.game import Move
from shenzhen.textio import parse_board

# A real shuffle of the 40-card deck: eight columns of five.
FRESH_DEAL = """
1: G6 DB R4 B2 R7
2: R2 DG B4 G9 R3
3: F G1 DG R6 DB
4: B1 B5 DR DG B7
5: R9 DR G8 B9 DB
6: R5 G3 G2 DR R8
7: DG DB B6 G7 DR
8: G5 G4 B8 R1 B3
"""


def test_a_fresh_deal_parses_without_any_header_lines():
    state = parse_board(FRESH_DEAL, settle=False)
    assert len(state.columns) == 8
    assert all(len(column) == 5 for column in state.columns)
    assert state.free == (None, None, None)
    assert state.foundations == (0, 0, 0)


def test_the_last_card_of_a_column_is_the_one_you_can_grab():
    state = parse_board(FRESH_DEAL, settle=False)
    assert state.columns[0][-1] == make_card(RED, 7)


def test_headers_are_parsed():
    state = parse_board(
        """
        free: DG . XR
        flower: 1
        foundations: 3 0 1
        1: G4 G5 G6 DG
        2: G7 G8 G9 DG
        3: R1 R2 R3 DG
        4: R4 R5 R6 DB
        5: R7 R8 R9 DB
        6: B2 B3 B4 DB
        7: B5 B6 B7 DB
        8: B8 B9
        """,
        settle=False,
    )
    assert state.free == (make_dragon(0), None, locked_cell(RED))
    assert state.foundations == (3, 0, 1)
    assert state.flower is True


def test_foundations_may_be_given_as_cards():
    a = parse_board("foundations: G0 R0 B0\n" + FRESH_DEAL, settle=False)
    b = parse_board(FRESH_DEAL, settle=False)
    assert a.foundations == b.foundations == (0, 0, 0)


def test_the_flower_is_inferred_from_whether_it_is_on_the_table():
    on_table = parse_board(FRESH_DEAL, settle=False)
    assert on_table.flower is False

    already_played = parse_board(FRESH_DEAL.replace("3: F G1", "3: G1"), settle=False)
    assert already_played.flower is True


def test_russian_letters_are_accepted():
    state = parse_board(FRESH_DEAL.replace("DG", "ДG"), settle=False)
    assert state.columns[6][0] == make_dragon(0)


def test_a_bad_card_names_itself():
    with pytest.raises(InvalidBoard) as excinfo:
        parse_board(FRESH_DEAL.replace("G9", "G0"))
    assert "G0" in str(excinfo.value)


def test_a_short_deck_is_rejected():
    with pytest.raises(InvalidBoard):
        parse_board("\n".join(FRESH_DEAL.strip().splitlines()[:7]))


def test_mixing_numbered_and_bare_lines_is_rejected():
    with pytest.raises(InvalidBoard):
        parse_board("1: G9 R8 B7 DG DR\nB6 G5 R4 DB F")


def test_a_repeated_column_is_rejected():
    with pytest.raises(InvalidBoard):
        parse_board(FRESH_DEAL + "\n1: G1")


def test_board_to_text_round_trips():
    for seed in range(10):
        original = deal(seed)
        assert parse_board(board_to_text(original)) == original


def test_render_board_mentions_every_column():
    text = render_board(deal(0), "ru")
    for index in range(1, 9):
        assert f"{index}:" in text


def test_move_descriptions_name_the_cards():
    state = parse_board(FRESH_DEAL, settle=False)
    text = describe_move(Move("tf", 0), state, "ru")
    assert "R7" in text
    assert describe_move(Move("dr", RED), state, "en").startswith("Collapse")
