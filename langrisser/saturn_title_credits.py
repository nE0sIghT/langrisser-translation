#!/usr/bin/env python3
"""Stamp the translator credit lines onto the Saturn title screens.

The visible title is not a linear bitmap: the container descriptor carries two
VDP2 tilemaps over one shared 8x8-cell store — an 80x28 hi-res overlay (logo,
"press start button", the (C) line; its transparent pixel value is the uniform
filler tile: 255 on TITLE1, 254 on TITLE2) and a 40x28 background plane. The
background plane also serves as the menu backdrop, so the credits must go onto
the *overlay* — the layer that is only ever the title text.

The credit lines are drawn with a transparent background into the overlay band
under the (C) line (y=193..223). The overlay's bottom rows reference the
shared filler tile, so each touched position gets a *new* cell: the cell store
is the container's last sub-asset, and it grows by the appended cells (TOC
size and the pattern-name entries are updated; the char-index field is 12
bits, so the grown store stays addressable). Non-filler cells referenced
exactly once are edited in place; a shared non-filler cell is a hard error.

Text rendering reuses the PS1 title-credit pipeline unchanged
(`langrisser.imgdat.title_text_mask` / `title_alpha_table` / `paste_alpha_mask`
with the PS1 line specs); masks are doubled horizontally because the overlay
is a 640-wide hi-res plane, so the on-screen proportions match PS1. The
emitted preview composites both planes for review.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path

from PIL import Image

from langrisser import saturn_container as sc
from langrisser.binfmt import BE
from langrisser.project import add_language_args, language_from_args
from langrisser import imgdat as imd

CELL_BYTES = 64          # 8x8 pixels, 8bpp
CHAR_MASK = 0x0FFF       # pattern-name character index field


@dataclass(frozen=True)
class LineSpec:
    font_size: int
    stroke_width: float
    raw_height: int
    y: int


# The PS1 line specs verbatim: the PS1 title bitmap is 640x225 — the same
# geometry as the Saturn overlay plane — so exact PS1 metric parity is the
# natural mask width and the PS1 pixel heights (11/9/9), only shifted up a
# line (PS1 places the lines at y=195/208/216 of its 225-row bitmap).
SATURN_CREDIT_SPECS = [
    LineSpec(font_size=20, stroke_width=0.14, raw_height=11, y=193),
    LineSpec(font_size=17, stroke_width=0.12, raw_height=9, y=205),
    LineSpec(font_size=17, stroke_width=0.12, raw_height=9, y=214),
]


@dataclass
class Screen:
    """The two tilemaps and shared cell store behind the title screen.

    The planes use different CRAM banks: the overlay's pattern-name entries
    carry palette bits 0 (first descriptor CLUT), the background's carry
    0x1000 (second CLUT) — visible in the (C) line, whose ink values are
    light through CLUT 1 and black through CLUT 2.
    """

    desc: bytearray
    cells: bytearray
    overlay_palette: list[tuple[int, int, int]]
    background_palette: list[tuple[int, int, int]]
    nt1_off: int
    overlay: list[list[int]]      # cols1 x rows1 pattern-name entries
    background: list[list[int]]   # cols2 x rows2


def parse_screen(cont: sc.Container) -> Screen:
    desc = bytearray(cont.sub(cont.entries[0]))
    cells = bytearray(cont.sub(cont.entries[1]))
    cols1, rows1 = BE.u16(desc, 0x04), BE.u16(desc, 0x06)
    cols2, rows2 = BE.u16(desc, 0x08), BE.u16(desc, 0x0A)
    nt1 = BE.u32(desc, 0x14)
    nt2 = nt1 + cols1 * rows1 * 2
    total = BE.u32(desc, 0x00)
    if total != nt2 + cols2 * rows2 * 2:
        raise SystemExit(
            f"unexpected TITLE descriptor layout: total {total:#x} != "
            f"tables end {nt2 + cols2 * rows2 * 2:#x}"
        )
    clut1 = sc.find_clut_offset(desc)
    clut2 = sc.image_clut_offset(desc)
    if clut1 is None or clut2 is None:
        raise SystemExit("missing CLUTs in the TITLE descriptor")
    overlay_palette = sc.read_clut(bytes(desc), clut1)
    background_palette = sc.read_clut(bytes(desc), clut2)

    def table(off: int, cols: int, rows: int) -> list[list[int]]:
        return [
            [BE.u16(desc, off + (cy * cols + cx) * 2) for cx in range(cols)]
            for cy in range(rows)
        ]

    return Screen(desc, cells, overlay_palette, background_palette, nt1,
                  table(nt1, cols1, rows1), table(nt2, cols2, rows2))


def cell_usage(screen: Screen) -> dict[int, int]:
    usage: dict[int, int] = {}
    for plane in (screen.overlay, screen.background):
        for row in plane:
            for entry in row:
                idx = entry & CHAR_MASK
                usage[idx] = usage.get(idx, 0) + 1
    return usage


def compose_plane(screen: Screen, plane: list[list[int]]) -> list[bytearray]:
    rows = [bytearray(len(plane[0]) * 8) for _ in range(len(plane) * 8)]
    for cy, entries in enumerate(plane):
        for cx, entry in enumerate(entries):
            idx = entry & CHAR_MASK
            tile = screen.cells[idx * CELL_BYTES:(idx + 1) * CELL_BYTES]
            for py in range(8):
                rows[cy * 8 + py][cx * 8:cx * 8 + 8] = tile[py * 8:py * 8 + 8]
    return rows


def overlay_transparent_value(screen: Screen) -> int:
    """The overlay's transparent pixel value = its uniform filler tile.

    The filler differs per screen (255 on TITLE1, 254 on TITLE2), so it is
    derived from the most common overlay entry rather than hard-coded.
    """
    counts: dict[int, int] = {}
    for row in screen.overlay:
        for entry in row:
            counts[entry] = counts.get(entry, 0) + 1
    filler = max(counts, key=counts.get) & CHAR_MASK
    tile = screen.cells[filler * CELL_BYTES:(filler + 1) * CELL_BYTES]
    values = set(tile)
    if len(values) != 1:
        raise SystemExit("overlay filler tile is not uniform; cannot infer transparency")
    return tile[0]


def credit_lines(args: argparse.Namespace, lang) -> list[str]:
    """The pack's credit lines, exactly as the PS1 build renders them."""
    if args.line:
        return list(args.line)
    config = Path(args.credits_json) if args.credits_json else lang.title_credits
    return imd.load_title_credit_lines(
        config if config.exists() else None,
        args.version, imd.git_short_hash())


