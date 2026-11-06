"""An MCP server's tool list is read to its end, page by page.

The client asked a server for its tool list once and kept that first page: it never followed
`nextCursor`, so on a server that pages its list every tool after the first page was missing from
the Tools page's card, from agents and from the trust review, and nothing said so. It now follows
the cursor until the list ends. It stops early at a bound (`mcp_client.MAX_TOOL_PAGES` pages),
at a cursor it has seen before, or at a later page that fails, keeps what it read, says so in the
log, and leaves a note the card shows ("… the first N are shown"). A later page that fails keeps
the pages read, as when only the first page was read. A failing first page, a closed connection
and the start's deadline still fail the start, and a stream that has ended reads as a closed
connection on the card.

Driven through `McpServerConn._refresh_tools`, the one place PersonalClaw lists a server's tools,
with a session that pages as `ClientSession.list_tools` does in the oldest `mcp` this package
allows (1.10): it takes only `cursor=`. `params=` came in 1.18.
"""

from __future__ import annotations

import asyncio
import logging
import types
from typing import Any

import pytest

from personalclaw import mcp_client, mcp_discovery


def _tool(name: str) -> Any:
    return types.SimpleNamespace(
        name=name, description=f"{name} things", inputSchema=None, annotations=None
    )


class _PagedSession:
    """A session whose `tools/list` answers *pages*: cursor → (tool names, next cursor). Its
    `list_tools` is mcp 1.10's, which takes no `params`."""

    def __init__(self, pages: dict[str | None, tuple[list[str], str | None]]) -> None:
        self.pages = pages
        self.asked: list[str | None] = []

    async def list_tools(self, cursor: str | None = None) -> Any:
        self.asked.append(cursor)
        names, following = self.pages[cursor]
        return types.SimpleNamespace(tools=[_tool(n) for n in names], nextCursor=following)


class _Endless(_PagedSession):
    """A server whose every page names a new cursor."""

    def __init__(self) -> None:
        super().__init__({})

    async def list_tools(self, cursor: str | None = None) -> Any:
        self.asked.append(cursor)
        n = len(self.asked)
        return types.SimpleNamespace(tools=[_tool(f"t{n}")], nextCursor=f"page-{n}")


class _FailsLater(_PagedSession):
    """A server whose page at cursor *at* answers with *error*, as `ClientSession` raises it."""

    def __init__(self, pages: dict, at: str | None, error: BaseException) -> None:
        super().__init__(pages)
        self.at, self.error = at, error

    async def list_tools(self, cursor: str | None = None) -> Any:
        if cursor == self.at:
            self.asked.append(cursor)
            raise self.error
        return await super().list_tools(cursor)


def _mcp_error(code: int, message: str) -> BaseException:
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    return McpError(ErrorData(code=code, message=message))


def _listed(session: Any) -> mcp_client.McpServerConn:
    conn = mcp_client.McpServerConn("pager", {"command": "pager"})
    asyncio.run(conn._refresh_tools(session))
    return conn


def test_every_page_of_the_list_is_read():
    session = _PagedSession({None: (["a", "b"], "c1"), "c1": (["c", "d"], None)})

    conn = _listed(session)

    assert [t.name for t in conn._tools] == ["a", "b", "c", "d"]
    assert session.asked == [None, "c1"]
    assert conn._tools_note == ""


def test_a_list_that_never_ends_stops_at_the_bound_and_says_so(caplog):
    session = _Endless()

    with caplog.at_level(logging.WARNING, logger="personalclaw.mcp_client"):
        conn = _listed(session)

    assert len(session.asked) == mcp_client.MAX_TOOL_PAGES
    assert len(conn._tools) == mcp_client.MAX_TOOL_PAGES, "what it read is kept"
    assert "pager" in conn._tools_note
    assert f"the first {mcp_client.MAX_TOOL_PAGES} are shown" in conn._tools_note, conn._tools_note
    assert any("pager" in r.getMessage() for r in caplog.records), "said in the log"


def test_a_cursor_seen_before_stops_at_once():
    session = _PagedSession({None: (["a"], "c1"), "c1": (["b"], "c1")})

    conn = _listed(session)

    assert session.asked == [None, "c1"], "asked for the same page again"
    assert [t.name for t in conn._tools] == ["a", "b"]
    assert "the first 2 are shown" in conn._tools_note, conn._tools_note


def test_a_server_that_ignores_the_cursor_lists_its_page_once():
    """It answers every cursor with its first page: those tools are listed once, not twice."""
    session = _PagedSession({None: (["a", "b"], "c1"), "c1": (["a", "b"], "c1")})

    conn = _listed(session)

    assert [t.name for t in conn._tools] == ["a", "b"]
    assert "the first 2 are shown" in conn._tools_note, conn._tools_note


