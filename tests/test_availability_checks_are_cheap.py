"""An availability check answers from metadata: it never runs the code it is checking for.

Two measured offenders. Sentence Transformers' hook ran ``import sentence_transformers`` to
learn whether it was installed — 171.8 s cold in the container, and the library then stayed
resident. kiro-cli's and gemini-cli's hooks each paid a cold ``npm root -g`` (5-7 s) on every
resolution, through core's own ACP resolver.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


def _plant_package(root: Path, name: str) -> Path:
    marker = root / f"{name}.imported"
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
    return marker


def test_the_sdk_check_finds_a_package_without_running_it(tmp_path, monkeypatch):
    from personalclaw.sdk.availability import missing_modules, modules_installed

    marker = _plant_package(tmp_path, "heavy_ml_stack_zz")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert modules_installed("heavy_ml_stack_zz")
    assert not marker.exists(), "the check executed the package it was only meant to find"
    assert "heavy_ml_stack_zz" not in sys.modules
    assert missing_modules("heavy_ml_stack_zz", "no_such_pkg_zz") == ["no_such_pkg_zz"]


def test_a_dotted_name_with_a_missing_parent_reads_missing_not_raised():
    from personalclaw.sdk.availability import missing_modules, modules_installed

    assert missing_modules("no_such_parent_zz.child") == ["no_such_parent_zz.child"]
    assert not modules_installed("no_such_parent_zz.child")


@pytest.fixture
def fake_npm(monkeypatch):
    from personalclaw.acp import cli_resolve

    calls: list[list[str]] = []
    answers = {"stdout": "/opt/npm-global/lib/node_modules\n"}

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=answers["stdout"], stderr="")

    monkeypatch.setattr(cli_resolve.shutil, "which", lambda name, **kw: f"/usr/bin/{name}")
    monkeypatch.setattr(cli_resolve.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_resolve, "_NPM_GLOBAL_ROOTS", {}, raising=False)
    return cli_resolve, calls, answers


def test_npm_root_is_asked_once_per_process(fake_npm):
    cli_resolve, calls, _answers = fake_npm
    cli_resolve._npm_root_global_bin()
    cli_resolve._npm_root_global_bin()
    npm_runs = [c for c in calls if c[1:] == ["root", "-g"]]
    assert len(npm_runs) == 1, f"`npm root -g` ran {len(npm_runs)} times for one npm"


def test_a_failed_npm_root_is_asked_again(fake_npm):
    cli_resolve, calls, answers = fake_npm
    answers["stdout"] = ""
    cli_resolve._npm_root_global_bin()
    answers["stdout"] = "/opt/npm-global/lib/node_modules\n"
    cli_resolve._npm_root_global_bin()
    assert len([c for c in calls if c[1:] == ["root", "-g"]]) == 2
