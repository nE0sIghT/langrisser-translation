# Langrisser III (PS2) Disc Format

Reverse-engineering notes for the PlayStation 2 release of *Langrisser III*.

**These notes were made in a separate working tree and are carried over here
unchanged in substance — nothing below has been re-verified against this
repository.** Where they name a tool or a path, it is that tree's, not this
one's: `scripts/dat_container.py`, `scripts/script_text.py` and `docs/PLAN.md`
do not exist here. Treat every claim as sourced, not as checked.

The disc itself is present at `iso/l3-ps2-jp/Langrisser III (Japan).bin`, and
its identity was confirmed in this repository against the same Redump entry
these notes cite.

## Disc identity

| | |
| --- | --- |
| Game | Langrisser III (ラングリッサーⅢ) |
| Platform | PlayStation 2 |
| Region | Japan (NTSC-J) |
| Serial | `SLPM-62689` (`BOOT2 = cdrom0:\SLPM_626.89;1`) |
| Version | `VER = 1.05` |
| Media | CD-ROM, single data track, MODE2/2352 |
| Release | 2005-10-27, X-Nauts for Taito — a port of the 1996 Sega Saturn original |
| Image build date (PVD) | 2005-09-13 18:27:29 |
| ISO9660 system id | `PLAYSTATION` |

Unlike every other disc this project targets, this one is single-track, so the
whole image is comparable across rips and there is no data-track/audio-track
distinction to make.

### Redump match

Matches **Redump disc 74251** — <http://redump.org/disc/74251/> — added
2020-11-14. All three Redump checksums match.

| | |
| --- | --- |
| Sectors | `277024` |
| Size | `651560448` bytes |
| CRC32 | `c18492a9` |
| MD5 | `d8aa7691fccc767d60faa7d5e15e9792` |
| SHA-1 | `191af46f0d93a8de5d60f0b23e82a15dba8235c0` |
| SHA-256 | `27ccf41d8d0fa96602e3dd4249d591f4f0633151afacf5dfce6028f5d3a54c09` |

The source these notes worked from was a MAME CHD, extracted with `chdman`:

| | |
| --- | --- |
| CHD file size | `363099401` bytes |
| Compressors | cdlz (LZMA), cdzl (Deflate), cdfl (FLAC) |
| Logical size | `678154752` bytes (277024 units × 2448) |
| Hunk / unit | `19584` / `2448` bytes |
| Data SHA-1 | `2fd5595607a5a8ec90f9646f84e1d3b0236a8d69` |
| CHD SHA-1 | `3cd83f9b3947ca848eaa7e085cfa25d839e74f1a` |
| Track meta | `TRACK:1 TYPE:MODE2_RAW SUBTYPE:NONE FRAMES:277024 PREGAP:0 POSTGAP:0` |

`chdman verify` passed. The unit size 2448 is 2352 raw plus 96 bytes of
subcode; `chdman extractcd` strips the subcode and yields the 2352-byte
sectors that hash as above.

## Filesystem

ISO9660, single MODE2/2352 track, user data at offset 24 within each sector
(Mode 2 Form 1). Verified by finding the primary volume descriptor (`CD001`) at
LBA 16, offset 24.

```text
SYSTEM.CNF          57   BOOT2 = cdrom0:\SLPM_626.89;1, VER 1.05, VMODE NTSC
SLPM_626.89  1,331,888   boot ELF
MODULES/                 IOP modules (IRX), sound and streaming
DATA/                    game data — text, graphics, maps
SND/                     audio
```

## The `.DAT` container

**Verified** across `COMMON`, `EP`, `SYSTEM`, the `Sxx*` stage files,
`CHARMAKE` and `PIC*` by hex inspection. Every file under `DATA/` shares one
header:

```text
0x000  u32      0
0x004  char[]   build path "D:\VscProj41\SrcData", NUL-padded
0x080  u32      0
0x084  u32      0x10
0x088  u32      0x800          sub-table offset
0x08C  u32      entry count    stage files 3, COMMON 14, SYSTEM 4
0x800  entry[]  16 bytes each: u32 id, u32 size, u32 offset, u32 flags
0x1000 ...      payloads; offset is relative to 0x1000, each padded to 0x10
```

The check that proves it: `0x1000 + last.offset + last.size == file size` holds
for every container, and a reassembly pass rebuilt 200 of 202 `DATA/*.DAT`
byte-identically. The two failures are small `EFFECT/*` files with a different
mini-header; five more files are not containers at all (296-byte stubs).

