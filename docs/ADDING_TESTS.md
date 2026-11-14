# Adding tasks (for teammates)

A task is one picture, one question, the data behind the picture, and the answer computed from that data in code. You add a task by writing a **generator**: one Python file that describes one family of pictures (for example, "bus timetable chart"). The runner does the rest: it generates the data and images, runs the validity checks, asks the models, scores the answers, and writes the report.

You only need to read `generate/base.py` (the contract) and copy `generate/generators/_template.py`. If your picture is a **Mermaid diagram** (or any picture drawn from source code), and every task asks the same question, copy the Mermaid template instead: see "Mermaid diagrams and a fixed question" below.

## Setup

- Python 3.12. The project uses the standard library only, so there is nothing new to install for generators. (Mermaid generators also need the Mermaid command-line tool; see the Mermaid section.)
- From the repo root, check your setup:

```
python3 run_tests.py --selftest
```

This runs without API keys or network access and should end with `SELFTEST PASSED`.

## 1. Create a generator file

```
python3 -m generate.new_generator bus_timetable
```

This creates `generate/generators/bus_timetable.py` from the template. The name must be snake_case and unique across all generators.

## 2. Fill in the file

Open the new file and fill in the TODOs.

**Identity (class attributes)**

| Attribute | What to put |
|-----------|-------------|
| `visual_type` | Kind of picture, for example `line chart` |
| `question_type` | Kind of question, for example `read peak value` |
| `answer_type` | `number`, `text`, or `choice` |
| `tolerance` | Numbers only: the accepted +/- value, or `None` for an exact match |
| `real_world_examples` | One or two real examples the visual is modeled on, as strings (name and URL or description) |
| `enabled` | Keep `False` while unfinished. Set `True` when the four required methods work |

**Required methods**

1. `generate_data(rng, index)`: return the data as a plain dict. Use only `rng` for randomness. Do not use the global `random` module, numpy's global state, or the clock. The same `rng` must give the same data every time.
2. `render(data, out_path)`: draw the image from `data` and save it to `out_path`.
3. `compute_answer(data)`: compute the answer in code from `data`. Never type an answer by hand.
4. `make_question(data)`: return the question text. It must not repeat the numbers or labels that appear in the image.

**Optional but strongly recommended** (these are how the task is judged as verifiable)

- `verify_render(data, image_path)`: check that the picture really shows the data, for example by measuring bar heights from pixels. Return `CheckResult.ok(...)` or `CheckResult.fail(msg, expected=..., actual=...)`.
- `question_needs_image(data, question)`: prove that the question cannot be answered from the text alone.

The runner adds the tolerance sentence to the question automatically when `answer_type` is `number` and `tolerance` is set. Do not write it yourself.

## Mermaid diagrams and a fixed question

Use this when your picture is drawn from **source code** (Mermaid) and every task asks the **same question**; only the diagram and the answer change.

```
python3 -m generate.new_generator my_flowchart --mermaid
```

This copies `generate/generators/_template_mermaid.py`, a complete working example: a random flowchart where the question is "how many arrows on the shortest route from Start to End?". Read the docstring at the top of that file first.

**One-time setup on the machine that generates:** install the Mermaid command-line tool, which needs Node.js:

```
npm install -g @mermaid-js/mermaid-cli
```

If `mmdc` is not on your PATH, set the `MMDC` environment variable to its full path. Ask the lead before relying on this: it is the one thing in the project that is not Python-only.

**What is different from a normal generator** (all optional class attributes, see `generate/base.py`):

| Attribute | What it does |
|-----------|--------------|
| `question_template` | The question text. Use it instead of writing `make_question()`. `{field}` placeholders are filled from the data |
| `fixed_question = True` | Says every task asks the identical question on purpose, so stage 3 prints an info line instead of a near-identical-question warning |
| `exact_answer = True` | The number is an exact count, not an estimate, so no tolerance is needed |
| `source_key = "mermaid"`, `source_ext = "mmd"` | `data["mermaid"]` holds the diagram's source code |

