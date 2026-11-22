"""Each scheduled workflow is classified: timely, with the event that starts it beside its cron, or
best effort, with why nothing waits on its time (#5850).

GitHub fires this repository's scheduled workflows hours late, or not at all. Over 2026-10-21..26
Breadth, CI telemetry, the GHCR pull, the walkthroughs, nix and action-pins started 283-335 min
late at the median, and the merge-verdict sweep's every-ten-minutes cron ran 9 times with 208
slots missed (flake-report.yml's first report, run 40331432116). So a workflow whose result a
gate or a report reads by a time is **timely**: an event that is not its cron starts it (a run it
reads finishing, a pull request), and its cron stays as the fallback. Every other scheduled
workflow is **best effort**: a person reads what it finds, whenever it runs.

:data:`SCHEDULED` holds each scheduled workflow under ``.github/workflows``, one row each. A
scheduled workflow it does not hold fails by name, and so does a row whose workflow has no
schedule. A timely row names its event, and fails when that event is not among its workflow's
triggers, so cron alone, or an unrelated trigger, cannot pass for it. A best-effort row gives its
reason. The triggers are read in each of the three shapes ``on:`` takes: one event, a list of
them, or a map of each to its settings.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest
import source_index
import yaml


class Row(NamedTuple):
    #: The event that starts a timely workflow beside its cron, or None for a best-effort one.
    event: str | None
    #: What reads its result by a time (timely), or why nothing does (best effort).
    reason: str


def timely(event: str, reason: str) -> Row:
    return Row(event, reason)


def best_effort(reason: str) -> Row:
    return Row(None, reason)


#: Every scheduled workflow, by its file name under ``.github/workflows``.
SCHEDULED: dict[str, Row] = {
    "ci-telemetry.yml": timely(
        "workflow_run",
        "the collector: each Merge and Breadth run's history, which the verdict's flake class and "
        "every report read, written as that run completes; its daily run writes the scheduled "
        "workflows' slots",
    ),
    "codeql-alerts.yml": timely(
        "workflow_run",
        "an issue for each new CodeQL alert on main, kept as main's CodeQL analysis completes",
    ),
    "merge-verdict.yml": timely(
        "workflow_run",
        "the merge verdict, posted as each run it judges completes (and on a check run); the "
        "ten-minute sweep is the fallback",
    ),
    "flaky-tests.yml": timely(
        "workflow_run",
        "the 'Flaky test:' issues the merge verdict's flake class reads, kept as CI telemetry's "
        "scheduled run finishes green on main",
    ),
    "action-pins.yml": timely(
        "pull_request",
        "a pull request that changes a pin is checked before it merges, and the verdict holds it "
        "to that run; the weekly run is for a tag that moves upstream",
    ),
    "nix.yml": timely(
        "pull_request",
        "a pull request that changes the flake or its bounds is built before it merges; the weekly "
        "run is for an input that moves outside the tree",
    ),
    "breadth.yml": best_effort(
        "the nightly platforms a pull request's run does not cover; nothing waits on its time: a "
        "release reads a Breadth record by its run and digest, and a rehearsal tag starts its own "
        "run. When it fires is the Backlog row 'Breadth's nightly schedule fires mid-day': it "
        "should give way to a building queue commit, which no trigger does",
    ),
    "clean-machine-walkthroughs.yml": best_effort(
        "the weekly clean-machine walkthroughs; a release rehearsal calls them itself",
    ),
    "flake-report.yml": best_effort(
        "the one weekly 'CI health' issue a person reads; nothing gates on it",
    ),
    "ghcr-anonymous-pull.yml": best_effort(
        "a standing check that the published images pull without a credential; a person reads "
        "its red",
    ),
    "headless-run-smoke.yml": best_effort(
        "a weekly drive of `personalclaw run`, so a regression surfaces unasked; a person reads "
        "its red",
    ),
    "image-pins.yml": best_effort(
        "the weekly 'Pinned images to refresh' issue; the refresh is a reviewed pull request",
    ),
    "pin-refresh.yml": best_effort(
        "the weekly refresh pull request, which merges on the normal verdict; a missed week's "
        "pins move in the next one",
    ),
    "release-rehearsal.yml": best_effort(
        "every release artifact built and smoked weekly; a release reads the newest rehearsal, "
        "and a maintainer dispatches one when it needs a fresher one",
    ),
    "settings-report.yml": best_effort(
        "the weekly 'Repository settings drift' issue a person reads",
    ),
}


def events(workflow: object) -> frozenset[str]:
    """The events a workflow runs on, from its ``on:`` in any of its three shapes. YAML 1.1
    reads a bare ``on`` key as true, so either key is read."""
    if not isinstance(workflow, dict):
        return frozenset()
    on = workflow.get("on", workflow.get(True))
    if isinstance(on, str):
        return frozenset({on})
    if isinstance(on, (list, dict)):
        return frozenset(str(event) for event in on)
    return frozenset()


def violations(texts: dict[str, str], table: dict[str, Row]) -> list[str]:
    """What is wrong with ``table`` against the workflows ``texts`` holds, by file name."""
    triggers = {name: events(yaml.safe_load(text)) for name, text in texts.items()}
    scheduled = {name for name, on in triggers.items() if "schedule" in on}
    bad = [
        f"{name} runs on a schedule and is not classified: add its row, timely with the event "
        "that starts it beside its cron, or best effort with why nothing waits on its time"
        for name in sorted(scheduled - set(table))
    ]
    for name, row in sorted(table.items()):
        if name not in scheduled:
            bad.append(f"{name} has a row but runs on no schedule: delete its row")
        elif row.event is not None and row.event not in triggers[name]:
            bad.append(
                f"{name} is timely but runs on {sorted(triggers[name])}: nothing but its cron "
                f"starts it, where its row says {row.event} does"
            )
        if not row.reason.strip():
            bad.append(f"{name}'s row gives no reason")
    return bad


def _texts() -> dict[str, str]:
    return {
        path.name: source_index.text(path)
        for path in source_index.workflow_files()
        if path.suffix in {".yml", ".yaml"}
    }


def test_every_scheduled_workflow_is_classified_and_each_timely_one_runs_on_its_event() -> None:
    assert violations(_texts(), SCHEDULED) == []
    assert {row.event for row in SCHEDULED.values()} > {None}, "no workflow is timely"


_CRON_ALONE = 'on:\n  schedule:\n    - cron: "5 4 * * *"\n  workflow_dispatch:\n'
_CHAINED = (
    _CRON_ALONE + '  workflow_run:\n    workflows: ["CI telemetry"]\n    types: [completed]\n'
)


@pytest.mark.parametrize(
    ("text", "table", "said"),
    [
        pytest.param(
            _CRON_ALONE,
            {"planted.yml": timely("workflow_run", "a gate reads it")},
            "planted.yml is timely but runs on ['schedule', 'workflow_dispatch']",
            id="a-timely-workflow-on-its-cron-alone",
        ),
        pytest.param(
            _CRON_ALONE + "  pull_request:\n",
            {"planted.yml": timely("workflow_run", "a gate reads it")},
            "nothing but its cron starts it, where its row says workflow_run does",
            id="a-timely-workflow-on-an-unrelated-event",
        ),
        pytest.param(
            _CHAINED,
            {},
            "planted.yml runs on a schedule and is not classified",
            id="a-new-scheduled-workflow",
        ),
        pytest.param(
            "on:\n  push:\n",
            {"planted.yml": best_effort("a person reads it")},
            "planted.yml has a row but runs on no schedule",
            id="a-row-whose-workflow-lost-its-schedule",
        ),
        pytest.param(
            _CRON_ALONE,
            {"planted.yml": best_effort(" ")},
            "planted.yml's row gives no reason",
            id="a-best-effort-row-with-no-reason",
        ),
    ],
)
def test_each_rule_fails_on_its_plant(text: str, table: dict[str, Row], said: str) -> None:
    found = violations({"planted.yml": text}, table)
    assert any(said in line for line in found), found


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_CHAINED, id="a-map"),
        pytest.param("on: [schedule, workflow_run]\n", id="a-list"),
    ],
)
def test_a_timely_workflow_on_its_event_beside_its_cron_passes(text: str) -> None:
    assert violations({"planted.yml": text}, {"planted.yml": timely("workflow_run", "x")}) == []


@pytest.mark.parametrize(
    ("text", "found"),
    [
        pytest.param("on: schedule\n", {"schedule"}, id="one-event"),
        pytest.param("on: [push, schedule]\n", {"push", "schedule"}, id="a-list"),
        pytest.param(_CHAINED, {"schedule", "workflow_dispatch", "workflow_run"}, id="a-map"),
        pytest.param('"on":\n  schedule: []\n', {"schedule"}, id="a-quoted-key"),
        pytest.param("name: none\n", set(), id="no-triggers"),
    ],
)
def test_the_triggers_are_read_in_each_shape(text: str, found: set[str]) -> None:
    assert events(yaml.safe_load(text)) == found


def test_the_real_workflows_are_read() -> None:
    texts = _texts()
    assert "flaky-tests.yml" in texts and len(texts) >= 10, sorted(texts)
    assert all(Path(name).suffix in {".yml", ".yaml"} for name in texts)
