#!/usr/bin/env python3
"""Annotate the target-language script dump with the speaker plate for each record.

Inserts a ``# spk: <name>`` comment line before every dialogue record in
``<pack>/SCEN/chunk_*.txt`` so the translation can be read with its
speaker in view. ``SCEN`` is the canonical translation source; ``SCEN2.DAT`` is
rebuilt from the same dump, so this script does not touch a ``SCEN2`` dump unless
``--include-scen2`` is requested explicitly. The
speaker comes from the same display-command extraction the wrapper uses
(``semantic_plate_slots``: name-pool slot at display byte +9, record =
pool_size + 1 + text id), and the name from the chunk's own translated plate
records (1..pool_size). ``# spk:`` lines (and all ``#`` lines) are ignored by
rewrap.py and sceninsert.py, so this never affects the build.

Idempotent: existing ``# spk:`` lines are stripped and rewritten, so it can be
re-run after re-translating or re-extracting speakers. Run after edits to refresh.
"""
import argparse
import re
from pathlib import Path

from langrisser.project import add_language_args, language_from_args
from langrisser.rewrap import semantic_plate_slots, speaker_pool_sizes

TAG_RE = re.compile(r"<\$[0-9A-Fa-f]{4}>")
SPK_RE = re.compile(r"^#\s*spk:")


def plate_names(lines: list[str], pool_size: int) -> dict[int, str]:
    """Record index -> translated plate name, from records 1..pool_size."""
    names: dict[int, str] = {}
    for raw in lines:
        if "\t" not in raw or raw.startswith("#"):
            continue
        idx, text = raw.split("\t", 1)
        if idx.isdigit() and 1 <= int(idx) <= pool_size:
            names[int(idx)] = TAG_RE.sub("", text).replace("<$FFFF>", "").strip()
    return names


def label(slot: int | None, names: dict[int, str]) -> str:
    if slot is None:
        return "(no plate)"
    if slot < 0:
        return "(location/crowd)"
    return names.get(slot + 1, f"(slot {slot})")


def annotate_file(fp: Path, slots: dict[int, int | None], pool_size: int) -> int:
    lines = fp.read_text(encoding="utf-8").splitlines()
    names = plate_names(lines, pool_size)
    out: list[str] = []
    added = 0
    for raw in lines:
        if SPK_RE.match(raw):
            continue  # drop stale annotation; regenerated below
        if "\t" in raw and not raw.startswith("#"):
            idx = raw.split("\t", 1)[0]
            if idx.isdigit() and int(idx) > pool_size and int(idx) in slots:
                out.append(f"# spk: {label(slots[int(idx)], names)}")
                added += 1
        out.append(raw)
    fp.write_text("\n".join(out) + "\n", encoding="utf-8")
    return added


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--translation-root", default=None,
                    help="Override the language pack's translated-text root.")
    ap.add_argument("--scen", default="work/l5/extracted/SCEN.DAT")
    ap.add_argument("--include-scen2", action="store_true",
                    help="Also annotate an existing SCEN2 dump. Default is SCEN only.")
    args = ap.parse_args()
    lang = language_from_args(args)
    dump_root = (Path(args.translation_root)
                 if args.translation_root else lang.dump_root)

    scen = Path(args.scen)
    slots_by_chunk = semantic_plate_slots(scen)
    pool_sizes = speaker_pool_sizes(scen)

    root = dump_root
    if root.name in {"SCEN", "SCEN2"}:
        target_dirs = [root]
    else:
        target_dirs = [root / "SCEN"]
        if args.include_scen2:
            target_dirs.append(root / "SCEN2")

    total = 0
    touched = []
    for target_dir in target_dirs:
        if not target_dir.exists():
            continue
        touched.append(str(target_dir))
        for fp in sorted(target_dir.glob("chunk_*.txt")):
            chunk_idx = int(fp.stem.split("_")[1])
            slots = slots_by_chunk.get(chunk_idx, {})
            pool_size = pool_sizes.get(chunk_idx) or 0
            if not slots or not pool_size:
                continue
            total += annotate_file(fp, slots, pool_size)
    scope = ", ".join(touched) if touched else "(no matching directories)"
    print(f"annotated {total} records with # spk: across {scope}")


if __name__ == "__main__":
    main()
