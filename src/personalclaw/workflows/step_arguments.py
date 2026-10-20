"""The arguments of a workflow step's action that would reach past the step's own run.

An action step's arguments (``config.with``) are its inputs, as its template writes them. A few
arguments name whose work the action's work is: the project a run it starts belongs to, the chat an
agent it starts answers to, and the session a handoff is recorded as and the folder it works and
writes in. An automation's action names them as the owner set it up and allowed it. A workflow
step's may not, since a template can be written or edited by a model: for a step each is its own
run's, which its dispatch states (``ActionContext.project_id``, and the run's identity the engine
stamps on the step's payload), and the provider tells the two apart by that dispatch
(``action_providers.base.is_workflow_step``), never by a payload.

So a template whose step names one is refused, naming the key, where it is validated (saving it, a
dry run, an edit to a running run: ``validator``) and when a run of it first starts
(``run_start.admit_step_identities``), both through ``authored_refusal``; and the provider refuses
one a step is handed at its dispatch (``dispatch_refusal``), where an argument bound in from
another step's output is first seen.
"""

from __future__ import annotations

from typing import Any

#: By provider: the arguments only an automation may give it, and why a workflow step's are its own
#: run's. Each reason is what the provider does for a step instead, so it is true as written.
RUN_SCOPED_ARGUMENTS: dict[str, tuple[tuple[str, ...], str]] = {
    "run-workflow": (
        ("project_id",),
        "The run a workflow step starts is in the step's own project",
    ),
    "run-prompt": (
        ("session",),
        "The agent a workflow step starts works for the step's own run, never for a chat",
    ),
    "second-opinion": (
        ("session_key", "workspace", "brief_dir"),
        "A workflow step's handoff is its own run's: it works and writes its brief in the run's "
        "folder, and is recorded as the run's",
    ),
}


def _names(arguments: Any, key: str) -> bool:
    """Whether *arguments* give *key* a value: an empty one names nothing, as each provider reads
    it."""
    return isinstance(arguments, dict) and str(arguments.get(key) or "").strip() != ""


def _sentence(provider: str, keys: list[str], where: str) -> str:
    """What a step whose arguments name *keys* of *provider* is told, after its subject."""
    _keys, why = RUN_SCOPED_ARGUMENTS[provider]
    names = [f"`{key}`" for key in keys]
    named = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    that, it = ("that key", "it") if len(names) == 1 else ("those keys", "them")
    return (
        f"names {named} in its `{where}`. {why}, so a template cannot set {that}: remove {it} "
        f"from the step's `{where}`"
    )


def authored_refusal(config: dict[str, Any]) -> str:
    """What an action step whose config, as authored, names an argument only an automation may give
    its provider is told, after its subject ("This step …" at save, the step by name at run start),
    or ``""``. Its arguments are where the engine reads them (``with``, else ``config``). Arguments
    bound whole (``"{{nodes.plan.output}}"``) name no keys here; the provider refuses one it is
    handed at the step (``dispatch_refusal``)."""
    provider = str(config.get("provider") or "")
    keys, _why = RUN_SCOPED_ARGUMENTS.get(provider, ((), ""))
    where = "with" if config.get("with") else "config"
    named = [key for key in keys if _names(config.get(where), key)]
    return _sentence(provider, named, where) if named else ""


def dispatch_refusal(provider: str, action_config: dict[str, Any]) -> str:
    """Why a workflow step's action is refused at its dispatch, or ``""``: the arguments it was
    handed, bound values resolved, name one only an automation may give *provider*."""
    keys, _why = RUN_SCOPED_ARGUMENTS[provider]
    named = [key for key in keys if _names(action_config, key)]
    return f"This step {_sentence(provider, named, 'with')}." if named else ""
