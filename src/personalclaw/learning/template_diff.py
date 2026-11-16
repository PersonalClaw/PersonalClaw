"""An accepted template change: applied before its decision is recorded.

The refiner files typed ops against a workflow and never applies them; accepting is the owner
installing, so the diff lands here as the workflow's next version. It used to be applied AFTER the
accept was recorded, so a diff that could not be applied (a definition gone, an op that no longer
fits, a step it would let do more) was recorded as accepted all the same, and its re-file was then
suppressed as "already accepted". It runs first now, as every other installer does
(``learning.installers``): a failed apply raises
:class:`~personalclaw.learning.proposals.NotAppliedError`, nothing is recorded, and the row stays
pending, with the reason the route answers as 409 ``learning_proposal_not_applied``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


async def apply_accepted(prop: Any) -> dict[str, Any]:
    """Apply an accepted ``template_diff`` to its target and save it as a NEW version.

    The refiner FILES typed ops and never applies them; accepting is the human installing, so
    THIS is where the diff lands, as a new template VERSION. The ops ride the change
    manifest's ``targeted_fix`` (the same field the inbox reads to stamp a risk tier); they are
    applied to a deep copy via ``mutations.apply_batch`` and, only if the batch is clean, saved
    as a new version (``service.save_accepted_diff``), which refuses a diff that would let a step
    do more than before: accepting a model's proposal is no yes to that. Best-effort: the
    proposal is not accepted until this answers ``applied``: :func:`install_accepted_diff` runs it
    before the decision is recorded, so a failed apply leaves the row pending and says why.
    """
    manifest = getattr(prop, "change_manifest", None)
    ops_raw = manifest.get("targeted_fix") if isinstance(manifest, dict) else None
    name = str(getattr(prop, "target", "") or "")
    if not name or not isinstance(ops_raw, list) or not ops_raw:
        return {"applied": False, "reason": "no typed ops on the proposal"}

    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows import mutations, service

    spec = None
    for pname in defs_mod.list_providers():
        provider = defs_mod.get_provider(pname)
        if provider is None:
            continue
        try:
            found = await provider.get_def(name)
        except Exception:
            continue
        if found is not None:
            spec = found if isinstance(found, dict) else getattr(found, "to_dict", lambda: None)()
            break
    if not isinstance(spec, dict):
        return {"applied": False, "reason": f"no definition named {name!r}"}

    try:
        ops = [mutations.Op.from_dict(o) for o in ops_raw if isinstance(o, dict)]
    except ValueError as exc:
        return {"applied": False, "reason": f"unparseable op: {exc}"}
    candidate, issues = mutations.apply_batch(ops, spec, {})
    if issues:
        return {"applied": False, "reason": "; ".join(i.code for i in issues)}

    saved = await service.save_accepted_diff(candidate, ops=ops_raw)
    if not saved.get("ok"):
        return {"applied": False, "reason": str(saved.get("message") or "it was not saved")}
    return {"applied": True, "version": int(saved.get("version") or 0)}


def install_accepted_diff(data: dict[str, Any]) -> int:
    """Apply the accepted template change *data* from the SYNC installer. Returns the version it
    saved. Raises :class:`~personalclaw.learning.proposals.NotAppliedError` when it was not
    applied, with the reason."""
    import asyncio

    from personalclaw import memory_writes
    from personalclaw.learning import proposals

    prop = SimpleNamespace(
        target=str(data.get("target") or ""), change_manifest=data.get("change_manifest") or {}
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        result = asyncio.run(apply_accepted(prop))
    else:
        with memory_writes.ScopeCarryingExecutor() as pool:
            result = pool.submit(asyncio.run, apply_accepted(prop)).result()
    if not result.get("applied"):
        name = prop.target or "the workflow"
        reason = str(result.get("reason") or "it was not saved")
        raise proposals.NotAppliedError(
            f"The change to {name} was not applied ({reason}). Nothing changed, and it is still "
            "pending."
        )
    return int(result.get("version") or 0)
