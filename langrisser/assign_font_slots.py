#!/usr/bin/env python3
"""Maintain target-language glyph slot assignments.

Collects every single char and compact pair needed by the current target texts
(script dump translations + menu map values), keeps all existing assignments
stable, and assigns new needs to the cheapest sacrificial kanji slots:
confirmed kanji, unused in chunk 0, unused in menu/UI string runs, rarest in
the script (those lines will be translated eventually).
"""
import argparse
import collections
import csv
import json
import re
import struct
from pathlib import Path

from langrisser.font_units import (PUNCT_PAIRS, SINGLE_PUNCTUATION, continuity_pairs,
                                   hyphen_boundary_pairs, is_pair_tail, needed_units,
                                   word_pairs)
from langrisser.offsetgroups import is_system_key
from langrisser.project import COMMON_FONT_MAP, add_language_args, language_from_args
from langrisser.scen import (FORCE_PAGE_BREAK, consumes_argument, find_text_block,
                        raw_glyph_slots, read_chunk_spans, words_from_bytes)

TAG_RE = re.compile(r"<\$[0-9A-Fa-f]{4}>")
def map_target_texts(mp: Path) -> list[str]:
    """Target strings from a translation map or unified string list."""
    data = json.loads(mp.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.values())
    return [e["text"] for e in data
            if (e.get("text") or "").strip() and e["text"] != "{BLANK}"]


