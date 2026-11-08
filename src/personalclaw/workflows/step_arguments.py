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

from collections.abc import Callable
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


#: Where an action step's arguments may be written: ``with``, else ``config``.
HOLDERS = ("with", "config")


def holder(config: dict[str, Any]) -> str:
    """Where *config*'s arguments are, as the dispatch reads them: ``with``, else ``config``."""
    return "with" if config.get("with") else "config"


def arguments(
    config: dict[str, Any], kept: Callable[[Any], Any] | None = None, where: str | None = None
) -> dict[str, Any] | None:
    """The mapping an action step's arguments are, under its holder (*where*, else
    :func:`holder`), as its dispatch hands them to the provider: a mapping as it is, a list of pairs
    as one, nothing as an empty one; for a config as written, what is written there read by *kept*
    first, whatever its shape (each reference kept as its name). None for anything else, which the
    dispatch refuses as no mapping."""
    held = config.get(where or holder(config))
    if kept is not None:
        held = kept(held)
    if not held:
        return {}
    if isinstance(held, dict):
        return dict(held)
    pairs = isinstance(held, (list, tuple)) and all(
        isinstance(pair, (list, tuple)) and len(pair) == 2 and isinstance(pair[0], str)
        for pair in held
    )
    return dict(held) if pairs else None


#: The words a step whose argument is named by a binding is refused in, at save and at dispatch.
BOUND_NAME = (
    "a step's argument can't be named by a binding, since each check reads an argument by its name "
    "as written",
    "write each argument's name as text",
)


def bound_name(config: dict[str, Any], where: str | None = None) -> str | None:
    """The first name, under *config*'s holder (*where*, else :func:`holder`) written as pairs, that
    holds a binding (``{{…}}``) as written; None when none does. What a binding makes of a name no
    check reads: each reads an argument by its name as written."""
    held = config.get(where or holder(config))
    pairs = held if isinstance(held, (list, tuple)) else ()
    names = (pair[0] for pair in pairs if isinstance(pair, (list, tuple)) and pair)
    return next((name for name in names if isinstance(name, str) and "{{" in name), None)


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
    where = holder(config)
    named = [key for key in keys if _names(config.get(where), key)]
    return _sentence(provider, named, where) if named else ""


def dispatch_refusal(provider: str, action_config: dict[str, Any]) -> str:
    """Why a workflow step's action is refused at its dispatch, or ``""``: the arguments it was
    handed, bound values resolved, name one only an automation may give *provider*."""
    keys, _why = RUN_SCOPED_ARGUMENTS[provider]
    named = [key for key in keys if _names(action_config, key)]
    return f"This step {_sentence(provider, named, 'with')}." if named else ""
