# Langrisser I & II (PS1) Disc Format

Reverse-engineering notes for the PlayStation release of *Langrisser I & II*,
written while working out what a Russian translation would have to touch. This
is a different engine from the `l45` family that the rest of this repository
targets: the disc holds two complete games side by side, each with its own copy
of every data file.

Findings are appended as they are confirmed against the disc. Anything not
stated here is not yet proven.

Source: `iso/l1-2-ps1-jp/Langrisser I & II (Japan) (Track 1).bin`, matching the
Redump entry for `SLPM 86798 / SLPS 00897 / SLPS 01822` (data track `b2ebdb90`,
286345 sectors).

## Disc Layout

Two games, one filesystem, one boot executable:

```text
/SYSTEM.CNF              BOOT = cdrom:\SLPS_008.97;1
/SLPS_008.97   118,784   loader
/LANG1.EXE     456,704   Langrisser I
/LANG2.EXE     458,752   Langrisser II
/MOVIE.EXE     126,976   full-motion video player
/MOVIE/*.STR             22 MDEC streams, 340 MB total
```

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

**Confirmed.** `LANG1/FONT.DAT` and `LANG2/FONT.DAT` are byte-identical, so both
games draw from one glyph plane.

| Property | Value |
| --- | --- |
| File size | `27,648` bytes |
| Glyph size | 12 × 12 pixels |
| Depth | 1 bit per pixel, MSB first, no padding between rows |
| Bytes per glyph | 18 (144 bits, used exactly) |
| Slots | 1536 |

This is the same glyph geometry as Langrisser V's `SYSTEM.BIN` font — 12 × 12
one-bit cells in 18-byte slots — so the renderer that builds target glyphs for
`l45` produces cells of the right shape for this game too. What differs is
where they live: a whole file here, a region of the executable's data there.

Rendered contact sheet (regenerate any time; `work/` is not tracked):

```bash
python3 -m langrisser.iso_mode2 "$L12BIN" extract /LANG1/FONT.DAT work/l1-2/extracted/LANG1.FONT.DAT
```

### Slot order

The plane is not in any standard code order. It opens with a fixed syllabary
block and then becomes a pool:

| Slots | Contents |
| --- | --- |
| `0`–`44` | katakana, ア through ロ |
| `45`–`68` | voiced and semi-voiced katakana, ガ through ポ |
| `69` | ヴ |
| `70`–`73` | ！ ？ ・ ー |
| `74`–`82` | small katakana ァィゥェォャュョッ |
| `84`–`94` | full-width digits, `１`–`０`, then `Ｄ` |
| `95`+ | mixed hiragana, kanji, Latin letters and punctuation |

From slot 95 on the plane is a pool with no character-code order: laid out in
sequence it reads as fragments of running wording, so it was clearly built from
some text in the order that text needed glyphs. It is **not** `SCEN.DAT`'s
order — walking both scripts and recording where each slot is first named puts
the appearances in increasing order only 54% of the time, which is chance. So
the pool follows something else, and a slot number carries no meaning outside
this disc.

The correspondence between slot and character therefore has to come from the
plane itself, as `data/games/l4/font_map.csv` did.

The last slots hold the ◯ and ✕ controller-button glyphs.

### The plane is this game's own

**Confirmed, and it is a negative result worth recording.** The cell format is
identical to the `l45` engine's — `data/engines/l45/manifest.json` describes
exactly these 12 × 12 one-bit cells packed 12 bits per row MSB-first into 18
bytes, slot N at N × 18 — but the artwork is not shared. Comparing every tile of
`FONT.DAT` against Langrisser V's plane in `SYSTEM.BIN`, at every byte
alignment, matches 35 tiles of 1536, and 34 of those are blank.

The slot order differs as well: Langrisser V's plane opens with punctuation,
digits and Latin letters, this one with katakana.

So `langrisser/derive_font_map.py`, which recovers a map by finding a
byte-identical tile in an already-mapped plane, has nothing to inherit from
here. The slot→character map for this disc has to be built the way Langrisser
V's reference map was built in the first place: read off the rendered plane.
The renderer and the CSV convention still apply.

