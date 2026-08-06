#!/usr/bin/env python3
"""Utilities for Langrisser V /L5/IMG.DAT.

IMG.DAT is handled in two layers:

* a generic offset-table archive with same-size asset replacement;
* image codecs for known asset payload layouts.

The verified editable payload is the type-8 8bpp indexed scanline-packet
layout used by the title screens, the prologue poem and several other images.
Unsupported packet types are listed by `inspect`/`list` but are not decoded.
"""
from __future__ import annotations

import argparse
import json
import struct
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


TOC_SIZE = 0x800
DEFAULT_PATCH_VERSION = "1"
TITLE_CREDIT_TARGET_RGB = (150, 220, 230)
TITLE_CREDIT_FONT_CANDIDATES = [
    "data/fonts/LiberationSansNarrow-Bold.ttf",
]
TITLE_QR_URL = "https://github.com/nE0sIghT/langrisser-5-translation"
TITLE_QR_MATRIX_FOR_DEFAULT_URL = (
    "00000000000000000000000000000000000000000",
    "00000000000000000000000000000000000000000",
    "00000000000000000000000000000000000000000",
    "00000000000000000000000000000000000000000",
    "00001111111000000100110100010011111110000",
    "00001000001000101111010010100010000010000",
    "00001011101010101101111011000010111010000",
    "00001011101011110010100000101010111010000",
    "00001011101011011001001001110010111010000",
    "00001000001010100001101000000010000010000",
    "00001111111010101010101010101011111110000",
    "00000000000011000011001111001000000000000",
    "00001011111000011111010100000011111000000",
    "00000001000010100000100011010011011010000",
    "00001111101100001001101000001110101100000",
    "00001100000010001010100111101100111010000",
    "00001100111001011000001100111001110000000",
    "00000100100110011111100110010111000110000",
    "00000010101001101100011010101111111100000",
    "00001001110000010010100111011111011000000",
    "00000000101100010110000100101101110010000",
    "00000011100101110010011110010011011110000",
    "00001101101000101101010011101101101100000",
    "00001001100111000001111111000101111110000",
    "00001111111011010001100100100000110100000",
    "00001111000111011111011111010010001010000",
    "00001001011010101101101010001100010100000",
    "00001010000110111101000001100110011000000",
    "00001010101111111011110100101111110100000",
    "00000000000010010011101101011000101010000",
    "00001111111000101111001011011010101100000",
    "00001000001010010011000011111000111010000",
    "00001011101010100000001100101111110110000",
    "00001011101010101111100110111100101110000",
    "00001011101010101110010000100011010000000",
    "00001000001000100100100011110110111000000",
    "00001111111010000000010000111101000100000",
    "00000000000000000000000000000000000000000",
    "00000000000000000000000000000000000000000",
    "00000000000000000000000000000000000000000",
    "00000000000000000000000000000000000000000",
)


@dataclass(frozen=True)
class TitleCreditLineSpec:
    line_index: int
    font_size: int
    stroke_width: float
    raw_height: int
    global_y: int


@dataclass(frozen=True)
class AssetEntry:
    index: int
    offset: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.offset


@dataclass(frozen=True)
class GapBitmapProfile:
    name: str
    asset_index: int
    rel_offset: int
    width: int
    height: int
    block_rows: int
    block_bytes: int
    gap_bytes: int
    gap_after_x_by_row_pos: dict[int, int]
    global_row0: int
    background_index: int
    ink_index: int
    palette_rel_offset: int | None = None
    palette_entries: int = 256

    def row_layout(self, logical_y: int) -> tuple[int, int | None, int]:
        if logical_y < 0 or logical_y >= self.height:
            raise ValueError(f"row out of range: {logical_y}")
        block = logical_y // self.block_rows
        row_pos = logical_y % self.block_rows
        off = self.rel_offset + block * self.block_bytes
        for pos in range(row_pos):
            off += self.width + (self.gap_bytes if pos in self.gap_after_x_by_row_pos else 0)
        gap_after_x = self.gap_after_x_by_row_pos.get(row_pos)
        physical_len = self.width + (self.gap_bytes if gap_after_x is not None else 0)
        return off, gap_after_x, physical_len

    def logical_y_from_global(self, global_y: int) -> int:
        return global_y - self.global_row0


TITLE_GAP_AFTER_X_BY_ROW_POS = {
    3: 96,
    6: 192,
    9: 288,
    12: 384,
    15: 480,
    18: 576,
    22: 32,
}

# Gap-bitmap unification. An IMG.DAT image is stored as whole PlayStation VRAM
# scanlines: each 2048-byte scanline holds 2016 bytes of image data followed by
# a 32-byte strip (the scanline's unused 16-px right edge in VRAM). So a 32-byte
# gap is inserted into the logical pixel stream every 2016 bytes. A 0x4000 block
# is exactly eight scanlines (8 * 2048). For width 640 that is 25.2 rows, so the
# title profile packs 25 rows (7 gaps) and 160 padding bytes per block; for
# width 768 it is exactly 21 rows (8 gaps) with no padding.
VRAM_SCANLINE_BYTES = 2048
SCANLINE_GAP_BYTES = 32
SCANLINE_DATA_BYTES = VRAM_SCANLINE_BYTES - SCANLINE_GAP_BYTES  # 2016


