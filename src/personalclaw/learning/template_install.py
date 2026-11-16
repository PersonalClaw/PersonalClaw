"""A proposed template installs: the ``template`` branch of :mod:`personalclaw.learning.installers`.

Three producers file a ``template`` proposal whose Accept should save a workflow: the after-turn
reviewer and the agent's save tool (both through ``learning.template_gate``), and trace mining's
recurring successful path (``learning.mining``). Nothing installed the kind before this branch, so
Accept answered 409 and the save tool's promise ("Accept it in the Learning review queue to create
the definition") was untrue.

The name it saves under is a single-valued tag the producers file, ``name:<slug>``
(:func:`template_name`). A row filed before the tag existed takes its name from what its producer
did file:
- the save tool's slug, as ``run_id``. The reviewer files the chat's key there, so a ``run_id``
  equal to the ``session_key`` is no name.
- else a ``Template: <slug>`` title.
With neither, it has no name, and nothing installs it.

The steps are the body's ``- `` or ``N. `` lines, in order, one prompt step each. They are built
through ``packs.prompt_cards.build_entity``, so a learned template and a pasted card's are checked
by one builder, and saved through the save every workflow goes through. The definition carries this
branch's own tag, not a card's.

A name a workflow already has is refused, and nothing is written. Accepting never replaces a
workflow you have. A plan shape (``run_end``'s repeated plan) names a pattern and has no steps, so
it stays one nothing installs, and says why.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

#: The single-valued tags a template proposal carries: the name it saves under, and the workflow a
#: mined path was mined from. ``proposals.enqueue`` keeps the first of each on a reinforce.
NAME_TAG = "name:"
WORKFLOW_TAG = "workflow:"
SINGLE_VALUED_TAGS = (NAME_TAG, WORKFLOW_TAG)

#: The tag a definition this branch saves carries, so the library can tell where it came from.
SPEC_TAG = "learned-template"

#: ``run_end``'s repeated plan shape: a pattern with no steps to save.
PLAN_SHAPE_TAG = "plan_similarity"

_STEP_RE = re.compile(r"^\s*(?:-|\d+\.)\s+(\S.*?)\s*$")
_TITLE_RE = re.compile(r"^\s*Template:\s*(\S+)\s*$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def tag_value(tags: Iterable[Any], prefix: str) -> str:
    """The value of the first tag in *tags* that starts with *prefix*, or ``""``."""
    for tag in tags or ():
        text = str(tag)
        if text.startswith(prefix) and text[len(prefix) :].strip():
            return text[len(prefix) :].strip()
    return ""


def keep_single_valued(kept: Iterable[Any], new: Iterable[Any]) -> list[str]:
    """*kept*'s tags joined with *new*'s, where a single-valued tag *kept* has is not given a
    second value: a reinforce never renames what was filed."""
    have = [str(t) for t in kept or ()]
    held = {p for p in SINGLE_VALUED_TAGS if any(t.startswith(p) for t in have)}
    added = [str(t) for t in new or () if not any(str(t).startswith(p) for p in held)]
    return sorted(set(have) | set(added))


def _slug(text: str) -> str:
    """*text* as a workflow name when it already is one (lowercase letters, digits, hyphens)."""
    value = str(text or "").strip().lower()
    return value if _SLUG_RE.match(value) else ""


def template_name(data: dict[str, Any]) -> str:
    """The name the template proposal *data* saves under, or ``""`` when it has none.

    Its ``name:`` tag; for a row filed before the tag, its ``run_id`` when that is not the chat's
    key (the save tool files the slug there), else a ``Template: <slug>`` title."""
    tagged = _slug(tag_value(data.get("tags") or [], NAME_TAG))
    if tagged:
        return tagged
    run_id = str(data.get("run_id") or "")
    if run_id and run_id != str(data.get("session_key") or ""):
        named = _slug(run_id)
        if named:
            return named
    match = _TITLE_RE.match(str(data.get("title") or ""))
    return _slug(match.group(1)) if match else ""


def template_steps(body: str) -> list[str]:
    """The steps of a template proposal's *body*: its ``- `` and ``N. `` lines, in order."""
    steps = []
    for line in str(body or "").splitlines():
        match = _STEP_RE.match(line)
        if match:
            steps.append(match.group(1))
    return steps


