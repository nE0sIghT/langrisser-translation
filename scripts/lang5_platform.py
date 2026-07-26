#!/usr/bin/env python3
"""Platform manifest helpers.

Language packs are target-language data. Platform manifests describe console
layout differences and mapping data without storing extracted source text.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lang5_project import ROOT

DEFAULT_PLATFORM_ROOT = ROOT / "data" / "platforms"


def _path(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else base / p


@dataclass(frozen=True)
class PlatformPack:
    code: str
    root: Path
    _data: dict[str, Any]

    @property
    def label(self) -> str:
        return str(self._data.get("label") or self.code)

    @property
    def scen_mapping(self) -> Path | None:
        return _path(self.root, self._data.get("scen_mapping"))

    @property
    def system_mapping(self) -> Path | None:
        return _path(self.root, self._data.get("system_mapping"))

    @property
    def base_platform(self) -> str:
        return str(self._data.get("base_platform") or self.code)

    @property
    def kanji_map(self) -> Path | None:
        """Token->character map for the platform's reordered kanji bank.

        Derived by `saturn_scen_audit.py` from positionally-matched record
        pairs; lets both consoles' token streams be normalized to text and
        compared directly.
        """
        return _path(self.root, self._data.get("kanji_map"))


def load_platform(platform: str, platform_root: str | Path = DEFAULT_PLATFORM_ROOT) -> PlatformPack:
    root = Path(platform_root)
    if not root.is_absolute():
        root = ROOT / root
    pack_root = root / platform
    manifest = pack_root / "manifest.json"
    if not manifest.exists():
        raise SystemExit(f"platform manifest not found: {manifest}")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    code = str(data.get("platform") or platform)
    return PlatformPack(code=code, root=pack_root, _data=data)


def add_platform_args(ap: argparse.ArgumentParser, default: str) -> None:
    ap.add_argument("--platform", default=default,
                    help="Source platform code from data/platforms/<code>.")
    ap.add_argument("--platform-root", default="data/platforms",
                    help="Directory containing platform manifests.")


def platform_from_args(args: argparse.Namespace) -> PlatformPack:
    return load_platform(args.platform, args.platform_root)
