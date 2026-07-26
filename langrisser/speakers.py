#!/usr/bin/env python3
"""Extract dialogue speaker evidence from Langrisser V SCEN VM blocks.

Text records only contain ``FB00 <id>`` dialogue IDs. The speaker plate is
selected by chunk VM command records before the text block. This module keeps
the extraction conservative: static VM sites are exposed as evidence, but no
speaker mapping is marked confirmed until it follows decoded runtime behavior.
"""

from __future__ import annotations

import argparse
import csv
import struct
from dataclasses import dataclass
from pathlib import Path

from langrisser.scen import Codec, find_text_block, load_charmap_csv, read_chunk_spans, words_from_bytes


def u16(blob: bytes, off: int) -> int:
    return struct.unpack_from("<H", blob, off)[0]


def u32(blob: bytes, off: int) -> int:
    return struct.unpack_from("<I", blob, off)[0]


@dataclass(frozen=True)
class ActorPlateEntry:
    key: int
    field2: int
    field3: int


@dataclass(frozen=True)
class SpeakerSite:
    source_file: str
    chunk_index: int
    chunk_start: int
    text_base: int
    text_records: int
    vm_off: int | None
    vm_rel_off: int | None
    form: str
    fb_id: int
    record_indices: tuple[int, ...]
    speaker_slot: int | None
    speaker_record: int | None
    speaker_name: str
    confidence: str
    state_word: int | None
    extra_words: tuple[int, ...]
    tail_words: tuple[int, ...]
    name_pool: tuple[tuple[int, str], ...]
    actor_table: tuple[ActorPlateEntry, ...]


@dataclass(frozen=True)
class VMTraceRow:
    source_file: str
    chunk_index: int
    chunk_start: int
    vm_off: int
    stream_start: int
    path_id: int
    step: int
    rel_off: int
    opcode: int
    kind: str
    length: int | None
    next_rel_off: int | None
    branch_rel_offs: tuple[int, ...]
    payload: bytes
    stop: bool
    note: str
    display_text_id: int | None
    display_raw_speaker: int | None
    display_field1_high: int | None
    display_field1_low: int | None
    display_flags: int | None
    display_byte8: int | None
    display_byte9: int | None


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


def actor_plate_table(chunk: bytes) -> tuple[ActorPlateEntry, ...]:
    """Return the chunk-local table loaded into runtime global 0x800eba38.

    The loader at 0x8003b44c uses header u32 +0x14 as the table offset and the
    low byte of header u32 +0x2c as the entry count. The lookup routine at
    0x800b2da4 treats entries as u16 key, u8 field2, u8 field3.
    """
    if len(chunk) < 0x30:
        return ()
    off = u32(chunk, 0x14)
    count = u32(chunk, 0x2C) & 0xFF
    if count == 0 or off + count * 4 > len(chunk):
        return ()
    rows: list[ActorPlateEntry] = []
    for idx in range(count):
        pos = off + idx * 4
        rows.append(
            ActorPlateEntry(
                key=u16(chunk, pos),
                field2=chunk[pos + 2],
                field3=chunk[pos + 3],
            )
        )
    return tuple(rows)


def parse_ff0b_command_records(vm: bytes, stream_start: int) -> list[tuple[int, list[int]]]:
    """Find legacy evidence records ending in ``FF0B flags FFFF FFFF``.

    These patterns are useful cross-references for FB00 IDs, but they are not
    trusted execution-order data. The real VM is bytecode-driven and can skip
    such patterns as payload.
    """
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


def _helper_80022b24_length(vm: bytes, off: int) -> int | None:
    """Return byte count consumed by helper 0x80022b24 at VM pointer ``off``."""
    if off + 2 > len(vm):
        return None
    return 4 if vm[off] == 0xFE else 2


