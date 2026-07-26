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

Later sessions add confirm-gated auto-fixes, the trust/debug simulators, the
platform-wide no-model degraded contract, mid-turn message handling, and the
health-scored self-remediation engine (see
``docs/roadmap/plans/PLATFORM-RESILIENCE.md``).
"""

from personalclaw.resilience.doctor import (
    Probe,
    ProbeResult,
    Tier,
    all_probes,
    register_probe,
    run_capability,
    run_doctor,
)

__all__ = [
    "Probe",
    "ProbeResult",
    "Tier",
    "all_probes",
    "register_probe",
    "run_capability",
    "run_doctor",
]
