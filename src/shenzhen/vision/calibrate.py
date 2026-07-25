"""Building a template bank from real screenshots.

The bank has to be cut from the actual game, so this is a one-off manual step:

1. Take a screenshot of a *fresh deal* -- it shows all 40 cards at once.
2. Write the deal out in the text notation (eight lines of five cards).
3. Run::

       python -m shenzhen.vision.calibrate build \\
           --image deal.png --labels deal.txt --out templates/default

   Pass ``--image``/``--labels`` more than once to average several deals,
   which is worth doing: it smooths out the anti-aliasing.

``detect`` and ``check`` are the tools for when something looks wrong --
``detect`` draws the geometry the layout pass found, ``check`` prints the
board it read.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

from ..cards import card_code
from ..notation import render_board
from ..textio import parse_board
from .classify import TemplateBank, standardise
from .layout import LayoutConfig, annotate, corner_patch, detect_layout
from .recognize import RecognitionError, recognize

DEFAULT_BANK = Path("templates/default")


def _read_image(path: str) -> np.ndarray:
    image = cv2.imread(path, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"cannot read image: {path}")
    return image


def cmd_detect(args: argparse.Namespace) -> int:
    image = _read_image(args.image)
    layout = detect_layout(image, LayoutConfig())

    print(f"card size: {layout.card_w}x{layout.card_h}, stacking offset: {layout.offset}")
    print(f"free cells: {sum(b is not None for b in layout.free_cells)} occupied")
    print(f"flower slot: {'occupied' if layout.flower else 'empty'}")
    print(f"foundations: {sum(b is not None for b in layout.foundations)} occupied")
    for i, column in enumerate(layout.columns, start=1):
        print(f"  column {i}: {len(column)} cards")
    for warning in layout.warnings:
        print(f"  warning: {warning}")

    if args.debug:
        cv2.imwrite(args.debug, annotate(image, layout))
        print(f"annotated screenshot written to {args.debug}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    if len(args.image) != len(args.labels):
        raise SystemExit("pass one --labels for each --image")

    samples: dict[int, list[np.ndarray]] = {}
    config = LayoutConfig()

    for image_path, labels_path in zip(args.image, args.labels):
        image = _read_image(image_path)
        board = parse_board(Path(labels_path).read_text(encoding="utf-8"), settle=False)
        layout = detect_layout(image, config)

        for index, (boxes, cards) in enumerate(zip(layout.columns, board.columns), start=1):
            if len(boxes) != len(cards):
                raise SystemExit(
                    f"{image_path}: column {index} -- the screenshot has {len(boxes)} cards "
                    f"but the labels list {len(cards)}.  Run the 'detect' command with "
                    f"--debug to see what was found."
                )
            for box, card in zip(boxes, cards):
                patch = corner_patch(image, box, layout.card_w, config)
                samples.setdefault(card, []).append(standardise(patch))

    bank = TemplateBank()
    for card, patches in samples.items():
        averaged = np.mean(np.stack(patches, axis=0), axis=0)
        bank.templates[card] = standardise(averaged)

    bank.save(args.out)
    print(f"wrote {len(bank)} templates to {args.out}")
    if bank.missing:
        print(
            "still missing: "
            + ", ".join(card_code(c) for c in bank.missing)
            + "\n(a fresh deal shows all 40 cards; add another screenshot to fill these in)"
        )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    image = _read_image(args.image)
    bank = TemplateBank.load(args.bank)
    try:
        result = recognize(image, bank)
    except RecognitionError as exc:
        print(f"the position read off the screenshot is not legal: {exc}")
        for read in exc.reads:
            print(f"  {read.where}: {card_code(read.card)} ({read.guess.confidence:.2f})")
        return 1

    print(render_board(result.state, lang="en"))
    print()
    for warning in result.warnings:
        print(f"warning: {warning}")
    for read in result.uncertain:
        print(
            f"unsure at {read.where}: {card_code(read.card)} "
            f"(confidence {read.guess.confidence:.2f}, margin {read.guess.margin:.2f})"
        )
    if result.confident:
        print("all cards read confidently")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m shenzhen.vision.calibrate", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)

    detect = sub.add_parser("detect", help="show the geometry found in a screenshot")
    detect.add_argument("--image", required=True)
    detect.add_argument("--debug", help="write an annotated copy of the screenshot here")
    detect.set_defaults(func=cmd_detect)

    build = sub.add_parser("build", help="cut a template bank from labelled screenshots")
    build.add_argument("--image", action="append", required=True)
    build.add_argument("--labels", action="append", required=True)
    build.add_argument("--out", default=str(DEFAULT_BANK))
    build.set_defaults(func=cmd_build)

    check = sub.add_parser("check", help="read a screenshot with an existing bank")
    check.add_argument("--image", required=True)
    check.add_argument("--bank", default=str(DEFAULT_BANK))
    check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
