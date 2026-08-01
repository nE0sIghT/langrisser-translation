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
from langrisser.l12_phrases import rebuild as rebuild_phrases
from langrisser.container import pad_chunk, rebuild_container_fixed_size
from langrisser.l12_scen import (PHRASE_PART, Reader, Writer, load_assignments,
                                 merged_plane, pack_chunk, read_chunks)
from langrisser.scen import read_chunk_spans
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
    writer = Writer(plane, fullwidth_units=set(lang.fullwidth_units))
    root = Path(args.translation_root) if args.translation_root else lang.script_dir
    scen = Path(args.scen) if args.scen else Path(
        "work", game.code, "extracted", "SCEN.DAT")
    blob = scen.read_bytes()
    pieces = [blob[a:b] for a, b in read_chunk_spans(blob)]

    # Parts 0-4 are one table copied into every chunk, so they are translated
    # once, in `shared.txt`, and applied wherever the copy is really the same.
    # Langrisser II carries a second variant of some of them; a chunk holding
    # that one keeps its Japanese rather than being given the wrong strings.
    shared_file = root / "shared.txt"
    shared = read_pack(shared_file) if shared_file.exists() else {}
    reference: dict[tuple[int, int], bytes] = {}
    if shared:
        first = next(iter(read_chunks(blob)))
        for pi, si in shared:
            if pi < len(first.parts) and si < len(first.parts[pi]):
                reference[(pi, si)] = first.parts[pi][si]

    translated = applied = skipped = 0
    for chunk in read_chunks(bytes(blob)):
        pack_file = root / f"chunk_{chunk.index:03d}.txt"
        records = dict(read_pack(pack_file)) if pack_file.exists() else {}
        translated += len(records)
        for key, text in shared.items():
            pi, si = key
            if pi >= len(chunk.parts) or si >= len(chunk.parts[pi]):
                continue
            if chunk.parts[pi][si] != reference.get(key):
                skipped += 1
                continue
            records.setdefault(key, text)
        if not records:
            continue
        reader = Reader(font, chunk)
        parts: list[list[bytes]] = [list(p) for p in chunk.parts]
        edited: dict[tuple[int, int], str] = {}
        for (pi, si), text in sorted(records.items()):
            if pi >= len(parts) or si >= len(parts[pi]):
                raise SystemExit(
                    f"{pack_file}: part {pi} record {si} is not in this chunk")
            was = parts[pi][si]
            if not was:
                continue
            if text == reader.decode(was, expand=False):
                continue
            edited[(pi, si)] = text
        touched = len(edited)
        if touched:
            table, rewritten = rebuild_phrases(chunk, edited, writer)
            parts[PHRASE_PART] = table
            for (pi, si), raw in rewritten.items():
                parts[pi][si] = raw
        if not touched:
            continue
        # Uncapped: a chunk may outgrow its own padding, and the layout below
        # pays for it out of the container's.
        pieces[chunk.index] = pack_chunk(bytes(blob[chunk.start:chunk.end]),
                                         parts, cap=False)
        applied += touched

    # Same layout Langrisser V uses: 0x800 alignment, reclaim whole sectors of
    # trailing padding from the back, and refuse rather than break either rule.
    spans = read_chunk_spans(blob)
    rebuilt = rebuild_container_fixed_size(
        blob,
        [pad_chunk(bytes(piece), blob[a:b])
         for piece, (a, b) in zip(pieces, spans)],
        spans, str(scen))
    if len(rebuilt) != len(blob):
        raise SystemExit("the container changed size, which the disc cannot take")

    Path(args.out_scen).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_scen).write_bytes(rebuilt)
    moved = sum(1 for (a, _), off in zip(read_chunk_spans(blob),
                                         read_chunk_spans(rebuilt)) if a != off[0])
    print(f"{game.code}/{lang.code}: {translated} records in the pack, "
          f"{len(shared)} shared-table records, {applied} written, "
          f"{moved} chunk(s) relocated -> {args.out_scen}")
    if skipped:
        print(f"  {skipped} shared-table record(s) left alone: the chunk holds "
              f"a different variant of that table")


if __name__ == "__main__":
    main()
