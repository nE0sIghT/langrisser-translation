# IMG.DAT Format

`/L5/IMG.DAT` is a fixed-size image container used by Langrisser V. It is not
part of the text pipeline and must be modified in-place: the container file
size and every replaced asset size must remain unchanged.

Durable tooling:

```bash
python3 -m langrisser.imgdat list work/l5/extracted/IMG.DAT
python3 -m langrisser.imgdat inspect work/l5/extracted/IMG.DAT 10
python3 -m langrisser.imgdat extract work/l5/extracted/IMG.DAT 10 work/title_asset10.bin
python3 -m langrisser.imgdat replace work/l5/extracted/IMG.DAT 10 work/title_asset10.bin work/IMG_roundtrip.DAT
python3 -m langrisser.imgdat title-credits work/l5/extracted/IMG.DAT \
  --out-imgdat work/l5/build/IMG.DAT.en \
  --version 1 \
  --out-display work/l5/build/title_credits_display.png
```

## Container

The first `0x800` bytes are a little-endian `u32` offset table.

```c
struct ImgDat {
    uint32_t offsets[];  // ascending asset offsets, terminated by file end
    uint8_t  assets[];
};
```

Rules verified on the current disc image:

- `offsets[0] == 0x800`.
- The table contains 17 offsets: 16 asset starts plus one terminal end offset.
- The terminal end offset is `0x315800`, equal to the `IMG.DAT` file size.
- Asset `i` is stored at `[offsets[i], offsets[i + 1])`.
- Asset replacement is valid only when the replacement byte length exactly
  matches the original asset length.

Current asset table:

| Index | Offset | End | Size |
| ---: | ---: | ---: | ---: |
| 0 | `0x000800` | `0x033800` | 208,896 |
| 1 | `0x033800` | `0x03f800` | 49,152 |
| 2 | `0x03f800` | `0x04b800` | 49,152 |
| 3 | `0x04b800` | `0x057800` | 49,152 |
| 4 | `0x057800` | `0x063800` | 49,152 |
| 5 | `0x063800` | `0x06f800` | 49,152 |
| 6 | `0x06f800` | `0x07b800` | 49,152 |
| 7 | `0x07b800` | `0x092000` | 92,160 |
| 8 | `0x092000` | `0x0d7000` | 282,624 |
| 9 | `0x0d7000` | `0x0e6000` | 61,440 |
| 10 | `0x0e6000` | `0x11c800` | 223,232 |
| 11 | `0x11c800` | `0x153000` | 223,232 |
| 12 | `0x153000` | `0x1f1000` | 647,168 |
| 13 | `0x1f1000` | `0x203800` | 75,776 |
| 14 | `0x203800` | `0x29b000` | 620,544 |
| 15 | `0x29b000` | `0x315800` | 501,760 |

## Asset Headers

Each asset starts with a custom header. These headers are not standard TIM
headers.

Known header observations:

- `u16[0] == 0x0160` for all listed assets.
- `u16[3]` varies and is likely a mode/depth field.
- `u16[8]` is a width candidate. For asset 10 it is `0x0280` (640), matching
  the decoded title bitmap width.
- `u16[13]` varies with `u16[3]` and is likely another mode/depth field.
- Asset 10 has a 256-entry RGB555 CLUT at relative offset `0x36220`.

Header field meanings are not fully decoded. `inspect` dumps the first header
words for comparison when adding new image profiles.

Asset 10 first words:

```text
u16: 0160 0000 0000 0008 0001 0000 1f40 0000
     0280 0100 00a0 0032 0000 0800 0000 0000
u32: 00000160 00080000 00000001 00001f40
     01000280 003200a0 08000000 00000000
```

## Palettes

Known palettes use PlayStation RGB555 words:

```text
bit  0..4   red
bit  5..9   green
bit 10..14  blue
bit 15      STP/semitransparency flag
```

The PNG exporter converts the 5-bit color channels to 8-bit RGB and ignores
the STP bit for preview rendering.

Title-screen palette:

| Field | Value |
| --- | --- |
| Asset-relative CLUT offset | `0x36220` |
| Absolute `IMG.DAT` CLUT offset | `0x11c220` |
| Entries | 256 |
| Size | 512 bytes |
| Runtime VRAM location observed in title dump | `(x=0, y=501)` |

