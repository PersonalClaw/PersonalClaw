"""``python -m tools.ci bisect-pollution``: the test that left another failing is named (#3949).

A test that fails only after others ran in its process was left failing by one of them. The tool
reads the failing worker's order file from the random-order leg's artifact and replays that
worker's tests in one process (``--replay-order``, ``tests/random_order.py``), halving the tests
before the failing one until one is left. The search is checked against a stand-in replay, where
any order can be planted, and once end to end: a planted tree of 256 tests, one of which leaves a
later one failing, traced in at most 12 pytest runs.
"""

from __future__ import annotations

import json
import os
import random
import sys
import textwrap
from pathlib import Path

import pytest

from tools.ci import bisect_pollution as bp

_TESTS_DIR = Path(__file__).resolve().parent

VICTIM = "test_victim.py::test_needs_a_clean_flag"


def _stand_in(polluters: set[str], *, alone_fails: bool = False):
    """A replay where the last test fails when any test of *polluters* ran before it (all of
    them, for a set of two or more), and every run is counted."""
    runs: list[list[str]] = []

    def replay(tests):
        runs.append(list(tests))
        ran = set(tests[:-1])
        if alone_fails:
            return "failed"
        return "failed" if polluters and polluters <= ran else "passed"

    return replay, runs


def _earlier(n: int) -> list[str]:
    return [f"test_m{i // 10}.py::test_{i}" for i in range(n)]


@pytest.mark.parametrize("n, at", [(1, 0), (2, 1), (3, 0), (255, 37), (600, 599), (1024, 512)])
def test_the_one_test_that_leaves_it_failing_is_named_within_its_bound(n: int, at: int) -> None:
    before = _earlier(n)
    replay, runs = _stand_in({before[at]})
    finding = bp.search(VICTIM, before, replay)
    assert (finding.outcome, finding.polluter, finding.confirmed) == ("polluter", before[at], True)
    assert len(runs) == len(finding.runs) <= bp.runs_at_most(n), len(runs)
    assert all(run[-1] == VICTIM for run in runs), "the failing test always runs last"
    # Each replay keeps the worker's order: what ran before the failing test is a slice of it.
    for run in runs[1:]:
        assert run[:-1] == [t for t in before if t in set(run[:-1])], run


def test_512_tests_before_it_take_at_most_12_runs_wherever_the_polluter_ran() -> None:
    assert (bp.runs_at_most(512), bp.runs_at_most(513), bp.runs_at_most(1024)) == (12, 13, 13)
    before = _earlier(512)
    for at in [0, 1, 255, 256, 510, 511, *random.Random(5).sample(range(512), 40)]:
        replay, runs = _stand_in({before[at]})
        finding = bp.search(VICTIM, before, replay)
        assert (finding.polluter, finding.confirmed) == (before[at], True)
        assert len(runs) <= 12, (at, len(runs))


def test_a_test_that_fails_alone_blames_nothing_before_it() -> None:
    replay, runs = _stand_in(set(), alone_fails=True)
    finding = bp.search(VICTIM, _earlier(50), replay)
    assert (finding.outcome, finding.polluter, len(runs)) == ("fails_alone", None, 1)


def test_a_failure_the_sequence_does_not_repeat_is_said_so() -> None:
    replay, runs = _stand_in(set())
    finding = bp.search(VICTIM, _earlier(50), replay)
    assert (finding.outcome, len(runs)) == ("not_reproduced", 2)


def test_a_failure_that_needs_two_earlier_tests_is_not_pinned_on_one() -> None:
    before = _earlier(64)
    replay, _runs = _stand_in({before[3], before[60]})
    finding = bp.search(VICTIM, before, replay)
    assert (finding.outcome, finding.polluter) == ("not_one_test", None), finding


def test_a_replay_that_did_not_finish_stops_the_search() -> None:
    before = _earlier(16)
    results = iter(["passed", "failed", "not_run"])
    finding = bp.search(VICTIM, before, lambda tests: next(results))
    assert (finding.outcome, len(finding.runs)) == ("inconclusive", 3)


# ── the order file ───────────────────────────────────────────────────────────────────────────


