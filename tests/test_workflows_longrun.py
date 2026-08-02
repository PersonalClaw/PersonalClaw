"""Tests for the long-run watcher mechanics (KNOWLEDGE-SYNTHESIS §4).

Nearly every failure this module guards against is INVISIBLE: nothing errors, the watcher keeps
running, and it just costs more every cycle or quietly stops noticing things. So the tests here
lean on measuring behaviour — what a 60-item sibling read actually yields, what the second cycle
actually sees — rather than asserting that a function exists.
"""

import time

import pytest

from personalclaw.workflows.bindings import BindingContext, BindingError, resolve
from personalclaw.workflows.longrun import (
    DIVERSITY_FLOOR,
    MAX_ADAPTIVE_DELAY_SECS,
    MIN_ADAPTIVE_DELAY_SECS,
    BufferState,
    SeenSet,
    bump_reflection,
    clamp_delay,
    compress_payload,
    continuity_header,
    convergence_warning,
    item_guid,
    pairwise_diversity,
    reflection_eligible,
    roll_continuity,
    sibling_view,
    significance_of,
    web_hygiene,
)
from personalclaw.workflows.models import InstanceState, JoinMode, LoopMode, Node
from personalclaw.workflows.tick import container_outcome, loop_should_continue, reap_watchers
from personalclaw.workflows.validator import validate_node_tree


def codes(spec) -> list[str]:
    return [i.code for i in validate_node_tree(Node.from_dict(spec)).issues]


# ── item identity ──


def test_a_republished_item_keeps_its_identity():
    """A feed re-serving the same story with a new fetch timestamp must not look novel — a
    whole-item hash would make the seen-set suppress nothing, silently, forever."""
    first = {"title": "Fed holds rates", "url": "https://x/a", "fetched_at": "10:00"}
    second = {"title": "Fed holds rates", "url": "https://x/a", "fetched_at": "11:00"}
    assert item_guid(first) == item_guid(second)


def test_an_explicit_guid_wins_over_content():
    """A source that supplies a guid knows better than we do what makes two items the same."""
    a = {"guid": "abc", "title": "one"}
    b = {"guid": "abc", "title": "totally different wording"}
    assert item_guid(a) == item_guid(b)


def test_different_content_gets_different_identity():
    assert item_guid({"statement": "a"}) != item_guid({"statement": "b"})


def test_an_unidentifiable_item_yields_no_guid():
    """ "" rather than a hash of nothing: an empty guid means "cannot dedupe this", and the
    seen-set skips it instead of collapsing every empty item into one."""
    assert item_guid({}) == ""
    assert item_guid(None) == ""


# ── seen-set ──


def test_the_second_cycle_only_sees_novel_items():
    seen = SeenSet()
    first = [{"guid": "g1"}, {"guid": "g2"}]
    assert len(seen.unseen(first)) == 2
    seen.mark_all(first)
    assert [i["guid"] for i in seen.unseen([{"guid": "g2"}, {"guid": "g3"}])] == ["g3"]


def test_unseen_does_not_mark():
    """A cycle that dies mid-synthesis must not have suppressed the items it never processed —
    they would be lost for good. The controller marks only after the cycle succeeds."""
    seen = SeenSet()
    seen.unseen([{"guid": "x"}])
    assert len(seen.unseen([{"guid": "x"}])) == 1


def test_duplicates_within_one_batch_collapse():
    assert len(SeenSet().unseen([{"guid": "a"}, {"guid": "a"}])) == 1


def test_the_seen_set_evicts_oldest_first():
    """Eviction order matters: dropping a RECENT guid means the very next cycle re-processes
    it, which is the one case the seen-set exists to prevent."""
    seen = SeenSet(capacity=3)
    for n in range(5):
        seen.mark(f"g{n}")
    assert "g0" not in seen
    assert "g4" in seen
    assert len(seen) == 3


def test_a_zero_capacity_does_not_disable_the_set():
    """A `capacity: 0` would make `mark` a no-op and every item novel forever — the failure the
    seen-set exists to prevent, reintroduced by a config typo."""
    seen = SeenSet(capacity=0)
    seen.mark("a")
    assert "a" in seen


