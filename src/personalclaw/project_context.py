"""Project living context — the overview, the wayfinder ledgers, and the injected block (S46).

`projects.py` resolves which project work binds to. This module owns what a project KNOWS: the
living overview the engine revises as runs complete, the three wayfinder ledgers, and the context
block every session inside the project sees.

Two distinctions the plan draws normatively, held here as separate files and separate functions:

* **Overview is current state; the ledger is history.** `context/overview.md` is revised in place —
  what the project now knows. The decisions ledger is append-only — what was settled and when.
  Collapsing them would mean either losing the history or making the current state something a
  reader has to reconstruct from a log.
* **Brief is what/why; instructions are how.** The brief is user-authored and stable; the
  instructions are operating procedure. An agent that cannot tell the goal from the procedure will
  follow the procedure past the point where the goal is met.

Everything is best-effort by construction. This feeds a **context builder**, and the never-break-a-
turn contract applies: a corrupt overview file must cost the block, never the user's message.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.workflows.containers import LEDGERS, ledger_entry, project_block

logger = logging.getLogger(__name__)

OVERVIEW_FILE = "overview.md"

#: One file per ledger, under the project's own `context/`. Separate files rather than sections of
#: one, because they are appended by different events at different times — one file would make every
#: append a read-modify-write of all three, and a torn write would lose two ledgers to fix one.
LEDGER_FILES = {
    "decisions": "decisions.md",
    "fog": "not-yet-specified.md",
    "out_of_scope": "out-of-scope.md",
}

#: The overview is revised in place, so it has no natural bound. A cap keeps it a summary
#: rather than a log — an overview that grew without limit would become the history it is
#: defined against, and would eventually crowd out the rest of the prompt.
MAX_OVERVIEW_CHARS = 8000

#: Ledger lines are one-liners by contract (the decisions ledger is "an index, not a store"). A cap
#: per line keeps a run from pasting its whole output into the index.
MAX_LEDGER_LINE = 400


def inlined_context_files(project_id: str) -> frozenset[str]:
    """Filenames whose content the project block ACTUALLY inlined for this project.

    Exists so the chat preamble's context-dir listing can exclude them. Measured on a live project:
    the overview and all three ledgers appeared both as inlined text and in a "read any for
    continuity" listing, inviting four tool calls to re-read what the agent had already been given.
    A listing that recommends redundant work is one an agent learns to ignore wholesale — taking the
    genuinely unread files with it.

    CONTENT-based, not name-based. A blanket exclusion on the reserved filenames looked equivalent
    and was not: a hand-authored `decisions.md` that is not in ledger line format inlines NOTHING,
    and excluding it by name would hide a file the agent has never seen. Hiding an unread file is
    the worse failure of the two — a redundant pointer wastes a tool call, a hidden file loses the
    context entirely.
    """
    inlined: set[str] = set()
    if read_overview(project_id):
        inlined.add(OVERVIEW_FILE)
    for kind, filename in LEDGER_FILES.items():
        if read_ledger(project_id, kind):
            inlined.add(filename)
    return frozenset(inlined)


def _context_dir(project_id: str) -> Path | None:
    """The project's context dir, or None when the project does not resolve.

    None rather than a created directory: `resolve_project_id` auto-creates projects, and a READ
    path that materialized one would mean opening a project page could invent a project.
    """
    if not project_id:
        return None
    try:
        from personalclaw.tasks.hierarchy import HierarchyStore

        store = HierarchyStore()
        if store.get_project(project_id) is None:
            return None
        return store.context_dir(project_id)
    except Exception:
        logger.debug("context dir lookup failed for %s", project_id, exc_info=True)
        return None


def read_overview(project_id: str) -> str:
    """The living overview, or "" when there is none. Never raises."""
    context = _context_dir(project_id)
    if context is None:
        return ""
    try:
        return (context / OVERVIEW_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    except Exception:
        logger.debug("overview read failed for %s", project_id, exc_info=True)
        return ""


def write_overview(project_id: str, text: str) -> bool:
    """Replace the overview. In place, atomically.

    In place is the point — this is current state, not history. The atomic write matters more here
    than for a log: a torn overview is a truncated description of the project that reads as
    complete,
    where a torn append is a visibly missing line.
    """
    context = _context_dir(project_id)
    if context is None:
        return False
    body = (text or "").strip()[:MAX_OVERVIEW_CHARS]
    try:
        atomic_write(context / OVERVIEW_FILE, body)
        return True
    except Exception:
        logger.debug("overview write failed for %s", project_id, exc_info=True)
        return False


def append_ledger(
    project_id: str, kind: str, text: str, *, link: str = "", reason: str = ""
) -> bool:
    """Append one line to a wayfinder ledger. Append-only, by construction.

    There is no update or delete. A ledger whose entries could be edited would stop being evidence
    of what was decided when — and the decisions ledger's entire job is to answer "why is it like
    this" months later, which a mutable log cannot do.
    """
    context = _context_dir(project_id)
    if context is None:
        return False
    filename = LEDGER_FILES.get(kind)
    if filename is None:
        return False
    try:
        entry = ledger_entry(kind, (text or "")[:MAX_LEDGER_LINE], link=link, reason=reason)
    except ValueError:
        return False
    if not entry["text"]:
        return False
    line = f"- {entry['text']}"
    if entry.get("reason"):
        line += f" — _{entry['reason']}_"
    if entry.get("link"):
        line += f" ([run]({entry['link']}))"
    path = context / filename
    try:
        header = (
            "" if path.exists() else f"# {kind.replace('_', ' ').title()}\n\n{LEDGERS[kind]}\n\n"
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(header + line + "\n")
        return True
    except Exception:
        logger.debug("ledger append failed for %s/%s", project_id, kind, exc_info=True)
        return False


def read_ledger(project_id: str, kind: str) -> list[str]:
    """A ledger's lines, newest last (append order). "" -safe and never raises."""
    context = _context_dir(project_id)
    filename = LEDGER_FILES.get(kind)
    if context is None or filename is None:
        return []
    try:
        text = (context / filename).read_text(encoding="utf-8")
    except OSError:
        return []
    except Exception:
        logger.debug("ledger read failed for %s/%s", project_id, kind, exc_info=True)
        return []
    return [line[2:].strip() for line in text.splitlines() if line.startswith("- ")]


