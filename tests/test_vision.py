"""Tests for the screenshot pipeline.

Two layers.  :mod:`fake_board` renders positions with the structure of the
game screen but stand-in artwork, which is how states no screenshot happens to
show get covered -- an empty tableau, every cell locked.  The fixtures under
``tests/fixtures`` are real screenshots with the position written out beside
them, and those are what say the thresholds suit the real thing.
"""

from __future__ import annotations

import random
from pathlib import Path

import cv2
import numpy as np
import pytest
from fake_board import OFFSET, render

from shenzhen.cards import FULL_DECK, GREEN, RED, card_code, locked_cell, make_dragon
from shenzhen.game import State, auto_resolve
from shenzhen.textio import parse_board
from shenzhen.vision.classify import TemplateBank, ink_colour
from shenzhen.vision.layout import (
    LayoutConfig,
    LayoutError,
    corner_patch,
    detect_layout,
    find_dragon_buttons,
)
from shenzhen.vision.recognize import recognize

CONFIG = LayoutConfig()
FIXTURES = Path(__file__).parent / "fixtures"
BANK_PATH = Path(__file__).parent.parent / "templates" / "default"


def raw_deal(seed: int) -> State:
    """A deal before the game collects anything -- all 40 cards on the table."""
    deck = list(FULL_DECK)
    random.Random(seed).shuffle(deck)
    return State(
        columns=tuple(tuple(deck[i * 5 : (i + 1) * 5]) for i in range(8)),
        free=(None, None, None),
        foundations=(0, 0, 0),
        flower=False,
    )


# --- layout, on synthetic boards -------------------------------------------


def test_every_column_is_cut_into_the_right_number_of_cards():
    for seed in range(5):
        state = auto_resolve(raw_deal(seed))[0]
        layout = detect_layout(render(state), CONFIG)
        assert [len(c) for c in layout.columns] == [len(c) for c in state.columns]


def test_the_stacking_offset_is_measured_not_guessed():
    layout = detect_layout(render(raw_deal(1)), CONFIG)
    assert abs(layout.offset - OFFSET) <= 2


def test_the_dragon_buttons_anchor_the_grid():
    """The buttons sit in slot 3 and are always drawn, which is what lets the
    free cells be identified when the leftmost ones are empty."""
    image = render(raw_deal(1))
    assert len(find_dragon_buttons(image, 190, CONFIG)) == 3

    layout = detect_layout(image, CONFIG)
    assert not layout.warnings
    assert all(cell is None for cell in layout.free_cells)
    assert sum(len(c) for c in layout.columns) == 40


def test_a_board_whose_first_columns_are_empty_still_lines_up():
    """Without the button anchor this is the case that breaks: the leftmost
    card is in column 3, not column 1."""
    base = raw_deal(7)
    columns = ((), (), *base.columns[2:])
    spare = base.columns[0] + base.columns[1]
    state = State(
        columns=columns[:7] + (columns[7] + spare,),
        free=base.free,
        foundations=base.foundations,
        flower=base.flower,
    )
    layout = detect_layout(render(state), CONFIG)
    assert [len(c) for c in layout.columns] == [len(c) for c in state.columns]


def test_a_picture_that_is_not_the_board_is_refused():
    with pytest.raises(LayoutError):
        detect_layout(np.zeros((400, 400, 3), dtype=np.uint8), CONFIG)


def test_ink_colour_separates_the_three_suits():
    from shenzhen.cards import BLACK, make_card

    state = raw_deal(1)
    image = render(state)
    layout = detect_layout(image, CONFIG)
    for boxes, cards in zip(layout.columns, state.columns):
        for box, card in zip(boxes, cards):
            for suit in (GREEN, RED, BLACK):
                if card == make_card(suit, 5):
                    patch = corner_patch(image, box, layout.card_w, CONFIG)
                    assert ink_colour(patch) == suit


def test_a_blank_crop_reads_as_no_card():
    assert ink_colour(np.full((40, 40, 3), (222, 232, 238), dtype=np.uint8)) is None


# --- free cells and foundations, on synthetic boards -----------------------


def test_free_cells_and_foundations_land_in_the_right_slots():
    base = raw_deal(400)
    state = State(
        columns=tuple(c[:-1] if i < 3 else c for i, c in enumerate(base.columns)),
        free=tuple(base.columns[i][-1] for i in range(3)),
        foundations=(2, 0, 5),
        flower=True,
    )
    layout = detect_layout(render(state), CONFIG)

    assert all(box is not None for box in layout.free_cells)
    assert layout.flower is not None
    assert sum(box is not None for box in layout.foundations) == 2
    assert not layout.warnings


def test_a_locked_cell_is_told_apart_from_a_card_by_its_back():
    """Both fill the same slot; only the pattern on the back says which."""
    base = raw_deal(500)
    state = State(
        columns=base.columns,
        free=(locked_cell(GREEN), make_dragon(RED), None),
        foundations=(0, 0, 0),
        flower=False,
    )
    layout = detect_layout(render(state), CONFIG)
    assert layout.locked_cells == [True, False, False]


def test_a_board_with_every_cell_locked_is_read():
    """The end of a game, which none of the screenshot fixtures happens to
    show: all twelve dragons collapsed, so all three cells hold a back."""
    state = parse_board(
        """
        free: XG XR XB
        flower: 1
        foundations: 8 8 8
        1: G9
        2: R9
        3: B9
        4:
        5:
        6:
        7:
        8:
        """,
        settle=False,
    )
    layout = detect_layout(render(state), CONFIG)
    assert layout.locked_cells == [True, True, True]
    assert [len(c) for c in layout.columns] == [1, 1, 1, 0, 0, 0, 0, 0]
    assert not layout.warnings


def test_the_offset_falls_back_when_nothing_is_stacked():
    """With one card per column there is no stacking offset to measure, so
    the splitter must not invent one out of the glyphs."""
    state = parse_board(
        """
        free: XG XR XB
        flower: 1
        foundations: 8 8 8
        1: G9
        2: R9
        3: B9
        4:
        5:
        6:
        7:
        8:
        """,
        settle=False,
    )
    layout = detect_layout(render(state), CONFIG)
    assert abs(layout.offset - round(CONFIG.nominal_offset * layout.card_w)) <= 2


# --- real screenshots ------------------------------------------------------


def _fixtures() -> list[tuple[str, Path, Path]]:
    found = []
    for image in sorted(FIXTURES.glob("*/*.png")):
        expected = image.with_suffix(".txt")
        if expected.exists():
            found.append((f"{image.parent.name}/{image.stem}", image, expected))
    return found


FIXTURE_CASES = _fixtures()


def test_there_are_screenshot_fixtures_to_test_against():
    """Guard against the fixtures silently going missing -- without them the
    rest of this file only proves the pipeline is self-consistent."""
    assert FIXTURE_CASES, f"no screenshot fixtures under {FIXTURES}"


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
@pytest.mark.parametrize("name,image_path,expected_path", FIXTURE_CASES, ids=lambda v: v if isinstance(v, str) else "")
def test_a_real_screenshot_reads_back_exactly(name, image_path, expected_path):
    bank = TemplateBank.load(BANK_PATH)
    image = cv2.imread(str(image_path))
    assert image is not None, f"could not read {image_path}"

    result = recognize(image, bank)
    expected = parse_board(expected_path.read_text(encoding="utf-8"))

    assert result.state == expected, name
    assert not result.warnings, result.warnings
    unsure = [f"{r.where}={card_code(r.card)}" for r in result.uncertain]
    assert not unsure, unsure
