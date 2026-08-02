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
- **How does a change get made?** → :mod:`personalclaw.learning.proposals`
- **Is it still relevant?** → :mod:`personalclaw.learning.decay` + `.usage` + `.curator`
- **What reaches the prompt?** → :mod:`personalclaw.learning.surfacing`

A cadence composes them in that order: gate first (cheapest, and a denial means
nothing else runs), then hygiene on the text, then staging for what survives, and
finally a proposal for anything that would durably change behaviour.

That last module carries the flywheel's trust anchor: **autonomous synthesis
proposes; the human installs.** The system may notice anything and change nothing
on its own.
"""

from __future__ import annotations

from personalclaw.learning.curator import Candidate, CuratorReport, MutationLog, run_aging
from personalclaw.learning.decay import DecayVerdict
from personalclaw.learning.decay import evaluate as evaluate_decay
from personalclaw.learning.decay import strength
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
from personalclaw.learning.proposals import (
    ChangeManifest,
    Kind,
    Proposal,
    Status,
    Verdict,
    content_fingerprint,
)
from personalclaw.learning.staging import (
    FlushOutcome,
    StagingStore,
    input_hash,
)
from personalclaw.learning.surfacing import (
    Allocation,
)
from personalclaw.learning.surfacing import Candidate as SurfacingCandidate
from personalclaw.learning.surfacing import (
    Tier,
    allocate,
    classify_intent,
)
from personalclaw.learning.usage import UsageRecord, UsageStore, promotion_ready

__all__ = [
    "Allocation",
    "Cadence",
    "Candidate",
    "ChangeManifest",
    "CuratorReport",
    "DecayVerdict",
    "FlushOutcome",
    "GateDecision",
    "GateReason",
    "HygieneVerdict",
    "Kind",
    "LearningGate",
    "MutationLog",
    "MIN_EVIDENCE_DEFAULT",
    "Proposal",
    "StagingStore",
    "Status",
    "SurfacingCandidate",
    "Tier",
    "Verdict",
    "UsageRecord",
    "UsageStore",
    "allocate",
    "classify_intent",
    "content_fingerprint",
    "evaluate_decay",
    "input_hash",
    "is_system_injected",
    "promotion_ready",
    "run_aging",
    "scrub",
    "session_score",
    "strength",
]
