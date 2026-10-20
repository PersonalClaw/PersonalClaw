"""What the Triggers page asks you before a write gives a trigger what its action runs, and what
holds your yes to the question it answered.

Creating a trigger whose action runs a provider it must be granted, saving an edit that re-points
or rewrites its action, switching it on, and an "Auto-approve tools" switch that loosens whether
its agent asks you each answer ``400 confirmation_required`` with one question
(`http_errors.consent_required`), which the page's ``withSecurityConsent`` turns into the consent
dialog; your yes is the same write again with ``confirm: true``. Each refusal and each grant is
written to the security audit (:func:`audit_grant`).

A question about an action that runs a workflow carries the version it showed (``shown``,
`triggers.grants.Question.shown`), and your yes is held to it (`triggers.grants.allowing`): when
the workflow, or one it runs as a step, moved after you were asked, the write is answered ``409
stale_write`` with the question as it is now (`http_errors.asked_again`), and nothing is written.

The doors are in `triggers`, which this reads the trigger stores from when called, as the other
trigger modules do.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aiohttp import web

from personalclaw.config.edit_spec import LOOSEN_TITLE, LooseningAsk
from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import asked_again, consent_required, json_error
from personalclaw.request_validation import bool_field


def audit_grant(caller: str, outcome: str, resources: str) -> None:
    """The security-audit row for one grant decision: asked and refused, or given."""
    from personalclaw.dashboard.handlers.triggers import _sel

    _sel().log_api_access(
        caller=caller,
        operation="trigger.grant",
        outcome=outcome,
        source="dashboard",
        resources=resources,
    )


def _unconsented_loosening(
    request: web.Request, body: dict, *, where: str, stored: dict[str, Any], saving: Any
) -> tuple[str, LooseningAsk] | None:
    """``(field, what the owner is asked)`` when the action *saving* — the write's action as it
    will be saved — loosens whether the trigger's agent asks you (an ``approval_mode: "auto"``, a
    ``capability: "mutating"`` write grant) over the *stored* action (``{provider, config}``,
    ``{}`` for a new trigger) and *body* carries no ``confirm: true``; ``None`` otherwise. The
    refusal is written to the security audit; the caller answers ``consent_required``.

    The owner's half of the rule; an app cannot define a trigger at all
    (``apps/permissions.ROUTE_AUTHZ``). The Schedule form's "Auto-approve tools" switch is the
    common case, and the SPA asks in the sentence this carries (``withSecurityConsent``).
    """
    from personalclaw.automation_posture import unconsented_step_loosening
    from personalclaw.dashboard.handlers.triggers import _sel

    if not isinstance(saving, dict):
        return None
    raw = stored.get("config")
    stored_config: dict[str, Any] = raw if isinstance(raw, dict) else {}
    new = saving.get("config")
    loosened = unconsented_step_loosening(
        where,
        current=stored_config,
        new=new if isinstance(new, dict) else {},
        body=body,
        provider=str(saving.get("provider") or stored.get("provider") or ""),
    )
    if loosened is None:
        return None
    field, _loosening = loosened
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="trigger.write",
        outcome="denied",
        source="dashboard",
        resources=f"{field}: loosening without confirm",
    )
    return loosened


def _apply_hook_action(hook: Any, action: dict) -> None:
    """Put *action* on *hook* the way `triggers._update_lifecycle` does: the provider when one is
    named, the config when one is sent."""
    if action.get("provider"):
        hook.provider = str(action["provider"])
    if "config" in action:
        hook.provider_config = dict(action.get("config") or {})


def _grant_for_save(
    state: DashboardState, body: dict, *, kind: str, raw: str, saving: Any
) -> tuple[Any, Any]:
    """The question (`triggers.grants.Question`) saving *body* needs the owner to answer, else
    ``None`` (`triggers.grants.question`), and the trigger as the save would store it, which her
    yes is held to (`grants.allowing`). *saving* is its action as the save stores it
    (`triggers._saving_action`), ``None`` when it sends none.

    The editor is where the owner re-points an action or rewrites what it runs, so it is where they
    are asked: an edit that saved `bash` into a trigger allowed only `notify`, or a new command into
    a trigger allowed to run the old one, used to save with nothing asked. With the owner's yes the
    save grants it (`tools.update`), so Run now works straight away.
    """
    import copy

    from personalclaw.dashboard.handlers.triggers import _LIFECYCLE, _hook_store, _trigger_store
    from personalclaw.triggers import grants

    if kind == _LIFECYCLE:
        # A lifecycle save may also send `enabled: true`, which is the toggle's switch-on — or its
        # Allow, when the trigger is on already — and is asked the toggle's question. Not asking it
        # of a trigger that is on would leave `triggers._update_lifecycle` to switch it off, the
        # opposite of what the save asked for.
        hook = _hook_store(state).get(raw)
        if hook is None:
            return None, None
        candidate = copy.copy(hook)
        if isinstance(saving, dict):
            _apply_hook_action(candidate, saving)
            return grants.question(candidate, before=hook), candidate
        need = grants.missing(candidate) if bool_field(body, "enabled", default=False) else []
        if not need:
            return None, candidate
        return grants.asking(candidate, need), candidate
    if not isinstance(saving, dict):
        return None, None
    row = _trigger_store().get(raw)
    if row is None:
        return None, None
    # The action the save stores (`tools.update` puts the edit over the stored one by the same
    # rule), so the question is about the row the save would store.
    candidate = copy.copy(row.trigger)
    candidate.workflow = {"inline": saving}
    return grants.question(candidate, before=row.trigger), candidate


def _grant_for_create(body: dict, *, trigger_type: str) -> tuple[Any, Any]:
    """The question (`triggers.grants.Question`) creating *body*'s trigger needs the owner to answer
    for its action, else ``None``, and the trigger as the create would make it, which her yes is
    held to (`grants.allowing`). The create dialog asks it with the rest, so creating one stays a
    single step."""
    from personalclaw.dashboard.handlers.triggers import (
        _EVENT,
        _LIFECYCLE,
        _RUN_COMPLETED,
        _SCHEDULE,
    )
    from personalclaw.hooks import ScriptHook
    from personalclaw.triggers import grants
    from personalclaw.triggers.models import Trigger

    action = body.get("action")
    if not isinstance(action, dict):
        return None, None
    name = str(body.get("name") or "").strip()
    if trigger_type == _LIFECYCLE:
        candidate: Any = ScriptHook(name=name)
        _apply_hook_action(candidate, action)
    elif trigger_type in (_SCHEDULE, _EVENT, _RUN_COMPLETED):
        kind = "clock" if trigger_type == _SCHEDULE else trigger_type
        candidate = Trigger(id="", name=name, kind=kind, workflow={"inline": action})
    else:
        return None, None
    return grants.question(candidate), candidate


def held_to_what_was_shown(
    candidate: Any, body: dict, *, ask_again: Callable[[str], web.Response | None]
) -> tuple[web.Response | None, Any]:
    """What the owner's yes in *body* allows of the workflow *candidate*'s action runs, held to
    what her question showed of it (``shown``, `triggers.grants.allowing`): ``(None, allowing)``,
    ``allowing`` None for an action that runs no workflow. When what it showed has moved since,
    ``(ask_again(why), None)``, the door's question asked again: the door answers that, and
    writes nothing."""
    from personalclaw.triggers import grants

    if candidate is None:
        return None, None
    try:
        return None, grants.allowing(candidate, body.get("shown"))
    except grants.AskAgain as again:
        return ask_again(again.why) or _nothing_changed(again.why), None


def _nothing_changed(why: str) -> web.Response:
    """The ``409 stale_write`` for a yes whose question showed what is not there now (*why*), when
    there is no question left to ask again."""
    return json_error(
        "stale_write",
        message=f"Nothing was changed: {why} Look at it again before you allow it.",
        status=409,
    )


#: The heading of the one question a write asks when its action needs a grant AND it loosens
#: whether the action's agent asks you: both sentences are in it, so the heading names both.
_GRANT_AND_LOOSEN_TITLE = "Allow what it runs, and loosen a security setting?"


def _asked(
    asks: list[tuple[str, str, str]],
    loosening: LooseningAsk | None = None,
    *,
    shown: dict[str, Any] | None = None,
    moved: str = "",
) -> web.Response | None:
    """One ``confirmation_required`` for everything a write needs the owner's yes for, or None.

    *asks* holds ``(field, sentence, title)`` per question — the grant for what the action runs,
    a loosened posture — so a single Allow is never consent to a sentence the dialog did not show,
    and its heading names what the owner is agreeing to: the question's own title when there is
    one, both halves when there are two. *loosening* is the posture question's own, when it is one
    of them: the dialog says what it changes from and to after the sentences, the last of which is
    its own. *shown* is what the grant's question showed of the workflow its action runs
    (`grants.Question.shown`), which her yes is held to.

    *moved* is what moved since the question an earlier yes answered (`grants.AskAgain`): the
    question is asked again, as the ``409 stale_write`` that says nothing was changed
    (`http_errors.asked_again`).
    """
    if not asks:
        return _nothing_changed(moved) if moved else None
    title = asks[0][2] if len(asks) == 1 else _GRANT_AND_LOOSEN_TITLE
    sentence = " ".join(sentence for _field, sentence, _title in asks)
    change = loosening.change if loosening else ""
    caution = loosening.caution if loosening else ""
    if moved:
        return asked_again(
            asks[0][0],
            moved,
            sentence,
            title=title,
            change=change,
            caution=caution,
            shown=shown,
        )
    return consent_required(
        asks[0][0], sentence, title=title, change=change, caution=caution, shown=shown
    )


def creation_consent(
    request: web.Request, body: dict, *, trigger_type: str, moved: str = ""
) -> web.Response | None:
    """The one question creating *body*'s trigger asks the owner, or None when it needs no yes.

    Asked by each create path after its own validation and before it writes, so the owner is never
    asked about a trigger the next line would refuse: the grant its action needs
    (`_grant_for_create`) and a loosened posture for its agent, in one ``confirmation_required``.
    The consent names the trigger it is about; a name that is not a string only labels it "new"
    here, and refusing that is the create path's job.

    *moved* is what moved since the question her yes answered showed it (`grants.AskAgain`): the
    whole question is asked again, as a ``409 stale_write`` that opens with it, and nothing is
    created (`creation_allowing`).
    """
    from personalclaw.safety_flags import confirm_granted

    name = body.get("name")
    label = name if isinstance(name, str) and name else "new"
    grant, _candidate = _grant_for_create(body, trigger_type=trigger_type)
    field = f"triggers.{label}.capabilities"
    # Asked again, it is asked as if no yes had come: that yes was to another question.
    asking = {**body, "confirm": False} if moved else body
    asks: list[tuple[str, str, str]] = []
    if grant is not None and not confirm_granted(asking):
        caller = request.get("user", "dashboard")
        why = "what it was asked about changed" if moved else "creating without confirm"
        audit_grant(caller, "denied", f"{field}: {why}")
        asks.append((field, grant.sentence, grant.title))
    loosened = _unconsented_loosening(
        request, asking, where=f"triggers.{label}.action", stored={}, saving=body.get("action")
    )
    if loosened is not None:
        asks.append((loosened[0], loosened[1].consent, LOOSEN_TITLE))
    return _asked(
        asks,
        loosened[1] if loosened else None,
        shown=grant.shown if grant is not None else None,
        moved=moved,
    )


def creation_allowing(
    request: web.Request, body: dict, *, trigger_type: str
) -> tuple[web.Response | None, Any]:
    """What the owner's yes to creating *body*'s trigger allows of the workflow its action runs,
    held to what the create dialog's question showed (`held_to_what_was_shown`), before anything
    is created: ``(None, allowing)``, or the question asked again and nothing created."""
    from personalclaw.safety_flags import confirm_granted

    if not confirm_granted(body):
        return None, None
    _grant, candidate = _grant_for_create(body, trigger_type=trigger_type)
    return held_to_what_was_shown(
        candidate,
        body,
        ask_again=lambda why: creation_consent(request, body, trigger_type=trigger_type, moved=why),
    )


def audit_created_grant(request: web.Request, trigger_id: str, granted: Any) -> None:
    """The security-audit row for the grant a create gave with the owner's yes, if it gave one."""
    if isinstance(granted, (list, tuple)) and granted:
        audit_grant(
            request.get("user", "dashboard"),
            "success",
            f"trigger:{trigger_id}: {', '.join(str(p) for p in granted)}",
        )


