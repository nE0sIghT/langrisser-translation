# Langrisser I & II (PS1) Disc Format

Reverse-engineering notes for the PlayStation release of *Langrisser I & II*,
written while working out what a Russian translation would have to touch. The
disc holds two complete games side by side, each with its own copy of every data
file, and they run on a different engine from the `l45` family the rest of this
repository targets.

Everything below is confirmed against the disc or the executable. Where a claim
is inference, it says so.

Source: `iso/l1-2-ps1-jp/Langrisser I & II (Japan) (Track 1).bin`, matching the
Redump entry for `SLPM 86798 / SLPS 00897 / SLPS 01822` (data track `b2ebdb90`,
286345 sectors).

## Disc layout

Two games, one filesystem, one boot executable:

```text
/SYSTEM.CNF              BOOT = cdrom:\SLPS_008.97;1
/SLPS_008.97   118,784   loader
/LANG1.EXE     456,704   Langrisser I
/LANG2.EXE     458,752   Langrisser II
/MOVIE.EXE     126,976   full-motion video player
/MOVIE/*.STR             22 MDEC streams, 340 MB total
```

All three are `PS-X EXE` images loading at `0x80010000`, so a file offset `f`
maps to address `0x80010000 + f − 0x800`.

Each game directory carries the same file names, so a tool that understands one
understands the other. Sizes differ because Langrisser II is the larger game:

| File | LANG1 | LANG2 | What it is |
| --- | ---: | ---: | --- |
| `SCEN.DAT` | `530,432` | `2,232,320` | scenario script |
| `FACE.DAT` | `3,657,728` | `21,671,936` | portrait art |
| `IMG.DAT` | `439,404` | `600,072` | screen graphics |
| `FONT.DAT` | `27,648` | `27,648` | glyph plane |
| `CLASS.DAT` | `7,140` | `7,140` | unit classes |
| `FIGHT.DAT` | `9,823` | `9,888` | battle tables |
| `COL.DAT` | `3,840` | `2,880` | palettes |
| `MAP.DAT` | `1,300` | `1,300` | map table |
| `VOICE.PAC` | `21,135,360` | `128,778,240` | speech |
| `BG.DAT` | — | `4,091,904` | backgrounds |
| `BGM.PAC`, `SE_SEQ.PAC`, `SE_VAB.PAC` | — | `9,158,656` + `1,057` + `946,176` | music and effects |

Langrisser I has no `BG.DAT` and no separate sound archives; its audio rides in
`VOICE.PAC` and CD audio (track 2).

The two-games-one-disc shape fits this repository's axes without a new concept:
one release (`l1-2-ps1-jp`) listing two games (`l1`, `l2`), each rooted at its
own directory, exactly as `l4-ps1-jp` and `l5-ps1-jp` root theirs at `/L4` and
`/L5`.

## FONT.DAT — the glyph plane

`LANG1/FONT.DAT` and `LANG2/FONT.DAT` are byte-identical, so both games draw
from one plane.

| Property | Value |
| --- | --- |
| File size | `27,648` bytes |
| Glyph size | 12 × 12 pixels |
| Depth | 1 bit per pixel, MSB first, no padding between rows |
| Bytes per glyph | 18 (144 bits, used exactly) |
| Slots | 1536 |

The executable confirms it: the glyph writer computes `slot × 18` against a
plane loaded at `0x80168000`.

This is the same cell geometry as `l45` — `data/engines/l45/manifest.json`
describes exactly these 18-byte cells — so the renderer that builds target
glyphs there produces cells of the right shape here. **The artwork is not
shared.** Comparing every tile against Langrisser V's plane, at every byte
alignment, matches 35 tiles of 1536, and 34 of those are blank. The slot order
differs too: Langrisser V opens with punctuation, digits and Latin, this plane
with katakana.

So `langrisser/derive_font_map.py`, which recovers a map by finding a
byte-identical tile in an already-mapped plane, has nothing to inherit here.

### The map

`data/common/font_mapping/l1_2_font_map.csv`, under `common/` because the two
games share the file byte for byte.

| | Slots |
| --- | ---: |
| Characters | 1500 |
| Icons, no character value | 2 |
| Blank | 34 |