def scanline_gaps(width: int, block_rows: int) -> dict[int, int]:
    """Gap positions (row_pos -> gap_after_x) for a gap-bitmap of this width.

    A 32-byte gap falls every SCANLINE_DATA_BYTES logical bytes. A boundary gap
    that lands exactly on the block's last row is recorded as gap_after_x=width.
    """
    gaps: dict[int, int] = {}
    for k in range(1, (block_rows * width) // SCANLINE_DATA_BYTES + 1):
        row, x = divmod(k * SCANLINE_DATA_BYTES, width)
        if row < block_rows:
            gaps[row] = x
        else:
            gaps[block_rows - 1] = width  # boundary gap closes the last row
    return gaps


# The hand-verified title layout must equal the generic rule.
assert scanline_gaps(640, 25) == TITLE_GAP_AFTER_X_BY_ROW_POS

PROFILES = {
    "title10": GapBitmapProfile(
        name="title10",
        asset_index=10,
        rel_offset=0x12020,
        width=640,
        height=225,
        block_rows=25,
        block_bytes=0x4000,
        gap_bytes=32,
        gap_after_x_by_row_pos=TITLE_GAP_AFTER_X_BY_ROW_POS,
        global_row0=0,
        background_index=254,
        ink_index=44,
        palette_rel_offset=0x36220,
    ),
    "title11": GapBitmapProfile(
        name="title11",
        asset_index=11,
        rel_offset=0x12020,
        width=640,
        height=225,
        block_rows=25,
        block_bytes=0x4000,
        gap_bytes=32,
        gap_after_x_by_row_pos=TITLE_GAP_AFTER_X_BY_ROW_POS,
        global_row0=0,
        background_index=254,
        ink_index=44,
        palette_rel_offset=0x36220,
    ),
}
TITLE_PATCH_PROFILE_NAMES = ("title10", "title11")

TITLE_CREDIT_SPECS = [
    TitleCreditLineSpec(line_index=0, font_size=20, stroke_width=0.14, raw_height=11, global_y=195),
    TitleCreditLineSpec(line_index=1, font_size=17, stroke_width=0.12, raw_height=9, global_y=208),
    TitleCreditLineSpec(line_index=2, font_size=17, stroke_width=0.12, raw_height=9, global_y=216),
]


def git_short_hash(repo_root: str | Path | None = None) -> str:
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["git", "rev-parse", "--short=8", "HEAD"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"failed to read git commit hash: {proc.stderr.strip()}")
    value = proc.stdout.strip()
    if not value:
        raise RuntimeError("git returned an empty commit hash")
    return value


def default_title_credit_lines(version: str, commit_hash: str) -> list[str]:
    return [
        f'Translation v{version} ({commit_hash}) by Yuri "nE0sIghT" Konotopov',
        "Thanks to CyberWarriorX for the Langrisser III toolkit",
        "Thanks to borgor for the Langrisser V translation guide",
    ]


def load_title_credit_lines(
    config_path: str | Path | None,
    version: str,
    commit_hash: str,
) -> list[str]:
    if config_path is None:
        return default_title_credit_lines(version, commit_hash)
    path = Path(config_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        raise ValueError(f"{path}: expected an object with a lines array")
    lines = data["lines"]
    if not 1 <= len(lines) <= len(TITLE_CREDIT_SPECS):
        raise ValueError(
            f"{path}: expected 1-{len(TITLE_CREDIT_SPECS)} title-credit lines"
        )
    result = []
    for line in lines:
        if not isinstance(line, str) or not line.strip():
            raise ValueError(f"{path}: title-credit lines must be non-empty strings")
        try:
            result.append(line.format(version=version, commit=commit_hash))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{path}: invalid title-credit template {line!r}") from exc
    return result


def qr_matrix_for_url(url: str) -> list[list[bool]]:
    if url == TITLE_QR_URL:
        return [[ch == "1" for ch in row] for row in TITLE_QR_MATRIX_FOR_DEFAULT_URL]

    try:
        import qrcode
    except ImportError as exc:
        raise RuntimeError(
            "non-default QR URLs require the optional qrcode package; "
            "the default release URL is embedded"
        ) from exc

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=4,
        box_size=1,
    )
    qr.add_data(url)
    qr.make(fit=True)
    return [[bool(cell) for cell in row] for row in qr.get_matrix()]


def nearest_palette_index(palette: list[tuple[int, int, int]], target: tuple[int, int, int]) -> int:
    return min(
        range(len(palette)),
        key=lambda i: sum((palette[i][channel] - target[channel]) ** 2 for channel in range(3)),
    )


def title_alpha_table(
    palette: list[tuple[int, int, int]],
    profile: GapBitmapProfile,
    target_rgb: tuple[int, int, int],
) -> list[int]:
    bg = palette[profile.background_index]
    candidates: list[tuple[int, tuple[int, int, int]]] = []
    for index, color in enumerate(palette):
        red, green, blue = color
        if index == profile.background_index:
            candidates.append((index, color))
            continue
        if max(color) > 235:
            continue
        if red > green + 25 and red > blue + 25:
            continue
        candidates.append((index, color))

    table: list[int] = []
    for alpha in range(256):
        t = alpha / 255.0
        desired = tuple(bg[channel] * (1 - t) + target_rgb[channel] * t for channel in range(3))
        best_index, _ = min(
            candidates,
            key=lambda item: sum((item[1][channel] - desired[channel]) ** 2 for channel in range(3)),
        )
        table.append(best_index)
    return table


def resolve_title_font(font_path: str | None) -> str:
    candidates = [font_path] if font_path else TITLE_CREDIT_FONT_CANDIDATES
    for candidate in candidates:
        if candidate is None:
            continue
        path = Path(candidate)
        if path.exists():
            return str(path)
    checked = ", ".join(str(c) for c in candidates if c)
    raise FileNotFoundError(f"title credit font not found; checked: {checked}")


def title_text_mask(
    text: str,
    font_path: str,
    font_size: int,
    stroke_width: float,
    supersample: int = 8,
) -> Image.Image:
    font = ImageFont.truetype(font_path, font_size * supersample)
    scratch = Image.new("L", (2048 * supersample, 128 * supersample), 0)
    draw = ImageDraw.Draw(scratch)
    draw.fontmode = "L"
    stroke = int(round(stroke_width * supersample))
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    width = bbox[2] - bbox[0] + 8 * supersample
    height = bbox[3] - bbox[1] + 8 * supersample
    if width <= 0 or height <= 0:
        raise ValueError(f"empty text mask for {text!r}")

    hi = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(hi)
    draw.fontmode = "L"
    draw.text(
        (4 * supersample - bbox[0], 4 * supersample - bbox[1]),
        text,
        font=font,
        fill=255,
        stroke_width=stroke,
        stroke_fill=255,
    )
    lo = hi.resize((max(1, width // supersample), max(1, height // supersample)), Image.Resampling.LANCZOS)
    bbox = lo.getbbox()
    if bbox is None:
        return lo
    return lo.crop(
        (
            max(0, bbox[0] - 1),
            max(0, bbox[1] - 1),
            min(lo.width, bbox[2] + 1),
            min(lo.height, bbox[3] + 1),
        )
    )


def paste_alpha_mask(
    rows: list[bytearray],
    profile: GapBitmapProfile,
    mask: Image.Image,
    x0: int,
    y0: int,
    alpha_table: list[int],
) -> None:
    pixels = mask.load()
    for y in range(mask.height):
        yy = y0 + y
        if yy < 0 or yy >= profile.height:
            continue
        row = rows[yy]
        for x in range(mask.width):
            xx = x0 + x
            if xx < 0 or xx >= profile.width:
                continue
            alpha = pixels[x, y]
            if alpha < 4:
                continue
            row[xx] = alpha_table[alpha]


def draw_title_credits(
    rows: list[bytearray],
    profile: GapBitmapProfile,
    palette: list[tuple[int, int, int]],
    lines: list[str],
    font_path: str | None = None,
) -> None:
    resolved_font = resolve_title_font(font_path)
    alpha_table = title_alpha_table(palette, profile, TITLE_CREDIT_TARGET_RGB)
    for spec, line in zip(TITLE_CREDIT_SPECS, lines):
        display_mask = title_text_mask(
            line,
            resolved_font,
            spec.font_size,
            spec.stroke_width,
        )
        raw_mask = display_mask.resize((display_mask.width, spec.raw_height), Image.Resampling.LANCZOS)
        if raw_mask.width > profile.width:
            raise ValueError(
                f"title-credit line {spec.line_index + 1} is too wide: "
                f"{raw_mask.width}>{profile.width}"
            )
        x = (profile.width - raw_mask.width) // 2
        y = profile.logical_y_from_global(spec.global_y)
        paste_alpha_mask(rows, profile, raw_mask, x, y, alpha_table)


def draw_title_qr(
    rows: list[bytearray],
    profile: GapBitmapProfile,
    palette: list[tuple[int, int, int]],
    url: str,
    x: int,
    y: int,
    module_width: int = 2,
    module_height: int = 1,
) -> None:
    matrix = qr_matrix_for_url(url)
    dark = profile.background_index
    light = nearest_palette_index(palette, (230, 255, 248))
    for my, line in enumerate(matrix):
        for mx, is_dark in enumerate(line):
            color = dark if is_dark else light
            x0 = x + mx * module_width
            y0 = y + my * module_height
            for py in range(module_height):
                yy = y0 + py
                if yy < 0 or yy >= profile.height:
                    continue
                row = rows[yy]
                for px in range(module_width):
                    xx = x0 + px
                    if 0 <= xx < profile.width:
                        row[xx] = color


def write_title_asset_previews(
    data: bytes,
    profile: GapBitmapProfile,
    out_raw: str | Path | None,
    out_display: str | Path | None,
    out_crop: str | Path | None = None,
) -> None:
    _, asset = get_asset(data, profile.asset_index)
    rows = decode_gap_bitmap(asset, profile)
    palette = read_palette(asset, profile)
    raw = render_rows(rows, profile, 1, palette)
    if out_raw:
        path = Path(out_raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw.save(path)
    display = render_display_aspect(raw, y_offset=8, visible_height=240, vertical_scale=2)
    if out_display:
        path = Path(out_display)
        path.parent.mkdir(parents=True, exist_ok=True)
        display.save(path)
    if out_crop:
        path = Path(out_crop)
        path.parent.mkdir(parents=True, exist_ok=True)
        display.crop((0, (178 + 8) * 2, profile.width, (224 + 8) * 2)).save(path)


def read_img(path: str | Path) -> bytearray:
    return bytearray(Path(path).read_bytes())


def parse_toc(data: bytes) -> list[AssetEntry]:
    """The offset table, however long this file's happens to be.

    Langrisser V pads the table to a 0x800 sector; Langrisser I & II do not —
    `LANG1.IMG.DAT` starts its first asset at 0x568. The table ends where the
    first asset begins either way, so read that from the first entry instead
    of assuming a size.
    """
    if len(data) < 8:
        raise ValueError("IMG.DAT is too small to hold an offset table")
    toc_size = struct.unpack_from("<I", data, 0)[0]
    if not 8 <= toc_size <= min(len(data), TOC_SIZE):
        raise ValueError(f"first asset offset 0x{toc_size:x} is not a table size")

    offsets: list[int] = []
    for pos in range(0, toc_size, 4):
        value = struct.unpack_from("<I", data, pos)[0]
        if value == 0 and pos > 0:
            break
        offsets.append(value)

    if len(offsets) < 2:
        raise ValueError("IMG.DAT TOC has fewer than two offsets")
    if offsets != sorted(offsets):
        raise ValueError("IMG.DAT TOC offsets are not sorted")
    if offsets[-1] > len(data):
        raise ValueError("IMG.DAT terminal offset is past end of file")
    if offsets[-1] < len(data):
        offsets.append(len(data))

    return [AssetEntry(i, offsets[i], offsets[i + 1]) for i in range(len(offsets) - 1)]


# --- Langrisser I & II bit-coded LZ payloads --------------------------------
# Every /LANG?/IMG.DAT asset is a bitstream, read MSB first, decoded by the
# routine at 0x80011ba4 in LANG1.EXE. The alphabet is a fixed prefix code:
#
#   1 1                literal, next 8 bits
#   1 0 0              literal, next 4 bits (values 0x00..0x0f)
#   1 0 1 0 0          literal 0x10        1 0 1 0 1  literal 0x30
#   1 0 1 1 0          literal 0x80        1 0 1 1 1  literal 0xff
#   0 0                copy 1 byte, 4-bit distance
#   0 1 0              copy 2 bytes, 4-bit distance
#   0 1 1 0            copy 3 bytes, 4-bit distance
#   0 1 1 1 0          copy 2 bytes, 8-bit distance
#   0 1 1 1 1 0        copy 3 bytes, 8-bit distance
#   0 1 1 1 1 1 0      copy 4 bytes, 8-bit distance
#   0 1 1 1 1 1 1 0    copy (4-bit + 5) bytes, 8-bit distance
#   0 1 1 1 1 1 1 1    end of stream
#
# A distance of zero means the maximum (0x10 for the 4-bit form, 0x100 for the
# 8-bit one); copies run forward one byte at a time, so they may overlap.
LZ_SHORT_DIST_MAX = 0x10
LZ_LONG_DIST_MAX = 0x100
LZ_SHORT_LITERALS = (0x10, 0x30, 0x80, 0xFF)


class _BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.buf = 0
        self.used = 8

    def _next_byte(self) -> int:
        if self.pos >= len(self.data):
            raise ValueError("IMG.DAT bitstream ran out before its end marker")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def bit(self) -> int:
        if self.used == 8:
            self.buf = self._next_byte()
            self.used = 0
        self.used += 1
        value = (self.buf >> 7) & 1
        self.buf = (self.buf << 1) & 0xFF
        return value

    def bits(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value


def lz_decompress(payload: bytes) -> bytes:
    """Expand one Langrisser I & II IMG.DAT asset."""
    reader = _BitReader(payload)
    out = bytearray()
    while True:
        if reader.bit():
            if reader.bit():
                out.append(reader.bits(8))
            elif not reader.bit():
                out.append(reader.bits(4))
            elif not reader.bit():
                out.append(LZ_SHORT_LITERALS[reader.bit()])
            else:
                out.append(LZ_SHORT_LITERALS[2 + reader.bit()])
            continue

        if not reader.bit():
            length, dist = 1, reader.bits(4) or LZ_SHORT_DIST_MAX
        elif not reader.bit():
            length, dist = 2, reader.bits(4) or LZ_SHORT_DIST_MAX
        elif not reader.bit():
            length, dist = 3, reader.bits(4) or LZ_SHORT_DIST_MAX
        elif not reader.bit():
            length, dist = 2, reader.bits(8) or LZ_LONG_DIST_MAX
        elif not reader.bit():
            length, dist = 3, reader.bits(8) or LZ_LONG_DIST_MAX
        elif not reader.bit():
            length, dist = 4, reader.bits(8) or LZ_LONG_DIST_MAX
        elif not reader.bit():
            length = reader.bits(4) + 5
            dist = reader.bits(8) or LZ_LONG_DIST_MAX
        else:
            return bytes(out)

        src = len(out) - dist
        for _ in range(length):
            out.append(out[src] if src >= 0 else 0)
            src += 1


class _BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.count = 0

    def put(self, value: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            self.acc = ((self.acc << 1) | ((value >> shift) & 1)) & 0xFF
            self.count += 1
            if self.count == 8:
                self.out.append(self.acc)
                self.acc = self.count = 0

    def finish(self) -> bytes:
        if self.count:
            self.out.append((self.acc << (8 - self.count)) & 0xFF)
        return bytes(self.out)


# (length, distance window, code, code width) for every copy form, cheapest first
_LZ_COPIES = (
    (1, LZ_SHORT_DIST_MAX, 0b00, 2),
    (2, LZ_SHORT_DIST_MAX, 0b010, 3),
    (3, LZ_SHORT_DIST_MAX, 0b0110, 4),
    (2, LZ_LONG_DIST_MAX, 0b01110, 5),
    (3, LZ_LONG_DIST_MAX, 0b011110, 6),
    (4, LZ_LONG_DIST_MAX, 0b0111110, 7),
)
_LZ_RUN_CODE, _LZ_RUN_WIDTH = 0b01111110, 8
_LZ_END_CODE, _LZ_END_WIDTH = 0b01111111, 8
_LZ_RUN_MIN, _LZ_RUN_MAX = 5, 20


def _lz_literal_width(byte: int) -> int:
    if byte <= 0x0F:
        return 3 + 4
    if byte in LZ_SHORT_LITERALS:
        return 5
    return 2 + 8


def _lz_nearest_matches(data: bytes, at: int, cap: int) -> dict[int, int]:
    """Shortest distance that reproduces each length 1..cap at this position."""
    nearest: dict[int, int] = {}
    for dist in range(1, min(LZ_LONG_DIST_MAX, at) + 1):
        src = at - dist
        length = 0
        while length < cap and data[src + length] == data[at + length]:
            length += 1
        for value in range(1, length + 1):
            nearest.setdefault(value, dist)
        if len(nearest) == cap:
            break
    return nearest


def lz_compress(data: bytes) -> bytes:
    """Pack bytes into the bitstream `lz_decompress` reads, parsing optimally.

    Costs are exact bit widths, so the shortest-path choice over them is the
    shortest stream this code can express.
    """
    size = len(data)
    cost = [0] * (size + 1)
    cost[size] = _LZ_END_WIDTH
    choice: list[tuple] = [("literal", 1, 0)] * (size + 1)
    for at in range(size - 1, -1, -1):
        best = _lz_literal_width(data[at]) + cost[at + 1]
        pick = ("literal", 1, 0)
        cap = min(_LZ_RUN_MAX, size - at)
        nearest = _lz_nearest_matches(data, at, cap)
        for length, window, _code, width in _LZ_COPIES:
            dist = nearest.get(length)
            if dist is None or dist > window or at + length > size:
                continue
            total = width + (4 if window == LZ_SHORT_DIST_MAX else 8) + cost[at + length]
            if total < best:
                best, pick = total, ("copy", length, dist)
        for length in range(_LZ_RUN_MIN, cap + 1):
            dist = nearest.get(length)
            if dist is None:
                break
            total = _LZ_RUN_WIDTH + 4 + 8 + cost[at + length]
            if total < best:
                best, pick = total, ("run", length, dist)
        cost[at], choice[at] = best, pick

    writer = _BitWriter()
    at = 0
    while at < size:
        kind, length, dist = choice[at]
        if kind == "literal":
            byte = data[at]
            if byte <= 0x0F:
                writer.put(0b100, 3)
                writer.put(byte, 4)
            elif byte in LZ_SHORT_LITERALS:
                writer.put(0b10100 | LZ_SHORT_LITERALS.index(byte), 5)
            else:
                writer.put(0b11, 2)
                writer.put(byte, 8)
        elif kind == "copy":
            for clen, window, code, width in _LZ_COPIES:
                if clen == length and dist <= window:
                    writer.put(code, width)
                    writer.put(dist & (0xF if window == LZ_SHORT_DIST_MAX else 0xFF),
                               4 if window == LZ_SHORT_DIST_MAX else 8)
                    break
        else:
            writer.put(_LZ_RUN_CODE, _LZ_RUN_WIDTH)
            writer.put(length - _LZ_RUN_MIN, 4)
            writer.put(dist & 0xFF, 8)
        at += length
    writer.put(_LZ_END_CODE, _LZ_END_WIDTH)
    return writer.finish()


LZ_BITMAP_KIND = 0
LZ_CLUT_KIND = 1
LZ_CLUT_ENTRIES = 16


def lz_bitmap(expanded: bytes):
    """`(width, height, 4bpp rows, palette)` for an expanded asset.

    Both kinds are 4bpp bitmaps sized in eight-pixel blocks. Kind 0 is
    `u16 kind, u16 unused, u16 width/8, u16 height/8` and takes its colours
    from a CLUT uploaded elsewhere; kind 1 carries its own sixteen-entry
    RGB555 CLUT between the kind word and the size words.
    """
    if len(expanded) < 8:
        return None
    kind = struct.unpack_from("<H", expanded, 0)[0]
    if kind == LZ_BITMAP_KIND:
        head, palette = 8, None
    elif kind == LZ_CLUT_KIND:
        head = 4 + 2 * LZ_CLUT_ENTRIES + 4
        if len(expanded) < head:
            return None
        palette = [
            rgb555_to_rgb888(word)
            for word in struct.unpack_from(f"<{LZ_CLUT_ENTRIES}H", expanded, 4)
        ]
    else:
        return None
    wide, high = struct.unpack_from("<2H", expanded, head - 4)
    width, height = wide * 8, high * 8
    if width <= 0 or height <= 0 or len(expanded) - head < width * height // 2:
        return None
    return width, height, expanded[head : head + width * height // 2], palette


LZ_FALLBACK_PALETTE = [(24, 24, 32)] + [
    (17 * i, min(255, 40 + 14 * i), min(255, 96 + 10 * i)) for i in range(1, 16)
]


def lz_bitmap_image(expanded: bytes, palette: list[tuple[int, int, int]] | None = None):
    """An expanded asset as a paletted image whose pixel values are its indices.

    Kind 0 has no CLUT of its own, so it is shown with a readable ramp; either
    way the stored value is the 4bpp index, which is what an edited PNG has to
    hand back.
    """
    decoded = lz_bitmap(expanded)
    if decoded is None:
        return None
    width, height, pixels, own_palette = decoded
    palette = palette or own_palette or LZ_FALLBACK_PALETTE
    img = Image.new("P", (width, height))
    flat: list[int] = []
    for value in palette:
        flat.extend(value)
    img.putpalette(flat + [0] * (768 - len(flat)))
    indices = bytearray(width * height)
    for i, byte in enumerate(pixels):
        indices[i * 2] = byte & 0xF
        indices[i * 2 + 1] = byte >> 4
    img.frombytes(bytes(indices))
    return img


def lz_bitmap_pixels(img: Image.Image, width: int, height: int) -> bytes:
    """Pack a paletted image back into 4bpp rows, checking it still fits."""
    if img.mode != "P":
        raise ValueError(f"asset override must be a paletted PNG, got mode {img.mode}")
    if (img.width, img.height) != (width, height):
        raise ValueError(
            f"asset override is {img.width}x{img.height}, expected {width}x{height}")
    indices = img.tobytes()
    over = sorted({value for value in indices if value > 0xF})
    if over:
        raise ValueError(f"asset override uses palette indices above 15: {over}")
    packed = bytearray(len(indices) // 2)
    for i in range(len(packed)):
        packed[i] = indices[i * 2] | (indices[i * 2 + 1] << 4)
    return bytes(packed)


def lz_replace_pixels(expanded: bytes, pixels: bytes) -> bytes:
    """Put new 4bpp rows into an expanded asset, keeping its header and CLUT."""
    decoded = lz_bitmap(expanded)
    if decoded is None:
        raise ValueError("asset is not a 4bpp bitmap")
    _, _, old, _ = decoded
    if len(pixels) != len(old):
        raise ValueError(f"pixel block is {len(pixels)} bytes, expected {len(old)}")
    head = len(expanded) - len(old)
    return expanded[:head] + pixels


def get_asset(data: bytes, index: int) -> tuple[AssetEntry, bytes]:
    entries = parse_toc(data)
    if index < 0 or index >= len(entries):
        raise IndexError(f"asset index {index} out of range 0..{len(entries)-1}")
    ent = entries[index]
    return ent, bytes(data[ent.offset : ent.end])


def rebuild_img(data: bytes, payloads: dict[int, bytes]) -> bytes:
    """Rewrite the archive with some assets replaced, re-laying the offset table.

    Assets no longer have to keep their original length: the table is rebuilt
    around them. The file must still fit its ISO extent, so the caller checks
    the result against the original length.
    """
    entries = parse_toc(data)
    unknown = sorted(set(payloads) - {ent.index for ent in entries})
    if unknown:
        raise IndexError(f"no such asset index: {unknown}")

    table_size = entries[0].offset
    slots = table_size // 4
    out = bytearray(table_size)
    offsets = []
    for ent in entries:
        payload = payloads.get(ent.index, bytes(data[ent.offset : ent.end]))
        offsets.append(len(out))
        out += payload
        if len(out) % 4:
            out += bytes(4 - len(out) % 4)
    offsets.append(len(out))

    if len(offsets) > slots:
        raise ValueError(
            f"offset table holds {slots} entries, rebuild needs {len(offsets)}")
    for slot, offset in enumerate(offsets):
        struct.pack_into("<I", out, slot * 4, offset)
    return bytes(out)


def rebuild_img_within(data: bytes, payloads: dict[int, bytes],
                       limit: int | None = None) -> bytes:
    """Rebuild the archive so it still fits, repacking everything if it must.

    Redrawing can cost room — Cyrillic in cells that used to be blank is real
    data where there was almost none — and the file may not outgrow its ISO
    extent. `lz_compress` parses optimally where the shipped streams do not,
    so repacking the assets nobody edited buys about six per cent back.
    """
    limit = len(data) if limit is None else limit
    out = rebuild_img(data, payloads)
    if len(out) <= limit:
        return out
    dense = dict(payloads)
    for ent in parse_toc(data):
        payload = bytes(data[ent.offset : ent.end])
        if ent.index in dense or not payload:
            continue
        dense[ent.index] = lz_compress(lz_decompress(payload))
    return rebuild_img(data, dense)


def replace_asset(data: bytearray, index: int, payload: bytes) -> None:
    ent, _ = get_asset(data, index)
    if len(payload) != ent.size:
        raise ValueError(f"asset {index} must stay same-size: {len(payload)} != {ent.size}")
    data[ent.offset : ent.end] = payload


def asset_header_summary(asset: bytes) -> dict[str, object]:
    head = asset[:0x40]
    u16 = list(struct.unpack("<" + "H" * (len(head) // 2), head))
    u32 = list(struct.unpack("<" + "I" * (len(head) // 4), head))
    return {
        "header_hex": head.hex(),
        "u16_first16": [f"0x{x:04x}" for x in u16[:16]],
        "u32_first8": [f"0x{x:08x}" for x in u32[:8]],
    }


def decode_gap_bitmap(asset: bytes, profile: GapBitmapProfile) -> list[bytearray]:
    rows: list[bytearray] = []
    for y in range(profile.height):
        off, gap_after_x, physical_len = profile.row_layout(y)
        if off + physical_len > len(asset):
            raise ValueError(
                f"{profile.name} row {y} exceeds asset size: "
                f"0x{off:x}+0x{physical_len:x} > 0x{len(asset):x}"
            )
        raw = asset[off : off + physical_len]
        if gap_after_x is None:
            row = bytearray(raw)
        else:
            row = bytearray(raw[:gap_after_x] + raw[gap_after_x + profile.gap_bytes :])
        if len(row) != profile.width:
            raise ValueError(f"{profile.name} decoded row {y} has width {len(row)}")
        rows.append(row)
    return rows


def encode_gap_bitmap(asset: bytes, rows: list[bytearray], profile: GapBitmapProfile) -> bytes:
    if len(rows) != profile.height:
        raise ValueError(f"expected {profile.height} rows, got {len(rows)}")
    out = bytearray(asset)
    for y, row in enumerate(rows):
        if len(row) != profile.width:
            raise ValueError(f"row {y} has width {len(row)}, expected {profile.width}")
        off, gap_after_x, physical_len = profile.row_layout(y)
        if off + physical_len > len(out):
            raise ValueError(f"{profile.name} row {y} exceeds asset size")
        if gap_after_x is None:
            out[off : off + profile.width] = row
        else:
            out[off : off + gap_after_x] = row[:gap_after_x]
            tail_len = profile.width - gap_after_x
            tail_off = off + gap_after_x + profile.gap_bytes
            out[tail_off : tail_off + tail_len] = row[gap_after_x:]
    return bytes(out)


def rgb555_to_rgb888(word: int) -> tuple[int, int, int]:
    r = word & 0x1F
    g = (word >> 5) & 0x1F
    b = (word >> 10) & 0x1F
    return ((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2))


def read_palette(asset: bytes, profile: GapBitmapProfile) -> list[tuple[int, int, int]] | None:
    if profile.palette_rel_offset is None:
        return None
    size = profile.palette_entries * 2
    start = profile.palette_rel_offset
    end = start + size
    if end > len(asset):
        raise ValueError(
            f"{profile.name} palette exceeds asset size: "
            f"0x{start:x}+0x{size:x} > 0x{len(asset):x}"
        )
    words = struct.unpack_from("<" + "H" * profile.palette_entries, asset, start)
    return [rgb555_to_rgb888(word) for word in words]


# --- Type-8 scanline-packet images (see docs/L45_IMG_DAT_FORMAT.md) ---
# Decoded type-8 images are streams of 2048-byte packets: a 0x20 header
# (magic 0x0160) followed by 2016 bytes of 8bpp pixel data.
# width_px = u16[10] * 2. Consecutive same-width type-8 packets form one image;
# CLUTs and unsupported packet types live in the gaps between decoded images.
PACKET_BYTES = VRAM_SCANLINE_BYTES        # 2048
PACKET_HEADER_BYTES = SCANLINE_GAP_BYTES  # 0x20
PACKET_MAGIC = 0x0160


def _u16(buf: bytes, off: int) -> int:
    return buf[off] | (buf[off + 1] << 8)


def is_packet_header(asset: bytes, off: int) -> bool:
    return (
        off + PACKET_BYTES <= len(asset)
        and _u16(asset, off) == PACKET_MAGIC
        and _u16(asset, off + 0x06) == 8
        and _u16(asset, off + 0x1A) == PACKET_BYTES
    )


PACKETS_PER_BLOCK = 8  # 0x4000 block / 0x800 packet


def image_groups(asset: bytes) -> list[tuple[int, int, int, int]]:
    """(start_offset, packet_count, width_px, block_rows) for each image."""
    groups: list[list[int]] = []
    prev_end: int | None = None
    for off in range(0, len(asset) - PACKET_HEADER_BYTES, PACKET_BYTES):
        if not is_packet_header(asset, off):
            prev_end = None
            continue
        width = _u16(asset, off + 0x14) * 2
        block_rows = _u16(asset, off + 0x16)
        if groups and prev_end == off and groups[-1][2] == width:
            groups[-1][1] += 1
        else:
            groups.append([off, 1, width, block_rows])
        prev_end = off + PACKET_BYTES
    return [tuple(g) for g in groups]


def decode_image(asset: bytes, start: int, packet_count: int, width: int,
                 block_rows: int) -> list[bytearray]:
    """Reshape an image to `width`, dropping each 0x4000 block's trailing pad.

    A 0x4000 block is PACKETS_PER_BLOCK packets and stores exactly
    block_rows * width pixel bytes; the remainder of the bodies is padding.
    """
    pixels_per_block = block_rows * width
    pixels = bytearray()
    p = 0
    while p < packet_count:
        body = bytearray()
        for k in range(PACKETS_PER_BLOCK):
            if p + k >= packet_count:
                break
            off = start + (p + k) * PACKET_BYTES
            body += asset[off + PACKET_HEADER_BYTES : off + PACKET_BYTES]
        pixels += body[:pixels_per_block]
        p += PACKETS_PER_BLOCK
    height = len(pixels) // width
    return [bytearray(pixels[y * width : (y + 1) * width]) for y in range(height)]


def encode_image(asset: bytes, start: int, packet_count: int, width: int,
                 block_rows: int, rows: list[bytearray]) -> bytes:
    """Inverse of decode_image: write edited index rows back into the packets.

    Packet headers and each block's trailing padding are left untouched, so an
    unedited round-trip is byte-identical.
    """
    pixels_per_block = block_rows * width
    flat = bytearray()
    for row in rows:
        if len(row) != width:
            raise ValueError(f"row width {len(row)} != image width {width}")
        flat += row
    out = bytearray(asset)
    p = 0
    fi = 0
    while p < packet_count:
        block = flat[fi : fi + pixels_per_block]
        fi += len(block)
        bp = 0
        for k in range(PACKETS_PER_BLOCK):
            if p + k >= packet_count or bp >= len(block):
                break
            off = start + (p + k) * PACKET_BYTES
            n = min(PACKET_BYTES - PACKET_HEADER_BYTES, len(block) - bp)
            out[off + PACKET_HEADER_BYTES : off + PACKET_HEADER_BYTES + n] = block[bp : bp + n]
            bp += n
        p += PACKETS_PER_BLOCK
    return bytes(out)


CLUT_BYTES = 256 * 2


def read_clut_at(asset: bytes, offset: int) -> list[tuple[int, int, int]] | None:
    if offset < 0 or offset + CLUT_BYTES > len(asset):
        return None
    words = struct.unpack_from("<256H", asset, offset)
    return [rgb555_to_rgb888(w) for w in words]


def clut_palettes(asset: bytes) -> list[list[tuple[int, int, int]]]:
    """Every 256-colour palette stored in the asset's CLUT blocks.

    A CLUT block is a 0x0160 header with width word 256 and a non-image type
    (1/2/3); u16[11] holds how many 256-entry palettes follow the header. An
    image with several palettes is animated (e.g. the title flame, the poem's
    line highlight), so they are all returned and the caller picks one.
    """
    palettes: list[list[tuple[int, int, int]]] = []
    off = 0
    while off + PACKET_HEADER_BYTES <= len(asset):
        if (_u16(asset, off) == PACKET_MAGIC and _u16(asset, off + 0x14) == 256
                and _u16(asset, off + 0x06) in (1, 2, 3)):
            count = _u16(asset, off + 0x16) or 1
            base = off + PACKET_HEADER_BYTES
            for k in range(count):
                pal = read_clut_at(asset, base + k * CLUT_BYTES)
                if pal is not None:
                    palettes.append(pal)
            off = base + count * CLUT_BYTES
            continue
        off += 0x10
    return palettes


def pick_palette(pixels: bytes, palettes: list[list[tuple[int, int, int]]]):
    """Pick the most colourful palette variant for an image's pixel indices."""
    if not palettes:
        return None
    counts = Counter(pixels)

    def richness(pal: list[tuple[int, int, int]]) -> int:
        total = 0
        for idx, n in counts.items():
            r, g, b = pal[idx]
            total += (max(r, g, b) - min(r, g, b) + max(r, g, b)) * n
        return total

    return max(palettes, key=richness)


def find_clut(asset: bytes, lo: int, hi: int) -> list[tuple[int, int, int]] | None:
    """Locate a 256-entry RGB555 CLUT in [lo, hi) by colour smoothness."""
    best: tuple[int, int] | None = None
    for off in range(lo, max(lo, hi - CLUT_BYTES) + 1, 16):
        words = struct.unpack_from("<256H", asset, off)
        if len(set(words)) < 64:
            continue
        smooth = sum(abs((words[i] & 31) - (words[i + 1] & 31)) for i in range(255))
        if best is None or smooth < best[0]:
            best = (smooth, off)
    if best is None:
        return None
    words = struct.unpack_from("<256H", asset, best[1])
    return [rgb555_to_rgb888(w) for w in words]


def render_rows(rows: list[bytearray], profile: GapBitmapProfile, scale: int,
                palette: list[tuple[int, int, int]] | None = None) -> Image.Image:
    img = Image.new("RGB", (profile.width, len(rows)), (0, 0, 0))
    px = img.load()
    for y, row in enumerate(rows):
        for x, value in enumerate(row):
            if palette is not None:
                color = palette[value]
            else:
                if value == profile.background_index:
                    color = (0, 0, 0)
                elif value == profile.ink_index:
                    color = (136, 192, 200)
                else:
                    color = (210, 210, 210)
            px[x, y] = color
    if scale != 1:
        img = img.resize((profile.width * scale, len(rows) * scale), Image.Resampling.NEAREST)
    return img


def render_display_aspect(raw: Image.Image, y_offset: int, visible_height: int,
                          vertical_scale: int) -> Image.Image:
    if raw.mode != "RGB":
        raw = raw.convert("RGB")
    screen = Image.new("RGB", (raw.width, visible_height), (0, 0, 0))
    screen.paste(raw, (0, y_offset))
    if vertical_scale != 1:
        screen = screen.resize((screen.width, screen.height * vertical_scale), Image.Resampling.NEAREST)
    return screen


def load_font(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    candidates: Iterable[str]
    if path:
        candidates = [path]
    else:
        candidates = [
            "data/fonts/PixelMplus12-Regular.ttf",
            "data/fonts/PixelMplus10-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    for candidate in candidates:
        p = Path(candidate)
        if p.exists():
            return ImageFont.truetype(str(p), size=size)
    raise FileNotFoundError("no usable TTF font found; pass --font")


def text_mask(text: str, font: ImageFont.FreeTypeFont) -> Image.Image:
    scratch = Image.new("1", (2048, 128), 0)
    draw = ImageDraw.Draw(scratch)
    draw.fontmode = "1"
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    if width <= 0 or height <= 0:
        raise ValueError(f"empty text mask for {text!r}")
    out = Image.new("1", (width, height), 0)
    out_draw = ImageDraw.Draw(out)
    out_draw.fontmode = "1"
    out_draw.text((-bbox[0], -bbox[1]), text, font=font, fill=1)
    return out


def draw_text(rows: list[bytearray], profile: GapBitmapProfile, text: str, global_y: int,
              font: ImageFont.FreeTypeFont, ink_index: int) -> None:
    mask = text_mask(text, font)
    x0 = (profile.width - mask.width) // 2
    y0 = profile.logical_y_from_global(global_y)
    if x0 < 0:
        raise ValueError(f"text is too wide for profile {profile.name}: {text!r}")
    if y0 < 0 or y0 + mask.height > profile.height:
        raise ValueError(f"text row {global_y} does not fit profile {profile.name}")
    pix = mask.load()
    for y in range(mask.height):
        row = rows[y0 + y]
        for x in range(mask.width):
            if pix[x, y]:
                row[x0 + x] = ink_index


def cmd_list(args: argparse.Namespace) -> None:
    data = read_img(args.imgdat)
    print("idx  offset    end       size     header_u16_first16")
    for ent in parse_toc(data):
        asset = data[ent.offset : ent.end]
        u16 = struct.unpack("<" + "H" * 16, asset[:32])
        fields = " ".join(f"{x:04x}" for x in u16)
        print(f"{ent.index:3d}  0x{ent.offset:06x}  0x{ent.end:06x}  {ent.size:7d}  {fields}")


def cmd_inspect(args: argparse.Namespace) -> None:
    data = read_img(args.imgdat)
    if args.asset is None:
        payload = []
        for ent in parse_toc(data):
            asset = data[ent.offset : ent.end]
            item = {"index": ent.index, "offset": ent.offset, "end": ent.end, "size": ent.size}
            item.update(asset_header_summary(asset))
            payload.append(item)
    else:
        ent, asset = get_asset(data, args.asset)
        payload = {"index": ent.index, "offset": ent.offset, "end": ent.end, "size": ent.size}
        payload.update(asset_header_summary(asset))
    print(json.dumps(payload, indent=2))


def cmd_extract(args: argparse.Namespace) -> None:
    data = read_img(args.imgdat)
    _, asset = get_asset(data, args.asset)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(asset)
    print(f"extracted asset {args.asset} -> {out} ({len(asset)} bytes)")


def cmd_replace(args: argparse.Namespace) -> None:
    data = read_img(args.imgdat)
    payload = Path(args.asset_file).read_bytes()
    replace_asset(data, args.asset, payload)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"replaced asset {args.asset} -> {out}")


def profile_from_args(args: argparse.Namespace) -> GapBitmapProfile:
    try:
        return PROFILES[args.profile]
    except KeyError as exc:
        known = ", ".join(sorted(PROFILES))
        raise SystemExit(f"unknown profile {args.profile!r}; known: {known}") from exc


def cmd_decode_gap_bitmap(args: argparse.Namespace) -> None:
    profile = profile_from_args(args)
    data = read_img(args.imgdat)
    _, asset = get_asset(data, profile.asset_index)
    rows = decode_gap_bitmap(asset, profile)
    palette = None if args.index_debug else read_palette(asset, profile)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    render_rows(rows, profile, args.scale, palette).save(out)
    print(f"decoded {profile.name} -> {out}")


def cmd_dump_all(args: argparse.Namespace) -> None:
    data = read_img(args.imgdat)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for ent in parse_toc(data):
        asset = bytes(data[ent.offset : ent.end])
        palettes = clut_palettes(asset)
        for gi, (start, packets, width, block_rows) in enumerate(image_groups(asset)):
            if width <= 0 or block_rows <= 0:
                continue
            rows = decode_image(asset, start, packets, width, block_rows)
            height = len(rows)
            palette = pick_palette(b"".join(bytes(r) for r in rows), palettes)
            img = Image.new("RGB", (width, height), (0, 0, 0))
            px = img.load()
            for y, row in enumerate(rows):
                for x, value in enumerate(row):
                    px[x, y] = palette[value] if palette else (value, value, value)
            if args.scale != 1:
                img = img.resize((width * args.scale, height * args.scale), Image.Resampling.NEAREST)
            name = out_dir / f"asset{ent.index:02d}_img{gi}_{width}x{height}.png"
            img.save(name)
            count += 1
    print(f"dumped {count} images to {out_dir}")


def cmd_dump_lz(args: argparse.Namespace) -> None:
    """Expand every Langrisser I & II asset and write one PNG per bitmap."""
    data = read_img(args.imgdat)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    thumbs: list[tuple[int, Image.Image]] = []
    other = 0
    for ent in parse_toc(data):
        payload = bytes(data[ent.offset : ent.end])
        if not payload:
            continue
        img = lz_bitmap_image(lz_decompress(payload))
        if img is None:
            other += 1
            continue
        img.save(out_dir / f"{ent.index:03d}_{img.width}x{img.height}.png")
        thumbs.append((ent.index, img))

    cell, per_row, per_sheet = args.cell, args.columns, args.columns * args.rows
    for page in range(0, len(thumbs), per_sheet):
        chunk = thumbs[page : page + per_sheet]
        rows = (len(chunk) + per_row - 1) // per_row
        sheet = Image.new("RGB", (per_row * cell, rows * cell), (12, 12, 16))
        draw = ImageDraw.Draw(sheet)
        for k, (index, img) in enumerate(chunk):
            thumb = img.copy()
            thumb.thumbnail((cell - 8, cell - 20), Image.Resampling.NEAREST)
            x, y = (k % per_row) * cell, (k // per_row) * cell
            sheet.paste(thumb, (x + 4, y + 16))
            draw.text((x + 4, y + 3), f"#{index}", fill=(210, 210, 210))
        sheet.save(out_dir / f"sheet_{page // per_sheet:02d}.png")
    print(f"{len(thumbs)} bitmaps and {other} non-bitmap assets -> {out_dir}")


def cmd_title_credits_preview(args: argparse.Namespace) -> None:
    data = read_img(args.imgdat)
    commit_hash = args.commit_hash or git_short_hash()
    lines = load_title_credit_lines(
        args.credits_json, args.version, commit_hash
    )

    for profile_name in TITLE_PATCH_PROFILE_NAMES:
        profile = PROFILES[profile_name]
        _, asset = get_asset(data, profile.asset_index)
        rows = decode_gap_bitmap(asset, profile)
        palette = read_palette(asset, profile)
        if palette is None:
            raise ValueError(f"{profile.name} has no palette configured")

        draw_title_credits(rows, profile, palette, lines, args.font)
        if not args.no_qr:
            draw_title_qr(
                rows,
                profile,
                palette,
                args.qr_url,
                args.qr_x,
                args.qr_y,
                args.qr_module_width,
                args.qr_module_height,
            )

        edited_asset = encode_gap_bitmap(asset, rows, profile)
        replace_asset(data, profile.asset_index, edited_asset)

    out_imgdat = Path(args.out_imgdat)
    out_imgdat.parent.mkdir(parents=True, exist_ok=True)
    out_imgdat.write_bytes(data)
    write_title_asset_previews(data, PROFILES["title10"], args.out_raw_preview, args.out_display, args.out_crop)
    if args.out_display:
        display_path = Path(args.out_display)
        title11_display = display_path.with_name(f"{display_path.stem}_title11{display_path.suffix}")
    else:
        title11_display = None
    if args.out_raw_preview:
        raw_path = Path(args.out_raw_preview)
        title11_raw = raw_path.with_name(f"{raw_path.stem}_title11{raw_path.suffix}")
    else:
        title11_raw = None
    if args.out_crop:
        crop_path = Path(args.out_crop)
        title11_crop = crop_path.with_name(f"{crop_path.stem}_title11{crop_path.suffix}")
    else:
        title11_crop = None
    write_title_asset_previews(data, PROFILES["title11"], title11_raw, title11_display, title11_crop)

    print(f"edited IMG.DAT -> {out_imgdat}")
    if args.out_raw_preview:
        print(f"raw preview -> {args.out_raw_preview}")
        print(f"title11 raw preview -> {title11_raw}")
    if args.out_display:
        print(f"display preview -> {args.out_display}")
        print(f"title11 display preview -> {title11_display}")
    if args.out_crop:
        print(f"display crop -> {args.out_crop}")
        print(f"title11 display crop -> {title11_crop}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="list IMG.DAT assets")
    p.add_argument("imgdat")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("inspect", help="dump asset header fields as JSON")
    p.add_argument("imgdat")
    p.add_argument("asset", nargs="?", type=int)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("extract", help="extract one raw asset")
    p.add_argument("imgdat")
    p.add_argument("asset", type=int)
    p.add_argument("out")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("replace", help="replace one raw asset; size must match")
    p.add_argument("imgdat")
    p.add_argument("asset", type=int)
    p.add_argument("asset_file")
    p.add_argument("out")
    p.set_defaults(func=cmd_replace)

    p = sub.add_parser("dump-all", help="decode every image in every asset to PNG (palette auto-detected)")
    p.add_argument("imgdat")
    p.add_argument("--out-dir", default="work/img_dump")
    p.add_argument("--scale", type=int, default=1)
    p.set_defaults(func=cmd_dump_all)

    p = sub.add_parser("dump-lz", help="expand Langrisser I & II assets to PNG plus contact sheets")
    p.add_argument("imgdat")
    p.add_argument("--out-dir", default="work/img_dump")
    p.add_argument("--cell", type=int, default=200)
    p.add_argument("--columns", type=int, default=6)
    p.add_argument("--rows", type=int, default=8)
    p.set_defaults(func=cmd_dump_lz)

    p = sub.add_parser("decode-gap-bitmap", help="decode a known gap-bitmap profile to PNG")
    p.add_argument("imgdat")
    p.add_argument("--profile", default="title10", choices=sorted(PROFILES))
    p.add_argument("--out", required=True)
    p.add_argument("--scale", type=int, default=1)
    p.add_argument(
        "--index-debug",
        action="store_true",
        help="render diagnostic index colors instead of the profile palette",
    )
    p.set_defaults(func=cmd_decode_gap_bitmap)

    def add_title_credit_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("imgdat")
        p.add_argument("--out-imgdat", default="work/title_credits/IMG.DAT")
        p.add_argument("--version", default=DEFAULT_PATCH_VERSION)
        p.add_argument("--commit-hash", help="default: git rev-parse --short=8 HEAD")
        p.add_argument("--font", help="default: data/fonts/LiberationSansNarrow-Bold.ttf")
        p.add_argument("--credits-json",
                       help="target-language title-credit line templates")
        p.add_argument("--qr-url", default=TITLE_QR_URL)
        p.add_argument("--no-qr", action="store_true")
        p.add_argument("--qr-x", type=int, default=550)
        p.add_argument("--qr-y", type=int, default=6)
        p.add_argument("--qr-module-width", type=int, default=2)
        p.add_argument("--qr-module-height", type=int, default=1)
        p.add_argument("--out-raw-preview")
        p.add_argument("--out-display")
        p.add_argument("--out-crop")
        p.set_defaults(func=cmd_title_credits_preview)

    p = sub.add_parser(
        "title-credits",
        help="write release title credits and QR into a same-size copy of IMG.DAT",
    )
    add_title_credit_args(p)

    p = sub.add_parser(
        "title-credits-preview",
        help="compatibility alias for title-credits",
    )
    add_title_credit_args(p)
    return ap


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
