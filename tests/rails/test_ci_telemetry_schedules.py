"""``tools/ci/telemetry.py``'s ``slots`` reads every scheduled workflow in the tree (#3948).

A scheduled run that never starts fires no ``workflow_run``, so the slots collector is what says
a night was missed. It must see every workflow with a ``cron:`` line, so a new nightly cannot
miss a slot unseen.
"""

from __future__ import annotations

import re

from rails_repo import REPO

from tools.ci import telemetry

_WORKFLOWS = REPO / ".github" / "workflows"


def test_the_slots_collector_reads_every_scheduled_workflow_in_the_tree() -> None:
    scheduled = sorted(
        p.name
        for p in _WORKFLOWS.glob("*.yml")
        if re.search(r"^\s*-?\s*cron:", p.read_text("utf-8"), re.M)
    )
    assert telemetry._scheduled_workflows() == scheduled
    assert "breadth.yml" in scheduled and "merge.yml" not in scheduled


def test_its_crons_are_the_ones_the_workflows_name() -> None:
    assert telemetry._crons(telemetry.BREADTH_PATH), "Breadth names a schedule"
    assert telemetry._crons(telemetry.MERGE_PATH) == []


# ── the collector's workflow ──────────────────────────────────────────────────────────────────

import yaml  # noqa: E402

_COLLECTOR = REPO / telemetry.COLLECTOR_PATH


def _collector() -> dict:
    return yaml.safe_load(_COLLECTOR.read_text("utf-8"))


def test_the_collector_follows_every_collected_workflow_by_name() -> None:
    names = {
        yaml.safe_load((REPO / telemetry.WORKFLOWS / name).read_text("utf-8"))["name"]
        for name in telemetry.COUNTED
    }
    assert {telemetry.MERGE_PATH, telemetry.BREADTH_PATH} == {
        f"{telemetry.WORKFLOWS}/{name}" for name in telemetry.COUNTED
    }
    on = _collector()[True]
    assert set(on["workflow_run"]["workflows"]) == names
    assert on["workflow_run"]["types"] == ["completed"]


def _normal(text: str) -> str:
    return re.sub(r"\s+", "", text)


def test_the_collect_job_starts_no_runner_for_a_run_that_does_not_count() -> None:
    """The job's ``if:`` is ``telemetry.collect_condition()`` word for word: the same rule
    ``telemetry.counts`` applies, per workflow and event. A changed operator, a dropped clause
    or an event the code does not count all fail here."""
    written = str(_collector()["jobs"]["collect"]["if"]).strip()
    assert written.startswith("${{") and written.endswith("}}"), written
    assert _normal(written[3:-2]) == _normal(telemetry.collect_condition())


def test_the_collector_follows_main_and_the_merge_queue_s_refs_for_main_only() -> None:
    """Main, and the queue's refs for main, whose runs the job's ``if:`` and ``counts()`` read
    whole: no other branch's run starts the workflow."""
    assert _collector()[True]["workflow_run"]["branches"] == ["main", "gh-readonly-queue/main/**"]
    assert telemetry.QUEUE_REF_PREFIX.startswith("gh-readonly-queue/main/")
    # What the job's ``if:`` lets start is a prefix of every ref ``counts()`` takes.
    assert telemetry.QUEUE_REF.pattern.startswith(telemetry.QUEUE_REF_PREFIX)


def _step(job: str, uses: str) -> dict:
    found = [
        step
        for step in _collector()["jobs"][job]["steps"]
        if str(step.get("uses", "")).startswith(uses)
    ]
    assert len(found) == 1, found
    return found[0]


def test_the_history_is_uploaded_under_the_name_the_planner_reads() -> None:
    """``tools/ci/shards.py`` looks a run's history up by ``telemetry.HISTORY_ARTIFACT``; an
    upload under any other name would leave every plan on equal weights, unseen."""
    upload = _step("collect", "actions/upload-artifact@")
    assert upload["with"]["name"] == telemetry.HISTORY_ARTIFACT.format(
        run="${{ github.event.workflow_run.id }}",
        attempt="${{ github.event.workflow_run.run_attempt }}",
    )


def test_each_attempt_is_collected_in_its_own_concurrency_group() -> None:
    group = str(_collector()["concurrency"]["group"])
    assert "github.event.workflow_run.id" in group
    assert "github.event.workflow_run.run_attempt" in group
    assert _collector()["concurrency"]["cancel-in-progress"] is False


def test_no_step_interpolates_an_expression_into_its_shell() -> None:
    """The triggering run's fields (a branch name, a commit title) are a candidate's text. They
    reach a step only through ``env:``, never spliced into the script."""
    for job in _collector()["jobs"].values():
        for step in job["steps"]:
            assert "${{" not in str(step.get("run", "")), step


