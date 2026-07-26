# Saturn vs PS1 Build Parity Gaps

This document is a delta report, not another format description. The baseline
format details remain in:

- `README.md` and `AGENTS.md` for the PS1 translation workflow and invariants.
- `docs/SYSTEM_BIN_FORMAT.md` for PS1 SYSTEM text and UI constraints.
- `docs/IMG_DAT_FORMAT.md` for PS1 IMG.DAT graphics.
- `docs/SATURN_DISC_FORMAT.md` for Saturn disc, SCEN, SYSTEM and graphics
  container findings.

The parity target is strict: the Saturn flow should cover the same translation
tasks as the PS1 flow, with shared logic in common modules and only container /
platform adapters kept platform-specific. Extra features such as staff/cast
screens or Virash overlay subtitles are not parity requirements unless the PS1
release flow also patches them.

## Current Matrix

| PS1 flow task | PS1 path | Saturn path | Saturn status | Gap reason | Proposed action |
| --- | --- | --- | --- | --- | --- |
| Source extraction | `iso_mode2.py` extracts PS1 files to `work/l5/extracted/` | `saturn_disc.py extract` extracts Saturn files to `work/l5/build/saturn/` | Partial | Saturn extraction exists, but there is no single Saturn equivalent to the PS1 release/extract bootstrap. | Add a Saturn extraction/bootstrap command or document the required extraction set in one release/build script. |
| No-edit roundtrip | `langrisser/verify_roundtrip.py` covers PS1 SCEN/SYSTEM no-edit paths | SCEN no-edit model is documented; SYSTEM and graphics have individual tooling | Partial | Roundtrip proofs are split across tools/docs instead of one mandatory build gate. | Add a Saturn no-edit verification driver that calls the existing per-container checks. |
| Font slots | `langrisser/assign_font_slots.py` -> generated assignments -> `langrisser/build_font.py` | Saturn build now runs the same generated-assignment stage, then writes `SYSTEM.DAT` glyphs | Implemented | The shared assignment stage uses the PS1 common source and a Saturn build-copy table. | Keep platform-specific SYSTEM overlays sparse; extend allocator source handling only when real Saturn-only strings are added. |
| SCEN text | `langrisser/sceninsert.py --fixed-size-repack` writes all PS1 SCEN/SCEN2 text | `langrisser/saturn_apply.py` writes Saturn `SCEN.DAT` field_3c pools through strict platform mapping | Implemented (`125/131` translated, 6 service preserved) | Saturn entry-order/content deltas are fully represented in `data/releases/l5-saturn-jp/scen_mapping.json`. | Keep future Saturn-only deltas as sparse platform overlays; strict mode must stay green. |
| SYSTEM text | `langrisser/system_dump.py` -> resolver -> reflow -> strict `langrisser/system_pack.py --repack` | `langrisser/saturn_system_pack.py` packs all Saturn groups through explicit platform mapping | Implemented (`16/16`) | Saturn-only RAM/save strings and compact Saturn-only class labels are stored as sparse overlays. | Add runtime review rows if any Saturn-only SYSTEM string needs wording changes. |
| Build-copy wrapping | PS1 build rewraps `work/l5/build/translation.<lang>/` with the exact generated `.tbl` | Saturn build rewraps `work/l5/build/translation.<lang>.saturn/` with the Saturn `.tbl` | Implemented | The tracked language pack is never rewritten. | None. |
| Translation validation | PS1 build validates control words, encodability and budgets under exact `.tbl` | Saturn build validates the same generated translation copy under the Saturn `.tbl` | Implemented for current data | No populated sparse SCEN override chunks exist yet. | Add validation for sparse Saturn override chunks when they are populated. |
| SYSTEM UI validation | `langrisser/validate_system_ui.py` checks atlas rows and fixed-width fields | Saturn build runs the common PS1 SYSTEM UI validator after applying sparse Saturn overlays | Implemented for current data | No runtime-only Saturn UI geometry issue is known. | Add Saturn-specific constraints only if runtime proves a different geometry. |
| Name-entry screen | `langrisser/patch_name_entry.py` patches SYSTEM grid and PS1 executable input table | `saturn_name_entry.py` patches two tables in `SYSTEM.DAT` | Static implemented | Display and input tables are located; runtime cursor / OK / cancel behaviour still needs confirmation. | Runtime-check only those behaviours. If it passes, treat the adapter as parity-complete. |
| Title credits | `langrisser/imgdat.py title-credits` patches IMG.DAT title assets | `saturn_title_credits.py` patches `TITLE1.DAT` | Implemented | None known for parity; platform container differs. | Keep shared text rendering in common code; keep only detile/retile in Saturn adapter. |
| Prologue poem | `langrisser/poem_translate.py` patches IMG.DAT poem assets using shared renderer | `saturn_poem_translate.py` patches `OPEN.DAT[2]` using shared renderer | Implemented | None known for parity; platform container differs. | Keep shared renderer; keep run-atlas packing Saturn-specific. |
| Scenario-clear banner | `langrisser/scenario_clear.py` patches IMG.DAT asset 9 using shared banner renderer | `saturn_scenario_clear.py` patches `CLEAR.DAT` | Implemented | None known for parity; platform container differs. | Keep shared banner renderer; keep CLUT/texture container Saturn-specific. |
| Now Loading plate | `langrisser/now_loading.py` patches IMG.DAT asset 0 | `saturn_now_loading.py` patches a compressed `SYSTEM.DAT` stream | Static implemented, runtime pending | The codec round-trips and fits, but the edited stream has not been runtime-confirmed after the latest review. | User runtime-checks the translated plate. If it does not appear, locate the alternate source/cache path before changing docs to "done". |
| Disc output | PS1 injects fixed-size files into a copied BIN and emits PPF3 | Saturn remasters mixed-mode BIN/CUE with shifted track indices | Implemented structurally, runtime pending | Current Saturn output grows track 1 because translated `SCEN.DAT` grows. | Runtime-check the generated BIN/CUE; release as xdelta plus generated cue unless a future fixed-size audit succeeds. |
| Release packaging | `scripts/release.sh` emits PPFs, hashes and manifest | Saturn BIN/CUE build exists, but no release/xdelta packaging script exists | Missing | Runtime confirmation and release packaging are separate from the build pipe. | Add xdelta release packaging after runtime smoke test. |

