#!/usr/bin/env python3
"""Translate the Now Loading plate (IMG.DAT asset 0, type-2 texture).

The plate is a 120x28 8bpp texture stored as two raw type-2 VRAM-upload
packets inside asset 0 (destination VRAM byte column 1664, row 456; found by
byte-matching a live VRAM dump against the disc files). The lettering is an
engraved stroke on a mottled metal face with an underline and corner rivets.
This erases only the inner face between the rivets, refills it with plate
texture sampled from clean rows, and redraws the target text with the
original stroke/bevel indices plus the underline.
"""
import argparse
import struct
import sys
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from langrisser.project import add_language_args, language_from_args
from langrisser import imgdat as imd

ASSET_INDEX = 0
WIDTH, HEIGHT = 120, 28
# Type-2 packet header signature: VRAM destination and size of the plate.
VRAM_X_WORDS, VRAM_Y, WIDTH_WORDS, ROWS = 0x340, 0x1C8, 0x3C, 0x1C
# Inner plate face between the corner rivets (x 12-14/112-113 top,
# 6-7/110-111 bottom) and the frame highlights (x 6..12 and 110..114). Only
# the original lettering rows are repainted; the underline (rows 18-19, full
# face width) and the tick shading above the letters stay original.
FACE_X0, FACE_X1 = 13, 110
TEXT_Y0 = 10                    # first lettering row to cover
UNDERLINE_Y = 18
UL_X0, UL_X1 = 9, 108           # original underline span
FACE_Y0 = 7                     # top of the paintable face (new glyph tops)
# Open face patch just left of the original letters; its rows 10..18 align
# one-to-one with the lettering band.
PATCH_X, PATCH_W, PATCH_H = 8, 5, 9
BLUR_RADIUS = 0.7
# The original g tail dents the bevel row below the underline shadow.
G_TAIL_X0, G_TAIL_X1, G_TAIL_Y = 101, 110, 20
# The tall ascenders (N, L, d, the i dot) poke above the lettering band
# into the tick-shaded rows; that strip is covered from the same rows'
# clean stretch right of the lettering so the tick pattern survives.
ASC_Y0, ASC_Y1 = 8, 11
ASC_X0, ASC_X1 = 13, 92
ASC_SRC_X, ASC_SRC_W = 94, 14
BASELINE = 17                   # original caps sit on row 17
CAP_TOP = 7                     # taller than the original 11..17: the Cyrillic
                                # line needs the full face to match the English span
# The original lettering spans x 14..106; the new text is letter-spaced out
# to the same width.
TARGET_X0, TARGET_X1 = 14, 107
FONT = "data/fonts/DejaVuSerif-Bold.ttf"
SUPERSAMPLE = 4
STROKE_LUM = 4                  # near-black engraved stroke core
CLUT_INDEX = 1                  # first grayscale CLUT of asset 0 (for classes/preview)


def luminance(color: tuple[int, int, int]) -> float:
    return (color[0] * 3 + color[1] * 6 + color[2]) / 10


def find_plate_packets(data: bytes, ent) -> list[list[int]]:
    """Adjacent packet pairs of the plate; asset 0 stores three copies."""
    offsets = []
    for off in range(ent.offset, ent.end, imd.PACKET_BYTES):
        t = struct.unpack_from("<16H", data, off)
        if (t[0] == imd.PACKET_MAGIC and t[3] == 2
                and t[8] == VRAM_X_WORDS and t[9] == VRAM_Y
                and t[10] == WIDTH_WORDS and t[11] == ROWS):
            offsets.append(off)
    pairs = [offsets[i : i + 2] for i in range(0, len(offsets), 2)]
    if not pairs or any(len(p) != 2 or p[1] != p[0] + imd.PACKET_BYTES for p in pairs):
        raise SystemExit(f"expected adjacent plate packet pairs, found {offsets}")
    return pairs


def read_plate(data: bytes, packs: list[int]) -> bytearray:
    body = bytearray()
    for off in packs:
        body += data[off + imd.PACKET_HEADER_BYTES : off + imd.PACKET_BYTES]
    return body[: WIDTH * HEIGHT]


