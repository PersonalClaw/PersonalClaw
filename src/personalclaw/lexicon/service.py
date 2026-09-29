"""LexiconService (core LEX) — the vocabulary engine over the LexiconStore.

Owns the four behaviors the design locks:
  * ``rebuild_from_graph`` — sync terms from knowledge-graph entities (name + aliases +
    entity_type), computing Double Metaphone keys. Incremental-friendly (upsert by id).
    ``sync_from_graph`` runs it by itself whenever the knowledge graph has changed since the
    last sync, and every consult goes through ``current_lexicon`` — so the Lexicon IS built
    from the graph, as the Vocabulary section says. It used to fill only when someone pressed
    Rebuild in Settings: a library whose notes named the owner's colleagues had an empty
    Lexicon, and voice memos spelled those names however the recogniser heard them.
  * ``select_bias_terms``  — a ranked, budget-capped term list for PRE-decode biasing
    (LEX.3): context entities first (a meeting's own notes prime its audio), then global
    top-weighted top-ups.
  * ``correct``            — POST-decode correction of a TranscriptResult (LEX.4): a learned
    correction (one the user taught) is applied; a low-confidence word that SOUNDS like a
    Lexicon word AND is spelled much like it is proposed, never applied on that evidence.
  * ``learn_correction``   — the feedback loop (LEX.5): upsert heard→meant, raise the
    term's weight, flip auto_apply past threshold.

A module-level ``select_bias_terms`` async wrapper is the seam the TranscriptionNode calls
(so the node needs no service handle); it returns [] when the Lexicon is empty/disabled.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

from personalclaw.lexicon.phonetics import phonetic_keys
from personalclaw.lexicon.store import LexiconStore

logger = logging.getLogger(__name__)

#: The knowledge graph's fingerprint at the last sync, in the store's ``meta`` table.
_GRAPH_FINGERPRINT = "graph_fingerprint"

#: How an entity's TYPE ranks its term. Decoder biasing hands the recogniser only the first
#: ~200 characters of the ranked list (Whisper's prompt window), so the order decides whose
#: names it is told about: people first, since their names are what it mishears and nothing
#: later in the transcript can recover them; then the named things; general concepts last,
#: as ordinary words it already knows. The types are the knowledge extractor's.
_TYPE_RANK: dict[str, float] = {
    "person": 0.6,
    "org": 0.4,
    "project": 0.4,
    "place": 0.4,
    "service": 0.3,
    "api": 0.3,
    "technology": 0.3,
    "tool": 0.3,
}
#: Within a type, the entities the owner's items mention most rank first. Bounded, so every
#: graph term stays below a manual term's weight (2.0): a word the owner added outranks any the
#: graph supplied.
_MENTION_RANK = 0.3


def graph_weight(entity_type: str, mentions: int) -> float:
    """The weight a graph-sourced term starts at: its type's rank, then how often it is
    mentioned (see :data:`_TYPE_RANK`). Always in [1.0, 2.0)."""
    count = max(0, int(mentions or 0))
    rank = _TYPE_RANK.get((entity_type or "").strip().lower(), 0.0)
    return round(1.0 + rank + _MENTION_RANK * count / (count + 2), 4)


@dataclass(frozen=True)
class GraphSnapshot:
    """The knowledge graph as the Lexicon reads it: a cheap fingerprint that changes whenever
    an entity or a mention does, and (once the fingerprint says they are needed) the entities
    with their mention counts."""

    fingerprint: str
    entities: list[dict] = field(default_factory=list)


def _knowledge_db():
    """A READ-ONLY connection to the knowledge store's database, or ``None`` when there is no
    knowledge store yet. Read-only and separate from the store's own connection, as the memory
    graph's seeding reads it: the Lexicon must never write there, and a sync must not share a
    connection with an ingest that may be mid-transaction on it."""
    from personalclaw.knowledge import knowledge_db_path
    from personalclaw.sqlite_compat import sqlite3

    path = knowledge_db_path()
    if not os.path.exists(path):
        return None
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def read_knowledge_graph(*, with_entities: bool) -> GraphSnapshot:
    """The knowledge graph's fingerprint, and its entities when *with_entities*.

    Raises when the graph exists and cannot be read: a sync PRUNES graph terms whose entity is
    gone, so a failed read taken for "no entities" would wipe them all. A home with no
    knowledge store yet has an empty graph, which is not a failed read."""
    db = _knowledge_db()
    if db is None:
        return GraphSnapshot(fingerprint="empty")
    try:
        count, latest, surfaces = db.execute(
            "SELECT COUNT(*), COALESCE(MAX(updated_at), ''), "
            "TOTAL(LENGTH(name) + LENGTH(COALESCE(aliases, ''))) FROM entities"
        ).fetchone()
        (mention_rows,) = db.execute("SELECT COUNT(*) FROM mentions").fetchone()
        fingerprint = f"{count}:{latest}:{int(surfaces)}:{mention_rows}"
        if not with_entities:
            return GraphSnapshot(fingerprint=fingerprint)
        mentions = {
            r["entity_id"]: int(r["n"])
            for r in db.execute("SELECT entity_id, COUNT(*) AS n FROM mentions GROUP BY entity_id")
        }
        entities: list[dict] = []
        for r in db.execute("SELECT id, name, entity_type, aliases FROM entities"):
            try:
                aliases = json.loads(r["aliases"] or "[]")
            except (TypeError, ValueError):
                aliases = []
            entities.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "entity_type": r["entity_type"],
                    "aliases": aliases if isinstance(aliases, list) else [],
                    "mentions": mentions.get(r["id"], 0),
                }
            )
        return GraphSnapshot(fingerprint=fingerprint, entities=entities)
    finally:
        db.close()


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
#: How alike a word must be SPELLED to the Lexicon word it sounds like (difflib's ratio) before
#: it is proposed as that word. Double Metaphone keys are coarse — "Okay", "echo" and "Aku"
#: all key to AK — and the score used to RISE as the spellings diverged, so once the Lexicon
#: held a colleague's name, a transcript's "Okay" was rewritten into it. A real mishearing
#: keeps much of the word ("Acu" for "Aku" is 0.67, "kubernetis" for "Kubernetes" 0.9);
#: a different word that only shares a key does not ("Okay" for "Aku" is 0.29).
_MIN_SPELLING = 0.5


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
        (``{id, name, entity_type, aliases, mentions}``). Returns the number of terms upserted.
        A true resync: graph terms whose entity no longer exists are pruned, and a
        user-disabled (pruned) graph term stays disabled. Manual/learned terms are
        untouched (upsert_term won't downgrade their source). Each term's weight is
        :func:`graph_weight`, and one transaction carries the whole resync."""
        n = 0
        synced_ids: set[str] = set()
        with self.store.batch():
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
                entity_type = str(e.get("entity_type") or "")
                self.store.upsert_term(
                    term_id=term_id,
                    canonical=name,
                    aliases=[str(a) for a in aliases],
                    phonetic_keys=sorted(set(keys)),
                    entity_type=entity_type,
                    weight=graph_weight(entity_type, e.get("mentions") or 0),
                    source="graph",
                )
                synced_ids.add(term_id)
                n += 1
            pruned = self.store.prune_graph_terms(keep=synced_ids)
        if pruned:
            logger.info("lexicon rebuild: pruned %d stale graph terms", pruned)
        return n

    def sync_from_graph(self, *, force: bool = False) -> int | None:
        """Bring the graph-sourced terms up to date with the knowledge graph: a resync when the
        graph's fingerprint differs from the one at the last sync, or when *force* (Rebuild).
        Returns how many terms it synced, or ``None`` when the graph had not changed. Raises
        when the graph cannot be read, leaving every term as it was."""
        current = read_knowledge_graph(with_entities=False)
        if not force and current.fingerprint == self.store.get_meta(_GRAPH_FINGERPRINT):
            return None
        graph = read_knowledge_graph(with_entities=True)
        n = self.rebuild_from_graph(graph.entities)
        self.store.set_meta(_GRAPH_FINGERPRINT, graph.fingerprint)
        logger.info("lexicon: synced %d terms from the knowledge graph", n)
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
        """Correct mis-heard terms in a TranscriptResult in place + collect proposals.
        ``result`` is an stt.provider.TranscriptResult.

        A learned correction (heard → meant, one the user taught) is APPLIED. Otherwise a
        low-confidence word that sounds like a word of a Lexicon term and is spelled much like
        it (:data:`_MIN_SPELLING`) is PROPOSED as that word — never applied on that evidence:
        a sound-alike key is too coarse to rewrite a transcript by itself. A word already
        spelled as a term, or one of its aliases or words, is left alone. Timestamps are
        preserved."""
        outcome = CorrectionOutcome()
        auto = self.store.auto_corrections()
        known = self._known_words()
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
                # 2. Spelled as a Lexicon word already, or heard with confidence → right.
                if bare.lower() in known or (w.prob or 1.0) > _LOW_PROB:
                    continue
                # 3. Sounds like, and is spelled much like, a Lexicon word → propose it.
                cand = self._best_phonetic_match(bare)
                if cand is not None:
                    suggested, score = cand
                    outcome.suggested.append(Correction(w.start, w.end, bare, suggested, score))
        # Re-derive segment text from (possibly rewritten) words, and the flat text.
        for seg in result.segments:
            if seg.words:
                seg.text = "".join(w.word for w in seg.words).strip() or seg.text
        if result.segments:
            result.text = " ".join(s.text for s in result.segments if s.text).strip() or result.text
        return outcome

    def _known_words(self) -> set[str]:
        """Every word of every enabled term, lowercased: its canonical form's and its
        aliases'. A transcript word spelled as one of these is not a mishearing."""
        known: set[str] = set()
        for term in self.store.enabled_terms():
            for surface in [term.canonical, *term.aliases]:
                for tok in str(surface).split():
                    tok = tok.strip(".,!?;:\"'()[]").lower()
                    if tok:
                        known.add(tok)
        return known

    def _best_phonetic_match(self, word: str) -> tuple[str, float] | None:
        """``(the Lexicon word *word* most likely is, score)``, or ``None``.

        The candidate is a single WORD of a term (``"Aku"`` of ``"Aku Lehto"``), never the
        whole term, since one heard word is one word. It must share a Double Metaphone key
        with *word* and be spelled alike to at least :data:`_MIN_SPELLING`; the score is
        ``0.5 + 0.5 × spelling``. A truncating mishearing whose key is a PREFIX of the word's
        (real case: "Cubeer"=KPR for "Kubernetes"=KPRN) is considered only when no exact key
        matched, and scores 0.1 lower."""
        from difflib import SequenceMatcher

        heard = word.lower()
        keys = phonetic_keys(word)
        best: tuple[str, float] | None = None

        def consider(terms, sounds_alike, penalty: float) -> None:
            nonlocal best
            for term in terms:
                for surface in [term.canonical, *term.aliases]:
                    for tok in str(surface).split():
                        tok = tok.strip(".,!?;:\"'()[]")
                        tkeys = phonetic_keys(tok)
                        if not any(sounds_alike(k, tk) for k in keys for tk in tkeys):
                            continue
                        spelling = SequenceMatcher(None, heard, tok.lower()).ratio()
                        if spelling < _MIN_SPELLING:
                            continue
                        score = round(0.5 + 0.5 * spelling - penalty, 3)
                        if best is None or score > best[1]:
                            best = (tok, score)

        for key in keys:
            consider(self.store.terms_for_phonetic_key(key), lambda a, b: a == b, 0.0)
        if best is None:
            for key in keys:
                consider(self.store.terms_for_phonetic_prefix(key), _prefix_match, 0.1)
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
        svc = current_lexicon()
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


def current_lexicon() -> LexiconService:
    """The Lexicon as a transcription (or the Vocabulary list) consults it: its graph terms
    first brought up to date with the knowledge graph. Cheap when nothing changed (one small
    read of the graph's fingerprint). Best-effort: a sync that fails is logged and leaves the
    terms as they were — a Lexicon problem must never stop a transcription."""
    svc = get_lexicon_service()
    try:
        svc.sync_from_graph()
    except Exception:
        logger.warning("lexicon: could not sync from the knowledge graph", exc_info=True)
    return svc
