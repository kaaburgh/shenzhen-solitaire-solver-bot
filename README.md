# shenzhen-solitaire-solver-bot

Telegram bot for the solitaire minigame in SHENZHEN I/O. Send it the board;
it tells you whether the deal can still be won and, if so, what the plan is
and the next five moves.

```
✅ Решение есть — 34 хода до победы.

Замысел — за чем идти и в каком порядке:
ходы 1–10: убрать 🟩 драконов — они в колонках 1, 6, 7. По пути: 🟢 до 2, 🔴 до 3
ходы 11–26: увести 🔴 в сбор до 8. По пути: ⚫ до 6
ход 27: убрать 🟥 драконов — они в колонках 1, 5, 8
ходы 28–32: освободить колонку 7. По пути: ⚫ до 9
ходы 33–34: убрать ⬜ драконов — они в колонках 4, 5, 6. По пути: 🟢 до 9

Ближайшие ходы:
▸ убрать 🟩 драконов
1. кол. 3 → кол. 8: R6
2. кол. 7 → в свободную ячейку: DR
3. кол. 7 → кол. 4: B8  [авто: B2]
4. кол. 2 → в фундамент: B3
5. кол. 1 → в свободную ячейку: DG
```

Answers in Russian or English (`/lang`).

## Status

Working end to end, screenshots included. The card templates committed at
`templates/default` were cut from the iOS app, and on eleven real screenshots
— iPhone and iPad, four board scales, covering a fresh deal, an early
mid-game, collapsed dragons, a played flower, full free cells, a dead
position, an endgame, a column deep enough to run off the bottom of the
screen, a JPEG compressed enough to wash out its ink colours, and two that
came back out of Telegram as photos — all 403 cards are read correctly. Five
of them, all on the two Telegram ones, are read without confidence and settled
by the deck; the other 398 the matcher calls outright.

The iPad was read by the geometry pass with no changes at all, which is what
the resolution-independence was for: 4:3 instead of 21:9, a different window,
cards half again as wide.

The bank is built from three of those boards, so the other eight are held-out:
they say the templates generalise rather than just fitting what they were cut
from. Feeding the rest in raises the worst confidence but leaves the worst
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

The second Telegram one cost a column. Its seventh column is eight cards deep,
and the bottom card of a column is the one card showing its whole face — so
the rank glyph in its corner is followed by blank card rather than by the next
card, and the brightness coming back after that ink lands within a third of a
stacking offset of where a ninth card would have begun. Averaged across the
column that ink moved the row mean by 9 grey levels where a real seam moves it
by 20, against a threshold sitting at 8, so which way it went came down to
which way the JPEG rounded. It went the wrong way: one B4 was cut into a B2
and a B5, and the deck was handed a board that cannot exist.

What tells a seam from a glyph is not how dark it is but how much of the card
it crosses: a seam is one edge right across the width, and no glyph is. So the
splitter now reads the step as the median across the column's width instead of
the mean, and anything narrower than half the column cannot move it. See
`_brightness_steps` (`src/shenzhen/vision/layout.py`).

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
again — the card templates ship inside the image, so that one command moves
the code and the templates together and they cannot drift apart.

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
| `SHENZHEN_COVERAGE` | — | measure the running bot into this directory |
| `LOG_LEVEL` | `INFO` | |

Each worker pins a CPU core while it searches, which is why the default is 2
rather than "as many as you have".

`SHENZHEN_COVERAGE` is the odd one out: setting it restarts the bot under
`coverage`, so that after a week of ordinary use you can look at what real
conversations never reached and delete it. It costs the solver about a fifth
of its search budget while it is on. See [docs/coverage.md](docs/coverage.md).

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
the release tag and with `latest`. What gets pushed is the image the checks
ran against, tagged where it stands rather than built a second time, so there
is no gap between what was tested and what was published. Watch it under the
repo's **Actions** tab; takes about a minute.

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
specific problem — `не хватает 🔴4; лишние 🟩` — rather than a shrug. The
cards are named the way they are drawn rather than the way they are typed,
because that message sends you back to the screen to find the one that is
wrong, and on the screen a suit is a colour and not a letter. Where the
complaint sits next to a reading in the typed notation, the two are lined up
for you: `Обозначения: 🟢 = G, 🔴 = R, ⚫ = B, …`.

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

