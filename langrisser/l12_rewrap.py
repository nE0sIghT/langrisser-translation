#!/usr/bin/env python3
"""Re-flow translated Langrisser I & II records to the window they are drawn in.

The wrapping itself is `text_layout`'s, the same code Langrisser V's rewrap
uses; all this file supplies is this engine's tag vocabulary and the widths its
runtime substitutions draw at. A `<line>` ends a line, a `<page>` ends a page,
a `<blank>` is a space, `<wait>` is zero-width, and `<name:8>` draws whatever
the name table holds — which is why the table is read here rather than guessed
at.

Not everything may be re-flowed. The objectives panel and a briefing card's
title and conditions are laid out, not wrapped — a heading, its bullets, a
blank line, another heading — and a wrapper turns that into prose. Those pages
are recognised by what they carry (the 「 of a title, the × of a heading, the
・ of a bullet) and left exactly as written; the card's narration pages around
them wrap like any other text.

Where Langrisser V compacts pages, this splits them. Russian is longer than the
Japanese it replaces, so a record that filled three lines there needs four
here, and the window holds three: the fourth line becomes the next page rather
than being lost off the bottom.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from langrisser.game import add_game_args, game_from_args
from langrisser.l12_scen import NAME_PART, Reader, read_chunks
from langrisser.l12_sceninsert import read_pack
from langrisser.l12_validate import cells
from langrisser.project import add_language_args, language_from_args
from langrisser.scen import load_charmap_csv
from langrisser.text_layout import LINE, PAGE, PRINTABLE, ZERO, Layout, wrap_stream

TAG_RE = re.compile(r"<[^>]*>")
LINE_BREAK = "<line>"
PAGE_BREAK = "<page>"
PAGE_SEPARATOR = "<wait><page>"
NAME_RE = re.compile(r"<name:(\d+)>")
# The player names the hero on the entry screen; the Japanese default is four
# cells and the original's own line lengths are laid out around that.
PAIR_CELLS = 4


def layout_for(names: dict[int, str]) -> Layout:
    def kind(tag: str) -> str:
        if tag == LINE_BREAK:
            return LINE
        if tag == PAGE_BREAK:
            return PAGE
        if tag == "<wait>":
            return ZERO
        return PRINTABLE

    def tag_cells(tag: str) -> int:
        if tag == "<blank>":
            return 1
        if tag == "<pair>":
            return PAIR_CELLS
        if tag == "<number>":
            return 2
        m = NAME_RE.fullmatch(tag)
        if m:
            return cells(names.get(int(m.group(1)), ""))
        if tag.startswith("<$"):
            return 1
        return 0

    return Layout(
        tag_re=TAG_RE,
        line_break=LINE_BREAK,
        page_break=PAGE_BREAK,
        page_breaks=frozenset({PAGE_BREAK}),
        cells=cells,
        kind=kind,
        tag_cells=tag_cells,
    )


# A page holding any of these is laid out by hand: a title in corner brackets,
# a conditions heading, a bulleted objective.
LAID_OUT = ("「", "×", "・")


def reflow(layout: Layout, text: str, width: int, max_lines: int) -> str:
    """Wrap the pages that are prose; leave the ones that are a layout."""
    out = []
    for page in text.split(PAGE_SEPARATOR):
        if any(mark in page for mark in LAID_OUT):
            out.append(page)
        else:
            out.append(wrap_stream(layout, page, width))
    return split_tall_pages(PAGE_SEPARATOR.join(out), max_lines)


def split_tall_pages(text: str, max_lines: int) -> str:
    """Turn the line break that would overflow a page into a page break."""
    out = []
    for page in text.split(PAGE_SEPARATOR):
        if any(mark in page for mark in LAID_OUT):
            out.append(page)
            continue
        lines = page.split(LINE_BREAK)
        while len(lines) > max_lines:
            out.append(LINE_BREAK.join(lines[:max_lines]))
            lines = lines[max_lines:]
        out.append(LINE_BREAK.join(lines))
    return PAGE_SEPARATOR.join(out)


def name_table(game: str, lang_names: Path | None = None) -> dict[int, str]:
    """`<name:N>` to the text it draws, from the translated shared table."""
    font = load_charmap_csv(Path("data/common/font_mapping/l1_2_font_map.csv"))
    chunk = next(iter(read_chunks(
        Path("work", game, "extracted", "SCEN.DAT").read_bytes())))
    reader = Reader(font, chunk)
    out = {}
    shared = read_pack(lang_names) if lang_names and lang_names.exists() else {}
    for si, raw in enumerate(chunk.part(NAME_PART)):
        text = shared.get((NAME_PART, si)) or (reader.decode(raw) if raw else "")
        out[si - 1] = text
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_language_args(ap)
    add_game_args(ap, default="l1")
    ap.add_argument("chunks", nargs="*", type=int)
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--max-lines", type=int, default=None)
    ap.add_argument("--parts", default="5,7",
                    help="Parts to re-flow: dialogue and the briefing cards.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    game = game_from_args(args)
    lang = language_from_args(args)
    root = lang.script_dir
    windows = {int(k): tuple(v) for k, v in (lang.windows or {}).items()}
    layout = layout_for(name_table(game.code, root / "shared.txt"))
    parts = {int(p) for p in args.parts.split(",")}

    changed = 0
    for pack in sorted(root.glob("*.txt")):
        if args.chunks:
            m = re.search(r"chunk_(\d+)", pack.name)
            if not m or int(m.group(1)) not in args.chunks:
                continue
        out_lines: list[str] = []
        touched = False
        part = None
        for raw in pack.read_text(encoding="utf-8").splitlines():
            header = re.match(r"# part (\d+)", raw)
            if header:
                part = int(header.group(1))
            if "\t" not in raw or raw.startswith("#") or part not in parts:
                out_lines.append(raw)
                continue
            index, _, text = raw.partition("\t")
            width, max_lines = windows.get(
                part, (lang.window_width, lang.max_lines))
            width = args.width or width
            max_lines = args.max_lines or max_lines
            fixed = reflow(layout, text, width, max_lines)
            if fixed != text:
                touched = True
                changed += 1
            out_lines.append(f"{index}\t{fixed}")
        if touched and not args.dry_run:
            pack.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"{game.code}/{lang.code}: {changed} record(s) re-flowed to "
          f"{ {p: windows.get(p) for p in sorted(parts)} }")
    sys.exit(0)


if __name__ == "__main__":
    main()
