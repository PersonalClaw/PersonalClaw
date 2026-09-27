"""What a trigger is allowed to run, who gives it that, and the words for asking and refusing.

`triggers.screen` is the fence: `ungranted_providers` names the providers a trigger's action runs
that its frozen block does not permit, and `grant_action` writes them into the block. This module
is what surrounds the fence, so a refusal, a consent question and the Triggers page describe one
grant the same way.

🔴 THE RULE. Nothing runs an action without the grant it needs, and nothing that runs unattended
gives one.

* **Both dispatches check.** The attended one (`dashboard.handlers.triggers._dispatch_store_action`
  — Run now, the restart review's Run now, a view refresh, a webhook fire) and the unattended one
  (`gateway._fire_store_trigger` — clock, event, file, web_watch, chained). Clock and event fires
  also meet the fence in `service.admit_fire`; file, web_watch and chained fires reach the dispatch
  without it, and before this ran whatever they held. A refusal says which grant is missing and how
  the owner gives it (:func:`refusal`).
* **Only the owner gives one, by saying yes** (:func:`give`): the Triggers page's switch (its Allow
  is the same switch sent on again) and the schedule editor, each after its consent dialog
  (:func:`consent`). An edit from anywhere else that needs a new grant — the chat's
  `automation_update`, the CLI — is saved with the trigger switched off (`tools.update`), and the
  chat cannot switch one on.
* **A restart gives nothing.** The capability backfill that granted every ungranted row whatever
  it ran, on every start, is gone: by the time it ran, the edit it rewarded was nobody's decision.
"""

from __future__ import annotations

from typing import Any


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
    return str(getattr(trigger, "name", "") or getattr(trigger, "id", "") or "this trigger")


def consent(trigger: Any, providers: list[str], *, saving: bool = False) -> str:
    """The sentence the owner agrees to when a grant is given: switching on, Allow, or saving.

    Product copy, so it says only what the grant does: the fence stops refusing the action. The
    other controls on a run (the autonomy ladder, the denylist, the injection screen) still apply.
    """
    from personalclaw.triggers.legacy_import import IMPORTED_BY

    name = _name(trigger)
    uses = _uses(providers)
    until_now = "It has not been allowed to until now."
    if saving:
        return f"Saving “{name}” allows it to use {uses} when it runs. {until_now}"
    if getattr(trigger, "created_by", "") == IMPORTED_BY:
        return (
            f"“{name}” was brought over from an older version of PersonalClaw and has not been "
            f"allowed to run here. Switching it on allows it to use {uses} when it fires."
        )
    if getattr(trigger, "enabled", False):
        return f"Allowing “{name}” lets it use {uses} when it runs. {until_now}"
    return f"Switching “{name}” on allows it to use {uses} when it runs. {until_now}"


def refusal(
    trigger: Any, providers: list[str], *, agent: bool = False, switching_on: bool = False
) -> str:
    """Why `trigger` did not run (or was not switched on), and how its grant is given.

    `agent` is the chat's wording: the one reading it cannot give the grant, so it is told who can.
    Otherwise the owner is told the one control that gives it for the trigger as it is now: Allow
    on its panel when it is on, its switch when it is off — both ask first.
    """
    outcome = "so it was not switched on" if switching_on else "so it did not run"
    head = f"“{_name(trigger)}” is not allowed to use {_uses(providers)}, {outcome}."
    if agent:
        return f"{head} Only the owner can allow it, on the Triggers page, which asks them first."
    if getattr(trigger, "enabled", False):
        return (
            f"{head} Allow it on the Triggers page: open it and choose Allow, and PersonalClaw "
            "asks you first."
        )
    return f"{head} Switch it on from the Triggers page: PersonalClaw asks you to allow it first."


def switched_off(trigger: Any, providers: list[str]) -> str:
    """What an edit that needed a new grant, saved without the owner's yes, did instead."""
    return (
        f"It now uses {_uses(providers)}, which it has not been allowed to, so it was saved "
        "switched off. The owner allows it by switching it on from the Triggers page, which asks "
        "them first."
    )


def give(trigger: Any) -> list[str]:
    """Grant `trigger` what its action runs, in place. Returns what was granted.

    The owner's yes, and only that: the Triggers page's switch and the schedule editor call this
    after their consent dialog. A row a legacy import brought over becomes the owner's here too
    (`legacy_import.adopt`), since this is the review it was waiting for.
    """
    from personalclaw.triggers.legacy_import import adopt
    from personalclaw.triggers.screen import grant_action

    granted = grant_action(trigger)
    adopt(trigger)
    return granted
