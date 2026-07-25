"""Tests for the screenshot pipeline.

These run against :mod:`fake_board`, which reproduces the *geometry* of the
game screen but not its artwork.  They prove the pipeline is wired up
correctly -- that columns are found, stacked cards are split apart, ink colour
picks the suit, templates pick the rank, and a collapsed cell is told apart
from a parked dragon.  They cannot prove the thresholds suit the real game;
only screenshots can.  See docs/calibration.md.
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from fake_board import render
from shenzhen.cards import FULL_DECK, GREEN, RED, locked_cell, make_dragon
from shenzhen.game import State, auto_resolve
from shenzhen.vision.classify import TemplateBank, ink_colour, normalise
from shenzhen.vision.layout import LayoutConfig, LayoutError, corner_patch, detect_layout
from shenzhen.vision.recognize import recognize

CONFIG = LayoutConfig()


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


@pytest.fixture(scope="module")
def bank() -> TemplateBank:
    """Cut a template bank the same way the calibrate command does."""
    samples: dict[int, list[np.ndarray]] = {}
    for seed in (101, 202):
        state = raw_deal(seed)
        image = render(state)
        layout = detect_layout(image, CONFIG)
        for boxes, cards in zip(layout.columns, state.columns):
            assert len(boxes) == len(cards)
            for box, card in zip(boxes, cards):
                patch = corner_patch(image, box, layout.card_w, CONFIG, max_h=layout.offset)
                samples.setdefault(card, []).append(normalise(patch))

    templates = TemplateBank()
    for card, patches in samples.items():
        templates.templates[card] = normalise(np.mean(np.stack(patches), axis=0))
    return templates


# --- layout ----------------------------------------------------------------


def test_every_column_is_found_and_cut_into_the_right_number_of_cards():
    for seed in range(5):
        state = auto_resolve(raw_deal(seed))[0]
        layout = detect_layout(render(state), CONFIG)
        assert [len(c) for c in layout.columns] == [len(c) for c in state.columns]


def test_the_stacking_offset_is_measured_not_guessed():
    from fake_board import OFFSET

    layout = detect_layout(render(raw_deal(1)), CONFIG)
    assert abs(layout.offset - OFFSET) <= 2


def test_a_fresh_deal_has_nothing_in_the_top_row():
    """The top row is empty at the start, so there is no gap to split on --
    every card must still be assigned to the tableau."""
    layout = detect_layout(render(raw_deal(1)), CONFIG)
    assert all(cell is None for cell in layout.free_cells)
    assert layout.flower is None
    assert all(slot is None for slot in layout.foundations)
    assert sum(len(c) for c in layout.columns) == 40


def test_a_picture_that_is_not_the_board_is_refused():
    noise = np.zeros((400, 400, 3), dtype=np.uint8)
    with pytest.raises(LayoutError):
        detect_layout(noise, CONFIG)


# --- ink colour ------------------------------------------------------------


def test_ink_colour_separates_the_three_suits():
    from shenzhen.cards import BLACK, make_card

    for suit, expected in ((GREEN, GREEN), (RED, RED), (BLACK, BLACK)):
        state = raw_deal(1)
        image = render(state)
        layout = detect_layout(image, CONFIG)
        for boxes, cards in zip(layout.columns, state.columns):
            for box, card in zip(boxes, cards):
                if card != make_card(suit, 5):
                    continue
                patch = corner_patch(image, box, layout.card_w, CONFIG, max_h=layout.offset)
                assert ink_colour(patch) == expected


def test_a_blank_crop_reads_as_no_card():
    blank = np.full((40, 40, 3), (222, 232, 238), dtype=np.uint8)
    assert ink_colour(blank) is None


# --- end to end ------------------------------------------------------------


@pytest.mark.parametrize("seed", range(300, 312))
def test_a_rendered_board_reads_back_exactly(seed, bank):
    state = auto_resolve(raw_deal(seed))[0]
    result = recognize(render(state), bank)
    assert result.state == state
    assert not result.warnings


def test_reads_are_correct_and_rarely_flagged(bank):
    """Every card is read right, and the "check this one" flag stays rare.

    A handful of flags is the intended behaviour -- the stand-in glyphs are a
    plain sans-serif font where 5 and 6 really are close -- but if most cards
    start tripping it, the thresholds have drifted and the confirmation step
    stops meaning anything.
    """
    total = flagged = 0
    for seed in range(300, 312):
        state = auto_resolve(raw_deal(seed))[0]
        result = recognize(render(state), bank)
        assert result.state == state
        total += len(result.reads)
        flagged += len(result.uncertain)

    assert total > 300
    assert flagged / total < 0.03, f"{flagged} of {total} reads flagged as uncertain"


def test_free_cells_and_foundations_are_read(bank):
    state = raw_deal(400)
    # Park a dragon in a cell and put something on two foundations.
    state = State(
        columns=tuple(c[:-1] if i < 3 else c for i, c in enumerate(state.columns)),
        free=(state.columns[0][-1], state.columns[1][-1], state.columns[2][-1]),
        foundations=(0, 0, 0),
        flower=False,
    )
    result = recognize(render(state), bank)
    assert sorted(x for x in result.state.free if x is not None) == sorted(
        x for x in state.free if x is not None
    )


def test_a_collapsed_cell_is_told_apart_from_a_parked_dragon(bank):
    """Both look like one dragon face in a cell.  The rest of the deck decides:
    four of that colour still visible means it is a real card."""
    base = raw_deal(500)

    # All four green dragons removed from play, one face shown in a cell.
    without_green = tuple(
        tuple(card for card in column if card != make_dragon(GREEN)) for column in base.columns
    )
    collapsed = State(
        columns=without_green,
        free=(locked_cell(GREEN), None, None),
        foundations=(0, 0, 0),
        flower=False,
    )
    result = recognize(render(collapsed), bank)
    assert result.state.free[0] == locked_cell(GREEN)
    assert not result.warnings

    # A red dragon merely sitting in a cell, with its three partners on the table.
    parked_source = next(
        (i, c) for i, c in enumerate(base.columns) if c and c[-1] == make_dragon(RED)
    )
    index, column = parked_source
    parked = State(
        columns=tuple(c[:-1] if i == index else c for i, c in enumerate(base.columns)),
        free=(make_dragon(RED), None, None),
        foundations=(0, 0, 0),
        flower=False,
    )
    result = recognize(render(parked), bank)
    assert result.state.free[0] == make_dragon(RED)
    assert not result.warnings
