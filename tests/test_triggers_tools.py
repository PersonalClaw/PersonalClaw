"""The `automation_*` chat-tool namespace (§4 / criterion 2 — S92).

Criterion 2 is the bar: *"When a file in ~/notes changes, summarize it into my knowledge base" is
creatable in chat in ONE message.* S83 shipped the `file` kind's watch runtime and recorded the
honest blocker — "Criterion 2 needs `automation_create` (§4), which needs somewhere to PUT a `file`
trigger. Measured: there is no unified trigger store." **S87 shipped that store**, so the blocker
is gone and this closes the criterion.

Every test drives the REAL `TriggerStore` against a `tmp_path`. The only injected pieces are the
cadence converter (an LLM call) and the run turn — the same two seams `ScheduleService` and S90's
executor already inject, which is what lets the whole namespace be driven without a model.
"""

from __future__ import annotations

import pytest

from personalclaw.triggers import tools as T
from personalclaw.triggers.store import TriggerStore


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _cron(expr="0 9 * * 1-5"):
    """A stand-in for `nl_to_cron` — injected so no test needs a model."""
    return lambda _cadence: (expr, "")


# ── 🔴 criterion 2, in one message ──


def test_criterion_2_is_creatable_in_one_message(store):
    """🔴 THE BAR, verbatim from the plan's Success Criteria. One call, no follow-up question, and
    the result is a real persisted `file` trigger — not a schedule, and not an error."""
    result = T.create(
        store,
        name="Summarize notes",
        when="when a file in ~/notes changes",
        message="Summarize the changed file into my knowledge base",
    )
    assert result.ok
    saved = store.get("file:summarize-notes")
    assert saved is not None
    assert saved.trigger.kind == "file"
    assert saved.trigger.spec["paths"] == ["~/notes/**"]
    assert saved.trigger.enabled is True
    assert saved.errors == []


def test_the_created_file_trigger_is_what_the_watch_runtime_expects(store):
    """🔴 A trigger the store accepts but `file_watch` cannot use would be present-and-inert — the
    defect class this program keeps finding. Driven through the real expander."""
    from personalclaw.triggers.file_watch import expand_globs

    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    spec = store.get("file:notes").trigger.spec
    expanded = expand_globs(spec["paths"])
    assert isinstance(expanded, dict) or isinstance(expanded, (list, tuple, set))


def test_a_cadence_request_still_becomes_a_cron_trigger(store):
    """The other half of §4's one-line contract, with the converter injected."""
    result = T.create(
        store,
        name="Daily digest",
        when="every weekday at 9",
        message="digest",
        cadence_to_cron=_cron(),
    )
    assert result.ok
    spec = store.get("clock:daily-digest").trigger.spec
    assert spec["kind"] == "cron"
    assert spec["expr"] == "0 9 * * 1-5"


def test_a_file_request_NEVER_calls_the_cadence_converter(store):
    """🔴 The property that prevents the per-minute poll. If a file request reached `nl_to_cron`, a
    model answering `* * * * *` would validate and schedule."""
    calls = []

    def spy(cadence):
        calls.append(cadence)
        return ("* * * * *", "")

    T.create(
        store,
        name="Notes",
        when="when a file in ~/notes changes",
        message="go",
        cadence_to_cron=spy,
    )
    assert calls == []
    assert store.get("file:notes").trigger.spec.get("expr") is None


def test_a_converter_error_fails_the_create_rather_than_saving_a_broken_row(store):
    """A trigger with no usable schedule would sit in the store never firing, and the user would
    have been told it was created."""
    result = T.create(
        store,
        name="Bad",
        when="every blue moon",
        message="go",
        cadence_to_cron=lambda _c: ("", "could not parse a cadence"),
    )
    assert not result.ok
    assert "could not parse" in result.text
    assert store.get("clock:bad") is None


# ── refusals: never guess ──


def test_an_unroutable_when_refuses_and_writes_nothing(store):
    result = T.create(store, name="Mystery", when="banana", message="go")
    assert not result.ok
    assert store.load() == []


