"""Refine proposals from a detected stumble — LEARNING-VISIBILITY S3 (the refinement arm).

``after_turn_review.detect_stumble`` decides that a turn used a skill and still went wrong.
This module turns that verdict into ONE reviewable artifact: a ``kind="refine"`` proposal on
the EXISTING queue (``skills/proposals.py``), carrying the refinement body accept will apply
and a unified diff of what applying it does.

Three properties are the whole point, and each is a defect this arm exists not to have:

* **The diff is computed, never claimed.** It is ``difflib`` over (the skill's current
  effective body) vs (that body with the proposed refinement block appended by the SAME
  renderer accept runs — ``overlays.render_block``). So the previewed diff is not a model's
  description of a patch; it is the patch, and a test asserts the post-accept body equals the
  diff's "after" side byte for byte.
* **It is derived at READ time, not stored.** A diff frozen at enqueue time goes stale the
  moment anything else refines the same skill, and a stale diff shown on an approval surface
  is worse than none: the user would approve a change they were not shown.
* **No model runs here.** The body is derived from the turn's own record, so the refinement
  arm's degraded floor is *proposing nothing* rather than guessing. That also makes each
  trigger unit-testable without a provider.

**Propose-don't-write.** Nothing in this module writes a skill. It appends to the review
queue; ``proposals.accept`` — a human action — is the only writer.

**Coordination, not a fork** (LEARNING-FLYWHEEL's refiner statistical gates): the flywheel's
stronger acceptance logic slots in front of the same ``enqueue(kind="refine")`` call and the
same accept path. There is no second queue, no second kind, and no second surface here.
"""

from __future__ import annotations

import difflib
import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# The daily cap is a ROLLING 24h window, not a calendar day: a calendar day lets a stumble at
# 23:59 and another at 00:01 both file, which is the burst the cap exists to prevent.
REFINE_CAP_WINDOW = timedelta(hours=24)

_DIFF_MAX = 6000
_QUOTE_MAX = 400

# One phrasing per trigger, written as skill guidance (this text becomes the refinement body a
# human accepts). Keyed by the closed trigger vocabulary — an unmapped trigger proposes
# NOTHING rather than filing an unlabelled refinement.
_BODY = {
    "correction": (
        "When this skill applies, honor the correction the user gave on {date}:\n" "\n" "{quote}"
    ),
    "failure_retry": (
        "The `{detail}` step failed on {date} and had to be retried before the task "
        "completed. Prefer the form that worked; if it fails the same way again, the "
        "working form of that step belongs in this procedure."
    ),
    "rejection": (
        "The user declined the `{detail}` action this procedure asked for on {date}. Ask "
        "before taking it, or use an approach that does not require it."
    ),
}