All 1536 are accounted for. The plane was **read**, not recognised: recognisers
get the kana and guess the kanji, because at 12 × 12 ア and 了, エ and 工, カ and
力 are one drawing, and a homoglyph is wrong the same way in every context, so
agreement across contexts confirms it rather than catching it. Each slot was
then checked against the character rendered beside it.

Two slots were corrected afterwards by how the script uses them rather than how
they look: 818 is `Ｘ` and not `×`, 781 is `Ｉ`.

The blanks are literally zero — all 18 bytes — at slots **84**, **902**, and the
tail **1504–1535**. The thinnest real glyph has four lit pixels, so there is no
grey area between "blank" and "faint".

### Slot order

Katakana first, then a pool with no character-code order:

| Slots | Contents |
| --- | --- |
| `0`–`44` | katakana, ア through ロ |
| `45`–`68` | voiced and semi-voiced katakana |
| `69` | ヴ |
| `70`–`73` | ！ ？ ・ ー |
| `74`–`83` | small katakana |
| `85`–`95` | full-width digits, then `Ｄ` |
| `96`+ | hiragana, kanji, Latin, punctuation, in no code order |

Laid out in sequence the pool reads as fragments of running wording, so it was
built from some text in the order that text needed glyphs. It is **not**
`SCEN.DAT`'s order — first appearances there rise only 54% of the time, which is
chance.

### Icons

Slots **1502** and **1503** are the controller buttons. Both are drawn in a
heavy stroke unlike anything else on the plane, and neither is named by either
script even once — while the thin `×` at slot 275, which looks similar at a
glance, is ordinary text used 375 times.

They are recorded the way Langrisser V records its icons: `group` is `symbol`
and **`char` is empty**. An icon with no character value cannot be handed to a
font and cannot be named by target text; the empty cell is the enforcement, not
a convention someone has to remember.

### Latin

All 26 capitals are present and used, plus two lower-case letters (`ｍ`, `ｚ`)
and the ten digits. It is not decoration — the game writes real Latin words:
`ＧＡＭＥＯＶＥＲ`, `ウィンドウＯＰＥＮ`, `ＮＰＣ`, `ＢＧＭ`,
`Ｆ．Ａ．Ｉインターナショナル`, and monster cries like `ＧＵＷＡＡＡＡＡ！` and
`Ｚｚｚｚｚｚ`. It also carries the stat abbreviations `ＡＴ`, `ＤＦ`, `ＭＰ`,
`ＭＶ` with `＋`, `－`, `×`, `（`, `）` — `ＡＴ＋８・ＤＦ－３`,
`ＭＰ×２・魔法射程＋３`, `ＭＶ＋２（部下含）`.

Of the 1502 drawn slots the script names 1372; 130 it never names. Those 130 are
**not** free: most are the surname kanji at 1408–1471, which the staff credits
draw from somewhere other than `SCEN.DAT`. `SCEN.DAT` silence alone does not
make a slot reusable.

## SCEN.DAT — the script container

### Catalog

The file opens with a `u32` little-endian pointer table; the last pointer equals
the file size, so N pointers describe N−1 chunks. Every chunk starts on a
`0x800` boundary — one CD sector — and the first pointer is `0x800`, so the
catalog owns the first sector.

| | `LANG1/SCEN.DAT` | `LANG2/SCEN.DAT` |
| --- | ---: | ---: |
| File size | `530,432` | `2,232,320` |
| Chunks | 21 | 107 |

This is the same catalog Langrisser V uses — `langrisser/scen.py` documents
`u32 chunk_pointers[]` with the last equal to the file size. Below it the two
formats part company.

### Chunk interior

A chunk begins with its own `u32` section table whose first entry is the table's
own byte size, which is what makes it self-describing. Section counts vary by
chunk (7, 9, 10, 13, 16 and 46 were all observed); most sections hold packed art
or code.

**Section 2 is the text**, in every chunk that has one — 21 of 21 in Langrisser
I, 106 of 107 in Langrisser II. It is a table in the same self-describing form
holding nine parts, and the executable addresses those parts by number:

