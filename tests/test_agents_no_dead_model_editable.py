"""The agents API does not publish a constant `model_editable` nobody reads.

Issue 570. Every agent record carried `model_editable`, hardcoded `True` at both emit sites, with
two writes and zero reads — not in the frontend's agent type at all. Beside it sat a comment
describing a REAL distinction ("reserved agents are locked EXCEPT their model"), which a field that
is always `True` cannot carry: it said the same thing for a reserved and a non-reserved agent,
while `editable` right next to it was computed from reservedness.

The behaviour was never wrong — the client narrows reserved agents to model-only off `reserved`
itself (`AgentDetail.tsx` → `isReservedAgent`). So this is a dead field, and the clean break is to
stop shipping it rather than to derive a second answer to a question `reserved` already answers.
The shape is the reason: a declared field with no reader, whose plausible meaning ("may this
agent's model be changed?") differs from its constant value, is what the next consumer trusts.
"""

from __future__ import annotations

import json as _json

import pytest
from aiohttp.test_utils import make_mocked_request

import personalclaw.config.loader as _loader
import personalclaw.dashboard.handlers as _handlers
import personalclaw.dashboard.handlers.agents as _agents_h

#: The field this rail exists to keep off the wire, plus the camelCase a client would coin for it.
DEAD_FIELDS = ("model_editable", "modelEditable")


def _acfg(monkeypatch, tmp_path, agents: dict):
    """A config.json carrying the given agents; both `config_path` seams point at it."""
    cfg = tmp_path / "config.json"
    cfg.write_text(_json.dumps({"agents": agents, "default_agent": "default"}))
    monkeypatch.setattr(_loader, "config_path", lambda: cfg)
    monkeypatch.setattr(_loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(_handlers, "config_path", lambda: cfg, raising=False)
    return cfg


@pytest.mark.asyncio
async def test_agent_list_omits_the_dead_field(monkeypatch, tmp_path):
    from personalclaw.agents.defaults import LOOP_WORKER_AGENT_NAME

    # A reserved agent AND a plain one, because the removed constant claimed to distinguish them.
    _acfg(monkeypatch, tmp_path, {LOOP_WORKER_AGENT_NAME: {}, "translation-reviewer": {}})
    resp = await _agents_h.api_personalclaw_agents(make_mocked_request("GET", "/api/agents"))
    body = _json.loads(resp.body.decode())

    agents = {a["name"]: a for a in body["agents"]}
    # A superset of what config.json named: the loader also seeds the reserved built-ins, which is
    # exactly the population the removed constant claimed to distinguish. Assert both ends are here
    # rather than pinning the seed list, then sweep every row.
    assert {LOOP_WORKER_AGENT_NAME, "translation-reviewer"} <= set(agents)
    assert any(a["reserved"] for a in agents.values()), "no reserved agent in the sample"
    assert any(not a["reserved"] for a in agents.values()), "no editable agent in the sample"
    for name, row in agents.items():
        for dead in DEAD_FIELDS:
            assert dead not in row, f"{name} still publishes {dead}"


@pytest.mark.asyncio
async def test_the_answer_that_replaces_it_is_still_on_the_wire(monkeypatch, tmp_path):
    """`reserved` is what the client narrows on, so removing the dead field must not touch it.

    Without this, "delete the unread field" could be satisfied by deleting the READ one too.
    """
    from personalclaw.agents.defaults import LOOP_WORKER_AGENT_NAME

    _acfg(monkeypatch, tmp_path, {LOOP_WORKER_AGENT_NAME: {}, "translation-reviewer": {}})
    resp = await _agents_h.api_personalclaw_agents(make_mocked_request("GET", "/api/agents"))
    agents = {a["name"]: a for a in _json.loads(resp.body.decode())["agents"]}

    assert agents[LOOP_WORKER_AGENT_NAME]["reserved"] is True
    assert agents[LOOP_WORKER_AGENT_NAME]["editable"] is False
    assert agents["translation-reviewer"]["reserved"] is False
    assert agents["translation-reviewer"]["editable"] is True


def test_no_emit_site_survives_anywhere_in_the_tree():
    """The census, not just the two handlers — the field had exactly two writers and no readers.

    A per-endpoint assertion cannot see a third emit site added later on another route (the #2983
    lesson: 8 of 9 doors fixed and the per-route rail still passed), so the denominator is the
    source tree.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for base in (root / "src" / "personalclaw", root / "web" / "src"):
        for path in base.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"} or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for dead in DEAD_FIELDS:
                if dead in text:
                    offenders.append(f"{path.relative_to(root)} ({dead})")
    assert offenders == [], f"the dead field came back: {offenders}"
