# Crosswire: Train Graph Task Factory (Innodata Track 2)

A generator that builds hard visual reasoning tasks from **graphic railway timetables** (train graphs, time–distance diagrams). Each seed produces structured timetable data, a deterministic PNG drawn from that data, a question, a code-computed answer, and a table of wrong-answer "traps" (fingerprints) that label how a model failed.

## Problem
Graphic timetables are used by railway planners and dispatchers to see overtakes, meets and line occupancy at a glance. Reading them needs following dozens of crossing coloured lines, so the image is required, not optional.

## Pipeline
seed → timetable data (JSON) → deterministic render (matplotlib, fixed fonts) → question program → answer computed from data → fingerprints (strategy traps + single slips) → validation → evaluation.

Question programs (`factory/families/traingraph/programs.py`):
| Program | Reasoning required |
|---|---|
| `tg_overtakes` | Follow one slow train; list every same-direction train that passes it while it stands, in order |
| `tg_meets` | Count every opposite-direction line crossing one train's line |
| `tg_section` | Cut the chart at a time and a stretch of stations; name every train inside |
| `tg_nth` | Order arrivals at a station across both directions; name the n-th |
| `tg_longest` | Find the longest single stop anywhere and trace it to its train number |

Levels 1–5 scale stations (6–10), trains (≈18–48) and pattern density.

## Why it is hard / how models fail
Every answer needs several lines followed through dense crossings, and no single label or legend entry gives it away. The likely ways a model fails are built in as traps: losing a line at a crossing, ordering by departure time instead of pass time, counting trains that are standing, and misreading the 10-minute grid. Each task stores its armed traps, and `eval/fingerprint.py` labels each wrong answer with the trap it matches.

## How to use

```bash
pip install -r requirements.txt

# build one fresh task, validate it, and score it with mock backends (no API key needed)
python -m factory fresh --seed 5001 --program tg_meets --level 5 --models mock:oracle

# build a task folder
python -m factory generate --out tasks/final --program tg_overtakes,tg_meets,tg_section,tg_nth,tg_longest --seeds 5000-5001 --levels 5

# validate every task in a folder
python -m validate.run_validate --tasks tasks/final

# evaluate the two models (needs the organizer key in the environment, never in a file)
export OPENROUTER_API_KEY=...        # PowerShell: $env:OPENROUTER_API_KEY="..."
python -m factory eval --tasks tasks/final --results results/final --models anthropic/claude-haiku-5.5,openai/gpt-6-luna

# organizer format checker
python -m factory check
```

## Run in Docker
```bash
docker build -t crosswire .
docker run --rm -v "$PWD:/work" -w /work crosswire fresh --seed 5001 --program tg_meets --level 5 --models mock:oracle
docker run --rm -e OPENROUTER_API_KEY -v "$PWD:/work" -w /work crosswire eval --tasks tasks/final --results results/final --models anthropic/claude-haiku-5.5,openai/gpt-6-luna
```
Same seed, same generator version → same task (pixel hash stored). Models: `anthropic/claude-haiku-5.5`, `openai/gpt-6-luna`, temperature 0, reasoning effort high. A fresh task is built with any unused seed.

## Reported folders
- Tasks: `tasks/final`
- Results: `results/final`

## Pilot results
Reported run on OpenRouter (`results/final`): 10 fresh level-5 tasks (seeds 5001-5002, two per question type), temperature 0, reasoning effort high, every reply complete (`finish_reason = stop`).

| Model | Correct |
|---|---|
| anthropic/claude-haiku-5.5 | **0 / 10** |
| openai/gpt-6-luna | **0 / 10** |

0/2 for both models on every question type. Wrong answers matched a stored trap in 6 of 20 replies (ordered by departure 2, up trains only 2, missed the last overtake 2); the other 14 were unmatched. Smoke test before the run: one level-3 meets task, Haiku 8 and Luna 9 against the answer 14.

Earlier, informal proxy pilots with Haiku subagents (not reported results) dropped other image types: logic analyzer 24/24, metro map 4/5, disc diffusion 5/5, hemocytometer 4/5.

## Validation (what each check establishes)
Schema, file/pixel hashes, re-render equality, legibility at 0.667× downscale, OCR of labels (Docker), a pixel check that every train in the data is drawn along its whole path in its colour with its number at its start and no stray line pixels, a second solver (time sampling) that must agree with the generator's exact crossing solver, answer-not-in-question, fingerprint consistency, and image dependence (twin scene with identical question text and a different answer).

## Hand check
Tasks in `tasks/final`: tg-tg_overtakes-L5-s5001, tg-tg_overtakes-L5-s5002, tg-tg_meets-L5-s5001, tg-tg_meets-L5-s5002, tg-tg_section-L5-s5001, tg-tg_section-L5-s5002, tg-tg_nth-L5-s5001, tg-tg_nth-L5-s5002, tg-tg_longest-L5-s5001, tg-tg_longest-L5-s5002. Each still needs a teammate's hand check (`python -m factory handcheck`).

## Reused
matplotlib, numpy, Pillow, requests, pytesseract; organizers' `check_submission.py` vendored in `tools/`.

## Limitations
- The pixel check confirms the image shows the data; it does not rebuild the timetable from pixels alone.
- Temperature is not listed as a supported parameter for either model on OpenRouter; it is sent and recorded.
- `archive/` holds earlier families (logic analyzer, ownership charts, wiring panels) that were dropped; they are not part of the submission.

## Team
To be filled in.
