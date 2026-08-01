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

from langrisser.l12_scen import BANK_FIRST, CONTROLS, PHRASE_PART, Writer

PHRASE_CODE = next(c for c, (name, _) in CONTROLS.items() if name == "phrase")
REFERENCE_COST = 2      # the control byte and its operand
TERMINATOR_COST = 1     # the 0x00 that ends a stored string
MIN_LENGTH = 2
# Long enough for a whole short line: a record that repeats another word for
# word then costs two bytes, which is where most of the saving is.
MAX_LENGTH = 64
ENTRIES = 239
Unit = tuple[bytes, bool]  # encoded bytes, eligible for phrase compression
Phrase = tuple[bytes, ...]


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


def compressible_runs(units: list[Unit]):
    """Yield contiguous printable runs; controls are hard boundaries."""
    run: list[bytes] = []
    for raw, compressible in units:
        if compressible:
            run.append(raw)
        elif run:
            yield run
            run = []
    if run:
        yield run


def candidates(records: dict[tuple[int, int], list[Unit]]) -> collections.Counter:
    counts: collections.Counter = collections.Counter()
    for units in records.values():
        for run in compressible_runs(units):
            for size in range(MIN_LENGTH, min(MAX_LENGTH, len(run)) + 1):
                # Match the left-to-right, non-overlapping replacement below.
                # Counting overlapping windows would overstate the saving for
                # repeated one-tile patterns.
                last_end: dict[Phrase, int] = {}
                for i in range(len(run) - size + 1):
                    phrase = tuple(run[i:i + size])
                    if i >= last_end.get(phrase, -1):
                        counts[phrase] += 1
                        last_end[phrase] = i + size
    return counts


def replace_phrase(units: list[Unit], phrase: Phrase, reference: bytes) -> list[Unit]:
    """Replace complete printable-token sequences with one phrase control."""
    out: list[Unit] = []
    i = 0
    while i < len(units):
        here = units[i:i + len(phrase)]
        if len(here) == len(phrase) and all(
                compressible for _raw, compressible in here) and tuple(
                    raw for raw, _compressible in here) == phrase:
            out.append((reference, False))
            i += len(phrase)
        else:
            out.append(units[i])
            i += 1
    return out


def saving(phrase: Phrase, uses: int) -> int:
    """Bytes gained by storing encoded tiles once and referencing them."""
    inline = sum(len(unit) for unit in phrase)
    return uses * (inline - REFERENCE_COST) - (inline + TERMINATOR_COST)


def expand_references(raw: bytes, table: list[bytes], generated: set[int]) -> bytes:
    """Expand generated phrase controls for the post-compression invariant."""
    out = bytearray()
    i = 0
    while i < len(raw):
        value = raw[i]
        if (value == PHRASE_CODE and i + 1 < len(raw)
                and raw[i + 1] in generated):
            index = raw[i + 1]
            if not 1 <= index <= len(table):
                raise ValueError(f"phrase reference {index} is outside the table")
            out += table[index - 1]
            i += 2
        elif value in CONTROLS:
            takes = CONTROLS[value][1]
            size = 2 if takes else 1
            out += raw[i:i + size]
            i += size
        elif value >= BANK_FIRST and i + 1 < len(raw):
            out += raw[i:i + 2]
            i += 2
        else:
            out.append(value)
            i += 1
    return bytes(out)


def rebuild(chunk, records: dict[tuple[int, int], str], writer: Writer):
    """Return the chunk's new phrase table and the records rewritten to use it.

    Greedy, and recounted after every pick: taking a phrase changes what the
    remaining candidates are worth, since the text they would have covered is
    now behind a reference.
    """
    pinned = pinned_indices(chunk, records)
    original_table = list(chunk.part(PHRASE_PART))
    encoded = {key: writer.encoded_units(text) for key, text in records.items()}
    original_records = {key: b"".join(raw for raw, _compressible in units)
                        for key, units in encoded.items()}
    # An explicitly authored <phrase:N> is a real dependency too. It belongs
    # to a rewritten record, so pinned_indices cannot discover it in the
    # original chunk.
    for raw in original_records.values():
        pinned.update(phrase_refs(raw))
    free = [n for n in range(1, ENTRIES + 1) if n not in pinned]
    chosen: dict[int, Phrase] = {}

    for index in free:
        counts = candidates(encoded)
        best, best_gain = None, 0
        for phrase, uses in counts.items():
            if uses < 2:
                continue
            gain = saving(phrase, uses)
            if gain > best_gain:
                best, best_gain = phrase, gain
        if best is None:
            break
        chosen[index] = best
        reference = bytes((PHRASE_CODE, index))
        encoded = {key: replace_phrase(units, best, reference)
                   for key, units in encoded.items()}

    table: list[bytes] = []
    for n in range(1, ENTRIES + 1):
        if n in chosen:
            table.append(b"".join(chosen[n]))
        elif n in pinned and n - 1 < len(original_table):
            table.append(original_table[n - 1])
        else:
            table.append(b"")
    rewritten = {key: b"".join(raw for raw, _compressible in units)
                 for key, units in encoded.items()}
    for key, raw in rewritten.items():
        if expand_references(raw, table, set(chosen)) != original_records[key]:
            raise ValueError(
                f"phrase compression changed the tiled stream for record {key}")
    return table, rewritten
