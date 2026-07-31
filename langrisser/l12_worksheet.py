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
from langrisser.l12_scen import NAME_PART, PHRASE_PART, Reader, read_chunks
from langrisser.scen import load_charmap_csv

REF_RE = re.compile(r"<(phrase|name):(\d+)>")
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
    args = ap.parse_args()

    game = game_from_args(args)
    font = load_charmap_csv(Path(args.font_map) if args.font_map else game.font_map)
    scen = Path(args.scen) if args.scen else Path("work", game.code, "extracted", "SCEN.DAT")
    chunk = next(c for c in read_chunks(scen.read_bytes()) if c.index == args.chunk)
    reader = Reader(font, chunk)
    parts = [int(p) for p in args.parts.split(",")]

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

    names = [reader.decode(s) for s in chunk.part(NAME_PART)]
    phrases = [reader.decode(s) for s in chunk.part(PHRASE_PART)]
    print("\n===== references used =====")
    for kind, n in sorted(refs):
        if kind == "name":
            value = names[n + 1] if n + 1 < len(names) else "?"
        else:
            value = phrases[n - 1] if 0 < n <= len(phrases) else "?"
        print(f"  <{kind}:{n}> = {value}")


if __name__ == "__main__":
    main()
