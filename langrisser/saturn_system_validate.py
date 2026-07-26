#!/usr/bin/env python3
"""Validate a packed Saturn SYSTEM.DAT against the runtime write contract.

The Saturn runtime addresses SYSTEM UI text through a pointer directory at
file offset 0x8000: pairs of big-endian RAM addresses (offset table, string
base) per group, pre-relocated for the load base 0x00200000 (see
docs/SATURN_DISC_FORMAT.md). The font glyph plane grows from offset 0 toward
that directory: slot 1819 ends exactly at 0x7FF8, slot 1820 would cross into
the directory and clobber the group 0/1 pointers — the game then renders
empty menus and hangs walking garbage offset tables. This is a real failure
mode this validator exists to catch.

The check is a whole-file diff contract between the original and the packed
SYSTEM.DAT. Writes are allowed only inside:

- the glyph plane `[0, 0x8000)`;
- each text group `[table_offset, group_end)` as found in the original;
- the Now Loading compressed stream budget at `STREAM_OFFSET`;
- the name-entry flat input table (located by its original kana tokens).

Everything else — the directory at `[0x8000, 0x8084)` above all — must stay
byte-identical, group structure (offset, entry count, string base) must be
preserved, and every directory pointer must resolve to a group table/base the
group scan actually found.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from langrisser import saturn_now_loading as nl
from langrisser.binfmt import BE
from langrisser.offsetgroups import SATURN, find_groups, run_length
from langrisser.patch_name_entry import ORIG_RUNS, encode_runs
from langrisser.scen import load_charmap_tbl
from langrisser.saturn_name_entry import char_to_tok, pack_words

RAM_BASE = 0x00200000
DIR_START = 0x8000
DIR_END = 0x8084


def group_spans(data: bytes) -> tuple[list[tuple[int, int, int]], list[tuple[int, int]]]:
    """Return ((table_off, entries, base) ...) and byte spans per group."""
    keys: list[tuple[int, int, int]] = []
    spans: list[tuple[int, int]] = []
    for off, table, base in find_groups(data, SATURN):
        last = base + table[-1] * 2
        end = last + (run_length(data, last, SATURN) + 1) * 2
        keys.append((off, len(table), base))
        spans.append((off, end))
    return keys, spans


def directory_pointers(data: bytes) -> list[int]:
    return [BE.u32(data, off) for off in range(DIR_START, DIR_END, 4)]


def diff_regions(a: bytes, b: bytes) -> list[tuple[int, int]]:
    regions: list[tuple[int, int]] = []
    i = 0
    n = len(a)
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            regions.append((i, j))
            i = j
        else:
            i += 1
    return regions


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--orig", default="work/build/saturn/SYSTEM.DAT",
                    help="Original extracted Saturn SYSTEM.DAT.")
    ap.add_argument("--system", required=True,
                    help="Packed/translated SYSTEM.DAT to validate.")
    ap.add_argument("--tbl", required=True,
                    help="Generated Saturn charmap .tbl (locates the "
                         "name-entry input table by its original tokens).")
    args = ap.parse_args()

    orig = Path(args.orig).read_bytes()
    new = Path(args.system).read_bytes()
    errors: list[str] = []
    if len(orig) != len(new):
        raise SystemExit(
            f"SYSTEM size changed: {len(orig)} -> {len(new)}; the file is "
            "loaded to a fixed RAM window and must keep its exact size"
        )

    orig_keys, spans = group_spans(orig)
    new_keys, _ = group_spans(new)
    if orig_keys != new_keys:
        moved = [
            f"orig {ok} -> packed {nk}"
            for ok, nk in zip(orig_keys, new_keys)
            if ok != nk
        ] or [f"group count {len(orig_keys)} -> {len(new_keys)}"]
        errors.append("group structure changed (the 0x8000 directory points "
                      "at fixed offsets): " + "; ".join(moved[:4]))

    # Every group the scan found must be reachable through the directory.
    for data, label in ((orig, "orig"), (new, "packed")):
        targets = {ptr - RAM_BASE for ptr in directory_pointers(data)}
        missing = [f"{off:#x}" for off, _, base in orig_keys
                   if off not in targets or base not in targets]
        if missing:
            errors.append(f"{label}: directory does not point at group "
                          f"tables/bases: {', '.join(missing[:6])}")

    # Allowed write spans.
    allowed = [(0, DIR_START)] + spans
    table = bytes(orig[nl.TABLE_OFFSET:nl.STREAM_OFFSET])
    _, stream_budget = nl.decode_stream(table, bytes(orig[nl.STREAM_OFFSET:]))
    allowed.append((nl.STREAM_OFFSET, nl.STREAM_OFFSET + stream_budget))
    c2t = char_to_tok(load_charmap_tbl(Path(args.tbl)))
    input_table = pack_words(
        [word for run in encode_runs(ORIG_RUNS, c2t) for word in run])
    input_pos = orig.find(input_table)
    if input_pos < 0:
        errors.append("name-entry input table not found in the original")
    else:
        allowed.append((input_pos, input_pos + len(input_table)))

    # Merge touching spans so a legitimate write straddling two adjacent
    # groups (their spans can abut exactly) is not misreported.
    merged: list[tuple[int, int]] = []
    for s, e in sorted(allowed):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    for a, b in diff_regions(orig, new):
        if not any(s <= a and b <= e for s, e in merged):
            where = ("GROUP POINTER DIRECTORY"
                     if a < DIR_END and b > DIR_START else "outside contract")
            errors.append(f"write at {a:#07x}..{b:#07x} ({where})")

    if errors:
        listing = "\n".join(f"  - {e}" for e in errors[:20])
        more = f"\n  ... +{len(errors) - 20} more" if len(errors) > 20 else ""
        raise SystemExit(
            f"Saturn SYSTEM write contract violated ({args.system}):\n"
            f"{listing}{more}"
        )
    print(f"SYSTEM contract ok: {len(orig_keys)} groups intact, directory "
          f"preserved, all writes within contract -> {args.system}")


if __name__ == "__main__":
    main()
