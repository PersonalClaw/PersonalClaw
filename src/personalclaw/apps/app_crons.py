"""An app's scheduled jobs: reconciled into the trigger store, and run at the app's agent tier.

An app manifest may declare ``crons: list[CronEntry]`` — scheduled agent jobs the app wants run on
a cadence. A job runs an AGENT, so it is honoured only when the app declares both the ``cron``
permission and an agent tier (``permissions.agent``, ``apps.agent_tiers``): :func:`schedules` is
that predicate, and install consent reads it. A manifest that declares jobs, or the ``cron``
permission, and no tier is refused at install (``AppManifest.validate``), since nothing it declares
could run; an install made before that rule runs none of its jobs.

**A job is the app's agent work, at the app's tier** (:func:`start_job`, which the ``invoke-agent``
action hands every fire of an ``app:`` trigger): ``text``, its model is handed the job's message
alone, with no tools; ``read``, an agent with read-only tools; ``tools``, an agent with the owner's
tools. The tier is the one the app holds when the job fires. The agent carries the app's name
(``SubagentInfo.app``), so it starts on the app's install consent and approves none of its calls
(``subagent_tier``): each call that needs approval asks the owner, whatever her own approval
settings say, as an app's agent-run does. Its result is the app's (it reports to ``app:<name>``),
never handed to a turn of her own agent. Nothing of the trigger's own step decides any of that: a
job's action carries no approval or write-access posture (``automation_posture.POSTURE_SPECS``),
reconciliation takes one off a row that has it, and a save that would put one on is refused
(:func:`posture_refusal`).

Rather than couple app_manager to the scheduler, the gateway calls :func:`reconcile_app_crons`
once at startup (after apps are loaded), and every app lifecycle transition does again.
Reconciliation is idempotent and declarative: every app-owned trigger is tagged
``created_by="app:<name>"``, so the desired set (enabled apps × their scheduled jobs) is diffed
against the registered ``app:*`` triggers, adding and pruning to match. This covers enable,
disable, uninstall, permission changes and manifest edits without per-lifecycle wiring.

Trigger id convention: ``app:<app-name>:<cron-name>`` (:func:`job_id`); the job is named by its
app (:func:`job_label`).

**🔴 S108 — this wrote to `crons.json`, so app crons DID NOT FIRE.** The clock engine
(`triggers.service.tick`) reads the unified store and nothing else, and the boot migration that
imports `crons.json` runs BEFORE reconciliation. A job written here landed in `crons.json`
with `triggers.json` empty, so an app's declared cron stayed inert until the NEXT gateway boot
imported it — every app cron was one restart behind its own manifest, and a freshly installed app's
cron never ran on the session that installed it. Reconciliation now writes the store directly.

The rows are built as `Trigger` objects rather than through `tools.create`, deliberately: this
reconciler's whole mechanism is a diff against a DETERMINISTIC id (``app:<app>:<cron>``), and
`tools.create` mints its own slug-derived unique id. Going through it would leave every restart
unable to recognize its own previous rows, so the diff would add duplicates forever instead of
converging.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.apps.agent_tiers import AGENT_READ, AGENT_TEXT, AGENT_TOOLS

logger = logging.getLogger(__name__)

_APP_JOB_PREFIX = "app:"


def job_id(app: str, cron: str) -> str:
    """The id the app *app*'s scheduled job *cron* is registered under: ``app:<app>:<cron>``."""
    return f"{_APP_JOB_PREFIX}{app}:{cron}"


def app_of(trigger_id: str) -> str:
    """The app whose scheduled job the trigger *trigger_id* is, or ``""`` for any other trigger.

    The inverse of :func:`job_id`. Every ``app:`` id is this module's (no other writer mints one,
    and reconciliation removes any it did not register), and an app's name holds no ``:``. Read
    from the id rather than the store, so work a job started is still that app's after the job is
    removed: the agent its fire started carries the id (``SubagentInfo.trigger_id``), as do the
    trigger's own session (``cron:<id>``) and a run it started (``RunOrigin.trigger_id``)."""
    head, _, rest = (trigger_id or "").partition(":")
    app, sep, cron = rest.partition(":")
    return app if f"{head}:" == _APP_JOB_PREFIX and app and sep and cron else ""


