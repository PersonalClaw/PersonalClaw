"""``run-job``'s replay rerun (tools/ci/jobs.py, ``rerun = "replay-order"``): a test that failed in
one seeded order has its worker's whole order replayed, once, in one xdist worker, only to learn
whether it fails again there; the job stays red whatever the replay shows.

Each case plants a small seeded suite (``tests/random_order.py`` and a conftest that loads it) in
a git repository in ``tmp_path`` and runs a two-order job through ``jobs.run_one``, the path
``run-job`` takes, so both orders and the replay are real pytest processes. What the replay found
is the file run-job leaves in its ``replays`` folder; the record's ``rerun_result`` stays null, so
no reader but breadth's record and the release gate takes a replay for a flake.

Security-marked: a replay must never turn a red green, and must replay a security-marked test that
ran before the failing one, never deselect it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import nested_pytest
import pytest
import random_order

from tools.ci import jobs, run, verdict
from tools.ci.catalogue import Catalogue, Job, Step

pytestmark = pytest.mark.security

PY = sys.executable
TESTS = Path(__file__).resolve().parent
POLLUTER = "tests/test_order.py::test_a_polluter"
VICTIM = "tests/test_order.py::test_b_victim"
IDS = [POLLUTER, VICTIM, "tests/test_order.py::test_c_plain"]


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


def _seeds() -> tuple[int, int]:
    """A seed whose order runs the polluter before the victim, and one that runs it after."""
    before = next(s for s in range(1, 500) if _first(s) == POLLUTER)
    after = next(s for s in range(1, 500) if _first(s) == VICTIM)
    return before, after


def _first(seed: int) -> str:
    order = random_order.shuffled(IDS, seed)
    return POLLUTER if order.index(POLLUTER) < order.index(VICTIM) else VICTIM


def _suite(tmp_path: Path, *, polluter_mark: str = "", victim: str = "") -> Path:
    """A repository of three tests in one module. The polluter leaves a module global set; the
    victim fails when it finds it set (an order dependence), or runs *victim*'s body instead."""
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / ".gitignore").write_text("reports/\n/.local/\n__pycache__/\n.pytest_cache/\n")
    (root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'testpaths = ["tests"]\n'
        'addopts = ["--timeout=60"]\n'
        'markers = ["security: never a flake"]\n'
    )
    (root / "tests" / "random_order.py").write_text((TESTS / "random_order.py").read_text())
    (root / "tests" / "conftest.py").write_text(
        "import random_order\n\n\n"
        "def pytest_addoption(parser):\n    random_order.addoption(parser)\n\n\n"
        "def pytest_configure(config):\n    random_order.configure(config)\n"
    )
    (root / "tests" / "shared_state.py").write_text("LEFT = False\n")
    (root / "tests" / "test_order.py").write_text(
        "import os\n\nimport pytest\n\nimport shared_state\n\n\n"
        f"{polluter_mark}def test_a_polluter():\n    shared_state.LEFT = True\n\n\n"
        "def test_b_victim():\n"
        + (victim or "    assert not shared_state.LEFT, 'left set by an earlier test'\n")
        + "\n\ndef test_c_plain():\n    pass\n"
    )
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "planted")
    return root


def _order(seed: int, n: int) -> Step:
    return Step(
        (
            *nested_pytest.argv(".", python=PY),
            "-p",
            "no:cacheprovider",
            "-n",
            "1",
            "--dist",
            "worksteal",
            "-q",
            "tests",
            "--random-order-seed",
            str(seed),
            f"--junitxml=reports/junit-{n}.xml",
        )
    )


def _job(first: int, second: int) -> Job:
    return Job(
        id="ordered",
        tiers=("breadth",),
        inputs=(":(glob)**",),
        external_inputs=(),
        toolchain=(),
        cache=(),
        security="security",
        timeout_min=5,
        command=(_order(first, 1), _order(second, 2)),
        junit=("reports/junit-1.xml", "reports/junit-2.xml"),
        rerun="replay-order",
    )


def _run(root: Path, job: Job, tmp_path: Path) -> tuple[jobs.Outcome, dict | None]:
    host = jobs.Host(root=root, family="ubuntu-", path=os.environ.get("PATH", os.defpath))
    work = tmp_path / "work"
    outcome = jobs.run_one(Catalogue((job,)), job, None, host, jobs.Options(), work)
    left = work / jobs.REPLAYS / "ordered.json"
    return outcome, json.loads(left.read_text()) if left.is_file() else None


