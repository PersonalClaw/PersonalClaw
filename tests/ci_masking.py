"""The CI contract's masking row (#3918): nothing throws away a command's exit status unseen.

A check that cannot fail is worse than none: the audit was green 33 of 33 nights by construction
(``pip_audit || true``), and the image's test stage let a type error through (``mypy ... ||
true``). So every place a workflow, a Dockerfile or a script under ``scripts/`` masks an exit
status is named here, with its reason:

- a shell ``|| true`` or ``|| :`` that ends a command (``|| : > file``, which writes a fallback,
  is not a mask), in a workflow step's ``run``, a Dockerfile, or a ``scripts/**/*.sh`` file;
- a ``continue-on-error`` on a job or a step.

Every such site must match an entry below, not only a validation tool's. Telling a validation
command from a cleanup one by its words would miss a wrapper (``make``, ``uv run``, a script),
so the row asks for a reason at every site instead. An entry that matches nothing fails too, so
the list cannot rot. A tolerated step whose result is what the job reports (``outcome_read``)
must also be read back by a later step of its job, as ``steps.<id>.outcome``. A job with more
than one reads them together, in one expression that is green only when every one was:
``steps.<a>.outcome == 'success' && steps.<b>.outcome == 'success' && 'success' || 'failure'``,
so one step's red never reads as green because another step was green.

What it does not see: a mask spelled another way (``set +e``, ``if cmd; then :; fi``, a
``trap``'d exit, ``|| echo``). The row holds the forms the tree uses and a realistic edit
would add; an adversarial rewrite is review's to catch.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]

#: The one way a job with several ``outcome_read`` steps reads their outcomes: green only when
#: every one of them was, each ``steps.<id>.outcome == 'success'``, joined by ``&&``.
_EVERY_ONE_GREEN = re.compile(
    r"(?:steps\.[\w-]+\.outcome == 'success' && )+'success' \|\| 'failure'"
)
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)
_OUTCOME = re.compile(r"steps\.([\w-]+)\.outcome\b")

#: ``|| true`` or ``|| :`` ending a command: at the end of the line, or before ``)``, a quote,
#: ``;``, ``&``, ``|`` or a comment.
_MASK = re.compile(r"\|\|\s*(?:true|:)(?=\s*(?:$|[)'\";&|#]))")


@dataclass(frozen=True)
class Masked:
    """An ``|| true`` line: its file, a piece of its text that names it, and why."""

    file: str
    text: str
    reason: str


@dataclass(frozen=True)
class Tolerated:
    """A ``continue-on-error``: the jobs it is on, the step (``None`` for the job itself)."""

    file: str
    jobs: tuple[str, ...]
    step: str | None
    reason: str
    outcome_read: bool = False


_CI = ".github/workflows/ci.yml"
_DOCKER = ".github/workflows/docker-single-container.yml"
_MERGE = ".github/workflows/merge.yml"
_WALK = ".github/workflows/clean-machine-walkthroughs.yml"
_BREADTH = ".github/workflows/breadth.yml"
_SUITE_LEGS = ("ubuntu", "macos", "arm", "coverage-shard")
#: The breadth legs that may carry an earlier result: the rails on macOS, and the whole-suite
#: collection that counts parses of the source tree (it runs no test). The suite legs run the
#: security-marked modules, so they run every night and carry nothing.
_TREE_KEYED_LEGS = ("rails-macos", "collection-parses")

MASKED: tuple[Masked, ...] = (
    Masked(
        _CI,
        'python3 "$check" --workflow ci.yml || true',
        "tree-check: a check that crashes or is refused writes no answer, and the `case` below "
        "reads anything but tested=true|false as tested=false, so every job runs.",
    ),
    Masked(
        _CI,
        'answer=$(cat "$RUNNER_TEMP/answer.txt" 2>/dev/null || true)',
        "tree-check: no answer file reads as empty, which the `case` below makes tested=false.",
    ),
    Masked(
        _CI,
        '--job "$SHARD_JOB" --group-prefix "$GROUP_PREFIX" || true',
        "The `test` and `test-macos-gate` gates' superseded proof: a check that crashes writes "
        "no answer, and the gate reads anything but superseded=<sha> as unproven. It exits 1 "
        "either way; the contract row runs the script and holds that.",
    ),
    Masked(
        _CI,
        'line=$(cat "$RUNNER_TEMP/answer.txt" 2>/dev/null || true)',
        "The `test` and `test-macos-gate` gates' superseded proof: no answer file reads as "
        "empty, which is unproven, and the gate exits 1 either way.",
    ),
    Masked(
        _CI,
        "--jq '.[0].number // empty' 2>/dev/null || true",
        "The feedback relay's pull request number, looked up by branch: none found means there "
        "is nothing to post to, and the step says so.",
    ),
    Masked(
        _MERGE,
        'python3 "$check" --workflow merge.yml || true',
        "tree-check, as in ci.yml: no answer is tested=false, so the whole tier is planned.",
    ),
    Masked(
        _MERGE,
        'answer=$(cat "$RUNNER_TEMP/answer.txt" 2>/dev/null || true)',
        "tree-check, as in ci.yml: no answer file is tested=false.",
    ),
    Masked(
        _DOCKER,
        'python3 "$check" --workflow docker-single-container.yml || true',
        "tree-check, as in ci.yml: no answer is tested=false, so the image is built.",
    ),
    Masked(
        _DOCKER,
        'answer=$(cat "$RUNNER_TEMP/answer.txt" 2>/dev/null || true)',
        "tree-check, as in ci.yml: no answer file is tested=false.",
    ),
    Masked(
        _WALK,
        "cat /data/walkthrough-marker 2>/dev/null || true",
        "Reads the marker back; the `test` on the next line fails the step when it is missing.",
    ),
    Masked(
        _WALK,
        "docker compose -f deploy/compose/compose.yaml down -v || true",
        "Teardown, under `if: always()`: the stack may never have come up.",
    ),
    Masked(
        _WALK,
        "unset PERSONALCLAW_PROJECT_DIR || true",
        "Clears a variable that may not be set; nothing is checked.",
    ),
    Masked(
        ".github/workflows/headless-run-smoke.yml",
        'pgrep -af "personalclaw gateway" || true',
        "Lists the leaked gateway for the log, in the branch that has already decided to exit 1.",
    ),
    Masked(
        ".github/workflows/release.yml",
        "trap 'hdiutil detach \"$MNT\" -quiet || true' EXIT",
        "Detaches the mounted disk image on exit, whatever the check before it concluded.",
    ),
    Masked(
        ".github/workflows/release-artifacts.yml",
        "trap 'hdiutil detach \"$MNT\" -quiet || true' EXIT",
        "The rehearsal's copy of release.yml's desktop-mac check: the same detach on exit.",
    ),
    Masked(
        "scripts/smoke_backend_bundle.sh",
        'kill "$GATEWAY_PID" 2>/dev/null || true',
        "Cleanup on exit: the gateway may already have stopped.",
    ),
    Masked(
        "scripts/smoke_backend_bundle.sh",
        'kill -9 "$GATEWAY_PID" 2>/dev/null || true',
        "Cleanup on exit, after the polite kill: the gateway may already have stopped.",
    ),
    Masked(
        "scripts/smoke_backend_bundle.sh",
        "grep -o '[0-9]*$' || true)",
        "Polls the boot log for a port: no port yet is an empty read, and the loop waits, then "
        "the script fails when none ever appears.",
    ),
)

TOLERATED: tuple[Tolerated, ...] = (
    Tolerated(
        _BREADTH,
        _TREE_KEYED_LEGS,
        "the earlier record's results",
        "No earlier record (a first night, an expired artifact) is not a failure: decide then "
        "says run.",
    ),
    Tolerated(
        _BREADTH,
        _TREE_KEYED_LEGS,
        "decide",
        "A decision that crashes writes no run=false, so the shard runs.",
    ),
    Tolerated(
        _BREADTH,
        (*_SUITE_LEGS, *_TREE_KEYED_LEGS, "security", "security-macos", "canary"),
        "pytest",
        "Its outcome is the shard's result: `ran` records it, and the last step fails the job "
        "on a red entry, after the evidence is written and uploaded.",
        outcome_read=True,
    ),
    *(
        Tolerated(
            _BREADTH,
            ("random-order",),
            order,
            "The second order runs whatever the first did, and the two outcomes together are "
            "the shard's result: `ran` records it, and the last step fails the job on a red "
            "entry, after the evidence is written and uploaded.",
            outcome_read=True,
        )
        for order in ("pytest-1", "pytest-2")
    ),
    Tolerated(
        _BREADTH,
        ("canary",),
        None,
        "The next Ubuntu image's leg: its red is the image's news, recorded and kept in its own "
        "issue, and not a verdict on the run.",
    ),
    Tolerated(
        _BREADTH,
        ("planned-ids-canary", "shard-union-canary"),
        None,
        "The canary's union check: its planned suite collected on the next Ubuntu image, and its "
        "shards held to it. Its red is the image's news, as the canary's is, and the gating "
        "union (`shard-union`) never reads the canary's ids.",
    ),
    Tolerated(
        _CI,
        ("e2e-shard", "e2e-stateful", "browse-live"),
        "chromium-starts",
        "A Chromium that does not start on the runner image is the cue to install its system "
        "libraries, not a failure: the next step runs apt only then, and checks again, so a "
        "browser that still cannot start fails the job there.",
        outcome_read=True,
    ),
    Tolerated(
        ".github/workflows/pr-feedback.yml",
        ("lint-help",),
        "dl",
        "Most failed runs upload no lint feedback, so there is often nothing to download.",
    ),
    Tolerated(
        ".github/workflows/release.yml",
        ("website-follow",),
        None,
        "Best effort by design: the website is a separate repository, and the release is done.",
    ),
)


def _paths(root: Path) -> list[Path]:
    return [
        *sorted(root.glob(".github/workflows/*.yml")),
        *sorted(root.glob("deploy/docker/Dockerfile*")),
        *sorted(root.glob("scripts/**/*.sh")),
    ]


def scaffold(root: Path) -> None:
    """The row's scaffold: a copy of every file it reads, as the tree has them."""
    for path in _paths(REPO):
        target = root / path.relative_to(REPO)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def _lines(text: str) -> Iterator[str]:
    """Shell or Dockerfile lines, continued lines joined, comment lines dropped."""
    for line in text.replace("\\\n", " ").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield stripped


