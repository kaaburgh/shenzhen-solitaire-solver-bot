# The plan

A numbered list of thirty-four moves answers a question the player usually
already knows the answer to. In a hard position, which moves are *legal* is on
the screen; what is not on the screen is which way to set off — dig the ace out
from under four cards, or go and collapse the green dragons, or run the blacks
up to 5 first to unload the table so that a column can be emptied at all. Get
that choice wrong and the next ten moves are wasted. So the answer leads with
the goals and hands over the moves underneath them:

```
✅ Решение есть — 34 хода до победы.

Замысел — за чем идти и в каком порядке:
ходы 1–10: убрать 🟩 драконов — они в колонках 1, 6, 7. По пути: 🟢 до 2, 🔴 до 3
ходы 11–26: увести 🔴 в сбор до 8. По пути: ⚫ до 6
ход 27: убрать 🟥 драконов — они в колонках 1, 5, 8
ходы 28–32: освободить колонку 7. По пути: ⚫ до 9
ходы 33–34: убрать ⬜ драконов — они в колонках 4, 5, 6. По пути: 🟢 до 9
```

Every batch of moves then carries the goal it belongs to at its head, because
the batches are read one at a time, minutes apart, and a batch that only
numbers its moves has lost the reason for them.

---

## What counts as a goal

Four kinds, and they are the ones players name:

| | |
| --- | --- |
| `DRAGONS` | four dragons of one colour collapse |
| `ACE` | a suit's foundation opens — its 1 is out from under the pile |
| `COLUMN` | a column comes empty, and more are empty than ever before |
| `COLLECT` | a suit is run up into its foundation |
| `FINISH` | nothing structural left; the rest of the line only collects |

The first three are landmarks in a strong sense: **no later move can take them
back.** A collapsed dragon colour is off the board for good, a foundation never
goes down, and "more columns empty than at any earlier point in this line" is a
running maximum — the column itself may well refill, but the record it set
stands. That is what makes them usable as boundaries: their order in the line
is the order they have to be done in, so cutting the line at them cannot invent
a dependency that is not there, and the moves between two of them are exactly
what the second one cost.

Everything else rides *inside* a phase rather than delimiting one. Foundations
advance every other move or so, mostly by the game's own automatic collection,
and columns empty and fill again constantly: over twenty deals a column comes
empty a median of **15 times per solution**, and **half of those emptyings are
filled again** — 92% of them within three moves. A column that empties and
refills was a step, not a destination, which is why `COLUMN` needs the record
and not merely the emptying.

`COLLECT` is the one goal that is not a landmark, and it exists for a specific
failure: a phase of twenty-two moves whose only heading is "collapse the white
dragons" says nothing at all about the next five. A stretch that long is nearly
always spent feeding one suit into its foundation before the dragons can be
reached, so a phase over eight moves is cut once, where that suit last
advances, and the two halves get a goal each. Ordered longest-first, and
`MAX_PHASES` gives way for a phase twice over the limit: twenty moves under one
heading are worse than a seventh line.

## Saying it against the board in front of you

Each goal carries one piece of evidence — which columns the dragons are in, how
deep the ace is buried, what will land in the emptied column — and all of it is
read off the position **as the phase begins**.

This is not a detail. Describing a dragon collapse by the columns the dragons
sit in *at the moment they collapse* is just as true and no use whatsoever:
that is a board ten moves in the future, and the one in front of the player
says something else. Dragons get parked in cells and shuffled between columns
on the way. So `_sitting` is evaluated at the phase's first position, where it
is something the player can check by looking, and `test_plan.py` pins that by
re-deriving every claim from that same position.

## Phases are read off the line, not searched for

The obvious ambition is to make the *solver* prefer lines that fall into clean
conceptual chains. It was measured and dropped, and the negative result is the
reason the design is as simple as it is.

There is an exact, cheap way to reorder a solved line. Two adjacent moves may
be swapped when both orders are legal **and land on the identical `State`** —
at which point everything downstream of the pair is untouched, including the
automatic collections and which free cell holds what, so the rest of the line
stays valid with no re-verification at all. Pull each landmark left through
such swaps (recursively hoisting whatever blocks it) and the moves that were
never part of reaching it fall behind it, which is exactly "if the dragons can
be done first, do them first".

On 20 deals, 95 landmarks:

* **14 landmark positions pulled earlier in total** — 0.15 moves per landmark;
* **3 of the 20 lines came out worse**, a landmark landing *later* than before,
  because hoisting one landmark drags moves across another;
* **1.5–4.7s per deal**, against a median solve of well under half a second.

The reason is not a weak implementation, it is the input: weighted A\* returns
a near-minimal line, and in a near-minimal line almost every move is
load-bearing for something soon after it. There is little slack to squeeze,
which is also why the phases read sensibly without any reordering — the
interleaving the transformation was meant to remove is largely not there. Ten
times the cost of the search for a fifteenth of a move per landmark is not a
trade worth making, so the line the solver found is the line the plan
describes.

## What it does not do

* **A digging phase gets no intermediate goal.** About a third of deals still
  have one phase over fifteen moves, and those are the ones where nothing is
  collected on the way — the whole stretch is unburying a dragon or an ace,
  with no sub-goal in the vocabulary to name. The plan says so honestly ("the
  next fifteen moves are getting to the white dragons"), which is at least the
  right expectation to set, but it is not guidance.
* **The goals are an interpretation.** There is no ground truth for "the goal a
  player would have named", so the tests check something weaker and
  well-defined: that the phases partition the moves, that each phase really
  reaches the goal it claims at the move it claims, and that every piece of
  evidence holds in the position it is stated about.
* **Causality is not claimed.** A phase says what it achieves and what it
  passes on the way; it does not say that the collecting was *in order to*
  reach the landmark, however likely that is. `По пути` is deliberately weaker
  than `чтобы`.
