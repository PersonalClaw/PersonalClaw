"""The tier workflows' own steps, run as written (#3915): pr.yml's and merge.yml's plan, merge.yml's
check of the catalogue, the verdict each run ends with, and the setup action's programs step their
stage jobs call.

Each step's ``run:`` is read out of its workflow and run under ``bash -e``, as GitHub runs a step
with no ``shell:``, with ``gh`` and ``git`` stubbed: ``gh`` lists the changed names as its
``--jq`` prints them (or serves main's ``verdict.py`` raw), ``git`` hands over main's tools/ci
(this checkout's, archived), and the planner either answers with a planted text or is the real
one.

What the rows hold. The plan step publishes only the keys and the stage jobs' matrices, each the
planner's own: an answer that is not a plan this workflow can run (no answer, a planner that
fails, keys missing or twice, a stage job the workflow does not run, a stage job's matrix that is
not a list of entries alone, an entry in a container, entries other than the keys) publishes
nothing and fails the step, so the verdict reads an empty plan and a failed job. A main whose
tools/ci cannot be fetched plans nothing either. The planner is main's, run from a folder of its
own over this checkout's catalogue. The check step refuses a catalogue main refuses, and a run of
main checks nothing. The verdict step reads main's ``verdict.py``, the plan's keys and the run's
records: green only with every record, red on a missing record, an empty plan or a failed job,
red when main's copy cannot be read, and never green on a malformed record. It is the results
job's last step, held back by nothing but a cancelled run, so the harness runs it as GitHub
does. The programs step is off unless a tier workflow names programs; it refuses every name
outside tmux, shellcheck and dash before it installs anything, and installs each one PATH lacks
from apt-get on Linux or brew on macOS, nothing elsewhere.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from rails_repo import REPO

from tools.ci import catalogue, merge_decision
from tools.ci import plan as planner
from tools.ci import verdict

_WORKFLOWS = REPO / ".github" / "workflows"
#: Each tier workflow, its tier and the stage jobs it runs.
_TIERS = {
    "pr.yml": ("change", ("test",)),
    "merge.yml": ("merge", ("build", "test", "test-2", "test-3")),
}
_REPO = "o/r"
_HEAD = "a" * 40
_RUN_ID = "4242"


def _workflow(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))


def _step(name: str, job: str, step_id: str) -> dict:
    steps = _workflow(name)["jobs"][job]["steps"]
    return next(s for s in steps if s.get("id") == step_id)


def _verdict_step(name: str) -> dict:
    steps = _workflow(name)["jobs"]["results"]["steps"]
    [step] = [s for s in steps if "--records" in str(s.get("run", ""))]
    return step


def _bash(script: str, tmp: Path, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    path = tmp / "step.sh"
    path.write_text(script, encoding="utf-8")
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", "-e", str(path)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


# ── the harness ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def judge_tar(tmp_path_factory) -> Path:
    """This checkout's tools/ci as ``git archive`` hands it over, the stand-in for main's."""
    out = tmp_path_factory.mktemp("judge") / "tools-ci.tar"
    listed = subprocess.run(
        ["git", "ls-files", "-z", "tools/ci"], cwd=REPO, capture_output=True, check=True
    ).stdout.decode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for rel in filter(None, listed.split("\0")):
            if (REPO / rel).is_file():
                tar.add(REPO / rel, arcname=rel)
    out.write_bytes(buffer.getvalue())
    return out


#: gh: the enumeration's names as its ``--jq`` prints them, or main's verdict.py raw; every other
#: call (the rails floor's) fails.
_GH = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB/gh.log"
[ -f "$STUB/gh-down" ] && exit 1
for a in "$@"; do
  case "$a" in
    */compare/*) cat "$STUB/changed"; exit 0 ;;
    */contents/tools/ci/verdict.py?ref=main) cat "$STUB/verdict.py"; exit 0 ;;
  esac
done
exit 1
"""

_GIT = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB/git.log"
case "$1" in
  fetch) [ -f "$STUB/fetch-fails" ] && exit 128; exit 0 ;;
  archive) cat "$STUB/judge.tar"; exit 0 ;;
