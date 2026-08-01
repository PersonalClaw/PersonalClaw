"""The learning subsystem — one gate, one hygiene policy, one staging log.

Three capture cadences observe different signals (per-turn, session-end, run-end),
and before this package each one decided independently whether it was allowed to
run and what it was allowed to look at. That is how a capture path silently
disagrees with its neighbour: two copies of an eligibility rule drift, and a
filter that exists on one path is simply absent on another.

So the three questions every cadence has to answer are answered in exactly one
place each:

- **May I capture at all?** → :mod:`personalclaw.learning.gate` (``LearningGate``)
- **What am I allowed to look at?** → :mod:`personalclaw.learning.hygiene`
- **Where does the raw signal land?** → :mod:`personalclaw.learning.staging`

A cadence composes all three: gate first (cheapest, and a denial means nothing
else runs), then hygiene on the text, then staging for what survives.
"""

from __future__ import annotations

from personalclaw.learning.gate import (
    Cadence,
    GateDecision,
    GateReason,
    LearningGate,
)
from personalclaw.learning.hygiene import (
    MIN_EVIDENCE_DEFAULT,
    HygieneVerdict,
    is_system_injected,
    scrub,
    session_score,
)
from personalclaw.learning.staging import (
    FlushOutcome,
    StagingStore,
    input_hash,
)

__all__ = [
    "Cadence",
    "FlushOutcome",
    "GateDecision",
    "GateReason",
    "HygieneVerdict",
    "LearningGate",
    "MIN_EVIDENCE_DEFAULT",
    "StagingStore",
    "input_hash",
    "is_system_injected",
    "scrub",
    "session_score",
]
