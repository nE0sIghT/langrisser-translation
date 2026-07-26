# Internal Data Formats (Confirmed)

## ISO layout (relevant files)

- `/L5/SCEN.DAT`
- `/L5/SCEN2.DAT`
- `/L5/SYSTEM.BIN`
- `/SLPS_018.19`

## SCEN.DAT / SCEN2.DAT

Container layout:
1. Global chunk pointer table (`u32`, little-endian)
2. Chunk payloads

Each chunk:
1. Local record offset table (`u16` ascending offsets)
2. Record byte streams (word-oriented script data)
3. Optional non-text suffix data after the text block

Record word format:
- `u16` tokens
- printable tokens are generally `< 0xE000`
- control tokens are mostly high ranges (`0xFxxx`, `0xFFxx`)

Battle chunks `001`-`042` have a suffix asset-slot pointer table immediately
after the text block. Its current reverse-engineering notes and alignment risk
are in `docs/BATTLE_SUFFIX_FORMAT.md`.

## Text stream model

- Script records are token streams, not raw Shift-JIS strings.
- Rendering/logic uses control opcodes and printable token IDs.
- Practical editable representation in tooling:
  - known glyph tokens as chars
  - unknown/control as tags: `<$HHHH>`

## SYSTEM.BIN

Contains:
- 12x12 font plane data (token/glyph usage)
- many short `{FFFF}`-terminated UI/menu strings
- class/skill/item/system labels and message fragments

Menu patching strategy:
- split into short `FFFF`-terminated runs
- decode with canonical token map
- replace mapped runs via fixed dictionary
- preserve run length and control words
- preserve leading literal `0x0000` cells in SYSTEM strings; save-screen
  templates use them as runtime overlay fields for scenario/state numbers,
  so pair-glyph compression must not fold them into the following Latin text

## Control token groups (high-level)

- `F600` — macro family with argument word
- `FB00`/`FBxx` — dialog/control commands with argument semantics in flow
- `FFFC` — line/page separator semantics in text path
- `FFFD`, `FFFE`, `FFFF` — control/termination states in stream handling

## Speaker plate reserves

The first `FFFF`-terminated records in story chunks form a local name-plate
pool. Dialogue records do not point to that pool directly: `FB00 <id>` is a
dialogue/event ID, not a name-record index. Example from chunk 45: record 10
uses `FB00 0003`, while its visible speaker is the local machine-voice plate
(name record 7 in that chunk).

The production reference layer is in the chunk VM block before the text block.
`langrisser/rewrap.py::semantic_plate_slots()` scans validated 12-byte
display/window commands (`0x0B..0x10`) and maps `record = first FB00 record +
text_id`. Plate selection is:

- actor key at `p+6..7`, resolved through the chunk actor-plate table;
- runtime-remapped crowd keys `0xFFE5..0xFFFE`, handled with the conservative
  chunk-wide reserve;
- for commands with actor key `0xFFFF`, `p+9` is either `0xFF` for no plate or
  a zero-based local speaker-pool slot.

`langrisser/scendump.py` uses this same extractor for `# spk:` comments and
`work/l5/scriptdump/all_records.csv`. No hand-written speaker map is canonical.

Detailed dispatch-level evidence is in:
- `docs/DISASM_SUMMARY.md`
