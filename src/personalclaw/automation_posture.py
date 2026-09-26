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


def _posture_value(config: Mapping[str, Any], key: str) -> str:
    """A posture key as the runtime reads it: ``capability`` is case-folded by every reader
    (``resolve_capability_class``); ``approval_mode`` is compared as written, so a value such as
    ``"AUTO"`` stays outside the rank and is asked about rather than waved through."""
    value = str(config.get(key) or "").strip()
    return value.lower() if key == "capability" else value


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
