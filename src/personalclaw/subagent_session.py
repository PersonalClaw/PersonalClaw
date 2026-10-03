"""What a subagent's session is built with: its model, its folder, its isolation and its reach.

``SubagentManager._run_inner`` acquires the agent's runtime with these
(``SessionManager.get_or_create``). The model is the spawn's own when it names one; with none the
Orchestration chain serves it. An agent started for an Incognito or Temporary chat runs on the one
model that chat's turn runs on instead (``memory_writes.spawn_model``), handed on to it with the
chat's mode when it was spawned (``memory_writes.hand_on``): its runtime is built on that model,
on the chat's own agent CLI when the chat runs on one, and a start that cannot run on it is refused,
in words, before anything is built.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from personalclaw import memory_writes
from personalclaw.subagent_tier import tier_for

if TYPE_CHECKING:
    from personalclaw.subagent import SubagentInfo


def session_kwargs(info: SubagentInfo, *, unattended: bool) -> tuple[str | None, dict[str, Any]]:
    """The model *info*'s session is built for (``None`` when its axis's chain serves it), and the
    rest of what it is built with, as ``get_or_create`` takes them. *unattended*: no person can
    answer the asks of its calls. Raises ``memory_writes.OtherModelRefused`` for a start that
    cannot run on its chat's model."""
    model, runtime = memory_writes.spawn_model(info.model)
    extra: dict[str, Any] = {"provider_kind": runtime} if runtime else {}
    if info.cwd:
        extra["cwd"] = info.cwd
    # Sandbox provider: thread the chosen isolation backend to the ACP worker
    # launch. Only forward a non-default so the chat/native paths (which ignore it) are
    # untouched; ``none`` is the transport default.
    if info.sandbox and info.sandbox != "none":
        extra["sandbox"] = info.sandbox
    if unattended:
        extra["unattended"] = True
    # Dry-run replay (T9): observe-mode — write-capable tools don't execute, so
    # the run previews what WOULD happen with no side effects.
    if info.dry_run:
        extra["dry_run"] = True
    from personalclaw.workflows.provisioning import step_documents, step_reads

    if roots := [*info.may_read, *info.may_change, *step_documents(info.parent_run)]:
        extra["extra_tool_roots"] = roots  # its trigger's reach, its run's documents
    if reads := step_reads(info.parent_run):  # its run's project tree, its batch's folder
        extra["read_tool_roots"] = reads
    if session_env := tier_for(info).session_env(info.extra_env):
        # The leaf's posture + lineage, and the run's tier. Passed through the session's
        # `extra_env` seam, which already forces a cold (non-pooled) session — a warm pooled
        # worker would carry the PREVIOUS leaf's env, and inheriting a sibling's capability
        # flag is precisely the cross-contamination this must not have.
        extra["extra_env"] = session_env
    return model or None, extra
