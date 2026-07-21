"""Unified Loop store (Slice 2). One table + per-id dir serving every kind via a
lean schema (shared columns + JSON list/dict columns + a kind_config blob)."""

from __future__ import annotations

import json

import pytest

from personalclaw.loop import store
from personalclaw.loop.loop import Loop, LoopStatus


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    return tmp_path


def _goal(**over):
    base = dict(
        id="",
        name="G",
        kind="goal",
        task="investigate the latency regression",
        project_id="p-1",
        kind_config={"goal_type": "open_ended", "granularity": "balanced"},
    )
    base.update(over)
    return store.create(Loop(**base))


def _code(**over):
    base = dict(
        id="",
        name="C",
        kind="code",
        task="add oauth login to the app",
        project_id="p-1",
        kind_config={"entry_stage": "design", "queued_task_ids": []},
    )
    base.update(over)
    return store.create(Loop(**base))


class TestCrud:
    def test_create_assigns_id_and_persists_kind_config(self):
        g = _goal()
        assert store.valid_loop_id(g.id)
        got = store.get(g.id)
        assert got.kind == "goal"
        assert got.kind_config["goal_type"] == "open_ended"

    def test_create_rejects_unknown_kind(self):
        with pytest.raises(ValueError):
            store.create(Loop(id="", name="x", kind="nope", task="t" * 12))

    def test_list_and_list_for_project(self):
        g, c = _goal(), _code()
        _goal(project_id="p-2")
        assert {lp.id for lp in store.list_all()} >= {g.id, c.id}
        assert {lp.id for lp in store.list_for_project("p-1")} == {g.id, c.id}

    def test_list_for_project_matches_tasks_project_id(self):
        # A loop bound to a project only via tasks_project_id (a project-less launch's
        # auto-provisioned backing project, or a task-provisioning code loop) must show
        # in the project's loop history — same as /linked. Was project_id-only.
        explicit = _goal(project_id="p-9")
        provisioned = _code(project_id="")
        store.set_tasks_links(provisioned.id, tasks_project_id="p-9", task_list_ids={})
        ids = {lp.id for lp in store.list_for_project("p-9")}
        assert ids == {explicit.id, provisioned.id}
        # blank project id never matches everything
        assert store.list_for_project("") == []

    def test_delete_removes_row_and_dir(self):
        g = _goal()
        d = store.loop_dir(g.id)
        assert d.exists()
        assert store.delete(g.id) is True
        assert store.get(g.id) is None
        assert not d.exists()


class TestStatusTransitions:
    def test_banks_elapsed_on_leaving_running(self):
        g = _goal()
        store.update_status(g.id, LoopStatus.RUNNING)
        store.update_status(g.id, LoopStatus.PAUSED)
        assert store.get(g.id).elapsed_seconds >= 0.0
        assert store.get(g.id).started_at is not None

    def test_terminal_is_frozen(self):
        g = _goal()
        store.update_status(g.id, LoopStatus.COMPLETE)
        with pytest.raises(store.TransitionError):
            store.update_status(g.id, LoopStatus.RUNNING)

    def test_status_mirrors_to_status_json(self):
        g = _goal()
        store.update_status(g.id, LoopStatus.RUNNING)
        sj = json.loads((store.safe_loop_dir(g.id) / "status.json").read_text())
        assert sj["status"] == "running"


class TestSpecEditFreeze:
    def test_update_spec_allowed_prelaunch(self):
        c = _code()  # READY = prelaunch
        store.update_spec(
            c.id, {"task": "add oauth + SSO", "kind_config": {"entry_stage": "implementation"}}
        )
        got = store.get(c.id)
        assert got.task == "add oauth + SSO"
        assert got.kind_config["entry_stage"] == "implementation"

    def test_update_spec_frozen_after_start(self):
        c = _code()
        store.update_status(c.id, LoopStatus.RUNNING)
        assert store.update_spec(c.id, {"task": "should not change"}) is None
        assert store.get(c.id).task == "add oauth login to the app"

    def test_rename_works_in_any_state(self):
        c = _code()
        store.update_status(c.id, LoopStatus.RUNNING)  # spec frozen
        store.rename(c.id, "Renamed while running")
        assert store.get(c.id).name == "Renamed while running"


class TestKindConfigQueue:
    def test_queue_unqueue_in_kind_config(self):
        c = _code()
        store.queue_tasks(c.id, ["t-a", "t-b"])
        store.unqueue_tasks(c.id, ["t-a"])
        assert store.get(c.id).kind_config["queued_task_ids"] == ["t-b"]

    def test_queue_dedupes_and_preserves_sibling_keys(self):
        # queue_tasks mutates only queued_task_ids — sibling kind_config keys
        # (entry_stage, execution_plan, …) must survive the round-trip, and dupes drop.
        c = _code(
            kind_config={
                "entry_stage": "design",
                "queued_task_ids": [],
                "execution_plan": [{"role": "impl"}],
            }
        )
        store.queue_tasks(c.id, ["t-a", "t-a", "t-b"])
        cfg = store.get(c.id).kind_config
        assert cfg["queued_task_ids"] == ["t-a", "t-b"]  # deduped
        assert cfg["entry_stage"] == "design"  # sibling preserved
        assert cfg["execution_plan"] == [{"role": "impl"}]  # nested sibling preserved


