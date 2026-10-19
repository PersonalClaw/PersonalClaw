"""``tools/ci/verdict_store.py``: a job's recorded pass stands for a later run only when every
input of the run is the same, the job's evidence may be reused at all, and the pass was recorded
from a clean checkout.

Every case runs on a planted repository in ``tmp_path``. The key and dirty variations are row
tables with an asserted row count, so a row dropped from a table fails the table.

The store's git runs with no ``GIT_*`` variable, so the suite's own global git file does not reach
it: each test here points ``HOME`` at a scratch folder, so that git reads a global file of the
test's own and never the developer's.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from table_rows import row

from tools.ci import catalogue
from tools.ci import verdict_store as vs

pytestmark = pytest.mark.security

HOUR = 3600.0
NOW = 1_800_000_000.0
PARTNER = "e" * 40


@pytest.fixture(autouse=True)
def _a_home_of_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The store's git reads the global file ``HOME`` names: here, one of this test's own."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    base.update(
        GIT_AUTHOR_NAME="Planted",
        GIT_AUTHOR_EMAIL="planted@example.invalid",
        GIT_COMMITTER_NAME="Planted",
        GIT_COMMITTER_EMAIL="planted@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    base.update(env or {})
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=base, check=True
    )
    return proc.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    (path / "a.txt").write_text("one\n")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "planted")
    return path


def _tree(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD^{tree}")


def _inputs(tree: str, **changes: Any) -> vs.KeyInputs:
    base = vs.KeyInputs(
        job_id="rails",
        entry_text='[[job]]\nid = "rails"\n',
        catalogue_version="c" * 64,
        argv=(("uv", "run", "pytest", "tests/rails"),),
        selection_digest="5" * 64,
        tree_oid=tree,
        external_revisions={"PersonalClaw/PersonalClawApps": PARTNER},
        lock_digests={"uv.lock": "1" * 64, "package-lock.json": "2" * 64},
        toolchain={"python": "3.12.15", "uv": "0.13.1", "node": "24.22.0"},
        os="linux",
        arch="x86_64",
        runner="ubuntu-24.04",
        advisory_policy="",
        declared={},
        capabilities={"git": "2.51.0", "tmux": "3.5"},
    )
    return dataclasses.replace(base, **changes)


def _job(
    reuse: str | None = "tree",
    security: str = "ordinary",
    live: bool = False,
    external: tuple[str, ...] = (),
) -> catalogue.Job:
    return catalogue.Job(
        id="rails",
        tiers=("merge",),
        inputs=(":(glob)**",),
        external_inputs=external,
        toolchain=(),
        cache=(),
        security=security,
        timeout_min=10,
        live=live,
        reuse=catalogue.parse_reuse(reuse),
        command=(catalogue.Step(("true",)),),
    )


def _result(repo: Path, **changes: Any) -> vs.Result:
    base = vs.Result(
        verdict="success",
        sha=_git(repo, "rev-parse", "HEAD"),
        seconds=12.5,
        skipped=("tests/rails/test_x.py::test_needs_docker",),
        junit="reports/junit-rails.xml",
        rerun=False,
        quarantined=(),
    )
    return dataclasses.replace(base, **changes)


def _recorded(repo: Path, inputs: vs.KeyInputs | None = None, now: float = NOW) -> vs.KeyInputs:
    """Record a clean pass on the repository's head; return its inputs."""
    inputs = inputs or _inputs(_tree(repo))
    start = vs.begin(repo)
    outcome = vs.Store.for_repo(repo).record(start, inputs, _result(repo), now=now)
    assert outcome.recorded, outcome.reason
    return inputs


# ── The module runs on a bare python3 ───────────────────────────────────────────────────────


def test_it_imports_only_the_standard_library_and_its_siblings() -> None:
    """A trusted job runs it on the runner image's own python3, before anything is installed.
    Its two siblings are standard library only, and their own tests hold that."""
    tree = ast.parse(Path(vs.__file__).read_text(encoding="utf-8"))
    absolute = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
    }
    relative = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0
    }
    absolute.discard("__future__")
    assert absolute, "the scan read no imports"
    assert absolute <= set(sys.stdlib_module_names), absolute - set(sys.stdlib_module_names)
    assert relative <= {"catalogue", "verdict"}, relative


# ── The reuse class comes from the catalogue's one parser ───────────────────────────────────


def test_the_class_is_read_by_the_catalogues_parser() -> None:
    assert vs.parse_reuse is catalogue.parse_reuse


