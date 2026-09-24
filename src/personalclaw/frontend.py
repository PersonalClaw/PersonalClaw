"""Shared helpers for building frontend assets."""

import asyncio
import hashlib
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

from personalclaw.cancellation import kill_timed_out

logger = logging.getLogger(__name__)

_DIR_NAME = "web"

# Named so a test can inject a deadline instead of sleeping on one.
_NPM_CI_TIMEOUT = 180.0
_NPM_BUILD_TIMEOUT = 120.0


def resolve_website_dist(pkg_dir: Path) -> Optional[Path]:
    """Locate a usable ``web/dist``.

    The package lives at ``<repo>/src/personalclaw``, so the repo root is two
    levels up. Probes repo root — ``<repo>/web/dist``.

    PUBLIC because it is also the only honest answer to *"could ``static/dist`` be a
    symlink here at all?"* — ``None`` means an installed layout, where a real directory
    is what the wheel ships and not a shadowing copy. The doctor probe and the
    ``serving-fs.symlink-repair`` fix both need that discriminator, and both used to
    re-derive it (the fix's ``_dist_paths`` docstring apologised for doing so). One
    derivation, so a probe and its own remediation cannot disagree about the layout.
    """
    repo_root = pkg_dir.parent.parent

    top_level_dist = repo_root / _DIR_NAME / "dist"
    if top_level_dist.is_dir() and (top_level_dist / "index.html").is_file():
        return top_level_dist.resolve()

    return None


# ---------------------------------------------------------------------------
# SPA build freshness — the SECOND variant of the stale-SPA bug-class.
#
# ``ensure_dev_dist_symlink`` (and the doctor probe that mirrors its detection)
# catch a static/dist *copy* shadowing the runtime symlink. Neither can see the
# other way a stale SPA reaches a browser: the symlink is perfectly correct, the
# target resolves, index.html is right there — and the BUILD BEHIND IT predates
# the checked-out sources. Every read-only signal those checks have says healthy.
#
# Freshness is keyed off a CONTENT DIGEST of the build inputs, never mtimes.
# Measured on the standing validation rig, 2026-09-19: a checkout landed at
# 23:41 and a vite build finished at 23:44, leaving every file in web/dist
# NEWER than every file in web/src — while the bundle had in fact been built
# from the pre-checkout sources, because the build started before the checkout
# landed. An mtime comparison calls that rig fresh. It was serving a bundle two
# commits behind, which is precisely how a frontend clause gets honestly but
# wrongly reported absent by whoever drives it.
#
# The digest deliberately excludes test files: vite's entry graph never imports
# them, so a test-only edit cannot change the bundle and must not read as stale.
# ---------------------------------------------------------------------------

_STAMP_NAME = ".build-inputs.json"
_SPA_INPUT_FILES = (
    "index.html",
    "vite.config.ts",
    "package.json",
    "tsconfig.json",
    "tsconfig.sw.json",
)
_SPA_INPUT_DIRS = ("src", "public")
_SPA_TEST_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")


def _iter_spa_inputs(repo_root: Path) -> Iterator[tuple[str, Path]]:
    """Yield ``(repo-relative posix path, file)`` for every SPA build input."""
    web = repo_root / _DIR_NAME
    for name in _SPA_INPUT_FILES:
        candidate = web / name
        if candidate.is_file():
            yield f"{_DIR_NAME}/{name}", candidate
    for dir_name in _SPA_INPUT_DIRS:
        root = web / dir_name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.name.endswith(_SPA_TEST_SUFFIXES):
                continue
            yield path.relative_to(repo_root).as_posix(), path
    # Dependency versions change the emitted bundle, so the lockfile is an input.
    # Single-root lockfile (npm/cli#4828): only the repo root carries one.
    lock = repo_root / "package-lock.json"
    if lock.is_file():
        yield "package-lock.json", lock


