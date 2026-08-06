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
from fake_board import CARD_H, CARD_W, OFFSET, TABLEAU_Y, render, slot_x

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
from shenzhen.vision.classify import TemplateBank, ink_colour
from shenzhen.vision.crop import CONTEXT, MARK, VIEW_CARD_W, card_crop
from shenzhen.vision.layout import (
    LayoutConfig,
    LayoutError,
    corner_patch,
    detect_layout,
    find_dragon_buttons,
)
from shenzhen.vision.recognize import (
    MIN_RECOVERABLE_CARD_WIDTH,
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


def test_a_bar_the_phone_draws_over_the_board_costs_no_columns():
    """The iPhone home indicator lies across the bottom of every screenshot,
    over whichever columns run that far down.  It is pale, so the card mask
    takes it for a card face, and it *touches* the column, so it is not a
    stray blob that could be ignored: the two fuse into one component several
    cards wide, which is then dropped as not card-shaped.

    That is a whole column missing from the reading rather than a card read
    wrong -- and a missing column is the one error the deck check cannot
    repair, since forty cards minus a column is not a board at all.  Drawn
    here across four slots at once, because the bar is wide enough to bridge
    neighbouring columns as well as to widen one.
    """
    state = auto_resolve(raw_deal(3))[0]
    image = render(state)
    deepest = max(len(c) for c in state.columns)
    row = TABLEAU_Y + (deepest - 1) * OFFSET + int(CARD_H * 0.8)
    cv2.rectangle(
        image,
        (slot_x(2), row),
        (slot_x(5) + CARD_W, row + int(CARD_W * 0.06)),
        (240, 240, 240),
        -1,
    )

    layout = detect_layout(image, CONFIG)
    assert [len(c) for c in layout.columns] == [len(c) for c in state.columns]
    assert not layout.warnings


def test_the_gap_between_the_two_rows_is_never_bridged():
    """The join that puts a column back together only closes a gap an overlay
    was cut out of.  Nothing else qualifies -- in particular the strip of felt
    between the top row and the tableau, which is narrower than the bar above
    and would fuse a free cell to the column beneath it."""
    state = auto_resolve(raw_deal(5))[0]
    layout = detect_layout(render(state), CONFIG)

    assert [len(c) for c in layout.columns] == [len(c) for c in state.columns]
    for index, cell in enumerate(state.free):
        assert (layout.free_cells[index] is not None) == (cell is not None)


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
    assert result.source_card_w is not None
    assert result.source_card_w < MIN_RELIABLE_CARD_WIDTH, (
        "the point of the fixture is that it arrives below what reads on its own"
    )
    # And having read it exactly, there is nothing to tell the sender about
    # the size of what they sent.
    assert result.narrow is None


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
def test_only_a_picture_smaller_than_telegram_sends_is_flagged_as_small():
    """Where the "send it as a file" advice belongs, and where it does not.

    A screenshot sent as a photo is not small in any sense worth telling its
    sender about: it is the size everybody's screenshots arrive at, it is the
    size every fixture reads back exactly at, and a reading that fails there
    failed at something else -- for a long time at a column the phone had
    drawn its home indicator across. Saying "your picture is too small" to
    that sends them off to fix what was not broken.

    Scaled down by hand until the cards are past what enlarging can recover,
    the size really is the thing to fix, and then it is worth saying. A number
    rather than a sentence, deliberately: the caller says it in the user's own
    language, and the reader has no business writing English into the middle
    of a Russian reply."""
    bank = TemplateBank.load(BANK_PATH)
    original = cv2.imread(str(FIXTURES / "ipad" / "shot1.png"))

    result = recognize(_as_telegram_photo(original), bank)
    assert result.state == parse_board((FIXTURES / "ipad" / "shot1.txt").read_text())
    assert result.source_card_w is not None
    assert result.source_card_w < MIN_RELIABLE_CARD_WIDTH
    assert result.narrow is None

    smaller = recognize(_as_telegram_photo(original, width=800), bank)
    assert smaller.narrow is not None
    assert smaller.narrow < MIN_RECOVERABLE_CARD_WIDTH


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
    # Squeezed, not shrunk: it arrived at the width every photo arrives at, so
    # there is no "send a bigger picture" to offer here, only the draft.
    assert not error.narrow
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


# --- the picture that goes with a question ---------------------------------


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
def test_a_slot_can_be_cut_back_out_of_the_picture_that_was_sent():
    """A question about "column 6, the bottom card" is only answerable from the
    game unless the bot shows which card it means. The reader works on an
    enlarged copy, so the boxes it hands out have to map back onto the picture
    the user actually sent before anything can be cut from it."""
    bank = TemplateBank.load(BANK_PATH)
    original = cv2.imread(str(FIXTURES / "iphone" / "shot1.png"))
    photo = _as_telegram_photo(original)

    result = recognize(photo, bank)
    assert result.scale > 1, "the point of this fixture is that it was enlarged"
    height, width = photo.shape[:2]

    for index, read in enumerate(result.reads):
        box = result.source_box(index)
        assert 0 <= box.x and box.x + box.w <= width, read.where
        assert 0 <= box.y and box.y + box.h <= height, read.where
        # Every card is drawn the same width, whatever is stacked on top of it.
        assert abs(box.w - result.source_card_w) <= result.scale, read.where


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
def test_the_crop_marks_the_card_and_is_bigger_than_the_picture_it_came_from():
    """Both halves matter. Without the mark the crop shows several cards and
    says nothing about which one is being asked about; without the enlargement
    it is handed back at the size that caused the question."""
    bank = TemplateBank.load(BANK_PATH)
    photo = _as_telegram_photo(cv2.imread(str(FIXTURES / "iphone" / "shot1.png")))

    result = recognize(photo, bank)
    index = next(i for i, r in enumerate(result.reads) if r.where == "1.1")
    box = result.source_box(index)

    data = card_crop(photo, box, result.source_card_w)
    assert data is not None
    crop = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)

    assert crop.shape[1] >= VIEW_CARD_W, crop.shape
    assert crop.shape[1] > box.w, "handed back no larger than it arrived"

    # The outline is in a colour the board does not contain, so finding it at
    # all is enough to say the card was marked.
    near_mark = np.all(np.abs(crop.astype(int) - np.array(MARK)) < 60, axis=2)
    assert near_mark.sum() > 0, "the slot was not marked"

    # And there is board around it rather than the card alone.
    assert crop.shape[1] > box.w * (1 + 2 * CONTEXT) * 0.9


