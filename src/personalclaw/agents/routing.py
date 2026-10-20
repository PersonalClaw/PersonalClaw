"""Agent routing — suggest the right specialist, never route silently.

When the user sends a message in a DEFAULT-agent chat and an installed specialist is
a clearly better fit, the classifier here produces a single non-blocking suggestion
the frontend renders as a chip ("Route to <agent>?"). The user consents; nothing about
the session changes until they click.

Deterministic-first, LLM never in the hot path (the calibrated shape from
``workflows/surfacing``):
  * **stage 1** — each comma-separated ``route_hints`` entry against the message. A hint of one
    or two words is a KEYWORD and matches when the message says it, as whole words in that
    order; a longer hint is an EXAMPLE REQUEST and matches when the message holds most of its
    words (gate 0.7, ``skills.loader._MIN_TRIGGER_OVERLAP``).
  * **stage 2** — cosine of the message vs each candidate's ``specialty + route_hints``
    vector; skipped entirely when no embedder is bound. A suggestion needs the top score
    above the confidence gate AND a clear margin over the runner-up (≥0.1) so ambiguous
    fits stay silent.

A send never waits on the embedding model for a candidate: the candidates' vectors come from an
index held in memory (:data:`_SPECIALTY_VECTORS`), keyed by the text embedded and the model that
embedded it, which embeds what it lacks in the background. Until every candidate has one, stage 2
is skipped, since a margin over a candidate left out would be no margin. The message itself is
embedded within :data:`QUERY_EMBED_BUDGET_SECS`. Routing used to embed the message and then each
candidate it had no vector for, one round trip each, on the event loop that serves every request,
so a model answering in 3 s held every other request for 3 s per text.

The message both stages read is what the person TYPED: a block they pasted (a log, an export,
a document) is material for the answer, not the request, and its words would otherwise swamp
the embedding and trip a keyword by accident.

Pure functions over the provider-agnostic ``AgentProfile`` metadata + the one
embedding path — zero per-provider logic. Never raises into the send path.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

from personalclaw import record_files
from personalclaw.agents.defaults import is_reserved_agent
from personalclaw.agents.native.tool_vectors import ToolVectors, bound_embedder
from personalclaw.embedding_providers.base import embed_within
from personalclaw.own_words import typed_text

logger = logging.getLogger(__name__)

# Stage gates, mirroring the surfacing/skills family exactly.
_KEYWORD_GATE = 0.7
DEFAULT_MIN_CONFIDENCE = 0.62
# A confident suggestion must beat the runner-up by this margin (ambiguous → silent).
_MARGIN = 0.1
# A hint this short is a keyword: an overlap ratio over one or two words is all-or-nothing and
# order-blind ("on call" would match "call me on Monday"), so it has to be said as a phrase.
_KEYWORD_MAX_WORDS = 2

#: How long a send waits for the embedding model to embed the message before routing suggests by
#: route hints alone. The send's response waits on it (the turn does not: it is already running),
#: so a model that is loading, busy or unreachable must not hold it for the model's own deadline.
QUERY_EMBED_BUDGET_SECS = 1.5

#: Each candidate's :func:`specialty_text` vector, in memory only (see the module docstring).
_SPECIALTY_VECTORS = ToolVectors(what="agent specialty", thread="agent-specialty-vectors")


@dataclass(frozen=True)
class RouteCandidate:
    agent: str  # AgentProfile config key
    specialty: str
    score: float
    method: str  # "keyword" | "embedding"


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _says(qwords: list[str], phrase: list[str]) -> bool:
    """Whether the message's words hold *phrase* as consecutive whole words."""
    n = len(phrase)
    return any(qwords[i : i + n] == phrase for i in range(len(qwords) - n + 1))


def _keyword_score(query: str, route_hints: str) -> float:
    """The best match of the query against the comma-separated hints, from 0 to 1.

    A keyword (one or two words) scores 1 when the message says it and 0 when it does not. An
    example request scores the share of its words the message holds, so a paraphrase of it still
    matches while a message that shares one common word with it does not."""
    qlist = _words(query)
    qwords = set(qlist)
    best = 0.0
    for phrase in route_hints.split(","):
        pwords = _words(phrase)
        if not pwords:
            continue
        if len(pwords) <= _KEYWORD_MAX_WORDS:
            score = 1.0 if _says(qlist, pwords) else 0.0
        else:
            unique = set(pwords)
            score = len(unique & qwords) / len(unique)
        best = max(best, score)
    return best


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


