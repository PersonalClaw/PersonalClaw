"""A test that fakes the global ``subprocess.run`` gets the sandbox check its worker settled.

``tests/test_gateway_base_owner.py``'s cron pair failed alone on macOS: their fake
``subprocess.run`` became the sandbox-exec check's runner, the check did not answer, and their
script was refused (tests/sandbox_host_facts.py).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import nested_pytest
import pytest
import sandbox_host_facts

from personalclaw import sandbox

_ROOT = Path(__file__).resolve().parents[1]


def _a_fake(*args: object, **kwargs: object) -> None:
    raise AssertionError("a test's own fake subprocess.run")


def test_the_worker_found_the_sandbox_check_answered() -> None:
    """What every test below stands on: the settle got an answer to each check.

    Red on a runner where the check cannot answer (a namespace child or ``sandbox-exec`` that did
    not finish in time). That is not a flake: every sandboxed command in that worker is refused
    there too (``sandbox.UNCHECKED_REFUSAL``), and this is the test that says why."""
    assert None not in sandbox_host_facts.answers().values(), sandbox_host_facts.answers()
    assert sandbox_host_facts.backend() not in (None, sandbox.UNCHECKED)


def test_a_fake_subprocess_run_that_raises_gets_the_settled_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", _a_fake)
    assert sandbox.detect_backend() == sandbox_host_facts.backend()
    assert sandbox._probe_sandbox_exec() is sandbox_host_facts.answers()["_probe_sandbox_exec"]
    assert sandbox._probe_unshare() is sandbox_host_facts.answers()["_probe_unshare"]


def test_a_check_the_test_before_cleared_is_put_back(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox.reset_backend()  # what a test of the check leaves behind (tests/test_sandbox_probe.py)
    sandbox_host_facts.restore()  # what conftest does before the next test
    monkeypatch.setattr(subprocess, "run", _a_fake)
    assert sandbox.detect_backend() == sandbox_host_facts.backend()


def test_a_test_of_the_check_that_clears_it_gets_a_fresh_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox.reset_backend()
    monkeypatch.setattr(subprocess, "run", _a_fake)
    expected = None if sys.platform == "darwin" else False
    assert sandbox._probe_sandbox_exec() is expected  # asked again, through the fake


def test_the_cron_pair_passes_alone_in_a_new_process(tmp_path: Path) -> None:
    """The failure as it was found: the file alone, in a new process, at -n 0."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    done = subprocess.run(
        [
            *nested_pytest.argv(tmp_path),
            "-q",
            "--color=no",
            "-p",
            "no:cacheprovider",
            "-n",
            "0",
            "tests/test_gateway_base_owner.py",
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,  # inside the suite's 30 s cap; about 3 s on a Mac
    )
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]


#: Loaded early (``-p``), before conftest: the user database names ``PC_PLANTED_REAL_HOME`` as this
#: account's home, so the real-home guard watches it, and the sandbox reads this computer as Linux,
#: so the settle runs Linux's namespace check, which starts a PersonalClaw child.
_LINUX_UNDER_A_PLANTED_HOME = """
import os, pwd, sys

_real = pwd.getpwuid


def _planted(uid):
    fields = list(_real(uid))
    fields[5] = os.environ["PC_PLANTED_REAL_HOME"]
    return pwd.struct_passwd(fields)


pwd.getpwuid = _planted

from personalclaw import sandbox


class _Linux:
    platform = "linux"

    def __getattr__(self, name):
        return getattr(sys, name)


sandbox.sys = _Linux()
"""


def test_the_settle_runs_where_home_is_the_real_one(tmp_path: Path) -> None:
    """Main's CI contract runs the suite with ``$HOME`` the runner's own, and there the real-home
    guard refused the settle's namespace child, so conftest did not import (run 40825648136). As
    there: ``$HOME`` is what the user database names, ``PERSONALCLAW_HOME`` is unset, and the
    contract's ``GITHUB_ACTIONS`` and ``CI`` are set; and the sandbox reads the computer as Linux.
    """
    real = tmp_path / "real-home"
    real.mkdir()
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "pc_linux_under_a_planted_home.py").write_text(_LINUX_UNDER_A_PLANTED_HOME)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env.update(
        HOME=str(real),
        PC_PLANTED_REAL_HOME=str(real),
        GITHUB_ACTIONS="true",
        CI="true",
        PYTHONPATH=os.pathsep.join([str(plugins), str(_ROOT / "src"), str(_ROOT)]),
    )
    done = subprocess.run(
        [
            *nested_pytest.argv(tmp_path),
            "-q",
            "--color=no",
            "-p",
            "no:cacheprovider",
            "-p",
            "pc_linux_under_a_planted_home",
            "--collect-only",
            "tests/test_workref.py",
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
    assert not (real / ".personalclaw").exists()