_DESCRIPTION = {
    "correction": "Refined after you corrected this turn",
    "failure_retry": "Refined after a step failed and had to be retried",
    "rejection": "Refined after you declined an action this skill asked for",
}


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_iso(value: object) -> datetime | None:
    """Parse an ISO 8601 stamp, tolerating a naive one by reading it as UTC.

    Returns ``None`` for anything unparseable, and every caller treats ``None`` as "cannot
    prove this is recent" — i.e. it does NOT satisfy the cap. A record whose timestamp cannot
    be read must not silence the next proposal.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def refinement_description(trigger: str) -> str:
    """The reviewer-facing one-liner for a stumble-driven refine proposal."""
    return _DESCRIPTION.get(trigger, "")


def refinement_body(trigger: str, *, detail: str = "", quote: str = "", now: datetime) -> str:
    """The refinement body accept will append, or ``""`` for an unmapped trigger.

    ``quote`` is the user's correction. It is REDACTED and truncated here rather than at the
    call site, because this is the function whose output becomes durable skill content — the
    one place that cannot be skipped.
    """
    template = _BODY.get(trigger)
    if not template:
        return ""
    return template.format(
        date=now.date().isoformat(),
        detail=re.sub(r"[`\n\r]", "", detail or "")[:64],
        quote=_blockquote(quote),
    ).strip()


def _blockquote(text: str) -> str:
    """The user's own words as a markdown blockquote — redacted, collapsed, bounded.

    A blockquote and not a fence: this text is *accepted skill guidance* after a human says
    yes, so it has to read as guidance. The fenced, model-facing copy of the raw trace rides
    on the proposal's ``source_excerpt`` instead, which is what the review surface renders as
    untrusted data.
    """
    flat = re.sub(r"\s+", " ", text or "").strip()[:_QUOTE_MAX]
    if not flat:
        return "> (the correction was empty)"
    try:
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        flat, _ = redact_exfiltration_urls(flat)
        flat, _ = redact_credentials(flat)
    except Exception:  # pragma: no cover - redaction must never block the proposal
        logger.debug("refine: redaction failed", exc_info=True)
    return f"> {flat}"


def cap_reason(skill: str, *, now: datetime | None = None) -> str:
    """``""`` when *skill* may take another refine proposal, else why it may not.

    Reads BOTH halves of "one refine per skill per day", because either alone is wrong:

    * the pending queue — a proposal already waiting for review;
    * the skill's overlay — a refinement already ACCEPTED. ``accept`` deletes the queue entry,
      so the queue on its own forgets a same-day refinement the instant the user approves it,
      and the second proposal of the day would slip through exactly when the user was engaged.
    """
    at = now or _now()
    cutoff = at - REFINE_CAP_WINDOW
    try:
        from personalclaw.skills import overlays, proposals
    except Exception:  # pragma: no cover - import failure is not a licence to spam
        return "skills store unavailable"
    for prop in proposals.list_pending():
        if prop.kind != "refine" or prop.refine_target != skill:
            continue
        stamp = _parse_iso(prop.created_at)
        if stamp is None or stamp >= cutoff:
            return f"a refine proposal for {skill} is already pending"
    last = overlays.last_refinement(skill)
    if last is not None:
        stamp = _parse_iso(last.get("created_at"))
        if stamp is not None and stamp >= cutoff:
            return f"{skill} already took a refinement in the last 24h"
    return ""


def proposed_body(skill: str, *, description: str, procedure_md: str, trigger: str, at: str) -> str:
    """The skill body that accepting this refinement produces.

    Built by appending :func:`overlays.render_block` exactly the way
    :func:`overlays.render_with_overlay` does — same renderer, same join, same version — so
    this is not a prediction of the accept path's output but a second evaluation of it.
    """
    from personalclaw.skills import overlays
    from personalclaw.skills.loader import SkillsLoader

    current = SkillsLoader(install_builtins=False).load_skill(skill)
    if current is None:
        return ""
    block = overlays.render_block(
        {
            "description": description,
            "procedure_md": procedure_md,
            "created_at": at,
            "trigger": trigger,
        },
        overlays.next_version(skill),
    )
    if not block.strip():
        return current
    return current.rstrip() + "\n\n" + block + "\n"


def proposal_diff(prop) -> str:
    """The unified diff a ``kind="refine"`` proposal would apply, or ``""``.

    ``""`` for a non-refine proposal, for a target that no longer resolves (accept falls back
    to creating a new skill in that case, and a diff would misdescribe it), and for a
    refinement that changes nothing.
    """
    if getattr(prop, "kind", "") != "refine" or not getattr(prop, "refine_target", ""):
        return ""
    skill = prop.refine_target
    from personalclaw.skills.loader import SkillsLoader

    current = SkillsLoader(install_builtins=False).load_skill(skill)
    if current is None:
        return ""
    after = proposed_body(
        skill,
        description=prop.description,
        procedure_md=prop.procedure_md,
        trigger=getattr(prop, "trigger", ""),
        at=prop.created_at,
    )
    if not after or after == current:
        return ""
    path = f"{skill}/SKILL.md"
    return _unified(current, after, path)


def _unified(old: str, new: str, path: str) -> str:
    """difflib over two skill bodies, capped.

    Written here rather than imported from ``acp/translate.make_unified_diff``: that helper is
    a protocol adapter's, and coupling the skills package to the ACP package for four lines of
    ``difflib`` buys one owner of a passthrough at the cost of a dependency pointing the wrong
    way. There is no shared text-diff module to put it in yet; when one exists, both collapse
    into it.
    """
    old_lines = (old if old.endswith("\n") else old + "\n").splitlines(keepends=True)
    new_lines = (new if new.endswith("\n") else new + "\n").splitlines(keepends=True)
    udiff = difflib.unified_diff(old_lines, new_lines, fromfile=path, tofile=path, n=3)
    return "".join(udiff).rstrip()[:_DIFF_MAX]


def propose_refinement(
    *,
    trigger: str,
    detail: str = "",
    skill: str,
    user_message: str,
    session_key: str,
    now: datetime | None = None,
):
    """File ONE refine proposal for *skill* from a detected stumble. Returns it, or ``None``.

    ``None`` — never an exception — for: an unmapped trigger, a skill that does not resolve,
    the daily cap, a refinement that would change nothing, or a queue that is full. Each of
    those is logged at INFO with its reason, because "the stumble arm proposed nothing" and
    "the stumble arm is broken" must not be the same observation.
    """
    at = now or _now()
    body = refinement_body(trigger, detail=detail, quote=user_message, now=at)
    if not body:
        logger.info("refine: no body template for trigger %r; proposing nothing", trigger)
        return None
    reason = cap_reason(skill, now=at)
    if reason:
        logger.info("refine: capped for %s — %s", skill, reason)
        return None
    description = refinement_description(trigger)
    stamp = at.isoformat(timespec="seconds")
    # A refinement that renders to the body the skill already has is not a refinement. Checked
    # BEFORE the enqueue so the queue never holds a proposal whose diff is empty — the review
    # surface would show an approve button over no change.
    if not proposed_body(
        skill, description=description, procedure_md=body, trigger=trigger, at=stamp
    ):
        logger.info("refine: %s does not resolve; proposing nothing", skill)
        return None
    from personalclaw.skills import proposals

    return proposals.enqueue(
        slug=skill,
        description=description,
        triggers="",
        procedure_md=body,
        session_key=session_key,
        created_at=stamp,
        kind="refine",
        refine_target=skill,
        trigger=trigger,
        source_excerpt=f"[stumble: {trigger}] {user_message}",
    )
