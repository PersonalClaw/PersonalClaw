"""Confirm-gated auto-fixes for Doctor findings.

Every fix is a ``Fix{id, title, impact, dry_preview(), apply()}`` paired with a probe
via its ``fix_id``. **Nothing auto-applies** — the Doctor tab renders the fix with its
impact description and a two-step confirm runs it; every application is SEL-audited.
Fixes touch harness mechanics ONLY (symlinks, caches, orphaned locks/PIDs, rollback
leftovers, bindings to models that are gone, PersonalClaw's own server entry in the agent
config) — never user content (memory entries, knowledge items, tasks); anything
content-adjacent is flagged, never auto-deleted. No fix adds a tool to what the agent is
offered or runs without asking: those lists are the owner's.

``dry_preview()`` is read-only and returns a human string describing what ``apply()``
would do. ``apply()`` performs the repair and returns a result string. Both are
exception-safe at the registry boundary (:func:`apply_fix`).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from personalclaw.resilience.core_server import CORE_SERVER_FIX
from personalclaw.resilience.doctor import MEMORY_INDEX_FIX, PRUNE_BINDINGS_FIX

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


def _run_async(coro_fn: Callable[[], Any]) -> Any:
    """Run an async check from a fix, which is sync by contract. Every caller runs a fix off the
    gateway loop, on a worker thread with no loop of its own (the Doctor routes and the
    Investigate snapshot alike), so the check gets a loop of its own there."""
    import asyncio

    return asyncio.run(coro_fn())


def _bindings_that_cannot_run() -> dict[str, list[tuple[str, str]]]:
    """``{use case: [(ref, why), …]}`` — every bound model that cannot run, with the reason.

    Two kinds, both read fresh: a model whose provider was removed (the stored chain still names
    it; every read already skips it), and one its local provider answers for and no longer lists
    (deleted or renamed: :func:`doctor.phantom_bindings`). A provider that did not answer is not
    asked about its models, so an outage never reads as models that are gone.
    """
    import json

    from personalclaw.providers.use_cases import (
        active_models_path,
        load_active_models,
        split_ref,
    )
    from personalclaw.resilience.doctor import phantom_bindings

    path = active_models_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        raw = {}
    kept = load_active_models()
    phantom = set(_run_async(phantom_bindings))
    out: dict[str, list[tuple[str, str]]] = {}
    for use_case, refs in (raw if isinstance(raw, dict) else {}).items():
        chain = [refs] if isinstance(refs, str) else refs if isinstance(refs, list) else []
        for ref in chain:
            ref = str(ref)
            parsed = split_ref(ref)
            provider = parsed[0] if parsed else ""
            if ref not in kept.get(use_case, []):
                out.setdefault(use_case, []).append((ref, f"{provider} was removed"))
            elif ref in phantom:
                out.setdefault(use_case, []).append((ref, f"{provider} no longer lists it"))
    return out


def _use_case_label(use_case: str) -> str:
    """The use case as Settings → Models names it (``code_tools`` → "Code & tools")."""
    from personalclaw.providers.use_cases import USE_CASE_NAMES

    return USE_CASE_NAMES.get(use_case, use_case)


def _left_empty(use_case: str, dropped: list[str]) -> str:
    """What a use case falls back to once every model it names is unbound, or ``""``."""
    from personalclaw.providers.use_cases import CHAT_SUBCATEGORIES, load_active_models

    remaining = [r for r in load_active_models().get(use_case, []) if r not in dropped]
    if remaining:
        return ""
    if use_case in CHAT_SUBCATEGORIES:
        return f"{_use_case_label(use_case)} then uses your Chat models"
    return f"{_use_case_label(use_case)} then has no model until you choose one"


def _prune_bindings_preview() -> str:
    gone = _bindings_that_cannot_run()
    if not gone:
        return "No binding names a model that is gone."
    parts = []
    for use_case, entries in gone.items():
        models = ", ".join(f"{ref.split(':', 1)[-1]} ({why})" for ref, why in entries)
        after = _left_empty(use_case, [ref for ref, _ in entries])
        parts.append(
            f"from {_use_case_label(use_case)}: {models}" + (f" — {after}" if after else "")
        )
    return "Would unbind " + "; ".join(parts) + "."


def _prune_bindings_apply() -> str:
    from personalclaw.providers.use_cases import load_active_models, save_active_models

    gone = _bindings_that_cannot_run()
    if not gone:
        return "No binding named a model that is gone; nothing changed."
    active = load_active_models()  # removed providers' refs are already left out
    for use_case, entries in gone.items():
        drop = {ref for ref, _ in entries}
        active[use_case] = [r for r in active.get(use_case, []) if r not in drop]
    save_active_models(active)
    return (
        "Unbound "
        + "; ".join(
            f"{', '.join(ref.split(':', 1)[-1] for ref, _ in entries)} from "
            f"{_use_case_label(use_case)}"
            for use_case, entries in gone.items()
        )
        + "."
    )


def _memory_index_preview() -> str:
    from personalclaw.config.loader import config_dir
    from personalclaw.resilience.doctor import memory_index_gaps

    ev = memory_index_gaps(config_dir())
    if not ev.get("db_present"):
        return "There is no memory.db yet, so there is nothing to index."
    waiting = ev["other_model"] + ev["unembedded"]
    if not ev.get("faiss_available"):
        if waiting:
            return (
                f"Would embed the {waiting} memor{'y' if waiting == 1 else 'ies'} the model bound "
                "now has not embedded, with that model (one embedding each). faiss is not "
                "installed, so there is no index to rebuild."
            )
        return "faiss is not installed, so there is no index to rebuild."
    # The index holds episodes, so it is counted against the embedded episodes, not against every
    # embedded memory: facts and lessons are compared by their own vectors (`memory_index_gaps`).
    holds = (
        f"it holds {ev['indexed_episodes']} of the {ev['embedded_episodes']} embedded episodes now"
    )
    preview = (
        f"Would rebuild the search index from the episodes embedded by the current model; {holds}."
    )
    if waiting:
        preview = (
            f"Would first embed the {waiting} memor{'y' if waiting == 1 else 'ies'} the model "
            "bound now has not embedded, with that model (one embedding each), then rebuild "
            f"the search index; {holds}."
        )
    return preview


def rebuild_memory_index(*, reembed: bool = True) -> str:
    """Rebuild the faiss index semantic recall reads, from the vectors memory.db already holds.

    Acts on THE index recall uses — the store the gateway registered — so a Fix pressed on the
    Doctor page repairs the running gateway's recall, not a copy of it; with no live store in this
    process (the CLI) it opens the home's database, whose open rebuilds and saves the file.

    ``reembed`` (the Doctor's Fix, which the user confirms) first embeds, with the model bound
    now, the memories it has not embedded — another model's, at another width, or none at all —
    which a rebuild alone can never index; the maintenance job passes False, so an unattended
    pass never spends an embedding call and only rebuilds the derived index. Without faiss there is
    no index: semantic recall searches the stored vectors directly, so the embedding is the whole
    Fix, and the job has nothing to do.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.vector_memory import VectorMemoryStore, faiss_available, recall_store

    if not reembed and not faiss_available():
        raise RuntimeError("faiss is not installed, so there is no index to rebuild")
    db = config_dir() / "memory.db"
    store = recall_store(db)
    opened = store is None
    if store is None:
        store = VectorMemoryStore(db_path=db)
        store.init()
    try:
        redone = store.reembed_stale() if reembed else None
        res = store.rebuild_faiss_index() if faiss_available() else None
    finally:
        if opened:
            store.close()
    if res is None:
        embedded = redone["reembedded"] if redone else 0
        return (
            f"Embedded {embedded} memor{'y' if embedded == 1 else 'ies'} with the model bound "
            "now. faiss is not installed, so semantic recall searches the stored vectors directly."
        )
    # A rebuild indexes every episode the bound model embedded at its width, so what it reports is
    # how many: the index holds episodes, and the facts and lessons in the Memory page's Embedded
    # count are compared by their own vectors.
    indexed = res["indexed"]
    msg = (
        f"Rebuilt the memory search index: {indexed} embedded "
        f"episode{'' if indexed == 1 else 's'} indexed."
    )
    if redone and redone["reembedded"]:
        n = redone["reembedded"]
        msg = f"Embedded {n} memor{'y' if n == 1 else 'ies'} with the model bound now. " + msg
    if res["other_model"]:
        why = (
            "no embedding model answered, so they could not be re-embedded — bind one in "
            "Settings → Models, which re-embeds every memory"
            if reembed
            else "a rebuild cannot index them; the Doctor's Fix re-embeds them"
        )
        k = res["other_model"]
        msg += (
            f" {k} more episode{'' if k == 1 else 's'} still came from a different embedding "
            f"model: {why}."
        )
    return msg


def _rebuild_memory_index_fix() -> str:
    return rebuild_memory_index(reembed=True)


#: What the server-entry Fix leaves alone, said in its preview and its result alike.
_LISTS_KEPT = (
    "The file's tools and allowedTools lists stay as they are: nothing is added to the tools the "
    "agent is offered, or to those it runs without asking."
)


def _core_server_starts(command: str) -> str:
    from personalclaw.resilience.core_server import core_server_args

    return " ".join([command, *core_server_args()])


def _restore_core_server_preview() -> str:
    from personalclaw.resilience.core_server import (
        AGENT_CONFIG_NAME,
        agent_config_path,
        core_server_command,
        core_server_detail,
        read_core_server,
    )

    reading = read_core_server(agent_config_path())
    if not reading.needs_setting_up:
        return f"Nothing would change: {core_server_detail(reading)}."
    command = core_server_command()
    if not command:
        return (
            "This install's personalclaw command was not found, so there is no command to give "
            "the entry; nothing would change."
        )
    starts = _core_server_starts(command)
    if reading.malformed:
        change = (
            f"Would replace PersonalClaw's server entry in {AGENT_CONFIG_NAME}, which is not a "
            f"server definition, with one that starts {starts}."
        )
    elif reading.entry is None:
        change = f"Would add PersonalClaw's server to {AGENT_CONFIG_NAME}, started as {starts}."
    elif reading.command == command:
        change = (
            f"Would set PersonalClaw's server in {AGENT_CONFIG_NAME} to start {starts}, with its "
            "own arguments, and keep the rest of its entry."
        )
    else:
        change = (
            f"Would set PersonalClaw's server in {AGENT_CONFIG_NAME} to start {starts}, in place "
            f"of {reading.command or 'no command'}, and keep the rest of its entry."
        )
    return f"{change} {_LISTS_KEPT}"


def _restore_core_server_apply() -> str:
    """Write PersonalClaw's own server entry: this install's command and the server's arguments,
    over whatever else the entry holds. Nothing else in the file changes, and a file that is not
    there or cannot be read is refused rather than written."""
    import copy

    from personalclaw.config.secret_refs import write_mcp_document
    from personalclaw.resilience.core_server import (
        AGENT_CONFIG_NAME,
        agent_config_path,
        core_server_args,
        core_server_command,
        core_server_detail,
        core_server_name,
        read_core_server,
    )

    path = agent_config_path()
    reading = read_core_server(path)
    if reading.set_up:
        return f"Nothing changed: {core_server_detail(reading)}."
    if not reading.needs_setting_up:
        raise RuntimeError(f"{core_server_detail(reading)}; nothing was written")
    command = core_server_command()
    if not command:
        raise RuntimeError(
            "this install's personalclaw command was not found, so the entry was left as it is"
        )
    document = copy.deepcopy(reading.document)
    servers = document.setdefault("mcpServers", {})
    servers[core_server_name()] = {
        **(reading.entry or {}),
        "command": command,
        "args": core_server_args(),
    }
    write_mcp_document(path, document)
    return (
        f"PersonalClaw's server in {AGENT_CONFIG_NAME} starts {_core_server_starts(command)} now. "
        f"{_LISTS_KEPT}"
    )


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
            id=PRUNE_BINDINGS_FIX,
            title="Prune bindings to models that are gone",
            impact="Removes from each use case in Settings → Models every model that cannot "
            "run: one its local provider no longer lists (deleted or renamed), and one whose "
            "provider was removed. The other models keep their order. To use a removed model "
            "again, download it and choose it there.",
            dry_preview=_prune_bindings_preview,
            apply=_prune_bindings_apply,
        )
    )
    register_fix(
        Fix(
            id=MEMORY_INDEX_FIX,
            title="Rebuild the memory search index",
            impact="Rebuilds the faiss index semantic recall reads from the vectors already "
            "stored in memory.db, at the width the current embedding model produces, and saves "
            "it. Memories the model bound now has not embedded (a different model's, or none at "
            "all) are embedded first with it. What your memories say is not changed.",
            dry_preview=_memory_index_preview,
            apply=_rebuild_memory_index_fix,
        )
    )
    register_fix(
        Fix(
            id=CORE_SERVER_FIX,
            title="Set up PersonalClaw's server in the agent config again",
            impact="Writes the entry for PersonalClaw's own server in the agent runtime config, "
            "agents/personalclaw.json, with this install's personalclaw command, as every gateway "
            "start does, and keeps the rest of the file as it is. Nothing is added to its tools "
            "list or to allowedTools, the tools the agent runs without asking: both lists are "
            "yours.",
            dry_preview=_restore_core_server_preview,
            apply=_restore_core_server_apply,
        )
    )


_register_builtin_fixes()
