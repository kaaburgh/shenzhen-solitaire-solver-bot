# Calibrating the screenshot reader

The bot reads a screenshot in two passes. The **layout** pass finds where the
cards are; it is geometric and needs no setup. The **classification** pass
decides which card each one is, and it matches against reference crops of the
real card glyphs — which cannot be shipped here, because they have to be cut
out of the game itself.

Until you build that template bank the bot still works: it accepts positions
typed out in text and refuses screenshots with an explanatory message.

Everything below is a one-off. Once `templates/default` exists, the bot uses
it forever.

---

## 1. Take the screenshots

Take **at least two screenshots of a fresh deal** — the moment after the
cards land, before you touch anything. A fresh deal shows all 40 cards at
once, which is exactly what the bank needs, and two of them let the tool
average out anti-aliasing.

Rules for the screenshots:

* full screen, not cropped, not scaled;
* PNG, or any lossless format — a JPEG at quality 80 smears the small glyphs;
* the resolution you actually play at.

## 2. Check that the layout pass finds the cards

```sh
python -m shenzhen.vision.calibrate detect --image deal1.png --debug boxes.png
```

It prints the card size it measured, the stacking offset, and how many cards
it found in each column. For a fresh deal you want:

```
card size: 122x170, stacking offset: 44
free cells: 0 occupied
flower slot: empty
foundations: 0 occupied
  column 1: 5 cards
  ...
  column 8: 5 cards
```

Open `boxes.png` and look at it. Each card should have its own rectangle,
sitting over the card's top-left corner glyph.

If the counts are wrong, the thresholds in `LayoutConfig`
(`src/shenzhen/vision/layout.py`) need adjusting for your screenshots — most
likely `seam_coverage`, which decides how dark the border between two
overlapping cards has to be before it counts as a seam.

## 3. Write down what the deal actually is

For each screenshot, write a text file with the eight columns, top to bottom
as they appear on screen:

```
1: G6 DB R4 B2 R7
2: R2 DG B4 G9 R3
3: F  G1 DG R6 DB
4: B1 B5 DR DG B7
5: R9 DR G8 B9 DB
6: R5 G3 G2 DR R8
7: DG DB B6 G7 DR
8: G5 G4 B8 R1 B3
```

`G` is the green suit (bamboo), `R` red (coins), `B` black (characters);
`DG`/`DR`/`DB` are the three dragons and `F` is the flower.

Yes, this is tedious. It is 40 cards, twice, and then never again.

## 4. Build the bank

```sh
python -m shenzhen.vision.calibrate build \
    --image deal1.png --labels deal1.txt \
    --image deal2.png --labels deal2.txt \
    --out templates/default
```

The tool refuses to guess: if a column has five boxes but the labels list
four cards, it stops and tells you which column, so you can go back to
`detect --debug` rather than end up with a bank full of misaligned crops.

It also reports any of the 31 distinct card faces it never saw. A single
fresh deal covers all of them, so anything missing means a column got
mis-split.

## 5. Check it against a real position

```sh
python -m shenzhen.vision.calibrate check --image midgame.png
```

Use a mid-game screenshot for this one — something with cards in the free
cells, a few on the foundations, and ideally a collapsed dragon cell. It
prints the position it read, plus anything it is unsure about.

## 6. Ship it

The bank is a directory of small PNGs. Either commit it and let the image
bake it in, or leave it out of git and mount it, which is what
`docker-compose.yml` does:

```yaml
volumes:
  - ./templates:/app/templates:ro
```

`.gitignore` excludes `templates/*/` by default, on the assumption that you
would rather not commit crops of the game's artwork. Delete that line if you
disagree.

---

## Tuning notes

Two numbers in `src/shenzhen/vision/classify.py` decide when the bot says
"I am not sure about this card":

* `MIN_CONFIDENCE` — how well the crop has to match its best template;
* `MIN_MARGIN` — how far ahead of the runner-up that match has to be.

The values there were measured on synthetic boards, where correct matches
never scored below 0.85 and beat the runner-up by more than 0.039 in 99% of
cases. Real card art is more distinctive than the stand-ins, so they should
be on the safe side — but after step 5, check what `check` reports on a few
positions and tighten them if it never flags anything, or loosen them if it
flags everything.

The one thing worth keeping: a card the bot is unsure about gets named back
to you, and you can correct the position by typing it out. A wrong card read
silently is much worse than a slow confirmation.
