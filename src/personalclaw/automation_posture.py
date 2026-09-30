"""Whether an automation's agent asks the owner — the per-automation half of the security posture.

An automation step (a trigger's action, a workflow node) can carry the approval decision the
global ``agent.approval_mode`` setting carries, stored on the step:

* ``approval_mode: "auto"`` makes the agent it spawns approve its own tool calls
  (``subagent._run_inner``), and
* ``capability: "mutating"`` is the write grant an unattended run otherwise never gets
  (``subagent.resolve_capability_class``: an auto-fired run defaults to read-only).

#3602's rule for a loosening write applies to both: the owner's write that loosens one needs
``"confirm": true`` (``edit_spec.unconsented_loosening``), and the refusal carries the sentence the
consent dialog shows. Tightening never asks.

An app never writes one at all, and that is decided per ROUTE rather than here: defining a
trigger or a workflow is owner-only for an app (``apps/permissions.ROUTE_AUTHZ``), because a step
screen could not keep up with what a step can do — run a shell command, start the owner's
workflows, prompt an agent that approves itself. An app's scheduled work is the ``crons`` its own
manifest declares, which install consent lists.

The spec table below is the one list of per-automation posture keys; the rail
(``tests/test_security_posture_rail.py``) drives the consent refusal for each entry.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping
from typing import Any

from personalclaw.config.edit_spec import SecurityControl, loosens_toward, unconsented_loosening

logger = logging.getLogger(__name__)

#: The step-config keys that decide whether an automation's agent asks the owner. Each is a
#: ``SecurityControl`` so the consent machinery is #3602's, not a second copy. The rank puts ``""``
#: (not set) between the two ``capability`` values because what it resolves to depends on the
#: run: read-only on an auto-approved fire, a write grant on a watched one.
POSTURE_SPECS: dict[str, dict[str, Any]] = {
    "approval_mode": {
        "security": SecurityControl(
            loosens_toward("", "auto"),
            "This automation's agent will approve its own tool calls instead of asking you "
            "first.",
        ),
    },
    "capability": {
        "security": SecurityControl(
            loosens_toward("research", "", "mutating"),
            "This automation's agent gets write access: it may change files and run commands, "
            "not only read.",
        ),
    },
}


#: The actions that start an agent, whose Allow says what that agent may do.
AGENT_STARTING_PROVIDERS: frozenset[str] = frozenset({"invoke-agent", "run-prompt"})


def what_its_agent_may_do(provider: str, config: Mapping[str, Any]) -> str:
    """What the agent an automation's action starts may do when it runs, as its Allow says it.

    Read the way the run reads it: ``run-prompt`` always runs its agent with nobody to ask, so it
    is read-only unless it names the files it changes (``write_scope``) or carries the
    ``capability: "mutating"`` grant; ``invoke-agent`` runs the same way when its step (or the
    global setting) lets the agent approve its own calls, and otherwise its agent asks. ``""`` for
    an action that starts no agent."""
    if provider not in AGENT_STARTING_PROVIDERS:
        return ""
    from personalclaw import write_scope
    from personalclaw.subagent import CAPABILITY_MUTATING, resolve_capability_class

    approval = "auto"
    if provider == "invoke-agent":
        from personalclaw.action_providers.invoke_agent_provider import approval_mode_of

        approval = approval_mode_of(dict(config))
    capability = resolve_capability_class(
        capability_class=_posture_value(config, "capability"), approval_mode=approval
    )
    if capability == CAPABILITY_MUTATING:
        if approval == "auto":
            return "Its agent may change files and run commands, not only read."
        return "Its agent asks you before it changes a file or runs a command."
    writes = write_scope.entries(dict(config))
    if writes:
        return (
            f"Its agent reads what it needs and may change only {write_scope.sentence(writes)}: "
            "it cannot change anything else or run commands."
        )
    return "Its agent only reads: it cannot change files or run commands."


def _posture_value(config: Mapping[str, Any], key: str) -> str:
    """A posture key as the runtime reads it: ``capability`` is case-folded by every reader
    (``resolve_capability_class``); ``approval_mode`` is compared as written, so a value such as
    ``"AUTO"`` stays outside the rank and is asked about rather than waved through."""
    value = str(config.get(key) or "").strip()
    return value.lower() if key == "capability" else value


def loosened_keys(config: Mapping[str, Any]) -> list[str]:
    """The posture keys whose value in *config* loosens over leaving them unset.

    What a step carries that only the owner's consent could have put there. A boot that imports a
    step from a file with no record of that consent (`triggers.legacy_import`) drops exactly these
    and keeps the rest of the step: a tightening value (``capability: "research"``) stays, and so
    does every key that is not a posture key.
    """
    return [
        key
        for key, spec in POSTURE_SPECS.items()
        if spec["security"].loosens(_posture_value({}, key), _posture_value(config, key))
    ]


def unconsented_step_loosening(
    where: str, *, current: Mapping[str, Any], new: Mapping[str, Any], body: Any
) -> tuple[str, str] | None:
    """``(field, consent)`` when the step config *new* loosens a posture key over *current* and
    *body* does not carry ``confirm: true``; ``None`` otherwise. *current* is ``{}`` for a step
    that did not exist, which is what an unset key means at run time."""
    for key, spec in POSTURE_SPECS.items():
        field = f"{where}.{key}"
        consent = unconsented_loosening(
            field,
            spec,
            current=_posture_value(current, key),
            new=_posture_value(new, key),
            body=body,
        )
        if consent:
            return field, consent
    return None


def workflow_steps(root: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``{path: step_config}`` for every node of a workflow spec that can carry a step posture,
    keyed by the engine's own instance path (``root.children[0]``).

    A ``stage`` node spawns an agent from its own config (``engine.dispatch_stage`` reads
    ``approval_mode`` and ``capability`` there); an ``action`` node dispatches ``config.provider``
    with ``config.with`` (or ``config.config``) as the action config, which is where an
    ``invoke-agent`` step's posture lives. A spec that does not parse yields ``{}``: the save then
    refuses it on validation, so nothing unscreened is stored.
    """
    from personalclaw.workflows.models import Node, NodeKind, walk

    try:
        tree = Node.from_dict(dict(root))
    except Exception:
        logger.debug("workflow spec did not parse for the posture screen", exc_info=True)
        return {}
    steps: dict[str, dict[str, Any]] = {}
    for path, node in walk(tree):
        cfg = node.config if isinstance(node.config, dict) else {}
        if node.kind is NodeKind.STAGE:
            steps[path] = cfg
        elif node.kind is NodeKind.ACTION:
            action = cfg.get("with") or cfg.get("config") or {}
            steps[path] = dict(action) if isinstance(action, dict) else {}
    return steps


