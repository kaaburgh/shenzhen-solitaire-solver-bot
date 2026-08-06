"""Scoring the reader against the fixtures, at whatever size they arrive.

The test suite asks whether every fixture still reads back exactly. That is
the right question for CI and the wrong one for changing a threshold: it
answers yes or no, and tuning needs a number that moves. This prints the
numbers, over the whole fixture set, so a change to the matcher can be
compared against the run before it rather than against an impression::

    python -m shenzhen.vision.benchmark                # as Telegram delivers them
    python -m shenzhen.vision.benchmark --width 0      # as they came off the device
    python -m shenzhen.vision.benchmark --verbose      # and which cards went wrong

Four figures come out, and they are not interchangeable:

``cards``
    Reads matching the known board, before the deck gets a say. The matcher's
    own score, and the one that moves when a threshold moves.
``boards``
    Fixtures whose final position is exactly right. What the user experiences,
    and much higher than ``cards`` because the deck recovers misreads.
``flagged``
    Reads the matcher declined to call. Not a failure -- this is the number
    that has to stay above zero for the deck to have anything to work with.
``confident-wrong``
    Reads called confidently and wrong. **The one to watch.** These bypass the
    resolver entirely, so each is a silent mistake in a board the bot presents
    as read. A change that improves ``cards`` while raising this is a
    regression whatever the headline says.

The last one is why this exists as a tool rather than as a note of what was
measured once. Sharpening the matcher tends to move ``cards`` and
``confident-wrong`` the same way, and only one of them is visible in a diff.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..cards import card_code
from ..textio import parse_board
from .classify import TemplateBank
from .recognize import RecognitionError, recognize

FIXTURES = Path("tests/fixtures")
DEFAULT_BANK = Path("templates/default")

#: what Telegram scales a photo's long side down to
TELEGRAM_WIDTH = 1280
TELEGRAM_QUALITY = 80


def as_telegram_photo(image: np.ndarray, width: int, quality: int) -> np.ndarray:
    """What Telegram does to a picture sent as a photo rather than as a file."""
    height = int(round(image.shape[0] * width / image.shape[1]))
    scaled = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", scaled, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:  # pragma: no cover -- OpenCV encodes BGR uint8 or raises
        raise SystemExit("could not re-encode the fixture")
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)


def fixtures(root: Path) -> list[tuple[str, Path, Path]]:
    """Every screenshot with its position written out beside it."""
    found = []
    for image in sorted(root.glob("*/*.png")) + sorted(root.glob("*/*.jpg")):
        labels = image.with_suffix(".txt")
        if labels.exists():
            found.append((f"{image.parent.name}/{image.stem}", image, labels))
    return found


def expected_cards(text: str) -> dict[str, int]:
    """Column slot -> card, keyed the way the reader labels its reads.

    Only the tableau: the reader's labels for it (``"5.3"``) line up with the
    text notation without any guessing, and it is 39 of the 40 cards.
    """
    board = parse_board(text, settle=False)
    return {
        f"{column + 1}.{depth + 1}": card
        for column, cards in enumerate(board.columns)
        for depth, card in enumerate(cards)
    }


@dataclass
class Score:
    cards_right: int = 0
    cards_total: int = 0
    boards_right: int = 0
    boards_total: int = 0
    flagged: int = 0
    confident_wrong: int = 0
    misread: Counter[str] = field(default_factory=Counter)

    def line(self) -> str:
        cards = self.cards_right / max(self.cards_total, 1)
        boards = f"{self.boards_right}/{self.boards_total}"
        return (
            f"cards {cards:.1%} ({self.cards_right}/{self.cards_total})  "
            f"boards {boards}  flagged {self.flagged}  "
            f"confident-wrong {self.confident_wrong}"
        )


def score_one(
    image: np.ndarray, bank: TemplateBank, text: str, score: Score, verbose: bool
) -> str:
    """Read one fixture, fold it into ``score``, and describe the outcome."""
    truth = expected_cards(text)
    try:
        result = recognize(image, bank)
        reads = result.reads
        exact = result.state == parse_board(text)
        outcome = "ok" if exact else "WRONG BOARD"
        if result.resolution is not None:
            outcome += f" (open {len(result.resolution.open)}, deduced {result.deduced})"
    except RecognitionError as exc:
        reads, exact = exc.reads, False
        outcome = f"FAILED: {exc}"

    wrong = []
    for read in reads:
        want = truth.get(read.where)
        if want is None:
            continue  # a cell, the flower or a foundation; not in the labels
        score.cards_total += 1
        if want == read.card:
            score.cards_right += 1
        else:
            wrong.append(f"{read.where}:{card_code(read.card)}!={card_code(want)}")
            score.misread[f"{card_code(read.card)}<-{card_code(want)}"] += 1
        if not read.confident:
            score.flagged += 1
        elif want != read.card:
            score.confident_wrong += 1

    score.boards_total += 1
    score.boards_right += exact
    if wrong and verbose:
        outcome += "\n      " + " ".join(wrong)
    return outcome


def run(
    root: Path,
    bank: TemplateBank,
    width: int,
    quality: int,
    verbose: bool,
) -> Score:
    score = Score()
    for name, image_path, labels_path in fixtures(root):
        image = cv2.imread(str(image_path))
        if image is None:
            raise SystemExit(f"cannot read fixture: {image_path}")
        # A .jpg fixture came back out of Telegram already. Squeezing it again
        # measures a picture nobody will ever send.
        if width and image_path.suffix == ".png":
            image = as_telegram_photo(image, width, quality)
        outcome = score_one(image, bank, labels_path.read_text(encoding="utf-8"), score, verbose)
        print(f"  {name:20} {outcome}")
    return score


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m shenzhen.vision.benchmark",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    parser.add_argument("--bank", type=Path, default=DEFAULT_BANK)
    parser.add_argument(
        "--width",
        type=int,
        default=TELEGRAM_WIDTH,
        help="scale .png fixtures to this width first; 0 reads them untouched",
    )
    parser.add_argument("--quality", type=int, default=TELEGRAM_QUALITY)
    parser.add_argument(
        "--verbose", action="store_true", help="name every card that came out wrong"
    )
    parser.add_argument(
        "--confusions", action="store_true", help="tally which card is read as which"
    )
    args = parser.parse_args(argv)

    if not args.bank.is_dir():
        raise SystemExit(f"no template bank at {args.bank}")
    bank = TemplateBank.load(args.bank)

    sent = "untouched" if not args.width else f"{args.width}px wide, quality {args.quality}"
    print(f"reading {args.fixtures} ({sent}) with {len(bank)} templates")
    score = run(args.fixtures, bank, args.width, args.quality, args.verbose)
    print(score.line())

    if args.confusions and score.misread:
        print("\nread as <- actually:")
        for pair, count in score.misread.most_common():
            print(f"  {pair}  x{count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
