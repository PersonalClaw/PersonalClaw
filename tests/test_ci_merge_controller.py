"""The merge verdict's poster judges a Merge run by main's rules, through the API alone.

``tools/ci/merge_controller.py`` runs against a fake API: a run, its attempts' jobs, both trees
and their blobs, the comparison with main and the run's artifacts. A red, cancelled, skipped or
missing ``results`` posts failure, never neutral; another workflow named Merge posts nothing; and
a green carries the receipt ``verify-check-run`` reads. A head that changes a definition is judged
by its own run and by main's CI contract over its definitions (#4584 step 4): its own run starts
that contract run on a branch at main's tip, and its verdict waits for it.
"""

from __future__ import annotations

import base64
import datetime
import difflib
import json
import re
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from ci_merge_controller_shared import GATE_BOUNDS, GATE_FILTERS

from tools.ci import merge_controller as mc
from tools.ci import merge_verdict, trust, verdict, workflows

REPO = "owner/project"
RUN = 77
HEAD = "a" * 40
MAIN = "b" * 40
CONTROLLER = "c" * 40
DIGEST = "sha256:" + "d" * 64
WF = trust.DEFINITION_ROOTS[0]
ACTIONS = trust.DEFINITION_ROOTS[1]
MERGE_PATH = mc.MERGE_WORKFLOW
MERGE_YML = "name: Merge\non:\n  workflow_dispatch: {}\njobs: {}\n"
#: Each gate's workflow (rule 5b) with its filter as main's stands, planted in every tree.
GATE = {g.file: g for g in workflows.GATES}


def gate_yml(
    file: str, paths: list[str] | None = None, minutes: object = None, **pull_request: Any
) -> str:
    """A gate's workflow: its name, its pull_request filter and each job's bound, nothing it
    runs."""
    spec = {"paths": GATE_FILTERS[file] if paths is None else paths, **pull_request}
    bound = GATE_BOUNDS[file] if minutes is None else minutes
    jobs = {job: {"timeout-minutes": bound} for job in GATE[file].jobs}
    return json.dumps({"name": GATE[file].name, "on": {"pull_request": spec}, "jobs": jobs}) + "\n"


GATE_FILES = {GATE[file].path: gate_yml(file) for file in GATE_FILTERS}


_PULL_NUMBER = re.compile(r"refs/pull/([0-9]+)/head")


def _after(at: str, minutes: float) -> str:
    """The time *minutes* after *at*, as the jobs API writes one."""
    start = datetime.datetime.fromisoformat(at.replace("Z", "+00:00"))
    end = start + datetime.timedelta(minutes=minutes)
    return end.isoformat(timespec="seconds").replace("+00:00", "Z")


def _oid(text: str) -> str:
    return (format(abs(hash(text)), "x") * 8)[:40]


class FakeApi:
    """GET answers by path; a path it does not know raises, as a 404 would."""

    def __init__(self) -> None:
        self.answers: dict[str, Any] = {}
        self.calls: list[str] = []
        self.blob_texts: dict[str, str] = {}
        self.run(event="pull_request")
        self.jobs(1, ("results", "completed", "success"))
        self.tree(MAIN, {MERGE_PATH: MERGE_YML, "README.md": "hi\n"})
        self.tree(HEAD, {MERGE_PATH: MERGE_YML, "README.md": "changed\n"})
        self.answers[f"/repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": MAIN}}
        self.answers[f"/repos/{REPO}/compare/{MAIN}...{HEAD}"] = {"behind_by": 0}
        self.artifacts({"ci-record-unit": DIGEST, "web-dist": "sha256:" + "e" * 64})
        self.review(HEAD, ("completed", "success"))
        self.sign_off(HEAD, ("completed", "success"))
        self.codeql(HEAD)
        for file in GATE_FILTERS:
            self.gate(file)

    def gate(self, file: str, *runs: Mapping[str, Any], head: str = HEAD) -> None:
        """The head's runs of a gate's workflow, oldest first, each under the gate's name. Each run
        may name its ``status``, ``conclusion``, ``event``, ``path``, ``head_sha`` and
        ``run_attempt``, and its ``attempts``: each attempt a list of its jobs, ``(name,
        conclusion, the step that failed or "")``, how many minutes it ran (one by default) and
        any field of its own to set (its times, say), a conclusion None for a job still going. By
        default one attempt with every one of the gate's jobs green."""
        gate = GATE[file]
        listed = []
        for n, run in enumerate(runs):
            run_id = 5000 + 100 * list(GATE_FILTERS).index(file) + n
            attempts = run.get("attempts", [[(job, "success", "") for job in gate.jobs]])
            listed.append(
                {
                    "id": run.get("id", run_id),
                    "name": gate.name,
                    "path": run.get("path", gate.path),
                    "event": run.get("event", "pull_request"),
                    "head_sha": run.get("head_sha", head),
                    "status": run.get("status", "completed"),
                    "conclusion": run.get("conclusion", "success"),
                    "run_attempt": run.get("run_attempt", len(attempts)),
                    "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
                }
            )
            for attempt, jobs in enumerate(attempts, 1):
                found = [
                    {
                        "id": run_id * 100 + i,
                        "name": name,
                        "status": "completed" if conclusion is not None else "in_progress",
                        "conclusion": conclusion,
                        "started_at": "2026-11-10T12:00:00Z",
                        "completed_at": _after("2026-11-10T12:00:00Z", (*more, 1.0)[0]),
                        "steps": (
                            [{"name": "Set up job", "conclusion": "success"}]
                            + ([{"name": step, "conclusion": "failure"}] if step else [])
                        ),
                        **(more[1] if len(more) > 1 else {}),
                    }
                    for i, (name, conclusion, step, *more) in enumerate(jobs)
                ]
                at = f"/repos/{REPO}/actions/runs/{run_id}/attempts/{attempt}/jobs"
                self.answers[f"{at}?per_page=100&page=1"] = {
                    "total_count": len(found),
                    "jobs": found,
                }
        query = f"head_sha={head}&event=pull_request&per_page=100&page=1"
        self.answers[f"/repos/{REPO}/actions/workflows/{file}/runs?{query}"] = {
            "total_count": len(listed),
            "workflow_runs": listed,
        }

    def codeql(
        self,
        head: str,
        status: str = "completed",
        jobs: Mapping[str, str | None] | None = None,
        branch: str = "refs/pull/12/head",
        pull_alerts: tuple[int, ...] = (3,),
        main_alerts: tuple[int, ...] = (1, 2, 3),
        languages: tuple[str, ...] = ("actions", "javascript-typescript", "python"),
        path: str = mc.CODEQL_WORKFLOW,
        processed: tuple[str, ...] | None = None,
    ) -> None:
        """GitHub's dynamic CodeQL run on the head, main's analyses and both refs' open alerts."""
        run = {"id": 800, "path": path, "head_sha": head, "status": status, "head_branch": branch}
        self.answers[f"/repos/{REPO}/actions/runs?head_sha={head}&per_page=100"] = {
            "total_count": 1,
            "workflow_runs": [run],
        }
        done = jobs if jobs is not None else {f"Analyze ({lang})": "success" for lang in languages}
        listed = [{"name": n, "conclusion": c} for n, c in done.items()]
        self.answers[f"/repos/{REPO}/actions/runs/800/jobs?per_page=100&page=1"] = {
            "total_count": len(listed),
            "jobs": listed,
        }
        self.answers[f"/repos/{REPO}/code-scanning/analyses?ref=refs/heads/main&per_page=100"] = [
            {"analysis_key": mc.CODEQL_ANALYSIS, "category": f"/language:{lang}"}
            for lang in languages
        ]
        alerts = "/repos/{REPO}/code-scanning/alerts?state=open&ref={ref}&per_page=100&page=1"
        number = _PULL_NUMBER.search(branch)
        if number:
            ready = languages if processed is None else processed
            self.answers[
                f"/repos/{REPO}/code-scanning/analyses?ref=refs/pull/{number[1]}/head&per_page=100"
            ] = [
                {
                    "analysis_key": mc.CODEQL_ANALYSIS,
                    "category": f"/language:{lang}",
                    "commit_sha": head,
                    "error": "",
                }
                for lang in ready
            ]
            self.answers[alerts.format(REPO=REPO, ref=f"refs/pull/{number[1]}/head")] = [
                {"number": n} for n in pull_alerts
            ]
        self.answers[alerts.format(REPO=REPO, ref="refs/heads/main")] = [
            {"number": n} for n in main_alerts
        ]

    def review(self, head: str, *runs: tuple[str, str | None], path: str = "") -> None:
        """The head's dependency-review runs, oldest first."""
        self._target_runs(mc.DEPENDENCY_REVIEW, head, runs, path=path)

    def sign_off(self, head: str, *runs: tuple[str, str | None], **fields: Any) -> None:
        """The head's DCO runs, oldest first; *fields* plant what each run says of itself."""
        self._target_runs(mc.DCO_CHECK, head, runs, **fields)

    def _target_runs(
        self, workflow: str, head: str, runs: Any, *, path: str = "", **fields: Any
    ) -> None:
        listed = [
            {
                "id": 900 + n,
                "path": path or workflow,
                "event": "pull_request_target",
                "head_sha": head,
                "status": s,
                "conclusion": c,
                **fields,
            }
            for n, (s, c) in enumerate(runs)
        ]
        query = f"head_sha={head}&event=pull_request_target&per_page=100"
        name = workflow.rsplit("/", 1)[-1]
        self.answers[f"/repos/{REPO}/actions/workflows/{name}/runs?{query}"] = {
            "total_count": len(listed),
            "workflow_runs": listed,
        }

    def run(self, **fields: Any) -> None:
        run = {
            "repository": {"full_name": REPO},
            "path": MERGE_PATH,
            "event": "pull_request",
            "status": "completed",
            "head_sha": HEAD,
            "run_attempt": 1,
            "html_url": f"https://github.com/{REPO}/actions/runs/{RUN}",
        }
        self.answers[f"/repos/{REPO}/actions/runs/{RUN}"] = {**run, **fields}

    def jobs(self, attempt: int, *jobs: tuple[str, str, str | None]) -> None:
        listed = [
            {
                "id": 100 * attempt + i,
                "name": n,
                "status": s,
                "conclusion": c,
                "labels": ["ubuntu-24.04"],
                "runner_id": 1000 + i,
                "runner_name": f"GitHub Actions {1000 + i}",
                "started_at": "2026-10-28T00:00:00Z",
                "run_id": RUN,
                "run_attempt": attempt,
                "steps": [{"name": "Set up job", "conclusion": "success"}],
            }
            for i, (n, s, c) in enumerate(jobs)
        ]
        self.listing(attempt, *listed)

    def listing(self, attempt: int, *listed: Mapping[str, Any]) -> None:
        """The attempt's jobs, as the API lists them, whole."""
        path = f"/repos/{REPO}/actions/runs/{RUN}/attempts/{attempt}/jobs?per_page=100&page=1"
        self.answers[path] = {"total_count": len(listed), "jobs": list(listed)}
        # The results job's annotations: none, unless a case plants the flakes it passed.
        for job in listed:
            if job["name"] == "results":
                self.answers.setdefault(self.notes_path(attempt), [])

    def notes_path(self, attempt: int) -> str:
        path = f"/repos/{REPO}/actions/runs/{RUN}/attempts/{attempt}/jobs?per_page=100&page=1"
        [job] = [j for j in self.answers[path]["jobs"] if j["name"] == "results"]
        return f"/repos/{REPO}/check-runs/{job['id']}/annotations?per_page=100&page=1"

    def flakes(self, attempt: int, *messages: str, title: str = "flake passed") -> None:
        """Plant the notices the results job wrote for each flake the rule passed."""
        self.answers[self.notes_path(attempt)] = [
            {"annotation_level": "notice", "title": title, "message": m} for m in messages
        ]

    def tree(
        self,
        commit: str,
        files: Mapping[str, str],
        truncated: bool = False,
        parents: tuple[str, ...] = (),
        gates: bool = True,
    ) -> None:
        """A commit's tree, with main's gate workflows (:data:`GATE_FILES`) unless *gates* is
        False or *files* names one of its own."""
        files = {**(GATE_FILES if gates else {}), **files}
        tree_oid = _oid(commit + "tree" + "".join(sorted(f + v for f, v in files.items())))
        self.answers[f"/repos/{REPO}/git/commits/{commit}"] = {
            "tree": {"sha": tree_oid},
            "parents": [{"sha": p} for p in parents],
        }
        entries = []
        for path, text in files.items():
            oid = _oid(text)
            self.blob_texts[oid] = text
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": oid})
            content = base64.b64encode(text.encode()).decode()
            self.answers[f"/repos/{REPO}/git/blobs/{oid}"] = {
                "content": content,
                "encoding": "base64",
            }
        entries.append(
            {"path": WF.split("/")[0], "mode": "040000", "type": "tree", "sha": "f" * 40}
        )
        self.answers[f"/repos/{REPO}/git/trees/{tree_oid}?recursive=1"] = {
            "sha": tree_oid,
            "tree": entries,
            "truncated": truncated,
        }

    def artifacts(self, named: Mapping[str, str]) -> None:
        items = [{"name": n, "digest": d} for n, d in named.items()]
        path = f"/repos/{REPO}/actions/runs/{RUN}/artifacts?per_page=100&page=1"
        self.answers[path] = {"total_count": len(items), "artifacts": items}

    def __call__(self, path: str) -> Any:
        self.calls.append(path)
        if path not in self.answers:
            raise OSError(f"HTTP 404 for {path}")
        return self.answers[path]


def _decide(api: FakeApi) -> mc.Posting | str:
    """The check run the run posts, or why it posts none."""
    outcome = mc.decide(api, REPO, RUN, CONTROLLER)
    return outcome.posting or outcome.why


def _red(api: FakeApi) -> mc.Posting:
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "failure", found
    assert found.output["title"].startswith("Red: ")
    assert merge_verdict.RECEIPT_FENCE not in found.output.get("text", ""), "a red has no receipt"
    return found


def api_tree_oid(commit: str, api: FakeApi | None = None) -> str:
    return (api or FakeApi()).answers[f"/repos/{REPO}/git/commits/{commit}"]["tree"]["sha"]


def test_a_green_run_posts_success_with_the_receipt_the_reader_takes() -> None:
    found = _decide(FakeApi())
    assert isinstance(found, mc.Posting) and found.conclusion == "success"
    body = mc.check_body(found.body())
    assert body["name"] == "merge-verdict" and body["head_sha"] == HEAD
    receipt = merge_verdict.read_receipt(body)
    assert receipt.controller_revision == CONTROLLER and receipt.head_sha == HEAD
    assert receipt.tree_oid == api_tree_oid(HEAD)
    assert receipt.evidence_digests == {"ci-record-unit": DIGEST}
    assert receipt.toolchain == {"runner": "ubuntu-24.04", "definitions": MAIN}


@pytest.mark.parametrize("state", ["green", "red", "waiting"])
def test_the_verdict_says_nothing_of_being_advisory(state: str) -> None:
    # main-protect requires merge-verdict since 2026-10-25 22:55Z (#3910 piece 5).
    api = FakeApi()
    if state == "red":
        api.jobs(1, ("results", "completed", "failure"))
    if state == "waiting":
        api.codeql(HEAD, pull_alerts=(), processed=("actions",))
    found = _decide(api)
    assert isinstance(found, mc.Posting)
    assert found.conclusion == ("success" if state == "green" else "failure")
    said = "\n".join(found.output.get(key, "") for key in ("title", "summary", "text")).lower()
    for stale in ("advisory", "not a required check", "#3915"):
        assert stale not in said, (stale, found.output["summary"])
    assert not hasattr(mc, "ADVISORY")


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "neutral", None])
def test_a_results_job_that_did_not_pass_posts_failure(conclusion: str | None) -> None:
    api = FakeApi()
    api.jobs(1, ("results", "completed", conclusion))
    found = _red(api)
    assert f"attempt 1's `results` is {conclusion!r}" in found.output["title"]


def test_a_missing_results_job_posts_failure() -> None:
    api = FakeApi()
    api.jobs(1, ("changes", "completed", "success"))
    assert "attempt 1 has 0 `results` jobs" in _red(api).output["title"]


def test_a_results_job_still_running_posts_failure() -> None:
    api = FakeApi()
    api.jobs(1, ("results", "in_progress", None))
    assert "is 'in_progress'" in _red(api).output["title"]


# ── The flakes the verdict passed (tools/ci/verdict.py's flake rule) ───────────────────────────

FLAKE = "tests/test_timing.py::test_it_answers_in_time (python-suite (1/3))"


def test_a_green_run_names_the_flakes_its_verdict_passed() -> None:
    api = FakeApi()
    api.flakes(1, FLAKE)
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success"
    assert found.output["title"] == (
        f"Green: every planned job passed on this head · passed 1 flake: {FLAKE}"
    )
    assert "the flake rule passed 1 failed test(s)" in found.output["summary"]
    assert f"`{FLAKE}`" in found.output["summary"]
    # The receipt is unchanged: the flakes are said, never a field a reader holds a head to.
    receipt = merge_verdict.read_receipt(mc.check_body(found.body()))
    assert receipt.evidence_digests == {"ci-record-unit": DIGEST}


def test_a_long_flake_name_is_cut_in_the_title_and_whole_in_the_summary() -> None:
    api = FakeApi()
    name = "tests/test_timing.py::test_it[" + "x" * 200 + "] (python-suite (1/3))"
    api.flakes(1, name, FLAKE)
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success"
    assert " · passed 2 flakes: " in found.output["title"]
    assert name not in found.output["title"] and name in found.output["summary"]


def test_notices_the_verdict_does_not_write_are_not_flakes() -> None:
    """run-job's own warnings name its classifications in the stage jobs; only the results job's
    `flake passed` notices are the verdict's."""
    api = FakeApi()
    api.flakes(1, FLAKE, title="flake")
    found = _decide(api)
    assert isinstance(found, mc.Posting)
    assert found.output["title"] == "Green: every planned job passed on this head"


@pytest.mark.parametrize(
    ("messages", "says"),
    [
        pytest.param((FLAKE, FLAKE + "2", FLAKE + "3"), "names 3 flakes passed", id="three"),
        pytest.param(("  ",), "with no test", id="a notice with no test"),
    ],
)
def test_a_results_job_naming_what_the_rule_never_passes_is_red(
    messages: tuple[str, ...], says: str
) -> None:
    api = FakeApi()
    api.flakes(1, *messages)
    assert says in _red(api).output["title"]


def test_unreadable_annotations_are_red() -> None:
    api = FakeApi()
    del api.answers[api.notes_path(1)]
    assert "annotations" in _red(api).output["title"]


def test_a_red_on_an_earlier_attempt_is_never_rerun_into_green() -> None:
    api = FakeApi()
    api.run(run_attempt=2)
    api.jobs(1, ("results", "completed", "failure"))
    api.jobs(2, ("results", "completed", "success"))
    assert "attempt 1's `results` is 'failure'" in _red(api).output["title"]


# ── Attempt 1 red only for jobs no runner took (rule 4's one excuse) ──────────────────────────
#
# Attempt 1's listing is the proof: every job but results passed, was skipped or was one no runner
# took. Only then is the verdict's notice read, and it must name exactly those jobs.

UNTAKEN = ("python-suite-macos (2/4)", "python-suite-macos (3/4)")


def _api_job(
    attempt: int,
    name: str,
    conclusion: str | None = "success",
    *,
    status: str = "completed",
    steps: int = 3,
    runner: str | None = "GitHub Actions 7",
    runner_id: int | None = 7,
    n: int = 0,
) -> dict[str, Any]:
    """One job of the Merge run's attempt, as the Actions API lists it."""
    return {
        "id": 100 * attempt + 50 + n,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "labels": ["ubuntu-24.04"],
        "runner_name": runner,
        "runner_id": runner_id,
        "started_at": "2026-10-28T00:00:00Z",
        "run_id": RUN,
        "run_attempt": attempt,
        "steps": [{"name": f"step {k}", "conclusion": "success"} for k in range(steps)],
    }


def _untaken_job(attempt: int, name: str, n: int) -> dict[str, Any]:
    """A job GitHub cancelled before any runner took it: no step, no runner name or id."""
    return _api_job(attempt, name, "cancelled", steps=0, runner="", runner_id=0, n=n)


def _attempt_1(api: FakeApi, *more: Mapping[str, Any], results: str | None = "failure") -> None:
    """Attempt 1's listing: results as said, two jobs green, one stage the plan left empty, the
    jobs of :data:`UNTAKEN` no runner took, and *more*."""
    api.listing(
        1,
        _api_job(1, "results", results, n=0),
        _api_job(1, "static", n=1),
        _api_job(1, "python-suite-macos (1/4)", n=2),
        _api_job(1, "matrix.key", "skipped", steps=0, runner=None, runner_id=None, n=3),
        *(_untaken_job(1, name, 4 + k) for k, name in enumerate(UNTAKEN)),
        *more,
    )
    api.answers.setdefault(api.notes_path(1), [])


def _untaken(
    api: FakeApi,
    attempt: int,
    *messages: str,
    title: str = verdict.NOT_ACQUIRED_NOTICE,
    level: str = "notice",
) -> None:
    """Add to the attempt's results annotations the notice main's verdict writes."""
    path = api.notes_path(attempt)
    api.answers[path] = [
        *api.answers.get(path, []),
        *({"annotation_level": level, "title": title, "message": m} for m in messages),
    ]


#: The notice main's verdict writes for :data:`UNTAKEN`, taken from its writer.
NOTICE = (
    verdict.not_acquired_notices(verdict.Verdict(green=False, not_acquired=UNTAKEN))
    .split("::", 2)[2]
    .rstrip("\n")
)


def _excusable(api: FakeApi, *later: str) -> None:
    """A rerun whose attempt 1 was red for :data:`UNTAKEN` alone, by its listing and its notice,
    and each later attempt's results as said (green by default)."""
    later = later or ("success",)
    api.run(run_attempt=1 + len(later))
    _attempt_1(api)
    _untaken(api, 1, NOTICE)
    for n, conclusion in enumerate(later, 2):
        api.jobs(n, ("results", "completed", conclusion))


def test_attempt_1_red_only_for_jobs_no_runner_took_is_green_on_a_green_rerun() -> None:
    api = FakeApi()
    _excusable(api)
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    assert found.output["title"] == "Green: every planned job passed on this head"
    first = found.output["summary"].splitlines()[0]
    assert first == (
        f"Judged by its own Merge run: attempt 1 of Merge run https://github.com/{REPO}/actions"
        f"/runs/{RUN} was red only for jobs no runner took (`{UNTAKEN[0]}`, `{UNTAKEN[1]}`),"
        " and every later attempt was green."
    ), first


def test_a_dispatch_s_rerun_red_only_for_jobs_no_runner_took_is_excused_too() -> None:
    """decide gives a dispatch's run rule 4's one excuse, as it gives a pull request's; only a
    queue commit's run excuses nothing."""
    api = FakeApi()
    _excusable(api)
    api.run(event="workflow_dispatch", run_attempt=2)
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    first = found.output["summary"].splitlines()[0]
    assert "was red only for jobs no runner took" in first, first


def test_a_green_with_no_excuse_still_says_every_attempt_was_green() -> None:
    api = FakeApi()
    api.run(run_attempt=2)
    api.jobs(2, ("results", "completed", "success"))
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    assert found.output["summary"].startswith(
        f"Judged by its own Merge run: every attempt of Merge run https://github.com/{REPO}"
    )


@pytest.mark.parametrize(
    "plant",
    [
        pytest.param(lambda api: None, id="no notice"),
        pytest.param(
            lambda api: _untaken(api, 1, NOTICE, title="flake passed"), id="another title"
        ),
        pytest.param(lambda api: _untaken(api, 1, NOTICE, level="warning"), id="a warning"),
        pytest.param(lambda api: _untaken(api, 2, NOTICE), id="on attempt 2"),
        pytest.param(lambda api: _untaken(api, 1, UNTAKEN[0]), id="one job short"),
        pytest.param(lambda api: _untaken(api, 1, NOTICE + ", static"), id="one job more"),
        pytest.param(
            lambda api: _untaken(api, 1, f"{UNTAKEN[0]}, static"), id="another job in its place"
        ),
        pytest.param(lambda api: _untaken(api, 1, "  "), id="no text"),
        pytest.param(lambda api: _untaken(api, 1, None), id="a message that is not text"),
    ],
)
def test_attempt_1_red_without_the_notice_of_exactly_its_untaken_jobs_stays_red(plant) -> None:
    api = FakeApi()
    api.run(run_attempt=2)
    _attempt_1(api)
    api.jobs(2, ("results", "completed", "success"))
    plant(api)
    assert "attempt 1's `results` is 'failure'" in _red(api).output["title"]


