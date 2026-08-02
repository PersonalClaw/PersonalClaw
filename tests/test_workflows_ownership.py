"""Tests for run session ownership and incognito inheritance (WORK-CONTAINERS §5.1, S50).

The property this module exists for: **a run launched from an incognito chat must not write memories
from its stages.** That failure is invisible — the chat itself stays clean, so the user has no
signal
that the mode was defeated.

Two seams had to learn the `workflow:` prefix, and both were verified by calling the real functions
rather than by adding a parallel helper:

* `sel._infer_source` — without it, every run-owned tool call audits as `channel`, the catch-all
where
  unrecognized keys silently land. "What did the run do" becomes unanswerable from the audit log
  even
  though every event is in it.
* `context._prompt_use_case_for` — without it, an owned session resolves to the `chat` prompt,
framing
  a stage worker as a conversational assistant.

The asymmetry: an unrecognized `memory_mode` value parses as INCOGNITO, not NORMAL. The value exists
because someone asked for privacy, and a typo or a newer mode name this build does not know must not
read as "record everything". A lost note is recoverable; a memory the user believed was never
written
is not.
"""

import pytest

from personalclaw.workflows.ownership import (
    LEARNING_PROVIDERS,
    OWNED_APP,
    OWNED_PREFIX,
    READ_SUPPRESSED,
    SEL_SOURCE,
    WRITE_SUPPRESSED,
    MemoryMode,
    OwnedSessions,
    Ownership,
    announcement,
    audit_fields,
    durable_metadata,
    inherit_mode,
    is_owned,
    own_session,
    owned_key,
    parse_mode,
    parse_owned,
    restriction_calls,
    sel_source,
    skips_node,
)

# ── the key ──


def test_the_key_carries_BOTH_the_run_and_the_node():
    """The run id groups a run's sessions for the cockpit; the node id says which stage. A key with
    only the run id would make five parallel stages indistinguishable in the audit log."""
    assert owned_key("r-abc", "review") == "workflow:r-abc:review"
    assert parse_owned("workflow:r-abc:review") == ("r-abc", "review")


def test_the_separator_matches_the_COLON_conventions():
    """`cron:` and `subagent:` use a colon; the loop convention (`loop-<id>`) is the odd one
    out, and
    copying it would make a fourth parser needed for a fourth shape."""
    assert OWNED_PREFIX.endswith(":")


def test_an_engine_instance_PATH_survives_as_a_node_id():
    """Node ids in this engine are instance paths like `root.children[0]`. A key format that
    could not
    hold one would force a second identifier scheme for exactly the nodes that fan out."""
    key = owned_key("r-1", "root.children[0]")
    assert parse_owned(key) == ("r-1", "root.children[0]")


def test_an_unexpected_character_is_SANITIZED_not_rejected():
    """A node id is author-controlled. A key that raised would fail the RUN over a naming detail,
    while sanitizing keeps the key parseable and loses nothing that identifies the session."""
    key = owned_key("r 1", "my node!")
    assert parse_owned(key) is not None


def test_an_empty_part_still_yields_a_parseable_key():
    assert parse_owned(owned_key("", "")) == ("unknown", "unknown")


@pytest.mark.parametrize(
    "key", ["dashboard:x", "cron:5", "subagent:9", "loop-7", "", "workflow:only-one-part"]
)
def test_a_non_owned_key_is_not_claimed(key):
    assert is_owned(key) is False
    assert parse_owned(key) is None


# ── the two seams, verified against the real functions ──


def test_the_SEL_ITSELF_infers_the_workflow_source():
    """Measured: `log_tool_call` calls `sel._infer_source` directly, so a helper elsewhere returning
    "workflow" would have been a parallel path the audit log never consults — every run-owned tool
    call would still record as `channel`."""
    from personalclaw.sel import _infer_source

    assert _infer_source(owned_key("r-1", "review")) == SEL_SOURCE


@pytest.mark.parametrize(
    "key,expected",
    [
        ("dashboard:x", "dashboard"),
        ("cron:5", "cron"),
        ("subagent:9", "subagent"),
        ("_bg", "background"),
        ("cli_chat", "cli"),
        ("some-slack-thread", "channel"),
    ],
)
def test_the_existing_SEL_sources_are_unchanged(key, expected):
    """Adding a source must not reroute an existing one — an audit filter that silently changed
    meaning would invalidate saved queries without any error."""
    from personalclaw.sel import _infer_source

    assert _infer_source(key) == expected


