# Langrisser IV & V Translation Toolkit (PS1 / Sega Saturn)

Toolkit and language-pack repository for translating **Langrisser V** on both
of its releases — PS1 (SLPS-01819) and Sega Saturn (T-2509G) — and, sharing
the same tooling, **Langrisser IV** (SLPS-01818), which ships on disc A of the
same PS1 release. One language pack drives every build: the game and the
console are build-time choices, so the same translation ships as a PS1 PPF
patch and as a remastered Saturn BIN/CUE. The project currently ships English
and Russian for Langrisser V; additional target-language packs follow the same
layout under each game's `lang/<code>/`.

Platform differences are data, not forks. `data/platforms/<code>/` describes
the console itself; the mappings that prove which entries two builds share
belong to the release that was ported, and target text existing only there
lives in that pack's own overlay.

The same holds for the *game*. Langrisser IV and V share every container
format, so both are described by manifests under `data/games/<code>/` and
driven by the same tools with `--game`.

What a game and a console cannot describe between them is a particular print,
so there is a third axis: a **release** is one shipped build — a set of games,
on a platform, in a region. It owns the medium, the disc paths, the boot
executable, the file offsets, the font-plane ceiling and the known-good dump
fingerprint, because every one of those differs between a game's ports. Tools
take `--release`, or resolve it from `--game` and `--platform` when a game has
exactly one release there.

| Release | Games | Medium | State |
| --- | --- | --- | --- |
| `l5-ps1-jp` | `l5` | `SLPS-01819`, disc B of the two-disc set | complete: PS1 PPF patch |
| `l5-saturn-jp` | `l5` | `T-2509G` | complete: remastered BIN/CUE |
| `l4-ps1-jp` | `l4` | `SLPS-01818`, disc A | base only: formats verified, font map derived, empty packs |

The repository contains only durable translation data and tooling. Original game
assets, extracted files, generated Japanese dumps, build products and local
patch outputs are not tracked.

## Patch Use

Provide your own original Langrisser V disc image. The patch modifies only the
`.bin`; the `.cue` is unchanged.

- DuckStation: put the language PPF next to your `.bin` and run the game.
- Other emulators: apply the PPF3 patch to the `.bin` with a PPF tool, then keep
  the original `.cue`.

Generated patch names:

- `patches/langrisser_v_en.ppf` - English patch.
- `patches/langrisser_v_ru.ppf` - Russian patch.

Verify release downloads against release `SHA256SUMS`.

## Donations

If you want to support the work or say thanks:

- BTC: `bc1q3qmyy5wx8antx2e44lrr4fv9h3z8hs4x7jlnat`
- EVM: `0x6b513f6853003726502ec258351fcf6b82336d49`
- Boosty: https://boosty.to/ne0sight/donate

## License

This toolkit is GPL-3.0-or-later. See `LICENSE`. Game assets are not included.

Bundled third-party fonts:

- `data/fonts/terminus-normal.otb` and `data/fonts/ter-u14n.bdf`: Terminus
  Font, SIL Open Font License 1.1. See `data/fonts/Terminus-OFL.txt`.
- `data/fonts/spleen-6x12.bdf`: Spleen 6x12, BSD 2-Clause. See
  `data/fonts/Spleen-LICENSE`.
- `data/fonts/PixelMplus*.ttf`: PixelMplus / M+ Fonts, M+ Font License. See
  `data/fonts/PixelMplus-README.txt`.
- `data/fonts/LiberationSansNarrow-Bold.ttf`: Liberation Sans Narrow, GPL-2
  with font exception. See `data/fonts/LiberationSansNarrow-COPYRIGHT.txt`.
- `data/fonts/DejaVuSerif-Bold.ttf`: DejaVu Fonts, Bitstream Vera / public
  domain derivative notices. See `data/fonts/DejaVu-LICENSE.txt`.

## Requirements

- Python 3.10+
- Pillow: `python3 -m pip install --user pillow`
- Original BIN/CUE image in `iso/<release>/` (see below)

## Original Image

Images live one directory per release, named by the release slug, so
`data/releases/<slug>/` and `iso/<slug>/` line up and a second print of the same
game on the same console has somewhere of its own to go. Each release manifest
records its own image under `media.image`, and every tool resolves it from
there:

```text
iso/
  l5-ps1-jp/     SLPS-01818-9-B.bin  + .cue   # PPF patch, and the common reference
  l5-saturn-jp/  LANGRISSER_5.bin    + .cue   # Saturn build
  l4-ps1-jp/     SLPS-01818.bin      + .cue   # Langrisser IV
```

