"""Consumer-liveness detection — a work unit whose output nobody reads (PP-10).

`PLATFORM-PRIMITIVES` §3. Every watchdog in this repo measures PRODUCER health; nothing asked
whether the output was ever READ. This covers the sweep that does, and the half that makes it a
detector rather than a nag.

Driven end to end against the REAL machinery — the real `publish:` producer
(`workflows/engine._open_publish_outcome`), the real Run Ledger, the real `outcome_resolver`, the
real native artifact provider and pin store, the real proposal queue — under a tmp home. Nothing
here asserts on a mock, because every defect this atom can have is a wiring defect.

What is asserted, and why each clause is load-bearing:

* **the silent half FIRST.** A sweep that fires on a never-opened artifact proves almost nothing —
  anything fires. The assertions that carry the atom are the ones where the artifact WAS touched
  (opened in the dashboard, pulled into a chat turn, pinned, edited) and the sweep says nothing;
* the firing half: three matured cycles with no touch → one `retirement` PROPOSAL;
* **it never acts.** No code path pauses, cancels or retires anything — asserted functionally (the
  runs are untouched after a firing sweep) AND structurally (an AST rail over the module, so a
  later edit that adds a stop cannot pass);
* the horizon: a fresh cycle cannot fire, at two levels — the resolver leaves an in-horizon
  question open, and `dormancy_verdict` refuses a window shorter than `DORMANCY_CYCLES`;
* an UNREADABLE cycle is `INSUFFICIENT`, never `DORMANT` — "we could not tell" must not accumulate
  into "nobody reads this";
* the producer's own writes (`created`, an agent `iterated`) are NOT touches, or every work unit
  would look consumed by itself;
* it reuses `PP-9`'s record — no second counter: the sweep reads `outcome_resolved` rows and the
  horizon comes off the `pending_outcome`;
* it resolves with NO vector store, which is what changed when the publish question moved from a
  semantic-memory metric nothing wrote to a consumption source;
* not nagging: a second sweep reinforces the one row instead of stacking a second, and a REJECTED
  proposal is never re-filed.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest

from personalclaw.artifacts import registry
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.learning import consumer_liveness, outcome_resolver
from personalclaw.learning import proposals as P
from personalclaw.ledger import outcomes
from personalclaw.memory_service import MemoryService
from personalclaw.workflows import engine as engine_mod
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import pinned
from personalclaw.workflows import store as store_mod
from personalclaw.workflows.models import RunStatus, WorkflowRun

#: A day in seconds, so the "matured" clock skips in units the horizon is expressed in.
_DAY = 86400.0

#: The publish horizon the engine declares (7 days). Read off the constant rather than retyped so a
#: change to the engine's generosity does not quietly make these tests assert the old number.
_HORIZON = engine_mod.PUBLISH_CONSUMPTION_HORIZON_SECS


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate the workflows store, proposals, artifact provider and pin store under a tmp home.

    `workflows.store` binds `config_dir` at import and `NativeArtifactProvider` resolves its root
    ONCE in `__init__` and is then cached in the registry, so neither is reachable by patching the
    loader symbol alone: the env var isolates the import-bound store, and the provider is registered
    explicitly against the tmp root and unregistered on teardown so it never leaks into the next
    test.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_surface_in_inbox", lambda prop: None)
    monkeypatch.setattr(P, "_resolve_inbox_item", lambda pid, status: None)
    registry.register_provider(NativeArtifactProvider(tmp_path / "artifacts"))
    yield tmp_path
    registry.unregister_provider("native")


# ── driving one publish cycle of a work unit ──


def _provider():
    provider = registry.get_provider("native")
    assert provider is not None
    return provider


def _cycle(unit: str, slug: str) -> WorkflowRun:
    """One cycle of `unit`: a run that publishes `slug` and opens the real publish question.

    Goes through `engine._open_publish_outcome` rather than hand-journalling a `pending_outcome`, so
    a change to the producer's declared metric/source/horizon breaks these tests instead of leaving
    them asserting a shape nothing writes.
    """
    run = store_mod.create(WorkflowRun(id="", workflow_name=unit))
    run.status = RunStatus.COMPLETE
    store_mod.save(run)
    _provider().create(name=f"{slug} body", content="the deliverable", slug=slug, source="workflow")
    engine_mod._open_publish_outcome(run.id, "emit", {"slug": slug, "action": "create"})
    return run


def _questions(run: WorkflowRun) -> list[dict[str, Any]]:
    return journal_mod.ledger(run.id, kinds={journal_mod.PENDING_OUTCOME})


def _grade(runs: list[WorkflowRun], *, after_days: float = 8.0) -> dict[str, int]:
    """Run the real resolver with the clock past every question's horizon.

    A DELIBERATELY vector-store-free service: a consumption-sourced question must grade on a box
    with no embedder, which is exactly what moving off the semantic-memory metric bought.
    """
    opened = min(outcome_resolver._epoch(q["ts"]) or 0.0 for run in runs for q in _questions(run))
    return outcome_resolver.resolve(
        MemoryService.over_vector_store(None), now=opened + after_days * _DAY
    )


def _resolutions(run: WorkflowRun) -> list[dict[str, Any]]:
    return journal_mod.ledger(run.id, kinds={journal_mod.OUTCOME_RESOLVED})


def _dormancy_proposals() -> list[Any]:
    return [
        p
        for p in P.list_pending(kind=P.Kind.RETIREMENT.value)
        if "consumer_liveness" in list(getattr(p, "tags", []) or [])
    ]


# ══ THE SILENT HALF — the assertions that prove this is not a blanket nag ══


def test_an_opened_artifact_keeps_the_sweep_silent(home):
    """THE load-bearing test. Three cycles, every artifact untouched EXCEPT the last, which the user
    opened in the dashboard (`POST /api/artifacts/{slug}/events` → `record_impression`). One reader
    means somebody is reading: the verdict is LIVE and the sweep files nothing.

    Without this clause a sweep that fires on the never-opened case proves nothing — anything fires.
    """
    runs = [_cycle("weekly-digest", f"digest-{i}") for i in range(3)]
    # The one touch: a consumer opened the newest artifact.
    _provider().record_impression("digest-2", by="user", session_id="reader-session")

    _grade(runs)
    report = consumer_liveness.sweep()

    assert report["units"] == 1
    assert report["live"] == 1
    assert report["dormant"] == 0
    assert report["proposed"] == 0
    assert _dormancy_proposals() == []


def test_a_chat_reference_is_a_touch(home):
    """The second live surface: the user pulled the artifact into a chat turn, which is
    `chat_runner`'s `record_impression(by="user", session_id=session.key)`. Same writer, same
    verdict — the sweep must not be coupled to one route."""
    runs = [_cycle("brief", f"brief-{i}") for i in range(3)]
    _provider().record_impression("brief-0", by="user", session_id="chat-session")

    _grade(runs)
    assert consumer_liveness.sweep()["dormant"] == 0
    assert _dormancy_proposals() == []


def test_a_pin_is_a_touch(home):
    """Pinning is the most explicit touch there is — "I care about this now" — and it is written by
    an existing store (`workflows/pinned.py`), not by anything this atom added."""
    runs = [_cycle("scan", f"scan-{i}") for i in range(3)]
    pinned.pin("scan-1")

    _grade(runs)
    assert consumer_liveness.sweep()["dormant"] == 0
    assert _dormancy_proposals() == []


def test_a_versioned_user_edit_is_a_touch(home):
    """A user editing the artifact is a stronger read than opening it: the `edited` event the update
    path writes counts.

    `snapshot=True` because that is the only branch of `update()` that appends a timeline event — an
    un-snapshotted content write leaves NO trace, so it is invisible to this sweep. That is the
    existing writer's behaviour, recorded here rather than worked around: changing artifact event
    semantics to widen the signal is a separate change with its own consumers.
    """
    runs = [_cycle("notes", f"notes-{i}") for i in range(3)]
    _provider().update("notes-0", content="the user rewrote a line", snapshot=True, actor="user")

    _grade(runs)
    assert consumer_liveness.sweep()["dormant"] == 0
    assert _dormancy_proposals() == []


def test_the_producers_own_writes_are_not_touches(home):
    """`created` and an agent `iterated` are the work unit writing its own output. Counting them
    would make every work unit look consumed by itself — a liveness signal that can never be
    false is not a signal."""
    runs = [_cycle("selfread", f"selfread-{i}") for i in range(3)]
    for index in range(3):
        _provider().update(
            f"selfread-{index}", content=f"revision {index}", snapshot=True, actor="agent"
        )
    events = {e.type for e in _provider().get("selfread-0").events}
    assert events == {"created", "iterated"}, events

    _grade(runs)
    assert consumer_liveness.sweep()["dormant"] == 1


# ══ THE FIRING HALF ══


def test_three_unread_cycles_are_reported_as_a_proposal(home):
    """The atom's first half: a work unit whose last three published artifacts nobody opened is
    reported — once, as a `retirement` PROPOSAL naming both options."""
    runs = [_cycle("dead-monitor", f"dead-{i}") for i in range(3)]

    graded = _grade(runs)
    assert graded["resolved"] == 3 and graded["inconclusive"] == 0
    assert all(r["measured"] == outcomes.UNCONSUMED for run in runs for r in _resolutions(run))

    report = consumer_liveness.sweep()
    assert report == {"units": 1, "dormant": 1, "live": 0, "insufficient": 0, "proposed": 1}

    (proposal,) = _dormancy_proposals()
    assert proposal.kind == P.Kind.RETIREMENT.value
    assert proposal.target == "consumer_liveness.dead-monitor"
    # Both options, explicitly, because only the user can tell the two facts apart.
    assert "PAUSE" in proposal.body and "RETIRE" in proposal.body
    assert "Nothing has been stopped" in proposal.body
    # The volatile evidence rides in refs (outside the fingerprint) so the body stays stable.
    assert {f"artifact:dead-{i}" for i in range(3)} <= set(proposal.evidence_refs)


def test_the_publish_question_grades_without_a_vector_store(home):
    """PP-9 opened the publish question against a semantic-memory metric nothing wrote, so it always
    closed `inconclusive`. PP-10's consumption source is read off the artifact, so the bet grades
    for real on a box with no embedder — asserted here because `_grade` passes a null store."""
    run = _cycle("one-shot", "one-shot-a")
    (question,) = _questions(run)
    assert question["metric_source"] == outcomes.SOURCE_CONSUMPTION
    assert question["metric"] == outcomes.consumption_metric("one-shot-a")
    assert question["horizon_secs"] == pytest.approx(_HORIZON)

    _grade([run])
    (resolved,) = _resolutions(run)
    assert resolved["resolution"] == outcomes.MEASURED
    assert resolved["measured"] == outcomes.UNCONSUMED


def test_the_sweep_reads_pp9s_record_and_adds_no_counter(home):
    """No second counter and no parallel store: every input the sweep uses is a `pending_outcome` /
    `outcome_resolved` row, and the horizon it reports comes off the question rather than from a
    number this module keeps."""
    runs = [_cycle("horizoned", f"h-{i}") for i in range(3)]
    _grade(runs)
    consumer_liveness.sweep()

    (proposal,) = _dormancy_proposals()
    # The horizon in the body came off the `pending_outcome`, not from a number this module keeps.
    assert f"{_HORIZON / _DAY:.0f} days" in proposal.body
    # The sweep persists NOTHING of its own: the home holds the stores that existed before it, and
    # no state file, counter or catalog named after it. A second store is the duplication PP-9/PP-10
    # exist to remove, so its absence is asserted rather than assumed.
    written = {entry.name for entry in home.iterdir()}
    assert not {name for name in written if "liveness" in name or "dormanc" in name}, written

    # Running it twice writes nothing new either — the verdict is re-derived from the ledger.
    before = sorted(str(path.relative_to(home)) for path in home.rglob("*"))
    consumer_liveness.sweep()
    assert sorted(str(path.relative_to(home)) for path in home.rglob("*")) == before


# ══ THE HORIZON — a fresh work unit cannot fire ══


def test_a_fresh_work_unit_does_not_fire(home):
    """Three cycles that published minutes ago. The resolver leaves every question OPEN inside its
    horizon, so the sweep has nothing graded to judge and stays silent. This is the "nobody looked
    YET" case, and calling it dormant is the exact mistake the atom forbids."""
    runs = [_cycle("fresh", f"fresh-{i}") for i in range(3)]

    graded = _grade(runs, after_days=1.0)  # inside the 7-day horizon
    assert graded == {"resolved": 0, "inconclusive": 0, "pending": 3, "proposed": 0}
    assert all(_resolutions(run) == [] for run in runs)

    report = consumer_liveness.sweep()
    assert report == {"units": 0, "dormant": 0, "live": 0, "insufficient": 0, "proposed": 0}
    assert _dormancy_proposals() == []


