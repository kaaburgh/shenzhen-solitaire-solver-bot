"""The goals a solved line is cut into, and how they are said.

The plan is an interpretation, so what can be tested is not that it picked the
goals a person would have named -- there is no such ground truth -- but that
every claim it makes about the position is one the position supports: the moves
it covers are the moves it says, the columns it names are the columns the cards
are in, and the goal at the end of a phase really is reached there.
"""

from __future__ import annotations

import pytest

from shenzhen.cards import make_card, make_dragon
from shenzhen.game import deal
from shenzhen.notation import describe_goal, describe_plan
from shenzhen.plan import (
    MAX_PHASES,
    Goal,
    GoalKind,
    Phase,
    phase_of,
    plan,
)
from shenzhen.solver import Status, solve

SEEDS = range(16)


def solved(seed: int):
    result = solve(deal(seed), max_nodes=200_000, time_limit=15)
    if result.status is not Status.SOLVED:
        pytest.skip(f"deal {seed} was not solved within the budget")
    return result


def test_a_won_position_has_no_plan():
    assert plan([]) == []


@pytest.mark.parametrize("seed", SEEDS)
def test_the_phases_cover_the_line_exactly_once(seed):
    """The plan is a partition of the moves, not a selection of them.

    It is shown next to the numbered move list and says which moves belong to
    which goal, so a gap or an overlap would misfile a move the player is about
    to play.
    """
    result = solved(seed)
    phases = plan(result.steps)

    assert phases
    assert phases[0].start == 0
    assert phases[-1].end == len(result.steps) - 1
    for earlier, later in zip(phases, phases[1:]):
        assert later.start == earlier.end + 1
    assert sum(phase.length for phase in phases) == len(result.steps)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_plan_stays_short_enough_to_read(seed):
    assert len(plan(solved(seed).steps)) <= MAX_PHASES + 2


@pytest.mark.parametrize("seed", SEEDS)
def test_every_phase_reaches_the_goal_it_claims(seed):
    """The last move of a phase is where its goal is achieved."""
    result = solved(seed)
    for phase in plan(result.steps):
        last = result.steps[phase.end]
        before, after = last.state_before, last.state_after
        goal = phase.goal

        if goal.kind is GoalKind.DRAGONS:
            assert last.move.kind == "dr"
            assert last.move.a == goal.suit
        elif goal.kind is GoalKind.ACE:
            assert before.foundations[goal.suit] == 0
            assert after.foundations[goal.suit] >= 1
        elif goal.kind is GoalKind.COLUMN:
            assert before.columns[goal.column]
            assert not after.columns[goal.column]
        elif goal.kind is GoalKind.COLLECT:
            assert after.foundations[goal.suit] == goal.rank
            assert after.foundations[goal.suit] > before.foundations[goal.suit]
        else:
            assert goal.kind is GoalKind.FINISH


@pytest.mark.parametrize("seed", SEEDS)
def test_the_evidence_describes_the_board_the_player_is_looking_at(seed):
    """Every detail a goal carries is checkable against the phase's first
    position -- the board in front of whoever is reading the line.

    Naming the columns the dragons sit in *at the moment they collapse* would be
    just as true and no use at all: that is a board ten moves from now.
    """
    result = solved(seed)
    for phase in plan(result.steps):
        state = result.steps[phase.start].state_before
        goal = phase.goal

        if goal.kind is GoalKind.DRAGONS:
            dragon = make_dragon(goal.suit)
            assert goal.columns == tuple(
                i for i, col in enumerate(state.columns) if dragon in col
            )
        if goal.kind is GoalKind.ACE and goal.column is not None:
            column = state.columns[goal.column]
            assert column[-1 - goal.buried] == make_card(goal.suit, 1)


@pytest.mark.parametrize("seed", SEEDS)
def test_what_lands_in_an_emptied_column_really_lands_there(seed):
    result = solved(seed)
    for phase in plan(result.steps):
        if phase.goal.kind is not GoalKind.COLUMN or phase.goal.lands is None:
            continue
        # The card is named as the one that goes in next, so the column has to
        # be empty when it does and hold it afterwards.
        landing = next(
            step
            for step in result.steps[phase.end + 1 :]
            if step.move.b == phase.goal.column
            and step.move.kind in ("tt", "ft")
        )
        assert not landing.state_before.columns[phase.goal.column]
        assert landing.state_after.columns[phase.goal.column][0] == phase.goal.lands


@pytest.mark.parametrize("seed", SEEDS)
def test_a_phase_does_not_repeat_its_own_goal_as_an_aside(seed):
    for phase in plan(solved(seed).steps):
        for aside in phase.also:
            assert (aside.kind, aside.suit) != (phase.goal.kind, phase.goal.suit)


def test_a_long_stretch_of_collecting_gets_a_goal_of_its_own():
    """Sixteen moves under one heading say nothing about the next five.

    Deal 0 spends its middle stretch feeding the red foundation before the red
    dragons can be reached at all, and that stretch is what the plan has to
    name -- not just the collapse it ends in.
    """
    phases = plan(solved(0).steps)
    collecting = [p for p in phases if p.goal.kind is GoalKind.COLLECT]
    assert collecting, describe_plan(phases)
    assert max(p.length for p in phases) <= 16


