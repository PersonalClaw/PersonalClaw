"""What built each release artifact is read from what ran, not from the workflow text (#3911).

``tools/ci/build_identity.py`` takes each artifact job's own record (its runner, toolchain, bases
and artifacts) and that job's set-up log, read from the API by its runner's name: the runner
image GitHub reports there and every action the job downloaded, nested ones included, each with
the SHA it resolved to. The log below is shaped as a real one is: a timestamp on every line, a
byte-order mark on the first, and the runner image in its own group.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.ci import build_identity as bi

CHECKOUT = "3d3c42e5aac5ba805825da76410c181273ba90b1"
UV = "1c37ad07a6a961277cf70c0d37d6f313000f5884"
#: A nested action: the composite one downloads it, and no workflow names it.
NESTED = "b" * 40

SET_UP = f"""﻿2026-10-23T14:50:27Z Current runner version: '2.338.0'
2026-10-23T14:50:27Z ##[group]Runner Image
2026-10-23T14:50:27Z Image: ubuntu-24.04
2026-10-23T14:50:27Z Version: 20261012.334.1
2026-10-23T14:50:27Z Included Software: https://github.com/actions/runner-images/blob/x.md
2026-10-23T14:50:27Z ##[endgroup]
2026-10-23T14:50:27Z Prepare all required actions
2026-10-23T14:50:27Z Getting action download info
2026-10-23T14:50:27Z Download action repository 'actions/checkout@{CHECKOUT}' (SHA:{CHECKOUT})
2026-10-23T14:50:28Z Download action repository 'astral-sh/setup-uv@{UV}' (SHA:{UV})
2026-10-23T14:50:28Z Download action repository 'someone/composite@v2' (SHA:{"c" * 40})
2026-10-23T14:50:29Z Download action repository 'someone/nested@v1' (SHA:{NESTED})
2026-10-23T14:50:30Z Complete job name: artifacts / build
2026-10-23T14:50:30Z ##[group]Run actions/checkout@{CHECKOUT}
"""


def _record(**changes: object) -> dict[str, object]:
    record: dict[str, object] = {
        "version": bi.RECORD_VERSION,
        "runner_name": "GitHub Actions 1000131001",
        "image_os": "ubuntu24",
        "image_version": "20261012.334.1",
        "artifacts": ["personalclaw-0.2.6-py3-none-any.whl", "personalclaw-0.2.6.tar.gz"],
        "images": [],
        "toolchain": {"python": "3.12.15", "uv": "uv 0.13.1", "node": "v24.22.0", "npm": "11.6.2"},
        "bases": [],
        "built_from_source": [],
    }
    record.update(changes)
    return record


JOBS = [
    {"id": 7, "name": "artifacts / build", "runner_name": "GitHub Actions 1000131001"},
    {"id": 8, "name": "artifacts / manifest", "runner_name": "GitHub Actions 1000131244"},
]


def _logs(**by_id: str):
    def log(job_id: int) -> str:
        return by_id.get(str(job_id), SET_UP)

    return log


# ── the set-up log ─────────────────────────────────────────────────────────────────────────────


def test_the_set_up_log_names_the_runner_image_and_every_action_with_its_sha() -> None:
    setup = bi.parse_setup(SET_UP)
    assert (setup.image, setup.version) == ("ubuntu-24.04", "20261012.334.1")
    assert [(a.action, a.ref, a.sha) for a in setup.actions] == [
        ("actions/checkout", CHECKOUT, CHECKOUT),
        ("astral-sh/setup-uv", UV, UV),
        ("someone/composite", "v2", "c" * 40),
        ("someone/nested", "v1", NESTED),
    ]


def test_the_sha_is_the_one_the_log_shows_not_the_one_the_workflow_names() -> None:
    """A ref that resolves elsewhere than its name suggests is recorded as it resolved."""
    moved = SET_UP.replace(
        f"'astral-sh/setup-uv@{UV}' (SHA:{UV})", f"'astral-sh/setup-uv@{UV}' (SHA:{'d' * 40})"
    )
    (uv,) = [a for a in bi.parse_setup(moved).actions if a.action == "astral-sh/setup-uv"]
    assert (uv.ref, uv.sha) == (UV, "d" * 40)


def test_ansi_colour_in_the_log_is_read_through() -> None:
    coloured = SET_UP.replace("Download action", "\x1b[36;1mDownload action")
    assert len(bi.parse_setup(coloured).actions) == 4


@pytest.mark.parametrize(
    ("planted", "says"),
    [
        (SET_UP.replace(" (SHA:" + NESTED + ")", ""), "someone/nested@v1 with no SHA"),
        (SET_UP.replace("Version: 20261012.334.1\n", ""), "names no runner image version"),
        (SET_UP.replace("Image: ubuntu-24.04\n", ""), "names no runner image"),
        (
            "\n".join(line for line in SET_UP.splitlines() if "Download action" not in line),
            "downloaded no action",
        ),
        (
            SET_UP.replace(
                "Prepare all required actions\n",
                "Prepare all required actions\n2026-10-23T14:50:27.5Z Download immutable action"
                " package 'actions/checkout@v7'\n",
            ),
            "actions/checkout@v7 with no SHA",
        ),
    ],
)
def test_a_set_up_log_that_lacks_an_identity_is_refused(planted: str, says: str) -> None:
    with pytest.raises(bi.Refused, match=says):
        bi.parse_setup(planted)


def test_an_immutable_action_package_is_recorded_by_its_source_commit() -> None:
    immutable = SET_UP.replace(
        "Prepare all required actions\n",
        "Prepare all required actions\n"
        "2026-10-23T14:50:27.5Z Download immutable action package 'actions/checkout@v7'\n"
        "2026-10-23T14:50:27.5Z   Version: 7.0.1\n"
        "2026-10-23T14:50:27.5Z   Digest: sha256:" + "e" * 64 + "\n"
        f"2026-10-23T14:50:27.5Z   Source commit SHA: {CHECKOUT}\n",
    )
    actions = bi.parse_setup(immutable).actions
    assert ("actions/checkout", "v7", CHECKOUT) in [(a.action, a.ref, a.sha) for a in actions]


# ── collecting each job's build ────────────────────────────────────────────────────────────────


def test_a_build_is_its_record_and_its_own_jobs_set_up_log() -> None:
    (build,) = bi.collect([_record()], JOBS, _logs())
    assert build["job"] == "artifacts / build"
    assert build["runner"] == {
        "image": "ubuntu-24.04",
        "version": "20261012.334.1",
        "image_os": "ubuntu24",
        "image_version": "20261012.334.1",
    }
    assert {"action": "someone/nested", "ref": "v1", "sha": NESTED} in build["actions"]
    assert build["toolchain"] == {
        "python": "3.12.15",
        "uv": "uv 0.13.1",
        "node": "v24.22.0",
        "npm": "11.6.2",
    }
    assert build["artifacts"] == [
        "personalclaw-0.2.6-py3-none-any.whl",
        "personalclaw-0.2.6.tar.gz",
    ]
    assert build["bases"] == []


def test_a_runner_whose_image_and_set_up_log_disagree_keeps_both() -> None:
    # Rehearsal run 40028736768's desktop-linux job: on an ubuntu-24.04 runner, ImageVersion read
    # 20261023.334.3 while the set-up log reported 20261012.334.1. GitHub said both.
    (build,) = bi.collect([_record(image_version="20261023.334.3")], JOBS, _logs())
    assert build["runner"] == {
        "image": "ubuntu-24.04",
        "version": "20261012.334.1",
        "image_os": "ubuntu24",
        "image_version": "20261023.334.3",
    }


def test_an_image_build_names_its_image_and_its_bases_and_no_host_toolchain() -> None:
    base = {
        "image": "pkg:docker/mirror.gcr.io/library/python@3.13.16-slim",
        "digest": "sha256:" + "a" * 64,
    }
    record = _record(
        artifacts=["personalclaw-gateway-amd64.spdx.json"],
        images=["ghcr.io/personalclaw/personalclaw-gateway"],
        toolchain={},
        bases=[base],
    )
    (build,) = bi.collect([record], JOBS, _logs())
    assert build["artifacts"] == [
        "ghcr.io/personalclaw/personalclaw-gateway",
        "personalclaw-gateway-amd64.spdx.json",
    ]
    assert build["toolchain"] == {"python": None, "uv": None, "node": None, "npm": None}
    assert build["bases"] == [base]


@pytest.mark.parametrize(
    ("records", "jobs", "says"),
    [
        (
            [_record(runner_name="GitHub Actions 9")],
            JOBS,
            "no job of this run ran on GitHub Actions 9",
        ),
        ([_record()], JOBS + [JOBS[0]], "2 jobs of this run ran on GitHub Actions 1000131001"),
        ([_record(image_version=" ")], JOBS, "names no image_version"),
        ([_record(image_os="")], JOBS, "names no image_os"),
        ([_record(version=99)], JOBS, "not a version 1 build record"),
        ([_record(), _record()], JOBS, "recorded twice"),
        ([], JOBS, "no build record"),
    ],
)
def test_a_record_its_job_does_not_bear_out_is_refused(
    records: list[dict], jobs: list[dict], says: str
) -> None:
    with pytest.raises(bi.Refused, match=says):
        bi.collect(records, jobs, _logs())


# ── the record a job writes ────────────────────────────────────────────────────────────────────


def test_a_job_records_its_runner_toolchain_artifacts_and_bases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "dist").mkdir()
    for name in ("personalclaw-0.2.6-py3-none-any.whl", "personalclaw-0.2.6.tar.gz"):
        (tmp_path / "dist" / name).write_bytes(b"")
    bases = tmp_path / "personalclaw-gateway-amd64.bases.json"
    base = {"image": "pkg:docker/python@3.13", "digest": "sha256:" + "a" * 64}
    bases.write_text(json.dumps([base]))
    containers = tmp_path / "containers.txt"
    containers.write_text("moby/buildkit@sha256:" + "b" * 64 + "\n<none>@<none>\nlocal/x@<none>\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUNNER_NAME", "GitHub Actions 1000131001")
    monkeypatch.setenv("ImageOS", "ubuntu24")
    monkeypatch.setenv("ImageVersion", "20261012.334.1")
    argv = ["record", "--out", "out", "--name", "build", "--artifact", "dist/*"]
    argv += ["--tool", "python=3.12.15", "--tool", "uv=uv 0.13.1"]
    argv += ["--tool", "node=v24.22.0", "--tool", "npm=11.6.2"]
    argv += ["--bases", str(bases), "--containers", str(containers)]
    assert bi.main(argv) == 0
    record = json.loads((tmp_path / "out" / "build.json").read_text())
    assert record == _record(
        bases=[base, {"image": "moby/buildkit", "digest": "sha256:" + "b" * 64}]
    )


@pytest.mark.parametrize(
    ("change", "says"),
    [
        (lambda argv, env: env.pop("ImageVersion"), "no ImageVersion"),
        (lambda argv, env: env.pop("RUNNER_NAME"), "no RUNNER_NAME"),
        (lambda argv, env: argv.remove("npm=11.6.2"), "names no npm version"),
        (
            lambda argv, env: argv.__setitem__(argv.index("npm=11.6.2"), "npm="),
            "names no npm version",
        ),
        (
            lambda argv, env: argv.__setitem__(argv.index("dist/*"), "nothing/*"),
            "nothing/\\* matches no file",
        ),
    ],
)
def test_a_record_that_would_lack_an_identity_is_not_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    change,
    says: str,
) -> None:
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "personalclaw-0.2.6.tar.gz").write_bytes(b"")
    monkeypatch.chdir(tmp_path)
    env = {"RUNNER_NAME": "r", "ImageOS": "ubuntu24", "ImageVersion": "1"}
    argv = ["record", "--out", "out", "--name", "build", "--artifact", "dist/*"]
    argv += ["--tool", "python=3.12.15", "--tool", "uv=uv 0.13.1", "--tool", "node=v24.22.0"]
    argv += ["--tool", "npm=11.6.2"]
    change(argv, env)
    if argv[-1] == "--tool":
        argv.pop()
    for key in ("RUNNER_NAME", "ImageOS", "ImageVersion"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert bi.main(argv) == 2
    assert not (tmp_path / "out" / "build.json").exists()
    assert says.replace("\\", "") in capsys.readouterr().err


def test_collect_reads_the_runs_jobs_and_logs_through_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = tmp_path / "records"
    records.mkdir()
    (records / "build.json").write_text(json.dumps(_record()))
    paths: list[str] = []

    def get(path: str) -> object:
        paths.append(path)
        return {"total_count": len(JOBS), "jobs": JOBS}

    monkeypatch.setattr(bi, "github_get", lambda token: get)
    monkeypatch.setattr(bi, "github_log", lambda token, repository: _logs())
    monkeypatch.setenv("RUNS_TOKEN", "the-jobs-token")
    out = tmp_path / "builds.json"
    argv = ["collect", "--records", str(records), "--repository", "owner/project"]
    assert bi.main([*argv, "--run-id", "42", "--out", str(out)]) == 0
    assert paths == ["/repos/owner/project/actions/runs/42/jobs?filter=latest&per_page=100"]
    assert [b["job"] for b in json.loads(out.read_text())] == ["artifacts / build"]
    monkeypatch.delenv("RUNS_TOKEN")
    assert bi.main([*argv, "--run-id", "42", "--out", str(out)]) == 2


# ── a run with a rerun ─────────────────────────────────────────────────────────────────────────

#: Release rehearsal run 42367171391 as the jobs API listed it (``run_attempt``, ``id``, name,
#: runner, its start on 2026-11-07; the image legs' names shortened): attempt 1, whose
#: desktop-mac (x64) failed, and attempt 2, its "Re-run failed jobs". Attempt 2 lists every job,
#: the ones it carried over with a new id but the runner and start they ran with: build is
#: 128387625871 and 128395592435, both on 1000174440.
RERUN = [
    (1, 128387562210, "version", "GitHub Actions 1000174436", "11:12:08"),
    (1, 128387625791, "artifacts / desktop-mac (arm64)", "GitHub Actions 1000174438", "11:12:25"),
    (1, 128387625837, "artifacts / desktop-mac (x64)", "GitHub Actions 1000174439", "11:12:26"),
    (1, 128387625854, "artifacts / desktop-linux", "GitHub Actions 1000174441", "11:12:25"),
    (1, 128387625871, "artifacts / build", "GitHub Actions 1000174440", "11:12:25"),
    (1, 128388173537, "artifacts / images (web)", "GitHub Actions 1000174487", "11:15:12"),
    (1, 128388173568, "artifacts / images (gateway)", "GitHub Actions 1000174490", "11:15:27"),
    (1, 128394115859, "artifacts / gateway-dashboard", "GitHub Actions 1000174577", "11:44:31"),
    (1, 128394116814, "artifacts / manifest", None, "11:43:28"),
    (2, 128395568957, "artifacts / desktop-mac (x64)", "GitHub Actions 1000174593", "11:50:32"),
    (2, 128395569570, "artifacts / desktop-linux", "GitHub Actions 1000174441", "11:12:25"),
    (2, 128395570023, "version", "GitHub Actions 1000174436", "11:12:08"),
    (2, 128395570368, "artifacts / desktop-mac (arm64)", "GitHub Actions 1000174438", "11:12:25"),
    (2, 128395570414, "artifacts / images (gateway)", "GitHub Actions 1000174490", "11:15:27"),
    (2, 128395570433, "artifacts / images (web)", "GitHub Actions 1000174487", "11:15:12"),
    (2, 128395570530, "artifacts / gateway-dashboard", "GitHub Actions 1000174577", "11:44:31"),
    (2, 128395592435, "artifacts / build", "GitHub Actions 1000174440", "11:12:25"),
    (2, 128398985492, "artifacts / manifest", "GitHub Actions 1000174637", "12:06:48"),
]
#: The runner each of that run's six build records named: the x64 one is its rerun's.
RERUN_BUILDERS = [
    "1000174440",
    "1000174438",
    "1000174593",
    "1000174441",
    "1000174487",
    "1000174490",
]


def _jobs(rows: list[tuple]) -> list[dict[str, object]]:
    return [
        {
            "run_attempt": attempt,
            "id": job_id,
            "name": name,
            "runner_name": runner,
            "started_at": at,
        }
        for attempt, job_id, name, runner, at in rows
    ]


def _api(rows: list[tuple]):
    """The jobs API as it answers for these rows: ``filter=all`` every attempt's, and
    ``filter=latest`` the newest attempt's alone."""
    newest = max(row[0] for row in rows)

    def get(path: str) -> object:
        query = path.partition("?")[2]
        if "filter=all" in query:
            listed = _jobs(rows)
        elif "filter=latest" in query:
            listed = _jobs([row for row in rows if row[0] == newest])
        else:
            raise AssertionError(f"the jobs API was asked {path}")
        return {"total_count": len(listed), "jobs": listed}

    return get


