#!/usr/bin/env bash
# Build release artifacts for Langrisser V (PS1) language-pack patches.
#
# The patches are binary diffs against the verified original data track, so the
# build needs the local BIN in iso/ (never committed). A clean tagged commit
# produces reproducible PPFs; --release refuses a dirty tree and must run on an
# exact git tag unless --version is explicitly supplied.
#
# Usage:
#   scripts/release.sh                         # dev build for en + ru into dist/dev/
#   scripts/release.sh -v 3                    # versioned build into dist/v3/
#   scripts/release.sh --lang en -v 3          # build one language
#   scripts/release.sh --release               # clean tagged release build
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -z "${PYTHON:-}" ]]; then
	if [[ -x .venv/bin/python ]]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
fi
ORIG_BIN="${ORIG_BIN:-iso/l5-ps1-jp/Langrisser IV & V - Final Edition (Japan) (Disc 2) (Langrisser V Disc) (Track 1).bin}"
ORIG_SHA256="${ORIG_SHA256:-ec9a15e84d82df498fd85b267fb1494547b2074ed34f3257c983f5e67aedd14d}"
EXTRACT_DIR="${EXTRACT_DIR:-work/l5/extracted}"
DIST_ROOT="${DIST_ROOT:-dist}"

VERSION=""
VERSION_EXPLICIT=0
RELEASE=0
LANGS=()

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
	case "$1" in
		-v|--version) VERSION="${2:?--version needs an argument}"; VERSION_EXPLICIT=1; shift 2 ;;
		--lang)       LANGS+=("${2:?--lang needs an argument}"); shift 2 ;;
		--release)    RELEASE=1; shift ;;
		-h|--help)    usage; exit 0 ;;
		*) echo "unknown argument: $1" >&2; usage; exit 1 ;;
	esac
done

