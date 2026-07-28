"""Regression coverage for glyph-less layout proposals."""

from __future__ import annotations

import importlib

import numpy as np

from shenzhen.cards import GREEN, make_dragon
from shenzhen.game import State
from shenzhen.vision.classify import Guess
from shenzhen.vision.layout import BoardLayout, Box
from shenzhen.vision.recognize import recognize

RECOGNIZE = importlib.import_module("shenzhen.vision.recognize")


def test_glyphless_false_split_is_not_reported_as_an_extra_card(monkeypatch):
    """A glyph-less proposed split must disappear instead of becoming a warning."""
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