def _collect(rows: list[tuple], runners: list[str]) -> tuple[list[dict], list[int]]:
    read: list[int] = []

    def log(job_id: int) -> str:
        read.append(job_id)
        return SET_UP

    records = [_record(runner_name=f"GitHub Actions {r}") for r in runners]
    return bi.collect(records, bi.run_jobs(_api(rows), "owner/project", 42), log), read


def test_a_rerun_of_failed_jobs_reads_each_build_once_as_its_newest_attempt_lists_it() -> None:
    """A release run whose failed job was rerun keeps its manifest: each builder is read once, the
    carried-over ones by their newest attempt's id, whose log is the one they ran with."""
    with pytest.raises(bi.Refused, match="no job of this run ran on GitHub Actions 1000174439"):
        _collect(RERUN, ["1000174439"])  # the failed x64 job's runner, which its rerun replaced
    builds, read = _collect(RERUN, RERUN_BUILDERS)
    assert [b["job"] for b in builds] == [
        "artifacts / build",
        "artifacts / desktop-linux",
        "artifacts / desktop-mac (arm64)",
        "artifacts / desktop-mac (x64)",
        "artifacts / images (gateway)",
        "artifacts / images (web)",
    ]
    assert sorted(read) == [
        128395568957,
        128395569570,
        128395570368,
        128395570414,
        128395570433,
        128395592435,
    ], "the newest attempt's ids, the rerun x64 job's own among them"


