"""What became of each step of an item's ingestion: one record, read by every surface.

Every step the ingest reaches — a graph node or a terminal stage — ends with a
:class:`PhaseOutcome`: its ``status``, the ``reason`` in plain words, and the ``fix`` (what the
owner can do, each with where in the app to do it). The runner persists the map as
``file_metadata.node_phases``; the item page draws it, and the item-graph read adds whether a
skipped step could run now (``ready``).

Four statuses, kept apart because each asks something different of the reader:

* ``done`` — the step ran and did its work.
* ``failed`` — it ran and went wrong; ``reason`` is the error.
* ``skipped`` — it could not run for want of something the owner can add: a model, an engine.
  ``fix`` says what, and ``needs`` names the capability (a use case, or :data:`OCR_ENGINE`) so a
  later read can tell whether it is there now. A step that waited on a skipped or failed one is
  ``skipped`` too, and carries what that one was missing.
* ``not_applicable`` — the step does not apply to this item: a branch the graph did not take
  (a PDF with a text layer needs no OCR), or nothing to do (no intents are defined). No fix.

The sentences here are product copy, so each says only what is true of the run that wrote it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"
NOT_APPLICABLE = "not_applicable"

#: The capability an installed OCR app adds: an engine the ``ocr`` step can run on with no model.
OCR_ENGINE = "ocr_engine"

#: Where a model is chosen for a use case.
MODELS_HREF = "#/settings/models"
#: The Store, filtered to the apps that add an OCR engine (they carry the ``ocr`` tag).
OCR_APPS_HREF = "#/apps?view=store&stag=ocr"

#: What an item processed before steps recorded their outcomes says of a step it skipped.
SKIPPED_BEFORE_REASONS = "It was skipped before steps recorded why."

#: Step names that are not their node type spelled out.
_STEP_NAMES = {"ocr": "OCR", "exif": "EXIF"}


@dataclass(frozen=True)
class Fix:
    """One thing the owner can do so a skipped step runs: the words, and the in-app route."""

    text: str
    href: str = ""
    #: The use case a "choose a model" fix is for, so several can be said as one. Not persisted.
    use_case: str = field(default="", compare=False)

    def to_dict(self) -> dict[str, str]:
        return {"text": self.text, "href": self.href} if self.href else {"text": self.text}


@dataclass(frozen=True)
class PhaseOutcome:
    """What became of one step. See the module docstring for the four statuses."""

    status: str
    reason: str = ""
    fix: tuple[Fix, ...] = ()
    needs: tuple[str, ...] = ()
    #: The sentences saying what was missing at the ROOT of a skip, kept so a step that waited
    #: on this one can say it too. Not persisted: a step's own reason already carries them.
    causes: tuple[str, ...] = field(default=(), compare=False)

    def to_dict(self) -> dict:
        out: dict = {"status": self.status}
        if self.reason:
            out["reason"] = self.reason
        if self.fix:
            out["fix"] = [f.to_dict() for f in self.fix]
        if self.needs:
            out["needs"] = list(self.needs)
        return out


def done() -> PhaseOutcome:
    return PhaseOutcome(DONE)


def failed(reason: str) -> PhaseOutcome:
    return PhaseOutcome(FAILED, _sentence(reason))


def not_applicable(reason: str) -> PhaseOutcome:
    return PhaseOutcome(NOT_APPLICABLE, _sentence(reason))


def skipped(reason: str, fix: tuple[Fix, ...] = (), needs: tuple[str, ...] = ()) -> PhaseOutcome:
    said = _sentence(reason)
    return PhaseOutcome(SKIPPED, said, fix, needs, causes=(said,) if needs else ())


def use_case_name(use_case: str) -> str:
    """What the Models page calls *use_case*: the row the owner binds a model in."""
    from personalclaw.providers.use_cases import USE_CASE_NAMES

    return USE_CASE_NAMES.get(use_case, use_case)


def model_fix(use_case: str) -> Fix:
    return _models_fix([use_case])


def _models_fix(use_cases: list[str]) -> Fix:
    names = _one_of([use_case_name(uc) for uc in use_cases])
    return Fix(
        f"Choose a model for {names} in Settings → Models",
        MODELS_HREF,
        use_case=",".join(use_cases),
    )


def ocr_engine_fix() -> Fix:
    return Fix("Install an OCR app from Apps", OCR_APPS_HREF)


def no_model(use_case: str, reason: str) -> PhaseOutcome:
    """A model-backed step that no model serves: *reason* is the probe's sentence."""
    if not use_case:
        return skipped(reason)
    return skipped(reason, (model_fix(use_case),), (use_case,))


def no_ocr_engine() -> PhaseOutcome:
    return skipped("No OCR engine is installed.", (ocr_engine_fix(),), (OCR_ENGINE,))


def either(outcomes: list[PhaseOutcome]) -> PhaseOutcome:
    """One skip for a step any of whose alternative backends would have run: every one's
    reason, every one's fix, and any one's need."""
    if len(outcomes) == 1:
        return outcomes[0]
    causes = _unique(c for o in outcomes for c in (o.causes or (o.reason,)))
    return PhaseOutcome(
        SKIPPED,
        " ".join(causes),
        _merged(f for o in outcomes for f in o.fix),
        _unique(n for o in outcomes for n in o.needs),
        causes=causes,
    )