def job_label(app_name: str, cron: str) -> str:
    """What the app's scheduled job *cron* is called: ``<app's display name>: <job>``, the name
    install consent showed for the app (``app_manager.display_name_of``). The job's row is named
    so, so wherever it is named it is named by its app: the Triggers page, its runs' title, the
    asks and notes it leaves in the Inbox (``triggers.store.trigger_name``)."""
    from personalclaw.apps.app_manager import display_name_of

    return f"{display_name_of(app_name)}: {cron}"


def schedules(manifest: Any, cron: Any) -> bool:
    """Whether reconciliation registers ``cron`` as a live, ENABLED trigger once ``manifest``'s
    app is installed and on: the ``cron`` permission, an agent tier for the job's agent to run at
    (``permissions.agent``: with none, it would start no agent), a name to key the trigger by and
    a cadence to fire on. The one predicate for it: the install-consent disclosure
    (``apps/disclosure.describe``) reads this to say "installing turns on this scheduled job",
    so that sentence and the trigger store cannot disagree about which jobs run."""
    permissions = manifest.permissions
    return bool(
        permissions.cron and permissions.agent_tier and cron.name and (cron.every or cron.cron_expr)
    )


#: What the agent an app's scheduled job starts may do at each tier, after "it runs at the app's
#: agent tier:", in the words of the tier's install-consent sentence
#: (``web/src/pages/apps/installConsent.tsx``, ``AGENT_TIER_SENTENCE``).
_JOB_AT_TIER: dict[str, str] = {
    AGENT_TEXT: (
        "its model is handed only the job's message, with no tools, so it can't read your files "
        "or memory, change anything, run commands or send messages"
    ),
    AGENT_READ: (
        "its agent has read-only tools, so it can read your files and data, and can't change "
        "anything or send messages"
    ),
    AGENT_TOOLS: (
        "its agent uses your tools, and the app can't approve its calls, so each one that needs "
        "approval asks you"
    ),
}


def what_the_job_may_do(trigger_id: str) -> str:
    """What the agent the app's scheduled job *trigger_id* starts may do, as the app holds it now
    (``permissions.agent_tier_now``), in a sentence; ``""`` for any other trigger. The job's own
    Allow says this (``triggers.grants.what_its_agent_may_do``): its step's posture decides none
    of it."""
    app = app_of(trigger_id)
    if not app:
        return ""
    from personalclaw.apps.app_manager import display_name_of
    from personalclaw.apps.permissions import agent_tier_now, no_agent_work

    named = f"It is the scheduled job of the app “{display_name_of(app)}”"
    tier = agent_tier_now(app)
    if not tier:
        return f"{named}, and it starts no agent: {no_agent_work(app)}."
    return f"{named}, and it runs at the app's agent tier: {_JOB_AT_TIER[tier]}."


def posture_refusal(trigger_id: str, action: Any) -> str:
    """Why *action* cannot be saved as the action of the app's scheduled job *trigger_id*: it sets
    how the job's agent asks you, or what it may change (``automation_posture.POSTURE_SPECS``),
    and that is the app's agent tier, which no step of its trigger decides (:func:`start_job`).
    ``""`` for any other trigger, and for an action that leaves both unset.

    Asked by every door that edits a trigger's action (``triggers.tools.update``, which the
    Triggers page, the chat and the CLI save through), so none of them shows the owner a posture
    the job would not run with. Its switch, its cadence and its message stay hers to change."""
    app = app_of(trigger_id)
    if not app or not isinstance(action, dict):
        return ""
    from personalclaw.automation_posture import POSTURE_SPECS

    inline = action.get("inline")
    config = (inline if isinstance(inline, dict) else action).get("config")
    if not isinstance(config, dict) or not any(
        str(config.get(key) or "").strip() for key in POSTURE_SPECS
    ):
        return ""
    named = job_label(app, trigger_id.split(":", 2)[2])
    return (
        f"“{named}” is a scheduled job of an app, and its agent runs at the app's agent tier: "
        "whether it asks you first and what it may change are the app's to declare, and agreed to "
        "when you installed it, so neither is set here. Pause it here, or change what the app "
        "declares by updating it."
    )


