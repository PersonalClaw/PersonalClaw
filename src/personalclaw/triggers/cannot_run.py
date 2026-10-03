"""An automation that cannot run its action: why, in its owner's words, and what is kept of it.

A fire can reach its dispatch and find nothing it can run: no app running here provides its action,
a ``{{secret:…}}`` its action uses does not resolve (`triggers.secrets`), or the trigger names no
action at all. Both dispatches refuse it before anything runs — `gateway._fire_store_trigger` for a
fire (clock, event, file, web watch, chained) and `dashboard.handlers.trigger_runs.
_dispatch_store_action` for a run by hand or from outside (Run now, a webhook's fire, a view's
refresh) — and :func:`refuse` keeps the refusal where its owner looks, as the other refusals on
those paths are kept. It used to be a warning in the log and a bare return: the automation looked
healthy on the Triggers page and never ran.

* Its row in the run history reads ``refused``, saying why (`run_record.record_refusal`).
* Its last run, which the Triggers page shows, is that refusal, with the same sentence.
* Its owner hears of it once, on the automation's failure route (`delivery.report_run`: the Inbox,
  unless they routed its failures elsewhere). A refusal for the same reason at a later fire is
  recorded and says nothing new, so nothing more is sent until a run gets through or a run is
  refused for another reason (a changed action, a different secret).

Each sentence names what is missing — the app and its action, or the secret by its name — and never
a value, then what to do. It does not say that the run did not happen: every surface that shows it
does (the history's `refused`, the notice's "<name> did not run", the chat's "did not run:").

The app is known when it registered the action in this process
(`action_providers.registry.action_origin`), which an enable does; for an action no app registered
here (a gateway started since the app went, or one whose apps have not started) the sentence says
what is true then: no app running here provides it.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Why a trigger that names no action did not run.
NO_ACTION = "It has no action. Choose what it runs on the Triggers page, or delete it."


def missing_action(provider: str) -> str:
    """Why a run whose action *provider* nothing here provides did not run.

    Names the app the action came from and what became of it — removed, failed to start, switched
    off, or running without that action — when the app registered it in this process; otherwise
    that no app running here provides it, which is true whatever the reason. Never raises: a lookup
    that fails says the second.
    """
    try:
        sentence = _from_its_app(provider)
    except Exception:  # noqa: BLE001 - the sentence that is always true stands in
        logger.debug("could not look up the app of action %r", provider, exc_info=True)
        sentence = ""
    return sentence or (
        f"No app running here provides its “{provider}” action. Install or activate the app that "
        "provides it, or change what it runs."
    )


def _from_its_app(provider: str) -> str:
    """The sentence naming the app that registered *provider* in this process, or ""."""
    from personalclaw.action_providers.registry import action_origin
    from personalclaw.providers.registry import get_provider_registry

    origin = action_origin(provider)
    if origin is None:
        return ""
    primary = get_provider_registry().get(origin.app)
    if primary is None:
        return (
            f"The “{origin.app_label}” app that provides its “{origin.label}” action is not "
            "installed. Install it again from the Store, or change what it runs."
        )
    records = [r for r in primary.chain() if r.provider_config.type == "action"] or [primary]
    app = f"The “{primary.manifest.displayName or origin.app_label}” app"
    action = f"its “{origin.label}” action"
    if any(r.error for r in records):
        return (
            f"{app} that provides {action} did not start, and Settings → Providers says why. Fix "
            "the app, or change what it runs."
        )
    if not any(r.enabled for r in records):
        return (
            f"{app} that provides {action} is deactivated. Activate it in Apps, or change what "
            "it runs."
        )
    return f"{app} no longer provides {action}. Change what it runs on the Triggers page."


def missing_secret(missing: Any) -> str:
    """Why a run whose action uses a secret that does not resolve did not run: the secret by its
    name (`triggers.secrets.UnresolvedSecret.key`), never a value, and for a key PersonalClaw keeps
    for a setting of its own, why no action can read it."""
    key = str(getattr(missing, "key", "") or "")
    refused = getattr(missing, "refused", None)
    if refused is not None:
        return (
            f"Its action uses the secret “{key}”, but {getattr(refused, 'cause', '')}. To use a "
            f"secret here, {getattr(refused, 'remedy', '')}."
        )
    return (
        f"Its action uses the secret “{key}”, which could not be found in Settings → Secrets. "
        "Add it there, or take it out of the action."
    )


async def refuse(
    trigger: Any,
    why: str,
    *,
    state: Any = None,
    by_hand: bool = False,
    store: Any = None,
    runs: Any = None,
) -> None:
    """Refuse a run of *trigger* that has nothing it can run, saying *why*.

    Records the refused run and its stamps (`run_record.record_refusal`; *by_hand* for a run a
    person or an outside caller started, *store* and *runs* the home's stores to write) and, the
    first time *trigger* is refused for *why*, tells its owner on its failure route through
    *state*. Never raises: the run was refused whatever becomes of its record.
    """
    from personalclaw.triggers import delivery, run_record

    trigger_id = str(getattr(trigger, "id", "") or "")
    logger.warning("trigger %s not run: %s", trigger_id, why)
    news = await run_record.record_refusal(
        trigger, why=why, by_hand=by_hand, store=store, runs=runs
    )
    if not news:
        return
    name = str(getattr(trigger, "name", "") or "") or trigger_id or "An automation"
    delivery.report_run(state, trigger, ok=False, error=why, title=f"{name} did not run")
