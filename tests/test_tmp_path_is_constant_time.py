"""`tmp_path` is the suite's own constant-time fixture, and keeps the shape tests rely on.

`tests/conftest.py` names each test's folder from a counter instead of pytest's `mktemp()`, which
scans the whole temporary folder on every call. These tests hold what the rest of the suite reads
from that folder: it is a new, empty, owner-only, direct child of the run's temporary folder, and
it carries the test's name.
"""

from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest

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
