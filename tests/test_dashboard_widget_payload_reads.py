"""Every field a dashboard widget consumes must exist in the response its endpoint really sends.

🔴 THE DEFECT CLASS THIS CLOSES (issue 466). The dashboard's Recent-activity widget read a
response contract the server had stopped sending. Not a drifted field — a **wholly dead contract**:
all eight names it consumed (`job_name`, `job_id`, `status`, `trigger`, `duration_ms`, `summary`,
`error`, `trace`) were absent from `/api/triggers/history`'s default shape, which means the widget
was never once executed against the live endpoint.

Both sides moved, and the direction matters. `git log -S job_name` puts the widget's reads at the
initial public commit (2026-07-19); `git log -S unified_feed` puts the endpoint's re-point at S84
(`94d5291d3`, 2026-08-03), whose predecessor returned `_redact_run(r, job_name=...)` — the legacy
`ScheduleRun` dicts, `job_name` included. So the **endpoint changed underneath the widget**, and the
widget's reads were simply never updated. Measured on a live gateway with five real fires:

    Recent activity
      Schedule   ran      4m ago
      Schedule   failed   4m ago
      Schedule   failed   5m ago
      Schedule   failed   5m ago
      Schedule   failed   5m ago

Five different automations, five identical labels, and four of the five sharing ONE accessible name
("Schedule — failed"). The endpoint knew every one of their names.

🔑 SO THE RAIL DERIVES BOTH SIDES, AND HAND-WRITES NEITHER. The sent set comes from driving the
**real route** through a real `aiohttp` app over real stores — the same fixture shape
`test_triggers_history.py` uses — and walking the response for leaf field names. The consumed set
comes from parsing the widget's **own source** for the reads it makes off a run row. A field added
to one side without the other reds here.

Why a rail rather than a stricter type: `ScheduleRun` in `web/src/lib/api.ts` describes TWO
endpoints that genuinely disagree (the per-trigger route really does send `status`/`job_name`; the
cross-trigger one really does send `outcome`/`trigger_id`), and every field on it is optional. That
is precisely what let an 8-of-8 mismatch compile silently for a month, and no single interface can
be honest about both. The check has to be per-consumer, against the endpoint that consumer calls.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import triggers as T
from personalclaw.event_triggers import MEMORY_UPDATE, event_spec
from personalclaw.hooks import ScriptHookStore
from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

REPO = pathlib.Path(__file__).resolve().parents[1]
WIDGET = REPO / "web" / "src" / "pages" / "dashboard" / "widgets" / "ScheduleWidget.tsx"
#: The shared partition helper the widget hands its rows to. Its reads are the widget's reads.
FOLD = REPO / "web" / "src" / "pages" / "dashboard" / "widgets" / "scheduleFold.ts"

NOW = time.time()

#: Reads that resolve on something OTHER than a run row, with the reason each is not a wire field.
#:
#: EMPTY, and measured that way rather than assumed: the extraction below is anchored on the exact
#: binding `r.`, so the neighbouring non-row reads never enter the set in the first place —
#: `runs.filter`, `visible.slice`, `did.has` and `o.tone`/`o.label` (a local `statusMeta()`
#: derivation) all bind a different name. An exemption list that is empty because it is unnecessary
#: is stronger than one populated to paper over a loose regex, and
#: `test_no_exemption_outlives_its_read` keeps any future entry honest in both directions.
NOT_WIRE_FIELDS: dict[str, str] = {}


# ── the sent side: the REAL route, over real stores ───────────────────────────


@pytest.fixture
def history_app(tmp_path, monkeypatch):
    """A real app on the real route, with every source of the feed contributing rows.

    Seeded so no branch of the response is empty: an empty `runs` list derives no row field names
    at all, which would let this census pass while checking nothing. Both home seams are redirected
    for the reason `test_triggers_history.py` records — conftest's autouse `_isolate_trigger_store`
    has already pointed `T.config_dir` at a different tmp dir, and last-wins is the documented way
    to override it.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(T, "config_dir", lambda: pathlib.Path(tmp_path))
    cfg = pathlib.Path(tmp_path)

    hooks = ScriptHookStore(config_dir=cfg)
    ran = hooks.create({"name": "Formatter", "event": "PostToolUse", "provider": "run-prompt"})
    live = hooks.get(ran.id)
    # `update()`'s allowlist is CONFIG fields only; the runtime fields are written by `_fire`.
    live.run_count, live.last_run, live.last_status = 7, NOW - 300, "ok"
    hooks._save()

    # The NAME JOIN's real source. `_trigger_names` reads the unified TriggerStore, not
    # `state.crons` — S110 made it store-only — so a fixture that only faked `list_jobs` would leave
    # every schedule row nameless and this rail would "pass" against a blank it created itself.
    # Measured: that is exactly what happened on the first run of this file.
    TriggerStore(base_dir=cfg).upsert(
        Trigger(
            id="j1",
            name="Nightly digest",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 3600},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    # A data-event trigger is a row in the same store, and its fires are ordinary rows in the same
    # run ledger a cron's are — so it contributes a run (`r3`) like any store trigger.
    TriggerStore(base_dir=cfg).upsert(
        Trigger(
            id="event:memory-watcher",
            name="Memory watcher",
            kind="event",
            enabled=True,
            spec=event_spec(MEMORY_UPDATE),
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )

    runs = ScheduleRunStore(cfg)
    for row in (
        {
            "run_id": "r1",
            "job_id": "j1",
            "trigger": "schedule",
            "started_at": NOW - 120,
            "finished_at": NOW - 110,
            "duration_ms": 10_000,
            "status": "success",
            "summary": "all good",
            "error": "",
        },
        {
            "run_id": "r2",
            "job_id": "j1",
            "trigger": "manual",
            "started_at": NOW - 30,
            "finished_at": NOW - 20,
            "duration_ms": 500,
            "status": "failure",
            "summary": "",
            "error": "boom",
        },
        {
            "run_id": "r3",
            "job_id": "event:memory-watcher",
            "trigger": "ok",
            "started_at": NOW - 600,
            "finished_at": NOW - 600,
            "duration_ms": 0,
            "status": "success",
            "summary": "",
            "error": "",
        },
    ):
        asyncio.run(runs.append(ScheduleRun.from_dict(row)))

    app = web.Application()

    class _State:
        pass

    state = _State()
    state._hook_store = hooks
    state._sessions = {}
    app["state"] = state
    T.register_trigger_routes(app)
    return app


def _get(app, path):
    async def _run_it():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(path)
            return resp.status, await resp.json()

    return asyncio.run(_run_it())


@pytest.fixture
def sent(history_app) -> dict:
    """The DEFAULT response of the endpoint the widget actually calls.

    No `shape=` parameter, exactly as `api.triggersHistory` requests it — the handler's own
    docstring anticipated a caller that omits it, and this widget is that caller.
    """
    status, body = _get(history_app, "/api/triggers/history?limit=12&offset=0")
    assert status == 200, f"the route did not answer 200: {body}"
    return body


def _row_fields(body: dict) -> set[str]:
    """Every field name carried by a row of the feed, unioned across ALL rows.

    Unioned rather than taken from the first row so a name only some kinds carry is still derived —
    the three projections differ, which is the whole reason one shared type went wrong.
    """
    return {key for row in body.get("runs", []) for key in row}


# ── the consumed side: the WIDGET'S OWN SOURCE ────────────────────────────────

#: The row callback binds the run to `r`, and the fold helper binds it to `r` too. So a read off a
#: run row is spelled `r.<name>` in both files, and that is what this extracts. Deliberately a parse
#: of the real source rather than a maintained list: a hand-written list of the fields the widget
#: reads is the artefact that goes stale, and going stale IS the defect.
_READ = re.compile(r"\br\.([A-Za-z_][A-Za-z0-9_]*)")


def _consumed_fields() -> set[str]:
    source = WIDGET.read_text(encoding="utf-8") + "\n" + FOLD.read_text(encoding="utf-8")
    # Strip comments first: this file's own 🔴 notes name the dead fields (`job_name`, `status`) on
    # purpose, and a census that counted prose as a read could never go green.
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"^\s*//.*$", "", source, flags=re.M)
    source = re.sub(r"(?<![:\w])//[^\n]*$", "", source, flags=re.M)
    return {m.group(1) for m in _READ.finditer(source)} - set(NOT_WIRE_FIELDS)


# ── non-vacuity, first ────────────────────────────────────────────────────────


def test_the_seeded_feed_carries_every_kind(sent):
    """A census over an empty feed is a passing test that checks nothing."""
    assert sent["runs"], "no rows derived — the seeding no longer reaches the endpoint"
    assert sent["kinds"] == ["lifecycle", "schedule"], (
        f"not every source projected: {sent['kinds']} — the derived field set would be "
        "missing whichever source dropped out"
    )
    assert "Memory watcher" in {
        r["trigger_name"] for r in sent["runs"]
    }, "the data-event trigger's run is missing — its fires share the ledger a cron's use"
    fields = _row_fields(sent)
    # Named so a walk that stopped short cannot pass.
    for name in ("id", "trigger_id", "trigger_name", "outcome", "reason", "finished_at"):
        assert name in fields, f"{name} not derived from the live response"


def test_the_widget_reads_are_extracted_from_its_source():
    """The consumed side must be non-empty and must include the reads the widget visibly makes."""
    consumed = _consumed_fields()
    assert len(consumed) >= 5, f"only {consumed} extracted — did the row callback stop binding `r`?"
    for name in ("outcome", "finished_at", "trigger_name", "reason"):
        assert (
            name in consumed
        ), f"{name} not extracted from {WIDGET.name} — is the parse still real?"


# ── the rail ──────────────────────────────────────────────────────────────────


def test_every_field_the_widget_reads_is_sent_by_the_endpoint(sent):
    """The rail itself. This is the assertion that would have caught issue 466 on the commit that
    re-pointed the endpoint, instead of a month later from a screenshot."""
    fields = _row_fields(sent)
    missing = sorted(_consumed_fields() - fields)
    assert missing == [], (
        "ScheduleWidget reads field(s) that GET /api/triggers/history does not send:\n  "
        + "\n  ".join(missing)
        + f"\n\nThe endpoint's real row fields are: {sorted(fields)}\n"
        "Either read the field the endpoint sends, or send the field — but not by inventing a "
        "payload to match a stale consumer. `api.triggersHistory` sets no `shape`, so this widget "
        "receives the UNIFIED FireRecord row, which is what the handler's docstring calls "
        "'the honest cross-kind answer'."
    )


def test_the_rail_catches_a_read_of_a_field_that_is_not_sent(sent):
    """🐤 CANARY. The census must red on a read the endpoint cannot satisfy — otherwise the green
    above means only that the parse found nothing. Injected into the CONSUMED side rather than a
    real file, so the canary cannot pass by accident on a name some row happens to carry."""
    fields = _row_fields(sent)
    poisoned = _consumed_fields() | {"zz_never_sent_by_any_endpoint"}
    caught = sorted(poisoned - fields)
    assert caught == [
        "zz_never_sent_by_any_endpoint"
    ], f"the census did not isolate the injected read, it reported {caught}"


def test_the_rail_would_have_caught_the_original_eight(sent):
    """The issue's eight names, pinned as a regression. Re-reading any of them off this endpoint is
    the defect, so each must still be absent from what the endpoint sends."""
    fields = _row_fields(sent)
    resurrected = sorted(
        {"job_name", "job_id", "status", "trigger", "duration_ms", "summary", "error", "trace"}
        & fields
    )
    assert resurrected == [], (
        f"the unified row grew legacy field(s) {resurrected}. Adding the legacy names back to "
        "satisfy a stale consumer is how the two shapes drift apart again — the widget reads the "
        "live shape instead."
    )


def test_no_exemption_outlives_its_read():
    """A stale exemption is how a census goes quietly vacuous, so name-checking runs both ways."""
    source = WIDGET.read_text(encoding="utf-8") + "\n" + FOLD.read_text(encoding="utf-8")
    reads = {m.group(1) for m in _READ.finditer(source)}
    stale = sorted(set(NOT_WIRE_FIELDS) - reads)
    assert stale == [], (
        f"NOT_WIRE_FIELDS names read(s) the widget no longer makes: {stale}. Delete the entry — an "
        "exemption for a read that does not exist exempts nothing and hides the next field that "
        "shares its name."
    )
    for name, reason in NOT_WIRE_FIELDS.items():
        assert reason.strip(), f"exemption {name!r} has no recorded reason"


# ── the name the widget renders is the one the endpoint resolved ──────────────


def test_every_row_carries_the_name_of_its_automation(sent):
    """The fix's own invariant. `trigger_id` is opaque (`schedule:j1`), so a feed that ships only
    the id forces every consumer to re-derive the name — and the dashboard's did not, which is why
    it rendered the literal word "Schedule" five times. Every kind knows its own name, so every
    row carries it."""
    unnamed = [r["trigger_id"] for r in sent["runs"] if not r.get("trigger_name")]
    assert unnamed == [], (
        f"row(s) {unnamed} carry no `trigger_name`. A schedule run arrives at the projection with "
        "the handler's `job_name` join already on it — an event trigger's run included — and a "
        "hook carries `.name`. Dropping it is the defect issue 466 reports."
    )
    names = {r["trigger_name"] for r in sent["runs"]}
    assert (
        len(names) > 1
    ), f"every row resolved to the same name {names} — that is the reported symptom, not the fix"


def test_the_name_join_is_not_duplicated_in_the_frontend():
    """ONE owner for the name. The widget must not rebuild the id→name join locally: a second copy
    disagrees with this one the moment a trigger is renamed, and the server already did it."""
    source = WIDGET.read_text(encoding="utf-8")
    source = re.sub(r"^\s*//.*$", "", source, flags=re.M)
    assert "split(':')" not in source and 'split(":")' not in source, (
        "ScheduleWidget appears to parse `trigger_id` itself. Read `trigger_name`, which the "
        "projection resolves once for every kind."
    )
