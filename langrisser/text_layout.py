#!/usr/bin/env python3
"""Lay a tagged text stream out in a window: line breaks, page breaks, heights.

Wrapping a script record is the same problem in every engine here — fit words
into a window of N cells by M lines without splitting a word, without letting a
control tag drift across a break, and without treating a zero-width tag as a
line of its own. What differs is only the tag vocabulary: which tag ends a
line, which ends a page, which draws something, and which consumes the tag
after it as an argument.

So that vocabulary is a `Layout` the caller supplies, and everything below is
shared. Langrisser V's dump speaks in `<$FFFC>` and `<$FFFD>`; Langrisser I &
II's speaks in `<line>` and `<page>`; the wrapping is the same either way.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from langrisser.font_units import is_punct_run

PRINTABLE = "printable"
LINE = "line"
PAGE = "page"
ZERO = "zero"


@dataclass(frozen=True)
class Layout:
    """One engine's tag vocabulary, as the wrapper needs to see it."""

    tag_re: re.Pattern
    line_break: str
    page_break: str
    page_breaks: frozenset
    cells: Callable[[str], int]
    kind: Callable[[str], str]
    tag_cells: Callable[[str], int] = lambda tag: 0
    takes_argument: Callable[[str], bool] = lambda tag: False
    force_page_break: str = ""


def visible_cells(layout: Layout, text: str) -> int:
    """Rendered width of a tag-bearing string, in cells.

    A tag that takes an argument swallows the tag after it: the argument is
    data, not something the window draws, so it must not be measured.
    """
    total = 0
    pos = 0
    tags = list(layout.tag_re.finditer(text))
    i = 0
    while i <= len(tags):
        raw = text[pos:tags[i].start()] if i < len(tags) else text[pos:]
        total += layout.cells(raw)
        if i == len(tags):
            break
        tag = tags[i].group(0)
        consumed = 1
        if layout.takes_argument(tag) and i + 1 < len(tags) \
                and tags[i + 1].start() == tags[i].end():
            consumed = 2
        total += layout.tag_cells(tag)
        pos = tags[i + consumed - 1].end()
        i += consumed
    return total


def structural_page_markers(layout: Layout, text: str) -> list[tuple[int, int, str]]:
    """Page and end markers that are not arguments of a control opcode."""
    out: list[tuple[int, int, str]] = []
    tags = list(layout.tag_re.finditer(text))
    i = 0
    while i < len(tags):
        m = tags[i]
        tag = m.group(0)
        if layout.takes_argument(tag) and i + 1 < len(tags) and tags[i + 1].start() == m.end():
            i += 2
            continue
        if tag in layout.page_breaks:
            out.append((m.start(), m.end(), tag))
        i += 1
    return out


def page_segments(layout: Layout, text: str) -> list[str]:
    """The record split into the pages the screen shows one at a time."""
    out: list[str] = []
    chunks = text.split(layout.force_page_break) if layout.force_page_break else [text]
    for chunk in chunks:
        start = 0
        for a, b, _tag in structural_page_markers(layout, chunk):
            out.append(chunk[start:a])
            start = b
        out.append(chunk[start:])
    return out


def page_heights_ok(layout: Layout, text: str, max_lines: int) -> bool:
    for page in page_segments(layout, text):
        if page.strip() and page.count(layout.line_break) + 1 > max_lines:
            return False
    return True


