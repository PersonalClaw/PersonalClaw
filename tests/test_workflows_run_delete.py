"""Deleting a run, and the per-item foreach projection (Slice 8c).

**Delete.** Two things a run delete has to get right, both of which a naive `DELETE FROM runs`
gets wrong:

* it must refuse a run that can still MOVE. The engine's single-writer discipline (WF2-R10)
  assumes the row outlives its writer — delete a live run and its controller keeps writing
  journal entries and a terminal status to a row that is gone. Cancel then delete: two steps for
  two genuinely different intents.
* it must remove the run DIRECTORY too. A row-only delete leaves the journal, the outputs and —
  worst — live continuation tokens on disk forever, invisible to every surface. The run looks
  gone while still holding a valid resume link.

**Per-item foreach context.** A twelve-item fan-out renders as twelve rows distinguishable only
by an index suffix, which cannot answer "which item is stuck?". The projection carries
`item_index`/`item_total`/`item_label` so a row can read `[3/12] auth.py`.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController, _item_label
from personalclaw.workflows.models import NodeInstance, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: home, raising=False)
    return home


SPEC = {
    "name": "del",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [{"kind": "transform", "id": "a", "config": {"expr": {"n": 1}}}],
    },
}


def _terminal_run(status: RunStatus = RunStatus.COMPLETE) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name="del"))
    store.write_spec(run.id, SPEC)
    store.write_state(run.id, {"root.children[0]": NodeInstance(path="root.children[0]")})
    run.status = status
    store.save(run)
    return run


class TestDeleteRefusals:
    async def test_an_unknown_run_is_not_found(self) -> None:
        result = await service.delete_run("nope")
        assert result["ok"] is False and result["code"] == "WF_RUN_NOT_FOUND"

    async def test_a_RUNNING_run_is_refused(self) -> None:
        """The single-writer invariant: a live controller would keep writing to a row that no
        longer exists. The message names the fix rather than just refusing."""
        run = _terminal_run(RunStatus.RUNNING)
        result = await service.delete_run(run.id)
        assert result["ok"] is False
        assert result["code"] == "WF_RUN_NOT_TERMINAL"
        assert "cancel" in result["message"].lower()
        assert store.get(run.id) is not None

    async def test_a_NEEDS_INPUT_run_is_refused(self) -> None:
        """Waiting, not finished — and it holds a live resume token somebody may still use."""
        run = _terminal_run(RunStatus.NEEDS_INPUT)
        assert (await service.delete_run(run.id))["code"] == "WF_RUN_NOT_TERMINAL"

    async def test_a_PAUSED_run_is_refused(self) -> None:
        run = _terminal_run(RunStatus.PAUSED)
        assert (await service.delete_run(run.id))["code"] == "WF_RUN_NOT_TERMINAL"

    @pytest.mark.parametrize(
        "status",
        [RunStatus.COMPLETE, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.ESCALATED],
    )
    async def test_every_terminal_status_is_deletable(self, status: RunStatus) -> None:
        run = _terminal_run(status)
        assert (await service.delete_run(run.id))["ok"] is True


class TestDeleteEffects:
    async def test_the_row_and_the_directory_both_go(self) -> None:
        """A row-only delete would leave the journal, outputs and continuation tokens on disk
        forever — invisible to every surface, and still a valid resume link."""
        run = _terminal_run()
        run_dir = store.run_dir(run.id)
        assert run_dir.is_dir(), "expected artifacts to exist before the delete"

        assert (await service.delete_run(run.id))["deleted"] is True
        assert store.get(run.id) is None
        assert not run_dir.exists()

    async def test_it_closes_the_runs_open_inbox_rows(self) -> None:
        """A gate open when the run was cancelled would otherwise outlive the run entirely and
        be unanswerable forever."""
        from personalclaw.inbox import InboxItem, InboxStore, ItemKind

        run = _terminal_run(RunStatus.CANCELLED)
        inbox = InboxStore()
        inbox.load()
        inbox.add(
            InboxItem(
                id="needs_input-x",
                channel="",
                channel_name="",
                thread_ts=None,
                message="Ship?",
                sender_id="",
                sender_name="",
                item_kind=ItemKind.NEEDS_INPUT.value,
                refs={"workflow": run.id, "workflow_node": "gate"},
            )
        )
        inbox.save()

        await service.delete_run(run.id)
        after = InboxStore()
        after.load()
        assert after.items["needs_input-x"].status != "pending"

    async def test_a_deleted_run_is_gone_from_the_list(self) -> None:
        run = _terminal_run()
        await service.delete_run(run.id)
        rows, total = store.list_runs()
        assert all(r.id != run.id for r in rows)
        assert total == 0

    async def test_the_supervisors_controller_is_dropped(self) -> None:
        """Nothing may hold a handle to a run whose row is about to disappear, or the next poll
        would try to reconcile a run that no longer exists."""

        class _Supervisor:
            def __init__(self) -> None:
                self.forgotten: list[str] = []
                self.held: dict[str, object] = {}

            def controller(self, run_id: str):
                return self.held.get(run_id)

            def forget(self, run_id: str) -> bool:
                self.forgotten.append(run_id)
                return self.held.pop(run_id, None) is not None

        run = _terminal_run()
        sup = _Supervisor()
        sup.held[run.id] = object()
        assert (await service.delete_run(run.id, supervisor=sup))["ok"] is True
        assert sup.forgotten == [run.id]

    async def test_a_supervisor_that_cannot_forget_does_not_block_the_delete(self) -> None:
        """A registry failure must not strand a run the user asked to remove."""

        class _Broken:
            def controller(self, run_id: str):
                return object()

            def forget(self, run_id: str):
                raise RuntimeError("registry is wedged")

        run = _terminal_run()
        assert (await service.delete_run(run.id, supervisor=_Broken()))["ok"] is True
        assert store.get(run.id) is None

    async def test_deleting_twice_is_not_an_error_the_second_time(self) -> None:
        """It is a 404, not a 500: the state the caller wanted is the state that exists."""
        run = _terminal_run()
        await service.delete_run(run.id)
        assert (await service.delete_run(run.id))["code"] == "WF_RUN_NOT_FOUND"


class TestForeachItemLabels:
    def test_a_dict_item_prefers_a_named_field(self) -> None:
        """A fan-out over records is the common case, and `{"path": "auth.py", …}` should read
        as `auth.py` rather than as its JSON."""
        assert _item_label({"path": "auth.py", "sha": "deadbeef"}) == "auth.py"
        assert _item_label({"name": "step one"}) == "step one"
        assert _item_label({"label": "explicit"}) == "explicit"
        assert _item_label({"title": "a title"}) == "a title"

    def test_a_dict_with_no_named_field_summarizes_its_keys(self) -> None:
        label = _item_label({"alpha": 1, "beta": 2, "gamma": 3, "delta": 4})
        assert "alpha=1" in label
        assert "delta" not in label, "only the first few keys — a row shows one line"

    def test_a_scalar_is_its_own_label(self) -> None:
        assert _item_label("auth.py") == "auth.py"
        assert _item_label(42) == "42"

    def test_a_container_reports_its_SIZE_not_its_contents(self) -> None:
        """A list's contents are not a label; its length is the only honest one-line summary."""
        assert _item_label([1, 2, 3]) == "3 items"

    def test_a_long_label_is_clipped(self) -> None:
        label = _item_label("x" * 500)
        assert len(label) <= 60 and label.endswith("…")

    def test_newlines_are_flattened(self) -> None:
        """A newline inside a row breaks the layout."""
        assert "\n" not in _item_label("first line\nsecond line")

    def test_none_has_no_label(self) -> None:
        assert _item_label(None) == ""