def test_a_later_page_that_fails_keeps_the_pages_read(caplog):
    """A replica that never saw the cursor answers "invalid cursor": the first page is kept, as when
    only the first page was read, with the note, rather than the start failing with none of its
    tools. The log names the server's error."""
    session = _FailsLater(
        {None: (["a", "b"], "c1"), "c1": (["c"], None)}, "c1", _mcp_error(-32602, "invalid cursor")
    )

    with caplog.at_level(logging.WARNING, logger="personalclaw.mcp_client"):
        conn = _listed(session)

    assert [t.name for t in conn._tools] == ["a", "b"]
    assert "failed to send the rest of its tool list" in conn._tools_note, conn._tools_note
    assert "the first 2 are shown" in conn._tools_note, conn._tools_note
    said = [r.getMessage() for r in caplog.records]
    assert any("pager" in m and "invalid cursor" in m for m in said), "in the log, with its error"


def test_a_later_page_error_naming_the_servers_url_logs_no_part_of_it(caplog):
    """A remote server's URL can carry a credential. The log names the error, and the URL as
    "<its URL>": neither the URL nor its masked form is written."""
    url = "https://mcp.example.test/mcp?token=s3cr3t-value"
    session = _FailsLater(
        {None: (["a"], "c1")}, "c1", _mcp_error(-32602, f"invalid cursor for {url}")
    )
    conn = mcp_client.McpServerConn("pager", {"type": "http", "url": url})

    with caplog.at_level(logging.WARNING, logger="personalclaw.mcp_client"):
        asyncio.run(conn._refresh_tools(session))

    (said,) = [r.getMessage() for r in caplog.records if "pager" in r.getMessage()]
    assert "invalid cursor for <its URL>" in said, said
    assert "s3cr3t" not in said and "mcp.example.test" not in said, said


@pytest.mark.parametrize("closed", ["ClosedResourceError", "BrokenResourceError"])
def test_a_later_page_on_an_ended_transport_still_fails_the_start(closed):
    """A broken stdio pipe or a closed HTTP stream ends the connection as CONNECTION_CLOSED does:
    keeping page one would read ok, and the next call would come back "not sent"."""
    import anyio

    session = _FailsLater({None: (["a", "b"], "c1")}, "c1", getattr(anyio, closed)())

    with pytest.raises(getattr(anyio, closed)):
        _listed(session)


@pytest.mark.parametrize("closed", ["ClosedResourceError", "BrokenResourceError"])
@pytest.mark.parametrize("remote", [False, True], ids=["stdio", "http"])
def test_an_ended_stream_reads_as_a_closed_connection_on_the_card(closed, remote):
    """The stream's own error has no message, so the card said only its class name."""
    import anyio

    url = "https://mcp.example.test/mcp"
    spec = {"type": "http", "url": url} if remote else {"command": "pager"}
    conn = mcp_client.McpServerConn("pager", spec)
    if not remote:
        conn._stdio = types.SimpleNamespace(
            waiting=False, left_to_finish=False, exited=False, returncode=None, stderr=""
        )
    failure = conn._start_failure(getattr(anyio, closed)(), timed_out=False)

    assert "closed its connection" in failure.headline, failure.headline
    assert closed not in failure.headline, failure.headline


def test_a_later_page_on_a_closed_connection_still_fails_the_start():
    from mcp.types import CONNECTION_CLOSED

    session = _FailsLater(
        {None: (["a", "b"], "c1")}, "c1", _mcp_error(CONNECTION_CLOSED, "Connection closed")
    )

    with pytest.raises(Exception, match="Connection closed"):
        _listed(session)


def test_the_first_page_failing_still_fails_the_start():
    session = _FailsLater({}, None, _mcp_error(-32603, "internal error"))

    with pytest.raises(Exception, match="internal error"):
        _listed(session)


def _start_with(note: str = "") -> Any:
    async def fake_start(name, spec, *, deadline, seal):
        tools = [mcp_client.McpToolSpec(name=n, description="", input_schema={}) for n in "ab"]
        return mcp_client.StartResult(
            tools=tools, error="", detail="", sign_in_needed=False, timed_out=False, note=note
        )

    return fake_start


def _pager() -> mcp_discovery.McpServerInfo:
    from mcp_owner_allowed import allow

    server = mcp_discovery.McpServerInfo(name="pager", command="/nonexistent/pc-fixture-mcp")
    allow(server)
    return server


