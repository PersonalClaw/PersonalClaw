"""The Memory Service (L3) — where ALL memory intelligence consolidates.

This is the platform-owned, vendor-neutral layer that mirrors how Knowledge's
platform service runs over every item regardless of source
(memory-architecture.md §3). Supersession, promotion, recall ranking, the L1
manifest, lessons + the contradiction judge, preference-facet derivation, lint,
and the reversible WAL all live *here* — expressed over the provider contract —
not trapped inside one concrete provider that every consumer must duck-type past.

M1 scope: this is a **facade**. It wraps today's ``MemoryStore`` (which holds the
markdown projection files + an attached ``VectorMemoryStore``) and delegates the
intelligence to it, with identical behavior. The point of M1 is that consumers
stop saying ``getattr(memory, "vector_store").X`` and start saying
``service.X`` — so M2/M3 can re-cut what's *behind* the facade (the provider
ABC) without touching a single consumer again.

After M3, nothing outside L2 (provider) / L3 (this) references ``vector_store``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, cast

from personalclaw.memory_providers.base import MemoryProvider

if TYPE_CHECKING:
    from personalclaw.memory import MemoryStore
    from personalclaw.memory_record import MemoryCapabilities, MemoryRecord
    from personalclaw.vector_memory import SemanticRejectCode, VectorMemoryStore

logger = logging.getLogger(__name__)

# Category → time-to-live in days (memory-architecture.md §3.6, O-A1). Memories
# age at different rates by what kind of thing they are. A category absent here
# never expires (facts/prefs are durable). Only records that carry a category are
# subject to TTL — legacy uncategorized rows are untouched.
_CATEGORY_TTL_DAYS: dict[str, float] = {
    "debug": 7.0,  # transient debugging context
    "event": 30.0,  # a thing that happened — ages out of relevance
    "decision": 180.0,  # decisions stay relevant for a while
    # "fact" / "pref" intentionally absent → durable, never TTL-expired
}

# The session working-memory summary is a bounded rolling digest, not the
# transcript — cap it so always-injection stays cheap (memory-architecture §3.5).
_WORKING_MEMORY_CAP = 2_000


def _is_episodic(rec: "MemoryRecord") -> bool:
    from personalclaw.memory_record import MemoryKind

    return rec.kind == MemoryKind.EPISODIC


class MemoryService:
    """L3 facade over a memory provider + its vector layer.

    Construct with the ``MemoryStore`` (the markdown/FTS provider) that may carry
    an attached ``vector_store``. Every intelligence operation routes through
    here; callers never touch ``vector_store`` directly.
    """

    def __init__(
        self,
        provider: "MemoryProvider",
        *,
        vector_store: "VectorMemoryStore | None" = None,
        fallback: "MemoryProvider | None" = None,
    ) -> None:
        self._provider = provider
        # An explicit vector store overrides the one discovered on the provider.
        # Used where the caller holds the record/vector provider directly (e.g.
        # the consolidator's write path) rather than a markdown projection that
        # carries an attached vector store.
        self._explicit_vs = vector_store
        # The fallback provider in the chain (memory-architecture.md §3.4): when
        # the primary record provider can't do vector search (no embedder), the
        # service degrades retrieval through this provider's FTS — the real
        # expression of VISION's "Qdrant primary → filesystem plain-text fallback"
        # as capability-degradation rather than a fiction inside one class.
        self._fallback = fallback

    @classmethod
    def over_vector_store(cls, vector_store: "VectorMemoryStore | None") -> "MemoryService":
        """Build a service whose backing provider IS the record/vector store.

        For L3 write paths (consolidation, after-turn review, CLI) that operate
        directly on the record store, not through a markdown projection. When
        ``vector_store`` is None the service degrades to no-op (no memory wired).
        """
        provider = vector_store if vector_store is not None else _NULL_PROVIDER
        return cls(provider, vector_store=vector_store)

    # ── identity / wiring ────────────────────────────────────────────────────

    @property
    def provider(self) -> "MemoryProvider":
        return self._provider

    @property
    def _vs(self) -> "VectorMemoryStore | None":
        """The record/vector store the service drives, or None.

        Either explicitly supplied (``over_vector_store`` — the provider IS the
        store) or discovered on a markdown-projection provider's
        ``vector_store`` attribute. This is the ONE place the service reaches the
        vector layer; M4 generalizes it to the ordered provider list.
        """
        if self._explicit_vs is not None:
            return self._explicit_vs
        return getattr(self._provider, "vector_store", None)

    def capabilities(self) -> "MemoryCapabilities":
        """What the backing provider can do (L2 flags). Degrades to FTS-only when
        no vector store is wired."""
        vs = self._vs
        if vs is not None:
            return vs.capabilities()
        from personalclaw.memory_record import MemoryCapabilities

        return MemoryCapabilities(
            vector=False, transactional_batch=False, event_log=False, full_text_search=True
        )

    @property
    def has_vector(self) -> bool:
        """Whether a record store is wired (can do semantic/episodic CRUD).

        NOTE: this is store-presence, not vector-retrieval capability — a store
        with no embedder still does semantic key/value CRUD + FTS. Use
        ``can_vector_search`` to gate vector retrieval specifically."""
        return self._vs is not None

    @property
    def can_vector_search(self) -> bool:
        """Whether the primary can do VECTOR retrieval right now (embedder wired).

        The capability check that replaces ``vs.embed_fn is not None`` scattered
        at call sites — a store with no embedder reports vector=False and the
        service degrades to the fallback FTS."""
        vs = self._vs
        return vs is not None and vs.capabilities().vector

    def fts_fallback_search(self, query: str, *, k: int = 8) -> list[dict]:
        """Degraded keyword search through the fallback provider (no vectors).

        Used when the primary can't do vector retrieval — the capability-
        degradation path. Returns the fallback's scored FTS hits, or [] when no
        fallback is wired."""
        if self._fallback is None or not query:
            return []
        try:
            return self._fallback.vector_query(text=query, k=k)
        except Exception:
            logger.debug("fts fallback search failed", exc_info=True)
            return []

    # ── read path: context injection / recall ─────────────────────────────────

    def get_context(
        self,
        *,
        query: str = "",
        prefs_cap: int = 4_000,
        projects_cap: int = 6_000,
        history_cap: int = 25_000,
        semantic_cap: int = 12_000,
        episodic_cap: int = 12_000,
        l1_manifest: bool | None = None,
    ) -> str:
        """The full memory-context block for prompt injection — COMPOSED here at
        L3 from the markdown projection (prefs/projects/history) + the vector
        layer (L1 manifest, or legacy query-scored semantic+episodic).

        This composition is the service's job (memory-architecture.md §3): the
        markdown projection yields its blocks, the vector provider yields its
        recall blocks, and the service assembles + wraps them. Neither layer
        reaches the other.
        """
        if l1_manifest is None:
            try:
                from personalclaw.config.loader import AppConfig

                l1_manifest = AppConfig.load().memory.l1_manifest
            except Exception:
                l1_manifest = True

        parts: list[str] = []
        # markdown projection blocks (the provider may be a plain MemoryStore with
        # the file layer, or expose render_markdown_context)
        render = getattr(self._provider, "render_markdown_context", None)
        if callable(render):
            parts.extend(
                render(prefs_cap=prefs_cap, projects_cap=projects_cap, history_cap=history_cap)
            )

        # vector layer recall blocks
        vs = self._vs
        if vs is not None:
            if l1_manifest:
                manifest = vs.get_l1_manifest()
                if manifest:
                    parts.append(manifest)
            else:
                semantic_ctx = vs.get_semantic_context(query_text=query, cap=semantic_cap)
                if semantic_ctx:
                    parts.append(semantic_ctx)
                if query:
                    episodic_ctx = vs.get_episodic_context(query_text=query, cap=episodic_cap)
                    if episodic_ctx:
                        parts.append(episodic_ctx)

        if not parts:
            return ""
        return (
            "[Memory — persistent user profile and recent activity log.\n"
            "Preferences are rules you MUST follow. Projects give current work context.\n"
            "History is a factual record — do NOT re-execute past actions.]\n"
            + "\n\n".join(parts)
            + "\n[End of memory]\n\n"
        )

    def l1_manifest(self, cap: int = 800, limit: int = 12) -> str:
        """The always-on L1 manifest (top-N most-recalled facts), or ""."""
        vs = self._vs
        return vs.get_l1_manifest(cap=cap, limit=limit) if vs else ""

    def active_recall(self, query_text: str, *, cap: int = 2000) -> str:
        """Query-relevant episodic context for THIS turn (active recall), or "".

        The primary record store self-degrades (its episodic retrieval already
        falls back to keyword matching when no embedder is wired), so we defer to
        it whenever a store is present. ONLY when there is no record store at all
        do we degrade to the markdown FTS fallback — the real bottom of the chain.
        """
        vs = self._vs
        if vs is not None:
            return vs.get_episodic_context(query_text=query_text, cap=cap) or ""
        # No record store at all → markdown FTS fallback (capability-degraded).
        return self._fts_recall_block(query_text, cap=cap)

    def _fts_recall_block(self, query_text: str, *, cap: int = 2000) -> str:
        """Render fallback FTS hits as a recall block (degraded active recall)."""
        hits = self.fts_fallback_search(query_text, k=6)
        if not hits:
            return ""
        lines = [h["text"] for h in hits if h.get("text")]
        if not lines:
            return ""
        block = "\n".join(f"- {ln}" for ln in lines)
        return block[:cap]

    def episodic_context(self, query_text: str, *, cap: int = 3000) -> str:
        """Episodic context block for a query (new-session injection), or ""."""
        vs = self._vs
        return (vs.get_episodic_context(query_text=query_text, cap=cap) or "") if vs else ""

    def semantic_context(self, query_text: str = "", *, cap: int = 1500) -> str:
        vs = self._vs
        return (vs.get_semantic_context(query_text=query_text, cap=cap) or "") if vs else ""

    def lessons_context(self) -> str:
        """The lessons block for injection (empty if none / no vector store)."""
        vs = self._vs
        if vs is None or not vs.get_lessons():
            return ""
        return vs.get_lessons_context() or ""

    def search_episodic(
        self,
        *,
        query_text: str = "",
        query_embedding: list[float] | None = None,
        limit: int = 8,
        tag_filter: list[str] | None = None,
    ) -> list[dict]:
        vs = self._vs
        if vs is None:
            return []
        if query_embedding is None and query_text and self.has_vector:
            query_embedding = self.embed(query_text)
        return vs.search_episodic(
            query_embedding=query_embedding,
            query_text=query_text,
            limit=limit,
            tag_filter=tag_filter,
        )

    def episodic_list(
        self, *, limit: int = 50, offset: int = 0, tag_filter: list[str] | None = None
    ) -> list[dict]:
        vs = self._vs
        if vs is None:
            return []
        return vs.get_episodic_list(limit=limit, offset=offset, tag_filter=tag_filter)

    def delete_episodic(self, mem_id: str, *, source: str = "user_explicit") -> bool:
        vs = self._vs
        return vs.delete_episodic(mem_id, source=source) if vs else False

    def lint(self) -> dict:
        """Run the memory-health sweep over the backing store; report dict."""
        vs = self._vs
        if vs is None:
            return {}
        from personalclaw.memory_lint import lint_memory

        return lint_memory(vs).to_dict()

    # ── session working memory (M5c — §3.5) ───────────────────────────────────
    # An always-injected, bounded, continuously-distilled running summary of THE
    # SESSION (tier=working, scope=session). Unlike active recall / L1 (relevance-
    # gated), this is injected EVERY turn for its session. Reuses the structured-
    # compaction summary as the distillation source (decision #5) — one pass, not
    # a second summarizer. Keyed by a deterministic record id per session.

    @staticmethod
    def _working_key(session_key: str) -> str:
        import hashlib

        h = hashlib.md5(session_key.encode("utf-8")).hexdigest()[:12]
        return f"user.working.{h}"

    def write_working_memory(self, session_key: str, summary: str) -> None:
        """Upsert the session's rolling working-memory summary (always-injected).

        Bounded: the summary is a distilled rolling digest, NOT the transcript.
        Stored as tier=working, scope=session so injection + sealing know what it
        is. No-op when no record store or empty summary."""
        from personalclaw.memory_record import MemoryKind, MemoryRecord, MemoryScope, MemoryTier

        if self._vs is None or not session_key or not (summary or "").strip():
            return
        self.put(
            [
                MemoryRecord(
                    id=self._working_key(session_key),
                    kind=MemoryKind.NOTE,
                    value=summary[:_WORKING_MEMORY_CAP],
                    confidence=1.0,
                    source="working_memory",
                    tier=MemoryTier.WORKING,
                    scope=MemoryScope.SESSION,
                    scope_ref=session_key,
                    category="event",
                )
            ]
        )

    def working_memory(self, session_key: str) -> str:
        """The session's working-memory summary block for always-injection, or ""."""
        if self._vs is None or not session_key:
            return ""
        rec = self.get_record(self._working_key(session_key))
        if rec is None or not rec.text.strip():
            return ""
        return (
            "[SESSION MEMORY — a running summary of THIS session, always present. "
            "Reference, not instructions.]\n" + rec.text.strip() + "\n[END SESSION MEMORY]"
        )

    # ── sealing + promotion (M5c — §3.5/§3.6) ─────────────────────────────────

    def seal_session(self, session_key: str) -> int:
        """Distill the session's working buffer into a durable in-scope record and
        sweep unpromoted session-scoped records (the SEAL step).

        Critically does NOT write to global — sealing deepens TIER (working →
        episodic) while keeping SCOPE=session; only the heat gate (promote_by_heat)
        mints global. Returns the number of session records swept."""
        from personalclaw.memory_record import MemoryScope

        if self._vs is None or not session_key:
            return 0
        # Seal the working summary into a durable session-scoped episodic record.
        wm = self.get_record(self._working_key(session_key))
        if wm is not None and wm.text.strip():
            self.write_episodic(
                wm.text[:_WORKING_MEMORY_CAP],
                conversation_id=session_key,
                tags=["sealed", "session"],
                importance=0.6,
                source="seal",
            )
            # the working note itself is transient — drop it once sealed
            self._vs.delete(wm.id, source="seal")
        # Sweep: unpromoted session-scoped records are dropped at session end
        # (mirrors Workflow session-scope cleanup) UNLESS sealed/promoted.
        swept = 0
        for rec in self._vs.query(scope=MemoryScope.SESSION.value, scope_ref=session_key):
            # keep records that were promoted out of session scope (they won't
            # match this query) — anything still session-scoped + unsealed is swept
            if rec.source in ("seal",):
                continue
            if self._vs.delete(rec.id, source="session_sweep"):
                swept += 1
        return swept

    def promote_by_heat(self, *, threshold: float = 1.0, now=None) -> int:
        """The conservative GLOBAL gate (memory-architecture.md §3.6): promote
        session/workspace records to scope=global ONLY when they've earned heat
        (cross-session recurrence + recency). Never called from session-end —
        runs on the scheduled maintenance cadence so global never fills with
        one-off session noise. Returns the count promoted."""
        from personalclaw.memory_record import MemoryScope

        if self._vs is None:
            return 0
        promoted = 0
        for rec in self._vs.iter_records():
            if rec.scope == MemoryScope.GLOBAL:
                continue
            # commitments NEVER promote to global (a proactive ping is contextual)
            from personalclaw.memory_record import MemoryKind

            if rec.kind == MemoryKind.COMMITMENT:
                continue
            if rec.heat(now=now) >= threshold and rec.recall_count >= 2:
                self._vs.db.execute(
                    f"UPDATE {'semantic_memory' if not _is_episodic(rec) else 'episodic_memories'} "
                    f"SET scope = ? WHERE {'key' if not _is_episodic(rec) else 'id'} = ?",
                    (MemoryScope.GLOBAL.value, rec.id),
                )
                self._vs.db.commit()
                self._vs.append_event(
                    event_type="promote_scope",
                    memory_type=rec.kind.value,
                    memory_key=rec.id,
                    old_value=rec.scope.value,
                    new_value=MemoryScope.GLOBAL.value,
                    source="heat_promote",
                )
                promoted += 1
        return promoted

    # ── procedural memory (M5d — O-A3) ────────────────────────────────────────
    # How the agent learns to WORK: tool/source outcomes → priors. A procedural
    # record captures "tool X on task-shape Y succeeded / was denied / needed
    # correction", mined at the after-turn-review seam, promoted into priors via
    # the heat gate. Failure-pattern synthesis collapses ≥N same-root-cause records
    # into ONE prior so the class never becomes tool-call-log noise.

    @staticmethod
    def _procedural_key(tool: str, task_shape: str, outcome: str) -> str:
        import hashlib

        h = hashlib.md5(f"{tool}|{task_shape}|{outcome}".encode("utf-8")).hexdigest()[:12]
        return f"user.procedural.{h}"

    def record_procedural(
        self,
        *,
        tool: str,
        task_shape: str,
        outcome: str,
        detail: str = "",
        scope_ref: str | None = None,
    ) -> str | None:
        """Record a how-to-work observation (tool X on task-shape Y → outcome).

        Outcome ∈ {success, denied, corrected, failed}. Stored as a procedural
        record at scope=session by default (the heat gate promotes recurring ones
        to global priors). Reinforces the visit_count when the same observation
        recurs. Returns the record key, or None when no record store."""
        from personalclaw.memory_record import MemoryKind, MemoryRecord, MemoryScope, MemoryTier

        if self._vs is None or not tool or not task_shape:
            return None
        key = self._procedural_key(tool, task_shape, outcome)
        existing = self.get_record(key)
        visit = (existing.recall_count if existing else 0) + 1
        text = f"{tool} on '{task_shape}' → {outcome}" + (f": {detail}" if detail else "")
        self.put(
            [
                MemoryRecord(
                    id=key,
                    kind=MemoryKind.PROCEDURAL,
                    value=text,
                    confidence=0.85,
                    source="procedural",
                    tier=MemoryTier.SEMANTIC,
                    scope=MemoryScope.SESSION,
                    scope_ref=scope_ref,
                    category="decision",
                    recall_count=visit,
                )
            ]
        )
        return key

    def procedural_priors(self, *, limit: int = 12) -> list[dict]:
        """The learned how-to-work priors (global procedural records), for
        recall-gated injection. Highest-heat first."""
        from personalclaw.memory_record import MemoryKind, MemoryScope

        recs = [
            r
            for r in self.get_records(kinds={MemoryKind.PROCEDURAL.value})
            if r.scope == MemoryScope.GLOBAL
        ]
        recs.sort(key=lambda r: r.heat(), reverse=True)
        return [{"key": r.id, "text": r.text, "heat": round(r.heat(), 3)} for r in recs[:limit]]

    def synthesize_failures(self, *, min_cluster: int = 3) -> int:
        """Failure-pattern synthesis (the load-bearing anti-noise mechanism): when
        ≥``min_cluster`` procedural FAILURE records share a root cause (same tool),
        collapse them into ONE synthesized prior ("prefer not / domain unreliable")
        and retire the scattered rows. Returns the number of priors synthesized.

        Without this the procedural class bloats into a tool-call log — it is NOT
        optional (memory-architecture.md §3.7)."""
        from collections import defaultdict

        from personalclaw.memory_record import MemoryKind, MemoryRecord, MemoryScope, MemoryTier

        if self._vs is None:
            return 0
        # cluster failed/denied procedural records by tool (the root-cause key)
        clusters: dict[str, list] = defaultdict(list)
        for r in self.get_records(kinds={MemoryKind.PROCEDURAL.value}):
            if "→ failed" in r.text or "→ denied" in r.text:
                tool = r.text.split(" on ", 1)[0].strip()
                clusters[tool].append(r)
        synthesized = 0
        for tool, members in clusters.items():
            if len(members) < min_cluster:
                continue
            # one synthesized prior replaces the N scattered rows
            prior_key = (
                f"user.procedural.synth.{__import__('hashlib').md5(tool.encode()).hexdigest()[:12]}"
            )
            self.put(
                [
                    MemoryRecord(
                        id=prior_key,
                        kind=MemoryKind.PROCEDURAL,
                        value=f"{tool} is unreliable for these task shapes — prefer an alternative "
                        f"(synthesized from {len(members)} failures)",
                        confidence=0.8,
                        source="failure_synthesis",
                        tier=MemoryTier.SEMANTIC,
                        scope=MemoryScope.GLOBAL,
                        category="decision",
                        recall_count=sum(m.recall_count for m in members),
                    )
                ]
            )
            for m in members:
                self._vs.delete(m.id, source="failure_synthesis")
            synthesized += 1
        return synthesized

    # ── self-persona (M5e) ────────────────────────────────────────────────────
    # A positive self-model: who the agent is BECOMING with this user (distinct
    # from the corrective lesson store, which records what NOT to do). scope=agent,
    # injected always-on like the L1 manifest but from the agent's own namespace.

    @staticmethod
    def _persona_key(agent: str, trait: str) -> str:
        import hashlib

        h = hashlib.md5(f"{agent}|{trait}".encode("utf-8")).hexdigest()[:12]
        return f"user.persona.{h}"

    def record_persona(self, *, agent: str, trait: str) -> str | None:
        """Record/reinforce an agent self-persona trait (scope=agent)."""
        from personalclaw.memory_record import MemoryKind, MemoryRecord, MemoryScope, MemoryTier

        if self._vs is None or not agent or not trait.strip():
            return None
        key = self._persona_key(agent, trait)
        existing = self.get_record(key)
        visit = (existing.recall_count if existing else 0) + 1
        self.put(
            [
                MemoryRecord(
                    id=key,
                    kind=MemoryKind.SELF_PERSONA,
                    value=trait.strip(),
                    confidence=0.85,
                    source="self_persona",
                    tier=MemoryTier.SEMANTIC,
                    scope=MemoryScope.AGENT,
                    scope_ref=agent,
                    recall_count=visit,
                )
            ]
        )
        return key

    def persona_block(self, *, agent: str, limit: int = 6) -> str:
        """The agent's self-persona block for always-on injection (scope matches
        the running agent), or ""."""
        from personalclaw.memory_record import MemoryKind

        traits = [
            r
            for r in self.get_records(kinds={MemoryKind.SELF_PERSONA.value})
            if r.scope_ref == agent
        ]
        if not traits:
            return ""
        traits.sort(key=lambda r: r.heat(), reverse=True)
        lines = [r.text for r in traits[:limit] if r.text.strip()]
        if not lines:
            return ""
        return (
            "[SELF — who you are becoming with this user (your own growth notes)]\n"
            + "\n".join(f"- {t}" for t in lines)
            + "\n[END SELF]"
        )

    # ── commitments (M5e — O-A4) — the proactive brain, GUARDRAILED ────────────
    # An inferred future check-in the agent notices from conversation, WITHOUT the
    # user setting a reminder. The one class with a 'creepy when wrong' failure
    # mode, so the guardrails are architecture, not config: OFF BY DEFAULT, hard
    # per-day cap, high-confidence only, scoped to exact agent+channel, one-tap
    # dismiss, heartbeat-clamped. NEVER injected into context; NEVER promotes to
    # global. Delivered at most once per window by the heartbeat.

    @staticmethod
    def _commitment_key(agent: str, text: str) -> str:
        import hashlib

        h = hashlib.md5(f"{agent}|{text}".encode("utf-8")).hexdigest()[:12]
        return f"user.commitment.{h}"

    def record_commitment(
        self,
        *,
        agent: str,
        channel: str,
        text: str,
        due_window: str,
        confidence: float = 0.0,
        enabled: bool = False,
        max_per_day: int = 3,
    ) -> str | None:
        """Record an inferred future check-in (M5e — O-A4). Returns the key, or
        None when refused by a guardrail.

        GUARDRAILS (all enforced here, not optional config):
        - ``enabled`` must be True (the feature is OFF BY DEFAULT).
        - high-confidence only (``confidence`` >= 0.8).
        - hard per-day cap (``max_per_day``) on active commitments for this agent.
        Scoped to the exact agent+channel; stored at scope=session|agent so it
        NEVER promotes to global (a proactive ping is contextual, not durable)."""
        from personalclaw.memory_record import MemoryKind, MemoryRecord, MemoryScope, MemoryTier

        if self._vs is None or not enabled:
            return None
        if confidence < 0.8 or not text.strip() or not agent or not due_window:
            return None
        # hard per-day cap: count active (non-dismissed) commitments for this agent
        active = [
            r
            for r in self.get_records(kinds={MemoryKind.COMMITMENT.value})
            if r.scope_ref == agent and not (r.extra or {}).get("dismissed_at")
        ]
        if len(active) >= max_per_day:
            logger.info(
                "commitment refused: per-day cap (%d) reached for agent %s", max_per_day, agent
            )
            return None
        key = self._commitment_key(agent, text)
        # The full envelope rides value_json (text + delivery metadata) since the
        # semantic row has no due_window/channel columns — commitments are a small,
        # heartbeat-delivered class, not a queryable column space.
        envelope = {"text": text.strip(), "due_window": due_window, "channel": channel}
        self.put(
            [
                MemoryRecord(
                    id=key,
                    kind=MemoryKind.COMMITMENT,
                    value=envelope,
                    confidence=confidence,
                    source="commitment",
                    tier=MemoryTier.EPISODIC,
                    scope=MemoryScope.AGENT,
                    scope_ref=agent,
                    category="event",
                )
            ]
        )
        return key

    def due_commitments(self, *, agent: str, now_iso: str) -> list[dict]:
        """Active commitments whose due_window has arrived, for heartbeat delivery
        (NOT context injection). One natural check-in per window; the caller marks
        delivered/dismissed via ``dismiss_commitment``."""
        from personalclaw.memory_record import MemoryKind

        out: list[dict] = []
        for r in self.get_records(kinds={MemoryKind.COMMITMENT.value}):
            if r.scope_ref != agent:
                continue
            env = r.value if isinstance(r.value, dict) else {}
            due = env.get("due_window")
            if due and due <= now_iso:
                out.append(
                    {
                        "key": r.id,
                        "text": env.get("text", ""),
                        "channel": env.get("channel"),
                        "due_window": due,
                    }
                )
        return out

    def due_commitments_all(self, *, now_iso: str) -> list[dict]:
        """Every active commitment whose due_window has arrived, across all agents
        — the heartbeat's delivery scan (the per-agent ``due_commitments`` powers
        the capture cap). Each dict carries the owning ``agent`` so the caller can
        scope delivery + dismiss correctly."""
        from personalclaw.memory_record import MemoryKind

        out: list[dict] = []
        for r in self.get_records(kinds={MemoryKind.COMMITMENT.value}):
            env = r.value if isinstance(r.value, dict) else {}
            due = env.get("due_window")
            if due and due <= now_iso:
                out.append(
                    {
                        "key": r.id,
                        "text": env.get("text", ""),
                        "channel": env.get("channel"),
                        "due_window": due,
                        "agent": r.scope_ref or "",
                    }
                )
        return out

    def dismiss_commitment(self, key: str) -> bool:
        """One-tap dismiss — supersede a commitment so the heartbeat never re-fires
        it (a delivered-or-dismissed commitment is done)."""
        if self._vs is None:
            return False
        return self._vs.delete(key, source="commitment_dismiss")

    # ── two-stage retrieval rank (M5b — O-A2) ─────────────────────────────────

    def rank_episodic(self, *, query_text: str, limit: int = 8, now=None) -> list[dict]:
        """Two-stage episodic retrieval: stage 1 = the store's relevance search
        (vector or FTS), stage 2 = a multiplicative operational boost by record
        heat (memory-architecture.md §3.3 read path). Returns the reranked hits.

        Heat boost is multiplicative + bounded so it nudges ordering without
        letting a frequently-recalled-but-irrelevant record outrank a strong
        semantic match — relevance still dominates."""
        # over-fetch so the boost can reorder a wider candidate set
        hits = self.search_episodic(query_text=query_text, limit=max(limit * 2, limit))
        if not hits:
            return []
        from personalclaw.memory_record import MemoryRecord

        for h in hits:
            base = float(h.get("score", h.get("cosine_sim", 0.0)) or 0.0)
            # build a lightweight record view to compute heat from the hit fields
            rec = MemoryRecord.from_episodic_row(h) if "created_at" in h else None
            heat = rec.heat(now=now) if rec is not None else 0.0
            # multiplicative boost in [1.0, 1.5]: relevance dominates, heat nudges
            h["ranked_score"] = base * (1.0 + min(0.5, 0.33 * heat))
        hits.sort(key=lambda x: x.get("ranked_score", 0.0), reverse=True)
        return hits[:limit]

    # ── category-TTL expiry (M5b — O-A1) ──────────────────────────────────────

    def expire_by_category(self, *, now=None) -> int:
        """Soft-delete records past their category-TTL. Returns count expired.

        Categories age at different rates (memory-architecture.md §3.6): debug/
        event memories are short-lived; facts/prefs are durable. Only touches
        records that carry a ``category`` (legacy uncategorized rows never expire),
        and NEVER expires user_explicit or globally-promoted durable facts."""
        vs = self._vs
        if vs is None:
            return 0
        from datetime import datetime, timezone

        from personalclaw.memory_record import MemoryScope

        ref = now or datetime.now(tz=timezone.utc)
        expired = 0
        for rec in vs.iter_records():
            ttl_days = _CATEGORY_TTL_DAYS.get(rec.category or "", None)
            if ttl_days is None:
                continue  # no category / non-expiring category → keep
            # never expire durable global facts or user-set entries
            if rec.scope == MemoryScope.GLOBAL and rec.source == "user_explicit":
                continue
            stamp = rec.last_accessed_at or rec.updated_at or rec.created_at
            if not stamp:
                continue
            try:
                last = datetime.fromisoformat(stamp)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            age_days = (ref - last).total_seconds() / 86400.0
            if age_days > ttl_days:
                if vs.delete(rec.id, source="category_ttl"):
                    expired += 1
        return expired

    # ── daily digest (mem-tree, descoped) ─────────────────────────────────────
    # The one "tree" node kind PClaw lacked: a per-day rollup of what happened,
    # so "what happened on day D?" is answerable without scanning every fragment.
    # A digest is itself an EPISODIC record (dated narrative — episodic's exact
    # shape) tagged ``daily-digest`` + the date; idempotency is by date-tag lookup
    # (episodic ids are content UUIDs, so we dedup on the tag, not the id). Runs on
    # the SAME maintenance cadence as promotion/TTL — no new scheduler, and LLM-free
    # by default (extractive summary; an injected summarizer is optional).

    _DIGEST_TAG = "daily-digest"

    def _digest_exists(self, day: str) -> bool:
        """Whether a daily-digest episodic already exists for ``day`` (idempotency).

        A digest carries BOTH the ``daily-digest`` tag and the date tag; match on
        both so a normal episodic that merely happens to be tagged with the date
        can't be mistaken for the digest."""
        for e in self.episodic_list(limit=50, tag_filter=[self._DIGEST_TAG]):
            tags = e.get("tags") or []
            if isinstance(tags, str):
                import json as _json

                try:
                    tags = _json.loads(tags)
                except ValueError:
                    tags = []
            if day in tags:
                return True
        return False

    def build_daily_digest(self, *, now=None, max_days: int = 3, summarizer=None) -> int:
        """Synthesize one digest per past day that has episodic activity but no
        digest yet. Returns the number of digests created.

        Only *completed* days are digested (never the current day, still accruing).
        ``summarizer(day, texts)->str`` is an optional LLM condenser; without it the
        digest is a bounded extractive list — the maintenance cadence stays LLM-free
        by default."""
        from datetime import datetime, timezone

        from personalclaw.memory_record import MemoryKind, MemoryRecord

        vs = self._vs
        if vs is None:
            return 0
        ref = now or datetime.now(tz=timezone.utc)
        today = ref.date().isoformat()

        # Group real episodic fragments by their calendar day (excluding existing
        # digests, so a digest never feeds the next day's digest).
        by_day: dict[str, list[MemoryRecord]] = {}
        for rec in vs.iter_records(kinds={MemoryKind.EPISODIC.value}):
            if self._DIGEST_TAG in (rec.tags or []):
                continue
            stamp = rec.created_at
            if not stamp:
                continue
            day = stamp[:10]  # ISO date prefix
            if len(day) != 10 or day >= today:  # skip today (still open) + junk
                continue
            by_day.setdefault(day, []).append(rec)

        created = 0
        # Newest completed days first; bound the per-run work.
        for day in sorted(by_day, reverse=True)[:max_days]:
            if self._digest_exists(day):
                continue  # already digested — idempotent
            frags = sorted(by_day[day], key=lambda r: r.created_at)
            texts = [" ".join((r.text or "").split()) for r in frags if r.text.strip()]
            if not texts:
                continue
            body = None
            if summarizer is not None:
                try:
                    body = summarizer(day, texts)
                except Exception:
                    logger.debug("daily-digest summarizer failed for %s", day, exc_info=True)
                    body = None
            if not body:
                # Extractive fallback: a bounded bullet list of the day's fragments.
                shown = texts[:20]
                body = f"Daily digest for {day} — {len(texts)} memory event(s):\n" + "\n".join(
                    f"- {t[:200]}" for t in shown
                )
                if len(texts) > len(shown):
                    body += f"\n- …and {len(texts) - len(shown)} more."
            # Written as an episodic record (dated narrative); source marks it a
            # digest, tags make it findable + idempotent.
            self.write_episodic(
                body,
                conversation_id=f"daily-digest:{day}",
                tags=[self._DIGEST_TAG, day],
                importance=0.9,
                source="daily_digest",
            )
            created += 1
        return created

    def daily_digests(self, *, limit: int = 30) -> list[dict]:
        """The daily-digest nodes, newest first — the 'what happened when' view."""
        digests: list[dict] = []
        for e in self.episodic_list(limit=max(limit * 2, 60), tag_filter=[self._DIGEST_TAG]):
            tags = e.get("tags") or []
            if isinstance(tags, str):
                import json as _json

                try:
                    tags = _json.loads(tags)
                except ValueError:
                    tags = []
            day = next((t for t in tags if t != self._DIGEST_TAG), "")
            digests.append(
                {"day": day, "text": e.get("text", ""), "created_at": e.get("created_at", "")}
            )
        digests.sort(key=lambda d: d["day"], reverse=True)
        return digests[:limit]

    # ── provenance-first recall (mem-tree, descoped) ──────────────────────────

    def recall_with_provenance(self, *, query_text: str, limit: int = 8, now=None) -> list[dict]:
        """Episodic recall that carries PROVENANCE, not just text — each hit keeps
        its source, originating session, and timestamp so the agent (and the UI)
        can see *where* and *when* a memory came from, the mem-tree provenance-
        first-retrieval property expressed over the existing episodic store."""
        hits = self.rank_episodic(query_text=query_text, limit=limit, now=now)
        out: list[dict] = []
        for h in hits:
            out.append(
                {
                    "text": h.get("text", ""),
                    "source": h.get("source") or "",
                    "session": h.get("conversation_id") or "",
                    "created_at": h.get("created_at") or "",
                    "score": round(float(h.get("ranked_score", h.get("score", 0.0)) or 0.0), 4),
                }
            )
        return out

    def embed(self, text: str) -> list[float] | None:
        """Embed text via the provider's wired embedding function, or None."""
        vs = self._vs
        if vs is None or vs.embed_fn is None:
            return None
        return vs._try_embed(text)

    # ── read path: semantic CRUD (dashboard + recall) ─────────────────────────

    def get_all_semantic(self) -> list[dict]:
        vs = self._vs
        return vs.get_all_semantic() if vs else []

    def get_semantic(self, key: str) -> dict | None:
        vs = self._vs
        return vs.get_semantic(key) if vs else None

    def set_semantic(
        self, key: str, value: object, confidence: float, source: str
    ) -> "tuple[SemanticRejectCode, str] | None":
        vs = self._vs
        if vs is None:
            return None
        return vs.set_semantic(key, value, confidence, source)

    def delete_semantic(self, key: str, source: str = "user_explicit") -> bool:
        vs = self._vs
        return vs.delete_semantic(key, source) if vs else False

    def supersede_semantic(self, old_key: str, new_key: str, source: str) -> bool:
        vs = self._vs
        return vs.supersede_semantic(old_key, new_key, source) if vs else False

    def record_recall(self, keys: list[str]) -> None:
        vs = self._vs
        if vs is not None:
            vs.record_recall(keys)

    # ── write path: episodic + lessons + promotion (consolidation) ─────────────

    # Sources that are DIRECT user input — trusted, never scanned (decision #3). Any
    # other source (a tool result, a skill, an autonomous consolidation over external
    # content) is UNTRUSTED and passes through the injection/invisible-Unicode gate: a
    # poisoned tool output must not quietly write a steering instruction into durable
    # memory that re-injects on later turns.
    _TRUSTED_WRITE_SOURCES = frozenset(
        {"user_explicit", "user", "seal", "session_sweep", "heat_promote", "supersede"}
    )

    def _memory_write_blocked(self, text: str, source: str) -> bool:
        """S5 gate: reject an untrusted memory write carrying a high-confidence injection
        / bidi-steering payload. Returns True (block) only on a DANGEROUS verdict — a
        lower-band signal is allowed (a lesson legitimately mentioning "ignore" prose
        shouldn't be lost). No-op for trusted (user) sources and empty text."""
        if not text or source in self._TRUSTED_WRITE_SOURCES:
            return False
        try:
            from personalclaw.supply_chain import Verdict, default_scanner

            report = default_scanner.scan_text(text, surface="manifest")
            if report.verdict is Verdict.DANGEROUS:
                logger.warning(
                    "memory write BLOCKED (source=%s): injection/steering payload (%s)",
                    source,
                    ", ".join(sorted({f.rule for f in report.findings})),
                )
                try:
                    from personalclaw.sel import sel

                    sel().log_api_access(
                        caller=f"memory_service.write:{source}",
                        operation="memory_write",
                        outcome="blocked",
                        source="memory",
                        resources="",
                        error="injection/bidi payload in untrusted memory write",
                    )
                except Exception:
                    pass
                return True
        except Exception:
            logger.debug("memory-write scan errored (fail-open)", exc_info=True)
        return False

    def write_episodic(
        self,
        text: str,
        *,
        embedding: list[float] | None = None,
        conversation_id: str = "",
        tags: list[str] | None = None,
        importance: float = 0.5,
        source: str = "consolidation",
    ) -> bool:
        vs = self._vs
        if vs is None:
            return False
        if self._memory_write_blocked(text, source):
            return False
        return vs.write_episodic(
            text,
            embedding=embedding,
            conversation_id=conversation_id,
            tags=tags,
            importance=importance,
            source=source,
        )

    def put(self, records: "list[MemoryRecord]") -> None:
        """Upsert axis-bearing records (tier × scope) through the provider's
        record contract. The write surface M5 uses for scoped/working/procedural
        records — the legacy write_lesson/write_episodic keep today's defaults."""
        vs = self._vs
        if vs is not None:
            vs.put(records)

    def write_lesson(
        self,
        rule: str,
        category: str = "knowledge",
        negative: str | None = None,
        source: str = "user_explicit",
    ) -> bool:
        vs = self._vs
        if vs is None:
            return False
        if self._memory_write_blocked(rule, source) or (
            negative and self._memory_write_blocked(negative, source)
        ):
            return False
        return vs.write_lesson(rule, category=category, negative=negative, source=source)

    def get_lessons(self, limit: int | None = None) -> list[dict]:
        vs = self._vs
        return vs.get_lessons(limit=limit) if vs else []

    def delete_lesson(self, rule_substring: str) -> bool:
        vs = self._vs
        return vs.delete_lesson(rule_substring) if vs else False

    def promote_episodic_patterns(self, **kw: Any) -> int:
        vs = self._vs
        return vs.promote_episodic_patterns(**kw) if vs else 0

    # ── lifecycle: events / WAL / stats ────────────────────────────────────────

    def get_events(self, limit: int = 50, offset: int = 0) -> list[dict]:
        vs = self._vs
        return vs.get_events(limit=limit, offset=offset) if vs else []

    def undo_event(self, event_id: int) -> tuple[bool, str]:
        vs = self._vs
        if vs is None:
            return (False, "no vector store")
        return vs.undo_event(event_id)

    def memory_stats(self) -> dict:
        vs = self._vs
        return vs.memory_stats() if vs else {}

    def get_records(
        self, kinds: "set[str] | None" = None, include_deleted: bool = False
    ) -> "list[MemoryRecord]":
        """The unified typed-record view (M0) over all backing tables."""
        vs = self._vs
        return vs.iter_records(kinds=kinds, include_deleted=include_deleted) if vs else []

    def get_record(self, record_id: str) -> "MemoryRecord | None":
        vs = self._vs
        return vs.get_record(record_id) if vs else None

    # ── intelligence wiring (contradiction judge / embed fn) ───────────────────

    def set_contradiction_judge(self, judge: "Callable[[str, str], bool] | None") -> None:
        vs = self._vs
        if (
            vs is not None
            and judge is not None
            and getattr(vs, "contradiction_judge", None) is None
        ):
            vs.contradiction_judge = judge

    @property
    def contradiction_judge(self) -> "Callable[[str, str], bool] | None":
        vs = self._vs
        return getattr(vs, "contradiction_judge", None) if vs else None


