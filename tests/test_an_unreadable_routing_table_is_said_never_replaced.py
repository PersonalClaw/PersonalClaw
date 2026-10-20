"""A routing table that cannot be read is said on the Routing tab, and never written over.

``GET /api/models/routing-policy`` caught every failure and answered ``{"enabled": false,
"use_cases": []}``: "routing is off and nothing is recorded", about a table nobody had read. And
the table's readers read a ``routing_policy.json`` that does not parse as an empty table, so even
without an exception a hand-edited table with one typo showed as empty, and the next reorder SAVED
that empty table plus its one change over the file: every other recorded order was gone.

Routing still decides without an unreadable table (a decision fails open; ``load_policy``). The
tab and every write now read it strictly: the tab answers ``routing_policy_unreadable`` with the
file and why, and a reorder or an accepted proposal is refused before anything is written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers.model_telemetry import register_model_telemetry_routes
from personalclaw.routing import policy, proposals

UC = "reasoning"
QC = "summarize"
#: A hand-edited table with one typo: the closing braces are missing.
BROKEN = '{"use_cases": {'


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated home that every reader and writer here resolves for itself: both bindings of
    ``config_dir`` are patched, and the redirect is asserted."""
    import personalclaw.config as config_pkg
    import personalclaw.config.loader as config_loader

    monkeypatch.setattr(config_pkg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(config_loader, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    assert policy._default_home() == tmp_path, "the fixture did not redirect the policy's home"
    return tmp_path


def _table(home: Path) -> Path:
    return home / "routing_policy.json"


def _break(home: Path) -> bytes:
    _table(home).write_text(BROKEN, encoding="utf-8")
    return _table(home).read_bytes()


def _unreadable(home: Path) -> str:
    return (
        f"Couldn't read your routing table, {_table(home)}: it is not valid JSON "
        "(line 1, column 16)."
    )


async def _call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    app = web.Application()
    register_model_telemetry_routes(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.request(method, path, json=body)
        return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_the_tab_says_the_table_could_not_be_read(home):
    _break(home)
    status, body = await _call("GET", "/api/models/routing-policy")
    assert status == 500
    assert body == {
        "error": {
            "code": "routing_policy_unreadable",
            "message": _unreadable(home)
            + " Until it can be read, routing does not use the orders recorded in it.",
        }
    }


@pytest.mark.asyncio
async def test_a_read_that_fails_is_not_routing_off(home, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("the table reader broke")

    monkeypatch.setattr(policy, "table_for", boom)
    status, body = await _call("GET", "/api/models/routing-policy")
    assert status == 500
    assert body["error"]["code"] == "routing_policy_unreadable"
    assert body["error"]["message"] == "Couldn't read the routing table: the table reader broke"


@pytest.mark.asyncio
async def test_a_table_that_is_not_there_is_still_shown_as_empty(home):
    """The control: no file is no recorded order, which the tab shows as a table."""
    status, body = await _call("GET", "/api/models/routing-policy")
    assert status == 200
    assert [row["use_case"] for row in body["use_cases"]] == [
        "reasoning",
        "background",
        "loops",
        "orchestration",
    ]


def test_routing_still_decides_without_an_unreadable_table(home):
    """A decision fails open: the unreadable table is read as holding no recorded order."""
    _break(home)
    assert policy.load_policy(home)["use_cases"] == {}
    assert policy.table_order(UC, QC, home=home) == []


def test_a_write_never_replaces_a_table_it_could_not_read(home):
    before = _break(home)
    with pytest.raises(policy.PolicyUnreadable) as exc:
        policy.set_order(UC, QC, ["cloud:big", "local:small"], home=home)
    assert str(exc.value) == _unreadable(home)
    assert _table(home).read_bytes() == before, "the unreadable table was written over"


@pytest.mark.asyncio
async def test_a_reorder_is_refused_before_any_lever_applies(home):
    """The body also sets the mode: a refusal that let the mode through would be half a write."""
    before = _break(home)
    status, body = await _call(
        "PUT",
        "/api/models/routing-policy",
        {"use_case": UC, "mode": "heuristic", "query_class": QC, "order": ["cloud:big"]},
    )
    assert status == 409
    assert body["error"]["code"] == "routing_policy_unreadable"
    assert body["error"]["message"] == (
        _unreadable(home) + " Nothing was changed: saving this order would replace the whole "
        "table. Fix or remove the file, then try again."
    )
    assert _table(home).read_bytes() == before
    assert policy.mode_for(UC, home=home) == "off", "the mode was applied by a refused write"


@pytest.mark.asyncio
async def test_accepting_a_proposal_over_an_unreadable_table_changes_nothing(home):
    prop = proposals.propose(
        use_case=UC,
        query_class=QC,
        current=["cloud:big", "local:small"],
        proposed=["local:small", "cloud:big"],
        evidence={"n": {"cloud:big": 20, "local:small": 22}},
        home=home,
    )
    assert prop is not None, "the fixture queued nothing to accept"
    before = _break(home)
    status, body = await _call("POST", f"/api/models/routing-proposals/{prop.id}/accept")
    assert status == 409
    assert body["error"]["code"] == "routing_policy_unreadable"
    assert body["error"]["message"].startswith(_unreadable(home) + " The proposal was not applied")
    assert _table(home).read_bytes() == before
    assert [p.id for p in proposals.pending(home=home)] == [prop.id], "it is still waiting"


def test_a_missing_table_is_created_by_the_first_order(home):
    """The control: "not there" is still an empty table a write may create."""
    assert not _table(home).exists()
    policy.set_order(UC, QC, ["local:small"], home=home)
    stored = json.loads(_table(home).read_text(encoding="utf-8"))
    assert stored["use_cases"][UC]["classes"][QC]["order"] == ["local:small"]
