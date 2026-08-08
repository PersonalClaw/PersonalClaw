"""Learning routes — the Proposal Inbox and the staging week panel (§6.1 / §7 — S78).

GET    /api/learning/proposals            the inbox: six kinds, ordered, filterable
GET    /api/learning/proposals/{id}       one proposal, full record
POST   /api/learning/proposals/{id}/accept   install (human reviewers only)
DELETE /api/learning/proposals/{id}       reject (human reviewers only)
GET    /api/learning/staging/week         the week-at-a-glance capture panel

The plan's success criterion 1 says "One Proposal Inbox SHOWS all six proposal kinds with
provenance,
evidence manifests, and risk-tier metadata; accept installs, reject dismisses — and the model cannot
accept its own proposals under any trust mode". Everything behind that sentence shipped in S75/S76
and
had no HTTP surface, so the criterion was unmet for want of a route: `inbox.build_view` and
`StagingStore.week` both return fully-serialized shapes, and this module wires them.

**The actor is load-bearing.** S75 measured that `proposals.accept()` knew nothing about who
was calling it, and put `require_human` inside it. A route that omitted the actor would default to
`user` and hand every caller — including an app-scoped token — the reviewer's authority. So the
actor is
DERIVED from the request rather than accepted from the body: a caller that could name itself `user`
would make the gate decorative.
"""

from __future__ import annotations

import logging

