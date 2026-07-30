#!/usr/bin/env python3
"""Shared read model for the Saturn SCEN.DAT container.

`SCEN.DAT` is a top-level catalog of 131 payload blocks; each block has a fixed
0x30-byte header of section offsets, and its scenario text lives in the
`resource_table.field_3c` local index table. This module holds the parsing that
both `saturn_scen_scan.py` (structural diagnostics) and `saturn_scen_text.py`
(text dump) need, so neither reimplements it. All multi-byte fields use the
Saturn on-disc big-endian order by default. See docs/L45_SATURN_DISC_FORMAT.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from langrisser.binfmt import BE, ByteOrder

SECTOR = 0x800
TEXT_TERMINATORS = {0xFFFE, 0xFFFF}


def align_up(value: int, align: int = SECTOR) -> int:
    return (value + align - 1) // align * align


def parse_catalog(data: bytes, order: ByteOrder = BE) -> list[tuple[int, int]]:
    """Return `(start_offset, used_size)` for every payload block.

    The catalog is `u32 count` followed by `count` `(start_sector, used_size)`
    pairs; `start_sector` is in 0x800-byte units relative to the file.
    """
    count = order.u32(data, 0)
    if not (0 < count < 0x10000 and 4 + count * 8 <= len(data)):
        return []
    return [
        (order.u32(data, 4 + i * 8) * SECTOR, order.u32(data, 8 + i * 8))
        for i in range(count)
    ]


@dataclass(frozen=True)
class BlockHeader:
    resource_table_offset: int
    category: int
    sub_id: int
    section0_offset: int
    section1_offset: int
    record_index_offset: int
    resource_map_offset: int
    fields: tuple[int, ...]  # field_18..field_2c


def parse_block_header(data: bytes, start: int, used: int, order: ByteOrder = BE) -> BlockHeader | None:
    """Parse the fixed 0x30-byte block header, or None if it is out of range."""
    if used < 0x30 or start + 0x30 > len(data):
        return None
    return BlockHeader(
        resource_table_offset=order.u32(data, start + 0x00),
        category=order.u16(data, start + 0x04),
        sub_id=order.u16(data, start + 0x06),
        section0_offset=order.u32(data, start + 0x08),
        section1_offset=order.u32(data, start + 0x0C),
        record_index_offset=order.u32(data, start + 0x10),
        resource_map_offset=order.u32(data, start + 0x14),
        fields=tuple(order.u32(data, start + off) for off in range(0x18, 0x30, 4)),
    )


def local_index_layout(data: bytes, start: int, used: int, order: ByteOrder = BE) -> tuple[int, int, list[int]] | None:
    """Return `(base, total_size, offsets)` of the field_3c local index table.

    The table is the scenario text pool: `u32 total_size`, then `u16` entry
    offsets (count derived from the first offset), then the entry payloads.
    Returns None if the structure does not validate.
    """
    if used < 0x44:
        return None
    resource_table_offset = order.u32(data, start)
    if not (0 <= resource_table_offset <= used - 0x44):
        return None
    table_base = start + resource_table_offset
    field_3c = order.u32(data, table_base + 0x3C)
    base = table_base + field_3c
    if base + 6 > len(data):
        return None
    total_size = order.u32(data, base)
    if not (4 <= total_size <= used - resource_table_offset - field_3c):
        return None
    first_offset = order.u16(data, base + 4)
    if first_offset < 6 or (first_offset - 4) % 2:
        return None
    count = (first_offset - 4) // 2
    offsets = [order.u16(data, base + 4 + i * 2) for i in range(count)]
    for i, off in enumerate(offsets):
        next_off = offsets[i + 1] if i + 1 < count else total_size
        if not (first_offset <= off <= next_off <= total_size):
            return None
    return base, total_size, offsets


def local_index_entries(data: bytes, start: int, used: int, order: ByteOrder = BE) -> list[list[int]] | None:
    """Return the token-word entries of a block's field_3c text table, or None."""
    layout = local_index_layout(data, start, used, order)
    if layout is None:
        return None
    base, total_size, offsets = layout
    entries: list[list[int]] = []
    for i, off in enumerate(offsets):
        next_off = offsets[i + 1] if i + 1 < len(offsets) else total_size
        entries.append([order.u16(data, base + off + 2 * j) for j in range((next_off - off) // 2)])
    return entries


class TableTooLarge(ValueError):
    """Raised when rebuilt text entries do not fit the original table size."""


def build_local_index_table(entries: list[list[int]], total_size: int, order: ByteOrder = BE) -> bytes:
    """Rebuild the field_3c table region for `entries`, padded to `total_size`.

    The region is fixed length so nothing after it in the block moves: any
    unused tail is zero-padded. Entry count and order must be preserved by the
    caller; the engine addresses entries through the regenerated offset array
    (`base + total_size` and all data past it stay byte-for-byte in place).
    Raises :class:`TableTooLarge` if the content exceeds `total_size`.
    """
    count = len(entries)
    first_offset = 4 + count * 2
    offsets: list[int] = []
    cursor = first_offset
    for words in entries:
        offsets.append(cursor)
        cursor += len(words) * 2
    # Entry offsets are stored as u16, so the whole table must fit in 0xFFFF
    # bytes as well as within the fixed original size; check before packing.
    packed_size = 4 + count * 2 + sum(len(words) for words in entries) * 2
    if packed_size > total_size or cursor > 0xFFFF:
        raise TableTooLarge(
            f"rebuilt table {packed_size} (end 0x{cursor:X}) exceeds "
            f"original {total_size} / 0xFFFF"
        )
    out = bytearray(order.pack_u32(total_size))
    for off in offsets:
        out += order.pack_u16(off)
    for words in entries:
        for word in words:
            out += order.pack_u16(word)
    out += b"\x00" * (total_size - len(out))
    return bytes(out)


def splice_local_index_table(data: bytes, start: int, used: int,
                             entries: list[list[int]], order: ByteOrder = BE) -> bytes:
    """Return `data` with block `start`'s field_3c table rebuilt for `entries`.

    Preserves the file length and every byte outside the fixed-size table
    region; raises if the block has no local index table or the content does
    not fit.
    """
    layout = local_index_layout(data, start, used, order)
    if layout is None:
        raise ValueError(f"block at 0x{start:X} has no field_3c local index table")
    base, total_size, offsets = layout
    if len(entries) != len(offsets):
        raise ValueError(
            f"entry count changed for block at 0x{start:X}: "
            f"{len(offsets)} -> {len(entries)}"
        )
    region = build_local_index_table(entries, total_size, order)
    return data[:base] + region + data[base + total_size:]


def rebuild_block_text(block: bytes, entries: list[list[int]], order: ByteOrder = BE) -> bytes:
    """Return `block` with its field_3c text table set to `entries`.

    If the rebuilt table fits the original `total_size`, it is spliced in place
    and the block length is unchanged. Otherwise the table is *enlarged in
    place* and everything after it shifts back by the (4-aligned) growth: the
    runtime loader (PROG1 `0x6079172`) resolves the table at
    `rt + u32(rt+0x3C)` and then chains every following section *relative to
    the table end* (`text + total_size`, plus u32 links stored in the data), so
    the table must stay where field_3c points and the shifted tail keeps every
    chained reference intact. Moving the table (the old approach of appending
    it at the block end) breaks that chain — the engine then reads garbage
    section pointers past the block. The block is taken and returned as
    standalone bytes (offset 0).
    """
    used = len(block)
    layout = local_index_layout(block, 0, used, order)
    if layout is None:
        raise ValueError("block has no field_3c local index table")
    base, total_size, offsets = layout
    if len(entries) != len(offsets):
        raise ValueError(f"entry count changed: {len(offsets)} -> {len(entries)}")
    packed = 4 + len(entries) * 2 + sum(len(words) for words in entries) * 2
    if packed > 0xFFFF:
        raise TableTooLarge(f"table {packed} exceeds the u16 offset range 0xFFFF")
    new_total = total_size if packed <= total_size else (packed + 3) & ~3
    region = build_local_index_table(entries, new_total, order)
    return block[:base] + region + block[base + total_size:]


def repack_scen(data: bytes, block_entries: dict[int, list[list[int]]],
                order: ByteOrder = BE) -> bytes:
    """Rebuild the whole SCEN.DAT applying `block_entries` and re-laying out.

    Each block whose index is in `block_entries` gets its field_3c text table
    rebuilt (growing the block if needed); other blocks are kept byte-identical.
    All blocks are then re-laid-out at 0x800-sector alignment and the top-level
    catalog (`count`, then per-block `start_sector`/`used_size`) is rewritten to
    match. With an empty `block_entries` this reproduces the input file.
    """
    blocks = parse_catalog(data, order)
    count = len(blocks)
    rebuilt: list[bytes] = []
    for chunk_index, (start, used) in enumerate(blocks):
        block = data[start:start + used]
        if chunk_index in block_entries:
            block = rebuild_block_text(block, block_entries[chunk_index], order)
        rebuilt.append(block)

    first_start = align_up(4 + count * 8)
    starts: list[int] = []
    cursor = first_start
    for block in rebuilt:
        starts.append(cursor)
        cursor = align_up(cursor + len(block))

    out = bytearray(cursor)
    out[0:4] = order.pack_u32(count)
    for i, (block, start) in enumerate(zip(rebuilt, starts)):
        out[4 + i * 8:4 + i * 8 + 4] = order.pack_u32(start // SECTOR)
        out[4 + i * 8 + 4:4 + i * 8 + 8] = order.pack_u32(len(block))
        out[start:start + len(block)] = block
    return bytes(out)
