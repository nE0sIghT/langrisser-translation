#!/usr/bin/env python3
"""Shared banner-lettering redraw for 8bpp indexed art (PS1 and Saturn).

Both the PS1 SCENARIO CLEAR banner (IMG.DAT asset 9) and the Saturn banner
(CLEAR.DAT texture) are 8bpp indexed images with the lettering on a flat field
between fixed ornaments. Translating either one means erasing just the lettering
rectangle and redrawing the target text there, transferring colours from the
original letters pixel-by-pixel (nearest sample in the same row) so the vertical
gradient and horizontal colour drift survive. That redraw is identical on both
platforms; only the container I/O, geometry and background index differ, which a
:class:`BannerLayout` carries. See scenario_clear.py (PS1) and
saturn_scenario_clear.py (Saturn).
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class BannerLayout:
    """Per-asset geometry and thresholds for the lettering redraw."""

    text_x0: int
    text_x1: int
    text_y0: int
    text_y1: int
    cap_top: int
    baseline: int
    paint_y0: int
    paint_y1: int
    bg_index: int
    banner_width: int = 224
    bright_lum: float = 110
    mid_lum: float = 45
    bright_alpha: int = 160
    mid_alpha: int = 70
    supersample: int = 4


def luminance(color: tuple[int, int, int]) -> float:
    return (color[0] * 3 + color[1] * 6 + color[2]) / 10


def collect_row_samples(rows: list[bytearray], palette: list[tuple[int, int, int]],
                        layout: BannerLayout
                        ) -> tuple[dict[int, list[tuple[int, int]]], dict[int, list[tuple[int, int]]]]:
    """Per-row (x, index) samples of the original letter fill and midtones."""
    bright: dict[int, list[tuple[int, int]]] = {}
    mid: dict[int, list[tuple[int, int]]] = {}
    for y in range(layout.text_y0, layout.text_y1):
        for x in range(layout.text_x0, layout.text_x1):
            index = rows[y][x]
            if index == layout.bg_index:
                continue
            lum = luminance(palette[index])
            if lum > layout.bright_lum:
                bright.setdefault(y, []).append((x, index))
            elif lum > layout.mid_lum:
                mid.setdefault(y, []).append((x, index))
    return bright, mid


def nearest_sample(samples: dict[int, list[tuple[int, int]]], y: int, x: int,
                   layout: BannerLayout) -> int | None:
    """Index of the sample nearest to (x, y): same row first, else nearest row."""
    for dy in range(0, layout.text_y1 - layout.paint_y0):
        for yy in (y - dy, y + dy) if dy else (y,):
            row = samples.get(yy)
            if not row:
                continue
            pos = bisect.bisect_left(row, (x,))
            best = None
            for cand in (row[pos - 1] if pos else None,
                         row[pos] if pos < len(row) else None):
                if cand and (best is None or abs(cand[0] - x) < abs(best[0] - x)):
                    best = cand
            return best[1]
    return None


def render_mask(text: str, font_path: str, layout: BannerLayout) -> Image.Image:
    """Text alpha mask sized to the banner, caps on the original cap band."""
    ss = layout.supersample
    cap_target = (layout.baseline - layout.cap_top) * ss
    font = None
    probe = Image.new("L", (8, 8))
    d = ImageDraw.Draw(probe)
    for cand in range(cap_target // 2, cap_target * 2):
        f = ImageFont.truetype(font_path, cand)
        bbox = d.textbbox((0, 0), "H", font=f)
        if bbox[3] - bbox[1] >= cap_target:
            font = f
            break
    if font is None:
        raise SystemExit(f"cannot reach cap height {cap_target} with {font_path}")
    width, height = layout.banner_width * ss, (layout.paint_y1 - layout.paint_y0) * ss
    big = Image.new("L", (width * 2, height), 0)
    d = ImageDraw.Draw(big)
    bbox = d.textbbox((0, 0), "H", font=font)
    baseline_y = (layout.baseline - layout.paint_y0) * ss
    d.text((width // 2, baseline_y - bbox[3]), text, fill=255, font=font)
    ink = big.getbbox()
    if ink is None:
        raise SystemExit("banner text rendered empty")
    big = big.crop((ink[0], 0, ink[2], height))
    max_w = (layout.text_x1 - layout.text_x0 - 4) * ss
    new_w = min(big.width, max_w)
    return big.resize((new_w // ss, height // ss), Image.LANCZOS)


def redraw_banner(rows: list[bytearray], palette: list[tuple[int, int, int]],
                  text: str, font_path: str, layout: BannerLayout) -> list[bytearray]:
    """Erase the lettering rectangle and redraw `text` in place, transferring
    the original letters' colours. Mutates and returns `rows`."""
    bright, mid = collect_row_samples(rows, palette, layout)
    if not bright:
        raise SystemExit("no letter fill samples found; wrong asset layout?")
    for y in range(layout.text_y0, layout.text_y1):
        for x in range(layout.text_x0, layout.text_x1):
            rows[y][x] = layout.bg_index

    mask = render_mask(text, font_path, layout)
    mx0 = (layout.text_x0 + layout.text_x1 - mask.width) // 2
    alpha = mask.load()
    for yy in range(mask.height):
        y = layout.paint_y0 + yy
        for xx in range(mask.width):
            value = alpha[xx, yy]
            if value < layout.mid_alpha:
                continue
            samples = bright if value >= layout.bright_alpha else mid
            index = nearest_sample(samples, y, mx0 + xx, layout)
            if index is not None:
                rows[y][mx0 + xx] = index
    return rows
