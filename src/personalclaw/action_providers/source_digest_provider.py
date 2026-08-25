"""The morning source digest's CALLER (WATCHED-SOURCES §6.2, WS-7's missing half).

WS-7 shipped `knowledge/source_digest.py:run_morning_digest` fully tested and **with zero
callers** — its own execution log records the atom as PARTIAL for exactly that reason: *"the
digest is invocable and fully tested, but nothing in the shipped product calls it yet"*. This
module is that caller: an action provider the scheduler can dispatch, plus the reconciliation
that makes a bundled `clock` trigger point at it.

**A clock trigger, NOT a bundled workflow template.** WS-7 chose a callable over a template
because *"inventing a template format for one consumer would have put `fence_untrusted` inside a
user-editable prompt string — a security control a template author could delete."* That
reasoning is intact here: the prompt is still composed in `source_digest.build_prompt`, the
trigger's `workflow.inline.config` is EMPTY, and there is no prompt text anywhere on this path
for a user to edit. The trigger carries a schedule and a provider name and nothing else.

**Why an action provider rather than calling the digest from the tick.** `reconcile_digest_cron`
(`digest_provider.py`) is the codebase's shape for "a bundled thing that arranges to run on a
clock": a deterministic-id `Trigger` row in the unified store whose `workflow.inline.provider`
names a registered action provider. Riding it means the digest inherits the whole fire path —
disposition, the overlap claim lock, history, `delivery: none` — instead of a second scheduling
mechanism beside it. It also means the digest is dispatchable by hand from the Triggers UI, which
is the "user can find it and use it" half of user-reachability.

**It writes `crons.json` NEVER.** `reconcile_digest_cron`'s docstring records S108: the boot
migration that imports `crons.json` runs BEFORE reconciliation, so a row written there stays
inert until the next boot. This writes the unified `TriggerStore` directly.

**Creation-only convergence, and NO new config field.** "Morning" is the meaning of the feature,
not a preference — the same call `reconcile_usage_recap_cron` makes for "monthly". So an existing
row is left exactly as the user left it, including a schedule they edited by hand, and no
`config.json` field is minted to converge. (`notification-digest` converges because ITS schedule
is a documented Settings control; this one has none.)

**Exactly one item and one notification per run, and nothing posts twice (SC#10).** Two distinct
guarantees, neither invented here:

1. *Within* a run, `run_morning_digest` writes ONE `note` item and calls `state.notify` once.
2. *Across* fires, `<home>/sources/digest_cursor.json` is the guard. The cursor advances past the
   window only after the item is durable, so a second sequential fire (a retry, a restart, a
   hand-run right after the cron) reads an EMPTY window and returns `skipped_reason` — zero
   items, zero notifications, zero model calls. Concurrent fires are refused one level up by the
   overlap claim lock (`triggers/firepath.py` step 7; `Trigger.overlap` defaults to `"skip"`).
   The failure the cursor deliberately does NOT prevent is a crash BETWEEN the item write and the
   cursor write, which re-reads the window and produces a second visible digest — WS-7's stated
   trade, because the reverse ordering loses a day's items silently.

**The notification gate is not re-implemented or bypassed.** The provider hands
`DashboardState` straight to the digest; delivery is `state.notify` →
`notification_allowed()` exactly as WS-7 shipped it, so `mute_all` / minimum severity / quiet
hours still suppress. Nothing here notifies on its own, which is why `delivery: none` on the
trigger costs nothing: the digest's own notification IS the user-visible output.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.action_providers.services import get_action_services

logger = logging.getLogger(__name__)

#: The trigger's DETERMINISTIC id. `tools.create` mints its own unique slug, so convergence
#: through it would add another digest trigger every restart instead of recognizing its own —
#: the reason `digest_provider`, `usage_recap_provider` and `app_crons` all build a `Trigger`
#: directly. The `system:` prefix keeps a reconcile away from a user's hand-made rows.
SOURCE_DIGEST_JOB_NAME = "system:source-digest"

#: 07:00 daily. Not a config field: §6.2 calls this the *morning* digest, so the hour is the
#: feature's meaning rather than a preference, and a knob with no Settings control behind it
#: would be an inert control (see the module docstring).
SOURCE_DIGEST_SCHEDULE = "0 7 * * *"


class SourceDigestActionProvider(ActionProvider):
    """Run one morning digest over watched-source items."""

    @property
    def name(self) -> str:
        return "source-digest"

    @property
    def display_name(self) -> str:
        return "Morning Source Digest"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.knowledge.source_digest import run_morning_digest

        services = get_action_services()
        state = getattr(services, "state", None) if services is not None else None
        if state is None:
            # No state means no gate, and the gate is where `mute_all` lives. Refusing is the
            # only honest answer: running anyway would notify past a control we cannot consult.
            return ActionResult(success=False, error="source digest: no dashboard state to notify")

        try:
            store = _open_store()
        except Exception as exc:  # noqa: BLE001 - surface as a result, never raise
            return ActionResult(success=False, error=f"source digest: no knowledge store: {exc}")

        try:
            result = await run_morning_digest(knowledge_store=store, state=state)
        except Exception as exc:  # noqa: BLE001 - surface as a result, never raise
            return ActionResult(success=False, error=f"source digest failed: {exc}")

        if result.skipped_reason:
            # An empty window is a SUCCESS with nothing to show — the same call
            # `digest_provider` makes for an empty queue. Reporting it as a failure would light
            # up the trigger's error surface every day a user's sources were quiet, and on every
            # install that has no watched sources at all.
            return ActionResult(
                success=True, exit_code=0, stdout=f"source digest: {result.skipped_reason}"
            )
        return ActionResult(
            success=True,
            exit_code=0,
            stdout=(
                f"source digest: created {result.item_id} from {result.item_count} items "
                f"(notified={result.notified})"
            ),
        )


def _open_store():
    """Open the ONE global knowledge store, through `knowledge_db_path`.

    Never a locally composed path — `knowledge_persist_provider._open_store` records the measured
    failure: a composed `<home>/knowledge/knowledge.db` lands in a second database the dashboard
    (which reads `<home>/workspace/knowledge/knowledge.db`) can never see, with no error either
    side. Resolved per call, so a test's redirected home is honoured.
    """
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))


def create_provider(config: dict[str, Any] | None = None) -> "SourceDigestActionProvider":
    return SourceDigestActionProvider()


def reconcile_source_digest_cron(store: Any) -> None:
    """Make the morning-digest trigger exist. Idempotent, best-effort.

    Creation-only, like `reconcile_usage_recap_cron`: there is no user-facing schedule setting to
    converge, so an existing row is left alone — including one the user edited or disabled. A
    scheduler problem must never block startup, hence every step is wrapped.

    Enabled on creation, deliberately. A disabled bundled trigger is the same defect WS-7 was
    PARTIAL for, one level up: registered and never fired. It is safe to leave on because a home
    with no watched sources produces an EMPTY window, and an empty window writes no item, sends
    no notification and spends no model call.
    """
    from personalclaw.triggers import screen as _screen
    from personalclaw.triggers.arm import arm as _arm
    from personalclaw.triggers.models import Trigger

    try:
        row = store.get(SOURCE_DIGEST_JOB_NAME)
    except Exception:
        logger.debug("source-digest cron: could not read the trigger store", exc_info=True)
        return
    if row is not None:
        return

    try:
        trigger = Trigger(
            id=SOURCE_DIGEST_JOB_NAME,
            name=SOURCE_DIGEST_JOB_NAME,
            kind="clock",
            enabled=True,
            created_by="system",
            spec={"kind": "cron", "expr": SOURCE_DIGEST_SCHEDULE},
            # EMPTY config: the prompt is composed in `source_digest.build_prompt`, behind the
            # fence, and never here. That is what keeps WS-7's security reasoning intact.
            workflow={"inline": {"provider": "source-digest", "config": {}}},
            # The digest's OUTPUT is a notification, so a cron-result notification about it would
            # be a notification about your notification (`digest_provider`'s reasoning, and the
            # store's spelling of the legacy `silent=True`).
            delivery="none",
        )
        # Writes a knowledge item and notifies, unattended, forever — write-capable, so the fence
        # needs the frozen grant (decision 7). A system-created trigger's opt-in is the code path
        # that created it.
        trigger.capabilities = _screen.capabilities_for_action(trigger)
        armed = _arm(trigger)
        if armed:
            trigger.next_fire_at = armed
        store.upsert(trigger)
        logger.info("registered the morning source-digest trigger (%s)", SOURCE_DIGEST_SCHEDULE)
    except Exception:
        logger.warning("source-digest cron: registration failed", exc_info=True)