def hires_mask(line: str, font_path: str, spec: LineSpec, width: int) -> Image.Image:
    """PS1-spec mask at natural width: both title bitmaps are 640 wide."""
    for size in range(spec.font_size, 9, -1):
        mask = imd.title_text_mask(line, font_path, size, spec.stroke_width)
        raw = mask.resize((mask.width, spec.raw_height),
                          Image.Resampling.LANCZOS)
        if raw.width <= width:
            return raw
    raise SystemExit(f"credit line does not fit {width}px: {line!r}")


def overlay_alpha_table(palette: list[tuple[int, int, int]], transparent: int,
                        target_rgb: tuple[int, int, int]) -> list[int]:
    """Alpha -> overlay palette index along a black->target ramp."""
    candidates = [(i, c) for i, c in enumerate(palette) if i != transparent]
    table: list[int] = []
    for alpha in range(256):
        t = alpha / 255.0
        desired = tuple(target_rgb[ch] * t for ch in range(3))
        best, _ = min(candidates, key=lambda item: sum(
            (item[1][ch] - desired[ch]) ** 2 for ch in range(3)))
        table.append(best)
    return table


def stamp_overlay(screen: Screen, lines: list[str], font_path: str) -> None:
    plane = screen.overlay
    width, height = len(plane[0]) * 8, len(plane) * 8
    transparent = overlay_transparent_value(screen)
    rows = compose_plane(screen, plane)
    original = [bytes(row) for row in rows]

    shim = SimpleNamespace(width=width, height=height,
                           background_index=transparent)
    # Ink comes from the overlay's own palette (CLUT 1) with no candidate
    # filters: a plain black->target ramp mapped to the nearest palette
    # colours, so the credits carry the same tones as the (C) line above.
    alpha_table = overlay_alpha_table(screen.overlay_palette, transparent,
                                      imd.TITLE_CREDIT_TARGET_RGB)
    for spec, line in zip(SATURN_CREDIT_SPECS, lines):
        raw = hires_mask(line, font_path, spec, width)
        imd.paste_alpha_mask(rows, shim, raw,
                             (width - raw.width) // 2, spec.y, alpha_table)

    # The PS1 titles carry a QR code to the project page (x=550, y=6,
    # 2x1 modules); parity requires it here too. Dark modules use the
    # darkest opaque overlay ink (the transparent filler would let the
    # background art bleed through the code).
    dark = min((i for i in range(len(screen.overlay_palette)) if i != transparent),
               key=lambda i: sum(c * c for c in screen.overlay_palette[i]))
    qr_shim = SimpleNamespace(width=width, height=height, background_index=dark)
    imd.draw_title_qr(rows, qr_shim, screen.overlay_palette,
                      imd.TITLE_QR_URL, 550, 6)

    usage = cell_usage(screen)
    filler_tile = bytes([transparent]) * CELL_BYTES
    for cy in range(len(plane)):
        for cx in range(len(plane[0])):
            new_tile = b"".join(
                bytes(rows[cy * 8 + py][cx * 8:cx * 8 + 8]) for py in range(8)
            )
            old_tile = b"".join(
                original[cy * 8 + py][cx * 8:cx * 8 + 8] for py in range(8)
            )
            if new_tile == old_tile:
                continue
            entry = plane[cy][cx]
            idx = entry & CHAR_MASK
            if usage[idx] == 1:
                screen.cells[idx * CELL_BYTES:(idx + 1) * CELL_BYTES] = new_tile
                continue
            if old_tile != filler_tile:
                raise SystemExit(
                    f"credit pixels hit shared non-filler cell {idx:#x} at "
                    f"({cx},{cy}); move the lines"
                )
            new_idx = len(screen.cells) // CELL_BYTES
            if new_idx > CHAR_MASK:
                raise SystemExit("cell store full: char index field is 12 bits")
            screen.cells += new_tile
            plane[cy][cx] = (entry & ~CHAR_MASK) | new_idx
    # write the updated overlay entries back into the descriptor
    cols = len(plane[0])
    for cy, entries in enumerate(plane):
        for cx, entry in enumerate(entries):
            off = screen.nt1_off + (cy * cols + cx) * 2
            screen.desc[off:off + 2] = BE.pack_u16(entry)


def screen_preview(screen: Screen) -> Image.Image:
    """Composite both planes the way the console shows them (640x224)."""
    bg = compose_plane(screen, screen.background)
    fg = compose_plane(screen, screen.overlay)
    transparent = overlay_transparent_value(screen)
    width, height = len(fg[0]), len(fg)
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        for x in range(width):
            value = fg[y][x]
            if value == transparent:
                px[x, y] = screen.background_palette[bg[y][x // 2]]
            else:
                px[x, y] = screen.overlay_palette[value]
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_language_args(ap)
    ap.add_argument("--title", default="work/l5/build/saturn/TITLE1.DAT")
    ap.add_argument("--out-title", default="work/l5/build/saturn/TITLE1.ru.DAT")
    ap.add_argument("--out-preview", default="work/l5/build/saturn/title_credits_preview.png")
    ap.add_argument("--font", default=None,
                    help="credit font (default: the PS1 title-credit font)")
    ap.add_argument("--line", action="append",
                    help="override a credit line (repeatable); default uses the pack's set")
    ap.add_argument("--credits-json", default=None,
                    help="title-credit line templates (default: the pack's file)")
    ap.add_argument("--version", default="dev",
                    help="patch version substituted into the credit templates")
    args = ap.parse_args()
    lang = language_from_args(args)

    src = Path(args.title).read_bytes()
    cont = sc.load(args.title)
    desc_entry, cells_entry = cont.entries[0], cont.entries[1]
    if cells_entry.end != len(src):
        raise SystemExit("cell store is not the last sub-asset; cannot grow")
    screen = parse_screen(cont)
    font_path = imd.resolve_title_font(args.font)
    lines = credit_lines(args, lang)
    if not 1 <= len(lines) <= len(SATURN_CREDIT_SPECS):
        raise SystemExit(
            f"expected 1-{len(SATURN_CREDIT_SPECS)} credit lines, got {len(lines)}")
    grown_from = len(screen.cells)
    stamp_overlay(screen, lines, font_path)

    data = bytearray(src)
    assert len(screen.desc) == desc_entry.size, "descriptor size must be preserved"
    data[desc_entry.offset:desc_entry.end] = screen.desc
    data[16:20] = BE.pack_u32(len(screen.cells))   # TOC entry 1 size
    data[cells_entry.offset:] = screen.cells
    out = Path(args.out_title)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(data))

    screen_preview(screen).save(args.out_preview)
    added = (len(screen.cells) - grown_from) // CELL_BYTES
    print(f"patched title -> {out}  ({len(lines)} credit lines on the overlay plane, "
          f"+{added} cells, file {len(src)} -> {len(data)})")
    print(f"screen preview -> {args.out_preview}")


if __name__ == "__main__":
    main()
