#!/usr/bin/env python3
"""Normalize the Saturn build texts with the platform record overrides.

Platform mappings replace some PS1-derived records with Saturn-specific text
(pad buttons: `Нажмите ○` -> `Нажмите C`, `кнопка △` -> `кнопка A`,
`▢: Подробнее` -> `START: Подробнее`). The replacement itself happens inside
the SCEN/SYSTEM packers, but every earlier stage (font-slot assignment,
rewrap, the encode validators) still sees the untouched common text — and
with the `.tbl` holding no PS1 pad glyphs those stages would fail on `△`.

This step rewrites the *build copies* to match what actually ships:

- raw `<$XXXX>` glyph tokens in the common text are moved to the Saturn slot
  holding that glyph — the pack is keyed by PS1, and this release reordered
  the plane, so the PS1 number would draw an unrelated character. Release
  override text is written against this build's own slots and is left alone,
  which is why this runs before the overrides are inlined;
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

from langrisser.offsetgroups import SATURN as SATURN_CFG
from langrisser.offsetgroups import (expand_group_map, find_groups, group_key,
                                     load_system_mapping, parse_group_key)
from langrisser.project import add_language_args, language_from_args
from langrisser.release import add_release_args, release_from_args
from langrisser.saturn_apply import load_mapping as load_scen_mapping
from langrisser.scen import glyph_tag_spans, raw_glyph_slots, remap_glyph_tags
from langrisser.sceninsert import parse_dump_file

GLYPH_BYTES = 18
PLANE_SLOTS = 1835   # both fonts end at slot 1834; Saturn data follows


def glyph(plane: bytes, slot: int) -> bytes:
    return plane[slot * GLYPH_BYTES:(slot + 1) * GLYPH_BYTES]


def native_token_remap(texts: list[str], ps1: bytes,
                       saturn: bytes) -> dict[int, int]:
    """PS1 glyph tokens the common text emits raw -> this build's own slot.

    Only tokens whose two planes disagree need moving; the rest already draw
    the same glyph here. A token whose glyph this plane does not hold at all
    is fatal: silently drawing the kanji that took the slot is exactly the
    failure this step exists to prevent.
    """
    remap: dict[int, int] = {}
    for token in sorted(raw_glyph_slots(texts)):
        want = glyph(ps1, token)
        # Token 0 is the block padding word, not a glyph reference.
        if token == 0 or not want or glyph(saturn, token) == want:
            continue
        slot = next((s for s in range(1, PLANE_SLOTS)
                     if glyph(saturn, s) == want), None)
        if slot is None:
            raise SystemExit(
                f"token <${token:04X}> draws a glyph this build's font does "
                "not have; it cannot ship as a raw token here")
        remap[token] = slot
    return remap


def retoken(translation_root: Path, strings_path: Path, ps1: bytes,
            saturn: bytes) -> tuple[dict[int, int], int]:
    """Move every raw glyph token of the shipping text onto this build's slots.

    The whole pack is keyed by PS1 - its characters reach the encoder through
    a PS1-derived table, and its raw tokens are PS1 slot numbers - so release
    overrides need this exactly as much as the common text does.
    """
    dumps = sorted(translation_root.glob("**/chunk_*.txt"))
    maps = [strings_path] + sorted(
        translation_root.glob("releases/*/system_strings.json"))
    text_of = {fp: fp.read_text(encoding="utf-8") for fp in dumps}
    json_of = {fp: json.loads(fp.read_text(encoding="utf-8")) for fp in maps}
    remap = native_token_remap(
        [line for text in text_of.values() for line in text.splitlines()]
        + [value for strings in json_of.values() for value in strings.values()],
        ps1, saturn)
    if not remap:
        return remap, 0

    def moved_in(text: str) -> int:
        return sum(1 for _, _, slot in glyph_tag_spans(text) if slot in remap)

    moved = 0
    for fp, text in text_of.items():
        out = remap_glyph_tags(text, remap)
        if out != text:
            moved += moved_in(text)
            fp.write_text(out, encoding="utf-8")
    for fp, strings in json_of.items():
        changed = {key: remap_glyph_tags(value, remap)
                   for key, value in strings.items()}
        if changed != strings:
            moved += sum(moved_in(value) for value in strings.values())
            fp.write_text(
                json.dumps(changed, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
    return remap, moved


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
                  saturn_orig: bytes) -> tuple[int, int]:
    """Replace or drop pack strings shadowed by release entries.

    A shadowed UI line whose release overlay carries this build's text (same
    index) is *replaced*, so the downstream validators check exactly what
    ships; only entries with no overlay counterpart are removed.

    Which pack strings this build actually shows is the recorded mapping: an
    id no group entry points at is not on this build, whatever it is on
    another one.
    """
    translations = json.loads(strings_path.read_text(encoding="utf-8"))
    sat_groups = find_groups(saturn_orig, SATURN_CFG)
    removed = replaced = 0
    for group_id, spec in (mapping.get("groups") or {}).items():
        gi = int(group_id)
        targets = expand_group_map(spec, len(sat_groups[gi][1]))
        used = {t for t in targets.values() if isinstance(t, int)}
        used |= {int(str(t["ps1_id"]).rsplit(":", 1)[1])
                 for t in targets.values()
                 if isinstance(t, dict) and "ps1_id" in t}
        shadowed = sorted(
            index for group, index in
            filter(None, (parse_group_key(key) for key in translations))
            if group == gi and index not in used
        )
        for k in shadowed:
            key = group_key(gi, k)
            platform_text = overlay.get(key)
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
    ap.add_argument("--ps1-system", default="work/l5/extracted/SYSTEM.BIN",
                    help="PS1 SYSTEM.BIN: the plane the pack's raw tokens "
                         "were written against.")
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

    saturn_orig = Path(args.saturn_orig).read_bytes()
    replaced = override_scen(
        Path(args.translation_root),
        lang.override_script_dir(release.code),
        load_scen_mapping(scen_mapping),
    )
    overlay_path = lang.override_system_strings(release.code)
    overlay = (json.loads(overlay_path.read_text(encoding="utf-8"))
               if overlay_path.exists() else {})
    sys_replaced, removed = shadow_system(
        Path(args.strings), overlay, load_system_mapping(system_mapping),
        saturn_orig,
    )
    # Last, so it covers the platform text just inlined above too.
    remap, moved = retoken(
        Path(args.translation_root), Path(args.strings),
        Path(args.ps1_system).read_bytes(), saturn_orig,
    )
    print(f"platform text overrides: {replaced} SCEN records replaced, "
          f"{sys_replaced} SYSTEM strings replaced, "
          f"{removed} shadowed SYSTEM strings removed")
    if remap:
        print("  raw glyph tokens moved onto this build's slots: "
              + " ".join(f"{old:#06x}->{new:#06x}" for old, new in
                         sorted(remap.items()))
              + f" ({moved} occurrences)")


if __name__ == "__main__":
    main()
