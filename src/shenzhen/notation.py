"""Rendering boards and moves as text, in Russian or English."""

from __future__ import annotations

from collections.abc import Sequence

from .cards import (
    SUIT_LETTERS,
    card_code,
    cell_code,
    dragon_colour,
    is_dragon,
    is_flower,
    is_locked,
    locked_colour,
    make_card,
    rank_of,
    suit_of,
)
from .game import DeckMismatch, Move, State
from .plan import Goal, GoalKind, Phase, phase_of
from .solver import Step

LANGS = ("ru", "en")
DEFAULT_LANG = "ru"

_COLOUR_NAMES = {
    "ru": ("зелёных", "красных", "белых"),
    "en": ("green", "red", "white"),
}

_WORDS = {
    "ru": {
        "cells": "Ячейки",
        "flower": "Цветок",
        "foundations": "Фундамент",
        "column": "кол.",
        "to_free": "в свободную ячейку",
        "from_free": "из ячейки",
        "to_foundation": "в фундамент",
        "collapse": "Схлопнуть {colour} драконов",
        "more_cards": "+{n} снизу",
        "auto": "авто",
        "yes": "да",
        "no": "нет",
        "deck_missing": "не хватает",
        "deck_extra": "лишние",
        "plan_span": "ходы {a}–{b}",
        "plan_one": "ход {a}",
        "plan_also": "По пути",
        "goal_dragons": "убрать {colour} драконов",
        "goal_dragons_in": "убрать {colour} драконов — они в колонках {columns}",
        "goal_dragons_in_one": "убрать {colour} драконов — они в колонке {columns}",
        "goal_ace": "достать {card}",
        "goal_ace_buried": "достать {card} — колонка {column}, под ней {cards}",
        "goal_column": "освободить колонку {column}",
        "goal_column_lands": "освободить колонку {column} — под {card}",
        "goal_collect": "увести {suit} в сбор до {rank}",
        "goal_collect_short": "{suit} до {rank}",
        "goal_finish": "добрать остаток",
        "also_dragons": "{colour} драконы",
        "also_finish": "остальное уходит в сбор",
        "cards_one": "{n} карта",
        "cards_few": "{n} карты",
        "cards_many": "{n} карт",
        "moves_one": "{n} ход",
        "moves_few": "{n} хода",
        "moves_many": "{n} ходов",
    },
    "en": {
        "cells": "Cells",
        "flower": "Flower",
        "foundations": "Foundations",
        "column": "col.",
        "to_free": "to a free cell",
        "from_free": "from a free cell",
        "to_foundation": "to foundation",
        "collapse": "Collapse the {colour} dragons",
        "more_cards": "+{n} below",
        "auto": "auto",
        "yes": "yes",
        "no": "no",
        "deck_missing": "missing",
        "deck_extra": "duplicated",
        "plan_span": "moves {a}–{b}",
        "plan_one": "move {a}",
        "plan_also": "On the way",
        "goal_dragons": "clear the {colour} dragons",
        "goal_dragons_in": "clear the {colour} dragons — they are in columns {columns}",
        "goal_dragons_in_one": "clear the {colour} dragons — they are in column {columns}",
        "goal_ace": "dig out {card}",
        "goal_ace_buried": "dig out {card} — column {column}, {cards} below it",
        "goal_column": "empty column {column}",
        "goal_column_lands": "empty column {column} — for {card}",
        "goal_collect": "run {suit} up to {rank}",
        "goal_collect_short": "{suit} up to {rank}",
        "goal_finish": "collect what is left",
        "also_dragons": "the {colour} dragons",
        "also_finish": "the rest goes up",
        "cards_one": "{n} card",
        "cards_few": "{n} cards",
        "cards_many": "{n} cards",
        "moves_one": "{n} move",
        "moves_few": "{n} moves",
        "moves_many": "{n} moves",
    },
}


def _w(lang: str, key: str) -> str:
    return _WORDS.get(lang, _WORDS[DEFAULT_LANG])[key]


# --- cards as colour -------------------------------------------------------
#
# Checking a card against the screen means matching a colour and a number, and
# `R9` gives you neither: the suit is a letter standing for a colour and the
# reader has to translate it back.  Telegram has no coloured text, so the
# colour has to arrive as a glyph that carries its own -- which leaves the rank
# free to be just the rank, the way it is drawn on the card.
#
# Circles are the numbered suits and squares are the dragons, so shape says
# which kind of card it is before the colour says which one.

