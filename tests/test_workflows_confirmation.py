"""Tests for the ConfirmationRequest record and its gates (TASKS-SOPS §4 R6, S57).

Two shipped defects were found by measuring, and both are pinned here.

**Single-use resolution did not hold.** `consume_continuation` documented "only one
unlink succeeds", and with 8 threads racing one token MULTIPLE callers received the
payload in 36 of 40 trials — the exact double-approval replay the rule exists to
prevent. `os.rename` decides the winner before anything is read: 0 of 40.

**`security.redact` missed three real credential shapes.** A key with hyphens in the
body, a generic `api_key=<value>` assignment, and a bearer token all survived into what
is supposed to be a redacted preview — the single most likely place for a fetched
credential to reach an inbox row.

The third property is that expiry is per-TYPE. Auto-rejecting a destructive confirmation
is safe; auto-rejecting a needs-input question throws away the work that was waiting on
the answer.
"""

import threading

import pytest

from personalclaw.workflows.confirmation import (
    DEFAULT_TTL_SECS,
    EXPIRY_POLICY,
    MAX_PREVIEW_CHARS,
    MUTABLE_TYPES,
    RESOLUTIONS,
    TOOL_PROFILES,
    ConfirmationRequest,
    ConfirmationType,
    ExpiryPolicy,
    Status,
    audit_fields,
    build_request,
    dag_card,
    may_mute,
    on_expiry,
    profile,
    redact_preview,
    request_id,
    requires_hitl,
    resolve,
)

NOW = 1_700_000_000.0


# ── single-use resolution, measured under contention ──


def test_a_RACED_resolution_is_consumed_exactly_ONCE(tmp_path, monkeypatch):
    """The defect this session fixed. `consume_continuation` documented "only one unlink succeeds";
    with 8 threads racing one token, multiple callers received the payload in 36 of 40
    trials, because
    unlink does not reliably raise for the losers and every reader had already read the
    file. A double
    approval replays one clarification into downstream steps."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.workflows import human_input as hi

    multi = 0
    trials = 25
    for trial in range(trials):
        cont = hi.create_continuation(f"r{trial}", node_id="a", instance_path="p", epoch=1)
        got: dict[int, object] = {}
        barrier = threading.Barrier(8)

        def claim(index: int, run=f"r{trial}", token=cont.token) -> None:
            barrier.wait()
            got[index] = hi.consume_continuation(run, token)

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if sum(1 for v in got.values() if v is not None) != 1:
            multi += 1
    assert multi == 0, f"{multi}/{trials} trials let more than one caller consume one approval"


def test_a_SEQUENTIAL_second_claim_gets_nothing(tmp_path, monkeypatch):
    """A retried POST or a widget-and-inbox race arrives sequentially as often as concurrently."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.workflows import human_input as hi

    cont = hi.create_continuation("rx", node_id="a", instance_path="p", epoch=1)
    assert hi.consume_continuation("rx", cont.token) is not None
    assert hi.consume_continuation("rx", cont.token) is None


def test_the_claimed_record_is_RETAINED_for_audit(tmp_path, monkeypatch):
    """A resolution that crashes mid-resume should be recoverable and auditable, not silently
    gone —
    which is why the claim renames rather than deletes."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.workflows import human_input as hi

    cont = hi.create_continuation("rx", node_id="a", instance_path="p", epoch=1)
    hi.consume_continuation("rx", cont.token)
    assert any("claimed" in f.name for f in hi._dir("rx").iterdir())


def test_a_TRAVERSAL_token_is_refused(tmp_path, monkeypatch):
    """A token arrives from an HTTP path and is not a trust boundary."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.workflows import human_input as hi

    for bad in ("../../etc/passwd", "a/b", "a\\b", ".."):
        assert hi.consume_continuation("rx", bad) is None


# ── the preview is redacted at construction ──


def test_the_preview_redacts_a_provider_key_with_HYPHENS():
    """Measured: `sk-[A-Za-z0-9]{32,}` cannot match a key whose body contains hyphens, so
    `sk-live-…` survived into a redacted preview."""
    request = build_request(
        run_id="r", gate_id="g", payload="the key sk-live-ABCDEFGH1234567890 here", now=NOW
    )
    assert "sk-live-ABCDEFGH1234567890" not in request.payload_preview
    assert "REDACTED" in request.payload_preview


