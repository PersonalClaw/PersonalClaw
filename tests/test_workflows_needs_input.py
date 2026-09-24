"""Tests for the NeedsInputItem contract (WORK-CONTAINERS §6.1 R1, S51).
 The card exists so a blocked run is answerable from a glance. Four properties carry it, and
each was measured against the engine's real vocabulary rather than a guessed one.  **The block
classifier must cover the ENGINE's failure classes.** An earlier version matched `dependency`,
`capability` and `config` — none of which are real `FailureClass` values — so every capability
failure fell through to "a decision", and the card asked the user to decide about a missing
credential instead of telling them to add one.  **A transient block is not the user's to
answer.** Surfacing a rate limit as a decision asks the user to do the system's waiting, and
it trains them to click through cards — which is how a real approval gets clicked through too.
**Owner binding is anti-hijack.** A gate surfaced into a shared channel must only be
answerable by the session that raised it, or answering it is a privilege escalation dressed as
convenience.  **Staleness reminds once.** A card that reminds every sweep becomes the
notification the user mutes, and muting is per-source, so it takes every genuinely urgent card
with it.
"""

import pytest

from personalclaw.workflows.human_input import AskKind
from personalclaw.workflows.models import FailureClass
from personalclaw.workflows.needs_input import (
    MAX_CHOICES,
    MAX_EVIDENCE_CHARS,
    MAX_RENOTIFICATIONS,
    RENOTIFY_AFTER_HOURS,
    USER_ACTIONABLE,
    BlockKind,
    NeedsInputItem,
    build_item,
    card_refs,
    classify_block,
    expired,
    from_refs,
    marked_renotified,
    may_satisfy,
    one_decision_lint,
    renotify_text,
    should_renotify,
    summarize_attempts,
    trim_evidence,
)

HOUR = 3600.0
NOW = 1_700_000_000.0


def card(**kw) -> NeedsInputItem:
    base = {"run_id": "r-1", "node_id": "approve", "blocker": "Publish?"}
    return NeedsInputItem(**{**base, **kw})


# ── the classifier covers the ENGINE's real vocabulary ──


@pytest.mark.parametrize("failure_class", [f.value for f in FailureClass])
def test_every_real_failure_class_classifies_without_falling_through_by_accident(failure_class):
    """The sweep that found the bug: an earlier version matched invented class names, so real
    classes
    silently landed on NEEDS_INPUT. This asserts each real class reaches a DELIBERATE answer."""
    kind = classify_block({}, {"failure_class": failure_class})
    assert isinstance(kind, BlockKind)


@pytest.mark.parametrize("failure_class", ["permission", "budget"])
def test_a_capability_failure_tells_the_user_to_GRANT_something(failure_class):
    """Not "make a decision" — the action is different. A spent budget or a denied permission
    needs a
    human to raise or grant, and filing it as a decision hides what to do."""
    assert classify_block({}, {"failure_class": failure_class}) is BlockKind.CAPABILITY


@pytest.mark.parametrize("failure_class", ["transient", "network", "timeout"])
def test_a_retryable_failure_is_TRANSIENT(failure_class):
    assert classify_block({}, {"failure_class": failure_class}) is BlockKind.TRANSIENT


@pytest.mark.parametrize("failure_class", ["protocol", "internal"])
def test_a_BUG_is_not_filed_as_retryable(failure_class):
    """Filing a bug as transient means it retries forever while nobody is told — the failure
    mode is
    silence, which is worse than an error."""
    assert classify_block({}, {"failure_class": failure_class}) is not BlockKind.TRANSIENT


def test_an_APPROVAL_ask_wins_over_any_failure_class():
    """It is the one kind where the work is already done, and misfiling it loses the "just say yes"
    affordance that makes it cheap to answer."""
    assert (
        classify_block({"kind": "approval"}, {"failure_class": "transient"}) is BlockKind.APPROVAL
    )