def _replayed(found: dict | None) -> dict:
    assert found is not None
    [test] = found["tests"]
    return test


def test_a_test_that_fails_again_in_its_workers_replayed_order_is_not_passed(tmp_path):
    """🔴 Before: run-job reran the victim alone, where it passed: a flake, though the order it
    failed in fails it every time. The replay runs the polluter before it again, and it fails."""
    before, after = _seeds()
    root = _suite(tmp_path)
    outcome, found = _run(root, _job(before, after), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    replayed = _replayed(found)
    assert replayed == {"test": VICTIM, "outcome": jobs.REPLAY_FAILED, "why": jobs.REPLAY_FAILED}
    assert found["seed"] == str(before) and found["worker"] == "gw0"
    assert found["argv"][-9:-3] == [
        "-n",
        "1",
        "--dist",
        "load",
        "--replay-order",
        found["argv"][-4],
    ]
    record = json.loads(outcome.record.read_text())
    assert record["result"] == verdict.FAILURE
    assert [t["rerun_result"] for t in record["rerun"]["tests"]] == [None]


def test_a_test_that_passes_in_its_workers_replayed_order_is_a_flakes_evidence(tmp_path):
    """The victim fails once on this machine, whatever its order: the replay of its worker's
    order passes it, and the job is still red."""
    before, after = _seeds()
    flag = tmp_path / "seen"
    victim = (
        f"    if not os.path.exists({str(flag)!r}):\n"
        f"        open({str(flag)!r}, 'w').close()\n"
        "        raise AssertionError('first call on this machine')\n"
    )
    root = _suite(tmp_path, victim=victim)
    outcome, found = _run(root, _job(before, after), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert _replayed(found) == {
        "test": VICTIM,
        "outcome": jobs.REPLAY_PASSED,
        "why": jobs.REPLAY_PASSED,
    }
    assert found["listed"] == 3


def test_a_security_marked_test_before_it_in_the_order_is_replayed_too(tmp_path):
    """🔴 CORE (iv): the polluter is security-marked. A rerun that deselects security tests runs
    the victim without it, where it passes; the replay runs it first again, as it ran."""
    before, after = _seeds()
    root = _suite(tmp_path, polluter_mark="@pytest.mark.security\n")
    outcome, found = _run(root, _job(before, after), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert _replayed(found)["outcome"] == jobs.REPLAY_FAILED


def test_a_test_that_fails_only_in_a_worker_replays_red(tmp_path):
    """🔴 CORE (a): the replay runs in one xdist worker, as the failing process was one, so a
    test that fails only in a worker fails in its replay; alone (``-n 0``) it would pass."""
    before, after = _seeds()
    victim = "    assert 'PYTEST_XDIST_WORKER' not in os.environ\n"
    root = _suite(tmp_path, victim=victim)
    outcome, found = _run(root, _job(before, after), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert _replayed(found)["outcome"] == jobs.REPLAY_FAILED


def test_a_failed_security_marked_test_is_never_replayed(tmp_path):
    before, after = _seeds()
    root = _suite(tmp_path)
    (root / "tests" / "test_order.py").write_text(
        (root / "tests" / "test_order.py")
        .read_text()
        .replace("def test_b_victim", "@pytest.mark.security\ndef test_b_victim")
    )
    _git(root, "commit", "-q", "-am", "the victim is a security row")
    outcome, found = _run(root, _job(before, after), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    replayed = _replayed(found)
    assert replayed["outcome"] == jobs.REPLAY_DID_NOT_RUN
    assert "security-marked" in replayed["why"]


def test_the_replay_keeps_the_steps_options_and_sets_its_own(tmp_path):
    argv = [
        *nested_pytest.argv("/x", python=PY),
        "-n",
        "4",
        "--dist",
        "worksteal",
        "-q",
        "tests",
        "--shard-list",
        "s.txt",
        "--random-order-seed",
        "801",
        "--junitxml=r.xml",
        "--shard-ids",
        "ids.txt",
        "-m",
        "not slow",
        "--timeout",
        "30",
        "-p",
        "conftest",
        "-n8",
    ]
    assert jobs.replay_args(argv) == [
        "-q",
        "tests",
        "--shard-list",
        "s.txt",
        "-m",
        "not slow",
        "--timeout",
        "30",
        "-p",
        "conftest",
    ]


def test_an_order_file_a_replaced_worker_wrote_or_that_names_it_twice_is_no_replay(tmp_path):
    folder = tmp_path / "orders"
    folder.mkdir()

    def write(name: str, *rows: tuple[str, int]) -> None:
        (folder / name).write_text(
            "".join(json.dumps({"nodeid": n, "position": p}) + "\n" for n, p in rows)
        )

    write("order-801-gw0.jsonl", ("a", 1), (VICTIM, 2))
    assert jobs.worker_order(folder, "801", VICTIM) == (folder / "order-801-gw0.jsonl", "")
    write("order-801-gw0.jsonl", ("a", 1), (VICTIM, 3))
    assert "not one process's order" in jobs.worker_order(folder, "801", VICTIM)[1]
    write("order-801-gw0.jsonl", (VICTIM, 1), (VICTIM, 2))
    assert "names it 2 times" in jobs.worker_order(folder, "801", VICTIM)[1]
    write("order-801-gw0.jsonl", ("a", 1), (VICTIM, 2))
    write("order-801-gw1.jsonl", (VICTIM, 1))
    assert "2 order files" in jobs.worker_order(folder, "801", VICTIM)[1]


def test_the_orders_folder_is_the_plugins():
    assert jobs.ORDERS == random_order.FOLDER


def _failed_step(
    tmp_path: Path, message: str = "assert False", *, folder: str = "tests"
) -> tuple[Path, list[str]]:
    """A seeded step's leavings, written out: its report failing the victim with *message*, and
    its one worker's order file, in a repository whose *folder*/test_order.py holds the victim."""
    root = tmp_path / "repo"
    (root / folder).mkdir(parents=True)
    (root / folder / "test_order.py").write_text("def test_b_victim():\n    pass\n")
    (root / jobs.ORDERS).mkdir(parents=True)
    classname = f"{folder.replace('/', '.')}.test_order"
    (root / "reports" / "junit-1.xml").write_text(
        f'<testsuites><testsuite><testcase classname="{classname}" name="test_b_victim">'
        f'<failure message="{message}"/></testcase></testsuite></testsuites>'
    )
    ids = [i.replace("tests/", f"{folder}/", 1) for i in IDS[:2]]
    (root / jobs.ORDERS / "order-801-gw0.jsonl").write_text(
        "".join(json.dumps({"nodeid": n, "position": p}) + "\n" for p, n in enumerate(ids, 1))
    )
    argv = [
        *nested_pytest.argv(tmp_path, python=PY),
        "-n",
        "1",
        "--dist",
        "worksteal",
        "-q",
        "tests",
    ]
    return root, [*argv, "--random-order-seed", "801", "--junitxml=reports/junit-1.xml"]


def _diagnosed(tmp_path: Path, root: Path, argv: list[str], *, deadline: float) -> dict:
    host = jobs.Host(root=root, family="ubuntu-", path=os.environ.get("PATH", os.defpath))
    *_, left = jobs.diagnose_replay(
        argv,
        cwd=root,
        env={},
        host=host,
        scratch=run.Scratch.create(tmp_path / "scratch"),
        grace=1.0,
        deadline=deadline,
        started=0.0,
    )
    return _replayed(left)


def test_a_case_its_worker_crashed_in_is_never_replayed(tmp_path):
    """🔴 CORE (c): a crashed or replaced worker's case is never a flake. Its order file is a
    replaced process's, so no replay of it is the order the case failed in."""
    root, argv = _failed_step(
        tmp_path, "worker 'gw0' crashed while running 'tests/test_order.py::test_b_victim'"
    )
    replayed = _diagnosed(tmp_path, root, argv, deadline=time.monotonic() + 60)
    assert replayed["outcome"] == jobs.REPLAY_DID_NOT_RUN
    assert "crashed or was replaced" in replayed["why"]


def test_a_replay_with_no_time_left_did_not_run(tmp_path):
    """🔴 CORE (vi): the replay runs inside the job's time; with none left it is red, said so."""
    root, argv = _failed_step(tmp_path)
    replayed = _diagnosed(tmp_path, root, argv, deadline=time.monotonic() - 1)
    assert replayed == {
        "test": VICTIM,
        "outcome": jobs.REPLAY_DID_NOT_RUN,
        "why": f"{jobs.REPLAY_DID_NOT_RUN}: the job's time ran out",
    }


@pytest.mark.parametrize(
    ("cases", "interrupted", "said"),
    [
        (2, False, "its report names it 2 times"),
        (0, False, "its report names it 0 times"),
        (1, True, "it was interrupted"),
    ],
    ids=["named-twice", "not-named", "interrupted"],
)
def test_a_replay_that_did_not_end_in_one_pass_of_the_test_did_not_run(
    tmp_path, monkeypatch, cases, interrupted, said
):
    """🔴 CORE (e) and (vi): a replay is a flake's evidence only when its report names the test
    exactly once, passed, and the replay ended by itself."""
    root, argv = _failed_step(tmp_path)

    def ran(command, **_):
        [out] = [a.removeprefix("--junitxml=") for a in command if a.startswith("--junitxml=")]
        case = '<testcase classname="tests.test_order" name="test_b_victim"/>'
        Path(out).write_text(f"<testsuites><testsuite>{case * cases}</testsuite></testsuites>")
        return run.Result(run.OK, 0, (), interrupted=interrupted)

    monkeypatch.setattr(jobs, "_security_marked", lambda *_, **__: set())
    monkeypatch.setattr(jobs.run, "run_job", ran)
    replayed = _diagnosed(tmp_path, root, argv, deadline=time.monotonic() + 60)
    assert replayed["outcome"] == jobs.REPLAY_DID_NOT_RUN
    assert replayed["why"] == f"{jobs.REPLAY_DID_NOT_RUN}: {said}"


def test_one_pass_of_the_test_in_a_replay_that_ended_by_itself_is_a_flakes_evidence(
    tmp_path, monkeypatch
):
    root, argv = _failed_step(tmp_path)

    def ran(command, **_):
        [out] = [a.removeprefix("--junitxml=") for a in command if a.startswith("--junitxml=")]
        case = '<testcase classname="tests.test_order" name="test_b_victim"/>'
        Path(out).write_text(f"<testsuites><testsuite>{case}</testsuite></testsuites>")
        return run.Result(run.OK, 0, ())

    monkeypatch.setattr(jobs, "_security_marked", lambda *_, **__: set())
    monkeypatch.setattr(jobs.run, "run_job", ran)
    replayed = _diagnosed(tmp_path, root, argv, deadline=time.monotonic() + 60)
    assert replayed["outcome"] == jobs.REPLAY_PASSED


def test_a_rails_failure_is_never_replayed(tmp_path):
    """🔴 A rail is red at once and never rerun: its replay would be a second reading of a check
    of the tree, which one order can't make flaky."""
    root, argv = _failed_step(tmp_path, folder="tests/rails")
    replayed = _diagnosed(tmp_path, root, argv, deadline=time.monotonic() + 60)
    assert replayed == {
        "test": "tests/rails/test_order.py::test_b_victim",
        "outcome": jobs.REPLAY_DID_NOT_RUN,
        "why": "a rail: red at once and never rerun",
    }


def test_the_replay_runs_inside_the_jobs_remaining_time(tmp_path, monkeypatch):
    """🔴 CORE (vi): the replay's process is held to what is left of the job's time, so a replay
    that hangs ends with the job's deadline, red, never past it."""
    root, argv = _failed_step(tmp_path)
    given: list[float] = []

    def ran(command, *, timeout, **_):
        given.append(timeout)
        [out] = [a.removeprefix("--junitxml=") for a in command if a.startswith("--junitxml=")]
        case = '<testcase classname="tests.test_order" name="test_b_victim"/>'
        Path(out).write_text(f"<testsuites><testsuite>{case}</testsuite></testsuites>")
        return run.Result(run.OK, 0, ())

    monkeypatch.setattr(jobs, "_security_marked", lambda *_, **__: set())
    monkeypatch.setattr(jobs.run, "run_job", ran)
    replayed = _diagnosed(tmp_path, root, argv, deadline=time.monotonic() + 40)
    assert replayed["outcome"] == jobs.REPLAY_PASSED
    [timeout] = given
    assert 0 < timeout <= 40