def test_the_seen_set_round_trips_through_the_journal_shape():
    """A restart must not re-process what the run already paid for — the seen-set is journaled
    precisely because a restart is when a months-long watcher is most likely interrupted."""
    items = [{"guid": "a"}, {"guid": "b"}]
    seen = SeenSet(capacity=99)
    seen.mark_all(items)
    restored = SeenSet.from_dict(seen.to_dict())
    assert restored.unseen(items) == []
    assert restored.capacity == 99


def test_an_unreadable_seen_record_restores_an_empty_set():
    """A resumed run must start even with a partly unreadable ledger: an empty set costs tokens,
    a crash costs the run."""
    assert len(SeenSet.from_dict("garbage")) == 0


# ── significance + sibling views ──


def test_an_item_with_no_significance_is_kept():
    """Defaulting to 0.0 would make the filter silently discard every output from a template
    that never opted in."""
    assert significance_of({"statement": "x"}) == 1.0


@pytest.mark.parametrize("word,expected", [("critical", 1.0), ("low", 0.3), ("noise", 0.0)])
def test_word_significance_is_first_class(word, expected):
    """Models return words far more reliably than calibrated floats."""
    assert significance_of({"significance": word}) == expected


def test_a_percentage_scale_normalizes():
    assert significance_of({"significance": 90}) == 0.9


def test_a_sibling_read_is_bounded_by_default():
    """The unbounded failure is invisible: nothing breaks, the run just costs more every cycle
    until it hits a context limit hours in."""
    outputs = [{"findings": [{"statement": f"f{i}"} for i in range(60)]}]
    assert len(sibling_view(outputs)) == 20


def test_full_actually_opts_out():
    """Measured regression: filtering inside `as_root` made `| full` inert — it could only ever
    see items the default had already dropped, a control that looks present and does nothing."""
    outputs = [{"findings": [{"statement": f"f{i}"} for i in range(60)]}]
    assert len(sibling_view(outputs, full=True)) == 60


def test_the_filter_runs_before_the_window():
    """Windowing first would let 20 low-significance items crowd out the one that mattered."""
    findings = [{"statement": f"f{i}", "significance": "low"} for i in range(30)]
    findings.append({"statement": "the one that matters", "significance": "critical"})
    view = sibling_view([{"findings": findings}], window=5)
    assert any(i["statement"] == "the one that matters" for i in view)


def test_iteration_envelopes_are_flattened_to_items():
    """`siblings.<id>.output` MEANS items. Unflattened, `| full` returned one envelope out of
    60 items and `| unseen` returned nothing at all — an envelope carries no identity."""
    outputs = [{"findings": [{"guid": "a"}, {"guid": "b"}]}, {"findings": [{"guid": "c"}]}]
    assert len(sibling_view(outputs, full=True)) == 3


def test_an_output_with_no_carrier_key_passes_through_whole():
    assert sibling_view([{"report": "a summary"}], full=True) == [{"report": "a summary"}]


# ── bindings ──


def test_a_bare_sibling_binding_is_bounded():
    ctx = BindingContext(
        sibling_outputs={"main": [{"findings": [{"statement": f"f{i}"} for i in range(60)]}]}
    )
    assert len(resolve("{{siblings.main.output}}", ctx)) == 20


def test_an_explicit_window_is_not_re_defaulted():
    """A template that stated its own bound has said what it wants; silently applying the
    default on top would make `window(50)` mean 20."""
    ctx = BindingContext(
        sibling_outputs={"main": [{"findings": [{"statement": f"f{i}"} for i in range(60)]}]}
    )
    assert len(resolve("{{siblings.main.output | window(50)}}", ctx)) == 50


def test_unseen_without_an_engine_seen_set_raises():
    """A silently inert `unseen` is the whole failure it exists to prevent: the watcher keeps
    working, costs grow every cycle, and nothing indicates why."""
    ctx = BindingContext(sibling_outputs={"main": [{"findings": [{"guid": "a"}]}]})
    with pytest.raises(BindingError):
        resolve("{{siblings.main.output | unseen}}", ctx)


def test_unseen_applies_the_engine_seen_set():
    seen = SeenSet()
    seen.mark_all([{"guid": "a"}])
    ctx = BindingContext(
        sibling_outputs={"main": [{"findings": [{"guid": "a"}, {"guid": "b"}]}]},
        seen_filter=seen.unseen,
    )
    assert [i["guid"] for i in resolve("{{siblings.main.output | unseen}}", ctx)] == ["b"]