@pytest.mark.parametrize("value", ["", "always", "ttl:0", "ttl:-1", "ttl:1.5", "ttl:", "TREE", 3])
def test_a_malformed_class_is_refused(value: object) -> None:
    with pytest.raises(ValueError):
        vs.parse_reuse(value)


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        pytest.param(_job(None), "never", id="no class is never"),
        pytest.param(_job("never"), "never", id="never"),
        pytest.param(_job("tree"), "tree", id="tree"),
        pytest.param(_job("ttl:6"), "ttl:6", id="ttl"),
        pytest.param(_job("tree", security="security"), "never", id="security is never"),
        pytest.param(_job("ttl:6", live=True), "never", id="live is never"),
        pytest.param(_job("tree", external=("advisory-feed",)), "never", id="a live feed is never"),
        pytest.param(_job("tree", external=("shard-history",)), "tree", id="another feed is kept"),
        pytest.param(_job("tree", security=""), "never", id="no security posture is never"),
        pytest.param(_job("ttl:6", security="trusted"), "never", id="an unknown posture is never"),
    ],
)
def test_the_class_a_job_is_decided_by(job: catalogue.Job, expected: str) -> None:
    assert str(vs.reuse_class(job)) == expected


# ── The key: one planted change per input changes it ────────────────────────────────────────


def test_every_input_is_in_the_key(repo: Path, subtests: Any) -> None:
    base = _inputs(_tree(repo))
    (repo / "a.txt").write_text("two\n")
    _git(repo, "commit", "-q", "-am", "a file in the tree")
    changed = {
        "a file in the tree": {"tree_oid": _tree(repo)},
        "the partner sha": {"external_revisions": {"PersonalClaw/PersonalClawApps": "f" * 40}},
        "a partner added": {"external_revisions": {**base.external_revisions, "other/x": PARTNER}},
        "a lock digest": {"lock_digests": {**base.lock_digests, "uv.lock": "3" * 64}},
        "another lock": {"lock_digests": {**base.lock_digests, "web/package-lock.json": "4" * 64}},
        "the advisory policy": {"advisory_policy": "advisories@2026-10-18"},
        "the catalogue version": {"catalogue_version": "d" * 64},
        "the entry text": {"entry_text": '[[job]]\nid = "rails"\nworkers = 2\n'},
        "the job id": {"job_id": "static"},
        "the argv": {"argv": (("uv", "run", "pytest", "tests/rails", "-x"),)},
        "the argv's order": {"argv": (("uv", "run", "tests/rails", "pytest"),)},
        "the selection digest": {"selection_digest": "6" * 64},
        "the runner image": {"runner": "ghcr.io/personalclaw/ci@sha256:" + "7" * 64},
        "a toolchain version": {"toolchain": {**base.toolchain, "uv": "0.13.2"}},
        "the os": {"os": "darwin"},
        "the arch": {"arch": "arm64"},
        "a declared input": {"declared": {"PERSONALCLAW_TEST_CHROME": "1"}},
        "a capability appears": {"capabilities": {**base.capabilities, "docker": "28.4.0"}},
        "a capability disappears": {"capabilities": {"git": "2.51.0"}},
        "a capability's version": {"capabilities": {**base.capabilities, "tmux": "3.6"}},
    }
    assert len(changed) == 20
    for name, change in changed.items():
        with row(subtests, change=name):
            assert vs.key(dataclasses.replace(base, **change)) != vs.key(base), name


def test_the_same_inputs_in_another_order_give_the_same_key(repo: Path) -> None:
    base = _inputs(_tree(repo))
    reordered = dataclasses.replace(
        base,
        lock_digests=dict(reversed(list(base.lock_digests.items()))),
        toolchain=dict(reversed(list(base.toolchain.items()))),
        capabilities=dict(reversed(list(base.capabilities.items()))),
    )
    assert vs.key(reordered) == vs.key(base)
    assert len(vs.key(base)) == 64


def test_the_stores_version_is_in_the_key(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _inputs(_tree(repo))
    before = vs.key(inputs)
    monkeypatch.setattr(vs, "VERSION", vs.VERSION + 1)
    assert vs.key(inputs) != before


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("external_revisions", "f" * 40),
        ("lock_digests", "3" * 64),
        ("toolchain", "9.9.9"),
        ("declared", "1"),
        ("capabilities", "1.0"),
    ],
)
def test_a_callers_mapping_changed_later_does_not_change_the_key(
    repo: Path, name: str, value: str
) -> None:
    mine = dict(getattr(_inputs(_tree(repo)), name))
    inputs = _inputs(_tree(repo), **{name: mine})
    before = vs.key(inputs)
    mine["added-later"] = value
    assert vs.key(inputs) == before


def test_a_callers_argv_changed_later_does_not_change_the_key(repo: Path) -> None:
    step = ["uv", "run", "pytest"]
    argv = [step]
    inputs = _inputs(_tree(repo), argv=argv)
    before = vs.key(inputs)
    step.append("-x")
    argv.append(["uv", "run", "ruff"])
    assert vs.key(inputs) == before


def test_a_sha256_tree_is_keyed(repo: Path) -> None:
    assert len(vs.key(_inputs("a" * 64))) == 64


