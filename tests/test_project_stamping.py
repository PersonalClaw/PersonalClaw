"""An ACP save stamps its Project.

The defect, precisely: ``artifact_save`` stamped the ``project_id`` the native runtime's
per-turn project contextvar holds, and read nothing else. An ACP CLI's tools run in a
separate ``personalclaw mcp-core`` process that never sets that project contextvar — and
``provider_bridge`` pops ``project_id`` unconditionally, handing it to the native builder
alone, so nothing about the binding crosses into the ACP branch either. Every ACP save
therefore filed an artifact with no project and it never appeared on its Project page.

A second defect hid behind the first fix: it guarded the gateway read on mcp-core's
session-key contextvar being empty. It was the later per-call binding, mcp-core binding the
session key around EVERY call (``_call_as_its_session``), that turned that guard into
"never ask": the gateway read became unreachable, and every agent CLI save still stamped
``project_id`` "". What decides whether to ask the gateway is the process the tool runs
in (``serves_an_agent_cli()``), not whether a session key happens to be bound.

The fix resolves the binding SERVER-SIDE from the session key, which already crosses to
that process. Three properties are worth more than the happy path and are pinned here:

* the gateway (``serves_an_agent_cli()`` False) must never ask itself, whether or not a
  session key is bound or resolvable — a blocking self-request is at best wasted and at
  worst unservable;
* an unscoped session must stamp "" and never a default project (filing work under a
  project the user never chose is worse than an unstamped artifact);
* the endpoint answers for the chat ``X-Session-Key`` names, which the tool server sets to
  the chat it serves, and with no header, ``dashboard:ui`` or an unknown chat it answers no
  project, never a default.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import personalclaw.mcp_artifacts as ma
from personalclaw import artifact_project, mcp_core
from personalclaw.mcp_core import _CURRENT_SESSION_KEY


class TestNativePathNeverAsksTheGateway:
    def test_a_bound_contextvar_short_circuits_before_any_http(self, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.agents.native.builtin_tools.current_project_id", lambda: "p-native"
        )
        called: list[str] = []
        monkeypatch.setattr(
            artifact_project,
            "session_bound",
            lambda: called.append("http") or artifact_project.Project("", ""),
        )
        assert artifact_project.current().id == "p-native"
        assert called == [], "the native path must not fall through to the gateway read"

    def test_in_process_native_with_no_project_returns_empty_without_asking(self, monkeypatch):
        """The load-bearing guard. An unscoped NATIVE turn has an empty project contextvar
        but a live session key, so without the in-process check it would issue a blocking
        GET from inside the gateway — the gateway waiting on itself. The gateway is said here,
        not left to the flag's default, so a test that ran first cannot decide it."""
        monkeypatch.setattr(mcp_core, "_SERVES_AN_AGENT_CLI", False)
        monkeypatch.setattr(
            "personalclaw.agents.native.builtin_tools.current_project_id", lambda: ""
        )
        calls: list[str] = []
        monkeypatch.setattr(
            "personalclaw.mcp_core._get", lambda path: calls.append(path) or {"project_id": "p-x"}
        )
        token = _CURRENT_SESSION_KEY.set("dashboard:native-turn")
        try:
            assert artifact_project.current().id == ""
        finally:
            _CURRENT_SESSION_KEY.reset(token)
        assert calls == [], f"in-process turn issued a self-request: {calls}"


