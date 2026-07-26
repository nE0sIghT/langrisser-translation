#!/usr/bin/env python3
"""Extract VM-to-text dialogue references from SCEN chunks.

The text stream only stores dialogue/event IDs as ``FB00 <id>``. The visible
speaker plate is selected by chunk VM bytecode before the text block. This
tool extracts the static VM command sites that reference those ``FB00`` IDs so
the speaker/name semantics can be reverse-engineered from game data instead
of maintained as hand-written maps.

The extractor is deliberately conservative: it emits evidence rows, not a
speaker assignment. The legacy pattern scan finds word-oriented records near
dialogue calls:

    <state> <fb_id> FF0B <flags> FFFF FFFF
    FF00 <fb_id> ... FF0B <flags> FFFF FFFF

These patterns are not trusted execution-order data. The VM is byte-oriented,
and some ``FF0B`` patterns can live inside skipped payload blocks. Use
``langrisser.speakers.py`` for the current speaker evidence ledger.
"""

from __future__ import annotations

import argparse
import csv
import struct
from pathlib import Path

from langrisser.scen import Codec, find_text_block, load_charmap_csv, read_chunk_spans, words_from_bytes


def u16(blob: bytes, off: int) -> int:
    return struct.unpack_from("<H", blob, off)[0]


def u32(blob: bytes, off: int) -> int:
    return struct.unpack_from("<I", blob, off)[0]


def decode_record(codec: Codec, words: list[int]) -> str:
    return codec.decode(words)


def chunk_fb_refs(chunk: bytes, block) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
    by_arg: dict[int, list[int]] = {}
    by_record: dict[int, list[int]] = {}
    for idx in range(1, block.record_count + 1):
        a, b = block.record_span(idx)
        words = words_from_bytes(chunk[a:b])
        args: list[int] = []
        for i, word in enumerate(words[:-1]):
            if word == 0xFB00:
                arg = words[i + 1]
                by_arg.setdefault(arg, []).append(idx)
                args.append(arg)
        if args:
            by_record[idx] = args
    return by_arg, by_record


def name_pool(codec: Codec, chunk: bytes, block) -> list[tuple[int, str]]:
    names: list[tuple[int, str]] = []
    for idx in range(1, block.record_count + 1):
        a, b = block.record_span(idx)
        words = words_from_bytes(chunk[a:b])
        if not words:
            continue
        if words[-1] == 0xFFFF:
            names.append((idx, decode_record(codec, words[:-1])))
            continue
        if words[-1] == 0xFFFE:
            break
    return names


def vm_block(chunk: bytes, block) -> tuple[int, bytes, int]:
    """Return (chunk-local VM offset, VM bytes, command stream start)."""
    if len(chunk) >= 0x44:
        off = u32(chunk, 0)
        if 0 <= off <= len(chunk) - 0x40 and u32(chunk, off) == 0x44:
            size = u32(chunk, off + 0x3C)
            if 0x40 <= size <= len(chunk) - off and off + size <= block.base:
                start = u32(chunk, off + 0x30)
                if start >= size:
                    start = 0x40
                return off, chunk[off : off + size], start
    return 0, chunk[: block.base], 0


def parse_vm_command_records(vm: bytes, stream_start: int) -> list[tuple[int, list[int]]]:
    records: list[tuple[int, list[int]]] = []
    p = stream_start
    while p + 8 <= len(vm):
        if u16(vm, p) == 0xFFFF:
            p += 2
            continue
        rec_start = p
        words: list[int] = []
        found = False
        while p + 2 <= len(vm) and len(words) < 192:
            words.append(u16(vm, p))
            p += 2
            if (
                len(words) >= 4
                and words[-4] == 0xFF0B
                and words[-2] == 0xFFFF
                and words[-1] == 0xFFFF
            ):
                found = True
                break
        if found:
            records.append((rec_start, words))
        else:
            p = rec_start + 2
    return records


