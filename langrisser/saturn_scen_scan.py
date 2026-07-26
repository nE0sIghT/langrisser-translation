#!/usr/bin/env python3
"""Read-only structural scan for Saturn SCEN.DAT."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from langrisser.binfmt import BE
from langrisser.offsetgroups import load_codemap
from langrisser.textcodec import decode_words
from langrisser.saturn_scen import SECTOR, TEXT_TERMINATORS
from langrisser.saturn_scen import parse_catalog as scen_parse_catalog


COMMON_CONTROL_WORDS = {0xFFFC, 0xFFFD, 0xFFF3, 0xFFF4, 0xFFF5, 0xFFF8}


def u32be(data: bytes, off: int) -> int:
    return BE.u32(data, off)


def u16be(data: bytes, off: int) -> int:
    return BE.u16(data, off)


def parse_resource_map(chunk: bytes, start: int, end: int, record_ids: list[int]) -> dict:
    size = end - start
    valid = 0 <= start <= end <= len(chunk) and size % 4 == 0
    rows = []
    if valid:
        for index, off in enumerate(range(start, end, 4)):
            rows.append({
                "index": index,
                "record_id": u16be(chunk, off),
                "resource_slot": chunk[off + 2],
                "variant": chunk[off + 3],
            })

    mapped_ids = {row["record_id"] for row in rows if row["record_id"] != 0xFFFF}
    unique_record_ids = {rid & 0xFFFF for rid in record_ids if rid != 0xFFFF}
    return {
        "valid": valid,
        "row_count": len(rows),
        "max_slot": max((row["resource_slot"] for row in rows), default=None),
        "slots": sorted({row["resource_slot"] for row in rows}),
        "variants": sorted({row["variant"] for row in rows}),
        "mapped_record_ids": len(mapped_ids),
        "indexed_record_ids": len(unique_record_ids),
        "indexed_record_ids_mapped": len(unique_record_ids & mapped_ids),
        "rows": rows,
    }


def parse_resource_table_summary(chunk: bytes, start: int, end: int) -> dict:
    size = end - start
    if not (0 <= start <= end <= len(chunk)) or size < 0x40:
        return {"valid": False, "size": size, "reason": "too small or out of range"}

    section = chunk[start:end]
    fixed_offsets = [u32be(section, index * 4) for index in range(14)]
    local_index_table = parse_local_index_table(section, u32be(section, 0x3C))
    return {
        "valid": True,
        "size": size,
        "first_14_offsets": fixed_offsets,
        "first_14_offsets_sorted": fixed_offsets == sorted(fixed_offsets),
        "first_14_offsets_in_range": all(0 <= value <= size for value in fixed_offsets),
        # These fields are structurally stable in the current image, but their
        # semantic meaning is still under investigation.
        "field_38": u32be(section, 0x38),
        "field_3c": u32be(section, 0x3C),
        "field_40": u32be(section, 0x40) if size >= 0x44 else None,
        "local_index_table": local_index_table,
    }


def read_words_until_section_end(section: bytes, off: int, end: int, limit: int) -> list[int]:
    words = []
    cursor = off
    while cursor + 2 <= end and len(words) < limit:
        word = u16be(section, cursor)
        words.append(word)
        cursor += 2
        if word in TEXT_TERMINATORS:
            break
    return words


def span_contains_terminator(section: bytes, off: int, end: int) -> bool:
    cursor = off
    while cursor + 2 <= end:
        if u16be(section, cursor) in TEXT_TERMINATORS:
            return True
        cursor += 2
    return False


def parse_local_index_table(section: bytes, base: int) -> dict:
    if not (0 <= base + 6 <= len(section)):
        return {"valid": False, "reason": "base out of range", "base": base}
    total_size = u32be(section, base)
    if not (4 <= total_size <= len(section) - base):
        return {
            "valid": False,
            "reason": "total size out of range",
            "base": base,
            "total_size": total_size,
        }
    first_offset = u16be(section, base + 4)
    if first_offset < 6 or first_offset > total_size or (first_offset - 4) % 2:
        return {
            "valid": False,
            "reason": "invalid first offset",
            "base": base,
            "total_size": total_size,
            "first_offset": first_offset,
        }

    count = (first_offset - 4) // 2
    offsets = [u16be(section, base + 4 + index * 2) for index in range(count)]
    sorted_offsets = offsets == sorted(offsets)
    in_range = all(first_offset <= offset < total_size for offset in offsets)
    entries_preview = []
    terminated_count = 0
    for index, offset in enumerate(offsets):
        next_offset = offsets[index + 1] if index + 1 < len(offsets) else total_size
        start = base + offset
        end = base + next_offset
        terminated = span_contains_terminator(section, start, end)
        if terminated:
            terminated_count += 1
        if index < 16:
            words = read_words_until_section_end(section, start, end, 24)
            entries_preview.append({
                "index": index,
                "offset": offset,
                "span_size": next_offset - offset,
                "terminated": terminated,
                "prefix": [f"{word:04X}" for word in words[:16]],
            })

    return {
        "valid": sorted_offsets and in_range,
        "base": base,
        "total_size": total_size,
        "entry_count": count,
        "first_offset": first_offset,
        "offsets_sorted": sorted_offsets,
        "offsets_in_range": in_range,
        "terminated_count": terminated_count,
        "entries_preview": entries_preview,
    }


def parse_catalog(data: bytes) -> dict:
    blocks = scen_parse_catalog(data)
    count = u32be(data, 0)
    raw_entries = [
        {
            "index": index,
            "start_sector": start_offset // SECTOR,
            "start_offset": start_offset,
            "used_size": used_size,
        }
        for index, (start_offset, used_size) in enumerate(blocks)
    ]

    entries = []
    for i, ent in enumerate(raw_entries):
        start = ent["start_offset"]
        if i + 1 < len(raw_entries):
            alloc_size = raw_entries[i + 1]["start_offset"] - start
        else:
            alloc_size = len(data) - start
        used_size = ent["used_size"]
        padding = data[start + used_size : start + alloc_size]
        entries.append(ent | {
            "alloc_size": alloc_size,
            "padding_size": alloc_size - used_size,
            "padding_zero": padding == b"\x00" * len(padding),
        })
    return {
        "count": count,
        "table_size": 4 + count * 8 if raw_entries else None,
        "entries": entries,
    }


def parse_chunk_header(data: bytes, ent: dict) -> dict:
    start = ent["start_offset"]
    used_size = ent["used_size"]
    chunk = data[start : start + used_size]
    if len(chunk) < 0x30:
        return {"valid": False, "reason": "chunk shorter than fixed header"}

    resource_table_offset = u32be(chunk, 0x00)
    category = u16be(chunk, 0x04)
    sub_id = u16be(chunk, 0x06)
    section0_offset = u32be(chunk, 0x08)
    section1_offset = u32be(chunk, 0x0C)
    record_index_offset = u32be(chunk, 0x10)
    resource_map_offset = u32be(chunk, 0x14)
    field_18 = u32be(chunk, 0x18)
    field_1c = u32be(chunk, 0x1C)
    field_20 = u32be(chunk, 0x20)
    field_24 = u32be(chunk, 0x24)
    field_28 = u32be(chunk, 0x28)
    field_2c = u32be(chunk, 0x2C)

    offsets = [
        section0_offset,
        section1_offset,
        record_index_offset,
        resource_map_offset,
        resource_table_offset,
        used_size,
    ]
    valid_offsets = (
        all(0 <= value <= used_size for value in offsets)
        and offsets == sorted(offsets)
    )

    sections = []
    names = [
        "section0",
        "section1",
        "record_index",
        "resource_map",
        "resource_table",
    ]
    for name, begin, end in zip(names, offsets, offsets[1:]):
        sections.append({
            "name": name,
            "start": begin,
            "end": end,
            "size": end - begin,
        })

    record_index = {"valid": False, "count": None, "records": []}
    if 0 <= record_index_offset + 4 <= used_size:
        count = u32be(chunk, record_index_offset)
        table_end = record_index_offset + 4 + count * 8
        valid = 0 <= count <= 1000 and table_end <= used_size
        records = []
        if valid:
            for i in range(count):
                off = record_index_offset + 4 + i * 8
                records.append({
                    "index": i,
                    "record_id": u32be(chunk, off),
                    "record_offset": u32be(chunk, off + 4),
                })
            valid = all(0 <= rec["record_offset"] <= resource_map_offset for rec in records)
            unique_offsets = sorted({rec["record_offset"] for rec in records})
            span_end_by_offset = {
                off: unique_offsets[i + 1] if i + 1 < len(unique_offsets) else resource_map_offset
                for i, off in enumerate(unique_offsets)
            }
            for rec in records:
                span_end = span_end_by_offset.get(rec["record_offset"])
                if span_end is not None:
                    rec["span_end"] = span_end
                    rec["span_size"] = span_end - rec["record_offset"]
        record_index = {
            "valid": valid,
            "count": count,
            "table_end": table_end,
            "first_record_starts_after_table": (
                bool(records) and records[0]["record_offset"] == table_end
            ),
            "offsets_sorted": all(
                records[i]["record_offset"] <= records[i + 1]["record_offset"]
                for i in range(len(records) - 1)
            ),
            "records": records,
        }

    resource_map = parse_resource_map(
        chunk,
        resource_map_offset,
        resource_table_offset,
        [rec["record_id"] for rec in record_index["records"]],
    )
    resource_table = parse_resource_table_summary(chunk, resource_table_offset, used_size)

    return {
        "valid": valid_offsets and record_index["valid"] and resource_map["valid"],
        "category": category,
        "sub_id": sub_id,
        "resource_table_offset": resource_table_offset,
        "section0_offset": section0_offset,
        "section1_offset": section1_offset,
        "record_index_offset": record_index_offset,
        "resource_map_offset": resource_map_offset,
        "field_18": field_18,
        "field_1c": field_1c,
        "field_20": field_20,
        "field_24": field_24,
        "field_28": field_28,
        "field_2c": field_2c,
        "sections": sections,
        "record_index": record_index,
        "resource_map": resource_map,
        "resource_table": resource_table,
    }


def plausible_token(word: int) -> bool:
    return (
        word == 0
        or 0x0001 <= word <= 0x08FF
        or word in COMMON_CONTROL_WORDS
        or word in TEXT_TERMINATORS
        or 0xFB00 <= word <= 0xFFFF
    )


def score_words(words: list[int]) -> int:
    printable = sum(1 for word in words if 0x0001 <= word <= 0x08FF)
    controls = sum(1 for word in words if word >= 0xFB00)
    return printable * 2 + controls


def scan_token_streams(data: bytes, start: int, end: int, min_words: int) -> list[dict]:
    hits = []
    pos = start + (start & 1)
    while pos + min_words * 2 <= end:
        words = []
        cursor = pos
        while cursor + 2 <= end:
            word = u16be(data, cursor)
            if not plausible_token(word):
                break
            words.append(word)
            cursor += 2
            if word in TEXT_TERMINATORS and len(words) >= min_words:
                break
            if len(words) >= 160:
                break
        if len(words) >= min_words and any(word in TEXT_TERMINATORS for word in words):
            hits.append({
                "offset": f"0x{pos:06X}",
                "words": len(words),
                "score": score_words(words),
                "prefix": [f"{word:04X}" for word in words[:32]],
            })
            pos = cursor
        else:
            pos += 2
    return hits


def add_decoded_previews(hits: list[dict], data: bytes, codemap: dict[int, str], limit: int) -> None:
    for hit in hits[:limit]:
        off = int(hit["offset"], 16)
        count = min(hit["words"], 80)
        words = list(struct.unpack_from(f">{count}H", data, off))
        hit["preview"] = decode_words(words, codemap)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scen", default="work/build/saturn/SCEN.DAT")
    ap.add_argument("--tbl", default="data/common/tables/lang5_jp.tbl")
    ap.add_argument("--out", default="work/build/saturn/scen_scan.json")
    ap.add_argument("--scan-start", type=lambda value: int(value, 0), default=0)
    ap.add_argument("--scan-end", type=lambda value: int(value, 0), default=0x60000)
    ap.add_argument("--min-words", type=int, default=8)
    ap.add_argument("--preview-limit", type=int, default=80)
    args = ap.parse_args()

    data = Path(args.scen).read_bytes()
    codemap = load_codemap(args.tbl)
    catalog = parse_catalog(data)
    chunks = []
    for ent in catalog["entries"]:
        chunks.append(ent | {"header": parse_chunk_header(data, ent)})
    scan_end = min(args.scan_end, len(data))
    hits = scan_token_streams(data, args.scan_start, scan_end, args.min_words)
    hits.sort(key=lambda hit: (-hit["score"], int(hit["offset"], 16)))
    add_decoded_previews(hits, data, codemap, args.preview_limit)
    out_data = {
        "file_size": len(data),
        "catalog": catalog,
        "chunks": chunks,
        "scan": {
            "start": f"0x{args.scan_start:06X}",
            "end": f"0x{scan_end:06X}",
            "min_words": args.min_words,
            "hit_count": len(hits),
            "top_hits": hits[:args.preview_limit],
        },
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"found {len(hits)} token-stream candidates -> {out}")


if __name__ == "__main__":
    main()