@pytest.mark.parametrize(
    "job",
    [
        pytest.param(_api_job(1, "shard-plan", "failure", n=9), id="shard-plan failed"),
        pytest.param(_api_job(1, "python-suite (1/2)", "failure", n=9), id="a leg failed"),
        pytest.param(_api_job(1, "changes", "cancelled", n=9), id="a job a runner took, cancelled"),
        pytest.param(
            _api_job(1, "changes", "failure", steps=0, runner="", runner_id=0, n=9),
            id="failed with no runner and no step",
        ),
        pytest.param(_api_job(1, "changes", None, status="in_progress", n=9), id="running"),
        pytest.param(_api_job(1, "changes", "neutral", n=9), id="neutral"),
        pytest.param(
            _api_job(1, "changes", "success", status="in_progress", n=9),
            id="success before it completed",
        ),
    ],
)
def test_a_valid_notice_beside_another_red_job_is_not_excused(job) -> None:
    api = FakeApi()
    api.run(run_attempt=2)
    _attempt_1(api, job)
    _untaken(api, 1, NOTICE)
    api.jobs(2, ("results", "completed", "success"))
    assert "attempt 1's `results` is 'failure'" in _red(api).output["title"]


def test_a_job_that_failed_with_no_runner_and_no_step_is_no_job_no_runner_took() -> None:
    """Only a job the verdict reads as never acquired counts: one that concluded failure with no
    runner and no step is a red, even when the notice names it."""
    api = FakeApi()
    api.run(run_attempt=2)
    api.listing(
        1,
        _api_job(1, "results", "failure", n=0),
        _api_job(1, "static", n=1),
        _untaken_job(1, UNTAKEN[0], 2),
        _api_job(1, UNTAKEN[1], "failure", steps=0, runner="", runner_id=0, n=3),
    )
    api.answers[api.notes_path(1)] = []
    _untaken(api, 1, NOTICE)
    api.jobs(2, ("results", "completed", "success"))
    assert "attempt 1's `results` is 'failure'" in _red(api).output["title"]


def test_only_attempt_1_s_red_for_jobs_no_runner_took_is_excused() -> None:
    api = FakeApi()
    api.run(run_attempt=3)
    api.jobs(1, ("results", "completed", "success"))
    api.listing(
        2,
        _api_job(2, "results", "failure", n=0),
        _api_job(2, "static", n=1),
        *(_untaken_job(2, name, 4 + k) for k, name in enumerate(UNTAKEN)),
    )
    api.answers[api.notes_path(2)] = []
    _untaken(api, 2, NOTICE)
    api.jobs(3, ("results", "completed", "success"))
    title = _red(api).output["title"]
    assert "attempt 2's `results` is 'failure'" in title and "unread" not in title, title


def test_a_notice_with_no_job_no_runner_took_in_the_listing_is_not_excused() -> None:
    api = FakeApi()
    api.run(run_attempt=2)
    api.listing(1, _api_job(1, "results", "failure"), _api_job(1, "static", n=1))
    api.answers[api.notes_path(1)] = []
    _untaken(api, 1, "static")
    api.jobs(2, ("results", "completed", "success"))
    assert "attempt 1's `results` is 'failure'" in _red(api).output["title"]


def test_a_rerun_after_jobs_no_runner_took_is_held_to_success() -> None:
    api = FakeApi()
    _excusable(api, "failure")
    _untaken(api, 2, NOTICE)
    title = _red(api).output["title"]
    assert "attempt 2's `results` is 'failure'" in title
    assert "attempt 1's" not in title


def test_a_red_middle_attempt_after_jobs_no_runner_took_is_red() -> None:
    api = FakeApi()
    _excusable(api, "failure", "success")
    assert "attempt 2's `results` is 'failure'" in _red(api).output["title"]


def test_attempt_1_red_for_jobs_no_runner_took_is_red_on_attempt_1() -> None:
    api = FakeApi()
    api.run(run_attempt=1)
    _attempt_1(api)
    _untaken(api, 1, NOTICE)
    assert "attempt 1's `results` is 'failure'" in _red(api).output["title"]


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped", "neutral", "timed_out", None])
def test_attempt_1_results_that_did_not_fail_is_never_excused(conclusion: str | None) -> None:
    api = FakeApi()
    _excusable(api)
    _attempt_1(api, results=conclusion)
    _untaken(api, 1, NOTICE)
    assert f"attempt 1's `results` is {conclusion!r}" in _red(api).output["title"]


def test_attempt_1_results_still_running_is_never_excused() -> None:
    api = FakeApi()
    _excusable(api)
    api.listing(
        1,
        {**_api_job(1, "results", None, status="in_progress")},
        *(_untaken_job(1, name, 4 + k) for k, name in enumerate(UNTAKEN)),
    )
    assert "attempt 1's `results` is 'in_progress'" in _red(api).output["title"]


def test_two_notices_of_jobs_no_runner_took_are_unread() -> None:
    api = FakeApi()
    _excusable(api)
    _untaken(api, 1, NOTICE)
    title = _red(api).output["title"]
    assert "unread: " in title and "writes 2 notices of jobs no runner took" in title


def test_attempt_1_s_unreadable_annotations_are_unread_once_its_listing_holds() -> None:
    api = FakeApi()
    _excusable(api)
    del api.answers[api.notes_path(1)]
    title = _red(api).output["title"]
    assert "unread: " in title and "annotations" in title


@pytest.mark.parametrize("other", ["failure", "success"], ids=["a job failed", "nothing untaken"])
def test_an_attempt_1_red_the_listing_does_not_excuse_never_reads_its_annotations(other) -> None:
    """The listing is read first: a red it does not excuse keeps today's reason and retry note,
    and never asks for the annotations."""
    api = FakeApi()
    api.run(run_attempt=2)
    api.listing(
        1,
        _api_job(1, "results", "failure", n=0),
        _api_job(1, "static", n=1),
        _api_job(1, "python-suite (1/2)", other, n=2),
    )
    api.jobs(2, ("results", "completed", "success"))
    notes = f"/repos/{REPO}/check-runs/{100 + 50}/annotations"
    asked: list[str] = []

    def watched(path: str) -> Any:
        if path.startswith(notes):
            asked.append(path)
            raise OSError("HTTP 502")
        return api(path)

    posting = mc.decide(watched, REPO, RUN, CONTROLLER).posting
    assert posting is not None and posting.conclusion == "failure", posting
    assert "attempt 1's `results` is 'failure'" in posting.output["title"]
    assert "unread" not in posting.output["title"] and asked == []


def test_a_listing_job_the_verdict_cannot_read_is_unread() -> None:
    api = FakeApi()
    _excusable(api)
    path = f"/repos/{REPO}/actions/runs/{RUN}/attempts/1/jobs?per_page=100&page=1"
    api.answers[path]["jobs"][1]["runner_id"] = "7"
    title = _red(api).output["title"]
    assert "unread: " in title and "has a runner id that is not a whole number" in title


def test_a_results_job_with_no_id_is_unread_on_an_excusable_rerun() -> None:
    """With the listing and the notice of an excusable rerun, attempt 1's ``results`` job listed
    with no id names no check run whose annotations could be read: the run is unread, so red."""
    api = FakeApi()
    _excusable(api)
    path = f"/repos/{REPO}/actions/runs/{RUN}/attempts/1/jobs?per_page=100&page=1"
    [results] = [j for j in api.answers[path]["jobs"] if j["name"] == "results"]
    results["id"] = None
    title = _red(api).output["title"]
    assert "unread: attempt 1's `results` job has no id to read its notices from" in title, title


@pytest.mark.parametrize("found", [0, 2])
def test_attempt_1_without_one_results_job_is_red_on_a_rerun(found: int) -> None:
    api = FakeApi()
    _excusable(api)
    path = f"/repos/{REPO}/actions/runs/{RUN}/attempts/1/jobs?per_page=100&page=1"
    jobs = api.answers[path]["jobs"]
    [results] = [j for j in jobs if j["name"] == "results"]
    rest = [j for j in jobs if j["name"] != "results"]
    listed = [*({**results, "id": results["id"] + 900 + k} for k in range(found)), *rest]
    api.answers[path] = {"total_count": len(listed), "jobs": listed}
    assert f"attempt 1 has {found} `results` jobs, not one" in _red(api).output["title"]


def test_a_green_rerun_after_jobs_no_runner_took_names_its_own_flakes() -> None:
    api = FakeApi()
    _excusable(api)
    api.flakes(2, FLAKE)
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    assert found.output["title"] == (
        f"Green: every planned job passed on this head · passed 1 flake: {FLAKE}"
    )


# A real attempt-1 listing's shape (a Merge run of 2026-11-12: 23 jobs, one macOS shard no runner
# took, three stages the plan left empty), read by main's verdict and by the controller alike.
_REAL = [
    ("tree-check", "success", 4), ("shard-plan", "success", 8), ("changes", "success", 12),
    ("python-suite (2/3)", "success", 18), ("python-suite-macos (2/5)", "success", 18),
    ("python-suite-macos (3/5)", "cancelled", 0), ("python-suite-macos (5/5)", "success", 18),
    ("python-suite-macos (1/5)", "success", 18), ("python-suite (3/3)", "success", 18),
    ("python-suite-macos (4/5)", "success", 18), ("matrix.key", "skipped", 0),
    ("static", "success", 18), ("rails (1/3)", "success", 18),
    ("python-suite (1/3)", "success", 18), ("matrix.key", "skipped", 0),
    ("matrix.key", "skipped", 0), ("rails (2/3)", "success", 18), ("rails (3/3)", "success", 18),
    ("rails-checks", "success", 18), ("security", "success", 18),
    ("namespace-sandbox", "success", 18), ("browse-live", "success", 19),
    ("results", "failure", 7),
]  # fmt: skip
_REAL_STAGES = ("tree-check", "changes", "shard-plan")


def _real_listing(attempt: int, *, results: str | None = "failure") -> list[dict[str, Any]]:
    out = []
    for n, (name, conclusion, steps) in enumerate(_REAL):
        if name == "results":
            out.append(_api_job(attempt, name, results, steps=steps, n=n))
        elif conclusion == "skipped":
            out.append(
                _api_job(attempt, name, conclusion, steps=0, runner=None, runner_id=None, n=n)
            )
        elif conclusion == "cancelled":
            out.append(_untaken_job(attempt, name, n))
        else:
            out.append(_api_job(attempt, name, conclusion, steps=steps, n=n))
    return out


def test_a_real_listing_read_by_main_s_verdict_and_the_controller_excuses_attempt_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.ci import __main__ as ci

    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    planned = sorted({n for n, c, _ in _REAL if n not in (*_REAL_STAGES, "matrix.key", "results")})
    left = "python-suite-macos (3/5)"
    identity = verdict.Identity(HEAD, {"apps": "c" * 40})
    records = tmp_path / "records"
    for key in planned:
        if key == left:
            continue
        text = verdict.job_record(
            key=key, result="success", identity=identity, steps=[(["true"], 0, 0.5)],
            attempt=1, run_id=RUN,
        )  # fmt: skip
        folder = records / verdict.record_artifact(key, run_id=RUN, attempt=1)
        folder.mkdir(parents=True)
        (folder / "record.json").write_text(text)
    needs = {s: {"result": "success", "outputs": {}} for s in _REAL_STAGES}
    needs.update(
        build={"result": "success", "outputs": {}}, test={"result": "abandoned", "outputs": {}}
    )
    (tmp_path / "plan.json").write_text(json.dumps(planned))
    (tmp_path / "needs.json").write_text(json.dumps(needs))
    listing = _real_listing(1, results=None)
    listing[-1] = {**listing[-1], "status": "in_progress"}
    (tmp_path / "first.json").write_text(
        json.dumps([{"total_count": len(listing), "jobs": listing}])
    )
    code = ci.main(
        [
            "verdict", "--plan", str(tmp_path / "plan.json"),
            "--needs", str(tmp_path / "needs.json"),
            "--records", str(records), "--run-id", str(RUN), "--attempt", "1",
            "--head", HEAD, "--partner", f"apps={'c' * 40}",
            "--run-head", HEAD, "--run-partner", f"apps={'c' * 40}",
            "--first-jobs", str(tmp_path / "first.json"),
        ]
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 1, out
    [line] = [
        x
        for x in out.splitlines()
        if x.startswith(f"::notice title={verdict.NOT_ACQUIRED_NOTICE}::")
    ]
    message = line.split("::", 2)[2]
    assert message == left
    # The run completes, is rerun, and its attempt 2 is green: the controller reads attempt 1's
    # listing as it then stands and the notice the verdict wrote.
    api = FakeApi()
    api.run(run_attempt=2)
    api.listing(1, *_real_listing(1))
    api.answers[api.notes_path(1)] = [
        {"annotation_level": "notice", "title": verdict.NOT_ACQUIRED_NOTICE, "message": message}
    ]
    api.jobs(2, ("results", "completed", "success"))
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    assert f"only for jobs no runner took (`{left}`)" in found.output["summary"]


CONTRACT_BRANCH = mc.CONTRACT_PREFIX + HEAD
CONTRACT_RUNS = (
    f"/repos/{REPO}/actions/workflows/contract.yml/runs?branch=contract-run%2F{HEAD}"
    "&event=workflow_dispatch&per_page=100"
)
CONTRACT_URL = f"https://github.com/{REPO}/actions/runs/4242"


def _contract(api: FakeApi, *runs: Mapping[str, Any]) -> None:
    """The head's contract runs: main's contract.yml on its branch at main's tip, unless a run
    says otherwise."""
    listed = [
        {
            "id": 4242 + n,
            "path": mc.CONTRACT_WORKFLOW,
            "event": "workflow_dispatch",
            "head_branch": CONTRACT_BRANCH,
            "head_sha": MAIN,
            "display_title": mc.CONTRACT_TITLE.format(head=HEAD),
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 1,
            "html_url": f"https://github.com/{REPO}/actions/runs/{4242 + n}",
            **r,
        }
        for n, r in enumerate(runs)
    ]
    api.answers[CONTRACT_RUNS] = {"total_count": len(listed), "workflow_runs": listed}
    for run in listed:
        # Its jobs: the contract job, in the run's state, unless the case lists them itself.
        jobs = run.get("jobs")
        if jobs is None:
            jobs = [{"name": "contract", "status": run["status"], "conclusion": run["conclusion"]}]
        path = f"/repos/{REPO}/actions/runs/{run['id']}/attempts/{run.get('run_attempt', 1)}/jobs"
        api.answers[f"{path}?per_page=100&page=1"] = {"total_count": len(jobs), "jobs": jobs}


def _changes(api: FakeApi, path: str = "tools/ci/verdict.py", text: str = "x = 1\n") -> FakeApi:
    """The head changes *path*; a gate whose filter names it ran on the head, green, as it would."""
    api.tree(HEAD, {MERGE_PATH: MERGE_YML, "README.md": "hi\n", path: text})
    for file, paths in GATE_FILTERS.items():
        if workflows.filter_matches(paths, path):
            api.gate(file, {})
    return api


@pytest.mark.parametrize(
    "path, text",
    [
        (MERGE_PATH, MERGE_YML + "# every job passes now\n"),
        ("tools/ci/verdict.py", "def decide(*a): return 'green'\n"),
        (f"{WF}merge-verdict.yml", "name: Merge verdict\n"),
    ],
)
def test_a_heads_green_run_that_changes_a_definition_starts_its_contract_and_waits_on_it(
    path: str, text: str
) -> None:
    api = _changes(FakeApi(), path, text)
    _contract(api)
    outcome = mc.decide(api, REPO, RUN, CONTROLLER)
    waiting = outcome.posting
    assert waiting is not None, "a head that waits on its contract is a waiting red"
    assert waiting.conclusion == "failure", "its own green run is not the verdict on its own"
    assert waiting.output["title"] == (
        "Red: waiting: main's CI contract over its definitions is started"
    )
    assert waiting.output["text"] == f"```{mc.WAITING_FENCE}\ncontract\n```\n"
    assert (outcome.dispatch, outcome.base, outcome.cleanup) == (HEAD, MAIN, None)


def test_a_definition_head_that_is_red_anyway_posts_red_and_still_starts_its_contract() -> None:
    api = _changes(FakeApi())
    api.jobs(1, ("results", "completed", "failure"))
    _contract(api)
    outcome = mc.decide(api, REPO, RUN, CONTROLLER)
    assert outcome.posting is not None and outcome.posting.conclusion == "failure"
    assert (outcome.dispatch, outcome.base, outcome.cleanup) == (HEAD, MAIN, None)


def test_a_definition_head_whose_contract_still_runs_waits_and_starts_none_again() -> None:
    api = _changes(FakeApi())
    _contract(api, {"status": "in_progress", "conclusion": None})
    outcome = mc.decide(api, REPO, RUN, CONTROLLER)
    assert outcome.posting is not None and outcome.dispatch is None
    assert outcome.posting.output["title"] == (
        "Red: waiting: main's CI contract over its definitions is 'in_progress': contract run "
        f"https://github.com/{REPO}/actions/runs/4242"
    )
    assert f"```{mc.WAITING_FENCE}" in outcome.posting.output["text"]


def test_a_head_waiting_on_codeql_and_its_contract_names_both_in_its_fence() -> None:
    api = _changes(FakeApi())
    api.codeql(HEAD, status="in_progress")
    _contract(api, {"status": "in_progress", "conclusion": None})
    found = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert found is not None
    assert found.output["text"] == f"```{mc.WAITING_FENCE}\ncodeql\ncontract\n```\n"


def test_a_definition_head_red_for_another_reason_waits_until_its_contract_reports() -> None:
    api = _changes(FakeApi())
    api.jobs(1, ("results", "completed", "failure"))
    _contract(api, {"status": "in_progress", "conclusion": None})
    found = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert found is not None and "waiting: main's CI contract" in found.output["summary"]
    assert found.output["title"] == "Red: attempt 1's `results` is 'failure'"
    assert found.output["text"] == f"```{mc.WAITING_FENCE}\ncontract\n```\n"


def test_a_definition_head_whose_contract_passed_is_green_and_names_it() -> None:
    api = _changes(FakeApi())
    _contract(api, {"conclusion": "failure"}, {})
    outcome = mc.decide(api, REPO, RUN, CONTROLLER)
    assert outcome.posting is not None and outcome.posting.conclusion == "success"
    assert outcome.cleanup == CONTRACT_BRANCH and outcome.dispatch is None
    receipt = merge_verdict.read_receipt(outcome.posting.body())
    assert receipt.toolchain == {
        "runner": "ubuntu-24.04",
        "definitions": HEAD,
        "contract": f"https://github.com/{REPO}/actions/runs/4243",
    }, "the newest contract run decides, and the definitions that ran are the head's"
    summary = outcome.posting.output["summary"]
    assert "the definitions that ran are its own" in summary and "main's CI contract" in summary


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", None])
def test_a_definition_head_whose_contract_did_not_pass_is_red(conclusion: str | None) -> None:
    api = _changes(FakeApi())
    _contract(api, {"conclusion": conclusion})
    outcome = mc.decide(api, REPO, RUN, CONTROLLER)
    assert outcome.posting is not None and outcome.posting.conclusion == "failure"
    assert "main's CI contract fails on its definitions" in outcome.posting.output["title"]
    assert outcome.cleanup == CONTRACT_BRANCH and outcome.dispatch is None


@pytest.mark.parametrize(
    "fields",
    [
        {"head_sha": "9" * 40},
        {"path": f"{WF}fake-contract.yml"},
        {"head_branch": mc.CONTRACT_PREFIX + "1" * 40},
        {"event": "push"},
        # Dispatched on this head's branch for another head: its job did not run.
        {"display_title": mc.CONTRACT_TITLE.format(head="1" * 40)},
        {"display_title": "Contract"},
    ],
    ids=[
        "an-older-main",
        "another-workflow",
        "another-heads-branch",
        "not-a-dispatch",
        "for-another-head",
        "named-for-no-head",
    ],
)
def test_a_contract_run_that_is_not_mains_on_this_head_counts_for_nothing(
    fields: Mapping[str, Any],
) -> None:
    api = _changes(FakeApi())
    _contract(api, fields)
    outcome = mc.decide(api, REPO, RUN, CONTROLLER)
    assert outcome.posting is not None and (outcome.dispatch, outcome.base) == (HEAD, MAIN)
    assert outcome.posting.output["title"].endswith("over its definitions is started")


def test_a_definition_heads_dispatched_run_starts_no_contract() -> None:
    api = _changes(FakeApi())
    api.run(event="workflow_dispatch")
    outcome = mc.decide(api, REPO, RUN, CONTROLLER)
    assert outcome.posting is None and outcome.dispatch is None
    assert "not its own pull_request run" in outcome.why


def test_a_head_that_changes_no_definition_is_judged_as_before_and_reads_no_contract() -> None:
    api = FakeApi()
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success"
    assert not any("contract" in c for c in api.calls), "no contract is read for it"
    receipt = merge_verdict.read_receipt(found.body())
    assert receipt.toolchain == {"runner": "ubuntu-24.04", "definitions": MAIN}


def test_a_head_behind_main_posts_failure() -> None:
    api = FakeApi()
    api.answers[f"/repos/{REPO}/compare/{MAIN}...{HEAD}"] = {"behind_by": 3}
    assert "3 commits behind main" in _red(api).output["title"]


def test_a_head_behind_main_and_failing_names_both() -> None:
    api = FakeApi()
    api.answers[f"/repos/{REPO}/compare/{MAIN}...{HEAD}"] = {"behind_by": 3}
    api.jobs(1, ("results", "completed", "failure"))
    api.review(HEAD, ("completed", "failure"))
    summary = _red(api).output["summary"]
    assert "3 commits behind main" in summary
    assert "attempt 1's `results` is 'failure'" in summary, summary
    assert "the head's dependency review is 'failure'" in summary, summary


def test_an_unread_answer_keeps_the_reasons_read_before_it() -> None:
    api = FakeApi()
    api.answers[f"/repos/{REPO}/compare/{MAIN}...{HEAD}"] = {"behind_by": 3}
    del api.answers[f"/repos/{REPO}/actions/runs/{RUN}/attempts/1/jobs?per_page=100&page=1"]
    summary = _red(api).output["summary"]
    assert "3 commits behind main" in summary and "unread: GET" in summary, summary


def test_a_run_with_no_record_artifact_has_no_evidence() -> None:
    api = FakeApi()
    api.artifacts({"web-dist": DIGEST})
    assert "no `ci-record-*` artifact" in _red(api).output["title"]


def test_a_truncated_tree_is_unread_and_posts_failure() -> None:
    api = FakeApi()
    api.tree(HEAD, {"README.md": "x\n"}, truncated=True)
    assert "unread: the tree of" in _red(api).output["title"]


def test_an_answer_the_api_will_not_give_posts_failure() -> None:
    api = FakeApi()
    del api.answers[f"/repos/{REPO}/compare/{MAIN}...{HEAD}"]
    assert "unread: GET" in _red(api).output["title"]


@pytest.mark.parametrize(
    "fields, why",
    [
        ({"path": f"{WF}fake-merge.yml"}, f"not {MERGE_PATH}"),
        ({"repository": {"full_name": "someone/fork"}}, "not owner/project"),
        ({"event": "push"}, "judges no pull request"),
        ({"event": "schedule"}, "judges no pull request"),
        ({"status": "in_progress"}, "not completed"),
    ],
)
def test_a_run_that_brings_no_verdict_posts_nothing(fields: dict[str, Any], why: str) -> None:
    api = FakeApi()
    api.run(**fields)
    found = _decide(api)
    assert isinstance(found, str) and why in found
    assert api.calls == [f"/repos/{REPO}/actions/runs/{RUN}"], "nothing else is read"


def test_a_dispatched_merge_run_is_judged() -> None:
    api = FakeApi()
    api.run(event="workflow_dispatch")
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success"


def test_the_definitions_are_read_from_the_api_as_git_would_read_them(tmp_path: Path) -> None:
    import subprocess

    files = {
        MERGE_PATH: "jobs:\n  a:\n    steps:\n      - run: make check\n",
        "Makefile": "check:\n\ttrue\n",
        "README.md": "hi\n",
    }
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text)
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "one"], check=True)
    api = FakeApi()
    api.tree(MAIN, files, gates=False)
    _, tree = mc.api_tree(api, REPO, MAIN)
    assert set(tree.blobs) == set(files)
    from_api = trust._definitions(tree, trust.REVIEWED_HELPERS)
    from_git = set(trust.definition_paths(tmp_path, "HEAD"))
    assert from_api == from_git and "Makefile" in from_api


@pytest.mark.parametrize(
    "change, says",
    [
        (lambda b: b.update(conclusion="neutral"), "not success or failure"),
        (lambda b: b.update(name="merge-verdict preview"), "not a completed merge-verdict"),
        (lambda b: b.update(status="in_progress"), "not a completed merge-verdict"),
        (lambda b: b.update(head_sha="HEAD"), "names no head"),
        (lambda b: b.update(extra=1), "not one merge-verdict check run"),
        (lambda b: b["output"].pop("text"), "no output text, so no receipt"),
    ],
)
def test_the_poster_refuses_a_body_this_module_did_not_write(change: Any, says: str) -> None:
    found = _decide(FakeApi())
    assert isinstance(found, mc.Posting)
    body = found.body()
    change(body)
    with pytest.raises((ValueError, trust.Refused), match=says):
        mc.check_body(body)


def test_the_post_sends_one_check_run_and_names_its_id() -> None:
    found = _decide(FakeApi())
    assert isinstance(found, mc.Posting)
    sent: list[tuple[str, Mapping[str, object]]] = []

    def send(path: str, body: Mapping[str, object]) -> tuple[int, object]:
        sent.append((path, body))
        return 201, {"id": 5}

    assert mc.post(REPO, found.body(), send) == 5
    assert sent == [(f"/repos/{REPO}/check-runs", found.body())]
    with pytest.raises(RuntimeError, match="not created \\(403\\)"):
        mc.post(REPO, found.body(), lambda p, b: (403, {"message": "no"}))