#: green (bamboo), red (coins), black (characters)
SUIT_MARKS = ("\U0001f7e2", "\U0001f534", "⚫")
#: the green, red and white dragons.  The white one is drawn in black ink and
#: shares the black suit's index, but it is a white dragon on screen and that
#: is what has to be recognisable here.
DRAGON_MARKS = ("\U0001f7e9", "\U0001f7e5", "⬜")
FLOWER_MARK = "\U0001f338"
LOCKED_MARK = "\U0001f512"
EMPTY_MARK = "·"


def card_mark(card: int) -> str:
    """A card as colour and rank -- ``🔴9`` rather than ``R9``."""
    if is_flower(card):
        return FLOWER_MARK
    if is_dragon(card):
        return DRAGON_MARKS[dragon_colour(card)]
    return SUIT_MARKS[suit_of(card)] + str(rank_of(card))


def cell_mark(value: int | None) -> str:
    """A free cell: empty, locked by collapsed dragons, or holding a card."""
    if value is None:
        return EMPTY_MARK
    if is_locked(value):
        return LOCKED_MARK + DRAGON_MARKS[locked_colour(value)]
    return card_mark(value)


def mark_code_legend() -> str:
    """``🟢 = G, 🔴 = R, …`` -- the two notations against each other.

    Needed only where a message says cards both ways at once: the complaint
    about a reading is in marks, because it sends you to the screen, while the
    reading itself is in the typed notation, because it is meant to be edited
    and sent back.  Built from the tables so the two cannot drift apart.
    """
    pairs = [f"{mark} = {letter}" for mark, letter in zip(SUIT_MARKS, SUIT_LETTERS)]
    pairs += [f"{mark} = D{letter}" for mark, letter in zip(DRAGON_MARKS, SUIT_LETTERS)]
    pairs.append(f"{FLOWER_MARK} = F")
    return ", ".join(pairs)


def _mark_run(cards: Sequence[tuple[int, int]]) -> str:
    """Cards and how many of each, as marks: ``⚫4``, or ``⚫4×2`` when the
    count is worth saying.  Saying ``×1`` of a card that exists once in the
    deck is noise on every line of the commonest case."""
    return ", ".join(
        card_mark(card) + (f"×{count}" if count > 1 else "") for card, count in cards
    )


def describe_deck_problem(deck: DeckMismatch, lang: str = DEFAULT_LANG) -> str:
    """Why a position is not a deck, in colours rather than letter codes.

    This message exists to send someone back to the screen to find the card
    that was read wrong, and on the screen a suit is a colour -- so ``B4``
    makes them translate the one thing they are about to go and match.
    """
    parts = []
    if deck.missing:
        parts.append(f"{_w(lang, 'deck_missing')} {_mark_run(deck.missing)}")
    if deck.extra:
        parts.append(f"{_w(lang, 'deck_extra')} {_mark_run(deck.extra)}")
    return "; ".join(parts)


_SLOTS = {
    "ru": {
        "column": "колонка {n}, {d}-я карта сверху",
        "column_only": "колонка {n}, единственная карта",
        "column_first": "колонка {n}, верхняя карта",
        "column_last": "колонка {n}, нижняя карта",
        "free": "свободная ячейка {n}",
        "foundation": "фундамент, стопка {n}",
        "flower": "слот цветка",
    },
    "en": {
        "column": "column {n}, card {d} from the top",
        "column_only": "column {n}, its only card",
        "column_first": "column {n}, the top card",
        "column_last": "column {n}, the bottom card",
        "free": "free cell {n}",
        "foundation": "foundation {n}",
        "flower": "the flower slot",
    },
}


def describe_slot(where: str, lang: str = DEFAULT_LANG, *, depth_total: int | None = None) -> str:
    """Where on the screen a card sits, in words.

    ``where`` is the internal tag the reader hands out -- ``"5.3"`` for the
    third card of column five, ``"free2"``, ``"foundation1"``, ``"flower"``.
    Said out loud so that a question about it can be answered by looking at
    the game rather than at the bot's own notation.

    ``depth_total`` is how many cards the column holds, and it is worth
    passing: counting five cards down a stack is exactly the fiddly bit, and
    the bottom card of a column -- the one you can pick up -- needs no counting
    at all once it is named as such.
    """
    words = _SLOTS.get(lang, _SLOTS[DEFAULT_LANG])
    if where.startswith("free"):
        return words["free"].format(n=where[4:])
    if where.startswith("foundation"):
        return words["foundation"].format(n=where[10:])
    if where == "flower":
        return words["flower"]
    column, _, depth = where.partition(".")
    if depth_total == 1:
        return words["column_only"].format(n=column)
    if depth == "1":
        return words["column_first"].format(n=column)
    if depth_total is not None and depth == str(depth_total):
        return words["column_last"].format(n=column)
    return words["column"].format(n=column, d=depth)


