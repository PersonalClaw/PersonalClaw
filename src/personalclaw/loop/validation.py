"""Deterministic pre-flight validation for a unified loop-creation payload.

The composer runs this before launch to show estimated cycles/duration and block an
unstartable config. The SHARED spine checks (task length, cycle budget, workspace
path safety, agent existence) live here; each kind contributes its own checks via an
optional ``validate_config(body) -> (errors, warnings)`` strategy method (goal type/
granularity + verify-command screening; code entry-stage + brownfield workspace). The
union folds the legacy loops + code validators onto the one entity. A native worker agent's
existence is the HTTP layer's to say (it passes ``agent_exists``); the agent CLI a loop runs on
is checked here (:func:`runtime_errors`), against the runtimes registered when it is asked.
"""

from __future__ import annotations

import math
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


def run_dollar_cap(config: dict) -> float | None:
    """A run-backed loop's dollar limit (``max_cost_usd``) as its run's dollar budget: ``0.0``
    when it sets none, ``None`` when it is not a number of 0 or more. A numeric string reads as
    its number, as :func:`_as_int` reads one."""
    raw = config.get("max_cost_usd")
    if raw is None or raw == "":
        return 0.0
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw.strip() if isinstance(raw, str) else raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


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


def workspace_write_target_errors(workspace_dir: str) -> list[str]:
    """Path-safety for a bound workspace PClaw will WRITE generated files into.

    One case stricter than :func:`workspace_dir_errors`: it also refuses the user's HOME
    directory itself. The base helper already rejects a relative path, a credential dir
    (``~/.ssh``, ``~/.aws`` …) and an OS/system root (``/``, ``/etc`` …) — but the bare home
    dir is none of those, yet dropping ``CLAUDE.md`` / ``AGENTS.md`` / ``.cursorrules``
    straight into ``$HOME`` is exactly the accident #358 guards against. Composes the base
    helper with ``require_exists=False`` (a project may bind a dir created before the first
    write), then adds the home check on the realpath'd value so a ``..``/symlink form cannot
    slip past. Returns ``[]`` for an empty binding — clearing a workspace is legal. Shared by
    the bind-time guard (``HierarchyStore``) and the regenerate-time guard so the two surfaces
    can never drift on what counts as an unsafe write root.
    """
    workspace_dir = (workspace_dir or "").strip()
    if not workspace_dir:
        return []
    errors = workspace_dir_errors(workspace_dir, require_exists=False)
    user_path = os.path.expanduser(workspace_dir)
    try:
        resolved = os.path.realpath(user_path)
        home = os.path.realpath(os.path.expanduser("~"))
    except (OSError, ValueError):
        resolved, home = user_path, os.path.expanduser("~")
    if resolved == home:
        msg = "Workspace directory cannot be your home directory itself."
        if msg not in errors:
            errors.append(msg)
    return errors


def numeric_and_boolean_field_errors(config: dict, *, present_only: bool = False) -> list[str]:
    """The numeric/boolean spec screens shared by the create gate and the PUT
    spec edit. ``present_only`` (the edit) checks only fields the patch carries;
    the create gate re-runs its own richer max_cycles block in :func:`validate`
    and uses this for the boolean floor."""
    cfg = AppConfig.load().loops
    errors: list[str] = []
    if "max_cycles" in config or not present_only:
        raw = config.get("max_cycles", 0)
        if raw not in (None, ""):
            n = _as_int(raw)
            if n is None:
                errors.append("max_cycles must be a whole number.")
            elif n < 0:
                errors.append("Max cycles cannot be negative (0 means uncapped).")
            elif n > cfg.max_cycles_hard_cap:
                errors.append(
                    f"Max cycles cannot exceed the hard cap of {cfg.max_cycles_hard_cap}."
                )
    if "idle_secs" in config and config.get("idle_secs") not in (None, ""):
        idle = _as_int(config.get("idle_secs"))
        if idle is None or idle < 0:
            errors.append("idle_secs must be a non-negative whole number.")
    for f in ("attended", "autopilot", "auto_teardown_on_complete"):
        if f in config and not isinstance(config.get(f), bool):
            errors.append(
                f"'{f}' must be a boolean (true/false) — a quoted string like "
                f'"false" would silently read as true.'
            )
    return errors


def _runtime_entry(provider: str):
    """The registered agent-runtime entry named *provider*, or ``None`` when none is set up."""
    from personalclaw.llm.acp_agent import ACP_AGENT_CAPABILITY
    from personalclaw.llm.registry import get_default_registry

    try:
        entry = get_default_registry().get_entry(provider)
    except Exception:
        return None
    return entry if entry.type == ACP_AGENT_CAPABILITY.type else None


