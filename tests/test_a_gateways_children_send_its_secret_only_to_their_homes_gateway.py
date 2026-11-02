"""A gateway's children send its credential only to their own home's gateway, at 127.0.0.1, once it
has proved it holds that home's secret.

Commands reached a home's gateway only after asking it which home it serves (``home_gateway.reach``:
``GET /api/healthz``, compared with this home's ``home_id``), and sent everything to ``127.0.0.1``.
The gateway's own children did neither: the ``mcp-core`` server an agent CLI runs, the MCP servers'
shared tool-policy read, and a scheduled script's launcher called the port they were handed with
the gateway's internal credential and never asked whose gateway answered there, and the two MCP
paths addressed it as ``localhost``, which can resolve to the IPv6 loopback first. A child that
outlives its gateway (an agent CLI session kept running, a script across a restart) still holds
the old port, where another program, or another home's gateway, may now listen. And ``home_id`` is
a fingerprint of a path, which anyone who can guess the path can answer with.

Now every caller that will send the secret (a command, and each child before each call) asks with a
fresh nonce, and the gateway proves it holds the secret: an HMAC under it over the nonce and the
port it was asked on (``home_gateway.gateway_proof``). A port answered by another home's gateway, a
program that is not one, one that answers this home's id without a proof or with a wrong one, a
replayed proof, or a proof made on another port, gets nothing.

What answers ``/api/healthz`` is stubbed, and so is the loopback transport, which records every
request a child would have sent: no socket is opened and no credential leaves the test. The
gateway's own route is driven through aiohttp's test server, to read the proof it answers.
"""

from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from personalclaw import gateway_base, home_gateway, mcp_core

pytestmark = pytest.mark.security

