#!/usr/bin/env python3
"""Insert edited script dump back into SCEN.DAT/SCEN2.DAT.

By default, records are re-encoded and repacked inside the original text
block of each chunk: records may trade space with each other, but the block
base, record count and everything outside the block stay byte-identical.
The space after the last record is padded with zero words.

With --fixed-size-repack, text blocks may grow, chunks are re-laid out at
0x800 alignment, the chunk pointer table is rewritten, and the final file
size must remain byte-identical to the source file.

With --allow-grow, a text block that no longer fits is enlarged in place:
the block size word is updated, the chunk suffix shifts down, the chunk is
re-padded to sector alignment and the container chunk pointer table is
rebuilt. The block base stays put because the game derives it as
vm_off + vm_size (see docs/INTERNAL_DATA_FORMATS.md), both of which are untouched.
"""
import argparse
import struct
from pathlib import Path

from langrisser.game import add_game_args, game_from_args
from langrisser.project import COMMON_FONT_MAP
from langrisser.scen import (
    Codec,
    TextBlock,
    find_text_block,
    load_charmap_csv,
    load_charmap_tbl,
    read_chunk_spans,
    words_to_bytes,
)

CHUNK_ALIGN = 0x800


def align_up(value: int, align: int = CHUNK_ALIGN) -> int:
    return (value + align - 1) & ~(align - 1)


