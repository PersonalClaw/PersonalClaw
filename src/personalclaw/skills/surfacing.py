"""Semantic skill surfacing at turn time (skill-semantic-surfacing, #26).

PClaw auto-*creates* and *refines* skills, but turn-time **recall** was the weak
half: ``SkillsLoader.get_triggered_skills`` matches purely on **keyword word-overlap**
against the ``triggers`` field. A synthesized skill whose triggers don't lexically
overlap a *paraphrased* request never surfaces → never reused → never refined. The
create → reuse → refine loop stayed half-open.

This adds **semantic matching** as a union with the keyword path, reusing the
embedder already wired for memory (``embedding_providers.registry.get_active_embed_fn``,
a sync ``(text) -> list[float] | None``):

1. Embed the user turn ONCE.
2. Score every candidate skill by ``max(cosine_vs_cached_desc_embedding,
   keyword_overlap)`` — so a paraphrase the keyword path missed still surfaces, and
   a keyword the embedder ranks low still fires (no regression vs the old behavior).
3. Rank by score, **tie-break by use_count** (#25 — proven-useful skills win ties),
   cap at ``skills.max_triggered``.

**Meaning alone picks at most one skill, and only a clear one.** A cosine over the floor is
not a match by itself: measured with a small local embedder against 23 skills, "yes, go
ahead" scored 0.57, "the second one" 0.61, and a request to file PDFs cleared the floor for
ten skills at once — while "It's ~/Notes/Garden/Home/kitchen-reno.md." pulled a family-trip
skill's whole body into the message at 0.552, 0.009 ahead of the next skill. What does
separate a real match is how far it LEADS the rest: every real one led the runner-up by
0.92 to 2.63 times the spread of that message's scores across the library, and no false one
by more than 0.52. So a skill joins on meaning only when it clears the floor AND leads the
next skill by :data:`SEMANTIC_LEAD` spreads (:func:`_semantic_standout`). The spread is the
message's own, which is what keeps the rule from depending on one embedder's scale. A
skill's declared ``triggers`` are the author's words and are unaffected.

Skill-description embeddings are cached **mtime+model-keyed** in a sidecar
``<skills_dir>/.skill_embeddings.json`` so we embed each description once and
re-embed only when the SKILL.md changes (or the active model changes). No SKILL.md
is ever rewritten.

Degrades cleanly: no active embedding model → pure keyword (identical to the old
path). Never raises — a surfacing error returns the keyword result, never breaks a turn.
"""

from __future__ import annotations

import json
import logging
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, overload

from personalclaw.atomic_write import atomic_write
from personalclaw.skills.loader import skills_dir

logger = logging.getLogger(__name__)

# Cosine gate for a semantic match — calibrated against workflows/surfacing's 0.62
# and vector_memory's short-text 0.55. Skill descriptions are short → 0.55. A floor, not a
# match: see SEMANTIC_LEAD.
DEFAULT_SEMANTIC_THRESHOLD = 0.55

#: How far the closest skill must lead the next one to join a turn on meaning alone, in
#: standard deviations of that message's scores across the library. Measured (module
#: docstring): real matches led by 0.92 or more, false ones by 0.52 or less.
SEMANTIC_LEAD = 0.75

#: The fewest skills with a score before a lead means anything: the spread of two or three
#: numbers is not a measure of what stands out. Below it, meaning picks nothing and the
#: declared triggers still do.
MIN_SCORED_FOR_LEAD = 5

# Keyword fallback gate — must match SkillsLoader._MIN_TRIGGER_OVERLAP so the
# keyword half of the union is byte-identical to the legacy trigger path.
_KEYWORD_GATE = 0.7