class TestAcpPathResolvesFromTheSessionKey:
    @pytest.fixture(autouse=True)
    def _out_of_process(self, monkeypatch):
        """The agent CLI's tool server: the process where ``serves_an_agent_cli()`` is True.

        The native runtime's project contextvar is empty there, so the binding has to come
        from the gateway, keyed by the session the call names. The tool server binds that
        session key around every tool call, so it is bound here too."""
        monkeypatch.setattr(mcp_core, "_SERVES_AN_AGENT_CLI", True)
        monkeypatch.setattr(
            "personalclaw.agents.native.builtin_tools.current_project_id", lambda: ""
        )
        token = _CURRENT_SESSION_KEY.set("dashboard:acp-chat")
        yield
        _CURRENT_SESSION_KEY.reset(token)

    def test_the_bound_project_is_stamped(self, monkeypatch):
        monkeypatch.setattr(artifact_project, "_resolve_session_key", lambda: "dashboard:acp-chat")
        seen: list[str] = []
        monkeypatch.setattr(
            "personalclaw.mcp_core._get",
            lambda path: seen.append(path) or {"project_id": "p-acp"},
        )
        assert artifact_project.current().id == "p-acp"
        assert seen == ["/api/chat/sessions/bound-project"]

    def test_an_unscoped_session_stamps_nothing(self, monkeypatch):
        """A 200 with an empty id is the normal unscoped answer — not an error, and not a
        reason to substitute a default."""
        monkeypatch.setattr(artifact_project, "_resolve_session_key", lambda: "dashboard:acp-chat")
        monkeypatch.setattr("personalclaw.mcp_core._get", lambda path: {"project_id": ""})
        assert artifact_project.current().id == ""

    def test_no_session_identity_asks_nothing(self, monkeypatch):
        """No session identity at all: the key the class's fixture binds is unbound for this
        test's duration, so the bound key and the resolver agree there is none."""
        monkeypatch.setattr(artifact_project, "_resolve_session_key", lambda: "")
        calls: list[str] = []
        monkeypatch.setattr(
            "personalclaw.mcp_core._get", lambda path: calls.append(path) or {"project_id": "p"}
        )
        token = _CURRENT_SESSION_KEY.set("")
        try:
            assert artifact_project.current().id == ""
        finally:
            _CURRENT_SESSION_KEY.reset(token)
        assert calls == []

    def test_a_gateway_error_stamps_nothing_and_does_not_raise(self, monkeypatch):
        """``_get`` reports transport failure as ``{"error": ...}`` rather than raising, so
        an unreachable gateway must not become a stamped project OR a failed save."""
        monkeypatch.setattr(artifact_project, "_resolve_session_key", lambda: "dashboard:acp-chat")
        monkeypatch.setattr(
            "personalclaw.mcp_core._get", lambda path: {"error": "Connection refused"}
        )
        assert artifact_project.current().id == ""

    def test_a_raising_bridge_stamps_nothing(self, monkeypatch):
        monkeypatch.setattr(artifact_project, "_resolve_session_key", lambda: "dashboard:acp-chat")

        def _boom(path):
            raise RuntimeError("socket exploded")

        monkeypatch.setattr("personalclaw.mcp_core._get", _boom)
        assert artifact_project.current().id == ""