## SCEN.DAT — the script container

### Catalog

**Confirmed.** The file opens with a `u32` little-endian pointer table; the last
pointer equals the file size, so N pointers describe N−1 chunks. Every chunk
starts on a `0x800` boundary — one CD sector — and the first pointer is `0x800`,
so the catalog owns the first sector.

| | `LANG1/SCEN.DAT` | `LANG2/SCEN.DAT` |
| --- | ---: | ---: |
| File size | `530,432` | `2,232,320` |
| Chunks | 21 | 107 |

This is the same catalog Langrisser V uses — `langrisser/scen.py` documents
`u32 chunk_pointers[]` with the last equal to the file size. Below it, the two
formats part company.

### Chunk interior

**Confirmed.** A chunk begins with its own `u32` section table, and the table's
first entry is the table's own byte size, which is what makes it
self-describing. Section counts vary by chunk (7, 9, 10, 13, 16 and 46 were all
observed); most sections hold packed art or code.

**Section 2 is the text**, in every chunk that has one — 21 of 21 in Langrisser
I, 106 of 107 in Langrisser II. It is itself a table in the same self-describing
form, and it holds nine parts with stable roles:

| Part | Holds | Scope |
| --- | --- | --- |
| 0 | menu and system wording | shared |
| 1 | character names | shared |
| 2 | item, weapon and armour names | shared |
| 3 | spell and skill names, labels | shared |
| 4 | scenario and place names | shared |
| 5 | dialogue | per chunk |
| 6 | short on-map labels | per chunk |
| 7 | narration and event text | per chunk |
| 8 | always empty | — |

"Shared" means literally the same bytes: parts 0–4 are one table copied into
every chunk. Langrisser I has a single variant of each across all 21 chunks;
Langrisser II has two or three across 106. A translation has to write each
variant back into every chunk that carries it.

### Text codec

**Confirmed** by decoding the character-name table, which comes out as the cast
of both games in order, and by every escape bank landing inside the font.

| Byte | Meaning |
| --- | --- |
| `0x00` | end of string |
| `0x01`–`0x09` | control codes; some take the next byte as an operand — see below |
| `0x0A`–`0xF6` | glyph, slot = byte − `0x0A` (slots 0–236) |
| `0xF7`–`0xFB` | two bytes: slot = 236 + (byte − `0xF7`) × **255** + next byte (slots 236–1510) |

The five escape banks tile the font: the highest slot any script names is 1504,
and `0xFC`–`0xFF` never appear in a text part, which is the check that says the
banks stop at `0xFB`.

**A bank is 255 slots wide, not 256, and that only became visible once the
font map was trustworthy.** With a 256 step the plane reads as plausible
nonsense, because the neighbour of the right character is still a real
character; the error grows by one per bank, so one string can look almost right
while another is clearly wrong. With 255 the same bytes read as Japanese
throughout — the debug menu comes out `面セレクト`, `ＢＧＭ`, `効果音`,
`戦闘テスト`, and place names as `レイガルド帝国`, `ヴェルゼリア城`.

The check that settles it: no argument byte ever reaches `0xFF` in either
script. The highest seen are `0xFB` after `0xF7`, `0xFE` after `0xF8`, `0xFD`
after `0xFB` — exactly what a 255-wide bank allows and a 256-wide one would
not explain.

## What the script names, and what it does not

Of the 1502 drawn slots the script names **1372**; **130** it never names.

Latin is complete: all 26 capitals are present and in use, plus two lower-case
letters (`ｍ`, `ｚ`) and the ten digits. It is not decoration — the game writes
real Latin words with it: `ＧＡＭＥＯＶＥＲ`, `ウィンドウＯＰＥＮ`, `ＮＰＣ`,
`ＢＧＭ`, `Ｆ．Ａ．Ｉインターナショナル`, and monster cries like
`ＧＵＷＡＡＡＡＡ！` and `Ｚｚｚｚｚｚ`. It also carries the stat abbreviations
`ＡＴ`, `ＤＦ`, `ＭＰ`, `ＭＶ` with `＋`, `－`, `×`, `（`, `）` —
`ＡＴ＋８・ＤＦ－３`, `ＭＰ×２・魔法射程＋３`, `ＭＶ＋２（部下含）`.

