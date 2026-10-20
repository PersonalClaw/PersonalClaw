"""A run anyone but you asks for is its automation firing, held to every rule a fire keeps.

You run an automation by hand from its page, or with ``personalclaw cron trigger`` typed at a
terminal: that run is yours, and the automation's hourly cap and its failure streak pass over it.
Anyone else can ask for a run by name too: an agent with its ``automation_run`` tool or the CLI in
its shell, an app, another automation's own work, a program on this machine. Measured before this
was written, every one of those was recorded as a run by hand, ``manual``:

* the hourly cap passed over each one, so an automation capped at two runs an hour ran on every
  call an agent made;
* the failure streak passed over each one, so an automation that failed on every call never
  paused, and its owner was never told;
* an agent ran an automation that was switched off, or one its own failures had paused;
* it ran through the dispatch your Run now takes, so it read as somebody watching to every check
  that asks, the one that keeps unattended work out of your own browser among them;
* the history said nothing of who asked.

Now each is a fire: admitted as the clock's, an event's and a webhook's are, dispatched as they
are, counted toward the cap and the streak, and recorded with who asked for it. A fire its rules
hold answers 429 or 409 ``fire_held`` and leaves its row in the history. Your own run is unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from signed_in_gateway import Gateway, signed_in_gateway

from personalclaw import session_keys
from personalclaw.action_providers.base import ActionResult
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

#: The action every automation here runs: one that says how it went, and what it was told.
PROBE = "asked-run-probe"
TID = "clock:standup-notes"
RUN_PATH = f"/api/triggers/schedule:{TID}/run"
#: An agent's own work: a subagent's session, and the chat you are in with your agent.
SUBAGENT = session_keys.SUBAGENT.key("a1b2c3")
CHAT = session_keys.CHAT.key("1-1700000000")


class _Probe:
    """An action that records each run, succeeding or failing as it is told."""

    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.fails = False

    async def execute(self, config: dict, ctx: Any, timeout: int = 30) -> ActionResult:
        from personalclaw.browse.target import unattended_origin

        self.runs.append(
            {
                "event": ctx.event,
                "payload": dict(ctx.payload or {}),
                "unattended": unattended_origin(),
            }
        )
        if self.fails:
            return ActionResult(success=False, error="the notes server said no")
        return ActionResult(success=True, stdout="done")


@pytest.fixture
def probe(monkeypatch) -> _Probe:
    from personalclaw.action_providers import registry

    action = _Probe()
    registry._ensure_default_providers_registered()
    monkeypatch.setitem(registry._providers, PROBE, action)
    return action


def _automation(home: Path, **fields: Any) -> None:
    """A scheduled automation its owner made and allowed to run its action."""
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=TID,
            name="Standup notes",
            kind=fields.pop("kind", "clock"),
            enabled=fields.pop("enabled", True),
            created_by="user",
            spec=fields.pop("spec", {"kind": "cron", "expr": "0 9 * * *"}),
            workflow={"inline": {"provider": PROBE, "config": {}}},
            capabilities={"providers": [PROBE]},
            **fields,
        )
    )


def _stored(home: Path) -> Trigger:
    loaded = TriggerStore(base_dir=home).get(TID)
    assert loaded is not None
    return loaded.trigger


async def _history(gw: Gateway) -> list[dict]:
    status, body = await gw.as_owner("GET", f"/api/triggers/store:{TID}/history?limit=50")
    assert status == 200, body
    return list(body["runs"])


async def _asked(gw: Gateway, work: str = SUBAGENT) -> tuple[int, Any]:
    """The call ``automation_run`` makes: the internal credential, naming the agent's work."""
    return await gw.as_work_of(work, "POST", RUN_PATH, json={})


# ── the hourly cap ──


@pytest.mark.asyncio
async def test_an_agents_run_past_the_hourly_cap_is_refused_and_recorded(
    tmp_path, monkeypatch, probe
):
    """🔴 Red before: all three ran, each recorded ``manual``, and the cap of two held nothing."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _automation(gw.home, gates={"max_runs_per_hour": 2})

        answers = [await _asked(gw) for _ in range(3)]

        assert [status for status, _ in answers[:2]] == [200, 200], answers
        assert all(body["ok"] is True for _, body in answers[:2]), answers
        status, held = answers[2]
        assert status == 429, held
        assert held["error"]["code"] == "fire_held"
        assert "as often as its owner allows" in held["error"]["message"]
        assert len(probe.runs) == 2
        rows = await _history(gw)
        ran = [r for r in rows if r["status"] == "success"]
        assert len(ran) == 2 and {r["source"] for r in ran} == {"agent"}, rows
        assert {r["trigger"] for r in ran} == {"ok"}, rows
        (suppressed,) = [r for r in rows if r["status"] == "skipped_gate"]
        assert suppressed["source"] == "agent", suppressed
        assert "reaches the cap of 2" in suppressed["error"]
        # A fire spends the automation's count, as the clock's fires do.
        assert _stored(gw.home).run_count == 2


@pytest.mark.asyncio
async def test_your_run_now_still_passes_over_the_hourly_cap(tmp_path, monkeypatch, probe):
    """Control, the same before and after: your Run now is yours, so the cap passes over it,
    and it spends nothing the cap or the budget reads."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _automation(gw.home, gates={"max_runs_per_hour": 1})

        for _ in range(3):
            status, body = await gw.as_owner("POST", RUN_PATH, json={})
            assert status == 200 and body["ok"] is True, body

        assert len(probe.runs) == 3
        assert {run["event"] for run in probe.runs} == {"manual.run"}
        rows = await _history(gw)
        assert [r["source"] for r in rows] == ["you"] * 3, rows
        assert _stored(gw.home).run_count == 0


