"""What a trigger is allowed to run, who gives it that, and the words for asking and refusing.

`triggers.screen` is the fence: `ungranted_providers` names the providers a trigger's action runs
that its frozen block does not permit, and `grant_action` writes them into the block. This module
is what surrounds the fence, so a refusal, a consent question and the Triggers page describe one
grant the same way.

🔴 THE RULE. Nothing runs an action without the grant it needs; a grant is the owner's yes to the
action as it stood when they gave it; and nothing but that yes gives one.

* **Both dispatches check.** The attended one
  (`dashboard.handlers.trigger_runs._dispatch_store_action` — Run now, an answer, the restart
  review's Run now) and the unattended one (`gateway._fire_store_trigger` — clock, event, file,
  web_watch, chained, webhook, view). Clock, event, webhook and view fires also meet the fence in
  `service.admit_fire`; file, web_watch and chained fires reach the dispatch without it, and
  before this ran whatever they held. A lifecycle trigger's fire checks it too
  (`hooks.run_script_hook`). A refusal says which grant is missing and how the owner gives it
  (:func:`refusal`).
* **A grant covers what the action runs, not only which provider runs it** (:func:`narrow`). An edit
  that changes what a granted action runs — another command, URL or prompt, another agent or
  workflow — keeps no grant for the change, so the new version is refused until the owner allows
  it, and a provider the edited action no longer runs keeps no grant either. An edit that leaves the
  action as it was keeps its grant. The step keys that decide whether the action's agent asks the
  owner (`automation_posture`) are left out of the comparison: loosening one is asked about on its
  own, and tightening one needs nobody's yes.
* **Only the owner gives one, by saying yes** (:func:`give`): the Triggers page's switch (its Allow
  is the same switch sent on again), the create dialog and the editor, each after its consent
  question (:func:`question`); and the CLI's `cron add` and `cron update` with `--yes`. Everything
  else creates and edits without one. A trigger the chat makes (`automation_create`,
  `set_onetime_task`, `set_recurring_task`) is made without one, so one whose action needs a grant
  does not run until the owner allows it on the Triggers page (an action that only reads, or only
  sends the owner words they gave, needs none); a chat edit that needs a grant is saved with the
  trigger switched off (`tools.update`); and the chat cannot switch one on.
* **The agent an allowed action starts starts on that yes** (:func:`allows_its_agent`). The owner
  who allowed a trigger "to use the “Invoke Agent” action when it runs" was asked whether its agent
  may start, so its start does not ask again. What the agent then does asks as any agent's calls do.
* **A yes to run a workflow is a yes to the version it saw** (:func:`allowed_workflow`). The
  workflow changes after the yes, and what a ``run-workflow`` action runs is the workflow, so the
  grant records the version it was given for, and a fire runs that one
  (`workflows.automation_version`): a newer version the owner saves in the workflow's editor is
  followed, since that save is her yes; a newer one anything else saves — an agent's tool, a sync,
  an import, an app — is not, until she chooses "Use vN" (:func:`use_current_version`), which asks.
  The workflows it runs as steps are held to the versions they were at the yes, by the same rule.
* **A restart gives nothing.** The capability backfill that granted every ungranted row whatever
  it ran, on every start, is gone: by the time it ran, the edit it rewarded was nobody's decision.

The system's own triggers are granted by the code that makes them, because each runs an action
PersonalClaw fixes and each is switched on by the owner: an app's crons (its install consent lists
them), the settings-driven singletons (`system:*`), a research report's schedule, the triage digest,
the Self-QA watch and a logged decision's review card. None of them takes an action from a caller.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from personalclaw.workflows.automation_version import Allowed, Current

logger = logging.getLogger(__name__)

#: The action that runs a workflow, whose grant records the version of the workflow it allows.
RUNS_A_WORKFLOW = "run-workflow"
#: Where in a grant's block that version is kept (`automation_version.Allowed`).
_ALLOWED_WORKFLOW = "workflow"
#: What a version's saver is called where the owner reads it (`versions.SAVERS`).
_SAVED_BY_WORDS = {
    "owner": "you, in the workflow's editor",
    "publish": "the publish switch",
    "agent": "an agent",
    "refiner": "the refiner",
    "import": "an import",
    "app": "an app",
    "shipped": "PersonalClaw",
    "brought_in": "another machine, a restore or a pack",
}


class Question(NamedTuple):
    """What saving a trigger needs the owner to allow, as the owner is asked it: the providers,
    the sentence they agree to (:func:`consent`) and the dialog's heading (:func:`title`)."""

    providers: list[str]
    sentence: str
    title: str


def missing(trigger: Any) -> list[str]:
    """The providers `trigger`'s action runs that it is not allowed to. `[]` when it may run.

    A provider nothing dispatches is left out: a run of it is refused first, for the app it needs
    (both dispatches resolve the provider before they check the grant, `triggers.cannot_run`), and
    that app is what the owner has to fix, since allowing it would change nothing. The fence itself
    still refuses it (`screen.ungranted_providers` fails closed), and it needs a grant the moment an
    app providing it is installed.
    """
    from personalclaw.triggers.screen import ungranted_providers

    ungranted = ungranted_providers(trigger)
    if not ungranted:
        return []
    try:
        from personalclaw.action_providers.registry import dispatchable_action_providers

        known = dispatchable_action_providers()
    except Exception:  # noqa: BLE001 - an unreadable registry leaves every grant required
        return ungranted
    return [provider for provider in ungranted if provider in known]


