"""A plan session records when it last PROGRESSED, so stall detection survives a reload (issue 488).

The walkthrough seeds its quiet clock from the client: `useRef(Date.now())` at component mount. So
`quietMs` measured "how long since this page loaded", not "how long since the session progressed" —
a planning session dead for 5.6 hours restarted its 3-minute countdown on every reload and never
offered Retry, while two spinners claimed work was in flight.

`created_at` cannot stand in for the missing clock: a HEALTHY session planning for 20 minutes is 20
minutes old, so a minutes-scale threshold would fire Retry on live work — and Retry kills an
in-flight pass. Only a real last-progress stamp separates "quiet because dead" from "old because
busy", which is why this field exists rather than the frontend deriving one.
"""

from __future__ import annotations

import inspect

import pytest

from personalclaw.planning import session as PS
from personalclaw.planning.session import PlanSession, PlanStep, StepStatus

#: Every mutator that changes session state. Named so a NEW one cannot be added without either
#: stamping the clock or consciously appearing in this list — the "forgot one" failure is silent,
#: because a missed stamp only shows up as a stall verdict that is quietly wrong.
MUTATORS = (
    "approve_step",
    "comment_step",
    "mark_running",
    "mark_pending",
    "submit_artifact",
    "edit_artifact",
)


def _session(status: str = StepStatus.PENDING.value) -> PlanSession:
    return PlanSession(
        project_id="p-1",
        created_at=1000.0,
        updated_at=1000.0,
        steps=[PlanStep(id="step-0", kind="brief", title="Brief", status=status)],
    )


# ── the field exists, round-trips, and is backfilled ────────────────────────────────────────


def test_updated_at_round_trips():
    s = _session()
    s.updated_at = 4242.0
    assert PlanSession.from_dict(s.to_dict()).updated_at == 4242.0


def test_a_session_written_before_the_field_backfills_from_created_at():
    """🔑 Without this, every pre-existing session reads `updated_at == 0` — "quiet since 1970" —
    and offers Retry the instant it is opened. That would be a worse bug than the one being fixed,
    because Retry kills an in-flight pass."""
    legacy = {"project_id": "p-1", "created_at": 900.0, "steps": [], "design_error": ""}
    assert "updated_at" not in legacy
    assert PlanSession.from_dict(legacy).updated_at == 900.0


def test_an_explicit_zero_also_backfills():
    # A half-written file can carry a literal 0; treat it the same as absent.
    d = {"project_id": "p-1", "created_at": 900.0, "updated_at": 0, "steps": []}
    assert PlanSession.from_dict(d).updated_at == 900.0


def test_a_freshly_CONSTRUCTED_session_backfills_too():
    """🔑 The backfill belongs to construction, not to reading, and this is why.

    `dashboard/chat_plan.py` builds a `PlanSession` and serializes it straight into a POST
    response with no read round-trip. A backfill that lived only in `from_dict` therefore put
    `updated_at: 0.0` on the wire for a session created seconds ago — a payload claiming it last
    progressed in 1970. The field's whole contract is that it means LAST PROGRESS, so every path
    that produces one has to satisfy it, not just the file reader.
    """
    fresh = PlanSession(project_id="p-1", created_at=1234.0)
    assert fresh.updated_at == 1234.0
    assert fresh.to_dict()["updated_at"] == 1234.0


def test_an_explicit_updated_at_is_never_overwritten_by_the_backfill():
    """The backfill is a FLOOR for a missing value, not a reset. If it clobbered a real stamp,
    every construction would reset the stall clock to creation time and a long-running session
    would look permanently dead."""
    s = PlanSession(project_id="p-1", created_at=1000.0, updated_at=9999.0)
    assert s.updated_at == 9999.0


def test_a_corrupt_payload_still_yields_a_session():
    # The existing tolerance must survive the new field.
    assert PlanSession.from_dict("not a dict").project_id == ""  # type: ignore[arg-type]


# ── every mutator stamps, and only when it really changed something ──────────────────────────


def test_every_named_mutator_exists():
    """Guards the list itself against a rename, which would otherwise make the sweep vacuous."""
    for name in MUTATORS:
        assert callable(getattr(PS, name)), f"{name} is listed but missing"


