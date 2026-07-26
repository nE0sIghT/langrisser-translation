#!/usr/bin/env python3
"""Engine manifest helpers.

An *engine* is the container family a release was built on: which formats its
files are in, how its glyph plane is laid out, how its text is encoded. It is
what decides whether a given loader can read a release at all.

It is a separate axis from the other three because it cuts across them.
Langrisser IV and V share one engine on both consoles, so the same SCEN and
SYSTEM loaders serve four releases; a Langrisser built on different code
shares none of them however familiar the game is.

The glyph geometry lives here rather than as constants in three font modules
that each spelled out 12, 12 and 18. Reading it from the engine is what lets a
plane of another shape be described instead of hardcoded.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langrisser.project import ROOT

DEFAULT_ENGINE_ROOT = ROOT / "data" / "engines"
DEFAULT_ENGINE = "l45"


@dataclass(frozen=True)
class GlyphGeometry:
    """Layout of one glyph in a release's font plane."""

    width: int
    height: int
    bpp: int
    bytes_per_glyph: int
    plane_offset: int
    default_max_slot: int

    def offset(self, slot: int) -> int:
        """Byte offset of a slot's bitmap within the file."""
        return self.plane_offset + slot * self.bytes_per_glyph

    def slot_slice(self, slot: int) -> slice:
        start = self.offset(slot)
        return slice(start, start + self.bytes_per_glyph)

    def tile(self, data: bytes, slot: int) -> bytes:
        return bytes(data[self.slot_slice(slot)])

    def slots_in(self, limit: int) -> int:
        """How many whole slots fit below a file offset."""
        return max(0, (limit - self.plane_offset)) // self.bytes_per_glyph


@dataclass(frozen=True)
class EnginePack:
    code: str
    root: Path
    _data: dict[str, Any]

    @property
    def label(self) -> str:
        return str(self._data.get("label") or self.code)

    @property
    def glyph(self) -> GlyphGeometry:
        g = dict(self._data.get("glyph") or {})
        return GlyphGeometry(
            width=int(g.get("width", 12)),
            height=int(g.get("height", 12)),
            bpp=int(g.get("bpp", 1)),
            bytes_per_glyph=int(g.get("bytes_per_glyph", 18)),
            plane_offset=int(str(g.get("plane_offset", 0)), 0)
            if isinstance(g.get("plane_offset"), str) else int(g.get("plane_offset", 0)),
            default_max_slot=int(g.get("default_max_slot", 1820)),
        )

    @property
    def containers(self) -> list[str]:
        return [str(c) for c in (self._data.get("containers") or [])]

    @property
    def codec(self) -> str:
        return str(self._data.get("codec") or "slot_tokens")


def load_engine(engine: str = DEFAULT_ENGINE,
                engine_root: str | Path = DEFAULT_ENGINE_ROOT) -> EnginePack:
    root = Path(engine_root)
    if not root.is_absolute():
        root = ROOT / root
    pack_root = root / engine
    manifest = pack_root / "manifest.json"
    if not manifest.exists():
        raise SystemExit(f"engine manifest not found: {manifest}")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    code = str(data.get("engine") or engine)
    return EnginePack(code=code, root=pack_root, _data=data)


def add_engine_args(ap: argparse.ArgumentParser,
                    default: str = DEFAULT_ENGINE) -> None:
    ap.add_argument("--engine", default=default,
                    help="Engine code from data/engines/<code>.")


def engine_from_args(args: argparse.Namespace) -> EnginePack:
    return load_engine(getattr(args, "engine", DEFAULT_ENGINE))
