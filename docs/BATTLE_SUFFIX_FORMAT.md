# Battle Chunk Suffix Notes

This document tracks the reverse-engineering state for the non-text payload
that follows battle chunk text blocks. It is the working log for battle text
block growth. Update it whenever a hypothesis is confirmed or rejected so the
same dead ends are not repeated.

## Scope

- Applies to battle chunks `001` through `042` in `SCEN.DAT` and `SCEN2.DAT`.
- The text block starts at `vm_off + vm_size`.
- The battle suffix starts at `text_block.base + text_block.size`.
- The diagnostic tool is `langrisser/battle_suffix.py`.

## Progress Log

- 2026-06-13: Inspected the newer `langrisser3-english` project. Its D00/ISO
  growth strategy is useful as a reference, but not directly applicable to
  this PS1 disc because our immediate failure is inside a battle chunk suffix.
- 2026-06-13: Disassembled the reported invalid-read PCs. `0x800B3FA4` is a
  copy helper, not the root loader. The producer path is around `0x800B2CE4`.
- 2026-06-13: Rejected the earlier `suffix+4` offset-base model. Runtime uses
  `asset_pointer[i] = suffix_base + u32(suffix_base + 4*i)`.
- 2026-06-13: Confirmed all original battle suffix starts are 4-byte aligned.
  The known broken chunk `002` build shifted the suffix by `0x13A`, creating
  a `2 mod 4` suffix start. This is now the leading failure hypothesis.
- 2026-06-13: Updated `langrisser/battle_suffix.py` to report actor-derived
  asset slots and slot offsets. Updated `langrisser/sceninsert.py` and
  `langrisser/validate_translation.py` so grown text blocks are 4-byte aligned and budget
  checks include that padding.
- 2026-06-13: Restored the fuller chunk `002` translation and built it with the
  suffix shifted by `0x13C`, leaving the suffix start 4-byte aligned. In-game
  testing confirmed the battle images/portraits stayed intact.

## Current Working Model

### Chunk header fields used by the suffix path

The battle chunk header contains a small actor table descriptor:

```text
chunk+0x14  u32le actor_table_offset
chunk+0x2C  u32le actor_table_count in the low byte
```

Actor table entries are 4 bytes each. The exact semantics are still partly
open, but the third byte is confirmed as a suffix asset slot selector for the
sprite/portrait path:

```text
actor+0x00  u16le actor/key candidate
actor+0x02  u8    asset slot index used by suffix pointer table
actor+0x03  u8    flags/variant candidate
```

### Suffix pointer table

The suffix starts with a runtime pointer table. The table length is not stored
as a separate field; the loader scans the actor table, finds the maximum asset
slot byte, and reads `max_slot + 1` `u32le` offsets from the suffix start.

```text
suffix+0x00  u32le slot_offset[0]
suffix+0x04  u32le slot_offset[1]
...
suffix+4*N   u32le slot_offset[N]
```

Runtime pointer construction is:

```text
asset_pointer[i] = suffix_base + u32le(suffix_base + 4*i)
```

So offsets are relative to `suffix_base`, not to `suffix_base + 4`.

Example, chunk `002`:

```text
text_end / suffix_base  0x61A8
suffix_len              0x15E58
actor_entries           7
asset_slots             6
table_bytes             0x18
slot offsets            0x15714,0x18,0x4C88,0x7980,0xB308,0xECBC
slot 0 pointer          suffix+0x15714 = 0x1B8BC
slot 1 pointer          suffix+0x18    = 0x61C0
```

Slot 1 normally starts immediately after the offset table. Slot 0 often points
near the end of the suffix and may be a default/empty asset or another special
asset. Do not sort the offset table when rebuilding; slot order is semantic.

## Disassembly Evidence

### `0x8003B44C` chunk setup path

This function initializes battle chunk state. Relevant observed behavior:

- Computes pointers by adding chunk-local offsets to the loaded chunk base.
- Computes the text/suffix area through the text block size.
- Stores the suffix/table base in globals later consumed by the sprite path.

Important consequence: the loader does not obviously hardcode the original
battle suffix offset. If the text block size changes and remains structurally
valid, the suffix base is recomputed from the current chunk data.

### `0x800B2CE4` suffix asset pointer table builder

Confirmed behavior:

1. Fill 64 entries at `0x8010A770..0x8010A86C` with the current suffix base.
2. Scan the current actor table and find the largest third byte (`asset slot`).
3. For every slot `0..max_slot`, read `u32le(suffix_base + 4*slot)`.
4. Store `suffix_base + offset` into the runtime pointer table.

