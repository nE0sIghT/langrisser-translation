#!/usr/bin/env python3
"""Write a Langrisser I & II language pack back into SCEN.DAT.

The counterpart of `l12_scen`'s dump, and the same shape as `sceninsert` is for
`l45`: the original file is the base, the pack supplies the records it has
translated, and everything it does not carry stays exactly as it was. A record
the pack leaves out is not an error — a partial translation has to build.

Records are addressed the way the dump writes them, `chunk / part / index`, so
a translator edits the file the dump produced and nothing has to agree on a
separate id scheme.

Growth is bounded by the chunk: each one is padded to a `0x800` boundary, so a
longer text section eats that padding and no further. `pack_chunk` refuses to
overrun rather than corrupting the chunk, and this reports which chunk ran out
so the wording can be shortened where it actually matters.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from langrisser.game import add_game_args, game_from_args
from langrisser.l12_scen import (Reader, Writer, load_assignments, merged_plane,
                                 pack_chunk, read_chunks)
from langrisser.project import add_language_args, language_from_args
from langrisser.release import add_release_args, release_from_args
from langrisser.scen import load_charmap_csv

PART_RE = re.compile(r"^# part (\d+)")
RECORD_RE = re.compile(r"^(\d+)\t(.*)$")


def read_pack(path: Path) -> dict[tuple[int, int], str]:
    """`(part, index) -> text` from one dump-shaped chunk file.

    A record is exactly one line. Line and page breaks are tags rather than
    real newlines, precisely so that this stays true: a record that could span
    lines would make a break at the edge of one indistinguishable from the
    separator after it.
    """
    out: dict[tuple[int, int], str] = {}
    part = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = PART_RE.match(line)
        if m:
            part = int(m.group(1))
            continue
        m = RECORD_RE.match(line)
        if m and part is not None:
            out[(part, int(m.group(1)))] = m.group(2)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_language_args(ap)
    add_game_args(ap, default="l2")
    add_release_args(ap, default="l1-2-ps1-jp")
    ap.add_argument("--scen", default=None, help="Original container.")
    ap.add_argument("--out-scen", required=True)
    ap.add_argument("--translation-root", default=None,
                    help="Override the pack's text root.")
    ap.add_argument("--font-map", default=None)
    ap.add_argument("--assignments", default=None,
                    help="Target-language slot table; default is the pack's. "
                         "The encoder needs it because a sacrificed slot no "
                         "longer draws the kanji the Japanese map names.")
    args = ap.parse_args()

    game = game_from_args(args)
    release_from_args(args, platform="ps1")   # validates --game against --release
    lang = language_from_args(args)
    font = load_charmap_csv(Path(args.font_map) if args.font_map else game.font_map)
    table = Path(args.assignments) if args.assignments else lang.font_assignments
    plane = merged_plane(font, load_assignments(table)) if table.exists() else font
    writer = Writer(plane)
    root = Path(args.translation_root) if args.translation_root else lang.script_dir
    scen = Path(args.scen) if args.scen else Path(
        "work", game.code, "extracted", "SCEN.DAT")
    blob = bytearray(scen.read_bytes())

    translated = applied = 0
    grown: list[tuple[int, str]] = []
    for chunk in read_chunks(bytes(blob)):
        pack_file = root / f"chunk_{chunk.index:03d}.txt"
        records = read_pack(pack_file) if pack_file.exists() else {}
        translated += len(records)
        if not records:
            continue
        reader = Reader(font, chunk)
        parts: list[list[bytes]] = [list(p) for p in chunk.parts]
        touched = 0
        for (pi, si), text in sorted(records.items()):
            if pi >= len(parts) or si >= len(parts[pi]):
                raise SystemExit(
                    f"{pack_file}: part {pi} record {si} is not in this chunk")
            was = parts[pi][si]
            if not was:
                continue
            if text == reader.decode(was, expand=False):
                continue
            parts[pi][si] = writer.encode(text)
            touched += 1
        if not touched:
            continue
        original = bytes(blob[chunk.start:chunk.end])
        try:
            blob[chunk.start:chunk.end] = pack_chunk(original, parts)
        except ValueError as exc:
            grown.append((chunk.index, str(exc)))
            continue
        applied += touched

    Path(args.out_scen).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_scen).write_bytes(bytes(blob))
    print(f"{game.code}/{lang.code}: {translated} records in the pack, "
          f"{applied} written -> {args.out_scen}")
    for index, why in grown:
        print(f"  chunk {index} left unchanged: {why}")
    if grown:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
