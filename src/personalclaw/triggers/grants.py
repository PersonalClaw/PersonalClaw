"""What a trigger is allowed to run, who gives it that, and the words for asking and refusing.

`triggers.screen` is the fence: `ungranted_providers` names the providers a trigger's action runs
that its frozen block does not permit, and `grant_action` writes them into the block. This module
is what surrounds the fence, so a refusal, a consent question and the Triggers page describe one
grant the same way.

🔴 THE RULE. Nothing runs an action without the grant it needs; a grant is the owner's yes to the
action as it stood when they gave it; and nothing but that yes gives one.

* **Both dispatches check.** The attended one
  (`dashboard.handlers.trigger_runs._dispatch_store_action` — Run now, the restart review's Run
  now, a view refresh, a webhook fire) and the unattended one
  (`gateway._fire_store_trigger` — clock, event, file, web_watch, chained). Clock and event fires
  also meet the fence in `service.admit_fire`; file, web_watch and chained fires reach the dispatch
  without it, and before this ran whatever they held. A lifecycle trigger's fire checks it too
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
* **A restart gives nothing.** The capability backfill that granted every ungranted row whatever
  it ran, on every start, is gone: by the time it ran, the edit it rewarded was nobody's decision.

The system's own triggers are granted by the code that makes them, because each runs an action
PersonalClaw fixes and each is switched on by the owner: an app's crons (its install consent lists
them), the settings-driven singletons (`system:*`), a research report's schedule, the triage digest,
the Self-QA watch and a logged decision's review card. None of them takes an action from a caller.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


class Question(NamedTuple):
    """What saving a trigger needs the owner to allow, as the owner is asked it: the providers,
    the sentence they agree to (:func:`consent`) and the dialog's heading (:func:`title`)."""

    providers: list[str]
    sentence: str
    title: str


def missing(trigger: Any) -> list[str]:
    """The providers `trigger`'s action runs that it is not allowed to. `[]` when it may run.

    A provider nothing dispatches is left out: a run of it fails on the unknown name first (both
    dispatches resolve the provider before they check the grant), and that name is what the owner
    has to fix, since allowing it would change nothing. The fence itself still refuses it
    (`screen.ungranted_providers` fails closed), and it needs a grant the moment an app providing
    it is installed.
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
    files and run commands, and every message it may send. An automation allowed without being
    told it could only read was an automation allowed to do a job it could not do.
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
    reach = what_its_agent_may_do(trigger)
    return f"{said} {reach}" if reach else said


def what_its_agent_may_do(trigger: Any) -> str:
    """What the agent `trigger`'s action starts may do when it runs, in the Allow's words
    (``automation_posture.AgentRunPolicy.sentence``), or ``""`` when its action starts no agent."""
    from personalclaw.automation_posture import AGENT_STARTING_PROVIDERS, agent_run_policy

    workflow = getattr(trigger, "workflow", None)
    action: dict[str, Any] = workflow if isinstance(workflow, dict) else {}
    nested = action.get("inline")
    if isinstance(nested, dict):
        action = nested
    provider = str(action.get("provider") or "")
    if provider not in AGENT_STARTING_PROVIDERS:
        return ""
    config = action.get("config")
    return agent_run_policy(provider, config if isinstance(config, dict) else {}).sentence()


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
    workflow = getattr(trigger, "workflow", None)
    action: dict[str, Any] = workflow if isinstance(workflow, dict) else {}
    nested = action.get("inline")
    if isinstance(nested, dict):
        action = nested
    if str(action.get("provider") or "").strip() not in _STARTS_AN_AGENT:
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

    granted = grant_action(trigger)
    adopt(trigger)
    return granted
