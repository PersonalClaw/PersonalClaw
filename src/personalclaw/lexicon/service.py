"""LexiconService (core LEX) — the vocabulary engine over the LexiconStore.

Owns the four behaviors the design locks:
  * ``rebuild_from_graph`` — sync terms from knowledge-graph entities (name + aliases +
    entity_type), computing Double Metaphone keys. Incremental-friendly (upsert by id).
  * ``select_bias_terms``  — a ranked, budget-capped term list for PRE-decode biasing
    (LEX.3): context entities first (a meeting's own notes prime its audio), then global
    top-weighted top-ups.
  * ``correct``            — POST-decode phonetic correction of a TranscriptResult (LEX.4):
    fires only when it SOUNDS like a Lexicon term, is SPELLED differently, and the source
    word is low-confidence; hybrid policy = auto-apply learned/high-confidence, propose the
    rest.
  * ``learn_correction``   — the feedback loop (LEX.5): upsert heard→meant, raise the
    term's weight, flip auto_apply past threshold.

A module-level ``select_bias_terms`` async wrapper is the seam the TranscriptionNode calls
(so the node needs no service handle); it returns [] when the Lexicon is empty/disabled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from personalclaw.lexicon.phonetics import phonetic_keys
from personalclaw.lexicon.store import LexiconStore

logger = logging.getLogger(__name__)

# Whisper's initial_prompt budget is ~224 tokens; keep the bias list well under that.
_BIAS_BUDGET = 64
# Common English words we never "correct" toward a Lexicon term (stop-lexicon guard).
_STOP_WORDS = frozenset(
    "the a an and or but if then this that these those is are was were be been being have "
    "has had do does did will would can could should may might must to of in on at for with "
    "as by from up out so no not yes it its he she they we you i me my your our their".split()
)
# Only correct words at/below this per-word confidence (L0 synergy — leave confident words).
_LOW_PROB = 0.6


def _prefix_match(a: str, b: str, min_len: int = 3) -> bool:
    """True if one metaphone key is a prefix of the other (both ≥ min_len). Catches a
    truncating mishearing whose key is a shortened form of the real term's key."""
    if len(a) < min_len or len(b) < min_len or a == b:
        return False
    return a.startswith(b) or b.startswith(a)


@dataclass
class Correction:
    start: float
    end: float
    heard: str
    suggested: str
    score: float


@dataclass
class CorrectionOutcome:
    applied: list[Correction] = field(default_factory=list)
    suggested: list[Correction] = field(default_factory=list)


