"""An Electron job off GitHub on a host whose kernel withholds Chromium's user namespaces cannot run
here, and a run that is short only of jobs this host cannot run is incomplete, never green or red.

``tools/ci/sandbox_host.py`` reads Ubuntu's AppArmor rule, and ``jobs.cannot_run_here`` asks it
for each job whose toolchain names ``electron``, off GitHub only. On GitHub nothing is read and the
job runs as it always did. A tier run's summary says ``### Verdict: incomplete`` and its last line
``incomplete: N job(s) could not run here`` only when every job that is not green is one this host
cannot run, each with its cause; a missing record or a red job keeps today's red.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from test_ci_admission import _Machine
from test_ci_jobs import (  # noqa: F401 - the job tests' autouse fixtures, by name
    _git,
    _host,
    _job,
    _run,
    _the_runner_s_setup_uv_names_only_what_a_test_plants,
    _the_user_database_names_a_planted_home,
    _tier,
)

from tools.ci import jobs, run, sandbox_host, verdict


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The job tests' planted repository, as ``test_ci_jobs.repo`` plants it."""
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / ".gitignore").write_text("reports/\n/.local/\n")
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (root / "tests" / "test_a.py").write_text("def test_it():\n    pass\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "planted")
    return root


#: What the planted host's probe says: the cause and the fix, as sandbox_host words them.
_WITHHELD = "this host withholds the user namespaces Chromium's sandbox needs (planted)"


def _withholds(root: Path) -> str:
    return _WITHHELD


def _lets(root: Path) -> None:
    return None


def _never(root: Path) -> str:
    raise AssertionError("the sandbox check ran on GitHub")


def _electron_job(job_id: str, *programs: str, **kw):
    return _job(job_id, *programs, toolchain=("electron",), **kw)


def _with_installer(repo: Path, tmp_path: Path, code: int = 0) -> Path:
    """The planted repository's own ``install_electron.sh``, committed: it notes that it ran in a
    marker outside the repository and exits *code*, as the real one fails on a host that cannot
    start Electron."""
    marker = tmp_path / "installed"
    script = repo / "tools" / "ci" / "install_electron.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f"#!/bin/sh\n: > {str(marker)!r}\nexit {code}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the installer")
    return marker


# ── the host's rule ──────────────────────────────────────────────────────────────────────────


def test_the_rule_on_names_the_cause_the_sysctl_fix_and_the_by_hand_fix_with_its_limit(tmp_path):
    said = sandbox_host.problem(tmp_path, platform="linux", read=lambda: "1")
    assert said is not None
    assert "kernel.apparmor_restrict_unprivileged_userns = 1" in said
    assert "sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0" in said
    assert (
        "echo 'kernel.apparmor_restrict_unprivileged_userns = 0' | sudo tee"
        " /etc/sysctl.d/60-apparmor-userns.conf"
    ) in said
    helper = tmp_path / "node_modules" / "electron" / "dist" / "chrome-sandbox"
    assert f"sudo chown root:root {helper}; sudo chmod 4755 {helper}" in said
    # The by-hand fix says it does not hold for a run: the job's own installs replace the helper.
    assert said.index("sudo chown") > said.index("sudo sysctl")
    assert "make gate's own installs replace that helper" in said


@pytest.mark.parametrize(
    ("platform", "value"),
    [("linux", None), ("linux", "0"), ("darwin", "1")],
    ids=["no-rule", "rule-off", "not-linux"],
)
def test_a_host_without_the_rule_on_can_run_it(tmp_path, platform, value):
    assert sandbox_host.problem(tmp_path, platform=platform, read=lambda: value) is None


def test_a_user_owned_helper_with_the_rule_off_still_can_run(tmp_path):
    """The helper's owner and mode are not read: with the rule off Chromium sandboxes itself in
    user namespaces, and an unprivileged helper is never used."""
    helper = tmp_path / "node_modules" / "electron" / "dist" / "chrome-sandbox"
    helper.parent.mkdir(parents=True)
    helper.write_text("")
    helper.chmod(0o755)
    assert helper.stat().st_uid == os.getuid() and not helper.stat().st_mode & stat.S_ISUID
    assert sandbox_host.problem(tmp_path, platform="linux", read=lambda: "0") is None
    host = _host(
        tmp_path,
        sandbox=lambda root: sandbox_host.problem(root, platform="linux", read=lambda: "0"),
    )
    assert jobs.cannot_run_here(_electron_job("web-unit", "pass"), host) == []


def test_the_rule_is_read_from_the_kernel_s_file_and_a_kernel_without_it_reads_none(tmp_path):
    shown = tmp_path / "apparmor_restrict_unprivileged_userns"
    shown.write_text("1\n")
    assert sandbox_host.read_rule(shown) == "1"
    assert sandbox_host.read_rule(tmp_path / "absent") is None


def test_a_planted_host_s_rule_is_read_from_the_file_its_variable_names(tmp_path, monkeypatch):
    """A test that runs run-job in a process of its own plants the host there: the variable names
    the file read in the kernel's place."""
    planted = tmp_path / "rule"
    planted.write_text("0\n")
    monkeypatch.setenv(sandbox_host.RULE_FILE_ENV, str(planted))
    assert sandbox_host.read_rule() == "0"
    assert sandbox_host.problem(tmp_path, platform="linux") is None
    planted.write_text("1\n")
    assert sandbox_host.problem(tmp_path, platform="linux") is not None
    monkeypatch.delenv(sandbox_host.RULE_FILE_ENV)
    assert sandbox_host.read_rule(tmp_path / "absent") is None


def test_a_rule_that_cannot_be_read_reads_as_none_and_the_job_is_not_stopped(tmp_path, monkeypatch):
    """A ``/proc`` that refuses the read is no crash: the rule reads as none, so the job runs
    and fails as it always did, never green."""

    def refused(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "read_text", refused)
    assert sandbox_host.read_rule(tmp_path / "apparmor_restrict_unprivileged_userns") is None
    assert sandbox_host.problem(tmp_path, platform="linux") is None


# ── a job ────────────────────────────────────────────────────────────────────────────────────


def test_an_electron_job_on_a_host_that_withholds_the_namespaces_cannot_run_here(repo, tmp_path):
    marker = _with_installer(repo, tmp_path)
    ran = tmp_path / "ran"
    job = _electron_job("web-unit", f"open({str(ran)!r}, 'w')")
    outcome = _run(repo, job, tmp_path, host=_host(repo, sandbox=_withholds))
    assert outcome.code == run.PARTIAL and not outcome.ran and outcome.not_here
    assert outcome.record is None
    assert outcome.reasons == [f"cannot run here: {_WITHHELD}"]
    assert not marker.exists() and not ran.exists(), "nothing of the job ran, its installs neither"


def test_a_job_without_electron_is_not_asked_about_the_sandbox(repo, tmp_path):
    outcome = _run(repo, _job("static", "pass"), tmp_path, host=_host(repo, sandbox=_never))
    assert outcome.code == run.OK and outcome.ran and not outcome.not_here, outcome.reasons


def test_an_electron_job_on_a_host_that_can_run_it_runs_as_before(repo, tmp_path):
    marker = _with_installer(repo, tmp_path)
    ran = tmp_path / "ran"
    job = _electron_job("web-unit", f"open({str(ran)!r}, 'w')")
    outcome = _run(repo, job, tmp_path, host=_host(repo, sandbox=_lets))
    assert outcome.code == run.OK and outcome.ran and not outcome.not_here, outcome.reasons
    assert marker.exists() and ran.exists()
    assert outcome.record is not None


def test_on_github_the_check_never_runs_and_the_job_fails_as_it_always_did(
    repo, tmp_path, monkeypatch
):
    """GitHub runs the job, and its own installer's failure is the job's red, as before."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "1234")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    marker = _with_installer(repo, tmp_path, code=1)
    host = _host(repo, on_github=True, sandbox=_never)
    assert jobs.cannot_run_here(_electron_job("web-unit", "pass"), host) == []
    outcome = _run(repo, _electron_job("web-unit", "pass"), tmp_path, host=host)
    assert marker.exists(), "its installer ran"
    assert outcome.code == run.RED and outcome.ran and not outcome.not_here
    assert outcome.reasons == ["step 1: the job exited 1"]


def test_this_host_reads_the_rule_off_github_and_never_on_it(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert jobs.Host.this(tmp_path).sandbox is None
    monkeypatch.delenv("GITHUB_ACTIONS")
    assert jobs.Host.this(tmp_path).sandbox is sandbox_host.problem


# ── a tier ───────────────────────────────────────────────────────────────────────────────────


def _summary_and_last(code, outcomes, judged) -> tuple[str, str]:
    return jobs._summary(judged, outcomes), jobs._last_line(code, outcomes, judged)


def test_a_tier_short_only_of_jobs_this_host_cannot_run_is_incomplete(repo, tmp_path):
    host = _host(repo, sandbox=_withholds)
    code, outcomes, judged = _tier(
        repo, tmp_path, _job("static", "pass"), _electron_job("web-unit", "pass"), host=host
    )
    assert code == run.PARTIAL and not judged.green
    summary, last = _summary_and_last(code, outcomes, judged)
    assert summary.startswith("### Verdict: incomplete\n"), summary
    assert f"- `web-unit`: {_WITHHELD}" in summary
    assert "Verdict: red" not in summary and "Verdict: green" not in summary
    assert last == "incomplete: 1 job could not run here"


def test_an_incomplete_tier_with_a_missing_record_is_red(repo, tmp_path):
    """A job that ran and left no record (it dirtied the checkout) is missing, not one this host
    cannot run: the summary stays today's red, naming it missing."""
    host = _host(repo, sandbox=_withholds)
    dirties = _job("dirty", f"open({str(repo / 'left-behind')!r}, 'w')")
    code, outcomes, judged = _tier(
        repo, tmp_path, dirties, _electron_job("web-unit", "pass"), host=host
    )
    assert code == run.PARTIAL and not judged.green
    summary, last = _summary_and_last(code, outcomes, judged)
    assert summary.startswith("### Verdict: red\n"), summary
    assert "- `dirty`: missing" in summary and "- `web-unit`: missing" in summary
    assert last == "partial"


def test_an_incomplete_tier_with_a_red_job_is_red(repo, tmp_path):
    host = _host(repo, sandbox=_withholds)
    code, outcomes, judged = _tier(
        repo,
        tmp_path,
        _job("bad", "raise SystemExit(1)"),
        _electron_job("web-unit", "pass"),
        host=host,
    )
    assert code == run.RED and not judged.green
    summary, last = _summary_and_last(code, outcomes, judged)
    assert summary.startswith("### Verdict: red\n"), summary
    assert last == "red"


def test_a_job_of_another_platform_is_one_this_host_cannot_run_too(repo, tmp_path):
    code, outcomes, judged = _tier(
        repo, tmp_path, _job("a", "pass"), _job("mac", "pass", platform="macos-26")
    )
    summary, last = _summary_and_last(code, outcomes, judged)
    assert summary.startswith("### Verdict: incomplete\n"), summary
    assert "- `mac`: it runs on macos-26" in summary
    assert last == "incomplete: 1 job could not run here"


def test_a_working_tree_run_short_only_of_jobs_it_cannot_run_ends_incomplete(repo, tmp_path):
    host = _host(repo, sandbox=_withholds)
    code, outcomes, judged = _tier(
        repo,
        tmp_path,
        _job("static", "pass"),
        _electron_job("web-unit", "pass"),
        _electron_job("web-build", "pass"),
        host=host,
        options=jobs.Options(working_tree=True),
    )
    assert code == run.PARTIAL
    assert jobs._last_line(code, outcomes, judged) == "incomplete: 2 jobs could not run here"


def test_a_job_its_step_marks_could_not_run_is_not_one_the_host_cannot_run(repo, tmp_path):
    """Only cannot_run_here's own outcome counts: a job that was not run for --fail-fast is not."""
    host = _host(repo, sandbox=_withholds)
    bad = _job("bad", "raise SystemExit(1)")
    code, outcomes, judged = _tier(
        repo, tmp_path, bad, _job("after", "pass"), host=host, fail_fast=True
    )
    assert [o.not_here for o in outcomes] == [False, False]
    assert jobs._last_line(code, outcomes, judged) == "red"


def test_a_working_tree_run_with_a_job_kept_out_for_memory_ends_partial(repo, tmp_path):
    """A working-tree run judges no record, so only its outcomes tell a job this host cannot run
    from one admission kept out: that one is not the host's, and the run stays partial."""
    host = _host(repo, sandbox=_withholds, memory=_Machine(512).memory())
    code, outcomes, judged = _tier(
        repo,
        tmp_path,
        _job("big", "pass", mem_gb=1),
        _electron_job("web-unit", "pass"),
        host=host,
        options=jobs.Options(working_tree=True, admission_wait=10),
    )
    assert code == run.PARTIAL
    assert [(o.key, o.ran, o.not_here) for o in outcomes] == [
        ("big", False, False),
        ("web-unit", False, True),
    ]
    assert jobs._last_line(code, outcomes, judged) == "partial"


@pytest.mark.parametrize(
    "reason",
    [
        verdict.Reason("", "reuse refused", "the receipt names another tree"),
        verdict.Reason("static", "no identity", "the result names no head"),
    ],
    ids=["the-plan", "a-job-that-passed"],
)
def test_a_verdict_with_a_reason_of_its_own_stays_red(reason):
    """Every job here passed or could not run, and the verdict still gives a reason that is not a
    missing record of a job this host could not run: the summary stays red."""
    outcomes = [
        jobs.Outcome("static", run.OK, []),
        jobs.Outcome(
            "web-unit", run.PARTIAL, [f"cannot run here: {_WITHHELD}"], ran=False, not_here=True
        ),
    ]
    missing = verdict.Reason("web-unit", "missing", "no result for a planned job")
    judged = verdict.Verdict(green=False, reasons=(missing, reason))
    assert jobs.could_not_run(outcomes, judged) == []
    assert jobs._summary(judged, outcomes).startswith("### Verdict: red\n")
    assert jobs._last_line(run.PARTIAL, outcomes, judged) == "partial"
    alone = verdict.Verdict(green=False, reasons=(missing,))
    assert jobs.could_not_run(outcomes, alone) == outcomes[1:]
