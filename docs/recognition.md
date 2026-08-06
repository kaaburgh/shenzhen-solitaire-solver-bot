# Reading the board off a screenshot: what is known

Working notes for anyone changing `src/shenzhen/vision/`. Not an overview —
the README has that, and `docs/calibration.md` covers rebuilding the template
bank. This is the part that was expensive to find out and cheap to lose:
what the input actually looks like, which levers move the numbers, which ones
were tried and did nothing, and what breaks if you are not careful.

**Read it before changing a threshold. Add to it when you learn something.**
A measurement that took an afternoon and lives only in a commit message will
be taken again by the next person; a dead end that is not written down will be
walked down again.

## Measuring

There is a benchmark. Use it instead of eyeballing a fixture:

```
python -m shenzhen.vision.benchmark              # as Telegram delivers them
python -m shenzhen.vision.benchmark --width 0    # untouched, off the device
python -m shenzhen.vision.benchmark --verbose --confusions
```

Four numbers, and they are not interchangeable:

| | what it means |
| --- | --- |
| `cards` | reads matching the known board, before the deck gets a say — the matcher's own score, the one that moves when a threshold moves |
| `boards` | fixtures whose final position is exactly right — what the user experiences, always higher, because the deck recovers misreads |
| `flagged` | reads the matcher declined to call — **not** a failure; this has to stay above zero or the deck has nothing to work with |
| `confident-wrong` | called confidently and wrong — **the one to watch** |

`confident-wrong` is the number that matters most and the one nobody looks at.
A confident read bypasses the resolver entirely, so each of these is a silent
mistake in a board the bot presents as read. Sharpening the matcher tends to
move `cards` and `confident-wrong` in the same direction, and only one of them
shows up in a headline. **A change that improves `cards` while raising
`confident-wrong` is a regression.**

Current baseline, 12 fixtures, 31 templates:

| | cards | boards | flagged | confident-wrong |
| --- | --- | --- | --- | --- |
| untouched (`--width 0`) | 99.5% | 12/12 | 5 | 0 |
| as Telegram sends it | 99.3% | 12/12 | 7 | 0 |
| `--quality 20` (past what is real) | 96.5% | 8/12 | 20 | 10 |

### Traps in measuring

* **Never re-compress a `.jpg` fixture.** Those came back out of Telegram
  already; squeezing them again measures a picture nobody will ever send. The
  benchmark and the test suite both special-case the suffix — keep that.
* **Do not sweep to arbitrary widths and read the boards column.** Rescaling
  to, say, 940px introduces column-splitting artefacts that have nothing to do
  with the picture being small, and they show up at *large* widths too (157,
  180, 191px cards all produce a spurious seam on some fixture). Sweeping is
  fine for `cards`; for `boards`, measure at widths that actually occur.
* **`cards` only scores tableau slots** — 39 of 40 — because those are the
  labels that line up with the text notation without guessing. It is counted
  over the slots the board is *known* to have, not over the reads that came
  back, so a card the layout never found counts against it. That matters: the
  obvious way round makes a total layout failure score 100%, and the worse the
  geometry gets the better the number looks. A fixture whose geometry fails
  outright reports `NO LAYOUT` and scores zero for that board rather than
  stopping the run.

  The home-indicator bug below is the worked example. It ate a whole column —
  six cards gone — and against the board's known slots that reads 97.8%
  against 99.3% fixed. Scored over the reads that came back it would have
  read **99.2%**: a tenth of a point, for one eighth of the board vanishing.
* Reads landing in tableau slots the board does not have — a column cut one
  card too many — are reported in brackets rather than scored. There is no
  truth to compare them against, and `boards` already fails.

## The input

Telegram scales a photo's long side to about **1280px** and re-encodes it as
JPEG around quality 80. On a phone screenshot of the game that puts the cards
at **90-100px wide**, with a rank glyph about ten pixels tall. Sent as a file
instead, the picture arrives untouched.

Fixtures live in `tests/fixtures/<source>/`:

* `.png` — untouched, off the device. The suite degrades these to simulate
  Telegram.
