# Name entry: the "Name cannot be used" bug

## Root cause (confirmed)

The rename screen **forbids naming the hero the same as an existing character.**
Group 9 & 10 **index 292** is `シゲマ` — **not** a typo of `シグマ`, but the
deliberately-misspelled name of the **impostor "Shigema"** from the comedy
side-scenario in SCEN chunk 41 (the Baran/Adon/Samson trio disguise themselves
as Sigma/Lanford/Alfred — local pool record 7 `シゲマ`, record 8 `ラソフォード`,
record 9 `アルフしッド`; objective record 15 = "Wipe out the impostors"). The
misspelling is the whole point: the fake must read *almost* like Sigma.

We mistranslated `idx292` to exact `"Sigma"`, making it a byte-exact copy of the
default name (`g9 idx0`). The engine's name-uniqueness guard then sees the
default `"Sigma"` as already used by a character and rejects it with
**"Name cannot be used"**.

It is **not** about pair-glyph packing, the keyboard grid, or `--repack`
relocation — those were earlier wrong theories, ruled out below.

## The validation (decompiled)

Interactive name entry is `FUN_800a4130` (state in `DAT_800dacda`). On OK with
the cursor on END it does:

```c
DAT_800dace4 = FUN_800a4dc8();   // trim trailing spaces, return length
sVar4        = FUN_800a4e74();   // uniqueness check
if (sVar4 != 1) { /* duplicate */ DAT_800dacda = 5; return; }   // -> reject path (idx237)
/* sVar4 == 1 (unique) -> accept */
```

`FUN_800a4e74` (`0x800a4e74`):

```c
for (k = 1; k < 0x149; k++)             // every group-9 character name, idx 1..328
    if (streq(name, g9_string[k])) return 0;   // idx0 (the hero himself) is skipped
for (k = 13; k < 15; k++)               // a small second list
    if (streq(name, list2[k]))   return 0;
return 1;                               // no match = unique
```

`FUN_8008ee78` (`0x8008ee78`) is exact string equality (compare halfword by
halfword until both reach the `0xFFFF`/`0xFFFE` terminator).

Gate direction: **match (returns 0) ⇒ reject** ("Name cannot be used");
**unique (returns 1) ⇒ accept**. i.e. the entered name must not equal any
character name in group 9 (idx ≥ 1) nor the small second list.

## Evidence (live RAM dump)

A read breakpoint on the idx237 string (`0x8013d5b2` in the repack build) fired
at `PC=0x800805DC`, `ra=0x800A48D8` (inside `FUN_800a4130`). Simulating
`FUN_800a4e74` on the captured RAM (`work/ram.bin`):

- name buffer `0x80109f20` = `Sigma` = `[0x528, 0x52d, 0x652]`
- group-9 string base `0x80140156`, offset table `0x8013fec2` (live values of
  `DAT_800db130` / `DAT_800dba30`)
- **exact match at index 292** — `g9[292] == "Sigma"`, same bytes as `g9[0]`.

So the default `Sigma` collided with `g9[292]` → rejected. Confirmed end to end.

## Why GOOD vs BAD differed

`g9/g10 idx292` (`シゲマ`) carries `en: "Sigma"` in
`data/games/l5/lang/en/system_strings.json` (added with the unified flow, `9597c2f`).

| build | g9 idx292 | default `Sigma` accepted? |
|---|---|---|
| 1aa1735 (in-place) | `シゲマ` (katakana, untranslated in the build) | **yes** (unique) |
| HEAD (repack) | `Sigma` (translated) | **no** (duplicate of g9 idx0) |

The bisect landed on `9b77b85` (the `--repack` commit) only because that build
is where `idx292` actually came out as `"Sigma"`; repack itself is not the
mechanism.

## Fix

Translate the three impostors with the fan-canon near-miss names (used by the
secret-scenario guides), **not** the exact hero names:

| index | JP | impostor (fix) | hero |
|---|---|---|---|
| 292 | `シゲマ` | **Sigema** | Sigma |
| 293 | `ラソフォード` | **Lasford** | Lanford |
| 294 | `アルフしッド` | **Alfsed** | Alfred |

`idx292` "Sigema" ≠ "Sigma" removes the collision ⇒ the default is unique ⇒
accepted. The misspelling is also the *correct* translation — in the JP the
joke turns on the near-miss (chunk-41 rec 49: the real Sigma says
"シゲマ？俺は…だが？" / "Sigema? I'm … though?"). The names are kept consistent
across `system_strings.json` (g9/g10 292-294), `names_base.csv`, and the chunk-41
script (pool records 7-9 and the spoken `Sigema` lines 31/48/49). Nothing to do
with repack or glyph encoding.

The dialogue uses of `シゲマ` (rec 31/48/49) were checked against the JP original
and are **deliberate, not typos** — the JP consistently writes `シゲマ` there
(the impostor), never `シグマ`.

General rule for this engine: **no group-9 character-name string at index ≥ 1
may equal the protagonist's default name**, or naming the hero that default is
blocked (`FUN_800a4e74`).

## Reverse-engineering reference

Tooling: Ghidra vendored at `external/ghidra/ghidra_12.0.3_PUBLIC` with bundled
JDK `external/ghidra/jdk-21.0.10+7`; project `work/ghidra/proj` (program
`SLPS_018.19`). This build has **no Jython** — headless scripts must be Java
`GhidraScript`s. A flat disassembly is `work/disasm/SLPS_018.19.objdump` (note:
objdump prints load/store displacements in **decimal**). EXE loads at
`0x80010000` (text at file `0x800`); SYSTEM.BIN loads at `0x80134a00`.

Key symbols / addresses:

| what | address |
|---|---|
| interactive name entry | `FUN_800a4130` |
| uniqueness validation | `FUN_800a4e74` |
| string equality | `FUN_8008ee78` |
| trailing-space trim | `FUN_800a4dc8` |
| prologue name-display screen | `FUN_8003ab18` (uses EXE grid `0x800d0da4`) |
| name buffer (protagonist name, 18 B, in save state) | `DAT_80109f20` = `0x80109f20` |
| group-9 names base / table (live) | `0x80140156` / `0x8013fec2` |
| prompt "Your name？" (g0 idx236) | `0x8013d5a4` (repack build) |
| error "Name cannot be used" (g0 idx237) | `0x8013d5b2` (repack build) |

Note: the keyboard grid exists twice — an EXE 10×10 copy (`0x800d0da4`, used by
the display `FUN_8003ab18`) and the SYSTEM.BIN runs (group 0, idx 213..231). The
validation does **not** use either; it compares against the group-9 name list.
