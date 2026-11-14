# innodata-hack

A pipeline that generates verifiable visual tasks for evaluating AI models. Each task has an image, a question, structured data, a computed reference answer, and validity checks. Two models (Claude Haiku 5.5 and GPT-6 Luna) are evaluated on every task through OpenRouter and their answers are scored.

**Status:** the runner is built and self-tested. The generators (the code that makes the actual tasks) now include a family of synthetic patient-monitoring chart generators. `python3 run_tests.py --selftest` passes 149 checks with no keys and no network. Live calls to OpenRouter have **not** been tested yet.

## Quick start

```
python3 -m venv .venv && source .venv/bin/activate     # Python 3.12+ recommended (3.10+ works)
cp .env.example .env                                   # then put OPENROUTER_API_KEY=... in .env (git-ignored)
python3 run_tests.py --selftest                         # proves the runner works; needs no keys
python3 -m generate.new_generator my_chart              # step 1: create a generator, then fill it in
python3 -m generate.new_generator my_flow --mermaid     # ...or a Mermaid-diagram generator (fixed question), see "Mermaid generators"
python3 run_tests.py --generate --dry-run               # generate tasks, validate, preview the model calls (no spend)
python3 run_tests.py                                    # the full run (needs model slugs + key, see "Configuration")
```

The runner uses only the Python standard library. `requirements.txt` is unchanged: `pillow`/`pandas` are there for generators to use, and `anthropic`/`openai` are not used by the runner (OpenRouter is called over plain HTTPS).

## Layout

```
run_tests.py   entry point: runs stages 0-7 (or a range), selftest, packaging
runner/        one module per stage, plus output, config, schema, selftest, package
generate/      pipeline steps 1-2: the generator contract + generators/ (teammates add files here)
validate/      pipeline steps 3-5: the checks behind stage 2, and the hand-check ledger
eval/          model calls (OpenRouter), answer extraction, scoring, reporting
tasks/         tasks.jsonl + images/ + data/ (+ source/ for diagram source code such as Mermaid, + hand_checks.json once tasks are hand-checked)
results/       run_config.json, responses.csv, validation.json, summary.md, raw/ replies, logs/
slides/        not yet described
README.md      this file
```

### Pipeline steps

| Step | Purpose | Where |
|------|---------|-------|
| 1 | Generate and save structured data | `generate/generators/<your_file>.py` -> `generate_data()` |
| 2 | Render the image from the data | `generate/generators/<your_file>.py` -> `render()` |
| 3 | Compute the reference answer in code | `compute_answer()` in the generator; re-checked by `validate/check_answer.py` |
| 4 | Check the rendering matches the data | `verify_render()` in the generator; run by `validate/check_render.py` |
| 5 | Verify the question requires the image | `validate/check_needs_image.py` (leak check; optional `question_needs_image()` hook) |

`python3 -m generate.generate_tasks` runs steps 1-3 for every enabled generator and writes `tasks/images/<id>.png`, `tasks/data/<id>.json`, `tasks/tasks.jsonl`, and (for generators that draw from source code) `tasks/source/<id>.mmd`. `python3 -m validate.run_all` runs the checks (steps 3-5) on their own.

### Runner stages

`python3 run_tests.py` runs stages 0-7 in order, or a selected range, and prints evidence for each rubric item. Each stage is a module in `runner/` that exposes `run(ctx)`; shared state is in `runner/context.py`; colors and the log copy are in `runner/output.py`.

| Stage | Module | What it prints | Rubric item |
|-------|--------|----------------|-------------|
| 0 | `runner/preflight.py` | Python version, required files, env var names set or not (never values), model slugs | Runnable submission |
| 1 | `runner/load_tasks.py` | Task count and schema problems (with line numbers) | Reliable generation |
| 2 | `runner/validate_tasks.py` | One line per check for each task, tagged (a)-(h), then a per-letter summary | Realistic/useful visuals (g, h), reliable generation (a, b), fair/verifiable tasks (c-f) |
| 3 | `runner/variation.py` | Counts per generator/visual/question type, near-duplicate warnings | Meaningful variation |
| 4 | `runner/evaluate.py` | Progress per model call, tokens, running cost; invalid replies flagged and re-run | Model evaluation |
| 5 | `runner/score.py` | Per-model accuracy, per-task pass/fail, too-easy and unsolved tasks | Task difficulty |
| 6 | `runner/format_check.py` | Organizer format checker result, if configured | Runnable submission |
| 7 | `runner/scorecard.py` | Rubric scorecard (READY / ATTENTION / HUMAN / NOT RUN) with evidence, files written | All items |

