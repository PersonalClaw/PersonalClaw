"""Agent routing (AGENT-ROUTING) — suggest the right specialist, never route silently.

When the user sends a message in a DEFAULT-agent chat and an installed specialist is
a clearly better fit, the classifier here produces a single non-blocking suggestion
the frontend renders as a chip ("Route to <agent>?"). The user consents; nothing about
the session changes until they click.

Deterministic-first, LLM never in the hot path (the calibrated shape from
``workflows/surfacing``):
  * **stage 1** — per-phrase keyword overlap over ``route_hints`` (gate 0.7,
    ``skills.loader._MIN_TRIGGER_OVERLAP``); a keyword-only hit additionally requires
    the matched phrase to carry ≥3 words (short-message spurious-match guard).
  * **stage 2** — cosine of the message vs a cached ``specialty + route_hints``
    embedding via the one unified embed path; skipped entirely when no embedder is
    bound. A suggestion needs the top score above the confidence gate AND a clear
    margin over the runner-up (≥0.1) so ambiguous fits stay silent.

Pure functions over the provider-agnostic ``AgentProfile`` metadata + the one
embedding path — zero per-provider logic. Never raises into the send path.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

from personalclaw.agents.defaults import is_reserved_agent

logger = logging.getLogger(__name__)

# Stage gates, mirroring the surfacing/skills family exactly.
_KEYWORD_GATE = 0.7
DEFAULT_MIN_CONFIDENCE = 0.62
# A confident suggestion must beat the runner-up by this margin (ambiguous → silent).
_MARGIN = 0.1
# A keyword-only match must come from a phrase of at least this many words, so a
# 2-word message can't spuriously clear the overlap gate (Risks §1).
_MIN_KEYWORD_PHRASE_WORDS = 3


@dataclass(frozen=True)
class RouteCandidate:
    agent: str  # AgentProfile config key
    specialty: str
    score: float
    method: str  # "keyword" | "embedding"


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _keyword_score(query: str, route_hints: str) -> tuple[float, int]:
    """Best per-phrase word-overlap of the query against comma-separated hints.

    Returns (best_ratio, words_in_best_phrase) so the caller can enforce the
    minimum-phrase-length guard on a keyword-only suggestion."""
    if not route_hints.strip():
        return 0.0, 0
    qwords = _words(query)
    best, best_len = 0.0, 0
    for phrase in route_hints.split(","):
        pwords = _words(phrase)
        if not pwords:
            continue
        ratio = len(pwords & qwords) / len(pwords)
        if ratio > best:
            best, best_len = ratio, len(pwords)
    return best, best_len


def eligible_candidates(cfg) -> list[tuple[str, str, str]]:
    """The routable agents: (name, specialty, route_hints) for each non-reserved
    config AgentProfile with routing metadata. The config layer is the source of
    truth (it's what chat binds); the marketplace copy is portable-only."""
    out: list[tuple[str, str, str]] = []
    for name, profile in (cfg.agents or {}).items():
        if is_reserved_agent(name):
            continue
        specialty = (getattr(profile, "specialty", "") or "").strip()
        route_hints = (getattr(profile, "route_hints", "") or "").strip()
        if not specialty and not route_hints:
            continue
        out.append((name, specialty, route_hints))
    return out


def _embed(text: str):
    """The active sync embed fn's vector for *text* (+ the model id), or (None, "")."""
    try:
        from personalclaw.embedding_providers.registry import (
            _active_embedding_spec,
            get_active_embed_fn,
        )

        fn = get_active_embed_fn()
    except Exception:
        return None, ""
    if fn is None:
        return None, ""
    model = ""
    try:
        spec = _active_embedding_spec()
        if spec:
            model = f"{spec[0]}:{spec[1]}"
    except Exception:
        model = ""
    try:
        return fn(text), model
    except Exception:
        return None, model


def classify(
    message: str,
    candidates: list[tuple[str, str, str]],
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    embed_cache: dict | None = None,
) -> RouteCandidate | None:
    """Return the single best routing candidate above the gate + margin, or None.

    ``embed_cache`` (when given) maps ``agent -> (model, vector)`` so specialty
    embeddings are computed once and reused; a model mismatch recomputes (staleness
    discipline from ``workflows/models``). Never raises.
    """
    message = (message or "").strip()
    if not message or not candidates:
        return None
    try:
        # Stage 1: keyword overlap over route_hints.
        kw_scored: list[tuple[float, int, str, str]] = []
        for name, specialty, hints in candidates:
            ratio, phrase_len = _keyword_score(message, hints)
            kw_scored.append((ratio, phrase_len, name, specialty))
        kw_scored.sort(key=lambda r: r[0], reverse=True)

        # Stage 2: embedding cosine over "specialty + hints" (skipped with no embedder).
        qvec, model = _embed(message)
        emb_scored: list[tuple[float, str, str]] = []
        if qvec is not None:
            for name, specialty, hints in candidates:
                cvec = _candidate_vector(name, specialty, hints, model, embed_cache)
                if cvec is None:
                    continue
                emb_scored.append((_cosine(qvec, cvec), name, specialty))
            emb_scored.sort(key=lambda r: r[0], reverse=True)

        # Prefer the embedding result (semantic) when it clears the confidence gate
        # AND beats its runner-up by the margin.
        if emb_scored:
            e_top = emb_scored[0]
            e_runner = emb_scored[1][0] if len(emb_scored) > 1 else 0.0
            if e_top[0] >= min_confidence and (e_top[0] - e_runner) >= _MARGIN:
                return RouteCandidate(
                    agent=e_top[1], specialty=e_top[2], score=e_top[0], method="embedding"
                )

        # Keyword-only fallback: needs the overlap gate, a ≥3-word matched phrase,
        # and a clear margin over the runner-up.
        if kw_scored:
            k_top = kw_scored[0]
            k_runner = kw_scored[1][0] if len(kw_scored) > 1 else 0.0
            if (
                k_top[0] >= _KEYWORD_GATE
                and k_top[1] >= _MIN_KEYWORD_PHRASE_WORDS
                and (k_top[0] - k_runner) >= _MARGIN
            ):
                return RouteCandidate(
                    agent=k_top[2], specialty=k_top[3], score=k_top[0], method="keyword"
                )
        return None
    except Exception:
        logger.debug("routing classify failed", exc_info=True)
        return None


def _candidate_vector(name: str, specialty: str, hints: str, model: str, cache: dict | None):
    """Cached specialty embedding for a candidate, keyed by embedding model so a
    model change recomputes (stale vector → degrade to keyword, never wrong-agent)."""
    text = (specialty + " " + hints).strip()
    if not text:
        return None
    if cache is not None:
        entry = cache.get(name)
        if entry and entry[0] == model:
            return entry[1]
    vec, _m = _embed(text)
    if cache is not None and vec is not None:
        cache[name] = (model, vec)
    return vec


# ── Suppression store (entity_settings/agent_routing.json) ──────────────────────
# Fail-OPEN per the availability doctrine: a missing/corrupt store = nothing
# suppressed. Class B (new persisted file) — plain clean break under the pre-1.0
# banner (tolerant reads, no migration).

_STORE = "agent_routing"


def _load_store() -> dict:
    from personalclaw.providers.entity_routes import _load_entity_settings

    try:
        raw = _load_entity_settings(_STORE)
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}

    # Migrate to lowercase keys
    migrated = False
    muted = raw.get("muted") or []
    new_muted = []
    for m in muted:
        lm = m.lower()
        if lm not in new_muted:
            new_muted.append(lm)
        if m != lm:
            migrated = True
            
    dismissals = raw.get("dismissals") or {}
    new_dismissals = {}
    for k, v in dismissals.items():
        lk = k.lower()
        if lk not in new_dismissals or v.get("last_dismissed_at", 0) > new_dismissals[lk].get("last_dismissed_at", 0):
            new_dismissals[lk] = v
        if k != lk:
            migrated = True
            
    if migrated:
        raw["muted"] = new_muted
        raw["dismissals"] = new_dismissals
        _save_store(raw)
        
    return raw


