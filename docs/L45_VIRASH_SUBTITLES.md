# Virash cutscene subtitles — analysis & plan

Status: **shipped (in-dialogue subtitle)**. The voiced monologue is now
subtitled by extending Virash's preceding dialogue (chunk 69, record 30) with
the monologue text on its own pages — see "Delivered" below. The two RE routes
explored here (engine overlay = approach 1, baked graphics = approach 2/Option
C) stay **open/unsolved** and are kept as analysis for a future in-scene
overlay; they were not needed to give the player the text.

## Delivered — in-dialogue subtitle (shipped)

Rather than draw into the cutscene itself (both RE routes below are still
blocked), the monologue is presented as continuation pages of Virash's line
that immediately precedes the cutscene. The player reads the full translation
in the dialogue window, then the voiced scene plays.

- **Where:** `data/games/l5/lang/en/SCEN/chunk_069.txt`, record 30 (Virash,
  FB00 `0x18`). The monologue text (from `data/games/l5/lang/en/virash_monologue.json`)
  is appended after his spoken "…More than you could ever imagine." line. No new
  record / FB00 id is created (`0x19` stays unused), so the JP control-tag
  signature is preserved and no override is needed.
- **New page + caption:** a custom authoring token **`<!FORCE$FFFD>`** opens the
  monologue on a fresh page so it never merges into the standard speech, and a
  second token isolates a one-line **yellow caption** (`<$FFF4>…<$FFF3>`):
  *"Virash told his story…"*. Layout: page 1 = his spoken line, page 2 = the
  yellow caption, page 3+ = the monologue (`<$FFFD>` per cue).
- **The token** (`<!FORCE$FFFD>`): a hard page break the reflow pass must never
  compact. It **encodes to a plain `<$FFFD>`** (the engine sees a normal page),
  but `langrisser/rewrap.py` splits the record on it and reflows each segment
  independently, so compaction cannot pull the monologue back into the speech.
  - `langrisser/scen.py` — `FORCE_PAGE_BREAK` constant; `Codec.encode`
    replaces it with `<$FFFD>` before tokenising.
  - `langrisser/rewrap.py` — `reflow_record` splits on the token (only the
    first segment keeps the speaker plate; only the last carries the yes/no tail
    reserve); `page_segments` treats it as a page boundary for the height check.
  - `langrisser/validate_translation.py` — strips it before the ASCII-punctuation
    check (it contains `!`); the control signature already ignores it.
- **Checks:** `verify_roundtrip`, `rewrap`, `check_speakers`, `validate_en`
  (chunk 69 `body` within budget, SCEN/SCEN2 fixed-size repack OK) all pass; the
  record length still fits the chunk budget, so file sizes are unchanged.

The rest of this document is the original analysis and the (still open) RE of an
in-scene overlay / baked-graphics subtitle.

## The scene

In **scenario 25 of 36** (its scene is chunk 69 = scene `44+25`), between two
spoken windows:

- before: Virash — "…I suppose so. What I'm about to tell you is all true. More…"
  (record 30)
- the cutscene (this task)
- after: Mariandel — "I can't believe there was such a terrible war…" (record 31)

The cutscene is a **voiced monologue by Virash** with **no on-screen text/subtitles**:

- left: a near-full-height static portrait of Virash;
- background: scrolls right→left — appears to show Crimzo (the red moon), soldiers,
  mages, and a scythe-wielding skeleton/death figure.

The voice is not subtitled anywhere, so a player who does not understand Japanese
gets nothing. Goal: **subtitle Virash's speech**.

## Candidate approaches (decide after analysis)

1. **Text overlay** — have the engine draw subtitle text over the scene (needs a
   draw hook / free VRAM for a font + a place to inject the strings and timing).
2. **Baked into the moving asset** — render the subtitle into the scrolling
   background (or a dedicated overlay sprite) as graphics.

Trade-offs depend on how the scene is rendered and timed, which the dumps should
reveal.

## Source data

`work/virash/virash/` (gitignored): six sequential snapshots taken during the
scene.

- `ram1..6.bin` — 2 MB each = PS1 main RAM (0x80000000 window, phys 0x000000..0x1FFFFF).
- `vram1..6.bin` — 1 MB each = PS1 VRAM (1024×512×16bpp framebuffer + texture pages).

## Analysis goals (in order)

1. **Audio track of Virash's speech.** Determine how the voice is played: CD-XA
   ADPCM stream (most likely — find the file/sector and the XA filter the engine
   set), SPU-ADPCM, or other. Needed for subtitle timing/sync and to confirm the
   scene boundaries.