## Indexed Gap-Bitmap Payload

At least one image payload uses an 8bpp indexed bitmap stored as logical rows
with physical gaps. The layout is generic; asset 10 is the first verified
profile.

Definitions:

- Logical row: `width` indexed pixels consumed by the image renderer.
- Physical row: bytes stored in the asset for one logical row.
- Gap: bytes inside a physical row that are not logical image pixels and must
  be preserved when encoding.
- Block: fixed group of logical rows with fixed physical byte size.

Verified layout parameters for the known profile:

```text
width       = 640 logical pixels
block_rows  = 25
block_bytes = 0x4000
gap_bytes   = 32
```

For each 25-row block, row positions below contain a 32-byte gap:

| Row position in block | Logical pixels before gap | Physical row bytes |
| ---: | ---: | ---: |
| 3 | 96 | 672 |
| 6 | 192 | 672 |
| 9 | 288 | 672 |
| 12 | 384 | 672 |
| 15 | 480 | 672 |
| 18 | 576 | 672 |
| 22 | 32 | 672 |

All other row positions are 640 physical bytes. A full 25-row block stores
`18 * 640 + 7 * 672 = 16224` row bytes plus `160` trailing padding bytes,
for a total of `0x4000` bytes.

Logical-to-physical mapping for a row:

```text
block_index = logical_y / 25
row_pos     = logical_y % 25
row_base    = bitmap_base + block_index * 0x4000 + sum(physical_row_len[0..row_pos-1])

if row_pos has no gap:
    physical_offset(logical_x) = row_base + logical_x
else:
    gap_x = gap_after_x[row_pos]
    physical_offset(logical_x) = row_base + logical_x                 if logical_x < gap_x
                                 row_base + logical_x + 32            otherwise
```

Encoding must write only logical pixels and preserve the 32-byte gap contents
and block padding bytes.

## Known Image Profiles: Title Screens

Profile names in `langrisser/imgdat.py`: `title10` and `title11`. These
are the two title screens seen in-game; they alternate around the opening
movie/title loop. They share the same bitmap layout and credit coordinates.

| Field | Value |
| --- | --- |
| Asset indices | 10 and 11 |
| Asset offsets in `IMG.DAT` | `0x0e6000`, `0x11c800` |
| Asset size | 223,232 bytes |
| Bitmap relative offset inside asset | `0x12020` |
| Bitmap absolute offsets in `IMG.DAT` | `0x0f8020`, `0x12e820` |
| Logical bitmap size | 640 x 225 |
| Source row range represented by profile | 0..224 |
| Background index | `0xfe` |
| Main cyan text index | `0x2c` |
| Palette relative offset inside asset | `0x36220` |
| Palette absolute offset in `IMG.DAT` | `0x11c220` |
| Display framebuffer offset | `(x=0, y=8)` |
| Display framebuffer size | 640 x 240 |
| Display preview aspect | 640 x 480 |

Decode commands:

```bash
python3 -m langrisser.imgdat decode-gap-bitmap work/l5/extracted/IMG.DAT \
  --profile title10 \
  --out work/title_credits/title10_decoded.png
python3 -m langrisser.imgdat decode-gap-bitmap work/l5/extracted/IMG.DAT \
  --profile title11 \
  --out work/title_credits/title11_decoded.png
```

The output is RGB by default. Pass `--index-debug` only when a diagnostic
index-colored rendering is needed.

Production title-credit patch command:

```bash
python3 -m langrisser.imgdat title-credits work/l5/extracted/IMG.DAT \
  --out-imgdat work/l5/build/IMG.DAT.en \
  --version 1 \
  --out-raw-preview work/l5/build/title_credits_raw.png \
  --out-display work/l5/build/title_credits_display.png \
  --out-crop work/l5/build/title_credits_crop.png
```

The command writes an edited same-size copy of `IMG.DAT`, patching both
title profiles. It decodes the edited container again and writes PNG previews
from that decoded data. It writes both the raw `640x225` asset preview and a
display-aspect `640x480` preview for `title10`; matching `*_title11.png`
previews are written for `title11`. It does not render over an emulator
screenshot.

