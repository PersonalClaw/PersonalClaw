"""What each agent did in a window of time is read off its model calls.

Home's "Your agents today" band draws each agent's work as blocks on a time axis. The blocks come
from the usage ledger, which writes one row per model call with the session it was for, the agent
that made it, when it ended and how long it took. A call is ``[ts - duration, ts]``; calls in one
session less than :data:`agent_day.GAP_SECS` apart are one block, because the time between them
is the agent running the tools the model asked for. ``before`` is the latest block an agent ended
earlier today, for the band's "← 13:10" edge.

Driven through the real ledger file under an isolated home, and through the real route.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import agent_day, spend_day
from personalclaw import usage_ledger as ul

#: It reads the route table that keeps every app off the route (``apps/permissions``).
pytestmark = [pytest.mark.security]

UTC = timezone.utc
NOW = datetime(2026, 11, 4, 2, 20, tzinfo=UTC)  # 18:20 in Noor's zone (UTC-8)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(spend_day, "zone", lambda: timezone(timedelta(hours=-8)))
    return tmp_path


def call(
    end: datetime,
    *,
    secs: float = 20,
    session: str = "dashboard:lisbon",
    agent: str = "",
    source: str = "chat",
) -> None:
    ul.record_turn(
        ul.TurnUsage(
            ts=end.isoformat(),
            session_key=session,
            source=source,
            agent=agent,
            provider="Example",
            model="example-1",
            duration_ms=int(secs * 1000),
            cost_usd=0.01,
        )
    )


def at(hh: int, mm: int, ss: int = 0) -> datetime:
    """A local (UTC-8) clock time on the evening of 3 November, as UTC."""
    return datetime(2026, 11, 3, hh, mm, ss, tzinfo=timezone(timedelta(hours=-8))).astimezone(UTC)


def day(since: datetime, until: datetime, now: datetime = NOW) -> dict[str, dict]:
    agents, _cut = agent_day.read(since=since, until=until, now=now)
    return {a["agent"]: a for a in agents}


def test_calls_a_short_gap_apart_are_one_block_from_the_first_start_to_the_last_end():
    call(at(18, 0, 30), secs=30)
    call(at(18, 2, 0), secs=40)  # 50 s after the last ended: the agent ran tools in between
    call(at(18, 3, 10), secs=10)

    blocks = day(at(14, 20), at(22, 20))[""]["blocks"]

    assert blocks == [
        {
            "start": at(18, 0).isoformat(),
            "end": at(18, 3, 10).isoformat(),
            "session": "dashboard:lisbon",
            "source": "chat",
        }
    ]


def test_a_gap_of_two_minutes_or_more_starts_a_new_block():
    call(at(17, 0, 20), secs=20)
    call(at(17, 2, 40), secs=20)  # starts 2 min after the last ended

    blocks = day(at(14, 20), at(22, 20))[""]["blocks"]

    assert [(b["start"], b["end"]) for b in blocks] == [
        (at(17, 0).isoformat(), at(17, 0, 20).isoformat()),
        (at(17, 2, 20).isoformat(), at(17, 2, 40).isoformat()),
    ]


def test_each_agent_and_each_of_its_sessions_is_its_own():
    call(at(18, 1), session="dashboard:lisbon")
    call(at(18, 1, 30), session="dashboard:sam")
    call(at(18, 1, 10), session="loop-desks", agent="Researcher", source="loop")

    got = day(at(14, 20), at(22, 20))

    assert set(got) == {
        "",
        "Researcher",
    }, "the default agent is the empty name, as the ledger keeps it"
    assert [b["session"] for b in got[""]["blocks"]] == ["dashboard:lisbon", "dashboard:sam"]
    assert [(b["session"], b["source"]) for b in got["Researcher"]["blocks"]] == [
        ("loop-desks", "loop")
    ]


def test_work_before_the_window_is_its_edge_and_work_before_midnight_is_not():
    # Written in the order the calls ended, as the ledger records them.
    call(at(7, 30) - timedelta(days=1), agent="Release helper", source="subagent")  # yesterday
    call(at(7, 30), agent="Inbox triage", source="cron")
    call(at(13, 10))  # this afternoon, before a 14:20 window
    call(at(13, 50))

    got = day(at(14, 20), at(22, 20))

    assert got[""]["blocks"] == [] and got[""]["before"] == at(13, 50).isoformat()
    assert got["Inbox triage"]["before"] == at(7, 30).isoformat()
    assert "Release helper" not in got, "work from yesterday is no part of today"


def test_a_call_that_ended_inside_the_window_is_in_it_and_one_at_its_end_is_not():
    call(at(14, 20, 10), secs=30)  # began before the window, ended in it
    call(at(22, 20), secs=5, agent="Researcher")  # ends exactly at the window's end: outside

    got = day(at(14, 20), at(22, 20))

    assert [b["start"] for b in got[""]["blocks"]] == [at(14, 19, 40).isoformat()]
    assert got.get("Researcher", {"blocks": []})["blocks"] == []


def test_a_call_with_no_duration_is_a_moment():
    call(at(16, 12), secs=0, agent="Release helper", source="subagent")

    (block,) = day(at(14, 20), at(22, 20))["Release helper"]["blocks"]

    assert block["start"] == block["end"] == at(16, 12).isoformat()


def test_a_damaged_ledger_line_is_skipped_and_the_rest_still_read(home):
    call(at(18, 0))
    with open(home / "usage" / "turns.jsonl", "a", encoding="utf-8") as f:
        f.write("{not json\n")
    call(at(18, 5), agent="Researcher")

    assert set(day(at(14, 20), at(22, 20))) == {"", "Researcher"}


def test_the_ledger_is_read_from_its_end_and_stops_well_before_the_window(home):
    """Rows are written as their calls end, so the file is in time order: a read of the last hours
    stops at the first row more than the slack before them, and never parses the weeks before.
    A row written a little out of order, inside the slack, does not stop it."""
    call(at(17, 0), session="dashboard:written-first")  # in the window, but behind the old row
    call(at(14, 20) - timedelta(days=2), session="dashboard:old")
    call(at(14, 25), session="dashboard:a")
    call(at(14, 15), session="dashboard:out-of-order")  # ended before :a, recorded after it
    call(at(18, 0), session="dashboard:late")

    read = ul.calls(since=at(14, 20).isoformat())

    assert [r["session_key"] for r in read] == ["dashboard:a", "dashboard:late"]


def test_a_day_with_no_calls_has_no_agents():
    assert agent_day.read(since=at(14, 20), until=at(22, 20), now=NOW) == ([], 0)


# ── the route ──────────────────────────────────────────────────────────────────────────────


def _app() -> web.Application:
    """The route behind a stand-in for the auth middleware: a request it admitted is yours unless it
    carries an app's token (``X-Test-App``) or the internal secret an agent's tools present."""
    from personalclaw.dashboard.handlers.agent_day import register_agent_day_routes

    @web.middleware
    async def signed_in(request, handler):
        if request.headers.get("X-Test-App"):
            request["app"] = request.headers["X-Test-App"]
        elif "X-Internal-Secret" not in request.headers:
            request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[signed_in])
    register_agent_day_routes(app)
    return app


@pytest.mark.asyncio
async def test_the_route_answers_the_window_it_was_asked_for():
    call(at(18, 0))
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get(
            "/api/agents/day",
            params={"since": at(14, 20).isoformat(), "until": at(22, 20).isoformat()},
        )
        body = await resp.json()

    assert resp.status == 200, body
    assert body["since"] == at(14, 20).isoformat() and body["until"] == at(22, 20).isoformat()
    assert body["cut"] == 0
    (agent,) = body["agents"]
    assert set(agent) == {"agent", "blocks", "before"}
    assert set(agent["blocks"][0]) == {
        "start",
        "end",
        "session",
        "source",
    }, "times and names, never content"


@pytest.mark.asyncio
async def test_the_route_refuses_a_time_it_cannot_read_and_a_window_past_a_day():
    async with TestClient(TestServer(_app())) as client:
        bad = await client.get("/api/agents/day", params={"since": "yesterday"})
        bad_until = await client.get("/api/agents/day", params={"until": "tomorrow"})
        a_day = await client.get(
            "/api/agents/day",
            params={
                "since": at(14, 20).isoformat(),
                "until": (at(14, 20) + timedelta(hours=24)).isoformat(),
            },
        )
        wide = await client.get(
            "/api/agents/day",
            params={
                "since": at(14, 20).isoformat(),
                "until": (at(14, 20) + timedelta(hours=24, seconds=1)).isoformat(),
            },
        )
        why = (await wide.json())["error"]["message"]
        backwards = await client.get(
            "/api/agents/day",
            params={"since": at(22, 20).isoformat(), "until": at(14, 20).isoformat()},
        )
        code = (await bad.json())["error"]["code"]

    assert bad.status == 400 and code == "bad_request"
    assert bad_until.status == 400 and wide.status == 400 and backwards.status == 400
    assert a_day.status == 200
    assert "24 hours at most" in why


@pytest.mark.asyncio
async def test_a_read_answers_its_newest_blocks_up_to_its_cap_and_says_how_many_it_left_out():
    """A day of one agent working in many chats is still one answer: the oldest blocks are left out
    past ``limit``, and ``cut`` says how many. A ``limit`` past the cap is refused, saying why."""
    for n in range(7):
        call(at(15, 0) + timedelta(minutes=10 * n), session=f"dashboard:c{n}")
    window = {"since": at(14, 20).isoformat(), "until": at(22, 20).isoformat()}

    async with TestClient(TestServer(_app())) as client:
        three = await (await client.get("/api/agents/day", params={**window, "limit": "3"})).json()
        every = await (await client.get("/api/agents/day", params=window)).json()
        past = await client.get(
            "/api/agents/day", params={**window, "limit": str(agent_day.MAX_BLOCKS + 1)}
        )
        none = await client.get("/api/agents/day", params={**window, "limit": "0"})
        word = await client.get("/api/agents/day", params={**window, "limit": "all"})
        why = (await past.json())["error"]["message"]

    (agent,) = three["agents"]
    assert [b["session"] for b in agent["blocks"]] == [
        "dashboard:c4",
        "dashboard:c5",
        "dashboard:c6",
    ]
    assert three["cut"] == 4
    assert len(every["agents"][0]["blocks"]) == 7 and every["cut"] == 0
    assert past.status == none.status == word.status == 400
    assert f"from 1 to {agent_day.MAX_BLOCKS}" in why


def test_the_cap_keeps_the_newest_blocks_whichever_agent_made_them(home):
    call(at(15, 0), agent="Researcher", session="loop-a")
    call(at(16, 0), session="dashboard:b")
    call(at(17, 0), agent="Researcher", session="loop-c")

    agents, cut = agent_day.read(since=at(14, 20), until=at(22, 20), now=NOW, limit=2)

    by = {a["agent"]: [b["session"] for b in a["blocks"]] for a in agents}
    assert by == {"Researcher": ["loop-c"], "": ["dashboard:b"]} and cut == 1


@pytest.mark.asyncio
async def test_the_fold_runs_off_the_event_loop(monkeypatch):
    """The band asks again whenever work changes, and the fold reads the ledger file: it runs in a
    thread, so a long day's file never holds the gateway's loop."""
    import threading

    ran_on: list[int] = []
    real = agent_day.read

    def read(**kwargs):
        ran_on.append(threading.get_ident())
        return real(**kwargs)

    monkeypatch.setattr(agent_day, "read", read)
    call(at(18, 0))
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/agents/day")
        loop_thread = threading.get_ident()

    assert resp.status == 200
    assert ran_on and ran_on[0] != loop_thread


@pytest.mark.asyncio
async def test_no_app_reaches_it_whatever_it_declared_and_your_sign_in_does(monkeypatch):
    """The route table declares it yours (``ROUTE_AUTHZ``), so the gateway's own app middleware
    refuses an app that declared ``/api/agents`` before the handler runs, and admits your request.
    """
    from personalclaw.apps import permissions
    from personalclaw.apps.permissions import ROUTE_AUTHZ, OwnerOnly
    from personalclaw.dashboard.handlers.agent_day import api_agents_day
    from personalclaw.dashboard.server import app_permission_middleware

    assert isinstance(ROUTE_AUTHZ["GET /api/agents/day"], OwnerOnly)

    class Declared:
        """An installed, enabled app whose manifest declares every path."""

        def can_use_api(self, path):
            return True

    monkeypatch.setattr(permissions, "app_lifecycle_denial", lambda name: "")
    monkeypatch.setattr(permissions, "checker_for", lambda name: Declared())
    reached: list[str] = []

    @web.middleware
    async def caller(request, handler):
        if request.headers.get("X-Test-App"):
            request["app"] = request.headers["X-Test-App"]
        else:
            request["user"] = "owner"
        return await handler(request)

    async def handler(request):
        reached.append(request.headers.get("X-Test-App", "you"))
        return await api_agents_day(request)

    app = web.Application(middlewares=[caller, app_permission_middleware])
    app.router.add_get("/api/agents/day", handler)
    call(at(18, 0))
    async with TestClient(TestServer(app)) as client:
        an_app = await client.get("/api/agents/day", headers={"X-Test-App": "garden"})
        refused = await an_app.text()
        yours = await client.get("/api/agents/day")

    assert an_app.status == 403 and "owner-only capability" in refused and "blocks" not in refused
    assert yours.status == 200
    assert reached == ["you"]


@pytest.mark.asyncio
async def test_with_no_window_the_route_reads_the_last_four_hours(monkeypatch):
    monkeypatch.setattr(agent_day, "clock", lambda: NOW)
    call(at(15, 0))
    call(at(13, 0), agent="Code reviewer")

    async with TestClient(TestServer(_app())) as client:
        body = await (await client.get("/api/agents/day")).json()

    assert (
        body["since"] == (NOW - timedelta(hours=4)).isoformat() and body["until"] == NOW.isoformat()
    )
    by = {a["agent"]: a for a in body["agents"]}
    assert len(by[""]["blocks"]) == 1 and by["Code reviewer"]["blocks"] == []


def test_the_ledger_rows_are_its_one_source(home):
    """The fold reads the ledger file the usage pages read, and keeps nothing of its own."""
    call(at(18, 0))
    before = sorted(p.name for p in home.rglob("*"))
    agent_day.read(since=at(14, 20), until=at(22, 20), now=NOW)

    assert sorted(p.name for p in home.rglob("*")) == before
    assert json.loads((home / "usage" / "turns.jsonl").read_text().splitlines()[0])["session_key"]


@pytest.mark.asyncio
async def test_the_helpers_list_says_when_a_finished_helper_ended_and_not_for_a_running_one():
    """Home's agents band marks a helper that failed at the time it ended; ``/api/spawn`` said
    only when it started."""
    from unittest.mock import MagicMock

    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers.messaging import api_spawn_list
    from personalclaw.subagent import SubagentInfo

    ended = SubagentInfo(
        id="rel00001", task="cut the release", started=1000.0, done=True, error="boom"
    )
    ended.elapsed = 120.0
    running = SubagentInfo(id="run00001", task="read the changelog", started=1100.0)
    subagents = MagicMock()
    subagents.all_agents = [ended, running]
    app = web.Application()
    app["state"] = MagicMock(subagents=subagents, _pending_approvals={})
    request = make_mocked_request("GET", "/api/spawn", app=app)
    request["user"] = "owner"

    rows = {r["id"]: r for r in json.loads((await api_spawn_list(request)).body)["agents"]}

    assert rows["rel00001"]["ended"] == 1120.0
    assert "ended" not in rows["run00001"]


@pytest.mark.asyncio
async def test_every_agents_day_is_yours_to_read_and_no_app_or_chats_agent_reads_it():
    """It names every chat's sessions and when each worked: you read it, and your own pages' tools,
    but an app or a chat's agent reads only what its own work did, so it is refused this."""
    call(at(18, 0))
    async with TestClient(TestServer(_app())) as client:
        yours = await client.get("/api/agents/day")
        pages = await client.get(
            "/api/agents/day", headers={"X-Internal-Secret": "s", "X-Session-Key": "dashboard:ui"}
        )
        an_app = await client.get("/api/agents/day", headers={"X-Test-App": "garden"})
        a_chat = await client.get(
            "/api/agents/day",
            headers={"X-Internal-Secret": "s", "X-Session-Key": "dashboard:chat-1"},
        )
        refused = await an_app.json()

    assert yours.status == 200 and pages.status == 200
    assert an_app.status == 403 and a_chat.status == 403
    assert refused["error"]["code"] == "agents_day_owner_only"
    assert "agents" not in refused