Stage 2 check letters: (a) structured data loads, (b) image renders from the data, (c) reference answer recomputed in code, (d) rendering matches the data, (e) question requires the image, (f) numeric tolerance stated in the prompt and used in scoring, (g) real-world examples cited, (h) hand-checked.

Output markers: `[PASS]`, `[FAIL]`, `[WARN]`, `[INFO]`, `[SKIP]`. Colors are off with `--no-color`, when `NO_COLOR` is set, or when output is not a terminal. Every run writes a plain-text copy of the log to `results/logs/`.

Exit code: 0 when the run is clean, 1 when any stage fails, aborts, or (with `--strict`) warns, 2 when the config cannot be read.

## How to run

```
python3 run_tests.py                        full run, stages 0-7
python3 run_tests.py --stage 2-5            a range (also: --stage 4, --stage 0,2-4)
python3 run_tests.py --dry-run              no API calls; shows what stage 4 would call
python3 run_tests.py --task <task_id>       limit the per-task stages to one task (several ids allowed)
python3 run_tests.py --models "GPT-6 Luna"  only these models (comma-separated names from run_config.json)
python3 run_tests.py --generate             run the generators first (right after preflight), then the stages.
                                           A FULL run still calls the models, so it needs the key; use --dry-run or --stage 0-3 to generate without spending
python3 run_tests.py --force                stage 4: ignore cached replies and call the models again
python3 run_tests.py --strict               treat warnings as failures
python3 run_tests.py --no-color             plain output
python3 run_tests.py --selftest             end-to-end test with demo generators in a temp folder
python3 run_tests.py --package              secret scan + submission zip (see "Packaging")
```

Other entry points (all run from any folder):

```
python3 -m generate.new_generator NAME                    create generate/generators/NAME.py from the template
python3 -m generate.generate_tasks [--count N] [--generator NAME]   run the generators -> tasks/ (--generator keeps other generators' tasks)
python3 -m validate.run_all                               validate every task
python3 -m validate.hand_check TASK_ID --by "Your Name"   record that a human hand-checked a task ("all" works too)
```

Evaluation is idempotent: valid replies are saved under `results/raw/<model>/<task_id>.json` and reused on the next run. Replies that are invalid (API error, cut off at the token limit, empty, filtered) are saved but never scored and never cached, so running stage 4 again re-runs exactly those. Wrong answers are data, not failures.

## Writing a generator

A generator makes one family of visual tasks. Create one with `python3 -m generate.new_generator my_chart`, fill in four methods, and set `enabled = True`:

```python
class MyChartGenerator(TaskGenerator):
    name = "my_chart"
    visual_type = "bar chart"
    question_type = "read one bar"
    answer_type = "number"                 # "number" | "text" | "choice"
    tolerance = 5                          # numeric answers: the tolerance sentence is added to the prompt for you
    real_world_examples = ["where this kind of chart is really used (1-2 required)"]

    def generate_data(self, rng, index): ...      # step 1: a JSON-able dict (use ONLY the rng you are given)
    def render(self, data, out_path): ...         # step 2: draw the image from that dict
    def compute_answer(self, data): ...           # step 3: the reference answer, computed in code
    def make_question(self, data): ...            # the question text
    # optional: verify_render(data, image_path) -> CheckResult, question_needs_image(...) -> CheckResult
```

Rules the runner enforces: everything is deterministic from the seed (no global `random`, no clock); `render()` and `compute_answer()` only see the data that was saved to disk; the question must not contain the answer or list the data values. The full contract is the docstring in `generate/base.py`. `python3 run_tests.py --selftest` contains two complete worked examples (stdlib-only PNG bar charts).

### Optional settings for generators

All optional; the defaults are the plain behavior above.

| Setting | What it does |
|---------|--------------|
| `question_template = "..."` | The question text, used instead of writing `make_question()`. `{field}` placeholders are filled from the data dict (write `{{` for a literal brace) |
| `fixed_question = True` | Every task deliberately asks the identical question. The variation report (stage 3) then prints an info line ("by design") instead of a near-identical-question warning. Only valid when the template has no `{placeholders}` |
| `exact_answer = True` | The number is an exact count, not a visual estimate, so the "no tolerance" warning is not shown |
| `source_key = "mermaid"`, `source_ext = "mmd"` | `data["mermaid"]` holds the diagram's source code. The runner checks it is there (a clear error if not) and also saves a copy to `tasks/source/<task_id>.mmd` so a person can open it. The data JSON stays the source of truth |

