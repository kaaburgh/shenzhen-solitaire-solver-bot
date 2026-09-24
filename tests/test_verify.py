"""Deciding whether a card is worth a human's eyes at all.

Driven through the real resolver, so what the check sees here is the shape of
thing a real screenshot hands it: reads with scores, some of them shaky, some
of them settled by deck arithmetic.
"""

from __future__ import annotations

from test_resolve import AMBIGUOUS, screen

from shenzhen.cards import parse_card
from shenzhen.notation import card_mark, describe_slot
from shenzhen.vision.resolve import resolve
from shenzhen.vision.verify import column_depths, spot_check


def checked(unsure=None, pinned=None, *, grid_anchored=True):
    view = screen(AMBIGUOUS, unsure=unsure)
    resolution = resolve(view.skeleton, view.reads)
    pinned = {view.index_of(where): parse_card(card) for where, card in (pinned or {}).items()}
    return view, spot_check(
        view.reads,
        resolution,
        pinned,
        grid_anchored=grid_anchored,
    )


def test_a_board_read_cleanly_is_not_put_up_for_checking_at_all():
    """The deck already proved this one: forty cards, each exactly once. There
    is nothing a human can add by reading four of them back."""
    _, check = checked()
    assert check is None


def test_shaky_reads_the_deck_agreed_with_are_not_worth_a_question():
    """The matcher was unsure and the deck came to the same answer anyway. Two
    independent verdicts agreeing is the strongest evidence available -- asking
    is asking a human to overrule both."""
    _, check = checked({"1.1": ["G1", "G5"], "3.1": ["R2", "R7"], "5.1": ["B3", "B8"]})
    assert check is None


def test_the_one_card_the_deck_overruled_is_the_one_put_up():
    """G3 misread as G8. The deck put G3 back, and that slot is the single
    place where the two ways of reading the board disagreed."""
    view, check = checked({"1.3": ["G8", "G3"]})

    assert check is not None
    assert check.where == "1.3"
    # As corrected, not as misread: offering the matcher's losing guess would
    # be asking the user to confirm a card the bot is not going to use.
    assert check.card == parse_card("G3")
    assert check.index == view.index_of("1.3")


def test_only_one_card_goes_up_however_many_the_deck_corrected():
    """Confirming one correction corroborates the arithmetic that produced the
    rest of them -- they all came out of the same forty-card count."""
    # G3 read as G8 and R2 read as R7, in different columns and settled
    # independently: both are corrections, and one question covers both.
    view, check = checked({"1.3": ["G8", "G3"], "3.1": ["R7", "R2"]})
    resolution = resolve(view.skeleton, view.reads)
    overruled = {
        i for i, card in resolution.settled.items() if card != view.reads[i].card
    }
    assert len(overruled) == 2

    assert check is not None
    assert check.index in overruled


def test_a_card_the_user_has_already_answered_is_not_put_back_up():
    """They answered it by looking at the screen a moment ago; asking again
    tests nothing."""
    _, check = checked({"1.3": ["G8", "G3"]}, pinned={"1.3": "G3"})
    assert check is None


def test_a_card_still_open_is_left_to_the_question_rather_than_checked():
    """An open card is not a confirmation, it is a question -- and the bot puts
    it as one, with its alternatives. A check beside it would say less."""
    view = screen(AMBIGUOUS, unsure={"1.3": ["G3", "G8"], "2.3": ["G8", "G3"]})
    resolution = resolve(view.skeleton, view.reads)
    assert resolution.open

    assert spot_check(view.reads, resolution, grid_anchored=True) is None


def test_an_unanchored_grid_puts_up_a_card_the_matcher_was_sure_of():
    """When the dragon buttons are missing, the layout guesses which slot the
    leftmost column occupies. A wrong guess slides every column sideways with
    all forty cards still present, so the deck is content and only naming a
    column out loud catches it."""
    view, check = checked(grid_anchored=False)

    assert check is not None
    assert "." in check.where, "a control card has to be named by its column"
    assert view.reads[check.index].confident


def test_an_anchored_grid_needs_no_control_card_even_if_layout_has_other_problems():
    """A local layout warning does not make every column label doubtful.

    The warning text is deliberately not an input to spot_check any more; the
    only fact that can ask for a geometry control card is whether the slot grid
    itself was anchored.
    """
    _, check = checked(grid_anchored=True)
    assert check is None


def test_a_position_that_was_typed_out_has_nothing_to_check():
    assert spot_check([], None, grid_anchored=True) is None
    assert spot_check([], grid_anchored=True) is None


def test_the_bottom_card_of_a_column_is_named_rather_than_counted_to():
    """Counting five cards down a stack is exactly the fiddly part, and the
    bottom card -- the one you can pick up -- needs no counting at all."""
    view = screen(AMBIGUOUS)
    depths = column_depths(view.reads)
    assert depths["1"] == 5

    assert "нижняя" in describe_slot("1.5", "ru", depth_total=depths["1"])
    assert "верхняя" in describe_slot("1.1", "ru", depth_total=depths["1"])
    assert "bottom" in describe_slot("1.5", "en", depth_total=depths["1"])
    assert describe_slot("1.5", "en") == "column 1, card 5 from the top"


def test_the_check_knows_how_deep_its_column_runs():
    """So the slot can be named the way someone looking at the screen would
    find it, rather than counted down to."""
    _, check = checked({"1.3": ["G8", "G3"]})
    assert check.depth_total == 5


def test_a_card_is_shown_as_colour_and_rank_not_as_a_letter_code():
    """`R9` names the colour in a letter the reader has to translate back.
    Telegram has no coloured text, so the colour arrives as a glyph."""
    assert card_mark(parse_card("R9")) == "🔴9"
    assert card_mark(parse_card("G1")) == "🟢1"
    assert card_mark(parse_card("B7")) == "⚫7"
    # Dragons have no rank, so the mark stands alone -- a square rather than a
    # circle, so shape says which kind of card it is before colour says which.
    assert card_mark(parse_card("DG")) == "🟩"
    assert card_mark(parse_card("DR")) == "🟥"
    assert card_mark(parse_card("DB")) == "⬜"
    assert card_mark(parse_card("F")) == "🌸"
