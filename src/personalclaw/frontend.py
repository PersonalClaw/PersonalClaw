"""Shared helpers for building frontend assets."""

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_DIR_NAME = "web"


def _resolve_website_dist(pkg_dir: Path) -> Optional[Path]:
    """Locate a usable ``web/dist``.

    The package lives at ``<repo>/src/personalclaw``, so the repo root is two
    levels up. Probes repo root — ``<repo>/web/dist``.
    """
    repo_root = pkg_dir.parent.parent

    top_level_dist = repo_root / _DIR_NAME / "dist"
    if top_level_dist.is_dir() and (top_level_dist / "index.html").is_file():
        return top_level_dist.resolve()

    return None


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

    candidate = _resolve_website_dist(pkg_dir)
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
            cwd=website_dir, capture_output=True, timeout=180,
        )
        if r.returncode == 0:
            r = subprocess.run(
                ["npm", "run", "build"],
                cwd=website_dir, capture_output=True, timeout=120,
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

    npm_i = await asyncio.create_subprocess_exec(
        "npm", "ci", "--no-audit", "--no-fund",
        cwd=str(website_dir),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(npm_i.wait(), timeout=180)
    except asyncio.TimeoutError:
        try:
            npm_i.kill()
        except ProcessLookupError:
            pass
        await npm_i.wait()
    if npm_i.returncode == 0:
        npm_build = await asyncio.create_subprocess_exec(
            "npm", "run", "build",
            cwd=str(website_dir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(npm_build.wait(), timeout=120)
        except asyncio.TimeoutError:
            try:
                npm_build.kill()
            except ProcessLookupError:
                pass
            await npm_build.wait()
        if npm_build.returncode != 0:
            _warn("Frontend build failed -- dashboard may be stale")
        else:
            _propagate_dist(website_dir / "dist", proj_path)
    else:
        _warn("Frontend npm ci failed -- dashboard may be stale")
