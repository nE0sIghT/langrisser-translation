#!/usr/bin/env python3
"""Pack the universal SYSTEM translation into the Saturn SYSTEM.DAT groups.

Saturn `SYSTEM.DAT` uses the same offset-table group model as PS1, and its 16
groups correspond 1:1 to the PS1 `SYSTEM.BIN` groups in order (14/16 with
identical entry counts). This reuses the shared `langrisser.offsetgroups` model with
the Saturn BE config and the PS1 codec to rebuild each group's
`[u16 offset table][strings]` in place with the translated text. Same-count
groups use direct PS1 index mapping; count-different or reordered groups must be
described by the release's `system_mapping.json` and any Saturn-only
target strings under `<pack>/platforms/saturn/system_strings.json`.

Fixed-size per group: the group stays at its base and within its original byte
budget, so nothing that points at it moves. A group whose rebuild would exceed
the budget is a strict build error unless `--allow-unmapped` is used for
diagnostics.
See docs/SATURN_DISC_FORMAT.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from langrisser.platform import add_platform_args, platform_from_args
from langrisser.release import add_release_args, release_from_args
from langrisser.project import COMMON_FONT_MAP, add_language_args, language_from_args
from langrisser.binfmt import BE
from langrisser.game import add_game_args, game_from_args
from langrisser.offsetgroups import PS1, SATURN, find_groups, group_key, run_length
from langrisser.saturn_apply import Normalizer, load_font_map_csv, proven_equal
from langrisser.scen import Codec, load_charmap_tbl
from langrisser.system_pack import (load_card_layout, load_system_layout,
                               reserve_leading_cells)

FFFF = 0xFFFF


def group_end_offset(data: bytes, table: list[int], base: int, cfg) -> int:
    last_off = base + table[-1] * 2
    return last_off + (run_length(data, last_off, cfg) + 1) * 2


def build_group_blob(seqs: list[list[int]]) -> list[int]:
    """Rebuild [u16 offset table][FFFF-terminated strings] as a word list.

    `offset[k]` is the word offset of string `k` from the string base (which is
    `n` words after the table start); string `k` is its words plus an `FFFF`.
    """
    offsets: list[int] = []
    strings: list[int] = []
    pos = 0
    for seq in seqs:
        offsets.append(pos)
        strings.extend(seq)
        strings.append(FFFF)
        pos += len(seq) + 1
    return offsets + strings


def load_mapping(path: Path | None) -> dict:
    if path is None:
        return {"groups": {}}
    if not path.exists():
        raise SystemExit(f"Saturn SYSTEM mapping not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: SYSTEM mapping must be an object")
    data.setdefault("groups", {})
    return data


def expand_group_map(spec: dict, entry_count: int) -> dict[int, object]:
    """Return `{saturn_index: ps1_index_or_platform_id}`."""
    out: dict[int, object] = {}
    for item in spec.get("ranges", []):
        saturn = int(item["saturn"])
        count = int(item["count"])
        if "ps1" in item:
            ps1 = int(item["ps1"])
            for off in range(count):
                out[saturn + off] = ps1 + off
        elif "platform" in item:
            platform = str(item["platform"])
            if count != 1:
                raise SystemExit(
                    "SYSTEM range platform mappings must be explicit entries; "
                    f"got {item}"
                )
            out[saturn] = {"platform": platform}
        elif item.get("preserve"):
            for off in range(count):
                out[saturn + off] = {"preserve": True}
        else:
            raise SystemExit(f"SYSTEM range mapping needs ps1/platform/preserve: {item}")
    for item in spec.get("entries", []):
        saturn = int(item["saturn"])
        if "ps1" in item:
            out[saturn] = int(item["ps1"])
        elif "ps1_id" in item:
            out[saturn] = {"ps1_id": str(item["ps1_id"])}
        elif "platform" in item:
            out[saturn] = {"platform": str(item["platform"])}
        elif item.get("preserve"):
            out[saturn] = {"preserve": True}
        else:
            raise SystemExit(f"SYSTEM entry mapping needs ps1/ps1_id/platform/preserve: {item}")
    bad = [idx for idx in out if idx < 0 or idx >= entry_count]
    if bad:
        raise SystemExit(f"SYSTEM mapping has out-of-range Saturn entries: {bad[:5]}")
    return out


def encoded_from_text(codec: Codec, text: str | None, orig: list[int],
                      *, required_id: str | None = None) -> list[int]:
    if text is None or text == "":
        if required_id:
            raise SystemExit(f"missing platform SYSTEM translation: {required_id}")
        return orig
    if text == "{BLANK}":
        return []
    return codec.encode(text.rstrip())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    add_game_args(ap)
    add_platform_args(ap, "saturn")
    add_release_args(ap, "l5-saturn-jp")
    ap.add_argument("--system-in", default=None,
                    help="Input SYSTEM.DAT with the target font applied.")
    ap.add_argument("--system-out", default=None,
                    help="Output translated SYSTEM.DAT.")
    ap.add_argument("--ps1-system", default="work/l5/extracted/SYSTEM.BIN")
    ap.add_argument("--strings", default=None,
                    help="Resolved common SYSTEM strings JSON.")
    ap.add_argument("--release-strings", default=None,
                    help="Language-specific release SYSTEM overlay JSON.")
    ap.add_argument("--mapping", default=None,
                    help="SYSTEM mapping JSON (default: the release manifest's)")
    ap.add_argument("--tbl", default=None,
                    help="Saturn charmap .tbl for the selected language.")
    ap.add_argument("--layout", default=None,
                    help="Per-language SYSTEM growth limits (default: the pack's).")
    ap.add_argument("--card-layout", default=None,
                    help="Multi-line card groups (default: the game manifest's).")
    ap.add_argument("--system-source", default=None,
                    help="Generated SYSTEM source dump (validates layout ids).")
    ap.add_argument("--allow-unmapped", action="store_true",
                    help="Diagnostic mode: preserve unmapped or over-budget groups.")
    args = ap.parse_args()

    lang = language_from_args(args)
    platform = platform_from_args(args)
    release = release_from_args(args, platform=platform.code)
    system_in = (
        Path(args.system_in) if args.system_in
        else Path(f"work/l5/build/saturn/SYSTEM.DAT.{lang.suffix}.font")
    )
    system_out = (
        Path(args.system_out) if args.system_out
        else Path(f"work/l5/build/saturn/SYSTEM.{lang.suffix}.DAT")
    )
    strings_path = (
        Path(args.strings) if args.strings
        else Path(f"work/l5/build/system_strings.{lang.suffix}.json")
    )
    tbl = (
        Path(args.tbl) if args.tbl
        else Path(f"work/l5/build/saturn/lang5_{lang.suffix}.saturn.tbl")
    )
    codec = Codec(load_charmap_tbl(tbl))
    data = bytearray(system_in.read_bytes())
    ps1_data = Path(args.ps1_system).read_bytes()
    sat_groups = find_groups(data, SATURN)
    ps1_groups = find_groups(ps1_data, PS1)
    source_by_id = {
        entry["id"]: entry
        for entry in json.loads(Path(args.system_source).read_text(encoding="utf-8"))
    } if args.system_source else {}
    default_max_grow, max_grow_overrides = load_system_layout(
        Path(args.layout) if args.layout else lang.system_layout, source_by_id)
    card_line_cells = load_card_layout(
        Path(args.card_layout) if args.card_layout else game_from_args(args).system_card_layout)
    norm = Normalizer(load_font_map_csv(COMMON_FONT_MAP),
                      load_font_map_csv(release.kanji_map))

    def ps1_words(gi: int, k: int) -> list[int] | None:
        if gi >= len(ps1_groups):
            return None
        _, table, base = ps1_groups[gi]
        if not 0 <= k < len(table):
            return None
        off = base + table[k] * 2
        return PS1.order.words(ps1_data, off, run_length(ps1_data, off, PS1))
    translations = json.loads(strings_path.read_text(encoding="utf-8"))
    platform_strings_path = (
        Path(args.release_strings) if args.release_strings
        else lang.override_system_strings(release.code)
    )
    platform_translations = (
        json.loads(platform_strings_path.read_text(encoding="utf-8"))
        if platform_strings_path.exists() else {}
    )
    mapping_path = Path(args.mapping) if args.mapping else release.system_mapping
    mapping = load_mapping(mapping_path)
    group_specs = {int(k): v for k, v in (mapping.get("groups") or {}).items()}

    changed = 0
    skipped_groups = 0
    fatal: list[str] = []
    for gi, (table_off, table, base) in enumerate(sat_groups):
        n = len(table)
        if base != table_off + n * 2:
            if not args.allow_unmapped:
                fatal.append(f"group {gi}: preamble between offset table and strings")
            continue  # group keeps a preamble between table and strings: skip
        group_end = group_end_offset(data, table, base, SATURN)
        budget = (group_end - table_off) // 2   # offset table + strings, in words
        spec = group_specs.get(gi)
        ps1_table_off = ps1_groups[gi][0] if gi < len(ps1_groups) else None
        if spec is None and (gi >= len(ps1_groups) or len(ps1_groups[gi][1]) != n):
            if args.allow_unmapped:
                skipped_groups += 1
                continue
            ps_count = len(ps1_groups[gi][1]) if gi < len(ps1_groups) else "missing"
            fatal.append(
                f"group {gi}: Saturn count {n}, PS1 count {ps_count}, "
                "no platform mapping"
            )
            continue
        seqs: list[list[int]] = []
        explicit_map = expand_group_map(spec, n) if spec is not None else None
        if explicit_map is not None and len(explicit_map) != n:
            missing = [idx for idx in range(n) if idx not in explicit_map]
            fatal.append(f"group {gi}: mapping does not cover entries {missing[:12]}")
            continue
        for k in range(n):
            off = base + table[k] * 2
            orig_len = table[k + 1] - table[k] - 1 if k + 1 < n else run_length(data, off, SATURN)
            orig = SATURN.order.words(data, off, orig_len)

            def place(text: str | None, entry_id: str | None,
                      *, required_id: str | None = None) -> None:
                """Encode `text` for entry `k` the way the PS1 packer does.

                The original's leading `0x0000` cells are structural indent
                (slot numbers, card gutters), so they are re-reserved rather
                than translated away; the result is capped like PS1 —
                card-group lines at their cell width, everything else at
                `orig_len + max_grow`.
                """
                seq = encoded_from_text(codec, text, orig, required_id=required_id)
                if seq is orig:
                    seqs.append(seq)
                    return
                seq = reserve_leading_cells(orig) + seq
                max_grow = (max_grow_overrides.get(entry_id, default_max_grow)
                            if entry_id else default_max_grow)
                cap = card_line_cells.get(gi, orig_len + max_grow)
                if len(seq) > cap:
                    fatal.append(
                        f"group {gi} entry {k}: line {len(seq)}>{cap} "
                        f"(max-grow {max_grow}) :: {text!r}")
                    seqs.append(orig)
                    return
                seqs.append(seq)

            if explicit_map is None:
                entry_id = group_key(gi, k)
                text = translations.get(entry_id)
                if text and not proven_equal(norm, orig, ps1_words(gi, k) or []):
                    fatal.append(
                        f"group {gi} entry {k}: Saturn original differs from "
                        "the PS1 record (needs a platform mapping)")
                place(text, entry_id)
                continue
            target = explicit_map[k]
            if isinstance(target, int):
                if ps1_table_off is None:
                    fatal.append(f"group {gi}: PS1 group missing for mapped entry {k}")
                    seqs.append(orig)
                    continue
                entry_id = group_key(gi, target)
                text = translations.get(entry_id)
                if text and not proven_equal(norm, orig, ps1_words(gi, target) or []):
                    fatal.append(
                        f"group {gi} entry {k}: mapped PS1 record {target} "
                        "differs from the Saturn original (needs a platform "
                        "mapping)")
                place(text, entry_id)
            elif "ps1_id" in target:
                ps1_id = str(target["ps1_id"])
                place(translations.get(ps1_id), ps1_id)
            elif "platform" in target:
                platform_id = str(target["platform"])
                place(platform_translations.get(platform_id), None,
                      required_id=platform_id)
            else:
                seqs.append(orig)
        blob = build_group_blob(seqs)
        if len(blob) > budget:
            skipped_groups += 1
            msg = f"group {gi}: rebuilt {len(blob)} words exceeds budget {budget}"
            if args.allow_unmapped:
                continue
            fatal.append(msg)
            continue
        blob += [FFFF] * (budget - len(blob))  # pad the fixed group span
        for i, word in enumerate(blob):
            data[table_off + i * 2:table_off + i * 2 + 2] = BE.pack_u16(word)
        changed += 1

    if fatal:
        lines = "\n".join(f"  - {msg}" for msg in fatal[:40])
        more = f"\n  ... +{len(fatal) - 40} more" if len(fatal) > 40 else ""
        raise SystemExit(f"Saturn SYSTEM mapping incomplete:\n{lines}{more}")

    system_out.parent.mkdir(parents=True, exist_ok=True)
    system_out.write_bytes(bytes(data))
    print(f"packed {changed}/{len(sat_groups)} SYSTEM groups "
          f"(skipped-over-budget={skipped_groups}) -> {system_out}")


if __name__ == "__main__":
    main()
