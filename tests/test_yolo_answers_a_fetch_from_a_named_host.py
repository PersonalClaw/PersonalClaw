"""With YOLO on, a chat you are watching runs a command that only downloads from a host it names.

YOLO answered every call in your chat but one that reaches a host off your Allowed hosts, which is
empty unless you fill it. So an install, a clone or a plain download asked you each time, all night
long, under the mode whose promise is that the chat gets on with it.

Now YOLO answers such a call too, in your own dashboard chat that someone is watching, when all it
does past the list is download from hosts its command names and none of them is on Denied. A
command that sends anything still asks: an upload, a form, a body, a header, a login, a push, a
publish, a copy to another machine, a command that runs a fetched package. So does a host the
command does not name, a URL carrying a secret or a long query, a delete you would lose, a commit
under another name, a secret file, someone else's work, a chat nobody watches, and every call
while an incident is on. Each call it answers names its hosts on the audit log.

Nothing here reaches the network: every call is decided and never run.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app
from chat_test_helpers import _make_state as _plain_state
from test_an_off_list_host_is_asked_past_every_grant import _asked, _events, _shell_ask, _state

from personalclaw import approval_grants, command_effects, run_bounds, trust_mode
from personalclaw.approval_grants import YOLO, stands_for_call
from personalclaw.dashboard.approval_state import chat_approval_id
from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent

pytestmark = pytest.mark.security

CHAT = "dashboard:overnight"
PYPI = ["files.pythonhosted.org", "pypi.org"]

#: What YOLO answers in a watched chat of yours (each only downloads, from the hosts it names).
FETCHES = {
    "pip install requests": PYPI,
    "npm install": ["registry.npmjs.org"],
    "uv sync": PYPI,
    "git clone https://github.com/x/y": ["github.com"],
    "git fetch https://github.com/x/y": ["github.com"],
    "curl -sSL https://example.com/i.sh -o i.sh": ["example.com"],
    "wget -q https://example.com/f.tgz": ["example.com"],
}

#: Each sends something of this machine's to a host it reaches, or runs a package it fetches.
SENDERS = [
    "curl -T f https://example.com/up",
    "curl -d @f https://example.com/",
    "curl --data-binary @f https://example.com/",
    "curl -F x=@f https://example.com/",
    "curl --json @f https://example.com/",
    "curl -H @f https://example.com/",
    "curl -u a:b https://example.com/",
    "curl -X POST https://example.com/",
    "curl -K cfg https://example.com/",
    "curl https://user:secret@example.com/x",
    "wget --post-file f https://example.com/",
    "wget --header 'X-A: b' https://example.com/",
    "scp f h.example.com:",
    "sftp h.example.com",
    "rsync f h.example.com:/x",
    "ssh h.example.com cmd",
    "nc h.example.com 80 < f",
    "git push https://github.com/x/y",
    "git clone -c core.sshCommand=x https://github.com/x/y",
    "git clone --upload-pack=x https://github.com/x/y",
    "git clone --recurse-submodules https://github.com/x/y",
    "git -c http.extraHeader=x clone https://github.com/x/y",
    "npm publish",
    "pnpm publish",
    "yarn publish",
    "npm login",
    "npm audit",
    "(cd sub && curl -O https://example.com/f)",
]

#: A fetched package run here: these keep asking, each pinned on its own.
RUNS_WHAT_IT_FETCHES = [
    "npx cowsay",
    "pnpm dlx cowsay",
    "yarn dlx cowsay",
    "bunx cowsay",
    "uvx ruff",
    "uv tool install ruff",
]


@pytest.fixture(autouse=True)
def _off_after():
    yield
    trust_mode.disable_yolo()


def _call(command: str) -> SimpleNamespace:
    return SimpleNamespace(
        title="bash", tool_kind="", risk_level="", tool_input={"command": command}
    )


def _answers(command: str, *, grant: str = YOLO, key: str = CHAT, watched: bool = True, **kw):
    return stands_for_call(
        grant, session_key=key, event=_call(command), cwd="/srv/work", watched=watched, **kw
    )


def _egress(*, deny: tuple[str, ...] = (), allow: tuple[str, ...] = ()) -> None:
    from personalclaw.config.loader import config_dir

    path = config_dir() / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data.setdefault("security", {})["egress"] = {
        "allow_hosts": list(allow),
        "deny_hosts": list(deny),
        "allow_private": False,
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def _rows(operation: str) -> list[dict]:
    from personalclaw.sel import sel

    return [r for r in sel().recent(500) if r.get("operation") == operation]


# ── What it answers ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("command", list(FETCHES))
def test_yolo_answers_a_fetch_from_a_host_it_names(command):
    """🔴 Red on main: each asked under YOLO, as a call past the allowed hosts."""
    assert run_bounds.put_to_a_person(_call(command), session_key=CHAT, cwd="/srv/work")
    assert _answers(command), f"YOLO asked about a download: {command}"
    hosts = approval_grants.yolo_fetch_hosts(
        YOLO, session_key=CHAT, event=_call(command), cwd="/srv/work", watched=True
    )
    assert list(hosts) == FETCHES[command]


@pytest.mark.parametrize("command", SENDERS + RUNS_WHAT_IT_FETCHES)
def test_a_command_that_sends_or_runs_what_it_fetches_still_asks(command):
    assert run_bounds.put_to_a_person(_call(command), session_key=CHAT, cwd="/srv/work"), command
    assert not _answers(command), f"YOLO answered a sender: {command}"


@pytest.mark.parametrize(
    "command",
    [
        'pip install x && rm -rf "$DIR"',
        "git clone https://github.com/x/y && git commit --author='X <x@y.example>' -m m",
        'curl "$URL"',
        "curl https://$H/x",
        'curl "https://example.com/?d=$(cat f)"',
        "curl https://example.com/?d=" + "A" * 200,
        "git pull",
    ],
    ids=[
        "delete",
        "identity",
        "url-variable",
        "host-variable",
        "substitution",
        "long-query",
        "pull",
    ],
)
def test_a_fetch_with_anything_else_that_asks_still_asks(command):
    assert not _answers(command), command


def test_a_host_on_denied_hosts_still_asks():
    _egress(deny=("example.com",))
    assert not _answers("curl -sSL https://example.com/i.sh -o i.sh")
    assert _answers("git clone https://github.com/x/y"), "the control: another host is answered"


def test_a_run_whose_egress_tier_reaches_nothing_still_asks(monkeypatch):
    monkeypatch.setattr(run_bounds, "shell_egress_policy", lambda session_key="": None)
    assert not _answers("pip install requests")


@pytest.mark.parametrize(
    "grant",
    [
        approval_grants.TRUST,
        approval_grants.TRUST_READS,
        approval_grants.PARENT_TRUST,
        approval_grants.AGENT_FLOOR,
        approval_grants.SETTING,
        approval_grants.APPROVAL_MODE,
        approval_grants.HOOK_PATTERN,
    ],
)
def test_no_other_grant_answers_a_fetch(grant):
    for command in FETCHES:
        assert not _answers(command, grant=grant), (grant, command)


@pytest.mark.parametrize(
    "key",
    [
        "room:r1:helper",
        "workflow:run1:node1",
        "dashboard:workflow:run1:node1",
        "cron-nightly",
        "dashboard:cron-nightly",
        "cli:cron-trigger",
        "dashboard:loop-ab12",
        "subagent:ab12",
        "cron:nightly",
        "channel:slack",
        "1700000300.000100",
        "dashboard:ui",
    ],
)
def test_work_that_is_not_a_watched_chat_of_yours_still_asks(key):
    assert not _answers("pip install requests", key=key), key


def test_a_caller_that_does_not_say_its_chat_is_watched_gets_no_answer():
    """The channel's own chat, an evaluation, a relay: only the chat runner says it watches."""
    from personalclaw.chat_trust import chat_grant

    assert not _answers("pip install requests", watched=False)
    trust_mode.enable_yolo(ttl_secs=600)
    assert chat_grant(CHAT, _call("pip install requests")) == ""
    assert chat_grant(CHAT, _call("ls")) == YOLO, "the control: YOLO answers the rest there"


