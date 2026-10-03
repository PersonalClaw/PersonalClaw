"""Which tool installs packages into *this* environment, and with which command.

PersonalClaw installs into its own environment, the one it runs from, when it updates (the
dashboard's Update, the staged auto-update, ``personalclaw update``) and when its startup repairs a
missing dependency. Each runs the tool that MADE that environment (:func:`own_installer`), read
from the record every installer leaves in a distribution's metadata, its ``INSTALLER`` file:
``uv sync``, ``uv pip install`` and ``uv tool install`` write ``uv``; pip and pipx write ``pip``.
That record is a fact about the environment, and what happens to be on PATH is not. Taking
whichever tool turned up (uv first, then pip) ran uv in environments pip made and installed a
uv-synced checkout behind its lockfile, and the checkout's update, which spelled out
``python -m pip``, failed in every environment uv made: they have no pip until they install a
version that declares it, and by then the update had already checked the new release out.

When that tool is not there, the install refuses with :class:`NoInstallerError`: one sentence that
says nothing was changed and names what to run. An update asks first
(:func:`require_own_installer`), before it changes anything.

uv is always pointed at the running interpreter (``--python <sys.executable>``). Otherwise it
resolves its own idea of the environment (``VIRTUAL_ENV``, or a discovered ``.venv``), which can be
a *different* one than the gateway imports from, and installing there reports success while the
import still fails. For a ``uv sync`` it is worse than that: asked for any other interpreter, uv
deletes the environment and makes a new one, under the running gateway.

``pip`` is checked as a MODULE (``python -m pip``) and never as a bare ``pip``
executable on PATH: a stray system-wide ``pip`` would install into some other
interpreter's site-packages, which is the failure this whole module exists to
prevent.

App packages are the one install that does NOT target this environment: they go into
``<home>/app-python`` with ``--prefix`` (``apps/app_python.py``), resolved AGAINST this
environment. That install is pip-only — :func:`prefix_install_argv` — because uv's
``--prefix`` does not treat the running environment's packages as installed. An app's
engine goes into the app's own environment, installed by this environment's pip run under
that environment's interpreter (:func:`env_install_argv`).

Both run the pip of this environment, which is why pip is one of PersonalClaw's own declared
dependencies. Undeclared, it was there only by accident of how the environment was made: a
``python -m venv`` environment gets one from ``ensurepip``, uv seeds none, and Debian and Ubuntu
strip ``ensurepip``'s wheels from their system Python, so on that Python (measured: Ubuntu
24.04's 3.12.3) no app with Python packages could be installed.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import shutil
import sys
import sysconfig
import tomllib
from pathlib import Path
from types import MappingProxyType

logger = logging.getLogger(__name__)

#: What every pip, uv and npm run PersonalClaw starts carries in its environment: no cache. pip
#: and uv keep what they download in the user's own cache (``~/Library/Caches/pip``,
#: ``~/.cache/pip``, ``~/.cache/uv``), and PersonalClaw leaves nothing outside its home:
#: installing one app that declares a Python package was measured filling pip's. What a cache
#: would hold is installed anyway (an app's packages in the home, PersonalClaw's own in its
#: installation), so a reinstall downloads it again. The variables, not ``--no-cache-dir``: pip
#: hands its environment, and not its flags, to the pip it runs for an sdist's build
#: requirements. Node keeps the code it compiles in the temp folder
#: (``node-compile-cache``, which npm turns on at every start) unless told not to.
_NO_CACHE_ENV = MappingProxyType(
    {"PIP_NO_CACHE_DIR": "1", "UV_NO_CACHE": "1", "NODE_DISABLE_COMPILE_CACHE": "1"}
)

#: The folder in the home for what those runs keep. npm cannot run without a cache, so its cache
#: (and the logs it keeps there) is ``npm``. ``tmp`` is the temp folder they run with: what one of
#: them leaves in its temp folder stays in the home (uv never removes the lock it takes to build
#: a setuptools project, ``uv-setuptools-<digest>.lock``). The durability inventory ignores the
#: folder as a cache.
INSTALLER_CACHE_DIRNAME = "installer-cache"


def installer_cache_env() -> dict[str, str]:
    """The settings every pip, uv and npm run PersonalClaw starts carries, over whatever else its
    environment holds: no pip, uv or node cache, npm's cache in the home, and a temp folder in
    the home, made here because a ``TMPDIR`` that does not exist is passed over for ``/tmp``.

    A child that runs code PersonalClaw did not write gets them on top of the child allowlist
    (``sandbox.build_child_env(..., extra=installer_cache_env())``); PersonalClaw's own installs
    through :func:`installer_env`. ``tests/test_installer_resolution.py`` holds every spawn of
    pip, uv or npm to one of the two.
    """
    from personalclaw.config.loader import config_dir

    root = config_dir() / INSTALLER_CACHE_DIRNAME
    tmp = root / "tmp"
    tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {**_NO_CACHE_ENV, "npm_config_cache": str(root / "npm"), "TMPDIR": str(tmp)}


#: Where a Node CLI PersonalClaw starts keeps the code it compiles. Node puts it in the temp folder
#: (``node-compile-cache``) whenever a program turns the cache on, which npm does at every start,
#: so each agent CLI, MCP server, app backend or bundler run through Node could leave one there.
#: ``NODE_COMPILE_CACHE`` moves it into the home, beside what the installers keep; the CLI keeps
#: its faster start.
NODE_COMPILE_CACHE_DIRNAME = "node-compile-cache"


def node_cli_env() -> dict[str, str]:
    """What every Node CLI PersonalClaw starts carries (an ACP agent, an MCP server, an app's
    backend or worker, the artifact bundler): its compile cache in ``<home>/installer-cache``,
    made here, owner-only, before the CLI looks for it."""
    from personalclaw.config.loader import config_dir

    cache = config_dir() / INSTALLER_CACHE_DIRNAME / NODE_COMPILE_CACHE_DIRNAME
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {"NODE_COMPILE_CACHE": str(cache)}


def installer_env() -> dict[str, str]:
    """This process's environment with :func:`installer_cache_env` over it, for an install or
    build of PersonalClaw itself (a self-update, the startup dependency repair, a frontend
    rebuild).

    It names the environment PersonalClaw runs from as uv's project environment
    (``UV_PROJECT_ENVIRONMENT``), so a ``uv sync`` of the checkout (:func:`checkout_install_argv`)
    installs into that environment wherever it is, and never into a ``.venv`` beside the sources
    that nothing runs from. pip and npm ignore it."""
    from personalclaw.env import gateway_env

    return {**gateway_env(), **installer_cache_env(), "UV_PROJECT_ENVIRONMENT": sys.prefix}


class NoInstallerError(RuntimeError):
    """A tool an install needs is not there, said in one sentence that names what to run.

    Raised instead of letting a ``No module named pip`` (or a missing ``uv``) escape from the
    install itself, after an update has already moved the checkout. A missing pip also carries
    the sentence's two halves, ``problem`` and ``fix`` (:func:`missing_pip`), for a surface that
    shows what broke and what to do apart (an engine's install).
    """

    def __init__(self, message: str, *, problem: str = "", fix: str = "") -> None:
        super().__init__(message)
        self.problem = problem
        self.fix = fix


def _have_uv() -> bool:
    return shutil.which("uv") is not None


def _have_pip() -> bool:
    """True if ``python -m pip`` would work, without spending a subprocess.

    ``find_spec`` can itself raise (a half-removed distribution leaves an import
    hook that errors rather than returning None), and a broken pip must read as
    "no pip" so the caller falls through to uv instead of crashing here.
    """
    try:
        return importlib.util.find_spec("pip") is not None
    except Exception:  # noqa: BLE001 — a pip that can't even be probed is not usable
        return False


def _own_distribution() -> importlib.metadata.Distribution | None:
    """The installed distribution of the running PersonalClaw, or ``None`` when there is none
    (PersonalClaw run from bare sources on ``PYTHONPATH``)."""
    try:
        return importlib.metadata.distribution("personalclaw")
    except importlib.metadata.PackageNotFoundError:
        return None


def own_installer() -> str:
    """The tool that made PersonalClaw's own environment, and so the one that installs into it.

    ``"uv"`` when the running PersonalClaw's ``INSTALLER`` record says uv. ``"pip"`` otherwise:
    pip and pipx write ``pip``, and PersonalClaw run from bare sources has no record, in an
    environment that ``python -m venv`` made with pip in it.
    """
    dist = _own_distribution()
    record = (dist.read_text("INSTALLER") or "") if dist is not None else ""
    return "uv" if record.strip() == "uv" else "pip"


def missing_pip(python: str = "") -> tuple[str, str]:
    """``(problem, fix)`` for *python* (the running interpreter by default) having no ``pip``
    module: the words every refusal here uses and ``personalclaw doctor`` prints, so the
    installer and the doctor say the same thing.

    pip is one of PersonalClaw's own dependencies, so an environment without it is an install
    missing part of itself, and the fix is the reinstall that puts it back. Never ``ensurepip``,
    which a Debian or Ubuntu system Python does not have, nor the distribution's ``python3-pip``,
    which installs into the system's own packages rather than this environment.
    """
    from personalclaw.python_support import reinstall_command

    problem = (
        f"{python or sys.executable} has no `pip` module, though pip is one of "
        "PersonalClaw's own dependencies"
    )
    command = reinstall_command()
    if command:
        return problem, f"reinstall PersonalClaw (`{command}`) to put it back"
    return problem, "reinstall PersonalClaw into that environment to put it back"


def _pip() -> list[str]:
    """``python -m pip`` on the running interpreter. Raises :class:`NoInstallerError`, naming
    the fix, when this environment has no ``pip`` module."""
    if not _have_pip():
        problem, fix = missing_pip()
        raise NoInstallerError(
            f"{problem}. {fix[0].upper()}{fix[1:]}, then try again", problem=problem, fix=fix
        )
    return [sys.executable, "-m", "pip"]


# pip flags that ``uv pip install`` does not accept. uv has no version self-check
# to disable; every other flag these call sites pass (``-U``, ``-e``, ``--quiet``)
# uv accepts with identical meaning, so only this one needs dropping. Forwarding an
# unknown flag makes uv exit non-zero, failing the install for a reason that has
# nothing to do with the packages.
_UV_REJECTS: frozenset[str] = frozenset({"--disable-pip-version-check"})


def _cannot_install(tool: str) -> NoInstallerError:
    """Why PersonalClaw cannot install into its own environment, which *tool* made: one sentence
    that says nothing was changed and names what to run.

    For uv that is ``personalclaw update`` wherever uv works: the gateway runs with the PATH it was
    started with (a service keeps the one it was installed with), so a uv a terminal has can be one
    the gateway cannot see. For pip it is the reinstall :func:`missing_pip` names, in the words
    ``personalclaw doctor`` uses, because pip is one of PersonalClaw's own dependencies.
    """
    if tool == "uv":
        return NoInstallerError(
            "Nothing was changed: uv made PersonalClaw's environment, and PersonalClaw cannot find "
            "`uv` on its PATH, so run `personalclaw update` from a terminal where `uv` works."
        )
    problem, fix = missing_pip()
    return NoInstallerError(
        f"Nothing was changed: pip made PersonalClaw's environment, and {problem}; {fix}, then "
        "run `personalclaw update`.",
        problem=problem,
        fix=fix,
    )


def require_own_installer() -> str:
    """:func:`own_installer`, once that tool is there to run.

    Every update asks this before it changes anything, so a missing tool refuses the update rather
    than failing it halfway, with the checkout on the new release and the environment not.

    Raises:
        NoInstallerError: the tool that made the environment is not there (:func:`_cannot_install`).
    """
    tool = own_installer()
    if not (_have_uv() if tool == "uv" else _have_pip()):
        raise _cannot_install(tool)
    return tool


def install_argv(args: list[str]) -> list[str]:
    """The argv that installs *args* into PersonalClaw's own environment, with the tool that made
    it (:func:`own_installer`).

    Args:
        args: installer arguments AFTER the ``install`` verb — requirement specs
            and/or pip-shaped flags (e.g. ``["-U", "personalclaw==0.1.2"]``).
            Flags the chosen installer would reject are dropped.

    Returns:
        A complete argv list for ``subprocess``.

    Raises:
        NoInstallerError: that tool is not there (:func:`require_own_installer`).
    """
    if require_own_installer() == "uv":
        kept = [a for a in args if a not in _UV_REJECTS]
        return ["uv", "pip", "install", "--python", sys.executable, *kept]
    return [sys.executable, "-m", "pip", "install", *args]


def checkout_install_argv(package_root: str | Path) -> list[str]:
    """The argv that installs the source checkout at *package_root* into PersonalClaw's own
    environment once an update has moved it, with the tool that made the environment. It runs in
    *package_root*.

    pip: ``pip install -e .``, editable, the way a checkout runs.

    uv: ``uv sync --locked``, the versions the checkout's lockfile names, the way uv made the
    environment. ``--inexact`` keeps what the environment has beyond them (a package installed by
    hand, an extra nothing here can tell was asked for), so an update adds and upgrades and never
    removes. The extras the environment has are synced with the rest (:func:`_installed_extras`).
    :func:`installer_env` names the running environment uv's project environment.

    Raises:
        NoInstallerError: that tool is not there (:func:`require_own_installer`).
    """
    if require_own_installer() == "uv":
        argv = ["uv", "sync", "--locked", "--inexact", "--python", sys.executable, "--quiet"]
        for extra in _installed_extras(_declared_extras(Path(package_root))):
            argv += ["--extra", extra]
        return argv
    return [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"]


def _declared_extras(package_root: Path) -> set[str]:
    """The extras the checkout at *package_root* declares (``[project.optional-dependencies]``),
    normalized. An extra the running version has and the checkout no longer declares is not one
    ``uv sync`` can be asked for."""
    from packaging.utils import canonicalize_name

    try:
        project = tomllib.loads((package_root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    table = project.get("project", {}).get("optional-dependencies", {})
    return {canonicalize_name(name) for name in table} if isinstance(table, dict) else set()


def _installed_extras(declared: set[str]) -> list[str]:
    """The extras of the running PersonalClaw that this environment has, among *declared*.

    Nothing records which extras an environment was synced with, so it is read back from what is
    installed. An extra counts when every package it adds on this platform is installed, and an
    extra that names others of PersonalClaw's (``dev`` naming ``test``) counts when they all do.
    One whose packages are there for another reason counts too, which only syncs those packages
    to the versions the lockfile names.
    """
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name

    dist = _own_distribution()
    if dist is None:
        return []
    own = canonicalize_name(dist.metadata["Name"] or "personalclaw")
    extras = {canonicalize_name(e) for e in dist.metadata.get_all("Provides-Extra") or []}
    needs: dict[str, list[Requirement]] = {}
    for line in dist.requires or []:
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            continue
        marker = requirement.marker
        if marker is None or marker.evaluate({"extra": ""}):
            continue  # one every install has, not an extra's
        for extra in extras:
            if marker.evaluate({"extra": extra}):
                needs.setdefault(extra, []).append(requirement)

    def installed(extra: str, asking: frozenset[str]) -> bool:
        if extra in asking:
            return True  # an extra naming itself back decides nothing
        wanted = needs.get(extra)
        if not wanted:
            return False
        for requirement in wanted:
            if canonicalize_name(requirement.name) == own:
                named = (canonicalize_name(e) for e in requirement.extras)
                if not all(installed(e, asking | {extra}) for e in named):
                    return False
            elif not _installed(requirement.name):
                return False
        return True

    return sorted(e for e in needs if e in declared and installed(e, frozenset()))


def _installed(name: str) -> bool:
    try:
        importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def prefix_install_argv(args: list[str]) -> list[str]:
    """The argv that runs ``pip install <args>`` on the running interpreter, for an install into a
    separate ``--prefix`` that must RESOLVE AGAINST this environment (app packages).

    pip only — never uv, even when uv is on PATH, because the two disagree on exactly the
    property this install depends on. Measured (pip 26.1, uv 0.12): with ``requests`` already in
    the environment, ``pip install --prefix P requests-toolbelt`` installed ONE package, while
    ``uv pip install --python <exe> --prefix P requests-toolbelt`` installed six — ``requests``,
    ``urllib3`` and the rest of the closure, as if nothing were installed. For app packages that
    means re-downloading and duplicating what core already ships (PyTorch among them), and a pin
    on a version only the running environment has (the image's ``torch …+cpu``) cannot be
    satisfied from an index at all.

    Raises:
        NoInstallerError: this environment has no ``pip`` module (see :func:`missing_pip`).
    """
    return [*_pip(), "install", *args]


def env_install_argv(python: str | Path, args: list[str]) -> list[str]:
    """The argv that runs ``pip install <args>`` into the environment *python* belongs to (an app
    engine's own), with the pip of this environment.

    pip's ``--python`` runs this copy of pip under that interpreter, so that environment needs no
    pip of its own and is made without one (``python -m venv --without-pip``). Making it with one
    asks ``ensurepip``, and a Debian or Ubuntu system Python has none unless ``python3-venv`` is
    installed: ``python -m venv`` fails there, ``--without-pip`` does not (measured on Ubuntu
    24.04). pip only, for the reason :func:`prefix_install_argv` gives.

    Raises:
        NoInstallerError: this environment has no ``pip`` module (see :func:`missing_pip`).
    """
    return [*_pip(), "--python", str(python), "install", *args]


# ── What an install changed ──────────────────────────────────────────────────


def _site_dirs() -> list[str]:
    """Where PersonalClaw's environment keeps what is installed in it: the running interpreter's
    ``site-packages``."""
    paths = sysconfig.get_paths()
    return sorted({paths["purelib"], paths["platlib"]})


def installed_distributions() -> frozenset[str]:
    """What is installed in PersonalClaw's environment, as an install changes it: the metadata
    folder of each distribution (``name-version.dist-info``), which an install adds, removes or
    renames. Read before an update's install, so an install that failed or was stopped can say
    whether the environment is as it was (:func:`changed_distributions`)."""
    found: set[str] = set()
    for folder in _site_dirs():
        try:
            found.update(p.name[: -len(".dist-info")] for p in Path(folder).glob("*.dist-info"))
        except OSError:
            continue
    return frozenset(found)


def changed_distributions(before: frozenset[str]) -> str:
    """The distributions an install added, removed or replaced in PersonalClaw's environment since
    *before* (:func:`installed_distributions`), named for a sentence (``alpha, beta and 2 more``),
    or ``""`` when the environment is as it was."""
    names = sorted({name.rsplit("-", 1)[0] for name in before ^ installed_distributions()})
    if len(names) > 3:
        return f"{', '.join(names[:3])} and {len(names) - 3} more"
    if len(names) > 1:
        return f"{', '.join(names[:-1])} and {names[-1]}"
    return names[0] if names else ""
