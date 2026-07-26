"""Confirm-gated auto-fixes for Doctor findings (PLATFORM-RESILIENCE §2).

Every fix is a ``Fix{id, title, impact, dry_preview(), apply()}`` paired with a probe
via its ``fix_id``. **Nothing auto-applies** — the Doctor tab renders the fix with its
impact description and a two-step confirm runs it; every application is SEL-audited.
Fixes touch harness mechanics ONLY (symlinks, caches, orphaned locks/PIDs, rollback
leftovers) — never user content (memory entries, knowledge items, tasks); anything
content-adjacent is flagged, never auto-deleted.

``dry_preview()`` is read-only and returns a human string describing what ``apply()``
would do. ``apply()`` performs the repair and returns a result string. Both are
exception-safe at the registry boundary (:func:`apply_fix`).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fix:
    """A confirm-gated repair for a Doctor finding.

    ``id`` is the stable ``fix_id`` a probe attaches. ``dry_preview`` is read-only;
    ``apply`` mutates (harness mechanics only) and returns a result string.
    """

    id: str
    title: str
    impact: str
    dry_preview: Callable[[], str]
    apply: Callable[[], str]


_FIXES: dict[str, Fix] = {}


def register_fix(fix: Fix) -> None:
    _FIXES[fix.id] = fix


def all_fixes() -> list[Fix]:
    return list(_FIXES.values())


def get_fix(fix_id: str) -> Optional[Fix]:
    return _FIXES.get(fix_id)


def apply_fix(fix_id: str, *, session_key: str = "dashboard") -> dict:
    """Run a registered fix's ``apply()`` under a SEL audit. Returns
    ``{ok, fix_id, result|error}``. Exception-safe: a failing fix reports ``ok:False``,
    never raises to the handler."""
    fix = _FIXES.get(fix_id)
    if fix is None:
        return {"ok": False, "fix_id": fix_id, "error": "unknown fix"}
    from personalclaw.sel import sel

    try:
        result = fix.apply()
        ok = True
        err = ""
    except Exception as exc:  # a fix's failure is a reported outcome, not a 500
        result = ""
        ok = False
        err = str(exc)
        logger.warning("fix %s failed", fix_id, exc_info=True)
    try:
        sel().log_tool_invocation(
            session_key=session_key,
            agent="personalclaw",
            source="dashboard",
            tool_name=f"doctor_fix:{fix_id}",
            tool_kind="maintenance",
            outcome="ok" if ok else "error",
            error=err,
            metadata={"fix_id": fix_id, "result": result[:200]},
        )
    except Exception:
        logger.debug("SEL audit for fix %s failed", fix_id, exc_info=True)
    return {"ok": ok, "fix_id": fix_id, **({"result": result} if ok else {"error": err})}


# ── Fix implementations (harness mechanics only) ─────────────────────────────


def _dist_paths() -> tuple[Path, Optional[Path]]:
    """(static/dist path, resolved web/dist target-or-None) — mirrors frontend.py's
    resolution without calling it (that function early-returns on a valid copy)."""
    import personalclaw

    pkg_dir = Path(personalclaw.__file__).resolve().parent
    tree_dist = pkg_dir / "static" / "dist"
    repo_root = pkg_dir.parent.parent
    built = repo_root / "web" / "dist"
    target = built.resolve() if (built / "index.html").is_file() else None
    return tree_dist, target


def _symlink_repair_preview() -> str:
    dist, target = _dist_paths()
    if dist.is_symlink():
        return "static/dist is already a symlink — nothing to repair."
    if not dist.exists():
        return "static/dist is missing." + (
            f" Would create a symlink → {target}."
            if target
            else " No web/dist build found to link."
        )
    if target is None:
        return "static/dist is a directory copy, but no web/dist build was found to link to."
    return (
        f"Would back up the shadowing copy to static/dist.shadow, then symlink "
        f"static/dist → {target} (closes the stale-SPA bug-class)."
    )


def _symlink_repair_apply() -> str:
    dist, target = _dist_paths()
    if dist.is_symlink():
        return "Already a symlink — no change."
    if target is None:
        raise RuntimeError("no web/dist build found to link (build the frontend first)")
    if dist.exists():
        # Back up the shadow copy rather than deleting it (never destroy content blindly).
        shadow = dist.parent / "dist.shadow"
        if shadow.exists():
            shutil.rmtree(shadow, ignore_errors=True)
        shutil.move(str(dist), str(shadow))
    dist.parent.mkdir(parents=True, exist_ok=True)
    dist.symlink_to(target)
    return f"Repaired: static/dist → {target} (shadow copy backed up)."


def _dead_locks() -> list[Path]:
    from personalclaw.config.loader import config_dir

    locks_dir = config_dir() / "locks"
    if not locks_dir.exists():
        return []
    out = []
    for p in locks_dir.glob("*.lock"):
        try:
            import time as _t

            if (_t.time() - p.stat().st_mtime) > 86400:
                out.append(p)
        except OSError:
            continue
    return out


def _rollback_dirs() -> list[Path]:
    from personalclaw.apps.manager import apps_dir

    ad = apps_dir()
    if not ad.exists():
        return []
    return [
        c
        for c in ad.iterdir()
        if c.is_dir() and c.name.startswith(".") and c.name.endswith(".rollback")
    ]


def _orphan_prune_preview() -> str:
    locks = _dead_locks()
    rollbacks = _rollback_dirs()
    parts = []
    if locks:
        parts.append(f"{len(locks)} stale lock file(s) (>24h old)")
    if rollbacks:
        parts.append(f"{len(rollbacks)} interrupted-update rollback dir(s)")
    if not parts:
        return "No orphaned locks or rollback leftovers found."
    return "Would remove: " + "; ".join(parts) + " (harness mechanics only — no user content)."


def _orphan_prune_apply() -> str:
    removed = 0
    for p in _dead_locks():
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    # Rollback leftovers are recovered/dropped by the apps reconciler (it decides
    # restore-vs-drop safely); invoke it rather than blindly rmtree'ing.
    recovered: list[str] = []
    try:
        from personalclaw.apps.app_manager import recover_interrupted_updates

        recovered = recover_interrupted_updates()
    except Exception:
        logger.debug("recover_interrupted_updates failed during orphan prune", exc_info=True)
    return f"Removed {removed} stale lock(s); reconciled {len(recovered)} rollback leftover(s)."


def _active_models_prune_preview() -> str:
    # load_active_models() prunes removed-provider refs on read; persisting requires a
    # save. Show the delta between raw on-disk and pruned.
    try:
        import json

        from personalclaw.config.loader import config_dir
        from personalclaw.providers.use_cases import load_active_models

        raw_path = config_dir() / "active_models.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else {}
        raw_refs = sum(len(v) for v in raw.values() if isinstance(v, list))
        pruned = load_active_models()
        pruned_refs = sum(len(v) for v in pruned.values())
        stale = raw_refs - pruned_refs
        if stale <= 0:
            return "No active-model bindings reference removed providers."
        return f"Would drop {stale} model binding(s) that reference removed providers."
    except Exception:
        return "Could not evaluate active-model bindings."


def _active_models_prune_apply() -> str:
    from personalclaw.providers.use_cases import load_active_models, save_active_models

    pruned = load_active_models()  # already drops removed-provider refs
    save_active_models(pruned)
    return "Persisted the pruned active-model bindings (removed-provider refs dropped)."


def _register_builtin_fixes() -> None:
    register_fix(
        Fix(
            id="serving-fs.symlink-repair",
            title="Repair the static/dist symlink",
            impact="Replaces a directory COPY shadowing the runtime symlink with a symlink "
            "to web/dist (backing up the copy). Closes the stale-SPA bug-class.",
            dry_preview=_symlink_repair_preview,
            apply=_symlink_repair_apply,
        )
    )
    register_fix(
        Fix(
            id="serving-fs.orphan-prune",
            title="Prune orphaned locks + rollback leftovers",
            impact="Removes stale lock files (>24h) and reconciles interrupted-update "
            "rollback dirs. Harness mechanics only — never touches user content.",
            dry_preview=_orphan_prune_preview,
            apply=_orphan_prune_apply,
        )
    )
    register_fix(
        Fix(
            id="model-providers.prune-bindings",
            title="Drop model bindings for removed providers",
            impact="Persists the removed-provider pruning that load_active_models already "
            "does on read, so stale bindings stop being silently ignored.",
            dry_preview=_active_models_prune_preview,
            apply=_active_models_prune_apply,
        )
    )


_register_builtin_fixes()
