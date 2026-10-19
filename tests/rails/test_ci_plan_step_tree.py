"""The ``changes`` job's plan step, run as written (#3945).

The step's ``run:`` is read out of ``.github/workflows/ci.yml`` and run under ``bash -e``, as
GitHub runs a step with no ``shell:``, with ``gh``, ``git`` and the planner stubbed: ``gh`` lists
the changed names as its ``--jq`` prints them, ``git`` hands over the planner as main has it
(this checkout's ``tools/ci``, archived), and the planner either answers with a planted text or
is the real one. Each row reads what the step published to ``$GITHUB_OUTPUT`` and what the two
gated jobs' conditions make of it.

What the rows hold: only ``full=true|false`` and a JSON list of job ids are ever published; an
answer that is not a plan (a crash, an exit 2, no answer, a line missing or doubled, a list that
is not a list of ids, a stray character) publishes ``full=true`` and runs both gated jobs; the
planner is main's, run from a folder of its own over this checkout's catalogue and never from
the checkout; a name reaches it whole, a newline in it included; the ``ci:full`` label plans the
whole tier; and the planner's warnings reach the log, and nothing else it prints a workflow
command.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from rails_repo import REPO

_CI = REPO / ".github" / "workflows" / "ci.yml"
_REPO = "o/r"
_BEFORE = "b" * 40
_HEAD = "a" * 40

#: The step's env, each key with the expression it must read. A key the step adds, or an
#: expression it changes, fails the row that pins this table, so the harness never runs the
#: step with a value GitHub would not give it.
_ENV = {
    "GH_TOKEN": "${{ github.token }}",
    "PR_NUMBER": "${{ github.event.pull_request.number }}",
    "HEAD_SHA": "${{ github.sha }}",
    "BEFORE": "${{ github.event.before }}",
    "FULL_LABEL": "${{ contains(github.event.pull_request.labels.*.name, 'ci:full') }}",
}

#: The clause each gated job's condition ends in, as :func:`_gate` reads it.
_GATE = (
    "needs.changes.outputs.full != 'false' || "
    "contains(fromJSON(needs.changes.outputs.jobs || '[]'), '{job}')"
)
_GATED = ("e2e", "apps-contract")


def _ci() -> dict:
    return yaml.safe_load(_CI.read_text(encoding="utf-8"))


def _step() -> dict:
    steps = _ci()["jobs"]["changes"]["steps"]
    return next(s for s in steps if s.get("id") == "plan")


def _gate(published: dict[str, str], job: str) -> bool:
    """A gated job's condition as GitHub reads it: an unwritten output is the empty string."""
    if published.get("full", "") != "false":
        return True
    return job in json.loads(published.get("jobs") or "[]")


# ── the harness ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def judge_tar(tmp_path_factory) -> Path:
    """This checkout's tools/ci as ``git archive`` hands it over, the stand-in for the base's."""
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


