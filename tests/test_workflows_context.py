"""Context lifecycle for long-horizon nodes (Slice 10b, WF2-R6).

The templates this engine ships — `deep-research`, `audit-sweep` — are exactly the shapes where
compaction alone demonstrably fails. Compaction keeps the WHAT and drops the WHY, so a compacted
loop re-litigates settled decisions, re-reads files it already verified, and reports confident
conclusions built on summaries of summaries.

Three mechanisms, each closing a different hole:

* **handoffs** — an iteration writes where it got to; the next STARTS from that rather than from a
  compacted transcript. Denser and more honest, because it was written by the iteration that did
  the work while it still remembered;
* **carryover buckets** — typed, bounded, deduped facts that survive any reset. Prose degrades
  under summarization ("I checked the auth module" loses the line numbers); structure does not;
* **decision records** — `{choice, reason, rejected, constraints}`. The rejected alternatives are
  load-bearing: without them a resumed run re-proposes the option already dismissed.

Everything is journaled, which is what makes rewind and resume replay it instead of reconstructing
a summary — and everything is bounded, because an unbounded bucket is a transcript with extra steps
and would reintroduce the context exhaustion it exists to prevent.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.context import (
    MAX_BUCKET_ITEMS,
    MAX_HANDOFF_FIELD,
    SESSION_CONTINUOUS,
    SESSION_FRESH,
    Carryover,
    Decision,
    Handoff,
    render_context,
    session_policy,
)
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.journal import CARRYOVER, DECISION, HANDOFF, LEDGER_KINDS, ledger
from personalclaw.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


class TestSessionPolicy:
    def test_fresh_is_the_default(self) -> None:
        """The long-horizon iterated shapes are this engine's common case, and `continuous` is the
        choice that needs justifying — a continuous session across twenty iterations is precisely
        where compaction fails."""
        assert session_policy({}) == SESSION_FRESH
        assert session_policy(None) == SESSION_FRESH

    def test_continuous_is_honoured_when_declared(self) -> None:
        assert session_policy({"session": "continuous"}) == SESSION_CONTINUOUS

    def test_a_typo_reads_as_FRESH(self) -> None:
        """The safe direction: a typo'd policy that silently kept a session alive is the failure
        this mechanism exists to prevent."""
        assert session_policy({"session": "contnuous"}) == SESSION_FRESH
        assert session_policy({"session": ""}) == SESSION_FRESH


class TestHandoff:
    def test_the_four_fields_round_trip(self) -> None:
        h = Handoff(
            verified_state="auth.py:40-88 reviewed",
            changes="null check at line 52",
            unverified="the OAuth path",
            next_action="review handlers.py",
        )
        assert Handoff.from_dict(h.to_dict()) == h

    def test_an_empty_handoff_renders_NOTHING(self) -> None:
        """A heading followed by four blank fields teaches a model that this section is noise,
        which is how the whole mechanism stops working."""
        assert Handoff().empty is True
        assert render_context(handoff=Handoff()) == ""

    def test_the_unverified_field_is_labelled_as_a_WARNING(self) -> None:
        """ "NOT verified" has to read as a caution, not as more state — it is the field that stops
        an iteration reporting success over an unexamined gap."""
        text = Handoff(unverified="the OAuth path was never reached").render()
        assert "do not assume" in text.lower()

    def test_a_long_field_is_clipped(self) -> None:
        """Fifty iterations of unbounded handoffs would exhaust the context handoffs exist to
        protect."""
        h = Handoff(verified_state="x" * (MAX_HANDOFF_FIELD * 3))
        rendered = h.to_dict()["verified_state"]
        assert len(rendered) <= MAX_HANDOFF_FIELD
        assert rendered.endswith("…")

    def test_a_partial_handoff_renders_only_what_it_has(self) -> None:
        text = Handoff(next_action="run the tests").render()
        assert "run the tests" in text
        assert "Verified so far" not in text


class TestCarryover:
    def test_buckets_merge_across_iterations(self) -> None:
        """An iteration that touched one file must not erase the nine a previous one verified."""
        first = Carryover(files_touched=[{"path": "a.py"}], verified=["a is safe"])
        second = Carryover(files_touched=[{"path": "b.py"}], verified=["b is safe"])
        merged = first.merge(second)
        assert {f["path"] for f in merged.files_touched} == {"a.py", "b.py"}
        assert merged.verified == ["a is safe", "b is safe"]

    def test_merge_does_not_mutate_either_side(self) -> None:
        """The caller usually holds the previous iteration's carryover; mutating it would make a
        rewind replay a bucket that had already absorbed the future."""
        first = Carryover(verified=["one"])
        first.merge(Carryover(verified=["two"]))
        assert first.verified == ["one"]

    def test_a_re_touched_file_keeps_the_NEWER_span(self) -> None:
        """Last wins: a file touched again has a newer line span, and keeping the first would make
        the carryover progressively less accurate the longer a run went on."""
        merged = Carryover(files_touched=[{"path": "a.py", "lines": "1-10"}]).merge(
            Carryover(files_touched=[{"path": "a.py", "lines": "1-99"}])
        )
        assert merged.files_touched == [{"path": "a.py", "lines": "1-99"}]

    def test_duplicate_claims_are_deduped(self) -> None:
        merged = Carryover(verified=["same"]).merge(Carryover(verified=["same", "new"]))
        assert merged.verified == ["same", "new"]

    def test_a_bucket_is_BOUNDED(self) -> None:
        """An unbounded bucket is a transcript with extra steps — it would reintroduce the context
        exhaustion the mechanism exists to prevent."""
        big = Carryover(verified=[f"claim {i}" for i in range(MAX_BUCKET_ITEMS * 3)])
        merged = Carryover().merge(big)
        assert len(merged.verified) == MAX_BUCKET_ITEMS

    def test_the_bound_keeps_the_NEWEST_entries(self) -> None:
        """Recency is the best cheap proxy for relevance to the next iteration."""
        big = Carryover(verified=[f"claim {i}" for i in range(MAX_BUCKET_ITEMS + 5)])
        merged = Carryover().merge(big)
        assert merged.verified[-1] == f"claim {MAX_BUCKET_ITEMS + 4}"

    def test_a_file_entry_with_no_path_is_dropped(self) -> None:
        """A path is the only thing that makes the entry addressable."""
        merged = Carryover().merge(Carryover(files_touched=[{"lines": "1-2"}, {"path": "ok.py"}]))
        assert [f["path"] for f in merged.files_touched] == ["ok.py"]

    def test_line_spans_survive_rendering(self) -> None:
        """The whole point of structure over prose: a summary would lose these."""
        text = Carryover(files_touched=[{"path": "auth.py", "lines": "40-88"}]).render()
        assert "auth.py:40-88" in text


class TestDecision:
    def test_rejected_alternatives_are_NAMED_not_counted(self) -> None:
        """ "3 alternatives rejected" is exactly the compaction artifact this record exists to
        prevent — a resumed run would re-propose one of them."""
        text = Decision(
            choice="use the existing store",
            reason="a second store needs its own migration",
            rejected=["a new sqlite file", "an in-memory cache"],
        ).render()
        assert "a new sqlite file" in text
        assert "in-memory cache" in text

    def test_both_field_spellings_are_accepted(self) -> None:
        """The journal writes `rejected_alternatives` (the plan's name); a model authoring one
        naturally writes `rejected`. Refusing either would silently drop the load-bearing field."""
        assert Decision.from_dict({"choice": "x", "rejected": ["a"]}).rejected == ["a"]
        assert Decision.from_dict({"choice": "x", "rejected_alternatives": ["b"]}).rejected == ["b"]

    def test_a_choiceless_decision_is_empty(self) -> None:
        assert Decision(reason="because").empty is True

    def test_decisions_are_labelled_as_CONSTRAINTS(self) -> None:
        """A reader who learns them as trivia will re-litigate them; one who learns them as
        constraints will not."""
        text = render_context(decisions=[Decision(choice="x")])
        assert "do not re-litigate" in text.lower()


class TestRenderOrder:
    def test_decisions_come_before_the_handoff(self) -> None:
        """They are CONSTRAINTS: a reader who learns them last has already started planning
        around their absence."""
        text = render_context(
            handoff=Handoff(next_action="do the thing"),
            decisions=[Decision(choice="the settled choice")],
        )
        assert text.index("settled choice") < text.index("do the thing")

    def test_the_next_action_lands_LAST(self) -> None:
        """It is what the reader should act on immediately after finishing the block."""
        text = render_context(
            handoff=Handoff(verified_state="state", next_action="the next move"),
            carryover=Carryover(verified=["a fact"]),
        )
        assert text.rstrip().endswith("the next move")

    def test_nothing_to_say_renders_nothing(self) -> None:
        assert render_context() == ""
        assert render_context(handoff=Handoff(), carryover=Carryover(), decisions=[]) == ""


# ── the engine wiring ───────────────────────────────────────────────────────


def _completion_recorder(prompts: list[str]):
    """A fake completion that records its prompt and hands back a full context payload."""

    async def completion(prompt, use_case=None, output_type=None):
        prompts.append(prompt)
        i = len(prompts)
        return json.dumps(
            {
                "handoff": {
                    "verified_state": f"iteration {i} verified auth.py:40-88",
                    "changes": f"edit {i}",
                    "unverified": "the OAuth path",
                    "next_action": f"do step {i + 1}",
                },
                "carryover": {"files_touched": [{"path": f"f{i}.py", "lines": "1-9"}]},
                "decision": {
                    "choice": f"approach {i}",
                    "reason": "cheaper",
                    "rejected_alternatives": ["the other way"],
                },
            }
        )

    return completion


def _loop_spec(*, session: str = "fresh", n: int = 3) -> dict:
    return {
        "name": "ctx",
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "counted", "n": n, "session": session},
            "body": {
                "kind": "infer",
                "id": "w",
                "config": {
                    "prompt": "Do the work.",
                    "model_tier": "fast",
                    "schema": {"handoff": "object"},
                },
            },
        },
    }


async def _run(spec: dict, prompts: list[str]) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name="ctx"))
    store.write_spec(run.id, spec)
    controller = RunController(
        run, spec, services=EngineServices(completion=_completion_recorder(prompts))
    )
    assert await controller.run_to_completion(timeout=30) == RunStatus.COMPLETE
    return run


class TestEngineWiring:
    async def test_a_fresh_iteration_RECEIVES_the_previous_handoff(self) -> None:
        """This is what makes `session: fresh` mean something. Without it the policy is a label —
        the iteration starts clean and also starts BLIND, which is worse than the continuous
        session it replaced."""
        prompts: list[str] = []
        await _run(_loop_spec(), prompts)
        assert len(prompts) == 3
        assert "iteration 2 verified auth.py:40-88" in prompts[-1]
        assert "do step 3" in prompts[-1]

    async def test_the_FIRST_iteration_gets_no_block(self) -> None:
        """There is no previous iteration to inherit from, and an empty heading teaches a model
        the section is noise."""
        prompts: list[str] = []
        await _run(_loop_spec(), prompts)
        assert prompts[0].strip() == "Do the work."

    async def test_carryover_ACCUMULATES_across_iterations(self) -> None:
        prompts: list[str] = []
        await _run(_loop_spec(), prompts)
        assert "f1.py:1-9" in prompts[-1]
        assert "f2.py:1-9" in prompts[-1]

    async def test_every_prior_decision_is_carried(self) -> None:
        """A decision made in iteration 1 must still constrain iteration 3."""
        prompts: list[str] = []
        await _run(_loop_spec(), prompts)
        assert "approach 1" in prompts[-1]
        assert "approach 2" in prompts[-1]

    async def test_the_context_is_PREPENDED_not_appended(self) -> None:
        """A model that reads the task first has already begun planning without the constraints."""
        prompts: list[str] = []
        await _run(_loop_spec(), prompts)
        assert prompts[-1].index("SETTLED DECISIONS") < prompts[-1].index("Do the work.")

    async def test_a_CONTINUOUS_session_gets_no_injected_block(self) -> None:
        """A continuous session already holds the previous iteration in its transcript; prepending
        a handoff would say everything twice."""
        prompts: list[str] = []
        await _run(_loop_spec(session="continuous"), prompts)
        assert all("HANDOFF" not in p for p in prompts)

    async def test_all_three_record_kinds_are_journaled(self) -> None:
        """Journaled, not held in memory — that is what makes rewind and resume replay them."""
        prompts: list[str] = []
        run = await _run(_loop_spec(), prompts)
        records = ledger(run.id, kinds={HANDOFF, CARRYOVER, DECISION})
        counts = {
            k: sum(1 for r in records if r.get("kind") == k) for k in (HANDOFF, CARRYOVER, DECISION)
        }
        assert counts == {HANDOFF: 3, CARRYOVER: 3, DECISION: 3}

    async def test_the_record_kinds_are_in_LEDGER_KINDS(self) -> None:
        """The flywheel reads that subset; a kind absent from it is emitted and then ignored."""
        assert {HANDOFF, CARRYOVER, DECISION} <= LEDGER_KINDS

    async def test_the_journaled_carryover_is_the_MERGED_state(self) -> None:
        """So a resume can take the last record and be complete, rather than replaying and
        re-merging every one."""
        prompts: list[str] = []
        run = await _run(_loop_spec(), prompts)
        records = [r for r in ledger(run.id, kinds={CARRYOVER})]
        last = records[-1]
        assert len(last["files_touched"]) == 3

    async def test_a_node_that_hands_over_NOTHING_journals_nothing(self) -> None:
        """A fabricated handoff is worse than none — the next iteration would trust it."""

        async def silent(prompt, use_case=None, output_type=None):
            return json.dumps({"result": "done"})

        spec = _loop_spec()
        run = store.create(WorkflowRun(id="", workflow_name="ctx"))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices(completion=silent))
        await controller.run_to_completion(timeout=30)
        assert ledger(run.id, kinds={HANDOFF, CARRYOVER, DECISION}) == []

    async def test_a_non_dict_output_is_tolerated(self) -> None:
        """A stage returning prose is normal; it simply hands nothing over."""

        async def prose(prompt, use_case=None, output_type=None):
            return "I did the thing."

        spec = {
            "name": "ctx",
            "root": {
                "kind": "loop",
                "id": "l",
                "config": {"mode": "counted", "n": 2},
                "body": {
                    "kind": "infer",
                    "id": "w",
                    "config": {"prompt": "go", "model_tier": "fast"},
                },
            },
        }
        run = store.create(WorkflowRun(id="", workflow_name="ctx"))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices(completion=prose))
        assert await controller.run_to_completion(timeout=30) == RunStatus.COMPLETE


class TestRehydration:
    async def test_a_RESUMED_run_rebuilds_its_context_from_the_ledger(self) -> None:
        """The whole reason these are journaled. A resumed run that lost them would restart blind,
        re-deriving what a previous iteration already verified."""
        prompts: list[str] = []
        run = await _run(_loop_spec(), prompts)

        # A fresh controller over the same run — what a restart produces.
        revived = RunController(
            store.get(run.id),
            _loop_spec(),
            services=EngineServices(completion=_completion_recorder([])),
        )
        await revived._prepare()
        assert revived._handoffs, "handoffs were not rehydrated"
        assert "root" in next(iter(revived._handoffs)), list(revived._handoffs)
        carried = next(iter(revived._carryover.values()))
        assert len(carried.files_touched) == 3
        assert len(next(iter(revived._decisions.values()))) == 3

    async def test_rehydration_keeps_the_LAST_handoff_per_container(self) -> None:
        """Only the most recent one is the state to resume from; an older one would send the next
        iteration back in time."""
        prompts: list[str] = []
        run = await _run(_loop_spec(), prompts)
        revived = RunController(
            store.get(run.id), _loop_spec(), services=EngineServices(completion=None)
        )
        await revived._prepare()
        handoff = next(iter(revived._handoffs.values()))
        assert "iteration 3" in handoff.verified_state

    async def test_an_unreadable_ledger_does_not_block_a_resume(self, monkeypatch) -> None:
        """It would start context-blind, which is worse than nothing but far better than a run that
        will not start at all."""
        spec = _loop_spec()
        run = store.create(WorkflowRun(id="", workflow_name="ctx"))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices(completion=None))
        monkeypatch.setattr(
            "personalclaw.workflows.journal.ledger",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk gone")),
        )
        controller._rehydrate_context()  # must not raise
        assert controller._handoffs == {}
