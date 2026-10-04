"""An artifact an agent CLI writes reaches Knowledge and the open pages as the gateway's does.

An agent on an agent CLI saves, edits and deletes artifacts with the CLI's tool server
(``personalclaw mcp-core``), a process of its own, which writes the artifact store there. What
follows a write lives only in the gateway: Knowledge's copy of the artifact, which makes its text
searchable (``knowledge.artifact_ingest``), and the hint that tells every open page to read it
again (``DashboardState.announce_artifact_change``). Both heard only the gateway's own writes, so
an artifact an agent CLI saved was never found by a search in Knowledge, its edit left the old
text there, its delete left a deleted artifact searchable, and an open Artifacts page showed none
of it until it was reloaded.

A process that writes the store now tells the gateway which artifact it wrote
(``artifacts.changes``), and the gateway reads that artifact from its own store and tells both, as
the work of the chat that made the call: an Incognito chat's artifact is shown on the open page and
kept out of Knowledge, as it is when the gateway's own agent saves it. The gateway's own writes are
heard once and handed to no one. The content scan reads an artifact's text once, at the tool that
writes it.

Driven as an agent CLI drives it: the real gateway asking for a sign-in, a page open on its event
stream, and the real ``personalclaw mcp-core`` started with the server the gateway declares to the
CLI (``acp.mcp_servers.core_mcp_servers``), spoken to over stdio. The refused text is the content
scanner's own example of content it refuses: a shopping list with an invisible right-to-left
override character in it; the same list without it is ordinary content.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import aiohttp
import pytest

from personalclaw import gateway_base, mcp_core
from personalclaw.acp.mcp_servers import core_mcp_servers
from personalclaw.artifacts import changes, registry
from personalclaw.artifacts.native import NativeArtifactProvider

#: The chats the agent CLI's tool server serves, as the gateway names them to that process.
CHAT = "dashboard:chat-cli-artifacts"
INCOGNITO = "dashboard:chat-cli-incognito"

#: Every auth shortcut a test process might inherit: each would admit a call before the internal
#: credential is looked at.
_AUTH_SHORTCUTS = (
    "PERSONALCLAW_AUTH_MODE",
    "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
    "PERSONALCLAW_SESSION_KEY",
)

#: The scanner's own example of content it refuses: a list with a right-to-left override in it.
OVERRIDE_LIST = "Shopping list for Saturday: \u202eeggs\u202c, flour, apples."
#: The same list without the override: ordinary content.
CLEAN_LIST = "Shopping list for Saturday: eggs, flour, apples."
#: What a new artifact whose text the content scan refused says.
REFUSED = "Its text failed the content safety scan, so nothing was made from it."

#: How long a page is watched for a hint that must not come.
SETTLE_SECS = 1.0


@pytest.fixture(autouse=True)
def _no_stray_listeners():
    """Every test starts and ends with no artifact listener: the change seam is module state, and a
    gateway stopped in an earlier test leaves its mirror subscribed against a closed library."""
    before = list(changes._listeners)
    changes._listeners.clear()
    yield
    changes._listeners.clear()
    changes._listeners.extend(before)


class _NoEmbedder:
    """No embedding model is bound: Knowledge's search reads its keyword index and its graph."""

    @staticmethod
    def is_available() -> bool:
        return False


