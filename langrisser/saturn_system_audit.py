#!/usr/bin/env python3
"""Audit the Saturn<->PS1 SYSTEM correspondence; PS1 is a reference only.

The SCEN side already proves every correspondence by comparing the JP
originals as normalized text (see `saturn_scen_audit.py`). SYSTEM was mapped
by index with hand-written platform entries, which silently let a Saturn
string carry its own translation even when its original is identical to a PS1
record that already has one — the two consoles then diverge for no reason.

For every group this runs the same order-preserving alignment over the
normalized originals and reports:

- `recover`: entries declared platform-only whose original provably equals a
  PS1 record (they should map to `ps1` and inherit the shared translation);
- `platform`: entries with no PS1 counterpart (genuinely Saturn-edited);
- `shifted`: index-mapped entries the alignment sends to a different PS1
  record (an index mapping that only works by luck).

With `--write-mapping` the group specs are rewritten to that proven form and
the now-unused overlay strings are dropped from the language packs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from langrisser.offsetgroups import PS1, SATURN, find_groups, run_length
from langrisser.project import COMMON_FONT_MAP
from langrisser.release import add_release_args, release_from_args
from langrisser.saturn_apply import (Normalizer, load_font_map_csv,
                                monotone_alignment)
from langrisser.game import add_game_args, game_from_args
from langrisser.saturn_system_pack import expand_group_map, load_mapping


def group_entries(data: bytes, group, cfg) -> list[list[int]]:
    _, table, base = group
    out = []
    for k in range(len(table)):
        off = base + table[k] * 2
        out.append(cfg.order.words(data, off, run_length(data, off, cfg)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--system", default="work/l5/build/saturn/SYSTEM.DAT")
    ap.add_argument("--ps1-system", default="work/l5/extracted/SYSTEM.BIN")
    add_release_args(ap, "l5-saturn-jp")
    ap.add_argument("--mapping", default=None,
                    help="SYSTEM mapping JSON (default: the release manifest's)")
    ap.add_argument("--kanji-map", default=None,
                    help="Kanji slot map CSV (default: the release manifest's)")
    add_game_args(ap)
    ap.add_argument("--lang-root", default=None,
                    help="Pack root (default: the game manifest's lang_root).")
    ap.add_argument("--langs", nargs="*", default=["ru", "en"])
    ap.add_argument("--write-mapping", action="store_true",
                    help="Rewrite the group specs to the proven form.")
    args = ap.parse_args()

    release = release_from_args(args)
    mapping_path = Path(args.mapping) if args.mapping else release.system_mapping
    kanji_map = Path(args.kanji_map) if args.kanji_map else release.kanji_map

    norm = Normalizer(load_font_map_csv(COMMON_FONT_MAP),
                      load_font_map_csv(kanji_map))
    sat = Path(args.system).read_bytes()
    ps1 = Path(args.ps1_system).read_bytes()
    sat_groups = find_groups(sat, SATURN)
    ps1_groups = find_groups(ps1, PS1)
    mapping = load_mapping(mapping_path)

    new_groups: dict[str, dict] = {}
    stats = {"recover": 0, "platform": 0, "shifted": 0, "space_override": 0}
    for gi, sat_group in enumerate(sat_groups):
        ps1_group = ps1_groups[gi]
        sat_entries = group_entries(sat, sat_group, SATURN)
        ps1_entries = group_entries(ps1, ps1_group, PS1)
        proven, unmatched = monotone_alignment(sat_entries, ps1_entries, norm)
        spec = (mapping.get("groups") or {}).get(str(gi))
        old = expand_group_map(spec, len(sat_entries)) if spec else {}

        # The packer needs full coverage, so every entry gets a target: the
        # proven PS1 record, or the platform/preserve declaration it had.
        targets: list[object] = []
        for k in range(len(sat_entries)):
            if k in proven:
                ps1_index = proven[k] - 1
                was = old.get(k, k)
                if isinstance(was, dict) and was.get("space_override"):
                    # Same original, but the Saturn group budget cannot hold
                    # the shared translation: the short form stays platform.
                    stats["space_override"] += 1
                    targets.append(dict(was))
                    continue
                if isinstance(was, dict) and "platform" in was:
                    stats["recover"] += 1
                elif isinstance(was, int) and was != ps1_index:
                    stats["shifted"] += 1
                targets.append(ps1_index)
            else:
                stats["platform"] += 1
                keep = old.get(k)
                targets.append(
                    dict(keep) if isinstance(keep, dict)
                    and ("platform" in keep or "preserve" in keep)
                    else {"preserve": True, "pending_review": True})

        ranges: list[dict] = []
        entries: list[dict] = []
        k = 0
        while k < len(targets):
            if isinstance(targets[k], int):
                run = 1
                while (k + run < len(targets)
                       and isinstance(targets[k + run], int)
                       and targets[k + run] == targets[k] + run):
                    run += 1
                if run > 1:
                    ranges.append({"saturn": k, "ps1": targets[k], "count": run})
                else:
                    entries.append({"saturn": k, "ps1": targets[k]})
                k += run
                continue
            entries.append({"saturn": k, **targets[k]})  # type: ignore[dict-item]
            k += 1
        spec_out: dict = {}
        if ranges:
            spec_out["ranges"] = ranges
        if entries:
            spec_out["entries"] = entries
        # A plain identity mapping needs no spec at all.
        if not (len(ranges) == 1 and not entries
                and ranges[0] == {"saturn": 0, "ps1": 0, "count": len(sat_entries)}):
            new_groups[str(gi)] = spec_out

    print(f"SYSTEM audit: {stats['recover']} platform entries recovered to PS1 "
          f"translations, {stats['shifted']} index mappings corrected, "
          f"{stats['platform']} genuinely Saturn-only entries, "
          f"{stats['space_override']} kept short for the group budget")

    if not args.write_mapping:
        return
    mapping["groups"] = new_groups
    mapping_path.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    used = {
        str(item["platform"])
        for spec in new_groups.values()
        for item in spec["entries"] if "platform" in item
    }
    lang_root = Path(args.lang_root) if args.lang_root else game_from_args(args).lang_root
    for lang in args.langs:
        path = (lang_root / lang / "releases" / release.code
                / "system_strings.json")
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        pruned = {k: v for k, v in data.items() if k in used}
        path.write_text(json.dumps(dict(sorted(pruned.items())),
                                   ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        print(f"  {lang}: overlay {len(data)} -> {len(pruned)} strings")
    print(f"mapping rewritten -> {mapping_path}")


if __name__ == "__main__":
    main()
