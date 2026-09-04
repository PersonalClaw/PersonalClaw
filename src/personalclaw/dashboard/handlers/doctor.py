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

from personalclaw.http_errors import json_error
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
        return json_error("doctor_disabled", status=404)
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
        return json_error("doctor_disabled", status=404)
    capability = request.match_info.get("capability", "")
    result = await run_capability(capability, _ctx(request))
    if result.get("unknown"):
        return json_error(
            "unknown_capability",
            message=f"No such capability: {capability}.",
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
        return json_error("doctor_disabled", status=404)
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
        return json_error("doctor_disabled", status=404)
    fix_id = request.match_info.get("fix_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not (isinstance(body, dict) and body.get("confirm") is True):
        return json_error("confirm_required", status=400)
    from personalclaw.resilience import fixes as _fixes

    if _fixes.get_fix(fix_id) is None:
        return json_error("unknown_fix", message=f"No such fix: {fix_id}.", status=404)
    result = await asyncio.to_thread(_fixes.apply_fix, fix_id)
    return web.json_response(result)


# ── Surfacing simulator (§3.1) — zero side effects, zero LLM calls ────────────


async def api_doctor_simulate_surfacing(request: web.Request) -> web.Response:
    """POST /api/doctor/simulate/surfacing {text} — dry-run the skill scorer in
    explain mode: per-candidate keyword/semantic scores, thresholds, and the
    inclusion/exclusion reason. Runs the SAME deterministic scorer a real turn runs."""
    if not _resilience_cfg().doctor_enabled:
        return json_error("doctor_disabled", status=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text", "")) if isinstance(body, dict) else ""
    if not text.strip():
        return json_error("text_required", status=400)

    def _simulate() -> list[dict]:
        from personalclaw.config.loader import AppConfig
        from personalclaw.skills.loader import SkillsLoader, _suppressed_producers
        from personalclaw.skills.surfacing import surface_skills

        cfg = AppConfig.load()
        skills = SkillsLoader().list_skills(with_usage=True)
        # FS-6: mirror the real turn — a feedback-suppressed skill is surfaced as a
        # withheld row so the simulator explains why it did not surface.
        rows = surface_skills(
            text,
            skills,
            max_skills=cfg.skills.max_triggered,
            suppressed=_suppressed_producers(),
            explain=True,
        )
        return list(rows)  # type: ignore[arg-type]

    return web.json_response({"query": text, "candidates": await asyncio.to_thread(_simulate)})


# ── Automation would-execute simulator (§3.3) — zero side effects, zero LLM calls ──
#
# The sibling of the surfacing simulator above, on the same trust surface: "simulate a query"
# and "simulate a trigger" side by side, so a user can see what an automation WOULD do before
# granting it unattended operation.
#
# 🔴 EVERY FACT BELOW IS READ FROM A SHIPPED RESOLVER, never re-derived. §3.3 is explicit that
# the execution machinery belongs to AUTOMATION-SUBSTRATE (AUTO-R15 / `automation_run(dry_run)`)
# and that this plan's remainder is only the unified RENDERING of it. A second next-fire
# calculator or a second capability evaluator here would be a surface that disagrees with the
# scheduler — the exact drift `schedule_view.describe_cadence` was written to end.
#
#   next_fire         → `triggers.schedule_view._next_run_ts` (persisted `next_fire_at` first,
#                       `triggers.arm.next_fire` only for an unarmed row) + `describe_cadence`
#   action_config     → `triggers.schedule_view._inline_action` + `triggers.secrets.references`
#                       (secret keys are NAMED, never resolved) + the provider's own renderer
#   session_key       → `triggers.wakeup.session_key_for`
#   capability_grants → `triggers.screen.requested_capabilities` + `provider_is_read_only` +
#                       `unfenced_actions` (the same three the firepath capability gate calls)
#   observe_mode      → `triggers.tools.run(dry_run=True, runner=None)`, which is exactly what
#                       `mcp_automation`'s `automation_run` dispatches a `dry_run` to, plus the
#                       T9 honesty check `ActionProvider.supports_dry_run`

#: The five facts §3.3 names, as data so a test can assert the response is TOTAL over them
#: rather than spot-checking three and trusting the rest. A fact dropped from the payload
#: reddens the rail instead of quietly becoming a blank row on the trust surface.
WOULD_EXECUTE_FACTS: tuple[str, ...] = (
    "next_fire",
    "action_config",
    "session_key",
    "capability_grants",
    "observe_mode",
)

#: Marker substituted for a `{{secret:KEY}}` reference in the rendered config. The reference is
#: NAMED and never resolved: a would-execute preview that read the credential store would put a
#: live token one JSON response away from a browser, and the user's question ("which credential
#: does this use?") is answered by the key, not by its value.
_SECRET_MASK = "«secret:{key} — not resolved by a preview»"


def _redact_leaf(value: Any) -> Any:
    """One config leaf, screened for credentials.

    Screens each leaf ONCE, at entry, rather than redacting a composed line later:
    `redact_credentials` is not idempotent over concatenated text, and a trailing chokepoint
    over a joined `key=value` line destroys the field NAME as well as the value.
    """
    from personalclaw.security import redact_credentials
    from personalclaw.triggers.secrets import SECRET_REF_RE

    if isinstance(value, str):
        named = SECRET_REF_RE.sub(lambda m: _SECRET_MASK.format(key=m.group(1)), value)
        return redact_credentials(named)[0]
    if isinstance(value, dict):
        return {k: _redact_leaf(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_leaf(v) for v in value]
    return value


def _next_fire_fact(trigger: Any, *, now: float) -> dict[str, Any]:
    """Fact 1 — the resolved next fire, from the resolver the SCHEDULER acts on."""
    from personalclaw.triggers.schedule_view import _next_run_ts, describe_cadence
    from personalclaw.triggers.service import to_iso

    persisted = str(getattr(trigger, "next_fire_at", "") or "")
    epoch = _next_run_ts(trigger, now=now)
    return {
        "cadence": describe_cadence(trigger),
        "at": to_iso(epoch) if epoch else "",
        "epoch": epoch,
        # WHERE the answer came from. A row the tick has armed and a row this preview computed
        # are different states, and collapsing them would hide an unarmed (inert) automation —
        # `arm.needs_arming`'s whole population.
        "source": "armed" if persisted else ("computed" if epoch else "none"),
        "armed": bool(persisted),
    }


def _action_config_fact(trigger: Any) -> dict[str, Any]:
    """Fact 2 — the action provider plus its `action_config`, rendered.

    "Rendered" means three things, each from the shipped path: `{{secret:KEY}}` references are
    NAMED not resolved, credential-shaped literals are redacted, and a `run-prompt`'s `$vars`
    are substituted into its saved template by the SAME `render_saved_prompt` the provider
    calls — so a template whose required variable is unset previews as the render error the
    real fire would hit, instead of as a plausible prompt.
    """
    from personalclaw.triggers.schedule_view import _inline_action
    from personalclaw.triggers.secrets import references

    action = _inline_action(trigger)
    provider_name = str(action.get("provider") or "")
    config = action.get("config")
    config = config if isinstance(config, dict) else {}

    values = config.get("vars")
    if not isinstance(values, dict):
        values = {}

    fact: dict[str, Any] = {
        "provider": provider_name,
        "config": _redact_leaf(config),
        "vars": _redact_leaf(values),
        "secret_refs": references(config),
        "rendered": "",
        "render_error": "",
    }
    if provider_name == "run-prompt":
        prompt_id = str(config.get("prompt_id") or "").strip()
        if prompt_id:
            from personalclaw.action_providers.run_prompt_provider import render_saved_prompt

            try:
                fact["rendered"] = _redact_leaf(render_saved_prompt(prompt_id, values or None))
            except (LookupError, ValueError) as exc:
                # The honest answer, and the one the real fire produces: `render_saved_prompt`
                # normalizes a missing required variable to ValueError, and the provider turns
                # that into a failed ActionResult. Reporting the error is the point — a preview
                # that swallowed it would promise a run that cannot happen.
                fact["render_error"] = str(exc)[:400]
    return fact


def _session_key_fact(trigger: Any) -> dict[str, Any]:
    """Fact 3 — the session key this fire would target."""
    from personalclaw.triggers.schedule_view import session_key_of
    from personalclaw.triggers.wakeup import session_key_for

    declared = str(getattr(trigger, "session", "") or "")
    pinned = session_key_of(trigger)
    mode = (
        "conversation"
        if declared.startswith("conversation:")
        else ("pinned" if pinned else "fresh")
    )
    return {
        "key": session_key_for(str(getattr(trigger, "id", "")), session=declared),
        "declared": declared,
        "mode": mode,
    }


def _capability_fact(trigger: Any) -> dict[str, Any]:
    """Fact 4 — the frozen capability set, evaluated against what this fire requests.

    Runs the firepath capability gate's own three calls rather than reading `capabilities` and
    calling it a day: the interesting answer is not what the row DECLARES, it is whether the
    declaration covers the action. Decision 7's read-only default is part of that — a read-only
    provider is granted with no `capabilities` block at all, and rendering such a trigger as
    "nothing permitted" would send users widening allowlists they never needed.
    """
    from personalclaw.triggers.screen import (
        provider_is_read_only,
        requested_capabilities,
        unfenced_actions,
    )

    declared = getattr(trigger, "capabilities", None)
    declared = dict(declared) if isinstance(declared, dict) else {}
    requested = requested_capabilities(trigger)
    needs_fence = {
        key: [v for v in values if not (key == "providers" and provider_is_read_only(v))]
        for key, values in requested.items()
    }
    needs_fence = {k: v for k, v in needs_fence.items() if v}
    refused = unfenced_actions(declared, requested=needs_fence) if needs_fence else []
    return {
        "declared": declared,
        "requested": requested,
        "needs_fence": needs_fence,
        "refused": [{"key": k, "value": v, "reason": r} for k, v, r in refused],
        "granted": not refused,
    }


def _observe_mode_fact(store: Any, trigger: Any) -> dict[str, Any]:
    """Fact 5 — AUTOMATION-SUBSTRATE's dry fire, plus the T9 honesty verdict.

    `tools.run(dry_run=True, runner=None)` is the local answer `automation_run` gives a
    `dry_run` (`mcp_automation`: "a `dry_run` needs no turn and is answered locally"). It walks
    the gate plan and returns BEFORE the runner is consulted, which is the property that makes
    this safe to offer from a browser button.

    `supports_dry_run` is the T9 rule: only the spawn-based LLM providers have an observe mode,
    so for `bash`/`run-script`/`webhook` this is a PREVIEW of what would run and says so. A
    panel that labelled a deterministic provider's description "observe-mode result" would be
    promising a safety property the provider does not have.
    """
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )
    from personalclaw.triggers.schedule_view import _inline_action
    from personalclaw.triggers.tools import run as automation_run

    # Same idiom as `proposals_contract`: the registry is populated lazily on first action
    # execution, so a read-only surface that skipped this would report EVERY built-in provider
    # as unknown and label every observe mode "preview" — a false negative that reads as a
    # deliberate T9 refusal.
    _ensure_default_providers_registered()
    provider_name = str(_inline_action(trigger).get("provider") or "")
    provider = get_action_provider(provider_name) if provider_name else None
    supported = bool(getattr(provider, "supports_dry_run", False))
    result = automation_run(
        store, trigger_id=str(getattr(trigger, "id", "")), dry_run=True, runner=None
    )
    return {
        "provider": provider_name,
        # Distinguish "this provider has no observe mode" from "we could not find the provider
        # at all" — the second is a broken row, and reporting it as the first would read as a
        # deliberate design decision.
        "provider_known": provider is not None,
        "supported": supported,
        "mode": "observe" if supported else "preview",
        "executed": False,
        "ok": bool(result.ok),
        "detail": result.text,
        "gate_plan": dict(result.data.get("plan") or {}),
    }


async def api_doctor_simulate_automation(request: web.Request) -> web.Response:
    """POST /api/doctor/simulate/automation {trigger_id} — what this automation WOULD do.

    The §3.3 would-execute description, beside the surfacing simulator: resolved next-fire,
    the rendered `action_config` with `$vars` substituted, the target session key, the
    capability grants, and the observe-mode result from AUTOMATION-SUBSTRATE's dry fire.
    Read-only by construction — nothing executes, no credential is resolved, no model is
    called, and the trigger row is never written."""
    from personalclaw.http_errors import json_error

    if not _resilience_cfg().doctor_enabled:
        return json_error("doctor_disabled", status=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    trigger_id = str(body.get("trigger_id", "")).strip() if isinstance(body, dict) else ""
    if not trigger_id:
        return json_error("trigger_id_required", status=400)

    def _describe() -> dict[str, Any] | None:
        from personalclaw.config.loader import config_dir
        from personalclaw.triggers.store import TriggerStore

        store = TriggerStore(base_dir=config_dir())
        row = store.get(trigger_id)
        if row is None:
            return None
        trigger = row.trigger
        now = time.time()
        return {
            "trigger": {
                "id": trigger.id,
                "name": trigger.name,
                "kind": trigger.kind,
                "enabled": bool(trigger.enabled),
                "state": str(getattr(trigger, "state", "") or ""),
                # AUTO-R15's typed issue records, verbatim — including `closest`, which is the
                # whole point of that contract: an agent that wrote `debounce_seconds` is told
                # which key it meant instead of being told its trigger is invalid.
                "ok": bool(row.ok),
                "issues": [i.to_dict() for i in row.issues],
            },
            "next_fire": _next_fire_fact(trigger, now=now),
            "action_config": _action_config_fact(trigger),
            "session_key": _session_key_fact(trigger),
            "capability_grants": _capability_fact(trigger),
            "observe_mode": _observe_mode_fact(store, trigger),
        }

    described = await asyncio.to_thread(_describe)
    if described is None:
        return json_error("unknown_trigger", status=404, error_extra={"trigger_id": trigger_id})
    # The wire shape spelled out AT the call site rather than forwarded as a name: the
    # error-envelope census refuses an unreadable payload precisely because that is where a
    # flat envelope would hide, and the same rule makes the response contract greppable from
    # the route. The five keys are `WOULD_EXECUTE_FACTS` — a test asserts the set is total.
    return web.json_response(
        {
            "trigger": described["trigger"],
            "next_fire": described["next_fire"],
            "action_config": described["action_config"],
            "session_key": described["session_key"],
            "capability_grants": described["capability_grants"],
            "observe_mode": described["observe_mode"],
            "dry_run": True,
        }
    )


# ── Per-provider selftest (§1.4) — a tiny REAL inference, user-click only ──────


async def api_provider_selftest(request: web.Request) -> web.Response:
    """POST /api/model-providers/{name}/selftest — dispatch a tiny real inference per
    declared capability (one-token chat / short embed), instead of the availability
    guess ``test_connection`` gives. User-click only (it costs tokens/compute); never
    run by a background job. Hard-timeout-bounded per capability."""
    if not _resilience_cfg().doctor_enabled:
        return json_error("doctor_disabled", status=404)
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


# ── Remediation engine (§4) ───────────────────────────────────────────────────


async def api_doctor_remediation(request: web.Request) -> web.Response:
    """GET /api/doctor/remediation — current health score, a dry-run plan preview, and
    the recent remediation-run ledger."""
    if not _resilience_cfg().doctor_enabled:
        return json_error("doctor_disabled", status=404)

    def _snapshot() -> dict:
        import time as _t

        from personalclaw.resilience import remediation as _rem

        deficits = _rem.measure_deficits()
        cfg = _resilience_cfg().remediation
        preview = _rem.run_remediation(
            target_score=float(cfg.target_score),
            max_cost_usd=cfg.max_cost_usd,
            now=_t.time(),
            dry_run=True,
        )
        return {
            "score": _rem.health_score(deficits),
            "target_score": cfg.target_score,
            "deficits": [
                {
                    "key": d.key,
                    "count": d.count,
                    "penalty": round(d.penalty, 1),
                    "reachable": d.reachable,
                }
                for d in deficits
            ],
            "plan": preview.jobs,
            "recent_runs": _rem.recent_runs(10),
        }

    return web.json_response(await asyncio.to_thread(_snapshot))


async def api_doctor_remediation_run(request: web.Request) -> web.Response:
    """POST /api/doctor/remediation/run — run the engine now (confirm-gated). SEL-audited."""
    if not _resilience_cfg().doctor_enabled:
        return json_error("doctor_disabled", status=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not (isinstance(body, dict) and body.get("confirm") is True):
        return json_error("confirm_required", status=400)

    def _run() -> dict:
        import time as _t

        from personalclaw.resilience import remediation as _rem

        cfg = _resilience_cfg().remediation
        result = _rem.run_remediation(
            target_score=float(cfg.target_score), max_cost_usd=cfg.max_cost_usd, now=_t.time()
        )
        from personalclaw.sel import sel

        sel().log_tool_invocation(
            session_key="dashboard",
            agent="personalclaw",
            source="dashboard",
            tool_name="doctor_remediation_run",
            tool_kind="maintenance",
            outcome="ok",
            metadata={
                "score_before": result.score_before,
                "score_after": result.score_after,
                "stopped": result.stopped_reason,
            },
        )
        return {
            "score_before": result.score_before,
            "score_after": result.score_after,
            "jobs": result.jobs,
            "stopped_reason": result.stopped_reason,
        }

    return web.json_response(await asyncio.to_thread(_run))


# ── Crash artifact detail (§6.5) ──────────────────────────────────────────────


async def api_doctor_crash(request: web.Request) -> web.Response:
    """GET /api/doctor/crash/{filename} — the full JSON of one crash artifact."""
    if not _resilience_cfg().doctor_enabled:
        return json_error("doctor_disabled", status=404)
    from personalclaw.resilience import crashes as _crashes

    filename = request.match_info.get("filename", "")
    data = await asyncio.to_thread(_crashes.read_crash, filename)
    if data is None:
        return json_error("not_found", message="No such crash artifact.", status=404)
    return web.json_response(data)
