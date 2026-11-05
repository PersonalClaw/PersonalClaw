"""`web_fetch` names PersonalClaw to the sites it reads, and a page a site refused is a failed
fetch.

Ask the agent to read a Wikipedia article with `web_fetch` and Wikipedia answered with its refusal
("Please set a user-agent and respect our robot policy …"), which the tool returned as the page:
the card read completed, with the refusal under "Fetched page". Two defects met there:

1. No guarded request named PersonalClaw unless its caller set the header (`net.fetch()` passed
   only the caller's headers, and `web_fetch()` passed none), so aiohttp's own
   `Python/3.x aiohttp/…` went out, the generic identity Wikipedia's robot policy refuses. The
   synchronous download path (`net.open_url`) sent urllib's the same way.
2. `web_fetch()` never read the response status: a refusal, a 404 page, a rate limit or a server
   error was extracted and returned with `ok=True`, and recorded as a page the conversation had.

Every request `net.fetch` and `net.open_url` send, and the render's, now names PersonalClaw
(`net.client.USER_AGENT`) unless its caller sets its own, and a final answer outside 2xx is a
failed fetch naming the status, on the plain and the render path, with none of its body returned
and the page not recorded as seen.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest

from personalclaw.net import client as net_client
from personalclaw.net.client import FetchResponse
from personalclaw.web import fetch as wf

_REFUSAL = "<html><body><p>Please set a user-agent and respect our robot policy.</p></body></html>"
_PAGE = (
    "<html><head><title>Heron</title></head><body><article><p>"
    + "A heron is a wading bird. " * 20
    + "</p></article></body></html>"
)


@pytest.fixture(autouse=True)
def _fresh_provenance(monkeypatch):
    monkeypatch.setattr(wf, "_seen_by_session", {})


# ── the User-Agent ─────────────────────────────────────────────────────────────────────────────


class _Sent:
    """What the fake HTTP client was handed for each request."""

    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []


def _wire(monkeypatch) -> _Sent:
    sent = _Sent()

    class _Response:
        status = 200
        headers = {"Content-Type": "text/plain"}

        async def __aenter__(self) -> "_Response":
            return self

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

        @property
        def content(self) -> Any:
            class _Stream:
                @staticmethod
                async def iter_chunked(_n: int):
                    yield b"ok"

            return _Stream()

    class _Session:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

        def request(self, _method: str, _url: str, **kw: Any) -> _Response:
            sent.headers.append(dict(kw.get("headers") or {}))
            return _Response()

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    monkeypatch.setattr(aiohttp, "TCPConnector", lambda **_kw: None)
    monkeypatch.setattr(
        "personalclaw.sel.sel",
        lambda: type("S", (), {"log_api_access": staticmethod(lambda **_k: None)})(),
    )
    return sent


def _public(host: str) -> list[str]:
    if host != "en.wikipedia.example":
        raise socket.gaierror(host)
    return ["93.184.216.34"]


def _user_agent(headers: dict[str, str]) -> str:
    return next((v for k, v in headers.items() if k.lower() == "user-agent"), "")


def test_a_request_with_no_headers_names_personalclaw(monkeypatch):
    sent = _wire(monkeypatch)

    asyncio.run(net_client.fetch("https://en.wikipedia.example/wiki/Heron", resolver=_public))

    (headers,) = sent.headers
    assert "PersonalClaw;" in _user_agent(headers), headers
    assert _user_agent(headers) == net_client.USER_AGENT


def test_a_callers_own_user_agent_is_kept_whatever_its_case(monkeypatch):
    sent = _wire(monkeypatch)

    asyncio.run(
        net_client.fetch(
            "https://en.wikipedia.example/wiki/Heron",
            headers={"user-agent": "personalclaw-update-check", "Accept": "text/html"},
            resolver=_public,
        )
    )

    (headers,) = sent.headers
    assert [v for k, v in headers.items() if k.lower() == "user-agent"] == [
        "personalclaw-update-check"
    ]
    assert headers["Accept"] == "text/html"


def test_the_download_path_names_personalclaw_too(monkeypatch):
    opened: list[Any] = []

    class _Opener:
        addheaders: list[tuple[str, str]] = [("User-agent", "Python-urllib/3")]

        def open(self, url: str, timeout: float) -> Any:
            opened.append(dict(self.addheaders))
            return object()

    monkeypatch.setattr("urllib.request.build_opener", lambda *_handlers: _Opener())

    net_client.open_url("https://en.wikipedia.example/file", timeout_s=5)

    (headers,) = opened
    assert _user_agent(headers) == net_client.USER_AGENT, headers


def test_the_sdk_hands_apps_the_same_user_agent():
    from personalclaw.sdk import net as sdk_net

    assert sdk_net.USER_AGENT == net_client.USER_AGENT
    assert "USER_AGENT" in sdk_net.__all__


# ── an answer outside 2xx is a failed fetch ───────────────────────────────────────────────────


def _answer(
    monkeypatch, status: int, body: str, url: str = "https://en.wikipedia.example/wiki/Heron"
) -> None:
    async def _fake(_url: str, **_kw: Any) -> FetchResponse:
        return FetchResponse(
            url=url, status=status, headers={"Content-Type": "text/html"}, body=body.encode()
        )

    monkeypatch.setattr(wf, "net_fetch", _fake)


@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_an_error_status_is_a_failed_fetch_that_names_it(monkeypatch, status):
    _answer(monkeypatch, status, _REFUSAL)

    out = asyncio.run(
        wf.web_fetch(
            "https://en.wikipedia.example/wiki/Heron", require_provenance=False, session_key="s1"
        )
    )

    assert out.ok is False
    assert str(status) in out.error, out.error
    assert out.content == "" and "robot policy" not in out.error
    assert out.recovery_hints, "a way forward"
    assert (
        wf.url_provenance("s1", "https://en.wikipedia.example/wiki/Heron") == ""
    ), "recorded as seen"


@pytest.mark.parametrize(
    ("status", "said"), [(403, "refused"), (429, "refused"), (404, "returned HTTP 404")]
)
def test_a_refusal_says_the_site_refused(monkeypatch, status, said):
    _answer(monkeypatch, status, _REFUSAL)

    out = asyncio.run(
        wf.web_fetch("https://en.wikipedia.example/wiki/Heron", require_provenance=False)
    )

    assert said in out.error, out.error


def test_a_page_that_answers_200_is_still_the_page(monkeypatch):
    _answer(monkeypatch, 200, _PAGE)

    out = asyncio.run(
        wf.web_fetch(
            "https://en.wikipedia.example/wiki/Heron", require_provenance=False, session_key="s1"
        )
    )

    assert out.ok is True and "wading bird" in out.content
    assert wf.url_provenance("s1", "https://en.wikipedia.example/wiki/Heron")


@pytest.mark.parametrize(("status", "ok"), [(403, False), (500, False), (0, True), (200, True)])
def test_the_render_path_reads_the_status_too(monkeypatch, status, ok):
    from personalclaw.web import render as wr

    async def _render(url: str, **_kw: Any) -> Any:
        return wr.RenderResult(
            ok=True, url=url, html=_REFUSAL if status >= 400 else _PAGE, status=status
        )

    monkeypatch.setattr(wr, "render_url", _render)

    out = asyncio.run(
        wf.web_fetch(
            "https://en.wikipedia.example/wiki/Heron", require_provenance=False, render=True
        )
    )

    assert out.ok is ok, out.error
    if not ok:
        assert str(status) in out.error


def test_web_extract_spends_no_model_call_on_a_refused_page(monkeypatch):
    _answer(monkeypatch, 403, _REFUSAL)
    import personalclaw.llm_helpers as helpers

    asked: list[Any] = []

    async def _model(*a: Any, **k: Any) -> Any:
        asked.append(a)
        raise AssertionError("the model was asked to extract from a refusal")

    monkeypatch.setattr(helpers, "one_shot_completion", _model)

    out = asyncio.run(
        wf.web_extract(
            "https://en.wikipedia.example/wiki/Heron", "the bird's name", require_provenance=False
        )
    )

    assert out.ok is False and "403" in out.error
    assert asked == []
