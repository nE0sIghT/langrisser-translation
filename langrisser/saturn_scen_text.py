#!/usr/bin/env python3
"""Dump the full Saturn SCEN.DAT scenario text pool in read-only mode.

The scenario text lives in each payload block's ``resource_table.field_3c``
local index table (see docs/SATURN_DISC_FORMAT.md). This tool walks all 131
payload blocks, reads every indexed text entry and emits a stable
``(chunk_index, entry_index)`` record set, mirroring the PS1
``work/l5/scriptdump/all_records.csv`` shape so the two platforms can be aligned.

Decoding uses the PS1 Japanese token table. Kana and control words match the
Saturn stream exactly; a subset of kanji decodes incorrectly because the Saturn
kanji banks are reordered relative to PS1. The raw token ids are always emitted
so downstream alignment does not depend on kanji parity.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from langrisser.offsetgroups import load_codemap
from langrisser.saturn_scen import TEXT_TERMINATORS, local_index_entries, parse_catalog

SOFT_BREAK = 0xFFFC


def decode_tokens(words: list[int], codemap: dict[int, str]) -> str:
    out: list[str] = []
    for word in words:
        if word in TEXT_TERMINATORS:
            out.append("<$%04X>" % word)
        elif word == SOFT_BREAK:
            out.append("<$FFFC>")
        elif word >= 0xFB00:
            out.append("<$%04X>" % word)
        elif word == 0:
            out.append("")
        else:
            out.append(codemap.get(word, "{?%04X}" % word))
    return "".join(out)


def dump(data: bytes, codemap: dict[int, str]) -> dict:
    catalog = parse_catalog(data)
    chunks = []
    records = []
    bad = []
    for chunk_index, (start, used) in enumerate(catalog):
        entries = local_index_entries(data, start, used)
        if entries is None:
            bad.append(chunk_index)
            continue
        text_entries = 0
        for entry_index, words in enumerate(entries):
            is_text = any(word in TEXT_TERMINATORS for word in words)
            if is_text:
                text_entries += 1
            records.append({
                "chunk_index": chunk_index,
                "entry_index": entry_index,
                "word_count": len(words),
                "is_text": is_text,
                "tokens": " ".join("%04X" % word for word in words),
                "jp": decode_tokens(words, codemap),
            })
        chunks.append({"chunk_index": chunk_index, "entry_count": len(entries),
                       "text_entries": text_entries})
    return {
        "file_size": len(data),
        "chunk_count": len(catalog),
        "parsed_chunks": len(chunks),
        "bad_chunks": bad,
        "entry_count": len(records),
        "text_entry_count": sum(1 for r in records if r["is_text"]),
        "chunks": chunks,
        "records": records,
    }


def write_csv(result: dict, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["chunk_index", "entry_index", "word_count", "is_text", "jp"])
        for rec in result["records"]:
            writer.writerow([rec["chunk_index"], rec["entry_index"],
                             rec["word_count"], int(rec["is_text"]), rec["jp"]])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scen", default="work/l5/build/saturn/SCEN.DAT")
    ap.add_argument("--tbl", default="data/common/tables/lang5_jp.tbl")
    ap.add_argument("--out", default="work/l5/build/saturn/scen_text.json")
    ap.add_argument("--out-csv", default="work/l5/build/saturn/scen_text.csv")
    args = ap.parse_args()

    data = Path(args.scen).read_bytes()
    codemap = load_codemap(args.tbl)
    result = dump(data, codemap)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.out_csv:
        write_csv(result, Path(args.out_csv))
    print(
        f"parsed {result['parsed_chunks']}/{result['chunk_count']} chunks, "
        f"{result['entry_count']} entries "
        f"({result['text_entry_count']} text) -> {out}"
    )
    if result["bad_chunks"]:
        print(f"unparsed chunks: {result['bad_chunks']}")


if __name__ == "__main__":
    main()
