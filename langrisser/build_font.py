#!/usr/bin/env python3
"""Patch target-language glyphs into the SYSTEM.BIN font plane and emit a table.

Slot assignments come from the language pack's font_slot_assignments.csv
(sacrificed rare-kanji slots chosen by usage analysis). The emitted .tbl
contains the full JP map minus sacrificed chars plus target-language additions and
the space token, so untouched JP lines still encode.
"""
import argparse
import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from langrisser.engine import load_engine
from langrisser.project import COMMON_FONT_MAP, add_language_args, language_from_args

# Plane geometry is the engine's, not this module's: reading it from the
# manifest is what lets a differently shaped plane be described rather than
# hardcoded here and in every other font tool.
_GLYPH = load_engine().glyph
GLYPH_W = _GLYPH.width
GLYPH_H = _GLYPH.height
GLYPH_BYTES = _GLYPH.bytes_per_glyph

# Spleen 6x12 is the English fallback. Language manifests may select another
# 6x12 bitmap font, such as Terminus for Cyrillic; two glyphs then tile a
# 12x12 game cell at a 6px pitch. PixelMplus10 remains the symbol fallback.
FONT_CANDIDATES = [
    "data/fonts/spleen-6x12.bdf",
    "data/fonts/PixelMplus10-Regular.ttf",
]
# The game's native A-Z/digit glyphs put their ink bottom on row 9, so target
# letter bodies must end there too or mixed words jitter by 1px. Baseline
# row 10 achieves that; descenders get squashed from 3px to 2px, which is
# the lesser evil.
BASELINE_ROW = 10
# Wide caps sit like the native kana/kanji: ink bottom on row 10 (the
# second-to-last row), with the last row 11 carrying only descender
# features (Д legs, Ф stem tip, Ц/Щ tails), so the 14px cap body fills
# rows 1..10.
WIDE_BASELINE_ROW = 11
LEADING_SPACE_X = 6
NATIVE_VISUAL_OVERRIDES = {
    0x0005: "?",  # native ？ is too centered for the target font.
    0x0006: "!",  # native ！ is too centered for the target font.
    0x0182: ":",  # native colon leaves a visible gap before :Gnome-style labels.
    **{0x0007 + i: str(i) for i in range(10)},  # keep digit pairs/singles visually uniform.
}
# Render-only substitution: the fullwidth ！／？ glyphs are 10px wide, so a
# combined pair tile (！？, ？！) draws the second one past the 12px cell and
# clips its right edge. The single ！／？ already render as the narrow ASCII
# forms (NATIVE_VISUAL_OVERRIDES), so render the pairs the same way. The .tbl /
# encoder keys stay fullwidth, so the source text still maps to these tiles.
RENDER_SUBST = {"！": "!", "？": "?"}
# Render-only redraw for any tile (pairs and singles alike). The stock
# Terminus 6x12 ё draws its diaeresis two rows tall and flush on the letter
# body, which reads as ears once the runtime outline surrounds it; one-row
# dots with a blank gap row read as dots.
REDRAWN_GLYPHS = {
    "ё": (
        ".....", ".....", ".#.#.", ".....",
        ".###.", "#...#", "#####", "#....",
        "#....", ".####", ".....", ".....",
    ),
}
# A pair has two five-pixel ink areas plus one leading guard column per
# half-cell. Terminus only exceeds that width for these Cyrillic forms; Omega
# comes from the wider symbol fallback. Singles retain their original width.
# Wide-caps compact forms for the 14px caps font. On the row-11 wide-caps
# baseline the cap body fills rows 1..10 and Ф/Д fit whole (their stem tip
# and legs land on row 11), but glyphs with ink above the cap line or two
# rows below the baseline overshoot the cell: Ё/Й stack a diacritic on the
# full body, Ц/Щ descend two rows. These forms keep the 14px stroke style
# and compress the body or tail by one row, staying on the shared
# baseline. Rows are centered horizontally in the cell.
WIDE_CAPS_COMPACT = {
    "Ё": (
        ".#..#.",
        "......",
        "######",
        "#.....",
        "#.....",
        "#.....",
        "####..",
        "#.....",
        "#.....",
        "#.....",
        "######",
        "......",
    ),
    "Й": (
        ".#..#.",
        "..##..",
        "#....#",
        "#....#",
        "#...##",
        "#..#.#",
        "#.#..#",
        "##...#",
        "#....#",
        "#....#",
        "#....#",
        "......",
    ),
    "Ц": (
        ".......",
        "#....#.",
        "#....#.",
        "#....#.",
        "#....#.",
        "#....#.",
        "#....#.",
        "#....#.",
        "#....#.",
        "#....#.",
        ".######",
        "......#",
    ),
    "Щ": (
        "........",
        "#..#..#.",
        "#..#..#.",
        "#..#..#.",
        "#..#..#.",
        "#..#..#.",
        "#..#..#.",
        "#..#..#.",
        "#..#..#.",
        "#..#..#.",
        ".#######",
        ".......#",
    ),
}
COMPACT_PAIR_GLYPHS = {
    "д": (
        ".....", ".....", ".....", ".....",
        ".###.", ".#.#.", ".#.#.", ".#.#.",
        ".#.#.", "#####", "#...#", ".....",
    ),
    "Д": (
        ".....", ".....", ".###.", ".#.#.",
        ".#.#.", ".#.#.", ".#.#.", ".#.#.",
        ".#.#.", "#####", "#...#", ".....",
    ),
    "ц": (
        ".....", ".....", ".....", ".....",
        "#..#.", "#..#.", "#..#.", "#..#.",
        "#..#.", ".####", "....#", "....#",
    ),
    "Ц": (
        ".....", ".....", "#..#.", "#..#.",
        "#..#.", "#..#.", "#..#.", "#..#.",
        "#..#.", ".####", "....#", "....#",
    ),
    "щ": (
        ".....", ".....", ".....", ".....",
        "#.#.#", "#.#.#", "#.#.#", "#.#.#",
        "#.#.#", ".####", "....#", "....#",
    ),
    "Ω": (
        ".....", ".....", ".###.", "#...#",
        "#...#", "#...#", "#...#", "#...#",
        ".#.#.", "##.##", ".....", ".....",
    ),
}