class _NullProvider(MemoryProvider):
    """A provider stand-in for "no memory wired" — yields nothing, accepts
    nothing. Lets ``MemoryService.over_vector_store(None)`` exist as a real
    object so callers don't special-case None. Implements the full
    ``MemoryProvider`` surface as no-ops so it satisfies the interface."""

    name = "null"
    vector_store = None

    def init(self) -> None:
        return None

    def capabilities(self) -> "MemoryCapabilities":
        from personalclaw.memory_record import MemoryCapabilities

        return MemoryCapabilities(
            vector=False, transactional_batch=False, event_log=False, full_text_search=False
        )

    def put(self, records: "list[MemoryRecord]") -> None:
        return None

    def get(self, record_id: str) -> "MemoryRecord | None":
        return None

    def delete(self, record_id: str, *, source: str = "user_explicit") -> bool:
        return False

    def query(self, **_kw: Any) -> "list[MemoryRecord]":
        return []

    def vector_query(self, **_kw: Any) -> "list[dict]":
        return []

    def embed(self, text: str) -> "list[float] | None":
        return None

    def append_event(self, **_kw: Any) -> int:
        return 0

    def read_events(self, *, limit: int = 50, offset: int = 0) -> "list[dict]":
        return []

    def render_markdown_context(self, **_kw: Any) -> list:
        return []


