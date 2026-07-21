"""Shared goal-scoping grill seam (#32) — assess → check_memory → decompose."""

from __future__ import annotations

import asyncio

from personalclaw import grill


def _run(coro):
    return asyncio.run(coro)


def _ask_returning(*responses):
    """An AskFn that returns queued responses in order (last repeats)."""
    seq = list(responses)

    async def _ask(prompt: str) -> str:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return _ask


# ── assess_goal ──


def test_assess_clear_goal_not_ambiguous():
    ask = _ask_returning('{"ambiguous": false, "questions": []}')
    amb, qs = _run(grill.assess_goal("ship the thing", ask))
    assert amb is False and qs == []


def test_assess_ambiguous_with_questions():
    ask = _ask_returning('{"ambiguous": true, "questions": ["Which platform?", "By when?"]}')
    amb, qs = _run(grill.assess_goal("make it good", ask))
    assert amb is True and qs == ["Which platform?", "By when?"]


def test_assess_ambiguous_but_no_questions_is_not_ambiguous():
    # ambiguous=true but no questions → can't act on it → treated as not-ambiguous
    ask = _ask_returning('{"ambiguous": true, "questions": []}')
    amb, _ = _run(grill.assess_goal("x", ask))
    assert amb is False


def test_assess_handles_garbage():
    amb, qs = _run(grill.assess_goal("x", _ask_returning("not json")))
    assert amb is False and qs == []


# ── check_memory ──


def test_check_memory_none_recall():
    assert _run(grill.check_memory("goal", None)) == ""


def test_check_memory_returns_recall():
    async def _recall(q):
        return "prior decision: use Postgres"

    assert "Postgres" in _run(grill.check_memory("db choice", _recall))


def test_check_memory_swallows_errors():
    async def _recall(q):
        raise RuntimeError("boom")

    assert _run(grill.check_memory("x", _recall)) == ""


# ── grill: flat shape (campaigns) ──


def test_grill_flat_decomposes():
    ask = _ask_returning(
        '{"ambiguous": false, "questions": []}',  # assess
        '["feasibility", "risks", "alternatives"]',  # decompose
    )
    r = _run(grill.grill("a real goal here", shape="flat", ask=ask, assess=True))
    assert r.shape == "flat"
    assert r.sub_goals == ["feasibility", "risks", "alternatives"]
    assert r.phases == []


def test_grill_flat_skip_assess():
    ask = _ask_returning('["one", "two"]')
    r = _run(grill.grill("goal", shape="flat", ask=ask, assess=False))
    assert r.sub_goals == ["one", "two"]
    assert r.clarifying_questions == []


def test_grill_flat_caps_at_20():
    ask = _ask_returning(str([f"sg{i}" for i in range(30)]).replace("'", '"'))
    r = _run(grill.grill("goal", shape="flat", ask=ask, assess=False))
    assert len(r.sub_goals) == 20


# ── grill: tree shape (projects) ──


def test_grill_tree_builds_phases():
    tree = '{"phases": [{"title": "Scope", "description": "d", "steps": [{"title": "platform", "prompt": "Which platform?"}]}]}'  # noqa: E501
    r = _run(grill.grill("goal", shape="tree", ask=_ask_returning(tree), assess=False))
    assert r.shape == "tree"
    assert len(r.phases) == 1
    assert r.phases[0]["title"] == "Scope"
    assert r.phases[0]["steps"][0]["prompt"] == "Which platform?"


def test_grill_tree_drops_stepless_and_promptless():
    tree = (
        '{"phases": [{"title": "P", "steps": [{"title": "x"}, {"title": "y", "prompt": "real?"}]}]}'
    )
    r = _run(grill.grill("goal", shape="tree", ask=_ask_returning(tree), assess=False))
    # only the step with a prompt survives
    assert [s["prompt"] for s in r.phases[0]["steps"]] == ["real?"]


# ── memory-checked + save_decisions ──


def test_grill_records_memory_hit():
    async def _recall(q):
        return "prior: already chose X"

    ask = _ask_returning('["a", "b"]')
    r = _run(grill.grill("goal", shape="flat", ask=ask, recall=_recall, assess=False))
    assert r.memory_hits == 1


def test_grill_saves_decisions():
    saved = []
    ask = _ask_returning('["alpha", "beta"]')
    _run(grill.grill("goal", shape="flat", ask=ask, save=saved.append, assess=False))
    assert saved == ["alpha", "beta"]


def test_grill_save_failure_does_not_break():
    def _boom(d):
        raise RuntimeError("save failed")

    ask = _ask_returning('["x"]')
    r = _run(grill.grill("goal", shape="flat", ask=ask, save=_boom, assess=False))
    assert r.sub_goals == ["x"]  # decomposition still returned
