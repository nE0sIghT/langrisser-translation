#!/usr/bin/env python3
"""Bootstrap the target-language translation dump for the startup quiz (chunk 0).

Takes the JP dump and the language pack's manual_record_overrides.json and
produces editable target chunk files. Structure-preserving merge: control
words and their arguments are kept, FFFC line breaks are re-flowed for
the target text, a leading choice marker (・) is preserved. Records whose
text is interleaved with other control words are left in Japanese and
reported for manual editing.
"""
import argparse
import json
import re
from pathlib import Path

from langrisser.project import add_language_args, language_from_args
from langrisser.scen import TAG_RE, Codec, consumes_argument, load_charmap_csv

WRAP = 26  # glyph cells per dialog line

ASCII_NORMALIZE = str.maketrans({"?": "？", "!": "！", "’": "'", "‘": "'"})


def parse_items(line: str) -> list[tuple[str, bool]]:
    """Split a dump line into (token-or-char, is_tag) items."""
    items: list[tuple[str, bool]] = []
    i = 0
    while i < len(line):
        m = TAG_RE.match(line, i)
        if m:
            items.append((m.group(0), True))
            i = m.end()
        else:
            items.append((line[i], False))
            i += 1
    return items


def wrap_text(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if len(cand) <= width:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def merge_record(line: str, target: str) -> str | None:
    """Return the merged target line, or None if structure is too complex."""
    items = parse_items(line)

    # Mark argument tags (word after F600/FBxx).
    flags: list[str] = []  # 'ctrl' | 'arg' | 'text' | 'break'
    prev_tok: int | None = None
    for tok, is_tag in items:
        if is_tag:
            v = int(tok[2:6], 16)
            if prev_tok is not None and consumes_argument(prev_tok):
                flags.append("arg")
            elif v == 0xFFFC:
                flags.append("break")
            elif v >= 0xE000:
                flags.append("ctrl")
            else:
                flags.append("text")  # unmapped glyph counts as text
            prev_tok = v
        else:
            flags.append("text")
            prev_tok = None

    text_pos = [i for i, f in enumerate(flags) if f in ("text", "break")]
    if not text_pos:
        return None
    first, last = text_pos[0], text_pos[-1]
    # Structural controls inside the text span make the record manual work.
    if any(flags[i] in ("ctrl", "arg") for i in range(first, last + 1)):
        return None

    prefix = "".join(t for t, _ in items[:first])
    suffix = "".join(t for t, _ in items[last + 1 :])
    marker = ""
    if items[first][0] == "・":
        marker = "・"

    normalized = " ".join(target.split()).translate(ASCII_NORMALIZE)
    body = "<$FFFC>".join(wrap_text(normalized, WRAP))
    return f"{prefix}{marker}{body}{suffix}"


def build_chunk(src_path: Path, overrides: dict[int, str]) -> tuple[list[str], list[int]]:
    out_lines: list[str] = []
    manual: list[int] = []
    for raw in src_path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith("#") or "\t" not in raw:
            out_lines.append(raw)
            continue
        idx_s, text = raw.split("\t", 1)
        idx = int(idx_s)
        target = overrides.get(idx)
        if not target:
            out_lines.append(raw)
            continue
        merged = merge_record(text, target)
        if merged is None:
            manual.append(idx)
            out_lines.append(raw)
        else:
            out_lines.append(f"{idx}\t{merged}")
    return out_lines, manual


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--jp-dump", default="work/l5/scriptdump")
    ap.add_argument("--overrides", default=None)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    lang = language_from_args(args)
    overrides_path = Path(args.overrides) if args.overrides else lang.manual_record_overrides
    out_dir = Path(args.out_dir) if args.out_dir else lang.dump_root

    raw = json.loads(overrides_path.read_text(encoding="utf-8"))
    per_file: dict[str, dict[int, str]] = {}
    for key, val in raw.items():
        fname, cidx, ridx = key.split(":")
        if int(cidx) != 0 or not val.strip():
            continue
        per_file.setdefault(fname, {})[int(ridx)] = val.strip()

    for fname, overrides in sorted(per_file.items()):
        stem = Path(fname).stem
        src = Path(args.jp_dump) / stem / "chunk_000.txt"
        lines, manual = build_chunk(src, overrides)
        out = out_dir / stem / "chunk_000.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{fname}: translated={len(overrides)-len(manual)} manual_needed={manual}")


if __name__ == "__main__":
    main()
