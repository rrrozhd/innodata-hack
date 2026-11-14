#!/usr/bin/env python3
"""Track 2 (Visual Task Factory) submission format checker.

Checks a submission ZIP (or the folder you are about to zip) against Section 5 of the Track 2 participant guide,
"Submission and demo" and "Repository layout and file formats", and runs the same upload and inspect checks the
judges' app runs when the ZIP is uploaded.

    python3 check_submission.py MyTeam_Track2.zip
    python3 check_submission.py MyTeam_Track2.zip --tasks tasks/final --results results/final
    python3 check_submission.py ./my-repo            # before zipping (the ZIP-level checks are skipped)
    python3 check_submission.py MyTeam_Track2.zip --json report.json

Exit code: 0 if no check FAILs, 1 if any check FAILs (with --strict, warnings fail too), 2 on bad usage.

FAIL means the submission does not have the required format: the upload is refused, or a judge's tool cannot read
a required file, or a required file, folder, field, column or README section is missing or malformed.
WARN means something the guide asks for looks missing or incomplete, or something may stop the judges' tools from
reading your work in full. Read every WARN; fix it unless you know why it does not apply.

It only reads files. Nothing is extracted, built or run, and nothing is sent anywhere. Python 3.8+, standard
library only.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

VERSION = "1.0.0"

# ---- limits and names used by the judges' app -------------------------------------------------------------------
MAX_UPLOAD_MB = 200            # the app refuses a larger upload
MAX_UNCOMPRESSED_MB = 2000     # and a ZIP that unpacks to more
CSV_FIELD_LIMIT = 131072       # Python's default csv field limit: a longer cell makes the judge's CSV reader fail
EXPECTED_TASKS = 10

ZIP_NAME_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]*_Track2\.zip")
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".idea",
             ".vscode", "site-packages", "dist", "build", ".ipynb_checkpoints", "__MACOSX"}
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
BLOAT_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "site-packages", ".pytest_cache", ".mypy_cache"}
TEXT_EXT = {".md", ".txt", ".rst", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".json", ".jsonl", ".csv"}
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".r", ".jl", ".ipynb", ".sh"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
RESULT_NAMES = ("responses.csv", "responses.jsonl", "results.csv", "results.jsonl", "run_config.json")
DEP_FILES = ("requirements.txt", "pyproject.toml", "setup.py", "environment.yml", "package.json", "poetry.lock",
             "uv.lock", "pipfile", "renv.lock", "description", "go.mod", "cargo.toml")
SECRET_FILES = (".env", ".env.local", "credentials.json", "secrets.json", ".netrc")
SECRET_PATTERNS = {
    "OpenRouter key": re.compile(r"sk-or-(?:v1-)?[A-Za-z0-9]{20,}"),
    "Anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "OpenAI-style key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}"),
    "key assignment": re.compile(r"(?i)\b[A-Z0-9_]*(?:API_KEY|SECRET|TOKEN)\s*[=:]\s*['\"]?([A-Za-z0-9_\-]{24,})"),
}

TASK_FIELDS = ("task_id", "image", "question", "answer", "answer_type")
ANSWER_TYPES = ("number", "integer", "label", "multiple_choice", "sequence")
RESULT_COLUMNS = ("model", "task_id", "status", "parsed", "question_type", "response", "ref_answer", "correct",
                  "finish_reason", "error")
STATUS_VALUES = ("ok", "api_error")
ERROR_VALUES = ("", "api_error", "length")

# what the judge's tools accept as a task record (a JSON object with a question and an answer)
QUESTION_KEYS = ("question", "prompt", "query")
ANSWER_KEYS = ("answer", "reference_answer", "ground_truth", "gt", "gold", "gold_answer", "expected_answer", "target")

HEADING_RX = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
# the judge finds README sections by these heading patterns
REQUIRED_SECTIONS = {
    "Reported folders": re.compile(r"report(ed)?\s+(task|result|evaluation|run|folder)|folders?\s+(to\s+)?judge|"
                                   r"judged\s+folders?", re.I),
    "Pilot results": re.compile(r"\bpilot", re.I),
    "Run in Docker": re.compile(r"docker", re.I),
}
HAND_CHECK_RX = re.compile(r"hand[\s\-_]*check|manual(ly)?\s+(check|review)|hand[\s\-]*review", re.I)
# the rest of the README content Section 5 lists (any heading wording is fine)
README_TOPICS = {
    "the problem statement": r"problem|challenge|overview|introduction|summary",
    "how the pipeline works": r"pipeline|how it works|architecture|design|approach",
    "how the tasks were made hard / what made the models fail": r"hard|difficult|fail|error analysis|why",
    "key findings": r"finding|result|takeaway|insight",
    "reused components": r"reuse|third[\s\-]party|existing code|pre[\s\-]?existing|acknowledg|credits",
    "manual interventions": r"manual|intervention|hand[\s\-]*(fix|edit|correct)",
    "limitations": r"limitation|known issue|caveat",
    "each team member's contribution": r"contribut|team|member|who did",
}


# ---- reporting ----------------------------------------------------------------------------------------------------
class Report:
    def __init__(self):
        self.items = []  # (section, level, message)
        self.section = ""

    def start(self, name):
        self.section = name

    def add(self, level, msg):
        self.items.append((self.section, level, msg))

    def ok(self, msg):
        self.add("PASS", msg)

    def warn(self, msg):
        self.add("WARN", msg)

    def fail(self, msg):
        self.add("FAIL", msg)

    def info(self, msg):
        self.add("INFO", msg)

    def counts(self):
        return Counter(level for _, level, _ in self.items)


# ---- reading a ZIP or a folder the way the judge does -------------------------------------------------------------
def _junk(name):
    base = name.rstrip("/").split("/")[-1]
    return name.startswith("__MACOSX/") or base in JUNK_NAMES or base.startswith("._")


class Submission:
    """The judge's view of a submission: junk dropped, a single top-level folder stripped, tool dirs skipped."""

    def __init__(self, path: Path):
        self.path = path
        self.is_zip = path.is_file()
        self.all_names = []  # every entry, for hygiene and secret-file checks
        if self.is_zip:
            self.zf = zipfile.ZipFile(path)
            names = [n for n in self.zf.namelist() if not n.endswith("/") and not _junk(n)]
            self.all_names = names
            tops = {n.split("/")[0] for n in names}
            self.prefix = (tops.pop() + "/") if len(tops) == 1 and all("/" in n for n in names) else ""
            files = sorted(n[len(self.prefix):] for n in names)
        else:
            self.prefix = ""
            files = []
            for root, dirs, fs in os.walk(path):
                rel_root = Path(root).relative_to(path)
                for d in dirs:
                    if d in SKIP_DIRS:
                        self.all_names.append(str(rel_root / d) + "/")
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for f in fs:
                    rel = str(rel_root / f) if str(rel_root) != "." else f
                    self.all_names.append(rel)
                    if not _junk(f):
                        files.append(rel)
            files.sort()
        self.files = [f for f in files if not (set(Path(f).parts) & SKIP_DIRS)]
        self.fileset = set(self.files)
        self.dirs = {str(p) for f in self.files for p in list(PurePosixPath(f).parents)[:-1]}

    def read_bytes(self, rel):
        return self.zf.read(self.prefix + rel) if self.is_zip else (self.path / rel).read_bytes()

    def read_text(self, rel):
        return self.read_bytes(rel).decode("utf-8", errors="replace")

    def size(self, rel):
        return self.zf.getinfo(self.prefix + rel).file_size if self.is_zip else (self.path / rel).stat().st_size


