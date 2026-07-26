#!/usr/bin/env python3
"""Build a Langrisser V target-language PPF patch.

Pipeline: language font into SYSTEM.BIN -> insert language dump into SCEN/SCEN2 ->
inject all three into a copy of the BIN -> PPF3 diff against the original.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from langrisser.build_reference import add_reference_args, check_or_record
from langrisser.game import add_game_args, game_from_args
from langrisser.imgdat import git_short_hash
from langrisser.media import writer_for
from langrisser.release import add_release_args, release_from_args
from langrisser.project import add_language_args, language_from_args
from langrisser.ppf3 import write_ppf3


def run(*cmd: object) -> None:
    subprocess.run([sys.executable, *(str(c) for c in cmd)], check=True)


def has_target_text(path: Path) -> bool:
    return any(
        line.strip() and not line.lstrip().startswith("#") and line.strip() != "---"
        for line in path.read_text(encoding="utf-8").splitlines()
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("--orig-bin", default=None,
                    help="Source image (default: the release manifest's).")
    ap.add_argument("--translation-root", default=None,
                    help="Override the language pack's translated-text root.")
    ap.add_argument("--scen", default="work/l5/extracted/SCEN.DAT")
    ap.add_argument("--scen2", default="work/l5/extracted/SCEN2.DAT")
    ap.add_argument("--system", default="work/l5/extracted/SYSTEM.BIN")
    ap.add_argument("--imgdat", default="work/l5/extracted/IMG.DAT")
    add_game_args(ap)
    ap.add_argument("--patch-version", default="dev")
    ap.add_argument("--work-bin", default=None)
    ap.add_argument("--out-ppf", default=None)
    add_release_args(ap)
    add_reference_args(ap)
    args = ap.parse_args()

    lang = language_from_args(args)
    scripts = Path(__file__).parent
    build_dir = lang.build_root
    build_dir.mkdir(parents=True, exist_ok=True)
    translation_root = (Path(args.translation_root)
                        if args.translation_root else lang.dump_root)
    build_translation_root = build_dir / f"translation.{lang.suffix}"
    if build_translation_root.exists():
        shutil.rmtree(build_translation_root)
    shutil.copytree(translation_root, build_translation_root)
    tbl = lang.tbl
    game = game_from_args(args)
    release = release_from_args(args, platform="ps1")
    exe = release.boot(game.code)
    exe_name = exe.lstrip("/")
    orig_bin = Path(args.orig_bin) if args.orig_bin else release.image
    if orig_bin is None:
        raise SystemExit(f"release {release.code} declares no source image")
    suffix = lang.suffix
    work_bin_path = Path(args.work_bin) if args.work_bin else lang.work_bin
    out_ppf_path = Path(args.out_ppf) if args.out_ppf else lang.out_ppf

    # Rebuild the generated SYSTEM source first: the font allocator needs the
    # current stable ids to exclude JP glyphs still used by untranslated UI.
    system_source = f"{build_dir}/system_source.{suffix}.json"
    run("-m", "langrisser.system_dump",
        "--system-bin", args.system,
        "--out", system_source)
    resolved_system_strings = f"{build_dir}/system_strings.{suffix}.json"
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

    # Complete the durable assignment baseline with every pair required by the
    # current target corpus. The generated copy keeps ordinary builds from
    # modifying tracked language-pack data while preventing stale pair tables.
    build_assignments = f"{build_dir}/font_slot_assignments.{suffix}.csv"
    run("-m", "langrisser.assign_font_slots",
        "--lang", args.lang,
        "--lang-root", lang.root.parent,
        "--groups-report", game.font_map,
        "--assignments", lang.font_assignments,
        "--out-assignments", build_assignments,
        "--translation-root", build_translation_root,
        "--menu-map", resolved_system_strings,
        "--system-source", system_source,
        "--scen", args.scen,
        "--scen2", args.scen2)

    font_args = [
        "-m", "langrisser.build_font",
        "--groups-report", game.font_map,
        "--assignments", build_assignments,
        "--system-bin", args.system,
        "--out-system-bin", f"{build_dir}/SYSTEM.BIN.{suffix}.font",
        "--out-tbl", tbl,
        "--font-size", str(lang.font_size),
    ]
    if lang.font:
        font_args.extend(["--font", lang.font])
    if lang.caps_font:
        font_args.extend(["--caps-font", lang.caps_font,
                          "--caps-font-size", str(lang.caps_font_size)])
    run(*font_args)
    reflowed_system_strings = f"{build_dir}/system_strings.{suffix}.reflowed.json"
    run("-m", "langrisser.reflow_system_cards",
        "--strings", resolved_system_strings,
        "--out", reflowed_system_strings,
        "--tbl", tbl,
        "--system-source", system_source)

    # Some SYSTEM menus stream several labels through a 9-column VRAM glyph
    # atlas. A label crossing an atlas row loses its continuation on screen.
    run("-m", "langrisser.validate_system_ui",
        "--lang", args.lang,
        "--lang-root", lang.root.parent,
        "--tbl", tbl,
        "--strings", reflowed_system_strings,
        "--system-source", system_source)

    # Pair selection changes measured cell widths. Rewrap and validate a build
    # copy against the exact generated table used for insertion; a build must
    # never rewrite tracked translation sources.
    run("-m", "langrisser.rewrap",
        "--lang", args.lang,
        "--lang-root", lang.root.parent,
        "--translation-root", build_translation_root,
        "--tbl", tbl,
        "--scen", args.scen)
    run("-m", "langrisser.validate_translation",
        "--lang", args.lang,
        "--lang-root", lang.root.parent,
        "--translation-root", build_translation_root,
        "--tbl", tbl,
        "--scen", args.scen,
        "--scen2", args.scen2)

    # Name-entry screen (kana grid in SYSTEM.BIN + the EXE's input table).
    run("-m", "langrisser.patch_name_entry",
        "--grid", lang.name_entry_grid,
        "--system-in", f"{build_dir}/SYSTEM.BIN.{suffix}.font",
        "--system-out", f"{build_dir}/SYSTEM.BIN.{suffix}.ne",
        "--exe-in", f"work/l5/extracted/{exe_name}",
        "--exe-out", f"{build_dir}/{exe_name}.{suffix}",
        "--tbl", tbl)

    # All SYSTEM.BIN UI text (names, descriptions, command help, save messages)
    # via the unified offset-table flow (see docs/SYSTEM_BIN_FORMAT.md).
    # --repack regenerates each group's offset table so short kanji labels can
    # hold a full translated word; the engine addresses every string by index as
    # base + table[k]*2 (verified in the EXE, see SYSTEM_BIN_FORMAT.md), so the
    # regenerated table is followed correctly. --max-grow caps per-line growth.
    run("-m", "langrisser.system_pack",
        "--system-in", f"{build_dir}/SYSTEM.BIN.{suffix}.ne",
        "--system-out", f"{build_dir}/SYSTEM.BIN.{suffix}",
        "--strings", reflowed_system_strings,
        "--layout", lang.system_layout,
        "--source-strings", system_source,
        "--tbl", tbl,
        "--repack",
        "--strict")

    run("-m", "langrisser.sceninsert", "--fixed-size-repack",
        "--scen", args.scen, "--scen2", args.scen2,
        "--dump-dir", build_translation_root, "--charmap", tbl,
        "--out-scen", f"{build_dir}/SCEN.{suffix}.DAT",
        "--out-scen2", f"{build_dir}/SCEN2.{suffix}.DAT")

    run("-m", "langrisser.imgdat", "title-credits",
        args.imgdat,
        "--out-imgdat", f"{build_dir}/IMG.DAT.{suffix}",
        "--version", args.patch_version,
        "--credits-json", lang.title_credits,
        "--out-raw-preview", f"{build_dir}/title_credits_{suffix}_raw.png",
        "--out-display", f"{build_dir}/title_credits_{suffix}_display.png",
        "--out-crop", f"{build_dir}/title_credits_{suffix}_crop.png")

    # Redraw the translated prologue poem graphic on top of the title credits
    # (different asset, so the two IMG.DAT edits do not overlap).
    if has_target_text(lang.poem):
        run("-m", "langrisser.poem_translate",
            "--imgdat", f"{build_dir}/IMG.DAT.{suffix}",
            "--poem", lang.poem,
            "--out-imgdat", f"{build_dir}/IMG.DAT.{suffix}",
            "--out-preview", f"{build_dir}/poem_{suffix}_preview.png")
    else:
        print(f"no target poem in {lang.poem}; preserving the original poem asset")

    # Redraw the SCENARIO CLEAR banner (asset 9; does not overlap the edits above).
    if lang.scenario_clear:
        run("-m", "langrisser.scenario_clear",
            "--lang", args.lang, "--lang-root", lang.root.parent,
            "--imgdat", f"{build_dir}/IMG.DAT.{suffix}",
            "--out-imgdat", f"{build_dir}/IMG.DAT.{suffix}",
            "--out-preview", f"{build_dir}/scenario_clear_{suffix}_preview.png")
    else:
        print("no scenario_clear text; preserving the original banner asset")

    # Redraw the Now Loading plate (asset 0 type-2 texture; separate packets).
    if lang.now_loading:
        run("-m", "langrisser.now_loading",
            "--lang", args.lang, "--lang-root", lang.root.parent,
            "--imgdat", f"{build_dir}/IMG.DAT.{suffix}",
            "--out-imgdat", f"{build_dir}/IMG.DAT.{suffix}",
            "--out-preview", f"{build_dir}/now_loading_{suffix}_preview.png")
    else:
        print("no now_loading text; preserving the original plate texture")

    injections = {
        release.media_path("SCEN.DAT", game.code): Path(f"{build_dir}/SCEN.{suffix}.DAT"),
        release.media_path("SYSTEM.BIN", game.code): Path(f"{build_dir}/SYSTEM.BIN.{suffix}"),
        release.media_path("IMG.DAT", game.code): Path(f"{build_dir}/IMG.DAT.{suffix}"),
        exe: Path(f"{build_dir}/{exe_name}.{suffix}"),
    }
    if Path(args.scen2).exists():
        injections[release.media_path("SCEN2.DAT", game.code)] = Path(
            f"{build_dir}/SCEN2.{suffix}.DAT")
    # The writer follows the release's medium and never grows the image:
    # relocation is unsafe on this disc, whose free tail region overlaps the
    # CD audio tracks, so file sizes must stay unchanged.
    work_bin = writer_for(release).write(orig_bin, work_bin_path, injections)

    out_ppf = out_ppf_path
    out_ppf.parent.mkdir(parents=True, exist_ok=True)
    records = write_ppf3(
        orig_bin.read_bytes(),
        work_bin.read_bytes(),
        out_ppf,
        lang.patch_description,
    )
    print(f"ppf_records={records} out={out_ppf}")

    if not args.skip_reference:
        artifacts = dict(injections)
        artifacts["patch.ppf"] = out_ppf
        # IMG.DAT carries the title credits, which render the commit hash,
        # and the PPF diff carries IMG.DAT; both only compare while the tree
        # has not moved.
        check_or_record(release.code, args.lang, artifacts,
                        record=args.record_reference,
                        stamp=git_short_hash(),
                        stamped=(release.media_path("IMG.DAT", game.code),
                                 "patch.ppf"))


if __name__ == "__main__":
    main()
