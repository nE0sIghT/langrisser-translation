#!/usr/bin/env python3
"""Dump Saturn SYSTEM.DAT string groups in read-only mode.

Saturn `SYSTEM.DAT` uses the same offset-table group model as PS1 `SYSTEM.BIN`,
so this tool is a thin front end over the shared `langrisser.offsetgroups` model with
the Saturn big-endian config. See docs/SATURN_DISC_FORMAT.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from langrisser.binfmt import ByteOrder
from langrisser.offsetgroups import (
    SATURN,
    GroupConfig,
    decode_run,
    find_groups,
    load_codemap,
    run_length,
)


def dump_system(data: bytes, codemap: dict[int, str], cfg: GroupConfig) -> dict:
    groups = find_groups(data, cfg)
    entries = []
    for group_index, (table_off, table, base) in enumerate(groups):
        for index in range(len(table)):
            off = base + table[index] * 2
            if index + 1 < len(table):
                word_count = table[index + 1] - table[index] - 1
            else:
                word_count = run_length(data, off, cfg)
            words = cfg.order.words(data, off, word_count)
            entries.append({
                "id": f"table:{table_off:05X}:{index}",
                "group": group_index,
                "table": f"0x{table_off:05X}",
                "index": index,
                "offset": f"0x{off:05X}",
                "words": word_count,
                "leading_cells": next((i for i, word in enumerate(words) if word != 0), len(words)),
                "jp": decode_run(words, codemap),
            })

    def group_end(base: int, table: list[int]) -> int:
        last_off = base + table[-1] * 2
        return last_off + (run_length(data, last_off, cfg) + 1) * 2

    return {
        "endian": cfg.order.endian,
        "scan_start": f"0x{cfg.scan_start:05X}",
        "group_count": len(groups),
        "string_count": len(entries),
        "groups": [
            {
                "group": i,
                "table": f"0x{table_off:05X}",
                "entries": len(table),
                "base": f"0x{base:05X}",
                "end": f"0x{group_end(base, table):05X}",
            }
            for i, (table_off, table, base) in enumerate(groups)
        ],
        "entries": entries,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--system", default="work/l5/build/saturn/SYSTEM.DAT")
    ap.add_argument("--tbl", default="data/common/tables/lang5_jp.tbl")
    ap.add_argument("--out", default="work/l5/build/saturn/system_strings.json")
    ap.add_argument("--scan-start", type=lambda value: int(value, 0), default=SATURN.scan_start)
    ap.add_argument("--endian", choices=("be", "le"), default=SATURN.order.endian)
    args = ap.parse_args()

    cfg = GroupConfig(order=ByteOrder(args.endian), scan_start=args.scan_start)
    data = Path(args.system).read_bytes()
    codemap = load_codemap(args.tbl)
    dumped = dump_system(data, codemap, cfg)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dumped, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"dumped {dumped['string_count']} strings from {dumped['group_count']} "
        f"groups -> {out}"
    )


if __name__ == "__main__":
    main()