def runtime_errors(config: dict, *, kind: str) -> list[str]:
    """What is wrong with the agent CLI *config* asks its loop to run on, or ``[]``.

    ``provider`` empty runs the loop on PersonalClaw, its kind's own worker. Otherwise it names
    an agent CLI (``acp:<cli>``) its app registered here, and ``provider_agent`` the agent that CLI
    offered (empty for one that offers a single agent). A name that is not set up here is refused
    now, not stored to fail on the worker's first turn — and a ``provider`` that is not an
    ``acp:`` runtime at all is refused too, because the worker would run on PersonalClaw while the
    loop said it runs elsewhere. A kind whose loop runs as a workflow has no worker session to
    put on a CLI, so the choice is refused rather than dropped.
    """
    from personalclaw.validation import _AGENT_NAME_RE

    provider = str(config.get("provider") or "").strip()
    agent = str(config.get("provider_agent") or "").strip()
    if not provider:
        if agent:
            return [f"{agent!r} names an agent CLI's agent, but no agent CLI to run it on."]
        return []
    if not provider.startswith("acp:"):
        return [
            f"{provider!r} isn't an agent runtime: a loop runs on PersonalClaw, or on an agent "
            "CLI set up under Settings → Providers."
        ]
    entry = _runtime_entry(provider)
    if entry is None:
        return [
            f"The agent CLI {provider!r} isn't set up here: install or enable its agent app, "
            "or run the loop on PersonalClaw."
        ]
    from personalclaw.agents.runtime_tests import runtime_label

    label = runtime_label(entry)
    if agent and not _AGENT_NAME_RE.match(agent):
        return [f"{agent!r} isn't an agent name: choose one of the agents {label} offers."]
    from personalclaw.workflows.service import PORTED_LOOP_KINDS

    if kind in PORTED_LOOP_KINDS:
        return [
            f"A {kind} loop runs as a workflow, which can't be put on one agent CLI, so it "
            f"can't run on {label}. Choose PersonalClaw to start it."
        ]
    return []


def runtime_blocker(loop) -> str | None:
    """Why *loop* can't start on the agent CLI it runs on, or ``None``.

    A loop on PersonalClaw has nothing to wait for. One on an agent CLI starts only when that CLI
    is ready as the user's last Test of it found (``agents/runtime_tests.readiness``, which starts
    nothing): starting it otherwise would arm workers whose every turn fails, unattended."""
    provider = str(getattr(loop, "provider", "") or "")
    if not provider:
        return None
    wrong = runtime_errors(
        {"provider": provider, "provider_agent": getattr(loop, "provider_agent", "")},
        kind=str(getattr(loop, "kind", "") or ""),
    )
    if wrong:
        return wrong[0]
    from personalclaw.agents import runtime_tests

    entry = _runtime_entry(provider)
    readiness = runtime_tests.readiness(entry)
    if readiness.get("ready"):
        return None
    label = runtime_tests.runtime_label(entry)
    why = str(readiness.get("detail") or readiness.get("state") or "not ready").strip().rstrip(".")
    return (
        f"This loop runs on {label}, which isn't ready: {why}. Get it ready, or choose "
        "another runtime for the loop, then start it."
    )


def _field(loop: object, name: str) -> str:
    """A create body's or a stored loop's *name* field, as text."""
    raw = loop.get(name) if isinstance(loop, dict) else getattr(loop, name, "")
    return str(raw or "").strip()


#: The fields whose change before launch moves the model a loop's worker runs on.
MODEL_FIELDS = frozenset({"agent", "model", "provider", "provider_agent"})


async def tools_blocker(body: dict) -> str | None:
    """Why the loop a create *body* describes can't do its work, or ``None``: the model it would
    work on can't use tools.

    Refused for EVERY kind, because no kind can make progress without tools, and a loop on a model
    that can't use them would only say it had done its work:

    * a loops-table kind's worker records each cycle by writing its finding into the loop's folder,
      and the supervisor credits only what is written there, never the worker's word; its document,
      its code and its design are files too, and its planner writes the plan the same way
      (:func:`worker_tools_blocker`);
    * a General loop runs as a workflow whose work step cites evidence its judge re-runs with tools,
      and the judge sees only that tool output (``workflows.preflight.tool_less_steps``).

    A loop on an agent CLI is never refused here: the CLI brings its own tools. Asked where a loop
    is created, planned and started, and where a change before launch moves its model, so none of
    it runs first. A worker whose model stops using tools after it started is held instead
    (``LoopWatchdog.hold_without_tools``).
    """
    from personalclaw.llm.tool_use import cannot_use_tools
    from personalclaw.workflows.service import PORTED_LOOP_KINDS, kind_tool_less_steps

    kind = _field(body, "kind").lower() or "goal"
    if kind not in PORTED_LOOP_KINDS:
        return await worker_tools_blocker(body)
    steps = await kind_tool_less_steps(kind)
    if not steps:
        return None
    return (
        f"This loop can't do its work: {cannot_use_tools(steps[0][1])}, and each of its steps "
        "works with them (its judge re-runs what a step cites). Choose a model that uses tools "
        "for Orchestration in Settings → Models."
    )