def test_the_preview_redacts_a_GENERIC_key_assignment():
    """There was no assignment form at all, so `api_key=<anything>` survived — and an unknown
    provider's key format is exactly what a shape-based pattern misses."""
    request = build_request(run_id="r", gate_id="g", payload="api_key=opaque12345678", now=NOW)
    assert "opaque12345678" not in request.payload_preview


def test_the_preview_redacts_a_BEARER_token():
    request = build_request(
        run_id="r",
        gate_id="g",
        payload="Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.defghijklmnop",
        now=NOW,
    )
    assert "eyJhbGciOiJIUzI1NiJ9" not in request.payload_preview


@pytest.mark.parametrize(
    "text",
    [
        "ghp_16CharsAtLeastHere0000000000000000",
        "AKIAIOSFODNN7EXAMPLE",
        "xoxb-1234567890-abcdef",
    ],
)
def test_previously_covered_shapes_are_STILL_redacted(text):
    """Widening a pattern set must not reroute what it already caught."""
    assert "REDACTED" in redact_preview(text)


@pytest.mark.parametrize(
    "prose",
    [
        "the API key rotation policy is quarterly",
        "we discussed passwords in the meeting",
        "bearer of bad news",
        "review the sk- prefix convention",
    ],
)
def test_ordinary_PROSE_is_not_over_redacted(prose):
    """A redactor that ate every sentence containing "password" would make previews useless, and a
    useless preview gets replaced by someone pasting the payload somewhere worse."""
    assert redact_preview(prose) == prose


def test_the_preview_is_BOUNDED():
    """An inbox row is a glance; a preview that scrolls is one nobody reads."""
    assert len(redact_preview("x" * 5000)) == MAX_PREVIEW_CHARS


def test_redaction_failure_WITHHOLDS_the_preview(monkeypatch):
    """Fails closed: a preview is the field most likely to carry a credential, and an unredacted one
    is worse than none."""

    def boom(_text):
        raise RuntimeError("redactor unavailable")

    monkeypatch.setattr("personalclaw.security.redact", boom)
    assert "withheld" in redact_preview("anything at all")


def test_an_empty_payload_previews_as_empty():
    assert redact_preview("") == ""
    assert redact_preview(None) == ""


# ── per-type expiry ──


def test_a_DESTRUCTIVE_confirmation_auto_REJECTS_on_expiry():
    """The action does not happen — the recoverable direction. Auto-approving a destructive action
    because nobody looked is the single worst behaviour this module could have."""
    request = build_request(
        run_id="r",
        gate_id="g",
        kind=ConfirmationType.DESTRUCTIVE_CONFIRM,
        now=NOW,
        ttl_seconds=10,
    )
    status, resolution, why = on_expiry(request, NOW + 100)
    assert status is Status.EXPIRED
    assert resolution is not None and resolution.approved is False
    assert "auto-REJECTED" in why


@pytest.mark.parametrize("kind", [ConfirmationType.APPROVAL, ConfirmationType.NEEDS_INPUT])
def test_an_answer_STILL_WANTED_is_held_not_rejected(kind):
    """The user being slow does not make the work unnecessary. Auto-rejecting a needs-input question
    throws away whatever was waiting on the answer."""
    request = build_request(run_id="r", gate_id="g", kind=kind, now=NOW, ttl_seconds=10)
    status, resolution, why = on_expiry(request, NOW + 100)
    assert status is Status.PENDING
    assert resolution is None
    assert "HELD" in why


def test_the_expiry_policy_is_declared_PER_TYPE():
    """A single global default would have to be wrong for one of them."""
    assert EXPIRY_POLICY[ConfirmationType.DESTRUCTIVE_CONFIRM] is ExpiryPolicy.AUTO_REJECT
    assert EXPIRY_POLICY[ConfirmationType.NEEDS_INPUT] is ExpiryPolicy.HOLD
    assert set(EXPIRY_POLICY) == set(ConfirmationType)


def test_a_LIVE_record_is_untouched_by_the_expiry_pass():
    request = build_request(run_id="r", gate_id="g", now=NOW, ttl_seconds=1000)
    status, resolution, why = on_expiry(request, NOW + 10)
    assert status is Status.PENDING
    assert resolution is None
    assert why == ""


def test_TTL_ZERO_means_never_expires():
    """An author writing `ttl: 0` means "wait for me". Reading it as instant expiry would auto-
    resolve
    the gate they were trying to hold open."""
    request = build_request(run_id="r", gate_id="g", now=NOW, ttl_seconds=0)
    assert request.expires_at() == 0.0
    assert request.expired(NOW + 10**9) is False


