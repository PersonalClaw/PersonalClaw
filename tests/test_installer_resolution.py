"""Which tool installs into PersonalClaw's own environment, and with which command.

PersonalClaw installs into the environment it runs from when it updates and when its startup
repairs a missing dependency, and each runs the tool that MADE that environment, read from the
record every installer leaves in a distribution's metadata (``INSTALLER``). Whichever tool turned
up used to win, uv first: so uv ran in environments pip made, a uv-synced checkout was installed
behind its lockfile, and the checkout's update, which spelled out ``python -m pip``, failed in
every environment uv made, which has no pip.

These pin that rule, the refusal when the tool is not there, and that uv is targeted at the
running interpreter. They must pass in an environment pip made and one uv made alike, so the
record (``environment_made_by``) and both tools are always set rather than read from the ambient
environment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from personalclaw import _installer


@pytest.fixture
def env(monkeypatch, environment_made_by):
    """``env(uv=..., pip=..., made_by="uv")``: which tools exist, and which one made the
    environment."""

    def _set(*, uv: bool, pip: bool, made_by: str = "uv"):
        environment_made_by(made_by)
        monkeypatch.setattr(_installer, "_have_uv", lambda: uv)
        monkeypatch.setattr(_installer, "_have_pip", lambda: pip)

    return _set


# ── the tool that made the environment installs into it ──────────────────────────


@pytest.mark.parametrize("made_by", ["uv", "pip"])
def test_the_record_names_the_tool_that_made_the_environment(environment_made_by, made_by):
    environment_made_by(made_by)
    assert _installer.own_installer() == made_by


def test_an_environment_with_no_record_is_pips(monkeypatch, tmp_path):
    """No ``INSTALLER`` (a distribution some tool wrote without one) reads as pip, the installer
    every environment ``python -m venv`` makes has."""
    info = tmp_path / "personalclaw-0.0.1.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: personalclaw\nVersion: 0.0.1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert _installer.own_installer() == "pip"


def test_an_environment_uv_made_installs_with_uv_even_with_pip_in_it(env):
    env(uv=True, pip=True, made_by="uv")
    assert _installer.install_argv(["x"])[:3] == ["uv", "pip", "install"]


def test_an_environment_pip_made_installs_with_pip_even_with_uv_on_path(env):
    """uv on PATH is no reason to install into an environment pip made with uv."""
    env(uv=True, pip=True, made_by="pip")
    assert _installer.install_argv(["x"]) == [sys.executable, "-m", "pip", "install", "x"]


def test_an_environment_uv_made_without_uv_refuses_rather_than_reach_for_pip(env):
    """A pip it happens to have does not install into an environment uv made: the refusal says
    nothing was changed and names the command that will work."""
    env(uv=False, pip=True, made_by="uv")
    with pytest.raises(_installer.NoInstallerError) as ei:
        _installer.install_argv(["x"])
    assert str(ei.value) == (
        "Nothing was changed: uv made PersonalClaw's environment, and PersonalClaw cannot find "
        "`uv` on its PATH, so run `personalclaw update` from a terminal where `uv` works."
    )


def test_an_environment_pip_made_without_pip_refuses_with_the_reinstall(env):
    """Names the interpreter and the reinstall that puts pip back. pip is one of PersonalClaw's
    own dependencies, so never ``ensurepip``, which a Debian or Ubuntu system Python does not have,
    and never a uv that happens to be on PATH."""
    env(uv=True, pip=False, made_by="pip")
    with pytest.raises(_installer.NoInstallerError) as ei:
        _installer.install_argv(["x"])
    msg = str(ei.value)
    assert msg.startswith("Nothing was changed: pip made PersonalClaw's environment")
    assert sys.executable in msg and "reinstall PersonalClaw" in msg
    assert "then run `personalclaw update`" in msg
    assert "ensurepip" not in msg


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
    env(uv=False, pip=True, made_by="pip")
    argv = _installer.install_argv(["--disable-pip-version-check", "pkg"])
    assert "--disable-pip-version-check" in argv


@pytest.mark.parametrize("flag", ["-U", "-e", "--quiet"])
@pytest.mark.parametrize("made_by", ["uv", "pip"])
def test_shared_flags_survive_for_both_installers(env, flag, made_by):
    # Verified against `uv pip install --help`: uv accepts -U/-e/--quiet with the
    # same meaning, so these must NOT be stripped or the callers change behavior.
    env(uv=made_by == "uv", pip=made_by == "pip", made_by=made_by)
    assert flag in _installer.install_argv([flag, "pkg"])


# ── a source checkout, installed once an update has moved it ───────────────────────────────────


def _checkout(tmp_path: Path, *extras: str) -> Path:
    table = "".join(f"{extra} = []\n" for extra in extras)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "personalclaw"\n[project.optional-dependencies]\n{table}'
    )
    return tmp_path


def test_a_checkout_pip_made_installs_editable_with_pip(env, tmp_path):
    env(uv=True, pip=True, made_by="pip")
    assert _installer.checkout_install_argv(_checkout(tmp_path)) == [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-e",
        ".",
        "--quiet",
    ]


def test_a_checkout_uv_made_syncs_its_lockfile_into_the_running_environment(env, tmp_path):
    """``--python`` is the running interpreter: asked for any other, ``uv sync`` deletes the
    environment and makes a new one (measured, uv 0.12), under the running gateway. ``--inexact``
    keeps what the environment has beyond the lockfile, so an update never removes a package."""
    env(uv=True, pip=False, made_by="uv")
    assert _installer.checkout_install_argv(_checkout(tmp_path)) == [
        "uv",
        "sync",
        "--locked",
        "--inexact",
        "--python",
        sys.executable,
        "--quiet",
    ]


def test_the_installs_name_the_running_environment_uvs_project_environment():
    """A sync of the checkout goes into the environment PersonalClaw runs from, wherever it is,
    and never into a ``.venv`` beside the sources that nothing runs from."""
    assert _installer.installer_env()["UV_PROJECT_ENVIRONMENT"] == sys.prefix


_EXTRAS = ("stt", "tts", "models", "dev", "gone")
_REQUIRES = (
    "pc-fixture-core>=1",
    'pc-fixture-whisper>=1; extra == "stt"',
    'pc-fixture-piper>=1; extra == "tts"',
    'personalclaw[stt,tts]; extra == "models"',
    'pc-fixture-pytest>=1; extra == "dev"',
    'personalclaw[models]; extra == "dev"',
    'pc-fixture-old>=1; extra == "gone"',
)


@pytest.mark.parametrize(
    "installed, synced",
    [
        # tts is missing a package, so neither `models` nor `dev`, which name it, is synced.
        (("pc-fixture-whisper", "pc-fixture-pytest", "pc-fixture-old"), ["stt"]),
        (
            ("pc-fixture-whisper", "pc-fixture-piper", "pc-fixture-pytest", "pc-fixture-old"),
            ["dev", "models", "stt", "tts"],
        ),
        ((), []),
    ],
)
def test_a_checkout_uv_made_syncs_the_extras_the_environment_has(
    environment_made_by, monkeypatch, tmp_path, installed, synced
):
    """Nothing records which extras an environment was synced with, so they are read back from
    what is installed: an extra whose packages are all there, or that names others that all are.
    One the checkout no longer declares (``gone``) is never asked for, which uv would refuse."""
    monkeypatch.setattr(_installer, "_have_uv", lambda: True)
    environment_made_by("uv", requires=_REQUIRES, extras=_EXTRAS, installed=installed)
    argv = _installer.checkout_install_argv(_checkout(tmp_path, "stt", "tts", "models", "dev"))
    asked = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--extra"]
    assert asked == synced


# ── the probe itself ──────────────────────────────────────────────────────────


def test_broken_pip_reads_as_absent(monkeypatch):
    """A half-removed distribution can leave an import hook that RAISES rather
    than returning None. That must read as "no pip", so an install refuses in words
    rather than crashing inside the resolver."""

    def boom(name):
        raise ValueError("broken meta-path finder")

    monkeypatch.setattr(_installer.importlib.util, "find_spec", boom)
    assert _installer._have_pip() is False


def test_pip_is_probed_as_a_module_not_a_path_executable(environment_made_by, monkeypatch):
    """A bare ``pip`` on PATH may belong to a DIFFERENT interpreter; installing
    with it would silently populate the wrong site-packages. Only ``python -m pip``
    is ever used, so a PATH pip must not make us think pip is usable."""
    environment_made_by("pip")
    monkeypatch.setattr(
        _installer.shutil, "which", lambda name: "/usr/bin/pip" if name == "pip" else None
    )
    monkeypatch.setattr(_installer.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(_installer.NoInstallerError):
        _installer.install_argv(["x"])


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


def test_a_prefix_install_without_pip_refuses_even_with_uv_present(env):
    """No pip, so no app package can install: uv cannot do this install in its place, and the
    refusal names the reinstall that puts pip back."""
    env(uv=True, pip=False)
    with pytest.raises(_installer.NoInstallerError) as ei:
        _installer.prefix_install_argv(["pkg"])
    message = str(ei.value)
    assert sys.executable in message and "Reinstall PersonalClaw" in message
    assert "ensurepip" not in message


# ── an app's engine: its own environment, installed with PersonalClaw's pip ────────────────────


def test_an_engine_install_runs_personalclaws_pip_against_the_engines_interpreter(env):
    """``pip --python`` runs this environment's pip under the engine's interpreter, so the
    engine's environment needs no pip of its own, and so no ensurepip to have made it with."""
    env(uv=True, pip=True)
    engine = Path("/data/apps/voice/venv/bin/python")
    assert _installer.env_install_argv(engine, ["--no-input", "--", "omnivoice"]) == [
        sys.executable,
        "-m",
        "pip",
        "--python",
        str(engine),
        "install",
        "--no-input",
        "--",
        "omnivoice",
    ]


