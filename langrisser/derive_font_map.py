#!/usr/bin/env python3
"""Derive a game's glyph slot->character map from an already-mapped game.

Every game in this series generates its own glyph plane: the low range
(kana, ASCII, punctuation) is identical across releases, but the kanji bank
holds a per-script subset in its own order. The bitmaps themselves are the
same artwork, so a new game's map can be recovered mechanically: for each of
its 12x12 tiles, look for a byte-identical tile in the reference game's plane
and inherit that character.

This is the same evidence-based approach `saturn_fix_native_glyphs` uses for
the Saturn plane, generalized to a whole plane, and it emits the shared
font-map CSV convention (`index_dec,index_hex,group,char,source`) so the
result drops straight into a game manifest's `font_map`.

Tiles with no match are the game's own kanji; they are reported (and
optionally listed) for OCR/manual mapping, which is how the reference map was
built in the first place.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from langrisser.build_font import GLYPH_BYTES
from langrisser.game import add_game_args, load_game
from langrisser.release import add_release_args, release_from_args
from langrisser.project import ROOT


def load_map(path: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    for row in csv.DictReader(open(path, encoding="utf-8")):
        if row["index_dec"].isdigit() and row["char"]:
            out[int(row["index_dec"])] = row["char"]
    return out


def plane_end(data: bytes, limit: int) -> int:
    """Last non-empty glyph slot before `limit` (the first text group)."""
    last = -1
    for slot in range(limit // GLYPH_BYTES):
        if any(data[slot * GLYPH_BYTES:(slot + 1) * GLYPH_BYTES]):
            last = slot
    return last


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_game_args(ap, default="l4")
    add_release_args(ap)
    ap.add_argument("--system", required=True,
                    help="This game's SYSTEM.BIN (or Saturn SYSTEM.DAT).")
    ap.add_argument("--reference-game", default="l5",
                    help="Game whose font map is already known.")
    ap.add_argument("--reference-system", default="work/extracted/SYSTEM.BIN")
    ap.add_argument("--out", default=None,
                    help="Output CSV (default: the game manifest's font_map).")
    ap.add_argument("--out-unmatched", default=None,
                    help="Write the unmatched slot list here for OCR/manual work.")
    args = ap.parse_args()

    game = load_game(args.game, args.game_root)
    release = release_from_args(args, platform="ps1")
    reference = load_game(args.reference_game, args.game_root)
    data = Path(args.system).read_bytes()
    ref_data = Path(args.reference_system).read_bytes()
    ref_map = load_map(reference.font_map)

    # Reference bitmaps -> character. Ties keep the lowest slot, which is the
    # one the encoder would pick anyway.
    by_bits: dict[bytes, str] = {}
    for slot, char in sorted(ref_map.items()):
        tile = bytes(ref_data[slot * GLYPH_BYTES:(slot + 1) * GLYPH_BYTES])
        if any(tile):
            by_bits.setdefault(tile, char)

    last = plane_end(data, release.offset("system_scan_start"))
    derived: dict[int, str] = {}
    unmatched: list[int] = []
    for slot in range(last + 1):
        tile = bytes(data[slot * GLYPH_BYTES:(slot + 1) * GLYPH_BYTES])
        if not any(tile):
            continue
        char = by_bits.get(tile)
        if char is None:
            unmatched.append(slot)
        else:
            derived[slot] = char

    out = Path(args.out) if args.out else game.font_map
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["index_dec", "index_hex", "group", "char", "source"],
            lineterminator="\n")
        writer.writeheader()
        for slot, char in sorted(derived.items()):
            writer.writerow({
                "index_dec": slot, "index_hex": f"{slot:X}",
                "group": "confirmed", "char": char,
                "source": f"bitmap:{reference.code}_font",
            })
    print(f"{game.code}: plane ends at slot {last}; derived {len(derived)} glyphs "
          f"from {reference.code}, {len(unmatched)} unmatched -> "
          f"{out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    if args.out_unmatched:
        Path(args.out_unmatched).write_text(
            "\n".join(f"{slot}\t{slot:04X}" for slot in unmatched) + "\n",
            encoding="utf-8")
        print(f"unmatched slots -> {args.out_unmatched}")


if __name__ == "__main__":
    main()
