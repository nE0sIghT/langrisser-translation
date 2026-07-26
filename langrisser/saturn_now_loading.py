#!/usr/bin/env python3
"""Translate the Saturn Now Loading plate inside SYSTEM.DAT.

The Saturn release stores the visible plate in SYSTEM.DAT as a compressed
120x32 VDP1 texture. At runtime the resident SH-2 decoder reads:

    table  = SYSTEM.DAT + 0x18000
    stream = SYSTEM.DAT + 0x19e30
    output = VDP1 VRAM 0x25c4a200

The decoded first 28 rows are byte-identical to the PS1 IMG.DAT Now Loading
plate; the remaining 4 rows are zero padding because the VDP1 command height is
32. This script reuses the PS1 plate redraw and only implements the Saturn
prefix/MTF compressor around it.
"""

from __future__ import annotations

import argparse
import heapq
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from langrisser import now_loading as now_loading
from langrisser.project import add_language_args, language_from_args
from langrisser import imgdat as imd

TABLE_OFFSET = 0x18000
STREAM_OFFSET = 0x19E30
WIDTH = 120
VISIBLE_HEIGHT = 28
VDP1_HEIGHT = 32
DECODED_SIZE = WIDTH * VDP1_HEIGHT


@dataclass(frozen=True)
class Code:
    typ: int
    value: int
    bits: tuple[int, ...]


def build_codes(table: bytes) -> dict[tuple[int, int], tuple[int, ...]]:
    codes: dict[tuple[int, int], tuple[int, ...]] = {}

    def walk(node: int, bits: tuple[int, ...]) -> None:
        typ = table[node + 1]
        if typ <= 0xF9:
            for bit in (0, 1):
                walk(node + (table[node + bit] + 1) * 2, bits + (bit,))
        else:
            codes[(typ, table[node])] = bits

    walk(0, ())
    return codes


def decode_stream(table: bytes, stream: bytes) -> tuple[bytes, int]:
    history = [0] * 16
    prefix_bytes = [0, stream[0], stream[1], stream[2]]
    prefix_words = [stream[3] & 0x0F, stream[3] >> 4,
                    stream[4] & 0x0F, stream[4] >> 4]
    pos = 5
    bitbuf = 0
    bits_left = 0
    out = bytearray()

    def mtf(base: int, index: int) -> int:
        value = base & 0xFF
        if index:
            value = (value + history[index - 1]) & 0xFF
        history[:] = [value] + history[:15]
        return value

    while True:
        node = 0
        while table[node + 1] <= 0xF9:
            if bits_left == 0:
                bitbuf = stream[pos]
                pos += 1
                bits_left = 8
            bit = bitbuf & 1
            bitbuf >>= 1
            bits_left -= 1
            node += (table[node + bit] + 1) * 2
        typ = table[node + 1]
        value = table[node]
        if typ == 0xFF:
            out.append(mtf(value, 0))
        elif typ == 0xFE:
            return bytes(out), pos
        else:
            cls = typ - 0xFA
            if not 0 <= cls < 4:
                raise ValueError(f"invalid compressed leaf type {typ:#x}")
            out.append(mtf(prefix_bytes[cls], prefix_words[cls]))
            for _ in range(value):
                out.append(mtf(prefix_bytes[cls], prefix_words[cls]))


def _mtf_tuple(history: tuple[int, ...], base: int, index: int) -> tuple[int, tuple[int, ...]]:
    value = base & 0xFF
    if index:
        value = (value + history[index - 1]) & 0xFF
    return value, (value,) + history[:15]