def scan_vm_refs(chunk: bytes, block, fb_by_arg: dict[int, list[int]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    vm_off, vm, stream_start = vm_block(chunk, block)
    for rec_off, words in parse_vm_command_records(vm, stream_start):
        if len(words) < 5:
            continue
        term = len(words) - 4
        w0 = words[0]
        w1 = words[1] if len(words) > 1 else None
        if w0 == 0xFF00 and w1 in fb_by_arg:
            extra = words[2:term]
            rows.append(
                {
                    "vm_off": f"0x{vm_off + rec_off:04X}",
                    "vm_rel_off": f"0x{rec_off:04X}",
                    "form": "ff00",
                    "fb_id": f"{w1:04X}",
                    "record_indices": " ".join(str(i) for i in fb_by_arg[w1]),
                    "state_word": "",
                    "extra_words": " ".join(f"{x:04X}" for x in extra),
                    "has_ff0b": "1",
                    "tail_words": " ".join(f"{x:04X}" for x in words[:12]),
                }
            )
        elif w1 in fb_by_arg and w0 != 0xFF00:
            extra = words[2:term]
            rows.append(
                {
                    "vm_off": f"0x{vm_off + rec_off:04X}",
                    "vm_rel_off": f"0x{rec_off:04X}",
                    "form": "state",
                    "fb_id": f"{w1:04X}",
                    "record_indices": " ".join(str(i) for i in fb_by_arg[w1]),
                    "state_word": f"{w0:04X}",
                    "extra_words": " ".join(f"{x:04X}" for x in extra),
                    "has_ff0b": "1",
                    "tail_words": " ".join(f"{x:04X}" for x in words[:12]),
                }
            )
    return rows


def scan_file(path: Path, codec: Codec, chunk_filter: set[int] | None) -> list[dict[str, str]]:
    data = path.read_bytes()
    spans = read_chunk_spans(data)
    out: list[dict[str, str]] = []
    for cidx, (start, end) in enumerate(spans):
        if chunk_filter is not None and cidx not in chunk_filter:
            continue
        chunk = data[start:end]
        try:
            block = find_text_block(chunk)
        except ValueError:
            continue
        fb_by_arg, _fb_by_record = chunk_fb_refs(chunk, block)
        if not fb_by_arg:
            continue
        names = name_pool(codec, chunk, block)
        name_text = " | ".join(f"{idx}:{name}" for idx, name in names)
        refs = scan_vm_refs(chunk, block, fb_by_arg)
        matched = {int(r["fb_id"], 16) for r in refs}
        for row in refs:
            row.update(
                {
                    "source_file": path.name,
                    "chunk_index": str(cidx),
                    "chunk_start": f"0x{start:06X}",
                    "text_base": f"0x{block.base:04X}",
                    "text_records": str(block.record_count),
                    "name_pool": name_text,
                }
            )
            out.append(row)
        for arg, records in sorted(fb_by_arg.items()):
            if arg not in matched:
                out.append(
                    {
                        "source_file": path.name,
                        "chunk_index": str(cidx),
                        "chunk_start": f"0x{start:06X}",
                        "text_base": f"0x{block.base:04X}",
                        "text_records": str(block.record_count),
                        "name_pool": name_text,
                        "vm_off": "",
                        "vm_rel_off": "",
                        "form": "missing",
                        "fb_id": f"{arg:04X}",
                        "record_indices": " ".join(str(i) for i in records),
                        "state_word": "",
                        "extra_words": "",
                        "has_ff0b": "0",
                        "tail_words": "",
                    }
                )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scen", default="work/l5/extracted/SCEN.DAT")
    ap.add_argument("--scen2", default="work/l5/extracted/SCEN2.DAT")
    ap.add_argument("--font-map", default="data/common/font_mapping/groups_report.csv")
    ap.add_argument("--out", default="work/vm_dialog_refs/dialog_refs.csv")
    ap.add_argument("--chunk", type=int, action="append", help="scan only this chunk index; may repeat")
    args = ap.parse_args()

    codec = Codec(load_charmap_csv(Path(args.font_map)))
    chunk_filter = set(args.chunk) if args.chunk else None
    rows: list[dict[str, str]] = []
    for src in (Path(args.scen), Path(args.scen2)):
        rows.extend(scan_file(src, codec, chunk_filter))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "source_file",
        "chunk_index",
        "chunk_start",
        "text_base",
        "text_records",
        "name_pool",
        "vm_off",
        "vm_rel_off",
        "form",
        "fb_id",
        "record_indices",
        "state_word",
        "extra_words",
        "has_ff0b",
        "tail_words",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)
    matched = sum(1 for r in rows if r["form"] != "missing")
    missing = sum(1 for r in rows if r["form"] == "missing")
    print(f"wrote {out_path} rows={len(rows)} matched={matched} missing={missing}")


if __name__ == "__main__":
    main()
