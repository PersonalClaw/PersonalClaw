"""``rails select`` (tools/ci/rails_select.py) on planted records: what each rule selects, and
what main's record is fetched from.

Each rule is shown with a control: the read that selects a rail, beside a path it did not read
that leaves the rail out. The fetch is driven through a planted ``gh``, which answers from a table
and refuses any call the table does not hold.
"""

from __future__ import annotations

import datetime
import io
import json
import zipfile
from typing import Any

import pytest

from tools.ci import rails_select

NOW = datetime.datetime(2026, 10, 24, 12, 0, tzinfo=datetime.timezone.utc)
COMMIT = "a" * 40

A = "tests/rails/test_a_tree.py"
B = "tests/rails/test_b_tree.py"
CONTRACT = "tests/rails/test_ci_contract.py"
RAILS = [A, B, CONTRACT]


def _reads(**given: Any) -> dict[str, list[Any]]:
    return {kind: list(given.get(kind, [])) for kind in ("files", "folders", "walks", "spawns")}


def _record(
    rails: dict[str, dict[str, list[Any]]],
    *,
    harness: dict[str, list[Any]] | None = None,
    unrecorded: dict[str, str] | None = None,
    recorded_at: str = "2026-10-24T10:00:00Z",
    commit: str = COMMIT,
    leg: str = "1",
) -> dict[str, Any]:
    return {
        "schema": rails_select.SCHEMA,
        "commit": commit,
        "recorded_at": recorded_at,
        "legs": [leg],
        "harness": harness or _reads(files=["tests/conftest.py"]),
        "rails": rails,
        "unrecorded": unrecorded or {},
    }


TREE = frozenset(
    {
        "tests/conftest.py",
        "src/pkg/a.py",
        "src/pkg/b.py",
        "src/pkg/data.json",
        "docs/one.md",
        "data/listed/x.txt",
        "data/other/y.txt",
        A,
        B,
        CONTRACT,
    }
)


def _select(
    paths: list[str],
    data: dict[str, Any] | None,
    *,
    tree: frozenset[str] | None = TREE,
    rails: list[str] = RAILS,
    now: datetime.datetime = NOW,
) -> dict[str, str]:
    record = None if data is None else rails_select.load(data)
    present = set(TREE)
    return rails_select.select(
        paths, rails, record, now=now, tree=tree, exists=lambda p: p in present
    )


def test_a_rail_is_selected_by_a_file_it_read_and_not_by_one_it_did_not() -> None:
    data = _record({A: _reads(files=["src/pkg/a.py"]), B: _reads(files=["src/pkg/b.py"])})
    chosen = _select(["src/pkg/a.py"], data)
    assert set(chosen) == {A, CONTRACT}
    assert chosen[A] == "reads src/pkg/a.py"
    assert chosen[CONTRACT] == "always selected"


def test_a_walk_selects_on_any_file_it_keeps_and_not_on_a_suffix_it_drops() -> None:
    data = _record({A: _reads(walks=[["src/pkg", ".py"]]), B: _reads(walks=[["src/pkg", ""]])})
    assert set(_select(["src/pkg/new_module.py"], data)) == {A, B, CONTRACT}
    assert set(_select(["src/pkg/data.json"], data)) == {B, CONTRACT}
    assert set(_select(["src/pkgx/a.py"], data)) == {CONTRACT}


def test_a_listed_folder_selects_on_an_added_or_removed_path_not_on_one_changed_in_place() -> None:
    data = _record({A: _reads(folders=["data/listed"]), B: _reads(files=["data/listed/x.txt"])})
    # x.txt changed in place: its folder's listing did not move, so only its reader runs.
    assert set(_select(["data/listed/x.txt"], data)) == {B, CONTRACT}
    added = _select(["data/listed/new.txt"], data)
    assert set(added) == {A, CONTRACT}
    assert added[A] == "lists data/listed, where data/listed/new.txt was added or removed"
    # Removed: not in this checkout, though the record's commit held it.
    present = set(TREE) - {"data/listed/x.txt"}
    record = rails_select.load(data)
    removed = rails_select.select(
        ["data/listed/x.txt"], RAILS, record, now=NOW, tree=TREE, exists=lambda p: p in present
    )
    assert set(removed) == {A, B, CONTRACT}
    assert set(_select(["data/other/new.txt"], data)) == {CONTRACT}


def test_a_path_in_a_folder_the_commit_never_held_counts_against_the_nearest_one_it_did() -> None:
    data = _record({A: _reads(folders=["data"]), B: _reads(folders=["data/listed"])})
    assert set(_select(["data/brand/new/file.txt"], data)) == {A, CONTRACT}


