"""Resolving the workflow config knobs the modules actually use (TASKS-SOPS §8 — S61k).

The four fields §8 names are wired through all four config points, and that is exactly where this
program has repeatedly shipped a control that is present and inert: `materialize`, `confirmation`
and `pool` each carry their own module constant, so a field could be set, persisted, echoed back by
`to_dict` and displayed in Settings while the runtime went on using 20 / 7 days / 900s.

So every knob is read HERE, once, with the module constant as the fallback — and each getter is what
the call sites use. A caller that reaches for the constant directly is the drift this module exists
to prevent, and `test_workflows_settings.py` asserts the call sites go through these.

Config reads are best-effort: a malformed `config.json` must not stop a run from materializing its
tasks. Each getter degrades to the constant, which is the value that shipped and is known good.
"""

from __future__ import annotations


def _workflows_config() -> object | None:
    """The live `WorkflowsConfig`, or None when it cannot be read.

    Loaded per call rather than cached: these are runtime-editable through the PATCH allowlist, and
    a cached value would keep applying the old number until the gateway restarted — which is the
    whole reason they are in the live-editable set rather than the restart-required one.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return AppConfig.load().workflows
    except Exception:
        return None


def surface_mode_default() -> str:
    """What a newly authored def's `surface_mode` should be.

    `off` on failure, matching both the field default and `DefMetadata`'s coercion: the direction
    that surfaces nothing is the one a config error must not override.
    """
    cfg = _workflows_config()
    value = str(getattr(cfg, "surface_mode_default", "off") or "off")
    return value if value in {"off", "passive", "suggest"} else "off"


def fanout_task_cap() -> int:
    """The most Tasks one foreach node may materialize."""
    from personalclaw.workflows.materialize import FANOUT_TASK_CAP

    cfg = _workflows_config()
    raw = getattr(cfg, "max_materialized_per_foreach", None)
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return FANOUT_TASK_CAP
    # A cap of 0 or below would materialize nothing, silently turning off the board for every run.
    # Clamped rather than honoured: an owner who wants no task rows sets `materialize_task: false`
    # on the node, which says so.
    return value if value >= 1 else FANOUT_TASK_CAP


def confirmation_ttl_secs() -> int:
    """How long a pending confirmation stays live. 0 means never expires."""
    from personalclaw.workflows.confirmation import DEFAULT_TTL_SECS

    cfg = _workflows_config()
    raw = getattr(cfg, "confirmation_ttl_secs", None)
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECS
    # Negative is not "no expiry" — `expires_at` already reads `<= 0` that way, and letting a
    # negative through would make the two spellings of the same intent look like different values in
    # the stored config.
    return max(0, value)


def lease_ttl_secs() -> int:
    """How long a task lease lasts, clamped to the ceiling the lease record enforces."""
    from personalclaw.workflows.pool import DEFAULT_LEASE_SECS, MAX_LEASE_SECS

    cfg = _workflows_config()
    raw = getattr(cfg, "lease_ttl_secs", None)
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_LEASE_SECS
    if value < 1:
        return DEFAULT_LEASE_SECS
    # Clamped here as well as in `Lease.expires_at`. Both, deliberately: the record's clamp is the
    # invariant, and this one keeps the number a caller SEES equal to the number that will apply —
    # a getter returning 86400 while the lease expires in 3600 is a lie a debugger would chase.
    return min(value, MAX_LEASE_SECS)
