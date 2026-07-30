#!/usr/bin/env python3
"""Re-flow translated dump records to the real text window width.

Within each record, text between control tags is re-wrapped: existing
<$FFFC> line breaks are treated as soft (replaced by spaces) and new ones
are inserted so that no rendered line exceeds the width budget in glyph
CELLS (pair tokens count as one cell). Words are never split. In non-battle
scene chunks, <$FFFD> page breaks are compacted only when the following page
is plain text with no event controls; battle chunks keep page breaks hard
because battle scripts can couple paging to portrait/event state. All other
control tags (FB00+arg, FFF4/FFF3 highlights, terminators) stay exactly
where they are.

The player-name macro <$F600><$0000> counts as NAME_CELLS (the name entry
screen allows 8 native glyphs — measured in-game); explicit printable
tags such as the <$0000> indent space count as one cell. All windows
that auto-wrap (dialogue, narration/briefing, quiz) are 21 cells wide
(measured in-game with a ruler build).

The engine draws the speaker name plate inline at the start of a plated
window, so plated first lines are shorter by the plate width. The production
wrapper reads each record's display command and takes the visible speaker slot
from byte `p+9`. `0xFF` means no plate; values outside the local speaker pool
fall back to the widest plate in the chunk. The pool size comes from the chunk
VM header (+0x38) in the original SCEN.DAT, which excludes location plates;
speaker plate names are kept short (<= 5 cells) so fallback bounds stay tight.

Choice records (text starting with ・) are kept single-line and reported
if they exceed the width.
"""
import argparse
import re
import sys
from pathlib import Path

from langrisser.project import add_language_args, language_from_args
from langrisser.scen import FORCE_PAGE_BREAK, TAG_RE, Codec, consumes_argument, \
    find_text_block, load_charmap_tbl, read_chunk_spans, words_from_bytes
from langrisser.speakers import trace_vm_bytecode, u16, u32, vm_block

LINE_BREAK = "<$FFFC>"
PAGE_BREAK = "<$FFFD>"
TERMINATORS = {"<$FFFE>", "<$FFFF>"}
PAGE_BREAKS = {PAGE_BREAK, *TERMINATORS}
PRINTABLE_TAG_LIMIT = 0xE000
NAME_MACRO = 0xF600
NAME_CELLS = 8
BATTLE_CHUNKS = set(range(1, 43))
# A yes/no confirmation box (display opcode 0x0c) covers the right side of the
# window's 3rd and 4th lines; reserve this many cells there so text clears it.
YESNO_TAIL_RESERVE = 8


def can_compact_pages(chunk_idx: int, compact_battle_pages: bool = False) -> bool:
    """Only scene/recap chunks get automatic FFFD demotion by default.

    Battle chunks have VM/event state around dialogue and portraits; playtest
    found that demoting seemingly plain FFFD continuations there can corrupt
    character images. Keep those page breaks structural unless explicitly
    requested for experiments.
    """
    return compact_battle_pages or chunk_idx not in BATTLE_CHUNKS


def cells(codec: Codec, text: str) -> int:
    return len(codec.encode(text))


# Punctuation that must never start a line: it has to stay with the word it
# follows. A break is never opened before a run made only of these (e.g. a word
# and its "？", "！", "?!", "…" must wrap together, not apart). "・" is excluded:
# it is the choice/list bullet and is meant to start a line.
PUNCT_RUN_CHARS = set("？！?!…‥。、，．：；:;,.")


def is_punct_run(s: str) -> bool:
    return bool(s) and all(ch in PUNCT_RUN_CHARS for ch in s)


def wrap(codec: Codec, text: str, width: int) -> str:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if cells(codec, cand) <= width:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return LINE_BREAK.join(lines)