def test_without_the_commits_tree_every_path_counts_as_added_or_removed() -> None:
    data = _record({A: _reads(folders=["src/pkg"]), B: _reads(files=["src/pkg/b.py"])})
    assert set(_select(["src/pkg/b.py"], data, tree=None)) == {A, B, CONTRACT}
    # The nearest folder some rail lists: here the root, which none does, so none is added.
    assert set(_select(["docs/one.md"], data, tree=None)) == {CONTRACT}


def test_a_path_the_runs_own_setup_reads_selects_every_rail() -> None:
    data = _record({A: _reads(files=["src/pkg/a.py"]), B: _reads()})
    chosen = _select(["tests/conftest.py"], data)
    assert set(chosen) == set(RAILS)
    assert chosen[B] == "the run's own setup reads tests/conftest.py, and every rail runs under it"


def test_a_rail_that_starts_a_child_is_always_selected() -> None:
    data = _record({A: _reads(spawns=["git"]), B: _reads(files=["src/pkg/b.py"])})
    chosen = _select(["docs/one.md"], data)
    assert set(chosen) == {A, CONTRACT}
    assert chosen[A] == "starts git: what a child reads is not recorded"


def test_a_rail_the_record_does_not_hold_or_whose_run_failed_is_selected() -> None:
    new = "tests/rails/test_new_tree.py"
    data = _record({A: _reads()}, unrecorded={B: f"{B}::test_x failed in its call"})
    chosen = _select(["docs/one.md"], data, rails=[*RAILS, new])
    assert set(chosen) == {B, new, CONTRACT}
    assert chosen[B] == f"unrecorded: {B}::test_x failed in its call"
    assert chosen[new] == "the record does not hold it"


def test_a_record_older_than_a_day_or_none_selects_every_rail() -> None:
    data = _record({A: _reads(), B: _reads()}, recorded_at="2026-10-23T11:59:00Z")
    chosen = _select(["docs/one.md"], data)
    assert set(chosen) == set(RAILS)
    assert chosen[A].startswith("the record is older than a day")
    fresh = _record({A: _reads(), B: _reads()}, recorded_at="2026-10-23T12:01:00Z")
    assert set(_select(["docs/one.md"], fresh)) == {CONTRACT}
    assert set(_select(["docs/one.md"], None)) == set(RAILS)


def test_the_legs_records_merge_and_records_of_two_commits_do_not() -> None:
    one = rails_select.load(_record({A: _reads(files=["x"])}, leg="1"))
    two = rails_select.load(
        _record({B: _reads(files=["y"])}, leg="2", recorded_at="2026-10-24T09:00:00Z")
    )
    merged = rails_select.merge([one, two])
    assert set(merged.rails) == {A, B}
    assert merged.legs == ("1", "2")
    assert merged.recorded_at == datetime.datetime(2026, 10, 24, 9, 0, tzinfo=datetime.timezone.utc)
    other = rails_select.load(_record({B: _reads()}, leg="2", commit="b" * 40))
    with pytest.raises(rails_select.RecordError, match="2 commits"):
        rails_select.merge([one, other])
    # A rail one leg left unrecorded is not trusted from another.
    failed = rails_select.load(_record({}, leg="3", unrecorded={A: "failed"}))
    assert A not in rails_select.merge([one, failed]).rails


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": 2}, "schema 2"),
        ({"error": "OSError: boom"}, "its recording broke"),
        ({"commit": ""}, "names no commit"),
        ({"recorded_at": "2026-10-24T10:00:00"}, "names no zone"),
        ({"harness": {"files": []}}, "does not hold exactly"),
        ({"rails": {A: _reads(walks=[["", ""]])}}, "walks the whole repository"),
    ],
)
def test_a_record_out_of_shape_is_refused(change: dict[str, Any], message: str) -> None:
    data = {**_record({A: _reads()}), **change}
    with pytest.raises(rails_select.RecordError, match=message):
        rails_select.load(data)


# ── fetching main's record ──────────────────────────────────────────────────────────────────


def _zip(name: str, data: object) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, json.dumps(data))
    return buffer.getvalue()


def _run(run_id: int, **given: Any) -> dict[str, Any]:
    run = {
        "id": run_id,
        "event": "push",
        "head_branch": "main",
        "head_sha": COMMIT,
        # The workflow's file: _run_refusal reads only its name.
        "path": "workflows/merge.yml",
        "created_at": "2026-10-24T11:00:00Z",
        "repository": {"full_name": "o/r"},
        "head_repository": {"full_name": "o/r"},
    }
    return {**run, **given}


def _artifact(art_id: int, leg: str, run_id: int, attempt: int = 1, **given: Any) -> dict[str, Any]:
    return {
        "id": art_id,
        "name": f"reports-rails-{leg}-3-{attempt}",
        "expired": False,
        "size_in_bytes": 100,
        "workflow_run": {"id": run_id},
        **given,
    }


