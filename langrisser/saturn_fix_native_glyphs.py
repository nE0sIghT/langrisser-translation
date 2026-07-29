#!/usr/bin/env python3
"""Point natively-encoded characters at the real Saturn glyph slots.

The translation encodes some characters through *native* tokens taken from the
PS1 slot->char map (`data/font_map`): `○`, the standalone hyphen, arrows. The
Saturn `SYSTEM.DAT` glyph plane is reordered in that region, so the PS1 token
often points at a different Saturn glyph — the "sigma/lambda hieroglyph" bug.
The Saturn font already contains those glyphs at other slots (`○` at 0x5F4,
`-` at 0x380, ...), and the `.tbl` must agree with the actual Saturn font: no
glyph is ever copied from PS1.

Character needs are computed from the *effective* Saturn texts — the common
translation with the platform mapping applied, platform overlay/records
included — so characters that exist only in PS1-replaced lines (the PS1 pad
symbols `▢`/`△`) drop out entirely and their stale mappings are removed from
the `.tbl` (any overlooked usage then fails the strict encode validators
instead of rendering a wrong glyph).

Two subcommands around the font build:

Where each character sits here is read from the recorded maps — the game's font
map plus this release's `kanji_map.csv` — so the plan needs no PS1 disc. What
those maps cannot name, a glyph with no character, is recorded per release as
`unnamed_glyph_slots`; `saturn_reconcile glyphs` is the step that compares the
two planes and checks both.

Two subcommands around the font build:

- `plan` (before slot assignment): classify every needed native-token
  character this release keeps at a different slot:
  `remap` — this release's map names the slot holding it (the slot is then
  excluded from the sacrificial pool via
  `langrisser.assign_font_slots --exclude-slots`); `assign` — the Saturn font has
  no such glyph (e.g. `×`: the originals spell 2割/3付4), so the character is
  force-assigned and rendered by the project font like any other tile;
  `drop` — not needed by any effective Saturn text.
- `apply` (after the font build): rewrite the `.tbl`: remapped characters
  move to their Saturn slots, dropped characters lose their stale mappings
  (assigned characters are already handled by the font build itself).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from langrisser.build_font import NATIVE_VISUAL_OVERRIDES
from langrisser.game import add_game_args, game_from_args
from langrisser.offsetgroups import build_codemap, load_font_map_csv
from langrisser.project import COMMON_FONT_MAP, add_language_args, language_from_args
from langrisser.release import add_release_args, release_from_args
from langrisser.sceninsert import parse_dump_file

TAG_RE = re.compile(r"<\$[0-9A-Fa-f]{4}>")


def chars_of(texts: list[str], grid: Path | None) -> set[str]:
    chars: set[str] = set()
    for text in texts:
        if text and text != "{BLANK}":
            chars.update(TAG_RE.sub("", text))
    if grid is not None and grid.exists():
        for run in json.loads(grid.read_text(encoding="utf-8"))["runs"]:
            chars.update(run)
    chars.discard(" ")
    chars.discard("\t")
    return chars


def is_symbol_class(ch: str) -> bool:
    """Symbols/latin/punctuation — the class the translation encodes natively.

    Kana and kanji stay out of the plan entirely: a differing bitmap there is
    usually the *same* character redrawn (the first differing slot 0x00CA is
    ロ on both consoles), the translation never encodes them, and the JP
    tooling (name-entry anchors, dumps) needs their mappings intact.
    """
    code = ord(ch)
    return code < 0x3000 or 0xFF00 <= code <= 0xFF65


def ps1_native_tokens(groups_report: Path) -> dict[str, int]:
    """Lowest PS1-map slot per symbol-class character (the encoder's choice)."""
    best: dict[str, int] = {}
    for row in csv.DictReader(open(groups_report, encoding="utf-8")):
        if not row["index_dec"].isdigit():
            continue
        tok = int(row["index_dec"])
        ch = row["char"]
        if len(ch) != 1 or tok == 0 or tok in NATIVE_VISUAL_OVERRIDES:
            continue
        if not is_symbol_class(ch):
            continue
        if ch not in best or tok < best[ch]:
            best[ch] = tok
    return best


def release_slots(game, release) -> dict[str, int]:
    """`character -> the slot holding it on this build`, from the recorded maps.

    Where a glyph sits is a property of the release, and both halves are
    already written down: the game's font map for the shared region, the
    release's `kanji_map.csv` for the bank it reordered. Reading it here is
    what keeps a build off another release's disc - only reconciliation
    compares planes (`saturn_reconcile glyphs`).
    """
    here = build_codemap(load_font_map_csv(game.font_map),
                         load_font_map_csv(release.kanji_map),
                         game.kanji_bank_start)
    slots: dict[str, int] = {}
    for slot in sorted(here):
        slots.setdefault(here[slot], slot)
    return slots


def cmd_plan(args: argparse.Namespace, lang, game, release,
             override_dir: Path) -> None:
    # The build copies are already normalized by saturn_apply_text_overrides
    # (platform records inlined, shadowed SYSTEM ids removed), so the
    # effective character set is simply everything those copies plus the
    # platform records and overlay contain.
    texts: list[str] = []
    for fp in sorted(Path(args.translation_root).glob("*/chunk_*.txt")):
        texts.extend(parse_dump_file(fp).values())
    for fp in sorted((override_dir / "SCEN").glob("chunk_*.txt")):
        texts.extend(parse_dump_file(fp).values())
    texts.extend(json.loads(Path(args.strings).read_text(encoding="utf-8")).values())
    overlay_path = override_dir / "system_strings.json"
    if overlay_path.exists():
        texts.extend(json.loads(overlay_path.read_text(encoding="utf-8")).values())
    effective = chars_of(texts, lang.name_entry_grid)

    natives = ps1_native_tokens(Path(args.groups_report))
    here = release_slots(game, release)
    remap: list[dict] = []
    assign: list[str] = []
    drop: list[str] = []
    for ch, tok in sorted(natives.items()):
        slot = here.get(ch)
        if slot == tok:
            continue          # same slot on both: the pack's token already fits
        if ch not in effective:
            drop.append(ch)
            continue
        if slot is not None:
            remap.append({"char": ch, "ps1_token": tok, "saturn_slot": slot})
        else:
            assign.append(ch)
    plan = {"remap": remap, "assign": assign, "drop": drop}
    out = Path(args.plan)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"native glyph plan: {len(remap)} remapped to existing Saturn slots, "
          f"{len(assign)} assigned as font tiles ({''.join(assign)!r}), "
          f"{len(drop)} dropped (PS1-only) -> {out}")


