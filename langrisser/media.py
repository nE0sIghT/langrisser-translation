#!/usr/bin/env python3
"""Writing translated files back onto a release's medium.

Each builder used to name its own medium: the PS1 flow called `iso_mode2.py`
because Langrisser V happens to sit on an ISO9660 disc, the Saturn flow called
`saturn_disc.py` because its disc is mixed-mode. Neither choice belongs in a
builder — it follows from the release's `media.kind`, and a cartridge release
would need a third answer neither builder could give.

This module is the one place that maps a kind to a writer. Adding a medium
means adding a writer here and a `kind` in a release manifest, not touching
the flows.

Writers deliberately do not grow the image. Every release the project targets
so far has been proven unsafe to relocate files on — the PS1 disc's free tail
overlaps its CD audio tracks — so growth is an explicit, per-medium decision
made by the writer that supports it (the Saturn remaster relocates grown files
before track 2), never a default.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

from langrisser.release import ReleasePack


class MediaWriter(Protocol):
    """Puts translated files onto a copy of a release's medium."""

    kind: str

    def write(self, source: Path, out: Path,
              replacements: dict[str, Path]) -> Path:
        """Copy `source` to `out` with each media path replaced.

        Returns the path actually written, which is not always `out`: a
        multi-track medium writes a BIN next to a CUE.
        """
        ...


class IsoMode2Writer:
    """MODE2/2352 ISO9660 image, replaced in place at fixed file sizes."""

    kind = "iso-mode2"

    def write(self, source: Path, out: Path,
              replacements: dict[str, Path]) -> Path:
        from langrisser import iso_mode2

        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, out)
        with open(out, "r+b") as fh:
            for media_path, local in replacements.items():
                iso_mode2.inject_file(fh, media_path, str(local))
        return out


class CueMultitrackWriter:
    """Mixed-mode BIN/CUE, remastered so grown files relocate before track 2."""

    kind = "cue-multitrack"

    def __init__(self, out_cue: Path | None = None):
        self.out_cue = out_cue

    def write(self, source: Path, out: Path,
              replacements: dict[str, Path]) -> Path:
        from langrisser import saturn_disc

        out.parent.mkdir(parents=True, exist_ok=True)
        cue = saturn_disc.parse_cue(source)
        out_cue = self.out_cue or out.with_suffix(".cue")
        saturn_disc.remaster_disc(cue, list(replacements.items()), out, out_cue)
        return out


WRITERS: dict[str, type] = {
    IsoMode2Writer.kind: IsoMode2Writer,
    CueMultitrackWriter.kind: CueMultitrackWriter,
}


def writer_for(release: ReleasePack, **kwargs) -> MediaWriter:
    """The writer for a release's medium."""
    kind = release.media_kind
    factory = WRITERS.get(kind)
    if factory is None:
        known = ", ".join(sorted(WRITERS))
        raise SystemExit(
            f"release {release.code} uses medium {kind!r}, which has no "
            f"writer; known media: {known}")
    return factory(**kwargs)
