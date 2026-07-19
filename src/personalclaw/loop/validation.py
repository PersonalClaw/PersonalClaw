"""Deterministic pre-flight validation for a unified loop-creation payload.

The composer runs this before launch to show estimated cycles/duration and block an
unstartable config. The SHARED spine checks (task length, cycle budget, workspace
path safety, agent existence) live here; each kind contributes its own checks via an
optional ``validate_config(body) -> (errors, warnings)`` strategy method (goal type/
granularity + verify-command screening; code entry-stage + brownfield workspace). The
union folds the legacy loops + code validators onto the one entity. Free of the agent
registry — the HTTP layer passes ``agent_exists`` — so it stays import-light + testable.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from personalclaw.config.loader import AppConfig
from personalclaw.security import is_sensitive_path, is_system_path

_MIN_TASK_LEN = 12
# Upper bound on the task text. A real BRD/TRD/design doc fits comfortably under this;
# the cap guards against a pathological paste (a whole repo, a binary, MBs of text)
# that would blow up the classifier prompt + bloat storage. The composer mirrors this
# client-side, but a client check is bypassable (chat SDLC tools / direct API), so the
# server enforces it too. Ported from the legacy code validator's _MAX_TASK_LEN, which
# the unified-validator cutover dropped (FE still referenced "the server cap").
_MAX_TASK_LEN = 100_000


def _as_int(value) -> int | None:
    """Coerce a JSON value to int, or None if it isn't a whole number. Tolerates a
    clean integer string (JSON clients sometimes send numbers as strings) but rejects
    a non-numeric one — so a malformed value surfaces as a clean validation error
    rather than an unhandled int() ValueError → 500. Ported from the legacy code
    validator, dropped at the unified-validator cutover (which used a raw int())."""
    if isinstance(value, bool):  # bool is an int subclass — treat True/False as invalid
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None
    return None


@dataclass
class ValidationResult:
    can_start: bool
    errors: list[str]
    warnings: list[str]
    estimated_cycles: int = 0
    estimated_duration_min: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def workspace_dir_errors(workspace_dir: str, *, require_exists: bool = True) -> list[str]:
    """Path-safety for a bound workspace — must be an absolute, non-sensitive dir.
    Existence is a HARD error only when ``require_exists`` (launch / PUT-bind); at
    CREATE the dir may not exist yet (a draft picks/creates it before launch), so the
    caller defers it to a warning. Shared by every workspace-binding entry point."""
    workspace_dir = (workspace_dir or "").strip()
    if not workspace_dir:
        return []
    # Test absoluteness on the ~-expanded input, NOT the realpath'd one: realpath
    # resolves a relative path against the gateway's cwd and ALWAYS returns an absolute
    # path, so isabs(realpath(...)) is dead and a relative input would silently bind to
    # wherever the server runs. A bound workspace must be exactly named.
    user_path = os.path.expanduser(workspace_dir)
    expanded = os.path.realpath(user_path)
    if not os.path.isabs(user_path):
        return ["Workspace directory must be an absolute path."]
    # The workspace is the cwd for an UNSANDBOXED worker that reads/writes/runs commands,
    # so a home credential dir (is_sensitive_path) OR an OS/system root (is_system_path,
    # /, /etc, /usr, /var, /System…) must be rejected — is_sensitive_path alone covers
    # only the former.
    if is_sensitive_path(expanded) or is_system_path(expanded):
        return ["Workspace directory points to a system or sensitive location."]
    if os.path.exists(expanded) and not os.path.isdir(expanded):
        return ["Workspace directory path is a file, not a directory."]
    if require_exists and not os.path.isdir(expanded):
        return ["Workspace directory does not exist."]
    return []


def spec_edit_errors(body: dict, *, kind: str, existing_kind_config: dict | None = None) -> list[str]:
    """Security-relevant checks for a PUT spec edit — mirrors the create gate so an
    edit can't smuggle in what create rejects (a sensitive/relative workspace_dir, or
    a destructive verify/test command). Only fields present in ``body`` are checked.
    A flat ``verify_command``/``test_command`` or a whole ``kind_config`` patch both
    route through the kind's ``validate_config`` (errors only — warnings don't block
    an edit). ``existing_kind_config`` lets the kind see the merged config."""
    from personalclaw.loop import kinds
    kinds.ensure_loaded()
    errors: list[str] = []
    if "workspace_dir" in body:
        # Path-safety is hard; existence is deferred (the dir may be created before launch).
        errors.extend(workspace_dir_errors(str(body.get("workspace_dir") or ""), require_exists=False))
    # Screen commands via the kind. Build a config view: the patch's kind_config (or
    # flat command fields) merged over the existing config so a partial patch is judged
    # in context. Only command/stage validity errors block; warnings are advisory.
    touches_cfg = "kind_config" in body or "verify_command" in body or "test_command" in body
    if touches_cfg:
        merged = dict(existing_kind_config or {})
        if isinstance(body.get("kind_config"), dict):
            merged.update(body["kind_config"])
        for f in ("verify_command", "test_command"):
            if f in body:
                merged[f] = body[f]
        strat = kinds.get_or_none(kind)
        hook = getattr(strat, "validate_config", None) if strat else None
        if hook is not None:
            try:
                k_errors, _warnings = hook({"kind_config": merged})
                errors.extend(k_errors)
            except Exception:
                import logging
                logging.getLogger(__name__).debug("kind %s validate_config (edit) errored", kind, exc_info=True)
    return errors


def validate(config: dict, *, agent_exists: bool = True) -> ValidationResult:
    """Deterministic pre-flight on a unified loop-creation payload. ``agent_exists``
    is supplied by the HTTP layer (validation stays free of the agent registry)."""
    from personalclaw.loop import kinds
    kinds.ensure_loaded()
    cfg = AppConfig.load().loops
    errors: list[str] = []
    warnings: list[str] = []

    task = str(config.get("task") or config.get("goal") or "").strip()
    if len(task) < _MIN_TASK_LEN:
        errors.append(f"Task is too vague — describe it in more detail (min {_MIN_TASK_LEN} characters).")
    elif len(task) > _MAX_TASK_LEN:
        errors.append(
            f"Task is too large ({len(task):,} characters) — trim it to under "
            f"{_MAX_TASK_LEN:,} (link or summarize a big document instead of pasting it whole).")

    # Cycle budget — the safety cap. 0 == ongoing/forever (relies on a DoD/stop/
    # stagnation to ever finish). Negative is invalid; over the hard cap is rejected.
    # A non-numeric value (client bug / direct API caller) must surface as a clean
    # error, never an unhandled int() ValueError → 500.
    max_cycles_raw = config.get("max_cycles", 0)
    max_cycles = _as_int(max_cycles_raw) if max_cycles_raw not in (None, "") else 0
    if max_cycles is None:
        errors.append("Cycle budget must be a whole number.")
        max_cycles = 0  # keep the estimate math below safe
    elif max_cycles < 0:
        errors.append("Cycle budget cannot be negative (use 0 for an ongoing loop).")
    elif max_cycles > cfg.max_cycles_hard_cap:
        errors.append(f"Max cycles cannot exceed the hard cap of {cfg.max_cycles_hard_cap}.")
    elif max_cycles > 50:
        low, high = max_cycles * 0.10, max_cycles * 0.30
        warnings.append(f"High cycle count ({max_cycles}). Estimated cost: ~${low:.2f}–${high:.2f}.")

    # Idle timeout (the per-cycle nudge cadence) — same numeric guard; a non-numeric
    # value is a clean error, not a launch-time crash.
    if "idle_secs" in config and config.get("idle_secs") not in (None, "") \
            and _as_int(config.get("idle_secs")) is None:
        errors.append("Idle timeout must be a whole number of seconds.")

    # Path-safety is a hard error; existence is deferred to a warning — the loop is
    # created as a draft and the launch action re-validates the dir (launch_blocker).
    ws = str(config.get("workspace_dir") or "")
    errors.extend(workspace_dir_errors(ws, require_exists=False))
    if ws.strip() and not workspace_dir_errors(ws, require_exists=False) \
            and workspace_dir_errors(ws, require_exists=True):
        warnings.append("Workspace directory does not exist yet — create or pick it before launching.")

    if not agent_exists:
        errors.append("Selected worker agent does not exist.")

    # Kind-specific checks (goal type/granularity + verify-command screening; code
    # entry-stage + brownfield workspace requirement) — the kind owns them.
    kind = str(config.get("kind", "goal")).strip().lower() or "goal"
    strat = kinds.get_or_none(kind)
    hook = getattr(strat, "validate_config", None) if strat else None
    if hook is not None:
        try:
            k_errors, k_warnings = hook(config)
            errors.extend(k_errors)
            warnings.extend(k_warnings)
        except Exception:
            import logging
            logging.getLogger(__name__).debug("kind %s validate_config errored", kind, exc_info=True)

    # An uncapped loop (max_cycles=0) estimates against the hard cap — and the duration
    # must derive from the SAME effective count, never N cycles but 0 minutes.
    effective_cycles = max_cycles or cfg.max_cycles_hard_cap
    return ValidationResult(
        can_start=not errors,
        errors=errors,
        warnings=warnings,
        estimated_cycles=effective_cycles,
        estimated_duration_min=effective_cycles * 2,
    )
