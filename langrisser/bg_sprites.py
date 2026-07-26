#!/usr/bin/env python3
"""Cutscene background sprite tool — extract / pack (lossless).

CONFIRMED: /L5/MAP_C.DAT has the **same container layout as /L5/IMG.DAT** —
a 0x800-byte TOC of sorted u32 asset offsets, and each asset stores its graphics
in the type-8 scanline-packet format (magic 0x0160, u16[3]==8, 2048-byte packets
= 0x20 header + 2016 px, width = u16[0x14]*2, block_rows = u16[0x16], 8 packets
per 0x4000 block). MAP_C holds 172 assets. So this tool drives the already-
verified codec in ``langrisser/imgdat.py`` (``parse_toc`` / ``image_groups`` /
``decode_image`` / ``encode_image`` / ``clut_palettes`` / ``pick_palette``) and
round-trips a chosen image group through an indexed PNG.

  list     enumerate assets, or the image groups inside one asset (--asset N)
  extract  one group  -> indexed PNG   (palette auto-picked from the group's
                                         OWN asset, or overridden by --palette)
  pack     indexed PNG -> that group   (in place, byte length unchanged)
  selftest decode->encode a group and assert the bytes are identical

Notes / facts (see docs/VIRASH_CUTSCENE_SUBTITLES.md):
* The palette MUST come from the same asset as the image: each asset bundles its
  own CLUT block(s) (typically 4 palette variants). Picking a palette globally,
  or reusing one VRAM CLUT for every image, gives wrong colours.
* The cutscene-background assets (MAP_C assets ~105..116) hold mostly **128-wide
  blocks built from 16 vertical strips of 8 px each** (a strip atlas — the strips
  are individually coherent but not in screen order), plus a few **fully coherent
  background frames** that decode directly. Assembling the full scrolling
  panorama still needs the runtime strip placement map; this tool is the lossless
  asset I/O and per-asset renderer underneath it.

Examples:
  python3 -m langrisser.bg_sprites list work/virash/MAP_C.DAT
  python3 -m langrisser.bg_sprites list work/virash/MAP_C.DAT --asset 108
  python3 -m langrisser.bg_sprites extract work/virash/MAP_C.DAT 0x19b8800 \
      work/virash/grp.png
  python3 -m langrisser.bg_sprites pack work/virash/grp.png \
      work/virash/MAP_C.DAT 0x19b8800
  python3 -m langrisser.bg_sprites selftest work/virash/MAP_C.DAT
"""
import argparse
import sys

import numpy as np
from PIL import Image, PngImagePlugin

from langrisser import imgdat as L


def _int(s):
    return int(s, 0)


def _palette_rgb(palette_path):
    """256x3 uint8 RGB from a raw 256xBGR555 file, or grayscale fallback."""
    try:
        clut = np.fromfile(palette_path, dtype="<u2")[:256]
    except (OSError, TypeError):
        return np.repeat(np.arange(256, dtype=np.uint8)[:, None], 3, 1)
    if clut.size < 256:
        clut = np.pad(clut, (0, 256 - clut.size))
    r = (clut & 0x1F) << 3
    g = ((clut >> 5) & 0x1F) << 3
    b = ((clut >> 10) & 0x1F) << 3
    return np.stack([r, g, b], -1).astype(np.uint8)


def _pal_to_rgb(pal):
    """A list[(r,g,b)] palette (from clut_palettes) -> 256x3 uint8 RGB."""
    arr = np.array(pal, dtype=np.uint8)
    if arr.shape[0] < 256:
        arr = np.vstack([arr, np.zeros((256 - arr.shape[0], 3), np.uint8)])
    return arr[:256]


def _asset_at(asset_file, offset):
    """Return (entry, asset_bytes, rel_offset) for the TOC asset holding offset."""
    for ent in L.parse_toc(asset_file):
        if ent.offset <= offset < ent.end:
            return ent, bytes(asset_file[ent.offset:ent.end]), offset - ent.offset
    return None, None, None


def _group_at(asset, offset):
    for g in L.image_groups(asset):
        start, cnt, w, br = g
        if start <= offset < start + cnt * L.PACKET_BYTES:
            return g
    return None


