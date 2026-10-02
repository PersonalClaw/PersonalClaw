"""Whether an automation's agent asks the owner — the per-automation half of the security posture.

An automation step (a trigger's action, a workflow node) carries its own approval decision, stored
on the step, so its run asks the owner or not by the consent given for that step:

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

What a trigger's agent-starting step lets its agent do when it runs is :func:`agent_run_policy`:
its run is built from it and its Allow is said from it (:class:`AgentRunPolicy`). Two facts outside
the step decide it too, both read where the Allow is said and again at every fire
(:func:`fire_policy`): whether the owner trusts the step's working folder
(``guardrails.project_trust``), and whether its agent runs on an agent CLI, which no write scope
can be held to (``write_scope``). One that changes after the Allow holds the run back or lets it
have what the Allow named for that case, never more, and a run held back says why
(:attr:`AgentRunPolicy.held_back`).
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from personalclaw.config.edit_spec import SecurityControl, loosens_toward, unconsented_loosening

logger = logging.getLogger(__name__)

#: What loosening ``capability`` gives an automation's agent, as its consent says it.
_WRITE_ACCESS = (
    "This automation's agent gets write access: it may change files, run commands and send "
    "messages, not only read."
)

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
        "security": SecurityControl(loosens_toward("research", "", "mutating"), _WRITE_ACCESS),
    },
}


#: The actions that start an agent, whose Allow says what that agent may do.
AGENT_STARTING_PROVIDERS: frozenset[str] = frozenset({"invoke-agent", "run-prompt"})

#: Where a message an automation's agent sends its owner goes (``notify``): the app, or a chat
#: channel the owner connected. It is the one thing a read-only run may send.
_TO_THE_OWNER = "in PersonalClaw or on a chat channel you've connected"


@dataclass(frozen=True)
class AgentRunPolicy:
    """What the agent an automation's action starts may do when it runs.

    The one mapping from a step's posture to its run: the action builds the run from it
    (``run-prompt``, ``invoke-agent``: ``approval_mode``, ``capability_class``, ``may_change``),
    the run's tool policy is built from those (``subagent_tier.tier_for``), and the Allow is
    :meth:`sentence`, so what the owner is told and what the run may do cannot drift apart
    (``tests/test_an_automations_allow_is_what_its_run_may_do.py`` holds them equal for every
    posture).

    * ``approval_mode`` is ``"auto"`` when nobody is asked about its calls, ``""`` (or any other
      value) when each change asks the owner first.
    * ``capability_class`` is the class the run is held to: ``research`` reads, ``mutating`` may
      change. A step given write access whose working folder the owner has not trusted is held to
      ``research`` (``project_trust.held_to``), and ``untrusted_folder`` names that folder.
    * ``writes`` are the files its job changes, as the owner wrote them (``write_scope``): a
      reading run may change those and nothing else, unless its agent runs on an agent CLI
      (``runtime``), which changes files with its own tools and is given none of them.
    * ``owners_auto`` is the owner's Approval mode "Auto" approving the calls of an agent whose
      step does not (`approval_grants.setting_grant`, for an agent no chat started, as a
      trigger's is). The run is handed nothing for it: the subagent manager reads the setting at
      each call. So the Allow names it, rather than say an agent asks that will not.

    An automation's own agent may always tell its owner what it found (``notify``, to the owner
    only: ``tool_providers.base.only_tells_the_owner``), and that message leaves the machine when
    it goes to a chat channel, so every sentence names it.
    """

    approval_mode: str
    capability_class: str
    writes: tuple[str, ...] = ()
    owners_auto: bool = False
    #: The step's working folder as written, when its owner has not trusted it and that holds back
    #: the write access the step asks for; "" otherwise.
    untrusted_folder: str = ""
    #: The agent CLI its agent runs on (``agents.runners.runtime_id_for_agent``), "" for
    #: PersonalClaw's own.
    runtime: str = ""

    @property
    def asks(self) -> bool:
        return self.approval_mode != "auto" and not self.owners_auto

    @property
    def reads_only(self) -> bool:
        from personalclaw.subagent import CAPABILITY_RESEARCH

        return self.capability_class == CAPABILITY_RESEARCH

    @property
    def may_change(self) -> tuple[str, ...]:
        """The real paths of :attr:`writes` (``write_scope.scope``): what a reading run may change,
        and where its file tools reach to change it. None on an agent CLI."""
        from personalclaw import write_scope

        return () if self.runtime else write_scope.scope(self.writes)

    @property
    def _kept_from_its_files(self) -> bool:
        """Whether its agent may not change the files its step names: a reading run on a CLI."""
        return bool(self.runtime and self.writes and self.reads_only)

    @property
    def held_back(self) -> str:
        """Why its run may do less than its step asks for, in the words its history and its
        trigger say it; "" when it may do all of it."""
        from personalclaw import write_scope

        said: list[str] = []
        if self.untrusted_folder:
            kept = write_scope.sentence(self.writes) if self.may_change else ""
            does = f"may change only {kept}" if kept else "only reads"
            said.append(
                f"Its agent {does}: its working folder {self.untrusted_folder} is in Preview "
                "until you trust it."
            )
        if self._kept_from_its_files:
            said.append(write_scope.not_held_on(self.runtime, self.writes))
        return " ".join(said)

    def sentence(self) -> str:
        """What the Allow says this agent may do, every effect that reaches past a read named, and
        what its working folder's trust would let it do once the owner gives it."""
        from personalclaw import write_scope

        if not self.reads_only:
            return f"Its agent {_with_write_access(self)}"
        changes = write_scope.sentence(self.writes) if self.may_change else ""
        if self.asks:
            acts = f"changes {changes} or messages you" if changes else "messages you"
            does = f"Its agent reads what it needs, and asks you before it {acts} {_TO_THE_OWNER}"
        elif changes:
            does = (
                f"Its agent reads what it needs, may change only {changes}, and may message you "
                f"{_TO_THE_OWNER}"
            )
        else:
            does = f"Its agent only reads, and may message you {_TO_THE_OWNER}"
        cannot = "anything else" if changes else "files"
        said = f"{does}: it cannot change {cannot}, run commands or message anyone else."
        if self._kept_from_its_files:
            said += " " + write_scope.not_held_on(self.runtime, self.writes)
        if self.untrusted_folder:
            said += (
                f" Its working folder {self.untrusted_folder} is in Preview: once you trust that "
                f"folder, it {_with_write_access(self)}"
            )
        return said


