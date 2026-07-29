# Saturn Disc Format

This document records the current reverse-engineering status for the Sega
Saturn release of Langrisser V. It is intentionally separate from the PS1
format documents: the existing production patcher targets the PlayStation disc,
while the Saturn disc shares some content concepts but uses different physical
tracks, filenames, on-disc word order and executable code.

## Current Status

Working target image:

| Path | Size | CRC32 | SHA-1 prefix |
| --- | ---: | --- | --- |
| `iso/saturn/LANGRISSER_5.bin` | `507074736` | `5E63C92F` | `8ef76305fe27` |

The image is a Sega Saturn mixed-mode disc. Sector 0 user data starts with:

```text
SEGA SEGASATURN SEGA TP T-25
```

The ISO9660 primary volume descriptor is on track 1 at LBA 16 and has volume id
`LANGRISSER_5`.

Terminology note: when this document says `big-endian`, it means the on-disc
byte order needed to read multi-byte fields and `u16` tokens correctly. This is
equivalent to treating the relevant resource words as byte-swapped relative to
the existing PS1 little-endian tooling; it does not prove the Saturn runtime
uses big-endian memory access for these structures.

Read-only tooling added for this investigation:

| Tool | Purpose |
| --- | --- |
| `langrisser/saturn_disc.py` | Parse/extract/remaster the Saturn mixed-mode BIN/CUE and summarize track-2 XA sectors |
| `langrisser/saturn_system_dump.py` | Dump Saturn `SYSTEM.DAT` text groups using the confirmed on-disc word order |
| `langrisser/saturn_scen_scan.py` | Scan Saturn `SCEN.DAT` catalog, chunk headers, record indices and token streams |
| `langrisser/saturn_scen_text.py` | Dump the full Saturn `SCEN.DAT` scenario text pool with stable `(chunk, entry)` ids |
| `langrisser/saturn_font.py` | Render Saturn `SYSTEM.DAT` glyph slots and diff them against the PS1 font |
| `langrisser/saturn_scen.py` | Shared SCEN.DAT read/rebuild model (catalog, block header, field_3c text pool) |
| `langrisser/saturn_apply.py` | Apply the universal language-pack translation to the Saturn SCEN text pool |
| `langrisser/saturn_reconcile.py` | One-off: compare this release's originals against another's and record the script/SYSTEM correspondence into `data/` |
| `langrisser/saturn_system_pack.py` | Pack the SYSTEM UI translation into the Saturn `SYSTEM.DAT` groups |
| `langrisser/saturn_system_validate.py` | Validate the packed `SYSTEM.DAT` write contract (pointer directory, group spans) |
| `langrisser/saturn_build.py` | Build-time Saturn flow: font + SYSTEM text + SCEN text + decoded graphics |
| `langrisser/saturn_poem_translate.py` | Re-pack the shared prologue-poem render into `OPEN.DAT[2]` VDP1 runs |
| `langrisser/saturn_now_loading.py` | Re-pack the Saturn compressed `SYSTEM.DAT` Now Loading plate |
| `langrisser/saturn_name_entry.py` | Patch the Saturn name-entry display grid and input table in `SYSTEM.DAT` |
| `langrisser/saturn_apply_text_overrides.py` | Normalize the build copies with the platform record overrides (Saturn pad buttons etc.) |
| `langrisser/saturn_fix_native_glyphs.py` | Plan/apply the native symbol remap onto the real Saturn glyph slots |
| `langrisser/saturn_title_credits.py` | Stamp the translator credits onto the `TITLE1`/`TITLE2` overlay tilemaps |

The Saturn tools share the platform-agnostic core: `langrisser.binfmt` (byte order),
`langrisser.offsetgroups` (the SYSTEM group model), `langrisser.build_font` (glyph slot
rewrite), `langrisser.poem_render` (poem text rasterisation/layout), and the PS1
token codec/dump loader, so no common logic is duplicated between the PS1 and
Saturn tooling.

### Build-time platform selection

Platform is chosen at build time; the `data/games/<game>/lang/<code>` pack is unchanged.
`langrisser/saturn_build.py` runs the reused stages against the extracted Saturn
files:

```bash
python3 -m langrisser.saturn_disc extract SYSTEM.DAT work/l5/build/saturn/SYSTEM.DAT
python3 -m langrisser.saturn_disc extract SCEN.DAT   work/l5/build/saturn/SCEN.DAT
python3 -m langrisser.saturn_build --lang ru \
  --assignments work/l5/build/font_slot_assignments.ru.csv
```

The stages, all reusing shared logic:

- `langrisser.build_font` writes the Cyrillic alphabet into `SYSTEM.DAT` glyph slots
  `0..1820` (text region untouched) and emits the `.tbl`.
- `langrisser.saturn_system_pack` rebuilds the SYSTEM UI groups with the translated
  text via the shared group model (BE), using
  `data/releases/l5-saturn-jp/system_mapping.json` for direct PS1 ids, sparse
  platform overlays, and verified preserved entries. It packs all 16/16 groups.
- `langrisser.saturn_apply` inserts translated scenario text through
  `data/releases/l5-saturn-jp/scen_mapping.json`: 125/131 SCEN blocks are translated,
  and the 6 service/name-pool blocks are explicitly preserved.
- Graphic steps run when the corresponding Saturn files are extracted:
  `CLEAR.DAT` scenario-clear banner, `TITLE1.DAT` title credits, and
  `OPEN.DAT[2]` prologue poem. The `Now Loading` plate is part of
  `SYSTEM.DAT`, so it is patched immediately after the SYSTEM text packer.
- With `--remaster-disc`, the build writes a translated mixed-mode BIN/CUE under
  `work/l5/build/saturn/`.

Remaining before release-grade Saturn parity: runtime-checking the statically
patched Saturn-only screens and the remastered BIN/CUE.

Generated investigation output lives under `work/l5/build/saturn/` and is not
tracked.

## Track Layout

From `iso/saturn/LANGRISSER_5.cue`:

| Track | Mode | INDEX 01 | LBA | Raw byte offset | Notes |
| --- | --- | --- | ---: | ---: | --- |
| 1 | `MODE1/2352` | `00:00:00` | `0` | `0` | Saturn boot sector and ISO9660 filesystem |
| 2 | `MODE2/2352` | `13:45:26` | `61901` | `145591152` | CD-XA/ADPCM payload sectors |
| 3 | `AUDIO` | `39:18:12` | `176862` | `415979424` | CD audio |
| 4 | `AUDIO` | `39:47:43` | `179068` | `421167936` | CD audio |
| 5 | `AUDIO` | `41:12:67` | `185467` | `436218384` | CD audio |
| 6 | `AUDIO` | `46:30:57` | `209307` | `492290064` | CD audio |

Confirmed sector user-data offsets:

| Mode | Raw sector size | User offset | User size used here |
| --- | ---: | ---: | ---: |
| `MODE1/2352` | `2352` | `16` | `2048` |
| `MODE2/2352` XA audio | `2352` | raw sector required | Form2 audio sectors, keep subheaders |

The current PS1 `langrisser/iso_mode2.py` assumes a single `MODE2/2352` data track
with 2048-byte user data at offset 24. It is not suitable for Saturn track 1
or track 2 without a new track-aware backend.

## Track 1 ISO9660 Root

Track 1 is a normal ISO9660 filesystem. Root directory:

| Type | LBA | Size | Name |
| --- | ---: | ---: | --- |
| file | `214` | `232660` | `A0LANG5.BIN` |
| dir | `21` | `4096` | `ADPCM` |
| file | `27603` | `710656` | `ALLUSB.BIN` |
| file | `27950` | `761856` | `ALLUSW.BIN` |
| file | `35809` | `198656` | `BAR.BIN` |
| file | `36380` | `7555072` | `BGM.BIN` |
| file | `31761` | `8290304` | `BTLBG.BIN` |
| file | `28340` | `83604` | `BTLDAT.BIN` |
| file | `28381` | `6922240` | `BTLEFF.BIN` |
| file | `41306` | `242700` | `CAST.DAT` |
| file | `41296` | `18808` | `CLEAR.DAT` |
| file | `35906` | `15968` | `CUR.DAT` |
| file | `55666` | `721` | `GRAPHIC.DOC` |
| file | `55667` | `12613411` | `GRAPHIC.LZH` |
| file | `212` | `19` | `L5_ABS.TXT` |
| file | `213` | `480` | `L5_BIB.TXT` |
| file | `211` | `32` | `L5_CPY.TXT` |
| file | `41977` | `28000948` | `LANG5.CPK` |
| file | `35939` | `720896` | `MAG.BIN` |
| file | `40271` | `2099200` | `MAGSND.BIN` |
| file | `676` | `5474304` | `MAP.DAT` |
| file | `3349` | `26744832` | `MAP_C.DAT` |
| file | `28322` | `35860` | `MRCUSW.BIN` |
| file | `55651` | `28926` | `OMAKE.LZH` |
| file | `41685` | `351404` | `OPEN.DAT` |
| file | `328` | `508908` | `PROG1.BIN` |
| file | `577` | `78548` | `PROG2.BIN` |
| file | `55650` | `1542` | `READ_ME.TXT` |
| file | `16408` | `22910976` | `SCEN.DAT` |
| file | `35914` | `50736` | `SHOP.DAT` |
| file | `36295` | `36630` | `SND00.BIN` |
| file | `27595` | `15000` | `SNDDEB.BIN` |
| file | `36313` | `135220` | `SND_DAT.BIN` |
| file | `41425` | `531136` | `STAFF.DAT` |
| file | `616` | `121344` | `SYSTEM.DAT` |
| file | `41857` | `123944` | `TITLE1.DAT` |
| file | `41918` | `118888` | `TITLE2.DAT` |
| file | `40069` | `413696` | `TK_SC.BIN` |
| file | `36291` | `8192` | `WD_FONT.BIN` |

## Track 2 XA / ADPCM Area

Track 2 is not an ISO9660 volume. It is a contiguous XA/ADPCM sector area.

Confirmed over the useful payload range:

