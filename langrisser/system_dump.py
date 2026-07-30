#!/usr/bin/env python3
"""Dump every translatable SYSTEM string of a release from its offset-table groups.

A build's SYSTEM file stores its UI text (unit/class/item/weapon/spell names,
their triangle-button descriptions, and the menu command help) as a sequence of
*groups*. Each group is::

    [ u16 offset table : N entries ][ N glyph-code strings ]

The offset table starts with `0x0000` and holds strictly increasing 16-bit
*word* offsets; entry `k` is the start of string `k` measured in 16-bit words
from the string base (`base = table_start + N*2`). String `k` therefore lives at
`base + offset[k]*2` and is terminated by `0xFFFF`; its length is
`offset[k+1] - offset[k] - 1` words for `k < N-1`, and the final string runs to
its own `0xFFFF`. Glyph codes index the SYSTEM font (`<0x0720`); `0xFFFC` is a
soft line break; `0xFFFF` terminates.

This is the single source of truth for what text the game shows: there is no
heuristic FFFF scan and no minimum-length filter, so short tails and the first
string of a group (which an FFFF scan would glue onto the table) are captured
exactly. See docs/L45_SYSTEM_BIN_FORMAT.md.

It dumps whichever release is being built, not one particular console: the byte
order comes from the platform, the scan start and group offsets from the release
manifest, and the reordered kanji bank from the release's `kanji_map.csv`. The
ids are the pack's, resolved through the release's recorded `system_mapping.json`
- so the lengths and indents are this build's own while the names stay the ones
the translation is keyed by.
"""
import argparse
import json
from pathlib import Path

from langrisser.game import add_game_args, game_from_args
from langrisser.release import add_release_args, release_from_args
from langrisser.offsetgroups import (
    build_codemap,
    decode_run,
    expand_group_map,
    find_groups,
    load_codemap,
    load_font_map_csv,
    load_system_mapping,
    loose_key,
    pack_id_for,
    run_length,
)
from langrisser.patch_name_entry import grid_spans

MAX_STEP = 0x30          # max plausible string length (+terminator) in words

# The offset-table group model (read_table/base_for/group_at/find_groups plus
# load_codemap/decode_run/run_length) lives in langrisser.offsetgroups so every
# release reuses it with its own config.

# The katakana name-entry grid lives inside group 0 but is owned by
# patch_name_entry.py / saturn_name_entry.py, which rewrite it as fixed
# 5-single-glyph runs. The unified text flow must NOT capture it: re-encoding
# those runs as ordinary text picks readability pair-glyphs (e.g. "ab" in one
# cell), which collapses the 5-column grid and corrupts the rename screen. Its
# spans come from the patcher (the single source of the grid location).


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_game_args(ap)
    add_release_args(ap)
    ap.add_argument("--system-bin", default="work/l5/extracted/SYSTEM.BIN",
                    help="The release's extracted SYSTEM file.")
    ap.add_argument("--tbl", default=None,
                    help="Override the token table (default: the font maps).")
    ap.add_argument("--out", default="work/l5/systemdump/system_strings.json")
    args = ap.parse_args()

    game = game_from_args(args)
    release = release_from_args(args)
    cfg = release.group_config
    data = Path(args.system_bin).read_bytes()
    # Slot->character comes from the game's tracked font map plus this
    # release's delta for the kanji bank it reordered; a curated HHHH=text
    # table stays available as an override.
    if args.tbl:
        codemap = load_codemap(args.tbl)
    else:
        codemap = build_codemap(load_font_map_csv(game.font_map),
                                load_font_map_csv(release.kanji_map),
                                game.kanji_bank_start)
    groups = find_groups(data, cfg)
    release.check_system_groups([table_off for table_off, _, _ in groups])
    grid = grid_spans(data, codemap, cfg.order)
    specs = {int(k): v for k, v in
             (load_system_mapping(release.system_mapping).get("groups") or {}).items()}

    entries = []
    covered = bytearray(len(data))  # mark bytes that belong to a group
    for gi, (table_off, table, base) in enumerate(groups):
        n = len(table)
        last_off = base + table[-1] * 2
        end = last_off + (run_length(data, last_off, cfg) + 1) * 2
        for b in range(table_off, end):
            covered[b] = 1
        targets = expand_group_map(specs[gi], n) if gi in specs else {}
        for k in range(n):
            off = base + table[k] * 2
            if k + 1 < n:
                words = table[k + 1] - table[k] - 1
            else:
                words = run_length(data, off, cfg)
            if any(start <= off < stop for start, stop in grid):
                continue  # name-entry grid run: owned by the name-entry patcher
            entry_id = pack_id_for(targets.get(k) if targets else None, gi, k)
            if entry_id is None:
                continue  # release-only text or preserved: not a pack string
            run = cfg.order.words(data, off, words)
            entries.append({
                "id": entry_id,
                "group": gi,
                "table": f"0x{table_off:05X}",
                "index": k,
                "offset": f"0x{off:05X}",
                "words": words,
                "leading_cells": next(
                    (i for i, word in enumerate(run) if word != 0),
                    len(run),
                ),
                "jp": decode_run(run, codemap),
            })

    # Loose strings: FFFF-terminated runs in the text region that are not part of
    # any offset-table group (e.g. the memory-card error messages). They have no
    # table to regenerate, so the packer keeps them at their fixed offset.
    region_end = max((int(e["offset"], 16) + e["words"] * 2 for e in entries),
                     default=cfg.scan_start)
    loose_offsets: list[int] = []
    pos = cfg.scan_start
    while pos < region_end:
        if covered[pos]:
            pos += 2
            continue
        words = run_length(data, pos, cfg)
        # A run that reaches into a group is not a loose string: it is the
        # gap padding before that group's table, read past its own end
        # because no terminator sits in the gap.
        end = pos + (words + 1) * 2
        if words >= 1 and end <= len(covered) and not any(covered[pos:end]):
            run = cfg.order.words(data, pos, words)
            text = decode_run(run, codemap)
            if words <= MAX_STEP and any(
                "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in text
            ):
                entries.append({
                    "id": loose_key(len(loose_offsets)),
                    "group": -1, "table": None, "index": None,
                    "offset": f"0x{pos:05X}", "words": words,
                    "leading_cells": next(
                        (i for i, word in enumerate(run) if word != 0),
                        len(run),
                    ),
                    "jp": text,
                })
                loose_offsets.append(pos)
        pos += (words + 1) * 2
    release.check_system_loose(loose_offsets)

    entries.sort(key=lambda e: int(e["offset"], 16))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(entries, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(f"dumped {len(entries)} strings from {len(groups)} groups "
          f"of {release.code} -> {args.out}")


if __name__ == "__main__":
    main()
