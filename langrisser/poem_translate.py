#!/usr/bin/env python3
"""Translate the prologue poem graphic (IMG.DAT asset 12, image 0).

The poem is an 8bpp indexed bitmap (768x252) drawn on the title attract loop.
The engine treats the three 256px-wide panels as one tall scroll (panel 0 on
top, then 1, then 2) and pans it upward. This redraws the poem from
the selected language pack's poem text onto that continuous strip (height*3, no
gap between panels), then slices it back into the three panels at the exact
panel boundaries, so a line straddling a boundary rejoins seamlessly. The image
keeps its size, so the result can be injected into the BIN like any other
IMG.DAT edit.

Glyphs use the original poem's colours: the body is the animated highlight
index (red in the canonical frame), the antialias edge a darker index, so the
title's per-line highlight palette cycling still applies.
"""
import argparse
import sys
from pathlib import Path

from langrisser import poem_render as poem_render
from langrisser.project import add_language_args, language_from_args
from langrisser import imgdat as imd

ASSET_INDEX = 12
IMAGE_INDEX = 0
BG_INDEX = poem_render.BG_INDEX
FONT = poem_render.FONT
FONT_SIZE = 12
LINE_HEIGHT = 18         # stamp canvas height per rendered line
HORIZONTAL_MARGIN = poem_render.HORIZONTAL_MARGIN
TOP_MARGIN = poem_render.TOP_MARGIN
BOTTOM_EMPTY = poem_render.BOTTOM_EMPTY
MAX_PITCH = poem_render.MAX_PITCH
MIN_PITCH = poem_render.MIN_PITCH


def find_overflow_group(asset: bytes, main_start: int, main_packets: int,
                        width_word: int) -> tuple[int, int, int]:
    """Locate the poem's `type=2` overflow block right after the main image.

    The poem image continues past the main group (VRAM rows 256-507) into a
    short `type=2` block (VRAM rows 508-511, same width): the bottom scanlines of
    every column live there, so the columns are taller than the main image. The
    engine treats the whole thing as one image, so these rows must be rewritten,
    not blanked. Returns (start_offset, packet_count, block_rows); count 0 if
    there is no overflow block.
    """
    off = main_start + main_packets * imd.PACKET_BYTES
    start, packets, block_rows = off, 0, 0
    while (off + imd.PACKET_HEADER_BYTES <= len(asset)
           and imd._u16(asset, off) == imd.PACKET_MAGIC
           and imd._u16(asset, off + 0x06) == 2
           and imd._u16(asset, off + 0x14) == width_word):
        if packets == 0:
            block_rows = imd._u16(asset, off + 0x16)
        packets += 1
        off += imd.PACKET_BYTES
    return start, packets, block_rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--imgdat", default="work/l5/extracted/IMG.DAT")
    ap.add_argument("--poem", default=None)
    ap.add_argument("--out-imgdat", default="work/l5/build/IMG.DAT.poem")
    ap.add_argument("--out-preview", default=None)
    ap.add_argument("--font", default=FONT)
    args = ap.parse_args()
    lang = language_from_args(args)
    poem_path = Path(args.poem) if args.poem else lang.poem
    preview_path = (Path(args.out_preview) if args.out_preview
                    else lang.build_path("poem_{lang}_preview.png"))

    data = imd.read_img(args.imgdat)
    ent, asset = imd.get_asset(data, ASSET_INDEX)
    main_start, main_packets, width, main_br = imd.image_groups(asset)[IMAGE_INDEX]
    main_h = len(imd.decode_image(asset, main_start, main_packets, width, main_br))

    # The poem image continues past the main group into a `type=2` overflow block
    # (VRAM rows 508-511): the bottom scanlines of every column live there, so a
    # column is `col_h` (= main_h + overflow rows) tall, not just main_h.
    width_word = imd._u16(asset, main_start + 0x14)
    ov_start, ov_packets, ov_br = find_overflow_group(asset, main_start, main_packets, width_word)
    ov_h = len(imd.decode_image(asset, ov_start, ov_packets, width, ov_br)) if ov_packets else 0
    col_h = main_h + ov_h

    panels = 3
    panel_w = width // panels
    # The engine stacks the three columns (col 0, then 1, then 2) into one
    # continuous vertical scroll of full-height columns, no gap between them.
    strip_h = col_h * panels
    try:
        layout = poem_render.render_poem_strip(
            poem_render.load_lines(poem_path),
            width=panel_w,
            strip_height=strip_h,
            font_path=args.font,
            font_size=FONT_SIZE,
            line_height=LINE_HEIGHT,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    strip_rows = layout.rows

    # Fold the scroll strip back into the stored width x col_h image, slicing each
    # column at its exact boundary so the engine re-stacks them into a seamless
    # scroll: a line straddling a column boundary is split between a column's
    # overflow rows and the top of the next column, and rejoins with no gap.
    full = [bytearray([BG_INDEX] * width) for _ in range(col_h)]
    for panel in range(panels):
        src_y = panel * col_h
        dst_x = panel * panel_w
        for y in range(col_h):
            full[y][dst_x:dst_x + panel_w] = strip_rows[src_y + y]

    # The main image holds rows 0..main_h-1 of every column; the overflow block
    # holds the remaining bottom rows. Write both so boundary-crossing lines keep
    # their bottom slivers.
    patched_asset = imd.encode_image(asset, main_start, main_packets, width, main_br, full[:main_h])
    if ov_packets:
        patched_asset = imd.encode_image(
            patched_asset, ov_start, ov_packets, width, ov_br, full[main_h:col_h])
    imd.replace_asset(data, ASSET_INDEX, patched_asset)
    out = Path(args.out_imgdat)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)

    palettes = imd.clut_palettes(patched_asset)
    palette = imd.pick_palette(b"".join(bytes(r) for r in full), palettes) or palettes[0]
    poem_render.save_indexed_preview(full, palette, preview_path)
    print(f"patched IMG.DAT -> {out}")
    print(f"poem preview -> {preview_path}")
    print(
        "poem layout: "
        f"pitch={layout.pitch} lines={len(layout.lines)} blocks={[len(b) for b in layout.blocks]} "
        f"col_h={col_h} (main {main_h} + overflow {ov_h}) strip_h={strip_h} panel_w={panel_w}"
    )


if __name__ == "__main__":
    main()