The `l5-ps1-jp` cue names its track file in upper case, so that directory also
holds an `SLPS-01818-9-B.BIN` symlink; the cue is authentic dump data and is not
edited to match.

| File | Size | CRC32 | MD5 | SHA-1 | SHA-256 |
| --- | ---: | --- | --- | --- | --- |
| `iso/l5-ps1-jp/SLPS-01818-9-B.bin` | `696248448` | `5d13a8df` | `7a9e431453fde9301188841f215bff98` | `e096604f2d4d69b48eb3c1b20ca5ea26e1ea8766` | `af3f5e1d6912f31f712d43cf71d954481fa9814021e62b41fdd8fce0c9429247` |
| `iso/l5-ps1-jp/SLPS-01818-9-B.cue` | `224` | `2683304f` | `455eca5422d06973bb32f7fed4ce2416` | `f2f2f1abf836e26acfd37030d7d9a378cca2a0de` | `754cfdc98d0aa354dd1d8cd0c5e4d377883a2acccf9636fd5e9826f1b1e52a66` |
| `iso/l5-saturn-jp/LANGRISSER_5.bin` | `507074736` | | | | `e517a65201ba9f087a14e2231ee3135acba173bc5041d1495fa333731e93dbc0` |
| `iso/l5-saturn-jp/LANGRISSER_5.cue` | `399` | | | | `58d09590a5399282f707536d6c154ecc19f60fd0cf8fa52d3d7beb375da65b52` |

The Saturn image is a mixed-mode disc, so its whole-file hash depends on the
rip's pregap convention. What must be verified is **data track 1** (every file
the patch touches lives there); it matches the Redump entry for `T-2509G`
v1.004 exactly:

| Track | Sectors | CRC-32 | MD5 | SHA-1 |
| --- | ---: | --- | --- | --- |
| 1 (Mode 1) | `61901` | `ef034bde` | `37685a3ac74ac252abb2d01ea6987c73` | `b90529e379efde5787693ffda6fff53fddd7c2ee` |

```bash
python3 -m langrisser.saturn_disc verify
```

## Repository Layout

| Path | Purpose |
| --- | --- |
| `langrisser/` | the toolkit: one module per tool, run as `python3 -m langrisser.<name>` |
| `scripts/release.sh` | release build driver |
| `data/common/` | shared maps, scenario map, UI constraints and JP table |
| `data/games/<code>/manifest.json` | game descriptor: glyph-plane map, curated table, pack root |
| `data/releases/<slug>/manifest.json` | release descriptor: medium, paths, offsets, font ceiling, dump fingerprint |
| `data/releases/<slug>/build_reference.json` | sha1 of every artifact the release builds, checked at the end of each build |
| `data/games/l4/font_map.csv` | Langrisser IV glyph slot→character map (derived + read from the plane) |
| `data/games/l4/lang/<lang>/` | Langrisser IV language packs |
| `data/platforms/<code>/` | console descriptors — the console itself, nothing game-specific |
| `data/engines/<code>/manifest.json` | container family: glyph geometry, container list, text codec |
| `data/releases/l5-saturn-jp/scen_mapping.json` | proven Saturn↔PS1 SCEN record correspondence |
| `data/releases/l5-saturn-jp/system_mapping.json` | proven Saturn↔PS1 SYSTEM entry correspondence |
| `data/releases/l5-saturn-jp/kanji_map.csv` | Saturn kanji slot→character map (its bank is reordered) |
| `data/games/l5/lang/<lang>/` | Langrisser V language packs (`en`, `ru`) |
| `<pack>/manifest.json` | language settings used by tools |
| `<pack>/SCEN/` | completed translated script chunks for that language |
| `<pack>/releases/<slug>/` | sparse target text only one release ships |
| `<pack>/system_strings.json` | target SYSTEM.BIN UI text overlay |
| `<pack>/system_layout.json` | SYSTEM.BIN line-growth constraints |
| `<pack>/title_credits.json` | language-specific title credits |
| `<pack>/names.csv` | item, class, spell, unit and NPC names |
| `<pack>/glossary.csv` | canonical glossary and recurring terms |
| `<pack>/font_slot_assignments.csv` | target glyph assignments |
| `<pack>/name_entry_grid.json` | target name-entry alphabet layout |
| `<pack>/review_status.csv` | per-record translation and JP cross-check status |
| `work/<game>/extracted/` | extracted game files, generated |
| `work/<game>/scriptdump/` | generated JP script dump, not tracked |
| `work/<game>/systemdump/` | generated SYSTEM.BIN string dump, not tracked |
| `work/<game>/wip_<lang>/` | partial translation staging area |
| `work/<game>/build/` | generated build files and previews |
| `patches/<patch_stem>_<lang>.ppf` | generated PPF output (`langrisser_v_ru.ppf`) |