esac
exit 1
"""

_PYTHON = """#!/bin/sh
if [ "$1" = "-m" ] && [ "$2" = "tools.ci" ] && [ "$3" = "plan" ]; then
  pwd > "$STUB/planner.cwd"
  for a in "$@"; do printf '%s\\n' "$a"; done > "$STUB/planner.argv"
  if [ -f "$STUB/answer" ]; then
    cat "$STUB/answer"
    exit "$(cat "$STUB/rc")"
  fi
fi
exec {python} "$@"
"""


@dataclass
class Ran:
    rc: int
    lines: list[str]
    log: str
    argv: list[str] | None
    cwd: str | None
    runner_temp: Path

    @property
    def published(self) -> dict[str, str]:
        return dict(line.partition("=")[::2] for line in self.lines)


def _stubs(tmp: Path) -> tuple[Path, Path, Path]:
    stub, bin_, temp = tmp / "stub", tmp / "bin", tmp / "runner"
    for folder in (stub, bin_, temp):
        folder.mkdir(parents=True)
    for name, body in (
        ("gh", _GH),
        ("git", _GIT),
        ("python3", _PYTHON.format(python=sys.executable)),
    ):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    return stub, bin_, temp


def _run_plan(
    tmp: Path,
    workflow: str,
    judge_tar: Path,
    *,
    answer: str | None = None,
    rc: int = 0,
    changed: tuple[str, ...] = ("docs/a.md",),
    fetch_fails: bool = False,
) -> Ran:
    """Run ``workflow``'s plan step once, over this checkout. ``answer`` plants the planner's
    stdout and ``rc``; without one the real planner runs from the archived judge."""
    stub, bin_, temp = _stubs(tmp)
    shutil.copy(judge_tar, stub / "judge.tar")
    if answer is not None:
        (stub / "answer").write_text(answer, encoding="utf-8", newline="")
        (stub / "rc").write_text(str(rc))
    (stub / "changed").write_text("".join(json.dumps(n) + "\n" for n in changed))
    if fetch_fails:
        (stub / "fetch-fails").write_text("")
    output, summary = tmp / "GITHUB_OUTPUT", tmp / "GITHUB_STEP_SUMMARY"
    output.write_text("")
    summary.write_text("")
    step = _step(workflow, "changes", "plan")
    env = {
        "PATH": f"{bin_}:/usr/bin:/bin",
        "HOME": str(tmp),
        "STUB": str(stub),
        **{k: str(v) for k, v in step["env"].items()},
        "GH_TOKEN": "a-token",
        "HEAD_SHA": _HEAD,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/feature",
        "GITHUB_REPOSITORY": _REPO,
        "GITHUB_WORKSPACE": str(REPO),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
        "RUNNER_TEMP": str(temp),
    }
    done = _bash(step["run"], tmp, env, REPO)
    argv, cwd = stub / "planner.argv", stub / "planner.cwd"
    return Ran(
        rc=done.returncode,
        lines=output.read_text(encoding="utf-8").split("\n")[:-1],
        log=done.stdout + done.stderr,
        argv=argv.read_text().splitlines() if argv.exists() else None,
        cwd=cwd.read_text().strip() if cwd.exists() else None,
        runner_temp=temp,
    )


def _own_plan(tier: str, changed: tuple[str, ...]) -> dict:
    """This checkout's planner over this checkout's catalogue, as the step runs main's."""
    path = REPO / catalogue.CATALOGUE
    cat = catalogue.load(path.read_text(encoding="utf-8"), str(path))
    overrides = planner.read_beside(path, cat)
    grid = planner.matrix(cat, planner.plan(cat, tier, list(changed), overrides=overrides))
    return grid


def _answer(grid: dict, jobs: dict[str, list[dict]] | None = None) -> str:
    split = jobs if jobs is not None else planner.stage_jobs(grid)
    lines = ["full=false", "jobs=[]", "matrix=" + json.dumps({"include": grid["include"]})]
    lines.append("keys=" + json.dumps(grid["keys"]))
    lines += [f"matrix-{name}=" + json.dumps({"include": e}) for name, e in split.items()]
    return "\n".join(lines) + "\n"


def _no_plan(ran: Ran) -> None:
    assert ran.rc != 0, ran.log
    assert ran.lines == [], ran.lines
    assert "::error title=no plan::" in ran.log, ran.log