def test_the_default_ttl_is_a_WEEK():
    """The realistic case is a user who is away; a gate expiring overnight turns travel into lost
    work."""
    assert DEFAULT_TTL_SECS == 7 * 24 * 3600


# ── resolutions ──


def test_APPROVE_resolves_and_resumes():
    resolution, error = resolve("approve")
    assert error == ""
    assert resolution.approved is True
    assert resolution.resumes is True


def test_REJECT_also_RESUMES():
    """Down the declined path. Leaving it pending would strand a run whose answer has been given."""
    resolution, _ = resolve("reject")
    assert resolution.approved is False
    assert resolution.resumes is True


def test_SKIP_leaves_the_item_pending():
    """Different from rejecting it. Without skip, a user has to answer in the order the engine
    happened to ask."""
    resolution, _ = resolve("skip")
    assert resolution.still_pending is True
    assert resolution.resumes is False


def test_QUIT_neither_resumes_nor_stays_pending():
    resolution, _ = resolve("quit")
    assert resolution.resumes is False
    assert resolution.still_pending is False


def test_an_UNKNOWN_verb_is_refused_not_treated_as_a_reject():
    """A typo silently rejecting an approval would decline work the user meant to allow, and they
    would not know why."""
    resolution, error = resolve("yolo")
    assert resolution is None
    assert "unknown resolution" in error


def test_an_empty_verb_is_refused():
    assert resolve("")[0] is None


def test_a_resolution_note_survives():
    resolution, _ = resolve("approve", note="checked the diff")
    assert resolution.note == "checked the diff"


def test_the_resolution_vocabulary_is_the_FOUR_the_plan_names():
    assert set(RESOLUTIONS) == {"approve", "reject", "skip", "quit"}


# ── identity ──


def test_the_id_is_DETERMINISTIC_for_one_gate_and_epoch():
    """A re-emitted request for the same waiting gate must be recognizably the same record, not a
    second row in the inbox."""
    assert request_id("r", "g", 1) == request_id("r", "g", 1)


def test_a_REWIND_produces_a_new_request():
    """The epoch is in the key because the question is being asked about different work."""
    assert request_id("r", "g", 1) != request_id("r", "g", 2)


def test_two_gates_in_one_run_get_distinct_ids():
    assert request_id("r", "approve", 1) != request_id("r", "publish", 1)


# ── round trip ──


def test_a_request_round_trips():
    request = build_request(
        run_id="r-1",
        gate_id="approve",
        kind=ConfirmationType.DESTRUCTIVE_CONFIRM,
        risk_category="destructive_op",
        title="Delete the stale rows?",
        payload="42 rows",
        resume_token="tok",
        now=NOW,
    )
    restored = ConfirmationRequest.from_dict(request.to_dict())
    assert restored == request


def test_an_UNKNOWN_type_reads_as_approval_which_HOLDS():
    """HOLD is the safe landing for a type this build cannot classify: the run waits for a human
    rather than auto-resolving something nobody could interpret."""
    restored = ConfirmationRequest.from_dict(
        {"id": "x", "run_id": "r", "gate_id": "g", "type": "vibes"}
    )
    assert restored.type is ConfirmationType.APPROVAL
    assert restored.expiry_policy is ExpiryPolicy.HOLD


def test_an_unknown_STATUS_reads_as_pending():
    """Pending keeps the record actionable. Reading it as resolved would silently drop a gate
    the run
    is still waiting on."""
    restored = ConfirmationRequest.from_dict(
        {"id": "x", "run_id": "r", "gate_id": "g", "status": "?"}
    )
    assert restored.status is Status.PENDING


def test_the_serialized_form_carries_the_DERIVED_policy():
    """A surface deciding what to do with an expired record should not have to re-derive the
    rule.
    """
    payload = build_request(
        run_id="r", gate_id="g", kind=ConfirmationType.DESTRUCTIVE_CONFIRM, now=NOW
    ).to_dict()
    assert payload["expiry_policy"] == ExpiryPolicy.AUTO_REJECT.value
    assert payload["expires_at"] > NOW


# ── require_hitl ──


def test_a_stage_can_declare_require_hitl():
    """Approval as a PROPERTY of the step, so an author gates a stage without structurally
    inserting a
    gate node — which would change the graph shape and every path-addressed binding downstream."""
    assert requires_hitl({"prompt": "x", "require_hitl": True}) is True


def test_an_absent_or_falsey_require_hitl_is_no_gate():
    assert requires_hitl({}) is False
    assert requires_hitl({"require_hitl": False}) is False


