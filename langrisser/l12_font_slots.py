#!/usr/bin/env python3
"""Give the target language's characters slots in the Langrisser I & II plane.

The plane is one file, `FONT.DAT`, shared byte-for-byte by both games, so the
assignment is one table for the whole disc rather than one per game.

Two limits decide what is available. The codec reaches slot 1510 and no
further — five banks of 255 above a base of 236 — so the 25 tiles above that
exist in the file but no script string can name them. And a slot may only be
taken once nothing still in Japanese needs it: an untranslated record keeps its
original bytes, so a sacrificed kanji does not vanish from the screen, it draws
a Cyrillic letter in the middle of a Japanese sentence.

Slots are handed out in the order they are cheapest to lose: tiles the Japanese
map never named, then tiles no remaining Japanese string references, and then
the kanji the remaining Japanese uses least. That last group is not free — a
line still in Japanese keeps its original bytes, so a sacrificed kanji draws a
Cyrillic letter in the middle of it — but those lines are going to be
translated, and a rare kanji costs one wrong glyph in one line while the tiles
buy the pairs that decide whether any chunk fits at all.

Existing assignments are kept exactly where they are, because moving a glyph
rewrites every record that used it.
"""
from __future__ import annotations

import argparse
import collections
import csv
import re
from pathlib import Path

from langrisser.font_units import needed_units
from langrisser.build_font import pick_fonts, render_tile
from langrisser.game import add_game_args, game_from_args, load_game
from langrisser.l12_scen import (BANK_BASE, BANK_FIRST, BANK_WIDTH, CONTROLS,
                                 GLYPH_FIRST, MAX_SLOT, read_chunks)
from langrisser.l12_sceninsert import read_pack
from langrisser.project import (add_language_args, language_from_args,
                                load_language)
from langrisser.release import add_release_args, release_from_args
from langrisser.scen import load_charmap_csv

TAG_RE = re.compile(r"<[^>]*>")


def string_slots(raw: bytes):
    """The plane slots one encoded string draws, controls skipped."""
    i = 0
    while i < len(raw):
        v = raw[i]
        if v in CONTROLS:
            i += 2 if CONTROLS[v][1] else 1
        elif v >= BANK_FIRST and i + 1 < len(raw):
            yield BANK_BASE + (v - BANK_FIRST) * BANK_WIDTH + raw[i + 1]
            i += 2
        elif v >= GLYPH_FIRST:
            yield v - GLYPH_FIRST
            i += 1
        else:
            i += 1


