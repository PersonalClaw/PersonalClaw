"""PA-5 — the triage digest read model and its HTTP surface (PROACTIVE-ASSISTANT §5.1/§5.4).

What these tests are FOR: the card renders four different facts — off, never run, empty, broken —
and a read model that collapses any two of them into an empty list tells the user the opposite of
what happened. So most of what follows is pairs: the negative case AND the case it must not be
confused with, asserted in the same test class, because a single assertion that "the list is
empty" passes for every one of the four.

Nothing here touches the real home: every test is a pure call over dicts, except the two route
tests, which build an aiohttp app and inspect its router without starting it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from personalclaw.proactive.manifest import (
    CollectedItem,
    Manifest,
    build_manifest,
    manifest_from_projection,
)
from personalclaw.proactive.pipeline import TriageResult
from personalclaw.proactive.surface import (
    MACHINE_DID_KINDS,
    STATE_ERROR,
    STATE_NEVER_RUN,
    STATE_OFF,
    STATE_READY,
    STATE_UNINSTALLED,
    answered_ordinals,
    build_digest_view,
    machine_did,
)

#: The literal size of the fixture window below, asserted directly by
#: `test_the_fixture_is_the_size_the_rails_assume`. Every count rail reads THIS, never a length
#: recomputed from the value under test — a floor derived from the thing it is meant to pin cannot
#: pin it (a view that dropped every item would satisfy `len(pending) == len(pending)`).
_FIXTURE_ITEMS = 3


@pytest.fixture
def window() -> Manifest:
    return build_manifest(
        [
            CollectedItem(
                source="inbox",
                source_id="gh_1",
                title="Review request on #412",
                detail="please take a look",
                materiality="action",
                permalink="https://example.test/412",
            ),
            CollectedItem(
                source="inbox",
                source_id="gh_2",
                title="Dependabot bumped left-pad",
                detail="automated",
                materiality="none",
            ),
            CollectedItem(
                source="channel",
                source_id="tg_9",
                title="Are we still on for Thursday?",
                detail="from a human",
                materiality="response",
            ),
        ],
        window_start="2026-08-25T06:00:00+00:00",
    )


def _output(**overrides: Any) -> dict:
    """A digest node output in the shape `TriageResult.summary()` emits."""
    base: dict[str, Any] = {
        "collected": _FIXTURE_ITEMS,
        "lanes": {"inbox": 2, "channel": 1, "run": 0},
        "items": [
            {
                "ordinal": "1",
                "source": "inbox",
                "source_id": "gh_1",
                "title": "Review request on #412",
                "permalink": "https://example.test/412",
                "materiality": "action",
            },
            {
                "ordinal": "2",
                "source": "inbox",
                "source_id": "gh_2",
                "title": "Dependabot bumped left-pad",
                "permalink": "",
                "materiality": "none",
            },
            {
                "ordinal": "3",
                "source": "channel",
                "source_id": "tg_9",
                "title": "Are we still on for Thursday?",
                "permalink": "",
                "materiality": "response",
            },
        ],
        "dropped": 0,
        "surfaced": 0,
        "proposable": 3,
        "proposals": [
            {
                "item_id": "1",
                "action_type": "reply_draft",
                "tier": "medium",
                "pattern_key": "reply_draft:inbox",
                "clamped": True,
            },
            {
                "item_id": "2",
                "action_type": "archive",
                "tier": "trivial",
                "pattern_key": "archive:inbox",
                "clamped": False,
            },
        ],
        "refused": [],
        "llm_calls": 2,
        "delivered": True,
        "short_circuited": False,
        "gate_called": True,
        "degraded": False,
        "digest_title": "Morning triage",
        "digest_body": "What your machine did: …",
        "window_start": "2026-08-25T06:00:00+00:00",
        "ledger_rows": 0,
        "notes": [],
    }
    base.update(overrides)
    return base


def _auto_ran(**overrides: Any) -> dict:
    """The same output, with PA-3's auto-execution stage having run."""
    auto: dict[str, Any] = dict(
        auto_executed=[
            {
                "item_id": "2",
                "source_id": "gh_2",
                "action_type": "archive",
                "provider": "inbox-op",
                "rule": "policy:trivial-tier",
                "reversal": "aW5ib3gtb3A6Z2hfMg==",
                "undoable": True,
                "ok": True,
                "error": "",
            }
        ],
        auto_deferred=[
            {
                "item_id": "1",
                "action_type": "reply_draft",
                "tier": "medium",
                "reason": "needs_you",
                "rule": "",
            }
        ],
        budget_breached=False,
        budget_reason="",
        auto_ledger_rows=1,
    )
    auto.update(overrides)
    return _output(**auto)


