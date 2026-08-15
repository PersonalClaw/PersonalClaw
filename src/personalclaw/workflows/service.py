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
import re
import shutil
import time
from typing import Any

from personalclaw.workflows import (
    attention,
    blocks,
)
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import (
    judge_calibration,
    macros,
    models,
    mutations,
    provisioning,
    secrets,
    store,
    template_lint,
)
from personalclaw.workflows.models import (
    TERMINAL_RUN_STATUSES,
    TERMINAL_STATES,
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


async def list_defs_surfacing(*, now: float = 0.0) -> dict[str, Any]:
    """The templates list WITH its surfacing state — freshness, scope, packs, route, reachability.

    `list_defs` deliberately returns a thin projection (name/description/source/version/tags/
    provider). Measured (S61b): that projection drops `metadata` entirely, so a templates list built
    on it CANNOT render a freshness gradient, a scope chip, or a surfacing toggle no matter what the
    def declares — the fields would be present on disk and invisible to every surface. This is the
    read the UX consumes.

    Cadence facts are batched here rather than looked up per def: one `list_runs` call per template
    on every list render is the shape that makes a list feel broken on a machine with history.
    """
    from personalclaw.workflows import surfacing_channels as channels

    defs_by_name: dict[str, Any] = {}
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None:
            continue
        try:
            found, _total = await provider.list_defs(limit=500)
        except Exception:
            logger.debug("workflow def provider %s failed to list", provider_name)
            continue
        for item in found:
            name = ""
            metadata: Any = None
            if isinstance(item, dict):
                name = str(item.get("name", "") or "")
                metadata = models.DefMetadata.from_dict(item.get("metadata") or {})
            else:
                name = str(getattr(item, "name", "") or "")
                metadata = getattr(item, "metadata", None)
            if not name or metadata is None:
                continue
            defs_by_name.setdefault(name, (provider_name, metadata))

    rows: list[dict[str, Any]] = []
    doctor_entries: list[dict[str, Any]] = []
    for name, (provider_name, metadata) in sorted(defs_by_name.items()):
        cadence = channels.cadence_from_def(
            name, metadata, last_completed_at=channels.last_completed(name)
        )
        rows.append(
            {
                "name": name,
                "provider": provider_name,
                "surface_mode": metadata.surface_mode,
                "summary": metadata.summary,
                "when_to_use": metadata.when_to_use,
                "cadence_days": metadata.cadence_days,
                "escalation": metadata.escalation,
                "packs": list(metadata.packs),
                "guided": metadata.guided,
                "freshness": channels.freshness(cadence, now).value,
                "overdue": channels.overdue(cadence, now),
                "last_completed_at": cadence.last_completed_at,
                "hands_off_to": [h.to_dict() for h in channels.handoffs_from_def(metadata)],
            }
        )
        doctor_entries.append(channels.doctor_entry(name, metadata))

    # Overdue-first, matching the list the plan describes; `sort_key` owns the rule so the API and
    # any other surface cannot disagree about the order.
    order = {
        name: channels.sort_key(
            channels.cadence_from_def(
                name,
                meta,
                last_completed_at=next(
                    (r["last_completed_at"] for r in rows if r["name"] == name), 0.0
                ),
            ),
            now,
        )
        for name, (_prov, meta) in defs_by_name.items()
    }
    rows.sort(key=lambda r: order.get(str(r["name"]), (9, 0.0, str(r["name"]))))
    return _ok(
        defs=rows,
        total=len(rows),
        findings=[f.to_dict() for f in channels.doctor(doctor_entries)],
    )


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
        return _ok(
            definition=secrets.strip_secrets(raw),
            provider=provider_name,
            default_eligibility=_default_eligibility(name),
        )
    return _err("WF_DEF_NOT_FOUND", f"no workflow definition named {name!r}")


def _default_eligibility(name: str) -> dict[str, Any]:
    """R6a: may this template become its kind's default? (LOOPS-EVOLUTION R6 criterion 1).

    Reads every `judge_verdict` this template's runs recorded to the ledger and asks the
    nodding-loop detector. A gate that has never rejected across enough real runs blocks the
    template from becoming a default and surfaces as a warning badge. Read-only projection over
    the ledger — no model call, no separate store.
    """
    records: list[judge_calibration.VerdictRecord] = []
    runs, _total = store.list_runs(workflow_name=name, limit=200)
    for run in runs:
        run_id = getattr(run, "id", "")
        if not run_id:
            continue
        entries = journal_mod.ledger(run_id, kinds={journal_mod.JUDGE_VERDICT})
        records.extend(judge_calibration.verdicts_from_journal(entries))
    allowed, reason = judge_calibration.may_become_default(records, template=name)
    return {"may_become_default": allowed, "reason": reason, "verdicts": len(records)}


async def author_def(
    *,
    name: str,
    root: dict[str, Any],
    description: str = "",
    inputs: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    save: bool = True,
    provenance: str = "chat",
    strict: bool = True,
    workspace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a spec and (optionally) save it.

    `save=False` is a real dry run: it validates and returns the issues WITHOUT writing, so
    an author can iterate before committing anything. Validating only at save time would
    mean every failed attempt leaves a broken def on disk.

    `strict` rejects on WARNINGS too. Authoring is exactly when a warning is cheap to fix,
    and a template that ships with a known smell propagates it to every run.

    `metadata` is the def's declared surfacing/matching block. Measured (S61b): there was NO write
    path for it — the parameter did not exist, so every `DefMetadata` field (including the
    `surface_mode`, `cadence_days` and `packs` the surfacing channels read) could be loaded from
    disk and never SET through the API. A field with a read path and no write path is a field only a
    hand-edited file can use, which is the config round-trip contract's exact failure.
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
    if isinstance(workspace, dict) and workspace:
        # The §4.1 `workspace:` declaration, carried through authoring so it reaches the persisted
        # def. This dict is an allowlist and the parameter did not exist: an author (including
        # `compile_batch`) could declare a workspace and have it silently dropped here, leaving a
        # run-start applier reading a key nothing ever wrote — the same config-round-trip failure
        # a field with a read path and no write path always is.
        spec[provisioning.WORKSPACE_KEY] = dict(workspace)
    if metadata:
        # Through `DefMetadata.from_dict` and back out, so the tolerant per-field coercion (unknown
        # `surface_mode` → `off`, negative `cadence_days` → 0) applies to the WRITE and not only to
        # the read. Coercing on read alone would store a value the next reader silently
        # reinterprets.
        spec["metadata"] = models.DefMetadata.from_dict(metadata).to_dict()

    # Macros expand HERE, before validation and before the write — so what is stored, what is
    # validated and what the engine runs are the same core nodes. Expanding at run time
    # instead would mean the journal, the resume cache and the rewind cascade all had to know
    # macros exist, and a user could never hand-edit the expansion to graduate from the
    # pattern (their edit would be regenerated over).
    try:
        spec = macros.expand_spec(spec)
        # Blocks AFTER macros, not before: a macro emits block references (the judge panel cites
        # the Finding record), and resolving first would leave those unresolved in the output.
        spec = blocks.resolve_spec(spec)
    except (macros.MacroError, blocks.BlockError) as exc:
        return _err("WF_DEF_MACRO_INVALID", str(exc), repromptable=True)
    # The EXPANDED root is what gets written, so the stored spec, the validated spec and the
    # spec the engine runs are the same tree.
    expanded_root = spec.get("root")
    root = expanded_root if isinstance(expanded_root, dict) else root

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
        # Conventions ADVICE, attached and never fatal (WF2-R15). A user's own half-finished
        # workflow is theirs to leave rough, so the lint informs rather than refuses — but an
        # author who never sees it cannot follow a convention they were not told about. The
        # bundled library is held to lint-clean by test instead.
        "lint": template_lint.lint_template(spec).to_dict(),
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


def _origin_metadata(session_key: str) -> dict[str, Any]:
    """The launching session's durable metadata head, for memory-mode inheritance.

    Read here rather than inside `inherit_mode` because that module is pure-by-design (no I/O, so
    it stays testable without a home). A missing key or an unreadable log yields `{}`, and
    `inherit_mode` then falls back to the process-global registry — the same both-sources order
    `session_search.is_restricted` uses so a restart cannot silently un-mark an in-flight run.
    """
    if not session_key:
        return {}
    try:
        from personalclaw.history import ConversationLog

        return ConversationLog().get_metadata(session_key) or {}
    except Exception:
        logger.debug("origin metadata read failed for %r", session_key, exc_info=True)
        return {}


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
    # Declared defaults are APPLIED, not merely documented. Before this they were validated and
    # then ignored: a template declaring `acceptance` with a default and a run that omitted it
    # failed on `binding failed: unresolved reference at 'acceptance'` — so every optional input
    # was a landmine, and a template could only be run by passing every key it declared. Found by
    # starting a bundled template from the UI with its optional field left blank.
    inputs = _with_declared_defaults(spec, inputs or {})

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

    # Inherit the launching session's memory posture (WORK-CONTAINERS §5.1). A run started from an
    # incognito/temporary chat must carry that mode DOWN into everything it owns, or its stages
    # would quietly write memories the user asked not to record — invisibly, because the chat itself
    # stayed clean. The mode rides in `extra` (already persisted and round-tripped), so a restart
    # replays it and the engine's node-skip + the run-end gate keep enforcing it after the process
    # forgets the in-memory registry. Stamped BEFORE create so the very first tick already sees it.
    from personalclaw.workflows import ownership

    inherited = ownership.inherit_mode(session_key, origin_metadata=_origin_metadata(session_key))
    # NORMAL is left UNSTAMPED: `run_mode` reads an absent key as normal, so stamping it would add a
    # redundant string to every unrestricted run's record for no behavioural gain. Only a restricted
    # inheritance is a fact worth recording.
    run_extra: dict[str, Any] = {}
    if inherited is not ownership.MemoryMode.NORMAL:
        run_extra = ownership.stamp_run_mode({}, inherited)

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
            extra=run_extra,
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
        # `wait_for_terminal`, NOT `run_to_completion`: a blocking caller must also return
        # when the run parks on needs_input, or the tool holds the turn that would render
        # the ask and nothing can ever answer it.
        status = await controller.wait_for_terminal(
            timeout=blocking_timeout or 0.0,
            on_progress=lambda snap: controller._publish("workflow_progress", snap),
        )
        body = _ok(run_id=run.id, status=status.value, blocking=True, nodes=_nodes_of(run.id))
        if status == RunStatus.NEEDS_INPUT:
            # Hand the caller what it needs to answer, in the same response — otherwise the
            # model has to guess that a second call is required and which token to use.
            from personalclaw.workflows.human_input import list_continuations

            pending = list_continuations(run.id)
            body["needs_input"] = [
                {
                    "node_id": c.node_id,
                    "resume_token": c.token,
                    "ask": c.ask,
                    "handoff": c.handoff,
                }
                for c in pending
            ]
        else:
            # Mirror a completion summary back into the launching session (WORK-CONTAINERS §5.1).
            # The blocking tool RESULT is the honest mirror surface: it lands in the launching
            # chat's transcript as a normal message and is persisted by that chat's own full
            # rewrite — a controller-side `ConversationLog.append` into the origin JSONL would be
            # clobbered by `_save_session_to_history`, which rebuilds the file from in-memory
            # messages. Indexability travels WITH the text via `ownership.announcement` rather
            # than being re-derived at the destination: a restricted origin gets the summary
            # WITHOUT it being indexed. (The durable control is belt-and-suspenders — a restricted
            # origin session is already skipped whole by `session_search` on reindex — but deciding
            # here keeps every future mirror surface from re-deriving the rule and getting it
            # wrong.)
            note = ownership.announcement(
                origin_key=run.origin.session_key,
                text=_completion_summary(store.get(run.id) or run, status),
                mode=inherited,
            )
            body["announcement"] = note.to_dict()
        return body
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
        # The containing project, so the run view can offer per-project controls (the R14
        # judge-guidance override writes through the project, which is what reaches this
        # run's worker and judge sessions). Empty for an unscoped run — the view hides the
        # control rather than writing to a project that does not exist.
        project_id=run.project_id,
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


def inspect_node(run_id: str, node_id: str) -> dict[str, Any]:
    """The §5 reconstructability set for one terminal node (WF2-A2).

    Read-only forensics over data the controller already persisted: from this payload alone
    a reader can see what a node *saw* (`resolved_prompt` + `resolved_inputs`), what it
    *produced* (`output`, or an `artifact_ref` when the value was offloaded), how many tries
    it took (`attempts`), the ledger slice that records the trajectory (`ledger_events`),
    and whether the output was served from the resume cache rather than a fresh run
    (`cached`). The acceptance bar §5 states is that prompt → tools → output is
    reconstructable from these events alone; this is the surface that exposes it.

    Never raises across the boundary — like every function here it returns
    `{"ok": bool, ...}`. Three distinct failures, because a caller renders them differently:
    an unknown run/node is a 404 (nothing to show), a node that exists but has not reached a
    terminal state is a 409 (`WF_NODE_NOT_TERMINAL` — retry as the run advances), and neither
    is a server fault.

    SECRETS: this returns the persisted values VERBATIM — the resolved prompt is stored raw
    by the controller (`_store_prompt` writes through `store.write_output`, which does NOT
    redact), so this dict is NOT safe to emit as-is. Redaction is the HTTP surface's job
    (WF2-A2 secrets contract); keeping the read un-redacted mirrors `output()`/`status()`,
    which also hand back stored state verbatim to their one in-process caller.
    """
    from personalclaw.workflows.bindings import node_deps

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

    node_by_path = dict(walk(root))
    id_paths = [p for p, node in node_by_path.items() if node.id == node_id]
    if not id_paths:
        return _err("WF_NODE_NOT_FOUND", f"no node {node_id!r} in this run's spec")

    instances = store.read_state(run_id)
    matched = [
        p
        for p in instances
        if any(p == b or p.startswith(f"{b}#") or p.startswith(f"{b}@") for b in id_paths)
    ]
    if not matched:
        return _err("WF_NODE_NOT_RUN", f"node {node_id!r} has not produced an output yet")
    # The LAST instance for the id — a `foreach` body produces many, and inspecting item 0
    # for the whole fan-out is the same footgun `output()` documents.
    target = sorted(matched)[-1]
    inst = instances[target]
    if inst.state not in TERMINAL_STATES:
        return _err(
            "WF_NODE_NOT_TERMINAL",
            f"node {node_id!r} is {inst.state.value}, not terminal — nothing to reconstruct yet",
        )

    base = target.split("#")[0].split("@")[0]
    node = node_by_path.get(base)

    # The ledger slice for THIS instance. `journal.ledger` reads events.jsonl (the LEDGER_KINDS
    # subset the flywheel reads); filtering to the exact instance_path keeps a sibling foreach
    # item's events out of this node's forensics.
    node_events = [e for e in journal_mod.ledger(run_id) if e.get("instance_path") == target]

    # resolved_prompt — the fully-resolved post-binding prompt the controller stored raw at
    # `<path>::prompt`. Inline when small; a ref past the inline boundary, so a megabyte prompt
    # does not ride in every inspect response. The ref is the one step_completed already recorded.
    prompt_ref = ""
    for e in node_events:
        if e.get("kind") == journal_mod.STEP_COMPLETED and e.get("resolved_prompt_ref"):
            prompt_ref = str(e["resolved_prompt_ref"])
            break
    stored_prompt = store.read_output(run_id, f"{target}::prompt")
    resolved_prompt: Any
    if isinstance(stored_prompt, str) and stored_prompt:
        if len(stored_prompt.encode("utf-8")) > journal_mod.MAX_INLINE_OUTPUT_BYTES:
            resolved_prompt = {"ref": prompt_ref or f"{target}::prompt"}
        else:
            resolved_prompt = stored_prompt
    elif prompt_ref:
        resolved_prompt = {"ref": prompt_ref}
    else:
        # A transform/action node has no LLM prompt — an empty string, not a fabricated ref.
        resolved_prompt = ""

    # resolved_inputs — what actually reached the node: each declared dependency mapped to the
    # output it produced. Reconstructed from persisted state the way `_resolved_inputs` builds
    # it live (node_deps → the dep's stored output), so a read after a restart sees the same
    # closure the run did. Only declared deps, not the whole output map.
    id_to_base: dict[str, str] = {}
    for p, n in node_by_path.items():
        if n.id:
            id_to_base.setdefault(n.id, p)
    resolved_inputs: dict[str, Any] = {}
    for dep in sorted(node_deps(node.config or {}) if node else set()):
        dep_base = id_to_base.get(dep)
        dep_matches = (
            [
                ip
                for ip in instances
                if ip == dep_base or ip.startswith(f"{dep_base}#") or ip.startswith(f"{dep_base}@")
            ]
            if dep_base
            else []
        )
        resolved_inputs[dep] = (
            store.read_output(run_id, sorted(dep_matches)[-1]) if dep_matches else None
        )

    # output — the node's terminal value, unless it was offloaded. An artifact pointer (a
    # future WV-11 ref that is not an `outputs/` path) or a spilled oversize/binary payload is
    # returned as `{"artifact_ref": ...}` rather than inlined: the whole point of the spill is
    # that the blob does not ride in the response, and a 5MB output (or a base64 screenshot)
    # inline would flood whatever renders this.
    output_ref = inst.output_ref or ""
    raw_output = store.read_output(run_id, target)
    if output_ref and not output_ref.startswith("outputs/"):
        output_field: Any = {"artifact_ref": output_ref}
    elif journal_mod.is_binary_payload(raw_output) or _serialized_bytes(raw_output) > (
        journal_mod.MAX_INLINE_OUTPUT_BYTES
    ):
        output_field = {"artifact_ref": output_ref or target}
    else:
        output_field = raw_output

    return _ok(
        run_id=run_id,
        node_id=node_id,
        instance_path=target,
        state=inst.state.value,
        resolved_prompt=resolved_prompt,
        resolved_inputs=resolved_inputs,
        output=output_field,
        # The retry records for this node — empty for a node that succeeded first try, since
        # `step_attempt` is only journaled on a retry. A list, so a reader can show each try's
        # typed failure and fix instruction rather than a bare count.
        attempts=[e for e in node_events if e.get("kind") == journal_mod.STEP_ATTEMPT],
        ledger_events=node_events,
        # cached — served from the resume/rewind cache (WF2-A1) rather than a fresh run. The
        # `step_cached` event is the record; "did my edit actually re-run this?" is answerable
        # from here, which is the question the event exists to answer.
        cached=any(e.get("kind") == journal_mod.STEP_CACHED for e in node_events),
    )


def _serialized_bytes(value: Any) -> int:
    """Byte size of a value's canonical JSON — the same boundary the journal spills at, so
    the inspect view offloads exactly what the journal would have."""
    import json

    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


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


async def delete_run(
    run_id: str, *, supervisor: Any = None, keep_open: bool = False
) -> dict[str, Any]:
    """Delete a TERMINAL run and its artifacts, tearing its workspace down FIRST.

    Refused while a run can still move. Deleting a live run would leave its controller writing
    journal entries and terminal status to a row that no longer exists — the single-writer
    discipline (WF2-R10) assumes the row outlives the writer. Cancel first, then delete: two
    deliberate steps for two genuinely different intents.

    Removes the run DIRECTORY as well as the row. A row-only delete would leave the journal,
    outputs and continuations on disk forever, invisible to every surface — the run would look
    gone while still costing the disk and still holding a live resume token.

    ASYNC because teardown runs a subprocess (WORK-CONTAINERS §4.1), and it must run BEFORE the
    directory goes away — that is the whole reason teardown exists. A scratch workspace lives
    UNDER the run dir, so the `rmtree` below would take it out; running teardown after that would
    execute `docker compose down` against a path that no longer holds the compose file. The one
    caller (`handlers.api_run_delete`) is already async.

    `keep_open` is the §4.1 override for when the workspace IS the deliverable: the run row and
    directory go, the workspace stays. Teardown still runs — keeping the directory is not keeping
    the processes.
    """
    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    if run.status not in TERMINAL_RUN_STATUSES:
        return _err(
            "WF_RUN_NOT_TERMINAL",
            f"run is {run.status.value}; cancel it before deleting",
            status=run.status.value,
        )
    # A controller for a terminal run is finished but may still be registered; dropping it
    # first means nothing holds a handle to a run being deleted.
    controller = _live(run_id, supervisor)
    if controller is not None and supervisor is not None:
        try:
            supervisor.forget(run_id)
        except Exception:
            logger.debug("could not unregister the controller for %s", run_id, exc_info=True)

    # Traversal guard, same shape as `prune_fork`: the id reaches here from a URL, and a
    # crafted one must not walk out of the runs root.
    target = store.run_dir(run_id).resolve()
    root = store.runs_root().resolve()
    if root not in target.parents:
        return _err("WF_RUN_DELETE_REFUSED", "refusing to delete a path outside the runs root")
    torn = await teardown_workspace(run, reason="delete", keep_open=keep_open)
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    # The inbox rows go too: a gate that was open when the run was cancelled would otherwise
    # outlive the run entirely and be unanswerable forever. The state comes off the supervisor,
    # which is what the route has — a delete is a request path, not the engine's own.
    attention.resolve_run_items(getattr(supervisor, "_state", None), run_id)
    deleted = store.delete(run_id)
    return _ok(run_id=run_id, deleted=deleted, teardown=torn.to_dict())


async def teardown_workspace(
    run: Any, *, reason: str, keep_open: bool = False, runner: Any = None
) -> Any:
    """Tear a run's workspace down before its directory is deleted (WORK-CONTAINERS §4.1).

    The shared performer for BOTH deletion paths — the explicit delete here and retention expiry
    in `watchdog.prune_runs`. One function rather than two call sites doing the same thing,
    because the two would eventually disagree about the order, and the order IS the contract.

    Gated by `workflows.workspace_teardown_on_expiry`. That knob has a real reader precisely
    here: when it is off, the workspace is removed without running its command. Off is the
    escape hatch for a teardown command that is itself the problem (one that hangs, or one whose
    author got it wrong), which is a real situation — but it defaults ON, because leaving a
    `docker compose` up after the run that started it is gone is the commoner harm.

    Never raises: both callers are deletion paths, and a run that cannot be deleted because its
    teardown threw would be a row visible forever with no way to remove it.
    """
    from personalclaw.workflows import provisioning

    try:
        from personalclaw.config.loader import AppConfig

        enabled = bool(AppConfig.load().workflows.workspace_teardown_on_expiry)
    except Exception:
        # A config read failure defaults to RUNNING teardown. Fail-safe in the direction of
        # stopping services: the cost of an extra teardown is a no-op (the BYOI contract requires
        # idempotency), while skipping one leaks whatever it was going to stop.
        enabled = True

    state = provisioning.workspace_state(run)
    if not state:
        return provisioning.TornDown()
    if not enabled:
        # Removal still happens (the directory would be orphaned otherwise) — only the declared
        # COMMAND is skipped, which is what the knob is about.
        state = dict(state)
        state.pop("teardown", None)
        extra = getattr(run, "extra", None)
        if isinstance(extra, dict):
            extra[provisioning.WORKSPACE_KEY] = state
    try:
        workspace_dir = _run_workspace_dir(run)
        torn = await provisioning.teardown(
            run, workspace_dir=workspace_dir, keep_open=keep_open, runner=runner
        )
    except Exception:
        logger.warning("workspace teardown failed for %s", getattr(run, "id", "?"), exc_info=True)
        return provisioning.TornDown()
    try:
        from personalclaw.workflows.journal import Journal

        Journal(str(getattr(run, "id", "") or "")).workspace_teardown(torn.to_dict(), reason=reason)
    except Exception:
        logger.debug("could not journal the workspace teardown", exc_info=True)
    return torn


def _run_workspace_dir(run: Any) -> str:
    """The codebase a run's project binds, for git-side teardown. "" when there is none.

    Read from the PROJECT rather than the run record on purpose: a bound workspace can be
    re-pointed between the run and its deletion, and the git operations (worktree remove, branch
    bookkeeping) must target wherever the repo is NOW, not where it was.
    """
    pid = str(getattr(run, "project_id", "") or "")
    if not pid:
        return ""
    try:
        from personalclaw.tasks.hierarchy import HierarchyStore

        project = HierarchyStore().get_project(pid)
        return str(getattr(project, "workspace_dir", "") or "") if project else ""
    except Exception:
        logger.debug("project workspace lookup failed for %s", pid, exc_info=True)
        return ""


def drop_status(run_id: str) -> dict[str, Any]:
    """The run's file-drop policy + what has already been dropped (WORK-CONTAINERS §2.5).

    Returns OK with `enabled: false` for a run whose template declared no drop, rather than an
    error:
    "this run does not accept files" is a fact the UI renders as a disabled affordance, and a 4xx
    would make an intentional configuration look like a broken request (§2.5's honest disabled
    status).
    """
    from personalclaw.workflows import filedrop

    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    policy = filedrop.parse_policy(store.read_spec(run_id))
    return _ok(
        enabled=policy.enabled,
        reason=policy.reason,
        auto_accept_mimes=list(policy.auto_accept_mimes),
        max_files=filedrop.MAX_DROPPED_FILES,
        files=filedrop.read_manifest(run_id),
    )


def accept_dropped_file(
    run_id: str, *, filename: str, data: bytes, mime: str = "", confirmed: bool = False
) -> dict[str, Any]:
    """Ingest one dropped file into the run's immutable zone, gated on approval.

    Every refusal is a DISTINCT code, because the four reasons a drop is refused need four different
    reactions from the caller: not accepting files at all (hide the affordance), needing approval
    (show what + size and ask), too many files (offer a clear-out), too large (nothing to do but
    pick a smaller file). One generic rejection would collapse them into a dead end.
    """
    from personalclaw.workflows import filedrop

    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    policy = filedrop.parse_policy(store.read_spec(run_id))
    if not policy.enabled:
        return _err("WF_DROP_DISABLED", policy.reason or "this run does not accept dropped files")
    needs, why = filedrop.approval_required(policy, mime, confirmed=confirmed)
    if needs:
        return _err(
            "WF_DROP_APPROVAL_REQUIRED",
            why,
            pending={"filename": filedrop.safe_filename(filename), "size": len(data), "mime": mime},
        )
    existing = filedrop.read_manifest(run_id)
    safe = filedrop.safe_filename(filename)
    if len(existing) >= filedrop.MAX_DROPPED_FILES and not any(
        e.get("filename") == safe for e in existing
    ):
        return _err(
            "WF_DROP_LIMIT",
            f"this run already holds {filedrop.MAX_DROPPED_FILES} dropped files",
        )
    try:
        entry = filedrop.store_dropped_bytes(run_id, filename, data)
    except (OSError, ValueError) as exc:
        return _err("WF_DROP_WRITE_FAILED", f"could not store the dropped file: {exc}")
    entry["mime"] = mime
    entry["approved"] = not needs
    entry["accepted_at"] = _now()
    filedrop.record_drop(run_id, entry)
    return _ok(file=entry, files=filedrop.read_manifest(run_id))


def outbox(run_id: str) -> dict[str, Any]:
    """The run's published-artifact listing — the §2.5 outbox, newest-first.

    A read over what `apply_publish` journalled, so the listing and the publishes cannot disagree.
    Each row carries its artifact `kind`; the FE resolves the preview through the `contentTypes`
    registry rather than this route naming renderers.
    """
    from personalclaw.workflows import filedrop

    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    return _ok(files=filedrop.outbox_entries(run_id))


def introspect(run_id: str) -> dict[str, Any]:
    """The nine-question introspection projection for one run (WORK-CONTAINERS §6.4, R6).

    Everything here is a PROJECTION over the journal this run already wrote —
    `introspection.py` holds the arithmetic and this function holds the reads. No metrics
    store, per the plan's own words: "pass-rate, failure distribution and latency
    percentiles are queries over this".

    The template card aggregates ACROSS runs of the same template, which is why this reads
    the sibling runs' ledgers too: "what is costing money" is a question about the template,
    not about the one run in front of you, and a p95 computed from a single run would just
    restate the run. The sibling read is bounded by `_TEMPLATE_CARD_RUNS` because a personal
    instance accumulates runs forever and the surface that answers "what does this usually
    cost" must not get slower every week.

    `checklist_gaps` runs LAST, over the payload actually assembled, so the response says
    which of the nine questions its own body cannot answer. That is what makes the checklist
    a contract rather than a comment: a surface rendering eight of nine has a named hole, and
    the name arrives with the data instead of in a review.
    """
    from personalclaw.workflows import filedrop, introspection

    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")

    events = journal_mod.ledger(run_id)
    stats = introspection.run_stats(run_id, events)
    gates = introspection.gate_stats(events)

    # Evidence for the Proof section is the run's OWN published outbox, not a directory scan:
    # a file the run dropped but never published is a byproduct, and counting it as evidence
    # would let a run prove itself with its own scratch output.
    evidence = [str(entry.get("slug") or "") for entry in filedrop.outbox_entries(run_id)]
    proof = introspection.proof_section(stats, evidence_files=[e for e in evidence if e])

    # The template card, across this template's recent runs. The current run is included —
    # excluding it would make the card disagree with the strip directly above it.
    card = introspection.TemplateCard(template=run.workflow_name)
    edges = introspection.EdgeStats()
    if run.workflow_name:
        siblings, _total = store.list_runs(
            workflow_name=run.workflow_name, limit=_TEMPLATE_CARD_RUNS
        )
        # Read each sibling's ledger ONCE and reuse it for every cross-run projection: the run
        # economics, the said-no badge and the edge distribution all ask the same events, and a
        # personal instance's card must not get slower by reading them three times.
        sibling_ledgers = [
            (r.id, events if r.id == run_id else journal_mod.ledger(r.id)) for r in siblings
        ]
        sibling_stats = [
            stats if rid == run_id else introspection.run_stats(rid, evs)
            for rid, evs in sibling_ledgers
        ]
        # Per-branch case and per-judge verdict distributions across the template (PP-8). Sample-
        # gated exactly like the said-no badge below: "always case A" and "case B never taken" are
        # claims about the selector's HISTORY, and one run can never carry the sample for either.
        edges = introspection.edge_stats([evs for _rid, evs in sibling_ledgers])
        # Gate warnings are the template's, not the run's: "this gate has never rejected" is a
        # claim about the gate's history, and one run can never carry the sample for it. The edge
        # findings (dead cases, degenerate selectors) ride the same list — they are the same shape
        # of claim over the same sample, and a reader should meet them in one place.
        warnings = sorted(
            {
                w
                for g in _template_gates(sibling_ledgers, run_id, gates).values()
                if (w := g.fake_check_warning())
            }
            | set(edges.warnings())
        )
        card = introspection.template_card(run.workflow_name, sibling_stats, warnings=warnings)

    from personalclaw.workflows.human_input import list_continuations

    nodes = _nodes_of(run_id)
    # The open asks, as the wire rows the inbox already renders. Read from the continuation
    # directory rather than inferred from node state: a WAITING node is not necessarily
    # answerable (a `wait` deadline is nobody's decision), and offering an answer box for a
    # timer would teach the user the surface guesses.
    open_asks = [
        {"resume_token": c.token, "node_id": c.node_id, "ask": journal_mod.redact(c.ask or {})}
        for c in list_continuations(run_id)
        if not c.expired
    ]
    answers: dict[str, Any] = {
        # "what is running now, and why" — the live nodes plus the template that asked for them.
        "running": {
            "status": run.status.value,
            "workflow": run.workflow_name,
            "nodes": [n for n in nodes if n.get("state") in ("running", "ready", "waiting")],
        },
        # "what changed" — the journal timeline, which is also the attempt ledger's source.
        "changed": introspection_timeline(events),
        # "what is blocked" — a waiting node is blocked on something external by definition.
        "blocked": [n for n in nodes if n.get("state") == "waiting"],
        # "what needs my approval" — the open continuations, i.e. the answerable gates.
        "approval": open_asks,
        "failed": [n for n in nodes if n.get("state") in ("failed", "scope_violation")],
        "cost": stats.to_dict(),
        # "what is risky" — the degraded nodes, every said-no warning the gates earned, and the
        # edge findings (a dead case or a selector doing no work is a risk the same way a fake check
        # is: the plan declares a decision the run never actually makes).
        "risky": {
            "degraded": [n for n in nodes if n.get("state") == "degraded"],
            "gates": [g.to_dict() for g in gates.values()],
            "edges": edges.to_dict(),
            "verification_debt": stats.verification_debt,
        },
        # "what happens next if I say nothing" — a WAITING run does nothing until answered; a
        # terminal run is done. Stated rather than implied: the question the plan promotes to a
        # criterion is exactly the one every other surface leaves to inference.
        "next": _next_if_silent(run, nodes, open_asks),
        "proof": proof.to_dict(),
    }
    return _ok(
        run_id=run_id,
        workflow=run.workflow_name,
        stats=stats.to_dict(),
        gates={node_id: g.to_dict() for node_id, g in gates.items()},
        # Per-branch case and per-judge verdict distributions across the template (PP-8), beside the
        # said-no gate table rather than on a surface of their own — a routing decision and a gate
        # decision are the same kind of edge, and a reader should meet them in one place.
        edges=edges.to_dict(),
        template_card=card.to_dict(),
        proof=proof.to_dict(),
        timeline=answers["changed"],
        # The live touched-items feed (§6.5): what this run published and what was handed to it.
        # Rides this payload rather than a route of its own — it answers "what changed" for
        # THINGS, where the timeline answers it for STEPS, and a reader needs both together.
        touched=touched_items(run_id),
        answers=answers,
        # Empty is the healthy answer. A non-empty list names a question this payload cannot
        # answer, which is a backend gap — the FE cannot close it by rendering harder.
        checklist_gaps=introspection.checklist_gaps(answers),
    )


def touched_items(run_id: str) -> list[dict[str, Any]]:
    """What this run TOUCHED, newest-first — the live touched-items feed (§6.5 / R13).

    Unions the two run-attributed mutation records that exist today:

    * ``publishes.jsonl`` — every artifact this run published, versioned or converged (§2.5).
    * the file-drop manifest — every file handed INTO the run.

    Both are already run-scoped, which is the whole reason the feed is buildable: attribution is
    the hard part, not the union. A feed assembled by scanning the artifact registry for things
    that changed recently would attribute another run's work to this one the moment two runs
    overlapped.

    **The knowledge half is absent, not omitted.** Knowledge mutations carry no run attribution
    (S47's lineage covered artifacts only), so a knowledge row here would have to be guessed from
    timing — and a feed that says "this run wrote that memory" on a coincidence is worse than a
    feed that does not mention memory. See the plan's §6.5 note; closing it is a journal-format
    change, not a rendering one.
    """
    from personalclaw.workflows import filedrop

    rows: list[dict[str, Any]] = []
    for entry in filedrop.outbox_entries(run_id):
        rows.append(
            {
                "kind": "artifact",
                "ref": str(entry.get("slug") or ""),
                "label": str(entry.get("artifact") or entry.get("slug") or ""),
                # `version` / `noop` / `create` — the verb matters: a converged republish is not
                # the same event as a new version, and collapsing them would make an unchanged
                # artifact look freshly written.
                "action": str(entry.get("action") or ""),
                "detail": str(entry.get("change_note") or ""),
                "node_id": str(entry.get("node_id") or ""),
                "ts": str(entry.get("updated_at") or ""),
            }
        )
    for entry in filedrop.read_manifest(run_id):
        rows.append(
            {
                "kind": "file",
                "ref": str(entry.get("filename") or ""),
                "label": str(entry.get("filename") or ""),
                "action": "dropped",
                "detail": str(entry.get("mime") or ""),
                "node_id": "",
                "ts": str(entry.get("accepted_at") or ""),
            }
        )
    # Newest-first: a feed is read from the top, and the most recent touch is the one a watching
    # user is waiting for. Empty timestamps sort last rather than crashing the comparison.
    rows.sort(key=lambda r: r["ts"] or "", reverse=True)
    return rows


#: How many of a template's recent runs the card aggregates. Bounded because a personal
#: instance accumulates runs indefinitely and the surface answering "what does this usually
#: cost" must not get slower every week. Newest-first, so the bound drops the oldest history
#: rather than the runs a user is actually asking about.
_TEMPLATE_CARD_RUNS = 50


def _template_gates(
    sibling_ledgers: list[tuple[str, list[dict[str, Any]]]],
    run_id: str,
    own: dict[str, Any],
) -> dict[str, Any]:
    """Per-gate stats ACROSS the template's runs, over ledgers the caller already read.

    The fake-check badge needs a SAMPLE: `FAKE_CHECK_MIN_RUNS` gate resolutions is a claim
    about the gate's history, and computing it from one run would leave the badge permanently
    unarmed — the exact "declared but can never fire" shape this atom exists to close. Takes the
    pre-read ledgers so the run-economics, said-no and edge-distribution projections share one read
    of each sibling rather than three.
    """
    from personalclaw.workflows import introspection

    merged: dict[str, Any] = {}
    for rid, events in sibling_ledgers:
        gates = own if rid == run_id else introspection.gate_stats(events)
        for node_id, stats in gates.items():
            into = merged.setdefault(node_id, introspection.GateStats(node_id=node_id))
            into.passes += stats.passes
            into.rejects += stats.rejects
            into.retries_consumed += stats.retries_consumed
    return merged


#: Journal kinds the cockpit timeline shows. A whitelist rather than "everything": the ledger
#: carries internal bookkeeping (cache keys, effect idempotency) that would bury the handful of
#: events a human reads, and a timeline nobody can scan is a timeline nobody opens.
_TIMELINE_KINDS = (
    "run_started",
    "run_finished",
    "step_started",
    "step_completed",
    "step_failed",
    "step_skipped",
    "step_cached",
    "step_attempt",
    "step_escalated",
    "gate_resolved",
    "gate_revised",
    "handoff",
    "decision",
    "steering",
    "breaker_trip",
)


def introspection_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The journal timeline + attempt ledger, oldest-first, redacted.

    Routed through `journal_mod.redact` — the SAME recursive redactor the journal writer uses,
    reused rather than re-derived so the two cannot drift. The ledger records a node's model and
    failure detail verbatim, and a failure message is exactly where a credential surfaces in a
    screenshot.

    Oldest-first because this reads as a narrative: "what changed" answered newest-first makes a
    reader reconstruct causality backwards.
    """
    out: list[dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if str(event.get("kind") or "") not in _TIMELINE_KINDS:
            continue
        row = {
            "kind": str(event.get("kind") or ""),
            "ts": str(event.get("ts") or ""),
            "node_id": str(event.get("node_id") or ""),
            "instance_path": str(event.get("instance_path") or ""),
            "attempt": event.get("attempt"),
            "state": str(event.get("state") or ""),
            "duration_secs": event.get("duration_secs"),
            "tokens": event.get("tokens"),
            "cost_usd": event.get("cost_usd"),
            "model": str(event.get("model") or ""),
            "approved": event.get("approved"),
            "detail": event.get("detail") or event.get("error") or "",
        }
        out.append(journal_mod.redact(row))
    return out


def _next_if_silent(
    run: Any, nodes: list[dict[str, Any]], open_asks: list[dict[str, Any]]
) -> dict[str, Any]:
    """ "What happens next if I say nothing" — answered, not implied.

    The one checklist question no existing surface answers, and the one that decides whether a
    user can walk away. Three real cases, because they demand different user action: a run
    waiting on an answer will sit there indefinitely (the user IS the blocker), a running run
    proceeds on its own, and a terminal run has already stopped.
    """
    from personalclaw.workflows.models import TERMINAL_RUN_STATUSES

    if run.status in TERMINAL_RUN_STATUSES:
        return {"action": "nothing", "detail": f"this run is {run.status.value}", "queued": []}
    if open_asks:
        return {
            "action": "waits",
            "detail": (
                f"{len(open_asks)} question(s) are waiting for an answer — this run makes no "
                "further progress until one is given"
            ),
            "queued": [str(c.get("node_id") or "") for c in open_asks],
        }
    queued = [str(n.get("node_id") or "") for n in nodes if n.get("state") in ("pending", "ready")]
    return {
        "action": "proceeds",
        "detail": (
            f"{len(queued)} node(s) are queued and will run without further input"
            if queued
            else "no queued work remains; the run is finishing"
        ),
        "queued": queued,
    }


def workspace_review(run_id: str) -> dict[str, Any]:
    """The code-run cockpit's diff panel + the two reintegration verbs (WORK-CONTAINERS §4.1).

    Pure read: it shells out for `git status --porcelain` and a `merge-tree` conflict probe,
    neither of which mutates either tree. Reintegration is OFFERED, never performed — the plan's
    explicit ruling, and the reason this is a GET rather than a POST.
    """
    from personalclaw.workflows import provisioning

    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    try:
        body = provisioning.reintegration(run, workspace_dir=_run_workspace_dir(run))
    except Exception as exc:
        logger.debug("workspace review failed for %s", run_id, exc_info=True)
        return _err("WF_WORKSPACE_UNREADABLE", f"could not read the run's workspace: {exc}")
    # `preserved_workspace_path` is recorded HERE because this is where the live alive+dirty state
    # is computed. Reading it costs a git call, so the run record carries the answer for every
    # surface that cannot afford one (the Work board, the export archive) — and the value is only
    # non-empty when a dirty workspace was genuinely kept.
    try:
        state = provisioning.inspect_run(run)
        if provisioning.stamp_preserved_path(run, state):
            store.save(run)
    except Exception:
        logger.debug("could not record preserved_workspace_path for %s", run_id, exc_info=True)
    return _ok(**body)


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


def steer_run(run_id: str, text: str) -> dict[str, Any]:
    """Queue a mid-run steering instruction (LOOPS-EVOLUTION R14).

    Recorded ON THE RUN and consumed at the next iteration boundary, exactly like a pause:
    the tick loop is the single writer, and injecting mid-iteration would race the worker's
    own state. Queued rather than applied, so the user can steer a run that is busy without
    waiting for it — the alternative today is cancel-and-restart, which loses the cycle
    context that made steering worth doing.
    """
    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    if run.status in TERMINAL_RUN_STATUSES:
        # A terminal run cannot act on an instruction, and silently accepting one would
        # leave the user believing they had changed something.
        return _err("WF_RUN_ALREADY_TERMINAL", f"run is already {run.status.value}")
    cleaned = (text or "").strip()
    if not cleaned:
        return _err("WF_STEER_EMPTY", "a steering instruction needs text")

    pending = run.extra.get("steering_queue")
    if not isinstance(pending, list):
        pending = []
    pending.append({"text": cleaned[:4000], "queued_at": _now()})
    run.extra["steering_queue"] = pending
    store.save(run)
    return _ok(run_id=run_id, queued=len(pending))


def pending_steering(run_id: str) -> dict[str, Any]:
    """What is queued but not yet consumed — so the UI can show it as pending.

    A queued instruction the user cannot see is indistinguishable from one that was
    dropped, and they will queue it again.
    """
    run = store.get(run_id)
    if run is None:
        return _err("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    pending = run.extra.get("steering_queue")
    items = pending if isinstance(pending, list) else []
    return _ok(run_id=run_id, pending=items, count=len(items))


def resolve_confirmation(
    run_id: str,
    *,
    supervisor: Any = None,
    verb: str = "",
    token: str = "",
    note: str = "",
    responder: str = "",
) -> dict[str, Any]:
    """Resolve a pending confirmation by VERB — the backend the DagView's Approve/Deny needs.

    Rides `resume_run` rather than reaching into the controller: there is ONE place a resume
    token is consumed (the claim primitive lives with the token, and S57 measured a read-then-
    unlink version letting multiple callers consume one approval in 36 of 40 races). A second
    resolve path would be a second chance to double-approve.

    What this adds over `resume_run` is the VERB vocabulary: `approve | reject | skip | quit`,
    with an unknown verb REFUSED rather than treated as a reject. A typo silently declining an
    approval would reject work the user meant to allow, and they would have no way to know why.

    `skip` and `quit` resolve nothing on purpose — skip leaves the item pending for the next
    pass (different from rejecting it) and quit stops asking without answering. Neither touches
    the run, so neither consumes the token.
    """
    from personalclaw.workflows.confirmation import resolve as resolve_verb

    resolution, error = resolve_verb(verb, note=note)
    if resolution is None:
        return _err("WF_CONFIRM_VERB_INVALID", error)
    if not resolution.resumes:
        # Skip/quit are decisions ABOUT the queue, not answers to the gate. Returning ok=True with
        # `resumed=False` says exactly that; consuming the token here would burn a single-use claim
        # on a non-answer and strand the gate forever.
        return _ok(
            run_id=run_id,
            resumed=False,
            still_pending=resolution.still_pending,
            verb=resolution.verb,
        )
    result = resume_run(
        run_id,
        supervisor=supervisor,
        token=token,
        # The gate's answer is the APPROVAL BOOLEAN, which is what the engine's gate resolution
        # reads. Passing the verb string would make `reject` truthy — the single worst possible
        # mistranslation in this path.
        answer=resolution.approved,
        responder=responder,
    )
    result.setdefault("verb", resolution.verb)
    result.setdefault("approved", resolution.approved)
    return result


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

    `answer` also carries the `revise{step_ref, comment}` verb, which `controller.resume`
    recognises: one step is amended and the gate re-asks, instead of the reviewer having to
    reject the whole plan and re-run it to get one sentence changed.
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
    from personalclaw.workflows import loop_aliases
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
        # Authoring sugar, in the manifest because the manifest is what an authoring model reads
        # to learn the shapes it may write. A macro or block absent here is one a model will
        # never use, so the library would be dead weight.
        macros=macros.macro_names(),
        shared_blocks=blocks.block_names(),
        # The legacy loop-kind aliases (LOOPS-EVOLUTION R10a). In the manifest because the
        # picker and any authoring model both need to know that `kind: goal` still resolves
        # — and because listing them here is what makes the Phase-4 retirement measurable
        # rather than a guess about who still refers to loops by kind.
        loop_aliases=loop_aliases.alias_manifest(),
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


def _with_declared_defaults(spec: dict[str, Any], provided: dict[str, Any]) -> dict[str, Any]:
    """Fill in every declared input the caller omitted, using its declared default.

    Applied at RUN START, once, so the run record shows the values the run actually used — a run
    whose inputs were completed lazily at each binding would leave a record that does not explain
    its own behaviour.

    A declared input with NO default still gets a key, valued "": the alternative is a binding
    error on an input the template said was optional, which is the bug this function exists to
    fix. An optional input's whole meaning is "the workflow works without it".

    The caller's value always wins, including an explicit empty string — a user who deliberately
    cleared a field is not asking for the default back.
    """
    declared = spec.get("inputs") or {}
    if not isinstance(declared, dict):
        return provided
    out = dict(provided)
    for key, meta in declared.items():
        if key in out:
            continue
        default = meta.get("default") if isinstance(meta, dict) else None
        out[str(key)] = "" if default is None else default
    return out


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
    # How many instances share each base path — the `12` in "[3/12]". Counted here rather than
    # stored, so a rewind that re-expands a fan-out cannot leave a stale total behind.
    totals: dict[str, int] = {}
    for path in instances:
        totals[path.split("#")[0].split("@")[0]] = (
            totals.get(path.split("#")[0].split("@")[0], 0) + 1
        )

    out: list[dict[str, Any]] = []
    for path in sorted(instances):
        inst = instances[path]
        base = path.split("#")[0].split("@")[0]
        row: dict[str, Any] = {
            "instance_path": path,
            "node_id": ids.get(base, ""),
            "state": inst.state.value,
            "attempt": inst.attempt,
            "degraded_reason": inst.degraded_reason,
            "failure": inst.failure.to_dict() if inst.failure else None,
        }
        # Per-item foreach context (WF2-R5), included only for an actually-iterated instance:
        # an `item_index` on a lone node would render "[1/1]", which is noise.
        suffix = re.search(r"[#@](\d+)$", path)
        if suffix and totals.get(base, 0) > 1:
            row["item_index"] = int(suffix.group(1))
            row["item_total"] = totals[base]
            if inst.item_label:
                row["item_label"] = inst.item_label
        out.append(row)
    return out


def _completion_summary(run: Any, status: RunStatus) -> str:
    """A one-line completion summary for the launching session's mirror.

    Drawn from the run's own recorded handoff summary — what the run said it produced —
    rather than fabricated, matching `controller._revise_project_overview`. A run that said
    nothing hands over nothing, so the line falls back to name + status, which is honest.
    """
    name = getattr(run, "workflow_name", "") or "run"
    line = f"{name} → {status.value}"
    try:
        handoff = (getattr(run, "extra", {}) or {}).get("summary")
        if isinstance(handoff, str) and handoff.strip():
            line += f": {handoff.strip().splitlines()[0][:200]}"
    except Exception:
        logger.debug("completion summary handoff read failed for %r", name, exc_info=True)
    return line


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
