#!/usr/bin/env python3
"""Record this release's correspondence to the pack's text, once, into `data/`.

Reconciliation is the one place allowed to open another release's disc. It
compares the two originals as normalized text, and writes what it found into
the release mappings. After that a build reads only its own disc plus `data/`:
the correspondence is a fact on record, not something re-derived from another
console's disc every time a patch is built.

Two mappings, one idea. `scen` records which pack record each Saturn script
entry carries; `system` records which pack string each Saturn SYSTEM group
entry carries. Both write:

* `ranges` - runs that correspond one-for-one, which is most of the text and
  collapses to a handful of lines;
* `entries` - everything already written by hand: release-only text pointing at
  a language override, explicitly preserved service records, and the
  `replaces_ps1` annotations. These are copied through untouched, because they
  carry decisions no alignment can reproduce.

Entries with neither are left out and reported: they are the ones that still
need a decision.

    python3 -m langrisser.saturn_reconcile scen
    python3 -m langrisser.saturn_reconcile system
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from langrisser.offsetgroups import PS1, SATURN, find_groups, run_length
from langrisser.project import COMMON_FONT_MAP
from langrisser.release import add_release_args, release_from_args
from langrisser.saturn_apply import (Normalizer, expand_record_map, load_font_map_csv,
                                     load_mapping, monotone_alignment,
                                     ps1_chunk_records, proven_equal)
from langrisser.saturn_scen import local_index_entries, parse_catalog
from langrisser.saturn_system_pack import expand_group_map
from langrisser.saturn_system_pack import load_mapping as load_system_mapping


def collapse(targets: dict[int, int]) -> list[dict[str, int]]:
    """Turn per-entry correspondence into runs.

    A run is consecutive Saturn entries whose pack records are consecutive
    too, which is what the text looks like wherever the two builds agree.
    """
    runs: list[dict[str, int]] = []
    for saturn in sorted(targets):
        ps1 = targets[saturn]
        if runs:
            last = runs[-1]
            if (saturn == last["saturn"] + last["count"]
                    and ps1 == last["ps1"] + last["count"]):
                last["count"] += 1
                continue
        runs.append({"saturn": saturn, "ps1": ps1, "count": 1})
    return runs


def merge_spec(spec: dict, fresh: dict[int, int]) -> dict[str, object]:
    """Hand-written decisions first, derived runs after."""
    merged: dict[str, object] = {}
    ranges = collapse(fresh)
    if ranges:
        merged["ranges"] = ranges
    if spec.get("entries"):
        merged["entries"] = spec["entries"]
    if spec.get("ranges"):
        merged.setdefault("ranges", []).extend(spec["ranges"])  # type: ignore[union-attr]
    return merged


def report(kind: str, derived: int, kept: int, open_items: list[str]) -> None:
    undecided = len(open_items)
    print(f"{kind}: {derived} recorded from alignment, {kept} kept as written, "
          f"{undecided} still undecided")
    for line in open_items[:10]:
        print("  " + line)
    if len(open_items) > 10:
        print(f"  ... {len(open_items) - 10} more")


def write_mapping(mapping: dict, key: str, out: dict[str, dict],
                  path: Path, dry_run: bool) -> None:
    if dry_run:
        print("dry run: mapping not written")
        return
    mapping[key] = dict(sorted(out.items(), key=lambda kv: int(kv[0])))
    path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print(f"mapping -> {path}")


def reconcile_scen(args) -> None:
    release = release_from_args(args)
    mapping_path = Path(args.mapping) if args.mapping else release.scen_mapping
    mapping = load_mapping(mapping_path)
    norm = Normalizer(load_font_map_csv(COMMON_FONT_MAP),
                      load_font_map_csv(release.kanji_map))
    data = Path(args.scen).read_bytes()
    ps1 = Path(args.ps1_scen).read_bytes()

    empty = {int(x) for x in mapping.get("empty_chunks", [])}
    specs = {int(k): v for k, v in (mapping.get("chunks") or {}).items()}
    out: dict[str, dict] = {}
    derived = kept = 0
    open_items: list[str] = []

    for chunk, (start, used) in enumerate(parse_catalog(data)):
        entries = local_index_entries(data, start, used)
        if entries is None or chunk in empty:
            continue
        spec = specs.get(chunk, {})
        explicit = expand_record_map(spec, len(entries)) if spec else {}
        try:
            ps1_records = ps1_chunk_records(ps1, chunk)
        except Exception:
            ps1_records = []
        auto, _ = (monotone_alignment(entries, ps1_records, norm)
                   if ps1_records else ({}, []))

        fresh = {i: rec for i, rec in auto.items() if i not in explicit}
        merged = merge_spec(spec, fresh)
        if merged:
            out[str(chunk)] = merged
        derived += len(fresh)
        kept += len(explicit)
        missing = [i for i in range(len(entries))
                   if i not in explicit and i not in auto]
        if missing:
            open_items.append(f"chunk {chunk:03d}: {len(missing)} entries")

    report("scen", derived, kept, open_items)
    write_mapping(mapping, "chunks", out, mapping_path, args.dry_run)


def group_records(data: bytes, groups, index: int, cfg) -> list[list[int]]:
    """Token stream of every string in group `index`."""
    if index >= len(groups):
        return []
    _, table, base = groups[index]
    out: list[list[int]] = []
    for k in range(len(table)):
        off = base + table[k] * 2
        words = (table[k + 1] - table[k] - 1 if k + 1 < len(table)
                 else run_length(data, off, cfg))
        out.append(cfg.order.words(data, off, words))
    return out


def reconcile_system(args) -> None:
    release = release_from_args(args)
    mapping_path = Path(args.mapping) if args.mapping else release.system_mapping
    mapping = load_system_mapping(mapping_path)
    norm = Normalizer(load_font_map_csv(COMMON_FONT_MAP),
                      load_font_map_csv(release.kanji_map))
    data = Path(args.system).read_bytes()
    ps1 = Path(args.ps1_system).read_bytes()

    sat_groups = find_groups(data, SATURN)
    ps1_groups = find_groups(ps1, PS1)
    specs = {int(k): v for k, v in (mapping.get("groups") or {}).items()}
    out: dict[str, dict] = {}
    derived = kept = 0
    open_items: list[str] = []

    for gi in range(len(sat_groups)):
        entries = group_records(data, sat_groups, gi, SATURN)
        spec = specs.get(gi, {})
        explicit = expand_group_map(spec, len(entries)) if spec else {}
        records = group_records(ps1, ps1_groups, gi, PS1)

        # Same position, same text is the common case and proves itself; the
        # rest is aligned within the group the way the script is.
        auto: dict[int, int] = {}
        for k in range(len(entries)):
            if k in explicit:
                continue
            if k < len(records) and proven_equal(norm, entries[k], records[k]):
                auto[k] = k
        rest = [k for k in range(len(entries)) if k not in explicit and k not in auto]
        if rest and records:
            aligned, _ = monotone_alignment([entries[k] for k in rest], records, norm)
            for position, record in aligned.items():
                auto[rest[position]] = record - 1

        merged = merge_spec(spec, auto)
        if merged:
            out[str(gi)] = merged
        derived += len(auto)
        kept += len(explicit)
        missing = [k for k in range(len(entries))
                   if k not in explicit and k not in auto]
        if missing:
            open_items.append(
                f"group {gi:2d}: {len(missing)} of {len(entries)} entries "
                f"{missing[:8]}")

    report("system", derived, kept, open_items)
    write_mapping(mapping, "groups", out, mapping_path, args.dry_run)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_release_args(ap, "l5-saturn-jp")
    ap.add_argument("--mapping", default=None,
                    help="Mapping to update (default: the release manifest's).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be recorded without writing.")
    sub = ap.add_subparsers(dest="what", required=True)

    scen = sub.add_parser("scen", help="Record the script correspondence.")
    scen.add_argument("--scen", default="work/l5/build/saturn/SCEN.DAT",
                      help="This release's SCEN.DAT.")
    scen.add_argument("--ps1-scen", default="work/l5/extracted/SCEN.DAT",
                      help="Script of the release the pack is keyed to.")
    scen.set_defaults(func=reconcile_scen)

    system = sub.add_parser("system", help="Record the SYSTEM correspondence.")
    system.add_argument("--system", default="work/l5/build/saturn/SYSTEM.DAT",
                        help="This release's SYSTEM.DAT.")
    system.add_argument("--ps1-system", default="work/l5/extracted/SYSTEM.BIN",
                        help="SYSTEM of the release the pack is keyed to.")
    system.set_defaults(func=reconcile_system)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
