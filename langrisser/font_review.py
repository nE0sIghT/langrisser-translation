#!/usr/bin/env python3
"""Render groups_report.csv glyph mapping as a self-contained HTML review page.

For every font index: the actual 12x12 glyph from SYSTEM.BIN (scaled,
pixelated) next to the mapped character. Reference characters are rendered
server-side with a CJK TTF so the page does not depend on browser fonts.
Rows listed in the proposals CSV are highlighted and shown in a summary
table at the top.
"""
import argparse
import base64
import csv
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from langrisser.engine import load_engine
from langrisser.project import COMMON_FONT_FIXES, COMMON_FONT_MAP

_GLYPH = load_engine().glyph
GLYPH_W = _GLYPH.width
GLYPH_H = _GLYPH.height
GLYPH_BYTES = _GLYPH.bytes_per_glyph

FONT_CANDIDATES_JP = [
    "external/duckstation/data/resources/fonts/NotoSansJP-VariableFont_wght.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
FONT_CANDIDATES_FALLBACK = [
    "external/duckstation/data/resources/fonts/NotoSansSC-VariableFont_wght.ttf",
]


def load_fonts(size: int = 40):
    fonts = []
    for group in (FONT_CANDIDATES_JP, FONT_CANDIDATES_FALLBACK):
        for cand in group:
            if Path(cand).exists():
                fonts.append(ImageFont.truetype(cand, size=size))
                break
    return fonts


def char_png_b64(ch: str, fonts, size: int = 48) -> str | None:
    if not ch:
        return None
    for font in fonts:
        if hasattr(font, "getmask") and font.getmask(ch).getbbox() is None:
            continue
        img = Image.new("L", (size, size), 255)
        d = ImageDraw.Draw(img)
        bbox = d.textbbox((0, 0), ch, font=font)
        x = (size - (bbox[2] - bbox[0])) // 2 - bbox[0]
        y = (size - (bbox[3] - bbox[1])) // 2 - bbox[1]
        d.text((x, y), ch, font=font, fill=0)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    return None


def glyph_b64(data: bytes, idx: int) -> str:
    off = idx * GLYPH_BYTES
    tile = data[off : off + GLYPH_BYTES]
    img = Image.new("L", (GLYPH_W, GLYPH_H), 255)
    px = img.load()
    for i in range(GLYPH_W * GLYPH_H):
        if tile[i // 8] & (1 << (7 - (i % 8))):
            px[i % GLYPH_W, i // GLYPH_W] = 0
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def img_tag(b64: str | None, cls: str = "") -> str:
    if b64 is None:
        return "<span class='empty'>&mdash;</span>"
    return f"<img class='{cls}' src='data:image/png;base64,{b64}'>"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--groups-report", default=str(COMMON_FONT_MAP))
    ap.add_argument("--system-bin", default="work/l5/extracted/SYSTEM.BIN")
    ap.add_argument("--proposals", default=str(COMMON_FONT_FIXES))
    ap.add_argument("--out", default="work/font_review/font_review.html")
    ap.add_argument("--flagged-only", action="store_true",
                    help="Emit only the flagged-entries table, skip the full grid.")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.groups_report, encoding="utf-8")))
    data = Path(args.system_bin).read_bytes()
    fonts = load_fonts()

    proposals = {}
    ppath = Path(args.proposals)
    if ppath.exists():
        for p in csv.DictReader(open(ppath, encoding="utf-8")):
            proposals[int(p["index_dec"])] = p

    css = """
    body{font-family:sans-serif;background:#1e1e1e;color:#ddd}
    img{image-rendering:pixelated;width:48px;height:48px;background:#fff;vertical-align:middle}
    img.ref{image-rendering:auto}
    .cur,.prop{font-size:40px;line-height:48px;display:inline-block;min-width:48px;text-align:center;vertical-align:middle}
    .cur{color:#9cf}.prop{color:#fc6}.empty{color:#666;font-size:24px}
    table{border-collapse:collapse}td,th{border:1px solid #444;padding:4px 8px;text-align:center}
    .grid{display:flex;flex-wrap:wrap;gap:4px}
    .cell{border:1px solid #333;padding:4px;width:120px}
    .cell small{color:#888;display:block}
    .wrong{border:2px solid #e55}.verify{border:2px solid #dd3}
    h2{border-bottom:1px solid #555;padding-bottom:4px}
    """
    out = ["<!doctype html><meta charset='utf-8'><title>L5 font review</title>",
           f"<style>{css}</style>"]

    out.append("<h2>Flagged entries</h2>")
    out.append("<p>'game' = glyph from SYSTEM.BIN. 'current/proposed (TTF)' = reference "
               "render with Noto JP (fallback Noto SC), independent of browser fonts. "
               "&mdash; means there is no character in that slot (no current mapping or "
               "no proposal yet), not a render failure.</p>")
    out.append("<table><tr><th>idx</th><th>hex</th><th>game</th>"
               "<th>current (TTF)</th><th>current (text)</th>"
               "<th>proposed (TTF)</th><th>proposed (text)</th>"
               "<th>confidence</th><th>reason</th></tr>")
    chars = {int(r["index_dec"]): r["char"] for r in rows if r["index_dec"].isdigit()}
    for idx in sorted(proposals):
        p = proposals[idx]
        cur = chars.get(idx, "")
        out.append(
            f"<tr><td>{idx}</td><td>{idx:04X}</td>"
            f"<td>{img_tag(glyph_b64(data, idx))}</td>"
            f"<td>{img_tag(char_png_b64(cur, fonts), 'ref')}</td>"
            f"<td><span class='cur'>{cur or '∅'}</span></td>"
            f"<td>{img_tag(char_png_b64(p['proposed'], fonts), 'ref')}</td>"
            f"<td><span class='prop'>{p['proposed'] or '?'}</span></td>"
            f"<td>{p['confidence']}</td><td>{p['reason']}</td></tr>"
        )
    out.append("</table>")

    if not args.flagged_only:
        out.append("<h2>Full table</h2><div class='grid'>")
        for r in rows:
            idx = int(r["index_dec"])
            ch = r["char"]
            p = proposals.get(idx)
            cls = "cell"
            if p:
                cls += " wrong" if p["confidence"] == "high" else " verify"
            prop_html = f"<span class='prop'>{p['proposed']}</span>" if p and p["proposed"] else ""
            out.append(
                f"<div class='{cls}'>{img_tag(glyph_b64(data, idx))}"
                f"<span class='cur'>{ch or ''}</span>{prop_html}"
                f"<small>{idx} / 0x{idx:04X} {r['group']}</small></div>"
            )
        out.append("</div>")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"flagged={len(proposals)} out={out_path}")


if __name__ == "__main__":
    main()
