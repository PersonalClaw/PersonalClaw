"""The personal decision journal (PROACTIVE-ASSISTANT §2, atom PA-4).

Every assertion here is aimed at a CALL SITE rather than at the mechanism it drives. The
question each test answers is "would deleting the caller be caught?":

* the type registration tests would go red if ``decision`` were dropped from
  ``NATIVE_TYPES``, from the HTTP handler's type set, or from the Passthrough graph map —
  each with a vacuity assertion beside it, so a rail that stopped matching anything cannot
  read as clean;
* the trigger tests would go red if ``log_decision`` stopped minting the one-shot, if the
  id stopped being deterministic, or if the workflow it names stopped shipping;
* the lesson tests would go red if ``resolve_decision`` stopped calling ``write_lesson`` —
  including the case where the write is REFUSED, because stamping a soft reference at a
  lesson that does not exist is the failure that would otherwise look like success;
* the tool tests drive ``invoke()`` by NAME, so deleting a handler yields "unknown builtin
  tool" rather than a silently unexercised function.

Stores are tmp_path throughout: the core module takes ``store``/``trigger_store``/``memory``
as parameters, so nothing here needs the real home, a gateway, an embedder or a model.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from personalclaw.decisions import (
    CALIBRATED_GRADES,
    DECISION_DOMAINS,
    DECISION_TYPE,
    MAX_DEFERRALS,
    RESOLUTION_GRADES,
    REVIEW_WORKFLOW,
    DecisionError,
    abandon_decision,
    calibration,
    decision_meta,
    horizon_from_days,
    lesson_text,
    list_decisions,
    log_decision,
    reschedule_review,
    resolve_decision,
    review_trigger_id,
)
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.triggers.store import TriggerStore


@pytest.fixture
def store(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(os.path.join(tmp_path, "knowledge.db"))


@pytest.fixture
def triggers(tmp_path: Path) -> TriggerStore:
    return TriggerStore(base_dir=tmp_path)


@pytest.fixture
def memory(tmp_path: Path):
    from personalclaw.memory_service import MemoryService
    from personalclaw.vector_memory import VectorMemoryStore

    vs = VectorMemoryStore(db_path=tmp_path / "memory.db")
    vs.init()
    return MemoryService.over_vector_store(vs)


def _log(store, triggers, **kw) -> dict:
    args = {
        "summary": "Take the contract over the salaried role",
        "content": "Reasoning: optionality now, less security. Stakes: a year of income.",
        "expectation": "I will earn more and regret the lost benefits by month six",
        "confidence": 0.7,
        "domain": "career",
        "review_horizon": horizon_from_days(90),
    }
    args.update(kw)
    return log_decision(store=store, trigger_store=triggers, **args)


# ── the native type, registered end to end ───────────────────────────────────


class TestNativeTypeRegistration:
    def test_decision_is_the_thirteenth_native_type(self) -> None:
        """The provider's typed-create allowlist is the gate `create_typed` checks; a type
        absent from it raises before any storage happens."""
        from personalclaw.knowledge_providers.native import NATIVE_TYPES

        assert DECISION_TYPE in NATIVE_TYPES
        assert len(NATIVE_TYPES) == 13

    def test_the_provider_accepts_a_decision_through_create_typed(self, store) -> None:
        """The ONE true create path. Asserting the provider rather than the store, because the
        store would happily persist any item_type string — the allowlist is the contract."""
        from personalclaw.knowledge_providers.native import NativeKnowledgeProvider

        provider = NativeKnowledgeProvider(store)
        item_id = provider.create_typed(item_type=DECISION_TYPE, title="A call", content="why")
        assert store.get_item(item_id)["item_type"] == DECISION_TYPE

    def test_the_provider_still_refuses_an_unknown_type(self, store) -> None:
        """The vacuity floor for the test above: if the allowlist stopped being consulted,
        `create_typed` would accept anything and the acceptance test would prove nothing."""
        from personalclaw.knowledge_providers.native import NativeKnowledgeProvider

        with pytest.raises(ValueError, match="unknown knowledge type"):
            NativeKnowledgeProvider(store).create_typed(item_type="verdict", title="x")

    def test_a_decision_rides_the_passthrough_graph(self) -> None:
        """The atom's contract. `graph_for` falls back to `DocumentGraph` for anything it does
        not know, so an UNLISTED decision would route through the document reader and merely
        look similar — which is why the type is mapped explicitly."""
        from personalclaw.knowledge.pipeline.graphs import PassthroughGraph, graph_for

        assert isinstance(graph_for(DECISION_TYPE), PassthroughGraph)

    def test_the_passthrough_assertion_can_fail(self) -> None:
        """Vacuity floor: `PassthroughGraph` is not what every type gets, so the assertion
        above is a real discriminator rather than a tautology about the fallback."""
        from personalclaw.knowledge.pipeline.graphs import PassthroughGraph, graph_for

        assert not isinstance(graph_for("pdf"), PassthroughGraph)

    def test_the_http_handler_knows_the_type_but_will_not_author_it(self) -> None:
        """`_KNOWLEDGE_TYPES` is what makes a decision a recognized library type (listing,
        filtering, watched-source validation). `_AUTHORABLE_TYPES` deliberately excludes it:
        an item authored through the generic create would be a decision with no review."""
        from personalclaw.dashboard.handlers.knowledge import (
            _AUTHORABLE_TYPES,
            _KNOWLEDGE_TYPES,
        )

        assert DECISION_TYPE in _KNOWLEDGE_TYPES
        assert DECISION_TYPE not in _AUTHORABLE_TYPES

    def test_the_refusal_names_log_decision_not_the_upload_endpoint(self) -> None:
        """A refusal that sent the caller to /ingest would send them somewhere that cannot
        make a decision — the message is the only guidance they get."""
        from personalclaw.dashboard.handlers.knowledge import (
            _CREATION_PATH,
            _DEFAULT_CREATION_PATH,
        )

        via = _CREATION_PATH.get(DECISION_TYPE, _DEFAULT_CREATION_PATH)
        assert "log_decision" in via
        assert "/ingest" not in via
        # Vacuity floor: the default path is still the upload one, so the mapping above is a
        # real override rather than a rename of the only branch.
        assert "/ingest" in _DEFAULT_CREATION_PATH


# ── log_decision ─────────────────────────────────────────────────────────────


class TestLogDecision:
    def test_it_creates_a_keyword_searchable_knowledge_item(self, store, triggers) -> None:
        """Searchable is asserted through the FTS index rather than by reading the row back:
        an item that exists but was never indexed is invisible to the search the user runs."""
        row = _log(store, triggers, summary="Move the database to Postgres")
        hits = store.search_items_fts("Postgres")
        assert row["id"] in [h["id"] for h in hits]

    def test_the_structured_fields_ride_the_metadata_json(self, store, triggers) -> None:
        """No new column, so `_migrate` stays untouched — the plan's §2.1 constraint."""
        row = _log(store, triggers)
        item = store.get_item(row["id"])
        meta = decision_meta(item)
        assert meta["status"] == "pending"
        assert meta["domain"] == "career"
        assert meta["confidence"] == 0.7
        assert meta["expectation"].startswith("I will earn more")
        # The decision block is NESTED under the item's metadata column, not spread across it,
        # so a future writer of `also_seen_in` cannot collide with a decision field.
        raw = json.loads(json.dumps(item["file_metadata"]))
        assert set(raw) == {"decision"}

    def test_it_mints_exactly_one_review_trigger_at_a_deterministic_id(
        self, store, triggers
    ) -> None:
        """Criterion 3's "exactly one". Counted over the WHOLE store rather than looked up by
        id, because a generated slug would also satisfy a by-id lookup of the row it created."""
        row = _log(store, triggers)
        rows = [r for r in triggers.load() if r.trigger.id.startswith("system:decision-journal")]
        assert len(rows) == 1
        assert rows[0].trigger.id == review_trigger_id(row["id"])
        assert row["reminder_trigger_id"] == review_trigger_id(row["id"])

    def test_the_review_trigger_is_a_one_shot_that_retires_itself(self, store, triggers) -> None:
        """`delete_after_run` is what makes an unanswered decision leave no dormant reminder,
        and it is the substrate's commitment-conversion shape."""
        row = _log(store, triggers)
        trigger = triggers.get(review_trigger_id(row["id"])).trigger
        assert trigger.kind == "clock"
        assert trigger.spec["kind"] == "at"
        assert trigger.spec["delete_after_run"] is True
        assert trigger.enabled is True
        # Armed at creation: a clock trigger with no `next_fire_at` is never surfaced by
        # `service.due_ids`, so an unarmed row is a reminder that never fires.
        assert trigger.next_fire_at

    def test_the_trigger_names_a_workflow_that_actually_ships(self, store, triggers) -> None:
        """The trigger→template link. Renaming the bundled directory, or pointing
        `REVIEW_WORKFLOW` at a template that does not exist, fails HERE rather than at the
        one fire the reminder gets."""
        from personalclaw.workflows.bundled_defs import read_template, template_names

        row = _log(store, triggers)
        trigger = triggers.get(review_trigger_id(row["id"])).trigger
        inline = trigger.workflow["inline"]
        assert inline["provider"] == "run-workflow"
        assert inline["config"]["workflow"] == REVIEW_WORKFLOW
        assert REVIEW_WORKFLOW in template_names()
        assert read_template(REVIEW_WORKFLOW) is not None

    def test_the_card_carries_the_prediction_the_user_stated(self, store, triggers) -> None:
        """The review has to quote the expectation as it was WHEN IT WAS MADE. Carried as
        workflow inputs, because nothing at fire time loads the item to look them up."""
        row = _log(store, triggers)
        inputs = triggers.get(review_trigger_id(row["id"])).trigger.workflow["inline"]["config"][
            "inputs"
        ]
        assert inputs["decision_id"] == row["id"]
        assert inputs["expectation"] == row["expectation"]
        assert inputs["confidence"] == "0.70"

    def test_the_review_delivers_to_the_inbox(self, store, triggers) -> None:
        """Delivery is a trigger field, not action config. `inbox` is what puts the card
        behind the notify gate (quiet hours) instead of a raw channel post."""
        row = _log(store, triggers)
        assert triggers.get(review_trigger_id(row["id"])).trigger.delivery == "inbox"

    def test_rescheduling_re_points_the_same_row_rather_than_adding_one(
        self, store, triggers
    ) -> None:
        """Idempotence is the whole reason the id is deterministic: a generated slug would
        leave the old reminder armed beside the new one, and the user would get two."""
        row = _log(store, triggers)
        later = horizon_from_days(200)
        reschedule_review(row["id"], later, store=store, trigger_store=triggers)
        rows = [r for r in triggers.load() if r.trigger.id.startswith("system:decision-journal")]
        assert len(rows) == 1
        assert rows[0].trigger.spec["at"] == pytest.approx(
            datetime.fromisoformat(later).timestamp()
        )

    def test_abandoning_retires_the_reminder_and_writes_no_lesson(
        self, store, triggers, memory
    ) -> None:
        """An abandoned decision has no outcome, so distilling a lesson would put a fabricated
        verdict in long-term memory."""
        row = _log(store, triggers)
        abandon_decision(row["id"], store=store, trigger_store=triggers)
        assert triggers.get(review_trigger_id(row["id"])) is None
        item = store.get_item(row["id"])
        assert decision_meta(item)["status"] == "abandoned"
        assert memory.get_lessons() == []

    @pytest.mark.parametrize(
        ("kw", "match"),
        [
            ({"confidence": 1.7}, "out of range"),
            ({"confidence": "very"}, "must be a number"),
            ({"domain": "vibes"}, "unknown domain"),
            ({"summary": "  "}, "one-line summary"),
            ({"expectation": ""}, "requires an expectation"),
            ({"review_horizon": "1999-01-01"}, "in the past"),
            ({"review_horizon": "next tuesday"}, "not a date"),
        ],
    )
    def test_it_refuses_input_it_cannot_honour(self, store, triggers, kw, match) -> None:
        """A past horizon is refused rather than accepted, because `triggers.arm` returns ""
        for an elapsed one-shot — the decision would persist with a reminder that can never
        fire, which is an inert control dressed as a feature."""
        with pytest.raises(DecisionError, match=match):
            _log(store, triggers, **kw)

    def test_a_refused_horizon_leaves_no_orphan_trigger(self, store, triggers) -> None:
        """The refusal happens before the item is created, so there is nothing to clean up."""
        with pytest.raises(DecisionError):
            _log(store, triggers, review_horizon="1999-01-01")
        assert list(triggers.load()) == []
        assert list_decisions(store=store) == []

    def test_the_default_horizon_comes_from_config(self, store, triggers, monkeypatch) -> None:
        """`proactive.decision_default_horizon_days`. Patched at the accessor rather than
        through a config file so the test states which knob it is reading."""
        import personalclaw.decisions as dj

        monkeypatch.setattr(dj, "default_horizon_days", lambda: 7)
        row = _log(store, triggers, review_horizon="")
        expected = (datetime.now() + timedelta(days=7)).date().isoformat()
        assert row["review_horizon"] == expected