def test_the_list_covers_every_mutator_in_the_module():
    """The exhaustiveness half: a NEW mutator that forgets to stamp is caught here rather than by
    a stall verdict quietly going wrong months later."""

    # 🪤 Two traps here, both of which made a first draft match NOTHING while looking right:
    # the module uses `from __future__ import annotations`, so a return annotation is the STRING
    # "bool" rather than the type; and `str(signature)` renders annotations QUOTED
    # (`session: 'PlanSession'`), so a substring check for `session: PlanSession` never hit.
    # Keying on the first parameter's NAME avoids both, and also excludes the imported
    # `asdict`/`dataclass`/`field` that `isfunction` otherwise picks up from module scope.
    def _takes_a_session(obj: object) -> bool:
        params = list(inspect.signature(obj).parameters)  # type: ignore[arg-type]
        return bool(params) and params[0] == "session"

    public = {
        name
        for name, obj in vars(PS).items()
        if inspect.isfunction(obj)
        and not name.startswith("_")
        and _takes_a_session(obj)
        and inspect.signature(obj).return_annotation in (bool, "bool")
    }
    assert public, "the introspection found no session functions at all — the filter is wrong"
    # `is_complete` reads; it is not a mutator. Everything else that takes a session and answers
    # bool is one.
    missing = public - {"is_complete"} - set(MUTATORS)
    stale = set(MUTATORS) - public
    assert not missing, f"mutators that do not appear in MUTATORS (do they stamp?): {missing}"
    assert not stale, f"MUTATORS names that no longer exist: {stale}"


@pytest.mark.parametrize(
    "name, setup_status, args",
    [
        ("approve_step", StepStatus.AWAITING_REVIEW.value, ()),
        ("comment_step", StepStatus.AWAITING_REVIEW.value, ("looks wrong",)),
        ("mark_running", StepStatus.PENDING.value, ()),
        ("mark_pending", StepStatus.RUNNING.value, ()),
        ("submit_artifact", StepStatus.RUNNING.value, ({"markdown": "x"},)),
        ("edit_artifact", StepStatus.AWAITING_REVIEW.value, ("edited",)),
    ],
)
def test_a_successful_mutation_stamps_the_clock(name, setup_status, args, monkeypatch):
    # The clock is DRIVEN, not raced. Asserting the stamp lands within N seconds of the real
    # `time.time()` would make this test host-load-dependent for no gain — the thing worth
    # pinning is that the stamp is the CURRENT time from the module's own clock, not an
    # arbitrary later value, and injecting the clock says exactly that.
    monkeypatch.setattr(PS.time, "time", lambda: 5_000.0)
    s = _session(setup_status)
    before = s.updated_at
    assert getattr(PS, name)(s, "step-0", *args) is True, f"{name} did not apply"
    assert s.updated_at > before, f"{name} changed state without stamping updated_at"
    assert s.updated_at == 5_000.0, f"{name} did not stamp from the module clock"


@pytest.mark.parametrize(
    "name, wrong_status, args",
    [
        ("approve_step", StepStatus.PENDING.value, ()),
        ("comment_step", StepStatus.PENDING.value, ("hi",)),
        ("mark_running", StepStatus.APPROVED.value, ()),
        ("mark_pending", StepStatus.APPROVED.value, ()),
        ("submit_artifact", StepStatus.PENDING.value, ({"markdown": "x"},)),
        ("edit_artifact", StepStatus.PENDING.value, ("edited",)),
    ],
)
def test_a_REFUSED_mutation_does_not_stamp(name, wrong_status, args):
    """🔑 The field must mean LAST PROGRESS, not last write. A poll that re-applies unchanged state
    must not reset the stall clock, or the countdown restarts forever exactly as it did before."""
    s = _session(wrong_status)
    before = s.updated_at
    assert getattr(PS, name)(s, "step-0", *args) is False
    assert s.updated_at == before, f"{name} stamped the clock on a no-op"


def test_an_unknown_step_id_does_not_stamp():
    s = _session(StepStatus.AWAITING_REVIEW.value)
    before = s.updated_at
    assert PS.approve_step(s, "step-nope") is False
    assert s.updated_at == before


def test_an_empty_comment_does_not_stamp():
    s = _session(StepStatus.AWAITING_REVIEW.value)
    before = s.updated_at
    assert PS.comment_step(s, "step-0", "   ") is False
    assert s.updated_at == before


# ── the payload carries it to the client ────────────────────────────────────────────────────


def test_the_wire_payload_exposes_updated_at():
    """The frontend cannot compute this, so it has to be sent."""
    assert "updated_at" in _session().to_dict()
