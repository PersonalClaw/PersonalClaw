"""Every run-control verb answers 404 for a run that does not exist.

Most already did. FOUR did not, and they did not fail quietly — they answered confidently about a
run that was not there. Three were reported; the fourth this rail found on its first run:

* ``confirm {verb: skip}`` and ``{verb: quit}`` returned **200**
  ``{"resumed": false, "still_pending": true}`` for a nonexistent id, because
  ``resolve_confirmation`` early-returned for the non-resuming verbs BEFORE any ``store.get``.
  Only approve/reject reached ``resume_run``, which is where the 404 lived. A tool firing skip at
  a typo'd or already-deleted run was told it had worked.
* ``rewind`` / ``run_from`` / ``edit`` returned **409 run_not_live** with "resume the run before
  rewind" — remediation for a run that cannot be resumed because there is nothing to resume. They
  asked ``_live()`` first, and a nonexistent run has no controller either.
* ``preview_edit`` answered ``WF_RUN_NO_SPEC`` — "run has no readable spec" — which ``_STATUS_MAP``
  translates to a **500 spec_unreadable**. The worst of the four: a typo'd id reported a server
  fault. Nobody reported this one; the table below did, which is the argument for enumerating the
  verbs rather than fixing the two that were noticed.

Both are issue 765. The same missing precheck is behind issue 679: ``resume`` answered
``{"resumed": true}`` on a complete/failed/cancelled run *and wrote to the finished run's ``extra``
on the way through*, because it checked existence but never terminality — while its three siblings
(cancel, pause, steer) all refuse with ``WF_RUN_ALREADY_TERMINAL``.

**Why the fix is one helper.** That ``_service_failure("WF_RUN_NOT_FOUND", ...)`` line appeared
**fourteen times, verbatim**. That is the reason three paths could omit it without looking
odd in review: there was no single thing to be missing. ``_run_or_missing`` makes "did this verb
check?" greppable.

🪤 THE FAKE VERSION OF THIS TEST asserts that `confirm{skip}` and `rewind` 404. That closes the
reported cases and says nothing about the next verb, which is exactly how four of thirteen came to
disagree in the first place. So the table below is the LIST of control verbs, and a new one has to
be added to it — a verb absent from the table is caught by
`test_the_table_covers_every_control_verb`, which reads the service's own exports rather than
trusting the table to be complete.
"""

from __future__ import annotations

import inspect

import pytest

from personalclaw.workflows import service, store
from personalclaw.workflows.models import RunStatus

MISSING = "no-such-run-zzz"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


class _FakeSupervisor:
    """A supervisor that knows no controllers.

    Deliberately not None: with no supervisor, `_live` returns None for every id, so a verb could
    pass these legs by accident. This says "the supervisor is present and has never heard of that
    run", which is the real shape of the bug — the id is absent, not the engine.
    """

    def controller(self, run_id: str):  # noqa: D102 - the seam under test
        return None


#: Every run-control verb, as `(name, callable)`. The point of the list is that it is a LIST: the
#: three that were wrong were wrong precisely because nothing enumerated their obligations.
CONTROL_VERBS = [
    ("cancel_run", lambda s: service.cancel_run(MISSING, supervisor=s)),
    ("pause_run", lambda s: service.pause_run(MISSING, supervisor=s)),
    ("steer_run", lambda s: service.steer_run(MISSING, "go left")),
    ("resume_run", lambda s: service.resume_run(MISSING, supervisor=s, answer="yes")),
    ("rewind_run", lambda s: service.rewind_run(MISSING, "root", supervisor=s)),
    ("run_from", lambda s: service.run_from(MISSING, "root", supervisor=s)),
    (
        "edit_run",
        lambda s: service.edit_run(MISSING, [{"op": "retry", "node_id": "root"}], supervisor=s),
    ),
    ("pending_steering", lambda s: service.pending_steering(MISSING)),
    ("preview_edit", lambda s: service.preview_edit(MISSING, [{"op": "retry", "node_id": "root"}])),
    (
        "resolve_confirmation:approve",
        lambda s: service.resolve_confirmation(MISSING, supervisor=s, verb="approve"),
    ),
    (
        "resolve_confirmation:reject",
        lambda s: service.resolve_confirmation(MISSING, supervisor=s, verb="reject"),
    ),
    (
        "resolve_confirmation:skip",
        lambda s: service.resolve_confirmation(MISSING, supervisor=s, verb="skip"),
    ),
    (
        "resolve_confirmation:quit",
        lambda s: service.resolve_confirmation(MISSING, supervisor=s, verb="quit"),
    ),
]


