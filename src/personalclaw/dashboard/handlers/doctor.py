"""Doctor + degraded-mode HTTP surface (PLATFORM-RESILIENCE §1, §5).

``GET /api/doctor`` runs every registered probe grouped by capability (cached 30s
so a dashboard poll can't turn the probe suite into a load source), and
``GET /api/doctor/{capability}`` re-runs just one capability's probes (uncached —
it's an explicit user re-probe of one card). ``GET /api/resilience/degraded`` reports
each model-dependent surface's no-model floor + availability, re-evaluated live (and
firing down/recovery transition notifications). All three are read-only.

Both surfaces are guard-class gated: ``resilience.doctor_enabled`` and
``resilience.degraded_indicator`` (a missing/unknown value keeps them ON).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from aiohttp import web

from personalclaw.resilience import degraded
from personalclaw.resilience.doctor import DoctorContext, run_capability, run_doctor

# Full-report cache (§11 risk mitigation: 30s TTL so the dashboard rollup poll
# reuses one run instead of re-probing every capability each tick).
_DOCTOR_TTL = 30.0
_doctor_cache: Optional[dict[str, Any]] = None
_doctor_cache_ts = 0.0


def _ctx(request: web.Request) -> DoctorContext:
    """Build the probe context from the live app: the dashboard state and the port
    the gateway bound (both optional — probes degrade to read-only file access)."""
    state = request.app.get("state")
    try:
        port = int(request.app.get("port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    return DoctorContext(state=state, port=port)


def _resilience_cfg():
    """The resilience config section (fresh read — cheap, and it's guard-class)."""
    from personalclaw.config.loader import AppConfig

    return AppConfig.load().resilience


async def api_doctor(request: web.Request) -> web.Response:
    """GET /api/doctor — all probes, grouped by capability, cached 30s."""
    if not _resilience_cfg().doctor_enabled:
        return web.json_response(
            {"error": {"code": "doctor_disabled", "message": "the Doctor surface is turned off"}},
            status=404,
        )
    global _doctor_cache, _doctor_cache_ts
    now = time.monotonic()
    if _doctor_cache is not None and now - _doctor_cache_ts < _DOCTOR_TTL:
        return web.json_response(_doctor_cache)
    report = await run_doctor(_ctx(request))
    _doctor_cache = report
    _doctor_cache_ts = now
    return web.json_response(report)


async def api_doctor_capability(request: web.Request) -> web.Response:
    """GET /api/doctor/{capability} — re-run one capability's probes (uncached)."""
    if not _resilience_cfg().doctor_enabled:
        return web.json_response(
            {"error": {"code": "doctor_disabled", "message": "the Doctor surface is turned off"}},
            status=404,
        )
    capability = request.match_info.get("capability", "")
    result = await run_capability(capability, _ctx(request))
    if result.get("unknown"):
        return web.json_response(
            {
                "error": {
                    "code": "unknown_capability",
                    "message": f"no such capability: {capability}",
                }
            },
            status=404,
        )
    return web.json_response(result)


async def api_degraded(request: web.Request) -> web.Response:
    """GET /api/resilience/degraded — per-surface no-model floor + availability.

    Re-evaluates live each call (cheap, no-instantiate ``can_resolve_use_case``
    probes) and fires a down/recovery notification on a surface changing state, via
    the live dashboard state. Returns ``{surfaces: [{surface, available, floor,
    backlog, use_cases}], degraded: [surface, ...]}``.
    """
    if not _resilience_cfg().degraded_indicator:
        return web.json_response({"surfaces": [], "degraded": []})
    state = request.app.get("state")
    rows = await _run_degraded(state)
    return web.json_response(
        {"surfaces": rows, "degraded": [r["surface"] for r in rows if not r["available"]]}
    )


async def _run_degraded(state: object) -> list[dict]:
    """Evaluate the degraded contracts off the event loop (backlog probes touch
    sqlite/JSON stores); notify on transitions via the live state."""
    return await asyncio.to_thread(degraded.evaluate, notify=True, state=state)


# ── Confirm-gated fixes (§2) ──────────────────────────────────────────────────


async def api_doctor_fixes(request: web.Request) -> web.Response:
    """GET /api/doctor/fixes — the fix catalog with read-only dry-previews."""
    if not _resilience_cfg().doctor_enabled:
        return web.json_response(
            {"error": {"code": "doctor_disabled", "message": "the Doctor surface is turned off"}},
            status=404,
        )
    from personalclaw.resilience import fixes as _fixes

    def _catalog() -> list[dict]:
        out = []
        for fx in _fixes.all_fixes():
            try:
                preview = fx.dry_preview()
            except Exception:
                preview = "(preview unavailable)"
            out.append({"id": fx.id, "title": fx.title, "impact": fx.impact, "preview": preview})
        return out

    return web.json_response({"fixes": await asyncio.to_thread(_catalog)})


async def api_doctor_fix_apply(request: web.Request) -> web.Response:
    """POST /api/doctor/fix/{fix_id} — apply a confirm-gated fix.

    Requires ``{confirm: true}`` (the two-step armed pattern) so a stray request can't
    mutate. Every application is SEL-audited inside ``apply_fix``.
    """
    if not _resilience_cfg().doctor_enabled:
        return web.json_response(
            {"error": {"code": "doctor_disabled", "message": "the Doctor surface is turned off"}},
            status=404,
        )
    fix_id = request.match_info.get("fix_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not (isinstance(body, dict) and body.get("confirm") is True):
        return web.json_response(
            {"error": {"code": "confirm_required", "message": 'fix requires {"confirm": true}'}},
            status=400,
        )
    from personalclaw.resilience import fixes as _fixes

    if _fixes.get_fix(fix_id) is None:
        return web.json_response(
            {"error": {"code": "unknown_fix", "message": f"no such fix: {fix_id}"}}, status=404
        )
    result = await asyncio.to_thread(_fixes.apply_fix, fix_id)
    return web.json_response(result)


# ── Surfacing simulator (§3.1) — zero side effects, zero LLM calls ────────────


async def api_doctor_simulate_surfacing(request: web.Request) -> web.Response:
    """POST /api/doctor/simulate/surfacing {text} — dry-run the skill scorer in
    explain mode: per-candidate keyword/semantic scores, thresholds, and the
    inclusion/exclusion reason. Runs the SAME deterministic scorer a real turn runs."""
    if not _resilience_cfg().doctor_enabled:
        return web.json_response(
            {"error": {"code": "doctor_disabled", "message": "the Doctor surface is turned off"}},
            status=404,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text", "")) if isinstance(body, dict) else ""
    if not text.strip():
        return web.json_response(
            {"error": {"code": "text_required", "message": "a query text is required"}}, status=400
        )

    def _simulate() -> list[dict]:
        from personalclaw.config.loader import AppConfig
        from personalclaw.skills.loader import SkillsLoader
        from personalclaw.skills.surfacing import surface_skills

        cfg = AppConfig.load()
        skills = SkillsLoader().list_skills(with_usage=True)
        rows = surface_skills(text, skills, max_skills=cfg.skills.max_triggered, explain=True)
        return list(rows)  # type: ignore[arg-type]

    return web.json_response({"query": text, "candidates": await asyncio.to_thread(_simulate)})


# ── Per-provider selftest (§1.4) — a tiny REAL inference, user-click only ──────


async def api_provider_selftest(request: web.Request) -> web.Response:
    """POST /api/model-providers/{name}/selftest — dispatch a tiny real inference per
    declared capability (one-token chat / short embed), instead of the availability
    guess ``test_connection`` gives. User-click only (it costs tokens/compute); never
    run by a background job. Hard-timeout-bounded per capability."""
    if not _resilience_cfg().doctor_enabled:
        return web.json_response(
            {"error": {"code": "doctor_disabled", "message": "the Doctor surface is turned off"}},
            status=404,
        )
    name = request.match_info.get("name", "")
    result = await _run_selftest(name)
    return web.json_response(result)


async def _run_selftest(name: str) -> dict:
    """Best-effort per-capability real-inference probe. Each capability is timed out
    and its failure isolated — the result maps capability → {ok, detail}."""
    import asyncio as _asyncio

    from personalclaw.providers.provider_bridge import can_resolve_use_case

    out: dict[str, dict] = {}

    async def _timed(coro, timeout: float = 15.0):
        return await _asyncio.wait_for(coro, timeout=timeout)

    # chat — one short completion via the resolved chat provider (async).
    if can_resolve_use_case("chat"):
        try:
            from personalclaw.llm_helpers import one_shot_completion

            txt = await _timed(one_shot_completion("ping", use_case="background"))
            out["chat"] = {"ok": bool(txt is not None), "detail": "completion returned"}
        except Exception as exc:
            out["chat"] = {"ok": False, "detail": str(exc)[:200]}

    # embedding — a short embed via the active embedder (sync fn, off-thread).
    if can_resolve_use_case("embedding"):
        try:
            from personalclaw.skills.surfacing import _active_embedder

            fn, _model = _active_embedder()
            vec = await _timed(_asyncio.to_thread(lambda: fn("ping") if fn else None))
            out["embedding"] = {
                "ok": bool(vec),
                "detail": f"{len(vec)} dims" if vec else "no vector",
            }
        except Exception as exc:
            out["embedding"] = {"ok": False, "detail": str(exc)[:200]}

    if not out:
        return {"provider": name, "capabilities": {}, "detail": "no testable capability bound"}
    return {"provider": name, "capabilities": out}


# ── Crash artifact detail (§6.5) ──────────────────────────────────────────────


async def api_doctor_crash(request: web.Request) -> web.Response:
    """GET /api/doctor/crash/{filename} — the full JSON of one crash artifact."""
    if not _resilience_cfg().doctor_enabled:
        return web.json_response(
            {"error": {"code": "doctor_disabled", "message": "the Doctor surface is turned off"}},
            status=404,
        )
    from personalclaw.resilience import crashes as _crashes

    filename = request.match_info.get("filename", "")
    data = await asyncio.to_thread(_crashes.read_crash, filename)
    if data is None:
        return web.json_response(
            {"error": {"code": "not_found", "message": "no such crash artifact"}}, status=404
        )
    return web.json_response(data)