| Property | Value |
| --- | --- |
| Physical track start | LBA `61901` |
| First referenced XA logical extent | LBA `62126` |
| Logical-to-physical correction | subtract `225` sectors (`00:03:00` pregap) |
| Last referenced physical sector end | LBA `176712` |
| Referenced sectors | `114811` |
| XA files indexed from ISO | `6068` |
| ADPCM directories | `40` (`DIR_00` through `DIR_38`, plus `DIR_VAM`) |

The ISO `ADPCM/` directory entries point into track 2. Their extent LBA is a
logical value that includes the 225-sector pregap. To read from the raw BIN:

```text
physical_raw_sector_lba = iso_directory_extent_lba - 225
```

Examples:

| ISO entry | Logical LBA | Physical raw LBA | Size field | Physical first subheader |
| --- | ---: | ---: | ---: | --- |
| `ADPCM/DIR_00/P001.XA` | `62126` | `61901` | `69248` | `0000640400006404` |
| `ADPCM/DIR_00/P002.XA` | `62160` | `61935` | `31488` | `0000640400006404` |
| Last referenced sector | `176936` | `176711` | n/a | `0000e4040000e404` |

Subheader classification for the referenced track 2 area:

| Subheader | Meaning | Count |
| --- | --- | ---: |
| `0000640400006404` | XA audio Form2 sector | `107556` |
| `0000e4040000e404` | XA audio Form2 sector with EOF flag | `6067` |
| `0000640500006405` | XA audio Form2 sector, coding byte `0x05` | `1187` |
| `0000e4050000e405` | EOF variant for coding byte `0x05` | `1` |

The number of EOF sectors matches the number of `.XA` files (`6068`), which
confirms the directory-to-track mapping.

Reproducible commands:

```bash
python3 -m langrisser.saturn_disc info
python3 -m langrisser.saturn_disc list --json > work/l5/build/saturn/iso_entries.json
python3 -m langrisser.saturn_disc xainfo > work/l5/build/saturn/xa_info.json
```

## Key File Mapping vs PS1

The Saturn disc is not a filename-level drop-in replacement for the PS1 disc.

| PS1 path / concept | PS1 size | Saturn counterpart | Saturn size | Status |
| --- | ---: | --- | ---: | --- |
| `/L5/SCEN.DAT` | `23480320` | `SCEN.DAT` | `22910976` | Related content, different container/index |
| `/L5/SCEN2.DAT` | `23463936` | none found | n/a | PS1 duplicate absent |
| `/L5/SYSTEM.BIN` | `98320` | `SYSTEM.DAT` | `121344` | Same text-table concept, swapped-word/on-disc BE and shifted |
| `/L5/IMG.DAT` | `3233792` | `TITLE1.DAT`, `TITLE2.DAT`, `OPEN.DAT`, etc. | varies | Different asset containers |
| `/SLPS_018.19` | `833536` | `A0LANG5.BIN`, `PROG1.BIN`, `PROG2.BIN` | varies | Different CPU/code platform |
| `/L5/VOICE.PAC`, `/L5/XA.PAC` | varies | `ADPCM/**/*.XA`, track 2 | `6068` clips | Different physical storage |

## `SYSTEM.DAT`

Confirmed:

- `SYSTEM.DAT` begins similarly to PS1 `SYSTEM.BIN` but diverges at offset
  `0x0E35`.
- The PS1 system dumper finds no groups when run unchanged because it assumes
  little-endian words and `SCAN_START = 0x8052`.
- A full scan finds 16 valid offset-table groups when multi-byte words are
  read in the confirmed swapped/on-disc BE order.
- The string token ids decode with the existing Japanese glyph table.

Group table summary:

| Group | Table offset | Entries | String base | End |
| ---: | ---: | ---: | ---: | ---: |
| 0 | `0x08084` | `292` | `0x082CC` | `0x09002` |
| 1 | `0x09004` | `44` | `0x0905C` | `0x094A6` |
| 2 | `0x094A8` | `190` | `0x09624` | `0x09FC6` |
| 3 | `0x09FC8` | `130` | `0x0A0CC` | `0x0A744` |
| 4 | `0x0A744` | `20` | `0x0A76C` | `0x0A854` |
| 5 | `0x0A854` | `33` | `0x0A896` | `0x0A992` |
| 6 | `0x0A994` | `65` | `0x0AA16` | `0x0AD22` |
| 7 | `0x0AD24` | `25` | `0x0AD56` | `0x0AE70` |
| 8 | `0x0AE70` | `96` | `0x0AF30` | `0x0B42E` |
| 9 | `0x0B430` | `330` | `0x0B6C4` | `0x0C624` |
| 10 | `0x0C624` | `330` | `0x0C8B8` | `0x0D764` |
| 11 | `0x0D764` | `252` | `0x0D95C` | `0x0FC08` |
| 12 | `0x0FC08` | `376` | `0x0FEF8` | `0x13790` |
| 13 | `0x13790` | `228` | `0x13958` | `0x15938` |
| 14 | `0x15A3C` | `140` | `0x15B54` | `0x16D3A` |
| 15 | `0x16D3C` | `88` | `0x16DEC` | `0x178F4` |

Total grouped strings found: `2639`.

### Group pointer directory — `SYSTEM.DAT + 0x8000`

The runtime does not scan for the groups: `SYSTEM.DAT` carries a pointer
directory at file offset `0x8000..0x8084`, immediately before group 0. It holds
one big-endian `u32` pair per group — the group's offset-table address and its
string-base address — pre-relocated for the fixed load base `0x00200000`
(`00208084 002082CC 00209004 0020905C …`), plus one extra pointer `0x00215938`
at `+0x8070` (the non-group blob between groups 13 and 14). No group-table
pointers exist in `PROG1.BIN`/`PROG2.BIN`/`A0LANG5.BIN`; the directory is the
addressing mechanism.

Two hard consequences for the build:

- groups must stay at their original offsets (the fixed-size in-place repack
  already guarantees this), and
- **nothing may ever write into `0x8000..0x8084`**. The font glyph plane ends
  right below it: slot `1819` ends exactly at `0x7FF8`, slot `1820` would cross
  `0x8000` and overwrite the group 0/1 pointers. This is not hypothetical: an
  early build assigned Cyrillic pairs up to slot 1820, clobbered the group 0
  table/base pointers and half of the group 1 table pointer, and the game
  booted to an empty start menu and hung after the intro quiz reading garbage
  offset tables. Hence `max_font_slot: 1819` in
  `data/releases/l5-saturn-jp/manifest.json` — the ceiling follows this
  build's own file layout, not the console — and the final
  `langrisser/saturn_system_validate.py` write-contract check in
  `langrisser/saturn_build.py` (directory byte-identical, groups unmoved, every
  write inside the glyph plane / group spans / Now Loading stream budget /
  name-entry input table).

Example decoded strings using the confirmed on-disc `u16` token order:

```text
table 0x08084 index 0: AT
table 0x08084 index 1: A+
table 0x08084 index 2: DF
table 0x08084 index 6: 体力
table 0x094A8 index 0: ファイター
table 0x09FC8 index 0: ソルジャー
```

### Correspondence to PS1 `SYSTEM.BIN`

The 16 Saturn groups map onto the 16 PS1 `SYSTEM.BIN` groups in the same order:

| Property | PS1 `SYSTEM.BIN` | Saturn `SYSTEM.DAT` |
| --- | ---: | ---: |
| Groups | `16` | `16` |
| Strings | `2620` | `2639` |

Per-group entry counts are identical for `14 / 16` groups; only group 0
(`292` vs `272`), group 1 (`44` vs `41`) and group 15 (`88` vs `92`) differ
slightly. Content aligns by index: in the unit-name group (Saturn `0x09FC8` /
PS1 `0x0A060`, both 130 entries), `127 / 130` strings are byte-identical, and
the only 3 differences (`較者` vs `聖者`) are the same kanji glyph-slot
reordering on an otherwise identical string.

This means the existing PS1 SYSTEM translation (unit/class names, menu labels,
descriptions) ports to Saturn by group+index alignment, exactly like the SCEN
text pool ports by `(chunk, entry)` alignment.

Implication for tooling: `langrisser/system_dump.py` / `langrisser/system_pack.py` can be
ported by adding an endian-aware mode and Saturn scan start/table offsets. The
PS1 runtime proof about `SYSTEM.BIN` index addressing does not automatically
apply to Saturn; the Saturn executable must be checked before allowing repack.

Reproducible command:

```bash
python3 -m langrisser.saturn_disc extract SYSTEM.DAT work/l5/build/saturn/SYSTEM.DAT
python3 -m langrisser.saturn_system_dump \
  --system work/l5/build/saturn/SYSTEM.DAT \
  --out work/l5/build/saturn/system_strings.json
```

## `SCEN.DAT`

Confirmed:

- Size: `22910976`.
- Starts with a swapped-word / big-endian-on-disc top-level catalog. This
  describes byte order in the file, not a claim about the Saturn CPU runtime
  endianness.
- First word is `0x00000083`, the catalog entry count (`131`).
- The next `131` entries parse cleanly as `(u32 start_sector, u32 used_size)`.
  `start_sector` is relative to `SCEN.DAT` and uses `0x800`-byte sectors;
  `used_size` is the byte length of the block before zero padding:

```text
00000083
00000001 00009BD8
00000015 00021ADC
00000059 0001B130
00000090 0001DC58
...
```

Properties of this top-level table:

| Property | Value |
| --- | --- |
| Entry count | `131` |
| Pair size | `8` bytes |
| Catalog table size | `0x41C` |
| First payload start | `0x000800` |
| First payload padding after catalog | `0x3E4` bytes |
| `start_sector` order | ascending |
| Allocation unit | `0x800` bytes |
| `used_size <= allocated_size` | all `131` entries |
| Sector padding | all zero |

Payload block layout begins with a fixed `0x30`-byte header. The header values
are also stored in swapped-word / big-endian-on-disc order:

```text
u32 resource_table_offset
u16 category              # observed values: 1 and 6
u16 sub_id
u32 section0_offset       # always 0x30 in the current dump
u32 section1_offset
u32 record_index_offset
u32 resource_map_offset
u32 field_18              # unknown
u32 field_1c              # unknown flags/metadata
u32 field_20              # unknown
u32 field_24              # unknown
u32 field_28              # unknown
u32 field_2c              # unknown
```

The section offsets are valid for all `131` blocks and are ordered as:

