#!/usr/bin/env python3
"""Shared offset-table *group* model for SYSTEM text on both platforms.

Both the PS1 `SYSTEM.BIN` and the Saturn `SYSTEM.DAT` store their UI text as a
sequence of groups, each::

    [ u16 offset table : N entries ][ optional preamble ][ N glyph-code strings ]

The offset table starts with `0x0000` and holds strictly increasing 16-bit word
offsets; string `k` lives at `base + offset[k]*2` and ends at `0xFFFF`. The only
per-platform differences are the byte order of the 16-bit words and where the
first group starts. This module captures that logic once; PS1 tooling uses the
default little-endian config, Saturn tooling passes a big-endian config.

See docs/SYSTEM_BIN_FORMAT.md (PS1) and docs/SATURN_DISC_FORMAT.md (Saturn).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from langrisser.binfmt import LE, ByteOrder

FFFF = 0xFFFF
SOFT_BREAK = 0xFFFC


@dataclass(frozen=True)
class GroupConfig:
    """Per-platform parameters for the offset-table group scan.

    Defaults describe the PS1 `SYSTEM.BIN` layout so existing PS1 callers can
    use the model without passing a config.
    """

    order: ByteOrder = LE
    scan_start: int = 0x8052   # first verified text group table
    max_step: int = 0x30       # max plausible string length (+terminator), words
    min_entries: int = 8       # a real group has at least this many strings
    max_preamble: int = 16     # words between a group's table and its string base


PS1 = GroupConfig()
SATURN = GroupConfig(order=ByteOrder("be"), scan_start=0x7000)


def load_font_map_csv(path: str | Path | None) -> dict[int, str]:
    """Load a slot->char font map CSV (`index_dec,index_hex,group,char,...`).

    This is the tracked map format: `data/common/font_mapping/groups_report.csv`
    for Langrisser V, a per-game file for other games, and the Saturn kanji
    delta. `load_codemap` reads the other tracked format, the `HHHH=text` table.
    """
    import csv

    if path is None or not Path(path).exists():
        return {}
    out: dict[int, str] = {}
    for row in csv.DictReader(open(path, encoding="utf-8")):
        if row["index_dec"].isdigit() and row["char"]:
            out[int(row["index_dec"])] = row["char"]
    return out


def load_codemap(tbl_path: str) -> dict[int, str]:
    """Load a HHHH=text token table into a {code: text} map."""
    codemap: dict[int, str] = {}
    for line in Path(tbl_path).read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if len(key) == 4:
            try:
                codemap[int(key, 16)] = value
            except ValueError:
                pass
    return codemap


def decode_run(words: list[int], codemap: dict[int, str]) -> str:
    """Decode a glyph-code run to text, marking soft breaks and control words."""
    out: list[str] = []
    for w in words:
        if w == SOFT_BREAK:
            out.append("\\n")
        elif w >= 0xFB00 or w == 0:
            out.append("" if w == 0 else f"{{{w:04X}}}")
        else:
            out.append(codemap.get(w, f"{{?{w:04X}}}"))
    return "".join(out)


# A SYSTEM string is named by where it sits, not by where it lands: which group
# it is in and which entry of that group. Those two numbers are the same on
# every build of the game, so one translation serves them all - unlike the
# table's file offset, which differs on each and made a pack readable only next
# to the build it was keyed from. The release manifest records each build's own
# group offsets, which is what turns these names back into addresses.
GROUP_ID = "group"
LOOSE_ID = "loose"


def group_key(group_index: int, entry_index: int) -> str:
    """Name of the `entry_index`-th string of group `group_index`."""
    return f"{GROUP_ID}:{group_index}:{entry_index}"


def loose_key(run_index: int) -> str:
    """Name of a text run that sits outside the group tables."""
    return f"{LOOSE_ID}:{run_index}"


def is_system_key(key: str) -> bool:
    return key.startswith((f"{GROUP_ID}:", f"{LOOSE_ID}:"))


def parse_group_key(key: str) -> tuple[int, int] | None:
    """Group and entry a key names, or None if it names something else."""
    parts = key.split(":")
    if len(parts) != 3 or parts[0] != GROUP_ID:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def read_table(data: bytes, pos: int, cfg: GroupConfig = PS1) -> list[int] | None:
    """Parse a group offset table at `pos`, or None if there isn't one."""
    if pos + 2 > len(data) or cfg.order.u16(data, pos) != 0:
        return None
    vals = [0]
    prev = 0
    i = pos + 2
    while i + 2 <= len(data):
        v = cfg.order.u16(data, i)
        if prev < v <= prev + cfg.max_step:
            vals.append(v)
            prev = v
            i += 2
        else:
            break
    return vals if len(vals) >= cfg.min_entries else None


def run_length(data: bytes, off: int, cfg: GroupConfig = PS1) -> int:
    """Count words until (not including) the next `0xFFFF` terminator."""
    n = 0
    while off + 2 * n + 2 <= len(data) and cfg.order.u16(data, off + 2 * n) != FFFF:
        n += 1
    return n


def base_for(data: bytes, pos: int, table: list[int], cfg: GroupConfig = PS1) -> int | None:
    """Return the string base for a group, or None if the table is not a group.

    A real text group has a `0xFFFF` terminator just before every string start.
    The base is normally `table_end`, but a few groups keep a small preamble
    between the table and the strings, so try a short range of bases and accept
    the first where every terminator checks out.
    """
    table_end = pos + len(table) * 2
    for pre in range(cfg.max_preamble + 1):
        base = table_end + pre * 2
        ok = True
        for k in range(1, len(table)):
            term = base + (table[k] - 1) * 2
            if term + 2 > len(data) or cfg.order.u16(data, term) != FFFF:
                ok = False
                break
        if ok:
            return base
    return None


def group_at(data: bytes, pos: int, cfg: GroupConfig = PS1) -> tuple[list[int], int] | None:
    """Return (table, base) for the group at `pos`, trimming any over-read."""
    table = read_table(data, pos, cfg)
    if table is None:
        return None
    for n in range(len(table), cfg.min_entries - 1, -1):
        sub = table[:n]
        base = base_for(data, pos, sub, cfg)
        if base is not None:
            return sub, base
    return None


def find_groups(data: bytes, cfg: GroupConfig = PS1) -> list[tuple[int, list[int], int]]:
    """Return (table_offset, table, string_base) for every group in `data`."""
    groups: list[tuple[int, list[int], int]] = []
    pos = cfg.scan_start
    while pos + 2 <= len(data):
        found = group_at(data, pos, cfg)
        if found is not None:
            table, base = found
            last_off = base + table[-1] * 2
            end = last_off + (run_length(data, last_off, cfg) + 1) * 2
            groups.append((pos, table, base))
            pos = end
        else:
            pos += 2
    return groups