_EMBED_CACHE_FILE = ".skill_embeddings.json"


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _keyword_score(query_words: set[str], triggers: str) -> tuple[float, bool]:
    """Best per-phrase word-overlap of the query against comma-separated triggers.

    Returns ``(best_overlap, negated)``. A phrase prefixed with ``!`` is a negative
    trigger: if it matches, the skill is excluded. Mirrors
    ``SkillsLoader.get_triggered_skills`` exactly so the keyword half is unchanged.
    """
    best = 0.0
    for trigger in triggers.split(","):
        trigger = trigger.strip().lower()
        if not trigger:
            continue
        if trigger.startswith("!"):
            neg_words = set(re.findall(r"\w+", trigger[1:]))
            if neg_words and neg_words <= query_words:
                return 0.0, True
        else:
            tw = set(re.findall(r"\w+", trigger))
            if tw:
                best = max(best, len(tw & query_words) / len(tw))
    return best, False


class _EmbedCache:
    """mtime+model-keyed sidecar cache of skill-description embeddings.

    ``{skill_path: {"mtime": float, "model": str, "vec": [float]}}``. A miss
    (new/changed file or model switch) re-embeds; everything else is a file read.
    """

    def __init__(self, path: Path | None = None):
        self._path = path or (skills_dir() / _EMBED_CACHE_FILE)
        self._data: dict[str, dict] | None = None
        self._dirty = False

    def _load(self) -> dict[str, dict]:
        if self._data is None:
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._data = raw if isinstance(raw, dict) else {}
            except (OSError, json.JSONDecodeError):
                self._data = {}
        return self._data

    def get_or_embed(
        self, path: str, text: str, mtime: float, model: str, embed_fn
    ) -> list[float] | None:
        data = self._load()
        row = data.get(path)
        if isinstance(row, dict) and row.get("mtime") == mtime and row.get("model") == model:
            vec = row.get("vec")
            return vec if isinstance(vec, list) else None
        try:
            vec = embed_fn(text)
        except Exception:
            return None
        if vec is None:
            return None
        data[path] = {"mtime": mtime, "model": model, "vec": vec}
        self._dirty = True
        return vec

    def flush(self) -> None:
        if self._dirty and self._data is not None:
            try:
                atomic_write(self._path, json.dumps(self._data, sort_keys=True))
                self._dirty = False
            except Exception:
                logger.debug("skill embedding cache flush failed", exc_info=True)


def _active_embedder():
    """Return ``(embed_fn, model_label)`` or ``(None, "")`` if no model is active."""
    try:
        from personalclaw.embedding_providers.registry import (
            _active_embedding_spec,
            get_active_embed_fn,
        )
    except Exception:
        return None, ""
    try:
        fn = get_active_embed_fn()
    except Exception:
        fn = None
    if fn is None:
        return None, ""
    model = ""
    try:
        spec = _active_embedding_spec()
        if spec:
            model = f"{spec[0]}:{spec[1]}"
    except Exception:
        model = ""
    return fn, model


@overload
def surface_skills(
    text: str,
    skills: list[dict],
    *,
    max_skills: int,
    semantic_threshold: float = ...,
    embed_cache: _EmbedCache | None = ...,
    suppressed: set[tuple[str, str]] = ...,
    explain: Literal[False] = ...,
) -> list[str]: ...


@overload
def surface_skills(
    text: str,
    skills: list[dict],
    *,
    max_skills: int,
    semantic_threshold: float = ...,
    embed_cache: _EmbedCache | None = ...,
    suppressed: set[tuple[str, str]] = ...,
    explain: Literal[True],
) -> list[dict]: ...


