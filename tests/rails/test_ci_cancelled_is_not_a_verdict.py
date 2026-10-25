"""A CANCELLED run must not publish a verdict, and main's run must not be cancelled (#2946).

Two defects, one root: the workflows treated "no answer" as "a failing answer", and then
arranged for there to be no answer most of the time.

**The manufactured red.** ``ci.yml``'s ``test`` (retired with ci.yml by #3915) and
``breadth.yml``'s ``coverage`` were aggregation gates over a sharded matrix, both
``if: ${{ always() }}``, and ``always()`` runs a job
*even when the run itself was cancelled* — at which point ``needs.<shard>.result`` is
``cancelled`` and the gate's ``!= success`` test turned that into ``exit 1``. Until #3915 ci.yml
also ran on a feature branch's ``push``, and its concurrency group collapsed that run onto the
branch's ``pull_request`` run, so one of the two was ALWAYS cancelled and every PR carried a red
``test`` that no test produced. Since #3915 a superseded ``pull_request`` run (a newer push to the
pull request's branch) is the run that gets cancelled; the gate's condition covers both.
MEASURED 2026-09-18 on ``9801cf068``: the cancelled ``push`` run ``35338585659`` reported all
twelve of its other jobs ``cancelled`` and ``test`` → ``failure``, while the surviving
``pull_request`` run ``35338682195`` reported ``test`` → ``success``. Check-runs key on the
SHA, so BOTH sat on that one commit and ``mergeStateStatus`` was permanently ``UNSTABLE``.

**The invisible gap.** main's verification workflow's group was ``cancel-in-progress: true``
on ``main``, so every merge killed the previous commit's verification. MEASURED the same day
over the last 400 main runs of that workflow: 324 cancelled · 46 failure · 29 success. Of the
last 40: 38 cancelled, 1 failure, 1 queued, ZERO success — the last successful main run was
#1215 at 2026-09-17T15:40Z. A cancelled run carries no conclusion a reader treats as red, so nothing
reading conclusions could see that main had stopped being verified.

**What this file asserts, and why as PROPERTIES.** Pinning the new ``if:`` strings as text
would accept the two wrong fixes as readily as the right one — ``if: success()`` (which makes
a failing shard report *skipped* instead of red, hiding real failures) and a per-SHA main group
(which stops the cancellation by fanning a 24-leg matrix out per merge). So each relation is
stated on its own:

1. no verdict job is gated on bare ``always()``: ``breadth.yml``'s ``coverage`` gate, and each
   tier workflow's ``results``, whose steps would otherwise run on a cancelled run and publish a
   green check for a run that measured nothing;
2. each is gated on ``!cancelled()`` — a failing shard still reaches it and reds, because
   ``fail-fast: false`` means a shard failure never cancels the run;
3. a shard cancelled on a live run is its OWN case, not folded into the generic failure: the
   ``coverage`` gate says *unproven*, and the tier's verdict reads the job ``cancelled``, red
   (``tests/test_ci_verdict_records.py``). On a push to main, a suite shard a newer push's
   waiting shard replaced reads ``superseded``, red, when main's ``tools/ci/superseded.py``
   proves it from the API (``merge.yml``'s ``results``), and ``cancelled`` otherwise;
4. main's verification run never cancels in progress: ``breadth.yml``'s group, and
   ``merge.yml``'s, which cancels only a pull request's run;
5. the tier still DOES cancel a pull request's run in progress — a newer push to its branch
   must cancel the run it supersedes, and fixing #2946 must not cost that.

ci.yml, whose ``test`` gate this file first held, was retired by #3915; its verdict is the
tier's ``results``.

Parsed with a line scan rather than PyYAML, matching the convention its siblings
``tests/rails/test_ci_concurrency_dedupe.py`` and ``tests/rails/test_ci_tier_enforcement.py``
state: PyYAML is not a declared test dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from rails_repo import REPO

REPO_ROOT = REPO
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
BREADTH_YML = WORKFLOWS / "breadth.yml"
PR_YML = WORKFLOWS / "pr.yml"
MERGE_YML = WORKFLOWS / "merge.yml"
TIER = (PR_YML, MERGE_YML)

#: ``(workflow file, aggregation job name, the sharded matrix job it aggregates)``.
AGGREGATE_GATES: tuple[tuple[Path, str, str], ...] = ((BREADTH_YML, "coverage", "coverage-shard"),)

#: The verdict jobs relations 1 and 2 hold: each aggregation gate and each tier's ``results``.
VERDICT_JOBS: tuple[tuple[Path, str], ...] = tuple((p, j) for p, j, _ in AGGREGATE_GATES) + tuple(
    (p, "results") for p in TIER
)

#: A tier workflow's cancel-in-progress: true for a pull request's run, false for any other.
_ONLY_A_PULL_REQUEST = "${{ github.event_name == 'pull_request' }}"


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
            m = re.match(r"\s+cancel-in-progress:\s*(\S.*?)\s*$", follower)
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


@pytest.mark.parametrize("path", TIER, ids=lambda p: p.name)
def test_the_tier_verdict_was_actually_found(path: Path) -> None:
    body = "\n".join(_job_block(path, "results"))
    assert "verdict.py" in body, (
        f"{path.name}: the `results` job runs no verdict.py, so it is not the verdict this file "
        f"thinks it is"
    )
    assert _job_if(path, "results"), f"{path.name}: the `results` job carries no job-level `if:`"


@pytest.mark.parametrize("path", (BREADTH_YML, *TIER), ids=lambda p: p.name)
def test_each_workflow_declares_a_concurrency_group(path: Path) -> None:
    """Floor for relations 4 and 5: a missing block must fail loudly, not read as compliant."""
    assert _cancel_in_progress(path)


# ---------------------------------------------------------------------------
# Relations 1–3: a cancelled run publishes no verdict.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path,job", VERDICT_JOBS, ids=lambda v: getattr(v, "name", v))
def test_the_gate_is_not_run_on_a_cancelled_run(path: Path, job: str) -> None:
    """Relations 1 + 2 — the manufactured red, stated directly.

    ``!cancelled()`` and not ``success()``: the gate MUST still run when a shard FAILS, because
    that is the whole point of an aggregation gate carrying a stable check name. ``fail-fast:
    false`` on the matrix means a failing shard never cancels the run, so both conditions hold
    at once — the gate reds on a real failure and reports nothing at all on a cancellation.
    """
    expr = _job_if(path, job)
    assert "cancelled()" in expr, (
        f"{path.name}: the {job!r} gate is `if: {expr}`, which says nothing about cancellation. "
        f"With bare `always()` a cancelled run still runs this job, and it publishes a {job!r} "
        f"verdict for a run that measured nothing — a red where the gate reads the cancelled "
        f"shard, a green where every step it would judge with is skipped (#2946)."
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
    lives on is a different state: the run is real, and one shard of the suite produced no
    answer. That must red (an unanswered question is never a passing answer — the doctrine
    ``breadth.yml``'s install-smoke legs already state) but with a message that says *unproven*
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

    ``breadth.yml`` runs on the nightly cron and dispatch. With
    ``cancel-in-progress: true`` every merge killed the run verifying the commit before it, and
    a cancelled run has no conclusion anything treats as red — so 81% of main's verification
    history evaporated without a single red check to show for it.
    """
    value = _cancel_in_progress(BREADTH_YML)
    assert value == "false", (
        f"breadth.yml's concurrency has `cancel-in-progress: {value}`. This workflow IS main's "
        "verification, so cancelling in progress means the next merge erases the answer the "
        "current one is computing: measured 324 cancelled / 46 failure / 29 success over 400 "
        "main runs, and zero successes in the last 40. `false` queues instead — the in-flight "
        "run always completes and GitHub supersedes the older PENDING run, so main's HEAD "
        "converges on a run that actually finished."
    )


