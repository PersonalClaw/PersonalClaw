"""WF2LOO-9 (R15): goal-pursuit-monitor — the parked-run + self-created-clock-trigger pattern.

The template's central mechanism is the trigger substrate's resume target: a monitor cycle
schedules its own next wake with ``set_onetime_task(resume_run_id="self")`` and the run parks
at an ``event`` gate that trigger answers. These tests hold the three bounds the plan calls
load-bearing — "ship them in the same slice as the tools, never after":

* **mandatory TTL** — every agent-created trigger expires (`_agent_expiry_iso`), so a forgotten
  clock cannot hold a cap slot forever;
* **provenance** — `created_by: agent` plus the resume target's `run_id` name who created it
  and what it wakes;
* **the write side of AUTO-R11's resume targets** — `T.create(resume=...)` produces the
  `workflow.resume` shape `wakeup.resume_target_of` reads, and it survives a store reload
  (criterion 7's restart half: the substrate's persistence is what replaces autonudge).

Every test drives the REAL ``TriggerStore`` against a ``tmp_path`` — the same discipline as
``test_triggers_tools.py``, and for the same reason: the persistence IS the feature.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from personalclaw.triggers import tools as T
from personalclaw.triggers.store import TriggerStore
from personalclaw.triggers.wakeup import RESUME_TARGET_KEY, resume_target_of


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _cron(expr="0 9 * * 1-5"):
    return lambda _cadence: (expr, "")


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


# ── mandatory TTL on every agent-created trigger ──


def test_agent_recurring_trigger_gets_default_ttl(store):
    """An agent-created cron row carries `expires_at` ~30 days out without anyone asking."""
    result = T.create(
        store,
        name="watch build",
        when="every weekday at 9",
        message="check the build",
        created_by="agent",
        cadence_to_cron=_cron(),
    )
    assert result.ok
    loaded = store.get(result.data["trigger"]["id"])
    assert loaded.ok, [i.message for i in loaded.issues]
    saved = loaded.trigger
    assert saved.expires_at, "agent-created trigger must always expire"
    remaining = _epoch(saved.expires_at) - datetime.now(timezone.utc).timestamp()
    assert abs(remaining - T.AGENT_RECURRING_TTL_SECS) < 3600


def test_agent_onetime_trigger_gets_short_ttl(store):
    """A one-shot (`at` spec) defaults to the 7-day TTL, not the recurring 30."""
    result = T.create(
        store,
        name="follow up",
        kind="clock",
        spec={"kind": "at", "at": "2999-01-01T09:00:00+00:00"},
        message="check it",
        created_by="agent",
    )
    assert result.ok
    loaded = store.get(result.data["trigger"]["id"])
    assert loaded.ok, [i.message for i in loaded.issues]
    saved = loaded.trigger
    remaining = _epoch(saved.expires_at) - datetime.now(timezone.utc).timestamp()
    assert abs(remaining - T.AGENT_ONETIME_TTL_SECS) < 3600


def test_explicit_ttl_is_honoured_and_clamped(store):
    """`ttl_secs` wins over the default; out-of-window values clamp rather than refuse."""
    result = T.create(
        store,
        name="short watch",
        when="every weekday at 9",
        message="check",
        created_by="agent",
        cadence_to_cron=_cron(),
        ttl_secs=3600,
    )
    loaded = store.get(result.data["trigger"]["id"])
    assert loaded.ok, [i.message for i in loaded.issues]
    saved = loaded.trigger
    remaining = _epoch(saved.expires_at) - datetime.now(timezone.utc).timestamp()
    assert abs(remaining - 3600) < 300

    clamped = T.create(
        store,
        name="tiny ttl",
        when="every weekday at 10",
        message="check",
        created_by="agent",
        cadence_to_cron=_cron("0 10 * * 1-5"),
        ttl_secs=1,  # below the 60s floor
    )
    saved2 = store.get(clamped.data["trigger"]["id"]).trigger
    remaining2 = _epoch(saved2.expires_at) - datetime.now(timezone.utc).timestamp()
    assert remaining2 >= T.MIN_AGENT_TTL_SECS - 5


def test_user_created_trigger_keeps_optin_expiry(store):
    """The mandate is scoped to self-scheduling: a USER row still opts in to expiry."""
    result = T.create(
        store,
        name="my cron",
        when="every weekday at 9",
        message="do it",
        created_by="user",
        cadence_to_cron=_cron(),
    )
    assert result.ok
    assert store.get(result.data["trigger"]["id"]).trigger.expires_at == ""


# ── the resume-target write side (AUTO-R11's counterpart) ──


def test_resume_target_roundtrips_through_the_store(store):
    """`resume=` becomes `workflow.resume`, and the READ side normalizes it back."""
    result = T.create(
        store,
        name="wake the monitor",
        kind="clock",
        spec={"kind": "at", "at": "2999-01-01T09:00:00+00:00"},
        message="time for the next check",
        created_by="agent",
        resume={"run_id": "r-monitor-7"},
    )
    assert result.ok
    loaded = store.get(result.data["trigger"]["id"])
    assert loaded.ok, [i.message for i in loaded.issues]
    saved = loaded.trigger
    assert saved.workflow.get(RESUME_TARGET_KEY, {}).get("run_id") == "r-monitor-7"
    target = resume_target_of(saved)
    assert target["run_id"] == "r-monitor-7"
    # The message rode as the gate ANSWER — what the woken run reads — not as an action.
    assert target["answers_gate"] is True
    assert target["gate_answer"] == "time for the next check"
    assert "provider" not in saved.workflow


def test_resume_and_workflow_together_are_refused(store):
    """Both-declared is the authoring error `_resume_target_issues` names — refuse early."""
    result = T.create(
        store,
        name="ambiguous",
        kind="clock",
        spec={"kind": "at", "at": "2999-01-01T09:00:00+00:00"},
        created_by="agent",
        resume={"run_id": "r1"},
        workflow={"provider": "run-prompt", "config": {"message": "x"}},
    )
    assert not result.ok
    assert "not both" in result.text


def test_resume_without_run_id_is_refused(store):
    result = T.create(
        store,
        name="no target",
        kind="clock",
        spec={"kind": "at", "at": "2999-01-01T09:00:00+00:00"},
        message="wake",
        created_by="agent",
        resume={},
    )
    assert not result.ok
    assert "run_id" in result.text


def test_cap_counts_resume_triggers_too(store):
    """Self-scheduling stays inside `self_schedule_max_outstanding` — resume rows included."""
    cap = T.max_agent_triggers()
    for i in range(cap):
        r = T.create(
            store,
            name=f"wake {i}",
            kind="clock",
            spec={"kind": "at", "at": "2999-01-01T09:00:00+00:00"},
            message="check",
            created_by="agent",
            resume={"run_id": f"r{i}"},
        )
        assert r.ok, r.text
    over = T.create(
        store,
        name="one too many",
        kind="clock",
        spec={"kind": "at", "at": "2999-01-01T09:00:00+00:00"},
        message="check",
        created_by="agent",
        resume={"run_id": "r-over"},
    )
    assert not over.ok
    assert str(cap) in over.text


# ── criterion 7's restart half: substrate persistence ──


def test_resume_trigger_survives_a_store_reload(store, tmp_path):
    """A gateway restart re-reads triggers from disk: the resume target and the TTL must both
    come back — this is what lets a parked monitor outlive the process that parked it."""
    created = T.create(
        store,
        name="restart survivor",
        kind="clock",
        spec={"kind": "at", "at": "2999-01-01T09:00:00+00:00"},
        message="next check",
        created_by="agent",
        resume={"run_id": "r-parked-42"},
    )
    assert created.ok

    fresh = TriggerStore(base_dir=tmp_path)  # a new process's view of the same disk
    loaded = fresh.get(created.data["trigger"]["id"])
    assert loaded is not None and loaded.ok, "a well-formed resume target must re-read clean"
    saved = loaded.trigger
    assert resume_target_of(saved)["run_id"] == "r-parked-42"
    assert saved.expires_at, "the mandatory TTL must persist across restarts"
    assert saved.created_by == "agent", "provenance must persist across restarts"


# ── the tool surface (mcp_automation) ──


def test_self_resolves_from_leaf_lineage_env(monkeypatch):
    from personalclaw.mcp_automation import _resolve_resume_target

    monkeypatch.setenv("__wf_run_id", "r-lineage-9")
    target, err = _resolve_resume_target({"resume_run_id": "self"})
    assert err == ""
    assert target == {"run_id": "r-lineage-9"}


def test_self_without_lineage_is_a_typed_error(monkeypatch):
    from personalclaw.mcp_automation import _resolve_resume_target

    monkeypatch.delenv("__wf_run_id", raising=False)
    target, err = _resolve_resume_target({"resume_run_id": "self"})
    assert target is None
    assert "no run lineage" in err


def test_explicit_run_id_passes_through():
    from personalclaw.mcp_automation import _resolve_resume_target

    target, err = _resolve_resume_target({"resume_run_id": "r-explicit"})
    assert err == ""
    assert target == {"run_id": "r-explicit"}

    none_target, none_err = _resolve_resume_target({})
    assert none_target is None and none_err == ""


# ── the template + its alias ──


def test_monitor_template_ships_and_parses():
    from personalclaw.workflows.bundled_defs import read_template, template_names

    assert "goal-pursuit-monitor" in template_names()
    wf = read_template("goal-pursuit-monitor")
    assert wf is not None


def test_monitor_template_parks_on_an_event_gate_and_self_schedules():
    """The template's mechanism, held structurally: an `event` park gate inside the watch
    loop, and both scheduling stages instructing `set_onetime_task` with `resume_run_id`."""
    import json
    from pathlib import Path

    from personalclaw.workflows.bundled_defs import bundled_root

    raw = json.loads((Path(bundled_root()) / "goal-pursuit-monitor" / "workflow.json").read_text())
    children = raw["root"]["children"]
    intake, watch = children[0], children[1]
    assert "set_onetime_task" in intake["config"]["prompt"]
    assert 'resume_run_id="self"' in intake["config"]["prompt"]

    park, check = watch["body"]["children"]
    assert park["kind"] == "gate"
    assert park["config"]["kind"] == "event"
    assert park["config"]["timeout_secs"] > 0, "a parked gate needs its safety-net deadline"
    assert 'resume_run_id="self"' in check["config"]["prompt"]
    # Self-scheduling creates triggers, which is mutating — a research leaf would be denied.
    assert intake["config"]["capability"] == "mutating"
    assert check["config"]["capability"] == "mutating"
    # The watch ends only when a check says the goal is met.
    assert watch["config"]["mode"] == "until"
    assert "goal_met" in watch["config"]["condition"]


def test_goal_monitor_alias_resolves():
    from personalclaw.workflows.loop_aliases import resolve_kind

    assert resolve_kind("goal", variant="monitor") == "goal-pursuit-monitor"
    # The bare kinds keep their existing meanings.
    assert resolve_kind("goal") == "goal-pursuit-open-ended"
    assert resolve_kind("goal", has_verify_command=True) == "goal-pursuit-verifiable"