def map_jp_keys(mp: Path, source_by_id: dict[str, dict]) -> set[str]:
    """JP source strings from a translation map (used to mark UI glyph slots)."""
    data = json.loads(mp.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        overlay_ids = {key for key in data if is_system_key(key)}
        unknown = overlay_ids - set(source_by_id)
        if unknown:
            raise SystemExit(
                f"{mp}: SYSTEM overlay ids require a current source dump; "
                f"unknown ids: {sorted(unknown)[:5]}"
            )
        if data and set(data).issubset(source_by_id):
            return {
                source_by_id[entry_id]["jp"]
                for entry_id, text in data.items()
                if text and source_by_id[entry_id].get("jp")
            }
        return set(data)
    return {e["jp"] for e in data if e.get("jp")}


def script_record_texts(translation_root: Path,
                        extra_script_dirs: list[Path] | None = None) -> list[str]:
    """Target-language record bodies from the translated chunk files."""
    files = sorted(translation_root.glob("*/chunk_*.txt"))
    for extra in extra_script_dirs or []:
        files.extend(sorted(extra.glob("chunk_*.txt")))
    out: list[str] = []
    for fp in files:
        for raw in fp.read_text(encoding="utf-8").splitlines():
            if "\t" in raw and not raw.startswith("#"):
                out.append(raw.split("\t", 1)[1].replace(FORCE_PAGE_BREAK, "<$FFFD>"))
    return out


def decode_run_key(words: list[int], tok2char: dict[int, str]) -> str:
    out = []
    for w in words:
        if w in tok2char:
            out.append(tok2char[w])
        elif w >= 0xFF00:
            out.append("{%04X}" % w)
        else:
            out.append("[%04X]" % w)
    return "".join(out)


def sacrificial_pool(groups_report: Path, scen: Path, scen2: Path,
                     other_files: list[Path], translated_keys: set[str],
                     translated_chunks: set[int], max_slot: int,
                     excluded_slots: set[int] = frozenset(),
                     usage_scan: dict | None = None) -> list[int]:
    tok2char: dict[int, str] = {}
    rows = list(csv.DictReader(open(groups_report, encoding="utf-8")))
    for r in rows:
        if r["index_dec"].isdigit() and r["char"]:
            tok2char[int(r["index_dec"])] = r["char"]

    usage: collections.Counter = collections.Counter()
    jp_visible: set[int] = set()
    if usage_scan is not None:
        usage.update({int(k): v for k, v in usage_scan["usage"].items()})
        jp_visible.update(usage_scan["jp_visible"])
    for f in (() if usage_scan is not None else (scen, scen2)):
        data = f.read_bytes()
        for ci, (s, e) in enumerate(read_chunk_spans(data)):
            chunk = data[s:e]
            block = find_text_block(chunk)
            for ri in range(1, block.record_count + 1):
                a, b = block.record_span(ri)
                prev = None
                for w in words_from_bytes(chunk[a:b]):
                    if w < 0xE000 and not (prev is not None and consumes_argument(prev)):
                        usage[w] += 1
                        # A chunk without a translation file still renders
                        # its JP glyphs, so its tiles cannot be sacrificed.
                        # Translated chunks are replaced on insert and stop
                        # showing JP, mirroring the translated_keys rule for
                        # UI runs below.
                        if ci not in translated_chunks:
                            jp_visible.add(w)
                    prev = w

    ui_used: collections.Counter = collections.Counter()
    if usage_scan is not None:
        ui_used.update({int(k): v for k, v in usage_scan["ui_used"].items()})
    for f in (() if usage_scan is not None else other_files):
        data = f.read_bytes()
        if f.name == "SYSTEM.BIN":
            data = data[0x8100:]  # skip font plane and offset tables
        ws = list(struct.unpack(f"<{len(data)//2}H", data[: len(data) & ~1]))
        run: list[int] = []
        for w in ws:
            if w == 0xFFFF:
                pr = [x for x in run if x < 0xE000]
                if len(pr) >= 3 and sum(1 for x in pr if x in tok2char) / len(pr) >= 0.8:
                    # Runs we already translate stop displaying their JP
                    # glyphs once patched, so they do not block slots.
                    if decode_run_key(run + [0xFFFF], tok2char) not in translated_keys:
                        ui_used.update(pr)
                run = []
            elif w < 0xE000 or 0xF000 <= w < 0xFFFF:
                run.append(w)
            else:
                run = []

    tier1: list[tuple[int, int]] = []
    tier2: list[tuple[int, int]] = []  # used in untranslated UI text: a
    # sacrifice costs one wrong glyph there until that text is translated
    for r in rows:
        if not r["index_dec"].isdigit() or r["group"] != "confirmed":
            continue
        idx = int(r["index_dec"])
        ch = r["char"]
        if len(ch) != 1 or not (0x4E00 <= ord(ch) <= 0x9FFF):
            continue
        if idx in jp_visible or idx > max_slot or idx in excluded_slots:
            continue
        if idx in ui_used:
            tier2.append((ui_used[idx] + usage.get(idx, 0), idx))
        else:
            tier1.append((usage.get(idx, 0), idx))
    tier1.sort()
    tier2.sort()
    return [idx for _, idx in tier1] + [idx for _, idx in tier2]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--groups-report", default=None)
    ap.add_argument("--assignments", default=None)
    ap.add_argument("--out-assignments", default=None,
                    help="Write the completed assignment set here instead of "
                         "modifying the language pack CSV.")
    ap.add_argument("--translation-root", default=None,
                    help="Override the language pack's translated-text root.")
    ap.add_argument("--menu-map", action="append",
                    default=None,
                    help="Translation maps (repeatable); defaults to menu+names maps.")
    ap.add_argument("--system-source",
                    default="work/l5/systemdump/system_strings.json",
                    help="Generated SYSTEM source dump used to resolve overlay ids.")
    ap.add_argument("--scen", default="work/l5/extracted/SCEN.DAT")
    ap.add_argument("--scen2", default="work/l5/extracted/SCEN2.DAT")
    ap.add_argument("--max-slot", type=int, default=1820,
                    help="Highest usable glyph slot on the target platform "
                         "(PS1 plane: 1820; Saturn: 1819, see manifest).")
    ap.add_argument("--extra-script-dir", action="append", default=[],
                    help="Additional script record dirs (platform SCEN records).")
    ap.add_argument("--extra-menu-strings", action="append", default=[],
                    help="Additional menu-string JSON maps (platform SYSTEM "
                         "overlay); scanned for needed chars only.")
    ap.add_argument("--usage-scan", default=None,
                    help="Platform usage-scan JSON (saturn_usage_scan): "
                         "replaces the PS1-side usage/jp_visible/ui_used "
                         "facts for the sacrificial pool.")
    ap.add_argument("--exclude-slots", default=None,
                    help="Native-glyph plan JSON (saturn_fix_native_glyphs plan): "
                         "its saturn_slot values stay native and are never "
                         "assigned or kept as Cyrillic tiles.")
    args = ap.parse_args()

    excluded: set[int] = set()
    forced_singles = ""
    if args.exclude_slots:
        plan = json.loads(Path(args.exclude_slots).read_text(encoding="utf-8"))
        excluded = {p["saturn_slot"] for p in plan["remap"]}
        # Characters the platform font lacks entirely are rendered as
        # project-font tiles like any other assigned single.
        forced_singles = "".join(plan.get("assign", []))

    scan = (json.loads(Path(args.usage_scan).read_text(encoding="utf-8"))
            if args.usage_scan else None)
    if scan is not None:
        # A slot this build still draws is off limits to the pool *and* to an
        # inherited assignment: the tracked CSV is shared by every release, so
        # a slot free on the one it was written from can hold a live glyph
        # here. Banning it moves the char to a slot this build really spares.
        excluded |= set(scan["jp_visible"])

    lang = language_from_args(args)
    groups_report = Path(args.groups_report) if args.groups_report else COMMON_FONT_MAP
    assignments = Path(args.assignments) if args.assignments else lang.font_assignments
    out_assignments = (Path(args.out_assignments)
                       if args.out_assignments else assignments)
    translation_root = (Path(args.translation_root)
                        if args.translation_root else lang.dump_root)
    maps = [Path(p) for p in (args.menu_map or [str(lang.system_strings)])]
    extra_script_dirs = [Path(p) for p in args.extra_script_dir]
    extra_menu_maps = [Path(p) for p in args.extra_menu_strings]
    excluded |= raw_glyph_slots(
        script_record_texts(translation_root, extra_script_dirs)
        + [text for mp in maps + extra_menu_maps if mp.exists()
           for text in map_target_texts(mp)])

    existing: dict[str, int] = {}
    rows = []
    apath = assignments
    if apath.exists():
        rows = list(csv.DictReader(open(apath, encoding="utf-8")))
        # An inherited assignment beyond the platform's font plane, or on a
        # slot the platform needs for a native glyph, must move: its char
        # re-enters the needs below and gets a new slot from the pool.
        def banned(r: dict) -> bool:
            slot = int(r["index_dec"])
            return slot > args.max_slot or slot in excluded
        over = [r for r in rows if banned(r)]
        if over:
            rows = [r for r in rows if not banned(r)]
            print(f"reassigning {len(over)} banned slots: "
                  + " ".join(f"{r['index_dec']}={r['char']!r}" for r in over))
        for r in rows:
            existing[r["char"]] = int(r["index_dec"])

    menu_texts: list[str] = []
    for mp in list(maps) + list(extra_menu_maps or []):
        if mp.exists():
            menu_texts.extend(map_target_texts(mp))
    singles, menu_pairs, spacing_pairs, continuity, script_pairs = needed_units(
        [TAG_RE.sub(" ", body) for body in
         script_record_texts(translation_root, extra_script_dirs)],
        menu_texts, lang.single_chars + forced_singles,
        lang.forced_pairs, set(existing),
    )
    must = [c for c in sorted(singles) if c not in existing]
    must += [p for p, _ in menu_pairs.most_common() if p not in existing]
    # Word integrity outranks spacing cosmetics: a missing word pair is a
    # visible hole inside a word, while a missing spacing pair only makes
    # a letter+space half a cell wider. So dialog word pairs (continuity,
    # then remaining word pairs) take slots before letter+space pairs.
    optional = [p for p, _ in continuity.most_common()
                if p not in existing and p not in must]
    optional += [p for p, _ in script_pairs.most_common()
                 if p not in existing and p not in must and p not in optional]
    optional += [p for p, _ in spacing_pairs.most_common()
                 if p not in existing and p not in must and p not in optional]

    # A pair tile only exists if both halves fit the five-pixel half-cell
    # (or have a compact form). Probe the actual renderer so glyphs it
    # would reject (e.g. wide capitals in all-caps machine lines) fall
    # back to fullwidth singles instead of failing the font build.
    from langrisser import build_font as bf
    fonts = bf.pick_fonts(str(lang.font) if lang.font else "", lang.font_size)
    pair_ok: dict[str, bool] = {}

    def renderable(unit: str) -> bool:
        if len(unit) != 2:
            return True
        if unit not in pair_ok:
            try:
                bf.render_tile(unit, fonts, caps_fonts=[])
                pair_ok[unit] = True
            except ValueError:
                pair_ok[unit] = False
        return pair_ok[unit]

    unrenderable = [p for p in must + optional if not renderable(p)]
    if unrenderable:
        print(f"unrenderable pairs fall back to singles: "
              f"{len(unrenderable)} ({' '.join(unrenderable[:12])}...)")
        must = [p for p in must if renderable(p)]
        optional = [p for p in optional if renderable(p)]

    taken = set(existing.values())
    # BTLDAT/MRCUSW/SLPS are mostly code/data whose pseudo-runs would
    # inflate the "used in UI" set; real UI strings live in SYSTEM/ALLUS*.
    translated_keys = set()
    source_by_id: dict[str, dict] = {}
    source_path = Path(args.system_source)
    if source_path.exists():
        source_by_id = {
            entry["id"]: entry
            for entry in json.loads(source_path.read_text(encoding="utf-8"))
        }
    for mp in maps:
        if mp.exists():
            translated_keys |= map_jp_keys(mp, source_by_id)
    translated_chunks = {
        int(m.group(1))
        for fp in translation_root.glob("*/chunk_*.txt")
        if (m := re.match(r"chunk_(\d+)$", fp.stem))
    }
    pool = [i for i in sacrificial_pool(
        groups_report, Path(args.scen), Path(args.scen2),
        [Path(p) for p in ("work/l5/extracted/SYSTEM.BIN", "work/l5/extracted/ALLUSB.BIN",
                           "work/l5/extracted/ALLUSW.BIN")],
        translated_keys, translated_chunks, args.max_slot, excluded, scan,
    ) if i not in taken]
    if len(pool) < len(must):
        # A platform slot cap (e.g. Saturn's 1819) can displace inherited
        # must-units after the pool is exhausted. Menu labels must fit fixed
        # widths, so a must-unit outranks a held optional pair: evict the
        # least valuable optional assignments (dead units first, then rare
        # spacing pairs, then rare dialog pairs) and reuse their slots — the
        # glyphs there are already sacrificed, so eviction costs no new tile.
        def evict_rank(unit: str) -> tuple[int, int] | None:
            if len(unit) != 2 or unit in menu_pairs or "-" in unit:
                return None  # singles, menu pairs and hyphen pairs stay
            if unit in script_pairs or unit in continuity:
                return (2, script_pairs[unit] + continuity[unit])
            if unit in spacing_pairs:
                return (1, spacing_pairs[unit])
            return (0, 0)  # not needed by any current text
        candidates = sorted(
            (r for r in rows if evict_rank(r["char"]) is not None),
            key=lambda r: evict_rank(r["char"]),
        )
        short = len(must) - len(pool)
        if len(candidates) < short:
            raise SystemExit(
                f"not enough sacrificial slots: need {len(must)}, have "
                f"{len(pool)}, and only {len(candidates)} evictable pairs")
        evicted = candidates[:short]
        evicted_set = {id(r) for r in evicted}
        rows = [r for r in rows if id(r) not in evicted_set]
        for r in evicted:
            existing.pop(r["char"], None)
            pool.insert(0, int(r["index_dec"]))
        print(f"evicted {short} optional pairs for platform must-units: "
              + " ".join(f"{r['index_dec']}={r['char']!r}" for r in evicted))
    dropped = max(0, len(must) + len(optional) - len(pool))
    need = (must + optional)[: len(pool)]
    if dropped:
        print(f"pool limit: {dropped} least-frequent dialog pairs fall back to single letters")
    def write_rows() -> None:
        out_assignments.parent.mkdir(parents=True, exist_ok=True)
        with out_assignments.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["index_dec", "char", "replaced_char"],
                lineterminator="\n",
            )
            w.writeheader()
            w.writerows(rows)

    if not need:
        if out_assignments != assignments:
            write_rows()
        print("assignments up to date")
        return

    src = {int(r["index_dec"]): r["replaced_char"] for r in rows} if rows else {}
    gmap = {}
    for r in csv.DictReader(open(groups_report, encoding="utf-8")):
        if r["index_dec"].isdigit():
            gmap[int(r["index_dec"])] = r["char"]

    for unit in need:
        slot = pool.pop(0)
        rows.append({"index_dec": str(slot), "char": unit,
                     "replaced_char": gmap.get(slot, "")})

    write_rows()
    print(f"added {len(need)} assignments (total {len(rows)})")


if __name__ == "__main__":
    main()