async def worker_tools_blocker(loop: object, *, planner: str = "") -> str | None:
    """Why the loops-table loop *loop* (a stored loop, or the body of one) can't do its work, or
    ``None``: the model its worker would run on, on the Loops chain, can't use tools. With
    *planner* (its planner agent's name) the model asked about is its planner's. See
    :func:`tools_blocker` for why every kind is refused."""
    from personalclaw.llm.tool_use import cannot_use_tools
    from personalclaw.loop import kinds
    from personalclaw.providers.provider_bridge import model_without_tools

    kinds.ensure_loaded()
    strat = kinds.get_or_none(_field(loop, "kind").lower() or "goal")
    agent = planner or _field(loop, "agent") or str(getattr(strat, "default_agent", "") or "")
    model = _field(loop, "model")
    found = await model_without_tools(
        axis="loops", agent=agent, model=model, runtime=_field(loop, "provider")
    )
    if not found:
        return None
    fix = _choose_instead(loop, agent)
    if planner:
        return (
            f"This loop can't be planned: {cannot_use_tools(found)}, and its planner writes the "
            f"plan with them. {fix}"
        )
    return (
        f"This loop can't do its work: {cannot_use_tools(found)}, and its worker does everything "
        f"with them (a cycle counts only for the finding it writes). {fix}"
    )


def _choose_instead(loop: object, agent: str) -> str:
    """What to choose in place of a model that can't use tools, where the loop's model came from:
    its own, else its agent's pin, else the Loops chain in Settings → Models."""
    if _field(loop, "model"):
        return "Choose a model that uses tools for this loop."
    if str(getattr((AppConfig.load().agents or {}).get(agent), "model", "") or ""):
        return f"Pick a model that uses tools for {agent} on the Agents page."
    return "Choose a model that uses tools for Loops in Settings → Models."


#: What a loop held for a model without tools does next, beside the sentence that says why.
NO_TOOLS_WHY = "Nothing runs until you resume it, and it resumes only on a model that uses tools."


def ran_without_tools(loop: object, model: str) -> str:
    """The question a running loop waits on once a turn of its worker ran on *model* without
    tools (``LoopWatchdog.hold_without_tools``): the cycle could write no finding, and no next one
    could either, so it is not asked again."""
    from personalclaw.llm.tool_use import cannot_use_tools
    from personalclaw.loop import kinds

    kinds.ensure_loaded()
    strat = kinds.get_or_none(_field(loop, "kind").lower() or "goal")
    agent = _field(loop, "agent") or str(getattr(strat, "default_agent", "") or "")
    return (
        f"Its worker ran this cycle without tools: {cannot_use_tools(model)}, and a cycle counts "
        f"only for the finding it writes. {_choose_instead(loop, agent)} Then resume the loop."
    )