def test_two_unconsumed_cycles_are_not_enough(home):
    """The sweep's own floor, independent of the resolver's: `DORMANCY_CYCLES` matured unconsumed
    cycles, not one and not two. One unopened deliverable is a busy week; a sweep that fired on it
    would train the user to skim past the surface it reports on."""
    runs = [_cycle("young", f"young-{i}") for i in range(2)]
    _grade(runs)

    report = consumer_liveness.sweep()
    assert report["insufficient"] == 1
    assert report["dormant"] == 0
    assert _dormancy_proposals() == []


def test_an_unreadable_cycle_is_insufficient_not_dormant(home):
    """The artifact was deleted, so consumption is UNKNOWABLE — the question closes `inconclusive`.
    That must not count as evidence that nobody looked: three cycles, one unreadable, verdict
    INSUFFICIENT. "We could not tell" accumulating into "nobody reads this" is how a sweep starts
    reporting fabrications."""
    runs = [_cycle("patchy", f"patchy-{i}") for i in range(3)]
    _provider().delete("patchy-1")

    graded = _grade(runs)
    assert graded["resolved"] == 2 and graded["inconclusive"] == 1

    report = consumer_liveness.sweep()
    assert report["insufficient"] == 1
    assert report["dormant"] == 0
    assert _dormancy_proposals() == []


