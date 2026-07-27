# shenzhen-solitaire-solver-bot

Telegram bot for the solitaire minigame in SHENZHEN I/O. Send it the board;
it tells you whether the deal can still be won and, if so, the next five
moves.

```
Вот что я вижу:
Ячейки:  DG  .  .   Цветок: да   Фундамент: G3 R1 B2

1: B6 G5 R4
2: R7 DB
3: —
4: G8 R6 B5 DG
...

✅ Решение есть — 24 хода до победы.
1. кол. 7 → в свободную ячейку: DR
2. Схлопнуть зелёных драконов
3. кол. 6 → кол. 4: G7  [авто: R2]
4. кол. 2 → в фундамент: B3
5. кол. 1 → кол. 5: R4 (B3 G2)
```

Answers in Russian or English (`/lang`).

## Status

Working end to end, screenshots included. The card templates committed at
`templates/default` were cut from the iOS app, and on nine real screenshots —
iPhone and iPad, four board scales, covering a fresh deal, an early mid-game,
collapsed dragons, a played flower, full free cells, a dead position, an
endgame, and a JPEG compressed enough to wash out its ink colours — all 323
cards are read correctly and confidently.

The iPad was read by the geometry pass with no changes at all, which is what
the resolution-independence was for: 4:3 instead of 21:9, a different window,
cards half again as wide.

The bank is built from three of those boards, so the other six are held-out:
they say the templates generalise rather than just fitting what they were cut
from. Feeding all nine in raises the worst confidence but leaves the worst
margin about where it is, which is not worth giving up the held-out evidence
for.

The JPEG one is worth a mention: sent as a compressed photo rather than a
file, it showed real green ink at saturation 40-58 -- a clean PNG reads 90+ --
because JPEG's chroma subsampling throws away colour detail far more readily
than brightness detail, and a small glyph doesn't have much colour detail to
spare. Lowering the threshold to catch it exposed a second, smaller issue: a
card whose detected box landed a pixel high caught a sliver of green felt at
the crop's edge, which at the new threshold was exactly as "coloured" as
genuine washed-out ink. Both are fixed in `ink_colour`
(`src/shenzhen/vision/classify.py`) and pinned by dedicated tests.

## Running it

