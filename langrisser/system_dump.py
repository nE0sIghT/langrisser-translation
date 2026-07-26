#!/usr/bin/env python3
"""Dump every translatable SYSTEM.BIN string from its offset-table groups.

SYSTEM.BIN stores its UI text (unit/class/item/weapon/spell names, their
triangle-button descriptions, and the menu command help) as a sequence of
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
exactly. See docs/SYSTEM_BIN_FORMAT.md.
"""
import argparse
import json
import struct
from pathlib import Path

from langrisser.game import add_game_args, game_from_args
from langrisser.release import add_release_args, release_from_args
from langrisser.offsetgroups import (
    PS1,
    GroupConfig,
    decode_run,
    find_groups,
    group_key,
    loose_key,
    load_codemap,
    load_font_map_csv,
    run_length,
)
from langrisser.patch_name_entry import grid_span as name_entry_grid_span

SCAN_START = 0x8052      # first verified text group table
MAX_STEP = 0x30          # max plausible string length (+terminator) in words

# The offset-table group model (read_table/base_for/group_at/find_groups plus
# load_codemap/decode_run/run_length) lives in langrisser.offsetgroups so the Saturn
# tooling reuses it with a big-endian config. It is imported here and re-exported
# for the packer and other PS1 callers, which use the default PS1 config.

# The katakana name-entry grid lives inside group 0 but is owned by
# patch_name_entry.py, which rewrites it as fixed 5-single-glyph runs.
# The unified text flow must NOT capture it: re-encoding those runs as ordinary
# text picks readability pair-glyphs (e.g. "ab" in one cell), which collapses
# the 5-column grid and corrupts the rename screen. Its span comes from the
# patcher (the single source of the grid location); see name_entry_grid_span.


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_game_args(ap)
    add_release_args(ap)
    ap.add_argument("--system-bin", default="work/l5/extracted/SYSTEM.BIN")
    ap.add_argument("--tbl", default=None,
                    help="JP token table (default: the game's font map).")
    ap.add_argument("--out", default="work/l5/systemdump/system_strings.json")
    args = ap.parse_args()

    # Each build's text groups start at their own offset; everything else in
    # the group model is shared.
    game = game_from_args(args)
    release = release_from_args(args, platform="ps1")
    cfg = GroupConfig(order=PS1.order,
                      scan_start=release.offset("system_scan_start"))
    data = Path(args.system_bin).read_bytes()
    # The game's tracked font map is the slot->character source of truth; the
    # HHHH=text table stays available as an override.
    table = Path(args.tbl) if args.tbl else game.text_table
    codemap = load_codemap(str(table)) if table else load_font_map_csv(game.font_map)
    groups = find_groups(data, cfg)
    release.check_system_groups([table_off for table_off, _, _ in groups])
    grid_span = name_entry_grid_span(data, codemap)

    entries = []
    covered = bytearray(len(data))  # mark bytes that belong to a group
    for gi, (table_off, table, base) in enumerate(groups):
        n = len(table)
        last_off = base + table[-1] * 2
        end = last_off + (run_length(data, last_off) + 1) * 2
        for b in range(table_off, end):
            covered[b] = 1
        for k in range(n):
            off = base + table[k] * 2
            if k + 1 < n:
                words = table[k + 1] - table[k] - 1
            else:
                words = run_length(data, off)
            if grid_span and grid_span[0] <= off < grid_span[1]:
                continue  # name-entry grid run: owned by langrisser.patch_name_entry
            run = list(struct.unpack_from("<%dH" % words, data, off)) if words else []
            entries.append({
                "id": group_key(gi, k),
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
    region_end = max((int(e["offset"], 16) + e["words"] * 2 for e in entries), default=cfg.scan_start)
    loose_offsets: list[int] = []
    pos = cfg.scan_start
    while pos < region_end:
        if covered[pos]:
            pos += 2
            continue
        words = run_length(data, pos)
        if words >= 1 and not covered[pos]:
            run = list(struct.unpack_from("<%dH" % words, data, pos))
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

    entries.sort(key=lambda e: int(e["offset"], 16))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"dumped {len(entries)} strings from {len(groups)} groups -> {args.out}")


if __name__ == "__main__":
    main()