def test_previous_output_resolves_when_present():
    ctx = BindingContext(previous_output={"report": "prior"}, has_previous=True)
    assert resolve("{{previous.output.report}}", ctx) == "prior"


def test_a_first_cycle_previous_is_not_an_error():
    """Every diff-aware template in the plan is written as
    `{{previous.output.summary | default('None yet')}}`; raising would make each one fail on its
    own first cycle unless it grew a branch node for the case."""
    assert (
        resolve("{{previous.output.report | default('None yet')}}", BindingContext()) == "None yet"
    )


def test_a_genuine_typo_still_raises():
    """`previous` being absent is normal. `nodes.typo.output` is an authoring error, and the
    distinction is what keeps the lenient path from swallowing real mistakes."""
    with pytest.raises(BindingError):
        resolve("{{nodes.typo.output}}", BindingContext())


def test_the_clamp_pipe_bounds_a_model_proposal():
    assert resolve("{{inputs.d | clamp(30, 86400)}}", BindingContext(inputs={"d": 5})) == 30
    assert resolve("{{inputs.d | clamp(30, 86400)}}", BindingContext(inputs={"d": 10**9})) == 86400


def test_the_hygiene_pipe_drops_junk():
    ctx = BindingContext(inputs={"items": [{"title": "Read more"}, {"title": "A real headline x"}]})
    assert len(resolve("{{inputs.items | hygiene}}", ctx)) == 1


# ── loop mode + reaping ──


def test_until_cancelled_never_self_terminates():
    node = Node.from_dict({"kind": "loop", "config": {"mode": "until_cancelled"}})
    keep, _reason = loop_should_continue(node, iteration=500)
    assert keep


def test_max_iterations_still_caps_a_watcher():
    node = Node.from_dict(
        {"kind": "loop", "config": {"mode": "until_cancelled", "max_iterations": 3}}
    )
    keep, reason = loop_should_continue(node, iteration=3)
    assert not keep
    assert reason == "max_iterations"


def test_join_any_does_not_short_circuit_on_its_own():
    """The premise the plan states — "join:any cancels it" — is NOT what the engine does: the
    non-terminal check precedes the ANY rule, so the container reads RUNNING while the watcher
    runs. That check is deliberate (a join must not fire early on a fan-out), which is why the
    reaping is a separate rule instead of a change to join semantics."""
    assert (
        container_outcome([InstanceState.DONE, InstanceState.RUNNING], join=JoinMode.ANY)
        == InstanceState.RUNNING
    )


def _watcher_spec():
    return Node.from_dict(
        {
            "kind": "parallel",
            "id": "root",
            "config": {"join": "any"},
            "children": [
                {
                    "kind": "loop",
                    "id": "main",
                    "config": {"mode": "until_dry", "streak": 2},
                    "body": {"kind": "stage", "id": "work", "config": {"prompt": "go"}},
                },
                {
                    "kind": "loop",
                    "id": "watch",
                    "config": {"mode": "until_cancelled"},
                    "body": {
                        "kind": "sequence",
                        "id": "cycle",
                        "children": [
                            {"kind": "wait", "id": "w", "config": {"duration_secs": 300}},
                            {"kind": "stage", "id": "syn", "config": {"prompt": "synthesize"}},
                        ],
                    },
                },
            ],
        }
    )


def test_a_watcher_is_reaped_once_its_work_finishes():
    root = _watcher_spec()
    states = {"root.children[0]": InstanceState.DONE}
    assert reap_watchers(root, states) == ["root.children[1]"]


def test_a_watcher_is_not_reaped_while_the_work_runs():
    """Reaping early would cut the synthesis off from the findings it exists to consolidate."""
    root = _watcher_spec()
    assert reap_watchers(root, {"root.children[0]": InstanceState.RUNNING}) == []


def test_a_failed_worker_does_not_reap():
    """`join: any` completes on SUCCESS. A watcher outliving a failed worker is right: the run
    is not finished, and something else may still succeed."""
    root = _watcher_spec()
    assert reap_watchers(root, {"root.children[0]": InstanceState.FAILED}) == []