class TestTheProcessDecidesWhoAnswers:
    """Which process the lookup runs in, not whether a session key is bound, decides
    whether the gateway is asked. The agent CLI's tool server binds the session key
    around every tool call, so a bound key cannot mean "I am inside the gateway"."""

    @pytest.fixture(autouse=True)
    def _no_native_project(self, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.agents.native.builtin_tools.current_project_id", lambda: ""
        )

    def test_the_agent_clis_tool_server_asks_the_gateway_with_its_call_bound(self, monkeypatch):
        """Pins the agent CLI's save: its tool server binds the session key for the call,
        and the lookup must still ask the gateway for that session's project. Red on main,
        where a bound key returns "" without asking."""
        monkeypatch.setattr(mcp_core, "_SERVES_AN_AGENT_CLI", True)
        monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "")
        calls: list[str] = []
        monkeypatch.setattr(
            "personalclaw.mcp_core._get", lambda path: calls.append(path) or {"project_id": "p1"}
        )
        token = _CURRENT_SESSION_KEY.set("dashboard:acp-chat")
        try:
            assert artifact_project.session_bound().id == "p1"
            assert calls == ["/api/chat/sessions/bound-project"]
            calls.clear()
            assert artifact_project.current().id == "p1"
            assert calls == ["/api/chat/sessions/bound-project"]
        finally:
            _CURRENT_SESSION_KEY.reset(token)

    def test_a_warm_pool_lookup_names_the_chat_its_call_bound(self, monkeypatch):
        """Pins which chat the lookup asks about. A warm-pool tool server is spawned with an
        empty PERSONALCLAW_SESSION_KEY, so the only key it has is the one its call bound; the
        request must carry that key as X-Session-Key. Red when the lookup reads only the
        environment's key, which names no chat and files the save under no project."""
        monkeypatch.setattr(mcp_core, "_SERVES_AN_AGENT_CLI", True)
        monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "")
        monkeypatch.setattr(mcp_core, "_internal_secret", lambda: "test-secret")
        monkeypatch.setattr(mcp_core, "_api_base", lambda: "http://127.0.0.1:1")
        keys: list[str | None] = []

        def _gateway(req, timeout=None):  # noqa: ARG001 - the transport's signature
            keys.append(req.get_header("X-session-key"))
            return _Answer(b'{"project_id": "p-b"}')

        monkeypatch.setattr(mcp_core.home_gateway, "open_loopback", _gateway)
        token = _CURRENT_SESSION_KEY.set("dashboard:chat-b")
        try:
            assert artifact_project.current().id == "p-b"
        finally:
            _CURRENT_SESSION_KEY.reset(token)
        assert keys == ["dashboard:chat-b"]

    @pytest.mark.parametrize("case", ["bound", "resolvable_only"])
    def test_the_gateway_never_asks_itself(self, monkeypatch, case):
        """Pins the gateway side: inside the gateway the native project contextvar is
        authoritative, so no request goes out, whether the session key is bound for the
        turn or only resolvable from the environment. ``resolvable_only`` is red on main,
        where an unbound key that still resolves sends the gateway a request to itself."""
        monkeypatch.setattr(mcp_core, "_SERVES_AN_AGENT_CLI", False)
        calls: list[str] = []
        monkeypatch.setattr(
            "personalclaw.mcp_core._get", lambda path: calls.append(path) or {"project_id": "p-x"}
        )
        if case == "bound":
            monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "")
            token = _CURRENT_SESSION_KEY.set("dashboard:native-turn")
        else:
            monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "dashboard:native-turn")
            token = _CURRENT_SESSION_KEY.set("")
        try:
            got = artifact_project.session_bound().id
        finally:
            _CURRENT_SESSION_KEY.reset(token)
        assert calls == [], f"the gateway issued a request to itself: {calls}"
        assert got == ""


class TestTheEndpointKeysOffTheHeaderOnly:
    """`api_chat_session_bound_project` answers for the chat the call names in X-Session-Key
    beside PersonalClaw's internal credential (the tool server names the chat it serves), never
    for one named in its path or query, and answers no Project to an app's token or to work
    that is no chat's."""

    def _request(self, headers: dict, sessions: dict):
        """A call an agent CLI's tools make: the internal credential, naming their session."""
        state = SimpleNamespace(_sessions=sessions)

        class _Request(dict):
            app = {"state": state}

        request = _Request()
        request.headers = {"X-Internal-Secret": "the-gateways-own", **headers}
        return request

    async def _call(self, request):
        from personalclaw.dashboard.chat_handlers import api_chat_session_bound_project

        resp = await api_chat_session_bound_project(request)
        import json

        return json.loads(resp.body.decode())

    @pytest.mark.asyncio
    async def test_the_header_names_the_session(self):
        sessions = {"a": SimpleNamespace(project_id="p-a")}
        got = await self._call(self._request({"X-Session-Key": "dashboard:a"}, sessions))
        assert got == {"project_id": "p-a"}

    @pytest.mark.asyncio
    async def test_an_absent_header_is_an_empty_id_not_an_error(self):
        got = await self._call(self._request({}, {"a": SimpleNamespace(project_id="p-a")}))
        assert got == {"project_id": ""}

    @pytest.mark.asyncio
    async def test_the_ui_pseudo_session_resolves_to_nothing(self):
        """``dashboard:ui`` is the browser's own key, not a chat session — the same
        exclusion ``handlers/context._session_project_id`` makes."""
        got = await self._call(
            self._request(
                {"X-Session-Key": "dashboard:ui"}, {"ui": SimpleNamespace(project_id="p")}
            )
        )
        assert got == {"project_id": ""}

    @pytest.mark.asyncio
    async def test_an_unknown_session_resolves_to_nothing(self):
        got = await self._call(self._request({"X-Session-Key": "dashboard:ghost"}, {}))
        assert got == {"project_id": ""}

    @pytest.mark.asyncio
    async def test_a_session_with_no_project_resolves_to_empty_never_a_default(self):
        sessions = {"a": SimpleNamespace(project_id="")}
        got = await self._call(self._request({"X-Session-Key": "dashboard:a"}, sessions))
        assert got == {"project_id": ""}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "work", ["cron:nightly", "subagent:nightly", "hook:nightly", "tile:nightly", "loop:nightly"]
    )
    async def test_work_that_is_no_chats_resolves_to_nothing(self, work):
        """A chat that has the name after the colon is not this work's chat."""
        sessions = {"nightly": SimpleNamespace(project_id="p-harbour")}
        got = await self._call(self._request({"X-Session-Key": work}, sessions))
        assert got == {"project_id": ""}


