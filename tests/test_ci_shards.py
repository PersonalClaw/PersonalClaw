"""``tools/ci/shards.py``: the suite split into whole-file shards by recorded durations."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tools.ci import shards, telemetry

PYPROJECT = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'


def _tree(root: Path, *files: str) -> Path:
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return root


# ── the universe: every file pytest collects ───────────────────────────────────────────────────


def test_the_universe_is_every_file_pytest_collects_by_its_defaults(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        "tests/test_a.py",
        "tests/b_test.py",
        "tests/sub/test_s.py",
        "tests/security/test_z.py",
        "tests/helper.py",
        "tests/conftest.py",
        "tests/.hidden/test_x.py",
        "tests/__pycache__/test_y.py",
        "tests/node_modules/test_n.py",
        "tests/rails/test_r.py",
        "src/test_outside.py",
    )
    assert shards.universe(root) == [
        "tests/b_test.py",
        "tests/security/test_z.py",
        "tests/sub/test_s.py",
        "tests/test_a.py",
    ], "tests/rails is the rails job's, so no shard may list a rail"


def test_a_shard_plan_listing_a_rail_fails_its_check(tmp_path: Path) -> None:
    """The control: a plan that hands a rail to a shard is a listed file pytest's shards would
    not collect, and ``check`` refuses it."""
    root = _tree(tmp_path, "tests/test_a.py", "tests/rails/test_r.py")
    files = shards.universe(root)
    assert shards.check(files, [["tests/test_a.py", "tests/rails/test_r.py"]])


def test_a_file_a_job_owns_is_in_no_shard(tmp_path: Path) -> None:
    root = _tree(tmp_path, "tests/test_a.py", "tests/test_owned.py")
    owned = {"tests/test_owned.py": "static"}
    assert shards.universe(root, owned) == ["tests/test_a.py"]
    assert shards.universe(root, {}) == ["tests/test_a.py", "tests/test_owned.py"]


def test_a_shard_plan_listing_a_file_a_job_owns_fails_its_check(tmp_path: Path) -> None:
    """The control: a plan that hands a job's file to a shard lists a file the shards would not
    collect, and ``check`` refuses it, as it refuses a rail."""
    root = _tree(tmp_path, "tests/test_a.py", "tests/test_owned.py")
    files = shards.universe(root, {"tests/test_owned.py": "static"})
    problems = shards.check(files, [["tests/test_a.py"], ["tests/test_owned.py"]])
    assert problems == ["tests/test_owned.py is listed but not collected"]


def test_shards_check_fails_on_a_plan_that_lists_a_job_owned_file(tmp_path: Path) -> None:
    """``shards check``, as CI runs it, over the real exclusion list: a planted plan that still
    hands the installer sweep, the npm floors or a file of the security corpus to a shard fails,
    naming each."""
    root = _tree(tmp_path, "tests/test_a.py", *shards.JOB_OWNED)
    plan = {
        "version": shards.VERSION,
        "legs": {"test-shard": {"n": 1, "buckets": [["tests/test_a.py", *shards.JOB_OWNED]]}},
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.ci",
            "shards",
            "check",
            "--plan",
            str(path),
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert done.returncode == 1, done.stdout + done.stderr
    for rel in shards.JOB_OWNED:
        assert f"{rel} is listed but not collected" in done.stdout, rel
    plan["legs"]["test-shard"]["buckets"] = [["tests/test_a.py"]]
    path.write_text(json.dumps(plan), encoding="utf-8")
    again = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.ci",
            "shards",
            "check",
            "--plan",
            str(path),
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert again.returncode == 0, again.stdout + again.stderr


def test_the_rails_folder_name_is_the_censuss() -> None:
    from tools.ci import rails_census

    assert shards.RAILS == rails_census.RAILS


def test_the_universe_reads_testpaths_and_python_files(tmp_path: Path) -> None:
    root = _tree(tmp_path, "suite/check_a.py", "suite/test_b.py", "tests/test_c.py")
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["suite"]\npython_files = ["check_*.py"]\n',
        encoding="utf-8",
    )
    assert shards.universe(root) == ["suite/check_a.py"]


def test_the_security_corpus_is_in_no_shard_and_its_job_runs_it(tmp_path: Path) -> None:
    """The ``security`` job runs the corpus on every run, so no shard collects it."""
    owned = {p: j for p, j in shards.JOB_OWNED.items() if j == "security"}
    assert len(owned) == 12, sorted(owned)
    assert "tests/test_supply_chain_gates.py" in owned
    assert all(p.startswith("tests/security/") or "supply_chain" in p for p in owned)
    root = _tree(tmp_path, "tests/test_a.py", *owned)
    buckets = shards.plan(shards.universe(root), {}, 2)
    assert sorted(f for b in buckets for f in b) == ["tests/test_a.py"]


# ── the plan ──────────────────────────────────────────────────────────────────────────────────


def _files(n: int) -> list[str]:
    return [f"tests/test_{i:03d}.py" for i in range(n)]


def _durations(files: list[str]) -> dict[str, float]:
    return {f: float((i * 37) % 101 + 1) for i, f in enumerate(files)}


def test_the_plan_balances_shards_within_one_percent() -> None:
    files = _files(200)
    durations = _durations(files)
    for n in (2, 3, 4, 6):
        buckets = shards.plan(files, durations, n)
        loads = [sum(durations[f] for f in b) for b in buckets]
        assert max(loads) / min(loads) <= 1.01, (n, loads)


def test_the_same_input_gives_the_same_buckets_whatever_its_order() -> None:
    files = _files(60)
    durations = _durations(files)
    once = shards.plan(files, durations, 4)
    assert shards.plan(list(reversed(files)), dict(reversed(durations.items())), 4) == once
    assert shards.plan(files, durations, 4) == once


def test_every_file_lands_in_exactly_one_bucket() -> None:
    files = _files(37)
    buckets = shards.plan(files, _durations(files), 4)
    assert sorted(f for b in buckets for f in b) == sorted(files)
    assert shards.check(files, buckets) == []


def test_a_file_with_no_record_weighs_the_median_of_the_recorded() -> None:
    files = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py", "tests/test_new.py"]
    durations = {"tests/test_a.py": 1.0, "tests/test_b.py": 5.0, "tests/test_c.py": 9.0}
    assert shards.weights(files, durations)["tests/test_new.py"] == 5.0
    assert shards.weights(files, {}) == {f: 1.0 for f in files}, "no record: equal weights"


def test_a_record_for_a_file_that_is_gone_is_ignored() -> None:
    files = ["tests/test_a.py", "tests/test_b.py"]
    durations = {"tests/test_a.py": 2.0, "tests/test_b.py": 2.0, "tests/test_gone.py": 999.0}
    buckets = shards.plan(files, durations, 2)
    assert sorted(f for b in buckets for f in b) == files


def test_check_names_a_file_in_no_bucket_one_in_two_and_one_not_collected() -> None:
    files = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
    problems = shards.check(
        files, [["tests/test_a.py", "tests/test_b.py"], ["tests/test_b.py", "tests/test_x.py"]]
    )
    assert any("tests/test_c.py" in p and "no shard" in p for p in problems)
    assert any("tests/test_b.py" in p and "2 shards" in p for p in problems)
    assert any("tests/test_x.py" in p and "not collected" in p for p in problems)


def test_a_planted_x_test_file_lands_in_a_bucket(tmp_path: Path) -> None:
    root = _tree(tmp_path, "tests/test_a.py", "tests/x_test.py")
    buckets = shards.plan(shards.universe(root), {}, 2)
    assert "tests/x_test.py" in {f for b in buckets for f in b}


# ── the durations source: #3948's telemetry record ──────────────────────────────────────────

FILES = ["tests/test_a.py", "tests/sub/test_s.py", "tests/test_ab.py"]
LEG = shards.Leg("test-shard", "ci.yml", "junit-test-shard-", 4)
LINUX = "linux-x86_64-3.12.15"
REPO_ID = 1


def _zip(name: str, data: bytes, *, deflate: bool = False) -> bytes:
    buffer = io.BytesIO()
    method = zipfile.ZIP_DEFLATED if deflate else zipfile.ZIP_STORED
    with zipfile.ZipFile(buffer, "w", method) as archive:
        archive.writestr(name, data)
    return buffer.getvalue()


def _jsonl(
    run: int,
    *tests: tuple,
    schema: str = telemetry.SCHEMA,
    version: int = telemetry.VERSION,
    cpus: dict[str, str | None] | None = None,
) -> bytes:
    """One collected history: a run line, then a test line per ``(report, test, seconds)``,
    each on ``LINUX`` with 4 workers unless the tuple names a platform and a worker count.
    A version 1 or 2 history's run line has no ``cpus``; a version 3 one's maps each report to
    the CPU model it ran on (``cpus``, none recorded unless given)."""
    head = {"kind": "run", "schema": schema, "version": version, "run_id": run}
    if version >= 3:
        head["cpus"] = cpus or {}
    lines = [dict(head, attempt=1)]
    for report, test, seconds, *where in tests:
        platform, workers = where or (LINUX, 4)
        lines.append(
            {
                "kind": "test",
                "run_id": run,
                "attempt": 1,
                "report": report,
                "test": test,
                "seconds": seconds,
                "platform": platform,
                "workers": workers,
            }
        )
    return "".join(json.dumps(x) + "\n" for x in lines).encode()


def _history(run: int, *tests: tuple, schema: str = telemetry.SCHEMA, **head) -> bytes:
    return _zip(f"history-{run}-1.jsonl", _jsonl(run, *tests, schema=schema, **head))


def _producer_of(run: int) -> int:
    """The collector run that uploads run ``run``'s history: on GitHub an artifact names the run
    that uploaded it, which is the ``ci-telemetry.yml`` run, never the CI run it describes."""
    return run * 100 + 1


#: The uploading run, as ``actions/runs/<id>`` reports it.
COLLECTOR_RUN = {
    "path": telemetry.COLLECTOR_PATH,
    "event": "workflow_run",
    "head_branch": "main",
    "head_repository": {"full_name": "o/r"},
}


class _Gh:
    """``gh`` as the record calls it: main's completed runs, each run's attempt-1 history
    artifact listed by name the way the API lists it, with the run that uploaded it, and that
    uploading run. As on GitHub, the uploader is the collector's own run, not the CI run."""

    def __init__(self, histories: dict[int, bytes | None] | None = None) -> None:
        self.histories: dict[int, bytes | None] = histories or {}
        self.sizes: dict[int, int] = {}
        self.made_by: dict[int, dict] = {}
        self.producers: dict[int, dict] = {}
        self.owners: dict[int, str] = {}
        self.conclusions: dict[int, str] = {}
        self.asked: list[str] = []
        self.downloads: list[int] = []
        self.broken = False

    def text(self, *args: str) -> str:
        path = next(a for a in args[1:] if not a.startswith("-"))
        self.asked.append(path)
        if self.broken:
            raise subprocess.CalledProcessError(1, ["gh", *args])
        if "/runs?" in path:
            assert "branch=main" in path and "status=completed" in path, path
            return "\n".join(
                f"{r} {self.conclusions.get(r, 'success')} {self.owners.get(r, 'o/r')}"
                for r in sorted(self.histories, reverse=True)
            )
        if "/actions/runs/" in path and "?" not in path:
            producer = int(path.rsplit("/", 1)[1])
            known = {_producer_of(r) for r in self.histories}
            # Any other id is some other workflow's run: a pull request's CI run, say.
            other = dict(COLLECTOR_RUN, path=telemetry.CI_PATH, event="pull_request")
            uploaded = dict(COLLECTOR_RUN if producer in known else other)
            return json.dumps(dict(uploaded, **self.producers.get(producer, {}), id=producer))
        if "/actions/artifacts?name=" in path:
            _, _, run_text, attempt = path.split("name=")[1].split("&")[0].split("-")
            run = int(run_text)
            data = self.histories.get(run)
            if data is None or attempt != "1":
                return json.dumps({"artifacts": []})
            made = {"id": _producer_of(run), "head_branch": "main", "head_repository_id": REPO_ID}
            made["repository_id"] = REPO_ID
            artifact = {
                "id": run * 10,
                "expired": False,
                "size_in_bytes": self.sizes.get(run, len(data)),
                "workflow_run": dict(made, **self.made_by.get(run, {})),
            }
            return json.dumps({"artifacts": [artifact]})
        raise AssertionError(f"the record asked gh for {args}")

    def binary(self, *args: str) -> bytes:
        path = next(a for a in args[1:] if not a.startswith("-"))
        self.asked.append(path)
        run = int(path.split("/artifacts/")[1].split("/")[0]) // 10
        self.downloads.append(run)
        data = self.histories[run]
        assert data is not None
        return data