@pytest.mark.parametrize("left_out", ["os", "arch"])
def test_inputs_that_leave_out_the_os_or_arch_are_not_built(repo: Path, left_out: str) -> None:
    """Left out, they would key every platform the same."""
    base = _inputs(_tree(repo))
    given = {f.name: getattr(base, f.name) for f in dataclasses.fields(base) if f.name != left_out}
    with pytest.raises(TypeError):
        vs.KeyInputs(**given)


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"tree_oid": "a" * 40 + "\n"}, id="tree with a newline"),
        pytest.param({"tree_oid": "A" * 40}, id="tree in uppercase"),
        pytest.param({"tree_oid": "a" * 39}, id="tree of 39"),
        pytest.param({"external_revisions": {"x/y": "e" * 41}}, id="partner of 41"),
        pytest.param({"job_id": ""}, id="no job id"),
        pytest.param({"argv": ()}, id="no argv"),
        pytest.param({"argv": ((),)}, id="an empty step"),
        pytest.param({"argv": (("uv", 3),)}, id="a word not text"),
        pytest.param({"argv": ("uv",)}, id="a step given as one text"),
        pytest.param({"catalogue_version": ""}, id="no catalogue version"),
        pytest.param({"catalogue_version": "c" * 63}, id="catalogue version of 63"),
        pytest.param({"catalogue_version": "c" * 64 + "\n"}, id="catalogue version, newline"),
        pytest.param({"catalogue_version": "C" * 64}, id="catalogue version in uppercase"),
        pytest.param({"selection_digest": ""}, id="no selection digest"),
        pytest.param({"selection_digest": "5" * 64 + "x"}, id="selection digest with a tail"),
        pytest.param({"selection_digest": "5" * 64 + "\n"}, id="selection digest, newline"),
        pytest.param({"lock_digests": {"uv.lock": ""}}, id="an empty lock digest"),
        pytest.param({"lock_digests": {"uv.lock": "1" * 63}}, id="a lock digest of 63"),
        pytest.param({"lock_digests": {"": "1" * 64}}, id="a lock with no name"),
        pytest.param({"toolchain": {"python": 3}}, id="a toolchain version not text"),
        pytest.param({"toolchain": {"python": None}}, id="no toolchain version"),
        pytest.param({"capabilities": {"git": None}}, id="no capability version"),
        pytest.param({"capabilities": {"git": 2}}, id="a capability version not text"),
        pytest.param({"declared": {"": "1"}}, id="a declared input with no name"),
        # Keyed as JSON, the name 3 would be the text "3": two inputs, one key.
        pytest.param({"declared": {3: "1"}}, id="a declared input named by no text"),
        pytest.param({"os": ""}, id="no os"),
        pytest.param({"os": None}, id="an os not text"),
        pytest.param({"arch": ""}, id="no arch"),
        pytest.param({"arch": None}, id="an arch not text"),
        pytest.param({"runner": None}, id="a runner not text"),
        pytest.param({"advisory_policy": None}, id="an advisory policy not text"),
        pytest.param({"entry_text": None}, id="an entry text not text"),
    ],
)
def test_inputs_that_name_nothing_exactly_are_refused(repo: Path, change: dict[str, Any]) -> None:
    with pytest.raises(vs.Refused):
        vs.key(_inputs(_tree(repo), **change))


# ── Dirty: a modified or untracked file reads dirty and records nothing ─────────────────────


def _modified(repo: Path) -> None:
    (repo / "a.txt").write_text("edited\n")


def _untracked(repo: Path) -> None:
    (repo / "new.txt").write_text("new\n")


def _untracked_hidden(repo: Path) -> None:
    _git(repo, "config", "status.showUntrackedFiles", "no")
    (repo / "sub").mkdir()
    (repo / "sub" / "new.txt").write_text("new\n")


def _staged(repo: Path) -> None:
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "b.txt")


def test_a_dirty_checkout_reads_dirty_and_records_nothing(repo: Path, subtests: Any) -> None:
    plants = {
        "a modified tracked file": _modified,
        "an untracked file": _untracked,
        "an untracked file under showUntrackedFiles=no": _untracked_hidden,
        "a staged new file": _staged,
    }
    assert len(plants) == 4
    for name, plant in plants.items():
        with row(subtests, plant=name):
            here = repo.parent / name.replace(" ", "-").replace("=", "-")
            _git(repo.parent, "clone", "-q", str(repo), str(here))
            plant(here)
            assert vs.dirty(here) is True
            start = vs.begin(here)
            assert start.dirty is True
            store = vs.Store.for_repo(here)
            inputs = _inputs(_tree(here))
            outcome = store.record(start, inputs, _result(here), now=NOW)
            assert not outcome.recorded and "dirty" in outcome.reason
            assert store.lookup(vs.key(inputs)) is None
            assert not list(store.root.glob("*")) if store.root.exists() else True


def test_a_clean_checkout_reads_clean_and_records(repo: Path) -> None:
    assert vs.dirty(repo) is False
    inputs = _recorded(repo)
    entry = vs.Store.for_repo(repo).lookup(vs.key(inputs))
    assert entry is not None
    assert entry.sha == _git(repo, "rev-parse", "HEAD")
    assert entry.tree == _tree(repo)
    assert entry.skipped == ("tests/rails/test_x.py::test_needs_docker",)
    assert entry.junit == "reports/junit-rails.xml"
    assert entry.seconds == 12.5


