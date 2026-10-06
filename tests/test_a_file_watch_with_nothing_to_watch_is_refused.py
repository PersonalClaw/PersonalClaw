"""A file watch with nothing to watch is refused when it is made, and says why.

``automation_create`` with ``kind: "file"`` takes its spec as given and leaves the ``when`` phrase
unread, so a request that named its folder only in the phrase was saved with ``spec: {}``: a watch
of no paths, which the watch loop skips, listed as "When a watched file changes" while the agent
told her it was watching the folder. Nothing checked the file kind's spec, though the clock, event
and web-watch kinds each refuse the spec they cannot run.
"""

from __future__ import annotations

import pytest

from personalclaw.triggers import tools as T
from personalclaw.triggers.models import Trigger, parse_trigger, validate_spec
from personalclaw.triggers.store import TriggerStore

NOTIFY = {"provider": "notify", "config": {"kind": "info", "title_template": "A new file"}}


@pytest.fixture
def store(tmp_path) -> TriggerStore:
    return TriggerStore(base_dir=tmp_path)


def test_a_file_watch_whose_folder_is_only_in_the_phrase_is_refused_with_the_reason(store):
    """🔴 Before: saved with `spec {}`, ok, and no word that it could never fire."""
    made = T.create(
        store,
        name="Drop folder log",
        kind="file",
        when="when a file in ~/Documents/drop changes",
        workflow=NOTIFY,
        owner_consented=True,
    )
    assert made.ok is False
    assert "spec.paths" in made.text
    assert "a file trigger needs" in made.text
    assert store.load() == [], "nothing was saved"


def test_the_same_phrase_without_a_kind_is_read_as_a_watch_of_that_folder(store):
    """Control: the phrase alone routes to a file watch and names the folder it watches."""
    made = T.create(
        store,
        name="Drop folder log",
        when="when a file in ~/Documents/drop changes",
        workflow=NOTIFY,
        owner_consented=True,
    )
    assert made.ok is True, made.text
    (row,) = store.load()
    assert row.trigger.kind == "file"
    assert row.trigger.spec["paths"], row.trigger.spec


@pytest.mark.parametrize(
    "spec",
    [{}, {"paths": []}, {"paths": [""]}, {"paths": "~/Documents/drop"}],
    ids=["no-paths", "empty-list", "blank-path", "not-a-list"],
)
def test_a_file_spec_with_nothing_it_can_watch_is_an_error(spec):
    errors = [i for i in validate_spec("file", spec) if i.severity == "error"]
    assert [i.path for i in errors] == ["spec.paths"], errors


def test_a_file_spec_with_a_path_is_clean():
    assert validate_spec("file", {"paths": ["~/Documents/drop/*.pdf"], "dedup": "content"}) == []


def test_a_stored_watch_of_no_paths_reads_broken_not_active(store):
    """A row an older build saved this way, or a hand edit: listed as needing attention and
    switched off, rather than as a watch that is running."""
    trigger, issues = parse_trigger(
        Trigger(id="file:drop", name="Drop", kind="file", enabled=True, spec={}).to_dict()
    )
    assert trigger.enabled is False
    assert any(i.path == "spec.paths" and i.severity == "error" for i in issues)


def test_an_edit_that_takes_every_path_away_is_refused(store):
    made = T.create(
        store,
        name="Drop folder log",
        when="when a file in ~/Documents/drop changes",
        workflow=NOTIFY,
        owner_consented=True,
    )
    assert made.ok is True, made.text
    (row,) = store.load()
    edited = T.update(store, trigger_id=row.trigger.id, patch={"spec": {"paths": []}})
    assert edited.ok is False
    assert "spec.paths" in edited.text
    assert store.get(row.trigger.id).trigger.spec["paths"], "the watch keeps its folder"