def test_mains_merge_run_is_never_cancelled_by_the_next_merge() -> None:
    """Relation 4 for the tier: a push to main is its commit's check, so ``merge.yml`` cancels in
    progress only a pull request's run (#3913)."""
    value = _cancel_in_progress(MERGE_YML)
    assert value == _ONLY_A_PULL_REQUEST, (
        f"merge.yml's concurrency has `cancel-in-progress: {value}`. A push to main is its "
        f"commit's check: the next merge must queue behind it, not erase it, so the run cancels "
        f"in progress only for a pull request, `{_ONLY_A_PULL_REQUEST}`."
    )


@pytest.mark.parametrize("path", TIER, ids=lambda p: p.name)
def test_the_pr_gate_still_cancels_superseded_runs(path: Path) -> None:
    """Relation 5 — cancelling a superseded run must not become collateral damage of fixing
    #2946.

    A newer push to a pull request's branch must cancel the run for the older head, or every push
    leaves its predecessor holding runners. The fix for the manufactured red is in the verdict
    job's condition, NOT in the group: turning cancellation off here would trade a cosmetic red
    for runner capacity.
    """
    value = _cancel_in_progress(path)
    assert value == _ONLY_A_PULL_REQUEST, (
        f"{path.name}'s concurrency has `cancel-in-progress: {value}`. A pull request's run must "
        f"cancel in progress, so a newer push to its branch cancels the run for the older head; "
        f"any other run is its commit's check. The cancelled run's verdict is fixed in "
        f"`results`' `if:`, not by keeping superseded runs alive."
    )


