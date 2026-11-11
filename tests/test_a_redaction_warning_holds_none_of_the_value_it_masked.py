"""A redaction warning names the kind of what was masked, and at most its length or a scheme or
host the masked text keeps, never any of the value.

The mask and the URL check each give a warning for what they take, and a caller may log it, as the
chat runner does for a segment it settles. Each value here is built when the test runs from one
body, and no four characters of that body in a row may be in a warning, or in ``gateway.log`` or
the settled segment once a chat segment holding the value is settled with the gateway's own log
handler attached.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.cli import _gateway_log_handler
from personalclaw.dashboard.chat_runner import _flush_segment
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.security import redact_credentials, redact_exfiltration_urls

pytestmark = pytest.mark.security

BODY = "Tq7Wn3Lp9Xv2Ks5Rd8FgHj4MzC6bY1eQ0aUo"

#: Each value, its parts joined at run time, so the source holds no value a scanner would report.
VALUES = {
    "an anthropic key": "sk-" + "ant-api03-" + BODY,
    "an openai project key": "sk-" + "proj-" + BODY,
    "a github token": "gh" + "p_" + BODY,
    "a github fine-grained token": "github" + "_pat_" + BODY[:22] + "_" + BODY,
    "a hugging face token": "h" + "f_" + BODY,
    "a google key": "AI" + "za" + BODY[:35],
    "a bearer token": "Authorization: Bearer " + BODY,
    "a password": "password: " + BODY[:14],
    "a client secret": "client_secret=" + BODY[:12],
    "a long url query": "https://collector.example/c?d=" + BODY * 7,
}

DASHES = "-" * 5
KEY_BLOCK = f"{DASHES}BEGIN PRIVATE KEY{DASHES}\n{BODY}\n{BODY}\n{DASHES}END PRIVATE KEY{DASHES}"

#: The mask's other kinds, each a value of its own, in the warnings only.
OTHER_KINDS = {
    "a url login": "https://ada:" + BODY[:12] + "@git.example.com/r.git",
    "an scp login": "ada:" + BODY[:12] + "@backup.example.com:srv",
    "a webhook": "https://hooks.example.com/services/" + BODY,
    "a key block": KEY_BLOCK,
    "a base64 chunk": base64.b64encode(("api_key=" + BODY).encode()).decode(),
    "a credential-like query": "https://collector.example/c?d=" + BODY + BODY[:10],
    "a quoted value": '{"password": "' + BODY + '"}',
}


def _runs_of_the_body(text: str) -> list[str]:
    return [BODY[i : i + 4] for i in range(len(BODY) - 3) if BODY[i : i + 4] in text]


@pytest.mark.parametrize(
    "value", list((VALUES | OTHER_KINDS).values()), ids=list(VALUES | OTHER_KINDS)
)
def test_no_warning_holds_any_of_what_it_masked(value: str) -> None:
    text = f"here it is: {value} ok"
    warnings = redact_credentials(text)[1] + redact_exfiltration_urls(text)[1]
    assert warnings, "the value was not masked, so the case proves nothing"
    assert not _runs_of_the_body(" ".join(warnings)), warnings


def test_a_pattern_warning_is_its_kind_alone() -> None:
    _, warnings = redact_credentials("api_key=" + BODY[:12])
    assert warnings == ["Redacted credential pattern"]


def test_a_long_query_warning_is_its_kind_its_length_and_its_host() -> None:
    _, warnings = redact_exfiltration_urls("see " + VALUES["a long url query"] + " now")
    assert warnings == ["Suspicious URL with long query params (254 chars): collector.example"]


def _state(tmp_path: Path) -> DashboardState:
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.remove = AsyncMock()
    return DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )


def test_a_settled_chat_segment_leaves_none_of_a_value_in_gateway_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    log_file = tmp_path / "gateway.log"
    handler = _gateway_log_handler(log_file)
    logger = logging.getLogger("personalclaw.dashboard.chat_runner")
    logger.addHandler(handler)
    try:
        state = _state(tmp_path)
        state.broadcast_ws = lambda *_: None  # type: ignore[method-assign]
        session = state.get_or_create_session("warnings")
        text = "\n".join(VALUES.values())
        session.stream_chunk(text)
        _flush_segment(state, session, text)
        segment = session.messages[-1]["content"]
    finally:
        logger.removeHandler(handler)
        handler.close()
    written = log_file.read_text(encoding="utf-8")
    assert written.count("redacted in chat segment") >= len(VALUES), written
    assert not _runs_of_the_body(written), written
    assert not _runs_of_the_body(segment), segment
