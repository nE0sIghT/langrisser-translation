#!/usr/bin/env python3
"""Langrisser I & II SCEN.DAT: read the script, macros expanded.

The container spine is the one `scen.py` documents — a `u32` pointer table whose
last entry is the file size — but everything below it is this engine's own, so
this module carries the parts that differ and nothing else.

    catalog:  u32 chunk_pointers[], last == file size  (scen.read_chunk_spans)
    chunk:    u32 section_table[], section_table[0] == its own byte size
    section 2 is the text, itself a table of that same self-describing form
    part:     NUL-terminated strings, back to back

A string is bytes: `0x00` ends it, `0x0A`-`0xF6` is a glyph, `0xF7`-`0xFB` opens
a 255-wide bank whose next byte completes the slot, and `0x01`-`0x09` are
control codes, four of which take the byte after them.

Two of those controls print another string in place of themselves - `0x04` from
part 4, the phrase table, and `0x09` from part 1, the character names. A third
of the bytes in a line of dialogue are one of those references, so a reader that
does not follow them is not reading the script. Expansion is one level deep
here; the engine restores the caller's stream pointer afterwards, so a nested
string could in principle nest again.
"""
from __future__ import annotations

import argparse
import json
import csv
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from langrisser.game import add_game_args, game_from_args
from langrisser.release import add_release_args, release_from_args
from langrisser.scen import Codec, load_charmap_csv, read_chunk_spans

SECTOR = 0x800
TEXT_SECTION = 2
PHRASE_PART = 4
NAME_PART = 1
BANK_FIRST = 0xF7
BANK_LAST = 0xFB
BANK_WIDTH = 255
BANK_BASE = 236
GLYPH_FIRST = 0x0A
GLYPH_MAX_SLOT = 0xF6 - GLYPH_FIRST   # highest slot a single byte can name

# Control codes, by what the executable's jump table at 0x8001017C does with
# them. `operand` is whether the byte after the code belongs to it.
CONTROLS = {
    0x01: ("state", True),
    0x02: ("pair", False),
    0x03: ("number", False),
    0x04: ("phrase", True),
    0x05: ("blank", False),
    0x06: ("wait", False),
    0x07: ("page", False),
    0x08: ("line", False),
    0x09: ("name", True),
}


def offset_table(seg: bytes) -> list[int] | None:
    """A self-describing u32 table: its first entry is its own byte size."""
    if len(seg) < 4:
        return None
    first = struct.unpack_from("<I", seg, 0)[0]
    if first < 8 or first % 4 or first > len(seg):
        return None
    table = list(struct.unpack_from(f"<{first // 4}I", seg, 0))
    if table[0] != first or any(table[i] > table[i + 1] for i in range(len(table) - 1)):
        return None
    return table if table[-1] <= len(seg) else None


def split_strings(part: bytes) -> list[bytes]:
    """Strings of a part, keeping bank escapes whole so `0x00` never splits one."""
    out: list[bytes] = []
    cur = bytearray()
    i = 0
    while i < len(part):
        v = part[i]
        if v == 0:
            out.append(bytes(cur))
            cur = bytearray()
            i += 1
        elif BANK_FIRST <= v <= BANK_LAST and i + 1 < len(part):
            cur += part[i:i + 2]
            i += 2
        else:
            cur.append(v)
            i += 1
    if cur:
        out.append(bytes(cur))
    return out


def build_table(blocks: list[bytes]) -> bytes:
    """A self-describing u32 table over `blocks`, in the form the game reads."""
    head = 4 * len(blocks)
    offsets, cursor = [], head
    for block in blocks:
        offsets.append(cursor)
        cursor += len(block)
    return struct.pack(f"<{len(blocks)}I", *offsets) + b"".join(blocks)


def join_strings(strings) -> bytes:
    """A part: every string NUL-terminated, back to back."""
    return b"".join(bytes(s) + b"\x00" for s in strings)


@dataclass(frozen=True)
class Chunk:
    index: int
    start: int
    end: int
    parts: tuple[tuple[bytes, ...], ...]

    def part(self, number: int) -> tuple[bytes, ...]:
        return self.parts[number] if number < len(self.parts) else ()


