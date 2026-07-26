#!/usr/bin/env python3
"""Replace the katakana name-entry grid with the target-language alphabet.

The grid exists in two places and both are patched in place (sizes never
change):

- SYSTEM.BIN holds 19 content runs of 5 tokens each, separated by 0xFFFF
  (then the cursor-control runs, which are left alone);
- SLPS_018.19 holds the rendered 10x10 screen table: row K = run K
  (columns 1-5) plus run K+9 (columns 6-10), with a blank left half on
  the last row.

Both copies are located by searching for the original kana tokens, and
every token is verified against the expected original before anything is
written, so a layout mismatch aborts instead of corrupting data.
"""
import argparse
import json
import struct
from pathlib import Path

from langrisser.project import add_language_args, language_from_args
from langrisser.scen import load_charmap_tbl

# Original grid content, in SYSTEM.BIN run order. The trailing space in
# run 17 is the original's one blank (selectable, types a space) cell.
ORIG_RUNS = [
    "アイウエオ", "カキクケコ", "サシスセソ", "タチツテト", "ナニヌネノ",
    "ハヒフヘホ", "マミムメモ", "ヤユヨワン", "ラリルレロ",
    "ガギグゲゴ", "ザジズゼゾ", "ダヂヅデド", "バビブベボ", "パピプペポ",
    "ヴ！？・ー", "ァィゥェォ", "ャュョッ ", "１２３４５", "６７８９０",
]
RUN_LEN = 5
SEPARATOR = 0xFFFF


def encode_runs(runs: list[str], char2tok: dict[str, int]) -> list[list[int]]:
    out = []
    for run in runs:
        if len(run) != RUN_LEN:
            raise SystemExit(f"grid run {run!r} must be {RUN_LEN} characters")
        toks = []
        for ch in run:
            if ch == " ":
                toks.append(0x0000)
                continue
            tok = char2tok.get(ch)
            if tok is None:
                raise SystemExit(f"grid character {ch!r} has no single-glyph token")
            toks.append(tok)
        out.append(toks)
    return out


def find_anchor(blob: bytes, anchor: bytes) -> int:
    pos = blob.find(anchor)
    if pos < 0 or blob.find(anchor, pos + 2) != -1:
        raise SystemExit("name-entry grid anchor not found exactly once")
    return pos


def char_to_tok(tok2char: dict[int, str]) -> dict[str, int]:
    """char -> single-glyph token, also accepting fullwidth digit spellings."""
    c2t: dict[str, int] = {}
    for tok, ch in tok2char.items():
        if len(ch) == 1:
            c2t.setdefault(ch, tok)
    for half, full in zip("0123456789", "０１２３４５６７８９"):
        if half in c2t:
            c2t.setdefault(full, c2t[half])
    return c2t


def grid_span(blob: bytes, tok2char: dict[int, str]) -> tuple[int, int] | None:
    """Byte span [start, end) of the name-entry grid in `blob`, or None.

    The engine references the grid by a hard-coded absolute address; there is no
    pointer in the data to read, so the region is located by its content (the
    first run's kana), exactly as the patcher does. This is the single source of
    the grid location: the unified SYSTEM text flow imports it to leave the grid
    alone (re-encoding those runs as ordinary text would break the rename screen).
    """
    c2t = char_to_tok(tok2char)
    try:
        anchor = struct.pack(f"<{RUN_LEN}H", *(c2t[ch] for ch in ORIG_RUNS[0]))
    except KeyError:
        return None
    pos = blob.find(anchor)
    if pos < 0 or blob.find(anchor, pos + 2) != -1:
        return None
    return pos, pos + len(ORIG_RUNS) * (RUN_LEN + 1) * 2


def patch_system(blob: bytearray, orig: list[list[int]], new: list[list[int]]) -> None:
    """19 runs of 5 tokens, each followed by an 0xFFFF separator."""
    anchor = struct.pack(f"<{RUN_LEN}H", *orig[0])
    pos = find_anchor(blob, anchor)
    for run_orig, run_new in zip(orig, new):
        have = list(struct.unpack_from(f"<{RUN_LEN + 1}H", blob, pos))
        if have != run_orig + [SEPARATOR]:
            raise SystemExit(f"SYSTEM.BIN grid mismatch at {pos:#x}: {have}")
        struct.pack_into(f"<{RUN_LEN}H", blob, pos, *run_new)
        pos += (RUN_LEN + 1) * 2


def patch_exe(blob: bytearray, orig: list[list[int]], new: list[list[int]]) -> None:
    """10 screen rows of 10 tokens: run K | run K+9, blank | run 19 last."""
    blank = [0x0000] * RUN_LEN
    rows_orig = [(orig[k] if k < 9 else blank) + orig[k + 9] for k in range(10)]
    rows_new = [(new[k] if k < 9 else blank) + new[k + 9] for k in range(10)]
    anchor = struct.pack("<10H", *rows_orig[0])
    pos = find_anchor(blob, anchor)
    for row_orig, row_new in zip(rows_orig, rows_new):
        have = list(struct.unpack_from("<10H", blob, pos))
        if have != row_orig:
            raise SystemExit(f"SLPS exe grid mismatch at {pos:#x}: {have}")
        struct.pack_into("<10H", blob, pos, *row_new)
        pos += 10 * 2


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--grid", default=None)
    ap.add_argument("--tbl", default=None)
    ap.add_argument("--system-in", default="work/l5/build/SYSTEM.BIN.menu")
    ap.add_argument("--system-out", default=None)
    ap.add_argument("--exe-in", default="work/l5/extracted/SLPS_018.19")
    ap.add_argument("--exe-out", default=None)
    args = ap.parse_args()

    lang = language_from_args(args)
    grid = Path(args.grid) if args.grid else lang.name_entry_grid
    tbl = Path(args.tbl) if args.tbl else lang.tbl
    system_out = (Path(args.system_out) if args.system_out
                  else lang.build_path("SYSTEM.BIN.{lang}.ne"))
    exe_out = (Path(args.exe_out) if args.exe_out
               else lang.build_path("SLPS_018.19.{lang}"))

    tok2char = load_charmap_tbl(tbl)
    char2tok: dict[str, int] = {}
    for tok, ch in tok2char.items():
        if len(ch) == 1:
            char2tok.setdefault(ch, tok)
    # The JP grid is dumped through the original font table, where digits
    # appear as their fullwidth forms; accept both spellings.
    jp2tok = dict(char2tok)
    for half, full in zip("0123456789", "０１２３４５６７８９"):
        jp2tok.setdefault(full, char2tok[half])

    orig = encode_runs(ORIG_RUNS, jp2tok)
    new = encode_runs(json.loads(grid.read_text(encoding="utf-8"))["runs"], char2tok)

    for src, dst, patch in (
        (Path(args.system_in), system_out, patch_system),
        (Path(args.exe_in), exe_out, patch_exe),
    ):
        blob = bytearray(src.read_bytes())
        patch(blob, orig, new)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(bytes(blob))
        print(f"name-entry grid patched: {src} -> {dst}")


if __name__ == "__main__":
    main()