def test_a_full_rerun_reads_only_the_newest_attempt() -> None:
    """Every job of a full re-run runs again, on new runners: a record of the attempt before it
    names a runner the newest attempt never used, and is refused."""
    first = [(1, 10 + n, f"job {n}", f"GitHub Actions {100 + n}", "10:00") for n in range(3)]
    again = [(2, 20 + n, f"job {n}", f"GitHub Actions {200 + n}", "11:00") for n in range(3)]
    builds, read = _collect(first + again, ["200", "201", "202"])
    assert [b["job"] for b in builds] == ["job 0", "job 1", "job 2"]
    assert read == [20, 21, 22]
    with pytest.raises(bi.Refused, match="no job of this run ran on GitHub Actions 100"):
        _collect(first + again, ["100"])


def test_two_jobs_on_one_runner_in_one_attempt_are_still_refused() -> None:
    rows = [
        (2, 30, "artifacts / build", "GitHub Actions 300", "11:00"),
        (2, 31, "artifacts / desktop-linux", "GitHub Actions 300", "11:05"),
    ]
    with pytest.raises(bi.Refused, match="2 jobs of this run ran on GitHub Actions 300"):
        _collect(rows, ["300"])


def test_a_run_with_more_jobs_than_one_page_is_refused() -> None:
    """The API's page holds 100 jobs: a run it says has more is refused, not read in part."""
    jobs = _jobs([(1, n, f"job {n}", f"GitHub Actions {n}", "10:00") for n in range(100)])
    with pytest.raises(bi.Refused, match="cannot read every job of run 42"):
        bi.run_jobs(lambda path: {"total_count": 101, "jobs": jobs}, "owner/project", 42)