def pack_chunk(original: bytes, parts, cap: bool = True) -> bytes:
    """Rebuild a chunk with a new text section, keeping every other section.

    Sections after the text one move, so the chunk's own table is rewritten;
    the chunk is then padded back to its sector-aligned length, which is where
    the growth budget comes from. Raises if the result no longer fits.

    `cap=False` returns the chunk at its natural length instead, for the
    caller that is going to lay every chunk out again and can spend another
    chunk's padding here (see `container.rebuild_container_fixed_size`).
    """
    sections = offset_table(original)
    if not sections:
        raise ValueError("chunk has no section table")
    bounds = list(zip(sections, sections[1:] + [len(original)]))
    blocks = [original[a:b] for a, b in bounds]
    blocks[TEXT_SECTION] = build_table([join_strings(p) for p in parts])
    # The last section's bytes run to the end of the chunk and include its
    # padding; strip that so the rebuilt chunk is padded once, not twice.
    blocks[-1] = blocks[-1].rstrip(b"\x00")
    packed = build_table(blocks)
    if not cap:
        return packed
    if len(packed) > len(original):
        raise ValueError(
            f"chunk grew past its sector: {len(packed)} > {len(original)}")
    return packed + b"\x00" * (len(original) - len(packed))


def read_chunks(blob: bytes) -> list[Chunk]:
    out = []
    for index, (start, end) in enumerate(read_chunk_spans(blob)):
        chunk = blob[start:end]
        sections = offset_table(chunk)
        if not sections or len(sections) <= TEXT_SECTION + 1:
            continue
        text = chunk[sections[TEXT_SECTION]:sections[TEXT_SECTION + 1]]
        parts = offset_table(text)
        if not parts:
            continue
        bounds = list(zip(parts, parts[1:] + [len(text)]))
        out.append(Chunk(index, start, end,
                         tuple(tuple(split_strings(text[a:b])) for a, b in bounds)))
    return out


# Editable form of a control: what a translator sees and may move, but not
# invent. `decode(expand=False)` writes these and `encode` reads them back, so a
# record round-trips through the pack unchanged.
TAG_RE = re.compile(r"<(\$[0-9A-Fa-f]{4}|[a-z]+(?::\d+)?)>")
BREAKS = {"line": "\n", "page": "\n\n", "blank": " "}


class Reader:
    """Decodes a string, following the references the engine follows."""

    def __init__(self, font: dict[int, str], chunk: Chunk, depth: int = 2):
        self.font = font
        self.chunk = chunk
        self.depth = depth
        # The plane draws some characters twice, so a character alone does not
        # say which slot wrote it. The first slot is canonical; any other is
        # written as a raw tag, the way l45 writes one, so the record still
        # encodes back to the bytes it came from.
        self.canonical: dict[str, int] = {}
        for slot, ch in sorted(font.items()):
            self.canonical.setdefault(ch, slot)

    def slot(self, index: int) -> str:
        ch = self.font.get(index)
        if not ch or self.canonical.get(ch) != index:
            return f"<${index:04X}>"
        return ch

    def decode(self, data: bytes, depth: int | None = None,
               expand: bool = True) -> str:
        """Text of a string. `expand=False` keeps references as tags, which is
        the form a translation is written in and the form `encode` reverses."""
        depth = (self.depth if depth is None else depth) if expand else 0
        out: list[str] = []
        i = 0
        while i < len(data):
            v = data[i]
            if v in CONTROLS:
                name, takes = CONTROLS[v]
                arg = data[i + 1] if takes and i + 1 < len(data) else None
                i += 2 if takes else 1
                out.append(self.control(name, arg, depth))
            elif v >= BANK_FIRST and i + 1 < len(data):
                out.append(self.slot(BANK_BASE + (v - BANK_FIRST) * BANK_WIDTH + data[i + 1]))
                i += 2
            elif v >= GLYPH_FIRST:
                out.append(self.slot(v - GLYPH_FIRST))
                i += 1
            else:
                out.append(f"<!{v:02X}>")
                i += 1
        return "".join(out)

    def inline_phrases(self, text: str, depth: int = 2) -> str:
        """Editable text with phrase references replaced by their own editable
        text.

        The phrase table is the script's own compression, so a translation may
        keep a reference or write the words out and the screen looks the same.
        Inlining states both forms in the same terms — including the layout
        tags a phrase carries inside it, which surface as the reference goes.
        """
        def sub(m: re.Match[str]) -> str:
            kind, _, arg = m.group(1).partition(":")
            if kind != "phrase" or not arg or depth <= 0:
                return m.group(0)
            strings = self.chunk.part(PHRASE_PART)
            if not 1 <= int(arg) <= len(strings):
                return m.group(0)
            inner = self.decode(strings[int(arg) - 1], expand=False)
            return self.inline_phrases(inner, depth - 1)

        return TAG_RE.sub(sub, text)

    def control(self, name: str, arg: int | None, depth: int) -> str:
        # Reading form uses real breaks; the editable form uses tags, because a
        # newline at the edge of a record cannot survive a line-based file.
        if name in BREAKS:
            return BREAKS[name] if depth > 0 else f"<{name}>"
        if name in ("phrase", "name") and arg is not None and depth <= 0:  # noqa: E501
            return f"<{name}:{arg}>"
        if name in ("phrase", "name") and arg is not None:
            part = PHRASE_PART if name == "phrase" else NAME_PART
            # Numbers are 1-based over the strings of that part.
            number = arg if name == "phrase" else arg + 2
            strings = self.chunk.part(part)
            if depth > 0 and 1 <= number <= len(strings):
                return self.decode(strings[number - 1], depth - 1)
            return f"<{name}:{number}>"
        if arg is not None:
            return f"<{name}:{arg}>"
        return f"<{name}>"


