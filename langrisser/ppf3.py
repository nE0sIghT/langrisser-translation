#!/usr/bin/env python3
import argparse
from pathlib import Path

# PPF3.0 image validation ("blockcheck"): tools read 1024 bytes from the image
# being patched at this offset (imagetype BIN) and compare them to the block
# stored in the patch header, refusing to patch a mismatched image.
BLOCKCHECK_OFFSET = 0x9320
BLOCKCHECK_SIZE = 1024


def write_ppf3(
    original: bytes,
    modified: bytes,
    out_path: Path,
    description: str,
    blockcheck: bool = True,
) -> int:
    if len(original) != len(modified):
        raise ValueError("PPF3 requires images of the same size.")

    # Only embed the validation block when the image is large enough to contain
    # it; otherwise emit a valid PPF3.0 patch without blockcheck.
    use_blockcheck = blockcheck and len(original) >= BLOCKCHECK_OFFSET + BLOCKCHECK_SIZE

    desc = description.encode("ascii", errors="replace")[:50].ljust(50, b"\x00")
    out = bytearray()
    out += b"PPF30"          # format id
    out += b"\x02"           # encoding method: 2 = PPF3.0
    out += desc              # 50-byte description
    out += b"\x00"           # image type: 0 = BIN
    out += b"\x01" if use_blockcheck else b"\x00"  # block check enabled/disabled
    out += b"\x00"           # undo data disabled
    out += b"\x00"           # dummy
    if use_blockcheck:       # 1024-byte image validation block
        out += original[BLOCKCHECK_OFFSET:BLOCKCHECK_OFFSET + BLOCKCHECK_SIZE]

    i = 0
    records = 0
    n = len(original)
    while i < n:
        if original[i] == modified[i]:
            i += 1
            continue
        start = i
        while i < n and original[i] != modified[i] and (i - start) < 255:
            i += 1
        chunk = modified[start:i]
        out += start.to_bytes(8, "little")
        out += bytes([len(chunk)])
        out += chunk
        records += 1

    out_path.write_bytes(out)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a PPF3 patch from two BIN images.")
    parser.add_argument("original", help="Original BIN")
    parser.add_argument("modified", help="Modified BIN")
    parser.add_argument("output", help="Output PPF path")
    parser.add_argument(
        "--description",
        default="Langrisser V patch",
        help="ASCII description (max 50 chars)",
    )
    parser.add_argument(
        "--no-blockcheck",
        action="store_true",
        help="Disable the PPF3.0 image validation block",
    )
    args = parser.parse_args()

    original = Path(args.original).read_bytes()
    modified = Path(args.modified).read_bytes()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records = write_ppf3(
        original, modified, out_path, args.description,
        blockcheck=not args.no_blockcheck,
    )
    print(f"wrote {out_path} ({records} records)")


if __name__ == "__main__":
    main()