def encode_stream(table: bytes, original_header: bytes, pixels: bytes) -> bytes:
    codes = build_codes(table)
    literals = {value: bits for (typ, value), bits in codes.items() if typ == 0xFF}
    end_bits = codes[(0xFE, 0)]
    prefix_bytes = [0, original_header[0], original_header[1], original_header[2]]
    prefix_words = [original_header[3] & 0x0F, original_header[3] >> 4,
                    original_header[4] & 0x0F, original_header[4] >> 4]
    runs: list[tuple[int, int, tuple[int, ...]]] = []
    for cls, typ in enumerate(range(0xFA, 0xFE)):
        for (leaf_typ, value), bits in codes.items():
            if leaf_typ == typ:
                runs.append((value + 1, cls, bits))
    # Long runs first. Dijkstra still chooses by bit cost, but this reduces
    # queue churn when several edges have the same total cost.
    runs.sort(key=lambda item: (-item[0], len(item[2])))

    def simulate_run(history: tuple[int, ...], cls: int, length: int) -> tuple[bytes, tuple[int, ...]]:
        h = history
        out = bytearray()
        for _ in range(length):
            value, h = _mtf_tuple(h, prefix_bytes[cls], prefix_words[cls])
            out.append(value)
        return bytes(out), h

    start = (0, tuple([0] * 16))
    dist: dict[tuple[int, tuple[int, ...]], int] = {start: 0}
    prev: dict[tuple[int, tuple[int, ...]], tuple[tuple[int, tuple[int, ...]], tuple[int, ...]]] = {}
    heap: list[tuple[int, int, tuple[int, tuple[int, ...]]]] = [(0, 0, start)]
    serial = 1
    goal: tuple[int, tuple[int, ...]] | None = None

    while heap:
        cost, _serial, state = heapq.heappop(heap)
        if cost != dist[state]:
            continue
        pos, history = state
        if pos == len(pixels):
            goal = state
            break

        literal = pixels[pos]
        if literal not in literals:
            raise ValueError(f"no literal code for palette index {literal}")
        _value, next_history = _mtf_tuple(history, literal, 0)
        next_state = (pos + 1, next_history)
        next_cost = cost + len(literals[literal])
        if next_cost < dist.get(next_state, 1 << 60):
            dist[next_state] = next_cost
            prev[next_state] = (state, literals[literal])
            heapq.heappush(heap, (next_cost, serial, next_state))
            serial += 1

        for length, cls, bits in runs:
            if pos + length > len(pixels):
                continue
            seq, next_history = simulate_run(history, cls, length)
            if pixels[pos:pos + length] != seq:
                continue
            next_state = (pos + length, next_history)
            next_cost = cost + len(bits)
            if next_cost < dist.get(next_state, 1 << 60):
                dist[next_state] = next_cost
                prev[next_state] = (state, bits)
                heapq.heappush(heap, (next_cost, serial, next_state))
                serial += 1

    if goal is None:
        raise ValueError("failed to encode Now Loading plate")

    chunks: list[tuple[int, ...]] = [end_bits]
    state = goal
    while state != start:
        state, bits = prev[state]
        chunks.append(bits)
    all_bits = [bit for chunk in reversed(chunks) for bit in chunk]

    out = bytearray(original_header)
    byte = 0
    bit_count = 0
    for bit in all_bits:
        byte |= (bit & 1) << bit_count
        bit_count += 1
        if bit_count == 8:
            out.append(byte)
            byte = 0
            bit_count = 0
    if bit_count:
        out.append(byte)
    return bytes(out)


def _header_from_words(base_header: bytes, words: tuple[int, int, int, int]) -> bytes:
    if any(not 0 <= word <= 0x0F for word in words):
        raise ValueError(f"invalid MTF header words: {words}")
    return bytes([
        base_header[0],
        base_header[1],
        base_header[2],
        words[0] | (words[1] << 4),
        words[2] | (words[3] << 4),
    ])


def encode_stream_fitting(table: bytes, original_header: bytes, pixels: bytes,
                          budget: int) -> tuple[bytes, bytes]:
    """Encode with known-good MTF header variants and return `(stream, header)`.

    The header's first three bytes are additive bases for MTF classes; the last
    two bytes hold four 4-bit history depths. The original plate uses
    `(1, 8, 4, 15)`. The Russian redraw keeps PS1 plate geometry but fits only
    when the same table uses `(1, 10, 5, 15)`, so both variants are tried before
    failing.
    """
    original_words = (
        original_header[3] & 0x0F,
        original_header[3] >> 4,
        original_header[4] & 0x0F,
        original_header[4] >> 4,
    )
    candidates = [
        original_header,
        _header_from_words(original_header, (1, 8, 5, 15)),
        _header_from_words(original_header, (1, 10, 5, 15)),
        _header_from_words(original_header, original_words),
    ]
    seen: set[bytes] = set()
    best: tuple[int, bytes, bytes] | None = None
    for header in candidates:
        if header in seen:
            continue
        seen.add(header)
        stream = encode_stream(table, header, pixels)
        decoded, _used = decode_stream(table, stream)
        if decoded != pixels:
            raise ValueError(f"encoder round-trip failed for header {header.hex()}")
        if best is None or len(stream) < best[0]:
            best = (len(stream), stream, header)
        if len(stream) <= budget:
            return stream, header
    assert best is not None
    raise ValueError(f"encoded stream is too large: {best[0]} > original {budget}")


