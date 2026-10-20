"""`GET /api/model-providers` listed the bundled floor model as a remote instance.

Found driving onboarding as a user: download the small model in step 3, open Settings →
Providers, and the model shows up twice — once under **Native (bundled)** with its download
card, where it belongs, and again under **Remote (multi-instance)** as a `bundled-chat`
"Configured" instance with Test, Edit and Delete. That second row is the only thing this
route put there, and none of its actions could work: the entry is registered in memory by the
app that ships it, so there is no `config.json` row behind it and both `PUT` and `DELETE
/api/model-providers/bundled-chat` answer 404 "not found".

The route's own docstring says it lists *configured* entries, and `ProviderEntry.floor` is
documented as never being one ("a row the user's config carries is a configured choice by
definition"). So the rule keys on that declaration — not on a name or a type, which core does
not know and must not learn.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.llm.capabilities import Capability
from personalclaw.llm.registry import ProviderEntry


class _Registry:
    """Only what the list route reads: the entries, a type with no static descriptor, and the
    catalog a listed instance's connection is measured with (none here: never measured)."""

    def __init__(self, entries):
        self._entries = entries

    def list_entries(self):
        return self._entries

    def capability_of(self, _type):
        raise LookupError("no capability descriptor")

    def build_catalog(self, _entry):
        return None


async def _listed(monkeypatch, tmp_path, entries) -> list[str]:
    from personalclaw.config import loader as config_loader
    from personalclaw.dashboard.handlers import providers as handler
    from personalclaw.llm import registry as llm_registry

    monkeypatch.setattr(config_loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(llm_registry, "get_default_registry", lambda: _Registry(entries))
    resp = await handler.api_providers_list(object())
    return [p["name"] for p in json.loads(resp.text)["providers"]]


def _entry(name: str, *, floor: bool) -> ProviderEntry:
    return ProviderEntry(
        name=name,
        type=name,
        model="m",
        declared_capabilities=frozenset({Capability.CHAT}),
        floor=floor,
    )


@pytest.mark.asyncio
async def test_a_floor_entry_is_not_listed_as_a_configured_instance(monkeypatch, tmp_path):
    entries = [_entry("my-openai", floor=False), _entry("bundled-chat", floor=True)]

    assert await _listed(monkeypatch, tmp_path, entries) == ["my-openai"]


@pytest.mark.asyncio
async def test_the_rule_is_the_declaration_not_the_name(monkeypatch, tmp_path):
    """The pair: a list that dropped `bundled-chat` by name would pass the test above and
    hide a user's own config row the day one shares the name. Undeclared, it is listed."""
    entries = [_entry("bundled-chat", floor=False)]

    assert await _listed(monkeypatch, tmp_path, entries) == ["bundled-chat"]
