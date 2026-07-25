"""Reading a board position from text.

The format is meant to be typed on a phone, so the parser is lenient about
case, separators and the order of lines.  A full position looks like::

    free: DG . .
    flower: 1
    foundations: 3 0 1
    1: G9 R8 B7 DG DR
    2: ...
    ...
    8: ...

``free``/``flower``/``foundations`` may be omitted -- a fresh deal is just the
eight column lines, and the numbers may even be given without the ``N:``
prefix as long as there are exactly eight lines.

Cards: ``G1``..``G9`` green (bamboo), ``R1``..``R9`` red (coins),
``B1``..``B9`` black (characters), ``DG``/``DR``/``DB`` dragons, ``F`` flower.
A free cell is ``.`` when empty and ``XG``/``XR``/``XB`` when locked by
collapsed dragons.
"""

from __future__ import annotations

import re

from .cards import FLOWER, parse_card, parse_cell
from .game import NUM_COLUMNS, NUM_FREE_CELLS, InvalidBoard, State, auto_resolve, validate

_FREE_KEYS = ("free", "cells", "ячейки", "яч", "свободные")
_FLOWER_KEYS = ("flower", "rose", "цветок", "роза")
_FOUNDATION_KEYS = ("foundations", "foundation", "found", "фундамент", "фундаменты", "сбор")

_COLUMN_RE = re.compile(r"^\s*([1-8])\s*[:).]\s*(.*)$")
_LABEL_RE = re.compile(r"^\s*([A-Za-zА-Яа-яЁё]+)\s*:\s*(.*)$")


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[\s,;]+", text.strip()) if t]


def parse_board(text: str, *, settle: bool = True) -> State:
    """Parse a position.  Raises :class:`InvalidBoard` with a message meant to
    be shown to the user."""
    columns: dict[int, tuple[int, ...]] = {}
    unlabelled: list[tuple[int, ...]] = []
    free: list[int | None] = [None] * NUM_FREE_CELLS
    foundations = [0, 0, 0]
    flower = False
    flower_given = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        column_match = _COLUMN_RE.match(line)
        if column_match:
            index = int(column_match.group(1)) - 1
            if index in columns:
                raise InvalidBoard(f"column {index + 1} is given twice")
            columns[index] = _parse_cards(column_match.group(2))
            continue

        label_match = _LABEL_RE.match(line)
        if label_match:
            label = label_match.group(1).lower()
            value = label_match.group(2)
            if label in _FREE_KEYS:
                free = _parse_free(value)
            elif label in _FLOWER_KEYS:
                flower = _parse_flag(value)
                flower_given = True
            elif label in _FOUNDATION_KEYS:
                foundations = _parse_foundations(value)
            else:
                raise InvalidBoard(f"unknown line: {raw.strip()!r}")
            continue

        # A bare line of cards -- accepted so a fresh deal can be pasted as
        # eight plain lines.
        unlabelled.append(_parse_cards(line))

    if unlabelled:
        if columns:
            raise InvalidBoard("mix of numbered and unnumbered column lines")
        if len(unlabelled) != NUM_COLUMNS:
            raise InvalidBoard(
                f"expected {NUM_COLUMNS} column lines, got {len(unlabelled)}"
            )
        columns = dict(enumerate(unlabelled))

    if not columns:
        raise InvalidBoard("no columns found")

    if not flower_given:
        # The flower is either on the table or already in its slot, so it can
        # be inferred instead of demanded.
        on_table = any(FLOWER in col for col in columns.values()) or FLOWER in free
        flower = not on_table

    board = State(
        columns=tuple(columns.get(i, ()) for i in range(NUM_COLUMNS)),
        free=tuple(free),
        foundations=(foundations[0], foundations[1], foundations[2]),
        flower=flower,
    )
    validate(board)

    if settle:
        board, _ = auto_resolve(board)
    return board


def _parse_cards(text: str) -> tuple[int, ...]:
    cards = []
    for token in _tokens(text):
        if token in ("-", "—", "."):
            continue
        try:
            cards.append(parse_card(token))
        except ValueError as exc:
            raise InvalidBoard(str(exc)) from exc
    return tuple(cards)


def _parse_free(text: str) -> list[int | None]:
    tokens = _tokens(text)
    if len(tokens) > NUM_FREE_CELLS:
        raise InvalidBoard(f"there are only {NUM_FREE_CELLS} free cells")
    cells: list[int | None] = [None] * NUM_FREE_CELLS
    for i, token in enumerate(tokens):
        try:
            cells[i] = parse_cell(token)
        except ValueError as exc:
            raise InvalidBoard(str(exc)) from exc
    return cells


def _parse_foundations(text: str) -> list[int]:
    tokens = _tokens(text)
    tops = [0, 0, 0]
    if len(tokens) == 3 and all(t.strip(".-").isdigit() or t in ("-", ".") for t in tokens):
        for i, token in enumerate(tokens):
            tops[i] = 0 if token in ("-", ".") else int(token)
    else:
        # Also accept "G3 R0 B1", where rank 0 means the foundation is empty.
        from .cards import SUIT_LETTERS

        for token in tokens:
            if token in ("-", "."):
                continue
            text = token.strip().upper()
            if len(text) != 2 or text[0] not in SUIT_LETTERS or not text[1].isdigit():
                raise InvalidBoard(f"{token!r} is not a foundation")
            tops[SUIT_LETTERS.index(text[0])] = int(text[1])
    for top in tops:
        if not 0 <= top <= 9:
            raise InvalidBoard(f"foundation out of range: {top}")
    return tops


def _parse_flag(text: str) -> bool:
    token = text.strip().lower()
    return token in ("1", "yes", "y", "true", "+", "да", "v", "✓", "х", "x")