def read_ps1_palette(imgdat_path: Path) -> list[tuple[int, int, int]]:
    data = imd.read_img(imgdat_path)
    ent, asset = imd.get_asset(data, now_loading.ASSET_INDEX)
    # Also validate that the PS1 source plate is structurally readable; this is
    # the asset whose palette the shared redraw routine was tuned against.
    pairs = now_loading.find_plate_packets(data, ent)
    copies = [now_loading.read_plate(data, pair) for pair in pairs]
    if any(c != copies[0] for c in copies[1:]):
        raise SystemExit("PS1 Now Loading plate copies differ; refusing to reuse palette")
    return imd.clut_palettes(asset)[now_loading.CLUT_INDEX]


def save_preview(path: Path, palette: list[tuple[int, int, int]],
                 original: bytes, translated: bytes) -> None:
    preview = Image.new("RGB", (WIDTH * 4, VDP1_HEIGHT * 8), (0, 0, 0))
    for idx, pixels in enumerate((original, translated)):
        frame = Image.new("RGB", (WIDTH, VDP1_HEIGHT))
        frame.putdata([palette[v] for v in pixels])
        preview.paste(frame.resize((WIDTH * 4, VDP1_HEIGHT * 4), Image.NEAREST),
                      (0, idx * VDP1_HEIGHT * 4))
    path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--system", default="work/build/saturn/SYSTEM.ru.DAT")
    ap.add_argument("--out-system", default=None)
    ap.add_argument("--palette-imgdat", default="work/extracted/IMG.DAT",
                    help="PS1 IMG.DAT used only for the proven-identical plate palette")
    ap.add_argument("--out-preview", default=None)
    ap.add_argument("--font", default=now_loading.FONT)
    ap.add_argument("--cap-top", type=int, default=now_loading.CAP_TOP)
    args = ap.parse_args()

    lang = language_from_args(args)
    if not lang.now_loading:
        raise SystemExit(f"{lang.code} has no now_loading text in its manifest")

    system_path = Path(args.system)
    system = bytearray(system_path.read_bytes())
    table = bytes(system[TABLE_OFFSET:STREAM_OFFSET])
    decoded, original_used = decode_stream(table, bytes(system[STREAM_OFFSET:]))
    if len(decoded) != DECODED_SIZE:
        raise SystemExit(f"decoded plate is {len(decoded)} bytes, expected {DECODED_SIZE}")
    if any(decoded[WIDTH * VISIBLE_HEIGHT:]):
        raise SystemExit("expected zero padding in Saturn Now Loading rows 28..31")

    palette = read_ps1_palette(Path(args.palette_imgdat))
    visible = now_loading.redraw_plate_pixels(
        decoded[:WIDTH * VISIBLE_HEIGHT],
        palette,
        lang.now_loading,
        args.font,
        args.cap_top,
    )
    target = bytes(visible) + b"\x00" * (WIDTH * (VDP1_HEIGHT - VISIBLE_HEIGHT))
    encoded, header = encode_stream_fitting(
        table,
        bytes(system[STREAM_OFFSET:STREAM_OFFSET + 5]),
        target,
        original_used,
    )
    roundtrip, used = decode_stream(table, encoded)
    if roundtrip != target:
        raise SystemExit("encoded Now Loading stream failed round-trip")

    out_path = Path(args.out_system) if args.out_system else system_path
    system[STREAM_OFFSET:STREAM_OFFSET + original_used] = \
        encoded + b"\x00" * (original_used - len(encoded))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(system)

    preview = (Path(args.out_preview) if args.out_preview
               else Path(f"work/build/saturn/now_loading_{lang.suffix}_preview.png"))
    save_preview(preview, palette, decoded, target)
    print(
        f"patched SYSTEM.DAT Now Loading -> {out_path}  "
        f"stream={len(encoded)}/{original_used} header={header.hex()} decoded_used={used}"
    )
    print(f"Now Loading preview -> {preview}")


if __name__ == "__main__":
    main()