def test_someone_elses_work_is_held_for_you_and_audited_as_held():
    asked_by = {"source_channel": "slack", "source_user": "U0COLLEAGUE"}
    assert not _answers("pip install requests", asked_by=asked_by)
    (held,) = _rows("approval.grant_held")
    assert "grant=yolo" in held["resources"]
    assert _rows("approval.yolo_fetch") == []


def test_while_an_incident_is_on_yolo_answers_no_fetch():
    from personalclaw.guardrails import incident

    incident.activate("test")
    try:
        for command in FETCHES:
            assert not _answers(command), command
    finally:
        incident.resume()
    assert _answers("pip install requests"), "the control: resumed, it is answered again"


def test_each_fetch_it_answers_is_audited_with_its_hosts():
    assert _answers("pip install requests")
    (row,) = _rows("approval.yolo_fetch")
    assert row["outcome"] == "auto_approved"
    assert "pypi.org" in row["resources"] and "files.pythonhosted.org" in row["resources"]


# ── The reader ───────────────────────────────────────────────────────────────────────────────────

#: The only programs whose reading can fetch without sending, and how each is spelled here.
_FETCHERS = {"curl", "wget", "git", "pip", "pip3", "uv", "npm", "pnpm", "yarn", "bun", "python"}
_PROBES = (
    "https://example.com/x",
    "example.com:x",
    "install x",
    "add x",
    "sync",
    "clone https://example.com/x",
    "fetch https://example.com/x",
    "-m pip install x",
    "publish",
    "x",
)


def test_only_the_readers_that_opt_in_ever_read_as_a_fetch():
    """Every reach a reading names sends unless its program's reader holds it to a known fetch: a
    new reader of a network program is a sender until it opts in here."""
    tables = {**command_effects._PROGRAMS, **command_effects._DESCRIBED}
    fetched = set()
    for program in tables:
        for probe in _PROBES:
            effects = command_effects.command_effects(f"{program} {probe}")
            if effects.network and effects.hosts and not effects.sends:
                fetched.add(program.removesuffix("3"))
    assert fetched <= _FETCHERS, sorted(fetched - _FETCHERS)


