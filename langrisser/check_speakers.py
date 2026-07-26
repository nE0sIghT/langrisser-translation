#!/usr/bin/env python3
"""Assert the speaker-plate extractor matches the in-game test set.

Reads the ground-truth table in docs/SPEAKER_TEST_SET.md and checks that
`semantic_plate_slots` resolves each record to the listed speaker. A mismatch
means the per-record plate reserve (and so the line wrapping) is wrong; this is a
mandatory check (AGENTS.md). Exits non-zero on any failure.
"""
import argparse
import re
import sys
from pathlib import Path

from langrisser.project import add_language_args, language_from_args
from langrisser.rewrap import semantic_plate_slots, speaker_pool_sizes

TAG_RE = re.compile(r"<\$[0-9A-Fa-f]{4}>")
CHUNK_RE = re.compile(r"^##\s*Chunk\s+(\d+)", re.I)
ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|")


def parse_test_set(path: Path) -> list[tuple[int, int, str]]:
    """Return (chunk, record, expected_speaker) rows from the markdown tables."""
    cases: list[tuple[int, int, str]] = []
    chunk = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = CHUNK_RE.match(line)
        if m:
            chunk = int(m.group(1))
            continue
        r = ROW_RE.match(line)
        if r and chunk is not None:
            cases.append((chunk, int(r.group(1)), r.group(2).strip()))
    return cases


def plate_names(target_file: Path, pool_size: int) -> dict[int, str]:
    """Record index -> translated plate name (records 1..pool_size of the dump)."""
    names: dict[int, str] = {}
    if not target_file.exists():
        return names
    for raw in target_file.read_text(encoding="utf-8").splitlines():
        if "\t" not in raw or raw.startswith("#"):
            continue
        idx, text = raw.split("\t", 1)
        if idx.isdigit() and 1 <= int(idx) <= pool_size:
            names[int(idx)] = TAG_RE.sub("", text).replace("<$FFFF>", "").strip()
    return names


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--test-set", default="docs/SPEAKER_TEST_SET.md")
    ap.add_argument("--scen", default="work/extracted/SCEN.DAT")
    ap.add_argument("--translation-root", default=None,
                    help="Override the language pack's translated-text root.")
    args = ap.parse_args()
    lang = language_from_args(args)
    dump_root = (Path(args.translation_root)
                 if args.translation_root else lang.dump_root)

    cases = parse_test_set(Path(args.test_set))
    if not cases:
        sys.exit(f"no test cases parsed from {args.test_set}")

    scen = Path(args.scen)
    slots_by_chunk = semantic_plate_slots(scen)
    pool_sizes = speaker_pool_sizes(scen)

    failures = []
    for chunk, record, expected in cases:
        pool_size = pool_sizes.get(chunk) or 0
        names = plate_names(dump_root / "SCEN" / f"chunk_{chunk:03d}.txt", pool_size)
        slot = slots_by_chunk.get(chunk, {}).get(record)
        got = names.get(slot + 1, f"(slot {slot})") if isinstance(slot, int) and slot >= 0 \
            else ("(no plate)" if slot is None else "(location/crowd)")
        if got != expected:
            failures.append(f"chunk {chunk} record {record}: expected {expected!r}, got {got!r}")

    if failures:
        print(f"SPEAKER CHECK FAILED ({len(failures)}/{len(cases)}):")
        for f in failures:
            print("  " + f)
        sys.exit(1)
    print(f"OK: {len(cases)} speaker plates match the test set")


if __name__ == "__main__":
    main()
