"""``spawn run``'s wait for its subagent ends, and says why, when the agent or the gateway is gone.

The blocking form polled ``GET /api/spawn/<id>`` every two seconds and read every failed poll as
"lost connection to gateway": an agent the gateway no longer held (404), a gateway that stopped
answering, and the hour its sign-in lasts running out while the agent still worked all said the
same, and only the second was true. Now:

* an agent the gateway no longer holds ends the wait, saying so, exit 1;
* a gateway that stopped answering, or broke off its answer, ends it with the sentence every client
  command gives;
* a sign-in the gateway refuses is made again once, and the wait goes on; refused again, the wait
  ends saying the sign-in was refused;
* a poll that times out is asked again, and the wait ends, saying so, once the gateway has not
  answered for 30 seconds: a busy gateway is not a gone one. An answer between starts the count
  again, and the wait sleeps between polls only after an answer;
* an answer that is not a JSON object, or an HTTP status it does not expect, ends the wait saying
  so;
* a start or a list the gateway does not answer within 5 seconds ends saying so, and a start adds
  that the subagent may have started.
"""

from __future__ import annotations

import argparse
import urllib.error
from pathlib import Path

import pytest
from aiohttp import web
from fakes import gateway_stand_in

from personalclaw import cli_commands

_TOKENS = ["first-token", "second-token"]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unset_env) -> Path:
    unset_env("PERSONALCLAW_PORT", "HTTP_PROXY", "http_proxy")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    (home / ".local_secret").write_text("test-local-secret", encoding="utf-8")
    monkeypatch.setattr(cli_commands._time, "sleep", lambda _s: None)
    return home


def _routes(home: Path, poll) -> dict:
    """A gateway that mints a new token on each sign-in and answers the agent's poll with *poll*,
    given the token the poll carried."""
    secret = (home / ".local_secret").read_text(encoding="utf-8").strip()
    minted: list[str] = []

    async def token_local(request: web.Request) -> web.Response:
        if request.headers.get("X-Local-Secret") != secret:
            return web.json_response({"error": "Forbidden"}, status=403)
        minted.append(_TOKENS[len(minted)])
        return web.json_response({"token": minted[-1]})

    async def spawn(request: web.Request) -> web.Response:
        return web.json_response({"id": "agent-9", "task": "probe"})

    async def status(request: web.Request) -> web.Response:
        token = str(request.headers.get("Authorization", "")).removeprefix("Bearer ")
        code, body = poll(token)
        return web.json_response(body, status=code)

    return {
        ("GET", "/api/token/local"): token_local,
        ("POST", "/api/spawn"): spawn,
        ("GET", "/api/spawn/agent-9"): status,
    }


def _run() -> argparse.Namespace:
    return argparse.Namespace(spawn_action="run", port=None, task="probe", fire_and_forget=False)


def test_an_agent_the_gateway_no_longer_holds_ends_the_wait_saying_so(home, capsys):
    with gateway_stand_in(home, routes=_routes(home, lambda t: (404, {"error": "no"}))) as gw:
        gw.record()
        with pytest.raises(SystemExit) as stop:
            cli_commands._spawn(_run())
    out, err = capsys.readouterr()
    assert (stop.value.code, out) == (1, "")
    assert "agent-9 is no longer on this home's gateway" in err
    assert "lost connection" not in err


def test_a_gateway_that_stops_answering_ends_the_wait_with_its_sentence(home, capsys, monkeypatch):
    """The gateway answers the start and stops answering the polls, as one that went away does."""
    import urllib.error

    from personalclaw import home_gateway

    real = home_gateway.open_loopback

    def gone_for_the_poll(request, *args, **kwargs):
        if request.full_url.endswith("/api/spawn/agent-9"):
            raise urllib.error.URLError("[Errno 61] Connection refused")
        return real(request, *args, **kwargs)

    with gateway_stand_in(home, routes=_routes(home, lambda t: (200, {"done": False}))) as gw:
        gw.record()
        said = home_gateway.reach(None).gone()
        monkeypatch.setattr(home_gateway, "open_loopback", gone_for_the_poll)
        with pytest.raises(SystemExit) as stop:
            cli_commands._spawn(_run())
    out, err = capsys.readouterr()
    assert (stop.value.code, out) == (1, "")
    assert said in err
    assert "lost connection" not in err