@dataclass
class _Gateway:
    home: Path
    user_home: Path
    port: int
    state: Any
    token: str
    #: Every frame the open page has been sent, in order.
    frames: list[dict] = field(default_factory=list)
    #: Every change the gateway's observers of the store were told, in order.
    heard: list[tuple[str, str]] = field(default_factory=list)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    @property
    def bearer(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def told(self, since: int = 0) -> int:
        """How many times, since frame *since*, the open page was told the artifacts changed."""
        return sum(
            1
            for frame in self.frames[since:]
            if frame.get("type") == "refresh"
            and "artifacts" in ((frame.get("data") or {}).get("kinds") or [])
        )

    async def found(self, words: str) -> list[str]:
        """The titles Knowledge's search shows for *words*: what the owner finds there."""
        async with aiohttp.ClientSession() as http:
            resp = await http.get(
                self.url("/api/knowledge/items"), params={"q": words}, headers=self.bearer
            )
            assert resp.status == 200, await resp.text()
            return [item["title"] for item in (await resp.json())["items"]]

    async def save_on_the_artifacts_page(self, name: str, content: str) -> str:
        """The owner saves a markdown artifact on the Artifacts page; its slug."""
        async with aiohttp.ClientSession() as http:
            resp = await http.post(
                self.url("/api/artifacts"),
                json={"name": name, "content": content, "kind": "markdown"},
                headers=self.bearer,
            )
            assert resp.status == 201, await resp.text()
            return (await resp.json())["slug"]

    def stored(self, slug: str) -> Any:
        """The artifact as the gateway's own store holds it."""
        return registry.get_provider("native").get(slug)


async def _until(predicate: Callable[[], bool], *, within: float = 15.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


async def _read_frames(ws: aiohttp.ClientWebSocketResponse, frames: list[dict]) -> None:
    async for message in ws:
        if message.type == aiohttp.WSMsgType.TEXT:
            frames.append(json.loads(message.data))


@contextlib.asynccontextmanager
async def _gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Gateway]:
    home = tmp_path / "data"
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    for name in _AUTH_SHORTCUTS:
        monkeypatch.delenv(name, raising=False)

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.handlers.knowledge as knowledge_handlers
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(knowledge_handlers, "_embedder", lambda: _NoEmbedder())
    # The gateway's store is this home's: the registry keeps the provider it first built, and an
    # earlier test built it in a home of its own.
    monkeypatch.setattr(registry, "_providers", {})

    runner, state = await server_mod.start_dashboard(sessions=MagicMock(count=0), port=0)
    state.get_or_create_session(CHAT.removeprefix("dashboard:"), memory_mode="persistent")
    state.get_or_create_session(INCOGNITO.removeprefix("dashboard:"), memory_mode="incognito")
    port = runner.addresses[0][1]
    # What the gateway does once it has bound (`gateway._publish_runtime_base`): its port in its
    # environment, for every child it starts, and the runtime record that names this process.
    monkeypatch.setenv(gateway_base.PORT_ENV, str(port))
    gateway_base.publish(port)
    heard: list[tuple[str, str]] = []

    def hear(change: str, slug: str) -> None:
        heard.append((change, slug))

    changes.subscribe(hear)
    try:
        async with aiohttp.ClientSession() as http:
            resp = await http.get(
                f"http://127.0.0.1:{port}/api/token/local",
                headers={"X-Local-Secret": (home / ".local_secret").read_text().strip()},
            )
            assert resp.status == 200, await resp.text()
            token = (await resp.json())["token"]
            gw = _Gateway(home=home, user_home=user_home, port=port, state=state, token=token)
            gw.heard = heard
            ws = await http.ws_connect(gw.url("/api/ws"), headers=gw.bearer)
            reader = asyncio.create_task(_read_frames(ws, gw.frames))
            try:
                # The page is open once the gateway has sent it its first frame.
                assert await _until(lambda: bool(gw.frames)), "the page never opened"
                yield gw
            finally:
                reader.cancel()
                await ws.close()
    finally:
        changes.unsubscribe(hear)
        gateway_base.unpublish()
        await runner.cleanup()


class _ToolServer:
    """The ``personalclaw mcp-core`` an agent CLI starts for one chat, spoken to over stdio."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self._id = 0

    async def ask(self, method: str, params: dict) -> dict:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self._id += 1
        frame = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        self.proc.stdin.write((json.dumps(frame) + "\n").encode())
        await self.proc.stdin.drain()
        while True:
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=90)
            if not line:
                err = await self.proc.stderr.read() if self.proc.stderr else b""
                raise AssertionError(f"the tool server ended: {err.decode()[-2000:]}")
            reply = json.loads(line)
            if reply.get("id") == self._id:
                return reply

    async def call(self, tool: str, arguments: dict) -> tuple[bool, str]:
        """One call of *tool*, answered as the CLI's agent reads it: (succeeded, text)."""
        result = (await self.ask("tools/call", {"name": tool, "arguments": arguments}))["result"]
        text = " ".join(part.get("text", "") for part in result.get("content", []))
        return not result.get("isError"), text


@contextlib.asynccontextmanager
async def _tool_server(gw: _Gateway, chat: str) -> AsyncIterator[_ToolServer]:
    """The tool server started as an agent CLI starts it: the command, arguments and environment
    the gateway declares to the CLI for *chat*, on the CLI's own ``PATH`` and ``HOME``."""
    (server,) = core_mcp_servers(session_key=chat)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(gw.user_home)}
    env.update({entry["name"]: entry["value"] for entry in server["env"]})
    proc = await asyncio.create_subprocess_exec(
        server["command"],
        *server["args"],
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    tools = _ToolServer(proc)
    try:
        await tools.ask("initialize", {})
        yield tools
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=20)
        except TimeoutError:
            proc.kill()
            await proc.wait()


