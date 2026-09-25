"""``GET /api/prompts/bindings`` — the payload Settings → Prompts renders from.

The endpoint's job is not only "which prompt serves each context" but "what IS each
context". It used to answer only the first, so the dashboard invented the second from
a four-entry table and printed the raw key for the other thirty-six rows. These lock
the seam: every row arrives named, described, and grouped.
"""

import json

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers.prompts import api_prompt_bindings
from personalclaw.providers import prompt_use_cases as puc


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    import personalclaw.prompt_providers.registry as reg

    reg._providers.clear()
    yield
    reg._providers.clear()


async def _payload():
    resp = await api_prompt_bindings(make_mocked_request("GET", "/api/prompts/bindings"))
    return json.loads(resp.body)


@pytest.mark.asyncio
async def test_every_binding_arrives_named_described_and_grouped():
    data = await _payload()
    assert len(data["bindings"]) >= 35, "vacuity floor — the vocabulary must resolve"
    for b in data["bindings"]:
        assert b["label"], f"{b['use_case']} unnamed"
        assert b["hint"], f"{b['use_case']} undescribed"
        assert b["category"] in puc.PROMPT_CATEGORY_ORDER
        # The measured defect: the row rendered `b.use_case` verbatim.
        if "_" in b["use_case"]:
            assert b["label"] != b["use_case"]


@pytest.mark.asyncio
async def test_categories_are_present_ordered_and_non_empty():
    data = await _payload()
    keys = [c["key"] for c in data["categories"]]
    # Declared order, not dict/insertion order — the panel renders them in sequence.
    assert keys == [k for k in puc.PROMPT_CATEGORY_ORDER if k in keys]
    assert keys, "at least one group"
    for c in data["categories"]:
        assert c["label"] and c["hint"]
        assert any(
            b["category"] == c["key"] for b in data["bindings"]
        ), f"{c['key']} is a heading over nothing"
    # And nothing may fall outside a sent group, or a binding becomes unreachable.
    assert {b["category"] for b in data["bindings"]} <= set(keys)


@pytest.mark.asyncio
async def test_an_app_owned_use_case_is_grouped_with_the_rest():
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
        data = await _payload()
        row = next(b for b in data["bindings"] if b["use_case"] == "widget_summarize")
        assert row["label"] == "Widget summarize"
        assert row["hint"] == "Summarize a widget payload for the dashboard."
        assert row["category"] == "internal"
        assert "internal" in [c["key"] for c in data["categories"]]
    finally:
        prompt_registry.unregister_app("native-widgets")


class _JsonRequest(dict):
    """The two things the save handler reads off a request: its JSON body and `user`."""

    def __init__(self, body: dict) -> None:
        super().__init__()
        self._body = body

    async def json(self) -> dict:
        return self._body


@pytest.mark.asyncio
async def test_rebinding_the_orchestrator_skill_rewrites_the_skill_file_that_loads(tmp_path):
    """The orchestrator skill is rendered into ``skills/orchestrator/SKILL.md`` and that
    FILE is what loads. Saving the binding (or editing the bound prompt) used to leave the
    file on the previous prompt until an unrelated agent edit regenerated it."""
    from personalclaw.config.loader import config_path
    from personalclaw.dashboard.handlers.prompts import api_prompt_bindings_save, api_prompt_delete
    from personalclaw.prompt_providers.base import PromptTemplate
    from personalclaw.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_prompt_provider,
    )
    from personalclaw.skills import SkillsLoader

    config_path().write_text(json.dumps({"agent": {"orchestrator_skill": True}}))
    _ensure_default_providers_registered()
    native = get_prompt_provider("native")
    native.create_prompt(
        PromptTemplate(
            name="my-orchestrator",
            content="---\nalways: true\n---\n# MY ROUTING RULES ORCH-9120\n{{roster}}",
        )
    )
    skill = SkillsLoader()._dir / "orchestrator" / "SKILL.md"

    resp = await api_prompt_bindings_save(
        _JsonRequest({"use_case": "orchestrator_skill", "ref": "native:my-orchestrator"})
    )
    assert resp.status == 200
    assert "ORCH-9120" in skill.read_text(), "the saved binding never reached the loaded file"

    # Deleting the bound prompt regenerates from the use case's own bundled prompt.
    from aiohttp.test_utils import make_mocked_request

    req = make_mocked_request(
        "DELETE", "/api/prompts/my-orchestrator", match_info={"name": "my-orchestrator"}
    )
    assert (await api_prompt_delete(req)).status == 200
    assert "ORCH-9120" not in skill.read_text()