def test_a_box_off_the_edge_of_the_picture_costs_the_question_nothing():
    """A crop is a nicety; a question with no picture beside it is the question
    the bot used to ask, which is worse but not broken."""
    from shenzhen.vision.layout import Box

    image = np.zeros((40, 40, 3), dtype=np.uint8)
    assert card_crop(image, Box(500, 500, 10, 10), 10) is None
    assert card_crop(image, Box(0, 0, 10, 10), 0) is None


# --- the benchmark ---------------------------------------------------------


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
def test_the_benchmark_scores_the_fixtures_it_is_pointed_at(capsys):
    """The tuning tool, kept honest by the suite.

    It is only ever run by hand, which is exactly how a tool rots: nobody
    notices it stopped importing until the afternoon they need it. Reading the
    fixtures as they came off the device is the one run whose answer is known
    -- every card right, no board wrong -- so it doubles as a check that the
    scoring itself is not lying."""
    from shenzhen.vision import benchmark

    assert benchmark.main(["--fixtures", str(FIXTURES), "--bank", str(BANK_PATH), "--width", "0"]) == 0

    printed = capsys.readouterr().out
    assert "confident-wrong 0" in printed, printed
    assert f"boards {len(FIXTURE_CASES)}/{len(FIXTURE_CASES)}" in printed, printed


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
def test_the_benchmark_leaves_a_picture_telegram_has_already_squeezed_alone(monkeypatch):
    """A .jpg fixture came back out of Telegram. Squeezing it a second time
    would measure a picture nobody will ever send, and would quietly make the
    hardest fixtures in the set harder than reality every time the benchmark
    is run."""
    from shenzhen.vision import benchmark

    cases = benchmark.fixtures(FIXTURES)
    pngs = [path for _n, path, _e in cases if path.suffix == ".png"]
    assert len(pngs) < len(cases), "the point of this test is that a .jpg fixture exists"

    squeezed = []
    original = benchmark.as_telegram_photo
    monkeypatch.setattr(
        benchmark,
        "as_telegram_photo",
        lambda image, width, quality: (
            squeezed.append(width) or original(image, width, quality)
        ),
    )

    score = benchmark.run(
        FIXTURES, TemplateBank.load(BANK_PATH), width=1280, quality=80, verbose=False
    )

    assert len(squeezed) == len(pngs), "a .jpg fixture was recompressed"
    assert score.boards_total == len(cases)


