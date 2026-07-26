#!/usr/bin/env python3
"""Project path helpers for target-language builds.

The toolkit keeps generated source dumps under work/ and durable translation
assets under each game's lang/<code>/. This module is the single place that resolves
language manifests and derived output paths.

A pack manifest has two halves. Its top level holds what every target language
needs whatever the game is: script directory, fonts and metrics, glossary,
SYSTEM overlays, review status. Its `assets` block holds the text of screens a
particular game happens to have — Langrisser V's prologue poem and Virash
monologue, the title credits, the name-entry grid. Splitting them is what
tells the author of a new game's pack which half applies to them, and lets a
game add a screen through `asset_path`/`asset_text` without this module
growing a property for it.

Target text that only one release ships lives under the pack's
`releases/<slug>/`, mirroring the shared layout. It is keyed by release rather
than by console because it is a delta against a specific build.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
COMMON_ROOT = ROOT / "data" / "common"
COMMON_FONT_MAP = COMMON_ROOT / "font_mapping" / "groups_report.csv"
COMMON_FONT_FIXES = COMMON_ROOT / "font_mapping" / "proposed_fixes.csv"
COMMON_SCENARIO_MAP = COMMON_ROOT / "scenario_map.json"
COMMON_JP_TBL = COMMON_ROOT / "tables" / "lang5_jp.tbl"


def _path(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else base / p


@dataclass(frozen=True)
class LanguagePack:
    code: str
    root: Path
    _data: dict[str, Any]

    @property
    def label(self) -> str:
        return str(self._data.get("label") or self.code)

    @property
    def suffix(self) -> str:
        return str(self._data.get("patch_suffix") or self.code)

    @property
    def patch_description(self) -> str:
        return str(self._data.get("patch_description") or f"Langrisser V {self.suffix.upper()} script+font")

    @property
    def script_dir(self) -> Path:
        return _path(self.root, str(self._data.get("script_dir") or "SCEN"))  # type: ignore[return-value]

    @property
    def dump_root(self) -> Path:
        return self.script_dir.parent

    @property
    def assets(self) -> dict[str, Any]:
        """Text and layout of screens specific to this pack's game."""
        return dict(self._data.get("assets") or {})

    def asset_path(self, name: str, default: str | None = None) -> Path:
        """File holding a game-specific asset, defaulting to its usual name."""
        return _path(self.root, str(self.assets.get(name) or default or name))  # type: ignore[return-value]

    def asset_text(self, name: str) -> str:
        """A game-specific asset that is a literal string, empty when absent.

        Absent means "leave the original alone": a pack that has not translated
        a banner keeps the shipped one rather than blanking it.
        """
        return str(self.assets.get(name) or "")

    def overrides(self, release: str) -> Path:
        """Directory of target text that only `release` ships.

        Keyed by release, not by console: the delta is against one build, and
        two builds of the same game on one console would need separate ones.
        """
        return self.root / "releases" / release

    def override_script_dir(self, release: str) -> Path:
        return self.overrides(release) / self.script_stem

    def override_system_strings(self, release: str) -> Path:
        return self.overrides(release) / "system_strings.json"

    @property
    def script_stem(self) -> str:
        return self.script_dir.name

    @property
    def font_assignments(self) -> Path:
        return _path(self.root, str(self._data.get("font_assignments") or "font_slot_assignments.csv"))  # type: ignore[return-value]

    @property
    def system_strings(self) -> Path:
        return _path(self.root, str(self._data.get("system_strings") or "system_strings.json"))  # type: ignore[return-value]

    @property
    def system_layout(self) -> Path:
        return _path(self.root, str(self._data.get("system_layout") or "system_layout.json"))  # type: ignore[return-value]

    @property
    def system_complete(self) -> bool:
        value = self._data.get("system_complete", False)
        if not isinstance(value, bool):
            raise SystemExit(
                f"{self.root / 'manifest.json'}: system_complete must be boolean"
            )
        return value

    @property
    def title_credits(self) -> Path:
        return self.asset_path("title_credits", "title_credits.json")

    @property
    def names(self) -> Path:
        return _path(self.root, str(self._data.get("names") or "names.csv"))  # type: ignore[return-value]

    @property
    def glossary(self) -> Path:
        return _path(self.root, str(self._data.get("glossary") or "glossary.csv"))  # type: ignore[return-value]

    @property
    def name_entry_grid(self) -> Path:
        return self.asset_path("name_entry_grid", "name_entry_grid.json")

    @property
    def manual_record_overrides(self) -> Path:
        return self.asset_path("manual_record_overrides",
                               "manual_record_overrides.json")

    @property
    def review_status(self) -> Path:
        return _path(self.root, str(self._data.get("review_status") or "review_status.csv"))  # type: ignore[return-value]

    @property
    def poem(self) -> Path:
        return self.asset_path("poem", "poem_prologue.txt")

    @property
    def poem_source(self) -> Path:
        return self.asset_path("poem_source", "poem_prologue_jp.txt")

    @property
    def virash_monologue(self) -> Path:
        return self.asset_path("virash_monologue", "virash_monologue.json")

    @property
    def font(self) -> Path | None:
        return _path(self.root, self._data.get("font"))

    @property
    def font_size(self) -> int:
        return int(self._data.get("font_size") or 10)

    @property
    def scenario_clear(self) -> str:
        return self.asset_text("scenario_clear")

    @property
    def now_loading(self) -> str:
        return self.asset_text("now_loading")

    @property
    def caps_font(self) -> Path | None:
        return _path(self.root, self._data.get("caps_font"))

    @property
    def caps_font_size(self) -> int:
        return int(self._data.get("caps_font_size") or 0)

    @property
    def single_chars(self) -> str:
        return str(self._data.get("single_chars") or "")

    @property
    def forced_pairs(self) -> list[str]:
        pairs = self._data.get("forced_pairs") or []
        if not isinstance(pairs, list) or any(not isinstance(p, str) or len(p) != 2 for p in pairs):
            raise SystemExit(f"{self.root / 'manifest.json'}: forced_pairs must contain two-character strings")
        return list(pairs)

    @property
    def window_width(self) -> int:
        return int(self._data.get("window_width") or 21)

    @property
    def choice_width(self) -> int:
        return int(self._data.get("choice_width") or 21)

    @property
    def max_lines(self) -> int:
        return int(self._data.get("max_lines") or 4)

    def manifest_copy(self) -> dict[str, Any]:
        return dict(self._data)

    @property
    def tbl(self) -> Path:
        return ROOT / "work" / "tables" / f"lang5_{self.suffix}.tbl"

    def build_path(self, name: str) -> Path:
        return ROOT / "work" / "build" / name.format(lang=self.suffix)

    @property
    def work_bin(self) -> Path:
        return self.build_path("langrisser_v_{lang}.bin")

    @property
    def out_ppf(self) -> Path:
        return ROOT / "patches" / f"langrisser_v_{self.suffix}.ppf"

    @property
    def wip_root(self) -> Path:
        return ROOT / "work" / f"wip_{self.suffix}"

    @property
    def review_root(self) -> Path:
        return ROOT / "work" / "review" / self.suffix


