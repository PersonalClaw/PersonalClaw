"""Alert when browse-live's environment quarantine recurs across distinct head SHAs.

The CI workflow uploads ``junit-browse-live`` for each measured run. This observer reads
recent CI runs, keeps only PR heads targeting the requested base branch, collapses retries
to the newest non-cancelled run per head SHA, and classifies the JUnit ZIP by the exact
``ENVIRONMENT, NOT POLICY`` marker. It never extracts or executes artifact content.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

MARKER = b"ENVIRONMENT, NOT POLICY"
ARTIFACT_NAME = "junit-browse-live"
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_JUNIT_BYTES = 8 * 1024 * 1024


class ObservationError(RuntimeError):
    """The cross-run signal could not be measured safely."""


class _CrossOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not forward GitHub's bearer token to a signed artifact-storage URL."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        fp: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(
            request,
            fp,
            code,
            message,
            headers,
            new_url,
        )
        if redirected is None:
            return None
        old_origin = urllib.parse.urlsplit(request.full_url)[:2]
        new_origin = urllib.parse.urlsplit(new_url)[:2]
        if old_origin != new_origin:
            redirected.remove_header("Authorization")
            redirected.remove_header("X-GitHub-Api-Version")
        return redirected


@dataclass(frozen=True)
class Observation:
    head_sha: str
    run_id: int
    quarantined: bool


class GitHubApi:
    def __init__(self, repository: str, token: str, api_root: str) -> None:
        self.repository = repository
        self.token = token
        self.api_root = api_root.rstrip("/")
        self.opener = urllib.request.build_opener(_CrossOriginRedirectHandler())

    def _read(self, url: str, limit: int) -> bytes:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                payload = response.read(limit + 1)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ObservationError(f"GitHub API request failed for {url}: {exc}") from exc
        if len(payload) > limit:
            raise ObservationError(f"GitHub API response exceeded {limit} bytes: {url}")
        return payload

    def json(self, path: str) -> Any:
        url = f"{self.api_root}/{path.lstrip('/')}"
        try:
            return json.loads(self._read(url, MAX_ARCHIVE_BYTES))
        except json.JSONDecodeError as exc:
            raise ObservationError(f"GitHub API returned invalid JSON for {url}") from exc

    def bytes(self, url: str) -> bytes:
        return self._read(url, MAX_ARCHIVE_BYTES)


def _run_key(run: dict[str, Any]) -> tuple[str, int]:
    timestamp = str(run.get("updated_at") or run.get("created_at") or "")
    return timestamp, int(run["id"])


def grouped_runs(runs: Iterable[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Newest SHA groups first, with newest retries first inside each group."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        head_sha = str(run.get("head_sha") or "")
        if head_sha:
            grouped[head_sha].append(run)
    for retries in grouped.values():
        retries.sort(key=_run_key, reverse=True)
    return sorted(
        grouped.items(),
        key=lambda item: max(_run_key(run) for run in item[1]),
        reverse=True,
    )


def junit_has_quarantine_marker(archive: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            reports = [
                entry
                for entry in zipped.infolist()
                if not entry.is_dir() and entry.filename.endswith(".xml")
            ]
            if not reports:
                raise ObservationError("junit-browse-live contained no XML report")
            if sum(entry.file_size for entry in reports) > MAX_JUNIT_BYTES:
                raise ObservationError(
                    f"junit-browse-live XML exceeded {MAX_JUNIT_BYTES} uncompressed bytes"
                )
            return any(MARKER in zipped.read(entry) for entry in reports)
    except zipfile.BadZipFile as exc:
        raise ObservationError("junit-browse-live was not a valid ZIP archive") from exc


def _targets_base(api: GitHubApi, repository: str, head_sha: str, base: str) -> bool:
    pulls = api.json(f"repos/{repository}/commits/{head_sha}/pulls")
    return any((pull.get("base") or {}).get("ref") == base for pull in pulls)


def _classify_run(api: GitHubApi, repository: str, run_id: int) -> bool | None:
    payload = api.json(f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = [
        artifact
        for artifact in payload.get("artifacts", [])
        if artifact.get("name") == ARTIFACT_NAME and not artifact.get("expired")
    ]
    if not artifacts:
        return None
    artifact = max(artifacts, key=lambda item: str(item.get("created_at") or ""))
    return junit_has_quarantine_marker(api.bytes(str(artifact["archive_download_url"])))


def collect_observations(
    api: GitHubApi,
    repository: str,
    workflow: str,
    base: str,
    window: int,
) -> list[Observation]:
    encoded_workflow = urllib.parse.quote(workflow, safe="")
    payload = api.json(
        f"repos/{repository}/actions/workflows/{encoded_workflow}/runs"
        "?status=completed&per_page=100"
    )
    observations: list[Observation] = []
    for head_sha, retries in grouped_runs(payload.get("workflow_runs", [])):
        if len(observations) >= window:
            break
        if not _targets_base(api, repository, head_sha, base):
            continue
        candidates = [
            run for run in retries if run.get("conclusion") not in (None, "cancelled", "skipped")
        ]
        if not candidates:
            continue
        run = candidates[0]
        run_id = int(run["id"])
        quarantined = _classify_run(api, repository, run_id)
        if quarantined is None:
            continue
        observations.append(Observation(head_sha, run_id, quarantined))
    return observations


def threshold_reached(observations: list[Observation], window: int, threshold: int) -> bool:
    recent = observations[:window]
    return len(recent) == window and sum(item.quarantined for item in recent) >= threshold


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", default="ci.yml")
    parser.add_argument("--base", default="main")
    parser.add_argument("--window", required=True, type=int)
    parser.add_argument("--threshold", required=True, type=int)
    args = parser.parse_args(argv[1:])
    if args.window <= 0 or args.threshold <= 0 or args.threshold > args.window:
        parser.error("--threshold and --window must satisfy 0 < threshold <= window")

    token = os.environ.get("GH_TOKEN", "")
    if not token:
        print("GH_TOKEN is required", file=sys.stderr)
        return 2
    api = GitHubApi(
        args.repository,
        token,
        os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )
    try:
        observations = collect_observations(
            api,
            args.repository,
            args.workflow,
            args.base,
            args.window,
        )
    except ObservationError as exc:
        print(f"browse-live frequency measurement failed: {exc}", file=sys.stderr)
        return 2

    print(f"browse-live quarantine frequency for PR heads targeting {args.base}:")
    for observation in observations:
        outcome = "QUARANTINE" if observation.quarantined else "clear"
        print(f"  {observation.head_sha[:12]}  run {observation.run_id}  {outcome}")
    if len(observations) < args.window:
        print(
            f"insufficient measured heads: {len(observations)}/{args.window}; "
            "no rate verdict yet"
        )
        return 0

    count = sum(item.quarantined for item in observations[: args.window])
    print(
        f"rate: {count}/{args.window} distinct head SHAs carried {MARKER.decode()!r}; "
        f"alert threshold is {args.threshold}/{args.window}"
    )
    if threshold_reached(observations, args.window, args.threshold):
        print(
            "::error title=browse-live environment quarantine is recurring::"
            f"{count} of the last {args.window} distinct head SHAs targeting {args.base} "
            f"carried {MARKER.decode()!r} (threshold {args.threshold}/{args.window})"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