class TestFileHelpers:
    def test_findings_round_trip_and_attribution(self):
        g = _goal()
        d = store.loop_dir(g.id)
        (d / "findings" / "cycle_001.json").write_text(json.dumps({"cycle": 1, "summary": "did x"}))
        (d / "findings" / "task_t-abc_001.json").write_text(
            json.dumps({"cycle": 1, "summary": "task work"})
        )
        f = store.get_findings(g.id)
        assert f[0]["summary"] == "did x"
        # task finding gets its task_id derived from the filename
        assert any(x.get("task_id") == "t-abc" for x in f)

    def test_nudges_applied_stamp(self):
        g = _goal()
        store.append_nudge(g.id, "focus on the db path", 0)
        store.mark_nudges_applied(g.id, 1)
        assert store.get_nudges(g.id)[0]["applied_cycle"] == 1

    def test_per_task_guidance_round_trip(self):
        c = _code()
        store.write_task_guidance(c.id, "t-abc", "prefer pure fns")
        assert store.read_task_guidance(c.id, "t-abc") == "prefer pure fns"
        store.clear_task_guidance(c.id, "t-abc")
        assert store.read_task_guidance(c.id, "t-abc") == ""

    def test_question_round_trip_redacts(self):
        c = _code()
        store.write_question(
            c.id, "Postgres or SQLite?", why="the key AKIAIOSFODNN7EXAMPLE implies scale"
        )
        q = store.pending_question(c.id)
        assert q["question"] == "Postgres or SQLite?"
        assert "AKIAIOSFODNN7EXAMPLE" not in q["why"]


class TestRedactedView:
    def test_get_redacted_attaches_findings_nudges_question(self):
        g = _goal()
        red = store.get_redacted(g.id)
        assert red["kind"] == "goal"
        assert "findings" in red and "nudges" in red and "pending_question" in red
        # files_dir — the cockpit roots its file tree + terminal here for no-workspace
        # (doc-producing) loops; must be present + point at the loop's on-disk dir.
        assert red["files_dir"] and red["files_dir"].endswith(g.id)

    def test_read_deliverable_and_log(self):
        g = _goal()  # open_ended → REPORT.md is the deliverable
        d = store.loop_dir(g.id)
        (d / "REPORT.md").write_text("# Report\nThe findings.")
        (d / "FINDINGS.md").write_text("cycle 1: did x")
        assert "The findings." in store.read_deliverable(g.id)
        assert "did x" in store.read_log(g.id)

    def test_read_deliverable_falls_back_when_no_named_doc(self):
        g = _goal()
        (store.loop_dir(g.id) / "FINDINGS.md").write_text("only the log exists")
        # no REPORT.md yet → falls back across known docs to FINDINGS.md
        assert "only the log" in store.read_deliverable(g.id)

    def test_get_redacted_attaches_verdicts_and_marginal_scores(self):
        g = _goal()
        store.write_verdict(g.id, 1, {"cycle": 1, "done": False, "marginal_value": 2.5})
        store.record_marginal_score(g.id, 2.5)
        red = store.get_redacted(g.id)
        # the cockpit ROI rail reads these off the redacted view for the initial render
        assert red["verdicts"] and red["verdicts"][0]["marginal_value"] == 2.5
        assert red["marginal_scores"] == [2.5]

    def test_list_redacted_attaches_findings_and_filters(self):
        g = _goal()
        (store.loop_dir(g.id) / "findings" / "cycle_001.json").write_text(
            json.dumps({"cycle": 1, "summary": "found it"})
        )
        _goal(project_id="p-2")
        rows = store.list_redacted()
        row = next(r for r in rows if r["id"] == g.id)
        # findings attached so list cards show count + latest-insight (parity w/ detail)
        assert row["findings"] and row["findings"][-1]["summary"] == "found it"
        # project + kind filters
        assert {r["id"] for r in store.list_redacted(project_id="p-1")} == {g.id}
        assert all(r["kind"] == "code" for r in store.list_redacted(kind="code"))


class TestReapOrphans:
    def test_reaps_dir_with_no_row(self, tmp_path):
        # a valid-id dir with no backing row is GC'd
        orphan = tmp_path / "loop" / "abcdef12"
        orphan.mkdir(parents=True)
        (orphan / "status.json").write_text("{}")
        assert store.reap_orphan_dirs() >= 1
        assert not orphan.exists()

    def test_reap_spares_live_dirs_db_and_non_id_entries(self, tmp_path):
        # A live loop's dir survives; the DB file + a non-id-shaped entry are never
        # touched (only valid_loop_id-shaped dirs are candidates).
        g = _goal()
        live_dir = store.loop_dir(g.id)
        root = tmp_path / "loop"
        (root / "loops.db").write_text("x") if not (root / "loops.db").exists() else None
        (root / "not-a-loop-id").mkdir(exist_ok=True)
        orphan = root / "deadbeef"
        orphan.mkdir()
        store.reap_orphan_dirs()
        assert live_dir.exists()  # backing row → spared
        assert (root / "not-a-loop-id").exists()  # wrong shape → never a candidate
        assert not orphan.exists()  # valid id, no row → reaped
