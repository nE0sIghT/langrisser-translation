# Translation Compression Debt

This file tracks places where English wording was intentionally compressed or
where a later polish pass should restore lost nuance if the byte budget allows.
It is not a list of every short translation. Add an entry only when meaning,
character voice, tutorial clarity, lore detail, or narrative tone was knowingly
reduced to fit the current script budget.

## Policy

- Preserve the current build first: file sizes must not grow and validator
  checks must stay green.
- When a line must be shortened enough to drop nuance, add an item here before
  committing the scenario.
- During a polish pass, revisit the item, compare JP and EN in the review HTML,
  then either expand the line or mark the item closed with the reason.
- Prefer recovering meaning by better wording, new pair glyphs, or global
  repack slack before accepting permanent loss.

## Reviewed Range

Reviewed commits from `26448ea3ad309514013b8ad7c2e4f66e56c2a30c` through
`0c3259f`:

- `26448ea` AGENTS trailer policy only, no script compression.
- `73b0605` scenario 6 epilogue.
- `a0de094` control-aware rewrap/name pair encoding changes.
- `fcfecd0` through `0c3259f` scenarios 7 through 20.

The items below are the concrete compression risks found during this pass.
Scenarios/chunks not listed here had no obvious meaning loss during this review,
though they still need normal playtest and style polish.

## Open Items

### Russian SYSTEM.BIN voice-cast list (`data/games/l5/lang/ru/system_strings.json`)

| ID | Location | Compression |
| --- | --- | --- |
| RU-HD-001 | `group:0:257-289` | Voice actors are shown as a given-name initial plus full surname. The original lists full names, but their Russian transliterations overflow the fixed shared UI group. Restore full names if the group can later borrow space safely. |

### SYSTEM.BIN triangle-button help (`data/games/l5/lang/en/system_strings.json`)

The help strings are glyph runs in offset-table groups (see
`docs/L45_SYSTEM_BIN_FORMAT.md`); each display line is one on-screen line. English
was compressed to fit, line by line. Items below are the knowing semantic
losses. With `langrisser/system_pack.py --repack` a line is no longer bound to the
original byte length (only the group total and the on-screen width), so some of
these can be reopened once the repack layout is verified in an emulator.

| ID | Location | Compression |
| --- | --- | --- |
| HD-002 | Spell stat lines (`属性:/目標:/解除:`) | The formal "Attribute / Target / Dispel" labels were shortened to `Element. Hits N unit/troop/area. Cure: X`. Tone/formality reduced; meaning kept. |
| HD-003 | Weapon/armor/unit flavor descriptions | Trailing hedges ("...という" = "they say") and decorative adjectives were dropped per line to fit; lore is preserved but prose is terser than the JP. |
| HD-004 | `%`-free rewrites | A few stat lines that used "割" (tenths) were written as multipliers (`AT x1.2`, `MP x1/4`) instead of percentages where the phrasing did not fit. |

## Closed Items