def spec_edit_errors(
    body: dict,
    *,
    kind: str,
    existing_kind_config: dict | None = None,
    existing_runtime: dict | None = None,
) -> list[str]:
    """Security-relevant checks for a PUT spec edit — mirrors the create gate so an
    edit can't smuggle in what create rejects (a sensitive/relative workspace_dir, or
    a destructive verify/test command). Only fields present in ``body`` are checked.
    A flat ``verify_command``/``test_command`` or a whole ``kind_config`` patch both
    route through the kind's ``validate_config`` (errors only — warnings don't block
    an edit). ``existing_kind_config`` lets the kind see the merged config, and
    ``existing_runtime`` (the stored ``provider``/``provider_agent``) the runtime an edit of
    either one leaves the loop on."""
    from personalclaw.loop import kinds

    kinds.ensure_loaded()
    errors: list[str] = []
    # Mirror the create gate's numeric + boolean screens for whatever the patch
    # carries. Without this, PUT bypassed every guard POST enforces: a negative
    # max_cycles reads as "uncapped" downstream, and update_spec's int(bool(...))
    # coercion turns autopilot:"false" (a truthy string) into autopilot ON — the
    # consequential direction, same reasoning as api_loop_autopilot's explicit-
    # boolean requirement.
    errors.extend(numeric_and_boolean_field_errors(body, present_only=True))
    if "workspace_dir" in body:
        # Path-safety is hard; existence is deferred (the dir may be created before launch).
        errors.extend(
            workspace_dir_errors(str(body.get("workspace_dir") or ""), require_exists=False)
        )
    if "provider" in body or "provider_agent" in body:
        runtime = {**(existing_runtime or {}), **body}
        errors.extend(runtime_errors(runtime, kind=kind))
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

                logging.getLogger(__name__).debug(
                    "kind %s validate_config (edit) errored", kind, exc_info=True
                )
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
        errors.append(
            f"Task is too vague — describe it in more detail (min {_MIN_TASK_LEN} characters)."
        )
    elif len(task) > _MAX_TASK_LEN:
        errors.append(
            f"Task is too large ({len(task):,} characters) — trim it to under "
            f"{_MAX_TASK_LEN:,} (link or summarize a big document instead of pasting it whole)."
        )

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
        warnings.append(
            f"High cycle count ({max_cycles}). Estimated cost: ~${low:.2f}–${high:.2f}."
        )

    # Idle timeout (the per-cycle nudge cadence) — same numeric guard; a non-numeric
    # value is a clean error, not a launch-time crash.
    if (
        "idle_secs" in config
        and config.get("idle_secs") not in (None, "")
        and _as_int(config.get("idle_secs")) is None
    ):
        errors.append("Idle timeout must be a whole number of seconds.")

    # Boolean floor: attended/autopilot/auto_teardown_on_complete must be real
    # booleans. The loop builder coerces with bool(), so a quoted "false" would
    # silently enable the field — reject it here instead.
    errors.extend(
        e
        for e in numeric_and_boolean_field_errors(config, present_only=True)
        if "must be a boolean" in e
    )

    # Path-safety is a hard error; existence is deferred to a warning — the loop is
    # created as a draft and the launch action re-validates the dir (launch_blocker).
    ws = str(config.get("workspace_dir") or "")
    errors.extend(workspace_dir_errors(ws, require_exists=False))
    if (
        ws.strip()
        and not workspace_dir_errors(ws, require_exists=False)
        and workspace_dir_errors(ws, require_exists=True)
    ):
        warnings.append(
            "Workspace directory does not exist yet — create or pick it before launching."
        )

    if not agent_exists:
        errors.append("Selected worker agent does not exist.")

    # Kind-specific checks (goal type/granularity + verify-command screening; code
    # entry-stage + brownfield workspace requirement) — the kind owns them.
    kind = str(config.get("kind", "goal")).strip().lower() or "goal"
    errors.extend(runtime_errors(config, kind=kind))
    strat = kinds.get_or_none(kind)
    hook = getattr(strat, "validate_config", None) if strat else None
    if hook is not None:
        try:
            k_errors, k_warnings = hook(config)
            errors.extend(k_errors)
            warnings.extend(k_warnings)
        except Exception:
            import logging

            logging.getLogger(__name__).debug(
                "kind %s validate_config errored", kind, exc_info=True
            )

    # A run-backed kind works in its project's context dir or the default workspace — it
    # has no scratch dir of its own — so "Scratch (auto-clean when done)" has nothing it could
    # reclaim. REFUSED rather than dropped: the create path used to discard the flag without a
    # word, which told the user their workspace would be cleaned up and then did nothing.
    # Lazy: the workflows service is heavy, and this module is imported by every loop surface.
    from personalclaw.workflows.service import PORTED_LOOP_KINDS

    if kind in PORTED_LOOP_KINDS and config.get("auto_teardown_on_complete") is True:
        errors.append(
            f"Scratch cleanup isn't available for a {kind} loop: it works in your project's "
            "folder (or the default workspace), so there is no scratch folder to reclaim. "
            "Untick Scratch to start it."
        )
    # Its dollar limit is the run's dollar budget, which pauses it when its steps have spent it
    # (`workflows.run_budget`), so it is read as one: a number of 0 or more. A time limit has no
    # home on a run, which is held to tokens and dollars only: refused for the reason Scratch is,
    # rather than dropped.
    if kind in PORTED_LOOP_KINDS:
        if run_dollar_cap(config) is None:
            errors.append("The dollar limit must be a number of 0 or more (0 is no limit).")
        deadline = config.get("deadline_secs")
        if deadline not in (None, "", 0) and deadline != "0":
            errors.append(
                f"A time limit isn't available for a {kind} loop: it runs as a workflow run, "
                "which is held to a token and a dollar budget but not to a time. Clear the time "
                "limit to start it."
            )

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
