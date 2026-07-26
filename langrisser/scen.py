#!/usr/bin/env python3
"""Core library for Langrisser V SCEN.DAT/SCEN2.DAT script containers.

Container layout:
    u32 chunk_pointers[] at file start (last pointer equals file size),
    chunk payloads at the pointed offsets.

Each chunk holds exactly one text block:
    base+0: u16 block_size      ; block spans [base, base+block_size)
    base+2: u16 offsets[N]      ; offsets[0] == 0, offsets[1] == 2*(N+1),
                                ; ascending, relative to base
    records follow the offset table; record i (1 <= i <= N-1) spans
    [base+offsets[i], base+offsets[i+1]) with offsets[N] := block_size.

Record content is a stream of u16 tokens: values < 0xE000 are printable
glyph indices into the SYSTEM.BIN font, higher values are control words.
Records end with a 0xFFFx terminator.
"""
from __future__ import annotations

import csv
import re
import struct
from dataclasses import dataclass
from pathlib import Path

TAG_RE = re.compile(r"<\$([0-9A-Fa-f]{4})>")
PRINTABLE_LIMIT = 0xE000
TERMINATOR_MIN = 0xFFF0

# Authoring-only marker for a hard page break the reflow pass must never
# compact. It encodes to a plain FFFD page break (the engine sees nothing
# special); rewrap keeps it verbatim so a forced new page survives reflow.
FORCE_PAGE_BREAK = "<!FORCE$FFFD>"


def consumes_argument(word: int) -> bool:
    """Control opcodes whose next word is an argument, not text
    (confirmed by disassembly: F600 macro, FBxx dialog commands)."""
    return word == 0xF600 or 0xFB00 <= word <= 0xFBFF


def read_chunk_spans(data: bytes) -> list[tuple[int, int]]:
    pts: list[int] = []
    for off in range(0, len(data), 4):
        v = struct.unpack_from("<I", data, off)[0]
        pts.append(v)
        if v == len(data):
            break
    else:
        raise ValueError("chunk pointer table has no end marker")
    return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


@dataclass
class TextBlock:
    base: int            # offset of the block inside the chunk
    size: int            # block size in bytes
    offsets: list[int]   # N entries, offsets[0] == 0

    @property
    def record_count(self) -> int:
        return len(self.offsets) - 1  # records are indexed 1..N-1

    def record_span(self, idx: int) -> tuple[int, int]:
        """Span of record idx inside the chunk."""
        if not 1 <= idx <= self.record_count:
            raise IndexError(f"record index {idx} out of 1..{self.record_count}")
        a = self.offsets[idx]
        b = self.offsets[idx + 1] if idx + 1 < len(self.offsets) else self.size
        return self.base + a, self.base + b


def find_text_block(chunk: bytes) -> TextBlock:
    """Locate the single text block in a chunk; raise if not exactly one."""
    found: list[TextBlock] = []
    n = len(chunk)
    pos = 0
    while pos + 6 <= n:
        size, zero, first = struct.unpack_from("<HHH", chunk, pos)
        if (
            zero == 0
            and size >= 8
            and size % 2 == 0
            and first >= 6
            and first % 2 == 0
            and first < size
            and pos + size <= n
        ):
            count = first // 2 - 1  # entries in the offset table
            block = _validate_block(chunk, pos, size, count)
            if block:
                found.append(block)
                pos += size
                continue
        pos += 2
    if len(found) != 1:
        raise ValueError(f"expected exactly 1 text block, found {len(found)}")
    return found[0]


def _validate_block(chunk: bytes, base: int, size: int, count: int) -> TextBlock | None:
    if count < 3 or base + 2 + 2 * count > base + size:
        return None
    offsets = [0]
    prev = 0
    for k in range(1, count):
        v = struct.unpack_from("<H", chunk, base + 2 + 2 * k)[0]
        if v <= prev or v >= size or v % 2:
            return None
        offsets.append(v)
        prev = v
    block = TextBlock(base=base, size=size, offsets=offsets)
    total = terminated = 0
    for i in range(1, block.record_count + 1):
        a, b = block.record_span(i)
        if b - a >= 2:
            last = struct.unpack_from("<H", chunk, b - 2)[0]
            total += 1
            if last >= TERMINATOR_MIN:
                terminated += 1
    if total == 0 or terminated / total < 0.9:
        return None
    return block


def words_from_bytes(blob: bytes) -> list[int]:
    if len(blob) % 2:
        raise ValueError("record byte length is odd")
    return list(struct.unpack(f"<{len(blob)//2}H", blob))


def words_to_bytes(words: list[int]) -> bytes:
    return struct.pack(f"<{len(words)}H", *words)


# --- token <-> text codec ---------------------------------------------------

def load_charmap_csv(path: Path) -> dict[int, str]:
    """token -> char from groups_report.csv (single-char entries only)."""
    out: dict[int, str] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not row["index_dec"].isdigit():
                continue
            ch = (row.get("char") or "").strip()
            if len(ch) == 1:
                out[int(row["index_dec"])] = ch
    return out