class LexiconService:
    def __init__(self, store: LexiconStore | None = None):
        self.store = store or LexiconStore()

    # ── LEX.1 sources: rebuild from graph entities ──────────────────────────────
    def rebuild_from_graph(self, entities: list[dict]) -> int:
        """Sync the Lexicon's graph-sourced terms from a list of entity dicts
        (``{id, name, entity_type, aliases}``). Returns the number of terms upserted.
        A true resync: graph terms whose entity no longer exists are pruned, and a
        user-disabled (pruned) graph term stays disabled. Manual/learned terms are
        untouched (upsert_term won't downgrade their source)."""
        n = 0
        synced_ids: set[str] = set()
        for e in entities:
            name = (e.get("name") or "").strip()
            if not name:
                continue
            aliases = e.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            keys: list[str] = []
            for surface in [name, *aliases]:
                for tok in str(surface).split():
                    keys.extend(phonetic_keys(tok))
            term_id = f"graph_{e.get('id') or name.lower()}"
            self.store.upsert_term(
                term_id=term_id,
                canonical=name,
                aliases=[str(a) for a in aliases],
                phonetic_keys=sorted(set(keys)),
                entity_type=str(e.get("entity_type") or ""),
                weight=1.0,
                source="graph",
            )
            synced_ids.add(term_id)
            n += 1
        pruned = self.store.prune_graph_terms(keep=synced_ids)
        if pruned:
            logger.info("lexicon rebuild: pruned %d stale graph terms", pruned)
        return n

    def add_manual_term(
        self, canonical: str, *, aliases: list[str] | None = None, entity_type: str = "manual"
    ) -> str:
        keys: list[str] = []
        for surface in [canonical, *(aliases or [])]:
            for tok in str(surface).split():
                keys.extend(phonetic_keys(tok))
        term_id = f"manual_{canonical.lower().replace(' ', '_')}"
        self.store.upsert_term(
            term_id=term_id,
            canonical=canonical,
            aliases=aliases or [],
            phonetic_keys=sorted(set(keys)),
            entity_type=entity_type,
            weight=2.0,
            source="manual",  # manual terms outrank raw graph terms
        )
        return term_id

    # ── LEX.3 pre-decode biasing ────────────────────────────────────────────────
    def select_bias_terms(
        self, *, context_terms: list[str] | None = None, budget: int = _BIAS_BUDGET
    ) -> list[str]:
        """Ranked, budget-capped bias terms. Context terms (e.g. a meeting's sibling
        entities) come FIRST, then globally top-weighted terms fill the rest."""
        out: list[str] = []
        seen: set[str] = set()
        for t in context_terms or []:
            t = t.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
                if len(out) >= budget:
                    return out
        for term in self.store.top_terms(budget * 2):
            if term.canonical.lower() not in seen:
                seen.add(term.canonical.lower())
                out.append(term.canonical)
                if len(out) >= budget:
                    break
        return out

    # ── LEX.4 post-decode phonetic correction ───────────────────────────────────
    def correct(self, result) -> CorrectionOutcome:
        """Correct mis-heard terms in a TranscriptResult in place (auto-apply branch) +
        collect proposals (propose branch). ``result`` is an stt.provider.TranscriptResult.

        For each word: if it sounds like a Lexicon term but is spelled differently and
        isn't a common word, and (learned auto-correction OR low source confidence), rewrite
        it (auto) or attach a suggestion (propose). Timestamps are preserved."""
        outcome = CorrectionOutcome()
        auto = self.store.auto_corrections()
        for seg in result.segments:
            words = seg.words or []
            for w in words:
                raw = w.word.strip()
                bare = raw.strip(".,!?;:\"'()[]").strip()
                if not bare or bare.lower() in _STOP_WORDS or len(bare) < 3:
                    continue
                # 1. Learned auto-correction (exact heard match) → always apply.
                if bare.lower() in auto:
                    meant = auto[bare.lower()]
                    if meant != bare:
                        w.word = w.word.replace(bare, meant)
                        outcome.applied.append(Correction(w.start, w.end, bare, meant, 1.0))
                    continue
                # 2. Phonetic match against Lexicon terms.
                cand = self._best_phonetic_match(bare)
                if cand is None:
                    continue
                term, score = cand
                # Fire only when it SOUNDS like the term but is SPELLED differently.
                if term.lower() == bare.lower():
                    continue
                low_conf = (w.prob or 1.0) <= _LOW_PROB
                if score >= 0.9 and low_conf:
                    w.word = w.word.replace(bare, term)
                    outcome.applied.append(Correction(w.start, w.end, bare, term, score))
                elif low_conf:
                    outcome.suggested.append(Correction(w.start, w.end, bare, term, score))
        # Re-derive segment text from (possibly rewritten) words, and the flat text.
        for seg in result.segments:
            if seg.words:
                seg.text = "".join(w.word for w in seg.words).strip() or seg.text
        if result.segments:
            result.text = " ".join(s.text for s in result.segments if s.text).strip() or result.text
        return outcome

    def _best_phonetic_match(self, word: str) -> tuple[str, float] | None:
        """Return (canonical, score) of the best same-sound Lexicon term, or None. Score
        blends phonetic-key overlap with a literal-difference bonus (sounds same, spelled
        different is the strongest signal)."""
        from difflib import SequenceMatcher

        best: tuple[str, float] | None = None
        keys = phonetic_keys(word)
        for key in keys:
            for term in self.store.terms_for_phonetic_key(key):
                literal = SequenceMatcher(None, word.lower(), term.canonical.lower()).ratio()
                # exact key (sounds alike) + spelled differently (low literal) → high score.
                score = 0.7 + (1.0 - literal) * 0.3
                if best is None or score > best[1]:
                    best = (term.canonical, round(score, 3))
        # Prefix fallback: a severe mishearing can truncate the word to a shorter metaphone
        # key (real case: "Cubeer"=KPR vs "Kubernetes"=KPRN). Only when no exact-key match
        # won, and scored lower (prefix is a weaker signal → stays in the "propose" band).
        if best is None:
            for key in keys:
                for term in self.store.terms_for_phonetic_prefix(key):
                    tkeys = phonetic_keys(term.canonical)
                    if not any(_prefix_match(key, tk) for tk in tkeys):
                        continue
                    literal = SequenceMatcher(None, word.lower(), term.canonical.lower()).ratio()
                    score = 0.6 + (1.0 - literal) * 0.2  # weaker → proposes, won't auto-apply
                    if best is None or score > best[1]:
                        best = (term.canonical, round(score, 3))
        return best

    # ── LEX.5 learned-corrections loop ───────────────────────────────────────────
    def learn_correction(
        self, heard: str, meant: str, *, always: bool = False, threshold: int = 2
    ) -> None:
        """Record a user transcript fix: upsert heard→meant, raise the term's weight so it's
        more likely biased next time, and (past threshold / 'always') flip auto_apply."""
        heard = heard.strip()
        meant = meant.strip()
        if not heard or not meant or heard == meant:
            return
        key = (phonetic_keys(meant) or [""])[0]
        self.store.upsert_correction(
            heard, meant, phonetic_key=key, auto_apply=True if always else None, threshold=threshold
        )
        # Make sure the corrected term exists in the Lexicon + bump its weight. Exact
        # canonical match — a LIKE substring search would let a superstring term (e.g.
        # "Kubernetes Cluster") mask "Kubernetes", skipping the add AND stranding the
        # weight bump (bump_weight matches canonical exactly).
        existing = self.store.get_term_by_canonical(meant)
        if existing is None:
            self.add_manual_term(meant, entity_type="learned")
        self.store.bump_weight(meant, delta=1.0)

    # ── CRUD passthroughs used by the API ────────────────────────────────────────
    def list_terms(self, **kw):
        return self.store.list_terms(**kw)

    def list_corrections(self, **kw):
        return self.store.list_corrections(**kw)