def test_a_change_between_start_and_record_records_nothing(repo: Path, subtests: Any) -> None:
    def edit(r: Path) -> None:
        (r / "a.txt").write_text("edited during the run\n")

    def new_head(r: Path) -> None:
        (r / "a.txt").write_text("committed during the run\n")
        _git(r, "commit", "-q", "-am", "a new head")

    def new_file(r: Path) -> None:
        (r / "left-behind.txt").write_text("x\n")

    plants = {"an edit": edit, "a new head": new_head, "a new untracked file": new_file}
    assert len(plants) == 3
    for name, plant in plants.items():
        with row(subtests, plant=name):
            here = repo.parent / ("during-" + name.replace(" ", "-"))
            _git(repo.parent, "clone", "-q", str(repo), str(here))
            inputs = _inputs(_tree(here))
            result = _result(here)
            start = vs.begin(here)
            assert start.dirty is False
            plant(here)
            store = vs.Store.for_repo(here)
            outcome = store.record(start, inputs, result, now=NOW)
            assert not outcome.recorded, name
            assert store.lookup(vs.key(inputs)) is None


def test_a_run_that_began_dirty_records_nothing_once_clean(repo: Path, subtests: Any) -> None:
    """The run's files were not the commit's when it began, so cleaning up after does not make
    its pass the commit's."""

    def untracked(r: Path) -> tuple[Any, Any]:
        return (lambda: _untracked(r), lambda: (r / "new.txt").unlink())

    def modified(r: Path) -> tuple[Any, Any]:
        return (lambda: _modified(r), lambda: _git(r, "checkout", "--", "a.txt"))

    plants = {"an untracked file, removed": untracked, "an edit, reverted": modified}
    assert len(plants) == 2
    for name, plant in plants.items():
        with row(subtests, plant=name):
            here = repo.parent / ("began-" + name.split(",")[0].replace(" ", "-"))
            _git(repo.parent, "clone", "-q", str(repo), str(here))
            make, undo = plant(here)
            make()
            start = vs.begin(here)
            undo()
            assert vs.dirty(here) is False
            store = vs.Store.for_repo(here)
            inputs = _inputs(_tree(here))
            outcome = store.record(start, inputs, _result(here), now=NOW)
            assert not outcome.recorded and "dirty" in outcome.reason
            assert store.lookup(vs.key(inputs)) is None


def test_a_status_that_cannot_be_read_is_dirty(tmp_path: Path) -> None:
    assert vs.dirty(tmp_path / "not-a-repository") is True


def test_a_checkout_with_no_head_begins_dirty(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-q")
    assert vs.dirty(empty) is False
    start = vs.begin(empty)
    assert start.dirty is True and start.head is None and start.tree is None


def test_a_pass_for_another_tree_or_head_records_nothing(repo: Path) -> None:
    store = vs.Store.for_repo(repo)
    start = vs.begin(repo)
    other_tree = _inputs("b" * 40)
    outcome = store.record(start, other_tree, _result(repo), now=NOW)
    assert not outcome.recorded and "another tree" in outcome.reason
    other_head = _result(repo, sha="b" * 40)
    outcome = store.record(start, _inputs(_tree(repo)), other_head, now=NOW)
    assert not outcome.recorded and "another head" in outcome.reason


# ── Only a clean pass is recorded ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"verdict": "failure"}, id="a red"),
        pytest.param({"verdict": "cancelled"}, id="cancelled"),
        pytest.param({"verdict": "skipped"}, id="skipped"),
        pytest.param({"verdict": "Success"}, id="an unknown word"),
        pytest.param({"rerun": True}, id="a pass that needed a rerun"),
        pytest.param({"rerun": None}, id="no rerun field"),
        pytest.param({"quarantined": ("tests/test_x.py::test_flake",)}, id="a quarantined failure"),
        pytest.param({"quarantined": None}, id="no quarantined list"),
    ],
)
def test_anything_but_a_clean_pass_records_nothing(repo: Path, change: dict[str, Any]) -> None:
    store = vs.Store.for_repo(repo)
    inputs = _inputs(_tree(repo))
    outcome = store.record(vs.begin(repo), inputs, _result(repo, **change), now=NOW)
    assert not outcome.recorded
    assert outcome.reason
    assert store.lookup(vs.key(inputs)) is None