def start_job(
    app: str,
    *,
    task: str,
    agent: str,
    model: str | None,
    max_turns: int,
    trigger_id: str,
    title: str,
    subagents: Any,
) -> tuple[Any, str]:
    """Start app *app*'s scheduled job: its agent, on *task*, at the agent tier the app holds now
    (``permissions.agent_tier_now``), as the app's work. Returns ``(the run, "")``, or
    ``(None, why)`` when the app may run no agent work now (switched off, gone, or declaring no
    tier), and then nothing starts.

    Held as an app's agent-run is (``handlers/apps.api_app_agent_run``): the tier's capability
    class (``agent_tiers.capability_class``); the app's name, so it starts on the app's install
    consent and approves none of its calls; and the app as its parent, so its result is the app's
    and no turn of the owner's agent is handed it. A text job runs on the worker built with no
    tools (``subagent_tier.run_agent``), so it names no agent. The trigger rides along, so its run
    history says how the job went and an approval it asks for is listed under it."""
    from personalclaw.apps.agent_tiers import capability_class
    from personalclaw.apps.permissions import agent_tier_now, no_agent_work

    tier = agent_tier_now(app)
    if not tier:
        return None, f"the scheduled job starts no agent: {no_agent_work(app)}"
    info = subagents.spawn(
        task,
        parent_session_key=f"{_APP_JOB_PREFIX}{app}",
        agent="" if tier == AGENT_TEXT else agent,
        max_turns=max_turns,
        model=model,
        capability_class=capability_class(tier),
        trigger_id=trigger_id,
        title=title,
        app=app,
    )
    return info, ""


def _desired_app_crons() -> dict[str, dict]:
    """The app triggers that SHOULD exist: for every enabled app, one entry per scheduled job
    (:func:`schedules`). Keyed by trigger id ``app:<app>:<cron>`` → the params to register."""
    from personalclaw.apps.app_manager import _manifest_of
    from personalclaw.apps.manager import _read_installed, apps_dir

    root = apps_dir()
    if not root.is_dir():
        return {}
    desired: dict[str, dict] = {}
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        meta = _read_installed(entry.name)
        if meta is None or not meta.enabled:
            continue
        manifest = _manifest_of(meta.name)
        if manifest is None:
            continue
        for cron in manifest.crons:
            # The `cron` permission, an agent tier, a name to key the trigger by and a cadence to
            # fire on — `schedules` is the same predicate install consent discloses.
            if not schedules(manifest, cron):
                continue
            job_name = job_id(meta.name, cron.name)
            desired[job_name] = {
                "name": job_label(meta.name, cron.name),
                # The action in the STORE's shape, the one an `invoke-agent` trigger keeps: what
                # the job runs, and nothing of how its agent asks or what it may change, which is
                # the app's agent tier, read when the job fires (`start_job`).
                "workflow": {
                    "inline": {
                        "provider": "invoke-agent",
                        "config": {
                            "task_template": cron.message,
                            "agent": cron.agent or "",
                            "model": "",
                        },
                    }
                },
                "spec": (
                    {"kind": "interval", "interval_secs": int(cron.every)}
                    if cron.every
                    else {"kind": "cron", "expr": cron.cron_expr}
                ),
                "created_by": f"{_APP_JOB_PREFIX}{meta.name}",
                # App crons are headless — there is no owner conversation to post to. `delivery`
                # is always `none` (the store's spelling of the legacy `silent=True`): otherwise
                # every run tried to open a channel DM to the trigger's created_by (an
                # "app:<name>" pseudo-id, not a real user) and logged a delivery failure. An app
                # surfaces a cron result itself (its backend, or the send_message tool), never via
                # cron auto-delivery.
                "delivery": "none",
            }
    return desired