_RUN = {"run_id": "run-abc", "status": "succeeded", "finished_at": "2026-08-25T08:00:01+00:00"}


class TestTheFixture:
    def test_the_fixture_is_the_size_the_rails_assume(self, window: Manifest) -> None:
        # The vacuity floor for every count below, asserted as a literal rather than derived.
        assert len(window) == _FIXTURE_ITEMS
        assert len(_output()["items"]) == _FIXTURE_ITEMS


class TestFourDifferentAnswers:
    """Off, never-run, empty and broken must be four states, not one empty list."""

    def test_uninstalled_is_not_an_empty_digest(self) -> None:
        view = build_digest_view(enabled=True, installed=False)
        assert view["state"] == STATE_UNINSTALLED
        assert "pending" not in view  # no section lists at all — there is nothing to list

    def test_off_is_not_uninstalled_and_not_empty(self) -> None:
        view = build_digest_view(enabled=False, installed=True)
        assert view["state"] == STATE_OFF
        assert view["installed"] is True and view["enabled"] is False

    def test_never_run_is_not_off(self) -> None:
        view = build_digest_view(enabled=True, installed=True)
        assert view["state"] == STATE_NEVER_RUN

    def test_a_failed_read_is_an_error_not_an_empty_state(self) -> None:
        view = build_digest_view(
            enabled=True, installed=True, error="OSError: journal.jsonl is unreadable"
        )
        assert view["state"] == STATE_ERROR
        assert "journal.jsonl" in view["error"]
        # The vacuity sibling: the SAME arguments without the error must NOT be an error, or this
        # assertion would pass for a view that always reported one.
        assert build_digest_view(enabled=True, installed=True)["state"] != STATE_ERROR

    def test_an_error_outranks_installedness(self) -> None:
        # A read that failed cannot report installedness honestly; claiming `uninstalled` there
        # would offer the user an install for something that may already exist.
        view = build_digest_view(enabled=False, installed=False, error="boom")
        assert view["state"] == STATE_ERROR

    def test_a_ready_digest_reports_ready(self, window: Manifest) -> None:
        view = build_digest_view(enabled=True, installed=True, run=_RUN, output=_output())
        assert view["state"] == STATE_READY
        assert view["run_id"] == "run-abc"


class TestAnUnmeasuredValueIsNotAZero:
    def test_an_absent_auto_stage_is_reported_as_absent(self) -> None:
        view = build_digest_view(enabled=True, installed=True, run=_RUN, output=_output())
        assert view["auto_stage_ran"] is False
        assert view["auto_done"] == []

    def test_a_stage_that_ran_and_did_nothing_is_distinguishable_from_one_that_is_off(self) -> None:
        # THE pair. Both produce `auto_done == []`; only `auto_stage_ran` tells them apart, so a
        # card that rendered "0 actions taken" for the first would be lying about the second.
        off = build_digest_view(enabled=True, installed=True, run=_RUN, output=_output())
        ran = build_digest_view(
            enabled=True,
            installed=True,
            run=_RUN,
            output=_output(auto_executed=[], auto_deferred=[], auto_ledger_rows=0),
        )
        assert off["auto_done"] == ran["auto_done"] == []
        assert off["auto_stage_ran"] is False
        assert ran["auto_stage_ran"] is True

    def test_skipped_ledger_rows_read_as_incomplete_not_as_none(self) -> None:
        # PA-3's `_record` returns 0 when it cannot stamp a row with a run key, and reports the
        # absence. `dropped: 2` with `ledger_rows: 0` therefore means "the rationales were not
        # written", which must not render as "nothing was filtered".
        view = build_digest_view(
            enabled=True, installed=True, run=_RUN, output=_output(dropped=2, ledger_rows=0)
        )
        assert view["dropped"] == 2
        assert view["ledger_complete"] is False

    def test_a_genuinely_empty_filter_is_complete(self) -> None:
        # The vacuity sibling for the rail above: zero dropped and zero rows is COMPLETE, so the
        # flag cannot be satisfied by always reporting incompleteness.
        view = build_digest_view(
            enabled=True, installed=True, run=_RUN, output=_output(dropped=0, ledger_rows=0)
        )
        assert view["ledger_complete"] is True


