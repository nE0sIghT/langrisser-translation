#!/usr/bin/env python3
"""Create a clean target-language scaffold under the game's lang/<code>.

The scaffold copies source structure but clears target text, font assignments,
name-entry characters and other translated values. It does not copy SCEN chunks
unless --copy-script is given. Generated JP dumps belong under work/ and are
never created here.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

from lang5_game import add_game_args, game_from_args
from lang5_project import ROOT, load_language
from lang5_release import releases_for


SCALAR_FILES = [
    "font_assignments",
    "system_strings",
    "system_layout",
    "title_credits",
    "names",
    "glossary",
    "name_entry_grid",
    "manual_record_overrides",
    "review_status",
    "poem",
    "poem_source",
    "virash_monologue",
]


def rel_to_root(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def write_scaffold(key: str, src: Path, dst: Path) -> None:
    if key == "poem_source":
        shutil.copyfile(src, dst)
        return
    if key == "font_assignments":
        with src.open(encoding="utf-8", newline="") as fh:
            fields = csv.DictReader(fh).fieldnames
        if not fields:
            raise SystemExit(f"missing CSV header: {src}")
        dst.write_text(",".join(fields) + "\n", encoding="utf-8")
        return
    if key == "review_status":
        dst.write_text(
            "chunk,record,target_done,reference_checked,note\n",
            encoding="utf-8",
        )
        return
    if key == "system_strings":
        dst.write_text("{}\n", encoding="utf-8")
        return
    if key == "system_layout":
        source = json.loads(src.read_text(encoding="utf-8"))
        value = {
            "default_max_grow": source.get("default_max_grow", 4),
            "overrides": {},
        }
        dst.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return
    if key == "title_credits":
        value = {
            "_comment": (
                "One to three target-language title-credit lines. "
                "Available placeholders: {version}, {commit}."
            ),
            "lines": [],
        }
        dst.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    if key in ("names", "glossary"):
        with src.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            fields = reader.fieldnames
            rows = list(reader)
        if not fields or "text" not in fields:
            raise SystemExit(f"missing target text column: {src}")
        for row in rows:
            row["text"] = ""
            if key == "names" and "alt" in row:
                row["alt"] = ""
        with dst.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        return
    if key == "manual_record_overrides":
        values = json.loads(src.read_text(encoding="utf-8"))
        dst.write_text(
            json.dumps({name: "" for name in values}, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    if key == "name_entry_grid":
        value = {
            "_comment": [
                "Target-language name-entry grid. Supply exactly 19 runs of 5 characters",
                "before building this language pack; an empty list marks an unfinished scaffold.",
            ],
            "runs": [],
        }
        dst.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return
    if key == "poem":
        comments = []
        for line in src.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                comments.append(line)
            elif line.strip():
                break
        comments.append(
            "# TODO: add the target-language poem while preserving four logical blocks."
        )
        dst.write_text("\n".join(comments) + "\n", encoding="utf-8")
        return
    if key == "virash_monologue":
        value = json.loads(src.read_text(encoding="utf-8"))
        value["_comment"] = (
            "Subtitle source for the scenario-25 Virash monologue. JP and timings "
            "are source material recovered from the non-text cutscene; text is "
            "the target translation."
        )
        for cue in value["cues"]:
            cue["text"] = ""
        dst.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    shutil.copyfile(src, dst)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("lang", help="New language code, e.g. ru.")
    ap.add_argument("--label", default=None, help="Human-readable language label.")
    ap.add_argument("--from-lang", default="en", help="Source pack to copy scaffold data from.")
    add_game_args(ap)
    ap.add_argument("--lang-root", default=None,
                    help="Override the pack root (default: the game manifest's).")
    ap.add_argument("--copy-script", action="store_true",
                    help="Also copy translated SCEN chunks from the source language.")
    ap.add_argument("--force", action="store_true", help="Overwrite an existing scaffold file.")
    args = ap.parse_args()

    game = game_from_args(args)
    lang_root = Path(args.lang_root) if args.lang_root else game.lang_root
    if not lang_root.is_absolute():
        lang_root = Path.cwd() / lang_root
    src = load_language(args.from_lang, lang_root)
    dst_root = lang_root / args.lang
    dst_root.mkdir(parents=True, exist_ok=True)
    (dst_root / "SCEN").mkdir(exist_ok=True)
    (dst_root / "SCEN" / ".gitkeep").touch()
    # One override directory per release of this game: a delta is against a
    # build, so a game shipped twice on one console gets one slot each.
    for release in releases_for(game.code):
        dst_release = dst_root / "releases" / release.code
        (dst_release / "SCEN").mkdir(parents=True, exist_ok=True)
        (dst_release / "SCEN" / ".gitkeep").touch()
        system_overlay = dst_release / "system_strings.json"
        if not system_overlay.exists() or args.force:
            system_overlay.write_text("{}\n", encoding="utf-8")

    manifest = src.manifest_copy()
    manifest["lang"] = args.lang
    manifest["label"] = args.label or args.lang.upper()
    manifest["patch_suffix"] = args.lang
    manifest["patch_description"] = f"Langrisser V {args.lang.upper()} script+font"

    for key in SCALAR_FILES:
        value = manifest.get(key)
        if not value:
            continue
        src_path = (src.root / value).resolve()
        dst_path = dst_root / Path(value).name
        if dst_path.exists() and not args.force:
            print(f"skip existing {dst_path}")
            continue
        write_scaffold(key, src_path, dst_path)
        manifest[key] = dst_path.name
        print(f"copied {src_path} -> {dst_path}")

    manifest["script_dir"] = "SCEN"
    if args.copy_script:
        for fp in sorted(src.script_dir.glob("chunk_*.txt")):
            dst = dst_root / "SCEN" / fp.name
            if dst.exists() and not args.force:
                continue
            shutil.copyfile(fp, dst)
        print(f"copied script chunks from {src.script_dir}")

    out_manifest = dst_root / "manifest.json"
    if out_manifest.exists() and not args.force:
        raise SystemExit(f"{out_manifest} exists; use --force to overwrite")
    out_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out_manifest}")


if __name__ == "__main__":
    main()
