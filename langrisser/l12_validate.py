#!/usr/bin/env python3
"""Check a Langrisser I & II translation against the Japanese it replaces.

Runs on the text rather than on a built image, so a record can be checked
before anything is packed. Widths, though, are measured through the glyph plane
the build draws with: how many cells a line takes is the tiler's answer and
nobody else's, and a heading in capitals takes twice what the same letters take
in prose. What it checks:

* every reference the record had is still there, the same ones, the same
  number, in the same order — `<name:12>` prints a character, `<pair>` the
  player's own name, and losing or reordering one says something the Japanese
  did not;
* the record fits its window: no line wider than the window in glyph cells,
  no page taller than the window in lines;
* nothing is left in Japanese.

Layout tags are deliberately not compared against the Japanese. Russian does
not fit the line structure of Japanese — it is longer, and a line that held a
clause there holds half of one here — so `<line>`, `<page>`, `<wait>` and
`<blank>` belong to the translation, and `l12_rewrap` places them. Demanding
the original's break sequence would be demanding the original's sentence
lengths.

What it deliberately does not check is whether the result fits the chunk in
bytes. That answer depends on the glyph plane, which does not carry the target
alphabet yet, and `l12_sceninsert` already reports the chunk that overruns its
padding — asking the same question twice would only let the two answers drift.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from langrisser.game import add_game_args, game_from_args
from langrisser.l12_rewrap import LINE_BREAK, layout_for, name_table
from langrisser.l12_scen import (Reader, Writer, load_assignments, merged_plane,
                                 read_chunks, slot_table)
from langrisser.l12_sceninsert import read_pack
from langrisser.project import add_language_args, language_from_args
from langrisser.release import add_release_args, release_from_args
from langrisser.scen import load_charmap_csv
from langrisser.text_layout import Layout, page_segments, visible_cells

# What the engine substitutes at runtime, and what therefore has to survive
# translation exactly. A raw glyph tag is not here: it names one character of
# the plane, so a translation that spells the word differently simply loses it.
# Nor are phrase references, which are the script's own compression — both
# sides are compared with them inlined (see `references`).
REFERENCE_RE = re.compile(r"<(?:name:\d+|pair|number)>")
# Katakana letters only: the middle dot ・ lives in the same block but
# separates names rather than continuing a word.
KATAKANA_RE = re.compile(r"[ァ-ヶーヽヾ]")
# The bullet, the ellipsis and the corner brackets are punctuation the plane
# draws, not Japanese words, and the target text keeps using them.
PUNCT = "・‥「」、。！？～ー－＋×（）／＆．＿＾"
JP_RE = re.compile(f"[぀-ヿ㐀-鿿]")
BREAKS = ("<line>", "<page>")


def references(reader: Reader, text: str) -> list[str]:
    """The runtime substitutions a record makes, in order.

    Phrase references are inlined first: a translation may keep one or spell
    the words out, and a phrase can hold a name reference of its own.

    A reference that continues a katakana word is not naming anybody. The
    script writes バランス as バ + the name Lance, because the name table
    already holds ランス and that saves two bytes. Katakana *before* the
    reference is what tells this apart: a real mention follows a particle,
    punctuation or a break, never the middle of a word. Spelling the word out
    is the only thing a translation can do with it.
    """
    inlined = reader.inline_phrases(text)
    out = []
    for m in REFERENCE_RE.finditer(inlined):
        before = inlined[m.start() - 1] if m.start() else ""
        if KATAKANA_RE.match(before):
            continue
        out.append(m.group(0))
    return out


def pages(layout: Layout, text: str) -> list[list[str]]:
    """The record as the window shows it: pages of lines, still tagged.

    Splitting is `text_layout`'s, the same code that placed the breaks in
    `l12_rewrap`. A second opinion here would only let the two drift, and the
    one that drifts silently is this one.
    """
    return [page.split(LINE_BREAK) for page in page_segments(layout, text)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_language_args(ap)
    add_game_args(ap, default="l2")
    add_release_args(ap, default="l1-2-ps1-jp")
    ap.add_argument("chunks", nargs="*", type=int,
                    help="Chunks to check; default is every chunk the pack has.")
    ap.add_argument("--scen", default=None)
    ap.add_argument("--translation-root", default=None)
    ap.add_argument("--font-map", default=None)
    ap.add_argument("--assignments", default=None)
    ap.add_argument("--width", type=int, default=None,
                    help="Cells per line (default: the pack's window_width).")
    ap.add_argument("--height", type=int, default=None,
                    help="Lines per page (default: the pack's max_lines).")
    args = ap.parse_args()

    game = game_from_args(args)
    release = release_from_args(args, platform="ps1")
    lang = language_from_args(args)
    font = load_charmap_csv(Path(args.font_map) if args.font_map else game.font_map)
    root = Path(args.translation_root) if args.translation_root else lang.script_dir
    plane = merged_plane(font, load_assignments(
        slot_table(args.assignments, lang, release)))
    writer = Writer(plane, fullwidth_units=set(lang.fullwidth_units))
    layout = layout_for(name_table(game.code, root / "shared.txt"), writer)
    scen = Path(args.scen) if args.scen else Path("work", game.code, "extracted", "SCEN.DAT")
    windows = {int(k): tuple(v) for k, v in (lang.windows or {}).items()}
    default = (args.width or lang.window_width, args.height or lang.max_lines)

    def window(part: int) -> tuple[int, int]:
        w, h = windows.get(part, default)
        return (args.width or w, args.height or h)

    problems = 0
    checked = 0
    seen_shared = False
    for chunk in read_chunks(scen.read_bytes()):
        if args.chunks and chunk.index not in args.chunks:
            continue
        pack_file = root / f"chunk_{chunk.index:03d}.txt"
        shared_file = root / "shared.txt"
        records = read_pack(pack_file) if pack_file.exists() else {}
        # The shared tables sit in every chunk but are translated once, so they
        # are checked once too, against the first chunk that carries them.
        if shared_file.exists() and not seen_shared:
            records = {**read_pack(shared_file), **records}
            seen_shared = True
        if not records:
            continue
        reader = Reader(font, chunk)
        for (pi, si), text in sorted(records.items()):
            if pi >= len(chunk.parts) or si >= len(chunk.parts[pi]):
                print(f"{pack_file}:{pi}/{si}: no such record in the chunk")
                problems += 1
                continue
            raw = chunk.parts[pi][si]
            if not raw:
                continue
            source = reader.decode(raw, expand=False)
            if text == source:
                continue          # untranslated, nothing to check
            checked += 1
            where = f"chunk {chunk.index} part {pi} #{si}"
            was, now = references(reader, source), references(reader, text)
            if was != now:
                print(f"{where}: references differ\n    was {was}\n    now {now}")
                problems += 1
            left = [c for c in JP_RE.findall(text) if c not in PUNCT]
            if left:
                print(f"{where}: still Japanese: {left[:6]}")
                problems += 1
            width, height = window(pi)
            for n, page in enumerate(pages(layout, text)):
                try:
                    widest = max(visible_cells(layout, line) for line in page)
                except ValueError as exc:
                    # A character with no slot in the plane. It has no width
                    # because the disc cannot draw it at all.
                    print(f"{where}: page {n + 1} cannot be drawn: {exc}")
                    problems += 1
                    continue
                if widest > width:
                    print(f"{where}: page {n + 1} is {widest} cells wide, "
                          f"window is {width}")
                    problems += 1
                if len(page) > height:
                    print(f"{where}: page {n + 1} has {len(page)} lines, "
                          f"window holds {height}")
                    problems += 1
    print(f"{game.code}/{lang.code}: {checked} translated records checked, "
          f"{problems} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
