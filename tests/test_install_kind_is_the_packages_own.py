"""Which install this is comes from the package that is running, never from where it was started.

🔴 The CLI walked up from the working directory for a PersonalClaw checkout (or read the path
``personalclaw setup`` had saved from such a walk) and exported what it found as
``PERSONALCLAW_PROJECT_DIR``; ``detect_install_kind()`` then called any tree there with a ``.git``
a git install. So a ``uv tool`` install started inside a checkout was "git", and "Update &
Restart" ran ``git fetch``/``git checkout`` on that tree while the wheel the gateway runs from
stayed where it was. The unattended auto-update did the same, with nobody watching.

Each test places the running package with ``self_update._package_dir`` (a wheel's
``site-packages`` copy, or a checkout's ``src/personalclaw``) and builds the tree around it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from personalclaw import self_update


def _checkout(root: Path) -> Path:
    """A PersonalClaw source checkout at *root*: the two layout markers and a ``.git``."""
    (root / "src" / "personalclaw").mkdir(parents=True)
    (root / "src" / "personalclaw" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'personalclaw'\n", encoding="utf-8")
    (root / ".git").mkdir()
    return root


def _wheel(root: Path) -> Path:
    """Where a wheel install's package lives: a copy inside ``site-packages``."""
    pkg = root / "lib" / "python3.13" / "site-packages" / "personalclaw"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    return pkg


@pytest.fixture(autouse=True)
def _no_kind_from_the_environment(monkeypatch):
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)


def _runs_from(monkeypatch, package_dir: Path) -> None:
    """Make *package_dir* the directory the running ``personalclaw`` was imported from."""
    monkeypatch.setattr(self_update, "_package_dir", lambda: package_dir, raising=False)


def _start_the_cli(monkeypatch) -> None:
    """Run the CLI's own start-up, the part before any command, as ``personalclaw --version``."""
    from personalclaw import cli

    monkeypatch.setattr(sys, "argv", ["personalclaw", "--version"])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 0


def test_a_wheel_started_inside_a_checkout_is_not_a_git_install(tmp_path, monkeypatch, capsys):
    checkout = _checkout(tmp_path / "PersonalClaw")
    _runs_from(monkeypatch, _wheel(tmp_path / "tool-venv"))
    monkeypatch.chdir(checkout / "src")

    _start_the_cli(monkeypatch)

    assert os.environ.get("PERSONALCLAW_PROJECT_DIR") is None, "the checkout became the install"
    assert self_update.detect_install_kind() == "pip"


def test_a_path_setup_saved_is_not_an_install(tmp_path, monkeypatch, capsys):
    """An earlier ``personalclaw setup`` run inside a checkout saved it to ``<home>/project_dir``,
    and every later start read it back, from any directory."""
    from personalclaw.config import loader as config_loader

    checkout = _checkout(tmp_path / "PersonalClaw")
    (config_loader.config_dir() / "project_dir").write_text(f"{checkout}\n", encoding="utf-8")
    _runs_from(monkeypatch, _wheel(tmp_path / "tool-venv"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    _start_the_cli(monkeypatch)

    assert os.environ.get("PERSONALCLAW_PROJECT_DIR") is None
    assert self_update.detect_install_kind() == "pip"


def test_an_inherited_project_dir_does_not_make_a_wheel_a_git_install(tmp_path, monkeypatch):
    """A ``PERSONALCLAW_PROJECT_DIR`` from a shell profile, or the one a desktop shell sets for
    its resources, says where resources are. It is not the install."""
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(_checkout(tmp_path / "PersonalClaw")))
    _runs_from(monkeypatch, _wheel(tmp_path / "tool-venv"))

    assert self_update.detect_install_kind() == "pip"


def test_the_checkout_the_package_runs_from_is_a_git_install(tmp_path, monkeypatch):
    checkout = _checkout(tmp_path / "PersonalClaw")
    _runs_from(monkeypatch, checkout / "src" / "personalclaw")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert self_update.detect_install_kind() == "git"


def test_the_cli_names_the_packages_checkout_from_any_directory(tmp_path, monkeypatch, capsys):
    checkout = _checkout(tmp_path / "PersonalClaw")
    _runs_from(monkeypatch, checkout / "src" / "personalclaw")
    other = _checkout(tmp_path / "another-checkout")
    monkeypatch.chdir(other)

    _start_the_cli(monkeypatch)

    assert os.environ.get("PERSONALCLAW_PROJECT_DIR") == str(checkout)


def test_the_monorepo_layout_is_found_one_level_up(tmp_path, monkeypatch):
    """``<repo>/PersonalClaw`` holds the package; the ``.git`` is at ``<repo>``."""
    repo = tmp_path / "workspace"
    package_root = repo / "PersonalClaw"
    (package_root / "src" / "personalclaw").mkdir(parents=True)
    (package_root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (repo / ".git").mkdir()
    _runs_from(monkeypatch, package_root / "src" / "personalclaw")

    assert self_update.detect_install_kind() == "git"


class _Proc:
    returncode = 1

    async def communicate(self):
        return (b"", b"stop here")


@pytest.mark.asyncio
async def test_the_update_check_fetches_in_the_packages_checkout(tmp_path, monkeypatch):
    """The start-up used to export the working directory's checkout, and the check fetched in
    whatever that was. It fetches where the running package comes from."""
    from personalclaw.config import loader as config_loader
    from personalclaw.dashboard.handlers import updates as dash_updates

    (config_loader.config_dir() / "config.json").write_text(
        '{"updates": {"check_enabled": true}}', encoding="utf-8"
    )
    started_in = _checkout(tmp_path / "started-in")
    runs_from = _checkout(tmp_path / "runs-from")
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(started_in))
    _runs_from(monkeypatch, runs_from / "src" / "personalclaw")

    cwds: list[str] = []

    async def _record(*argv, **kw):
        cwds.append(kw.get("cwd"))
        return _Proc()

    monkeypatch.setattr(dash_updates.asyncio, "create_subprocess_exec", _record)

    await dash_updates._do_update_check()

    assert cwds == [str(runs_from)], f"git ran in {cwds}"
