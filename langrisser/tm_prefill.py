#!/usr/bin/env python3
"""Pre-fill target-language chunk files from translation memory before manual work.

Sources, in priority order:
1. exact-match translation memory built from all already-translated chunks
   (same JP record text -> same translated record text);
2. name-plate records (text + <$FFFF>) resolved via the language pack's
   names.csv and glossary.csv.

Untranslated records keep their JP text and their indices are printed, so
the output goes to a staging directory (work/l5/wip_<lang> by default): files in
the language pack must be fully translated or the build fails on kanji whose
font slots were sacrificed for target-language glyphs. Move a chunk file to
the pack's SCEN only once it passes langrisser.validate_translation.
"""
import argparse
import csv
import re
from pathlib import Path

from langrisser.project import add_language_args, language_from_args

TAG_RE = re.compile(r"<\$[0-9A-Fa-f]{4}>")


def read_records(path: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "\t" in raw and not raw.startswith("#"):
            idx, text = raw.split("\t", 1)
            out[int(idx)] = text
    return out


def jp_like(text: str) -> bool:
    return bool(re.search(r"[぀-ヺ一-鿿]", TAG_RE.sub("", text).replace("・", "")))


def build_tm(jp_dump: Path, translation_root: Path, stem: str) -> dict[str, str]:
    tm: dict[str, str] = {}
    for target_fp in sorted((translation_root / stem).glob("chunk_*.txt")):
        jp_fp = jp_dump / stem / target_fp.name
        if not jp_fp.exists():
            continue
        jp = read_records(jp_fp)
        target = read_records(target_fp)
        for idx, jp_text in jp.items():
            target_text = target.get(idx)
            if target_text and target_text != jp_text and not jp_like(target_text):
                tm.setdefault(jp_text, target_text)
    return tm


def _translated_value(row: dict[str, str], preferred: tuple[str, ...]) -> str:
    for col in preferred:
        val = (row.get(col) or "").strip()
        if val and val != "?":
            return val
    return ""


def load_names(names_path: Path, glossary_path: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    for fp, jp_col, target_cols in (
        (names_path, "jp", ("text",)),
        (glossary_path, "jp", ("text",)),
    ):
        if not fp.exists():
            continue
        for row in csv.DictReader(open(fp, encoding="utf-8")):
            jp = row[jp_col].split("/")[0].strip()
            target = _translated_value(row, target_cols)
            if jp and target and target != "?":
                names.setdefault(jp, target)
    return names


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("chunks", nargs="+", type=int)
    ap.add_argument("--jp-dump", default="work/l5/scriptdump")
    ap.add_argument("--translation-root", default=None,
                    help="Override the language pack's translated-text root.")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--stem", default="SCEN")
    args = ap.parse_args()

    lang = language_from_args(args)
    jp_dump = Path(args.jp_dump)
    translation_root = (Path(args.translation_root)
                        if args.translation_root else lang.dump_root)
    out_dir = Path(args.out_dir) if args.out_dir else lang.wip_root
    tm = build_tm(jp_dump, translation_root, args.stem)
    names = load_names(lang.names, lang.glossary)

    for cidx in args.chunks:
        jp_fp = jp_dump / args.stem / f"chunk_{cidx:03d}.txt"
        jp = read_records(jp_fp)
        header = [l for l in jp_fp.read_text(encoding="utf-8").splitlines()
                  if l.startswith("#")]
        out_lines = list(header)
        todo: list[int] = []
        filled = 0
        for idx in sorted(jp):
            text = jp[idx]
            if text in tm:
                text = tm[text]
                filled += 1
            elif text.endswith("<$FFFF>") and "<$" not in text[:-7]:
                base = text[:-7]
                if base in names:
                    text = names[base] + "<$FFFF>"
                    filled += 1
                elif jp_like(text):
                    todo.append(idx)
            elif jp_like(text):
                todo.append(idx)
            out_lines.append(f"{idx}\t{text}")
        out_fp = out_dir / args.stem / jp_fp.name
        out_fp.parent.mkdir(parents=True, exist_ok=True)
        out_fp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print(f"chunk {cidx:03d}: records={len(jp)} prefilled={filled} todo={len(todo)}")
        print(f"  todo indices: {todo}")


if __name__ == "__main__":
    main()