def wrap_stream(layout: Layout, text: str, width: int, reserve: int = 0,
                tail_reserve: int = 0) -> str:
    """Wrap a mixed text/control stream without treating control tags as a
    visual line reset. Zero-width control tags create safe break points
    before the next printable word; tags glued to the tail of a word (such
    as the highlight-off after the word) stay with that word. The name
    macro and printable tags carry real cell widths.

    `reserve` is the speaker-plate width: the engine draws the name plate
    and its bracket inline at the start of the window, so the first line
    of a plated record is shorter by that amount. Continuation pages after
    <$FFFD> do not redraw the plate and therefore restart at full width."""
    out: list[str] = []
    line_parts: list[str] = []
    pending_tags: list[str] = []
    atom_parts: list[str] = []
    line_reserve = reserve
    line_has_text = False
    saw_space = False
    saw_tag_boundary = False
    line_no = 0
    # The yes/no box only sits on the page that shows the prompt (the last
    # page); earlier pages of a multi-page record use the full width.
    page_no = 0
    last_page = sum(1 for _a, _b, t in structural_page_markers(layout, text) if t == layout.page_break)

    def flush_atom() -> None:
        nonlocal line_reserve, line_has_text, saw_space, saw_tag_boundary, line_no
        if not atom_parts and not pending_tags:
            return
        if atom_parts:
            sep = " " if saw_space and line_has_text else ""
            can_break = line_has_text and (saw_space or saw_tag_boundary)
            candidate = "".join(line_parts + ([sep] if sep else [])
                                + pending_tags + atom_parts)
            # A yes/no confirmation box sits over the right of the 3rd and 4th
            # lines, so those lines (0-based index >= 2) lose tail_reserve cells.
            extra = tail_reserve if (line_no >= 2 and page_no == last_page) else 0
            if can_break and line_reserve + visible_cells(layout, candidate) + extra > width:
                out.append(layout.line_break)
                line_no += 1
                line_parts.clear()
                line_reserve = 0
                sep = ""
            elif sep:
                out.append(sep)
                line_parts.append(sep)
            out.extend(pending_tags)
            line_parts.extend(pending_tags)
            pending_tags.clear()
            out.extend(atom_parts)
            line_parts.extend(atom_parts)
            atom_parts.clear()
            line_has_text = True
            saw_space = False
            saw_tag_boundary = False

    tags = list(layout.tag_re.finditer(text))
    pos = 0
    i = 0
    while i <= len(tags):
        raw = text[pos : tags[i].start()] if i < len(tags) else text[pos:]
        parts = [p for p in re.split(r"(\s+)", raw) if p]
        k = 0
        while k < len(parts):
            part = parts[k]
            if part.isspace():
                # Keep trailing punctuation with its word: if a run of pure
                # punctuation follows this space, glue the space + punctuation
                # into the current atom instead of opening a break here, so the
                # word and its punctuation wrap together rather than apart.
                nxt = parts[k + 1] if k + 1 < len(parts) else ""
                if atom_parts and is_punct_run(nxt):
                    atom_parts.append(part)
                    atom_parts.append(nxt)
                    k += 2
                    continue
                flush_atom()
                saw_space = True
            else:
                atom_parts.append(part)
            k += 1
        if i == len(tags):
            break
        m = tags[i]
        tag_text = m.group(0)
        consumed = 1
        if layout.takes_argument(tag_text) and i + 1 < len(tags) \
                and tags[i + 1].start() == m.end():
            tag_text += tags[i + 1].group(0)
            consumed = 2
        kind = layout.kind(tag_text)
        if kind == PRINTABLE:
            atom_parts.append(tag_text)
        elif kind == LINE:
            flush_atom()
            saw_space = True
        elif kind == PAGE:
            flush_atom()
            out.extend(pending_tags)
            pending_tags.clear()
            out.append(tag_text)
            if tag_text == layout.page_break:
                page_no += 1
            line_parts.clear()
            line_reserve = 0
            line_no = 0
            line_has_text = False
            saw_space = False
            saw_tag_boundary = False
        elif atom_parts:
            # zero-width tag glued to a word tail (e.g. highlight-off):
            # keep it inside the atom so it never drifts across a break.
            atom_parts.append(tag_text)
        else:
            pending_tags.append(tag_text)
            saw_tag_boundary = True
        pos = m.start() + len(tag_text)
        i += consumed
    flush_atom()
    out.extend(pending_tags)
    return "".join(out)


