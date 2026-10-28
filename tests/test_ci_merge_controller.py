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
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tools.ci import merge_controller as mc
from tools.ci import merge_verdict, trust

REPO = "owner/project"
RUN = 77
HEAD = "a" * 40
MAIN = "b" * 40
CONTROLLER = "c" * 40
DIGEST = "sha256:" + "d" * 64
WF = trust.DEFINITION_ROOTS[0]
MERGE_PATH = mc.MERGE_WORKFLOW
MERGE_YML = "name: Merge\non:\n  workflow_dispatch: {}\njobs: {}\n"


_PULL_NUMBER = re.compile(r"refs/pull/([0-9]+)/head")


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
        self.codeql(HEAD)

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
        listed = [
            {
                "id": 900 + n,
                "path": path or mc.DEPENDENCY_REVIEW,
                "head_sha": head,
                "status": s,
                "conclusion": c,
            }
            for n, (s, c) in enumerate(runs)
        ]
        query = f"head_sha={head}&event=pull_request_target&per_page=100"
        self.answers[f"/repos/{REPO}/actions/workflows/dependency-review.yml/runs?{query}"] = {
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
            }
            for i, (n, s, c) in enumerate(jobs)
        ]
        path = f"/repos/{REPO}/actions/runs/{RUN}/attempts/{attempt}/jobs?per_page=100&page=1"
        self.answers[path] = {"total_count": len(listed), "jobs": listed}
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
    ) -> None:
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
            "html_url": f"https://github.com/{REPO}/actions/runs/{4242 + n}",
            **r,
        }
        for n, r in enumerate(runs)
    ]
    api.answers[CONTRACT_RUNS] = {"total_count": len(listed), "workflow_runs": listed}


def _changes(api: FakeApi, path: str = "tools/ci/verdict.py", text: str = "x = 1\n") -> FakeApi:
    api.tree(HEAD, {MERGE_PATH: MERGE_YML, "README.md": "hi\n", path: text})
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
    api.tree(MAIN, files)
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