def labels(trigger: Any) -> list[str]:
    """What :func:`missing` names, as the owner reads it (`Bash Command`, not `bash`)."""
    return [provider_label(provider) for provider in missing(trigger)]


def provider_label(provider: str) -> str:
    """An action provider's display name (`bash` → `Bash Command`), or its id when none is known.

    Through `dispatchable_action_providers` first: the built-ins register lazily on the first action
    run, so a process that has run none would otherwise label every action by its bare id. Reads the
    display name and nothing else (`test_the_grant_copy_only_reads_the_display_name`).
    """
    try:
        from personalclaw.action_providers.registry import (
            dispatchable_action_providers,
            get_action_provider,
        )

        dispatchable_action_providers()
        found = get_action_provider(provider)
    except Exception:  # noqa: BLE001 - a label must not fail the sentence it is in
        found = None
    label = str(getattr(found, "display_name", "") or "") if found is not None else ""
    return label or provider


def _uses(providers: list[str]) -> str:
    named = " and ".join(f"“{provider_label(p)}”" for p in providers)
    return f"the {named} action{'s' if len(providers) > 1 else ''}"


def _name(trigger: Any) -> str:
    """The trigger as these sentences name it: its name masked the way the Automations page shows
    it (`security.redact_for_display`), since a consent dialog or a refusal is a read of it too."""
    from personalclaw.security import redact_for_display

    name = str(getattr(trigger, "name", "") or getattr(trigger, "id", "") or "this trigger")
    return redact_for_display(name)


def _runs(trigger: Any, provider: str) -> dict[str, Any] | None:
    """What `trigger`'s action runs with `provider`, in the form two versions are compared in; None
    when its action does not run `provider`.

    The action's config, less what is not what it runs: the posture keys, which are asked about on
    their own when they loosen (`automation_posture`), and the forms' unset values — ``""``, ``[]``
    or ``{}`` for a field left empty, and ``timeout: 0``, which the schedule form writes for "the
    default" and which every provider that reads it takes as unset (`int(timeout or 0) or
    default`). A save that changed nothing must not read as a new command. Any other ``0`` or
    ``False`` is a value, and stays: turning a flag off can be exactly the change that matters.
    """
    from personalclaw.automation_posture import POSTURE_SPECS

    workflow = getattr(trigger, "workflow", None)
    if not isinstance(workflow, dict):
        return None
    nested = workflow.get("inline")
    inline: dict[str, Any] = nested if isinstance(nested, dict) else workflow
    if str(inline.get("provider") or "").strip() != provider:
        return None
    raw = inline.get("config")
    config: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return {
        key: value
        for key, value in config.items()
        if key not in POSTURE_SPECS
        and value not in (None, "", [], {})
        and not (key == "timeout" and value == 0)
    }


def narrow(trigger: Any, before: Any) -> list[str]:
    """Keep in `trigger`'s grant only what its edited action still runs as `before` ran it.

    Called on every edit that carries an action, with the row as it was stored. A provider stays
    granted when the edited action runs it with the same config; it loses its grant when the action
    runs it with a different one (returned, so the caller can say the action changed), and when the
    action no longer runs it at all — a grant left for a provider the trigger stopped using is how a
    later edit back to it would run unasked. Replaces the block rather than editing it, so a copy
    that shares it with the stored row (`copy.copy`) leaves that row untouched.
    """
    from personalclaw.triggers.screen import capabilities_for_action

    frozen = getattr(trigger, "capabilities", None)
    block = dict(frozen) if isinstance(frozen, dict) else {}
    held = block.get("providers")
    if not isinstance(held, (list, tuple)):
        return []
    runs = set(capabilities_for_action(trigger).get("providers", []))
    kept: list[str] = []
    changed: list[str] = []
    for provider in held:
        if not isinstance(provider, str) or provider not in runs:
            continue
        if _runs(before, provider) == _runs(trigger, provider):
            kept.append(provider)
        else:
            changed.append(provider)
    if kept != list(held):
        if kept:
            block["providers"] = kept
        else:
            block.pop("providers")
        if RUNS_A_WORKFLOW not in kept:
            # The version of the workflow a yes was given for goes with the yes.
            block.pop(_ALLOWED_WORKFLOW, None)
        trigger.capabilities = block
    return changed


