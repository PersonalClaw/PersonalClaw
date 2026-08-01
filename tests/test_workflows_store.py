"""Run store — SQLite rows, the run directory, and corruption tolerance.

The split under test: SQLite holds only what queries need, and anything large (spec,
outputs, journal) lives in `runs/<id>/`. A journal can reach megabytes, so putting it in
a row would make every status poll pay for it.

Corruption tolerance is a real requirement rather than defensiveness. A crash mid-append
leaves a half-written final journal line, and a reader that refuses the whole file turns
one lost event into a lost run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.workflows import store as st
from personalclaw.workflows.models import (
    InstanceState,
    NodeInstance,
    OriginKind,
    RunBudget,
    RunOrigin,
    RunStatus,
    WorkflowRun,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch) -> Path:
    """Patch BOTH bindings: the store imported `config_dir` by value, so patching only
    the config module leaves the store pointed at the real home."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "config_dir", lambda: tmp_path)
    return tmp_path


def _run(**kw) -> WorkflowRun:
    base = {"id": "", "workflow_name": "research"}
    base.update(kw)
    return WorkflowRun(**base)  # type: ignore[arg-type]


class TestRunCrud:
    def test_create_assigns_an_id_and_makes_the_directory(self, isolated) -> None:
        run = st.create(_run())
        assert len(run.id) == 8
        assert st.run_dir(run.id).is_dir()
        assert run.created_at  # stamped on create

    def test_a_new_run_is_its_own_root(self) -> None:
        run = st.create(_run())
        assert run.root_run_id == run.id

    def test_round_trip_preserves_every_field(self) -> None:
        run = st.create(
            _run(
                status=RunStatus.RUNNING,
                intent="find the latency cause",
                origin=RunOrigin(kind=OriginKind.CHAT, session_key="s1", tool_call_id="t1"),
                budget=RunBudget(max_tokens=5000, max_cost=1.5, max_retries=2),
                inputs={"topic": "checkout"},
                pinned=True,
                total_tokens=1234,
            )
        )
        got = st.get(run.id)
        assert got is not None
        assert got.status is RunStatus.RUNNING
        assert got.intent == "find the latency cause"
        assert got.origin.kind is OriginKind.CHAT
        assert got.origin.session_key == "s1"
        assert got.budget.max_tokens == 5000
        assert got.budget.max_cost == 1.5
        assert got.inputs == {"topic": "checkout"}
        assert got.pinned is True
        assert got.total_tokens == 1234

    def test_save_updates_in_place(self) -> None:
        run = st.create(_run())
        run.status = RunStatus.COMPLETE
        run.total_tokens = 99
        st.save(run)
        rows, total = st.list_runs()
        assert total == 1  # updated, not duplicated
        assert rows[0].status is RunStatus.COMPLETE
        assert rows[0].total_tokens == 99

    def test_save_inserts_when_the_row_is_absent(self) -> None:
        """Upsert: a save after an out-of-band delete must not lose the run."""
        run = _run(id="deadbeef")
        st.save(run)
        assert st.get("deadbeef") is not None

    def test_get_missing_returns_none(self) -> None:
        assert st.get("nope1234") is None

    def test_delete_removes_the_row(self) -> None:
        run = st.create(_run())
        assert st.delete(run.id) is True
        assert st.get(run.id) is None
        assert st.delete(run.id) is False

    def test_delete_leaves_the_directory_for_the_retention_sweep(self) -> None:
        """Directory removal has to enumerate every sibling artifact kind and refuse
        paths escaping the run dir — more than a row delete should be doing."""
        run = st.create(_run())
        st.delete(run.id)
        assert st.run_dir(run.id).is_dir()

    def test_unknown_extra_columns_round_trip(self) -> None:
        run = _run(id="ex000001")
        run.extra = {"future_field": 7}
        st.save(run)
        assert st.get("ex000001").extra == {"future_field": 7}


class TestQueries:
    def test_run_tree_is_one_query(self) -> None:
        """`(root_run_id, status)` is indexed so a tree is a query, not a recursive walk."""
        parent = st.create(_run())
        for i in range(3):
            st.create(
                _run(
                    root_run_id=parent.id,
                    parent_run_id=parent.id,
                    branch_key=str(i),
                    workflow_name="sub",
                )
            )
        _, total = st.list_runs(root_run_id=parent.id)
        assert total == 4  # the parent plus its three children

    def test_filter_by_name_and_status(self) -> None:
        st.create(_run(workflow_name="a", status=RunStatus.COMPLETE))
        st.create(_run(workflow_name="a", status=RunStatus.FAILED))
        st.create(_run(workflow_name="b", status=RunStatus.COMPLETE))
        assert st.list_runs(workflow_name="a")[1] == 2
        assert st.list_runs(status=RunStatus.COMPLETE)[1] == 2
        assert st.list_runs(workflow_name="a", status="failed")[1] == 1

    def test_pagination_reports_the_unpaged_total(self) -> None:
        for _ in range(5):
            st.create(_run())
        rows, total = st.list_runs(limit=2)
        assert len(rows) == 2 and total == 5

    def test_active_runs_finds_what_crash_recovery_must_resume(self) -> None:
        st.create(_run(status=RunStatus.RUNNING))
        st.create(_run(status=RunStatus.NEEDS_INPUT))
        st.create(_run(status=RunStatus.PAUSED))
        st.create(_run(status=RunStatus.COMPLETE))
        st.create(_run(status=RunStatus.CANCELLED))
        assert len(st.active_runs()) == 3

    def test_count_for_def_backs_the_per_def_retention_cap(self) -> None:
        for _ in range(3):
            st.create(_run(workflow_name="noisy"))
        st.create(_run(workflow_name="quiet"))
        assert st.count_for_def("noisy") == 3
        assert st.count_for_def("absent") == 0


