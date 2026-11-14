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

## Pilot results

Not run yet.

## How to run

Not runnable yet. Every script currently raises `NotImplementedError`.

Planned setup:

1. `python3.12 -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. `cp .env.example .env` and set `OPENROUTER_API_KEY`. Keys are read from the environment only.
4. Validate: `python -m validate.run_all`
5. Evaluate: `python -m eval.call_models`, then `python -m eval.report`

## Open questions

- Steps 1 and 2 have no scripts yet. Where should they live?
- Confirm the `tasks.jsonl` and `responses.csv` schemas, shown as placeholders.
- Exact OpenRouter slugs for Claude Haiku 5.5 and GPT-6 Luna.
- Reasoning-effort setting and fixed max token value.
- Whether `anthropic` stays in `requirements.txt` if both models go through OpenRouter.
- Role of `slides/` and whether `run_all.py` should be the single entry point.