def question(candidate: Any, *, before: Any = None) -> Question | None:
    """What saving `candidate` needs the owner to allow (:class:`Question`), or None.

    The one grant question every owner surface asks: the create dialog, the editor and the CLI.
    `before` is the row as stored, None for a new one. The candidate is narrowed against it first
    (:func:`narrow`), in place, so an edit that changes what a granted action runs is asked about as
    the new action it is, and the candidate is the row the save would store.
    """
    changed = narrow(candidate, before) if before is not None else []
    need = missing(candidate)
    if not need:
        return None
    if before is None:
        return Question(
            need, consent(candidate, need, creating=True), title(candidate, need, creating=True)
        )
    return Question(
        need,
        consent(candidate, need, saving=True, changed=changed),
        title(candidate, need, saving=True, changed=changed),
    )


def title(
    trigger: Any,
    providers: list[str],
    *,
    creating: bool = False,
    saving: bool = False,
    changed: list[str] | tuple[str, ...] = (),
) -> str:
    """The consent dialog's heading for the question :func:`consent` words, with the same
    arguments. It names what is being allowed; "Loosen a security setting?" is the heading of a
    loosened posture alone (`config.edit_spec.LOOSEN_TITLE`)."""
    from personalclaw.triggers.legacy_import import IMPORTED_BY

    if creating:
        return "Allow what this trigger runs?"
    if saving:
        if changed and set(providers) <= set(changed):
            return "Allow the changed action?"
        return "Allow the new action?"
    if getattr(trigger, "created_by", "") == IMPORTED_BY:
        return "Allow a trigger from an older version?"
    return "Allow this trigger to run?"


def consent(
    trigger: Any,
    providers: list[str],
    *,
    creating: bool = False,
    saving: bool = False,
    changed: list[str] | tuple[str, ...] = (),
) -> str:
    """The sentence the owner agrees to when a grant is given: creating, saving, switching on, or
    Allow. `changed` names the providers a save changes what they run (:func:`narrow`).

    Product copy, so it says only what the grant does: the fence stops refusing the action as it is
    now. The other controls on a run (the autonomy ladder, the denylist, the injection screen)
    still apply. A switch-on and an Allow say nothing about what the trigger was allowed before,
    because a grant an edit took away and one never given look the same from here.

    An action that starts an agent ends the sentence with what that agent may do when it runs
    (:func:`what_its_agent_may_do`): only read, change only the files its job names, or change
    files and run commands, and every message it may send; and what holds it to less as things
    stand, a working folder the owner has not trusted (with what trusting it gives) or an agent CLI
    no files to change can be held to. An automation allowed without being told it could only read
    was an automation allowed to do a job it could not do.
    """
    from personalclaw.triggers.legacy_import import IMPORTED_BY

    name = _name(trigger)
    uses = _uses(providers)
    if creating:
        said = f"Creating “{name}” allows it to use {uses} when it runs."
    elif saving and changed and set(providers) <= set(changed):
        said = f"Saving “{name}” changes what {uses} runs, and allows the new version to run."
    elif saving:
        said = (
            f"Saving “{name}” allows it to use {uses} when it runs. "
            "It has not been allowed to until now."
        )
    elif getattr(trigger, "created_by", "") == IMPORTED_BY:
        said = (
            f"“{name}” was brought over from an older version of PersonalClaw and has not been "
            f"allowed to run here. Switching it on allows it to use {uses} when it fires."
        )
    elif getattr(trigger, "enabled", False):
        said = f"Allowing “{name}” lets it use {uses}, as it is now, when it runs."
    else:
        said = f"Switching “{name}” on allows it to use {uses}, as it is now, when it runs."
    reach = what_its_agent_may_do(trigger) or _which_version(trigger)
    return f"{said} {reach}" if reach else said


def _which_version(trigger: Any) -> str:
    """Which version of its workflow a yes to `trigger` lets it run, in the consent's words, or
    ``""`` when its action runs no workflow (or none by that name exists, which its save refuses).
    """
    name = _workflow_of(trigger)
    if not name:
        return ""
    from personalclaw.workflows.automation_version import called, current_now

    now = current_now(name)
    if now is None or not now.spec:
        return ""
    by = _SAVED_BY_WORDS.get(now.saved_by) if now.saved_by != "owner" else ""
    version = f"version {now.version} now, saved by {by}" if by else f"version {now.version} now"
    started = [n for n in called(now.spec) if n != name]
    if not started:
        return (
            f"It runs “{name}” as it is when you allow it ({version}), and a newer version only "
            "once you save one in the workflow's editor."
        )
    return (
        f"It runs “{name}” as it is when you allow it ({version}), and {_quoted(started)}, "
        "which it runs as steps, as they are then too; and a newer version of any of them only "
        "once you save one in its editor."
    )


def _quoted(names: list[str]) -> str:
    """Workflow names as a sentence lists them: “a”, “b” and “c”."""
    quoted = [f"“{n}”" for n in names]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def _action_of(trigger: Any) -> tuple[str, dict[str, Any]]:
    """`trigger`'s action as ``(provider, config)``, from either stored shape (`{"inline": …}` or
    the flat `{provider, config}`): ``("", {})`` when it declares none."""
    workflow = getattr(trigger, "workflow", None)
    action: dict[str, Any] = workflow if isinstance(workflow, dict) else {}
    nested = action.get("inline")
    if isinstance(nested, dict):
        action = nested
    config = action.get("config")
    return str(action.get("provider") or "").strip(), config if isinstance(config, dict) else {}