def _slug(said: str) -> str:
    found = re.search(r"\(slug: ([a-z0-9-]+)", said)
    assert found, said
    return found.group(1)


# ── an agent CLI's writes ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_what_the_agent_cli_saves_and_edits_is_found_in_knowledge_and_shown_on_the_page(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: the tool saved the artifact, and Knowledge's search found nothing and
    the open page was told nothing; its edit left nothing to find either."""
    async with _gateway(tmp_path, monkeypatch) as gw, _tool_server(gw, CHAT) as tools:
        since = len(gw.frames)
        ok, said = await tools.call(
            "artifact_save",
            {
                "name": "Harbour survey notes",
                "kind": "markdown",
                "content": "# Harbour survey\n\nThe cormorant colony moved to the north jetty.",
            },
        )
        assert ok, said
        slug = _slug(said)
        assert gw.stored(slug) is not None, "the tool server writes the gateway's own store"

        assert await _until(lambda: gw.told(since) >= 1), "the open page was not told"
        assert await gw.found("cormorant") == ["Harbour survey notes"]
        assert gw.heard == [(changes.UPSERT, slug)], "told once: neither missed nor doubled"

        since = len(gw.frames)
        ok, said = await tools.call(
            "artifact_update",
            {
                "slug": slug,
                "content": "# Harbour survey\n\nThe colony is back on the south breakwater.",
            },
        )
        assert ok, said
        assert await _until(lambda: gw.told(since) >= 1), "the open page was not told of the edit"
        assert await gw.found("breakwater") == ["Harbour survey notes"]
        assert await gw.found("cormorant") == [], "the old text must leave Knowledge"
        assert gw.heard == [(changes.UPSERT, slug)] * 2


@pytest.mark.asyncio
async def test_an_artifact_the_agent_cli_deletes_leaves_knowledge_and_the_page(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: the artifact the owner saved stayed searchable in Knowledge after the
    agent CLI deleted it, and the open page went on showing it."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        slug = await gw.save_on_the_artifacts_page(
            "Tide table draft", "# Tides\n\nThe neap tide turns at the lighthouse."
        )
        assert await gw.found("lighthouse") == ["Tide table draft"]

        async with _tool_server(gw, CHAT) as tools:
            since, heard = len(gw.frames), len(gw.heard)
            ok, said = await tools.call("artifact_delete", {"slug": slug})
            assert ok, said
            assert gw.stored(slug) is None

            assert await _until(lambda: gw.told(since) >= 1), "the open page was not told"
            assert await gw.found("lighthouse") == [], "a deleted artifact is still searchable"
            assert gw.heard[heard:] == [(changes.DELETE, slug)]


@pytest.mark.asyncio
async def test_an_incognito_chats_artifact_is_shown_on_the_page_and_kept_out_of_knowledge(
    tmp_path, monkeypatch
):
    """🔴 Red on integration, where the open page was not told. An Incognito chat keeps nothing in
    Knowledge, whichever agent writes for it: its artifact is in the Artifacts library and on the
    open page, and Knowledge's search does not find it, as when the gateway's own agent saves it."""
    from personalclaw import memory_writes
    from personalclaw.tool_providers.registry import create_artifacts_provider

    async with _gateway(tmp_path, monkeypatch) as gw:
        # The control: the gateway's own agent, saving for the Incognito chat in its turn's scope,
        # under the mode the chat holds (`memory_writes.runs_as_its_session`).
        since = len(gw.frames)
        token = mcp_core.set_current_session_key(INCOGNITO)
        try:
            with memory_writes.derived_from(INCOGNITO, memory_mode="incognito"):
                result = await create_artifacts_provider().invoke(
                    "artifact_save",
                    {
                        "name": "Gift ideas",
                        "kind": "markdown",
                        "content": "A brass sextant for the captain.",
                    },
                )
        finally:
            mcp_core.reset_current_session_key(token)
        assert result.success, result.error
        assert await _until(lambda: gw.told(since) >= 1)
        assert await gw.found("sextant") == []

        async with _tool_server(gw, INCOGNITO) as tools:
            since = len(gw.frames)
            ok, said = await tools.call(
                "artifact_save",
                {
                    "name": "Party plan",
                    "kind": "markdown",
                    "content": "Lanterns along the quay at dusk.",
                },
            )
            assert ok, said
            assert gw.stored(_slug(said)) is not None
            assert await _until(lambda: gw.told(since) >= 1), "the open page was not told"
            assert await gw.found("lanterns") == []


@pytest.mark.asyncio
async def test_the_agent_clis_artifact_text_is_scanned_once_at_its_tool(tmp_path, monkeypatch):
    """🔴 Red on integration, where nothing the tool server saved was found in Knowledge. The
    content scan reads an artifact's text at the tool that writes it, in the tool server, and the
    gateway, reading the artifact from its store, scans none of it again. A text the scan refuses is
    not written, so the gateway is told of nothing. The refused list is saved from a file the agent
    wrote: the tool's own arguments are stripped of invisible characters before anything reads
    them (``validation``), and a file's text is read as it is."""
    from personalclaw.knowledge import text_items
    from personalclaw.sel import sel

    scanned_here: list[tuple[str, ...]] = []
    real_refusal = text_items.refusal

    async def counting_refusal(*parts: str, surface: str):
        scanned_here.append(parts)
        return await real_refusal(*parts, surface=surface)

    monkeypatch.setattr(text_items, "refusal", counting_refusal)
    async with _gateway(tmp_path, monkeypatch) as gw, _tool_server(gw, CHAT) as tools:
        since = len(gw.frames)
        ok, said = await tools.call(
            "artifact_save", {"name": "Groceries", "kind": "markdown", "content": CLEAN_LIST}
        )
        assert ok, said
        assert await _until(lambda: gw.told(since) >= 1)
        assert await gw.found("flour") == ["Groceries"]
        assert scanned_here == [], "the gateway scanned again what the tool's own scan had read"

        since, heard = len(gw.frames), len(gw.heard)
        errands = tmp_path / "errands.md"
        errands.write_text(OVERRIDE_LIST, encoding="utf-8")
        ok, said = await tools.call(
            "artifact_save",
            {"name": "Hardware store run", "kind": "markdown", "content_file": str(errands)},
        )
        assert not ok, said
        assert REFUSED in said
        await asyncio.sleep(SETTLE_SECS)
        assert gw.told(since) == 0 and gw.heard[heard:] == [], "nothing was written to tell of"
        assert [a.name for a in registry.get_provider("native").list()] == ["Groceries"]
        refusals = [
            row
            for row in sel().recent(limit=200)
            if row.get("caller_identity") == "uploads.content_scan:artifact"
            and row.get("outcome") == "rejected"
        ]
        assert len(refusals) == 1, "the tool server's scan refused the text, once"
        assert scanned_here == []


