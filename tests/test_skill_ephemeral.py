"""skill-ephemeral-promotion: session-live skill drafts + end-of-session promotion.

A draft is captured in-the-moment (skill_remember), lives only for its session
until the user promotes it to a permanent tier (this-agent / all-agents) or forgets
it. Promotion reuses the tier writers; nothing lands in the library unreviewed.
"""

from __future__ import annotations

import pytest

from personalclaw.skills import ephemeral
from personalclaw.skills import loader as loader_mod
from personalclaw.skills.loader import SkillsLoader


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader_mod, "config_dir", lambda: tmp_path)
    import personalclaw.skills.marketplace as mp

    monkeypatch.setattr(mp, "SKILL_DISCOVERY_PATHS", [])
    return tmp_path


def test_remember_writes_draft(home):
    d = ephemeral.remember("sess:1", "Deploy Checklist", "1. run tests\n2. ship")
    assert d is not None and d.slug == "deploy-checklist"
    drafts = ephemeral.list_drafts("sess:1")
    assert len(drafts) == 1
    assert drafts[0].title == "Deploy Checklist"
    assert "run tests" in drafts[0].body


def test_remember_rejects_empty(home):
    assert ephemeral.remember("sess:1", "", "body") is None
    assert ephemeral.remember("sess:1", "title", "") is None


def test_remember_is_idempotent_by_title(home):
    ephemeral.remember("sess:1", "Same Title", "v1")
    ephemeral.remember("sess:1", "Same Title", "v2 updated")
    drafts = ephemeral.list_drafts("sess:1")
    assert len(drafts) == 1  # overwrote, not duplicated
    assert "v2 updated" in drafts[0].body


def test_drafts_are_session_scoped(home):
    ephemeral.remember("sess:1", "A skill", "body a")
    assert len(ephemeral.list_drafts("sess:1")) == 1
    assert len(ephemeral.list_drafts("sess:2")) == 0  # other session sees nothing


def test_context_block_makes_drafts_live(home):
    ephemeral.remember("sess:1", "Live Skill", "do the thing")
    block = ephemeral.context_block("sess:1")
    assert "Live Skill" in block and "do the thing" in block
    assert ephemeral.context_block("sess:empty") == ""


def test_redacts_secrets_in_body(home):
    # The draft body runs through the same credential redactor as auto-extraction
    # (conservative: catches key=value secret shapes). Prove redaction is applied.
    d = ephemeral.remember("sess:1", "creds", "set aws_secret_access_key=AKIAIOSFODNN7EXAMPLE now")
    assert "AKIAIOSFODNN7EXAMPLE" not in d.body
    assert "REDACTED" in d.body


def test_promote_to_global(home):
    ephemeral.remember("sess:1", "Global Skill", "global steps")
    name = ephemeral.promote("sess:1", "global-skill", "global")
    assert name == "global-skill"
    # It's now a real global skill…
    assert SkillsLoader(install_builtins=False).load_skill("global-skill") is not None
    # …and the draft is cleared.
    assert ephemeral.list_drafts("sess:1") == []


def test_promote_to_agent_tier(home):
    ephemeral.remember("sess:1", "Agent Skill", "agent steps")
    name = ephemeral.promote("sess:1", "agent-skill", "agent", agent="researcher")
    assert name == "agent-skill"
    # Written under the agent-local tier, visible to that agent only.
    assert (
        SkillsLoader(install_builtins=False, agent="researcher").load_skill("agent-skill")
        is not None
    )
    assert SkillsLoader(install_builtins=False).load_skill("agent-skill") is None


def test_promote_agent_scope_without_agent_refused(home):
    ephemeral.remember("sess:1", "X", "body")
    with pytest.raises(ephemeral.PromotionError):
        ephemeral.promote("sess:1", "x", "agent")


def test_promote_with_edits(home):
    ephemeral.remember("sess:1", "Original", "orig body")
    name = ephemeral.promote(
        "sess:1", "original", "global", title="Edited Name", body="edited body"
    )
    assert name == "edited-name"
    content = SkillsLoader(install_builtins=False).load_skill("edited-name")
    assert "edited body" in content


def test_discard_forgets_draft(home):
    ephemeral.remember("sess:1", "Doomed", "body")
    assert ephemeral.discard("sess:1", "doomed") is True
    assert ephemeral.list_drafts("sess:1") == []


def test_clear_session_drops_all(home):
    ephemeral.remember("sess:1", "One", "b1")
    ephemeral.remember("sess:1", "Two", "b2")
    assert ephemeral.clear_session("sess:1") == 2
    assert ephemeral.list_drafts("sess:1") == []


def test_promoted_skill_marked_taught(home):
    ephemeral.remember("sess:1", "Marked", "body")
    ephemeral.promote("sess:1", "marked", "global")
    content = SkillsLoader(install_builtins=False).load_skill("marked")
    assert "source: taught" in content
