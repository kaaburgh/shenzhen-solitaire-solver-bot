# Black-dragon JPEG chroma fringe regression

This note preserves the provenance and measurements behind the black/white
dragon fix merged in PR #29. It supplements the `A shaky ink colour has to
stay shaky` section of `docs/recognition.md`.

## Source screenshots

Two Telegram-returned JPEG screenshots triggered the same user-visible failure:
the bottom white dragon in one tableau column was reported as an uncertain
card instead of being accepted as `DB`.

The original files were both 1280x959 JPEGs:

| case | bytes | SHA-256 | affected card |
| --- | ---: | --- | --- |
| 1 | 105129 | `214ba995c8bb3c8278aa9785c8741b6bea9cd7e64fa8134499fd70714162a340` | column 5, bottom `DB` |
| 2 | 96962 | `ba904c43643f75eda1cd680bf77faef60e287bb3dca58e8cdbdd4a0877dc6c51` | column 4, bottom `DB` |

The full expected positions, read from the screenshots and checked against the
40-card deck, are:

```text
# case 1
free: DR XG DB
flower: 1
foundations: 0 0 2
1: G9
2: R1 G4 R9
3: R6
4: R5 G3 G6 B5 R4 B3 G2
5: B4 R8 DB
6: G1 R3 DR DB
7: DB G7 R2 B8 B9 G8 R7
8: DR G5 B7 DR B6
```

```text
# case 2
free: . G9 .
flower: 1
foundations: 3 0 0
1: DG B3 B7 DR DB
2: B8 DR R1 DR
3: DR R7 DB R2 DG
4: R3 B1 R4 G6 DB
5: B6 R5 G4
6: B9 B4 B5 DG
7: DG G5 B2 G8
8: DB G7 R8 R6 R9
```

## What the classifier was seeing

Recognition enlarges these Telegram-width cards before classification. The two
affected corner patches are 99x57 pixels; after the 4% edge exclusion the ink
statistics are computed over 91x53 = 4823 pixels, so
`COLOUR_FRACTION = 0.0075` gives a threshold of 36.1725 coloured pixels.

Before the fix, the HSV saturation test admitted 41 pixels in case 1 and 57 in
case 2. Their mean hue landed in the green range, so both dragons became
`GREEN, colour_certain=False` rather than black. The deck resolver settled the
other shaky cards but these reads remained open and produced the user question.

Those admitted pixels were JPEG chroma fringe around the near-black rectangle,
not genuine coloured ink. Their maximum absolute BGR channel spread was only
19 levels in case 1 and 18 in case 2. The existing washed-out green regression
(`HSV (62, 55, 130)`) has an absolute BGR spread of 28 levels.

PR #29 therefore did not move `COLOUR_FRACTION` or the saturation threshold.
It added `COLOUR_CHROMA = 20` and requires a pixel to clear both relative HSV
saturation and this absolute channel-spread floor. With that filter, both
measured dragon patches contribute zero coloured pixels and read as
`BLACK, colour_certain=True`, while the washed-out green regression still
clears the floor.

Compact regression coverage lives in
`tests/test_black_dragon_chroma_regression.py`. It preserves the measured
fringe-pixel distributions without re-encoding the screenshots. The source
JPEG hashes above are retained so exact end-to-end fixtures can be matched to
the files later without ambiguity.