def pick_fonts(path: str, size: int) -> list[ImageFont.FreeTypeFont]:
    """All available candidate fonts; the first is primary, the rest are
    per-glyph fallbacks for characters the primary lacks (e.g. ♀/♂ live in
    PixelMplus but not in the bitmap fonts)."""
    fonts = []
    for cand in ([path] if path else []) + FONT_CANDIDATES:
        if cand and Path(cand).exists():
            fonts.append(ImageFont.truetype(cand, size=12 if cand.endswith(".bdf") else size))
    if not fonts:
        raise SystemExit("no usable TTF found")
    return fonts


def font_has(font: ImageFont.FreeTypeFont, ch: str) -> bool:
    # A char missing from the font renders as the same mask as a codepoint
    # that is guaranteed to be absent.
    def mask_bytes(c: str) -> bytes:
        mask = font.getmask(c)
        return bytes(mask) if mask.size[0] else b""
    return mask_bytes(ch) != mask_bytes("\U000E0000")


def render_wide_capital(
    ch: str,
    fonts: list[ImageFont.FreeTypeFont],
    caps_fonts: list[ImageFont.FreeTypeFont],
) -> Image.Image:
    """Render a single capital, wide when a dedicated caps font is set.

    An unpaired capital drawn at half width floats in the 12px cell, so
    all-caps words (machine lines, arcade splashes) look starved. With a
    caps font (e.g. the 8x14 Terminus strike) the capital keeps real
    letterforms at cap height 10 on the row-11 baseline (ink rows 1..10,
    the native kana/kanji ink bottom), filling the cell without the
    "square" look of a 2x stretch; descender features may use row 11.
    Glyphs whose ink would overshoot the cell must provide a compact
    form; shifting them off the shared baseline reads as a defect in
    game. Without a caps font, the capital is the centered running-text
    glyph.
    """
    img = Image.new("L", (GLYPH_W, GLYPH_H), 255)
    art = WIDE_CAPS_COMPACT.get(ch) if caps_fonts else None
    if art:
        d = ImageDraw.Draw(img)
        x0 = (GLYPH_W - len(art[0])) // 2
        for y, row in enumerate(art):
            for dx, value in enumerate(row):
                if value == "#":
                    d.point((x0 + dx, y), fill=0)
        return img
    caps_font = next((f for f in caps_fonts if font_has(f, ch)), None)
    font = caps_font or next((f for f in fonts if font_has(f, ch)), fonts[0])
    head = GLYPH_H
    canvas = Image.new("L", (GLYPH_W, GLYPH_H + head), 255)
    d = ImageDraw.Draw(canvas)
    ascent, _descent = font.getmetrics()
    baseline = WIDE_BASELINE_ROW if caps_font is not None else BASELINE_ROW
    bbox = d.textbbox((0, 0), ch, font=font)
    d.text((0 - bbox[0], head + baseline - ascent), ch, font=font, fill=0)
    canvas = canvas.point(lambda v: 0 if v < 140 else 255)
    px = canvas.load()
    ink = [(x, y - head) for x in range(GLYPH_W) for y in range(GLYPH_H + head)
           if px[x, y] == 0]
    if not ink:
        return img
    width = max(x for x, _ in ink) - min(x for x, _ in ink) + 1
    shift = (GLYPH_W - width) // 2 - min(x for x, _ in ink)
    out = img.load()
    for x, y in ink:
        nx = x + shift
        if caps_font is not None and (
                nx in (0, GLYPH_W - 1) or y < 0 or y >= GLYPH_H):
            raise ValueError(
                f"wide capital {ch!r} does not fit the cell; "
                "add a compact wide form"
            )
        out[nx, y] = 0
    return img