def surface_skills(
    text: str,
    skills: list[dict],
    *,
    max_skills: int,
    semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    embed_cache: _EmbedCache | None = None,
    suppressed: set[tuple[str, str]] | None = None,
    explain: bool = False,
) -> list[str] | list[dict]:
    """Return up to *max_skills* skill keys for this turn, semantic ∪ keyword.

    *skills* is ``SkillsLoader.list_skills(with_usage=True)`` output (needs
    key/description/triggers/path/always/use_count). The ``use_count`` field (#25)
    is the tiebreak. Excludes ``always`` skills (injected unconditionally elsewhere).

    *suppressed* (FS-6) is Feedback-Signal's withholding set — the
    ``(producer_kind, producer_id)`` pairs :func:`feedback.suppressed_producers`
    computed as persistently-wrong. A matched skill whose identity
    ``("skill_synthesis", <key>)`` is in it is WITHHELD: a producer whose judgments
    keep drawing 👎 stops surfacing until the user edits it (which clears it). Default
    empty = suppress nothing; the live wiring (``SkillsLoader.get_surfaced_skills``)
    fetches the set fail-open, so a feedback fault degrades to normal surfacing.

    With ``explain=True`` (the Doctor surfacing simulator, PLATFORM-RESILIENCE §3.1)
    it returns instead a per-candidate breakdown — ``[{key, kw_score, sem_score,
    threshold_kw, threshold_sem, negated, included, reason}, …]`` for EVERY candidate
    (including excluded ones, so the user sees WHY something was excluded — a withheld
    skill is surfaced with a suppression reason, never dropped silently), sorted the
    same way. Pure deterministic scoring, zero LLM calls — the same arms the real turn
    computes, returned instead of discarded.
    """
    suppressed = suppressed or set()
    query = (text or "").strip()
    if not query or not skills:
        return []
    query_words = set(re.findall(r"\w+", query.lower()))
    if not query_words:
        return []

    embed_fn, model = _active_embedder()
    query_vec = None
    if embed_fn is not None:
        try:
            query_vec = embed_fn(query)
        except Exception:
            query_vec = None
    cache = embed_cache or _EmbedCache()

    # Pass 1 — score every candidate, because whether meaning picks a skill depends on how
    # the OTHER skills score for this message (`_semantic_standout`).
    explained: list[dict] = []
    candidates: list[tuple[dict, float, float | None]] = []  # (skill, kw_score, sem_score)
    for s in skills:
        key = s.get("key", "")
        if s.get("always"):
            if explain:
                explained.append(
                    _explain_row(
                        key,
                        0.0,
                        0.0,
                        semantic_threshold,
                        False,
                        False,
                        "always-on (injected unconditionally)",
                    )
                )
            continue
        if s.get("status") == "archived":
            if explain:
                explained.append(
                    _explain_row(
                        key,
                        0.0,
                        0.0,
                        semantic_threshold,
                        False,
                        False,
                        "archived by the skill curator",
                    )
                )
            continue  # curator (#27) archived this skill — keep on disk, off the turn
        triggers = s.get("triggers", "") or ""
        kw_score, negated = _keyword_score(query_words, triggers) if triggers else (0.0, False)
        if negated:
            if explain:
                explained.append(
                    _explain_row(
                        key,
                        kw_score,
                        0.0,
                        semantic_threshold,
                        True,
                        False,
                        "vetoed by a negative (!) trigger",
                    )
                )
            continue  # a negative trigger vetoes the skill outright

        sem_score: float | None = None
        if query_vec is not None:
            # Embed the description (+ triggers, which carry intent phrases).
            desc = (s.get("description", "") or s.get("name", "")).strip()
            embed_text = f"{desc}\n{triggers}".strip() if triggers else desc
            try:
                mtime = Path(s["path"]).stat().st_mtime
            except (OSError, KeyError):
                mtime = 0.0
            vec = cache.get_or_embed(
                s.get("path", s.get("key", "")), embed_text, mtime, model, embed_fn
            )
            if vec is not None:
                sem_score = _cosine(query_vec, vec)
        candidates.append((s, kw_score, sem_score))
    cache.flush()

    standout = _semantic_standout(
        [(c[0].get("key", ""), c[2]) for c in candidates if c[2] is not None],
        semantic_threshold,
    )

    # Pass 2 — decide.
    scored: list[tuple[float, int, str]] = []  # (score, use_count, key)
    for s, kw_score, sem_value in candidates:
        key = s.get("key", "")
        sem_score = sem_value or 0.0
        kw_hit = kw_score >= _KEYWORD_GATE
        sem_hit = standout.key == key
        matched = kw_hit or sem_hit
        # A matched producer Feedback-Signal marked persistently-wrong is
        # WITHHELD — the match stands, but the skill does not surface until the user
        # edits it (which clears the suppression). Membership is exact-tuple against
        # feedback's producer identity for a skill: ("skill_synthesis", <key>).
        withheld = matched and ("skill_synthesis", key) in suppressed
        included = matched and not withheld
        if explain:
            if withheld:
                reason = (
                    "withheld (feedback suppression: this skill's judgments keep drawing 👎 — "
                    "edit it to restore surfacing)"
                )
            elif included:
                why = (
                    f"keyword {kw_score:.2f} ≥ {_KEYWORD_GATE}"
                    if kw_hit
                    else f"semantic {sem_score:.2f} ≥ {semantic_threshold}, and {standout.why}"
                )
                reason = f"included ({why})"
            elif query_vec is None:
                reason = (
                    f"excluded (keyword {kw_score:.2f} < {_KEYWORD_GATE}; no embedder for semantic)"
                )
            elif sem_score < semantic_threshold:
                reason = (
                    f"excluded (keyword {kw_score:.2f} < {_KEYWORD_GATE}, "
                    f"semantic {sem_score:.2f} < {semantic_threshold})"
                )
            elif standout.key:
                reason = (
                    f"excluded (keyword {kw_score:.2f} < {_KEYWORD_GATE}; semantic "
                    f"{sem_score:.2f} clears {semantic_threshold}, but meaning picks "
                    f"{standout.key}, the clear closest)"
                )
            else:
                reason = (
                    f"excluded (keyword {kw_score:.2f} < {_KEYWORD_GATE}; semantic "
                    f"{sem_score:.2f} clears {semantic_threshold}, but {standout.why})"
                )
            explained.append(
                _explain_row(
                    key,
                    kw_score,
                    sem_score,
                    semantic_threshold,
                    False,
                    included,
                    reason,
                    lead=standout.lead if key == standout.closest else None,
                )
            )
        if not included:
            continue
        # Union score: the better of the two normalized signals.
        score = max(kw_score, sem_score)
        use_count = int(s.get("use_count", 0) or 0)  # #25 tiebreak
        scored.append((score, use_count, key))

    if explain:
        # Included first (by score), then excluded — the same ordering the real turn
        # would rank, with excluded candidates surfaced for the "why not?" answer.
        explained.sort(
            key=lambda r: (not r["included"], -max(r["kw_score"], r["sem_score"]), r["key"])
        )
        return explained
    # Rank by score desc, then proven use_count desc (#25 tiebreak), then key for
    # determinism.
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return [key for _score, _uc, key in scored[:max_skills]]


