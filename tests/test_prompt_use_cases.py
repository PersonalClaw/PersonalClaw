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


# ── How a use case DESCRIBES itself ──────────────────────────────────────────
#
# Settings → Prompts renders one row per bindable use case. It used to carry its own
# four-entry label table against a catalog of forty, so thirty-six rows printed their
# raw key (`nl_to_cron`) with no description — including as the accessible name of
# the row's picker (`aria-label="Prompt for nl_to_cron"`). The vocabulary is OPEN (an
# app contributes bindable use cases), so a table in any single consumer is
# structurally unable to stay complete; the label/hint/category now come from here.


def test_every_core_use_case_has_a_human_label():
    # The vacuity floor first: this asserts a property of every member, so it is
    # worthless if the vocabulary ever resolves to a handful.
    assert len(puc.PROMPT_USE_CASES) >= 40
    for uc in puc.PROMPT_USE_CASES:
        label = puc.use_case_label(uc)
        assert label, f"{uc} has no label"
        # The defect this replaced: the row showed the key itself. A key with a
        # separator can never legitimately equal its own label.
        if "_" in uc or "-" in uc:
            assert label != uc, f"{uc} still renders its raw key"
        assert not label.startswith(" ") and label.strip() == label


def test_every_core_use_case_has_a_hint():
    for uc in puc.PROMPT_USE_CASES:
        assert puc.use_case_hint(uc), f"{uc} has no description"


def test_the_four_agent_contexts_describe_the_CONTEXT_not_the_prompt():
    # Their catalog descriptions say "The bundled PersonalClaw system prompt for the
    # <x> context" — true of every row on the panel, and so useless as a row hint.
    # These four are overridden; the assertion is that the override actually wins.
    for uc in ("chat", "background", "code", "goal_loop"):
        assert "bundled PersonalClaw system prompt" not in puc.use_case_hint(uc)
    assert puc.use_case_hint("chat") == "Interactive sessions — dashboard, Slack, CLI"


def test_every_core_use_case_lands_in_a_declared_category():
    # `category` is the catalog's own field, and its docstring already called it "the
    # Settings-UI grouping" — it simply was never sent to the UI. A category with no
    # heading would render a group the panel cannot label.
    for uc in puc.PROMPT_USE_CASES:
        cat = puc.use_case_category(uc)
        assert cat in puc.PROMPT_CATEGORY_ORDER, f"{uc} → unknown category {cat!r}"
        assert puc.PROMPT_CATEGORY_LABEL[cat] and puc.PROMPT_CATEGORY_HINT[cat]
    # Every declared group must be non-empty, or the vocabulary and the headings have
    # drifted apart in the other direction.
    for cat in puc.PROMPT_CATEGORY_ORDER:
        assert any(puc.use_case_category(uc) == cat for uc in puc.PROMPT_USE_CASES), cat


def test_an_app_owned_use_case_is_named_and_described_like_a_bundled_one():
    # The live system has these: an installed knowledge app contributes four. Before
    # the registry carried a description they arrived as bare humanized keys.
    from personalclaw.apps import prompt_registry

    prompt_registry.register_use_case(
        "widget_summarize",
        provider="native",
        prompt_name="task-widget-summarize",
        category="internal",
        app="native-widgets",
        description="Summarize a widget payload for the dashboard.",
    )
    try:
        assert "widget_summarize" in puc.all_prompt_use_cases()
        assert puc.use_case_label("widget_summarize") == "Widget summarize"
        assert (
            puc.use_case_hint("widget_summarize") == "Summarize a widget payload for the dashboard."
        )
        assert puc.use_case_category("widget_summarize") == "internal"
    finally:
        prompt_registry.unregister_app("native-widgets")


def test_an_app_declaring_a_junk_category_still_gets_a_row():
    # A row dropped because its app typo'd a category is a binding the user cannot
    # reach at all — worse than a row in the wrong group.
    from personalclaw.apps import prompt_registry

    prompt_registry.register_use_case(
        "odd_one",
        provider="native",
        prompt_name="task-odd",
        category="nonsense",
        app="a",
        description="",
    )
    try:
        assert puc.use_case_category("odd_one") == "internal"
        assert puc.use_case_label("odd_one") == "Odd one"
    finally:
        prompt_registry.unregister_app("a")