def test_decide_writes_the_body_and_says_whether_to_post(tmp_path: Path, monkeypatch: Any) -> None:
    out, outputs = tmp_path / "verdict.json", tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    argv = ["decide", "--repo", REPO, "--run-id", str(RUN), "--controller-revision", CONTROLLER]
    assert mc.main([*argv, "--out", str(out)], get=FakeApi()) == 0
    assert [b["conclusion"] for b in json.loads(out.read_text())] == ["success"]
    api = FakeApi()
    api.run(event="push")
    assert mc.main([*argv, "--out", str(tmp_path / "none.json")], get=api) == 0
    assert outputs.read_text().splitlines() == [
        "dispatch=",
        "base=",
        "cleanup=",
        "post=true",
        "dispatch=",
        "base=",
        "cleanup=",
        "post=false",
    ]
    assert not (tmp_path / "none.json").exists()


# ── The contract run (#4584 step 4) ───────────────────────────────────────────────────────────


def test_a_completed_contract_run_judges_its_head_again_from_its_own_merge_run() -> None:
    api = _changes(FakeApi())
    _contract(api, {})
    contract_run = 4242
    api.answers[f"/repos/{REPO}/actions/runs/{contract_run}"] = {
        "repository": {"full_name": REPO},
        "path": mc.CONTRACT_WORKFLOW,
        "event": "workflow_dispatch",
        "status": "completed",
        "head_branch": CONTRACT_BRANCH,
        "head_sha": MAIN,
    }
    _merge_runs(api, OWN_MERGE, [RUN])
    outcome = mc.judge(api, REPO, contract_run, CONTROLLER)
    assert outcome.posting is not None and outcome.posting.conclusion == "success"
    assert outcome.posting.head_sha == HEAD and outcome.cleanup == CONTRACT_BRANCH


@pytest.mark.parametrize(
    "fields, says",
    [
        ({"head_branch": mc.CONTRACT_PREFIX + "main"}, "names no head"),
        ({"head_branch": "some-branch"}, "names no head"),
        ({"status": "in_progress"}, "not a completed dispatch of the contract"),
        ({"event": "push"}, "not a completed dispatch of the contract"),
    ],
)
def test_a_contract_run_that_names_no_head_posts_nothing(fields: dict[str, Any], says: str) -> None:
    api = FakeApi()
    api.answers[f"/repos/{REPO}/actions/runs/9"] = {
        "repository": {"full_name": REPO},
        "path": mc.CONTRACT_WORKFLOW,
        "event": "workflow_dispatch",
        "status": "completed",
        "head_branch": CONTRACT_BRANCH,
        **fields,
    }
    outcome = mc.judge(api, REPO, 9, CONTROLLER)
    assert outcome.posting is None and says in outcome.why


def test_a_trusted_run_of_the_old_path_is_no_longer_judged() -> None:
    api = FakeApi()
    api.run(event="workflow_dispatch", head_branch=mc.TRUSTED_PREFIX + HEAD)
    found = _decide(api)
    assert isinstance(found, str) and "old path" in found


def _refs(api: FakeApi, prefix: str, made: Mapping[str, str]) -> None:
    """Branches under ``prefix``: each name to the date of a commit, or a commit id itself."""
    refs = []
    for name, when in made.items():
        if re.fullmatch("[0-9a-f]{40}", when):
            refs.append({"ref": f"refs/heads/{name}", "object": {"sha": when}})
            continue
        sha = _oid(name + when)
        refs.append({"ref": f"refs/heads/{name}", "object": {"sha": sha}})
        api.answers[f"/repos/{REPO}/git/commits/{sha}"] = {"committer": {"date": when}}
    api.answers[f"/repos/{REPO}/git/matching-refs/heads/{prefix}"] = refs


def test_the_sweep_takes_contract_branches_off_mains_tip_and_old_trusted_ones() -> None:
    api = FakeApi()
    kept, moved = mc.CONTRACT_PREFIX + "1" * 40, mc.CONTRACT_PREFIX + "2" * 40
    _refs(api, mc.CONTRACT_PREFIX, {kept: MAIN, moved: "9" * 40})
    old, new = mc.TRUSTED_PREFIX + "3" * 40, mc.TRUSTED_PREFIX + "4" * 40
    _refs(
        api,
        mc.TRUSTED_PREFIX,
        {
            old: "2026-10-21T10:00:00Z",
            new: "2026-10-23T09:00:00Z",
            mc.TRUSTED_PREFIX + "not-a-head": "2026-10-01T00:00:00Z",
        },
    )
    now = mc.datetime.datetime(2026, 10, 23, 12, 0, tzinfo=mc.datetime.timezone.utc)
    assert mc.stale_branches(api, REPO, now, MAIN) == [moved, old]


def test_a_judged_head_and_the_sweep_leave_no_branch_but_the_one_in_use(
    tmp_path: Path, monkeypatch: Any
) -> None:
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    argv = ["decide", "--repo", REPO, "--run-id", str(RUN), "--controller-revision", CONTROLLER]
    api = _changes(FakeApi())
    _contract(api, {})
    stale = mc.CONTRACT_PREFIX + "1" * 40
    _refs(api, mc.CONTRACT_PREFIX, {stale: "9" * 40, CONTRACT_BRANCH: MAIN})
    _refs(api, mc.TRUSTED_PREFIX, {})
    assert mc.main([*argv, "--out", str(tmp_path / "v.json")], get=api) == 0
    lines = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
    assert lines["post"] == "true" and set(lines["cleanup"].split()) == {stale, CONTRACT_BRANCH}
    outputs.unlink()
    again = _changes(FakeApi())
    _contract(again)
    _refs(again, mc.CONTRACT_PREFIX, {CONTRACT_BRANCH: "9" * 40})
    _refs(again, mc.TRUSTED_PREFIX, {})
    assert mc.main([*argv, "--out", str(tmp_path / "w.json")], get=again) == 0
    lines = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
    assert lines["dispatch"] == HEAD and lines["base"] == MAIN
    assert lines["cleanup"] == "", "the branch about to be used is kept"


class FakeWriter:
    def __init__(self, answers: dict[tuple[str, str], tuple[int, object]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, method: str, path: str, body: Any) -> tuple[int, object]:
        self.calls.append((method, path, body))
        return self.answers.get((method, path), (404, None))

    def body(self, method: str, path: str) -> Any:
        return next(b for m, p, b in self.calls if (m, p) == (method, path))


def test_the_contract_branch_is_made_at_mains_tip_with_no_commit() -> None:
    writer = FakeWriter({("POST", f"/repos/{REPO}/git/refs"): (201, {})})
    assert mc.make_contract_branch(writer, REPO, HEAD, MAIN) == CONTRACT_BRANCH
    assert writer.body("POST", f"/repos/{REPO}/git/refs") == {
        "ref": f"refs/heads/{CONTRACT_BRANCH}",
        "sha": MAIN,
    }
    assert not any("/git/commits" in p or "/git/trees" in p for _, p, _ in writer.calls)


def test_a_contract_branch_that_exists_is_moved_to_mains_tip() -> None:
    writer = FakeWriter(
        {
            ("GET", f"/repos/{REPO}/git/ref/heads/{CONTRACT_BRANCH}"): (200, {}),
            ("PATCH", f"/repos/{REPO}/git/refs/heads/{CONTRACT_BRANCH}"): (200, {}),
        }
    )
    mc.make_contract_branch(writer, REPO, HEAD, MAIN)
    assert writer.body("PATCH", f"/repos/{REPO}/git/refs/heads/{CONTRACT_BRANCH}") == {
        "sha": MAIN,
        "force": True,
    }
    with pytest.raises(ValueError, match="full commit id"):
        mc.make_contract_branch(writer, REPO, "main", MAIN)


def test_the_dispatch_runs_mains_contract_on_a_contract_branch_only() -> None:
    path = f"/repos/{REPO}/actions/workflows/contract.yml/dispatches"
    writer = FakeWriter({("POST", path): (204, None)})
    mc.dispatch(writer, REPO, CONTRACT_BRANCH)
    assert writer.calls == [("POST", path, {"ref": CONTRACT_BRANCH, "inputs": {"head": HEAD}})]
    for other in ("main", mc.TRUSTED_PREFIX + HEAD, mc.CONTRACT_PREFIX + "main"):
        with pytest.raises(ValueError):
            mc.dispatch(writer, REPO, other)


def test_cleanup_deletes_every_branch_it_is_given() -> None:
    other = mc.TRUSTED_PREFIX + "3" * 40
    paths = [f"/repos/{REPO}/git/refs/heads/{b}" for b in (CONTRACT_BRANCH, other)]
    writer = FakeWriter({("DELETE", p): (204, None) for p in paths})
    argv = ["cleanup", "--repo", REPO, "--branch", CONTRACT_BRANCH, "--branch", other]
    assert mc.main(argv, api=writer) == 0
    assert [(m, p) for m, p, _ in writer.calls] == [("DELETE", p) for p in paths]


def test_a_judged_contract_branch_is_deleted_and_one_already_gone_is_fine() -> None:
    gone = FakeWriter({})
    mc.delete_branch(gone, REPO, CONTRACT_BRANCH)
    path = f"/repos/{REPO}/git/refs/heads/{CONTRACT_BRANCH}"
    broken = FakeWriter({("DELETE", path): (500, None)})
    with pytest.raises(RuntimeError, match="answered 500"):
        mc.delete_branch(broken, REPO, CONTRACT_BRANCH)
    with pytest.raises(ValueError, match="not a contract or trusted-run branch"):
        mc.delete_branch(gone, REPO, "release")


# ── The dependency review (#4585 step 6) ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "runs, says",
    [
        ((), "has no dependency review"),
        ((("in_progress", None),), "is 'in_progress', not finished"),
        ((("completed", "failure"),), "dependency review is 'failure'"),
        ((("completed", "success"), ("completed", "failure")), "dependency review is 'failure'"),
    ],
)
def test_a_dependency_review_that_did_not_pass_is_red(runs: Any, says: str) -> None:
    api = FakeApi()
    api.review(HEAD, *runs)
    assert says in _red(api).output["summary"]


def test_the_newest_dependency_review_decides() -> None:
    api = FakeApi()
    api.review(HEAD, ("completed", "failure"), ("completed", "success"))
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success"
    assert "its dependency review passed" in found.output["summary"]


def test_a_review_from_another_workflow_counts_for_nothing() -> None:
    api = FakeApi()
    api.review(HEAD, ("completed", "success"), path=f"{WF}fake-review.yml")
    assert "has no dependency review" in _red(api).output["summary"]


# ── The sign-off check (#3908) ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "runs, says",
    [
        ((), "has no sign-off check (dco.yml)"),
        ((("in_progress", None),), "sign-off check (DCO) is 'in_progress', not finished"),
        ((("completed", "failure"),), "sign-off check (DCO) is 'failure'"),
        ((("completed", "success"), ("completed", "failure")), "sign-off check (DCO) is 'failure'"),
    ],
)
def test_a_sign_off_check_that_did_not_pass_is_red(runs: Any, says: str) -> None:
    api = FakeApi()
    api.sign_off(HEAD, *runs)
    assert says in _red(api).output["summary"]


def test_a_red_sign_off_check_names_its_fix_in_the_title() -> None:
    """The case of #5601: GitHub's Update branch wrote a merge commit with no sign-off, DCO went
    red and the verdict stayed green. The red says how to update the head instead."""
    api = FakeApi()
    api.sign_off(HEAD, ("completed", "failure"))
    title = _red(api).output["title"]
    assert title == f"Red: the head's sign-off check (DCO) is 'failure': {mc.DCO_FIX}"
    assert len(title) <= 255, "the title is cut at 255, and the fix is its tail"
    assert "Update branch" in title and "update with rebase, or rebase locally" in title


def test_the_newest_sign_off_check_decides() -> None:
    api = FakeApi()
    api.sign_off(HEAD, ("completed", "failure"), ("completed", "success"))
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success"
    assert "its sign-off check (DCO) passed" in found.output["summary"]


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"path": f"{WF}fake-dco.yml"}, id="a candidate's own workflow named DCO"),
        pytest.param(
            {"event": "pull_request"}, id="dco.yml run as the head's copy, on pull_request"
        ),
        pytest.param({"head_sha": "d" * 40}, id="a run on an older head of the same pull request"),
    ],
)
def test_a_sign_off_check_that_is_not_mains_on_this_head_counts_for_nothing(
    fields: dict[str, Any],
) -> None:
    api = FakeApi()
    api.sign_off(HEAD, ("completed", "success"), **fields)
    assert "has no sign-off check (dco.yml)" in _red(api).output["summary"]


def test_a_head_red_on_its_review_and_its_sign_off_names_both() -> None:
    api = FakeApi()
    api.review(HEAD, ("completed", "failure"))
    api.sign_off(HEAD, ("completed", "failure"))
    summary = _red(api).output["summary"]
    assert "the head's dependency review is 'failure'" in summary, summary
    assert "the head's sign-off check (DCO) is 'failure'" in summary, summary


REVIEW_RUN = 55


def _review_run(api: FakeApi, **fields: Any) -> None:
    run = {
        "id": REVIEW_RUN,
        "repository": {"full_name": REPO},
        "path": mc.DEPENDENCY_REVIEW,
        "event": "pull_request_target",
        "status": "completed",
        "head_sha": HEAD,
    }
    api.answers[f"/repos/{REPO}/actions/runs/{REVIEW_RUN}"] = {**run, **fields}


def _merge_runs(api: FakeApi, query: str, ids: list[int]) -> None:
    runs = [{"id": i, "path": MERGE_PATH} for i in ids]
    api.answers[f"/repos/{REPO}/actions/workflows/merge.yml/runs?{query}"] = {
        "total_count": len(runs),
        "workflow_runs": runs,
    }


OWN_MERGE = f"head_sha={HEAD}&event=pull_request&status=completed&per_page=100"


def test_a_completed_review_judges_its_head_from_its_own_merge_run() -> None:
    api = FakeApi()
    _review_run(api)
    _merge_runs(api, OWN_MERGE, [RUN - 1, RUN])
    outcome = mc.judge(api, REPO, REVIEW_RUN, CONTROLLER)
    assert outcome.posting is not None and outcome.posting.conclusion == "success"
    assert outcome.posting.external_id == f"{RUN}/1", "the newest own Merge run is judged"


def test_a_review_with_no_completed_merge_run_posts_nothing() -> None:
    api = FakeApi()
    _review_run(api)
    _merge_runs(api, OWN_MERGE, [])
    outcome = mc.judge(api, REPO, REVIEW_RUN, CONTROLLER)
    assert outcome.posting is None and outcome.dispatch is None
    assert "no completed Merge run of its own" in outcome.why


def test_a_review_of_a_head_that_changes_ci_reads_its_contract_run() -> None:
    api = _changes(FakeApi())
    _review_run(api)
    _merge_runs(api, OWN_MERGE, [RUN])
    _contract(api, {"status": "in_progress", "conclusion": None})
    waiting = mc.judge(api, REPO, REVIEW_RUN, CONTROLLER)
    assert waiting.posting is not None and waiting.dispatch is None
    assert "is 'in_progress'" in waiting.posting.output["title"]
    _contract(api, {})
    judged = mc.judge(api, REPO, REVIEW_RUN, CONTROLLER)
    assert judged.posting is not None and judged.posting.head_sha == HEAD
    assert judged.posting.conclusion == "success" and judged.cleanup == CONTRACT_BRANCH


def test_a_completed_sign_off_check_judges_its_head_again() -> None:
    api = FakeApi()
    _review_run(api, path=mc.DCO_CHECK)
    _merge_runs(api, OWN_MERGE, [RUN])
    api.sign_off(HEAD, ("completed", "failure"))
    red = mc.judge(api, REPO, REVIEW_RUN, CONTROLLER)
    assert red.posting is not None and red.posting.head_sha == HEAD
    assert red.posting.conclusion == "failure"
    assert "update with rebase" in red.posting.output["title"]
    api.sign_off(HEAD, ("completed", "failure"), ("completed", "success"))
    green = mc.judge(api, REPO, REVIEW_RUN, CONTROLLER)
    assert green.posting is not None and green.posting.conclusion == "success"


def test_a_sign_off_run_from_a_fork_or_on_pull_request_posts_nothing() -> None:
    api = FakeApi()
    _review_run(api, path=mc.DCO_CHECK, event="pull_request")
    assert "not a pull request's" in mc.judge(api, REPO, REVIEW_RUN, CONTROLLER).why
    _review_run(api, path=mc.DCO_CHECK, repository={"full_name": "someone/fork"})
    assert mc.judge(api, REPO, REVIEW_RUN, CONTROLLER).posting is None


def test_a_review_run_that_is_not_a_pull_requests_posts_nothing() -> None:
    api = FakeApi()
    _review_run(api, event="pull_request")
    assert (
        "not a pull request's dependency review" in mc.judge(api, REPO, REVIEW_RUN, CONTROLLER).why
    )


def test_a_head_main_has_moved_past_is_told_to_update_and_starts_no_contract() -> None:
    api = FakeApi()
    api.tree(MAIN, {MERGE_PATH: MERGE_YML + "# main moved on\n", "README.md": "hi\n"})
    api.answers[f"/repos/{REPO}/compare/{MAIN}...{HEAD}"] = {"behind_by": 4}
    outcome = mc.decide(api, REPO, RUN, CONTROLLER)
    assert outcome.dispatch is None, "a head behind main starts no contract run"
    assert not any("contract" in c for c in api.calls), "and reads none"
    assert outcome.posting is not None and outcome.posting.conclusion == "failure"
    assert outcome.posting.output["title"] == (
        "Red: the head is 4 commits behind main: update the branch"
    )


# ── CodeQL, read from what it ran (#4585 step 6) ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "plant, says",
    [
        (dict(status="in_progress"), "CodeQL's analysis of the head is 'in_progress'"),
        (
            dict(
                jobs={
                    "Analyze (actions)": "success",
                    "Analyze (javascript-typescript)": "success",
                    "Analyze (python)": "failure",
                }
            ),
            "`Analyze (python)` is 'failure'",
        ),
        (
            dict(jobs={"Analyze (actions)": "success", "Analyze (python)": "success"}),
            "`Analyze (javascript-typescript)` is None",
        ),
        (dict(pull_alerts=(3, 9)), "CodeQL finds 1 alerts this pull request brings (#9)"),
        (dict(branch="main"), "is not a pull request's"),
        (dict(path=f"{WF}codeql.yml"), "CodeQL has not analysed the head yet"),
    ],
)
def test_codeql_that_did_not_pass_on_the_head_is_red(plant: dict[str, Any], says: str) -> None:
    api = FakeApi()
    api.codeql(HEAD, **plant)
    assert says in _red(api).output["summary"]


def test_an_alert_main_already_has_is_not_the_pull_requests() -> None:
    api = FakeApi()
    api.codeql(HEAD, pull_alerts=(1, 2, 3), main_alerts=(1, 2, 3, 4))
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success"
    assert "found no alert it brings" in found.output["summary"]


# ── An alert that moved with its line is not one the pull request brings (#5610) ───────────────

_FILES = "src/personalclaw/dashboard/handlers/files.py"
_MAIN_AT = "f" * 40
_INJECTION = "py/path-injection"


def _alert(number: int, line: int, sha: str, path: str = _FILES, rule: str = _INJECTION) -> dict:
    return {
        "number": number,
        "rule": {"id": rule},
        "most_recent_instance": {
            "commit_sha": sha,
            "location": {"path": path, "start_line": line, "end_line": line},
        },
    }


def _file(api: FakeApi, path: str, sha: str, text: str) -> None:
    content = base64.b64encode(text.encode()).decode()
    quoted = urllib.parse.quote(path)
    api.answers[f"/repos/{REPO}/contents/{quoted}?ref={sha}"] = {
        "type": "file",
        "sha": "9" * 40,
        "encoding": "base64",
        "content": content,
    }
    _diffed(api, path, sha, text)


def _patch(old: str, new: str) -> str:
    """A file's patch as the compare API gives it: its unified diff's hunks, headers dropped."""
    lines = difflib.unified_diff(old.splitlines(), new.splitlines(), n=3, lineterm="")
    return "\n".join(list(lines)[2:])


def _diffed(api: FakeApi, path: str, sha: str, text: str) -> None:
    """Each main commit's compare with the head, as GitHub gives it: every file whose text both
    hold and which differs, with its patch. A test that plants a compare of its own keeps it."""
    texts = api.__dict__.setdefault("texts", {})
    texts[(path, sha)] = text
    if sha not in (HEAD, MAIN):
        # By default main's tip has not changed the file since the commit its alert names.
        api.answers.setdefault(f"/repos/{REPO}/compare/{sha}...{MAIN}", {"files": []})
    for (where, base), old in texts.items():
        new = texts.get((where, HEAD))
        if base == HEAD or new is None:
            continue
        compare = api.answers.setdefault(f"/repos/{REPO}/compare/{base}...{HEAD}", {})
        if compare.get("planted"):
            continue
        files = [f for f in compare.get("files", []) if f["filename"] != where]
        if old != new:
            files.append({"filename": where, "status": "modified", "patch": _patch(old, new)})
        compare["files"] = files


def _alerts(api: FakeApi, pull: list[dict], main: list[dict]) -> None:
    alerts = "/repos/{REPO}/code-scanning/alerts?state=open&ref={ref}&per_page=100&page=1"
    api.answers[alerts.format(REPO=REPO, ref="refs/pull/12/head")] = pull
    api.answers[alerts.format(REPO=REPO, ref="refs/heads/main")] = main


_MAIN_FILE = "def read(path):\n    return open(path).read()\n"
_HEAD_FILE = "def read(path):\n    if path:\n        return open(path).read()\n"


def _moved_api(pull: list[dict], main: list[dict], head_file: str = _HEAD_FILE) -> FakeApi:
    api = FakeApi()
    api.codeql(HEAD)
    _alerts(api, pull, main)
    _file(api, _FILES, _MAIN_AT, _MAIN_FILE)
    _file(api, _FILES, HEAD, head_file)
    return api


def test_a_reindented_alerted_line_moved_and_is_not_brought() -> None:
    api = _moved_api([_alert(421, 3, HEAD)], [_alert(106, 2, _MAIN_AT)])
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    note = "1 CodeQL alerts moved with their lines, not brought: #421 (was #106)"
    assert found.output["title"] == f"Green: every planned job passed on this head ({note})"
    assert f"- {note}" in found.output["summary"]


def test_a_green_with_a_moved_alert_and_a_flake_names_both_in_its_title() -> None:
    api = _moved_api([_alert(421, 3, HEAD)], [_alert(106, 2, _MAIN_AT)])
    api.flakes(1, FLAKE)
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    note = "1 CodeQL alerts moved with their lines, not brought: #421 (was #106)"
    assert found.output["title"] == (
        f"Green: every planned job passed on this head ({note}) · passed 1 flake: {FLAKE}"
    )
    assert f"- {note}" in found.output["summary"]
    assert f"`{FLAKE}`" in found.output["summary"]


_REPEATED = "    os.unlink(tmp_path)\n"
_THRICE = (
    "def f0(tmp_path):\n" + _REPEATED + "def f1(p):\n" + _REPEATED + "def f2(p):\n" + _REPEATED
)


@pytest.mark.parametrize(
    "head_file",
    [
        pytest.param(
            "def f0(tmp_path):\n"
            + _REPEATED
            + "def f1(p):\n"
            + _REPEATED
            + "def new(tmp_path):\n"
            + _REPEATED,
            id="an unalerted copy deleted",
        ),
        pytest.param(
            "def f0(tmp_path):\n"
            + _REPEATED
            + "def f1(p):\n"
            + _REPEATED
            + "def f2(p):\n"
            + "    os.remove(p)\n"
            + "def new(tmp_path):\n"
            + _REPEATED,
            id="an unalerted copy rewritten",
        ),
    ],
)
def test_a_new_alert_pairs_with_no_main_alert_whose_lines_the_pull_request_left(
    head_file: str,
) -> None:
    """Main has the line three times and alerts only the first (#106), which the head leaves as
    it was. The head takes away an unalerted copy, by deleting or rewriting it, and adds a new
    alerted one (#421): the text cap allows one pair, but #106 still stands, so #421 is brought."""
    api = FakeApi()
    api.codeql(HEAD)
    line = 6 if "os.remove" not in head_file else 8
    _alerts(api, [_alert(421, line, HEAD)], [_alert(106, 2, _MAIN_AT)])
    _file(api, _FILES, _MAIN_AT, _THRICE)
    _file(api, _FILES, HEAD, head_file)
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "failure", found
    assert "moved with their lines" not in found.output["title"], found.output["title"]


