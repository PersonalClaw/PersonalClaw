"""A due research report actually fires, and `is_due` finally has a production caller.

WF2KNO-12 shipped a report definition store, a runner, an API, a UI, a `research-finding`
kind and a delivery path — and nothing attached the schedule to anything. Measured then and
recorded as the atom's UNMET clause: `grep -rn 'is_due' src/` found no caller of
`research_reports.is_due` outside its own module. A report could be defined, run by hand,
refused, listed and delivered; a *due* report never fired.

The fix is a `clock` trigger row per report, whose action is the provider that already
exists — deliberately NOT a second sweeper loop, because `gateway.py`'s `_clock_loop` is
explicit that a clock fire and a file fire go through ONE dispatch path "rather than two
that drift", and a second loop would re-decide arming, overlap, catch-up and audit slightly
differently.

Two seams here would each have produced a row that looks scheduled and never fires, and
neither is visible from the definition side:

* **The frozen-capability fence.** `screen.EMPTY_MEANS = "deny"` — "a trigger that declared
  nothing gets nothing" — and `knowledge-report` is classified write-capable. A row with no
  `capabilities` block is refused at fire time.
* **Arming.** A freshly upserted clock row carries `next_fire_at = ""`, which `arm.py`'s own
  docstring calls "permanently inert … due_ids STILL []". `boot_migrate.arm_unarmed` runs at
  BOOT only, so a report created while the gateway is up would have waited for a restart —
  the same never-fires defect, moved one step later.

Both are asserted below against the shipped functions rather than against a restatement of
what they should return.
"""

from __future__ import annotations

import pytest

from personalclaw.knowledge import report_schedules as rs
from personalclaw.knowledge import research_reports as rr
from personalclaw.schedule import ScheduleDefinition
from personalclaw.triggers.store import TriggerStore

CRON = "0 9 * * 1"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home — this writes the report store AND the trigger store."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _defn(**over):
    raw = {
        "name": "weekly perf",
        "prompt": "what changed?",
        "tz": "UTC",
        "schedule": {"kind": "cron", "cron_expr": CRON},
        "source": {"tags": ["perf"], "window_secs": 604800},
        "context": {"tags": ["docs"]},
        "citation_policy": rr.CITE_SOURCE_ONLY,
        "enabled": True,
    }
    raw.update(over)
    return rr.from_dict(raw)


def _rows() -> list:
    return TriggerStore().list_triggers()


# ── The schedule is attached ───────────────────────────────────────────────


def test_saving_a_report_creates_its_clock_trigger(home):
    """The atom's whole remainder: a saved report is now attached to the clock."""
    defn = rr.save_report(_defn())
    rows = _rows()
    assert len(rows) == 1, f"a saved report produced {len(rows)} trigger rows"
    row = rows[0]
    assert row.kind == "clock"
    assert row.spec == {"kind": "cron", "expr": CRON, "timezone": "UTC"}
    assert row.workflow == {
        "provider": "knowledge-report",
        "config": {"report_id": defn.id},
    }


def test_the_row_is_ARMED_at_creation_not_only_at_boot(home):
    """The inert-row trap, in the words of `arm.py`'s own docstring.

    A clock row with `next_fire_at = ""` is "permanently inert"; `arm_unarmed` only runs at
    boot, so without this a report created on a running gateway waits for a restart.
    """
    rr.save_report(_defn())
    row = _rows()[0]
    assert row.next_fire_at, "the row was persisted unarmed — it will not fire until a restart"


def test_the_capability_fence_ADMITS_the_row(home):
    """The second invisible seam, asserted through the shipped fence.

    `knowledge-report` is write-capable and `EMPTY_MEANS = "deny"`, so a row without a frozen
    block is refused at fire time — a schedule that exists, arms, fires, and is then turned
    away.
    """
    from personalclaw.triggers import screen

    rr.save_report(_defn())
    row = _rows()[0]
    decision = screen.capability_allows(row.capabilities, key="providers", value="knowledge-report")
    assert getattr(
        decision, "allowed", False
    ), f"the fence refuses this row's own action: {row.capabilities} -> {decision}"


def test_an_empty_capability_block_would_be_refused(home):
    """Vacuity for the test above: the fence must be capable of saying no.

    Without this, `capability_allows` could return allowed for anything and the assertion
    above would prove nothing about the block being frozen.
    """
    from personalclaw.triggers import screen

    decision = screen.capability_allows({}, key="providers", value="knowledge-report")
    assert not getattr(decision, "allowed", True), "the fence permits an empty block"


