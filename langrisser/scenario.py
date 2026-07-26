#!/usr/bin/env python3
"""Work with the script by game scenario instead of raw chunk numbers.

Uses data/common/scenario_map.json: scenario K consists of scene_a (chunk 44+K),
the battle chunk K (which also holds the post-battle dialogue) and scene_b
(chunk 86+K).

Commands:
  list                 overview of all scenarios with titles and progress
  chunks K             chunk numbers of scenario K (or: quiz, opt:NAME)
  dump K               JP text of scenario K in play order, one file
  prefill K            stage scenario K chunks for translation (tm_prefill)
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from langrisser.project import COMMON_SCENARIO_MAP, add_language_args, language_from_args

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = COMMON_SCENARIO_MAP
JP_DUMP = ROOT / "work/l5/scriptdump/SCEN"
TARGET_DUMP = ROOT / "data/games/l5/lang/en/SCEN"
TAG_RE = re.compile(r"<\$[0-9A-F]{4}>")


def load_map() -> dict:
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


def scenario_chunks(smap: dict, key: str) -> list[tuple[str, int]]:
    """Ordered (role, chunk) pairs for a scenario selector."""
    if key == "quiz":
        return [("quiz", c) for c in smap["quiz"]["chunks"]] + \
               [("tutorial battle", c) for c in smap["tutorial_battle"]["chunks"]]
    if key.startswith("opt:"):
        opt = smap["optional_maps"][key[4:]]
        return [("intro", opt["intro"]), ("battle", opt["battle"])]
    k = int(key)
    n = smap["scenario_rule"]["scenarios"]
    if not 1 <= k <= n:
        raise SystemExit(f"scenario must be 1..{n}, or 'quiz', or 'opt:NAME'")
    return [("scene a", 44 + k), ("battle", k), ("scene b", 86 + k)]


def read_records(cidx: int, root: Path) -> dict[int, str]:
    fp = root / f"chunk_{cidx:03d}.txt"
    out: dict[int, str] = {}
    if not fp.exists():
        return out
    for raw in fp.read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith("#") or "\t" not in raw:
            continue
        idx, text = raw.split("\t", 1)
        if idx.strip().isdigit():
            out[int(idx)] = text
    return out


def battle_title(cidx: int) -> str:
    """First location-name record (FFFF) that follows an objective (FFFE)."""
    recs = read_records(cidx, TARGET_DUMP) or read_records(cidx, JP_DUMP)
    seen_objective = False
    for idx in sorted(recs):
        text = recs[idx]
        if text.endswith("<$FFFE>"):
            seen_objective = True
        elif seen_objective and text.endswith("<$FFFF>"):
            return TAG_RE.sub("", text).strip()
    return ""


def chunk_progress(cidx: int) -> str:
    return CURRENT_LANG.upper() if (
        TARGET_DUMP / f"chunk_{cidx:03d}.txt"
    ).exists() else "jp"


def cmd_list(smap: dict) -> None:
    n = smap["scenario_rule"]["scenarios"]
    print("scenario  scene_a  battle  scene_b  state        title")
    for k in range(1, n + 1):
        parts = scenario_chunks(smap, str(k))
        states = "/".join(chunk_progress(c) for _, c in parts)
        print(f"{k:8d}  {44+k:7d}  {k:6d}  {86+k:7d}  {states:11s}  {battle_title(k)}")
    print("\nquiz: chunk 0 + tutorial battle 37"
          f" [{chunk_progress(0)}/{chunk_progress(37)}]")
    for name, opt in smap["optional_maps"].items():
        if not isinstance(opt, dict):
            continue
        states = "/".join(chunk_progress(opt[r]) for r in ("intro", "battle"))
        print(f"opt:{name}: intro {opt['intro']} + battle {opt['battle']} [{states}]")
    print(f"recaps: world {smap['recaps']['world_situation']},"
          f" bios {smap['recaps']['character_bios']}")


def cmd_dump(smap: dict, key: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = key.replace(":", "_")
    out_fp = out_dir / f"scenario_{name}.txt"
    lines: list[str] = []
    for role, cidx in scenario_chunks(smap, key):
        jp = read_records(cidx, JP_DUMP)
        target = read_records(cidx, TARGET_DUMP)
        lines.append(f"=== {role}: chunk {cidx:03d}"
                     + (" (translated)" if target else "") + " ===")
        for idx in sorted(jp):
            lines.append(f"{idx}\t{target.get(idx, jp[idx])}")
        lines.append("")
    out_fp.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out_fp}")


def cmd_prefill(smap: dict, key: str) -> None:
    for role, cidx in scenario_chunks(smap, key):
        if (TARGET_DUMP / f"chunk_{cidx:03d}.txt").exists():
            print(f"{role}: chunk {cidx:03d} already translated, skipping")
            continue
        subprocess.run([sys.executable, str(ROOT / "scripts/tm_prefill.py"),
                        "--lang", CURRENT_LANG, str(cidx)], check=True)


CURRENT_LANG = "en"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_language_args(ap)
    ap.add_argument("command", choices=("list", "chunks", "dump", "prefill"))
    ap.add_argument("scenario", nargs="?",
                    help="1..36, 'quiz' or 'opt:NAME' (see data/common/scenario_map.json)")
    ap.add_argument("--out-dir", default="work/scenario_text")
    args = ap.parse_args()

    global TARGET_DUMP, CURRENT_LANG
    lang = language_from_args(args)
    TARGET_DUMP = lang.script_dir
    CURRENT_LANG = lang.code

    smap = load_map()
    if args.command == "list":
        cmd_list(smap)
        return
    if not args.scenario:
        raise SystemExit("this command needs a scenario selector")
    if args.command == "chunks":
        for role, cidx in scenario_chunks(smap, args.scenario):
            print(f"{role}: {cidx}")
    elif args.command == "dump":
        cmd_dump(smap, args.scenario, Path(args.out_dir))
    elif args.command == "prefill":
        cmd_prefill(smap, args.scenario)


if __name__ == "__main__":
    main()