def _as_registered(trigger: Any, params: dict) -> list[str]:
    """Put back on the registered job *trigger* what reconciliation registers it with (*params*) on
    the points no edit of it decides, and say which it changed: its route is ``none`` (an app's job
    is headless, so it has no conversation to post to), and its action carries no approval or
    write-access posture (``automation_posture.POSTURE_SPECS``), since its agent runs at the app's
    agent tier whatever a step says (:func:`start_job`). A row registered before that rule carries
    ``approval_mode: "auto"``, and would show the owner an agent that approves its own calls; one
    still named by its id gets its app's name (:func:`job_label`). Everything else on the row stays
    as it is: its switch, its cadence, its message, a name the owner gave it."""
    from personalclaw.automation_posture import POSTURE_SPECS

    changed: list[str] = []
    if str(getattr(trigger, "name", "") or "") == trigger.id:
        trigger.name = params["name"]
        changed.append("name")
    if str(getattr(trigger, "delivery", "") or "") != "none":
        trigger.delivery = "none"
        changed.append("delivery")
    workflow = getattr(trigger, "workflow", None)
    inline = workflow.get("inline") if isinstance(workflow, dict) else None
    action = inline if isinstance(inline, dict) else workflow
    config = action.get("config") if isinstance(action, dict) else None
    if isinstance(config, dict):
        for key in POSTURE_SPECS:
            if key in config:
                del config[key]
                changed.append(key)
    return changed


def reconcile_app_crons(store: Any) -> None:
    """Make the store's ``app:*`` triggers match what the installed apps schedule.

    Idempotent: safe to call on every startup. Best-effort — a single bad entry is logged and
    skipped, never blocking the others or startup.
    """
    from personalclaw.triggers import screen as _screen
    from personalclaw.triggers.arm import arm as _arm
    from personalclaw.triggers.models import Trigger

    try:
        desired = _desired_app_crons()
    except Exception:
        logger.warning("app-cron reconcile: could not compute desired set", exc_info=True)
        return

    try:
        rows = store.load()
    except Exception:
        logger.warning("app-cron reconcile: could not read the trigger store", exc_info=True)
        return
    existing = {
        row.trigger.id: row.trigger
        for row in rows
        if str(row.trigger.id).startswith(_APP_JOB_PREFIX)
    }

    # Prune app triggers no longer desired (app disabled/uninstalled, a permission or the agent
    # tier revoked, or the manifest dropped the entry).
    for trigger_id in existing:
        if trigger_id not in desired:
            try:
                store.delete(trigger_id)
                logger.info("app-cron reconcile: pruned %s", trigger_id)
            except Exception:
                logger.debug("app-cron reconcile: prune failed for %s", trigger_id, exc_info=True)

    # Add newly-desired app triggers (skip ones already registered — leave a user's enable/disable
    # toggle on an existing app trigger untouched). An existing one gets back what no edit of it
    # decides (`_as_registered`): its `none` route and an action with no posture of its own.
    for trigger_id, params in desired.items():
        cur = existing.get(trigger_id)
        if cur is not None:
            try:
                changed = _as_registered(cur, params)
                if changed:
                    store.upsert(cur)
                    logger.info(
                        "app-cron reconcile: %s set as registered (%s)",
                        trigger_id,
                        ", ".join(changed),
                    )
            except Exception:
                logger.debug("app-cron reconcile: could not correct %s", trigger_id, exc_info=True)
            continue
        try:
            trigger = Trigger(
                id=trigger_id,
                name=params["name"],
                kind="clock",
                enabled=True,
                created_by=params["created_by"],
                spec=dict(params["spec"]),
                workflow=dict(params["workflow"]),
                delivery=params["delivery"],
            )
            # 🔴 FREEZE THE CAPABILITY SET (decision 7). An app cron runs `invoke-agent`,
            # which is write-capable, and the now-wired fence denies on an empty block — so without
            # this every app-declared cron would refuse on its next fire. The app's own manifest
            # permissions (`cron` and its agent tier) are the opt-in; this records what they cover.
            trigger.capabilities = _screen.capabilities_for_action(trigger)
            # ARM IT NOW, for the reason `tools.create` records: `service.due_ids` only surfaces
            # rows that HAVE a `next_fire_at`, so an unarmed trigger never fires. Arming here rather
            # than leaving it to the next boot sweep is the difference between an app's cron running
            # tonight and running after the user restarts.
            armed = _arm(trigger)
            if armed:
                trigger.next_fire_at = armed
            store.upsert(trigger)
            logger.info("app-cron reconcile: registered %s", trigger_id)
        except Exception:
            logger.warning("app-cron reconcile: failed to register %s", trigger_id, exc_info=True)
