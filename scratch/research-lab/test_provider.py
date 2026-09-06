"""Contract + behaviour tests for the research-lab tool provider.

Contract: personalclaw.sdk.tool:ToolProvider

These run with no network, no credentials and no gateway: the contract tests assert the
SHAPE core depends on, so a change that breaks registration fails here first, and the
behaviour tests drive a whole campaign — open, several unattended cycles, synthesis —
against a temporary PERSONALCLAW_HOME.

``asyncio.run`` rather than ``pytest.mark.asyncio``: the app must be testable with a bare
``pytest`` and no plugin installed.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from provider import MAX_DEPTH, MAX_FOLLOW_UPS, ResearchLabProvider, create_provider

CONTRACT_METHODS = ("display_name", "invoke", "list_tools", "name")

TOOL_NAMES = {
    "research_open",
    "research_list",
    "research_next",
    "research_record",
    "research_report",
}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Never write to the real home: the provider persists under PERSONALCLAW_HOME."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def campaigns_dir(home):
    return home / "apps" / "research-lab" / "data" / "campaigns"


def call(provider, tool, **arguments):
    return asyncio.run(provider.invoke(tool, arguments))


def tools(provider):
    return asyncio.run(provider.list_tools())


# ── contract ────────────────────────────────────────────────────────────────────


def test_factory_returns_the_provider() -> None:
    assert isinstance(create_provider({}), ResearchLabProvider)


def test_factory_accepts_no_config() -> None:
    assert isinstance(create_provider(None), ResearchLabProvider)


def test_nothing_abstract_is_left() -> None:
    """An unimplemented abstract method makes the provider uninstantiable."""
    assert not getattr(ResearchLabProvider, "__abstractmethods__", frozenset())


def test_registers_under_the_app_name() -> None:
    """Every per-type registry keys a provider by `.name`."""
    assert create_provider({}).name == "research-lab"


def test_declares_its_display_name() -> None:
    assert create_provider({}).display_name == "Research Lab"


def test_every_contract_method_is_declared_on_the_stub() -> None:
    """Inherited-but-unimplemented is the drift this catches."""
    for name in CONTRACT_METHODS:
        assert name in vars(ResearchLabProvider), f"{name} is not implemented on the stub"


def test_settings_reach_the_provider() -> None:
    provider = create_provider({"default_cycle_budget": 2, "cycle_breadth": 1})
    assert (provider._budget, provider._breadth) == (2, 1)


# ── tool surface ────────────────────────────────────────────────────────────────


def test_the_declared_tools_are_the_implemented_tools() -> None:
    """A tool listed but never dispatched is a tool the model calls and never reaches."""
    provider = create_provider({})
    assert {t.name for t in tools(provider)} == TOOL_NAMES
    for name in TOOL_NAMES:
        assert call(provider, name, campaign="nope").error != f"unknown tool: {name}"


def test_every_tool_names_its_provider_and_takes_an_object() -> None:
    for definition in tools(create_provider({})):
        assert definition.provider == "research-lab"
        assert definition.parameters["type"] == "object"


def test_an_unknown_tool_fails_legibly() -> None:
    result = call(create_provider({}), "research_teleport")
    assert not result.success
    assert "unknown tool" in result.error
    assert result.recovery_hints


# ── the flagship: multiple unattended cycles, then a synthesised report ─────────


def test_a_campaign_runs_multiple_unattended_cycles_and_synthesises_a_report(home) -> None:
    provider = create_provider({"cycle_breadth": 1})
    opened = call(
        provider,
        "research_open",
        question="Does local-first sync beat a cloud broker?",
        sub_questions=["What do local-first users lose?", "What does a broker cost?"],
        cycle_budget=4,
    )
    assert opened.success, opened.error
    campaign = opened.metadata["campaign"]

    # Drive the loop exactly as the cron's prompt does: next → record → next → …
    cycles = 0
    while True:
        advance = call(provider, "research_next", campaign=campaign)
        assert advance.success, advance.error
        if advance.metadata["done"]:
            break
        cycles += 1
        assert cycles < 10, "the unattended loop did not terminate"
        for node in advance.metadata["worklist"]:
            recorded = call(
                provider,
                "research_record",
                campaign=campaign,
                node=node["id"],
                finding=f"Finding for {node['id']}.",
                sources=[f"https://example.test/{node['id']}"],
            )
            assert recorded.success, recorded.error

    assert cycles >= 2, f"expected several unattended cycles, ran {cycles}"

    report = call(provider, "research_report", campaign=campaign)
    assert report.success, report.error
    assert report.metadata["cycles_run"] == cycles
    assert report.metadata["answered"] == report.metadata["total"] == 2
    assert "# Does local-first sync beat a cloud broker?" in report.output
    assert "## Findings" in report.output
    assert "What does a broker cost?" in report.output
    assert "https://example.test/q1" in report.output

    persisted = campaigns_dir(home) / campaign / "report.md"
    assert persisted.read_text(encoding="utf-8") == report.output


def test_the_cycle_budget_stops_an_unanswered_campaign(home) -> None:
    """Budget exhaustion reports done SUCCESSFULLY — the loop stops on that, so an error
    here would read as a transient failure and keep the cron retrying forever."""
    provider = create_provider({"cycle_breadth": 1})
    campaign = call(
        provider,
        "research_open",
        question="Unanswerable",
        sub_questions=["a", "b", "c"],
        cycle_budget=1,
    ).metadata["campaign"]

    assert call(provider, "research_next", campaign=campaign).metadata["done"] is False
    spent = call(provider, "research_next", campaign=campaign)
    assert spent.success
    assert spent.metadata["done"] is True
    assert spent.metadata["reason"] == "budget_spent"
    assert spent.metadata["open"] == 3

    report = call(provider, "research_report", campaign=campaign)
    assert "## Open questions" in report.output
    assert "No sub-question has been answered yet." in report.output


def test_a_finding_grafts_its_follow_ups_onto_the_tree(home) -> None:
    provider = create_provider({})
    campaign = call(provider, "research_open", question="Root", sub_questions=["first"]).metadata[
        "campaign"
    ]
    call(provider, "research_next", campaign=campaign)
    recorded = call(
        provider,
        "research_record",
        campaign=campaign,
        node="q1",
        finding="answered",
        follow_ups=["second", "second", "third"],
    )
    assert [f["question"] for f in recorded.metadata["follow_ups"]] == ["second", "third"]
    assert recorded.metadata["open"] == 2


def test_the_depth_cap_stops_the_tree_growing_forever(home) -> None:
    """A cycle that answers one question with another must eventually run out of tree, or
    an unattended campaign never ends."""
    provider = create_provider({"cycle_breadth": 1})
    campaign = call(
        provider, "research_open", question="Root", sub_questions=["level one"], cycle_budget=20
    ).metadata["campaign"]
    grafts = 0
    for step in range(MAX_DEPTH + 3):
        advance = call(provider, "research_next", campaign=campaign, breadth=1)
        if advance.metadata["done"]:
            break
        node = advance.metadata["worklist"][0]["id"]
        if call(
            provider,
            "research_record",
            campaign=campaign,
            node=node,
            finding="raises another",
            follow_ups=[f"deeper {step}"],
        ).metadata["follow_ups"]:
            grafts += 1
    assert grafts == MAX_DEPTH - 1, grafts
    assert call(provider, "research_next", campaign=campaign).metadata["done"] is True


def test_only_a_bounded_number_of_follow_ups_is_grafted(home) -> None:
    provider = create_provider({})
    campaign = call(provider, "research_open", question="Root", sub_questions=["first"]).metadata[
        "campaign"
    ]
    call(provider, "research_next", campaign=campaign)
    grafted = call(
        provider,
        "research_record",
        campaign=campaign,
        node="q1",
        finding="answered",
        follow_ups=[f"q-{i}" for i in range(MAX_FOLLOW_UPS + 5)],
    ).metadata["follow_ups"]
    assert len(grafted) == MAX_FOLLOW_UPS


# ── refusals ────────────────────────────────────────────────────────────────────


def test_an_omitted_campaign_resolves_the_single_open_one(home) -> None:
    """The cron's turn carries no id, so this is the unattended path."""
    provider = create_provider({})
    campaign = call(provider, "research_open", question="Only one", sub_questions=["a"]).metadata[
        "campaign"
    ]
    assert call(provider, "research_next").metadata["campaign"] == campaign


