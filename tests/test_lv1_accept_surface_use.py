"""LEARNING-VISIBILITY T1.3 (LV-1): the accept → surface → use loop closes.

One integration test over the REAL turn path, in the order a user experiences it:

    propose  →  accept  →  the next matching prompt surfaces the accepted skill
             →  its usage count increments

The seam driven here is ``ContextBuilder.build_message`` — the single function a
chat turn calls to assemble its prompt. It is chosen over calling
``surface_skills`` (or ``SkillsLoader.get_surfaced_skills``) directly because the
claim under test is about *the next matching prompt*, and only ``build_message``
contains the whole chain: ``skills.get_surfaced_skills(text)`` → ``load_skill``
per hit → ``allocate_skills(...)`` → ``SkillUsageStore().record_uses(loaded)``
(``context.py``, the ``if skill_requests:`` branch). Driving one link in
isolation would leave the other three unproven, and the use counter in
particular is written *only* on that path — a test that called
``surface_skills`` alone could pass with the counter entirely unwired.

The ContextBuilder is built ONCE, before the proposal is accepted, and both
turns run through that same long-lived instance — which is how the gateway holds
it. That also makes the "before" turn a real vacuity floor rather than a
different object: the same builder must go from *not* surfacing the skill to
surfacing it, with nothing between the two calls but the accept.

No model calls: the embedder is pinned absent, so surfacing runs its keyword
half (an isolated home has no embedding provider configured anyway, but pinning
it means this test can never reach out to one).
"""

from __future__ import annotations

import pytest

from personalclaw.context import ContextBuilder
from personalclaw.memory import MemoryStore
from personalclaw.skills import proposals
from personalclaw.skills import surfacing as surfacing_mod
from personalclaw.skills.loader import SkillsLoader
from personalclaw.skills.usage import SkillUsageStore

# The prompt shares every word of the skill's triggers, so the keyword half of
# the union scores 1.0 — comfortably over surfacing's 0.7 gate.
PROMPT = "please run the widget release flow now"
TRIGGERS = "widget release flow"
SLUG = "release-flow"
SKILL_NAME = "auto/release-flow"
BODY_MARKER = "BODY-LV1-RELEASE-FLOW"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated home, following tests/test_skill_proposals.py's fixture exactly.

    Patching the skills loader's ``config_dir`` binding is enough to redirect the
    whole chain: the proposal queue (``config_dir()/skills/.proposals``), the live
    skills tree (``skills_dir()``), and the usage sidecar
    (``skills_dir()/.usage.json``) all resolve through it.
    """
    from personalclaw.skills import loader as loader_mod
    from personalclaw.skills import marketplace as mp

    monkeypatch.setattr(loader_mod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(mp, "SKILL_DISCOVERY_PATHS", [])
    # No model calls: force the no-embedder branch of the semantic ∪ keyword union.
    monkeypatch.setattr(surfacing_mod, "_active_embedder", lambda: (None, ""))
    return tmp_path


def _pin_config(monkeypatch):
    """Pin the two skills knobs the turn path reads.

    ``progressive_disclosure_threshold`` must stay above the number of matches so
    bodies are INLINED — above the threshold the turn injects an index only and
    deliberately records no use (the body never reached the turn).
    """
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    cfg.skills.max_triggered = 10
    cfg.skills.progressive_disclosure_threshold = 8
    monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", classmethod(lambda cls: cfg))
    return cfg


def test_accepted_skill_surfaces_on_the_next_prompt_and_counts_the_use(home, monkeypatch):
    _pin_config(monkeypatch)
    skills_root = home / "skills"
    usage = SkillUsageStore(path=skills_root / ".usage.json")
    builder = ContextBuilder(
        memory=MemoryStore(workspace=home / "ws"),
        skills=SkillsLoader(skills_path=skills_root, install_builtins=False),
    )

    # ── 1. propose ── nothing live may change (the plan's inviolable rule).
    prop = proposals.enqueue(
        slug=SLUG,
        description="How to cut a widget release",
        triggers=TRIGGERS,
        procedure_md=f"1. tag\n2. build\n3. publish\n\n{BODY_MARKER}",
        session_key="sess:lv1",
        created_at="2026-08-17T00:00:00+00:00",
    )
    assert prop is not None
    assert [p.id for p in proposals.list_pending()] == [prop.id]
    assert not (skills_root / "auto" / SLUG).exists(), "a proposal must not write a live skill"
    assert SKILL_NAME not in {s["key"] for s in builder.skills.list_skills()}

    # ── vacuity floor ── the same prompt, before accepting, surfaces nothing and
    # counts nothing. If either assertion here were already satisfied, the
    # post-accept assertions below would be free.
    before, _ = builder.build_message(PROMPT, is_new_session=False)
    assert BODY_MARKER not in before
    assert SKILL_NAME not in before
    assert usage.get(SKILL_NAME).count == 0
    assert usage.all_usage() == {}

    # ── 2. accept ── the human approval that installs it.
    created = proposals.accept(prop.id)
    assert created == SKILL_NAME
    assert proposals.list_pending() == []

    # ── 3. the next matching prompt surfaces THAT skill, body inlined ──
    after, _ = builder.build_message(PROMPT, is_new_session=False)
    assert f"[Skill: {SKILL_NAME}]" in after, "accepted skill did not surface on a matching prompt"
    assert BODY_MARKER in after, "skill surfaced but its procedure never reached the turn"
    assert "INDEX only" not in after  # bodies inlined, so a use is expected

    # ── 4. …and the use is counted, for that skill and no other ──
    counts = {name: u.count for name, u in usage.all_usage().items()}
    assert counts == {SKILL_NAME: 1}
    assert usage.get(SKILL_NAME).last_used_at != ""

    # A second matching turn increments again — the counter accumulates rather
    # than latching at "seen once".
    builder.build_message(PROMPT, is_new_session=False)
    assert usage.get(SKILL_NAME).count == 2
