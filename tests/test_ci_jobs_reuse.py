"""``python -m tools.ci run <tier>`` and the verdict store (tools/ci/jobs.py, verdict_store.py).

A run reads the checkout once as it begins, keys each planned job on every input it runs on,
reuses a job the store holds a clean pass of on this tree (``tree``, or ``ttl`` inside its TTL),
runs every other one, records each clean pass a job of a reusable class made, and writes
``reuse-receipt.json``. A reused job leaves no job record; the run's verdict counts it by its
receipt. A dirty run replays and records nothing; ``--no-reuse`` replays nothing; a working-tree
run has no store at all.

Each case plants a git repository in ``tmp_path`` whose jobs are Python programs that add a line
to a call log outside the repository, so how many times a job ran is counted, not inferred. The
host-capability fingerprint is passed in: the planted tree has none of the suite's probes.

Security-marked: a reuse that should not happen is a false green.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from tools.ci import capabilities, catalogue, jobs, run, verdict_store
from tools.ci.catalogue import Catalogue, Env, Job, Step, parse_reuse

pytestmark = pytest.mark.security

PY = sys.executable


@pytest.fixture(autouse=True)
def _a_home_of_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The store's git reads the global file ``HOME`` names: here, one of this test's own."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Planted",
        GIT_AUTHOR_EMAIL="planted@example.invalid",
        GIT_COMMITTER_NAME="Planted",
        GIT_COMMITTER_EMAIL="planted@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("reports/\n/.local/\n/.ci-apps/\n")
    (root / "a.txt").write_text("one\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "planted")
    return root


def _job(job_id: str, log: Path, *, fails: bool = False, **kw) -> Job:
    """A job that adds its id to *log*, then passes (or fails)."""
    program = f"open({str(log)!r}, 'a').write({job_id!r} + '\\n')" + (
        "; raise SystemExit(1)" if fails else ""
    )
    fields = dict(
        id=job_id,
        tiers=("merge",),
        inputs=(":(glob)**",),
        external_inputs=(),
        toolchain=(),
        cache=(),
        security="ordinary",
        timeout_min=5,
        reuse=parse_reuse("tree"),
        command=(Step((PY, "-c", program)),),
    )
    fields.update(kw)
    return Job(**fields)  # type: ignore[arg-type]


def _calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def _no_probes(root: Path, **_: object) -> Mapping[str, str]:
    return {}


def _tier(
    repo: Path, tmp_path: Path, *tier_jobs: Job, n: int = 1, options=None, fingerprint=None
) -> tuple[int, list[jobs.Outcome], object, dict | None]:
    """Run the tier once, into a work folder of its own; return its code, outcomes, verdict and
    the receipt it wrote (None when it wrote none)."""
    work = tmp_path / f"work-{n}"
    host = jobs.Host(root=repo, family="ubuntu-", path=os.environ.get("PATH", os.defpath))
    code, outcomes, judged = jobs.run_tier(
        Catalogue(tuple(tier_jobs)),
        "merge",
        host,
        options or jobs.Options(),
        work,
        fingerprint=fingerprint or _no_probes,
    )
    receipt = work / jobs.RECEIPT
    return code, outcomes, judged, json.loads(receipt.read_text()) if receipt.exists() else None


def _decisions(receipt: dict | None) -> dict[str, str]:
    assert receipt is not None
    return {j["id"]: j["decision"] for j in receipt["jobs"]}


def test_a_tree_job_that_passed_is_reused_on_an_unchanged_tree(repo, tmp_path):
    log = tmp_path / "calls.log"
    job = _job("rails", log)
    code, _, judged, receipt = _tier(repo, tmp_path, job)
    assert code == run.OK and judged.green
    assert _decisions(receipt) == {"rails": "run"}
    assert receipt["recorded"] == {"rails": "recorded"}
    code, outcomes, judged, receipt = _tier(repo, tmp_path, job, n=2)
    assert code == run.OK and judged.green, judged
    assert _calls(log) == ["rails"], "the second run reused the pass, so it ran nothing"
    assert _decisions(receipt) == {"rails": "reused"}
    assert receipt["dirty"] is False
    [outcome] = outcomes
    assert outcome.reused and outcome.record is None
    head = _git(repo, "rev-parse", "HEAD")
    assert receipt["jobs"][0]["source"]["sha"] == head
    assert any(line.startswith(f"reused rails from {head[:12]}") for line in outcome.reasons)


def test_a_never_job_runs_every_time(repo, tmp_path, subtests):
    log = tmp_path / "calls.log"
    never = {
        "security": _job("suite", log, security="security"),
        "live": _job("audit", log, live=True),
        "class never": _job("static", log, reuse=parse_reuse("never")),
    }
    for n, (name, job) in enumerate(never.items()):
        with subtests.test(job=name):
            _tier(repo, tmp_path, job, n=10 * n + 1)
            _, outcomes, judged, receipt = _tier(repo, tmp_path, job, n=10 * n + 2)
            assert judged.green
            assert _calls(log).count(job.id) == 2
            assert _decisions(receipt) == {job.id: "run"}
            # Not recorded, and the receipt says why: the class's own reason.
            [decision] = receipt["jobs"]
            assert receipt["recorded"] == {job.id: f"not recorded: {decision['reason']}"}
            assert "never" in decision["reason"]
            assert not outcomes[0].reused


def test_a_reused_job_and_a_job_that_ran_make_one_green_verdict(repo, tmp_path):
    log = tmp_path / "calls.log"
    tree_job, never_job = _job("rails", log), _job("suite", log, security="security")
    _tier(repo, tmp_path, tree_job, never_job)
    code, _, judged, receipt = _tier(repo, tmp_path, tree_job, never_job, n=2)
    assert code == run.OK and judged.green, judged
    assert _decisions(receipt) == {"rails": "reused", "suite": "run"}
    assert _calls(log) == ["rails", "suite", "suite"]


def test_a_red_job_is_never_recorded_and_runs_again(repo, tmp_path):
    log = tmp_path / "calls.log"
    job = _job("rails", log, fails=True)
    code, _, judged, receipt = _tier(repo, tmp_path, job)
    assert code == run.RED and not judged.green
    assert receipt["recorded"] == {"rails": "not recorded: it is red"}
    code, _, _, receipt = _tier(repo, tmp_path, job, n=2)
    assert code == run.RED
    assert _calls(log) == ["rails", "rails"]
    assert _decisions(receipt) == {"rails": "run"}


def test_a_new_tree_runs_the_job_again(repo, tmp_path):
    log = tmp_path / "calls.log"
    job = _job("rails", log)
    _tier(repo, tmp_path, job)
    (repo / "a.txt").write_text("two\n")
    _git(repo, "commit", "-q", "-am", "a new tree")
    _, _, judged, receipt = _tier(repo, tmp_path, job, n=2)
    assert judged.green
    assert _calls(log) == ["rails", "rails"]
    assert _decisions(receipt) == {"rails": "run"}


def test_a_change_of_capability_runs_the_job_again(repo, tmp_path):
    log = tmp_path / "calls.log"
    job = _job("rails", log)
    _tier(repo, tmp_path, job, fingerprint=lambda root, **_: {"probe:git": "absent"})
    _, _, _, receipt = _tier(
        repo, tmp_path, job, n=2, fingerprint=lambda root, **_: {"probe:git": "/usr/bin/git@x"}
    )
    assert _calls(log) == ["rails", "rails"]
    assert _decisions(receipt) == {"rails": "run"}


def test_a_change_of_the_jobs_entry_runs_it_again(repo, tmp_path):
    log = tmp_path / "calls.log"
    _tier(repo, tmp_path, _job("rails", log))
    _, _, _, receipt = _tier(repo, tmp_path, _job("rails", log, timeout_min=6), n=2)
    assert _calls(log) == ["rails", "rails"]
    assert _decisions(receipt) == {"rails": "run"}


def test_a_dirty_run_replays_and_records_nothing(repo, tmp_path):
    log = tmp_path / "calls.log"
    job = _job("rails", log)
    _tier(repo, tmp_path, job)
    (repo / "untracked.txt").write_text("x\n")
    _, _, _, receipt = _tier(repo, tmp_path, job, n=2)
    assert receipt["dirty"] is True
    assert _decisions(receipt) == {"rails": "run"}
    assert "dirty" in receipt["recorded"]["rails"]
    assert _calls(log) == ["rails", "rails"]


def test_a_dirty_run_records_nothing_a_clean_one_could_reuse(repo, tmp_path):
    log = tmp_path / "calls.log"
    job = _job("rails", log)
    (repo / "untracked.txt").write_text("x\n")
    _tier(repo, tmp_path, job)
    (repo / "untracked.txt").unlink()
    _, _, _, receipt = _tier(repo, tmp_path, job, n=2)
    assert receipt["dirty"] is False
    assert _decisions(receipt) == {"rails": "run"}
    assert receipt["recorded"] == {"rails": "recorded"}


def test_no_reuse_runs_everything_and_still_records(repo, tmp_path):
    log = tmp_path / "calls.log"
    job = _job("rails", log)
    _tier(repo, tmp_path, job)
    _, _, judged, receipt = _tier(repo, tmp_path, job, n=2, options=jobs.Options(reuse=False))
    assert judged.green
    assert _calls(log) == ["rails", "rails"]
    assert _decisions(receipt) == {"rails": "run"}
    assert "--no-reuse" in receipt["jobs"][0]["reason"]
    assert receipt["recorded"] == {"rails": "recorded"}
    _tier(repo, tmp_path, job, n=3)
    assert _calls(log) == ["rails", "rails"], "a --no-reuse pass is reused by the next run"


def test_a_working_tree_run_has_no_store(repo, tmp_path):
    log = tmp_path / "calls.log"
    job = _job("rails", log)
    _tier(repo, tmp_path, job)
    _, outcomes, _, receipt = _tier(
        repo, tmp_path, job, n=2, options=jobs.Options(working_tree=True)
    )
    assert receipt is None
    assert _calls(log) == ["rails", "rails"]
    assert not outcomes[0].reused


def test_a_fingerprint_that_is_refused_turns_reuse_off_and_runs_everything(repo, tmp_path):
    log = tmp_path / "calls.log"
    job = _job("rails", log)
    _tier(repo, tmp_path, job)

    def refused(root: Path, **_: object) -> Mapping[str, str]:
        raise capabilities.Refused("a probe whose tool the scan cannot name")

    code, outcomes, judged, receipt = _tier(repo, tmp_path, job, n=2, fingerprint=refused)
    assert code == run.OK and judged.green
    assert _calls(log) == ["rails", "rails"]
    why = "a probe whose tool the scan cannot name"
    assert receipt == {
        "off": why,
        "dirty": False,
        "jobs": [
            {
                "id": "rails",
                "decision": "run",
                "reuse": "tree",
                "key": None,
                "inputs": None,
                "reason": f"reuse is off: {why}",
                "source": None,
                "ttl_age_seconds": None,
            }
        ],
        "recorded": {"rails": f"not recorded: reuse is off: {why}"},
    }


def test_a_ttl_job_is_reused_inside_its_ttl(repo, tmp_path):
    log = tmp_path / "calls.log"
    job = _job("served", log, reuse=parse_reuse("ttl:6"))
    _tier(repo, tmp_path, job)
    _, _, _, receipt = _tier(repo, tmp_path, job, n=2)
    assert _decisions(receipt) == {"served": "reused"}
    assert receipt["jobs"][0]["ttl_age_seconds"] is not None


def test_the_keys_inputs_name_the_job_and_this_host(repo, tmp_path):
    log = tmp_path / "calls.log"
    job = _job("rails", log)
    _, _, _, receipt = _tier(
        repo, tmp_path, job, fingerprint=lambda root, **_: {"switch:X": "unset"}
    )
    inputs = receipt["jobs"][0]["inputs"]
    assert inputs["job_id"] == "rails"
    assert inputs["tree_oid"] == _git(repo, "rev-parse", "HEAD^{tree}")
    assert inputs["os"] == sys.platform and inputs["arch"] == platform.machine()
    assert inputs["capabilities"] == {"switch:X": "unset"}
    assert inputs["argv"] == [[".", *job.command[0].argv]]
    key = verdict_store.key(verdict_store.KeyInputs(**{**inputs, "argv": inputs["argv"]}))
    assert receipt["jobs"][0]["key"] == key


def test_run_takes_no_reuse_once_and_run_job_does_not(monkeypatch):
    seen: list[jobs.Options] = []

    def fake_tier(cat, tier, host, options, work, **kw):
        seen.append(options)
        return run.OK, [], jobs.verdict.Verdict(green=True, reasons=())

    monkeypatch.setattr(jobs, "run_tier", fake_tier)
    monkeypatch.setattr(jobs.run, "real_home", lambda: Path("/nonexistent-home"))
    assert jobs.run_main(["merge", "--no-reuse", "--out", "x"]) == run.OK
    assert jobs.run_main(["merge", "--out", "y"]) == run.OK
    assert [o.reuse for o in seen] == [False, True]
    assert jobs.run_main(["merge", "--no-reuse", "--no-reuse"]) == run.PARTIAL
    assert jobs.run_job_main(["rails", "--no-reuse"]) == run.PARTIAL


def test_a_receipt_that_reuses_a_never_job_turns_the_run_red(repo, tmp_path, monkeypatch):
    """A forged receipt, one decide() never writes, is refused by the run's verdict: the job it
    calls reused is not run, and the run is red, never green."""
    log = tmp_path / "calls.log"
    job = _job("suite", log, security="security")
    real = verdict_store.decide

    def forged(*args, **kw):
        receipt = real(*args, **kw)
        return verdict_store.Receipt(
            receipt.dirty,
            tuple(
                dataclasses.replace(d, reused=True, source=verdict_store.Source("a" * 40, 1.0, 0))
                for d in receipt.jobs
            ),
        )

    monkeypatch.setattr(jobs.verdict_store, "decide", forged)
    code, _, judged, _ = _tier(repo, tmp_path, job)
    assert not judged.green and code == run.RED
    # Refused, and the job it called reused was never run, so it has no result either.
    by_kind = {r.kind: r for r in judged.reasons}
    assert set(by_kind) == {"reuse refused", "missing"}, judged.reasons
    assert "never reused" in by_kind["reuse refused"].detail
    assert by_kind["missing"].key == "suite"


def test_a_pass_whose_record_names_a_rerun_is_not_recorded(repo, tmp_path):
    """run-job writes a rerun only on a red job; a record that names one on a pass is still not
    recorded."""
    job = _job("rails", tmp_path / "calls.log")
    reuse = jobs._begin_reuse(
        Catalogue((job,)),
        [job],
        jobs.Host(root=repo, family="ubuntu-", path=os.environ.get("PATH", os.defpath)),
        jobs.Options(),
        _no_probes,
        0.0,
    )
    record = tmp_path / "record.json"
    record.write_text(
        json.dumps(
            {
                "result": "success",
                "head_sha": _git(repo, "rev-parse", "HEAD"),
                "quarantined": [],
                "rerun": {"argv": None, "exit": None, "tests": []},
                "steps": [{"seconds": 1.0}],
            }
        )
    )
    outcome = jobs.Outcome("rails", run.OK, record=record)
    jobs._record_pass(reuse, job, [outcome], 1.0)
    assert reuse.recorded == {"rails": "not recorded: the pass needed a rerun"}


def test_the_key_holds_the_jobs_entry_and_the_locks(repo, tmp_path):
    (repo / "uv.lock").write_text("version = 1\n")
    (repo / "package-lock.json").write_text("{}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "locks")
    job = _job("rails", tmp_path / "calls.log")
    _, _, _, receipt = _tier(repo, tmp_path, job)
    inputs = receipt["jobs"][0]["inputs"]
    assert inputs["entry_text"] == repr(job)
    assert inputs["lock_digests"] == {
        "uv.lock": hashlib.sha256(b"version = 1\n").hexdigest(),
        "package-lock.json": hashlib.sha256(b"{}\n").hexdigest(),
    }


# ── the key holds what the run used ─────────────────────────────────────────────────────────


def _program(job_id: str, program: str, *args: str, **kw) -> Job:
    """A job whose one step runs *program* with *args*."""
    return dataclasses.replace(
        _job(job_id, Path(os.devnull), **kw), command=(Step((PY, "-c", program, *args)),)
    )


def _logs(log: Path, what: str) -> str:
    """A program that adds the value of the expression *what* to *log*."""
    return f"import os, sys; open({str(log)!r}, 'a').write(str({what}) + '\\n')"


def _committed(repo: Path, rel: str, text: str) -> str:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"plant {rel}")
    return _git(repo, "rev-parse", "HEAD")


def test_a_pass_with_a_local_worker_count_is_not_reused_by_a_run_with_the_catalogues(
    repo, tmp_path, subtests
):
    """--workers changes what the steps run: the pytest -n they pass, and the count a job's
    workers_env names. The key holds the count the run used, so neither pass stands for the
    other."""
    with subtests.test("a pytest -n"):
        log = tmp_path / "pytest.log"
        job = _program(
            "rails", _logs(log, "' '.join(sys.argv[1:])"), "pytest", "-n", "4", workers=4
        )
        _, _, _, receipt = _tier(repo, tmp_path, job, n=1, options=jobs.Options(workers=1))
        assert receipt["jobs"][0]["inputs"]["argv"] == [
            [".", PY, "-c", job.command[0].argv[2], "pytest", "-n", "1"]
        ]
        assert receipt["jobs"][0]["inputs"]["declared"]["workers"] == "1"
        _, _, _, receipt = _tier(repo, tmp_path, job, n=2)
        assert _calls(log) == ["pytest -n 1", "pytest -n 4"]
        assert _decisions(receipt) == {"rails": "run"}
        assert receipt["jobs"][0]["inputs"]["declared"]["workers"] == "4"
    with subtests.test("a workers_env"):
        log = tmp_path / "env.log"
        job = _program(
            "web-unit",
            _logs(log, "os.environ['SEEN_WORKERS']"),
            workers=2,
            workers_env="SEEN_WORKERS",
        )
        _tier(repo, tmp_path, job, n=11, options=jobs.Options(workers=1))
        _, _, _, receipt = _tier(repo, tmp_path, job, n=12)
        assert _calls(log) == ["1", "2"]
        assert _decisions(receipt) == {"web-unit": "run"}
    with subtests.test("auto is this machine's count"):
        job = _program("web-unit", "pass", workers="auto", workers_env="SEEN_WORKERS")
        _, _, _, receipt = _tier(repo, tmp_path, job, n=21)
        assert receipt["jobs"][0]["inputs"]["declared"]["workers"] == str(os.cpu_count() or 1)


def test_a_tree_job_that_reads_base_is_not_reused_for_another_base(repo, tmp_path):
    """{base} is a commit, not this run's scratch folder: a pass diffed from one base does not
    stand for a run diffed from another, and the key holds the commit the ref names."""
    first = _git(repo, "rev-parse", "HEAD")
    second = _committed(repo, "b.txt", "b\n")
    log = tmp_path / "calls.log"
    job = _program("rails", _logs(log, "sys.argv[1]"), "{base}")
    _, _, _, receipt = _tier(repo, tmp_path, job, options=jobs.Options(base="main~1"))
    assert receipt["jobs"][0]["inputs"]["declared"]["base"] == first
    _, _, _, receipt = _tier(repo, tmp_path, job, n=2, options=jobs.Options(base=second))
    assert _calls(log) == ["main~1", second]
    assert _decisions(receipt) == {"rails": "run"}
    _, _, _, receipt = _tier(repo, tmp_path, job, n=3, options=jobs.Options(base="main"))
    assert _decisions(receipt) == {"rails": "reused"}, "the same base commit, named another way"


def test_a_tree_job_whose_step_env_reads_base_is_not_reused_for_another_base(repo, tmp_path):
    """The rails entry names {base} in its step's env alone (``BASE_SHA = "{base}"``, which the
    site baselines read), with none in its argv: the key holds the commit there too."""
    first = _git(repo, "rev-parse", "HEAD")
    second = _committed(repo, "b.txt", "b\n")
    log = tmp_path / "calls.log"
    step = Step((PY, "-c", _logs(log, "os.environ['BASE_SHA']")), env=Env({"BASE_SHA": "{base}"}))
    assert not any("{base}" in word for word in step.argv), "the base must be in the env alone"
    job = dataclasses.replace(_job("rails", Path(os.devnull)), command=(step,))
    _, _, _, receipt = _tier(repo, tmp_path, job, options=jobs.Options(base="main~1"))
    assert receipt["jobs"][0]["inputs"]["declared"]["base"] == first
    _, _, _, receipt = _tier(repo, tmp_path, job, n=2, options=jobs.Options(base=second))
    assert _calls(log) == ["main~1", second]
    assert _decisions(receipt) == {"rails": "run"}
    _, _, _, receipt = _tier(repo, tmp_path, job, n=3, options=jobs.Options(base="main"))
    assert _decisions(receipt) == {"rails": "reused"}, "the same base commit, named another way"


def test_a_base_that_names_no_commit_turns_reuse_off(repo, tmp_path):
    log = tmp_path / "calls.log"
    job = _program("rails", _logs(log, "sys.argv[1]"), "{base}")
    _, _, judged, receipt = _tier(repo, tmp_path, job, options=jobs.Options(base="no-such-ref"))
    assert _calls(log) == ["no-such-ref"] and judged.green
    assert "no-such-ref" in receipt["off"]
    assert _decisions(receipt) == {"rails": "run"}


def test_a_steps_own_program_is_keyed(repo, tmp_path):
    """A step runs a program from the job's PATH that no toolchain word names (web-build's
    python3): a new program there runs the job again."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "planted-tool"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    host = jobs.Host(root=repo, family="ubuntu-", path=f"{bin_dir}{os.pathsep}{os.defpath}")
    job = dataclasses.replace(
        _job("web-build", Path(os.devnull)), command=(Step(("planted-tool",)),)
    )

    def tier(n: int) -> dict:
        work = tmp_path / f"own-{n}"
        jobs.run_tier(
            Catalogue((job,)), "merge", host, jobs.Options(), work, fingerprint=_no_probes
        )
        return json.loads((work / jobs.RECEIPT).read_text())

    receipt = tier(1)
    toolchain = receipt["jobs"][0]["inputs"]["toolchain"]
    assert toolchain["program:planted-tool"] == capabilities.identity(stub)
    assert _decisions(tier(2)) == {"web-build": "reused"}
    stub.write_text("#!/bin/sh\nexit 0 # another build\n")
    assert _decisions(tier(3)) == {"web-build": "run"}


