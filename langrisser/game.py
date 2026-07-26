#!/usr/bin/env python3
"""Game manifest helpers.

A *game* is the work itself, independent of how it was shipped: its glyph
plane contents, its curated token table and its language packs. Everything
about a particular print — media, disc paths, boot executable, file offsets,
dump hashes — belongs to a release instead (`langrisser.release`), because those
differ between a game's ports and between region variants of one port.

So a game manifest does not list its platforms: releases name the games they
ship, and `GamePack.releases` reads that back. Storing the list on both sides
would be the same fact written twice.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langrisser.project import ROOT

DEFAULT_GAME_ROOT = ROOT / "data" / "games"
DEFAULT_GAME = "l5"


def _path(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else base / p


@dataclass(frozen=True)
class GamePack:
    code: str
    root: Path
    _data: dict[str, Any]

    @property
    def label(self) -> str:
        return str(self._data.get("label") or self.code)

    @property
    def engine(self) -> str:
        """Container family the game was built on."""
        return str(self._data.get("engine") or "l45")

    @property
    def patch_stem(self) -> str:
        """Stem of this game's shipped patch and its work image."""
        return str(self._data.get("patch_stem") or self.code)

    @property
    def font_map(self) -> Path:
        """Slot->character map for this game's glyph plane.

        Each game generates its own plane, so the kanji bank differs; the
        shared low range (kana/ASCII) is identical and lives in the same CSV
        convention (`index_dec,index_hex,group,char,source`).
        """
        return _path(self.root, str(self._data["font_map"]))  # type: ignore[return-value]

    @property
    def text_table(self) -> Path | None:
        """Curated `HHHH=text` token table, when the game has one.

        Langrisser V ships one (`data/common/tables/lang5_jp.tbl`) with
        editorial fixes on top of the raw glyph map; a game without one reads
        its `font_map` instead.
        """
        return _path(self.root, self._data.get("text_table"))

    @property
    def lang_root(self) -> Path:
        """Directory holding this game's language packs."""
        return _path(self.root, str(self._data["lang_root"]))  # type: ignore[return-value]

    @property
    def releases(self) -> list[str]:
        """Slugs of every release shipping this game, read from the releases."""
        from langrisser.release import releases_for
        return [r.code for r in releases_for(self.code)]

    @property
    def platforms(self) -> list[str]:
        """Consoles this game has been brought up on, via its releases."""
        from langrisser.release import releases_for
        return sorted({r.platform for r in releases_for(self.code)})


def load_game(game: str = DEFAULT_GAME,
              game_root: str | Path = DEFAULT_GAME_ROOT) -> GamePack:
    root = Path(game_root)
    if not root.is_absolute():
        root = ROOT / root
    pack_root = root / game
    manifest = pack_root / "manifest.json"
    if not manifest.exists():
        raise SystemExit(f"game manifest not found: {manifest}")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    code = str(data.get("game") or game)
    return GamePack(code=code, root=pack_root, _data=data)


def add_game_args(ap: argparse.ArgumentParser, default: str = DEFAULT_GAME) -> None:
    ap.add_argument("--game", default=default,
                    help="Game code from data/games/<code> (l5, l4).")
    ap.add_argument("--game-root", default="data/games",
                    help="Directory containing game manifests.")


def game_from_args(args: argparse.Namespace) -> GamePack:
    return load_game(getattr(args, "game", DEFAULT_GAME),
                     getattr(args, "game_root", DEFAULT_GAME_ROOT))
