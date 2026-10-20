"""The owner's consent question reaches the page as a question, not as a failed request.

🔴 THE DEFECT (measured on ``origin/main``). A write that needs the owner's yes, sent without it, is
answered with the question (`http_errors.consent_required`), and the page sends every such write
that way first on purpose: the gateway decides which writes need a yes. The answer was a ``400``,
so the browser logged every Allow the owner was asked for — an MCP server, a heartbeat task, what a
trigger runs — as a failed request, though nothing had failed.

The contract now: a client that says it asks (``X-PersonalClaw-Consent: ask``, which every write
the page sends carries) gets the question as a ``200`` marked ``X-PersonalClaw-Consent-Asked``.
Nothing is written either way, a client that does not say so keeps the ``400``, and the security
log records the refusal as it was made.

Driven through the whole dashboard (`start_dashboard`, its real middleware stack) with a real
queued heartbeat task.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import aiohttp
import pytest

TASK = "Water the plants"
ASK = {"X-PersonalClaw-Consent": "ask"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / ".personalclaw"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")
    import personalclaw.heartbeat as hb

    path = hb.ensure_heartbeat_file()
    path.write_text(path.read_text() + f"- {TASK}\n")
    return home


@pytest.mark.asyncio
async def test_the_allow_question_is_an_answer_to_a_page_that_asks(home):
    import personalclaw.heartbeat as hb
    from personalclaw.dashboard.server import start_dashboard

    runner, _state = await start_dashboard(sessions=MagicMock(count=0), port=0)
    try:
        host, port = runner.addresses[0][:2]
        url = f"http://{host}:{port}/api/heartbeat/tasks/allow"
        async with aiohttp.ClientSession() as http:
            async with http.post(url, json={"text": TASK}, headers=ASK) as resp:
                asked = (
                    resp.status,
                    resp.headers.get("X-PersonalClaw-Consent-Asked"),
                    await resp.json(),
                )
            still_waiting = not hb.allowed(TASK)
            async with http.post(url, json={"text": TASK}) as resp:
                refused = (
                    resp.status,
                    resp.headers.get("X-PersonalClaw-Consent-Asked"),
                    await resp.json(),
                )
            async with http.post(url, json={"text": TASK, "confirm": True}, headers=ASK) as resp:
                allowed = (resp.status, await resp.json())
    finally:
        await runner.cleanup()

    status, marker, body = asked
    assert (status, marker) == (200, "1"), asked
    assert body["error"]["code"] == "confirmation_required"
    assert body["error"]["detail"] == {
        "field": "heartbeat_task",
        "consent": hb.consent(TASK),
        "title": hb.CONSENT_TITLE,
    }
    assert still_waiting, "the question allowed nothing"
    assert refused == (400, None, body), "a client that does not ask is refused as before"
    assert allowed == (200, {"ok": True})
    assert hb.allowed(TASK)


@pytest.mark.asyncio
async def test_only_the_consent_question_is_answered_that_way(home):
    """Any other refusal keeps its status for a page that asks: a task that is not queued is 404."""
    from personalclaw.dashboard.server import start_dashboard

    runner, _state = await start_dashboard(sessions=MagicMock(count=0), port=0)
    try:
        host, port = runner.addresses[0][:2]
        url = f"http://{host}:{port}/api/heartbeat/tasks/allow"
        async with aiohttp.ClientSession() as http:
            async with http.post(url, json={"text": "never queued"}, headers=ASK) as resp:
                status, marker = resp.status, resp.headers.get("X-PersonalClaw-Consent-Asked")
    finally:
        await runner.cleanup()
    assert (status, marker) == (404, None)