def test_an_already_terminal_watcher_is_not_re_reaped():
    root = _watcher_spec()
    states = {"root.children[0]": InstanceState.DONE, "root.children[1]": InstanceState.CANCELLED}
    assert reap_watchers(root, states) == []


def test_join_all_never_reaps():
    """Under `join: all` the watcher IS a leg the container waits for; reaping it would silently
    change the template's declared completion semantics."""
    spec = _watcher_spec().to_dict()
    spec["config"]["join"] = "all"
    assert reap_watchers(Node.from_dict(spec), {"root.children[0]": InstanceState.DONE}) == []


def test_a_container_worker_leg_still_reaps():
    """A worker leg that is a `sequence` holds no state of its own, so a raw-map read would see
    PENDING and the watcher would never be reaped."""
    spec = _watcher_spec().to_dict()
    spec["children"][0] = {
        "kind": "sequence",
        "id": "main",
        "children": [{"kind": "stage", "id": "work", "config": {"prompt": "go"}}],
    }
    states = {"root.children[0].children[0]": InstanceState.DONE}
    assert reap_watchers(Node.from_dict(spec), states) == ["root.children[1]"]


# ── validation ──


def test_an_unreapable_watcher_is_refused():
    """No exit condition and nothing able to stop it is a silent hang — the worst outcome for an
    unattended run."""
    spec = {
        "kind": "loop",
        "id": "w",
        "config": {"mode": "until_cancelled"},
        "body": {
            "kind": "sequence",
            "children": [{"kind": "wait", "config": {"duration_secs": 60}}],
        },
    }
    assert "WF_UNREAPABLE_WATCHER" in codes(spec)


def test_max_iterations_makes_a_bare_watcher_valid():
    spec = {
        "kind": "loop",
        "id": "w",
        "config": {"mode": "until_cancelled", "max_iterations": 100},
        "body": {
            "kind": "sequence",
            "children": [{"kind": "wait", "config": {"duration_secs": 60}}],
        },
    }
    assert "WF_UNREAPABLE_WATCHER" not in codes(spec)


def test_a_parallel_of_only_watchers_is_refused():
    """It can never satisfy its own join, so it is exactly as immortal as a bare loop."""
    body = {"kind": "sequence", "children": [{"kind": "wait", "config": {"duration_secs": 60}}]}
    spec = {
        "kind": "parallel",
        "id": "root",
        "config": {"join": "any"},
        "children": [
            {"kind": "loop", "id": "a", "config": {"mode": "until_cancelled"}, "body": body},
            {"kind": "loop", "id": "b", "config": {"mode": "until_cancelled"}, "body": body},
        ],
    }
    assert codes(spec).count("WF_UNREAPABLE_WATCHER") == 2


def test_a_watcher_with_no_wait_is_refused():
    """It would cycle as fast as the model answers and burn a whole budget in minutes — the one
    long-run failure that is expensive rather than merely slow."""
    spec = _watcher_spec().to_dict()
    spec["children"][1]["body"] = {"kind": "stage", "id": "syn", "config": {"prompt": "go"}}
    assert "WF_WATCHER_NO_WAIT" in codes(spec)


def test_the_plan_shape_validates_clean():
    assert codes(_watcher_spec().to_dict()) == []


def test_a_buffer_seal_wait_is_a_valid_wait():
    spec = {
        "kind": "wait",
        "id": "w",
        "config": {"seal": {"threshold": 20, "flush_stale_after_secs": 3600}},
    }
    assert codes(spec) == []


def test_a_seal_with_no_stale_flush_warns():
    """Without it a slow trickle never reaches the threshold and the synthesis never runs.
    Nothing errors — the watcher just quietly does nothing, forever."""
    assert "WF_SEAL_NO_FLUSH" in codes(
        {"kind": "wait", "id": "w", "config": {"seal": {"threshold": 5}}}
    )


def test_an_empty_seal_reports_exactly_one_issue():
    """Three issues for one typo is how a validation report stops being read."""
    assert codes({"kind": "wait", "id": "w", "config": {"seal": {}}}) == ["WF_BAD_SEAL"]


# ── buffer seal ──


def test_a_full_buffer_seals():
    buf = BufferState(seal_threshold=3)
    buf.add([{"a": 1}, {"b": 2}, {"c": 3}])
    sealed, reason = buf.should_seal(now=1000.0)
    assert sealed
    assert "buffer_full" in reason


