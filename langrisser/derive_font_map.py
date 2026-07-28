#!/usr/bin/env python3
"""Derive a glyph slot->character map from an already-mapped plane.

Every game in this series generates its own glyph plane, and so does every
release of it: the low range (kana, ASCII, punctuation) is identical, but the
kanji bank holds a subset in its own order. The bitmaps themselves are the same
artwork, so a map can be recovered mechanically: for each 12x12 tile, look for a
byte-identical tile in the reference plane and inherit that character.

Two outputs, one derivation:

* by default the whole plane, which is a game's `font_map`;
* with `--bank-only`, just the reordered kanji bank, which is a release's
  `kanji_map.csv` - the delta that lets its tokens be read as text.

Both use the shared CSV convention (`index_dec,index_hex,group,char,source`).

This is measurement, not inference, and it is how the map should be built: the
Saturn bank was first voted from positionally matched record pairs, which put
the wrong character in eleven slots and left seventy-nine unnamed.

Tiles with no match are the plane's own glyphs; they are reported (and
optionally listed) for OCR/manual mapping, which is how the reference map was
built in the first place. In `--bank-only` mode whatever the existing map says
about them is carried over rather than dropped.

Reading two releases' planes makes this reconciliation work: it fills `data/`
once, and no build runs it.
"""

from __future__ import annotations

import argparse
import csv
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
    ap.add_argument("--reference-system", default="work/l5/extracted/SYSTEM.BIN")
    ap.add_argument("--bank-only", action="store_true",
                    help="Map only the reordered kanji bank: a release's delta.")
    ap.add_argument("--out", default=None,
                    help="Output CSV (default: the game's font_map, or the "
                         "release's kanji_map with --bank-only).")
    ap.add_argument("--out-unmatched", default=None,
                    help="Write the unmatched slot list here for OCR/manual work.")
    args = ap.parse_args()

    game = load_game(args.game, args.game_root)
    release = release_from_args(args)
    reference = load_game(args.reference_game, args.game_root)
    data = Path(args.system).read_bytes()
    ref_data = Path(args.reference_system).read_bytes()
    ref_map = load_map(reference.font_map)
    if args.bank_only and game.kanji_bank_start is None:
        raise SystemExit(f"game {game.code} declares no kanji_bank_start")
    first_slot = game.kanji_bank_start if args.bank_only else 0

    # Reference bitmaps -> character. Ties keep the lowest slot, which is the
    # one the encoder would pick anyway.
    by_bits: dict[bytes, str] = {}
    for slot, char in sorted(ref_map.items()):
        tile = bytes(ref_data[slot * GLYPH_BYTES:(slot + 1) * GLYPH_BYTES])
        if any(tile):
            by_bits.setdefault(tile, char)

    # The plane's own ceiling, not the text scan floor: a build may keep
    # unrelated data between the two (Saturn's group pointer directory).
    last = plane_end(data, (release.max_font_slot + 1) * GLYPH_BYTES)
    derived: dict[int, str] = {}
    unmatched: list[int] = []
    for slot in range(first_slot, last + 1):
        tile = bytes(data[slot * GLYPH_BYTES:(slot + 1) * GLYPH_BYTES])
        if not any(tile):
            continue
        char = by_bits.get(tile)
        if char is None:
            unmatched.append(slot)
        else:
            derived[slot] = char

    out = Path(args.out) if args.out else (
        release.kanji_map if args.bank_only else game.font_map)
    source = f"bitmap:{reference.code}_font"
    rows = {slot: {"group": "confirmed", "char": char, "source": source}
            for slot, char in derived.items()}
    kept = 0
    if out.exists():
        # A slot the reference plane does not hold keeps whatever the map
        # already says about it - the readings that were filled in by hand,
        # and the rows recording a tile that is not a character at all. Only
        # what this run can measure is rewritten, so re-running is safe.
        previous = {int(row["index_dec"]): row
                    for row in csv.DictReader(open(out, encoding="utf-8"))
                    if row["index_dec"].isdigit()}
        for slot in unmatched:
            if slot in previous:
                rows[slot] = previous[slot]
                kept += 1

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["index_dec", "index_hex", "group", "char", "source"],
            lineterminator="\n")
        writer.writeheader()
        for slot, row in sorted(rows.items()):
            writer.writerow({
                "index_dec": slot, "index_hex": f"{slot:X}",
                "group": row["group"], "char": row["char"],
                "source": row["source"],
            })
    what = "kanji bank" if args.bank_only else "plane"
    where = release.code if args.bank_only else game.code
    print(f"{where}: {what} ends at slot {last}; derived {len(derived)} glyphs "
          f"from {reference.code}, {len(unmatched)} unmatched"
          f"{f' ({kept} kept as recorded)' if kept else ''} -> "
          f"{out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    if args.out_unmatched:
        Path(args.out_unmatched).write_text(
            "\n".join(f"{slot}\t{slot:04X}" for slot in unmatched) + "\n",
            encoding="utf-8")
        print(f"unmatched slots -> {args.out_unmatched}")


if __name__ == "__main__":
    main()
