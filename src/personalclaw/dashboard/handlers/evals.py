"""Evals routes — the judge tier table (§6 / ES-4), pre-registered studies (§2 / ES-5)
and the harness ablation report (§3.1 / ES-7).

GET /api/evals/judge-bench           the newest benchmark run's table + recommendations
GET /api/evals/studies               one row per pre-registered study
GET /api/evals/studies/{study_id}    one study's verdict, agreement rate and per-run rows
GET /api/evals/ablation              the newest keep/remove/lighten report + the registry

**Read-only on purpose.** The full shipped matrix is 540 judge calls; a POST that started
one would hold a request open for minutes and spend real money on a click. So the RUN is
`personalclaw judge-bench` — a deliberate, previewable invocation with its own spend
preflight — and this route publishes what it produced. That is also §6's posture verbatim:
the harness recommends, the human rebinds on the existing Models panel.

404 when nothing has run, with a distinct code from "evals disabled": "no benchmark yet"
and "the feature is off" send a user to two different places, and one code for both would
make the panel's empty state a guess.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw.http_errors import json_error

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    """The `evals.enabled` kill-switch (§10).

    Fails CLOSED on an unreadable config: this surface publishes measurement artifacts
    from a home directory, so "we could not read the switch" must not resolve to "serve
    it". The panel renders the refusal rather than an empty table.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().evals.enabled)
    except Exception:
        logger.debug("evals enabled check failed — treating as disabled", exc_info=True)
        return False


async def api_evals_judge_bench(request: web.Request) -> web.Response:
    """GET /api/evals/judge-bench — the newest tier-recommendation table.

    The whole payload is computed by `judge_bench` and merely serialized here: adequacy,
    the floors it was judged against, and the inadequacy reasons all arrive decided. A
    frontend that re-derived "is this tier good enough" would eventually disagree with the
    harness, and the copy shipping the permissive answer would be the UI.
    """
    if not _enabled():
        return json_error(
            "evals_disabled",
            message="The eval substrate is off. Turn on `evals.enabled` to publish "
            "benchmark results.",
            status=404,
        )
    from personalclaw.evals.judge_bench import latest_bench_view

    try:
        view = latest_bench_view()
    except Exception:
        logger.warning("judge bench view failed", exc_info=True)
        return json_error(
            "judge_bench_unreadable",
            message="The benchmark artifacts could not be read.",
            status=500,
        )
    if view is None:
        return json_error(
            "judge_bench_absent",
            message="No judge benchmark has run yet. Run `personalclaw judge-bench` "
            "to produce one.",
            status=404,
        )
    _audit(request, "evals_judge_bench", "read", f"bench_id={view.get('bench_id')}")
    return web.json_response(view)


async def api_evals_studies(request: web.Request) -> web.Response:
    """GET /api/evals/studies — one compact row per pre-registered study (§2.4 / ES-5).

    Read-only for the same reason the bench table is: a k=5 paired study is ten template
    runs plus six judge calls per pair, so starting one from a click would spend real money
    and hold the request open. Registration is a proposal-queue item and the run is a
    deliberate invocation; this publishes what they produced.
    """
    if not _enabled():
        return json_error(
            "evals_disabled",
            message="The eval substrate is off. Turn on `evals.enabled` to publish "
            "study results.",
            status=404,
        )
    from personalclaw.evals.studies import study_index

    try:
        rows = study_index()
    except Exception:
        logger.warning("study index failed", exc_info=True)
        return json_error(
            "studies_unreadable",
            message="The study artifacts could not be read.",
            status=500,
        )
    _audit(request, "evals_studies", "read", f"count={len(rows)}")
    return web.json_response({"studies": rows})


async def api_evals_study(request: web.Request) -> web.Response:
    """GET /api/evals/studies/{study_id} — one study's verdict, agreement and per-run rows.

    🔴 The payload comes from `studies.study_view`, which deliberately omits the rubric TEXT
    and the ``locked/`` checks. That omission is a §2.2 control, not a size optimization: a
    dashboard is one `curl` away from an agent's context, so a route that served the hidden
    checks would defeat the clause the whole study is built around. The rubric's HASH is
    published instead — enough to prove the pin, not enough to satisfy it.
    """
    if not _enabled():
        return json_error(
            "evals_disabled",
            message="The eval substrate is off. Turn on `evals.enabled` to publish "
            "study results.",
            status=404,
        )
    study_id = request.match_info.get("study_id", "")
    from personalclaw.evals.studies import study_view

    try:
        view = study_view(study_id)
    except Exception:
        logger.warning("study view failed for %s", study_id, exc_info=True)
        return json_error(
            "studies_unreadable",
            message="The study artifacts could not be read.",
            status=500,
        )
    if view is None:
        return json_error(
            "study_absent",
            message=f"No study {study_id!r} is registered.",
            status=404,
        )
    _audit(request, "evals_study", "read", f"study_id={study_id}")
    return web.json_response(view)