def test_an_omitted_campaign_refuses_rather_than_guesses(home) -> None:
    provider = create_provider({})
    call(provider, "research_open", question="First one", sub_questions=["a"])
    call(provider, "research_open", question="Second one", sub_questions=["a"])
    result = call(provider, "research_next")
    assert not result.success
    assert "2 campaigns are open" in result.error


def test_an_omitted_campaign_with_none_open_says_so(home) -> None:
    result = call(create_provider({}), "research_next")
    assert not result.success
    assert "no open campaign" in result.error


@pytest.mark.parametrize("bad", ["../escape", "/etc/passwd", "Upper", "with space"])
def test_a_campaign_id_that_is_not_an_id_is_refused(home, bad: str) -> None:
    """The id becomes a directory name, and tool arguments come from a model."""
    result = call(create_provider({}), "research_report", campaign=bad)
    assert not result.success
    assert "not a campaign id" in result.error


def test_an_unknown_campaign_is_refused(home) -> None:
    result = call(create_provider({}), "research_report", campaign="nope")
    assert not result.success
    assert "no such campaign" in result.error


def test_a_question_is_required(home) -> None:
    result = call(create_provider({}), "research_open", question="   ")
    assert not result.success
    assert "question is required" in result.error


def test_a_finding_is_required(home) -> None:
    provider = create_provider({})
    campaign = call(provider, "research_open", question="Root", sub_questions=["a"]).metadata[
        "campaign"
    ]
    result = call(provider, "research_record", campaign=campaign, node="q1", finding="")
    assert not result.success
    assert "finding is required" in result.error


