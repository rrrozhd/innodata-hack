# innodata-hack

A pipeline that generates verifiable visual tasks for evaluating AI models. Each task has an image, a question, structured data, a computed reference answer, and validity checks. Two models (Claude Haiku 5.5 and GPT-6 Luna) are evaluated on every task through OpenRouter and their answers are scored.

**Status:** the runner is built and self-tested. The one thing left to write is the **generators** (the code that makes the actual tasks). `python run_tests.py --selftest` passes 66 checks with no keys and no network. Live calls to OpenRouter have **not** been tested yet.

## Quick start

```
python3 -m venv .venv && source .venv/bin/activate     # Python 3.12+ recommended (3.10+ works)
cp .env.example .env                                   # then put OPENROUTER_API_KEY=... in .env (git-ignored)
python run_tests.py --selftest                         # proves the runner works; needs no keys
python -m generate.new_generator my_chart              # step 1: create a generator, then fill it in
python run_tests.py --generate --dry-run               # generate tasks, validate, preview the model calls (no spend)
python run_tests.py                                    # the full run (needs model slugs + key, see "Configuration")
```

The runner uses only the Python standard library. `requirements.txt` is unchanged: `pillow`/`pandas` are there for generators to use, and `anthropic`/`openai` are not used by the runner (OpenRouter is called over plain HTTPS).

## Layout

```
run_tests.py   entry point: runs stages 0-7 (or a range), selftest, packaging
runner/        one module per stage, plus output, config, schema, selftest, package
generate/      pipeline steps 1-2: the generator contract + generators/ (teammates add files here)
validate/      pipeline steps 3-5: the checks behind stage 2, and the hand-check ledger
eval/          model calls (OpenRouter), answer extraction, scoring, reporting
tasks/         tasks.jsonl + images/ + data/ (+ hand_checks.json once tasks are hand-checked)
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

`python -m generate.generate_tasks` runs steps 1-3 for every enabled generator and writes `tasks/images/<id>.png`, `tasks/data/<id>.json`, and `tasks/tasks.jsonl`. `python -m validate.run_all` runs the checks (steps 3-5) on their own.

### Runner stages

`python run_tests.py` runs stages 0-7 in order, or a selected range, and prints evidence for each rubric item. Each stage is a module in `runner/` that exposes `run(ctx)`; shared state is in `runner/context.py`; colors and the log copy are in `runner/output.py`.

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
python run_tests.py                        full run, stages 0-7
python run_tests.py --stage 2-5            a range (also: --stage 4, --stage 0,2-4)
python run_tests.py --dry-run              no API calls; shows what stage 4 would call
python run_tests.py --task <task_id>       limit the per-task stages to one task (several ids allowed)
python run_tests.py --models "GPT-6 Luna"  only these models (comma-separated names from run_config.json)
python run_tests.py --generate             run the generators first (right after preflight), then the stages
python run_tests.py --force                stage 4: ignore cached replies and call the models again
python run_tests.py --strict               treat warnings as failures
python run_tests.py --no-color             plain output
python run_tests.py --selftest             end-to-end test with demo generators in a temp folder
python run_tests.py --package              secret scan + submission zip (see "Packaging")
```

Other entry points (all run from any folder):

```
python -m generate.new_generator NAME                    create generate/generators/NAME.py from the template
python -m generate.generate_tasks [--count N]            run the generators -> tasks/
python -m validate.run_all                               validate every task
python -m validate.hand_check TASK_ID --by "Your Name"   record that a human hand-checked a task ("all" works too)
```

Evaluation is idempotent: valid replies are saved under `results/raw/<model>/<task_id>.json` and reused on the next run. Replies that are invalid (API error, cut off at the token limit, empty, filtered) are saved but never scored and never cached, so running stage 4 again re-runs exactly those. Wrong answers are data, not failures.

## Writing a generator

A generator makes one family of visual tasks. Create one with `python -m generate.new_generator my_chart`, fill in four methods, and set `enabled = True`:

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

Rules the runner enforces: everything is deterministic from the seed (no global `random`, no clock); `render()` and `compute_answer()` only see the data that was saved to disk; the question must not contain the answer or list the data values. The full contract is the docstring in `generate/base.py`. `python run_tests.py --selftest` contains two complete worked examples (stdlib-only PNG bar charts).

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

`python run_tests.py --package` scans every file that would be packaged for API keys (and refuses if it finds one), requires every task to be hand-checked (`--allow-unchecked` overrides, with a warning), and writes `dist/<Team>_Track<N>.zip` without `.env`, caches, `.git`, virtualenvs, or old zips. The zip's internal layout and the track number are provisional until confirmed with the organizers.

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
