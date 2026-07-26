#!/usr/bin/env python3
"""Pack target-language SYSTEM.BIN overlays into their offset-table groups.

This is the inverse of `langrisser.system_dump.py`. For each group it rebuilds the
`[u16 offset table][strings]` layout from a generated source dump under work/
and a durable target-only overlay in the language pack. Because the table is
regenerated, a translated string is no longer bound to the original string's
byte length - only to the group's total size (the group stays at its fixed base
so nothing that points at it has to move).

Per-string the limit is the on-screen line width, not the data: each string is
one display line. The language pack's system_layout.json sets a conservative
default growth limit and explicit stable-id exceptions for strings verified to
need more room. Strings left untranslated keep their original bytes;
`text == "{BLANK}"` clears the line.

See docs/SYSTEM_BIN_FORMAT.md.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

from langrisser.game import add_game_args
from langrisser.release import add_release_args, release_from_args
from langrisser.offsetgroups import PS1, GroupConfig
from langrisser.project import add_language_args, language_from_args
from langrisser.scen import Codec, load_charmap_tbl
from langrisser.system_dump import find_groups, run_length

FFFF = 0xFFFF


def load_system_layout(path: Path, source_by_id: dict[str, dict]) -> tuple[int, dict[str, int]]:
    """Load and validate per-language SYSTEM line-growth limits."""
    if not path.exists():
        raise SystemExit(f"SYSTEM layout not found: {path}")
    layout = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(layout, dict):
        raise SystemExit(f"SYSTEM layout must be an object: {path}")
    unknown_fields = set(layout) - {"default_max_grow", "overrides"}
    if unknown_fields:
        raise SystemExit(f"{path}: unknown fields: {sorted(unknown_fields)}")

    default = layout.get("default_max_grow")
    overrides = layout.get("overrides", {})
    if isinstance(default, bool) or not isinstance(default, int) or default < 0:
        raise SystemExit(f"{path}: default_max_grow must be a non-negative integer")
    if not isinstance(overrides, dict):
        raise SystemExit(f"{path}: overrides must be an object")

    clean: dict[str, int] = {}
    for entry_id, value in overrides.items():
        if entry_id not in source_by_id:
            raise SystemExit(f"{path}: unknown SYSTEM id in overrides: {entry_id}")
        if source_by_id[entry_id]["group"] == -1:
            raise SystemExit(f"{path}: loose SYSTEM string cannot grow: {entry_id}")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SystemExit(
                f"{path}: override for {entry_id} must be a non-negative integer"
            )
        clean[entry_id] = value
    return default, clean


def load_card_layout(path: Path) -> dict[int, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    groups = data.get("groups", {})
    if not isinstance(groups, dict):
        raise SystemExit(f"{path}: groups must be an object")
    out: dict[int, int] = {}
    for table, spec in groups.items():
        if not isinstance(spec, dict) or "line_cells" not in spec:
            raise SystemExit(f"{path}: invalid card layout for {table}")
        out[int(table, 16)] = int(spec["line_cells"])
    return out


def reserve_leading_cells(orig: list[int]) -> list[int]:
    """Leading 0x0000 cells from the original run, to prepend to a translation.

    Some strings begin with blank cells the engine overdraws at runtime (the
    LOAD-menu stage counter "[N]面", the status-cure unit name, ...). The dump
    renders 0x0000 as nothing, so translations omit them; preserving them keeps
    the translated text from starting under those glyphs and overlapping them.
    """
    lead = 0
    for t in orig:
        if t == 0:
            lead += 1
        else:
            break
    return [0] * lead


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    add_game_args(ap)
    add_release_args(ap)
    ap.add_argument("--system-in", default="work/build/SYSTEM.BIN.font")
    ap.add_argument("--system-out", default=None)
    ap.add_argument("--strings", default=None)
    ap.add_argument("--layout", default=None,
                    help="Per-language SYSTEM line-growth limits JSON.")
    ap.add_argument("--card-layout",
                    default="data/common/system_card_layout.json")
    ap.add_argument("--source-strings",
                    default="work/systemdump/system_strings.json",
                    help="Generated SYSTEM source dump with offsets and JP text.")
    ap.add_argument("--tbl", default=None)
    ap.add_argument("--repack", action="store_true",
                    help="Regenerate each group's offset table so strings may change "
                         "length (default: in-place, table untouched, byte-compatible). "
                         "Only safe if the game locates strings by table index; verify "
                         "in an emulator before enabling.")
    ap.add_argument("--max-grow", type=int, default=None,
                    help="Override the layout's default growth limit for diagnostics; "
                         "per-id layout overrides still take precedence.")
    ap.add_argument("--strict", action="store_true",
                    help="Exit non-zero on any unencodable line or over-budget group.")
    args = ap.parse_args()

    release = release_from_args(args, platform="ps1")
    lang = language_from_args(args)
    strings_path = Path(args.strings) if args.strings else lang.system_strings
    layout_path = Path(args.layout) if args.layout else lang.system_layout
    source_strings_path = Path(args.source_strings)
    tbl = Path(args.tbl) if args.tbl else lang.tbl
    system_out = (Path(args.system_out) if args.system_out
                  else lang.build_path("SYSTEM.BIN.{lang}"))

    codec = Codec(load_charmap_tbl(tbl))
    data = bytearray(Path(args.system_in).read_bytes())
    groups = find_groups(data, GroupConfig(
        order=PS1.order, scan_start=release.offset("system_scan_start")))
    if not source_strings_path.exists():
        raise SystemExit(
            f"SYSTEM source dump not found: {source_strings_path}; "
            "run scripts/system_dump.py first"
        )
    source_entries = json.loads(source_strings_path.read_text(encoding="utf-8"))
    translations = json.loads(strings_path.read_text(encoding="utf-8"))
    if not isinstance(source_entries, list):
        raise SystemExit(f"SYSTEM source dump must be a list: {source_strings_path}")
    if not isinstance(translations, dict):
        raise SystemExit(f"SYSTEM translation overlay must be an object: {strings_path}")
    if any("id" not in entry for entry in source_entries):
        raise SystemExit(
            f"outdated SYSTEM source dump: {source_strings_path}; "
            "regenerate it with scripts/system_dump.py"
        )

    source_by_id = {e["id"]: e for e in source_entries}
    if len(source_by_id) != len(source_entries):
        raise SystemExit(f"duplicate ids in SYSTEM source dump: {source_strings_path}")
    unknown = sorted(set(translations) - set(source_by_id))
    if unknown:
        raise SystemExit(
            f"{strings_path}: {len(unknown)} unknown SYSTEM ids, first: {unknown[:5]}"
        )
    default_max_grow, max_grow_overrides = load_system_layout(
        layout_path, source_by_id
    )
    card_line_cells = load_card_layout(Path(args.card_layout))
    if args.max_grow is not None:
        if args.max_grow < 0:
            raise SystemExit("--max-grow must be a non-negative integer")
        default_max_grow = args.max_grow

    group_by_table = {table_off: gi for gi, (table_off, _table, _base) in enumerate(groups)}
    by_key: dict[tuple[int, int], str] = {}
    id_by_key: dict[tuple[int, int], str] = {}
    loose: list[tuple[dict, str]] = []
    for source in source_entries:
        if source["group"] == -1:
            continue
        table_off = int(source["table"], 16)
        if table_off in group_by_table:
            id_by_key[(group_by_table[table_off], source["index"])] = source["id"]
    for entry_id, text in translations.items():
        if not isinstance(text, str):
            raise SystemExit(f"{strings_path}: {entry_id} value must be a string")
        if not text:
            continue
        source = source_by_id[entry_id]
        if source["group"] == -1:
            loose.append((source, text))
        else:
            table_off = int(source["table"], 16)
            if table_off not in group_by_table:
                raise SystemExit(
                    f"{source_strings_path}: source table {source['table']} "
                    "does not exist in the input SYSTEM.BIN"
                )
            by_key[(group_by_table[table_off], source["index"])] = text

    problems = []
    changed = 0

    # Loose strings have no table to regenerate: write within the fixed budget.
    for e, text in loose:
        # rstrip only: a leading space is a deliberate layout choice (it separates
        # the text from an engine-drawn prefix like the LOAD-menu "[N]面" counter),
        # so it must survive into the encoded line.
        text = text.rstrip()
        if not text:
            continue
        off = int(e["offset"], 16)
        budget = int(e["words"])
        if text == "{BLANK}":
            struct.pack_into("<%dH" % budget, data, off, *([FFFF] * budget))
            changed += 1
            continue
        try:
            toks = codec.encode(text)
        except Exception as exc:
            problems.append(f"loose {e['offset']}: unencodable ({exc}) :: {text!r}")
            continue
        orig = list(struct.unpack_from("<%dH" % budget, data, off))
        toks = reserve_leading_cells(orig) + toks
        if len(toks) > budget:
            problems.append(f"loose {e['offset']}: {len(toks)}>{budget} :: {text!r}")
            continue
        struct.pack_into("<%dH" % budget, data, off, *(toks + [FFFF] * (budget - len(toks))))
        changed += 1
    for gi, (table_off, table, base) in enumerate(groups):
        n = len(table)
        last_off = base + table[-1] * 2
        group_end = last_off + (run_length(data, last_off) + 1) * 2
        blob_budget = (group_end - base) // 2     # words available for strings+terminators

        # Encode each string's new code sequence (or keep the original).
        seqs: list[list[int]] = []
        lens: list[int] = []
        for k in range(n):
            off = base + table[k] * 2
            orig_len = table[k + 1] - table[k] - 1 if k + 1 < n else run_length(data, off)
            lens.append(orig_len)
            orig = list(struct.unpack_from("<%dH" % orig_len, data, off)) if orig_len else []
            text = by_key.get((gi, k), "").rstrip()  # keep leading layout spaces
            if not text:
                seqs.append(orig)
                continue
            if text == "{BLANK}":
                seqs.append([])
                changed += 1
                continue
            try:
                toks = codec.encode(text)
            except Exception as exc:
                problems.append(f"g{gi}#{k}: unencodable ({exc}) :: {text!r}")
                seqs.append(orig)
                continue
            toks = reserve_leading_cells(orig) + toks
            entry_id = id_by_key[(gi, k)]
            max_grow = max_grow_overrides.get(entry_id, default_max_grow)
            if args.repack and table_off in card_line_cells:
                cap = card_line_cells[table_off]
            else:
                cap = orig_len + max_grow if args.repack else orig_len
            if len(toks) > cap:
                problems.append(
                    f"{entry_id}: line {len(toks)}>{cap} "
                    f"(max-grow {max_grow}) :: {text!r}"
                )
                seqs.append(orig)
                continue
            seqs.append(toks)
            changed += 1

        if not args.repack:
            # In-place: keep the original table and each string's slot; only the
            # text inside changes (FFFF-padded). Byte-compatible, table untouched.
            for k, s in enumerate(seqs):
                off = base + table[k] * 2
                struct.pack_into("<%dH" % lens[k], data, off, *(s + [FFFF] * (lens[k] - len(s))))
            continue

        # --repack: regenerate the offset table and pack the string blob tight.
        # Identical strings share one blob slot: the engine reads text at
        # base + table[k]*2 and never writes back, so aliasing is safe.
        new_table: list[int] = []
        blob: list[int] = []
        slot_by_seq: dict[tuple[int, ...], int] = {}
        for s in seqs:
            key = tuple(s)
            slot = slot_by_seq.get(key)
            if slot is None:
                slot = len(blob)
                slot_by_seq[key] = slot
                blob.extend(s)
                blob.append(FFFF)
            new_table.append(slot)
        blob_words = len(blob)
        if blob_words > blob_budget:
            problems.append(f"group {gi} @ {table_off:#x}: blob {blob_words}>{blob_budget} words")
            continue
        struct.pack_into("<%dH" % n, data, table_off, *new_table)
        struct.pack_into("<%dH" % blob_words, data, base, *blob)
        for off in range(base + blob_words * 2, group_end, 2):
            struct.pack_into("<H", data, off, FFFF)

    system_out.parent.mkdir(parents=True, exist_ok=True)
    system_out.write_bytes(data)
    print(f"packed {changed} translated lines into {len(groups)} groups -> {system_out}")
    for p in problems:
        print("  PROBLEM", p)
    if problems and args.strict:
        sys.exit(1)


if __name__ == "__main__":
    main()