```text
section0_offset <= section1_offset <= record_index_offset
  <= resource_map_offset <= resource_table_offset <= used_size
```

The `record_index` section is now confirmed:

```text
u32 record_count
repeat record_count:
    u32 record_id
    u32 record_offset      # relative to the start of this payload block
```

Validation over all payload blocks:

| Property | Value |
| --- | --- |
| Valid block headers | `131 / 131` |
| Valid record-index tables | `131 / 131` |
| Total indexed records | `1820` |
| Category counts | `1: 68`, `6: 63` |
| Empty record-index tables | `4` |
| Non-empty first record starts after table | `127 / 127` |
| All record offsets are in-range | `131 / 131` |

Record offsets are not globally sorted and may repeat, so record span recovery
must sort unique offsets inside each block and use `resource_map_offset` as the
end of the record payload area.

The `resource_map` section is confirmed as fixed 4-byte rows:

```text
repeat until resource_table_offset:
    u16 record_id
    u8  resource_slot
    u8  variant
```

Validation over all payload blocks:

| Property | Value |
| --- | --- |
| Valid resource-map sections | `131 / 131` |
| Total rows | `1362` |
| Row size | `4` bytes |
| Observed resource slots | `0..15` |
| Observed variants | `0`, `1`, `2`, `3` |
| Non-empty chunks with every indexed non-`FFFF` record id mapped | `51 / 127` |

Slot distribution:

```text
0:130 1:133 2:140 3:126 4:124 5:140 6:141 7:98
8:100 9:81 10:55 11:40 12:23 13:16 14:10 15:5
```

Variant distribution:

```text
0:1128 1:147 2:86 3:1
```

This section does not list every `record_index` record. It appears to map only
records that need a local resource/string association. Slots `14` and `15` are
real, so tooling must not hardcode a maximum slot of `13`.

The `resource_table` section is partially decoded. The first 14 `u32` values
are sorted offsets inside the same section and are in-range for all `131`
blocks. The next stable fields are:

```text
u32 first_14_offsets[14]
u32 field_38          # observed values 6..16, meaning unknown
u32 field_3c          # offset to the local index table described below
u32 field_40          # often FFFFFFFF, but not always a sentinel
```

`field_3c` points to a local indexed table inside `resource_table`:

```text
u32 total_size
u16 entry_offsets[count]
byte/word payload entries...
```

`count` is derived from the first offset:

```text
count = (entry_offsets[0] - 4) / 2
```

Validation over all payload blocks:

| Property | Value |
| --- | --- |
| Valid local index tables | `131 / 131` |
| Total indexed entries | `10071` |
| Entry count range per block | `8..372` |
| Local table `total_size` range | `100..25484` bytes |
| Entries whose span contains `FFFF`/`FFFE` terminator | `10068 / 10071` |

The first entries in many blocks are readable JP names when decoded with the
current token table, for example:

```text
0094 008D 00BB FFFF                  -> シグマ
00C6 00BD 009D FFFF                  -> ラムダ
008C 00C6 00C9 00A0 00A5 FFFF        -> クラレット
```

This is the scenario text pool. It is now confirmed as the store that holds
every speaker name and every dialogue/event line for the block, in reading
order, using the same broad control-word grammar as the PS1 script (`FFFC`
soft break, `FFFD`/`FFFE`/`FFFF` terminators, `FFF3`–`FFF8` inline markup,
`FB00`+speaker-byte name control). `langrisser/saturn_scen_text.py` walks all
blocks and emits every entry with a stable `(chunk_index, entry_index)` id.

| Property | Value |
| --- | --- |
| Blocks parsed | `131 / 131` |
| Total text entries | `10071` |
| Terminated (real text) entries | `10068` |
| Control-only entries | `3` (single-word `F702` in chunk `38`) |

The three `F702` entries in chunk `38` sit between shop-style confirmation
prompts (`…でよろしいですか？`) and carry no text terminator. `F702` is a
standalone control/placeholder token, not a string. Every other entry is text.

### Correspondence to the PS1 script

The Saturn text pool maps directly onto the PS1 per-chunk record text:

| Property | PS1 `SCEN.DAT` | Saturn `SCEN.DAT` |
| --- | ---: | ---: |
| Chunks | `131` | `131` |
| Text records / entries | `10244` | `10071` |

Per-chunk entry counts match exactly for `95 / 131` blocks and are within `±2`
for `111 / 131`. All differences are small and negative (Saturn keeps a few
fewer entries per block, consistent with PS1 counting trailing blank or
differently-split records). The chunk ordering is 1:1. This means the existing
PS1 translation can be ported to Saturn by `(chunk, entry)` alignment with only
minor per-block reconciliation, without first solving the record-payload or
resource grammars.

Reproducible command:

```bash
python3 -m langrisser.saturn_scen_text \
  --scen work/l5/build/saturn/SCEN.DAT \
  --out work/l5/build/saturn/scen_text.json \
  --out-csv work/l5/build/saturn/scen_text.csv
```

### Record payloads are binary resource data, not text

The `record_index` records point at the payload area between the record-index
table and `resource_map_offset`. These payloads are **not** scenario text: they
are high-entropy binary resources (tilemaps / graphics / event VM data). For
example, chunk `24` record `0` begins `4D03 1420 2000 0200 0000 0657 0000 067D`
followed by a dense binary body, and its `resource_map` rows group record ids
into `(resource_slot, variant)` sets (e.g. ids `E1..EA` → slots `5..7`,
variant `1`). Translation work does not need to decode this area; all
translatable strings are in the `field_3c` text pool above.

Important negative facts:

- `resource_slot` is not proven to be a direct index into only the first 14
  offsets, because slots `14` and `15` exist.
- `field_40` must not be treated as a mandatory `FFFFFFFF` sentinel; many
  chunks contain values shaped like token data at that position.
- The exact relationship between `resource_map` rows, `record_index` payloads
  and `resource_table` local index entries is still open.

The PS1 text-block locator does not work:

- No little-endian PS1-style text blocks found in the first 1 MiB.
- No swapped-word/on-disc BE PS1-style text blocks found in the first 1 MiB.

However, scenario text/token streams are present. A read-only scan of
`0x000000..0x060000` finds `619` swapped-word/on-disc BE token-stream
candidates.
Inside payload/resource regions there are clear script tokens using the same
broad token model as PS1:

```text
offset 0x4D328:
0253 0254 0058 005C 029C 0031 0004 FFFC ...

decoded with the current PS1 JP table:
鞋弱には溶い.<$FFFC>...
```

```text
offset 0x4DC64:
0057 006E 0078 007D 0045 0070 005A 0034 ...
... FFFE FB00 0064 ...
```

```text
offset 0x4DE90:
004D 0038 0076 FFF5 FFF8 048C 048C 0004 FFFE FB00 0072 ...
```

These free-standing token streams are the same text captured by the `field_3c`
text pool above; the scanner simply finds them without the index-table walk.
The catalog, local record index, resource map and text pool are all mapped, and
the text-extraction path for translation is complete. The still-undecoded parts
(record-payload grammar and full `resource_table` resource semantics) are only
needed for graphics/map/event editing, not for text.

## Glyph Map vs PS1

Saturn script text uses swapped-word/on-disc BE `u16` tokens whose ids overlap
the PS1 Japanese table but are **not** a full match:

- **Kana is identical.** Katakana names decode exactly (`シグマ`, `クラレット`,
  `ランフォード…`, `レインフォルス`) and hiragana decodes cleanly.
- **Control words are identical** (`FFFC`, `FFFD`, `FFFE`, `FFFF`, `FFF3`–`FFF8`,
  `FB00`+arg).
- **Kanji slots are reordered/replaced.** Some kanji decode correctly, others
  do not. The root cause is the font plane itself (see the Font section): the
  Saturn `SYSTEM.DAT` glyph slot at a given id often holds a different kanji
  than PS1. Confirmed alignment against the PS1 script and font:

| Saturn token | PS1 glyph slot | Saturn glyph slot | Note |
| --- | --- | --- | --- |
| `0x020D` | 差 | 元 | `ランフォード元帥` (Lanford the Marshal) |
| `0x020E` | 元 | 帥 | Saturn slot holds a different kanji than PS1 |
| `0x0138` | 部 | 部 | identical slot — `部下` decodes correctly |
| `0x0122` | 下 | 下 | identical slot |

Because token ids are glyph-plane indices, the fix is a Saturn-specific glyph
table, derived by rendering the divergent Saturn slots (or by aligning Saturn
entries to matched PS1 records). This does **not** block the translation port:
kana + control structure decode correctly, which is enough to align Saturn text
entries with PS1 records structurally (see the per-chunk correspondence above),
and inserted target text will use a project-authored font/table regardless.

## Font (`SYSTEM.DAT` glyph plane)

The in-game text font is owned by `SYSTEM.DAT`, in the same format as PS1
`SYSTEM.BIN`:

| Property | Value |
| --- | --- |
| Cell | `12x12`, `1bpp`, `18` bytes/glyph, `12` bits/row MSB-first, rows packed continuously |
| Glyph address | `index * 18` from offset `0` |
| Glyph slots | `0..1817` hold glyphs; `1818..1820` are zero padding; writable slots are `0..1819` (slot `1820` crosses into the `0x8000` pointer directory — PS1 allows `0..1820`) |
| Byte order | natural (glyph bytes are **not** byte-swapped, unlike the `u16` text tokens) |

The plane's tail is also *shifted* relative to PS1: the last Saturn glyphs
`1810..1817` equal PS1 `1814..1820` (minus one insertion around Saturn `1814`),
so near the end the PS1-derived slot→kanji map is off by a few slots. The
release's `kanji_map.csv` records what each slot really holds here, and the
`replaced_char` column of a generated assignment CSV keeps naming the *shared*
map's glyph — it says which slot was taken, not which glyph this build lost.

### Which slots this build may sacrifice (`saturn_usage_scan.py`)

A slot is free only if nothing this build still draws with it, and that is a
per-release question: two builds order the same bank differently, so a slot the
shared map calls a spare kanji can be a live glyph here. The scan therefore
reads Saturn's own data and hands the assigner the facts:

