"""What a workflow definition is checked for before it is saved or run.

Every save of a definition is checked here first (``service.author_def``): it has a name of its own,
its macros and blocks are expanded, the values a read hid are restored, it holds no literal
credential, and it is valid. A definition that is run once and saved nowhere, a batch's tasks
compiled for the one run that starts them (``batch_start``), is checked the same way
(:func:`run_once_def`), and screened as a save of it would be: a step that would do more than read
does so only on the owner's own yes.
"""

from __future__ import annotations

from typing import Any

from personalclaw.workflows import (
    blocks,
    macros,
    models,
    provisioning,
    secrets,
    template_lint,
    versions,
)
from personalclaw.workflows.models import WorkflowDef, valid_name
from personalclaw.workflows.validator import validate_spec


async def run_once_def(
    *,
    name: str,
    root: dict[str, Any],
    description: str = "",
    workspace: dict[str, Any] | None = None,
    strict: bool = True,
    owner_allowed: bool = False,
) -> dict[str, Any]:
    """A definition that is run once and saved nowhere: a batch's tasks, compiled for the one run
    that starts them (`batch_start`), which holds it as every run holds the spec it runs. Nothing
    else can name it: it is in no list of your workflows, no read finds it, and no start or delete
    reaches it.

    It is checked as a save of it would be (:func:`checked_spec`), and screened as its write would
    be (``service._write_definition``): a step that would do more than read does so only on the
    owner's own yes (*owner_allowed*: her Allow of the ask that named each step). Answers the
    service's envelope with the ``definition``, shaped as a stored one reads (its first version,
    saved by an agent), or the failure that refused it."""
    from personalclaw.workflows import service  # it imports this module

    spec, body = await checked_spec(
        name=name, root=root, description=description, workspace=workspace, strict=strict
    )
    if spec is None:
        return body
    loosened = await service._loosenings(spec)
    if loosened and not owner_allowed:
        said = "; ".join(f"“{s.label}” {', and '.join(s.may)}" for s in loosened)
        return service._service_failure(
            "WF_DEF_NEEDS_OWNER_YES",
            f"{name!r} did not start: its steps would do more ({said}), and they do that only on "
            "the owner's own Allow of the ask that names each of them.",
            steps=[step.to_dict() for step in loosened],
        )
    stamp = service._now()
    shaped = {
        **spec,
        "version": 1,
        "provenance": versions.AGENT,
        "created_at": stamp,
        "updated_at": stamp,
    }
    return service._ok(definition=WorkflowDef.from_dict(shaped).to_dict())


