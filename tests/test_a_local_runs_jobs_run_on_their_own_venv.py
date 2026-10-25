"""A job run by ``tools.ci run-job`` runs on its own venv, in a new checkout and in a contributor's.

Gate proof 40143285105 ran ``make gate`` as a contributor runs it, in a new checkout on a GitHub
runner. A local run's venv is in the work folder (``.local/run-job/cache/venv-<python>``), and two
things a job runs looked for one in the checkout's ``.venv`` instead:
- the exemplar smokes (``harness/exemplars/resolve_py.sh``) found no Python, and six failed;
- the browser runs' gateway command (``web/playwright.config.ts``) found no ``personalclaw``, and
  every e2e job's web server exited 127.
In a checkout with a ``.venv`` of its own, both would have run that, the contributor's, not the
job's locked one.

run-job now gives every step of a venv job ``PERSONALCLAW_PY``, its venv's interpreter
(``tools/ci/jobs.py`` ``JOB_PYTHON_ENV``), and both take it first. The plants run both for real in
a new checkout, with the environment run-job makes for the python-suite job, beside a decoy
``.venv`` that exits 3 if anything runs it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci import catalogue, jobs

REPO = Path(__file__).resolve().parents[1]
_DECOY = 3


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A new checkout: a git repository with this tree's harness and no venv of its own."""
    root = tmp_path / "checkout"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "harness").symlink_to(REPO / "harness", target_is_directory=True)
    (root / "web").mkdir()
    return root


def _job_env(checkout: Path) -> dict[str, str]:
    """What run-job gives the python-suite job's steps in *checkout*, locally, with its venv made:
    an interpreter and a ``personalclaw`` that are this test's own Python under another name."""
    text = (REPO / catalogue.CATALOGUE).read_text(encoding="utf-8")
    job = catalogue.load(text).job("python-suite")
    host = jobs.Host(root=checkout, family="ubuntu-", path=os.environ["PATH"])
    given = jobs._cache_env(job, host)
    python = Path(given[jobs.JOB_PYTHON_ENV])
    assert python.is_relative_to(checkout / jobs.WORK), python  # the work folder's, not .venv
    python.parent.mkdir(parents=True)
    _script(python, f'exec "{sys.executable}" "$@"')
    _script(python.parent / "personalclaw", f'exec "{sys.executable}" -m personalclaw "$@"')
    return {jobs.JOB_PYTHON_ENV: str(python)}


def _decoy(checkout: Path) -> None:
    """A contributor's own .venv: anything that runs from it exits 3."""
    for name in ("python", "personalclaw"):
        _script(checkout / ".venv" / "bin" / name, f"echo the decoy venv; exit {_DECOY}")


def _script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _env(**extra: str) -> dict[str, str]:
    """A step's environment, as run-job builds it: from nothing but PATH, HOME and what it gives,
    with this tree's sources first, as the suite's own interpreter has them."""
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
    env["PYTHONPATH"] = str(REPO / "src")
    env.update(extra)
    return env


def _smoke(checkout: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    smoke = checkout / "harness" / "exemplars" / "slice_0" / "smoke.sh"
    return subprocess.run(
        ["bash", str(smoke)], cwd=checkout, env=env, capture_output=True, text=True, timeout=60
    )


def test_a_new_checkouts_exemplar_runs_on_the_jobs_python(checkout: Path) -> None:
    given = _job_env(checkout)
    # Without it, a new checkout has no Python to run the exemplar on, as the gate proof found.
    refused = _smoke(checkout, _env())
    assert refused.returncode == 1 and "no usable Python" in refused.stderr, refused.stderr
    _decoy(checkout)
    # And a contributor's checkout runs it on the contributor's own venv.
    decoy = _smoke(checkout, _env())
    assert decoy.returncode == _DECOY, (decoy.stdout, decoy.stderr)
    # With what run-job gives, the job's own Python runs it, beside the contributor's .venv.
    ran = _smoke(checkout, _env(**given))
    assert ran.returncode == 0, (ran.stdout, ran.stderr)
    assert "PASS" in ran.stdout, ran.stdout


def _gateway_line() -> str:
    """The gateway command's line that picks the ``personalclaw`` it starts, as the config
    writes it: its single-quoted pieces, joined."""
    config = (REPO / "web" / "playwright.config.ts").read_text(encoding="utf-8")
    block = re.search(r"\n(\s*'if \[ -n \"\$\{PERSONALCLAW_PY:-\}\" \].*?fi',)\n", config, re.S)
    assert block, "the gateway command no longer picks its personalclaw from PERSONALCLAW_PY"
    return "".join(re.findall(r"'([^']*)'", block.group(1)))


def _gateway(checkout: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """That line, run where the config runs its command (``web/``), then ``"$PC" --version`` in
    place of the gateway's own arguments."""
    script = f'{_gateway_line()}\nexec "$PC" --version'
    return subprocess.run(
        ["sh", "-c", script],
        cwd=checkout / "web",
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a_new_checkouts_gateway_starts_from_the_jobs_venv(checkout: Path) -> None:
    given = _job_env(checkout)
    _decoy(checkout)
    started = _gateway(checkout, _env(**given))
    assert started.returncode == 0, (started.stdout, started.stderr)
    assert started.stdout.startswith("personalclaw "), started.stdout
    # By hand, without run-job, the browser specs still run on the checkout's own .venv.
    by_hand = _gateway(checkout, _env())
    assert by_hand.returncode == _DECOY, (by_hand.stdout, by_hand.stderr)


def test_a_job_venv_with_no_personalclaw_is_refused_not_passed_over(checkout: Path) -> None:
    _decoy(checkout)
    python = checkout / "elsewhere" / "bin" / "python"
    _script(python, f'exec "{sys.executable}" "$@"')
    refused = _gateway(checkout, _env(**{jobs.JOB_PYTHON_ENV: str(python)}))
    assert refused.returncode == 1, (refused.stdout, refused.stderr)
    assert "PERSONALCLAW_E2E_FATAL" in refused.stderr and str(python) in refused.stderr
