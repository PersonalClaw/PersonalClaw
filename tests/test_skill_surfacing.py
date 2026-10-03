"""Semantic skill surfacing at turn time (skill-semantic-surfacing, #26)."""

from __future__ import annotations

from collections import Counter

import pytest

import personalclaw.skills.surfacing as surf
from personalclaw.agents.native.tool_vectors import Embedder, ToolVectors
from personalclaw.skills.surfacing import surface_skills


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
    monkeypatch.setattr(surf, "bound_embedder", lambda: None)


def _bound(monkeypatch, tmp_path, fn, skills: list[dict]) -> dict:
    """Bind ``fn`` as the embedding model, with ``skills``' descriptions already in a skill index
    of the test's own: the ``index``/``index_path`` a turn reads."""
    embedder = Embedder(model="stub:v1", one=fn, many=None)
    monkeypatch.setattr(surf, "bound_embedder", lambda: embedder)
    index = ToolVectors(what="skill description", thread="skill-vectors")
    path = tmp_path / ".emb.json"
    index.want(path, embedder, [surf.skill_text(s) for s in skills])
    assert index.drain(timeout=10)
    return {"index": index, "index_path": path}


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


# ── Feedback suppression withholds a matched skill ──


def test_suppressed_skill_is_withheld():
    # Two skills that both match on keyword; one is feedback-suppressed and must
    # not surface, the healthy one must.
    skills = [
        _skill("wrong-one", "d", "deploy service"),
        _skill("good-one", "d", "deploy service"),
    ]
    out = surface_skills(
        "deploy service",
        skills,
        max_skills=5,
        suppressed={("skill_synthesis", "wrong-one")},
    )
    assert out == ["good-one"]  # the suppressed producer is withheld, the healthy one stays


def test_default_suppresses_nothing():
    # Absent the arg (fail-open default), a matched skill surfaces as before.
    skills = [_skill("s", "d", "deploy service")]
    assert surface_skills("deploy service", skills, max_skills=5) == ["s"]


def test_explain_shows_suppression_withheld():
    skills = [_skill("wrong-one", "d", "deploy service")]
    rows = surface_skills(
        "deploy service",
        skills,
        max_skills=5,
        suppressed={("skill_synthesis", "wrong-one")},
        explain=True,
    )
    row = next(r for r in rows if r["key"] == "wrong-one")
    # matched but withheld — surfaced with a suppression reason, never dropped silently
    assert row["included"] is False
    assert row["kw_score"] >= 0.7  # it DID match — this is a withhold, not a miss
    assert "suppression" in row["reason"]


def test_suppression_only_bites_matches():
    # A skill that would not match anyway is unaffected by being in the set (no crash,
    # not surfaced for an unrelated reason).
    skills = [_skill("off-topic", "d", "shorten url")]
    out = surface_skills(
        "deploy service",
        skills,
        max_skills=5,
        suppressed={("skill_synthesis", "off-topic")},
    )
    assert out == []


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
    # triggers say "invoice"; query says "charge" — no keyword overlap, but both
    # embed onto the billing axis → semantic hit. The library around it is what lets meaning
    # tell a clear match: billing is the one skill on the query's axis.
    skills = [
        _skill("billing", "handle invoice questions", "invoice help", path=str(tmp_path / "b.md"))
    ] + [
        _skill(f"other-{n}", f"unrelated task {n}", path=str(tmp_path / f"o{n}.md"))
        for n in range(4)
    ]
    bound = _bound(monkeypatch, tmp_path, _StubEmbedder(), skills)
    out = surface_skills(
        "help me with this charge", skills, max_skills=3, semantic_threshold=0.9, **bound
    )
    assert out == ["billing"]


def test_semantic_off_topic_excluded(monkeypatch, tmp_path):
    skills = [_skill("shipping", "delivery tracking", "deliver track", path=str(tmp_path / "s.md"))]
    bound = _bound(monkeypatch, tmp_path, _StubEmbedder(), skills)
    out = surface_skills(
        "question about my invoice", skills, max_skills=3, semantic_threshold=0.9, **bound
    )
    assert out == []  # billing query, shipping skill → orthogonal → no hit


# ── the skill index (text + model keyed, filled in the background) ──


def test_a_changed_description_is_embedded_again_and_an_unchanged_one_is_read(
    monkeypatch, tmp_path
):
    calls: list[str] = []

    def embed(text):
        calls.append(text)
        return [1.0, 0.0]

    skills = [_skill("a", "first skill", "x"), _skill("b", "second skill", "y")]
    bound = _bound(monkeypatch, tmp_path, embed, skills)
    calls.clear()
    skills[1] = _skill("b", "second skill, reworded", "y")

    surface_skills("anything at all", skills, max_skills=3, **bound)
    assert bound["index"].drain(timeout=10)
    # The reworded description is embedded by the index's own thread, which the turn starts
    # before it embeds the message, so the two calls come in either order. What holds is which
    # texts were embedded, once each: the message and the reworded description, and never the
    # description that did not change.
    assert Counter(calls) == Counter(["anything at all", surf.skill_text(skills[1])]), calls


def test_the_index_is_read_back_after_a_restart(monkeypatch, tmp_path):
    calls: list[str] = []

    def embed(text):
        calls.append(text)
        return [0.5, 0.5]

    skills = [_skill("a", "first skill", "x")]
    bound = _bound(monkeypatch, tmp_path, embed, skills)
    calls.clear()
    restarted = {"index": ToolVectors(), "index_path": bound["index_path"]}

    rows = surface_skills("anything at all", skills, max_skills=3, explain=True, **restarted)
    assert restarted["index"].drain(timeout=10)
    assert calls == ["anything at all"], "the description's vector came from the file"
    assert rows[0]["sem_score"] > 0


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