def unconsented_workflow_loosening(
    name: str, *, current_root: Mapping[str, Any] | None, new_root: Mapping[str, Any], body: Any
) -> tuple[str, str] | None:
    """``(field, consent)`` for the first step of *new_root* that loosens the step at the same
    path in *current_root* (the stored definition, ``None`` for a new one) without consent."""
    current = workflow_steps(current_root) if current_root else {}
    for path, config in workflow_steps(new_root).items():
        loosened = unconsented_step_loosening(
            f"workflows.{name}.{path}", current=current.get(path, {}), new=config, body=body
        )
        if loosened is not None:
            return loosened
    return None


# ── another machine's definitions (a device sync) ────────────────────────────


def _raw_steps(node: Any, path: str = "root") -> list[tuple[str, dict[str, Any]]]:
    """``(path, the dict a step keeps its posture in)`` for every step of a RAW spec node, in the
    shape :func:`workflow_steps` reads — a ``stage``'s own config, an ``action``'s ``with`` (or
    ``config``) — keyed by the same instance path, so a change can be made where it is read."""
    if not isinstance(node, dict):
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    config = node.get("config")
    kind = str(node.get("kind", "")).strip()
    if isinstance(config, dict):
        if kind == "stage":
            out.append((path, config))
        elif kind == "action":
            action = config.get("with") or config.get("config")
            if isinstance(action, dict):
                out.append((path, action))
    for index, child in enumerate(node.get("children") or []):
        out.extend(_raw_steps(child, f"{path}.children[{index}]"))
    out.extend(_raw_steps(node.get("body"), f"{path}.body"))
    cases = node.get("cases")
    for label, case in (cases if isinstance(cases, dict) else {}).items():
        out.extend(_raw_steps(case, f"{path}.cases[{label}]"))
    out.extend(_raw_steps(node.get("default"), f"{path}.default"))
    return out


def _runs_the_same(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    """Whether two versions of one step run the same, whatever each says of its posture."""
    return {k: v for k, v in before.items() if k not in POSTURE_SPECS} == {
        k: v for k, v in after.items() if k not in POSTURE_SPECS
    }


def workflow_what_it_is(document: Mapping[str, Any]) -> dict[str, Any]:
    """*document* — a workflow definition as a file holds it, its steps under ``root`` — without
    the step keys that loosen whether a step's agent asks (:func:`loosened_keys`): a copy, or the
    document as it is when it has no such step.

    A loosening value is the owner's yes, given where they are shown the step (``confirm: true``
    on a save), so another machine's is not this one's. What a device sync compares of a
    definition, and all that one from another machine brings: a tightening value stays, and so
    does every key that is not a posture key.
    """
    if not any(loosened_keys(step) for _, step in _raw_steps(document.get("root"))):
        return dict(document)
    kept = copy.deepcopy(dict(document))
    for _, step in _raw_steps(kept.get("root")):
        for key in loosened_keys(step):
            step.pop(key, None)
    return kept


def workflow_edit_arrived(here: Mapping[str, Any], edited: Mapping[str, Any]) -> dict[str, Any]:
    """*edited* — a definition this home has (*here*), with the edit another machine made to it
    taken in, loosening keys and all left out (:func:`workflow_what_it_is`) — as this home writes
    it: each step that still runs as it ran here keeps what this home's owner allowed it (its
    loosening keys here), unless the edit set the key itself, which only a tightening value
    survives to do; a step the edit changed keeps none, so it asks again until the owner here
    allows it — the rule an automation's grant follows (``triggers.grants.narrow``). A step is the
    one at the same path, as the save's consent check reads it
    (:func:`unconsented_workflow_loosening`)."""
    allowed = {path: step for path, step in _raw_steps(here.get("root")) if loosened_keys(step)}
    if not allowed:
        return dict(edited)
    out = copy.deepcopy(dict(edited))
    for path, step in _raw_steps(out.get("root")):
        before = allowed.get(path)
        if before is not None and _runs_the_same(before, step):
            for key in loosened_keys(before):
                step.setdefault(key, before[key])
    return out