def _with_write_access(policy: AgentRunPolicy) -> str:
    """What the agent *policy* describes does with write access, after "Its agent" or "it"."""
    if policy.asks:
        return "asks you before it changes a file, runs a command or sends a message."
    if policy.owners_auto:
        return (
            "may change files, run commands and send messages without asking you, because "
            "Settings → Agent defaults → Approval mode is Auto."
        )
    return "may change files, run commands and send messages without asking you."


def agent_run_policy(provider: str, config: Mapping[str, Any]) -> AgentRunPolicy:
    """What the agent an action of *provider* (one of :data:`AGENT_STARTING_PROVIDERS`) starts
    may do when it runs, from its step *config*, read without changing anything.

    ``run-prompt`` always runs its agent with nobody to ask; ``invoke-agent`` does when its step
    (or the hook setting) lets the agent approve its own calls (``approval_mode_of``), and its
    agent asks otherwise, unless the owner chose Approval mode "Auto" (:attr:`owners_auto`). The
    class is ``subagent.resolve_capability_class``'s: read-only for a run nobody is asked in,
    unless the step carries ``capability: "mutating"``, and read-only in a working folder its
    owner has not trusted (``project_trust.held_to``)."""
    from personalclaw.guardrails.project_trust import held_to

    return _policy(provider, config, held_to)


def fire_policy(provider: str, config: Mapping[str, Any]) -> AgentRunPolicy:
    """:func:`agent_run_policy` at a fire, the one check both agent-starting actions build their
    run from: the first fire a working folder holds back also records it as Preview and asks the
    owner to trust it (``project_trust.gate_project_capability``)."""
    from personalclaw.guardrails.project_trust import gate_project_capability

    return _policy(provider, config, gate_project_capability)


def step_problem(config: Mapping[str, Any]) -> str:
    """Why an agent-starting step's files to change cannot be saved as written, or "": a scope no
    save takes (``write_scope.problem``), or any at all for an agent that runs on an agent CLI,
    which no scope can be held to (``write_scope.not_given_on``). Both doors that save one ask it:
    the Triggers page (``dashboard.handlers.triggers._action_problem``) and the automation tools
    the chat and the CLI save through (``triggers.tools.write_scope_refusal``)."""
    from personalclaw import write_scope

    refused = write_scope.problem(config.get("writes"))
    if refused or not write_scope.entries(dict(config)):
        return refused
    runtime = _runtime_of(config)
    return write_scope.not_given_on(runtime) if runtime else ""


def _runtime_of(config: Mapping[str, Any]) -> str:
    """The agent CLI the step's agent runs on, "" for PersonalClaw's own: the precedence the
    session factory builds its runtime by (``agents.runners.runtime_id_for_agent``)."""
    from personalclaw.agents.runners import runtime_id_for_agent

    return runtime_id_for_agent(str(config.get("agent") or "").strip() or None)


