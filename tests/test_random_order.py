"""``tests/random_order.py``: the suite in an order drawn from a seed, and the order each
process ran.

The shuffle is a pure function of the collected ids and the seed, so the same seed gives the same
order in every xdist worker (xdist refuses a run whose workers collected different orders) and a
different seed a different one. Each process writes the order it actually ran, one test a line,
under its real worker id, so a failing order and the allocation behind it are kept. The planted
runs go through this suite's own ``conftest`` in a fresh pytest, as the nightly leg runs it.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import nested_pytest
import pytest
import random_order

_TESTS_DIR = Path(__file__).resolve().parent

IDS = [f"tests/test_{m}.py::test_{t}" for m in "abcd" for t in range(5)]


def _modules(order: list[str]) -> list[str]:
    return list(dict.fromkeys(i.split("::", 1)[0] for i in order))


def test_the_same_seed_gives_the_same_order() -> None:
    assert random_order.shuffled(IDS, 7) == random_order.shuffled(list(IDS), 7)


def test_a_different_seed_gives_a_different_order() -> None:
    orders = {tuple(random_order.shuffled(IDS, seed)) for seed in range(20)}
    assert len(orders) == 20


def test_it_is_a_permutation_that_keeps_each_modules_tests_together() -> None:
    order = random_order.shuffled(IDS, 3)
    assert sorted(order) == sorted(IDS)
    seen = [i.split("::", 1)[0] for i in order]
    runs = [m for n, m in enumerate(seen) if n == 0 or seen[n - 1] != m]
    assert len(runs) == len(set(runs)) == 4, runs


def test_both_the_modules_and_the_tests_within_them_move() -> None:
    moved_modules = any(_modules(random_order.shuffled(IDS, s)) != _modules(IDS) for s in range(5))
    moved_tests = any(
        [i for i in random_order.shuffled(IDS, s) if i.startswith("tests/test_a.py")]
        != [i for i in IDS if i.startswith("tests/test_a.py")]
        for s in range(5)
    )
    assert moved_modules and moved_tests


def test_the_shuffle_reads_no_global_random_state() -> None:
    import random

    random.seed(1)
    first = random_order.shuffled(IDS, 9)
    random.seed(2)
    assert random_order.shuffled(IDS, 9) == first


# ── through this suite's conftest ─────────────────────────────────────────────────────────────

_POLLUTER = """
    import os


    def test_sets_a_global():
        os.environ["PLANTED_ORDER_FLAG"] = "1"
"""

_VICTIM = """
    import os


    def test_needs_the_global():
        assert os.environ.get("PLANTED_ORDER_FLAG") == "1"