@pytest.mark.parametrize(
    "compare",
    [
        pytest.param(
            {"files": [{"filename": _FILES, "status": "modified"}]}, id="the file has no patch"
        ),
        pytest.param(
            {
                "files": [
                    {"filename": f"f{i}.py", "status": "added", "patch": "@@"} for i in range(300)
                ]
            },
            id="the compare lists 300 files, not this one",
        ),
    ],
)
def test_a_pair_whose_lines_the_compare_does_not_show_is_unread(compare: dict) -> None:
    api = _moved_api([_alert(421, 3, HEAD)], [_alert(106, 2, _MAIN_AT)])
    api.answers[f"/repos/{REPO}/compare/{_MAIN_AT}...{HEAD}"] = {**compare, "planted": True}
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "failure", found
    assert "unread" in found.output["summary"], found.output["summary"]


def test_a_main_alert_whose_line_mains_tip_removed_waits_for_mains_analysis() -> None:
    """#106 is open at an older main commit, but main's tip has since removed its line, and main's
    CodeQL has not analysed the tip. The head, up to date with main, adds a new alerted copy of
    that text: the compare from #106's commit to the head shows the line removed, but main took it
    away, not the pull request. So #421 pairs with nothing, and the red waits for main's analysis.
    """
    api = _moved_api([_alert(421, 3, HEAD)], [_alert(106, 2, _MAIN_AT)])
    api.answers[f"/repos/{REPO}/compare/{_MAIN_AT}...{MAIN}"] = {
        "files": [{"filename": _FILES, "status": "modified", "patch": _patch(_MAIN_FILE, "")}]
    }
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "failure", found
    assert "moved with their lines" not in found.output["title"], found.output["title"]
    assert f"{mc.WAITING}main's CodeQL has not analysed its tip's change to {_FILES}" in (
        found.output["summary"]
    )
    assert mc.WAITING_FENCE in found.output["text"]


def test_a_main_compare_too_long_to_show_the_file_is_unread() -> None:
    api = _moved_api([_alert(421, 3, HEAD)], [_alert(106, 2, _MAIN_AT)])
    files = [{"filename": f"f{i}.py", "status": "added", "patch": "@@"} for i in range(300)]
    api.answers[f"/repos/{REPO}/compare/{_MAIN_AT}...{MAIN}"] = {"files": files}
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "failure", found
    assert "unread" in found.output["summary"] and "lists 300 files" in found.output["summary"]


def test_six_moved_alerts_of_one_block_are_all_moved() -> None:
    """#5610's shape: six alerts on main, the same six lines re-indented further down."""
    main_file = "".join(f"x{n} = open(p{n})\n" for n in range(6))
    head_file = "def f():\n" + "".join(f"    x{n} = open(p{n})\n" for n in range(6))
    pull = [_alert(421 + n, 2 + n, HEAD) for n in range(6)]
    main = [_alert(106 + n, 1 + n, _MAIN_AT) for n in range(6)]
    api = FakeApi()
    api.codeql(HEAD)
    _alerts(api, pull, main)
    _file(api, _FILES, _MAIN_AT, main_file)
    _file(api, _FILES, HEAD, head_file)
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    pairs = ", ".join(f"#{421 + n} (was #{106 + n})" for n in range(6))
    assert f"6 CodeQL alerts moved with their lines, not brought: {pairs}" in found.output["title"]


def test_a_new_alert_with_nothing_unreported_is_brought() -> None:
    api = _moved_api([_alert(421, 3, HEAD), _alert(106, 2, _MAIN_AT)], [_alert(106, 2, _MAIN_AT)])
    found = _red(api)
    assert "CodeQL finds 1 alerts this pull request brings (#421)" in found.output["summary"]
    assert "moved" not in found.output["title"]


@pytest.mark.parametrize(
    ("pull", "head_file"),
    [
        pytest.param(
            [_alert(421, 3, HEAD)],
            "def read(path):\n    if path:\n        return open(path + '/x').read()\n",
            id="the-same-rule-and-path-but-other-text",
        ),
        pytest.param(
            [_alert(421, 3, HEAD, rule="py/command-line-injection")],
            _HEAD_FILE,
            id="another-rule",
        ),
        pytest.param(
            [_alert(421, 3, HEAD, path="src/personalclaw/other.py")], _HEAD_FILE, id="another-path"
        ),
        pytest.param([_alert(421, 9, HEAD)], _HEAD_FILE, id="a-line-past-the-end"),
    ],
)
def test_an_alert_that_does_not_match_an_unreported_one_is_brought(
    pull: list[dict], head_file: str
) -> None:
    api = _moved_api(pull, [_alert(106, 2, _MAIN_AT)], head_file)
    _file(api, "src/personalclaw/other.py", HEAD, _HEAD_FILE)
    found = _red(api)
    assert "CodeQL finds 1 alerts this pull request brings (#421)" in found.output["summary"]
    assert "moved" not in found.output["title"]


def test_two_copies_on_the_head_where_main_had_one_leave_one_brought() -> None:
    head_file = _HEAD_FILE + "        return open(path).read()\n"
    api = _moved_api([_alert(421, 3, HEAD), _alert(422, 4, HEAD)], [_alert(106, 2, _MAIN_AT)])
    _file(api, _FILES, HEAD, head_file)
    found = _red(api)
    assert found.output["title"] == (
        "Red: CodeQL finds 1 alerts this pull request brings (#422) "
        "(1 CodeQL alerts moved with their lines, not brought: #421 (was #106))"
    )


def test_a_copy_added_beside_an_untouched_alerted_line_is_brought() -> None:
    """The pull request's ref reports only its diff's alerts: main's alert at line 2 is not
    reported because the line is untouched, not because it moved. The copy at line 4 is new."""
    head_file = _MAIN_FILE + "def other(path):\n    return open(path).read()\n"
    api = _moved_api([_alert(421, 4, HEAD)], [_alert(106, 2, _MAIN_AT)], head_file)
    found = _red(api)
    assert "CodeQL finds 1 alerts this pull request brings (#421)" in found.output["summary"]
    assert "moved" not in found.output["title"]


def test_a_new_copy_of_a_line_main_repeats_is_brought() -> None:
    """files.py's shape: a line alerted three times on main, and a new handler that adds a
    fourth copy. Each of main's three is untouched, so none of them moved."""
    line = "    os.unlink(tmp_path)\n"
    main_file = "".join(f"def f{n}(tmp_path):\n{line}" for n in range(3))
    head_file = main_file + "def new(tmp_path):\n" + line
    api = FakeApi()
    api.codeql(HEAD)
    _alerts(api, [_alert(421, 8, HEAD)], [_alert(106 + n, 2 + 2 * n, _MAIN_AT) for n in range(3)])
    _file(api, _FILES, _MAIN_AT, main_file)
    _file(api, _FILES, HEAD, head_file)
    found = _red(api)
    assert "CodeQL finds 1 alerts this pull request brings (#421)" in found.output["summary"]


_OTHER = "def other():\n    a = 1\n    b = 2\n    c = 3\n"


def test_a_line_moved_from_one_place_to_another_moved() -> None:
    """The patch removes the alerted line where it was and adds it below another function."""
    api = _moved_api([_alert(421, 6, HEAD)], [_alert(106, 2, _MAIN_AT)], _OTHER + _MAIN_FILE)
    _file(api, _FILES, _MAIN_AT, _MAIN_FILE + _OTHER)
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    assert "#421 (was #106)" in found.output["title"]


def test_an_alerted_line_only_shifted_by_lines_added_above_it_is_brought() -> None:
    """Lines added above the alerted one leave it in the patch's context, not among the lines the
    pull request removes or rewrites: its main alert stands, so a new number there is brought."""
    head_file = "def read(path):\n    pass\n\n\n    return open(path).read()\n"
    api = _moved_api([_alert(421, 5, HEAD)], [_alert(106, 2, _MAIN_AT)], head_file)
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "failure", found
    assert "moved with their lines" not in found.output["title"]


def test_two_reindented_copies_where_main_had_two_both_moved() -> None:
    main_file = "def a(path):\n    return open(path)\ndef b(path):\n    return open(path)\n"
    head_file = (
        "class C:\n    def a(self, path):\n        return open(path)\n"
        "    def b(self, path):\n        return open(path)\n"
    )
    api = FakeApi()
    api.codeql(HEAD)
    _alerts(
        api,
        [_alert(421, 3, HEAD), _alert(422, 5, HEAD)],
        [_alert(106, 2, _MAIN_AT), _alert(107, 4, _MAIN_AT)],
    )
    _file(api, _FILES, _MAIN_AT, main_file)
    _file(api, _FILES, HEAD, head_file)
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    assert (
        "2 CodeQL alerts moved with their lines, not brought: #421 (was #106), #422 (was #107)"
        in (found.output["title"])
    )


def test_main_alerts_on_one_line_that_name_two_commits_are_unread() -> None:
    api = _moved_api([_alert(421, 3, HEAD)], [_alert(106, 2, _MAIN_AT), _alert(107, 2, "d" * 40)])
    _file(api, _FILES, "d" * 40, _MAIN_FILE)
    assert _red(api).output["title"].startswith("Red: unread: ")


@pytest.mark.parametrize("side", ["head", "main"])
def test_a_file_that_cannot_be_read_is_unread_never_a_pass(side: str) -> None:
    api = _moved_api([_alert(421, 3, HEAD)], [_alert(106, 2, _MAIN_AT)])
    sha = HEAD if side == "head" else _MAIN_AT
    del api.answers[f"/repos/{REPO}/contents/{urllib.parse.quote(_FILES)}?ref={sha}"]
    found = _red(api)
    assert found.output["title"].startswith("Red: unread: ")


def test_an_alert_whose_instance_is_not_the_head_is_unread() -> None:
    api = _moved_api([_alert(421, 3, "e" * 40)], [_alert(106, 2, _MAIN_AT)])
    found = _red(api)
    assert "is at eeee" in found.output["title"]


def test_a_large_file_is_read_through_its_blob() -> None:
    api = _moved_api([_alert(421, 3, HEAD)], [_alert(106, 2, _MAIN_AT)])
    quoted = urllib.parse.quote(_FILES)
    api.answers[f"/repos/{REPO}/contents/{quoted}?ref={HEAD}"] = {
        "type": "file",
        "sha": "8" * 40,
        "encoding": "none",
        "content": "",
    }
    api.answers[f"/repos/{REPO}/git/blobs/{'8' * 40}"] = {
        "encoding": "base64",
        "content": base64.b64encode(_HEAD_FILE.encode()).decode(),
    }
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found


def test_alerts_that_name_no_place_are_compared_by_number_as_before() -> None:
    """The fixture's alerts name a number alone: nothing pairs, and no file is read."""
    api = FakeApi()
    api.codeql(HEAD, pull_alerts=(3, 9), main_alerts=(1, 2, 3))
    found = _red(api)
    assert "CodeQL finds 1 alerts this pull request brings (#9)" in found.output["summary"]
    assert not any("/contents/" in c for c in api.calls)


def test_every_language_main_analyses_is_required() -> None:
    api = FakeApi()
    api.codeql(HEAD, languages=("actions", "go"))
    api.answers[f"/repos/{REPO}/actions/runs/800/jobs?per_page=100&page=1"] = {
        "total_count": 1,
        "jobs": [{"name": "Analyze (actions)", "conclusion": "success"}],
    }
    assert "`Analyze (go)` is None" in _red(api).output["summary"]


CHECK = 66


def _check(api: FakeApi, **fields: Any) -> None:
    check = {
        "id": CHECK,
        "name": "CodeQL",
        "status": "completed",
        "conclusion": "neutral",
        "head_sha": HEAD,
        "app": {"id": mc.CODEQL_APP_ID, "slug": "github-advanced-security"},
    }
    api.answers[f"/repos/{REPO}/check-runs/{CHECK}"] = {**check, **fields}


def test_the_codeql_check_wakes_the_poster_and_its_neutral_counts_for_nothing() -> None:
    api = FakeApi()
    _check(api)
    _merge_runs(api, OWN_MERGE, [RUN])
    outcome = mc.judge_check(api, REPO, CHECK, CONTROLLER)
    assert outcome.posting is not None and outcome.posting.conclusion == "success"
    assert outcome.posting.head_sha == HEAD


@pytest.mark.parametrize(
    "fields, says",
    [
        ({"app": {"id": 5444147, "slug": "personalclaw-ci-controller"}}, "not code scanning's"),
        ({"name": "merge-verdict"}, "not a completed CodeQL check"),
        ({"status": "in_progress"}, "not a completed CodeQL check"),
    ],
)
def test_any_other_check_run_posts_nothing(fields: dict[str, Any], says: str) -> None:
    api = FakeApi()
    _check(api, **fields)
    outcome = mc.judge_check(api, REPO, CHECK, CONTROLLER)
    assert outcome.posting is None and outcome.dispatch is None and says in outcome.why


