"""SDK: feedback capture — the one write API for AI-judgment verdicts (Tier-S).

Stable re-export of :func:`personalclaw.feedback.record_feedback` and
:class:`~personalclaw.feedback.FeedbackRecord` so an app records feedback on its
own judgments identically to core surfaces. App bundles calling through the
``/api/feedback`` route get ``source_app`` stamped server-side and their producer
namespaced to ``app:<name>:<producer>``; in-process SDK callers SHOULD pass their
producer as ``producer_kind="app", producer_id="<app>:<producer>"`` themselves.

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
