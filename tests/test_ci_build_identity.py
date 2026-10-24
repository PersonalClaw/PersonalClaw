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
    assert paths == ["/repos/owner/project/actions/runs/42/jobs?filter=all&per_page=100"]
    assert [b["job"] for b in json.loads(out.read_text())] == ["artifacts / build"]
    monkeypatch.delenv("RUNS_TOKEN")
    assert bi.main([*argv, "--run-id", "42", "--out", str(out)]) == 2