* `.jpg` — came back out of Telegram. Read exactly as they landed.

Card widths, for orientation: iPhone screenshot 193px native, 97px through
Telegram. iPad 252px native. `compressed/shot1` is 180px native and is a
doubly-compressed one — it is the fixture that breaks first, and that is what
it is for.

## Load-bearing findings

### Enlarging is registration, not detail

The single biggest lever on small pictures. `recognize.enlarge` blows a
sub-150px picture up by a **whole-number factor, bilinear**, before reading it.

It adds no information — a blurred 3 stays a blurred 3 — and it still took
`cards` from 89.0% to 99.1% and `boards` from 3/10 to 10/10 on the ten
fixtures that existed at the time, delivered the way Telegram delivers
them. What it buys is registration: crops are cut on
integer pixels off a lattice fitted to the columns, so at 97px the glyph lands
up to half a pixel from where the templates expect it, on a glyph ten pixels
tall.

Both choices were measured, not assumed:

* **bicubic** — overshoot sharpens JPEG ringing into strokes that are not
  there. 97.9% against bilinear's 99.1%, and it left a `confident-wrong`.
* **nearest** — preserves the very error the enlargement exists to remove.
* **fractional target widths** — beat against the original pixel grid, so
  which cards get smeared depends on where they happen to sit. Targets of 170,
  190 and 200px gave 97.9%, 99.1% and 98.5%: noise, not signal. Anyone tuning
  that number is fitting the fixtures.

### Every confident read was right

On the screenshot that prompted the enlargement work, 15 of 40 cards were
misread — and **all 15 were among the reads the matcher had already flagged**.
All 20 confident reads were correct.

This is the property the whole design rests on: uncertainty is routed to the
deck and to the user, so it only has to be *detected*, not resolved. Anything
that makes the matcher more confident without making it more right attacks
this directly. It is why `confident-wrong` is the metric to watch.

### The screenshot contains the phone, not just the game

A screenshot is of the whole screen. On an iPhone the home indicator — the
pale bar along the bottom — lies right across whichever column runs that far
down, and it is bright and unsaturated, so `card_mask` takes it for a card
face.

What makes it expensive is that it *touches* the column, so it is not a stray
blob that could be ignored. The two fuse into one component about three cards
wide, the width filter throws that out as not card-shaped, and the column goes
with it. **A whole column missing is the one error the deck cannot repair** —
six cards absent read as six cards missing, and every reading fails.

What separates the phone's furniture from the game is *length*. Nothing the
game draws is wider than one card, and the slots sit a quarter of a card
apart, so there is a clear band between them for the threshold to live in
(`LayoutConfig.overlay_width`, 1.3 cards). A horizontal opening with a kernel
that long keeps exactly the runs that are not the board.

The part worth remembering is what happens next. Cutting the bar out leaves
the column it lay across in two pieces, so the cut-out is **kept rather than
deleted**: two blobs are one column when what lies between them is precisely
what was erased. That condition is not decoration — it is what stops the join
from bridging the felt between the top row and the tableau, which is a narrower
gap than the bar is thick. A join keyed on distance alone would fuse those two
rows on every board.

Generalises beyond this one bar: any phone or desktop chrome drawn over the
board is wider than a card or it is not in the way.

### A shaky ink colour has to stay shaky

`ink_reading` decides green/red/black by what fraction of the crop reads as
saturated ink — a **fraction, not a pixel count**, so that enlarging a picture
cannot change its colours.

The interesting part is that there is no threshold that separates the two ways
it goes wrong. Washed-out green on a twice-compressed screenshot lands just
under the line; the chroma fringing along the white dragon's black rectangle
lands just over it. Measured across the fixtures, the worst true-green crop
reads 0.005 and the worst true-black 0.011 — they overlap, and no value has
them on opposite sides.

So a reading that lands within a factor of `COLOUR_UNCERTAIN` of the threshold
sets `Guess.colour_certain = False`, which makes the whole read unsure. The
deck settles both cases once they arrive as doubt rather than as a decision.

