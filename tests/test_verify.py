"""Picking the few cards worth checking by hand.

Driven through the real resolver, so what the sampler sees here is the shape
of thing a real screenshot hands it: reads with scores, some of them shaky,
some of them settled by deck arithmetic.
"""

from __future__ import annotations

from test_resolve import AMBIGUOUS, screen

from shenzhen.cards import parse_card
from shenzhen.notation import card_mark, describe_slot
from shenzhen.vision.resolve import resolve
from shenzhen.vision.verify import DOUBTS, column_depths, spot_checks

SHAKY = {"1.1": ["G1", "G5"], "3.1": ["R2", "R7"], "5.1": ["B3", "B8"]}


def read_at(view, checks):
    """The reads the checks name, by slot."""
    return {check.where: check for check in checks}


def sampled(unsure=None, pinned=None):
    view = screen(AMBIGUOUS, unsure=unsure)
    resolution = resolve(view.skeleton, view.reads)
    pinned = {view.index_of(where): parse_card(card) for where, card in (pinned or {}).items()}
    return view, spot_checks(view.reads, resolution, pinned)


def test_the_shaky_reads_are_all_put_up_and_the_rest_are_not():
    view, checks = sampled(SHAKY)

    assert set(SHAKY) <= {c.where for c in checks}
    # Three doubts and the one control card -- not the other thirty-six.
    assert len(checks) == DOUBTS + 1


def test_a_card_the_reader_was_sure_of_goes_up_alongside_them():
    """The doubts catch a misread glyph. Only a card the bot claims to be
    certain about catches the failures that get everything wrong at once -- a
    screenshot of the wrong deal, a grid anchored one slot over."""
    view, checks = sampled(SHAKY)

    control = [c for c in checks if c.where not in SHAKY]
    assert len(control) == 1
    assert view.reads[view.index_of(control[0].where)].confident
    # And from a column none of the shaky ones came from, so a grid read one
    # slot over cannot happen to line up on the whole sample.
    shaky_columns = {where.partition(".")[0] for where in SHAKY}
    assert control[0].where.partition(".")[0] not in shaky_columns


def test_the_sample_reads_down_the_board_rather_than_by_doubt():
    """Board order, so the eye works down the screen -- and so the control card
    does not give itself away by always coming last."""
    _, checks = sampled(SHAKY)
    columns = [int(c.where.partition(".")[0]) for c in checks]
    assert columns == sorted(columns)


def test_the_cards_shown_are_the_ones_the_deck_settled_on():
    """A slot the matcher got wrong and the deck corrected has to be checked as
    corrected -- offering the matcher's own losing guess would be asking the
    user to confirm a card the bot is not going to use."""
    view, checks = sampled({"1.3": ["G8", "G3"]})  # G3 misread as G8

    assert read_at(view, checks)["1.3"].card == parse_card("G3")


def test_a_card_the_user_has_already_answered_is_not_put_back_up():
    """They answered it by looking at the screen a moment ago; asking again
    tests nothing."""
    view, checks = sampled({"1.3": ["G3", "G8"], "2.3": ["G8", "G3"]}, pinned={"1.3": "G3"})
    assert "1.3" not in {c.where for c in checks}


def test_the_flower_slot_is_never_sampled():
    """It can only ever hold the flower, however badly the glyph reads."""
    view, checks = sampled({"flower": ["F", "G3"]})
    assert "flower" not in {c.where for c in checks}


def test_a_cleanly_read_board_still_offers_its_weakest_cards():
    """Nothing shaky on this one, so the sample is whatever won by least --
    which is still a far better use of the user's attention than forty cards."""
    _, checks = sampled()
    assert len(checks) == DOUBTS + 1


def test_a_position_that_was_typed_out_has_nothing_to_sample():
    assert spot_checks([]) == ()


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
