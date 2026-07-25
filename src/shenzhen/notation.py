"""Rendering boards and moves as text, in Russian or English."""

from __future__ import annotations

from typing import Sequence

from .cards import SUIT_LETTERS, card_code, cell_code
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


def render_board(state: State, lang: str = DEFAULT_LANG) -> str:
    """A compact monospace picture of the position.

    Columns read left to right exactly as on screen: the first card printed
    sits at the back of the stack, the last one is the card you can grab.
    """
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
        body = " ".join(f"{card_code(c):>2}" for c in col) if col else "—"
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