## SCEN Divergence Report

The strict Saturn mapper now covers every translatable Saturn `SCEN.DAT` text
entry. It applies 125 translated blocks, explicitly preserves the 6 known
service/name-pool blocks, and leaves no unresolved chunks in
`data/releases/l5-saturn-jp/scen_mapping.json`. No Japanese source text is stored in
the mapping file.

Resolved mapping classes:

| Class | Chunks | Resolution |
| --- | --- | --- |
| Identity/prefix-aligned blocks | Most chunks with matching speaker/control sequence | Automatic mapping to PS1 record `entry+1`. |
| Unique stable-token subsequence blocks | `3`, `9`, `15` | Automatic alignment using kana/ASCII/punctuation/control words only. |
| PS1-only deletion deltas | `0`, `10`, `11`, `17`, `20`, `22`, `24`, `27`, `29`, `34`, `40`, `80` | Explicit range maps skip records present only in the PS1 script. |
| Local reorder/source-revision deltas | `4`, `16`, `19`, `21`, `25`, `26`, `28`, `30`, `31`, `32`, `33`, `35`, `38` | Explicit durable `saturn -> ps1` ranges/entries, plus `preserve` for verified service entries. |

Service chunks that are intentionally not language-pack chunks:

| Chunk | Saturn entries | PS1 records | Current state | Action |
| ---: | ---: | ---: | --- | --- |
| 43 | 8 | 8 | Name-pool/dummy block: Sigma, Lambda, Clarett, Alfred, Brenda, Lanford Marshal, two bullet terminators. | Listed in `empty_chunks`; preserved. |
| 44 | 8 | 8 | Same service block. | Listed in `empty_chunks`; preserved. |
| 81 | 8 | 8 | Same service block. | Listed in `empty_chunks`; preserved. |
| 123 | 8 | 8 | Same service block. | Listed in `empty_chunks`; preserved. |
| 127 | 8 | 8 | Same service block. | Listed in `empty_chunks`; preserved. |
| 128 | 8 | 8 | Same service block. | Listed in `empty_chunks`; preserved. |

## SYSTEM Divergence Report

The Saturn SYSTEM packer now translates all 16 text tables in strict mode. The
tables below were the structural blockers and are now covered by
`data/releases/l5-saturn-jp/system_mapping.json`. Table numbers are only local report
indices; use offsets for durable references.