A screenshot is of the whole screen, so the phone's own furniture is in it,
and on an iPhone the pale home indicator lies right across the bottom — over
whichever column runs that far down. It is bright and unsaturated, so it looks
exactly like a card face, and because it *touches* the column it is not a
stray blob that could be ignored: the two fuse into one shape three cards
wide, which is thrown out as not card-shaped and takes the column with it.
That is a whole column missing from the reading, and unlike a card read wrong
it is not something the deck can repair. What tells the bar from the board is
length: nothing the game draws is wider than one card, and the slots are set a
quarter of a card apart, so a horizontal run longer than a card belongs to
something else whatever it is. Those runs are cut out of the mask before
anything is measured, and the pieces of column either side of a cut are joined
back up — where the gap between two blobs is exactly what was cut out of it,
the two are one column.

Splitting a column into cards is the part worth explaining. Overlapping cards
do not come apart when the image is thresholded — a column is one tall blob,
and the seam between two cards is not dark enough to find. What is findable is
that each card is drawn with a top-to-bottom gradient, so every boundary is a
step up in brightness — and one that runs right across the width of the card,
which the edges of the glyphs printed on it do not. That is what separates
them: the step is read as the median across the column's width, and nothing
narrower than half the column can move a median, however dark it is. The steps
that survive fall on a regular lattice, and fitting it is what says where the
column stops. The stacking offset comes out of the same fit, and
falls back to the fraction of the card width the game is known to use when a
board has nothing stacked on it to measure.

The **classification** pass reads each card's top-left glyph — the part that
stays visible under another card. Ink colour settles the suit on its own
(green and red print are found by saturation, black by darkness) and template
matching picks the rank within it.

The **deck** settles what is left. Every card in a Shenzhen deck is unique —
each of the 27 suit cards exists exactly once, and a board holds all forty —
so a card the matcher cannot call is usually not an open question at all: it
is whatever the other thirty-nine leave over. Each unsure read becomes a
variable with a shortlist of candidates, every combination is checked against
the deck, and what survives is the set of boards the picture could be showing.
A card they all agree on is filled in silently; only a card they disagree
about is put to you, one tap to answer.

That is most of the work. Squeeze the fixtures until cards start coming out
shaky and every one of them still reads back exactly right with **nothing
asked** — the elimination covers all of it. Push further and the questions
start, but they stay cheap, because each answer is fed back through the deck
before the next question is chosen: pinning one of two slots that might be the
3 or the 8 pins the other one too. Worst case across the degraded fixtures was
two questions.

Being the only legal reading is not by itself proof, though — a shortlist that
cannot see the true card can have exactly one survivor and still be wrong. So
the board never comes from the first shortlist that works: whichever level
finds an answer, the answer is taken from one level wider, where the truth is
in reach and a disagreement turns into a question rather than a silent
mistake.

When even that cannot close — too many cards unreadable for the elimination to
bite — there is no board to show, but there is still a reading, and most of
its forty cards are right. That comes back written out in the text notation
for you to correct the handful that are not, which beats both retyping the
position and being sent away for a better screenshot.

A free cell holding four collapsed dragons shows a patterned back rather than
a dragon face; it is told apart by that pattern being about half green against
a card face's ~1%. The back does not say which dragons went into it, so that
is deduced — a colour is collapsed exactly when none of its four dragons is
left anywhere on the board.

The templates are cut from real screenshots, and rebuilding them is
[docs/calibration.md](docs/calibration.md) — worth reading if the reader ever
starts getting cards wrong.

### Screenshots sent as photos

Telegram scales a photo's long side down to about 1280px, which puts the cards
around 97px wide, and how much of the board survives that used to be a cliff
rather than a slope:

| card width in the picture | cards read right |
| --- | --- |
| ≥150px (a screenshot sent as a file) | 100% |
| 120–149px | 99.4% |
| 105–119px | 98.5% |
| 90–104px (a phone screenshot sent as a photo) | 89.9% |
| 75–89px | 77.8% |

Measured by degrading the fixtures and reading them back against their known
positions, per card and before the deck gets a say. At the bottom of that
table a rank glyph is ten pixels tall and a 3 stops being distinguishable from
an 8 — and yet most of the loss was not the missing detail. It was
registration. Every crop is cut on integer pixels off a lattice fitted to the
columns, so at 97px the glyph lands up to half a pixel away from where the
templates expect it, which on a ten-pixel glyph is a lot of the glyph.

