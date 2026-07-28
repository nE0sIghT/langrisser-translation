#!/usr/bin/env python3
"""Audit the Saturn<->PS1 SCEN correspondence; PS1 is a reference only.

For every non-empty block, the monotone stable-signature alignment proves
which Saturn entries may take the PS1 record's translation. Every entry with
no proven counterpart carries Saturn-edited content and must be resolved with
a platform record (translated from the Saturn original) — until then it is
explicitly preserved in `scen_mapping.json` with `"pending_review": true`.

Outputs:

- a review report with the Saturn original decoded through the release's own
  kanji map (derived from the glyph bitmaps by `derive_font_map --bank-only`),
  the closest PS1 record and its current ru/en translations — everything a
  translator needs to author the platform record;
- with `--write-mapping`, the `entries` half of `scen_mapping.json` - the
  exceptional records (platform records carried over, the rest
  preserve/pending). The `ranges` half is reconciliation's and is left alone.
"""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path

from langrisser.offsetgroups import build_codemap
from langrisser.saturn_apply import (load_mapping, monotone_alignment,
                                normalizer_for, ps1_chunk_records)
from langrisser.game import add_game_args, game_from_args
from langrisser.sceninsert import parse_dump_file
from langrisser.release import add_release_args, release_from_args
from langrisser.saturn_scen import local_index_entries, parse_catalog


