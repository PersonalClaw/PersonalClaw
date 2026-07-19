"""Scratch-workspace lifecycle for loops (auto-campaign-scratch-workspace).

A loop owns a scratch dir (``config_dir()/loop/<id>/`` — its findings, briefs, and
document deliverable). By default that dir PERSISTS after the loop finishes (never
auto-delete a user's work). When a loop is created with ``auto_teardown_on_complete``,
the dir is treated as disposable scratch and removed once the loop reaches a terminal
state — but ONLY after the completion path has already **graduated** the deliverable
to the permanent Artifacts library (``watchdog._register_deliverable_artifact``). So
the report survives even for a torn-down scratch loop; only the raw scratch is reclaimed.

An externally-bound ``workspace_dir`` (the user's own codebase) is NEVER auto-torn-down
— teardown is scoped strictly to the loop's own dir.
"""

from __future__ import annotations

import logging
import shutil

logger = logging.getLogger(__name__)


def should_teardown(loop) -> bool:
    """Whether this loop's scratch dir should be auto-reclaimed on completion."""
    return bool(getattr(loop, "auto_teardown_on_complete", False))


def teardown_scratch(loop_id: str) -> bool:
    """Remove the loop's OWN scratch dir (config_dir()/loop/<id>/). Returns True if a
    dir was removed. Never touches an external workspace_dir. Best-effort.

    Called only after the deliverable has been registered as a permanent artifact, so
    the human-facing output is preserved even though the raw scratch is reclaimed."""
    from personalclaw.loop.store import safe_loop_dir

    d = safe_loop_dir(loop_id)
    if d is None or not d.exists():
        return False
    try:
        shutil.rmtree(d, ignore_errors=True)
        logger.info("Reclaimed scratch workspace for loop %s (auto-teardown)", loop_id)
        return True
    except OSError:
        logger.debug("scratch teardown failed for %s", loop_id, exc_info=True)
        return False
