#!/usr/bin/env python3
"""Decode the Saturn multi-asset graphic containers (TITLE/OPEN/STAFF/CAST...).

These per-screen `.DAT` files are the Saturn analogue of PS1 `IMG.DAT`: one file
holding several assets behind a table of contents. The layout (verified byte-exact
on TITLE1/TITLE2/OPEN/CAST/STAFF — see docs/SATURN_DISC_FORMAT.md) is:

    u32 count
    count x (u32 sub_offset, u32 sub_size)      # contiguous sub-assets

Each container alternates a small **descriptor** sub-asset with its big **image**
sub-asset:

* the descriptor is a mini-container: a `(u16 width_px, u16 height_px)` sprite
  table, a 256-colour BGR555 CLUT (a `0x200`-byte block its header points at) and
  a VDP1 coordinate table;
* the image is the full picture stored as **VDP2 8x8 cells**, 8bpp. De-tiling
  those cells with the descriptor's CLUT reconstructs it. The UI-text screens
  (e.g. `TITLE1.DAT`, the title credits) are a linear 40-cell (320 px) grid — the
  default here; some full-art screens (staff/cast illustrations) use a wider grid
  or a VDP2 nametable, so `--cols` is tunable and those are not yet reconstructed.

`CLEAR.DAT` is the exception: a single bare asset with no TOC (handled by
`saturn_scenario_clear.py`). This module only decodes/re-encodes; disc insertion
is a fixed-size repack, same as the other Saturn tools.

Colour conversion and the endian helper are shared with the PS1 tooling
(`langrisser.imgdat.rgb555_to_rgb888`, `langrisser.binfmt.BE`) — no duplicated logic.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from langrisser.binfmt import BE
from langrisser import imgdat as imd

CELL = 8                      # VDP2 cell is 8x8 pixels
DEFAULT_CELL_COLS = 40        # 40 cells = 320 px, the width every image uses
CLUT_ENTRIES = 256
CLUT_BYTES = CLUT_ENTRIES * 2  # 0x200: 256 big-endian BGR555 entries


@dataclass(frozen=True)
class TocEntry:
    index: int
    offset: int
    size: int

    @property
    def end(self) -> int:
        return self.offset + self.size


def parse_toc(data: bytes) -> list[TocEntry]:
    """Parse the container TOC. Raises ValueError if it is not a container."""
    if len(data) < 12:
        raise ValueError("too small to be a container")
    count = BE.u32(data, 0)
    if count == 0 or count > 64 or 4 + count * 8 > len(data):
        raise ValueError(f"implausible TOC count {count}")
    entries = []
    pos = 4
    for i in range(count):
        off, size = BE.u32(data, pos), BE.u32(data, pos + 4)
        pos += 8
        if not (0 < off <= len(data) and 0 < size and off + size <= len(data)):
            raise ValueError(f"entry {i} out of bounds: off={off:#x} size={size:#x}")
        entries.append(TocEntry(i, off, size))
    # The sub-assets tile the file contiguously; that is the container signature
    # that separates a real TOC from a bare asset whose first word looks like one.
    for a, b in zip(entries, entries[1:]):
        if a.end != b.offset:
            raise ValueError("sub-assets are not contiguous; not a TOC container")
    return entries


def find_clut_offset(descriptor: bytes) -> int | None:
    """Offset of the sprite CLUT inside a descriptor sub-asset.

    The descriptor header is a list of u32s; a CLUT is a `0x200`-byte block. We
    look for an `(offset, 0x200)` pair pointing in-bounds. This is the palette of
    the descriptor's small VDP1 sprites; the big cell image uses the next one
    (see :func:`image_clut_offset`).
    """
    for pos in range(0, min(len(descriptor), 0x40) - 8, 4):
        off, length = BE.u32(descriptor, pos), BE.u32(descriptor, pos + 4)
        if length == CLUT_BYTES and 0 < off and off + CLUT_BYTES <= len(descriptor):
            return off
    return None


def image_clut_offset(descriptor: bytes) -> int | None:
    """Offset of the big cell image's CLUT inside its descriptor sub-asset.

    The descriptor stores two consecutive 256-colour palettes: the first is for
    its small sprites, the second (immediately after) is the palette the cell
    image is drawn with. Verified across TITLE1/OPEN/CAST: with this palette the
    background maps to pure black (index 255 = `(0,0,0)`) and the art is coherent
    (stone logo / blue nebula), whereas the sprite palette renders it as noise.
    """
    sprite = find_clut_offset(descriptor)
    if sprite is None:
        return None
    img = sprite + CLUT_BYTES
    return img if img + CLUT_BYTES <= len(descriptor) else sprite


def read_clut(descriptor: bytes, offset: int) -> list[tuple[int, int, int]]:
    """256-entry palette: big-endian BGR555 (bit15 ignored), reusing the PS1
    colour conversion so both platforms decode colours identically."""
    return [imd.rgb555_to_rgb888(BE.u16(descriptor, offset + i * 2))
            for i in range(CLUT_ENTRIES)]


def detile(cells: bytes, cols: int = DEFAULT_CELL_COLS) -> tuple[bytearray, int, int]:
    """VDP2 8x8 8bpp cells (row-major within a cell, cells left-to-right then
    top-to-bottom) -> a linear index bitmap. Returns (pixels, width, height)."""
    per_cell = CELL * CELL
    total = len(cells) // per_cell
    rows = total // cols
    width, height = cols * CELL, rows * CELL
    out = bytearray(width * height)
    for c in range(rows * cols):
        cx, cy = (c % cols) * CELL, (c // cols) * CELL
        base = c * per_cell
        for r in range(CELL):
            src = base + r * CELL
            dst = (cy + r) * width + cx
            out[dst:dst + CELL] = cells[src:src + CELL]
    return out, width, height


def retile(pixels: bytes, width: int, cols: int = DEFAULT_CELL_COLS) -> bytes:
    """Inverse of :func:`detile`: linear bitmap -> VDP2 8x8 cell stream."""
    per_cell = CELL * CELL
    rows = (len(pixels) // width) // CELL
    out = bytearray(rows * cols * per_cell)
    for c in range(rows * cols):
        cx, cy = (c % cols) * CELL, (c // cols) * CELL
        base = c * per_cell
        for r in range(CELL):
            src = (cy + r) * width + cx
            dst = base + r * CELL
            out[dst:dst + CELL] = pixels[src:src + CELL]
    return bytes(out)


@dataclass
class Container:
    entries: list[TocEntry]
    data: bytes

    def sub(self, entry: TocEntry) -> bytes:
        return self.data[entry.offset:entry.end]

    def images(self) -> list[tuple[TocEntry, TocEntry]]:
        """Pair each big image sub-asset with the preceding descriptor.

        Descriptors start with a small value (a table offset) and carry a CLUT;
        image sub-assets are the large ones whose leading u32s are zero (the
        `tex_off`/`tex_size` slots are unused for cell images).
        """
        pairs = []
        last_desc = None
        for e in self.entries:
            sub = self.sub(e)
            is_image = len(sub) >= 8 and BE.u32(sub, 0) == 0 and BE.u32(sub, 4) == 0
            if is_image and last_desc is not None:
                pairs.append((last_desc, e))
            else:
                last_desc = e
        return pairs


def load(path: str | Path) -> Container:
    data = Path(path).read_bytes()
    return Container(parse_toc(data), data)


def cmd_list(args: argparse.Namespace) -> None:
    cont = load(args.container)
    print(f"{args.container}: {len(cont.entries)} sub-assets")
    for e in cont.entries:
        sub = cont.sub(e)
        clut = find_clut_offset(sub)
        kind = "image(cells)" if (len(sub) >= 8 and BE.u32(sub, 0) == 0
                                  and BE.u32(sub, 4) == 0) else "descriptor"
        extra = f" clut@{clut:#x}" if clut is not None else ""
        print(f"  [{e.index}] off={e.offset:#08x} size={e.size:#08x} {kind}{extra}")


def cmd_preview(args: argparse.Namespace) -> None:
    cont = load(args.container)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(args.container).stem
    for i, (desc, img) in enumerate(cont.images()):
        clut_off = image_clut_offset(cont.sub(desc))
        palette = (read_clut(cont.sub(desc), clut_off) if clut_off is not None
                   else [(v, v, v) for v in range(256)])
        pixels, width, height = detile(cont.sub(img), args.cols)
        frame = Image.new("RGB", (width, height))
        frame.putdata([palette[v] for v in pixels])
        scale = max(1, args.scale)
        frame = frame.resize((width * scale, height * scale), Image.NEAREST)
        path = out_dir / f"{stem}_asset{img.index}.png"
        frame.save(path)
        print(f"asset {img.index}: {width}x{height} -> {path}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list", help="list the container's sub-assets")
    pl.add_argument("container")
    pl.set_defaults(func=cmd_list)
    pp = sub.add_parser("preview", help="render each image sub-asset to a PNG")
    pp.add_argument("container")
    pp.add_argument("--out-dir", default="work/build/saturn/previews")
    pp.add_argument("--cols", type=int, default=DEFAULT_CELL_COLS,
                    help="cell columns (width/8); default 40 = 320 px")
    pp.add_argument("--scale", type=int, default=1)
    pp.set_defaults(func=cmd_preview)
    return ap


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