def cmd_apply(args: argparse.Namespace) -> None:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    assigned = {
        int(r["index_dec"])
        for r in csv.DictReader(open(args.assignments, encoding="utf-8"))
    }
    clash = [p for p in plan["remap"] if p["saturn_slot"] in assigned]
    if clash:
        raise SystemExit(
            "planned Saturn glyph slots were assigned to Cyrillic tiles "
            f"(pass the plan to langrisser.assign_font_slots --exclude-slots): {clash}")

    tbl_path = Path(args.tbl)
    lines = tbl_path.read_text(encoding="utf-8").splitlines()
    remap = {p["ps1_token"]: (p["saturn_slot"], p["char"]) for p in plan["remap"]}
    stale_keys = {f"{slot:04X}" for slot, _ in remap.values()}
    dropped = set(plan["drop"])
    out_lines: list[str] = []
    for line in lines:
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            if len(key) == 4:
                try:
                    tok = int(key, 16)
                except ValueError:
                    tok = -1
                if tok in remap:
                    continue          # stale PS1 mapping for the moved char
                if key.upper() in stale_keys:
                    continue          # PS1-map char sitting on the reused slot
                if value in dropped:
                    continue          # PS1-only char: encoding it must fail loudly
        out_lines.append(line)
    for old, (new, ch) in sorted(remap.items()):
        out_lines.append(f"{new:04X}={ch}")
    tbl_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"native glyphs: remapped {len(remap)} to existing Saturn slots, "
          f"dropped {len(dropped)} PS1-only chars from the tbl")
    if remap:
        print("  " + " ".join(f"{old:#06x}->{new:#06x}={ch!r}"
                              for old, (new, ch) in sorted(remap.items())))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    add_game_args(ap)
    add_release_args(ap, "l5-saturn-jp")
    ap.add_argument("command", choices=["plan", "apply"])
    ap.add_argument("--plan", required=True, help="Plan JSON path.")
    ap.add_argument("--groups-report", default=str(COMMON_FONT_MAP))
    ap.add_argument("--translation-root", default=None)
    ap.add_argument("--strings", default=None,
                    help="plan: resolved common SYSTEM strings JSON.")
    ap.add_argument("--tbl", default=None, help="apply: .tbl rewritten in place.")
    ap.add_argument("--assignments", default=None,
                    help="apply: build font slot assignments CSV.")
    args = ap.parse_args()
    lang = language_from_args(args)
    override_dir = lang.overrides(args.release)
    if args.command == "plan":
        if not (args.translation_root and args.strings):
            raise SystemExit("plan requires --translation-root and --strings")
        cmd_plan(args, lang, game_from_args(args), release_from_args(args),
                 override_dir)
    else:
        if not (args.tbl and args.assignments):
            raise SystemExit("apply requires --tbl and --assignments")
        cmd_apply(args)


if __name__ == "__main__":
    main()
