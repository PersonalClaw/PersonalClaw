"""The merge verdict's poster judges a Merge run by main's rules, through the API alone.

``tools/ci/merge_controller.py`` runs against a fake API: a run, its attempts' jobs, both trees
and their blobs, the comparison with main and the run's artifacts. A red, cancelled, skipped or
missing ``results`` posts failure, never neutral; a head that changes a definition is not
judged; another workflow named Merge posts nothing; and a green carries the receipt
``verify-check-run`` reads.
"""

from __future__ import annotations

import base64
import json
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
            {"name": n, "status": s, "conclusion": c, "labels": ["ubuntu-24.04"]}
            for n, s, c in jobs
        ]
        path = f"/repos/{REPO}/actions/runs/{RUN}/attempts/{attempt}/jobs?per_page=100&page=1"
        self.answers[path] = {"total_count": len(listed), "jobs": listed}

    def tree(self, commit: str, files: Mapping[str, str], truncated: bool = False) -> None:
        tree_oid = _oid(commit + "tree")
        self.answers[f"/repos/{REPO}/git/commits/{commit}"] = {"tree": {"sha": tree_oid}}
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
    return mc.decide(api, REPO, RUN, CONTROLLER)


def _red(api: FakeApi) -> mc.Posting:
    found = _decide(api)
    assert isinstance(found, mc.Posting) and found.conclusion == "failure", found
    assert found.output["title"].startswith("Red: ")
    assert "text" not in found.output, "a red carries no receipt"
    return found


def test_a_green_run_posts_success_with_the_receipt_the_reader_takes() -> None:
    found = _decide(FakeApi())
    assert isinstance(found, mc.Posting) and found.conclusion == "success"
    body = mc.check_body(found.body())
    assert body["name"] == "merge-verdict" and body["head_sha"] == HEAD
    receipt = merge_verdict.read_receipt(body)
    assert receipt.controller_revision == CONTROLLER and receipt.head_sha == HEAD
    assert receipt.tree_oid == _oid(HEAD + "tree")
    assert receipt.evidence_digests == {"ci-record-unit": DIGEST}
    assert receipt.toolchain == {"runner": "ubuntu-24.04", "definitions": MAIN}


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


def test_a_red_on_an_earlier_attempt_is_never_rerun_into_green() -> None:
    api = FakeApi()
    api.run(run_attempt=2)
    api.jobs(1, ("results", "completed", "failure"))
    api.jobs(2, ("results", "completed", "success"))
    assert "attempt 1's `results` is 'failure'" in _red(api).output["title"]


@pytest.mark.parametrize(
    "path, text",
    [
        (MERGE_PATH, MERGE_YML + "# every job passes now\n"),
        ("tools/ci/verdict.py", "def decide(*a): return 'green'\n"),
        (f"{WF}merge-verdict.yml", "name: Merge verdict\n"),
    ],
)
def test_a_head_that_changes_a_definition_is_not_judged(path: str, text: str) -> None:
    api = FakeApi()
    api.tree(HEAD, {MERGE_PATH: MERGE_YML, "README.md": "hi\n", path: text})
    found = _red(api)
    assert found.output["title"].startswith("Red: not judged: the head changes 1 CI definitions")
    assert f"`{path}`" in found.output["title"]


def test_a_head_behind_main_posts_failure() -> None:
    api = FakeApi()
    api.answers[f"/repos/{REPO}/compare/{MAIN}...{HEAD}"] = {"behind_by": 3}
    assert "3 commits behind main" in _red(api).output["title"]


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
    assert json.loads(out.read_text())["conclusion"] == "success"
    api = FakeApi()
    api.run(event="push")
    assert mc.main([*argv, "--out", str(tmp_path / "none.json")], get=api) == 0
    assert outputs.read_text().splitlines() == ["post=true", "post=false"]
    assert not (tmp_path / "none.json").exists()
