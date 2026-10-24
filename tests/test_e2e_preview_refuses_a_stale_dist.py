"""The browser run refuses a stale web build before any test starts (#3939).

Every Playwright spec runs against the preview server, which serves the one `web/dist` the run's
build made. `web/playwright.config.ts` starts that server as `FRESHNESS_CHECK && npm run preview
...`: a dist built from other sources than the checked-out `web/`, or one with no stamp to say
what it was built from (`--require-stamp`), fails the check, the preview never starts, and the
run fails on its webServer before its first test.

`tests/test_spa_dist_freshness.py` holds the check's verdicts. This holds the config to running
the check first and to the `&&` that keeps the preview from starting after a refusal. The
config's own command runs here, in a planted tree the check reads as it reads the repository,
with `npm run preview` standing in for a command that marks the preview started.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_spa_dist_freshness import _build, _fake_repo

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "web" / "playwright.config.ts"

#: The preview's start, which this test stands in for.
_PREVIEW = "npm run preview -- --port ${PORT} --strictPort"


def _preview_command() -> str:
    """The preview webServer's command from the config, its freshness check spelled out and the
    preview itself replaced by a marker file."""
    source = CONFIG.read_text(encoding="utf-8")
    check = re.search(r"const FRESHNESS_CHECK =\s*'([^']+)'", source)
    assert check, "web/playwright.config.ts defines no FRESHNESS_CHECK string"
    commands = re.findall(r"command: `([^`]+)`", source)
    preview = [c for c in commands if "npm run preview" in c]
    assert preview == ["${FRESHNESS_CHECK} && " + _PREVIEW], (
        "the preview webServer must run the freshness check first and start only if it passes: "
        f"{preview}"
    )
    return (
        preview[0]
        .replace("${FRESHNESS_CHECK}", check.group(1))
        .replace(_PREVIEW, "touch preview-started")
    )


@pytest.fixture
def planted(tmp_path: Path) -> Path:
    """A repo-shaped tree with the real freshness script, whose `../.venv/bin/python`, the one
    the config's command runs, starts this interpreter by its own path: a virtual environment it
    belongs to stays its own. A bare symlink to it would start the base interpreter, which has
    none of this tree's packages, and the script's import would fail before it checked anything."""
    root = _fake_repo(tmp_path)
    (root / "scripts").mkdir()
    shutil.copy2(REPO / "scripts" / "spa_dist_freshness.py", root / "scripts")
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    return root


def _start(root: Path) -> subprocess.CompletedProcess[str]:
    # The script reads this tree's `personalclaw`: the planted tree's `src/personalclaw` is a
    # folder for the built dashboard, not the package, and the script puts it first on its path.
    path = [str(REPO / "src"), *filter(None, [os.environ.get("PYTHONPATH")])]
    return subprocess.run(
        ["sh", "-c", _preview_command()],
        cwd=root / "web",
        env={**os.environ, "PYTHONPATH": os.pathsep.join(path)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_a_dist_built_from_the_checked_out_sources_starts_the_preview(planted: Path) -> None:
    _build(planted)
    done = _start(planted)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (planted / "web" / "preview-started").exists()


def test_a_source_edited_after_the_build_stops_the_run_before_the_preview(planted: Path) -> None:
    _build(planted)
    (planted / "web" / "src" / "App.tsx").write_text(
        "export const App = () => 'edited';\n", encoding="utf-8"
    )
    done = _start(planted)
    assert done.returncode != 0
    assert "STALE" in done.stderr, done.stdout + done.stderr
    assert not (planted / "web" / "preview-started").exists(), "the preview started anyway"


def test_a_dist_with_no_stamp_stops_the_run_before_the_preview(planted: Path) -> None:
    """A dist built outside `make web-build` says nothing of what it was built from, so the run
    cannot prove it current and does not serve it."""
    dist = planted / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<script src=/assets/x.js></script>", encoding="utf-8")
    done = _start(planted)
    assert done.returncode != 0
    assert "UNSTAMPED" in done.stderr, done.stdout + done.stderr
    assert not (planted / "web" / "preview-started").exists(), "the preview started anyway"