def spa_build_input_digest(repo_root: Path) -> Optional[str]:
    """Digest of everything the SPA bundle is built from.

    ``None`` when there is no source tree to compare against — an installed
    wheel ships a prebuilt ``static/dist`` and no ``web/src``, so freshness is
    not a question that can be asked there, let alone failed.
    """
    if not (repo_root / _DIR_NAME / "src").is_dir():
        return None
    digest = hashlib.sha256()
    for rel, path in sorted(_iter_spa_inputs(repo_root), key=lambda item: item[0]):
        try:
            body = path.read_bytes()
        except OSError:
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(body).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def write_spa_build_stamp(repo_root: Path) -> Optional[str]:
    """Record what the freshly built ``web/dist`` was built FROM.

    Called by the build path so a later reader can tell a current bundle from a
    stale one. Returns the digest written, or ``None`` when there is nothing to
    stamp. The stamp lives inside ``web/dist``, which ``.gitignore`` covers.
    """
    digest = spa_build_input_digest(repo_root)
    dist = repo_root / _DIR_NAME / "dist"
    if digest is None or not dist.is_dir():
        return None
    payload = {"inputs_sha256": digest, "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        (dist / _STAMP_NAME).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write SPA build stamp: %s", exc)
        return None
    return digest


def spa_dist_freshness(repo_root: Path) -> tuple[str, dict]:
    """Is the built SPA current for the checked-out sources?

    Returns ``(state, evidence)`` where state is one of:

    ``no-sources``
        No ``web/src`` — an installed wheel. Not checkable, never a fault.
    ``no-dist``
        Nothing built yet.
    ``unstamped``
        A build exists but carries no provenance, so it cannot be *proven*
        current. Raw ``npm run build`` (what CI runs) leaves this state, and a
        CI build is structurally incapable of being stale — so this is reported,
        not treated as a fault. A caller that can cheaply rebuild should.
    ``stale``
        The bundle was built from different sources than the ones checked out.
    ``fresh``
        Proven current.
    """
    actual = spa_build_input_digest(repo_root)
    if actual is None:
        return "no-sources", {}
    dist = repo_root / _DIR_NAME / "dist"
    if not (dist / "index.html").is_file():
        return "no-dist", {"inputs_sha256": actual}
    stamp = dist / _STAMP_NAME
    if not stamp.is_file():
        return "unstamped", {"inputs_sha256": actual}
    try:
        recorded = json.loads(stamp.read_text(encoding="utf-8")).get("inputs_sha256")
    except (OSError, ValueError):
        return "unstamped", {"inputs_sha256": actual}
    evidence = {"inputs_sha256": actual, "built_from_sha256": recorded}
    return ("fresh" if recorded == actual else "stale"), evidence


def ensure_dev_dist_symlink() -> Optional[Path]:
    """Make the web React build discoverable at runtime.

    The dashboard serves its SPA from ``<personalclaw>/static/dist/index.html``.
    Build the web app first: ``cd web && npm ci && npm run build``.
    """
    pkg_dir = Path(__file__).resolve().parent
    tree_dist = pkg_dir / "static" / "dist"

    if tree_dist.is_dir() and not tree_dist.is_symlink():
        if (tree_dist / "index.html").is_file():
            return tree_dist

    if tree_dist.is_symlink():
        try:
            target = tree_dist.resolve(strict=True)
        except (FileNotFoundError, OSError):
            target = None
        if target is not None and (target / "index.html").is_file():
            return target
        try:
            tree_dist.unlink()
        except OSError as exc:
            logger.warning("Failed to remove stale dist symlink %s: %s", tree_dist, exc)
            return None

    candidate = resolve_website_dist(pkg_dir)
    if candidate is None:
        return None

    tree_dist.parent.mkdir(parents=True, exist_ok=True)
    if tree_dist.exists() or tree_dist.is_symlink():
        try:
            if tree_dist.is_dir() and not tree_dist.is_symlink():
                shutil.rmtree(tree_dist)
            else:
                tree_dist.unlink()
        except OSError as exc:
            logger.warning("Failed to clear %s before symlink: %s", tree_dist, exc)
            return None
    try:
        tree_dist.symlink_to(candidate)
    except OSError as exc:
        logger.warning("Failed to symlink %s -> %s: %s", tree_dist, candidate, exc)
        return None
    logger.info("Linked frontend dist: %s -> %s", tree_dist, candidate)
    return candidate