## Mermaid generators

Use this when the picture is a Mermaid diagram and every task asks the **same question** (only the diagram and the answer change).

```
python3 -m generate.new_generator my_flowchart --mermaid
```

That copies `generate/generators/_template_mermaid.py`, a complete working example (a random flowchart; the question is "how many arrows on the shortest route from Start to End?"). Read its top docstring first, then change how the graph is built.

**Where the Mermaid code is stored.** In three places, all made by the runner:
1. `tasks/data/<id>.json`, key `mermaid`. This is the source of truth; `render()` reads it from here.
2. `tasks/source/<id>.mmd`. A plain-text copy to open or paste into the Mermaid live editor.
3. `tasks/images/<id>.png`. The picture the models see, drawn from (1).

**Checklist for the teammate**
1. Run the command above. Open the new file.
2. Install the Mermaid command-line tool once: `npm install -g @mermaid-js/mermaid-cli` (needs Node.js and a headless Chromium). If `mmdc` is not on your PATH, set the `MMDC` environment variable to its full path.
3. In `generate_data()`, build the **structure** first (nodes, edges, labels), then build the Mermaid text from it. Make it as complex as you want. Use only the `rng` you are given.
4. In `compute_answer()`, compute the answer from the structure, never from the picture or by hand.
5. Edit `question_template` if you want different wording. It must not contain the answer or list the diagram's contents. Keep `fixed_question = True`.
6. Cite 1-2 real examples in `real_world_examples` (the rules require it).
7. Keep `verify_render()`: it proves the Mermaid text contains exactly the edges in the data. Extend it if your diagram has more than `A --> B` edges.
8. Make the **answers vary** across tasks. With one fixed question, varied answers and different diagrams are what make the set "meaningfully varied"; stage 3 warns if half the tasks share one answer or any two tasks share identical data.
9. Set `enabled = True`, run `python3 -m generate.generate_tasks --generator my_flowchart --count 3`, and **open the three images** in `tasks/images/` to see they look right.
10. Run `python3 -m validate.run_all`, then hand-check each task: `python3 -m validate.hand_check TASK_ID --by "Your Name"`.

**Honest limits.** The plumbing (storing the source, calling `mmdc`, the checks) is self-tested with a stand-in `mmdc`; rendering with the real Mermaid tool has not been tried here, so run step 9 on a machine that has it. `verify_render()` checks the Mermaid text against the data, not the drawn pixels. A very large diagram may need a bigger `width` (an argument to `render_mermaid`) to stay readable for the models.

## The patient-monitoring chart generators (vitals)

**These are synthetic monitoring-data reading tasks. They are not clinical cases, not clinically validated, and not diagnoses or medical advice.** The "patients" are made up from simple rules and a seed.

Each task is a PNG of 2-4 stacked panels (A-D), one vital sign per panel (heart rate, oxygen saturation, respiratory rate, systolic blood pressure, temperature) over time. A panel is flat, or flat and then a steady change, sometimes followed by a partial recovery, with seeded noise and some missing readings. Four easy generators (now switched off) ask four kinds of question about the same kind of chart:

| Generator | Question | Answer | Tolerance (written in the question) |
|-----------|----------|--------|-------------------------------------|
| `vitals_onset` | at what minute does the steady trend in panel X begin? | minute | 2 readings or 6% of the duration, whichever is larger |
| `vitals_final_value` | what is the last recorded value in panel X? | number | 3% of that panel's axis range (at least one unit) |
| `vitals_threshold` | at what minute is the first reading above/below a stated value? | minute | same as onset |
| `vitals_first_change` | which panel's change begins earliest? (same question every time) | panel letter | exact |