def test_the_literal_route_is_registered_before_the_dynamic_one():
    """Vacuity floor for the route itself: registered AFTER ``{session}`` it would be
    captured as a session named "bound-project" and the endpoint would be unreachable —
    the exact hazard ``bulk``/``templates`` are commented for in ``dashboard/routes.py``."""
    import pathlib

    import personalclaw

    src = (pathlib.Path(personalclaw.__file__).parent / "dashboard" / "routes.py").read_text()
    lit = src.index('add_get("/api/chat/sessions/bound-project"')
    dyn = src.index('add_get("/api/chat/sessions/{session}"')
    assert lit < dyn, "bound-project must register before the {session} pattern"


class TestANameIsDedupedInItsOwnProject:
    """A fresh save's name check looks only in the Project the save is filed under. Looking in
    every Project refused a save whose name another Project's artifact holds, and its hint
    steered the agent to write that artifact's next version, which stays in the other Project."""

    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        from personalclaw.artifacts.native import NativeArtifactProvider

        prov = NativeArtifactProvider(root=tmp_path / "artifacts")
        monkeypatch.setattr("personalclaw.artifacts.registry.get_provider", lambda _name: prov)
        monkeypatch.setattr(ma, "_resolve_session_key", lambda: "dashboard:chat-1")
        monkeypatch.setattr(mcp_core, "_SERVES_AN_AGENT_CLI", False)
        return prov

    @staticmethod
    def _save_in(project_id: str, name: str, content: str) -> str:
        from personalclaw.agents.native.builtin_tools import bind_tool_context, reset_tool_context

        bound = bind_tool_context(cwd=None, project_id=project_id)
        try:
            return ma._call_tool(
                "artifact_save", {"name": name, "kind": "markdown", "content": content}
            )
        finally:
            reset_tool_context(bound)

    def test_a_save_in_p_makes_a_new_row_beside_qs(self, store):
        theirs = store.create(
            name="Berth plan", content="Q's berths.", kind="markdown", project_id="p-quay"
        )
        said = self._save_in("p-harbour", "Berth plan", "P's berths.")
        assert "(slug: berth-plan-2," in said, said
        ours = store.get("berth-plan-2")
        assert ours.project_id == "p-harbour"
        assert ours.content == "P's berths."
        kept = store.get(theirs.slug)
        assert (kept.project_id, kept.version, kept.content) == ("p-quay", 1, "Q's berths.")

    def test_an_unscoped_save_dedups_only_against_unscoped(self, store):
        store.create(name="Berth plan", content="Q's berths.", kind="markdown", project_id="p-quay")
        said = self._save_in("", "Berth plan", "Loose berths.")
        assert "(slug: berth-plan-2," in said, said
        assert store.get("berth-plan-2").project_id == ""

        said = self._save_in("", "Berth plan", "More loose berths.")
        assert "already exists (slug: berth-plan-2)" in said, said
        assert store.get("berth-plan-2").content == "Loose berths."

    def test_a_save_in_p_is_refused_when_p_holds_the_name(self, store):
        store.create(
            name="Berth plan", content="P's berths.", kind="markdown", project_id="p-harbour"
        )
        said = self._save_in("p-harbour", "Berth plan", "More of P's berths.")
        assert "already exists (slug: berth-plan)" in said, said
        assert store.get("berth-plan-2") is None
        assert store.get("berth-plan").content == "P's berths."