def context_block(project_id: str) -> str:
    """The project block for any session inside the project.

    Used by BOTH stage sessions and ordinary chat sessions whose `project_id` matches — one block,
    one composer. Two composers would drift, and an agent seeing a different project description in
    chat than in a run is an agent whose answers cannot be reconciled.

    Swallows everything and returns "" on any failure, per the never-break-a-turn contract at the
    `context.build_message` seam this feeds.
    """
    if not project_id:
        return ""
    try:
        from personalclaw.tasks.hierarchy import HierarchyStore

        project = HierarchyStore().get_project(project_id)
        if project is None:
            return ""
        return project_block(
            brief=str(getattr(project, "brief", "") or ""),
            overview=read_overview(project_id),
            instructions=str(getattr(project, "agent_instructions_template", "") or ""),
        )
    except Exception:
        logger.debug("project context block skipped for %s", project_id, exc_info=True)
        return ""


def handoff_snapshot(project_id: str) -> dict[str, Any]:
    """The Context tab's handoff projection: focus, blockers, next actions, gotchas.

    Assembled from what the project has RECORDED rather than from a model call, so it is available
    when the gateway is degraded — which is exactly when someone wants to know where they left off.
    Each field says where it came from; a snapshot that presented a derived guess as a recorded fact
    would be the same guess-as-requirement failure the planner's Step-0 schema guards against.
    """
    fog = read_ledger(project_id, "fog")
    return {
        "focus": read_overview(project_id)[:600],
        "focus_source": "overview.md (revised as runs complete)",
        "open_questions": fog,
        "open_questions_source": "not-yet-specified ledger",
        "decisions": read_ledger(project_id, "decisions")[-10:],
        "decisions_source": "decisions ledger (append-only, newest last)",
        "out_of_scope": read_ledger(project_id, "out_of_scope"),
        "out_of_scope_source": "out-of-scope ledger",
        # Explicitly absent rather than invented: ordered next actions need the run/task state a
        # caller holds, and fabricating them from the ledgers would put a plausible list in front of
        # someone about to act on it.
        "next_actions": [],
        "next_actions_note": "supplied by the caller from live run/task state",
    }