def test_an_engine_install_without_pip_refuses_as_an_app_package_install_does(env):
    env(uv=True, pip=False)
    with pytest.raises(_installer.NoInstallerError) as engine:
        _installer.env_install_argv(Path("/data/apps/voice/venv/bin/python"), ["omnivoice"])
    with pytest.raises(_installer.NoInstallerError) as package:
        _installer.prefix_install_argv(["omnivoice"])
    assert str(engine.value) == str(package.value)


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


def test_personalclaw_updates_installer_keeps_no_cache(tmp_path, monkeypatch, environment_made_by):
    """`personalclaw update` on a git checkout installs it through `cli_server._install`."""
    from personalclaw import cli_server

    environment_made_by("uv")
    log = _recording_tools(tmp_path, monkeypatch, "uv")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "personalclaw"\n')
    cli_server._install([], checkout=str(tmp_path))
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
# `python -m pip`, or comes from `install_argv`/`checkout_install_argv`/`prefix_install_argv`/
# `env_install_argv` in the same function.

_SPAWN_FUNCS = {"run", "Popen", "call", "check_call", "check_output", "create_subprocess_exec"}
_INSTALLER_ENVS = ("installer_env(", "installer_cache_env(", "_pip_env(")
_INSTALLER_ARGV_FUNCS = {
    "install_argv",
    "checkout_install_argv",
    "prefix_install_argv",
    "env_install_argv",
}


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
        ("dashboard/handlers/updates.py", "_apply_pip_update._apply"),
        ("dashboard/handlers/updates.py", "_advance_checkout"),
        ("frontend.py", "build_frontend_sync"),
        ("frontend.py", "build_frontend_async"),
        ("acp/cli_resolve.py", "_npm_global_root"),
        ("acp/cli_resolve.py", "provision_acp_adapter"),
    } <= set(installer_spawns())
