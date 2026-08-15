"""C6 — the Proposal contract and its apply dispatcher (INU-7).

**Why this module exists.** Before it, a `proposal` inbox item was an ``InboxItem`` with
``refs["learning_proposal"]`` and its resolution was hard-wired to
``learning/proposals.accept``. Every upcoming producer (auto-learned skills, session-org
suggestions, feedback retire-proposals, earned-autonomy offers, app-emitted drafts) would
otherwise have wired its own approval path into the inbox handlers — N surfaces, N
resolutions, N ways to get "approved but nothing happened" wrong.

So one payload (:class:`Proposal`, carried in ``refs["proposal"]``) and one dispatcher
(:func:`apply_item`) own the mechanics, and **execution stays where it already lives**:

===================  ==========================================================
apply case           existing dispatcher it routes to
===================  ==========================================================
``action``           ``action_providers.registry.get_action_provider`` +
                     ``provider.execute(ActionContext, config)`` — the same call
                     shape ``gateway``/``event_triggers``/``hooks`` use.
``workflow``         ``workflows.service.start_run(name=..., inputs=...)``
``skill_promotion``  ``learning.proposals.accept(pid, installer=...)`` (the T4.1
                     path, now one case of the contract rather than the only one)
``app_callback``     ``tool_providers.app_routes.resolve_route`` +
                     ``call_app_route`` — the owner's reverse proxy, app-scoped
                     token, ``agentCallable`` gate included
===================  ==========================================================

This module owns **no** execution of its own. If a reader ever finds a subprocess, an HTTP
client or a file write below, that is the defect this docstring exists to prevent.

**The apply case set is CLOSED.** ``apply`` carries exactly one of the four keys.
:meth:`Proposal.apply_case` raises on zero, two, or an unrecognised key, and
:data:`_DISPATCH` is asserted total against :class:`ApplyCase` at import — there is no
default branch for an unmapped case to fall through, because this repo has a defect class
where exactly that swallowed an unmapped enum value.

**A failed apply keeps the item PENDING.** :func:`apply_item` writes ``HANDLED`` +
``refs["proposal_result"]`` only on success; on any failure the row keeps its current
status and records ``refs["proposal_error"]``. A proposal that silently vanished is worse
than one that failed loudly: the user would believe the thing happened.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

#: ``refs`` key carrying the serialized :class:`Proposal`.
REFS_KEY = "proposal"
#: ``refs`` key carrying the last successful apply result.
RESULT_KEY = "proposal_result"
#: ``refs`` key carrying the last failed apply error (item stays PENDING).
ERROR_KEY = "proposal_error"

PREVIEW_KINDS = ("text", "diff")


class ApplyCase(str, Enum):
    """The closed set of apply cases. Adding a member REQUIRES a `_DISPATCH` entry."""

    ACTION = "action"
    WORKFLOW = "workflow"
    SKILL_PROMOTION = "skill_promotion"
    APP_CALLBACK = "app_callback"


class ProposalError(Exception):
    """A malformed proposal payload, or an apply case that cannot be dispatched."""


@dataclass(frozen=True)
class Proposal:
    """The C6 payload, carried in ``refs["proposal"]`` on a ``kind=proposal`` item.

    ``provenance`` is who produced it — ``"skills"``, ``"learning"``, ``"session_org"``,
    ``"app:<name>"``. It is half of the batch-approve grouping key: the UI offers a sweep
    only across one ``(provenance, kind)`` pair, so "approve all" can never mean "approve
    these four unrelated things".

    ``editable`` opts the row into edit-then-approve: the frontend posts an edited payload
    and THAT is what apply receives (:func:`apply_item`'s ``edited`` argument).
    """

    title: str
    preview: str = ""
    preview_kind: str = "text"
    provenance: str = ""
    expires_at: str | None = None
    editable: bool = False
    apply: dict[str, Any] = field(default_factory=dict)

    def apply_case(self) -> ApplyCase:
        """The single apply case this proposal declares.

        Raises :class:`ProposalError` on zero keys, more than one key, or a key outside
        the closed set — never guesses, never picks the first.
        """
        if not isinstance(self.apply, dict) or not self.apply:
            raise ProposalError("proposal.apply is empty: exactly one apply case required")
        keys = sorted(str(k) for k in self.apply)
        if len(keys) > 1:
            raise ProposalError(
                f"proposal.apply declares {len(keys)} cases ({', '.join(keys)}): exactly one"
            )
        try:
            return ApplyCase(keys[0])
        except ValueError:
            known = ", ".join(c.value for c in ApplyCase)
            raise ProposalError(f"unknown apply case {keys[0]!r} (known: {known})") from None

    def payload(self) -> dict[str, Any]:
        """The declared case's argument dict."""
        raw = self.apply.get(self.apply_case().value)
        return dict(raw) if isinstance(raw, dict) else {}

    def is_expired(self, *, now: float | None = None) -> bool:
        """True when ``expires_at`` is in the past. An unparseable value is NOT expired —
        a bad timestamp must not silently dismiss a real proposal."""
        if not self.expires_at:
            return False
        from datetime import datetime, timezone

        try:
            parsed = datetime.fromisoformat(str(self.expires_at).replace("Z", "+00:00"))
        except ValueError:
            logger.debug("proposal expires_at unparseable: %r", self.expires_at)
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp() < (now if now is not None else time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "preview": self.preview,
            "preview_kind": self.preview_kind,
            "provenance": self.provenance,
            "expires_at": self.expires_at,
            "editable": self.editable,
            "apply": dict(self.apply),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Proposal":
        """Tolerant read (an older row keeps working); ``apply`` shape is validated only
        when :meth:`apply_case` is asked for, so a listing never 500s on a bad payload."""
        if not isinstance(data, dict):
            raise ProposalError("proposal payload is not an object")
        preview_kind = str(data.get("preview_kind") or "text")
        if preview_kind not in PREVIEW_KINDS:
            preview_kind = "text"
        expires = data.get("expires_at")
        raw_apply = data.get("apply")
        return cls(
            title=str(data.get("title") or ""),
            preview=str(data.get("preview") or ""),
            preview_kind=preview_kind,
            provenance=str(data.get("provenance") or ""),
            expires_at=str(expires) if expires else None,
            editable=bool(data.get("editable", False)),
            apply=dict(raw_apply) if isinstance(raw_apply, dict) else {},
        )


@dataclass(frozen=True)
class ApplyOutcome:
    """What one apply did. ``ok=False`` means the item stays PENDING carrying ``error``."""

    ok: bool
    case: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "case": self.case, "result": dict(self.result), "error": self.error}


# ---------------------------------------------------------------------------
# The four cases. Each one CALLS an existing dispatcher; none reimplements one.
# ---------------------------------------------------------------------------


async def _apply_action(args: dict[str, Any], ctx: "ApplyContext") -> dict[str, Any]:
    """Action-provider invocation through the action registry (the hooks/triggers path)."""
    from personalclaw.action_providers import registry
    from personalclaw.action_providers.base import ActionContext

    # 🔴 The kill switch, on the proposal-apply path too. This dispatches an action provider
    # directly rather than through `triggers.tools.run`, so enforcing it only there would leave
    # Approve firing actions during an incident — the same gap S117 found when three unattended
    # entry points existed and only one checked the flag. A proposal apply is user-clicked, so
    # `manual_refusal` (the manual Run path's gate) is the right check rather than the
    # unattended denylist seam; the refusal surfaces as a failed apply, which keeps the item
    # PENDING with the reason instead of silently dropping the user's approval.
    from personalclaw.triggers.tools import manual_refusal

    refusal = manual_refusal()
    if refusal:
        raise ProposalError(f"refused: {refusal}")

    name = str(args.get("provider") or "")
    if not name:
        raise ProposalError("apply.action needs a `provider`")
    registry._ensure_default_providers_registered()
    provider = registry.get_action_provider(name)
    if provider is None:
        raise ProposalError(f"unknown action provider {name!r}")
    config = dict(args.get("config") or {})
    result = await provider.execute(
        config,
        ActionContext(event="proposal_apply", payload={"item_id": ctx.item_id}),
    )
    if not getattr(result, "success", False):
        detail = getattr(result, "error", "") or getattr(result, "stderr", "")
        raise ProposalError(str(detail or "action provider failed"))
    return {"provider": name, "output": str(getattr(result, "stdout", "") or "")}


async def _apply_workflow(args: dict[str, Any], ctx: "ApplyContext") -> dict[str, Any]:
    """A workflow run through ``workflows.service.start_run``.

    DEVIATION from C6's ``{ref | inline}`` sketch: only ``ref`` is accepted. There is no
    existing dispatcher that starts an unsaved inline definition, and declaring a shape
    nothing serves is this repo's #47 defect (declarable → looks supported → silently
    dead). A producer that wants an inline def saves it first, then proposes its name.
    """
    from personalclaw.workflows.service import start_run

    ref = str(args.get("ref") or "")
    if not ref:
        raise ProposalError("apply.workflow needs a `ref` (a saved definition name)")
    res = await start_run(
        name=ref,
        inputs=dict(args.get("inputs") or {}),
        idempotency_key=f"proposal:{ctx.item_id}",
    )
    if not res.get("ok"):
        raise ProposalError(str(res.get("error") or res.get("code") or "workflow start failed"))
    return {"workflow": ref, "run_id": str(res.get("run_id") or "")}


async def _apply_skill_promotion(args: dict[str, Any], ctx: "ApplyContext") -> dict[str, Any]:
    """The T4.1 skill path, now one case of the contract.

    Routes to ``learning.proposals.accept`` with the SAME installer dispatch the learning
    handler builds, so accepting from the inbox and accepting from the Learning surface
    install through one code path (and the human-only gate inside ``accept`` still holds).
    """
    from personalclaw.learning import proposals as learning_proposals

    pid = str(args.get("pid") or "")
    if not pid:
        raise ProposalError("apply.skill_promotion needs a `pid`")
    installer = ctx.installer
    if installer is None:
        from personalclaw.learning import (
            project_context_review,
        )
        from personalclaw.learning import skill_promotion as skill_promotion_mod

        def installer(prop) -> None:  # noqa: F811 - deliberate local default
            data = prop.to_dict()
            if project_context_review.is_project_context_proposal(data):
                project_context_review.install_accepted_project_context(data)
            elif skill_promotion_mod.is_skill_promotion_proposal(data):
                skill_promotion_mod.install_accepted_skill(data)

    prop = learning_proposals.accept(pid, installer=installer, actor=ctx.actor)
    return {"pid": pid, "status": getattr(prop, "status", "")}


async def _apply_app_callback(args: dict[str, Any], ctx: "ApplyContext") -> dict[str, Any]:
    """POST to the emitting app's declared route through the owner's reverse proxy."""
    from personalclaw.tool_providers.app_routes import call_app_route, resolve_route

    app_name = str(args.get("app") or "")
    route = str(args.get("route") or "")
    if not app_name or not route:
        raise ProposalError("apply.app_callback needs `app` and `route`")
    resolution = resolve_route(app_name, route, dict(args.get("arguments") or {}))
    result = await call_app_route(resolution)
    if not getattr(result, "success", False):
        raise ProposalError(str(getattr(result, "error", "") or "app callback failed"))
    return {"app": app_name, "route": route, "output": str(getattr(result, "output", "") or "")}


@dataclass
class ApplyContext:
    """What a case needs beyond its own args. Deliberately tiny."""

    item_id: str = ""
    actor: str = "user"
    installer: Callable[[Any], None] | None = None


_DISPATCH: dict[ApplyCase, Callable[[dict[str, Any], ApplyContext], Awaitable[dict[str, Any]]]] = {
    ApplyCase.ACTION: _apply_action,
    ApplyCase.WORKFLOW: _apply_workflow,
    ApplyCase.SKILL_PROMOTION: _apply_skill_promotion,
    ApplyCase.APP_CALLBACK: _apply_app_callback,
}

# Totality, asserted at import rather than trusted: a new ApplyCase member with no handler
# would otherwise be an unmapped value dispatched through nothing.
_missing = set(ApplyCase) - set(_DISPATCH)
if _missing:  # pragma: no cover - import-time guard
    raise RuntimeError(
        f"ApplyCase members without a dispatcher: {sorted(c.value for c in _missing)}"
    )


async def apply_proposal(
    proposal: Proposal,
    *,
    item_id: str = "",
    actor: str = "user",
    installer: Callable[[Any], None] | None = None,
) -> ApplyOutcome:
    """Run one proposal's apply case. Never raises — returns a typed outcome."""
    try:
        case = proposal.apply_case()
    except ProposalError as exc:
        return ApplyOutcome(ok=False, error=str(exc))
    handler = _DISPATCH[case]  # total by the import-time guard above
    ctx = ApplyContext(item_id=item_id, actor=actor, installer=installer)
    try:
        result = await handler(proposal.payload(), ctx)
    except ProposalError as exc:
        return ApplyOutcome(ok=False, case=case.value, error=str(exc))
    except Exception as exc:  # a dispatcher blew up: report it, keep the item
        logger.warning("proposal apply (%s) failed: %s", case.value, exc, exc_info=True)
        return ApplyOutcome(ok=False, case=case.value, error=f"{type(exc).__name__}: {exc}")
    return ApplyOutcome(ok=True, case=case.value, result=result)


def proposal_of(item: Any) -> Proposal | None:
    """The C6 payload on an inbox item, or None when it carries none."""
    refs = getattr(item, "refs", None)
    if not isinstance(refs, dict):
        return None
    raw = refs.get(REFS_KEY)
    if raw is None:
        return None
    try:
        return Proposal.from_dict(raw)
    except ProposalError:
        logger.debug("item %s carries an unreadable proposal payload", getattr(item, "id", "?"))
        return None


async def apply_item(
    item: Any,
    *,
    store: Any = None,
    edited: dict[str, Any] | None = None,
    actor: str = "user",
    installer: Callable[[Any], None] | None = None,
) -> ApplyOutcome:
    """Apply the proposal on *item* and write the outcome back to the row.

    Success → ``HANDLED`` + ``refs["proposal_result"]``. Failure → the status is left
    exactly as it was (PENDING for an unhandled row) and ``refs["proposal_error"]`` records
    what happened, so the user still sees the proposal and can retry it. The row is
    persisted on BOTH paths: an error nobody wrote down is an error nobody can act on.

    ``edited`` is the edit-then-approve payload: for an ``editable`` proposal it REPLACES
    the stored payload (and is persisted, so the row shows what was actually applied). An
    edit on a non-editable proposal is refused rather than silently ignored.
    """
    from personalclaw.inbox import ItemStatus

    proposal = proposal_of(item)
    if proposal is None:
        return ApplyOutcome(ok=False, error="item carries no proposal payload")

    if edited is not None:
        if not proposal.editable:
            return ApplyOutcome(ok=False, error="this proposal is not editable")
        try:
            proposal = Proposal.from_dict(edited)
        except ProposalError as exc:
            return ApplyOutcome(ok=False, error=f"edited payload rejected: {exc}")
        item.refs[REFS_KEY] = proposal.to_dict()

    if proposal.is_expired():
        outcome = ApplyOutcome(ok=False, error="proposal expired")
        _record(item, store, outcome)
        return outcome

    outcome = await apply_proposal(
        proposal, item_id=str(getattr(item, "id", "")), actor=actor, installer=installer
    )
    if outcome.ok:
        item.status = ItemStatus.HANDLED.value
        item.refs[RESULT_KEY] = outcome.to_dict()
        item.refs.pop(ERROR_KEY, None)
    _record(item, store, outcome)
    return outcome


def _record(item: Any, store: Any, outcome: ApplyOutcome) -> None:
    """Persist the row. On failure the status is untouched — that is the whole point."""
    if not outcome.ok:
        item.refs[ERROR_KEY] = outcome.to_dict()
    if store is None:
        return
    try:
        # The row is the SAME object the store holds, so this is a persist, not a merge —
        # named fields are passed so a reader can see exactly what apply writes back.
        written = store.update(getattr(item, "id", ""), status=item.status, refs=item.refs)
        if written is None:
            logger.warning("proposal apply: item %s not in store", getattr(item, "id", "?"))
    except Exception:
        logger.warning("proposal apply: inbox write failed for %s", getattr(item, "id", "?"))


# ---------------------------------------------------------------------------
# The app emission path's naming + registration (INU-7 T7.2).
#
# One place decides what an app's proposal kind is CALLED, because three readers must
# agree: the enable-time registration, the 403 check on POST /api/inbox/proposals, and the
# rules store (`<source>/<kind>`). Two of them computing the pair independently is how a
# declared kind ends up unregistered and silently undeliverable.
# ---------------------------------------------------------------------------

#: Source prefix for an app-emitted notification pair.
APP_SOURCE_PREFIX = "app:"
#: Kind prefix, so an app's proposal kinds never collide with its other kinds.
APP_KIND_PREFIX = "proposal:"


def app_source(app_name: str) -> str:
    """The notification `source` for *app_name* — also the proposal's `provenance`."""
    return f"{APP_SOURCE_PREFIX}{app_name}"


def app_kind(kind_suffix: str) -> str:
    """The notification `kind` for a declared suffix."""
    return f"{APP_KIND_PREFIX}{kind_suffix}"


def register_app_proposal_kinds(app_name: str, manifest: Any) -> list[str]:
    """Register every declared proposal kind for *app_name*. Returns the kinds registered.

    Called at ENABLE time (and idempotent, because re-enabling an app must not raise on the
    duplicate). ``verifiable=True`` by default: an app-emitted proposal is an app's claim,
    so INU-6's skeptic gate is allowed to apply to it — the gate still runs only when the
    user's rule sets ``verify:true``, so this widens what MAY be checked, never what is.

    ``attention=True`` because a proposal is a durable row, not a transient toast.
    """
    from personalclaw import notification_kinds

    declared = list(getattr(getattr(manifest, "permissions", None), "proposals", []) or [])
    registered: list[str] = []
    for entry in declared:
        suffix = getattr(entry, "kind_suffix", "")
        if not suffix or not getattr(entry, "is_valid", lambda: False)():
            logger.warning("app %s: skipping invalid proposal kind_suffix %r", app_name, suffix)
            continue
        kind = app_kind(suffix)
        try:
            notification_kinds.register(
                notification_kinds.NotificationKind(
                    source=app_source(app_name),
                    kind=kind,
                    label=getattr(entry, "label", "") or suffix,
                    attention=True,
                    verifiable=True,
                )
            )
        except ValueError:
            # Already registered (a re-enable). Not an error: the kind is what it should be.
            logger.debug("app %s: proposal kind %s already registered", app_name, kind)
        registered.append(kind)
    return registered


def deregister_app_proposal_kinds(app_name: str, manifest: Any) -> list[str]:
    """Drop *app_name*'s proposal kinds on disable/uninstall.

    Load-bearing, not tidiness: a kind that outlives its app is a phantom the rules UI
    still lists and ``resolve_kind`` still resolves, so a stale rule would keep governing
    a kind nothing can emit.
    """
    from personalclaw import notification_kinds

    declared = list(getattr(getattr(manifest, "permissions", None), "proposals", []) or [])
    dropped: list[str] = []
    for entry in declared:
        suffix = getattr(entry, "kind_suffix", "")
        if not suffix:
            continue
        kind = app_kind(suffix)
        if notification_kinds.unregister(app_source(app_name), kind):
            dropped.append(kind)
    return dropped