def specialty_text(specialty: str, hints: str) -> str:
    """What is embedded for a candidate: its specialty and its route hints."""
    return f"{specialty} {hints}".strip()


def _embedding_scores(
    message: str, candidates: list[tuple[str, str, str]], index: ToolVectors
) -> list[tuple[float, str, str]]:
    """Stage 2: the message's cosine with each candidate's vector, best first, or nothing when
    no embedding model is bound, a candidate has no vector yet (it is being embedded) or the model
    did not embed the message within :data:`QUERY_EMBED_BUDGET_SECS`. Never embeds a candidate."""
    embedder = bound_embedder()
    if embedder is None:
        return []
    texts = {name: specialty_text(specialty, hints) for name, specialty, hints in candidates}
    wanted = [t for t in texts.values() if t]
    known = index.vectors(None, embedder.model, wanted)
    missing = [t for t in wanted if t not in known]
    if missing:
        index.want(None, embedder, missing)
        return []
    embedded = embed_within(embedder.one, message, within=QUERY_EMBED_BUDGET_SECS)
    if embedded.vector is None:
        if embedded.timed_out:
            logger.info(
                "agent routing: the embedding model did not embed the message within %g s, so "
                "this send is matched by route hints alone",
                QUERY_EMBED_BUDGET_SECS,
            )
        return []
    scored = [
        (_cosine(embedded.vector, known[texts[name]]), name, specialty)
        for name, specialty, _hints in candidates
        if texts[name]
    ]
    scored.sort(key=lambda r: r[0], reverse=True)
    return scored


def classify(
    message: str,
    candidates: list[tuple[str, str, str]],
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    index: ToolVectors | None = None,
) -> RouteCandidate | None:
    """Return the single best routing candidate above the gate + margin, or None.

    ``index`` holds the candidates' vectors (:data:`_SPECIALTY_VECTORS` unless given). A vector
    is filed under the text embedded and the model that embedded it, so an edited specialty or
    another model is embedded afresh and never compared stale. Never raises.
    """
    message = (message or "").strip()
    if not message or not candidates:
        return None
    try:
        # Stage 1: the message against each candidate's route_hints.
        kw_scored: list[tuple[float, str, str]] = []
        for name, specialty, hints in candidates:
            kw_scored.append((_keyword_score(message, hints), name, specialty))
        kw_scored.sort(key=lambda r: r[0], reverse=True)

        # Stage 2: embedding cosine over "specialty + hints" (skipped with no embedder).
        emb_scored = _embedding_scores(message, candidates, index or _SPECIALTY_VECTORS)

        # Prefer the embedding result (semantic) when it clears the confidence gate
        # AND beats its runner-up by the margin.
        if emb_scored:
            e_top = emb_scored[0]
            e_runner = emb_scored[1][0] if len(emb_scored) > 1 else 0.0
            if e_top[0] >= min_confidence and (e_top[0] - e_runner) >= _MARGIN:
                return RouteCandidate(
                    agent=e_top[1], specialty=e_top[2], score=e_top[0], method="embedding"
                )

        # Keyword fallback: needs the gate and a clear margin over the runner-up, so a
        # keyword two agents share suggests neither.
        if kw_scored:
            k_top = kw_scored[0]
            k_runner = kw_scored[1][0] if len(kw_scored) > 1 else 0.0
            if k_top[0] >= _KEYWORD_GATE and (k_top[0] - k_runner) >= _MARGIN:
                return RouteCandidate(
                    agent=k_top[1], specialty=k_top[2], score=k_top[0], method="keyword"
                )
        return None
    except Exception:
        logger.debug("routing classify failed", exc_info=True)
        return None


# ── Suppression store (entity_settings/agent_routing.json) ──────────────────────
# Fail-OPEN per the availability doctrine: a missing/corrupt store = nothing
# suppressed. Class B (new persisted file) — plain clean break under the pre-1.0
# banner (tolerant reads, no migration).

_STORE = "agent_routing"