def _record(gh: _Gh) -> shards.TelemetryRecord:
    return shards.TelemetryRecord("o/r", text=gh.text, binary=gh.binary)


# ── the command line ──────────────────────────────────────────────────────────────────────────


def _plan(tmp_path: Path, monkeypatch, gh: _Gh, *legs: str) -> tuple[int, dict]:
    root = _tree(tmp_path, *FILES, "tests/test_c.py")
    monkeypatch.setattr(shards, "_gh_text", gh.text)
    monkeypatch.setattr(shards, "_gh_binary", gh.binary)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out = tmp_path / "plan" / "plan.json"
    argv = ["plan", "--repo", "o/r", "--root", str(root), "--out", str(out)]
    for leg in legs:
        argv += ["--leg", leg]
    code = shards.main(argv)
    return code, (json.loads(out.read_text("utf-8")) if out.exists() else {})


def test_plan_writes_one_plan_for_every_leg(tmp_path: Path, monkeypatch) -> None:
    gh = _Gh(
        {
            20: _history(
                20,
                ("junit-test-shard-1", "tests.test_a::t", 30.0),
                ("junit-test-shard-1", "tests.test_ab::t", 1.0),
                ("junit-test-macos-1", "tests.test_a::t", 30.0),
            )
        }
    )
    code, plan = _plan(
        tmp_path,
        monkeypatch,
        gh,
        "test-shard:ci.yml:junit-test-shard-:2",
        "test-macos:ci.yml:junit-test-macos-:3",
    )
    assert code == 0
    assert plan["version"] == shards.VERSION
    assert set(plan["legs"]) == {"test-shard", "test-macos"}
    assert len(plan["legs"]["test-shard"]["buckets"]) == 2
    assert len(plan["legs"]["test-macos"]["buckets"]) == 3
    assert "tests/test_a.py" in plan["legs"]["test-shard"]["buckets"][0], "heaviest first"
    assert "telemetry runs 20" in plan["legs"]["test-shard"]["durations"]