def _propagate_dist(
    built_dist: Path,
    proj_path: Path,
    log: Callable[[str], None] = print,
) -> None:
    """Ensure static/dist points to the freshly built web dist."""
    static_dist = proj_path / "src" / "personalclaw" / "static" / "dist"
    if static_dist.is_symlink() and static_dist.resolve() == built_dist.resolve():
        return
    if static_dist.is_symlink() or (static_dist.is_dir() and not static_dist.is_symlink()):
        try:
            if static_dist.is_dir() and not static_dist.is_symlink():
                shutil.rmtree(static_dist)
            else:
                static_dist.unlink()
        except OSError as exc:
            log(f"  Could not remove stale static/dist: {exc}")
            return
    try:
        static_dist.symlink_to(built_dist)
        log(f"  Linked static/dist -> {built_dist}")
    except OSError as exc:
        log(f"  Could not symlink static/dist: {exc}")


def build_frontend_sync(
    proj_path: Path,
    log: Callable[[str], None] = print,
) -> None:
    """Build frontend assets (sync).

    Looks for ``web/`` at project root and runs
    ``npm ci && npm run build`` if Node.js is available.
    """
    website_dir = proj_path / _DIR_NAME
    if not website_dir.is_dir():
        log(f"  {_DIR_NAME}/ not found — skipping frontend build")
        return

    if not shutil.which("node"):
        log("  Node.js not found — skipping frontend build")
        return

    log(f"  Building {_DIR_NAME} (npm ci && npm run build)...")
    try:
        r = subprocess.run(
            ["npm", "ci", "--no-audit", "--no-fund"],
            cwd=website_dir,
            capture_output=True,
            timeout=180,
        )
        if r.returncode == 0:
            r = subprocess.run(
                ["npm", "run", "build"],
                cwd=website_dir,
                capture_output=True,
                timeout=120,
            )
            if r.returncode == 0:
                _propagate_dist(website_dir / "dist", proj_path, log)
            else:
                log("  Frontend build failed — dashboard may be stale")
        else:
            log("  Frontend npm ci failed — dashboard may be stale")
    except subprocess.TimeoutExpired:
        log("  Frontend build timed out — dashboard may be stale")


async def build_frontend_async(
    proj: str,
    push_progress: Optional[Callable[[str, str], None]] = None,
) -> None:
    """Build frontend assets (async)."""
    proj_path = Path(proj)
    website_dir = proj_path / _DIR_NAME

    def _warn(msg: str) -> None:
        if push_progress:
            push_progress("warning", msg)

    if not website_dir.is_dir():
        _warn(f"{_DIR_NAME}/ not found — skipping frontend build")
        return

    if not shutil.which("node"):
        _warn("Node.js not found — skipping frontend build")
        return

    # start_new_session on both: npm forks — a package's install script, and for `run
    # build` the bundler itself (vite/esbuild and its workers) — so `npm_x.kill()`
    # reached the npm wrapper and left the tree that was actually burning the deadline
    # running. Only a GROUP signal reaches it. See kill_timed_out.
    npm_i = await asyncio.create_subprocess_exec(
        "npm",
        "ci",
        "--no-audit",
        "--no-fund",
        cwd=str(website_dir),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        await asyncio.wait_for(npm_i.wait(), timeout=_NPM_CI_TIMEOUT)
    except asyncio.TimeoutError:
        await kill_timed_out(npm_i)
    if npm_i.returncode == 0:
        npm_build = await asyncio.create_subprocess_exec(
            "npm",
            "run",
            "build",
            cwd=str(website_dir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(npm_build.wait(), timeout=_NPM_BUILD_TIMEOUT)
        except asyncio.TimeoutError:
            await kill_timed_out(npm_build)
        if npm_build.returncode != 0:
            _warn("Frontend build failed -- dashboard may be stale")
        else:
            _propagate_dist(website_dir / "dist", proj_path)
    else:
        _warn("Frontend npm ci failed -- dashboard may be stale")