def _audit(request: web.Request, operation: str, outcome: str, resources: str) -> None:
    """SEL-log the read. The table names which model a judge should be bound to, so who
    read it is an audit question — best-effort, and never breaks the response."""
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
        logger.debug("evals SEL audit failed", exc_info=True)


async def api_evals_ablation(request: web.Request) -> web.Response:
    """GET /api/evals/ablation — the newest keep/remove/lighten report (ES-7 §3.1).

    Read-only for the same reason judge-bench is: a POST that started an ablation would hold
    a request open for a multi-cell matrix and spend real money on a click. The RUN is
    ``personalclaw ablation`` (with its own preflight) or the monthly cadence; this publishes
    what those produced.

    A ``remove`` verdict ALSO reaches the user as a LEARN-R9 retirement proposal in the
    inbox — that is the actionable surface. This route is the evidence behind it, and the only
    surface a ``keep``/``lighten`` verdict has at all.
    """
    if not _enabled():
        return json_error(
            "evals_disabled",
            message="The eval substrate is off. Turn on `evals.enabled` to publish "
            "ablation reports.",
            status=404,
        )
    from personalclaw.evals.ablation import latest_ablation_view

    try:
        view = latest_ablation_view()
    except Exception:
        logger.warning("ablation view failed", exc_info=True)
        return json_error(
            "ablation_unreadable",
            message="The ablation artifacts could not be read.",
            status=500,
        )
    if view is None:
        # A distinct code from "evals disabled" and from "nothing registered": those send a
        # user to three different places (the switch, the registry, and waiting for the
        # cadence), and one code for all of them would make the panel's empty state a guess.
        return json_error(
            "ablation_absent",
            message="No ablation has run yet. Register a component in "
            "`evals/ablation_registry.json` and run `personalclaw ablation --force`.",
            status=404,
        )
    _audit(request, "evals_ablation", "read", f"matrix_id={view['report'].get('matrix_id')}")
    return web.json_response(view)


async def api_evals_retrieval(request: web.Request) -> web.Response:
    """GET /api/evals/retrieval — the newest per-arm P@k/R@k table for BOTH stores (§5).

    Read-only, like every other route here, but for a different reason than judge-bench's:
    retrieval costs no model calls, yet §5.1's constraint is that the harness never writes
    to knowledge.db or memory.db, and the cheapest way to keep that promise on a web
    surface is to have no run trigger on it at all. The RUN is
    ``personalclaw retrieval-eval``.

    Both stores are always present in the payload, each with its own table — §5.1 runs them
    SEPARATELY and never shares a corpus, so a merged table would be the one shape the
    boundary forbids.
    """
    if not _enabled():
        return json_error(
            "evals_disabled",
            message="The eval substrate is off. Turn on `evals.enabled` to publish "
            "retrieval ablation reports.",
            status=404,
        )
    from personalclaw.evals import retrieval_bench as rb

    try:
        view = rb.latest_retrieval_view()
    except Exception:
        logger.warning("retrieval view failed", exc_info=True)
        return json_error(
            "retrieval_unreadable",
            message="The retrieval benchmark artifacts could not be read.",
            status=500,
        )
    runs = {kind: data.get("run") or "" for kind, data in (view.get("stores") or {}).items()}
    if not any(runs.values()):
        # Distinct from "evals disabled": that sends a user to the switch, this sends them to
        # the command. One code for both would make the panel's empty state a guess.
        return json_error(
            "retrieval_absent",
            message="No retrieval benchmark has run yet. Run "
            "`personalclaw retrieval-eval` to score both stores.",
            status=404,
        )
    _audit(request, "evals_retrieval", "read", f"runs={runs}")
    return web.json_response(view)