def test_an_empty_buffer_never_seals_even_when_stale():
    """A stale-flush of nothing would pay for a synthesis of no new material every hour forever
    — the exact cost the volume trigger exists to avoid."""
    buf = BufferState(seal_threshold=20, flush_stale_after_secs=1, last_flush_at=1.0)
    assert buf.should_seal(now=10_000.0) == (False, "")


def test_a_stale_buffer_with_one_item_seals():
    buf = BufferState(seal_threshold=20, flush_stale_after_secs=60, last_flush_at=1.0)
    buf.add([{"a": 1}])
    sealed, reason = buf.should_seal(now=10_000.0)
    assert sealed
    assert "flush_stale" in reason


def test_draining_resets_the_trigger():
    buf = BufferState(seal_threshold=2)
    buf.add([{"a": 1}, {"b": 2}])
    assert buf.drain(now=500.0) == [{"a": 1}, {"b": 2}]
    assert buf.should_seal(now=500.0) == (False, "")


def test_a_token_threshold_seals_independently():
    buf = BufferState(seal_threshold=0, seal_tokens=10)
    buf.add([{"text": "x" * 200}])
    sealed, reason = buf.should_seal(now=1.0)
    assert sealed
    assert "tokens" in reason


def test_the_buffer_round_trips():
    buf = BufferState(seal_threshold=7, seal_tokens=11, flush_stale_after_secs=13)
    buf.add([{"a": 1}])
    restored = BufferState.from_dict(buf.to_dict())
    assert restored.seal_threshold == 7
    assert restored.items == [{"a": 1}]


# ── adaptive delay ──


def test_a_spin_proposal_is_clamped_up():
    """2 seconds would burn a whole budget in an hour, and it would look like a working run."""
    secs, reason = clamp_delay(2, default=300)
    assert secs == MIN_ADAPTIVE_DELAY_SECS
    assert "clamped_up" in reason


def test_a_week_long_proposal_is_clamped_down():
    secs, _ = clamp_delay(10**9, default=300)
    assert secs == MAX_ADAPTIVE_DELAY_SECS


def test_a_reasonable_proposal_is_honoured():
    assert clamp_delay(900, default=300) == (900, "")


def test_garbage_falls_back_to_the_configured_delay_not_the_floor():
    """ "The model returned nonsense" must not make the loop faster."""
    assert clamp_delay("nonsense", default=300)[0] == 300
    assert clamp_delay(None, default=300)[0] == 300
    assert clamp_delay(True, default=300)[0] == 300


# ── convergence guard ──


def test_echoing_sources_are_flagged():
    echo = [{"statement": "the fed held rates steady today"}] * 3
    assert pairwise_diversity(echo) < DIVERSITY_FLOOR
    assert "converged" in convergence_warning(echo)


def test_genuinely_diverse_sources_are_not_flagged():
    diverse = [
        {"statement": "fed held rates"},
        {"statement": "oil supply disruption in the strait"},
        {"statement": "chip export controls tighten"},
    ]
    assert convergence_warning(diverse) == ""


def test_one_source_is_not_an_echo():
    """Reporting 0.0 diversity would make every first cycle raise a false flag."""
    assert pairwise_diversity([{"statement": "one"}]) == 1.0
    assert convergence_warning([{"statement": "one"}]) == ""


def test_high_confidence_suppresses_the_flag():
    """Sources agreeing on a well-established fact is not an echo, and a guard that fires on
    every clear answer gets ignored — which is worse than not having it."""
    echo = [{"statement": "water boils at 100C"}] * 3
    assert convergence_warning(echo, confidence=0.95) == ""


# ── lineage ──


def test_a_thrice_reflected_item_is_no_longer_eligible():
    """Past that point the watcher is summarizing its own summaries: each pass loses detail while
    gaining confidence."""
    assert reflection_eligible({"reflection_count": 2})
    assert not reflection_eligible({"reflection_count": 3})


def test_an_item_with_no_count_is_eligible():
    assert reflection_eligible({"statement": "x"})


def test_bumping_does_not_mutate_the_input():
    original = {"statement": "x"}
    assert bump_reflection(original)["reflection_count"] == 1
    assert "reflection_count" not in original


# ── continuity ──