def _order(path: Path, ids: list[str], *, seed: int, worker: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    out = path / f"order-{seed}-{worker}.jsonl"
    lines = [
        json.dumps({"nodeid": n, "position": p, "worker": worker, "seed": seed})
        for p, n in enumerate(ids, 1)
    ]
    out.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")  # read by position
    return out


def test_the_failing_workers_order_file_gives_the_tests_before_it(tmp_path: Path) -> None:
    ids = [*_earlier(5), VICTIM, "test_m9.py::test_after"]
    path = _order(tmp_path / "random-order-2", ids, seed=4011, worker="gw1")
    _order(tmp_path / "random-order-2", _earlier(3), seed=4011, worker="gw0")
    assert bp.order_file(tmp_path, VICTIM, None, None) == path
    assert bp.preceding(path, VICTIM) == _earlier(5)


def test_a_test_that_ran_in_two_orders_needs_its_seed_named(tmp_path: Path) -> None:
    first = _order(tmp_path, [VICTIM], seed=4011, worker="gw1")
    _order(tmp_path, [VICTIM], seed=4012, worker="gw3")
    with pytest.raises(bp.BisectError, match="name --seed/--worker"):
        bp.order_file(tmp_path, VICTIM, None, None)
    assert bp.order_file(tmp_path, VICTIM, 4011, None) == first
    with pytest.raises(bp.BisectError, match="no order file"):
        bp.order_file(tmp_path, VICTIM, 4011, "gw3")


def test_a_line_that_is_no_order_line_is_named(tmp_path: Path) -> None:
    tmp_path.joinpath("order-1-gw0.jsonl").write_text('{"nodeid": "a::b"}\n', encoding="utf-8")
    with pytest.raises(bp.BisectError, match="order-1-gw0.jsonl:1: not an order line"):
        bp.order_file(tmp_path, "a::b", None, None)


# ── end to end ───────────────────────────────────────────────────────────────────────────────

_CONFTEST = """
    import random_order


    def pytest_addoption(parser):
        random_order.addoption(parser)


    def pytest_configure(config):
        random_order.configure(config)
"""

_POLLUTER = """
    import flag


    def test_leaves_the_flag_set():
        flag.state["set"] = True
"""

_VICTIM = """
    import flag


    def test_needs_a_clean_flag(tmp_path):
        assert not flag.state["set"], "a test before this one left the flag set"
"""


@pytest.mark.timeout(120)
def test_a_planted_polluter_is_found_in_at_most_12_pytest_runs(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    files = {
        # Its own project, so pytest names its tests from it and reads no ini file above it.
        "pytest.ini": "[pytest]\n",
        "conftest.py": _CONFTEST,
        "flag.py": 'state = {"set": False}\n',
        "test_polluter.py": _POLLUTER,
        "test_victim.py": _VICTIM,
    }
    for m in range(16):
        files[f"test_m{m}.py"] = "\n".join(f"def test_{t}():\n    pass\n" for t in range(16))
    for name, text in files.items():
        (tree / name).write_text(textwrap.dedent(text), encoding="utf-8")
    plain = [f"test_m{m}.py::test_{t}" for m in range(16) for t in range(16)]
    polluter = "test_polluter.py::test_leaves_the_flag_set"
    before = plain[:91] + [polluter] + plain[91:255]
    path = _order(tmp_path / "orders", [*before, VICTIM], seed=4021, worker="gw2")

    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(_TESTS_DIR), str(tree)])
    replay = bp.pytest_replay(
        python=sys.executable,
        root=tree,
        scratch=tmp_path / "scratch",
        pytest_args=("-o", "addopts=", "--color=no"),
        env=env,
        seconds=120,
    )
    read = bp.order_file(tmp_path / "orders", VICTIM, None, None)
    assert read == path and bp.preceding(read, VICTIM) == before
    found = bp.search(VICTIM, before, replay, path=str(path))
    assert len(before) == 256
    assert (found.outcome, found.polluter, found.confirmed) == ("polluter", polluter, True), (
        found,
        [(tmp_path / "scratch" / f"run-{i}" / "pytest.log").read_text()[-1500:] for i in (1, 2)],
    )
    assert len(found.runs) <= 12, found.runs
    # Each run kept a basetemp of its own, under the scratch folder it was given.
    assert sorted(p.name for p in (tmp_path / "scratch").iterdir()) == sorted(
        f"run-{i}" for i in range(1, len(found.runs) + 1)
    )
    assert all(
        (tmp_path / "scratch" / f"run-{i}" / "basetemp").is_dir()
        for i in range(1, len(found.runs) + 1)
    )


def test_the_command_reads_the_artifact_and_says_what_it_found(tmp_path: Path, capsys) -> None:
    from tools.ci import __main__ as ci

    _order(tmp_path, [VICTIM], seed=1, worker="gw0")
    _order(tmp_path, [VICTIM], seed=2, worker="gw0")
    assert ci.main(["bisect-pollution", VICTIM, "--orders", str(tmp_path)]) == 2
    assert "name --seed/--worker" in capsys.readouterr().err
