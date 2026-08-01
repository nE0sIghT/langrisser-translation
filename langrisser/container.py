#!/usr/bin/env python3
"""Lay a SCEN container out again inside a file that may not change size.

Both engines here store their script the same way: a leading table of absolute
32-bit chunk offsets, then the chunks themselves, each starting on a 0x800
boundary. So a chunk that outgrows its own span can still be written — the
catalog is only pointers — as long as the file-level total holds and the
alignment is kept.

The alignment is not optional. The pointers are plain byte offsets and look
like they would allow anything, but a Langrisser I build whose chunks were
packed to word alignment hangs on a black screen after the menu: something
reaches for a chunk by sector where the catalog does not show it. Langrisser V
has always treated 0x800 as a hard rule and raises rather than break it; this
is that code, shared.

When the chunks do not fit, whole sectors of trailing zero padding are
reclaimed from the back before giving up, and giving up means saying so — the
text has to get shorter.
"""
from __future__ import annotations

import struct

from langrisser.scen import read_chunk_spans

CHUNK_ALIGN = 0x800


def align_up(size: int, align: int = CHUNK_ALIGN) -> int:
    return -(-size // align) * align


def pad_chunk(chunk: bytes, was: bytes | None = None) -> bytes:
    """A chunk padded out to the next 0x800 boundary.

    Growth is absorbed into the chunk's own trailing zero padding first, so a
    chunk only claims another sector when it really outgrew the one it had.
    """
    if was is not None and len(chunk) > len(was):
        orig_tz = len(was) - len(was.rstrip(b"\x00"))
        stripped = chunk.rstrip(b"\x00")
        removable = min(orig_tz, len(chunk) - len(stripped))
        if len(chunk) - removable <= len(was):
            chunk = chunk[: len(chunk) - removable]
            chunk += b"\x00" * (len(was) - len(chunk))
    if len(chunk) % CHUNK_ALIGN:
        chunk += b"\x00" * (CHUNK_ALIGN - len(chunk) % CHUNK_ALIGN)
    return chunk


def trim_aligned_chunk(chunk: bytes) -> bytes:
    """Drop whole-sector trailing zero padding from a chunk."""
    used = len(chunk.rstrip(b"\x00"))
    size = align_up(used)
    return chunk[:size].ljust(size, b"\x00")


def trim_blobs_to_fit(blobs: list[bytes], capacity: int) -> tuple[list[bytes], int]:
    """Reclaim whole-sector trailing zero padding, from the last chunk
    backwards, until the chunks fit the capacity (or nothing is left to
    trim). Returns the adjusted chunk list and its total size."""
    blobs = list(blobs)
    total = sum(len(blob) for blob in blobs)
    if total > capacity:
        for i in range(len(blobs) - 1, -1, -1):
            trimmed = trim_aligned_chunk(blobs[i])
            saved = len(blobs[i]) - len(trimmed)
            if saved <= 0:
                continue
            blobs[i] = trimmed
            total -= saved
            if total <= capacity:
                break
    return blobs, total


def rebuild_container_fixed_size(data: bytes, chunks: list[bytes],
                                 spans: list[tuple[int, int]],
                                 label: str) -> bytes:
    header_size = spans[0][0]
    header = bytearray(data[:header_size])
    # Reclaim whole-sector trailing padding only when the translated
    # chunks actually need container-level space.
    blobs, chunks_total = trim_blobs_to_fit(list(chunks), len(data) - header_size)
    total = header_size + chunks_total

    if total > len(data):
        grew = [f"chunk {i} {len(blob) - (b - a):+} bytes"
                for i, (blob, (a, b)) in enumerate(zip(blobs, spans))
                if len(blob) != b - a]
        raise SystemExit(
            f"{label}: fixed-size repack needs {total} bytes, source file is "
            f"{len(data)} bytes, {total - len(data)} over. Shorten text or free "
            f"more padding. Chunks that changed size: {', '.join(grew) or 'none'}"
        )

    cur = header_size
    ptrs: list[int] = []

    for blob in blobs:
        if cur % CHUNK_ALIGN:
            raise SystemExit(f"{label}: chunk pointer 0x{cur:X} is not 0x800-aligned")
        ptrs.append(cur)
        cur += len(blob)

    ptrs.append(len(data))
    if len(ptrs) * 4 > header_size:
        raise SystemExit(f"{label}: pointer table does not fit in original header")
    for i, p in enumerate(ptrs):
        struct.pack_into("<I", header, i * 4, p)

    result = bytes(header) + b"".join(blobs)
    result += b"\x00" * (len(data) - len(result))
    if len(result) != len(data):
        raise AssertionError("fixed-size repack changed file size")
    return result


