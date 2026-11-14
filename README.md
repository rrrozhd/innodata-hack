# innodata-hack

A pipeline that generates verifiable visual tasks for evaluating AI models. Each task has an image, a question, structured data, a computed reference answer, and validity checks. Two models are evaluated on every task through OpenRouter and their answers are scored.

Status: script skeleton only. No real tasks, tests, or evaluations yet.

## Layout

```
validate/     pipeline steps 3-5: reference answer, render check, needs-image check
eval/         model calls, answer extraction, scoring, reporting
tasks/        tasks.jsonl + images/ + data/ (placeholders)
results/      responses.csv, run_config.json, raw/ (placeholders)
slides/       not yet described
README.md     this file
```

Folder names for `tasks/` and `results/` are provisional.

### Pipeline steps

| Step | Purpose | Where |
|------|---------|-------|
| 1 | Generate and save structured data | not yet placed (open question) |
| 2 | Render the image from the data | not yet placed (open question) |
| 3 | Compute the reference answer in code | `validate/check_answer.py` |
| 4 | Check the rendering matches the data | `validate/check_render.py` |
| 5 | Verify the question requires the image | `validate/check_needs_image.py` |

`validate/run_all.py` runs steps 3-5 over `tasks/tasks.jsonl`.

Evaluation: `eval/call_models.py` sends each task to each model, `eval/extract_answer.py` pulls out the final answer, `eval/score.py` scores it, and `eval/report.py` writes the summary.

### Runner architecture

`run_tests.py` is the entry point. It runs stages 0-7 in order, or a selected range, and prints evidence for each rubric item. Each stage lives in `runner/` and exposes `run(ctx)`. Shared state is in `runner/context.py`. Colors and log copy are in `runner/output.py`. The output uses plain ANSI codes and has no dependencies beyond the standard library.

| Stage | Module | What it prints | Rubric item |
|-------|--------|----------------|-------------|
| 0 | `runner/preflight.py` | Python version, required files, env var names set or not (never values) | Runnable submission |
| 1 | `runner/load_tasks.py` | Task count and schema problems | Reliable generation |
| 2 | `runner/validate_tasks.py` | One line per check a-h for each task | Realistic/useful visuals (g, h), reliable generation (a, b), fair/verifiable tasks (c-f) |
| 3 | `runner/variation.py` | Variation summary and near-duplicate warnings | Meaningful variation |
| 4 | `runner/evaluate.py` | Progress per model call, tokens, running cost; invalid replies rerun | Model evaluation |
| 5 | `runner/score.py` | Per-model accuracy, per-task pass/fail, too-easy and unsolved tasks | Task difficulty |
| 6 | `runner/format_check.py` | Organizer format checker result, if configured | Runnable submission |
| 7 | `runner/scorecard.py` | Rubric scorecard, overall result, files written | All items |

Output markers: `[PASS]`, `[FAIL]`, `[WARN]`, `[INFO]`. Colors are off with `--no-color`, when `NO_COLOR` is set, or when output is not a terminal. Every run writes a plain-text copy of the log to `results/logs/`.

Exit code: 0 when every selected stage passes, 1 when any stage fails or is not implemented, 2 when the config cannot be read.

## Pilot results

Not run yet.

## How to run

Status: the stage logic is not implemented yet. Each stage currently prints a warning and the run exits 1.

Planned setup:

1. `python3.12 -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. `cp .env.example .env` and set `OPENROUTER_API_KEY`. Keys are read from the environment only.
4. Full run: `python run_tests.py`
5. One stage: `python run_tests.py --stage 2`. A range also works, for example `--stage 2-5`.
6. No API calls: `python run_tests.py --stage 4 --dry-run`
7. One task: `python run_tests.py --task <task_id>`
8. Plain output: `python run_tests.py --no-color`

The older `validate/run_all.py` and `eval/` scripts are not called by the runner yet. Whether to keep them is an open question.

## Open questions

- Steps 1 and 2 have no scripts yet. Where should they live?
- Confirm the `tasks.jsonl` and `responses.csv` schemas, shown as placeholders.
- Exact OpenRouter slugs for Claude Haiku 5.5 and GPT-6 Luna.
- Reasoning-effort setting and fixed max token value.
- Whether `anthropic` stays in `requirements.txt` if both models go through OpenRouter.
- `slides/` is kept as-is; its contents are not described yet.
- Runner: is the organizer's format checker a CLI tool, and what is its command? Enabled is false until confirmed.
- Runner: pricing values in `results/run_config.json` are placeholders until copied from OpenRouter.
- Runner: should `results/logs/` be committed or gitignored?
- Runner: how is "realistic/useful" judged? Stage 2 checks g and h are the only evidence so far.
