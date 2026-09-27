"""A goal monitor run keeps no document, and its run page says so rather than waiting for one.

A run's page names the document its worker keeps, read off the loop kind the template belongs to.
For ``goal-pursuit-monitor`` that was the goal kind's monitor variant, which says ``MONITOR_LOG.md``
— and nothing in the template ever asks for one. Measured on ``main``: a monitor run's page showed
a ``MONITOR_LOG.md`` that was "not written yet", and added that waiting would not produce it. Each
wake of the monitor is a fresh step with no directory of its own to keep a log in; its checks and
its close-out report ARE its output.

So the template states it (``"document": ""``), and a template's own statement wins over its
kind's. The legacy monitor LOOP is untouched: it keeps ``MONITOR_LOG.md`` in its own directory, and
its brief still asks for it.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.workflows import deliverable as D
from personalclaw.workflows import loop_aliases
from personalclaw.workflows.bundled_defs import read_template

MONITOR = "goal-pursuit-monitor"


@pytest.fixture()
def run_home(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _run(workflow: str, spec: dict) -> str:
    from personalclaw.workflows import store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    run = WorkflowRun(id=store.new_run_id(), workflow_name=workflow, status=RunStatus.RUNNING)
    store.save(run)
    store.write_spec(run.id, spec)
    return run.id


def test_a_monitor_run_reports_that_it_keeps_no_document(run_home):
    """🔴 Red on main: the page named MONITOR_LOG.md, "not written", "nothing asked for it"."""
    from personalclaw.workflows import run_cockpit

    assert loop_aliases.resolve_kind("goal", variant="monitor") == MONITOR, "vacuity floor"
    payload = run_cockpit.run_deliverable(_run(MONITOR, read_template(MONITOR).to_dict()))

    report = payload["report"]
    assert report["name"] is None, report
    assert report["absent_reason"] == D.KIND_HAS_NO_DOCUMENT, report
    assert payload["instructed"] is None, "there is no document to have been asked for"
    assert payload["derivation"]["declared_by"] == {"template": MONITOR, "name": ""}


def test_the_legacy_monitor_loop_still_keeps_its_log():
    """The floor: the statement is the TEMPLATE's. A monitor loop keeps MONITOR_LOG.md in its own
    directory, and its brief asks for it."""
    from personalclaw.loop import kinds
    from personalclaw.loop.loop import Loop

    kinds.ensure_loaded()
    goal = kinds.get_or_none("goal")
    loop = Loop(id="l1", name="w", kind="goal", task="watch", kind_config={"goal_type": "monitor"})
    assert goal.deliverable_name(loop) == "MONITOR_LOG.md"
    assert "MONITOR_LOG.md" in goal.build_brief(loop)


def test_a_template_that_states_no_statement_is_answered_by_its_kind(run_home):
    """Absent is not a statement: an open-ended goal run still reads its kind's REPORT.md."""
    from personalclaw.workflows import run_cockpit

    template = loop_aliases.resolve_kind("goal", variant="open_ended")
    spec = read_template(template).to_dict()
    assert D.DOCUMENT_KEY not in spec, "vacuity floor: this template states nothing"
    payload = run_cockpit.run_deliverable(_run(template, spec))
    assert payload["report"]["name"] == "REPORT.md"
    assert payload["derivation"]["declared_by"]["kind"] == "goal"


def test_a_stated_document_is_read_and_its_own_statement_does_not_count_as_asking(run_home):
    """A template may name the file its steps keep. That name is what the page reads — and the
    statement is not the steps asking for it: only a prompt, an action or a check that names the
    file is."""
    from personalclaw.workflows import run_cockpit, store

    silent = {D.DOCUMENT_KEY: "NOTES.md", "root": {"kind": "action", "id": "a"}}
    run_id = _run("bespoke", silent)
    payload = run_cockpit.run_deliverable(run_id)
    assert payload["report"]["name"] == "NOTES.md"
    assert payload["report"]["absent_reason"] == D.NOT_WRITTEN
    assert payload["instructed"] is False, "the statement named the file; no step asked for it"
    assert payload["derivation"]["declared_by"] == {"template": "bespoke", "name": "NOTES.md"}

    (store.run_dir(run_id) / "NOTES.md").write_text("# kept\n")
    assert run_cockpit.run_deliverable(run_id)["report"]["content"] == "# kept\n"

    asked = {**silent, "root": {"kind": "stage", "id": "s", "config": {"prompt": "Keep NOTES.md."}}}
    assert run_cockpit.run_deliverable(_run("bespoke", asked))["instructed"] is True


def test_a_stated_name_cannot_leave_the_run(run_home):
    """The name comes off a spec a person or a model wrote, so the read stays inside the run."""
    from personalclaw.workflows import run_cockpit, store

    run_id = _run("bespoke", {D.DOCUMENT_KEY: "../../OUTSIDE.md", "root": {"kind": "action"}})
    (store.run_dir(run_id).parent.parent / "OUTSIDE.md").write_text("not this run's")
    assert run_cockpit.run_deliverable(run_id)["report"]["present"] is False


@pytest.mark.parametrize("bad", [None, 3, ["REPORT.md"], {"name": "x"}])
def test_only_a_string_is_a_statement(bad):
    assert D.stated_document({D.DOCUMENT_KEY: bad}) is None


def test_every_bundled_statement_is_a_document_the_template_keeps_or_none():
    """A template naming a document its steps never write would be the same broken promise in a new
    place. So a bundled statement is either none, or a name the template's own steps use."""
    from personalclaw.workflows.bundled_defs import template_names

    names = template_names()
    assert len(names) > 20, "vacuity floor: the bundled library was not read"
    stated = {}
    for name in names:
        loaded = read_template(name)
        assert loaded is not None, name
        spec = loaded.to_dict()
        value = D.stated_document(spec)
        if value is None:
            continue
        stated[name] = value
        if value:
            rest = {k: v for k, v in spec.items() if k != D.DOCUMENT_KEY}
            assert value in json.dumps(rest), f"{name} states {value} and never asks for it"
    assert stated == {MONITOR: ""}, stated
