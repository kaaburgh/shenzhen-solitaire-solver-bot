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

Current baseline, `main`, 31 templates:

| | cards | boards | flagged | confident-wrong |
| --- | --- | --- | --- | --- |
| untouched (`--width 0`) | 99.5% | 11/11 | 5 | 0 |
| as Telegram sends it | 99.2% | 11/11 | 7 | 0 |
| `--quality 20` (past what is real) | 96.2% | 7/11 | 20 | 10 |

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
  labels that line up with the text notation without guessing. A layout failure
  that drops cards entirely will therefore *flatter* `cards`. Watch `boards`.

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

It is cheap: 6 reads out of 403 across the fixture set at Telegram's width
land in that band. It is also, as of now, why almost everything that gets
flagged gets flagged — 6 of the 7. If a change drops `flagged` to zero, check
whether this is what it switched off before believing the matcher got better.

**Do not try to fix this by tuning `COLOUR_FRACTION`.** It has been tried; the
distributions overlap.

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
