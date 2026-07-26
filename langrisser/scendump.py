#!/usr/bin/env python3
"""Dump SCEN.DAT/SCEN2.DAT script text to per-chunk editable files.

Output layout (lang3 toolkit style):
    <out>/<STEM>/chunk_NNN.txt   lines: "<record_index>\t<text>"
    <out>/all_records.csv        flat list for analysis
    <out>/summary.txt
"""
import argparse
import csv
from pathlib import Path

import re

from langrisser.scen import (
    Codec,
    find_text_block,
    load_charmap_csv,
    read_chunk_spans,
    words_from_bytes,
)
from langrisser.rewrap import semantic_plate_slots
from langrisser.game import add_game_args, game_from_args
from langrisser.project import COMMON_FONT_MAP

_TAG = re.compile(r"<\$[0-9A-Fa-f]{4}>")


def _plate_name(text: str) -> str:
    """The visible speaker-plate name from a name-pool record's text."""
    return _TAG.sub("", text).strip()


def dump_file(src: Path, out_dir: Path, codec: Codec) -> list[dict[str, str]]:
    data = src.read_bytes()
    root = out_dir / src.stem
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    # Per-record speaker, read straight from the display commands (no VM walk;
    # see docs/SPEAKER_NAME_EXTRACTION.md). slot is the 0-based name-pool slot,
    # None = no plate, -1 = runtime-remapped crowd line.
    chunk_slots = semantic_plate_slots(src)

    skipped: list[int] = []
    for cidx, (s, e) in enumerate(read_chunk_spans(data)):
        chunk = data[s:e]
        try:
            block = find_text_block(chunk)
        except ValueError:
            # Not every chunk carries dialogue (Langrisser IV has data-only
            # chunks); record them instead of failing the whole dump.
            skipped.append(cidx)
            continue
        decoded = {
            ridx: codec.decode(words_from_bytes(chunk[slice(*block.record_span(ridx))]))
            for ridx in range(1, block.record_count + 1)
        }
        rec_slot = chunk_slots.get(cidx, {})

        def speaker_for(ridx: int) -> str:
            slot = rec_slot.get(ridx, "absent")
            if slot == "absent" or slot is None:
                return ""
            if slot == -1:
                return "(crowd)"
            name = _plate_name(decoded.get(slot + 1, ""))
            return name or f"slot {slot}"

        lines = [
            f"# file={src.name} chunk={cidx} chunk_start=0x{s:06X}",
            f"# block_base=0x{block.base:04X} block_size=0x{block.size:04X} records={block.record_count}",
        ]
        for ridx in range(1, block.record_count + 1):
            text = decoded[ridx]
            words = words_from_bytes(chunk[slice(*block.record_span(ridx))])
            speaker = speaker_for(ridx)
            if speaker:
                lines.append(f"# spk: {speaker}")
            lines.append(f"{ridx}\t{text}")
            rows.append(
                {
                    "source_file": src.name,
                    "chunk_index": str(cidx),
                    "record_index": str(ridx),
                    "speaker": speaker,
                    "word_count": str(len(words)),
                    "text": text,
                }
            )
        (root / f"chunk_{cidx:03d}.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    if skipped:
        print(f"{src.name}: {len(skipped)} chunk(s) without a text block: {skipped}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_game_args(ap)
    ap.add_argument("--scen", default="work/extracted/SCEN.DAT")
    ap.add_argument("--scen2", default=None,
                    help="Second script file (Langrisser V mirrors SCEN in SCEN2).")
    ap.add_argument("--charmap", default=None,
                    help="Slot->char map (default: the game's font map).")
    ap.add_argument("--out-dir", default="work/scriptdump")
    args = ap.parse_args()

    game = game_from_args(args)
    codec = Codec(load_charmap_csv(
        Path(args.charmap) if args.charmap else game.font_map))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    sources = [Path(args.scen)] + ([Path(args.scen2)] if args.scen2 else [])
    for src in sources:
        rows.extend(dump_file(src, out_dir, codec))

    csv_path = out_dir / "all_records.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["source_file", "chunk_index", "record_index", "speaker",
                        "word_count", "text"],
        )
        writer.writeheader()
        writer.writerows(rows)

    unknown = sum(r["text"].count("<$") for r in rows)
    (out_dir / "summary.txt").write_text(
        f"records={len(rows)}\ntagged_tokens={unknown}\ncharmap={args.charmap}\n",
        encoding="utf-8",
    )
    print(f"records={len(rows)} out={out_dir}")


if __name__ == "__main__":
    main()