def test_the_helper_agrees_with_the_SEL():
    """Two answers to "what source is this" would eventually disagree, and the log's answer is
    the one
    that ends up on disk."""
    from personalclaw.sel import _infer_source

    key = owned_key("r-1", "review")
    assert sel_source(key) == _infer_source(key)


def test_the_helper_delegates_for_a_non_owned_key():
    assert sel_source("dashboard:x") == "dashboard"


def test_an_owned_session_resolves_to_the_BACKGROUND_prompt():
    """Measured: without the prefix entry an owned session got the `chat` prompt — a stage worker
    framed as a conversational assistant, which is the wrong framing for unattended work."""
    from personalclaw.context import _prompt_use_case_for

    assert _prompt_use_case_for(owned_key("r-1", "review")) == "background"


@pytest.mark.parametrize(
    "key,expected",
    [
        ("dashboard:x", "chat"),
        ("loop:3", "goal_loop"),
        ("code:2", "code"),
        ("cron:1", "background"),
        ("subagent:9", "background"),
    ],
)
def test_the_existing_prompt_use_cases_are_unchanged(key, expected):
    from personalclaw.context import _prompt_use_case_for

    assert _prompt_use_case_for(key) == expected


def test_behaviour_keys_off_APP_not_the_key_prefix():
    """Verified in code: the gateway and chat runner both branch on `session._app == "loop"`,
    never on
    a key prefix. The `loop_`/`loop:` prefix-match in the prompt resolver is a known near-miss the
    plan says not to repeat, so ownership sets `_app` and the key is only an identifier."""
    assert own_session("r-1", "review").app == OWNED_APP


# ── mode parsing fails CLOSED ──


@pytest.mark.parametrize("raw", ["", None, "normal"])
def test_an_absent_mode_is_normal(raw):
    assert parse_mode(raw) is MemoryMode.NORMAL


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("incognito", MemoryMode.INCOGNITO),
        ("temporary", MemoryMode.TEMPORARY),
        ("TEMPORARY", MemoryMode.TEMPORARY),
        ("  Incognito  ", MemoryMode.INCOGNITO),
    ],
)
def test_a_known_mode_parses(raw, expected):
    assert parse_mode(raw) is expected


def test_an_UNKNOWN_mode_parses_as_incognito():
    """The whole safety argument: the value exists because someone asked for privacy, and a typo
    or a
    newer mode name this build does not know must not be read as "record everything"."""
    assert parse_mode("ephemeral_v2") is MemoryMode.INCOGNITO
    assert parse_mode("private") is MemoryMode.INCOGNITO


# ── the read/write split ──


def test_temporary_suppresses_BOTH_reads_and_writes():
    assert MemoryMode.TEMPORARY in WRITE_SUPPRESSED
    assert MemoryMode.TEMPORARY in READ_SUPPRESSED


def test_incognito_suppresses_writes_but_NOT_reads():
    """Incognito exists to keep a session out of the record while letting it work. Treating its
    reads
    as blocked would silently degrade its answers, which is a different product than the one asked
    for."""
    assert MemoryMode.INCOGNITO in WRITE_SUPPRESSED
    assert MemoryMode.INCOGNITO not in READ_SUPPRESSED


def test_the_ownership_record_exposes_both_postures():
    owned = own_session("r-1", "n", inherited_mode=MemoryMode.INCOGNITO)
    assert owned.suppresses_writes is True
    assert owned.suppresses_reads is False


def test_a_normal_session_suppresses_nothing():
    owned = own_session("r-1", "n")
    assert owned.suppresses_writes is False
    assert owned.suppresses_reads is False


# ── inheritance reads BOTH sources ──


def test_the_DURABLE_metadata_line_is_honored():
    """The registry only knows sessions this process has seen; the JSONL line is what history
    consolidation re-derives from after a restart. Checking only the registry would mean a gateway
    restart silently un-marks every incognito run in flight."""
    assert (
        inherit_mode("dashboard:x", origin_metadata={"memory_mode": "incognito"})
        is MemoryMode.INCOGNITO
    )


def test_the_live_REGISTRY_is_honored_when_there_is_no_durable_line(monkeypatch):
    from personalclaw import session_restrictions

    session_restrictions.clear("dashboard:probe")
    session_restrictions.mark_incognito("dashboard:probe")
    try:
        assert inherit_mode("dashboard:probe") is MemoryMode.INCOGNITO
    finally:
        session_restrictions.clear("dashboard:probe")