# ── the wiring the harness relies on ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("workflow", sorted(_TIERS))
def test_the_plan_step_reads_only_the_env_the_harness_gives_it(workflow: str) -> None:
    tier, jobs = _TIERS[workflow]
    step = _step(workflow, "changes", "plan")
    assert step.get("env") == {
        "GH_TOKEN": "${{ github.token }}",
        "HEAD_SHA": "${{ github.sha }}",
        "TIER": tier,
        "STAGE_JOBS": " ".join(jobs),
    }
    assert step.get("shell") is None and _workflow(workflow).get("defaults") is None
    assert "${{" not in step["run"], "an expression pasted into the shell"


def test_both_workflows_plan_with_the_same_step() -> None:
    # One step text, its tier and stage jobs in its env: a fix to one is a fix to both.
    assert _step("pr.yml", "changes", "plan")["run"] == _step("merge.yml", "changes", "plan")["run"]
    assert _verdict_step("pr.yml") == _verdict_step("merge.yml")


# ── the plan step ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("workflow", "changed"),
    [
        ("pr.yml", ("docs/a.md",)),
        ("pr.yml", ("src/personalclaw/sdk/x.py",)),
        ("merge.yml", ("docs/a.md",)),
        ("merge.yml", ("src/personalclaw/sdk/x.py",)),
        ("merge.yml", ("web/src/main.ts",)),
    ],
)
def test_the_real_planner_publishes_its_keys_and_each_stage_jobs_matrix(
    tmp_path: Path, judge_tar: Path, workflow: str, changed: tuple[str, ...]
) -> None:
    tier, jobs = _TIERS[workflow]
    ran = _run_plan(tmp_path, workflow, judge_tar, changed=changed)
    assert ran.rc == 0, ran.log
    want = _own_plan(tier, changed)
    published = ran.published
    assert json.loads(published.pop("keys")) == want["keys"]
    split = planner.stage_jobs(want)
    assert set(published) == set(split) <= set(jobs), ran.lines
    for name, entries in split.items():
        assert json.loads(published[name]) == {"include": entries}, name
    # The planner is main's, beside this checkout's catalogue, for the workflow's own tier.
    assert ran.cwd is not None
    assert Path(ran.cwd).resolve() == (ran.runner_temp / "judge").resolve()
    assert (ran.argv or [])[:4] == ["-m", "tools.ci", "plan", tier]
    assert {"--outputs", "--matrix"} <= set(ran.argv or [])
    assert "--full" not in (ran.argv or [])


def test_merge_yml_plans_the_e2e_legs_after_the_build_for_a_web_change(
    tmp_path: Path, judge_tar: Path
) -> None:
    ran = _run_plan(tmp_path, "merge.yml", judge_tar, changed=("web/src/main.ts",))
    published = ran.published

    def ids(name: str) -> list[str]:
        return [e["id"] for e in json.loads(published[name])["include"]]

    assert ids("build") == ["web-build"]
    assert {"package", "e2e-shard", "e2e-stateful"} == set(ids("test-2"))
    assert ids("test-3") == ["e2e", "ui-quality"]
    assert "python-suite" in ids("test") and "web-build" not in ids("test")


@pytest.mark.parametrize(
    ("answer", "rc"),
    [
        pytest.param("", 2, id="exit 2, nothing printed"),
        pytest.param("", 0, id="nothing printed"),
        pytest.param("full=false\njobs=[]\n", 0, id="no keys"),
        pytest.param("full=false\nkeys=[]\n", 0, id="empty keys"),
        pytest.param('keys=["static"]\nkeys=["static"]\n', 0, id="keys twice"),
        pytest.param('keys="static"\n', 0, id="keys not a list"),
        pytest.param("keys=static\n", 0, id="keys not JSON"),
    ],
)
def test_an_answer_that_is_not_a_plan_publishes_nothing_and_fails(
    tmp_path: Path, judge_tar: Path, answer: str, rc: int
) -> None:
    _no_plan(_run_plan(tmp_path, "pr.yml", judge_tar, answer=answer, rc=rc))


def test_a_planner_that_fails_after_a_whole_answer_publishes_nothing(
    tmp_path: Path, judge_tar: Path
) -> None:
    answer = _answer(_own_plan("change", ("docs/a.md",)))
    ran = _run_plan(tmp_path, "pr.yml", judge_tar, answer=answer, rc=1)
    _no_plan(ran)