def test_continuity_keeps_the_newest_lines():
    """Dropping the newest would make the object progressively less relevant the longer a
    recurring workflow ran."""
    state: dict = {}
    for n in range(8):
        state = roll_continuity(state, outcome=f"run {n}", topics=[f"t{n}"], refs=[f"r{n}"])
    assert state["summary"][0] == "run 7"
    assert len(state["summary"]) == 5


def test_continuity_caps_topics_and_refs():
    state: dict = {}
    for n in range(30):
        state = roll_continuity(state, outcome="x", topics=[f"t{n}"], refs=[f"r{n}"])
    assert len(state["recent_topics"]) == 10
    assert len(state["recent_refs"]) == 20


def test_continuity_dedupes():
    state = roll_continuity({}, outcome="x", topics=["Rates", "rates"], refs=[])
    assert state["recent_topics"] == ["Rates"]


def test_an_empty_continuity_renders_nothing():
    """A heading followed by blank space reads to a model as "there was prior work and it
    produced nothing" — a different and wrong claim."""
    assert continuity_header({}) == ""
    assert continuity_header(None) == ""


def test_a_populated_continuity_renders_a_header():
    state = roll_continuity({}, outcome="found three issues", topics=["latency"], refs=["r1"])
    header = continuity_header(state)
    assert "previous runs" in header
    assert "found three issues" in header


# ── hygiene ──


def test_junk_titles_are_dropped():
    kept = web_hygiene([{"title": "Read more"}, {"title": "A real headline about rates"}])
    assert len(kept) == 1


def test_domain_filtering_is_on_host_boundaries():
    """Substring matching would let `example.com.attacker.net` pass an `example.com` filter."""
    items = [
        {"title": "A real headline here", "url": "https://example.com/a"},
        {"title": "Another real headline", "url": "https://example.com.attacker.net/b"},
        {"title": "A third real headline", "url": "https://news.example.com/c"},
    ]
    kept = web_hygiene(items, allow_domains=["example.com"])
    assert {i["url"] for i in kept} == {"https://example.com/a", "https://news.example.com/c"}


# ── payload compression ──


def test_compression_says_the_view_is_incomplete():
    """A silent truncation reads as a complete list, and a synthesis built on it would report
    absence as evidence."""
    text, was = compress_payload([{"x": "y" * 100} for _ in range(50)], cap=120)
    assert was
    assert "INCOMPLETE" in text


def test_a_small_payload_passes_through_untouched():
    text, was = compress_payload("short", cap=100)
    assert (text, was) == ("short", False)


# ── the wait dispatcher ──


def _wait(cfg, ctx=None, now=None):
    import asyncio

    from personalclaw.workflows.engine import dispatch_wait

    node = Node.from_dict({"kind": "wait", "id": "w", "config": cfg})
    return asyncio.get_event_loop().run_until_complete(
        dispatch_wait(node, ctx or BindingContext(), now=now or time.time())
    )


def test_an_adaptive_wait_clamps_a_spin_proposal():
    now = 1000.0
    result = _wait({"duration_secs": 300, "adaptive": 2}, now=now)
    assert result.wake_at - now == MIN_ADAPTIVE_DELAY_SECS


def test_an_adaptive_wait_honours_a_sane_proposal():
    now = 1000.0
    assert _wait({"duration_secs": 300, "adaptive": 900}, now=now).wake_at - now == 900


def test_a_wait_with_no_adaptive_key_uses_its_configured_duration():
    now = 1000.0
    assert _wait({"duration_secs": 300}, now=now).wake_at - now == 300


def test_a_seal_wait_completes_when_the_buffer_is_full():
    ctx = BindingContext(inputs={"buf": [{"a": 1}] * 25})
    result = _wait(
        {"seal": {"threshold": 20, "flush_stale_after_secs": 3600, "items": "{{inputs.buf}}"}},
        ctx=ctx,
    )
    assert result.state == InstanceState.DONE
    assert result.output["sealed"] is True


def test_a_seal_wait_rechecks_when_the_buffer_is_short():
    """It parks on a bounded re-check rather than the stale deadline: the buffer fills from a
    SIBLING, so nothing about this node's own state would wake it."""
    ctx = BindingContext(inputs={"buf": [{"a": 1}]})
    now = 1000.0
    result = _wait(
        {
            "seal": {
                "threshold": 20,
                "flush_stale_after_secs": 3600,
                "items": "{{inputs.buf}}",
                "check_every_secs": 45,
            }
        },
        ctx=ctx,
        now=now,
    )
    assert result.state == InstanceState.WAITING
    assert result.wake_at - now == 45


