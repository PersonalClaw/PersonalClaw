"""A default quiet window the scheduler cannot read is refused when it is saved.

Settings → Workflows → "Default quiet hours" took ``10pm-7am`` with a 200. The PATCH allowlist
checked only that the value was a string of at most 64 characters, while the scheduler reads the
field with ``triggers.calendar.parse_default_window``, which could not parse it and quietly treated
it as NO default. So the field showed a window, the save said it worked, and no automation was
ever held.

The fix makes the two readings one function: the write validates with the parser the scheduler
uses, and the refusal names the form it takes.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.triggers import calendar

FIELD = "workflows.default_quiet_windows"


def _make_app() -> web.Application:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


@pytest.fixture
def tmp_config(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    with patch("personalclaw.config.loader.config_path", return_value=cfg_path):
        yield cfg_path


def _stored(cfg_path) -> str:
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    return str((data.get("workflows") or {}).get("default_quiet_windows", ""))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "typed",
    [
        "10pm-7am",  # the validator's value: 12-hour times
        "22:00",  # one time, not a window
        "25:00-08:00",  # an hour that does not exist
        "22:00-22:00",  # starts and ends together: covers no time
        "garbage",
    ],
)
async def test_a_window_the_scheduler_cannot_read_is_refused(tmp_config, typed) -> None:
    async with TestClient(TestServer(_make_app())) as c:
        resp = await c.patch("/api/config/personalclaw", json={"path": FIELD, "value": typed})
        body = await resp.json()
    assert resp.status == 400, body
    # The refusal says which form works, with an example, so the Settings toast is actionable.
    assert "HH:MM-HH:MM" in body["error"] and "22:00-07:00" in body["error"], body
    assert _stored(tmp_config) == "", "a refused window must not reach config.json"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "typed,start,end",
    [
        ("22:00-07:00", "22:00", "07:00"),
        (" 22:00 to 07:00 ", "22:00", "07:00"),
        ("09:00–17:00", "09:00", "17:00"),  # en dash
    ],
)
async def test_a_window_that_saves_is_the_window_in_effect(tmp_config, typed, start, end) -> None:
    async with TestClient(TestServer(_make_app())) as c:
        resp = await c.patch("/api/config/personalclaw", json={"path": FIELD, "value": typed})
    assert resp.status == 200
    assert _stored(tmp_config) == typed.strip()
    applied = calendar.default_quiet_window()
    assert applied is not None and (applied.start, applied.end) == (start, end)


@pytest.mark.asyncio
async def test_clearing_the_field_still_saves_and_means_no_default(tmp_config) -> None:
    async with TestClient(TestServer(_make_app())) as c:
        resp = await c.patch("/api/config/personalclaw", json={"path": FIELD, "value": ""})
    assert resp.status == 200
    assert calendar.default_quiet_window() is None


@pytest.mark.asyncio
async def test_the_validator_is_the_parser(tmp_config, monkeypatch) -> None:
    """One function, not two that agree today. Break the parser and the WRITE must follow it."""

    def _refuse_everything(value):
        raise ValueError("the parser said no")

    monkeypatch.setattr(calendar, "parse_default_window", _refuse_everything)
    async with TestClient(TestServer(_make_app())) as c:
        resp = await c.patch(
            "/api/config/personalclaw", json={"path": FIELD, "value": "22:00-07:00"}
        )
        body = await resp.json()
    assert resp.status == 400
    assert body["error"] == "the parser said no"


def test_a_value_stored_before_validation_still_reads_as_no_default(tmp_config) -> None:
    """A hand edit, or a value saved by an older build, is not a crash: the scheduler reads NO
    default — a malformed window that matched all day would look like a broken scheduler."""
    tmp_config.write_text(json.dumps({"workflows": {FIELD.split(".")[1]: "10pm-7am"}}))
    assert calendar.default_quiet_window() is None