# ── resolution + the R18 lesson ──────────────────────────────────────────────


class TestResolution:
    def test_resolving_writes_a_lesson_citing_expectation_versus_outcome(
        self, store, triggers, memory
    ) -> None:
        """Criterion 5's memory half. The lesson must CITE both sides: a lesson that records
        only the outcome cannot teach calibration, which is the entire point of the journal."""
        row = _log(store, triggers)
        resolved = resolve_decision(
            row["id"],
            outcome="I earned 30% more and never missed the benefits",
            grade="better",
            store=store,
            trigger_store=triggers,
            memory=memory,
        )
        lessons = memory.get_lessons()
        assert len(lessons) == 1
        # A lesson's `value_json` is the rule text encoded directly.
        rule = json.loads(lessons[0]["value_json"])
        assert isinstance(rule, str)
        assert row["expectation"] in rule
        assert "never missed the benefits" in rule
        assert "70%" in rule
        assert resolved["status"] == "resolved"
        assert resolved["outcome_grade"] == "better"

    def test_the_soft_reference_points_at_a_row_that_exists(self, store, triggers, memory) -> None:
        """`lesson_memory_key` is a soft string reference, deliberately not a foreign key — so
        the only thing that makes it trustworthy is that it was read back OUT of the memory
        store rather than re-derived from the rule text."""
        row = _log(store, triggers)
        resolved = resolve_decision(
            row["id"],
            outcome="it went badly",
            grade="worse",
            store=store,
            trigger_store=triggers,
            memory=memory,
        )
        key = resolved["lesson_memory_key"]
        assert key and key.startswith("lesson.")
        assert key in {row_["key"] for row_ in memory.get_lessons()}

    def test_the_two_stores_stay_uncoupled(self, store, triggers, memory) -> None:
        """Criterion 5's "linked only by soft references": the knowledge item holds a string,
        and the memory row holds nothing pointing back."""
        row = _log(store, triggers)
        resolved = resolve_decision(
            row["id"],
            outcome="mixed bag",
            grade="mixed",
            store=store,
            trigger_store=triggers,
            memory=memory,
        )
        lesson = memory.get_lessons()[0]
        assert row["id"] not in json.dumps(lesson)
        assert isinstance(resolved["lesson_memory_key"], str)

    def test_a_refused_lesson_write_leaves_the_reference_null(self, store, triggers) -> None:
        """The falsification floor for the two tests above. If the stamp were unconditional,
        `lesson_memory_key` would point at a row that does not exist and every assertion about
        the soft reference would pass while the reference was a lie."""

        class _Refusing:
            def write_lesson(self, *a, **kw):
                return False

            def get_lessons(self):
                return []

        row = _log(store, triggers)
        resolved = resolve_decision(
            row["id"],
            outcome="nothing happened",
            grade="as_expected",
            store=store,
            trigger_store=triggers,
            memory=_Refusing(),
        )
        assert resolved["status"] == "resolved"
        assert resolved["lesson_memory_key"] is None

    def test_a_broken_memory_store_still_records_the_users_answer(self, store, triggers) -> None:
        """The user typed the outcome. Losing it because the lesson step raised would be the
        worse of the two failures, so the lesson is written LAST."""

        class _Exploding:
            def write_lesson(self, *a, **kw):
                raise RuntimeError("memory.db is locked")

        row = _log(store, triggers)
        resolved = resolve_decision(
            row["id"],
            outcome="it worked out",
            grade="better",
            store=store,
            trigger_store=triggers,
            memory=_Exploding(),
        )
        assert resolved["status"] == "resolved"
        assert resolved["outcome"] == "it worked out"
        assert resolved["lesson_memory_key"] is None

    def test_resolving_retires_the_reminder(self, store, triggers, memory) -> None:
        row = _log(store, triggers)
        resolve_decision(
            row["id"],
            outcome="done",
            grade="as_expected",
            store=store,
            trigger_store=triggers,
            memory=memory,
        )
        assert triggers.get(review_trigger_id(row["id"])) is None

    def test_a_resolved_decision_cannot_be_resolved_twice(self, store, triggers, memory) -> None:
        """A second resolution would write a second lesson about the same decision."""
        row = _log(store, triggers)
        kw = {"store": store, "trigger_store": triggers, "memory": memory}
        resolve_decision(row["id"], outcome="done", grade="as_expected", **kw)
        with pytest.raises(DecisionError, match="already resolved"):
            resolve_decision(row["id"], outcome="again", grade="worse", **kw)

    def test_it_refuses_a_grade_outside_the_vocabulary(self, store, triggers, memory) -> None:
        row = _log(store, triggers)
        with pytest.raises(DecisionError, match="unknown grade"):
            resolve_decision(
                row["id"],
                outcome="x",
                grade="excellent",
                store=store,
                trigger_store=triggers,
                memory=memory,
            )

    def test_it_refuses_an_empty_outcome(self, store, triggers, memory) -> None:
        row = _log(store, triggers)
        with pytest.raises(DecisionError, match="what actually happened"):
            resolve_decision(
                row["id"],
                outcome="   ",
                grade="better",
                store=store,
                trigger_store=triggers,
                memory=memory,
            )

    def test_the_lesson_body_never_paraphrases_the_expectation(self) -> None:
        """Composed deterministically, so the citation half cannot be summarized away — and
        resolving a decision costs no tokens on a path the user reaches by answering a card."""
        rule = lesson_text(
            summary="S",
            expectation="E happens by June",
            confidence=0.9,
            outcome="E did not happen",
            grade="worse",
        )
        assert "E happens by June" in rule
        assert "E did not happen" in rule
        assert "90%" in rule