def load_charmap_tbl(path: Path) -> dict[int, str]:
    """token -> text (1-2 chars) from a HHHH=text table file."""
    out: dict[int, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        a, b = raw.split("=", 1)
        try:
            tok = int(a.strip(), 16)
        except ValueError:
            continue
        if 1 <= len(b) <= 2:
            out[tok] = b
    return out


class Codec:
    """Round-trip-safe codec: a char is emitted only for its canonical
    (lowest) token; all other tokens are emitted as <$XXXX> tags, so
    decode->encode reproduces the original words exactly."""

    def __init__(self, tok2char: dict[int, str]):
        self.char2tok: dict[str, int] = {}
        for tok in sorted(tok2char):
            self.char2tok.setdefault(tok2char[tok], tok)
        self.tok2char = {
            tok: ch for tok, ch in tok2char.items() if self.char2tok[ch] == tok
        }

    def decode(self, words: list[int]) -> str:
        out: list[str] = []
        prev: int | None = None
        for w in words:
            if prev is not None and consumes_argument(prev):
                out.append(f"<${w:04X}>")  # argument word, never text
            else:
                out.append(self.tok2char.get(w, f"<${w:04X}>"))
            prev = w
        return "".join(out)

    def encode(self, text: str) -> list[int]:
        """Encode text with pair tokens while avoiding ugly word starts.

        The primary objective is still minimum token count. For equal-cost
        encodings, prefer the capital pair over a lone native capital
        (Sigma => Si + gm + a, not S + ig + ma): the native fullwidth
        capital is centered in its cell, which reads as a stray indent
        (bad in choice lists like Yes/No). A trailing single lowercase
        renders left-aligned and joins the word seamlessly.
        """
        text = text.replace(FORCE_PAGE_BREAK, "<$FFFD>")
        out: list[int] = []
        i = 0
        while i < len(text):
            m = TAG_RE.match(text, i)
            if m:
                out.append(int(m.group(1), 16))
                i = m.end()
                continue
            next_tag = TAG_RE.search(text, i)
            j = next_tag.start() if next_tag else len(text)
            out.extend(self._encode_plain(text[i:j], i))
            i = j
        return out

    @staticmethod
    def _caps_run_len(text: str, i: int) -> int:
        """Length of the maximal uppercase-letter run containing text[i]."""
        def caps(ch: str) -> bool:
            return ch.isalpha() and ch.isupper()
        if not caps(text[i]):
            return 0
        a = i
        while a > 0 and caps(text[a - 1]):
            a -= 1
        b = i
        while b + 1 < len(text) and caps(text[b + 1]):
            b += 1
        return b - a + 1

    def _encode_plain(self, text: str, base_pos: int) -> list[int]:
        n = len(text)
        # dp[i] = (token_count, visual_penalty, token_list)
        dp: list[tuple[int, int, list[int]] | None] = [None] * (n + 1)
        dp[n] = (0, 0, [])
        for i in range(n - 1, -1, -1):
            best: tuple[int, int, list[int]] | None = None
            for width in (2, 1):
                piece = text[i : i + width]
                if len(piece) != width:
                    continue
                # An all-caps word of three or more letters renders as
                # uniform fullwidth singles; pairing part of it (e.g. a
                # menu pair like НА inside ВНИМАНИЕ) would make it lumpy.
                # Two-letter caps runs (АТ, DF, ДА...) still pack as pairs.
                if (width == 2 and piece.isalpha() and piece.isupper()
                        and self._caps_run_len(text, i) >= 3):
                    continue
                tok = self.char2tok.get(piece)
                tail = dp[i + width]
                if tok is None or tail is None:
                    continue
                cand = (
                    1 + tail[0],
                    self._visual_penalty(text, i, width) + tail[1],
                    [tok] + tail[2],
                )
                if best is None or cand[:2] < best[:2]:
                    best = cand
            if best is not None:
                dp[i] = best
        if dp[0] is None:
            for i, ch in enumerate(text):
                if ch not in self.char2tok and text[i : i + 2] not in self.char2tok:
                    raise ValueError(
                        f"cannot encode character {ch!r} at position {base_pos + i}"
                    )
            raise ValueError(f"cannot encode text segment at position {base_pos}")
        return dp[0][2]

    @staticmethod
    def _visual_penalty(text: str, i: int, width: int) -> int:
        if width != 1:
            return 0
        ch = text[i]
        if ch.isupper():
            # Lone capital followed by lowercase: the centered native glyph
            # leaves a gap before the left-aligned tail ("Y es", "Г из").
            # All-caps words (HARD, MP) stay penalty-free.
            nxt = text[i + 1] if i + 1 < len(text) else ""
            return 10 if nxt.islower() else 0
        if ch.islower():
            # A single lowercase is seamless at the end of a word, slightly
            # off elsewhere (half-cell gap before the next pair or hyphen).
            nxt = text[i + 1] if i + 1 < len(text) else ""
            return 1 if nxt.isalpha() or nxt == "-" else 0
        if ch == "-":
            prev = text[i - 1] if i else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            # A native standalone hyphen occupies a full cell despite its
            # narrow ink. Prefer an allocated boundary pair inside words.
            return 2 if prev.isalpha() or nxt.isalpha() else 0
        return 0