def test_the_promptless_HEADLINE_ladder_is_reachable_from_a_real_gate():
    """`_blocker_text`'s fallback ladder — the failure cause, else "`<node>` is waiting" — was
    unreachable in production for the same reason the inbox title's was: `_ask_payload` manufactured
    `prompt = "Approval needed"`, so the first branch always won and the card's headline named
    neither the step nor the cause. Asserted from the REAL ask, not a hand-built dict."""
    from personalclaw.workflows.engine import _ask_payload
    from personalclaw.workflows.needs_input import _blocker_text

    class _Node:
        id = "init_gate"

    ask = _ask_payload(_Node(), {})
    assert ask["prompt"] == "", f"a prompt was manufactured again: {ask['prompt']!r}"

    # No failure recorded: the node is named, so the card says which step is waiting.
    assert _blocker_text(ask, None, "init_gate") == "`init_gate` is waiting"
    # A failure's plain cause outranks that, because it says WHY rather than merely where.
    assert _blocker_text(ask, {"cause_plain": "the deploy key expired"}, "init_gate") == (
        "the deploy key expired"
    )
    # And an authored prompt still outranks both.
    authored = _ask_payload(_Node(), {"prompt": "Ship the release?"})
    assert _blocker_text(authored, {"cause_plain": "x"}, "init_gate") == "Ship the release?"


@pytest.mark.parametrize("ask_kind", [k.value for k in AskKind])
def test_every_real_ASK_kind_classifies(ask_kind):
    assert isinstance(classify_block({"kind": ask_kind}, None), BlockKind)


def test_no_ask_and_no_failure_is_a_decision():
    assert classify_block(None, None) is BlockKind.NEEDS_INPUT


# ── actionability ──


def test_a_transient_block_is_NOT_the_users_to_answer():
    """Asking the user to decide about a rate limit is asking them to do the system's waiting."""
    assert BlockKind.TRANSIENT not in USER_ACTIONABLE
    assert card(block_kind=BlockKind.TRANSIENT).actionable is False


@pytest.mark.parametrize("kind", [BlockKind.NEEDS_INPUT, BlockKind.CAPABILITY, BlockKind.APPROVAL])
def test_the_other_three_kinds_are_actionable(kind):
    assert card(block_kind=kind).actionable is True


# ── the card's shape ──


def test_the_blocker_is_the_asks_OWN_prompt():
    """A generic "a step needs input" forces the user to open the row to learn anything, which
    turns a
    glanceable inbox into a list of doors."""
    item = build_item(run_id="r", node_id="n", ask={"prompt": "Publish the draft?"})
    assert item.blocker == "Publish the draft?"


def test_a_failure_supplies_the_blocker_when_there_is_no_ask():
    item = build_item(run_id="r", node_id="n", failure={"cause_plain": "the API key is missing"})
    assert item.blocker == "the API key is missing"


def test_a_blocker_always_says_SOMETHING():
    """An empty headline is a row the user cannot triage at all."""
    assert build_item(run_id="r", node_id="check-sources").blocker


def test_the_recommendation_comes_from_the_ASKS_default():
    """The ask already carries one — S45's grill protocol makes every question ship a
    recommendation.
    Re-deriving it here would give the card a second opinion that could contradict the run's own."""
    item = build_item(run_id="r", node_id="n", ask={"prompt": "How often?", "default": "weekly"})
    assert "weekly" in item.recommendation


def test_a_failures_REMEDIATION_is_the_recommendation_when_there_is_no_default():
    item = build_item(
        run_id="r", node_id="n", failure={"cause_plain": "x", "remediation": "add the token"}
    )
    assert item.recommendation == "add the token"


def test_an_approval_card_recommends_reviewing_the_output():
    item = build_item(run_id="r", node_id="n", ask={"kind": "approval", "prompt": "ok?"})
    assert "approve" in item.recommendation.lower()


def test_attempts_are_summarized_one_line_each():
    """This is what earns the recommendation credibility: the same suggestion reads as a guess
    without
    it and as a considered next step with it."""
    lines = summarize_attempts(
        [
            {"attempt": 1, "outcome": "failed", "note": "source unreachable"},
            {"attempt": 2, "outcome": "failed", "note": "still unreachable"},
        ]
    )
    assert lines == [
        "attempt 1: failed — source unreachable",
        "attempt 2: failed — still unreachable",
    ]