def roundtrip(blob: bytes) -> tuple[int, int, list[int]]:
    """Rebuild every chunk from what was read and compare. Returns (ok, total, bad)."""
    ok, total, bad = 0, 0, []
    for start, end in read_chunk_spans(blob):
        original = blob[start:end]
        sections = offset_table(original)
        if not sections or len(sections) <= TEXT_SECTION + 1:
            continue
        text = original[sections[TEXT_SECTION]:sections[TEXT_SECTION + 1]]
        parts = offset_table(text)
        if not parts:
            continue
        total += 1
        bounds = list(zip(parts, parts[1:] + [len(text)]))
        strings = [split_strings(text[a:b]) for a, b in bounds]
        try:
            same = pack_chunk(original, strings) == original
        except ValueError:
            same = False
        if same:
            ok += 1
        else:
            bad.append(start)
    return ok, total, bad


# The last slot `Writer.slot_bytes` can encode: five banks of `BANK_WIDTH`
# above `BANK_BASE`. No argument byte reaches 0xFF in either script, which is
# why the top of the last bank is one short of the width. Tiles above this
# exist in the plane but no string can name them.
MAX_SLOT = BANK_BASE + (BANK_LAST - BANK_FIRST) * BANK_WIDTH + BANK_WIDTH - 1


def load_assignments(path: Path) -> dict[int, str]:
    """`slot -> text` from a target-language slot table."""
    with path.open(encoding="utf-8") as fh:
        return {int(r["index_dec"]): r["char"] for r in csv.DictReader(fh)}


def merged_plane(font: dict[int, str], assignments: dict[int, str]) -> dict[int, str]:
    """The plane as a built disc will draw it.

    A sacrificed slot stops being the kanji it was: its tile gets redrawn, so
    a record that still encoded through it would come out as a Cyrillic letter
    mid-sentence. Dropping the old reading here is what makes the encoder
    refuse such a record instead of writing it.
    """
    plane = dict(font)
    plane.update(assignments)
    return plane


class Writer:
    """Encodes a record back to the bytes the engine reads.

    The inverse of `decode(expand=False)`: every glyph becomes its slot, every
    tag becomes its control, and a slot too high for one byte becomes a bank
    escape. It refuses a character the plane does not have rather than dropping
    it, because a silently missing glyph is a hole nobody sees until the game
    draws it.
    """

    # A slot number no plane has, standing in for the space while the codec
    # tiles. The engine draws a lone space with the blank control rather than
    # a glyph, but a space inside a pair is an ordinary tile and has to be on
    # offer as one — those pairs are most of what keeps a line looking packed
    # instead of spelled out letter by letter.
    BLANK = 1 << 20

    def __init__(self, font: dict[int, str]):
        self.slot_of: dict[str, int] = {}
        for slot, ch in sorted(font.items()):
            self.slot_of.setdefault(ch, slot)
        self.by_name = {name: code for code, (name, _takes) in CONTROLS.items()}
        # How to cut a line into tiles is Langrisser V's codec, used as it
        # stands rather than answered a second time: fewest cells, then
        # best-looking, so a word is never left with one full-width letter
        # stranded among narrow ones.
        self.codec = Codec(
            {**font, self.BLANK: " "},
            compact_interword_spaces=True,
        )

    def tile_run(self, text: str) -> bytes:
        """Encode a run of plain text, tiled by Langrisser V's codec."""
        out = bytearray()
        for slot in self.codec.encode(text):
            out += (bytes((self.by_name["blank"],)) if slot == self.BLANK
                    else self.slot_bytes(slot))
        return bytes(out)

    def slot_bytes(self, slot: int) -> bytes:
        if slot < 0 or slot > BANK_BASE + (BANK_LAST - BANK_FIRST) * BANK_WIDTH + 0xFE:
            raise ValueError(f"slot {slot} is outside the plane")
        if slot <= GLYPH_MAX_SLOT:
            return bytes((slot + GLYPH_FIRST,))
        rel = slot - BANK_BASE
        bank, arg = divmod(rel, BANK_WIDTH)
        if bank and arg == 0:
            # Banks meet: bank N argument 255 and bank N+1 argument 0 name the
            # same slot. The game's own packer took the lower bank, so match it,
            # or a rebuilt chunk differs from the original for no reason.
            bank, arg = bank - 1, BANK_WIDTH
        return bytes((BANK_FIRST + bank, arg))

    def encode(self, text: str) -> bytes:
        out = bytearray()
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\n":
                page = text.startswith("\n\n", i)
                out.append(self.by_name["page" if page else "line"])
                i += 2 if page else 1
                continue
            m = TAG_RE.match(text, i)
            if m:
                out += self.tag_bytes(m.group(1))
                i = m.end()
                continue
            j = i
            while j < len(text) and text[j] != "\n" and not TAG_RE.match(text, j):
                j += 1
            out += self.tile_run(text[i:j])
            i = j
        return bytes(out)

    def tag_bytes(self, body: str) -> bytes:
        if body.startswith("$"):
            return self.slot_bytes(int(body[1:], 16))
        name, _, arg = body.partition(":")
        code = self.by_name.get(name)
        if code is None:
            raise ValueError(f"unknown control <{body}>")
        takes = CONTROLS[code][1]
        if takes != bool(arg):
            raise ValueError(f"<{body}> has the wrong shape for control {code}")
        return bytes((code, int(arg))) if takes else bytes((code,))