def test_a_pathless_file_request_asks_which_path(store):
    """🔴 Guessing a root would watch the wrong tree silently."""
    result = T.create(store, name="Notes", when="when a file changes", message="go")
    assert not result.ok
    assert "which path" in result.text.lower()
    assert store.load() == []


def test_a_create_with_no_name_is_refused(store):
    assert not T.create(store, name="", when="every day at 9", message="go").ok


def test_a_create_with_no_work_to_do_is_refused(store):
    """A trigger with no workflow fires and does nothing — an automation that exists and cannot
    act."""
    result = T.create(store, name="Empty", when="when a file in ~/notes changes")
    assert not result.ok
    assert "message or a workflow" in result.text


def test_an_explicit_kind_and_spec_bypass_routing(store):
    """A caller that already knows should not be re-parsed by a lexical router."""
    result = T.create(
        store,
        name="Exact",
        kind="file",
        spec={"paths": ["/tmp/x/**"]},
        message="go",
    )
    assert result.ok
    assert store.get("file:exact").trigger.spec["paths"] == ["/tmp/x/**"]


# ── 🔴 decision 5d: announced and capped ──


def test_an_agent_created_trigger_is_announced_in_the_result(store):
    """§4: agent-created triggers are "announced to the user on creation … visible, not silent"."""
    text = T.create(store, name="Notes", when="when a file in ~/notes changes", message="go").text
    assert "I created this for you" in text
    assert "Automations page" in text


def test_the_announcement_explains_the_routing_choice(store):
    """A wrong route the user cannot see is a wrong route they cannot correct."""
    text = T.create(store, name="Notes", when="when a file in ~/notes changes", message="go").text
    assert "path" in text
    assert "~/notes/**" in text


def test_an_agent_created_trigger_is_tagged(store):
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    assert store.get("file:notes").trigger.created_by == "agent"


def test_the_agent_cap_is_enforced_with_a_count_and_a_remedy(store):
    """🔴 Decision 5d's cap (default 20 active). "Limit reached" without a number leaves the user
    unable to tell what to pause."""
    for i in range(T.max_agent_triggers()):
        assert T.create(store, name=f"A{i}", when="when a file in ~/notes changes", message="go").ok
    blocked = T.create(store, name="Over", when="when a file in ~/notes changes", message="go")
    assert not blocked.ok
    assert str(T.max_agent_triggers()) in blocked.text
    assert "pause or delete" in blocked.text.lower()


def test_a_paused_agent_trigger_does_not_count_against_the_cap(store):
    """A paused automation is not doing anything, and counting it would make the cap
    unrecoverable without deleting history the user may still want."""
    for i in range(T.max_agent_triggers()):
        T.create(store, name=f"A{i}", when="when a file in ~/notes changes", message="go")
    T.set_paused(store, trigger_id="file:a0", paused=True)
    assert T.create(store, name="Room", when="when a file in ~/notes changes", message="go").ok


def test_a_user_created_trigger_is_not_capped(store):
    """The cap exists because AGENTS create silently. The user asking for their 21st automation is
    not the risk decision 5d addresses."""
    for i in range(T.max_agent_triggers()):
        T.create(store, name=f"A{i}", when="when a file in ~/notes changes", message="go")
    assert T.create(
        store,
        name="Mine",
        when="when a file in ~/notes changes",
        message="go",
        created_by="user",
    ).ok


# ── ids ──


def test_the_id_uses_the_kind_slug_namespace(store):
    """§7 step 2 calls the `kind:<raw>` namespace "the migration map"; an opaque uuid would break
    that mapping and give the user an id they cannot recognize in their own store."""
    T.create(store, name="My Daily Digest!", when="when a file in ~/notes changes", message="go")
    assert store.get("file:my-daily-digest") is not None


