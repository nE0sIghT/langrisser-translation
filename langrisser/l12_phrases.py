#!/usr/bin/env python3
"""Rebuild a chunk's phrase table around the target text.

Part 4 is the script's own compression: 239 entries, and a reference to one
costs two bytes wherever the words would have cost more. The Japanese entries
are Japanese — `るため、`, `たのである。` — so a translated record references
none of them, and the table sits in the chunk as roughly two kilobytes of dead
weight at exactly the moment the chunk has none to spare.

So the table is rebuilt rather than translated: entries nothing references are
emptied, and the freed indices are given the substrings the target text repeats
most. This is the mechanism the original uses, pointed at the new language.

Two things pin an entry. Parts 0-3 are the menus and the item and spell names,
which are not translated yet and still reference the table; so does any record
in this chunk we are not rewriting. Those keep both their index and their
bytes.

Nothing here touches the pack files. The choice of phrases is a property of the
build, not of the translation, and a human-edited line stays a human-edited
line.
"""
from __future__ import annotations

import collections
import re

from langrisser.l12_scen import CONTROLS, PHRASE_PART, Writer

TAG_RE = re.compile(r"<[^>]*>")
PHRASE_CODE = next(c for c, (name, _) in CONTROLS.items() if name == "phrase")
REFERENCE_COST = 2      # the control byte and its operand
TERMINATOR_COST = 1     # the 0x00 that ends a stored string
MIN_LENGTH = 2
# Long enough for a whole short line: a record that repeats another word for
# word then costs two bytes, which is where most of the saving is.
MAX_LENGTH = 64
ENTRIES = 239


def phrase_refs(raw: bytes):
    """Every phrase index one encoded string references."""
    i = 0
    while i < len(raw):
        v = raw[i]
        if v in CONTROLS:
            takes = CONTROLS[v][1]
            if v == PHRASE_CODE and i + 1 < len(raw):
                yield raw[i + 1]
            i += 2 if takes else 1
        elif v >= 0xF7 and i + 1 < len(raw):
            i += 2
        else:
            i += 1


def pinned_indices(chunk, records: dict[tuple[int, int], str]) -> set[int]:
    """Indices a string we are not rewriting still points at."""
    pinned: set[int] = set()
    for pi, part in enumerate(chunk.parts):
        if pi == PHRASE_PART:
            continue
        for si, raw in enumerate(part):
            if raw and (pi, si) not in records:
                pinned.update(phrase_refs(raw))
    for raw in chunk.part(PHRASE_PART):
        pinned.update(phrase_refs(raw))
    return pinned


def segments(text: str) -> list[str]:
    """The runs of a record a phrase may be cut from.

    Line and page breaks stay in the record: a phrase that swallowed one would
    move the layout into the table, where the next record to reference it would
    inherit a break it never asked for.
    """
    return [s for s in TAG_RE.sub("\n", text).split("\n") if s]


def candidates(texts: dict[tuple[int, int], str]) -> collections.Counter:
    counts: collections.Counter = collections.Counter()
    for text in texts.values():
        for seg in segments(text):
            for size in range(MIN_LENGTH, MAX_LENGTH + 1):
                for i in range(len(seg) - size + 1):
                    counts[seg[i:i + size]] += 1
    return counts


def replace_outside_tags(text: str, phrase: str, tag: str) -> str:
    """Swap `phrase` for `tag` in the words only.

    A plain `str.replace` would also match inside a tag it had already
    written — `<phrase:12>` contains `hr` like any word does — and quietly
    produce a control nobody wrote.
    """
    out, last = [], 0
    for m in TAG_RE.finditer(text):
        out.append(text[last:m.start()].replace(phrase, tag))
        out.append(m.group(0))
        last = m.end()
    out.append(text[last:].replace(phrase, tag))
    return "".join(out)


def saving(phrase: str, uses: int, writer: Writer) -> int:
    """Bytes gained by storing `phrase` once and referencing it `uses` times."""
    try:
        inline = len(writer.encode(phrase))
    except ValueError:
        return 0
    return uses * (inline - REFERENCE_COST) - (inline + TERMINATOR_COST)


def rebuild(chunk, records: dict[tuple[int, int], str], writer: Writer):
    """Return the chunk's new phrase table and the records rewritten to use it.

    Greedy, and recounted after every pick: taking a phrase changes what the
    remaining candidates are worth, since the text they would have covered is
    now behind a reference.
    """
    pinned = pinned_indices(chunk, records)
    original = list(chunk.part(PHRASE_PART))
    free = [n for n in range(1, ENTRIES + 1) if n not in pinned]
    texts = dict(records)
    chosen: dict[int, str] = {}

    for index in free:
        counts = candidates(texts)
        best, best_gain = None, 0
        for phrase, uses in counts.items():
            if uses < 2:
                continue
            gain = saving(phrase, uses, writer)
            if gain > best_gain:
                best, best_gain = phrase, gain
        if best is None:
            break
        chosen[index] = best
        tag = f"<phrase:{index}>"
        texts = {key: replace_outside_tags(text, best, tag)
                 for key, text in texts.items()}

    table: list[bytes] = []
    for n in range(1, ENTRIES + 1):
        if n in chosen:
            table.append(writer.encode(chosen[n]))
        elif n in pinned and n - 1 < len(original):
            table.append(original[n - 1])
        else:
            table.append(b"")
    return table, texts
