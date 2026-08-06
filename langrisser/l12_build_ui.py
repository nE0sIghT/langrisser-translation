#!/usr/bin/env python3
"""Translate the tile-font UI strings and redraw the glyphs they need.

The strings `l12_uistrings` finds are laid out by position: the options screen
is fifteen six-character labels in one run, and a cursor lands on a fixed
character column. So a translation may not change a string's length, and each
cell has to stay where it was. A pack therefore gives the Russian as cells
separated by `|`, one per original character.

Sixteen pixels per cell is too little for a Russian word, so a cell holds up
to two letters, drawn side by side into one tile-font character - the same
pair-glyph trick the glyph plane already uses for the script. Each distinct
pair becomes one new character code, taken from the codes the font leaves
empty and then from the Japanese characters no surviving string still needs.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from langrisser import imgdat
from langrisser.game import add_game_args, game_from_args
from langrisser.l12_uistrings import (SPACE_CODE, decode, harvest,
                                      load_exe, load_ui_charmap)
from langrisser.project import add_language_args, language_from_args

# Which IMG.DAT asset holds which block of character codes.
FONT_ASSETS = {0: 37, 64: 38, 128: 63}
CODES_PER_ASSET = 64
CELL = 16
INK, OUTLINE, CLEAR = 1, 3, 0
EXE_HEADER = 0x800


def read_pack(path: Path) -> dict[str, str]:
    """Japanese string -> cells separated by `|`."""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if not row or row[0].startswith("#"):
                continue
            if len(row) != 2:
                raise SystemExit(f"{path}: expected two columns, got {row}")
            out[row[0]] = row[1]
    return out


EXPLICIT = "@"


def explicit_record(key: str) -> tuple[int, int] | None:
    """`@<file offset>:<characters>` - a record no scan for strings can find.

    Character code 0 is `ア`, so a run of labels cannot be null-terminated and
    the engine reads these by fixed length instead. The pre-battle menu is one
    such run: four six-character cells laid end to end.
    """
    if not key.startswith(EXPLICIT):
        return None
    offset, _, length = key[1:].partition(":")
    return int(offset, 0), int(length, 0)


def cells_of(value: str) -> list[str]:
    return [c for c in value.split("|")]


def render_pair(text: str, font: ImageFont.FreeTypeFont) -> Image.Image:
    """One tile-font character holding up to two letters, ink plus outline."""
    mask = Image.new("1", (CELL, CELL), 0)
    draw = ImageDraw.Draw(mask)
    draw.fontmode = "1"
    half = CELL // len(text) if text else CELL
    for i, ch in enumerate(text):
        box = draw.textbbox((0, 0), ch, font=font)
        w, h = box[2] - box[0], box[3] - box[1]
        x = i * half + max(0, (half - w) // 2) - box[0]
        y = (CELL - 1 - h) // 2 - box[1]
        draw.text((x, y), ch, font=font, fill=1)
    glyph = Image.new("P", (CELL, CELL))
    pixels = glyph.load()
    on = mask.load()
    for y in range(CELL):
        for x in range(CELL):
            pixels[x, y] = INK if on[x, y] else CLEAR
    for y in range(CELL):
        for x in range(CELL):
            if pixels[x, y] != CLEAR:
                continue
            if any(0 <= x + dx < CELL and 0 <= y + dy < CELL and on[x + dx, y + dy]
                   for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                                  (-1, -1), (1, -1), (-1, 1), (1, 1))):
                pixels[x, y] = OUTLINE
    return glyph


def write_glyph(pixels: bytearray, width: int, code: int, glyph: Image.Image) -> None:
    """Put a 16x16 character back as the four 8x8 tiles the engine expects."""
    local = code % CODES_PER_ASSET
    gx, gy = (local % 4) * 32, (local // 4) * 8
    src = glyph.load()
    for tile in range(4):
        ox, oy = (tile % 2) * 8, (tile // 2) * 8
        for y in range(8):
            for x in range(8):
                pixels[(gy + y) * width + gx + tile * 8 + x] = src[ox + x, oy + y]


def plan_codes(pack: dict[str, str], charmap: dict[int, str],
               strings) -> tuple[dict[str, int], list[str]]:
    """Assign a character code to every cell of Russian the pack asks for."""
    wanted: list[str] = []
    values = [pack.get(decode(s.codes, charmap)) for s in strings]
    values += [v for k, v in pack.items() if explicit_record(k)]
    for value in values:
        if value is None:
            continue
        for cell in cells_of(value):
            if cell.strip() and cell not in wanted:
                wanted.append(cell)

    # Only codes the font leaves blank are used. Reusing a drawn glyph needs
    # proof that nothing writes its code, and there is none to be had: the
    # pre-battle menu names its characters from a table read with a stride,
    # which no scan for strings finds, so a "free" kanji turned up on screen
    # as half a Russian word. A blank cell cannot be in use - drawing it
    # would show nothing - so blanks are the only safe pool.
    free = [c for c in range(max(FONT_ASSETS) + CODES_PER_ASSET)
            if c not in charmap and c != SPACE_CODE]
    if len(wanted) > len(free):
        raise SystemExit(
            f"{len(wanted)} cells need a character code and the font has "
            f"{len(free)} blank ones; shorten or drop strings in ui_strings.tsv")
    return {cell: free[i] for i, cell in enumerate(wanted)}, wanted


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_language_args(ap)
    add_game_args(ap, default="l1")
    ap.add_argument("--exe", required=True)
    ap.add_argument("--img-dat", default=None)
    ap.add_argument("--out-exe", required=True)
    ap.add_argument("--out-img-dat", required=True)
    args = ap.parse_args()

    game = game_from_args(args)
    lang = language_from_args(args)
    charmap = load_ui_charmap()
    pack = read_pack(lang.root / "ui_strings.tsv")
    exe_path = Path(args.exe)
    strings = harvest(exe_path)
    if not pack:
        print(f"{game.code}: no UI strings translated")
        pack = {}

    assignment, wanted = plan_codes(pack, charmap, strings)
    data, t_addr, _ = load_exe(exe_path)
    out = bytearray(data)
    written = 0
    for source in strings:
        value = pack.get(decode(source.codes, charmap))
        if value is None:
            continue
        cells = cells_of(value)
        if len(cells) != source.length:
            raise SystemExit(
                f"{decode(source.codes, charmap)!r} has {source.length} cells, "
                f"the pack gives {len(cells)}")
        encoded = bytes(SPACE_CODE if not c.strip() else assignment[c] for c in cells)
        out[source.file_offset:source.file_offset + len(encoded)] = encoded
        written += 1
    for key, value in pack.items():
        record = explicit_record(key)
        if record is None:
            continue
        offset, length = record
        cells = cells_of(value)
        if len(cells) != length:
            raise SystemExit(f"{key} holds {length} characters, "
                             f"the pack gives {len(cells)}")
        out[offset:offset + length] = bytes(
            SPACE_CODE if not c.strip() else assignment[c] for c in cells)
        written += 1

    img_src = Path(args.img_dat) if args.img_dat else Path(
        "work", game.code, "extracted", "IMG.DAT")
    archive = img_src.read_bytes()
    font = ImageFont.truetype(str(lang.font), size=lang.font_size)
    payloads = {}
    for base, asset in sorted(FONT_ASSETS.items()):
        codes = {c: t for t, c in assignment.items()
                 if base <= c < base + CODES_PER_ASSET}
        if not codes:
            continue
        _, packed = imgdat.get_asset(archive, asset)
        expanded = imgdat.lz_decompress(packed)
        width, height, _, _ = imgdat.lz_bitmap(expanded)
        image = imgdat.lz_bitmap_image(expanded)
        pixels = bytearray(image.tobytes())
        for code, text in codes.items():
            write_glyph(pixels, width, code, render_pair(text, font))
        image.frombytes(bytes(pixels))
        payloads[asset] = imgdat.lz_compress(
            imgdat.lz_replace_pixels(expanded, imgdat.lz_bitmap_pixels(
                image, width, height)))
    rebuilt = imgdat.rebuild_img_within(archive, payloads, len(archive))
    if len(rebuilt) > len(archive):
        raise SystemExit("redrawn font does not fit the archive")

    Path(args.out_exe).write_bytes(bytes(out))
    Path(args.out_img_dat).write_bytes(rebuilt)
    print(f"{game.code}: {written} UI strings, {len(wanted)} new characters "
          f"in {len(payloads)} font assets")


if __name__ == "__main__":
    main()
