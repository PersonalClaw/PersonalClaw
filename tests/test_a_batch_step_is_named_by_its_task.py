"""A batch's steps are named by their tasks' own titles, never by a slug of a path.

Measured on a running gateway: a loop handed ``subagent_run`` two tasks whose text opened with the
repository they were to read ("Repo: /home/user/src/feedsmith. Read only …"). Each step's id was
cut from the first four words of that text, and that id was the only name the step had, so the run
page, the run's ending and the Inbox asks all called the steps "repo_users_…_0" and
"repo_users_…_1": a slug of her own home folder, which told her nothing about what she was asked to
approve.

Each task now declares a short ``title``, and that is its step's name (the node's ``label``) on
every surface, and what its id is cut from. A task with no title is named by the start of what it
is FOR (its objective) — prose the author wrote for a reader — and never by the task's text.
"""

from __future__ import annotations

from personalclaw.dashboard.approval_state import _who_asked
from personalclaw.workflows import store
from personalclaw.workflows.batch_compile import compile_batch, leaf_from_item, leaf_item_schema
from personalclaw.workflows.models import WorkflowRun

#: The task text the measured batch sent, with an invented home folder.
TASK = (
    "Repo: /home/user/src/feedsmith. Read only REPORT.md lines 12-36 and src/feedsmith/parse.py "
    "lines 20-30, and check the four release-note lines against the code."
)
CONTRACT = {
    "objective": "Independently audit four reworked release-note lines against the source",
    "output_format": "A JSON list of verdicts, one per line, each with its evidence",
    "boundary": "Read only; change no file in the repository",
}


def _item(**over) -> dict:
    return {"task": TASK, **CONTRACT, **over}


def _children(*items: dict) -> list[dict]:
    result = compile_batch([leaf_from_item(i) for i in items])
    assert result.compiled and result.ok, [f.to_dict() for f in result.findings]
    return result.spec["root"]["children"]


def test_a_titled_task_names_its_step_by_its_title():
    first, second = _children(
        _item(title="Audit the parser lines"), _item(title="Audit the migration notes")
    )
    assert (first["label"], second["label"]) == (
        "Audit the parser lines",
        "Audit the migration notes",
    )
    assert (first["id"], second["id"]) == (
        "audit_the_parser_lines_0",
        "audit_the_migration_notes_1",
    )


def test_no_step_is_named_from_the_tasks_text():
    for child in _children(_item(), _item(title="Audit the parser lines")):
        for name in (child["id"], child["label"]):
            assert "home" not in name and "user" not in name and "repo" not in name.lower(), name


def test_an_untitled_task_is_named_by_what_it_is_for():
    first, second = _children(_item(), _item())
    assert first["label"] == "Independently audit four reworked release-note lines…"
    assert first["id"] == "independently_audit_four_reworked_0"
    assert second["id"] == "independently_audit_four_reworked_1", "two steps share one id"


def test_the_tool_shows_a_model_the_title_it_may_give():
    schema = leaf_item_schema()
    assert schema["properties"]["title"]["type"] == "string"
    assert "title" not in schema["required"], "a task without a title is still a task"
    assert not leaf_from_item(_item(title="Audit the parser lines")).unreadable


def test_the_inbox_asks_about_a_step_by_its_name():
    run = store.create(WorkflowRun(id="", workflow_name="subagent-batch-1"))
    (child,) = _children(_item(title="Audit the parser lines"), _item())[:1]
    store.write_spec(
        run.id,
        {
            "name": "subagent-batch-1",
            "root": {"kind": "parallel", "id": "batch", "children": [child]},
        },
    )
    entry = {"session": f"workflow:{run.id}:{child['id']}"}
    assert _who_asked(entry) == "The “Audit the parser lines” step of a workflow run"
