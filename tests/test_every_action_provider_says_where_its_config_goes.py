"""Every action provider says where its config goes: to a model, to a run it starts, or into what
it does itself.

A ``{{secret:NAME}}`` in a step's or an automation's config is filled with the value only for a
provider that uses its config itself. One whose config a model reads keeps the reference
(``ActionProvider.hands_config_to_a_model``), and one that starts a run hands it the reference
(``hands_config_to_a_run``). A provider that declares neither is filled, so each one is either
declared or named here with what it does with its config, and a new one is a choice someone makes.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.security

#: The providers whose config a model reads.
TO_A_MODEL = frozenset(
    {
        "best-of-n",
        "browse",
        "invoke-agent",
        "knowledge-retrieve",
        "optimize-score",
        "run-prompt",
        "second-opinion",
        "triage-digest",
    }
)

#: The providers that start a run with their config.
TO_A_RUN = frozenset({"run-workflow"})

#: Every other provider, with what it does with its config.
USES_ITS_CONFIG: dict[str, str] = {
    "artifact-update": "writes it into an artifact",
    "artifact_inspect": "names the offloaded step output it reads",
    "bash": "runs it as a shell command",
    "call-app-route": (
        "sends it, filled, to an app's route; a route that hands it to a model must not be given "
        "a reference"
    ),
    "check-work": "derives checks from the text and the folder it names, and runs them",
    "create-task": "writes it into a task",
    "heartbeat-tasks": "runs the tasks HEARTBEAT.md holds",
    "identity-report": "composes the identity report from the store",
    "inbox-op": "writes it into an inbox item",
    "knowledge-consolidate": "plans a consolidation of the knowledge store",
    "knowledge-gaps": "reads the knowledge store",
    "knowledge-health": "measures the knowledge store",
    "knowledge-persist": "writes it into the knowledge store",
    "knowledge-propose": "routes knowledge drafts to the proposal queue",
    "knowledge-relate": "writes relations into the knowledge store",
    "knowledge-report": "names a stored report, whose model is sent what that report reads",
    "net-fetch": "fetches the URL it names",
    "notification-digest": "collapses the queued notifications into one digest",
    "notify": "sends it as a notification",
    "optimize-file": "files a search's winner as a proposal",
    "render-report": "renders a report spec",
    "run-script": "runs it as a script",
    "self-remediation": "runs one remediation pass",
    "selfqa-commit-watch": "hands off to a fixed action name",
    "selfqa-evidence": "names the scenario evidence it checks",
    "selfqa-file-finding": "writes it into a finding",
    "selfqa-triage": "names the commits and the repository it triages",
    "send-message": "sends it as a message",
    "source-digest": "digests the watched sources' items",
    "usage-recap": "renders the month's spend",
}


def _providers() -> dict[str, object]:
    from personalclaw.action_providers import registry

    registry._ensure_default_providers_registered()
    return {name: registry.get_action_provider(name) for name in registry.list_action_providers()}


def test_every_action_provider_says_where_its_config_goes():
    providers = _providers()
    to_a_model = {n for n, p in providers.items() if p.hands_config_to_a_model}
    to_a_run = {n for n, p in providers.items() if p.hands_config_to_a_run}

    assert to_a_model == TO_A_MODEL
    assert to_a_run == TO_A_RUN
    assert set(providers) - to_a_model - to_a_run == set(USES_ITS_CONFIG)
    assert all(reason.strip() for reason in USES_ITS_CONFIG.values())


@pytest.mark.parametrize("name", ["browse", "knowledge-retrieve", "triage-digest"])
def test_a_provider_whose_config_a_model_reads_says_so(name):
    """🔴 Red before: each declared that it uses its config itself, so it was filled."""
    assert _providers()[name].hands_config_to_a_model is True
