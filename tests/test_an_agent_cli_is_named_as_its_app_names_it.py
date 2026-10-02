"""An agent CLI has one name everywhere: the name its app has in the Store.

Measured: the chat's sentences named a CLI as its app does ("Kiro CLI"), while the runtime pickers
(a loop's "Runs on", a room's member picker) and the agents a runtime lists named it from its id in
title case ("Kiro Cli"). The runtime's row now carries that one name (``label``), and the agents its
Test lists are named with it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from personalclaw.agents import runtime_tests
from personalclaw.dashboard.handlers.providers import _runtime_row


@pytest.fixture
def apps(monkeypatch):
    import personalclaw.providers.registry as provider_registry

    known = {"kiro-cli-agent": SimpleNamespace(manifest=SimpleNamespace(displayName="Kiro CLI"))}
    monkeypatch.setattr(
        provider_registry, "get_provider_registry", lambda: SimpleNamespace(get=known.get)
    )
    return known


def _entry(name: str, **options):
    return SimpleNamespace(name=name, type="acp_agent", options=options)


def test_a_runtime_is_named_by_the_app_that_brought_it(apps):
    entry = _entry("acp:kiro-cli", extension="kiro-cli-agent", dialect="default")
    assert runtime_tests.runtime_label(entry) == "Kiro CLI"
    row = _runtime_row(entry, {"ready": True, "state": "ready", "detail": ""})
    assert row["label"] == "Kiro CLI"


def test_its_own_label_wins_and_only_an_unknown_one_is_named_by_its_id(apps):
    assert runtime_tests.runtime_label(_entry("acp:demo-cli", runtime_label="Demo")) == "Demo"
    assert runtime_tests.runtime_label(_entry("acp:demo-cli")) == "Demo Cli"


def test_the_agents_its_test_lists_carry_that_name(apps):
    """A Zed-dialect runtime offers one base agent, named for the runtime itself."""
    from personalclaw.llm.acp_agent import AcpAgentProvider

    entry = _entry("acp:kiro-cli", extension="kiro-cli-agent", dialect="claude-code")
    options = {
        **entry.options,
        "runtime_id": entry.name,
        "runtime_label": runtime_tests.runtime_label(entry),
    }
    (agent,) = AcpAgentProvider.agents_from_snapshot(options, {"configOptions": []})
    assert agent.name == "Kiro CLI"