def test_temporary_in_the_registry_inherits_as_temporary(monkeypatch):
    from personalclaw import session_restrictions

    session_restrictions.clear("dashboard:probe2")
    session_restrictions.mark_temporary("dashboard:probe2")
    try:
        assert inherit_mode("dashboard:probe2") is MemoryMode.TEMPORARY
    finally:
        session_restrictions.clear("dashboard:probe2")


def test_the_durable_line_WINS_over_a_clean_registry():
    """A restarted gateway has an empty registry and a full disk. If the registry won, every
    restored
    incognito session would come back unrestricted."""
    assert (
        inherit_mode("dashboard:unseen", origin_metadata={"memory_mode": "temporary"})
        is MemoryMode.TEMPORARY
    )


def test_an_unrestricted_origin_inherits_normal():
    assert inherit_mode("dashboard:fresh", origin_metadata={}) is MemoryMode.NORMAL


def test_a_BROKEN_registry_does_not_raise(monkeypatch):
    """A context/lookup failure must not take down a run start."""

    def boom(_key):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr("personalclaw.session_restrictions.is_temporary", boom)
    assert inherit_mode("dashboard:x") is MemoryMode.NORMAL


def test_an_unknown_durable_mode_inherits_as_RESTRICTED():
    """The fail-closed direction applied to inheritance: a mode string this build does not recognize
    still came from someone asking for privacy."""
    assert (
        inherit_mode("dashboard:x", origin_metadata={"memory_mode": "future_private_mode"})
        is MemoryMode.INCOGNITO
    )


# ── the engine skips learning nodes outright ──


def test_a_restricted_run_SKIPS_a_learning_node():
    """Skipping at the ENGINE is the primary control. Letting the node run and trusting the
    consumer's
    own gate would make correctness depend on every persist path checking a flag — and a new write
    path added later would leak by default."""
    skip, why = skips_node({"provider": "knowledge-persist"}, MemoryMode.INCOGNITO)
    assert skip is True
    assert "knowledge-persist" in why


@pytest.mark.parametrize("provider", sorted(LEARNING_PROVIDERS))
def test_every_declared_learning_provider_is_skipped(provider):
    assert skips_node({"provider": provider}, MemoryMode.TEMPORARY)[0] is True


def test_a_normal_run_skips_nothing():
    assert skips_node({"provider": "knowledge-persist"}, MemoryMode.NORMAL) == (False, "")


def test_an_ordinary_node_is_not_skipped_in_a_restricted_run():
    """Over-skipping would make an incognito run produce nothing at all, which is not what the mode
    asks for — it asks for work without a record."""
    assert skips_node({"provider": "http-fetch"}, MemoryMode.INCOGNITO)[0] is False


def test_a_node_can_DECLARE_that_it_persists_memory():
    """The provider list cannot know about an app-contributed writer. A declaration is how a new
    persist path opts into the restriction instead of leaking past it."""
    skip, why = skips_node({"persists_memory": True}, MemoryMode.INCOGNITO)
    assert skip is True
    assert "persists_memory" in why


def test_provider_matching_is_case_insensitive():
    assert skips_node({"provider": "Knowledge-Persist"}, MemoryMode.INCOGNITO)[0] is True


# ── the completion announcement ──


def test_a_restricted_origin_gets_the_summary_but_NOT_the_index():
    """The run's outcome is still useful to the person who asked for it. What they asked to avoid is
    the record."""
    note = announcement("dashboard:x", "finished 3 stages", MemoryMode.INCOGNITO)
    assert note.text == "finished 3 stages"
    assert note.indexable is False
    assert "incognito" in note.reason


def test_a_normal_origin_gets_an_indexable_summary():
    assert announcement("dashboard:x", "done", MemoryMode.NORMAL).indexable is True


def test_indexability_is_decided_HERE_not_at_the_destination():
    """Deciding at the destination means every surface that can receive a summary re-derives the
    rule,
    and the one that forgot would index it. The user asked for a private session, not a private
    session with one indexed exception."""
    payload = announcement("dashboard:x", "done", MemoryMode.TEMPORARY).to_dict()
    assert payload["indexable"] is False
    assert payload["reason"]


# ── the durable line and the registry marks ──


def test_a_normal_session_writes_its_mode_EXPLICITLY():
    """An absent key is indistinguishable from a pre-mode session, and the tolerant reader treats
    unknown values as restricted — so an omitted mode would be read as unrestricted only by
    accident
    of the empty-string branch. Writing it makes the posture a recorded fact."""
    assert durable_metadata(MemoryMode.NORMAL) == {"memory_mode": "normal"}


