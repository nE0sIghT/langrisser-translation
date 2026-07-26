# Language Pack Format

Target-language data lives under `data/games/<game>/lang/<code>/`. The build does not read
translated chunks, SYSTEM strings, name tables or font slots from global EN
paths; it resolves them through `manifest.json`.

Generated source dumps stay under `work/`:

- `work/l5/scriptdump/` — JP SCEN/SCEN2 script dump.
- `work/l5/systemdump/` — SYSTEM.BIN string inspection dumps.
- `work/l5/extracted/` — files extracted from the original BIN.

Do not commit those generated dumps. They are reproducible from the user's own
disc image.

## Required Files

```text
data/games/<game>/lang/<code>/
  manifest.json
  SCEN/
  platforms/
  font_slot_assignments.csv
  system_strings.json
  system_layout.json
  title_credits.json
  names.csv
  glossary.csv
  name_entry_grid.json
  manual_record_overrides.json
  review_status.csv
  poem_prologue.txt
  poem_prologue_jp.txt
  virash_monologue.json
  releases/
    l5-saturn-jp/
      SCEN/
      system_strings.json
```

`SCEN/` contains only completed translated chunks. Work-in-progress chunks live
in `work/l5/wip_<code>/SCEN/`.

`releases/<slug>/` contains target-language text that exists only in that
release. It is keyed by release rather than by console because the delta is
against one shipped build: two builds of the same game on the same console
would each need their own. Keep the directory empty unless that release's
mapping explicitly sends an entry to it. The shared translation stays in
`SCEN/` and `system_strings.json`; do not duplicate common strings in an
override.

Current override shape:

```text
data/games/<game>/lang/<code>/releases/l5-saturn-jp/
  SCEN/                 # sparse chunk_NNN.txt files for entries only this build has
  system_strings.json   # sparse SYSTEM id -> target text overlay
```

Language-specific data uses neutral target fields:

- `font_slot_assignments.csv`: `index_dec,char,replaced_char`;
- `names.csv`: `jp,text,alt` (`alt` is an optional target-language short form);
- `glossary.csv`: `jp,guide_en,text,note`;
- `system_strings.json`: object mapping generated stable ids to target text;
- `system_layout.json`: default and per-stable-id SYSTEM line-growth limits;
- `title_credits.json`: one to three language-specific title-credit templates;
- `virash_monologue.json`: each cue stores its translation in `text`.
- `review_status.csv`: record-level target completion and reference-vs-JP
  review decisions.

`guide_en` is explicitly an English reference-source field, not the selected
language's output field.

`system_strings.json` intentionally contains no extracted Japanese text or
offset metadata. `langrisser/system_dump.py` regenerates those fields under
`work/<game>/systemdump/system_strings.json`; the packer joins the durable
overlay to that source by ids such as `group:0:1` and `loose:3`.

An id names *where a string sits*, not where it lands: which group it is in and
which entry of that group, or which text run outside the tables. Those numbers
are the same on every build of the game, so one translation serves them all.
Each release records its own group offsets in its manifest (`system_groups`),
and that list is what turns an id back into an address on that build — which
is also why the dumpers fail loudly if a scan disagrees with it, instead of
silently changing what every id means.

## Manifest

Fields currently consumed by the tools:

| Field | Meaning |
| --- | --- |
| `lang` | Language code. |
| `label` | Human-readable language name. |
| `patch_suffix` | Output suffix: `langrisser_v_<suffix>.ppf`, table `lang5_<suffix>.tbl`. |
| `patch_description` | PPF3 description string. |
| `script_dir` | Relative path to translated SCEN chunks, normally `SCEN`. |
| `font_assignments` | Relative path to target glyph assignments CSV. |
| `system_strings` | Relative path to SYSTEM.BIN UI translation JSON. |
| `system_layout` | Relative path to SYSTEM.BIN line-growth constraints JSON. |
| `system_complete` | Fail the build when any Japanese-bearing SYSTEM entry remains unresolved. |
| `names` | Relative path to name table CSV. |
| `glossary` | Relative path to glossary CSV. |
| `review_status` | Relative path to record-level translation review CSV. |
| `font` | Font path for rendering target glyph slots, relative to the language root. |
| `font_size` | TTF render size for font-slot rendering. |
| `caps_font` | Optional separate font for single uppercase glyphs in all-caps runs. |
| `caps_font_size` | Render size for `caps_font`. |
| `single_chars` | Characters that must receive glyph slots even before script text uses them; this includes target-specific punctuation not present in the native map. |
| `forced_pairs` | Optional two-character glyphs that must be allocated, such as compact UI labels. |
| `window_width` | Dialogue window width in cells. |
| `choice_width` | Choice-row width in cells. |
| `max_lines` | Safe page height for rewrap checks. |
| `assets` | Screens this pack's game happens to have (see below). |