def test_a_planted_plan_is_published_as_the_planner_printed_it(
    tmp_path: Path, judge_tar: Path
) -> None:
    grid = _own_plan("change", ("docs/a.md",))
    ran = _run_plan(tmp_path, "pr.yml", judge_tar, answer=_answer(grid))
    assert ran.rc == 0, ran.log
    assert json.loads(ran.published["test"]) == {"include": grid["include"]}
    assert json.loads(ran.published["keys"]) == grid["keys"]


def test_a_stage_job_the_workflow_does_not_run_publishes_nothing(
    tmp_path: Path, judge_tar: Path
) -> None:
    # pr.yml runs `test` alone: a plan with a build would leave its entries unrun.
    grid = _own_plan("merge", ("web/src/main.ts",))
    _no_plan(_run_plan(tmp_path, "pr.yml", judge_tar, answer=_answer(grid)))


def test_entries_other_than_the_keys_publish_nothing(tmp_path: Path, judge_tar: Path) -> None:
    grid = _own_plan("change", ("docs/a.md",))
    fewer = {"test": grid["include"][1:]}
    _no_plan(_run_plan(tmp_path, "pr.yml", judge_tar, answer=_answer(grid, fewer)))
    more = {"test": [*grid["include"], grid["include"][0]]}
    _no_plan(_run_plan(tmp_path / "more", "pr.yml", judge_tar, answer=_answer(grid, more)))


def test_an_entry_in_a_container_publishes_nothing(tmp_path: Path, judge_tar: Path) -> None:
    grid = _own_plan("change", ("docs/a.md",))
    boxed = [dict(grid["include"][0], container="ubuntu:26.04"), *grid["include"][1:]]
    _no_plan(_run_plan(tmp_path, "pr.yml", judge_tar, answer=_answer(grid, {"test": boxed})))


def test_an_entry_whose_slug_no_artifact_name_can_hold_publishes_nothing(
    tmp_path: Path, judge_tar: Path
) -> None:
    grid = _own_plan("change", ("docs/a.md",))
    bad = [dict(grid["include"][0], slug="a/b"), *grid["include"][1:]]
    _no_plan(_run_plan(tmp_path, "pr.yml", judge_tar, answer=_answer(grid, {"test": bad})))


@pytest.mark.parametrize(
    "shape",
    [
        pytest.param(lambda entries: {"include": entries, "os": ["a", "b"]}, id="a list beside"),
        pytest.param(lambda entries: {"include": []}, id="no entries"),
        pytest.param(lambda entries: entries, id="a list, not a matrix"),
    ],
)
def test_a_stage_job_matrix_that_is_not_its_entries_alone_publishes_nothing(
    tmp_path: Path, judge_tar: Path, shape: Callable[[list[dict]], object]
) -> None:
    # A matrix GitHub would cross with another list runs each entry more than once, and one with
    # no entries runs nothing: the step refuses either before anything is published.
    grid = _own_plan("change", ("docs/a.md",))
    lines = [line for line in _answer(grid).splitlines() if not line.startswith("matrix-test=")]
    lines.append(f"matrix-test={json.dumps(shape(grid['include']))}")
    ran = _run_plan(tmp_path, "pr.yml", judge_tar, answer="\n".join(lines) + "\n")
    _no_plan(ran)
    assert "test: not a matrix of entries" in ran.log, ran.log


def test_a_main_whose_tools_ci_cannot_be_fetched_plans_nothing(
    tmp_path: Path, judge_tar: Path
) -> None:
    ran = _run_plan(tmp_path, "merge.yml", judge_tar, fetch_fails=True)
    _no_plan(ran)
    assert ran.argv is None, "a planner ran with no main to take it from"


# ── merge.yml's check of the catalogue (#3909) ───────────────────────────────────────────────


def _run_check(tmp: Path, judge_tar: Path, *, candidate: str | None = None, ref: str) -> Ran:
    bin_, temp, workspace = tmp / "bin", tmp / "runner", tmp / "workspace"
    for folder in (bin_, temp / "judge", workspace / "tools" / "ci"):
        folder.mkdir(parents=True)
    with tarfile.open(judge_tar) as tar:
        tar.extractall(temp / "judge", filter="data")
    own = (REPO / catalogue.CATALOGUE).read_text(encoding="utf-8")
    (workspace / catalogue.CATALOGUE).write_text(candidate or own, encoding="utf-8")
    (bin_ / "python3").write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
    (bin_ / "python3").chmod(0o755)
    env = {
        "PATH": f"{bin_}:/usr/bin:/bin",
        "HOME": str(tmp),
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": ref,
        "GITHUB_WORKSPACE": str(workspace),
        "RUNNER_TEMP": str(temp),
    }
    done = _bash(_step("merge.yml", "changes", "catalogue-check")["run"], tmp, env, workspace)
    return Ran(done.returncode, [], done.stdout + done.stderr, None, None, temp)


