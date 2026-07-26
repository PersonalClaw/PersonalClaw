"""Platform resilience — diagnosis and degradation substrate (PLATFORM-RESILIENCE).

PersonalClaw is a constellation of degradable subsystems (gateway, app-backend
subprocesses, channel transports, local-model providers, memory/knowledge stores,
the FE static-dist symlink) whose failure history is dominated by *silent*
degradation. This package makes that visible.

Session 1 (this slice) ships the Doctor core:

* :mod:`personalclaw.resilience.doctor` — a tiered read-only probe framework
  (process → socket → cheap-RPC → per-capability), a flat probe registry, and
  ``run_doctor()`` with downward short-circuiting and the "capability-degraded is
  never core failure" doctrine. Probes are read-only by contract: an exception
  becomes an ``ok=False`` result, never a 500.

Session 2 adds the platform-wide no-model degraded contract:

* :mod:`personalclaw.resilience.degraded` — a ``DegradedContract`` registry (one per
  model-dependent surface, declaring its LLM-free floor + a read-only backlog probe),
  ``evaluate()`` deriving each surface's availability from ``can_resolve_use_case``,
  and down/recovery transition notifications.

Later sessions add confirm-gated auto-fixes, the trust/debug simulators, mid-turn
message handling, and the health-scored self-remediation engine (see
``docs/roadmap/plans/PLATFORM-RESILIENCE.md``).
"""

from personalclaw.resilience.active_jobs import (
    ActiveJob,
    ActiveJobTracker,
    classify_origin,
    get_tracker,
    is_cancellable_origin,
    reset_tracker,
)
from personalclaw.resilience.crashes import (
    crash_count,
    read_crash,
    recent_crashes,
    record_crash,
)
from personalclaw.resilience.degraded import (
    DegradedContract,
    all_contracts,
    degraded_surfaces,
    evaluate,
    get_contract,
    register_contract,
)
from personalclaw.resilience.doctor import (
    Probe,
    ProbeResult,
    Tier,
    all_probes,
    register_probe,
    run_capability,
    run_doctor,
)
from personalclaw.resilience.fixes import Fix, all_fixes, apply_fix, get_fix, register_fix
from personalclaw.resilience.remediation import (
    Deficit,
    RemediationJob,
    RunResult,
    all_jobs,
    health_score,
    measure_deficits,
    recent_runs,
    run_remediation,
)

__all__ = [
    "Probe",
    "ProbeResult",
    "Tier",
    "all_probes",
    "register_probe",
    "run_capability",
    "run_doctor",
    "DegradedContract",
    "all_contracts",
    "degraded_surfaces",
    "evaluate",
    "get_contract",
    "register_contract",
    "ActiveJob",
    "ActiveJobTracker",
    "classify_origin",
    "get_tracker",
    "is_cancellable_origin",
    "reset_tracker",
    "record_crash",
    "recent_crashes",
    "read_crash",
    "crash_count",
    "Fix",
    "all_fixes",
    "apply_fix",
    "get_fix",
    "register_fix",
    "Deficit",
    "RemediationJob",
    "RunResult",
    "all_jobs",
    "health_score",
    "measure_deficits",
    "recent_runs",
    "run_remediation",
]