class TestDeferral:
    def test_too_early_re_arms_rather_than_resolving(self, store, triggers, memory) -> None:
        row = _log(store, triggers)
        deferred = resolve_decision(
            row["id"],
            outcome="the quarter is not over",
            grade="too_early",
            store=store,
            trigger_store=triggers,
            memory=memory,
        )
        assert deferred["status"] == "pending"
        assert deferred["deferrals"] == 1
        assert deferred["review_horizon"] > row["review_horizon"]
        assert triggers.get(review_trigger_id(row["id"])) is not None
        # No lesson: there is no outcome yet to compare against the expectation.
        assert memory.get_lessons() == []

    def test_it_defers_at_most_twice_then_goes_stale_pending(self, store, triggers, memory) -> None:
        """Criterion 6. Past the cap the item stays pending with NO trigger — the journal view
        surfaces it, and nothing nags again."""
        row = _log(store, triggers)
        kw = {"store": store, "trigger_store": triggers, "memory": memory}
        for _ in range(MAX_DEFERRALS):
            resolve_decision(row["id"], outcome="still open", grade="too_early", **kw)
        final = resolve_decision(row["id"], outcome="still open", grade="too_early", **kw)
        assert final["status"] == "pending"
        assert final["stale_pending"] is True
        assert final["reminder_trigger_id"] is None
        assert triggers.get(review_trigger_id(row["id"])) is None

    def test_a_deferral_never_stacks_a_second_reminder(self, store, triggers, memory) -> None:
        """Each deferral re-points the one deterministic row. A generated id here would leave
        the elapsed reminder behind and add a new one on every defer."""
        row = _log(store, triggers)
        resolve_decision(
            row["id"],
            outcome="open",
            grade="too_early",
            store=store,
            trigger_store=triggers,
            memory=memory,
        )
        rows = [r for r in triggers.load() if r.trigger.id.startswith("system:decision-journal")]
        assert len(rows) == 1