| ID | Scenario / chunk | Records | Resolution |
| --- | --- | --- | --- |
| CD-001 | Scenario 12, `chunk_012.txt` | `48` | Closed 2026-06-14. Restored the step-by-step Snow Dragon egg event, including the silent wait, sudden gust, falling egg catch, parent's return, formal thanks, and future-reward vow. |
| CD-002 | Scenario 12, `chunk_012.txt` | `104` | Closed 2026-06-14. Expanded Alfred's criticism to state that the villagers would have starved without the party and that they ran without trying to act. |
| CD-003 | Scenario 19, `chunk_019.txt` | `55` | Closed 2026-06-14. Restored Glob's miscalculation, the heirs-of-light ploy, Gilmore weakening mankind, the corpse pile, and Langrisser being in demon hands. |
| CD-004 | Scenario 19, `chunk_019.txt` | `135` | Closed 2026-06-14. Rephrased the Pondbag/Umagee recommendation with the amusement-hall framing and "interesting things" detail. |
| CD-005 | Scenario 20, `chunk_020.txt` | `50` | Closed 2026-06-14. Restored the single red lotus image, the beautiful woman appearing on the lake surface, and her granting the sword. |
| CD-006 | Scenario 20, `chunk_020.txt` | `98-101` | Closed 2026-06-14. Restored the warmer thanks exchange, including saving thanks until Kalxath is peaceful and Clarett's happiness that everyone lends strength to the country. |
| CD-007 | Scenario 20, `chunk_020.txt` | `103` | Closed 2026-06-14. Expanded the Snow Dragon reward line to state that the children grew strong thanks to the party and that the dragon came to repay the debt. |
| CD-008 | Scenario 11, `chunk_055.txt` | `31` | Closed 2026-06-14. Restored Glob's reasoning that there is no reason not to wait if waiting improves the situation, and that the scheme has borne fruit. |
| CD-009 | Scenario 7, `chunk_093.txt` | `73` | Closed 2026-06-14. Restored the advice that standing around lost in thought changes nothing, so the party should move first and then chase the swords. |
| CD-010 | Scenario 12, `chunk_098.txt` | `46` | Closed 2026-06-14. Expanded Clarett's introspection about fleeing from others, relying on others' judgment, Brenda's criticism, needing to change, and finding her own way to save Kalxath. |
| CD-011 | Scenario 12, `chunk_098.txt` | `75` | Closed 2026-06-14. Restored the contingency wording that they may have to act themselves and may need the party to work for them. |
| CD-012 | Scenario 16, `chunk_016.txt` | `14,17` | Closed 2026-06-14. Restored the "just received information" framing and the Teleport Ring received from King Gilmore as the transfer method. |
| CD-013 | Scenario 20, `chunk_064.txt` | `16,18,22,24,35` | Closed 2026-06-14. Expanded the lore exposition around Glob as one of Boser's demon generals, near-immortality, Chaos as the power source, and the Langrisser/Alhazard human-demon war framing. |
| CD-014 | Scenario 4, `chunk_090.txt` | `29` | Closed 2026-06-14. Reviewed against the JP line; the current translation already preserves the promised general post, resented captaincy, mercenary-company return, and "you will regret losing us" bluster. |
| CD-015 | Scenario 5, `chunk_049.txt` | `8,10,12,22,24` | Closed 2026-06-14. Restored the trimmed family-scene softeners, including "his old self", Alfred's follow-up "Don't you think？", and "Admiral Wheeler" in narration. |
| CD-016 | Scenario 24, `chunk_110.txt` | `55` | Closed 2026-06-14. Restored Lainforce's sharper jab that he would never let her face danger, even at the cost of his life, and would not lose to such a man. |
| CD-017 | Recap, `chunk_129.txt` | all recap records | Closed 2026-06-14. Full JP/EN review found dense wording but no actionable lost lore, chronology, or character framing requiring text changes. |
| CD-018 | Recap/bios, `chunk_130.txt` | all ending biography records | Closed 2026-06-14. Full JP/EN review found compressed ceremonial phrasing but preserved the branch outcomes, character epilogues, deaths, marriages, reforms, and world-state details. |
| CD-019 | Scenario 2 battle, `chunk_002.txt` | `15,21,23,33,46,49,53,63,66,71,76,79,84,88,90,96,97,103,104,113` | Closed 2026-06-13. The fuller text was restored after the battle suffix alignment rule was confirmed: chunk `002` may shift its suffix when the new suffix start remains 4-byte aligned. In-game testing confirmed battle images/portraits stayed intact. |
| HD-001 | SYSTEM.BIN help stat lines that decoded with an `N`/`up`/`down` placeholder | Closed 2026-06-17. Not a runtime value at all: glyph code `0x000A` is the digit `3` in this font, but the dump decoded it as a line break, so embedded `3`/`13`/`30`/`35`/`38` numbers were lost. Fixed the decoder and restored every real number from the data (e.g. "Attack cost +3", "Summon MP: 35", "Sell at 3/4 buy price", "Monster 13", "Skill: Petrify 3"). No RAM dump needed. |