class TestDeliveryIsNotClaimed:
    """🔴 Found by DRIVING it, not by a test: `delivered` cannot mean delivered.

    Driven on a live gateway with quiet hours covering the moment: the run reported
    `delivered: True` and the notification list did NOT grow by one. `DashboardState.notify`
    returns `None`, so the pipeline's flag can only ever mean "handed to the delivery gate". The
    view renames it at the boundary so no consumer can inherit the wrong claim.
    """

    def test_the_view_never_publishes_a_field_called_delivered(self) -> None:
        view = build_digest_view(
            enabled=True, installed=True, run=_RUN, output=_output(delivered=True)
        )
        assert "delivered" not in view
        assert view["handed_to_notify"] is True

    def test_the_flag_still_travels_so_the_absence_is_explainable(self) -> None:
        # The vacuity sibling: `False` must survive too, or the rename would just be a constant.
        view = build_digest_view(
            enabled=True, installed=True, run=_RUN, output=_output(delivered=False)
        )
        assert view["handed_to_notify"] is False


class TestPending:
    def test_pending_is_the_deferred_set_when_the_stage_ran(self) -> None:
        view = build_digest_view(enabled=True, installed=True, run=_RUN, output=_auto_ran())
        assert [row["ordinal"] for row in view["pending"]] == ["1"]
        assert [row["ordinal"] for row in view["auto_done"]] == ["2"]

    def test_pending_is_every_proposal_when_the_stage_did_not_run(self) -> None:
        view = build_digest_view(enabled=True, installed=True, run=_RUN, output=_output())
        assert [row["ordinal"] for row in view["pending"]] == ["1", "2"]

    def test_a_tier_badge_is_carried_not_derived(self) -> None:
        view = build_digest_view(enabled=True, installed=True, run=_RUN, output=_output())
        tiers = {row["ordinal"]: row["tier"] for row in view["pending"]}
        assert tiers == {"1": "medium", "2": "trivial"}

    def test_the_clamp_flag_survives_into_the_card(self) -> None:
        view = build_digest_view(enabled=True, installed=True, run=_RUN, output=_output())
        clamped = {row["ordinal"]: row["clamped"] for row in view["pending"]}
        assert clamped == {"1": True, "2": False}

    def test_the_pattern_key_is_joined_from_the_proposal_row(self) -> None:
        # `auto_deferred` carries no pattern, and the "always" tap needs one. Without the join the
        # button would have to invent a pattern from the action type, which is how a narrow taught
        # rule quietly becomes a broad one.
        view = build_digest_view(enabled=True, installed=True, run=_RUN, output=_auto_ran())
        assert view["pending"][0]["pattern_key"] == "reply_draft:inbox"

    def test_an_unjoinable_deferred_row_gets_no_invented_pattern(self) -> None:
        output = _auto_ran(
            auto_deferred=[
                {"item_id": "3", "action_type": "archive", "tier": "trivial", "reason": "needs_you"}
            ]
        )
        view = build_digest_view(enabled=True, installed=True, run=_RUN, output=output)
        assert view["pending"][0]["pattern_key"] == ""

    def test_provenance_comes_from_the_runs_own_manifest_projection(self) -> None:
        view = build_digest_view(enabled=True, installed=True, run=_RUN, output=_output())
        assert view["pending"][0]["title"] == "Review request on #412"
        assert view["pending"][0]["item_permalink"] == "https://example.test/412"

    def test_missing_provenance_is_blank_not_a_placeholder(self) -> None:
        view = build_digest_view(enabled=True, installed=True, run=_RUN, output=_output(items=[]))
        # A card that printed "Item 1" here would be inventing the one string the user recognises
        # the item by.
        assert view["pending"][0]["title"] == ""
        assert view["pending"][0]["ordinal"] == "1"


