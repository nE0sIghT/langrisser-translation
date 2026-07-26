#!/usr/bin/env python3
"""Reflow fixed four-line SYSTEM description cards to their measured width."""
import argparse
import json
from pathlib import Path

from langrisser.game import add_game_args, game_from_args
from langrisser.offsetgroups import group_key, parse_group_key
from langrisser.scen import Codec, load_charmap_tbl


def wrap_words(text: str, codec: Codec, widths: list[int]) -> list[str]:
    words = text.split()

    def solve(word_index: int, line_index: int) -> list[str] | None:
        if word_index == len(words):
            return []
        if line_index == len(widths):
            return None
        for end in range(len(words), word_index, -1):
            line = " ".join(words[word_index:end])
            if len(codec.encode(line)) > widths[line_index]:
                continue
            tail = solve(end, line_index + 1)
            if tail is not None:
                return [line, *tail]
        return None

    lines = solve(0, 0)
    if lines is None:
        raise ValueError(
            f"text does not fit line widths {widths}: {text!r}"
        )
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_game_args(ap)
    ap.add_argument("--strings", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tbl", required=True)
    ap.add_argument("--system-source", required=True)
    ap.add_argument("--layout",
                    default=None,
                    help="Multi-line card groups (default: the game manifest's).")
    args = ap.parse_args()

    strings_path = Path(args.strings)
    strings = json.loads(strings_path.read_text(encoding="utf-8"))
    source = {
        entry["id"]: entry
        for entry in json.loads(
            Path(args.system_source).read_text(encoding="utf-8")
        )
    }
    card_layout = (Path(args.layout) if args.layout
                   else game_from_args(args).system_card_layout)
    layout = json.loads(card_layout.read_text(encoding="utf-8"))
    codec = Codec(load_charmap_tbl(Path(args.tbl)))
    problems: list[str] = []
    changed = 0

    for group, spec in layout["groups"].items():
        size = int(spec["card_size"])
        width = int(spec["line_cells"])
        indices = sorted(
            entry for entry in (
                parsed[1] for parsed in
                (parse_group_key(key) for key in strings)
                if parsed and parsed[0] == int(group)
            )
        )
        if not indices:
            continue
        count = max(indices) + 1
        if count % size:
            raise SystemExit(
                f"group {group}: {count} entries is not divisible by {size}"
            )
        for start in range(0, count, size):
            keys = [group_key(int(group), i) for i in range(start, start + size)]
            widths = [
                width - int(source[key].get("leading_cells", 0))
                for key in keys
            ]
            parts = [
                strings.get(key, "")
                for key in keys
                if strings.get(key, "") not in ("", "{BLANK}")
            ]
            if not parts:
                continue
            try:
                lines = wrap_words(" ".join(parts), codec, widths)
            except ValueError as exc:
                problems.append(f"{table} card {start // size}: {exc}")
                continue
            if len(lines) > size:
                problems.append(
                    f"{table} card {start // size}: "
                    f"{len(lines)} lines exceeds {size}"
                )
                continue
            lines.extend(["{BLANK}"] * (size - len(lines)))
            for key, line in zip(keys, lines):
                if strings.get(key) != line:
                    strings[key] = line
                    changed += 1

    for problem in problems:
        print(f"PROBLEM {problem}")
    if problems:
        raise SystemExit(f"{len(problems)} SYSTEM card reflow problem(s)")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(strings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"reflowed {changed} SYSTEM card lines -> {out}")


if __name__ == "__main__":
    main()