if [[ ${#LANGS[@]} -eq 0 ]]; then
	LANGS=(en ru)
fi

exact_tag="$(git describe --tags --exact-match 2>/dev/null || true)"
if [[ -z "$VERSION" ]]; then
	if [[ -n "$exact_tag" ]]; then VERSION="${exact_tag#v}"; else VERSION="dev"; fi
fi
if [[ "$VERSION" =~ ^[0-9] ]]; then LABEL="v$VERSION"; else LABEL="$VERSION"; fi
DIST="$DIST_ROOT/$LABEL"

log() { printf '\n==> %s\n' "$*"; }

if [[ "$RELEASE" == 1 ]]; then
	if [[ -n "$(git status --porcelain)" ]]; then
		echo "ERROR: working tree is dirty; a release build must be clean." >&2
		git status --short >&2
		exit 1
	fi
	if [[ -z "$exact_tag" && "$VERSION_EXPLICIT" == 0 ]]; then
		echo "ERROR: --release needs an exact git tag or explicit --version." >&2
		exit 1
	fi
fi

if [[ ! -f "$ORIG_BIN" ]]; then
	echo "ERROR: original image not found at $ORIG_BIN." >&2
	echo "Place the verified Langrisser V PS1 BIN there (see README)." >&2
	exit 1
fi

log "Verifying original image"
echo "$ORIG_SHA256  $ORIG_BIN" | sha256sum -c -

log "Ensuring extracted game files"
mkdir -p "$EXTRACT_DIR"
for spec in \
	"/L5/SCEN.DAT:SCEN.DAT" \
	"/L5/SCEN2.DAT:SCEN2.DAT" \
	"/L5/SYSTEM.BIN:SYSTEM.BIN" \
	"/L5/IMG.DAT:IMG.DAT" \
	"/SLPS_018.19:SLPS_018.19"; do
	path="${spec%%:*}"
	name="${spec##*:}"
	if [[ ! -f "$EXTRACT_DIR/$name" ]]; then
		"$PYTHON" -m langrisser.iso_mode2 "$ORIG_BIN" extract "$path" "$EXTRACT_DIR/$name"
	fi
done

lang_suffix() {
	local lang="$1"
	"$PYTHON" - "$lang" <<'PY'
import json, sys
from pathlib import Path
lang = sys.argv[1]
manifest = json.loads(Path('data/games/l5/lang', lang, 'manifest.json').read_text(encoding='utf-8'))
print(manifest.get('patch_suffix') or lang)
PY
}

file_stats() {
	"$PYTHON" - "$1" <<'PY'
import hashlib, sys, zlib
from pathlib import Path
p = Path(sys.argv[1])
b = p.read_bytes()
print(f"size={len(b)} crc32={zlib.crc32(b)&0xffffffff:08X} sha256={hashlib.sha256(b).hexdigest()}")
PY
}

file_crc32() {
	"$PYTHON" - "$1" <<'PY'
import sys, zlib
from pathlib import Path
print(f"{zlib.crc32(Path(sys.argv[1]).read_bytes())&0xffffffff:08X}")
PY
}

file_size() {
	stat -Lc%s "$1"
}

log "Running shared no-edit round trip"
"$PYTHON" -m langrisser.verify_roundtrip

rm -rf "$DIST"
mkdir -p "$DIST"

commit="$(git rev-parse HEAD)"
{
	printf 'Langrisser V translation %s\n' "$LABEL"
	printf 'Commit: %s\n' "$commit"
	if [[ -n "$exact_tag" ]]; then printf 'Tag: %s\n' "$exact_tag"; fi
	printf '\nOriginal image:\n'
	printf '  File: %s\n' "$ORIG_BIN"
	printf '  %s\n' "$(file_stats "$ORIG_BIN")"
	printf '\n'
} > "$DIST/MANIFEST.txt"

{
	printf '# Langrisser V (PS1) translation - %s\n' "$LABEL"
	printf '# commit %s\n' "$commit"
	if [[ -n "$exact_tag" ]]; then printf '# tag %s\n' "$exact_tag"; fi
} > "$DIST/SHA256SUMS"

metadata=""

for lang in "${LANGS[@]}"; do
	suffix="$(lang_suffix "$lang")"
	log "Validating $lang"
	"$PYTHON" -m langrisser.rewrap --lang "$lang"
	if [[ "$lang" == "en" ]]; then
		"$PYTHON" -m langrisser.check_speakers --lang "$lang"
	fi
	if [[ "$lang" == "ru" ]]; then
		"$PYTHON" -m langrisser.validate_terms --lang "$lang" --require-complete --require-speakers --max-plate-chars 10
	else
		"$PYTHON" -m langrisser.validate_terms --lang "$lang" --require-complete
	fi
	"$PYTHON" -m langrisser.validate_translation --lang "$lang"

	log "Building $lang patch (version $VERSION)"
	"$PYTHON" -m langrisser.build_ppf --lang "$lang" --patch-version "$VERSION"

	ppf="patches/langrisser_v_${suffix}.ppf"
	patched_bin="work/l5/build/langrisser_v_${suffix}.bin"
	ppf_name="langrisser_v_${suffix}-${LABEL}.ppf"
	cp "$ppf" "$DIST/$ppf_name"
	ppf_sha="$(sha256sum "$DIST/$ppf_name" | cut -d' ' -f1)"
	img_sha="$(sha256sum "$patched_bin" | cut -d' ' -f1)"
	img_md5="$(md5sum "$patched_bin" | cut -d' ' -f1)"
	img_crc="$(file_crc32 "$patched_bin")"
	img_sz="$(file_size "$patched_bin")"
	printf '%s  %s\n' "$ppf_sha" "$ppf_name" >> "$DIST/SHA256SUMS"
	{
		printf '[%s]\n' "$lang"
		printf '  PPF: %s\n' "$ppf_name"
		printf '  PPF SHA-256: %s\n' "$ppf_sha"
		printf '  Patched image: %s\n' "$patched_bin"
		printf '  %s\n' "$(file_stats "$patched_bin")"
		printf '\n'
	} >> "$DIST/MANIFEST.txt"
	metadata+=$'\n'
	metadata+="# [$lang] Patched data track (apply $ppf_name to the verified original):"$'\n'
	metadata+="#   crc32  $img_crc"$'\n'
	metadata+="#   sha256 $img_sha"$'\n'
	metadata+="#   md5    $img_md5"$'\n'
	metadata+="#   size   $img_sz bytes"$'\n'
done

{
	printf '#\n'
	printf '%s' "$metadata"
	printf '# Required original data track (the audio tracks and .cue are unchanged):\n'
	printf '#   file   %s\n' "$(basename "$ORIG_BIN")"
	printf '#   crc32  %s\n' "$(file_crc32 "$ORIG_BIN")"
	printf '#   sha256 %s\n' "$ORIG_SHA256"
	printf '#   md5    %s\n' "$(md5sum "$ORIG_BIN" | cut -d' ' -f1)"
	printf '#   size   %s bytes\n' "$(file_size "$ORIG_BIN")"
} >> "$DIST/SHA256SUMS"

log "Release artifacts in $DIST/"
ls -l "$DIST"