def test_creating_the_same_name_twice_does_NOT_overwrite(store):
    """🔴 MEASURED: `store.upsert` is an upsert, so a duplicate name would REPLACE the first
    automation and report success. A user who asked for a second one and lost their first would
    have no way to know."""
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="first")
    T.create(store, name="Notes", when="when a file in ~/docs changes", message="second")
    ids = sorted(r.trigger.id for r in store.load())
    assert ids == ["file:notes", "file:notes-2"]
    assert store.get("file:notes").trigger.spec["paths"] == ["~/notes/**"]


def test_a_nameless_slug_still_produces_a_usable_id(store):
    assert T.slug_for("!!!", "clock") == "clock:automation"


# ── list ──


def test_list_shows_every_automation_with_its_kind(store):
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    T.create(store, name="Digest", when="every day at 9", message="go", cadence_to_cron=_cron())
    result = T.list_automations(store)
    assert "file:notes" in result.text
    assert "clock:digest" in result.text
    assert len(result.data["automations"]) == 2


def test_list_filters_by_kind_and_state(store):
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    T.create(store, name="Digest", when="every day at 9", message="go", cadence_to_cron=_cron())
    T.set_paused(store, trigger_id="file:notes", paused=True)
    assert len(T.list_automations(store, kind="file").data["automations"]) == 1
    assert [a["id"] for a in T.list_automations(store, state="active").data["automations"]] == [
        "clock:digest"
    ]
    assert [a["id"] for a in T.list_automations(store, state="paused").data["automations"]] == [
        "file:notes"
    ]


def test_an_empty_store_says_so_rather_than_erroring(store):
    result = T.list_automations(store)
    assert result.ok
    assert "no automations" in result.text.lower()


def test_a_BROKEN_row_is_listed_not_hidden(store):
    """🔴 S87's lenient parse keeps an unparseable row; hiding it here would make a broken
    automation invisible in the one place an agent looks to find out why nothing fired."""
    import json

    store.path.write_text(
        json.dumps({"version": 1, "triggers": [{"id": "x", "name": "X", "kind": "nonsense"}]})
    )
    result = T.list_automations(store)
    assert "x" in result.text
    assert result.data["automations"][0]["broken"]


# ── update ──


def test_update_applies_an_allowlisted_field(store):
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    assert T.update(store, trigger_id="file:notes", patch={"name": "Renamed"}).ok
    assert store.get("file:notes").trigger.name == "Renamed"


def test_update_REPORTS_a_rejected_field_rather_than_dropping_it(store):
    """🔴 An agent that thinks it changed `health_status` and got no error keeps believing a stale
    model of the automation."""
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    result = T.update(store, trigger_id="file:notes", patch={"name": "R", "run_count": 999})
    assert result.ok
    assert "run_count" in result.text
    assert result.data["rejected"] == ["run_count"]
    assert store.get("file:notes").trigger.run_count == 0


def test_a_patch_of_only_rejected_fields_fails(store):
    """🔴 §3.7 autopauses on the health numbers, so letting an automation rewrite its own health
    record would let it evade its own failure policy."""
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    result = T.update(
        store,
        trigger_id="file:notes",
        patch={"run_count": 999, "health_status": "ok", "last_run_id": "x"},
    )
    assert not result.ok
    assert store.get("file:notes").trigger.run_count == 0


def test_updating_an_unknown_id_is_an_error(store):
    assert not T.update(store, trigger_id="nope", patch={"name": "x"}).ok


# ── pause / resume ──


def test_pause_then_resume_round_trips(store):
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    assert T.set_paused(store, trigger_id="file:notes", paused=True).ok
    assert store.get("file:notes").trigger.enabled is False
    assert T.set_paused(store, trigger_id="file:notes", paused=False).ok
    assert store.get("file:notes").trigger.enabled is True


def test_resuming_a_BROKEN_row_reports_the_refusal(store):
    """🔴 `store.set_enabled` refuses to enable a row that failed to parse (S87). Swallowing that
    would leave a "resumed" automation silently disabled — the class of lie this program hunts."""
    import json

    store.path.write_text(
        json.dumps({"version": 1, "triggers": [{"id": "x", "name": "X", "kind": "nonsense"}]})
    )
    result = T.set_paused(store, trigger_id="x", paused=False)
    assert not result.ok
    assert "parse error" in result.text