| Part | Holds | Scope |
| --- | --- | --- |
| 0 | menu and system wording | shared |
| 1 | **character names** — the target of control `0x09` | shared |
| 2 | item, weapon and armour names | shared |
| 3 | spell and skill names, debug menu labels | shared |
| 4 | **the phrase table** — the target of control `0x04` | shared |
| 5 | dialogue | per chunk |
| 6 | short on-map labels | per chunk |
| 7 | narration and event text | per chunk |
| 8 | always empty | — |

"Shared" means literally the same bytes: parts 0–4 are one table copied into
every chunk. Langrisser I has a single variant of each across all 21 chunks;
Langrisser II has two or three across 106. A translation has to write each
variant back into every chunk that carries it.

### Text codec

Confirmed from the decoder at `0x800164E8` in `LANG2.EXE`:

| Byte | Meaning |
| --- | --- |
| `0x00` | end of string |
| `0x01`–`0x09` | control codes, four of which take the next byte |
| `0x0A`–`0xF6` | glyph, slot = byte − `0x0A` (slots 0–236) |
| `0xF7`–`0xFB` | two bytes: slot = 236 + (byte − `0xF7`) × **255** + next byte |

```mips
lbu   $a0, ($a1)          ; byte
sltiu $v0, $a0, 0xa       ; < 0x0A -> control
sltiu $v0, $a0, 0xf7      ; < 0xF7 -> slot = byte - 0x0A
addiu $v1, $a0, -0xf7     ; else bank = byte - 0xF7
sll   $v0, $v1, 8
subu  $v0, $v0, $v1       ; bank * 255
addiu $a0, $a0, 0xec      ; arg + 236
addu  $a0, $v0, $a0
```

A bank is **255** slots wide, not 256, and that is worth stating loudly because
a 256-wide guess reads as plausible nonsense: the neighbour of the right
character is still a real character, and the error grows by one per bank, so one
string looks almost right while another is clearly wrong.

Because the step is 255 and the argument is a full byte, **the banks overlap at
their seams**: `0xF7 0xFF` and `0xF8 0x00` both name slot 491. The game's own
packer takes the lower bank, and a rebuilt chunk has to do the same or it
differs from the original for no reason at all.

### Control codes

Codes `0x01`–`0x09` index a jump table at `0x8001017C`:

| Code | Handler | What it does |
| --- | --- | --- |
| `0x01` | `0x80016914` | takes one byte and stores it in a global — state, not text |
| `0x02` | `0x80016938` | prints a pair from a `u16` table, advancing its own counter |
| `0x03` | `0x80016948` | prints a decimal number from a `u16` table |
| `0x04` | `0x8001689C` | takes one byte; prints string `byte` of part 4 |
| `0x05` | `0x80016968` | calls the glyph writer with `-1` — a blank |
| `0x06` | `0x80016978` | calls a hook — waits |
| `0x07` | `0x800167A4` | calls a hook and resets the cursor — new page |
| `0x08` | `0x800167DC` | advances Y by `0x10`, resets X — line break |
| `0x09` | `0x80016850` | takes one byte; prints string `byte + 2` of part 1 |

The cursor reset performed by `0x07` is caller-dependent. In the opening quiz
(`LANG1/SCEN.DAT`, chunk 20, part 5), every original internal page continues
with `0x05 0x08` (`<blank><line>`). Runtime verification shows why: without
that prefix the page appears blank. Together with the original control pattern,
this indicates that the first post-page row is outside the visible quiz window;
the exact caller-side cursor offset has not been disassembled. Other dialogue
callers begin drawing immediately after `0x07` and must not receive this prefix.
`l12_rewrap` infers such a prefix from the original records for each chunk/part
and applies it only when at least two continuations use it consistently.

`0x04` and `0x09` are one mechanism. Both call `0x80015C30(part, number)`, which
walks `number − 1` NUL-terminated strings from the start of that part and
returns a pointer; the caller then runs it as a nested stream and restores its
own pointer. This is `l45`'s macro — `F600` with an argument word — written for
a byte codec, and `0x09` is its `FBxx` dialog command.

Numbers are 1-based, so the zero-based index is `byte − 1` for `0x04` and
`byte + 1` for `0x09`.

### Part 4 is the phrase table

