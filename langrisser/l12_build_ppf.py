#!/usr/bin/env python3
"""Build the Langrisser I & II patch: plane, both scripts, one PPF.

The disc carries two complete games that share a glyph plane, so the build is
one pass over the release rather than one per game: assign slots from what both
translations need, draw the plane once and write it into both game directories,
insert each script, inject, diff.

The alphabet is reassigned on every build on purpose. Which kanji can be
sacrificed depends on how much Japanese is left, so the pool grows as the
translation does and the packing gets better by itself; pinning yesterday's
assignment would freeze the worst version of it.

Nothing may change size — the data track is followed by CD audio — so the
inserter keeps the container's length and the injector refuses to grow a file.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from langrisser.game import add_game_args, game_from_args, load_game
from langrisser.media import writer_for
from langrisser.ppf3 import write_ppf3
from langrisser.project import (add_language_args, language_from_args,
                                load_language)
from langrisser.release import add_release_args, release_from_args


def run(*cmd: object) -> None:
    subprocess.run([sys.executable, *(str(c) for c in cmd)], check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_language_args(ap)
    add_game_args(ap, default="l1")
    add_release_args(ap, default="l1-2-ps1-jp")
    ap.add_argument("--orig-bin", default=None)
    ap.add_argument("--work-bin", default=None)
    ap.add_argument("--out-ppf", default=None)
    ap.add_argument("--build-dir", default=None)
    ap.add_argument("--translation-root", default=None,
                    help="Override the selected --game's SCEN directory. The "
                         "other game on the disc keeps its language pack; useful "
                         "for throwaway builds that must not touch tracked text.")
    args = ap.parse_args()

    release = release_from_args(args, platform="ps1")
    games = list(release.games)
    # The language pack has to be this disc's. Resolved without a game it
    # falls back to Langrisser V's, whose description would then be stamped
    # into a Langrisser I & II patch.
    game = game_from_args(args)
    if game.code not in games:
        raise SystemExit(f"{game.code} is not on release {release.code}")
    args.lang_root = str(game.root / "lang")
    lang = language_from_args(args)
    build = Path(args.build_dir) if args.build_dir else Path("work", "build", release.code)
    build.mkdir(parents=True, exist_ok=True)
    orig_bin = Path(args.orig_bin) if args.orig_bin else release.image
    if orig_bin is None:
        raise SystemExit(f"release {release.code} declares no source image")

    roots = {
        code: load_language(
            args.lang, load_game(code, args.game_root).lang_root, code
        ).script_dir
        for code in games
    }
    if args.translation_root:
        roots[game.code] = Path(args.translation_root)
    for code, root in roots.items():
        if not root.is_dir():
            raise SystemExit(f"{code} translation root is not a directory: {root}")

    # One generated table for the disc: the plane is shared, so a slot spent on
    # one game is spent on the other. Start from the durable baseline but never
    # rewrite it during an ordinary build.
    build_assignments = build / f"font_slot_assignments.{lang.suffix}.csv"
    slot_args: list[object] = [
        "-m", "langrisser.l12_font_slots",
        "--lang", args.lang, "--game", games[0], "--release", release.code,
        "--assignments", lang.font_assignments,
        "--out", build_assignments,
    ]
    for code in games:
        slot_args.extend(["--translation-root", f"{code}={roots[code]}"])
    run(*slot_args)

    font_dat = build / f"FONT.{lang.suffix}.DAT"
    run("-m", "langrisser.l12_build_font",
        "--lang", args.lang, "--game", games[0],
        "--assignments", build_assignments,
        "--out-font-dat", font_dat)

    injections = {}
    for game in games:
        out_scen = build / f"SCEN.{game}.{lang.suffix}.DAT"
        insert = ["-m", "langrisser.l12_sceninsert",
                  "--lang", args.lang, "--game", game, "--release", release.code,
                  "--translation-root", roots[game],
                  "--assignments", build_assignments,
                  "--out-scen", out_scen]
        run(*insert)
        injections[release.media_path("SCEN.DAT", game)] = out_scen
        # Both game directories hold their own copy of the same plane.
        injections[release.media_path("FONT.DAT", game)] = font_dat

    work_bin = Path(args.work_bin) if args.work_bin else build / f"{release.code}.{lang.suffix}.bin"
    written = writer_for(release).write(orig_bin, work_bin, injections)

    out_ppf = Path(args.out_ppf) if args.out_ppf else Path(
        "patches", f"langrisser_1_2_{lang.suffix}.ppf")
    out_ppf.parent.mkdir(parents=True, exist_ok=True)
    # The patch covers the whole disc, so it is described by the release
    # rather than by whichever game's pack supplied the fonts.
    description = f"{release.label} {lang.label} script+font"
    records = write_ppf3(orig_bin.read_bytes(), written.read_bytes(), out_ppf,
                         description)
    print(f"ppf_records={records} out={out_ppf}")


if __name__ == "__main__":
    main()