def test_FAILED_attempts_are_kept():
    """An attempt log showing only successes would make a five-attempt struggle look like a
    first-try
    block, and the user would wonder why the system gave up so fast."""
    lines = summarize_attempts([{"attempt": 1, "outcome": "failed"}])
    assert "failed" in lines[0]


def test_a_malformed_attempt_entry_is_skipped_rather_than_crashing_the_card():
    assert summarize_attempts([None, "junk", {"outcome": "ok"}]) == ["ok"]


def test_no_attempts_yields_an_empty_list_not_a_placeholder():
    """ "No attempts recorded" as text would claim the run tried nothing, which is different
    from the
    ledger not being available."""
    assert summarize_attempts(None) == []


def test_evidence_is_TRIMMED():
    """An inbox row is a glance. A card carrying a full transcript pushes the decision below the
    fold,
    which is the one thing it exists to show — and the detail is one deep link away."""
    trimmed = trim_evidence({"log": "x" * 5000})
    assert len(trimmed["log"]) == MAX_EVIDENCE_CHARS


def test_evidence_lists_are_bounded_too():
    trimmed = trim_evidence({"files": [f"f{i}" for i in range(50)]})
    assert len(trimmed["files"]) == 10


def test_evidence_scalars_survive_as_scalars():
    """A count rendered as a string cannot be compared or summed by a surface reading it."""
    trimmed = trim_evidence({"count": 3, "ok": True, "ratio": 0.5})
    assert trimmed == {"count": 3, "ok": True, "ratio": 0.5}


def test_choices_are_capped():
    item = build_item(run_id="r", node_id="n", ask={"choices": [str(i) for i in range(20)]})
    assert len(item.choices) == MAX_CHOICES


# ── owner binding ──


def test_the_OWNER_may_answer():
    assert may_satisfy(card(owner="dashboard:chat-1"), "dashboard:chat-1") == (True, "")


def test_a_STRANGER_may_not_answer_an_owned_card():
    """Anti-hijack: a gate surfaced into a channel several people can see must only be answerable by
    the session that raised it, or answering it becomes a privilege escalation."""
    allowed, why = may_satisfy(card(owner="dashboard:chat-1"), "slack:someone-else")
    assert allowed is False
    assert "dashboard:chat-1" in why


def test_an_UNBOUND_card_is_answerable_from_anywhere():
    """Deliberate, not an oversight: a run the user started themselves should be answerable from
    whichever surface they are at. Requiring an owner match would mean starting a run in chat
    and
    being unable to answer it from the dashboard."""
    assert may_satisfy(card(owner=""), "anything") == (True, "")


def test_the_refusal_NAMES_the_owner():
    """ "Not allowed" leaves the user unable to act; naming the owner tells them where to go."""
    assert "dashboard:chat-1" in may_satisfy(card(owner="dashboard:chat-1"), "other")[1]


# ── staleness re-notify ──


def test_a_card_older_than_the_window_earns_ONE_reminder():
    fires, why = should_renotify(card(created_at=NOW - (RENOTIFY_AFTER_HOURS + 1) * HOUR), now=NOW)
    assert fires is True
    assert "unanswered" in why


def test_a_FRESH_card_is_not_reminded():
    """A shorter window fires while the user is simply busy."""
    fires, why = should_renotify(card(created_at=NOW - 2 * HOUR), now=NOW)
    assert fires is False
    assert "2.0h old" in why


def test_a_card_already_reminded_is_NOT_reminded_again():
    """A card that reminds every day becomes the notification the user mutes, and muting is
    per-source — it takes every genuinely urgent card with it."""
    stale = card(created_at=NOW - 100 * HOUR, renotifications=MAX_RENOTIFICATIONS)
    fires, why = should_renotify(stale, now=NOW)
    assert fires is False
    assert "already reminded" in why


def test_a_TRANSIENT_card_is_never_reminded():
    """It is not the user's to answer, so a reminder is pure noise about the system's own
    waiting."""
    stale = card(created_at=NOW - 100 * HOUR, block_kind=BlockKind.TRANSIENT)
    assert should_renotify(stale, now=NOW)[0] is False