def _drop_entry(text: str, job_id: str) -> str:
    head, *entries = text.split("\n[[job]]\n")
    kept = [e for e in entries if not e.startswith(f'id = "{job_id}"\n')]
    assert len(kept) == len(entries) - 1, job_id
    return "\n[[job]]\n".join([head, *kept])


def test_the_check_passes_a_catalogue_main_accepts(tmp_path: Path, judge_tar: Path) -> None:
    ran = _run_check(tmp_path, judge_tar, ref="refs/heads/feature")
    assert ran.rc == 0, ran.log
    assert "catalogue-check: main's catalogue has no objection to this one" in ran.log


def test_the_check_fails_a_catalogue_main_refuses(tmp_path: Path, judge_tar: Path) -> None:
    # A security job main runs, dropped: main's rules refuse it, and the job is red.
    own = (REPO / catalogue.CATALOGUE).read_text(encoding="utf-8")
    ran = _run_check(
        tmp_path, judge_tar, candidate=_drop_entry(own, "security-macos"), ref="refs/heads/x"
    )
    assert ran.rc == 1, ran.log
    assert "::error title=catalogue refused::" in ran.log


def test_a_run_of_main_checks_main_against_itself_no_further(
    tmp_path: Path, judge_tar: Path
) -> None:
    own = (REPO / catalogue.CATALOGUE).read_text(encoding="utf-8")
    ran = _run_check(
        tmp_path, judge_tar, candidate=_drop_entry(own, "security-macos"), ref="refs/heads/main"
    )
    assert ran.rc == 0, ran.log
    assert "the catalogue under test is main's own" in ran.log


def test_the_check_is_the_last_step_of_the_job_the_merge_decision_requires() -> None:
    steps = _workflow("merge.yml")["jobs"][merge_decision.CATALOGUE_CHECK]["steps"]
    ids = [s.get("id") for s in steps]
    assert ids[-1] == "catalogue-check" and ids.index("plan") < ids.index("catalogue-check")


# ── the verdict step ─────────────────────────────────────────────────────────────────────────


def _record(key: str, *, result: str = "success", head: str = _HEAD, attempt: int = 1) -> str:
    return verdict.job_record(
        key=key,
        result=result,
        identity=verdict.Identity(head, {}),
        steps=[(["true"], 0, 0.1)],
        attempt=attempt,
        run_id=int(_RUN_ID),
    )


def _needs(**results: str) -> str:
    return json.dumps({job: {"result": r, "outputs": {}} for job, r in results.items()})


def _run_verdict(
    tmp: Path,
    workflow: str,
    *,
    keys: list[str] | None,
    records: dict[str, str],
    needs: str,
    gh_down: bool = False,
    attempt: str = "1",
    plant: Callable[[Path], None] | None = None,
) -> Ran:
    stub, bin_, temp = _stubs(tmp)
    (stub / "verdict.py").write_bytes((REPO / "tools" / "ci" / "verdict.py").read_bytes())
    if gh_down:
        (stub / "gh-down").write_text("")
    folder = temp / "records"
    folder.mkdir(parents=True)
    for key, text in records.items():
        artifact = folder / verdict.record_artifact(key, run_id=int(_RUN_ID), attempt=1)
        artifact.mkdir(parents=True)
        (artifact / f"{verdict.record_slug(key)}.json").write_text(text, encoding="utf-8")
    if plant is not None:
        plant(folder)
    summary = tmp / "GITHUB_STEP_SUMMARY"
    summary.write_text("")
    step = _verdict_step(workflow)
    env = {
        "PATH": f"{bin_}:/usr/bin:/bin",
        "HOME": str(tmp),
        "STUB": str(stub),
        "GH_TOKEN": "a-token",
        "NEEDS": needs,
        "KEYS": json.dumps(keys) if keys is not None else "",
        "HEAD_SHA": _HEAD,
        "GITHUB_REPOSITORY": _REPO,
        "GITHUB_RUN_ID": _RUN_ID,
        "GITHUB_RUN_ATTEMPT": attempt,
        "GITHUB_STEP_SUMMARY": str(summary),
        "RUNNER_TEMP": str(temp),
    }
    done = _bash(step["run"], tmp, env, tmp)
    log = done.stdout + done.stderr + summary.read_text()
    return Ran(done.returncode, [], log, None, None, temp)