def canonical_agent(agent: str) -> str:
    """The key an agent is stored under in the suppression store.

    🔑 THE STORE'S AGENT IDENTITY IS CASE-INSENSITIVE, AND THAT HAS TO BE SAYABLE. Every
    read/write below funnels through this, so ``PersonalClaw`` and ``personalclaw`` are one
    muted agent rather than two. It used to be five inline ``.lower()`` calls with the rule
    written down nowhere — which is exactly how the dashboard's agent detail page came to test
    ``routing_status()["muted"].includes(agentName)`` with the RAW name and report a muted
    ``PersonalClaw`` as "Active — eligible for auto-routing suggestions", with no Unmute
    control, while ``is_suppressed`` was returning True for it. A consumer cannot honour a
    convention it cannot name.
    """
    return str(agent).strip().lower()


def _load_store() -> dict:
    from personalclaw.providers.entity_routes import _load_entity_settings

    try:
        # Fail-OPEN on a discarded read (`or {}`): a suppression store we cannot read means
        # nothing is suppressed, so the user sees a routing notice they had muted. Noise, not
        # loss — and the opposite choice would silently hide the whole surface.
        raw = _load_entity_settings(_STORE) or {}
    except Exception:
        logger.warning(
            "Agent-routing suppression store load failed for entity %s; using empty settings",
            _STORE,
            exc_info=True,
        )
        return {}
    raw["muted"] = list(dict.fromkeys(canonical_agent(m) for m in (raw.get("muted") or [])))
    raw["dismissals"] = {canonical_agent(k): v for k, v in (raw.get("dismissals") or {}).items()}
    return raw


def _save_store(store: dict) -> None:
    from personalclaw.providers.entity_routes import _save_entity_settings

    try:
        _save_entity_settings(_STORE, store)
    except record_files.Unreadable:
        # The file there cannot be read, so nothing was written to it: said to whoever asked
        # for the change, never answered as done.
        raise
    except Exception:
        logger.debug("agent-routing store save failed", exc_info=True)


def is_suppressed(agent: str, *, now: float, cooldown_hours: float) -> bool:
    """True when *agent* is muted or inside its dismissal cooldown."""
    agent_key = canonical_agent(agent)
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
    agent_key = canonical_agent(agent)
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


def unmute(agent: str) -> str:
    """Clear an agent's mute + dismissal history. Returns the key that was cleared.

    Reachable from BOTH surfaces that can hold a mute: the agent's own detail page (per-agent)
    and Settings › Chat › Agent routing (the whole list). The second one is not a convenience —
    ``record_dismiss`` writes a key without checking that an agent by that name exists, and an
    agent can be deleted after it was muted, so the store legitimately holds keys with no detail
    page to visit. The list is the only surface that can reach those.
    """
    agent_key = canonical_agent(agent)
    store = _load_store()
    muted = store.get("muted") or []
    if agent_key in muted:
        muted.remove(agent_key)
        store["muted"] = muted
    (store.get("dismissals") or {}).pop(agent_key, None)
    _save_store(store)
    return agent_key


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


def _last_suggested_turn(state) -> dict:
    """The last_suggested_turn dict, lazily attached to DashboardState."""
    if not hasattr(state, "_routing_last_turn"):
        state._routing_last_turn = {}
    return state._routing_last_turn


def suggest_for_send(
    state, session, message: str, *, pasted: list[str] | tuple[str, ...] = ()
) -> RouteCandidate | None:
    """The api_chat hook: gate → classify → SEL log → return a suggestion (the caller
    broadcasts it). Best-effort; never raises into the send path.

    *pasted* is the send's pasted blocks (``own_words.pasted_blocks``); the classifier reads the
    message without them (``own_words.typed_text``).

    Gates (any fail → None, no event): routing disabled; session not default-agent; the chat
    keeps nothing (an Incognito or Temporary chat, or one whose mode cannot be read); per-session
    frequency cap not elapsed; the matched agent is suppressed (cooldown/muted).
    """
    try:
        from personalclaw import memory_writes
        from personalclaw.config.loader import AppConfig
        from personalclaw.constants import dashboard_history_key

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
        # The send runs outside the chat's turn, so the embedding model would read the message
        # here: the one answer to whether a model other than the chat's own may read it, asked of
        # every record of the chat's mode under both spellings of its key.
        key = str(getattr(session, "key", "") or "")
        mode = getattr(session, "memory_mode", None)
        if memory_writes.blocks_background_models(
            key, dashboard_history_key(key), memory_mode=mode if isinstance(mode, str) else None
        ):
            return None

        import time as _time

        now = _time.time()
        _last_turn = _last_suggested_turn(state)
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
            typed_text(message, pasted),
            candidates,
            min_confidence=rc.min_confidence,
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
