"""`tmp_path` is the suite's own constant-time fixture, and keeps the shape tests rely on.

`tests/conftest.py` names each test's folder from a counter instead of pytest's `mktemp()`, which
scans the whole temporary folder on every call. These tests hold what the rest of the suite reads
from that folder: it is a new, empty, owner-only, direct child of the run's temporary folder, and
it carries the test's name.

They also hold its cost. A worker's temporary folder gains a folder for every test it runs, so a
`tmp_path` that scanned it got dearer with every test before it: the last tenth of a long worker
run cost about twice the first. The fixture's own function is driven here against a folder that
is empty and then holds a CI worker's worth of others, and the same measure, pointed at the
numbering `mktemp()` uses, is shown to see the growth.
"""

from __future__ import annotations

import re
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import conftest
import pytest
from _pytest.pathlib import make_numbered_dir

#: The fixture's own function, called with what it reads from pytest's request and factory.
TMP_PATH = conftest.tmp_path._get_wrapped_function()

#: Folders a run's temporary folder already holds when the cost is read the second time: about a
#: third of a CI worker's share of the suite.
HELD = 3000

#: A folder made beside :data:`HELD` others may cost twice what it costs in an empty folder, plus
#: this much, before it counts as a scan. Measured on a laptop: the fixture costs about 0.1 ms
#: either way; `mktemp()`'s numbering costs 0.36 ms empty and 1.65 ms beside 3,000 folders
#: (3.5 ms beside 8,000), so a scan is past the bound by more than half again.
SLACK = 0.0003

_SEEN: list[Path] = []


def test_tmp_path_is_a_new_child_of_the_run_s_temporary_folder(tmp_path, tmp_path_factory):
    assert tmp_path.parent == tmp_path_factory.getbasetemp()
    assert tmp_path.is_dir()
    assert list(tmp_path.iterdir()) == []
    assert stat.S_IMODE(tmp_path.stat().st_mode) & 0o077 == 0


def test_tmp_path_is_numbered_and_carries_the_test_s_name(tmp_path, request):
    name = request.node.name
    assert len(name) > 30, "the name must be long enough to show the cut"
    assert re.fullmatch(rf"t\d+-{re.escape(name[:30])}", tmp_path.name), tmp_path.name


@pytest.mark.parametrize("value", ["a/b", "x y[z]"])
def test_a_name_with_separators_stays_one_folder_deep(tmp_path, tmp_path_factory, value):
    assert tmp_path.parent == tmp_path_factory.getbasetemp()
    assert re.fullmatch(r"t\d+-\w{1,30}", tmp_path.name), tmp_path.name


@pytest.mark.parametrize("attempt", range(3))
def test_every_test_gets_a_folder_of_its_own(tmp_path, attempt):
    assert tmp_path not in _SEEN
    _SEEN.append(tmp_path)


def _median_seconds(make, runs: int = 25) -> float:
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        make()
        times.append(time.perf_counter() - started)
    return sorted(times)[runs // 2]


def _scans(empty: float, held: float) -> bool:
    return held >= 2 * empty + SLACK


def _cost_empty_then_held(base: Path, make) -> tuple[float, float]:
    """The median cost of ``make()`` in an empty *base*, then with :data:`HELD` folders in it."""
    empty = _median_seconds(make)
    for number in range(HELD):
        (base / f"t{number}-an_earlier_test").mkdir()
    return empty, _median_seconds(make)


def test_a_test_s_folder_costs_the_same_however_many_the_run_already_holds(tmp_path):
    base = tmp_path / "run"
    base.mkdir()
    factory = SimpleNamespace(getbasetemp=lambda: base)
    request = SimpleNamespace(node=SimpleNamespace(name="test_a_later_test"))
    empty, held = _cost_empty_then_held(base, lambda: TMP_PATH(request, factory))
    assert not _scans(empty, held), (
        f"a tmp_path costs {held * 1000:.2f} ms beside {HELD} folders and "
        f"{empty * 1000:.2f} ms in an empty one: it grows with the tests the worker already ran"
    )


def test_the_measure_sees_mktemp_grow_with_the_folders_the_run_holds(tmp_path):
    """The positive control: pointed at pytest's own numbering, the same measure sees the growth."""
    base = tmp_path / "run"
    base.mkdir()
    empty, held = _cost_empty_then_held(
        base, lambda: make_numbered_dir(root=base, prefix="test_a_later_test", mode=0o700)
    )
    assert _scans(empty, held), (
        f"mktemp's numbering costs {held * 1000:.2f} ms beside {HELD} folders and "
        f"{empty * 1000:.2f} ms in an empty one: the measure no longer sees a scan"
    )
