"""Semantic skill surfacing at turn time (skill-semantic-surfacing, #26)."""

from __future__ import annotations

from pathlib import Path

import pytest

import personalclaw.skills.surfacing as surf
from personalclaw.skills.surfacing import _EmbedCache, surface_skills


def _skill(
    key: str, desc: str, triggers: str = "", *, always=False, use_count=0, path=None
) -> dict:
    return {
        "key": key,
        "name": key,
        "description": desc,
        "triggers": triggers,
        "path": path or f"/skills/{key}/SKILL.md",
        "dir": f"/skills/{key}",
        "always": always,
        "use_count": use_count,
    }


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Default: no active embedding model → pure keyword (legacy parity)."""
    monkeypatch.setattr(surf, "_active_embedder", lambda: (None, ""))


# ── keyword path (no embedder) — must match legacy get_triggered_skills ──


def test_keyword_match():
    skills = [_skill("tiny-url", "shorten a url", "shorten url, tiny url")]
    assert surface_skills("please shorten this url", skills, max_skills=3) == ["tiny-url"]


def test_keyword_miss():
    skills = [_skill("tiny-url", "shorten a url", "shorten url")]
    assert surface_skills("what is the weather", skills, max_skills=3) == []


def test_negative_trigger_vetoes():
    skills = [_skill("s", "d", "search code, !weather")]
    assert surface_skills("search code weather", skills, max_skills=3) == []


def test_always_skills_excluded():
    skills = [_skill("pinned", "x", "hello world", always=True)]
    assert surface_skills("hello world", skills, max_skills=3) == []


def test_max_skills_cap():
    skills = [_skill(f"s{i}", "d", "alpha beta") for i in range(5)]
    assert len(surface_skills("alpha beta", skills, max_skills=2)) == 2


def test_empty_query():
    assert surface_skills("", [_skill("s", "d", "x")], max_skills=3) == []


def test_use_count_breaks_keyword_ties():
    # identical triggers → identical keyword score → higher use_count wins ordering
    skills = [
        _skill("cold", "d", "deploy service", use_count=0),
        _skill("hot", "d", "deploy service", use_count=9),
    ]
    out = surface_skills("deploy service", skills, max_skills=2)
    assert out == ["hot", "cold"]


# ── semantic path (stub embedder) ──


class _StubEmbedder:
    """Maps text → a 2-d vector by keyword presence, so cosine is predictable."""

    def __call__(self, text: str):
        t = text.lower()
        # axis 0 = "billing", axis 1 = "shipping"
        return [
            1.0 if "bill" in t or "invoice" in t or "charge" in t else 0.0,
            1.0 if "ship" in t or "deliver" in t else 0.0,
        ]


def test_semantic_surfaces_paraphrase_keyword_misses(monkeypatch, tmp_path):
    monkeypatch.setattr(surf, "_active_embedder", lambda: (_StubEmbedder(), "stub:v1"))
    # triggers say "invoice"; query says "charge" — no keyword overlap, but both
    # embed onto the billing axis → semantic hit.
    skills = [
        _skill("billing", "handle invoice questions", "invoice help", path=str(tmp_path / "b.md"))
    ]
    Path(skills[0]["path"]).write_text("x")
    cache = _EmbedCache(path=tmp_path / ".emb.json")
    out = surface_skills(
        "help me with this charge", skills, max_skills=3, semantic_threshold=0.9, embed_cache=cache
    )
    assert out == ["billing"]


def test_semantic_off_topic_excluded(monkeypatch, tmp_path):
    monkeypatch.setattr(surf, "_active_embedder", lambda: (_StubEmbedder(), "stub:v1"))
    skills = [_skill("shipping", "delivery tracking", "deliver track", path=str(tmp_path / "s.md"))]
    Path(skills[0]["path"]).write_text("x")
    cache = _EmbedCache(path=tmp_path / ".emb.json")
    out = surface_skills(
        "question about my invoice", skills, max_skills=3, semantic_threshold=0.9, embed_cache=cache
    )
    assert out == []  # billing query, shipping skill → orthogonal → no hit


# ── embedding cache (mtime + model keyed) ──


def test_embed_cache_reuses_until_mtime_changes(tmp_path):
    calls = {"n": 0}

    def embed(_text):
        calls["n"] += 1
        return [1.0, 0.0]

    f = tmp_path / "s.md"
    f.write_text("v1")
    cache = _EmbedCache(path=tmp_path / ".emb.json")
    mt = f.stat().st_mtime
    cache.get_or_embed(str(f), "desc", mt, "m1", embed)
    cache.get_or_embed(str(f), "desc", mt, "m1", embed)  # same mtime+model → cached
    assert calls["n"] == 1
    cache.get_or_embed(str(f), "desc", mt + 1, "m1", embed)  # mtime change → re-embed
    assert calls["n"] == 2
    cache.get_or_embed(str(f), "desc", mt + 1, "m2", embed)  # model change → re-embed
    assert calls["n"] == 3


def test_embed_cache_persists_across_instances(tmp_path):
    calls = {"n": 0}

    def embed(_text):
        calls["n"] += 1
        return [0.5, 0.5]

    f = tmp_path / "s.md"
    f.write_text("v1")
    p = tmp_path / ".emb.json"
    c1 = _EmbedCache(path=p)
    c1.get_or_embed(str(f), "desc", f.stat().st_mtime, "m1", embed)
    c1.flush()
    c2 = _EmbedCache(path=p)
    c2.get_or_embed(str(f), "desc", f.stat().st_mtime, "m1", embed)
    assert calls["n"] == 1  # second instance read from disk


# ── search_skills (PT5): discover ANY skill by capability (parity w/ tool_search) ──


def test_search_skills_ranks_full_library():
    skills = [
        _skill("deploy", "deploy a service to prod", "deploy, release"),
        _skill("tiny-url", "shorten a url", "shorten url"),
        _skill("weather", "get the forecast", "weather"),
    ]
    hits = surf.search_skills("how do I release to production", skills, limit=10)
    keys = [h["key"] for h in hits]
    assert "deploy" in keys
    assert all(set(h.keys()) == {"key", "description"} for h in hits)


def test_search_skills_generous_no_gate():
    # a weak keyword overlap still appears (discovery is generous, unlike surfacing).
    skills = [_skill("thumb", "generate a small preview image", "thumbnail")]
    assert any(h["key"] == "thumb" for h in surf.search_skills("preview", skills))


def test_search_skills_excludes_archived():
    skills = [
        _skill("live", "do a thing", "thing"),
        {**_skill("old", "do a thing", "thing"), "status": "archived"},
    ]
    keys = [h["key"] for h in surf.search_skills("thing", skills, limit=10)]
    assert "live" in keys and "old" not in keys


def test_search_skills_empty_query_returns_sample():
    skills = [_skill(f"s{i}", "d", "x") for i in range(5)]
    assert len(surf.search_skills("", skills, limit=3)) == 3
