#!/usr/bin/env python3
"""Validate SYSTEM.BIN text sequences with engine-specific layout constraints."""
import argparse
import json
import sys
from pathlib import Path

from langrisser.project import ROOT, add_language_args, language_from_args
from langrisser.scen import Codec, TAG_RE, load_charmap_tbl

BLANK_CELL = 0x0000


def load_object(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return data


def encoded_length(codec: Codec, text: str) -> int:
    return len(codec.encode(str(text).rstrip()))


def encoded_tokens(codec: Codec, text: str) -> list[int]:
    return codec.encode(str(text).rstrip())


def first_visible_cell(tokens: list[int]) -> int | None:
    for i, token in enumerate(tokens):
        if token != BLANK_CELL:
            return i
    return None


def first_visible_text(codec: Codec, tokens: list[int]) -> str:
    for token in tokens:
        if token != BLANK_CELL:
            return codec.tok2char.get(token, "")
    return ""


def authored_visible_text(text: str) -> str:
    return TAG_RE.sub("", str(text)).strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--tbl", default=None)
    ap.add_argument("--strings", default=None)
    ap.add_argument("--system-source", default="work/l5/systemdump/system_strings.json")
    ap.add_argument(
        "--constraints",
        default="data/common/system_ui_constraints.json",
    )
    args = ap.parse_args()

    lang = language_from_args(args)
    tbl = Path(args.tbl) if args.tbl else lang.tbl
    strings_path = Path(args.strings) if args.strings else lang.system_strings
    source_path = Path(args.system_source)
    constraints_path = Path(args.constraints)
    if not constraints_path.is_absolute():
        constraints_path = ROOT / constraints_path

    codec = Codec(load_charmap_tbl(tbl))
    overlay = load_object(strings_path)
    source_data = json.loads(source_path.read_text(encoding="utf-8"))
    source = {entry["id"]: entry["jp"] for entry in source_data}
    constraints = load_object(constraints_path)

    problems = 0
    for sequence in constraints.get("atlas_sequences", []):
        name = sequence["name"]
        columns = int(sequence["columns"])
        slot = int(sequence.get("start_slot", 0))
        for entry_id in sequence["ids"]:
            if entry_id not in source:
                print(f"{name}: unknown SYSTEM id {entry_id}")
                problems += 1
                continue
            text = overlay.get(entry_id, source[entry_id])
            if text == "{BLANK}":
                text = ""
            try:
                length = encoded_length(codec, text)
            except ValueError as exc:
                print(f"{name}: {entry_id} is unencodable: {exc}")
                problems += 1
                continue
            column = slot % columns
            if length and column + length > columns:
                print(
                    f"{name}: {entry_id} {text!r} crosses the {columns}-cell "
                    f"atlas row: start={slot} column={column} length={length}"
                )
                problems += 1
            else:
                print(
                    f"{name}: {entry_id} {text!r} "
                    f"slots={slot}..{slot + length - 1} OK"
                )
            slot += length

    for field in constraints.get("fixed_width_fields", []):
        name = field["name"]
        max_cells = int(field["max_cells"])
        entry_ids = field.get("ids", [field.get("id")])
        for entry_id in entry_ids:
            if entry_id not in source:
                print(f"{name}: unknown SYSTEM id {entry_id}")
                problems += 1
                continue
            text = overlay.get(entry_id, source[entry_id])
            if text == "{BLANK}":
                text = ""
            try:
                length = encoded_length(codec, text)
            except ValueError as exc:
                print(f"{name}: {entry_id} is unencodable: {exc}")
                problems += 1
                continue
            if length > max_cells:
                print(
                    f"{name}: {entry_id} {text!r} uses {length}>{max_cells} cells"
                )
                problems += 1
            else:
                print(
                    f"{name}: {entry_id} {text!r} "
                    f"uses {length}/{max_cells} cells OK"
                )

    for field in constraints.get("composed_runtime_fields", []):
        name = field["name"]
        prefix_id = field["prefix_id"]
        suffix_id = field["suffix_id"]
        suffix_start = int(field["suffix_start"])
        max_cells = int(field["max_cells"])
        missing = [entry_id for entry_id in (prefix_id, suffix_id)
                   if entry_id not in source]
        if missing:
            print(f"{name}: unknown SYSTEM id(s): {', '.join(missing)}")
            problems += 1
            continue
        prefix = overlay.get(prefix_id, source[prefix_id])
        suffix = overlay.get(suffix_id, source[suffix_id])
        if prefix == "{BLANK}":
            prefix = ""
        if suffix == "{BLANK}":
            suffix = ""
        try:
            prefix_len = encoded_length(codec, prefix)
            suffix_tokens = encoded_tokens(codec, suffix)
        except ValueError as exc:
            print(f"{name}: composed field is unencodable: {exc}")
            problems += 1
            continue
        suffix_len = len(suffix_tokens)
        first_visible = first_visible_cell(suffix_tokens)
        end_cell = max(prefix_len, suffix_start + suffix_len)
        if first_visible is None:
            visible_start = suffix_start + suffix_len
            min_gap = 0
        else:
            visible_start = suffix_start + first_visible
            visible = first_visible_text(codec, suffix_tokens)
            prefix_tail = authored_visible_text(prefix)[-1:]
            min_gap = 0 if (
                visible[:1] in ":;,.!?)]}"
                or prefix_tail in ":;,.!?)]}"
            ) else 1
        ok = True
        if end_cell > max_cells:
            print(
                f"{name}: {prefix!r}+{suffix!r} uses {end_cell}>{max_cells} "
                "runtime cells"
            )
            problems += 1
            ok = False
        if prefix_len + min_gap > visible_start:
            print(
                f"{name}: {prefix!r} touches suffix {suffix!r}: "
                f"prefix_len={prefix_len}, suffix_visible_start={visible_start}, "
                f"min_gap={min_gap}"
            )
            problems += 1
            ok = False
        if ok:
            print(
                f"{name}: {prefix!r}+{suffix!r} "
                f"prefix={prefix_len}, suffix@{suffix_start}, "
                f"end={end_cell}/{max_cells} OK"
            )

    if problems:
        print(f"{problems} SYSTEM UI layout problem(s)")
        sys.exit(1)
    print("SYSTEM UI layout OK")


if __name__ == "__main__":
    main()
