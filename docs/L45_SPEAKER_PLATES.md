# Speaker Name Extraction Notes

This document records the current production method for extracting speaker
plate widths for dialogue line wrapping. The old broad VM-walk task is parked;
the shipped wrapper does not need it.

## Current Production Mechanism

`langrisser/rewrap.py` uses `semantic_plate_slots()` as the authoritative source for
per-record speaker plates. `langrisser/scendump.py` uses the same function to emit
`# spk: <name>` comments and the `speaker` column in `work/l5/scriptdump/all_records.csv`.

The extractor does **not** execute the VM. It scans the chunk VM block for
self-contained 12-byte display/window commands and accepts one when:

- opcode byte `p+0` is `0x0B..0x10`;
- mode bits `(p+3) & 3` are `<= 2`;
- name-visible flag `p+8` is `<= 1`;
- text id `u16@p+10` is within the chunk's text-record range.

**Record index is `pool_size + 1 + text_id`.** The name plates occupy records
`1..pool_size`, so the first text record (text id 0) is `pool_size + 1`. This is
*not* `first_fb_record + text_id`: the first text record can be a non-FB00
location card (chunk 69: pool_size 7, so text id 0 is record 8 "Brenda's
Sickroom"; the first FB00 record is 9).

**Speaker is the zero-based name-pool slot at byte `p+9`:**

- `p+9 < speaker_pool_size` => that local speaker-pool slot;
- `p+9 == 0xFF` => no plate, reserve `0`;
- `p+9 >= speaker_pool_size` (e.g. `58`) => a location/special plate; treat as
  unresolved and keep the conservative chunk-wide pool reserve.

A record can carry more than one display command; the one that draws the plate
has `p+8 == 0` (name visible), so among a record's commands the lowest
`(p+8, position)` wins. (Chunk 69 record 31 has a `p+9=0`/`p+8=1` command before
the real `p+9=1`/`p+8=0` Mariandel command; taking the first gave Sigma.)

> **History.** A prior revision read the speaker from the actor key at `u16@p+6`
> (via `actor_plate_table.field2`) with record = `first_fb + text_id`. A static
> read of the window handler suggested `p+6..7` was the speaker and `p+9` mere
> routing, but the in-game plate test set in `docs/L45_SPEAKER_TEST_SET.md` proved
> it wrong on the verified plated lines: `p+9` is the plate selector and
> `p+6..7` is unrelated routing.

Plate width is the rendered speaker name plus the `0x0001` glyph that the engine
draws next to the name. Continuation pages after `<$FFFD>` do not redraw the
speaker plate and wrap at full width.

## Confirmed Format Facts

1. The VM block begins with a dispatch table, not executable bytecode. Linear
   walking from offset 0 is invalid.
2. Display/window commands are 12-byte opcodes `0x0B..0x10` handled by
   `FUN_80024424`.
3. The speaker selector is the zero-based name-pool slot at `p+9` (`0xFF` = no
   plate; `>= pool_size` = location/special). The actor key at `p+6..7` is
   unrelated routing, not the plate (in-game verified, chunk 69).
4. The actor key at `p+6..7` and the `0x800eba38` actor table still exist, but
   they are research/routing data for this task. The production wrapper does not
   use them to choose the visible speaker plate.
5. The chunk VM header word at `+0x38` is the speaker-name-pool size. It excludes
   location plates and scene captions, so it is safe for fallback reserves.
6. The semantic scan yields a confident production mapping without a full VM
   interpreter. The full VM walk remains useful for research but is not required
   for wrapping.

## Legacy And Parked Work

`traced_plate_slots()` and `display_plate_slots()` in `langrisser/rewrap.py` are
legacy fallbacks. Do not base new work on their broad byte9 scans. The only
production use of `p+9` is inside `semantic_plate_slots()`, after a display
command passes the semantic filter. Actor-key value is not a production guard.

The broad task of reimplementing the VM interpreter remains parked. Do not resume
static VM tracing, actor-state interpretation, or full bytecode emulation unless
a new runtime defect proves the semantic display-command extraction insufficient.

Historical notes below are kept only to prevent repeating rejected paths.

## Work Ledger

This section is the anti-loop checklist. Update it before switching strategies
or after proving a hypothesis false. Do not re-run a closed approach unless new
evidence changes the premise.

Status values:

```text
DONE        confirmed enough to use as a constraint
REJECTED    tested and false; do not reuse
ACTIVE      current line of work
PENDING     next work item
BLOCKED     needs another decoded dependency
PARKED      stopped on purpose; do not resume without a new requirement
```

| Status | Item | Evidence | Next action |
| --- | --- | --- | --- |
| DONE | Text records use 16-bit tokens, not Shift-JIS text. | Round-trip tooling and font/token work. | Keep this as a hard constraint. |
| DONE | Printable token ID equals glyph index for native glyphs. | Font mapping and scenario decode work. | Keep using glyph-index tokens. |
| DONE | Story chunks have an initial local speaker-name pool. | `work/l5/scriptdump` chunk dumps; first `FFFF` records decode as names. | Use as candidate plate list only. |
| DONE | Dialogue records contain `FB00 <id>` markers. | Script dump and token control analysis. | Use `FB00` IDs as dialogue segment keys. |
| DONE | One record can contain multiple `FB00` markers. | Chunk 45 has Sigma/Lambda switches in one record. | Rewrap must be segment-aware. |
| REJECTED | `FB00 <id>` directly indexes the speaker-name pool. | IDs exceed local name count and do not match visible speakers. | Never use as speaker slot. |
| REJECTED | The high byte before `FF0B` is a direct speaker slot. | Chunk 45 maps `0x0013` to Sigma by this heuristic, but the line is Lambda. | Remove or downgrade `vm_direct_00` confirmed output. |
| DONE | A chunk-wide widest-POOL reserve with short plate names is acceptable. | Early breaks came from location plates leaking into the bound and from long titled names; with the VM-header pool size and <= 5-cell plates the slack is 2-3 cells. | Shipped in `langrisser/rewrap.py`; see the Decision section. |
| REJECTED | Derive a per-record plate bound from JP first-line widths (21 - width). | The JP script relies on the engine's anywhere-wrap: dialogue first lines reach 81 cells, so the bound goes negative/below the real plate. | Do not retry; JP line breaks carry no plate evidence. |
| REJECTED | A record-wide reserve is enough. | Records can switch speakers after an internal `FB00`. | Use active reserve changes after `FB00`. |
| DONE | `langrisser/speakers.py` no longer emits heuristic speaker confirmations. | Chunk 45 now reports `confirmed=0`; former `vm_direct_00` rows are `vm_state_byte_rejected/unresolved`. | Use it only as an evidence dumper until runtime behavior is decoded. |
| DONE | `langrisser/vm_dialog_refs.py` is documented as legacy evidence only. | Its header no longer calls the `FF0B` patterns confirmed command shapes. | Do not use it for execution order. |
| DONE | VM dispatcher is byte-oriented. | Dispatcher reads one opcode byte from `gp + 0x30c`. | Parse VM command starts by byte offset. |
| DONE | Opcodes `0x0b..0x10` reach handler `0x80024424`. | Jump table at `0x80010250`. | Decode this handler's inputs precisely. |
| DONE | Handler `0x80024424` writes window/dialogue state fields. | Writes to `0x8011a024` structure. | Identify which field resolves final speaker plate. |
| DONE | Display payload byte 9 is the visible speaker slot after semantic display-command filtering. | Chunk 45 maps records 10/11 to slot 6 (`機械音声`), matching the visible Machine plate; chunk 69 verified plated lines reject the actor-key model. | Use `p+9` only inside `semantic_plate_slots()` after its opcode/mode/name/text-id filters pass. |
| DONE | Continuation pages after `FFFD` do not redraw the speaker plate. | User playtest: first page can show a blue speaker plate, while the next page in the same record has no name; chunk 1 record 96 demonstrates this. | Reset reserve to zero after page breaks in `langrisser/rewrap.py`. |
| DONE | Runtime actor/plate lookup table exists. | `0x800b2da4` searches table at `0x800eba38` with count `0x800eba46`. | Decode the table source in each chunk. |
| DONE | Map chunk-local data to runtime `0x800eba38` table. | Header `u32 +0x14` is the table offset; low byte of header `u32 +0x2c` is the entry count. | Use `actor_plate_table()` in `langrisser/speakers.py`. |
| DONE | Decode the static `0x800eba38` table shape. | Static parser matches `0x800b2da4`: `u16 key`, `u8 field2`, `u8 field3`. | Decode field semantics in the VM handler context. |
| REJECTED | Word-only `FF0B ... FFFF FFFF` pattern scan is executable VM command parsing. | Chunk 45 starts at opcode `0x00`; that handler length-skips payload bytes containing `FF0B` patterns. | Keep word patterns as evidence only, never as command order. |
| DONE | Build a bytecode trace for the VM stream. | `langrisser/speakers.py --trace-out` reaches real chunk 45 display commands in byte order. | Use trace rows as evidence, not speaker mapping. |
| DONE | Decode enough opcode lengths to trace chunk 45 into display commands. | Known lengths include `00`, `04/05/09`, `06/07/08/0a`, `0b..10`, `14..1b`, `23..25`, `63`, `6f`, `78`. | Keep extending from disassembly when the tracer stops. |
| DONE | Decode branch targets for conditional opcode `0x7b`. | Chunk 45 `0x7b` at rel `0x0246` yields targets `0x024c` and `0x025c` from the `0x80025a1c -> 0x80026400` path. | Keep it as CFG branch evidence until the runtime condition is modeled. |
| PARKED | Continue bytecode tracing after the first `0x7b` branch. | CFG trace reaches more display commands after both `0x7b` targets. | Decode the next stopping opcode reported by `--trace-out`. |
| PARKED | Resolve non-linear VM entry/control flow for chunks 65/107. | Linear trace reaches `opcode 00` with skip length `0xfc00` after the first `0x04` command. | Determine whether this is an exit sentinel, alternate entrypoint, or conditional path. |
| PARKED | Decode `0x800a39a4` `FBxx` text-control handler. | Dispatch table sends `FB` family there. | Confirm how text `FB00 <id>` links to VM state. |
| PARKED | Implement trusted speaker extraction API. | Requires resolved table and command semantics. | Emit only dispatch-verified mappings. |
| DONE | Integrate display-command plate reserves into `langrisser/rewrap.py`. | `semantic_plate_slots()` reads the local speaker slot from display byte `p+9`; `0xFF` means no plate and values outside the local pool keep the conservative chunk-wide pool reserve. | Current production path. |
| DONE | Validate against chunk 45 in-game bad lines. | Simulated render: no line exceeds 21 cells with the pool reserve. | Confirm visually in the next playtest. |
| DONE | VM block begins with a u32 script offset table. | Ascending u32 offsets at vm start in 131/131 chunks; tracer wrongly starts at offset 0 (the table) and reads entry `0x44` as an opcode. | Start the walk at the first table entry; decode opcodes `0x28`/`0x44`. |
| REJECTED | Recover display rows by opcode-only `0x0B..0x10` 12-byte scans. | Requiring the complete `0..N-1` `text_id` set to resolve uniquely succeeds in 0/131 chunks; lenient filters admit false-positive windows. | Use the semantic opcode/mode/name/text-id filter instead. |
| DONE | "Town" plate is speaker 町人 (pool slot 7), not a location. | Chunk 4 name pool slot 7 = 町人; actor-key extraction resolves its lines to slot 7. | Treat it as an ordinary speaker plate. |
| DONE | Stop zeroing the safe-bound reserve for `<$FB00>`-less spoken records. | `semantic_plate_slots()` maps display `text_id` contiguously from the first `FB00` record, so interleaved FB00-less spoken lines such as chunk 4 record 79 get a plate reserve or crowd fallback. | Keep this behavior in rewrap. |

## Archived Work Plan

Do not execute this plan for wrapping polish unless the parked decision is
explicitly reversed. It remains here to prevent repeating already tested
reverse-engineering paths.

1. Make `langrisser/speakers.py` conservative. DONE.
   - Remove any `confirmed` result based only on the rejected high-byte
     heuristic.
   - Keep such rows as evidence with `unresolved` confidence if they are useful.
   - Success criterion: no known-false chunk 45 row is marked confirmed.
   - Result: chunk 45 writes `confirmed=0`; the old high-byte evidence is
     labeled `vm_state_byte_rejected`.
2. Decode the chunk loader structure. DONE.
   - Start at `0x8003b44c`.
   - Trace how loaded-header offsets become `0x800eba38`, `0x800eba46`,
     `0x800eb2ac`, `0x800eb8fc`, and `0x800eb574`.
   - Success criterion: a script can locate and dump the `0x800eba38` table
     for sampled chunks from static SCEN data.
   - Result: `langrisser/speakers.py` now parses `0x800eba38` from chunk
     header `+0x14` and count from header `+0x2c`.
3. Decode the `0x800eba38` table format. DONE.
   - Use `0x800b2da4` as the reference behavior.
   - Confirm entry width and field semantics on multiple chunks.
   - Success criterion: static dumps match the table shape expected by the
     function: `u16 key`, `u8 field2`, `u8 field3`.
   - Result: sampled chunks decode as 4-byte entries. Field semantics still
     depend on the VM handler and remain part of the next step.
4. Replace word-only VM pseudo-record parsing with byte-accurate command
   tracing. DONE.
   - Treat the VM stream as bytes.
   - Do not treat `FF0B` patterns inside skipped payload blocks as command
     starts.
   - Success criterion: chunk 45 trace reaches the actual display command
     sites in execution order.
   - Result: `python3 -m langrisser.speakers --chunk 45 --trace-out
     work/vm_dialog_refs/vm_trace_045.csv` reaches 30 display commands per
     SCEN/SCEN2 copy and stops at opcode `0x7b`.
5. Decode branch/conditional VM opcodes required by chunk 45. DONE.
   - Decode opcode `0x7b`, dispatched through `0x80025a1c`.
   - Confirm whether it changes the VM pointer by conditional skip, table
     branch, or fallthrough.
   - Success criterion: chunk 45 trace continues past VM rel `0x0246` without
     guessing.
   - Result: `0x7b` reads one ignored byte, then one of two relative skip
     lengths. For chunk 45 at rel `0x0246`, the possible next offsets are
     `0x024c` and `0x025c`.
6. Continue bytecode tracing after the first `0x7b` branch. PARKED.
   - Extend the known opcode length set from disassembly whenever `--trace-out`
     stops.
   - Keep branch targets as CFG evidence unless the runtime condition is fully
     modeled.
   - Success criterion: chunk 45 trace reaches the end of its reachable display
     command graph without unknown opcodes.
7. Resolve non-linear entry/control flow for chunks 65 and 107. PARKED.
   - Linear trace from header stream start reaches `opcode 00` with skip length
     `0xfc00`.
   - Determine whether this is an intentional stream exit, alternate entrypoint,
     or an unmodeled conditional branch.
   - Success criterion: these chunks can be traced into their real dialogue
     display commands.
8. Reimplement the relevant subset of `0x80024424`.
   - Model only fields needed for dialogue text and speaker/window plate.
   - Use the static `0x800eba38` table where the handler does runtime lookup.
   - Success criterion: chunk 45 resolves Sigma/Lambda speaker switches
     without manual names.
9. Decode or confirm `0x800a39a4`.
   - Verify whether `FB00 <id>` only marks the text segment or also triggers
     state selection.
   - Success criterion: no missing link remains between text `FB00` markers and
     VM command targets.
10. Integrate with rewrap.
   - Add an API returning `chunk -> fb_id -> speaker_name/reserve`.
   - Update wrapping to change active reserve after every `FB00` marker.
   - Success criterion: known bad chunk 45 lines wrap without premature word
     splits.
11. Run required build checks.
   - `python3 -m langrisser.verify_roundtrip`
   - `python3 -m langrisser.rewrap`
   - `python3 -m langrisser.validate_translation`
   - `python3 -m langrisser.build_ppf`
   - Success criterion: failures, if any, are unrelated WIP translation files
     and are documented clearly.

## Do Not Repeat Without New Evidence

- Do not use `FB00 <id>` as a name-pool index.
- Do not treat the high byte of a nearby state word as a speaker slot.
- Do not mark any static VM row `confirmed` unless it follows decoded runtime
  behavior.
- Do not solve this with hand-written per-chunk speaker overrides.
- Do not resume broad static VM tracing to improve wrapping unless a concrete
  playtest issue remains after semantic display-command extraction and the
  pool-bound fallback.
- Do not derive plate bounds from JP line breaks; the JP script engine-wraps
  mid-line and its first lines exceed the window.
- Do not parse the VM stream only as aligned 16-bit records when interpreting
  opcode dispatch.
- Do not treat `FF0B <flags> FFFF FFFF` pattern matches inside opcode `0x00`
  length-skipped payload as executed display commands.
- Do not wire the legacy `langrisser/speakers.py` tracer into rewrap; use
  `semantic_plate_slots()` unless a new defect proves it insufficient.

## Confirmed Data Model

- Story chunks begin with a local pool of `FFFF`-terminated speaker-name
  records.
- Dialogue text records contain `FB00 <id>` markers.
- `FB00 <id>` is not a direct speaker-name record index. It is a dialogue or
  event reference used by the VM/text system.
- One text record may contain multiple `FB00 <id>` markers, so speaker reserve
  has to be segment-aware, not just record-aware.
- SCEN and SCEN2 text blocks are byte-identical. Speaker extraction should use
  SCEN only and apply to both during build.

## Why Static Width Is Wrong

The current fallback strategy reserves space for the widest local speaker plate
in a chunk. That is safe but overly pessimistic. It causes visible over-wrapping
when the real speaker has a short name.

Chunk 45 is the key failing case. A Lambda line was wrapped as if a larger or
incorrect plate were active, producing a break inside `just`. The same chunk has
records where Sigma and Lambda alternate inside the same record, which proves
that a per-record reserve is not precise enough.

## VM Block Observations

Several story chunks contain a VM block before the text record block.

Observed VM header fields:

```text
0x30  u32 command stream start
0x38  u32 local name count in sampled chunks
0x3c  u32 VM block size
```

Examples where `0x38` matched the local name-pool count:

```text
chunk 000: 7
chunk 037: 7
chunk 045: 9
chunk 065: 9
chunk 088: 7
chunk 107: 10
```

Chunk 45 observed VM block:

```text
chunk start: 0x78f000
text block base: 0x14b8
record count: 51
VM offset: 0x1194
VM size: 0x0324
stream start: 0x0060
name count: 9
```

## Dispatcher Evidence

The main VM dispatcher around `0x8001d6a8` reads one opcode byte from the VM
instruction pointer stored at `gp + 0x30c`. It stores the current opcode at
`gp + 0x31c`. Opcodes below `0x7f` dispatch through the table at `0x80010250`.

Relevant entries from the table:

```text
0x00 -> 0x8001d738
0x01 -> 0x8001d764
0x02 -> 0x8001d7d0
0x03 -> 0x8001d808
0x04 -> 0x8001d854
0x05 -> 0x8001d854
0x06 -> 0x8001d864
0x07 -> 0x8001d864
0x08 -> 0x8001d864
0x09 -> 0x8001d854
0x0a -> 0x8001d864
0x0b..0x10 -> 0x8001d874
```

The shared handler for opcodes `0x0b..0x10` calls `0x80024424`.

## Handler `0x80024424`

This handler consumes payload bytes after an opcode in the `0x0b..0x10` range
and populates the runtime window/dialogue state structure at `0x8011a024`.

Observed field writes:

```text
payload[0]              -> 0x8011a02a  struct +6
payload[1] low nibble   -> 0x8011a02b  struct +7
payload[1] high nibble  -> 0x8011a025  struct +1
payload[2]              -> flags
payload[3:5]            -> s7
payload[5:7]            -> s5
payload[7]              -> s6
payload[8]              -> t2
payload[9:11]           -> text/dialogue id
```

The text window path later reads these structure fields. In particular:

```text
0x800a3ed4..0x800a3ee0:
  a0 = lhu gp + 0x4fc
  a1 = lbu gp + 0x504
  a2 = lbu gp + 0x50c

0x800a3f80..0x800a3fbc:
  struct +8 -> gp + 0x4f4
  struct +7 -> gp + 0x504
  struct +2 -> gp + 0x50c
```

This strongly suggests that `struct +7` is involved in speaker plate or window
state selection, but the static mapping from VM bytes to final speaker plate is
not fully decoded yet.

## Bytecode Trace Evidence

`langrisser/speakers.py` now has a conservative VM bytecode tracer:

```bash
python3 -m langrisser.speakers \
  --chunk 45 \
  --out work/vm_dialog_refs/speaker_045_check.csv \
  --trace-out work/vm_dialog_refs/vm_trace_045.csv
```

Observed result:

```text
speaker rows: 96, confirmed 0, unresolved 96
linear trace rows before CFG branch support: 148 across SCEN and SCEN2
linear display commands before CFG branch support: 60 total, 30 per file copy
first branch: opcode 0x7b at VM rel 0x0246 in both SCEN and SCEN2
```

First chunk 45 display commands reached by the trace:

```text
rel 0x0078  opcode 0x0b  text id 0000  byte9 06
rel 0x008a  opcode 0x0b  text id 0001  byte9 06
rel 0x00b4  opcode 0x0b  text id 0002  byte9 ff
rel 0x00c0  opcode 0x0b  text id 0003  byte9 07
rel 0x00cc  opcode 0x0b  text id 0004  byte9 ff
rel 0x00d8  opcode 0x0b  text id 0005  byte9 07
rel 0x00f6  opcode 0x0b  text id 0006  byte9 ff
rel 0x010a  opcode 0x0b  text id 0007  byte9 07
```

These rows prove that byte tracing reaches executed display commands. They do
not yet prove speaker names.

Opcode length facts added to the tracer:

```text
0x00        length = 4 + u16(payload[1:3]); invalid if it exits the VM block
0x04/05/09 length = opcode + u8 + 0x80022b24 helper + u16
0x06/07/08/0a length = 4
0x0b..10   length = 12; calls 0x80024424
0x14/15/25/6f/78 length = 4
0x11/16/18/19/1a/1b/23/24/26/63 length = 2
0x17        length = 6 normally, 8 when the helper's first byte is 0xfe
0x7b        CFG branch; possible next offsets are p+2+u16(p+2)+2 and p+4+u16(p+4)+2
```

Known current stops:

```text
chunk 045: first branch opcode 0x7b at VM rel 0x0246, targets 0x024c and 0x025c
chunk 065: opcode 0x00 at VM rel 0x0064, skip length 0xfc00 exits VM block
chunk 107: opcode 0x00 at VM rel 0x0064, skip length 0xfc00 exits VM block
```

The chunk 65/107 result must not be treated as a valid linear trace into
dialogue. It marks missing control-flow or entrypoint interpretation.

## Actor/Plate Lookup Table Evidence

Function `0x800b2da4` searches a runtime table referenced by globals:

```text
0x800eba38  table pointer
0x800eba46  table entry count
```

Observed behavior:

```text
for each entry:
  if entry.u16 == actor_or_state_id:
    *a1 = entry.byte2
    *a2 = entry.byte3
    return
*a1 = 0xff
*a2 = 0
```

This table likely maps actor or scene-state IDs to text/window/speaker fields.
The table is loaded from chunk data by the scenario loader.

Loader evidence around `0x8003b44c`:

```text
0x800eba46 = low byte of header u32 + 0x2c
0x800eba38 = data base + *(header u32 + 0x14)
0x800eb2ac = *(loaded header + 0x38)
0x800eb8fc = data base + *(loaded header + 0x3c)
0x800eb574 = data base + *(loaded header + 0x34)
```

The `0x800eba38` table location and entry shape are now decoded:

```text
chunk header + 0x14  u32 table offset
chunk header + 0x2c  u32 entry count, low byte used by the game

entry +0x00  u16 key
entry +0x02  u8  field2
entry +0x03  u8  field3
```

Sample static dumps:

```text
chunk 045: 0007:00:00 0008:01:00
chunk 065: 0012:00:00 0040:01:00 008F:02:00 0090:02:00 ...
chunk 107: 0012:00:00 0040:01:00 00E1:02:01 00E2:02:01 ...
```

Field semantics still have to be interpreted in the VM handler context.

## Text Control Dispatcher Evidence

The text token dispatcher at `0x800a36b4` handles printable tokens and high
control families.

For high-byte `0xf6..0xfe`, the dispatch table at `0x80015be4` includes:

```text
F6 -> 0x800a3a90
F7 -> 0x800a3b00
F8 -> 0x800a3b00
F9 -> 0x800a3b00
FA -> 0x800a3bfc
FB -> 0x800a39a4
FC -> 0x800a3b00
FD -> 0x800a3a34
FE -> 0x800a39f8
```

`FBxx` handling starts at `0x800a39a4`. It consumes argument bytes and can call
`0x800193f0`. This path still needs full interpretation because `FB00 <id>` is
the visible marker used in text records.

## Rejected Hypotheses

### `FB00 <id>` is the speaker slot

False. The `FB00` argument can exceed the local name count and does not match
name-pool positions.

### The high byte before `FF0B` is a direct speaker slot

False. The current untracked `langrisser/speakers.py` can label rows as
`vm_direct_00` when the high byte of the preceding state word looks like a
speaker slot. Chunk 45 disproves this.

Example failure:

```text
FB id 0x0013 should use Lambda, but the current heuristic maps it to Sigma.
FB id 0x0025 should also use Lambda, but the current heuristic can map it to a
different local name.
```

Therefore this heuristic must not be used as confirmed data.

### Initial name-pool order is enough

False. The local name pool is only a list of available plates. The dialogue VM
selects among those plates using additional state.

### `FF0B` pattern scanning is command execution

False. The old evidence parser scans word-oriented pseudo-records ending in:

```text
FF0B <flags> FFFF FFFF
```

These patterns exist in chunk data, but they are not sufficient to prove VM
execution order. Chunk 45 starts its VM stream at opcode `0x00`; that handler
uses a length field and can skip bytes containing `FF0B` patterns as embedded
payload data.

Therefore `FF0B` pattern rows may be useful evidence, but they must not be
treated as executed display commands. The extractor needs a bytecode trace from
the VM dispatcher.

## Required Extractor Behavior

The production speaker extractor should:

1. Parse the local name pool.
2. Parse the VM header and command stream.
3. Decode the per-chunk actor/name lookup table loaded into `0x800eba38`.
4. Trace VM bytecode from the real dispatcher entry point.
5. Interpret `0x0b..0x10` commands only when the trace reaches them.
6. Resolve each targeted `FB00 <id>` to a speaker name slot.
7. Mark unresolved cases explicitly instead of guessing.
8. Provide an API for rewrap:

```text
chunk index -> FB id -> speaker name -> first-line reserve
```

## Rewrap Integration Requirement

`langrisser/rewrap.py` must not use one reserve for a whole chunk when a trusted
display row gives a tighter record-level reserve. A fully segment-level reserve
would still require resolving multiple `FB00` markers inside one record.

Required behavior:

```text
When a FB00 marker is emitted:
  update the active speaker reserve for subsequent printable text.

When a page break is emitted:
  reset the active reserve to zero because the speaker plate is not redrawn.

When no speaker is known:
  fall back to the conservative reserve and report the unresolved FB id.
```

Choice records and non-dialogue records must keep their existing special rules.

## Current Implementation Risk

`langrisser/speakers.py` is useful as an evidence dumper. It must remain
conservative: no row may be marked `confirmed` unless it follows decoded
runtime behavior. The rejected high-byte heuristic is now emitted as
`vm_state_byte_rejected` with `unresolved` confidence.

## Next Reverse-Engineering Steps

1. Build a bytecode tracer for the VM stream, starting with opcodes observed in
   chunk 45.
2. Fully decode `0x800a39a4` and adjacent `FBxx` handling.
3. Reimplement the `0x80024424` command effect for the subset that selects
   dialogue text and speaker plate state.
4. Validate against chunk 45:
   - Sigma lines resolve to Sigma.
   - Lambda lines resolve to Lambda.
   - multi-speaker records switch reserve after each `FB00`.
5. Run rewrap and verify the known bad lines no longer split words early.