@dataclass(frozen=True)
class _Standout:
    """What meaning alone says about one message: the skill it picks, if any, and why."""

    #: The skill meaning picks, or ``""`` when it picks none.
    key: str
    #: The closest skill, picked or not, and how many spreads it leads the next one by.
    closest: str
    lead: float | None
    #: The clause the Doctor's surfacing simulator prints for it.
    why: str


def _semantic_standout(scores: list[tuple[str, float]], floor: float) -> _Standout:
    """The one skill this message's meaning singles out, or none.

    The closest skill is picked when it clears ``floor`` AND leads the next one by
    :data:`SEMANTIC_LEAD` standard deviations of the message's scores across the library.
    A lead is only measured over :data:`MIN_SCORED_FOR_LEAD` scores or more.
    """
    if len(scores) < MIN_SCORED_FOR_LEAD:
        return _Standout(
            key="",
            closest="",
            lead=None,
            why=(
                f"only {len(scores)} skill(s) could be scored, too few to tell a clear match "
                f"on meaning (needs {MIN_SCORED_FOR_LEAD})"
            ),
        )
    ranked = sorted(scores, key=lambda kv: (-kv[1], kv[0]))
    (top_key, top), (next_key, runner_up) = ranked[0], ranked[1]
    spread = statistics.pstdev([v for _k, v in ranked])
    lead = (top - runner_up) / spread if spread > 0 else 0.0
    if top >= floor and lead >= SEMANTIC_LEAD:
        return _Standout(
            key=top_key,
            closest=top_key,
            lead=lead,
            why=(
                f"it leads the next skill, {next_key}, by {lead:.2f} spreads "
                f"(≥ {SEMANTIC_LEAD})"
            ),
        )
    if top < floor:
        why = f"no skill clears the {floor} floor on meaning (closest: {top_key}, {top:.2f})"
    else:
        why = (
            f"{top_key} is closest and leads {next_key} by only {lead:.2f} spreads "
            f"(needs {SEMANTIC_LEAD}), so meaning singles out no skill"
        )
    return _Standout(key="", closest=top_key, lead=lead, why=why)