## Langrisser I & II

Записи, где русский текст пришлось ужать против японского из-за ширины окна
(21 ячейка, парные глифы) или из-за запаса чанка. Заносить **до** коммита
сценария.

| Сценарий | Запись | Что потеряно | Статус |
| --- | --- | --- | --- |
| l1 / 0 | 5/22 | `父上の事が心配だ` — «неспокойно за отца» ужато до «неспокойно»: строка не влезала в окно | открыто |

## Langrisser I & II, Russian

The window is 15 cells by 3 lines and the objectives panel is one line per
condition, so a condition that reads as a clause in Japanese has to read as a
label in Russian. Everything below is a place where that, or a reference the
script substitutes at runtime, cost the line something the Japanese said.

| ID | Scenario / chunk | Records | What was lost |
| --- | --- | --- | --- |
| RU-001 | L1 scenario 1, `chunk_000.txt` | part 6 `3` | `レディンがナームと合流` — "joins up with Naam" became "reached Naam". The panel line does not fit the reunion sense. |
| RU-002 | L1 scenario 2, `chunk_001.txt` | part 6 `3`, `4` | The gate is "the upper-left gate of the map"; the panel says "the gate at the top left". |
| RU-003 | L1 scenario 4, `chunk_003.txt` | part 6 `3` | `１６ターンの間レディンが生存する` — "survives for 16 turns" became "is alive 16 turns". |
| RU-004 | L1 scenario 5, `chunk_004.txt` | part 6 `3`, card | `黒騎士ランスの撃破` lost "the Black Knight"; the panel names Lance only. The epithet is still said in the dialogue. |
| RU-005 | L1 scenario 6, `chunk_005.txt` | part 6 `3`, card | `占領軍司令ゼルドの撃破` lost "commander of the occupying army". |
| RU-006 | L1 scenario 11, `chunk_010.txt` | part 6 `3`, `4` | `最上階の階段` — "the staircase to the top floor" became "the staircase up". |
| RU-007 | L1 scenarios 14, 15, `chunk_013.txt`, `chunk_014.txt` | part 6 `6` | `ＮＰＣの全滅` says NPC, which is the game talking about itself. The panel says "all the townsfolk", which is what those NPCs are in both scenarios. |
| RU-008 | L2 scenario 2, `chunk_001.txt` | part 7 `0` | The card's last page names Riana once in Japanese and the Russian wanted her twice for the sentence to read naturally; the second mention was dropped to keep the reference sequence. |
| RU-009 | L2 scenario 3, `chunk_002.txt` | `155` | Morgan's jibe calls Zolm a fool of a commander through the name table's `指揮官`. Russian cannot decline a substituted word, so the line says "this fool — and he a commander" rather than "a fool of a commander". |
| RU-010 | L2 scenario 3, `chunk_002.txt` | part 6 `6` | `司祭・神官の全滅` distinguishes the high priest from the temple clergy; the panel line says "all the priests". |
| RU-011 | L1 shared table, `shared.txt` | part 1 | Fifteen unit labels were shortened to buy back bytes the container needed: `帝国軍指揮官` is "Командир" rather than "Командир имперской армии", `グレートドラゴン` is "Дракон", `リビングアーマー` is "Доспех", `バンパイアロード` is "Вампир", `デーモンロード` is "Демон". The table sits in all 21 chunks, so each byte counts 21 times. |
| RU-012 | L1 scenarios 1 and 12, `chunk_000.txt`, `chunk_011.txt` | part 7 `0`, `5/1`, `5/23`, `5/31`, `5/59`, `5/60`, `5/40` | The two briefing cards' narration was tightened and six lines reworded to fit the container: the Baldea card no longer says the sword was passed down "from generation to generation", and Digos's siege line drops "with an army". |
