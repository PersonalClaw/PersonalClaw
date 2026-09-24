"""Heat-earned promotion, wired (LEARN-R6f / WF2LEA-9 part 1).

`usage.promotion_ready` shipped as a correct multi-gate with NO caller anywhere in the
tree — the exact inert shape this program keeps finding, and the worse half of it: the
bare "surfaced ≥2×" the gate was written to replace was still what the ladder
effectively used, because nothing consulted the replacement.

So these tests are about the WIRE, not the arithmetic. They drive the real cadence
function end to end and read the result out of the queue's own live reader
(`proposals.list_pending`, which is what `/api/learning/proposals` serves), and they
assert the call site exists in `history.py` so the wire cannot be quietly cut.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from personalclaw.learning import curator, proposals
from personalclaw.learning.proposals import Kind
from personalclaw.learning.usage import UsageStore


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Proposals are files under the home — this must never be the real one."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.learning.proposals._dir", lambda: tmp_path / "proposals")


@pytest.fixture
def store(tmp_path):
    s = UsageStore(tmp_path)
    yield s
    s.close()


def _days(n: int) -> list[str]:
    """An active-day list covering the last n days, so idle time is measurable."""
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(n)]


def _earn(store: UsageStore, entity: str, *, kind: str = "template", contexts=("a", "b", "c")):
    """Drive the store through enough real events to pass the multi-gate.

    `immediate=True` per event because the store's reinforcement damping halves a
    repeat inside the hour — which is correct behaviour, and would otherwise make this
    fixture record three runs as two.
    """
    for ctx in contexts:
        for event in ("surfaced", "run", "run_success"):
            store.record(kind=kind, entity=entity, event=event, context=ctx, immediate=True)


# ── the gate, driven through the store ──


def test_an_earning_entity_becomes_a_suggestion(store):
    _earn(store, "weekly-review")
    sugs = curator.promotion_suggestions(store.list_kind("template"), active_dates=_days(3))
    assert [s.entity for s in sugs] == ["weekly-review"]
    assert sugs[0].uses == 3 and sugs[0].contexts == 3
    assert "multi-gate" in sugs[0].why


def test_one_busy_afternoon_is_not_evidence(store):
    """Usage in a SINGLE context fails the diversity gate — the whole point of it."""
    _earn(store, "one-place", contexts=("a", "a", "a", "a"))
    assert curator.promotion_suggestions(store.list_kind("template"), active_dates=_days(3)) == []


def test_too_few_uses_is_not_evidence(store):
    _earn(store, "barely", contexts=("a", "b"))
    assert curator.promotion_suggestions(store.list_kind("template"), active_dates=_days(3)) == []


def test_a_pinned_entity_is_a_settled_question(store):
    _earn(store, "pinned-one")
    store.set_flags("template", "pinned-one", pinned=True)
    assert curator.promotion_suggestions(store.list_kind("template"), active_dates=_days(3)) == []


def test_user_authored_entities_are_never_offered(store):
    _earn(store, "mine")
    store.set_flags("template", "mine", source_type="user")
    assert curator.promotion_suggestions(store.list_kind("template"), active_dates=_days(3)) == []


# ── the wire: writer → the queue's live reader ──


def test_a_suggestion_reaches_the_proposal_inbox(store):
    """The live reader is `list_pending` — what `/api/learning/proposals` serves."""
    _earn(store, "weekly-review")
    sugs = curator.promotion_suggestions(store.list_kind("template"), active_dates=_days(3))
    assert curator.file_promotion_suggestions(sugs) == 1

    pending = proposals.list_pending()
    rows = [p for p in pending if p.target == "weekly-review"]
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == Kind.TIER_MIGRATION.value, "the kind that had no writer now has one"
    assert "3 use(s) across 3 distinct context(s)" in row.body, "the evidence must travel"
    assert "promotion" in row.tags


def test_filing_twice_does_not_nag(store):
    """A daily cadence must reinforce a pending row, not stack duplicates."""
    _earn(store, "weekly-review")
    sugs = curator.promotion_suggestions(store.list_kind("template"), active_dates=_days(3))
    curator.file_promotion_suggestions(sugs)
    curator.file_promotion_suggestions(sugs)
    assert len([p for p in proposals.list_pending() if p.target == "weekly-review"]) == 1


def test_dry_run_files_nothing(store):
    _earn(store, "weekly-review")
    sugs = curator.promotion_suggestions(store.list_kind("template"), active_dates=_days(3))
    assert curator.file_promotion_suggestions(sugs, dry_run=True) == 0
    assert proposals.list_pending() == []


def test_nothing_is_ever_auto_promoted(store):
    """The mechanism ends at the queue. A proposal is pending, never installed."""
    _earn(store, "weekly-review")
    curator.file_promotion_suggestions(
        curator.promotion_suggestions(store.list_kind("template"), active_dates=_days(3))
    )
    row = [p for p in proposals.list_pending() if p.target == "weekly-review"][0]
    assert row.status == "pending"


# ── the call site ──


def test_the_consolidation_tick_calls_the_gate():
    """An AST assertion, because the wire is the deliverable.

    Testing the functions alone would pass exactly the state this atom found: a
    correct gate, fully tested, that no cadence runs.
    """
    src = Path("src/personalclaw/history.py").read_text()
    tree = ast.parse(src)
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_run_learning_curator"
    )
    called = {
        node.func.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "promotion_suggestions" in called
    assert "file_promotion_suggestions" in called