def _explain_row(
    key: str,
    kw_score: float,
    sem_score: float,
    threshold_sem: float,
    negated: bool,
    included: bool,
    reason: str,
    *,
    lead: float | None = None,
) -> dict:
    return {
        "key": key,
        "kw_score": round(kw_score, 3),
        "sem_score": round(sem_score, 3),
        "threshold_kw": _KEYWORD_GATE,
        "threshold_sem": threshold_sem,
        # The closest skill's lead over the next one, in spreads; None on every other row.
        "lead": None if lead is None else round(lead, 2),
        "threshold_lead": SEMANTIC_LEAD,
        "negated": negated,
        "included": included,
        "reason": reason,
    }


def search_skills(query: str, skills: list[dict], *, limit: int = 20) -> list[dict]:
    """Rank the ENTIRE skill library against ``query`` and return
    ``[{key, description}]`` — backs the ``skill_search`` tool so an agent can
    discover skills it wasn't surfaced this turn (progressive skill discovery,
    the parity of ``tool_search``). Scores by ``max(semantic, keyword, substring)``;
    lexical is the fail-open floor (no embed model → keyword-only). Generous (no
    score gate, unlike per-turn surfacing) and ignores the ``max_skills`` cap.
    Excludes only ``archived`` skills (kept on disk, off discovery)."""
    q = (query or "").strip()
    qwords = set(re.findall(r"\w+", q.lower()))
    embed_fn, model = _active_embedder()
    query_vec = None
    if embed_fn is not None and q:
        try:
            query_vec = embed_fn(q)
        except Exception:
            query_vec = None
    cache = _EmbedCache()
    scored: list[tuple[float, int, str, str]] = []  # (score, use_count, key, desc)
    for s in skills:
        if s.get("status") == "archived":
            continue
        key = s.get("key", "")
        desc = (s.get("description", "") or s.get("name", "") or key).strip()
        triggers = s.get("triggers", "") or ""
        hay = f"{key} {desc} {triggers}".lower()
        haywords = set(re.findall(r"\w+", hay))
        kw = (len(qwords & haywords) / len(qwords)) if qwords else 0.0
        substr = 0.5 if q and q.lower() in hay else 0.0
        sem = 0.0
        if query_vec is not None:
            embed_text = f"{desc}\n{triggers}".strip() if triggers else desc
            try:
                mtime = Path(s["path"]).stat().st_mtime
            except (OSError, KeyError):
                mtime = 0.0
            vec = cache.get_or_embed(s.get("path", key), embed_text, mtime, model, embed_fn)
            if vec is not None:
                sem = _cosine(query_vec, vec)
        score = max(kw, substr, sem)
        if score > 0 or not q:
            scored.append((score, int(s.get("use_count", 0) or 0), key, desc[:200]))
    cache.flush()
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return [{"key": k, "description": d} for _s, _uc, k, d in scored[:limit]]
