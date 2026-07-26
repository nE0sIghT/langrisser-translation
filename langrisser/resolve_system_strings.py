#!/usr/bin/env python3
"""Resolve a language pack's complete SYSTEM.BIN translation overlay.

Explicit stable-id overrides win. Omitted entries whose Japanese source exactly
matches a canonical names.csv or glossary.csv term inherit that translation.
The generated result belongs under work/; only explicit context-dependent text
is stored in system_strings.json.
"""
import argparse
import csv
import json
import re
from pathlib import Path

from langrisser.project import add_language_args, language_from_args

JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def load_object(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(
        not isinstance(k, str) or not isinstance(v, str)
        for k, v in data.items()
    ):
        raise SystemExit(f"{path}: expected a string-to-string JSON object")
    return data


def load_terms(paths: list[Path]) -> dict[str, str]:
    terms: dict[str, str] = {}
    for path in paths:
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                jp = row.get("jp", "")
                text = row.get("text", "")
                if not jp or not text:
                    continue
                previous = terms.get(jp)
                if previous is not None and previous != text:
                    raise SystemExit(
                        f"conflicting canonical translation for {jp!r}: "
                        f"{previous!r} vs {text!r}"
                    )
                terms[jp] = text
    return terms


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--system-source",
                    default="work/l5/systemdump/system_strings.json")
    ap.add_argument("--strings", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--require-complete", action="store_true")
    args = ap.parse_args()

    lang = language_from_args(args)
    source_path = Path(args.system_source)
    strings_path = Path(args.strings) if args.strings else lang.system_strings
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(source, list):
        raise SystemExit(f"{source_path}: expected a JSON array")
    source_by_id = {entry["id"]: entry for entry in source}
    if len(source_by_id) != len(source):
        raise SystemExit(f"{source_path}: duplicate stable ids")

    explicit = load_object(strings_path)
    unknown = sorted(set(explicit) - set(source_by_id))
    if unknown:
        raise SystemExit(
            f"{strings_path}: unknown SYSTEM ids, first: {unknown[:5]}"
        )
    terms = load_terms([lang.glossary, lang.names])

    resolved: dict[str, str] = {}
    missing: list[tuple[str, str]] = []
    residual: list[tuple[str, str]] = []
    inherited = 0
    for entry in source:
        entry_id = entry["id"]
        jp = entry.get("jp", "")
        if entry_id in explicit:
            text = explicit[entry_id]
        elif jp in terms:
            text = terms[jp]
            inherited += 1
        else:
            if args.require_complete and JP_RE.search(jp):
                missing.append((entry_id, jp))
            continue
        resolved[entry_id] = text
        if args.require_complete and text != "{BLANK}" and JP_RE.search(text):
            residual.append((entry_id, text))

    if missing or residual:
        for entry_id, text in missing[:30]:
            print(f"MISSING {entry_id}: {text}")
        for entry_id, text in residual[:30]:
            print(f"RESIDUAL {entry_id}: {text}")
        raise SystemExit(
            f"SYSTEM translation incomplete: missing={len(missing)} "
            f"residual={len(residual)}"
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(resolved, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"resolved {len(resolved)} SYSTEM strings "
        f"({len(explicit)} explicit, {inherited} canonical) -> {out}"
    )


if __name__ == "__main__":
    main()