def test_a_run_with_no_rerun_reads_as_it_always_did() -> None:
    """One attempt: its jobs are the newest attempt's, read as before."""
    once = [row for row in RERUN if row[0] == 1 and row[1] != 128387625837]
    runners = [r for r in RERUN_BUILDERS if r != "1000174593"]
    builds, read = _collect(once, runners)
    records = [_record(runner_name=f"GitHub Actions {r}") for r in runners]
    assert builds == bi.collect(records, _jobs(once), _logs())
    assert sorted(read) == [128387625791, 128387625854, 128387625871, 128388173537, 128388173568]


# ── a library built from source ────────────────────────────────────────────────────────────────

FROM_SOURCE = {
    "package": "cryptography",
    "version": "50.0.0",
    "sdist": "sha256:" + "c" * 64,
    "wheel": "cryptography-50.0.0-cp311-abi3-macosx_10_9_x86_64.whl",
    "wheel_sha256": "sha256:" + "d" * 64,
    "rust": "rustc 1.98.0 (abc 2026-09-01)",
    "openssl": "OpenSSL 3.6.3 9 Jun 2026",
    "installed_openssl": "OpenSSL 3.6.3 9 Jun 2026",
}


def _record_with_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: object
) -> dict[str, object]:
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "PersonalClaw-0.2.6-x64.dmg").write_bytes(b"")
    (tmp_path / "from-source.json").write_text(json.dumps(source))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUNNER_NAME", "GitHub Actions 1000131001")
    monkeypatch.setenv("ImageOS", "macos26")
    monkeypatch.setenv("ImageVersion", "20261013.0424.1")
    argv = ["record", "--out", "out", "--name", "desktop-mac-x64", "--artifact", "dist/*"]
    argv += ["--tool", "python=3.12.15", "--tool", "uv=uv 0.13.1"]
    argv += ["--tool", "node=v24.22.0", "--tool", "npm=11.6.2"]
    argv += ["--built-from-source", "from-source.json"]
    assert bi.main(argv) == 0
    return json.loads((tmp_path / "out" / "desktop-mac-x64.json").read_text())


def test_a_library_the_build_compiled_from_its_sdist_is_recorded_and_collected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _record_with_source(tmp_path, monkeypatch, FROM_SOURCE)
    assert record["built_from_source"] == [FROM_SOURCE]
    (build,) = bi.collect([record], JOBS, _logs())
    assert build["built_from_source"] == [FROM_SOURCE]


@pytest.mark.parametrize(
    ("source", "says"),
    [
        ({k: v for k, v in FROM_SOURCE.items() if k != "rust"}, "names no rust"),
        ({**FROM_SOURCE, "wheel_sha256": "d" * 64}, "names no wheel_sha256 digest"),
        ({**FROM_SOURCE, "installed_openssl": "OpenSSL 4.0.1"}, "not the 'OpenSSL 3.6.3"),
        ([FROM_SOURCE], "is not one library built from source"),
    ],
)
def test_a_library_built_from_source_that_names_too_little_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, source: object, says: str
) -> None:
    with pytest.raises(AssertionError):
        _record_with_source(tmp_path, monkeypatch, source)
    assert says in capsys.readouterr().err
