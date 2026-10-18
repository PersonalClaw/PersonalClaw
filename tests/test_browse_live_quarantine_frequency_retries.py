"""The quarantine-frequency observer retries a server error, never a client error.

A GitHub API 5xx, a dropped connection or a timeout is retried, three tries in all with a 2 s and
then an 8 s wait. A 4xx is an answer, so it is never retried. A read that never succeeds is "could
not measure" (exit 2, naming the read), never a finding (exit 1). Every case drives ``main`` end
to end through a scripted opener, and the waits are recorded, not slept.
"""

from __future__ import annotations

import io
import json
import urllib.error
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from tools import browse_live_quarantine_frequency as frequency

_API = "https://api.example.invalid"
_REPOSITORY = "example/project"
_SHA = "a" * 40
_RUNS = f"{_API}/repos/{_REPOSITORY}/actions/workflows/ci.yml/runs?status=completed&per_page=100"
_PULLS = f"{_API}/repos/{_REPOSITORY}/commits/{_SHA}/pulls"
_ARTIFACTS = f"{_API}/repos/{_REPOSITORY}/actions/runs/7/artifacts?per_page=100"
_ZIP = "https://artifacts.example.invalid/7.zip"


def _junit(message: str) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zipped:
        zipped.writestr(
            "junit-browse-live.xml", f"<testsuite><failure>{message}</failure></testsuite>"
        )
    return payload.getvalue()


def _answers() -> dict[str, list[Any]]:
    """One measured head on main, its report carrying the marker: a full window of one."""
    run = {
        "id": 7,
        "head_sha": _SHA,
        "updated_at": "2026-10-18T10:00:00Z",
        "conclusion": "failure",
    }
    runs = {"workflow_runs": [run]}
    artifacts = {
        "artifacts": [
            {
                "name": frequency.ARTIFACT_NAME,
                "expired": False,
                "created_at": "2026-10-18T10:00:00Z",
                "archive_download_url": _ZIP,
            }
        ]
    }
    return {
        _RUNS: [json.dumps(runs).encode()],
        _PULLS: [json.dumps([{"base": {"ref": "main"}}]).encode()],
        _ARTIFACTS: [json.dumps(artifacts).encode()],
        _ZIP: [_junit("ENVIRONMENT, NOT POLICY — unattached")],
    }


def _http(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "scripted", {}, None)  # type: ignore[arg-type]


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = io.BytesIO(body)

    def read(self, limit: int = -1) -> bytes:
        return self._body.read(limit)


class _Opener:
    """Answers each URL from its script, in order; the last answer repeats."""

    def __init__(self, script: dict[str, list[Any]]) -> None:
        self.script = script
        self.calls: list[str] = []

    @contextmanager
    def _respond(self, answer: Any) -> Iterator[_Response]:
        yield _Response(answer)

    def open(self, request: Any, timeout: float) -> Any:
        url = request.full_url
        self.calls.append(url)
        answers = self.script[url]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return self._respond(answer)


def _main(monkeypatch: pytest.MonkeyPatch, opener: _Opener, waits: list[float]) -> int:
    monkeypatch.setenv("GH_TOKEN", "scripted")
    monkeypatch.setenv("GITHUB_API_URL", _API)
    argv = ["frequency", "--repository", _REPOSITORY, "--window", "1", "--threshold", "1"]
    return frequency.main(argv, opener=opener, sleep=waits.append)


def test_a_502_then_a_200_is_measured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _answers()
    script[_RUNS].insert(0, _http(_RUNS, 502))
    opener, waits = _Opener(script), []
    # The one measured head carries the marker, so the window of one is a finding: exit 1.
    assert _main(monkeypatch, opener, waits) == 1
    assert opener.calls.count(_RUNS) == 2
    assert waits == [2]
    assert "rate: 1/1" in capsys.readouterr().out


def test_three_502s_cannot_measure_and_name_the_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _answers()
    script[_RUNS] = [_http(_RUNS, 502)]
    opener, waits = _Opener(script), []
    assert _main(monkeypatch, opener, waits) == 2
    assert opener.calls == [_RUNS] * 3
    assert waits == [2, 8]
    captured = capsys.readouterr()
    assert _RUNS in captured.err
    assert "3 tries" in captured.err
    assert "::error" not in captured.out


@pytest.mark.parametrize("code", [404, 403, 422, 429])
def test_a_client_error_is_never_retried(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], code: int
) -> None:
    script = _answers()
    script[_RUNS] = [_http(_RUNS, code)]
    opener, waits = _Opener(script), []
    assert _main(monkeypatch, opener, waits) == 2
    assert opener.calls == [_RUNS]
    assert waits == []
    assert str(code) in capsys.readouterr().err


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(urllib.error.URLError(ConnectionResetError("reset by peer")), id="reset"),
        pytest.param(TimeoutError("timed out"), id="timeout"),
        pytest.param(ConnectionResetError("reset by peer"), id="reset mid-read"),
        pytest.param(_http(_ZIP, 503), id="503"),
    ],
)
def test_a_dropped_connection_or_server_error_on_any_read_is_retried(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    script = _answers()
    script[_ZIP].insert(0, failure)
    opener, waits = _Opener(script), []
    assert _main(monkeypatch, opener, waits) == 1
    assert opener.calls.count(_ZIP) == 2
    assert waits == [2]


def test_the_default_opener_still_strips_the_token_on_a_cross_origin_redirect() -> None:
    """The opener became injectable for these tests; the one CI uses keeps the redirect handler."""
    api = frequency.GitHubApi(_REPOSITORY, "scripted", _API)
    assert any(
        isinstance(handler, frequency._CrossOriginRedirectHandler)
        for handler in api.opener.handlers
    )


def test_a_clean_run_waits_for_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    opener, waits = _Opener(_answers()), []
    assert _main(monkeypatch, opener, waits) == 1
    assert len(opener.calls) == 4
    assert waits == []