The top-level fields above apply to any target language of any game. Screens a
particular game has live in `assets`, so a new game's pack neither inherits
keys that mean nothing to it nor makes the loader grow a field per screen:

| `assets` key | Meaning |
| --- | --- |
| `title_credits` | Relative path to title-credit templates JSON. |
| `name_entry_grid` | Relative path to name-entry layout JSON. |
| `manual_record_overrides` | Relative path to quiz/bootstrap overrides. |
| `poem` | Relative path to translated prologue poem text. |
| `poem_source` | Relative path to recognized original poem text. |
| `virash_monologue` | Relative path to Virash monologue cue JSON. |
| `scenario_clear` | Optional translated IMG.DAT asset 9 banner text; empty or absent preserves the original graphic. |
| `now_loading` | Optional translated IMG.DAT asset 0 loading-plate text; empty or absent preserves the original graphic. |

## Release Packs

Per-build structure lives under `data/releases/<slug>/`. These files store
mappings and layout decisions only; they must not contain extracted Japanese
source text. `data/platforms/<platform>/` holds the console descriptor and
nothing game-specific.

```text
data/releases/
  l5-ps1-jp/
    manifest.json
    build_reference.json
  l5-saturn-jp/
    manifest.json
    build_reference.json
    scen_mapping.json
    system_mapping.json
    kanji_map.csv
```

The common language pack is PS1-based because the existing translation and
review data were built from the PS1 `SCEN.DAT` and `SYSTEM.BIN` dumps. A
non-PS1 build may reuse common target strings only when platform mapping proves
that a platform entry corresponds to a PS1 chunk record or SYSTEM stable id.

`scen_mapping.json`:

- `empty_chunks`: service/name-pool chunks that are preserved and not counted
  as missing translations;
- `unresolved_chunks`: durable no-source-text tracker of Saturn chunks that
  still need explicit mapping;
- `chunks`: per-chunk explicit mapping for Saturn entries that cannot be proven
  by automatic identity or unique stable-token alignment.

Chunk mapping entries use zero-based platform entry indices and one-based PS1
record numbers:

```json
{
  "chunks": {
    "4": {
      "ranges": [{"saturn": 0, "ps1": 1, "count": 107}],
      "entries": [{"saturn": 107, "ps1": 123}]
    }
  }
}
```

A Saturn-only translated record uses a sparse platform chunk file:

```json
{"saturn": 253, "platform": 253}
```

which resolves to
`data/games/<game>/lang/<code>/releases/l5-saturn-jp/SCEN/chunk_NNN.txt`
record `253`.

A verified non-translated SCEN entry can be preserved explicitly:

```json
{"saturn": 253, "preserve": true}
```

Use this only for service/control records that are not target-language text or
for records owned by another platform adapter. Preserved SCEN entries are copied
from the platform source entry unchanged.

`system_mapping.json` uses `unresolved_groups` as the no-source-text tracker for
known platform-specific SYSTEM deltas. Its `groups` object maps Saturn SYSTEM
group entries either to PS1 group indices / stable ids or to language-specific
Saturn overlay ids:

```json
{
  "groups": {
    "1": {
      "entries": [
        {"saturn": 0, "platform": "group:1:0"},
        {"saturn": 30, "ps1_id": "group:1:30"}
      ]
    }
  }
}
```