def _save_store(store: dict) -> None:
    from personalclaw.providers.entity_routes import _save_entity_settings

    try:
        _save_entity_settings(_STORE, store)
    except Exception:
        logger.debug("agent-routing store save failed", exc_info=True)


def is_suppressed(agent: str, *, now: float, cooldown_hours: float) -> bool:
    """True when *agent* is muted or inside its dismissal cooldown."""
    agent_key = agent.lower()
    store = _load_store()
    if agent_key in (store.get("muted") or []):
        return True
    entry = (store.get("dismissals") or {}).get(agent_key)
    if not isinstance(entry, dict):
        return False
    last = float(entry.get("last_dismissed_at", 0.0) or 0.0)
    return (now - last) < (max(0.0, cooldown_hours) * 3600.0)


def record_dismiss(agent: str, *, now: float, mute_at: int = 3) -> dict:
    """Bump *agent*'s dismissal counter; mute it once the count reaches ``mute_at``.
    Returns the updated status for the agent."""
    agent_key = agent.lower()
    store = _load_store()
    dismissals = store.setdefault("dismissals", {})
    entry = dismissals.setdefault(agent_key, {"count": 0, "last_dismissed_at": 0.0})
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_dismissed_at"] = now
    muted = store.setdefault("muted", [])
    if entry["count"] >= mute_at and agent_key not in muted:
        muted.append(agent_key)
    _save_store(store)
    return {"agent": agent_key, "count": entry["count"], "muted": agent_key in muted}


