#!/usr/bin/env python3
"""Validate translated chunks against the JP source structure.

Per record: the sequence of control tags (>= 0xE000, except the soft line
break FFFC) and the argument words of F600/FBxx must match the JP source
exactly. Also checks that every translated line encodes, reports leftover JP text
and validates the fixed-size SCEN/SCEN2 repack budget used by the builder.

The record checks are about the text and hold for any build of the game. The
budget is not: it measures the PS1 containers. A build that ships another one
passes `--budget-mode none` and gets the fit answered by the packer that owns
its format — otherwise a line that fits there but overflows a PS1 block would
fail a build PS1 has nothing to do with.
"""
import argparse
import re
from pathlib import Path

from langrisser.project import add_language_args, language_from_args
from langrisser.scen import (Codec, FORCE_PAGE_BREAK, TAG_RE, consumes_argument,
                        find_text_block, load_charmap_tbl, read_chunk_spans)
from langrisser.sceninsert import align_up, rebuild_chunk_fixed, trim_blobs_to_fit

LEGACY_UNALLOCATED_PUNCTUATION = set("!?;—–")


def read_records(path: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "\t" in raw and not raw.startswith("#"):
            idx, text = raw.split("\t", 1)
            out[int(idx)] = text
    return out


def control_signature(text: str) -> list[str]:
    sig = []
    tags = TAG_RE.findall(text)
    prev = None
    for h in tags:
        v = int(h, 16)
        take = False
        if prev is not None and consumes_argument(prev):
            take = True  # argument word, must match verbatim
        elif v >= 0xE000 and v not in (0xFFFC, 0xFFFD, 0xFFF4, 0xFFF3):
            # FFFC/FFFD are soft breaks; FFF4/FFF3 are highlight toggles
            # (checked for balance separately)
            take = True
        if take:
            sig.append(h.upper())
        prev = v
    return sig


def repacked_file_size(src: Path, records_by_chunk: dict[int, dict[int, str]],
                       codec: Codec) -> tuple[int, list[str]]:
    """Exact fixed-size repack simulation: rebuilds every edited chunk with
    the builder's own chunk/padding logic and applies the same container
    padding reclaim, so the reported total matches langrisser.sceninsert."""
    data = src.read_bytes()
    spans = read_chunk_spans(data)
    header_size = spans[0][0]
    blobs: list[bytes] = []
    problems: list[str] = []
    for cidx, (s, e) in enumerate(spans):
        chunk = data[s:e]
        edits = records_by_chunk.get(cidx, {})
        if edits:
            block = find_text_block(chunk)
            try:
                chunk = rebuild_chunk_fixed(chunk, block, edits, codec,
                                            f"{src.name} chunk {cidx:03d}")
            except SystemExit as exc:
                problems.append(str(exc))
        blobs.append(chunk)

    blobs, chunks_total = trim_blobs_to_fit(blobs, len(data) - header_size)
    return header_size + chunks_total, problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("chunks", nargs="*", type=int)
    ap.add_argument("--jp-dump", default="work/l5/scriptdump")
    ap.add_argument("--translation-root", default=None,
                    help="Override the language pack's translated-text root.")
    ap.add_argument("--tbl", default=None)
    ap.add_argument("--scen", default="work/l5/extracted/SCEN.DAT")
    ap.add_argument("--scen2", default="work/l5/extracted/SCEN2.DAT")
    ap.add_argument("--stem", default="SCEN")
    ap.add_argument("--budget-mode",
                    choices=("fixed-repack", "local", "block", "none"),
                    default="fixed-repack",
                    help="'none' checks the records only, for a build whose "
                         "container is not the PS1 one.")
    args = ap.parse_args()

    lang = language_from_args(args)
    unallocated_punctuation = (
        LEGACY_UNALLOCATED_PUNCTUATION - set(lang.single_chars)
    )
    translation_root = (Path(args.translation_root)
                        if args.translation_root else lang.dump_root)
    tbl = Path(args.tbl) if args.tbl else lang.tbl

    codec = Codec(load_charmap_tbl(tbl))
    measure = args.budget_mode != "none"
    data = Path(args.scen).read_bytes() if measure else b""
    spans = read_chunk_spans(data) if measure else []

    chunk_ids = args.chunks
    if not chunk_ids:
        chunk_ids = sorted(int(p.stem.split("_")[1])
                           for p in Path(translation_root, args.stem).glob("chunk_*.txt"))

    records_by_chunk: dict[int, dict[int, str]] = {}
    problems = 0
    for cidx in chunk_ids:
        jp = read_records(Path(args.jp_dump, args.stem, f"chunk_{cidx:03d}.txt"))
        target = read_records(
            Path(translation_root, args.stem, f"chunk_{cidx:03d}.txt")
        )
        records_by_chunk[cidx] = target
        body = 0
        jp_left = 0
        for idx in sorted(jp):
            if idx not in target:
                print(f"chunk {cidx} rec {idx}: MISSING in {lang.code.upper()}")
                problems += 1
                continue
            if control_signature(jp[idx]) != control_signature(target[idx]):
                print(f"chunk {cidx} rec {idx}: CONTROL TAG MISMATCH")
                problems += 1
            if target[idx].count("<$FFF4>") != target[idx].count("<$FFF3>"):
                print(f"chunk {cidx} rec {idx}: UNBALANCED highlight tags")
                problems += 1
            plain_text = TAG_RE.sub(
                "", target[idx].replace(FORCE_PAGE_BREAK, "")
            )
            found_unallocated = sorted(set(plain_text) & unallocated_punctuation)
            if found_unallocated:
                marks = "".join(found_unallocated)
                print(f"chunk {cidx} rec {idx}: unallocated punctuation: {marks}")
                problems += 1
            try:
                body += 2 * len(codec.encode(target[idx]))
            except ValueError as exc:
                print(f"chunk {cidx} rec {idx}: UNENCODABLE {exc}")
                problems += 1
            if re.search(
                r"[぀-ヺ一-鿿]",
                TAG_RE.sub("", target[idx]).replace("・", ""),
            ):
                jp_left += 1
        if not measure:
            print(f"chunk {cidx:03d}: body={body}"
                  + (f" jp_left={jp_left}" if jp_left else ""))
            continue
        s, e = spans[cidx]
        chunk = data[s:e]
        block = find_text_block(chunk)
        table_len = 2 + 2 * len(block.offsets)
        tz = len(chunk) - len(chunk.rstrip(b"\x00"))
        needed = table_len + body
        effective_size = block.size if needed <= block.size else align_up(needed, 4)
        effective_body = effective_size - table_len
        if args.budget_mode == "block":
            # The translated records must fit inside the original text block.
            # This preserves all following chunk data at byte-identical offsets.
            budget = block.size - table_len
        else:
            budget = block.size + (tz & ~1) - table_len
        compared_body = body if args.budget_mode == "block" else effective_body
        if compared_body <= budget:
            status = "OK"
        elif args.budget_mode == "fixed-repack":
            status = "REPACK"
        else:
            status = "OVER BUDGET"
        if compared_body > budget and args.budget_mode in ("local", "block"):
            problems += 1
        pad_note = (
            f" aligned_body={effective_body}"
            if needed > block.size and effective_body != body else ""
        )
        print(f"chunk {cidx:03d}: body={body}{pad_note} budget={budget} {status}"
              + (f" jp_left={jp_left}" if jp_left else ""))
    if args.budget_mode == "fixed-repack":
        for src in (Path(args.scen), Path(args.scen2)):
            total, repack_problems = repacked_file_size(src, records_by_chunk, codec)
            for msg in repack_problems:
                print(msg)
                problems += 1
            status = "OK" if total <= src.stat().st_size else "OVER BUDGET"
            if total > src.stat().st_size:
                problems += 1
            print(f"{src.name}: fixed-size repack={total} file_size={src.stat().st_size} {status}")
    if problems:
        raise SystemExit(f"{problems} problems found")
    print("all good")


if __name__ == "__main__":
    main()
