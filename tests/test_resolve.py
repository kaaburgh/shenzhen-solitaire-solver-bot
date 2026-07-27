"""Deck arithmetic over the cards the reader was unsure about.

Driven with made-up reads rather than screenshots: what is under test here is
the elimination, and stating "this slot scored G3 first and G8 second" outright
is both clearer and more pointed than hunting for a real picture that happens
to be ambiguous in the right place.  The screenshot end of it is covered in
``test_vision.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from shenzhen.cards import FLOWER, card_code, is_locked, make_card, parse_card
from shenzhen.game import auto_resolve, deal
from shenzhen.textio import parse_board
from shenzhen.vision.resolve import (
    MAX_POSSIBILITIES,
    Skeleton,
    Unresolvable,
    resolve,
    widest_options,
)


@dataclass
class FakeGuess:
    ranking: tuple[tuple[int, float], ...]
    sure: bool
    confidence: float = 0.95
    margin: float = 0.30

    @property
    def is_confident(self) -> bool:
        return self.sure


@dataclass
class FakeRead:
    card: int
    where: str
    guess: FakeGuess
    resolvable: bool = True

    @property
    def confident(self) -> bool:
        return self.guess.is_confident


@dataclass
class Screen:
    """A board as the reader would have seen it, plus what it was unsure of."""

    skeleton: Skeleton
    reads: list[FakeRead] = field(default_factory=list)

    def index_of(self, where: str) -> int:
        return next(i for i, r in enumerate(self.reads) if r.where == where)

    def card_at(self, where: str) -> int:
        return self.reads[self.index_of(where)].card


def screen(state, unsure: dict[str, list[str]] | None = None) -> Screen:
    """Describe ``state`` as a set of reads, some of them shaky.

    ``unsure`` maps a slot to the ranking the matcher supposedly produced for
    it, best first, as card codes.  The reader's own winner is the first of
    them -- which is how a wrong-but-plausible read gets expressed.
    """
    unsure = {k: [parse_card(c) for c in v] for k, v in (unsure or {}).items()}
    reads: list[FakeRead] = []

    def add(card: int, where: str, *, resolvable: bool = True) -> int:
        ranking = unsure.get(where)
        if ranking is None:
            guess = FakeGuess(ranking=((card, 0.95),), sure=True)
            winner = card
        else:
            guess = FakeGuess(
                ranking=tuple((c, 0.90 - 0.01 * i) for i, c in enumerate(ranking)),
                sure=False,
                confidence=0.90,
                margin=0.01,
            )
            winner = ranking[0]
        reads.append(
            FakeRead(card=winner, where=where, guess=guess, resolvable=resolvable)
        )
        return len(reads) - 1

    columns = tuple(
        tuple(add(card, f"{i + 1}.{d + 1}") for d, card in enumerate(column))
        for i, column in enumerate(state.columns)
    )

    free: list[int | None] = []
    locked: list[int] = []
    for i, cell in enumerate(state.free):
        if cell is None:
            free.append(None)
        elif is_locked(cell):
            free.append(None)
            locked.append(i)
        else:
            free.append(add(cell, f"free{i + 1}"))

    if state.flower:
        add(FLOWER, "flower", resolvable=False)

    foundations: list[int | None] = [None, None, None]
    slot = 0
    for suit, top in enumerate(state.foundations):
        if top:
            foundations[slot] = add(make_card(suit, top), f"foundation{slot + 1}")
            slot += 1

    return Screen(
        skeleton=Skeleton(
            columns=columns,
            free=tuple(free),
            locked=tuple(locked),
            flower_slot=state.flower,
            foundations=tuple(foundations),
        ),
        reads=reads,
    )


BOARD = auto_resolve(deal(7))[0]


# --- the deck settling things by itself ------------------------------------


def test_a_board_read_cleanly_has_nothing_to_ask_about():
    view = screen(BOARD)
    resolution = resolve(view.skeleton, view.reads)

    assert resolution.state == BOARD
    assert resolution.open == ()
    assert resolution.certain


def test_a_single_shaky_card_is_settled_by_what_is_left_over():
    """Thirty-nine cards read confidently leaves exactly one card it can be,
    however badly the fortieth scored."""
    where = "3.1"
    truth = BOARD.columns[2][0]
    decoy = card_code(BOARD.columns[4][0])
    view = screen(BOARD, unsure={where: [decoy, card_code(truth)]})

    # The reader's own winner is the wrong one.
    assert view.card_at(where) != truth

    resolution = resolve(view.skeleton, view.reads)
    assert resolution.open == ()  # nothing to ask
    assert resolution.settled[view.index_of(where)] == truth
    assert resolution.state == BOARD


def test_several_shaky_cards_still_need_no_question_when_they_do_not_collide():
    slots = {
        "1.1": [card_code(BOARD.columns[0][0]), "G5"],
        "2.1": [card_code(BOARD.columns[1][0]), "R4"],
        "4.2": [card_code(BOARD.columns[3][1]), "B7"],
    }
    view = screen(BOARD, unsure=slots)
    resolution = resolve(view.skeleton, view.reads)

    assert resolution.open == ()
    assert len(resolution.settled) == 3
    assert resolution.state == BOARD


# --- the case worth asking about -------------------------------------------


AMBIGUOUS = parse_board(
    """
    free: . . .
    foundations: 0 0 0
    1: G1 G2 G3 G4 G5
    2: G6 G7 G8 G9 R1
    3: R2 R3 R4 R5 R6
    4: R7 R8 R9 B1 B2
    5: B3 B4 B5 B6 B7
    6: B8 B9 DG DG DG
    7: DG DR DR DR DR
    8: DB DB DB DB F
    """
)


def test_two_slots_that_could_be_each_other_cost_exactly_one_question():
    """The 3-versus-8 case: the reader cannot tell which slot holds which, but
    the deck knows it holds one of each, so pinning either one pins both."""
    a, b = "1.3", "2.3"  # G3 and G8
    view = screen(AMBIGUOUS, unsure={a: ["G3", "G8"], b: ["G8", "G3"]})

    resolution = resolve(view.skeleton, view.reads)
    assert len(resolution.possibilities) == 2
    assert set(resolution.open) == {view.index_of(a), view.index_of(b)}

    # One question -- either one will do, both split the field the same way.
    asked = resolution.next_question()
    assert asked in resolution.open
    assert set(map(card_code, resolution.options(asked))) == {"G3", "G8"}

    answered = resolve(
        view.skeleton, view.reads, {asked: parse_card("G3")}, level=resolution.level
    )
    assert answered.open == ()  # the other one fell out for free
    assert answered.state == AMBIGUOUS


def test_the_question_chosen_is_the_one_that_splits_the_field_most():
    """Two slots are a coin toss between the same pair; a third is a three-way.
    Asking the three-way first leaves fewer boards standing either way."""
    view = screen(
        AMBIGUOUS,
        unsure={
            "1.3": ["G3", "G8"],
            "2.3": ["G8", "G3"],
            "3.3": ["R4", "R5", "R6"],
            "3.4": ["R5", "R4", "R6"],
            "3.5": ["R6", "R4", "R5"],
        },
    )
    resolution = resolve(view.skeleton, view.reads)

    asked = resolution.next_question()
    assert len(resolution.options(asked)) == 3
    assert view.reads[asked].where.startswith("3.")


def test_an_answer_that_cannot_coexist_with_the_rest_is_rejected():
    view = screen(AMBIGUOUS, unsure={"1.3": ["G3", "G8"], "2.3": ["G8", "G3"]})
    resolution = resolve(view.skeleton, view.reads)
    index = view.index_of("2.3")

    # G3 is already spoken for by 1.3 under every surviving reading, and no
    # other card can take its place there.
    with pytest.raises(Unresolvable):
        resolve(
            view.skeleton,
            view.reads,
            {index: parse_card("G3"), view.index_of("1.3"): parse_card("G3")},
            level=resolution.level,
        )


# --- widening past what the templates suggested ----------------------------


def test_none_of_these_offers_cards_the_shortlist_left_out():
    """The escape hatch.  B7 scored fifth for slot 5.1, so the shortlist cuts
    it -- but there is a legal board with it there, and the user is the one
    who can see that."""
    view = screen(
        AMBIGUOUS,
        unsure={
            "5.1": ["B3", "B4", "B5", "B6", "B7"],  # capped to the first four
            "5.2": ["B4", "B3"],
            "5.5": ["B7", "B3"],
        },
    )
    index = view.index_of("5.1")

    offered = resolve(view.skeleton, view.reads).options(index)
    wider = widest_options(view.skeleton, view.reads, {}, index)

    assert set(map(card_code, offered)) == {"B3", "B4"}
    assert parse_card("B7") in wider
    assert set(offered) <= set(wider)
    assert len(wider) < 31  # the deck still rules most of the pack out


def test_widening_respects_answers_already_given():
    view = screen(AMBIGUOUS, unsure={"1.3": ["G3", "G8"], "2.3": ["G8", "G3"]})
    pinned = {view.index_of("1.3"): parse_card("G3")}

    wider = widest_options(view.skeleton, view.reads, pinned, view.index_of("2.3"))
    assert parse_card("G3") not in wider  # taken
    assert parse_card("G8") in wider


# --- honesty about what it does not know -----------------------------------


def test_a_board_that_cannot_be_made_legal_is_refused_rather_than_guessed():
    view = screen(BOARD)
    # Overwrite a confident read with a duplicate of another card, and give it
    # no runners-up to escape through.
    index = view.index_of("1.1")
    twin = view.card_at("2.1")
    view.reads[index] = FakeRead(
        card=twin, where="1.1", guess=FakeGuess(ranking=((twin, 0.95),), sure=True)
    )

    with pytest.raises(Unresolvable) as caught:
        resolve(view.skeleton, view.reads)
    assert card_code(twin) in str(caught.value)


def test_a_flooded_search_admits_it_rather_than_claiming_a_settled_card():
    """With this many open cards the search stops early, and a card looking
    settled across the boards it did reach means nothing -- so it says so."""
    # Every dragon on the board, each read as "some dragon, colour unclear".
    # Four of each colour makes 12!/(4!4!4!) = 34650 legal readings, which is
    # a long way past what the search will enumerate.
    dragons = ["6.3", "6.4", "6.5", "7.1", "7.2", "7.3", "7.4", "7.5"]
    dragons += ["8.1", "8.2", "8.3", "8.4"]
    view = screen(AMBIGUOUS, unsure={where: ["DG", "DR", "DB"] for where in dragons})
    resolution = resolve(view.skeleton, view.reads)

    assert resolution.truncated
    assert not resolution.interviewable
    assert not resolution.certain
    assert len(resolution.possibilities) <= MAX_POSSIBILITIES


def test_the_flower_slot_is_never_a_question():
    """It can only ever hold the flower, so however the glyph reads there is
    nothing there to ask a human about."""
    assert AMBIGUOUS.flower  # it played itself the moment column 8 exposed it
    view = screen(AMBIGUOUS)
    flower = view.index_of("flower")
    view.reads[flower].guess = FakeGuess(ranking=((FLOWER, 0.4),), sure=False)

    resolution = resolve(view.skeleton, view.reads)
    assert flower not in resolution.open
    assert flower not in resolution.settled
    assert resolution.state == AMBIGUOUS
