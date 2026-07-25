"""Card encoding for Shenzhen Solitaire (the minigame from SHENZHEN I/O).

The deck has 40 cards:

* 27 suit cards -- three suits (green/bamboo, red/coins, black/characters),
  ranks 1..9;
* 12 dragons -- four each of the green, red and white dragon.  The white
  dragon is drawn in black ink, so internally it shares the "black" colour
  index with the character suit;
* 1 flower (rose).

Cards are encoded as small integers so that game states are cheap to hash and
compare inside the solver:

======  ===========================================
0..26   suit cards, ``suit * 9 + (rank - 1)``
27..29  dragons, ``DRAGON_BASE + colour``
30      the flower
======  ===========================================
"""

from __future__ import annotations

# --- colours / suits -------------------------------------------------------

GREEN = 0  # bamboo
RED = 1  # coins
BLACK = 2  # characters (the white dragon is drawn in black ink)

SUITS = (GREEN, RED, BLACK)
SUIT_LETTERS = ("G", "R", "B")

RANK_MIN = 1
RANK_MAX = 9

# --- card ids --------------------------------------------------------------

DRAGON_BASE = 27
FLOWER = 30
NUM_CARD_IDS = 31


def make_card(suit: int, rank: int) -> int:
    if suit not in SUITS:
        raise ValueError(f"bad suit: {suit!r}")
    if not RANK_MIN <= rank <= RANK_MAX:
        raise ValueError(f"bad rank: {rank!r}")
    return suit * 9 + (rank - 1)


def make_dragon(colour: int) -> int:
    if colour not in SUITS:
        raise ValueError(f"bad dragon colour: {colour!r}")
    return DRAGON_BASE + colour


def is_suit_card(card: int) -> bool:
    return 0 <= card < DRAGON_BASE


def is_dragon(card: int) -> bool:
    return DRAGON_BASE <= card < FLOWER


def is_flower(card: int) -> bool:
    return card == FLOWER


def suit_of(card: int) -> int:
    return card // 9


def rank_of(card: int) -> int:
    return card % 9 + 1


def dragon_colour(card: int) -> int:
    return card - DRAGON_BASE


def colour_of(card: int) -> int:
    """Ink colour of a card -- the only property the screenshot parser can read
    reliably before it has matched the glyph itself."""
    if is_suit_card(card):
        return suit_of(card)
    if is_dragon(card):
        return dragon_colour(card)
    return RED  # the flower is drawn in red and green; treat it as red


FULL_DECK: tuple[int, ...] = tuple(
    [make_card(s, r) for s in SUITS for r in range(RANK_MIN, RANK_MAX + 1)]
    + [make_dragon(c) for c in SUITS for _ in range(4)]
    + [FLOWER]
)
assert len(FULL_DECK) == 40

# --- locked free cells -----------------------------------------------------
#
# A free cell that holds four collapsed dragons is stored as a negative
# integer so that it can live in the same tuple as ordinary cards.

def locked_cell(colour: int) -> int:
    if colour not in SUITS:
        raise ValueError(f"bad dragon colour: {colour!r}")
    return -(colour + 1)


def is_locked(value: int | None) -> bool:
    return value is not None and value < 0


def locked_colour(value: int) -> int:
    return -value - 1


# --- text notation ---------------------------------------------------------
#
#   G1..G9  green (bamboo)      DG  green dragon
#   R1..R9  red (coins)         DR  red dragon
#   B1..B9  black (characters)  DB  white dragon (black ink)
#   F       flower              XG/XR/XB  free cell locked by collapsed dragons

def card_code(card: int) -> str:
    if is_flower(card):
        return "F"
    if is_dragon(card):
        return "D" + SUIT_LETTERS[dragon_colour(card)]
    return SUIT_LETTERS[suit_of(card)] + str(rank_of(card))


def cell_code(value: int | None) -> str:
    if value is None:
        return "."
    if is_locked(value):
        return "X" + SUIT_LETTERS[locked_colour(value)]
    return card_code(value)


_ALIASES = {
    # tolerate the common ways of typing the suits by hand
    "З": "G", "Б": "G", "К": "R", "М": "R", "Ч": "B", "И": "B",
    "D": "D", "Д": "D", "Ц": "F", "Ф": "F", "X": "X", "Х": "X",
}


def _normalise(token: str) -> str:
    return "".join(_ALIASES.get(ch, ch) for ch in token.strip().upper())


def parse_card(token: str) -> int:
    """Parse a single card in text notation.  Raises ``ValueError``."""
    text = _normalise(token)
    if not text:
        raise ValueError("empty card")
    if text in ("F", "FLOWER", "ROSE"):
        return FLOWER
    if text[0] == "D":
        if len(text) != 2 or text[1] not in SUIT_LETTERS:
            raise ValueError(f"unknown dragon: {token!r}")
        return make_dragon(SUIT_LETTERS.index(text[1]))
    if text[0] in SUIT_LETTERS:
        if len(text) != 2 or not text[1].isdigit():
            raise ValueError(f"unknown card: {token!r}")
        rank = int(text[1])
        if not RANK_MIN <= rank <= RANK_MAX:
            raise ValueError(f"rank out of range: {token!r}")
        return make_card(SUIT_LETTERS.index(text[0]), rank)
    raise ValueError(f"unknown card: {token!r}")


def parse_cell(token: str) -> int | None:
    """Parse a free-cell slot: ``.`` (empty), ``XG``/``XR``/``XB`` (locked) or
    an ordinary card."""
    text = _normalise(token)
    if text in (".", "-", "_", ""):
        return None
    if text[0] == "X":
        if len(text) != 2 or text[1] not in SUIT_LETTERS:
            raise ValueError(f"unknown locked cell: {token!r}")
        return locked_cell(SUIT_LETTERS.index(text[1]))
    return parse_card(text)