# ── module-level bias seam (called by TranscriptionNode) ─────────────────────────
async def select_bias_terms(
    *, context_item_id: str | None = None, budget: int = _BIAS_BUDGET
) -> list[str]:
    """The node-facing entry point (LEX.3). Resolves context entities for the item's
    siblings when available, else falls back to globally top-weighted terms. Returns []
    when the Lexicon is empty/unavailable so transcription just runs unbiased."""
    try:
        svc = get_lexicon_service()
        if svc.store.count_terms() == 0:
            return []
        context_terms: list[str] = []
        if context_item_id:
            context_terms = _context_terms_for_item(context_item_id)
        return svc.select_bias_terms(context_terms=context_terms, budget=budget)
    except Exception:
        logger.debug("select_bias_terms failed (non-fatal)", exc_info=True)
        return []


def _context_terms_for_item(item_id: str) -> list[str]:
    """Entity names related to *item_id* (its own extracted entities), for context-scoped
    biasing. Best-effort — returns [] on any failure."""
    try:
        from personalclaw.knowledge import get_knowledge_store

        store = get_knowledge_store()
        rows = store.db.execute(
            """SELECT e.name FROM entities e JOIN mentions m ON m.entity_id = e.id
               WHERE m.item_id = ? LIMIT 100""",
            (item_id,),
        )
        return [r["name"] for r in rows if r["name"]]
    except Exception:
        return []


_service: LexiconService | None = None


def get_lexicon_service() -> LexiconService:
    global _service
    if _service is None:
        _service = LexiconService()
    return _service