def _policy(
    provider: str, config: Mapping[str, Any], held_to: Callable[[str, str], str | None]
) -> AgentRunPolicy:
    from personalclaw import write_scope
    from personalclaw.subagent import resolve_capability_class

    approval = "auto"
    if provider == "invoke-agent":
        from personalclaw.action_providers.invoke_agent_provider import approval_mode_of

        approval = approval_mode_of(dict(config))
    asked = resolve_capability_class(
        capability_class=_posture_value(config, "capability"), approval_mode=approval
    )
    folder = str(config.get("cwd") or "").strip()
    capability = held_to(folder, asked) or asked
    return AgentRunPolicy(
        approval_mode=approval,
        capability_class=capability,
        writes=tuple(write_scope.entries(dict(config))),
        owners_auto=provider == "invoke-agent" and approval != "auto" and _the_owners_auto_stands(),
        untrusted_folder=folder if capability != asked else "",
        runtime=_runtime_of(config),
    )


def _the_owners_auto_stands() -> bool:
    """Whether the owner's Approval mode "Auto" approves an agent no chat started now, as the
    operator ceiling lets it (`approval_grants`); a sentence asks it, so nothing is audited."""
    from personalclaw import approval_grants

    grant = approval_grants.setting_grant()
    return bool(grant) and approval_grants.stands(grant, caller="automation", audit=False)


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
    where: str,
    *,
    current: Mapping[str, Any],
    new: Mapping[str, Any],
    body: Any,
    provider: str = "",
) -> tuple[str, str] | None:
    """``(field, consent)`` when the step config *new* loosens a posture key over *current* and
    *body* does not carry ``confirm: true``; ``None`` otherwise. *current* is ``{}`` for a step
    that did not exist, which is what an unset key means at run time.

    *provider* is the action the step runs, when it runs one. The write access an agent-starting
    action (:data:`AGENT_STARTING_PROVIDERS`) is given waits for its working folder's trust
    (``project_trust.held_to``), and its consent says so (:func:`_write_access_as_it_stands`)."""
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
            if key == "capability" and provider in AGENT_STARTING_PROVIDERS:
                consent = _write_access_as_it_stands(new)
            return field, consent
    return None


def _write_access_as_it_stands(step: Mapping[str, Any]) -> str:
    """What giving an agent-starting *step*'s agent write access gives it as things stand: write
    access, or, in a working folder its owner has not trusted, write access once they do."""
    from personalclaw.guardrails.project_trust import held_to
    from personalclaw.subagent import CAPABILITY_MUTATING

    folder = str(step.get("cwd") or "").strip()
    if held_to(folder, CAPABILITY_MUTATING) == CAPABILITY_MUTATING:
        return _WRITE_ACCESS
    return (
        f"This automation's agent gets write access once you trust its working folder {folder}: "
        "then it may change files, run commands and send messages, not only read."
    )


def workflow_steps(root: Mapping[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    """``{path: (provider, step_config)}`` for every node of a workflow spec that can carry a
    step posture, keyed by the engine's own instance path (``root.children[0]``).

    A ``stage`` node spawns an agent from its own config (``engine.dispatch_stage`` reads
    ``approval_mode`` and ``capability`` there), and runs no action (``provider`` ``""``); an
    ``action`` node dispatches ``config.provider`` with ``config.with`` (or ``config.config``) as
    the action config, which is where an ``invoke-agent`` step's posture lives. A spec that does
    not parse yields ``{}``: the save then refuses it on validation, so nothing unscreened is
    stored.
    """
    from personalclaw.workflows.models import Node, NodeKind, walk

    try:
        tree = Node.from_dict(dict(root))
    except Exception:
        logger.debug("workflow spec did not parse for the posture screen", exc_info=True)
        return {}
    steps: dict[str, tuple[str, dict[str, Any]]] = {}
    for path, node in walk(tree):
        cfg = node.config if isinstance(node.config, dict) else {}
        if node.kind is NodeKind.STAGE:
            steps[path] = ("", cfg)
        elif node.kind is NodeKind.ACTION:
            action = cfg.get("with") or cfg.get("config") or {}
            provider = str(cfg.get("provider") or "").strip()
            steps[path] = (provider, dict(action) if isinstance(action, dict) else {})
    return steps


def unconsented_workflow_loosening(
    name: str, *, current_root: Mapping[str, Any] | None, new_root: Mapping[str, Any], body: Any
) -> tuple[str, str] | None:
    """``(field, consent)`` for the first step of *new_root* that loosens the step at the same
    path in *current_root* (the stored definition, ``None`` for a new one) without consent."""
    current = workflow_steps(current_root) if current_root else {}
    for path, (provider, config) in workflow_steps(new_root).items():
        loosened = unconsented_step_loosening(
            f"workflows.{name}.{path}",
            current=current.get(path, ("", {}))[1],
            new=config,
            body=body,
            provider=provider,
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
