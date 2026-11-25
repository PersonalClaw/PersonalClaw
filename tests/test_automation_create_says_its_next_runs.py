"""The agent's answer to making or moving a clock automation says when it will run.

The agent heard one next run only from `automation_list`, so after making an automation it told
the owner nothing about when it runs, and a skipped day surfaced only when it was skipped. Now
`automation_create`'s answer for a clock automation, and `automation_update`'s when its cadence
moved, carry its next three runs from the preview the form shows (`triggers.preview`), a line per
note, and `data.next_runs`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from personalclaw.triggers import tools
from personalclaw.triggers.store import TriggerStore

NOTE = {"inline": {"provider": "notify", "config": {"title_template": "Morning"}}}


def _make(store: TriggerStore, spec: dict, *, kind: str = "clock"):
    return tools.create(
        store, name="Morning note", kind=kind, spec=spec, workflow=NOTE, created_by="user"
    )


def test_a_weekday_automation_says_its_next_three_runs(tmp_path) -> None:
    made = _make(
        TriggerStore(base_dir=tmp_path),
        {"kind": "cron", "expr": "0 9 * * 1-5", "timezone": "America/New_York"},
    )
    assert made.ok, made.text
    (line,) = [said for said in made.text.splitlines() if said.startswith("  next runs:")]
    assert line.endswith("(times in America/New_York)")
    walls = line.removeprefix("  next runs: ").split(" (times in")[0].split("; ")
    assert len(walls) == 3 and all(w.endswith("09:00") for w in walls)
    assert [r["wall"] for r in made.data["next_runs"]] == walls
    assert all(r["runs"] for r in made.data["next_runs"])


def test_a_skipped_day_is_said_on_its_own_line(tmp_path) -> None:
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    made = _make(
        TriggerStore(base_dir=tmp_path),
        {"kind": "cron", "expr": "0 12 * * *", "timezone": "UTC", "skip_dates": [tomorrow]},
    )
    assert made.ok, made.text
    day = datetime.strptime(tomorrow, "%Y-%m-%d")
    assert f"  Skipped: {day.day} {day:%b %Y} is one of its skip dates." in made.text.splitlines()


def test_a_file_watch_says_no_runs(tmp_path) -> None:
    (tmp_path / "notes").mkdir()
    made = _make(TriggerStore(base_dir=tmp_path), {"paths": [str(tmp_path / "notes")]}, kind="file")
    assert made.ok, made.text
    assert "next runs" not in made.text
    assert "next_runs" not in made.data


def test_a_moved_cadence_says_its_new_runs_and_a_rename_does_not(tmp_path) -> None:
    store = TriggerStore(base_dir=tmp_path)
    made = _make(store, {"kind": "cron", "expr": "0 9 * * *", "timezone": "UTC"})
    trigger_id = made.data["trigger"]["id"]
    renamed = tools.update(store, trigger_id=trigger_id, patch={"name": "Morning summary"})
    assert renamed.ok, renamed.text
    assert "next runs" not in renamed.text and "next_runs" not in renamed.data
    moved = tools.update(
        store,
        trigger_id=trigger_id,
        patch={"spec": {"kind": "cron", "expr": "0 7 * * *", "timezone": "UTC"}},
    )
    assert moved.ok, moved.text
    assert all(r["wall"].endswith("07:00") for r in moved.data["next_runs"])
    assert any(said.startswith("  next runs:") for said in moved.text.splitlines())


def test_the_tool_tells_the_agent_to_say_them() -> None:
    from personalclaw.mcp_automation import _list_tools

    (create,) = [t for t in _list_tools() if t["name"] == "automation_create"]
    assert (
        "The result lists its next runs: tell the owner when it will run, and any skipped day."
        in create["description"]
    )