There is no separate macro table anywhere on the disc. Part 4 holds 239 strings,
`0x04` has 237 distinct operands, and the strings are what a compression table
looks like: `ラングリッサー`, `レイガルド帝国`, `手に入れた！`, `ごめんなさい`,
`そんなある日`, `ありがとう、`. Checked against the contexts that forced the
reading:

| Operand | Part 4 string | Reads as |
| --- | --- | --- |
| `0xD9` | `ない！` | `今度こそ負けない！`, `いけない！`, `危ない！` |
| `0xDA` | `のです` | `見つけたのですが` |
| `0x57` | `ません。` | `召喚できません。`, `装備できません。` |

About a third of the bytes in a line of dialogue are references into it. Part 1
is the same story for `0x09`: `カオス様を復活させ` comes out of it, which is the
plot of Langrisser II.

The translated build reconstructs part 4 after the shared text codec has tiled
each complete printable run. Repeated sequences of whole encoded tiles are
replaced by `0x04,index`; controls remain hard boundaries. Compressing Unicode
substrings before tiling is incorrect: a phrase boundary can split a pair glyph
and make a word render with full-cell gaps even though decoding still returns
the same letters. The packer expands every generated reference after rebuilding
the table and requires byte-for-byte equality with the pre-compression tiled
stream, so phrase compression cannot change spacing or pair alignment.

### How much text there is

Counting each shared table once rather than once per chunk:

| | Strings | Glyphs |
| --- | ---: | ---: |
| Langrisser I | 2,197 | 28,094 |
| Langrisser II | 10,364 | 216,460 |
| **Total** | **12,561** | **244,554** |

Counting the copies instead gives 150,885 strings and 1.13 million glyphs, which
is what a naive extractor reports. The duplication is 4.6× overall. For scale,
the Langrisser V pack carries 10,244 translated script records.

These are counts of stored bytes, so they under-count what a reader sees — every
`0x04` expands to a phrase — and over-count what has to be translated, since the
phrase table is written once and referenced everywhere.

## Writing it back

### There is no SYSTEM file

This engine has no counterpart to `l45`'s `SYSTEM.BIN`. The UI text lives in
`SCEN.DAT` like everything else, as parts 0–4 of each chunk's text section:
menu wording, character names, item names, spell and skill names, and the
phrase table. So "system" and "script" are one packing problem here, not two —
and a change to a shared part has to be written into every chunk that carries
that variant.

### The round trips

There are two, and a translation needs both. `--verify-text` decodes every
string to text and encodes it back:

| | Strings | Byte-identical |
| --- | ---: | ---: |
| `LANG1/SCEN.DAT` | 24,957 | **24,957** |
| `LANG2/SCEN.DAT` | 126,445 | **126,445** |

Two things make that exact rather than approximate. The plane draws some
characters twice, so a character alone does not say which slot wrote it; the
first slot is canonical and any other is written as a raw `<$XXXX>` tag, the way
`l45` writes one. And the bank seams are honoured, as above.

`--roundtrip` then reads every chunk, rebuilds it from what it read, and
compares:

| | Chunks | Byte-identical |
| --- | ---: | ---: |
| `LANG1/SCEN.DAT` | 21 | **21** |
| `LANG2/SCEN.DAT` | 106 | **106** |

Rebuilding means the text section is written afresh from its parts, the chunk's
own section table is recomputed because every section after the text one moves,
and the chunk is padded back to the length it had. Nothing is copied through
except the sections this format does not touch.

### Writing a pack back

`langrisser/l12_sceninsert.py` is the counterpart of the dump and the same
shape `sceninsert` has for `l45`: the original file is the base, the pack
supplies the records it has translated, and a record it leaves out stays as it
was, because a partial translation has to build. Records are addressed
`chunk / part / index`, which is how the dump writes them, so nothing has to
agree on a separate id scheme.

Feeding the dump straight back in reproduces both files byte for byte —
24,957 records for Langrisser I and 126,445 for Langrisser II, none of them
written, because none of them changed. Editing one record changes exactly two
bytes and the file still rebuilds and re-reads.

That works because a record is one line and breaks are tags, not real
newlines. With real newlines a break at the edge of a record is
indistinguishable from the separator after it, and the last record of every
part silently gained one.

