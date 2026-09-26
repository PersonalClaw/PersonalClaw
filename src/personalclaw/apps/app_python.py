"""Where installed apps' Python packages live: ``<home>/app-python``, a pip ``--prefix``.

An app declares ``dependencies.pythonDependencies``; the installer pip-installs them into this
directory — never into the environment the gateway runs from — and every process that runs app
code loads them AFTER the interpreter's own ``site-packages``.

Why here, and why this shape:

* **It is the one writable, persistent place on every install kind.** The published image runs
  as uid 10001 over a root-owned ``/opt/venv``, so installing into the running environment failed
  with ``[Errno 13] Permission denied`` for every app whose packages the image did not already
  carry (Amazon Bedrock, both diarization apps, RapidOCR, Qdrant) — and a writable image layer
  would still have lost them on the next ``docker rm`` + ``docker run``. The home is ``/data``
  there and ``~/.personalclaw`` on a pip/uv install: one code path, no container special case.
* **An app can add packages; it can neither change nor shadow the gateway's.** The directory is
  APPENDED to ``sys.path`` (:func:`activate`), so a module the gateway already provides always
  wins the import. And pip resolves against the running environment with every distribution the
  gateway can import PINNED by a constraints file, so a dependency that needs another version of
  one of them fails resolution instead of being installed. Measured without the pins: ``pip
  install --prefix`` resolved a conflicting ``urllib3`` by UNINSTALLING the base environment's
  copy — on a user-owned venv that deletes a package core is running on.
* **One directory for every app, not one per app.** In-process app code shares one interpreter,
  and an interpreter imports one version of a module: a second app's copy of a package is simply
  shadowed by the first's, so per-app directories would promise an isolation Python cannot give.
  Instead pip resolves EVERY installed app's requirements in one run, so what it installs is a
  version all of them accept — or the install is refused, naming the conflict.
* **Garbage-collected from metadata, not reference-counted.** :func:`collect` deletes every
  distribution here that no app's requirement closure reaches — after an uninstall, an update,
  a failed install and at boot. It is derived from the manifests and dist-info on disk each
  time, so there is no ledger to drift out of step with them.
* **Reproducible, so rebuilt rather than backed up.** The directory is a function of the
  installed apps' manifests and the running interpreter (its layout is keyed by the Python
  version), which is why :func:`broken_apps` + :func:`install_everything` can rebuild whatever a
  new image's Python, a changed core dependency or a restored snapshot leaves missing, and why
  the durability inventory ignores it.
"""

from __future__ import annotations

import contextlib
import fcntl
import importlib
import importlib.metadata
import logging
import os
import re
import shutil
import site
import subprocess
import sys
import sysconfig
import tempfile
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw._app_python_child import PATH_ENV
from personalclaw.apps import manager as _manager
from personalclaw.apps.manager import APP_MANIFEST_FILENAME, INSTALLED_META_FILENAME

logger = logging.getLogger(__name__)

#: ``<home>/app-python``. Listed in the durability inventory's ``IGNORED``: reproducible state.
APP_PYTHON_DIRNAME = "app-python"

#: The module a Python child of an app runs instead of its entry script (see
#: ``personalclaw/_app_python_child.py`` for why ``PYTHONPATH`` cannot do this job).
CHILD_MODULE = "personalclaw._app_python_child"

_PIP_TIMEOUT = 600  # seconds — a heavy wheel (torch) can take minutes
_LOCK_FILENAME = ".lock"

#: pip settings that would redirect or reshape an install that must land in ``--prefix``.
#: Everything else in the environment (index URLs, proxies, certificates) is the operator's
#: and is passed through untouched.
_PIP_LOCATION_VARS = ("PIP_TARGET", "PIP_PREFIX", "PIP_ROOT", "PIP_USER")

#: How much of pip's output rides a failure into the "Fix with AI" prompt.
_LOG_TAIL_CHARS = 2000


class PackageInstallError(Exception):
    """Installing app packages failed. ``str()`` is the sentence the user reads.

    ``log_excerpt`` is the tail of pip's output, and it is set ONLY when that log is the useful
    next step — a package that failed to build, or a failure this module could not name. A
    failure whose cause is already stated (the data folder is not writable, the machine is
    offline, a version conflict, a package with no release for this platform) carries none:
    handing an assistant a log about an environment the user cannot change invites advice that
    does not apply, and the install surface only offers "Fix with AI" when a log is present.
    """

    def __init__(self, message: str, *, log_excerpt: str = "") -> None:
        super().__init__(message)
        self.log_excerpt = log_excerpt