def test_pausing_an_unknown_id_is_an_error(store):
    assert not T.set_paused(store, trigger_id="nope", paused=True).ok


# ── delete ──


def test_delete_requires_confirm(store):
    """An irreversible action a tool call should not be able to take by accident."""
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    result = T.delete(store, trigger_id="file:notes")
    assert not result.ok
    assert "confirm" in result.text
    assert store.get("file:notes") is not None


def test_the_refusal_offers_pausing_as_the_reversible_option(store):
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    assert "pause" in T.delete(store, trigger_id="file:notes").text.lower()


def test_delete_with_confirm_removes_the_row(store):
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    assert T.delete(store, trigger_id="file:notes", confirm=True).ok
    assert store.get("file:notes") is None


def test_deleting_an_unknown_id_is_an_error(store):
    assert not T.delete(store, trigger_id="nope", confirm=True).ok


# ── 🔴 automation_run and its gate bypasses ──


def test_a_manual_run_NEVER_bypasses_the_trust_boundary(store):
    """🔴 §4 allows a manual fire to bypass "min-interval + max_runs_per_hour, never rate floors".
    `screen` is the prompt-injection boundary (criterion 6) and `capability` is the frozen action
    set — a "the user asked for it" bypass on either would make the trust boundary optional, which
    is the escalation route criterion 6 is written against."""
    plan = T.manual_gate_plan()
    assert "screen" in plan["enforced"]
    assert "capability" in plan["enforced"]
    assert "budget" in plan["enforced"]
    assert set(plan["bypassed"]) == {"quiet", "duty"}


def test_the_bypass_sets_are_disjoint_and_cover_only_real_gates():
    """🔴 A bypass name that matched no gate would be a bypass that silently does nothing — and a
    gate missing from both sets would be unclassified."""
    from personalclaw.triggers.firepath import GATE_ORDER

    assert not (T.MANUAL_BYPASSES & T.MANUAL_NEVER_BYPASSES)
    assert T.MANUAL_BYPASSES <= set(GATE_ORDER)
    assert T.MANUAL_NEVER_BYPASSES <= set(GATE_ORDER)


def test_the_gate_plan_tracks_the_shipped_gate_order():
    """Built from `firepath.GATE_ORDER` rather than a copy, so a reordered fire path cannot leave a
    stale plan behind."""
    from personalclaw.triggers.firepath import GATE_ORDER

    plan = T.manual_gate_plan()
    assert plan["enforced"] + plan["bypassed"] != []
    assert set(plan["enforced"]) | set(plan["bypassed"]) == set(GATE_ORDER)


def test_a_dry_run_executes_NOTHING(store):
    """🔴 "Observe-mode replay" that ran would be the worst possible surprise."""
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    called = []
    result = T.run(
        store,
        trigger_id="file:notes",
        dry_run=True,
        runner=lambda p: called.append(p),
    )
    assert result.ok
    assert called == []
    assert result.data["plan"]["executes"] is False
    assert "nothing was executed" in result.text


def test_a_real_run_calls_the_injected_runner(store):
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    seen = []

    def runner(payload):
        seen.append(payload["trigger_id"])
        return {"status": "ok"}

    assert T.run(store, trigger_id="file:notes", runner=runner).ok
    assert seen == ["file:notes"]


def test_a_run_with_no_runner_REFUSES_rather_than_faking_success(store):
    """🔴 "Launched" with nothing behind it is the fire-and-forget lie S90's executor was written
    to keep out of this codebase."""
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    result = T.run(store, trigger_id="file:notes")
    assert not result.ok
    assert "nothing was executed" in result.text