| Saturn table | PS1 table | Saturn count | PS1 count | Resolved action | Notes |
| ---: | ---: | ---: | ---: | --- | --- |
| `0x08084` | `0x08052` | 292 | 291 | Mixed direct PS1 map, explicit `preserve` for the name-entry grid/control runs, and Saturn overlays for `START`/RAM labels. | `saturn_name_entry.py` rewrites the preserved grid after SYSTEM packing. |
| `0x09004` | `0x08FAE` | 44 | 41 | Saturn-only RAM/save text overlay. | Decorative separator entry is preserved. |
| `0x0A854` | `0x0A8EC` | 33 | 33 | Direct map plus two compact Saturn-only RU labels to fit the fixed group budget. | The group now fits in 159 words. |
| `0x16D3C` | `0x16DC0` | 88 | 92 | Four explicit PS1 range mappings covering the reordered command-help blocks. | No Saturn-only target text needed. |

## Common-Layer Refactor Targets

These are analysis findings, not implementation steps already taken.

| Area | Current state | Gap | Target shape |
| --- | --- | --- | --- |
| Font assignment | Both PS1 and Saturn builds generate build-copy assignments. | Saturn-only overlay strings are not yet fed into assignment source. | Add platform overlay source handling when real overrides are populated. |
| Rewrap/validate | Both PS1 and Saturn builds rewrap/validate build copies with the exact generated table. | Sparse platform override chunks are not yet validated because none are populated. | Validate platform override chunks when added. |
| SYSTEM resolving | Saturn build regenerates the PS1 common SYSTEM source/resolved map before packing. | Implemented for current known Saturn SYSTEM deltas. | Extend `data/releases/l5-saturn-jp/system_mapping.json` only if new Saturn-only SYSTEM strings are identified. |
| Graphics rendering | Title, poem, clear and Now Loading already reuse several render cores. | Container adapters still import PS1 image helpers directly in places. | Keep rendering/palette helpers common; keep only container decode/encode per platform. |
| Release | PS1 has `release.sh`; Saturn has build-script remastered BIN/CUE output but no release package. | No reproducible xdelta artifact. | Add Saturn release mode after runtime smoke test; use xdelta as the binary patch format. |

## Fixed-Size vs Remaster Audit

The PS1 invariant is that edited files keep their file-level size and the `.cue`
does not change. The current Saturn build grows `SCEN.DAT`, remasters the
mixed-mode image, and shifts track indices. A future fixed-size audit may still
try to avoid the cue change, but the working build path is remastering.

Required analysis before changing release shape away from remastering:

1. Measure the translated size pressure per Saturn SCEN block after all mapping
   gaps are resolved.
2. Check whether Saturn `SCEN.DAT` has enough internal padding / reallocatable
   block space for a fixed-size file-level repack.
3. Check whether `SYSTEM.DAT`, `TITLE1.DAT`, `OPEN.DAT`, `CLEAR.DAT` and the Now
   Loading stream stay fixed-size after final parity changes.
4. If all edited files can stay fixed-size, emit xdelta for the `.bin` and keep
   the original `.cue`.
5. If `SCEN.DAT` must grow, emit xdelta for the remastered `.bin` and distribute
   the generated `.cue`.

## Runtime Checks Needed

Only checks with real remaining risk are listed:

| Area | Real risk | Check owner |
| --- | --- | --- |
| Saturn name entry | Display grid and input table might not cover cursor order, OK/cancel, or a hidden whitelist. | Runtime check by user. |
| Saturn Now Loading | Static stream decode/encode is implemented, but edited stream use is not yet confirmed after review. | Runtime check by user. |
| Remastered Saturn image | If fixed-size is not achieved and tracks shift, the generated cue/remaster must boot and reach affected scenes. | Runtime check by user after build artifact exists. |

## Patch Format

Use `xdelta3` for Saturn. It works for both fixed-size and remastered/grown BIN
outputs. The release shape depends on the fixed-size audit:

- Fixed-size output: `langrisser_v_saturn_<lang>.xdelta`; original `.cue`
  unchanged.
- Remastered output: `langrisser_v_saturn_<lang>.xdelta` plus generated
  `langrisser_v_saturn_<lang>.cue`.

PPF is not the preferred Saturn release format because the current Saturn path
may change BIN size and cue track indices. BPS is possible but less attractive
for large CD images.

## Next Analysis Tasks

1. Runtime-check remastered Saturn BIN/CUE output for both languages.
2. Add xdelta release packaging once runtime smoke tests pass.
3. Add validation for populated sparse platform override chunks/strings when
   such overrides are introduced.