class TestUndoAndPermalinks:
    def test_an_auto_done_row_carries_the_reversal_handle(self) -> None:
        view = build_digest_view(enabled=True, installed=True, run=_RUN, output=_auto_ran())
        row = view["auto_done"][0]
        assert row["undoable"] is True and row["reversal"]

    def test_a_row_with_no_reversal_is_not_offered_as_undoable(self) -> None:
        output = _auto_ran(
            auto_executed=[
                {
                    "item_id": "2",
                    "source_id": "gh_2",
                    "action_type": "archive",
                    "provider": "inbox-op",
                    "rule": "policy:trivial-tier",
                    "reversal": "",
                    "undoable": False,
                    "ok": True,
                    "error": "",
                }
            ]
        )
        view = build_digest_view(enabled=True, installed=True, run=_RUN, output=output)
        assert view["auto_done"][0]["undoable"] is False

    def test_every_permalink_is_the_substrates_own_run_url(self) -> None:
        from personalclaw.triggers.delivery import status_url

        view = build_digest_view(
            enabled=True,
            installed=True,
            run=_RUN,
            output=_auto_ran(),
            events=[{"kind": "auto_executed", "seq": 3, "item_ordinal": "2"}],
        )
        expected = status_url(run_id="run-abc")
        assert expected  # the builder produces something for a known run
        assert view["permalink"] == expected
        assert {row["permalink"] for row in view["auto_done"]} == {expected}
        assert {row["permalink"] for row in view["machine_did"]} == {expected}


class TestWhatYourMachineDid:
    def _events(self) -> list[dict]:
        return [
            {"kind": "step_completed", "seq": 1},
            {"kind": "proposal_refused", "seq": 2, "reason": "unknown_action"},
            {"kind": "auto_executed", "seq": 3, "item_ordinal": "2", "rule": "policy:trivial-tier"},
            {"kind": "skipped_triage", "seq": 4, "item_ordinal": "5", "rationale": "dependabot"},
        ]

    def test_only_the_declared_kinds_reach_the_section(self) -> None:
        rows = machine_did(self._events())
        assert {row["kind"] for row in rows} <= set(MACHINE_DID_KINDS)
        assert "step_completed" not in {row["kind"] for row in rows}
        # Vacuity: the section is not empty, so the exclusion above is doing work.
        assert len(rows) == 3

    def test_the_section_groups_by_kind_not_by_write_order(self) -> None:
        rows = machine_did(self._events())
        assert [row["kind"] for row in rows] == [
            "auto_executed",
            "skipped_triage",
            "proposal_refused",
        ]

    def test_a_rationale_reaches_the_reason_field(self) -> None:
        rows = machine_did(self._events())
        skipped = next(row for row in rows if row["kind"] == "skipped_triage")
        assert skipped["reason"] == "dependabot"


class TestReplyIdempotency:
    def test_an_answered_ordinal_is_read_off_the_runs_own_ledger(self) -> None:
        events = [{"kind": "triage_reply", "item_ordinal": "1", "verb": "always no", "seq": 7}]
        assert answered_ordinals(events)["1"]["verb"] == "always no"

    def test_an_unanswered_window_has_no_index(self) -> None:
        assert answered_ordinals([{"kind": "auto_executed", "item_ordinal": "1"}]) == {}

    def test_the_card_marks_an_answered_proposal_as_answered(self) -> None:
        view = build_digest_view(
            enabled=True,
            installed=True,
            run=_RUN,
            output=_output(),
            events=[{"kind": "triage_reply", "item_ordinal": "1", "verb": "no", "seq": 7}],
        )
        answered = {row["ordinal"]: (row["answered"], row["answer"]) for row in view["pending"]}
        # The pair: item 1 answered, item 2 not. A view that marked everything answered — or
        # nothing — would fail one half.
        assert answered == {"1": (True, "no"), "2": (False, "")}