Two slots are icons rather than characters: **1502 `〇` and 1503 `✕`**, the
controller buttons. Both are drawn in a heavy stroke unlike anything else on the
plane, and neither is named by either script even once — while the thin `×` at
slot 275, which looks similar at a glance, is ordinary text used 375 times.

The other 128 unnamed slots are **not** free. Most are the surname kanji at
1408–1471 — `澤`, `樋`, `鈴`, `薮`, `眞`, `鮎`, `鶴`, `嶋`, `篤` — which the
staff credits draw from somewhere other than `SCEN.DAT`. Before any slot is
treated as reusable, whatever else draws text has to be checked; `SCEN.DAT`
silence alone does not make a slot free.

Control-code frequency over both scripts, and what follows each one:

| Code | Count | Distinct next bytes | Reading |
| --- | ---: | ---: | --- |
| `0x01` | 508 | 30 | operand |
| `0x02` | 3,090 | 33 | operand |
| `0x03` | 681 | 25 | operand |
| `0x04` | 31,641 | 239 | bare; ordinary text follows |
| `0x05` | 12,811 | 101 | bare; often doubled |
| `0x06` | 6,029 | 22 | pairs with `0x07` 5,862 times of 6,029 |
| `0x07` | 5,960 | 140 | bare |
| `0x08` | 26,422 | 190 | bare |
| `0x09` | 3,650 | 52 | operand, and a small one — the top followers are `0x01`, `0x16`, `0x0C`, `0x0B`, `0x13` |

**Some of these take an operand, and that matters more than it looks.** A code
whose next byte ranges over almost the whole alphabet is bare — real text
follows it. One whose next byte is drawn from a couple of dozen low values is
consuming that byte, and `0x09` is the clearest: decoded text puts it right
before what reads as a character name, so it is almost certainly a name
reference of the kind `l45` writes as `0xFB00`–`0xFBFF`.

Which codes take an operand is not yet proven, only indicated, and it has to be
settled before the text can be read — an operand byte decoded as a glyph is a
character that was never on screen.

Against `l45`: that engine reads `u16` tokens, treats everything below `0xE000`
as a glyph index and ends a record with `0xFFFx`. This one is byte-oriented with
banked escapes and a `0x00` terminator. The token layer needs its own
implementation; only the catalog above it and the glyph geometry below it carry
over.

### How much text there is

**Confirmed**, counting each shared table once rather than once per chunk:

| | Strings | Glyphs |
| --- | ---: | ---: |
| Langrisser I | 2,197 | 28,094 |
| Langrisser II | 10,364 | 216,460 |
| **Total** | **12,561** | **244,554** |

Counting the copies instead gives 150,885 strings and 1.13 million glyphs, which
is what a naive extractor would report. The duplication is 4.6× overall.

For scale, the Langrisser V pack carries 10,244 translated script records, so
the two games together are somewhat larger than the project's current corpus.

## Reading the plane

The slot→character map is the one piece a translation cannot start without, and
it is not finished. What is measured so far, all against the 84 kana slots whose
characters are known from the plane itself and confirmed by the character-name
table decoding as the cast of both games:

| Method | Correct |
| --- | --- |
| Template match against PixelMplus, whole L5 charset as candidates | 34 / 84 |
| Tesseract `jpn`, `--psm 10`, one tile at a time | 8 / 84 |
| PaddleOCR `PP-OCRv5_server_rec`, one tile at a time | 23 / 84 |
| PaddleOCR, tiles rendered as a line of 12 | 58 / 84 |

Two things that read as obvious are wrong. Template matching against a modern
12-pixel Japanese font does not work: this font draws in an 11 × 11 box at
offset (0, 1), a modern one fills 12 × 12, and a one-pixel stroke shift is
already a larger distance than a genuinely different character. And smoothing
the enlarged tiles hurts rather than helps — bicubic enlargement reads 51 of 84
where nearest reads 48, but blurring on top drops it to 36 at radius 1.5 and 12
at radius 5.

