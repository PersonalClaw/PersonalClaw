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


def test_security_paths_stay_in_the_shards_until_a_merge_tier_job_runs_them(
    tmp_path: Path,
) -> None:
    """No pull-request run has a security job yet, so selecting them out would drop them."""
    root = _tree(tmp_path, "tests/security/test_z.py", "tests/test_supply_chain_gates.py")
    buckets = shards.plan(shards.universe(root), {}, 2)
    assert sorted(f for b in buckets for f in b) == [
        "tests/security/test_z.py",
        "tests/test_supply_chain_gates.py",
    ]


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


def _jsonl(run: int, *tests: tuple, schema: str = telemetry.SCHEMA) -> bytes:
    """One collected history: a run line, then a test line per ``(report, test, seconds)``,
    each on ``LINUX`` with 4 workers unless the tuple names a platform and a worker count."""
    head = {"kind": "run", "schema": schema, "version": telemetry.VERSION, "run_id": run}
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


def _history(run: int, *tests: tuple, schema: str = telemetry.SCHEMA) -> bytes:
    return _zip(f"history-{run}-1.jsonl", _jsonl(run, *tests, schema=schema))


class _Gh:
    """``gh`` as the record calls it: main's completed runs, and each run's attempt-1 history
    artifact, listed by name the way the API lists it, with the run that made it."""

    def __init__(self, histories: dict[int, bytes | None] | None = None) -> None:
        self.histories: dict[int, bytes | None] = histories or {}
        self.sizes: dict[int, int] = {}
        self.made_by: dict[int, dict] = {}
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
        if "/actions/artifacts?name=" in path:
            _, _, run_text, attempt = path.split("name=")[1].split("&")[0].split("-")
            run = int(run_text)
            data = self.histories.get(run)
            if data is None or attempt != "1":
                return json.dumps({"artifacts": []})
            made = {"id": run, "head_branch": "main", "head_repository_id": REPO_ID}
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