_KEYS = ["static", "rails (1/2)", "rails (2/2)"]
_GREEN_NEEDS = _needs(changes="success", test="success")


@pytest.mark.parametrize("workflow", sorted(_TIERS))
def test_the_verdict_step_reads_the_env_the_harness_gives_it(workflow: str) -> None:
    step = _verdict_step(workflow)
    assert step.get("env") == {
        "GH_TOKEN": "${{ github.token }}",
        "NEEDS": "${{ toJSON(needs) }}",
        "KEYS": "${{ needs.changes.outputs.keys }}",
        "HEAD_SHA": "${{ github.sha }}",
    }
    assert "${{" not in step["run"]
    # The harness runs the step as if its condition held: it is `!cancelled()` alone, so a run
    # whose `changes` published no plan is still judged, red, and nothing after it runs.
    assert step.get("if") == "${{ !cancelled() }}" and "continue-on-error" not in step
    assert _workflow(workflow)["jobs"]["results"]["steps"][-1] == step


@pytest.mark.parametrize("workflow", sorted(_TIERS))
def test_every_planned_record_green_is_green(tmp_path: Path, workflow: str) -> None:
    ran = _run_verdict(
        tmp_path, workflow, keys=_KEYS, records={k: _record(k) for k in _KEYS}, needs=_GREEN_NEEDS
    )
    assert ran.rc == 0, ran.log
    assert "Verdict: green" in ran.log


def test_a_planned_entry_with_no_record_is_red(tmp_path: Path) -> None:
    records = {k: _record(k) for k in _KEYS[:-1]}
    ran = _run_verdict(tmp_path, "merge.yml", keys=_KEYS, records=records, needs=_GREEN_NEEDS)
    assert ran.rc == 1, ran.log
    assert "`rails (2/2)`: missing" in ran.log


def test_a_red_record_is_red(tmp_path: Path) -> None:
    records = {k: _record(k, result="failure" if k == "static" else "success") for k in _KEYS}
    ran = _run_verdict(tmp_path, "merge.yml", keys=_KEYS, records=records, needs=_GREEN_NEEDS)
    assert ran.rc == 1 and "`static`: failure" in ran.log, ran.log


def test_a_record_of_another_head_is_red(tmp_path: Path) -> None:
    records = {k: _record(k, head="b" * 40 if k == "static" else _HEAD) for k in _KEYS}
    ran = _run_verdict(tmp_path, "merge.yml", keys=_KEYS, records=records, needs=_GREEN_NEEDS)
    assert ran.rc == 1 and "`static`: stale head" in ran.log, ran.log


def test_a_failed_job_of_the_run_is_red_beside_green_records(tmp_path: Path) -> None:
    # The catalogue check failing `changes` after it published a plan: the records are all
    # green, and the run is still red.
    needs = _needs(changes="failure", test="success")
    ran = _run_verdict(
        tmp_path, "merge.yml", keys=_KEYS, records={k: _record(k) for k in _KEYS}, needs=needs
    )
    assert ran.rc == 1 and "`changes`: unplanned failure" in ran.log, ran.log


def test_no_plan_is_an_empty_plan_and_red(tmp_path: Path) -> None:
    needs = _needs(changes="failure", test="skipped")
    ran = _run_verdict(tmp_path, "merge.yml", keys=None, records={}, needs=needs)
    assert ran.rc == 1 and "empty plan" in ran.log, ran.log


def test_a_record_of_a_later_attempt_than_the_runs_is_refused(tmp_path: Path) -> None:
    records = {k: _record(k, attempt=2 if k == "static" else 1) for k in _KEYS}
    ran = _run_verdict(tmp_path, "merge.yml", keys=_KEYS, records=records, needs=_GREEN_NEEDS)
    assert ran.rc == 2, ran.log


