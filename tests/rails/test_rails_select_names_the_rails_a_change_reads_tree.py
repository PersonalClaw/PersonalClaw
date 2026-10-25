"""``rails select`` names the rails that two changes on main broke, from a real record (#3920).

On one day two lanes broke a rail on main that their own runs never ran:

- a refusal in ``src/personalclaw/cli_doctor.py`` printed on stdout, against
  ``tests/rails/test_a_cli_refusal_prints_on_stderr_tree.py``;
- a patch in ``tests/test_app_code.py`` outlived its test, against
  ``tests/rails/test_a_test_undoes_only_its_own_patches.py``.

This runs those two rails in a child pytest that writes a rails leg's report, so the recorder
(``tests/rails_reads.py``) records them on the real tree, then asks the selector about each
change. Each change must select its rail. A path neither rail reads must select neither. A
package module the harness imports selects only the rail that reads it, and the harness's own
conftest selects both.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess

import nested_pytest
import pytest
import rails_reads
import source_index
from rails_repo import REPO

from tools.ci import rails_select

STDERR_RAIL = "tests/rails/test_a_cli_refusal_prints_on_stderr_tree.py"
PATCHES_RAIL = "tests/rails/test_a_test_undoes_only_its_own_patches.py"

#: Each day's break: the change, and the rail it broke.
BREAKS = {
    "src/personalclaw/cli_doctor.py": STDERR_RAIL,
    "tests/test_app_code.py": PATCHES_RAIL,
}

#: A package module conftest's registry fences import for every test: not the harness's own, so
#: it selects only the rail that reads it (the stderr rail walks the package).
FENCED = "src/personalclaw/context.py"

#: Paths neither rail reads.
UNREAD = ("docs/architecture/app-platform.md", "web/src/main.tsx")


@pytest.fixture(scope="module")
def recorded(tmp_path_factory) -> rails_select.Record:
    out = tmp_path_factory.mktemp("rails-reads")
    # The child is a run of its own, not this worker's: no xdist or pytest variable of ours.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env[rails_reads.FORCE] = "1"  # a record made here, on any run
    done = subprocess.run(
        [
            *nested_pytest.argv(out),
            "-q",
            "-n",
            "0",
            "-p",
            "no:cacheprovider",
            STDERR_RAIL,
            PATCHES_RAIL,
            f"--junitxml={out / 'junit-rails-plant.xml'}",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,  # inside the 100 s mark of the test that builds this fixture
        check=False,
    )
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
    data = json.loads((out / "rails-reads-plant.json").read_text(encoding="utf-8"))
    return rails_select.load(data, "the plant's record")


@pytest.mark.timeout(100)
def test_each_break_selects_the_rail_it_broke_and_an_unread_path_neither(recorded) -> None:
    # The plants name real files the rails read: the doctor is a reporter the stderr rail
    # names, and the app-code test is a module the patches rail parses.
    assert "def _doctor" in source_index.text(REPO / "src/personalclaw/cli_doctor.py")
    assert (REPO / "tests/test_app_code.py").is_file()
    tree = rails_select._tree(REPO, "HEAD")
    assert tree is not None and set(BREAKS) | set(UNREAD) | {FENCED} <= tree
    conftest = (REPO / "tests/conftest.py").read_text(encoding="utf-8")
    assert "import personalclaw.context as ctx" in conftest
    now = recorded.recorded_at + datetime.timedelta(minutes=1)

    def chosen(path: str) -> dict[str, str]:
        found = rails_select.select(
            [path],
            [STDERR_RAIL, PATCHES_RAIL],
            recorded,
            now=now,
            tree=tree,
            exists=lambda p: (REPO / p).exists(),
        )
        return found

    for path, rail in BREAKS.items():
        assert chosen(path).get(rail) == f"reads {path}", (path, chosen(path))
    for path in UNREAD:
        assert chosen(path) == {}, (path, chosen(path))
    assert chosen(FENCED) == {STDERR_RAIL: f"reads {FENCED}"}
    assert set(chosen("tests/conftest.py")) == {STDERR_RAIL, PATCHES_RAIL}