"""

_PLAIN = "\n".join(f"def test_{n}():\n    pass\n" for n in range(6))


def _pytest(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env.pop("PLANTED_ORDER_FLAG", None)
    env["PYTHONPATH"] = os.pathsep.join([str(_TESTS_DIR), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    return subprocess.run(
        [
            *nested_pytest.argv(tmp_path),
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "-p",
            "conftest",
            "--color=no",
            *args,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _plant(tmp_path: Path) -> None:
    for name, text in {
        "test_one.py": _PLAIN,
        "test_two.py": _PLAIN,
        "test_three.py": _PLAIN,
    }.items():
        (tmp_path / name).write_text(textwrap.dedent(text))


def _collected(tmp_path: Path, *args: str) -> list[str]:
    proc = _pytest(tmp_path, "-p", "no:xdist", "--collect-only", "-q", *args)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    return [line for line in proc.stdout.splitlines() if "::" in line]


def test_the_seed_sets_the_order_collection_gives_and_no_seed_leaves_it(tmp_path: Path) -> None:
    _plant(tmp_path)
    plain = _collected(tmp_path)
    seven = _collected(tmp_path, "--random-order-seed", "7")
    assert seven == _collected(tmp_path, "--random-order-seed", "7")
    assert seven != _collected(tmp_path, "--random-order-seed", "8")
    assert sorted(seven) == sorted(plain) and seven != plain
    assert seven == random_order.shuffled(plain, 7)


def test_every_worker_runs_its_share_of_one_order_and_says_which(tmp_path: Path) -> None:
    _plant(tmp_path)
    proc = _pytest(tmp_path, "-q", "-n", "2", "--random-order-seed", "11")
    out = proc.stdout[-3000:] + proc.stderr[-2000:]
    assert proc.returncode == 0, out
    assert "18 passed" in proc.stdout, out
    files = sorted((tmp_path / random_order.FOLDER).glob("order-11-*.jsonl"))
    assert {f.name for f in files} == {"order-11-gw0.jsonl", "order-11-gw1.jsonl"}, files
    ran: list[str] = []
    for path in files:
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        assert [line["position"] for line in lines] == list(range(1, len(lines) + 1))
        assert {line["worker"] for line in lines} == {path.stem.rsplit("-", 1)[1]}
        assert {line["seed"] for line in lines} == {11}
        ran += [line["nodeid"] for line in lines]
    assert sorted(ran) == sorted(_collected(tmp_path)), "every test ran once, on one worker"


def _failing_seed(ids: list[str], polluter: str, victim: str) -> int:
    for seed in range(1, 500):
        order = random_order.shuffled(ids, seed)
        if order.index(victim) < order.index(polluter):
            return seed
    raise AssertionError("no seed put the victim first")


def test_an_order_that_fails_leaves_its_order_and_the_pre_cleanup_snapshot(tmp_path: Path) -> None:
    """The planted victim passes only after the polluter: a seed that runs it first fails, and the
    run leaves the order it ran and the state-leak report taken before any fence restored."""
    (tmp_path / "test_a_polluter.py").write_text(textwrap.dedent(_POLLUTER))
    (tmp_path / "test_b_victim.py").write_text(textwrap.dedent(_VICTIM))
    # Named, not collected: each pytest this module starts loads the whole conftest.
    polluter = "test_a_polluter.py::test_sets_a_global"
    victim = "test_b_victim.py::test_needs_the_global"
    ids = [polluter, victim]
    seed = _failing_seed(ids, polluter, victim)
    proc = _pytest(tmp_path, "-q", "-p", "no:xdist", "--random-order-seed", str(seed))
    out = proc.stdout[-3000:] + proc.stderr[-2000:]
    assert proc.returncode == 1, out
    assert f"FAILED {victim}" in proc.stdout, out
    lines = [
        json.loads(line)
        for line in (tmp_path / random_order.FOLDER / f"order-{seed}-main.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [line["nodeid"] for line in lines] == random_order.shuffled(ids, seed)
    report = json.loads((tmp_path / "reports" / "state-leaks.json").read_text())
    assert report["tests"] == 2


def test_the_seed_names_itself_in_the_summary(tmp_path: Path) -> None:
    _plant(tmp_path)
    proc = _pytest(tmp_path, "-q", "-p", "no:xdist", "--random-order-seed", "5")
    assert "random order: seed 5" in proc.stdout, proc.stdout[-2000:]


def test_a_second_run_of_a_seed_replaces_its_order_file(tmp_path: Path) -> None:
    """A process's first test starts its order file afresh, so a rerun of the same seed in the
    same folder leaves that run's order, not two runs' end to end."""
    _plant(tmp_path)
    for _ in range(2):
        proc = _pytest(tmp_path, "-q", "-p", "no:xdist", "--random-order-seed", "5")
        assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    path = tmp_path / random_order.FOLDER / "order-5-main.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["position"] for line in lines] == list(range(1, 19))
    assert [line["nodeid"] for line in lines] == random_order.shuffled(_collected(tmp_path), 5)


@pytest.mark.parametrize("bad", ["x", "-1", "1.5"])
def test_a_seed_that_is_no_whole_number_from_zero_is_refused(tmp_path: Path, bad: str) -> None:
    _plant(tmp_path)
    proc = _pytest(tmp_path, "-q", "-p", "no:xdist", "--random-order-seed", bad)
    assert proc.returncode == 4, proc.stdout[-2000:] + proc.stderr[-2000:]


# ── a replay of one process's order ───────────────────────────────────────────────────────────


def test_an_order_file_and_a_plain_list_read_as_the_same_order() -> None:
    rows = [
        {"nodeid": n, "position": p, "worker": "gw1", "seed": 3} for p, n in [(2, "b"), (1, "a")]
    ]
    as_order = "\n".join(json.dumps(r) for r in rows) + "\n"
    assert random_order.replayed(as_order) == ["a", "b"], "an order file reads by position"
    assert random_order.replayed("a\n\nb\n") == ["a", "b"]


def test_a_replay_of_a_failing_order_file_fails_as_the_order_did(tmp_path: Path) -> None:
    """The leg's artifact is enough: the order file a failing worker left, replayed in one
    process, fails its victim again, and the two tests in the other order pass."""
    (tmp_path / "test_a_polluter.py").write_text(textwrap.dedent(_POLLUTER))
    (tmp_path / "test_b_victim.py").write_text(textwrap.dedent(_VICTIM))
    polluter = "test_a_polluter.py::test_sets_a_global"
    victim = "test_b_victim.py::test_needs_the_global"
    seed = _failing_seed([polluter, victim], polluter, victim)
    ran = _pytest(tmp_path, "-q", "-p", "no:xdist", "--random-order-seed", str(seed))
    assert ran.returncode == 1, ran.stdout[-3000:] + ran.stderr[-2000:]
    order = tmp_path / random_order.FOLDER / f"order-{seed}-main.jsonl"
    again = _pytest(tmp_path, "-q", "-p", "no:xdist", "--replay-order", str(order))
    assert again.returncode == 1 and f"FAILED {victim}" in again.stdout, again.stdout[-3000:]
    (tmp_path / "fixed.txt").write_text(f"{polluter}\n{victim}\n")
    fixed = _pytest(tmp_path, "-q", "-p", "no:xdist", "--replay-order", "fixed.txt")
    assert fixed.returncode == 0 and "2 passed" in fixed.stdout, fixed.stdout[-3000:]


def test_a_replay_runs_what_it_lists_in_its_order_and_refuses_what_it_cannot(
    tmp_path: Path,
) -> None:
    _plant(tmp_path)
    listed = ["test_two.py::test_3", "test_one.py::test_0", "test_two.py::test_1"]
    (tmp_path / "listed.txt").write_text("\n".join(listed) + "\n")
    assert _collected(tmp_path, "--replay-order", "listed.txt") == listed
    (tmp_path / "gone.txt").write_text("test_one.py::test_0\ntest_one.py::test_99\n")
    gone = _pytest(tmp_path, "-q", "-p", "no:xdist", "--replay-order", "gone.txt")
    assert gone.returncode == 4 and "did not collect" in gone.stderr, gone.stderr[-2000:]
    both = _pytest(
        tmp_path, "-q", "-p", "no:xdist", "--replay-order", "listed.txt", "--random-order-seed", "3"
    )
    assert both.returncode == 4 and "drop the seed" in both.stderr, both.stderr[-2000:]