- SCEN entries the mapping preserves keep their original tokens;
- SYSTEM group entries no target string covers are copied word for word by the
  packer — a pack id with no resolved translation, a release-only overlay id
  the pack does not carry, or an explicit `preserve`;
- FFFF-terminated runs in the files outside the translation pipeline
  (BAR/SHOP/CUR/BTLDAT/TK_SC, the resident overlays, the `SYSTEM.DAT` tail).

Those slots are barred from the sacrificial pool *and* from inherited
assignments: the tracked CSV is shared by every release of the game, so a slot
free on the one it was written from must be able to move here. Without the
SYSTEM half of that census the Saturn build drew Cyrillic pairs over the `@`
placeholder in the two unit-name pools, over an unnamed icon in group 0, and
over the `▪▪▪注意▪▪▪` frame of the memory-card warning.

### Native symbols — the glyph plan (`saturn_fix_native_glyphs.py`)

The translation encodes some characters through *native* tokens from the PS1
slot→char map (`○`, the standalone `-`, arrows, `（）～`). The Saturn plane
holds those glyphs at *different* slots (`○` `0x4F2`→`0x5F4`, `-`
`0x316`→`0x380`, `←` `0x694`→`0x698`, ...), so the PS1 token would render an
unrelated kanji. The build therefore runs a symbol-class-only (non-CJK,
non-kana) *glyph plan* around the font build:

- `remap` — the exact PS1 bitmap exists in the original Saturn plane: the
  `.tbl` is rewritten to that slot, and `langrisser.assign_font_slots
  --exclude-slots` keeps the slot out of the sacrificial pool (inherited
  assignments on it are reassigned);
- `assign` — the Saturn font has no such glyph (`×`: the Saturn originals
  spell `2割`/`3付4` instead), so the character is force-assigned and rendered
  by the project font like any other tile — nothing is ever copied from PS1;
- `drop` — needed by no *effective* Saturn text (see below): its stale
  mapping is removed from the `.tbl`, so an overlooked usage fails the strict
  encode validators instead of silently rendering a wrong glyph. This is how
  the PS1 pad symbols `▢`/`△` are kept out of the Saturn build.

Kana/kanji are never touched: a differing bitmap there is usually the *same*
character redrawn (the first differing slot `0x00CA` is ロ on both consoles),
and the JP-anchor tooling (name-entry locate, dumps) needs those mappings.

Character needs are computed from the **effective** Saturn texts:
`saturn_apply_text_overrides.py` runs before the plan and normalizes the build
copies with the platform record overrides — shadowed SCEN records get the
platform text (Saturn pad buttons `A`/`B`/`C`/`Y`/`START` instead of PS1
`○×△▢`/`SELECT`), shadowed SYSTEM entries are removed in favour of the
platform overlay — so every later stage (slot assignment, rewrap, validators)
sees exactly the text that ships.

A character reaches the plane through the `.tbl`, so the plan can move it. A
raw `<$XXXX>` token does not: it tells the encoder to emit that word whatever
the table says. Those the same normalizer rewrites in the text itself, moving
each PS1 token to the Saturn slot holding its glyph (`0x0663`, the icon that
closes a whimpering line, to `0x0665`) and failing loudly if this plane has no
such glyph. It reads *and* writes the build copy of the release overrides as
well as the common text, because the whole pack — overrides included — is
written against PS1 slot numbers.

Both the SYSTEM UI text and the SCEN dialogue index into this one plane: SCEN
token `0x0094` renders シ from the `SYSTEM.DAT` font, matching `シグマ` in the
script. `WD_FONT.BIN` (8 KiB) is not this font — it is repeating dither/window
pattern data (`0xAA`/`0x99`/`0x66`), not text glyphs.

Comparison of glyph slots `0..1820` against PS1 `SYSTEM.BIN`:

| Property | Value |
| --- | --- |
| Identical slots | `465` |
| Differing slots | `1356` |
| First differing slot | `0x00CA` (starts at byte `0xE34`; first differing byte `0xE35`, the known divergence point) |
| Main differing bands | `0x0185..0x025D`, `0x025F..0x02CA`, `0x02CC..0x05AC`, `0x05FC..0x071C` |

Kana, punctuation and the early shared range (slots `0x00..0xC9` and the
`0x00CB..0x0184` band) are byte-identical, which is why kana and some kanji
decode correctly. The large `0x0185+` kanji region is reordered/replaced.

Implication for tooling: the glyph format and slot layout match PS1 exactly, so
`langrisser/build_font.py`'s slot-rewrite approach is directly portable to Saturn
`SYSTEM.DAT` — but capped at slot `1819` (`--max-slot`, from the platform
manifest): slot `1820` would overwrite the group pointer directory at `0x8000`
(see above).

Reproducible command:

```bash
python3 -m langrisser.saturn_disc extract SCEN.DAT work/l5/build/saturn/SCEN.DAT
python3 -m langrisser.saturn_scen_scan \
  --scen work/l5/build/saturn/SCEN.DAT \
  --out work/l5/build/saturn/scen_scan.json
```

## Asset-Like `.DAT` Containers

Several Saturn `.DAT` files start with a swapped-word/on-disc BE
directory-like header:

```text
u32 count
repeat count:
    u32 a
    u32 b
```

Confirmed examples:

| File | Size | Count | First entries |
| --- | ---: | ---: | --- |
| `TITLE1.DAT` | `123944` | `2` | `(0x14, 0x1FD4)`, `(0x1FE8, 0x1C440)` |
| `TITLE2.DAT` | `118888` | `2` | `(0x14, 0x1FD4)`, `(0x1FE8, 0x1B080)` |
| `OPEN.DAT` | `351404` | `3` | `(0x1C, 0x6C1C)`, `(0x6C38, 0x3C440)`, `(0x43078, 0x12C34)` |
| `CAST.DAT` | `242700` | `5` | `(0x2C, 0x7430)`, `(0x745C, 0x26580)`, ... |
| `STAFF.DAT` | `531136` | `6` | `(0x34, 0x3E88)`, `(0x3EBC, 0x39400)`, ... |
| `SND_DAT.BIN` | `135220` | `2` | `(0x14, 0x67F2)`, `(0x6808, 0x1A82A)` |

The meaning of each pair is not fully proven. For title/open/cast/staff assets,
the `a` offset points to a descriptor/header block and the `b` offset points to
pixel payload bytes. Example `TITLE1.DAT` entry 0 descriptor:

```text
a = 0x14:
00001E60 0050001C 0028001C 00000020 00000200 00000420 000015A0 00000000
A100DFFF DBDFE7DF FBBED7DF DFBFEFBE ...
```

Partially decoded descriptor fields (all swapped-word/on-disc BE):

- `0x0050 x 0x001C` and `0x0028 x 0x001C` read as `width x height` pairs
  (`80x28`, `40x28`), followed by a series of `u32` sub-offsets
  (`0x20, 0x200, 0x420, 0x15A0`) into the payload.
- The trailing `A100DFFF DBDFE7DF …` words are CLUT data, not direct pixels.

These are not PS1 `IMG.DAT` records. A Saturn title/bitmap editor will need a
separate decoder; the container directory, descriptor `width x height` fields,
two consecutive 256-colour CLUTs, and VDP2 8x8 cell images are the confirmed
starting points.

Further findings (still not a working decoder):

- The `TITLE1.DAT` pixel payload is **uncompressed** (entropy ~6.1 bits/byte,
  exactly 256 distinct byte values) — not LZH/CPK packed like
  `GRAPHIC.LZH`/`LANG5.CPK`.
- The pixels are **8bpp CLUT-indexed**, not 16bpp: rendering the payload bytes
  as indices through a 256-entry BGR555 CLUT produces real structure, whereas a
  16bpp interpretation is noise.
- The large images are **VDP2 8x8 cells**, not a single linear bitmap — and the
  title screens are **tilemap-composed**: a linear de-tile of the cell store
  looks like garbage. The `TITLE1`/`TITLE2` descriptor (sub-asset 0) carries
  two pattern-name tables over one shared cell store: `u16` dims at `+0x04`
  (`80x28`, the 640x224 hi-res overlay: logo, "press start button", the (C)
  line) and `+0x08` (`40x28`, the 320x224 background art), the first table's
  offset at `+0x14`, the second directly after it, total payload size at
  `+0x00`. Entries are BE `u16`: char index in bits 0..11, flip bits 14..15
  (overlay), palette bit 12 (background). The overlay's uniform filler tile is
  its transparent pixel value (255 on `TITLE1`, 254 on `TITLE2`).
  `saturn_title_credits.py` stamps the credit lines into background cells that
  are referenced exactly once (the store has only 2 free cells, so nothing can
  be allocated); staff/cast full-art screens may need different cell-column
  counts or their own nametable handling.
- `CLEAR.DAT` (the SCENARIO CLEAR banner) is now decoded — see below.

### Multi-asset container format (the "one file" model, like PS1 `IMG.DAT`)

The per-screen `.DAT` files are **containers with a top-level table of contents**,
the direct analogue of PS1 `IMG.DAT` holding many assets. The header is:

```text
u32 count
count x (u32 sub_offset, u32 sub_size)   # contiguous: off[i] + size[i] == off[i+1]
```

Verified byte-exact (contiguous, in-bounds) on `TITLE1.DAT`/`TITLE2.DAT`
(count 2), `OPEN.DAT` (3), `CAST.DAT` (5) and `STAFF.DAT` (6). `CLEAR.DAT` has
**no** TOC — it is a single bare asset that starts directly with its
`tex_off`/`tex_size` header (reading its first word as a count gives garbage).

Each container holds two kinds of sub-asset, alternating (small descriptor, then
its big pixel payload):

- **Small sub-asset `[0]`** — a nested mini-container: a sprite table of
  `(u16 width_px, u16 height_px)` entries plus data offsets, **two consecutive
  256-colour BGR555 CLUTs**, and a VDP1 coordinate/command table whose words echo
  the `CLEAR.DAT` header (`0x3c`, `0x44`, ...). Dimensions seen: `80x28`, `40x28`.
  The descriptor header gives the first CLUT as an `(offset, 0x200)` pair (e.g.
  `0x20` in `TITLE1.DAT`); that first palette is for the small VDP1 sprites, and
  the **second palette, immediately after it (`+0x200`, e.g. `0x220`), is the one
  the big cell image is drawn with**. Verified on TITLE1/OPEN/CAST: with the
  image palette the background is pure black (index 255 = `(0,0,0)`) and the art
  is coherent (stone "LANGRISSER / THE END OF LEGEND" logo; blue star nebula on
  CAST); the sprite palette renders the image as colour noise. Big-endian BGR555,
  reusing `langrisser.imgdat.rgb555_to_rgb888`.
