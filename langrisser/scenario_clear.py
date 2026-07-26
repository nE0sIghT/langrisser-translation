#!/usr/bin/env python3
"""Translate the SCENARIO CLEAR banner (IMG.DAT asset 9).

The banner is stored as three identical 224x72 8bpp images (the three CLUTs
animate the orb shine; the lettering indices are constant across them). The
lettering sits on a plain black field between the mechanical rods, flanked by
small bracket ornaments that stay untouched. This erases only the lettering
rectangle and redraws the target text there, transferring colours from the
original letters pixel-by-pixel (nearest sample in the same row), so the
vertical gradient and the horizontal green-to-gold drift both survive.
"""
import argparse
import sys
from pathlib import Path

from PIL import Image

from langrisser.banner import BannerLayout, redraw_banner
from langrisser.project import add_language_args, language_from_args
from langrisser import imgdat as imd

ASSET_INDEX = 9
FONT = "data/fonts/DejaVuSerif-Bold.ttf"
# The field behind the lettering is plain black (index 255); the bracket
# ornaments at x~18 and x~206 stay outside the lettering rectangle. Original
# caps ink rows 31..48 (cap height 16 on a row-48 baseline); diacritics may
# rise into the black gap above and a descender dip one row below, never into
# the rods.
PS1_LAYOUT = BannerLayout(
    text_x0=22, text_x1=205, text_y0=30, text_y1=50,
    cap_top=32, baseline=48, paint_y0=28, paint_y1=51,
    bg_index=255,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--imgdat", default="work/l5/extracted/IMG.DAT")
    ap.add_argument("--text", default=None)
    ap.add_argument("--out-imgdat", default="work/l5/build/IMG.DAT.scenario_clear")
    ap.add_argument("--out-preview", default=None)
    ap.add_argument("--font", default=FONT)
    args = ap.parse_args()
    lang = language_from_args(args)
    text = args.text if args.text is not None else lang.scenario_clear
    if not text:
        raise SystemExit("no banner text: pass --text or set scenario_clear in the manifest")
    preview_path = (Path(args.out_preview) if args.out_preview
                    else lang.build_path("scenario_clear_{lang}_preview.png"))

    data = imd.read_img(args.imgdat)
    ent, asset = imd.get_asset(data, ASSET_INDEX)
    groups = imd.image_groups(asset)
    palettes = imd.clut_palettes(asset)
    if not groups or not palettes:
        raise SystemExit("asset 9 has no decodable images or palettes")

    start, packets, width, block_rows = groups[0]
    rows = imd.decode_image(asset, start, packets, width, block_rows)
    redraw_banner(rows, palettes[0], text, args.font, PS1_LAYOUT)

    patched = asset
    for start, packets, width, block_rows in groups:
        patched = imd.encode_image(patched, start, packets, width, block_rows, rows)
    imd.replace_asset(data, ASSET_INDEX, patched)
    out = Path(args.out_imgdat)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)

    height = len(rows)
    preview = Image.new("RGB", (width * 2, height * 2 * len(palettes)), (0, 0, 0))
    for pi, palette in enumerate(palettes):
        frame = Image.new("RGB", (width, height))
        frame.putdata([palette[v] for row in rows for v in row])
        preview.paste(frame.resize((width * 2, height * 2), Image.NEAREST), (0, pi * height * 2))
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(preview_path)
    print(f"patched IMG.DAT -> {out}")
    print(f"banner preview -> {preview_path}")


if __name__ == "__main__":
    main()