@pytest.mark.parametrize(
    ("change", "now"),
    [
        pytest.param({"seconds": math.nan}, NOW, id="seconds not a number"),
        pytest.param({"seconds": math.inf}, NOW, id="seconds infinite"),
        pytest.param({"seconds": -1.0}, NOW, id="seconds negative"),
        pytest.param({"seconds": True}, NOW, id="seconds a boolean"),
        pytest.param({"skipped": (1,)}, NOW, id="a skipped id not text"),
        pytest.param({"junit": 5}, NOW, id="junit not text"),
        pytest.param({}, math.nan, id="recorded at no number"),
    ],
)
def test_a_pass_no_entry_can_hold_records_nothing(
    repo: Path, change: dict[str, Any], now: float
) -> None:
    """'recorded' means lookup() reads the entry back."""
    store = vs.Store.for_repo(repo)
    inputs = _inputs(_tree(repo))
    outcome = store.record(vs.begin(repo), inputs, _result(repo, **change), now=now)
    assert not outcome.recorded and outcome.reason
    assert not store.root.exists() or list(store.root.iterdir()) == []


# ── A torn or foreign entry is never read as a pass ─────────────────────────────────────────


def _entry_file(repo: Path, inputs: vs.KeyInputs) -> Path:
    return vs.Store.for_repo(repo).root / f"{vs.key(inputs)}.json"


def _rewrite(path: Path, **changes: Any) -> None:
    data = json.loads(path.read_text())
    data.update(changes)
    path.write_text(json.dumps(data))


def test_a_recorded_entry_is_read_back(repo: Path) -> None:
    inputs = _recorded(repo)
    assert vs.Store.for_repo(repo).lookup(vs.key(inputs)) is not None


