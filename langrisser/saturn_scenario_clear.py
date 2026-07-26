#!/usr/bin/env python3
"""Translate the Saturn SCENARIO CLEAR banner (CLEAR.DAT).

CLEAR.DAT is `u32 texture_offset`, `u32 texture_size`, a VDP1 sprite header,
then a 224x80 8bpp texture (confirmed against a VDP1 VRAM dump). The lettering
sits on a black field (index 0) between the mechanical ornaments. This reuses
the shared `langrisser.banner.redraw_banner` — the same erase-and-redraw core as the
PS1 banner — and only supplies the Saturn container I/O, geometry and palette.
The file size is preserved (the texture is rewritten in place).
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

from PIL import Image

from langrisser.banner import BannerLayout, redraw_banner
from langrisser.project import add_language_args, language_from_args
from langrisser import imgdat as imd

WIDTH = 224
PALETTE_SIZE = 256 * 2   # 256-entry 16bpp CLUT sits directly before the texture
FONT = "data/fonts/DejaVuSerif-Bold.ttf"
# Caps span rows ~30..50 across x20..204 (inside the flanking bracket ornaments);
# the gold letter fill sits at luminance ~130, midtone edges from ~35 up.
# The plate behind the caps is opaque black at palette index 255; index 0 is
# the VDP1-transparent colour — erasing to 0 made the band see-through in game.
SATURN_LAYOUT = BannerLayout(
    text_x0=20, text_x1=204, text_y0=29, text_y1=51,
    cap_top=32, baseline=49, paint_y0=27, paint_y1=53,
    bg_index=255, banner_width=WIDTH, bright_lum=115, mid_lum=35,
)


def load_palette(data: bytes, offset: int) -> list[tuple[int, int, int]]:
    """256-entry CLUT from the disc: big-endian BGR555, same as PS1 IMG.DAT."""
    return [imd.rgb555_to_rgb888(struct.unpack_from(">H", data, offset + i * 2)[0])
            for i in range(256)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--clear", default="work/l5/build/saturn/CLEAR.DAT")
    ap.add_argument("--out-clear", default="work/l5/build/saturn/CLEAR.ru.DAT")
    ap.add_argument("--text", default=None)
    ap.add_argument("--font", default=FONT)
    ap.add_argument("--out-preview", default="work/l5/build/saturn/scenario_clear_preview.png")
    args = ap.parse_args()

    lang = language_from_args(args)
    text = args.text if args.text is not None else lang.scenario_clear
    if not text:
        raise SystemExit("no banner text: pass --text or set scenario_clear in the manifest")

    data = bytearray(Path(args.clear).read_bytes())
    tex_off = int.from_bytes(data[0:4], "big")
    tex_size = int.from_bytes(data[4:8], "big")
    height = tex_size // WIDTH
    tex = data[tex_off:tex_off + tex_size]
    rows = [bytearray(tex[y * WIDTH:(y + 1) * WIDTH]) for y in range(height)]

    # The 256-colour CLUT sits on-disc directly before the texture.
    palette = load_palette(data, tex_off - PALETTE_SIZE)
    redraw_banner(rows, palette, text, args.font, SATURN_LAYOUT)

    flat = b"".join(bytes(r) for r in rows)
    data[tex_off:tex_off + tex_size] = flat
    out = Path(args.out_clear)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(data))
    assert len(out.read_bytes()) == len(Path(args.clear).read_bytes()), "CLEAR.DAT size must be preserved"

    preview = Image.new("RGB", (WIDTH * 3, height * 3))
    preview_src = Image.new("RGB", (WIDTH, height))
    preview_src.putdata([palette[v] for r in rows for v in r])
    preview.paste(preview_src.resize((WIDTH * 3, height * 3), Image.NEAREST))
    Path(args.out_preview).parent.mkdir(parents=True, exist_ok=True)
    preview.save(args.out_preview)
    print(f"patched CLEAR.DAT -> {out}")
    print(f"banner preview -> {args.out_preview}")


if __name__ == "__main__":
    main()