- **Levels 1-10** are stored in each task's data. Higher levels have more panels (2 -> 4), more noise (x0.5 -> x1.5), missing readings (none up to level 2, then up to about 10%) and longer, denser time axes. Axis scales differ from panel to panel (tight or wide), so trends can look alike but mean different things. Each generator starts at a different level offset so a few tasks per generator still cover easy to hard.
- **Answers come from the saved data**, never from the picture: onset and the first-change panel from the stored trend parameters, the final value and threshold crossing by scanning the stored readings. Stage 2 recomputes each answer from the seed and re-measures the PNG's pixels (`verify_render`) to prove the picture shows exactly the stored readings, gaps included.
- Same seed means the same data, picture bytes and answer, noise included (noise is a seeded, clipped gaussian). Threshold and first-change patients are re-drawn unless the answer is clearly more than the noise away from ambiguity.
- Drawing uses **Pillow only** (already in `requirements.txt`); no Node or Mermaid needed.
- Shared code is in `generate/generators/_vitals.py` (not registered as a generator); each question type is its own small file. `python3 run_tests.py --selftest` section J checks the rules.
- Known limits: all four share one visual type, so stage 3 warns "only 1 distinct visual type" (the ownership chart could be a second one); `vitals_first_change` has only 2-4 possible letters, so guessing succeeds fairly often; the tolerances are a judgment call to tune after the first real run.

## Review sheet (for humans to follow and test the tasks)

Every time tasks are generated, and every time results are scored, the pipeline writes two files next to the tasks:

- `tasks/review.html`: open it in a browser. Each task shows its picture and its question; the correct answer, the accepted tolerance and a "how to check it by hand" note stay **hidden until you click "Show correct answer"**, so a person can try the task first. After a live run it also lists what each model answered and whether it was marked correct.
- `tasks/REVIEW.md`: the same in plain text with everything visible.

