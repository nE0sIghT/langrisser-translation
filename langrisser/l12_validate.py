#!/usr/bin/env python3
"""Check a Langrisser I & II translation against the Japanese it replaces.

Runs on the text, not on bytes, because the byte form needs slots the plane
does not have yet while the Japanese is still in place. What it can check now
is what actually breaks a scenario if it is wrong:

* every control tag the record had is still there, the same ones and the same
  number of them — a lost `<name:12>` prints nobody, a lost `<phrase:217>` eats
  a clause, and a lost `<page>` runs two screens together;
* their order is unchanged, because a name that moves past a line break lands
  on the wrong line;
* the record still fits the window it is drawn in, counted in cells rather than
  characters, since a pair glyph is one cell and a single is one cell;
* nothing is left in Japanese.

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
from langrisser.l12_scen import Reader, read_chunks
from langrisser.l12_sceninsert import read_pack
from langrisser.project import add_language_args, language_from_args
from langrisser.scen import load_charmap_csv

TAG_RE = re.compile(r"<(?:\$[0-9A-Fa-f]{4}|[a-z]+(?::\d+)?)>")
# A raw glyph tag names one character of the plane, so a translation that spells
# the word differently loses it and nothing breaks. Every other tag either
# substitutes something at runtime or lays the page out, and losing one of those
# breaks the scene. Phrase references are neither: they are the script's own
# compression, so both sides are compared with them inlined (see `tags`).
DROPPABLE = re.compile(r"<\$[0-9A-Fa-f]{4}>")
# The bullet, the ellipsis and the corner brackets are punctuation the plane
# draws, not Japanese words, and the target text keeps using them.
PUNCT = "・‥「」、。！？～ー－＋×（）／＆．＿＾"
JP_RE = re.compile(f"[぀-ヿ㐀-鿿]")
BREAKS = ("<line>", "<page>")


def tags(reader: Reader, text: str) -> list[str]:
    """The tags a record lays out, with phrase references written out first.

    A translation is free to drop a phrase reference and spell the words, but
    a phrase can hold layout tags of its own — `<phrase:131>` is three blanks
    and a corner bracket — and those become the record's own once the
    reference goes. Comparing the two sides unexpanded counts them as newly
    invented, which is how a correct title card gets reported as broken.
    """
    inlined = reader.inline_phrases(text)
    return [t for t in TAG_RE.findall(inlined) if not DROPPABLE.fullmatch(t)]


CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def cells(text: str) -> int:
    """Width of a record's longest line, in glyph cells.

    A cell holds one Japanese glyph but two Cyrillic letters, because the
    target font packs them in pairs the way `build_font` already does for
    Langrisser V. Counting characters would reject lines that fit.
    """
    widest = 0
    for line in TAG_RE.sub(lambda m: "\n" if m.group(0) in BREAKS else "", text).split("\n"):
        latin = len(CYRILLIC.findall(line))
        widest = max(widest, (latin + 1) // 2 + (len(line) - latin))
    return widest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_language_args(ap)
    add_game_args(ap, default="l2")
    ap.add_argument("chunks", nargs="*", type=int,
                    help="Chunks to check; default is every chunk the pack has.")
    ap.add_argument("--scen", default=None)
    ap.add_argument("--translation-root", default=None)
    ap.add_argument("--font-map", default=None)
    ap.add_argument("--width", type=int, default=None,
                    help="Cells per line (default: the pack's window_width).")
    args = ap.parse_args()

    game = game_from_args(args)
    lang = language_from_args(args)
    font = load_charmap_csv(Path(args.font_map) if args.font_map else game.font_map)
    root = Path(args.translation_root) if args.translation_root else lang.script_dir
    scen = Path(args.scen) if args.scen else Path("work", game.code, "extracted", "SCEN.DAT")
    width = args.width or lang.window_width

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
            was, now = tags(reader, source), tags(reader, text)
            if was != now:
                lost = [t for t in was if now.count(t) < was.count(t)]
                extra = [t for t in now if was.count(t) < now.count(t)]
                if lost or extra:
                    print(f"{where}: tags differ — lost {lost or '-'}, extra {extra or '-'}")
                else:
                    print(f"{where}: tag order changed\n    was {was}\n    now {now}")
                problems += 1
            left = [c for c in JP_RE.findall(text) if c not in PUNCT]
            if left:
                print(f"{where}: still Japanese: {left[:6]}")
                problems += 1
            wide = cells(text)
            if wide > width:
                print(f"{where}: {wide} cells wide, window is {width}")
                problems += 1
    print(f"{game.code}/{lang.code}: {checked} translated records checked, "
          f"{problems} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
