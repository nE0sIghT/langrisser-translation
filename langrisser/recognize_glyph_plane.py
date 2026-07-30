#!/usr/bin/env python3
"""Read a glyph plane by recognising it inside the game's own text.

A glyph plane is a pile of 12x12 tiles with no character codes attached, and a
tile on its own is a bad thing to hand a recogniser: at this size ア and 了, エ
and 工, カ and 力 are the same drawing, so a per-tile pass answers with the
wrong script about as often as the right one. What disambiguates them is the
same thing that disambiguates them for a reader - the word they sit in.

So this renders the game's own strings back as lines of tiles, recognises the
line, and lets every position vote for its slot. A slot named the same way by
several unrelated words is confirmed by agreement, not by one guess.

Slots whose character is already known are passed in as seeds and never
recognised; they still get rendered, because their presence in a line is what
makes the rest of the line readable.

Output is the shared map convention: `index_dec,index_hex,group,char,source`,
with `group` reading `confirmed` or `unconfirmed`.
"""
from __future__ import annotations

import argparse
import csv
import multiprocessing as mp
import os
import struct
import subprocess
import tempfile
import warnings
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

_WORKER: dict = {}

warnings.filterwarnings("ignore")
os.environ.setdefault("GLOG_minloglevel", "3")

GLYPH_W = GLYPH_H = 12
GLYPH_BYTES = 18


