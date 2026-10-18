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
    assert "breadth.yml" in scheduled and "ci.yml" not in scheduled


def test_its_crons_are_the_ones_the_workflows_name() -> None:
    assert telemetry._crons(telemetry.BREADTH_PATH), "Breadth names a schedule"
    assert telemetry._crons(telemetry.CI_PATH) == []


# ── the collector's workflow ──────────────────────────────────────────────────────────────────

import yaml  # noqa: E402

_COLLECTOR = _WORKFLOWS / "ci-telemetry.yml"


def _collector() -> dict:
    return yaml.safe_load(_COLLECTOR.read_text("utf-8"))


def test_the_collector_follows_both_collected_workflows_by_name() -> None:
    names = {
        yaml.safe_load((REPO / p).read_text("utf-8"))["name"]
        for p in (telemetry.CI_PATH, telemetry.BREADTH_PATH)
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


def test_the_collector_follows_main_only() -> None:
    assert _collector()[True]["workflow_run"]["branches"] == ["main"]


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
    wf = _collector()
    assert wf["permissions"] == {"contents": "read", "actions": "read"}
    assert all("permissions" not in job for job in wf["jobs"].values())
