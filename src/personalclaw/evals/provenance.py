"""The ONE vocabulary for "this fact was never recorded" across the eval report.

Three sites in one report collapsed *absent* into *zero/none*, and each of them was on its way
to inventing its own word for the difference:

* a cell's token count (#2540) — a provider that omits its ``usage`` block made
  ``spend_from_home`` report ``tokens: 0`` while still reporting ``observed: true``, so the §4
  token denominator could not tell a cell that spent and did not say from one that spent nothing;
* the pin's model (#2561) — ``compute_pin`` records the INVOKING HOME's binding, and nothing
  recorded what the CELLS could reach, so a bound run and an all-cells-failed run carried the
  identical ``model_fp``;
* the report's own schema (#2562) — ``provider_binding`` was added without moving
  ``REPORT_SCHEMA``, so a missing key and a recorded ``null`` were the same bytes to a reader.

Three passes over one schema would have minted three words for one fact. This module is the one
word, and every site imports it from here.

**The word is "unrecorded".** Not ``priced``: that word already exists in ``loop_spend``,
``usage_ledger`` and ``run_totals`` and means precisely *"a cost is unknown"*. #2630 ruled it must
not be widened to cover token counts, because a row with a known cost and an unknown token count
would have to pick one value and either choice lies to one of its two readers. A cell whose
provider omitted ``usage`` is exactly that row: its ``dollars_est`` is a real (zero) estimate over
a real attempt, and its token count is absent. So this is a new word, not a widened one.

**The three states**, and why collapsing any two re-creates the defect:

``RECORDED``
    The fact is known and has a value. A digest, a token count, a binding object.
``RECORDED_NONE``
    The fact is known and its value is nothing: the cells could reach no model, the run bound no
    provider, the cell made no billable attempt. This is a MEASUREMENT.
``UNRECORDED``
    The fact was never recorded. This is not a measurement, and any arithmetic over it is not a
    weaker measurement — it is not a measurement.

**The representation rule.** A fact's STATE and its VALUE are read from different places, and
which place depends on the shape — spelled out here once so no consumer has to guess:

* a **scalar** (a token count) has only two states, because a recorded absence of spend IS ``0``.
  Its state comes from a boolean sibling spelled ``*_recorded``; ``None`` is the value it carries
  when that boolean is ``False``, and ``0`` is NEVER used for an absence. The sibling exists
  because some consumers cannot make the scalar nullable at all — ``Trial.tokens`` is an ``int``
  by construction;
* a **container** (a fingerprint mapping) carries all three states in its own shape, and
  :func:`state_of` reads them: key absent OR ``None`` ⇒ ``UNRECORDED``, an EMPTY mapping/sequence
  ⇒ ``RECORDED_NONE``, anything else ⇒ ``RECORDED``. ``None`` is an absence everywhere in this
  module — one meaning, so a reader never has to know which field it is looking at;
* a whole **field group** added in one schema version takes its state from the schema the artifact
  STATES (``learning_bench.provenance_recorded``), never from whether one of its keys is present.
  ``provider_binding`` is the field this matters most for, and the reason: ES-17 shipped it with
  ``null`` meaning *recorded, nothing was bound* — the one place ``null`` is a measurement rather
  than an absence. Rather than bend the rule above around it, its state is read from the schema,
  which is #2562's fix and is why :func:`state_of` is never asked about it;
* a ``results.tsv`` **digest cell** — where every value is a string and an empty cell already
  means "the column did not exist when this row was written" — spells the words
  :data:`UNRECORDED` and :data:`NO_MODEL` literally. Neither can be mistaken for the 12-hex
  digest that means ``RECORDED``.

So :func:`state_of` is asked about the key that RECORDS a fact — a ``*_recorded`` flag, a
``report_schema``, a container — and never about a nullable scalar, whose ``None`` means
"unrecorded" rather than "recorded as nothing".
"""

from __future__ import annotations

from typing import Any, Mapping

#: The one word. Spelled the same in Python, in TSV cells, in report fields and in
#: ``web/src/lib/unrecorded.ts`` — ``tests/test_evals_unrecorded_vocabulary.py`` reds on a second
#: spelling.
UNRECORDED = "unrecorded"

#: A ``results.tsv`` digest cell whose subject is recorded and is *no model at all* — the cells of
#: this run could reach none, so they resolved the offline ``scripted`` replay. A separate word
#: from :data:`UNRECORDED` because "we know none" and "we never looked" are the two claims this
#: whole module exists to keep apart, and an empty TSV cell already means a third thing (the
#: column did not exist when the row was written).
NO_MODEL = "no_model"

#: The three states a provenance/spend fact can be in.
RECORDED = "recorded"
RECORDED_NONE = "recorded_none"

#: Closed. A fourth member would be a fourth reading of the same absence.
STATES: tuple[str, ...] = (RECORDED, RECORDED_NONE, UNRECORDED)


def state_of(container: Mapping[str, Any] | None, key: str) -> str:
    """Which of :data:`STATES` ``container[key]`` is in.

    Key absent, no container at all, or present and ``None`` ⇒ :data:`UNRECORDED`. Present and an
    EMPTY mapping/sequence ⇒ :data:`RECORDED_NONE`. Anything else ⇒ :data:`RECORDED`.

    ``None`` is an absence here and everywhere else in this module — one meaning for one spelling,
    so a caller never has to know which field it is looking at. ``0`` is deliberately
    :data:`RECORDED`: a recorded zero is a measurement, and that assertion is the one this whole
    family turns on.

    Do NOT ask this about ``provider_binding``: ES-17 shipped that field with ``null`` meaning
    *recorded, nothing was bound*, which is the single field where ``null`` is a measurement. Its
    state comes from ``learning_bench.provenance_recorded`` — the schema the report states — which
    is #2562's fix.
    """
    if container is None or key not in container:
        return UNRECORDED
    value = container[key]
    if value is None:
        return UNRECORDED
    if isinstance(value, (Mapping, list, tuple, set)) and not value:
        return RECORDED_NONE
    return RECORDED


def is_unrecorded(container: Mapping[str, Any] | None, key: str) -> bool:
    """``True`` when ``container[key]`` was never recorded — the guard every consumer owes.

    Read this before any arithmetic on the value. A consumer that skips it publishes an absence
    as a measured number, which is the same defect at a new site.
    """
    return state_of(container, key) == UNRECORDED


def digest_cell(digest: str | None, *, recorded: bool) -> str:
    """One ``results.tsv`` digest cell, in this module's vocabulary.

    ``recorded=False`` ⇒ :data:`UNRECORDED`. Recorded with no digest ⇒ :data:`NO_MODEL`.
    Otherwise the digest itself. Callers never hand-spell either word, so the ledger cannot
    acquire a second spelling of an absence.
    """
    if not recorded:
        return UNRECORDED
    return digest or NO_MODEL


__all__ = [
    "NO_MODEL",
    "RECORDED",
    "RECORDED_NONE",
    "STATES",
    "UNRECORDED",
    "digest_cell",
    "is_unrecorded",
    "state_of",
]