def write_plate(data: bytearray, packs: list[int], pixels: bytes) -> None:
    pos = 0
    for off in packs:
        n = min(imd.PACKET_BYTES - imd.PACKET_HEADER_BYTES, len(pixels) - pos)
        data[off + imd.PACKET_HEADER_BYTES : off + imd.PACKET_HEADER_BYTES + n] = \
            pixels[pos : pos + n]
        pos += n


def render_mask(text: str, font_path: str, cap_top: int) -> Image.Image:
    """Antialiased plate-sized alpha of the lettering and the underline.

    The glyphs are rendered supersampled at the plate's cap band height,
    letter-spaced out so the line covers the same span as the original
    English lettering, and centered; the underline with its shadow row is
    part of the same mask so everything downsamples together. The width is
    never squeezed, which would tear the thin strokes.
    """
    ss = SUPERSAMPLE
    target_w = (TARGET_X1 - TARGET_X0) * ss
    probe = Image.new("L", (8, 8))
    d = ImageDraw.Draw(probe)
    for cap in range(BASELINE - cap_top + 1, 4, -1):
        font = None
        for cand in range(cap * ss // 2, cap * ss * 3):
            f = ImageFont.truetype(font_path, cand)
            bbox = d.textbbox((0, 0), "Н", font=f)
            if bbox[3] - bbox[1] >= cap * ss:
                font = f
                break
        if font is None:
            continue
        advances = [d.textlength(ch, font=font) for ch in text]
        natural = sum(advances)
        if natural > target_w:
            continue
        tracking = (target_w - natural) / max(1, len(text) - 1)
        big = Image.new("L", (WIDTH * ss, HEIGHT * ss), 0)
        bd = ImageDraw.Draw(big)
        bbox = bd.textbbox((0, 0), "Н", font=font)
        pen = (TARGET_X0 + TARGET_X1) * ss / 2 - target_w / 2
        for ch, advance in zip(text, advances):
            bd.text((pen, BASELINE * ss - bbox[3]), ch, fill=255, font=font)
            pen += advance + tracking
        bd.rectangle((UL_X0 * ss, UNDERLINE_Y * ss,
                      UL_X1 * ss - 1, (UNDERLINE_Y + 1) * ss - 1), fill=255)
        bd.rectangle((UL_X0 * ss, (UNDERLINE_Y + 1) * ss,
                      UL_X1 * ss - 1, (UNDERLINE_Y + 2) * ss - 1), fill=150)
        return big.resize((WIDTH, HEIGHT), Image.LANCZOS)
    raise SystemExit(f"cannot fit {text!r} on the plate with {font_path}")


def redraw_plate_pixels(pixels: bytes | bytearray, palette: list[tuple[int, int, int]],
                        text: str, font_path: str = FONT, cap_top: int = CAP_TOP,
                        erase_only: bool = False) -> bytearray:
    """Return a translated 120x28 Now Loading plate texture.

    Container-specific code (PS1 IMG.DAT packets, Saturn SYSTEM.DAT
    compression) feeds this routine the same indexed plate bytes and palette, so
    the visual edit remains byte-identical across platforms.
    """
    # Palette indices already used on the plate, by luminance: the engraved
    # stroke core, two antialias midtones and the bevel highlight.
    used = Counter(pixels[y * WIDTH + x]
                   for y in range(FACE_Y0, UNDERLINE_Y + 2)
                   for x in range(FACE_X0, FACE_X1))

    def nearest_used(target: float) -> int:
        return min(used, key=lambda i: (abs(luminance(palette[i]) - target),
                                        -used[i]))

    # Cover the original lettering and its underline with the plate's own
    # face: the open patch just left of the letters (PATCH_X..+PATCH_W,
    # rows 10..18) is aligned row-for-row with the lettering band, so
    # tiling it horizontally preserves the vertical shading with real
    # texture. A light Gaussian pass over the covered pixels then hides
    # the tile seams; the sharp text and underline are drawn after it.
    out = bytearray(pixels)
    covered = []
    for y in range(TEXT_Y0, UNDERLINE_Y + 2):
        sy = min(y, TEXT_Y0 + PATCH_H - 1)
        x0 = FACE_X0 if y < UNDERLINE_Y else UL_X0
        for x in range(x0, FACE_X1):
            sx = PATCH_X + (x - x0) % PATCH_W
            out[y * WIDTH + x] = pixels[sy * WIDTH + sx]
            covered.append((x, y))
    # The g tail also dented the bevel row below the underline shadow;
    # cover it with the same row's own pattern from a few pixels left.
    for x in range(G_TAIL_X0, G_TAIL_X1):
        out[G_TAIL_Y * WIDTH + x] = pixels[G_TAIL_Y * WIDTH + x - 9]
        covered.append((x, G_TAIL_Y))
    # Ascender tops in the tick rows, covered by the same rows' clean part.
    for y in range(ASC_Y0, ASC_Y1):
        for x in range(ASC_X0, ASC_X1):
            out[y * WIDTH + x] = pixels[y * WIDTH + ASC_SRC_X + (x - ASC_X0) % ASC_SRC_W]
            covered.append((x, y))
    rgb = Image.new("RGB", (WIDTH, HEIGHT))
    rgb.putdata([palette[v] for v in out])
    blurred = rgb.filter(ImageFilter.GaussianBlur(BLUR_RADIUS)).load()
    for x, y in covered:
        out[y * WIDTH + x] = nearest_used(luminance(blurred[x, y]))

    def paint(x: int, y: int, index: int) -> None:
        if FACE_X0 <= x < FACE_X1 and FACE_Y0 <= y <= UNDERLINE_Y + 1:
            out[y * WIDTH + x] = index

    if not erase_only:
        alpha = render_mask(text, font_path, cap_top).load()
        # Alpha-blend the near-black stroke over the actual plate pixel and
        # snap to the nearest plate index: the lettering antialiases against
        # the real background with every gray step the palette offers.
        for y in range(FACE_Y0, UNDERLINE_Y + 2):
            for x in range(FACE_X0, FACE_X1):
                value = alpha[x, y]
                if value < 12:
                    continue
                bg = luminance(palette[out[y * WIDTH + x]])
                target = bg + (STROKE_LUM - bg) * value / 255
                paint(x, y, nearest_used(target))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--imgdat", default="work/l5/extracted/IMG.DAT")
    ap.add_argument("--text", default=None)
    ap.add_argument("--out-imgdat", default="work/l5/build/IMG.DAT.now_loading")
    ap.add_argument("--out-preview", default=None)
    ap.add_argument("--font", default=FONT)
    ap.add_argument("--cap-top", type=int, default=CAP_TOP)
    ap.add_argument("--erase-only", action="store_true",
                    help="cover the original lettering but draw no new text")
    args = ap.parse_args()
    lang = language_from_args(args)
    text = args.text if args.text is not None else lang.now_loading
    if not text:
        raise SystemExit("no plate text: pass --text or set now_loading in the manifest")
    preview_path = (Path(args.out_preview) if args.out_preview
                    else lang.build_path("now_loading_{lang}_preview.png"))

    data = imd.read_img(args.imgdat)
    ent, asset = imd.get_asset(data, ASSET_INDEX)
    palette = imd.clut_palettes(asset)[CLUT_INDEX]
    pairs = find_plate_packets(data, ent)
    copies = [read_plate(data, pair) for pair in pairs]
    if any(c != copies[0] for c in copies[1:]):
        raise SystemExit("plate copies differ; refusing to patch them uniformly")
    pixels = copies[0]
    out = redraw_plate_pixels(pixels, palette, text, args.font, args.cap_top,
                              erase_only=args.erase_only)
    for pair in pairs:
        write_plate(data, pair, bytes(out))
    out_path = Path(args.out_imgdat)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)

    preview = Image.new("RGB", (WIDTH * 4, HEIGHT * 8), (0, 0, 0))
    for pi, pix in enumerate((pixels, out)):
        frame = Image.new("RGB", (WIDTH, HEIGHT))
        frame.putdata([palette[v] for v in pix])
        preview.paste(frame.resize((WIDTH * 4, HEIGHT * 4), Image.NEAREST), (0, pi * HEIGHT * 4))
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(preview_path)
    print(f"patched IMG.DAT -> {out_path}")
    print(f"plate preview -> {preview_path}")


if __name__ == "__main__":
    main()
