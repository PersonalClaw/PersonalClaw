"""The folders a subagent may work in: the workspace, a root the owner added, or its own workflow
run's folder (:func:`validate_cwd`), and that run's folder for a spawn that is a run's step
(:func:`_run_workdir_for`). Re-exported from ``personalclaw.subagent``, whose spawns read them."""

from __future__ import annotations

import os


def validate_cwd(cwd: str, allowed_roots: list[str], *, run_workdir: str = "") -> tuple[str, str]:
    """Validate a caller-supplied ``cwd`` for a spawn.

    Resolves symlinks and verifies the path is an existing directory inside the workspace (the
    folder the parent session itself works in), under a root the operator added in
    ``agent.subagent_cwd_allowed_roots``, or inside *run_workdir*: the folder of the workflow run
    the spawn is a step of (``workflows.provisioning.run_workdir``: its scratch folder, its own
    worktree, the folder its project is bound to when it works in place, or its project's context
    folder), which the engine put the run's steps in. The list is empty by default, so the
    workspace and a run's own folder are where a spawn may work until the owner names another.

    Args:
        cwd: Caller-supplied absolute path (may contain ``~``).
        allowed_roots: Extra permitted root paths from config (may contain ``~``).
        run_workdir: The spawning run's own folder, ``""`` for any other spawn.

    Returns:
        ``(resolved_cwd, error)``. On success ``error`` is empty and
        ``resolved_cwd`` is the canonical absolute path (realpath-resolved).
        On failure ``error`` is a reason string and ``resolved_cwd`` is empty.
    """
    if not cwd:
        return ("", "")
    try:
        expanded = os.path.expanduser(cwd)
        if not os.path.isabs(expanded):
            return ("", "cwd must be an absolute path")
        resolved = os.path.realpath(expanded)
    except (OSError, ValueError) as exc:
        return ("", f"cwd resolution failed: {exc}")
    if not os.path.isdir(resolved):
        return ("", "cwd does not exist or is not a directory")
    # The workspace defaults to the home's own folder, which no configured root names: a subagent
    # may always work where the session that spawns it works.
    from personalclaw.config.loader import workspace_root

    workspace = os.path.realpath(workspace_root())
    resolved_roots = [workspace] + [os.path.realpath(os.path.expanduser(r)) for r in allowed_roots]
    if run_workdir:
        resolved_roots.append(os.path.realpath(run_workdir))
    for root in resolved_roots:
        if resolved == root or resolved.startswith(root + os.sep):
            return (resolved, "")
    return (
        "",
        f"cwd is not in the workspace ({workspace}) or under any allowed root: {allowed_roots}. "
        "Add its folder in Settings → Agent defaults → Allowed working directories.",
    )


def _run_workdir_for(parent_run: str) -> str:
    """The folder of the workflow run a spawn is a step of, or ``""``.

    ``parent_run`` is ``workflow:<run_id>`` only when the workflow engine dispatches the run's own
    step: no caller of ``/api/spawn`` or of an agent's tool can set it. The folder itself comes
    from that run's record (``workflows.provisioning.run_workdir``), never from the request, so a
    spawn is admitted to its own run's folder and to no other."""
    from personalclaw.workflows.ownership import OWNED_PREFIX

    if not parent_run.startswith(OWNED_PREFIX):
        return ""
    from personalclaw.workflows.provisioning import run_workdir

    return run_workdir(parent_run[len(OWNED_PREFIX) :])
