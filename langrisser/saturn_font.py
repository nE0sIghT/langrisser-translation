#!/usr/bin/env python3
"""Inspect the Saturn SYSTEM.DAT text font in read-only mode.

The Saturn text font uses the same format as PS1 SYSTEM.BIN: a 12x12 1bpp glyph
plane addressed as ``index * 18`` from offset 0, glyph slots 0..1820, 12 bits
per row MSB-first with rows packed continuously (18 bytes/glyph). Both the
SYSTEM UI text and the SCEN dialogue index into this one plane. See
docs/SATURN_DISC_FORMAT.md.

Subcommands:
  render  ASCII-render one or more glyph slots.
  diff    Compare glyph slots 0..1820 against a PS1 SYSTEM.BIN and report how
          many are byte-identical and which id ranges differ.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from langrisser.engine import load_engine

_GLYPH = load_engine().glyph
GLYPH_BYTES = _GLYPH.bytes_per_glyph
CELL = _GLYPH.width
MAX_SLOT = _GLYPH.default_max_slot


def glyph_rows(data: bytes, index: int) -> list[str]:
    off = index * GLYPH_BYTES
    raw = data[off:off + GLYPH_BYTES]
    bits = "".join(f"{byte:08b}" for byte in raw)
    return [bits[r * CELL:(r + 1) * CELL].replace("0", ".").replace("1", "#")
            for r in range(CELL)]


def cmd_render(args: argparse.Namespace) -> None:
    data = Path(args.font).read_bytes()
    for token in args.slots:
        index = int(token, 0)
        print(f"--- glyph 0x{index:04X} @0x{index * GLYPH_BYTES:04X} ---")
        for row in glyph_rows(data, index):
            print(row)


def contiguous_ranges(values: list[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    if not values:
        return ranges
    start = prev = values[0]
    for value in values[1:]:
        if value == prev + 1:
            prev = value
        else:
            ranges.append((start, prev))
            start = prev = value
    ranges.append((start, prev))
    return ranges


def cmd_diff(args: argparse.Namespace) -> None:
    sat = Path(args.font).read_bytes()
    ps1 = Path(args.ps1).read_bytes()
    differ = [i for i in range(MAX_SLOT + 1)
              if sat[i * GLYPH_BYTES:(i + 1) * GLYPH_BYTES]
              != ps1[i * GLYPH_BYTES:(i + 1) * GLYPH_BYTES]]
    identical = MAX_SLOT + 1 - len(differ)
    print(f"glyph slots 0..{MAX_SLOT}: identical={identical} differ={len(differ)}")
    if differ:
        print(f"first differing slot: 0x{differ[0]:04X} "
              f"(byte offset 0x{differ[0] * GLYPH_BYTES:04X})")
        print("differing ranges:")
        for start, end in contiguous_ranges(differ):
            print(f"  0x{start:04X}..0x{end:04X}  ({end - start + 1})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    r = sub.add_parser("render", help="ASCII-render glyph slots")
    r.add_argument("--font", default="work/build/saturn/SYSTEM.DAT")
    r.add_argument("slots", nargs="+", help="glyph indices (e.g. 0x0094 0x0122)")
    r.set_defaults(func=cmd_render)

    d = sub.add_parser("diff", help="compare glyph slots against PS1 SYSTEM.BIN")
    d.add_argument("--font", default="work/build/saturn/SYSTEM.DAT")
    d.add_argument("--ps1", default="work/extracted/SYSTEM.BIN")
    d.set_defaults(func=cmd_diff)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