async def api_evals_retrieval_card(request: web.Request) -> web.Response:
    """GET /api/evals/retrieval/card?store=knowledge|memory — §5.2's hand-labeling card.

    The card is the human half of the qrels set: mined weak labels answer the tail, and the
    head queries need someone to say which results actually answer them. Read-only against
    the stores (the builder is wrapped in the same byte-identical rail the run uses); the
    only thing it writes is the benchmark file under ``evals/``.
    """
    if not _enabled():
        return json_error(
            "evals_disabled",
            message="The eval substrate is off. Turn on `evals.enabled` to label "
            "retrieval qrels.",
            status=404,
        )
    from personalclaw.evals import retrieval_bench as rb

    store_kind = (request.query.get("store") or "").strip()
    if store_kind not in rb.STORES:
        # No default: the two stores never share a corpus, so a card built for the wrong one
        # would collect labels against ids the other store has never heard of.
        return json_error(
            "store_required",
            message=f"Pass ?store= one of {', '.join(rb.STORES)}.",
            status=400,
        )
    try:
        card = rb.card_for_store(store_kind)
    except rb.StoreMutatedError as exc:
        return json_error("store_mutated", message=str(exc), status=500)
    except Exception:
        logger.warning("retrieval card failed for %s", store_kind, exc_info=True)
        return json_error(
            "card_unavailable",
            message=f"The {store_kind} store could not be read for labelling.",
            status=500,
        )
    _audit(request, "evals_retrieval_card", "read", f"store={store_kind}")
    return web.json_response(card)


async def api_evals_retrieval_labels(request: web.Request) -> web.Response:
    """POST /api/evals/retrieval/labels — save a completed hand-label card.

    Body: ``{"store": "...", "labels": {"<query>": ["<id>", ...]}}``. An EMPTY list for a
    query is a real judgement ("none of these answer it") and is stored as such — treating
    it as "nothing submitted" would silently re-inherit the mined weak label the human just
    overruled.
    """
    if not _enabled():
        return json_error(
            "evals_disabled",
            message="The eval substrate is off. Turn on `evals.enabled` to label "
            "retrieval qrels.",
            status=404,
        )
    from personalclaw.evals import retrieval_bench as rb

    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", message="Body must be JSON.", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_json", message="Body must be a JSON object.", status=400)
    store_kind = str(body.get("store") or "").strip()
    if store_kind not in rb.STORES:
        return json_error(
            "store_required",
            message=f"`store` must be one of {', '.join(rb.STORES)}.",
            status=400,
        )
    raw = body.get("labels")
    if not isinstance(raw, dict):
        return json_error(
            "labels_required",
            message='`labels` must be an object of {"<query>": ["<id>", ...]}.',
            status=400,
        )
    labels = {str(q): [str(i) for i in (v or [])] for q, v in raw.items() if str(q)}
    try:
        benchmark = rb.apply_labels_for_store(store_kind, labels)
    except rb.RetrievalBenchError as exc:
        _audit(request, "evals_retrieval_labels", "rejected", f"store={store_kind}")
        return json_error("labels_rejected", message=str(exc), status=400)
    hand = sum(1 for q in benchmark.queries if q.source == rb.SOURCE_HAND_LABEL)
    _audit(
        request,
        "evals_retrieval_labels",
        "ok",
        f"store={store_kind} labelled={hand} queries={len(benchmark.queries)}",
    )
    return web.json_response(
        {
            "ok": True,
            "store": store_kind,
            "queries": len(benchmark.queries),
            "hand_labelled": hand,
            "subject_sha256": benchmark.sha256,
        }
    )


def register_evals_routes(app: web.Application) -> None:
    """Register /api/evals/* — the judge tier table, the studies, the ablation report and
    the retrieval per-arm ablation (+ its hand-label card)."""
    app.router.add_get("/api/evals/judge-bench", api_evals_judge_bench)
    app.router.add_get("/api/evals/studies", api_evals_studies)
    app.router.add_get("/api/evals/studies/{study_id}", api_evals_study)
    app.router.add_get("/api/evals/ablation", api_evals_ablation)
    app.router.add_get("/api/evals/retrieval", api_evals_retrieval)
    app.router.add_get("/api/evals/retrieval/card", api_evals_retrieval_card)
    app.router.add_post("/api/evals/retrieval/labels", api_evals_retrieval_labels)
