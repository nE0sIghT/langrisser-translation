#!/usr/bin/env python3
import argparse
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Tuple


SECTOR_RAW_SIZE = 2352
SECTOR_USER_OFFSET = 24
SECTOR_USER_SIZE = 2048


@dataclass
class IsoEntry:
    name: str
    extent_lba: int
    size: int
    is_dir: bool
    parent: str

    @property
    def path(self) -> str:
        if self.parent == "/":
            return f"/{self.name}"
        return f"{self.parent}/{self.name}"


def mmssff_to_lba(mmssff: str) -> int:
    m, s, f = [int(x) for x in mmssff.split(":")]
    return (m * 60 * 75) + (s * 75) + f


def read_user_sector(fh, lba: int) -> bytes:
    fh.seek((lba * SECTOR_RAW_SIZE) + SECTOR_USER_OFFSET)
    data = fh.read(SECTOR_USER_SIZE)
    if len(data) != SECTOR_USER_SIZE:
        raise ValueError(f"incomplete sector at LBA {lba}")
    return data


def read_user_bytes(fh, start_lba: int, size: int) -> bytes:
    out = bytearray()
    pos = 0
    while pos < size:
        sector = read_user_sector(fh, start_lba + (pos // SECTOR_USER_SIZE))
        offset = pos % SECTOR_USER_SIZE
        take = min(size - pos, SECTOR_USER_SIZE - offset)
        out += sector[offset : offset + take]
        pos += take
    return bytes(out)


def write_user_bytes(fh, start_lba: int, payload: bytes) -> None:
    pos = 0
    total = len(payload)
    while pos < total:
        sector_index = start_lba + (pos // SECTOR_USER_SIZE)
        offset = pos % SECTOR_USER_SIZE
        take = min(total - pos, SECTOR_USER_SIZE - offset)
        fh.seek((sector_index * SECTOR_RAW_SIZE) + SECTOR_USER_OFFSET + offset)
        fh.write(payload[pos : pos + take])
        pos += take


def parse_dir_records(blob: bytes) -> Iterator[Tuple[int, int, bool, str]]:
    i = 0
    while i < len(blob):
        rec_len = blob[i]
        if rec_len == 0:
            i = ((i // SECTOR_USER_SIZE) + 1) * SECTOR_USER_SIZE
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
            if ";" in name:
                name = name.split(";", 1)[0]
        yield extent_lba, size, is_dir, name
        i += rec_len


def parse_dir_records_with_offsets(blob: bytes) -> Iterator[Tuple[int, int, int, int, bool, str]]:
    i = 0
    while i < len(blob):
        rec_len = blob[i]
        if rec_len == 0:
            i = ((i // SECTOR_USER_SIZE) + 1) * SECTOR_USER_SIZE
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
            if ";" in name:
                name = name.split(";", 1)[0]
        yield i, rec_len, extent_lba, size, is_dir, name
        i += rec_len


def read_pvd(fh) -> bytes:
    pvd = read_user_sector(fh, 16)
    if pvd[1:6] != b"CD001" or pvd[0] != 1:
        raise ValueError("primary volume descriptor not found at sector 16")
    return pvd


def walk_iso(fh) -> List[IsoEntry]:
    pvd = read_pvd(fh)
    root_rec = pvd[156:190]
    root_lba = struct.unpack_from("<I", root_rec, 2)[0]
    root_size = struct.unpack_from("<I", root_rec, 10)[0]

    entries: List[IsoEntry] = []
    stack = [("/", root_lba, root_size)]
    seen = set()

    while stack:
        parent, lba, size = stack.pop()
        key = (parent, lba, size)
        if key in seen:
            continue
        seen.add(key)
        raw = read_user_bytes(fh, lba, size)
        for extent, ent_size, is_dir, name in parse_dir_records(raw):
            if name in (".", ".."):
                continue
            ent = IsoEntry(name=name, extent_lba=extent, size=ent_size, is_dir=is_dir, parent=parent)
            entries.append(ent)
            if is_dir:
                stack.append((ent.path, extent, ent_size))
    entries.sort(key=lambda e: e.path.lower())
    return entries


def extract_file(fh, path: str, out_path: str) -> None:
    wanted = path.strip("/")
    with open(fh.name, "rb") as handle:
        entries = walk_iso(handle)
        for ent in entries:
            if ent.is_dir:
                continue
            if ent.path.strip("/") == wanted:
                data = read_user_bytes(handle, ent.extent_lba, ent.size)
                Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                Path(out_path).write_bytes(data)
                return
    raise FileNotFoundError(f"ISO file not found: {path}")


def inject_file(fh, path: str, in_path: str) -> None:
    wanted = path.strip("/")
    payload = Path(in_path).read_bytes()
    entries = walk_iso(fh)
    for ent in entries:
        if ent.is_dir:
            continue
        if ent.path.strip("/") == wanted:
            if len(payload) > ent.size:
                raise ValueError(
                    f"payload too large for {path}: {len(payload)} > {ent.size}. "
                    "In-place injection must not grow file size."
                )
            if len(payload) < ent.size:
                payload = payload + (b"\x00" * (ent.size - len(payload)))
            write_user_bytes(fh, ent.extent_lba, payload)
            return
    raise FileNotFoundError(f"ISO file not found: {path}")


def _find_parent_dir_entry(fh, parent_path: str) -> IsoEntry:
    if parent_path == "/":
        pvd = read_pvd(fh)
        root_rec = pvd[156:190]
        root_lba = struct.unpack_from("<I", root_rec, 2)[0]
        root_size = struct.unpack_from("<I", root_rec, 10)[0]
        return IsoEntry(name="", extent_lba=root_lba, size=root_size, is_dir=True, parent="/")
    entries = walk_iso(fh)
    for ent in entries:
        if ent.path == parent_path and ent.is_dir:
            return ent
    raise FileNotFoundError(f"parent directory not found: {parent_path}")


def _total_raw_sectors(fh) -> int:
    fh.seek(0, 2)
    end = fh.tell()
    if end % SECTOR_RAW_SIZE != 0:
        raise ValueError("BIN size is not aligned to raw sector size.")
    return end // SECTOR_RAW_SIZE


def _find_free_region_lba(fh, needed_sectors: int, reserve_start_lba: int = 64) -> int | None:
    entries = walk_iso(fh)
    total = _total_raw_sectors(fh)

    ranges: List[Tuple[int, int]] = [(0, max(0, reserve_start_lba))]
    for e in entries:
        sec = (e.size + (SECTOR_USER_SIZE - 1)) // SECTOR_USER_SIZE
        ranges.append((e.extent_lba, e.extent_lba + sec))
    ranges.sort()

    merged: List[List[int]] = []
    for a, b in ranges:
        if not merged or a > merged[-1][1]:
            merged.append([a, b])
        else:
            merged[-1][1] = max(merged[-1][1], b)

    cur = 0
    for a, b in merged:
        if cur < a and (a - cur) >= needed_sectors:
            return cur
        cur = max(cur, b)
    if cur < total and (total - cur) >= needed_sectors:
        return cur
    return None


def _update_dir_record_extent_size(
    fh,
    parent_lba: int,
    parent_size: int,
    child_name: str,
    new_extent_lba: int,
    new_size: int,
) -> None:
    blob = bytearray(read_user_bytes(fh, parent_lba, parent_size))
    updated = False
    for off, rec_len, _, _, is_dir, name in parse_dir_records_with_offsets(bytes(blob)):
        if is_dir:
            continue
        if name == child_name:
            rec = blob[off : off + rec_len]
            struct.pack_into("<I", rec, 2, new_extent_lba)
            struct.pack_into(">I", rec, 6, new_extent_lba)
            struct.pack_into("<I", rec, 10, new_size)
            struct.pack_into(">I", rec, 14, new_size)
            blob[off : off + rec_len] = rec
            updated = True
            break
    if not updated:
        raise FileNotFoundError(f"directory record not found for '{child_name}' in parent directory")
    write_user_bytes(fh, parent_lba, bytes(blob))


def inject_file_allow_grow(fh, path: str, in_path: str) -> None:
    wanted = path.strip("/")
    payload = Path(in_path).read_bytes()
    entries = walk_iso(fh)
    target = None
    for ent in entries:
        if ent.is_dir:
            continue
        if ent.path.strip("/") == wanted:
            target = ent
            break
    if target is None:
        raise FileNotFoundError(f"ISO file not found: {path}")

    old_lba = target.extent_lba
    old_size = target.size
    if len(payload) <= old_size:
        inject_file(fh, path, in_path)
        return

    sectors_needed = (len(payload) + (SECTOR_USER_SIZE - 1)) // SECTOR_USER_SIZE
    new_lba = _find_free_region_lba(fh, sectors_needed)
    if new_lba is None:
        raise RuntimeError(
            f"no free in-image region for {path} growth: need {sectors_needed} sectors; "
            "cannot keep BIN size unchanged"
        )
    padded = payload + (b"\x00" * (sectors_needed * SECTOR_USER_SIZE - len(payload)))
    write_user_bytes(fh, new_lba, padded)

    parent = _find_parent_dir_entry(fh, target.parent)
    _update_dir_record_extent_size(
        fh,
        parent_lba=parent.extent_lba,
        parent_size=parent.size,
        child_name=target.name,
        new_extent_lba=new_lba,
        new_size=len(payload),
    )

    # Best-effort safety note for reproducibility/debug.
    print(
        f"grew {path}: old_lba={old_lba} old_size={old_size} "
        f"-> new_lba={new_lba} new_size={len(payload)} sectors={sectors_needed}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Read PS1 MODE2/2352 ISO9660 filesystem.")
    parser.add_argument("bin_path", help="Path to MODE2/2352 BIN")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List files in ISO filesystem")
    p_list.add_argument("--dirs", action="store_true", help="Include directories in output")

    p_extract = sub.add_parser("extract", help="Extract a file from ISO")
    p_extract.add_argument("iso_path", help="Path in ISO (e.g. /SCUS_123.45)")
    p_extract.add_argument("output", help="Output path")

    p_inject = sub.add_parser("inject", help="Inject file into ISO (in-place by default)")
    p_inject.add_argument("iso_path", help="Path in ISO (e.g. /SCUS_123.45)")
    p_inject.add_argument("input", help="Input file to inject")
    p_inject.add_argument("--allow-grow", action="store_true", help="Allow file growth by appending new sectors and rewriting directory record.")

    args = parser.parse_args()
    mode = "rb+" if args.cmd == "inject" else "rb"
    with open(args.bin_path, mode) as fh:
        if args.cmd == "list":
            for ent in walk_iso(fh):
                if ent.is_dir and not args.dirs:
                    continue
                kind = "d" if ent.is_dir else "f"
                print(f"{kind} {ent.extent_lba:7d} {ent.size:9d} {ent.path}")
        elif args.cmd == "extract":
            extract_file(fh, args.iso_path, args.output)
            print(f"extracted {args.iso_path} -> {args.output}")
        elif args.cmd == "inject":
            if args.allow_grow:
                inject_file_allow_grow(fh, args.iso_path, args.input)
            else:
                inject_file(fh, args.iso_path, args.input)
            print(f"injected {args.input} -> {args.iso_path}")


if __name__ == "__main__":
    main()