def test_require_hitl_must_be_the_BOOLEAN_true():
    """A truthy string is an author mistake, and treating `"false"` as a gate would surprise them in
    the direction of extra prompts they cannot explain."""
    assert requires_hitl({"require_hitl": "yes"}) is False


# ── per-stage mute ──


@pytest.mark.parametrize("kind", sorted(MUTABLE_TYPES, key=lambda k: k.value))
def test_an_approval_or_question_may_be_MUTED(kind):
    assert may_mute(kind)[0] is True


def test_a_DESTRUCTIVE_confirmation_may_NOT_be_muted():
    """ "Stop asking me about deletions" is a request to remove the last check before an
    unrecoverable
    action — the one setting that cannot be undone by changing it back."""
    ok, why = may_mute(ConfirmationType.DESTRUCTIVE_CONFIRM)
    assert ok is False
    assert "cannot be muted" in why


# ── tool profiles ──


@pytest.mark.parametrize("name", sorted(TOOL_PROFILES))
def test_each_profile_resolves(name):
    found, error = profile(name)
    assert error == ""
    assert found["capability"] in {"research", "mutating"}


def test_the_READ_ONLY_profile_confirms_nothing():
    """It cannot reach a write tool, so a confirmation would be a prompt about an action that cannot
    happen — and prompts about impossible actions are how a user learns to click through."""
    found, _ = profile("read_only")
    assert found["confirm"] == ()
    assert found["capability"] == "research"


def test_an_OUTWARD_profile_confirms_the_most():
    found, _ = profile("outward")
    assert ConfirmationType.DESTRUCTIVE_CONFIRM in found["confirm"]


def test_an_UNKNOWN_profile_is_refused_not_defaulted():
    """Defaulting loose would silently grant a stage more than the author asked
    for; defaulting strict would silently break a stage that needs to write, and
    the author would debug the wrong thing."""
    found, error = profile("nonsense")
    assert found is None
    assert "unknown tool profile" in error


def test_the_profile_vocabulary_reuses_S48s_capability_words():
    """Two least-privilege vocabularies would disagree about a tool, and the
    looser one would win."""
    from personalclaw.workflows.batch_compile import Capability

    words = {p["capability"] for p in TOOL_PROFILES.values()}
    assert words <= {c.value for c in Capability}


# ── audit ──


def test_a_resolution_audit_names_WHO_approved():
    """ "Who approved this" is the question an audit exists to answer, and a log
    recording only that an approval happened cannot answer it."""
    request = build_request(run_id="r-1", gate_id="approve", now=NOW)
    request.resolved_by = "dashboard:chat-1"
    resolution, _ = resolve("approve")
    fields = audit_fields(request, resolution)
    assert fields["resolved_by"] == "dashboard:chat-1"
    assert fields["approved"] is True
    assert fields["operation"] == "confirmation.approve"


def test_an_unattributed_resolution_says_UNKNOWN_rather_than_empty():
    """An empty resolver reads as "no resolver", which is indistinguishable from
    an unrecorded one."""
    request = build_request(run_id="r", gate_id="g", now=NOW)
    resolution, _ = resolve("reject")
    assert audit_fields(request, resolution)["resolved_by"] == "unknown"


# ── the DagView card ──


def test_a_PENDING_request_offers_both_verbs():
    """The backend seam the FE's declared-but-unwired `onApprove`/`onDeny` has been waiting for."""
    card = dag_card(build_request(run_id="r", gate_id="approve", now=NOW))
    assert card.awaiting is True
    assert card.can_approve and card.can_deny


def test_a_RESOLVED_request_offers_NEITHER():
    """A node still offering Approve after the gate was answered is how a user
    double-approves — exactly what the claim primitive had to be fixed to prevent."""
    request = build_request(run_id="r", gate_id="approve", now=NOW)
    request.status = Status.RESOLVED
    card = dag_card(request)
    assert card.awaiting is False
    assert not card.can_approve and not card.can_deny


def test_the_card_carries_the_REDACTED_preview():
    """One record, two surfaces. Two builders would drift, and the drift shows as a node offering
    Approve for a gate the inbox already resolved."""
    request = build_request(run_id="r", gate_id="g", payload="api_key=opaque12345678", now=NOW)
    assert "opaque12345678" not in dag_card(request).preview


def test_the_card_names_its_node_for_deep_linking():
    card = dag_card(build_request(run_id="r", gate_id="publish", now=NOW))
    assert card.node_id == "publish"
    assert card.confirmation_id.startswith("cr-")