Everything generated is scoped by game, because otherwise two games' builds
write the same files: `work/l5/build/SCEN.ru.DAT` and `work/l4/build/SCEN.ru.DAT`
are different discs' data under one name. The patch stem comes from the game
manifest, so Langrisser V keeps `langrisser_v_<lang>.ppf`.

Do not commit generated JP script dumps, extracted game files, build outputs or
partial translated chunks.

## Translation Model

Every target language is a language pack under its game's pack root
(`data/lang/<lang>/` for Langrisser V, `data/games/l4/lang/<lang>/` for
Langrisser IV; the root is a field of the game manifest). A pack contains
durable translation/editorial data only:

- completed SCEN chunks;
- target SYSTEM strings;
- names, glossary and review status;
- font assignments and name-entry layout;
- target title credits and non-reproducible graphic/cutscene transcript text.

The common translation is PS1-based. Console-specific structure lives under
its release pack; target text that only one build ships lives under that
pack's `releases/<slug>/`. A Saturn build reuses common
PS1 strings only when the platform mapping proves the entry correspondence.
If platform-specific mapping or target text is missing, the strict build fails
instead of silently preserving Japanese.

Generated Japanese source data stays under `work/` and is reproducible from the
user's own disc image. This avoids committing game text that can be extracted by
scripts.

English (`en`) and Russian (`ru`) are complete language packs. Additional
languages should start from `langrisser/init_lang.py` and follow the same
extraction, review, validation and build flow.

## Flow 1: Extract Source Data

Extract original game files:

```bash
mkdir -p work/l5/extracted
python3 -m langrisser.iso_mode2 iso/l5-ps1-jp/SLPS-01818-9-B.bin extract /L5/SCEN.DAT  work/l5/extracted/SCEN.DAT
python3 -m langrisser.iso_mode2 iso/l5-ps1-jp/SLPS-01818-9-B.bin extract /L5/SCEN2.DAT work/l5/extracted/SCEN2.DAT
python3 -m langrisser.iso_mode2 iso/l5-ps1-jp/SLPS-01818-9-B.bin extract /L5/SYSTEM.BIN work/l5/extracted/SYSTEM.BIN
python3 -m langrisser.iso_mode2 iso/l5-ps1-jp/SLPS-01818-9-B.bin extract /L5/IMG.DAT    work/l5/extracted/IMG.DAT
python3 -m langrisser.iso_mode2 iso/l5-ps1-jp/SLPS-01818-9-B.bin extract /SLPS_018.19  work/l5/extracted/SLPS_018.19
```

Dump source text and verify the no-edit round trip:

```bash
python3 -m langrisser.scendump
python3 -m langrisser.system_dump
python3 -m langrisser.verify_roundtrip
```

`system_dump` dumps whichever release it is pointed at: the byte order comes
from the platform, the group offsets from the release manifest, and the pack
ids from the release's `system_mapping.json`, so the budgets and indents are
that build's own while the ids stay the ones the translation is keyed by.

Generated JP source stays under `work/l5/scriptdump/` and `work/l5/systemdump/`.
These dumps are required for translation and building but are not committed.

The insert reports how much of the script it actually wrote
(`translated=10244/10244`) and names the chunks it left as the original. A
chunk with no translation file is inserted as nothing at all, so without that
count a missing file looks exactly like a finished one.

For Langrisser IV, point the same tools at disc A with `--game l4` (its files
live under `/L4`, and it has no `SCEN2.DAT`):

```bash
mkdir -p work/l4
python3 -m langrisser.iso_mode2 iso/l4-ps1-jp/SLPS-01818.bin extract /L4/SCEN.DAT   work/l4/SCEN.DAT
python3 -m langrisser.iso_mode2 iso/l4-ps1-jp/SLPS-01818.bin extract /L4/SYSTEM.BIN work/l4/SYSTEM.BIN
python3 -m langrisser.scendump     --game l4 --scen work/l4/SCEN.DAT --out-dir work/l4/scriptdump
python3 -m langrisser.system_dump  --game l4 --system-bin work/l4/SYSTEM.BIN --out work/l4/system_strings.json
```

