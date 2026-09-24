"""A CANCELLED run must not publish a verdict, and main's run must not be cancelled (#2946).

Two defects, one root: the workflows treated "no answer" as "a failing answer", and then
arranged for there to be no answer most of the time.

**The manufactured red.** ``ci.yml``'s ``test`` and ``full.yml``'s ``matrix`` are aggregation
gates over a sharded matrix. Both were ``if: ${{ always() }}``, and ``always()`` runs a job
*even when the run itself was cancelled* — at which point ``needs.<shard>.result`` is
``cancelled`` and the gate's ``!= success`` test turned that into ``exit 1``. Since ci.yml's
concurrency group deliberately collapses a branch's ``pull_request`` and ``push`` runs, one of
the two is ALWAYS cancelled, so every PR carried a red ``test`` that no test produced.
MEASURED 2026-09-18 on ``9801cf068``: the cancelled ``push`` run ``35338585659`` reported all
twelve of its other jobs ``cancelled`` and ``test`` → ``failure``, while the surviving
``pull_request`` run ``35338682195`` reported ``test`` → ``success``. Check-runs key on the
SHA, so BOTH sat on that one commit and ``mergeStateStatus`` was permanently ``UNSTABLE``.

**The invisible gap.** ``full.yml``'s group was ``cancel-in-progress: true`` on ``main``, so
every merge killed the previous commit's verification. MEASURED the same day over the last 400
main runs of that workflow: 324 cancelled · 46 failure · 29 success. Of the last 40: 38
cancelled, 1 failure, 1 queued, ZERO success — the last successful main run was #1215 at
2026-09-17T15:40Z. A cancelled run carries no conclusion a reader treats as red, so nothing
reading conclusions could see that main had stopped being verified.

**What this file asserts, and why as PROPERTIES.** Pinning the new ``if:`` strings as text
would accept the two wrong fixes as readily as the right one — ``if: success()`` (which makes
a failing shard report *skipped* instead of red, hiding real failures) and a per-SHA main group
(which stops the cancellation by fanning a 24-leg matrix out per merge). So each relation is
stated on its own:

1. neither aggregate gate is gated on bare ``always()``;
2. each is gated on ``!cancelled()`` — a failing shard still reaches the gate and reds, because
   ``fail-fast: false`` means a shard failure never cancels the run;
3. each gate script handles ``cancelled`` as its OWN branch, so a shard cancelled on a live run
   is not silently folded into the generic failure message;
4. ``full.yml`` does not cancel in progress — main's verification always completes;
5. ``ci.yml`` still DOES cancel in progress — the #2595 dedupe must not be collateral damage of
   fixing #2946, and it is the thing that stopped every PR running the matrix twice.

Parsed with a line scan rather than PyYAML, matching the convention its siblings
``tests/test_ci_concurrency_dedupe.py`` and ``tests/test_ci_tier_enforcement.py`` state: PyYAML
is not a declared test dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI_YML = WORKFLOWS / "ci.yml"
FULL_YML = WORKFLOWS / "full.yml"

#: ``(workflow file, aggregation job name, the sharded matrix job it aggregates)``.
AGGREGATE_GATES: tuple[tuple[Path, str, str], ...] = (
    (CI_YML, "test", "test-shard"),
    (FULL_YML, "matrix", "matrix-shard"),
)


def _job_block(path: Path, job: str) -> list[str]:
    """The lines of a top-level ``jobs:`` entry, excluding its own ``  <job>:`` header.

    A top-level job is indented two spaces, so the block ends at the next two-space-indented
    key. Anchoring on the indent (rather than on a blank line or the next ``- name:``) is what
    keeps a nested ``concurrency:``/``if:`` inside a *step* from being read as the job's own.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(f"  {job}:")
    except ValueError as exc:  # pragma: no cover — the vacuity floor below reports this
        raise AssertionError(f"{path.name} has no top-level job named {job!r}") from exc
    block: list[str] = []
    for line in lines[start + 1 :]:
        if re.match(r"^  \S", line):
            break
        block.append(line)
    return block


def _job_if(path: Path, job: str) -> str:
    """The job-level ``if:`` expression (four-space indent), or ``''`` when absent."""
    for line in _job_block(path, job):
        m = re.match(r"^    if:\s*(\S.*?)\s*$", line)
        if m:
            return m.group(1)
    return ""


