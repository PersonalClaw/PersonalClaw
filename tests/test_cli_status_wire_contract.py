"""#2903: every counter `personalclaw status` prints must exist in the real `/api/status`.

THE BUG. `_status` read three top-level keys the endpoint has never emitted —
`data.get('crons', 0)`, `data.get('messages', 0)`, `data.get('tool_calls', 0)` — so three
of its seven lines were hardwired to `0`. A home with five system schedules printed
`Cron jobs: 0` while the same payload carried `cron.total == 5`, the dashboard rail showed
5, and `personalclaw cron list` listed all five. `messages` / `tool_calls` could never be
non-zero at all: `status_snapshot` dropped its writerless `messages` counter (see
`test_dashboard_status_snapshot.py::test_the_snapshot_makes_no_unmeasured_claim`) and
neither key has a producer anywhere in the payload.

Every assertion here is driven against a REAL `/api/status` response from a real seeded
home, then fed to the real CLI reader — so a rename on EITHER side of the wire fails this
test instead of silently zeroing a line. A test that hand-builds the payload it then reads
cannot see this class of bug; that is precisely how the old `crons`-shaped fixture in
`test_cli.py::test_status_success` kept passing while the surface printed nonsense.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.cli_server import _STATUS_LINES, _status
from personalclaw.dashboard.state import DashboardState


@pytest.fixture
def home(tmp_path, monkeypatch):
    """One isolated home behind every seam the status payload reads.

    Production resolves the trigger store, the hook/event stores and `status_snapshot`'s
    own `trigger_counts()` to a single `config_dir()`; in tests they default to different
    tmp homes. Pinning them here is what makes the seeded schedules show up in the `cron`
    block rather than in one of three empty worlds (same reasoning as
    `test_dashboard_triggers_metric.py`).
    """
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: h)
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.triggers.config_dir", lambda: h, raising=False
    )
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: h, raising=False)
    import personalclaw.event_triggers as et
    from personalclaw.hooks import set_global_hook_store

    et._engine = None
    try:
        yield h
    finally:
        et._engine = None
        set_global_hook_store(None)


def _seed_schedule(home, trigger_id: str) -> None:
    """A real clock schedule, written to the unified store the way the runtime does."""
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=trigger_id,
            name=trigger_id,
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "every_secs": 3600},
        )
    )


def _real_status_payload(monkeypatch) -> dict:
    """The bytes `GET /api/status` actually serves, from the real handler."""
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard import handlers_system
    from personalclaw.dashboard.handlers import updates as _updates_mod

    # Skip the background update-recheck so the probe stays a pure read.
    monkeypatch.setattr(_updates_mod, "_last_update_check", time.time())

    state = DashboardState(
        sessions=MagicMock(count=2),
        start_time=time.time() - 120,
        subagents=MagicMock(count=1),
        context_builder=None,
    )
    state._owner_hash = "test-hash"  # avoid the owner-hash executor round trip
    app = web.Application()
    app["state"] = state
    req = make_mocked_request("GET", "/api/status", app=app)
    req["user"] = "tester"
    resp = asyncio.run(handlers_system.api_status(req))
    return json.loads(resp.body.decode())


def _run_status(payload: dict) -> str:
    """Drive the real CLI reader over *payload* and return what it printed."""
    import io
    from contextlib import redirect_stdout

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(payload).encode()
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    buf = io.StringIO()
    with patch("urllib.request.urlopen", return_value=mock_resp), redirect_stdout(buf):
        _status(argparse.Namespace(port=7777))
    return buf.getvalue()


def test_the_cron_line_reports_the_seeded_count(home, monkeypatch) -> None:
    """🔴 THE BUG, end to end: five schedules in the store must print as five.

    Before the fix this read `data['crons']`, a key no payload has ever had, and printed
    `Cron jobs:   0` against this exact payload.
    """
    for i in range(5):
        _seed_schedule(home, f"clock:{i}")

    payload = _real_status_payload(monkeypatch)
    assert payload["cron"]["total"] == 5, "precondition: the emitter counts the seeded schedules"

    out = _run_status(payload)
    assert "Cron jobs:   5" in out
    assert "Cron jobs:   0" not in out


def test_every_key_path_the_cli_reads_exists_in_the_real_payload(home, monkeypatch) -> None:
    """The drift pin, in both directions.

    Walks `_STATUS_LINES` — the table the printer itself iterates — against the real
    response. A key renamed or dropped on the emitter side, or a path typo'd on the reader
    side, fails here; that is the check nothing performed when the payload lost `messages`
    and `crons` became the `cron` block.
    """
    _seed_schedule(home, "clock:a")
    payload = _real_status_payload(monkeypatch)

    missing = []
    for label, path in _STATUS_LINES:
        cur: object = payload
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                missing.append((label, ".".join(path)))
                break
            cur = cur[key]
    assert not missing, f"status lines reading key paths /api/status does not emit: {missing}"

    # …and the rendered surface therefore carries no unknowns.
    assert "—" not in _run_status(payload)


def test_no_line_claims_a_count_the_payload_cannot_produce(home, monkeypatch) -> None:
    """The two writerless lines are GONE, not defaulted to 0.

    `messages` / `tool_calls` have no producer in the payload — `stats` carries
    `total_turns` and the token counters, neither of these — and `stats.py`'s standing rule
    is that a counter without a call site is removed rather than surfaced. Printing them as
    a permanent `0` made a busy gateway look idle.
    """
    payload = _real_status_payload(monkeypatch)
    for key in ("crons", "messages", "tool_calls"):
        assert key not in payload, f"the emitter does not carry {key!r}"

    out = _run_status(payload)
    assert "Messages" not in out
    assert "Tool calls" not in out
    # The honest activity signal that replaced them IS measured (`stats.inc_turns`).
    assert f"Turns:       {payload['stats']['total_turns']}" in out


def test_a_missing_key_renders_as_a_dash_not_a_zero(home, monkeypatch) -> None:
    """The anti-fabrication rail itself: absence must not print as a measurement.

    A `0` here is indistinguishable from a genuinely idle gateway, which is the whole
    reason #2903 went unnoticed. Drops the `cron` block from an otherwise real payload.
    """
    payload = _real_status_payload(monkeypatch)
    payload.pop("cron")

    out = _run_status(payload)
    assert "Cron jobs:   —" in out
    assert "Cron jobs:   0" not in out
