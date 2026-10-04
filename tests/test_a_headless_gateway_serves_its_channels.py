"""A headless gateway serves its channels as the full gateway does, without the dashboard's pages.

``personalclaw gateway --headless`` started a server of its own in place of the gateway's: the
handful of routes an agent's tools call, open to any caller, and nothing else. It loaded no
installed app, so no channel received a message and no model an app provides could answer one, and
its log said "Starting in dashboard-only mode (no channel app is configured)" beside a configured
channel. It wrote no internal credential, so the agent's tools had none to send, and it answered no
health check, so ``personalclaw status`` and ``run`` read it as stopped.

A headless gateway is now the gateway started without the dashboard's pages. These start the real
command in a home of their own that holds an installed channel app and the offline scripted model,
and use the channel as the person on it does: a message lands where the channel's receiver reads,
and what the channel is handed to send is what that person reads.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from port_guard import GUARD

APP = "file-chat"
PROVIDER = "filechat"
OWNER = "4242"
REPLY = "FILE-CHAT-ANSWER: this answer came from the offline scripted model."

#: The channel app: its messages are files. Its receiver hands each file that lands in
#: ``data/inbox`` to the gateway's guarded door, and everything the channel is handed to send is
#: written to ``data/sent.jsonl``.
_PROVIDER_PY = '''
"""A channel whose messages are files, for a gateway started in a test."""

import asyncio
import json
from pathlib import Path

from personalclaw.sdk.channel import ChannelCapabilities, ChannelMessage, ChannelTransportProvider

PROVIDER = "filechat"
DATA = Path(__file__).resolve().parent / "data"


def _sent(kind, channel, text):
    with open(DATA / "sent.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": kind, "channel": channel, "text": text}) + "\\n")


class FileDelivery:
    """What the gateway hands this channel to send, written down."""

    async def open_dm(self, user_id):
        return "dm-" + user_id

    async def deliver_text(self, channel, text, thread_ts="", **_hints):
        _sent("text", channel, text)
        return "m-text"

    async def deliver_rich(self, channel, payload, fallback_text, **_hints):
        _sent("rich", channel, fallback_text)
        return "m-rich"

    async def deliver_cron_result(self, channel, job_name, job_id, text, thread_ts=""):
        _sent("cron", channel, text)
        return "m-cron"

    async def deliver_notification(self, channel, title, text, thread_ts=""):
        _sent("notification", channel, text)
        return "m-notification"

    async def deliver_chat_mirror(self, channel, text, thread_ts=""):
        _sent("answer", channel, text)

    async def deliver_subagent_reply(self, channel, text, thread_ts="", elapsed_secs=0.0):
        _sent("subagent", channel, text)

    async def resolve_user_name(self, user_id):
        return user_id

    async def resolve_user_profile(self, user_id):
        return {}

    async def channel_info(self, channel_id):
        return {}

    def list_reply_channels(self):
        return []

    def is_tracked_channel(self, channel_id):
        return False

    def build_thread_link(self, channel, ts):
        return ""

    async def upload_attachment(self, channel, file_path, **_hints):
        return ""

    async def start_stream(self, channel, thread_ts="", initial_text=""):
        _sent("progress", channel, initial_text)
        return "m-progress"

    async def append_stream_task(self, channel, stream_ts, task_id, title, status):
        return None

    async def stop_stream(self, channel, stream_ts):
        return None

    async def request_approval(self, event, *, source, **_context):
        return None


class FileChannel(ChannelTransportProvider):
    def __init__(self, config=None):
        self._receiver = None

    @property
    def name(self):
        return PROVIDER

    @property
    def display_name(self):
        return "File Chat"

    def capabilities(self):
        return ChannelCapabilities(inbound=True, threads=True, max_text_len=4000)

    async def connect(self):
        return True

    async def disconnect(self):
        return None

    @property
    def connected(self):
        return True

    async def send(self, message):
        _sent("send", message.channel_id, message.text)
        return True

    async def health(self):
        return {"state": "ready", "detail": "reads data/inbox"}

    async def start_inbound(self, services):
        (DATA / "inbox").mkdir(parents=True, exist_ok=True)
        services.register_channel_delivery(FileDelivery(), provider=PROVIDER)
        self._receiver = asyncio.ensure_future(self._receive(services))
        (DATA / "receiving").write_text(type(services).__name__, encoding="utf-8")

    async def stop_inbound(self):
        if self._receiver is not None:
            self._receiver.cancel()

    async def _receive(self, services):
        while True:
            for path in sorted((DATA / "inbox").glob("*.json")):
                message = json.loads(path.read_text(encoding="utf-8"))
                path.unlink()
                where = "dm-" + message["sender"]
                try:
                    verdict = await services.deliver_channel_inbound(
                        PROVIDER,
                        ChannelMessage(
                            channel_id=where,
                            text=message["text"],
                            sender=message["sender"],
                            thread_id=where,
                            message_id=path.stem,
                            metadata={"chat_type": "private"},
                        ),
                        is_dm=True,
                    )
                except Exception as exc:
                    _sent("door-failed", where, repr(exc))
                    continue
                if verdict.canned_reply:
                    _sent("canned", where, verdict.canned_reply)
            await asyncio.sleep(0.1)


def create_channel(config=None):
    return FileChannel(config)
'''

_SCRIPT = {
    "version": 1,
    "on_exhausted": "repeat_last",
    "turns": [
        {
            "text": REPLY,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 9},
        }
    ],
}

#: How long a gateway may take to say it is up, and its channel to answer, before the test fails.
STARTUP_SECS = 90.0
ANSWER_SECS = 60.0


@dataclass
class _Home:
    """An isolated PersonalClaw home holding an installed, set-up channel app."""

    root: Path
    pc: Path
    script: Path

    @property
    def app_data(self) -> Path:
        return self.pc / "apps" / APP / "data"


@pytest.fixture
def home(tmp_path, monkeypatch) -> _Home:
    """What a gateway finds when it restarts in a home whose owner installed the channel app and
    paired on it: the app's files and install record, the owner trusted on the channel and named
    its owner, and a config that keeps the start from checking for updates."""
    from personalclaw import channel_trust
    from personalclaw.apps import manager

    pc = tmp_path / "pc-home"
    (pc / "workspace").mkdir(parents=True)
    (pc / "config.json").write_text(
        json.dumps({"dashboard": {"user_name": "Sam"}, "updates": {"check_enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    app = pc / "apps" / APP
    (app / "data").mkdir(parents=True)
    (app / "provider.py").write_text(textwrap.dedent(_PROVIDER_PY), encoding="utf-8")
    (app / "app.json").write_text(
        json.dumps(
            {
                "name": APP,
                "version": "1.0.0",
                "displayName": "File Chat",
                "description": "A chat channel whose messages are files.",
                "provider": {"type": "channel", "implementation": "provider:create_channel"},
            }
        ),
        encoding="utf-8",
    )
    manager._write_installed(
        APP, manager.InstalledApp(name=APP, version="1.0.0", displayName="File Chat", enabled=True)
    )
    channel_trust.allow_sender(PROVIDER, OWNER, "Sam")
    script = tmp_path / "script.json"
    script.write_text(json.dumps(_SCRIPT), encoding="utf-8")
    return _Home(root=tmp_path, pc=pc, script=script)


@dataclass
class _Gateway:
    proc: subprocess.Popen
    out: Path
    home: _Home
    port: int
    token: str

    def said(self) -> str:
        return self.out.read_text(encoding="utf-8", errors="replace")

    def log(self) -> str:
        path = self.home.pc / "gateway.log"
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def evidence(self) -> str:
        return (
            f"\n--- gateway stdout ---\n{self.said()[-4000:]}"
            f"\n--- gateway.log ---\n{self.log()[-4000:]}"
        )

    def get(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, str, str]:
        """GET *path* from this gateway: ``(status, content type, body)``."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", headers=headers or {}
        )
        try:
            with opener.open(request, timeout=10) as resp:
                return resp.status, resp.headers.get_content_type(), resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get_content_type(), exc.read().decode(errors="replace")

    def owner(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _child_env(home: _Home) -> dict[str, str]:
    """The gateway's environment: this home and a scratch HOME, the scripted model as its one
    model, and no other PersonalClaw setting from the shell that runs the suite. The suite's own
    safety switches go with it, so the gateway starts no app's server or worker."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PERSONALCLAW_")}
    user_home = home.root / "user-home"
    nothing = home.root / "nothing"
    user_home.mkdir(exist_ok=True)
    nothing.mkdir(exist_ok=True)
    env.update(
        {
            "HOME": str(user_home),
            "PERSONALCLAW_HOME": str(home.pc),
            "PERSONALCLAW_WORKSPACE": str(home.pc / "workspace"),
            "PERSONALCLAW_SCRIPTED_MODEL_SCRIPT": str(home.script),
            "PERSONALCLAW_CREDENTIAL_BACKEND": "dotenv",
            # The owner the channel's pairing named: the id the door reaches them by on it.
            "PERSONALCLAW_OWNER_ID_FILECHAT": OWNER,
            "PERSONALCLAW_SKIP_APP_BACKENDS": "1",
            "PERSONALCLAW_SKIP_APP_WORKERS": "1",
            "PERSONALCLAW_ACP_NO_PROVISION": "1",
            "PERSONALCLAW_DISABLE_LIVE_WRITES": "1",
            "CLAUDE_CONFIG_DIR": str(nothing),
            "CODEX_HOME": str(nothing),
        }
    )
    return env


def _wait_for(what: str, ready, seconds: float, gateway: _Gateway | None = None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = ready()
        if found:
            return found
        if gateway is not None and gateway.proc.poll() is not None:
            break
        time.sleep(0.2)
    raise AssertionError(f"{what}{gateway.evidence() if gateway is not None else ''}")


@pytest.fixture
def start_gateway(home) -> Iterator:
    """``start_gateway(*flags)`` runs ``personalclaw gateway`` in the home and returns it once it
    says it is up. Every gateway it started is stopped as a person stops one, with SIGTERM."""
    started: list[_Gateway] = []

    def start(*flags: str) -> _Gateway:
        out = home.root / f"gateway{len(started)}.out"
        with open(out, "w", encoding="utf-8") as sink:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "personalclaw",
                    "-v",
                    "gateway",
                    *flags,
                    "--port",
                    "auto",
                    "--no-open",
                    "--json-ready",
                ],
                env=_child_env(home),
                cwd=home.root,
                stdout=sink,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        gateway = _Gateway(proc=proc, out=out, home=home, port=0, token="")
        started.append(gateway)

        def ready_line() -> dict | None:
            for line in gateway.said().splitlines():
                if line.startswith("PERSONALCLAW_READY:"):
                    return json.loads(line.split(":", 1)[1])
            return None

        ready = _wait_for("the gateway never said it was up", ready_line, STARTUP_SECS, gateway)
        gateway.port, gateway.token = int(ready["port"]), str(ready["token"])
        GUARD.own(gateway.port)  # the port this test's own gateway chose, and said which
        _wait_for(
            "the gateway never finished starting",
            lambda: "PersonalClaw gateway starting" in gateway.said(),
            STARTUP_SECS,
            gateway,
        )
        return gateway

    yield start
    for gateway in started:
        if gateway.proc.poll() is None:
            gateway.proc.send_signal(signal.SIGTERM)
            try:
                gateway.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                gateway.proc.kill()
                gateway.proc.wait(timeout=10)


def _sent(home: _Home) -> list[dict[str, str]]:
    path = home.app_data / "sent.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _say_on_the_channel(home: _Home, text: str, name: str) -> None:
    """The owner sends *text* on the channel: a message file lands where its receiver reads."""
    inbox = home.app_data / "inbox"
    landing = inbox / f".{name}.part"
    landing.write_text(json.dumps({"sender": OWNER, "text": text}), encoding="utf-8")
    landing.rename(inbox / f"{name}.json")


def _answered_on_the_channel(gateway: _Gateway, text: str) -> list[dict[str, str]]:
    """Send *text* as the owner and wait for the channel to be handed the scripted answer."""
    _wait_for(
        "the channel app's receiver never started: the gateway did not load the installed "
        "channel app",
        lambda: (gateway.home.app_data / "receiving").exists(),
        STARTUP_SECS,
        gateway,
    )
    _say_on_the_channel(gateway.home, text, "m-1")
    return _wait_for(
        f"the message was not answered on the channel; it was handed {_sent(gateway.home)}",
        lambda: [m for m in _sent(gateway.home) if REPLY in m["text"]],
        ANSWER_SECS,
        gateway,
    )


def test_a_headless_gateway_answers_its_channel_through_the_door(start_gateway, home):
    gateway = start_gateway("--headless")

    answers = _answered_on_the_channel(gateway, "Are you there?")

    assert {m["channel"] for m in answers} == {f"dm-{OWNER}"}
    assert (home.app_data / "receiving").read_text(encoding="utf-8") == "GatewayOrchestrator"
    assert "no channel app is configured" not in gateway.log(), gateway.evidence()


def test_a_headless_gateway_serves_no_dashboard_page_and_answers_its_clients(start_gateway, home):
    gateway = start_gateway("--headless")

    page = gateway.get("/", gateway.owner())
    assert page[0] == 404, f"a headless gateway served a page: {page[:2]}"
    # `personalclaw status`, `run` and `spawn` find a running gateway by its health check.
    assert gateway.get("/api/healthz")[0] == 200, gateway.evidence()
    # An agent's tools reach the gateway with the internal credential it writes in its home,
    # naming the chat they work for, and its routes answer nobody who brings no credential, as
    # the full gateway's do.
    secret = (home.pc / ".local_secret").read_text(encoding="utf-8").strip()
    tool_call = {"X-Internal-Secret": secret, "X-Session-Key": "dashboard:chat-1"}
    assert gateway.get("/api/lessons", tool_call)[0] == 200, gateway.evidence()
    status, _type, body = gateway.get("/api/lessons")
    assert (status, json.loads(body)["error"]["code"]) == (403, "session_required")


#: What a headless gateway leaves out: the dashboard's pages, and the device pairing flow, whose
#: link opens one and whose device lands on another.
PAGES = {
    "/",
    "/claw.svg",
    "/manifest.webmanifest",
    "/sw.js",
    "/THIRD_PARTY_NOTICES.txt",
    "/THIRD_PARTY_NOTICES_NPM.txt",
    "/login",
    "/pair",
    "/api/devices/pair/start",
    "/api/devices/pair/complete",
}


def test_without_its_pages_the_route_table_is_the_same_api():
    from aiohttp import web

    from personalclaw.dashboard.routes import register_dashboard_routes

    def routes(pages: bool) -> set[tuple[str, str]]:
        app = web.Application()
        register_dashboard_routes(app, pages=pages)
        return {(r.method, r.resource.canonical) for r in app.router.routes() if r.resource}

    full, headless = routes(True), routes(False)
    assert headless <= full
    assert {path for _method, path in full - headless} == PAGES


def test_the_full_gateway_answers_the_same_channel_and_serves_the_dashboard(start_gateway, home):
    """The positive control: the same home, started as the full gateway."""
    gateway = start_gateway()

    answers = _answered_on_the_channel(gateway, "Are you there?")

    assert {m["channel"] for m in answers} == {f"dm-{OWNER}"}
    status, content_type, _ = gateway.get("/", gateway.owner())
    assert status != 404 and content_type == "text/html", (status, content_type)
    assert gateway.get("/api/healthz")[0] == 200
