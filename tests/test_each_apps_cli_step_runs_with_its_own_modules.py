"""Each app's CLI step runs with its own modules, however many apps run theirs in one process.

``personalclaw setup`` and ``doctor`` run every installed app's step in turn, in one process, and a
step imports its own modules by top-level name. Many apps ship a module of the same name (every
provider app has a ``provider.py``), so the second app's ``from provider import …`` found the first
app's module in ``sys.modules`` and ran its step with it. Both apps here are real installed apps in
a scratch home: a ``provider.py`` and a ``helpers.py`` each, imported by name by each step, the way
``docs-slides``' step imports its provider.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from personalclaw import app_cli, app_code
from personalclaw.apps import manager

APPS = ("alpha-app", "beta-app")
NAMES = ("provider", "helpers")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A scratch home with both apps installed, enabled, and each declaring a setup step and a
    doctor probe."""
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    from personalclaw.config import loader as cfg_loader

    monkeypatch.setattr(cfg_loader, "config_dir", lambda: tmp_path)
    for name in APPS:
        _install(tmp_path, name)
    before = {name: sys.modules[name] for name in NAMES if name in sys.modules}
    yield tmp_path
    for name in APPS:
        app_code.release(name)
    for name in NAMES:
        sys.modules.pop(name, None)
    sys.modules.update(before)


def _install(root: Path, name: str) -> None:
    folder = root / "apps" / name
    folder.mkdir(parents=True)
    (folder / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": True}), encoding="utf-8"
    )
    manifest = {
        "name": name,
        "version": "1.0.0",
        "displayName": name,
        "description": name,
        "cli": {"setup": "app_cli:setup", "doctor": "app_cli:doctor"},
    }
    (folder / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "provider.py").write_text(
        "import pathlib\n"
        f"WHO = {name!r}\n"
        "with open(pathlib.Path(__file__).with_name('imported'), 'a') as fh:\n"
        "    fh.write('provider\\n')\n",
        encoding="utf-8",
    )
    (folder / "helpers.py").write_text(f"def who():\n    return {name!r}\n", encoding="utf-8")
    (folder / "app_cli.py").write_text(
        "from personalclaw.sdk.cli import DoctorLine\n"
        "from provider import WHO\n"
        "\n"
        "\n"
        "def setup(ctx):\n"
        "    from helpers import who\n"
        "    ctx.print(f'setup ran as {WHO}, helped by {who()}')\n"
        "\n"
        "\n"
        "def doctor():\n"
        "    from provider import WHO as now\n"
        "    return [DoctorLine('who', 'ok', f'{WHO} {now}')]\n",
        encoding="utf-8",
    )


def _imported(home: Path, name: str) -> int:
    """How many times *name*'s ``provider.py`` has run."""
    marker = home / "apps" / name / "imported"
    return marker.read_text(encoding="utf-8").count("provider") if marker.exists() else 0


def test_each_setup_step_imports_its_own_module_of_a_name_every_app_ships(home, capsys):
    assert app_cli.run_app_setup_steps() == []

    said = capsys.readouterr().out.splitlines()
    assert said == [
        "setup ran as alpha-app, helped by alpha-app",
        "setup ran as beta-app, helped by beta-app",
    ]
    assert [_imported(home, name) for name in APPS] == [1, 1]


def test_each_doctor_probe_does_too_after_every_setup_step_ran(home, capsys):
    """The doctor probes run after the setup steps in the same process: each app finds its own
    modules again, the ones its setup step loaded, rather than a second copy or another app's."""
    app_cli.run_app_setup_steps()
    capsys.readouterr()

    assert app_cli.run_app_doctor_probes() == []

    said = [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert said == [
        "alpha-app",
        "✅ who  alpha-app alpha-app",
        "beta-app",
        "✅ who  beta-app beta-app",
    ]
    assert [_imported(home, name) for name in APPS] == [1, 1]


def test_no_step_leaves_its_modules_for_what_runs_after_it(home, capsys):
    """After the steps, no module of either app is in ``sys.modules`` under its plain name, and
    one the process already had there before them is back, as it was."""
    elsewhere = sys.modules.setdefault("helpers", type(sys)("helpers"))

    app_cli.run_app_setup_steps()

    apps = str(home / "apps")
    left = [n for n, m in sys.modules.items() if str(getattr(m, "__file__", "")).startswith(apps)]
    assert left == []
    assert sys.modules["helpers"] is elsewhere


def test_an_app_released_since_its_last_step_loads_its_modules_afresh(home, capsys):
    app_cli.run_app_setup_steps()
    app_code.release("alpha-app")

    app_cli.run_app_setup_steps(only_app="alpha-app")

    assert _imported(home, "alpha-app") == 2
    assert capsys.readouterr().out.splitlines()[-1] == "setup ran as alpha-app, helped by alpha-app"
