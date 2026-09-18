"""SDK: feedback capture — the one write API for AI-judgment verdicts (Tier-S).

Stable re-export of :func:`personalclaw.feedback.record_feedback` and
:class:`~personalclaw.feedback.FeedbackRecord` so an app records feedback on its
own judgments identically to core surfaces.

**Pass ``source_app="<your app name>"`` and a BARE ``producer_id``.** Core does the
namespacing: a call with ``source_app`` set lands as
``producer_kind="app", producer_id="<app>:<producer>"`` with the target kind forced
to ``app_judgment``, identically to the ``/api/feedback`` route (#2784). Callers used
to be told to pre-namespace their own producer here; doing that now yields
``<app>:<app>:<producer>``, because the rule moved into the write API where BOTH
doors pass through it — an app-supplied producer is a leaf name, never a namespace.

👍 is silent-positive (recorded only for the accuracy denominator); only 👎 with
an optional short reason ever feeds learning. Deterministic, local-only — records
never leave the instance.
"""

from personalclaw.feedback import (  # noqa: F401
    PRODUCER_KINDS,
    TARGET_KINDS,
    FeedbackRecord,
    current_verdict,
    record_feedback,
)

__all__ = [
    "FeedbackRecord",
    "record_feedback",
    "current_verdict",
    "TARGET_KINDS",
    "PRODUCER_KINDS",
]