def test_re_saving_updates_the_same_row_rather_than_accumulating(home):
    """The trigger id is derived from the report id, so an edit is an update."""
    defn = rr.save_report(_defn())
    defn.name = "weekly perf (renamed)"
    rr.save_report(defn)
    rr.save_report(defn)
    rows = _rows()
    assert len(rows) == 1, f"three saves produced {len(rows)} rows"
    assert rows[0].id == rs.trigger_id_for(defn.id)
    assert "renamed" in rows[0].name, "the row kept a stale name"


def test_pausing_the_report_disables_the_row(home):
    """One switch. A paused report whose row stayed enabled would wake the runner to be
    told "disabled" — a fire that exists only to be skipped."""
    defn = rr.save_report(_defn())
    assert _rows()[0].enabled is True
    defn.enabled = False
    rr.save_report(defn)
    assert _rows()[0].enabled is False
    defn.enabled = True
    rr.save_report(defn)
    row = _rows()[0]
    assert row.enabled is True and row.next_fire_at, "re-enabling left the row unarmed"


def test_deleting_the_report_removes_the_row(home):
    defn = rr.save_report(_defn())
    assert len(_rows()) == 1
    assert rr.delete_report(defn.id) is True
    assert _rows() == [], "the schedule outlived the report it belongs to"


def test_clearing_the_cadence_removes_the_row(home):
    """A definition with no usable schedule must not keep firing on the old one."""
    defn = rr.save_report(_defn())
    assert len(_rows()) == 1
    defn.schedule = ScheduleDefinition(kind="")
    rr.save_report(defn)
    assert _rows() == [], "a report with no cadence kept a live trigger"


# ── The cadence mapping ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sched,expected",
    [
        (
            ScheduleDefinition(kind="cron", cron_expr=CRON),
            {"kind": "cron", "expr": CRON, "timezone": "UTC"},
        ),
        # `interval`, not `every`: `CLOCK_KINDS` has no `every`, so a row spelling it that way
        # is an ERROR from `validate_spec` and would persist broken.
        (
            ScheduleDefinition(kind="every", every_secs=3600),
            {"kind": "interval", "interval_secs": 3600, "timezone": "UTC"},
        ),
        # An epoch float, because `arm.next_fire` reads `spec["at"]` through `_positive()`.
        (
            ScheduleDefinition(kind="at", at_ts=2_000_000_000.0),
            {"kind": "at", "at": 2_000_000_000.0, "timezone": "UTC"},
        ),
    ],
)
def test_each_cadence_maps_to_a_clock_spec_the_substrate_accepts(home, sched, expected):
    defn = _defn()
    defn.schedule = sched
    defn.tz = "UTC"
    assert rs.clock_spec(defn) == expected


@pytest.mark.parametrize(
    "sched",
    [
        ScheduleDefinition(kind=""),
        ScheduleDefinition(kind="cron", cron_expr=""),
        ScheduleDefinition(kind="every", every_secs=0),
        ScheduleDefinition(kind="at", at_ts=0.0),
        ScheduleDefinition(kind="nonsense"),
    ],
)
def test_an_unusable_cadence_yields_no_row_rather_than_a_guess(home, sched):
    """Fail closed, and never invent a default: `is_due` already refuses with a named reason,
    and a guessed cadence would run a report on a schedule nobody chose."""
    defn = _defn()
    defn.schedule = sched
    assert rs.clock_spec(defn) == {}
    assert rs.to_trigger(defn) is None


def test_a_report_with_no_explicit_tz_still_carries_the_host_zone(home, monkeypatch):
    """The drift that had no test until a falsification leg found nothing to run.

    `ReportDefinition.tz` documents `"" == host local`, and `_report_tz` honours that. An
    ABSENT `spec["timezone"]` means something ELSE on the trigger side: `arm._tz` falls back
    to UTC. So on a non-UTC host the row would arm for the UTC hour while `is_due` waited for
    the local one — the fire arrives, the pre-flight skips it as not-due, and the report runs
    late or not that day. Safe rather than wrong, which is why it would have gone unnoticed.
    """
    import personalclaw.schedule as sched_mod

    monkeypatch.setattr(sched_mod, "get_local_tz", lambda: ("Europe/Berlin", None))
    defn = _defn()
    defn.tz = ""
    spec = rs.clock_spec(defn)
    assert spec.get("timezone") == "Europe/Berlin", (
        "a report with no explicit tz produced a spec with no timezone, so the trigger would "
        f"arm in UTC while is_due waits for host local: {spec}"
    )


