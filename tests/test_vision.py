"""Tests for the screenshot pipeline.

Two layers.  :mod:`fake_board` renders positions with the structure of the
game screen but stand-in artwork, which is how states no screenshot happens to
show get covered -- an empty tableau, every cell locked.  The fixtures under
``tests/fixtures`` are real screenshots with the position written out beside
them, and those are what say the thresholds suit the real thing.
"""

from __future__ import annotations

import importlib
import random
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import pytest
from fake_board import CARD_W, OFFSET, TABLEAU_Y, render, slot_x

from shenzhen.cards import (
    BLACK,
    FULL_DECK,
    GREEN,
    RED,
    card_code,
    locked_cell,
    make_dragon,
    parse_card,
)
from shenzhen.game import NUM_COLUMNS, InvalidBoard, State, auto_resolve
from shenzhen.textio import parse_board
from shenzhen.vision.classify import Guess, TemplateBank, ink_colour
from shenzhen.vision.layout import (
    BoardLayout,
    Box,
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

#: the module itself, for the one test that has to reach a constant inside it.
#: ``shenzhen.vision`` re-exports the ``recognize`` function under the name of
#: the module it lives in, so plain attribute access finds the function.
RECOGNIZE = importlib.import_module("shenzhen.vision.recognize")


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


def test_ink_across_the_lattice_is_not_taken_for_a_card_boundary():
    """Ink on the face of the bottom card, ending exactly where the lattice
    expects the next card to start, is the case that used to cut that card in
    two: the brightness coming back after a stroke moves the row *mean* as far
    as a real seam does.  What the two do not share is width -- a seam is one
    edge right across the card, and a stroke is not.

    Painted in rather than rendered, and a bold stroke rather than the corner
    glyph that actually did it: the point is that no amount of ink counts if
    it leaves half the column alone.
    """
    state = auto_resolve(raw_deal(3))[0]
    image = render(state)

    for index, column in enumerate(state.columns):
        left = slot_x(index) + int(CARD_W * 0.32)
        right = slot_x(index) + int(CARD_W * 0.58)
        row = TABLEAU_Y + len(column) * OFFSET  # where a ninth card would start
        cv2.line(image, (left, row), (right, row), (35, 35, 35), int(CARD_W * 0.05))

    layout = detect_layout(image, CONFIG)
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


def test_a_glyphless_false_split_is_not_reported_as_an_extra_card(monkeypatch):
    """A dragon glyph can imitate a card edge in a small image.

    The resulting lower region has no corner glyph and is discarded.  It must
    not survive as a warning about "card 2" beside a legal board which correctly
    describes the dragon above it as the column's only card.
    """
    dragon = make_dragon(GREEN)
    upper = Box(0, 0, 150, 36)
    lower = Box(0, 36, 150, 251)
    layout = BoardLayout(card_w=150, card_h=287, offset=36)
    layout.columns[6] = [upper, lower]
    monkeypatch.setattr(RECOGNIZE, "detect_layout", lambda _image, _config: layout)

    guess = Guess(card=dragon, confidence=1.0, margin=1.0, colour=GREEN)
    answers = iter((guess, None))
    monkeypatch.setattr(RECOGNIZE, "classify", lambda _patch, _bank: next(answers))

    state = State(
        columns=((), (), (), (), (), (), (dragon,), ()),
        free=(None, None, None),
        foundations=(0, 0, 0),
        flower=True,
    )
    monkeypatch.setattr(
        RECOGNIZE,
        "resolve",
        lambda _skeleton, _reads: RECOGNIZE.Resolution(
            state=state, unknowns=(), possibilities=(), level=0
        ),
    )

    result = recognize(np.zeros((300, 300, 3), dtype=np.uint8), object())

    assert [read.where for read in result.reads] == ["7.1"]
    assert result.state.columns[6] == (dragon,)
    assert result.warnings == []


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
    """Every screenshot with its position written out beside it.

    ``.png`` fixtures are untouched originals, straight off the device.
    ``.jpg`` ones came back out of Telegram and are already scaled and
    recompressed, so nothing here may squeeze them a second time.
    """
    found = []
    for image in sorted(FIXTURES.glob("*/*.png")) + sorted(FIXTURES.glob("*/*.jpg")):
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


def _as_sent(image_path, image, width=1280):
    """The fixture as it would arrive, without compressing anything twice."""
    if image_path.suffix == ".jpg":
        return image
    return _as_telegram_photo(image, width)


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
@pytest.mark.parametrize(
    "name,image_path,expected_path", FIXTURE_CASES, ids=lambda v: v if isinstance(v, str) else ""
)
def test_a_screenshot_sent_as_a_photo_reads_back_exactly(name, image_path, expected_path):
    """The case the bot actually gets. Sending a screenshot as a file is what
    /help asks for, and hardly anybody does it -- an ordinary photo comes
    through scaled to 1280px, which puts the cards around 97px wide and the
    rank glyphs below the size a template match can call.

    Every fixture still reads back exactly, and with nothing left to ask
    about: the picture is enlarged before it is read, which recovers the
    registration the scaling cost, and the deck settles the few cards that
    stay shaky."""
    bank = TemplateBank.load(BANK_PATH)
    original = cv2.imread(str(image_path))
    assert original is not None, f"could not read {image_path}"

    result = recognize(_as_sent(image_path, original), bank)

    assert result.state == parse_board(expected_path.read_text(encoding="utf-8")), name
    assert result.uncertain == [], [r.where for r in result.uncertain]
    assert result.narrow is not None, "the point of the fixture is that it is small"
    assert result.narrow < MIN_RELIABLE_CARD_WIDTH


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
def test_enlarging_the_picture_is_what_makes_a_photo_readable(monkeypatch):
    """The control for the test above, and the reason ``enlarge`` exists.

    Nothing else in the pipeline changes with the scale -- the crops are cut
    in fractions of the card width and the templates are matched at a fixed
    size -- so the enlargement is easy to mistake for a no-op that only costs
    memory. It is not: read at the size it arrived, this same screenshot comes
    out as a board that could not exist."""
    bank = TemplateBank.load(BANK_PATH)
    photo = cv2.imread(str(FIXTURES / "telegram" / "shot1.jpg"))
    expected = parse_board((FIXTURES / "telegram" / "shot1.txt").read_text())

    assert recognize(photo, bank).state == expected

    # Nothing is small enough to enlarge any more, so the reader looks at the
    # pixels it was given.
    monkeypatch.setattr(RECOGNIZE, "MIN_RELIABLE_CARD_WIDTH", 0)
    with pytest.raises(RecognitionError):
        recognize(photo, bank)


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
def test_a_board_that_reads_but_came_in_small_is_flagged_for_checking():
    """Between "reads perfectly" and "cannot possibly be right" there is a
    band where most boards still come out correct. Those are not refused --
    but the width comes back with the read, which is what stops the bot
    treating it as something it is sure of.

    A number rather than a sentence, deliberately: the caller says it in the
    user's own language, and the reader has no business writing English into
    the middle of a Russian reply."""
    bank = TemplateBank.load(BANK_PATH)
    original = cv2.imread(str(FIXTURES / "ipad" / "shot1.png"))

    result = recognize(_as_telegram_photo(original), bank)
    assert result.state == parse_board((FIXTURES / "ipad" / "shot1.txt").read_text())
    assert not result.confident
    assert result.narrow is not None
    assert result.narrow < MIN_RELIABLE_CARD_WIDTH


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
@pytest.mark.parametrize(
    "name,image_path,expected_path", FIXTURE_CASES, ids=lambda v: v if isinstance(v, str) else ""
)
def test_a_mildly_degraded_screenshot_still_needs_no_questions(name, image_path, expected_path):
    """Squeezed enough that individual cards start coming out shaky, every one
    of these still reads back exactly right and with nothing to ask about:
    each card exists once in the deck, so a card the matcher is unsure of is
    pinned by the thirty-nine around it.

    This is the whole point of the resolver, so it is checked on every fixture
    rather than on a representative one."""
    bank = TemplateBank.load(BANK_PATH)
    original = cv2.imread(str(image_path))
    degraded = _as_sent(image_path, original, width=1600)

    result = recognize(degraded, bank)
    assert result.state == parse_board(expected_path.read_text(encoding="utf-8")), name
    assert result.resolution is not None
    assert result.uncertain == [], [r.where for r in result.uncertain]


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
@pytest.mark.parametrize("quality", range(30, 100, 5))
def test_the_bottom_card_of_a_column_stays_one_card(quality):
    """A photo whose columns are cut wrong is not a shaky read the deck can
    settle: an extra card is an extra card, and the board comes back as one
    that could not exist -- which is how this arrived, as ``missing B4x1;
    duplicated B2x1, B5x1`` on a board whose seventh column really ends in a
    single B4.

    Column 7 of this fixture is the hard case in one picture: eight cards
    deep, so it runs off the bottom of the screen, and the corner glyph of the
    bottom card ends within a third of a stacking offset of where a ninth card
    would start.  Whether that ink cleared the old threshold came down to
    which way the JPEG rounded, so it is pinned across the range of quality
    settings rather than at the one the fixture happens to carry."""
    bank = TemplateBank.load(BANK_PATH)
    photo = cv2.imread(str(FIXTURES / "telegram" / "shot2.jpg"))
    ok, buffer = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok

    result = recognize(cv2.imdecode(buffer, cv2.IMREAD_COLOR), bank)
    expected = parse_board((FIXTURES / "telegram" / "shot2.txt").read_text())
    assert result.state == expected


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
def test_the_deck_does_the_work_that_would_otherwise_be_questions():
    """The same run, counted: across the fixtures at this scale the matcher
    flags cards it cannot call, and the deck settles every one of them."""
    bank = TemplateBank.load(BANK_PATH)
    flagged = settled = 0
    for _name, image_path, _expected in FIXTURE_CASES:
        degraded = _as_sent(image_path, cv2.imread(str(image_path)), width=1600)
        result = recognize(degraded, bank)
        flagged += sum(1 for r in result.reads if not r.confident and r.resolvable)
        settled += result.deduced

    assert flagged > 0, "nothing was shaky, so this proves nothing"
    assert settled == flagged, (settled, flagged)


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
def test_a_reading_that_does_not_add_up_comes_back_as_a_draft_to_correct():
    """A picture squeezed past what the reader can recover from is the one
    case left where there is no board to show. There is still a reading, and
    most of its forty cards are right, so it comes back in the input notation
    for the user to fix the handful that are not -- which beats both retyping
    the position and being told to go and find a better screenshot."""
    bank = TemplateBank.load(BANK_PATH)
    original = cv2.imread(str(FIXTURES / "iphone" / "shot1.png"))
    # Scaled as Telegram scales it and then compressed harder than Telegram
    # compresses it -- past what enlarging can recover.
    mangled = _as_telegram_photo(original, width=1280, quality=20)

    with pytest.raises(RecognitionError) as excinfo:
        recognize(mangled, bank)

    error = excinfo.value
    assert error.narrow
    draft = error.draft
    assert draft is not None

    # Eight column lines of card codes: the user edits cards, not syntax. It
    # does not add up as a board -- that is why it is a draft and not a read.
    columns = [line for line in draft.splitlines() if line[:1].isdigit()]
    assert len(columns) == NUM_COLUMNS
    with pytest.raises(InvalidBoard):
        parse_board(draft)

    # And it is worth handing over: most of the deck is already right, so what
    # is left is correcting a few cards rather than typing out forty.
    expected = parse_board((FIXTURES / "iphone" / "shot1.txt").read_text())
    wanted = Counter(card for column in expected.columns for card in column)
    drafted = Counter(
        parse_card(token) for line in columns for token in line.split()[1:]
    )
    assert sum((wanted & drafted).values()) >= 0.9 * sum(wanted.values())
