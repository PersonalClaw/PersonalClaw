"""learn-after-turn-review skill axis: the forked-LLM 4-tier skill ladder.

A bounded background review decides at most one skill action (refine < support_file
< create, biasing toward refining what exists) and ENQUEUES it as a propose-only
proposal — never a live write. The guardrail blocks environment-failure turns.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw import after_turn_review as atr
from personalclaw.skills import loader as loader_mod
from personalclaw.skills import proposals


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader_mod, "config_dir", lambda: tmp_path)
    import personalclaw.skills.marketplace as mp

    monkeypatch.setattr(mp, "SKILL_DISCOVERY_PATHS", [])
    return tmp_path


def _completion(obj):
    async def _c(prompt: str) -> str:
        return json.dumps(obj)

    return _c


def _run(**kw):
    return asyncio.run(
        atr.run_skill_ladder_review(
            session_key=kw.get("session_key", "sess:1"),
            user_message=kw.get("user_message", "always deploy via the staging gate first"),
            assistant_text=kw.get(
                "assistant_text", "Understood — I'll gate deploys through staging."
            ),
            loaded_skills=kw.get("loaded_skills", ["deploy-flow"]),
            completion=kw["completion"],
        )
    )


# ── prompt + parse ───────────────────────────────────────────────────────────


def test_parse_tolerates_code_fence():
    raw = '```json\n{"action":"none"}\n```'
    assert atr._parse_ladder_json(raw) == {"action": "none"}


def test_parse_extracts_embedded_object():
    raw = 'Here is my decision: {"action":"create","slug":"x"} — done.'
    assert atr._parse_ladder_json(raw)["action"] == "create"


def test_parse_returns_none_on_garbage():
    assert atr._parse_ladder_json("not json at all") is None
    assert atr._parse_ladder_json("") is None


# ── action routing (all through the propose-only queue) ──────────────────────


def test_create_enqueues_proposal(home):
    summary = _run(
        completion=_completion(
            {
                "action": "create",
                "slug": "staging-gate",
                "description": "Gate deploys through staging",
                "triggers": "deploy",
                "procedure_md": "1. deploy to staging\n2. verify\n3. promote",
                "rationale": "new class",
            }
        )
    )
    assert summary and "staging-gate" in summary
    pend = proposals.list_pending()
    assert len(pend) == 1 and pend[0].slug == "staging-gate"
    assert pend[0].kind == "new"


def test_refine_enqueues_with_target(home):
    summary = _run(
        completion=_completion(
            {
                "action": "refine",
                "slug": "deploy-flow",
                "target": "deploy-flow",
                "description": "Refined deploy flow",
                "triggers": "deploy",
                "procedure_md": "updated steps here",
                "rationale": "improve existing",
            }
        )
    )
    assert "refine" in summary
    pend = proposals.list_pending()
    assert pend[0].kind == "refine" and pend[0].refine_target == "deploy-flow"


def test_action_none_enqueues_nothing(home):
    summary = _run(completion=_completion({"action": "none"}))
    assert summary is None
    assert proposals.list_pending() == []


def test_missing_fields_enqueues_nothing(home):
    summary = _run(completion=_completion({"action": "create", "slug": "x"}))  # no desc/procedure
    assert summary is None
    assert proposals.list_pending() == []


def test_guardrail_blocks_env_failure_turn(home):
    # An environment-failure turn can't teach a skill — blocked before the LLM call.
    called = {"n": 0}

    async def _c(prompt: str) -> str:
        called["n"] += 1
        return json.dumps(
            {"action": "create", "slug": "x", "description": "d", "procedure_md": "p"}
        )

    summary = _run(user_message="the deploy tool is broken and permission denied", completion=_c)
    assert summary is None
    assert called["n"] == 0  # never even called the model
    assert proposals.list_pending() == []


def test_completion_failure_is_safe(home):
    async def _boom(prompt: str) -> str:
        raise RuntimeError("model down")

    summary = _run(completion=_boom)
    assert summary is None
    assert proposals.list_pending() == []


def test_never_writes_live_skill(home):
    # The whole point: the ladder proposes, it does not install.
    _run(
        completion=_completion(
            {
                "action": "create",
                "slug": "should-not-be-live",
                "description": "d",
                "triggers": "t",
                "procedure_md": "p",
            }
        )
    )
    from personalclaw.skills.loader import SkillsLoader

    assert SkillsLoader(install_builtins=False).load_skill("auto/should-not-be-live") is None
    assert SkillsLoader(install_builtins=False).load_skill("should-not-be-live") is None
    # …but it IS in the review queue.
    assert any(p.slug == "should-not-be-live" for p in proposals.list_pending())


# ── the last-run marker (`G44`) ───────────────────────────────────────────────
#
# The defect: `GET /api/skills/proposals` answering `{"proposals": []}` is two
# different facts wearing one face — "the ladder ran and had nothing to propose"
# and "the ladder never ran". Every test below holds the proposals list EMPTY and
# asserts on the marker instead, because that is the exact condition under which
# the two were indistinguishable. A rail that only checked the filing case would
# be testing the observation that was never ambiguous.


def test_last_review_is_none_before_any_pass(home):
    """The vacuity floor. `None` is the value that means "never ran"; if a fresh
    home already reported a dict, every "recorded" assertion below would pass on a
    store that cannot say no."""
    assert proposals.last_review() is None


def test_a_pass_that_proposes_nothing_is_still_observable(home):
    """THE rail. Same empty proposals list as `test_action_none_enqueues_nothing`,
    but now the two worlds are distinguishable from the marker alone."""
    assert _run(completion=_completion({"action": "none"})) is None
    assert proposals.list_pending() == []  # indistinguishable half, unchanged

    rec = proposals.last_review()
    assert rec is not None, "a pass that proposed nothing recorded nothing — `G44` is back"
    assert rec["verdict"] == "no_action"
    assert rec["session_key"] == "sess:1"
    assert isinstance(rec["elapsed_ms"], int) and rec["elapsed_ms"] >= 0
    assert rec["at"]


def test_a_failing_pass_records_its_verdict(home):
    """Recorded from the `finally`, so the expensive-and-dead case — the one an
    operator most needs and the one that files nothing — is on the surface too."""

    async def _boom(prompt: str) -> str:
        raise RuntimeError("model down")

    assert _run(completion=_boom) is None
    assert proposals.list_pending() == []
    rec = proposals.last_review()
    assert rec is not None and rec["verdict"] == "provider_error"


def test_marker_is_overwritten_not_appended(home):
    """One marker, always the latest pass — not an unbounded second store."""
    _run(completion=_completion({"action": "none"}), session_key="sess:first")
    _run(completion=_completion({"action": "none"}), session_key="sess:second")
    rec = proposals.last_review()
    assert rec is not None and rec["session_key"] == "sess:second"


def test_marker_is_not_mistaken_for_a_proposal(home):
    """The sentinel-collision floor: the marker must not live in the directory
    `list_pending()` globs, or a working ladder would grow a phantom proposal."""
    _run(completion=_completion({"action": "none"}))
    assert proposals.last_review() is not None  # it really was written
    assert proposals.list_pending() == []


def test_route_reports_the_marker_and_its_absence(home):
    """The API is the surface `O31` was taken from, so the field has to be ON it —
    a recorded marker no route exposes would be the same observation as before."""
    import asyncio

    from personalclaw.dashboard.handlers import skills as skills_handlers

    async def _payload():
        resp = await skills_handlers.api_skill_proposals_list(object())  # request unused
        return json.loads(resp.text)

    before = asyncio.run(_payload())
    assert before["proposals"] == [] and before["lastReview"] is None

    _run(completion=_completion({"action": "none"}))

    after = asyncio.run(_payload())
    assert after["proposals"] == [], "the two payloads must differ ONLY in lastReview"
    assert after["lastReview"] is not None
    assert after["lastReview"]["verdict"] == "no_action"