def test_every_telemetry_upload_is_kept_ninety_days() -> None:
    uploads = [
        step
        for job in _collector()["jobs"].values()
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert len(uploads) == 2
    assert all(step["with"]["retention-days"] == 90 for step in uploads), uploads
    assert all(step["with"]["if-no-files-found"] == "error" for step in uploads)


def test_the_collector_holds_no_write() -> None:
    """It reads: the run and its artifacts, and for a tested push the pull request it merged and
    that pull request's events, as tree-check does."""
    wf = _collector()
    assert wf["permissions"] == {
        "contents": "read",
        "actions": "read",
        "pull-requests": "read",
        "issues": "read",
    }
    assert all("permissions" not in job for job in wf["jobs"].values())


# ── every report a collected workflow uploads reaches the history ─────────────────────────────


def _uploads(workflow: dict) -> list[tuple[str, str, list[str]]]:
    """Each artifact upload of a workflow: its job, its name and its paths."""
    found = []
    for job_id, job in (workflow.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            if not str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                continue
            given = step.get("with") or {}
            paths = [p.strip() for p in str(given.get("path", "")).splitlines() if p.strip()]
            found.append((job_id, str(given.get("name", "")), paths))
    return found


def _holds_a_report(path: str) -> bool:
    """Whether an upload's path is where a junit report sits: the reports folder itself, or a
    ``junit-*.xml`` file."""
    base = path.rstrip("/").rsplit("/", 1)[-1]
    return path.rstrip("/") == "reports" or (base.startswith("junit-") and base.endswith(".xml"))


def unread_reports(workflow: dict) -> list[str]:
    """Each upload of a junit report whose name the collector does not read reports from
    (``telemetry.REPORT_ARTIFACT_PREFIXES``): its test cases would never reach the history, and
    the shard planner would weigh every file the same, unseen."""
    return [
        f"{job}'s upload {name!r} holds {path}, and the collector reads reports only from"
        f" artifacts named {' or '.join(p + '*' for p in telemetry.REPORT_ARTIFACT_PREFIXES)}"
        for job, name, paths in _uploads(workflow)
        for path in paths
        if _holds_a_report(path) and not name.startswith(telemetry.REPORT_ARTIFACT_PREFIXES)
    ]


def _collected() -> dict[str, dict]:
    return {
        name: yaml.safe_load((REPO / telemetry.WORKFLOWS / name).read_text("utf-8"))
        for name in sorted(telemetry.COUNTED)
    }


def test_every_report_a_collected_workflow_uploads_is_one_the_collector_reads() -> None:
    """The flip renamed merge.yml's reports to ``reports-<slug>-<attempt>``, which the collector
    did not read, and every plan since weighed every file the same. Each collected workflow's
    report uploads are held to the names the collector reads."""
    collected = _collected()
    assert set(collected) == {"merge.yml", "breadth.yml"}
    for name, workflow in collected.items():
        held = [u for u in _uploads(workflow) if any(_holds_a_report(p) for p in u[2])]
        assert held, f"{name} uploads no report: the scan no longer reads it"
        assert unread_reports(workflow) == [], name


def _renamed(name: str, old: str, new: str) -> dict:
    text = (REPO / telemetry.WORKFLOWS / name).read_text("utf-8")
    assert old in text, old
    return yaml.safe_load(text.replace(old, new))


def test_a_report_uploaded_under_a_name_the_collector_does_not_read_is_named() -> None:
    reports = _renamed("merge.yml", "name: reports-${{", "name: test-reports-${{")
    found = unread_reports(reports)
    assert found and all(
        "'test-reports-${{ matrix.slug }}-${{ github.run_attempt }}'" in f for f in found
    )
    assert "holds reports/" in found[0]
    junit = _renamed("breadth.yml", "name: junit-rails-macos", "name: rails-macos-report")
    assert unread_reports(junit) == [
        "rails-macos's upload 'rails-macos-report' holds reports/junit-rails-macos.xml, and the"
        " collector reads reports only from artifacts named junit-* or reports-*"
    ]
    # A folder under reports/, or another file there, is no report.
    assert not _holds_a_report("reports/random-order/")
    assert not _holds_a_report("reports/real-home-guard-macos.txt")


def test_the_collector_path_names_the_workflow_that_uploads_the_history() -> None:
    """The shard planner reads a history only when its uploader ran this workflow."""
    assert _COLLECTOR.is_file()
    uploads = [
        step
        for step in _collector()["jobs"]["collect"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert len(uploads) == 1