# ── the fingerprint ─────────────────────────────────────────────────────────────────────────


def _reads_switches(
    root: Path, *, path: str, environ: Mapping[str, str], home: Path
) -> Mapping[str, str]:
    return {
        f"switch:{name}": f"set:{environ[name]}" if name in environ else "unset"
        for name in capabilities.SWITCHES
    }


def test_the_fingerprint_gets_each_steps_environment_and_a_home_of_none_of_the_users(
    repo, tmp_path
):
    seen: list[dict] = []

    def fingerprint(root: Path, **kw: object) -> Mapping[str, str]:
        seen.append(dict(kw))
        return {}

    job = _program("rails", "pass", env=Env({"PERSONALCLAW_X": "1"}))
    step = dataclasses.replace(job.command[0], env=Env({"PERSONALCLAW_Y": "2"}))
    _tier(repo, tmp_path, dataclasses.replace(job, command=(step,)), fingerprint=fingerprint)
    [kw] = seen
    environ = kw["environ"]
    assert environ["CI"] == "true" and environ["UV_PYTHON_PREFERENCE"] == "only-managed"
    assert environ["PERSONALCLAW_X"] == "1" and environ["PERSONALCLAW_Y"] == "2"
    assert kw["path"] == os.environ.get("PATH", os.defpath)
    assert Path(str(kw["home"])) != Path.home()
    assert Path(str(kw["home"])).is_relative_to(repo)