def wrap_stream(codec: Codec, text: str, width: int, reserve: int = 0,
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
    last_page = sum(1 for _a, _b, t in structural_page_markers(text) if t == PAGE_BREAK)

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
            if can_break and line_reserve + visible_cells(codec, candidate) + extra > width:
                out.append(LINE_BREAK)
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

    tags = list(TAG_RE.finditer(text))
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
        val = int(m.group(1), 16)
        consumed = 1
        if consumes_argument(val) and i + 1 < len(tags) and tags[i + 1].start() == m.end():
            tag_text += tags[i + 1].group(0)
            consumed = 2
        if val == NAME_MACRO:
            atom_parts.append(tag_text)
        elif val < PRINTABLE_TAG_LIMIT:
            atom_parts.append(tag_text)
        elif tag_text == LINE_BREAK:
            flush_atom()
            saw_space = True
        elif tag_text in PAGE_BREAKS:
            flush_atom()
            out.extend(pending_tags)
            pending_tags.clear()
            out.append(tag_text)
            if tag_text == PAGE_BREAK:
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


def safe_page_continuation(text: str, start: int) -> bool:
    """True if the page after an FFFD can be treated as plain continuation.

    The check is intentionally conservative: scan until the next page/end
    marker and reject any event/control opcode. Printable tags, line breaks,
    highlights, and the player-name macro are text-like and safe.
    """
    tags = [m for m in TAG_RE.finditer(text) if m.start() >= start]
    i = 0
    while i < len(tags):
        m = tags[i]
        tag_text = m.group(0)
        val = int(m.group(1), 16)
        if tag_text in PAGE_BREAKS:
            return True
        if tag_text == LINE_BREAK or tag_text in ("<$FFF4>", "<$FFF3>"):
            i += 1
            continue
        if val < PRINTABLE_TAG_LIMIT:
            i += 1
            continue
        if val == NAME_MACRO:
            if i + 1 >= len(tags) or tags[i + 1].start() != m.end():
                return False
            i += 2
            continue
        return False
    return True


def structural_page_markers(text: str) -> list[tuple[int, int, str]]:
    """Return page/end markers that are not arguments of control opcodes."""
    out: list[tuple[int, int, str]] = []
    tags = list(TAG_RE.finditer(text))
    i = 0
    while i < len(tags):
        m = tags[i]
        val = int(m.group(1), 16)
        consumed = 1
        if consumes_argument(val) and i + 1 < len(tags) \
                and tags[i + 1].start() == m.end():
            consumed = 2
        if consumed == 1 and m.group(0) in PAGE_BREAKS:
            out.append((m.start(), m.end(), m.group(0)))
        i += consumed
    return out


def page_segments(text: str) -> list[str]:
    """Split on structural page/end markers, ignoring control arguments.

    The authoring-only forced page break is a hard page boundary too, so it
    splits pages here even though it is not a <$XXXX> tag.
    """
    out: list[str] = []
    for chunk in text.split(FORCE_PAGE_BREAK):
        start = 0
        for a, b, _tag in structural_page_markers(chunk):
            out.append(chunk[start:a])
            start = b
        out.append(chunk[start:])
    return out


def page_heights_ok(text: str, max_lines: int) -> bool:
    """Check rendered page height after wrapping."""
    for page in page_segments(text):
        if page.strip() and page.count(LINE_BREAK) + 1 > max_lines:
            return False
    return True


def compact_safe_pages(codec: Codec, text: str, width: int, reserve: int,
                       max_lines: int) -> str:
    """Iteratively demote safe FFFD page breaks to soft line breaks.

    Each candidate is rewrapped and accepted only if it stays within the
    page-height budget. This keeps voiced/event boundaries intact while
    allowing plain continuation pages to fill the window.
    """
    current = text
    while True:
        changed = False
        for pos, end, tag in structural_page_markers(current):
            if tag != PAGE_BREAK:
                continue
            if not safe_page_continuation(current, end):
                continue
            candidate = current[:pos] + LINE_BREAK + current[end:]
            wrapped = wrap_stream(codec, candidate, width, reserve)
            if page_heights_ok(wrapped, max_lines):
                current = candidate
                changed = True
                break
        if not changed:
            return current


def reflow_record(codec: Codec, text: str, width: int, reserve: int = 0,
                  force_plate: bool = False, max_lines: int = 4,
                  compact_pages: bool = True, tail_reserve: int = 0) -> str:
    # A forced page break splits the record into segments that are reflowed
    # independently, so compaction can never merge text across it. Only the
    # first segment keeps the speaker plate (continuation pages redraw none),
    # and only the last segment carries the yes/no tail reserve.
    if FORCE_PAGE_BREAK in text:
        segments = text.split(FORCE_PAGE_BREAK)
        last = len(segments) - 1
        out = [
            reflow_record(codec, seg, width,
                          reserve if i == 0 else 0,
                          force_plate if i == 0 else False,
                          max_lines, compact_pages,
                          tail_reserve if i == last else 0)
            for i, seg in enumerate(segments)
        ]
        return FORCE_PAGE_BREAK.join(out)
    # The plate reserve only applies to spoken records; narration windows
    # have no name plate. Some windows keep the previous speaker's plate
    # on records without their own FB00 marker (force_plate).
    if "<$FB" not in text and not force_plate:
        reserve = 0
    # Yes/no records are single-page prompts; skip page compaction so the
    # tail reserve cannot collide with a merged page.
    if compact_pages and not tail_reserve:
        text = compact_safe_pages(codec, text, width, reserve, max_lines)
    return wrap_stream(codec, text, width, reserve, tail_reserve)



def speaker_pool_sizes(scen_path: Path) -> dict[int, int]:
    """Speaker-name pool size per chunk from the original SCEN.DAT.

    The chunk VM header word +0x38 counts the FFFF-terminated speaker
    plates at the head of the record list. Location plates and scene
    captions that follow the pool are not counted (verified against all
    131 chunks), so this is the exact set of names the engine can draw
    inline in the dialogue window."""
    sizes: dict[int, int] = {}
    data = scen_path.read_bytes()
    for ci, (start, end) in enumerate(read_chunk_spans(data)):
        chunk = data[start:end]
        try:
            block = find_text_block(chunk)
        except Exception:
            continue
        if not block or not block.record_count:
            continue
        off, _vm, _stream = vm_block(chunk, block)
        if off:
            sizes[ci] = u32(chunk, off + 0x38)
    return sizes


def traced_plate_slots(scen_path: Path) -> dict[int, dict[int, int | None]]:
    """Legacy VM-tracer fallback: chunk -> record -> byte9-derived slot.

    This predates the actor-key extractor below. It is retained only as a
    conservative fallback for old traced rows; the production path is
    `semantic_plate_slots()`, which reads the speaker actor key at p+6..7.
    """
    out: dict[int, dict[int, int | None]] = {}
    data = scen_path.read_bytes()
    for ci, (start, end) in enumerate(read_chunk_spans(data)):
        chunk = data[start:end]
        try:
            block = find_text_block(chunk)
        except Exception:
            continue
        fb_records: set[int] = set()
        for idx in range(1, block.record_count + 1):
            a, b = block.record_span(idx)
            words = words_from_bytes(chunk[a:b])
            if any(word == 0xFB00 for word in words):
                fb_records.add(idx)
        if not fb_records:
            continue
        first_fb_record = min(fb_records)
        rows = trace_vm_bytecode(
            source_file=scen_path.name,
            chunk_index=ci,
            chunk_start=start,
            chunk=chunk,
            block=block,
        )
        for row in rows:
            if row.kind != "display_24424" or row.display_text_id is None:
                continue
            rec_idx = first_fb_record + row.display_text_id
            if rec_idx not in fb_records:
                continue
            if row.display_byte9 == 0xFF:
                out.setdefault(ci, {})[rec_idx] = None
            elif row.display_byte9 is not None:
                out.setdefault(ci, {})[rec_idx] = row.display_byte9
    return out


def display_plate_slots(
    scen_path: Path,
    pool_sizes: dict[int, int],
) -> tuple[dict[int, dict[int, int | None]], set[int], dict[int, set[int]]]:
    """Legacy linear-scan fallback for display metadata.

    traced_plate_slots follows control flow and stops on the tutorial-style
    chunks whose bytecode it cannot walk (it misreads an early opcode-00 skip).
    This byte9-based scan is not authoritative for speakers; it remains only
    for yes/no window detection and conservative legacy fallback data. Exact
    plate reserves come from `semantic_plate_slots()`.

    Returns:
    - chunk -> record -> speaker slot (None means no plate)
    - chunks whose plate persists across unresolved records (the FB00-less ones)
    - chunk -> records that draw a yes/no confirmation box
    """
    out: dict[int, dict[int, int | None]] = {}
    persistent: set[int] = set()
    yesno: dict[int, set[int]] = {}
    data = scen_path.read_bytes()
    for ci, (start, end) in enumerate(read_chunk_spans(data)):
        pool_size = pool_sizes.get(ci) or 0
        if pool_size == 0:
            continue
        chunk = data[start:end]
        try:
            block = find_text_block(chunk)
        except Exception:
            continue
        first_text: int | None = None
        has_fb = False
        for idx in range(1, block.record_count + 1):
            words = words_from_bytes(chunk[slice(*block.record_span(idx))])
            if 0xFB00 in words:
                has_fb = True
            if first_text is None and 0xFFFE in words and 0xFFFF not in words:
                first_text = idx
        if first_text is None or (has_fb and ci != 0):
            continue
        _vm_off, vm, _ss = vm_block(chunk, block)
        rec_slots: dict[int, int | None] = {}
        p = 0
        while p + 12 <= len(vm):
            if (0x0B <= vm[p] <= 0x10 and vm[p + 3] in (0x00, 0x01, 0x03)
                    and (vm[p + 9] == 0xFF or vm[p + 9] < pool_size)
                    and u16(vm, p + 10) < block.record_count):
                rec = first_text + u16(vm, p + 10)
                if rec <= block.record_count:
                    rec_slots.setdefault(rec, None if vm[p + 9] == 0xFF else vm[p + 9])
                    # opcode 0x0c draws a yes/no confirmation box over the
                    # bottom-right of a question window. The opcode is also
                    # used by non-question quiz records, so require the native
                    # question-mark glyph before reserving that space.
                    a, b = block.record_span(rec)
                    if vm[p] == 0x0C and 0x0005 in words_from_bytes(chunk[a:b]):
                        yesno.setdefault(ci, set()).add(rec)
                p += 12
                continue
            p += 1
        if rec_slots:
            out[ci] = rec_slots
            if not has_fb:
                persistent.add(ci)
    return out, persistent, yesno


def semantic_plate_slots(scen_path: Path) -> dict[int, dict[int, int | None]]:
    """Per-record speaker plate, read straight from the display commands.

    Each display command (opcode 0x0B..0x10, 12 bytes) selects the speaker by the
    zero-based name-pool slot at byte +9, and the display order by the u16 text id
    at +10. The text records follow the name pool (records 1..pool_size), so
    `record = pool_size + 1 + text id`. byte +9 == 0xFF means no plate; a value
    >= pool_size is a location/special plate (treated as -1 -> chunk-wide reserve).
    Real commands are filtered by the mode `byte+3 & 3 <= 2` and the name-visible
    flag `byte+8 <= 1`. A record may carry more than one display command; the one
    that actually draws the plate has `byte+8 == 0` (name visible), so among a
    record's commands the lowest `(byte+8, position)` wins.

    byte +6..7 is the actor key used for other routing, not the plate. An earlier
    revision read the plate from that key (via the actor-plate table) and from
    `record = first FB00 + text id`; the in-game speaker test set rejects that
    model on the verified plated lines. See docs/L45_SPEAKER_PLATES.md.

    Returns chunk -> record -> slot, where slot is the zero-based speaker-pool
    slot, `None` for no plate, or `-1` for a location/unresolved plate (fall back
    to the chunk-wide reserve there).
    """
    from langrisser.speakers import vm_block
    out: dict[int, dict[int, int | None]] = {}
    data = scen_path.read_bytes()
    for ci, (start, end) in enumerate(read_chunk_spans(data)):
        chunk = data[start:end]
        try:
            block = find_text_block(chunk)
            vm_off, vm, _stream = vm_block(chunk, block)
        except Exception:
            continue
        pool_size = u32(chunk, vm_off + 0x38) if vm_off else 0
        if not pool_size or pool_size >= block.record_count:
            continue
        base = pool_size + 1                        # text record shown by id 0
        n_text = block.record_count - pool_size
        best: dict[int, tuple[tuple[int, int], int]] = {}
        for p in range(len(vm) - 11):
            if not (0x0B <= vm[p] <= 0x10):
                continue
            b8 = vm[p + 8]
            if (vm[p + 3] & 3) > 2 or b8 > 1:
                continue
            tid = vm[p + 10] | (vm[p + 11] << 8)
            if not (0 <= tid < n_text):
                continue
            rec = base + tid
            key = (b8, p)                           # name-visible (b8=0) wins
            if rec not in best or key < best[rec][0]:
                best[rec] = (key, vm[p + 9])
        rec_slot: dict[int, int | None] = {}
        for rec, (_key, slot) in best.items():
            if slot < pool_size:
                rec_slot[rec] = slot
            elif slot == 0xFF:
                rec_slot[rec] = None
            else:
                rec_slot[rec] = -1
        out[ci] = rec_slot
    return out


def slot_plate_reserves(codec: Codec, records: list[tuple[str, str]],
                        pool_size: int | None = None) -> dict[int, int]:
    """Zero-based speaker-pool slot -> plate reserve in rendered cells."""
    if pool_size is None:
        return {}
    by_idx = {int(idx): text for idx, text in records if idx.isdigit()}
    reserves: dict[int, int] = {}
    for slot in range(pool_size):
        text = by_idx.get(slot + 1)
        if not text or not text.endswith("<$FFFF>"):
            continue
        plate = text[: -len("<$FFFF>")]
        reserves[slot] = visible_cells(codec, plate) + 1
    return reserves


def plate_reserve(codec: Codec, records: list[tuple[str, str]],
                  pool_size: int | None = None) -> int:
    """Worst-case speaker plate width for a chunk, plus one cell for the
    bracket the engine draws after the name. (The speaker of a given line
    is bound by VM runtime state, so the widest pool plate is the safe
    bound.) With a known pool size, exactly the first pool_size records
    are the speaker plates; without one, fall back to the FFFF-terminated
    records before the first FFFE objective record."""
    widest = 0
    for idx, text in records:
        if pool_size is not None:
            if not idx.isdigit() or int(idx) > pool_size:
                break
        elif text.endswith("<$FFFE>"):
            break
        if text.endswith("<$FFFF>"):
            widest = max(widest, visible_cells(codec, text[: -len("<$FFFF>")]))
    return widest + 1 if widest else 0


def visible_cells(codec: Codec, text: str) -> int:
    """Rendered width of a tag-bearing string in cells, with the name
    macro at its worst-case width."""
    n = 0
    pos = 0
    tags = list(TAG_RE.finditer(text))
    i = 0
    while i <= len(tags):
        raw = text[pos : tags[i].start()] if i < len(tags) else text[pos:]
        n += cells(codec, raw)
        if i == len(tags):
            break
        val = int(tags[i].group(1), 16)
        consumed = 1
        if consumes_argument(val) and i + 1 < len(tags) \
                and tags[i + 1].start() == tags[i].end():
            consumed = 2
        if val == NAME_MACRO:
            n += NAME_CELLS
        elif val < PRINTABLE_TAG_LIMIT:
            n += 1
        pos = tags[i + consumed - 1].end()
        i += consumed
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--translation-root", default=None,
                    help="Override the language pack's translated-text root.")
    ap.add_argument("--tbl", default=None)
    ap.add_argument("--scen", default="work/l5/extracted/SCEN.DAT")
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--choice-width", type=int, default=None)
    # The JP script routinely shows 4-line pages after engine wrap (594 of
    # them; 5-line pages exist but are rare), so 4 is the safe page height.
    ap.add_argument("--max-lines", type=int, default=None)
    ap.add_argument("--compact-battle-pages", action="store_true",
                    help="Experimental: also demote safe-looking FFFD breaks in battle chunks.")
    args = ap.parse_args()

    lang = language_from_args(args)
    dump_root = (Path(args.translation_root)
                 if args.translation_root else lang.dump_root)
    tbl = Path(args.tbl) if args.tbl else lang.tbl
    width_arg = args.width if args.width is not None else lang.window_width
    choice_width = args.choice_width if args.choice_width is not None else lang.choice_width
    max_lines = args.max_lines if args.max_lines is not None else lang.max_lines

    codec = Codec(load_charmap_tbl(tbl))
    scen_path = Path(args.scen)
    pool_sizes = speaker_pool_sizes(scen_path) if scen_path.exists() else {}
    traced_slots = traced_plate_slots(scen_path) if scen_path.exists() else {}
    semantic_slots = semantic_plate_slots(scen_path) if scen_path.exists() else {}
    if scen_path.exists():
        display_slots, persistent_chunks, yesno_slots = display_plate_slots(scen_path, pool_sizes)
    else:
        display_slots, persistent_chunks, yesno_slots = {}, set(), {}
    if not pool_sizes:
        print(f"WARNING: {args.scen} not found; falling back to the FFFF-prefix "
              "plate heuristic (location plates may inflate the reserve)")
    choice_errors = 0
    for fp in sorted(dump_root.glob("*/chunk_*.txt")):
        records = []
        for raw in fp.read_text(encoding="utf-8").splitlines():
            if "\t" in raw and not raw.startswith("#"):
                records.append(tuple(raw.split("\t", 1)))
        chunk_idx = int(fp.stem.split("_")[1])
        pool_size = pool_sizes.get(chunk_idx)
        chunk_reserve = plate_reserve(codec, records, pool_size)
        compact_pages = can_compact_pages(chunk_idx, args.compact_battle_pages)
        slot_reserves = slot_plate_reserves(codec, records, pool_size)
        # Path-traced slots are authoritative; the linear display scan fills in
        # the chunks the tracer cannot walk. Require several resolved plates
        # before trusting the scan: a lone display match in a narration chunk
        # (e.g. the recap chunks 129/130) is byte-slide noise and must not force
        # a plate reserve on every line.
        disp = display_slots.get(chunk_idx, {})
        confident = sum(1 for s in disp.values() if s is not None) >= 3
        yesno_records = yesno_slots.get(chunk_idx, set())
        record_slots = dict(traced_slots.get(chunk_idx, {}))
        if confident:
            for rec, slot in disp.items():
                record_slots.setdefault(rec, slot)
        # The semantic display-command read is authoritative per record (it
        # covers FB00-less spoken lines the tracer misses); it overrides the
        # tracer/scan where it has a value. Chunk 0 is the quiz, whose FB00
        # markers are portraits rather than name plates, so it keeps its
        # reserve-0 handling below.
        if chunk_idx != 0:
            for rec, slot in semantic_slots.get(chunk_idx, {}).items():
                record_slots[rec] = slot
        # Unresolved dialogue keeps the persistent plate only in the FB00-less
        # chunks where one speaker narrates the whole block; the quiz's plate
        # does not persist, so only explicitly resolved records carry a reserve.
        plated_no_fb = confident and chunk_idx in persistent_chunks
        width = width_arg
        out_lines: list[str] = []
        for raw in fp.read_text(encoding="utf-8").splitlines():
            if "\t" not in raw or raw.startswith("#"):
                out_lines.append(raw)
                continue
            idx, text = raw.split("\t", 1)
            reserve = chunk_reserve
            stripped = TAG_RE.sub("", text)
            if stripped.count("・") > 1:
                # multi-bullet objective lists keep their structure verbatim
                out_lines.append(raw)
                continue
            if stripped.startswith("・"):
                # Choices must stay single-line: a wrapped tail becomes a
                # bogus selectable row in the game's menu.
                if "<$FFFE>" in text:
                    head, tail = text.split("<$FFFE>", 1)
                    head = " ".join(head.replace(LINE_BREAK, " ").split())
                    new_text = f"{head}<$FFFE>{tail}"
                else:
                    new_text = " ".join(text.replace(LINE_BREAK, " ").split())
                n = visible_cells(codec, new_text.split("<$FFFE>")[0])
                if n > choice_width:
                    print(f"{fp.name} record {idx}: choice is {n} cells (max {choice_width})")
                    choice_errors += 1
                out_lines.append(f"{idx}\t{new_text}")
            else:
                force_plate = False
                rec_idx = int(idx)
                if rec_idx in record_slots:
                    slot = record_slots[rec_idx]
                    if slot is None:
                        reserve = 0
                    elif slot == -1:
                        # Unresolved crowd key (runtime-remapped plate): keep the
                        # conservative chunk-wide reserve so the line still clears.
                        reserve = chunk_reserve
                        force_plate = True
                    else:
                        if slot in slot_reserves:
                            reserve = slot_reserves[slot]
                        force_plate = True
                elif plated_no_fb and not text.rstrip().endswith("<$FFFF>"):
                    force_plate = True
                # The quiz's FB00 markers are portraits, not speaker plates, so
                # records the display scan did not resolve carry no reserve.
                if chunk_idx == 0 and not force_plate:
                    reserve = 0
                tail_reserve = YESNO_TAIL_RESERVE if rec_idx in yesno_records else 0
                new_text = reflow_record(
                    codec, text, width, reserve, force_plate,
                    max_lines=max_lines,
                    compact_pages=compact_pages,
                    tail_reserve=tail_reserve,
                )
                # Page height check: lines between page/terminator controls.
                for page in page_segments(new_text):
                    n = page.count(LINE_BREAK) + 1
                    if page.strip() and n > max_lines:
                        print(f"{fp.name} record {idx}: page has {n} lines (max {max_lines})")
                out_lines.append(f"{idx}\t{new_text}")
        fp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print(f"reflowed {fp}")
    if choice_errors:
        print(f"{choice_errors} choice record(s) exceed the safe single-line width")
        sys.exit(1)


if __name__ == "__main__":
    main()
