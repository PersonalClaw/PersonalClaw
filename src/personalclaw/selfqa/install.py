"""Materializing the commit-watch script into ``~/.personalclaw/crons/``.

The script cannot be served from the package the way bundled templates are.
:func:`personalclaw.schedule_script.resolve_script_path` requires a script job's file to resolve
*under* the crons dir — that path fence is the whole reason a script job is safe to schedule — so
the file has to physically exist there.

That makes this an install step, and install steps into the user's home need a story for "did the
user edit it?" on upgrade. The answer here: the shipped copy is overwritten when its content
differs, and the state file beside it is never touched. This script is companion machinery rather
than a user template — the user's knobs are in `agent.self_qa`, not in this file — so silently
preserving a local edit would strand a fixed watcher on a broken version. A user who wants
different watch behaviour writes their own script under `crons/` and points a job at that.
"""

from __future__ import annotations

import json
import logging
from importlib import resources
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCRIPTS_PKG = "personalclaw.selfqa.scripts"

#: The installed filename, and the `file.py:func` spec a Schedule's `script` field uses.
COMMIT_WATCH_SCRIPT = "selfqa_commit_watch.py"
COMMIT_WATCH_SPEC = f"{COMMIT_WATCH_SCRIPT}:check"
COMMIT_WATCH_CONFIG = "selfqa_commit_watch.config.json"

#: The trigger's deterministic id. Deterministic because convergence needs it: a generated slug
#: would add another watcher on every restart instead of recognising its own, the bug
#: `reconcile_digest_cron` records.
WATCH_TRIGGER_ID = "system:selfqa-commit-watch"

#: How often the watcher looks. Five minutes is the "within one cron interval" the atom means: a
#: tick is one `git rev-parse`, so a tighter interval buys latency at no token cost, and a looser
#: one lets a push sit unchecked.
WATCH_INTERVAL_SECS = 300


def packaged_script_source() -> str:
    """The shipped script's source text, read from the package.

    `importlib.resources` rather than `__file__` arithmetic, so this resolves identically for an
    editable install, a wheel, and a source checkout.
    """
    return (resources.files(_SCRIPTS_PKG) / COMMIT_WATCH_SCRIPT).read_text(encoding="utf-8")


def install_commit_watch_script(crons_dir: Path | None = None) -> Path:
    """Write the commit-watch script into the crons dir and return its path.

    Idempotent: an identical existing file is left alone (so the mtime does not churn on every
    boot), a differing one is replaced. Creates the crons dir if it does not exist.
    """
    if crons_dir is None:
        from personalclaw.config.loader import config_dir

        crons_dir = config_dir() / "crons"

    crons_dir.mkdir(parents=True, exist_ok=True)
    target = crons_dir / COMMIT_WATCH_SCRIPT
    source = packaged_script_source()

    try:
        if target.read_text(encoding="utf-8") == source:
            return target
    except OSError:
        pass

    target.write_text(source, encoding="utf-8")
    logger.info("selfqa: installed commit-watch script at %s", target)
    return target


def write_watch_config(repo: str, crons_dir: Path | None = None) -> Path:
    """Write the watched-repo path where the sandboxed script can read it.

    This file exists because the two channels one would reach for first are both silently dead
    inside the sandbox — see the comment on `CONFIG_FILE` in the script itself. Written next to the
    script, which is the crons dir, so the script derives the path from `__file__` and imports
    nothing.
    """
    if crons_dir is None:
        from personalclaw.config.loader import config_dir

        crons_dir = config_dir() / "crons"
    crons_dir.mkdir(parents=True, exist_ok=True)
    target = crons_dir / COMMIT_WATCH_CONFIG
    target.write_text(json.dumps({"repo": repo}, indent=2) + "\n", encoding="utf-8")
    return target


def reconcile(store: Any, *, crons_dir: Path | None = None) -> None:
    """Make the commit watcher match `agent.self_qa`. Idempotent, best-effort.

    Converges rather than only creating, so turning the companion on in Settings takes effect
    without the user knowing a cron exists to be registered — and so editing `watched_repo`
    re-points the existing watcher instead of leaving it on the old path.

    **A disabled companion DISABLES its trigger; it never deletes it.** Deleting the last entry has
    been observed to stop the scheduler outright, and a disabled row is also the more honest
    surface: the user sees the watcher they configured, switched off, rather than an empty list
    that looks like their setting did not save.
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.triggers import screen as _screen
    from personalclaw.triggers.arm import arm as _arm
    from personalclaw.triggers.models import Trigger

    try:
        cfg = AppConfig.load().agent.self_qa
    except Exception:
        logger.debug("selfqa reconcile: could not read the config", exc_info=True)
        return

    repo = (cfg.watched_repo or "").strip()
    # `enabled` alone is not enough to run: a watcher with no repo would tick forever doing
    # nothing, which reads as a broken feature rather than an unfinished setup.
    active = bool(cfg.enabled and repo)

    try:
        install_commit_watch_script(crons_dir)
        write_watch_config(repo, crons_dir)
    except OSError:
        logger.warning("selfqa reconcile: could not install the commit-watch script", exc_info=True)
        return

    try:
        existing = store.get(WATCH_TRIGGER_ID)
    except Exception:
        logger.debug("selfqa reconcile: could not read the trigger store", exc_info=True)
        return

    if existing is None and not active:
        # Nothing registered and nothing wanted. Registering a disabled row here would put a
        # switched-off watcher in the user's trigger list before they ever asked for one.
        return

    try:
        # `store.get` returns a `LoadedTrigger` — the row PLUS whatever was wrong with reading it.
        # The entity to write is the `.trigger` inside; using the pair itself would set attributes
        # on the wrapper and upsert something with no `id`.
        trigger = (
            existing.trigger
            if existing is not None
            else Trigger(
                id=WATCH_TRIGGER_ID,
                name="Self-QA commit watch",
                kind="clock",
                created_by="system",
                # `delivery: none` — the watcher's OUTPUT is a workflow run, which reports itself. A
                # cron-result notification per tick would be a notification about looking.
                delivery="none",
            )
        )
        trigger.enabled = active
        trigger.spec = {"kind": "interval", "interval_secs": WATCH_INTERVAL_SECS}
        trigger.workflow = {
            "inline": {
                "provider": "run-script",
                "config": {"script": COMMIT_WATCH_SPEC, "timeout": 60},
            }
        }
        # The script starts a workflow run, so it is write-capable and the fence needs the frozen
        # grant (decision 7). A system-created trigger's opt-in is the code path that created it.
        trigger.capabilities = _screen.capabilities_for_action(trigger)
        if active:
            armed = _arm(trigger)
            if armed:
                trigger.next_fire_at = armed
        store.upsert(trigger)
        logger.info(
            "selfqa: commit watch %s (repo=%s)",
            "armed" if active else "disabled",
            repo or "<unset>",
        )
    except Exception:
        logger.warning("selfqa reconcile: registration failed", exc_info=True)