def test_each_job_has_the_capabilities_of_its_own_environment(repo, tmp_path):
    with_switch = _program("rails", "pass", env=Env({"PERSONALCLAW_TEST_CONTAINER_RUNTIME": "1"}))
    without = _program("web-unit", "pass")
    _, _, _, receipt = _tier(repo, tmp_path, with_switch, without, fingerprint=_reads_switches)
    caps = {j["id"]: j["inputs"]["capabilities"] for j in receipt["jobs"]}
    assert caps["rails"]["switch:PERSONALCLAW_TEST_CONTAINER_RUNTIME"] == "set:1"
    assert caps["web-unit"]["switch:PERSONALCLAW_TEST_CONTAINER_RUNTIME"] == "unset"


def test_a_switch_one_step_sets_is_read_as_set_for_that_step(repo, tmp_path):
    """rails-checks' pytest step sets PERSONALCLAW_REQUIRE_NPM_TREE=1 in its own env: that step's
    capabilities read it set, the others' unset."""
    job = _program("rails-checks", "pass")
    plain = job.command[0]
    switched = dataclasses.replace(plain, env=Env({"PERSONALCLAW_REQUIRE_NPM_TREE": "1"}))
    _, _, _, receipt = _tier(
        repo,
        tmp_path,
        dataclasses.replace(job, command=(plain, switched)),
        fingerprint=_reads_switches,
    )
    caps = receipt["jobs"][0]["inputs"]["capabilities"]
    assert caps["step 1: switch:PERSONALCLAW_REQUIRE_NPM_TREE"] == "unset"
    assert caps["step 2: switch:PERSONALCLAW_REQUIRE_NPM_TREE"] == "set:1"
    assert caps["switch:PERSONALCLAW_TEST_CHROME"] == "unset", "a capability every step shares"
    one_step = dataclasses.replace(_program("one-step", "pass"), command=(switched,))
    _, _, _, receipt = _tier(repo, tmp_path, one_step, n=2, fingerprint=_reads_switches)
    assert (
        receipt["jobs"][0]["inputs"]["capabilities"]["switch:PERSONALCLAW_REQUIRE_NPM_TREE"]
        == "set:1"
    )