- **Big sub-asset `[1]`** — the full-screen image, stored as **VDP2 8x8 cells**
  (its header `tex_off`/`tex_size` are `0`). Rendered linearly it shears with the
  diagonal-streak signature of tiled data; de-tiling with the mini-container's
  CLUT is the remaining decode step. This is why a *linear* VRAM sprite never
  byte-matches these files even uncompressed — the on-disc order is tiled.

This is the general recipe for the title/prologue/staff/cast graphics; decoding
the `[1]` cell arrangement + `[0]` sprite table lets them be translated the same
way `CLEAR.DAT` was. `Now Loading` is the other exception: it is a compressed
texture inside `SYSTEM.DAT`, not a `.DAT` TOC sub-asset.

### Prologue poem — `OPEN.DAT` sub-asset `[2]` (VDP1 text-run list)

The attract-loop poem (PS1 `IMG.DAT` asset 12, a 768x252 red-on-black bitmap) is
on Saturn the third sub-asset of `OPEN.DAT` (`[1]` is the opening cutscene art).
It is **uncompressed** (entropy ~4.3 bits/byte) and is *not* a linear image but a
**VDP1 text-run list** — the poem is drawn as many small sprites:

```text
+0x00 u32  sub-asset size
+0x04 u32  width  (0x140 = 320)
+0x08 u32  height (0x300 = 768 — three 320x256 attract screens stacked)
+0x0c u32  palette offset (0x24)
+0x10 u32  palette entries (0x100)
+0x14 u32  run count (0x32 = 50)
+0x18 u32  run table offset (0x224)
+0x1c u32  glyph atlas offset (0x3b4)
+0x20 u32  glyph atlas size (0x12880)
+0x24      256-entry BGR555 CLUT (index 0 = transparent -> black attract bg)
+0x224     run table: `count` x (u16 x, u16 y, u16 srca_units,
                                 u16 (width_units << 8) | height)
+0x3b4     glyph atlas: 8bpp row-major pixels
```

The last two fields are VDP1 command units, not byte/pixel units:

- `srca_units` is an 8-byte unit; source byte offset is
  `atlas_offset + srca_units * 8`.
- `width_units` is an 8-pixel unit; run width is `width_units * 8`.
- `height` is stored directly in pixels.

With those units, the format is fully accounted for: all 50 original runs are
consecutive in atlas space (`next_srca == srca + width_units * height`), and the
last run ends exactly at `atlas_size == 0x12880`. The earlier "partial/tiny run"
interpretation was caused by reading `srca` and `width` as direct byte/pixel
values.

Each run is a red-text fragment blitted at `(x, y)`; the original `y` values step
by 20 px (`25, 45, 65, ...`, eight lines per poem block — matching the PS1
`TOP_MARGIN ~24` / pitch 20). The atlas is the same ink model as the PS1 poem:
background index 0, **outline index `0xd4` = 212 — identical to the PS1
`langrisser.poem_render.OUTLINE_INDEX`** — and a red fill ramp (`89 -> 176 -> 209`,
verified to be the same red shades in the Saturn CLUT). This cross-validates
that both platforms share the poem art style, although Saturn stores it as VDP1
runs rather than a PS1 bitmap.

`langrisser/saturn_poem_translate.py` re-encodes this sub-asset fixed-size. It
uses the shared `langrisser/poem_render.py` renderer (same text loading, palette
indices, centering, line stamps and vertical layout as PS1), then packs the
320x768 indexed canvas as VDP1 runs, writes a new run table, and pads unused
run-table/atlas space. The output `OPEN.<lang>.DAT` preserves the original
`OPEN.DAT` length and the sub-asset length.

The renderer is shared, but the current Saturn backend uses smaller render
parameters (`font=10`, `line_height=14`) because PS1's `font=12`,
`line_height=18` overflows the fixed `0x12880` run atlas with one run per line
(RU: `0x19128`, EN: `0x17840`). Current settings fit (RU: 40 runs,
`0x12128/0x12880`; EN: 39 runs, `0x10290/0x12880`). A future closer visual match
can split lines into smaller fragments or otherwise improve packing, without
changing the shared renderer.

### `CLEAR.DAT` (SCENARIO CLEAR) — decoded and translated

A VDP1 VRAM dump was used **only to discover** the format; the tool itself reads
everything from the disc file. The VDP1 command table (32-byte commands at VRAM
`0x0`) lists a sprite of **224x80, 8bpp** at texture address `0x4A200`, drawn at
(48,72) — the banner. That texture appears verbatim in `CLEAR.DAT`, so the file
is uncompressed, and everything the redraw needs is on-disc:

```text
u32 texture_offset   (0x378)
u32 texture_size     (0x4600 = 224*80)
VDP1 sprite header / coordinate table
tex_off - 0x200: 256-entry CLUT (16bpp, big-endian BGR555 — same layout as PS1
                 IMG.DAT; reuse langrisser.imgdat.rgb555_to_rgb888)
0x378: 224x80 8bpp texture (background = index 0)
```

The "888" first word was a misread — it is `0x378`, the texture offset. The
palette is the 512 bytes immediately before the texture (`tex_off - 0x200`).
`langrisser/saturn_scenario_clear.py` reads the texture and CLUT from `CLEAR.DAT`
and rewrites the texture in place (fixed size) by calling the shared
`langrisser.banner.redraw_banner` — the exact erase-and-redraw core the PS1 banner
uses — producing a gold `СЦЕНАРИЙ ПРОЙДЕН`.

This gives the general Saturn graphic recipe: an uncompressed 8bpp texture with a
`tex_off`/`tex_size` header and a 256-colour BGR555 CLUT just before the texture,
redrawn in index space with the shared banner core. (A VRAM dump plus the VDP1
command table is the way to *find* a sprite's `(SRCA, width, height, mode)`, but
the build depends only on the disc file.)

### Now Loading plate — `SYSTEM.DAT` compressed texture

The Now Loading dump's VDP1 command table draws a **120x32, 8bpp** sprite at
`0x4A200` (position 182,183). Rendered from the dump it is unmistakably the
"Now Loading" engraved-metal plate, and its first 28 rows are **byte-identical
to the PS1 plate** in `IMG.DAT` asset 0. The Saturn command height is 32 rows;
rows 28..31 decode as zero padding.

Runtime dump command evidence (VDP1 command words read as stored BE words):

```text
now_loading/cmd[3]:
  ctrl=1000 pmod=00A0 colr=7400 srca=9440 size=0F20 xy=(00B6,00B7)
  source = 0x9440 * 8 = 0x4A200, width = 0x0F * 8 = 120, height = 0x20 = 32

scenario_clear/cmd[13]:
  ctrl=1000 pmod=00A0 colr=7400 srca=9440 size=1C50 xy=(0030,0048)
  source = 0x9440 * 8 = 0x4A200, width = 0x1C * 8 = 224, height = 0x50 = 80
```

**VRAM↔disc transform is identity — measured, not assumed.** The SCENARIO CLEAR
banner is a control: it is uploaded to the *same* VRAM slot `0x4A200` and it *is*
stored raw on disc (`CLEAR.DAT`). Its VRAM texture (224x80) equals the on-disc
texture **100% byte-for-byte** (`swap16` matches only 65%). So the emulator dumps
VDP1 VRAM in the same byte order the disc stores 8bpp textures: **no BE /
word-swap / VRAM-format correction is needed** to compare a dumped sprite against
disc data.

The plate is not a declared `.DAT` container sub-asset. It is loaded through the
resident SH-2 texture decoder from `SYSTEM.DAT`:

```text
SYSTEM.DAT + 0x18000  prefix/Huffman-style decode table
SYSTEM.DAT + 0x19E30  compressed Now Loading stream
runtime source        0x00219E30 (SYSTEM.DAT loaded at 0x00200000)
runtime output        0x25C4A200 (VDP1 VRAM 0x4A200)
original stream used  0x791 bytes (1937)
```

The relevant files are loaded in high/low WRAM as follows in the runtime dumps:

| File | Runtime base |
| --- | ---: |
| `A0LANG5.BIN` | `0x06010000` |
| `PROG1.BIN` | `0x06079000` |
| `SYSTEM.DAT` | `0x00200000` |

`PROG1.BIN` calls the decoder at `0x06082CAE` with:

```text
r4 = 0x00218000  # decode table
r5 = 0x00219E30  # compressed stream
r6 = 0x25C4A200  # VDP1 destination
```

The decoder body is in `A0LANG5.BIN` at runtime address `0x0601253C`. It walks a
binary prefix tree from the table, then applies a 16-byte move-to-front history
transform. Leaf types are:

| Leaf second byte | Meaning |
| --- | --- |
| `0xFF` | literal byte from the first leaf byte |
| `0xFE` | end marker |
| `0xFA..0xFD` | MTF/history class; first leaf byte is an additional repeat count |

The first five stream bytes are the MTF-class header. The original stream uses
header `00000081f4`, i.e. history depths `(1, 8, 4, 15)`. The Russian PS1-parity
redraw (`Загрузка…`, same 120x28 visible pixels as the PS1 patch plus four zero
rows) fits the original `0x791`-byte stream budget with header `000000a1f5`,
i.e. depths `(1, 10, 5, 15)`, producing `1928/1937` bytes.

`langrisser/saturn_now_loading.py` implements both decoder and encoder. It reuses
`langrisser.now_loading.redraw_plate_pixels`, so the Saturn visible plate is
byte-identical to the PS1 translated plate; only the container codec differs.
The build patches `SYSTEM.<lang>.DAT` in place and preserves the file length.

Decoding the remaining tile arrangements, palettes and dimensions — and then
redrawing the translated graphics — is still needed for the remaining bitmap
assets. **Of the graphic assets: SCENARIO CLEAR is done; title credits are done;
the prologue poem is done; Now Loading is done; staff/cast containers are
recognized and tractable; the name-entry screen is statically patched but still
needs runtime confirmation.**