# ── the gateway's own writes ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_save_in_the_gateway_is_mirrored_once_and_handed_to_no_one(tmp_path, monkeypatch):
    """The control: what the gateway writes itself was always heard. The owner's save and the
    gateway's own agent's save are each heard once, shown on the page once and found in Knowledge,
    and the gateway hands neither to anyone."""
    from personalclaw.tool_providers.registry import create_artifacts_provider

    handed: list[str] = []
    real_post = mcp_core._post

    def recording_post(path: str, body: dict | None = None, *, timeout: float | None = None):
        handed.append(path)
        return real_post(path, body, timeout=timeout)

    monkeypatch.setattr(mcp_core, "_post", recording_post)
    async with _gateway(tmp_path, monkeypatch) as gw:
        since = len(gw.frames)
        owners = await gw.save_on_the_artifacts_page(
            "Mooring rota", "# Mooring rota\n\nThe pontoon keys hang in the chandlery."
        )
        token = mcp_core.set_current_session_key(CHAT)
        try:
            result = await create_artifacts_provider().invoke(
                "artifact_save",
                {"name": "Crew list", "kind": "markdown", "content": "Bosun, cook and navigator."},
            )
        finally:
            mcp_core.reset_current_session_key(token)
        assert result.success, result.error
        agents = _slug(result.output)

        assert await gw.found("chandlery") == ["Mooring rota"]
        assert await gw.found("navigator") == ["Crew list"]
        await asyncio.sleep(SETTLE_SECS)
        assert gw.heard == [(changes.UPSERT, owners), (changes.UPSERT, agents)]
        assert gw.told(since) == 2
        assert [path for path in handed if path.startswith("/api/artifacts/")] == []