def _cancel_in_progress(path: Path) -> str:
    """The workflow-level ``concurrency.cancel-in-progress`` value, as written."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.rstrip() != "concurrency:":
            continue
        for follower in lines[i + 1 : i + 6]:
            m = re.match(r"\s+cancel-in-progress:\s*(\S+)\s*$", follower)
            if m:
                return m.group(1)
    raise AssertionError(
        f"{path.name} has no workflow-level `concurrency:` / `cancel-in-progress:` — every "
        "relation below would have nothing to judge"
    )


# ---------------------------------------------------------------------------
# Vacuity floors, first: each relation below is trivially true on an empty parse.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path,job,shard_job", AGGREGATE_GATES, ids=lambda v: getattr(v, "name", v))
def test_the_aggregate_gate_was_actually_found(path: Path, job: str, shard_job: str) -> None:
    block = _job_block(path, job)
    assert block, f"{path.name}: the {job!r} job parsed as an empty block"
    body = "\n".join(block)
    assert f"needs.{shard_job}.result" in body, (
        f"{path.name}: the {job!r} job does not read `needs.{shard_job}.result`, so it is not "
        f"the aggregation gate this file thinks it is — the rail stopped watching the gate."
    )
    assert _job_if(path, job), f"{path.name}: the {job!r} job carries no job-level `if:`"


def test_both_workflows_declare_a_concurrency_group() -> None:
    """Floor for relations 4 and 5: a missing block must fail loudly, not read as compliant."""
    assert _cancel_in_progress(CI_YML)
    assert _cancel_in_progress(FULL_YML)


# ---------------------------------------------------------------------------
# Relations 1–3: a cancelled run publishes no verdict.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path,job,shard_job", AGGREGATE_GATES, ids=lambda v: getattr(v, "name", v))
def test_the_gate_is_not_run_on_a_cancelled_run(path: Path, job: str, shard_job: str) -> None:
    """Relations 1 + 2 — the manufactured red, stated directly.

    ``!cancelled()`` and not ``success()``: the gate MUST still run when a shard FAILS, because
    that is the whole point of an aggregation gate carrying a stable check name. ``fail-fast:
    false`` on the matrix means a failing shard never cancels the run, so both conditions hold
    at once — the gate reds on a real failure and reports nothing at all on a cancellation.
    """
    expr = _job_if(path, job)
    assert "cancelled()" in expr, (
        f"{path.name}: the {job!r} gate is `if: {expr}`, which says nothing about cancellation. "
        f"With bare `always()` a cancelled run still runs this gate, `needs.{shard_job}.result` "
        f"is 'cancelled', and the `!= success` test publishes a {job!r} FAILURE for a run that "
        f"measured nothing — on the same SHA as the surviving run's real verdict (#2946)."
    )
    assert re.search(r"!\s*cancelled\(\)", expr), (
        f"{path.name}: the {job!r} gate is `if: {expr}`. It must be gated on `!cancelled()` so "
        f"a cancelled run reports NO verdict."
    )
    assert not re.search(r"\bsuccess\(\)", expr), (
        f"{path.name}: the {job!r} gate is `if: {expr}`. Gating on `success()` makes a FAILING "
        f"shard report this stable check as *skipped* rather than red, which hides exactly the "
        f"failures the gate exists to surface. `!cancelled()` is the condition that does both."
    )


@pytest.mark.parametrize("path,job,shard_job", AGGREGATE_GATES, ids=lambda v: getattr(v, "name", v))
def test_the_gate_script_treats_cancelled_as_its_own_case(
    path: Path, job: str, shard_job: str
) -> None:
    """Relation 3 — 'no answer' and 'a failing answer' must not share one message.

    The job-level ``!cancelled()`` covers a cancelled RUN. A single leg cancelled while the run
    lives on is a different state: the run is real, and one quarter of the suite produced no
    answer. That must red (an unanswered question is never a passing answer — the doctrine
    ``full.yml``'s install-smoke legs already state) but with a message that says *unproven*
    rather than sending the reader hunting for a failing test that does not exist.
    """
    body = "\n".join(_job_block(path, job))
    assert "cancelled" in body, (
        f"{path.name}: the {job!r} gate script never mentions 'cancelled', so a leg that was "
        f"cancelled on a live run is reported as though a test had failed."
    )
    assert "unproven" in body, (
        f"{path.name}: the {job!r} gate script has no 'unproven' branch. A cancelled leg means "
        f"the suite measured nothing; saying 'shards failed' points the reader at a failing "
        f"test that does not exist."
    )


# ---------------------------------------------------------------------------
# Relations 4–5: main's verification completes; the PR dedupe survives.
# ---------------------------------------------------------------------------


def test_mains_verification_run_is_never_cancelled_by_the_next_merge() -> None:
    """Relation 4 — the invisible gap, stated directly.

    ``full.yml`` runs on push-to-main, the nightly cron and dispatch. With
    ``cancel-in-progress: true`` every merge killed the run verifying the commit before it, and
    a cancelled run has no conclusion anything treats as red — so 81% of main's verification
    history evaporated without a single red check to show for it.
    """
    value = _cancel_in_progress(FULL_YML)
    assert value == "false", (
        f"full.yml's concurrency has `cancel-in-progress: {value}`. This workflow IS main's "
        "verification, so cancelling in progress means the next merge erases the answer the "
        "current one is computing: measured 324 cancelled / 46 failure / 29 success over 400 "
        "main runs, and zero successes in the last 40. `false` queues instead — the in-flight "
        "run always completes and GitHub supersedes the older PENDING run, so main's HEAD "
        "converges on a run that actually finished."
    )


def test_the_pr_gate_still_cancels_superseded_runs() -> None:
    """Relation 5 — #2595's dedupe must not become collateral damage of fixing #2946.

    ci.yml's group is what stopped every non-fork PR running the whole matrix TWICE on one
    head SHA (~72 jobs in flight to answer 8 PRs, against runner concurrency of 4–6). The fix
    for the manufactured red is in the aggregate gate's condition, NOT in the group: turning
    cancellation off here would trade a cosmetic red for the binding constraint on merge rate.
    """
    value = _cancel_in_progress(CI_YML)
    assert value == "true", (
        f"ci.yml's concurrency has `cancel-in-progress: {value}`. That group collapses the "
        "`pull_request` and `push` runs of a branch AND cancels superseded pushes (#2595); "
        "without cancellation both duplicates run to completion and half of all runner "
        "capacity recomputes an answer already in flight. The cancelled loser's red is fixed "
        "in the `test` gate's `if:`, not by keeping both runs alive."
    )