def test_phase_of_finds_the_goal_a_move_belongs_to():
    phases = [
        Phase(Goal(GoalKind.FINISH), start=0, end=2),
        Phase(Goal(GoalKind.FINISH), start=3, end=4),
    ]
    assert phase_of(phases, 0) is phases[0]
    assert phase_of(phases, 2) is phases[0]
    assert phase_of(phases, 3) is phases[1]
    assert phase_of(phases, 5) is None
    assert phase_of((), 0) is None


# --- wording ---------------------------------------------------------------


@pytest.mark.parametrize("lang", ("ru", "en"))
@pytest.mark.parametrize("seed", (0, 1, 2, 3))
def test_every_goal_is_said_in_both_languages(seed, lang):
    """No goal falls through to a bare enum name or a stray ``None``."""
    phases = plan(solved(seed).steps)
    text = describe_plan(phases, lang)
    assert len(text.splitlines()) == len(phases)
    for line in text.splitlines():
        assert "None" not in line
        assert "GoalKind" not in line
        assert line.strip()


def test_a_goal_names_the_cards_the_way_they_are_drawn():
    """Colour and rank, not letter codes: the plan is checked against a screen
    where a suit is a colour."""
    goal = Goal(GoalKind.ACE, suit=1, column=3, buried=2)
    assert describe_goal(goal, "ru") == "достать 🔴1 — колонка 4, под ней 2 карты"
    assert describe_goal(goal, "en") == "dig out 🔴1 — column 4, 2 cards below it"
    assert describe_goal(goal, "ru", detail=False) == "достать 🔴1"


@pytest.mark.parametrize(
    ("buried", "expected"),
    [(1, "1 карта"), (2, "2 карты"), (5, "5 карт"), (11, "11 карт"), (21, "21 карта")],
)
def test_russian_agrees_the_count_with_the_noun(buried, expected):
    goal = Goal(GoalKind.ACE, suit=0, column=0, buried=buried)
    assert describe_goal(goal, "ru").endswith(expected)


def test_one_dragon_column_is_not_said_in_the_plural():
    one = Goal(GoalKind.DRAGONS, suit=0, columns=(4,))
    two = Goal(GoalKind.DRAGONS, suit=0, columns=(4, 6))
    assert describe_goal(one, "ru") == "убрать 🟩 драконов — они в колонке 5"
    assert describe_goal(two, "ru") == "убрать 🟩 драконов — они в колонках 5, 7"
    assert describe_goal(one, "en") == "clear the 🟩 dragons — they are in column 5"


def test_a_plan_line_names_the_moves_it_covers():
    """The plan and the numbered move list are one document: the span is how a
    player gets from a goal to the moves that reach it."""
    phases = [
        Phase(Goal(GoalKind.DRAGONS, suit=2, columns=(0,)), start=0, end=3),
        Phase(Goal(GoalKind.FINISH), start=4, end=4),
    ]
    assert describe_plan(phases, "ru").splitlines() == [
        "ходы 1–4: убрать ⬜ драконов — они в колонке 1",
        "ход 5: добрать остаток",
    ]
    assert describe_plan(phases, "en").splitlines() == [
        "moves 1–4: clear the ⬜ dragons — they are in column 1",
        "move 5: collect what is left",
    ]


def test_an_aside_is_said_shorter_than_a_goal():
    phase = Phase(
        Goal(GoalKind.DRAGONS, suit=0, columns=(1,)),
        start=0,
        end=1,
        also=(Goal(GoalKind.COLLECT, suit=1, rank=8),),
    )
    line = describe_plan([phase], "ru")
    assert line.endswith("По пути: 🔴 до 8")
    # ...where the same goal as a headline gets the verb
    assert describe_goal(Goal(GoalKind.COLLECT, suit=1, rank=8), "ru") == (
        "увести 🔴 в сбор до 8"
    )


def test_the_cascade_is_one_phrase_rather_than_three_arithmetic_facts():
    """A phase that finishes all three foundations is the endgame, and saying
    ``🟢 до 9, 🔴 до 9, ⚫ до 9`` buries that under a sum."""
    phase = Phase(
        Goal(GoalKind.DRAGONS, suit=0, columns=(1,)),
        start=0,
        end=4,
        also=(Goal(GoalKind.FINISH),),
    )
    assert describe_plan([phase], "ru").endswith("По пути: остальное уходит в сбор")
    assert describe_plan([phase], "en").endswith("On the way: the rest goes up")


@pytest.mark.parametrize("seed", SEEDS)
def test_a_phase_never_spells_the_cascade_out_suit_by_suit(seed):
    for phase in plan(solved(seed).steps):
        finished = [
            goal
            for goal in phase.also
            if goal.kind is GoalKind.COLLECT and goal.rank == 9
        ]
        assert len(finished) < 3


def test_some_real_deal_ends_in_a_cascade():
    """The rule above is only worth having if positions reach it, and they do:
    the last stretch of a line usually finishes every suit at once."""
    assert any(
        Goal(GoalKind.FINISH) in phase.also
        for seed in SEEDS
        for phase in plan(solved(seed).steps)
    )