def render_board(state: State, lang: str = DEFAULT_LANG, *, colour: bool = False) -> str:
    """A compact picture of the position.

    Columns read left to right exactly as on screen: the first card printed
    sits at the back of the stack, the last one is the card you can grab.

    ``colour`` swaps the letter codes for the coloured marks.  That version is
    easier to check against the screen and impossible to keep in a monospace
    block -- emoji are not one character wide -- so it drops the padding
    instead of pretending to line up.
    """
    if colour:
        cells = " ".join(cell_mark(c) for c in state.free)
        foundations = " ".join(
            f"{SUIT_MARKS[i]}{top}" for i, top in enumerate(state.foundations)
        )
    else:
        cells = " ".join(f"{cell_code(c):>2}" for c in state.free)
        foundations = " ".join(
            f"{SUIT_LETTERS[i]}{top}" for i, top in enumerate(state.foundations)
        )
    flower = _w(lang, "yes") if state.flower else _w(lang, "no")

    lines = [
        f"{_w(lang, 'cells')}: {cells}   "
        f"{_w(lang, 'flower')}: {flower}   "
        f"{_w(lang, 'foundations')}: {foundations}",
        "",
    ]
    for i, col in enumerate(state.columns, start=1):
        if not col:
            body = "—"
        elif colour:
            body = " ".join(card_mark(c) for c in col)
        else:
            body = " ".join(f"{card_code(c):>2}" for c in col)
        lines.append(f"{i}: {body}")
    return "\n".join(lines)


def describe_move(move: Move, state: State, lang: str = DEFAULT_LANG) -> str:
    """One human-readable line for a move, resolved against the position it is
    played from (so it can name the actual cards)."""
    col = _w(lang, "column")

    if move.kind == "tt":
        moved = state.columns[move.a][-move.n :]
        head = card_code(moved[0])
        tail = ""
        if move.n > 1:
            tail = " (" + " ".join(card_code(c) for c in moved[1:]) + ")"
        return f"{col} {move.a + 1} → {col} {move.b + 1}: {head}{tail}"

    if move.kind == "tf":
        card = card_code(state.columns[move.a][-1])
        return f"{col} {move.a + 1} → {_w(lang, 'to_free')}: {card}"

    if move.kind == "ft":
        card = card_code(state.free[move.a])
        return f"{_w(lang, 'from_free')} → {col} {move.b + 1}: {card}"

    if move.kind == "tF":
        card = card_code(state.columns[move.a][-1])
        return f"{col} {move.a + 1} → {_w(lang, 'to_foundation')}: {card}"

    if move.kind == "fF":
        card = card_code(state.free[move.a])
        return f"{_w(lang, 'from_free')} → {_w(lang, 'to_foundation')}: {card}"

    if move.kind == "dr":
        colour = _COLOUR_NAMES.get(lang, _COLOUR_NAMES[DEFAULT_LANG])[move.a]
        return _w(lang, "collapse").format(colour=colour)

    return str(move)  # pragma: no cover


# --- the plan --------------------------------------------------------------
#
# The moves say what to do; the plan says what for.  Both are needed, and they
# are deliberately written in different registers so that neither can be
# mistaken for the other: a move line is an instruction resolved against one
# position ("кол. 7 → в свободную ячейку: DR"), a plan line is a span of moves
# and the goal they add up to ("ходы 1–10: убрать 🟩 драконов").

#: marks a goal where it heads a run of moves
GOAL_MARK = "▸"


def _counted(n: int, lang: str, thing: str) -> str:
    """``3 карты``, ``34 хода`` -- Russian agrees the noun with the number, and
    picks a different form for one, for two to four, and for the rest.  English
    only needs the first of those, and says so by giving the same word twice."""
    if n % 100 in (11, 12, 13, 14):
        form = "many"
    elif n % 10 == 1:
        form = "one"
    elif n % 10 in (2, 3, 4):
        form = "few"
    else:
        form = "many"
    return _w(lang, f"{thing}_{form}").format(n=n)


def describe_moves(n: int, lang: str = DEFAULT_LANG) -> str:
    """A number of moves, as a phrase that can be dropped into a sentence."""
    return _counted(n, lang, "moves")