PORT = 47123
SECRET = "s3cret-for-the-test"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """This home, holding the local secret its gateway wrote, and the port its children are
    handed."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv(gateway_base.PORT_ENV, str(PORT))
    (tmp_path / ".local_secret").write_text(SECRET, encoding="utf-8")
    monkeypatch.setattr(mcp_core, "_internal_secret", lambda: SECRET)
    return tmp_path


class _Answer(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


#: Who answers the identity read, and how.
THIS_HOME, ANOTHER_HOME, A_STRANGER = "this home", "another home", "a stranger"
NO_PROOF, WRONG_PROOF, REPLAYED, RELAYED = "no proof", "wrong proof", "replayed", "relayed"


@pytest.fixture
def loopback(monkeypatch) -> dict[str, Any]:
    """The gateway side, stubbed: who answers ``/api/healthz`` and with what proof, the nonces it
    was asked with, and every other request a child sent, with its headers."""
    seen: dict[str, Any] = {"answers": THIS_HOME, "sent": [], "nonces": []}

    def healthz(port: int, *, attempts: int = 1, nonce: str = "") -> Any:
        seen["nonces"].append(nonce)
        answers = seen["answers"]
        if answers == A_STRANGER:
            return {}
        said: dict[str, Any] = {"status": "ok", "pid": 1, "home_id": home_gateway.home_id()}
        if answers == ANOTHER_HOME:
            said["home_id"] = "0123456789abcdef"
        if answers in (THIS_HOME, ANOTHER_HOME):
            said["proof"] = home_gateway.gateway_proof(SECRET, nonce, port)
        elif answers == WRONG_PROOF:
            said["proof"] = home_gateway.gateway_proof("not this home's secret", nonce, port)
        elif answers == REPLAYED:  # the proof the first read was answered with, kept
            said["proof"] = home_gateway.gateway_proof(SECRET, seen["nonces"][0], port)
        elif answers == RELAYED:  # a proof fetched from this home's gateway on its own port
            said["proof"] = home_gateway.gateway_proof(SECRET, nonce, port + 1)
        return said

    def open_loopback(request: Any, *, timeout: float) -> Any:
        seen["sent"].append((request.full_url, dict(request.header_items())))
        return _Answer(b'{"ok": true}')

    monkeypatch.setattr(home_gateway, "_healthz", healthz)
    monkeypatch.setattr(home_gateway, "open_loopback", open_loopback)
    return seen


def _secret_sent(seen: dict[str, Any]) -> list[str]:
    return [url for url, headers in seen["sent"] if SECRET in headers.values()]


def test_a_core_tool_call_sends_nothing_to_another_homes_gateway(home, loopback) -> None:
    loopback["answers"] = ANOTHER_HOME

    answer = mcp_core._get("/api/status")

    assert _secret_sent(loopback) == [], "the credential went to another home's gateway"
    assert "not by this home's" in str(answer.get("error", "")), answer


def test_a_core_tool_call_sends_nothing_to_a_program_that_is_not_a_gateway(home, loopback) -> None:
    loopback["answers"] = A_STRANGER

    answer = mcp_core._post("/api/send-message", {"text": "x"})

    assert _secret_sent(loopback) == []
    assert "not a PersonalClaw gateway" in str(answer.get("error", "")), answer


def test_this_homes_id_without_a_proof_gets_nothing(home, loopback) -> None:
    """🔴 Before: the home's id, which a path's fingerprint is, was all the read asked."""
    loopback["answers"] = NO_PROOF

    answer = mcp_core._get("/api/status")

    assert _secret_sent(loopback) == []
    said = str(answer.get("error", ""))
    assert "did not prove it holds this home's local secret" in said, answer
    assert "restart it" in said, "an older gateway, which gives no proof, is told to restart"


def test_this_homes_id_with_a_wrong_proof_gets_nothing(home, loopback) -> None:
    loopback["answers"] = WRONG_PROOF

    answer = mcp_core._get("/api/status")

    assert _secret_sent(loopback) == []
    assert "a proof this home's local secret did not make" in str(answer.get("error", "")), answer


def test_a_replayed_proof_fails_against_a_fresh_nonce(home, loopback) -> None:
    loopback["answers"] = REPLAYED

    mcp_core._get("/api/status")  # its read's own nonce: a good proof
    answer = mcp_core._get("/api/status")  # the same proof again, for a new nonce

    nonces = loopback["nonces"]
    assert len(nonces) == 2 and nonces[0] != nonces[1] and all(map(home_gateway.is_nonce, nonces))
    assert len(_secret_sent(loopback)) == 1, "only the first call, whose proof was its own"
    assert "did not make" in str(answer.get("error", "")), answer


def test_a_proof_made_on_another_port_is_not_taken(home, loopback) -> None:
    """A program on the handed port that asks this home's gateway, on its own port, for a proof and
    passes it on: the proof is bound to the port it was made on."""
    loopback["answers"] = RELAYED

    answer = mcp_core._get("/api/status")

    assert _secret_sent(loopback) == []
    assert "did not make" in str(answer.get("error", "")), answer


def test_its_own_gateway_is_proved_before_each_call_at_127_0_0_1(home, loopback) -> None:
    mcp_core._get("/api/status")
    mcp_core._get("/api/status")

    assert [url for url, _h in loopback["sent"]] == [f"http://127.0.0.1:{PORT}/api/status"] * 2
    assert len(loopback["nonces"]) == 2, "asked, with a nonce of its own, before every call"


def test_a_port_lost_between_two_calls_that_got_through_gets_nothing_more(home, loopback) -> None:
    """The gateway restarted elsewhere and another took its port, with no failed call between."""
    mcp_core._get("/api/status")
    loopback["answers"] = ANOTHER_HOME

    answer = mcp_core._get("/api/status")

    assert len(_secret_sent(loopback)) == 1
    assert "not by this home's" in str(answer.get("error", "")), answer


def test_the_tool_policy_read_sends_nothing_without_a_proof(home, loopback, monkeypatch) -> None:
    from personalclaw import mcp_shared

    monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "pinned:abc")
    monkeypatch.setattr(mcp_shared, "_excluded_tools", None)
    monkeypatch.setattr(mcp_shared, "_last_failure_time", 0.0)
    monkeypatch.setattr(mcp_shared, "_last_startup_race_time", 0.0)
    loopback["answers"] = NO_PROOF

    mcp_shared._resolve_excluded_tools()

    assert _secret_sent(loopback) == []


def test_a_command_takes_no_gateway_that_cannot_prove_it(home, loopback) -> None:
    """``reach`` is what every command that sends the home's secret asks, and it asks the proof."""
    loopback["answers"] = NO_PROOF

    with pytest.raises(home_gateway.NotThisHomesGateway, match="did not prove"):
        home_gateway.reach(PORT)

    loopback["answers"] = THIS_HOME
    assert home_gateway.reach(PORT).url == f"http://127.0.0.1:{PORT}"


def test_a_child_is_handed_the_loopback_address_not_a_name(home, loopback) -> None:
    """``localhost`` can resolve to the IPv6 loopback first, where another program may listen."""
    assert gateway_base.resolve_api_base() == f"http://127.0.0.1:{PORT}"


# ── the gateway's own identity route ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_gateway_answers_a_nonce_with_its_proof_over_the_port_it_was_asked_on() -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers_system import api_healthz

    app = web.Application()
    app["local_secret"] = SECRET
    app.router.add_get("/api/healthz", api_healthz)
    nonce = home_gateway.new_nonce()
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        proved = await (await client.get(f"/api/healthz?nonce={nonce}")).json()
        plain = await (await client.get("/api/healthz")).json()
        malformed = await (await client.get("/api/healthz?nonce=not-a-nonce")).json()
        port = client.port

    assert proved["proof"] == home_gateway.gateway_proof(SECRET, nonce, port)
    assert "proof" not in plain and "proof" not in malformed
    assert SECRET not in json.dumps(proved), "the secret itself never leaves"