def _vm_step(vm: bytes, p: int) -> tuple[str, int | None, bool, str, tuple[int, ...]]:
    """Decode one VM command enough to advance a conservative byte trace.

    This intentionally models only instruction length and obvious command
    class. It does not infer speaker names.
    """
    if p >= len(vm):
        return "eof", None, True, "instruction pointer is outside VM block", ()

    op = vm[p]
    remaining = len(vm) - p

    if op == 0x00:
        if remaining < 4:
            return "skip_block_truncated", None, True, "opcode 00 needs a u16 skip length", ()
        skip_len = u16(vm, p + 2)
        length = 4 + skip_len
        if p + length > len(vm):
            return (
                "skip_block_invalid",
                length,
                True,
                f"opcode 00 skip length 0x{skip_len:04X} exits VM block",
                (),
            )
        return "skip_block", length, False, f"opcode 00 skip length 0x{skip_len:04X}", ()

    if op in (0x01, 0x03):
        return (
            "control_flow_unimplemented",
            None,
            True,
            "indexed call/jump changes VM pointer through the VM script table",
            (),
        )

    if op == 0x02:
        return "return_unimplemented", 1, True, "return needs call-stack modeling", ()

    if op in (0x04, 0x05, 0x09):
        helper_len = _helper_80022b24_length(vm, p + 2)
        if helper_len is None:
            return "actor_state_truncated", None, True, "0x80025110 helper operands truncated", ()
        length = 1 + 1 + helper_len + 2
        if p + length > len(vm):
            return "actor_state_truncated", None, True, "0x80025110 operands exceed VM block", ()
        return (
            "actor_state_25110",
            length,
            False,
            f"0x80025110; helper 0x80022b24 consumes {helper_len} bytes",
            (),
        )

    if op in (0x06, 0x07, 0x08, 0x0A):
        if remaining < 4:
            return "actor_state_25454_truncated", None, True, "0x80025454 operands truncated", ()
        return "actor_state_25454", 4, False, "0x80025454 consumes u8 + u16 operands", ()

    if 0x0B <= op <= 0x10:
        if remaining < 12:
            return "display_truncated", None, True, "0x80024424 display command truncated", ()
        return "display_24424", 12, False, "0x80024424 display/window command", ()

    if op == 0x17:
        if remaining < 2:
            return "actor_position_truncated", None, True, "opcode 17 first operand truncated", ()
        if vm[p + 1] != 0:
            if remaining < 6:
                return "actor_position_truncated", None, True, "opcode 17 nonzero form truncated", ()
            return "actor_position", 6, False, "opcode 17 nonzero form", ()
        helper_len = _helper_80022b24_length(vm, p + 2)
        if helper_len is None:
            return "actor_position_truncated", None, True, "opcode 17 helper operands truncated", ()
        length = 1 + 1 + helper_len + 2
        if p + length > len(vm):
            return "actor_position_truncated", None, True, "opcode 17 operands exceed VM block", ()
        return "actor_position", length, False, f"opcode 17 zero form; helper consumes {helper_len} bytes", ()

    if op in (0x14, 0x15, 0x25, 0x6F, 0x78):
        if remaining < 4:
            return "state_len4_truncated", None, True, "known 4-byte state command truncated", ()
        return "state_len4", 4, False, "known 4-byte state command", ()

    if op in (0x11, 0x16, 0x18, 0x19, 0x1A, 0x1B, 0x23, 0x24, 0x26, 0x63):
        if remaining < 2:
            return "state_len2_truncated", None, True, "known 2-byte state command truncated", ()
        return "state_len2", 2, False, "known 2-byte state command", ()

    if op == 0x7B:
        if remaining < 6:
            return "conditional_7b_truncated", None, True, "opcode 7B operands truncated", ()
        no_mismatch = p + 2 + u16(vm, p + 2) + 2
        mismatch = p + 4 + u16(vm, p + 4) + 2
        branches = tuple(
            target for target in (no_mismatch, mismatch) if p < target <= len(vm)
        )
        return (
            "conditional_25a1c",
            None,
            False,
            "opcode 7B has two possible branch targets through 0x80025a1c",
            branches,
        )

    return "unknown_opcode", None, True, "opcode length not decoded yet", ()