def unmute(agent: str) -> None:
    """Clear an agent's mute + dismissal history (from the agent detail page)."""
    agent_key = agent.lower()
    store = _load_store()
    muted = store.get("muted") or []
    if agent_key in muted:
        muted.remove(agent_key)
        store["muted"] = muted
    (store.get("dismissals") or {}).pop(agent_key, None)
    _save_store(store)


def routing_status() -> dict:
    store = _load_store()
    return {
        "muted": list(store.get("muted") or []),
        "dismissals": dict(store.get("dismissals") or {}),
    }


# ── The api_chat hook ───────────────────────────────────────────────────────────

# Per-session cap: at most one suggestion every N user turns, so it never nags even
# before a dismissal. Tracked in-process on DashboardState (restart re-seeds — fine).
_TURNS_BETWEEN_SUGGESTIONS = 5


def _routing_state(state) -> tuple[dict, dict]:
    """(embed_cache, last_suggested_turn) dicts lazily attached to DashboardState."""
    if not hasattr(state, "_routing_embed_cache"):
        state._routing_embed_cache = {}
    if not hasattr(state, "_routing_last_turn"):
        state._routing_last_turn = {}
    return state._routing_embed_cache, state._routing_last_turn


def suggest_for_send(state, session, message: str) -> RouteCandidate | None:
    """The api_chat hook: gate → classify → SEL log → return a suggestion (the caller
    broadcasts it). Best-effort; never raises into the send path.

    Gates (any fail → None, no event): routing disabled; session not default-agent;
    ``memory_mode != "persistent"``; per-session frequency cap not elapsed; the matched
    agent is suppressed (cooldown/muted).
    """
    try:
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load()
        rc = cfg.agents_routing
        if not rc.enabled:
            return None
        # Explicit-agent sessions opt out: a non-empty agent that isn't the default
        # means the user already chose. Empty or == default = a default-agent chat.
        default_agent = cfg.default_agent or ""
        sess_agent = getattr(session, "agent", "") or ""
        if sess_agent and sess_agent != default_agent:
            return None
        if getattr(session, "memory_mode", "persistent") != "persistent":
            return None

        import time as _time

        now = _time.time()
        _embed_cache, _last_turn = _routing_state(state)
        key = getattr(session, "key", "")
        user_turns = sum(1 for m in getattr(session, "messages", []) if m.get("role") == "user")
        prev = _last_turn.get(key, -_TURNS_BETWEEN_SUGGESTIONS)
        if user_turns - prev < _TURNS_BETWEEN_SUGGESTIONS:
            return None

        candidates = [
            c for c in eligible_candidates(cfg) if c[0] != sess_agent and c[0] != default_agent
        ]
        if not candidates:
            return None
        result = classify(
            message, candidates, min_confidence=rc.min_confidence, embed_cache=_embed_cache
        )
        if result is None:
            return None
        if is_suppressed(result.agent, now=now, cooldown_hours=rc.cooldown_hours):
            return None

        _last_turn[key] = user_turns
        try:
            from personalclaw.sel import sel

            sel().log_api_access(
                caller="dashboard",
                operation="agents.routing_suggest",
                outcome="suggested",
                source="dashboard",
                resources=f"session={key},agent={result.agent},method={result.method},"
                f"score={result.score:.3f}",
            )
        except Exception:
            logger.debug("routing SEL log failed", exc_info=True)
        return result
    except Exception:
        logger.debug("suggest_for_send failed", exc_info=True)
        return None