SYSTEM entries that are not ordinary translated text can also be preserved
explicitly:

```json
{"saturn": 213, "count": 19, "preserve": true}
```

Use this only for verified non-text runs or records owned by a separate platform
adapter, such as the Saturn name-entry glyph grid before `saturn_name_entry.py`
rewrites it.

If a Saturn build is requested without the needed platform source files or
without required platform mapping/overlays, the strict build fails. This is
intentional: a PS1 extraction alone is not enough to produce a complete Saturn
translation.

Relative manifest paths are resolved from the language directory.

## Record Review Status

`review_status.csv` is a sparse or complete list with this fixed header:

```csv
chunk,record,target_done,reference_checked,note
```

- `target_done`: `1` only after the target record is translated and reviewed;
- `reference_checked`: `1` only after the existing reference-language record
  has been checked against the Japanese source;
- `note`: optional editorial context.

Missing rows mean both states are pending. The review generator validates
booleans, duplicate keys and stale full-run keys. These states are editorial
decisions and are intentionally independent from automatic checks for missing
records, control-signature differences and residual Japanese.

Generate a scenario-oriented three-way review with:

```bash
python3 -m langrisser.review_html --lang ru --scenario 1
```

The JP dump drives the records, English is the default reference language, and
all HTML output stays under `work/l5/review/<lang>/`.

Target terminology can be checked independently of script completion:

```bash
python3 -m langrisser.validate_terms --lang ru --require-complete --require-speakers --max-plate-chars 10
```

The validator compares the JP key sequence with the reference pack and checks
duplicate keys, required values, glossary aliases and canonical overlap
between `names.csv` and `glossary.csv`. With `--require-speakers`, it also
verifies that every real SCEN speaker-pool key has a target-language plate;
`--max-plate-chars` applies a conservative pre-pair length limit where the
target language needs one.

## SYSTEM Layout Constraints

`system_layout.json` prevents one translated UI line from becoming
unexpectedly wider merely because its offset-table group has enough storage:

```json
{
  "default_max_grow": 4,
  "overrides": {
    "group:0:262": 6
  }
}
```

Values are additional encoded words beyond the original line length. Overrides
use the same generated stable ids as `system_strings.json`; unknown ids,
negative values and loose strings are rejected. Loose strings have no
regenerable offset table and always remain within their original fixed budget.
Keep the default conservative and add only display-verified exceptions.

`langrisser/system_pack.py --max-grow N` is a diagnostic override for the default;
stable-id overrides still take precedence. The normal build does not pass this
option and reads all limits from the language pack.

## Creating A Pack

```bash
python3 -m langrisser.init_lang ru --label Russian
```

The initializer copies source structure while clearing target values. It leaves
`SCEN/` empty unless `--copy-script` is explicitly passed.

The full PPF build completes the tracked assignment baseline into a generated
`work/l5/build/font_slot_assignments.<lang>.csv`, then rebuilds the font, rewraps
a copy under `work/l5/build/translation.<lang>/` and validates it before
insertion. The build never rewrites tracked translation sources. This makes
newly required pairs part of the actual build instead of an optional
maintenance step.

After generating the final table, the build also checks shared engine layout
constraints from `data/games/l5/system_ui_constraints.json`. In particular,
startup-menu labels may not cross a 9-cell VRAM-atlas row.

To persist newly derived assignments in the language pack for review, run:

```bash
python3 -m langrisser.assign_font_slots --lang ru
python3 -m langrisser.build_font --lang ru
```

## Build Outputs

For language suffix `<s>`:

- `work/l5/tables/lang5_<s>.tbl`
- `work/l5/build/SCEN.<s>.DAT`
- `work/l5/build/SCEN2.<s>.DAT`
- `work/l5/build/SYSTEM.BIN.<s>`
- `work/l5/build/IMG.DAT.<s>`
- `work/l5/build/SLPS_018.19.<s>`
- `work/l5/build/langrisser_v_<s>.bin`
- `patches/langrisser_v_<s>.ppf`

All outputs are generated and untracked.
