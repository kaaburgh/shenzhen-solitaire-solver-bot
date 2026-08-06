# Working on this repo

A Telegram bot that reads a Shenzhen Solitaire screenshot and says whether the
position is still winnable. `README.md` is the tour; this file is the part
that changes how you should work.

## Reading screenshots — read the notes first

**Before changing anything under `src/shenzhen/vision/`, read
[`docs/recognition.md`](docs/recognition.md).**

It holds what has already been measured about reading the board off a picture:
what Telegram does to a screenshot, which levers move the numbers and by how
much, which plausible-looking changes were tried and did nothing, and which
thresholds have been shown to have no good value. Several of those findings
cost an afternoon of measurement each. Re-deriving them is the default failure
mode on this part of the codebase, and the notes exist to stop it.

**When you learn something there, write it down.** Same commit as the change.
In particular:

* a measurement worth quoting later — put the number in the notes, not only in
  a commit message
* a change you tried that did not help — the Dead ends section; an unrecorded
  dead end gets walked down again
* a threshold you found has no clean value, and what you did instead
* a failure mode you hit and left unfixed — the Known and not fixed section

Do not rewrite the notes into a summary of the current code. They are a record
of *why* the code is what it is, and the parts about approaches that are no
longer in the tree are the most valuable parts.

### One shape of bug to check for by name

`docs/recognition.md` has a section called *The mistake this code keeps
making*. Read it — it is the only entry there about the code rather than about
the artwork, and it has caught this repo twice in consecutive changes.

The short version: **a function that takes a geometric region and returns a
bare number is suspect.** Ask what it does when the region is partly off the
picture, or is not where the caller thinks it is. Clamping to the frame, or
measuring whatever is reachable, produces a plausible number about the wrong
pixels — and the plausible number is worse than a crash, because the next
step is usually to calibrate a threshold with it.

Go through `layout.region`, which returns `None` instead of clamping, rather
than slicing an array directly.

### Measure with the benchmark, not by eye

```
python -m shenzhen.vision.benchmark              # as Telegram delivers them
python -m shenzhen.vision.benchmark --width 0    # untouched, off the device
```

Quote the before and after in the commit message. Watch `confident-wrong` in
particular: a confident read bypasses the deck resolver entirely, so each one
is a silent mistake in a board the bot presents as read. A change that
improves `cards` while raising `confident-wrong` is a regression, whatever the
headline number says. `docs/recognition.md` explains the four figures.

The test suite is the other half of this and does not replace it: it answers
yes or no on every fixture, which is right for CI and useless for tuning.

### Screenshot fixtures

`tests/fixtures/<source>/shotN.png` plus `shotN.txt` with the position written
out. A screenshot that came back **out of Telegram** goes in as `.jpg` — both
the suite and the benchmark read `.jpg` fixtures exactly as they landed and
never compress them a second time, because a twice-squeezed picture is one
nobody will ever send. `docs/calibration.md` has the full procedure, including
rebuilding the template bank.

A new fixture that moves `confident-wrong` off zero is telling you something
about the reader, not about the fixture.

## Everything else

* `docs/calibration.md` — rebuilding the card template bank.
* `docs/coverage.md` — measuring what the running bot actually executes.
* Lint with `python -m ruff check .`, and run `python -m pytest` before
  pushing. Both are CI gates, along with a Docker build.
