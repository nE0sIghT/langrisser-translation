#!/usr/bin/env python3
"""Release manifest helpers.

A *release* is one concrete shipped build: a set of games, on a platform, in
a region, at a revision. It is the thing that actually gets patched, and it is
the axis the series needs.

Game and platform alone cannot describe the series. Compilations put several
games on one medium (the Saturn and PSP collections), the same game ships on
media that share no file system at all (a PS1 ISO9660 disc and a Mega Drive
cartridge), and a game can have region variants whose text differs. Facts like
"where the SYSTEM groups start", "which boot executable", "how high the font
plane may grow" and "which dump is authentic" belong to none of game or
platform: they are properties of a single shipped build.

What each axis owns:

* game     - the work: glyph plane map, curated table, language packs
* release  - this shipped build: media, paths, offsets, dump hashes
* platform - the console itself: nothing game-specific
* engine   - the container family shared by releases built on the same code

"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from langrisser.project import ROOT

DEFAULT_RELEASE_ROOT = ROOT / "data" / "releases"
DEFAULT_REGION = "jp"


def _path(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else base / p


@dataclass(frozen=True)
class ReleasePack:
    code: str
    root: Path
    _data: dict[str, Any]

    @property
    def label(self) -> str:
        return str(self._data.get("label") or self.code)

    @property
    def games(self) -> list[str]:
        """Game codes this release ships, in disc order.

        More than one for a compilation; the media roots are keyed by game.
        """
        return [str(g) for g in self._data.get("games") or []]

    @property
    def platform(self) -> str:
        return str(self._data["platform"])

    @property
    def engine(self) -> str:
        """Container family, i.e. which loaders can read this release."""
        return str(self._data.get("engine") or "l45")

    @property
    def region(self) -> str:
        return str(self._data.get("region") or DEFAULT_REGION)

    @property
    def serial(self) -> str:
        """Catalogue number printed on the medium, when it has one."""
        return str(self._data.get("serial") or self.code)

    @property
    def media(self) -> dict[str, Any]:
        return dict(self._data.get("media") or {})

    @property
    def media_kind(self) -> str:
        """How the medium is read: iso-mode2, cue-multitrack, rom-flat, ..."""
        return str(self.media.get("kind") or "iso-mode2")

    @property
    def image(self) -> Path | None:
        """The source image, relative to the project root."""
        value = self.media.get("image")
        return _path(ROOT, str(value)) if value else None

    def _for_game(self, key: str, game: str | None) -> str | None:
        table = self.media.get(key) or {}
        if not table:
            return None
        if game is None:
            if len(table) != 1:
                raise SystemExit(
                    f"release {self.code} ships {len(table)} games; "
                    f"name one to resolve its {key}")
            game = next(iter(table))
        if game not in table:
            raise SystemExit(f"release {self.code} has no {key} for game {game}")
        return str(table[game])

    def game_root(self, game: str | None = None) -> str:
        """Directory holding a game's files on this medium (`/L5`, or `/`)."""
        return self._for_game("roots", game) or "/"

    def boot(self, game: str | None = None) -> str:
        """Boot executable path, for media that have one."""
        value = self._for_game("boot", game)
        if value is None:
            raise SystemExit(f"release {self.code} declares no boot executable")
        return value

    def media_path(self, name: str, game: str | None = None) -> str:
        """Full path of a game file on this medium (`SCEN.DAT` -> `/L5/SCEN.DAT`)."""
        root = self.game_root(game).rstrip("/")
        return f"{root}/{name}"

    @property
    def system_groups(self) -> list[int]:
        """File offset of each SYSTEM text group's offset table, in scan order.

        A pack names a string by which group it is in and where in that group,
        never by an address, so that one translation serves every build. This
        list is what turns that name into an address here. It is recorded
        rather than only scanned so a change in the scanner shows up as a
        mismatch instead of silently shifting what every key means.
        """
        return [int(str(v), 0) for v in (self._data.get("system_groups") or [])]

    @property
    def system_loose(self) -> list[int]:
        """Offsets of text runs outside the group tables, in scan order."""
        return [int(str(v), 0) for v in (self._data.get("system_loose") or [])]

    def check_system_groups(self, scanned: list[int]) -> None:
        """Fail if the scan disagrees with the recorded group offsets."""
        self._check_scan("group", self.system_groups, scanned)

    def check_system_loose(self, scanned: list[int]) -> None:
        """Fail if the scan disagrees with the recorded loose-run offsets.

        A build with none recorded says so with an empty list; a heuristic
        that starts finding runs in the gaps between groups then shows up as
        a mismatch instead of quietly adding strings nobody translated.
        """
        if "system_loose" not in self._data:
            return
        self._check_scan("loose-run", self.system_loose, scanned)

    def _check_scan(self, what: str, recorded: list[int], scanned: list[int]) -> None:
        if recorded == scanned or (not recorded and what == "group"):
            return
        raise SystemExit(
            f"release {self.code}: SYSTEM {what} scan disagrees with the "
            f"recorded offsets; every pack key would change meaning.\n"
            f"  recorded: {[f'0x{v:04X}' for v in recorded]}\n"
            f"  scanned:  {[f'0x{v:04X}' for v in scanned]}")

    def offset(self, name: str, default: int | None = None) -> int:
        """A named file offset of this build, parsed from its hex string."""
        raw = (self._data.get("offsets") or {}).get(name)
        if raw is None:
            if default is None:
                raise SystemExit(
                    f"release {self.code} declares no offset {name!r}")
            return default
        return int(str(raw), 0)

    @property
    def engine_pack(self):
        """The container family this release was built on."""
        from langrisser.engine import load_engine
        return load_engine(self.engine)

    @property
    def platform_pack(self):
        """The console this release runs on."""
        from langrisser.platform import load_platform
        return load_platform(self.platform)

    @property
    def group_config(self):
        """How to read this build's SYSTEM offset-table groups.

        The two things that vary: the console's byte order, and where this
        build's first text group sits. Everything else about the group model
        is shared (`langrisser.offsetgroups`).
        """
        from langrisser.offsetgroups import GroupConfig
        return GroupConfig(order=self.platform_pack.order,
                           scan_start=self.offset("system_scan_start"))

    @property
    def max_font_slot(self) -> int:
        """Highest glyph slot this build's font plane may hold.

        A build-level fact, not a console one: it is the plane's own layout
        that decides. Saturn Langrisser V caps at 1819 because slot 1820's
        bytes cross file offset 0x8000, where its SYSTEM.DAT keeps the group
        pointer directory (see docs/SATURN_DISC_FORMAT.md). A release that
        does not narrow it gets the engine's own ceiling.
        """
        value = self._data.get("max_font_slot")
        if value is not None:
            return int(value)
        return self.engine_pack.glyph.default_max_slot

    @property
    def scen_mapping(self) -> Path | None:
        return _path(self.root, self._data.get("scen_mapping"))

    @property
    def system_mapping(self) -> Path | None:
        return _path(self.root, self._data.get("system_mapping"))

    @property
    def kanji_map(self) -> Path | None:
        """Token->character map for this build's reordered kanji bank.

        Derived from record pairs matched against the already-mapped build; lets
        both token streams be normalized to text and compared directly.
        """
        return _path(self.root, self._data.get("kanji_map"))

    @property
    def unnamed_glyph_slots(self) -> dict[int, int]:
        """`pack slot -> this build's slot` for glyphs the font map leaves unnamed.

        A character needs no entry here: the game's font map says which slot
        holds it on the release the pack is keyed from, and this release's own
        map says which slot holds it here. An unnamed glyph - a bare icon - has
        no such handle, so the correspondence is recorded instead of derived.
        """
        return {int(key, 16): int(value, 16)
                for key, value in (self._data.get("unnamed_glyph_slots") or {}).items()}

    @property
    def verify(self) -> list[dict[str, Any]]:
        """Known-good dump fingerprints, one entry per verifiable part."""
        return [dict(item) for item in (self._data.get("verify") or [])]

    def verify_entry(self, role: str) -> dict[str, Any] | None:
        for item in self.verify:
            if item.get("role") == role:
                return item
        return None