Resource layout per file:

| File | Resources |
| --- | --- |
| `SnnP.DAT`, `SnnB.DAT`, `SnnE.DAT` | `id 1` script — VM bytecode with embedded text, flags `0x02000000`; `id 2` small blob, likely palette or parameters, flags `0x02010000`; `id 3` graphics, the largest, flags `0x02020000` |
| `COMMON.DAT` (14), `SYSTEM.DAT` (4) | resource ids from `0x200`, flags `0` or `8` |

## Text encoding — Shift-JIS

**Verified**, and it is the finding that most changes what a translation costs:
script text is plain Shift-JIS, not a font-index encoding. Confirmed by
decoding real Japanese straight out of the stage files — `S01P.DAT` carries
`『浮遊城・襲撃』` and its narration, `S01B.DAT` the objectives `・敵の全滅`
and `・＠の死亡`, `COMMON.DAT` the class names `ファイター`, `エンペラー`,
`ドラゴンロード`.

Shift-JIS is ASCII-compatible in the low range, so Latin text can be written as
single bytes and **no glyph slots have to be sacrificed** — unlike the PS1 and
Saturn builds, where every target character costs a slot. Whether the in-game
font actually has ASCII glyphs to render still has to be confirmed in an
emulator.

### Script records

Inside the script resource (`id 1`), text records sit among the VM bytecode. A
record is a NUL-terminated speaker or label string followed by a NUL-terminated
line, with `0x0A` as the in-line break, preceded by a small type/header word and
followed by an `0xFFFFFFFF` terminator. Two record types are known:

| Type | Meaning |
| --- | --- |
| `08` | narration and system text. The label is a tag such as `プロログフラグ` or `エピログフラグ`; the text is the on-screen narration. The victory/defeat block is one such record. |
| `04` | spoken dialogue. The label is the speaker name — `ティアリス`, `騎士ジェリオール`, `ウィリアム候爵`, or `＠` for the player — and the text is the line. |

A read-only dumper walked these and emitted every Shift-JIS run with its
offset: roughly 151 runs in `S01P`, 203 in `S01B`, 44 in `S01E`.

Markers seen inside text, semantics not yet confirmed in-game:

- `＠` — player-character name placeholder.
- `＜` and `＞` — inline control markers.

## File roles

Verified by decoded content:

| Path | Role |
| --- | --- |
| `DATA/COMMON.DAT` | class and unit names, common terms — the glossary source |
| `DATA/GRP00..02/STnn/SnnP.DAT` | prolog: stage title and intro narration |
| `DATA/GRP00..02/STnn/SnnB.DAT` | battle: objectives and in-battle dialogue |
| `DATA/GRP00..02/STnn/SnnE.DAT` | ending and post-battle dialogue |
| `DATA/EP.DAT` | branching epilogues |
| `DATA/GRP00/ST00/CHARMAKE.DAT` | character-creation screen |
| `DATA/SYSTEM.DAT` | UI and system data, mostly graphics — locate menu strings precisely before editing |

Stages ST37–41 carry 296-byte `SnnE.DAT` stubs with no ending text.

This is the same scenario shape the Langrisser V toolkit already models —
intro, battle, ending per scenario.

## Other on-disc formats

| Extension | Format |
| --- | --- |
| `*.TM2` | Sony TIM2 images (`TIM2` magic) — terrain under `TIKEI/` and UI graphics |
| `*.PSS` | PS2 MPEG-PS video (`00 00 01 BA` start code) — opening and ending movies |
| `SND/*.IVB` | audio (`BVII` magic); also `EVT.VBD`, `SE/SE00.HD` + `.BD` (`IECSsreV`) |
| `MODULES/*.IRX` | IOP modules (`\x7fELF`); `IOPRP300.IMG` (`RESET`) is a module pack |

## What is not mapped yet

**Reinsertion.** The VM bytecode references text by offset, so editing a line
shifts every later offset and the script's pointer table would have to be
rebuilt. The record header fields and the VM command that points at each text
record are not mapped, and that is the blocker before any text can be written
back.

**Growth budget.** Assume the ISO9660 layout and per-file sizes are fixed until
a full disc rebuild is shown to boot in PCSX2. Whether text can grow depends on
the `.DAT` sub-table — padding versus repack — which is unmapped.

## Compression debt

The notes carried a register for cases where a byte budget forces wording that
drops nuance, tone, or tutorial and lore detail, to be revisited once the budget
allows. It was empty: no translation had been written yet.
