"""A file dropped into a workflow run reaches the steps that bind ``{{run.dropped}}``, screened and
fenced with its name.

🔴 The defect (Backlog: "Workflow file drop: read_dropped_text, the only fenced read path, has no
callers"). A workflow that declares ``file_drop`` takes files on its run page behind an approval,
and stored and listed them, but nothing handed one to a step: ``filedrop.read_dropped_text`` had no
caller, so a file the owner dropped into a run reached no step, and the app permission's "the run
reads it only after you approve" described a read that never happened.

Now a step's ``{{run.dropped}}`` holds each file dropped into its run, oldest first: its text read
by the injection screen and fenced with its name (``outside_text.admit``), or a sentence saying
why it is not there. It is read when a step that binds it starts, so a file dropped while one step
runs reaches the next. A workflow that declares no drop has no ``run.dropped``, and a step that
binds it fails saying why.

Driven as the product runs it: a run's controller, a ``stage`` step whose spawned helper records
the task it was handed, and files dropped through the run's own drop
(``service.accept_dropped_file``).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from test_the_injection_screen_passes_ordinary_text_and_refuses_a_take_over import (
    ORDINARY,
    REFUSED,
    TAKE_OVER,
)

from personalclaw.security import outside_fences
from personalclaw.workflows import filedrop, service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.security

#: The take-overs the screen refuses outright, from its own table.
REFUSED_TEXTS = {name: text for name, (tier, text) in TAKE_OVER.items() if tier == REFUSED}

READS_THE_DROP = "Work from what the owner dropped into this run:\n{{run.dropped}}"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    folder = tmp_path / "workspace"
    folder.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.default_workspace_dir", lambda: str(folder))
    return home


class _Info:
    def __init__(self, agent_id: str, task: str) -> None:
        self.id = agent_id
        self.prompt = task
        self.cwd = ""
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False
        self.agent = ""


class _Helpers:
    """The helpers a run's steps spawn: each records the task it was handed and is done at once.
    *during* runs while the first helper works, as the owner dropping a file meanwhile."""

    def __init__(self, during=None) -> None:
        self.tasks: list[str] = []
        self.infos: dict[str, _Info] = {}
        self.during = during

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(f"sub{len(self.infos) + 1}", str(kw.get("task") or ""))
        self.infos[info.id] = info
        self.tasks.append(info.prompt)
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None and not info.done:
            if self.during is not None and len(self.tasks) == 1:
                self.during()
            info.done = True
            info.result = json.dumps({"summary": "done"})
        return info


def _stage(node_id: str, prompt: str) -> dict[str, Any]:
    return {"kind": "stage", "id": node_id, "config": {"prompt": prompt}}


def _run(*prompts: str, file_drop: Any = True) -> WorkflowRun:
    spec: dict[str, Any] = {
        "name": "drop-reader",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [_stage(f"step{i}", p) for i, p in enumerate(prompts)],
        },
    }
    if file_drop is not None:
        spec["file_drop"] = file_drop
    run = store.create(WorkflowRun(id="", workflow_name="drop-reader"))
    store.write_spec(run.id, spec)
    return run


def _drop(run: WorkflowRun, name: str, data: bytes, mime: str = "text/plain") -> dict[str, Any]:
    result = service.accept_dropped_file(
        run.id, filename=name, data=data, mime=mime, confirmed=True
    )
    assert result.get("ok"), result
    return result


def _drive(run: WorkflowRun, helpers: _Helpers) -> RunStatus:
    controller = RunController(
        run, store.read_spec(run.id), services=EngineServices(subagents=helpers)
    )
    return asyncio.run(controller.run_to_completion(timeout=30.0))


def _spans(text: str) -> list[tuple[str, str]]:
    out = []
    for part in text.split("<untrusted_content")[1:]:
        attrs, _, rest = part.partition(">")
        out.append((attrs, rest.split("</untrusted_content>", 1)[0]))
    return out


def assert_fenced(handed: str, text: str, *, name: str, what: str = "") -> None:
    """*text* reached the step inside a fence naming the dropped file, and none of it outside."""
    assert any(
        f"source=dropped file {name}" in attrs and text in body for attrs, body in _spans(handed)
    ), f"{what}: not inside a fence from the dropped file {name}:\n{handed[:2000]}"
    for line in text.splitlines():
        if len(line.strip()) > 3:
            assert line not in outside_fences(handed), f"{what}: {line!r} outside a fence"


def test_a_dropped_file_reaches_the_step_that_binds_the_drop_fenced_with_its_name():
    """🔴 Before: nothing read the drop, so the step was handed no word of the file."""
    run = _run(READS_THE_DROP)
    _drop(run, "notes.txt", b"The vendor's renewal is due on 2026-12-01.\nBudget: 4,000.")
    helpers = _Helpers()
    assert _drive(run, helpers) is RunStatus.COMPLETE, store.get(run.id)
    (task,) = helpers.tasks
    assert "The files dropped into this run (1):" in task, task
    assert_fenced(
        task, "The vendor's renewal is due on 2026-12-01.\nBudget: 4,000.", name="notes.txt"
    )


def test_every_ordinary_text_reaches_the_step_fenced():
    run = _run(READS_THE_DROP)
    for index, text in enumerate(ORDINARY.values()):
        _drop(run, f"ordinary{index}.txt", text.encode())
    helpers = _Helpers()
    assert _drive(run, helpers) is RunStatus.COMPLETE, store.get(run.id)
    (task,) = helpers.tasks
    for index, (name, text) in enumerate(ORDINARY.items()):
        assert_fenced(task, text, name=f"ordinary{index}.txt", what=name)


def test_a_dropped_file_the_screen_refuses_never_reaches_the_step():
    """The screen reads a dropped file before any model does; a take-over is kept as nothing, and
    what stands in for it names the pattern class and never the words."""
    run = _run(READS_THE_DROP)
    for index, text in enumerate(REFUSED_TEXTS.values()):
        _drop(run, f"refused{index}.txt", text.encode())
    helpers = _Helpers()
    assert _drive(run, helpers) is RunStatus.COMPLETE, store.get(run.id)
    (task,) = helpers.tasks
    for index, (name, text) in enumerate(REFUSED_TEXTS.items()):
        for line in text.splitlines():
            if len(line.strip()) > 3:
                assert line not in task, f"{name}: {line!r} reached the step"
        assert f"[The dropped file refused{index}.txt was withheld: the injection screen" in task
    assert (filedrop.drop_dir(run.id) / "refused0.txt").read_bytes() == next(
        iter(REFUSED_TEXTS.values())
    ).encode(), "the bytes on disk stay what the owner handed over"


def test_a_file_dropped_while_a_step_runs_reaches_the_next_step():
    run = _run(READS_THE_DROP, READS_THE_DROP)
    helpers = _Helpers(during=lambda: _drop(run, "late.md", b"Use the March figures, not May."))
    assert _drive(run, helpers) is RunStatus.COMPLETE, store.get(run.id)
    first, second = helpers.tasks
    assert filedrop.NOTHING_DROPPED in first and "March figures" not in first, first
    assert_fenced(second, "Use the March figures, not May.", name="late.md")


def test_only_a_step_that_binds_the_drop_reads_it(monkeypatch):
    run = _run("Plan the week.", READS_THE_DROP)
    _drop(run, "notes.txt", b"Ship the report on Friday.")
    reads: list[str] = []
    real = filedrop.dropped_for_steps

    def counted(run_id: str, spec: dict | None) -> str | None:
        reads.append(run_id)
        return real(run_id, spec)

    monkeypatch.setattr(filedrop, "dropped_for_steps", counted)
    helpers = _Helpers()
    assert _drive(run, helpers) is RunStatus.COMPLETE, store.get(run.id)
    plan, reader = helpers.tasks
    assert "Ship the report" not in plan and "Ship the report" in reader
    assert reads == [run.id], "read once, for the step that binds it"


def test_a_file_that_is_not_text_is_named_and_an_empty_one_said_so():
    run = _run(READS_THE_DROP)
    _drop(run, "chart.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", mime="image/png")
    _drop(run, "frame.bin", b"GIF89a\x00\x01\x00\x01 frame data", mime="image/gif")
    _drop(run, "blank.txt", b"")
    helpers = _Helpers()
    assert _drive(run, helpers) is RunStatus.COMPLETE, store.get(run.id)
    (task,) = helpers.tasks
    assert "[chart.png is not text, so only its name is handed on.]" in task
    assert "[frame.bin is not text, so only its name is handed on.]" in task, "UTF-8 with NULs"
    assert "[blank.txt is empty.]" in task
    assert "PNG" not in task.replace("chart.png", "") and "frame data" not in task


def test_the_drop_hands_a_step_a_bounded_share_and_names_what_it_left_out(monkeypatch):
    """Oldest first: the share goes to the files handed over first, each a second apart here."""
    monkeypatch.setattr(filedrop, "MAX_DROPPED_CHARS", 3_000)
    stamps = iter(f"2026-11-22T10:00:0{s}Z" for s in range(9, -1, -1))
    monkeypatch.setattr(service, "_now", lambda: next(stamps))
    run = _run(READS_THE_DROP)
    # Dropped last first, so their order on disk is the reverse of the order they were handed in.
    for index in (3, 2, 1, 0):
        _drop(run, f"part{index}.txt", (f"section {index} " * 200).encode())
    helpers = _Helpers()
    assert _drive(run, helpers) is RunStatus.COMPLETE, store.get(run.id)
    (task,) = helpers.tasks
    assert "section 0" in task and "[part0.txt: its first" not in task
    assert "[part1.txt: its first" in task, "the file the share ends in is cut, and says so"
    assert "section 1" in task and "section 1" not in outside_fences(task), "a cut is fenced too"
    for later in ("part2.txt", "part3.txt"):
        assert f"[{later} was left out: the files before it hold all the text" in task
    assert "section 3" not in task


def test_a_workflow_that_takes_no_files_has_no_drop_and_its_step_says_why():
    run = _run(READS_THE_DROP, file_drop=None)
    helpers = _Helpers()
    assert _drive(run, helpers) is RunStatus.FAILED
    assert helpers.tasks == [], "the step never started"
    said = json.dumps(store.get(run.id).to_dict())
    assert "declares `file_drop`" in said, said[:3000]


def test_a_file_dropped_as_the_drops_own_manifest_keeps_both():
    """A file named as the drop's manifest overwrote it, and the manifest's next write overwrote
    the file."""
    run = _run(READS_THE_DROP)
    _drop(run, "notes.txt", b"First file.")
    stored = _drop(run, "manifest.json", b'{"budget": 4000}')["file"]
    assert stored["filename"] == "dropped_manifest.json", stored
    names = sorted(row["filename"] for row in filedrop.read_manifest(run.id))
    assert names == ["dropped_manifest.json", "notes.txt"], names
    helpers = _Helpers()
    assert _drive(run, helpers) is RunStatus.COMPLETE, store.get(run.id)
    (task,) = helpers.tasks
    assert_fenced(task, "First file.", name="notes.txt")
    assert_fenced(task, '{"budget": 4000}', name="dropped_manifest.json")


def test_a_run_that_keeps_a_document_and_takes_no_files_says_which_declaration_it_lacks():
    """The `run` root is there (the run keeps a document), so the read fails at the field, and
    its sentence names what gives a run one rather than a spelling to check."""
    from personalclaw.workflows.bindings import BindingContext, BindingError, resolve

    ctx = BindingContext(run_document="/runs/r1/documents/NOTES.md")
    assert resolve("{{run.document}}", ctx) == "/runs/r1/documents/NOTES.md"
    with pytest.raises(BindingError) as refused:
        resolve("{{run.dropped}}", ctx)
    said = str(refused.value) + " " + str(getattr(refused.value, "remediation", ""))
    assert "for a run whose workflow declares `file_drop`" in said, said