# ══ IT NEVER ACTS ══

#: Names that would mean the sweep decided on its own. The atom forbids every one of them: only the
#: user knows whether "nobody looked yet" or "nobody will ever look".
_FORBIDDEN_CALLS = frozenset(
    {
        "pause",
        "unpause",
        "resume",
        "cancel",
        "abandon",
        "retire",
        "stop",
        "disable",
        "unschedule",
        "delete",
        "remove",
        "save",
        "abort",
    }
)


def test_the_sweep_module_cannot_call_anything_that_stops_a_work_unit():
    """A STRUCTURAL rail, not a reading of today's code: the sweep's whole licence is that it
    proposes. An edit that later reaches for a pause has to fail here rather than ship as a control
    that quietly acts, so the check is over the module's call graph and needs no home fixture."""
    source = pathlib.Path(consumer_liveness.__file__).read_text(encoding="utf-8")
    called: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            called.add(func.attr)
        elif isinstance(func, ast.Name):
            called.add(func.id)
    offenders = sorted(called & _FORBIDDEN_CALLS)
    assert offenders == [], f"consumer_liveness may only propose; it calls {offenders}"


def test_a_firing_sweep_leaves_the_work_unit_running(home):
    """The functional half of the same guarantee: the sweep fires, files its proposal, and every run
    is byte-identical afterwards. Nothing was paused, nothing was retired, no status moved."""
    runs = [_cycle("still-running", f"sr-{i}") for i in range(3)]
    _grade(runs)
    before = {run.id: store_mod.get(run.id).to_dict() for run in runs}  # type: ignore[union-attr]

    assert consumer_liveness.sweep()["proposed"] == 1

    after = {run.id: store_mod.get(run.id).to_dict() for run in runs}  # type: ignore[union-attr]
    assert after == before
    assert all(store_mod.get(run.id).status == RunStatus.COMPLETE for run in runs)