def test_a_wait_with_nothing_configured_fails():
    assert _wait({}).state == InstanceState.FAILED


# ── ledger + enum drift ──


def test_the_new_ledger_kinds_are_registered():
    """A watcher stopped early produced fewer cycles than its cadence implies; a refiner reading
    cycle counts without the event would conclude the template under-performed."""
    from personalclaw.workflows import journal

    for kind in (
        journal.WATCHER_REAPED,
        journal.SEEN_SET,
        journal.BUFFER_SEAL,
        journal.DELAY_CLAMPED,
    ):
        assert kind in journal.LEDGER_KINDS


def test_until_cancelled_is_in_the_loop_mode_enum():
    assert LoopMode("until_cancelled") is LoopMode.UNTIL_CANCELLED


# ── engine path handling (three defects found by running the plan's shape live) ──


def test_a_node_below_an_iteration_marker_keeps_its_spec_path():
    """`_base_path` truncated at the last marker, so `…body@0.children[0]` resolved to the body
    SEQUENCE. Live effect: a `wait` nested in a loop body was read as a gate by
    `_wake_due_nodes`, and every cycle failed with "gate timed out with no answer" — for a
    template containing no gate at all."""
    from personalclaw.workflows.controller import _base_path

    assert _base_path("root.children[0].body@0.children[0]") == "root.children[0].body.children[0]"
    assert _base_path("root.body#3.children[1]") == "root.body.children[1]"
    assert _base_path("root.body@2") == "root.body"
    assert _base_path("root") == "root"


def test_a_container_bodied_loop_finds_its_parent():
    """The marker need not END the path. It always does for a leaf body, never for a container
    one — so `int("0.children[2]")` raised, `_advance_loop` returned silently, the loop never
    advanced and the run deadlocked after exactly one iteration. Five shipped templates use
    container-bodied loops."""
    from personalclaw.workflows.controller import _loop_parent

    assert _loop_parent("root.children[1].body@0.children[2]") == ("root.children[1]", 0)
    assert _loop_parent("root.children[0].body@2") == ("root.children[0]", 2)


def test_the_innermost_loop_marker_wins():
    """A loop nested in another loop's body must advance ITSELF, not its parent."""
    from personalclaw.workflows.controller import _loop_parent

    assert _loop_parent("root.body@1.body@3.children[0]") == ("root.body@1", 3)


def test_a_foreach_marker_is_not_a_loop_iteration():
    from personalclaw.workflows.controller import _loop_parent

    assert _loop_parent("root.body#2.children[0]") == (None, 0)


def test_instance_paths_sort_numerically():
    """A string sort puts `body@10` before `body@2`, so "oldest first" silently became wrong at
    the tenth iteration: the window would keep the wrong items and `previous.output` would
    return the wrong cycle. Ten cycles in is later than any short test would reach."""
    from personalclaw.workflows.controller import _natural_key

    paths = [f"root.body@{n}.children[0]" for n in range(12)]
    ordered = [p.split("@")[1].split(".")[0] for p in sorted(paths, key=_natural_key)]
    assert ordered == [str(n) for n in range(12)]


def test_derive_state_is_public_for_the_iteration_check():
    """A container-bodied loop advances on "did the whole body finish?", and a second private
    notion of completeness would disagree with the scheduler exactly where it matters."""
    from personalclaw.workflows.tick import derive_state

    body = Node.from_dict(
        {
            "kind": "sequence",
            "id": "cycle",
            "children": [
                {"kind": "wait", "id": "w", "config": {"duration_secs": 1}},
                {"kind": "transform", "id": "t", "config": {"expr": "{{inputs.x}}"}},
            ],
        }
    )
    partial = {"root.body@0.children[0]": InstanceState.DONE}
    assert derive_state(body, "root.body@0", partial) == InstanceState.RUNNING
    whole = dict(partial) | {"root.body@0.children[1]": InstanceState.DONE}
    assert derive_state(body, "root.body@0", whole) == InstanceState.DONE
