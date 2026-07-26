#!/usr/bin/env python3
"""Build-output reference hashes.

Every build stage already validates its own contract, but nothing pins the
*result*: a refactor that reshuffles manifests, renames modules or moves data
around must leave the produced files byte-identical, and only a recorded hash
can prove that.

Each release keeps `data/releases/<slug>/build_reference.json` listing the
sha1 of every artifact it produces, per language. Builds check it as the last
stage and fail on any drift; `--record-reference` re-records it, which is the
explicit way to say "this output change is intended".

Some artifacts carry the build stamp on purpose: the title credits render the
commit hash so a patch in the wild can be traced back to the tree that built
it. Those cannot be pinned by hash across commits, so the record keeps the
stamp it was taken at and reports stamped artifacts as not comparable when the
stamp has moved, instead of failing. Everything else stays strict.

This is a build-time validator like the SYSTEM contract check, not a test: it
runs as part of the build and refuses to let it finish on a mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

from lang5_project import ROOT

DEFAULT_RELEASE_ROOT = ROOT / "data" / "releases"

COMMENT = ("sha1 of every artifact this release builds, per language. Builds "
           "verify it as their last stage; re-record only when an output "
           "change is intended.")


def digest(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def reference_path(release: str,
                   release_root: str | Path = DEFAULT_RELEASE_ROOT) -> Path:
    root = Path(release_root)
    if not root.is_absolute():
        root = ROOT / root
    return root / release / "build_reference.json"


def _load(path: Path, release: str) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"release": release, "_comment": COMMENT, "artifacts": {}}


def check_or_record(release: str,
                    lang: str,
                    artifacts: dict[str, Path],
                    record: bool = False,
                    stamp: str | None = None,
                    stamped: Iterable[str] = (),
                    release_root: str | Path = DEFAULT_RELEASE_ROOT) -> None:
    """Verify (or record) the sha1 of this build's artifacts.

    `artifacts` maps a stable logical name to the produced file. Names, not
    paths, are the key: work paths carry a language suffix and may move, the
    logical role of the file does not.

    `stamped` names the artifacts that embed `stamp` (the commit hash the
    credits render). They are only compared while the stamp is unchanged.
    """
    path = reference_path(release, release_root)
    doc = _load(path, release)
    doc.setdefault("_comment", COMMENT)
    recorded = doc.setdefault("artifacts", {}).get(lang)
    stamped = set(stamped)

    missing = sorted(name for name, p in artifacts.items() if not Path(p).exists())
    if missing:
        raise SystemExit(
            f"build reference: {release}/{lang} artifacts not produced: "
            + ", ".join(missing))

    files = {}
    for name, p in sorted(artifacts.items()):
        entry = {"sha1": digest(Path(p)), "size": Path(p).stat().st_size}
        if name in stamped:
            entry["stamped"] = True
        files[name] = entry
    current = {"stamp": stamp, "files": files}

    if record or recorded is None:
        doc["artifacts"][lang] = current
        doc["artifacts"] = dict(sorted(doc["artifacts"].items()))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        verb = "re-recorded" if record else "recorded"
        print(f"build reference {verb}: {release}/{lang} "
              f"{len(files)} artifacts -> {path}")
        return

    was = recorded.get("files", {})
    stamp_moved = stamp != recorded.get("stamp")
    drift = []
    skipped = 0
    for name in sorted(set(files) | set(was)):
        want = was.get(name)
        got = files.get(name)
        if want is None:
            drift.append(f"  + {name}: new artifact {got['sha1']}")
        elif got is None:
            drift.append(f"  - {name}: no longer built (was {want['sha1']})")
        elif want["sha1"] == got["sha1"]:
            continue
        elif stamp_moved and (want.get("stamped") or got.get("stamped")):
            skipped += 1
        else:
            drift.append(f"  ! {name}: {want['sha1']} -> {got['sha1']}"
                         f" (size {want['size']} -> {got['size']})")
    if drift:
        raise SystemExit(
            f"build reference mismatch for {release}/{lang} ({path}):\n"
            + "\n".join(drift)
            + "\nRe-run with --record-reference if the change is intended.")
    note = ""
    if skipped:
        note = (f", {skipped} stamped not comparable "
                f"({recorded.get('stamp')} -> {stamp})")
    print(f"build reference ok: {release}/{lang} "
          f"{len(files) - skipped} artifacts match{note}")


def default_release(game: str, platform: str, region: str = "jp") -> str:
    """Slug of the release a game+platform build targets.

    Every disc the project owns is the Japanese first print, so the region
    defaults to `jp`; a second print or a localised original gets its own slug
    rather than overloading this one.
    """
    return f"{game}-{platform}-{region}"


def add_reference_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--record-reference", action="store_true",
                    help="Re-record the build output hashes instead of "
                         "checking them (use when an output change is "
                         "intended).")
    ap.add_argument("--skip-reference", action="store_true",
                    help="Skip the build output hash check entirely.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--release", required=True)
    ap.add_argument("--lang", required=True)
    ap.add_argument("--release-root", default=DEFAULT_RELEASE_ROOT)
    ap.add_argument("--stamp", default=None,
                    help="Build stamp the stamped artifacts embed.")
    ap.add_argument("--stamped", action="append", default=[],
                    help="Artifact name that embeds the build stamp.")
    add_reference_args(ap)
    ap.add_argument("artifact", nargs="+",
                    help="name=path pairs of produced artifacts.")
    args = ap.parse_args()

    artifacts: dict[str, Path] = {}
    for item in args.artifact:
        name, _, path = item.partition("=")
        if not path:
            raise SystemExit(f"expected name=path, got {item!r}")
        artifacts[name] = Path(path)

    if args.skip_reference:
        print("build reference check skipped")
        return
    check_or_record(args.release, args.lang, artifacts,
                    record=args.record_reference,
                    stamp=args.stamp, stamped=args.stamped,
                    release_root=args.release_root)


if __name__ == "__main__":
    main()