from aiohttp import web

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    """The `learning.enabled` kill-switch. Every route 404s when learning is off.

    404 rather than 403: with learning disabled there is no inbox, and reporting "forbidden" would
    imply one exists behind a permission wall. Matches the feedback module's rail.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().learning.enabled)
    except Exception:
        # Fail OPEN for a read surface: a malformed config should not hide the review queue, because
        # a hidden queue looks like an empty one and proposals then accumulate unseen.
        logger.debug("learning config unreadable; treating the surface as enabled", exc_info=True)
        return True


def _actor(request: web.Request) -> str:
    """Who is making this request, for S75's accept gate.

    DERIVED, never read from the body. An app-scoped token is an `agent`: an installed app acting
    through the API is exactly the "worker whose self-report needs checking" case §7 names, and
    letting
    one accept its own proposals would reproduce the hole S75 closed one layer up.

    A dashboard session is `user` — that is a human at the UI, which is the only actor the gate
    permits. Anything unrecognized returns `""`, which `require_human` denies rather than assuming
    human.
    """
    if request.get("app"):
        return "agent"
    if request.get("user"):
        return "user"
    return ""


def _installer_for(request: web.Request):
    """The accept-time installer, or None when no memory store is reachable.

    `proposals.accept` runs the installer AFTER `require_human` — so this is the human installing,
    the one path §2.6 permits to write a self-model principle live. It writes ONLY self-model
    principles (`source_cadence == "self_model"`); an ordinary `lesson_batch` from a correction
    already lives in the lesson store, so there is nothing further to install for it. A missing
    store means accept still records the decision — the proposal is not lost, only its live
    projection is deferred, which is the right failure for a best-effort projection.
    """
    from personalclaw.dashboard.handlers.memory import _get_service
    from personalclaw.learning import self_model_observer

    try:
        svc = _get_service(request.app["state"])
    except Exception:
        logger.debug("accept installer: no memory service", exc_info=True)
        return None

    def _install(prop) -> None:
        data = prop.to_dict()
        if self_model_observer.is_self_model_proposal(data):
            self_model_observer.install_accepted_principle(svc, data)

    return _install


def _tier_for(prop) -> str:
    """The risk tier for one proposal, or `""`.

    Only a `template_diff` carries typed ops to derive a tier from, so anything else is left
    unscored
    rather than stamped with a meaningless one — S75's projection defaults those to `review`, and a
    fabricated `low` would hand a lesson bulk-accept eligibility nobody computed.
    """
    from personalclaw.learning.refiner import risk_tier

    if str(getattr(prop, "kind", "")) != "template_diff":
        return ""
    manifest = getattr(prop, "change_manifest", None)
    ops = getattr(manifest, "targeted_fix", None) if manifest is not None else None
    if isinstance(ops, list):
        return risk_tier([o for o in ops if isinstance(o, dict)])
    return ""


async def api_learning_proposals(request: web.Request) -> web.Response:
    """GET /api/learning/proposals — the inbox across all six kinds.

    `?kind=` / `?tier=` / `?flagged=1` map onto `inbox.filter_rows`. Counts ride the response so a
    filter chip can render its number without a second request — a chip a user must click to
    discover
    is empty is worse than no chip.
    """
    if not _enabled():
        return web.json_response({"error": "learning is disabled"}, status=404)

    from personalclaw.learning import proposals as store
    from personalclaw.learning.inbox import build_view

    kind = (request.query.get("kind") or "").strip()
    tier = (request.query.get("tier") or "").strip()
    flagged = request.query.get("flagged", "") in ("1", "true", "yes")

    try:
        pending = store.list_pending()
    except Exception:
        # A corrupt row must not empty the queue: proposals are per-file, and one unreadable file
        # hiding the rest is how a backlog silently disappears.
        logger.warning("proposal listing failed", exc_info=True)
        pending = []

    tiers = {str(getattr(p, "id", "")): _tier_for(p) for p in pending}
    view = build_view(pending, tiers=tiers, kind=kind, tier=tier, flagged_only=flagged)
    return web.json_response(view.to_dict())


async def api_learning_proposal(request: web.Request) -> web.Response:
    """GET /api/learning/proposals/{id} — one full record.

    The inbox row is a summary; the detail view needs the body and the change manifest a reviewer
    actually reads before accepting.
    """
    if not _enabled():
        return web.json_response({"error": "learning is disabled"}, status=404)

    from personalclaw.learning import proposals as store

    prop = store.get(request.match_info.get("id", ""))
    if prop is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(prop.to_dict())


async def api_learning_proposal_accept(request: web.Request) -> web.Response:
    """POST /api/learning/proposals/{id}/accept — install it.

    The actor is derived from the request and passed through to `proposals.accept`, which runs S75's
    `require_human`. A refusal is **403 with the gate's reason**, not a generic error: a reviewer
    who
    hits this needs to know an agent-scoped token cannot accept, and a bare 500 would send them
    looking for a bug.

    There is deliberately no `?force=` or trust override. §7: "under ANY trust mode".
    """
    if not _enabled():
        return web.json_response({"error": "learning is disabled"}, status=404)

    from personalclaw.learning import proposals as store

    pid = request.match_info.get("id", "")
    actor = _actor(request)
    try:
        prop = store.accept(pid, actor=actor, installer=_installer_for(request))
    except store.AcceptError as exc:
        message = str(exc)
        # A missing row is a 404; a refused actor is a 403. Collapsing them would report a
        # permission
        # decision as a typo and vice versa.
        status = 404 if message.startswith("no proposal") else 403
        _audit(request, "learning.proposal_accept", "rejected", f"{pid}:{message}")
        return web.json_response({"error": message}, status=status)
    _audit(request, "learning.proposal_accept", "ok", f"{pid}:{prop.kind}")
    return web.json_response({"ok": True, "proposal": prop.to_dict()})


async def api_learning_proposal_reject(request: web.Request) -> web.Response:
    """DELETE /api/learning/proposals/{id} — dismiss it, and REMEMBER the decision.

    Gated on the same actor for the reason S75 recorded: an agent that could reject would clear its
    own
    bad proposals before a human read them, and the rejection exemplars the flywheel learns from
    would
    stop accumulating.
    """
    if not _enabled():
        return web.json_response({"error": "learning is disabled"}, status=404)

    from personalclaw.learning import proposals as store

    pid = request.match_info.get("id", "")
    actor = _actor(request)
    if store.get(pid) is None:
        return web.json_response({"error": "not found"}, status=404)
    ok = store.reject(pid, actor=actor)
    _audit(request, "learning.proposal_reject", "ok" if ok else "rejected", pid)
    if not ok:
        # `reject` returns False for a refused actor as well as a missing row; the row existed
        # above,
        # so this is the gate. 403 with the same shape as accept keeps the two symmetric.
        return web.json_response(
            {"error": "only a human reviewer may reject proposals"}, status=403
        )
    return web.json_response({"ok": True})


async def api_learning_staging_week(request: web.Request) -> web.Response:
    """GET /api/learning/staging/week — the week-at-a-glance capture panel.

    `?days=` bounds the window (1-31). The panel's point is that an EMPTY day renders as a gap:
    `health()` aggregates and cannot see a day where capture never ran, which is the failure the
    staging tier exists to expose.
    """
    if not _enabled():
        return web.json_response({"error": "learning is disabled"}, status=404)

    from personalclaw.learning.staging import StagingStore

    try:
        days = max(1, min(int(request.query.get("days", "7")), 31))
    except ValueError:
        return web.json_response({"error": "days must be an integer"}, status=400)

    store = StagingStore()
    try:
        return web.json_response(store.week(days=days))
    finally:
        # Closed explicitly: the store holds a sqlite handle, and a leaked one per request would
        # exhaust file descriptors on a page that polls.
        store.close()


def _audit(request: web.Request, operation: str, outcome: str, resources: str) -> None:
    """SEL-audit a review action.

    §3 requires an audit of accepts, and `proposals.accept` already logs one. This adds the HTTP
    caller's identity, which that layer cannot see — "who accepted this" is the question an audit
    trail
    exists to answer, and the store only knows the actor CLASS.
    """
    try:
        import personalclaw.dashboard.handlers as _pkg

        _pkg.sel().log_api_access(
            caller=str(request.get("app") or request.get("user") or "unknown"),
            operation=operation,
            outcome=outcome,
            source="dashboard",
            resources=resources,
        )
    except Exception:
        logger.debug("learning SEL audit failed", exc_info=True)


def register_learning_routes(app: web.Application) -> None:
    """Register /api/learning/* — the Proposal Inbox and the staging panel.

    Literal paths register BEFORE `/{id}` so aiohttp does not capture `staging` as a proposal
    id — the ordering landmine S67 and S70 each paid for once.
    """
    app.router.add_get("/api/learning/staging/week", api_learning_staging_week)
    app.router.add_get("/api/learning/proposals", api_learning_proposals)
    app.router.add_get("/api/learning/proposals/{id}", api_learning_proposal)
    app.router.add_post("/api/learning/proposals/{id}/accept", api_learning_proposal_accept)
    app.router.add_delete("/api/learning/proposals/{id}", api_learning_proposal_reject)