### Name-entry alphabet tables — `SYSTEM.DAT`

Saturn does not carry the PS1 executable-side 10x10 name-entry table. Static
searches found both confirmed Saturn copies inside `SYSTEM.DAT`, using the same
kana token ids as PS1 but stored as on-disc BE words:

```text
SYSTEM.DAT + 0x08CE6  display grid: 19 runs of 5 u16 tokens, each followed by FFFF
SYSTEM.DAT + 0x1B6E0  flat input table: the same 95 u16 tokens, no separators
```

The first structure is the visible alphabet grid. The second is the accepted
input list / cursor-order table. No matching PS1-style `row K = run K | run K+9`
copy exists in `A0LANG5.BIN`, `PROG1.BIN` or `PROG2.BIN`; only incidental short
run fragments appear there.

`langrisser/saturn_name_entry.py` patches both tables in place after the target
font table has been generated. It locates the original kana patterns, verifies
the full source contents, and writes the target language's
`name_entry_grid.json` as BE tokens from the Saturn build `.tbl`. File size is
unchanged. Runtime confirmation of cursor movement and OK/cancel behavior is
still pending.

## Translation Coverage On Saturn

Honest status of applying the universal language pack to Saturn, by asset:

| Translation asset (README) | PS1 | Saturn |
| --- | --- | --- |
| SCEN scenario/dialogue text | done | done — strict pipeline translates 125/131 blocks through `data/releases/l5-saturn-jp/scen_mapping.json`; 6 service/name-pool chunks are explicitly preserved |
| SYSTEM UI text | done | strict pipeline — 16/16 groups pack through `data/releases/l5-saturn-jp/system_mapping.json`; Saturn-only RAM/save strings live in sparse language overlays |
| Font glyphs | done | done — Cyrillic into `SYSTEM.DAT` slots 0..1819 (slot 1820 would cross the `0x8000` pointer directory); needed native symbols remapped onto the real Saturn slots via the glyph plan |
| Title credits graphic | done | **done** — `saturn_title_credits.py` stamps the PS1 credit lines (same `title_text_mask`/`title_alpha_table` pipeline, masks doubled for the 640-wide hi-res plane) onto the *overlay* tilemap of `TITLE1.DAT`/`TITLE2.DAT` with a transparent background (the 40x28 background plane doubles as the menu backdrop and must stay clean); each filler position gets a new cell appended to the cell store (last sub-asset, TOC size updated) |
| Prologue poem graphic | done | done — `OPEN.DAT[2]` VDP1 run-atlas format; `saturn_poem_translate.py` renders the target poem to 320x768 at the PS1 metrics (font 12 / line height 18) and grows the atlas to fit (the poem is the last sub-asset: header `+0x00`/`+0x20` and the TOC size are updated; RU: 38 runs, `0x19128` atlas bytes) |
| Now Loading plate | done | done — compressed 120x32 8bpp texture in `SYSTEM.DAT`; decoded/re-encoded by `saturn_now_loading.py`; visible 120x28 output is byte-identical to the PS1 translated plate |
| SCENARIO CLEAR banner | done | done — `CLEAR.DAT` 224x80 8bpp, translated via the shared banner redraw |
| Name-entry alphabet screen | done | implemented/static — `saturn_name_entry.py` patches `SYSTEM.DAT+0x08CE6` display grid and `+0x1B6E0` input table; runtime confirmation pending |
| Virash cutscene subtitles | done | **not investigated** |

The text/font path and PS1-parity graphics are applied and validated. Remaining
risk is runtime confirmation of Saturn-specific adapters and remastered disc
boot/playback.

## Insertion / Repack Model

Applying the Saturn translation is a fixed-size rebuild when a structure fits,
and a grown remaster when translated `SCEN.DAT` needs more space. SYSTEM and the
known graphics stay fixed-size; SCEN text tables can be appended inside their
own block and the disc can be remastered with shifted track indices.

### SCEN scenario text — proven

The `field_3c` text pool is rebuilt in place per block. Its region layout is::

    u32 total_size
    u16 entry_offsets[count]      # relative to the region base, u16
    entry payloads (u16 tokens, FFFE/FFFF-terminated), concatenated
    zero padding to total_size

`saturn_scen.build_local_index_table` / `splice_local_index_table` rebuild the
region from a list of token entries and splice it back. The model is validated:

- Rebuilding all 131 blocks from their own parsed entries reproduces the file
  **byte-for-byte** (`131/131` identical), so the layout is fully understood.
- A modified entry keeps the file length, leaves every byte outside the region
  unchanged, and reads back correctly; other entries are identical up to their
  terminator (freed space becomes trailing zero padding after a terminator,
  which the engine never reads).

Constraints for insertion:

- Preserve entry **count and order** (entries are addressed through the
  regenerated offset array).
- The whole region must stay under `0xFFFF` bytes (offsets are `u16`).
- If the content fits the original `total_size`, nothing else in the block moves.

Growth (translated text longer than Japanese — the common case for Russian):
the table is **enlarged in place and everything after it shifts back** by the
4-aligned growth (`saturn_scen.rebuild_block_text`); `saturn_scen.repack_scen`
then re-lays the blocks at 0x800-sector alignment and rewrites the top-level
catalog. In-place growth is dictated by the runtime block loader (PROG1
`0x6079172`, disassembled from the mednafen dumps):

```text
rt   = block + u32(block+0x00)         ; resource table
                u16(rt+0x3A) -> global ; text base index
text = rt + u32(rt+0x3C)               ; the field_3c table (u32 read)
A    = text + u32(text)                ; == text + total_size: NEXT section!
B    = A + u32(A) ...                  ; further sections chain on relative
                                       ; u32 links stored in the data
```

Every section after the text table is addressed **relative to the table end**,
so shifting the tail keeps the whole chain intact, while *moving* the table
does not: the first Saturn build appended grown tables at the block end and
repointed field_3c, which left `text + total_size` pointing at garbage past
the block — in game the first battle lost its unit icons and hung, and the
attract demos froze near the menu. The in-place growth mostly fits inside the
0x800 sector slack, so the Russian file grows by only a few KB.

### Alignment — reuse the PS1 work only where it provably fits