def save_consent(
    request: web.Request,
    state: DashboardState,
    body: dict,
    *,
    kind: str,
    raw: str,
    saving: Any,
    moved: str = "",
) -> tuple[web.Response | None, Any, Any]:
    """The one question saving *body* asks the owner (None when it needs no yes, or carries it),
    the grant question in it (`_grant_for_save`) and the trigger as the save would store it.
    *moved* is what moved since the question an earlier yes answered (`grants.AskAgain`): the
    whole question is asked again, as `creation_consent` asks it."""
    from personalclaw.dashboard.handlers.triggers import _stored_action
    from personalclaw.safety_flags import confirm_granted

    caller = request.get("user", "dashboard")
    grant, candidate = _grant_for_save(state, body, kind=kind, raw=raw, saving=saving)
    grant_field = f"triggers.{request.match_info['id']}.capabilities"
    # Asked again, it is asked as if no yes had come: that yes was to another question.
    asking = {**body, "confirm": False} if moved else body
    asks: list[tuple[str, str, str]] = []
    if grant is not None and not confirm_granted(asking):
        why = "what it was asked about changed" if moved else "saving without confirm"
        audit_grant(caller, "denied", f"{grant_field}: {why}")
        asks.append((grant_field, grant.sentence, grant.title))
    loosened = _unconsented_loosening(
        request,
        asking,
        where=f"triggers.{request.match_info['id']}.action",
        stored=_stored_action(state, kind, raw),
        saving=saving,
    )
    if loosened is not None:
        asks.append((loosened[0], loosened[1].consent, LOOSEN_TITLE))
    asked = _asked(
        asks,
        loosened[1] if loosened else None,
        shown=grant.shown if grant is not None else None,
        moved=moved,
    )
    return asked, grant, candidate