def decoder(charmap: dict[int, str]):
    def dec(words: list[int]) -> str:
        parts: list[str] = []
        for w in words:
            if w == 0xFFFC:
                parts.append("\\n")
            elif w == 0xFFFD:
                parts.append("<PAGE>")
            elif w >= 0xFB00:
                parts.append(f"{{{w:04X}}}")
            else:
                parts.append(charmap.get(w, "?"))
        return "".join(parts)
    return dec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_game_args(ap)
    ap.add_argument("--scen", default="work/l5/build/saturn/SCEN.DAT")
    ap.add_argument("--ps1-scen", default="work/l5/extracted/SCEN.DAT")
    add_release_args(ap, "l5-saturn-jp")
    ap.add_argument("--mapping", default=None,
                    help="SCEN mapping JSON (default: the release manifest's)")
    ap.add_argument("--lang-root", default=None,
                    help="Pack root (default: the game manifest's lang_root).")
    ap.add_argument("--langs", nargs="*", default=["ru", "en"])
    ap.add_argument("--out-report", default="work/l5/build/saturn/scen_platform_review.md")
    ap.add_argument("--write-mapping", action="store_true",
                    help="Add the exceptional entries this run found to the "
                         "chunk specs; nothing already recorded is removed.")
    ap.add_argument("--auto-resolve", action="store_true",
                    help="Author platform records automatically where the Saturn "
                         "original provably equals some PS1 record (duplicates / "
                         "reordered lines), copying that record's ru/en text.")
    args = ap.parse_args()

    release = release_from_args(args)
    mapping_path = Path(args.mapping) if args.mapping else release.scen_mapping

    lang_root = Path(args.lang_root) if args.lang_root else game_from_args(args).lang_root
    scen_dirs = {lang: lang_root / lang / "SCEN" for lang in args.langs}
    sat = Path(args.scen).read_bytes()
    ps1 = Path(args.ps1_scen).read_bytes()
    mapping = load_mapping(mapping_path)
    empty = {int(x) for x in mapping.get("empty_chunks", [])}
    chunk_specs = {int(k): v for k, v in (mapping.get("chunks") or {}).items()}

    norm = normalizer_for(release)
    dec_sat = decoder(build_codemap(norm.ps1_charmap, norm.kanji_map,
                                    norm.bank_start))
    dec_ps1 = decoder(norm.ps1_charmap)

    report: list[str] = [
        "# Saturn-edited SCEN records pending platform translations",
        "",
        "Each entry's JP original differs from every PS1 record "
        "(stable-signature proof). Saturn kanji are decoded through the "
        "release's kanji map; `?` marks a slot the reference plane does not "
        "hold.",
        "",
    ]
    new_chunks: dict[str, dict] = {}
    pending_total = 0
    platform_total = 0
    for ci, (s, u) in enumerate(parse_catalog(sat)):
        if ci in empty:
            continue
        entries = local_index_entries(sat, s, u)
        if entries is None:
            continue
        ps1_tokens = ps1_chunk_records(ps1, ci)
        _, unmatched = monotone_alignment(entries, ps1_tokens, norm)
        old_spec = chunk_specs.get(ci, {})
        platform_entries = {
            int(item["saturn"]): item
            for item in old_spec.get("entries", [])
            if "platform" in item
        }
        keep: list[dict] = []
        chunk_report: list[str] = []
        common = {}
        for lang, sdir in scen_dirs.items():
            fp = sdir / f"chunk_{ci:03d}.txt"
            common[lang] = parse_dump_file(fp) if fp.exists() else {}
        ru, en = common.get("ru", {}), common.get("en", {})
        ps_sigs = [norm.signature(t) for t in ps1_tokens]

        def strip_tail(tokens: list[int]) -> tuple[int, ...]:
            t = tuple(tokens)
            while t and t[-1] == 0xFFFF:
                t = t[:-1]
            return t

        def provably_equal_record(si: int) -> int | None:
            """A PS1 record (1-based) whose JP provably equals the Saturn entry.

            Either exact token equality (kana/ASCII lines, duplicates moved
            around), or equality of the decoded strings where every Saturn
            kanji resolved through the derived map — same text, only the
            reordered kanji token ids differ.
            """
            mine = strip_tail(entries[si])
            for r, pt in enumerate(ps1_tokens):
                if strip_tail(pt) == mine:
                    return r + 1
            sat_txt = dec_sat(list(mine))
            if "?" in sat_txt:
                return None
            for r, pt in enumerate(ps1_tokens):
                if dec_ps1(list(strip_tail(pt))) == sat_txt:
                    return r + 1
            return None

        auto_writes: dict[int, int] = {}
        for si in unmatched:
            if si in platform_entries:
                keep.append(platform_entries[si])
                platform_total += 1
                continue
            if args.auto_resolve:
                r = provably_equal_record(si)
                if r is not None and r in ru and r in en:
                    auto_writes[si] = r
                    keep.append({"saturn": si, "platform": si,
                                 "auto_from_ps1": r})
                    platform_total += 1
                    continue
            keep.append({"saturn": si, "preserve": True, "pending_review": True})
            pending_total += 1
            sig = norm.signature(entries[si])
            best = max(
                range(len(ps1_tokens)),
                key=lambda k: difflib.SequenceMatcher(
                    a=sig, b=ps_sigs[k], autojunk=False).ratio(),
                default=None,
            )
            chunk_report.append(f"### chunk {ci:03d} entry {si}")
            chunk_report.append(f"- SAT JP: `{dec_sat(entries[si])}`")
            if best is not None:
                chunk_report.append(f"- closest PS1 record {best + 1}:")
                chunk_report.append(f"  - JP: `{dec_ps1(ps1_tokens[best])}`")
                if best + 1 in ru:
                    chunk_report.append(f"  - RU: `{ru[best + 1]}`")
                if best + 1 in en:
                    chunk_report.append(f"  - EN: `{en[best + 1]}`")
            chunk_report.append("")
        for lang, records_map in common.items():
            if not auto_writes:
                break
            pfile = (lang_root / lang / "releases" / release.code / "SCEN"
                     / f"chunk_{ci:03d}.txt")
            existing = parse_dump_file(pfile) if pfile.exists() else {}
            additions = [
                f"{si}\t{records_map[r]}"
                for si, r in sorted(auto_writes.items())
                if si not in existing
            ]
            if additions:
                header = ("# Auto-resolved Saturn records: the JP original provably "
                          "equals the named PS1 record (duplicate/reordered line).\n"
                          if not pfile.exists() else "")
                pfile.parent.mkdir(parents=True, exist_ok=True)
                with pfile.open("a", encoding="utf-8") as f:
                    f.write(header + "\n".join(additions) + "\n")
        if keep:
            new_chunks[str(ci)] = {"entries": keep}
        if chunk_report:
            report.append(f"## chunk {ci:03d} — {len(chunk_report) // 6 + 1} records")
            report.extend(chunk_report)

    Path(args.out_report).write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"audit: {platform_total} platform records, {pending_total} pending "
          f"preserve entries; kanji map {len(norm.kanji_map)} tokens")
    print(f"report -> {args.out_report}")
    if args.write_mapping:
        # This tool adds the entries it found; it never removes one. The
        # recorded ranges are reconciliation's, and an entry already written
        # is a decision - a release-specific wording stays release-specific
        # even where the two originals do turn out to match.
        merged = {k: dict(v) for k, v in (mapping.get("chunks") or {}).items()}
        for chunk, spec in new_chunks.items():
            existing = merged.setdefault(chunk, {}).get("entries", [])
            known = {int(item["saturn"]) for item in existing}
            added = [item for item in spec["entries"]
                     if int(item["saturn"]) not in known]
            if existing or added:
                merged[chunk]["entries"] = existing + added
        mapping["chunks"] = dict(sorted(
            ((k, v) for k, v in merged.items() if v), key=lambda kv: int(kv[0])))
        mapping_path.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"mapping entries updated -> {mapping_path}")


if __name__ == "__main__":
    main()