@pytest.mark.asyncio
async def test_the_note_survives_the_tools_pages_probe(monkeypatch):
    """The Tools page's probe (`probe_server`) keeps its own record after the connection's: the
    note has to be in it, or the card never says the list was cut short."""
    note = "pager sent the same page of its tool list again, so PersonalClaw stopped reading it"
    monkeypatch.setattr(mcp_discovery, "_probe_cache", {})
    monkeypatch.setattr("personalclaw.mcp_client.try_start", _start_with(note))

    probed = await mcp_discovery.probe_server(_pager())
    assert probed.status == "ok" and probed.note == note

    *_, kept = mcp_discovery._get_cached(_pager())
    assert kept == note, "the card reads the probe's record"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "server",
    [
        {"command": "/nonexistent/pc-fixture-mcp"},
        {"transport": "http", "url": "https://mcp.example.test/mcp"},
    ],
    ids=["stdio", "http"],
)
async def test_the_probe_keeps_the_note_its_real_start_left(monkeypatch, server):
    """Through the real `try_start`, for a spawned server and a remote one (`_probe_remote` keeps
    a record of its own): the connection reads a list that sends the same page again, and the
    probe's record holds the note the card shows."""
    from mcp_owner_allowed import allow

    async def paged_list_tools(self):
        await self._refresh_tools(_PagedSession({None: (["a"], "c1"), "c1": (["b"], "c1")}))
        return list(self._tools)

    monkeypatch.setattr(mcp_discovery, "_probe_cache", {})
    monkeypatch.setattr(mcp_client.McpServerConn, "list_tools", paged_list_tools)
    info = mcp_discovery.McpServerInfo(name="pager", **server)
    allow(info)

    probed = await mcp_discovery.probe_server(info)

    assert probed.status == "ok", (probed.status, probed.error)
    assert [t["name"] if isinstance(t, dict) else t.name for t in probed.tools] == ["a", "b"]
    assert "the first 2 are shown" in probed.note, probed.note
    *_, kept = mcp_discovery._get_cached(info)
    assert kept == probed.note, "the card reads the probe's record"


@pytest.mark.asyncio
async def test_a_probe_that_fails_drops_an_old_starts_note(monkeypatch):
    """The server came from `list_servers`, carrying what the last start said. A new probe's
    answer replaces its note with its tools, error and detail: a start that failed lists no
    tools, so "the first N are shown" would be untrue beside its error."""

    async def no_answer(name, spec, *, deadline, seal):
        return mcp_client.StartResult(
            tools=[],
            error="pager did not answer",
            detail="",
            sign_in_needed=False,
            timed_out=True,
        )

    monkeypatch.setattr(mcp_discovery, "_probe_cache", {})
    monkeypatch.setattr("personalclaw.mcp_client.try_start", no_answer)
    server = _pager()
    server.note = "pager sent the same page again, so PersonalClaw stopped: the first 2 are shown."

    probed = await mcp_discovery.probe_server(server)
    assert probed.status == "error" and probed.note == ""


def test_the_note_reaches_the_card_and_the_trust_review_sees_every_tool(monkeypatch):
    observed: list[list[str]] = []
    monkeypatch.setattr(mcp_discovery, "_probe_cache", {})
    monkeypatch.setattr(mcp_discovery, "_current_seal", lambda name: "seal")
    monkeypatch.setattr(mcp_discovery, "_probed_as", lambda server: "seal")
    monkeypatch.setattr(
        "personalclaw.mcp_read_only_trust.observe",
        lambda name, tools: observed.append([getattr(t, "name", "") for t in tools]),
    )
    tools = [mcp_client.McpToolSpec(name=n, description="", input_schema={}) for n in "abcd"]

    mcp_discovery.note_start("pager", "seal", tools=tools, note="pager listed more tools than …")

    server = mcp_discovery.McpServerInfo(name="pager", transport="stdio", command="pager")
    server.status, server.tools, server.error, server.detail, server.note = (
        mcp_discovery._get_cached(server)
    )
    assert server.status == "ok"
    assert server.to_dict()["note"] == "pager listed more tools than …"
    assert observed == [["a", "b", "c", "d"]]


def test_a_full_list_leaves_no_note_on_the_card(monkeypatch):
    monkeypatch.setattr(mcp_discovery, "_probe_cache", {})
    monkeypatch.setattr(mcp_discovery, "_current_seal", lambda name: "seal")
    monkeypatch.setattr(mcp_discovery, "_probed_as", lambda server: "seal")
    monkeypatch.setattr("personalclaw.mcp_read_only_trust.observe", lambda name, tools: None)

    mcp_discovery.note_start("pager", "seal", tools=[])

    server = mcp_discovery.McpServerInfo(name="pager", transport="stdio", command="pager")
    *_, note = mcp_discovery._get_cached(server)
    assert note == ""
    assert "note" not in server.to_dict()


@pytest.mark.parametrize("pages", [1, 3])
def test_a_list_within_the_bound_is_read_whole(pages):
    chain = {
        None if i == 0 else f"c{i}": ([f"t{i}"], f"c{i + 1}" if i + 1 < pages else None)
        for i in range(pages)
    }

    conn = _listed(_PagedSession(chain))

    assert len(conn._tools) == pages and conn._tools_note == ""
