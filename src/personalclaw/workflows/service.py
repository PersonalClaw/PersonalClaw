"""Workflow operations — the ONE implementation the chat tools and HTTP routes share.

Every workflow operation lives here as a plain function returning a plain dict. The chat
tools (Slice 6a) call it in-process; the REST handlers (Slice 7a) will call the same
functions and serialize the same dicts. That is deliberate: two surfaces over one engine
must not grow two behaviours, and "the tool did X but the API did Y" is the bug class this
prevents by construction.

Three rules the shape follows:

**Never raise across the boundary.** Every function returns `{"ok": bool, ...}` with a
stable `code` on failure. A tool call that raises burns the model's turn on a traceback it
cannot act on; a coded error it can read and correct.

**The supervisor is injected, never imported from a global.** A run needs a controller to
drive it, and that controller must be the one the watchdog knows about — otherwise a
restart adopts the run a second time and two writers race. Callers pass the supervisor;
tests pass a fake.

**Reads never mutate.** `status`/`output`/`observe` construct no controller and start no
run. A read that lazily started something would make polling a side-effecting act.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import mutations, secrets, store
from personalclaw.workflows.models import (
    TERMINAL_RUN_STATUSES,
    InstanceState,
    Node,
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowDef,
    WorkflowRun,
    valid_name,
    walk,
)
from personalclaw.workflows.validator import validate_spec

logger = logging.getLogger(__name__)

#: `workflow_observe` window bounds. Clamped because an unbounded subscribe in a chat turn
#: is a hang: the model waits, the user waits, and nothing says why (WF2-R11).
MIN_OBSERVE_MS = 100
MAX_OBSERVE_MS = 30_000
DEFAULT_OBSERVE_MS = 5_000


def _err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message, **extra}


def _ok(**fields: Any) -> dict[str, Any]:
    return {"ok": True, **fields}


# ── definitions ──────────────────────────────────────────────────────────────


async def list_defs(*, tag: str = "", source: str = "") -> dict[str, Any]:
    """Every definition across every registered provider."""
    out: list[dict[str, Any]] = []
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None:
            continue
        try:
            found, _total = await provider.list_defs(limit=500)
        except Exception:
            # One broken provider must not hide every other pack's templates.
            logger.debug("workflow def provider %s failed to list", provider_name)
            continue
        for item in found:
            d = item if isinstance(item, dict) else getattr(item, "to_dict", lambda: {})()
            if not isinstance(d, dict) or not d.get("name"):
                continue
            if tag and tag not in (d.get("tags") or []):
                continue
            if source and str(d.get("source", "")) != source:
                continue
            out.append(
                {
                    "name": d.get("name"),
                    "description": d.get("description", ""),
                    "source": d.get("source", "user"),
                    "version": d.get("version", 1),
                    "tags": d.get("tags") or [],
                    "provider": provider_name,
                }
            )
    out.sort(key=lambda d: str(d.get("name")))
    return _ok(defs=out, total=len(out))


async def get_def(name: str) -> dict[str, Any]:
    """One definition in full, with secret values stripped to `_has*` flags.

    Stripped on the way OUT, always: a def read is rendered in a UI and echoed into a chat
    turn, and a credential that reaches either is a credential leaked to both (WF2-R14).
    """
    if not name:
        return _err("WF_DEF_NAME_REQUIRED", "a definition name is required")
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None:
            continue
        try:
            found = await provider.get_def(name)
        except Exception:
            logger.debug("workflow def provider %s failed on %s", provider_name, name)
            continue
        if found is None:
            continue
        raw = found if isinstance(found, dict) else getattr(found, "to_dict", lambda: {})()
        return _ok(definition=secrets.strip_secrets(raw), provider=provider_name)
    return _err("WF_DEF_NOT_FOUND", f"no workflow definition named {name!r}")


async def author_def(
    *,
    name: str,
    root: dict[str, Any],
    description: str = "",
    inputs: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    save: bool = True,
    provenance: str = "chat",
    strict: bool = True,
) -> dict[str, Any]:
    """Validate a spec and (optionally) save it.

    `save=False` is a real dry run: it validates and returns the issues WITHOUT writing, so
    an author can iterate before committing anything. Validating only at save time would
    mean every failed attempt leaves a broken def on disk.

    `strict` rejects on WARNINGS too. Authoring is exactly when a warning is cheap to fix,
    and a template that ships with a known smell propagates it to every run.
    """
    if not valid_name(name):
        return _err(
            "WF_DEF_NAME_INVALID",
            f"{name!r} is not a valid name — use lowercase letters, digits and hyphens "
            "(it becomes a directory)",
        )
    spec = {
        "name": name,
        "description": description,
        "root": root or {},
        "inputs": inputs or {},
        "tags": tags or [],
        "provenance": provenance,
    }

    inline = secrets.find_inline_secrets(spec)
    if inline:
        # Refused, never merely warned: once saved, the value is on disk and every later
        # defence is damage control (WF2-R14).
        return _err(
            "WF_DEF_INLINE_SECRET",
            "the spec contains literal credentials — use {{secret:KEY}} instead",
            findings=[f.to_dict() for f in inline],
        )

    result = validate_spec(spec, strict=strict)
    body = {
        "valid": result.ok,
        "issues": [i.to_dict() for i in result.issues],
        "levels": result.levels,
    }
    if not result.ok:
        return _err("WF_DEF_INVALID", "the spec did not validate", **body, repromptable=True)
    if not save:
        return _ok(saved=False, dry_run=True, **body)

    # Dry-run-before-save for AGENT-authored defs (WF2-R12). A human saving a spec they
    # wrote has already reviewed it; an agent's spec gets a preflight first, so a def that
    # cannot possibly run is caught at authoring rather than at every future start. The
    # findings are ATTACHED, not fatal: a template referencing a credential the user has not
    # added yet is a legitimate thing to save.
    dry_run_report: dict[str, Any] | None = None
    if provenance == "chat":
        from personalclaw.workflows.preflight import preflight as run_preflight

        dry_run_report = run_preflight(spec).to_dict()

    writable = [
        p
        for p in (defs_mod.get_provider(n) for n in defs_mod.list_providers())
        if p is not None and not p.readonly
    ]
    if not writable:
        return _err(
            "WF_DEF_NO_WRITABLE_PROVIDER",
            "no writable workflow definition provider is registered",
        )
    try:
        saved = await writable[0].save_def(**spec)
    except Exception as exc:
        return _err("WF_DEF_SAVE_FAILED", f"could not save the definition: {exc}")
    raw = saved if isinstance(saved, dict) else getattr(saved, "to_dict", lambda: {})()
    return _ok(
        saved=True,
        definition=secrets.strip_secrets(raw),
        provenance=provenance,
        preflight=dry_run_report,
        **body,
    )


async def delete_def(name: str) -> dict[str, Any]:
    if not name:
        return _err("WF_DEF_NAME_REQUIRED", "a definition name is required")
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None or provider.readonly:
            continue
        try:
            if await provider.delete_def(name):
                return _ok(deleted=True, name=name, provider=provider_name)
        except Exception as exc:
            return _err("WF_DEF_DELETE_FAILED", f"could not delete {name!r}: {exc}")
    return _err("WF_DEF_NOT_FOUND", f"no writable definition named {name!r}")


# ── runs ─────────────────────────────────────────────────────────────────────


async def start_run(
    *,
    name: str,
    inputs: dict[str, Any] | None = None,
    mode: str = "background",
    supervisor: Any = None,
    origin_kind: OriginKind = OriginKind.CHAT,
    session_key: str = "",
    project_id: str = "",
    idempotency_key: str = "",
    blocking_timeout: float = 0.0,
    skip_preflight: bool = False,
) -> dict[str, Any]:
    """Instantiate a def and start driving it.

    A caller idempotency key returns the EXISTING run rather than minting a second one — a
    retried tool call is a retry, not a new request (WF2-R1).

    Preflight runs first unless explicitly skipped: a missing credential caught here costs
    nothing, and caught at node 7 has already paid for six nodes of model calls.
    """
    from personalclaw.workflows.effects import START_DEDUPE

    if idempotency_key:
        existing = START_DEDUPE.lookup(idempotency_key)
        if existing:
            return _ok(run_id=existing, deduped=True, status=_status_of(existing))

    found = await get_def(name)
    if not found.get("ok"):
        return found
    # The STORED def, not the stripped read: a run needs the real credential bindings.
    definition = await _raw_def(name)
    if definition is None:
        return _err("WF_DEF_NOT_FOUND", f"no workflow definition named {name!r}")

    spec = definition if isinstance(definition, dict) else definition.to_dict()
    missing = _missing_required_inputs(spec, inputs or {})
    if missing:
        # Refused BEFORE tokens are spent — a run that fails three nodes deep on a missing
        # input has already cost money for nothing.
        return _err(
            "WF_RUN_MISSING_INPUTS",
            f"missing required input(s): {', '.join(missing)}",
            missing=missing,
        )

    # Run-start preflight (WF2-R12): credentials, binaries, models and action providers.
    # Blocking here rather than degrading at node 7, which has already paid for six nodes.
    # `skip_preflight` exists for a deliberate override, and says so in the response.
    if not skip_preflight:
        from personalclaw.workflows.preflight import preflight as run_preflight

        checks = run_preflight(spec)
        if not checks.ok:
            return _err(
                "WF_RUN_PREFLIGHT_FAILED",
                "the run cannot start: " + "; ".join(f.message for f in checks.errors),
                preflight=checks.to_dict(),
            )
        if checks.warnings:
            logger.info(
                "workflow %s: starting with %d unverifiable requirement(s)",
                name,
                len(checks.warnings),
            )

    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=name,
            status=RunStatus.DRAFT,
            spec_version=int(spec.get("version", 1) or 1),
            inputs=dict(inputs or {}),
            mode=mode if mode in ("blocking", "background") else "background",
            project_id=project_id,
            origin=RunOrigin(kind=origin_kind, session_key=session_key),
        )
    )
    store.write_spec(run.id, spec)
    if idempotency_key:
        START_DEDUPE.remember(idempotency_key, run.id)

    if supervisor is None:
        return _err(
            "WF_NO_SUPERVISOR",
            "the workflow supervisor is unavailable, so the run was created but not started",
            run_id=run.id,
        )
    try:
        controller = await supervisor.launch(run, spec)
    except Exception as exc:
        return _err("WF_RUN_LAUNCH_FAILED", f"could not start the run: {exc}", run_id=run.id)

    if run.mode == "blocking":
        status = await controller.run_to_completion(timeout=blocking_timeout or 0.0)
        return _ok(run_id=run.id, status=status.value, blocking=True, nodes=_nodes_of(run.id))
    return _ok(run_id=run.id, status=RunStatus.RUNNING.value, blocking=False)


def status(run_id: str) -> dict[str, Any]:
    """Run status plus node-level progress. Pure read — constructs no controller."""
    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    return _ok(
        run_id=run.id,
        workflow=run.workflow_name,
        status=run.status.value,
        spec_version=run.spec_version,
        error=run.error_message,
        attention=run.attention,
        tokens=run.total_tokens,
        elapsed_secs=run.elapsed_seconds,
        nodes=_nodes_of(run_id),
    )


def output(run_id: str, node_id: str) -> dict[str, Any]:
    """One node's stored output.

    Reads the LAST instance for a node id: a `foreach` body produces many, and returning
    the first would silently hand back item 0's answer for the whole fan-out.
    """
    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    spec = store.read_spec(run_id)
    if spec is None:
        return _err("WF_RUN_NO_SPEC", f"run {run_id!r} has no readable spec")
    try:
        root = Node.from_dict(spec.get("root") or {})
    except ValueError as exc:
        return _err("WF_RUN_BAD_SPEC", f"unreadable spec: {exc}")
    paths = [p for p, node in walk(root) if node.id == node_id]
    if not paths:
        return _err("WF_NODE_NOT_FOUND", f"no node {node_id!r} in this run's spec")
    instances = store.read_state(run_id)
    matched = [
        p
        for p in instances
        if any(p == b or p.startswith(f"{b}#") or p.startswith(f"{b}@") for b in paths)
    ]
    if not matched:
        return _err("WF_NODE_NOT_RUN", f"node {node_id!r} has not produced an output yet")
    target = sorted(matched)[-1]
    return _ok(
        run_id=run_id,
        node_id=node_id,
        instance_path=target,
        state=instances[target].state.value,
        output=store.read_output(run_id, target),
    )


async def observe(run_id: str, duration_ms: int = DEFAULT_OBSERVE_MS) -> dict[str, Any]:
    """Watch a run for a bounded window and return what changed (WF2-R11).

    Cheaper and safer than a status-polling loop in chat: one call, one clamped wait, a
    timestamped delta. The clamp is the point — an unbounded subscribe in a chat turn is a
    hang where the model waits, the user waits, and nothing explains why.

    Returns as soon as the run goes terminal rather than burning the whole window on a run
    that already finished.
    """
    import asyncio

    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    window = max(MIN_OBSERVE_MS, min(int(duration_ms or DEFAULT_OBSERVE_MS), MAX_OBSERVE_MS))
    before = {p: i.state.value for p, i in store.read_state(run_id).items()}
    baseline = len(journal_mod.ledger(run_id))
    deadline = time.monotonic() + (window / 1000.0)

    while time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        current = store.get(run_id)
        if current is not None and current.status in TERMINAL_RUN_STATUSES:
            break

    after = {p: i.state.value for p, i in store.read_state(run_id).items()}
    changed = [
        {"instance_path": p, "from": before.get(p, "pending"), "to": s}
        for p, s in sorted(after.items())
        if before.get(p) != s
    ]
    final = store.get(run_id)
    return _ok(
        run_id=run_id,
        window_ms=window,
        clamped=window != int(duration_ms or DEFAULT_OBSERVE_MS),
        status=final.status.value if final else "unknown",
        changed=changed,
        events=journal_mod.ledger(run_id)[baseline:],
    )


# ── mutation + control ───────────────────────────────────────────────────────


def _live(run_id: str, supervisor: Any) -> Any | None:
    if supervisor is None:
        return None
    getter = getattr(supervisor, "controller", None)
    return getter(run_id) if callable(getter) else None


def edit_run(
    run_id: str,
    ops: list[dict[str, Any]],
    *,
    supervisor: Any = None,
    expect_version: int | None = None,
    confirm_cascade: bool = False,
    actor: str = "chat",
) -> dict[str, Any]:
    """Queue a mutation batch on a live run.

    Requires a LIVE controller: mutation is only safe at the controller's drain point, and
    editing a run nobody is driving would write state with no one to apply it (WF2-R10).
    """
    controller = _live(run_id, supervisor)
    if controller is None:
        return _err(
            "WF_RUN_NOT_LIVE",
            f"run {run_id!r} has no live controller — only a running workflow can be edited",
        )
    body = controller.submit_mutation(
        ops, actor=actor, confirm=confirm_cascade, expect_version=expect_version
    )
    body.setdefault("run_id", run_id)
    return body


def preview_edit(run_id: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
    """The cascade preview WITHOUT queueing anything — a pure what-if.

    Available on a run with no live controller too, so a user can see what an edit would
    cost before deciding to resume the run and apply it.
    """
    spec = store.read_spec(run_id)
    if spec is None:
        return _err("WF_RUN_NO_SPEC", f"run {run_id!r} has no readable spec")
    from personalclaw.workflows.effects import effect_history

    result = mutations.prepare_batch(
        ops, spec, store.read_state(run_id), effects=effect_history(run_id)
    )
    return {**result.to_dict(), "run_id": run_id, "queued": False}


def cancel_run(run_id: str, *, supervisor: Any = None) -> dict[str, Any]:
    """Record a STICKY cancel intent.

    Written to disk rather than applied in memory, so a cancel issued while the gateway is
    down is still honoured on restart. The controller (or the watchdog) writes the terminal
    status — a handler must never do it (WF2-R10).
    """
    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    if run.status in TERMINAL_RUN_STATUSES:
        return _err("WF_RUN_ALREADY_TERMINAL", f"run is already {run.status.value}")
    store.request_cancel(run_id)
    controller = _live(run_id, supervisor)
    if controller is not None:
        controller.request_cancel()
    return _ok(run_id=run_id, cancel_requested=True)


def pause_run(run_id: str, *, supervisor: Any = None) -> dict[str, Any]:
    """Stop launching new nodes; in-flight ones finish.

    A pause is a REQUEST recorded on the run, consumed by the tick loop — the same
    single-writer discipline as cancel.
    """
    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    if run.status in TERMINAL_RUN_STATUSES:
        return _err("WF_RUN_ALREADY_TERMINAL", f"run is already {run.status.value}")
    run.extra["pause_requested"] = True
    store.save(run)
    return _ok(run_id=run_id, pause_requested=True)


def resume_run(
    run_id: str,
    *,
    supervisor: Any = None,
    token: str = "",
    answer: Any = None,
    responder: str = "",
    channel: str = "",
    always_allow: bool = False,
) -> dict[str, Any]:
    """Answer a gate, or clear a pause.

    When no token is given the newest pending continuation is used — a chat user says
    "approve it", not a 32-character token. If several gates are pending, the token becomes
    required rather than guessed: approving the wrong gate is worse than asking.
    """
    from personalclaw.workflows.human_input import list_continuations

    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")

    if answer is None and not token:
        run.extra.pop("pause_requested", None)
        store.save(run)
        return _ok(run_id=run_id, resumed=True, gate_answered=False)

    controller = _live(run_id, supervisor)
    if controller is None:
        return _err(
            "WF_RUN_NOT_LIVE",
            f"run {run_id!r} has no live controller to apply the answer to",
        )
    if not token:
        pending = list_continuations(run_id)
        if not pending:
            return _err("WF_NO_PENDING_GATE", "this run has no gate awaiting an answer")
        if len(pending) > 1:
            return _err(
                "WF_AMBIGUOUS_GATE",
                f"{len(pending)} gates are pending — name one with a resume token",
                pending=[
                    {"node_id": c.node_id, "token": c.token, "prompt": c.ask.get("prompt", "")}
                    for c in pending
                ],
            )
        token = pending[0].token
    result = controller.resume(
        token, answer, responder=responder, channel=channel, always_allow=always_allow
    )
    result.setdefault("run_id", run_id)
    return result


def _reentry(
    run_id: str,
    node_id: str,
    op: str,
    *,
    supervisor: Any = None,
    redo_effects: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    controller = _live(run_id, supervisor)
    if controller is None:
        return _err(
            "WF_RUN_NOT_LIVE",
            f"run {run_id!r} has no live controller — resume the run before {op}",
        )
    return controller.submit_mutation(
        [{"op": op, "node_id": node_id, "redo_effects": redo_effects, "force": force}],
        actor="chat",
        confirm=True,
    )


def rewind_run(run_id: str, node_id: str, **kw: Any) -> dict[str, Any]:
    return _reentry(run_id, node_id, "rewind", **kw)


def run_from(run_id: str, node_id: str, **kw: Any) -> dict[str, Any]:
    return _reentry(run_id, node_id, "run_from", **kw)


def skip_nodes(run_id: str, node_ids: list[str], *, supervisor: Any = None) -> dict[str, Any]:
    controller = _live(run_id, supervisor)
    if controller is None:
        return _err("WF_RUN_NOT_LIVE", f"run {run_id!r} has no live controller")
    return controller.submit_mutation(
        [{"op": "skip", "node_id": n} for n in (node_ids or [])], actor="chat", confirm=True
    )


def fork_run(
    run_id: str, *, checkpoint_id: str = "", note: str = "", supervisor: Any = None
) -> dict[str, Any]:
    """Branch a new run. Works on a terminal run too — forking a finished result to explore
    an alternative is the main reason to fork at all."""
    from personalclaw.workflows.checkpoints import fork_run as do_fork

    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    spec = store.read_spec(run_id)
    if spec is None:
        return _err("WF_RUN_NO_SPEC", f"run {run_id!r} has no readable spec")
    try:
        result = do_fork(
            run,
            spec,
            store.read_state(run_id),
            checkpoint_id=checkpoint_id,
            note=note,
            now=_now(),
        )
    except ValueError as exc:
        return _err("WF_FORK_FAILED", str(exc))
    return _ok(**result.to_dict())


def audit(*, dry_run: bool = True, supervisor: Any = None) -> dict[str, Any]:
    from personalclaw.workflows.audit import audit as do_audit

    return _ok(**do_audit(dry_run=dry_run, supervisor=supervisor).to_dict())


# ── manifest ─────────────────────────────────────────────────────────────────


def manifest() -> dict[str, Any]:
    """The node taxonomy, pipes and op catalog, GENERATED from the real registries.

    Generated, never hand-written: a hand-maintained catalog drifts from the code the
    moment either changes, and an author following a stale catalog writes specs the engine
    rejects. A CI drift test can compare this against the code because both come from the
    same enums.
    """
    from personalclaw.workflows.bindings import PIPES
    from personalclaw.workflows.models import (
        CONTAINER_KINDS,
        GateKind,
        ItemErrorPolicy,
        JoinMode,
        LoopMode,
        NodeKind,
        lane_for,
    )

    return _ok(
        spec_semver=__import__(
            "personalclaw.workflows.models", fromlist=["SPEC_SEMVER"]
        ).SPEC_SEMVER,
        node_kinds=[
            {
                "kind": k.value,
                "container": k in CONTAINER_KINDS,
                "lane": lane_for(k),
            }
            for k in NodeKind
        ],
        gate_kinds=[g.value for g in GateKind],
        join_modes=[j.value for j in JoinMode],
        loop_modes=[m.value for m in LoopMode],
        item_error_policies=[p.value for p in ItemErrorPolicy],
        pipes=sorted(PIPES),
        mutation_ops=[o.value for o in mutations.OpKind],
        instance_states=[s.value for s in InstanceState],
        run_statuses=[s.value for s in RunStatus],
    )


# ── helpers ──────────────────────────────────────────────────────────────────


async def _raw_def(name: str) -> Any | None:
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None:
            continue
        try:
            found = await provider.get_def(name)
        except Exception:
            continue
        if found is not None:
            return found
    return None


def _missing_required_inputs(spec: dict[str, Any], provided: dict[str, Any]) -> list[str]:
    declared = spec.get("inputs") or {}
    if not isinstance(declared, dict):
        return []
    missing: list[str] = []
    for key, meta in declared.items():
        if not isinstance(meta, dict) or not meta.get("required"):
            continue
        if key in provided or meta.get("default") is not None:
            continue
        missing.append(str(key))
    return sorted(missing)


def _nodes_of(run_id: str) -> list[dict[str, Any]]:
    instances = store.read_state(run_id)
    spec = store.read_spec(run_id)
    ids: dict[str, str] = {}
    if spec:
        try:
            for path, node in walk(Node.from_dict(spec.get("root") or {})):
                if node.id:
                    ids[path] = node.id
        except ValueError:
            pass
    out: list[dict[str, Any]] = []
    for path in sorted(instances):
        inst = instances[path]
        base = path.split("#")[0].split("@")[0]
        out.append(
            {
                "instance_path": path,
                "node_id": ids.get(base, ""),
                "state": inst.state.value,
                "attempt": inst.attempt,
                "degraded_reason": inst.degraded_reason,
                "failure": inst.failure.to_dict() if inst.failure else None,
            }
        )
    return out


def _status_of(run_id: str) -> str:
    run = store.get(run_id)
    return run.status.value if run else "unknown"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = [
    "WorkflowDef",
    "audit",
    "author_def",
    "cancel_run",
    "delete_def",
    "edit_run",
    "fork_run",
    "get_def",
    "list_defs",
    "manifest",
    "observe",
    "output",
    "pause_run",
    "preview_edit",
    "resume_run",
    "rewind_run",
    "run_from",
    "skip_nodes",
    "start_run",
    "status",
]
