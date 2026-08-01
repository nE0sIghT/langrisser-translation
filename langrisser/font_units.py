#!/usr/bin/env python3
"""What a target language needs from a glyph plane: single tiles and pairs.

This is the language half of font packing, and it does not depend on which
game's plane the tiles end up in — a Russian word tiles the same way whether
the cell comes from Langrisser V's `SYSTEM.BIN` or Langrisser I & II's
`FONT.DAT`. Both allocators call `needed_units` and then spend their own
sacrificial slots in the order it returns.

The subtle part is `continuity_pairs`. A pair tile holds two half-width
characters in one native cell, so an odd-length word has two ways to tile and
the wrong one strands a full-width letter in the middle of narrow ones. Picking
pairs by frequency alone gets this wrong, and it also misses the pairs that
span a space, which are what keep word edges from doubling the inter-word gap.
"""
from __future__ import annotations

import collections
import re

WORD_RE = re.compile(r"[\w'.,]+", re.UNICODE)
ALPHA_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
HYPHENATED_WORD_RE = re.compile(
    r"[^\W_]+(?:-[^\W_]+)+",
    re.UNICODE,
)
SPACE_LETTER_RE = re.compile(r" ([^\W_])", re.UNICODE)
LETTER_SPACE_RE = re.compile(r"([^\W_]) (?=[^\W_])", re.UNICODE)
PUNCT_SPACE_RE = re.compile(r"([,\.…？！:]) ")
LETTER_COLON_RE = re.compile(r"([^\W_]):", re.UNICODE)
SINGLE_PUNCTUATION = "'.,…()"
PAIR_PUNCTUATION = "'.,"
PUNCT_PAIRS = ("！？", "？！", " -")


def is_pair_tail(ch: str) -> bool:
    return ch.islower() or ch.isdigit() or ch in PAIR_PUNCTUATION


def word_pairs(w: str):
    """Every usable adjacent pair in a word.

    Codec.encode chooses the globally cheapest tiling. Supplying only one
    greedy tiling prevents it from shifting pair boundaries to avoid an
    interior single glyph or to combine the preceding space with the first
    letter. A capital is still allowed only at the start of a word; all-caps
    words stay native full-width singles.
    """
    if len(w) == 2 and w.isupper():
        yield w
        return
    for i in range(len(w) - 1):
        a, b = w[i], w[i + 1]
        ok = is_pair_tail(b) and (
            is_pair_tail(a) or (a.isupper() and i == 0)
        )
        if ok:
            yield w[i : i + 2]


def hyphen_boundary_pairs(word: str, both: bool = False):
    """Yield the boundary pairs needed for a gap-free hyphenated word.

    A pair font puts two half-width characters in one native cell. Without a
    pair crossing each hyphen, the standalone narrow hyphen or adjacent letter
    leaves half a cell blank and makes ``Наконец-то`` look like
    ``Наконец -то``. Choose one boundary pair per hyphen according to the
    current segment parity, so the whole word tiles tightly without spending
    two scarce glyph slots on both ``letter-`` and ``-letter``.

    That parity is the word's own, which holds only while the word starts on a
    cell boundary. A pair that carries the preceding space (`` Э``) shifts the
    word half a cell and makes the other boundary the right one, so the tiler
    is left choosing between a stranded hyphen and a stranded letter. Where the
    slot pool has room, `both` asks for either boundary and lets the tiler pick
    the one the surrounding line actually needs.
    """
    if both:
        parts = word.split("-")
        for i in range(len(parts) - 1):
            yield parts[i][-1] + "-"
            yield "-" + parts[i + 1][0]
        return
    parts = word.split("-")
    consumed_prefix = 0
    for i in range(len(parts) - 1):
        left = parts[i]
        right = parts[i + 1]
        available = len(left) - consumed_prefix
        if available % 2:
            yield left[-1] + "-"
            consumed_prefix = 0
        else:
            yield "-" + right[0]
            consumed_prefix = 1