def test_a_restricted_session_writes_its_mode():
    assert durable_metadata(MemoryMode.INCOGNITO) == {"memory_mode": "incognito"}


def test_a_temporary_session_needs_BOTH_registry_marks():
    """`is_temporary` gates reads while `is_restricted` (true for either mark) gates writes. Marking
    only temporary would leave the write gate depending on one function's internals."""
    assert restriction_calls(own_session("r", "n", inherited_mode=MemoryMode.TEMPORARY)) == [
        "mark_temporary",
        "mark_incognito",
    ]


def test_an_incognito_session_needs_one_mark():
    assert restriction_calls(own_session("r", "n", inherited_mode=MemoryMode.INCOGNITO)) == [
        "mark_incognito"
    ]


def test_a_normal_session_needs_no_marks():
    assert restriction_calls(own_session("r", "n")) == []


def test_the_marks_are_NAMED_rather_than_performed():
    """The registry is process-global state every other test shares. Returning the names keeps this
    module testable without mutating it, and leaves the mutation with the caller that owns the
    session lifecycle."""
    calls = restriction_calls(own_session("r", "n", inherited_mode=MemoryMode.INCOGNITO))
    assert all(isinstance(c, str) for c in calls)


# ── audit fields ──


def test_the_run_id_rides_in_the_AUDIT_EVENT_not_only_the_key():
    """An auditor filtering by run should not have to parse keys — and a key format change would
    silently break every saved filter."""
    fields = audit_fields(own_session("r-42", "review"))
    assert fields["run_id"] == "r-42"
    assert fields["node_id"] == "review"
    assert fields["source"] == SEL_SOURCE


# ── owned-session cleanup ──


def test_owned_sessions_are_tracked_for_CLEANUP():
    """`session_restrictions.clear` exists, and a run that ended without calling it leaves marks
    in a
    bounded LRU where they eventually evict — the restriction outlives the session by an
    unpredictable
    amount and then vanishes, which is the worst of both."""
    owned = OwnedSessions(run_id="r-1")
    owned.add("a")
    owned.add("b")
    assert owned.cleanup_plan() == ["workflow:r-1:a", "workflow:r-1:b"]


def test_adding_the_same_node_twice_records_one_session():
    owned = OwnedSessions(run_id="r-1")
    first = owned.add("a")
    second = owned.add("a")
    assert first == second
    assert len(owned.keys) == 1


def test_the_cleanup_plan_is_in_creation_order():
    owned = OwnedSessions(run_id="r-1")
    for node in ("z", "a", "m"):
        owned.add(node)
    assert owned.cleanup_plan() == ["workflow:r-1:z", "workflow:r-1:a", "workflow:r-1:m"]


def test_an_ownership_record_serializes_completely():
    payload = Ownership(
        key="workflow:r:n", run_id="r", node_id="n", memory_mode=MemoryMode.INCOGNITO
    ).to_dict()
    assert payload["app"] == OWNED_APP
    assert payload["source"] == SEL_SOURCE
    assert payload["memory_mode"] == "incognito"
    assert payload["suppresses_writes"] is True


# ── the run record carries the mode in `extra` ──


def test_the_mode_persists_in_the_runs_EXTRA_dict():
    """`extra` is already stored and round-tripped, so the mode needs no schema change. A new column
    for one string would be a migration under the pre-1.0 banner for no gain."""
    from personalclaw.workflows.models import WorkflowRun
    from personalclaw.workflows.ownership import run_mode, stamp_run_mode

    run = WorkflowRun(id="r", workflow_name="w", extra=stamp_run_mode({}, MemoryMode.INCOGNITO))
    assert run_mode(run) is MemoryMode.INCOGNITO
    # Survives the record's own serialization round trip, which is what a restart replays through.
    assert run_mode(WorkflowRun.from_dict(run.to_dict())) is MemoryMode.INCOGNITO


def test_stamping_does_not_mutate_the_callers_dict():
    """Run records are compared and journaled; mutating the dict the caller holds would leave a
    stamped object behind after a rejected create."""
    from personalclaw.workflows.ownership import stamp_run_mode

    original: dict = {}
    stamp_run_mode(original, MemoryMode.INCOGNITO)
    assert original == {}


def test_stamping_preserves_other_extra_keys():
    from personalclaw.workflows.ownership import stamp_run_mode

    assert stamp_run_mode({"other": 1}, MemoryMode.INCOGNITO) == {
        "other": 1,
        "memory_mode": "incognito",
    }