@pytest.mark.asyncio
async def test_a_fire_after_your_runs_still_has_its_whole_cap(tmp_path, monkeypatch, probe):
    """Your runs are not counted toward the cap a fire is held to, so an agent's first run after
    yours goes ahead; its second, past a cap of one, does not."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _automation(gw.home, gates={"max_runs_per_hour": 1})
        for _ in range(2):
            status, body = await gw.as_owner("POST", RUN_PATH, json={})
            assert status == 200 and body["ok"] is True, body

        first, second = await _asked(gw), await _asked(gw)

        assert first[0] == 200 and first[1]["ok"] is True, first
        assert second[0] == 429 and second[1]["error"]["code"] == "fire_held", second
        assert len(probe.runs) == 3


# ── the failure streak ──


@pytest.mark.asyncio
async def test_an_agents_failing_runs_trip_the_failure_streak(tmp_path, monkeypatch, probe):
    """🔴 Red before: every failure was a run by hand, which the streak passes over, so the
    automation failed on every call, never paused, and its owner was never told."""
    probe.fails = True
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        told: list[dict] = []
        notify = gw.state.notify

        def _told(kind: str, title: str, body: str, **extra: Any) -> None:
            told.append({"title": title, "body": body, **extra})
            notify(kind, title, body, **extra)

        monkeypatch.setattr(gw.state, "notify", _told)
        _automation(gw.home, failure_policy={"autopause_after": 2})

        first, second, third = await _asked(gw), await _asked(gw), await _asked(gw)

        assert first[1]["ok"] is False and "the notes server said no" in first[1]["result"]
        assert second[1]["ok"] is False
        # Paused by its failures, it fires for nobody, an agent included.
        assert third[0] == 409 and third[1]["error"]["code"] == "fire_held", third
        assert "paused itself" in third[1]["error"]["message"]
        assert len(probe.runs) == 2
        paused = _stored(gw.home)
        assert paused.enabled is False and paused.state == "autopaused", paused.to_dict()
        rows = await _history(gw)
        assert [(r["trigger"], r["source"]) for r in rows] == [("failed", "agent")] * 2, rows
        assert any(
            (t.get("meta") or {}).get("event") == "automation.needs_attention" for t in told
        ), told


@pytest.mark.asyncio
async def test_your_failing_run_now_neither_pauses_it_nor_clears_a_streak(
    tmp_path, monkeypatch, probe
):
    """Control: testing a broken automation by hand must neither pause it nor reset the streak
    its fires are building. One failed fire, then your failing Run nows, then one more failed fire:
    the streak of two is reached by the two fires alone."""
    probe.fails = True
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _automation(gw.home, failure_policy={"autopause_after": 2})

        await _asked(gw)
        for _ in range(3):
            status, body = await gw.as_owner("POST", RUN_PATH, json={})
            assert status == 200 and body["ok"] is False, body
        assert _stored(gw.home).state == "active"
        await _asked(gw)

        assert _stored(gw.home).state == "autopaused"
        rows = await _history(gw)
        assert [r["source"] for r in rows] == ["agent", "you", "you", "you", "agent"], rows


# ── what does not fire, and what it reads as ──


@pytest.mark.asyncio
async def test_an_agent_does_not_run_an_automation_that_is_switched_off(
    tmp_path, monkeypatch, probe
):
    """🔴 Red before: the agent's Run now ran it, as yours does. Yours still does."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _automation(gw.home, enabled=False)

        status, body = await _asked(gw)

        assert status == 409 and body["error"]["code"] == "fire_held", body
        assert "switched off" in body["error"]["message"]
        assert probe.runs == []
        status, body = await gw.as_owner("POST", RUN_PATH, json={})
        assert status == 200 and body["ok"] is True, body
        assert len(probe.runs) == 1


