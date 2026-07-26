#!/usr/bin/env python3
"""Endian-aware integer helpers shared by the PS1 and Saturn tooling.

The PlayStation build stores multi-byte SYSTEM/SCEN fields little-endian; the
Saturn disc stores the same logical structures in on-disc big-endian
(byte-swapped) order. Any parser that must handle both selects the byte order
once, as a :class:`ByteOrder`, and reuses identical parsing logic regardless of
platform. See docs/SATURN_DISC_FORMAT.md.
"""

from __future__ import annotations

import struct


class ByteOrder:
    """A fixed byte order with `u16`/`u32` read and pack helpers."""

    __slots__ = ("endian", "_u16", "_u32")

    def __init__(self, endian: str) -> None:
        if endian not in ("le", "be"):
            raise ValueError(f"endian must be 'le' or 'be', got {endian!r}")
        self.endian = endian
        self._u16 = "<H" if endian == "le" else ">H"
        self._u32 = "<I" if endian == "le" else ">I"

    def u16(self, data: bytes, off: int) -> int:
        return struct.unpack_from(self._u16, data, off)[0]

    def u32(self, data: bytes, off: int) -> int:
        return struct.unpack_from(self._u32, data, off)[0]

    def pack_u16(self, value: int) -> bytes:
        return struct.pack(self._u16, value)

    def pack_u32(self, value: int) -> bytes:
        return struct.pack(self._u32, value)

    def words(self, data: bytes, off: int, count: int) -> list[int]:
        if not count:
            return []
        fmt = ("<" if self.endian == "le" else ">") + f"{count}H"
        return list(struct.unpack_from(fmt, data, off))


LE = ByteOrder("le")
BE = ByteOrder("be")
