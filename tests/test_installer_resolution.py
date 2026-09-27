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
from pathlib import Path

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


# ── app packages: a --prefix install resolved AGAINST this environment ────────────


def test_prefix_installs_use_pip_even_when_uv_is_present(env):
    """uv's ``--prefix`` treats nothing as installed (measured: it re-installed ``requests``
    and its whole closure beside a base environment that already had them), so the app
    package install is pip-only — the ONE caller that must not prefer uv."""
    env(uv=True, pip=True)
    assert _installer.prefix_install_argv(["--prefix", "/p", "pkg"]) == [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--prefix",
        "/p",
        "pkg",
    ]


def test_a_pip_less_environment_runs_the_bundled_pip_wheel(env, tmp_path, monkeypatch):
    """`uv tool install personalclaw` makes a venv with no pip module; its Python still ships
    ensurepip's pip wheel, and pip runs straight from that wheel."""
    env(uv=True, pip=False)
    wheel = tmp_path / "pip-99.0-py3-none-any.whl"
    monkeypatch.setattr(_installer, "_bundled_pip_wheel", lambda: wheel)
    assert _installer.prefix_install_argv(["pkg"]) == [
        sys.executable,
        str(wheel / "pip"),
        "install",
        "pkg",
    ]


def test_the_bundled_wheel_is_found_where_ensurepip_keeps_it():
    """The real lookup, on the interpreter running the tests — the vacuity floor for the case
    above: if this Python ships a bundled wheel at all, the probe must find it."""
    import ensurepip
    from pathlib import Path

    bundled = sorted((Path(ensurepip.__file__).parent / "_bundled").glob("pip-*.whl"))
    assert _installer._bundled_pip_wheel() == (bundled[-1] if bundled else None)


def test_no_pip_and_no_bundled_wheel_names_the_remedy(env, monkeypatch):
    env(uv=True, pip=False)
    monkeypatch.setattr(_installer, "_bundled_pip_wheel", lambda: None)
    with pytest.raises(_installer.NoInstallerError) as ei:
        _installer.prefix_install_argv(["pkg"])
    message = str(ei.value)
    assert "ensurepip" in message and sys.executable in message


# ── what an install or build PersonalClaw runs keeps outside the home: nothing ────────────────
#
# pip, uv and npm each keep a cache in the user's home (`~/Library/Caches/pip`, `~/.cache/uv`,
# `~/.npm`). On `main` every self-update (`personalclaw update`, Update & Restart, auto-update,
# the startup dependency repair), every frontend rebuild and every ACP adapter provision ran
# them with the gateway's own environment, so each filled those caches. pip and uv now keep no
# cache, and npm keeps its own in the home.

_CACHE_VARS = (
    "PIP_NO_CACHE_DIR",
    "UV_NO_CACHE",
    "NODE_DISABLE_COMPILE_CACHE",
    "npm_config_cache",
    "TMPDIR",
)


def _recording_tools(tmp_path, monkeypatch, *names: str):
    """Stand-ins for *names* on PATH, each recording the cache settings it ran with; the log."""
    import os

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "ran.log"
    record = " ".join(f"{var}=${{{var}:-}}" for var in _CACHE_VARS)
    for name in names:
        tool = bin_dir / name
        tool.write_text(f'#!/bin/sh\necho "{name} {record}" >> "{log}"\nexit 0\n', "utf-8")
        tool.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]))
    for var in _CACHE_VARS:
        monkeypatch.delenv(var, raising=False)
    return log


def _in_the_home(*parts: str) -> str:
    from personalclaw.config.loader import config_dir

    return str(config_dir().joinpath("installer-cache", *parts))


def _settings() -> list[str]:
    """What each run must have recorded: every cache off, npm's cache and the temp folder in
    the home."""
    return [
        "PIP_NO_CACHE_DIR=1",
        "UV_NO_CACHE=1",
        "NODE_DISABLE_COMPILE_CACHE=1",
        f"npm_config_cache={_in_the_home('npm')}",
        f"TMPDIR={_in_the_home('tmp')}",
    ]


def test_personalclaw_updates_installer_keeps_no_cache(tmp_path, monkeypatch):
    """`personalclaw update` on a git checkout reinstalls through `cli_server._install`."""
    from personalclaw import cli_server

    log = _recording_tools(tmp_path, monkeypatch, "uv")
    cli_server._install(["-e", ".", "--quiet"], cwd=str(tmp_path), label="install -e .")
    assert log.read_text().split() == ["uv", *_settings()]


@pytest.mark.parametrize("build", ["sync", "async"])
def test_a_frontend_rebuild_keeps_npms_cache_in_the_home(tmp_path, monkeypatch, build):
    import asyncio

    from personalclaw import frontend

    log = _recording_tools(tmp_path, monkeypatch, "npm", "node")
    checkout = tmp_path / "checkout"
    (checkout / "web").mkdir(parents=True)
    if build == "sync":
        frontend.build_frontend_sync(checkout, log=lambda _line: None)
    else:
        asyncio.run(frontend.build_frontend_async(str(checkout)))

    runs = [line.split() for line in log.read_text().splitlines()]
    assert [run[0] for run in runs] == ["npm", "npm"], "npm ci, then npm run build"
    for run in runs:
        assert run[1:] == _settings(), run