@pytest.mark.asyncio
async def test_an_automation_only_you_run_is_run_by_nobody_else(tmp_path, monkeypatch, probe):
    """🔴 Red before: the agent ran the automation its chat made to run only when you run it."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _automation(gw.home, kind="manual", spec={})

        status, body = await _asked(gw, CHAT)

        assert status == 409 and body["error"]["code"] == "fire_held", body
        assert "only when its owner runs it" in body["error"]["message"]
        assert probe.runs == []


@pytest.mark.asyncio
async def test_an_agents_run_reads_as_nobody_watching(tmp_path, monkeypatch, probe):
    """🔴 Red before: the agent of the chat you are in ran it through your Run now's dispatch, so
    what it ran read as somebody watching (`browse.target.unattended_origin` said nobody was
    away), and it was told it was a run by hand. Yours still reads as you there."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _automation(gw.home)

        status, body = await _asked(gw, CHAT)
        assert status == 200 and body["ok"] is True, body
        status, body = await gw.as_owner("POST", RUN_PATH, json={})
        assert status == 200 and body["ok"] is True, body

        agents, yours = probe.runs
        assert agents["unattended"] != "" and agents["event"] == "agent.run", agents
        assert agents["payload"].get("manual") is None, agents
        assert yours["unattended"] == "" and yours["event"] == "manual.run", yours


# ── the CLI ──


@pytest.mark.asyncio
async def test_the_cli_typed_at_a_terminal_is_yours_and_run_by_a_script_is_a_fire(
    tmp_path, monkeypatch, probe
):
    """``personalclaw cron trigger`` names its work: your own command when you type it at a
    terminal, which is yours like your Run now, and a dispatch with no session when a script runs
    it, which fires. 🔴 Red before: both were recorded as runs by hand, past a cap of one."""
    from personalclaw.guardrails.policy import unattended_dispatch_key

    typed = session_keys.CLI.key("cron-trigger")
    scripted = unattended_dispatch_key(typed)
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _automation(gw.home, gates={"max_runs_per_hour": 1})

        typed_answers = [await gw.as_work_of(typed, "POST", RUN_PATH, json={}) for _ in range(2)]
        first, second = [await gw.as_work_of(scripted, "POST", RUN_PATH, json={}) for _ in "ab"]

        assert all(status == 200 and body["ok"] for status, body in typed_answers), typed_answers
        assert first[0] == 200 and first[1]["ok"] is True, first
        assert second[0] == 429 and second[1]["error"]["code"] == "fire_held", second
        rows = await _history(gw)
        # Newest first.
        assert [r["source"] for r in rows if r["status"] == "success"] == [
            "program",
            "you",
            "you",
        ], rows


class _Stdin:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.mark.parametrize(
    "tty,named,expected",
    [
        (True, "", "cli:cron-trigger"),
        (False, "", "unattended:cli:cron-trigger"),
        (True, CHAT, CHAT),
        (False, SUBAGENT, SUBAGENT),
    ],
)
def test_the_cli_names_whose_run_it_asks_for(monkeypatch, tty, named, expected):
    """What ``personalclaw cron trigger`` names: the session's work it runs in when there is one
    (an agent's shell), your own command when you type it at a terminal, and a dispatch with no
    session otherwise. 🔴 Red before: outside a session it named the automation's own work,
    whoever ran it."""
    from personalclaw import home_gateway, schedule_trigger

    seen: list[str] = []

    class _Gateway:
        """This home's gateway, as ``home_gateway.reach`` finds it: what each call names."""

        def post(self, path: str, body: dict, *, secret_header: str, work: str = "", **_kw: Any):
            assert secret_header == "X-Internal-Secret"
            seen.append(work)
            return 200, {"ok": True, "name": "Standup notes"}

    if named:
        monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", named)
    else:
        monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)
    monkeypatch.setattr(home_gateway, "reach", lambda port=None: _Gateway())
    monkeypatch.setattr(schedule_trigger.sys, "stdin", _Stdin(tty))

    ok, said = schedule_trigger.trigger_schedule_job(TID)

    assert ok and said == "triggered 'Standup notes'", said
    assert seen == [expected]


# ── a scheduled fire ──


@pytest.mark.asyncio
async def test_a_scheduled_fire_is_recorded_as_its_schedules_and_counted(
    tmp_path, monkeypatch, probe
):
    """Control: the clock's fire runs as it did, its row now saying the schedule started it, and
    the hourly cap counts it."""
    from personalclaw.schedule_history import ScheduleRunStore

    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _automation(gw.home)

        run_id = await gw.state.fire_trigger(_stored(gw.home), {"trigger_id": TID})

        assert [run["event"] for run in probe.runs] == ["trigger.fired"]
        (row,) = await _history(gw)
        assert row["run_id"] == run_id
        assert (row["status"], row["trigger"], row["source"]) == ("success", "ok", "schedule")
        assert await ScheduleRunStore(gw.home).count_since(TID, 0.0) == 1