def what_its_agent_may_do(trigger: Any) -> str:
    """What the agent `trigger`'s action starts may do when it runs, in the Allow's words
    (``automation_posture.AgentRunPolicy.sentence``), or ``""`` when its action starts no agent.
    An app's scheduled job's agent runs at the app's agent tier, whatever its step says, and is
    said so (``app_crons.what_the_job_may_do``)."""
    from personalclaw.apps.app_crons import what_the_job_may_do
    from personalclaw.automation_posture import AGENT_STARTING_PROVIDERS, agent_run_policy

    provider, config = _action_of(trigger)
    if provider not in AGENT_STARTING_PROVIDERS:
        return ""
    return what_the_job_may_do(str(getattr(trigger, "id", "") or "")) or (
        agent_run_policy(provider, config).sentence()
    )


def held_back(trigger: Any) -> dict[str, str] | None:
    """Why the agent `trigger`'s action starts may do less than its step asks, as things stand now
    (``automation_posture.AgentRunPolicy.held_back``), and the working folder the owner would trust
    to give it back (``""`` when no folder holds it back): ``{"why", "folder"}``. None when nothing
    holds it back, or its action starts no agent, or it is an app's scheduled job, whose agent's
    tier is the app's (``app_crons.start_job``). The Triggers page shows it with the trigger."""
    from personalclaw.apps.app_crons import app_of
    from personalclaw.automation_posture import AGENT_STARTING_PROVIDERS, agent_run_policy
    from personalclaw.guardrails.project_trust import resolve_dir

    provider, config = _action_of(trigger)
    if provider not in AGENT_STARTING_PROVIDERS or app_of(str(getattr(trigger, "id", "") or "")):
        return None
    policy = agent_run_policy(provider, config)
    if not policy.held_back:
        return None
    return {"why": policy.held_back, "folder": resolve_dir(policy.untrusted_folder)}


def _only_on_the_page(trigger: Any) -> str:
    """Where a grant is given, said to someone who is not there: the Triggers page's own control
    for the trigger as it is now — Allow on its panel when it is on, its switch when it is off."""
    enabled = getattr(trigger, "enabled", False)
    control = "open it there and choose Allow" if enabled else "switch it on there"
    return f"It can be allowed only on the Triggers page, which asks first: {control}."


def refusal(
    trigger: Any, providers: list[str], *, elsewhere: bool = False, switching_on: bool = False
) -> str:
    """Why `trigger` did not run (or was not switched on), and how its grant is given.

    `elsewhere` is the wording read away from the dashboard — a channel's `cron resume`, the CLI,
    the chat's tools — where the grant cannot be given, so it says where it can. It says where, not
    who: the one reading it is usually the owner, in their own channel or terminal, and "only the
    owner can allow it" told them they were someone else. On the dashboard the owner is told the
    one control that gives it for the trigger as it is now: Allow on its panel when it is on, its
    switch when it is off — both ask first.
    """
    outcome = "so it was not switched on" if switching_on else "so it did not run"
    head = f"“{_name(trigger)}” is not allowed to use {_uses(providers)}, {outcome}."
    if elsewhere:
        return f"{head} {_only_on_the_page(trigger)}"
    if getattr(trigger, "enabled", False):
        return (
            f"{head} Allow it on the Triggers page: open it and choose Allow, and PersonalClaw "
            "asks you first."
        )
    return f"{head} Switch it on from the Triggers page: PersonalClaw asks you to allow it first."


def switched_off(
    trigger: Any, providers: list[str], *, changed: list[str] | tuple[str, ...] = ()
) -> str:
    """What an edit that needed a new grant, saved without the owner's yes, did instead. Read away
    from the dashboard (the chat's `automation_update`), so it says where, too."""
    saved = f"so it was saved switched off. {_only_on_the_page(trigger)}"
    if changed and set(providers) <= set(changed):
        return f"What {_uses(providers)} runs changed, and the change has not been allowed, {saved}"
    return f"It now uses {_uses(providers)}, which it has not been allowed to, {saved}"


#: The actions that start an agent. Their grant is the owner's yes to that agent starting: "allows
#: it to use the “Invoke Agent” action when it runs" is what the create dialog asked.
_STARTS_AN_AGENT: frozenset[str] = frozenset({"invoke-agent", "run-prompt"})