The files that make up each task are in the repo too: `tasks/images/<id>.png` (the picture), `tasks/tasks.jsonl` (question and reference answer), `tasks/data/<id>.json` (the seeded data the picture was drawn from, including the answer's source). To regenerate the sheet on its own: `python3 -m generate.review_sheet`. To record that you checked a task by hand: `python3 -m validate.hand_check TASK_ID --by "Your Name"`.

## The ECG strip generators (the live tasks)

**The live tasks are synthetic ECG rhythm strips, and only those.** The vitals-chart and overlay generators are switched off (`enabled = False`, not deleted), so `python3 run_tests.py --generate` makes only ECG tasks.

**These are synthetic measuring-and-counting tasks, not clinical cases. They are not clinically validated, and no question asks for a diagnosis** (nothing asks what a rhythm "is" or whether anything is wrong). The use case: in real life an ECG is usually a paper strip, a PDF printout or a photo of a monitor, so the picture is often the only source and there is no table to give a model.

A strip is a made-up single-lead trace, 10 seconds at 250 samples per second, drawn on the standard pink grid (25 mm/s, 10 mm/mV; small box 0.04 s, large box 0.2 s) with a calibration pulse and strong seeded noise and baseline wander. Beats are sums of Gaussian bumps (P, Q, R, S, T). A "premature" beat comes early, is wider and taller, has no P wave and an inverted T.

| Generator | Question | Answer |
|-----------|----------|--------|
| `ecg_beat_count` | how many QRS complexes (beats) are on the strip? | exact, 24-36 (very fast 150-200 bpm, irregular spacing, strong noise) |
| `ecg_rate` | what is the heart rate in bpm, from the spacing between consecutive beats (not by counting)? | 60 / mean beat spacing, within 3 bpm (steady strip, 120-190 bpm) |
| `ecg_premature_beat` | counting from the left (first = 1), which beat is the early, wide one? | exact position (14+ beats, 110-150 bpm) |
| `ecg_interval` | how many large boxes from beat A to beat B, 5-8 beats apart? | from the stored beat positions, within 0.5 large box (fast irregular strip, 18+ beats) |
| `ecg_ectopic_count` | how many early, wide beats are on the strip? | exact, 3-5 (never adjacent, 100-140 bpm) |

The first live run (Haiku 10/10, Luna 6/10) showed the easy ECG strips were not hard enough, so the strips are now much faster and noisier (strong tremor-like noise, large baseline drift, tall T waves) and the questions need counting many beats or measuring across many beats. Whether the models fail them is measured by the live run, not assumed.

- **Data first.** The saved data holds all 2,500 samples and the list of beats (sample index and type). The picture is drawn from the samples; answers are computed from the stored beats. `verify_png` re-measures the picture (grid lines every 5 mm, the trace at every 6th sample and at every beat apex) to prove it shows that data.
- **Independent check.** Selftest section M finds the beats again from the raw samples with separate code and recomputes every answer from them.
- Same seed, same strip. Code is in `generate/generators/_ecg.py`; each question type is a small file. Pillow only, no new dependency.
- Known limits: ECG is one visual type, so stage 3 warns "only 1 distinct visual type"; the beat shapes are simple Gaussians and look cleaner than real traces; whether the models fail these tasks is measured by the live run, not assumed.

### The overlay generators (six lines on one chart; switched off)

For the hardest tasks, `generate/generators/overlay_*.py` draw six synthetic series on **one shared axis** ("% change from the first reading"), so the lines pile on top of each other and cross constantly. Colours are deliberately close in pairs (two blues, a red and an orange), and a legend maps letters A-F to the lines. Each chart has 91 or 121 readings per series. Answers come from the saved readings; section L of the selftest recounts them by separate code.

| Generator | Question | Answer |
|-----------|----------|--------|
| `overlay_cross_count` | how many times do lines X and Y cross? | exact whole number, 8-22 (no ties) |
| `overlay_highest_at` | which of the six lines is highest at minute M? | a letter A-F (winner leads by at least 0.7 points) |
| `overlay_peak_minute` | at what minute does line X reach its highest point? | minute, within one 2-minute interval (peak leads by at least 0.8 points) |

`verify_png` proves the picture matches the data; a reading hidden under another line is accepted only when another series' line is actually drawn on top of that spot. Overlay charts have their own visual type, so stage 3 now sees 2 distinct visual types.

### The hard vitals generators (the ones that are switched on)

The first live run showed that Claude Haiku 5.5 and GPT-6 Luna passed every task from the four easy generators above, so those four are **switched off** (`enabled = False`, not deleted). The tasks now come from three hard generators that use the same chart code, always at levels 8-10, on long dense time axes (91 or 121 readings per panel) with 4-5 panels:

| Generator | Question | Answer | Why it is hard |
|-----------|----------|--------|----------------|
| `vitals_gap_count` | how many separate gaps (runs of missing readings) does panel X's line have? | exact whole number, 9-20 | about one reading in eight is missing, so the line is chopped up and easy to miscount |
| `vitals_crossing_count` | how many times does the line cross the dashed reference line? | exact whole number, 8-22 | a noisy flat line weaves across the dashed line; every reading is at least 3 pixels from it, so no crossing is a judgment call |
| `vitals_exact_reading` | what is the value of panel X at minute M? | within 1 unit (0.1 for temperature) | M is a labelled gridline minute; the axes are tight, so 1 unit is at least 4 pixels, but a rough look is not enough |

Answers are still computed from the saved readings, not the picture, and `python3 run_tests.py --selftest` (section K) recounts every one by separate code. These tasks are meant to be solvable by a careful human with the gridlines and a lot of patience; whether the models fail them is measured by the live run, not assumed.

## The ownership chart generator

`generate/generators/ownership_chart.py` is a finished generator that is currently **switched off** (`enabled = False`; not deleted). Set it back to `True` to use it as a second visual type (it needs `mmdc`). Each task is a made-up web of people (pills) and companies (rectangles) with a percent on every arrow. The question is always the same: **how many people hold 25% or more of the target company?** (the target is the one company at the bottom that owns nothing; a person's stake is the sum over all routes of the product of the percents along the route).

| Level | Entities | Cross-holding chance | Gap to the 25% line |
|-------|----------|----------------------|---------------------|
| 1 | 8 | 0.05 | 40 points |
| 10 | 22 | 0.25 | 5 points |

Levels 2-9 are evenly spaced between these. With the default 10 tasks, task `ownership_chart_000` is level 1 and `_009` is level 10 (more tasks cycle through the levels again).

- A chart depends only on its **level and seed**, and both are written into the `.mmd` as `%% level:` and `%% seed:` comments. `build_chart(level, seed)` in the generator file returns the chart or `None`; a chart that breaks any structure, number or answer rule is skipped and the next seed is tried.
- Labels are always 16 px and the picture must fit in 2,000 px each way. The generator draws each candidate with the Mermaid CLI to measure it and tries the next seed if it does not fit, so **generating needs `mmdc`** (see "Mermaid generators").
- The rules are listed in the file's docstring and each one is checked in `python3 run_tests.py --selftest` (section I) by a separate route-by-route calculation.
- Known limit: because anyone above the line holds at least 27%, **the answer can only be 1, 2 or 3**, and it is 2 in roughly 70% of charts. A model guessing "2" does well, and stage 3 will warn that many tasks share one answer. See the open question in the guide.

## Configuration

`results/run_config.json` is the run configuration. Its keys are unchanged. **A value that still starts with `PLACEHOLDER` counts as "not set".**

| Key | Behavior |
|-----|----------|
| `models[].openrouter_slug` | Must be set for a live run. While it is a placeholder, stage 4 stops before any API call; `--dry-run` still works |
| `reasoning_effort` | Placeholder -> default `high`, with a warning in stage 0 |
| `max_tokens` | Placeholder -> default `16000`, with a warning in stage 0 (raise it if replies are truncated) |
| `temperature` | `0` |
| `pricing` | Hand-copied per-million prices. Used only when OpenRouter reports no cost for a call |
| `format_checker` | `{"enabled": true, "command": "python /path/to/checker.py {root}"}` turns stage 6 on |
| `run_date`, `git_commit`, `prompt_version`, `settings`, `last_run` | Written by the runner after a live evaluation; your other keys are never overwritten. Placeholder `reasoning_effort`/`max_tokens` are replaced by what the run actually used |

Optional extra keys (all have defaults): `team_name`, `track_number`, `seed` (1234), `task_count_target` (10), `budget_usd` (hard stop for one evaluation run), and the `eval`, `validation`, `variation`, and `submission` blocks (see `runner/config.py`, `DEFAULTS`).

The API key is read from the `OPENROUTER_API_KEY` environment variable (or `.env`, which is git-ignored and never packaged). It is never printed, logged, saved in `results/`, or zipped.

## tasks.jsonl record

One JSON object per line. The record keeps the repo's original fields, plus what the generator adds:

```
task_id, image_path, question, data (path to tasks/data/<id>.json), reference_answer, checks,
generator, index, seed, visual_type, question_type, answer_type, tolerance, real_world_examples, notes, schema_version
```

A record with `"_placeholder": true` is skipped everywhere and never scored. Running `generate` replaces the placeholder record with real ones.

## Packaging

`python3 run_tests.py --package` scans every file that would be packaged for API keys (and refuses if it finds one), requires every task to be hand-checked (`--allow-unchecked` overrides, with a warning), and writes `dist/<Team>_Track<N>.zip` without `.env`, caches, `.git`, virtualenvs, or old zips. The zip's internal layout and the track number are provisional until confirmed with the organizers.

## Pilot results

<!-- PILOT_RESULTS_PLACEHOLDER -->
Not run yet. After the first real run, paste `results/summary.md` here (stage 5 writes it).

## What has and has not been tested

Tested by `--selftest` (66 checks, no network): answer extraction and scoring; the OpenRouter client against a local fake server (retry on 429, truncation, hard errors, request shape, key header); the whole pipeline with demo generators and a mock model (generate, validate, variation, evaluate, score, scorecard); a deliberately truncated reply being flagged and re-run only once; tampered answers and images being caught; the secret scan and zip contents; placeholder handling; running from another folder; and that the original entry points still start.

**Not tested:** real calls to OpenRouter (the exact response fields for reasoning tokens and cost may differ by provider), the organizer's format checker, and anything on Windows.

## Open questions

- Confirm the final `tasks.jsonl` and `responses.csv` schemas (the runner writes the repo's fields plus the generator fields listed above; `responses.csv` columns are in `eval/score.py`, `CSV_COLUMNS`).
- Exact OpenRouter slugs for Claude Haiku 5.5 and GPT-6 Luna.
- Reasoning-effort setting and fixed max token value: the runner defaults to `high` and `16000` until `run_config.json` says otherwise.
- `anthropic` and `openai` in `requirements.txt` are not used by the runner. Keep them only if a generator needs them.
- `slides/` is kept as-is; its contents are not described yet.
- Is the organizer's format checker a CLI tool, and what is its command? Stage 6 is skipped with a warning until `format_checker` is filled in.
- Pricing values in `results/run_config.json` are placeholders until copied from OpenRouter.
- Should `results/logs/` be committed or gitignored? (It is currently neither: the runner writes a log per run.)
- How is "realistic/useful" judged? Stage 2 checks (g) and (h) and the scorecard's HUMAN item are the only evidence so far.
- Track number and zip layout for the submission (provisional).
