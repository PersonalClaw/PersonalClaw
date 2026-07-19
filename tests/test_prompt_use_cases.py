"""Tests for prompt use-case bindings — which system prompt serves each context.

The default-agent system prompt resolves from the prompt provider via a per-use-case
binding (chat / background / code / goal_loop), falling back to the bundled
``system-default`` prompt (seeded from the shipped prompt) when unbound.
"""

import pytest

from personalclaw.providers import prompt_use_cases as puc


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    # Reset the in-process prompt-provider registry between tests.
    import personalclaw.prompt_providers.registry as reg
    reg._providers.clear()
    yield
    reg._providers.clear()


def test_use_case_vocabulary():
    # The four default-AGENT contexts are always present. The full vocabulary is
    # larger now (every bundled prompt — agent system prompts AND internal task
    # prompts — is individually bindable), and is derived from the catalog.
    for agent_uc in ("chat", "background", "code", "goal_loop"):
        assert agent_uc in puc.PROMPT_USE_CASES
    from personalclaw.prompt_providers.catalog import BUNDLED_PROMPTS
    assert puc.PROMPT_USE_CASES == tuple(p.use_case for p in BUNDLED_PROMPTS)


def test_unbound_falls_back_to_its_bundled_prompt():
    # Each use-case defaults to its OWN tailored bundled prompt (catalog-driven),
    # not a shared one.
    from personalclaw.prompt_providers.catalog import BUNDLED_PROMPTS

    for entry in BUNDLED_PROMPTS:
        assert puc.active_prompt_ref(entry.use_case) == f"native:{entry.name}"


def test_default_prompt_seeded_and_resolves():
    # resolve goes through the provider; seeding happens on ensure-registered.
    content = puc.resolve_prompt_content("chat")
    assert content and "PersonalClaw" in content


def test_binding_overrides_resolution():
    from personalclaw.prompt_providers.base import PromptTemplate
    from personalclaw.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_prompt_provider,
    )

    _ensure_default_providers_registered()
    get_prompt_provider("native").create_prompt(
        PromptTemplate(name="custom-code", content="CUSTOM CODE SYSTEM PROMPT — long enough.")
    )
    puc.save_active_prompts({"code": "native:custom-code"})

    assert puc.active_prompt_ref("code") == "native:custom-code"
    assert puc.resolve_prompt_content("code").startswith("CUSTOM CODE")
    # other use-cases still resolve the default
    assert "PersonalClaw" in puc.resolve_prompt_content("chat")


def test_unknown_use_case_falls_back_to_chat_prompt():
    # Unknown use-cases fall back to the chat prompt (the ultimate default).
    assert puc.active_prompt_ref("bogus") == f"native:{puc.DEFAULT_PROMPT_NAME}"
    assert puc.DEFAULT_PROMPT_NAME == "system-chat"


def test_save_rejects_unknown_use_case_keys():
    puc.save_active_prompts({"chat": "native:system-chat", "bogus": "native:x"})
    saved = puc.load_active_prompts()
    assert "chat" in saved and "bogus" not in saved


def test_split_ref():
    assert puc.split_ref("native:system-default") == ("native", "system-default")
    assert puc.split_ref("unqualified") is None


class TestSessionKeyDerivation:
    """The hot-path derives the use-case from the session_key when not explicit."""

    @pytest.mark.parametrize(
        "session_key,expected",
        [
            ("dashboard:abc", "chat"),
            ("cli_chat", "chat"),
            ("_bg", "background"),
            ("cron:job1", "background"),
            ("subagent:x", "background"),
            ("code:proj1", "code"),
            ("loop:goal1", "goal_loop"),
            ("campaign-7", "goal_loop"),
        ],
    )
    def test_derivation(self, session_key, expected):
        from personalclaw.context import _prompt_use_case_for

        assert _prompt_use_case_for(session_key) == expected

    def test_explicit_non_default_wins(self):
        from personalclaw.context import _prompt_use_case_for

        assert _prompt_use_case_for("dashboard:x", "code") == "code"