@pytest.mark.parametrize("status", ["handled", "dismissed", "sent"])
def test_a_closed_card_is_not_reminded(status):
    stale = card(created_at=NOW - 100 * HOUR)
    fires, why = should_renotify(stale, now=NOW, status=status)
    assert fires is False
    assert status in why


def test_a_SEEN_card_can_still_be_reminded():
    """Seen is the read/unread boundary, not an answer. A card the user glanced at and left is
    exactly
    the one a reminder is for."""
    stale = card(created_at=NOW - 100 * HOUR)
    assert should_renotify(stale, now=NOW, status="seen")[0] is True


def test_a_card_with_no_creation_time_cannot_be_aged():
    assert should_renotify(card(created_at=0.0), now=NOW)[0] is False


def test_the_reminder_counter_INCREMENTS():
    """Reminding without incrementing would remind every sweep, which is the failure the cap
    exists to
    prevent."""
    once = marked_renotified(card(created_at=NOW - 100 * HOUR))
    assert once.renotifications == 1
    assert should_renotify(once, now=NOW)[0] is False


def test_marking_returns_a_NEW_card():
    original = card(created_at=NOW)
    marked_renotified(original)
    assert original.renotifications == 0


def test_the_reminder_text_names_the_question_without_repeating_the_card():
    """A reminder that repeats the whole card is a second card, and the user has to work out whether
    it is the same one."""
    text = renotify_text(card(blocker="Publish the draft?"))
    assert "Publish the draft?" in text
    assert len(text) < 200


# ── expiry ──


def test_a_card_with_no_deadline_NEVER_expires():
    """Most gates wait for a person with no deadline, and inventing one would silently abandon runs
    the user still intends to answer."""
    assert expired(card(expires_at=0.0), now=NOW + 10**9) is False


def test_a_card_past_its_deadline_is_expired():
    assert expired(card(expires_at=NOW - 1), now=NOW) is True


def test_a_card_before_its_deadline_is_not():
    assert expired(card(expires_at=NOW + HOUR), now=NOW) is False


def test_the_ttl_is_computed_from_now_plus_the_window():
    item = build_item(run_id="r", node_id="n", now=NOW, ttl_secs=HOUR)
    assert item.expires_at == NOW + HOUR


def test_no_ttl_leaves_the_deadline_unset():
    assert build_item(run_id="r", node_id="n", now=NOW).expires_at == 0.0


# ── refs round trip ──


def test_the_card_rides_the_EXISTING_refs_dict():
    """The inbox is a general attention store shared with channel messages. Widening `InboxItem`'s
    schema for one item kind would make every other kind carry empty workflow fields."""
    refs = card_refs(card(run_id="r-9", node_id="approve", resume_token="tok"))
    assert refs["workflow"] == "r-9"
    assert refs["workflow_node"] == "approve"
    assert refs["resume_token"] == "tok"
    assert refs["needs_input"]["blocker"] == "Publish?"


def test_a_card_round_trips_through_refs():
    original = card(
        block_kind=BlockKind.CAPABILITY,
        attempted=["attempt 1: failed"],
        evidence={"log": "tail"},
        owner="dashboard:x",
        created_at=NOW,
    )
    restored = from_refs(card_refs(original))
    assert restored.block_kind is BlockKind.CAPABILITY
    assert restored.attempted == ["attempt 1: failed"]
    assert restored.evidence == {"log": "tail"}
    assert restored.owner == "dashboard:x"


def test_a_row_with_NO_card_reads_as_None():
    """A row raised before this contract existed has no card, and synthesizing an empty one
    would put
    a blank decision in front of the user."""
    assert from_refs({"workflow": "r-1"}) is None
    assert from_refs({}) is None
    assert from_refs(None) is None


def test_an_UNKNOWN_block_kind_reads_as_actionable():
    """Being wrong toward actionable costs one card the user could have ignored; being wrong toward
    transient hides a decision the run is blocked on, and the run waits forever with nothing
    surfaced."""
    restored = NeedsInputItem.from_dict({"run_id": "r", "block_kind": "some_future_kind"})
    assert restored.block_kind is BlockKind.NEEDS_INPUT
    assert restored.actionable is True