It is cheap: 6 reads out of 441 across the fixture set at Telegram's width
land in that band. It is also, as of now, why almost everything that gets
flagged gets flagged — 6 of the 7. If a change drops `flagged` to zero, check
whether this is what it switched off before believing the matcher got better.

**Do not try to fix this by tuning `COLOUR_FRACTION`.** It has been tried; the
distributions overlap.

### Two width thresholds, for two different questions

There are two, and confusing them is how the bot ended up blaming the picture
for its own bugs.

`MIN_RELIABLE_CARD_WIDTH` (150) is **where enlarging is needed** — the width
below which the matcher wants help. It is an input to `enlarge` and nothing
else.

`MIN_RECOVERABLE_CARD_WIDTH` (75) is **where the size is worth mentioning to
whoever sent the picture** — where enlarging stops being enough. It is what
both `RecognitionError.narrow` and `Recognition.narrow` are keyed on, and so
the only one the user ever hears about. Measured with the whole pipeline in
place, enlargement and deck check included, which is what the sender actually
gets:

| card width | readings the deck threw out |
| --- | --- |
| ≥100px | none (0/239) |
| 80–99px (a phone screenshot sent as a photo) | 1 in 22 (2/44) |
| 70–79px | 1 in 11 (2/22) |
| <70px | 1 in 2 (17/34) |

Swept over the `.png` fixtures rescaled from 700px wide upwards in steps of
50, bucketed by the card width that came out. Reproduced independently of the
run that set the threshold, which swept differently and got 1 in 120 / 1 in 17
/ 1 in 7 / 3 in 5 — same shape, different denominators. The shape is the part
that matters, and it is robust: clean at ≥100px, rare in the eighties,
common below seventy.

"Send it as a file" is real advice and at 97px it is the wrong advice: a
reading that fails at the width Telegram delivers has almost certainly failed
at something else, and saying "your picture is too small" sends the sender off
to fix what was not wrong. That is not hypothetical — it is what the bot did
for a column the phone had drawn its home indicator across.

Nothing arriving as a Telegram photo comes near 75px. A picture that small has
been cropped or scaled by hand, and then the size really is the thing to fix.

### A seam is wide, a glyph is not

The column splitter cuts a stacked column at rows where brightness steps up.
The strokes of the big glyph on the bottom card do that too. What separates
them is that a seam is one edge right across the card and a stroke is not —
see `LayoutConfig.min_step` and the commit that introduced it (`45e0e94`).
This is also the one threshold in `LayoutConfig` that is not a length and so
does not scale with the card: the game draws the seam the same way at every
board size. It measures 13-26 grey levels on real screenshots against about 2
for a glyph, and anything in 4.5-7.5 reads every fixture right.

## Dead ends

Written down so they are not walked again. All measured on the fixture set at
Telegram's width, against a 92.6% baseline at the time:

* **Smooth interpolation inside `standardise`.** `cv2.resize` with
  `INTER_AREA` degenerates to nearest when *enlarging*, and the corner crop is
  40x23 stretched to 40x40 — so this looked like a real bug. Switching to
  cubic for the enlarging case: 92.6%, unchanged. Not where the loss was.
* **Low-passing the templates to the crop's own resolution** before scoring,
  so a sharp reference cannot out-score a blurred one. 93.2%, +0.6%. Real but
  not worth the complexity next to enlarging.
* **Raising `MAX_UNKNOWNS`** to let the resolver chew on 21 shaky reads. The
  search truncates at `MAX_POSSIBILITIES` long before it converges, and a
  truncated search cannot honestly call anything settled. The answer to too
  many unknowns is to read better, not to search harder.

## Known and not fixed

* At some card widths (157, 180, 191px measured) the column splitter finds a
  spurious seam and a column comes out one card too long. Predates the
  enlargement work, happens at large widths too, and Telegram does not produce
  those widths. Reachable by rescaling a fixture to an odd width.

## When you add a fixture

`docs/calibration.md` has the procedure. Two things worth repeating here:
save it as `.jpg` if it came through Telegram, and re-run the benchmark before
and after — a new fixture that moves `confident-wrong` off zero is telling you
something about the reader, not about the fixture.