def verify_text(blob: bytes, font: dict[int, str]) -> tuple[int, int, tuple | None]:
    """Every string through text and back. The check a translation rests on:
    if a record cannot survive being read and written unchanged, an edited one
    cannot be trusted either."""
    writer = Writer(font)
    ok = bad = 0
    first = None
    for chunk in read_chunks(blob):
        reader = Reader(font, chunk)
        for pi, strings in enumerate(chunk.parts):
            for si, raw in enumerate(strings):
                if not raw:
                    continue
                text = reader.decode(raw, expand=False)
                try:
                    same = writer.encode(text) == raw
                except ValueError:
                    same = False
                if same:
                    ok += 1
                else:
                    bad += 1
                    if first is None:
                        first = (chunk.index, pi, si, text[:60])
    return ok, bad, first


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_game_args(ap, default="l2")
    add_release_args(ap, default="l1-2-ps1-jp")
    ap.add_argument("--scen", default=None,
                    help="Script container (default: the extracted copy for --game).")
    ap.add_argument("--font-map", default=None,
                    help="Slot map (default: the game manifest's).")
    ap.add_argument("--roundtrip", action="store_true",
                    help="rebuild every chunk unchanged and check it is byte-identical")
    ap.add_argument("--verify-text", action="store_true",
                    help="decode every string to text and encode it back")
    ap.add_argument("--out-dir")
    ap.add_argument("--depth", type=int, default=2,
                    help="how far to follow phrase and name references")
    args = ap.parse_args()

    game = game_from_args(args)
    release = release_from_args(args, platform="ps1")
    scen = Path(args.scen) if args.scen else Path(
        "work", game.code, "extracted", release.media_path("SCEN.DAT", game.code).lstrip("/").split("/")[-1])
    blob = scen.read_bytes()
    if args.verify_text:
        font = load_charmap_csv(Path(args.font_map) if args.font_map else game.font_map)
        ok, bad, first = verify_text(blob, font)
        print(f"text round trip: {ok}/{ok + bad} strings byte-identical"
              + (f", first at chunk {first[0]} part {first[1]} #{first[2]}: {first[3]!r}"
                 if first else ""))
        raise SystemExit(0 if not bad else 1)
    if args.roundtrip:
        ok, total, bad = roundtrip(blob)
        print(f"no-edit round trip: {ok}/{total} chunks byte-identical"
              + (f", first mismatch at 0x{bad[0]:X}" if bad else ""))
        raise SystemExit(0 if ok == total else 1)
    font = load_charmap_csv(Path(args.font_map) if args.font_map else game.font_map)
    chunks = read_chunks(blob)
    if not args.out_dir:
        raise SystemExit("--out-dir is required unless --roundtrip")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    summary = []
    for chunk in chunks:
        reader = Reader(font, chunk, args.depth)
        lines = []
        counts = []
        for pi, strings in enumerate(chunk.parts):
            counts.append(len(strings))
            lines.append(f"# part {pi}  {len(strings)} strings")
            for si, raw in enumerate(strings):
                if raw:
                    lines.append(f"{si}\t{reader.decode(raw)}")
            lines.append("")
        (out / f"chunk_{chunk.index:03d}.txt").write_text(
            "\n".join(lines), encoding="utf-8")
        summary.append({"chunk": chunk.index, "offset": chunk.start,
                        "bytes": chunk.end - chunk.start, "parts": counts})
    (out / "chunks.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"{len(chunks)} chunks -> {out}")


if __name__ == "__main__":
    main()
