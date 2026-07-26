#!/usr/bin/env python3
"""Record the Saturn->pack record correspondence once, into `data/`.

Reconciliation is the one place allowed to open another release's disc. It
compares the two originals as normalized text, and writes what it found into
the release mapping. After that a build reads only its own disc plus `data/`:
the correspondence is a fact on record, not something re-derived from the PS1
disc every time a patch is built.

What it writes per chunk:

* `ranges` - the runs of Saturn entries that correspond to a run of pack
  records, which is most of the script and collapses to a handful of lines;
* `entries` - everything already written by hand: Saturn-only records pointing
  at a release override, explicitly preserved service records, and the
  `replaces_ps1` annotations. These are copied through untouched, because they
  carry decisions no alignment can reproduce.

Saturn entries with neither are left out and reported: they are the ones that
still need a decision.

    python3 -m langrisser.saturn_reconcile \\
        --scen work/l5/build/saturn/SCEN.DAT --ps1-scen work/l5/extracted/SCEN.DAT
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from langrisser.project import COMMON_FONT_MAP
from langrisser.release import add_release_args, release_from_args
from langrisser.saturn_apply import (Normalizer, expand_record_map, load_font_map_csv,
                                     load_mapping, monotone_alignment,
                                     ps1_chunk_records)
from langrisser.saturn_scen import local_index_entries, parse_catalog


def collapse(targets: dict[int, int]) -> list[dict[str, int]]:
    """Turn per-entry correspondence into runs.

    A run is consecutive Saturn entries whose pack records are consecutive
    too, which is what the script looks like wherever the two builds agree.
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_release_args(ap, "l5-saturn-jp")
    ap.add_argument("--scen", default="work/l5/build/saturn/SCEN.DAT",
                    help="This release's SCEN.DAT.")
    ap.add_argument("--ps1-scen", default="work/l5/extracted/SCEN.DAT",
                    help="Script of the release the pack is keyed to.")
    ap.add_argument("--mapping", default=None,
                    help="Mapping to update (default: the release manifest's).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be recorded without writing.")
    args = ap.parse_args()

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
    derived = kept = undecided = 0
    open_chunks: list[str] = []

    for chunk, (start, used) in enumerate(parse_catalog(data)):
        entries = local_index_entries(data, start, used)
        if entries is None or chunk in empty:
            continue
        spec = specs.get(chunk, {})
        # Hand-written decisions win and are copied through verbatim.
        explicit = expand_record_map(spec, len(entries)) if spec else {}
        try:
            ps1_records = ps1_chunk_records(ps1, chunk)
        except Exception:
            ps1_records = []
        auto, _ = (monotone_alignment(entries, ps1_records, norm)
                   if ps1_records else ({}, []))

        fresh = {i: rec for i, rec in auto.items() if i not in explicit}
        ranges = collapse(fresh)
        new_spec: dict[str, object] = {}
        if ranges:
            new_spec["ranges"] = ranges
        if spec.get("entries"):
            new_spec["entries"] = spec["entries"]
        if spec.get("ranges"):
            new_spec.setdefault("ranges", []).extend(spec["ranges"])
        if new_spec:
            out[str(chunk)] = new_spec
        derived += len(fresh)
        kept += len(explicit)
        missing = [i for i in range(len(entries))
                   if i not in explicit and i not in auto]
        undecided += len(missing)
        if missing:
            open_chunks.append(f"chunk {chunk:03d}: {len(missing)} entries")

    print(f"reconciled: {derived} recorded from alignment, {kept} kept as "
          f"written, {undecided} still undecided")
    for line in open_chunks[:10]:
        print("  " + line)
    if undecided and len(open_chunks) > 10:
        print(f"  ... {len(open_chunks) - 10} more chunks")

    if args.dry_run:
        print("dry run: mapping not written")
        return
    mapping["chunks"] = dict(sorted(out.items(), key=lambda kv: int(kv[0])))
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    print(f"mapping -> {mapping_path}")


if __name__ == "__main__":
    main()