Each game generates its own glyph plane, so a new game needs its own slot→
character map. The artwork is shared, so the map is derived mechanically by
matching tiles against an already-mapped game (this produced the bulk of `data/games/l4/font_map.csv`: 1628 of 1905
glyphs). The remaining 277 tiles are Langrisser IV's own kanji; they were read
from rendered contact sheets and each reading was verified in context against
real script lines, bringing the map to 1875 characters and 99.62% of the
script's tokens. The 30 non-characters (item icons, the segments of a
stretched `ー`, a sweat-drop mark, plane noise) are recorded in the same CSV
with an empty `char` and a group saying what they are:

```bash
python3 -m langrisser.derive_font_map --game l4 \
  --system work/l4/SYSTEM.BIN --reference-system work/l5/extracted/SYSTEM.BIN \
  --out-unmatched work/l4/font_map_unmatched.txt
```

## Flow 2: Prepare A Language Pack

Create a new language scaffold:

```bash
python3 -m langrisser.init_lang <lang> --label "Language Name"
```

By default this copies source structure while clearing target-language fields
and does not copy script chunks. Use `--copy-script` only for explicit
experiments.

Review or edit these files for the target language:

- `data/lang/<lang>/manifest.json`
- `data/lang/<lang>/font_slot_assignments.csv`
- `data/lang/<lang>/system_strings.json`
- `data/lang/<lang>/system_layout.json`
- `data/lang/<lang>/title_credits.json`
- `data/lang/<lang>/names.csv`
- `data/lang/<lang>/glossary.csv`
- `data/lang/<lang>/name_entry_grid.json`
- `data/lang/<lang>/manual_record_overrides.json`
- `data/lang/<lang>/poem_prologue.txt`
- `data/lang/<lang>/virash_monologue.json`

`system_strings.json` is a target-only `stable id -> translated text` overlay.
Offsets, budgets and Japanese source come from the generated
`work/l5/systemdump/system_strings.json`. Exact JP strings already present in the
language pack's `names.csv` or `glossary.csv` are inherited automatically during
the build; do not duplicate those canonical translations in the SYSTEM overlay.
Set `system_complete` in the language manifest only after the strict resolver
passes; later builds then reject any unresolved Japanese SYSTEM entry.

`system_layout.json` keeps conservative per-line growth limits and stable-id
exceptions required by longer target-language strings. Add exceptions only after
confirming that the affected UI field can display them.

The full patch build derives any missing target-language pairs into
`work/l5/build/font_slot_assignments.<lang>.csv`, rebuilds the font, rewraps a copy
under `work/l5/build/translation.<lang>/` with that exact table and validates it
before insertion. It never rewrites tracked translation sources. To persist a
new assignment baseline for review, run:

```bash
python3 -m langrisser.assign_font_slots --lang <lang>
python3 -m langrisser.build_font --lang <lang>
python3 -m langrisser.font_review
```

## Flow 3: Translate And Review

Work scenario by scenario:

```bash
python3 -m langrisser.scenario --lang <lang> list
python3 -m langrisser.scenario --lang <lang> dump 1
python3 -m langrisser.scenario --lang <lang> prefill 1
```

`prefill` writes partial chunks to `work/l5/wip_<lang>/SCEN/`. Move a chunk to
`data/lang/<lang>/SCEN/` only after it is fully translated and validates.

Source priority for translation and review:

1. Generated Japanese dump under `work/` is authoritative.
2. Existing English text is a cross-check, not a replacement for the Japanese.
3. The GameFAQs guide and fan terminology are secondary references.
4. If English conflicts with Japanese, Japanese wins and English should be
   corrected.

Per translation/review pass:

```bash
python3 -m langrisser.rewrap --lang <lang>
python3 -m langrisser.validate_terms --lang <lang> --require-complete
python3 -m langrisser.validate_translation --lang <lang>
python3 -m langrisser.review_html --lang <lang> --scenario 1
```

For English, verify the speaker extractor against the in-game test set:

```bash
python3 -m langrisser.check_speakers --lang en
```

For Russian, enforce speaker coverage and conservative plate width:

```bash
python3 -m langrisser.validate_terms --lang ru --require-complete --require-speakers --max-plate-chars 10
```

The review generator writes `work/l5/review/<lang>/index.html` and one page per
selected chunk. It shows JP, reference EN and target text together with speaker
plates, controls and page boundaries. Use `--scenario quiz`, `--scenario 1`, or
`--scenario opt:<name>` to follow play order; omit `--scenario` for the complete
non-empty script.

