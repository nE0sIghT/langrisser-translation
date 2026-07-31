#!/usr/bin/env python3
"""Scenario-by-scenario review pages for the Langrisser I & II script.

The Langrisser V pages in `review_html.py` compare a JP record against its
reference and target translations; there is nothing to compare here yet, so this
shows what the script *is*: every string of every part, with the references
followed, so a reader sees the line the game draws rather than the bytes.

One page per game. Scenario N is chunk N — the chunk's own part 7 carries its
title card, which is what proves the binding.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

from langrisser.l12_scen import (
    Reader, load_font_map, read_chunks,
)

PART_ROLES = {
    0: "menu and system wording",
    1: "character names",
    2: "items, weapons, armour",
    3: "spells, skills, debug menu",
    4: "phrase table",
    5: "dialogue",
    6: "victory and defeat conditions",
    7: "scenario title card",
    8: "empty",
}
SHARED = {0, 1, 2, 3, 4}

CSS = """
:root{--bg:#171a1d;--panel:#20252a;--line:#394149;--text:#e8e3d8;
--muted:#999b98;--jp:#f1d08a;--tag:#9cc5dd;--shared:#8d8f8c}
*{box-sizing:border-box}
body{font-family:"DejaVu Sans",sans-serif;background:var(--bg);color:var(--text);
margin:0;padding:22px;line-height:1.55}
h1{font:700 24px Georgia,serif;margin:0 0 4px}
h2{font:600 17px Georgia,serif;margin:26px 0 6px;color:var(--jp)}
h3{font:600 13px sans-serif;margin:14px 0 4px;color:var(--muted);
text-transform:uppercase;letter-spacing:.06em}
.sub{color:var(--muted);font-size:13px;margin-bottom:14px}
.toolbar{position:sticky;top:0;z-index:2;background:rgba(23,26,29,.97);
border-bottom:1px solid var(--line);padding:9px 0;margin-bottom:14px;font-size:13px}
.toolbar a{color:var(--tag);margin-right:12px;text-decoration:none}
.index{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 20px}
.index a{background:var(--panel);border:1px solid var(--line);border-radius:2px;
padding:3px 8px;font-size:12px;color:var(--text);text-decoration:none}
.index a:hover{border-color:var(--tag)}
table{border-collapse:collapse;width:100%;margin:4px 0 10px}
td{border-top:1px solid var(--line);padding:5px 8px;vertical-align:top}
td.n{color:var(--muted);font-size:12px;width:52px;text-align:right;
font-variant-numeric:tabular-nums}
td.t{white-space:pre-wrap;font-size:15px}
.tag{color:var(--tag);font-size:12px}
.shared{color:var(--shared)}
.badge{display:inline-block;border:1px solid var(--line);border-radius:2px;
padding:2px 7px;font-size:12px;background:var(--panel);margin-right:6px}
"""

TAG_RE = re.compile(r"<(/?)([a-z!$][^>]*)>")


def markup(text: str) -> str:
    """Escape the text, then colour the control tags the reader left in."""
    out = html.escape(text)
    return re.sub(r"&lt;([^&]+?)&gt;", r'<span class="tag">&lt;\1&gt;</span>', out)


def title_of(reader: Reader, chunk) -> str:
    for raw in chunk.part(7):
        m = re.search(r"「(.+?)」", reader.decode(raw))
        if m:
            return m.group(1).strip()
    return ""


def render(game: str, label: str, chunks, font, depth: int) -> str:
    parts = [f"<h1>{html.escape(label)}</h1>"]
    readers = {c.index: Reader(font, c, depth) for c in chunks}
    titles = {c.index: title_of(readers[c.index], c) for c in chunks}
    scen = [c for c in chunks if titles[c.index]]
    parts.append(f'<div class="sub">{len(chunks)} chunks, {len(scen)} of them '
                 "scenarios. References into the phrase table and the name table "
                 "are followed, so these are the lines the game draws.</div>")
    parts.append('<div class="index">')
    for c in chunks:
        t = titles[c.index] or "—"
        parts.append(f'<a href="#c{c.index}">{c.index}. {html.escape(t)}</a>')
    parts.append("</div>")

    def table(reader, strings):
        rows = ["<table>"]
        for si, raw in enumerate(strings):
            if raw:
                rows.append(f'<tr><td class="n">{si}</td>'
                            f'<td class="t">{markup(reader.decode(raw))}</td></tr>')
        rows.append("</table>")
        return "\n".join(rows)

    # Parts 0-4 are one table copied into every chunk, so show them once. A page
    # that repeated them would be ninety times longer and no more informative.
    first = chunks[0]
    parts.append('<h2 id="shared">Shared tables</h2>')
    parts.append('<div class="sub">The same bytes in every chunk. Where a chunk '
                 "carries a different variant it is noted on that scenario.</div>")
    for pi in sorted(SHARED):
        strings = first.part(pi)
        if not any(strings):
            continue
        parts.append(f"<h3>part {pi} — {PART_ROLES.get(pi, '')}</h3>")
        parts.append(table(readers[first.index], strings))

    for c in chunks:
        reader = readers[c.index]
        t = titles[c.index]
        head = f"{c.index}. {html.escape(t)}" if t else f"{c.index}. (no title card)"
        parts.append(f'<h2 id="c{c.index}">{head}</h2>')
        variant = [pi for pi in sorted(SHARED) if c.part(pi) != first.part(pi)]
        counts = " ".join(f'<span class="badge">part {i}: {len(p)}</span>'
                          for i, p in enumerate(c.parts) if p and i not in SHARED)
        if variant:
            counts += ('<span class="badge">own variant of part '
                       + ", ".join(str(v) for v in variant) + "</span>")
        parts.append(f"<div>{counts}</div>")
        for pi, strings in enumerate(c.parts):
            if pi in SHARED and pi not in variant:
                continue
            if not strings or not any(strings):
                continue
            note = ' <span class="shared">(differs from the shared copy)</span>' \
                if pi in SHARED else ""
            parts.append(f"<h3>part {pi} — {PART_ROLES.get(pi, '')}{note}</h3>")
            parts.append(table(reader, strings))
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scen", action="append", required=True,
                    metavar="GAME=PATH", help="e.g. l2=work/l1-2/extracted/LANG2.SCEN.DAT")
    ap.add_argument("--font-map", default="data/common/font_mapping/l1_2_font_map.csv")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--depth", type=int, default=2)
    args = ap.parse_args()

    font = load_font_map(Path(args.font_map))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    labels = {"l1": "Langrisser I", "l2": "Langrisser II"}
    pages = []
    for spec in args.scen:
        game, _, path = spec.partition("=")
        chunks = read_chunks(Path(path).read_bytes())
        body = render(game, labels.get(game, game), chunks, font, args.depth)
        nav = " ".join(f'<a href="{g}.html">{labels.get(g, g)}</a>'
                       for g in [s.split("=")[0] for s in args.scen])
        page = (f"<!doctype html><meta charset=utf-8><title>{labels.get(game, game)}"
                f" script</title><style>{CSS}</style>"
                f'<div class="toolbar">{nav}</div>{body}')
        (out / f"{game}.html").write_text(page, encoding="utf-8")
        pages.append((game, len(chunks)))
    for game, n in pages:
        print(f"{game}: {n} chunks -> {out / (game + '.html')}")


if __name__ == "__main__":
    main()