def continuity_pairs(texts: list[str], known: set[str],
                     frequency: collections.Counter) -> collections.Counter:
    """Pairs needed to avoid full-cell singleton letters inside words.

    Odd-length words can tile from either edge, including a neighbouring
    space. Choose the alignment requiring the fewest new pair glyphs; normal
    pair frequency breaks ties so the selected additions remain reusable.
    """
    out: collections.Counter = collections.Counter()
    available = set(known)
    for text in texts:
        for match in ALPHA_WORD_RE.finditer(text):
            word = match.group(0)
            if len(word) < 2:
                continue
            valid = set(word_pairs(word))
            options: list[list[str]] = []

            even = [word[i:i + 2] for i in range(0, len(word) - 1, 2)]
            if all(pair in valid for pair in even):
                if len(word) % 2 == 0:
                    options.append(even)
                elif match.end() < len(text) and text[match.end()] == " ":
                    options.append([*even, word[-1] + " "])

            if len(word) % 2:
                odd = [word[i:i + 2] for i in range(1, len(word) - 1, 2)]
                if (
                    match.start() > 0
                    and text[match.start() - 1] == " "
                    and all(pair in valid for pair in odd)
                ):
                    options.append([" " + word[0], *odd])

            # A boundary singleton is still preferable to an interior one when
            # punctuation prevents a space pair.
            if not options:
                options.append(even)

            def score(option: list[str]) -> tuple[int, int]:
                missing = {pair for pair in option if pair not in available}
                return len(missing), -sum(frequency[pair] for pair in option)

            chosen = min(options, key=score)
            for pair in chosen:
                if pair not in available:
                    out[pair] += 1
                    available.add(pair)
    return out


def needed_units(script_texts: list[str], menu_texts: list[str] | None = None,
                 extra_singles: str = "", forced_pairs: list[str] | None = None,
                 existing_units: set[str] | None = None,
                 both_hyphen_boundaries: bool = False):
    """Return singles and prioritized pair groups needed by target text.

    `script_texts` are dialogue-shaped: room to breathe, lowercase pairs
    chosen by frequency. `menu_texts` are labels that must fit a fixed number
    of cells, so they get the full pairing rules and absolute priority.

    Menu labels must fit fixed slot counts, so they get the full pairing
    rules (capital-initial, digits, punctuation) and absolute priority.
    Spacing pairs are optional encodings that improve readability and save
    tokens: leading/trailing space pairs keep half-width word edges from
    visually doubling the inter-word gap, punctuation-space pairs render
    punctuation plus a narrow trailing gap, and letter-colon pairs avoid a
    visible gap after narrow word tails like "Earth:".
    Script dialogs have room: lowercase pairs only, prioritized by frequency,
    assigned while the sacrificial pool lasts.

    `both_hyphen_boundaries` asks for both pairs around every hyphen instead
    of the one the word's own parity wants; see `hyphen_boundary_pairs`. Worth
    the extra slots only where the pool is not the binding constraint.
    """
    # Callers pass text with control tags already replaced by a space: no
    # codec forms a pair across a tag, and joining the halves would demand
    # phantom pairs like ",п" out of a line break. Which sequences are tags
    # is the one part of this that differs per engine.
    menu_texts = list(menu_texts or [])

    singles: set[str] = set()
    menu_pairs: collections.Counter = collections.Counter()
    spacing_pairs: collections.Counter = collections.Counter()
    script_pairs: collections.Counter = collections.Counter()
    singles.update(extra_singles)
    for pair in forced_pairs or []:
        menu_pairs[pair] += 1_000_000

    def collect_spacing(text: str, target: collections.Counter,
                        hyphen_target: collections.Counter | None = None) -> None:
        target.update(
            " " + match.group(1)
            for match in SPACE_LETTER_RE.finditer(text)
        )
        target.update(
            match.group(1) + " "
            for match in LETTER_SPACE_RE.finditer(text)
        )
        target.update(
            match.group(1) + " "
            for match in PUNCT_SPACE_RE.finditer(text)
        )
        target.update(
            match.group(1) + ":"
            for match in LETTER_COLON_RE.finditer(text)
        )
        for match in HYPHENATED_WORD_RE.finditer(text):
            (hyphen_target if hyphen_target is not None
             else target).update(hyphen_boundary_pairs(
                 match.group(0), both_hyphen_boundaries))

    # A missing boundary pair leaves a mid-word hole ("Наконец ‑то"), the
    # same artifact continuity pairs exist to prevent, so dialog hyphen
    # pairs rank with continuity instead of the cosmetic spacing tier.
    script_hyphens: collections.Counter = collections.Counter()
    for t in script_texts:
        for ch in t:
            if ch.islower() or ch in SINGLE_PUNCTUATION:
                singles.add(ch)
        collect_spacing(t, spacing_pairs, script_hyphens)
    for t in menu_texts:
        for ch in t:
            if ch.islower() or ch in SINGLE_PUNCTUATION:
                singles.add(ch)
        collect_spacing(t, menu_pairs)
    for p in PUNCT_PAIRS:
        menu_pairs[p] += 1_000_000
    for t in menu_texts:
        for m in WORD_RE.finditer(t):
            for p in word_pairs(m.group(0)):
                menu_pairs[p] += 1
    for t in script_texts:
        for m in WORD_RE.finditer(t):
            for p in word_pairs(m.group(0)):
                script_pairs[p] += 1
    continuity = continuity_pairs(
        script_texts,
        set(existing_units or ()) | set(menu_pairs),
        script_pairs,
    )
    # An all-caps dialog word with no pairs renders uniformly (every
    # letter fullwidth), which reads fine; holes appear only when it is
    # partially paired. So dialog caps-caps pairs are not demanded at
    # all, freeing their slots for lowercase word pairs. Menu labels
    # keep theirs: menu widths depend on packing.
    for p in [p for p in continuity if len(p) == 2 and p[0].isupper() and p[1].isupper()]:
        del continuity[p]
    # Boost so even a once-used boundary outranks rare in-word pairs: a
    # split like "кое ‑как" reads worse than one thin letter elsewhere.
    for p, c in script_hyphens.items():
        continuity[p] += c + 4
    return singles, menu_pairs, spacing_pairs, continuity, script_pairs