def allows_its_agent(trigger_id: str) -> bool:
    """Whether the trigger ``trigger_id`` names may start the agent its action starts, now,
    without asking the owner again.

    True when its action starts an agent (``invoke-agent``, ``run-prompt``) and the owner's grant
    covers that action as it is: they were asked when they created it, edited it or switched it
    on, and a second question when the agent starts asked the same thing twice. The start only:
    the agent's own calls ask as any agent's do.

    Read when the agent starts, as every grant is (`approval_grants`): an edit that changed the
    action, or a grant taken back since the fire, asks. A lifecycle hook is named
    ``lifecycle:<id>`` (`hooks.LIFECYCLE_TRIGGER_PREFIX`). Anything that cannot be read (no such
    trigger, a store that will not load) is False, so the start asks.
    """
    if not trigger_id:
        return False
    try:
        from personalclaw.config.loader import config_dir
        from personalclaw.hooks import LIFECYCLE_TRIGGER_PREFIX

        home = config_dir()
        if trigger_id.startswith(LIFECYCLE_TRIGGER_PREFIX):
            from personalclaw.hooks import ScriptHookStore

            trigger: Any = ScriptHookStore(config_dir=home).get(
                trigger_id.removeprefix(LIFECYCLE_TRIGGER_PREFIX)
            )
        else:
            from personalclaw.triggers.store import TriggerStore

            row = TriggerStore(base_dir=home).get(trigger_id)
            trigger = row.trigger if row is not None else None
    except Exception:  # noqa: BLE001 - an unreadable trigger allows nothing: the start asks
        logger.warning("could not read trigger %s for its agent's start", trigger_id, exc_info=True)
        return False
    if trigger is None:
        return False
    if _action_of(trigger)[0] not in _STARTS_AN_AGENT:
        return False
    return not missing(trigger)


def give(trigger: Any) -> list[str]:
    """Grant `trigger` what its action runs, in place. Returns what was granted.

    The owner's yes, and only that: the Triggers page's switch, the create dialog and the editor
    call this after their consent question, and the CLI after `--yes`. A row a legacy import
    brought over becomes the owner's here too (`legacy_import.adopt`), since this is the review it
    was waiting for.
    """
    from personalclaw.triggers.legacy_import import adopt
    from personalclaw.triggers.screen import grant_action

    name = _workflow_of(trigger)
    if not name:
        granted = grant_action(trigger)
        adopt(trigger)
        return granted
    from personalclaw.workflows.automation_version import current_now

    now = current_now(name)
    if now is None or not now.spec:
        # A yes to a workflow that is not there would be a yes to whatever is saved under its name
        # next, so it gives nothing: its fire says the workflow is missing.
        adopt(trigger)
        return []
    granted = grant_action(trigger)
    _allow(trigger, now)
    adopt(trigger)
    return granted


def _workflow_of(trigger: Any) -> str:
    """The workflow `trigger`'s own action runs, or ``""`` when its action runs none."""
    provider, config = _action_of(trigger)
    if provider != RUNS_A_WORKFLOW:
        return ""
    return str(config.get("workflow") or "").strip()


def allowed_workflow(trigger: Any) -> "Allowed | None":
    """The version of its workflow `trigger`'s grant allows it to run, or None: its action runs no
    workflow, its grant does not cover that action, or the grant records no version of it."""
    from personalclaw.workflows.automation_version import Allowed

    name = _workflow_of(trigger)
    block = getattr(trigger, "capabilities", None)
    if not name or not isinstance(block, dict):
        return None
    held = block.get("providers")
    if not isinstance(held, (list, tuple)) or RUNS_A_WORKFLOW not in held:
        return None
    allowed = Allowed.from_dict(block.get(_ALLOWED_WORKFLOW))
    return allowed if allowed is not None and allowed.workflow == name else None


def _allow(trigger: Any, now: "Current", *, calls: Any = None) -> None:
    """Record in `trigger`'s grant that it may run `now`, its workflow as it is, and each workflow
    `now` runs as a step as it is too (*calls*, `automation_version.closure`, read here when not
    given), in place. The version history keeps each first (`automation_version.keep`), so the
    versions allowed can still run once the workflows move on."""
    from personalclaw.workflows.automation_version import Allowed, closure_now, keep

    keep(now)
    if calls is None:
        calls = closure_now(now.spec, root=now.name)
    current = getattr(trigger, "capabilities", None)
    block = dict(current) if isinstance(current, dict) else {}
    block[_ALLOWED_WORKFLOW] = Allowed(now.name, now.version, now.digest, calls).to_dict()
    trigger.capabilities = block


def made_by_personalclaw(trigger: Any) -> bool:
    """Whether `trigger` is one of the automations PersonalClaw's own code makes and grants (an
    app's job, a settings-driven singleton, a logged decision's review card): it runs a template
    PersonalClaw or the app ships, as it is. Every other automation's grant came from its owner's
    yes, and runs the version that yes was given for."""
    by = str(getattr(trigger, "created_by", "") or "")
    return by == "system" or by.startswith(("system:", "app:"))


