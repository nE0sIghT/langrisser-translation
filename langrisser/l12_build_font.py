#!/usr/bin/env python3
"""Draw the target language's glyphs into a copy of `FONT.DAT`.

The plane is a bare file of 12x12 tiles, so this is the whole job: take the
slot table `l12_font_slots` wrote and redraw those tiles. The drawing itself is
`build_font`'s — same cell, same fonts, same baseline rules, and the pair
tiles it already knows how to pack two letters into — so only the container
differs and none of that is repeated here.

`build_font`'s own entry point cannot be pointed at this file: it also stamps
Langrisser V's native visual overrides, whose slot numbers mean something else
in this plane, and emits an insert table this engine has no use for.

The file may not change size. Nothing here can change it, since a slot is
written in place, but the check is cheap and a wrong assignment table would
otherwise be found much later.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from langrisser.build_font import pick_fonts, render_tile
from langrisser.engine import load_engine
from langrisser.game import add_game_args, game_from_args
from langrisser.l12_scen import MAX_SLOT
from langrisser.project import add_language_args, language_from_args

from PIL import ImageFont


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_language_args(ap)
    add_game_args(ap, default="l1")
    ap.add_argument("--font-dat", default=None, help="Original plane.")
    ap.add_argument("--out-font-dat", required=True)
    ap.add_argument("--assignments", default=None)
    args = ap.parse_args()

    game = game_from_args(args)
    lang = language_from_args(args)
    glyph = load_engine(game.engine).glyph
    src = Path(args.font_dat) if args.font_dat else Path(
        "work", game.code, "extracted", "FONT.DAT")
    table = Path(args.assignments) if args.assignments else lang.font_assignments

    fonts = pick_fonts(str(lang.font) if lang.font else "", lang.font_size)
    caps_fonts = []
    if lang.caps_font:
        caps_fonts.append(ImageFont.truetype(str(lang.caps_font),
                                             size=lang.caps_font_size or 12))

    data = bytearray(src.read_bytes())
    slots = len(data) // glyph.bytes_per_glyph
    drawn = 0
    for row in csv.DictReader(table.open(encoding="utf-8")):
        slot, text = int(row["index_dec"]), row["char"]
        if slot > MAX_SLOT:
            raise SystemExit(
                f"slot {slot} holds a tile no script string can name: the codec "
                f"reaches {MAX_SLOT}. Re-run l12_font_slots.")
        if slot >= slots:
            raise SystemExit(f"slot {slot} is past the end of {src}")
        at = slot * glyph.bytes_per_glyph
        data[at:at + glyph.bytes_per_glyph] = render_tile(text, fonts, caps_fonts)
        drawn += 1

    if len(data) != src.stat().st_size:
        raise SystemExit("the plane changed size, which the disc cannot take")
    out = Path(args.out_font_dat)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(data))
    print(f"{game.code}/{lang.code}: {drawn} tiles redrawn in {slots} -> {out}")


if __name__ == "__main__":
    main()