def load_plane(path: Path) -> list[np.ndarray]:
    blob = path.read_bytes()
    out = []
    for i in range(len(blob) // GLYPH_BYTES):
        v = int.from_bytes(blob[i * GLYPH_BYTES:(i + 1) * GLYPH_BYTES], "big")
        out.append(np.array(
            [[(v >> (143 - (y * GLYPH_W + x))) & 1 for x in range(GLYPH_W)]
             for y in range(GLYPH_H)], np.uint8))
    return out


def render_line(tiles: list[np.ndarray], scale: int = 6,
                xbrz_factor: int = 6, filters: str = "",
                pad: int = 6) -> np.ndarray:
    """A row of tiles, enlarged for the recogniser.

    How the enlargement reconstructs the strokes decides most of the result,
    and only the pixel-art algorithms help. Measured against the known kana
    block: nearest reads 53 of 84, hqx 63, xbr 64, xBRZ 73, and xBRZ into xbr
    75, while plain interpolation gains nothing (bicubic 51) and blurring the
    enlarged strip makes it worse - 36 at radius 1.5, 12 at radius 5.
    """
    import xbrz

    strip = np.concatenate(tiles, axis=1)
    img = Image.fromarray(255 - strip * 255).convert("RGBA")
    if xbrz_factor > 1:
        img = xbrz.scale_pillow(img, xbrz_factor)
    img = img.convert("RGB")
    if filters:
        with tempfile.TemporaryDirectory(prefix="glyph_line_") as td:
            src, dst = Path(td) / "in.png", Path(td) / "out.png"
            img.save(src)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                            "-vf", filters, str(dst)], check=True)
            img = Image.open(dst).convert("L")
    else:
        img = img.convert("L")
    want = strip.shape[1] * scale
    if img.width < want:
        factor = max(1, want // img.width)
        img = img.resize((img.width * factor, img.height * factor), Image.NEAREST)
    canvas = Image.new("L", (img.width + 2 * pad, img.height + 2 * pad), 255)
    canvas.paste(img, (pad, pad))
    return np.array(canvas.convert("RGB"))


def _render_worker(job):
    """Render one line in a pool worker; tiles are shared once via an initializer."""
    window, scale, factor, filters = job
    return render_line([_WORKER["tiles"][s] for s in window], scale, factor, filters)


def _init_worker(tiles):
    _WORKER["tiles"] = tiles


# --- the L1&2 script container, only as far as reading its text needs ---

def catalog(blob: bytes) -> list[tuple[int, int]]:
    w = list(struct.unpack_from("<512I", blob, 0))
    k = 1
    while k < 512 and w[k] > w[k - 1] and w[k] <= len(blob):
        k += 1
    return [(w[i], w[i + 1]) for i in range(k - 1)]


def offset_table(seg: bytes) -> list[int] | None:
    """A self-describing u32 table: its first entry is its own byte size."""
    if len(seg) < 4:
        return None
    first = struct.unpack_from("<I", seg, 0)[0]
    if first < 8 or first % 4 or first > len(seg):
        return None
    t = list(struct.unpack_from(f"<{first // 4}I", seg, 0))
    if t[0] != first or any(t[i] > t[i + 1] for i in range(len(t) - 1)):
        return None
    return t if t[-1] <= len(seg) else None


def decode_string(part: bytes, at: int) -> tuple[list[int], int]:
    """Slots of the string starting at `at`, and where the next one starts."""
    slots: list[int] = []
    i = at
    while i < len(part):
        v = part[i]
        if v == 0:
            return slots, i + 1
        if v < 0x0A:
            i += 1
        elif v <= 0xF6:
            slots.append(v - 0x0A)
            i += 1
        elif v <= 0xFB:
            if i + 1 >= len(part):
                break
            slots.append(237 + (v - 0xF7) * 256 + part[i + 1])
            i += 2
        else:
            i += 2
    return slots, len(part)


def script_strings(paths: list[Path]) -> list[tuple[int, ...]]:
    seen: set[tuple[int, ...]] = set()
    out: list[tuple[int, ...]] = []
    for path in paths:
        blob = path.read_bytes()
        for start, end in catalog(blob):
            chunk = blob[start:end]
            sections = offset_table(chunk)
            if not sections or len(sections) < 4:
                continue
            text = chunk[sections[2]:sections[3]]
            parts = offset_table(text)
            if not parts:
                continue
            for a, b in zip(parts, parts[1:] + [len(text)]):
                part, i = text[a:b], 0
                while i < len(part):
                    slots, i = decode_string(part, i)
                    key = tuple(slots)
                    if len(slots) >= 2 and key not in seen:
                        seen.add(key)
                        out.append(key)
    return out


def load_seeds(path: Path | None) -> dict[int, str]:
    if path is None:
        return {}
    return {int(r["index_dec"]): r["char"]
            for r in csv.DictReader(path.open(encoding="utf-8")) if r.get("char")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plane", required=True, help="raw glyph plane (FONT.DAT)")
    ap.add_argument("--scen", action="append", default=[], required=True,
                    help="script container to take context from; repeatable")
    ap.add_argument("--seeds", default=None,
                    help="CSV of slots already known, in map convention")
    ap.add_argument("--out", required=True)
    ap.add_argument("--votes", type=int, default=6,
                    help="stop feeding a slot once this many lines have named it")
    ap.add_argument("--max-line", type=int, default=14, help="glyphs per rendered line")
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8,
                    help="processes rendering lines while the model runs")
    ap.add_argument("--max-lines", type=int, default=0,
                    help="stop after this many lines; 0 means all")
    ap.add_argument("--device", default=None,
                    help="paddle device, e.g. gpu or cpu; default is paddle's own")
    ap.add_argument("--scale", type=int, default=6, help="pixel scale of a rendered line")
    ap.add_argument("--xbrz", type=int, default=6,
                    help="xBRZ magnification; 1 disables it")
    ap.add_argument("--filters", default="",
                    help="ffmpeg filter chain applied after xBRZ. It costs a "
                         "subprocess per line, so it is off by default: xbr=2 "
                         "on top of xBRZ reads 75 of 84 on the known kana "
                         "against 73 without, which does not pay for thousands "
                         "of process spawns.")
    ap.add_argument("--model", default="PP-OCRv5_server_rec")
    args = ap.parse_args()

    tiles = load_plane(Path(args.plane))
    seeds = load_seeds(Path(args.seeds) if args.seeds else None)
    strings = script_strings([Path(p) for p in args.scen])
    print(f"plane {len(tiles)} slots, {len(seeds)} seeded, "
          f"{len(strings)} distinct strings")

    # Longest first: a long line carries more context and covers more slots.
    strings.sort(key=len, reverse=True)
    wanted = {i for i in range(len(tiles)) if i not in seeds}
    votes: dict[int, Counter] = defaultdict(Counter)
    lines: list[tuple[list[int], np.ndarray]] = []
    for slots in strings:
        if not any(s in wanted and sum(votes[s].values()) < args.votes for s in slots):
            continue
        for k in range(0, len(slots), args.max_line):
            window = [s for s in slots[k:k + args.max_line] if s < len(tiles)]
            if len(window) < 2:
                continue
            lines.append(window)
            for s in window:
                if s in wanted:
                    votes[s][""] += 0
        for s in slots:
            if s in wanted:
                votes[s]["#lines"] = votes[s].get("#lines", 0)
    print(f"{len(lines)} lines to recognise", flush=True)

    if args.max_lines:
        lines = lines[:args.max_lines]
        print(f"limited to {len(lines)} lines", flush=True)

    from paddleocr import TextRecognition
    model = TextRecognition(model_name=args.model,
                            **({"device": args.device} if args.device else {}))

    tally: dict[int, Counter] = defaultdict(Counter)
    aligned = dropped = 0
    pool = mp.Pool(args.jobs, initializer=_init_worker, initargs=(tiles,))
    jobs = ((w, args.scale, args.xbrz, args.filters) for w in lines)
    rendered = pool.imap(_render_worker, jobs, chunksize=16)
    for start in range(0, len(lines), args.batch):
        windows = lines[start:start + args.batch]
        images = [next(rendered) for _ in windows]
        results = model.predict(images)
        for window, res in zip(windows, results):
            text = (res.get("rec_text") or "") if isinstance(res, dict) else ""
            if len(text) != len(window):
                dropped += 1
                continue
            aligned += 1
            for slot, ch in zip(window, text):
                tally[slot][ch] += 1
        if start % (args.batch * 20) == 0:
            print(f"  {start + len(windows)}/{len(lines)} lines, "
                  f"{aligned} aligned, {dropped} dropped", flush=True)
    pool.close()
    pool.join()
    print(f"recognised {aligned} lines, dropped {dropped} on length mismatch")

    rows = []
    confirmed = 0
    for i in range(len(tiles)):
        if i in seeds:
            rows.append((i, seeds[i], "confirmed", "seed"))
            confirmed += 1
            continue
        counts = tally.get(i)
        if not counts:
            rows.append((i, "", "unconfirmed", "no-context"))
            continue
        ch, n = counts.most_common(1)[0]
        total = sum(counts.values())
        share = n / total
        group = "confirmed" if total >= 3 and share >= 0.8 else "unconfirmed"
        confirmed += group == "confirmed"
        rows.append((i, ch, group, f"paddle:{share:.2f}:{total}"))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["index_dec", "index_hex", "group", "char", "source"])
        for i, ch, group, source in rows:
            writer.writerow([i, f"{i:x}", group, ch, source])
    print(f"{confirmed}/{len(tiles)} slots confirmed -> {out}")


if __name__ == "__main__":
    main()
