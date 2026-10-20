"""A saved research report is attached to the clock: one automation row, armed and granted.

The report's schedule becomes a `clock` trigger row per report, whose action is the provider that
already exists — deliberately NOT a second sweeper loop, because `gateway.py`'s `_clock_loop` is
explicit that a clock fire and a file fire go through ONE dispatch path "rather than two that
drift", and a second loop would re-decide arming, overlap, catch-up and audit slightly differently.
A fire of that row IS the report's run (`test_a_reports_schedule_is_one_schedule.py`).

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
    """The change's whole remainder: a saved report is now attached to the clock."""
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
    """Fail closed, and never invent a default: a guessed cadence would run a report on a
    schedule nobody chose. With no row, it runs when you press Run now."""
    defn = _defn()
    defn.schedule = sched
    assert rs.clock_spec(defn) == {}
    assert rs.to_trigger(defn) is None


def test_a_report_with_no_explicit_tz_still_carries_the_host_zone(home, monkeypatch):
    """The drift that had no test until a falsification leg found nothing to run.

    `ReportDefinition.tz` documents `""` as "follow the machine". An ABSENT `spec["timezone"]`
    used to mean something ELSE on the trigger side — `arm._trigger_tz` fell back to UTC — so on
    a non-UTC host the row would arm for the UTC hour and the report run at the wrong time.

    Driven through the REAL resolution now (#2520) rather than a `get_local_tz` stub. The stub
    was hiding the actual bug: `get_local_tz`'s own fallback was UTC, so `_effective_tz`
    resolved to `"UTC"` on any stock install and this test could never have seen it.
    """
    monkeypatch.setenv("TZ", "Europe/Berlin")
    defn = _defn()
    defn.tz = ""
    spec = rs.clock_spec(defn)
    assert spec.get("timezone") == "Europe/Berlin", (
        "a report with no explicit tz produced a spec with no timezone, so the trigger would "
        f"arm in UTC rather than on this machine's clock: {spec}"
    )


def test_an_explicit_tz_wins_over_the_host_zone(home, monkeypatch):
    """Vacuity for the fallback: it must not overwrite a zone the user chose."""
    monkeypatch.setenv("TZ", "Europe/Berlin")
    defn = _defn(tz="America/New_York")
    assert rs.clock_spec(defn)["timezone"] == "America/New_York"


def test_the_timezone_travels_with_the_spec(home):
    """A cron is evaluated in the trigger's OWN timezone (`arm._tz` reads `spec["timezone"]`),
    so dropping it would fire a 9am report at 9am UTC."""
    defn = _defn(tz="America/New_York")
    assert rs.clock_spec(defn)["timezone"] == "America/New_York"