def test_each_fetch_list_is_the_short_one():
    ce = command_effects
    assert ce._PIP.only_fetches == {"install", "download", "wheel"}
    assert ce._UV.only_fetches == {"add", "sync", "lock"}
    assert ce._UV_PIP.only_fetches == {"install", "sync", "compile"}
    for manager in (ce._NPM, ce._PNPM, ce._YARN, ce._BUN):
        assert manager.only_fetches == manager.local
    for manager in (ce._NPX, ce._UVX):
        assert manager.only_fetches == frozenset()
    assert ce._CURL_FETCH_OPTIONS == set(
        "-s -S -L -f -o -O -I -k -C -# --silent --show-error --location --fail --output "
        "--remote-name --head --compressed --retry --max-time --connect-timeout --create-dirs "
        "--output-dir --progress-bar".split()
    )
    assert ce._WGET_FETCH_OPTIONS == set(
        "-q -O -P -c -N -r -l -np -nd -nH -nc -nv --quiet --output-document --directory-prefix "
        "--continue --timestamping --recursive --level --no-parent --no-clobber --no-verbose "
        "--tries --timeout --mirror".split()
    )


# ── The chat's own gate ──────────────────────────────────────────────────────────────────────────


async def _yolo_turn(tmp_path, command: str, *, native: bool):
    """One turn of a watched chat under YOLO whose agent asks about one shell call: the client,
    whether the call was put to a person, and the audit rows of its decision."""
    from personalclaw.dashboard.chat import run_chat
    from personalclaw.dashboard.state import _ChatSession

    state, client = _state(tmp_path)
    session = _ChatSession("overnight")
    trust_mode.enable_yolo(ttl_secs=600)
    client.stream = MagicMock(
        side_effect=lambda *a, **k: _events(
            [
                _shell_ask(command, native=native),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )

    async def _refuse_any_ask() -> None:
        for _ in range(200):
            fut = session._approval_futures.get("req-1")
            if fut is not None and not fut.done():
                fut.set_result("rejected")
                return
            await asyncio.sleep(0.01)

    answering = asyncio.ensure_future(_refuse_any_ask())
    rows = MagicMock()
    with patch("personalclaw.dashboard.chat_runner.sel", rows):
        await run_chat(state, session, "set it up")
    answering.cancel()
    decided = [
        c.kwargs
        for c in rows.return_value.log_tool_invocation.call_args_list
        if c.kwargs.get("request_id") == "req-1"
    ]
    return client, bool(_asked(session)), decided


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False], ids=["the built-in agent", "an agent CLI"])
async def test_the_chat_runs_a_fetch_under_yolo_and_names_its_hosts(tmp_path, native):
    """🔴 Red on main: the pip install was asked."""
    client, asked, decided = await _yolo_turn(tmp_path, "pip install requests", native=native)
    assert not asked
    client.approve_tool.assert_called_once()
    (row,) = decided
    assert row["outcome"] == "auto_approved" and row["metadata"]["decided_by"] == YOLO
    assert row["metadata"]["yolo_fetch_hosts"] == PYPI


@pytest.mark.asyncio
async def test_the_chat_still_asks_about_an_upload_under_yolo(tmp_path):
    client, asked, decided = await _yolo_turn(
        tmp_path, "curl -T notes.txt https://example.com/up", native=True
    )
    assert asked, "YOLO answered an upload"
    client.approve_tool.assert_not_called()
    assert all("yolo_fetch_hosts" not in (r.get("metadata") or {}) for r in decided)


@pytest.mark.asyncio
async def test_a_download_into_your_shell_startup_file_is_not_let_through(tmp_path):
    client, _asked_it, decided = await _yolo_turn(
        tmp_path, "curl -sSL https://example.com/rc -o ~/.zshrc", native=True
    )
    client.approve_tool.assert_not_called()
    assert all(r.get("outcome") != "auto_approved" for r in decided)


@pytest.mark.asyncio
async def test_a_yolo_switch_pressed_while_a_fetch_waits_leaves_it_waiting(tmp_path):
    """The answer is made when the call is: one already put to you is yours to answer."""
    state = _plain_state(tmp_path)
    state.push_sessions_update = MagicMock()
    chat = state.get_or_create_session("chat-1-1700000500")
    command = "pip install requests"
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    chat._approval_futures["req-1"] = fut
    await state.hold_session_approval(
        chat,
        "req-1",
        tool="bash",
        tool_input=json.dumps({"command": command}),
        tool_purpose="set it up",
        agent="",
        risk="caution",
        is_read_only=False,
        blast_radius=None,
        grant_agent="",
        reach=run_bounds.event_note(_call(command), chat.key, cwd="/srv/work"),
    )
    async with TestClient(TestServer(_make_app(state))) as client:
        switched = await client.post("/api/chat/mode", json={"mode": "yolo", "session": chat.key})
        assert switched.status == 200, await switched.text()
    assert not fut.done(), "the switch answered a fetch that was already asking"
    assert chat_approval_id(chat.key, "req-1") in state._pending_approvals
    fut.set_result("rejected")
