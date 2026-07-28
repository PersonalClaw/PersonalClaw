"""Installer resolution for uv-created venvs (issues #46, #51).

Four paths install packages into the running environment: the app dependency
installer, the pip-kind self-updater, the startup dep repair, and the git-checkout
updater. All four used to hardcode ``python -m pip``, which does not exist in a
``uv venv`` — the project's own documented dev setup — so each died with
``No module named pip``.

These tests pin the resolution ORDER and, critically, that uv is targeted at the
running interpreter. They must pass on a pip venv and a uv venv alike, so both
installers are always faked rather than probed from the ambient environment.
"""

from __future__ import annotations

import sys

import pytest

from personalclaw import _installer


@pytest.fixture
def env(monkeypatch):
    """Control which installers exist. ``env(uv=..., pip=...)``."""

    def _set(*, uv: bool, pip: bool):
        monkeypatch.setattr(_installer, "_have_uv", lambda: uv)
        monkeypatch.setattr(_installer, "_have_pip", lambda: pip)

    return _set


# ── resolution order ──────────────────────────────────────────────────────────


def test_uv_wins_when_both_are_present(env):
    # uv is preferred: it is the installer that created the venv in the documented
    # setup, and a venv with both is still a uv-managed venv.
    env(uv=True, pip=True)
    assert _installer.installer_name() == "uv"
    assert _installer.install_argv(["x"])[:3] == ["uv", "pip", "install"]


def test_pip_is_used_when_uv_is_absent(env):
    env(uv=False, pip=True)
    assert _installer.installer_name() == "pip"
    assert _installer.install_argv(["x"]) == [sys.executable, "-m", "pip", "install", "x"]


def test_neither_available_raises_an_actionable_error(env):
    """The whole point of #46/#51: not a bare ``No module named pip``."""
    env(uv=False, pip=False)
    assert _installer.installer_name() == ""
    with pytest.raises(_installer.NoInstallerError) as ei:
        _installer.install_argv(["x"])
    msg = str(ei.value)
    # Names BOTH remedies and the interpreter, so the reader isn't sent after pip
    # when the real answer is "this is a uv venv and uv isn't on PATH".
    assert "uv" in msg and "ensurepip" in msg
    assert sys.executable in msg


# ── uv must target the RUNNING interpreter ────────────────────────────────────


def test_uv_pins_the_target_interpreter(env):
    """Without ``--python``, uv resolves its OWN idea of the active environment
    (VIRTUAL_ENV, or a discovered .venv) — which can be a different env than the
    one the gateway imports from. It would then report success while the import
    still fails."""
    env(uv=True, pip=False)
    argv = _installer.install_argv(["pkg"])
    assert "--python" in argv
    assert argv[argv.index("--python") + 1] == sys.executable


def test_uv_python_flag_precedes_the_requirements(env):
    env(uv=True, pip=False)
    argv = _installer.install_argv(["pkg>=1.0"])
    assert argv.index("--python") < argv.index("pkg>=1.0")


# ── flag translation ──────────────────────────────────────────────────────────


def test_pip_only_flag_is_dropped_for_uv(env):
    """``--disable-pip-version-check`` is a pip flag; forwarding it makes uv exit
    non-zero, failing the install for a reason unrelated to the packages."""
    env(uv=True, pip=False)
    argv = _installer.install_argv(["--disable-pip-version-check", "pkg"])
    assert "--disable-pip-version-check" not in argv
    assert "pkg" in argv


def test_pip_keeps_its_own_flag(env):
    env(uv=False, pip=True)
    argv = _installer.install_argv(["--disable-pip-version-check", "pkg"])
    assert "--disable-pip-version-check" in argv


@pytest.mark.parametrize("flag", ["-U", "-e", "--quiet"])
def test_shared_flags_survive_for_both_installers(env, flag):
    # Verified against `uv pip install --help`: uv accepts -U/-e/--quiet with the
    # same meaning, so these must NOT be stripped or the callers change behavior.
    for uv in (True, False):
        env(uv=uv, pip=not uv)
        assert flag in _installer.install_argv([flag, "pkg"])


# ── the probe itself ──────────────────────────────────────────────────────────


def test_broken_pip_reads_as_absent(monkeypatch):
    """A half-removed distribution can leave an import hook that RAISES rather
    than returning None. That must read as "no pip" so the caller falls through to
    uv, not crash inside the resolver."""

    def boom(name):
        raise ValueError("broken meta-path finder")

    monkeypatch.setattr(_installer.importlib.util, "find_spec", boom)
    assert _installer._have_pip() is False


def test_pip_is_probed_as_a_module_not_a_path_executable(env, monkeypatch):
    """A bare ``pip`` on PATH may belong to a DIFFERENT interpreter; installing
    with it would silently populate the wrong site-packages. Only ``python -m pip``
    is ever used, so a PATH pip must not make us think pip is usable."""
    monkeypatch.setattr(
        _installer.shutil, "which", lambda name: "/usr/bin/pip" if name == "pip" else None
    )
    monkeypatch.setattr(_installer.importlib.util, "find_spec", lambda name: None)
    assert _installer.installer_name() == ""