def render_tile(
    text: str,
    fonts: list[ImageFont.FreeTypeFont],
    caps_fonts: list[ImageFont.FreeTypeFont],
) -> bytes:
    """Render 1 or 2 characters into one 12x12 tile on a common baseline.

    Pairs are drawn at a 6px pitch (PixelMplus halfwidth glyphs are 5px
    wide), which is what makes translated text as dense as the JP original and
    removes the huge inter-letter gaps of one-letter-per-cell text.
    Single lowercase/punctuation is left-aligned so word tails join up;
    single capitals render fullwidth (see render_wide_capital).
    """
    if len(text) == 1 and text.isalpha() and text.isupper():
        wide = render_wide_capital(text, fonts, caps_fonts)
        wpx = wide.load()
        out = bytearray(GLYPH_BYTES)
        for i in range(GLYPH_W * GLYPH_H):
            if wpx[i % GLYPH_W, i // GLYPH_W] == 0:
                out[i // 8] |= 1 << (7 - (i % 8))
        return bytes(out)
    img = Image.new("L", (GLYPH_W, GLYPH_H), 255)
    d = ImageDraw.Draw(img)
    for k, ch in enumerate(text[:2]):
        if ch == " ":
            continue
        ch = RENDER_SUBST.get(ch, ch)
        compact = REDRAWN_GLYPHS.get(ch)
        if compact is None and len(text) == 2:
            compact = COMPACT_PAIR_GLYPHS.get(ch)
        if compact:
            x = k * 6
            for y, row in enumerate(compact):
                for px, value in enumerate(row):
                    if value == "#":
                        d.point((x + px, y), fill=0)
            continue
        font = next((f for f in fonts if font_has(f, ch)), fonts[0])
        ascent, _descent = font.getmetrics()
        bbox = d.textbbox((0, 0), ch, font=font)
        x = k * 6
        if len(text) == 2 and text[0] == " " and k == 1:
            x = LEADING_SPACE_X
        d.text((x - bbox[0], BASELINE_ROW - ascent), ch, font=font, fill=0)
    if "…" in text:
        img = shift_image_down(img, 3)
    img = img.point(lambda v: 0 if v < 140 else 255)
    img = add_left_guard(img, text)
    px = img.load()
    out = bytearray(GLYPH_BYTES)
    for i in range(GLYPH_W * GLYPH_H):
        if px[i % GLYPH_W, i // GLYPH_W] == 0:
            out[i // 8] |= 1 << (7 - (i % 8))
    return bytes(out)


def add_left_guard(img: Image.Image, text: str) -> Image.Image:
    """Keep column zero blank, matching the native game's text glyphs.

    Each six-pixel half-cell reserves its first column: pair glyphs occupy
    x=1..5 and x=7..11. Wide pair glyphs require an explicit compact form;
    silently dropping or merging their edge pixels would corrupt the letter.
    Singles have the full remaining eleven columns available.
    """
    src = img.load()
    out = Image.new("L", img.size, 255)
    dst = out.load()
    if len(text) == 2:
        if any(src[x, y] == 0 for x in (5, 11) for y in range(GLYPH_H)):
            raise ValueError(
                f"pair {text!r} contains a glyph wider than five pixels; "
                "add a compact pair form"
            )
        for y in range(GLYPH_H):
            for x in range(5):
                dst[x + 1, y] = src[x, y]
            for x in range(6, GLYPH_W - 1):
                dst[x + 1, y] = src[x, y]
    else:
        if any(src[GLYPH_W - 1, y] == 0 for y in range(GLYPH_H)):
            raise ValueError(
                f"single glyph {text!r} is too wide for a left guard"
            )
        for y in range(GLYPH_H):
            for x in range(GLYPH_W - 1):
                dst[x + 1, y] = src[x, y]
    return out


def shift_image_down(img: Image.Image, dy: int) -> Image.Image:
    out = Image.new("L", img.size, 255)
    out.paste(img.crop((0, 0, img.width, img.height - dy)), (0, dy))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--groups-report", default=None)
    ap.add_argument("--assignments", default=None)
    ap.add_argument("--system-bin", default="work/l5/extracted/SYSTEM.BIN")
    ap.add_argument("--out-system-bin", default=None)
    ap.add_argument("--out-tbl", default=None)
    ap.add_argument("--font", default=None)
    ap.add_argument("--font-size", type=int, default=None)
    ap.add_argument("--caps-font", default=None)
    ap.add_argument("--caps-font-size", type=int, default=None)
    ap.add_argument("--max-slot", type=int, default=1820,
                    help="Highest writable glyph slot (PS1 plane: 1820; "
                         "Saturn: 1819 — slot 1820 would overwrite the "
                         "group pointer directory at 0x8000).")
    args = ap.parse_args()

    lang = language_from_args(args)
    groups_report = Path(args.groups_report) if args.groups_report else COMMON_FONT_MAP
    assignments_path = Path(args.assignments) if args.assignments else lang.font_assignments
    out_system_bin = Path(args.out_system_bin) if args.out_system_bin else lang.build_path("SYSTEM.BIN.{lang}.font")
    out_tbl = Path(args.out_tbl) if args.out_tbl else lang.tbl
    font_path = args.font if args.font is not None else (str(lang.font) if lang.font else "")
    font_size = args.font_size if args.font_size is not None else lang.font_size
    caps_font_path = args.caps_font if args.caps_font is not None else (
        str(lang.caps_font) if lang.caps_font else "")
    caps_font_size = args.caps_font_size if args.caps_font_size is not None else lang.caps_font_size

    assignments: dict[int, str] = {}
    for row in csv.DictReader(open(assignments_path, encoding="utf-8")):
        idx = int(row["index_dec"])
        if idx > args.max_slot:
            raise SystemExit(
                f"slot {idx} is beyond the font plane (last writable slot is "
                f"{args.max_slot}; later bytes hold platform data)"
            )
        assignments[idx] = row["char"]

    tok2char: dict[int, str] = {}
    for row in csv.DictReader(open(groups_report, encoding="utf-8")):
        if row["index_dec"].isdigit() and len((row["char"] or "")) == 1:
            tok2char[int(row["index_dec"])] = row["char"]
    # An assigned char must win over a native glyph with the same label
    # (the JP font has its own a/m/p for am/pm clocks; they are styled
    # fullwidth and clash visually with the rendered lowercase). The
    # encoder takes the lowest token per char, so drop the duplicates.
    assigned_chars = set(assignments.values())
    for tok in [t for t, c in tok2char.items() if c in assigned_chars]:
        del tok2char[tok]
    for tok, ch in assignments.items():
        tok2char[tok] = ch
    tok2char[0x0000] = " "
    # ASCII normalization targets for translated text on native fullwidth glyphs.
    tok2char.setdefault(0x0005, "？")
    tok2char.setdefault(0x0006, "！")

    fonts = pick_fonts(font_path, font_size)
    caps_fonts = []
    if caps_font_path:
        if not Path(caps_font_path).exists():
            raise SystemExit(f"caps font not found: {caps_font_path}")
        caps_fonts.append(ImageFont.truetype(caps_font_path, size=caps_font_size or 12))
    data = bytearray(Path(args.system_bin).read_bytes())
    for tok, ch in assignments.items():
        data[tok * GLYPH_BYTES : (tok + 1) * GLYPH_BYTES] = render_tile(ch, fonts, caps_fonts)
    for tok, ch in NATIVE_VISUAL_OVERRIDES.items():
        data[tok * GLYPH_BYTES : (tok + 1) * GLYPH_BYTES] = render_tile(ch, fonts, caps_fonts)

    out_system_bin.parent.mkdir(parents=True, exist_ok=True)
    out_system_bin.write_bytes(bytes(data))

    lines = [
        f"# Langrisser V {lang.label} insert table (generated)",
        "# Format: HHHH=c",
    ]
    for tok in sorted(tok2char):
        lines.append(f"{tok:04X}={tok2char[tok]}")
    out_tbl.parent.mkdir(parents=True, exist_ok=True)
    out_tbl.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"glyphs_patched={len(assignments)} out_bin={out_system_bin} out_tbl={out_tbl}")


if __name__ == "__main__":
    main()
