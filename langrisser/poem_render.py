"""Shared renderer for the Langrisser V prologue poem graphics.

The PS1 and Saturn releases store the attract-loop poem differently, but the
text source, palette indices, line rasterisation and vertical layout rules are
shared. Platform-specific scripts should render through this module and only own
their final container packing (`IMG.DAT` packets on PS1, `OPEN.DAT` VDP1 runs on
Saturn).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BG_INDEX = 0
OUTLINE_INDEX = 212
RED_RAMP = ((185, 89), (120, 176), (55, 209))
FONT = "data/fonts/DejaVuSerif-Bold.ttf"
HORIZONTAL_MARGIN = 8
TOP_MARGIN = 24
BOTTOM_EMPTY = 44
MAX_PITCH = 20
MIN_PITCH = 14
SUPERSAMPLE = 4


@dataclass(frozen=True)
class LineStamp:
    rows: list[bytearray]
    bbox_top: int
    bbox_bottom: int


@dataclass(frozen=True)
class PoemLayout:
    lines: list[str]
    stamps: list[LineStamp | None]
    blocks: list[list[LineStamp]]
    pitch: int
    rows: list[bytearray]


def load_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        if line.strip() == "---":
            continue
        lines.append(line)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def ramp_index(value: int, red_ramp: tuple[tuple[int, int], ...] = RED_RAMP) -> int | None:
    for threshold, index in red_ramp:
        if value >= threshold:
            return index
    return None


def make_line_stamp(
    text: str,
    width: int,
    font_path: str,
    *,
    font_size: int,
    line_height: int,
    horizontal_margin: int = HORIZONTAL_MARGIN,
    bg_index: int = BG_INDEX,
    outline_index: int = OUTLINE_INDEX,
    red_ramp: tuple[tuple[int, int], ...] = RED_RAMP,
    supersample: int = SUPERSAMPLE,
) -> LineStamp:
    stamp = [bytearray([bg_index] * width) for _ in range(line_height)]
    if not text.strip():
        return LineStamp(stamp, 0, 0)

    big = Image.new("L", (width * supersample, line_height * supersample), 0)
    draw = ImageDraw.Draw(big)
    font = ImageFont.truetype(font_path, font_size * supersample)
    text_w = draw.textlength(text, font=font)
    max_text_w = (width - 2 * horizontal_margin) * supersample
    if text_w > max_text_w:
        raise ValueError(
            f"poem line is too wide ({text_w / supersample:.1f}px > "
            f"{max_text_w / supersample:.1f}px): {text!r}"
        )

    draw.text(((width * supersample - text_w) / 2, supersample), text, fill=255, font=font)
    resampling = getattr(Image, "Resampling", Image)
    small = big.resize((width, line_height), resampling.LANCZOS)
    glyph = small.load()
    outline = small.filter(ImageFilter.MaxFilter(3)).load()
    edge_alpha = red_ramp[-1][0]
    bbox_top = line_height
    bbox_bottom = 0
    for yy in range(line_height):
        for xx in range(width):
            index = ramp_index(glyph[xx, yy], red_ramp)
            if index is not None:
                stamp[yy][xx] = index
                bbox_top = min(bbox_top, yy)
                bbox_bottom = max(bbox_bottom, yy + 1)
            elif outline[xx, yy] >= edge_alpha:
                stamp[yy][xx] = outline_index
                bbox_top = min(bbox_top, yy)
                bbox_bottom = max(bbox_bottom, yy + 1)
    if bbox_bottom == 0:
        return LineStamp(stamp, 0, 0)
    return LineStamp(stamp, bbox_top, bbox_bottom)


def paint_stamp(rows: list[bytearray], stamp: LineStamp, top_y: int,
                bg_index: int = BG_INDEX) -> None:
    height = len(rows)
    for yy, stamp_row in enumerate(stamp.rows):
        gy = top_y + yy
        if not 0 <= gy < height:
            continue
        dst = rows[gy]
        for xx, index in enumerate(stamp_row):
            if index != bg_index:
                dst[xx] = index


def split_blocks(lines: list[str], stamps: list[LineStamp | None]) -> list[list[LineStamp]]:
    blocks: list[list[LineStamp]] = []
    block: list[LineStamp] = []
    for line, stamp in zip(lines, stamps):
        if not line.strip():
            if block:
                blocks.append(block)
                block = []
            continue
        if stamp is None:
            raise ValueError("missing line stamp")
        block.append(stamp)
    if block:
        blocks.append(block)
    return blocks


def layout_strip(
    lines: list[str],
    stamps: list[LineStamp | None],
    *,
    width: int,
    strip_height: int,
    line_height: int,
    top_margin: int = TOP_MARGIN,
    bottom_empty: int = BOTTOM_EMPTY,
    min_pitch: int = MIN_PITCH,
    max_pitch: int = MAX_PITCH,
    bg_index: int = BG_INDEX,
) -> tuple[int, list[bytearray]]:
    last_slot = max(1, len(lines) - 1)
    usable = strip_height - top_margin - line_height - bottom_empty
    pitch = max(min_pitch, min(max_pitch, usable // last_slot))
    rows = [bytearray([bg_index] * width) for _ in range(strip_height)]
    for slot, (line, stamp) in enumerate(zip(lines, stamps)):
        if stamp is None or not line.strip():
            continue
        paint_stamp(rows, stamp, top_margin + slot * pitch, bg_index)
    return pitch, rows


def render_poem_strip(
    lines: list[str],
    *,
    width: int,
    strip_height: int,
    font_path: str = FONT,
    font_size: int,
    line_height: int,
    horizontal_margin: int = HORIZONTAL_MARGIN,
    top_margin: int = TOP_MARGIN,
    bottom_empty: int = BOTTOM_EMPTY,
    min_pitch: int = MIN_PITCH,
    max_pitch: int = MAX_PITCH,
    expected_blocks: int | None = 4,
) -> PoemLayout:
    if not any(s.strip() for s in lines):
        raise ValueError("poem file has no text lines")

    stamps = [
        make_line_stamp(
            line,
            width,
            font_path,
            font_size=font_size,
            line_height=line_height,
            horizontal_margin=horizontal_margin,
        ) if line.strip() else None
        for line in lines
    ]
    blocks = split_blocks(lines, stamps)
    if expected_blocks is not None and len(blocks) != expected_blocks:
        raise ValueError(f"expected {expected_blocks} poem blocks, got {len(blocks)}")

    pitch, rows = layout_strip(
        lines,
        stamps,
        width=width,
        strip_height=strip_height,
        line_height=line_height,
        top_margin=top_margin,
        bottom_empty=bottom_empty,
        min_pitch=min_pitch,
        max_pitch=max_pitch,
    )
    return PoemLayout(lines, stamps, blocks, pitch, rows)


def save_indexed_preview(rows: list[bytearray], palette: list[tuple[int, int, int]],
                         path: Path) -> None:
    img = Image.new("RGB", (len(rows[0]), len(rows)), (0, 0, 0))
    px = img.load()
    for y, row in enumerate(rows):
        for x, value in enumerate(row):
            px[x, y] = palette[value] if value else (0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