class TestRunDirectory:
    def test_spec_round_trip(self) -> None:
        run = st.create(_run())
        st.write_spec(run.id, {"root": {"kind": "sequence"}})
        assert st.read_spec(run.id) == {"root": {"kind": "sequence"}}

    def test_missing_spec_reads_as_none(self) -> None:
        assert st.read_spec("absent01") is None

    def test_corrupt_spec_reads_as_none_rather_than_raising(self) -> None:
        run = st.create(_run())
        (st.run_dir(run.id) / "spec.json").write_text("{not json", encoding="utf-8")
        assert st.read_spec(run.id) is None

    def test_spec_history_is_one_file_per_version(self) -> None:
        """Append-only by construction — a concurrent writer cannot corrupt a prior
        entry, because it never opens one."""
        run = st.create(_run())
        st.write_spec_history(run.id, 1, {"ops": [], "actor": "chat"})
        st.write_spec_history(run.id, 2, {"ops": [{"op": "skip"}], "actor": "user"})
        files = sorted(p.name for p in (st.run_dir(run.id) / "spec_history").iterdir())
        assert files == ["v001.json", "v002.json"]

    def test_state_round_trip(self) -> None:
        run = st.create(_run())
        st.write_state(
            run.id,
            {
                "root": NodeInstance(path="root", state=InstanceState.DONE, epoch=2, attempt=1),
                "root.children[0]": NodeInstance(
                    path="root.children[0]",
                    state=InstanceState.DEGRADED,
                    degraded_reason="no_token",
                ),
            },
        )
        got = st.read_state(run.id)
        assert got["root"].state is InstanceState.DONE
        assert got["root"].epoch == 2
        assert got["root.children[0]"].degraded_reason == "no_token"

    def test_missing_state_reads_as_empty_meaning_nothing_ran(self) -> None:
        """The safe interpretation: the frontier recomputes readiness from the spec."""
        assert st.read_state("absent01") == {}

    def test_corrupt_state_reads_as_empty(self) -> None:
        run = st.create(_run())
        (st.run_dir(run.id) / "state.json").write_text("{{{", encoding="utf-8")
        assert st.read_state(run.id) == {}

    def test_output_is_keyed_by_path_hash(self) -> None:
        """Node paths contain brackets and dots and can be long — hashing keeps them
        filesystem-safe, and the caller never reconstructs the hash itself."""
        run = st.create(_run())
        ref = st.write_output(run.id, "root.children[0].body", {"findings": [1, 2]})
        assert ref.startswith("outputs/") and ref.endswith(".json")
        assert st.read_output(run.id, "root.children[0].body") == {"findings": [1, 2]}

    def test_distinct_paths_do_not_collide(self) -> None:
        run = st.create(_run())
        st.write_output(run.id, "root.children[0]", "a")
        st.write_output(run.id, "root.children[1]", "b")
        assert st.read_output(run.id, "root.children[0]") == "a"
        assert st.read_output(run.id, "root.children[1]") == "b"

    def test_missing_output_reads_as_none(self) -> None:
        run = st.create(_run())
        assert st.read_output(run.id, "root.never") is None


class TestAppendOnlyLogs:
    def test_append_and_read(self) -> None:
        run = st.create(_run())
        st.append_jsonl(run.id, "journal.jsonl", {"e": "start"})
        st.append_jsonl(run.id, "journal.jsonl", {"e": "done"})
        assert [r["e"] for r in st.read_jsonl(run.id, "journal.jsonl")] == ["start", "done"]

    def test_a_corrupt_line_is_skipped_not_fatal(self) -> None:
        """A crash mid-append leaves a half-written final line. Dropping it is correct;
        refusing the file would turn one lost event into a lost run."""
        run = st.create(_run())
        st.append_jsonl(run.id, "journal.jsonl", {"e": "one"})
        with (st.run_dir(run.id) / "journal.jsonl").open("a", encoding="utf-8") as fh:
            fh.write('{"e": "trunc\n')
        st.append_jsonl(run.id, "journal.jsonl", {"e": "three"})
        assert [r["e"] for r in st.read_jsonl(run.id, "journal.jsonl")] == ["one", "three"]

    def test_non_dict_lines_are_skipped(self) -> None:
        run = st.create(_run())
        with (st.run_dir(run.id) / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write('"just a string"\n[1,2]\n')
        st.append_jsonl(run.id, "events.jsonl", {"e": "real"})
        assert [r["e"] for r in st.read_jsonl(run.id, "events.jsonl")] == ["real"]

    def test_missing_log_reads_as_empty(self) -> None:
        assert st.read_jsonl("absent01", "journal.jsonl") == []


class TestStickyCancel:
    def test_cancel_intent_survives_a_restart(self) -> None:
        """A cancel issued while the gateway is down must still be honoured — which is
        why the intent is a file rather than in-memory state."""
        run = st.create(_run())
        assert st.cancel_requested(run.id) is False
        st.request_cancel(run.id)
        assert st.cancel_requested(run.id) is True  # a fresh read, no cached flag

    def test_clear_is_idempotent(self) -> None:
        run = st.create(_run())
        st.request_cancel(run.id)
        st.clear_cancel(run.id)
        st.clear_cancel(run.id)  # must not raise on an absent file
        assert st.cancel_requested(run.id) is False