class _Gh:
    def __init__(self, answers: dict[str, bytes]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, path: str) -> bytes:
        self.asked.append(path)
        for prefix, answer in self.answers.items():
            if path.startswith(prefix):
                return answer
        raise AssertionError(f"gh was asked {path}, which the plant does not answer")


def _gh(
    runs: list[dict[str, Any]], artifacts: dict[int, list[dict[str, Any]]], zips: dict[int, bytes]
) -> _Gh:
    (workflow,) = rails_select.WORKFLOWS
    answers = {
        f"repos/{{owner}}/{{repo}}/actions/workflows/{workflow}/runs": json.dumps(
            {"workflow_runs": runs}
        ).encode(),
    }
    for run_id, listed in artifacts.items():
        answers[f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/artifacts"] = json.dumps(
            {"artifacts": listed}
        ).encode()
    for art_id, data in zips.items():
        answers[f"repos/{{owner}}/{{repo}}/actions/artifacts/{art_id}/zip"] = data
    return _Gh(answers)


def test_main_s_newest_push_run_with_a_record_is_read_and_others_are_refused() -> None:
    good = _zip("reports/rails-reads-1.json", _record({A: _reads(files=["src/pkg/a.py"])}))
    gh = _gh(
        [
            _run(9, event="pull_request"),
            _run(8, head_repository={"full_name": "fork/r"}),
            _run(7),
            _run(6),
        ],
        {7: [_artifact(70, "1", 7)], 6: [_artifact(60, "1", 6)]},
        {70: good},
    )
    record, notes = rails_select.fetch(gh, NOW)
    assert record is not None and record.source == "run 7 of merge.yml, legs 1"
    assert set(record.rails) == {A}
    assert notes == ["run 9: not a push to main", "run 8: its head is another repository's"]
    assert not any("/runs/6/" in asked for asked in gh.asked)


@pytest.mark.parametrize(
    ("artifact", "member", "data", "refusal"),
    [
        (_artifact(70, "1", 6), "reports/rails-reads-1.json", None, "was not uploaded by run 7"),
        (_artifact(70, "1", 7, expired=True), "reports/rails-reads-1.json", None, "has expired"),
        (
            _artifact(70, "1", 7, size_in_bytes=rails_select.MAX_ARTIFACT_BYTES + 1),
            "reports/rails-reads-1.json",
            None,
            "past",
        ),
        (
            _artifact(70, "1", 7),
            "other/rails-reads-1.json",
            None,
            "holds no reports/rails-reads-1.json",
        ),
        (
            _artifact(70, "1", 7),
            "reports/rails-reads-1.json",
            _record({}, commit="b" * 40),
            "not the run's",
        ),
        (_artifact(70, "1", 7), "reports/rails-reads-1.json", _record({}, leg="2"), "not 1"),
    ],
)
def test_an_artifact_not_the_runs_own_record_is_refused(
    artifact: dict[str, Any], member: str, data: dict[str, Any] | None, refusal: str
) -> None:
    gh = _gh(
        [_run(7)],
        {7: [artifact]},
        {70: _zip(member, data if data is not None else _record({A: _reads()}))},
    )
    record, notes = rails_select.fetch(gh, NOW)
    assert record is None
    assert len(notes) == 1 and refusal in notes[0], notes


def test_a_leg_s_newest_attempt_is_read_and_its_record_may_sit_at_the_artifact_s_root() -> None:
    """A rerun uploads the leg again under its own attempt: the newest is read. An upload whose
    one folder is reports/ itself holds the record at its root."""
    older = _zip("reports/rails-reads-1.json", _record({A: _reads(files=["src/pkg/a.py"])}))
    newer = _zip("rails-reads-1.json", _record({B: _reads(files=["src/pkg/b.py"])}))
    gh = _gh(
        [_run(7)],
        {7: [_artifact(70, "1", 7, attempt=1), _artifact(71, "1", 7, attempt=2)]},
        {70: older, 71: newer},
    )
    record, notes = rails_select.fetch(gh, NOW)
    assert record is not None and set(record.rails) == {B}, (record, notes)
    assert not any("/artifacts/70/" in asked for asked in gh.asked)


def test_only_runs_of_the_last_day_are_asked_for() -> None:
    gh = _gh([], {}, {})
    record, notes = rails_select.fetch(gh, NOW)
    assert record is None and notes == []
    assert gh.asked == [
        f"repos/{{owner}}/{{repo}}/actions/workflows/{workflow}/runs"
        "?branch=main&event=push&per_page=50&created=%3E%3D2026-10-23T12:00:00Z"
        for workflow in rails_select.WORKFLOWS
    ]
