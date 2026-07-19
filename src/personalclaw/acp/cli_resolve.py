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

import logging
import os
import shutil
import subprocess
from glob import glob
from pathlib import Path

__all__ = [
    "resolve_acp_cli",
    "node_argv_for_script",
    "is_npx_fallback",
    "resolve_node_ge",
    "provision_acp_adapter",
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


def _npm_root_global_bin() -> str | None:
    """Return the global npm ``bin`` dir (``$(npm root -g)/../.bin``) if resolvable.

    ``npm root -g`` prints the global ``node_modules`` dir; its sibling ``.bin``
    holds the global CLI shims. Best-effort and fast-timeout — never raises.
    """
    npm = shutil.which("npm")
    if not npm:
        return None
    try:
        import subprocess

        out = subprocess.run(
            [npm, "root", "-g"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    root = (out.stdout or "").strip()
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
                [node, "--version"], capture_output=True, text=True, timeout=5,
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


def provision_acp_adapter(npm_pkg: str, bin_names: list[str]) -> str | None:
    """Install *npm_pkg* under a Node ≥20 interpreter into the managed prefix.

    Returns the resolved adapter binary path on success, else None. Idempotent:
    if the adapter is already present in the managed prefix it is returned without
    re-installing. The install runs ``npm install --prefix <managed>`` with a
    Node ≥20 on PATH (:func:`resolve_node_ge`) so it never trips ``EBADENGINE``,
    and writes to a private prefix so a wedged global/npx cache can't block it.

    This is the "an adapter provisions its own dependency" path: a bundle whose
    only resolution would be the npx fallback calls this once to turn the transient
    fetch-and-run into a durable on-disk install. Best-effort; never raises.

    Set ``PERSONALCLAW_ACP_NO_PROVISION=1`` to disable — for tests/CI (no network
    installs as a side effect) and frozen/desktop builds (a locked environment
    where a runtime ``npm install`` is undesirable). When disabled, an already-
    provisioned adapter is still returned (idempotent read), but nothing installs.
    """
    prefix = _managed_bin_dir()
    bin_dir = prefix / "node_modules" / ".bin"

    # Already provisioned? Return it (idempotent) — even when installs are disabled.
    for name in bin_names:
        cand = bin_dir / name
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)

    if os.environ.get("PERSONALCLAW_ACP_NO_PROVISION") == "1":
        logger.debug("acp adapter %s: provisioning disabled (PERSONALCLAW_ACP_NO_PROVISION)", npm_pkg)
        return None

    node = resolve_node_ge()
    if not node:
        logger.warning(
            "acp adapter %s: cannot auto-provision — no Node >= %d found "
            "(install a newer Node or set the adapter's *_ACP_BIN override)",
            npm_pkg, _MIN_NODE_MAJOR,
        )
        return None
    npm = shutil.which("npm", path=os.pathsep.join(
        [str(Path(node).parent), os.environ.get("PATH", "")]))
    if not npm:
        logger.warning("acp adapter %s: npm not found alongside %s", npm_pkg, node)
        return None

    try:
        prefix.mkdir(parents=True, exist_ok=True)
        # Put the chosen Node first on PATH so npm's engine check + any lifecycle
        # scripts run under it, not the (possibly too-old) default node.
        env = {**os.environ, "PATH": os.pathsep.join(
            [str(Path(node).parent), os.environ.get("PATH", "")])}
        logger.info("acp adapter %s: provisioning under %s into %s", npm_pkg, node, prefix)
        proc = subprocess.run(
            [npm, "install", "--prefix", str(prefix), "--no-fund", "--no-audit", npm_pkg],
            capture_output=True, text=True, timeout=180, env=env,
        )
        if proc.returncode != 0:
            logger.warning(
                "acp adapter %s: provisioning failed (rc=%d): %s",
                npm_pkg, proc.returncode, (proc.stderr or "")[-400:],
            )
            return None
    except Exception:
        logger.warning("acp adapter %s: provisioning errored", npm_pkg, exc_info=True)
        return None

    for name in bin_names:
        cand = bin_dir / name
        if cand.exists() and os.access(cand, os.X_OK):
            logger.info("acp adapter %s: provisioned → %s", npm_pkg, cand)
            return str(cand)
    logger.warning("acp adapter %s: installed but no bin found in %s", npm_pkg, bin_dir)
    return None
