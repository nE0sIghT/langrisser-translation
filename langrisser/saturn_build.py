#!/usr/bin/env python3
"""Build translated Saturn data files from a universal language pack.

Platform is a build-time choice: the same pack that produces the PS1 PPF drives
this Saturn flow. It reuses the shared stages unchanged:

1. dump this release's own SYSTEM source and resolve the target strings;
2. complete font assignments into a build copy and emit a Saturn `.tbl`;
3. reflow, validate and rewrap a generated translation copy with that table;
4. pack Saturn `SYSTEM.DAT` through platform mappings;
5. insert translated scenario text into Saturn `SCEN.DAT` through platform
   mappings (fixed-size where it fits, growing + re-laying-out blocks where it
   does not).

Outputs the translated `SYSTEM.DAT` and `SCEN.DAT` under the game's own
`work/<game>/build/saturn/`.
With `--remaster-disc`, it also writes a translated mixed-mode BIN/CUE under
the same directory.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from langrisser.build_reference import add_reference_args, check_or_record
from langrisser.game import add_game_args, game_from_args
from langrisser.imgdat import git_short_hash
from langrisser.media import writer_for
from langrisser.platform import add_platform_args, platform_from_args
from langrisser.project import add_language_args, language_from_args
from langrisser.release import add_release_args, release_from_args


def run(*cmd: object) -> None:
    result = subprocess.run([sys.executable, *(str(c) for c in cmd)])
    if result.returncode:
        raise SystemExit(result.returncode)


def has_target_text(path: Path) -> bool:
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped != "---":
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    add_game_args(ap)
    add_platform_args(ap, "saturn")
    ap.add_argument("--saturn-dir", default=None,
                    help="directory holding the extracted Saturn SYSTEM.DAT/SCEN.DAT "
                         "(default: the game's build dir)")
    ap.add_argument("--assignments", default=None,
                    help="font slot assignments CSV (default: the pack's tracked file)")
    ap.add_argument("--translation-root", default=None,
                    help="Override the language pack's translated-text root.")
    ap.add_argument("--ps1-scen", default="work/l5/extracted/SCEN.DAT",
                    help="PS1 SCEN.DAT used as the common script source.")
    ap.add_argument("--ps1-scen2", default="work/l5/extracted/SCEN2.DAT",
                    help="PS1 SCEN2.DAT used for common validation/font-slot safety.")
    ap.add_argument("--ps1-system", default="work/l5/extracted/SYSTEM.BIN",
                    help="PS1 SYSTEM.BIN used as the common SYSTEM source.")
    ap.add_argument("--cue", default=None,
                    help="source Saturn CUE for --remaster-disc "
                         "(default: the release manifest's image)")
    ap.add_argument("--remaster-disc", action="store_true",
                    help="build a translated BIN/CUE in addition to extracted files")
    ap.add_argument("--out-bin", default=None,
                    help="translated Saturn BIN path for --remaster-disc")
    ap.add_argument("--out-cue", default=None,
                    help="translated Saturn CUE path for --remaster-disc")
    ap.add_argument("--patch-version", default="dev",
                    help="Patch version substituted into the title credits.")
    ap.add_argument("--allow-unmapped", action="store_true",
                    help="Diagnostic mode: preserve unmapped Saturn SCEN/SYSTEM data.")
    add_release_args(ap)
    add_reference_args(ap)
    args = ap.parse_args()

    game = game_from_args(args)
    lang = language_from_args(args)
    platform = platform_from_args(args)
    release = release_from_args(args, platform=platform.code)
    if platform.code != "saturn":
        raise SystemExit(f"this builder only supports the saturn platform, got {platform.code}")
    scripts = Path(__file__).resolve().parent
    build_dir = lang.build_root
    build_dir.mkdir(parents=True, exist_ok=True)
    saturn = Path(args.saturn_dir) if args.saturn_dir else build_dir / "saturn"
    system_in = saturn / "SYSTEM.DAT"
    scen_in = saturn / "SCEN.DAT"
    for path in (system_in, scen_in):
        if not path.exists():
            raise SystemExit(
                f"missing {path}; extract it first: "
                f"python3 -m langrisser.saturn_disc extract {path.name} {path}"
            )
    # What still reads the PS1 files: the glyph plan and the shared script
    # validators. Everything the correspondence decides now comes from the
    # release mappings, which reconciliation wrote.
    for path in (Path(args.ps1_scen), Path(args.ps1_scen2), Path(args.ps1_system)):
        if not path.exists():
            raise SystemExit(
                f"missing common PS1 source {path}; extract PS1 base files first"
            )

    translation_root = (Path(args.translation_root)
                        if args.translation_root else lang.dump_root)
    build_translation_root = build_dir / f"translation.{lang.suffix}.saturn"
    if build_translation_root.exists():
        shutil.rmtree(build_translation_root)
    shutil.copytree(translation_root, build_translation_root)
    # Release overrides are read from the copy too, so the build-time
    # normalizations reach them exactly as they reach the common text.
    build_overrides = build_translation_root / "releases" / release.code

    assignments = Path(args.assignments) if args.assignments else lang.font_assignments
    system_font = saturn / f"SYSTEM.DAT.{lang.suffix}.font"
    tbl = saturn / f"lang5_{lang.suffix}.saturn.tbl"

    # The JP source of the UI text, read from this build's own SYSTEM: its
    # word budgets, its leading indent cells, and the pack ids it carries,
    # resolved through the release's recorded SYSTEM mapping.
    system_source = build_dir / f"system_source.{lang.suffix}.saturn.json"
    run("-m", "langrisser.system_dump",
        "--release", release.code,
        "--release-root", args.release_root,
        "--system-bin", system_in,
        "--out", system_source)
    resolved_system_strings = build_dir / f"system_strings.{lang.suffix}.json"
    resolve_args = [
        "-m", "langrisser.resolve_system_strings",
        "--lang", args.lang,
        "--lang-root", lang.root.parent,
        "--system-source", system_source,
        "--out", resolved_system_strings,
    ]
    if lang.system_complete:
        resolve_args.append("--require-complete")
    run(*resolve_args)

    # Normalize the build copies with the platform record overrides so every
    # stage (slot assignment, rewrap, encode validators) sees the text that
    # actually ships — e.g. Saturn pad buttons instead of PS1 pad symbols.
    run("-m", "langrisser.saturn_apply_text_overrides",
        "--lang", args.lang, "--lang-root", lang.root.parent,
        "--release", release.code,
        "--translation-root", build_translation_root,
        "--strings", resolved_system_strings,
        "--saturn-orig", system_in,
        "--ps1-system", args.ps1_system,
        "--scen-mapping", release.scen_mapping,
        "--system-mapping", release.system_mapping)

    # Characters encoded through native PS1-map tokens can hit Saturn slots
    # that hold a different glyph (reordered kanji region). Plan the remap to
    # the Saturn slots that already hold the right glyphs, so the assigner
    # never sacrifices those slots; the .tbl is remapped after the font build.
    glyph_plan = saturn / f"native_glyphs.{lang.suffix}.plan.json"
    run("-m", "langrisser.saturn_fix_native_glyphs",
        "--lang", args.lang, "--lang-root", lang.root.parent,
        "--release", release.code,
        "plan",
        "--plan", glyph_plan,
        "--saturn-orig", system_in,
        "--ps1-system", args.ps1_system,
        "--translation-root", build_translation_root,
        "--strings", resolved_system_strings)

    # Sacrificial-slot facts must come from this release's own data: what the
    # Saturn build leaves untranslated is what a sacrifice would corrupt.
    usage_scan = saturn / f"usage_scan.{lang.suffix}.json"
    run("-m", "langrisser.saturn_usage_scan",
        "--game", args.game, "--game-root", args.game_root,
        "--release", release.code,
        "--scen", scen_in,
        "--mapping", release.scen_mapping,
        "--kanji-map", release.kanji_map,
        "--system-bin", system_in,
        "--strings", resolved_system_strings,
        "--platform-strings", build_overrides / "system_strings.json",
        "--out", usage_scan)

    build_assignments = build_dir / f"font_slot_assignments.{lang.suffix}.saturn.csv"
    run("-m", "langrisser.assign_font_slots",
        "--lang", args.lang,
        "--lang-root", lang.root.parent,
        "--groups-report", game.font_map,
        "--assignments", assignments,
        "--out-assignments", build_assignments,
        "--translation-root", build_translation_root,
        "--menu-map", resolved_system_strings,
        "--system-source", system_source,
        "--scen", args.ps1_scen,
        "--scen2", args.ps1_scen2,
        "--max-slot", str(release.max_font_slot),
        "--exclude-slots", glyph_plan,
        "--extra-script-dir", build_overrides / "SCEN",
        "--extra-menu-strings", build_overrides / "system_strings.json",
        "--usage-scan", usage_scan)

    font_cmd = [
        "-m", "langrisser.build_font",
        "--lang", args.lang, "--lang-root", lang.root.parent,
        "--groups-report", game.font_map,
        "--assignments", build_assignments,
        "--system-bin", system_in,
        "--out-system-bin", system_font,
        "--out-tbl", tbl,
        "--font-size", str(lang.font_size),
        "--max-slot", str(release.max_font_slot),
    ]
    if lang.font:
        font_cmd.extend(["--font", lang.font])
    if lang.caps_font:
        font_cmd.extend(["--caps-font", lang.caps_font,
                         "--caps-font-size", str(lang.caps_font_size)])
    run(*font_cmd)
    # Rewrite the .tbl onto the planned Saturn slots before anything encodes
    # with it: remapped chars move to real Saturn glyphs, PS1-only chars are
    # dropped so any overlooked usage fails the strict validators.
    run("-m", "langrisser.saturn_fix_native_glyphs",
        "--lang", args.lang, "--lang-root", lang.root.parent,
        "apply",
        "--plan", glyph_plan,
        "--tbl", tbl,
        "--assignments", build_assignments)

    reflowed_system_strings = build_dir / f"system_strings.{lang.suffix}.saturn.reflowed.json"
    run("-m", "langrisser.reflow_system_cards",
        "--strings", resolved_system_strings,
        "--out", reflowed_system_strings,
        "--tbl", tbl,
        "--system-source", system_source)
    run("-m", "langrisser.validate_system_ui",
        "--lang", args.lang,
        "--lang-root", lang.root.parent,
        "--tbl", tbl,
        "--strings", reflowed_system_strings,
        "--release-strings", build_overrides / "system_strings.json",
        "--system-source", system_source)
    run("-m", "langrisser.rewrap",
        "--lang", args.lang,
        "--lang-root", lang.root.parent,
        "--translation-root", build_translation_root,
        "--tbl", tbl,
        "--scen", args.ps1_scen)
    run("-m", "langrisser.validate_translation",
        "--lang", args.lang,
        "--lang-root", lang.root.parent,
        "--translation-root", build_translation_root,
        "--tbl", tbl,
        "--scen", args.ps1_scen,
        "--scen2", args.ps1_scen2)

    system_out = saturn / f"SYSTEM.{lang.suffix}.DAT"
    system_cmd: list[object] = [
        "-m", "langrisser.saturn_system_pack",
        "--lang", args.lang,
        "--lang-root", lang.root.parent,
        "--platform", args.platform,
        "--platform-root", args.platform_root,
        "--release", release.code,
        "--release-root", args.release_root,
        "--system-in", system_font,
        "--system-out", system_out,
        "--strings", reflowed_system_strings,
        "--release-strings", build_overrides / "system_strings.json",
        "--tbl", tbl,
        "--layout", lang.system_layout,
        "--system-source", system_source,
    ]
    if args.allow_unmapped:
        system_cmd.append("--allow-unmapped")
    run(*system_cmd)
    run("-m", "langrisser.saturn_name_entry",
        "--lang", args.lang, "--lang-root", lang.root.parent,
        "--system-in", system_out,
        "--system-out", system_out,
        "--tbl", tbl)
    if lang.now_loading:
        run("-m", "langrisser.saturn_now_loading",
            "--lang", args.lang, "--lang-root", lang.root.parent,
            "--system", system_out,
            "--out-system", system_out,
            "--out-preview", saturn / f"now_loading_{lang.suffix}_preview.png")
    # The runtime addresses SYSTEM text through the pointer directory at
    # +0x8000; validate the final file against the write contract so no
    # stage can clobber it again (see docs/SATURN_DISC_FORMAT.md).
    run("-m", "langrisser.saturn_system_validate",
        "--orig", system_in,
        "--system", system_out,
        "--tbl", tbl)

    scen_out = saturn / f"SCEN.{lang.suffix}.DAT"
    scen_cmd: list[object] = [
        "-m", "langrisser.saturn_apply",
        "--lang", args.lang, "--lang-root", lang.root.parent,
        "--platform", args.platform,
        "--platform-root", args.platform_root,
        "--release", release.code,
        "--release-root", args.release_root,
        "--scen", scen_in,
        "--out-scen", scen_out,
        "--tbl", tbl,
        "--translation-root", build_translation_root,
    ]
    if args.allow_unmapped:
        scen_cmd.append("--allow-unmapped")
    run(*scen_cmd)

    # SCENARIO CLEAR banner (CLEAR.DAT), if extracted and the pack sets the text.
    clear_in = saturn / "CLEAR.DAT"
    if lang.scenario_clear and clear_in.exists():
        run("-m", "langrisser.saturn_scenario_clear",
            "--lang", args.lang, "--lang-root", lang.root.parent,
            "--clear", clear_in,
            "--out-clear", saturn / f"CLEAR.{lang.suffix}.DAT")

    # Translator credits on both title screens (TITLE1/TITLE2), if extracted.
    for title_name in ("TITLE1", "TITLE2"):
        title_in = saturn / f"{title_name}.DAT"
        if title_in.exists():
            run("-m", "langrisser.saturn_title_credits",
                "--lang", args.lang, "--lang-root", lang.root.parent,
                "--title", title_in,
                "--out-title", saturn / f"{title_name}.{lang.suffix}.DAT",
                "--out-preview", saturn / f"{title_name.lower()}_credits_{lang.suffix}_preview.png",
                "--credits-json", lang.title_credits,
                "--version", args.patch_version)

    # Prologue poem in the attract loop (OPEN.DAT sub-asset 2), if extracted.
    open_in = saturn / "OPEN.DAT"
    if open_in.exists() and has_target_text(lang.poem):
        run("-m", "langrisser.saturn_poem_translate",
            "--lang", args.lang, "--lang-root", lang.root.parent,
            "--open", open_in,
            "--out-open", saturn / f"OPEN.{lang.suffix}.DAT",
            "--out-preview", saturn / f"open_poem_{lang.suffix}_preview.png")

    # Every on-disc file this build replaces, in disc-path order. Optional
    # stages (banner, title, poem) only produce theirs when the pack carries
    # the text, so the list is what actually got built.
    replaced = {"/SCEN.DAT": scen_out, "/SYSTEM.DAT": system_out}
    for name in ("CLEAR", "TITLE1", "TITLE2", "OPEN"):
        candidate = saturn / f"{name}.{lang.suffix}.DAT"
        if candidate.exists():
            replaced[f"/{name}.DAT"] = candidate

    if args.remaster_disc:
        out_bin = Path(args.out_bin) if args.out_bin else saturn / f"langrisser_v_{lang.suffix}_saturn.bin"
        out_cue = Path(args.out_cue) if args.out_cue else saturn / f"langrisser_v_{lang.suffix}_saturn.cue"
        source = Path(args.cue) if args.cue else release.image
        # Which writer this is follows the release's medium, not this flow.
        writer_for(release, out_cue=out_cue).write(source, out_bin, replaced)

    print(f"saturn build: system -> {system_out}, scen -> {scen_out}")

    if not args.skip_reference:
        # The title screens render the commit hash, so they only compare
        # while the tree has not moved; everything else is pinned outright.
        check_or_record(
            release.code, args.lang, replaced,
            record=args.record_reference,
            stamp=git_short_hash(),
            stamped=("/TITLE1.DAT", "/TITLE2.DAT"))


if __name__ == "__main__":
    main()
