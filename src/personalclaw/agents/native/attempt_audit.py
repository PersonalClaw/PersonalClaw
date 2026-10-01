"""The native loop's own rows in the model-call log (``model_calls.jsonl``).

The loop records an inference attempt itself only when it was exceptional: every failed attempt,
and the outcome of a retry or of a fallback (``NativeAgentRuntime._audit_inference_attempt``), so
the audit trail tells the retry story without a row for every healthy inference. The guard
records the calls themselves. These rows price nothing and add nothing: a failed attempt cost
nothing, and the call a passing one reports is counted where it was made (the guard's own row on
a metered axis, the turn's usage row otherwise).

A row names the provider entry and the model the attempt was sent to, as the guard's rows do, and
is dated by the wall clock, as every other row of the log is. The loop dated them by its latency
clock (``now_ms``, monotonic, which read as a date said 1973) and named the guard's class as the
provider.
"""

from __future__ import annotations

import time

from personalclaw.guardrails.audit import AttemptRecord, now_ms
from personalclaw.guardrails.failure import FailureMode


def inference_attempt(
    mode: FailureMode,
    *,
    served_ref: str,
    model: str,
    runtime_id: int,
    attempt: int,
    started_ms: float,
    passed: bool,
    fallback: bool = False,
) -> AttemptRecord:
    """The row for one attempt of a loop serving *served_ref* (``"<entry>:<model>"``), whose
    definition names *model*; *started_ms* is when it started on the latency clock.

    ``fallback`` marks an attempt on a model the turn fell back to: strategy ``fallback``, and
    ``degraded`` (a fallback ref served it), which is what the guard's own rows say for it.
    """
    # The entry name holds no colon and a model id may (``gpt-oss:20b``): split on the first.
    entry, colon, served = served_ref.partition(":")
    if not colon:
        entry, served = "", entry
    return AttemptRecord(
        audit_id=f"native-{runtime_id:x}-{time.time_ns():x}",
        ts=time.time(),
        use_case="native_loop",
        provider=entry,
        model=served or model,
        attempt=attempt,
        failure_mode=mode.value,
        latency_ms=max(0.0, now_ms() - started_ms),
        passed=passed,
        strategy="fallback" if fallback else ("retry" if attempt > 1 else "direct"),
        degraded=fallback and passed,
        priced=True,
    )
