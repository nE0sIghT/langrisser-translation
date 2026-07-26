#!/usr/bin/env python3
"""Patch the Saturn name-entry alphabet tables inside SYSTEM.DAT.

Saturn keeps both confirmed name-entry structures in `SYSTEM.DAT`:

- a display grid: 19 runs of 5 BE u16 tokens, each followed by 0xFFFF;
- a flat input table: the same 95 tokens without separators.

Unlike PS1, no executable-side 10x10 grid has been found. Both Saturn tables are
located by their original kana tokens and verified before being rewritten.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from langrisser.binfmt import BE
from langrisser.patch_name_entry import ORIG_RUNS, RUN_LEN, encode_runs
from langrisser.project import add_language_args, language_from_args
from langrisser.scen import load_charmap_tbl

SEPARATOR = 0xFFFF


def char_to_tok(tok2char: dict[int, str]) -> dict[str, int]:
    c2t: dict[str, int] = {}
    for tok, ch in tok2char.items():
        if len(ch) == 1:
            c2t.setdefault(ch, tok)
    for half, full in zip("0123456789", "０１２３４５６７８９"):
        if half in c2t:
            c2t.setdefault(full, c2t[half])
    return c2t


def pack_words(words: list[int]) -> bytes:
    return b"".join(BE.pack_u16(word) for word in words)


def find_unique(blob: bytes, needle: bytes, label: str) -> int:
    pos = blob.find(needle)
    if pos < 0:
        raise SystemExit(f"{label} anchor not found")
    if blob.find(needle, pos + 1) != -1:
        raise SystemExit(f"{label} anchor is not unique")
    return pos


def patch_display_grid(blob: bytearray, orig: list[list[int]], new: list[list[int]]) -> int:
    orig_words: list[int] = []
    for run in orig:
        orig_words.extend(run)
        orig_words.append(SEPARATOR)
    pos = find_unique(blob, pack_words(orig_words), "Saturn name-entry display grid")
    cur = pos
    for run_orig, run_new in zip(orig, new):
        have = [BE.u16(blob, cur + i * 2) for i in range(RUN_LEN + 1)]
        if have != run_orig + [SEPARATOR]:
            raise SystemExit(f"display grid mismatch at {cur:#x}: {have}")
        blob[cur:cur + RUN_LEN * 2] = pack_words(run_new)
        cur += (RUN_LEN + 1) * 2
    return pos


def patch_input_table(blob: bytearray, orig: list[list[int]], new: list[list[int]]) -> int:
    orig_flat = [word for run in orig for word in run]
    new_flat = [word for run in new for word in run]
    pos = find_unique(blob, pack_words(orig_flat), "Saturn name-entry input table")
    have = [BE.u16(blob, pos + i * 2) for i in range(len(orig_flat))]
    if have != orig_flat:
        raise SystemExit(f"input table mismatch at {pos:#x}")
    blob[pos:pos + len(new_flat) * 2] = pack_words(new_flat)
    return pos


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--grid", default=None)
    ap.add_argument("--tbl", default=None)
    ap.add_argument("--system-in", default="work/build/saturn/SYSTEM.ru.DAT")
    ap.add_argument("--system-out", default=None)
    args = ap.parse_args()

    lang = language_from_args(args)
    grid = Path(args.grid) if args.grid else lang.name_entry_grid
    tbl = Path(args.tbl) if args.tbl else Path(f"work/build/saturn/lang5_{lang.suffix}.saturn.tbl")
    out = Path(args.system_out) if args.system_out else Path(args.system_in)

    tok2char = load_charmap_tbl(tbl)
    c2t = char_to_tok(tok2char)
    orig = encode_runs(ORIG_RUNS, c2t)
    new = encode_runs(json.loads(grid.read_text(encoding="utf-8"))["runs"], c2t)

    blob = bytearray(Path(args.system_in).read_bytes())
    display_pos = patch_display_grid(blob, orig, new)
    input_pos = patch_input_table(blob, orig, new)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    print(
        f"Saturn name-entry patched: display@{display_pos:#x} "
        f"input@{input_pos:#x} -> {out}"
    )


if __name__ == "__main__":
    main()