# ══ NOT NAGGING ══


def test_a_second_sweep_reinforces_rather_than_stacking(home):
    """Idempotency without a state file: the body is stable per work unit, so the queue's own
    fingerprint cascade REINFORCES the pending row. A sweep that filed a fresh proposal every
    curator tick would be the nag this atom exists to avoid."""
    runs = [_cycle("repeat", f"repeat-{i}") for i in range(3)]
    _grade(runs)

    assert consumer_liveness.sweep()["proposed"] == 1
    assert consumer_liveness.sweep()["proposed"] == 1  # reinforced, not a second row

    (proposal,) = _dormancy_proposals()
    assert proposal.reinforcements >= 2


def test_a_rejected_dormancy_finding_is_never_re_filed(home):
    """ "I know, leave it alone" has to stick. Rejecting the proposal records the decision against
    its fingerprint, and the stable body means the next sweep hashes to the same one and skips."""
    runs = [_cycle("accepted-risk", f"ar-{i}") for i in range(3)]
    _grade(runs)
    consumer_liveness.sweep()
    (proposal,) = _dormancy_proposals()
    P.reject(proposal.id)

    assert consumer_liveness.sweep()["proposed"] == 0
    assert _dormancy_proposals() == []


# ══ THE PURE VERDICT ══


def _res(resolution: str, measured: float | None) -> dict[str, Any]:
    return {"resolution": resolution, "measured": measured}