def trace_vm_bytecode(
    *,
    source_file: str,
    chunk_index: int,
    chunk_start: int,
    chunk: bytes,
    block,
    max_steps: int = 512,
) -> list[VMTraceRow]:
    vm_off, vm, stream_start = vm_block(chunk, block)
    rows: list[VMTraceRow] = []
    queue: list[tuple[int, int]] = [(stream_start, 0)]
    next_path_id = 1
    visited: set[int] = set()
    while queue and len(rows) < max_steps:
        p, path_id = queue.pop(0)
        if p in visited:
            continue
        visited.add(p)
        if p >= len(vm):
            continue
        kind, length, stop, note, branches = _vm_step(vm, p)
        opcode = vm[p]
        next_rel_off = p + length if length is not None else None
        payload_end = p + (length if length is not None and length > 0 else min(16, len(vm) - p))
        payload = vm[p + 1 : min(payload_end, len(vm))]

        display_text_id = None
        display_raw_speaker = None
        display_field1_high = None
        display_field1_low = None
        display_flags = None
        display_byte8 = None
        display_byte9 = None
        if kind == "display_24424" and p + 12 <= len(vm):
            display_raw_speaker = vm[p + 1]
            field1 = vm[p + 2]
            display_field1_low = field1 & 0x0F
            display_field1_high = field1 >> 4
            display_flags = vm[p + 3]
            display_byte8 = vm[p + 8]
            display_byte9 = vm[p + 9]
            display_text_id = u16(vm, p + 10)

        rows.append(
            VMTraceRow(
                source_file=source_file,
                chunk_index=chunk_index,
                chunk_start=chunk_start,
                vm_off=vm_off,
                stream_start=stream_start,
                path_id=path_id,
                step=len(rows),
                rel_off=p,
                opcode=opcode,
                kind=kind,
                length=length,
                next_rel_off=next_rel_off,
                branch_rel_offs=branches,
                payload=payload,
                stop=stop,
                note=note,
                display_text_id=display_text_id,
                display_raw_speaker=display_raw_speaker,
                display_field1_high=display_field1_high,
                display_field1_low=display_field1_low,
                display_flags=display_flags,
                display_byte8=display_byte8,
                display_byte9=display_byte9,
            )
        )
        if branches:
            for target in branches:
                if target not in visited:
                    queue.append((target, next_path_id))
                    next_path_id += 1
            continue
        if stop or length is None or next_rel_off is None or next_rel_off <= p:
            continue
        if next_rel_off not in visited:
            queue.insert(0, (next_rel_off, path_id))
    return rows


def slot_to_name(slot: int | None, names: list[tuple[int, str]]) -> tuple[int | None, str]:
    if slot is None or slot <= 0 or slot > len(names):
        return None, ""
    return names[slot - 1]


def _site(
    *,
    source_file: str,
    chunk_index: int,
    chunk_start: int,
    block,
    names: list[tuple[int, str]],
    fb_id: int,
    records: list[int],
    vm_off: int | None,
    vm_rel_off: int | None,
    form: str,
    speaker_slot: int | None,
    confidence: str,
    state_word: int | None = None,
    extra_words: list[int] | tuple[int, ...] = (),
    tail_words: list[int] | tuple[int, ...] = (),
    actor_table: tuple[ActorPlateEntry, ...] = (),
) -> SpeakerSite:
    speaker_record, speaker_name = slot_to_name(speaker_slot, names)
    if speaker_record is None and confidence == "confirmed":
        confidence = "unresolved"
    return SpeakerSite(
        source_file=source_file,
        chunk_index=chunk_index,
        chunk_start=chunk_start,
        text_base=block.base,
        text_records=block.record_count,
        vm_off=vm_off,
        vm_rel_off=vm_rel_off,
        form=form,
        fb_id=fb_id,
        record_indices=tuple(records),
        speaker_slot=speaker_slot,
        speaker_record=speaker_record,
        speaker_name=speaker_name,
        confidence=confidence,
        state_word=state_word,
        extra_words=tuple(extra_words),
        tail_words=tuple(tail_words),
        name_pool=tuple(names),
        actor_table=actor_table,
    )


def scan_vm_speakers(
    *,
    source_file: str,
    chunk_index: int,
    chunk_start: int,
    chunk: bytes,
    block,
    codec: Codec,
) -> list[SpeakerSite]:
    fb_by_arg, _fb_by_record = chunk_fb_refs(chunk, block)
    if not fb_by_arg:
        return []

    names = name_pool(codec, chunk, block)
    table = actor_plate_table(chunk)
    vm_off, vm, stream_start = vm_block(chunk, block)
    rows: list[SpeakerSite] = []
    for rec_off, words in parse_ff0b_command_records(vm, stream_start):
        if len(words) < 5:
            continue
        term = len(words) - 4
        w0 = words[0]
        w1 = words[1] if len(words) > 1 else None
        state_opcode = w0 & 0x00FF
        state_param = (w0 >> 8) & 0x00FF
        extra = words[2:term]

        if w1 in fb_by_arg and state_opcode == 0x00 and state_param not in (0x00, 0xFF):
            rows.append(
                _site(
                    source_file=source_file,
                    chunk_index=chunk_index,
                    chunk_start=chunk_start,
                    block=block,
                    names=names,
                    fb_id=w1,
                    records=fb_by_arg[w1],
                    vm_off=vm_off + rec_off,
                    vm_rel_off=rec_off,
                    form="vm_state_byte_rejected",
                    speaker_slot=None,
                    confidence="unresolved",
                    state_word=w0,
                    extra_words=extra,
                    tail_words=words[:12],
                    actor_table=table,
                )
            )
        elif w0 == 0xFF00 and w1 in fb_by_arg:
            rows.append(
                _site(
                    source_file=source_file,
                    chunk_index=chunk_index,
                    chunk_start=chunk_start,
                    block=block,
                    names=names,
                    fb_id=w1,
                    records=fb_by_arg[w1],
                    vm_off=vm_off + rec_off,
                    vm_rel_off=rec_off,
                    form="vm_ff00",
                    speaker_slot=None,
                    confidence="unresolved",
                    state_word=w0,
                    extra_words=extra,
                    tail_words=words[:12],
                    actor_table=table,
                )
            )
        elif w1 in fb_by_arg:
            rows.append(
                _site(
                    source_file=source_file,
                    chunk_index=chunk_index,
                    chunk_start=chunk_start,
                    block=block,
                    names=names,
                    fb_id=w1,
                    records=fb_by_arg[w1],
                    vm_off=vm_off + rec_off,
                    vm_rel_off=rec_off,
                    form="vm_indirect",
                    speaker_slot=None,
                    confidence="unresolved",
                    state_word=w0,
                    extra_words=extra,
                    tail_words=words[:12],
                    actor_table=table,
                )
            )

    for arg, records in sorted(fb_by_arg.items()):
        if not any(r.fb_id == arg for r in rows):
            rows.append(
                _site(
                    source_file=source_file,
                    chunk_index=chunk_index,
                    chunk_start=chunk_start,
                    block=block,
                    names=names,
                    fb_id=arg,
                    records=records,
                    vm_off=None,
                    vm_rel_off=None,
                    form="missing",
                    speaker_slot=None,
                    confidence="unresolved",
                    actor_table=table,
                )
            )
    return rows