def describe_goal(goal: Goal, lang: str = DEFAULT_LANG, *, detail: bool = True) -> str:
    """One goal in words.

    ``detail`` carries the evidence that makes the goal concrete -- which
    columns the dragons are sitting on, how deep the ace is.  It belongs in the
    plan, where the point is to judge the goal before playing anything, and is
    dropped where the goal is only a heading over the moves that reach it.
    """
    if goal.kind is GoalKind.DRAGONS:
        colour = DRAGON_MARKS[goal.suit]
        if detail and goal.columns:
            key = "goal_dragons_in_one" if len(goal.columns) == 1 else "goal_dragons_in"
            columns = ", ".join(str(c + 1) for c in goal.columns)
            return _w(lang, key).format(colour=colour, columns=columns)
        return _w(lang, "goal_dragons").format(colour=colour)

    if goal.kind is GoalKind.ACE:
        card = card_mark(make_card(goal.suit, 1))
        if detail and goal.column is not None and goal.buried:
            return _w(lang, "goal_ace_buried").format(
                card=card, column=goal.column + 1, cards=_counted(goal.buried, lang, "cards")
            )
        return _w(lang, "goal_ace").format(card=card)

    if goal.kind is GoalKind.COLUMN:
        if detail and goal.lands is not None:
            return _w(lang, "goal_column_lands").format(
                column=goal.column + 1, card=card_mark(goal.lands)
            )
        return _w(lang, "goal_column").format(column=goal.column + 1)

    if goal.kind is GoalKind.COLLECT:
        key = "goal_collect" if detail else "goal_collect_short"
        return _w(lang, key).format(suit=SUIT_MARKS[goal.suit], rank=goal.rank)

    return _w(lang, "goal_finish")


def describe_also(goal: Goal, lang: str = DEFAULT_LANG) -> str:
    """A goal as something a phase passes through rather than aims at."""
    if goal.kind is GoalKind.DRAGONS:
        return _w(lang, "also_dragons").format(colour=DRAGON_MARKS[goal.suit])
    if goal.kind is GoalKind.FINISH:
        return _w(lang, "also_finish")
    return describe_goal(goal, lang, detail=False)


def describe_plan(phases: Sequence[Phase], lang: str = DEFAULT_LANG) -> str:
    """The whole line as the handful of goals it is made of, one per line.

    Each line names the moves it covers, so the plan and the numbered move list
    are one document: "ходы 11–27" is where to look for the moves that get
    there, and which goal the move you are playing right now belongs to.
    """
    lines = []
    for phase in phases:
        if phase.length == 1:
            span = _w(lang, "plan_one").format(a=phase.start + 1)
        else:
            span = _w(lang, "plan_span").format(a=phase.start + 1, b=phase.end + 1)

        text = f"{span}: {describe_goal(phase.goal, lang)}"
        if phase.also:
            extras = ", ".join(describe_also(g, lang) for g in phase.also)
            text += f". {_w(lang, 'plan_also')}: {extras}"
        lines.append(text)
    return "\n".join(lines)


def describe_steps(
    steps: Sequence[Step],
    lang: str = DEFAULT_LANG,
    start: int = 1,
    *,
    phases: Sequence[Phase] = (),
) -> str:
    """Number a run of moves, noting what the game picked up after each one.

    With ``phases``, the goal each run of moves is working towards heads it --
    at every phase boundary and at the top of the batch, so that a batch read
    on its own still says what it is for.
    """
    lines = []
    for offset, step in enumerate(steps):
        index = start - 1 + offset
        phase = phase_of(phases, index)
        if phase is not None and (offset == 0 or phase.start == index):
            lines.append(f"{GOAL_MARK} {describe_goal(phase.goal, lang, detail=False)}")

        text = describe_move(step.move, step.state_before, lang)
        if step.collected:
            picked = ", ".join(card_code(c) for c in step.collected)
            text += f"  [{_w(lang, 'auto')}: {picked}]"
        lines.append(f"{start + offset}. {text}")
    return "\n".join(lines)


def board_to_text(state: State) -> str:
    """The position in the input notation, ready to be pasted back into the
    bot after an edit."""
    lines = [
        "free: " + " ".join(cell_code(c) for c in state.free),
        "flower: " + ("1" if state.flower else "0"),
        "foundations: " + " ".join(str(t) for t in state.foundations),
    ]
    for i, col in enumerate(state.columns, start=1):
        lines.append(f"{i}: " + " ".join(card_code(c) for c in col))
    return "\n".join(lines)