def default_lang_root(game: str | None = None,
                      game_root: str | Path | None = None) -> Path:
    """Where a game keeps its language packs (from its manifest).

    Imported lazily: `langrisser.game` builds on this module's ROOT.
    """
    from langrisser.game import DEFAULT_GAME, DEFAULT_GAME_ROOT, load_game

    return load_game(game or DEFAULT_GAME, game_root or DEFAULT_GAME_ROOT).lang_root


def load_language(lang: str = "en", lang_root: str | Path | None = None) -> LanguagePack:
    root = Path(lang_root) if lang_root else default_lang_root()
    if not root.is_absolute():
        root = ROOT / root
    pack_root = root / lang
    manifest = pack_root / "manifest.json"
    if not manifest.exists():
        raise SystemExit(f"language manifest not found: {manifest}")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    code = str(data.get("lang") or lang)
    return LanguagePack(code=code, root=pack_root, _data=data)


def add_language_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--lang", default="en",
                    help="Target language code from the game's pack root.")
    ap.add_argument("--lang-root", default=None,
                    help="Override the directory containing language packs "
                         "(default: the game manifest's lang_root).")


def language_from_args(args: argparse.Namespace) -> LanguagePack:
    root = getattr(args, "lang_root", None) or default_lang_root(
        getattr(args, "game", None), getattr(args, "game_root", None))
    return load_language(args.lang, root)