# ── where it is ─────────────────────────────────────────────────────────────────


def root() -> Path:
    """``<home>/app-python`` — re-resolved per call, like every home path."""
    return _manager.config_dir() / APP_PYTHON_DIRNAME


def site_dirs() -> list[Path]:
    """The site-packages directories ``pip install --prefix <root>`` fills for THIS interpreter.

    Computed the way pip computes it (``pip._internal.locations._sysconfig.get_scheme``): the
    interpreter's preferred ``prefix`` scheme — ``osx_framework_library`` mapped to
    ``posix_prefix``, as pip does for a custom prefix — with every base variable pointed at the
    prefix. The layout carries the Python version (``lib/python3.13/site-packages``), so a new
    image's Python never imports an extension module compiled for the old one.
    """
    base = str(root())
    scheme = sysconfig.get_preferred_scheme("prefix")
    if scheme == "osx_framework_library":
        scheme = "posix_prefix"
    keys = ("installed_base", "base", "installed_platbase", "platbase", "prefix", "exec_prefix")
    paths = sysconfig.get_paths(scheme=scheme, vars={key: base for key in keys})
    out: list[Path] = []
    for key in ("purelib", "platlib"):
        path = Path(paths[key])
        if path not in out:
            out.append(path)
    return out


def _existing_site_dirs() -> list[str]:
    return [str(d) for d in site_dirs() if d.is_dir()]


# ── making it importable ─────────────────────────────────────────────────────────


def activate() -> bool:
    """Append the app packages to this process's import path, after everything already on it.

    :func:`site.addsitedir`, so ``.pth`` files in the directory are honoured exactly as they are
    in site-packages. Idempotent. Called before any app module is imported
    (``providers.loader.register_extension_providers``) and after every install, which is what
    makes a freshly installed app importable without a restart. Returns whether it added anything.
    """
    added = False
    for directory in _existing_site_dirs():
        if directory not in sys.path:
            site.addsitedir(directory)
            added = True
    if added:
        importlib.invalidate_caches()
    return added


def child_env() -> dict[str, str]:
    """What a Python child of an app needs in its environment to load the app packages."""
    dirs = _existing_site_dirs()
    return {PATH_ENV: os.pathsep.join(dirs)} if dirs else {}


def child_argv(entry: Path) -> list[str]:
    """How to start an app's Python entry script so it sees the app packages after its own."""
    return [sys.executable, "-m", CHILD_MODULE, str(entry)]


def app_packages_env() -> dict[str, str] | None:
    """The environment for a command that must import the app packages, or ``None`` to inherit
    the gateway's unchanged (no app package is installed).

    For a child :data:`CHILD_MODULE` cannot wrap: an app's setup hook (a shell command), or an
    app provider running one of its declared packages as ``python -m <package>`` (piper-tts).
    ``PYTHONPATH`` is the one mechanism that reaches such a ``python`` — which is how a hook can
    import what the app declared (the installer runs the dependency step before ``onInstall``
    for exactly that). Its entries precede site-packages inside that one process; neither child
    is started through the resource-ceiling shim, so there is no platform code in it for a
    package to shadow. Published on ``personalclaw.sdk.util``: piper-tts re-derived it by hand
    (#124), from where ``importlib`` found the package.
    """
    dirs = _existing_site_dirs()
    if not dirs:
        return None
    env = dict(os.environ)
    inherited = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([*([inherited] if inherited else []), *dirs])
    return env


# ── reading what is installed ─────────────────────────────────────────────────────


def _within(path: str, parent: str) -> bool:
    path, parent = os.path.abspath(path), os.path.abspath(parent)
    return path == parent or path.startswith(parent + os.sep)


def _base_paths() -> list[str]:
    """This process's import path minus the app packages: what the gateway itself provides."""
    here = str(root())
    return [p for p in sys.path if p and not _within(p, here)]


def _index(paths: list[str]) -> dict[str, list[importlib.metadata.Distribution]]:
    """Canonical name → distributions on *paths*, in import-resolution order (first one wins)."""
    from packaging.utils import canonicalize_name

    out: dict[str, list[importlib.metadata.Distribution]] = {}
    for dist in importlib.metadata.distributions(path=paths):
        try:
            name = dist.metadata["Name"]
        except Exception:  # noqa: BLE001 — unreadable metadata is not a distribution we can use
            name = None
        if name:
            out.setdefault(canonicalize_name(name), []).append(dist)
    return out


