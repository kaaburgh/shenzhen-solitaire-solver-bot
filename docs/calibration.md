# Calibrating the screenshot reader

The bot reads a screenshot in two passes. The **layout** pass finds where the
cards are; it is geometric and needs no setup. The **classification** pass
decides which card each one is, and it matches against reference crops of the
card glyphs — which have to be cut out of the game itself.

A bank cut from the iOS app is committed at `templates/default`, built from
both an iPhone and an iPad screenshot, so screenshot reading works out of the
box for that version of the game. You only need what follows if the reader
starts getting things wrong — a different platform, a different skin, a game
update that redraws the cards.

If you do rebuild it, feed in screenshots from **every device you play on**.
Adding an iPad board to a bank built only from the iPhone roughly doubled the
worst-case margin between a card and its runner-up, on the iPhone screenshots
as well as the iPad one: the templates end up describing the glyph rather than
one device's rendering of it.

Past that, more boards from a device already represented buy very little —
going from three boards to five moved the worst margin not at all. Prefer to
keep the extras as held-out fixtures instead; a screenshot the bank was cut
from cannot tell you the bank generalises.

---

## What the layout pass assumes

Worth knowing before changing anything, because these are what the tunables in
`LayoutConfig` are describing:

* The board is one horizontal grid of eight slots. The tableau columns are
  slots 0–7. Along the top, slots 0–2 are the free cells, slot 3 holds the
  three dragon buttons, slot 4 the flower, slots 5–7 the foundations.
* The **dragon buttons anchor that grid**. They are always drawn, even
  darkened when their dragons cannot be collapsed, and they never move — which
  is what lets the free cells be identified on a board whose first columns
  happen to be empty. They are found by their tan colour, not by brightness,
  precisely so a darkened one still counts.
* Cards are drawn with a top-to-bottom brightness gradient. Overlapping cards
  do not separate into distinct shapes when thresholded — a column comes out
  as one tall blob, and the seam between two cards is not dark enough to find.
  What is findable is the gradient resetting: a **step up in row brightness**
  at every card boundary. Those steps sit on a regular lattice, and fitting
  the lattice is what tells a real boundary from a stroke of the large glyph
  on the bottom card.
* The stacking offset is a fixed fraction of the card width — 0.243 to 0.246
  across three board scales on two devices. It is measured per screenshot
  where there is anything stacked to measure it from, and falls back to that
  fraction where there is not.
* A free cell locked by four collapsed dragons shows a **card back**, not a
  dragon face. The back is a green check pattern: about 47% of it reads as
  green, against under 1% of a card face. The back does not say *which*
  dragons went into it — that is deduced, since a colour is collapsed exactly
  when none of its four dragons is anywhere on the board.

## Rebuilding the bank

### 1. Take the screenshots

Two are enough, and they need to show all 31 distinct card faces between them.
A single mid-game board usually shows nearly all of them; check what `build`
reports as missing and add another if needed.

* full screen, not cropped, not scaled — letterboxing is fine, it is ignored;
* PNG or another lossless format. A recompressed JPEG smears the small glyphs;
* the resolution you actually play at.

### 2. Check that the layout pass finds the cards

```sh
python -m shenzhen.vision.calibrate detect --image board1.png --debug boxes.png
```

```
card size: 193x369, stacking offset: 47
free cells: 3 occupied
flower slot: occupied
foundations: 3 occupied
  column 1: 1 cards
  ...
```

Open `boxes.png` and look. Every card should have its own rectangle sitting
over its top-left glyph, free cells should be labelled `cellN` — with
`LOCKED` where a cell holds collapsed dragons — and the foundations `foundN`.

The quickest check that it is all correct: the cards on the board, plus four
for every locked cell, plus one per foundation rank, plus the flower, must
come to exactly 40.

### 3. Write down what the board actually is

For each screenshot, a text file in the notation the bot accepts:

```
free: XG XR DB
flower: 0
foundations: 3 2 0
1: B9
2: R9 G4
3: B1 DB F B3
4:
5: DB G8 R7 G6 B5 R4
6:
7: B2 G9 R8 G7 B6 R5
8: B7 B8 DB R6 G5 B4 R3
```

`G` is the green suit (bamboo), `R` red (coins), `B` black (characters);
`DG`/`DR`/`DB` are the green, red and white dragons and `F` is the flower.
Columns run top to bottom as they appear on screen.

`build` parses this against the full 40-card deck and refuses anything that
could not be a real position, so a slip here is caught rather than baked into
the bank.

### 4. Build

```sh
python -m shenzhen.vision.calibrate build \
    --image board1.png --labels board1.txt \
    --image board2.png --labels board2.txt \
    --out templates/default
```

It stops with the column number if a screenshot and its labels disagree about
how many cards a column holds, so you can go back to `detect --debug` rather
than end up with a bank full of misaligned crops. It also reports any of the
31 faces it never saw.

### 5. Check it against a board it has not seen

```sh
python -m shenzhen.vision.calibrate check --image board3.png
```

Use a screenshot that was *not* in the build set — that is the only run that
tells you anything. It prints the position it read plus anything it is unsure
about.

### 6. Add it to the test suite

Drop the screenshot and its board text into `tests/fixtures/<device>/` as
`shotN.png` and `shotN.txt`. The suite picks up every pair it finds there and
checks the reader still gets it exactly right.

---

## Tuning notes

Two numbers in `src/shenzhen/vision/classify.py` decide when the bot says it
is unsure about a card: `MIN_CONFIDENCE`, how well a crop has to match its best
template, and `MIN_MARGIN`, how far ahead of the runner-up that match has to
be. On the committed fixtures every card is read well clear of both, so there
is room to tighten them if a new bank turns out to be shakier.

The one thing worth preserving: a card the bot is unsure about gets named back
to you, and you can correct the position by typing it out. A card read wrongly
in silence is much worse than a slow confirmation.