def parse_dump_file(path: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith("#") or "\t" not in raw:
            continue
        idx, text = raw.split("\t", 1)
        if idx.strip().isdigit():
            out[int(idx.strip())] = text
    return out


def rebuild_block(chunk: bytes, block: TextBlock, edits: dict[int, str],
                  codec: Codec, max_size: int, label: str) -> bytes:
    """Re-encode records into a new text block image. The result keeps the
    original block size when the text fits; otherwise it is exactly as
    large as needed, 4-byte aligned, up to max_size. Original chunks keep
    every text block end 4-byte aligned; battle suffix data is read with
    word loads immediately after the block."""
    table_len = 2 + 2 * len(block.offsets)

    payloads: list[bytes] = []
    for ridx in range(1, block.record_count + 1):
        a, b = block.record_span(ridx)
        if ridx in edits:
            payloads.append(words_to_bytes(codec.encode(edits[ridx])))
        else:
            payloads.append(chunk[a:b])
    body = b"".join(payloads)

    needed = table_len + len(body)
    out_size = block.size if needed <= block.size else align_up(needed, 4)
    if out_size > max_size:
        raise SystemExit(
            f"{label}: text needs {len(body)} bytes, available budget is "
            f"{max_size - table_len}. Shorten the text or use --allow-grow."
        )
    if out_size > 0xFFFF:
        raise SystemExit(f"{label}: text block would exceed u16 size limit")

    blob = bytearray(out_size)
    blob[0:2] = out_size.to_bytes(2, "little")
    cur = table_len
    for i, payload in enumerate(payloads, start=1):
        blob[2 + 2 * i : 4 + 2 * i] = cur.to_bytes(2, "little")
        blob[cur : cur + len(payload)] = payload
        cur += len(payload)
    return bytes(blob)


def trim_aligned_chunk(chunk: bytes) -> bytes:
    """Drop whole-sector trailing zero padding from a chunk."""
    used = len(chunk.rstrip(b"\x00"))
    size = align_up(used)
    return chunk[:size].ljust(size, b"\x00")


def rebuild_chunk_fixed(chunk: bytes, block: TextBlock, edits: dict[int, str],
                        codec: Codec, label: str) -> bytes:
    """Rebuild one chunk for the fixed-size repack: the text block may grow,
    growth is absorbed into the chunk's own trailing zero padding when
    possible, and the result is 0x800-aligned."""
    blob = rebuild_block(chunk, block, edits, codec, 0xFFFF, label)
    new_chunk = chunk[: block.base] + blob + chunk[block.base + block.size :]
    if len(new_chunk) > len(chunk):
        orig_tz = len(chunk) - len(chunk.rstrip(b"\x00"))
        stripped = new_chunk.rstrip(b"\x00")
        removable = min(orig_tz, len(new_chunk) - len(stripped))
        if len(new_chunk) - removable <= len(chunk):
            new_chunk = new_chunk[: len(new_chunk) - removable]
            new_chunk += b"\x00" * (len(chunk) - len(new_chunk))
    if len(new_chunk) % CHUNK_ALIGN:
        new_chunk += b"\x00" * (CHUNK_ALIGN - len(new_chunk) % CHUNK_ALIGN)
    return new_chunk


def trim_blobs_to_fit(blobs: list[bytes], capacity: int) -> tuple[list[bytes], int]:
    """Reclaim whole-sector trailing zero padding, from the last chunk
    backwards, until the chunks fit the capacity (or nothing is left to
    trim). Returns the adjusted chunk list and its total size."""
    blobs = list(blobs)
    total = sum(len(blob) for blob in blobs)
    if total > capacity:
        for i in range(len(blobs) - 1, -1, -1):
            trimmed = trim_aligned_chunk(blobs[i])
            saved = len(blobs[i]) - len(trimmed)
            if saved <= 0:
                continue
            blobs[i] = trimmed
            total -= saved
            if total <= capacity:
                break
    return blobs, total


def rebuild_container_fixed_size(data: bytes, chunks: list[bytes],
                                 spans: list[tuple[int, int]],
                                 label: str) -> bytes:
    header_size = spans[0][0]
    header = bytearray(data[:header_size])
    # Reclaim whole-sector trailing padding only when the translated
    # chunks actually need container-level space.
    blobs, chunks_total = trim_blobs_to_fit(list(chunks), len(data) - header_size)
    total = header_size + chunks_total

    if total > len(data):
        raise SystemExit(
            f"{label}: fixed-size repack needs {total} bytes, source file is "
            f"{len(data)} bytes. Shorten text or free more padding."
        )

    cur = header_size
    ptrs: list[int] = []

    for blob in blobs:
        if cur % CHUNK_ALIGN:
            raise SystemExit(f"{label}: chunk pointer 0x{cur:X} is not 0x800-aligned")
        ptrs.append(cur)
        cur += len(blob)

    ptrs.append(len(data))
    if len(ptrs) * 4 > header_size:
        raise SystemExit(f"{label}: pointer table does not fit in original header")
    for i, p in enumerate(ptrs):
        struct.pack_into("<I", header, i * 4, p)

    result = bytes(header) + b"".join(blobs)
    result += b"\x00" * (len(data) - len(result))
    if len(result) != len(data):
        raise AssertionError("fixed-size repack changed file size")
    return result


def dump_path_for(dump_root: Path, primary_stem: str,
                  fallback_stems: tuple[str, ...], chunk_idx: int) -> Path:
    """Find the editable dump file for one chunk.

    SCEN2's text is intentionally translated from the SCEN dump in the project
    flow, but the round-trip verifier still dumps and reinserts SCEN2 under its
    own stem. Prefer the source file's own stem when it exists, then fall back.
    """
    name = f"chunk_{chunk_idx:03d}.txt"
    for stem in (primary_stem, *fallback_stems):
        path = dump_root / stem / name
        if path.exists():
            return path
    return dump_root / primary_stem / name


def insert_file(src: Path, dump_root: Path, out_path: Path, codec: Codec,
                allow_grow: bool = False, fixed_size_repack: bool = False,
                fallback_dump_stems: tuple[str, ...] = ()) -> int:
    data = src.read_bytes()
    spans = read_chunk_spans(data)
    new_chunks: list[bytes] = []
    changed = 0
    grew = False

    for cidx, (s, e) in enumerate(spans):
        chunk = data[s:e]
        dump_path = dump_path_for(dump_root, src.stem, fallback_dump_stems, cidx)
        edits = parse_dump_file(dump_path) if dump_path.exists() else {}
        if edits:
            block = find_text_block(chunk)
            if fixed_size_repack:
                new_chunk = rebuild_chunk_fixed(chunk, block, edits, codec,
                                                f"{src.name} chunk {cidx}")
            else:
                orig_tz = len(chunk) - len(chunk.rstrip(b"\x00"))
                if allow_grow:
                    max_size = 0xFFFF
                else:
                    max_size = block.size + (orig_tz & ~1)
                blob = rebuild_block(chunk, block, edits, codec, max_size,
                                     f"{src.name} chunk {cidx}")
                new_chunk = chunk[: block.base] + blob + chunk[block.base + block.size :]
                if len(new_chunk) > len(chunk) and not allow_grow:
                    # Absorb growth into the chunk's own trailing zero padding
                    # so the container layout (and the ISO) stays untouched.
                    stripped = new_chunk.rstrip(b"\x00")
                    removable = min(orig_tz, len(new_chunk) - len(stripped))
                    if len(new_chunk) - removable <= len(chunk):
                        new_chunk = new_chunk[: len(new_chunk) - removable]
                        new_chunk += b"\x00" * (len(chunk) - len(new_chunk))
                if len(new_chunk) % CHUNK_ALIGN:
                    new_chunk += b"\x00" * (CHUNK_ALIGN - len(new_chunk) % CHUNK_ALIGN)
            if len(new_chunk) != len(chunk):
                grew = True
                print(f"{src.name} chunk {cidx}: grown 0x{len(chunk):X} -> 0x{len(new_chunk):X}")
            if new_chunk != chunk:
                changed += 1
            new_chunks.append(new_chunk)
        else:
            new_chunks.append(chunk)

    if fixed_size_repack:
        result = rebuild_container_fixed_size(data, new_chunks, spans, src.name)
        changed = sum(
            1 for (s, e), chunk in zip(spans, read_chunk_spans(result))
            if data[s:e] != result[chunk[0]:chunk[1]]
        )
    elif not grew:
        out = bytearray(data)
        for (s, e), chunk in zip(spans, new_chunks):
            out[s:e] = chunk
        result = bytes(out)
    else:
        header = bytearray(data[: spans[0][0]])
        cur = spans[0][0]
        ptrs: list[int] = []
        blobs: list[bytes] = []
        for chunk in new_chunks:
            ptrs.append(cur)
            blobs.append(chunk)
            cur += len(chunk)
        ptrs.append(cur)
        for i, p in enumerate(ptrs):
            struct.pack_into("<I", header, i * 4, p)
        result = bytes(header) + b"".join(blobs)
        print(f"{src.name}: container rebuilt, size {len(data)} -> {len(result)}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(result)
    print(f"{src.name}: chunks_changed={changed} -> {out_path}")
    return changed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_game_args(ap)
    ap.add_argument("--scen", default="work/l5/extracted/SCEN.DAT")
    ap.add_argument("--scen2", default=None,
                    help="Second script file (Langrisser V mirrors SCEN in SCEN2).")
    ap.add_argument("--dump-dir", default="work/l5/scriptdump")
    ap.add_argument("--charmap", default=None,
                    help="groups_report.csv or a HHHH=c .tbl file "
                         "(default: the game's font map)")
    ap.add_argument("--out-scen", default="work/l5/build/SCEN.DAT")
    ap.add_argument("--out-scen2", default="work/l5/build/SCEN2.DAT")
    ap.add_argument("--allow-grow", action="store_true",
                    help="Allow text blocks (and the container) to grow.")
    ap.add_argument("--fixed-size-repack", action="store_true",
                    help="Repack chunks and rewrite pointers without changing file size.")
    args = ap.parse_args()
    if args.allow_grow and args.fixed_size_repack:
        ap.error("--allow-grow and --fixed-size-repack are mutually exclusive")

    game = game_from_args(args)
    charmap_path = Path(args.charmap) if args.charmap else game.font_map
    if charmap_path.suffix == ".tbl":
        codec = Codec(load_charmap_tbl(charmap_path))
    else:
        codec = Codec(load_charmap_csv(charmap_path))

    dump_root = Path(args.dump_dir)
    insert_file(Path(args.scen), dump_root, Path(args.out_scen), codec,
                args.allow_grow, args.fixed_size_repack)
    if args.scen2:
        insert_file(Path(args.scen2), dump_root, Path(args.out_scen2), codec,
                    args.allow_grow, args.fixed_size_repack,
                    fallback_dump_stems=("SCEN",))


if __name__ == "__main__":
    main()