@dataclass
class _Env:
    """The two sources an import resolves against, in the order it resolves them."""

    base: dict[str, list[importlib.metadata.Distribution]]
    apps: dict[str, list[importlib.metadata.Distribution]]

    @classmethod
    def read(cls) -> _Env:
        return cls(base=_index(_base_paths()), apps=_index(_existing_site_dirs()))

    def find(self, key: str) -> tuple[importlib.metadata.Distribution | None, bool]:
        """The distribution an import of *key* loads, and whether it is an app package."""
        if self.base.get(key):
            return self.base[key][0], False
        if self.apps.get(key):
            return self.apps[key][0], True
        return None, False

    def app_versions(self) -> dict[str, str]:
        return {key: dists[0].version for key, dists in self.apps.items()}

    def pins(self) -> list[str]:
        """``name==version`` for every distribution the gateway itself can import.

        The constraint that makes pip treat them as fixed: a resolution that needs a different
        version of any of them fails instead of replacing it. Versions pip cannot parse are left
        out rather than breaking the constraints file.
        """
        from packaging.version import InvalidVersion, Version

        out: list[str] = []
        for key, dists in sorted(self.base.items()):
            version = dists[0].version
            try:
                Version(version)
            except (InvalidVersion, TypeError):
                continue
            out.append(f"{key}=={version}")
        return out


def _satisfies(req, version: str) -> bool:
    if req.url:  # a direct reference names a source, not a version: installed is satisfied
        return True
    try:
        return req.specifier.contains(version, prereleases=True)
    except Exception:  # noqa: BLE001 — an unparseable installed version satisfies nothing
        return False


def _closure(requirements: Iterable[str], env: _Env) -> tuple[set[str], list[str]]:
    """Walk *requirements*' closure the way imports resolve it — the gateway's own distribution
    first, then the app packages. Returns ``(app-package names the closure reaches, requirement
    strings nothing satisfies)``. Markers are evaluated for this interpreter, extras followed."""
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name

    reached: set[str] = set()
    unmet: list[str] = []
    seen: set[tuple[str, frozenset[str]]] = set()
    stack = []
    for spec in requirements:
        try:
            req = Requirement(spec)
        except InvalidRequirement:
            unmet.append(spec)
            continue
        if req.marker is None or req.marker.evaluate({"extra": ""}):
            stack.append(req)
    while stack:
        req = stack.pop()
        key = canonicalize_name(req.name)
        dist, is_app = env.find(key)
        if dist is None or not _satisfies(req, dist.version):
            unmet.append(str(req))
            continue
        if is_app:
            reached.add(key)
        extras = frozenset(canonicalize_name(e) for e in req.extras)
        if (key, extras) in seen:
            continue
        seen.add((key, extras))
        for line in dist.requires or []:
            try:
                dep = Requirement(line)
            except InvalidRequirement:
                continue
            if dep.marker is not None and not any(
                dep.marker.evaluate({"extra": extra}) for extra in (extras or {""})
            ):
                continue
            stack.append(dep)
    return reached, unmet


# ── which apps declare what ───────────────────────────────────────────────────────


@dataclass
class Declared:
    """One app tree's declared packages."""

    name: str
    label: str
    requirements: list[str] = field(default_factory=list)


def _read_declared(tree: Path) -> Declared | None:
    from personalclaw.apps.manifest import AppManifest

    manifest_file = tree / APP_MANIFEST_FILENAME
    if not manifest_file.is_file():
        return None
    try:
        manifest = AppManifest.from_json_file(manifest_file)
    except Exception:  # noqa: BLE001 — an unreadable manifest declares nothing we can honour
        logger.debug("app packages: unreadable manifest %s", manifest_file, exc_info=True)
        return None
    return Declared(
        name=manifest.name or tree.name,
        label=manifest.displayName or manifest.name or tree.name,
        requirements=[str(s) for s in manifest.dependencies.pythonDependencies],
    )


