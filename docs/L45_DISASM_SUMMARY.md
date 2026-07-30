# Disassembly Summary (Text/Control Path)

This is the structured subset needed by production translation tooling.

## Core dispatcher

- Main stream dispatcher: `FUN_800A36B4` (`0x800A36B4`)

Behavior classes:
- `< 0xE000`: printable token path
- `0xFFF3..0xFFFF`: jump-table dispatch (`0x80015BAC`)
- `0xF6xx..0xFExx`: jump-table dispatch (`0x80015BE4`)

## Confirmed control semantics

- `F600`:
  - macro-like opcode
  - consumes the next word as argument
  - expanded through macro path before glyph emission

- `FBxx` (including `FB00`):
  - non-printing control family
  - command/flow semantics, not direct glyph emission
  - argument handling must be preserved in token stream

- `FFFC`:
  - explicit separator handling in text path
  - also participates in UI flow transitions

- `FFFD`, `FFFE`, `FFFF`:
  - control/termination branches in stream processing
  - must be preserved exactly in record structure

## Practical requirement for repacker

During EN insertion:
1. Preserve order and positions of control tokens.
2. Preserve argument words for control opcodes (`F600`, `FB00` family usage in stream).
3. Replace only printable slots.
4. Keep record-level size constraints compatible with original container.

## Why this matters

If control-token placement/arguments drift, text can appear as garbage, paging can break, or script flow can desync even when patch applies successfully.