def ext(p):
    return Path(p).suffix.lower()


def in_folder(f, folder):
    return f.startswith(folder + "/") if folder else True


def is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def norm_label(z):
    z = str(z).lower().replace("&", " and ")
    z = re.sub(r"[^a-z0-9$.\- ]+", " ", z)
    z = re.sub(r"^\s*the\s+", "", z)
    return re.sub(r"\s+", " ", z).strip(" .")


def parse_number(s):
    if s is None:
        return None
    s = str(s).replace("−", "-").replace(" ", "").replace(",", "")
    s = re.split(r"[=≈~]", s)[-1]
    m = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", s)
    return float(m.group(0)) if m else None


def same_reference(row_value, task_ref):
    """The judge's comparison of a results row's ref_answer with the task's answer."""
    if isinstance(task_ref, list):
        v = row_value
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                pass
        if isinstance(v, str):
            v = [x for x in re.split(r"\s*(?:;|\n|->|→|\|)\s*", v.strip()) if x.strip()]
            if len(v) == 1 and len(task_ref) > 1:
                v = [x for x in v[0].split(",") if x.strip()]
        return isinstance(v, list) and [norm_label(x) for x in v] == [norm_label(x) for x in task_ref]
    if is_number(task_ref):
        x = parse_number(row_value)
        return x is not None and abs(x - float(task_ref)) <= 1e-6 * max(1.0, abs(float(task_ref)))
    return norm_label(row_value) == norm_label(task_ref)


def short(x, n=60):
    s = str(x)
    return s if len(s) <= n else s[: n - 1] + "…"


def listing(items, n=8):
    items = list(items)
    return ", ".join(str(i) for i in items[:n]) + (f" … (+{len(items) - n} more)" if len(items) > n else "")