async def checked_spec(
    *,
    name: str,
    root: dict[str, Any],
    description: str = "",
    inputs: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    strict: bool = True,
    workspace: dict[str, Any] | None = None,
    runtime_hints: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
    on_overlap: str = "",
    based_on: str = "",
    based_on_version: int = 0,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The definition a save of *name* would write, checked as every save is
    (``service.author_def``): a name of its own, its macros and blocks expanded, its hidden values
    restored, no literal credential in it, and valid. ``(spec, body)``: the spec as it would be
    written and what its check found (issues, levels, lint), or ``(None, failure)`` saying what
    refused it."""
    from personalclaw.workflows import service  # it imports this module

    if not valid_name(name):
        return None, service._service_failure(
            "WF_DEF_NAME_INVALID",
            f"{name!r} is not a valid name — use lowercase letters, digits and hyphens "
            "(it becomes a directory)",
        )
    # A read-only provider's names are RESERVED. Saving over one used to succeed and then be
    # ignored: the def list showed the name twice (bundled + user) while `get_def` and every
    # run-start returned the FIRST provider in sort order — `bundled` — so a user who "edited" a
    # bundled template saw their save succeed, saw their copy in the list, and ran the original
    # (issue 764). Split-brain, with the UI reporting the half that was not used.
    #
    # Refused rather than resolved in the user's favour, because `bundled_defs` already decided
    # this: templates are served FROM THE PACKAGE so that `pip install --upgrade` ships new ones
    # with no reconciliation. A user def shadowing a bundled name reintroduces exactly the "did
    # the user edit it?" question that design avoided, and an upgrade's improved template would
    # sit masked behind a stale copy with nothing to say so.
    #
    # Checked BEFORE validation so a dry run reports it too — the point of `save=False` is to
    # learn what is wrong before committing, and the name is the cheapest thing to fix.
    reserved_by = await service._reserved_name_provider(name)
    if reserved_by:
        return None, service._service_failure(
            "WF_DEF_NAME_RESERVED",
            f"{name!r} is the name of a read-only {reserved_by} template. Save your version "
            "under a different name — a copy under your own name is never touched by an "
            "upgrade, while a shadow of a bundled name would be ignored at run time.",
            provider=reserved_by,
        )
    spec = {
        "name": name,
        "description": description,
        "root": root or {},
        "inputs": inputs or {},
        "tags": tags or [],
    }
    if isinstance(workspace, dict) and workspace:
        # The `workspace:` declaration, carried through authoring so it reaches the persisted
        # def. This dict is an allowlist and the parameter did not exist: an author (including
        # `compile_batch`) could declare a workspace and have it silently dropped here, leaving a
        # run-start applier reading a key nothing ever wrote — the same config-round-trip failure
        # a field with a read path and no write path always is.
        spec[provisioning.WORKSPACE_KEY] = dict(workspace)
    if isinstance(runtime_hints, dict) and runtime_hints:
        spec["runtime_hints"] = dict(runtime_hints)
    if isinstance(defaults, dict) and defaults:
        spec["defaults"] = dict(defaults)
    if on_overlap:
        spec["on_overlap"] = str(on_overlap)
    # BEFORE `metadata` is coerced below: a presence flag inside it has to be restored while it is
    # still where the read put it, and `DefMetadata.from_dict` would drop an unknown key outright.
    if metadata:
        spec["metadata"] = dict(metadata)
    source = await service._reinject_source(based_on or name, version=based_on_version)
    spec = secrets.reinject_secrets(spec, source)
    # An agent reads a definition through the model boundary, masked, so a string it hands back
    # can carry a `[REDACTED: …]` marker where the definition holds a value: each keeps it.
    from personalclaw.security import MASK_CONFLICT, MaskConflict, keep_masked_values

    try:
        spec = keep_masked_values(spec, source)
    except MaskConflict:
        return None, service._service_failure(
            "WF_DEF_MASK_CONFLICT", MASK_CONFLICT, repromptable=True
        )
    hidden_lost = secrets.unmatched_flags(spec)
    if spec.get("metadata"):
        # Through `DefMetadata.from_dict` and back out, so the tolerant per-field coercion (unknown
        # `surface_mode` → `off`, negative `cadence_days` → 0) applies to the WRITE and not only to
        # the read. Coercing on read alone would store a value the next reader silently
        # reinterprets.
        spec["metadata"] = models.DefMetadata.from_dict(spec["metadata"]).to_dict()
    root = spec["root"] if isinstance(spec.get("root"), dict) else root

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
        return None, service._service_failure("WF_DEF_MACRO_INVALID", str(exc), repromptable=True)
    # The EXPANDED root is what gets written, so the stored spec, the validated spec and the
    # spec the engine runs are the same tree.
    expanded_root = spec.get("root")
    root = expanded_root if isinstance(expanded_root, dict) else root

    inline = secrets.find_inline_secrets(spec)
    if inline:
        # Refused, never merely warned: once saved, the value is on disk and every later
        # defence is damage control.
        return None, service._service_failure(
            "WF_DEF_INLINE_SECRET",
            "the spec contains literal credentials — use {{secret:KEY}} instead",
            findings=[f.to_dict() for f in inline],
        )

    result = validate_spec(spec, strict=strict)
    if hidden_lost:
        # Reported as validation issues, at the step, in the validator's own path grammar, so a
        # dry run shows them and a client pins them where the rest of a spec's problems go.
        result.issues.extend(
            service._hidden_value_issue(where, node_id, field, source=based_on or name)
            for where, node_id, field in hidden_lost
        )
        result.levels = []
    body = {
        "valid": result.ok,
        "issues": [i.to_dict() for i in result.issues],
        "levels": result.levels,
        # Conventions ADVICE, attached and never fatal. A user's own half-finished
        # workflow is theirs to leave rough, so the lint informs rather than refuses — but an
        # author who never sees it cannot follow a convention they were not told about. The
        # bundled library is held to lint-clean by test instead.
        "lint": template_lint.lint_template(spec).to_dict(),
    }
    if not result.ok:
        return None, service._service_failure(
            "WF_DEF_INVALID", "the spec did not validate", **body, repromptable=True
        )
    return spec, body