def waited_on(upstream: list[tuple[str, PhaseOutcome]]) -> PhaseOutcome:
    """The outcome of a step none of whose inputs arrived, from what became of them.

    *upstream* is each step it needs, as ``(name, outcome)``. When every one of them was not
    needed, neither is this one. Otherwise it is skipped, says which it waited on and why that
    one did not run, and carries its fix: whatever lets that one run lets this one run.
    """
    names = [name for name, _ in upstream]
    if all(o.status == NOT_APPLICABLE for _, o in upstream):
        return not_applicable(
            f"It needs {_one_of(names)} first, which "
            + ("wasn't needed here." if len(names) == 1 else "weren't needed here.")
        )
    causes = _unique(c for _, o in upstream for c in o.causes)
    any_failed = any(o.status == FAILED for _, o in upstream)
    if len(names) == 1:
        head = f"It needs {names[0]} first, which " + ("failed" if any_failed else "was skipped")
    else:
        head = f"It needs {_one_of(names)} first, and " + (
            "neither ran" if len(names) == 2 else "none of them ran"
        )
    reason = f"{head} because {_lower_first(' '.join(causes))}" if causes else f"{head}."
    return PhaseOutcome(
        SKIPPED,
        _sentence(reason),
        _merged(f for _, o in upstream for f in o.fix),
        _unique(n for _, o in upstream for n in o.needs),
        causes=causes,
    )


def branch_not_taken(upstream: str) -> PhaseOutcome:
    """A step behind a conditional edge whose source chose another way for this item."""
    return not_applicable(f"{upstream} decided this item doesn't need it.")


def step_name(node_type: str) -> str:
    """A step as the item page names it: ``video_classify`` → "Video classify"."""
    if node_type in _STEP_NAMES:
        return _STEP_NAMES[node_type]
    spoken = node_type.replace("_", " ")
    return spoken[:1].upper() + spoken[1:]


async def capability_ready(need: str) -> bool:
    """Whether *need* — a use case, or :data:`OCR_ENGINE` — is there now, asked of the same
    probes the executor asks before it runs a step, so "ready" never promises a run that
    would skip again.

    Image · Modality is read from its binding alone — the one its fix names — and not from the
    image reader, whose chat-model fallback can list a provider's catalog: this runs on every
    read of an item's steps, and a listing there could hold the page for seconds. A bound
    model that resolves is exactly what the reader serves first, so the answer can only be
    more cautious than the run, never more hopeful."""
    try:
        if need == OCR_ENGINE:
            from personalclaw.ocr.registry import ocr_available

            return bool(ocr_available())
        from personalclaw.providers.image_input import IMAGE_USE_CASE

        if need == IMAGE_USE_CASE:
            from personalclaw.providers.provider_bridge import can_resolve_use_case
            from personalclaw.providers.use_cases import active_model_refs

            return bool(active_model_refs(need)) and can_resolve_use_case(need)
        from personalclaw.knowledge.pipeline.registry import unserved_reason_sync

        return not unserved_reason_sync(need)
    except Exception:  # noqa: BLE001 — a probe fault reads as "not there", never as ready
        return False


def told(node_phases: object) -> list[str]:
    """Each step of a persisted outcome map that was skipped or failed, as one line in words:
    "Vision skipped: No image model is set up. Fix: Choose a model for …". For a surface
    that reads the item as text (Investigate), where the item page draws the same map."""
    if not isinstance(node_phases, dict):
        return []
    lines = []
    for step, outcome in node_phases.items():
        if not isinstance(outcome, dict) or outcome.get("status") not in (SKIPPED, FAILED):
            continue
        line = f"{step_name(str(step))} {outcome['status']}"
        if outcome.get("reason"):
            line += f": {outcome['reason']}"
        fixes = [f.get("text") for f in outcome.get("fix") or [] if isinstance(f, dict)]
        if fixes := [f for f in fixes if f]:
            line += f" Fix: {'; or '.join(fixes)}."
        lines.append(line)
    return lines


def legacy(status: str) -> dict:
    """The outcome of a step recorded before steps recorded outcomes (a bare status word)."""
    if status == SKIPPED:
        return skipped(SKIPPED_BEFORE_REASONS).to_dict()
    return {"status": status}


def _sentence(text: str) -> str:
    """*text* as a sentence: trimmed, its first letter raised, ending in a full stop."""
    said = " ".join((text or "").split())
    if not said:
        return ""
    said = said[:1].upper() + said[1:]
    return said if said[-1] in ".!?" else f"{said}."


def _lower_first(text: str) -> str:
    """A sentence continued after "because": its first letter lowered, unless it opens with a
    name or an initialism ("OCR", "Image · Modality")."""
    first = text.split(" ", 1)[0]
    if len(first) > 1 and first[1:2].isupper():
        return text
    return text[:1].lower() + text[1:]


def _one_of(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} or {names[-1]}"


def _merged(fixes) -> tuple[Fix, ...]:
    """*fixes* without repeats, every "choose a model" fix said as one: "Choose a model for
    Speech-to-text or Image · Modality in Settings → Models". A step runs when ANY step before it
    did, so any one of those models is enough — "or" is the true word. Kept where the first of
    them stood."""
    fixes = _unique(fixes)
    use_cases = _unique(uc for f in fixes if f.use_case for uc in f.use_case.split(","))
    if len(use_cases) < 2:
        return fixes
    merged: list[Fix] = []
    for f in fixes:
        if not f.use_case:
            merged.append(f)
        elif not any(m.use_case for m in merged):
            merged.append(_models_fix(list(use_cases)))
    return tuple(merged)


def _unique(items) -> tuple:
    seen: list = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return tuple(seen)
