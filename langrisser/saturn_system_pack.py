#!/usr/bin/env python3
"""Pack the universal SYSTEM translation into the Saturn SYSTEM.DAT groups.

Saturn `SYSTEM.DAT` uses the same offset-table group model as PS1, so this
reuses the shared `langrisser.offsetgroups` model with the Saturn BE config to
rebuild each group's `[u16 offset table][strings]` in place with the translated
text.

Which pack string each Saturn entry carries is read from the release's
`system_mapping.json`, which must cover every entry of every group. That
correspondence was compared against the other release's original once, by
`langrisser.saturn_reconcile system`, and written down; packing does not
re-derive it and never opens another console's disc. Entries the pack has no
string for are named there as language-specific overrides under
`<pack>/releases/<slug>/system_strings.json`, or preserved as-is.

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
from langrisser.project import add_language_args, language_from_args
from langrisser.binfmt import BE
from langrisser.game import add_game_args, game_from_args
from langrisser.offsetgroups import (SATURN, expand_group_map, find_groups, group_key,
                                     load_system_mapping, run_length)
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
    sat_groups = find_groups(data, SATURN)
    source_by_id = {
        entry["id"]: entry
        for entry in json.loads(Path(args.system_source).read_text(encoding="utf-8"))
    } if args.system_source else {}
    default_max_grow, max_grow_overrides = load_system_layout(
        Path(args.layout) if args.layout else lang.system_layout, source_by_id)
    card_line_cells = load_card_layout(
        Path(args.card_layout) if args.card_layout else game_from_args(args).system_card_layout)
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
    mapping = load_system_mapping(mapping_path)
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
        if spec is None:
            if args.allow_unmapped:
                skipped_groups += 1
                continue
            fatal.append(f"group {gi}: no recorded correspondence; "
                         "run langrisser.saturn_reconcile system")
            continue
        seqs: list[list[int]] = []
        explicit_map = expand_group_map(spec, n)
        if len(explicit_map) != n:
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

            target = explicit_map[k]
            if isinstance(target, int):
                entry_id = group_key(gi, target)
                place(translations.get(entry_id), entry_id)
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