#: Durations files another tool, or an older version of this one, would leave behind: pytest-split's
#: own, and a plain JSON table. Each gives weights that plan the shards differently from the record.
STRAY_DURATIONS = (".test_durations", "durations.json")


@pytest.mark.parametrize("where", ["root", "cwd"])
@pytest.mark.parametrize("name", STRAY_DURATIONS)
def test_a_planted_second_durations_file_changes_nothing(
    tmp_path: Path, monkeypatch, name: str, where: str
) -> None:
    """The plan's one source is the telemetry record. A second durations file, in the repository
    or in the folder the plan runs from, is never read, so it changes no file's shard."""
    record = {
        20: _history(
            20,
            ("junit-test-shard-1", "tests.test_a::t", 30.0),
            ("junit-test-shard-1", "tests.test_ab::t", 1.0),
            ("junit-test-shard-1", "tests.sub.test_s::t", 2.0),
        )
    }
    leg = "test-shard:ci.yml:junit-test-shard-:2"
    (tmp_path / "alone").mkdir()
    code, alone = _plan(tmp_path / "alone", monkeypatch, _Gh(dict(record)), leg)
    assert code == 0

    planted = tmp_path / "planted"
    planted.mkdir()
    # Weights that would move tests/test_a.py, the heaviest file, out of its shard, and change
    # every bucket.
    stray = {
        "tests/test_a.py": 0.1,
        "tests/test_ab.py": 500.0,
        "tests/sub/test_s.py": 400.0,
        "tests/test_c.py": 300.0,
    }
    folder = planted if where == "root" else tmp_path / "cwd"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(json.dumps(stray), encoding="utf-8")
    monkeypatch.chdir(tmp_path / "cwd" if where == "cwd" else planted)
    from_stray = shards.plan(sorted([*FILES, "tests/test_c.py"]), stray, 2)
    code, with_stray = _plan(planted, monkeypatch, _Gh(dict(record)), leg)

    assert code == 0
    buckets = with_stray["legs"]["test-shard"]["buckets"]
    assert buckets == alone["legs"]["test-shard"]["buckets"]
    assert (
        buckets != from_stray
    ), "the planted weights must plan differently, or this proves nothing"
    assert "telemetry runs 20" in with_stray["legs"]["test-shard"]["durations"]


def test_plan_still_writes_a_complete_plan_when_durations_are_unreadable(
    tmp_path: Path, monkeypatch
) -> None:
    """Durations only balance the shards: without them every file weighs the same."""
    gh = _Gh()
    gh.broken = True
    code, plan = _plan(tmp_path, monkeypatch, gh, "test-shard:ci.yml:junit-test-shard-:2")
    assert code == 0
    files = sorted(f for b in plan["legs"]["test-shard"]["buckets"] for f in b)
    assert files == sorted([*FILES, "tests/test_c.py"])
    assert "unreadable" in plan["legs"]["test-shard"]["durations"]


def _plan_file(tmp_path: Path, buckets: list[list[str]], n: int | None = None) -> Path:
    path = tmp_path / "plan.json"
    legs = {"test-shard": {"n": n or len(buckets), "buckets": buckets, "durations": "x"}}
    path.write_text(json.dumps({"version": shards.VERSION, "legs": legs}), encoding="utf-8")
    return path


def _list(tmp_path: Path, plan: Path, shard: str, leg: str = "test-shard") -> tuple[int, Path]:
    out = tmp_path / "shard.txt"
    argv = ["list", "--plan", str(plan), "--leg", leg, "--shard", shard, "--out", str(out)]
    return shards.main(argv), out


def test_list_writes_one_shards_files_one_per_line_and_their_digest(
    tmp_path: Path, monkeypatch
) -> None:
    """The digest is a breadth shard's input: a carried result was measured on these files."""
    said = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(said))
    plan = _plan_file(tmp_path, [["tests/test_a.py", "tests/test_b.py"], ["tests/test_c.py"]])
    code, out = _list(tmp_path, plan, "1/2")
    assert code == 0
    assert out.read_text(encoding="utf-8").splitlines() == ["tests/test_a.py", "tests/test_b.py"]
    first = said.read_text(encoding="utf-8").strip()
    assert first.startswith("digest=") and len(first) == len("digest=") + 64
    said.write_text("", encoding="utf-8")
    assert _list(tmp_path, plan, "2/2")[0] == 0
    assert said.read_text(encoding="utf-8").strip() != first, "another list, another digest"


@pytest.mark.parametrize(
    ("buckets", "shard", "leg"),
    [
        ([["tests/test_a.py"], []], "2/2", "test-shard"),
        ([["tests/test_a.py"], ["tests/test_b.py"]], "1/3", "test-shard"),
        ([["tests/test_a.py"], ["tests/test_b.py"]], "3/2", "test-shard"),
        ([["tests/test_a.py"], ["tests/test_b.py"]], "0/2", "test-shard"),
        ([["tests/test_a.py"], ["tests/test_b.py"]], "1/2", "test-macos"),
        ([["tests/test_a.py"], ["tests/test_b.py"]], "one", "test-shard"),
    ],
    ids=["an empty shard", "another N", "past N", "shard 0", "no such leg", "not i/N"],
)
def test_list_fails_and_writes_nothing_when_it_cannot_list(
    tmp_path: Path, buckets: list[list[str]], shard: str, leg: str
) -> None:
    """pytest given no paths falls back to `testpaths`, so an empty list must never be written."""
    plan = _plan_file(tmp_path, buckets)
    code, out = _list(tmp_path, plan, shard, leg)
    assert code != 0
    assert not out.exists()


