"""The skill overlay store looks in the skills folder the loader names at the time it is asked.

`skills.overlays` imported `skills_dir` from the loader by name, so the module kept whatever that
name was bound to when it was first imported. A test that pointed the loader's `skills_dir` at a
folder of its own and then called a tool that imported the overlay store for the first time left
the store reading that test's folder for the rest of the process. Every later test that accepted a
refinement then found the one the earlier folder still held, and the queue's anti-flood rail
refused its proposal ("already took a refinement in the last 24h"): seven tests of
`test_skill_proposal_cycle.py` failed with `None is not None` on one CI worker, and passed alone.

The store now asks the loader module each time, as the proposal queue already does.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import nested_pytest
import pytest

ROOT = Path(__file__).resolve().parent.parent

#: A test that points the loader at a folder of its own and imports the overlay store for the first
#: time (through the `skill_invoke` tool), then the proposal-cycle file: its early tests accept a
#: refinement, which the stale store wrote into the first test's folder, and the later ones then
#: found it there whatever home they had.
LEAKER = "tests/test_skill_progressive_disclosure.py::test_skill_invoke_records_use"
VICTIM = "tests/test_skill_proposal_cycle.py"


def test_the_overlay_folder_is_the_loaders_skills_folder_when_it_is_asked(tmp_path, monkeypatch):
    """🔴 Red before: the store kept the `skills_dir` it was imported with."""
    from personalclaw.skills import loader, overlays

    monkeypatch.setattr(loader, "skills_dir", lambda: tmp_path / "one")
    assert overlays.overlays_dir() == tmp_path / "one" / ".overlays"
    monkeypatch.setattr(loader, "skills_dir", lambda: tmp_path / "two")
    assert overlays.overlays_dir() == tmp_path / "two" / ".overlays"


@pytest.mark.timeout(60)
def test_a_test_that_moved_the_skills_folder_leaves_the_next_ones_overlays_alone(tmp_path):
    """🔴 Red before: the leaking test and then the proposal-cycle file, in a process of their own,
    the overlay store first imported by the leaking test. Seven of the file's tests had their
    proposal refused by a refinement an earlier one had accepted."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    proc = subprocess.run(
        [
            *nested_pytest.argv(tmp_path),
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "-p",
            "no:xdist",
            "-q",
            "--color=no",
            LEAKER,
            VICTIM,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=110,
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    assert proc.returncode == 0 and "failed" not in tail and "passed" in tail, (
        proc.stdout[-3000:] + proc.stderr[-2000:]
    )