#: gh: the enumeration's names as its ``--jq`` prints them, a JSON string a line under
#: ``@json`` and the bare names otherwise; every other call (the rails floor's) fails.
_GH = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB/gh.log"
listing=""
jq=""
previous=""
for a in "$@"; do
  case "$a" in
    */pulls/*/files|*/compare/*) listing=yes ;;
  esac
  [ "$previous" = "--jq" ] && jq="$a"
  previous="$a"
done
[ -n "$listing" ] || exit 1
[ -f "$STUB/gh-down" ] && exit 1
case "$jq" in
  *'| @json') cat "$STUB/changed" ;;
  *) cat "$STUB/changed.raw" ;;
esac
"""

_GIT = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB/git.log"
case "$1" in
  fetch) [ -f "$STUB/fetch-fails" ] && exit 128; exit 0 ;;
  archive) [ -f "$STUB/judge.tar" ] || exit 128; cat "$STUB/judge.tar"; exit 0 ;;
esac
exit 1
"""

_PYTHON = """#!/bin/sh
if [ "$1" = "-m" ] && [ "$2" = "tools.ci" ]; then
  pwd > "$STUB/planner.cwd"
  for a in "$@"; do printf '%s\\n' "$a"; done > "$STUB/planner.argv"
  if [ -f "$STUB/answer" ]; then
    cat "$STUB/answer"
    [ -f "$STUB/err" ] && cat "$STUB/err" >&2
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
    summary: str
    argv: list[str] | None
    cwd: str | None
    git: list[str]
    runner_temp: Path
    workspace: Path

    @property
    def published(self) -> dict[str, str]:
        return dict(line.partition("=")[::2] for line in self.lines)


def _run(
    tmp: Path,
    *,
    answer: str | None = None,
    rc: int = 0,
    err: str = "",
    changed: tuple[str, ...] = ("docs/a.md",),
    listing: str | None = None,
    event: str = "pull_request",
    before: str = _BEFORE,
    label: bool = False,
    judge: Path | None = None,
    gh_down: bool = False,
    fetch_fails: bool = False,
    checkout: Path | None = None,
) -> Ran:
    """Run the step once. ``gh`` lists the ``changed`` names as ``--jq '... | @json'`` prints
    them, or ``listing`` as it is. ``answer`` plants the planner's stdout (and ``rc``, ``err``);
    without one, the real planner runs from ``judge``, over ``checkout`` (this checkout when
    none is given) as the workspace."""
    stub, bin_, temp = tmp / "stub", tmp / "bin", tmp / "runner"
    for folder in (stub, bin_, temp):
        folder.mkdir()
    workspace = checkout if checkout is not None else REPO
    if answer is not None:
        workspace = tmp / "workspace"
        workspace.mkdir()
        (stub / "answer").write_text(answer, encoding="utf-8", newline="")
        (stub / "rc").write_text(str(rc))
        (stub / "err").write_text(err, encoding="utf-8")
        tar = io.BytesIO()
        with tarfile.open(fileobj=tar, mode="w") as t:
            info = tarfile.TarInfo("tools/ci/__main__.py")
            info.size = 0
            t.addfile(info, io.BytesIO(b""))
        (stub / "judge.tar").write_bytes(tar.getvalue())
    elif judge is not None:
        shutil.copy(judge, stub / "judge.tar")
    if listing is None:
        listing = "".join(json.dumps(name) + "\n" for name in changed)
    (stub / "changed").write_text(listing, encoding="utf-8", newline="")
    raw = "".join(name + "\n" for name in changed)
    (stub / "changed.raw").write_text(raw, encoding="utf-8", newline="")
    if gh_down:
        (stub / "gh-down").write_text("")
    if fetch_fails:
        (stub / "fetch-fails").write_text("")
    for name, body in (
        ("gh", _GH),
        ("git", _GIT),
        ("python3", _PYTHON.format(python=sys.executable)),
    ):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    output, summary = tmp / "GITHUB_OUTPUT", tmp / "GITHUB_STEP_SUMMARY"
    output.write_text("")
    summary.write_text("")
    script = tmp / "step.sh"
    script.write_text(_step()["run"], encoding="utf-8")
    pr = event == "pull_request"
    env = {
        "PATH": f"{bin_}:/usr/bin:/bin",
        "HOME": str(tmp),
        "STUB": str(stub),
        "GH_TOKEN": "a-token",
        "PR_NUMBER": "7" if pr else "",
        "HEAD_SHA": _HEAD,
        "BEFORE": "" if pr else before,
        "FULL_LABEL": "true" if label else "false",
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF": "refs/pull/7/merge" if pr else "refs/heads/main",
        "GITHUB_REPOSITORY": _REPO,
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
        "RUNNER_TEMP": str(temp),
    }
    bash = shutil.which("bash") or "/bin/bash"
    done = subprocess.run(
        [bash, "-e", str(script)],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    argv_file, cwd_file, git_log = stub / "planner.argv", stub / "planner.cwd", stub / "git.log"
    return Ran(
        rc=done.returncode,
        lines=output.read_text(encoding="utf-8").split("\n")[:-1],
        log=done.stdout + done.stderr,
        summary=summary.read_text(encoding="utf-8"),
        argv=argv_file.read_text().splitlines() if argv_file.exists() else None,
        cwd=cwd_file.read_text().strip() if cwd_file.exists() else None,
        git=git_log.read_text().splitlines() if git_log.exists() else [],
        runner_temp=temp,
        workspace=workspace,
    )


def _whole_tier(ran: Ran) -> None:
    assert ran.rc == 0, ran.log
    assert ran.lines == ["full=true", "jobs=[]"], (ran.lines, ran.log)
    assert all(_gate(ran.published, job) for job in _GATED)


def _commands_only_from(ran: Ran, planner_warnings: list[str]) -> None:
    """Every workflow command in the log is the step's own warning or one the planner wrote."""
    for line in ran.log.splitlines():
        if line.startswith("::"):
            assert line.startswith("::warning title=plan inconclusive::") or line in (
                planner_warnings
            ), line


# ── the wiring the harness relies on ─────────────────────────────────────────────────────────


def test_the_step_reads_only_the_env_the_harness_gives_it() -> None:
    step = _step()
    assert step.get("env") == _ENV
    ci = _ci()
    shells = [step.get("shell"), ci.get("defaults"), ci["jobs"]["changes"].get("defaults")]
    assert shells == [None, None, None], "the harness runs the step as GitHub's default bash -e"
    assert "${{" not in step["run"], "an expression pasted into the shell"


def test_the_changes_job_holds_only_read_permissions() -> None:
    changes = _ci()["jobs"]["changes"]
    assert changes["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "actions": "read",
    }
    assert changes["outputs"] == {
        "full": "${{ steps.plan.outputs.full }}",
        "jobs": "${{ steps.plan.outputs.jobs }}",
    }


@pytest.mark.parametrize("job", _GATED)
def test_each_gated_job_reads_the_outputs_as_the_harness_does(job: str) -> None:
    """Each survives a failed `changes` job (`!cancelled()`, so an unwritten output runs it) and
    ends in the clause :func:`_gate` models."""
    condition = " ".join(str(_ci()["jobs"][job].get("if") or "").split())
    assert condition.startswith("${{ !cancelled() && "), condition
    assert condition.endswith("(" + _GATE.format(job=job) + ") }}"), condition


# ── planted answers ──────────────────────────────────────────────────────────────────────────

_LIST = '["rails","e2e"]'

#: (id, the planner's stdout, its exit code): each a plan the step publishes as it is.
_PLANS = [
    ("a plan", f"full=false\njobs={_LIST}\n", 0, "false", _LIST),
    ("the whole tier", 'full=true\njobs=["rails"]\n', 0, "true", '["rails"]'),
    ("an empty list", "full=false\njobs=[]\n", 0, "false", "[]"),
    ("its lines swapped", f"jobs={_LIST}\nfull=false\n", 0, "false", _LIST),
    (
        "stray lines around it",
        f"::notice::x\nfull=false\njobs={_LIST}\nfoo=bar\n",
        0,
        "false",
        _LIST,
    ),
    ("no final newline", f"full=false\njobs={_LIST}", 0, "false", _LIST),
]

#: (id, the planner's stdout, its exit code): each not a plan, so the whole tier runs.
_NOT_PLANS = [
    ("exit 2, nothing printed", "", 2),
    ("a crash after full", "full=false\n", 1),
    ("no jobs line, a stray command line", "full=false\n::notice::x\n", 0),
    ("a crash after both lines", f"full=false\njobs={_LIST}\n", 1),
    ("no jobs line", "full=false\n", 0),
    ("no full line", f"jobs={_LIST}\n", 0),
    ("nothing printed", "", 0),
    ("jobs not JSON", "full=false\njobs=e2e\n", 0),
    ("jobs an object", 'full=false\njobs={"e2e":1}\n', 0),
    ("jobs numbers", "full=false\njobs=[1,2]\n", 0),
    ("jobs a string", 'full=false\njobs="e2e"\n', 0),
    ("jobs null", "full=false\njobs=null\n", 0),
    ("jobs nested", 'full=false\njobs=[["e2e"]]\n', 0),
    ("jobs not as the planner prints them", 'full=false\njobs=["rails", "e2e"]\n', 0),
    ("a job id that is not one", 'full=false\njobs=["e2e","Not An Id"]\n', 0),
    ("jobs with a carriage return", f"full=false\njobs={_LIST}\r\n", 0),
    ("full in capitals", f"full=FALSE\njobs={_LIST}\n", 0),
    ("full the Python word", f"full=False\njobs={_LIST}\n", 0),
    ("full with a trailing space", f"full=false \njobs={_LIST}\n", 0),
    ("full with a carriage return", f"full=false\r\njobs={_LIST}\n", 0),
    ("full twice", f"full=false\nfull=false\njobs={_LIST}\n", 0),
    ("full twice, differing", f"full=true\nfull=false\njobs={_LIST}\n", 0),
    ("jobs twice", f"full=false\njobs={_LIST}\njobs={_LIST}\n", 0),
    ("full another word", f"full=no\njobs={_LIST}\n", 0),
    ("full empty", f"full=\njobs={_LIST}\n", 0),
]


@pytest.mark.parametrize(
    ("answer", "rc", "full", "jobs"),
    [row[1:] for row in _PLANS],
    ids=[row[0] for row in _PLANS],
)
def test_a_plan_is_published_as_it_is(tmp_path, answer, rc, full, jobs) -> None:
    ran = _run(tmp_path, answer=answer, rc=rc)
    assert ran.rc == 0, ran.log
    assert ran.lines == [f"full={full}", f"jobs={jobs}"], ran.lines
    for job in _GATED:
        assert _gate(ran.published, job) is (full == "true" or job in json.loads(jobs))
    _commands_only_from(ran, [])


@pytest.mark.parametrize(
    ("answer", "rc"), [row[1:] for row in _NOT_PLANS], ids=[row[0] for row in _NOT_PLANS]
)
def test_an_answer_that_is_not_a_plan_runs_the_whole_tier(tmp_path, answer, rc) -> None:
    ran = _run(tmp_path, answer=answer, rc=rc)
    _whole_tier(ran)
    assert "::warning title=plan inconclusive::" in ran.log
    _commands_only_from(ran, [])


def test_the_planner_is_the_base_commits_run_beside_this_checkouts_catalogue(tmp_path) -> None:
    ran = _run(tmp_path, answer=f"full=false\njobs={_LIST}\n")
    assert ran.cwd is not None and Path(ran.cwd).resolve() == (ran.runner_temp / "judge").resolve()
    argv = ran.argv or []
    assert argv[:4] == ["-m", "tools.ci", "plan", "merge"], argv

    def value(flag: str) -> str:
        assert argv.count(flag) == 1, (flag, argv)
        return argv[argv.index(flag) + 1]

    assert value("--catalogue") == f"{ran.workspace}/tools/ci/catalogue.toml"
    assert value("--trusted-areas") == f"{ran.runner_temp}/judge/tools/ci/areas.toml"
    assert value("--root") == str(ran.workspace)
    assert value("--paths") == f"{ran.runner_temp}/changed.txt" and "-z" in argv
    listed = (ran.runner_temp / "changed.txt").read_bytes()
    assert listed == b"docs/a.md\0", listed
    assert value("--repo") == _REPO
    assert "--outputs" in argv and "--full" not in argv
    assert ran.git == [
        "fetch --no-tags --depth=1 origin refs/heads/main",
        "archive FETCH_HEAD tools/ci",
    ]


def test_a_push_to_main_is_judged_by_the_commit_it_replaced(tmp_path) -> None:
    ran = _run(tmp_path, answer=f"full=false\njobs={_LIST}\n", event="push")
    assert ran.lines == ["full=false", f"jobs={_LIST}"]
    assert ran.git == [f"fetch --no-tags --depth=1 origin {_BEFORE}", "archive FETCH_HEAD tools/ci"]


@pytest.mark.parametrize("before", ["", "0" * 40], ids=["no before", "a new branch"])
def test_a_push_with_no_commit_it_replaced_has_no_judge_and_runs_the_whole_tier(
    tmp_path, before: str
) -> None:
    ran = _run(tmp_path, answer=f"full=false\njobs={_LIST}\n", event="push", before=before)
    _whole_tier(ran)
    assert ran.argv is None, "a planner ran with no base commit to take it from"


def test_a_judge_that_cannot_be_fetched_runs_the_whole_tier(tmp_path) -> None:
    ran = _run(tmp_path, answer=f"full=false\njobs={_LIST}\n", fetch_fails=True)
    _whole_tier(ran)
    assert ran.argv is None


def test_the_ci_full_label_asks_the_planner_for_the_whole_tier(tmp_path) -> None:
    ran = _run(tmp_path, answer='full=true\njobs=["rails"]\n', label=True)
    assert ran.argv is not None and ran.argv.count("--full") == 1, ran.argv


def test_the_planners_warnings_reach_the_log_and_its_plan_the_summary(tmp_path) -> None:
    warning = "::warning title=rails floor not read::TimeoutError: out of time"
    err = '{\n "full": false\n}\n' + warning + "\nan ordinary line\n"
    ran = _run(tmp_path, answer=f"full=false\njobs={_LIST}\n", err=err)
    assert warning in ran.log.splitlines()
    assert '"full": false' in ran.summary and warning in ran.summary
    assert "an ordinary line" not in ran.log
    _commands_only_from(ran, [warning])


# ── the real planner, from the archived judge ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changed", "full", "e2e", "apps"),
    [
        (("docs/guides/x.md",), "false", False, False),
        (("src/personalclaw/sdk/x.py",), "false", True, True),
        (("tests/test_x.py",), "false", False, False),
        (("docs/guides/x.md", "tests/test_x.py"), "false", False, False),
        (("src/personalclaw/x.py",), "false", True, False),
        (("flake.nix",), "true", True, True),
        ((".github/workflows/ci.yml",), "true", True, True),
        ((), "true", True, True),
        (('docs/it\'s "quoted".md',), "false", False, False),
        (("docs/${{ github.token }}.md",), "false", False, False),
        (("docs/two\nlines.md",), "true", True, True),
        (("x.md\ndocs/y.md",), "true", True, True),
        (("docs/a\rb.md",), "true", True, True),
    ],
    ids=[
        "docs only",
        "an SDK file",
        "a test only",
        "docs and a test",
        "a backend file",
        "a path only the whole tree names",
        "the workflow",
        "an empty enumeration",
        "quotes in a name",
        "an expression in a name",
        "a newline in a name",
        "a newline between two known names",
        "a carriage return in a name",
    ],
)
def test_the_real_planner_decides_the_gated_jobs(
    tmp_path, judge_tar, changed: tuple[str, ...], full: str, e2e: bool, apps: bool
) -> None:
    """A name with a newline in it reaches the planner whole, never as two names it would read
    as two known paths, so it plans the whole tier."""
    ran = _run(tmp_path, changed=changed, judge=judge_tar)
    assert ran.rc == 0, ran.log
    assert [line.partition("=")[0] for line in ran.lines] == ["full", "jobs"], ran.lines
    assert ran.published["full"] == full, ran.log
    assert (_gate(ran.published, "e2e"), _gate(ran.published, "apps-contract")) == (e2e, apps)
    floor = [line for line in ran.log.splitlines() if line.startswith("::warning ")]
    _commands_only_from(ran, floor)


@pytest.mark.parametrize(
    "listing",
    ["docs/a.md\n", '"docs/a.md"\n\n', '"docs/a.md"\n["x"]\n', '"docs/a\\u0000b.md"\n'],
    ids=["a bare name", "a blank line", "a line not a string", "a NUL in a name"],
)
def test_a_listing_it_cannot_read_runs_the_whole_tier(tmp_path, judge_tar, listing: str) -> None:
    ran = _run(tmp_path, listing=listing, judge=judge_tar)
    assert ran.rc == 0 and ran.published["full"] == "true", ran.log
    assert (ran.runner_temp / "changed.txt").read_bytes() == b""


def test_the_real_planner_with_the_label_plans_the_whole_tier(tmp_path, judge_tar) -> None:
    ran = _run(tmp_path, changed=("docs/guides/x.md",), judge=judge_tar, label=True)
    assert ran.published["full"] == "true", ran.log


def test_an_enumeration_that_fails_runs_the_whole_tier(tmp_path, judge_tar) -> None:
    ran = _run(tmp_path, judge=judge_tar, gh_down=True)
    assert ran.rc == 0 and ran.published["full"] == "true", ran.log
    assert all(_gate(ran.published, job) for job in _GATED)


#: The whole-tier overrides a planted pull request drops, each by the line that opens its
#: ``paths``, with a path under it the step is handed.
_DROPPED_OVERRIDES = {
    "the workflows'": ('paths = [".github/workflows/"]', ".github/workflows/ci.yml"),
    "tools/ci's": ('paths = ["tools/ci/"]', "tools/ci/plan.py"),
    "deploy/docker's": ('paths = [\n  "deploy/docker/",', "deploy/docker/Dockerfile.backend"),
}


def _without_override(text: str, opening: str) -> str:
    """The areas file with the one ``[[override]]`` whose paths open with ``opening`` removed."""
    blocks = text.split("[[override]]\n")
    kept = [b for b in blocks if not b.startswith(opening)]
    assert len(kept) == len(blocks) - 1, f"no override opens with {opening!r}"
    return "[[override]]\n".join(kept)


@pytest.mark.parametrize("dropped", sorted(_DROPPED_OVERRIDES))
def test_a_pull_request_that_drops_a_whole_tier_override_still_runs_the_whole_tier(
    tmp_path, judge_tar, dropped: str
) -> None:
    """The planted pull request: this tree's catalogue, and its areas file without one of
    main's whole-tier overrides. Its own files alone plan the path narrowly, so the plant takes
    effect; through the step, main's areas file, which the judge carries, plans the whole
    tier."""
    from tools.ci import catalogue
    from tools.ci import plan as planner

    opening, path = _DROPPED_OVERRIDES[dropped]
    checkout = tmp_path / "checkout"
    (checkout / "tools" / "ci").mkdir(parents=True)
    for name in ("catalogue.toml", "areas.toml"):
        text = (REPO / "tools" / "ci" / name).read_text(encoding="utf-8")
        if name == "areas.toml":
            text = _without_override(text, opening)
        (checkout / "tools" / "ci" / name).write_text(text, encoding="utf-8")
    cat = catalogue.read(checkout)
    alone = planner.plan(cat, "merge", [path], overrides=catalogue.read_areas(checkout, cat))
    assert not alone.full, f"the plant took no effect: {path} is whole-tier on its own"
    ran = _run(tmp_path, changed=(path,), judge=judge_tar, checkout=checkout)
    assert ran.rc == 0, ran.log
    assert ran.published["full"] == "true", (ran.lines, ran.summary)
    assert "main's tools/ci/areas.toml" in ran.summary, ran.summary


def test_a_judge_with_no_planner_runs_the_whole_tier(tmp_path) -> None:
    """A base commit older than the planner archives no tools/ci: git archive fails."""
    ran = _run(tmp_path, judge=None)
    _whole_tier(ran)
