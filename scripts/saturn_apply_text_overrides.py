#!/usr/bin/env python3
"""Normalize the Saturn build texts with the platform record overrides.

Platform mappings replace some PS1-derived records with Saturn-specific text
(pad buttons: `Нажмите ○` -> `Нажмите C`, `кнопка △` -> `кнопка A`,
`▢: Подробнее` -> `START: Подробнее`). The replacement itself happens inside
the SCEN/SYSTEM packers, but every earlier stage (font-slot assignment,
rewrap, the encode validators) still sees the untouched common text — and
with the `.tbl` holding no PS1 pad glyphs those stages would fail on `△`.

This step rewrites the *build copies* to match what actually ships:

- in the build translation root, each common SCEN record shadowed by a
  platform record (per `scen_mapping.json`) gets the platform text;
- in the resolved SYSTEM strings JSON, each PS1 entry shadowed by a platform
  entry (per `system_mapping.json`) is deleted — the packer takes the
  platform overlay text instead.

The language pack itself is never modified.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from lang5_offsetgroups import PS1 as PS1_CFG
from lang5_offsetgroups import SATURN as SATURN_CFG
from lang5_offsetgroups import find_groups
from lang5_project import add_language_args, language_from_args
from lang5_release import add_release_args, release_from_args
from lang5_saturn_apply import load_mapping as load_scen_mapping
from lang5_saturn_system_pack import expand_group_map, load_mapping as load_system_mapping
from lang5_sceninsert import parse_dump_file


def ps1_record_for(spec: dict, item: dict) -> int | None:
    """The PS1 record a platform entry replaces.

    Preferred source is the entry's own `replaces_ps1` annotation (a platform
    record replaces a PS1-only record, so no automatic alignment can name it);
    legacy range-based specs are still honoured.
    """
    if "replaces_ps1" in item:
        return int(item["replaces_ps1"])
    saturn_index = int(item["saturn"])
    for range_item in spec.get("ranges", []):
        saturn, count = int(range_item["saturn"]), int(range_item["count"])
        if "ps1" in range_item and saturn <= saturn_index < saturn + count:
            return int(range_item["ps1"]) + (saturn_index - saturn)
    return None


def override_scen(translation_root: Path, platform_scen: Path, mapping: dict) -> int:
    replaced = 0
    for chunk_key, spec in (mapping.get("chunks") or {}).items():
        chunk_index = int(chunk_key)
        platform_file = platform_scen / f"chunk_{chunk_index:03d}.txt"
        entries = [item for item in spec.get("entries", []) if "platform" in item]
        if not entries:
            continue
        platform_records = parse_dump_file(platform_file) if platform_file.exists() else {}
        targets: dict[int, str] = {}
        for item in entries:
            ps1_idx = ps1_record_for(spec, item)
            if ps1_idx is None:
                continue
            pidx = int(item["platform"])
            if pidx not in platform_records:
                raise SystemExit(
                    f"chunk {chunk_index:03d}: platform record {pidx} missing "
                    f"in {platform_file}")
            targets[ps1_idx] = platform_records[pidx]
        if not targets:
            continue
        for fp in translation_root.glob(f"*/chunk_{chunk_index:03d}.txt"):
            lines = fp.read_text(encoding="utf-8").splitlines()
            out: list[str] = []
            for line in lines:
                m = re.match(r"(\d+)\t(.*)$", line)
                if m and int(m.group(1)) in targets:
                    idx = int(m.group(1))
                    text = targets[idx]
                    # The copy stays keyed to the PS1 records, so Saturn-only
                    # speaker prefixes would trip the PS1 control-tag
                    # validator; the full platform record still ships via the
                    # apply. Drop the prefix here when PS1 has none.
                    if not m.group(2).startswith("<$FB00>"):
                        text = re.sub(r"^(<\$FB00><\$[0-9A-Fa-f]{4}>)+", "", text)
                    out.append(f"{idx}\t{text}")
                    replaced += 1
                else:
                    out.append(line)
            fp.write_text("\n".join(out) + "\n", encoding="utf-8")
    return replaced


def shadow_system(strings_path: Path, overlay: dict, mapping: dict,
                  saturn_orig: bytes, ps1_system: bytes) -> tuple[int, int]:
    """Replace or drop PS1 strings shadowed by platform entries.

    A shadowed UI line whose platform overlay carries the Saturn text (same
    index) is *replaced*, so the downstream validators check exactly what
    ships; only entries with no overlay counterpart are removed.
    """
    translations = json.loads(strings_path.read_text(encoding="utf-8"))
    sat_groups = find_groups(saturn_orig, SATURN_CFG)
    ps1_groups = find_groups(ps1_system, PS1_CFG)
    removed = replaced = 0
    for group_key, spec in (mapping.get("groups") or {}).items():
        gi = int(group_key)
        n = len(sat_groups[gi][1])
        sat_table_off = sat_groups[gi][0]
        ps1_table_off = ps1_groups[gi][0]
        targets = expand_group_map(spec, n)
        used_ps1 = {t for t in targets.values() if isinstance(t, int)}
        used_ps1 |= {int(str(t["ps1_id"]).rsplit(":", 1)[1])
                     for t in targets.values()
                     if isinstance(t, dict) and "ps1_id" in t}
        for k in range(len(ps1_groups[gi][1])):
            if k in used_ps1:
                continue
            key = f"table:{ps1_table_off:05X}:{k}"
            if key not in translations:
                continue
            platform_text = overlay.get(f"table:{sat_table_off:05X}:{k}")
            if platform_text is not None:
                translations[key] = platform_text
                replaced += 1
            else:
                translations.pop(key)
                removed += 1
    strings_path.write_text(
        json.dumps(translations, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return replaced, removed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--translation-root", required=True,
                    help="Build translation copy, rewritten in place.")
    ap.add_argument("--strings", required=True,
                    help="Resolved common SYSTEM strings JSON, rewritten in place.")
    ap.add_argument("--saturn-orig", required=True)
    ap.add_argument("--ps1-system", default="work/extracted/SYSTEM.BIN")
    add_release_args(ap, "l5-saturn-jp")
    ap.add_argument("--scen-mapping", default=None,
                    help="SCEN mapping JSON (default: the release manifest's)")
    ap.add_argument("--system-mapping", default=None,
                    help="SYSTEM mapping JSON (default: the release manifest's)")
    args = ap.parse_args()
    lang = language_from_args(args)
    release = release_from_args(args)
    scen_mapping = (Path(args.scen_mapping) if args.scen_mapping
                    else release.scen_mapping)
    system_mapping = (Path(args.system_mapping) if args.system_mapping
                      else release.system_mapping)

    replaced = override_scen(
        Path(args.translation_root),
        lang.root / "platforms" / "saturn" / "SCEN",
        load_scen_mapping(scen_mapping),
    )
    overlay_path = lang.root / "platforms" / "saturn" / "system_strings.json"
    overlay = (json.loads(overlay_path.read_text(encoding="utf-8"))
               if overlay_path.exists() else {})
    sys_replaced, removed = shadow_system(
        Path(args.strings),
        overlay,
        load_system_mapping(system_mapping),
        Path(args.saturn_orig).read_bytes(),
        Path(args.ps1_system).read_bytes(),
    )
    print(f"platform text overrides: {replaced} SCEN records replaced, "
          f"{sys_replaced} SYSTEM strings replaced, "
          f"{removed} shadowed SYSTEM strings removed")


if __name__ == "__main__":
    main()