@pytest.mark.parametrize(
    "resolutions,expected",
    [
        ([], outcomes.INSUFFICIENT),
        ([_res(outcomes.MEASURED, 0.0)], outcomes.INSUFFICIENT),
        ([_res(outcomes.MEASURED, 0.0)] * 2, outcomes.INSUFFICIENT),
        ([_res(outcomes.MEASURED, 0.0)] * 3, outcomes.DORMANT),
        ([_res(outcomes.MEASURED, 0.0)] * 9, outcomes.DORMANT),
        # One touch anywhere in the window wins: a fortnightly reader is a reader.
        (
            [
                _res(outcomes.MEASURED, 0.0),
                _res(outcomes.MEASURED, 1.0),
                _res(outcomes.MEASURED, 0.0),
            ],
            outcomes.LIVE,
        ),
        # A single touch is enough on its own — no cycle floor applies to LIVE.
        ([_res(outcomes.MEASURED, 1.0)], outcomes.LIVE),
        # An unreadable cycle is never evidence of dormancy.
        (
            [_res(outcomes.MEASURED, 0.0)] * 2 + [_res(outcomes.INCONCLUSIVE, None)],
            outcomes.INSUFFICIENT,
        ),
        # A touch OUTSIDE the window does not rescue a dormant unit — only the last N count.
        ([_res(outcomes.MEASURED, 1.0)] + [_res(outcomes.MEASURED, 0.0)] * 3, outcomes.DORMANT),
    ],
)
def test_dormancy_verdict_is_three_states(resolutions, expected):
    """Three verdicts, not two: "nobody looked yet" and "we could not tell" are both different
    facts from "nobody reads this", and collapsing either into DORMANT is how the control gets teeth
    it has not earned."""
    assert outcomes.dormancy_verdict(resolutions) == expected


def test_the_consumption_metric_round_trips():
    """The resolution record carries the metric, not the slug, so the sweep recovers the artifact
    from the metric name. Built and parsed in one place — two hand-written format strings would
    drift and the sweep would silently lose its evidence refs."""
    assert (
        outcomes.slug_from_metric(outcomes.consumption_metric("weekly-digest")) == "weekly-digest"
    )
    assert outcomes.slug_from_metric("lesson.metric.plan_a_win") == ""
    assert outcomes.slug_from_metric("") == ""
