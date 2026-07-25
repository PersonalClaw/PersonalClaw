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
from pathlib import Path
from typing import Literal, overload

from personalclaw.atomic_write import atomic_write
from personalclaw.skills.loader import skills_dir

logger = logging.getLogger(__name__)

# Cosine gate for a semantic match — calibrated against workflows/surfacing's 0.62
# and vector_memory's short-text 0.55. Skill descriptions are short → 0.55.
DEFAULT_SEMANTIC_THRESHOLD = 0.55

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
    explain: Literal[True],
) -> list[dict]: ...


def surface_skills(
    text: str,
    skills: list[dict],
    *,
    max_skills: int,
    semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    embed_cache: _EmbedCache | None = None,
    explain: bool = False,
) -> list[str] | list[dict]:
    """Return up to *max_skills* skill keys for this turn, semantic ∪ keyword.

    *skills* is ``SkillsLoader.list_skills(with_usage=True)`` output (needs
    key/description/triggers/path/always/use_count). The ``use_count`` field (#25)
    is the tiebreak. Excludes ``always`` skills (injected unconditionally elsewhere).

    With ``explain=True`` (the Doctor surfacing simulator, PLATFORM-RESILIENCE §3.1)
    it returns instead a per-candidate breakdown — ``[{key, kw_score, sem_score,
    threshold_kw, threshold_sem, negated, included, reason}, …]`` for EVERY candidate
    (including excluded ones, so the user sees WHY something was excluded), sorted the
    same way. Pure deterministic scoring, zero LLM calls — the same arms the real turn
    computes, returned instead of discarded.
    """
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

    scored: list[tuple[float, int, str]] = []  # (score, use_count, key)
    explained: list[dict] = []
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

        kw_hit = kw_score >= _KEYWORD_GATE

        sem_score = 0.0
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
        sem_hit = sem_score >= semantic_threshold

        included = kw_hit or sem_hit
        if explain:
            if included:
                why = (
                    f"keyword {kw_score:.2f} ≥ {_KEYWORD_GATE}"
                    if kw_hit
                    else f"semantic {sem_score:.2f} ≥ {semantic_threshold}"
                )
                reason = f"included ({why})"
            elif query_vec is None:
                reason = (
                    f"excluded (keyword {kw_score:.2f} < {_KEYWORD_GATE}; no embedder for semantic)"
                )
            else:
                reason = (
                    f"excluded (keyword {kw_score:.2f} < {_KEYWORD_GATE}, "
                    f"semantic {sem_score:.2f} < {semantic_threshold})"
                )
            explained.append(
                _explain_row(key, kw_score, sem_score, semantic_threshold, False, included, reason)
            )
        if not included:
            continue
        # Union score: the better of the two normalized signals.
        score = max(kw_score, sem_score)
        use_count = int(s.get("use_count", 0) or 0)  # #25 tiebreak
        scored.append((score, use_count, s["key"]))

    cache.flush()
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


def _explain_row(
    key: str,
    kw_score: float,
    sem_score: float,
    threshold_sem: float,
    negated: bool,
    included: bool,
    reason: str,
) -> dict:
    return {
        "key": key,
        "kw_score": round(kw_score, 3),
        "sem_score": round(sem_score, 3),
        "threshold_kw": _KEYWORD_GATE,
        "threshold_sem": threshold_sem,
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
