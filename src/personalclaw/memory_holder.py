"""Holder attribution — the optional "whose claim is this?" axis on semantic rows.

MEMORY-GRAPH-AND-VAULT §4.2 (MGAV-5). A plain semantic row asserts a fact about the
world. A *claim* asserts that somebody said something, and the two must not be stored
the same way: "the migration ships Friday" and "Alex says the migration ships Friday"
have different truth conditions, and collapsing them is how a memory system starts
confidently repeating one person's guess as established fact.

Deliberately an AXIS on existing rows, not a claims subsystem:

* ``holder`` — ``""`` (plain fact, the default and today's behavior), ``user``,
  ``assistant``, ``person:<entity_id>``, or ``external``.
* ``weight`` — a coarse 0.05-quantized strength, capped by holder class. There is no
  ``kind`` column: kind inference stays key-prefix based (``claim.*``), which is the
  recon invariant the rest of the memory system already relies on.

Two rules carry the safety here:

1. **A plain fact is never re-weighted.** ``holder=""`` keeps its full weight, so
   introducing this axis cannot silently down-rank every record written before it.
2. **The caps are a ceiling, not a rejection.** A self-report claiming certainty 0.95
   is clamped to its class ceiling and kept — dropping the write instead would lose a
   memory the user gave us because a model over-claimed about it.
"""

from __future__ import annotations

from typing import Iterable

#: The ``claim.*`` key prefix that marks an explicit take. Kind inference is
#: key-prefix based (no kind column), so this string is the discriminator.
CLAIM_PREFIX = "claim."

#: Holder classes. ``""`` is a first-class member: it means "plain fact", which is what
#: every row written before this axis existed genuinely is.
HOLDER_NONE = ""
HOLDER_USER = "user"
HOLDER_ASSISTANT = "assistant"
HOLDER_EXTERNAL = "external"
#: A third party's claim. Carries the graph entity id: ``person:ent_abc123``.
HOLDER_PERSON_PREFIX = "person:"

HOLDERS = (HOLDER_NONE, HOLDER_USER, HOLDER_ASSISTANT, HOLDER_EXTERNAL)

#: Weight quantum — 0.05 increments, because a 0.05 grid is already finer than anyone
#: can justify and a 0.01 grid invites false precision the source never had.
WEIGHT_QUANTUM = 0.05

#: Ceiling for a SELF-reported claim (the holder speaking for themselves).
SELF_REPORT_CAP = 0.75
#: Ceiling for a secondhand / amplified claim.
SECONDHAND_CAP = 0.55

#: Precedence for adjudicating contradictions (§4.2: "user statement > compiled
#: synthesis > external"). A plain fact sits at the compiled-synthesis level, which is
#: exactly what it is: the store's own distillation.
_PRECEDENCE = {
    HOLDER_USER: 3,
    HOLDER_NONE: 2,
    HOLDER_ASSISTANT: 2,
    HOLDER_EXTERNAL: 1,
}
#: A named third party outranks an anonymous external source but not the user or the
#: store's own synthesis — someone else's belief is evidence, not a correction.
_PERSON_PRECEDENCE = 1


def is_person(holder: str) -> bool:
    """Whether ``holder`` names a third party (``person:<entity_id>``)."""
    return bool(holder) and holder.startswith(HOLDER_PERSON_PREFIX)


def person_entity_id(holder: str) -> str:
    """The entity id inside a ``person:<entity_id>`` holder, or ""."""
    return holder[len(HOLDER_PERSON_PREFIX) :] if is_person(holder) else ""


def normalize_holder(holder: object) -> str:
    """Coerce any input to a known holder string, defaulting to plain fact.

    Unknown values fall back to ``""`` rather than raising: holder arrives from a model
    verdict and a hand-edited config, and an unrecognised label must degrade to "plain
    fact" (today's behavior) instead of costing the write.
    """
    if not isinstance(holder, str):
        return HOLDER_NONE
    value = holder.strip().lower()
    if not value:
        return HOLDER_NONE
    if value in HOLDERS:
        return value
    if value.startswith(HOLDER_PERSON_PREFIX) and value[len(HOLDER_PERSON_PREFIX) :].strip():
        return f"{HOLDER_PERSON_PREFIX}{value[len(HOLDER_PERSON_PREFIX):].strip()}"
    return HOLDER_NONE


