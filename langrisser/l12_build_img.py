#!/usr/bin/env python3
"""Put the target language's redrawn screens into a copy of `IMG.DAT`.

Some Langrisser I & II screens are painted rather than typeset — the title and
load menu is one bitmap with `スタート` / `ロード` drawn into it — so they are
translated by redrawing the asset, not by editing a string.

A pack supplies one paletted PNG per asset it redraws, named by asset index,
under `<pack>/IMG/`. Pixel values are the asset's own 4bpp indices, so
the palette travels with the original and a redraw cannot invent colours the
hardware has not been given. Everything else — the bitstream codec, the
offset-table archive — is `imgdat`'s, shared with Langrisser V.

The archive is rebuilt rather than patched in place, so a redraw is free to
compress to a different length; it only has to leave the file no larger than
the original, which is what the ISO extent holds.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image

from langrisser import imgdat
from langrisser.game import add_game_args, game_from_args
from langrisser.project import add_language_args, language_from_args

ASSET_NAME = re.compile(r"^(\d+)")


def pack_overrides(directory: Path) -> dict[int, Path]:
    """Asset index -> PNG, for every `<index>*.png` in the pack directory."""
    found: dict[int, Path] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.png")):
        match = ASSET_NAME.match(path.stem)
        if not match:
            raise SystemExit(f"{path}: file name must start with the asset index")
        index = int(match.group(1))
        if index in found:
            raise SystemExit(f"asset {index} is redrawn twice: {found[index]}, {path}")
        found[index] = path
    return found


def redraw(data: bytes, overrides: dict[int, Path]) -> dict[int, bytes]:
    """Compressed payloads for the overridden assets."""
    payloads: dict[int, bytes] = {}
    for index, path in sorted(overrides.items()):
        _, original = imgdat.get_asset(data, index)
        expanded = imgdat.lz_decompress(original)
        decoded = imgdat.lz_bitmap(expanded)
        if decoded is None:
            raise SystemExit(f"{path}: asset {index} is not a 4bpp bitmap")
        width, height, _, _ = decoded
        with Image.open(path) as img:
            pixels = imgdat.lz_bitmap_pixels(img, width, height)
        payloads[index] = imgdat.lz_compress(imgdat.lz_replace_pixels(expanded, pixels))
    return payloads


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_language_args(ap)
    add_game_args(ap, default="l1")
    ap.add_argument("--img-dat", default=None, help="Original archive.")
    ap.add_argument("--out-img-dat", required=True)
    args = ap.parse_args()

    game = game_from_args(args)
    lang = language_from_args(args)
    src = Path(args.img_dat) if args.img_dat else Path(
        "work", game.code, "extracted", "IMG.DAT")
    data = src.read_bytes()

    overrides = pack_overrides(lang.image_dir)
    payloads = redraw(data, overrides)
    out = imgdat.rebuild_img_within(data, payloads, len(data))
    if len(out) > len(data):
        raise SystemExit(
            f"rebuilt archive is {len(out)} bytes against the original "
            f"{len(data)}: it would not fit the disc")

    Path(args.out_img_dat).write_bytes(out)
    print(f"{game.code}: {len(overrides)} assets redrawn, "
          f"{len(data)} -> {len(out)} bytes -> {args.out_img_dat}")


if __name__ == "__main__":
    main()
