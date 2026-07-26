# Implemented Toolkit Milestones

This document records completed project work. Active work belongs in
`docs/PLAN.md`; detailed binary formats belong in the format-specific documents
linked below.

## Script Container

- Corrected the historical +2-byte record-boundary error in SCEN text blocks.
- Implemented strict SCEN/SCEN2 chunk and text-block parsing.
- Implemented per-chunk UTF-8 dump and insertion with preserved control words.
- Added a byte-identical no-edit round-trip check.
- Confirmed that SCEN and SCEN2 use byte-identical script text; the language
  pack stores only SCEN chunks and the inserter reuses them for SCEN2.
- Implemented fixed-size chunk relocation and whole-file repacking without
  changing SCEN/SCEN2 or disc-image file sizes.
- Preserved 4-byte battle-suffix alignment after text-block growth.

Details: `docs/INTERNAL_DATA_FORMATS.md`, `docs/BATTLE_SUFFIX_FORMAT.md`, and
`docs/DISASM_SUMMARY.md`.

## Text And Speakers

- Confirmed that printable text tokens directly select glyph indices.
- Identified the relevant control words and argument-bearing commands.
- Implemented control-signature validation.
- Implemented speaker-pool and per-record plate extraction used by the line
  wrapper.
- Added an in-game verified speaker regression set.
- Implemented dialogue, narration, quiz, choice and continuation-page wrapping.

Details: `docs/SPEAKER_NAME_EXTRACTION.md` and
`docs/SPEAKER_TEST_SET.md`.

## Font And UI

- Extracted and mapped the native 12x12, 1bpp SYSTEM.BIN font plane.
- Confirmed that glyph slots end at 1820 and protected the following menu data.
- Implemented target-language glyph allocation and font rendering.
- Implemented compact pair glyphs and spacing/punctuation composites.
- Implemented the name-entry grid and executable table patch.
- Reverse-engineered SYSTEM.BIN string groups and verified table-index runtime
  addressing.
- Implemented offset-table-aware SYSTEM dump and fixed-size repack.
- Added per-language SYSTEM line-growth limits with stable-ID exceptions, so
  verified long fields do not weaken validation for unrelated UI strings.

Details: `docs/SYSTEM_BIN_FORMAT.md` and
`docs/NAME_ENTRY_ALPHABET.md`.

## Graphics

- Implemented generic IMG.DAT extraction and in-place asset replacement.
- Added title-screen credits and QR code generation for assets 10 and 11.
- Added prologue-poem rendering into its continuous three-panel scroll.
- Added the Virash cutscene subtitle asset flow.

Details: `docs/IMG_DAT_FORMAT.md` and
`docs/VIRASH_CUTSCENE_SUBTITLES.md`.

## English Release

- Completed the startup quiz and tutorial.
- Completed main scenarios 1-36.
- Completed optional scenarios 38-42 and their paired scenes.
- Completed recap and biography chunks 129-130.
- Translated menu/UI text, names, descriptions, save messages and battle text.
- Added English font, name entry, prologue poem, Virash subtitles and title
  credits.
- Produced a fixed-size PPF build without growing any disc file.

The voice-cast name list is translated; the remaining staff roll stays
Japanese. Remaining English work is cross-checking against Japanese and
editorial/runtime polish, not initial coverage.

## Multilingual Build Architecture

- Split shared data into `data/common/` and target packs into
  `data/games/<game>/lang/<code>/`.
- Added manifest-driven path and output resolution.
- Made build, font, validation, review and scenario tools language-selectable.
- Replaced EN-specific target fields with neutral `text` and `char` schemas.
- Added clean-language scaffolding without copied script chunks.
- Kept reproducible JP SCEN and SYSTEM dumps under ignored `work/` paths.
- Converted durable SYSTEM translations to target-only stable-ID overlays; the
  generated metadata and Japanese source remain under `work/l5/systemdump/`.
- Added scenario-oriented JP/EN/target HTML review pages with speaker plates,
  control/page structure, automatic structural flags and durable per-record
  translation/cross-check status.
- Populated and validated the Russian class, creature, equipment, spell,
  military-role and proper-name terminology, including every SCEN speaker-pool
  key and the five-cell plate-width constraint.

## Russian SYSTEM/UI

- Translated the complete Russian SYSTEM/UI corpus, including menus, battle and
  preparation prompts, save/load messages, names, classes, units, spells,
  equipment, skills and their description cards.
- Added strict completeness validation for SYSTEM translations.
- Added canonical inheritance from each language pack's names and glossary,
  avoiding duplicate context-free translations in SYSTEM overlays.
- Added deterministic target-font-aware reflow for fixed four-line unit, item
  and magic description cards.
- Preserved fixed SYSTEM.BIN size and verified complete EN and RU builds.
- Cross-checked all 95 equipment slots and 94 unique description cards against
  Japanese, correcting damaged EN/RU line joins, inaccurate item names and
  three EN action-cost values that had been mistaken for percentages.

## Russian Scenario Translation

- Translated scenario 1 from Japanese, including its opening scene, battle,
  branching dialogue, item events, post-battle scene and complete tactical
  tutorial.
- Cross-checked every scenario 1 record against the English pack and restored
  the JP modification/cloning detail that the English text had omitted.