The display-aspect preview inserts the raw bitmap into a `640x240` frame at
`y=8` and vertically doubles the frame. The raw asset remains `640x225`; only
the preview accounts for the way the high-resolution PlayStation frame is
perceived on screen.

### Release Credits and QR

`langrisser/build_ppf.py` calls `langrisser/imgdat.py title-credits` as part of the
standard patch build and injects the resulting `/L5/IMG.DAT`.

The release text is generated from:

- `--version` / `langrisser/build_ppf.py --patch-version` (`1` by default);
- `git rev-parse --short=8 HEAD` unless `--commit-hash` is passed.

Rendered text:

```text
Translation v<version> (<commit>) by Yuri "nE0sIghT" Konotopov
Thanks to CyberWarriorX for the Langrisser III toolkit
Thanks to borgor for the Langrisser V translation guide
```

The renderer uses the bundled `data/fonts/LiberationSansNarrow-Bold.ttf`
font (GPL-2 with font exception; copyright notice is stored next to the font).
Text is rendered with FreeType supersampling to an alpha mask, downsampled to
the raw title bitmap height, and alpha-mapped to existing cyan-ish palette
entries. The text is pasted transparently: source title pixels are preserved
where the alpha mask is empty.

The QR code points to:

```text
https://github.com/nE0sIghT/langrisser-5-translation
```

The default QR matrix is embedded in the script, so the build does not need a
QR generation package. A non-default `--qr-url` requires Python's optional
`qrcode` package. The raw QR module size is `2x1` pixels; after the title
preview's vertical x2 display scaling each module appears as a square `2x2`
screen block.

## Type-8 Scanline-Packet Images

The editable images currently supported by `langrisser/imgdat.py` are
`u16[3] == 8` indexed scanline packets. A type-8 packet is fixed-size `0x800`
bytes (2048): a `0x20`-byte header followed by `0x7E0` (2016) bytes of 8bpp
pixel data. The gap-bitmap "32-byte gap" documented above is exactly this
`0x20` per-scanline header seen from the pixel stream's point of view
(`2016 + 32 = 2048`).

Other blocks in the same assets can also start with magic `0x0160`, but are
not decoded by the type-8 image codec unless they have `u16[3] == 8` and
`u16[13] == 0x800`.

Packet header (16-bit little-endian words; offsets relative to the packet):

| Word | Byte | Meaning |
| ---: | ---: | --- |
| `u16[0]` | `0x00` | magic `0x0160` |
| `u16[3]` | `0x06` | block type/depth (`8` = supported 8bpp indexed image scanline) |
| `u16[10]` | `0x14` | image width in 16-bit VRAM words; **width_px = u16[10] * 2** |
| `u16[11]` | `0x16` | block_rows (rows of `width_px` that fill one `0x4000` block) |
| `u16[13]` | `0x1a` | packet stride, `0x800` (2048) for decoded type-8 images |

An **image** is a run of consecutive type-8 packets that share the same width.
A single asset can hold several images back to back (e.g. a menu background
followed by the full-size graphic). The runs are separated by non-image blocks
that can hold the 256-entry RGB555 CLUT for the preceding image(s). To decode an
image:
concatenate the `0x7E0` data bodies of its packets, then reshape the result to
`width_px` (each packet body spans ~2.6 logical rows, so rows wrap across
packets — this is why the gap-bitmap gap lands at a different `x` on each row).

Observed decoded type-8 image inventory (width x height), from
`langrisser/imgdat.py dump-all` and no-edit encode round-trip checks:

| Asset | Images |
| ---: | --- |
| 0 | 112x144, 168x96, 112x144, 168x96, 112x144, 168x96 |
| 1-6 | no decoded type-8 image groups; these assets use unsupported type-7 blocks |
| 7 | 3x 184x87 |
| 8 | 3x 368x215 (character portraits - e.g. the blue-haired shop maid) |
| 9 | 3x 224x72 |
| 10, 11 | 320x200 start/load/config menu background + 640x225 title screen |
| 12 | **768x256 prologue poem** (decodes as 768x252 + a 4-row `type=2` tail; 3 stacked 256x256 scroll columns) + 960x256 + 640x225 (left = menu bg) + 224x72 |
| 13 | 320x200 |
| 14 | 1224x182 + 1024x255 ending staff-credits + 320x200 |
| 15 | 416x494 CAST credits + 320x450 + 320x200 + 224x72 + 88x183 |