def allowed_for_fire(trigger_id: str, now: "Current") -> tuple["Allowed | None", str]:
    """The version of its workflow, *now*, the automation *trigger_id* may run at this fire:
    ``(allowed, "")``, ``(None, "")`` when the fire runs the workflow as it is, or ``(None, why)``
    when it may run nothing.

    Read from the store as it is at the fire, as every grant is (:func:`allows_its_agent`). As it is
    runs: a run no automation started (no *trigger_id*), one an action of another kind started
    through this one, an automation PersonalClaw's own code made (:func:`made_by_personalclaw`),
    and one no store holds any more — a fire that raced its own deletion, which said so in the log.
    An owner's Allow from before allowed versions were recorded covered every version, and is bound
    here, at its first fire, to the version it runs (:func:`_allow`). A store that cannot be read
    runs nothing."""
    if not trigger_id:
        return None, ""
    from personalclaw.config.loader import config_dir
    from personalclaw.hooks import LIFECYCLE_TRIGGER_PREFIX

    home = config_dir()
    try:
        if trigger_id.startswith(LIFECYCLE_TRIGGER_PREFIX):
            from personalclaw.hooks import ScriptHookStore

            hooks = ScriptHookStore(config_dir=home)
            trigger: Any = hooks.get(trigger_id.removeprefix(LIFECYCLE_TRIGGER_PREFIX))

            def keep() -> None:
                hooks.update(trigger.id, {"capabilities": trigger.capabilities})

        else:
            from personalclaw.triggers.routing import routed
            from personalclaw.triggers.store import TriggerStore

            store = routed(TriggerStore(base_dir=home))
            row = store.get(trigger_id)
            trigger = row.trigger if row is not None else None

            def keep() -> None:
                store.upsert(trigger)

    except Exception:  # noqa: BLE001 - an automation that cannot be read runs nothing
        logger.warning("could not read trigger %s for its workflow's version", trigger_id)
        return None, (
            "the automation that fired could not be read, so it is not known which version of "
            f"“{now.name}” it may run, and it did not run"
        )
    if trigger is None:
        logger.warning("trigger %s is in no store at its fire: %s runs as is", trigger_id, now.name)
        return None, ""
    if _workflow_of(trigger) != now.name:
        return None, ""
    allowed = allowed_workflow(trigger)
    if allowed is not None or made_by_personalclaw(trigger):
        return allowed, ""
    if missing(trigger):
        return None, refusal(trigger, missing(trigger))
    _allow(trigger, now)
    try:
        keep()
    except Exception:  # noqa: BLE001 - this fire runs the version it was bound to either way
        logger.warning("could not record which version of %s trigger %s runs", now.name, trigger_id)
    return allowed_workflow(trigger), ""


def use_current_version(trigger: Any, version: int, steps: dict[str, int] | None = None) -> str:
    """Let `trigger` run its workflow's version *version*, and the workflows its steps start at the
    versions *steps* names: the owner's "Use vN" on its row, after its consent question
    (:func:`use_version_question`). Both must be what its row offered (``use``), as things are now,
    or nothing is changed: a version saved after she looked is not one she was asked about. Done in
    place: ``""``, or why not."""
    name = _workflow_of(trigger)
    if not name:
        return "its action runs no workflow"
    if missing(trigger):
        return "it is not allowed to run yet: allow it first, which asks you"
    from personalclaw.workflows.automation_version import closure_now

    read = _read(trigger)
    now = read[1] if read is not None else None
    if read is None or now is None or not now.spec:
        return f"there is no workflow named “{name}” now"
    if now.version != version:
        return f"“{name}” is version {now.version} now, not {version}: look at it again"
    calls = closure_now(now.spec, root=name)
    offered = _use_of(read[0], _moves(read[0], now, calls), now)
    if offered is None or offered["steps"] != dict(steps or {}):
        return "what its steps would run has changed since you looked: look at it again"
    _allow(trigger, now, calls=calls)
    return ""


def _saved(version: int, saved_by: str) -> str:
    """One version and who saved it, as the consent question says it."""
    words = _SAVED_BY_WORDS.get(saved_by)
    if words:
        return f"v{version} by {words}"
    return f"v{version}, from before PersonalClaw recorded who saved each version"


def _since(since: list[tuple[int, str]]) -> str:
    """Who saved each version since, as the consent question says it."""
    return "; ".join(_saved(version, saver) for version, saver in since) or "nothing recorded"


class _Move(NamedTuple):
    """One version "Use vN" changes: the workflow, the version it runs now (0 for none), the one
    it would run, and who saved each version since."""

    workflow: str
    was: int
    to: int
    since: list[tuple[int, str]]


def _moves(described: dict[str, Any], now: "Current | None", calls: Any = None) -> list[_Move]:
    """What "Use vN" would change on the automation `described` says, its workflow being *now*: its
    own workflow first when its version moves, then each workflow the steps of *now* start whose
    version moves with it (*calls*, `automation_version.closure`, read here when not given). Empty
    for an automation it would change nothing on, or one it does not apply to: not allowed yet, or
    one PersonalClaw's own code made."""
    if described["follows"] or not described["allowed"] or now is None or not now.spec:
        return []
    from personalclaw.workflows.automation_version import closure_now, current_now, saved_since

    name = described["workflow"]
    out: list[_Move] = []
    if described["newer"]:
        since = [(row["version"], row["saved_by"]) for row in described["since"]]
        out.append(_Move(name, described["runs"], described["newer"], since))
    if calls is None:
        calls = closure_now(now.spec, root=name, keeping=False)
    before = {row["workflow"]: row["runs"] for row in described["steps"]}
    for call in calls:
        was = before.get(call.workflow, 0)
        if call.workflow in before and was == call.version:
            continue
        child = current_now(call.workflow)
        since = saved_since(call.workflow, was, child) if child is not None else []
        out.append(_Move(call.workflow, was, call.version, since))
    return out


