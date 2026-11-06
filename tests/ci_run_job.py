"""A workflow step that runs a catalogue entry through run-job, read as the entry's command (#5600).

A tier workflow's job may run one catalogue entry as ``python -m tools.ci run-job <id>``: its
steps are then the catalogue's alone. Every CI rail that reads what a job runs reads such a step
through :func:`read_run_job`, one step per command step, so a rail holds a leg to the same rule
whether its steps are written out or run through run-job. The contract rows in
``tests/rails/test_ci_contract.py`` read every workflow this way (the rows that hold the run-job
line itself read it as written), and so do the masking row (``tests/ci_masking.py``) and the
JUnit report rails (``tests/rails/test_ci_junit_artifacts_tree.py``).
"""

from __future__ import annotations

import functools
import re
import shlex
import tomllib
from collections.abc import Iterator, Sequence
from types import SimpleNamespace
from typing import Any

from tools.ci import catalogue

#: The key each step read from a run-job step carries: the run-job step's id, whose outcome
#: is the step's own.
RUN_JOB_STEP = "run-job-step"
#: Each catalogue placeholder as a workflow line spells it, the first of its spellings.
SPELLINGS = {name: spellings[0] for name, spellings in catalogue.PLACEHOLDERS.items()}


def run_lines(run: str) -> Iterator[tuple[str, list[str] | None]]:
    """Each logical line of a run: block, a placeholder for each runner spelling, with its words
    (``None`` for a line no argv could equal, such as an echo with an apostrophe)."""
    for name, spellings in catalogue.PLACEHOLDERS.items():
        for spelling in spellings:
            run = run.replace(spelling, f"{{{name}}}")
    for line in run.replace("\\\n", " ").splitlines():
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            yield line.strip(), None
            continue
        if words:
            yield line.strip(), words


def run_job_call(words: Sequence[str]) -> tuple[str, tuple[str, ...]] | None:
    """The entry and the arguments after it when *words*, one command with its placeholders read
    (:func:`run_lines`), run ``python -m tools.ci run-job <id>``: by the runner's Python or the
    job's own (``{python}``), ``-I`` allowed. None for any other command."""
    words = list(words)
    if not words:
        return None
    head = words[0].rsplit("/", 1)[-1]
    if not (head == "{python}" or re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", head)):
        return None
    args = words[1:]
    if args[:1] == ["-I"]:
        args = args[1:]
    if args[:3] != ["-m", "tools.ci", "run-job"] or len(args) < 4:
        return None
    return args[3], tuple(args[4:])


@functools.lru_cache(maxsize=8)
def catalogue_entries(text: str) -> dict[str, Any]:
    """The catalogue's entries by id, each with its ``id`` and its ``command`` steps. When the
    catalogue refuses an entry its steps are still read from the TOML, so a rail judges what the
    leg would run, and the catalogue's own row says why it refuses. None when it is not TOML."""
    try:
        return {job.id: job for job in catalogue.load(text, catalogue.CATALOGUE).jobs}
    except catalogue.CatalogueError:
        pass
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    entries: dict[str, Any] = {}
    for job in data.get("job") or []:
        if not isinstance(job, dict) or not isinstance(job.get("id"), str):
            continue
        steps = job.get("command")
        if not isinstance(steps, list):
            continue
        command = tuple(
            catalogue.Step(
                tuple(str(w) for w in step["argv"]),
                str(step.get("workdir", "")),
                catalogue.Env({str(k): str(v) for k, v in (step.get("env") or {}).items()}),
            )
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("argv"), list)
        )
        entries[job["id"]] = SimpleNamespace(id=job["id"], command=command)
    return entries


def as_spelled(word: str) -> str:
    """One catalogue argv word as a workflow line writes it: each placeholder in its workflow
    spelling, double-quoted when a variable in it would be expanded, bare otherwise."""
    for name, spelling in SPELLINGS.items():
        if word == f"{{{name}}}" and spelling.startswith('"'):
            return spelling
        word = word.replace(f"{{{name}}}", spelling)
    if re.search(r"\$(?!\{\{)", word) or re.search(r"\s", re.sub(r"\$\{\{.*?\}\}", "", word)):
        return '"' + word.replace('"', '\\"') + '"'
    return word


#: A shell control operator or redirect, as a token of its own.
_OPERATOR = re.compile(r"[|&;<>()]+")


def one_command(line: str) -> bool:
    """Whether a run: line is one command: no control operator (``||``, ``&&``, ``;``, ``|``,
    ``&``) and no redirect outside its quotes."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return not any(_OPERATOR.fullmatch(token) for token in lexer)
    except ValueError:
        return False


def entry_of(step: dict[str, Any], entries: dict[str, Any]) -> Any | None:
    """The entry a step runs through run-job, when its run: is that one command and nothing
    more; else None. A run-job call with anything beside it (`|| true`, `; exit 0`, a redirect)
    stays as written, so each rail reads the line itself: the masking row names a mask."""
    lines = [(line, words) for line, words in run_lines(str(step.get("run", ""))) if words]
    if len(lines) != 1 or not one_command(lines[0][0]):
        return None
    call = run_job_call(lines[0][1])
    return entries.get(call[0]) if call else None


def read_run_job(catalogue_text: str, workflow: Any) -> Any:
    """*workflow*, parsed, with each step that runs an entry of the catalogue *catalogue_text*
    through run-job read as the entry's command: one step per command step, its run: the step's
    argv as the workflow spells it, its env the run-job step's with the command step's, its
    directory the command step's, and its other keys the run-job step's. The last keeps the
    step's name and id; each one before it names its place in the entry. Every one names the
    run-job step's id as its ``run-job-step``, the step whose outcome is its own: an id is the
    workflow's to hold once, so the ones before the last carry none."""
    if not isinstance(workflow, dict):
        return workflow
    # Only the reader marks a step as read from a run-job step: a key the workflow spells
    # itself goes, so no written-out step reads as one.
    for job in (workflow.get("jobs") or {}).values():
        for step in (job.get("steps") if isinstance(job, dict) else None) or []:
            if isinstance(step, dict):
                step.pop(RUN_JOB_STEP, None)
    entries = catalogue_entries(catalogue_text)
    if not entries:
        return workflow
    for job in (workflow.get("jobs") or {}).values():
        steps = job.get("steps") if isinstance(job, dict) else None
        if not isinstance(steps, list):
            continue
        read: list[Any] = []
        for step in steps:
            entry = entry_of(step, entries) if isinstance(step, dict) else None
            if entry is None or not entry.command:
                read.append(step)
                continue
            for n, command in enumerate(entry.command, 1):
                one = {k: v for k, v in step.items() if k not in ("run", "env", "id", "name")}
                env = {**(step.get("env") or {}), **dict(command.env)}
                if env:
                    one["env"] = env
                if command.workdir:
                    one["working-directory"] = command.workdir
                last = n == len(entry.command)
                one["name"] = (
                    step.get("name")
                    if last
                    else f"{step.get('name') or 'run-job'} ({entry.id}, step {n} of"
                    f" {len(entry.command)})"
                )
                if "id" in step:
                    one[RUN_JOB_STEP] = step["id"]
                if last and "id" in step:
                    one["id"] = step["id"]
                one["run"] = " ".join(as_spelled(w) for w in command.argv)
                read.append(one)
        job["steps"] = read
    return workflow
