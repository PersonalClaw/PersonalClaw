"""A workflow step never feeds a quiet ``grep`` from a pipe while ``pipefail`` is on.

``grep -q`` stops reading at its first match. The command writing into the pipe then fails on
its next write, and ``pipefail`` makes that failure the step's exit status, so the check reds
precisely when what it looks for turns up early. v0.2.0's ``desktop-linux`` job failed this way
on a good package: ``dpkg-deb --contents … | grep -q "resources/app.asar"`` printed
``tar: stdout: write error``, and the release's ``notes`` job, which needs it, never ran.
The release jobs run on tags only, so nothing else exercises them before a release.

Search a file or a variable instead. ``printf``/``echo`` of a variable is exempt: the shell
builtin hands the whole string to the pipe in one write before ``grep`` reads it.

Parsed rather than grepped: a step comment that explains the trap quotes the pattern too.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"

# A single `|` (never `||`) between two commands.
_PIPE = re.compile(r"(?<!\|)\|(?!\|)")
_QUIET_GREP = re.compile(r"^\s*grep\s+(?:-[A-Za-z]*q[A-Za-z]*\b|--quiet\b|--silent\b)")
_LEADING_KEYWORDS = re.compile(r"^\s*(?:(?:if|elif|while|until|then|do|!)\s+|\$\(|\()*")
_BUILTIN_WRITERS = ("printf", "echo")


def _logical_lines(script: str) -> list[str]:
    """Shell lines with backslash continuations joined and comments dropped."""
    joined = re.sub(r"\\\n", " ", script)
    return [
        line for line in joined.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]


def quiet_greps_fed_by_a_pipe(script: str) -> list[str]:
    """Every line of ``script`` where an external command pipes into a quiet grep."""
    found = []
    for line in _logical_lines(script):
        segments = _PIPE.split(line)
        for writer, reader in zip(segments, segments[1:]):
            if not _QUIET_GREP.match(reader):
                continue
            command = _LEADING_KEYWORDS.sub("", writer).split()
            if command and command[0] not in _BUILTIN_WRITERS:
                found.append(line.strip())
    return found


def _shell(step: dict, job: dict, workflow: dict) -> str:
    for scope in (
        step,
        job.get("defaults", {}).get("run", {}),
        workflow.get("defaults", {}).get("run", {}),
    ):
        if scope.get("shell"):
            return str(scope["shell"])
    return ""


def _runs_under_pipefail(script: str, shell: str) -> bool:
    # `shell: bash` runs `bash --noprofile --norc -eo pipefail {0}`; no `shell:` runs `bash -e {0}`.
    return "pipefail" in script or shell.strip() == "bash" or "pipefail" in shell


def test_the_detector_flags_the_line_that_failed_and_passes_its_fix():
    assert quiet_greps_fed_by_a_pipe('dpkg-deb --contents x.deb | grep -q "resources/app.asar"')
    assert quiet_greps_fed_by_a_pipe("a | sort | grep -qF needle")
    assert quiet_greps_fed_by_a_pipe(
        "if ! curl -s \\\n  https://example.test | grep --quiet ok; then exit 1; fi"
    )
    assert not quiet_greps_fed_by_a_pipe(
        'grep -q "resources/app.asar" "$RUNNER_TEMP/deb-contents.txt"'
    )
    assert not quiet_greps_fed_by_a_pipe(
        'if printf \'%s\' "$msg" | grep -qF "Signed-off-by"; then :; fi'
    )
    assert not quiet_greps_fed_by_a_pipe("make lint || grep -q x log.txt")
    assert not quiet_greps_fed_by_a_pipe("# dpkg-deb --contents x.deb | grep -q app.asar")


def test_no_step_feeds_a_quiet_grep_from_a_pipe_under_pipefail():
    paths = sorted(_WORKFLOWS.glob("*.yml"))
    assert paths, "no workflows found"
    offenders = []
    for path in paths:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_id, job in (workflow.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                script = str(step.get("run") or "")
                if not script or not _runs_under_pipefail(script, _shell(step, job, workflow)):
                    continue
                offenders += [
                    f"{path.name} {job_id}: {line}" for line in quiet_greps_fed_by_a_pipe(script)
                ]
    assert not offenders, (
        "a quiet grep reads a pipe under pipefail, so the check fails when it finds its match "
        "early; write the output to a file and search that:\n" + "\n".join(offenders)
    )