def test_list_fails_on_a_plan_it_cannot_read(tmp_path: Path) -> None:
    code, out = _list(tmp_path, tmp_path / "missing.json", "1/2")
    assert code != 0 and not out.exists()


def test_check_fails_on_a_planted_unlisted_file_and_passes_the_plan_as_made(
    tmp_path: Path, monkeypatch
) -> None:
    gh = _Gh()
    code, _ = _plan(tmp_path, monkeypatch, gh, "test-shard:ci.yml:junit-test-shard-:2")
    assert code == 0
    plan = tmp_path / "plan" / "plan.json"
    argv = ["check", "--plan", str(plan), "--root", str(tmp_path)]
    assert shards.main(argv) == 0
    (tmp_path / "tests" / "test_planted.py").write_text("", encoding="utf-8")
    assert shards.main(argv) != 0


def test_the_package_runs_the_shards_command() -> None:
    """`python -m tools.ci shards …`, as the workflows call it."""
    done = subprocess.run(
        [sys.executable, "-m", "tools.ci", "shards", "--help"],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert done.returncode == 0, done.stderr
    assert "plan" in done.stdout and "list" in done.stdout and "check" in done.stdout


# ── a failed durations fetch never stops the shards ─────────────────────────────────────────────


def _corrupt_deflate() -> bytes:
    """A zip whose directory reads but whose compressed member does not: a zlib error, not a
    BadZipFile, on read."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "history-20-1.jsonl", _jsonl(20, *[("junit-test-shard-1", "t", 1.0)] * 200)
        )
    raw = buffer.getvalue()
    return raw[:40] + bytes(b ^ 0xFF for b in raw[40:120]) + raw[120:]


def _raising(exc: BaseException):
    def call(*args: str) -> str:
        raise exc

    return call


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, ["gh", "api"], stderr="HTTP 502"),
        subprocess.CalledProcessError(1, ["gh", "api"], stderr="API rate limit exceeded"),
        subprocess.TimeoutExpired(["gh", "api"], 120),
        RuntimeError("an answer of the wrong shape"),
    ],
    ids=["server error", "rate limit", "timeout", "anything else"],
)
def test_a_durations_fetch_that_errors_still_gives_a_checked_plan(
    error: BaseException, tmp_path: Path, monkeypatch, capsys
) -> None:
    gh = _Gh()
    code, plan = _plan(tmp_path, monkeypatch, gh, "test-shard:ci.yml:junit-test-shard-:2")
    assert code == 0
    monkeypatch.setattr(shards, "_gh_text", _raising(error))
    monkeypatch.setattr(shards, "_gh_binary", _raising(error))
    out = tmp_path / "plan" / "plan.json"
    argv = ["plan", "--repo", "o/r", "--root", str(tmp_path), "--out", str(out)]
    assert shards.main([*argv, "--leg", "test-shard:ci.yml:junit-test-shard-:2"]) == 0
    leg = json.loads(out.read_text("utf-8"))["legs"]["test-shard"]
    assert "unreadable" in leg["durations"] and type(error).__name__ in leg["durations"]
    assert "::warning title=shard durations unreadable::" in capsys.readouterr().out
    assert sorted(f for b in leg["buckets"] for f in b) == sorted([*FILES, "tests/test_c.py"])
    assert shards.main(["check", "--plan", str(out), "--root", str(tmp_path)]) == 0


@pytest.mark.parametrize(
    "archive",
    [b"not a zip", b"", _corrupt_deflate()],
    ids=["not a zip", "empty", "a member that does not inflate"],
)
def test_a_corrupt_history_archive_still_gives_a_checked_plan(
    archive: bytes, tmp_path: Path, monkeypatch, capsys
) -> None:
    """A history that cannot be read is skipped and named in the log; with no other the plan
    weighs every file the same, and says so."""
    gh = _Gh({20: archive})
    code, plan = _plan(tmp_path, monkeypatch, gh, "test-shard:ci.yml:junit-test-shard-:2")
    assert code == 0
    leg = plan["legs"]["test-shard"]
    assert "every file weighs the same" in leg["durations"]
    assert "::warning title=telemetry history unreadable::run 20" in capsys.readouterr().out
    out = tmp_path / "plan" / "plan.json"
    assert shards.main(["check", "--plan", str(out), "--root", str(tmp_path)]) == 0


def test_check_still_fails_a_plan_that_is_not_whole_after_a_failed_fetch(
    tmp_path: Path, monkeypatch
) -> None:
    """The fallback balances nothing and proves nothing: `shards check` is still the proof."""
    gh = _Gh()
    gh.broken = True
    code, _ = _plan(tmp_path, monkeypatch, gh, "test-shard:ci.yml:junit-test-shard-:2")
    assert code == 0
    out = tmp_path / "plan" / "plan.json"
    (tmp_path / "tests" / "test_planted.py").write_text("", encoding="utf-8")
    assert shards.main(["check", "--plan", str(out), "--root", str(tmp_path)]) != 0


# ── on main, breadth keeps the earlier run's plan while it still plans today's files ──────────


class _KeepGh:
    """``gh`` for ``plan --keep-from-main``: main's completed breadth runs and their plans."""

    def __init__(self, runs: dict[int, bytes | None], *, broken: bool = False) -> None:
        self.plans, self.broken, self.asked = runs, broken, []

    def text(self, *args: str) -> str:
        path = next(a for a in args[1:] if not a.startswith("-"))
        self.asked.append(path)
        if self.broken:
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 502")
        if "/actions/artifacts?name=" in path:
            return json.dumps({"artifacts": []})
        if "/runs?" in path:
            assert "branch=main" in path, path
            return "\n".join(str(r) for r in self.plans) if "status=completed" in path else ""
        if "/artifacts?" in path:
            run = int(path.split("/runs/")[1].split("/")[0])
            return f"{run * 10} shard-plan" if self.plans.get(run) is not None else "1 junit-x"
        raise AssertionError(f"plan asked gh for {args}")

    def binary(self, *args: str) -> bytes:
        path = next(a for a in args[1:] if not a.startswith("-"))
        data = self.plans[int(path.split("/artifacts/")[1].split("/")[0]) // 10]
        assert data is not None
        return _zip("plan.json", data)


ALL = sorted([*FILES, "tests/test_c.py"])


def _earlier(buckets: list[list[str]], n: int = 2, leg: str = "ubuntu") -> bytes:
    legs = {leg: {"n": n, "buckets": buckets, "durations": "x"}}
    return json.dumps({"version": shards.VERSION, "legs": legs}).encode()


def _keep(tmp_path: Path, monkeypatch, gh: _KeepGh, *, keep: bool = True, n: int = 2):
    root = _tree(tmp_path, *ALL)
    monkeypatch.setattr(shards, "_gh_text", gh.text)
    monkeypatch.setattr(shards, "_gh_binary", gh.binary)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out = tmp_path / "plan" / "plan.json"
    argv = ["plan", "--repo", "o/r", "--root", str(root), "--run-id", "99", "--out", str(out)]
    argv += ["--leg", f"ubuntu:breadth.yml:junit-ubuntu-:{n}"]
    if keep:
        argv += ["--keep-from-main", "breadth.yml"]
    assert shards.main(argv) == 0
    assert shards.main(["check", "--plan", str(out), "--root", str(root)]) == 0
    return json.loads(out.read_text("utf-8"))["legs"]["ubuntu"]


# An earlier plan no fresh one would make: the files planned in one odd split.
ODD = [["tests/test_a.py"], ["tests/sub/test_s.py", "tests/test_ab.py", "tests/test_c.py"]]


def test_main_keeps_the_newest_earlier_plan_that_still_plans_todays_files(
    tmp_path: Path, monkeypatch
) -> None:
    """A shard's list digest is one of its inputs: a plan remade each night would hand every
    shard other files, and no tree-keyed shard would ever carry its result."""
    gh = _KeepGh({30: None, 20: _earlier(ODD), 10: _earlier([ALL[:2], ALL[2:]])})
    leg = _keep(tmp_path, monkeypatch, gh)
    assert leg["buckets"] == ODD
    assert "kept run 20's plan" in leg["durations"]


def test_a_changed_set_of_files_plans_afresh(tmp_path: Path, monkeypatch) -> None:
    gh = _KeepGh({20: _earlier([["tests/test_a.py"], ["tests/test_ab.py", "tests/test_gone.py"]])})
    leg = _keep(tmp_path, monkeypatch, gh)
    assert "does not plan today's files" in leg["durations"]
    assert sorted(f for b in leg["buckets"] for f in b) == ALL


def test_another_number_of_shards_plans_afresh(tmp_path: Path, monkeypatch) -> None:
    gh = _KeepGh({20: _earlier(ODD)})
    leg = _keep(tmp_path, monkeypatch, gh, n=3)
    assert len(leg["buckets"]) == 3 and "does not plan today's files" in leg["durations"]


def test_an_unreadable_earlier_plan_still_gives_a_checked_fresh_plan(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    leg = _keep(tmp_path, monkeypatch, _KeepGh({20: _earlier(ODD)}, broken=True))
    assert "::warning title=no earlier shard plan kept::" in capsys.readouterr().out
    assert sorted(f for b in leg["buckets"] for f in b) == ALL
    assert "kept" not in leg["durations"]


def test_without_keep_no_earlier_plan_is_read(tmp_path: Path, monkeypatch) -> None:
    """Off main nothing is reused, so `freshness` passes no --keep-from-main there."""
    gh = _KeepGh({20: _earlier(ODD)})
    leg = _keep(tmp_path, monkeypatch, gh, keep=False)
    # The durations source lists main's runs too; only the keep reads a run's plan artifacts.
    assert not any("/artifacts?" in a for a in gh.asked)
    assert leg["buckets"] != ODD


def test_the_run_never_keeps_its_own_plan(tmp_path: Path, monkeypatch) -> None:
    gh = _KeepGh({99: _earlier(ODD)})
    leg = _keep(tmp_path, monkeypatch, gh)
    assert leg["buckets"] != ODD and "kept" not in leg["durations"]


# ── the record: what it reads, and what it never reads ─────────────────────────────────────────


def test_the_record_gives_each_files_median_over_the_newest_three_runs_first_attempts() -> None:
    """Each file's seconds per run are its tests' first-attempt seconds in the leg's own
    reports; the record takes the median over the three newest runs with a history."""
    t = "tests.test_a::t"
    gh = _Gh(
        {
            50: _history(
                50, ("junit-test-shard-1", t, 2.0), ("junit-test-shard-2", "tests.test_a.K::u", 1.0)
            ),
            40: _history(40, ("junit-test-shard-1", t, 90.0)),
            30: _history(30, ("junit-test-shard-1", t, 4.0), ("junit-test-macos-1", t, 500.0)),
            20: _history(20, ("junit-test-shard-1", t, 900.0)),
        }
    )
    durations, said = _record(gh).durations(LEG, FILES)
    assert durations == {"tests/test_a.py": 4.0}, "median of 3, 90 and 4; macOS's is another leg"
    assert "telemetry runs 50, 40, 30" in said and LINUX in said and "4 workers" in said
    assert 20 not in gh.downloads, "a fourth run is never read"


def test_the_record_reads_only_attempt_one_the_first_results() -> None:
    gh = _Gh({50: _history(50, ("junit-test-shard-1", "tests.test_a::t", 2.0))})
    _record(gh).durations(LEG, FILES)
    asked = [a for a in gh.asked if "name=" in a]
    assert asked and all("telemetry-history-50-1&" in a for a in asked), asked


def test_a_run_with_no_history_is_skipped() -> None:
    gh = _Gh({50: None, 40: _history(40, ("junit-test-shard-1", "tests.test_ab::t", 7.0))})
    durations, said = _record(gh).durations(LEG, FILES)
    assert durations == {"tests/test_ab.py": 7.0} and "runs 40" in said


@pytest.mark.parametrize(
    "made_by",
    [
        {"id": 999, "head_repository_id": 2},
        {"head_repository_id": 2},
        {"head_branch": "feature-x"},
        {"id": 999},
    ],
    ids=["a fork's run", "a fork's run of that id", "another branch", "another run"],
)
def test_a_history_of_that_name_another_run_made_is_never_downloaded(made_by: dict) -> None:
    """A pull request's run, a fork's included, can upload an artifact with any name. Only one
    made by that very run, on main, in this repository, is read."""
    gh = _Gh({50: _history(50, ("junit-test-shard-1", "tests.test_a::t", 2.0))})
    gh.made_by[50] = made_by
    durations, said = _record(gh).durations(LEG, FILES)
    assert durations == {} and "every file weighs the same" in said
    assert gh.downloads == []


@pytest.mark.parametrize(
    "producer",
    [
        {"event": "pull_request"},
        {"path": telemetry.CI_PATH},
        {"head_branch": "feature-x"},
        {"head_repository": {"full_name": "someone/fork"}},
    ],
    ids=["a pull request's run", "another workflow", "another branch", "a fork's run"],
)
def test_only_the_collectors_own_upload_is_read(producer: dict) -> None:
    """A history is read only when the run that uploaded it is the collector: a
    ``ci-telemetry.yml`` run, triggered by ``workflow_run``, on main, in this repository."""
    gh = _Gh({50: _history(50, ("junit-test-shard-1", "tests.test_a::t", 2.0))})
    gh.producers[_producer_of(50)] = producer
    durations, said = _record(gh).durations(LEG, FILES)
    assert durations == {} and "every file weighs the same" in said
    assert gh.downloads == []


def test_a_history_whose_run_line_names_another_run_is_skipped(capsys) -> None:
    """The collector writes the CI run's id in the history's first line; a history uploaded
    under run 50's name that describes run 40 is not run 50's."""
    gh = _Gh({50: _history(40, ("junit-test-shard-1", "tests.test_a::t", 2.0))})
    durations, _ = _record(gh).durations(LEG, FILES)
    assert durations == {}
    assert "describes run 40, not run 50" in capsys.readouterr().out


def test_a_forks_run_listed_under_main_is_skipped() -> None:
    """The API lists a fork's pull-request run from a branch the fork named main under
    branch=main; the record reads only this repository's runs."""
    gh = _Gh(
        {
            50: _history(50, ("junit-test-shard-1", "tests.test_a::t", 999.0)),
            40: _history(40, ("junit-test-shard-1", "tests.test_a::t", 2.0)),
        }
    )
    gh.owners[50] = "someone/fork"
    durations, said = _record(gh).durations(LEG, FILES)
    assert durations == {"tests/test_a.py": 2.0} and "runs 40" in said and 50 not in gh.downloads


@pytest.mark.parametrize(
    "data",
    [
        _history(40, ("junit-test-shard-1", "tests.test_a::t", 2.0), schema="something-else"),
        _zip("history-40-1.jsonl", b"not json\n"),
        b"not a zip",
    ],
    ids=["another schema", "not json", "not a zip"],
)
def test_a_history_the_reader_does_not_understand_is_skipped_and_named(data: bytes, capsys) -> None:
    gh = _Gh({40: data})
    durations, said = _record(gh).durations(LEG, FILES)
    assert durations == {} and "no telemetry history" in said
    assert "::warning title=telemetry history unreadable::run 40" in capsys.readouterr().out


def test_a_history_archive_over_the_limit_is_never_downloaded() -> None:
    gh = _Gh({40: _history(40, ("junit-test-shard-1", "tests.test_a::t", 2.0))})
    gh.sizes[40] = shards.MAX_HISTORY_ARCHIVE_BYTES + 1
    durations, _ = _record(gh).durations(LEG, FILES)
    assert durations == {} and gh.downloads == []


def test_a_history_line_over_its_bound_is_refused_before_it_is_read_whole(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(shards, "MAX_HISTORY_LINE_BYTES", 300)
    long_test = "tests.test_a::" + "t" * 400
    gh = _Gh({40: _history(40, ("junit-test-shard-1", long_test, 2.0))})
    durations, _ = _record(gh).durations(LEG, FILES)
    assert durations == {}
    assert "a history line passes 300 bytes" in capsys.readouterr().out


def test_a_history_over_its_total_bound_is_refused(monkeypatch, capsys) -> None:
    rows = [("junit-test-shard-1", "tests.test_a::t", 1.0)] * 50
    data = _history(40, *rows)
    monkeypatch.setattr(shards, "MAX_HISTORY_BYTES", len(_jsonl(40, *rows)) - 1)
    durations, _ = _record(_Gh({40: data})).durations(LEG, FILES)
    assert durations == {}
    assert "the history passes" in capsys.readouterr().out


def test_the_record_reads_only_the_platform_and_workers_its_newest_history_recorded() -> None:
    """A leg that moves to another ``-n`` plans from the first run that recorded the new count:
    the older runs, on the old count, never weigh it."""
    t = "tests.test_a::t"
    gh = _Gh(
        {
            50: _history(50, ("junit-test-shard-1", t, 3.0, LINUX, 3)),
            40: _history(40, ("junit-test-shard-1", t, 9.0, LINUX, 2)),
            30: _history(30, ("junit-test-shard-1", t, 5.0, LINUX, 3)),
            20: _history(20, ("junit-test-shard-1", t, 9.0, "darwin-arm64-3.13.16", 3)),
        }
    )
    durations, said = _record(gh).durations(LEG, FILES)
    assert durations == {"tests/test_a.py": 4.0}, "the median of runs 50 and 30 only"
    assert "runs 50, 30" in said and "3 workers" in said


def test_each_history_is_downloaded_once_for_every_leg() -> None:
    t = "tests.test_a::t"
    gh = _Gh(
        {
            run: _history(run, ("junit-test-shard-1", t, 2.0), ("junit-test-macos-1", t, 4.0))
            for run in (50, 40, 30)
        }
    )
    record = _record(gh)
    macos = shards.Leg("test-macos", "ci.yml", "junit-test-macos-", 4)
    assert record.durations(LEG, FILES)[0] == {"tests/test_a.py": 2.0}
    assert record.durations(macos, FILES)[0] == {"tests/test_a.py": 4.0}
    assert sorted(gh.downloads) == [30, 40, 50]


def test_with_no_record_yet_the_plan_weighs_every_file_the_same_and_says_so(
    tmp_path: Path, monkeypatch
) -> None:
    """Until the first collected main run the record holds nothing, and there is no other
    source: the plan is whole, unbalanced, and says why."""
    gh = _Gh()
    code, plan = _plan(tmp_path, monkeypatch, gh, "test-shard:ci.yml:junit-test-shard-:2")
    assert code == 0
    leg = plan["legs"]["test-shard"]
    assert "no telemetry history" in leg["durations"]
    assert "every file weighs the same" in leg["durations"]
    assert not any("junit-" in a for a in gh.asked if "/artifacts" in a), "no junit is read"


# ── host factors: seconds measured on a slower CPU model weigh what the file costs ─────────────

SLOW, FAST = "EPYC slow 64-Core", "EPYC fast 96-Core"
A, S, AB = "tests.test_a::t", "tests.sub.test_s::t", "tests.test_ab::t"


def _hosted(run: int, a: float, s: float, ab: float, models: tuple, **head) -> bytes:
    """One run of three reports, one file each, each report on the CPU model ``models`` names."""
    reports = ("junit-test-shard-1", "junit-test-shard-2", "junit-test-shard-3")
    return _history(
        run,
        (reports[0], A, a),
        (reports[1], S, s),
        (reports[2], AB, ab),
        cpus=dict(zip(reports, models, strict=True)),
        **head,
    )


#: Every file costs 10 s on the fast model and 20 s on the slow one. ``test_a`` drew the slow
#: model in two of the three runs, so its plain median says 20 s.
HOSTED = {
    50: (20.0, 10.0, 10.0, (SLOW, FAST, FAST)),
    40: (20.0, 10.0, 20.0, (SLOW, FAST, SLOW)),
    30: (10.0, 20.0, 10.0, (FAST, SLOW, FAST)),
}
#: Plain medians: test_a 20, the others 10. Each model's factor is the median of ran / planned
#: over the reports that ran on it: slow 20/20, 20/20, 20/10, 20/10 is 1.5; fast 10/10 four
#: times and 10/20 once is 1.0. Divided by its host's factor, test_a ran 13.3, 13.3 and 10 s.
HOSTED_DURATIONS = {
    "tests/test_a.py": pytest.approx(40 / 3),
    "tests/sub/test_s.py": 10.0,
    "tests/test_ab.py": 10.0,
}


def test_the_record_divides_each_runs_seconds_by_its_hosts_factor() -> None:
    gh = _Gh({run: _hosted(run, *row) for run, row in HOSTED.items()})
    durations, said = _record(gh).durations(LEG, FILES)
    assert durations == HOSTED_DURATIONS, "the plain median would weigh test_a at 20 s"
    assert f"{SLOW} 1.50" in said and f"{FAST} 1.00" in said, said


def test_a_report_with_no_recorded_cpu_model_keeps_its_seconds() -> None:
    """A report whose CPU model was not recorded has no factor to take, so its seconds weigh
    as they ran (a factor of 1.0), never by some other model's factor or one of its own."""
    gh = _Gh(
        {
            50: _hosted(50, 20.0, 10.0, 10.0, (SLOW, FAST, FAST)),
            40: _hosted(40, 20.0, 20.0, 10.0, (None, SLOW, FAST)),
            30: _hosted(30, 10.0, 10.0, 20.0, (None, FAST, SLOW)),
        }
    )
    durations, said = _record(gh).durations(LEG, FILES)
    # test_a: 20 / 2 (slow), then 20 and 10 as they ran: the median is 10, against 13.3 if the
    # unrecorded reports took a factor of their own (ran / planned 1.0 and 0.5: 0.75).
    assert durations["tests/test_a.py"] == 10.0
    assert "an unrecorded CPU model weighs as it ran" in said, said


@pytest.mark.parametrize("version", [1, 2], ids=["version 1", "version 2"])
def test_an_older_history_is_still_read_and_weighs_as_it_ran(version: int) -> None:
    """Histories written before the CPU model was recorded stay readable for as long as they
    are kept: none records a model, so every factor is 1.0 and the plan is the plain median."""
    gh = _Gh(
        {
            50: _hosted(50, 20.0, 10.0, 10.0, (SLOW, FAST, FAST), version=version),
            40: _hosted(40, 20.0, 20.0, 10.0, (SLOW, SLOW, FAST), version=version),
            30: _hosted(30, 10.0, 10.0, 20.0, (FAST, FAST, SLOW), version=version),
        }
    )
    durations, said = _record(gh).durations(LEG, FILES)
    assert durations == {
        "tests/test_a.py": 20.0,
        "tests/sub/test_s.py": 10.0,
        "tests/test_ab.py": 10.0,
    }
    assert "runs 50, 40, 30" in said


def test_a_history_of_a_newer_version_is_skipped_and_named(capsys) -> None:
    gh = _Gh({40: _hosted(40, 1.0, 1.0, 1.0, (FAST,) * 3, version=telemetry.VERSION + 1)})
    durations, _ = _record(gh).durations(LEG, FILES)
    assert durations == {}
    assert "::warning title=telemetry history unreadable::run 40" in capsys.readouterr().out


def test_the_run_being_planned_never_weighs_its_own_plan_or_its_factors() -> None:
    """A run planning again (a rerun of all its jobs) is listed among main's completed runs once
    its first attempt ended, with that attempt's history. The record never reads it: neither its
    seconds nor its hosts' factors, which would grade the plan by the run it plans."""
    # Run 60's reports would give the slow model a factor near 0 and test_a 900 s.
    histories = {60: _hosted(60, 900.0, 1.0, 1.0, (FAST, SLOW, SLOW))}
    histories.update({run: _hosted(run, *row) for run, row in HOSTED.items()})
    gh = _Gh(histories)
    record = shards.TelemetryRecord("o/r", text=gh.text, binary=gh.binary, planning=60)
    durations, said = record.durations(LEG, FILES)
    assert 60 not in gh.downloads and "runs 50, 40, 30" in said
    assert durations == HOSTED_DURATIONS


def test_the_plan_command_names_its_own_run_to_the_record(tmp_path: Path, monkeypatch) -> None:
    gh = _Gh(
        {
            60: _history(60, ("junit-test-shard-1", A, 900.0)),
            50: _history(50, ("junit-test-shard-1", A, 2.0)),
        }
    )
    monkeypatch.setattr(sys, "argv", ["x"])
    root = _tree(tmp_path, *FILES, "tests/test_c.py")
    monkeypatch.setattr(shards, "_gh_text", gh.text)
    monkeypatch.setattr(shards, "_gh_binary", gh.binary)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out = tmp_path / "plan.json"
    argv = ["plan", "--repo", "o/r", "--root", str(root), "--out", str(out), "--run-id", "60"]
    assert shards.main([*argv, "--leg", "test-shard:ci.yml:junit-test-shard-:2"]) == 0
    assert 60 not in gh.downloads
    assert "runs 50" in json.loads(out.read_text("utf-8"))["legs"]["test-shard"]["durations"]


# ── the union: the shards' test ids together are the planned suite's, each once ─────────────

EXPECTED = ["tests/test_a.py::test_one", "tests/test_a.py::test_two[x]", "tests/test_b.py::test_b"]


def _ids(folder: Path, name: str, ids: list[str]) -> None:
    folder.mkdir(exist_ok=True)
    (folder / name).write_text("".join(f"{line}\n" for line in ids), encoding="utf-8")


def _union(
    tmp_path: Path, expected: list[str] | None, *shard_ids: list[str] | None, leg: str = "ubuntu"
) -> int:
    """Run ``shards union`` over one leg: its planned suite's ids (None writes no file) and each
    shard's ids (None leaves that shard's file out)."""
    folder = tmp_path / "ids"
    folder.mkdir(exist_ok=True)
    if expected is not None:
        _ids(folder, shards.PLANNED_IDS.format(leg=leg), expected)
    for i, ids in enumerate(shard_ids, 1):
        if ids is not None:
            _ids(folder, shards.SHARD_IDS.format(leg=leg, shard=i), ids)
    argv = ["union", "--dir", str(folder), "--leg", f"{leg}:{len(shard_ids)}"]
    return shards.main(argv)


def test_union_passes_when_the_shards_ids_are_the_expected_ids_each_once(tmp_path: Path) -> None:
    assert _union(tmp_path, EXPECTED, EXPECTED[:2], EXPECTED[2:]) == 0


def test_union_fails_on_a_planted_missing_id(tmp_path: Path, capsys) -> None:
    """A test the planned suite collects that no shard collected ran nowhere."""
    assert _union(tmp_path, EXPECTED, EXPECTED[:1], EXPECTED[2:]) != 0
    assert "tests/test_a.py::test_two[x]" in capsys.readouterr().out


def test_union_fails_on_a_planted_extra_id(tmp_path: Path, capsys) -> None:
    """A test a shard collected that the planned suite does not is one the plan never named."""
    extra = "tests/test_c.py::test_extra"
    assert _union(tmp_path, EXPECTED, EXPECTED[:2], [*EXPECTED[2:], extra]) != 0
    assert extra in capsys.readouterr().out


def test_union_fails_on_an_id_two_shards_collected(tmp_path: Path, capsys) -> None:
    assert _union(tmp_path, EXPECTED, EXPECTED[:2], EXPECTED[1:]) != 0
    assert "tests/test_a.py::test_two[x]" in capsys.readouterr().out


def test_union_fails_on_an_id_one_shard_collected_twice(tmp_path: Path, capsys) -> None:
    assert _union(tmp_path, EXPECTED, [*EXPECTED[:2], EXPECTED[0]], EXPECTED[2:]) != 0
    assert "tests/test_a.py::test_one" in capsys.readouterr().out


def test_union_fails_closed_on_a_shard_that_wrote_no_id_file(tmp_path: Path, capsys) -> None:
    """A shard that was cancelled, or failed before collecting, is red, never absent: the ids it
    would have named are not known to have run."""
    assert _union(tmp_path, EXPECTED, EXPECTED, None) != 0
    assert "shard 2" in capsys.readouterr().out


def test_union_fails_closed_on_a_leg_whose_planned_suite_was_not_collected(
    tmp_path: Path, capsys
) -> None:
    assert _union(tmp_path, None, EXPECTED) != 0
    assert "ubuntu" in capsys.readouterr().out


def test_union_fails_on_an_expected_collection_that_names_no_test(tmp_path: Path) -> None:
    """Nothing against nothing would pass: an empty planned collection is one that failed."""
    assert _union(tmp_path, [], [], []) != 0


def test_union_reports_every_leg_before_it_fails(tmp_path: Path, capsys) -> None:
    """One command checks every leg, so a red names each leg that is short, not only the first."""
    folder = tmp_path / "ids"
    for leg in ("ubuntu", "macos"):
        _ids(folder, shards.PLANNED_IDS.format(leg=leg), EXPECTED)
        _ids(folder, shards.SHARD_IDS.format(leg=leg, shard=1), EXPECTED[:2])
    argv = ["union", "--dir", str(folder), "--leg", "ubuntu:1", "--leg", "macos:1"]
    assert shards.main(argv) != 0
    out = capsys.readouterr().out
    assert "ubuntu: tests/test_b.py::test_b" in out and "macos: tests/test_b.py::test_b" in out


def test_union_fails_on_a_planned_collection_that_holds_an_id_twice(tmp_path: Path, capsys) -> None:
    """A planned suite that would run a test twice is no suite to hold the shards to, each once."""
    planned = [*EXPECTED, EXPECTED[2]]
    assert _union(tmp_path, planned, EXPECTED[:2], EXPECTED[2:]) != 0
    assert "tests/test_b.py::test_b is in the planned suite 2 times" in capsys.readouterr().out


@pytest.mark.parametrize("unreadable", ["planned", "shard"])
def test_union_names_an_id_file_that_is_not_text_and_still_checks_every_leg(
    tmp_path: Path, unreadable: str, capsys
) -> None:
    """An id file that is not UTF-8 text is that leg's red, by name, and the legs after it are
    still checked, so one red names every leg that is short."""
    folder = tmp_path / "ids"
    for leg in ("ubuntu", "macos"):
        _ids(folder, shards.PLANNED_IDS.format(leg=leg), EXPECTED)
        _ids(folder, shards.SHARD_IDS.format(leg=leg, shard=1), EXPECTED[:2])
    name = shards.PLANNED_IDS if unreadable == "planned" else shards.SHARD_IDS
    (folder / name.format(leg="ubuntu", shard=1)).write_bytes(b"\xff\xfe\n")
    argv = ["union", "--dir", str(folder), "--leg", "ubuntu:1", "--leg", "macos:1"]
    assert shards.main(argv) != 0
    out = capsys.readouterr().out
    assert f"ubuntu: {name.format(leg='ubuntu', shard=1)} is not UTF-8 text" in out
    assert "macos: tests/test_b.py::test_b" in out


@pytest.mark.parametrize("leg", ["ubuntu", "ubuntu:0", "ubuntu:x", ":2"])
def test_union_refuses_a_leg_it_cannot_read(tmp_path: Path, leg: str, capsys) -> None:
    """Refused as written, before any file is read: a leg of no shards is never a leg."""
    folder = tmp_path / "ids"
    _ids(folder, shards.PLANNED_IDS.format(leg="ubuntu"), EXPECTED)
    assert shards.main(["union", "--dir", str(folder), "--leg", leg]) != 0
    assert "--leg is name:shards" in capsys.readouterr().err


def test_universe_writes_the_files_every_shard_of_a_plan_lists(tmp_path: Path) -> None:
    """The planned suite's collection reads the same exclusions the plan does: the rails and the
    files a job owns are out."""
    root = _tree(tmp_path, "tests/test_a.py", "tests/sub/b_test.py", "tests/rails/test_r.py")
    (root / "tests" / "test_website_installer.py").write_text("", encoding="utf-8")
    out = tmp_path / "universe.txt"
    assert shards.main(["universe", "--root", str(root), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").splitlines() == shards.universe(root)
    assert out.read_text(encoding="utf-8").splitlines() == [
        "tests/sub/b_test.py",
        "tests/test_a.py",
    ]


def test_universe_fails_and_writes_nothing_on_a_tree_with_no_test_file(tmp_path: Path) -> None:
    """pytest given an empty list would collect every test, so none is ever written."""
    root = _tree(tmp_path, "tests/helper.py")
    out = tmp_path / "universe.txt"
    assert shards.main(["universe", "--root", str(root), "--out", str(out)]) != 0
    assert not out.exists()