def cmd_list(args):
    data = open(args.mapc, "rb").read()
    entries = L.parse_toc(data)
    if args.asset is None:
        # enumerate the assets (the container's top level)
        for ent in entries:
            asset = bytes(data[ent.offset:ent.end])
            groups = [g for g in L.image_groups(asset)
                      if 0 < g[2] <= 4096 and g[3] > 0]
            pals = L.clut_palettes(asset)
            widths = sorted({g[2] for g in groups})
            print(f"asset{ent.index:3d}  {ent.offset:#09x}..{ent.end:#09x}  "
                  f"size={ent.size:#08x}  images={len(groups)}  "
                  f"palettes={len(pals)}  widths={widths}")
        print(f"{len(entries)} assets")
        return
    ent = entries[args.asset]
    asset = bytes(data[ent.offset:ent.end])
    pals = L.clut_palettes(asset)
    print(f"asset{ent.index} @ {ent.offset:#x} ({len(pals)} palettes):")
    for start, cnt, w, br in L.image_groups(asset):
        if w <= 0 or br <= 0 or w > 4096:
            continue
        height = (cnt // L.PACKETS_PER_BLOCK) * br
        # absolute offset is what extract/pack take
        print(f"  @{ent.offset + start:#09x}  (rel {start:#07x})  "
              f"packets={cnt:3d}  {w}x{height}")


def cmd_extract(args):
    data = open(args.mapc, "rb").read()
    ent, asset, rel = _asset_at(data, args.offset)
    if asset is None:
        sys.exit(f"offset {args.offset:#x} is not inside any TOC asset")
    g = _group_at(asset, rel)
    if g is None:
        sys.exit(f"no image group at {args.offset:#x} (use `list --asset {ent.index}`)")
    start, cnt, w, br = g
    rows = L.decode_image(asset, start, cnt, w, br)
    arr = np.array(rows, dtype=np.uint8)
    img = Image.fromarray(arr, mode="P")
    if args.palette:                       # explicit raw BGR555 override
        pal_rgb = _palette_rgb(args.palette)
        pal_src = args.palette
    else:                                   # auto: the asset's own CLUT(s)
        pal = L.pick_palette(arr.tobytes(), L.clut_palettes(asset))
        pal_rgb = _pal_to_rgb(pal) if pal else _palette_rgb(None)
        pal_src = f"asset{ent.index} CLUT" if pal else "grayscale"
    img.putpalette(pal_rgb.reshape(-1).tolist())
    meta = PngImagePlugin.PngInfo()
    # store the ABSOLUTE group offset so pack can find the same asset+group
    meta.add_text("mapc_group", f"{ent.offset + start:#x},{cnt},{w},{br}")
    img.save(args.out, pnginfo=meta)
    print(f"extracted asset{ent.index} group @{ent.offset + start:#x} "
          f"({w}x{arr.shape[0]}, {cnt} packets, palette={pal_src}) -> {args.out}")


def cmd_pack(args):
    data = bytearray(open(args.mapc, "rb").read())
    img = Image.open(args.png)
    if img.mode != "P":
        sys.exit("PNG must stay palette/indexed ('P'); do not flatten to RGB.")
    meta = img.text.get("mapc_group")
    if meta:
        abs_start, cnt, w, br = (int(x, 0) for x in meta.split(","))
    else:
        abs_start, cnt, w, br = args.offset, None, None, None
    ent, asset, rel = _asset_at(data, abs_start)
    if asset is None:
        sys.exit(f"group offset {abs_start:#x} is not inside any TOC asset")
    if cnt is None:                         # no embedded info: resolve from TOC
        g = _group_at(asset, rel)
        if g is None:
            sys.exit("no embedded group info and no group at that offset")
        start, cnt, w, br = g
    else:
        start = rel
    arr = np.asarray(img, dtype=np.uint8)
    if arr.shape[1] != w:
        sys.exit(f"PNG width {arr.shape[1]} != group width {w}")
    rows = [bytearray(arr[y].tobytes()) for y in range(arr.shape[0])]
    # re-encode only this asset's bytes, then splice them back in place
    new_asset = L.encode_image(asset, start, cnt, w, br, rows)
    if len(new_asset) != len(asset):
        sys.exit("asset size changed!")
    size_before = len(data)
    data[ent.offset:ent.end] = new_asset
    assert len(data) == size_before, "file size changed!"
    with open(args.mapc, "wb") as f:
        f.write(data)
    print(f"packed {args.png} -> {args.mapc} asset{ent.index} group "
          f"@{abs_start:#x} (size unchanged: {size_before})")


def cmd_selftest(args):
    data = open(args.mapc, "rb").read()
    entries = L.parse_toc(data)
    ok_all = True
    tested = 0
    for ent in entries:
        asset = bytes(data[ent.offset:ent.end])
        g = next(((s, c, w, b) for (s, c, w, b) in L.image_groups(asset)
                  if 0 < w <= 4096 and b > 0), None)
        if g is None:
            continue
        start, cnt, w, br = g
        rows = L.decode_image(asset, start, cnt, w, br)
        # mirror pack: re-encode the asset, splice it back, expect no change
        new_asset = L.encode_image(asset, start, cnt, w, br, rows)
        spliced = bytearray(data)
        spliced[ent.offset:ent.end] = new_asset
        ok = bytes(spliced) == data
        ok_all &= ok
        tested += 1
        if not ok:
            print(f"asset{ent.index} group @{ent.offset + start:#x} "
                  f"{w}x{(cnt//L.PACKETS_PER_BLOCK)*br}: FAILED")
    print(f"{tested} assets round-tripped: "
          + ("OK (every decode->encode->splice is byte-identical)"
             if ok_all else "FAILED"))
    sys.exit(0 if ok_all else 1)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="enumerate assets, or one asset's groups")
    p.add_argument("mapc")
    p.add_argument("--asset", type=int, default=None,
                   help="show the image groups inside this asset index")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("extract", help="group -> indexed PNG (auto palette)")
    p.add_argument("mapc")
    p.add_argument("offset", type=_int, help="absolute offset of/within the group")
    p.add_argument("out")
    p.add_argument("--palette", default=None,
                   help="raw 256xBGR555 file to override the asset's own CLUT")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("pack", help="indexed PNG -> group (in place)")
    p.add_argument("png")
    p.add_argument("mapc")
    p.add_argument("offset", type=_int, nargs="?", default=0)
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("selftest", help="decode->encode round-trip check")
    p.add_argument("mapc")
    p.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
