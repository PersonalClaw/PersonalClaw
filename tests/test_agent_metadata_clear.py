"""Routing notes are clearable: an empty PUT clears the note (#668).

The editor's natural clear gesture — select-all, delete, Save — PUT an empty
``content`` and got 400 ``content required``. The empty state is supported
everywhere else (``load()`` returns ``""`` for a missing file; agents without a
note are normal), so only this write path forbade producing it: a note, once
set, could never be removed from the UI. The FE half (the swallowed catch) was
fixed separately; these rails pin the backend contract: an empty PUT clears, a
non-empty PUT still saves, and clearing an agent with no note is an idempotent
200.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from personalclaw import agent_metadata
from personalclaw.dashboard.handlers.agents import api_agent_metadata_put


@pytest.fixture(autouse=True)
def home(tmp_path):
    with patch.object(agent_metadata, "metadata_dir", return_value=tmp_path):
        yield tmp_path


@pytest.fixture(autouse=True)
def _quiet_side_effects():
    # Orchestrator regen reads the real AppConfig; SEL logs to the real ledger.
    # Neither is under test — the contract here is the PUT's store effect + status.
    with (
        patch("personalclaw.dashboard.handlers.agents._regen_orchestrator"),
        patch("personalclaw.dashboard.handlers.agents._sel", return_value=MagicMock()),
    ):
        yield


def _req(name: str, body: dict) -> MagicMock:
    r = MagicMock()
    r.match_info = {"name": name}
    r.get = lambda key, default=None: "tester" if key == "user" else default

    async def _json():
        return body

    r.json = _json
    return r


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


def test_an_empty_put_clears_an_existing_note(home):
    agent_metadata.save("router-a", "use for deep reviews")
    resp = _run(api_agent_metadata_put(_req("router-a", {"content": ""})))
    assert resp.status == 200
    assert _body(resp)["ok"] is True
    # Canonical empty is "absent": the file is gone and load() reads "".
    assert agent_metadata.load("router-a") == ""
    assert not (home / "router-a.md").exists()


def test_a_whitespace_only_put_also_clears(home):
    agent_metadata.save("router-b", "note")
    resp = _run(api_agent_metadata_put(_req("router-b", {"content": "   \n  "})))
    assert resp.status == 200
    assert agent_metadata.load("router-b") == ""


def test_a_non_empty_put_still_saves(home):
    resp = _run(api_agent_metadata_put(_req("router-c", {"content": "prefers refactors"})))
    assert resp.status == 200
    assert agent_metadata.load("router-c") == "prefers refactors"


def test_clearing_an_agent_with_no_note_is_an_idempotent_200(home):
    resp = _run(api_agent_metadata_put(_req("router-d", {"content": ""})))
    assert resp.status == 200
    assert agent_metadata.load("router-d") == ""
