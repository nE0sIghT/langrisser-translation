# Agent instructions

Toolkit for translating Langrisser V (PS1, SLPS-01818). Read `README.md`
first for the human translation workflow; `docs/PLAN.md` holds active work,
`docs/IMPLEMENTED.md` records completed milestones, and the format-specific
documents hold verified binary details. This file lists the rules that must not
be broken.

## Hard invariants

- **File sizes never change.** The disc has CD audio right after the data
  track. Never call `iso_mode2.py` with `--allow-grow`; never let
  `SCEN.DAT`/`SCEN2.DAT`/`SYSTEM.BIN` grow. Text growth first uses the
  chunk's trailing zero padding; beyond that the fixed-size repack
  relocates chunks inside the file (validator status `REPACK` is fine as
  long as the file-level totals stay `OK`).
- **Font atlas ends at glyph 1820.** Tiles 1821+ in `SYSTEM.BIN` are a menu
  offset table, not glyphs. Writing there breaks menus.
- **Control words are sacred.** Every `<$XXXX>` tag ≥ `0xE000` (except the
  soft breaks `FFFC`/`FFFD` and highlight toggles `FFF4`/`FFF3`) and every
  argument word of `F600`/`FBxx` must survive translation in order.
  `langrisser/validate_translation.py --lang <lang>` enforces this — keep it green.
- **SCEN and SCEN2 text blocks are byte-identical.** Translate
  `data/games/<game>/lang/<lang>/SCEN/` only; the inserter reuses that dump when rebuilding
  `SCEN2.DAT`.
- **No partially translated chunks in `data/games/<game>/lang/<lang>/SCEN/`.**
  Untranslated kanji whose glyph slots were sacrificed for target-language
  glyphs fail the encode step and break the build. Work-in-progress lives in
  `work/wip_<lang>/`
  (`langrisser/tm_prefill.py` writes there).

## Mandatory checks before claiming success

```bash
python3 -m langrisser.verify_roundtrip   # byte-identical no-edit pipeline
python3 -m langrisser.rewrap --lang en             # window-width line wrapping
python3 -m langrisser.check_speakers --lang en     # speaker plates vs the in-game test set
python3 -m langrisser.validate_terms --lang en --require-complete
python3 -m langrisser.validate_terms --lang ru --require-complete --require-speakers --max-plate-chars 10
python3 -m langrisser.validate_translation --lang en        # tags, encodability, budgets
python3 -m langrisser.build_ppf --lang en          # full build must succeed
```

Speaker/author correctness is **mandatory**: `langrisser/check_speakers.py` must print
`OK`. It verifies that `semantic_plate_slots` resolves each record in
`docs/SPEAKER_TEST_SET.md` to the in-game–verified speaker; a wrong speaker means
a wrong plate reserve and wrong wrapping. A failure is a bug in the extractor —
fix the extractor, never relax the expected value. When you confirm a plate in
game, add a row to `docs/SPEAKER_TEST_SET.md`.

## Translation conventions

- Source of meaning: the JP dump in `work/scriptdump/`. `work/translation.txt`
  (borgor's GameFAQs guide:
  https://gamefaqs.gamespot.com/saturn/562834-langrisser-v-the-end-of-legend/faqs/41339)
  may be copied verbatim where its wording fits the JP line and the byte budget;
  otherwise rephrase.
- Names and terms: `data/games/<game>/lang/<lang>/names.csv` and
  `data/games/<game>/lang/<lang>/glossary.csv` are canonical; follow the
  Langrisser fan canon for series terms.
- Text windows (dialogue, narration/briefing, quiz) are 21 cells wide
  (measured in-game) and the player-name macro `<$F600><$0000>` renders up
  to 8 cells (the name entry limit). The engine draws the speaker plate
  inline at the start of the window, so the re-wrapper reserves speaker-plate
  width on the first line of a plated spoken record. Continuation pages after
  `<$FFFD>` do not redraw the plate and wrap at full width. When decoded VM
  display rows identify a record's plate slot, use that exact width; otherwise
  reserve the widest plate of the chunk's speaker pool (its size comes from
  the chunk VM header in `SCEN.DAT`). Keep speaker plate names at 5 cells or
  less so that fallback reserve stays tight (titles like "Marshal" are
  dropped from plates, not from dialogue text).
  Pages of up to 4 lines are safe (the JP script uses them routinely).
  Choice records (`・...`) must stay single-line — a wrapped tail becomes
  a phantom selectable row. Multi-bullet objective records keep their
  structure.
- Punctuation support is language-pack specific. A mark is usable only when it
  exists in the native map or is allocated through that pack's `single_chars`
  and is present in its bundled font. EN allocates `";!?/`; RU allocates
  `«»—;!?/`. Prefer the ordinary `-` in translated dialogue; use `—` only
  when it materially improves a poetic line, never as habitual prose
  punctuation. Ellipsis is the single-cell `…` (a trailing period merges into
  it: `…` not `….`).
  Write ordinary spaces in translated text; the encoder may fold them into
  narrow `space+letter`, `letter+space`, `punctuation+space`, and
  `letter+punctuation` glyphs when those assignments exist. Do not hand-remove
  spaces to save bytes.
- Tight chunks: if the validator says OVER BUDGET, shorten the text; never
  drop records or tags to make it fit.
- Compression debt: if byte-budget pressure forces wording that drops nuance,
  tone, tutorial detail, or lore detail, record the affected chunk/record in
  `docs/COMPRESSION_DEBT.md` before committing.

## Repository conventions

- Commit author: `Yuri Konotopov <ykonotopov@gmail.com>`. Include a
  `Co-Authored-By` trailer identifying the agent. Messages: functional
  English, present tense.
- Keep code, comments and documentation in English. Target-language text belongs
  only under the corresponding `data/games/<game>/lang/<lang>/` pack.
- `work/`, `iso/`, `patches/`, `archive/`, `external/` are not in git;
  everything under `data/`, `langrisser/` and `scripts/` is.
- Work scenario by scenario, not by raw chunk number:
  `langrisser/scenario.py --lang <lang> list/chunks/dump/prefill` maps scenario K
  to its chunks (scene `44+K`, battle `K`, scene `86+K`; see
  `data/common/scenario_map.json`).
- After translating a chunk: rewrap → validate → build → regenerate review
  pages (`langrisser/review_html.py`) → commit the `SCEN` chunk and any related
  durable data/docs together.