def load_release(release: str,
                 release_root: str | Path = DEFAULT_RELEASE_ROOT) -> ReleasePack:
    root = Path(release_root)
    if not root.is_absolute():
        root = ROOT / root
    pack_root = root / release
    manifest = pack_root / "manifest.json"
    if not manifest.exists():
        raise SystemExit(f"release manifest not found: {manifest}")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    code = str(data.get("release") or release)
    return ReleasePack(code=code, root=pack_root, _data=data)


@lru_cache(maxsize=None)
def _all_releases(release_root: str) -> tuple[ReleasePack, ...]:
    root = Path(release_root)
    if not root.is_absolute():
        root = ROOT / root
    if not root.is_dir():
        return ()
    return tuple(load_release(child.name, root)
                 for child in sorted(root.iterdir())
                 if (child / "manifest.json").exists())


def releases_for(game: str,
                 platform: str | None = None,
                 region: str | None = None,
                 release_root: str | Path = DEFAULT_RELEASE_ROOT
                 ) -> list[ReleasePack]:
    """Every release shipping `game`, narrowed by platform and region."""
    found = [r for r in _all_releases(str(release_root)) if game in r.games]
    if platform:
        found = [r for r in found if r.platform == platform]
    if region:
        found = [r for r in found if r.region == region]
    return found


def find_release(game: str,
                 platform: str | None = None,
                 region: str | None = None,
                 release_root: str | Path = DEFAULT_RELEASE_ROOT) -> ReleasePack:
    """Resolve the single release a game+platform build targets.

    Ambiguity is an error rather than a guess: once a game ships twice on one
    console - a standalone print and a compilation - only the caller knows
    which one is meant, and it says so with `--release`.
    """
    found = releases_for(game, platform, region, release_root)
    if not found:
        where = f"{game}/{platform or 'any'}/{region or 'any'}"
        raise SystemExit(f"no release found for {where}")
    if len(found) > 1:
        names = ", ".join(r.code for r in found)
        raise SystemExit(
            f"{game} on {platform} has several releases ({names}); "
            "pick one with --release")
    return found[0]


def add_release_args(ap: argparse.ArgumentParser,
                     default: str | None = None) -> None:
    """Add `--release`.

    Tools that also take `--game` leave `default` unset and let the slug be
    resolved from game plus platform. A tool written against one particular
    build names it as the default instead, so its bare invocation keeps
    working while still being repointable at another release.
    """
    ap.add_argument("--release", default=default,
                    help="Release slug from data/releases/<slug>."
                         + ("" if default else " Defaults to the single "
                            "release matching --game and --platform."))
    ap.add_argument("--release-root", default="data/releases",
                    help="Directory containing release manifests.")


def release_from_args(args: argparse.Namespace,
                      platform: str | None = None) -> ReleasePack:
    """Load the release named by `--release`, or resolve it from game+platform."""
    root = getattr(args, "release_root", DEFAULT_RELEASE_ROOT)
    explicit = getattr(args, "release", None)
    if explicit:
        return load_release(explicit, root)
    game = getattr(args, "game", None)
    if not game:
        raise SystemExit("--release or --game is required")
    if platform is None:
        platform = getattr(args, "platform", None) or "ps1"
    return find_release(game, platform, getattr(args, "region", None), root)