def test_a_sign_in_that_ran_out_is_made_again_and_the_wait_goes_on(home, capsys):
    def poll(token: str):
        if token == _TOKENS[0]:
            return 401, {"error": "token expired"}
        return 200, {"done": True, "result": "the subagent's answer"}

    with gateway_stand_in(home, routes=_routes(home, poll)) as gw:
        gw.record()
        cli_commands._spawn(_run())
    out, _err = capsys.readouterr()
    assert out == "the subagent's answer\n"


def _polls_raise(monkeypatch, raised) -> None:
    """The agent's polls raise what ``raised()`` gives (``None`` to let one through); the gateway
    answers the sign-in and the start."""
    from personalclaw import home_gateway

    real = home_gateway.open_loopback

    def poll(request, *args, **kwargs):
        if request.full_url.endswith("/api/spawn/agent-9"):
            exc = raised()
            if exc is not None:
                raise exc
        return real(request, *args, **kwargs)

    monkeypatch.setattr(home_gateway, "open_loopback", poll)


#: A gateway too slow to answer: a read that timed out, and a connect that did.
_TIMEOUTS = pytest.mark.parametrize(
    "timeout",
    [TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out"))],
    ids=["while-reading", "while-connecting"],
)


@_TIMEOUTS
def test_a_poll_that_times_out_is_asked_again(home, capsys, monkeypatch, timeout):
    timeouts = [timeout]
    done = (200, {"done": True, "result": "the subagent's answer"})
    with gateway_stand_in(home, routes=_routes(home, lambda t: done)) as gw:
        gw.record()
        _polls_raise(monkeypatch, lambda: timeouts.pop() if timeouts else None)
        cli_commands._spawn(_run())
    out, err = capsys.readouterr()
    assert out == "the subagent's answer\n"
    assert "No gateway is running" not in err


def test_a_gateway_that_never_answers_a_poll_ends_the_wait_saying_so(home, capsys, monkeypatch):
    """Six polls of 5 seconds each, and no sleep after one that timed out: it waited already, so
    the wait ends 30 seconds after the gateway last answered."""
    slept: list[float] = []
    monkeypatch.setattr(cli_commands._time, "sleep", slept.append)
    with gateway_stand_in(home, routes=_routes(home, lambda t: (200, {"done": False}))) as gw:
        gw.record()
        gone = _gone()
        _polls_raise(monkeypatch, lambda: TimeoutError("timed out"))
        with pytest.raises(SystemExit) as stop:
            cli_commands._spawn(_run())
    out, err = capsys.readouterr()
    assert (stop.value.code, out) == (1, "")
    assert "did not answer for subagent agent-9 within 30 seconds" in err
    assert gone not in err
    assert slept == [2], "a sleep only before the first poll, the one after an answer"


def test_an_answer_between_timeouts_starts_their_count_again(home, capsys, monkeypatch):
    """Five polls that time out, an answer that the agent still works, five more, then its result:
    never six in a row, so the wait goes on to the result."""
    answers = iter([(200, {"done": False}), (200, {"done": True, "result": "the answer"})])
    late = [TimeoutError("timed out")] * 5
    polls = [*late, None, *late, None]
    with gateway_stand_in(home, routes=_routes(home, lambda t: next(answers))) as gw:
        gw.record()
        _polls_raise(monkeypatch, lambda: polls.pop(0))
        cli_commands._spawn(_run())
    out, err = capsys.readouterr()
    assert (out, polls) == ("the answer\n", [])
    assert "did not answer" not in err


def _poll_answers(home: Path, response) -> dict:
    """The gateway's routes, answering the agent's poll with what *response* () makes."""
    routes = _routes(home, lambda t: (200, {}))

    async def status(request: web.Request) -> web.Response:
        return response()

    return {**routes, ("GET", "/api/spawn/agent-9"): status}


@pytest.mark.parametrize(
    "response, said",
    [
        (
            lambda: web.json_response(None),
            "answered for subagent agent-9 with something that is not a JSON object.",
        ),
        (
            lambda: web.Response(text="<html>busy</html>", content_type="text/html"),
            "answered for subagent agent-9 with something that is not JSON.",
        ),
        (
            lambda: web.json_response({"error": "boom"}, status=500),
            "answered HTTP 500 for subagent agent-9: Internal Server Error.",
        ),
    ],
    ids=["null", "not-json", "http-500"],
)
def test_an_answer_the_wait_cannot_read_ends_it_saying_so(home, capsys, response, said):
    with gateway_stand_in(home, routes=_poll_answers(home, response)) as gw:
        gw.record()
        with pytest.raises(SystemExit) as stop:
            cli_commands._spawn(_run())
    out, err = capsys.readouterr()
    assert (stop.value.code, out) == (1, "")
    assert said in err, err


def _spawn_raises(monkeypatch, method: str, raised: BaseException) -> None:
    """``method`` ``/api/spawn`` (the list, or the start) raises *raised*; the sign-in goes on."""
    from personalclaw import home_gateway

    real = home_gateway.open_loopback

    def call(request, *args, **kwargs):
        if request.full_url.endswith("/api/spawn") and request.get_method() == method:
            raise raised
        return real(request, *args, **kwargs)

    monkeypatch.setattr(home_gateway, "open_loopback", call)


@_TIMEOUTS
@pytest.mark.parametrize(
    "args, method, said",
    [
        (
            argparse.Namespace(spawn_action="list", port=None, as_json=False),
            "GET",
            "did not answer within 5 seconds.",
        ),
        (
            _run(),
            "POST",
            "did not answer within 5 seconds, so the subagent may have started: "
            "`personalclaw spawn list` shows it.",
        ),
    ],
    ids=["list", "start"],
)
def test_a_list_or_a_start_the_gateway_does_not_answer_in_time_says_so(
    home, capsys, monkeypatch, timeout, args, method, said
):
    with gateway_stand_in(home, routes=_routes(home, lambda t: (200, {"done": False}))) as gw:
        gw.record()
        gone = _gone()
        _spawn_raises(monkeypatch, method, timeout)
        with pytest.raises(SystemExit) as stop:
            cli_commands._spawn(args)
    out, err = capsys.readouterr()
    assert (stop.value.code, out) == (1, "")
    assert f"Error: this home's gateway {said}" in err, err
    assert gone not in err


def test_an_answer_broken_off_ends_the_wait_with_the_gone_sentence(home, capsys, monkeypatch):
    import http.client

    with gateway_stand_in(home, routes=_routes(home, lambda t: (200, {"done": False}))) as gw:
        gw.record()
        gone = _gone()
        _polls_raise(monkeypatch, lambda: http.client.IncompleteRead(b"{"))
        with pytest.raises(SystemExit) as stop:
            cli_commands._spawn(_run())
    out, err = capsys.readouterr()
    assert (stop.value.code, out) == (1, "")
    assert gone in err
    assert "Traceback" not in err


def test_a_sign_in_refused_twice_ends_the_wait_saying_so(home, capsys):
    with gateway_stand_in(home, routes=_routes(home, lambda t: (401, {"error": "no"}))) as gw:
        gw.record()
        with pytest.raises(SystemExit) as stop:
            cli_commands._spawn(_run())
    out, err = capsys.readouterr()
    assert (stop.value.code, out) == (1, "")
    assert "refused this command's sign-in for agent-9" in err


def _gone() -> str:
    """What a command says once this home's gateway has stopped answering since it was reached."""
    from personalclaw import home_gateway

    return home_gateway.reach(None).gone()
