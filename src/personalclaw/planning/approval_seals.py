"""Your yes to a plan step, sealed to the step as its review showed it, and kept in an owner book.

A step's ``approved`` status in its plan records that a yes was given. What the yes was given to
is recorded here, in the owner book ``grants/plan_steps.json`` (`owner_grants`), sealed to the
SHA-256 of what the review showed: the step's kind, title, objective and whole artifact, masked as
a read masks them (:func:`shown`). ``grants/`` is an owner-only place (`owner_only`).

Where an approval is used, each approved step is checked against its seal
(:func:`reopen_unsealed`): a loop's plan before it is built into its spec, a chat's plan before it
briefs a fresh chat. A step with no seal of yours, or whose text changed since, goes back to
review, and nothing is built or run from it. A change to how a read masks the step changes what it
showed, so that step goes back to review too.

An approval names the revision of what it was shown (:func:`review_revision`, in ``If-Match``), so
a press over a step that changed since its read is refused instead of approving the change.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from personalclaw.owner_grants import GrantBook
from personalclaw.planning.session import PlanSession, PlanStep, StepStatus, _touch

if TYPE_CHECKING:
    from aiohttp import web

#: The book your yeses to plan steps are recorded in.
BOOK = GrantBook("plan_steps")

#: The fields of a step its review shows, and so the ones a yes covers.
_SHOWN = ("kind", "title", "objective", "artifact")


def loop_scope(loop_id: str) -> str:
    """The scope of a loop's plan."""
    return f"loop:{loop_id}"


def chat_scope(chat_key: str) -> str:
    """The scope of a chat's plan."""
    return f"chat:{chat_key}"


def shown(step: PlanStep) -> str:
    """The step as its review shows it: its kind, title, objective and whole artifact, masked as
    a read masks them (`planning.session.wire`), in one canonical JSON text."""
    from personalclaw.security import redact_values_for_display

    row = step.to_dict()
    document: dict[str, Any] = {key: redact_values_for_display(row[key]) for key in _SHOWN}
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )


def review_revision(step: PlanStep) -> str:
    """The revision an approval of *step* names in ``If-Match``: that of what its review shows."""
    from personalclaw.stale_write import revision_of

    return revision_of(shown(step))


def stale_refusal(request: web.Request, step: PlanStep) -> web.Response | None:
    """The refusal an approval of *step* gets when it does not name the revision of the step as it
    is now (``If-Match``), else ``None``: ``428 revision_required`` when it names none, ``409
    stale_write`` when the step changed after the read it names. The codes of every whole-document
    write (`stale_write`), in an approval's words. Called with nothing awaited before the approval
    is written."""
    from personalclaw.http_errors import json_error
    from personalclaw.stale_write import REVISION_HEADER, claimed_revision

    claimed = claimed_revision(request)
    if not claimed:
        return json_error(
            "revision_required",
            message=(
                f"An approval names the plan step {step.title!r} as you read it: read the plan "
                f"and send the step's review_revision in {REVISION_HEADER}."
            ),
            status=428,
        )
    if claimed != review_revision(step):
        return json_error(
            "stale_write",
            message=(
                f"The plan step {step.title!r} changed after you read it, so it was not approved. "
                "Review it as it is now."
            ),
            status=409,
        )
    return None


def _prefix(scope: str, session: PlanSession) -> str:
    """The start of every yes's key for *session*: the plan's scope and the plan itself (its start
    time, so a new plan of the same loop or chat never matches an old one's yes)."""
    return f"{scope}:{session.created_at!r}:"


def _key(scope: str, session: PlanSession, step: PlanStep) -> str:
    """A yes's key: the plan (:func:`_prefix`) and the step."""
    return _prefix(scope, session) + step.id


#: Why a plan is put back to you when a step you approved is no longer in it.
DROPPED = (
    "A step you approved is no longer in this plan, so its approved steps wait for your review "
    "again: what is left is not the plan you said yes to."
)


def seal(scope: str, session: PlanSession, step: PlanStep) -> None:
    """Record your yes to *step* as it stands. Only an approval of yours calls this."""
    BOOK.give(_key(scope, session, step), shown(step))


def sealed(scope: str, session: PlanSession, step: PlanStep) -> bool:
    """Whether *step*, as it stands, is what you said yes to."""
    return BOOK.holds(_key(scope, session, step), shown(step))


def reopen_unsealed(scope: str, session: PlanSession, *, but: str = "") -> list[str]:
    """Put each approved step of *session* without your seal, or changed since it, back to review,
    and return their ids. *but* names a step the caller is approving now.

    A yes is sealed per step, so a step you approved that is no longer in the plan is seen here, by
    its seal: the plan left is not the one you said yes to, so every approved step goes back to you,
    the session says why (``design_error``, :data:`DROPPED`), and the missing ids are returned with
    them, so the plan is neither built nor run. Once the caller has written the session so,
    :func:`forget_dropped` forgets the missing step's yes: your next yes to each step left is your
    yes to the plan without it."""
    dropped = _dropped(scope, session)
    reopened = [
        step.id
        for step in session.steps
        if step.id != but
        and step.status == StepStatus.APPROVED.value
        and (dropped or not sealed(scope, session, step))
    ]
    for step in session.steps:
        if step.id in reopened:
            step.status = StepStatus.AWAITING_REVIEW.value
    if dropped:
        session.design_error = DROPPED
    if reopened or dropped:
        _touch(session)
    return dropped + reopened


def _dropped(scope: str, session: PlanSession) -> list[str]:
    """The ids of the steps of *session* you said yes to that it no longer holds."""
    present = {step.id for step in session.steps}
    prefix = _prefix(scope, session)
    return sorted(
        key[len(prefix) :]
        for key in BOOK.keys()
        if key.startswith(prefix) and key[len(prefix) :] not in present
    )


def forget_dropped(scope: str, session: PlanSession) -> None:
    """Forget your yes to each step *session* no longer holds. Called only after the session that
    :func:`reopen_unsealed` put back to you is written, so a write that fails leaves the yes, and
    the plan is put back to you again rather than built without the step."""
    prefix = _prefix(scope, session)
    for step_id in _dropped(scope, session):
        BOOK.revoke(prefix + step_id)


def forget(scope: str) -> None:
    """Forget every yes recorded for *scope*: its plan was cancelled, replaced or deleted."""
    prefix = scope + ":"
    for key in BOOK.keys():
        if key.startswith(prefix):
            BOOK.revoke(key)