def test_a_verdict_py_that_cannot_be_read_is_red(tmp_path: Path) -> None:
    ran = _run_verdict(
        tmp_path,
        "merge.yml",
        keys=_KEYS,
        records={k: _record(k) for k in _KEYS},
        needs=_GREEN_NEEDS,
        gh_down=True,
    )
    assert ran.rc == 1 and "::error title=no verdict::" in ran.log, ran.log


def _write_record(folder: Path, name: str, file: str, text: str) -> None:
    (folder / name).mkdir(exist_ok=True)
    (folder / name / file).write_text(text, encoding="utf-8")


_STATIC = verdict.record_artifact("static", run_id=int(_RUN_ID), attempt=1)


@pytest.mark.parametrize(
    "plant",
    [
        pytest.param(lambda f: _write_record(f, _STATIC, "static.json", ""), id="an empty file"),
        pytest.param(lambda f: _write_record(f, _STATIC, "static.json", "{"), id="not JSON"),
        pytest.param(lambda f: _write_record(f, _STATIC, "static.json", "[]"), id="not an object"),
        pytest.param(
            lambda f: _write_record(f, _STATIC, "static.json", '{"key": "static"}'),
            id="fields missing",
        ),
        pytest.param(
            lambda f: _write_record(f, _STATIC, "static.json", _record("rails (1/2)")),
            id="another key's record under static's name",
        ),
        pytest.param(
            lambda f: _write_record(f, _STATIC, "extra.json", _record("static")),
            id="two files in one artifact",
        ),
        pytest.param(lambda f: (f / _STATIC / "static.json").unlink(), id="an empty artifact"),
        pytest.param(
            lambda f: (f / "stray.json").write_text(_record("static")), id="a file beside them"
        ),
    ],
)
def test_a_malformed_record_is_never_green(tmp_path: Path, plant: Callable[[Path], None]) -> None:
    # Every planned record is there and green, then one is spoiled: the reading stops (exit 2)
    # or reads red, and never green.
    ran = _run_verdict(
        tmp_path,
        "merge.yml",
        keys=_KEYS,
        records={k: _record(k) for k in _KEYS},
        needs=_GREEN_NEEDS,
        plant=plant,
    )
    assert ran.rc in (1, 2) and "Verdict: green" not in ran.log, ran.log


# ── the setup action's programs step ─────────────────────────────────────────────────────────

_SETUP = REPO / ".github" / "actions" / "setup" / "action.yml"

#: Each installer logs its argv and, on an install, puts the program on PATH; `sudo` runs its
#: argv. Only shell builtins and absolute paths, since PATH is the stub folder alone.
_INSTALLER = """#!/bin/sh
printf '%s\\n' "${0##*/} $*" >> "$STUB/installs"
[ -f "$STUB/install-fails" ] && exit 100
case "$*" in *install*) for name; do :; done
  [ -f "$STUB/install-puts-nothing" ] && exit 0
  printf '#!/bin/sh\\n' > "$BIN/$name"; /bin/chmod +x "$BIN/$name" ;;
esac
exit 0
"""


def _setup_action() -> dict:
    return yaml.safe_load(_SETUP.read_text(encoding="utf-8"))


def _programs_step() -> dict:
    steps = _setup_action()["runs"]["steps"]
    [step] = [s for s in steps if "PROGRAMS" in (s.get("env") or {})]
    return step