This is the current strongest evidence for the suffix table format.

### `0x800B319C` / `0x800B3084` asset consumers

These functions index the runtime pointer table at `0x8010A770` by asset id
and then pass the selected asset header/payload into decode/render helpers.

Observed fields in the asset header are read as halfwords/bytes around:

```text
asset+0x04
asset+0x1A..0x28
asset+0x2A + 2*variant
asset+0x2E + 2*variant
```

The detailed per-asset payload format is still open.

### `0x800B3FA4` is not the root loader

`0x800B3FA4` / `0x800B3FA8` is a copy helper. Invalid reads there mean the
source pointer was already bad. Treating this function as the root format
parser is a dead end.

## Alignment Finding

All original battle chunk suffix starts are 4-byte aligned. This matters
because the suffix pointer table is read with `lw` instructions.

The previously broken chunk `002` build shifted the suffix by `0x13A`, which
is `2 mod 4`. That would move the suffix table to an unaligned address.
This matches the newer Langrisser III tooling note that sprite data after a
text block must stay 4-byte aligned; otherwise sprite data shifts by 2 bytes
and corrupts on screen.

Confirmed rule: battle text block growth is safe when the rebuilt text block
size keeps `text_block.base + text_block.size` 4-byte aligned and all file/chunk
size invariants still pass. The old chunk `002` regression came from an
unaligned `2 mod 4` suffix start, not from moving the suffix.

## Hypotheses

| ID | Hypothesis | Status | Evidence / next action |
| --- | --- | --- | --- |
| H-001 | Battle suffix starts with `payload_size`, then `table_size`, with offsets relative to `suffix+4`. | Rejected | Disassembly at `0x800B2CE4` reads `u32(suffix+4*i)` and stores `suffix+that_value`. The old `payload_size` was actually slot 0's offset. |
| H-002 | Invalid read PCs `0x800B3FA4`/`0x800B3FA8` identify the root suffix parser. | Rejected | Those addresses are a copy helper. The bad source pointer is produced by earlier asset-table code. |
| H-003 | Actor table byte 2 selects the suffix asset slot. | Confirmed enough for tooling | `0x800B2CE4` scans byte 2 over actor entries, uses the maximum as the count of suffix offsets to read, and downstream code indexes the resulting pointer table. |
| H-004 | The game hardcodes the original suffix offset. | Not supported | `0x8003B44C` and `0x800B2CE4` use the current computed suffix base. If growth failed, another invariant was broken. |
| H-005 | The chunk `002` portrait regression came from 2-byte suffix misalignment, not from suffix movement itself. | Confirmed | Original battle suffix starts are all 4-byte aligned; the broken build shifted chunk `002` by `0x13A`. A restored chunk `002` build shifted the suffix by `0x13C`, kept it 4-byte aligned, and passed in-game image/portrait testing. |
| H-006 | Slot 0 is a default/empty asset. | Open | Slot 0 often points to a tail region, sometimes all zeroes, sometimes structured data. It must be preserved and not sorted away. |
| H-007 | Full data-track growth via rebuilt BIN/CUE would solve battle chunk growth. | Rejected for this issue | It may solve external disc capacity, but it does not fix malformed or misaligned battle suffix data inside a chunk. |

## Current Plan

1. Update the suffix diagnostic tooling to reflect the confirmed slot-offset
   format. Done in `langrisser/battle_suffix.py`.
2. Change the SCEN inserter so a grown text block is padded to a 4-byte size
   before the suffix is appended. Done in `langrisser/sceninsert.py`.
3. Update validation so budget checks include the 4-byte growth padding cost.
   Done in `langrisser/validate_translation.py`.
4. Produce a controlled build that allows battle chunk growth only when the
   resulting suffix start remains 4-byte aligned. Done with chunk `002`.
5. If the controlled aligned build fixes the portrait regression, replace the
   current battle-only block-budget rule with an alignment-aware rule. Done.
6. If a future aligned growth build still breaks assets, continue
   with dynamic tracing of the producer/consumer path around `0x800B2CE4`,
   `0x800B319C`, and `0x800B3084`.

## Current Safety Rule

Normal builds may grow battle text blocks as long as the rebuilt suffix start
remains 4-byte aligned and the fixed-size SCEN/SCEN2 repack validates. The
inserter and validator account for this padding automatically.

Use block-budget mode only as a regression diagnostic:

```bash
python3 -m langrisser.validate_translation <chunk> --budget-mode block
```

This keeps the suffix byte-identical at its original offset. It is safe but
may force unnecessary compression; it is no longer the default budget rule.
