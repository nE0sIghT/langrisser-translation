# Speaker plate test set

In-game–verified speaker plates. `langrisser/check_speakers.py` asserts that
`semantic_plate_slots` (the per-record plate extractor used by the line wrapper)
resolves each record below to the listed speaker. A mismatch means the plate
reserve — and therefore the line wrapping — is wrong for that record, so this is
a **mandatory** check (see AGENTS.md).

How to use it:

- Run `python3 -m langrisser.check_speakers`; it must print `OK`.
- When you confirm a plate in game (the name shown on a dialogue window), add a
  row here. Each row is `| record | speaker | phrase |` under its `## Chunk N`
  heading. `record` is the record index in `data/games/l5/lang/en/SCEN/chunk_N.txt`;
  `speaker` is the exact English plate name, `(no plate)`, or
  `(location/crowd)`; `phrase` is the line, for humans.
- A failure here is a real bug in the extractor, not a reason to edit the test:
  fix `semantic_plate_slots`, do not relax the expected value.

Background and the decoded mechanism are in `docs/L45_SPEAKER_PLATES.md`.

## Chunk 4

Name pool: Sigma, Lambda, Clarett, Alfred, Brenda, Lanford, Town, Goldry, Wiler,
Officer, Guard, Selena. A town under attack; the panicking residents speak under
the 町人 = "Town" plate (kept short for the plate width).

| record | speaker | phrase |
|---|---|---|
| 79 | Town | I refuse to die caught up in someone's war！ |

## Chunk 45

Name pool: Sigma, Lambda, Clarett, Alfred, Brenda, Lanford, Machine, Voice, Woman.
This is the opening laboratory dialogue and includes both plated speech and
unplated continuation/thought records.

| record | speaker | phrase |
|---|---|---|
| 10 | Machine | ∑066…cultivation program complete. |
| 11 | Machine | Awakening program complete. |
| 12 | (no plate) | …Mh…nh… |
| 13 | Voice | …Sigma… |
| 16 | (no plate) | …Let me sleep. |
| 17 | Voice | Wake up, Sigma. |
| 18 | (no plate) | Who's there！？ |
| 19 | Woman | Awake at last, ∑066. |
| 21 | Woman | Your development code, ∑066. |
| 22 | (no plate) | ∑066… ^052… Lambda…？ |
| 23 | Lambda | What is it, ∑066… |
| 24 | (no plate) | Huh！？ Why is my sword out… |
| 25 | Lambda | Will you cut me down or not？ |
| 31 | Lambda | Yes. Beings made in the same culture tank. |
| 33 | Lambda | So you were never taught. |
| 40 | Sigma | Fine. Sigma will do. |
| 41 | Sigma | My name is <$F600><$0000>. |
| 47 | Machine | Intruders in the laboratory. |
| 48 | Lambda | Enemies, it seems. |
| 49 | Sigma | Whew. Let's go, Lambda. |

## Chunk 69

Name pool: Sigma, Mariandel, Clarett, Alfred, Brenda, Lanford, Virash.
Mariandel is the blue-haired girl.

| record | speaker | phrase |
|---|---|---|
| 20 | Mariandel | Did something happen between you and that man? |
| 26 | Mariandel | But what reason could he have to attack a village? |
| 31 | Mariandel | I can't believe there was such a terrible war… |
| 46 | Alfred | …Reynolds… don't tell me… Brother joined without knowing Lainforce's true aim… |
| 47 | Brenda | He's likely being used. |
| 48 | Alfred | Everyone, lend me your strength this time. |
| 49 | Virash | Right. Stopping the Three-Country Alliance also means… |