def test_a_run_with_no_recorded_mode_is_normal():
    """Absence means the run predates the feature or came from an unrestricted origin — both
    genuinely
    unrestricted, unlike an unrecognized VALUE, which is a privacy request in an unknown vocabulary.
    """
    from personalclaw.workflows.models import WorkflowRun
    from personalclaw.workflows.ownership import run_mode

    assert run_mode(WorkflowRun(id="r", workflow_name="w")) is MemoryMode.NORMAL


# ── the wired engine seam ──


def _assert_isolated(tmp_path) -> None:
    """The store must resolve INTO tmp_path before any write.

    Measured the hard way: patching `personalclaw.config.loader.config_dir` does NOT reach
    `workflows.store`, which imports `config_dir` at module level — so two test runs were written
    into the REAL `~/.personalclaw` and then leaked into `test_context`'s
    `active_workflows_block()` assertions. Patching `workflows.store.config_dir` is the established
    convention (see `test_workflows_run_delete._isolated_home`); this assertion is what makes a
    future mis-patch fail loudly instead of writing to someone's real home.
    """
    from personalclaw.workflows import store

    assert str(tmp_path) in str(store.workflows_dir())


def test_the_engine_SKIPS_a_learning_node_in_a_restricted_run(tmp_path, monkeypatch):
    """The end-to-end claim, driven against a real run record: a run launched from an incognito chat
    does not execute its persist nodes at all."""
    import asyncio

    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    _assert_isolated(tmp_path)
    from personalclaw.workflows import store
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch
    from personalclaw.workflows.models import InstanceState, Node, RunStatus, WorkflowRun
    from personalclaw.workflows.ownership import stamp_run_mode

    store.create(
        WorkflowRun(
            id="r-inc",
            workflow_name="w",
            status=RunStatus.RUNNING,
            extra=stamp_run_mode({}, MemoryMode.INCOGNITO),
        )
    )
    node = Node.from_dict(
        {
            "kind": "stage",
            "id": "save",
            "config": {"prompt": "persist it", "provider": "knowledge-persist"},
        }
    )
    result = asyncio.run(dispatch(node, BindingContext(inputs={}), run_id="r-inc", subagents=None))
    # DEGRADED, not FAILED: the node was deliberately not run, which is a success with a
    # machine-readable reason rather than an error the user has to interpret.
    assert result.state is InstanceState.DEGRADED
    assert "knowledge-persist" in result.degraded_reason


def test_the_engine_does_NOT_skip_a_learning_node_in_a_normal_run(tmp_path, monkeypatch):
    """Over-skipping would make every run stop persisting — the control has to be conditional on
    the
    inherited mode, not on the node kind alone."""
    import asyncio

    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    _assert_isolated(tmp_path)
    from personalclaw.workflows import store
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch
    from personalclaw.workflows.models import InstanceState, Node, RunStatus, WorkflowRun

    store.create(WorkflowRun(id="r-norm", workflow_name="w", status=RunStatus.RUNNING))
    node = Node.from_dict(
        {
            "kind": "stage",
            "id": "save",
            "config": {"prompt": "persist it", "provider": "knowledge-persist"},
        }
    )
    result = asyncio.run(dispatch(node, BindingContext(inputs={}), run_id="r-norm", subagents=None))
    # It proceeds far enough to need a subagent manager — i.e. it was NOT skipped.
    assert result.state is not InstanceState.DEGRADED


def test_a_missing_run_does_not_skip_anything(tmp_path, monkeypatch):
    """A lookup failure must not silently stop doing the work the user asked for. The fail-closed
    direction in this feature is about the memory MODE, not about whether the run executes."""
    import asyncio

    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    _assert_isolated(tmp_path)
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch
    from personalclaw.workflows.models import InstanceState, Node

    node = Node.from_dict(
        {
            "kind": "stage",
            "id": "save",
            "config": {"prompt": "x", "provider": "knowledge-persist"},
        }
    )
    result = asyncio.run(dispatch(node, BindingContext(inputs={}), run_id="r-gone", subagents=None))
    assert result.state is not InstanceState.DEGRADED


def test_the_engine_helper_calls_the_REAL_store_api():
    """Measured: an earlier version called `store.load()` and read `run.memory_mode`; NEITHER
    exists,
    so the helper would have raised on every stage and its `except` would have swallowed it — an
    enforcement control that silently never fires."""
    from personalclaw.workflows import store

    assert hasattr(store, "get")
    assert not hasattr(store, "load")
    from personalclaw.workflows.models import WorkflowRun

    assert "memory_mode" not in {f for f in WorkflowRun.__dataclass_fields__}