def scan_file(path: Path, codec: Codec, chunk_filter: set[int] | None = None) -> list[SpeakerSite]:
    data = path.read_bytes()
    spans = read_chunk_spans(data)
    rows: list[SpeakerSite] = []
    for cidx, (start, end) in enumerate(spans):
        if chunk_filter is not None and cidx not in chunk_filter:
            continue
        chunk = data[start:end]
        try:
            block = find_text_block(chunk)
        except ValueError:
            continue
        rows.extend(
            scan_vm_speakers(
                source_file=path.name,
                chunk_index=cidx,
                chunk_start=start,
                chunk=chunk,
                block=block,
                codec=codec,
            )
        )
    return rows


def trace_file(path: Path, chunk_filter: set[int] | None = None) -> list[VMTraceRow]:
    data = path.read_bytes()
    spans = read_chunk_spans(data)
    rows: list[VMTraceRow] = []
    for cidx, (start, end) in enumerate(spans):
        if chunk_filter is not None and cidx not in chunk_filter:
            continue
        chunk = data[start:end]
        try:
            block = find_text_block(chunk)
        except ValueError:
            continue
        rows.extend(
            trace_vm_bytecode(
                source_file=path.name,
                chunk_index=cidx,
                chunk_start=start,
                chunk=chunk,
                block=block,
            )
        )
    return rows


def confirmed_speaker_slots(path: Path, codec: Codec, chunk_index: int) -> dict[int, int]:
    """Return ``FB00`` argument -> local name-record index for confirmed rows."""
    rows = scan_file(path, codec, {chunk_index})
    out: dict[int, int] = {}
    for row in rows:
        if row.confidence == "confirmed" and row.speaker_record is not None:
            out.setdefault(row.fb_id, row.speaker_record)
    return out


def row_dict(row: SpeakerSite) -> dict[str, str]:
    return {
        "source_file": row.source_file,
        "chunk_index": str(row.chunk_index),
        "chunk_start": f"0x{row.chunk_start:06X}",
        "text_base": f"0x{row.text_base:04X}",
        "text_records": str(row.text_records),
        "name_pool": " | ".join(f"{idx}:{name}" for idx, name in row.name_pool),
        "actor_table": " ".join(
            f"{entry.key:04X}:{entry.field2:02X}:{entry.field3:02X}"
            for entry in row.actor_table
        ),
        "vm_off": "" if row.vm_off is None else f"0x{row.vm_off:04X}",
        "vm_rel_off": "" if row.vm_rel_off is None else f"0x{row.vm_rel_off:04X}",
        "form": row.form,
        "fb_id": f"{row.fb_id:04X}",
        "record_indices": " ".join(str(i) for i in row.record_indices),
        "speaker_slot": "" if row.speaker_slot is None else str(row.speaker_slot),
        "speaker_record": "" if row.speaker_record is None else str(row.speaker_record),
        "speaker_name": row.speaker_name,
        "confidence": row.confidence,
        "state_word": "" if row.state_word is None else f"{row.state_word:04X}",
        "extra_words": " ".join(f"{x:04X}" for x in row.extra_words),
        "tail_words": " ".join(f"{x:04X}" for x in row.tail_words),
    }


