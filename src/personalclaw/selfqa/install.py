"""Converging the Self-QA commit watch onto the vcs trigger (SELF-VERIFICATION SV-11).

The Wave-2 companion shipped an interim seam — a cron script materialized into
``~/.personalclaw/crons/`` on an interval trigger — because no vcs trigger existed yet and a
script job may only load from that fenced directory. AUTOMATION-SUBSTRATE's ``vcs`` preset
now exists (:func:`personalclaw.triggers.file_watch.vcs_patterns`), so this module does what
the plan's §3.1 promised from the start: *"When AUTO-R12's vcs preset lands, the cron script
retires and the same template binds to the real trigger — the template is the durable half,
the trigger is a swap."*

:func:`reconcile` therefore converges a ``file``-kind trigger (the vcs preset over
``agent.self_qa.watched_repo``) whose action is the ``selfqa-commit-watch`` provider — the
retired script's delta logic, moved in-process (:mod:`personalclaw.selfqa.watch`). It also
REMOVES any interim artifacts a Wave-2 home still carries (the installed script, its config,
its state file beside them): pre-1.0 clean break, per CONTRIBUTING's breaking-changes
posture, and leaving a dead script in the crons dir would invite a user to schedule it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The trigger's deterministic id. Deterministic because convergence needs it: a generated
#: slug would add another watcher on every restart instead of recognising its own — and it
#: is unchanged from the interim seam, so the SAME row swaps kind in place on upgrade.
WATCH_TRIGGER_ID = "system:selfqa-commit-watch"

#: The interim seam's on-disk artifacts, removed on reconcile when found. Names are kept
#: here (not imported from a scripts package — that package is gone) so the cleanup can
#: recognise what an older version installed.
_RETIRED_CRON_FILES = (
    "selfqa_commit_watch.py",
    "selfqa_commit_watch.config.json",
    "selfqa_commit_watch.state.json",
)


def remove_retired_script(crons_dir: Path | None = None) -> list[str]:
    """Delete the interim commit-watch script artifacts, returning what was removed.

    Best-effort and idempotent: a fresh home removes nothing, an upgraded Wave-2 home
    removes up to three files. The state is NOT migrated — the new watcher's first fire
    records HEAD and stays quiet (first-sight rule), which is the same behaviour a fresh
    enable has always had.
    """
    if crons_dir is None:
        from personalclaw.config.loader import config_dir

        crons_dir = config_dir() / "crons"
    removed: list[str] = []
    for name in _RETIRED_CRON_FILES:
        target = crons_dir / name
        try:
            if target.is_file():
                target.unlink()
                removed.append(name)
        except OSError:
            logger.debug("selfqa reconcile: could not remove retired %s", name, exc_info=True)
    if removed:
        logger.info("selfqa: removed retired commit-watch artifacts: %s", ", ".join(removed))
    return removed


def reconcile(store: Any, *, crons_dir: Path | None = None) -> None:
    """Make the commit watcher match `agent.self_qa`. Idempotent, best-effort.

    Converges rather than only creating, so turning the companion on in Settings takes
    effect without the user knowing a trigger exists to be registered — and so editing
    `watched_repo` re-points the existing watcher instead of leaving it on the old path.

    **A disabled companion DISABLES its trigger; it never deletes it.** Deleting the last
    entry has been observed to stop the scheduler outright, and a disabled row is also the
    more honest surface: the user sees the watcher they configured, switched off, rather
    than an empty list that looks like their setting did not save.
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.triggers import screen as _screen
    from personalclaw.triggers.file_watch import vcs_patterns
    from personalclaw.triggers.models import Trigger

    try:
        cfg = AppConfig.load().agent.self_qa
    except Exception:
        logger.debug("selfqa reconcile: could not read the config", exc_info=True)
        return

    repo = (cfg.watched_repo or "").strip()
    # `enabled` alone is not enough to run: a watcher with no repo would watch nothing,
    # which reads as a broken feature rather than an unfinished setup.
    active = bool(cfg.enabled and repo)

    remove_retired_script(crons_dir)

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
        # `store.get` returns a `LoadedTrigger` — the row PLUS whatever was wrong with reading
        # it. The entity to write is the `.trigger` inside; using the pair itself would set
        # attributes on the wrapper and upsert something with no `id`.
        trigger = (
            existing.trigger
            if existing is not None
            else Trigger(
                id=WATCH_TRIGGER_ID,
                name="Self-QA commit watch",
                kind="file",
                created_by="system",
                # `delivery: none` — the watcher's OUTPUT is a workflow run, which reports
                # itself. A fire notification per push would be a notification about looking.
                delivery="none",
            )
        )
        trigger.enabled = active
        # An upgraded Wave-2 row arrives as `clock`; the swap happens HERE, on the same id,
        # so the user's trigger list shows one watcher whose kind changed — not two.
        trigger.kind = "file"
        # Content dedup: a ref rewritten to the same bytes (a no-op force-push) is not a
        # change worth firing on. The preset's globs cover refs/heads/* AND .git/HEAD, so a
        # branch switch re-seeds honestly instead of firing on the next commit with a stale
        # idea of the branch.
        trigger.spec = {"paths": vcs_patterns(repo or "."), "dedup": "content"}
        trigger.workflow = {
            "inline": {
                "provider": "selfqa-commit-watch",
                "config": {"repo": repo},
            }
        }
        # The provider starts a workflow run, so it is write-capable and the fence needs the
        # frozen grant (decision 7). A system-created trigger's opt-in is the code path that
        # created it.
        trigger.capabilities = _screen.capabilities_for_action(trigger)
        store.upsert(trigger)
        logger.info(
            "selfqa: commit watch %s (repo=%s, vcs preset)",
            "armed" if active else "disabled",
            repo or "<unset>",
        )
    except Exception:
        logger.warning("selfqa reconcile: registration failed", exc_info=True)
