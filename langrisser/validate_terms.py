#!/usr/bin/env python3
"""Validate target-language names and glossary consistency."""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from langrisser.project import ROOT, add_language_args, language_from_args, load_language

NAME_FIELDS = ("jp", "text", "alt")
GLOSSARY_FIELDS = ("jp", "guide_en", "text", "note")


def read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != fields:
            raise SystemExit(f"{path}: expected CSV header {','.join(fields)}")
        return list(reader)


def aliases(value: str) -> list[str]:
    return [part.strip() for part in value.split("/") if part.strip()]


def canonical(value: str) -> str:
    values = aliases(value)
    return values[0] if values else ""


def read_records(path: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "\t" in raw and not raw.startswith("#"):
            idx, text = raw.split("\t", 1)
            out[int(idx)] = text
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--reference-lang", default="en")
    ap.add_argument(
        "--require-complete",
        action="store_true",
        help="Reject every empty target name or glossary term.",
    )
    ap.add_argument("--scen", default="work/extracted/SCEN.DAT")
    ap.add_argument("--jp-dump", default="work/scriptdump/SCEN")
    ap.add_argument(
        "--max-plate-chars",
        type=int,
        default=0,
        help="Reject speaker plates longer than this before pair encoding (0 disables).",
    )
    ap.add_argument(
        "--require-speakers",
        action="store_true",
        help="Require a mapped plate for every speaker-pool key in SCEN.",
    )
    args = ap.parse_args()

    lang = language_from_args(args)
    reference = load_language(args.reference_lang, args.lang_root)
    names = read_csv(lang.names, NAME_FIELDS)
    reference_names = read_csv(reference.names, NAME_FIELDS)
    glossary = read_csv(lang.glossary, GLOSSARY_FIELDS)
    problems: list[str] = []

    target_keys = [row["jp"].strip() for row in names]
    reference_keys = [row["jp"].strip() for row in reference_names]
    if target_keys != reference_keys:
        problems.append(
            f"{lang.names}: JP key sequence differs from {reference.names}"
        )

    names_by_jp: dict[str, set[str]] = defaultdict(set)
    plate_names_by_jp: dict[str, set[str]] = defaultdict(set)
    for line_no, row in enumerate(names, 2):
        jp = row["jp"].strip()
        text = row["text"].strip()
        plate_text = row["alt"].strip() or text
        if not jp:
            problems.append(f"{lang.names}:{line_no}: empty JP key")
        if args.require_complete and not text:
            problems.append(f"{lang.names}:{line_no}: empty target text for {jp}")
        if jp and text:
            names_by_jp[jp].add(text)
            plate_names_by_jp[jp].add(plate_text)
    for jp, values in names_by_jp.items():
        if len(values) > 1:
            problems.append(
                f"{lang.names}: conflicting target names for {jp}: "
                + ", ".join(sorted(values))
            )

    glossary_by_jp: dict[str, str] = {}
    for line_no, row in enumerate(glossary, 2):
        target = canonical(row["text"])
        if args.require_complete and not target:
            problems.append(
                f"{lang.glossary}:{line_no}: empty target glossary value"
            )
        for jp in aliases(row["jp"]):
            previous = glossary_by_jp.get(jp)
            if previous and target and previous != target:
                problems.append(
                    f"{lang.glossary}:{line_no}: conflicting glossary values "
                    f"for {jp}: {previous!r} vs {target!r}"
                )
            elif target:
                glossary_by_jp[jp] = target

    overlaps = 0
    for jp, values in names_by_jp.items():
        glossary_value = glossary_by_jp.get(jp)
        if glossary_value is None:
            continue
        overlaps += 1
        if values != {glossary_value}:
            problems.append(
                f"{jp}: names.csv {sorted(values)!r} != "
                f"glossary {glossary_value!r}"
            )

    scen_path = Path(args.scen)
    jp_dump = Path(args.jp_dump)
    if not scen_path.is_absolute():
        scen_path = ROOT / scen_path
    if not jp_dump.is_absolute():
        jp_dump = ROOT / jp_dump
    speaker_keys = 0
    if args.require_speakers and scen_path.exists() and jp_dump.exists():
        from langrisser.rewrap import speaker_pool_sizes

        term_map = {
            jp: next(iter(values))
            for jp, values in plate_names_by_jp.items()
            if len(values) == 1
        }
        for jp, value in glossary_by_jp.items():
            term_map.setdefault(jp, value)
        for chunk, pool_size in speaker_pool_sizes(scen_path).items():
            source_path = jp_dump / f"chunk_{chunk:03d}.txt"
            if not source_path.exists():
                problems.append(f"missing JP speaker source: {source_path}")
                continue
            records = read_records(source_path)
            for record in range(1, pool_size + 1):
                jp = records.get(record, "").removesuffix("<$FFFF>")
                if not jp:
                    continue
                speaker_keys += 1
                target = term_map.get(jp, "")
                if not target:
                    problems.append(
                        f"chunk {chunk:03d} record {record}: "
                        f"unmapped speaker plate {jp!r}"
                    )
                elif args.max_plate_chars and len(target) > args.max_plate_chars:
                    problems.append(
                        f"chunk {chunk:03d} record {record}: speaker plate "
                        f"{target!r} exceeds {args.max_plate_chars} "
                        "pre-pair characters"
                    )
    elif args.require_speakers:
        if not scen_path.exists():
            problems.append(f"missing SCEN data: {scen_path}")
        if not jp_dump.exists():
            problems.append(f"missing JP script dump: {jp_dump}")

    if problems:
        for problem in problems:
            print(problem)
        raise SystemExit(f"{len(problems)} terminology problem(s)")
    print(
        f"terminology OK: names={len(names)} glossary={len(glossary)} "
        f"overlaps={overlaps} speaker_records={speaker_keys}"
    )


if __name__ == "__main__":
    main()
