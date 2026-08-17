"""Evals routes — the judge tier-recommendation table (EVALUATION-SUBSTRATE §6 / ES-4).

GET /api/evals/judge-bench   the newest benchmark run's table + recommendations

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

logger = logging.getLogger(__name__)


def _error(code: str, message: str, status: int) -> web.Response:
    """The shared error envelope. `code` is a stable snake token an agent may branch on."""
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


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
        return _error(
            "evals_disabled",
            "The eval substrate is off. Turn on `evals.enabled` to publish benchmark results.",
            404,
        )
    from personalclaw.evals.judge_bench import latest_bench_view

    try:
        view = latest_bench_view()
    except Exception:
        logger.warning("judge bench view failed", exc_info=True)
        return _error("judge_bench_unreadable", "The benchmark artifacts could not be read.", 500)
    if view is None:
        return _error(
            "judge_bench_absent",
            "No judge benchmark has run yet. Run `personalclaw judge-bench` to produce one.",
            404,
        )
    _audit(request, "evals_judge_bench", "read", f"bench_id={view.get('bench_id')}")
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


def register_evals_routes(app: web.Application) -> None:
    """Register /api/evals/* — the judge tier-recommendation table."""
    app.router.add_get("/api/evals/judge-bench", api_evals_judge_bench)
