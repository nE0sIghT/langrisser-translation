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
from collections import defaultdict
from collections.abc import Hashable
from pathlib import Path

from langrisser.game import add_game_args, game_from_args
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


def add_tm_records(
    candidates: dict[str, set[str]],
    jp: dict[Hashable, str],
    target: dict[Hashable, str],
) -> None:
    """Add exact source/target records to a shared, conflict-aware TM."""
    for idx, jp_text in jp.items():
        target_text = target.get(idx)
        if target_text and target_text != jp_text and not jp_like(target_text):
            candidates[jp_text].add(target_text)


def unique_tm(candidates: dict[str, set[str]]) -> dict[str, str]:
    """Keep only source records with one proven target translation.

    The same short Japanese response can legitimately have different Russian
    wording in different contexts. Picking whichever chunk sorts first makes
    the prefill nondeterministically wrong; a conflict must remain for manual
    translation instead.
    """
    return {source: next(iter(targets))
            for source, targets in candidates.items() if len(targets) == 1}


def build_tm(jp_dump: Path, translation_root: Path, stem: str) -> dict[str, str]:
    candidates: dict[str, set[str]] = defaultdict(set)
    for target_fp in sorted((translation_root / stem).glob("chunk_*.txt")):
        jp_fp = jp_dump / stem / target_fp.name
        if not jp_fp.exists():
            continue
        jp = read_records(jp_fp)
        target = read_records(target_fp)
        add_tm_records(candidates, jp, target)
    return unique_tm(candidates)


def prefill_records(
    jp: dict[Hashable, str],
    tm: dict[str, str],
    names: dict[str, str] | None = None,
    all_unmatched: bool = False,
) -> tuple[dict[Hashable, str], list[Hashable], int]:
    """Apply exact TM matches and optional L4/L5 name-plate matches."""
    out: dict[Hashable, str] = {}
    todo: list[Hashable] = []
    filled = 0
    names = names or {}
    for idx, source in jp.items():
        text = source
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
        elif all_unmatched or jp_like(text):
            todo.append(idx)
        out[idx] = text
    return out, todo, filled


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


def l12_chunk_records(reader, chunk, parts: tuple[int, ...]) -> dict[tuple[int, int], str]:
    """Decode L1/L2 records into the same exact-text TM model as L4/L5."""
    out: dict[tuple[int, int], str] = {}
    for part in parts:
        for index, raw in enumerate(chunk.part(part)):
            if raw:
                out[(part, index)] = reader.decode(raw, expand=False)
    return out


def prefill_l12(args, game, lang) -> None:
    """L1/L2 storage adapter around the common exact-match TM."""
    from langrisser.l12_scen import Reader, read_chunks
    from langrisser.l12_sceninsert import read_pack
    from langrisser.scen import load_charmap_csv

    parts = tuple(int(part) for part in args.parts.split(","))
    scen = Path(args.jp_scen) if args.jp_scen else (
        lang.work_root / "extracted" / "SCEN.DAT")
    font = load_charmap_csv(game.font_map)
    chunks = {chunk.index: chunk for chunk in read_chunks(scen.read_bytes())}
    candidates: dict[str, set[str]] = defaultdict(set)
    translation_root = (Path(args.translation_root)
                        if args.translation_root else lang.script_dir)
    for target_fp in sorted(translation_root.glob("chunk_*.txt")):
        match = re.search(r"chunk_(\d+)", target_fp.name)
        if not match:
            continue
        chunk = chunks.get(int(match.group(1)))
        if chunk is None:
            continue
        jp = l12_chunk_records(Reader(font, chunk), chunk, parts)
        add_tm_records(candidates, jp, read_pack(target_fp))
    tm = unique_tm(candidates)
    out_dir = (Path(args.out_dir) if args.out_dir
               else lang.work_root / f"wip_{lang.code}")
    out_dir.mkdir(parents=True, exist_ok=True)

    for cidx in args.chunks:
        chunk = chunks.get(cidx)
        if chunk is None:
            raise SystemExit(f"chunk {cidx:03d} does not exist in {scen}")
        jp = l12_chunk_records(Reader(font, chunk), chunk, parts)
        # L1/L2 phrase references can hide the entire Japanese wording behind
        # tags, so character-range detection cannot prove an unmatched record
        # translated. Every non-TM record requires manual review there.
        target, todo, filled = prefill_records(jp, tm, all_unmatched=True)
        lines: list[str] = []
        for part in parts:
            strings = chunk.part(part)
            lines.append(f"# part {part}  {len(strings)} strings")
            for index, raw in enumerate(strings):
                key = (part, index)
                if raw:
                    lines.append(f"{index}\t{target[key]}")
            lines.append("")
        out_fp = out_dir / f"chunk_{cidx:03d}.txt"
        out_fp.write_text("\n".join(lines), encoding="utf-8")
        print(f"chunk {cidx:03d}: records={len(jp)} prefilled={filled} "
              f"todo={len(todo)}")
        if todo:
            print("  todo records: " + ", ".join(
                f"{part}:{index}" for part, index in todo))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    add_game_args(ap)
    ap.add_argument("chunks", nargs="+", type=int)
    ap.add_argument("--jp-dump", default="work/l5/scriptdump")
    ap.add_argument("--jp-scen", default=None,
                    help="L1/L2 source SCEN.DAT (default: work/<game>/extracted/SCEN.DAT).")
    ap.add_argument("--translation-root", default=None,
                    help="Override the language pack's translated-text root.")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--stem", default="SCEN")
    ap.add_argument("--parts", default="7,6,5",
                    help="L1/L2 parts to stage (default: title, objectives, dialogue).")
    args = ap.parse_args()

    game = game_from_args(args)
    lang = language_from_args(args)
    if game.engine == "l12":
        prefill_l12(args, game, lang)
        return
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
        target, todo, filled = prefill_records(jp, tm, names)
        for idx in sorted(jp):
            out_lines.append(f"{idx}\t{target[idx]}")
        out_fp = out_dir / args.stem / jp_fp.name
        out_fp.parent.mkdir(parents=True, exist_ok=True)
        out_fp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print(f"chunk {cidx:03d}: records={len(jp)} prefilled={filled} todo={len(todo)}")
        print(f"  todo indices: {todo}")


if __name__ == "__main__":
    main()