def test_what_the_installers_keep_is_in_the_home_where_the_inventory_ignores_it():
    from personalclaw._installer import installer_env
    from personalclaw.config.loader import config_dir
    from personalclaw.durability.inventory import is_accounted, is_ignored

    env = installer_env()
    assert env["PIP_NO_CACHE_DIR"] == "1" and env["UV_NO_CACHE"] == "1"
    assert env["NODE_DISABLE_COMPILE_CACHE"] == "1"
    assert Path(env["TMPDIR"]).is_dir(), "a TMPDIR that does not exist is passed over for /tmp"
    for kept in (env["npm_config_cache"], env["TMPDIR"]):
        rel = Path(kept).relative_to(config_dir()).as_posix()
        assert is_ignored(rel) and is_accounted(rel), rel


# The rail. A spawn is a package install or build when its argv starts with npm, runs
# `python -m pip`, or comes from `install_argv`/`prefix_install_argv` in the same function.

_SPAWN_FUNCS = {"run", "Popen", "call", "check_call", "check_output", "create_subprocess_exec"}
_INSTALLER_ENVS = ("installer_env(", "installer_cache_env(", "_pip_env(")
_INSTALLER_ARGV_FUNCS = {"install_argv", "prefix_install_argv"}


def _callee(node) -> str:
    import ast

    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _runs_an_installer(call, installer_argvs: set[str]) -> bool:
    import ast

    args = list(call.args)
    if args and isinstance(args[0], (ast.List, ast.Tuple)):
        args = list(args[0].elts)
    if args and isinstance(args[0], ast.Starred):
        args = [args[0].value]
    if args and isinstance(args[0], ast.Name) and args[0].id in installer_argvs:
        return True
    words = [
        a.value if isinstance(a, ast.Constant) else getattr(a, "id", None)
        for a in args
        if isinstance(a, (ast.Constant, ast.Name))
    ]
    if words and words[0] in ("npm", "npx"):
        return True
    return any(words[i : i + 2] == ["-m", "pip"] for i in range(len(words)))


def installer_spawns() -> dict[tuple[str, str], bool]:
    """``(file, function) -> whether its spawns pass the installer environment``, each spawn
    attributed to the innermost function around it."""
    import ast

    import personalclaw

    src = Path(personalclaw.__file__).resolve().parent
    found: dict[tuple[str, str], bool] = {}
    for py in sorted(src.rglob("*.py")):
        rel = py.relative_to(src).as_posix()
        if rel.startswith("skills/bundled/"):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

        def enclosing(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node
            return tree

        def qualname(node) -> str:
            names = []
            while node is not tree:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.append(node.name)
                node = parents[node]
            return ".".join(reversed(names)) or "<module>"

        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or _callee(call) not in _SPAWN_FUNCS:
                continue
            fn = enclosing(call)
            argvs = {
                target.id
                for node in ast.walk(fn)
                if isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and _callee(node.value) in _INSTALLER_ARGV_FUNCS
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            if not _runs_an_installer(call, argvs):
                continue
            env = next((kw.value for kw in call.keywords if kw.arg == "env"), None)
            sources = [env] if env is not None else []
            if isinstance(env, ast.Name):  # `env=env`: what the function assigned it
                sources = [
                    node.value
                    for node in ast.walk(fn)
                    if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == env.id for t in node.targets)
                ]
            ok = bool(sources) and all(
                any(tag in ast.unparse(value) for tag in _INSTALLER_ENVS) for value in sources
            )
            site = (rel, qualname(fn))
            found[site] = found.get(site, True) and ok
    return found


def test_every_install_or_build_personalclaw_runs_keeps_no_cache_outside_the_home():
    spawns = installer_spawns()
    missing = sorted(site for site, ok in spawns.items() if not ok)
    assert not missing, (
        "these run pip, uv or npm without `_installer.installer_env()` or "
        f"`installer_cache_env()`, so they fill the user's own caches: {missing}"
    )


def test_the_rail_sees_every_known_install_site():
    """The vacuity floor: a detector that stopped recognising a spawn would pass above."""
    assert {
        ("apps/app_python.py", "_pip_install"),
        ("cli_server.py", "_install"),
        ("gateway.py", "GatewayOrchestrator._check_missing_deps"),
        ("gateway.py", "GatewayOrchestrator._auto_apply_update"),
        ("dashboard/handlers/updates.py", "_apply_pip_update._apply"),
        ("dashboard/handlers/updates.py", "api_update_apply._apply"),
        ("frontend.py", "build_frontend_sync"),
        ("frontend.py", "build_frontend_async"),
        ("acp/cli_resolve.py", "_npm_global_root"),
        ("acp/cli_resolve.py", "provision_acp_adapter"),
    } <= set(installer_spawns())