Langrisser V was translated on PS1 first, so the Saturn build reuses that work
rather than redoing it. A Saturn entry takes a PS1 record's translation
**only** when both JP originals provably match; where they differ, the Saturn
text is its own. Raw token ids in the reordered kanji bank
(`>= 0x185`) are incomparable across consoles — but *normalized to text*
through the tracked `data/releases/l5-saturn-jp/kanji_map.csv` (same CSV convention
as the common `groups_report.csv`, holding *only* the reordered kanji bank from
the game's `kanji_bank_start`; the shared range is never duplicated) they
compare directly. The map is measured, not inferred: `derive_font_map.py
--bank-only` matches each Saturn glyph bitmap against the already-mapped PS1
plane, which names 1418 of the bank's 1429 glyphs. The earlier map was voted
from positionally matched record pairs, and that put the wrong character in
eleven slots (`巫` read as `祭`, `訓` as `価`) and left seventy-nine unnamed.
The apply
(`monotone_alignment`) runs an order-preserving longest matching per block:
pass 1 on the full normalized originals (kanji included, trailing `FFFF`
stripped), pass 2 for records containing unresolved rare kanji — stable
signature (kana/ASCII/controls) match verified position-by-position with
wildcards allowed only at the unresolved spots. Insertions/deletions on
either side align without hand-written ranges. This runs in
`langrisser/saturn_reconcile.py`, once: what it proves is written to the
release mapping as ranges, and the build reads those instead. This replaced the earlier
speaker-checked prefix alignment, which silently mis-placed translations in
narration-heavy blocks (no `FB00` speaker to compare — block 39 had 245
wrongly-fed entries).

Entries with no proven counterpart hold Saturn-edited content (pad-button
phrasing, extra lines, reordered duplicates, karaoke jokes). They must come
from release records (`data/games/<game>/lang/<code>/releases/l5-saturn-jp/SCEN/`, wired
through `scen_mapping.json` entries with a `replaces_ps1` annotation naming
the PS1-only record they supersede) or stay explicitly preserved with
`"pending_review": true` until translated — anything else fails the build.
`langrisser/saturn_scen_audit.py` regenerates the minimal mapping and emits
`work/l5/build/saturn/scen_platform_review.md`: each pending record with the
Saturn original decoded through the derived Saturn kanji map
(`kanji_map.csv`) plus the closest PS1 record and its current ru/en text.

### Universality

The `data/games/<game>/lang/<code>` pack is console-agnostic: the same translation applies to
both PS1 and Saturn. Because the target alphabet occupies the same font slots on
both, a record's encoded token stream is identical; only the byte order and
container differ. `langrisser/saturn_apply.py` reuses the PS1 dump
(`parse_dump_file`), codec (`Codec`) and `.tbl` unchanged. Platform is therefore
a build-time choice, not a property of the pack. Automatic prefix/signature
alignment covers structurally identical chunks; explicit durable mappings cover
PS1-only deletions and Saturn local reorders. On the current packs it translates
125/131 blocks and explicitly preserves the 6 service chunks.

### SYSTEM UI text — same offset-table repack

`SYSTEM.DAT` groups are the same offset-table structure as PS1, so the PS1
`--repack` path (regenerate each group's offset table, keep string indices)
ports via the shared `langrisser.offsetgroups` model with the Saturn BE config.
Index addressing is proven on PS1 and structurally implied on Saturn (the
offset-table indirection exists precisely to allow it); a final guarantee needs
the Saturn executable, but the fixed-size in-place repack is safe regardless as
long as string indices and group layout are preserved.

### Font — slot rewrite

Cyrillic glyphs are drawn into `SYSTEM.DAT` glyph slots `0..1820` (same
12x12x18 format as PS1), so `langrisser/build_font.py`'s slot-rewrite ports directly;
only the glyph-plane file offset differs.

### Remaining runtime and release checks

- Runtime-check that the remastered BIN/CUE reaches translated scenario text,
  SYSTEM screens and Saturn-specific graphic adapters.
- If future Saturn-only text is found, add sparse platform overrides under
  `data/games/<game>/lang/<code>/releases/l5-saturn-jp/` and keep strict builds green.

### Cross-validation and ISO-output recipe (Langrisser III, Saturn)

The sibling project `external/langrisser3-english` (same publisher, same Saturn
engine family) independently confirms this container model and supplies the
disc-output recipe. Its `D00.DAT` is structurally identical to our `SCEN.DAT`:

- a big-endian `u32 section_count` then `(sector, size)` pairs at `0x800`
  sectors — our catalog;
- each section's text area is `u32 text_size`, `u16 offset_table_size`, a
  `u16` offset array **ending in a `0x00A4` sentinel**, then 2-byte big-endian
  tile-code entries with control codes (`0xF600` consumes an argument) — our
  `field_3c` text pool. The sentinel is the "+1" entry our count includes.

Its `iso_tools.py` shows the disc-output path now reimplemented here (that repo
has no open-source license, and the technique is standard CD-ROM practice):

- Mode1/2352 sectors, user data at offset `16`, with **EDC/ECC recomputed**
  per edited sector.
- Growing a file by **relocating it to the end of the image** and updating its
  ISO9660 directory record (extent + size, written both little- and big-endian
  per spec).
- Reassembling the `.cue`/BIN set from the patched track 1 plus the original
  audio tracks, shifting track 2's MSF for the new track-1 length.

This directly applies to injecting our grown `SCEN.DAT` (and same-size
`SYSTEM.DAT`) into the Saturn disc. `langrisser/saturn_disc.py remaster` implements
the output path conservatively: grown files are relocated into new MODE1 sectors
inserted before track 2, `ADPCM/**/*.XA` logical LBAs and cue INDEX times are
shifted by the same sector delta, and all modified MODE1 sectors are rebuilt
with fresh EDC/ECC. It does **not** help with the graphic asset formats
themselves; those still need per-format decoders.

## Executable / Code Files

Likely code-bearing files:

| File | Size | Notes |
| --- | ---: | --- |
| `A0LANG5.BIN` | `232660` | Boot or early program module |
| `PROG1.BIN` | `508908` | Program code/data |
| `PROG2.BIN` | `78548` | Program code/data, references `LANG5.CPK` in plain ASCII |

The PS1 executable patching logic is not portable. Saturn uses a different CPU
and memory model; all runtime proofs and patches must be rediscovered in the
Saturn code.

## Applicability To The Current Project

Reusable with little conceptual risk:

- Existing English/Russian translation content as translation memory.
- Canonical terminology (`names.csv`, `glossary.csv`) after mapping Saturn
  source ids to PS1 ids.
- Review workflow concepts.
- Text token codec concepts: endian and record location are now solved; the
  Saturn SCEN text pool aligns to PS1 chunks 1:1 and the SYSTEM groups align to
  PS1 groups 1:1, so both text stores port from the existing translation.
- The slot-rewrite font builder (`langrisser/build_font.py`): the Saturn font is the
  same 12x12x18 PS1 format in `SYSTEM.DAT`, so drawing the target alphabet into
  slots `0..1820` ports directly.

Partially reusable after platform adaptation:

- `SYSTEM.BIN` dumper/packer logic: same group concept, but Saturn
  `SYSTEM.DAT` uses swapped/on-disc BE words and shifted groups.
- Scenario validation concepts: control words match the PS1 model, but record
  payload/VM metadata is still undecoded (not needed for text).

Not directly reusable:

- PS1 `iso_mode2.py` as-is.
- PS1 `SCEN.DAT` chunk pointer / local record table parser.
- PS1 `IMG.DAT` title/poem/bitmap tooling.
- PS1 executable patches (`SLPS_018.19`) and all runtime addresses.
- PPF target assumptions for the PS1 `.bin`.

## Work Tracker

Current focus: the minimally-necessary text path for translation is complete —
both SCEN and SYSTEM text are located, deterministically dumpable, mapped 1:1 to
the PS1 script/UI, packed back into Saturn files, and built with the shared
language packs. The core font format is confirmed PS1-compatible. Remaining work
is graphic/runtime parity: remaining staff/cast graphics, unresolved mapping
deltas, and optional Saturn-specific kanji-table cleanup for reading JP kanji
directly. The record-payload and full
`resource_table` grammars are only needed for graphics/map/event editing, not
for text.

### Milestones

- [x] Parse Saturn CUE and track layout.
- [x] Read track 1 as `MODE1/2352` ISO9660.
- [x] List and extract track-1 files without modifying the image.
- [x] Map ISO `ADPCM/**/*.XA` entries to track-2 physical sectors.
- [x] Remaster translated Saturn files into a mixed-mode BIN/CUE.
- [x] Confirm the 225-sector logical-to-physical XA correction.
- [x] Dump Saturn `SYSTEM.DAT` string groups using the confirmed on-disc word order.
- [x] Parse the `SCEN.DAT` top-level 131-entry table.
- [x] Scan low `SCEN.DAT` regions for swapped-word/on-disc BE token streams.
- [x] Map `SCEN.DAT` top-level catalog as `(start_sector, used_size)`.
- [x] Map `SCEN.DAT` fixed payload header and section offsets.
- [x] Map `SCEN.DAT` local `record_index` section.
- [x] Map `SCEN.DAT` `resource_map` row structure.
- [x] Map `SCEN.DAT` `resource_table.field_3c` local index table header.
- [x] Confirm `field_3c` local index table is the scenario text pool.
- [x] Build a deterministic Saturn script dump with stable `(chunk, entry)` ids.
- [x] Compare Saturn script entries against PS1 `work/l5/scriptdump/` (131 chunks 1:1).
- [x] Confirm Saturn `SYSTEM.DAT` groups correspond 1:1 to PS1 `SYSTEM.BIN`.
- [x] Confirm the Saturn dialogue/control-word grammar (matches PS1 model).
- [x] Characterize the Saturn glyph map vs PS1 (kana identical, kanji reordered).
- [x] Classify the Saturn font storage and renderer cell model (`SYSTEM.DAT`, 12x12x18, PS1-compatible).
- [ ] Build the Saturn-specific kanji table (only needed to fully read JP kanji).
- [x] Decode at least one Saturn title/bitmap container.
- [x] Decode and translate the `CLEAR.DAT` scenario-clear banner.
- [x] Decode and stamp the `TITLE1.DAT` title credits.
- [x] Decode and translate the `OPEN.DAT[2]` prologue poem run-atlas.
- [x] Decode and translate the compressed `SYSTEM.DAT` Now Loading plate.
- [x] Locate and patch the Saturn name-entry display grid and input table.
- [x] Define the SCEN insertion/repack model (fixed-size field_3c rebuild).
- [x] Validate the model by 131/131 byte-identical round-trip + substitution.
- [x] Implement SCEN text growth (append + re-layout) and apply the RU pack.
- [x] Implement the Saturn SYSTEM UI-text packer (shared group model, BE).
- [x] Reuse the font builder to draw Cyrillic into the Saturn glyph plane.
- [x] Wire the Saturn build flow (font + SYSTEM + SCEN) reusing shared stages.
- [x] Reconcile the interspersed Saturn<->PS1 per-chunk/group mapping deltas.
  Strict builds for EN/RU now fail only on real future mapping gaps;
  `--allow-unmapped` is diagnostic only.
- [x] Inject the grown Saturn files back into the mixed-mode BIN/CUE.
- [ ] Decode `SCEN.DAT` record-payload grammar (graphics/map/event editing only).
- [ ] Decode `SCEN.DAT` `resource_table` resource semantics (non-text editing only).

### Tested Hypotheses

| Hypothesis | Status | Evidence / Result |
| --- | --- | --- |
| Saturn track 1 can be read with the PS1 `iso_mode2.py` sector model. | Rejected | PS1 helper assumes MODE2 user offset 24; Saturn track 1 is MODE1 user offset 16. |
| Track 2 is a second ISO9660 filesystem. | Rejected | Track 2 has XA subheaders and no ISO PVD; ISO entries in `ADPCM/` point into it. |
| `ADPCM/**/*.XA` extents point directly to physical raw sectors. | Rejected | Physical sector is ISO extent minus the 225-sector pregap. |
| `SYSTEM.DAT` text tables are PS1-style little-endian groups. | Rejected | Little-endian scan finds no groups; swapped/on-disc BE scan finds 16 valid groups. |
| `SYSTEM.DAT` keeps the same broad text-table concept as PS1. | Confirmed | 16 swapped/on-disc BE groups decode to unit/menu/system strings; total 2639 strings. |
| Saturn `SYSTEM.DAT` groups correspond 1:1 to PS1 `SYSTEM.BIN` groups. | Confirmed | Both 16 groups in the same order; 14/16 have identical entry counts; the unit-name group is 127/130 byte-identical by index (3 diffs are glyph-slot reordering). `langrisser.saturn_reconcile system` resolved all 2639 entries (2042 by alignment, 597 already written by hand, 0 undecided) and recorded them in `system_mapping.json`. |
| Saturn `SCEN.DAT` top-level entries are `(event id, pointer)`. | Rejected | First field multiplied by `0x800` gives every payload start; second field is always `used_size <= allocated_size`, with zero padding after it. |
| Saturn `SCEN.DAT` top-level catalog is `(start_sector, used_size)`. | Confirmed | All 131 entries validate; first payload starts at `0x800`, allocation is sector-aligned, all padding bytes are zero. |
| Saturn `SCEN.DAT` payloads have a fixed header with section offsets. | Confirmed | All 131 blocks have ordered section offsets from `0x30` through `resource_table_offset`. |
| Saturn `SCEN.DAT` has a local record index. | Confirmed | At `record_index_offset`: `u32 count`, then `(record_id, record_offset)` pairs; 1820 total indexed records, all offsets in range. |
| Saturn `SCEN.DAT` `resource_map` rows are 4-byte `(record_id, resource_slot, variant)` records. | Confirmed | All 131 sections validate as 4-byte rows; 1362 rows; slots 0..15 and variants 0..3 observed. |
| Saturn `SCEN.DAT` `resource_slot` is always below 14. | Rejected | Slots 14 and 15 occur in several chunks. |
| `resource_table + 0x40` is always an `FFFFFFFF` sentinel. | Rejected | 45 chunks have `FFFFFFFF`; the rest contain non-sentinel-looking values, often shaped like token data. |
| `resource_table.field_3c` points to a local indexed table. | Confirmed | All 131 blocks validate as `u32 total_size` plus `u16` offsets; 10071 entries total. |
| The `field_3c` local indexed table is the scenario text pool. | Confirmed | 10068/10071 entries are terminated text; the 3 exceptions are `F702` control-only entries in chunk 38. Names + dialogue + event lines in reading order, PS1 control grammar. |
| Saturn `SCEN.DAT` chunks correspond 1:1 to PS1 `SCEN.DAT` chunks. | Confirmed | Both have 131 chunks; per-chunk text counts match exactly for 95/131 and within ±2 for 111/131; all deltas small and negative. |
| Saturn `SCEN.DAT` record payloads are text records. | Rejected | Payloads between record-index and `resource_map` are high-entropy binary resources (tilemaps/graphics/VM); text lives only in the `field_3c` pool. |
| Saturn `SCEN.DAT` contains raw swapped-word/on-disc BE token streams. | Confirmed | Scanner finds 619 candidates; these are the same entries the `field_3c` walk yields. |
| PS1 JP token table is exact for Saturn scenario text. | Rejected (partial map) | Kana and control words match exactly; kanji slots are reordered (e.g. 元 is Saturn `0x020D` vs PS1 `0x020E`, while `部下` at `0x0138`/`0x0122` is unshifted). |
| Saturn `SYSTEM.DAT` uses the PS1 12x12x18 font format. | Confirmed | Glyph at `index*18`, 12 bits/row MSB-first; `下`/`部`/`シ` render correctly and are byte-identical to PS1. |
| The Saturn text font equals the PS1 font. | Rejected (partial) | 465/1821 glyph slots byte-identical; 1356 differ, all in the `0x0185+` kanji region; kana identical. |
| `WD_FONT.BIN` holds the in-game text font. | Rejected | It is repeating dither/window pattern data (`0xAA`/`0x99`/`0x66`), not 12x12 glyphs; SYSTEM.DAT owns the text font. |
| The SCEN `field_3c` text pool can be rebuilt in place at fixed size. | Confirmed | Rebuilding all 131 blocks from their parsed entries reproduces the file byte-for-byte; a substitution preserves length and every byte outside the region. |
| Saturn title/OPEN/CAST/STAFF `.DAT` files are PS1 `IMG.DAT` records. | Rejected | They use separate swapped-word/on-disc BE directory-like headers and different payload layout. |
| Saturn title assets store a descriptor block with `width x height` fields plus a pixel payload. | Confirmed | `TITLE1.DAT` entry-0 descriptor holds `80x28`/`40x28` dims and payload sub-offsets; the top-level TOC splits descriptor sub-assets from image sub-assets. |
| The Saturn title pixel payload is 16bpp RGB555 direct colour. | Rejected | The apparent `u16` gradients are CLUT bytes. Rendering the payload as 8bpp indices through the descriptor's image CLUT reconstructs coherent art; 16bpp direct-colour rendering is noise. |
| Saturn title/open/cast/staff large image payloads are VDP2 8x8 cell streams. | Confirmed | `saturn_container.py` de-tiles 8bpp cells; `saturn_title_credits.py` re-tiles the modified title-credit image fixed-size. |
| The prologue poem `OPEN.DAT[2]` run table uses direct byte offsets and pixel widths. | Rejected | `srca` and `width` looked tiny under that reading. Re-reading them as VDP1 units (`srca * 8`, `width_units * 8`) accounts for all 50 runs and exactly consumes the `0x12880` atlas. |
| The prologue poem `OPEN.DAT[2]` is a fixed VDP1 run-atlas image. | Confirmed | Header geometry is 320x768; run table entries are `(x, y, srca_units, width_units/height)`; all original runs are consecutive in atlas space; `saturn_poem_translate.py` re-packs translated poems fixed-size. |
| The Now Loading plate is only embedded in resident SH-2 code/data. | Rejected | Runtime tracing found the compressed stream in `SYSTEM.DAT+0x19E30`, loaded at `0x00219E30`; `PROG1` passes it to the decoder at `0x06082CAE`. |
| The Saturn Now Loading plate can be decoded and re-encoded fixed-size. | Confirmed | `saturn_now_loading.py` decodes `SYSTEM.DAT+0x18000/+0x19E30` to the 120x32 VDP1 texture, redraws the visible 120x28 through the PS1 plate routine, and re-encodes the RU stream as `1928/1937` bytes. |
| The Saturn name-entry screen uses a PS1-style executable 10x10 table. | Rejected | Full PS1 row-layout patterns do not occur in `A0LANG5.BIN`, `PROG1.BIN` or `PROG2.BIN`; only the two full tables in `SYSTEM.DAT` match. |
| The Saturn name-entry grid and input list can be patched in `SYSTEM.DAT`. | Confirmed statically | `saturn_name_entry.py` verifies and rewrites the full display grid at `0x08CE6` and flat input table at `0x1B6E0` using target-language single glyph tokens. |
| Saturn translated files can be remastered into a mixed-mode BIN/CUE. | Confirmed structurally | `saturn_disc.py remaster` relocates grown `SCEN.DAT`, shifts track 2+ cue times and ADPCM directory extents, rebuilds MODE1 EDC/ECC, and extracted replacements compare byte-identical to build outputs. |
| Some Saturn/PS1 SCEN count deltas can be aligned without manual maps. | Confirmed narrowly | Three chunks have a unique exact subsequence match when comparing only platform-stable JP tokens (kana/ASCII/punctuation/control words). `langrisser/saturn_apply.py` applies only those unique matches and leaves ambiguous cases to explicit `scen_mapping.json` entries. |
| All current Saturn/PS1 SCEN text deltas are covered by durable mapping. | Confirmed | Strict EN/RU Saturn builds translate 125/131 SCEN blocks, preserve the 6 verified service/name-pool chunks, and report `skipped(misaligned)=0`. |

### Immediate Next Steps

1. Runtime-check the Saturn name-entry screen/cursor/OK behavior.
2. Runtime-check the remastered Saturn BIN/CUE.
3. Build the Saturn-specific kanji table by aligning Saturn entries with matched
   PS1 records, so JP kanji reads cleanly (optional: structural alignment already
   works without it).

## Next Reverse-Engineering Plan

Completed:

- Read-only Saturn disc explorer:
  - CUE parser;
  - MODE1 ISO extraction from track 1;
  - XA entry reader for `ADPCM/**/*.XA` with the 225-sector correction.
- Read-only Saturn SYSTEM dumper:
  - swapped-word/on-disc BE string groups;
  - Saturn scan start;
  - JSON output compatible with review/comparison scripts.
- Initial `SCEN.DAT` scanner:
  - top-level `(start_sector, used_size)` catalog parser;
  - swapped-word/on-disc BE token-stream candidate scan;
- `SCEN.DAT` catalog and local record index:
  - top-level `(start_sector, used_size)` catalog;
  - fixed per-payload section offsets;
  - local `(record_id, record_offset)` tables.
  - `resource_map` rows as `(record_id, resource_slot, variant)`;
  - derived record spans for payload grammar analysis.
- `SCEN.DAT` scenario text extraction:
  - `field_3c` local index table confirmed as the text pool;
  - deterministic dump with stable `(chunk, entry)` ids (`saturn_scen_text.py`);
  - 131-chunk 1:1 correspondence with the PS1 script;
  - glyph map characterized (kana identical, kanji piecewise-reordered).
- Saturn insertion/build path:
  - fixed-size/grown `SCEN.DAT` block repack;
  - `SYSTEM.DAT` UI string packer;
  - shared font builder for Saturn glyph plane;
  - Saturn build driver for SYSTEM/SCEN/CLEAR/TITLE1/OPEN.
- Saturn graphics:
  - `CLEAR.DAT` scenario-clear banner decoded and redrawn;
  - `TITLE1.DAT` title-credit image decoded and stamped;
  - `OPEN.DAT[2]` prologue poem run-atlas decoded and re-packed.
  - `SYSTEM.DAT` compressed Now Loading plate decoded and re-packed.

Next:

1. Runtime-check the Saturn name-entry screen and cursor/OK behavior.
2. Runtime-check the remastered Saturn BIN/CUE.
3. Optional cleanup:
   - build a Saturn-specific kanji table by aligning matched PS1/Saturn records;
   - finish staff/cast bitmap parity if needed for release polish.

## Open Questions

Resolved for the translation text path:

- Scenario text is the `field_3c` local index pool; record payloads are binary
  resources, not text. Text streams are raw swapped-word tokens (uncompressed).
- Saturn chunks map 1:1 to PS1 chunks, so the existing translation can be ported
  by `(chunk, entry)` alignment.

Resolved for the font/render path:

- `SYSTEM.DAT` owns the text font (12x12x18, glyph at `index*18`, PS1-compatible
  slots `0..1820`); `WD_FONT.BIN` is dither/window pattern data. SCEN dialogue
  and SYSTEM UI share this one plane. The slot-rewrite font builder is portable.
- `SYSTEM.DAT` UI strings can be packed by the shared group model using the
  Saturn swapped/on-disc word order.

Resolved for the current graphic path:

- `CLEAR.DAT`, `TITLE1.DAT` title credits, `OPEN.DAT[2]` prologue poem, and the
  compressed `SYSTEM.DAT` Now Loading plate are decoded and re-encodable
  fixed-size.
- The Saturn name-entry display grid and flat input table are located and
  patchable in `SYSTEM.DAT`.
- Translated Saturn files can be remastered into a mixed-mode BIN/CUE with
  shifted track times and fresh MODE1 EDC/ECC.

Unresolved lower-priority format details:

- What is the exact Saturn kanji slot ordering (needed only to read JP kanji)?
- What is the `resource_table`/record-payload grammar? (Graphics/map/event
  editing only, not text.)
- Does the statically patched Saturn name-entry screen behave correctly at
  runtime (cursor movement, text entry, OK/cancel)?
- What compact patch format is appropriate for distributing Saturn mixed-mode
  BIN/CUE edits, if full BIN/CUE output is not acceptable?