def headings(text):
    lines = text.splitlines()
    out, in_code = [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        m = None if in_code else HEADING_RX.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return lines, out


def section_body(lines, heads, k):
    i, level, _ = heads[k]
    end = next((j for j, lv, _ in heads[k + 1:] if lv <= level), len(lines))
    return "\n".join(lines[i + 1:end])


# ---- 1. upload: what the app does before it accepts the file ------------------------------------------------------
def check_upload(rep, path: Path, max_upload_mb, max_uncompressed_mb):
    rep.start("1. Upload (the judges' app refuses the file if these fail)")
    if not zipfile.is_zipfile(path):
        rep.fail("The file is not a ZIP archive.")
        return False
    size = path.stat().st_size
    if size > max_upload_mb * 1024 * 1024:
        rep.fail(f"The ZIP is {size / 1048576:.1f} MB; the upload limit is {max_upload_mb} MB.")
    else:
        rep.ok(f"ZIP size {size / 1048576:.1f} MB (limit {max_upload_mb} MB).")
    if ZIP_NAME_RX.fullmatch(path.name):
        rep.ok(f"File name {path.name} has the form TeamName_Track2.zip.")
    else:
        rep.fail(f"File name {path.name!r} must be TeamName_Track2.zip (letters, digits, - and _ in the team name).")
    good = True
    with zipfile.ZipFile(path) as zf:
        total, unsafe, links = 0, [], []
        for info in zf.infolist():
            p = PurePosixPath(info.filename.replace("\\", "/"))
            if p.is_absolute() or ".." in p.parts or (len(info.filename) > 1 and info.filename[1] == ":"):
                unsafe.append(info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                links.append(info.filename)
            total += info.file_size
        if unsafe:
            rep.fail(f"Unsafe paths (absolute or with ..): {listing(unsafe, 5)}. The upload is refused.")
            good = False
        else:
            rep.ok("No absolute or parent-relative paths.")
        if links:
            rep.warn(f"Symbolic links in the ZIP: {listing(links, 5)}. They are not followed; zip the real files.")
        if total > max_uncompressed_mb * 1024 * 1024:
            rep.fail(f"The ZIP unpacks to {total / 1048576:.0f} MB; the limit is {max_uncompressed_mb} MB.")
            good = False
        else:
            rep.ok(f"Unpacks to {total / 1048576:.1f} MB (limit {max_uncompressed_mb} MB).")
        try:
            bad = zf.testzip()
        except Exception as e:  # noqa: BLE001 - any read error means a corrupt archive
            bad = f"({e})"
        if bad:
            rep.fail(f"The ZIP is corrupt (first bad file: {bad}). The upload is refused.")
            good = False
        else:
            rep.ok("Every file in the ZIP reads back intact (CRC check).")
    return good


# ---- 2. layout: README, Dockerfile, validate/, eval/, dependencies, hygiene ---------------------------------------
def check_layout(rep, sub: Submission):
    rep.start("2. Repository layout")
    if sub.is_zip and sub.prefix:
        rep.info(f"All files sit in one top-level folder ({sub.prefix.rstrip('/')}/); the judge treats it as the "
                 "repository root.")
    readme = next((f for f in sub.files if "/" not in f and f.lower() == "readme.md"), None)
    if readme:
        rep.ok(f"{readme} at the repository root.")
    else:
        other = [f for f in sub.files if Path(f).name.lower().startswith("readme")]
        rep.fail("No README.md at the repository root" + (f" (found: {listing(other, 4)})" if other else "") + ".")
    dockerfile = next((f for f in sub.files if f.lower() == "dockerfile"), None)
    if dockerfile:
        rep.ok("Dockerfile at the repository root.")
    else:
        rep.fail("No Dockerfile at the repository root. Judges build the image from it.")
    for folder, what in (("validate", "validation code"), ("eval", "evaluation code")):
        code = [f for f in sub.files if f.startswith(folder + "/") and ext(f) in CODE_EXT and sub.size(f) > 0]
        if code:
            rep.ok(f"{folder}/ at the root with {len(code)} code file(s).")
        elif any(f.startswith(folder + "/") for f in sub.files):
            rep.fail(f"{folder}/ exists but holds no code files ({what} must be in it).")
        else:
            nested = sorted({d for d in sub.dirs if Path(d).name == folder})
            rep.fail(f"No {folder}/ folder at the repository root for the {what}"
                     + (f" (found {listing(nested, 3)}; it must be at the root)" if nested else "") + ".")
    deps = [f for f in sub.files if Path(f).name.lower() in DEP_FILES]
    if deps:
        rep.ok(f"Dependencies: {listing(deps, 4)}.")
    else:
        rep.warn("No dependency file found (requirements.txt, pyproject.toml, package.json, …).")
    bloat = defaultdict(int)
    for n in sub.all_names:
        parts = set(Path(n).parts)
        for d in parts & BLOAT_DIRS:
            bloat[d] += 1
    if bloat:
        rep.warn("The ZIP includes tool folders the judge ignores: "
                 + ", ".join(f"{d}/ ({c} entries)" for d, c in sorted(bloat.items()))
                 + ". Leave them out to keep the upload small.")
    junk = [n for n in (sub.zf.namelist() if sub.is_zip else []) if _junk(n)]
    if junk:
        rep.info(f"{len(junk)} OS clutter entries (__MACOSX/, .DS_Store, ._*) are ignored by the judge.")
    slides = [f for f in sub.files if ext(f) == ".pptx"]
    for s in slides:
        try:
            with zipfile.ZipFile(io.BytesIO(sub.read_bytes(s))) as pz:
                n = sum(1 for x in pz.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", x))
            (rep.warn if n > 5 else rep.ok)(f"Slides {s}: {n} slides" + (" (the maximum is 5)." if n > 5 else "."))
        except zipfile.BadZipFile:
            rep.warn(f"Slides {s} cannot be opened as a .pptx file.")
    return readme, dockerfile


# ---- 3. secrets -----------------------------------------------------------------------------------------------------
def check_secrets(rep, sub: Submission, dockerfile):
    rep.start("3. Secrets (no API keys or .env files anywhere)")
    hits = []
    for n in sub.all_names:
        if Path(n).name.lower() in SECRET_FILES:
            try:
                size = sub.zf.getinfo(n).file_size if sub.is_zip else (sub.path / n).stat().st_size
            except (KeyError, OSError):
                size = 1
            if size > 0:
                shown = n[len(sub.prefix):] if sub.prefix and n.startswith(sub.prefix) else n
                hits.append(f"{shown}: secrets file included")
    for f in sub.files:
        name = Path(f).name.lower()
        if (ext(f) in TEXT_EXT | CODE_EXT or name.startswith(".env") or name == "dockerfile") and sub.size(f) < 5_000_000:
            txt = sub.read_text(f)
            for kind, rx in SECRET_PATTERNS.items():
                m = next((m for m in rx.finditer(txt) if not (kind == "key assignment" and re.search(
                    r"(?i)your|example|xxxx|placeholder|\.\.\.", m.group(0)))), None)
                if m:
                    line = txt.count("\n", 0, m.start()) + 1
                    hits.append(f"{f}:{line}: {kind} ({m.group(0)[:10]}…)")
                    break
    if hits:
        for h in hits[:20]:
            rep.fail(f"Possible secret: {h}")
        rep.info("Remove the key, rotate it if it was real, and read keys from environment variables only.")
    else:
        rep.ok("No API keys or secrets files found.")
    if dockerfile:
        df = sub.read_text(dockerfile)
        if re.search(r"(?im)^\s*(COPY|ADD)\b[^\n]*\.env\b", df):
            rep.fail("The Dockerfile copies a .env file into the image.")
        if re.search(r"(?im)^\s*(ENV|ARG)\s+OPENROUTER_API_KEY\s*[= ]\s*\S", df):
            rep.fail("The Dockerfile sets OPENROUTER_API_KEY. Pass it only at run time (docker run -e).")


# ---- 4. README sections -------------------------------------------------------------------------------------------
def check_readme(rep, sub: Submission, readme):
    rep.start("4. README.md sections")
    if not readme:
        rep.fail("Skipped: no README.md at the root.")
        return "", [], []
    text = sub.read_text(readme)
    lines, heads = headings(text)
    found = {}
    for name, rx in REQUIRED_SECTIONS.items():
        cands = [k for k, (_, _, h) in enumerate(heads) if rx.search(h)]
        if name == "Run in Docker":  # the judge takes a Docker heading whose section holds a `docker run`
            with_run = [k for k in cands if re.search(r"docker\s+(container\s+)?run\b", section_body(lines, heads, k))]
            cands = with_run or cands
        if not cands:
            rep.fail(f"No \"{name}\" section: the README needs a heading named \"{name}\".")
            continue
        k = cands[0]
        body = section_body(lines, heads, k).strip()
        found[name] = (heads[k][2], body)
        exact = heads[k][2].lower().strip(" :`*") == name.lower()
        if not body:
            rep.fail(f"\"{heads[k][2]}\" (line {heads[k][0] + 1}) is empty.")
        elif exact:
            rep.ok(f"\"{name}\" section (line {heads[k][0] + 1}).")
        else:
            rep.warn(f"\"{name}\" found under the heading \"{heads[k][2]}\" (line {heads[k][0] + 1}); the guide asks "
                     f"for the heading \"{name}\".")
    if "Pilot results" in found:
        body = found["Pilot results"][1]
        if not re.search(r"\d", body):
            rep.warn("\"Pilot results\" contains no numbers; it should give the pilot scores.")
        if not re.search(r"(?i)chang|generator|harder|adjust|v\d", body):
            rep.warn("\"Pilot results\" does not seem to describe the generator changes made because of the pilots.")
    hand = next(((k, h) for k, (_, _, h) in enumerate(heads) if HAND_CHECK_RX.search(h)), None)
    hand_body = section_body(lines, heads, hand[0]) if hand else ""
    if hand:
        rep.ok(f"Hand-check notes section \"{hand[1]}\".")
    else:
        files = [f for f in sub.files if re.search(r"hand[\s\-_]*check|manual[\s\-_]*(check|review)", Path(f).name,
                                                   re.I)]
        rep.warn("No hand-check section in the README (a heading such as \"Hand check\"). Section 3 asks for short "
                 "notes per task in the README; the judge looks for them under such a heading"
                 + (f". Found {listing(files, 3)}: link it from that section or copy the notes in." if files else "."))
    head_text = " | ".join(h for _, _, h in heads)
    missing = [t for t, rx in README_TOPICS.items() if not re.search(rx, head_text, re.I)]
    if missing:
        rep.warn("No heading found for: " + "; ".join(missing) + ". Section 5 asks the README to cover them.")
    else:
        rep.ok("Headings cover the other README content Section 5 lists.")
    return text, found, hand_body


def readme_folders(sub: Submission, body: str):
    """Folders named in the Reported folders section that exist in the submission."""
    toks = set(re.findall(r"`([^`\n]+)`", body)) | set(re.findall(r"[\w.\-]+(?:/[\w.\-]+)+/?", body))
    out = []
    for t in toks:
        t = t.strip()
        if t.startswith("./"):
            t = t[2:]
        t = t.strip("/")
        if t in sub.dirs and t not in out:
            out.append(t)
    return out


# ---- 5. what the app's inspect step shows: task and results folders ----------------------------------------------
def find_task_sets(sub: Submission):
    """The judge's task-folder discovery: *.jsonl files whose lines are objects with a question and an answer."""
    sets = {}
    for f in sub.files:
        if ext(f) == ".jsonl" and sub.size(f) < 50_000_000:
            recs = []
            for line in sub.read_text(f).splitlines():
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    recs = []
                    break
                if isinstance(o, dict) and any(k in o for k in QUESTION_KEYS) and any(k in o for k in ANSWER_KEYS):
                    recs.append(o)
            if recs:
                d = str(Path(f).parent)
                sets[f] = ("" if d == "." else d, recs)
    return sets


def check_inspect(rep, sub: Submission, readme_text, sections, tasks_arg, results_arg):
    rep.start("5. Reported folders (what the judges' app offers after upload)")
    sets = find_task_sets(sub)
    task_dirs = sorted({d for d, _ in sets.values()})
    result_dirs = sorted({str(Path(f).parent) for f in sub.files if Path(f).name.lower() in RESULT_NAMES
                          and str(Path(f).parent) != "."})
    if not task_dirs:
        rep.fail("No task file found: the app finds none to offer (a .jsonl whose lines are JSON objects with "
                 "\"question\" and \"answer\").")
    else:
        rep.info("Task folders the app offers: " + listing(
            [f"{d or '(root)'} ({sum(len(r) for dd, r in sets.values() if dd == d)} tasks)" for d in task_dirs], 10))
    if not result_dirs:
        rep.fail("No results folder found: the app looks for a folder holding responses.csv, responses.jsonl or "
                 "run_config.json.")
    else:
        rep.info("Results folders the app offers: " + listing(result_dirs, 10))

    named = []
    if "Reported folders" in sections:
        body = sections["Reported folders"][1]
        own = re.split(r"(?m)^#{1,6}\s", body, maxsplit=1)[0]  # the section's own text, before any subsection
        named = readme_folders(sub, own) or readme_folders(sub, body)
    named_tasks = [d for d in named if d in task_dirs]
    named_results = [d for d in named if d in result_dirs]

    def pick(arg, named_list, kind, offered):
        if arg:
            a = arg.strip("/")
            if a not in sub.dirs:
                rep.fail(f"--{kind} {arg!r} is not a folder in the submission.")
                return None
            if named and a not in named:
                rep.warn(f"--{kind} {a} is not named in the README's \"Reported folders\" section.")
            return a
        if "Reported folders" not in sections:
            rep.fail(f"Cannot tell which {kind} folder is reported: no \"Reported folders\" section. Pass --{kind} "
                     "to check one anyway.")
            return offered[0] if len(offered) == 1 else None
        if len(named_list) == 1:
            rep.ok(f"Reported {kind} folder named in the README: {named_list[0]}/ (the app preselects it).")
            return named_list[0]
        if len(named_list) > 1:
            rep.fail(f"\"Reported folders\" names {len(named_list)} {kind} folders ({listing(named_list, 5)}); name "
                     f"exactly one. Pass --{kind} to check one of them.")
            return None
        rep.fail(f"\"Reported folders\" names no existing {kind} folder"
                 + (f" (it should be one of: {listing(offered, 6)})" if offered else "")
                 + ". Write its path from the repository root, e.g. `"
                 + ("tasks/final" if kind == "tasks" else "results/final") + "`.")
        return None

    tasks_dir = pick(tasks_arg, named_tasks, "tasks", task_dirs)
    results_dir = pick(results_arg, named_results, "results", result_dirs)
    # the app preselects the first folder, alphabetically, whose path appears anywhere in the README (plain substring)
    for kind, chosen, offered in (("tasks", tasks_dir, task_dirs), ("results", results_dir, result_dirs)):
        pre = sorted(offered, key=lambda d: (not (bool(d) and d in readme_text), d))
        if chosen and pre and pre[0] != chosen:
            rep.warn(f"After upload the app preselects {pre[0] or '(root)'}/ as the {kind} folder, not {chosen}/ "
                     f"(it takes the first folder, alphabetically, whose path appears anywhere in the README"
                     + (f"; \"{pre[0]}\" is part of \"{chosen}\"" if pre[0] and pre[0] in chosen else "")
                     + "). Judges can change it, but a distinct folder name avoids the mix-up.")
    if tasks_dir is not None and tasks_dir not in task_dirs:
        rep.fail(f"The tasks folder {tasks_dir}/ holds no task file the app can read (see section 6).")
    if results_dir is not None and results_dir not in result_dirs:
        rep.fail(f"The results folder {results_dir}/ holds no responses.csv, responses.jsonl or run_config.json.")
    return tasks_dir, results_dir


# ---- 6. tasks.jsonl -----------------------------------------------------------------------------------------------
def tolerance_strings(v, kind):
    out = set()
    if kind == "rel":
        p = v * 100
        for s in (f"{p:g}", f"{p:.1f}", f"{p:.2f}"):
            out |= {s + "%", s + " %", s + " percent", s + " per cent"}
    out |= {f"{v:g}", repr(v), str(v)}
    return out


def check_tasks(rep, sub: Submission, folder, expected, prompt_text):
    rep.start(f"6. Task file: {folder or '(root)'}/tasks.jsonl")
    tf = (folder + "/" if folder else "") + "tasks.jsonl"
    if tf not in sub.fileset:
        others = [f for f in sub.files if Path(f).parent == Path(folder or ".") and ext(f) == ".jsonl"]
        rep.fail(f"No tasks.jsonl in {folder or '(root)'}/" + (f" (found {listing(others, 3)})" if others else "")
                 + ". The task file must be named tasks.jsonl.")
        return None
    text = sub.read_text(tf)
    recs, bad = [], []
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError as e:
            bad.append(f"line {i}: {e.msg}")
            continue
        if not isinstance(o, dict):
            bad.append(f"line {i}: not a JSON object")
            continue
        recs.append((i, o))
    if bad:
        rep.fail(f"tasks.jsonl has lines that are not JSON objects: {listing(bad, 5)}. The judge's reader then "
                 "ignores the whole file.")
    if not recs:
        rep.fail("tasks.jsonl holds no task records.")
        return None
    n = len(recs)
    (rep.ok if n == expected else rep.warn)(
        f"{n} task(s)" + ("." if n == expected else f"; the reported set should have {expected} accepted tasks."))

    problems = defaultdict(list)  # message -> [task ids]
    ids = []
    for line_no, r in recs:
        tid = r.get("task_id")
        label = str(tid) if tid not in (None, "") else f"line {line_no}"
        for k in TASK_FIELDS:
            if r.get(k) in (None, "", []):
                problems[f"missing or empty required field \"{k}\""].append(label)
        if tid not in (None, ""):
            if not isinstance(tid, str):
                problems["task_id is not a string"].append(label)
            ids.append(str(tid))
        img = r.get("image")
        if isinstance(img, str) and img:
            p = img.replace("\\", "/")
            if p.startswith("/") or re.match(r"^[A-Za-z]:", p):
                problems["image is an absolute path (it must be relative to the task folder)"].append(label)
            else:
                rel = str(PurePosixPath(folder or ".", p)) if folder else str(PurePosixPath(p))
                rel = os.path.normpath(rel).replace("\\", "/")
                if rel not in sub.fileset:
                    root_rel = os.path.normpath(p).replace("\\", "/")
                    problems["image file not found relative to the task folder"
                             + (" (it is relative to the repository root instead)" if root_rel in sub.fileset
                                else "")].append(f"{label} → {img}")
                elif sub.size(rel) == 0:
                    problems["image file is empty"].append(label)
                elif ext(rel) not in IMG_EXT:
                    problems[f"image is not an image file ({', '.join(sorted(IMG_EXT))})"].append(label)
        elif img not in (None, ""):
            problems["image is not a path string"].append(label)
        q = r.get("question")
        if q not in (None, "") and not isinstance(q, str):
            problems["question is not a string"].append(label)

        at, ans = r.get("answer_type"), r.get("answer")
        has_rel, has_abs = r.get("tolerance_rel") is not None, r.get("tolerance_abs") is not None
        if at not in (None, "") and at not in ANSWER_TYPES:
            problems[f"answer_type is not one of {', '.join(ANSWER_TYPES)}"].append(f"{label} ({short(at, 20)})")
        elif at == "number":
            if not is_number(ans):
                problems["answer_type number but answer is not a JSON number"].append(f"{label} ({short(ans, 20)})")
            if has_rel and has_abs:
                problems["both tolerance_rel and tolerance_abs set (use one)"].append(label)
            elif not (has_rel or has_abs):
                if not (is_number(ans) and float(ans).is_integer()):
                    problems["non-integer number answer without tolerance_rel or tolerance_abs"].append(label)
            else:
                key = "tolerance_rel" if has_rel else "tolerance_abs"
                tv = r.get(key)
                if not is_number(tv) or tv < 0:
                    problems[f"{key} is not a non-negative number"].append(f"{label} ({short(tv, 20)})")
                elif tv == 0:
                    problems[f"{key} is 0 (an exact match on a continuous reading is rarely fair)"].append(label)
                elif has_rel and tv >= 1:
                    problems["tolerance_rel ≥ 1: it is a fraction (0.05 means ±5%), not a percentage"].append(
                        f"{label} ({tv})")
                elif isinstance(q, str):
                    pool = q + "\n" + prompt_text
                    if not any(s in pool for s in tolerance_strings(float(tv), "rel" if has_rel else "abs")):
                        problems["tolerance not found in the question or the prompt template (state the same "
                                 "tolerance in the prompt)"].append(f"{label} ({key}={tv})")
        elif at == "integer":
            if not (is_number(ans) and float(ans).is_integer()):
                problems["answer_type integer but answer is not a whole JSON number"].append(
                    f"{label} ({short(ans, 20)})")
        elif at == "label":
            if not isinstance(ans, str):
                problems["answer_type label but answer is not a string"].append(f"{label} ({short(ans, 20)})")
        elif at == "multiple_choice":
            opts = r.get("options")
            if not isinstance(opts, list) or len(opts) < 2:
                problems["multiple_choice without an \"options\" list (in letter order)"].append(label)
            elif not (isinstance(ans, str) and re.fullmatch(r"[A-Z]", ans.strip())
                      and ord(ans.strip()) - 65 < len(opts)):
                problems["multiple_choice answer is not an option letter (A, B, …) within the options"].append(
                    f"{label} ({short(ans, 20)})")
        elif at == "sequence":
            if not isinstance(ans, list) or not ans:
                problems["answer_type sequence but answer is not a non-empty JSON list"].append(
                    f"{label} ({short(ans, 20)})")
        if at not in ("number",) and (has_rel or has_abs) and at in ANSWER_TYPES and at != "integer":
            problems[f"tolerance set on a {at} answer (tolerances apply to numbers)"].append(label)

    dup = [t for t, c in Counter(ids).items() if c > 1]
    if dup:
        rep.fail(f"Duplicate task_id: {listing(dup)}. Each task_id must be unique.")
    for msg, who in problems.items():
        level = rep.warn if msg.startswith(("tolerance not found", "tolerance_abs is 0", "tolerance_rel is 0")) \
            else rep.fail
        level(f"{msg}: {listing(who, 6)}")
    if not problems and not dup and not bad:
        rep.ok(f"Every task has {', '.join(TASK_FIELDS)} in the required form; image paths resolve; tolerances set "
               "where needed.")

    keys = Counter(k for _, r in recs for k in r)
    types = Counter(r.get("answer_type") for _, r in recs)
    rep.info("Answer types: " + ", ".join(f"{k} {v}" for k, v in types.most_common()) + ".")
    if not any("seed" in k.lower() for k in keys):
        rep.warn("No seed field in the task records. Section 4 asks for the seed with every task, and the judges "
                 "rebuild the tasks from it.")
    if not any("version" in k.lower() for k in keys):
        rep.warn("No generator-version field in the task records (Section 4).")
    if not any(k in keys for k in ("question_type", "qtype", "template", "family")):
        rep.warn("No question_type field in the task records; the results need one per task (Section 5).")
    check_fields = [k for k in keys if re.search(r"verif|check|valid", k, re.I)]
    check_files = [f for f in sub.files if in_folder(f, folder) and re.search(r"verif|check|valid",
                                                                                f[len(folder) + 1:], re.I)]
    if check_fields or check_files:
        rep.ok("Automatic check results: " + listing(check_fields + check_files, 4) + ".")
    else:
        rep.warn("No automatic check results found in the task records or the task folder (Section 5: \"the full "
                 "generated set with its automatic check results\").")
    data_json = [f for f in sub.files if in_folder(f, folder) and ext(f) == ".json"]
    if not data_json and not any(isinstance(r.get("data"), dict) for _, r in recs):
        rep.warn("No per-task data JSON in the task folder (Section 4: save the data as JSON next to the image).")
    return {"file": tf, "records": {str(r.get("task_id")): r for _, r in recs}, "ids": ids}


# ---- 7. responses.csv / responses.jsonl ---------------------------------------------------------------------------
def load_rows(sub: Submission, f):
    """(rows, problem) for a results file. CSV cells longer than the judge's reader limit are a problem."""
    if ext(f) == ".csv":
        text = sub.read_text(f)
        old = csv.field_size_limit()
        csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
        try:
            rows = list(csv.DictReader(io.StringIO(text)))
        except csv.Error as e:
            return None, f"cannot be read as CSV: {e}"
        finally:
            csv.field_size_limit(old)
        longest = max((len(v) for r in rows for v in r.values() if isinstance(v, str)), default=0)
        if longest > CSV_FIELD_LIMIT:
            return rows, (f"a cell is {longest} characters long; the judge's CSV reader stops at {CSV_FIELD_LIMIT} and "
                          "then reads no rows at all. Ship responses.jsonl instead, or move long replies to the raw "
                          "logs and keep the CSV cell shorter")
        return rows, None
    rows = []
    for i, line in enumerate(sub.read_text(f).splitlines(), 1):
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError as e:
            return None, f"line {i} is not valid JSON ({e.msg})"
        if not isinstance(o, dict):
            return None, f"line {i} is not a JSON object"
        rows.append(o)
    return rows, None


def cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def check_results(rep, sub: Submission, folder, tasks):
    rep.start(f"7. Results: {folder}/")
    files = [f for f in sub.files if str(Path(f).parent) == folder]
    main = [f for f in files if Path(f).name.lower() in ("responses.csv", "responses.jsonl")]
    if not main:
        rep.fail(f"No responses.csv or responses.jsonl directly in {folder}/.")
        return
    rows_by_file = {}
    for f in main:
        rows, problem = load_rows(sub, f)
        if problem:
            rep.fail(f"{Path(f).name}: {problem}.")
        if rows is None:
            continue
        if not rows:
            rep.fail(f"{Path(f).name} has no rows.")
            continue
        rows_by_file[f] = rows
    if len(main) == 2:
        rep.warn("Both responses.csv and responses.jsonl are present. The judge reads every results file in the "
                 "folder, so they must hold the same rows (checked below).")
    for f, rows in rows_by_file.items():
        name = Path(f).name
        cols = {str(c).strip() for r in rows for c in r.keys() if c is not None} if ext(f) == ".jsonl" \
            else {str(c).strip() for c in rows[0].keys() if c is not None}
        lower = {c.lower(): c for c in cols}
        missing = [c for c in RESULT_COLUMNS if c not in cols]
        if missing:
            case = [c for c in missing if c in lower]
            rep.fail(f"{name}: missing column(s) {', '.join(missing)}"
                     + (f" ({', '.join(lower[c] for c in case)} differ only in case)" if case else "") + ".")
        else:
            rep.ok(f"{name}: all {len(RESULT_COLUMNS)} required columns present; {len(rows)} rows.")
        g = lambda r, c: cell(r.get(c))  # noqa: E731
        issues = defaultdict(list)
        pairs = Counter()
        models = Counter()
        task_ids = set(tasks["ids"]) if tasks else set()
        for i, r in enumerate(rows, 2 if ext(f) == ".csv" else 1):
            where = f"row {i}" if ext(f) == ".csv" else f"line {i}"
            model, tid = g(r, "model"), g(r, "task_id")
            st, err, cor, fin = g(r, "status").lower(), g(r, "error").lower(), g(r, "correct"), g(r, "finish_reason")
            if not model:
                issues["empty model"].append(where)
            if not tid:
                issues["empty task_id"].append(where)
            models[model] += 1
            pairs[(model, tid)] += 1
            if tasks and tid and tid not in task_ids:
                issues["task_id not in the reported tasks.jsonl"].append(f"{where} ({tid})")
            if "status" in cols and st not in STATUS_VALUES:
                issues["status is not ok or api_error"].append(f"{where} ({short(st, 15) or 'empty'})")
            if "error" in cols and err not in ERROR_VALUES:
                issues["error is not empty, api_error or length"].append(f"{where} ({short(err, 15)})")
            if "correct" in cols:
                if err:
                    if cor:
                        issues["correct should be empty when error is set"].append(where)
                elif cor not in ("0", "1"):
                    issues["correct is not 1 or 0 (it may be empty only when error is set)"].append(
                        f"{where} ({short(cor, 10) or 'empty'})")
            if st == "api_error" and "error" in cols and err != "api_error":
                issues["status api_error but error is not api_error"].append(where)
            if st == "ok" and err == "api_error":
                issues["status ok but error is api_error"].append(where)
            if fin.lower() == "length" and "error" in cols and err != "length":
                issues["finish_reason length (cut off) but error is not length"].append(where)
            if err == "length" and fin and fin.lower() != "length":
                issues["error length but finish_reason is not length"].append(where)
            if st == "ok" and "response" in cols and not g(r, "response"):
                issues["status ok but response is empty"].append(where)
            if "question_type" in cols and not g(r, "question_type"):
                issues["empty question_type"].append(where)
            if tasks and tid in tasks["records"] and "ref_answer" in cols:
                ref = tasks["records"][tid].get("answer")
                rv = r.get("ref_answer")
                if ref is not None and not same_reference(rv, ref):
                    issues["ref_answer differs from the task's answer in tasks.jsonl (a red flag for the judges)"]\
                        .append(f"{where} ({tid}: {short(cell(rv), 20)} vs {short(json.dumps(ref), 20)})")
        dups = [f"{m} / {t} ×{c}" for (m, t), c in pairs.items() if c > 1]
        if dups:
            issues["more than one row for the same model and task (one row per model and task)"] += dups
        for msg, who in issues.items():
            rep.fail(f"{name}: {msg}: {listing(who, 5)}")
        if not issues:
            rep.ok(f"{name}: row values are in the required form; one row per model and task.")
        real_models = [m for m in models if m]
        rep.info(f"{name}: models " + ", ".join(f"{m} ({c} rows)" for m, c in models.items() if m) + ".")
        if len(real_models) < 2:
            rep.warn(f"{name}: {len(real_models)} model(s); the evaluation covers two models.")
        no_slash = [m for m in real_models if "/" not in m]
        if no_slash:
            rep.warn(f"{name}: model {listing(no_slash, 3)} does not look like an exact OpenRouter model ID "
                     "(provider/model).")
        if tasks:
            for m in real_models:
                miss = sorted(task_ids - {t for (mm, t) in pairs if mm == m})
                if miss:
                    rep.fail(f"{name}: {m} has no row for {len(miss)} reported task(s): {listing(miss, 5)}. Every "
                             "model must answer every accepted task.")
        bad_api = [f"{g(r, 'model')} / {g(r, 'task_id')}" for r in rows
                   if g(r, "error").lower() == "api_error" or g(r, "status").lower() == "api_error"]
        bad_cut = [f"{g(r, 'model')} / {g(r, 'task_id')}" for r in rows
                   if g(r, "error").lower() == "length" or g(r, "finish_reason").lower() == "length"]
        if bad_api or bad_cut:
            rep.fail(f"{name}: incomplete responses: {len(bad_api)} API error(s)"
                     + (f" ({listing(bad_api, 5)})" if bad_api else "")
                     + f", {len(bad_cut)} cut off by the token limit" + (f" ({listing(bad_cut, 5)})" if bad_cut else "")
                     + ". All model responses must be complete, with no truncation or API error: find the cause, fix "
                     "it and run the evaluation again.")
        else:
            rep.ok(f"{name}: every response is complete (no API error or cut-off).")
    if len(rows_by_file) == 2:
        (a, ra), (b, rb) = rows_by_file.items()
        key = lambda r: (cell(r.get("model")), cell(r.get("task_id")), cell(r.get("correct")),  # noqa: E731
                         cell(r.get("error")).lower())
        if Counter(map(key, ra)) != Counter(map(key, rb)):
            rep.fail("responses.csv and responses.jsonl disagree (model, task_id, correct or error). Ship one, or "
                     "make them identical.")

    # run config, raw responses, error/retry log (Section 5: model IDs, prompts, settings, raw responses, errors)
    cfgs = [f for f in files if Path(f).name.lower() in ("run_config.json", "config.json", "eval_config.json")]
    if not cfgs:
        rep.warn(f"No run_config.json in {folder}/. Section 5 asks for the exact model IDs, prompts and settings; the "
                 "judge reads them from run_config.json (models, temperature, max_tokens, prompt_template).")
    else:
        try:
            cfg = json.loads(sub.read_text(cfgs[0]))
        except json.JSONDecodeError as e:
            rep.fail(f"{cfgs[0]} is not valid JSON ({e.msg}).")
            cfg = None
        if isinstance(cfg, dict):
            flat = json.dumps(cfg)
            temps = re.findall(r'"temperature"\s*:\s*([0-9.]+)', flat)
            if not temps:
                rep.warn(f"{Path(cfgs[0]).name}: no temperature recorded (Section 4: temperature=0).")
            elif any(float(t) != 0 for t in temps):
                rep.fail(f"{Path(cfgs[0]).name}: temperature {', '.join(sorted(set(temps)))}; Section 4 requires 0.")
            else:
                rep.ok(f"{Path(cfgs[0]).name}: temperature 0.")
            if not re.search(r'"max_(?:output_|completion_|new_)?tokens"\s*:\s*\d', flat):
                rep.warn(f"{Path(cfgs[0]).name}: no max_tokens recorded (Section 4: a fixed max_tokens).")
            if not re.search(r'"prompt', flat):
                rep.warn(f"{Path(cfgs[0]).name}: no prompt or prompt template recorded (Section 5 asks for the prompts).")
    raw = [f for f in sub.files if in_folder(f, folder) and re.search(r"raw", f[len(folder) + 1:], re.I)]
    if raw:
        rep.ok(f"Raw responses: {len(raw)} file(s) under {folder}/.")
    else:
        rep.warn(f"No raw-response logs found under {folder}/ (a file or folder named raw…). Section 5 asks for the "
                 "raw responses.")
    elog = [f for f in sub.files if in_folder(f, folder) and re.search(r"error|retr", Path(f).name, re.I)]
    if elog:
        rep.info(f"API error / retry log: {listing(elog, 3)}.")


def prompt_templates(sub: Submission, results_dir):
    """Prompt text from the run config, used to look for tolerance statements the template adds."""
    if not results_dir:
        return ""
    for name in ("run_config.json", "config.json", "eval_config.json"):
        f = f"{results_dir}/{name}"
        if f in sub.fileset:
            try:
                cfg = json.loads(sub.read_text(f))
            except json.JSONDecodeError:
                return ""
            out = []

            def walk(o, key=""):
                if isinstance(o, dict):
                    for k, v in o.items():
                        walk(v, k)
                elif isinstance(o, list):
                    for v in o:
                        walk(v, key)
                elif isinstance(o, str) and "prompt" in key.lower():
                    out.append(o)
            walk(cfg)
            return "\n".join(out)
    return ""


# ---- 8. Run in Docker section ---------------------------------------------------------------------------------------
def check_docker_section(rep, sections):
    rep.start("8. README \"Run in Docker\" commands")
    if "Run in Docker" not in sections:
        rep.fail("Skipped: no Docker section in the README.")
        return
    body = sections["Run in Docker"][1]
    runs = re.findall(r"docker\s+(?:container\s+)?run\b", body)
    if not runs:
        rep.fail("The Docker section has no `docker run` command. The app reports the reproduction as an error.")
    elif len(runs) < 2:
        rep.warn("The Docker section has one `docker run` command; it needs two: (1) rebuild the 10 reported tasks "
                 "and evaluate and score them, (2) build, evaluate and score one fresh task.")
    else:
        rep.ok(f"{len(runs)} `docker run` commands.")
    if re.search(r"docker\s+(?:image\s+)?build\b|docker\s+buildx\s+build\b|docker\s+compose\s+build\b", body):
        rep.ok("`docker build` command present.")
    else:
        rep.fail("The Docker section has no `docker build` command. Judges build the image from the README's commands.")
    if "OPENROUTER_API_KEY" not in body:
        rep.fail("The Docker section does not pass OPENROUTER_API_KEY (e.g. `docker run -e OPENROUTER_API_KEY …`).")
    elif re.search(r"--env-file", body):
        rep.warn("The Docker section uses --env-file. Judges pass the key as the OPENROUTER_API_KEY environment "
                 "variable; make sure `-e OPENROUTER_API_KEY` works on its own.")
    else:
        rep.ok("OPENROUTER_API_KEY is passed at run time.")
    if not re.search(r"(?:\s-v\s|--volume|--mount)", body):
        rep.warn("No volume mount (-v / --mount) in the Docker commands: outputs written inside the container are "
                 "lost when it exits. Say where the outputs are written.")
    if not re.search(r"(?i)\bseed", body):
        rep.warn("The Docker section does not say how to choose the seed.")
    if not re.search(r"(?i)\bmodel", body):
        rep.warn("The Docker section does not say how to choose the model.")
    if not re.search(r"(?i)\bfresh|\bnew task|\bone task", body):
        rep.warn("The Docker section does not seem to describe building one fresh task.")
    if re.search(r"(?m)^\s*(cd|source|conda|python3?\s+-m\s+venv)\b", body) and not runs:
        rep.warn("The Docker section runs host commands; only Docker should be needed.")


# ---- main -----------------------------------------------------------------------------------------------------------
def run(path: Path, tasks_arg=None, results_arg=None, expected=EXPECTED_TASKS, max_upload_mb=MAX_UPLOAD_MB,
        max_uncompressed_mb=MAX_UNCOMPRESSED_MB):
    rep = Report()
    if path.is_file():
        if not check_upload(rep, path, max_upload_mb, max_uncompressed_mb):
            return rep, {}
    else:
        rep.start("1. Upload (the judges' app refuses the file if these fail)")
        rep.info("Checking a folder: the ZIP-level checks (name, size, paths, integrity) are skipped. Run the "
                 "checker again on the final ZIP.")
    sub = Submission(path)
    if not sub.files:
        rep.start("2. Repository layout")
        rep.fail("The submission holds no files.")
        return rep, {}
    readme, dockerfile = check_layout(rep, sub)
    check_secrets(rep, sub, dockerfile)
    readme_text, sections, _hand = check_readme(rep, sub, readme)
    tasks_dir, results_dir = check_inspect(rep, sub, readme_text, sections, tasks_arg, results_arg)
    tasks = None
    if tasks_dir is not None:
        tasks = check_tasks(rep, sub, tasks_dir, expected, prompt_templates(sub, results_dir))
        if tasks and _hand:
            missing = [t for t in tasks["ids"] if not re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", _hand)]
            if missing:
                rep.warn(f"The README's hand-check notes do not mention {len(missing)} reported task(s): "
                         f"{listing(missing, 5)}.")
            else:
                rep.ok("The README's hand-check notes mention every reported task.")
    if results_dir is not None:
        check_results(rep, sub, results_dir, tasks)
    check_docker_section(rep, sections)
    return rep, {"tasks": tasks_dir, "results": results_dir}


COLORS = {"PASS": "\033[32m", "WARN": "\033[33m", "FAIL": "\033[31m", "INFO": "\033[36m"}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check a Track 2 submission ZIP (or folder) against the required format "
                                             "(guide Section 5) and the judges' upload checks.")
    ap.add_argument("submission", help="TeamName_Track2.zip, or the repository folder before zipping")
    ap.add_argument("--tasks", help="reported task folder, from the repository root (default: the one the README's "
                                    "\"Reported folders\" section names)")
    ap.add_argument("--results", help="reported results folder, from the repository root (default: from the README)")
    ap.add_argument("--expected-tasks", type=int, default=EXPECTED_TASKS, help="tasks in the reported set (default 10)")
    ap.add_argument("--max-upload-mb", type=int, default=MAX_UPLOAD_MB, help=f"default {MAX_UPLOAD_MB}")
    ap.add_argument("--max-uncompressed-mb", type=int, default=MAX_UNCOMPRESSED_MB, help=f"default {MAX_UNCOMPRESSED_MB}")
    ap.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    ap.add_argument("--quiet", action="store_true", help="print only WARN and FAIL lines and the summary")
    ap.add_argument("--json", metavar="FILE", help="also write the results as JSON to FILE ('-' for stdout only)")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    a = ap.parse_args(argv)
    path = Path(a.submission)
    if not path.exists():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 2
    rep, chosen = run(path, a.tasks, a.results, a.expected_tasks, a.max_upload_mb, a.max_uncompressed_mb)
    c = rep.counts()
    failed = c["FAIL"] > 0 or (a.strict and c["WARN"] > 0)
    if a.json:
        data = {"checker_version": VERSION, "submission": str(path), "reported_folders": chosen,
                "passed": not failed, "counts": dict(c),
                "checks": [{"section": s, "level": lv, "message": m} for s, lv, m in rep.items]}
        if a.json == "-":
            print(json.dumps(data, indent=2))
            return 1 if failed else 0
        Path(a.json).write_text(json.dumps(data, indent=2))
    color = sys.stdout.isatty() and not a.no_color and os.environ.get("NO_COLOR") is None
    paint = (lambda lv: f"{COLORS[lv]}{lv}\033[0m") if color else (lambda lv: lv)
    print(f"Track 2 submission format check v{VERSION}: {path}")
    section = None
    for s, lv, m in rep.items:
        if a.quiet and lv in ("PASS", "INFO"):
            continue
        if s != section:
            print(f"\n{s}")
            section = s
        print(f"  [{paint(lv)}] {m}")
    print(f"\n{c['FAIL']} FAIL, {c['WARN']} WARN, {c['PASS']} PASS.")
    if c["FAIL"]:
        print("Result: NOT READY. Fix every FAIL before you submit.")
    elif c["WARN"]:
        print("Result: format OK" + (" (but --strict: warnings count as failures)." if a.strict else
                                     ". Read the warnings: each one is something the guide asks for."))
    else:
        print("Result: format OK.")
    print("This checks the format only. It does not build your image, rerun your pipeline or judge task quality; "
          "run your README's \"Run in Docker\" commands on a clean machine as well.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
