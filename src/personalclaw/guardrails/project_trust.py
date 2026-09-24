"""Project-folder Trust/Preview gate (AUTONOMY-GUARDRAILS §4.3).

Before a project-bound run executes *project* scripts — a project ``<cwd>/loop.md`` picked up by
run-prompt, or a Code-loop deliverable gate running project commands — the first touch of a folder
asks **Trust** vs **Preview**:

* **Trust** → the run may execute project scripts; its declared capability grant stands.
* **Preview** (the safe default, and what an untrusted/undecided folder gets) → the run proceeds
  under ``REVIEW_ONLY``: read-only, no script execution.

Preview maps onto the §4.1 capability class rather than inventing a second read-only mechanism:
:func:`gate_project_capability` forces the spawn's ``capability_class`` to ``research`` for an
untrusted folder, so "REVIEW_ONLY for project-script execution" is the SAME approval-layer denial a
read-only research spawn gets (``subagent._run_inner``). One read-only control, two entry points.

Decisions persist in ``~/.personalclaw/project_trust.json`` keyed by the RESOLVED directory::

    {"<resolved dir>": {"trusted": bool, "decided_at": "<iso8601>"}}

The FIRST touch of an unknown folder persists a Preview record (so the prompt fires ONCE, not every
fire) and raises a needs-input inbox row asking the user to Trust the folder. An explicit Trust
(:func:`record_project_trust` with ``trusted=True``, exposed at ``POST /api/guardrails/project-
trust``) flips the record; only then does a write grant execute in that folder.

**Fail direction.** Reads are fail-OPEN for the *store* (a corrupt/missing file never crashes a
fire) but fail-CLOSED for the *decision*: absence of a record means Preview (read-only), never
trusted. Note cron scripts are already path-fenced to ``~/.personalclaw/crons/``
(:func:`schedule_script.resolve_script_path`) — this gate covers the PROJECT-folder gap, not the
cron path.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

_FILENAME = "project_trust.json"

# The capability class an untrusted/Preview folder forces. Kept equal to
# ``subagent.CAPABILITY_RESEARCH`` (a coherence test asserts it) so Preview reuses the §4.1
# read-only class rather than a parallel read-only mechanism that could drift from it.
PREVIEW_CAPABILITY = "research"

#: Trust decision states.
DECISION_TRUSTED = "trusted"
DECISION_PREVIEW = "preview"
DECISION_UNKNOWN = "unknown"

#: Notification surface for the first-touch prompt — an EXISTING registered attention pair
#: (``system``/``agent_request``, see ``notification_kinds``), reused so no new kind is minted.
_PROMPT_SOURCE = "system"
_PROMPT_KIND = "agent_request"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _trust_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _FILENAME


def resolve_dir(cwd: str) -> str:
    """The canonical store key for a project directory.

    ``realpath`` collapses symlinks and ``..`` so two spellings of one folder share one decision —
    a decision recorded for ``/proj`` must not be bypassed by touching ``/proj/./`` or a symlink to
    it. An empty/blank ``cwd`` yields ``""`` (no project folder → the caller does not gate)."""
    c = (cwd or "").strip()
    if not c:
        return ""
    try:
        return os.path.realpath(c)
    except OSError:
        return c


def _read_store() -> dict[str, Any]:
    """The whole trust store, or ``{}`` on a corrupt/missing file (warn, never crash)."""
    path = _trust_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning(
            "project_trust store at %s is unreadable/corrupt — treating all folders as Preview",
            path,
            exc_info=True,
        )
        return {}
    return data if isinstance(data, dict) else {}


def _write_store(data: dict[str, Any]) -> None:
    atomic_write(_trust_path(), json.dumps(data, indent=2, sort_keys=True))


def project_decision(cwd: str) -> str:
    """The recorded decision for a folder: ``trusted`` / ``preview`` / ``unknown`` (first touch).

    Fail-CLOSED: a corrupt store or a record with a non-true ``trusted`` is Preview, not trusted."""
    key = resolve_dir(cwd)
    if not key:
        return DECISION_UNKNOWN
    rec = _read_store().get(key)
    if not isinstance(rec, dict):
        return DECISION_UNKNOWN
    return DECISION_TRUSTED if rec.get("trusted") is True else DECISION_PREVIEW


def record_project_trust(cwd: str, trusted: bool) -> dict[str, Any]:
    """Persist a Trust/Preview decision for ``cwd`` (keyed by resolved dir). SEL-audited.

    Returns the stored record. ``trusted=True`` is the explicit **Trust**; ``trusted=False`` records
    (or keeps) **Preview**. Idempotent per state — re-recording the same decision only refreshes
    ``decided_at``."""
    key = resolve_dir(cwd)
    if not key:
        raise ValueError("project trust requires a non-empty directory")
    store = _read_store()
    record = {"trusted": bool(trusted), "decided_at": _now_iso()}
    store[key] = record
    _write_store(store)
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="project_trust",
            operation="project_trust.record",
            outcome="trusted" if trusted else "preview",
            source="guardrails",
            resources=f"dir={key}",
        )
    except Exception:  # noqa: BLE001 - audit is best-effort; the decision still persisted
        logger.debug("project_trust: SEL audit failed", exc_info=True)
    return record


def _prompt_trust_vs_preview(resolved_dir: str, state: Any | None) -> None:
    """Raise a needs-input inbox row asking the user to Trust the folder (or keep Preview).

    Headless-safe (a fire with no dashboard state still persisted the Preview record); deduped on
    the folder so a folder that fires every 20 minutes prompts ONCE, not a hundred times."""
    try:
        from personalclaw.inbox import emit_attention_item

        if state is None:
            try:
                from personalclaw.inbox_providers.native_source import get_dashboard_state

                state = get_dashboard_state()
            except Exception:  # noqa: BLE001 - headless: the row is best-effort, decision persisted
                state = None
        emit_attention_item(
            state,
            source=_PROMPT_SOURCE,
            kind=_PROMPT_KIND,
            item_kind=_PROMPT_KIND,
            title="Trust this project folder?",
            body=(
                f"An automation wants to run project scripts in {resolved_dir!r}. Until you Trust "
                "it, the run stays in Preview (read-only, no script execution). Trust the folder "
                "to allow it to write and run project scripts."
            ),
            refs={"dir": resolved_dir, "guardrail": "project_trust"},
            dedup_key=f"project_trust:{resolved_dir}",
        )
    except Exception:  # noqa: BLE001 - the prompt is best-effort; the safe default already applied
        logger.warning("project_trust: could not raise the Trust prompt", exc_info=True)


def gate_project_capability(
    cwd: str, requested: str | None, *, state: Any | None = None
) -> str | None:
    """Bound a spawn's capability class by the project folder's trust decision (§4.3).

    * **Trusted** folder → ``requested`` stands (the run's own capability grant decides).
    * **Preview** (already decided) → forced to :data:`PREVIEW_CAPABILITY` (read-only).
    * **Unknown** (first touch) → persist a Preview record, raise the Trust prompt, and force
      read-only. The write grant a caller passed CANNOT execute project scripts in a folder the user
      never trusted — the dangerous direction this gate exists to refuse.

    A blank ``cwd`` (no project folder) passes ``requested`` through unchanged: the gate is for
    project-bound runs only."""
    if not (cwd or "").strip():
        return requested
    decision = project_decision(cwd)
    if decision == DECISION_TRUSTED:
        return requested
    if decision == DECISION_UNKNOWN:
        resolved = resolve_dir(cwd)
        record_project_trust(cwd, trusted=False)
        _prompt_trust_vs_preview(resolved, state)
    # Preview (decided or just-recorded first touch): read-only, no script execution.
    return PREVIEW_CAPABILITY