# ── one decision per card ──


def test_a_multi_question_blocker_is_FLAGGED():
    """A card offering several decisions gets answered on the first and abandoned on the rest,
    and the
    run stays blocked on a card the user believes they handled."""
    findings = one_decision_lint(card(blocker="Publish it? And should we also tweet it?"))
    assert any("one decision per card" in f for f in findings)


def test_an_approval_card_with_explicit_choices_is_flagged():
    """Approve/reject IS the affordance; adding choices is two affordances for one decision."""
    findings = one_decision_lint(card(block_kind=BlockKind.APPROVAL, choices=["yes", "no"]))
    assert any("approval card" in f for f in findings)


def test_a_single_question_card_is_clean():
    assert one_decision_lint(card(blocker="Publish the draft?", choices=["yes", "no"])) == []


def test_the_lint_is_ADVISORY_and_the_card_still_ships():
    """A slightly overloaded card beats no card. The finding is reported so a template author can
    split it, not to block the run that is already waiting."""
    overloaded = card(blocker="A? B? C?")
    assert one_decision_lint(overloaded)
    assert overloaded.actionable is True


# ── the wired emit path ──


def _fake_state():
    from personalclaw.inbox import InboxStore

    class Svc:
        def __init__(self):
            self.inbox = InboxStore()

    class FakeState:
        def __init__(self):
            self._inbox_svc = Svc()

        def notify(self, *a, **k):
            pass

    return FakeState()


def test_raising_a_gate_attaches_the_STRUCTURED_card():
    """The end-to-end claim, driven against a real `InboxStore`: the row the inbox holds carries the
    card, not just a title and a token."""
    from personalclaw.workflows import attention

    state = _fake_state()
    item_id = attention.raise_gate_item(
        state,
        run_id="r-51",
        workflow="publish-article",
        node_id="approve",
        instance_path="root.children[2]",
        epoch=1,
        resume_token="tok-abc",
        ask={"kind": "approval", "prompt": "Publish the draft?"},
        attempts=[{"attempt": 1, "outcome": "failed", "note": "source unreachable"}],
        evidence={"log_tail": "x" * 2000},
        owner="dashboard:chat-1",
        project_id="p-9",
        now=NOW,
    )
    assert item_id
    rows = [i for i in state._inbox_svc.inbox.items.values() if i.id == item_id]
    assert rows, "the row must be in the LIVE store, not a private one"
    row = rows[0]
    restored = from_refs(row.refs)
    assert restored is not None
    assert restored.block_kind is BlockKind.APPROVAL
    assert restored.owner == "dashboard:chat-1"
    assert restored.attempted == ["attempt 1: failed — source unreachable"]
    assert len(restored.evidence["log_tail"]) == MAX_EVIDENCE_CHARS


def test_the_LEGACY_refs_keys_are_preserved_verbatim():
    """A surface written against today's shape must keep working — the card is additive."""
    from personalclaw.workflows import attention

    state = _fake_state()
    item_id = attention.raise_gate_item(
        state,
        run_id="r-52",
        workflow="w",
        node_id="n",
        instance_path="root",
        epoch=1,
        resume_token="tok",
        ask={"prompt": "q?"},
    )
    row = [i for i in state._inbox_svc.inbox.items.values() if i.id == item_id][0]
    assert row.refs["workflow"] == "r-52"
    assert row.refs["workflow_name"] == "w"
    assert row.refs["workflow_node"] == "n"
    assert row.refs["resume_token"] == "tok"


def test_a_gate_raised_with_NO_card_inputs_still_raises_a_row():
    """A required card argument would have made the whole existing gate path a breaking change for a
    payload most callers cannot yet supply."""
    from personalclaw.workflows import attention

    state = _fake_state()
    assert attention.raise_gate_item(
        state,
        run_id="r-53",
        workflow="w",
        node_id="n",
        instance_path="root",
        epoch=1,
        resume_token="tok",
    )