2. **Background asset.** Locate the scrolling scene graphic in VRAM (diff the six
   frames: the scrolling region changes, static parts don't), its texture page,
   CLUT, and the source on the disc.
3. **Virash asset (optional).** Locate the static Virash portrait in VRAM and its
   source.

After these: pick approach 1 vs 2 and write the implementation plan.

## Working notes

### Dump characterisation

- VRAM diff (6 frames): only the left half changes (X<384), in two Y bands
  (0–191, 256–447) — these are the **two double-buffered framebuffers** (≈360×240
  display), redrawn each frame because the background scrolls. X≥384 is static =
  **texture pages** (assets uploaded once; the scroll is done by moving texture U,
  not re-uploading). Confirmed visually: framebuffer shows the static Virash
  portrait on the left and the dark red scene (Crimzo moon top, demon/death figure
  right). There is a **black band at the bottom** of the display — natural place
  for a subtitle line.
- RAM diff (6 frames): the big churn is `0x80120000–0x80140000` (≈70%/28% per
  frame) = the **double-buffered GPU display list/primitive buffer** rebuilt each
  frame. Not audio. (PS1 CD-XA audio is decoded by the CD hardware straight to the
  SPU, so the voice samples are never in main RAM.)

### Goal 1 — audio track: FOUND (VOICE.PAC, XA-ADPCM)

- RAM holds `\L5\VOICE.PAC;1` as the active CD path during the scene → the voice
  streams from **`/L5/VOICE.PAC`** (disc LBA 86636, ~219 MB).
- VOICE.PAC raw sectors are **Mode2 Form2 XA-ADPCM audio** (subheader submode
  `0x64`, coding `0x04` = **18.9 kHz mono**), **32-channel interleaved** (sector
  86636 = chan 0, … 86667 = chan 31, then repeat). So it carries 32 parallel
  ~11-minute audio streams; the scene plays ONE via the CD XA filter
  (`CdlFILTER` file/channel). Virash's line = (channel C, start sector, length).
- A working XA decoder is in `/tmp/xa_decode.py` (decodes a given channel+range
  to 18.9 kHz mono WAV; output is real audio, not noise).
- **The scene is scenario 25/36, not early.** So Virash's clip is deep in the
  file, NOT at the start — its (channel, sector) must be pinned, not guessed:
  - best: read the live CD position (LBA/MSF of the playing XA sector, and the
    filter channel) from the emulator during the scene; or
  - decode the chunk-69 VM cutscene-trigger to get the voice id, then find the
    voice-id→VOICE.PAC sector index table (location TBD).
- Other audio/stream files on disc: `XA.PAC` (~39 MB), `L5.STR`/`LL.STR`,
  `ADPCM.DAT`, `SOUND.DAT`, `MOVIE.BIN`.
- TODO: pin the exact channel + start sector of Virash's clip. The strings
  `\L5\VOICE.PAC;1`, `XA.PAC;1` live in a static EXE filename-pointer table at
  `0x80010168` (not the live read position). The live CD position is in the libcd
  work area (no symbols → hard to grep). Cleanest ways to pin it:
  - capture `CdlLOC` (BCD min:sec:frame) in the emulator at the moment the scene
    starts streaming, convert MSF→LBA, subtract VOICE.PAC's base LBA 86636; or
  - decode the chunk-69 VM cutscene-trigger opcode (between text id 22 = record 30
    and id 23 = record 31) which carries the scene/voice id.
  Then extract that XA channel to measure the clip length for subtitle timing.

### Audio sources mapped (revised)

- **VOICE.PAC** = short **dialogue** voice clips only. 32-channel mono XA; a
  full-file longest-continuous-voice scan shows every clip is **≤ 8.5 s** —
  these are per-line dialogue voices, NOT a long monologue. An EXE table at file
  offset `0x11e8` (189 × 36-byte records) indexes them by MSF, but the per-record
  channel/length encoding is only partly decoded (block starts land on minute
  boundaries; finer offsets unresolved). The earlier `work/virash/clips/*.wav`
  were 90 s *windows* from those block starts — not real clip boundaries (so
  "all 1:29" was just the decode cap).
- **XA.PAC** = interleaved **cutscene A/V pack** (8 audio + 8 data sectors,
  repeating). Audio is **18.9 kHz stereo** XA on channels 0-7 and 17-23.
  - ch0 ≈ 116 s — continuous = **BGM/music**.
  - ch1-7, 17-23 ≈ 58 s each, short clips with pauses = **cutscene voice**.
  - Decoded to `work/virash/xapac_clips/xapac_ch*.wav` (per-channel concatenation
    in file order; may span several cutscenes).
- The scene's visuals do **not** match XA.PAC bytes (probe search: 0 hits), so the
  scrolling background/Virash are engine textures loaded separately, not streamed
  MDEC. Confirms the scene is engine-rendered with a streamed voice track.
- **FOUND (in-game confirmed): Virash's monologue = XA.PAC channel 2**, mono XA
  (coding `0x04`, 18.9 kHz), the **first clip on that channel: 0 → 105.4 s**
  (a clean digital-silence gap at ~105.1–105.5 s separates it from the next
  speaker). Decoded mono (the channel is mono, not stereo — only ch0/poem is
  stereo) to **`work/virash/VIRASH_MONOLOGUE.wav`** (105.4 s). ch0 = the prologue
  poem narration (stereo), confirmed clean/full by ear.
- Extraction recipe: read `/L5/XA.PAC` (LBA 198988) raw 2352-byte sectors, take
  audio sectors (submode bit `0x04`) with subheader channel == 2, decode XA-ADPCM
  mono, keep the first ~494 channel-sectors (105.4 s).

### Goals 2/3 — assets

- Both the scrolling background and the static Virash portrait are **textures in
  VRAM** (right/static half), drawn into the framebuffer each frame. Disc source
  not yet pinned (candidates: `IMG.DAT`, a cutscene-specific TIM); needs VRAM↔file
  correlation. (in progress)

### Implication for subtitling (early)

The scene is **engine-rendered and script-driven** (chunk-69 VM, between records
30 and 31), not an MDEC movie. So a subtitle is best drawn by the engine over the
bottom black band (approach 1), timed off the scene script/voice, rather than
baked into the scrolling texture (which would scroll with the background).

## Monologue transcript & subtitle source

The machine-readable subtitle source — timed JP + EN cues — is
**`data/games/l5/lang/en/virash_monologue.json`** (edit the text there). This section
is provenance: how that text was obtained and verified.

### Two independent transcripts agree

A first-pass STT (user) and **faster-whisper (medium, ja)** were run separately
and agree. Homophone differences are resolved by the **game script** (canon).
Reproduce whisper: `python3 work/virash/whisper_run.py` → `work/virash/whisper_out.txt`.

```
[  0.0-  3.5] 遥か昔 この地には
[  3.5-  9.9] 赤と青の月があった 赤い月をクリムゾ
[  9.9- 18.4] 青い月をペイリアという クリムゾには高度な魔法文明を築いた
[ 18.4- 24.8] クリムゾニアという民が住んでいた クリムゾニアの中でも
[ 24.8- 32.6] 身分の低い労働階級は地上に下ろされ この地に住んでいた人類ともども
[ 32.6- 38.8] クリムゾニア上流階級の支配を受けていた さらに
[ 38.8- 43.6] クリムゾニアの高度な魔道技術は 混沌の神
[ 43.6- 48.6] カオスとの契約さえも可能にし 呼び出した魔族まで
[ 48.6- 54.5] 労働力として使っていた 数百年という
[ 54.5- 61.9] 長い寿命を持つ彼らの映画は 永遠のものと思われた
[ 61.9- 69.9] その映画はある時終わりを迎える 地上の労働階級者たちが
[ 70.0- 78.0] 反乱を起こしたのだ 空を埋め尽くす空中戦艦
[ 78.0- 82.5] 地上には ひしめく魔道巨兵
[ 82.5- 88.1] 高い魔道技術を使い この世界すべてを巻き込んだ激しい戦いは
[ 88.1- 96.3] 言葉で表せるほどのレベルではなかった その戦いの果てに
[ 96.3-100.2] クリムゾは 本来の軌道を外れ
[100.2-107.0] 200年という超大円軌道を描く 衛星となったのだ
```

Homophone fixes (both STTs guess the common word; game script wins):

| heard | correct (game) | reading | evidence |
|---|---|---|---|
| 映画 (movie) | **栄華** (glory) | えいが | 栄華 is in the script, 映画 is not |
| 超大円 / 超楕円 | **超楕円軌道** | — | game uses 超楕円軌道 (chunk 116 #39, chunk 69) |
| 魔道 / 魔導 巨兵・技術 | **魔動** | まどう | game spells 魔動 (魔動巨兵, 魔動砲) |
| 空中戦艦 | **空宙戦艦** | くうちゅう | game term for these sky-space ships (EN "sky battleship") |

Both STTs independently give `200年という` and `言葉で表せるほどのレベルではなかった`
verbatim — high confidence overall.

### Content confirmed by three sources

1. **Game recap**, `work/l5/scriptdump/SCEN/chunk_129.txt` #66 ("そしてヴィラージュは、
   古代魔法大戦について語る。…クリムゾは本来の軌道を外れ、200年周期で楕円軌道を描く衛星となった。").
2. **Borgor's GameFAQs guide** (`work/translation.txt` ~L8500-8517) — an English
   *summary* (borgor couldn't catch an exact translation): red Crimzo / blue
   **Pelia**; Crimzonia elite rule Crimzo; cast-down = Crimzo Landers; pact with
   Chaos; planet revolts; great war with huge battleships; 200-year orbit.
3. Terminology matches the game throughout.

### Canonical EN terms used in the cues

Crimzo · Pelia · Crimzonia · Crimzo Landers · Chaos (god of chaos) · demons
(魔族) · sky battleship (空宙戦艦) · magic giants (魔動巨兵, cf. 魔動砲 = "magic
cannon") · advanced magical civilization · true orbit · satellite · extreme
elliptical orbit (超楕円軌道). Match `chunk_129` #66 EN and `names_base.csv` /
`glossary_names.csv`.

## Implementation — engine hook (RE + proof-of-concept)

Chosen approach: inject our own primitive into the scene's per-frame draw list
(no dialogue window needed). All addresses are in `SLPS_018.19` (loads at
`0x80010000`; file offset = addr − 0x80010000 + 0x800).

### Rendering pipeline (reverse-engineered)

The game uses a **libgs-style packet/OT system**:

- Primitive packet buffer at **`0x80124000`** (~`0x1a00` words), **double-buffered**
  (two bases set at `0x800176a4`); current alloc pointer in `0xd3c($gp)`.
- **GsGETPACKET** (allocate N bytes from the packet buffer, overflow-checked) at
  **`0x80017dac`** / `0x80017dd8` / `0x80017a78`.
- **GsSortObject(prim)** at **`0x80017e28`**: reads the packet length from
  `prim[3]` (top byte of the tag word = word count), `GsGETPACKET`s, copies the
  packet (word-copy loop at `0x800b3fa4`/`…fb8`), then links it into the **current
  OT held in the global `0xb74($gp)`**. **Only `$a0` = prim is needed**; `$a1/$a2`
  are set internally. So a primitive = standard PS1 packet: word0 tag (len in top
  byte), then `len` GPU command words.
- The scene draws **standard SPRT (GP0 0x64)** textured rects (background ≈ 1430
  tiles 24×16, CLUT `0x7c80`) + polys (`0x20`/`0x2c` for Virash). A subtitle glyph
  is just one more SPRT into the same OT; the bottom-right **black band** has no
  primitives, so any depth shows there.

### Hook point (the scene's per-frame draw)

Found via: PCSX-Redux **write breakpoint on `0x80124000`** → stops in the packet
copy (`pc 0x800B3FB8`, `$ra 0x80017E6C` = inside GsSortObject); a **RAM dump taken
while stopped** (`work/virash.bin`, `$sp = 0x801FFE58`) gives the live stack, where
GsSortObject's saved `$ra` at `$sp+0x1c` = **`0x80035768`** — scene code. (The
earlier vsync-time dumps had a stale stack there; the on-breakpoint dump was needed.)

Disassembly confirms the scene per-frame builder:

```
0x80035760: jal  0x80017e28    ; GsSortObject(prim)   <-- per-frame draw
0x80035768: lw   $t2, 0xc0($sp)                        <-- hook here (after the call)
...
0x80035784: jal  0x80017e28    ; another GsSortObject
```

`0x80035768` is right after a GsSortObject call: the OT is open and the function
reloads $t/$a/$v from the stack afterward, so clobbering them is safe.

### PoC injection notes

The first PoC hooked `0x80035768` (right after one `GsSortObject` call inside the
object/tile loop). In-game it reached the Virash voice line, then the background
music/video froze while the XA voice continued. This is consistent with adding a
new primitive once per rendered object/tile, exhausting the packet/OT path.

Additional issues found in that first PoC:

- The original cave at `0x800d8e0c` is zero in the EXE but holds live runtime data
  in the breakpoint RAM dump. It must not be used.
- The `jal GsSortObject` delay slot was wrong: `$a0` was completed after the call,
  so `GsSortObject` received `0x800d0000` instead of the primitive pointer.
- The hook did not preserve live caller-saved registers.

The second PoC used a later, once-per-function point:

- hook site: **`0x80035b08`**, replacing `lw $ra,0x134($sp)`;
- return site: **`0x80035b0c`**;
- hook code cave: **`0x800d8e80`**;
- primitive data: **`0x800d8f80`**;
- cave selection: zero in both the source EXE and the live breakpoint RAM dump
  (`0x800d8e74..0x800d9be3`);
- hook preserves `$ra`, `$v0/$v1`, `$a0-$a3`, and `$t0-$t9`;
- `lui/ori $a0` runs before `jal GsSortObject`;
- marker primitive: magenta TILE at `x=24, y=204, w=336, h=22`.

In-game result: loading a scenario-25 save hung at `Now Loading`. That means this
function is still generic enough to run during loading, before the render context
needed by our injected `GsSortObject` call is always valid.

The third PoC moves out of the generic helper entirely. The live stack shows:

`0x80036e38 -> 0x800b5e78 -> 0x80035004 -> GsSortObject`.

The current hook is in the caller at **`0x80036f18`**, after the six draw-loop
calls to `0x800b5e78`. This should avoid the `Now Loading` path that hit the
generic helper:

- skip if `$gp+0xb74` is null (current OT pointer used by `GsSortObject`);
- otherwise draw the same magenta TILE.
- the original `li $t0,1` is replayed in the trampoline;
- the jump delay slot intentionally executes the original `li $v1,16` at
  `0x80036f1c`, then returns to `0x80036f20`.

In-game result: scenario-25 loading still hung at `Now Loading`. This disproves
the current hook family (`0x80035004` helper and `0x80036e38` caller) as a safe
blind insertion point. Further work must use debugger traces from a clean,
working build to identify a Virash-specific render/task callback before another
PoC is generated.

### Debugger-guided PoC

Runtime breakpoint on `GsSortObject` during the Virash cutscene:

```
pc=0x80017E28 ra=0x800280D0 sp=0x801FFD98 gp=0x800DAADC
a0=0x800DBA60 v0=0x800DBA60 v1=0xE1000000
```

`a0=0x800dba60` points to an existing 2-word `DR_TPAGE` primitive:

```
0x800dba60: 0x02000000
0x800dba64: 0xe100048e
```

This call comes from:

```
0x800280bc: li   a0,0x380
0x800280c0: jal  0x80016df8      ; allocate/build original primitive
0x800280c8: jal  0x80017e28      ; GsSortObject
0x800280cc: move a0,v0           ; delay slot: original primitive pointer
0x800280d0: function epilogue
```

The current PoC wraps that exact existing call instead of inserting a new render
call elsewhere:

- patch `0x800280c8` from `jal GsSortObject` to `jal 0x800d8e80`;
- leave the original delay slot `move a0,v0` intact;
- wrapper first calls `GsSortObject(original a0)`;
- wrapper then calls `GsSortObject(0x800d8f80)` for the magenta TILE;
- wrapper restores caller-saved registers and returns normally to `0x800280d0`.

This uses a render moment that is already proven valid by the runtime
breakpoint, avoiding the previous blind insertion points.

In-game result: this still hung at `Now Loading`; debugger showed an invalid/zero
state after the hang. The standard patch was rebuilt without the PoC. The next
step is not another hook guess: compare the same call site during `Now Loading`
and during the Virash cutscene on a clean build, then add a state gate before any
draw call.

Built artifact:

- Full current translation + diagnostic marker:
  `patches/langrisser_v_en.ppf`.

Expected: a magenta bar in the Virash cutscene bottom black band = concept
proven (we can draw into the scene). Then: refine coords to the black band,
swap the TILE for glyph SPRTs (EN font in free VRAM), and drive cue selection by
XA position (channel-2 sector).

Historical first-PoC trampoline for reference:

```
addiu $sp,$sp,-8 ; sw $ra,0($sp)
lui $a0,0x800d ; jal 0x80017e28 ; ori $a0,$a0,0x8e40   ; GsSortObject(PRIM=0x800d8e40)
lw $ra,0($sp) ; addiu $sp,$sp,8
lw $t2,0xc0($sp)        ; displaced original
j 0x80035770 ; nop      ; return
PRIM @0x800d8e40: 0x03000000, 0x60FF00FF, (190<<16)|120, (36<<16)|180
                  ; tag len=3 | GP0 0x60 flat rect magenta | Y=190,X=120 | H=36,W=180
```

### Open / next

- Confirm in game (box visible? position? other scenes? crash?).
- If the code path runs outside this cutscene, gate by scene state.
- Font into VRAM during the scene; glyph SPRT uv/clut from the EN font build.
- Timing: read the live XA sector (CdlGetlocL) → elapsed → cue id.

### Runtime VM transition evidence (record 30 -> cutscene -> record 31)

Static byte scans are not sufficient here. The useful evidence is the live VM
state captured from a clean build during the Virash sequence.

Clean runtime dumps show the active VM instruction pointer at `gp+0x30c` inside
chunk 69, whose VM block is loaded at `0x8016e478`:

```text
work/virash1.bin: gp+0x30c = 0x8016e75a = vm rel 0x02e2
work/virash.bin : gp+0x30c = 0x8016e76e = vm rel 0x02f6
```

The bytes at those runtime offsets are:

```text
vm rel 0x02e2: 63 00 1a 3c 1a 50 1a 50 1a 50 1a 28 18 00 17 00 64 0e 01 07
vm rel 0x02f6: 18 01 22 00 63 01 11 00 24 1e 14 01 1e 01 1a 1e 26 62 23 03
```

Disassembly confirms:

- opcode `0x63 nn` consumes one operand byte and calls `0x80035b3c(nn, 1)`.
  This is the scene/object script runner, not text rendering.
- opcode `0x22 nn` consumes one operand byte and sets global flag
  `0x800db37c = 0x0200` before returning to the VM dispatcher.

The object-script runner uses the base pointer at `0x800db380`. In both clean
runtime dumps that pointer is `0x8014f320`; the object offset table is at
`base + *(base+0x28) = 0x8014f388`. The two object calls seen in the live VM
transition resolve to very short object scripts:

```text
object 0: table offset 0x00ea -> 0x8014f40a: 01 01 ff ff ...
object 1: table offset 0x00f0 -> 0x8014f410: 00 01 ff ff ...
```

So `63 00` / `63 01` are verified object-state toggles around the transition,
not the long Virash monologue payload.

Therefore the transition after Virash's record 30 is not encoded as a special
control word in the text record. The text record ends normally with `FFFE`; the
cutscene is driven by the surrounding VM bytecode and object-script calls.

The local VM segment later in the same byte range contains bytes that pass the
semantic shape of display/window commands:

```text
vm rel 0x0376: display-shaped command with text id 0x0018
vm rel 0x038a: display-shaped command with text id 0x0019
vm rel 0x0396: display-shaped command with text id 0x001a
```

This is not execution proof. Static display-command scans are useful for speaker
wrapping, but they are insufficient for identifying the Virash cutscene trigger:
the VM is bytecode with branches, helper-consuming operands and object-script
side effects. The missing text id `0x0019` is only a static hint until a live
trace reaches that offset or the intervening VM control flow is decoded.

The next useful debugger breakpoints are the VM/object boundary, not another
render hook:

- `0x8001f1c8` / `0x80035b3c`: captures opcode `0x63 nn` calls and the object
  script id (`a0`) as the scene transitions.
- `0x8001df60`: captures opcode `0x22 nn` and confirms the flag/timing around
  the transition.
- A live dump at or just after VM rel `0x038a` would confirm whether the
  display-shaped `0x0019` site is actually executed.

Conclusion: do not place another subtitle PoC into a generic draw path. First
pin the Virash-specific object/event script path from the runtime VM transition,
then draw from a gated scene callback or from the object script's own render
state.

## Background asset — located in MAP_C.DAT (Option C)

Option C = bake the subtitle (scrolling, accepted) into the background asset, no
code hook. Findings:

- The scrolling cutscene background is **NOT in IMG.DAT** (confirmed: not in
  `work/img_dump`). It lives in **`/L5/MAP_C.DAT`** (disc LBA 25022, ~40.5 MB),
  stored **uncompressed**: 5/5 distinctive runs from the live VRAM background
  texture match MAP_C byte-for-byte (other big files — L5.STR, LL.STR, BTLBG.DAT,
  MOVIE.BIN, SOUND.DAT — got 0 hits).
- The texture is **8bpp indexed**; CLUT (256 colours, BGR555) is at VRAM `(0,498)`.
- The Virash-scene background region in MAP_C starts ≈ **`0x19fe868`**. The actual
  block header is at `0x19fe800` (u32 LE: `0x160`=352, `0x80000`=**524288 = data
  size**, `1`, `0x3f00`, `0x35c`=860, `0x40`/`0x7e`, …); **pixel data begins at
  `0x19fe820`**.

#### Format DEFINITIVELY IDENTIFIED: type-8 scanline-packets (== IMG.DAT)

MAP_C.DAT uses the **same image codec as `/L5/IMG.DAT`** — the type-8
scanline-packet format already implemented in `langrisser/imgdat.py`
(`image_groups` / `decode_image` / `encode_image`). Verified against MAP_C:

- **MAP_C is the same _container_ as IMG.DAT, not just the same codec.** It opens
  with the identical **0x800-byte TOC of sorted u32 asset offsets**
  (`langrisser.imgdat.parse_toc`): **172 assets**. Each asset bundles **its own image
  groups _and_ its own CLUT block(s)** — typically **4 palette variants** per asset.
- A packet is **2048 bytes = 0x20 header + 2016 pixel bytes**. Header: magic
  `u16[0]==0x0160`, `u16[3]==8` (type), `u16[0x1a]==2048`, **width = `u16[0x14]*2`**,
  **block_rows = `u16[0x16]`**. Eight packets make one `0x4000` block of
  `block_rows*width` pixels. (My earlier "0x80000 block / 16×16 tile bank /
  autocorr-128" reading was a coincidence of the 128-px width — the real unit is
  the packet, not a 0x80000 tile bank.)
- Decoding with the verified codec is **byte-exact round-trip**, per asset and
  spliced back into the file (`selftest`: **167 assets round-trip byte-identical;
  MAP_C MD5 unchanged after extract→pack**).
- **The palette MUST come from the same asset as the image.** Picking a palette
  globally over all of MAP_C's CLUTs — or reusing a single VRAM CLUT for every
  group — yields wrong colours. The correct path mirrors IMG.DAT's `cmd_dump_all`:
  `clut_palettes(asset)` then `pick_palette(pixels, …)` **within that asset**.

#### Placement CRACKED & VERIFIED: the packet header is a VRAM-upload descriptor

The 0x20-byte packet header is a **VRAM destination rectangle** (the `LoadImage`
DMA target), not just image metadata. Per 8-packet block, the header words are:

- `u16[8]` = **VRAM X** in 16-bit units → byte column = `u16[8] * 2`.
- `u16[9]` = **VRAM Y** (row). A group's two blocks stack at Y=0 and Y=126.
- `u16[10]` = **width** in 16-bit units (`0x40`=64 → 128 px at 8 bpp).
- `u16[11]` = **height** (`0x7e`=126 rows). `u16[12]` = a running cell/frame id.

**Proof (100% byte-exact):** reconstructing **asset110** alone — placing each
block at `(x=u16[8]*2 bytes, y=u16[9])` in a 2048×512 byte-plane — reproduces the
real captured VRAM **byte-for-byte: 100.0% over 177 408 bytes against all six
`work/virash/vram{1..6}.bin` dumps**. So **asset110 _is_ the Virash cave's VRAM
texture page** (VRAM byte-cols 1336..2048, rows 0..252). Reverse byte-search of
1001 MAP_C blocks against the dumps also pinned assets **91, 94, 101, 110, 120,
123** as VRAM-resident during this scene, each at exactly its header `(X,Y)`.

Consequences:
- **Different assets reuse the same VRAM region at different scroll times**
  (texture streaming). So a single VRAM snapshot matches **one** asset; piling
  several assets into one canvas overwrites and matches poorly — reconstruct
  **per asset**.
- The assembled VRAM page still **looks like a busy strip atlas** (it _is_ what is
  in VRAM, byte-exact). The smooth on-screen cave is the GPU **compositing that
  texture page into the framebuffer** (left half of VRAM — two double-buffered
  16-bpp framebuffers showing cave **+ Virash**, confirmed by an RGB555 render of
  `vram1`). So the **last unknown is the texture→screen UV map** (which atlas
  texels land where on screen), not the pixel format, the palette, or the VRAM
  placement — those are all solved and reversible.
- The earlier **asset108 landscape is a _different_ scene**: its block is **not
  found byte-exact in any of the six cave dumps**, so it is not the Virash cave.

The cutscene-background imagery lives in **assets ~105..116** (file offsets
`0x18fa800`..`0x1b0e800`; the cave texture page itself is **asset110**,
`0x19e5000`). What the decoded groups actually are, with the correct
per-asset palette:

- **Some groups are fully coherent background frames that decode straight from the
  data** (no VRAM, no Virash overlay). Verified example: **asset108 group
  `@0x19b8800` (128×252)** decodes to a coherent landscape — a sky with a large
  glowing ring/oval, snowy mountains, a lake, and a draped structure at the left
  edge. (Honesty note: that landscape does **not** obviously match the dark Virash
  _cave_, so which asset holds the Virash cave specifically is **not yet pinned** —
  several of assets 105..116 are adjacent cutscene scenes.)
- The remaining 128-wide groups are **vertical-strip atlases**: with the correct
  palette each block resolves into coherent texture, but split into **16 vertical
  strips of 8 px** (measured: column-discontinuity peaks at every 8 px → strip
  edges at x=8,16,…,120). The strips are individually coherent but **not in screen
  order**, so a raw block looks striped. Assembling the panorama = reordering the
  8-px strips.
- **Retraction:** the earlier "group `@0x1a0f800` = reaper's hand+scythe" and
  "small-width groups = character faces" were **wrong-palette artifacts** (noise
  from a global/VRAM CLUT), not real content. With per-asset palettes those small
  groups are plainly strip-atlas fragments, not a reaper or faces.

- **So the full coherent cave panorama is still assembled at runtime** from the
  8-px strips; the missing piece is the **strip→screen placement** (the runtime
  tilemap), not the pixel format or the palette. Individual coherent frames and
  all strips are now extractable/packable from the data with correct colours.

#### The real cave CLUT (palette) — found in VRAM

The cave texture's CLUT is at **`vram1` byte `0x25110`** (256 × RGB555). Its mean
colour `(61.6, 26.7, 10.7)` matches the framebuffer cave's mean `(61.6, 27.3,
10.7)` to within 0.7, and rendering **asset110** with it gives correct **brown
cave tones** (`work/virash/a110_vramclut.png`). So the palette is solved; the only
thing that still makes asset110 look like a "strip atlas" is the **column order**,
not the colours.

#### Honest state of the assembly (DATA → coherent image)

- **DATA → VRAM is fully solved & reversible.** The packet header places each
  block in VRAM (verified 100 % byte-exact, above); `langrisser/bg_sprites.py pack`
  writes edits back losslessly. This is the "disassemble back into the game" half.
- **The cave bg is NOT stored as one coherent image anywhere in the data** — it is
  an **8-px-wide column atlas** (raw index render: smooth _within_ each 8-px
  column, hard break _between_ columns; columns not in screen order). A scan of
  assets 95..130 for a coherent cave frame found none (only the unrelated asset108
  landscape and some portraits decode coherently).
- **Content-based column reordering does NOT recover the scene**: matching atlas
  8-px columns to the framebuffer cave window (correct CLUT, full 252-row height =
  block height, so no vertical scale) only approximates the framebuffer blurrily,
  because the columns are self-similar (`work/virash/cm2_recon.png`). The true
  column→screen order is the runtime UV map.

#### GPU draw list re-examined (correcting my earlier mis-read)

All six dumps are **real active-scroll captures** (user-confirmed). Re-parsing the
libgs OT: the buffer at `0x80124000` holds packet/tag words linking into a
**Z-bucket OT array at `0x10a880+`** (most slots chain forward; some point to prim
packets at `0x127xxx`/`0x0e3xxx`). Each dump contains **≈1425 background SPRT**
(GP0 `0x64`, **24×16**, CLUT `0x7c80`) — matching the prior "≈1430 tiles" RE.
**Observation (not yet a screen map):** every one of those SPRT draws to screen
**X=48, Y∈{32,48}**, sampling **UV∈{80,104}×{0,16}** (a tiny 2×2-tile patch); the
`GP0 0xE5` commands set the **framebuffer-base Y** (8 / 248, i.e. the double
buffer), not a per-tile X. So the visible screen-spanning placement is **not** in
these SPRT XY directly — consistent with the scene being **composed into an
offscreen buffer and then scaled to the display** (which is also why raw
framebuffer↔MAP_C texel matching fails). The per-tile screen destination is still
to be located; this is **not** a degenerate/fade frame (my earlier claim was a
parsing error).

**Step-1 probe results (env-command + primitive census of the live dumps):**

- The `1425` bg SPRT all draw to screen **(48,48)/(48,32)** sampling **one** 24×16
  texel rect (UV 80–104 × 0–16); their texpage `E1` toggles **`0x048e`/`0x048f`**
  = VRAM X base **896 / 960** (u16), which lies **inside asset110's VRAM region**
  (byte-cols 1336–2048) — confirming they sample the asset110 cave texture, but
  **not** spreading across the screen.
- `E5` drawing-offset (214 distinct, top `(0,8)/(0,248)/(0,240)/(0,0)`) and
  `E3/E4` drawing-area (top `(0,8)–(319,231)` / `(0,248)–(319,471)`) only select
  the **double-buffer base and the full 320×232 screen area** — they are **not**
  per-tile positions.
- Textured **quads `0x2C`** in the bg screen region are few (~58, CLUT `0x7805`,
  page `0x1e`, tiny UV span) — detail/effect sprites, **not** the bg fill.
- **Conclusion:** no static primitive XY in the dumps gives the screen-spanning
  tile layout, which corroborates the **offscreen-compose-then-scale** model. The
  per-tile destination must come from the **scene draw routine's code** (it lives
  in the RAM dumps at file offset `0x35760`, RAM `0x80035760`, already located in
  the engine-hook RE).

**Step-1 disassembly of the draw routine (RAM `0x80035100..0x800357f0`,
MIPS via capstone on `ram4.bin`):** it is a **general per-object draw engine**, not
a simple tilemap blit:

- It keeps a **per-index scratch/state array at `0x800e2a00`** (stride **36 bytes**:
  `$s6 = 0x800e2a00 + idx*36`; mostly zero in the dump = idle). The **real object
  descriptors are indirected**: it loads a base pointer from **`0x800db380`**
  (`lw $v0,-0x4c80($0x800e0000)`) → in `ram4` that is **`0x8014f320`**, and each
  record = `*(0x800db380) + list_offset`. The block at `0x8014f320` is structured
  (early fields `… 0x3c 0x44 0x44 0x68 0x68 0x68 0x174 0x178 …` — UV/offset values;
  `0x68`=104 matches the bg SPRT U=104). Object type is the byte at record `+2`.
- For each element it writes a primitive into the packet at `$s1`/`$a0`. Two
  branches (selected by a flag — `xor` of two state bytes `& 0x30`):
  - **scaled textured QUAD (`0x2C`)**: 4 vertices `(s3,s2)`, `(s3+fp,s2)`,
    `(s3,s2+s7)`, `(s3+fp,s2+s7)` → so **X=`$s3`, Y=`$s2`, W=`$fp`, H=`$s7`**, and
    the texpage/UV word at `+0x16` comes from `jal 0x800b7d70` (GetTPage-style).
  - **1:1 SPRT (`0x64`)**: same X/Y, plus `u=$s5,v=$s4,w=$fp,h=$s7`.
- Some object types call **`jal 0x800174a0` (RNG)** and take `rand % range` /
  `rand / (field+1)` using ranges from the descriptor (`+6`, `+8`, `+0xa`) — i.e.
  **part of the scene is RNG-driven animation** (likely flame/glow/particles),
  written back into the descriptor (`sh … ,8($s6)` etc.).
- So the **on-screen layout is produced by this engine** from the `0x800e2a00`
  descriptor table (positions/scales/UV per object) plus per-frame RNG — it is
  **not** a static tile→screen array sitting in the data. The scrolling cave wall
  is one object type whose `$s3/$s2/$fp/$s7` come from the descriptor + the scroll
  position; fully inverting it means reading the descriptor table layout and the
  scroll variable (more EXE/RAM RE), or driving the engine.

**Bottom line for assembly:** the editable, reversible source (asset110 texture +
header VRAM placement + `0x25110` CLUT) is fully in hand, but the **coherent
on-screen cave is synthesised by the engine** (scaled quads + descriptor table +
RNG), so there is no single static "panorama" to export; producing one means
either reconstructing the descriptor-driven layout or compositing via the engine.

#### Why the coherent wide image cannot be exported from static data alone

- Stitching the framebuffer scene-windows **does work** where frames overlap:
  with the static Virash portrait masked off (portrait right-edge ≈ x177) and
  normalized cross-correlation on the pure scene region, **f4→f5 = horizontal
  shift +64 px at corr 0.99** (clean scroll; merges into a seamless two-reaper
  scene — see `work/virash/bg_stitch_f4f5.png`). So the scroll is horizontal and
  the windows are stitchable.
- **Correction to an earlier wrong read:** the 6 frames are NOT clustered — they
  are spread across the scene. Intensity correlation under-measured the dark cave
  frames (low texture ⇒ nothing to lock onto). Re-running on **gradient/edge**
  images gives the real motion. The background is **one continuous right-scrolling
  panorama made of ~3 scenes with transitions** between captures:
  - f1→f2→f3 = the **cave** scene scrolling: shifts **+108, +75** (corr 0.59/0.72);
  - f3→f4 = corr 0.19 → a **gap/transition** (cave → reapers);
  - f4→f5 = the **reapers** scene scrolling: **+64** (corr 0.85);
  - f5→f6 = corr 0.29 → a **gap/transition** (reapers → the draped ghost).
- So the windows ARE stitchable per scrolling run; the two low-corr boundaries are
  **coverage gaps** (between those dumps the scroll moved more than one window
  width). Filling them needs 1–2 extra frames near each transition.
- **CORRECTION (my earlier read was a parsing error — do not repeat it).** All six
  RAM+VRAM dumps are **real captures taken during the active scroll** — none is a
  fade/clear/transition frame. The GPU primitive list at `0x80124000` in them is
  **genuine placement data**: the background ≈ **1430 `0x64` SPRT tiles, 24×16,
  CLUT `0x7c80`** (per the RE in "Rendering pipeline" above). My previous "all SPRT
  at XY (48,48), degenerate" claim was a mis-parse of the packet/OT structure, not
  the truth. The tile→screen placement therefore **is** in these dumps and must be
  parsed correctly (walk the OT linked list from `0x80124000`; each SPRT packet =
  tag word then GP0 `0x64` command words with screen XY + texture UV).

### Stitched background — DELIVERED (from the 6 framebuffers)

Stitching each scrolling run (Virash portrait masked off at x≈177; per-pair
gradient cross-correlation) yields coherent wide backgrounds:

- `work/virash/bg_seg1_cave.png` — cave: skeletal demon + red eclipse + a small
  supplicant warrior (326 px wide, from f1+f2+f3).
- `work/virash/bg_seg2_reapers.png` — two scythe-wielding skeletal reapers in robes
  (207 px, f4+f5).
- `work/virash/bg_seg3_ghost.png` — the draped ghost (143 px, f6).
- `work/virash/bg_wide_layout.png` — the three segments butted into one wide
  background (676 px, **no Virash, no separators**); the only discontinuities are
  the two coverage-gap seams at x≈326 and x≈533.
- `work/virash/bg_all6_frames.png` — the 6 raw displayed frames for reference.

A fully seamless panorama still needs the two gap frames; otherwise this is the
usable wide canvas for designing subtitle placement.

### Sprite extract / pack tool — DELIVERED

`langrisser/bg_sprites.py` drives the verified **type-8 codec** (from
`langrisser.imgdat`) against MAP_C, **per asset** (TOC-aware): it lists assets/groups
and round-trips any group through an indexed PNG, **byte-exact and size-preserving**
(data files are gitignored under `work/virash/`):

- `list MAP_C.DAT` → enumerate the **172 assets** (offset range, size, image count,
  palette count, widths).
- `list MAP_C.DAT --asset 108` → the image groups inside one asset (absolute
  offset, packets, WxH).
- `extract MAP_C.DAT 0x19b8800 grp.png` → decode one group to an indexed PNG with
  the palette **auto-picked from the group's own asset** (no manual CLUT needed;
  `--palette file.bin` overrides with a raw 256×BGR555 file). The group's absolute
  `start,cnt,width,block_rows` is stored in a PNG `mapc_group` text chunk.
- `pack grp.png MAP_C.DAT` → re-encode the edited PNG into that asset's group and
  splice the asset back into the file (file size asserted unchanged; packet
  headers/padding untouched ⇒ unedited round-trip is byte-identical).
- `selftest MAP_C.DAT` → for every asset, decode→encode→splice and assert the file
  is unchanged (**167 assets pass**; MAP_C MD5 unchanged after extract→pack).

Supporting files (gitignored, in `work/virash/`): `MAP_C.DAT` (extracted from disc
LBA 25022).

**What works now:** coherent background **frames** and all **8-px strips** extract
and repack from the data with correct **per-asset** colours — no Virash, no VRAM.
**Remaining blocker:** the 128-wide **atlas** groups store the panorama as 8-px
vertical strips in non-screen order, so the full coherent scrolling background
still needs the **strip→screen placement map** (runtime tilemap / EXE
upload+placement) before a subtitle can be painted in place on it.

### Exported for viewing

- `work/virash/scene_f1.png`, `scene_f4.png`, `scene_f6.png` — the **displayed
  frame** (368×240, 2×): static Virash on the left, the scrolling cave scene
  (red moon mood, a **scythe-wielding skeleton/death** on the right), and a
  **black band along the bottom** (and right edge).

### Open for Option C

- Crack the MAP_C swizzle/tilemap so a specific on-screen region can be edited.
  The rigorous way: parse the live GPU list's SPRT UV+screen-position pairs
  (the tilemap) and invert it, or find the texture-upload/tilemap code in the EXE.
  (Static reshape guesses have failed.)
- Decide where text goes: the clean **bottom black band is static** (engine-
  cleared, ~Y≥192), so it is likely **not** part of the bakeable scrolling
  texture — confirm whether the background texture's bottom rows are black-and-
  scrolling (bakeable) or the band is cleared FB (needs a hook). The background
  imagery itself is busy (cave/skeleton), so baked text there has poor contrast.

Net: the asset is found and the format is cracked — but it is a **16×16 tile
bank**, not a wide picture. Two hard consequences for Option C:

1. **The wide image must be reconstructed, not read.** It does not exist as stored
   data; it is recovered by stitching the framebuffers (done — see
   `bg_wide_layout.png`, with two coverage-gap seams) or, for a directly-editable
   asset, by inverting the runtime tilemap (still open).
2. **Baking into the bank does not give clean scrolling text.** Because the scene
   reuses tiles, editing a tile in the bank changes *every* place that tile is
   drawn — text painted into the bank would repeat/scatter, not read as one
   scrolling subtitle line. So even with the panorama image in hand, it would serve
   only as a **positioning reference/mock-up**, not a directly-editable asset.

This strengthens the doc's earlier read (§"Implication for subtitling"): an
**engine-drawn overlay over the bottom black band (approach 1)** is the technically
sound route; pure asset-baking (Option C) is fighting the renderer.
