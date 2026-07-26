#!/usr/bin/env python3
"""Apply a target-language translation onto the Saturn SCEN.DAT text pool.

This reuses the PS1 translation content and codec unchanged: because the target
alphabet occupies the same font slots on both consoles, a record's encoded token
stream is identical; only the byte order (Saturn on-disc big-endian) and the
container (the field_3c local index table) differ. Each Saturn text entry is
rebuilt in place at fixed size via `saturn_scen.splice_local_index_table`.

Saturn block `c`'s entry `e` corresponds to PS1 chunk `c` record `e+1` only
when platform data proves that relationship. Identity/prefix and unique
stable-token alignments are automatic; interspersed platform differences must be
listed in the release's `scen_mapping.json` or supplied as
language-specific target text under `<pack>/platforms/saturn/SCEN/`.
Read-only against the disc: it reads an extracted SCEN.DAT and writes a new one.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from langrisser.platform import add_platform_args, platform_from_args
from langrisser.release import add_release_args, release_from_args
from langrisser.project import add_language_args, language_from_args
from langrisser.scen import Codec, find_text_block, load_charmap_tbl, read_chunk_spans, words_from_bytes
from langrisser.sceninsert import parse_dump_file
from langrisser.offsetgroups import load_font_map_csv
from langrisser.saturn_scen import (local_index_entries, local_index_layout, parse_catalog,
                         repack_scen)


WILDCARD = object()   # an unresolved rare Saturn kanji: matches nothing exactly


class Normalizer:
    """Normalize both consoles' token streams to comparable text.

    Tokens are font-slot numbers, so raw ids are incomparable across consoles
    in the reordered kanji bank (>= 0x185). Normalization turns each token
    into its character: the shared low range and controls directly, Saturn
    kanji through the derived platform `kanji_map`. A rare Saturn kanji the
    map could not resolve becomes a `WILDCARD` that never *proves* equality.
    """

    def __init__(self, ps1_charmap: dict[int, str], kanji_map: dict[int, str]):
        self.ps1_charmap = ps1_charmap
        self.kanji_map = kanji_map

    def _sym(self, token: int, saturn: bool) -> object:
        if token >= 0xE000:
            return f"<{token:04X}>"
        if token < 0x0185 or not saturn:
            return self.ps1_charmap.get(token, f"[{token:04X}]")
        char = self.kanji_map.get(token)
        return char if char is not None else WILDCARD

    def normalize(self, tokens: list[int], *, saturn: bool) -> tuple:
        toks = list(tokens)
        while toks and toks[-1] == 0xFFFF:
            toks.pop()
        return tuple(self._sym(t, saturn) for t in toks)


def monotone_alignment(
    entries: list[list[int]], ps1_tokens: list[list[int]],
    norm: Normalizer | None,
) -> tuple[dict[int, int], list[int]]:
    """Prove Saturn->PS1 record correspondence; PS1 is a reference only.

    Pass 1 matches order-preservingly on the *normalized text* of both
    originals (kanji included, through the platform map) — full equality.
    Pass 2 catches records containing unresolved rare kanji: they match on
    the stable signature (kana/ASCII/controls), verified position-by-position
    on the normalized text with wildcards allowed only at the unresolved
    spots. Returns `{saturn_index: ps1_record (1-based)}` plus the Saturn
    entries with no proven counterpart — Saturn-edited content that must come
    from langrisser.platform records (or stay preserved until translated).
    """
    import difflib

    def blocks(a: list, b: list) -> dict[int, int]:
        matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
        out: dict[int, int] = {}
        for a0, b0, n in matcher.get_matching_blocks():
            for k in range(n):
                out[a0 + k] = b0 + k
        return out

    mapping: dict[int, int] = {}
    if norm is not None:
        sat_norm = [norm.normalize(e, saturn=True) for e in entries]
        ps_norm = [norm.normalize(t, saturn=False) for t in ps1_tokens]
        exact = blocks([hash(x) for x in sat_norm],
                       [hash(x) for x in ps_norm])
        mapping = {a: b + 1 for a, b in exact.items()}
        rest_sat = [i for i in range(len(entries)) if i not in mapping]
        rest_ps = [j for j in range(len(ps1_tokens))
                   if j + 1 not in set(mapping.values())]
        if rest_sat and rest_ps:
            sig = blocks([hash(stable_signature(entries[i])) for i in rest_sat],
                         [hash(stable_signature(ps1_tokens[j])) for j in rest_ps])
            for ai, bj in sig.items():
                si, pj = rest_sat[ai], rest_ps[bj]
                a, b = sat_norm[si], ps_norm[pj]
                if len(a) == len(b) and all(
                    x is WILDCARD or x == y for x, y in zip(a, b)
                ):
                    mapping[si] = pj + 1
    else:
        sig = blocks([hash(stable_signature(e)) for e in entries],
                     [hash(stable_signature(t)) for t in ps1_tokens])
        mapping = {a: b + 1 for a, b in sig.items()}
    unmatched = [i for i in range(len(entries)) if i not in mapping]
    return mapping, unmatched


def proven_equal(norm: Normalizer | None, sat_tokens: list[int],
                 ps1_rec: list[int]) -> bool:
    """One pair's proof: normalized-text equality, wildcards only at
    unresolved rare kanji; falls back to the stable signature without a map."""
    if norm is None:
        return stable_signature(sat_tokens) == stable_signature(ps1_rec)
    a = norm.normalize(sat_tokens, saturn=True)
    b = norm.normalize(ps1_rec, saturn=False)
    if len(a) != len(b):
        return False
    return all(x is WILDCARD or x == y for x, y in zip(a, b))


def stable_signature(tokens: list[int]) -> tuple[int, ...]:
    """Return the PS1/Saturn-stable part of a JP token stream.

    Kana, ASCII/punctuation and control words are shared between platforms.
    The high kanji bank is reordered on Saturn, so it must not participate in
    automated alignment. Control arguments are kept because speaker ids and
    name macros are alignment-critical.
    """
    out: list[int] = []
    prev: int | None = None
    for token in tokens:
        keep = token < 0x0185 or token >= 0xE000
        if prev == 0xF600 or (prev is not None and 0xFB00 <= prev <= 0xFBFF):
            keep = True
        if keep:
            out.append(token)
        prev = token
    return tuple(out)


def ps1_chunk_records(ps1_scen: bytes, chunk_index: int) -> list[list[int]]:
    start, end = read_chunk_spans(ps1_scen)[chunk_index]
    block = find_text_block(ps1_scen[start:end])
    out: list[list[int]] = []
    for idx in range(1, block.record_count + 1):
        a, b = block.record_span(idx)
        out.append(words_from_bytes(ps1_scen[start + a:start + b]))
    return out


def load_mapping(path: Path | None) -> dict:
    if path is None:
        return {"empty_chunks": [], "chunks": {}}
    if not path.exists():
        raise SystemExit(f"Saturn SCEN mapping not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: SCEN mapping must be an object")
    data.setdefault("empty_chunks", [])
    data.setdefault("chunks", {})
    return data


def release_scen_records(lang_root: Path, release_code: str,
                         chunk_index: int) -> dict[int, str]:
    path = (lang_root / "releases" / release_code / "SCEN"
            / f"chunk_{chunk_index:03d}.txt")
    return parse_dump_file(path) if path.exists() else {}


def expand_record_map(spec: dict, entry_count: int) -> dict[int, object]:
    """Return `{saturn_entry_index: ps1_record_or_platform_record}`.

    Mapping entries use zero-based Saturn entry indices. PS1 records are
    one-based because language-pack chunks use that convention.
    """
    out: dict[int, object] = {}
    for item in spec.get("ranges", []):
        saturn = int(item["saturn"])
        count = int(item["count"])
        if "ps1" in item:
            ps1 = int(item["ps1"])
            for off in range(count):
                out[saturn + off] = ps1 + off
        elif "platform" in item:
            platform = int(item["platform"])
            for off in range(count):
                out[saturn + off] = {"platform": platform + off}
        elif item.get("preserve"):
            for off in range(count):
                out[saturn + off] = {"preserve": True}
        else:
            raise SystemExit(f"SCEN range mapping needs ps1/platform/preserve: {item}")
    for item in spec.get("entries", []):
        saturn = int(item["saturn"])
        if "ps1" in item:
            out[saturn] = int(item["ps1"])
        elif "platform" in item:
            out[saturn] = {"platform": int(item["platform"])}
        elif item.get("preserve"):
            out[saturn] = {"preserve": True}
        else:
            raise SystemExit(f"SCEN entry mapping needs ps1/platform/preserve: {item}")
    bad = [idx for idx in out if idx < 0 or idx >= entry_count]
    if bad:
        raise SystemExit(f"SCEN mapping has out-of-range Saturn entries: {bad[:5]}")
    return out


def apply_scen(data: bytes, lang_scen_dir: Path, codec: Codec,
               ps1_scen: bytes | None = None, *,
               mapping: dict | None = None,
               lang_root: Path | None = None,
               release_code: str = "",
               strict: bool = True,
               no_grow: bool = False,
               norm: Normalizer | None = None) -> tuple[bytes, dict]:
    """Insert the translation, treating PS1 strictly as a *reference*.

    Every Saturn entry takes a PS1 record's translation only when both JP
    originals stable-signature-match (automatic monotone alignment, or an
    explicit `ps1` mapping target — verified the same way). Saturn entries
    with no proven counterpart carry Saturn-edited content: they must be
    covered by a platform record, or explicitly preserved (pending
    translation); anything else is a build error.
    """
    if ps1_scen is None:
        raise SystemExit("apply_scen needs the PS1 SCEN.DAT reference")
    blocks = parse_catalog(data)
    stats = {"blocks": len(blocks), "applied": 0,
             "entries_written": 0, "missing_dump": 0,
             "mapped": 0, "empty_skipped": 0, "skipped_over_budget": 0,
             "release_records": 0, "preserved_pending": 0}
    mapping = mapping or {"empty_chunks": [], "chunks": {}}
    empty_chunks = {int(x) for x in mapping.get("empty_chunks", [])}
    chunk_specs = {int(k): v for k, v in (mapping.get("chunks") or {}).items()}
    fatal: list[str] = []
    block_entries: dict[int, list[list[int]]] = {}
    for chunk_index, (start, used) in enumerate(blocks):
        entries = local_index_entries(data, start, used)
        if entries is None:
            continue
        if chunk_index in empty_chunks:
            stats["empty_skipped"] += 1
            continue
        dump_path = lang_scen_dir / f"chunk_{chunk_index:03d}.txt"
        if dump_path.exists():
            records = parse_dump_file(dump_path)  # {1-based idx: text}
        else:
            # Platform-only chunks (e.g. the party name pools) have no common
            # dump; any remaining ps1-mapped entry then fails loudly below.
            records = {}
            stats["missing_dump"] += 1
        ps1_tokens = ps1_chunk_records(ps1_scen, chunk_index)
        auto, unmatched = monotone_alignment(entries, ps1_tokens, norm)
        targets: dict[int, object] = dict(auto)
        for idx in unmatched:
            targets[idx] = {"unmatched": True}
        spec = chunk_specs.get(chunk_index)
        if spec is not None:
            # Explicit platform/preserve/ps1 entries override the automatic
            # alignment for individual records; ps1 targets are still
            # signature-verified below like the automatic ones.
            targets.update(expand_record_map(spec, len(entries)))
            stats["mapped"] += 1
        release_records = (
            release_scen_records(lang_root, release_code, chunk_index)
            if lang_root is not None and release_code else {}
        )
        new_entries: list[list[int]] = []
        chunk_fatal: list[str] = []
        for saturn_index in range(len(entries)):
            target = targets.get(saturn_index)
            if isinstance(target, int):
                if not (1 <= target <= len(ps1_tokens)) or not proven_equal(
                    norm, entries[saturn_index], ps1_tokens[target - 1]
                ):
                    chunk_fatal.append(
                        f"chunk {chunk_index:03d} entry {saturn_index}: mapped "
                        f"PS1 record {target} does not match the Saturn original "
                        "(needs a platform record)"
                    )
                    new_entries.append(entries[saturn_index])
                    continue
                text = records.get(target)
                if text is None:
                    chunk_fatal.append(
                        f"chunk {chunk_index:03d} entry {saturn_index}: PS1 "
                        f"record {target} not found in the language chunk"
                    )
                    new_entries.append(entries[saturn_index])
                    continue
                new_entries.append(codec.encode(text))
            elif isinstance(target, dict) and "platform" in target:
                platform_index = int(target["platform"])
                text = release_records.get(platform_index)
                if text is None:
                    chunk_fatal.append(
                        f"chunk {chunk_index:03d} entry {saturn_index}: platform "
                        f"record {platform_index} not found"
                    )
                    new_entries.append(entries[saturn_index])
                    continue
                stats["release_records"] += 1
                new_entries.append(codec.encode(text))
            elif isinstance(target, dict) and target.get("preserve"):
                stats["preserved_pending"] += 1
                new_entries.append(entries[saturn_index])
            elif isinstance(target, dict) and target.get("unmatched"):
                if strict:
                    chunk_fatal.append(
                        f"chunk {chunk_index:03d} entry {saturn_index}: Saturn "
                        "original has no matching PS1 record (needs a platform "
                        "record or an explicit preserve)"
                    )
                stats["preserved_pending"] += 1
                new_entries.append(entries[saturn_index])
            else:
                chunk_fatal.append(
                    f"chunk {chunk_index:03d} entry {saturn_index}: mapping does "
                    "not cover this entry"
                )
                new_entries.append(entries[saturn_index])
        if chunk_fatal:
            fatal.extend(chunk_fatal)
            continue
        if no_grow:
            _, total_size, _ = local_index_layout(data, start, used)
            packed = 4 + len(new_entries) * 2 + sum(len(w) for w in new_entries) * 2
            if packed > total_size:
                stats["skipped_over_budget"] += 1
                continue
        block_entries[chunk_index] = new_entries
        stats["applied"] += 1
        stats["entries_written"] += len(new_entries)

    if fatal:
        lines = "\n".join(f"  - {msg}" for msg in fatal[:40])
        more = f"\n  ... +{len(fatal) - 40} more" if len(fatal) > 40 else ""
        raise SystemExit(f"Saturn SCEN mapping incomplete:\n{lines}{more}")

    out = repack_scen(data, block_entries)
    stats["grown_bytes"] = len(out) - len(data)
    return out, stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    add_platform_args(ap, "saturn")
    add_release_args(ap, "l5-saturn-jp")
    ap.add_argument("--scen", default="work/l5/build/saturn/SCEN.DAT")
    ap.add_argument("--out-scen", default="work/l5/build/saturn/SCEN.applied.DAT")
    ap.add_argument("--tbl", default=None,
                    help="charmap .tbl (default: platform build tbl)")
    ap.add_argument("--ps1-scen", default="work/l5/extracted/SCEN.DAT",
                    help="PS1 SCEN.DAT used only for exact stable-token alignment")
    ap.add_argument("--mapping", default=None,
                    help="SCEN mapping JSON (default: the release manifest's)")
    ap.add_argument("--translation-root", default=None,
                    help="Translated-text root override (the build passes its "
                         "rewrapped/normalized copy; default: the language pack).")
    ap.add_argument("--allow-unmapped", action="store_true",
                    help="Diagnostic mode: preserve chunks whose mapping is not proven.")
    ap.add_argument("--no-grow", action="store_true",
                    help="Diagnostic mode: keep over-budget blocks original so no "
                         "block grows or moves (isolates growth-related bugs).")
    args = ap.parse_args()

    lang = language_from_args(args)
    platform = platform_from_args(args)
    release = release_from_args(args, platform=platform.code)
    if args.tbl:
        tbl = Path(args.tbl)
    elif platform.code == "ps1":
        tbl = lang.tbl
    else:
        tbl = Path(f"work/l5/build/{platform.code}/lang5_{lang.suffix}.{platform.code}.tbl")
    mapping_path = Path(args.mapping) if args.mapping else release.scen_mapping
    mapping = load_mapping(mapping_path)
    codec = Codec(load_charmap_tbl(tbl))
    data = Path(args.scen).read_bytes()
    ps1_scen = Path(args.ps1_scen).read_bytes() if args.ps1_scen else None
    from langrisser.project import COMMON_FONT_MAP
    norm = Normalizer(load_font_map_csv(COMMON_FONT_MAP),
                      load_font_map_csv(release.kanji_map))
    scen_dir = (Path(args.translation_root) / lang.script_dir.name
                if args.translation_root else lang.script_dir)
    out, stats = apply_scen(
        data, scen_dir, codec, ps1_scen,
        mapping=mapping,
        lang_root=lang.root,
        release_code=release.code,
        strict=not args.allow_unmapped,
        no_grow=args.no_grow,
        norm=norm,
    )

    out_path = Path(args.out_scen)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out)
    print(
        f"applied {stats['applied']}/{stats['blocks']} blocks, "
        f"{stats['entries_written']} entries; "
        f"mapped={stats['mapped']} "
        f"release-records={stats['release_records']} "
        f"preserved-pending={stats['preserved_pending']} "
        f"empty-skipped={stats['empty_skipped']} "
        f"missing-dump={stats['missing_dump']} "
        f"skipped-over-budget={stats['skipped_over_budget']}; "
        f"file grew {stats['grown_bytes']} bytes -> {out_path}"
    )


if __name__ == "__main__":
    main()