def test_a_torn_or_foreign_entry_is_a_miss(repo: Path, subtests: Any) -> None:
    inputs = _recorded(repo)
    path = _entry_file(repo, inputs)
    good = path.read_text()
    other = vs.key(dataclasses.replace(inputs, job_id="static"))

    def other_inputs(p: Path) -> None:
        data = json.loads(good)
        data["inputs"]["job_id"] = "static"
        p.write_text(json.dumps(data))

    def no_junit(p: Path) -> None:
        data = json.loads(good)
        del data["junit"]
        p.write_text(json.dumps(data))

    def unencodable_inputs(p: Path) -> None:
        data = json.loads(good)
        data["inputs"]["os"] = "\ud800"
        p.write_text(json.dumps(data))

    tears = {
        "truncated": lambda p: p.write_text(good[: len(good) // 2]),
        "empty": lambda p: p.write_text(""),
        "not an object": lambda p: p.write_text("[]"),
        "not utf-8": lambda p: p.write_bytes(b"\xff\xfe{}"),
        "a mismatched key field": lambda p: _rewrite(p, key=other),
        "inputs of another key": other_inputs,
        "another version": lambda p: _rewrite(p, version=vs.VERSION + 1),
        "a red verdict": lambda p: _rewrite(p, verdict="failure"),
        "no recorded_at": lambda p: _rewrite(p, recorded_at=None),
        "a sha with a newline": lambda p: _rewrite(p, sha=json.loads(good)["sha"] + "\n"),
        "skipped not a list": lambda p: _rewrite(p, skipped="tests/x.py"),
        "seconds negative": lambda p: _rewrite(p, seconds=-1),
        "a key given twice": lambda p: p.write_text(good[:-1] + f', "key": "{other}"}}'),
        "a red verdict, then a green one": lambda p: p.write_text(
            good.replace('"verdict":"success"', '"verdict":"failure","verdict":"success"')
        ),
        "another tree": lambda p: _rewrite(p, tree="b" * 40),
        "a field missing": no_junit,
        "a field no entry has": lambda p: _rewrite(p, note="x"),
        "recorded_at not a number": lambda p: _rewrite(p, recorded_at=float("nan")),
        "seconds infinite": lambda p: _rewrite(p, seconds=float("inf")),
        "seconds a boolean": lambda p: _rewrite(p, seconds=True),
        "recorded_at a boolean": lambda p: _rewrite(p, recorded_at=True),
        "junit not text": lambda p: _rewrite(p, junit=5),
        "a skipped id not text": lambda p: _rewrite(p, skipped=[1]),
        "version true": lambda p: _rewrite(p, version=True),
        "version 1.0": lambda p: _rewrite(p, version=1.0),
        "nested too deep": lambda p: p.write_text("[" * 100_000 + "]" * 100_000),
        "inputs not encodable": unencodable_inputs,
    }
    assert len(tears) == 27
    for name, tear in tears.items():
        with row(subtests, tear=name):
            path.write_text(good)
            tear(path)
            assert vs.Store.for_repo(repo).lookup(vs.key(inputs)) is None, name
    path.write_text(good)
    assert vs.Store.for_repo(repo).lookup(vs.key(inputs)) is not None


def test_a_job_whose_entry_is_malformed_runs(repo: Path) -> None:
    """A miss runs the job: the receipt is still made."""
    job = _job("tree")
    inputs = _recorded(repo, _inputs(_tree(repo), job_id=job.id))
    _entry_file(repo, inputs).write_text("[" * 100_000 + "]" * 100_000)
    (decision,) = _decide(repo, [job], now=NOW).jobs
    assert not decision.reused and decision.source is None


def test_a_leftover_temp_file_is_never_read(repo: Path) -> None:
    inputs = _recorded(repo)
    path = _entry_file(repo, inputs)
    good = path.read_text()
    path.unlink()
    (path.parent / f".{path.stem}.partial.tmp").write_text(good)
    assert vs.Store.for_repo(repo).lookup(vs.key(inputs)) is None


@pytest.mark.parametrize("bad", ["../escape", "a" * 63, "a" * 64 + "\n", "A" * 64, ""])
def test_a_key_that_is_not_a_digest_is_a_miss_and_opens_nothing(
    repo: Path, bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _recorded(repo)
    store = vs.Store.for_repo(repo)
    # A file beside the store, where "../escape" would lead, holding a whole entry.
    (store.root.parent / "escape.json").write_bytes(_entry_file(repo, inputs).read_bytes())
    opened: list[Path] = []
    real = Path.read_bytes

    def spy(self: Path) -> bytes:
        opened.append(self)
        return real(self)

    monkeypatch.setattr(vs.Path, "read_bytes", spy)
    assert store.lookup(bad) is None
    assert opened == []


@pytest.mark.parametrize(
    "failure",
    [OSError("the disk went away"), KeyboardInterrupt()],
    ids=["an error", "an interrupt"],
)
def test_a_replace_that_fails_leaves_no_entry_and_no_temp(
    repo: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    def broken(src: str, dst: str) -> None:
        raise failure

    store = vs.Store.for_repo(repo)
    inputs = _inputs(_tree(repo))
    start = vs.begin(repo)
    with monkeypatch.context() as patched, pytest.raises(type(failure)):
        patched.setattr(vs.os, "replace", broken)
        store.record(start, inputs, _result(repo), now=NOW)
    assert store.lookup(vs.key(inputs)) is None
    assert list(store.root.iterdir()) == []


def test_record_writes_through_a_temp_file_in_the_store(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str]] = []
    synced: list[tuple[int, int]] = []
    real, real_fsync = os.replace, os.fsync

    def fsync(fd: int) -> None:
        stat = os.fstat(fd)
        synced.append((stat.st_ino, stat.st_size))
        real_fsync(fd)

    def spy(src: str, dst: str) -> None:
        # The whole temp file is on disk before it takes the entry's name.
        stat = os.stat(src)
        assert synced == [(stat.st_ino, stat.st_size)]
        seen.append((str(src), str(dst)))
        real(src, dst)

    monkeypatch.setattr(vs.os, "fsync", fsync)
    monkeypatch.setattr(vs.os, "replace", spy)
    inputs = _recorded(repo)
    path = _entry_file(repo, inputs)
    assert len(seen) == 1
    src, dst = seen[0]
    assert Path(dst) == path
    assert Path(src).parent == path.parent and Path(src).name.startswith(".")
    assert synced[0][1] == path.stat().st_size > 0


# ── One store per clone, whatever worktree or hook asks ─────────────────────────────────────


def test_worktrees_of_one_clone_share_one_store(repo: Path) -> None:
    other = repo.parent / "second"
    _git(repo, "worktree", "add", "-q", "--detach", str(other))
    first = vs.Store.for_repo(repo).root
    second = vs.Store.for_repo(other).root
    assert first.resolve() == second.resolve()
    assert first.is_absolute()
    assert first.resolve() == (repo / ".git" / "ci-verdicts").resolve()
    inputs = _recorded(repo)
    assert vs.Store.for_repo(other).lookup(vs.key(inputs)) is not None


def test_an_exported_git_dir_does_not_move_the_store(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _git(elsewhere, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(elsewhere / ".git" / "index"))
    root = vs.Store.for_repo(repo).root
    assert root.resolve() == (repo / ".git" / "ci-verdicts").resolve()
    assert vs.dirty(repo) is False


def test_no_exported_git_variable_moves_the_store_or_what_git_reads(
    repo: Path, tmp_path: Path, subtests: Any
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _git(elsewhere, "init", "-q")
    (elsewhere / "other.txt").write_text("other\n")
    head, tree = _git(repo, "rev-parse", "HEAD"), _tree(repo)
    exports = {
        "GIT_DIR": str(elsewhere / ".git"),
        "GIT_COMMON_DIR": str(elsewhere / ".git"),
        "GIT_WORK_TREE": str(elsewhere),
        "GIT_INDEX_FILE": str(elsewhere / ".git" / "index"),
        "GIT_OBJECT_DIRECTORY": str(elsewhere / ".git" / "objects"),
    }
    assert len(exports) == 5
    for name, value in exports.items():
        with row(subtests, export=name), pytest.MonkeyPatch.context() as env:
            env.setenv(name, value)
            root = vs.Store.for_repo(repo).root
            assert root.resolve() == (repo / ".git" / "ci-verdicts").resolve(), name
            start = vs.begin(repo)
            assert (start.dirty, start.head, start.tree) == (False, head, tree), name


def test_no_git_variable_reaches_the_stores_git(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not only the five that locate a repository: a configuration a hook passed on
    (``GIT_CONFIG_*``, ``GIT_CONFIG_PARAMETERS``) or a ceiling does not reach it either."""
    started: list[dict[str, str]] = []
    real = subprocess.run

    def spy(*args: Any, **kwargs: Any) -> Any:
        started.append(dict(kwargs.get("env") or os.environ))
        return real(*args, **kwargs)

    for name, value in {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "status.showUntrackedFiles",
        "GIT_CONFIG_VALUE_0": "no",
        "GIT_CONFIG_PARAMETERS": "'core.excludesfile'='/dev/null'",
        "GIT_CEILING_DIRECTORIES": str(repo.parent),
        "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(vs.subprocess, "run", spy)
    vs.Store.for_repo(repo)
    vs.begin(repo)
    vs.dirty(repo)
    assert len(started) >= 4
    for env in started:
        assert not [k for k in env if k.startswith("GIT_")], env


def test_the_stores_git_reads_a_global_file_of_this_tests_own(repo: Path, tmp_path: Path) -> None:
    """With no ``GIT_*`` variable, git reads the global file ``HOME`` names, as the user's own git
    would: in this module, a scratch one, never the developer's."""
    home = Path(os.environ["HOME"])
    assert home.is_relative_to(tmp_path), home
    (home / ".gitconfig").write_text("[probe]\n\tmarker = this-tests-own\n")
    read = vs._git(repo, "config", "--get", "probe.marker")
    assert read.stdout.strip() == "this-tests-own"


def test_a_folder_outside_any_clone_has_no_store(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(vs.Refused):
        vs.Store.for_repo(plain)
    with pytest.raises(vs.Refused):
        vs.Store.for_repo(tmp_path / "missing")


# ── The decision and the receipt ────────────────────────────────────────────────────────────


def _decide(
    repo: Path, jobs: list[catalogue.Job], now: float, start: vs.Start | None = None
) -> vs.Receipt:
    """Decide *jobs* on inputs of the repository's tree, from *start* (the checkout read now when
    none is given)."""
    inputs = {job.id: _inputs(_tree(repo), job_id=job.id) for job in jobs}
    start = start or vs.begin(repo)
    return vs.decide(jobs, inputs, vs.Store.for_repo(repo), now=now, start=start)


def _named(job: catalogue.Job, job_id: str) -> catalogue.Job:
    return dataclasses.replace(job, id=job_id)


def test_a_tree_job_with_a_recorded_pass_is_reused(repo: Path) -> None:
    job = _job("tree")
    inputs = _recorded(repo, _inputs(_tree(repo), job_id=job.id), now=NOW - 2 * HOUR)
    receipt = _decide(repo, [job], now=NOW)
    (decision,) = receipt.jobs
    assert decision.reused and decision.reuse == "tree"
    assert decision.key == vs.key(inputs)
    assert decision.source is not None
    assert decision.source.sha == _git(repo, "rev-parse", "HEAD")
    assert decision.source.age_seconds == 2 * HOUR
    assert decision.source.skipped == 1


def test_a_tree_job_with_other_inputs_runs(repo: Path) -> None:
    _recorded(repo, _inputs(_tree(repo), selection_digest="9" * 64))
    (decision,) = _decide(repo, [_job("tree")], now=NOW).jobs
    assert not decision.reused and decision.source is None


@pytest.mark.parametrize(
    "job",
    [
        pytest.param(_job("never"), id="never"),
        pytest.param(_job(None), id="no class"),
        pytest.param(_job("tree", security="security"), id="security"),
        pytest.param(_job("ttl:24", live=True), id="live"),
        pytest.param(_job("tree", external=("advisory-feed",)), id="a live feed"),
        pytest.param(_job("tree", security="trusted"), id="an unknown posture"),
    ],
)
def test_a_never_job_runs_despite_a_recorded_pass(repo: Path, job: catalogue.Job) -> None:
    _recorded(repo, _inputs(_tree(repo), job_id=job.id))
    (decision,) = _decide(repo, [job], now=NOW).jobs
    assert not decision.reused
    assert decision.reuse == "never"


@pytest.mark.parametrize(
    ("age", "reused"),
    [
        pytest.param(0.0, True, id="just recorded"),
        pytest.param(6 * HOUR - 1, True, id="inside its ttl"),
        pytest.param(6 * HOUR, False, id="at its ttl"),
        pytest.param(6 * HOUR + 1, False, id="past its ttl"),
        pytest.param(-1.0, False, id="recorded in the future"),
    ],
)
def test_a_ttl_job_is_reused_only_inside_its_ttl(repo: Path, age: float, reused: bool) -> None:
    job = _job("ttl:6")
    _recorded(repo, _inputs(_tree(repo), job_id=job.id), now=NOW - age)
    (decision,) = _decide(repo, [job], now=NOW).jobs
    assert decision.reused is reused
    assert decision.reuse == "ttl:6"
    # Reused or run, the receipt gives a TTL job's age.
    assert decision.ttl_age_seconds == age
    assert decision.to_json()["ttl_age_seconds"] == age
    if reused:
        assert decision.source is not None and decision.source.age_seconds == age


@pytest.mark.parametrize(
    "reuse",
    [catalogue.Reuse("always"), catalogue.Reuse("ttl", None), catalogue.Reuse("Tree")],
    ids=["always", "a ttl of no hours", "Tree"],
)
def test_a_class_no_parser_makes_runs(repo: Path, reuse: catalogue.Reuse) -> None:
    """A class built past ``parse_reuse`` is not one the store reuses by."""
    job = dataclasses.replace(_job("tree"), reuse=reuse)
    _recorded(repo, _inputs(_tree(repo), job_id=job.id))
    (decision,) = _decide(repo, [job], now=NOW).jobs
    assert not decision.reused and decision.source is None


def test_a_dirty_run_replays_nothing(repo: Path) -> None:
    """The receipt's dirty field and the replay rule come from the one reading of the checkout
    the run began with."""
    job = _job("tree")
    _recorded(repo, _inputs(_tree(repo), job_id=job.id))
    _untracked(repo)
    start = vs.begin(repo)
    assert start.dirty is True
    receipt = _decide(repo, [job], now=NOW, start=start)
    assert receipt.dirty is True
    assert receipt.to_json()["dirty"] is True
    (decision,) = receipt.jobs
    assert not decision.reused and "dirty" in decision.reason


def test_inputs_of_another_tree_than_the_checkouts_are_run(repo: Path) -> None:
    """A pass recorded on another tree does not stand for this checkout, whatever inputs the
    caller names."""
    job = _job("tree")
    first = _recorded(repo, _inputs(_tree(repo), job_id=job.id))
    (repo / "a.txt").write_text("two\n")
    _git(repo, "commit", "-q", "-am", "a new tree")
    start = vs.begin(repo)
    assert start.tree != first.tree_oid
    receipt = vs.decide([job], {job.id: first}, vs.Store.for_repo(repo), now=NOW, start=start)
    (decision,) = receipt.jobs
    assert not decision.reused and decision.source is None
    assert "tree" in decision.reason


def test_the_receipt_lists_every_planned_job(repo: Path) -> None:
    tree_job, ttl_job, never_job = (
        _named(_job("tree"), "web-unit"),
        _named(_job("ttl:6"), "served"),
        _named(_job("tree", security="security"), "python-suite"),
    )
    for job in (tree_job, ttl_job, never_job):
        _recorded(repo, _inputs(_tree(repo), job_id=job.id), now=NOW - 2 * HOUR - 300)
    missing = _named(_job("tree"), "client")
    receipt = _decide(repo, [tree_job, ttl_job, never_job, missing], now=NOW)
    data = receipt.to_json()
    assert data["dirty"] is False
    assert [j["id"] for j in data["jobs"]] == ["web-unit", "served", "python-suite", "client"]
    by_id = {j["id"]: j for j in data["jobs"]}
    assert by_id["web-unit"]["decision"] == "reused"
    assert by_id["served"]["decision"] == "reused"
    assert by_id["served"]["ttl_age_seconds"] == 2 * HOUR + 300
    assert by_id["web-unit"]["ttl_age_seconds"] is None
    assert by_id["python-suite"]["decision"] == "run"
    assert by_id["python-suite"]["reuse"] == "never"
    assert by_id["client"]["decision"] == "run" and by_id["client"]["source"] is None
    for job in data["jobs"]:
        assert job["inputs"]["job_id"] == job["id"]
        assert job["inputs"]["tree_oid"] == _tree(repo)
        assert job["key"] == vs.key(_inputs(_tree(repo), job_id=job["id"]))
        assert job["reason"]
    sha = _git(repo, "rev-parse", "HEAD")
    assert by_id["web-unit"]["source"] == {
        "sha": sha,
        "age_seconds": 2 * HOUR + 300,
        "skipped": 1,
    }
    json.dumps(data)
    lines = receipt.lines()
    assert f"reused web-unit from {sha[:12]}, 2h 05m, 1 skipped" in lines
    assert f"reused served from {sha[:12]}, 2h 05m, 1 skipped" in lines
    assert any(line.startswith("ran python-suite: ") for line in lines)
    assert any(line.startswith("ran client: ") for line in lines)
    assert len(lines) == 4


def test_a_planned_job_with_no_inputs_is_refused(repo: Path) -> None:
    with pytest.raises(vs.Refused):
        vs.decide([_job("tree")], {}, vs.Store.for_repo(repo), now=NOW, start=vs.begin(repo))


def test_inputs_for_another_job_are_refused(repo: Path) -> None:
    inputs = {"rails": _inputs(_tree(repo), job_id="static")}
    with pytest.raises(vs.Refused):
        store, start = vs.Store.for_repo(repo), vs.begin(repo)
        vs.decide([_job("tree")], inputs, store, now=NOW, start=start)