def _use_of(
    described: dict[str, Any], moves: list[_Move], now: "Current | None"
) -> dict[str, Any] | None:
    """What the row's "Use vN" sends back (``use``): the workflow's version now, and the version of
    each workflow its steps start that moves with it; None when it would change nothing."""
    if not moves or now is None:
        return None
    name = described["workflow"]
    return {
        "version": now.version,
        "steps": {m.workflow: m.to for m in moves if m.workflow != name},
    }


def use_version_question(trigger: Any) -> Question | None:
    """What "Use vN" on `trigger`'s row asks the owner (:class:`Question`, its ``providers`` the
    workflow action), or None when it runs every workflow it runs, its own and each its steps
    start, as it is now. It names each version that would change, and who saved each since."""
    read = _read(trigger)
    if read is None:
        return None
    described, now = read
    moves = _moves(described, now)
    if not moves:
        return None
    name, who = described["workflow"], _name(trigger)
    own = [m for m in moves if m.workflow == name]
    steps = [m for m in moves if m.workflow != name]
    said: list[str] = []
    if own:
        runs = own[0].was
        if runs:
            head = f"“{who}” runs version {runs} of “{name}”."
        else:
            runs = described["allowed"]
            head = (
                f"“{who}” was allowed to run version {runs} of “{name}”, which is no longer "
                "kept here, so it runs nothing."
            )
        said.append(
            f"{head} Allowing this lets it run version {own[0].to} instead, when it runs, "
            f"unattended included. Saved since version {runs}: {_since(own[0].since)}."
        )
    else:
        said.append(
            f"“{who}” runs version {described['runs']} of “{name}”, its newest. Allowing this lets "
            "the workflows it runs as steps run their newest versions instead, when it runs, "
            "unattended included."
        )
    for move in steps:
        if move.was:
            said.append(
                f"Its steps start “{move.workflow}” at version {move.to} instead of version "
                f"{move.was}. Saved since version {move.was}: {_since(move.since)}."
            )
        else:
            said.append(
                f"Its steps start “{move.workflow}” at version {move.to}, which they do not start "
                f"now. Saved: {_since(move.since)}."
            )
    if own:
        title = f"Run version {own[0].to} of “{name}”?"
    elif len(steps) == 1:
        title = f"Run the newest version of “{steps[0].workflow}”?"
    else:
        title = f"Run the newest versions of what “{name}” runs?"
    return Question([RUNS_A_WORKFLOW], " ".join(said), title)


def use_version_change(trigger: Any) -> str:
    """What "Use vN" changes on `trigger`, from and to, one line per workflow (``"v4 → v5"`` for
    its own, ``"“part” v1 → v2"`` for one its steps start), for its consent dialog."""
    read = _read(trigger)
    if read is None:
        return ""
    described, now = read
    lines = []
    for move in _moves(described, now):
        if move.workflow == described["workflow"]:
            lines.append(f"v{move.was or described['allowed']} → v{move.to}")
        elif move.was:
            lines.append(f"“{move.workflow}” v{move.was} → v{move.to}")
        else:
            lines.append(f"“{move.workflow}” v{move.to}")
    return "\n".join(lines)


def workflow_version(trigger: Any) -> dict[str, Any] | None:
    """What `trigger`'s row says about the version of its workflow it runs, or None when its action
    runs no workflow. The Triggers page shows it with the trigger, and the workflow's page with its
    other automations.

    * ``workflow``, and ``runs``: the version a fire runs now (0 when it would run none), with
      ``saved_by``, who saved that version;
    * ``allowed``: the version its owner's yes was given for, 0 when it records none — not allowed
      yet, or allowed before versions were recorded, which its next fire binds to the version then;
    * ``follows``: whether it runs the workflow as it is (:func:`made_by_personalclaw`);
    * ``newer``: the workflow's version now, when that is not what runs (0 otherwise), with
      ``since``, who saved each version after the one it runs;
    * ``problem``: why a fire would run nothing (``""`` when it would run);
    * ``steps``: each workflow its steps start, at every depth, as the same fields say it
      (``workflow``, ``via`` — the workflow whose step starts it —, ``runs``, ``saved_by``,
      ``newer``, ``since``, ``problem``); ``[]`` when a fire would run nothing;
    * ``use``: what its "Use vN" sends (``{version, steps}``, :func:`use_current_version`), or None
      when it runs every workflow it runs as it is now."""
    read = _read(trigger)
    if read is None:
        return None
    described, now = read
    try:
        described["use"] = _use_of(described, _moves(described, now), now)
    except Exception:  # noqa: BLE001 - a row must render when what it starts cannot be read
        logger.debug("could not read what Use vN changes on trigger %s", getattr(trigger, "id", ""))
        described["use"] = None
    return described


