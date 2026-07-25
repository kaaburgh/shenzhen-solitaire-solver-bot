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

The rules engine, the solver and the bot are done and tested. **Screenshot
reading needs a one-off calibration against your own screenshots** before it
will work — see [Reading screenshots](#reading-screenshots) below. Until then
the bot accepts positions typed out in text and says so when you send it a
picture.

## Running it

```sh
cp .env.example .env      # put your @BotFather token in it
docker compose up -d --build
```

Or without compose:

```sh
docker build -t shenzhen-solitaire-solver-bot .
docker run -d --restart unless-stopped \
  -e TELEGRAM_BOT_TOKEN=... \
  -v "$PWD/templates:/app/templates:ro" \
  shenzhen-solitaire-solver-bot
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

Two passes. The layout pass finds the cards geometrically: it thresholds the
image, measures the card width from the connected components, splits the top
row of slots from the tableau, groups the tableau into eight columns and cuts
each column apart along the seams between overlapping cards. All of it is
expressed in fractions of the measured card width, so it does not care what
resolution you play at.

The classification pass reads each card's top-left corner glyph — the part
that stays visible when another card is laid over it. Ink colour picks the
suit outright (green and red print are found by saturation, black by
darkness), and template matching picks the rank within that suit.

**Those templates have to be cut from real screenshots**, which is the
calibration step: [docs/calibration.md](docs/calibration.md). It takes two
screenshots of a fresh deal and about ten minutes.

What the pipeline already handles: cards buried under other cards, the
stacking offset measured rather than assumed, empty slots, and telling a
dragon parked in a free cell apart from four collapsed ones (both look like a
single dragon face — the rest of the deck settles it, since four of that
colour are still visible in the first case and one in the second).

The bot always shows you the position it read and asks before spending time
on it, and it names any card it is unsure about. Send the screenshot **as a
file** rather than as a photo if you can: Telegram recompresses photos and
the glyphs smear.

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

82 tests, about 13 seconds. They cover the rules (runs, dragons, autocollect,
deck validation), the solver — including replaying every move of a returned
solution against a fresh board to check it really wins — the text format, the
conversation flow against stand-ins for Telegram's objects, and the vision
pipeline against a synthetic board renderer (`tests/fake_board.py`) that
reproduces the geometry of the game screen but not its artwork.

That last one is worth being precise about: it proves the pipeline is wired
up correctly, not that the thresholds suit the real game. Only screenshots
can do that.

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