Review decisions are durable language-pack data in
`data/lang/<lang>/review_status.csv`. Set `target_done=1` only after the target
record is complete and `reference_checked=1` only after checking the reference
English record against JP. Generated review pages separately flag missing
records, control mismatches and residual Japanese.

Editing rules:

- Preserve control tags and control arguments.
- Move only safe line/page breaks: `<$FFFC>` and `<$FFFD>`.
- Keep choice records single-line.
- Use ordinary spaces and punctuation; the encoder selects compact glyph pairs.
- Keep normal hyphenated spelling; the allocator creates boundary pairs that
  prevent narrow hyphens from producing false visual spaces.
- Record meaning loss caused by byte budgets in `docs/COMPRESSION_DEBT.md`.

English reference guide:
https://gamefaqs.gamespot.com/saturn/562834-langrisser-v-the-end-of-legend/faqs/41339

## Flow 4: Build Patch

Mandatory shared checks:

```bash
python3 -m langrisser.verify_roundtrip
python3 -m langrisser.rewrap --lang <lang>
python3 -m langrisser.validate_terms --lang <lang> --require-complete
python3 -m langrisser.validate_translation --lang <lang>
python3 -m langrisser.build_ppf --lang <lang> --patch-version dev
```

Run `python3 -m langrisser.check_speakers --lang en` before English release
builds; Russian speaker coverage is enforced by
`langrisser/validate_terms.py --lang ru --require-complete --require-speakers --max-plate-chars 10`.

The PPF build automatically validates engine-specific SYSTEM UI constraints,
including startup-menu VRAM-atlas rows and other tight fixed-width fields.

Generated outputs for language suffix `<s>`:

- `work/l5/build/langrisser_v_<s>.bin`
- `patches/langrisser_v_<s>.ppf`
- generated DAT/SYSTEM/EXE intermediates under `work/l5/build/`
- preview images under `work/l5/build/`

Release build:

```bash
scripts/release.sh --release
```

The release script builds the complete English and Russian artifact set by
default, writes it to `dist/vX/`, and records PPF plus patched-image hashes in
`SHA256SUMS` and `MANIFEST.txt`. Use `--lang <lang>` to build a single language
or `--version <label>` for a non-tagged development release.

## Flow 5: Saturn Build

The Saturn release of Langrisser V runs the same language pack through a
console-specific back end. Nothing is translated twice: the pack stays
PS1-keyed, and `data/releases/l5-saturn-jp/` records what the two builds
share.

Verify the disc and extract the Saturn side once:

```bash
python3 -m langrisser.saturn_disc verify
for f in SYSTEM.DAT SCEN.DAT CLEAR.DAT TITLE1.DAT TITLE2.DAT OPEN.DAT; do
  python3 -m langrisser.saturn_disc extract $f work/l5/build/saturn/$f
done
```

Its own Japanese source reads out the same way the PS1 side does, one tool per
container:

```bash
python3 -m langrisser.system_dump   --release l5-saturn-jp \
    --system-bin work/l5/build/saturn/SYSTEM.DAT --out work/l5/systemdump/saturn.json
python3 -m langrisser.saturn_scen_text --scen work/l5/build/saturn/SCEN.DAT
```

Build:

```bash
python3 -m langrisser.saturn_build --lang ru
python3 -m langrisser.saturn_build --lang ru --remaster-disc
```

Two PS1-derived inputs remain, and neither is a correspondence: the Japanese
script dump under `work/l5/scriptdump/`, which the record validator compares
control tags against, and `work/l5/extracted/SCEN.DAT`, which `rewrap` reads for
the speaker-plate structure. Both describe the common script the pack is keyed
to rather than anything about the PS1 build. Which record and which string this
release carries, and where each glyph sits on it, all come from
`data/releases/l5-saturn-jp/` — recorded once by reconciliation, never
re-derived at build time. The builder runs, in order: platform text
overrides → native-glyph plan → Saturn-side slot usage scan (which glyphs this
build still draws, so the font never takes their slots) → font → reflow
and validation → SYSTEM pack (+ write-contract check) → name entry → Now
Loading → SCEN insert → SCENARIO CLEAR, title credits and the prologue poem.