So the reader enlarges a small picture by a whole-number factor before looking
at it, which divides that error by the factor and costs the picture nothing.
It adds no information — a blurred 3 stays a blurred 3 — but across the
fixtures as Telegram delivers them it takes cards read right from 89.0% to
99.1%, and boards read exactly from three in ten to ten in ten, with nothing
left to ask about. The three cards still misread are all settled by the deck.

Bilinear and whole-number, both measured rather than assumed: bicubic's
overshoot sharpens JPEG ringing into strokes that are not there, nearest
preserves the very error the enlargement is meant to remove, and a fractional
resize beats against the original grid so which cards it smears depends on
where they happen to sit.

Sending the screenshot **as a file** still avoids all of this — it arrives
untouched. It is no longer a requirement, and it is barely ever volunteered. A
picture that came in small and read correctly is a picture that read
correctly, so the advice needs a reading that actually failed to add up — and
that alone is not enough either, because at 97px a failed reading has almost
certainly failed at something else:

| card width in the picture | readings the deck threw out |
| --- | --- |
| ≥100px | 1 in 120 |
| 80–99px (a phone screenshot sent as a photo) | 1 in 17 |
| 70–79px | 1 in 7 |
| <70px | 3 in 5 |

Measured on the same fixtures, but with the whole pipeline in place —
enlargement, and the deck settling the shaky reads — which is what the person
on the other end actually gets. So the advice waits for cards under **75px**,
which nothing arriving as a Telegram photo comes near: a picture that small
has been cropped or scaled by hand, and then the size really is the thing to
fix. Above it the reply is the draft to correct and nothing else. For a long
time the picture got the blame for a column the phone had drawn its home
indicator across, which is exactly the round trip this avoids.

## Confirming what was read

Usually nothing is confirmed at all, because the deck has already done it.
`validate` demands exactly the forty cards of a real deck, no more and no
fewer, and a reading only becomes a board by passing it. That one fact rules
out almost everything a confirmation step used to exist for: a screenshot of
something else, a card dropped because its glyph would not classify, a grid
whose columns came out shuffled — each of those loses or duplicates cards, and
none survives the count. Reading forty cards back to someone who could have
been told the answer is asking them to redo work that was already done
properly.

What the deck cannot see is a reading where two mistakes cancel, and the
matcher's own scores are what point at those. Every card arrives with two
independent verdicts on it: the template match, and the deck arithmetic that
had to fit it alongside thirty-nine others. Where both say the same thing
there is nothing left to ask, so the bot goes straight to the answer, with the
whole board and a way to correct it one button away on the reply. Where the
deck *overruled* the matcher, one slot goes up — the single place the two ways
of reading the board disagreed.

That question comes with a picture of the card. The screenshots that produce
questions are exactly the ones that came in small, so "column 6, the bottom
card — is that the white dragon?" is otherwise answerable only by going back
to the game or squinting at a thumbnail. The slot is cut out of the picture
that was sent, with a card width of board around it for context, outlined in a
colour the game does not use, and enlarged until it is comfortably readable.
Across the ten screenshot fixtures eight go through without a word and two ask
about one card each.

One failure the deck genuinely cannot catch is geometric. When the layout pass
cannot find the dragon buttons it falls back to guessing which grid slot the
leftmost column occupies, and a wrong guess slides every column sideways: all
forty cards are present, the deck is content, and every column is mislabelled.
That is the one case that still puts up a card the matcher was *sure* of,
named by the column it sits in, because saying the column out loud is what
catches the shift.

Cards are shown as colour and rank — `🔴9` rather than `R9`. The suit *is* a
colour on the card, and a letter standing for it is one translation step in
the middle of a job that is nothing but matching. Telegram has no coloured
text, so the colour arrives as a glyph that carries its own: circles are the
numbered suits, squares are the dragons, so shape says which kind of card
before colour says which one. Slots are named where they sit rather than in
the bot's own grid notation — "column 4, the bottom card" is something you can
find without counting. Typing a position out still gets it echoed back in
full: that one is the user's own text rather than a reading, and a typo in it
is exactly what the echo is for.

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

## The plan

Thirty-four numbered moves answer a question the player usually already knows
the answer to. Which moves are *legal* is on the screen; what is not on the
screen is which way to set off — dig the ace out from under four cards, go and
collapse the green dragons, or run the blacks up to 5 first to unload the table
so a column can be emptied at all. Get that choice wrong and the next ten moves
are wasted. So the verdict leads with the goals — the block at the top of this
file is one in full, on deal 0.

