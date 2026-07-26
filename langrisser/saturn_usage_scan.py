#!/usr/bin/env python3
"""Scan Saturn-side glyph slot usage for the font slot assigner.

The sacrificial-slot analysis must be based on the platform's own data: a
slot is only safe to hand to a Cyrillic tile if nothing the *Saturn* build
leaves untranslated still renders it. This scanner emits the usage facts the
assigner needs:

- `usage`: token frequency across the Saturn SCEN originals (rarity ranking
  for sacrifice order);
- `jp_visible`: tokens of entries the mapping explicitly preserves (they keep
  rendering their original glyphs, so their slots are untouchable);
- `ui_used`: tokens found in FFFF-terminated text runs inside the Saturn data
  files outside the translation pipeline (BAR/SHOP/CUR/BTLDAT/TK_SC, the
  resident code overlays, the SYSTEM.DAT tail) — decoded through the merged
  Saturn font map, so a sacrifice there costs a visibly wrong glyph until
  that text is translated.
"""

from __future__ import annotations

import argparse
import json
import struct
from collections import Counter
from pathlib import Path

from langrisser.project import COMMON_FONT_MAP
from langrisser.release import add_release_args, release_from_args
from langrisser.saturn_apply import load_font_map_csv, load_mapping
from langrisser.scen import consumes_argument
from langrisser.saturn_scen import local_index_entries, parse_catalog


def scen_usage(scen: bytes, mapping: dict) -> tuple[Counter, set[int]]:
    usage: Counter = Counter()
    jp_visible: set[int] = set()
    chunk_specs = {int(k): v for k, v in (mapping.get("chunks") or {}).items()}
    empty = {int(x) for x in mapping.get("empty_chunks", [])}
    for ci, (start, used) in enumerate(parse_catalog(scen)):
        entries = local_index_entries(scen, start, used)
        if entries is None:
            continue
        preserved = {
            int(item["saturn"])
            for item in (chunk_specs.get(ci) or {}).get("entries", [])
            if item.get("preserve")
        }
        for si, words in enumerate(entries):
            prev = None
            for w in words:
                if w < 0xE000 and not (prev is not None and consumes_argument(prev)):
                    usage[w] += 1
                    if ci in empty or si in preserved:
                        jp_visible.add(w)
                prev = w
    return usage, jp_visible


def file_runs(data: bytes, charmap: dict[int, str]) -> Counter:
    """FFFF-terminated BE token runs that decode as Saturn text."""
    particles = {tok for tok, ch in charmap.items() if ch in "のをはにがでてとしだよね"}
    used: Counter = Counter()
    ws = struct.unpack(f">{len(data) // 2}H", data[: len(data) & ~1])
    run: list[int] = []
    for w in ws:
        if w == 0xFFFF:
            printable = [x for x in run if x < 0xE000]
            # Binary structures decode "printable" through the dense merged
            # map; real Japanese sentences also carry kana with grammar
            # particles, so demand both before believing a run is text.
            kana = sum(1 for x in printable if x < 0xCA)
            if (len(printable) >= 4
                    and sum(1 for x in printable if x in charmap)
                    / len(printable) >= 0.8
                    and kana / len(printable) >= 0.25
                    and sum(1 for x in printable if x in particles) >= 2):
                used.update(printable)
            run = []
        elif w < 0xE000 or 0xF000 <= w < 0xFFFF:
            run.append(w)
        else:
            run = []
    return used


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scen", default="work/l5/build/saturn/SCEN.DAT")
    add_release_args(ap, "l5-saturn-jp")
    ap.add_argument("--mapping", default=None,
                    help="SCEN mapping JSON (default: the release manifest's)")
    ap.add_argument("--kanji-map", default=None,
                    help="Kanji slot map CSV (default: the release manifest's)")
    ap.add_argument("--files", nargs="*", default=[
        "work/l5/build/saturn/SYSTEM.DAT",
        "work/l5/build/saturn/BAR.BIN",
        "work/l5/build/saturn/SHOP.DAT",
        "work/l5/build/saturn/CUR.DAT",
        "work/l5/build/saturn/BTLDAT.BIN",
        "work/l5/build/saturn/TK_SC.BIN",
        "work/l5/build/saturn/PROG1.BIN",
        "work/l5/build/saturn/PROG2.BIN",
        "work/l5/build/saturn/A0LANG5.BIN",
    ])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    release = release_from_args(args)
    mapping_path = Path(args.mapping) if args.mapping else release.scen_mapping
    kanji_map = Path(args.kanji_map) if args.kanji_map else release.kanji_map

    charmap = load_font_map_csv(COMMON_FONT_MAP)
    charmap.update(load_font_map_csv(kanji_map))
    usage, jp_visible = scen_usage(
        Path(args.scen).read_bytes(), load_mapping(mapping_path))
    ui_used: Counter = Counter()
    for f in args.files:
        p = Path(f)
        if not p.exists():
            raise SystemExit(f"usage scan input missing: {p}")
        data = p.read_bytes()
        if p.name == "SYSTEM.DAT":
            # The glyph plane, pointer directory and text groups are all
            # owned by the translation; only the tail (texture decoder,
            # Now Loading, name-entry input) can hold untranslated runs.
            data = data[0x178F4:]
        ui_used.update(file_runs(data, charmap))
    out = {
        "usage": {str(k): v for k, v in usage.items()},
        "jp_visible": sorted(jp_visible),
        "ui_used": {str(k): v for k, v in ui_used.items()},
    }
    Path(args.out).write_text(json.dumps(out) + "\n", encoding="utf-8")
    print(f"saturn usage scan: {len(usage)} scen tokens, "
          f"{len(jp_visible)} jp-visible, {len(ui_used)} ui-run tokens -> {args.out}")


if __name__ == "__main__":
    main()