def weight_cap(holder: str) -> float:
    """The ceiling on ``weight`` for this holder class.

    A plain fact is uncapped (1.0) — see the module docstring's rule 1.
    """
    holder = normalize_holder(holder)
    if holder == HOLDER_NONE:
        return 1.0
    if holder == HOLDER_EXTERNAL:
        return SECONDHAND_CAP
    return SELF_REPORT_CAP


def normalize_weight(holder: str, weight: object) -> float:
    """Quantize ``weight`` to the 0.05 grid and clamp it to ``holder``'s ceiling.

    Clamping rather than rejecting is deliberate (module docstring, rule 2). The
    quantization happens BEFORE the clamp so the returned value is always on the grid.
    """
    try:
        raw = float(weight)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raw = weight_cap(holder)
    if raw != raw or raw in (float("inf"), float("-inf")):  # NaN / inf
        raw = weight_cap(holder)
    raw = max(0.0, min(1.0, raw))
    quantized = round(round(raw / WEIGHT_QUANTUM) * WEIGHT_QUANTUM, 2)
    return min(quantized, weight_cap(holder))


def precedence(holder: object) -> int:
    """How much authority a holder class carries when two rows contradict.

    Higher wins. Used at the DECIDE point (§4.1), not at read time: a lower-precedence
    claim must not be able to supersede a higher-precedence one at all, which is a
    different guarantee from merely ranking below it in recall.
    """
    holder = normalize_holder(holder)
    if is_person(holder):
        return _PERSON_PRECEDENCE
    return _PRECEDENCE.get(holder, _PRECEDENCE[HOLDER_NONE])


def attribution(holder: object, *, entity_name: str = "") -> str:
    """The short human phrase the injected fact block prefixes a claim with, or "".

    "" for a plain fact, so an unattributed row renders exactly as it always has.
    """
    holder = normalize_holder(holder)
    if holder == HOLDER_NONE:
        return ""
    if holder == HOLDER_USER:
        return "you say"
    if holder == HOLDER_ASSISTANT:
        return "I concluded"
    if holder == HOLDER_EXTERNAL:
        return "reported externally"
    name = entity_name.strip() or person_entity_id(holder)
    return f"{name} believes"


def render_fact_line(
    key: str,
    value_str: str,
    *,
    holder: object = "",
    weight: object = None,
    entity_name: str = "",
) -> str:
    """One line of the injected fact block, attributed when the row carries a holder.

    A plain fact renders byte-identically to the pre-MGAV-5 format (``key: value``).
    An attributed claim renders the attribution AND the weight, because an attribution
    without a strength reads as endorsement — "Alex believes X" and "Alex believes X
    (0.35)" should not look the same to the model.
    """
    holder = normalize_holder(holder)
    base = f"{key}: {value_str}"
    if holder == HOLDER_NONE:
        return base
    phrase = attribution(holder, entity_name=entity_name)
    w = normalize_weight(holder, weight if weight is not None else weight_cap(holder))
    return f"{base} [{phrase}, weight {w:.2f}]"


def entity_names_for(holders: Iterable[str], graph: object) -> dict[str, str]:
    """Map ``person:<id>`` holders to entity display names via ``graph``.

    Returns ``{holder: name}``, skipping ids the graph does not know — an unresolvable
    id renders as the raw id rather than inventing a name for it.
    """
    wanted = {h for h in holders if is_person(h)}
    if not wanted:
        return {}
    out: dict[str, str] = {}
    try:
        entities = graph.entities()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — attribution must never cost a turn
        return {}
    by_id = {e.id: e.name for e in entities}
    for holder in wanted:
        name = by_id.get(person_entity_id(holder))
        if name:
            out[holder] = name
    return out