The asset 10/11/13 `320x200` blocks (type 8, VRAM `(640, 256)`) are the menu
background, not previews, but decode as noise as plain 8bpp - the same artwork
appears coherent as the left half of asset 12's `640x225` image, so the
`(640, 256)` copy uses a different packing. The prologue poem's canonical frame
is palette index 1 (`0x9ca20`, dark-red text); the colourful-frame picker in
`dump-all` may choose a lighter highlight frame instead.

The opening prologue poem (the scrolling "wall of text" on the title attract
loop) is **asset 12, image 0**. The decoded `type=8` group is 768x252, but the
real image is **768x256**: the bottom 4 rows live in the `type=2` remainder
block right after it (VRAM `(0, 508)`, contiguous with the main image at VRAM Y
256-507). 256 rows do not divide evenly into the 21-rows-per-`0x4000`-block
packing (`256 = 12*21 + 4`), so the packer emits 12 full blocks plus a 4-row
`type=2` tail. Both runs are the **same image** and both must be rewritten.

In game that 768x256 image is **three 256x256 columns** stacked top-to-bottom
into one continuous vertical scroll (column 0, then 1, then 2 = a 768px-tall
strip). A line of text may straddle a column boundary (strip rows 256 / 512):
its bottom sliver is the column's last rows, which - being rows 252-255 of the
768-wide image - are exactly the `type=2` remainder block. Blanking that block
(rather than rewriting it) drops those slivers and leaves a black seam between
"screens". `langrisser/poem_render.py` renders the whole 768px strip on one
uniform line pitch; `langrisser/poem_translate.py` slices it back into the
three columns and writes the main image **and** the remainder block. Translating
it is a graphics edit: redraw the text into the indexed bitmap and re-pack it
into the scanline packets, leaving every `0x20` packet header untouched.

`block_rows` and the per-row gap positions are fully determined by the width
through the scanline rule, so `langrisser/imgdat.py` derives them with
`scanline_gaps(width, block_rows)` instead of hand-listing them per profile.

### Block types and palettes

Every block (packet) header word `u16[3]` is a **type**, and `u16[8]`/`u16[9]`
are the block's VRAM destination X/Y. Observed types:

| `u16[3]` | stride `u16[13]` | meaning |
| ---: | ---: | --- |
| 8 | 2048 | 8bpp indexed image scanline (the images above) |
| 7 | 1792 | Unsupported indexed/sprite-like blocks used by assets 1-6 |
| 4 | 1024 | 4bpp indexed block |
| 1 / 2 / 3 | — | **CLUT block** when the width word `u16[10]` is 256 (`u16[11]` = palette count) |
| 2 | 2048 | **image remainder**: a short `type=8`-style tail whose width word matches the preceding image (e.g. the prologue poem's last 4 rows), not a CLUT |

A **CLUT block** is the palette store: its `0x20` header (width word 256) is
followed by `u16[11]` consecutive 256-entry RGB555 palettes, normally uploaded
to VRAM `(0, 500)`. When a CLUT block carries more than one palette they are
**animation frames** that share one image - the title flame (asset 10/11: two
palettes at `0x36020` / `0x36220`) and the prologue poem's line highlight
(asset 12: four palettes at `0x9c820`+`k*0x200`). `clut_palettes()` collects
them all and `dump-all` renders each image with its most colourful frame.
`title10/11` keep their hand-set `palette_rel_offset` (`0x36220`) for the
byte-exact credits build.

## Unknowns

- A few header words are still unlabelled (`u16[6]` ~0x1f00 range, likely a
  VRAM/CLUT reference; the exact per-image height field).
- Assets 1-6 use `0x0160` headers with `u16[3] == 7` and stride `0x700`; this
  layout is not decoded yet.
- The small `type=8` blocks uploaded to VRAM `(640, 256)` (the asset 10/11/13
  "previews") still decode as noise as plain 8bpp - probably a different pixel
  packing or a scratch/effect buffer rather than a displayable image.
- `type=4` (4bpp) blocks are detected but not yet decoded.
- Importing edited indexed PNGs back into arbitrary `IMG.DAT` images.
