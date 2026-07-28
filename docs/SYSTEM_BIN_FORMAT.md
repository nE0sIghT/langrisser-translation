# SYSTEM.BIN text format

`SYSTEM.BIN` holds the game's UI font and all of its menu/UI text: unit, class,
item, weapon, spell and character names; their triangle-button descriptions; the
menu command help; and the save / memory-card messages. This document describes
how that text is stored, so it can be dumped, translated and packed back with a
single flow (`langrisser/system_dump.py` + `langrisser/system_pack.py`) instead of
content-matched global replaces.

## Layout overview

```
0x00000 .. 0x0800A   font/menu-adjacent glyph data through glyph slot 1820
0x0800A .. 0x08052   non-text data before the first string group
0x08052 .. ~0x179B0  text region: a sequence of string groups (+ a few loose runs)
```

Glyph codes are 16-bit little-endian indices into the font plane (`code * 18`
points at the glyph). Special codes:

| code | meaning |
| --- | --- |
| `0x0000` | space |
| `0xFFFC` | soft line break inside a run |
| `0xFFFF` | end of string (run terminator) |
| `>= 0xFB00` | reserved / control (not a glyph) |

Glyph 1820 starts at `0x7FF8` and occupies bytes through `0x8009`. The bytes
from `0x800A` through `0x8051` are non-text prelude data, so
`langrisser/system_dump.py` starts at the first verified string-group table,
which for this build is `0x8052` (each release records its own as
`system_scan_start`); that group's first string base is `0x8298`. `langrisser/build_font.py`
rewrites glyphs only up to slot 1820 to draw the target alphabet. Runtime font
cell behavior and the verified 12x12 boundary probes are documented in
`docs/FONT_RENDERING_MODEL.md` (see also `IMG_DAT_FORMAT.md` for the unrelated
picture assets).

## String groups

Almost all text lives in **groups**. A group is:

```
[ u16 offset table : N entries ][ optional preamble ][ N glyph strings ]
```

- **Offset table** — starts with `0x0000` and holds strictly increasing 16-bit
  *word* offsets. Entry `k` is the start of string `k`, measured in 16-bit words
  from the string base. Successive entries differ by `len(string_k) + 1` (the
  `+1` is the string's `0xFFFF` terminator), so steps are small (a line is at
  most ~0x20 words). The table ends where the values stop ascending.
- **Preamble** — most groups have none (`base = table_end`), but a few (e.g. the
  memory-card group) keep a small fixed block of words between the table and the
  first string. The real base is the one where a `0xFFFF` terminator sits just
  before every string start; the dumper searches `0..16` preamble words for it.
- **Strings** — string `k` is at `base + offset[k]*2` and runs to its `0xFFFF`.
  Length is `offset[k+1] - offset[k] - 1` words for `k < N-1`; the last string
  runs to its own terminator.

A candidate ascending run is only a real group if **every** table entry points at
a `0xFFFF`-terminated run (validated terminator before each string start). This
rejects look-alike ascending data (nested sub-tables, stat arrays). The greedy
ascending read can also overshoot into the first string; the dumper trims the
table to the longest prefix that still validates.

The original Japanese build has 16 groups holding 2620 strings, plus 8
**loose** strings (the memory-card / "do not power off" messages) that have no
offset table and are addressed directly. The dumper emits those with
`group = -1`.

### Why the first line of a description used to look "glued"

A naive `0xFFFF` scan starts each run after the previous terminator, so it glues
a group's offset table onto string 0 (the table has no `0xFFFF` inside it) and
reports one oversized run of garbage + text. Parsing the table instead yields
string 0 cleanly. This is why the old help dump missed several first lines and
short tail lines.

## Editing flow

```bash
# 1. dump every string (offset table aware) to a generated inspection JSON
python3 -m langrisser.system_dump --out work/l5/systemdump/system_strings.json

# 2. translate the target-only stable-id overlay:
#    data/games/<game>/lang/<lang>/system_strings.json
#    "{BLANK}" clears a leftover line; omitted ids preserve the JP source.

# 3. resolve exact canonical names/terms inherited from the language pack
python3 -m langrisser.resolve_system_strings --lang <lang> \
    --system-source work/l5/systemdump/system_strings.json \
    --out work/l5/build/system_strings.<lang>.json

# 4. pack back into SYSTEM.BIN
python3 -m langrisser.system_pack \
    --system-in work/l5/build/SYSTEM.BIN.font \
    --system-out work/l5/build/SYSTEM.BIN.<lang> \
    --source-strings work/l5/systemdump/system_strings.json \
    --strings work/l5/build/system_strings.<lang>.json --strict
```

The generated source dump contains ids, offsets, budgets and JP text. It stays
under ignored `work/`. The durable language file is only a JSON object from a
stable source id to context-dependent target text. Exact source strings found
in the language pack's `names.csv` or `glossary.csv` are inherited
automatically and must not be duplicated in the overlay. Grouped ids use
`group:<ordinal>:<index>`; text runs outside the group tables use
`loose:<n>`. Neither names an address, so one translation serves every build;
the release manifest's `system_groups`/`system_loose` turn a name back into an
address, and a release that seats the pack's strings differently says so in its
`system_mapping.json`.