Every [release](#releases) publishes an image to `ghcr.io`, so a deploy is a
pull, not a build:

```sh
cp .env.example .env      # put your @BotFather token in it

# the package is private, same as the repo -- do this once per server
echo "$GHCR_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin

docker compose pull
docker compose up -d
```

`GHCR_TOKEN` is a [personal access token][pat] with `read:packages`. To
update later, the whole thing is `docker compose pull && docker compose up -d`
again.

[pat]: https://github.com/settings/tokens?type=beta

Building from source still works, for local development or if you'd rather
not depend on the registry:

```sh
docker compose up -d --build
```

The bot uses long polling, so it needs no inbound port, no reverse proxy and
no certificate — only outbound HTTPS. It runs as an unprivileged user and
keeps nothing on disk.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | — | required |
| `SHENZHEN_TEMPLATES` | `templates/default` | card template bank |
| `SHENZHEN_MAX_NODES` | `400000` | search budget, in positions |
| `SHENZHEN_TIME_LIMIT` | `20` | search budget, in seconds |
| `SHENZHEN_WORKERS` | `2` | how many positions may be solved at once |
| `LOG_LEVEL` | `INFO` | |

Each worker pins a CPU core while it searches, which is why the default is 2
rather than "as many as you have".

## Releases

Cutting one is what ships an image — nothing publishes on an ordinary merge
to `main`.

1. GitHub → **Releases** → **Draft a new release**.
2. Pick a tag (create one), e.g. `v1.1.0`. Semantic versioning isn't enforced,
   but is the natural fit: bump the middle number for a feature (the iPad
   support, say), the last for a fix (the JPEG colour bug), the first only for
   something that breaks how the bot is run or configured.
3. Click **Generate release notes** — pulls in every merged PR since the last
   tag, titled and linked. Edit if you want, or don't.
4. **Publish release.**

That triggers `.github/workflows/release.yml`: it rebuilds the image (same
Dockerfile the CI `docker` job already validated on the PR), runs the same
two smoke checks again — solves a deal, loads the card template bank — and
only then pushes `ghcr.io/kaaburgh/shenzhen-solitaire-solver-bot` tagged with
the release tag and with `latest`. Watch it under the repo's **Actions** tab;
takes about a minute.

A tag alone (`git tag v1.1.0 && git push origin v1.1.0`) does not trigger
this — it has to go through **Publish release**, since that is the point
where "this is a real release" gets decided.

## Typing a position out

```
free: DG . XR
foundations: 3 0 1
1: G9 R8 B7 DG DR
2: B6 G5 R4 DB F
...
8: B3 G1 R2 DR DB
```

* `G1`…`G9` green (bamboo), `R1`…`R9` red (coins), `B1`…`B9` black
  (characters), `DG`/`DR`/`DB` dragons, `F` flower.
* Within a column, cards go **top to bottom as they look on screen** — the
  last one is the card you can pick up.
* A free cell is `.` when empty and `XG`/`XR`/`XB` when locked by four
  collapsed dragons.
* `free` and `foundations` can be left out when the cells are empty and
  nothing has been collected; then eight lines of cards are enough.
* Whether the flower has been played is inferred from whether it is on the
  table, so there is no need to say.

Anything that could not have come from a real deck is rejected with the
specific problem — `missing R4x1; duplicated DGx1` — rather than a shrug.

## Reading screenshots

Two passes, both resolution-independent — every length is a fraction of the
card width the image itself is measured for, so letterboxing and whatever
scale the game picks are both irrelevant.

The **layout** pass works out the geometry. The board is one grid of eight
slots: the tableau columns are slots 0–7, and along the top slots 0–2 are the
free cells, slot 3 the dragon buttons, slot 4 the flower, slots 5–7 the
foundations. The grid is anchored on the **dragon buttons**, found by their
tan colour rather than by brightness so that a darkened one still counts —
they are always drawn and never move, which is what makes the free cells
identifiable on a board whose first columns happen to be empty.

Splitting a column into cards is the part worth explaining. Overlapping cards
do not come apart when the image is thresholded — a column is one tall blob,
and the seam between two cards is not dark enough to find. What is findable is
that each card is drawn with a top-to-bottom gradient, so every boundary is a
step up in row brightness. Those steps fall on a regular lattice, and fitting
it separates real boundaries from the strokes of the big glyph on the bottom
card. The stacking offset comes out of the same fit, and falls back to the
fraction of the card width the game is known to use when a board has nothing
stacked on it to measure.

The **classification** pass reads each card's top-left glyph — the part that
stays visible under another card. Ink colour settles the suit on its own
(green and red print are found by saturation, black by darkness) and template
matching picks the rank within it.

A free cell holding four collapsed dragons shows a patterned back rather than
a dragon face; it is told apart by that pattern being about half green against
a card face's ~1%. The back does not say which dragons went into it, so that
is deduced — a colour is collapsed exactly when none of its four dragons is
left anywhere on the board.

The templates are cut from real screenshots, and rebuilding them is
[docs/calibration.md](docs/calibration.md) — worth reading if the reader ever
starts getting cards wrong.

The bot always shows you the position it read and asks before spending time on
it, and it names any card it is unsure about.

Send the screenshot **as a file**, not as a photo. This is not a nicety —
Telegram scales a photo's long side down to about 1280px, and how much of the
board survives that is a cliff, not a slope:

| card width in the picture | cards read right | whole boards right |
| --- | --- | --- |
| ≥150px (a screenshot sent as a file) | 100% | 100% |
| 120–149px | 99.4% | 81–92% |
| 105–119px | 98.5% | 61% |
| 90–104px (a phone screenshot sent as a photo) | 89.9% | 18% |
| 75–89px | 77.8% | 0% |

Measured by degrading the fixtures and reading them back against their known
positions. Below roughly 150px the rank glyph is a handful of pixels across
and a 3 stops being distinguishable from an 8 — no threshold fixes that, the
detail is gone. The bot measures the card width it got and says so, rather
than reporting the resulting impossible board as deck arithmetic.

## Rules

Implemented as the game plays them:

* 8 columns, 3 free cells, 3 foundations, 1 flower slot;
* a run moves together when each card is one rank lower than the one above it
  and a different suit; dragons and the flower move alone;
* an empty column takes anything;
* four dragons of one colour collapse into a free cell when all four are
  exposed and a cell is free (or already holds one of them);
* the flower leaves on its own the moment it is exposed;
* a card is collected automatically once it cannot possibly be needed to hold
  a lower card — aces and twos always, anything higher once every foundation
  has reached one below it. You can also drag a card up by hand earlier, and
  the solver will if it helps.

Automatic collections never appear as moves — they are not things you do. The
move list mentions them in passing (`[авто: G3, R4]`) so the board still
matches after you play a move.

## The solver

Weighted A* over canonical positions: columns and free cells are
interchangeable, so positions that differ only by which column a stack sits in
are treated as one. The heuristic counts the cards still to collect and
penalises burying the cards the foundations want next.

Three outcomes, kept distinct because the difference matters:

* **winnable** — a full line to the win was found; you get the first five
  moves and a button for the next five;
* **not winnable** — the search exhausted every reachable position. This is a
  proof, not a guess;
* **no answer** — the budget ran out first. The bot says so rather than
  claiming the deal is dead.

On 200 random deals: 198 solved, 1 proven unsolvable, 1 over budget; median
under half a second, worst case about two seconds.

## Development

```sh
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

CI runs the suite on Python 3.11, 3.12 and 3.13, lints with ruff, and builds
the image — then checks the built image can actually solve a deal and load its
template bank, rather than only that the build exited zero.

87 tests, about 12 seconds. They cover the rules (runs, dragons, autocollect,
deck validation), the solver — including replaying every move of a returned
solution against a fresh board to check it really wins — the text format, and
the conversation flow against stand-ins for Telegram's objects.

The vision pipeline is tested on two levels. `tests/fixtures/` holds real
screenshots with the position written out beside them, and reading each one
back exactly is what says the thresholds suit the real artwork. On top of
that, `tests/fake_board.py` renders boards with the structure of the game
screen but stand-in marks, which covers states no screenshot happens to show —
an empty tableau, every cell locked, a board whose first columns are empty.
Those synthetic tests deliberately stop at the geometry: matching stand-in
marks would measure the stand-in, not the pipeline.

```
src/shenzhen/
  cards.py      card encoding and text notation
  game.py       rules, moves, the game's automatic behaviour
  solver.py     the search
  notation.py   rendering boards and moves, ru/en
  textio.py     parsing a typed position
  vision/       layout.py, classify.py, recognize.py, calibrate.py
  bot/          main.py, handlers.py, i18n.py, storage.py
```