def installed_apps() -> list[Declared]:
    """Every INSTALLED app (it has ``installed.json``) that declares packages, by name."""
    base = _manager.apps_dir()
    if not base.is_dir():
        return []
    out: list[Declared] = []
    for tree in sorted(base.iterdir()):
        if tree.name.startswith(".") or not (tree / INSTALLED_META_FILENAME).is_file():
            continue
        declared = _read_declared(tree)
        if declared is not None and declared.requirements:
            out.append(declared)
    return out


def _protected_requirements() -> list[str]:
    """Everything any app tree on disk declares — what :func:`collect` must not delete.

    Wider than :func:`installed_apps` on purpose: an app mid-install has no ``installed.json``
    yet, an update in flight keeps the old version in ``apps/.{name}.rollback``, and a staged
    new version sits in ``apps/.quarantine``. Each of their packages may already be here, and a
    collection running beside them must not take them away.
    """
    base = _manager.apps_dir()
    if not base.is_dir():
        return []
    trees = [t for t in base.iterdir() if t.is_dir()]
    quarantine = base / ".quarantine"
    if quarantine.is_dir():
        trees.extend(t for t in quarantine.iterdir() if t.is_dir())
    specs: list[str] = []
    for tree in trees:
        declared = _read_declared(tree)
        if declared is not None:
            specs.extend(declared.requirements)
    return specs


# ── the lock ──────────────────────────────────────────────────────────────────────

_thread_lock = threading.RLock()
_lock_depth = 0
_lock_file = None


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    """Serialize every change to the directory: across threads AND processes (``fcntl``).

    Re-entrant within a process, because :func:`install_everything` and :func:`collect` are
    called both on their own and from inside another locked step.
    """
    global _lock_depth, _lock_file
    with _thread_lock:
        if _lock_depth == 0:
            here = root()
            here.mkdir(parents=True, exist_ok=True)
            handle = open(here / _LOCK_FILENAME, "w")  # noqa: SIM115 — held across the yield
            fcntl.flock(handle, fcntl.LOCK_EX)
            _lock_file = handle
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
            if _lock_depth == 0 and _lock_file is not None:
                fcntl.flock(_lock_file, fcntl.LOCK_UN)
                _lock_file.close()
                _lock_file = None


# ── installing ─────────────────────────────────────────────────────────────────────


def unmet(requirements: list[str]) -> list[str]:
    """Which of *requirements*' closure nothing importable satisfies — ``[]`` means ready."""
    return _closure(requirements, _Env.read())[1]


def ensure(app: str, requirements: list[str], *, label: str) -> bool:
    """Make *app*'s *requirements* importable, installing whatever is missing.

    Nothing to do — and no pip, no network — when the gateway or an already-installed app
    provides every requirement. Otherwise pip resolves *app*'s requirements together with every
    OTHER installed app's (*app*'s previous version, on an update, is replaced by these), so the
    result satisfies all of them. Raises :class:`PackageInstallError`.

    Returns whether the gateway must RESTART for the change to take effect: true only when a
    package that was already here changed version while this process has loaded modules from the
    directory. A first install adds modules nothing has imported yet, and :func:`activate` makes
    them importable in place.
    """
    if not requirements or not unmet(requirements):
        return False  # the common case, answered without the lock or the directory existing
    with _locked():
        env = _Env.read()
        if not _closure(requirements, env)[1]:
            return False  # another install provided them while this one waited for the lock
        others = [d for d in installed_apps() if d.name != app]
        before = env.app_versions()
        _pip_install(Declared(name=app, label=label, requirements=list(requirements)), others, env)
        activate()
        after = _Env.read()
        missing = _closure(requirements, after)[1]
        if missing:
            raise PackageInstallError(
                f"pip reported {label}'s Python packages as installed, but "
                f"{', '.join(missing)} still cannot be found in {root()}. Report this as a "
                "PersonalClaw bug."
            )
        now = after.app_versions()
        changed = any(now.get(key) != version for key, version in before.items())
        return changed and _loaded_from(root())


def broken_apps() -> list[tuple[Declared, list[str]]]:
    """Installed apps whose declared packages are not all importable, with what is missing."""
    env = _Env.read()
    out: list[tuple[Declared, list[str]]] = []
    for declared in installed_apps():
        missing = _closure(declared.requirements, env)[1]
        if missing:
            out.append((declared, missing))
    return out