def _read(trigger: Any) -> "tuple[dict[str, Any], Current | None] | None":
    """`trigger`'s row on the version of its workflow it runs (:func:`workflow_version`, less
    ``use``), and the workflow as it is now; None when its action runs no workflow."""
    name = _workflow_of(trigger)
    if not name:
        return None
    from personalclaw.workflows.automation_version import (
        Allowed,
        bound_of,
        current_now,
        runs,
        saved_since,
        steps_now,
    )

    try:
        now = current_now(name)
    except Exception:  # noqa: BLE001 - a row must render when its workflow cannot be read
        logger.debug("could not read workflow %s for trigger %s", name, getattr(trigger, "id", ""))
        now = None
    described: dict[str, Any] = {
        "workflow": name,
        "runs": 0,
        "saved_by": "",
        "allowed": 0,
        "follows": made_by_personalclaw(trigger),
        "newer": 0,
        "since": [],
        "problem": "",
        "steps": [],
    }
    if now is None or not now.spec:
        described["problem"] = f"there is no workflow named “{name}” that can run"
        return described, now
    pinned = allowed_workflow(trigger)
    allowed = pinned
    if pinned is not None:
        described["allowed"] = pinned.version
    elif not described["follows"]:
        # Not allowed yet, or allowed before versions were recorded: either way, the yes it is
        # given (or its next fire) binds it to the workflow as it is now.
        allowed = Allowed(name, now.version, now.digest)
    fire = runs(allowed, now)
    described["runs"] = fire.version if fire.spec is not None else 0
    described["saved_by"] = fire.saved_by
    described["problem"] = fire.problem
    if fire.newer is not None:
        described["newer"] = fire.newer.version
        described["since"] = [
            {"version": number, "saved_by": saver}
            for number, saver in saved_since(name, fire.version, fire.newer)
        ]
    if fire.spec is not None:
        try:
            found = steps_now(name, fire, bound_of(pinned) if pinned is not None else None)
        except Exception:  # noqa: BLE001 - see above: the row renders without its steps
            logger.debug("could not read the steps of workflow %s for trigger %s", name, trigger)
            found = []
        described["steps"] = [_step_row(step) for step in found]
    return described, now


def _step_row(step: Any) -> dict[str, Any]:
    """One workflow an automation's steps start, as its row says it (:func:`workflow_version`)."""
    from personalclaw.workflows.automation_version import saved_since

    ran = step.runs
    row: dict[str, Any] = {
        "workflow": step.workflow,
        "via": step.via,
        "runs": ran.version if ran.spec is not None else 0,
        "saved_by": ran.saved_by,
        "newer": 0,
        "since": [],
        "problem": ran.problem,
    }
    if ran.newer is not None:
        row["newer"] = ran.newer.version
        if ran.version:
            row["since"] = [
                {"version": number, "saved_by": saver}
                for number, saver in saved_since(step.workflow, ran.version, ran.newer)
            ]
    return row


def running(name: str) -> list[dict[str, Any]]:
    """The automations that run the workflow *name*, as the workflow's page lists them: its stored
    triggers (an app's served rows included) and lifecycle hooks whose own action runs it, and those
    whose workflow runs it as a step (``via``, the workflow whose step starts it; ``""`` for its
    own action), each with the version of its workflow it runs (:func:`workflow_version`). ``id``
    is the one the Triggers page opens it by, ``name`` masked as that page shows it. A store that
    cannot be read lists nothing.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.hooks import LIFECYCLE_TRIGGER_PREFIX, ScriptHookStore
    from personalclaw.triggers.delivery import status_url
    from personalclaw.triggers.routing import routed
    from personalclaw.triggers.store import TriggerStore

    home = config_dir()
    found: list[tuple[str, Any]] = []
    try:
        found.extend(
            (str(t.id), t)
            for t in routed(TriggerStore(base_dir=home)).list_triggers(include_broken=False)
        )
    except Exception:  # noqa: BLE001 - an unreadable store costs its rows, not the page
        logger.warning("could not read the triggers that run workflow %s", name)
    try:
        found.extend(
            (f"{LIFECYCLE_TRIGGER_PREFIX}{hook.id}", hook)
            for hook in ScriptHookStore(config_dir=home).list_all()
        )
    except Exception:  # noqa: BLE001 - see above
        logger.warning("could not read the lifecycle triggers that run workflow %s", name)
    out: list[dict[str, Any]] = []
    for ident, trigger in found:
        own = _workflow_of(trigger)
        if not own:
            continue
        described = workflow_version(trigger)
        if own == name:
            via = ""
        else:
            step = next(
                (row for row in (described or {}).get("steps", []) if row["workflow"] == name), None
            )
            if step is None:
                continue
            via = step["via"]
        out.append(
            {
                "id": ident,
                "name": _name(trigger),
                "enabled": bool(getattr(trigger, "enabled", False)),
                "needs_grant": labels(trigger),
                "workflow_version": described,
                "via": via,
                "status_url": status_url(trigger_id=ident),
            }
        )
    return out
