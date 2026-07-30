# Font rendering model

This document records verified facts about Langrisser V's runtime text glyph
rendering. It is not a proposal for a target-font layout; keep implementation
choices separate until they are tested in game.

## Font plane

Confirmed from `/L5/SYSTEM.BIN` and the existing extractor/build pipeline:

- Printable text tokens are 16-bit glyph indices.
- A glyph index points directly into the font plane at `index * 18`.
- Each glyph is a `12x12` 1bpp bitmap: `12 * 12 = 144` bits = `18` bytes.
- Valid font glyph slots end at index `1820`. Bytes after glyph 1820 are menu
  data, not font data.
- Runtime text uses the same 12-pixel horizontal cell advance as the stored
  glyph width.

## Native glyph edge usage

A scan of original glyph slots `0..1820` shows that native glyphs are not
constrained to a universal one-pixel right guard:

| edge usage | count |
| --- | ---: |
| touches `x=0` | 15 |
| touches `x=11` | 1616 |
| touches `y=0` | 1643 |
| touches `y=11` | 12 |

The `x=0` users are mostly symbol/icon slots; among ordinary Latin letters,
`W` is the only observed left-edge case. This supports treating `x=0` as an
unsafe/rare column for target text letters, but it does **not** support treating
`x=11` as globally clipped or forbidden.

## Runtime boundary probe

A temporary RU quiz-start patch reused disposable icon slots as probe glyphs:

- one glyph per vertical stroke `x=0..x=11`;
- one blank spacer glyph;
- one ruler glyph repeated across 21 cells;
- one glyph containing both `x=0` and `x=11`.

Observed in game:

- Repeating the ruler glyph produces a continuous 21-cell line with no extra
  horizontal gap between glyph cells.
- A row of `x=0..x=11` probes aligns as expected: each next probe is shifted by
  one pixel inside its cell.
- `x=0` is visible.
- `x=10` is visible.
- `x=11` is visible.
- `x=11 + x=0` in adjacent cells produces a two-pixel white vertical stroke at
  the cell seam, proving that the engine does not clip the rightmost column.
- A full row of `x=11` probes and a full row of `x=10` probes are both visible.

Conclusion: the game does not globally clip the right edge of a 12x12 glyph.
Visible right-edge damage in translated text must be caused by how our target
font glyphs occupy the cell, by the outline/shadow pass, or by specific glyph
art, not by the cell width itself.

## Runtime outline / shadow observations

The same probe showed that text is not rendered as plain 1bpp white pixels.
White strokes have black pixels around them. The exact draw routine is not yet
specified, but these observations are confirmed:

- A sparse `x=0` vertical white stroke shows black pixels on its right side.
- A sparse `x=10` vertical white stroke shows black pixels on both sides.
- A sparse `x=11` vertical white stroke shows black pixels on its left side.
- At an `x=11 + x=0` cell seam, the two white strokes merge into a two-pixel
  white stroke with black pixels visible on both sides.
- A single glyph containing both `x=0` and `x=11` shows inward black pixels:
  the left stroke shadows to the right, and the right stroke shadows to the
  left.

Working interpretation, not yet disassembled proof: the renderer applies a
black outline/shadow around white glyph pixels, constrained by the available
neighboring pixels/cell edges. Because edge ink cannot receive an outside
outline pixel inside the same 12-pixel cell, target glyphs that place meaningful
letter-body ink on `x=11` or `y=11` can look visually cut off even though the
white pixel itself is drawn. Top-edge diacritics are a likely exception:
`Ё/ё/Й/й` dots/breve may be acceptable at `y=0` if the main letter body remains
inside the safe area.

This means the safe target-letter area is smaller than the physical `12x12`
tile, but the rule is not a binary ban on every edge pixel. Treat `12x12` as
the storage/advance size, not as the full usable ink box for newly rendered
target-language letters. A few decorative or diagonal edge pixels can be
acceptable; dense body strokes on an edge are the real risk.

## Current target-font generator behavior

As of this document, `langrisser/build_font.py` renders compact pair glyphs
with this layout:

- first half: source pixels shifted to `x=1..5`;
- second half: source pixels shifted to `x=7..11`;
- `x=0` and `x=6` are guards;
- `x=11` may contain real ink from the second character of a pair.

Measured examples from the generated tables/builds:

