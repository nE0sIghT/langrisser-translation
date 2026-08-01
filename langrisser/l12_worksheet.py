#!/usr/bin/env python3
"""Print one chunk as a translation worksheet.

Two forms of every record side by side: the editable one, which is what the
pack file holds and what `l12_sceninsert` reads back, and the readable one with
the phrase and name references followed. Neither alone is enough — the editable
form does not say who `<name:9>` is, and the readable form cannot be written
back.

Also lists the references the chunk uses, because a translator needs to know
that `<phrase:28>` is the victory-conditions heading before deciding whether to
keep it or write the words out.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from langrisser.game import add_game_args, game_from_args
from langrisser.l12_scen import Reader, read_chunks
from langrisser.scen import load_charmap_csv

REF_RE = re.compile(r"<(phrase|name):(\d+)>")
TAG_RE = re.compile(r"<[^>]+>")
DIALOGUE = (7, 6, 5, 0, 2, 3)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_game_args(ap, default="l1")
    ap.add_argument("chunk", type=int)
    ap.add_argument("--scen", default=None)
    ap.add_argument("--font-map", default=None)
    ap.add_argument("--parts", default="7,6,5",
                    help="Parts to print, in the order to print them.")
    ap.add_argument("--pack-skeleton", metavar="FILE",
                    help="Write a pack file with every record numbered and its "
                         "Japanese in place, to translate over. Transcribing "
                         "numbers by hand is how a whole scenario slips by one "
                         "record. Goes under work/: extracted Japanese is not "
                         "kept in the language pack.")
    ap.add_argument("--skeleton", metavar="PART/INDEX",
                    help="Print one record as a fill-in template: its tags in "
                         "order with the text between them blanked. Layout tags "
                         "have to land in the same places or the screen breaks, "
                         "and matching them by hand is how it goes wrong.")
    args = ap.parse_args()

    game = game_from_args(args)
    font = load_charmap_csv(Path(args.font_map) if args.font_map else game.font_map)
    scen = Path(args.scen) if args.scen else Path("work", game.code, "extracted", "SCEN.DAT")
    chunk = next(c for c in read_chunks(scen.read_bytes()) if c.index == args.chunk)
    reader = Reader(font, chunk)
    parts = [int(p) for p in args.parts.split(",")]

    if args.pack_skeleton:
        out = Path(args.pack_skeleton)
        out.parent.mkdir(parents=True, exist_ok=True)
        blocks = []
        for pi in parts:
            rows = [f"{si}\t{reader.decode(raw, expand=False)}"
                    for si, raw in enumerate(chunk.part(pi)) if raw]
            if rows:
                blocks.append(f"# part {pi}  {len(chunk.part(pi))} strings\n"
                              + "\n".join(rows))
        out.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        print(f"{out}: {sum(b.count(chr(10)) for b in blocks)} records to translate")
        return

    if args.skeleton:
        pi, _, si = args.skeleton.partition("/")
        raw = chunk.part(int(pi))[int(si)]
        text = reader.decode(raw, expand=False)
        out, last = [], 0
        for m in TAG_RE.finditer(text):
            if m.start() > last:
                out.append(f"«{text[last:m.start()]}»")
            out.append(m.group(0))
            last = m.end()
        if last < len(text):
            out.append(f"«{text[last:]}»")
        print("".join(out))
        print(f"\n{len(TAG_RE.findall(text))} tags, "
              f"{sum(1 for x in out if x.startswith('«'))} text slots to fill")
        return

    refs: set[tuple[str, int]] = set()
    for pi in parts:
        print(f"\n===== part {pi} =====")
        for si, raw in enumerate(chunk.part(pi)):
            if not raw:
                continue
            editable = reader.decode(raw, expand=False)
            refs.update((k, int(n)) for k, n in REF_RE.findall(editable))
            print(f"--- {si} ---")
            print(f"  {editable}")
            print(f"  {reader.decode(raw).replace(chr(10), ' ⏎ ')}")

    names = [reader.decode(s) for s in chunk.part(chunk.name_part)]
    phrases = [reader.decode(s) for s in chunk.part(chunk.phrase_part)]
    print("\n===== references used =====")
    for kind, n in sorted(refs):
        if kind == "name":
            value = names[n + 1] if n + 1 < len(names) else "?"
        else:
            value = phrases[n - 1] if 0 < n <= len(phrases) else "?"
        print(f"  <{kind}:{n}> = {value}")


if __name__ == "__main__":
    main()