@pytest.mark.parametrize("name,call", CONTROL_VERBS, ids=[n for n, _ in CONTROL_VERBS])
def test_a_nonexistent_run_is_NOT_FOUND(name: str, call) -> None:
    body = call(_FakeSupervisor())
    assert body["ok"] is False, f"{name} reported success for a run that does not exist: {body}"
    assert body["code"] == "WF_RUN_NOT_FOUND", (
        f"{name} answered {body['code']!r}. A nonexistent id is a 404, not a state complaint — "
        "`run_not_live` tells the user to resume a run that is not there (issue 765)."
    )


def test_skip_and_quit_do_not_claim_a_pending_gate() -> None:
    """The reported symptom, pinned as itself.

    `{"resumed": false, "still_pending": true}` reads as "your skip was recorded and the gate is
    still waiting" — a specific, false statement about a run that does not exist. The generic leg
    above would also catch this, but a red naming the shape is worth more than one naming a code.
    """
    for verb in ("skip", "quit"):
        body = service.resolve_confirmation(MISSING, supervisor=_FakeSupervisor(), verb=verb)
        assert body["ok"] is False
        assert "still_pending" not in body
        assert body.get("resumed") is None


def test_an_unknown_VERB_is_still_refused_before_anything_else() -> None:
    """🪤 The floor for the reordering. The existence check moved ABOVE the verb split, and it must
    not have moved above the verb VALIDATION — a typo'd verb has to stay a 400 rather than becoming
    a 404 about the run, or the caller is told to fix the wrong thing."""
    body = service.resolve_confirmation(MISSING, supervisor=_FakeSupervisor(), verb="aprove")
    assert body["ok"] is False
    assert body["code"] == "WF_CONFIRM_VERB_INVALID"


def _terminal_run(status: RunStatus):
    from personalclaw.workflows.models import WorkflowRun

    return store.create(WorkflowRun(id="", workflow_name="wf", status=status))


@pytest.mark.parametrize("status", [RunStatus.COMPLETE, RunStatus.FAILED, RunStatus.CANCELLED])
def test_resume_REFUSES_a_terminal_run_and_does_not_write_to_it(status: RunStatus) -> None:
    """issue 679. The refusal matters, and so does the absence of the write.

    The clear-pause path pops `pause_requested` and calls `store.save`, so answering 200 here was
    not cosmetic — it mutated a finished run. Asserting only the status code would leave the write
    unexamined, so this marks the run and checks the mark survives.
    """
    run = _terminal_run(status)
    run.extra["pause_requested"] = True
    store.save(run)

    body = service.resume_run(run.id, supervisor=_FakeSupervisor())

    assert body["ok"] is False
    assert body["code"] == "WF_RUN_ALREADY_TERMINAL"
    assert status.value in body["message"]
    # The finished run is untouched — this is the half that made 679 more than a wrong status.
    assert store.get(run.id).extra.get("pause_requested") is True


def test_the_table_covers_every_control_verb() -> None:
    """The vacuity floor, and the part that outlives this fix.

    A parametrized table is only as good as its completeness, and a hand-written list silently
    stops covering the next verb. So: read the service's own public run-control functions and
    require each to appear in `CONTROL_VERBS`. A new verb reds HERE, with instructions, rather
        than shipping without a guard.
    """
    # Functions whose first positional parameter is `run_id` and that live in the control section.
    # Reads are excluded by name: `status`/`output`/`detail`/`observe` answer questions about a run
    # and already 404 on their own, and this rail is about the verbs that ACT.
    exclude = {
        "run_status",
        "run_output",
        "run_detail",
        "observe_run",
        "list_runs",
        "run_continuations",
        "start_run",
        "fork_run",
        "resume_run_from_token",
    }
    covered = {n.split(":")[0] for n, _ in CONTROL_VERBS}
    missing = []
    for name, fn in vars(service).items():
        if name.startswith("_") or not inspect.isfunction(fn) or name in exclude:
            continue
        params = list(inspect.signature(fn).parameters)
        if not params or params[0] != "run_id":
            continue
        if not any(
            k in name
            for k in ("cancel", "pause", "steer", "resume", "rewind", "run_from", "edit", "confirm")
        ):
            continue
        if name not in covered:
            missing.append(name)
    assert not missing, (
        f"{missing} take a run_id and act on the run but are not in CONTROL_VERBS. Add them, and "
        "make sure each resolves the run through `_run_or_missing` before doing anything else "
        "(issues 765, 679)."
    )