def test_a_fingerprint_that_cannot_read_the_host_turns_reuse_off(repo, tmp_path):
    log = tmp_path / "calls.log"
    job = _job("rails", log)

    def unreadable(root: Path, **_: object) -> Mapping[str, str]:
        raise PermissionError("the probe folder cannot be read")

    _tier(repo, tmp_path, job)
    code, _, judged, receipt = _tier(repo, tmp_path, job, n=2, fingerprint=unreadable)
    assert code == run.OK and judged.green
    assert _calls(log) == ["rails", "rails"]
    assert "cannot be read" in receipt["off"]


# ── the key's other inputs ──────────────────────────────────────────────────────────────────


def test_a_change_to_another_jobs_entry_runs_this_one_again(repo, tmp_path):
    """The catalogue's version is the digest of every job's entry."""
    log = tmp_path / "calls.log"
    job, other = _job("rails", log), _job("static", log, reuse=parse_reuse("never"))
    _tier(repo, tmp_path, job, other)
    edited = dataclasses.replace(other, timeout_min=7)
    _, _, _, receipt = _tier(repo, tmp_path, job, edited, n=2)
    assert _decisions(receipt)["rails"] == "run"
    assert _calls(log).count("rails") == 2


def test_the_key_holds_each_toolchain_programs_bytes_and_each_install(repo, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("dash", "npm"):
        (bin_dir / name).write_text("#!/bin/sh\nexit 0\n")
        (bin_dir / name).chmod(0o755)
    host = jobs.Host(root=repo, family="ubuntu-", path=f"{bin_dir}{os.pathsep}{os.defpath}")
    job = _job("rails", tmp_path / "calls.log", toolchain=("dash", "npm-ci"))

    def tier(n: int) -> dict:
        work = tmp_path / f"tool-{n}"
        jobs.run_tier(
            Catalogue((job,)), "merge", host, jobs.Options(), work, fingerprint=_no_probes
        )
        return json.loads((work / jobs.RECEIPT).read_text())

    receipt = tier(1)
    inputs = receipt["jobs"][0]["inputs"]
    assert inputs["argv"][0] == [".", "npm", "ci"]
    assert inputs["toolchain"]["dash"] == capabilities.identity(bin_dir / "dash")
    assert _decisions(tier(2)) == {"rails": "reused"}
    (bin_dir / "dash").write_text("#!/bin/sh\nexit 0 # another build\n")
    assert _decisions(tier(3)) == {"rails": "run"}


def test_a_new_partner_head_runs_the_job_again(repo, tmp_path):
    partner = repo / ".ci-apps"
    partner.mkdir()
    _git(partner, "init", "-q", "-b", "main")
    first = _committed(partner, "p.txt", "1\n")
    log = tmp_path / "calls.log"
    job = _job("contract", log, external_inputs=("apps-partner",))
    _, _, _, receipt = _tier(repo, tmp_path, job)
    assert receipt["jobs"][0]["inputs"]["external_revisions"] == {"apps-partner": first}
    _committed(partner, "p.txt", "2\n")
    _, _, judged, receipt = _tier(repo, tmp_path, job, n=2)
    assert _decisions(receipt) == {"contract": "run"} and judged.green
    assert _calls(log) == ["contract", "contract"]


# ── what is recorded ────────────────────────────────────────────────────────────────────────

QUARANTINED = (
    '<testsuites><testsuite name="s"><testcase classname="t" name="q">'
    '<properties><property name="quarantine_issue" value="1"/></properties>'
    '<skipped type="pytest.xfail" message="x"/></testcase></testsuite></testsuites>'
)


def _writes_junit(xml: str) -> str:
    return (
        "import pathlib; p = pathlib.Path('reports/junit.xml'); p.parent.mkdir(exist_ok=True);"
        f" p.write_text({xml!r})"
    )


def test_a_pass_with_a_quarantined_failure_is_not_recorded(repo, tmp_path):
    job = _program("rails", _writes_junit(QUARANTINED), junit=("reports/junit.xml",))
    code, _, judged, receipt = _tier(repo, tmp_path, job)
    assert code == run.OK and judged.green, judged
    assert receipt["recorded"] == {"rails": "not recorded: 1 quarantined failure(s)"}
    _, _, _, receipt = _tier(repo, tmp_path, job, n=2)
    assert _decisions(receipt) == {"rails": "run"}


def test_the_reuse_line_counts_the_tests_the_pass_skipped(repo, tmp_path):
    """A skipped case counts; a passed one and an expected failure do not."""
    xml = (
        '<testsuites><testsuite name="s">'
        '<testcase classname="t" name="skip">'
        '<skipped type="pytest.skip" message="no docker"/></testcase>'
        '<testcase classname="t" name="bare"><skipped/></testcase>'
        '<testcase classname="t" name="ok"/>'
        '<testcase classname="t" name="xf"><skipped type="pytest.xfail" message="x"/></testcase>'
        "</testsuite></testsuites>"
    )
    job = _program("rails", _writes_junit(xml), junit=("reports/junit.xml",))
    _tier(repo, tmp_path, job)
    _, outcomes, _, receipt = _tier(repo, tmp_path, job, n=2)
    assert _decisions(receipt) == {"rails": "reused"}
    assert receipt["jobs"][0]["source"]["skipped"] == 2
    [line] = [r for r in outcomes[0].reasons if r.startswith("reused")]
    assert line.endswith(", 2 skipped"), line


def test_a_sharded_job_is_recorded_only_when_every_shard_passed(repo, tmp_path):
    _committed(repo, "pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    _committed(repo, "tests/test_a.py", "def test_a():\n    pass\n")
    _committed(repo, "tests/test_b.py", "def test_b():\n    pass\n")
    log, flag = tmp_path / "calls.log", tmp_path / "fail-shard-2"
    program = (
        f"import os, sys; open({str(log)!r}, 'a').write('shard ' + sys.argv[1] + '\\n');"
        f" raise SystemExit(1 if sys.argv[1] == '2' and os.path.exists({str(flag)!r}) else 0)"
    )
    job = _program("rails", program, "{shard}", shards=2)
    flag.write_text("x")
    code, _, _, receipt = _tier(repo, tmp_path, job)
    assert code == run.RED
    assert receipt["recorded"] == {"rails": "not recorded: it is red"}
    flag.unlink()
    _, _, judged, receipt = _tier(repo, tmp_path, job, n=2)
    assert _decisions(receipt) == {"rails": "run"}, "one red shard recorded the job's pass"
    assert receipt["recorded"] == {"rails": "recorded"} and judged.green
    _, outcomes, judged, receipt = _tier(repo, tmp_path, job, n=3)
    assert _decisions(receipt) == {"rails": "reused"} and judged.green
    assert [o.reused for o in outcomes] == [True, True]
    assert _calls(log) == ["shard 1", "shard 2", "shard 1", "shard 2"]


def test_a_job_after_a_reused_one_is_recorded_from_its_own_records(repo, tmp_path):
    log, flag = tmp_path / "calls.log", tmp_path / "web-unit-fails"
    first = _job("rails", log)
    second = _program(
        "web-unit",
        f"import os; open({str(log)!r}, 'a').write('web-unit\\n');"
        f" raise SystemExit(1 if os.path.exists({str(flag)!r}) else 0)",
    )
    flag.write_text("x")
    _tier(repo, tmp_path, first, second)
    flag.unlink()
    _, _, judged, receipt = _tier(repo, tmp_path, first, second, n=2)
    assert _decisions(receipt) == {"rails": "reused", "web-unit": "run"}
    assert receipt["recorded"] == {"web-unit": "recorded"}
    assert judged.green


def test_the_receipt_says_why_each_job_not_reused_was_not_recorded(repo, tmp_path):
    log = tmp_path / "calls.log"
    never_job = _job("suite", log, security="security")
    red_job = _job("rails", log, fails=True)
    cannot = _job("mac", log, platform="macos-26")
    _, _, _, receipt = _tier(repo, tmp_path, never_job, red_job, cannot)
    assert _calls(log) == ["suite", "rails"]
    assert receipt["recorded"] == {
        "suite": "not recorded: security evidence is never reused",
        "rails": "not recorded: it is red",
        "mac": "not recorded: it did not run here",
    }


def test_a_store_that_cannot_be_written_does_not_stop_the_run(repo, tmp_path):
    common = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    (common / "ci-verdicts").write_text("not a folder\n")
    log = tmp_path / "calls.log"
    code, _, judged, receipt = _tier(repo, tmp_path, _job("rails", log), _job("web-unit", log))
    assert _calls(log) == ["rails", "web-unit"]
    assert code == run.OK and judged.green
    assert receipt["recorded"]["rails"].startswith("not recorded: the store cannot be written: ")


def test_an_off_receipt_still_carries_dirty_and_a_decision_per_job(repo, tmp_path, capsys):
    (repo / "untracked.txt").write_text("x\n")

    def refused(root: Path, **_: object) -> Mapping[str, str]:
        raise capabilities.Refused("a probe whose tool the scan cannot name")

    log = tmp_path / "calls.log"
    capsys.readouterr()
    _, _, _, receipt = _tier(
        repo,
        tmp_path,
        _job("rails", log),
        _job("suite", log, security="security"),
        fingerprint=refused,
    )
    assert receipt["dirty"] is True
    assert _decisions(receipt) == {"rails": "run", "suite": "run"}
    assert {j["reuse"] for j in receipt["jobs"]} == {"tree", "never"}
    assert set(receipt["recorded"]) == {"rails", "suite"}
    assert "reuse is off: a probe whose tool the scan cannot name" in capsys.readouterr().err


def test_no_reuse_reads_no_entry_and_keeps_each_jobs_own_reason(repo, tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    served, suite = _job("served", log, reuse=parse_reuse("ttl:6")), _job(
        "suite", log, security="security"
    )
    _tier(repo, tmp_path, served, suite)
    looked: list[str] = []
    real = verdict_store.Store.lookup

    def lookup(self, entry_key):
        looked.append(entry_key)
        return real(self, entry_key)

    monkeypatch.setattr(verdict_store.Store, "lookup", lookup)
    _, _, _, receipt = _tier(repo, tmp_path, served, suite, n=2, options=jobs.Options(reuse=False))
    assert looked == []
    by_id = {j["id"]: j for j in receipt["jobs"]}
    assert "--no-reuse" in by_id["served"]["reason"]
    assert by_id["served"]["ttl_age_seconds"] is None
    assert by_id["suite"]["reason"] == "security evidence is never reused"


# ── what the run prints ─────────────────────────────────────────────────────────────────────


def test_the_run_prints_each_jobs_receipt_line(repo, tmp_path, capsys):
    log = tmp_path / "calls.log"
    tree_job, never_job = _job("rails", log), _job("suite", log, security="security")
    _tier(repo, tmp_path, tree_job, never_job)
    _, outcomes, _, _ = _tier(repo, tmp_path, tree_job, never_job, n=2)
    capsys.readouterr()
    jobs._say(outcomes)
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "rails: reused"
    assert re.fullmatch(r"  reused rails from [0-9a-f]{12}, 0m, 0 skipped", out[1]), out
    assert out[2].startswith("suite: passed (record ")
    assert out[3] == "  ran suite: security evidence is never reused"


#: run_tier itself, for a test that stands run_main's in for it to call.
_RUN_TIER = jobs.run_tier


def _main_over(repo: Path, tmp_path: Path, monkeypatch, tier_jobs, capsys, n: int) -> str:
    """run_main's output for a run of *tier_jobs* on the planted repository."""

    def planted(cat, tier, host, options, work, **kw):
        planted_host = jobs.Host(
            root=repo, family="ubuntu-", path=os.environ.get("PATH", os.defpath)
        )
        return _RUN_TIER(
            Catalogue(tuple(tier_jobs)),
            "merge",
            planted_host,
            options,
            work,
            fingerprint=_no_probes,
        )

    monkeypatch.setattr(jobs, "run_tier", planted)
    monkeypatch.setattr(jobs.run, "real_home", lambda: tmp_path / "home")
    capsys.readouterr()
    assert jobs.run_main(["merge", "--out", str(tmp_path / f"main-{n}")]) == run.OK
    return capsys.readouterr().out


def test_a_run_that_stands_on_a_reused_pass_says_green_on_reuse(
    repo, tmp_path, monkeypatch, capsys
):
    """A reused job did not run on this candidate, so a green run that reused one says it is
    green on reuse and names each reused job; a run that reused nothing says what it said."""
    log = tmp_path / "calls.log"
    tree_job, never_job = _job("rails", log), _job("suite", log, security="security")
    plain = "### Verdict: green\n\nEvery planned job passed on this candidate.\n"
    assert plain in _main_over(repo, tmp_path, monkeypatch, [tree_job, never_job], capsys, 1)
    some = _main_over(repo, tmp_path, monkeypatch, [tree_job, never_job], capsys, 2)
    summary = some[some.index("### Verdict") :]
    assert summary.startswith("### Verdict: green on reuse\n")
    assert "`rails`" in summary and "`suite`" not in summary
    # Alone in its catalogue, rails is keyed anew: it runs once, then stands on that pass.
    assert plain in _main_over(repo, tmp_path, monkeypatch, [tree_job], capsys, 3)
    every = _main_over(repo, tmp_path, monkeypatch, [tree_job], capsys, 4)
    summary = every[every.index("### Verdict") :]
    assert summary.startswith("### Verdict: green on reuse\n")
    assert "No planned job ran on this candidate" in summary and "`rails`" in summary
    assert _calls(log) == ["rails", "suite", "suite", "rails"]


def test_a_green_on_reuse_summary_lists_each_quarantined_failure_too() -> None:
    """A run green on reuse lists the failed tests its records name, as a plain green does."""
    from types import SimpleNamespace

    from tools.ci import verdict

    flaky = "tests/test_a.py::test_flaky"
    judged = verdict.Verdict(
        green=True,
        reasons=(),
        tests=(verdict.FailedTest("rails", 1, flaky, verdict.QUARANTINED, verdict.WAIVED),),
    )
    outcomes = [
        SimpleNamespace(key="rails", reused=False),
        SimpleNamespace(key="suite", reused=True),
    ]
    text = jobs._summary(judged, outcomes)  # type: ignore[arg-type]
    assert text.startswith("### Verdict: green on reuse\n")
    assert text.endswith(f"- `rails` attempt 1: `{flaky}`: quarantined ({verdict.WAIVED})\n")


def test_the_docs_name_every_tree_job_of_the_merge_tier():
    """docs/maintainers/ci.md says which merge-tier jobs a second gate can reuse; it names the
    catalogue's own."""
    path = Path(catalogue.REPO) / catalogue.CATALOGUE
    cat = catalogue.load(path.read_text(encoding="utf-8"), str(path))
    tree = {j.id for j in cat.tier("merge") if verdict_store.reuse_class(j).kind == "tree"}
    docs = (Path(catalogue.REPO) / "docs" / "maintainers" / "ci.md").read_text(encoding="utf-8")
    [said] = re.findall(
        r"\*\*What a gate can reuse:\*\* of the merge tier's jobs only (.+?) are\s+`tree`",
        docs,
        re.S,
    )
    assert set(re.findall(r"`([^`]+)`", said)) == tree