@pytest.mark.skipif(not BANK_PATH.is_dir(), reason="no template bank installed")
def test_a_fixture_the_layout_cannot_read_is_scored_not_raised(tmp_path):
    """A geometry change that makes a fixture unreadable is exactly when the
    comparison against the previous run is worth having, so it must not be the
    thing that stops the run from printing one.

    And the board's own cards count against the score even though none of them
    was read. Scoring what came back instead would mean the worse the geometry
    got the better the number looked -- a fixture that produced no reads at all
    would come out at a clean 100%."""
    from shenzhen.vision import benchmark

    labels = (FIXTURES / "iphone" / "shot1.txt").read_text(encoding="utf-8")
    unreadable = tmp_path / "junk"
    unreadable.mkdir()
    cv2.imwrite(str(unreadable / "shot1.png"), np.zeros((400, 900, 3), dtype=np.uint8))
    (unreadable / "shot1.txt").write_text(labels, encoding="utf-8")

    score = benchmark.Score()
    outcome = benchmark.score_one(
        cv2.imread(str(unreadable / "shot1.png")),
        TemplateBank.load(BANK_PATH),
        labels,
        score,
        verbose=False,
    )

    assert "NO LAYOUT" in outcome, outcome
    assert score.boards_total == 1 and score.boards_right == 0
    assert score.cards_right == 0
    assert score.cards_total == len(benchmark.expected_cards(labels))
    assert score.cards_total > 30, "the whole board should be counted against it"


# --- a column the components lost ------------------------------------------


def test_a_column_the_components_lose_is_read_off_the_grid():
    """Defence in depth behind the overlay cut-out, and independent of it.

    The board is a fixed grid: eight slots one pitch apart, every column
    starting at the same y. So a slot no component landed in is not
    necessarily an empty column -- it may be a column something was drawn
    across, fused with it, and thrown out as not card-shaped. That costs six
    cards at once, which is the one error the deck cannot repair.

    Checked with the overlay cut-out switched off, because the point is that
    the two mechanisms do not depend on each other: either one alone reads
    this screenshot, which is what makes the pair worth having."""
    bank = TemplateBank.load(BANK_PATH)
    photo = cv2.imread(str(FIXTURES / "telegram" / "shot3.jpg"))
    expected = parse_board((FIXTURES / "telegram" / "shot3.txt").read_text())

    # An opening kernel this long finds nothing, so the bar stays in the mask
    # and the component pass loses column 4 exactly as it used to.
    without_cut_out = LayoutConfig(overlay_width=99.0)
    result = recognize(photo, bank, without_cut_out)

    assert result.state == expected
    assert any("column 4" in w for w in result.warnings), result.warnings


def test_an_empty_column_is_not_invented_out_of_felt():
    """The other half of it. The game draws *nothing* for an empty tableau
    column -- no outline, no placeholder, only felt -- so "there is a column
    here the components missed" and "this column is empty" are told apart by
    how much of the slot reads as card, and nothing else.

    Get that wrong in this direction and every empty column grows a phantom
    card, which is a deck error on boards that used to read perfectly."""
    base = raw_deal(11)
    # Empty the third and sixth columns onto the eighth.
    spare = base.columns[2] + base.columns[5]
    columns = list(base.columns)
    columns[2] = ()
    columns[5] = ()
    columns[7] = columns[7] + spare
    state = State(
        columns=tuple(columns), free=(None, None, None), foundations=(0, 0, 0), flower=False
    )

    layout = detect_layout(render(state), CONFIG)

    assert [len(c) for c in layout.columns] == [len(c) for c in state.columns]
    assert not layout.warnings, layout.warnings