def install_everything() -> None:
    """One pip run over every installed app's requirements — the boot-time rebuild after a new
    image's Python, a changed core dependency or a restored snapshot. Raises
    :class:`PackageInstallError`, attributed to the first app still missing something."""
    with _locked():
        broken = broken_apps()
        if not broken:
            return
        target = broken[0][0]
        others = [d for d in installed_apps() if d.name != target.name]
        _pip_install(target, others, _Env.read())
        activate()


def _pip_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _PIP_LOCATION_VARS}
    # pip decides what is already installed from its own import path, so it has to see the app
    # packages to reuse, upgrade or keep them; the base environment it sees on its own.
    inherited = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        [*([inherited] if inherited else []), *(str(d) for d in site_dirs())]
    )
    return env


def _parseable(declared: Declared) -> list[str]:
    """*declared*'s requirements pip can read. The install guard refuses an unparseable one
    before it is ever installed, so one here predates that guard; it is dropped (and logged)
    rather than failing every OTHER app's install on a line pip rejects outright."""
    from packaging.requirements import InvalidRequirement, Requirement

    kept: list[str] = []
    for spec in declared.requirements:
        try:
            Requirement(spec)
        except InvalidRequirement:
            logger.warning("app %s: ignoring unparseable python dependency %r", declared.name, spec)
            continue
        kept.append(spec)
    return kept