def _run_programs(
    tmp: Path,
    programs: str,
    *,
    runner_os: str = "Linux",
    present: tuple[str, ...] = (),
    install_fails: bool = False,
    install_puts_nothing: bool = False,
) -> tuple[int, list[str], str]:
    """Run the action's programs step as written, under the `shell: bash` GitHub runs it with,
    with PATH the stub folder alone. The working folder holds a file named for each listed
    program, so a name read as a pattern would match one."""
    stub, bin_, cwd = tmp / "stub", tmp / "bin", tmp / "cwd"
    for folder in (stub, bin_, cwd):
        folder.mkdir(parents=True)
    for name in ("apt-get", "brew"):
        (bin_ / name).write_text(_INSTALLER)
        (bin_ / name).chmod(0o755)
    (bin_ / "sudo").write_text('#!/bin/sh\nexec "$@"\n')
    (bin_ / "sudo").chmod(0o755)
    for name in present:
        (bin_ / name).write_text("#!/bin/sh\n")
        (bin_ / name).chmod(0o755)
    for name in planner.SETUP_PROGRAMS:
        (cwd / name).write_text("")
    if install_fails:
        (stub / "install-fails").write_text("")
    if install_puts_nothing:
        (stub / "install-puts-nothing").write_text("")
    step = _programs_step()
    script = tmp / "step.sh"
    script.write_text(step["run"], encoding="utf-8")
    env = {
        "PATH": str(bin_),
        "HOME": str(tmp),
        "STUB": str(stub),
        "BIN": str(bin_),
        "PROGRAMS": programs,
        "RUNNER_OS": runner_os,
    }
    bash = shutil.which("bash") or "/bin/bash"
    done = subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    log = stub / "installs"
    installs = log.read_text().splitlines() if log.exists() else []
    return done.returncode, installs, done.stdout + done.stderr


def test_the_programs_step_is_off_by_default_so_no_other_call_changes() -> None:
    action = _setup_action()
    assert action["inputs"]["programs"]["default"] == ""
    step = _programs_step()
    assert step["if"] == "inputs.programs != ''"
    assert step["shell"] == "bash"
    assert step["env"] == {"PROGRAMS": "${{ inputs.programs }}"}
    assert "${{" not in step["run"], "an expression pasted into the shell"
    # Only the tier workflows' stage jobs name programs; every other call leaves the default.
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        for job_id, job in (yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]).items():
            for s in job.get("steps") or []:
                if s.get("uses") != "./.github/actions/setup":
                    continue
                given = (s.get("with") or {}).get("programs")
                if path.name in _TIERS:
                    assert given == "${{ join(matrix.programs, ' ') }}", (path.name, job_id)
                else:
                    assert given is None, (path.name, job_id)


@pytest.mark.parametrize(
    ("runner_os", "want"),
    [
        (
            "Linux",
            ["apt-get update -qq"]
            + [f"apt-get install -y --no-install-recommends {p}" for p in planner.SETUP_PROGRAMS],
        ),
        ("macOS", [f"brew install {p}" for p in planner.SETUP_PROGRAMS]),
    ],
)
def test_each_listed_program_the_path_lacks_comes_from_the_runners_package_manager(
    tmp_path: Path, runner_os: str, want: list[str]
) -> None:
    rc, installs, log = _run_programs(
        tmp_path, " ".join(planner.SETUP_PROGRAMS), runner_os=runner_os
    )
    assert rc == 0, log
    assert installs == want


def test_a_program_on_path_is_not_installed(tmp_path: Path) -> None:
    rc, installs, log = _run_programs(tmp_path, "tmux dash", present=("tmux",))
    assert rc == 0, log
    assert installs == ["apt-get update -qq", "apt-get install -y --no-install-recommends dash"]
    rc, installs, log = _run_programs(tmp_path / "all", "tmux dash", present=("tmux", "dash"))
    assert (rc, installs) == (0, []), log


@pytest.mark.parametrize(
    "programs",
    [
        "curl",
        "tmux curl",
        "tmux dash bash",
        "TMUX",
        "tmux;id",
        "tmux $(id)",
        "tmux `id`",
        "tmux|id",
        "tmux&&id",
        "t?ux",
        "tmu*",
        "[t]mux",
        "../tmux",
        "tmux -y",
        "--reinstall",
    ],
)
def test_a_name_outside_the_list_fails_before_any_install(tmp_path: Path, programs: str) -> None:
    rc, installs, log = _run_programs(tmp_path, programs)
    assert rc != 0, log
    assert installs == [], f"installed before the refusal: {installs}"
    assert "the setup action installs tmux, shellcheck and dash, not" in log, log


def test_a_runner_with_no_known_installer_installs_nothing(tmp_path: Path) -> None:
    rc, installs, log = _run_programs(tmp_path, "tmux", runner_os="Windows")
    assert rc != 0 and installs == [], log


@pytest.mark.parametrize("how", ["install_fails", "install_puts_nothing"])
def test_a_program_still_missing_after_its_install_fails_the_step(tmp_path: Path, how: str) -> None:
    rc, _installs, log = _run_programs(tmp_path, "shellcheck", **{how: True})
    assert rc != 0, log