`langrisser/system_pack.py` has two modes:

- **in-place (default)** — each translated string is written into its original
  slot, padded with `0xFFFF`; the offset table is untouched. This is
  byte-compatible with the original layout, so every string keeps its exact
  position. A translation may not be longer than the original line.
- **`--repack`** — the offset table is regenerated from the actual string
  lengths, so a string may be longer or shorter (bounded by the group's total
  size). Because this moves later strings within the group, it is only safe if
  the game locates strings by table index (not by absolute offset).
  The selected language pack's `system_layout.json` caps how many words wider
  than the original each line may get. The conservative default is 4; lines
  verified to need more room use explicit stable-id overrides. The
  `--max-grow N` option can replace the default for diagnostics, but the normal
  build takes its limits entirely from the language pack.

  **Index addressing is verified in the EXE (so `--repack` is safe).** SYSTEM.BIN
  is loaded to the fixed base `0x80134a00` (the constant is built at
  `SLPS_018.19:0x8001a878` and confirmed against a live RAM dump). The engine
  never holds absolute string pointers — it recomputes them from the relative
  tables at runtime:
  - init builds a string-pointer array as `ptr[k] = base + table[k]` (loop at
    `0x8001a8ac`: `lhu` a table entry, `addu` the group base, `sw` to the array);
  - the per-string accessor is `addr = base + table[k]*2` (`0x800184f0`:
    `lhu`,`sll …,1`,`addu`), exactly the word-offset layout above.

  Regenerating the table while keeping each group's base fixed therefore yields
  addresses the engine reads correctly. A final in-game pass is still worthwhile,
  but it is confirmation, not a gamble.

### Display limits

A string is one on-screen line. Even though `--repack` frees the data from the
original byte length, the **display** does not grow: each line is bounded by the
text box width (about 21 full-width cells; compact pair fonts pack roughly two
letters per cell), and a help topic has a fixed number of lines (one run per line, and `N` is
fixed per group). Growing a line past the box width clips it. So `--repack` only
usefully reclaims room on lines that were under-full or via re-flowing a topic
across its existing lines — it does not allow unbounded expansion.

The unit, item and magic description tables are fixed four-line cards.
`data/games/l5/system_card_layout.json` records their group ordinals and verified
21-cell line width. `langrisser/reflow_system_cards.py` treats each card as one text
block and deterministically redistributes words across its four lines using the
exact generated target-language table. Per-line leading cells reserved for
engine-drawn values are recorded by the dumper and subtracted from the available
width. The packer then enforces the same
absolute cell limit for those groups instead of the conservative per-source-line
growth heuristic.

Some compact status and class-name fields are narrower than the normal
21-cell line. Runtime-verified limits for these fields live in
`data/games/l5/system_ui_constraints.json` under `fixed_width_fields`.
`langrisser/validate_system_ui.py` measures the resolved translation with the exact
generated language table and rejects a build that exceeds one of those limits.

### Startup-menu VRAM atlas rows

The startup menu streams its three labels through `FUN_800a5b14` in this order:

1. `group:0:211` (`START`)
2. `group:0:71` (`LOAD`)
3. `group:0:212` (`コンフィグ`)

Each encoded token is converted to a 12x12 bitmap and uploaded to a temporary
VRAM atlas with 9 columns:

```text
x = 0x395 + (slot % 9) * 3    # width is in 16-bit VRAM words
y = 0x100 + (slot / 9) * 12
```

The atlas has 189 slots (`9 * 21`); `FUN_800a5b14` checks the upper bound at
`0x800a5b64`. A separate `0xBD` immediate at `0x800815fc` is an unrelated
screen coordinate.

Although the renderer contains code intended to split a strip at an atlas-row
boundary, the continuation is not displayed on this menu. Therefore each
individual label must fit in the remainder of its current 9-cell row. The
original layout preserves this invariant: `START` uses 5 slots and `LOAD` uses
4, so `コンフィグ` starts at slot 9.

This was confirmed by the Russian build: `Начать` (3) plus `Загрузка` (4)
placed `Настройки` (5) at slot 7, displaying only its first two tokens
(`Наст`). Shortening the preceding label by one slot exposed exactly one more
token. Using `Новая игра` (5) restores the original `5 + 4 = 9` alignment and
the full label displays.

`data/games/l5/system_ui_constraints.json` records these sequences.
`langrisser/validate_system_ui.py` encodes the final target strings with the
generated table and fails the build if a label crosses an atlas row.

## Round-trip guarantee

Dumping with the JP table and packing an empty translation overlay reproduces
SYSTEM.BIN byte-for-byte (`langrisser/system_pack.py --system-in SYSTEM.BIN --tbl
data/common/tables/lang5_jp.tbl`), which is the correctness check for the group
parser.