- EN `read each your` encodes as `re`, `ad`, ` e`, `ac`, `h `, `yo`, `ur`.
- EN pair glyphs such as `re`, `ad`, `ac`, `yo`, `ur` occupy `x=1..11`.
- RU pairs such as `Го`, `сп`, `од`, `ин`, `из`, `ар`, `оф`, `-т`, `ты` also
  occupy `x=1..11` in the current build.
- Generated RU pairs frequently use the bottom edge for descenders and tails.
  Examples include pairs with `р/у/ц/щ`; generated examples such as `ар`, `ро`
  occupy `y=4..11`.
- Generated RU pairs with `Ё/Й` can use the top edge. Examples such as `Ёр` and
  `Йе` occupy `y=0..9`; `Ёр` spans the full `y=0..11`. The top-edge pixels here
  are diacritics, so this is not automatically a defect; bottom-edge overlap
  from descenders/tails is the stronger confirmed risk.

This explains the observed symptom without proving the final fix: many pair
glyphs put ink on physical tile edges, where the white pixels are visible but
the outside black outline cannot be represented inside the cell.

Edge-risk classification should therefore consider the amount and role of edge
ink, not only whether an edge is touched:

- low risk: one or two diagonal/decorative pixels, such as a `W` corner or a
  left-edge pixel in a `З`-like shape when spacing requires it;
- medium risk: short edge accents or diacritics (`Ё/ё/Й/й` top pixels);
- high risk: long vertical/horizontal body strokes on `x=0`, `x=11`, or
  `y=11`, especially letters such as `С`, `Н`, `М`, `П`, `Ш`, or pair glyphs
  where the second character has a full right-side stem.

## Open questions before changing layout

Do not treat these as solved until another runtime patch verifies them:

- Whether pair glyphs can move the second half from `x=7..11` to `x=6..10`
  without making the current 6x12 Terminus shapes look too crowded.
- Whether the target font should be constrained to an effective `4x10` or
  `5x10` ink box inside each half-cell, rather than using the physical `6x12`
  font metrics directly.
- Whether top diacritics (`Ё/ё/Й/й`) should be allowed to use `y=0` while
  keeping the main glyph body inside the safe box.
- How to score edge-risk automatically: raw edge touch is too strict; the
  validator should distinguish sparse decorative pixels from dense body
  strokes.
- Whether the current base font should instead be rendered narrower, edited per
  glyph, or replaced.
- Whether singles that legitimately use `x=11` have the same visual problem or
  only pair glyphs are objectionable.
- Whether the outline/shadow algorithm is shared by all text contexts or differs
  between SCEN text, SYSTEM/UI text, and special menus.
- The exact draw order and color/CLUT behavior of the runtime text outline.

## Rejected experiments

### Terminus pair box `x=1..5` + `x=6..10`

Tested in a temporary RU PPF:

- pair glyphs were forced off `x=11`;
- pair halves used `x=1..5` and `x=6..10`;
- pair halves touching `y=11` were shifted up by one pixel;
- `Ё/Й` top diacritics were still allowed at `y=0`.

Mathematically the generated pair glyphs avoided `x=11` and `y=11`, but the
runtime result was rejected: the text looked uneven and unstable, with letters
visibly "jumping" in multiple directions. This approach removes the inner
half-cell guard and applies per-half vertical shifts, so it trades the right-edge
outline issue for worse overall rhythm. Do not retry this exact layout as a
final solution.

### Tom Thumb 3px/4px Cyrillic cores

Tested in temporary RU PPF builds using Tom Thumb Cyrillic sheet data:

- `3x5` white core at the original size: readable only by boundary separation,
  but too small in real gameplay text.
- `3x8`: same horizontal core, vertically stretched. The text remained too
  weak and was rejected.
- `4x8`: mechanically widened core. Runtime outline/shadow expanded it into a
  heavy unreadable blob.
- `air4x8`: row-wise `3px -> 4px` widening that preserved open counters better
  than mechanical scaling. Runtime result was still rejected as unreadable.

Conclusion: Tom Thumb is not a viable source for the Russian game font. The
forms are too small at 3px and too crude when widened. Future work should use a
better 5px/6px source or a purpose-built bitmap Cyrillic alphabet shaped for
the game's outline, not another Tom Thumb scaling variant.