def test_the_grid_origin_is_a_slot_number_not_a_coordinate():
    """``origin`` is backed out of the dragon buttons through a rough offset,
    and it only ever feeds ``_slot_of``, which rounds -- so it needs to be
    right to half a pitch and no better. Measured on a real screenshot it sits
    about half a card away from where the columns actually are.

    Anything that wants a real coordinate has to calibrate against a column
    that was genuinely found, which is what ``column_base`` is for. A crop
    taken at ``origin`` instead straddles the gap and catches two columns at
    60% each -- which looks like a column being there, and reads as neither.
    """
    from shenzhen.vision.layout import column_base

    image = render(raw_deal(2))
    layout = detect_layout(image, CONFIG)
    found = {slot: boxes[0] for slot, boxes in enumerate(layout.columns) if boxes}
    pitch = CONFIG.slot_pitch * layout.card_w

    base = column_base(found, pitch)
    assert base is not None
    for slot, box in found.items():
        assert abs((base + slot * pitch) - box.x) <= 0.1 * layout.card_w


def test_an_empty_slot_is_something_the_game_draws_not_an_absence():
    """The game marks an emptied column with a card-sized patch of lighter
    check.  That is what makes "this column is empty" a thing the picture
    says, rather than a thing inferred from finding nothing -- and it is the
    only reason a column hidden behind something can be told from one that is
    genuinely empty.

    Pinned against the felt beside it because the level itself depends on the
    device and on what the compression left: measured on real screenshots the
    mark runs 1.10-1.19 times the felt, a slot with cards 2.87-3.42."""
    from shenzhen.vision.layout import column_base, felt_level

    base_state = raw_deal(13)
    columns = list(base_state.columns)
    spare = columns[3]
    columns[3] = ()
    columns[6] = columns[6] + spare
    state = State(
        columns=tuple(columns), free=(None, None, None), foundations=(0, 0, 0), flower=False
    )
    image = render(state)
    layout = detect_layout(image, CONFIG)
    assert not layout.columns[3], "column 4 is the empty one here"

    found = {slot: boxes[0] for slot, boxes in enumerate(layout.columns) if boxes}
    pitch = CONFIG.slot_pitch * layout.card_w
    grid = column_base(found, pitch)
    top = min(b.y for b in found.values())
    felt = felt_level(image, grid, pitch, top, layout)

    x = int(round(grid + 3 * pitch))
    mark = image[top + 20 : top + layout.card_h - 20, x + 20 : x + layout.card_w - 20]
    level = cv2.cvtColor(mark, cv2.COLOR_BGR2HSV)[:, :, 2].mean()

    low, high = CONFIG.slot_mark_range
    assert low <= level / felt <= high, level / felt
    assert level / felt > 1.02, "an empty slot is not bare felt"


def test_a_column_hidden_behind_something_is_not_reported_as_empty():
    """The third case, and the reason the mark is worth reading. A slot that
    shows neither cards nor the mark is covered by something -- and saying so
    beats reporting an empty column and letting the deck fail five cards
    later, which is what the reader did before it could tell the two apart."""
    state = auto_resolve(raw_deal(3))[0]
    image = render(state)

    # Something opaque over the whole of column 4, down to the bottom of its
    # deepest card, and felt-coloured so that it is not mistaken for a card
    # either.
    x = slot_x(3)
    deep = TABLEAU_Y + (len(state.columns[3]) - 1) * OFFSET + CARD_H
    cv2.rectangle(image, (x - 4, TABLEAU_Y - 4), (x + CARD_W + 4, deep + 4), (40, 55, 30), -1)

    layout = detect_layout(image, CONFIG)
    assert not layout.columns[3]
    assert any("column 4" in w and "hidden" in w for w in layout.warnings), layout.warnings


def test_a_slot_running_off_the_edge_is_not_called_hidden():
    """A screenshot cropped through a column leaves half a slot in frame, and
    half a slot of cards is as bright as a whole one -- so measuring the
    fragment says "not the empty-slot mark", which is the same answer as
    "something is covering this column". It is not: nobody can say anything
    about a slot that runs off the picture.

    Everything reading a slot compares it against what a *whole* slot looks
    like, so the measurement has to decline rather than answer from whatever
    survived the crop."""
    state = auto_resolve(raw_deal(3))[0]
    image = render(state)

    for cut, name in ((slot_x(7) + CARD_W // 2, "column 8"), (slot_x(0) + CARD_W // 2, "column 1")):
        cropped = image[:, :cut] if name == "column 8" else image[:, cut:]
        layout = detect_layout(cropped, CONFIG)
        assert not any("hidden" in w for w in layout.warnings), (name, layout.warnings)