def survey(games: list[str], roots: dict[str, Path]):
    """Slots still needed by Japanese, slots ever used, and single-char demand.

    Which pairs to cut is a separate question and a harder one; it is
    `assign_font_slots.needed_units`' job, not repeated here.
    """
    still: collections.Counter = collections.Counter()
    ever: set[int] = set()
    wanted: collections.Counter = collections.Counter()
    for game in games:
        root = roots[game]
        scen = Path("work", game, "extracted", "SCEN.DAT")
        # The shared tables live in one file but sit in every chunk, so they
        # count as translated everywhere: their kanji are free and their
        # letters need slots like any other.
        shared_file = root / "shared.txt"
        shared = read_pack(shared_file) if shared_file.exists() else {}
        for text in shared.values():
            wanted.update(TAG_RE.sub("", text))
        for chunk in read_chunks(scen.read_bytes()):
            pack = root / f"chunk_{chunk.index:03d}.txt"
            records = dict(read_pack(pack)) if pack.exists() else {}
            records.update(shared)
            for key, text in records.items():
                if key not in shared:
                    wanted.update(TAG_RE.sub("", text))
            for pi, part in enumerate(chunk.parts):
                for si, raw in enumerate(part):
                    if not raw:
                        continue
                    slots = list(string_slots(raw))
                    ever.update(slots)
                    if (pi, si) not in records:
                        still.update(slots)
    return still, ever, wanted


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_language_args(ap)
    add_game_args(ap, default="l1")
    add_release_args(ap, default="l1-2-ps1-jp")
    ap.add_argument("--font-map", default=None)
    ap.add_argument("--assignments", default=None,
                    help="Canonical assignment baseline (default: the pack's).")
    ap.add_argument("--out", default=None,
                    help="Generated assignment table (default: update the "
                         "canonical baseline).")
    ap.add_argument(
        "--translation-root", action="append", default=[], metavar="GAME=DIR",
        help="Override one release game's SCEN directory. Repeat for multiple "
             "games; unspecified games use their language packs.",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    game = game_from_args(args)
    release = release_from_args(args, platform="ps1")
    lang = language_from_args(args)
    font = load_charmap_csv(Path(args.font_map) if args.font_map else game.font_map)
    assignments = (Path(args.assignments) if args.assignments
                   else lang.font_assignments)
    out = Path(args.out) if args.out else assignments

    games = sorted(release.games) if hasattr(release, "games") else [game.code]
    roots = {
        code: load_language(
            args.lang, load_game(code, args.game_root).lang_root, code
        ).script_dir
        for code in games
    }
    for value in args.translation_root:
        code, sep, root = value.partition("=")
        if not sep or not code or not root:
            raise SystemExit(
                f"--translation-root expects GAME=DIR, got {value!r}")
        if code not in roots:
            raise SystemExit(
                f"--translation-root names {code!r}, not a game in "
                f"{release.code}: {', '.join(games)}")
        roots[code] = Path(root)
    for code, root in roots.items():
        if not root.is_dir():
            raise SystemExit(f"{code} translation root is not a directory: {root}")
    still, ever, wanted = survey(games, roots)

    # A pair has to fit one cell at a 6px pitch. Fullwidth characters do not,
    # and the text does carry a few — the scenario number is drawn in them —
    # so ask the renderer rather than guessing which those are.
    fonts = pick_fonts(str(lang.font) if lang.font else "", lang.font_size)
    def packable(pair: str) -> bool:
        try:
            render_tile(pair, fonts, [])
        except ValueError:
            return False
        return True

    have = {ch for ch in font.values() if ch}
    kept: dict[int, tuple[str, str]] = {}
    taken: dict[str, int] = {}
    if assignments.exists():
        for row in csv.DictReader(assignments.open(encoding="utf-8")):
            slot, ch = int(row["index_dec"]), row["char"]
            # Single characters keep their slot; the text cannot be written
            # without them and their identity never changes. Pairs are chosen
            # again from scratch every build, because which ones are worth a
            # slot depends on the whole corpus and that grows with every
            # scenario. Nothing is pinned to them: every record the pack
            # carries is re-encoded here anyway.
            if len(ch) == 1 and slot <= MAX_SLOT and packable(ch):
                kept[slot] = (ch, row.get("replaced_char") or "")
                taken[ch] = slot

    need = sorted(ch for ch in wanted if ch not in have and ch not in taken)

    # A plane character the target text itself uses is not spare, however
    # rare it is in the Japanese that is left: the corner brackets around a
    # scenario title are drawn by the same glyphs the Japanese card used.
    borrowed = {slot for slot, ch in font.items() if ch and ch in wanted}
    free = [s for s in range(MAX_SLOT + 1)
            if s not in kept and s not in borrowed]
    # Cheapest first: never named by the Japanese map, then never referenced by
    # any string, then freed by our own translation, and last the kanji the
    # Japanese that is left still draws — rarest of those first.
    free.sort(key=lambda s: (bool(font.get(s)), s in ever, still[s], s))

    if len(need) > len(free):
        raise SystemExit(
            f"{len(need)} characters need slots but only {len(free)} are free; "
            f"translate more chunks to release kanji, or cut characters")

    for ch, slot in zip(need, free):
        kept[slot] = (ch, font.get(slot) or "")

    # Whatever is left goes to the two-letter tiles. These are not optional
    # decoration: one letter per cell makes a Russian line twice the width of
    # the Japanese it replaces, and doubles what it costs in bytes.
    #
    # Which pairs, and in what order, is `assign_font_slots`' analysis, used
    # here as it stands. Frequency alone picks the wrong set: it misses the
    # pairs that span a space, and it ignores that an odd-length word has two
    # ways to tile — so a word can end up with one full-width letter stranded
    # in the middle of narrow ones. Continuity pairs are chosen to prevent
    # exactly that and therefore come first.
    spare = free[len(need):]
    texts: list[str] = []
    for code in games:
        for pack in sorted(roots[code].glob("*.txt")):
            texts.extend(TAG_RE.sub(" ", t) for t in read_pack(pack).values())
    _, menu_pairs, spacing_pairs, continuity, script_pairs = needed_units(
        texts, forced_pairs=list(lang.forced_pairs or []),
        existing_units=set(taken),
        # This plane leaves hundreds of slots free after every wanted pair is
        # placed, so there is nothing to gain by guessing one boundary per
        # hyphen and living with the gap when the guess is wrong.
        both_hyphen_boundaries=True,
    )
    ranked: list[str] = []
    for group in (menu_pairs, continuity, spacing_pairs, script_pairs):
        ranked.extend(p for p, _ in group.most_common())
    seen: set[str] = set()
    fullwidth = set(lang.fullwidth_units)
    want_pairs = [p for p in ranked
                  if len(p) == 2 and p not in taken and p not in seen
                  and p not in fullwidth
                  and not seen.add(p) and packable(p)]
    added_pairs = 0
    for pair, slot in zip(want_pairs, spare):
        kept[slot] = (pair, font.get(slot) or "")
        added_pairs += 1

    rows = [{"index_dec": slot, "char": ch, "replaced_char": was}
            for slot, (ch, was) in sorted(kept.items())]
    print(f"{game.code}+{','.join(games)}/{lang.code}: {len(rows)} slots assigned "
          f"({len(need)} new singles, {added_pairs} pairs of {len(want_pairs)} wanted), "
          f"{len(free) - len(need) - added_pairs} still free "
          f"(ceiling {MAX_SLOT}, {len(still)} slots still Japanese, "
          f"{sum(1 for s in kept if still[s])} of them taken)")
    if args.dry_run:
        for row in rows if args.dry_run and False else []:
            print(f"   {row['index_dec']:5} {row['char']} <- {row['replaced_char'] or '(blank)'}")
        return

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, ["index_dec", "char", "replaced_char"])
        w.writeheader()
        w.writerows(rows)
    print(f"   -> {out}")


if __name__ == "__main__":
    main()