# Punctuation that must never start a line: it has to stay with the word it
# follows. A break is never opened before a run made only of these (e.g. a word
# and its "？", "！", "?!", "…" must wrap together, not apart). "・" is excluded:
# it is the choice/list bullet and is meant to start a line.
PUNCT_RUN_CHARS = set("？！?!…‥。、，．：；:;,.")


def is_punct_run(s: str) -> bool:
    return bool(s) and all(ch in PUNCT_RUN_CHARS for ch in s)


def wrap_cells(text: str, width: int, measure, line_break: str) -> str:
    """Greedy word wrap by rendered width, in cells. Words are never split.

    `measure` returns the cell width of a candidate line, which is the only
    engine-specific part: what a cell costs depends on which pairs the plane
    carries.
    """
    lines: list[str] = []
    cur = ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if measure(cand) <= width:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return line_break.join(lines)



# Tiling a line into the one- and two-character units a plane can draw. Moved
# here from Langrisser V's codec unchanged: `has_unit` says which units exist
# and `cost` what one is worth to the caller — a token there, a byte or two
# here — and the rest, including the look of a lone capital or a stranded
# lowercase, is the same problem in both engines.

def caps_run_len(text: str, i: int) -> int:
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





def visual_penalty(text: str, i: int, width: int,
                   compact_interword_spaces: bool = False) -> int:
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
    if ch == " " and compact_interword_spaces:
        prev = text[i - 1] if i else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        # A space of its own is a whole empty cell between two words, which
        # is the widest hole a line can have; the pairs that carry a space
        # next to a letter exist precisely to avoid it. Worth more than a
        # stranded hyphen, so a tie is broken towards spending the hyphen.
        return 3 if prev.isalpha() and nxt.isalpha() else 0
    return 0


def tile_text(text: str, has_unit, cost=None, base_pos: int = 0,
              compact_interword_spaces: bool = False) -> list[str]:
    n = len(text)
    if cost is None:
        def cost(piece):
            return 1
    # dp[i] = (cost, visual_penalty, piece_list)
    dp: list[tuple[int, int, list[str]] | None] = [None] * (n + 1)
    dp[n] = (0, 0, [])
    for i in range(n - 1, -1, -1):
        best: tuple[int, int, list[str]] | None = None
        for width in (2, 1):
            piece = text[i : i + width]
            if len(piece) != width:
                continue
            # An all-caps word of three or more letters renders as
            # uniform fullwidth singles; pairing part of it (e.g. a
            # menu pair like НА inside ВНИМАНИЕ) would make it lumpy.
            # Two-letter caps runs (АТ, DF, ДА...) still pack as pairs.
            if (width == 2 and piece.isalpha() and piece.isupper()
                    and caps_run_len(text, i) >= 3):
                continue
            tok = piece if has_unit(piece) else None
            tail = dp[i + width]
            if tok is None or tail is None:
                continue
            cand = (
                cost(piece) + tail[0],
                visual_penalty(text, i, width, compact_interword_spaces)
                + tail[1],
                [tok] + tail[2],
            )
            if best is None or cand[:2] < best[:2]:
                best = cand
        if best is not None:
            dp[i] = best
    if dp[0] is None:
        for i, ch in enumerate(text):
            if not has_unit(ch) and not has_unit(text[i : i + 2]):
                raise ValueError(
                    f"cannot encode character {ch!r} at position {base_pos + i}"
                )
        raise ValueError(f"cannot encode text segment at position {base_pos}")
    return dp[0][2]