_NULL_PROVIDER = _NullProvider()


# ── service resolution ─────────────────────────────────────────────────────────

# A MemoryService wraps a provider; we key the cache by the provider object id so
# the same provider always yields the same service (services are cheap, stateless
# facades — the state lives in the provider).
_services: "dict[int, MemoryService]" = {}


def service_for(provider: "MemoryProvider") -> MemoryService:
    """Return the MemoryService wrapping ``provider`` (cached per provider).

    When the provider is a markdown ``MemoryStore`` (the common per-session case),
    the service is built with a ``FilesystemMemoryProvider`` fallback over the
    SAME store — so vector retrieval degrades to FTS keyword search when no
    embedder is configured (the real fallback chain, memory-architecture.md §3.4).
    """
    key = id(provider)
    svc = _services.get(key)
    if svc is None or svc.provider is not provider:
        fallback = None
        # Build the FTS fallback when the provider carries a markdown projection
        # (read_preferences/search). The native record store is the primary; the
        # markdown FTS is the degraded path.
        if hasattr(provider, "read_preferences") and hasattr(provider, "search"):
            try:
                from personalclaw.memory_providers.filesystem import FilesystemMemoryProvider

                fallback = FilesystemMemoryProvider(cast("MemoryStore", provider))
            except Exception:
                fallback = None
        svc = MemoryService(provider, fallback=fallback)
        _services[key] = svc
    return svc