The encoder refuses a character the plane does not have rather than dropping
it. Right now that means Cyrillic is refused: the target glyphs are not in
`FONT.DAT` yet, and building them is the next stage, not a detail of this one.

### The growth budget

Chunks start on `0x800` boundaries, so each one ends with padding that a longer
text section can eat before anything has to move:

| | Chunks | Padding | Median per chunk |
| --- | ---: | ---: | ---: |
| `LANG1/SCEN.DAT` | 21 | 24,233 bytes | 1,203 |
| `LANG2/SCEN.DAT` | 106 | 120,805 bytes | 1,101 |

About a kilobyte per chunk is the first local budget. When a chunk needs more,
the shared fixed-size container rebuilder gives it another sector, rewrites the
catalog and reclaims trailing sectors from later chunks. Every chunk remains
`0x800`-aligned and the file-level size stays unchanged; the build refuses if
the complete set no longer fits.

Two things make the budget go further than it looks. The phrase table is one
indirection the target text can use as well: a Russian ending or a recurring
name costs a two-byte reference per use once it is in part 4. And parts 0–4 are
shared, so translating them is paid for once per variant, not once per chunk.

## How the target text will use the plane

Same two mechanisms Langrisser V already builds, and for the same reason — a
slot is one 12 × 12 cell whatever is drawn in it:

- **Menu and interface**: single letters, mostly capitals, one per slot, centred
  in the cell. This is what the game already does with `ＡＴ`, `ＤＦ`, `ＭＰ`,
  `ＭＶ`, and what `build_font` renders for Langrisser V. All-caps runs of at
  least three letters use these full-size singles automatically. Two-letter
  labels listed in a language pack's `fullwidth_units` (Russian `АТ`, `ЗЩ`,
  `MP`) do the same; neither letter may be folded into a pair with the adjacent
  space or punctuation. Intentionally compact controls such as `ОК` remain
  ordinary pair glyphs.
- **Everything else** — dialogue, inscriptions, monster cries: two target
  letters packed into one cell, the compact pair glyphs `build_font` already
  generates.

The slot budget is tight: 34 blank slots, and the 130 the script never names are
mostly credit surnames that something outside `SCEN.DAT` still draws.

## Where this lives in the project

Nothing here needed a new concept — the disc fits the axes the repository
already has:

| Path | What it holds |
| --- | --- |
| `data/engines/l12/manifest.json` | the container family: glyph cell, container list, codec |
| `data/games/l1/manifest.json`, `data/games/l2/manifest.json` | one per game, both naming engine `l12` and the shared font map |
| `data/releases/l1-2-ps1-jp/manifest.json` | the disc: two games, their roots and boot files, and the Redump fingerprint of its data track |
| `data/common/font_mapping/l1_2_font_map.csv` | the slot map, in `common/` because the two `FONT.DAT` files are byte-identical |

The tools take `--game` and `--release` like the rest, so `l12_scen --game l1`
resolves its script, its slot map and its disc from the manifests.

`langrisser/l12_scen.py` carries only what differs from `l45`: the section
table, the nine parts, the byte codec and the control codes. The chunk pointer
table comes from `scen.read_chunk_spans` and the slot map from
`scen.load_charmap_csv`; `langrisser/l12_review_html.py` builds its styling on
`review_html`'s.

## Still open

- **Chunk to scenario binding is done**, but the scenario *number* the title
  card prints comes from control `0x03`, so the card reads `SCENARIO-<number>`
  rather than a literal.
- **`0x02` and `0x03` need a width, not a table.** `0x02` prints the name the
  player entered — it is what `・<pair>の死亡` shows — and `0x03` prints a
  decimal number. Neither is text a translation writes, but both take room on
  the line, so line wrapping needs their worst case exactly as `rewrap.py`
  counts `NAME_CELLS` for `l45`'s `<$F600><$0000>`. `0x02` runs 2,960 times
  (1,911 in menus, 611 in dialogue) and `0x03` 516 times (399 in menus).
- **`0x01` never appears.** Not once in either script. Its handler stores a byte
  in a global and prints nothing, so there is nothing to preserve and nothing to
  measure.
- **Writing back.** The round trip is byte-identical and the in-chunk budget is
  measured, but no chunk has been grown past its sector, so moving the catalog
  is untested.