Strict mode stops on any unresolved `data/releases/l5-saturn-jp/` mapping gap;
`--allow-unmapped` is a diagnostic only. The non-remaster command emits
translated extracted files under `work/l5/build/saturn/`; `--remaster-disc` emits
a translated mixed-mode Saturn BIN/CUE in the same directory. Saturn output
grows `SCEN.DAT` and `OPEN.DAT`, so the generated `.cue` is part of the build
artifact.

### Reconciliation: filling `data/` from another release's disc

A Saturn build reads its own disc plus `data/` and nothing else. What it needs
to know about the PS1 print — which pack record each script entry carries,
which pack string each SYSTEM entry carries, what each reordered glyph slot is —
is compared once and written down. Reconciliation is the only step allowed to
open another release's disc, it is not part of any build, and it is re-runnable:
what it derived is marked `proved` and rebuilt, while hand-written decisions are
carried through untouched.

```bash
python3 -m langrisser.derive_font_map --game l5 --release l5-saturn-jp \
    --bank-only --system work/l5/build/saturn/SYSTEM.DAT   # the glyph bank
python3 -m langrisser.saturn_reconcile scen                # script records
python3 -m langrisser.saturn_reconcile system              # SYSTEM entries
python3 -m langrisser.saturn_reconcile glyphs              # slots no map names
```

The glyph bank is where a build learns which slot holds each *character*, and
that answers almost everything: the release's own map plus the game's is enough
to point the encoder at the right slot without ever opening the PS1 disc. The
exception is a glyph with no character — an icon the text names by raw
`<$XXXX>` token — which no map can place; `glyphs` confirms the slot recorded
for it in the release manifest.

Where the Saturn originals diverge from PS1 (edited lines, pad buttons, the
save menu), the audits list what still needs Saturn-specific translation:

```bash
python3 -m langrisser.saturn_scen_audit   --write-mapping   # SCEN records
python3 -m langrisser.saturn_system_audit --write-mapping   # SYSTEM entries
```

They emit `work/l5/build/saturn/scen_platform_review.md` with the Saturn original
decoded through `data/releases/l5-saturn-jp/kanji_map.csv`, the closest PS1 record
and its current translations — everything needed to author the platform record.

## Important Constraints

- PS1 disc files must not grow. SCEN/SCEN2 chunk relocation is allowed only
  inside the original fixed file sizes. Saturn uses a separate remastering path
  when translated `SCEN.DAT` grows.
- `SCEN` is the canonical script source. `SCEN2` text is byte-identical and is
  rebuilt from the same language-pack chunks.
- The font atlas ends at glyph 1820 on PS1; later SYSTEM.BIN words are menu
  data. On Saturn the last writable slot is 1819 — slot 1820 would overwrite
  the `SYSTEM.DAT` group pointer directory at `0x8000`
  (`max_font_slot` in the release manifest — the plane's own layout sets it,
  not the console).
- Existing work is reused, never assumed: a Saturn entry takes the already
  translated PS1 text only when both Japanese originals are provably identical
  as normalized text (kana/ASCII plus the derived Saturn kanji map). Where they
  differ, the Saturn text is its own and the strict build fails until it is
  written. That comparison happens once, in reconciliation; a build reads the
  answer from the release mapping.
- Each console has its own budgets. A group packs into its own byte span and
  every line into its own cell width, so a shared translation may need a
  shorter platform form on one console (`space_override` in the SYSTEM
  mapping).
- Control words and argument words must survive translation in order.
- Target punctuation must exist in the native map or be allocated by the
  language pack.

## Documentation

- `AGENTS.md`: non-negotiable project rules for coding agents.
- `docs/PLAN.md`: active Russian editorial and EN cross-check plan.
- `docs/IMPLEMENTED.md`: completed toolkit, English and multilingual milestones.
- `docs/INTERNAL_DATA_FORMATS.md`: format index and verified binary notes.
- `docs/LANGUAGE_PACK_FORMAT.md`: language-pack structure.
- `docs/RU_TERMINOLOGY.md`: canonical Russian names and terminology policy.
- `docs/SYSTEM_BIN_FORMAT.md`: SYSTEM.BIN string groups.
- `docs/IMG_DAT_FORMAT.md`: IMG.DAT image container.
- `docs/BATTLE_SUFFIX_FORMAT.md`: battle chunk suffix payloads.
- `docs/SPEAKER_NAME_EXTRACTION.md`: speaker plate extraction and wrapping.
- `docs/SATURN_DISC_FORMAT.md`: Saturn disc, SYSTEM/SCEN containers, tilemap
  title screens and the Saturn build flow.