def _pip_install(target: Declared, others: list[Declared], env: _Env) -> None:
    from personalclaw._installer import NoInstallerError, prefix_install_argv

    requirements = sorted({spec for d in (target, *others) for spec in _parseable(d)})
    here = root()
    here.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pclaw-app-python-") as scratch:
        constraints = Path(scratch) / "constraints.txt"
        constraints.write_text("\n".join(env.pins()) + "\n", encoding="utf-8")
        try:
            argv = prefix_install_argv(
                [
                    "--prefix",
                    str(here),
                    "--constraint",
                    str(constraints),
                    "--disable-pip-version-check",
                    "--no-input",
                    "--progress-bar",
                    "off",
                    "--no-warn-script-location",
                    *requirements,
                ]
            )
        except NoInstallerError as exc:
            raise PackageInstallError(
                f"Couldn't install {target.label}'s Python packages: {exc}."
            ) from exc
        logger.info(
            "app %s: installing python packages %s into %s", target.name, requirements, here
        )
        try:
            proc = subprocess.run(  # noqa: S603 — requirements come from scanned manifests
                argv,
                capture_output=True,
                text=True,
                timeout=_PIP_TIMEOUT,
                env=_pip_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise PackageInstallError(
                f"Installing {target.label}'s Python packages ({_specs(target)}) took longer "
                f"than {_PIP_TIMEOUT // 60} minutes and was stopped. A slow connection or a very "
                "large package can do this; install it again to retry."
            ) from exc
        except OSError as exc:
            raise PackageInstallError(
                f"Couldn't start pip to install {target.label}'s Python packages: {exc}."
            ) from exc
    if proc.returncode != 0:
        output = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
        logger.warning(
            "app %s: pip exited %s installing %s:\n%s",
            target.name,
            proc.returncode,
            requirements,
            output[-4 * _LOG_TAIL_CHARS :],
        )
        message, actionable = explain_failure(output, target, others)
        raise PackageInstallError(
            message, log_excerpt=output.strip()[-_LOG_TAIL_CHARS:] if actionable else ""
        )


def _loaded_from(directory: Path) -> bool:
    """Has this process imported any module from *directory*?"""
    here = str(directory)
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if isinstance(path, str) and _within(path, here):
            return True
    return False


# ── explaining a failure ───────────────────────────────────────────────────────────

_OSERROR_RE = re.compile(r"\[Errno (\d+)\][^\n']*(?:'([^'\n]+)')?")
_NETWORK_RE = re.compile(
    r"NewConnectionError|NameResolutionError|Failed to establish a new connection|"
    r"Temporary failure in name resolution|Name or service not known|nodename nor servname|"
    r"Network is unreachable|No route to host|ConnectTimeoutError|ReadTimeoutError|ProxyError|"
    r"SSLError|CERTIFICATE_VERIFY_FAILED",
)
_HOST_RE = re.compile(r"host='([^']+)'")
_NO_MATCH_RE = re.compile(r"No matching distribution found for (\S+)")
_BUILD_RE = re.compile(
    r"Failed (?:building wheel for|to build) (?!installable\b)([A-Za-z0-9][A-Za-z0-9._-]*)"
)
_CONFLICT_BLOCK_RE = re.compile(r"The conflict is caused by:\n(.*?)(?:\n\s*\n|\Z)", re.S)
_CONSTRAINT_LINE_RE = re.compile(r"The user requested \(constraint\) (\S+?)==(\S+)")
_REQUESTED_LINE_RE = re.compile(r"The user requested (.+)")

_EACCES, _EROFS, _ENOSPC, _EDQUOT = 13, 30, 28, 122


def _specs(target: Declared) -> str:
    return ", ".join(target.requirements)


def _machine() -> str:
    return f"Python {sys.version_info.major}.{sys.version_info.minor} on {sysconfig.get_platform()}"


def explain_failure(output: str, target: Declared, others: list[Declared]) -> tuple[str, bool]:
    """The sentence a user reads for pip's *output*, and whether the log is worth handing on.

    Scans the WHOLE output, not its tail: pip reports an unreachable index as retry warnings at
    the top and then ends with the same "No matching distribution" line a package that does not
    exist ends with, so a tail-only reading tells an offline user their package is missing.
    """
    label, home = target.label, str(_manager.config_dir())

    oserror = _OSERROR_RE.search(output) if "due to an OSError" in output else None
    if oserror is not None:
        code, where = int(oserror.group(1)), oserror.group(2) or str(root())
        if code in (_EACCES, _EROFS):
            why = "permission denied" if code == _EACCES else "the file system is read-only"
            return (
                f"Couldn't install {label}'s Python packages: PersonalClaw can't write to "
                f"{where} ({why}). The folder PersonalClaw keeps its data in ({home}) has to be "
                f"writable by the user the gateway runs as (uid {os.getuid()}); fix its "
                "ownership or permissions, then install again.",
                False,
            )
        if code in (_ENOSPC, _EDQUOT):
            return (
                f"Couldn't install {label}'s Python packages: the disk that holds {home} is "
                "full. Free some space, then install again.",
                False,
            )

    if "ResolutionImpossible" in output or "conflicting dependencies" in output:
        return (
            f"{label} can't be installed alongside what this gateway already runs: "
            f"{_conflict(output, target, others)}. An app can add Python packages but not change "
            "one PersonalClaw or another installed app already uses, so nothing was installed. "
            f"A newer version of {label} may fix this; otherwise, report it to the app's author.",
            False,
        )

    if _NETWORK_RE.search(output):
        host = _HOST_RE.search(output)
        reach = host.group(1) if host else "the Python package index"
        return (
            f"Couldn't download {label}'s Python packages ({_specs(target)}): this machine "
            f"couldn't reach {reach}. Check its internet connection or proxy settings, then "
            "install again.",
            False,
        )

    built = _BUILD_RE.search(output)
    if built is not None:
        return (
            f"{built.group(1)} has no ready-made package for this machine ({_machine()}), and "
            f"building it from source failed, so {label} couldn't be installed.",
            True,
        )

    missing = _NO_MATCH_RE.search(output)
    if missing is not None:
        return (
            f"No release of {missing.group(1)} can be installed on this machine ({_machine()}), "
            f"so {label} can't be installed here. The app may not support this platform yet.",
            False,
        )

    return f"pip couldn't install {label}'s Python packages ({_specs(target)}).", True


def _normalized(spec: str) -> str:
    """A requirement as pip prints it (``qdrant-client<2,>=1.9`` for ``qdrant-client>=1.9,<2``)."""
    from packaging.requirements import InvalidRequirement, Requirement

    try:
        return str(Requirement(spec))
    except InvalidRequirement:
        return spec.strip()


def _conflict(output: str, target: Declared, others: list[Declared]) -> str:
    """pip's "The conflict is caused by" lines, with the requester of each named."""
    block = _CONFLICT_BLOCK_RE.search(output)
    if block is None:
        return "its packages need versions that conflict with packages already in use"
    mine = {_normalized(s) for s in target.requirements}
    parts: list[str] = []
    for raw in block.group(1).splitlines():
        line = raw.strip()
        if not line:
            continue
        pinned = _CONSTRAINT_LINE_RE.match(line)
        if pinned is not None:
            parts.append(f"PersonalClaw runs {pinned.group(1)} {pinned.group(2)}")
            continue
        requested = _REQUESTED_LINE_RE.match(line)
        if requested is not None:
            spec = _normalized(requested.group(1))
            owner = next(
                (d.label for d in others if spec in {_normalized(s) for s in d.requirements}),
                None,
            )
            if owner is not None and spec not in mine:
                parts.append(f"the installed app {owner} requires {spec}")
            else:
                parts.append(f"{target.label} requires {spec}")
            continue
        parts.append(line)
    return "; ".join(parts[:6]) or "its packages need versions that conflict with packages in use"


# ── collecting garbage ──────────────────────────────────────────────────────────────


def collect() -> list[str]:
    """Delete every distribution here that no app tree's requirement closure reaches.

    Also drops the layout of any OTHER Python version (``lib/python3.12`` after an image moved to
    3.13) and duplicate copies of one distribution. Removal is by each distribution's own RECORD,
    and never outside this directory. Returns ``"name version"`` for what it removed.
    """
    here = root()
    if not here.is_dir():
        return []
    removed: list[str] = []
    with _locked():
        env = _Env.read()
        reached, _ = _closure(_protected_requirements(), env)
        kept: list[importlib.metadata.Distribution] = []
        doomed: list[importlib.metadata.Distribution] = []
        for key, dists in env.apps.items():
            for position, dist in enumerate(dists):
                (kept if position == 0 and key in reached else doomed).append(dist)
        # Two distributions can list the SAME file — a stale duplicate dist-info beside the live
        # one, or two packages that both install one module (`opencv-python` and
        # `opencv-python-headless` each write `cv2/`). Deleting the doomed one's RECORD verbatim
        # would delete files the kept one is running on, so a path any kept distribution lists
        # is never deleted.
        keep = frozenset(path for dist in kept for path in _record_paths(dist))
        for dist in doomed:
            label = f"{dist.metadata['Name']} {dist.version}"  # read before it is deleted
            if _remove_distribution(dist, here, keep=keep):
                removed.append(label)
        _drop_other_python_versions()
    if removed:
        importlib.invalidate_caches()
        logger.info("app packages: removed %d no app still needs: %s", len(removed), removed)
    return removed


def _record_paths(dist: importlib.metadata.Distribution) -> list[str]:
    """Each file *dist*'s RECORD lists, as a real path: the parent directory resolved (so a
    symlinked directory cannot hide where a file lives), the file name kept as written (so a
    symlink is the link itself, never its target)."""
    out: list[str] = []
    for entry in dist.files or []:
        lexical = os.path.normpath(os.path.abspath(str(dist.locate_file(entry))))
        out.append(
            os.path.join(os.path.realpath(os.path.dirname(lexical)), os.path.basename(lexical))
        )
    return out


def _remove_distribution(
    dist: importlib.metadata.Distribution, here: Path, *, keep: frozenset[str] = frozenset()
) -> bool:
    """Delete *dist*'s files as its RECORD lists them — each one only if it resolves inside
    *here* (a RECORD entry is data, and ``../`` or a symlinked directory must not walk out), and
    none that *keep* names."""
    if dist.files is None:
        logger.warning(
            "app packages: %s has no RECORD, so it is left in place", dist.metadata["Name"]
        )
        return False
    real_root = os.path.realpath(here)
    parents: set[str] = set()
    for target in _record_paths(dist):
        parent = os.path.dirname(target)
        if not _within(parent, real_root) or parent == real_root or target in keep:
            continue
        with contextlib.suppress(FileNotFoundError):
            if os.path.islink(target) or os.path.isfile(target):
                os.unlink(target)
        parents.add(parent)
    for directory in sorted(parents, key=len, reverse=True):
        _prune_upwards(directory, real_root)
    return True


def _prune_upwards(directory: str, stop: str) -> None:
    """Remove *directory* and its parents while they hold nothing but bytecode caches."""
    while _within(directory, stop) and directory != stop:
        try:
            entries = os.listdir(directory)
        except OSError:
            return
        if any(e != "__pycache__" for e in entries):
            return
        shutil.rmtree(directory, ignore_errors=True)
        directory = os.path.dirname(directory)


def _drop_other_python_versions() -> None:
    for directory in site_dirs():
        version_dir, lib = directory.parent, directory.parent.parent
        if not version_dir.name.startswith("python") or not lib.is_dir():
            continue
        for sibling in lib.iterdir():
            if sibling.is_dir() and sibling.name.startswith("python") and sibling != version_dir:
                shutil.rmtree(sibling, ignore_errors=True)
                logger.info("app packages: removed %s (another Python version's layout)", sibling)