def write_csv(rows: list[SpeakerSite], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "source_file",
        "chunk_index",
        "chunk_start",
        "text_base",
        "text_records",
        "name_pool",
        "actor_table",
        "vm_off",
        "vm_rel_off",
        "form",
        "fb_id",
        "record_indices",
        "speaker_slot",
        "speaker_record",
        "speaker_name",
        "confidence",
        "state_word",
        "extra_words",
        "tail_words",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow(row_dict(row))


def trace_row_dict(row: VMTraceRow) -> dict[str, str]:
    return {
        "source_file": row.source_file,
        "chunk_index": str(row.chunk_index),
        "chunk_start": f"0x{row.chunk_start:06X}",
        "vm_off": f"0x{row.vm_off:04X}",
        "stream_start": f"0x{row.stream_start:04X}",
        "path_id": str(row.path_id),
        "step": str(row.step),
        "rel_off": f"0x{row.rel_off:04X}",
        "abs_off": f"0x{row.vm_off + row.rel_off:04X}",
        "opcode": f"{row.opcode:02X}",
        "kind": row.kind,
        "length": "" if row.length is None else str(row.length),
        "next_rel_off": "" if row.next_rel_off is None else f"0x{row.next_rel_off:04X}",
        "branch_rel_offs": " ".join(f"0x{x:04X}" for x in row.branch_rel_offs),
        "payload": row.payload.hex(" "),
        "stop": "yes" if row.stop else "no",
        "note": row.note,
        "display_text_id": "" if row.display_text_id is None else f"{row.display_text_id:04X}",
        "display_raw_speaker": "" if row.display_raw_speaker is None else f"{row.display_raw_speaker:02X}",
        "display_field1_high": "" if row.display_field1_high is None else f"{row.display_field1_high:X}",
        "display_field1_low": "" if row.display_field1_low is None else f"{row.display_field1_low:X}",
        "display_flags": "" if row.display_flags is None else f"{row.display_flags:02X}",
        "display_byte8": "" if row.display_byte8 is None else f"{row.display_byte8:02X}",
        "display_byte9": "" if row.display_byte9 is None else f"{row.display_byte9:02X}",
    }


def write_trace_csv(rows: list[VMTraceRow], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "source_file",
        "chunk_index",
        "chunk_start",
        "vm_off",
        "stream_start",
        "path_id",
        "step",
        "rel_off",
        "abs_off",
        "opcode",
        "kind",
        "length",
        "next_rel_off",
        "branch_rel_offs",
        "payload",
        "stop",
        "note",
        "display_text_id",
        "display_raw_speaker",
        "display_field1_high",
        "display_field1_low",
        "display_flags",
        "display_byte8",
        "display_byte9",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow(trace_row_dict(row))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scen", default="work/l5/extracted/SCEN.DAT")
    ap.add_argument("--scen2", default="work/l5/extracted/SCEN2.DAT")
    ap.add_argument("--font-map", default="data/common/font_mapping/groups_report.csv")
    ap.add_argument("--out", default="work/vm_dialog_refs/speaker_refs.csv")
    ap.add_argument("--trace-out", help="write conservative bytecode trace CSV")
    ap.add_argument("--chunk", type=int, action="append", help="scan only this chunk index; may repeat")
    args = ap.parse_args()

    codec = Codec(load_charmap_csv(Path(args.font_map)))
    chunk_filter = set(args.chunk) if args.chunk else None
    rows: list[SpeakerSite] = []
    for src in (Path(args.scen), Path(args.scen2)):
        if src.exists():
            rows.extend(scan_file(src, codec, chunk_filter))
    write_csv(rows, Path(args.out))
    confirmed = sum(1 for r in rows if r.confidence == "confirmed")
    unresolved = sum(1 for r in rows if r.confidence != "confirmed")
    print(f"wrote {args.out} rows={len(rows)} confirmed={confirmed} unresolved={unresolved}")

    if args.trace_out:
        trace_rows: list[VMTraceRow] = []
        for src in (Path(args.scen), Path(args.scen2)):
            if src.exists():
                trace_rows.extend(trace_file(src, chunk_filter))
        write_trace_csv(trace_rows, Path(args.trace_out))
        display_rows = sum(1 for r in trace_rows if r.kind == "display_24424")
        stops = sum(1 for r in trace_rows if r.stop)
        print(
            f"wrote {args.trace_out} rows={len(trace_rows)} displays={display_rows} stops={stops}"
        )


if __name__ == "__main__":
    main()