# ── reads ────────────────────────────────────────────────────────────────────


class TestListAndCalibration:
    def test_it_filters_by_status_and_domain(self, store, triggers, memory) -> None:
        a = _log(store, triggers, summary="A", domain="career")
        b = _log(store, triggers, summary="B", domain="financial")
        resolve_decision(
            b["id"],
            outcome="fine",
            grade="as_expected",
            store=store,
            trigger_store=triggers,
            memory=memory,
        )
        pending = [r["id"] for r in list_decisions(store=store, status="pending")]
        assert pending == [a["id"]]
        assert [r["id"] for r in list_decisions(store=store, status="resolved")] == [b["id"]]
        assert [r["id"] for r in list_decisions(store=store, domain="financial")] == [b["id"]]

    def test_overdue_is_derived_not_stored(self, store, triggers) -> None:
        """A pending decision past its horizon. Derived at read time, so a reminder that fired
        and deleted itself does not have to leave a status behind for the view to work."""
        row = _log(store, triggers, review_horizon=horizon_from_days(2))
        future = datetime.now() + timedelta(days=5)
        assert [r["id"] for r in list_decisions(store=store, status="overdue", now=future)] == [
            row["id"]
        ]
        # Vacuity floor: the same decision is NOT overdue today, so the filter is reading the
        # horizon rather than matching every pending row.
        assert list_decisions(store=store, status="overdue") == []

    def test_it_refuses_an_unknown_status_or_domain(self, store) -> None:
        with pytest.raises(DecisionError, match="unknown status"):
            list_decisions(store=store, status="mostly")
        with pytest.raises(DecisionError, match="unknown domain"):
            list_decisions(store=store, domain="vibes")

    def test_calibration_is_count_honest_under_ten(self, store, triggers, memory) -> None:
        """Criterion 7: computed from knowledge.db alone, no new store and no LLM, and honest
        about a sample too small to mean anything."""
        for i in range(3):
            row = _log(store, triggers, summary=f"D{i}", domain="technical")
            resolve_decision(
                row["id"],
                outcome="fine",
                grade="as_expected",
                store=store,
                trigger_store=triggers,
                memory=memory,
            )
        strip = calibration(store=store)
        assert strip["technical"]["n"] == 3
        assert strip["technical"]["as_expected_rate"] == 1.0
        assert strip["technical"]["mean_confidence"] == 0.7
        assert strip["technical"]["count_honest"] is False
        # Vacuity floor: the flag flips when the sample is large enough, so `count_honest` is
        # reading the count rather than being hard-coded False.
        assert calibration(store=store, min_n=3)["technical"]["count_honest"] is True

    def test_a_mixed_outcome_is_not_scored_as_a_hit_or_a_miss(
        self, store, triggers, memory
    ) -> None:
        """Scoring `mixed` either way would invent a verdict the user declined to give."""
        row = _log(store, triggers, domain="personal")
        resolve_decision(
            row["id"],
            outcome="some of both",
            grade="mixed",
            store=store,
            trigger_store=triggers,
            memory=memory,
        )
        assert calibration(store=store) == {}
        assert "mixed" not in CALIBRATED_GRADES
        assert "mixed" in RESOLUTION_GRADES