# ── a scheduled script's launcher ───────────────────────────────────────────────────────────

HOME_ID = "fedcba9876543210"


def _launcher(tmp_path: Path, monkeypatch) -> dict[str, Any]:
    """The launcher a scheduled script runs in, loaded as the child loads it, with the config the
    gateway writes for it."""
    from personalclaw import schedule_script

    cfg = tmp_path / "cfg.json"
    cfg.write_text(
        json.dumps(
            {
                "script_path": "",
                "func": "",
                "message": "",
                "secret": SECRET,
                "secret_unavailable": "",
                "port": PORT,
                "session_key": "cron:abc",
                "home_id": HOME_ID,
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["launcher", str(cfg)])
    module = types.ModuleType("pc_cron_launcher")
    src, run = schedule_script._LAUNCHER_SRC.rsplit("\n_main()", 1)
    assert not run.strip(), "the launcher ends by running the script"
    exec(
        compile(src, "launcher", "exec"), module.__dict__
    )  # noqa: S102 - its own source, no script
    return module.__dict__


class _Opener:
    """The gateway side of a launcher's calls back: who answers its identity read, and every other
    request it sent."""

    def __init__(self, answers: str) -> None:
        self.answers = answers
        self.sent: list[tuple[str, dict]] = []

    def open(self, request: Any, timeout: float = 0) -> Any:
        url = request.full_url
        if "/api/healthz" in url:
            nonce = url.partition("nonce=")[2]
            said: dict[str, Any] = {"status": "ok", "home_id": HOME_ID}
            if self.answers == ANOTHER_HOME:
                said["home_id"] = "0123456789abcdef"
            if self.answers in (THIS_HOME, ANOTHER_HOME):
                said["proof"] = home_gateway.gateway_proof(SECRET, nonce, PORT)
            elif self.answers == WRONG_PROOF:
                said["proof"] = home_gateway.gateway_proof("not this home's secret", nonce, PORT)
            return _Answer(json.dumps(said).encode())
        self.sent.append((url, dict(request.header_items())))
        return _Answer(b'{"ok": true}')


@pytest.mark.parametrize("answers", [ANOTHER_HOME, NO_PROOF, WRONG_PROOF])
def test_a_scripts_call_back_goes_only_to_a_gateway_that_proves_it(
    tmp_path, monkeypatch, answers
) -> None:
    ns = _launcher(tmp_path, monkeypatch)
    ns["_LOOPBACK"] = opener = _Opener(answers)

    answer = ns["_post"]("/api/send-message", {"text": "x"})

    assert (
        opener.sent == []
    ), "the credential went to a gateway that did not prove it is this home's"
    assert answer["ok"] is False and "not answered by this home's gateway" in json.dumps(answer)


def test_a_scripts_call_back_reaches_its_own_gateway_proved_each_time(
    tmp_path, monkeypatch
) -> None:
    ns = _launcher(tmp_path, monkeypatch)
    ns["_LOOPBACK"] = opener = _Opener(THIS_HOME)

    assert ns["_post"]("/api/send-message", {"text": "x"}) == {"ok": True}
    opener.answers = ANOTHER_HOME
    assert ns["_post"]("/api/send-message", {"text": "y"})["ok"] is False

    assert [url for url, _h in opener.sent] == [f"http://127.0.0.1:{PORT}/api/send-message"]


def test_the_gateway_hands_the_launcher_its_homes_id(home, monkeypatch) -> None:
    from personalclaw import schedule_script

    crons = home / "crons"
    crons.mkdir(parents=True, exist_ok=True)
    script = crons / "probe.py"
    script.write_text("def run(ctx):\n    return 'ok'\n", encoding="utf-8")
    written: dict[str, Any] = {}

    def _fake_run(argv, **_kw):
        for arg in argv:
            if "pc-cron-cfg-" in str(arg) and str(arg).endswith(".json"):
                written.update(json.loads(Path(str(arg)).read_text(encoding="utf-8")))
        raise AssertionError("stop before exec")

    monkeypatch.setattr(schedule_script.subprocess, "run", _fake_run)
    monkeypatch.setattr(schedule_script, "_crons_dir", lambda: crons)
    with pytest.raises(AssertionError, match="stop before exec"):
        schedule_script.run_script_sandboxed(f"{script}:run", "job", "", 5)

    assert written.get("home_id") == home_gateway.home_id(), written