@pytest.mark.asyncio
async def test_the_gateway_reads_what_it_is_told_of_from_its_own_store(tmp_path, monkeypatch):
    """The route a process that wrote the store tells the gateway on carries no text and no claim
    of what changed: the gateway reads the artifact from its store, and tells its observers that it
    was written when the store holds it and removed when it does not. A call names the work it is
    for, as every call made with the internal credential does, and names an artifact by its slug."""

    @contextlib.contextmanager
    def written_elsewhere() -> Iterator[None]:
        """A write as another process makes it: nothing in this process hears it."""
        real_emit = changes.emit
        changes.emit = lambda change, slug: None  # type: ignore[assignment]
        try:
            yield
        finally:
            changes.emit = real_emit  # type: ignore[assignment]

    async with _gateway(tmp_path, monkeypatch) as gw:
        store = registry.get_provider("native")
        with written_elsewhere():
            art = store.create(name="Buoy log", content="Red buoy relit at dawn.", kind="markdown")
        assert await gw.found("relit") == []
        secret = (gw.home / ".local_secret").read_text().strip()

        async def tell(slug: str, *, chat: str = CHAT) -> aiohttp.ClientResponse:
            headers = {"X-Internal-Secret": secret}
            if chat:
                headers["X-Session-Key"] = chat
            async with aiohttp.ClientSession() as http:
                resp = await http.post(gw.url(f"/api/artifacts/{slug}/changed"), headers=headers)
                await resp.read()
                return resp

        since = len(gw.frames)
        resp = await tell(art.slug)
        assert resp.status == 200
        assert await gw.found("relit") == ["Buoy log"]
        assert gw.heard[-1] == (changes.UPSERT, art.slug)
        assert await _until(lambda: gw.told(since) >= 1)

        with written_elsewhere():
            assert store.delete(art.slug)
        resp = await tell(art.slug)
        assert resp.status == 200
        assert await gw.found("relit") == []
        assert gw.heard[-1] == (changes.DELETE, art.slug)

        heard = len(gw.heard)
        unnamed = await tell(art.slug, chat="")
        assert unnamed.status == 403, "a call that names no work it is for is refused"
        not_a_slug = await tell("Not%20a%20slug")
        assert not_a_slug.status == 400
        assert gw.heard[heard:] == []


