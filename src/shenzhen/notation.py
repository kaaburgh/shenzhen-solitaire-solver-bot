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
    rank_of,
    suit_of,
)
from .game import Move, State
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


def describe_steps(steps: Sequence[Step], lang: str = DEFAULT_LANG, start: int = 1) -> str:
    """Number a run of moves, noting what the game picked up after each one."""
    lines = []
    for offset, step in enumerate(steps):
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
