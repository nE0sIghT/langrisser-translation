#!/usr/bin/env python3
"""Find and decode the UI strings the Langrisser I & II executables hold.

Screens that are not script - the title and load menus, the pre-battle menu,
the shop, the options screen - are drawn with a tile font rather than the
glyph plane. `0x8001c248(surface, x, y, code)` turns one byte into the four
8x8 tiles at `code * 4 + 0x100`, so a string is one byte per character and the
character set is `data/common/font_mapping/l1_2_ui_font_map.csv`.

There is no offset table: each string is a literal that the compiler reaches
with a hard-coded `lui`/`addiu` pair, so they are found by walking the code
for those pairs and following them into the data the executable ships with.
"""
from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path

import capstone

from langrisser.scen import load_charmap_csv

UI_FONT_MAP = Path("data/common/font_mapping/l1_2_ui_font_map.csv")
TERMINATOR = 0x00
MIN_LENGTH = 2
EXE_HEADER = 0x800


@dataclass(frozen=True)
class UiString:
    address: int
    file_offset: int
    codes: tuple[int, ...]
    referenced_by: tuple[int, ...]

    @property
    def length(self) -> int:
        return len(self.codes)


def load_exe(path: Path) -> tuple[bytes, int, int]:
    data = path.read_bytes()
    t_addr, t_size = struct.unpack_from("<II", data, 0x18)
    return data, t_addr, t_size


def pointer_references(data: bytes, t_addr: int, t_size: int) -> dict[int, list[int]]:
    """Every `lui`/`addiu` pair in the text, as target address -> code addresses."""
    refs: dict[int, list[int]] = {}
    words = struct.unpack_from(f"<{t_size // 4}I", data, EXE_HEADER)
    pending: dict[int, tuple[int, int]] = {}
    for i, word in enumerate(words):
        op = word >> 26
        rt = (word >> 16) & 31
        if op == 0x0F:                                  # lui rt, imm
            pending[rt] = (word & 0xFFFF, t_addr + i * 4)
            continue
        if op == 0x09:                                  # addiu rt, rs, imm
            rs = (word >> 21) & 31
            if rs in pending and rs == rt:
                hi, at = pending.pop(rs)
                lo = word & 0xFFFF
                target = (hi << 16) + (lo - 0x10000 if lo & 0x8000 else lo)
                refs.setdefault(target & 0xFFFFFFFF, []).append(at)
        # any write to a register invalidates a pending upper half
        if op in (0x09, 0x0C, 0x0D, 0x0E, 0x24, 0x25, 0x23, 0x20, 0x21):
            pending.pop(rt, None)
        elif op == 0:
            pending.pop((word >> 11) & 31, None)
    return refs


def read_string(data: bytes, t_addr: int, address: int, limit: int) -> tuple[int, ...] | None:
    offset = address - t_addr + EXE_HEADER
    if not 0 <= offset < len(data):
        return None
    codes: list[int] = []
    while offset < len(data) and len(codes) <= 256:
        byte = data[offset]
        if byte == TERMINATOR:
            return tuple(codes) if len(codes) >= MIN_LENGTH else None
        if byte > limit:
            return None
        codes.append(byte)
        offset += 1
    return None


TILE_BASE = 0x100
CALLER_DEPTH = 3


def find_put_character(data: bytes, t_addr: int, t_size: int) -> int:
    """The routine that turns a character code into four tiles.

    Recognised by its arithmetic rather than its address, because the two
    executables are separate builds: a `sll rX, rY, 2` (four tiles per
    character) feeding an `addiu rZ, rX, 0x100` (the font's first tile).
    """
    words = struct.unpack_from(f"<{t_size // 4}I", data, EXE_HEADER)
    shifted: dict[int, int] = {}
    for i, word in enumerate(words):
        if word >> 26 == 0 and (word & 0x3F) == 0 and ((word >> 6) & 31) == 2:
            shifted[(word >> 11) & 31] = i                      # sll rd, rt, 2
        elif word >> 26 == 0x09 and (word & 0xFFFF) == TILE_BASE:
            rs = (word >> 21) & 31
            if rs in shifted and i - shifted[rs] <= 16:
                return t_addr + i * 4
    raise ValueError(f"no tile-font character writer in {t_addr:#x}")


def call_graph(data: bytes, t_addr: int, t_size: int) -> tuple[list[int], dict[int, set[int]]]:
    """Function entry points and the `jal` targets each one contains."""
    md = capstone.Cs(capstone.CS_ARCH_MIPS,
                     capstone.CS_MODE_MIPS32 | capstone.CS_MODE_LITTLE_ENDIAN)
    md.detail = False
    words = struct.unpack_from(f"<{t_size // 4}I", data, EXE_HEADER)
    starts, calls, current = [t_addr], {}, t_addr
    for i, word in enumerate(words):
        address = t_addr + i * 4
        if word >> 26 == 3:                                  # jal
            calls.setdefault(current, set()).add(((address & 0xF0000000) |
                                                  ((word & 0x3FFFFFF) << 2)))
        elif word == 0x03E00008:                             # jr $ra
            current = address + 8
            starts.append(current)
    del md
    return starts, calls


def text_drawing_functions(data: bytes, t_addr: int, t_size: int) -> set[int]:
    """Functions that reach the tile-font character writer."""
    starts, calls = call_graph(data, t_addr, t_size)
    writer = find_put_character(data, t_addr, t_size)
    reaching = {enclosing(starts, writer)}
    for _ in range(CALLER_DEPTH):
        grown = {fn for fn, targets in calls.items() if targets & reaching}
        if grown <= reaching:
            break
        reaching |= grown
    return reaching


def enclosing(starts: list[int], address: int) -> int:
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= address:
            lo = mid
        else:
            hi = mid - 1
    return starts[lo]


def harvest(path: Path, drawn_only: bool = True) -> list[UiString]:
    data, t_addr, t_size = load_exe(path)
    charmap = load_charmap_csv(UI_FONT_MAP)
    limit = max(charmap)
    starts, _ = call_graph(data, t_addr, t_size)
    drawing = text_drawing_functions(data, t_addr, t_size) if drawn_only else None
    found: dict[int, UiString] = {}
    for target, sites in pointer_references(data, t_addr, t_size).items():
        codes = read_string(data, t_addr, target, limit)
        if codes is None:
            continue
        if drawing is not None:
            sites = [s for s in sites if enclosing(starts, s) in drawing]
            if not sites:
                continue
        found[target] = UiString(target, target - t_addr + EXE_HEADER,
                                 codes, tuple(sorted(sites)))
    return [found[a] for a in sorted(found)]


def decode(codes: tuple[int, ...], charmap: dict[int, str]) -> str:
    return "".join(charmap.get(c, f"<{c}>") for c in codes)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exe", nargs="+")
    ap.add_argument("--min-length", type=int, default=MIN_LENGTH)
    ap.add_argument("--all", action="store_true",
                    help="Do not restrict to strings a text-drawing function reaches.")
    args = ap.parse_args()

    charmap = load_charmap_csv(UI_FONT_MAP)
    for name in args.exe:
        path = Path(name)
        strings = [s for s in harvest(path, drawn_only=not args.all)
                   if s.length >= args.min_length]
        print(f"# {path} - {len(strings)} strings")
        for s in strings:
            print(f"{s.file_offset:#08x}\t{s.address:#010x}\t{s.length:3d}\t"
                  f"{len(s.referenced_by)}\t{decode(s.codes, charmap)}")


if __name__ == "__main__":
    main()