# ── who is told, from where ────────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path, monkeypatch) -> NativeArtifactProvider:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    return NativeArtifactProvider(root=tmp_path / "home" / "artifacts")


@pytest.fixture
def handed(monkeypatch) -> list[tuple[str, float | None]]:
    """What this process hands to a gateway: each path it posts, with how long it waits."""
    posted: list[tuple[str, float | None]] = []

    def post(path: str, body: dict | None = None, *, timeout: float | None = None) -> dict:
        posted.append((path, timeout))
        return {"slug": path.split("/")[3]}

    monkeypatch.setattr(mcp_core, "_post", post)
    return posted


def _gateway_is(monkeypatch, pid: int | None) -> None:
    """The live gateway of this home, as its runtime record names it (``None``: none runs)."""
    live = None if pid is None else gateway_base.LiveGateway(port=19931, pid=pid)
    monkeypatch.setattr(gateway_base, "live_gateway", lambda: live)


def test_a_process_beside_the_gateway_hands_it_each_write_once(store, handed, monkeypatch):
    """An agent CLI's tool server, or any other process of this home that is not its gateway,
    tells the gateway each artifact it writes, once per write, and nothing for a write that
    changed nothing."""
    _gateway_is(monkeypatch, os.getpid() + 1)

    note = store.create(name="Chart notes", content="# Chart", kind="markdown")
    store.update(note.slug, content="# Chart, corrected", snapshot=True, actor="agent")
    store.update(note.slug, content="# Chart, corrected", snapshot=True, actor="agent")
    assert store.delete(note.slug)

    told = f"/api/artifacts/{note.slug}/changed"
    assert [path for path, _ in handed] == [told, told, told]
    assert all(wait == changes.GATEWAY_TOLD_WITHIN_SECS for _, wait in handed)


def test_the_gateway_hears_its_own_writes_and_hands_them_to_no_one(store, handed, monkeypatch):
    """In the gateway's own process its observers hear the write, and it sends itself nothing."""
    _gateway_is(monkeypatch, os.getpid())
    heard: list[tuple[str, str]] = []

    def hear(change: str, slug: str) -> None:
        heard.append((change, slug))

    changes.subscribe(hear)
    try:
        note = store.create(name="Chart notes", content="# Chart", kind="markdown")
    finally:
        changes.unsubscribe(hear)
    assert heard == [(changes.UPSERT, note.slug)]
    assert handed == []


def test_with_no_gateway_running_a_write_is_kept_and_nothing_is_sent(store, handed, monkeypatch):
    _gateway_is(monkeypatch, None)
    note = store.create(name="Chart notes", content="# Chart", kind="markdown")
    assert store.get(note.slug) is not None
    assert handed == []


def test_a_gateway_that_cannot_be_told_leaves_the_write_kept_and_says_which(
    store, monkeypatch, caplog
):
    """The write has happened by the time the gateway is told, so a gateway that cannot be told
    leaves it kept, and the log says which artifact the gateway has not heard of and why."""
    _gateway_is(monkeypatch, os.getpid() + 1)

    def unanswered(path: str, body: dict | None = None, *, timeout: float | None = None) -> dict:
        return {"error": "the gateway did not answer POST /api/artifacts/x/changed within 10 s"}

    monkeypatch.setattr(mcp_core, "_post", unanswered)
    with caplog.at_level(logging.WARNING, logger="personalclaw.artifacts.changes"):
        note = store.create(name="Chart notes", content="# Chart", kind="markdown")
    assert store.get(note.slug) is not None
    (record,) = [r for r in caplog.records if r.name == "personalclaw.artifacts.changes"]
    assert note.slug in record.getMessage()
    assert "did not answer" in record.getMessage()