def test_decide_takes_a_check_run_id(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    api = FakeApi()
    _check(api, app={"id": 1, "slug": "someone"})
    argv = ["decide", "--repo", REPO, "--check-run-id", str(CHECK)]
    argv += ["--controller-revision", CONTROLLER, "--out", str(tmp_path / "v.json")]
    assert mc.main(argv, get=api) == 0
    assert "post=false" in (tmp_path / "outputs").read_text()


def test_analyses_still_processing_are_a_waiting_red_never_a_green() -> None:
    api = FakeApi()
    api.codeql(HEAD, pull_alerts=(), processed=("actions",))
    found = _red(api)
    assert "CodeQL's javascript-typescript results are still processing" in found.output["summary"]
    assert "CodeQL's python results are still processing" in found.output["summary"]
    assert f"```{mc.WAITING_FENCE}" in found.output["text"]


def test_an_analysis_of_another_commit_is_still_processing() -> None:
    api = FakeApi()
    api.codeql(HEAD)
    ref = f"/repos/{REPO}/code-scanning/analyses?ref=refs/pull/12/head&per_page=100"
    for analysis in api.answers[ref]:
        analysis["commit_sha"] = "9" * 40
    assert "results are still processing" in _red(api).output["summary"]


def test_a_red_for_another_reason_that_also_waits_on_codeql_is_marked_waiting() -> None:
    api = FakeApi()
    api.codeql(HEAD, status="in_progress")
    api.jobs(1, ("results", "completed", "failure"))
    found = _red(api)
    assert "is 'in_progress', not finished" in found.output["summary"]
    assert found.output["text"] == f"```{mc.WAITING_FENCE}\ncodeql\n```\n"


def test_a_red_with_nothing_pending_is_not_marked_waiting() -> None:
    api = FakeApi()
    api.jobs(1, ("results", "completed", "failure"))
    found = _red(api)
    assert mc.WAITING_FENCE not in found.output.get("text", ""), "a sweep has nothing to clear"


APP = 5444147
PULLS = f"/repos/{REPO}/pulls?state=open&base=main&per_page=100&page=1"
VERDICTS = (
    f"/repos/{REPO}/commits/{HEAD}/check-runs?check_name=merge-verdict&app_id={APP}"
    "&filter=latest&per_page=100"
)


def _posted(api: FakeApi, *checks: Mapping[str, Any]) -> None:
    api.answers[PULLS] = [{"number": 12, "head": {"sha": HEAD}}]
    api.answers[VERDICTS] = {"total_count": len(checks), "check_runs": list(checks)}


def _verdict(posting: mc.Posting, check_id: int, app: int = APP) -> dict[str, Any]:
    return {"id": check_id, "app": {"id": app}, **posting.body()}


def test_merge_before_codeql_then_codeql_finishes_then_green() -> None:
    api = FakeApi()
    api.codeql(HEAD, status="in_progress")
    _merge_runs(api, OWN_MERGE, [RUN])
    first = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert first is not None and first.conclusion == "failure"
    assert "waiting: CodeQL's analysis of the head is 'in_progress'" in first.output["title"]
    _posted(api, _verdict(first, 1))
    api.codeql(HEAD)
    [later] = mc.judge_waiting(api, REPO, APP, CONTROLLER)
    assert later.posting is not None and later.posting.conclusion == "success"
    assert later.posting.head_sha == HEAD


def test_the_sweep_posts_nothing_when_nothing_changed() -> None:
    api = FakeApi()
    api.codeql(HEAD, status="in_progress")
    _merge_runs(api, OWN_MERGE, [RUN])
    first = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert first is not None
    _posted(api, _verdict(first, 1))
    assert mc.judge_waiting(api, REPO, APP, CONTROLLER) == []


@pytest.mark.parametrize("why", ["a red for another reason", "another App's", "a green"])
def test_the_sweep_judges_again_only_a_waiting_red_of_the_controllers(why: str) -> None:
    api = FakeApi()
    _merge_runs(api, OWN_MERGE, [RUN])
    if why == "a green":
        newest = mc.decide(api, REPO, RUN, CONTROLLER).posting
        check = _verdict(newest, 1) if newest else {}
    else:
        api.codeql(HEAD, status="in_progress")
        newest = mc.decide(api, REPO, RUN, CONTROLLER).posting
        assert newest is not None
        check = _verdict(newest, 1, app=APP if why != "another App's" else 1)
        if why == "a red for another reason":
            check["output"] = {"title": "Red: attempt 1's `results` is 'failure'", "summary": "x"}
        api.codeql(HEAD)
    _posted(api, check)
    assert mc.judge_waiting(api, REPO, APP, CONTROLLER) == []


def _graph(*pulls: tuple[str, list[tuple[int, str, str]]], total: int | None = None) -> Any:
    """The one read's answer: each open pull request's head and its verdicts, as GraphQL spells
    them (``databaseId``, an upper-case ``conclusion``, ``text``)."""
    nodes = [
        {
            "commits": {
                "nodes": [
                    {
                        "commit": {
                            "oid": oid,
                            "checkSuites": {
                                "nodes": [
                                    {
                                        "checkRuns": {
                                            "nodes": [
                                                {"databaseId": i, "conclusion": c, "text": x}
                                                for i, c, x in runs
                                            ]
                                        }
                                    }
                                ]
                            },
                        }
                    }
                ]
            }
        }
        for oid, runs in pulls
    ]
    count = len(nodes) if total is None else total
    return {"data": {"repository": {"pullRequests": {"totalCount": count, "nodes": nodes}}}}


_WAITS = f"```{mc.WAITING_FENCE}\ncodeql\n```\n"


def _asked(answer: Any) -> tuple[mc.Query, list[Mapping[str, object]]]:
    """A query that answers *answer* (or raises it) and notes each read's variables."""
    seen: list[Mapping[str, object]] = []

    def query(text: str, variables: Mapping[str, object]) -> Any:
        assert text == mc.WAITING_QUERY
        seen.append(variables)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return query, seen


def test_one_read_naming_no_waiting_head_ends_the_sweep_with_no_other_read() -> None:
    api = FakeApi()
    api.calls.clear()
    query, seen = _asked(_graph((HEAD, [(1, "SUCCESS", "")]), ("b" * 40, [])))
    assert mc.judge_waiting(api, REPO, APP, CONTROLLER, query) == []
    assert seen == [{"owner": "owner", "name": "project", "app": APP}]
    assert api.calls == [], "the sweep read a head the one read said waits on nothing"


def test_one_read_naming_a_waiting_head_judges_it_again_through_the_rest_reads() -> None:
    api = FakeApi()
    api.codeql(HEAD, status="in_progress")
    _merge_runs(api, OWN_MERGE, [RUN])
    first = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert first is not None
    _posted(api, _verdict(first, 1))
    api.codeql(HEAD)
    query, _ = _asked(_graph((HEAD, [(1, "FAILURE", _WAITS)])))
    [later] = mc.judge_waiting(api, REPO, APP, CONTROLLER, query)
    assert later.posting is not None and later.posting.conclusion == "success"
    assert VERDICTS in api.calls, "the REST read of the head's verdicts is the gate"


def test_the_rest_reads_stay_the_gate_when_the_one_read_says_a_head_waits() -> None:
    """The one read only narrows: a head it calls waiting whose newest verdict the REST read
    finds green is not judged again."""
    api = FakeApi()
    _merge_runs(api, OWN_MERGE, [RUN])
    green = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert green is not None and green.conclusion == "success"
    _posted(api, _verdict(green, 2))
    query, _ = _asked(_graph((HEAD, [(1, "FAILURE", _WAITS)])))
    assert mc.judge_waiting(api, REPO, APP, CONTROLLER, query) == []


def test_the_newest_verdict_decides_in_the_one_read() -> None:
    query, _ = _asked(_graph((HEAD, [(1, "FAILURE", _WAITS), (2, "SUCCESS", "")])))
    assert mc.waiting_heads(query, REPO, APP) == []
    query, _ = _asked(_graph((HEAD, [(2, "FAILURE", _WAITS), (1, "SUCCESS", "")])))
    assert mc.waiting_heads(query, REPO, APP) == [HEAD]


@pytest.mark.parametrize(
    "answer",
    [
        OSError("GraphQL answered 502"),
        {"data": None, "errors": [{"message": "Resource not accessible by integration"}]},
        {"data": {"repository": None}},
        "not json",
        _graph((HEAD, [(1, "FAILURE", _WAITS)]), total=101),
        _graph(("not a sha", [])),
    ],
    ids=["no answer", "errors", "no repository", "a string", "too many", "no head"],
)
def test_a_one_read_it_cannot_read_leaves_the_sweep_reading_every_head(answer: Any) -> None:
    api = FakeApi()
    api.codeql(HEAD, status="in_progress")
    _merge_runs(api, OWN_MERGE, [RUN])
    first = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert first is not None
    _posted(api, _verdict(first, 1))
    api.codeql(HEAD)
    query, _ = _asked(answer)
    assert mc.waiting_heads(query, REPO, APP) is None
    [later] = mc.judge_waiting(api, REPO, APP, CONTROLLER, query)
    assert later.posting is not None and later.posting.conclusion == "success"


def test_decide_waiting_asks_the_one_read_it_is_given(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    api = FakeApi()
    api.calls.clear()
    query, seen = _asked(_graph((HEAD, [])))
    out = tmp_path / "v.json"
    argv = ["decide", "--repo", REPO, "--waiting", "--controller-revision", CONTROLLER]
    argv += ["--controller-app-id", str(APP), "--out", str(out)]
    assert mc.main(argv, get=api, query=query) == 0
    assert len(seen) == 1 and not out.exists()
    assert "post=false" in (tmp_path / "outputs").read_text()
    assert not [c for c in api.calls if "/pulls" in c or "/check-runs" in c]


def test_the_codeql_workflow_finishing_judges_its_head_again() -> None:
    api = FakeApi()
    _merge_runs(api, OWN_MERGE, [RUN])
    codeql_run = 801
    api.answers[f"/repos/{REPO}/actions/runs/{codeql_run}"] = {
        "id": codeql_run,
        "repository": {"full_name": REPO},
        "path": f"{mc.CODEQL_WORKFLOW}/codeql",
        "status": "completed",
        "head_sha": HEAD,
    }
    outcome = mc.judge(api, REPO, codeql_run, CONTROLLER)
    assert outcome.posting is not None and outcome.posting.conclusion == "success"


@pytest.mark.parametrize(
    "done, conclusion", [({}, "success"), ({"conclusion": "failure"}, "failure")]
)
def test_a_contract_run_that_reports_is_posted_by_the_sweep_and_its_branch_goes(
    tmp_path: Path, monkeypatch: Any, done: Mapping[str, Any], conclusion: str
) -> None:
    """#5524's case: a contract run, dispatched with the job's token, completes and wakes
    nothing. The schedule's sweep finds the head's waiting red, posts its verdict and names the
    contract branch to delete."""
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    api = _changes(FakeApi())
    _contract(api)
    first = mc.decide(api, REPO, RUN, CONTROLLER)
    assert first.posting is not None and first.dispatch == HEAD, "posted waiting, contract started"
    _merge_runs(api, OWN_MERGE, [RUN])
    _posted(api, _verdict(first.posting, 1))
    _contract(api, {"status": "in_progress", "conclusion": None})
    [running] = mc.judge_waiting(api, REPO, APP, CONTROLLER)
    assert running.posting is not None and running.cleanup is None
    _posted(api, _verdict(running.posting, 2))
    _contract(api, done)
    _refs(api, mc.CONTRACT_PREFIX, {CONTRACT_BRANCH: MAIN})
    _refs(api, mc.TRUSTED_PREFIX, {})
    out = tmp_path / "v.json"
    argv = ["decide", "--repo", REPO, "--waiting", "--controller-app-id", str(APP)]
    assert mc.main([*argv, "--controller-revision", CONTROLLER, "--out", str(out)], get=api) == 0
    [posted] = json.loads(out.read_text())
    assert (posted["head_sha"], posted["conclusion"]) == (HEAD, conclusion)
    lines = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
    assert lines["post"] == "true" and lines["cleanup"] == CONTRACT_BRANCH
    assert lines["dispatch"] == "", "a sweep starts no contract run"


def test_decide_waiting_writes_every_changed_verdict(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    api = FakeApi()
    api.codeql(HEAD, status="in_progress")
    _merge_runs(api, OWN_MERGE, [RUN])
    first = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert first is not None
    _posted(api, _verdict(first, 1))
    api.codeql(HEAD)
    out = tmp_path / "v.json"
    argv = ["decide", "--repo", REPO, "--waiting", "--controller-revision", CONTROLLER]
    with pytest.raises(SystemExit):
        mc.main([*argv, "--out", str(out)], get=api)
    assert mc.main([*argv, "--controller-app-id", str(APP), "--out", str(out)], get=api) == 0
    assert [b["conclusion"] for b in json.loads(out.read_text())] == ["success"]
    lines = (tmp_path / "outputs").read_text()
    assert "post=true" in lines and "dispatch=\n" in lines


# ── Every poster run judges a few waiting heads again (#4584 step 4) ───────────────────────────


def _verdicts_of(head: str) -> str:
    return VERDICTS.replace(HEAD, head)


@pytest.mark.parametrize(
    "done, conclusion", [({}, "success"), ({"conclusion": "failure"}, "failure")]
)
def test_a_contract_that_reports_is_posted_by_the_next_unrelated_poster_run(
    tmp_path: Path, monkeypatch: Any, done: Mapping[str, Any], conclusion: str
) -> None:
    """No schedule and no wake from the contract run: a poster run woken by another workflow's
    completion judges the waiting head again, posts its verdict and deletes its branch."""
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    api = _changes(FakeApi())
    _contract(api)
    first = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert first is not None and mc.WAITING_FENCE in first.output["text"]
    _merge_runs(api, OWN_MERGE, [RUN])
    _posted(api, _verdict(first, 1))
    _contract(api, done)
    _refs(api, mc.CONTRACT_PREFIX, {CONTRACT_BRANCH: MAIN})
    _refs(api, mc.TRUSTED_PREFIX, {})
    other = 777
    run = api.answers[f"/repos/{REPO}/actions/runs/{RUN}"]
    api.answers[f"/repos/{REPO}/actions/runs/{other}"] = {
        **run,
        "id": other,
        "path": f"{WF}something-else.yml",
    }
    query, _ = _asked(_graph((HEAD, [(1, "FAILURE", first.output["text"])])))
    out = tmp_path / "v.json"
    argv = ["decide", "--repo", REPO, "--run-id", str(other), "--controller-app-id", str(APP)]
    argv += ["--controller-revision", CONTROLLER, "--out", str(out)]
    assert mc.main(argv, get=api, query=query) == 0
    [posted] = json.loads(out.read_text())
    assert (posted["head_sha"], posted["conclusion"]) == (HEAD, conclusion)
    lines = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
    assert lines["post"] == "true" and lines["cleanup"] == CONTRACT_BRANCH
    assert lines["dispatch"] == "", "the other run's outcome starts nothing, the sweep neither"


def test_a_poster_run_judges_at_most_a_few_waiting_heads_the_oldest_first() -> None:
    heads = {"d" * 40: 1, "b" * 40: 2, "a" * 40: 5, "c" * 40: 9}
    api = FakeApi()
    api.answers[PULLS] = [{"number": n, "head": {"sha": h}} for n, h in enumerate(heads)]
    for head in heads:
        api.answers[_verdicts_of(head)] = {"total_count": 0, "check_runs": []}
    graph = _graph(*[(h, [(i, "FAILURE", _WAITS)]) for h, i in heads.items()])
    query, _ = _asked(graph)
    api.calls.clear()
    assert mc.judge_waiting(api, REPO, APP, CONTROLLER, query, limit=2) == []
    read = [h for h in heads if _verdicts_of(h) in api.calls]
    assert sorted(read) == ["b" * 40, "d" * 40], "the two oldest waiting verdicts, no more"


def test_a_poster_run_does_not_judge_its_own_head_twice() -> None:
    api = FakeApi()
    api.answers[PULLS] = [{"number": 12, "head": {"sha": HEAD}}]
    query, _ = _asked(_graph((HEAD, [(1, "FAILURE", _WAITS)])))
    api.calls.clear()
    assert (
        mc.judge_waiting(api, REPO, APP, CONTROLLER, query, limit=3, skip=frozenset({HEAD})) == []
    )
    assert VERDICTS not in api.calls


def test_a_poster_run_whose_one_read_gives_no_answer_judges_no_waiting_head() -> None:
    api = FakeApi()
    api.calls.clear()
    query, _ = _asked(OSError("GraphQL answered 502"))
    assert mc.judge_waiting(api, REPO, APP, CONTROLLER, query, limit=3) == []
    assert api.calls == [], "only the schedule's sweep reads every head"


# ── A red for another reason is judged again until nothing waits (#4584 step 4) ───────────────


@pytest.mark.parametrize(
    "done, says",
    [
        ({}, None),
        (
            {"conclusion": "failure"},
            "main's CI contract fails on its definitions: contract run "
            f"https://github.com/{REPO}/actions/runs/4242 is 'failure'",
        ),
    ],
    ids=["contract passed", "contract failed"],
)
def test_a_red_head_whose_contract_reports_gets_its_final_reasons_and_its_branch_goes(
    tmp_path: Path, monkeypatch: Any, done: Mapping[str, Any], says: str | None
) -> None:
    """#5528's case: red for its own run, and waiting on its contract. Once the contract reports,
    an unrelated poster run posts the final red, which no longer says it waits and names the
    contract's failure when there is one, and the contract branch is deleted."""
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    api = _changes(FakeApi())
    api.jobs(1, ("results", "completed", "failure"))
    _contract(api)
    first = mc.decide(api, REPO, RUN, CONTROLLER)
    assert first.posting is not None and first.dispatch == HEAD
    _merge_runs(api, OWN_MERGE, [RUN])
    _posted(api, _verdict(first.posting, 1))
    _contract(api, done)
    _refs(api, mc.CONTRACT_PREFIX, {CONTRACT_BRANCH: MAIN})
    _refs(api, mc.TRUSTED_PREFIX, {})
    other = 777
    run = api.answers[f"/repos/{REPO}/actions/runs/{RUN}"]
    api.answers[f"/repos/{REPO}/actions/runs/{other}"] = {
        **run,
        "id": other,
        "path": f"{WF}something-else.yml",
    }
    query, _ = _asked(_graph((HEAD, [(1, "FAILURE", first.posting.output["text"])])))
    out = tmp_path / "v.json"
    argv = ["decide", "--repo", REPO, "--run-id", str(other), "--controller-app-id", str(APP)]
    argv += ["--controller-revision", CONTROLLER, "--out", str(out)]
    assert mc.main(argv, get=api, query=query) == 0
    [posted] = json.loads(out.read_text())
    assert (posted["head_sha"], posted["conclusion"]) == (HEAD, "failure")
    summary = posted["output"]["summary"]
    assert "attempt 1's `results` is 'failure'" in summary
    assert "waiting:" not in summary, "nothing it waited on is still said to be pending"
    assert mc.WAITING_FENCE not in (posted["output"].get("text") or ""), "and it is judged no more"
    if says is not None:
        assert says in summary
    lines = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
    assert lines["post"] == "true" and lines["cleanup"] == CONTRACT_BRANCH


def test_a_red_with_nothing_pending_is_never_judged_again() -> None:
    api = FakeApi()
    _merge_runs(api, OWN_MERGE, [RUN])
    api.jobs(1, ("results", "completed", "failure"))
    red = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert red is not None and mc.WAITING_FENCE not in red.output.get("text", "")
    _posted(api, _verdict(red, 1))
    query, _ = _asked(_graph((HEAD, [(1, "FAILURE", red.output.get("text", ""))])))
    assert mc.waiting_heads(query, REPO, APP) == []
    api.calls.clear()
    assert mc.judge_waiting(api, REPO, APP, CONTROLLER, query) == []
    assert VERDICTS not in api.calls, "the one read names no head to read again"


def test_an_unchanged_waiting_red_is_judged_to_the_same_bytes_and_not_posted_again() -> None:
    """The sweep posts only what changed, so the same state must give the same verdict, byte for
    byte: nothing in it may be a time, or follow the order the API happens to list things in."""
    api = _changes(FakeApi())
    api.jobs(1, ("results", "completed", "failure"))
    api.codeql(HEAD, processed=("actions",))
    _contract(api, {"status": "in_progress", "conclusion": None})
    _merge_runs(api, OWN_MERGE, [RUN])
    first = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert first is not None and mc.WAITING_FENCE in first.output["text"]
    assert "results are still processing" in first.output["summary"]
    # The same state, listed in another order.
    for path, answer in list(api.answers.items()):
        if "/code-scanning/" in path and isinstance(answer, list):
            api.answers[path] = list(reversed(answer))
    again = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert again is not None and json.dumps(again.body()) == json.dumps(first.body())
    _posted(api, _verdict(first, 1))
    assert mc.judge_waiting(api, REPO, APP, CONTROLLER) == [], "nothing changed, nothing posted"


# ── A Merge run that has started: its contract beside it ──────────────────────────────────────

STARTED_JOB = {
    "id": 501,
    "name": "changes",
    "status": "in_progress",
    "conclusion": None,
    "started_at": "2026-10-28T00:00:05Z",
    "runner_id": 1000147145,
    "runner_name": "GitHub Actions 1000147145",
}
ATTEMPT_1_JOBS = f"/repos/{REPO}/actions/runs/{RUN}/attempts/1/jobs?per_page=100&page=1"


def _started(api: FakeApi, *jobs: Mapping[str, Any], **run: Any) -> FakeApi:
    """The head's Merge run, started: attempt 1's jobs as the jobs API lists them, no contract run
    and no contract branch yet, unless a case plants them."""
    api.run(**{"status": "in_progress", **run})
    listed = [dict(j) for j in jobs]
    api.answers[ATTEMPT_1_JOBS] = {"total_count": len(listed), "jobs": listed}
    api.answers.setdefault(CONTRACT_RUNS, {"total_count": 0, "workflow_runs": []})
    api.answers.setdefault(f"/repos/{REPO}/git/matching-refs/heads/{mc.CONTRACT_PREFIX}", [])
    return api


def test_a_started_run_of_a_definition_head_starts_its_contract_and_posts_nothing() -> None:
    api = _started(_changes(FakeApi()), STARTED_JOB)
    outcome = mc.start_contract(api, REPO, RUN)
    assert (outcome.dispatch, outcome.base, outcome.posting) == (HEAD, MAIN, None)
    assert "starts beside its run" in outcome.why


@pytest.mark.parametrize(
    "job",
    [
        pytest.param(None, id="no job yet"),
        pytest.param({"status": "queued", "started_at": None, "runner_name": None}, id="queued"),
        pytest.param({"status": "waiting", "started_at": None, "runner_name": ""}, id="waiting"),
        pytest.param(
            {"status": "completed", "conclusion": "action_required"}, id="action_required"
        ),
        pytest.param({"started_at": None}, id="no started_at"),
        pytest.param({"runner_name": ""}, id="no runner name"),
        pytest.param({"runner_id": 0}, id="runner id 0"),
        pytest.param({"runner_id": None}, id="no runner id"),
        pytest.param(
            {"status": "queued", "started_at": "2026-10-28T00:00:01Z"}, id="queued, a time"
        ),
        pytest.param({"status": "completed", "conclusion": "skipped"}, id="skipped"),
        pytest.param({"status": "pending"}, id="pending"),
    ],
)
def test_no_contract_starts_until_a_job_of_attempt_1_ran_on_a_runner(
    job: Mapping[str, Any] | None,
) -> None:
    """The approval gate, read from the jobs API: a run from a fork that waits for a
    maintainer's approval starts no job on a runner, whatever the run itself says."""
    jobs = () if job is None else ({**STARTED_JOB, **job},)
    api = _started(_changes(FakeApi()), *jobs)
    outcome = mc.start_contract(api, REPO, RUN)
    assert outcome.dispatch is None and outcome.posting is None
    assert "may wait for a maintainer's approval" in outcome.why


class _Recorded(dict):  # type: ignore[type-arg]
    """A run whose every read is recorded, to show which of its fields are read."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.read: set[str] = set()

    def get(self, key: Any, default: Any = None) -> Any:
        self.read.add(key)
        return super().get(key, default)

    def __getitem__(self, key: Any) -> Any:
        self.read.add(key)
        return super().__getitem__(key)


#: A fork's run as the API gives it: its head is in another repository, and its branch, which
#: its author names, says whatever the author likes; GitHub lists no pull request for it.
FORK_FIELDS = {
    "head_repository": {"full_name": "someone/project", "fork": True},
    "head_branch": "contract-run/" + "9" * 40,
    "pull_requests": [],
    "display_title": "$(curl evil)",
    "conclusion": "success",
}


@pytest.mark.parametrize("ran", [True, False], ids=["approved", "awaiting approval"])
def test_a_forks_started_run_is_judged_by_its_jobs_and_nothing_its_author_names(ran: bool) -> None:
    api = _started(_changes(FakeApi()), *([STARTED_JOB] if ran else []), **FORK_FIELDS)
    path = f"/repos/{REPO}/actions/runs/{RUN}"
    api.answers[path] = _Recorded(api.answers[path])
    outcome = mc.start_contract(api, REPO, RUN)
    assert (outcome.dispatch, outcome.posting) == ((HEAD, None) if ran else (None, None))
    assert api.answers[path].read <= mc.START_READS, api.answers[path].read
    assert not {"head_repository", "head_branch", "pull_requests"} & api.answers[path].read


@pytest.mark.parametrize(
    "plant, says",
    [
        pytest.param(
            lambda api: _contract(api, {"status": "in_progress"}), "already has", id="a run"
        ),
        pytest.param(lambda api: _contract(api, {}), "already has", id="a finished run"),
        pytest.param(
            lambda api: _refs(api, mc.CONTRACT_PREFIX, {CONTRACT_BRANCH: MAIN}),
            "already at main's tip",
            id="its branch at main's tip",
        ),
        pytest.param(
            lambda api: api.run(run_attempt=2, status="in_progress"), "a re-run", id="a re-run"
        ),
    ],
)
def test_a_second_start_or_a_re_run_starts_no_second_contract(plant: Any, says: str) -> None:
    api = _started(_changes(FakeApi()), STARTED_JOB)
    plant(api)
    outcome = mc.start_contract(api, REPO, RUN)
    assert outcome.dispatch is None and outcome.posting is None and says in outcome.why


def test_the_second_start_of_one_run_finds_the_first_ones_branch_and_starts_nothing() -> None:
    api = _started(_changes(FakeApi()), STARTED_JOB)
    first = mc.start_contract(api, REPO, RUN)
    assert first.dispatch == HEAD
    # What the first start's contract job does before its dispatch: the branch, at main's tip.
    _refs(api, mc.CONTRACT_PREFIX, {CONTRACT_BRANCH: first.base or ""})
    assert mc.start_contract(api, REPO, RUN).dispatch is None


def test_a_contract_branch_left_at_an_older_tip_does_not_stop_a_start() -> None:
    api = _started(_changes(FakeApi()), STARTED_JOB)
    _refs(api, mc.CONTRACT_PREFIX, {CONTRACT_BRANCH: "9" * 40})
    assert mc.start_contract(api, REPO, RUN).dispatch == HEAD


@pytest.mark.parametrize(
    "change, says",
    [
        pytest.param(
            lambda api: api.run(event="workflow_dispatch", status="in_progress"),
            "own run",
            id="dispatch",
        ),
        pytest.param(lambda api: api.run(event="push", status="in_progress"), "own run", id="push"),
        pytest.param(
            lambda api: api.run(path=f"{WF}other.yml", status="in_progress"),
            "is not",
            id="other workflow",
        ),
        pytest.param(lambda api: api.run(status="queued"), "not started", id="queued"),
        pytest.param(lambda api: api.run(status="waiting"), "not started", id="waiting"),
        pytest.param(
            lambda api: api.answers.update(
                {f"/repos/{REPO}/compare/{MAIN}...{HEAD}": {"behind_by": 2}}
            ),
            "behind main",
            id="behind main",
        ),
    ],
)
def test_a_start_that_is_not_a_current_pull_requests_own_run_starts_nothing(
    change: Any, says: str
) -> None:
    api = _started(_changes(FakeApi()), STARTED_JOB)
    change(api)
    outcome = mc.start_contract(api, REPO, RUN)
    assert outcome.dispatch is None and outcome.posting is None and says in outcome.why


def test_a_started_run_of_a_head_that_changes_no_definition_starts_nothing() -> None:
    api = _started(FakeApi(), STARTED_JOB)
    outcome = mc.start_contract(api, REPO, RUN)
    assert outcome.dispatch is None and "changes no CI definition" in outcome.why


def test_start_never_posts_even_where_every_other_path_would(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A state where judging would post: a green completed run, a waiting head to judge again,
    and stale branches to sweep. --start writes no body and says post=false, and no judge runs."""
    api = _changes(FakeApi())
    _contract(api, {})
    _started(api, STARTED_JOB, status="completed")
    _refs(api, mc.CONTRACT_PREFIX, {mc.CONTRACT_PREFIX + "1" * 40: "9" * 40})
    _merge_runs(api, OWN_MERGE, [RUN])
    assert mc.decide(api, REPO, RUN, CONTROLLER).posting is not None, "this state would post"

    def judged(*_: Any, **__: Any) -> Any:
        raise AssertionError("--start judged something")

    for name in ("decide", "judge", "rejudge", "judge_waiting", "judge_check", "wake"):
        monkeypatch.setattr(mc, name, judged)
    out, outputs = tmp_path / "verdict.json", tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    argv = ["decide", "--repo", REPO, "--start", str(RUN), "--controller-revision", CONTROLLER]
    query, _ = _asked(_graph((HEAD, [(1, "FAILURE", f"```{mc.WAITING_FENCE}\ncontract\n```\n")])))
    argv += ["--controller-app-id", str(APP), "--out", str(out)]
    assert mc.main(argv, get=api, query=query) == 0
    assert not out.exists(), "--start wrote a body to post"
    assert outputs.read_text().splitlines() == ["dispatch=", "base=", "cleanup=", "post=false"]


def test_start_names_the_head_to_dispatch_and_nothing_else(
    tmp_path: Path, monkeypatch: Any
) -> None:
    api = _started(_changes(FakeApi()), STARTED_JOB)
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    argv = ["decide", "--repo", REPO, "--start", str(RUN), "--controller-revision", CONTROLLER]
    assert mc.main([*argv, "--out", str(tmp_path / "v.json")], get=api) == 0
    assert outputs.read_text().splitlines() == [
        f"dispatch={HEAD}",
        f"base={MAIN}",
        "cleanup=",
        "post=false",
    ]


def test_an_unread_start_starts_nothing_and_leaves_it_to_the_runs_completion(
    tmp_path: Path, monkeypatch: Any
) -> None:
    api = _started(_changes(FakeApi()), STARTED_JOB)
    del api.answers[ATTEMPT_1_JOBS]
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    argv = ["decide", "--repo", REPO, "--start", str(RUN), "--controller-revision", CONTROLLER]
    assert mc.main([*argv, "--out", str(tmp_path / "v.json")], get=api) == 0
    assert outputs.read_text().splitlines() == ["dispatch=", "base=", "cleanup=", "post=false"]


# ── A contract run's wake: the same judge as a Merge run's completion ─────────────────────────


def _state(name: str) -> FakeApi:
    """One state of a head, as both entry points read it: its completed Merge run, its contract
    runs, and its branches."""
    api = FakeApi()
    _refs(api, mc.CONTRACT_PREFIX, {})
    _refs(api, mc.TRUSTED_PREFIX, {})
    if name != "no definition":
        _changes(api)
    _contract(api)
    if name == "results red":
        api.jobs(1, ("results", "completed", "failure"))
    if name == "red on attempt 1, green on 2":
        api.run(run_attempt=2)
        api.jobs(1, ("results", "completed", "failure"))
        api.jobs(2, ("results", "completed", "success"))
    if name == "behind main":
        api.answers[f"/repos/{REPO}/compare/{MAIN}...{HEAD}"] = {"behind_by": 3}
    if name == "contract green":
        _contract(api, {})
        _refs(api, mc.CONTRACT_PREFIX, {CONTRACT_BRANCH: MAIN})
    if name == "contract red":
        _contract(api, {"conclusion": "failure"})
    if name == "contract running":
        _contract(api, {"status": "in_progress", "conclusion": None})
    if name == "no contract yet":
        _contract(api)
    if name == "review red":
        api.review(HEAD, ("completed", "failure"))
    if name == "no evidence":
        api.artifacts({"web-dist": "sha256:" + "e" * 64})
    _merge_runs(api, OWN_MERGE, [RUN])
    return api


STATES = (
    "contract green",
    "contract red",
    "contract running",
    "no contract yet",
    "results red",
    "red on attempt 1, green on 2",
    "behind main",
    "review red",
    "no evidence",
    "no definition",
)


def _through(entry: list[str], api: FakeApi, tmp_path: Path, monkeypatch: Any) -> tuple[Any, ...]:
    out, outputs = tmp_path / f"{entry[0]}.json", tmp_path / f"{entry[0]}.outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    argv = ["decide", "--repo", REPO, *entry, "--controller-revision", CONTROLLER]
    assert mc.main([*argv, "--out", str(out)], get=api) == 0
    body = json.loads(out.read_text()) if out.exists() else None
    return body, outputs.read_text().splitlines()


@pytest.mark.parametrize("state", STATES)
def test_a_wake_and_a_merge_runs_completion_give_one_verdict(
    state: str, tmp_path: Path, monkeypatch: Any
) -> None:
    """The differential: one API state, judged through the completed Merge run (``--run-id``)
    and through a contract run's wake (``--head``), gives the same check run, byte for byte, and
    the same dispatch, base and branches to delete."""
    by_run = _through(["--run-id", str(RUN)], _state(state), tmp_path, monkeypatch)
    by_head = _through(["--head", HEAD], _state(state), tmp_path, monkeypatch)
    assert by_head == by_run
    assert by_run[0] is not None, "every state posts a verdict"


def test_the_states_each_judge_as_their_names_say() -> None:
    """A control for the differential: its states are not one verdict under many names."""
    postings = {s: mc.decide(_state(s), REPO, RUN, CONTROLLER).posting for s in STATES}
    assert all(isinstance(p, mc.Posting) for p in postings.values())
    said = {s: p.output["title"] for s, p in postings.items() if p is not None}
    assert said["contract green"].startswith("Green") and said["no definition"].startswith("Green")
    assert "contract fails" in said["contract red"]
    assert "'in_progress'" in said["contract running"] and "is started" in said["no contract yet"]
    assert "behind main" in said["behind main"]
    assert "attempt 1's `results` is 'failure'" in said["red on attempt 1, green on 2"]
    bodies = {json.dumps(p.body(), sort_keys=True) for p in postings.values() if p is not None}
    assert len(bodies) == len(STATES)


@pytest.mark.parametrize("head", ["main", "a" * 39, "A" * 40, "$(id)", ""])
def test_a_wake_for_what_is_not_a_full_commit_id_judges_nothing(head: str) -> None:
    api = _state("contract green")
    api.calls.clear()
    outcome = mc.wake(api, REPO, head, CONTROLLER)
    assert outcome.posting is None and "not a full commit id" in outcome.why
    assert api.calls == []


# ── The approval gate on the run's completion, and the contract judged by its job ─────────────


def test_a_completed_run_that_ran_no_job_on_a_runner_starts_no_contract() -> None:
    """A fork's run that waits for approval, were GitHub to report it completed with
    ``action_required``: its head is red, and main's contract is not started over its tools/ci."""
    api = _changes(FakeApi())
    _contract(api)
    api.run(**{**FORK_FIELDS, "conclusion": "action_required"})
    api.answers[ATTEMPT_1_JOBS] = {
        "total_count": 1,
        "jobs": [{**STARTED_JOB, "status": "completed", "conclusion": "action_required"}],
    }
    outcome = mc.decide(api, REPO, RUN, CONTROLLER)
    assert outcome.dispatch is None
    assert outcome.posting is not None and outcome.posting.conclusion == "failure"
    assert "is not started from it" in outcome.posting.output["summary"]
    assert mc.WAITING_FENCE not in outcome.posting.output.get("text", ""), "nothing it waits on"


def test_a_completed_re_run_starts_the_contract_from_the_attempt_it_ran() -> None:
    """Attempt 1 cancelled in the queue, attempt 2 ran: its completion starts the contract."""
    api = _changes(FakeApi())
    _contract(api)
    api.run(run_attempt=2)
    api.answers[ATTEMPT_1_JOBS] = {
        "total_count": 1,
        "jobs": [{"id": 1, "name": "results", "status": "completed", "conclusion": "cancelled"}],
    }
    api.jobs(2, ("results", "completed", "success"))
    outcome = mc.decide(api, REPO, RUN, CONTROLLER)
    assert outcome.dispatch == HEAD
    assert (
        outcome.posting is not None and "attempt 1's `results`" in outcome.posting.output["title"]
    )


def test_a_contract_whose_wake_failed_is_judged_by_its_contract_job() -> None:
    api = _changes(FakeApi())
    _contract(
        api,
        {
            "conclusion": "failure",
            "jobs": [
                {"name": "contract", "status": "completed", "conclusion": "success"},
                {"name": "wake", "status": "completed", "conclusion": "failure"},
            ],
        },
    )
    found = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert found is not None and found.conclusion == "success", found


def test_a_contract_job_that_failed_is_red_whatever_its_run_says() -> None:
    api = _changes(FakeApi())
    _contract(
        api,
        {
            "conclusion": "success",
            "jobs": [{"name": "contract", "status": "completed", "conclusion": "failure"}],
        },
    )
    found = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert found is not None and "main's CI contract fails" in found.output["title"]


@pytest.mark.parametrize(
    "jobs",
    [
        pytest.param([], id="none"),
        pytest.param(
            [
                {"name": "contract", "status": "completed", "conclusion": "success"},
                {"name": "contract", "status": "completed", "conclusion": "success"},
            ],
            id="two",
        ),
    ],
)
def test_a_completed_contract_run_with_other_than_one_contract_job_is_unread(
    jobs: list[dict[str, Any]],
) -> None:
    api = _changes(FakeApi())
    _contract(api, {"jobs": jobs})
    found = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert found is not None and "unread" in found.output["summary"]
    assert "not one" in found.output["summary"]


def test_a_contract_run_just_dispatched_and_listing_no_job_yet_is_waited_on() -> None:
    api = _changes(FakeApi())
    _contract(api, {"status": "queued", "conclusion": None, "jobs": []})
    found = mc.decide(api, REPO, RUN, CONTROLLER).posting
    assert found is not None and mc.WAITING_FENCE in found.output["text"]
    assert "'queued'" in found.output["title"]


@pytest.mark.parametrize(
    "head",
    [
        "main",
        "a" * 39,
        "A" * 40,
        "$(id)",
        "a" * 40 + "; true",
        "../../" + "a" * 40,
        "-x",
        "--run-id",
        "--run-id\n" + "a" * 40,
        "a" * 40 + "\n",
    ],
)
def test_a_wake_dispatched_with_a_malformed_head_posts_nothing(
    head: str, tmp_path: Path, monkeypatch: Any
) -> None:
    """The dispatch's ``head`` reaches the controller through the step's environment, quoted,
    as one argument, once the step's own check has passed it: here the controller's check, given
    the head as ``--head=`` would hand it on whatever it starts with. What is not a full commit id
    is refused before any read, and posts nothing."""
    api = _state("contract green")
    api.calls.clear()
    out, outputs = tmp_path / "v.json", tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    argv = ["decide", "--repo", REPO, f"--head={head}", "--controller-revision", CONTROLLER]
    assert mc.main([*argv, "--out", str(out)], get=api) == 0
    assert not out.exists(), "a malformed head wrote a body to post"
    assert "post=false" in outputs.read_text().splitlines()
    assert "dispatch=" in outputs.read_text().splitlines()
    # Only the sweep of stale branches reads anything: main's tip and the branch lists.
    sweep = {
        f"/repos/{REPO}/git/ref/heads/main",
        f"/repos/{REPO}/git/matching-refs/heads/{mc.CONTRACT_PREFIX}",
        f"/repos/{REPO}/git/matching-refs/heads/{mc.TRUSTED_PREFIX}",
    }
    assert set(api.calls) <= sweep, set(api.calls) - sweep


# ── A queue commit of main's merge queue (0.4.0 A1) ───────────────────────────────────────────

APP = 5444147
QUEUE = "e" * 40
AHEAD = "9" * 40
README_PATCH = "@@ -1 +1 @@\n-hi\n+changed"


def _verdicts(api: FakeApi, commit: str, *checks: Mapping[str, Any]) -> None:
    """The App's merge verdicts on a commit, as the newest-verdict read lists them."""
    query = urllib.parse.urlencode(
        {"check_name": "merge-verdict", "app_id": APP, "filter": "latest", "per_page": 100}
    )
    listed = [{"id": 700 + n, "app": {"id": APP}, "status": "completed", **c} for n, c in
              enumerate(checks)]  # fmt: skip
    api.answers[f"/repos/{REPO}/commits/{commit}/check-runs?{query}"] = {"check_runs": listed}


def _entry(
    queue_commit: str, built_on: str, number: int = 12, position: int = 1, **pull: Any
) -> dict[str, Any]:
    return {
        "position": position,
        "headCommit": {"oid": queue_commit} if queue_commit else None,
        "baseCommit": {"oid": built_on} if built_on else None,
        "pullRequest": {
            "number": number,
            "state": "OPEN",
            "baseRefName": "main",
            "headRefOid": HEAD,
            **pull,
        },
    }


class FakeQueue:
    """Main's merge queue, as its one GraphQL read answers."""

    def __init__(self, *entries: dict[str, Any]) -> None:
        self.entries = list(entries)

    def __call__(self, text: str, variables: Mapping[str, object]) -> Any:
        if "mergeQueue" not in text:
            # Any other read (a red's retry note) is unanswered here, as a GraphQL error would be.
            raise OSError("no planted answer for this query")
        assert variables == {"owner": "owner", "name": "project"}
        nodes = list(self.entries)
        return {"data": {"repository": {"mergeQueue": {"entries": {
            "totalCount": len(nodes), "nodes": nodes}}}}}  # fmt: skip


def _queued(built_on: str = MAIN, number: int = 12) -> tuple[FakeApi, FakeQueue]:
    """A green queue commit: #12's head squashed on ``built_on``, its run green, its pull
    request's head green, and its change the pull request's own."""
    api = FakeApi()
    api.run(
        event="merge_group",
        head_sha=QUEUE,
        head_branch=f"gh-readonly-queue/main/pr-{number}-{built_on}",
    )
    api.tree(QUEUE, {MERGE_PATH: MERGE_YML, "README.md": "changed\n"}, parents=(built_on,))
    files = [
        {"filename": "README.md", "status": "modified", "patch": README_PATCH, "sha": "1" * 40}
    ]
    api.answers[f"/repos/{REPO}/compare/{built_on}...{QUEUE}"] = {"files": files}
    api.answers[f"/repos/{REPO}/compare/main...{HEAD}"] = {"files": files}
    _verdicts(api, QUEUE)
    _verdicts(api, HEAD, {"conclusion": "success"})
    return api, FakeQueue(_entry(QUEUE, built_on, number))


def _judge_queue(api: FakeApi, queue: FakeQueue, live: bool = True) -> mc.Posting | str:
    outcome = mc.decide(api, REPO, RUN, CONTROLLER, mc.Queue(queue, APP, live))
    return outcome.posting or outcome.why


def _queue_red(api: FakeApi, queue: FakeQueue) -> mc.Posting:
    found = _judge_queue(api, queue)
    assert isinstance(found, mc.Posting) and found.conclusion == "failure", found
    assert found.head_sha == QUEUE
    return found


def test_a_green_queue_commit_posts_success_on_the_queue_commit_with_its_receipt() -> None:
    found = _judge_queue(*_queued())
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    assert found.head_sha == QUEUE
    assert found.output["title"] == "Green: every planned job passed on #12's queue commit"
    receipt = merge_verdict.read_receipt(mc.check_body(found.body()))
    assert receipt.head_sha == QUEUE and receipt.tree_oid == api_tree_oid(QUEUE, _queued()[0])
    assert receipt.toolchain["queue_pull_request"] == "12"
    assert receipt.toolchain["queue_pull_request_head"] == HEAD
    assert receipt.toolchain["queue_built_on"] == MAIN
    assert receipt.toolchain["definitions"] == MAIN
    assert "built on `" + MAIN + "`, which is main's tip" in found.output["summary"]


def test_a_queue_commit_built_on_the_entry_ahead_is_judged_green() -> None:
    api, queue = _queued(built_on=AHEAD)
    queue.entries.insert(0, _entry(AHEAD, MAIN, number=11, position=1))
    queue.entries[1]["position"] = 2
    found = _judge_queue(api, queue)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    assert "the queue commit of the entry ahead" in found.output["summary"]


def test_a_queue_run_without_the_queues_read_posts_nothing() -> None:
    api, _queue = _queued()
    assert "judged only with the queue's read" in str(_decide(api))


def test_a_queue_run_whose_branch_names_no_entry_posts_nothing() -> None:
    api, queue = _queued()
    api.answers[f"/repos/{REPO}/actions/runs/{RUN}"][
        "head_branch"
    ] = f"gh-readonly-queue/main/{HEAD}"
    assert "names no entry of main's queue" in str(_judge_queue(api, queue))


def test_a_queue_commit_the_queue_no_longer_lists_posts_nothing() -> None:
    api, _queue = _queued()
    assert "no entry of main's merge queue now" in str(_judge_queue(api, FakeQueue()))


def test_a_queue_commit_whose_branch_names_another_pull_request_is_red() -> None:
    api, queue = _queued()
    queue.entries[0]["pullRequest"]["number"] = 13
    assert (
        "the queue lists it for #13, its branch names #12"
        in _queue_red(api, queue).output["summary"]
    )


def test_a_queue_commit_whose_parent_is_not_the_commit_it_was_built_on_is_red() -> None:
    api, queue = _queued()
    api.tree(QUEUE, {MERGE_PATH: MERGE_YML, "README.md": "changed\n"}, parents=(MAIN, AHEAD))
    assert "not one commit" in _queue_red(api, queue).output["summary"]


def test_a_queue_commit_built_past_a_gap_in_the_queue_is_red() -> None:
    """Built on main's tip while an entry ahead of it is queued: main would reach the entry
    ahead's tree and then this one's, and this one's run never saw the entry ahead."""
    api, queue = _queued()
    queue.entries.insert(0, _entry(AHEAD, MAIN, number=11, position=1))
    queue.entries[1]["position"] = 2
    found = _queue_red(api, queue)
    assert "neither main's tip nor the queue commit of the entry ahead" in found.output["summary"]


@pytest.mark.parametrize("verdict", [None, "failure"])
def test_a_queue_commit_whose_pull_requests_head_is_not_green_is_red(verdict: str | None) -> None:
    api, queue = _queued()
    if verdict is None:
        _verdicts(api, HEAD)
    else:
        _verdicts(api, HEAD, {"conclusion": verdict})
    found = _queue_red(api, queue)
    assert f"its pull request's head {HEAD} has no green merge verdict" in found.output["summary"]


@pytest.mark.parametrize(
    "queued, said",
    [
        ([{"filename": "README.md", "patch": README_PATCH},
          {"filename": "extra.py", "patch": "@@ -0,0 +1 @@\n+x = 1"}],
         "the queue commit changes `extra.py`, which its pull request does not"),
        ([], "the queue commit leaves out its pull request's change to `README.md`"),
        ([{"filename": "README.md", "patch": "@@ -1 +1 @@\n-hi\n+other"}],
         "the queue commit changes `README.md` otherwise than its pull request"),
    ],
)  # fmt: skip
def test_a_queue_commit_whose_change_is_not_its_pull_requests_is_red(
    queued: list[dict[str, str]], said: str
) -> None:
    api, queue = _queued()
    api.answers[f"/repos/{REPO}/compare/{MAIN}...{QUEUE}"] = {"files": queued}
    assert said in _queue_red(api, queue).output["summary"]


def test_a_change_that_applies_on_a_moved_base_is_still_the_same_change() -> None:
    """The context lines differ when the entry ahead changed the lines around it; the +/- lines
    are what is compared."""
    api, queue = _queued()
    moved = "@@ -4,3 +4,3 @@ a line the entry ahead added\n context\n-hi\n+changed\n context"
    api.answers[f"/repos/{REPO}/compare/{MAIN}...{QUEUE}"] = {
        "files": [{"filename": "README.md", "patch": moved}]
    }
    found = _judge_queue(api, queue)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found


def _queue_changes_a_definition(api: FakeApi) -> str:
    """The queue commit changes a definition; the path its contract runs are listed at."""
    files = {MERGE_PATH: MERGE_YML, "README.md": "changed\n", "tools/ci/verdict.py": "x = 1\n"}
    api.tree(QUEUE, files, parents=(MAIN,))
    return (
        f"/repos/{REPO}/actions/workflows/contract.yml/runs?branch=contract-run%2F{QUEUE}"
        "&event=workflow_dispatch&per_page=100"
    )


def test_a_queue_commit_that_changes_a_definition_waits_on_main_s_contract_over_it() -> None:
    api, queue = _queued()
    runs = _queue_changes_a_definition(api)
    api.answers[runs] = {"total_count": 0, "workflow_runs": []}
    outcome = mc.decide(api, REPO, RUN, CONTROLLER, mc.Queue(queue, APP, True))
    assert outcome.posting is not None and outcome.posting.conclusion == "failure"
    assert mc.WAITING_CONTRACT in outcome.posting.output["summary"]
    assert outcome.dispatch == QUEUE and outcome.base == MAIN


@pytest.mark.parametrize("conclusion", ["success", "failure"])
def test_a_queue_commit_s_contract_run_decides_its_definitions(conclusion: str) -> None:
    api, queue = _queued()
    runs = _queue_changes_a_definition(api)
    run = {"id": 5151, "path": mc.CONTRACT_WORKFLOW, "event": "workflow_dispatch",
           "head_branch": mc.CONTRACT_PREFIX + QUEUE, "head_sha": MAIN,
           "display_title": mc.CONTRACT_TITLE.format(head=QUEUE), "status": "completed",
           "run_attempt": 1, "html_url": "u"}  # fmt: skip
    api.answers[runs] = {"total_count": 1, "workflow_runs": [run]}
    api.answers[f"/repos/{REPO}/actions/runs/5151/attempts/1/jobs?per_page=100&page=1"] = {
        "total_count": 1,
        "jobs": [{"name": "contract", "status": "completed", "conclusion": conclusion}],
    }
    outcome = mc.decide(api, REPO, RUN, CONTROLLER, mc.Queue(queue, APP, True))
    assert outcome.posting is not None
    assert outcome.posting.conclusion == conclusion
    assert outcome.cleanup == mc.CONTRACT_PREFIX + QUEUE
    if conclusion == "success":
        receipt = merge_verdict.read_receipt(mc.check_body(outcome.posting.body()))
        assert receipt.toolchain["definitions"] == QUEUE


def test_a_contract_run_at_an_older_main_tip_does_not_count_for_a_queue_commit() -> None:
    """A rails change merged since the contract ran: its pass is stale (CORE condition 2)."""
    api, queue = _queued()
    runs = _queue_changes_a_definition(api)
    old = {"id": 5152, "path": mc.CONTRACT_WORKFLOW, "event": "workflow_dispatch",
           "head_branch": mc.CONTRACT_PREFIX + QUEUE, "head_sha": AHEAD,
           "display_title": mc.CONTRACT_TITLE.format(head=QUEUE), "status": "completed",
           "conclusion": "success", "run_attempt": 1}  # fmt: skip
    api.answers[runs] = {"total_count": 1, "workflow_runs": [old]}
    outcome = mc.decide(api, REPO, RUN, CONTROLLER, mc.Queue(queue, APP, True))
    assert outcome.posting is not None and outcome.posting.conclusion == "failure"
    assert outcome.dispatch == QUEUE, "it runs again at main's tip"


@pytest.mark.parametrize("final", ["success", "failure"])
def test_a_queue_commit_s_final_verdict_is_never_posted_again(final: str) -> None:
    """CORE condition 1: once a queue commit's verdict waits on nothing, no later look posts on
    it, so a commit already on main cannot be turned red."""
    api, queue = _queued()
    _verdicts(api, QUEUE, {"conclusion": final, "output": {"text": ""}})
    api.jobs(1, ("results", "completed", "failure"))
    assert "never posted again" in str(_judge_queue(api, queue))


def test_a_queue_commit_whose_verdict_waits_is_judged_again() -> None:
    api, queue = _queued()
    waits = {"text": f"```{mc.WAITING_FENCE}\ncontract\n```\n"}
    _verdicts(api, QUEUE, {"conclusion": "failure", "output": waits})
    found = _judge_queue(api, queue)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found


def test_a_queue_commit_s_red_run_is_red() -> None:
    api, queue = _queued()
    api.jobs(1, ("results", "completed", "failure"))
    assert "attempt 1's `results` is 'failure'" in _queue_red(api, queue).output["summary"]


def test_a_queue_commit_s_attempt_1_red_for_jobs_no_runner_took_is_never_excused() -> None:
    """A queue commit's attempt-1 red is its posted verdict, so its entry is queued again."""
    api, queue = _queued()
    api.answers[f"/repos/{REPO}/actions/runs/{RUN}"]["run_attempt"] = 2
    _attempt_1(api)
    _untaken(api, 1, NOTICE)
    api.jobs(2, ("results", "completed", "success"))
    found = _queue_red(api, queue)
    assert "attempt 1's `results` is 'failure'" in found.output["summary"]


@pytest.mark.parametrize("live", [True, False])
def test_under_the_queue_a_head_behind_main_is_not_red_for_being_behind(live: bool) -> None:
    api = FakeApi()
    api.answers[f"/repos/{REPO}/compare/{MAIN}...{HEAD}"] = {
        "behind_by": 3,
        "merge_base_commit": {"sha": AHEAD},
    }
    api.tree(AHEAD, {MERGE_PATH: MERGE_YML, "README.md": "hi\n"})
    outcome = mc.decide(api, REPO, RUN, CONTROLLER, mc.Queue(FakeQueue(), APP, live))
    assert outcome.posting is not None
    if live:
        assert outcome.posting.conclusion == "success", outcome.posting.output
        assert "the merge queue tests it on before it lands" in outcome.posting.output["summary"]
    else:
        assert outcome.posting.conclusion == "failure"
        assert "3 commits behind main" in outcome.posting.output["title"]


def test_a_queue_commit_is_judged_again_from_its_own_queue_run() -> None:
    api, queue = _queued()
    _merge_runs(api, f"head_sha={QUEUE}&event=pull_request&status=completed&per_page=100", [])
    _merge_runs(api, f"head_sha={QUEUE}&event=merge_group&status=completed&per_page=100", [RUN])
    outcome = mc.wake(api, REPO, QUEUE, CONTROLLER, mc.Queue(queue, APP, True))
    assert outcome.posting is not None and outcome.posting.head_sha == QUEUE
    assert outcome.posting.conclusion == "success"


@pytest.mark.parametrize("live", [True, False])
def test_the_sweep_judges_a_waiting_queue_commit_again_once_merges_are_queued(live: bool) -> None:
    api, queue = _queued()
    waits = {"text": f"```{mc.WAITING_FENCE}\ncontract\n```\n"}
    _verdicts(api, QUEUE, {"conclusion": "failure", "output": waits})
    _merge_runs(api, f"head_sha={QUEUE}&event=pull_request&status=completed&per_page=100", [])
    _merge_runs(api, f"head_sha={QUEUE}&event=merge_group&status=completed&per_page=100", [RUN])
    changed = mc.judge_waiting(
        api, REPO, APP, CONTROLLER, None, limit=3, queue=mc.Queue(queue, APP, live)
    )
    posted = [o.posting for o in changed if o.posting is not None]
    if live:
        assert [(p.head_sha, p.conclusion) for p in posted] == [(QUEUE, "success")]
    else:
        assert posted == [], "before the flip there is no queue to sweep"


@pytest.mark.parametrize(
    "rules, live",
    [
        ([{"type": "pull_request"}, {"type": "merge_queue"}], True),
        ([{"type": "pull_request"}, {"type": "required_status_checks"}], False),
        ({"message": "Not Found"}, False),
        (None, False),
    ],
    ids=["a merge queue rule", "none", "an answer of another shape", "no answer"],
)
def test_whether_merges_are_queued_is_read_from_main_s_rules(rules: Any, live: bool) -> None:
    """Exactly when GitHub queues main's merges; an answer it cannot read keeps rule 3."""
    api = FakeApi()
    if rules is not None:
        api.answers[f"/repos/{REPO}/rules/branches/main?per_page=100"] = rules
    assert mc.queue_live(api, REPO) is live


# ── The retry budget (0.4.0 A5, R1) ─────────────────────────────────────────────────────────
#
# A red pull request verdict says which retry of its change it is and whether its red is
# flake-class, and why. The note changes no conclusion. Each case is one planted red: a suite
# shard failed, its results job named the failed test, and the pull request's earlier heads and
# the open flaky-test issues answer one GraphQL read.

FLAKY_TEST = "tests/test_timing.py::test_it_answers_in_time"
EARLIER = "8" * 40
OTHER_HEAD = "7" * 40
SHARD = "python-suite (1/3)"
RESULTS_RED = "attempt 1's `results` is 'failure'"


class FakeRetry:
    """The pull request's timeline (oldest first: a force-push as its earlier head and that
    head's tree, a removal from main's merge queue as its reason) and the open flaky-test issues,
    as the retry note's one GraphQL read answers."""

    def __init__(
        self,
        heads: tuple[tuple[str, str], ...] = (),
        flaky: tuple[str, ...] = (FLAKY_TEST,),
        *,
        timeline: tuple[dict[str, Any], ...] | None = None,
        **issue: Any,
    ) -> None:
        self.timeline = (
            [{"__typename": "HeadRefForcePushedEvent",
              "beforeCommit": {"oid": s, "tree": {"oid": t}}} for s, t in heads]
            if timeline is None
            else list(timeline)
        )  # fmt: skip
        self.flaky, self.issue = flaky, issue
        self.calls = 0

    def __call__(self, text: str, variables: Mapping[str, object]) -> Any:
        assert "HeadRefForcePushedEvent" in text and "RemovedFromMergeQueueEvent" in text
        assert variables == {
            "owner": "owner",
            "name": "project",
            "number": 12,
            "label": "flaky-test",
            "heads": mc.RETRY_HEADS,
        }
        self.calls += 1
        author = {"login": "github-actions", "__typename": "Bot"}
        issues = [
            {"title": mc.flaky_title(f), "author": author, "authorAssociation": "NONE",
             **self.issue}
            for f in self.flaky
        ]  # fmt: skip
        return {"data": {"repository": {
            "pullRequest": {"headRefOid": HEAD, "timelineItems": {"nodes": self.timeline}},
            "issues": {"totalCount": len(issues), "nodes": issues},
        }}}  # fmt: skip


def _pushed(sha: str, tree: str) -> dict[str, Any]:
    return {
        "__typename": "HeadRefForcePushedEvent",
        "beforeCommit": {"oid": sha, "tree": {"oid": tree}},
    }


def _removed(reason: str = "failed_checks") -> dict[str, Any]:
    return {"__typename": "RemovedFromMergeQueueEvent", "reason": reason}


def _failed(test: str = FLAKY_TEST, key: str = SHARD, standing: str = "flake") -> str:
    return json.dumps({"key": key, "attempt": 1, "test": test, "standing": standing})


def _counted(*notes: str, count: int | None = None) -> tuple[str, ...]:
    """The results job's failed-test notices: the count first (by default, how many follow)."""
    return (json.dumps({"failed": len(notes) if count is None else count}), *notes)


def _retried(
    *jobs: tuple[str, str, str | None], notes: tuple[str, ...] = _counted(_failed())
) -> FakeApi:
    """A red pull request run: by default one suite shard failed with one flaky test named."""
    api = FakeApi()
    api.run(event="pull_request", pull_requests=[{"number": 12, "head": {"sha": HEAD}}])
    api.jobs(1, *(jobs or ((SHARD, "completed", "failure"),)), ("results", "completed", "failure"))
    api.flakes(1, *notes, title="failed test")
    files = [{"filename": "README.md", "status": "modified", "patch": README_PATCH}]
    api.answers[f"/repos/{REPO}/compare/main...{HEAD}"] = {"files": files}
    return api


def _note(api: FakeApi, retry: FakeRetry | None = None) -> mc.Posting:
    outcome = mc.decide(api, REPO, RUN, CONTROLLER, mc.Queue(retry or FakeRetry(), APP, False))
    found = outcome.posting
    assert isinstance(found, mc.Posting) and found.conclusion == "failure", outcome
    return found


def _earlier(api: FakeApi, sha: str, conclusion: str = "failure", **check: Any) -> str:
    """An earlier head with its newest verdict; its tree, the head's."""
    _verdicts(api, sha, {"conclusion": conclusion, **check})
    return api_tree_oid(HEAD, api)


def test_a_first_flake_class_red_says_retry_0_of_2_and_why() -> None:
    found = _note(_retried())
    assert found.output["title"] == f"Red (retry 0 of 2, flake-class): {RESULTS_RED}"
    summary = found.output["summary"]
    assert "- retry 0 of 2\n" in summary
    assert "- flake-class: 1 failed test(s), each with an open flaky-test issue" in summary


def test_a_version_5_flake_that_first_timed_out_is_still_flake_class() -> None:
    """job-record/5 says how each rerun test first failed. The notices the controller reads keep
    their keys and the test's standing, so a flake that first timed out is flake-class as
    before (verdict.failed_notices, read here as the results job writes them)."""
    rerun = {
        "argv": ["uv", "run", "pytest", "-n", "0"],
        "exit": 1,
        "tests": [{"test": FLAKY_TEST, "rerun_result": verdict.FLAKE, "why": "passed alone"}],
    }
    record = json.loads(
        verdict.job_record(
            key=SHARD,
            result=verdict.FAILURE,
            identity=verdict.Identity(HEAD, {}),
            steps=[(["uv", "run", "pytest"], 1, 1.0)],
            rerun=rerun,
            unstarted_steps=0,
        )
    )
    record["schema"] = "personalclaw.ci.job-record/5"
    record["rerun"]["tests"][0]["first_failure"] = verdict.TIMED_OUT
    results = verdict.from_job_records([json.dumps(record)])
    judged = verdict.decide([SHARD], results, verdict.Identity(HEAD, {}))
    notes = tuple(line.split("::", 2)[2] for line in verdict.failed_notices(judged).splitlines())
    assert notes == _counted(_failed()), "the notices are as a version-4 record's"
    found = _note(_retried(notes=notes))
    assert found.output["title"] == f"Red (retry 0 of 2, flake-class): {RESULTS_RED}"


def test_an_earlier_red_head_with_the_same_tree_is_a_retry() -> None:
    api = _retried()
    tree = _earlier(api, EARLIER)
    found = _note(api, FakeRetry(((EARLIER, tree),)))
    assert found.output["title"].startswith("Red (retry 1 of 2, flake-class): ")


def test_an_earlier_red_head_with_the_same_lines_is_a_retry_and_two_spend_the_budget() -> None:
    api = _retried()
    tree = _earlier(api, EARLIER)
    _earlier(api, OTHER_HEAD)
    same = [{"filename": "README.md", "status": "modified", "patch": README_PATCH}]
    api.answers[f"/repos/{REPO}/compare/main...{OTHER_HEAD}"] = {"files": same}
    found = _note(api, FakeRetry(((OTHER_HEAD, "6" * 40), (EARLIER, tree))))
    assert found.output["title"].startswith("Red (retry budget spent, flake-class): ")
    assert (
        "- retry budget spent: 2 retries of this change were red, so a person looks"
        " (quarantine, fix or infrastructure)" in found.output["summary"]
    )


@pytest.mark.parametrize(
    "conclusion, check, patch",
    [
        pytest.param("failure", {}, "@@ -1 +1 @@\n-hi\n+other", id="another-change"),
        pytest.param("success", {}, README_PATCH, id="a-green-head"),
        pytest.param(
            "failure",
            {"output": {"text": f"```{mc.WAITING_FENCE}\ncodeql\n```\n"}},
            README_PATCH,
            id="a-red-that-waited",
        ),
    ],
)
def test_an_earlier_head_that_is_no_red_retry_of_this_change_is_not_counted(
    conclusion: str, check: dict, patch: str
) -> None:
    api = _retried()
    _verdicts(api, OTHER_HEAD, {"conclusion": conclusion, **check})
    files = [{"filename": "README.md", "status": "modified", "patch": patch}]
    api.answers[f"/repos/{REPO}/compare/main...{OTHER_HEAD}"] = {"files": files}
    found = _note(api, FakeRetry(((OTHER_HEAD, "6" * 40),)))
    assert found.output["title"].startswith("Red (retry 0 of 2, flake-class): ")


def test_a_rerun_attempt_is_a_retry() -> None:
    api = _retried()
    api.run(
        event="pull_request", run_attempt=2, pull_requests=[{"number": 12, "head": {"sha": HEAD}}]
    )
    api.jobs(2, (SHARD, "completed", "failure"), ("results", "completed", "failure"))
    api.flakes(2, *_counted(_failed()), title="failed test")
    found = _note(api)
    assert found.output["title"].startswith("Red (retry 1 of 2, flake-class): ")


def test_each_removal_of_this_head_from_the_queue_for_failed_checks_is_a_retry() -> None:
    api = _retried()
    tree = _earlier(api, EARLIER, "success")
    timeline = (
        _removed(),  # of the earlier head: not this head's
        _pushed(EARLIER, tree),
        _removed(),
        _removed("manually_removed"),
        _removed("failed_checks"),
    )
    found = _note(api, FakeRetry(timeline=timeline))
    assert found.output["title"].startswith("Red (retry budget spent, flake-class): ")


def test_a_cancelled_or_timed_out_shard_with_no_failed_test_is_flake_class() -> None:
    found = _note(_retried((SHARD, "completed", "cancelled"), notes=_counted()))
    assert "- flake-class: 1 test job(s) cancelled or timed out" in found.output["summary"]


@pytest.mark.parametrize(
    "jobs, notes, flaky, says",
    [
        pytest.param((), _counted(_failed()), (), "has no flaky history", id="no-flaky-issue"),
        pytest.param(
            (), _counted(_failed("tests/test_readme.py::test_it")),
            ("tests/test_readme.py::test_it",),
            "reads `tests/test_readme.py`, which the pull request changes",
            id="reads-a-changed-file",
        ),
        pytest.param(
            (), _counted(_failed(standing="not classified")), (FLAKY_TEST,),
            "is not classified", id="no-rerun-classified-it",
        ),
        pytest.param(
            (), _counted(_failed(standing="quarantined")), (FLAKY_TEST,),
            "is quarantined", id="a-quarantined-failure-the-change-reads",
        ),
        pytest.param(
            (("rails (1/3)", "completed", "failure"),), _counted(_failed(key="rails (1/3)")),
            (FLAKY_TEST,), "only a test job's red is flake-class", id="a-rail",
        ),
        pytest.param(
            ((SHARD, "completed", "failure"),), _counted(), (FLAKY_TEST,),
            "failed with no failed test named", id="a-failure-naming-no-test",
        ),
        pytest.param(
            (), _counted(_failed(), count=11), (FLAKY_TEST,),
            "its results job names 1 of its 11 failed tests", id="fewer-named-than-counted",
        ),
        pytest.param(
            (), (_failed(),), (FLAKY_TEST,),
            "names no one count of its failed tests", id="no-count",
        ),
        pytest.param(
            (), (*_counted(_failed()), json.dumps({"failed": 1})), (FLAKY_TEST,),
            "names no one count of its failed tests", id="two-counts",
        ),
        pytest.param(
            (), ("not json",), (FLAKY_TEST,), "not one the verdict writes", id="an-odd-notice",
        ),
    ],
)  # fmt: skip
def test_a_red_that_is_not_flake_class_says_why(
    jobs: tuple, notes: tuple[str, ...], flaky: tuple[str, ...], says: str
) -> None:
    api = _retried(*jobs, notes=notes)
    if "test_readme" in says:
        files = [{"filename": "tests/test_readme.py", "status": "modified", "patch": "@@ -1 +1 @@"}]
        api.answers[f"/repos/{REPO}/compare/main...{HEAD}"] = {"files": files}
    found = _note(api, FakeRetry(flaky=flaky))
    assert found.output["title"].startswith("Red (retry 0 of 2): ")
    assert "- not flake-class: " in found.output["summary"]
    assert says in found.output["summary"], found.output["summary"]


def test_a_red_with_a_reason_beside_its_tests_is_not_flake_class() -> None:
    api = _retried()
    api.sign_off(HEAD, ("completed", "failure"))
    found = _note(api)
    assert "- not flake-class: its red is not its tests' alone: " in found.output["summary"]


@pytest.mark.parametrize(
    "author, association, counted",
    [
        pytest.param({"login": "someone", "__typename": "User"}, "NONE", False, id="anyone"),
        pytest.param(
            {"login": "someone", "__typename": "User"}, "CONTRIBUTOR", False, id="a-contributor"
        ),
        pytest.param({"login": "github-actions", "__typename": "User"}, "NONE", False,
                     id="a-user-named-as-the-bot"),
        pytest.param({"login": "owner", "__typename": "User"}, "OWNER", True, id="the-owner"),
        pytest.param({"login": "m", "__typename": "User"}, "MEMBER", True, id="a-member"),
        pytest.param({"login": "c", "__typename": "User"}, "COLLABORATOR", True,
                     id="a-collaborator"),
    ],
)  # fmt: skip
def test_a_flaky_issue_counts_only_when_the_workflow_or_a_maintainer_filed_it(
    author: dict, association: str, counted: bool
) -> None:
    retry = FakeRetry(author=author, authorAssociation=association)
    summary = _note(_retried(), retry).output["summary"]
    assert ("- flake-class: " in summary) is counted, summary
    assert ("has no flaky history" in summary) is not counted


def test_two_entries_listing_one_queue_commit_post_a_named_red() -> None:
    api, queue = _queued()
    queue.entries.append(_entry(QUEUE, MAIN, 12, position=2))
    found = _queue_red(api, queue)
    assert "main's merge queue lists 2 entries for the one commit" in found.output["title"]


def test_the_note_changes_no_conclusion_and_a_green_reads_nothing_for_it() -> None:
    api = FakeApi()
    api.run(event="pull_request", pull_requests=[{"number": 12, "head": {"sha": HEAD}}])
    retry = FakeRetry()
    outcome = mc.decide(api, REPO, RUN, CONTROLLER, mc.Queue(retry, APP, False))
    assert outcome.posting is not None and outcome.posting.conclusion == "success"
    assert retry.calls == 0 and "retry" not in outcome.posting.output["summary"]


def test_a_run_naming_no_pull_request_says_its_retries_are_not_counted() -> None:
    api = _retried()
    api.run(event="pull_request", pull_requests=[])
    found = _note(api)
    assert found.output["title"] == f"Red: {RESULTS_RED}"
    assert "- retries not counted: the run names no pull request" in found.output["summary"]


def test_an_unread_answer_leaves_the_count_unread_and_the_red_as_it_is() -> None:
    def broken(text: str, variables: Mapping[str, object]) -> Any:
        raise OSError("HTTP 502")

    api = _retried()
    outcome = mc.decide(api, REPO, RUN, CONTROLLER, mc.Queue(broken, APP, False))
    assert outcome.posting is not None and outcome.posting.conclusion == "failure"
    assert outcome.posting.output["title"] == f"Red: {RESULTS_RED}"
    assert "- retries not counted: unread: " in outcome.posting.output["summary"]


def test_a_red_that_only_waits_carries_no_note() -> None:
    api = FakeApi()
    api.run(event="pull_request", pull_requests=[{"number": 12, "head": {"sha": HEAD}}])
    api.codeql(HEAD, status="in_progress")
    retry = FakeRetry()
    found = _note(api, retry)
    assert retry.calls == 0 and "retr" not in found.output["summary"]


def test_the_flaky_title_is_flaky_tests_own() -> None:
    from tools.ci import flaky_issues

    for test in (FLAKY_TEST, "tests/test_long.py::test_" + "x" * 300):
        assert mc.flaky_title(test) == flaky_issues.title(test)
    assert (mc.FLAKY_LABEL, mc.FLAKY_PREFIX) == (flaky_issues.LABEL, flaky_issues.PREFIX)


# ── A queue commit's red carries the retry note too (R2's re-enqueue reads it) ────────────────


def _queue_red_run(
    notes: tuple[str, ...] = _counted(_failed()), retry: FakeRetry | None = None
) -> tuple[FakeApi, mc.Queue]:
    """#12's queue commit, its run red in one suite shard naming one flaky test; the queue's read
    and the retry note's, each answered by its own fake."""
    api, entries = _queued()
    api.jobs(1, (SHARD, "completed", "failure"), ("results", "completed", "failure"))
    api.flakes(1, *notes, title="failed test")
    answers = retry or FakeRetry()

    def query(text: str, variables: Mapping[str, object]) -> Any:
        return (
            answers(text, variables)
            if "HeadRefForcePushedEvent" in text
            else entries(text, variables)
        )

    return api, mc.Queue(query, APP, True)


def test_a_queue_commit_s_red_counts_its_removals_and_says_it_is_flake_class() -> None:
    api, queue = _queue_red_run(retry=FakeRetry(timeline=(_removed(),)))
    outcome = mc.decide(api, REPO, RUN, CONTROLLER, queue)
    found = outcome.posting
    assert found is not None and found.conclusion == "failure" and found.head_sha == QUEUE
    assert found.output["title"].startswith("Red (retry 1 of 2, flake-class): ")
    assert "- flake-class: 1 failed test(s)" in found.output["summary"]


def test_a_queue_commit_s_failed_test_that_reads_its_change_is_not_flake_class() -> None:
    """The queue commit's change against its parent is what its tests ran on: a failed test whose
    own file it changes is not flake-class."""
    api, queue = _queue_red_run()
    files = [
        {"filename": "README.md", "status": "modified", "patch": README_PATCH, "sha": "1" * 40},
        {"filename": "tests/test_timing.py", "status": "modified", "patch": "@@ -1 +1 @@\n+x"},
    ]
    api.answers[f"/repos/{REPO}/compare/{MAIN}...{QUEUE}"] = {"files": files}
    api.answers[f"/repos/{REPO}/compare/main...{HEAD}"] = {"files": files}
    found = mc.decide(api, REPO, RUN, CONTROLLER, queue).posting
    assert found is not None and found.output["title"].startswith("Red (retry 0 of 2): ")
    assert "reads `tests/test_timing.py`, which the pull request changes" in found.output["summary"]


def test_a_queue_commit_s_note_it_cannot_read_leaves_its_red_as_it_is() -> None:
    api, queue = _queue_red_run()
    del api.answers[f"/repos/{REPO}/git/commits/{HEAD}"]
    found = mc.decide(api, REPO, RUN, CONTROLLER, queue).posting
    assert found is not None and found.conclusion == "failure"
    assert found.output["title"].startswith("Red: ")
    assert "- retries not counted: unread: " in found.output["summary"]


# ── The gates (rule 5b): a workflow of its own that a pull request's paths start ─────────────
#
# Each gate's pull_request runs on the head are read always, every one must pass on every
# attempt, and main's copy of its filter decides only whether no run is red. The class this
# closes: a head red on a gate beside a green Merge run is red.

NIX_RUN = f"https://github.com/{REPO}/actions/runs/5000"
NIX_RUNS = f"/repos/{REPO}/actions/workflows/nix.yml/runs?head_sha={HEAD}&event=pull_request"
NIX_PATHS = GATE_FILTERS["nix.yml"]


def _gated(path: str = "uv.lock", text: str = "changed\n", api: FakeApi | None = None) -> FakeApi:
    """A head that changes one path a gate's filter names, and nothing that defines CI."""
    api = api or FakeApi()
    api.tree(HEAD, {MERGE_PATH: MERGE_YML, "README.md": "hi\n", path: text})
    return api


def _nix(*jobs: tuple[str, str | None, str], **run: Any) -> dict[str, Any]:
    """One Nix run whose attempts are each the jobs given (by default one green `flake`)."""
    return {"attempts": [list(jobs)] if jobs else [[("flake", "success", "")]], **run}


def _title(api: FakeApi, queue: mc.Queue | None = None) -> str:
    found = mc.decide(api, REPO, RUN, CONTROLLER, queue).posting
    assert found is not None, "every judged head posts"
    return found.output["title"]


def test_a_head_that_changes_no_gated_path_is_green_and_says_no_gate_was_expected() -> None:
    api = FakeApi()
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success"
    for gate in workflows.GATES:
        assert f"- no {gate.what} ({gate.file}) was expected: it changes no path main's" in (
            found.output["summary"]
        )
        # Its runs are read all the same: one listing a gate, whatever main's filter says.
        listing = f"/repos/{REPO}/actions/workflows/{gate.file}/runs?head_sha={HEAD}"
        assert sum(c.startswith(listing) for c in api.calls) == 1, gate.file


@pytest.mark.parametrize("path", NIX_PATHS)
def test_a_head_that_changes_a_nix_path_and_has_no_nix_build_is_red_not_waiting(path: str) -> None:
    path = path.replace("**", "python.nix")
    api = _gated(path)
    for file, paths in GATE_FILTERS.items():
        if file != "nix.yml" and workflows.filter_matches(paths, path):
            api.gate(file, {})
    found = _red(api)
    assert found.output["title"] == (
        f"Red: the head changes `{path}`, which main's nix.yml runs on, and has no Nix build"
        " (nix.yml) run on pull_request: its own copy's filter may miss it, and a diff of more"
        " than 300 files starts none"
    )
    assert "text" not in found.output, "a missing run waits on nothing"


@pytest.mark.parametrize("status", ["in_progress", "queued", "waiting"])
def test_a_nix_build_still_going_is_a_red_that_waits_on_it(status: str) -> None:
    api = _gated()
    api.gate("nix.yml", _nix(("flake", None, ""), status=status, conclusion=None))
    found = _red(api)
    assert found.output["title"] == (
        f"Red: waiting: the head's Nix build (nix.yml) is {status!r}, not finished: run {NIX_RUN}"
    )
    assert found.output["text"] == f"```{mc.WAITING_FENCE}\nnix.yml\n```\n"


@pytest.mark.parametrize(
    ("jobs", "run", "says"),
    [
        pytest.param(
            [("flake", "failure", "The next release builds on the same package set")],
            {"conclusion": "failure"},
            "attempt 1's `flake` is 'failure' at `The next release builds on the same package set`",
            id="a-build-step",
        ),
        pytest.param(
            [("flake", "cancelled", "")],
            {"conclusion": "cancelled"},
            "attempt 1's `flake` is 'cancelled'",
            id="cancelled",
        ),
        pytest.param(
            [("flake", "timed_out", "Build the package and run the flake's checks")],
            {"conclusion": "timed_out"},
            "attempt 1's `flake` is 'timed_out' at `Build the package and run the flake's checks`",
            # A 'timed_out' conclusion GitHub may send; it sends a job that reaches its bound as
            # 'cancelled', which test_a_nix_job_cancelled_at_its_bound_... reads.
            id="timed-out-as-github-may-send-it",
        ),
        pytest.param(
            [("flake", "skipped", "")],
            {"conclusion": "success"},
            "attempt 1's `flake` is 'skipped'",
            id="skipped",
        ),
        pytest.param(
            [("other", "success", "")],
            {"conclusion": "success"},
            "attempt 1 has 0 `flake` jobs, not one",
            id="missing",
        ),
        pytest.param(
            [("flake", "success", ""), ("flake", "success", "")],
            {"conclusion": "success"},
            "attempt 1 has 2 `flake` jobs, not one",
            id="twice",
        ),
        pytest.param(
            [("flake", "success", ""), ("extra", "failure", "Run it")],
            {"conclusion": "failure"},
            "attempt 1's `extra` is 'failure' at `Run it`",
            id="another-job-red",
        ),
        pytest.param(
            [],
            {"conclusion": "startup_failure"},
            "attempt 1 ran no job, and the run is 'startup_failure'",
            id="job-less",
        ),
        pytest.param(
            [("flake", "success", "")],
            {"conclusion": "failure"},
            "the run is 'failure'",
            id="green-jobs-red-run",
        ),
    ],
)
def test_a_nix_build_that_did_not_pass_is_red(
    jobs: list[tuple[str, str | None, str]], run: dict[str, Any], says: str
) -> None:
    api = _gated()
    api.gate("nix.yml", {"attempts": [jobs], **run})
    title = _red(api).output["title"]
    assert title == f"Red: the head's Nix build (nix.yml) is red: {says} (run {NIX_RUN})"


def test_a_red_nix_build_beside_a_green_merge_run_is_red() -> None:
    """A head whose own pyproject change sets a bound the package set cannot meet: every Merge
    job green, DCO, review and CodeQL green, and its Nix run red at the next-release step. The
    verdict read none of it and was green; now it is red."""
    api = _gated("pyproject.toml", '[project]\ndependencies = ["example>=9.9,<10"]\n')
    api.gate(
        "nix.yml", _nix(("flake", "failure", "The next release builds on the same package set"))
    )
    found = _red(api)
    assert "the head's Nix build (nix.yml) is red" in found.output["title"]
    assert found.output["summary"].count("- the head's Nix build") == 1


def test_a_nix_red_on_attempt_1_is_never_rerun_into_green() -> None:
    api = _gated()
    api.gate(
        "nix.yml",
        {"attempts": [[("flake", "failure", "Build it")], [("flake", "success", "")]]},
    )
    assert _title(api) == (
        f"Red: the head's Nix build (nix.yml) is red: attempt 1's `flake` is 'failure' at"
        f" `Build it` (run {NIX_RUN})"
    )


def test_any_red_nix_run_on_the_head_is_red_not_only_the_newest() -> None:
    """A pull request closed and reopened gets a new run on the same head: an older red one
    still counts, so a reopen can't turn a red into a green."""
    api = _gated()
    api.gate("nix.yml", _nix(("flake", "failure", "Build it"), conclusion="failure"), _nix())
    assert f"`flake` is 'failure' at `Build it` (run {NIX_RUN})" in _title(api)


def test_an_older_run_red_for_more_than_a_cancel_is_red_beside_a_green_newer_run() -> None:
    """Only a run a newer one cancelled is set aside: an older run GitHub could not start is red."""
    api = _gated()
    api.gate("nix.yml", {"conclusion": "startup_failure", "attempts": [[]]}, _nix())
    assert "attempt 1 ran no job, and the run is 'startup_failure'" in _title(api), _title(api)


def test_a_nix_run_cancelled_by_a_newer_one_on_the_head_is_set_aside() -> None:
    api = _gated()
    api.gate("nix.yml", _nix(("flake", "cancelled", ""), conclusion="cancelled"), _nix())
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    assert f"- its Nix build (nix.yml) passed on every attempt: run {NIX_RUN[:-1]}1" in (
        found.output["summary"]
    )


@pytest.mark.parametrize(
    "run",
    [
        pytest.param({"path": WF + "fake-nix.yml"}, id="another-workflow-named-Nix"),
        pytest.param({"event": "workflow_dispatch"}, id="a-dispatch"),
        pytest.param({"event": "push"}, id="a-push"),
        pytest.param({"head_sha": "9" * 40}, id="an-older-head"),
    ],
)
def test_a_nix_run_that_is_not_mains_on_this_head_counts_for_nothing(run: dict[str, Any]) -> None:
    api = _gated()
    api.gate("nix.yml", _nix(**run))
    assert "and has no Nix build (nix.yml) run on pull_request" in _title(api)


def test_a_red_nix_run_on_the_head_counts_though_main_s_filter_expects_none() -> None:
    """The head's own filter started a run main's would not: it is held to it all the same."""
    api = FakeApi()
    api.gate("nix.yml", _nix(("flake", "failure", "Build it"), conclusion="failure"))
    assert "the head's Nix build (nix.yml) is red" in _title(api)


def test_whether_a_head_needs_a_nix_build_is_main_s_filter() -> None:
    api = _gated()
    assert "has no Nix build (nix.yml) run" in _title(api)
    without = [p for p in NIX_PATHS if p != "uv.lock"]
    api.tree(MAIN, {GATE["nix.yml"].path: gate_yml("nix.yml", without)})
    api.tree(HEAD, {GATE["nix.yml"].path: gate_yml("nix.yml", without), "uv.lock": "changed\n"})
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    assert "- no Nix build (nix.yml) was expected" in found.output["summary"]


def test_a_head_that_narrows_its_own_nix_filter_still_needs_a_nix_build() -> None:
    """The head's nix.yml names only flake.nix, so it starts no run for its uv.lock change, nor
    for its own nix.yml: main's filter names both, so the head is red."""
    api = _changes(FakeApi(), GATE["nix.yml"].path, gate_yml("nix.yml", ["flake.nix"]))
    api.tree(
        HEAD,
        {
            MERGE_PATH: MERGE_YML,
            "README.md": "hi\n",
            GATE["nix.yml"].path: gate_yml("nix.yml", ["flake.nix"]),
            "uv.lock": "changed\n",
        },
    )
    api.gate("nix.yml")
    _contract(api, {})
    assert _title(api).startswith(
        "Red: the head changes `.github/workflows/nix.yml`, `uv.lock`, which main's nix.yml runs"
        " on, and has no Nix build (nix.yml) run on pull_request"
    )


@pytest.mark.parametrize(
    ("paths", "pull_request", "says"),
    [
        pytest.param(
            ["nix/[ab].nix"],
            {},
            "names a filter pattern this does not read: 'nix/[ab].nix'",
            id="a-set",
        ),
        pytest.param(
            ["flake.ni?"],
            {},
            "names a filter pattern this does not read: 'flake.ni?'",
            id="a-question-mark",
        ),
        pytest.param(
            ["**/flake.nix"],
            {},
            "names a filter pattern this does not read: '**/flake.nix'",
            id="a-leading-globstar",
        ),
        pytest.param(
            NIX_PATHS,
            {"paths-ignore": ["docs/**"]},
            "filters pull_request by paths-ignore",
            id="paths-ignore",
        ),
        pytest.param(
            NIX_PATHS, {"branches": ["main"]}, "filters pull_request by branches", id="branches"
        ),
        pytest.param(NIX_PATHS, {"types": ["opened"]}, "filters pull_request by types", id="types"),
        pytest.param([], {}, "has no pull_request paths list", id="no-paths"),
    ],
)
def test_a_filter_the_matcher_does_not_read_is_unread(
    paths: list[str], pull_request: dict[str, Any], says: str
) -> None:
    api = FakeApi()
    api.tree(MAIN, {GATE["nix.yml"].path: gate_yml("nix.yml", paths, **pull_request)})
    api.tree(HEAD, {GATE["nix.yml"].path: gate_yml("nix.yml", paths, **pull_request)})
    assert _title(api).startswith(f"Red: unread: main's nix.yml {says}")


def test_main_without_a_gated_workflow_is_unread() -> None:
    api = FakeApi()
    files = {p: t for p, t in GATE_FILES.items() if p != GATE["release-image-tooling.yml"].path}
    api.tree(MAIN, {MERGE_PATH: MERGE_YML, "README.md": "hi\n", **files}, gates=False)
    api.tree(HEAD, {MERGE_PATH: MERGE_YML, "README.md": "changed\n", **files}, gates=False)
    assert _title(api) == (
        "Red: unread: main has no .github/workflows/release-image-tooling.yml, which tools/ci's"
        " GATES names"
    )


def test_a_green_names_each_gate_run_in_its_summary_and_receipt() -> None:
    api = _gated()
    api.gate("nix.yml", _nix())
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    assert f"- its Nix build (nix.yml) passed on every attempt: run {NIX_RUN}" in (
        found.output["summary"]
    )
    receipt = merge_verdict.read_receipt(mc.check_body(found.body()))
    assert receipt.toolchain == {"runner": "ubuntu-24.04", "definitions": MAIN, "nix.yml": NIX_RUN}


@pytest.mark.parametrize(
    ("file", "path"),
    [
        ("action-pins.yml", ACTIONS + "setup/action.yml"),
        ("action-pins.yml", "tools/ci/action_pins.py"),
        ("release-image-tooling.yml", "tools/ci/oci_layout.py"),
        ("release-image-tooling.yml", WF + "release.yml"),
    ],
)
def test_each_gate_is_expected_by_its_own_filter(file: str, path: str) -> None:
    api = _changes(FakeApi(), path)
    api.gate(file)
    _contract(api, {})
    title = _title(api)
    assert f"and has no {GATE[file].what} ({file}) run on pull_request" in title, title


GATE_RUN = 5100


@pytest.mark.parametrize("file", list(GATE_FILTERS))
def test_a_completed_gate_run_judges_its_head_again_from_its_own_merge_run(file: str) -> None:
    api = _gated()
    _merge_runs(api, OWN_MERGE, [RUN])
    run = {
        "repository": {"full_name": REPO},
        "path": GATE[file].path,
        "event": "pull_request",
        "status": "completed",
        "head_sha": HEAD,
    }
    api.answers[f"/repos/{REPO}/actions/runs/{GATE_RUN}"] = run
    api.gate("nix.yml", _nix(("flake", "failure", "Build it"), conclusion="failure"))
    outcome = mc.judge(api, REPO, GATE_RUN, CONTROLLER)
    assert outcome.posting is not None and outcome.posting.head_sha == HEAD
    assert "the head's Nix build (nix.yml) is red" in outcome.posting.output["title"]
    for other, why in (
        ({"event": "workflow_dispatch"}, "not a completed pull request run of a gate"),
        ({"event": "push"}, "not a completed pull request run of a gate"),
        ({"status": "in_progress"}, "not a completed pull request run of a gate"),
        ({"repository": {"full_name": "someone/fork"}}, "is of 'someone/fork'"),
    ):
        api.answers[f"/repos/{REPO}/actions/runs/{GATE_RUN}"] = {**run, **other}
        outcome = mc.judge(api, REPO, GATE_RUN, CONTROLLER)
        assert outcome.posting is None and why in outcome.why, (other, outcome.why)


def test_a_head_waiting_on_codeql_and_nix_names_both_in_its_fence() -> None:
    api = _gated()
    api.codeql(HEAD, pull_alerts=(), processed=("actions",))
    api.gate("nix.yml", _nix(("flake", None, ""), status="in_progress", conclusion=None))
    found = _red(api)
    assert found.output["text"] == f"```{mc.WAITING_FENCE}\ncodeql\nnix.yml\n```\n"


def test_each_gate_s_wait_has_its_own_fence_word() -> None:
    for gate in workflows.GATES:
        reason = f"{mc.WAITING_GATE}{gate.what} ({gate.file}) is 'queued', not finished: run x"
        assert mc._fence_word(reason) == gate.file
    assert mc._fence_word(f"{mc.WAITING_CONTRACT}is started") == "contract"
    assert mc._fence_word(f"{mc.WAITING}CodeQL has not analysed the head yet") == "codeql"


# The retry note on a gate's red: flake-class only when every red job of it was cancelled or
# failed at a step that fetches only outside inputs.


def _gate_red(*jobs: tuple[str, str | None, str], results: str = "success") -> FakeApi:
    api = _retried() if results == "failure" else _gated()
    if results != "failure":
        api.run(event="pull_request", pull_requests=[{"number": 12, "head": {"sha": HEAD}}])
        files = [{"filename": "uv.lock", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"}]
        api.answers[f"/repos/{REPO}/compare/main...{HEAD}"] = {"files": files}
    api.gate("nix.yml", _nix(*jobs, conclusion="failure"))
    return api


@pytest.mark.parametrize(
    ("job", "why"),
    [
        pytest.param(
            ("flake", "failure", "Install Nix"),
            "the Nix build failed at `Install Nix`, a step that fetches only outside inputs",
            id="install-nix",
        ),
        pytest.param(
            ("flake", "failure", "Set up job"),
            "the Nix build failed at `Set up job`, a step that fetches only outside inputs",
            id="set-up-job",
        ),
        pytest.param(
            ("flake", "failure", "Run actions/checkout@" + "3" * 40),
            f"the Nix build failed at `Run actions/checkout@{'3' * 40}`, a step that fetches only"
            " outside inputs",
            id="the-checkout",
        ),
        pytest.param(
            ("flake", "cancelled", ""),
            "the Nix build was cancelled (a newer run in nix.yml's concurrency group, a person, or"
            " a lost runner cancels it)",
            id="cancelled",
        ),
    ],
)
def test_a_nix_red_at_an_outside_input_or_cancelled_is_flake_class(
    job: tuple[str, str | None, str], why: str
) -> None:
    found = _note(_gate_red(job))
    assert found.output["title"].startswith("Red (retry 0 of 2, flake-class): the head's Nix build")
    assert f"- flake-class: {why}: a new head runs it again" in found.output["summary"]


@pytest.mark.parametrize(
    "job",
    [
        pytest.param(
            ("flake", "failure", "The next release builds on the same package set"),
            id="a-build-step",
        ),
        pytest.param(
            ("flake", "failure", "Install uv"), id="install-uv-runs-after-the-head-s-script"
        ),
        # A 'timed_out' conclusion GitHub may send; the shape it sends for a job at its bound is
        # the cancelled-at-bound row's.
        pytest.param(
            ("flake", "timed_out", "Build the package and run the flake's checks"),
            id="timed-out-as-github-may-send-it",
        ),
        pytest.param(("flake", "timed_out", "Install Nix"), id="timed-out-at-an-outside-step"),
        pytest.param(("flake", "failure", ""), id="no-step-named"),
    ],
)
def test_a_nix_red_of_the_change_s_own_is_not_flake_class(job: tuple[str, str | None, str]) -> None:
    found = _note(_gate_red(job))
    assert found.output["title"].startswith("Red (retry 0 of 2): the head's Nix build"), found
    assert "- not flake-class: its red is not its tests' alone: the head's Nix build" in (
        found.output["summary"]
    )


def test_a_nix_outside_red_beside_a_flake_class_results_red_is_flake_class() -> None:
    found = _note(_gate_red(("flake", "failure", "Install Nix"), results="failure"))
    assert found.output["title"].startswith("Red (retry 0 of 2, flake-class): ")
    assert "and the Nix build failed at `Install Nix`" in found.output["summary"]


def test_a_nix_outside_red_beside_a_results_red_that_is_not_flake_class_is_not() -> None:
    api = _gate_red(("flake", "failure", "Install Nix"), results="failure")
    found = _note(api, FakeRetry(flaky=()))
    assert found.output["title"].startswith("Red (retry 0 of 2): ")
    assert "has no flaky history" in found.output["summary"]


def test_a_nix_outside_red_beside_a_red_of_another_gate_s_own_is_not_flake_class() -> None:
    api = _gate_red(("flake", "failure", "Install Nix"))
    api.gate(
        "action-pins.yml",
        {
            "attempts": [
                [("sha-matches-tag", "failure", "Every pinned SHA is its release tag's commit")]
            ],
            "conclusion": "failure",
        },
    )
    found = _note(api)
    assert found.output["title"].startswith("Red (retry 0 of 2): ")
    assert "- not flake-class: its red is not its tests' alone: the head's action pin check" in (
        found.output["summary"]
    )


def test_a_date_only_retry_of_a_nix_flake_counts_as_retry_1() -> None:
    api = _gate_red(("flake", "failure", "Install Nix"))
    tree = _earlier(api, EARLIER)
    found = _note(api, FakeRetry(((EARLIER, tree),)))
    assert found.output["title"].startswith("Red (retry 1 of 2, flake-class): the head's Nix build")


# The matcher, as GitHub reads a filter.


def test_the_filter_matcher_reads_globs_as_github_does() -> None:
    match = workflows.filter_matches
    assert match(["*.md"], "README.md") and not match(["*.md"], "docs/x.md"), "* stops at /"
    assert match(["nix/**"], "nix/a/b.nix") and not match(["nix/**"], "nixos/a"), "** crosses /"
    assert not match(["deploy/**", "!deploy/docker/**"], "deploy/docker/x"), "a later ! takes back"
    assert match(["!deploy/docker/**", "deploy/**"], "deploy/docker/x"), "order matters"
    for pattern in ("?", "a+", "[ab]", "{a,b}", "**/x", "a/**/b", "/x", "a**b", "", "a\\b"):
        assert not workflows.filter_modelled(pattern), pattern
        with pytest.raises(ValueError, match="does not read"):
            match([pattern], "x")


# ── A gate's run a newer one cancelled, its timeouts, and a head that changes a gate ──────────

PINS_RUN = f"https://github.com/{REPO}/actions/runs/5200"


def test_a_nix_red_whose_rerun_a_reopen_cancelled_stays_red_beside_a_green_newer_run() -> None:
    """A run's conclusion is its last attempt's: attempt 1 failed at the head's own step, a close
    or a reopen cancelled its re-run, so the run reads 'cancelled', and the reopen's run passed.
    Only the cancel is set aside; attempt 1's red holds."""
    api = _gated()
    older = {
        "conclusion": "cancelled",
        "attempts": [[("flake", "failure", "Build it")], [("flake", "cancelled", "")]],
    }
    api.gate("nix.yml", older, _nix())
    assert _title(api) == (
        f"Red: the head's Nix build (nix.yml) is red: attempt 1's `flake` is 'failure' at"
        f" `Build it` (run {NIX_RUN})"
    )


def test_a_gate_job_red_in_a_run_a_close_cancelled_stays_red_and_its_cancelled_job_does_not() -> (
    None
):
    """pin-smoke failed while layout-smoke ran, a close cancelled the run, and the reopen's run
    passed: pin-smoke's red holds, and layout-smoke's cancel, which the close did, is set aside."""
    api = FakeApi()
    older = {
        "conclusion": "cancelled",
        "attempts": [
            [("pin-smoke", "failure", "Build a two-platform image (no push)"),
             ("layout-smoke", "cancelled", "")]
        ],
    }  # fmt: skip
    api.gate("release-image-tooling.yml", older, {})
    found = _red(api)
    assert found.output["title"] == (
        "Red: the head's release image tooling run (release-image-tooling.yml) is red: attempt"
        " 1's `pin-smoke` is 'failure' at `Build a two-platform image (no push)` (run"
        f" {PINS_RUN})"
    )
    assert "layout-smoke" not in found.output["summary"]


def test_a_set_aside_run_is_named_in_no_passed_line_or_receipt() -> None:
    api = _gated()
    api.gate("nix.yml", _nix(("flake", "cancelled", ""), conclusion="cancelled"), _nix())
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    newer = f"https://github.com/{REPO}/actions/runs/5001"
    assert f"- its Nix build (nix.yml) passed on every attempt: run {newer}\n" in (
        found.output["summary"] + "\n"
    )
    receipt = merge_verdict.read_receipt(mc.check_body(found.body()))
    assert receipt.toolchain["nix.yml"] == newer


def test_a_middle_attempt_red_between_two_greens_is_red() -> None:
    api = _gated()
    api.gate(
        "nix.yml",
        {
            "attempts": [
                [("flake", "success", "")],
                [("flake", "failure", "Build it")],
                [("flake", "success", "")],
            ]
        },
    )
    assert "attempt 2's `flake` is 'failure' at `Build it`" in _title(api)


@pytest.mark.parametrize("minutes", [45.0, 45.2])
def test_a_nix_job_cancelled_at_its_bound_timed_out_and_is_the_change_s_own(
    minutes: float,
) -> None:
    """GitHub ends a job that reaches its timeout-minutes as 'cancelled': one that ran its whole
    45 minutes (main's nix.yml) timed out, the change's own, never flake-class. This is the shape
    GitHub sends for a timeout."""
    api = _gate_red(("flake", "cancelled", "", minutes))
    api.gate("nix.yml", _nix(("flake", "cancelled", "", minutes), conclusion="cancelled"))
    found = _note(api)
    assert found.output["title"] == (
        "Red (retry 0 of 2): the head's Nix build (nix.yml) is red: attempt 1's `flake` timed"
        f" out at its 45 minutes (run {NIX_RUN})"
    )


def test_an_older_nix_run_that_timed_out_is_red_beside_a_green_newer_run() -> None:
    api = _gated()
    timed_out = _nix(("flake", "cancelled", "", 45.2), conclusion="cancelled")
    api.gate("nix.yml", timed_out, _nix())
    assert "attempt 1's `flake` timed out at its 45 minutes" in _title(api)


@pytest.mark.parametrize("minutes", [3.0, 44.9])
def test_a_nix_job_cancelled_before_its_bound_is_still_flake_class(minutes: float) -> None:
    api = _gate_red(("flake", "cancelled", "", minutes))
    api.gate("nix.yml", _nix(("flake", "cancelled", "", minutes), conclusion="cancelled"))
    found = _note(api)
    assert found.output["title"].startswith("Red (retry 0 of 2, flake-class): the head's Nix")


@pytest.mark.parametrize("minutes", ["${{ inputs.minutes }}", 0, True, 4.5])
def test_a_gate_bound_that_is_no_whole_number_of_minutes_is_unread(minutes: object) -> None:
    api = FakeApi()
    api.tree(MAIN, {GATE["nix.yml"].path: gate_yml("nix.yml", minutes=minutes)})
    api.tree(HEAD, {GATE["nix.yml"].path: gate_yml("nix.yml", minutes=minutes)})
    assert _title(api).startswith(
        f"Red: unread: main's nix.yml bounds `flake` by {minutes!r}, not a whole number"
    )


@pytest.mark.parametrize(
    "path",
    [GATE["nix.yml"].path, ACTIONS + "setup/action.yml"],
    ids=["the-gate-s-workflow", "a-local-action"],
)
@pytest.mark.parametrize("step", ["Set up job", "Install Nix", "Run actions/checkout@" + "3" * 40])
def test_a_head_that_changes_what_a_gate_runs_owns_its_red_at_an_outside_step(
    path: str, step: str
) -> None:
    """The run takes its steps from the head: a bad action pin or a build step named Install Nix
    of the head's own fails there, so a red at those steps is the change's own."""
    api = _gate_red(("flake", "failure", step))
    text = gate_yml("nix.yml") if path == GATE["nix.yml"].path else "runs: {using: composite}\n"
    api.tree(HEAD, {MERGE_PATH: MERGE_YML, "README.md": "hi\n", path: text + "# changed\n"})
    api.gate("action-pins.yml", {})
    _contract(api, {})
    found = _note(api)
    assert found.output["title"].startswith("Red (retry 0 of 2): the head's Nix build"), found


def test_a_cancelled_job_beside_a_job_red_at_its_own_step_in_one_gate_is_not_flake_class() -> None:
    api = _gate_red(("flake", "success", ""))
    api.gate("nix.yml", _nix())
    api.gate(
        "release-image-tooling.yml",
        {
            "conclusion": "failure",
            "attempts": [
                [("pin-smoke", "cancelled", ""),
                 ("layout-smoke", "failure", "Load and run each platform from the layout")]
            ],
        },
    )  # fmt: skip
    found = _note(api)
    assert found.output["title"].startswith("Red (retry 0 of 2): the head's release image"), found


def test_under_the_queue_a_behind_head_needs_a_nix_build_by_main_s_tip_filter() -> None:
    """Behind main under the queue, the head's change is read against the commit it was built on,
    and the filter is main's tip's: there it names uv.lock, though the built-on commit's and the
    head's own nix.yml do not, so a head that changes uv.lock with no run is red."""
    api = FakeApi()
    api.answers[f"/repos/{REPO}/compare/{MAIN}...{HEAD}"] = {
        "behind_by": 3,
        "merge_base_commit": {"sha": AHEAD},
    }
    narrow = gate_yml("nix.yml", [p for p in NIX_PATHS if p != "uv.lock"])
    api.tree(AHEAD, {MERGE_PATH: MERGE_YML, "README.md": "hi\n", GATE["nix.yml"].path: narrow})
    api.tree(
        HEAD,
        {
            MERGE_PATH: MERGE_YML,
            "README.md": "hi\n",
            GATE["nix.yml"].path: narrow,
            "uv.lock": "changed\n",
        },
    )
    found = mc.decide(api, REPO, RUN, CONTROLLER, mc.Queue(FakeQueue(), APP, True)).posting
    assert found is not None and found.conclusion == "failure", found
    assert "the head changes `uv.lock`, which main's nix.yml runs on" in found.output["title"]


def test_a_head_that_deletes_a_nix_file_needs_a_nix_build() -> None:
    api = FakeApi()
    api.tree(MAIN, {MERGE_PATH: MERGE_YML, "README.md": "hi\n", "nix/old.nix": "{ }\n"})
    assert "the head changes `nix/old.nix`, which main's nix.yml runs on" in _title(api)


@pytest.mark.parametrize(
    ("plant", "says"),
    [
        pytest.param(
            lambda api: api.answers.pop(f"{NIX_RUNS}&per_page=100&page=1"),
            "Red: unread: GET /repos/owner/project/actions/workflows/nix.yml/runs",
            id="no-runs-listing",
        ),
        pytest.param(
            lambda api: (
                api.gate("nix.yml", _nix()),
                api.answers.pop(
                    f"/repos/{REPO}/actions/runs/5000/attempts/1/jobs?per_page=100&page=1"
                ),
            ),
            "Red: unread: GET /repos/owner/project/actions/runs/5000/attempts/1/jobs",
            id="no-attempt-jobs",
        ),
        pytest.param(
            lambda api: (
                api.tree(MAIN, {GATE["nix.yml"].path: "on: [unclosed\n"}),
                api.tree(HEAD, {GATE["nix.yml"].path: "on: [unclosed\n"}),
            ),
            "Red: unread: main's nix.yml is not YAML",
            id="not-yaml",
        ),
        pytest.param(
            lambda api: api.gate("nix.yml", _nix(run_attempt=None)),
            "Red: unread: nix.yml run 5000 names no attempt",
            id="no-run-attempt",
        ),
        pytest.param(
            lambda api: api.gate("nix.yml", _nix(id="5000")),
            f"Red: unread: a run of {HEAD}'s nix.yml names no id",
            id="no-whole-id",
        ),
    ],
)
def test_a_gate_answer_this_cannot_read_is_unread(plant: Any, says: str) -> None:
    api = FakeApi()
    plant(api)
    assert _title(api).startswith(says), _title(api)


def test_only_the_last_attempt_of_a_run_a_newer_one_cancelled_is_set_aside() -> None:
    """Attempt 1 was cancelled (a person, a lost runner), attempt 2 re-ran it and a reopen's run
    cancelled that: attempt 2 is the cancel the newer run did, attempt 1's is not."""
    api = _gated()
    older = {
        "conclusion": "cancelled",
        "attempts": [[("flake", "cancelled", "")], [("flake", "cancelled", "")]],
    }
    api.gate("nix.yml", older, _nix())
    assert "attempt 1's `flake` is 'cancelled'" in _title(api)
    assert "attempt 2" not in _title(api)


def test_an_older_cancelled_run_whose_job_failed_at_a_step_first_is_red_at_that_step() -> None:
    """A job that failed at a step, then was cancelled (a close, a reopen), concludes 'cancelled':
    it is judged as the failure it names, never set aside."""
    api = _gated()
    older = _nix(("flake", "cancelled", "Build the package and run the flake's checks"))
    api.gate("nix.yml", {**older, "conclusion": "cancelled"}, _nix())
    assert _title(api) == (
        "Red: the head's Nix build (nix.yml) is red: attempt 1's `flake` is 'failure' at"
        f" `Build the package and run the flake's checks` (run {NIX_RUN})"
    )


def test_a_newest_cancelled_run_whose_job_failed_at_its_own_step_is_not_flake_class() -> None:
    api = _gate_red(("flake", "cancelled", "Build the package and run the flake's checks"))
    api.gate(
        "nix.yml",
        _nix(
            ("flake", "cancelled", "Build the package and run the flake's checks"),
            conclusion="cancelled",
        ),
    )
    assert _note(api).output["title"].startswith("Red (retry 0 of 2): the head's Nix build")


@pytest.mark.parametrize(
    ("first", "says"),
    [
        pytest.param([], "attempt 1 ran no job", id="no-job-on-attempt-1"),
        pytest.param([("other", "success", "")], "attempt 1 has 0 `flake` jobs", id="no-flake"),
    ],
)
def test_an_earlier_attempt_of_a_run_a_newer_one_cancelled_is_read_whole(
    first: list[tuple[str, str | None, str]], says: str
) -> None:
    api = _gated()
    older = {"conclusion": "cancelled", "attempts": [first, [("flake", "cancelled", "")]]}
    api.gate("nix.yml", older, _nix())
    assert says in _title(api)


@pytest.mark.parametrize(
    ("times", "says"),
    [
        pytest.param({"started_at": None}, "names no started_at with a zone", id="no-start"),
        pytest.param(
            {"completed_at": "2026-11-10T12:30:00"},
            "names no completed_at with a zone: '2026-11-10T12:30:00'",
            id="no-zone",
        ),
        pytest.param({"started_at": "soon"}, "names no started_at with a zone", id="no-time"),
    ],
)
def test_a_cancelled_job_whose_times_this_cannot_read_is_unread(
    times: dict[str, object], says: str
) -> None:
    api = _gated()
    api.gate("nix.yml", _nix(("flake", "cancelled", "", 3.0, times), conclusion="cancelled"))
    assert _title(api).startswith("Red: unread: job 500000 " + says), _title(api)


@pytest.mark.parametrize(
    ("times", "says"),
    [
        pytest.param({"started_at": None}, "names no started_at with a zone", id="no-start"),
        pytest.param({"completed_at": None}, "names no completed_at with a zone", id="no-end"),
        pytest.param(
            {"started_at": "2026-11-10T12:00:00"}, "names no started_at with a zone", id="naive"
        ),
    ],
)
def test_a_superseded_job_whose_times_this_cannot_read_is_unread(
    times: dict[str, object], says: str
) -> None:
    """A job of a run a newer one cancelled, which ran 45.2 minutes past its 45: a time this cannot
    read is unread, never set aside."""
    api = _gated()
    older = _nix(("flake", "cancelled", "", 45.2, times), conclusion="cancelled")
    api.gate("nix.yml", older, _nix())
    assert _title(api).startswith("Red: unread: job 500000 " + says), _title(api)


#: A job GitHub cancelled before any runner took it: no runner, no step, and its started_at is when
#: it was queued (as the jobs API writes such a job), here 50 minutes before the cancel. The jobs
#: API names no runner by a runner_id of 0 or of null.
QUEUED = ("flake", "cancelled", "", 50.0, {"runner_id": 0, "runner_name": "", "steps": []})
QUEUED_NULL = ("flake", "cancelled", "", 50.0, {"runner_id": None, "runner_name": "", "steps": []})


@pytest.mark.parametrize("queued", [QUEUED, QUEUED_NULL], ids=["runner-id-0", "runner-id-null"])
def test_a_job_cancelled_in_the_queue_past_its_bound_is_read_as_a_cancel(
    queued: tuple[str, str, str, float, dict[str, object]],
) -> None:
    """Its time is its wait in the queue, not a run: set aside in an older run beside a green
    newer one, and flake-class on the newest."""
    api = _gated()
    api.gate("nix.yml", _nix(queued, conclusion="cancelled"), _nix())
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    api = _gate_red(queued)
    api.gate("nix.yml", _nix(queued, conclusion="cancelled"))
    assert _note(api).output["title"].startswith("Red (retry 0 of 2, flake-class): the head's Nix")


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"runner_id": 1000179068, "steps": []}, id="a-runner-and-no-step"),
        pytest.param({"runner_id": None, "steps": None}, id="no-steps-list"),
    ],
)
def test_a_cancelled_job_that_names_a_runner_or_no_steps_list_is_timed(
    fields: dict[str, object],
) -> None:
    """Only a job that names no runner and lists no step is read as one cancelled in the queue:
    one that names a runner, or an answer with no steps list, is timed, so 50 minutes past its 45
    is a timeout, red beside a green newer run."""
    api = _gated()
    older = _nix(("flake", "cancelled", "", 50.0, fields), conclusion="cancelled")
    api.gate("nix.yml", older, _nix())
    assert "attempt 1's `flake` timed out at its 45 minutes" in _title(api), _title(api)


def test_a_job_cancelled_before_it_started_is_read_as_a_cancel() -> None:
    """GitHub can end a job it cancelled before it started a second before its start: it ran
    no time, so it did not time out. Set aside in an older run beside a green newer one, and
    flake-class on the newest."""
    early = ("flake", "cancelled", "", -1 / 60)
    api = _gated()
    api.gate("nix.yml", _nix(early, conclusion="cancelled"), _nix())
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "success", found
    api = _gate_red(early)
    api.gate("nix.yml", _nix(early, conclusion="cancelled"))
    assert _note(api).output["title"].startswith("Red (retry 0 of 2, flake-class): the head's Nix")