#: What a new row's reply ends with when the gateway did not say which Project its chat is in.
NOTE = (
    " Filed under no project: PersonalClaw's gateway didn't say which project this chat belongs to."
)


class _Answer:
    """What the gateway's transport hands back: a body to read, inside a ``with``."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _refused_connection(req, timeout=None):  # noqa: ARG001 - the transport's signature
    import urllib.error

    raise urllib.error.URLError(ConnectionRefusedError())


def _unavailable(req, timeout=None):  # noqa: ARG001
    import io
    import urllib.error

    raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b""))


def _not_json(req, timeout=None):  # noqa: ARG001
    return _Answer(b"<html>Bad gateway</html>")


def _timed_out(req, timeout=None):  # noqa: ARG001
    raise TimeoutError("timed out")


def _a_number(req, timeout=None):  # noqa: ARG001
    return _Answer(b'{"project_id": 7}')


_FAILURES = pytest.mark.parametrize(
    "answer",
    [_refused_connection, _unavailable, _not_json, _timed_out, _a_number],
    ids=["refused", "http-503", "not-json", "timeout", "project-id-7"],
)


class TestAFailedLookupIsSaidAloud:
    """A Project lookup the gateway does not answer files the new row under no Project, never a
    default one and never a failed save, and says so: in the reply, where the agent reads it, and
    in a WARNING that names the chat. Before, the reply read like any unscoped save, nothing was
    logged, and an answer that was not a str was stored as its str() ('7')."""

    SESSION = "dashboard:acp-chat"

    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        from personalclaw.artifacts.native import NativeArtifactProvider

        prov = NativeArtifactProvider(root=tmp_path / "artifacts")
        monkeypatch.setattr("personalclaw.artifacts.registry.get_provider", lambda _name: prov)
        monkeypatch.setattr(mcp_core, "_SERVES_AN_AGENT_CLI", True)
        monkeypatch.setattr(
            "personalclaw.agents.native.builtin_tools.current_project_id", lambda: ""
        )
        monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "")
        monkeypatch.setattr(mcp_core, "_internal_secret", lambda: "test-secret")
        monkeypatch.setattr(mcp_core, "_api_base", lambda: "http://127.0.0.1:1")
        bound = _CURRENT_SESSION_KEY.set(self.SESSION)
        yield prov
        _CURRENT_SESSION_KEY.reset(bound)

    @staticmethod
    def _gateway(monkeypatch, answer) -> None:
        monkeypatch.setattr(mcp_core.home_gateway, "open_loopback", answer)

    def _said_aloud(self, caplog) -> list[str]:
        return [
            r.getMessage()
            for r in caplog.records
            if r.name == "personalclaw.mcp_artifacts"
            and r.levelno == logging.WARNING
            and self.SESSION in r.getMessage()
        ]

    @_FAILURES
    def test_artifact_save(self, store, monkeypatch, caplog, answer):
        self._gateway(monkeypatch, answer)
        caplog.set_level(logging.WARNING)
        said = ma._call_tool(
            "artifact_save", {"name": "Berth plan", "kind": "markdown", "content": "Berths."}
        )
        assert "(slug: berth-plan," in said, said
        assert store.get("berth-plan").project_id == ""
        assert said.endswith(NOTE), said
        assert self._said_aloud(caplog), [r.getMessage() for r in caplog.records]

    @_FAILURES
    def test_document_create(self, store, monkeypatch, caplog, answer):
        self._gateway(monkeypatch, answer)
        caplog.set_level(logging.WARNING)
        said = ma._call_tool("document_create", {"name": "Tide log", "markdown": "# Tides\n"})
        assert said.startswith("Created docx: tide-log (v1"), said
        assert store.get("tide-log").project_id == ""
        assert said.endswith(NOTE), said
        assert self._said_aloud(caplog), [r.getMessage() for r in caplog.records]

    def test_a_loops_save_of_text_it_holds_asks_nothing(self, store, monkeypatch, caplog):
        """A loop's save of text its deliverable already holds updates that deliverable in
        place, so it has no row to stamp and no Project to ask the gateway for."""
        tags = ["loop:abc123"]
        self._gateway(monkeypatch, lambda req, timeout=None: _Answer(b'{"project_id": ""}'))
        args = {"name": "Berth plan", "kind": "markdown", "content": "Berths.", "tags": tags}
        first = ma._call_tool("artifact_save", args)
        assert "(slug: berth-plan," in first, first

        asked: list[str] = []

        def _recorded(req, timeout=None):
            asked.append(req.full_url)
            return _unavailable(req, timeout)

        self._gateway(monkeypatch, _recorded)
        caplog.set_level(logging.WARNING)
        said = ma._call_tool("artifact_save", {**args, "name": "Berth plan, final"})
        assert "updated it in place" in said, said
        assert not [url for url in asked if url.endswith("/api/chat/sessions/bound-project")], asked
        assert self._said_aloud(caplog) == []

    def test_an_unscoped_docx_of_the_same_name_and_a_failed_lookup_make_a_new_row(
        self, store, monkeypatch, caplog
    ):
        """An unscoped document of the same name is not this chat's to write over: the lookup
        did not say the chat is unscoped. The create makes its own row rather than a refusal
        that steers the agent onto that document's next version."""
        self._gateway(monkeypatch, lambda req, timeout=None: _Answer(b'{"project_id": ""}'))
        first = ma._call_tool("document_create", {"name": "Tide log", "markdown": "# Loose\n"})
        assert first.startswith("Created docx: tide-log (v1"), first
        assert not first.endswith(NOTE), first

        self._gateway(monkeypatch, _refused_connection)
        caplog.set_level(logging.WARNING)
        said = ma._call_tool("document_create", {"name": "Tide log", "markdown": "# Ours\n"})
        assert said.startswith("Created docx: tide-log-2 (v1"), said
        assert said.endswith(NOTE), said
        assert self._said_aloud(caplog)
        kept = store.get("tide-log")
        assert (kept.project_id, kept.version) == ("", 1)
        assert store.get("tide-log-2").project_id == ""

    @_FAILURES
    def test_an_unscoped_artifact_of_the_same_name_and_a_failed_lookup_make_a_new_row(
        self, store, monkeypatch, caplog, answer
    ):
        """The artifact twin of the document case above: the unscoped 'Berth plan' is not this
        chat's to be refused onto, and following that refusal's hint would write the chat's text
        over it as its next version."""
        self._gateway(monkeypatch, lambda req, timeout=None: _Answer(b'{"project_id": ""}'))
        args = {"name": "Berth plan", "kind": "markdown"}
        first = ma._call_tool("artifact_save", {**args, "content": "Loose berths."})
        assert "(slug: berth-plan," in first, first
        assert not first.endswith(NOTE), first

        self._gateway(monkeypatch, answer)
        caplog.set_level(logging.WARNING)
        said = ma._call_tool("artifact_save", {**args, "content": "Our berths."})
        assert "(slug: berth-plan-2," in said, said
        assert said.endswith(NOTE), said
        assert self._said_aloud(caplog)
        assert store.get("berth-plan-2").project_id == ""
        kept = store.get("berth-plan")
        assert (kept.project_id, kept.version, kept.content) == ("", 1, "Loose berths.")
