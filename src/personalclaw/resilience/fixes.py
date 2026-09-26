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

from personalclaw.resilience.doctor import MEMORY_INDEX_FIX

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
    """(static/dist path, resolved web/dist target-or-None).

    Calls ``frontend.resolve_website_dist`` rather than re-deriving it. The hand-rolled
    copy this replaces carried the note *"mirrors frontend.py's resolution without
    calling it"*, and that duplication is how the doctor probe came to disagree with this
    very fix about whether an installed layout is broken: the fix refused with "no
    web/dist build found to link" while the probe reported a fault anyway. One
    derivation, one answer.
    """
    import personalclaw
    from personalclaw.frontend import resolve_website_dist

    pkg_dir = Path(personalclaw.__file__).resolve().parent
    return pkg_dir / "static" / "dist", resolve_website_dist(pkg_dir)


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


def _memory_index_preview() -> str:
    from personalclaw.config.loader import config_dir
    from personalclaw.resilience.doctor import memory_index_gaps

    ev = memory_index_gaps(config_dir())
    if not ev.get("db_present"):
        return "There is no memory.db yet, so there is nothing to index."
    if not ev.get("faiss_available"):
        return "faiss is not installed, so there is no index to rebuild."
    other = ev["other_model"]
    preview = (
        f"Would rebuild the search index from the memories embedded by the current model; it "
        f"holds {ev['faiss_ids']} of {ev['embedded_count']} now."
    )
    if other:
        preview = (
            f"Would first re-embed the {other} memor{'y' if other == 1 else 'ies'} a different "
            "embedding model wrote, with the model bound now (one embedding each), then rebuild "
            f"the search index; it holds {ev['faiss_ids']} of {ev['embedded_count']} now."
        )
    return preview


def rebuild_memory_index(*, reembed: bool = True) -> str:
    """Rebuild the faiss index semantic recall reads, from the vectors memory.db already holds.

    Acts on THE index recall uses — the store the gateway registered — so a Fix pressed on the
    Doctor page repairs the running gateway's recall, not a copy of it; with no live store in this
    process (the CLI) it opens the home's database, whose open rebuilds and saves the file.

    ``reembed`` (the Doctor's Fix, which the user confirms) first re-embeds the memories another
    model wrote, which a rebuild alone can never index; the maintenance job passes False, so an
    unattended pass never spends an embedding call and only rebuilds the derived index.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.vector_memory import VectorMemoryStore, faiss_available, recall_store

    if not faiss_available():
        raise RuntimeError("faiss is not installed, so there is no index to rebuild")
    db = config_dir() / "memory.db"
    store = recall_store(db)
    opened = store is None
    if store is None:
        store = VectorMemoryStore(db_path=db)
        store.init()
    try:
        redone = store.reembed_other_model() if reembed else None
        res = store.rebuild_faiss_index()
    finally:
        if opened:
            store.close()
    msg = (
        f"Rebuilt the memory search index: {res['indexed']} of {res['embedded']} embedded "
        "memories indexed."
    )
    if redone and redone["reembedded"]:
        msg = f"Re-embedded {redone['reembedded']} memories another embedding model wrote. " + msg
    if res["other_model"]:
        why = (
            "no embedding model answered, so they could not be re-embedded — bind one in "
            "Settings → Models, which re-embeds every memory"
            if reembed
            else "a rebuild cannot index them; the Doctor's Fix re-embeds them"
        )
        msg += f" {res['other_model']} still came from a different embedding model: {why}."
    return msg


def _rebuild_memory_index_fix() -> str:
    return rebuild_memory_index(reembed=True)


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
    register_fix(
        Fix(
            id=MEMORY_INDEX_FIX,
            title="Rebuild the memory search index",
            impact="Rebuilds the faiss index semantic recall reads from the vectors already "
            "stored in memory.db, at the width the current embedding model produces, and saves "
            "it. Memories a different embedding model wrote are re-embedded first with the model "
            "bound now. What your memories say is not changed.",
            dry_preview=_memory_index_preview,
            apply=_rebuild_memory_index_fix,
        )
    )


_register_builtin_fixes()
