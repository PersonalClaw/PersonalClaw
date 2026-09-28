"""Vendor-neutral launch-argv resolution for ACP CLI adapters.

An ACP provider bundle (``acp:<cli>``) needs to turn "the name of a CLI" into a
concrete launch ``argv`` that works inside the gateway daemon — where ``$PATH``
is often the minimal one the supervisor inherited, not the user's interactive
shell PATH (so a CLI installed via nvm/mise/volta or ``npm i -g`` is invisible to
a bare :func:`shutil.which`). This module owns that resolution **mechanism**; it
is parameterised by env-var name, bin names, and an optional npm-package fallback
so it carries **zero** knowledge of any specific CLI. The vendor-specific values
(env-var name, bin names, npm package) live in the per-CLI bundles that call this.

Resolution order (first hit wins):

1. ``$<ENV_VAR>`` — an explicit operator override (absolute path or argv;
   honoured verbatim, never re-validated against PATH).
2. :func:`shutil.which` for each bin name (the daemon's PATH).
3. Common node-version-manager install roots (nvm / mise / asdf / volta / fnm)
   and the global ``npm root -g`` bin dir — globbed for each bin name. This is
   what makes a ``npm i -g`` CLI resolvable from a daemon with a minimal PATH.
4. ``npx -y <npm_pkg>`` as a last resort, when an npm package name is supplied
   (lets a never-installed adapter still run, at the cost of a cold ``npx`` fetch).

A ``.js`` entry script is returned as ``[node, script, ...]`` rather than
``[script, ...]`` so it does not depend on the shebang resolving a ``node`` that
may not be on the daemon PATH.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Iterator
from datetime import datetime, timezone
from glob import glob
from pathlib import Path

__all__ = [
    "resolve_acp_cli",
    "node_argv_for_script",
    "is_npx_fallback",
    "npx_package",
    "resolve_node_ge",
    "provision_acp_adapter",
    "adapter_installs_allowed",
    "adapter_install_failure",
]

logger = logging.getLogger(__name__)

# Minimum Node major the ACP adapters (@agentclientprotocol/*) require. Provisioning
# picks an interpreter at least this new; a lower one (e.g. a mise-pinned Node 18)
# is skipped so the install/run can't fail with EBADENGINE.
_MIN_NODE_MAJOR = 20


def node_argv_for_script(path: str) -> list[str]:
    """Return a launch argv for *path*, prefixing ``node`` for ``.js`` entries.

    A ``.js`` script relies on its shebang (``#!/usr/bin/env node``) to find an
    interpreter; in a daemon with a minimal PATH that shebang can fail. Prefixing
    the resolved ``node`` (falling back to the literal ``node`` if none is found)
    sidesteps it. Non-``.js`` entries (native binaries, extension-less shims) are
    returned unchanged.
    """
    if path.endswith(".js"):
        node = shutil.which("node") or "node"
        return [node, path]
    return [path]


def _node_manager_bin_globs() -> list[str]:
    """Glob patterns for bin dirs created by common node version managers.

    Patterns are expanded against ``$HOME`` and a few well-known prefixes so a
    CLI installed under nvm/mise/asdf/volta/fnm — or globally via npm — is
    findable even when the daemon PATH omits those dirs.

    A deployment whose CLIs live elsewhere (e.g. an enterprise package manager
    that installs to a non-standard prefix) can add colon-separated bin dirs via
    the ``PERSONALCLAW_EXTRA_BIN_PATHS`` env var without editing core.
    """
    home = Path(os.path.expanduser("~"))
    globs = [
        # nvm: ~/.nvm/versions/node/<ver>/bin
        str(home / ".nvm" / "versions" / "node" / "*" / "bin"),
        # mise / rtx: ~/.local/share/{mise,rtx}/installs/node/<ver>/bin
        str(home / ".local" / "share" / "mise" / "installs" / "node" / "*" / "bin"),
        str(home / ".local" / "share" / "rtx" / "installs" / "node" / "*" / "bin"),
        # asdf: ~/.asdf/installs/nodejs/<ver>/bin
        str(home / ".asdf" / "installs" / "nodejs" / "*" / "bin"),
        # volta: ~/.volta/bin
        str(home / ".volta" / "bin"),
        # fnm: ~/.fnm/node-versions/<ver>/installation/bin
        str(home / ".fnm" / "node-versions" / "*" / "installation" / "bin"),
        # Common global npm prefixes.
        str(home / ".npm-global" / "bin"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
    ]
    # PersonalClaw-managed adapter prefix — where auto-provisioned ACP adapters
    # land (see provision_acp_adapter). Searched so a provisioned adapter resolves
    # as a real binary on the next resolve, never via npx. Uses config_dir() so it
    # tracks a custom PERSONALCLAW_HOME (best-effort; skipped if it can't resolve).
    try:
        from personalclaw.config.loader import config_dir

        globs.append(str(config_dir() / "acp-adapters" / "node_modules" / ".bin"))
    except Exception:
        pass
    extra = os.environ.get("PERSONALCLAW_EXTRA_BIN_PATHS", "")
    globs.extend(p for p in (s.strip() for s in extra.split(os.pathsep)) if p)
    return globs


# Public alias for the SDK surface (sdk.acp) — ACP-bundle apps (e.g. codex) need the
# node-manager bin globs to locate their CLI; expose it without the leading underscore.
node_manager_bin_globs = _node_manager_bin_globs


#: ``npm root -g`` per npm executable. It cannot change while this process runs, and it is
#: not cheap: a cold ``npm`` startup took 5-7 s in the container, paid on EVERY resolution
#: that missed PATH — twice per CLI at boot (``create_provider`` then ``login_command``) and
#: again inside every availability check of kiro-cli and gemini-cli. Only an answer is kept:
#: a timed-out or failed run is asked again next time.
_NPM_GLOBAL_ROOTS: dict[str, str] = {}


def _npm_global_root(npm: str) -> str:
    """``npm root -g`` for one npm executable (memoized), or ``""`` when it gave no answer."""
    cached = _NPM_GLOBAL_ROOTS.get(npm)
    if cached is not None:
        return cached
    from personalclaw._installer import installer_env

    try:
        out = subprocess.run(
            [npm, "root", "-g"],
            capture_output=True,
            text=True,
            timeout=5,
            env=installer_env(),
        )
    except Exception:
        return ""
    root = (out.stdout or "").strip()
    if root:
        _NPM_GLOBAL_ROOTS[npm] = root
    return root


def _npm_root_global_bin() -> str | None:
    """Return the global npm ``bin`` dir (``$(npm root -g)/../.bin``) if resolvable.

    ``npm root -g`` prints the global ``node_modules`` dir; its sibling ``.bin``
    holds the global CLI shims. Best-effort and fast-timeout — never raises. The
    directory test runs every time: a first ``npm i -g`` creates it after the root
    was memoized.
    """
    npm = shutil.which("npm")
    if not npm:
        return None
    root = _npm_global_root(npm)
    if not root:
        return None
    bin_dir = Path(root).parent / ".bin"
    return str(bin_dir) if bin_dir.is_dir() else None


def resolve_acp_cli(
    *,
    env_var: str,
    bin_names: list[str],
    npm_pkg: str | None = None,
    subcommand: list[str] | None = None,
) -> list[str] | None:
    """Resolve a launch argv for an ACP CLI adapter, or ``None`` if unresolved.

    Parameters
    ----------
    env_var:
        Name of an environment variable an operator can set to override
        resolution. Its value may be an absolute path to the entry OR a full
        argv (whitespace-split). Honoured verbatim — ``subcommand`` is NOT
        appended to an override (the operator supplies the complete argv).
    bin_names:
        Candidate executable basenames to look for on PATH / in node-manager
        bin dirs, in priority order (e.g. ``["claude-code-acp"]``).
    npm_pkg:
        Optional npm package name; when supplied and nothing else resolves,
        returns ``["npx", "-y", <npm_pkg>, *subcommand]`` as a last resort.
    subcommand:
        Optional args appended to the resolved binary to put it into ACP
        stdio-protocol mode (e.g. ``["acp"]`` for ``<cli> acp``). Appended to
        the PATH/glob/npx resolutions, but NOT to an explicit env override.

    Returns
    -------
    A launch argv (``list[str]``) ready to spawn, or ``None`` when no candidate
    is found and no ``npm_pkg`` fallback is available. ``.js`` entries are
    returned in ``[node, script]`` form.
    """
    extra = list(subcommand or [])

    # 1. Explicit operator override — complete argv, no subcommand appended.
    override = os.environ.get(env_var, "").strip()
    if override:
        parts = override.split()
        if len(parts) == 1:
            return node_argv_for_script(parts[0])
        # A full argv was supplied — honour it verbatim.
        return parts

    # 2. PATH lookup for each bin name.
    for name in bin_names:
        found = shutil.which(name)
        if found:
            return node_argv_for_script(found) + extra

    # 3. Node-version-manager + global-npm bin dirs.
    search_dirs = list(_node_manager_bin_globs())
    npm_bin = _npm_root_global_bin()
    if npm_bin:
        search_dirs.append(npm_bin)
    for name in bin_names:
        for pattern in search_dirs:
            # Join the bin name on and glob the whole thing so version
            # wildcards in the dir pattern expand.
            for hit in sorted(glob(str(Path(pattern) / name))):
                if not os.path.isdir(hit) and os.access(hit, os.X_OK):
                    return node_argv_for_script(hit) + extra

    # 4. npx last resort.
    if npm_pkg:
        npx = shutil.which("npx") or "npx"
        return [npx, "-y", npm_pkg, *extra]

    return None


def npx_package(argv: list[str] | None) -> str:
    """The npm package an ``npx -y <pkg>`` argv fetches, or ``""`` for any other argv."""
    if not is_npx_fallback(argv):
        return ""
    return next((str(part) for part in (argv or [])[1:] if not str(part).startswith("-")), "")


def is_npx_fallback(argv: list[str] | None) -> bool:
    """True when *argv* is the ``npx -y <pkg>`` last-resort, not a real adapter.

    A resolved argv is the npx fallback iff its first element's basename is
    ``npx``. Callers use this to tell "the adapter is installed on disk" (steps
    1-3 of :func:`resolve_acp_cli`) from "nothing is installed, we'd fetch-and-run
    it transiently" (step 4) — the latter is fragile (needs a good, ≥20 Node + a
    clean npx cache) and is what auto-provisioning + the readiness gate act on.
    """
    if not argv:
        return False
    return Path(argv[0]).name.lower() in ("npx", "npx.cmd")


def resolve_node_ge(min_major: int = _MIN_NODE_MAJOR) -> str | None:
    """Return a ``node`` executable whose major version is ≥ *min_major*, or None.

    Searches PATH then the node-version-manager bin dirs (newest first), so a
    machine whose default ``node`` is too old (e.g. a mise-pinned Node 18) still
    yields a usable interpreter if a newer one is installed anywhere. Best-effort;
    never raises. This is what lets provisioning avoid the ``EBADENGINE`` wall
    that a bare ``npx`` under the pinned-old Node hits.
    """
    candidates: list[str] = []
    on_path = shutil.which("node")
    if on_path:
        candidates.append(on_path)
    # node-manager dirs, newest version dir first (reverse-sorted glob).
    for pattern in _node_manager_bin_globs():
        for hit in sorted(glob(str(Path(pattern) / "node")), reverse=True):
            if not os.path.isdir(hit) and os.access(hit, os.X_OK):
                candidates.append(hit)
    for node in candidates:
        try:
            out = subprocess.run(
                [node, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            continue
        ver = (out.stdout or "").strip().lstrip("v")
        major = ver.split(".", 1)[0]
        if major.isdigit() and int(major) >= min_major:
            return node
    return None


def _managed_bin_dir() -> Path:
    """The PersonalClaw-managed dir where auto-provisioned adapters are installed.

    Under ``~/.personalclaw/acp-adapters`` (an ``npm --prefix`` root); its
    ``node_modules/.bin`` is added to the resolver's search dirs, so an adapter
    installed here is found by :func:`resolve_acp_cli` on the next resolve — no
    reliance on the shared, corruption-prone npx cache.
    """
    from personalclaw.config.loader import config_dir

    return config_dir() / "acp-adapters"


#: Whether the user is installing or enabling an app right now — the one moment an ACP adapter
#: may be installed (:func:`adapter_installs_allowed`). Off everywhere else, a gateway start
#: included, so an adapter whose install failed is never tried again behind the user's back.
_INSTALLS_ALLOWED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "pclaw_acp_adapter_installs_allowed", default=False
)

#: Beside the provenance lock in the managed prefix: why the last install of each adapter failed.
INSTALL_FAILURES_NAME = ".pclaw-install-failures.json"


@contextlib.contextmanager
def adapter_installs_allowed() -> Iterator[None]:
    """While the user installs or enables an app, let its providers install the ACP adapter they
    need (:func:`provision_acp_adapter`). ``app_manager`` holds this around the load an install
    and an enable end with, and nothing else does: that is the moment the user said yes."""
    token = _INSTALLS_ALLOWED.set(True)
    try:
        yield
    finally:
        _INSTALLS_ALLOWED.reset(token)


def _install_failures_path() -> Path:
    return _managed_bin_dir() / INSTALL_FAILURES_NAME


def _read_install_failures() -> dict:
    try:
        raw = json.loads(_install_failures_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_install_failures(failures: dict) -> None:
    from personalclaw.atomic_write import atomic_write

    try:
        _managed_bin_dir().mkdir(parents=True, exist_ok=True)
        atomic_write(
            _install_failures_path(), json.dumps(failures, indent=2, sort_keys=True) + "\n"
        )
    except Exception:  # noqa: BLE001 - the install's answer still reaches the caller
        logger.warning("acp adapter install failures could not be recorded", exc_info=True)


def _install_failed(npm_pkg: str, reason: str) -> None:
    """Remember why installing *npm_pkg* failed, so its app's card can say so and offer Retry."""
    logger.warning("acp adapter %s: install failed — %s", npm_pkg, reason)
    failures = _read_install_failures()
    failures[npm_pkg] = {
        "error": reason,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _write_install_failures(failures)


def _install_succeeded(npm_pkg: str) -> None:
    failures = _read_install_failures()
    if failures.pop(npm_pkg, None) is not None:
        _write_install_failures(failures)


def adapter_install_failure(npm_pkg: str) -> dict | None:
    """``{"error", "at"}`` for the last failed install of *npm_pkg*, or ``None``.

    Kept until an install of it succeeds. A read: it installs and retries nothing."""
    found = _read_install_failures().get(npm_pkg) if npm_pkg else None
    if not isinstance(found, dict) or not found.get("error"):
        return None
    return {"error": str(found["error"]), "at": str(found.get("at") or "")}


def provision_acp_adapter(
    npm_pkg: str,
    bin_names: list[str],
    *,
    pin_version: str = "",
    expected_integrity: str = "",
) -> str | None:
    """The adapter *npm_pkg* installed in the managed prefix, installing it under a Node ≥20
    when the user is installing or enabling its app. Returns its binary's path, else None.

    Idempotent: an adapter already in the managed prefix is returned without re-installing. An
    install happens only inside :func:`adapter_installs_allowed` — the consent moment; at any
    other time (a gateway start, an update) this returns the installed adapter or None, so a
    failed install is never retried behind the user's back. A failure is remembered with its
    reason (:func:`adapter_install_failure`) until an install succeeds; the app's card says so
    and offers Retry, which enables the app again.

    The install runs ``npm install --prefix <managed>`` with a Node ≥20 on PATH
    (:func:`resolve_node_ge`) so it never trips ``EBADENGINE``, and writes to a private prefix
    so a wedged global/npx cache can't block it. Best-effort; never raises.

    Set ``PERSONALCLAW_ACP_NO_PROVISION=1`` to disable — for tests/CI (no network
    installs as a side effect) and frozen/desktop builds (a locked environment
    where a runtime ``npm install`` is undesirable). When disabled, an already-
    provisioned adapter is still returned (idempotent read), but nothing installs.

    ``pin_version`` / ``expected_integrity`` carry a catalog row's adapter pin. A pinned
    version is installed as ``<pkg>@<version>`` rather than the floating latest, and the
    install's provenance is recorded into the managed prefix's ``.pclaw-lock.json`` afterwards
    so :func:`personalclaw.agents.runners.verify_adapter` can prove later that the adapter on
    disk is still the one that was installed. A pin MISMATCH records nothing — the adapter
    stays unverified rather than being blessed by the recording.
    """
    prefix = _managed_bin_dir()
    bin_dir = prefix / "node_modules" / ".bin"

    # Already provisioned? Return it (idempotent) — even when installs are disabled.
    for name in bin_names:
        cand = bin_dir / name
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)

    if os.environ.get("PERSONALCLAW_ACP_NO_PROVISION") == "1":
        logger.debug(
            "acp adapter %s: provisioning disabled (PERSONALCLAW_ACP_NO_PROVISION)", npm_pkg
        )
        return None

    if not _INSTALLS_ALLOWED.get():
        logger.debug(
            "acp adapter %s: not installing — an adapter installs only when you enable its app",
            npm_pkg,
        )
        return None

    node = resolve_node_ge()
    if not node:
        _install_failed(
            npm_pkg,
            f"no Node {_MIN_NODE_MAJOR} or newer was found, and the adapter needs it: install "
            "a newer Node, or set the app's adapter path",
        )
        return None
    npm = shutil.which(
        "npm", path=os.pathsep.join([str(Path(node).parent), os.environ.get("PATH", "")])
    )
    if not npm:
        _install_failed(npm_pkg, f"npm was not found beside {node}")
        return None

    try:
        prefix.mkdir(parents=True, exist_ok=True)
        from personalclaw._installer import installer_cache_env
        from personalclaw.sandbox import build_child_env

        # npm runs the install scripts of the adapter and of every package it depends on, so
        # the install gets the child allowlist and npm's own settings, never the gateway's
        # environment and the secrets in it, and npm's cache in the home. The chosen Node goes
        # first on PATH so npm's engine check and those scripts run under it, not the (possibly
        # too-old) default node.
        env = build_child_env(
            site="acp-adapter-install",
            installer="npm",
            extra={
                **installer_cache_env(),
                "PATH": os.pathsep.join([str(Path(node).parent), os.environ.get("PATH", "")]),
            },
        )
        logger.info("acp adapter %s: provisioning under %s into %s", npm_pkg, node, prefix)
        spec = f"{npm_pkg}@{pin_version}" if pin_version else npm_pkg
        proc = subprocess.run(
            [npm, "install", "--prefix", str(prefix), "--no-fund", "--no-audit", spec],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
        if proc.returncode != 0:
            from personalclaw.security import mask_child_output

            # What npm printed is not PersonalClaw's text: masked before it is kept, shown on
            # the app's card and printed by doctor, and its lines joined into one to read there.
            said = " ".join(
                line.strip()
                for line in mask_child_output(
                    proc.stderr or proc.stdout, limit=400, tail=True, one_line=False
                ).split("\n")
                if line.strip()
            )
            _install_failed(
                npm_pkg,
                f"npm install exited {proc.returncode}" + (f": {said}" if said else ""),
            )
            return None
    except subprocess.TimeoutExpired:
        _install_failed(npm_pkg, "npm install ran past 180 seconds and was stopped")
        return None
    except Exception as exc:  # noqa: BLE001 - the failure is remembered and said
        from personalclaw.security import mask_child_output

        logger.debug("acp adapter %s: provisioning errored", npm_pkg, exc_info=True)
        _install_failed(npm_pkg, f"npm install could not run: {mask_child_output(str(exc))}")
        return None

    # Record what npm actually installed BEFORE returning the path, so the very first
    # resolve after provisioning can already verify the adapter's provenance. A pin
    # mismatch refuses to record (and says so), leaving the adapter unverified.
    try:
        from personalclaw.agents.runners import AdapterPin, record_provenance

        pin = (
            AdapterPin(npm_pkg=npm_pkg, version=pin_version, integrity=expected_integrity)
            if (pin_version and expected_integrity)
            else None
        )
        if not record_provenance(npm_pkg, pin=pin):
            logger.warning(
                "acp adapter %s: provenance NOT recorded — it will read as unverified", npm_pkg
            )
    except Exception:
        logger.warning("acp adapter %s: provenance recording errored", npm_pkg, exc_info=True)

    for name in bin_names:
        cand = bin_dir / name
        if cand.exists() and os.access(cand, os.X_OK):
            logger.info("acp adapter %s: provisioned → %s", npm_pkg, cand)
            _install_succeeded(npm_pkg)
            return str(cand)
    _install_failed(
        npm_pkg, f"npm installed it, but no {' or '.join(bin_names)} command came with it"
    )
    return None