# ── the chat tools ───────────────────────────────────────────────────────────


def _invoke(name: str, args: dict):
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    return asyncio.run(NativeBuiltinToolProvider().invoke(name, args))


class TestChatTools:
    def test_the_three_tools_surface_from_the_knowledge_app_provider(self) -> None:
        """The plan registers them beside the knowledge tools, in the same category — a
        decision IS a knowledge item, so the journal cannot be removed independently of the
        library its entries live in."""
        from personalclaw.agents.native.builtin_tools import (
            APP_CATEGORY_PROVIDERS,
            NativeBuiltinToolProvider,
        )

        provider = NativeBuiltinToolProvider(
            categories={"knowledge"}, provider_name=APP_CATEGORY_PROVIDERS["knowledge"][0]
        )
        names = {t.name for t in asyncio.run(provider.list_tools())}
        assert {"log_decision", "decision_list", "decision_resolve"} <= names

    def test_the_tool_schemas_read_their_vocabulary_from_the_owning_module(self) -> None:
        """A hand-copied enum would let a tool advertise a domain or grade
        `personalclaw.decisions` rejects, and the model would keep sending it."""
        from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

        defs = {t.name: t for t in asyncio.run(NativeBuiltinToolProvider().list_tools())}
        assert defs["log_decision"].parameters["properties"]["domain"]["enum"] == list(
            DECISION_DOMAINS
        )
        assert defs["decision_resolve"].parameters["properties"]["grade"]["enum"] == list(
            RESOLUTION_GRADES
        )

    def test_log_decision_reaches_the_journal_with_ingestion_wired(self, monkeypatch) -> None:
        """The CALL SITE. Driven through `invoke()` by name, so deleting `_t_log_decision`
        yields "unknown builtin tool" instead of an unexercised function — and `enqueue` is
        asserted because omitting it would leave every logged decision keyword-only forever."""
        import personalclaw.decisions as dj
        from personalclaw.agents.native.builtin_tools import _enrich_in_background

        seen: dict = {}

        def _fake(**kw):
            seen.update(kw)
            return {
                "id": "itm-1",
                "summary": kw["summary"],
                "domain": kw["domain"],
                "expectation": kw["expectation"],
                "confidence": 0.6,
                "review_horizon": "2027-01-01",
                "reminder_trigger_id": "system:decision-journal:itm-1",
            }

        monkeypatch.setattr(dj, "log_decision", _fake)
        result = _invoke(
            "log_decision",
            {
                "summary": "Ship the rewrite",
                "expectation": "two weeks",
                "confidence": 0.6,
                "domain": "technical",
            },
        )
        assert result.success, result.error
        assert seen["summary"] == "Ship the rewrite"
        assert seen["confidence"] == 0.6
        assert seen["enqueue"] is _enrich_in_background
        assert "system:decision-journal:itm-1" in result.output

    def test_log_decision_surfaces_a_refusal_instead_of_raising(self, monkeypatch) -> None:
        """A bad confidence is the model's mistake to fix, so the message has to reach it."""
        import personalclaw.decisions as dj

        def _boom(**kw):
            raise dj.DecisionError("confidence 4.0 is out of range")

        monkeypatch.setattr(dj, "log_decision", _boom)
        result = _invoke("log_decision", {"summary": "S", "expectation": "E", "confidence": 4.0})
        assert result.success is False
        assert "out of range" in result.error

    def test_decision_list_reaches_the_journal(self, monkeypatch) -> None:
        import personalclaw.decisions as dj

        seen: dict = {}

        def _fake(**kw):
            seen.update(kw)
            return [
                {
                    "id": "itm-1",
                    "summary": "Ship it",
                    "status": "pending",
                    "domain": "technical",
                    "review_horizon": "2027-01-01",
                    "overdue": True,
                    "stale_pending": False,
                    "outcome_grade": None,
                }
            ]

        monkeypatch.setattr(dj, "list_decisions", _fake)
        result = _invoke("decision_list", {"status": "overdue", "limit": 5})
        assert result.success, result.error
        assert seen == {"status": "overdue", "domain": "", "limit": 5}
        assert "OVERDUE" in result.output
        assert "itm-1" in result.output

    def test_decision_resolve_reaches_the_journal_and_reports_the_lesson(self, monkeypatch) -> None:
        import personalclaw.decisions as dj
        from personalclaw.agents.native.builtin_tools import _enrich_in_background

        seen: dict = {}

        def _fake(item_id, **kw):
            seen["id"] = item_id
            seen.update(kw)
            return {
                "id": item_id,
                "summary": "Ship it",
                "status": "resolved",
                "expectation": "two weeks",
                "outcome_grade": "worse",
                "lesson_memory_key": "lesson.abc123",
            }

        monkeypatch.setattr(dj, "resolve_decision", _fake)
        result = _invoke(
            "decision_resolve", {"id": "itm-1", "outcome": "six weeks", "grade": "worse"}
        )
        assert result.success, result.error
        assert seen["id"] == "itm-1"
        assert seen["outcome"] == "six weeks"
        assert seen["grade"] == "worse"
        assert seen["enqueue"] is _enrich_in_background
        assert "lesson.abc123" in result.output

    def test_decision_resolve_reports_a_deferral_as_a_deferral(self, monkeypatch) -> None:
        """A `too_early` did not resolve anything; saying it did would be a wrong report of a
        write that never happened."""
        import personalclaw.decisions as dj

        monkeypatch.setattr(
            dj,
            "resolve_decision",
            lambda item_id, **kw: {
                "id": item_id,
                "summary": "S",
                "status": "pending",
                "stale_pending": False,
                "review_horizon": "2027-06-01",
                "deferrals": 1,
            },
        )
        result = _invoke(
            "decision_resolve", {"id": "itm-1", "outcome": "still open", "grade": "too_early"}
        )
        assert result.success
        assert "rescheduled" in result.output
        assert "2027-06-01" in result.output

    def test_decision_resolve_requires_an_id(self) -> None:
        result = _invoke("decision_resolve", {"outcome": "x", "grade": "worse"})
        assert result.success is False
        assert "requires 'id'" in result.error