class TestTheManifestProjectionIsTheOrdinalContractMadeDurable:
    def test_the_summary_carries_the_ordinal_to_id_map(self, window: Manifest) -> None:
        summary = TriageResult(manifest=window).summary()
        assert [row["ordinal"] for row in summary["items"]] == ["1", "2", "3"]
        assert {row["source_id"] for row in summary["items"]} == {"gh_1", "gh_2", "tg_9"}

    def test_the_projection_never_carries_the_fenced_body(self, window: Manifest) -> None:
        # `detail` is the model-facing, untrusted item text. A read model for a card must not
        # become a second copy of it.
        blob = json.dumps(window.projection())
        assert "please take a look" not in blob
        assert "from a human" not in blob
        # Vacuity: the detail IS on the items, so the absence above is a property of the
        # projection rather than of the fixture.
        assert window.items[0].detail == "please take a look"

    def test_a_projection_round_trips_without_renumbering(self, window: Manifest) -> None:
        restored = manifest_from_projection(window.projection(), window_start=window.window_start)
        assert restored.ordinals() == window.ordinals()
        for item in window.items:
            back = restored.by_ordinal(item.ordinal)
            assert back is not None
            assert back.source_id == item.source_id

    def test_an_unaddressable_row_is_dropped_rather_than_admitted(self) -> None:
        rows = [
            {"ordinal": "1", "source_id": "gh_1", "source": "inbox"},
            {"ordinal": "2", "source_id": "", "source": "inbox"},
            {"ordinal": "", "source_id": "gh_3", "source": "inbox"},
        ]
        restored = manifest_from_projection(rows)
        # An ordinal that cannot be addressed must not stay in the id space a reply is checked
        # against, or `2 yes` would be accepted and then act on nothing.
        assert restored.ordinals() == {"1"}


class TestTheRoutesAreRegistered:
    """The CALL SITE. A handler nobody routes to is a handler nobody can reach."""

    def _paths(self) -> set[str]:
        from aiohttp import web

        from personalclaw.dashboard import handlers

        app = web.Application()
        app.router.add_get("/api/proactive/digest", handlers.api_proactive_digest)
        app.router.add_post("/api/proactive/digest/reply", handlers.api_proactive_reply)
        app.router.add_post("/api/proactive/install", handlers.api_proactive_install)
        return {getattr(r.resource, "canonical", "") for r in app.router.routes()}

    def test_the_three_handlers_are_importable_from_the_facade(self) -> None:
        from personalclaw.dashboard import handlers

        for name in ("api_proactive_digest", "api_proactive_reply", "api_proactive_install"):
            assert callable(getattr(handlers, name)), name

    def test_the_server_registers_all_three_paths(self) -> None:
        import inspect

        from personalclaw.dashboard import server

        source = inspect.getsource(server)
        for path in (
            '"/api/proactive/digest"',
            '"/api/proactive/digest/reply"',
            '"/api/proactive/install"',
        ):
            assert path in source, path
        # Vacuity: a path this module does NOT register must be absent, so the assertion above is
        # not satisfied by any substring of the file.
        assert '"/api/proactive/nonexistent"' not in source


class TestTheReplyLedgerKindIsVisible:
    def test_triage_reply_is_in_the_readable_set(self) -> None:
        from personalclaw.ledger.kinds import LEDGER_KINDS, TRIAGE_REPLY

        # A kind outside `LEDGER_KINDS` is written and then invisible to `read_events`, which
        # would make the idempotency index permanently empty and every reply act twice.
        assert TRIAGE_REPLY in LEDGER_KINDS

    def test_the_surface_reads_the_same_token_the_writer_writes(self) -> None:
        from personalclaw.ledger.kinds import TRIAGE_REPLY

        assert TRIAGE_REPLY in MACHINE_DID_KINDS
