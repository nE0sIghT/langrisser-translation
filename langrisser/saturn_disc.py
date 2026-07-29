#!/usr/bin/env python3
"""Sega Saturn BIN/CUE helpers for Langrisser V.

The PS1 tooling in this repository assumes one MODE2/2352 data track with
2048-byte user sectors at raw offset 24. The Saturn release is a mixed-mode
disc: track 1 is MODE1/2352 ISO9660, track 2 is a separate MODE2 XA/ADPCM area,
and later tracks are CD audio.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from dataclasses import dataclass, asdict
from math import ceil
from pathlib import Path
from typing import Iterator

from langrisser.release import load_release


SECTOR_RAW_SIZE = 2352
MODE1_USER_OFFSET = 16
MODE1_USER_SIZE = 2048
XA_SUBHEADER_OFFSET = 16
XA_SUBHEADER_SIZE = 8
MODE1_SYNC = b"\x00" + b"\xFF" * 10 + b"\x00"
MODE1_EDC_OFFSET = 0x810
MODE1_ZERO_OFFSET = 0x814
MODE1_ECC_P_OFFSET = 0x81C
MODE1_ECC_Q_OFFSET = 0x8C8
MODE1_ECC_END = 0x930


@dataclass(frozen=True)
class Track:
    number: int
    mode: str
    index_lba: int
    pregap_frames: int = 0

    @property
    def raw_offset(self) -> int:
        return self.index_lba * SECTOR_RAW_SIZE


@dataclass(frozen=True)
class CueSheet:
    cue_path: Path
    bin_path: Path
    tracks: tuple[Track, ...]

    def track(self, number: int) -> Track:
        for track in self.tracks:
            if track.number == number:
                return track
        raise KeyError(f"track {number:02d} not found")


@dataclass(frozen=True)
class IsoEntry:
    name: str
    extent_lba: int
    size: int
    is_dir: bool
    parent: str

    @property
    def path(self) -> str:
        return f"/{self.name}" if self.parent == "/" else f"{self.parent}/{self.name}"


EDC_LUT: tuple[int, ...] | None = None
ECC_F_LUT: tuple[int, ...] | None = None
ECC_B_LUT: tuple[int, ...] | None = None


def mmssff_to_lba(mmssff: str) -> int:
    minute, second, frame = [int(part) for part in mmssff.split(":")]
    return minute * 60 * 75 + second * 75 + frame


def lba_to_mmssff(lba: int) -> str:
    minute, rem = divmod(lba, 60 * 75)
    second, frame = divmod(rem, 75)
    return f"{minute:02d}:{second:02d}:{frame:02d}"


def _bcd(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def mode1_header(lba: int) -> bytes:
    # CD-ROM sector headers store absolute time as LBA + 150 in BCD.
    minute, rem = divmod(lba + 150, 60 * 75)
    second, frame = divmod(rem, 75)
    return bytes((_bcd(minute), _bcd(second), _bcd(frame), 0x01))


def edc_lut() -> tuple[int, ...]:
    global EDC_LUT
    if EDC_LUT is None:
        table: list[int] = []
        for i in range(256):
            edc = i
            for _ in range(8):
                edc = (edc >> 1) ^ (0xD8018001 if edc & 1 else 0)
            table.append(edc & 0xFFFFFFFF)
        EDC_LUT = tuple(table)
    return EDC_LUT


def ecc_luts() -> tuple[tuple[int, ...], tuple[int, ...]]:
    global ECC_F_LUT, ECC_B_LUT
    if ECC_F_LUT is None or ECC_B_LUT is None:
        f = [0] * 256
        b = [0] * 256
        for i in range(256):
            j = (i << 1) ^ (0x11D if i & 0x80 else 0)
            j &= 0xFF
            f[i] = j
            b[i ^ j] = i
        ECC_F_LUT = tuple(f)
        ECC_B_LUT = tuple(b)
    return ECC_F_LUT, ECC_B_LUT


def edc_calc(data: bytes | bytearray) -> int:
    table = edc_lut()
    edc = 0
    for byte in data:
        edc = (edc >> 8) ^ table[(edc ^ byte) & 0xFF]
    return edc & 0xFFFFFFFF


def ecc_compute(src: bytes | bytearray, major_count: int, minor_count: int,
                major_mult: int, minor_inc: int) -> bytes:
    ecc_f, ecc_b = ecc_luts()
    size = major_count * minor_count
    out = bytearray(major_count * 2)
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        ecc_a = 0
        ecc_bv = 0
        for _ in range(minor_count):
            value = src[index]
            index += minor_inc
            while index >= size:
                index -= size
            ecc_a ^= value
            ecc_bv ^= value
            ecc_a = ecc_f[ecc_a]
        ecc_a = ecc_b[ecc_f[ecc_a] ^ ecc_bv]
        out[major] = ecc_a
        out[major + major_count] = ecc_a ^ ecc_bv
    return bytes(out)


def rebuild_mode1_sector(lba: int, user: bytes | bytearray) -> bytes:
    if len(user) != MODE1_USER_SIZE:
        raise ValueError(f"MODE1 user sector must be {MODE1_USER_SIZE} bytes")
    raw = bytearray(SECTOR_RAW_SIZE)
    raw[0:12] = MODE1_SYNC
    raw[12:16] = mode1_header(lba)
    raw[MODE1_USER_OFFSET:MODE1_USER_OFFSET + MODE1_USER_SIZE] = user
    struct.pack_into("<I", raw, MODE1_EDC_OFFSET, edc_calc(raw[:MODE1_EDC_OFFSET]))
    raw[MODE1_ZERO_OFFSET:MODE1_ECC_P_OFFSET] = b"\x00" * 8
    raw[MODE1_ECC_P_OFFSET:MODE1_ECC_Q_OFFSET] = ecc_compute(raw[0x0C:], 86, 24, 2, 86)
    raw[MODE1_ECC_Q_OFFSET:MODE1_ECC_END] = ecc_compute(raw[0x0C:], 52, 43, 86, 88)
    return bytes(raw)


def parse_cue(cue_path: Path) -> CueSheet:
    cue_path = cue_path.resolve()
    bin_path: Path | None = None
    tracks: list[Track] = []
    current_number: int | None = None
    current_mode: str | None = None
    current_pregap = 0

    for raw in cue_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'FILE\s+"([^"]+)"\s+BINARY', line, flags=re.IGNORECASE)
        if m:
            bin_path = (cue_path.parent / m.group(1)).resolve()
            continue
        m = re.match(r"TRACK\s+(\d+)\s+(\S+)", line, flags=re.IGNORECASE)
        if m:
            current_number = int(m.group(1))
            current_mode = m.group(2).upper()
            current_pregap = 0
            continue
        m = re.match(r"PREGAP\s+(\d\d:\d\d:\d\d)", line, flags=re.IGNORECASE)
        if m:
            current_pregap = mmssff_to_lba(m.group(1))
            continue
        m = re.match(r"INDEX\s+01\s+(\d\d:\d\d:\d\d)", line, flags=re.IGNORECASE)
        if m and current_number is not None and current_mode is not None:
            tracks.append(
                Track(
                    number=current_number,
                    mode=current_mode,
                    index_lba=mmssff_to_lba(m.group(1)),
                    pregap_frames=current_pregap,
                )
            )
            continue

    if bin_path is None:
        raise ValueError(f"no BINARY FILE entry in {cue_path}")
    if not tracks:
        raise ValueError(f"no tracks in {cue_path}")
    return CueSheet(cue_path=cue_path, bin_path=bin_path, tracks=tuple(tracks))


def read_raw_sector(fh, lba: int) -> bytes:
    fh.seek(lba * SECTOR_RAW_SIZE)
    data = fh.read(SECTOR_RAW_SIZE)
    if len(data) != SECTOR_RAW_SIZE:
        raise ValueError(f"incomplete raw sector at LBA {lba}")
    return data


def read_mode1_user_sector(fh, absolute_lba: int) -> bytes:
    raw = read_raw_sector(fh, absolute_lba)
    return raw[MODE1_USER_OFFSET : MODE1_USER_OFFSET + MODE1_USER_SIZE]


def write_mode1_user_sector(fh, absolute_lba: int, user: bytes | bytearray) -> None:
    fh.seek(absolute_lba * SECTOR_RAW_SIZE)
    fh.write(rebuild_mode1_sector(absolute_lba, user))


def read_mode1_user_bytes(fh, track1: Track, start_lba: int, size: int) -> bytes:
    out = bytearray()
    pos = 0
    while pos < size:
        sector = read_mode1_user_sector(fh, track1.index_lba + start_lba + pos // MODE1_USER_SIZE)
        offset = pos % MODE1_USER_SIZE
        take = min(size - pos, MODE1_USER_SIZE - offset)
        out += sector[offset : offset + take]
        pos += take
    return bytes(out)


def write_mode1_user_bytes(fh, track1: Track, start_lba: int, payload: bytes) -> None:
    pos = 0
    total = len(payload)
    while pos < total:
        absolute_lba = track1.index_lba + start_lba + pos // MODE1_USER_SIZE
        existing = bytearray(read_mode1_user_sector(fh, absolute_lba))
        offset = pos % MODE1_USER_SIZE
        take = min(total - pos, MODE1_USER_SIZE - offset)
        existing[offset:offset + take] = payload[pos:pos + take]
        write_mode1_user_sector(fh, absolute_lba, existing)
        pos += take


def parse_dir_records(blob: bytes) -> Iterator[tuple[int, int, bool, str]]:
    i = 0
    while i < len(blob):
        rec_len = blob[i]
        if rec_len == 0:
            i = ((i // MODE1_USER_SIZE) + 1) * MODE1_USER_SIZE
            continue
        rec = blob[i : i + rec_len]
        if len(rec) < 34:
            break
        extent_lba = struct.unpack_from("<I", rec, 2)[0]
        size = struct.unpack_from("<I", rec, 10)[0]
        flags = rec[25]
        name_len = rec[32]
        ident = rec[33 : 33 + name_len]
        is_dir = bool(flags & 0x02)
        name = ident.decode("latin-1", errors="replace")
        if name == "\x00":
            name = "."
        elif name == "\x01":
            name = ".."
        else:
            name = name.split(";", 1)[0]
        yield extent_lba, size, is_dir, name
        i += rec_len


def parse_dir_records_with_offsets(blob: bytes) -> Iterator[tuple[int, int, int, int, bool, str]]:
    i = 0
    while i < len(blob):
        rec_len = blob[i]
        if rec_len == 0:
            i = ((i // MODE1_USER_SIZE) + 1) * MODE1_USER_SIZE
            continue
        rec = blob[i : i + rec_len]
        if len(rec) < 34:
            break
        extent_lba = struct.unpack_from("<I", rec, 2)[0]
        size = struct.unpack_from("<I", rec, 10)[0]
        flags = rec[25]
        name_len = rec[32]
        ident = rec[33 : 33 + name_len]
        is_dir = bool(flags & 0x02)
        name = ident.decode("latin-1", errors="replace")
        if name == "\x00":
            name = "."
        elif name == "\x01":
            name = ".."
        else:
            name = name.split(";", 1)[0]
        yield i, rec_len, extent_lba, size, is_dir, name
        i += rec_len


def read_pvd(fh, track1: Track) -> bytes:
    pvd = read_mode1_user_sector(fh, track1.index_lba + 16)
    if pvd[0] != 1 or pvd[1:6] != b"CD001":
        raise ValueError("primary volume descriptor not found on track 1 LBA 16")
    return pvd


def walk_iso(cue: CueSheet) -> list[IsoEntry]:
    track1 = cue.track(1)
    entries: list[IsoEntry] = []
    with cue.bin_path.open("rb") as fh:
        pvd = read_pvd(fh, track1)
        root_rec = pvd[156:190]
        root_lba = struct.unpack_from("<I", root_rec, 2)[0]
        root_size = struct.unpack_from("<I", root_rec, 10)[0]

        stack = [("/", root_lba, root_size)]
        seen: set[tuple[str, int, int]] = set()
        while stack:
            parent, lba, size = stack.pop()
            key = (parent, lba, size)
            if key in seen:
                continue
            seen.add(key)
            raw = read_mode1_user_bytes(fh, track1, lba, size)
            for extent, ent_size, is_dir, name in parse_dir_records(raw):
                if name in (".", ".."):
                    continue
                ent = IsoEntry(name=name, extent_lba=extent, size=ent_size, is_dir=is_dir, parent=parent)
                entries.append(ent)
                if is_dir:
                    stack.append((ent.path, extent, ent_size))
    return sorted(entries, key=lambda entry: entry.path.lower())


def extract_iso_file(cue: CueSheet, path: str, out_path: Path) -> None:
    wanted = "/" + path.strip("/")
    entry = next((ent for ent in walk_iso(cue) if ent.path.upper() == wanted.upper()), None)
    if entry is None or entry.is_dir:
        raise FileNotFoundError(path)
    track1 = cue.track(1)
    with cue.bin_path.open("rb") as fh:
        payload = read_mode1_user_bytes(fh, track1, entry.extent_lba, entry.size)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)


def sectors_for_size(size: int) -> int:
    return ceil(size / MODE1_USER_SIZE)


def _root_dir(cue: CueSheet, fh) -> tuple[int, int]:
    pvd = read_pvd(fh, cue.track(1))
    root_rec = pvd[156:190]
    return struct.unpack_from("<I", root_rec, 2)[0], struct.unpack_from("<I", root_rec, 10)[0]


def _directory_map(cue: CueSheet, entries: list[IsoEntry], fh) -> dict[str, tuple[int, int]]:
    root_lba, root_size = _root_dir(cue, fh)
    out = {"/": (root_lba, root_size)}
    for ent in entries:
        if ent.is_dir:
            out[ent.path] = (ent.extent_lba, ent.size)
    return out


def _pack_dir_record_values(record: bytearray, extent_lba: int, size: int) -> None:
    struct.pack_into("<I", record, 2, extent_lba)
    struct.pack_into(">I", record, 6, extent_lba)
    struct.pack_into("<I", record, 10, size)
    struct.pack_into(">I", record, 14, size)


def update_directory_records(cue: CueSheet, fh, updates: dict[str, tuple[int, int]]) -> None:
    """Update ISO9660 directory records for file paths.

    `updates` maps full normalized paths to `(new_extent_lba, new_size)`.
    Directory extents are intentionally not moved by the current remaster
    strategy, so only file records are rewritten.
    """
    if not updates:
        return
    entries = walk_iso(cue)
    dirs = _directory_map(cue, entries, fh)
    by_parent: dict[str, dict[str, tuple[str, int, int]]] = {}
    for path, (extent, size) in updates.items():
        parent = str(Path(path).parent).replace("\\", "/")
        if parent == ".":
            parent = "/"
        name = Path(path).name
        by_parent.setdefault(parent, {})[name.upper()] = (path, extent, size)

    for parent, wanted in by_parent.items():
        if parent not in dirs:
            raise FileNotFoundError(f"parent directory not found: {parent}")
        lba, size = dirs[parent]
        blob = bytearray(read_mode1_user_bytes(fh, cue.track(1), lba, size))
        found: set[str] = set()
        for off, rec_len, _old_extent, _old_size, is_dir, name in parse_dir_records_with_offsets(bytes(blob)):
            if is_dir:
                continue
            item = wanted.get(name.upper())
            if item is None:
                continue
            path, new_extent, new_size = item
            rec = bytearray(blob[off:off + rec_len])
            _pack_dir_record_values(rec, new_extent, new_size)
            blob[off:off + rec_len] = rec
            found.add(path)
        missing = set(path for path, _extent, _size in wanted.values()) - found
        if missing:
            raise FileNotFoundError(f"directory records not found in {parent}: {sorted(missing)}")
        write_mode1_user_bytes(fh, cue.track(1), lba, bytes(blob))


def update_volume_space(cue: CueSheet, fh, sector_delta: int) -> None:
    if sector_delta == 0:
        return
    track1 = cue.track(1)
    pvd = bytearray(read_pvd(fh, track1))
    old_le = struct.unpack_from("<I", pvd, 80)[0]
    old_be = struct.unpack_from(">I", pvd, 84)[0]
    if old_le != old_be:
        raise ValueError("PVD volume-space endian copies differ")
    new_size = old_le + sector_delta
    struct.pack_into("<I", pvd, 80, new_size)
    struct.pack_into(">I", pvd, 84, new_size)
    write_mode1_user_sector(fh, track1.index_lba + 16, pvd)


def write_file_payload(cue: CueSheet, fh, extent_lba: int, payload: bytes) -> None:
    padded = payload + b"\x00" * (sectors_for_size(len(payload)) * MODE1_USER_SIZE - len(payload))
    write_mode1_user_bytes(fh, cue.track(1), extent_lba, padded)


def parse_replacement(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("replacement must be ISO_PATH=LOCAL_PATH")
    iso_path, local = value.split("=", 1)
    iso_path = "/" + iso_path.strip("/")
    return iso_path, Path(local)


def shifted_cue_text(cue: CueSheet, out_bin: Path, sector_delta: int) -> str:
    current_track = 0
    out: list[str] = []
    for raw in cue.cue_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.rstrip("\r\n")
        m = re.match(r'(\s*)FILE\s+"([^"]+)"\s+BINARY(.*)', line, flags=re.IGNORECASE)
        if m:
            out.append(f'{m.group(1)}FILE "{out_bin.name}" BINARY{m.group(3)}')
            continue
        m = re.match(r"(\s*)TRACK\s+(\d+)\s+(\S+)(.*)", line, flags=re.IGNORECASE)
        if m:
            current_track = int(m.group(2))
            out.append(line)
            continue
        m = re.match(r"(\s*)INDEX\s+(\d+)\s+(\d\d:\d\d:\d\d)(.*)", line, flags=re.IGNORECASE)
        if m and current_track >= 2:
            new_lba = mmssff_to_lba(m.group(3)) + sector_delta
            out.append(f"{m.group(1)}INDEX {m.group(2)} {lba_to_mmssff(new_lba)}{m.group(4)}")
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def remaster_disc(cue: CueSheet, replacements: list[tuple[str, Path]],
                  out_bin: Path, out_cue: Path) -> None:
    entries = walk_iso(cue)
    entry_by_path = {ent.path.upper(): ent for ent in entries if not ent.is_dir}
    normalized: list[tuple[str, IsoEntry, Path, int]] = []
    for iso_path, local in replacements:
        ent = entry_by_path.get(iso_path.upper())
        if ent is None:
            raise FileNotFoundError(f"ISO file not found: {iso_path}")
        size = local.stat().st_size
        normalized.append((iso_path, ent, local, size))

    grown = [(iso_path, ent, local, size) for iso_path, ent, local, size in normalized if size > ent.size]
    append_lba = cue.track(2).index_lba
    append_cursor = append_lba
    replacement_extents: dict[str, tuple[int, int]] = {}
    for iso_path, ent, _local, size in grown:
        replacement_extents[iso_path] = (append_cursor, size)
        append_cursor += sectors_for_size(size)
    sector_delta = append_cursor - append_lba

    out_bin.parent.mkdir(parents=True, exist_ok=True)
    original = cue.bin_path.read_bytes()
    insert_at = append_lba * SECTOR_RAW_SIZE
    out_bin.write_bytes(
        original[:insert_at]
        + b"\x00" * (sector_delta * SECTOR_RAW_SIZE)
        + original[insert_at:]
    )

    updates: dict[str, tuple[int, int]] = {}
    for iso_path, ent, _local, size in normalized:
        if iso_path in replacement_extents:
            updates[iso_path] = replacement_extents[iso_path]
        else:
            updates[iso_path] = (ent.extent_lba, size)
    if sector_delta:
        for ent in entries:
            if ent.is_dir:
                continue
            if ent.path.upper().startswith("/ADPCM/") and ent.path.upper().endswith(".XA"):
                updates[ent.path] = (ent.extent_lba + sector_delta, ent.size)

    with out_bin.open("rb+") as fh:
        update_volume_space(cue, fh, sector_delta)
        update_directory_records(cue, fh, updates)
        for iso_path, ent, local, size in normalized:
            extent = replacement_extents.get(iso_path, (ent.extent_lba, size))[0]
            write_file_payload(cue, fh, extent, local.read_bytes())

    out_cue.parent.mkdir(parents=True, exist_ok=True)
    out_cue.write_text(shifted_cue_text(cue, out_bin, sector_delta), encoding="utf-8")
    print(
        f"remastered Saturn image -> {out_bin} ({out_bin.stat().st_size} bytes), "
        f"cue -> {out_cue}, shifted tracks by {sector_delta} sectors"
    )
    for iso_path, ent, _local, size in normalized:
        extent = replacement_extents.get(iso_path, (ent.extent_lba, size))[0]
        print(f"  {iso_path}: lba {ent.extent_lba}->{extent} size {ent.size}->{size}")


def _xa_entries(cue: CueSheet) -> list[IsoEntry]:
    return [
        ent for ent in walk_iso(cue)
        if not ent.is_dir and ent.path.upper().startswith("/ADPCM/") and ent.path.upper().endswith(".XA")
    ]


def xa_logical_to_physical_lba(cue: CueSheet, logical_lba: int) -> int:
    return logical_lba - cue.track(2).pregap_frames


def xa_sector_spans(cue: CueSheet) -> list[tuple[IsoEntry, int, int]]:
    entries = sorted(_xa_entries(cue), key=lambda ent: ent.extent_lba)
    out: list[tuple[IsoEntry, int, int]] = []
    with cue.bin_path.open("rb") as fh:
        for i, ent in enumerate(entries):
            physical = xa_logical_to_physical_lba(cue, ent.extent_lba)
            if i + 1 < len(entries):
                sectors = entries[i + 1].extent_lba - ent.extent_lba
            else:
                sectors = 0
                while True:
                    raw = read_raw_sector(fh, physical + sectors)
                    sub = raw[XA_SUBHEADER_OFFSET : XA_SUBHEADER_OFFSET + XA_SUBHEADER_SIZE]
                    sectors += 1
                    if sub[2] & 0x80:
                        break
            out.append((ent, physical, sectors))
    return out


def xa_info(cue: CueSheet) -> dict:
    spans = xa_sector_spans(cue)
    subheaders: dict[str, int] = {}
    eof_count = 0
    with cue.bin_path.open("rb") as fh:
        for _ent, physical, sectors in spans:
            for sector in range(sectors):
                raw = read_raw_sector(fh, physical + sector)
                sub = raw[XA_SUBHEADER_OFFSET : XA_SUBHEADER_OFFSET + XA_SUBHEADER_SIZE]
                key = sub.hex().upper()
                subheaders[key] = subheaders.get(key, 0) + 1
                if sub[2] & 0x80:
                    eof_count += 1
    return {
        "files": len(spans),
        "directories": len({Path(ent.path).parent.as_posix() for ent, _physical, _sectors in spans}),
        "first": {
            "path": spans[0][0].path,
            "logical_lba": spans[0][0].extent_lba,
            "physical_lba": spans[0][1],
            "sectors": spans[0][2],
            "size": spans[0][0].size,
        } if spans else None,
        "last": {
            "path": spans[-1][0].path,
            "logical_lba": spans[-1][0].extent_lba,
            "physical_lba": spans[-1][1],
            "sectors": spans[-1][2],
            "size": spans[-1][0].size,
        } if spans else None,
        "physical_range": [
            min((physical for _ent, physical, _sectors in spans), default=0),
            max((physical + sectors for _ent, physical, sectors in spans), default=0),
        ],
        "referenced_sectors": sum(sectors for _ent, _physical, sectors in spans),
        "eof_sectors": eof_count,
        "subheaders": dict(sorted(subheaders.items())),
    }


def extract_xa_raw(cue: CueSheet, path: str, out_path: Path) -> None:
    wanted = "/" + path.strip("/")
    spans = xa_sector_spans(cue)
    match = next((span for span in spans if span[0].path.upper() == wanted.upper()), None)
    if match is None:
        raise FileNotFoundError(path)
    _ent, physical, sectors = match
    out = bytearray()
    with cue.bin_path.open("rb") as fh:
        for sector in range(sectors):
            out += read_raw_sector(fh, physical + sector)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(out))


def cmd_info(args: argparse.Namespace) -> None:
    cue = parse_cue(Path(args.cue))
    print(json.dumps({
        "cue": str(cue.cue_path),
        "bin": str(cue.bin_path),
        "bin_size": cue.bin_path.stat().st_size,
        "tracks": [asdict(track) | {"raw_offset": track.raw_offset} for track in cue.tracks],
    }, indent=2))


def cmd_list(args: argparse.Namespace) -> None:
    cue = parse_cue(Path(args.cue))
    entries = walk_iso(cue)
    if args.json:
        print(json.dumps([asdict(ent) | {"path": ent.path} for ent in entries], indent=2))
        return
    for ent in entries:
        kind = "dir " if ent.is_dir else "file"
        print(f"{kind} {ent.extent_lba:6d} {ent.size:9d} {ent.path}")


def cmd_extract(args: argparse.Namespace) -> None:
    cue = parse_cue(Path(args.cue))
    extract_iso_file(cue, args.path, Path(args.out))


def cmd_xainfo(args: argparse.Namespace) -> None:
    cue = parse_cue(Path(args.cue))
    print(json.dumps(xa_info(cue), indent=2))


def cmd_extract_xa(args: argparse.Namespace) -> None:
    cue = parse_cue(Path(args.cue))
    extract_xa_raw(cue, args.path, Path(args.out))


def cmd_remaster(args: argparse.Namespace) -> None:
    cue = parse_cue(Path(args.cue))
    remaster_disc(cue, args.replace, Path(args.out_bin), Path(args.out_cue))


def cmd_verify(args: argparse.Namespace) -> None:
    """Check the data track against the release's known-good fingerprint.

    Only the data track is compared: every file the patch touches lives on it,
    while the whole-image hash is not comparable across rips because a
    mixed-mode dump may or may not store the audio pregaps.
    """
    import hashlib
    import zlib

    release = load_release(args.release)
    want = release.verify_entry("track1")
    if want is None:
        raise SystemExit(f"release {release.code} records no track1 fingerprint")

    cue = parse_cue(Path(args.cue))
    track1 = cue.track(1)
    end = cue.track(2).index_lba if len(cue.tracks) > 1 else None
    sectors = (end or 0) - track1.index_lba
    with cue.bin_path.open("rb") as fh:
        fh.seek(track1.raw_offset)
        blob = fh.read(sectors * SECTOR_RAW_SIZE)
    have = {
        "sectors": sectors,
        "crc32": f"{zlib.crc32(blob) & 0xFFFFFFFF:08x}",
        "md5": hashlib.md5(blob).hexdigest(),
        "sha1": hashlib.sha1(blob).hexdigest(),
    }
    bad = {k: (v, want[k]) for k, v in have.items() if v != want[k]}
    for key, value in have.items():
        mark = "MISMATCH" if key in bad else "ok"
        print(f"  track 1 {key:8s}: {value}  [{mark}]")
    if bad:
        raise SystemExit(
            f"data track does not match the recorded dump for {release.serial}; "
            "the build needs an authentic disc")
    source = want.get("source", "the recorded dump")
    print(f"data track 1 matches {source} ({release.serial})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cue", default=None,
                    help="Source CUE (default: the release manifest's image).")
    ap.add_argument("--release", default="l5-saturn-jp",
                    help="Release whose image and dump fingerprint this reads.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("extract")
    p.add_argument("path")
    p.add_argument("out")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("verify")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("xainfo")
    p.set_defaults(func=cmd_xainfo)

    p = sub.add_parser("extract-xa-raw")
    p.add_argument("path")
    p.add_argument("out")
    p.set_defaults(func=cmd_extract_xa)

    p = sub.add_parser("remaster")
    p.add_argument("--out-bin", required=True)
    p.add_argument("--out-cue", required=True)
    p.add_argument("--replace", action="append", type=parse_replacement, default=[],
                   metavar="ISO_PATH=LOCAL_PATH",
                   help="replace an ISO file; grown files are relocated before track 2")
    p.set_defaults(func=cmd_remaster)

    args = ap.parse_args()
    if not args.cue:
        image = load_release(args.release).image
        if image is None:
            raise SystemExit(f"{args.release} records no media image; pass --cue")
        args.cue = str(image)
    args.func(args)


if __name__ == "__main__":
    main()