#: The group a suite shard on main shares across commits, less its key, as the stage jobs spell
#: it: `merge-<'suite' on main's first attempt>-<matrix.key>`.
_SUITE_GROUP_PREFIX = "merge-suite-"
_SUITE_JOBS = ("python-suite", "python-suite-macos")


def test_a_push_to_main_proves_a_replaced_suite_shard_before_the_verdict() -> None:
    """Relation 3 on main: merge.yml's ``results`` runs the previous main commit's
    ``superseded.py``, not this checkout's, under ``python3 -I`` with nothing installed, on a
    push to main alone, for each suite job and in the stage jobs' own group, and the verdict
    step after it reads the proof. A proof that cannot be made writes nothing, so the shard
    reads cancelled."""
    text = MERGE_YML.read_text(encoding="utf-8")
    results = yaml.safe_load(text)["jobs"]["results"]
    steps = [str(step.get("run", "")) for step in results["steps"]]
    [proving] = [n for n, run in enumerate(steps) if "superseded.py" in run]
    [judging] = [n for n, run in enumerate(steps) if "--records" in run]
    assert proving < judging, "the verdict runs before the proof it reads"
    assert results["steps"][proving]["if"] == (
        "${{ !cancelled() && github.event_name == 'push' && github.ref == 'refs/heads/main' }}"
    ), "the proof is made off a push to main"
    proof, verdict = steps[proving], steps[judging]
    assert "contents/tools/ci/superseded.py?ref=${BEFORE}" in proof, "not the previous commit's"
    assert results["steps"][proving]["env"]["BEFORE"] == "${{ github.event.before }}"
    assert 'python3 -I "$check" --workflow merge.yml --job "$job"' in proof
    assert f"for job in {' '.join(_SUITE_JOBS)}; do" in proof
    assert f'--group-prefix {_SUITE_GROUP_PREFIX} --out "$RUNNER_TEMP/superseded.json"' in proof
    assert 'if [ -f "$RUNNER_TEMP/superseded.json" ]; then' in verdict
    assert '${proof:+--superseded "$proof"}' in verdict, "the verdict is not given the proof"
    for scope in ("actions", "checks"):
        assert (
            results["permissions"].get(scope) == "read"
        ), f"results cannot read what superseded.py asks the API ({scope})"
    group = (
        "group: merge-${{ github.ref == 'refs/heads/main' && github.run_attempt == '1' && "
        "matrix.id == 'python-suite' && 'suite' || github.ref == 'refs/heads/main' && "
        "github.run_attempt == '1' && matrix.id == 'python-suite-macos' && 'suite' || "
        "github.event.pull_request.number || github.sha }}-${{ matrix.key }}"
    )
    assert group in text, "the stage jobs' suite group is not the one the proof reads"