What does work is context. A tile on its own is unreadable in principle at this
size: ア and 了, エ and 工, カ and 力, ヌ and 又 are the same drawing, and a
per-tile recogniser answers with the wrong script about as often as the right
one — every single-tile miss above is a homoglyph, not a failure to see the
shape. Rendering the game's own strings back as lines and letting each position
vote is what disambiguates them, exactly as it does for a human reader.

`langrisser/recognize_glyph_plane.py` does that: it walks the script, renders
each distinct string as a line of tiles, recognises the line, and counts a slot
confirmed when at least three lines name it and at least four in five agree.

### Where the passes got to

Seeded with the 84 kana and run over both scripts. The enlargement is the whole
difference between the two runs — same lines, same model, same thresholds:

| Enlargement | Lines aligned | Slots confirmed |
| --- | ---: | ---: |
| Nearest-neighbour | 16,613 of 20,977 | 1,032 of 1,536 |
| xBRZ ×6 | 19,486 of 20,977 | 1,075 of 1,536 |

A line is only counted when the recogniser returns exactly as many characters
as it was given, so the jump from 16,613 to 19,486 is the recogniser losing its
place far less often. The two maps agree on 992 slots and disagree on 1.

**The kana are right and the kanji are not**, in both. Decoding the item and
character tables returns them cleanly — `エルウィン`, `ナイフ`,
`ウォーハンマー`, `グレートソード`, `ラングリッサー` — and dialogue comes back
half-readable: `ハァ、ハァ……ついに、`, `アッハハハハハハハハハハッ！` sit next
to kanji that form no word.

The cause is now visible in that same decode, and it is not the recogniser: the
control codes above appear *inside* words, in places a character belongs. Their
operand bytes were rendered as glyphs and fed to the model as part of the line,
so every line carrying one taught it a character that was never there. Reading
the plane cannot get much further until the operands are settled.

### What the next pass needs

- The control-code operands settled first, so no line contains a glyph that is
  not a glyph.
- A candidate set per slot rather than one answer, so a wrong homoglyph can be
  overridden without re-recognising.
- Agreement across *different* words weighted above repetition of the same one:
  a homoglyph is wrong the same way every time, so repetition confirms it.
- A pass that reads the decoded text as Japanese and corrects what does not form
  words — which is what caught the Langrisser V errors, and what its map still
  records as 87 hand-fixed variant confusions and 79 context confirmations.

## The plane is fully read

All 1536 slots are accounted for: 34 are blank and **1502 carry a glyph, every
one of them read**. The map is `data/common/font_mapping/l1_2_font_map.csv`,
under `common/` because the two games share the file byte for byte.

It was read rather than recognised, and then each slot was checked against the
character rendered beside it — see the sheets `recognize_glyph_plane` and the
verification pass produce under `work/l1-2/`. Two slots were corrected
afterwards by how the script uses them, not by how they look: 818 is `Ｘ` and
not `×`, 781 is `Ｉ`.

**Read is not the same as decoded.** The map is complete, but the control-code
operands are not settled, so some bytes are still taken for glyphs that are not
glyphs. Dumping the chunks and reading them back against the original is the
check that closes this, and it needs the operands first.

## How the target text will use the plane

Same two mechanisms Langrisser V already builds, and for the same reason — a
slot is one 12 × 12 cell whatever is drawn in it:

- **Menu and interface**: single letters, mostly capitals, one per slot,
  centred in the cell. This is what the game already does with `ＡＴ`, `ＤＦ`,
  `ＭＰ`, `ＭＶ`, and what `build_font` renders for Langrisser V.
- **Everything else** — dialogue, inscriptions, monster cries: two target
  letters packed into one cell, the compact pair glyphs `build_font` already
  generates.

What this costs is slots, and the plane has few to give: 34 blank ones, and the
130 the script never names are mostly the staff-credit surnames, which
something outside `SCEN.DAT` still draws. The budget is a question for after
the operands, not before.
