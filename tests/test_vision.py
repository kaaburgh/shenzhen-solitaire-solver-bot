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

from shenzhen.cards import BLACK, FULL_DECK, GREEN, RED, card_code, locked_cell, make_dragon
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
from shenzhen.vision.recognize import (
    MIN_RELIABLE_CARD_WIDTH,
    RecognitionError,
    recognize,
)

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
    from shenzhen.cards import make_card

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


def _solid_patch(height, width, bgr):
    return np.full((height, width, 3), bgr, dtype=np.uint8)


def test_ink_colour_survives_jpeg_style_desaturation():
    """A screenshot sent to the bot as a compressed photo, not a file, showed
    green ink at saturation ~44-58 where a clean PNG reads 90+ -- JPEG's
    chroma subsampling throws away colour detail far more readily than
    brightness detail, and a small glyph does not have much colour detail to
    spare. Hue 62 / saturation 55 sits at the upper end of what was measured
    on that screenshot, comfortably above COLOUR_SATURATION; the lowered
    threshold is what full-fixture reads at the lower end depend on."""
    patch = _solid_patch(30, 60, (222, 232, 238))
    patch[8:22, 15:45] = (104, 130, 102)  # BGR for HSV (62, 55, 130)
    assert ink_colour(patch) == GREEN


def test_ink_colour_ignores_a_felt_sliver_at_the_box_edge():
    """The same screenshot misread a black '2' as green, traced to the card's
    detected box landing one pixel high and catching a sliver of green felt at
    the crop's top edge. At the lowered saturation threshold above, that
    sliver is exactly as 'coloured' as genuine washed-out ink -- it has to be
    kept out by position, not by degree, which is what the edge margin in
    ink_colour is for."""
    patch = _solid_patch(30, 60, (222, 232, 238))
    patch[0:1, :] = (51, 137, 69)  # BGR felt green: HSV hue 54, saturation 160
    patch[8:22, 15:45] = (60, 60, 60)  # genuine black ink: dark, unsaturated
    assert ink_colour(patch) == BLACK


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


# --- screenshots Telegram has recompressed ---------------------------------


def _as_telegram_photo(image, width=1280, quality=80):
    """What Telegram does to a picture sent as a photo rather than a file:
    scales the long side down to ~1280 and re-encodes it as JPEG."""
    height = int(round(image.shape[0] * width / image.shape[1]))
    scaled = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", scaled, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
def test_a_rescaled_screenshot_is_diagnosed_as_rescaled_not_as_a_bad_board():
    """A phone screenshot sent as a photo comes back as a board that cannot
    exist. Reporting the deck arithmetic ("missing G3x1, duplicated G8x1")
    tells the user nothing they can act on; what they need to know is that the
    picture arrived too small, which is fixed by sending it as a file."""
    bank = TemplateBank.load(BANK_PATH)
    original = cv2.imread(str(FIXTURES / "iphone" / "shot3.png"))
    assert recognize(original, bank).state, "the original should read fine"

    with pytest.raises(RecognitionError) as excinfo:
        recognize(_as_telegram_photo(original), bank)

    assert excinfo.value.likely_rescaled
    assert excinfo.value.card_w is not None
    assert excinfo.value.card_w < MIN_RELIABLE_CARD_WIDTH


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
def test_a_board_that_reads_but_came_in_small_is_flagged_for_checking():
    """Between "reads perfectly" and "cannot possibly be right" there is a
    band where most boards still come out correct. Those are not refused --
    but the read carries a warning, which is what stops the bot treating it as
    something it is sure of."""
    bank = TemplateBank.load(BANK_PATH)
    original = cv2.imread(str(FIXTURES / "ipad" / "shot1.png"))

    result = recognize(_as_telegram_photo(original), bank)
    assert result.state == parse_board((FIXTURES / "ipad" / "shot1.txt").read_text())
    assert not result.confident
    assert any("px wide" in w for w in result.warnings), result.warnings