def _workflow_jobs(root: Path) -> Iterator[tuple[str, str, dict[str, Any]]]:
    for path in sorted(root.glob(".github/workflows/*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_id, job in (doc.get("jobs") or {}).items():
            yield path.relative_to(root).as_posix(), str(job_id), job or {}


def _masked_lines(root: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for rel, _, job in _workflow_jobs(root):
        for step in job.get("steps") or []:
            found += [(rel, line) for line in _lines(str(step.get("run", "")))]
    for path in _paths(root):
        if not path.name.endswith(".yml"):
            text = path.read_text(encoding="utf-8")
            found += [(path.relative_to(root).as_posix(), line) for line in _lines(text)]
    return [(rel, line) for rel, line in found if _MASK.search(line)]


def _on(value: object) -> bool:
    return value is not None and value is not False and str(value).strip().lower() != "false"


def violations(root: Path) -> list[str]:
    bad: list[str] = []
    masked = _masked_lines(root)
    for rel, line in masked:
        if not any(e.file == rel and e.text in line for e in MASKED):
            bad.append(f"{rel}: `{line}` throws an exit status away, and no entry names it")
    for e in MASKED:
        if not any(rel == e.file and e.text in line for rel, line in masked):
            bad.append(f"{e.file}: the entry for `{e.text}` matches nothing: delete it")

    sites: dict[tuple[str, str, str | None], tuple[dict[str, Any], int]] = {}
    for rel, job_id, job in _workflow_jobs(root):
        if _on(job.get("continue-on-error")):
            sites[(rel, job_id, None)] = (job, -1)
        for i, step in enumerate(job.get("steps") or []):
            if _on(step.get("continue-on-error")):
                key = str(step.get("id") or step.get("name") or f"step {i + 1}")
                sites[(rel, job_id, key)] = (job, i)
    named = {(e.file, job, e.step): e for e in TOLERATED for job in e.jobs}
    for (rel, job_id, step), (job, index) in sites.items():
        entry = named.get((rel, job_id, step))
        where = f"{rel}:{job_id}" + (f" step {step!r}" if step else "")
        if entry is None:
            bad.append(f"{where} is continue-on-error, and no entry names it")
        elif entry.outcome_read:
            steps = job.get("steps") or []
            sid = steps[index].get("id")
            later = " ".join(str(s) for s in steps[index + 1 :])
            if not sid or f"steps.{sid}.outcome" not in later:
                bad.append(f"{where} is continue-on-error, and no later step reads its outcome")
    bad += _read_together(sites, named)
    for key in named:
        if key not in sites:
            rel, job_id, step = key
            what = f"step {step!r}" if step else "the job"
            bad.append(f"{rel}:{job_id}: the entry for {what} matches nothing: delete it")
    return bad


def _strings(value: object) -> Iterator[str]:
    """Every string in a step, its keys' values at any depth."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _read_together(
    sites: dict[tuple[str, str, str | None], tuple[dict[str, Any], int]],
    named: dict[tuple[str, str, str | None], Tolerated],
) -> list[str]:
    """In a job with more than one ``outcome_read`` step, every expression that reads one of
    their outcomes reads them all, green only when every one was."""
    groups: dict[tuple[str, str], tuple[dict[str, Any], set[str]]] = {}
    for (rel, job_id, step), (job, index) in sites.items():
        entry = named.get((rel, job_id, step))
        sid = (job.get("steps") or [{}])[index].get("id") if index >= 0 else None
        if entry is not None and entry.outcome_read and sid:
            groups.setdefault((rel, job_id), (job, set()))[1].add(str(sid))
    bad: list[str] = []
    for (rel, job_id), (job, ids) in sorted(groups.items()):
        if len(ids) < 2:
            continue
        for step in job.get("steps") or []:
            for text in _strings(step):
                for expression in _EXPRESSION.findall(text):
                    read = set(_OUTCOME.findall(expression))
                    if not read & ids:
                        continue
                    spoken = " ".join(expression.split())
                    if read != ids or not _EVERY_ONE_GREEN.fullmatch(spoken):
                        bad.append(
                            f"{rel}:{job_id} reads `{spoken}`, not green only when every one of"
                            f" {sorted(ids)} was: each `steps.<id>.outcome == 'success'`,"
                            " joined by &&"
                        )
    return bad


#: The row's planted violations: (name, file, old text, new text). Each must fail the check.
PLANTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "a pytest whose exit status is thrown away",
        _CI,
        '--shard-list "$RUNNER_TEMP/shard.txt" --junitxml=reports/junit-test-shard-'
        "${{ matrix.shard }}.xml",
        '--shard-list "$RUNNER_TEMP/shard.txt" --junitxml=reports/junit-test-shard-'
        "${{ matrix.shard }}.xml || true",
    ),
    (
        "a validation step that may fail without failing its job",
        _CI,
        "      - name: audit the graphs core ships\n",
        "      - name: audit the graphs core ships\n        continue-on-error: true\n",
    ),
    (
        "a job that may fail without failing the run",
        _CI,
        "    timeout-minutes: 15\n    permissions:\n      contents: read\n    steps:\n",
        "    timeout-minutes: 15\n    continue-on-error: true\n    permissions:\n"
        "      contents: read\n    steps:\n",
    ),
    (
        "a masked command in the image build",
        "deploy/docker/Dockerfile.backend",
        "        -r /build/requirements.txt\n",
        "        -r /build/requirements.txt || true\n",
    ),
    (
        "a masked check in a script",
        "scripts/ci_smoke_run.sh",
        '|| fail ".turns was not 1"',
        "|| true",
    ),
    (
        "an entry whose line is gone",
        ".github/workflows/headless-run-smoke.yml",
        '            pgrep -af "personalclaw gateway" || true\n',
        '            pgrep -af "personalclaw gateway"\n',
    ),
    (
        "a tolerated pytest whose outcome nothing reads",
        _BREADTH,
        "OUTCOME: ${{ steps.pytest.outcome }}",
        "OUTCOME: success",
    ),
    (
        "two tolerated orders whose shard is green when either was",
        _BREADTH,
        "OUTCOME: ${{ steps.pytest-1.outcome == 'success' && steps.pytest-2.outcome == 'success'"
        " && 'success' || 'failure' }}",
        "OUTCOME: ${{ (steps.pytest-1.outcome == 'success' || steps.pytest-2.outcome =="
        " 'success') && 'success' || 'failure' }}",
    ),
    (
        "two tolerated orders whose shard is green when the first was, and the second read apart",
        _BREADTH,
        "OUTCOME: ${{ steps.pytest-1.outcome == 'success' && steps.pytest-2.outcome == 'success'"
        " && 'success' || 'failure' }}",
        "OUTCOME: ${{ steps.pytest-1.outcome == 'success' && 'success' || 'failure' }}\n"
        "          SECOND: ${{ steps.pytest-2.outcome }}",
    ),
    (
        "an entry whose step is gone",
        ".github/workflows/pr-feedback.yml",
        "        continue-on-error: true   # most CI failures are test/web, which upload nothing\n",
        "",
    ),
)