class TestForeachProjection:
    FAN = {
        "name": "fan",
        "root": {
            "kind": "foreach",
            "id": "loop",
            "config": {"items": [{"path": "a.py"}, {"path": "b.py"}, {"path": "c.py"}]},
            "body": {"kind": "transform", "id": "item", "config": {"expr": "{{item.path}}"}},
        },
    }

    async def test_a_fan_out_projects_index_total_and_label(self) -> None:
        """What "[2/3] b.py" needs. Without it the three rows differ only by a path suffix."""
        run = store.create(WorkflowRun(id="", workflow_name="fan"))
        store.write_spec(run.id, self.FAN)
        c = RunController(run, self.FAN, services=EngineServices())
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE

        rows = {r["instance_path"]: r for r in service.status(run.id)["nodes"]}
        body = [r for p, r in rows.items() if "#" in p]
        assert len(body) == 3
        assert {r["item_index"] for r in body} == {0, 1, 2}
        assert all(r["item_total"] == 3 for r in body)
        assert {r.get("item_label") for r in body} == {"a.py", "b.py", "c.py"}

    async def test_a_non_iterated_node_carries_no_item_fields(self) -> None:
        """An `item_index` on a lone node would render "[1/1]", which is noise."""
        run = store.create(WorkflowRun(id="", workflow_name="del"))
        store.write_spec(run.id, SPEC)
        c = RunController(run, SPEC, services=EngineServices())
        await c.run_to_completion(timeout=20)
        for row in service.status(run.id)["nodes"]:
            assert "item_index" not in row, row

    async def test_the_label_SURVIVES_a_reload(self) -> None:
        """It is persisted on the instance because the items list is re-resolved from a binding:
        after an upstream output changes, the label would otherwise be unrecoverable — and a
        retry must show the item it originally got, not whatever now sits at that index."""
        run = store.create(WorkflowRun(id="", workflow_name="fan"))
        store.write_spec(run.id, self.FAN)
        c = RunController(run, self.FAN, services=EngineServices())
        await c.run_to_completion(timeout=20)

        # Re-read from DISK, with no controller in memory.
        reloaded = store.read_state(run.id)
        labels = {i.item_label for p, i in reloaded.items() if "#" in p}
        assert labels == {"a.py", "b.py", "c.py"}

    async def test_the_projection_still_validates(self) -> None:
        """The new fields are in the projection's field table, so a fan-out snapshot is not
        suddenly "invalid" for carrying them."""
        from personalclaw.workflows.projection import project

        run = store.create(WorkflowRun(id="", workflow_name="fan"))
        store.write_spec(run.id, self.FAN)
        c = RunController(run, self.FAN, services=EngineServices())
        await c.run_to_completion(timeout=20)
        _snap, issues = project(run.id)
        assert issues == []

    async def test_node_started_publishes_the_item_context(self) -> None:
        """So a widget can label a row the moment it starts, not only after the snapshot."""
        events: list[tuple[str, dict]] = []
        run = store.create(WorkflowRun(id="", workflow_name="fan"))
        store.write_spec(run.id, self.FAN)
        c = RunController(
            run,
            self.FAN,
            services=EngineServices(publish=lambda e, p: events.append((e, p))),
        )
        await c.run_to_completion(timeout=20)
        started = [
            p
            for e, p in events
            if e == "workflow_node_started" and "#" in p.get("instance_path", "")
        ]
        assert started
        assert all("item_index" in p for p in started)
        assert {p.get("item_label") for p in started} == {"a.py", "b.py", "c.py"}