def test_a_finding_for_an_unknown_sub_question_is_refused(home) -> None:
    provider = create_provider({})
    campaign = call(provider, "research_open", question="Root", sub_questions=["a"]).metadata[
        "campaign"
    ]
    result = call(provider, "research_record", campaign=campaign, node="q99", finding="x")
    assert not result.success
    assert "no sub-question" in result.error
    assert result.recovery_hints


def test_two_campaigns_with_the_same_question_get_distinct_ids(home) -> None:
    provider = create_provider({})
    first = call(provider, "research_open", question="Same question").metadata["campaign"]
    second = call(provider, "research_open", question="Same question").metadata["campaign"]
    assert first != second


def test_an_unreadable_campaign_is_skipped_not_fatal(home) -> None:
    provider = create_provider({})
    call(provider, "research_open", question="Readable", sub_questions=["a"])
    broken = campaigns_dir(home) / "broken"
    broken.mkdir(parents=True)
    (broken / "campaign.json").write_text("{not json", encoding="utf-8")
    listed = call(provider, "research_list")
    assert listed.success
    assert [c["campaign"] for c in listed.metadata["campaigns"]] == ["readable"]


def test_the_persisted_campaign_is_valid_json(home) -> None:
    provider = create_provider({})
    campaign = call(provider, "research_open", question="Root", sub_questions=["a"]).metadata[
        "campaign"
    ]
    path = campaigns_dir(home) / campaign / "campaign.json"
    assert json.loads(path.read_text(encoding="utf-8"))["question"] == "Root"


def test_no_campaigns_lists_cleanly(home) -> None:
    result = call(create_provider({}), "research_list")
    assert result.success
    assert result.metadata["campaigns"] == []
    assert result.recovery_hints