def is_template_proposal(data: dict[str, Any]) -> bool:
    """Whether *data* is a ``template`` proposal this branch is asked about (a pasted card's is
    claimed earlier, by its own tag)."""
    from personalclaw.learning.proposals import Kind

    return isinstance(data, dict) and str(data.get("kind") or "") == Kind.TEMPLATE.value


def why_not(data: dict[str, Any]) -> str:
    """Why the template proposal *data* can't be installed, or ``""`` when it can."""
    if PLAN_SHAPE_TAG in [str(t) for t in data.get("tags") or []]:
        return (
            "This names a repeated pattern. It has no steps to save, so it can't be accepted. "
            "Open the latest run to make a template from it, or reject this."
        )
    if not template_name(data):
        return (
            "This template has no name to save it under, so it can't be accepted. Reject it, "
            "and save the steps from the chat with a name."
        )
    if len(template_steps(str(data.get("body") or ""))) < 2:
        return "This template has fewer than two steps to save, so it can't be accepted."
    return ""


def install_accepted_template(data: dict[str, Any]) -> str:
    """Save the workflow the accepted template proposal *data* describes. Returns its name.

    Run by ``learning.proposals.accept`` after its human-only gate, so this is the owner
    installing. A name a workflow already has raises
    :class:`~personalclaw.learning.proposals.AcceptError` before anything is written."""
    from personalclaw.learning import proposals
    from personalclaw.packs import prompt_cards

    reason = why_not(data)
    if reason:
        raise proposals.NoProposalInstallerError(reason)
    name = template_name(data)
    if name in _workflow_names():
        raise proposals.AcceptError(
            f"A workflow named {name} already exists, so nothing was saved and the proposal "
            "still waits."
        )
    try:
        _target, spec, _payload = prompt_cards.build_entity(
            {
                "target": "template",
                "name": name,
                "description": str(data.get("title") or ""),
                "steps": [{"prompt": step} for step in template_steps(str(data.get("body") or ""))],
            }
        )
    except prompt_cards.PromptCardError as exc:
        raise proposals.AcceptError(_not_saved(name, exc)) from exc
    spec["tags"] = [SPEC_TAG]
    from personalclaw.workflows.versions import AGENT

    # Saved as the agent's: its steps are the agent's words, and an Accept here is no edit of
    # hers in the workflow's editor, the one door an automation follows a newer version from.
    return prompt_cards.save_definition(spec, saved_by=AGENT)


def _workflow_names() -> set[str]:
    """The names of the workflows this home has, lowercased."""
    import asyncio

    from personalclaw import memory_writes
    from personalclaw.workflows import service

    async def _go() -> dict[str, Any]:
        return await service.list_defs()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        listed = asyncio.run(_go())
    else:
        with memory_writes.ScopeCarryingExecutor() as pool:
            listed = pool.submit(asyncio.run, _go()).result()
    return {
        str(d.get("name") or "").strip().lower()
        for d in (listed or {}).get("defs") or []
        if isinstance(d, dict)
    }


#: The validator's code for a step holding something shaped like a credential.
_CREDENTIAL_CODE = "WF_INLINE_SECRET"


def _not_saved(name: str, exc: Exception) -> str:
    """Why the template *name* was not saved, in words: what happened, what to do, and that the
    proposal still waits. Never the value, and never the prompt-card path's own words ("the
    card"), which aren't this proposal's."""
    codes = tuple(getattr(exc, "codes", ()) or ())
    if _CREDENTIAL_CODE in codes:
        return (
            f"The template {name} was not saved: one of its steps holds something shaped like a "
            "credential. Reject it, or make the template yourself with the credential kept as a "
            "secret. It still waits."
        )
    if codes:
        return (
            f"The template {name} was not saved: its steps wouldn't make a template that runs "
            f"({', '.join(codes)}). It still waits."
        )
    return f"The template {name} was not saved: {str(exc).rstrip('.')}. It still waits."