def test_an_explicit_tz_wins_over_the_host_zone(home, monkeypatch):
    """Vacuity for the fallback: it must not overwrite a zone the user chose."""
    import personalclaw.schedule as sched_mod

    monkeypatch.setattr(sched_mod, "get_local_tz", lambda: ("Europe/Berlin", None))
    defn = _defn(tz="America/New_York")
    assert rs.clock_spec(defn)["timezone"] == "America/New_York"


def test_the_timezone_travels_with_the_spec(home):
    """A cron is evaluated in the trigger's OWN timezone (`arm._tz` reads `spec["timezone"]`),
    so dropping it would fire a 9am report at 9am UTC."""
    defn = _defn(tz="America/New_York")
    assert rs.clock_spec(defn)["timezone"] == "America/New_York"


# ── The pre-flight: `is_due` gets its caller ──────────────────────────────


class _Ctx:
    event = "clock.fire"
    context = ""
    payload: dict = {}


async def _run(config: dict):
    from personalclaw.action_providers.knowledge_report_provider import (
        KnowledgeReportActionProvider,
    )

    return await KnowledgeReportActionProvider().execute(config, _Ctx())


@pytest.mark.asyncio
async def test_a_scheduled_fire_that_is_not_due_is_a_named_skip(home, monkeypatch):
    """The pre-flight. The trigger may be more eager than the report; `is_due` is the
    authority for the window, and it owns the four hardening rules a cron cannot express."""
    import json

    defn = rr.save_report(_defn())
    calls: list = []
    monkeypatch.setattr(rr, "record_run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(rr, "is_due", lambda d, *, now: (False, "waiting for the window"))

    result = await _run({"report_id": defn.id})

    assert result.success is True, "a not-due fire is a skip, not a failure"
    body = json.loads(result.stdout or "{}")
    assert body.get("skipped") == "not_due" and body.get("reason")
    assert calls == [], (
        "a fire that did not run stamped the report — that advances the watermark past "
        "material this fire never read"
    )


@pytest.mark.asyncio
async def test_a_MANUAL_run_skips_the_dueness_check(home, monkeypatch):
    """The user clicking Run now is the authority for that fire; refusing it as "not due"
    would make the button lie. The flag rides the action CONFIG, which a trigger row never
    sets — so a scheduled fire cannot acquire it by accident."""
    defn = rr.save_report(_defn())
    asked: list = []

    def _is_due(d, *, now):
        asked.append(d.id)
        return (False, "not due")

    monkeypatch.setattr(rr, "is_due", _is_due)
    # The scope resolution is not under test; a store failure is a clean recorded failure.
    monkeypatch.setattr(rr, "record_run", lambda *a, **k: None)

    await _run({"report_id": defn.id, "manual": True})

    assert asked == [], "a manual run consulted the schedule and could refuse the user"


@pytest.mark.asyncio
async def test_a_DUE_scheduled_fire_is_not_skipped(home, monkeypatch):
    """Vacuity for the pre-flight, and the assertion that makes this atom's claim true.

    A gate that skips every fire would satisfy the not-due test while leaving the report
    exactly as inert as before this change.
    """
    import json

    defn = rr.save_report(_defn())
    monkeypatch.setattr(rr, "is_due", lambda d, *, now: (True, "due"))
    monkeypatch.setattr(rr, "record_run", lambda *a, **k: None)

    result = await _run({"report_id": defn.id})

    body = json.loads(result.stdout or "{}")
    assert body.get("skipped") != "not_due", "a due report was skipped as not due"


def test_is_due_now_has_a_production_caller(home):
    """The rail on the atom's own UNMET clause — a source scan, and only that.

    The recorded evidence for leaving WF2KNO-12 `todo` was `grep -rn 'is_due' src/` finding no
    caller, so this asserts the inverse of that exact measurement. It proves PRESENCE, not
    reachability: with the pre-flight disabled by `if False:` this test still passed, because
    the call site is still in the file. Reachability is
    `test_a_scheduled_fire_that_is_not_due_is_a_named_skip`, which drives the shipped provider
    and reds under the same mutation — the two are a pair on purpose, and neither is
    sufficient alone.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "src" / "personalclaw"
    callers = []
    for path in root.rglob("*.py"):
        if path.name == "research_reports.py":
            continue  # the definition itself
        text = path.read_text(encoding="utf-8")
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        if re.search(r"\brr\.is_due\(|research_reports\.is_due\(", code):
            callers.append(str(path.relative_to(root)))
    assert callers, (
        "nothing in src/ calls research_reports.is_due — a due report still never fires, "
        "which is the exact clause this atom was left `todo` for"
    )