Each line names the moves it covers, so the plan and the numbered list are one
document, and every batch of moves carries its goal at the head — the batches
are read one at a time, minutes apart, and a batch that only numbers its moves
has lost the reason for them.

The line is cut at the things a later move cannot take back: a dragon colour
collapsed, a suit's 1 out from under the pile, more columns empty than at any
earlier point. Those cannot be reordered, so the moves between two of them are
exactly what the second one cost. Foundations creeping up and columns that
empty and refill — a median of 15 emptyings per solution, half of them filled
again within three moves — ride inside a phase instead of delimiting one. Each
goal is stated with the bit of the board that makes it checkable, read off the
position **as the phase begins** rather than as it will be when the goal lands:
naming the columns the dragons sit in at the moment they collapse is just as
true and no use at all, because that board is ten moves away.

The phases are read off the line the solver already found. Making the *search*
prefer lines that fall into clean chains was tried and dropped: a solved line
can be reordered exactly — swap two adjacent moves when both orders are legal
and land on the identical position, and everything downstream is untouched — but
over 20 deals and 95 landmarks it pulled landmarks earlier by 0.15 moves each,
made 3 of the 20 lines worse, and cost more than the search itself. A
near-minimal line is already dependency-tight. Details, and what the plan does
not do, in [docs/plan.md](docs/plan.md).

## Development

```sh
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

CI runs the suite on Python 3.11, 3.12 and 3.13, lints with ruff, and builds
the image — then checks the built image can actually solve a deal and load its
template bank, rather than only that the build exited zero.

350 tests, about 40 seconds. They cover the rules (runs, dragons, autocollect,
deck validation), the solver — including replaying every move of a returned
solution against a fresh board to check it really wins — the text format, and
the conversation flow against stand-ins for Telegram's objects.

The plan has no ground truth to check against — there is no fact of the matter
about which goal a player would have named — so `tests/test_plan.py` checks the
weaker thing, that every claim it makes is one the position supports: the
phases partition the moves with no gap or overlap, each phase reaches the goal
it claims at the move it claims, and each piece of evidence is re-derived from
the position it is stated about.

The deck resolver is tested on made-up reads rather than screenshots
(`tests/test_resolve.py`): saying "this slot scored G3 first and G8 second"
outright is both clearer and more pointed than hunting for a real picture that
happens to be ambiguous in the right place. Its screenshot end is covered by
degrading the fixtures and checking they still come back exact.

The vision pipeline is tested on two levels. `tests/fixtures/` holds real
screenshots with the position written out beside them, and reading each one
back exactly is what says the thresholds suit the real artwork. On top of
that, `tests/fake_board.py` renders boards with the structure of the game
screen but stand-in marks, which covers states no screenshot happens to show —
an empty tableau, every cell locked, a board whose first columns are empty.
Those synthetic tests deliberately stop at the geometry: matching stand-in
marks would measure the stand-in, not the pipeline.

The suite answers yes or no on every fixture, which is what CI needs and no
use at all for tuning a threshold. For that there is a benchmark:

```sh
python -m shenzhen.vision.benchmark              # as Telegram delivers them
python -m shenzhen.vision.benchmark --width 0    # untouched, off the device
```

It scores the whole fixture set four ways — cards read right, boards read
exactly, reads the matcher declined to call, and reads it called confidently
and got wrong. The last of those is the one to watch: a confident read never
reaches the deck resolver, so each one is a silent mistake in a board the bot
presents as read.

What has already been measured about reading screenshots — which levers move
those numbers, which plausible changes turned out to do nothing, which
thresholds have no good value — is in
[docs/recognition.md](docs/recognition.md), which is worth reading before
touching `vision/` and worth adding to afterwards.

```
src/shenzhen/
  cards.py      card encoding and text notation
  game.py       rules, moves, the game's automatic behaviour
  solver.py     the search
  notation.py   rendering boards and moves, ru/en
  textio.py     parsing a typed position
  vision/       layout.py, classify.py, resolve.py, recognize.py,
                crop.py, verify.py, calibrate.py, benchmark.py
  bot/          main.py, handlers.py, i18n.py, storage.py, runtime_coverage.py
```