**Where the Mermaid code is stored.** In three places, all made by the runner:
1. `tasks/data/<id>.json`, key `mermaid`: the source of truth. `render()` reads the Mermaid text from here.
2. `tasks/source/<id>.mmd`: a plain-text copy you can open or paste into the Mermaid live editor.
3. `tasks/images/<id>.png`: the picture the models see, drawn from (1).

**How to build it** (the template already does this):
- In `generate_data()`, build the **structure** first (nodes, edges, labels) and then build the Mermaid text from it. Make it as large and tangled as you like.
- In `compute_answer()`, compute the answer from the structure. Never read it off the picture, and never type it.
- In `verify_render()`, prove the Mermaid text contains exactly the edges in the data (the template does). This checks the text against the data, not the drawn pixels.
- **Make the answers vary.** With one fixed question, different diagrams and different answers are what make the set varied. Stage 3 warns if half the tasks share one answer, or if two tasks share identical data.

**Try it:**

```
python3 -m generate.generate_tasks --generator my_flowchart --count 3
```

Then open the three images in `tasks/images/` and the matching `.mmd` files in `tasks/source/`. The picture-drawing step has only been tested with a stand-in for `mmdc`, so this is the moment to confirm the real tool draws what you expect (large diagrams may need a bigger `width`, an argument to `render_mermaid` in `generate/mermaid.py`).

## 3. Check your generator

```
python3 run_tests.py --generate --generator bus_timetable --count 6 --stage 0-2
```

Stage 1 lists any generator problems with a reason (for example, missing examples or a required method still unimplemented). Stage 2 prints one line per check (a to h) for each task. Fix any `[FAIL]` line. The `expected`, `actual`, and `look at` lines say where to look.

## 4. Hand-check your tasks

Every task must be checked by a person before it is packaged. Open each image and confirm it matches the question and the answer.

```
python3 -m validate.hand_check bus_timetable_003 --by "Your Name" --notes "checked picture, question, answer"
python3 -m validate.hand_check all --by "Your Name" --notes "..."
```

This records the check in `tasks/hand_checks.json`.

## 5. Run the full pipeline

```
python3 run_tests.py                 # all stages
python3 run_tests.py --stage 4 --dry-run   # no API calls
```

Live model calls need `OPENROUTER_API_KEY` in your environment or in a `.env` file at the repo root. Never write the key into any other file, and never paste it into a commit message or chat. The lead sets the model slugs and prices in `results/run_config.json`; a live run stops with a clear message while they are still `PLACEHOLDER`.

## 6. Package (lead only, normally)

```
python3 run_tests.py --package --team TEAM_NAME --track N
```

This scans every file for key-shaped strings and blocks the zip if it finds one. It also requires every task to be hand-checked, unless `--allow-unchecked` is given.

## Rules to keep in mind

- **Deterministic.** The same seed gives the same data, image, and answer on every machine.
- **Answer from code.** `compute_answer` is the only source of the reference answer. The runner recomputes it from the saved data to check it.
- **Question must not leak the data.** The runner runs a generic leak check, and your `question_needs_image` hook adds a stronger one.
- **Cite real examples.** One or two, in `real_world_examples`. Stage 2 checks this.
- **No secrets in files.** Keys live only in environment variables or `.env`, and `.env` is git-ignored.
- **Keep it small.** One generator per visual family. Do not add dependencies without agreement with the lead.

## Open questions for the lead

- Should generated files (`tasks/images/`, `tasks/data/`, `tasks/tasks.jsonl`) be committed, or regenerated by each person?
- Is a generator author allowed to hand-check their own tasks?
- Is there a target number of tasks per generator?
- Mermaid generators need the Mermaid command-line tool (Node.js) to regenerate. Is that acceptable, or must the submission be reproducible with Python alone (in which case the committed images and `tasks/source/*.mmd` are the record)?
- Does a fixed question for every task in the Mermaid generator count as "meaningful variation" for the judges?
- The ownership chart generator asks "how many people hold 25% or more?". Because anyone above the line holds at least 27%, the answer can only be 1, 2 or 3 (and is 2 in about 70% of charts), so a model can guess it. Should the answer instead be the list of those people's names (much harder to guess, but scored by exact text match)?
