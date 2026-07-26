#!/usr/bin/env python3
"""Translate the Saturn prologue poem in OPEN.DAT.

The Saturn poem is OPEN.DAT sub-asset 2: a VDP1 sprite-run list with a fixed
50-entry run table and a fixed 0x12880-byte atlas. VDP1 units matter here:
`srca` is stored in 8-byte units and run width is stored in 8-pixel units. This
encoder renders the target poem to a 320x768 indexed canvas, packs each non-empty
line as one run, and pads the remaining run-table/atlas space so OPEN.DAT keeps
its exact size.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from langrisser import poem_render as poem_render
from langrisser.binfmt import BE
from langrisser.project import add_language_args, language_from_args
from langrisser import imgdat as imd

SUBASSET_INDEX = 2
BG_INDEX = poem_render.BG_INDEX
FONT = poem_render.FONT
# PS1 metric parity: the PS1 poem renders at font 12 / line height 18
# (langrisser.poem_translate). The Saturn canvas is wider (320 vs 256) and the
# atlas grows to fit (the poem is the last OPEN.DAT sub-asset).
FONT_SIZE = 12
LINE_HEIGHT = 18
TOP_MARGIN = 25
BOTTOM_EMPTY = poem_render.BOTTOM_EMPTY
MAX_PITCH = poem_render.MAX_PITCH
MIN_PITCH = poem_render.MIN_PITCH


@dataclass(frozen=True)
class Run:
    x: int
    y: int
    width: int
    height: int
    pixels: bytes


def canvas_runs(canvas: list[bytearray]) -> list[Run]:
    runs: list[Run] = []
    y = 0
    while y < len(canvas):
        if not any(canvas[y]):
            y += 1
            continue
        block_rows = []
        while y < len(canvas) and any(canvas[y]):
            block_rows.append(canvas[y])
            y += 1
        xs = [x for row in block_rows for x, value in enumerate(row) if value != BG_INDEX]
        x0 = min(xs)
        x1 = max(xs) + 1
        width = int(math.ceil((x1 - x0) / 8) * 8)
        if width <= 0 or width > 320:
            raise ValueError(f"invalid run width {width} at y={y}")
        pixels = bytearray()
        for row in block_rows:
            pixels.extend(row[x0:x0 + width])
        runs.append(Run(x0, y - len(block_rows), width, len(block_rows), bytes(pixels)))
    return runs


def parse_open_toc(data: bytes) -> list[tuple[int, int]]:
    count = BE.u32(data, 0)
    if not 0 < count < 64:
        raise ValueError("invalid OPEN.DAT TOC")
    entries = []
    for i in range(count):
        off = BE.u32(data, 4 + i * 8)
        size = BE.u32(data, 8 + i * 8)
        if off + size > len(data):
            raise ValueError(f"OPEN.DAT subasset {i} out of range")
        entries.append((off, size))
    return entries


def write_u32(buf: bytearray, off: int, value: int) -> None:
    buf[off:off + 4] = value.to_bytes(4, "big")


def write_u16(buf: bytearray, off: int, value: int) -> None:
    buf[off:off + 2] = value.to_bytes(2, "big")


def patch_poem_subasset(sub: bytes, runs: list[Run]) -> bytes:
    """Rebuild the poem sub-asset, growing the atlas when the text needs it.

    The poem is the last OPEN.DAT sub-asset, so a larger atlas only appends
    bytes: the header stays self-consistent by updating the atlas size at
    +0x20 and the total sub-asset size at +0x00 (== atlas_off + atlas_size).
    """
    width = BE.u32(sub, 0x04)
    height = BE.u32(sub, 0x08)
    run_off = BE.u32(sub, 0x18)
    atlas_off = BE.u32(sub, 0x1C)
    orig_atlas_size = BE.u32(sub, 0x20)
    run_capacity = (atlas_off - run_off) // 8
    if width != 320 or height != 768:
        raise ValueError(f"unexpected poem geometry {width}x{height}")
    if len(runs) > run_capacity:
        raise ValueError(f"too many poem runs: {len(runs)} > {run_capacity}")
    needed = sum(run.width * run.height for run in runs)
    atlas_size = max(orig_atlas_size, (needed + 15) & ~15)
    if atlas_size // 8 > 0xFFFF:
        raise ValueError(f"poem atlas {atlas_size:#x} exceeds the u16 srca range")
    out = bytearray(sub[:atlas_off])
    write_u32(out, 0x00, atlas_off + atlas_size)
    write_u32(out, 0x14, len(runs))
    write_u32(out, 0x20, atlas_size)
    out[run_off:atlas_off] = b"\x00" * (atlas_off - run_off)
    atlas = bytearray([BG_INDEX] * atlas_size)
    cursor = 0
    for i, run in enumerate(runs):
        if run.width % 8:
            raise ValueError("run width must be divisible by 8")
        n = run.width * run.height
        atlas[cursor:cursor + n] = run.pixels
        ro = run_off + i * 8
        write_u16(out, ro + 0, run.x)
        write_u16(out, ro + 2, run.y)
        write_u16(out, ro + 4, cursor // 8)
        write_u16(out, ro + 6, ((run.width // 8) << 8) | run.height)
        cursor += n
    return bytes(out + atlas)


def decode_poem_subasset(sub: bytes) -> list[bytearray]:
    width = BE.u32(sub, 0x04)
    height = BE.u32(sub, 0x08)
    count = BE.u32(sub, 0x14)
    run_off = BE.u32(sub, 0x18)
    atlas_off = BE.u32(sub, 0x1C)
    atlas_size = BE.u32(sub, 0x20)
    canvas = [bytearray([BG_INDEX] * width) for _ in range(height)]
    for i in range(count):
        ro = run_off + i * 8
        x = BE.u16(sub, ro + 0)
        y = BE.u16(sub, ro + 2)
        src = BE.u16(sub, ro + 4) * 8
        size_word = BE.u16(sub, ro + 6)
        run_width = (size_word >> 8) * 8
        run_height = size_word & 0xFF
        n = run_width * run_height
        if src + n > atlas_size:
            raise ValueError(f"run {i} exceeds atlas: {src + n:#x} > {atlas_size:#x}")
        if x + run_width > width or y + run_height > height:
            raise ValueError(f"run {i} exceeds canvas: ({x},{y}) {run_width}x{run_height}")
        for row in range(run_height):
            src_o = atlas_off + src + row * run_width
            canvas[y + row][x:x + run_width] = sub[src_o:src_o + run_width]
    return canvas


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--open", default="work/build/saturn/OPEN.DAT")
    ap.add_argument("--poem", default=None)
    ap.add_argument("--out-open", default=None)
    ap.add_argument("--out-preview", default=None)
    ap.add_argument("--font", default=FONT)
    ap.add_argument("--font-size", type=int, default=FONT_SIZE)
    ap.add_argument("--line-height", type=int, default=LINE_HEIGHT)
    args = ap.parse_args()
    lang = language_from_args(args)
    poem_path = Path(args.poem) if args.poem else lang.poem
    out_open = args.out_open or f"work/build/saturn/OPEN.{lang.suffix}.DAT"
    out_preview = args.out_preview or f"work/build/saturn/open_poem_{lang.suffix}_preview.png"
    data = bytearray(Path(args.open).read_bytes())
    entries = parse_open_toc(data)
    off, size = entries[SUBASSET_INDEX]
    sub = bytes(data[off:off + size])
    width, height = BE.u32(sub, 0x04), BE.u32(sub, 0x08)
    pal_off = BE.u32(sub, 0x0C)
    palette = [imd.rgb555_to_rgb888(BE.u16(sub, pal_off + i * 2)) for i in range(256)]
    try:
        layout = poem_render.render_poem_strip(
            poem_render.load_lines(poem_path),
            width=width,
            strip_height=height,
            font_path=args.font,
            font_size=args.font_size,
            line_height=args.line_height,
            top_margin=TOP_MARGIN,
            bottom_empty=BOTTOM_EMPTY,
            min_pitch=MIN_PITCH,
            max_pitch=MAX_PITCH,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    canvas = layout.rows
    runs = canvas_runs(canvas)
    patched = patch_poem_subasset(sub, runs)
    readback = decode_poem_subasset(patched)
    if readback != canvas:
        raise ValueError("patched OPEN.DAT poem readback does not match rendered canvas")
    if off + size != len(data):
        raise SystemExit("poem sub-asset is no longer last in OPEN.DAT; cannot grow")
    data[24:28] = len(patched).to_bytes(4, "big")  # TOC entry 2 size
    data[off:] = patched
    out = Path(out_open)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(data))
    grown = len(data) - (off + size)
    if grown:
        print(f"OPEN.DAT grew by {grown} bytes (atlas extended for the PS1-parity font)")
    poem_render.save_indexed_preview(readback, palette, Path(out_preview))
    atlas_bytes = sum(run.width * run.height for run in runs)
    atlas_total = BE.u32(patched, 0x20)
    print(
        f"patched OPEN.DAT -> {out}  runs={len(runs)} pitch={layout.pitch} "
        f"atlas={atlas_bytes:#x}/{atlas_total:#x} font={args.font_size} line_height={args.line_height}"
    )
    print(f"poem preview -> {out_preview}")


if __name__ == "__main__":
    main()