def given(hook: Any, body: dict, allowing: Any) -> bool:
    """Whether the owner's yes in *body* gave *hook* what its action runs (`grants.give`): False
    with no yes, and for a yes that names no version of the workflow its action runs, which allows
    none of it (`grants.AskAgain`)."""
    from personalclaw.safety_flags import confirm_granted
    from personalclaw.triggers import grants

    if not confirm_granted(body):
        return False
    try:
        grants.give(hook, allowing=allowing)
    except grants.AskAgain:
        return False
    return True


def switch_on_grant(
    request: web.Request,
    body: Any,
    trigger: Any,
    *,
    persist: Callable[[], Any],
    broken: bool = False,
) -> web.Response | None:
    """Give a trigger what switching it on needs, asking the owner first. None when it may go on.

    A trigger whose action runs a write-capable provider its frozen block does not permit — a row a
    legacy import brought over (`triggers.legacy_import`), an edit saved without the owner's yes, a
    row the chat made, a lifecycle trigger made before hooks carried a grant — is refused by every
    dispatch (`triggers.grants`). Switching it on is the moment to ask, and so is Allow on a trigger
    that is already on, which the panel sends here as ``enabled: true``: without ``confirm: true``
    this answers ``400 confirmation_required`` in the gateway's own words, which the page's
    ``withSecurityConsent`` turns into the consent dialog; with it, the providers are granted and an
    imported row becomes the owner's (`grants.give`), *persist* stores that, and both the refusal
    and the grant are written to the security audit. An imported nudge needs no grant, only the
    owner's switch, so it is adopted without a question. A *broken* row (parse errors) is left to
    `set_paused`, which refuses it with the reason rather than asking about a trigger that cannot
    run anyway.

    The question about an action that runs a workflow carries the version it named (``shown``),
    and the yes is held to it (`grants.allowing`): a yes whose workflow, or a workflow it runs as a
    step, moved after the question is answered ``409 stale_write`` with the question as it is now,
    and nothing is granted or switched on.
    """
    from personalclaw.safety_flags import confirm_granted
    from personalclaw.triggers import grants, legacy_import

    if broken:
        return None
    missing = grants.missing(trigger)
    if not missing and not legacy_import.needs_review(trigger):
        return None
    field = f"triggers.{request.match_info['id']}.capabilities"
    caller = request.get("user", "dashboard")
    if missing and not confirm_granted(body):
        audit_grant(caller, "denied", f"{field}: switching on without confirm")
        asked = grants.asking(trigger, missing)
        return consent_required(field, asked.sentence, title=asked.title, shown=asked.shown)

    def ask_again(why: str) -> web.Response:
        audit_grant(caller, "denied", f"{field}: what it was asked about changed")
        asked = grants.asking(trigger, missing)
        return asked_again(field, why, asked.sentence, title=asked.title, shown=asked.shown)

    # Her yes is to the version of a workflow its question showed: one that moved since is asked
    # again, and nothing is granted or switched on.
    again, allowed = held_to_what_was_shown(trigger if missing else None, body, ask_again=ask_again)
    if again is not None:
        return again
    granted = grants.give(trigger, allowing=allowed)
    persist()
    if granted:
        audit_grant(caller, "success", f"trigger:{trigger.id}: {', '.join(granted)}")
    return None
