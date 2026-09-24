"""The post-CI observer counts quarantine markers once per head SHA."""

from __future__ import annotations

import io
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from tools import browse_live_quarantine_frequency as frequency

_REPO = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO / ".github" / "workflows" / "browse-live-quarantine-frequency.yml"


def _junit(message: str) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zipped:
        zipped.writestr(
            "junit-browse-live.xml",
            f"<testsuite><testcase><failure>{message}</failure></testcase></testsuite>",
        )
    return payload.getvalue()


class _Api:
    def __init__(
        self, runs: list[dict[str, Any]], bases: dict[str, str], reports: dict[int, bytes]
    ):
        self.runs = runs
        self.bases = bases
        self.reports = reports

    def json(self, path: str) -> Any:
        if "/actions/workflows/" in path:
            return {"workflow_runs": self.runs}
        if "/commits/" in path:
            sha = path.split("/commits/", 1)[1].split("/", 1)[0]
            return [{"base": {"ref": self.bases[sha]}}]
        if "/artifacts?" in path:
            run_id = int(path.split("/actions/runs/", 1)[1].split("/", 1)[0])
            if run_id not in self.reports:
                return {"artifacts": []}
            return {
                "artifacts": [
                    {
                        "name": frequency.ARTIFACT_NAME,
                        "expired": False,
                        "created_at": "2026-09-20T10:00:00Z",
                        "archive_download_url": f"https://artifacts.invalid/{run_id}",
                    }
                ]
            }
        raise AssertionError(f"unexpected API path: {path}")

    def bytes(self, url: str) -> bytes:
        return self.reports[int(url.rsplit("/", 1)[1])]


def _run(run_id: int, sha: str, updated: str, conclusion: str = "success") -> dict[str, Any]:
    return {
        "id": run_id,
        "head_sha": sha,
        "updated_at": updated,
        "conclusion": conclusion,
    }


def test_marker_classification_reads_junit_inside_the_zip() -> None:
    assert frequency.junit_has_quarantine_marker(_junit("ENVIRONMENT, NOT POLICY — unattached"))
    assert not frequency.junit_has_quarantine_marker(_junit("ordinary assertion failure"))


def test_artifact_redirect_does_not_forward_the_github_token_cross_origin() -> None:
    handler = frequency._CrossOriginRedirectHandler()
    request = urllib.request.Request(
        "https://api.github.com/repos/example/project/actions/artifacts/1/zip",
        headers={
            "Authorization": "Bearer secret",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://artifacts.example.invalid/signed.zip?sig=opaque",
    )

    assert redirected is not None
    assert redirected.get_header("Authorization") is None
    assert redirected.get_header("X-GitHub-Api-Version") is None


def test_retries_are_collapsed_to_the_newest_measured_run_per_sha() -> None:
    runs = [
        _run(12, "a" * 40, "2026-09-20T10:02:00Z", "cancelled"),
        _run(11, "a" * 40, "2026-09-20T10:01:00Z"),
        _run(10, "a" * 40, "2026-09-20T10:00:00Z", "failure"),
        _run(20, "b" * 40, "2026-09-20T09:00:00Z", "failure"),
        _run(30, "c" * 40, "2026-09-20T08:00:00Z", "failure"),
        _run(40, "d" * 40, "2026-09-20T07:00:00Z"),
        _run(50, "e" * 40, "2026-09-20T06:00:00Z", "failure"),
        _run(60, "f" * 40, "2026-09-20T05:00:00Z", "failure"),
    ]
    reports = {
        10: _junit("ENVIRONMENT, NOT POLICY — stale failed retry"),
        11: _junit("passed on the newest retry"),
        20: _junit("ENVIRONMENT, NOT POLICY — unattached"),
        30: _junit("ENVIRONMENT, NOT POLICY — other base"),
        40: _junit("passed"),
        50: _junit("ENVIRONMENT, NOT POLICY — unattached"),
        60: _junit("ENVIRONMENT, NOT POLICY — unattached"),
    }
    bases = {sha * 40: "main" for sha in "abdef"}
    bases["c" * 40] = "release"

    observations = frequency.collect_observations(
        _Api(runs, bases, reports),
        "PersonalClaw/PersonalClaw",
        "ci.yml",
        "main",
        5,
    )

    assert [item.run_id for item in observations] == [11, 20, 40, 50, 60]
    assert [item.quarantined for item in observations] == [False, True, False, True, True]
    assert frequency.threshold_reached(observations, window=5, threshold=3)


def test_threshold_needs_a_full_window_and_three_distinct_quarantined_heads() -> None:
    clear = frequency.Observation("a" * 40, 1, False)
    quarantined = [
        frequency.Observation(char * 40, index, True) for index, char in enumerate("bcde", start=2)
    ]
    assert not frequency.threshold_reached([clear, *quarantined[:3]], window=5, threshold=3)
    assert frequency.threshold_reached([clear, *quarantined], window=5, threshold=3)
    assert not frequency.threshold_reached(
        [clear, clear, *quarantined[:2], clear], window=5, threshold=3
    )


def test_workflow_states_and_invokes_the_three_of_five_main_threshold() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert 'workflows: ["CI"]' in text
    assert "github.event.workflow_run.conclusion != 'cancelled'" in text
    assert 'BROWSE_LIVE_WINDOW: "5"' in text
    assert 'BROWSE_LIVE_THRESHOLD: "3"' in text
    assert "3 of the last 5 DISTINCT head SHAs targeting main" in text
    assert "python3 tools/browse_live_quarantine_frequency.py" in text
    assert "--base main" in text