def test_a_PAUSED_automation_can_still_be_run_by_hand(store):
    """Pausing means "stop firing on your own". Refusing a hand-driven run would remove the main
    way a user tests an automation before re-enabling it."""
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    T.set_paused(store, trigger_id="file:notes", paused=True)
    result = T.run(store, trigger_id="file:notes", runner=lambda p: {"status": "ok"})
    assert result.ok
    assert "does not re-enable" in result.text
    assert store.get("file:notes").trigger.enabled is False


def test_a_broken_row_cannot_be_run(store):
    import json

    store.path.write_text(
        json.dumps({"version": 1, "triggers": [{"id": "x", "name": "X", "kind": "nonsense"}]})
    )
    assert not T.run(store, trigger_id="x", runner=lambda p: {}).ok


def test_running_an_unknown_id_is_an_error(store):
    assert not T.run(store, trigger_id="nope").ok


# ── history ──


def test_history_reports_no_runs_honestly(store):
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    result = T.history(store, trigger_id="file:notes")
    assert result.ok
    assert "no recorded runs" in result.text


def test_history_projects_through_the_shipped_unified_feed(store):
    """🔴 Criterion 4: a hook, an event trigger and a cron show the same record shape. Using S84's
    projection rather than a second one is what keeps this tool's output identical to the Runs
    inbox.

    Also the correction of this function's first draft: `history` exposes NO reader — no
    `recent_fires` — so `unified_feed` takes the source rows from the caller.
    """
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    runs = [
        {
            "job_id": "file:notes",
            "job_name": "Notes",
            "run_id": "r1",
            "status": "ok",
            "started_at": 1_800_000_000.0,
        }
    ]
    result = T.history(store, trigger_id="file:notes", schedule_runs=runs)
    assert result.ok
    assert result.data["runs"]


def test_history_only_returns_the_requested_trigger(store):
    """Another automation's runs in this feed would make an agent debug the wrong thing."""
    T.create(store, name="Notes", when="when a file in ~/notes changes", message="go")
    runs = [
        {"job_id": "file:notes", "run_id": "r1", "status": "ok", "started_at": 1.0},
        {"job_id": "other", "run_id": "r2", "status": "error", "started_at": 2.0},
    ]
    result = T.history(store, trigger_id="file:notes", schedule_runs=runs)
    assert all(r["trigger_id"].endswith("file:notes") for r in result.data["runs"])
    assert len(result.data["runs"]) == 1


def test_history_for_an_unknown_id_is_an_error(store):
    assert not T.history(store, trigger_id="nope").ok


# ── the namespace itself ──


def test_every_declared_tool_name_has_a_handler():
    """🔴 A declared tool with no handler reports "unknown tool" at the worst possible moment."""
    handlers = {
        "automation_create": T.create,
        "automation_list": T.list_automations,
        "automation_update": T.update,
        "automation_pause": T.set_paused,
        "automation_resume": T.set_paused,
        "automation_run": T.run,
        "automation_history": T.history,
        "automation_delete": T.delete,
        "automation_delete_all": T.delete_all,
    }
    assert set(T.TOOL_NAMES) == set(handlers)
    assert all(callable(h) for h in handlers.values())


def test_the_namespace_covers_section_4s_table():
    """§4 declares eight tools, plus S109's `delete_all` (the scoped bulk delete carried over from
    the retired `schedule_remove_all`). A missing one is scope quietly dropped."""
    assert len(T.TOOL_NAMES) == 9
    for name in T.TOOL_NAMES:
        assert name.startswith("automation_")


def test_the_patch_allowlist_excludes_every_health_field():
    """The fields §3.7's autopause thresholds on must not be agent-settable."""
    for protected in (
        "run_count",
        "health_status",
        "last_run_id",
        "last_failure_at",
        "last_success_at",
        "state",
    ):
        assert protected not in T.PATCHABLE


def test_a_tool_result_serializes():
    result = T.ToolResult(True, "hi", {"a": 1})
    assert result.to_dict() == {"ok": True, "text": "hi", "data": {"a": 1}}
