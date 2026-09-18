"""One phase-key vocabulary: `phase_status` is written and read under the SAME field name.

`phase_key` is the projection key for a plan phase — the backend writes `phase_status` (and
`task_list_ids`) under it, and the frontend's run fold looks a phase's rendered state up by it.
It used to be four hand-written copies saying three different things — goal/general `title`,
code `stage`-or-title, design `step`-or-title — with nothing asserting that any reader agreed
with any writer. Nothing did:

    design's writer keyed `phase_status` by `step` ('foundations', 'palette', …) while
    `web/src/pages/loops/runFold.ts` keyed its lookup by `stage`, a field NO design plan row
    carried. So every lookup missed, EVERY design stage rendered `todo` forever, and the header
    counter — which counts phase_status VALUES rather than looking keys up — read "3/5 stages"
    over five empty circles (issue 494; measured on the canonical five-phase plan, and the
    issue's own live loop had a six-row planner-generated one).

The fix was one name, not a fallback that reads both: `stage` is the phase id every planner
already emits, so design's rows carry `stage` too and there is exactly ONE `phase_key`. These
rails keep it that way. Two matter most:

* `test_no_kind_overrides_phase_key` is parametrized over the REGISTRY, so a new kind that
  reintroduces its own spelling turns red here rather than in a user's frozen progress strip.
* `test_a_design_runs_written_keys_are_the_keys_a_reader_looks_up` drives the real writer
  through the real store and asserts the written keys are exactly what the shared vocabulary
  resolves to — the paired fact the two sides used to disagree about.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from personalclaw.loop import kinds, store
from personalclaw.loop.kinds import PHASE_KEY_FIELDS, LoopKindStrategy
from personalclaw.loop.loop import Loop

#: The frontend's copy of the vocabulary. Read from source (not duplicated here) so the
#: comparison is DERIVED from the file that actually drives the render.
_FE_PHASE_KEYS = Path(__file__).resolve().parents[1] / "web/src/pages/loops/loopPhases.ts"

#: The spelling design used to key `phase_status` by, and which no reader outside the design
#: kind knew. Named so a reader can see WHICH word is retired, and asserted dead below.
_RETIRED_DIALECT = "step"


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    """Every rail here that touches the store writes under tmp_path, never the real home."""
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    return tmp_path


def _kinds() -> list[str]:
    kinds.ensure_loaded()
    return sorted(kinds.registered_kinds())


# ── one owner ─────────────────────────────────────────────────────────────────


def test_the_registry_is_not_empty():
    """Vacuity floor. Every parametrized rail below draws its cases from the registry, so an
    empty registry would make the whole file pass by testing nothing."""
    assert len(_kinds()) >= 3, f"suspiciously few loop kinds registered: {_kinds()}"


@pytest.mark.parametrize("kind", _kinds())
def test_no_kind_overrides_phase_key(kind):
    """THE structural rail, derived from the registry rather than a hand-kept list of kinds.

    A kind that needs a different phase id changes its PLANNER to emit `stage`; it does not
    get its own key function. Four copies is how the writer and the reader came to disagree,
    and the type system cannot catch it because every copy returns `str`.
    """
    strategy = kinds.get(kind)
    own = type(strategy).phase_key
    assert own is LoopKindStrategy.phase_key, (
        f"{type(strategy).__name__} overrides phase_key. There is one implementation, on "
        "LoopKindStrategy, reading kinds.PHASE_KEY_FIELDS — a kind that needs a different "
        "phase id emits `stage` from its planner instead."
    )


@pytest.mark.parametrize("kind", _kinds())
def test_every_kind_keys_a_row_by_the_declared_fields(kind):
    """Behaviour, not identity: a kind could inherit the one implementation and still be fed
    rows it cannot key. For each declared field IN ISOLATION the key is that field's value,
    and priority follows the declared order."""
    strategy = kinds.get(kind)
    for field in PHASE_KEY_FIELDS:
        assert (
            strategy.phase_key({field: "resolved"}) == "resolved"
        ), f"{kind} cannot key a row carrying only {field!r}, which PHASE_KEY_FIELDS declares"
    priority = {f: f"v-{f}" for f in PHASE_KEY_FIELDS}
    assert (
        strategy.phase_key(priority) == f"v-{PHASE_KEY_FIELDS[0]}"
    ), f"{kind} does not honour the declared field priority {PHASE_KEY_FIELDS}"


@pytest.mark.parametrize("kind", _kinds())
def test_the_retired_dialect_stays_dead(kind):
    """A row carrying ONLY the old spelling must key to nothing.

    This is the assertion that forbids the tempting "read either key" fix. Accepting both
    spellings would keep this file green while leaving two words in circulation for the next
    surface to pick the wrong one — which is exactly how 494 happened.
    """
    assert _RETIRED_DIALECT not in PHASE_KEY_FIELDS, (
        f"{_RETIRED_DIALECT!r} is back in the vocabulary — the point of the fix was ONE name, "
        "not a reader that accepts both"
    )
    assert kinds.get(kind).phase_key({_RETIRED_DIALECT: "foundations"}) == ""


def test_an_empty_leading_field_falls_through_instead_of_keying_on_empty():
    """A titled-but-stageless row deliberately carries an EMPTY-string `stage`. Coalescing on
    null (`stage ?? title`) keeps that '' as the key → an unconditional `phase_status` miss →
    the stage sticks on 'todo'. Trimming is load-bearing, so it is asserted, not assumed."""
    first, second = PHASE_KEY_FIELDS[0], PHASE_KEY_FIELDS[1]
    strategy = kinds.get(_kinds()[0])
    assert strategy.phase_key({first: "", second: "Kickoff"}) == "Kickoff"
    assert strategy.phase_key({first: "   ", second: "Kickoff"}) == "Kickoff"
    assert strategy.phase_key({first: "  build  "}) == "build", "the key must be trimmed"
    assert strategy.phase_key({}) == ""


# ── the two sides of the wire declare the same field list ─────────────────────


def test_the_frontend_reads_the_same_field_list_in_the_same_order():
    """The cross-wire rail. The frontend cannot import this tuple, so it keeps its own literal
    — and a literal on the far side of a wire is precisely what drifted. Renaming the field on
    EITHER side now reds here instead of silently freezing a progress strip.

    Read out of source deliberately: the assertion is about what the shipped reader says, so a
    duplicated copy in this test would defeat the purpose.
    """
    src = _FE_PHASE_KEYS.read_text()
    m = re.search(r"export const PHASE_KEY_FIELDS = (\[[^\]]*\]) as const", src)
    assert m, (
        f"{_FE_PHASE_KEYS.name} no longer exports a PHASE_KEY_FIELDS literal. The frontend's "
        "phase-status lookup is keyed off it; if it moved, move this rail with it rather than "
        "deleting the only check that the two sides agree."
    )
    fe = tuple(json.loads(m.group(1).replace("'", '"')))
    assert fe == tuple(PHASE_KEY_FIELDS), (
        "the frontend and backend phase-key vocabularies have diverged — "
        f"frontend {fe}, backend {tuple(PHASE_KEY_FIELDS)}. This is the 494 defect exactly: "
        "the writer keys phase_status by one word and the render looks it up by another."
    )


# ── every kind's own canonical rows are keyable ───────────────────────────────


@pytest.mark.parametrize("kind", [k for k in _kinds() if hasattr(kinds.get(k), "default_phases")])
def test_a_kinds_own_canonical_rows_all_produce_a_key(kind):
    """Derived from the PRODUCER's shape: whatever rows the kind itself calls canonical must be
    keyable by the shared vocabulary. A planner that renames its id field reds here."""
    strategy = kinds.get(kind)
    rows = strategy.default_phases()
    assert rows, f"vacuity floor: {kind}.default_phases() produced no rows to check"
    for i, row in enumerate(rows):
        assert strategy.phase_key(row) != "", (
            f"{kind}.default_phases()[{i}] carries none of {PHASE_KEY_FIELDS} "
            f"(keys: {sorted(row)}) — nothing can look its phase_status up"
        )
        assert (
            _RETIRED_DIALECT not in row
        ), f"{kind}.default_phases()[{i}] still carries the retired {_RETIRED_DIALECT!r} field"


# ── the behaviour the vocabulary exists to preserve ───────────────────────────


class _Ctx:
    """The watchdog capabilities `on_new_cycle` uses, recorded rather than performed."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.completed: str | None = None

    def publish(self, loop_id: str, event: str, data: dict) -> None:
        self.events.append((event, data))

    async def complete(self, loop_id: str, reason: str) -> None:
        self.completed = reason


def test_a_design_runs_written_keys_are_the_keys_a_reader_looks_up():
    """THE load-bearing rail — driven through the real store and the real writer, not the sets.

    `DesignKind.on_new_cycle` is what actually populates `phase_status`. Every key it writes
    must be one the shared vocabulary resolves a plan row to, because that resolution IS the
    frontend's lookup. Re-spell either side and this goes red.

    The second assertion is the 494 symptom stated as one paired fact: the header counts
    phase_status VALUES while the rows look keys UP, so a key mismatch makes the two disagree
    with each other on the same screen. Requiring them equal forbids that state.
    """
    kinds.ensure_loaded()
    design = kinds.get("design")
    plan = design.default_phases()
    loop = store.create(
        Loop(id="", name="D", kind="design", task="build a warm design system", plan=plan)
    )

    # Four cycles, the worker reporting it is on step 4 of 5 — enough to leave three phases
    # `done` and one `active`, which is the state that used to render as five empty circles.
    findings = [{"cycle": i + 1, "step": str(i + 1)} for i in range(4)]
    ctx = _Ctx()
    completed = asyncio.run(design.on_new_cycle(store.get(loop.id), findings, ctx))
    assert not completed, "precondition: reporting step 4 of 5 must not finish the loop"

    written = store.get(loop.id).phase_status or {}
    assert written, "precondition: the writer must have populated phase_status at all"

    # Resolve the rows the way an INDEPENDENT reader does — straight off the declared field
    # vocabulary, NOT by calling `phase_key` again. Re-using the writer's own function here
    # would make this a tautology that no key mismatch could ever fail; the frontend does not
    # have that function, it has the field list, and that asymmetry IS the defect.
    def independent(row: dict) -> str:
        for field in PHASE_KEY_FIELDS:
            value = str(row.get(field, "") or "").strip()
            if value:
                return value
        return ""

    resolvable = {independent(row) for row in plan} - {""}
    orphans = set(written) - resolvable
    assert not orphans, (
        f"phase_status carries keys no plan row resolves to: {sorted(orphans)}. Written under "
        f"{sorted(written)}, but a reader keying by {PHASE_KEY_FIELDS} looks up "
        f"{sorted(resolvable)} — every lookup would miss and every stage would render 'todo'."
    )

    counted_done = sum(1 for state in written.values() if state == "done")
    looked_up_done = sum(1 for row in plan if written.get(independent(row)) == "done")
    assert counted_done == looked_up_done == 3, (
        "the header counter and the stage rows disagree about the same run: counting "
        f"phase_status values gives {counted_done} done, looking the plan rows up gives "
        f"{looked_up_done}. That is a self-contradicting progress strip (issue 494)."
    )


def test_the_advance_event_names_the_phase_by_the_shared_field():
    """The `phase_advance` payload carries the phase it moved to. It named that field with the
    retired dialect, which left a third spelling of the same id on the SSE wire for a future
    consumer to read wrong. It reports the shared field name."""
    kinds.ensure_loaded()
    design = kinds.get("design")
    plan = design.default_phases()
    loop = store.create(Loop(id="", name="D", kind="design", task="a design system", plan=plan))
    ctx = _Ctx()
    asyncio.run(design.on_new_cycle(store.get(loop.id), [{"cycle": 1, "step": "2"}], ctx))

    advances = [data for event, data in ctx.events if event == "phase_advance"]
    assert advances, "vacuity floor: the writer published no phase_advance to inspect"
    payload = advances[-1]
    assert _RETIRED_DIALECT not in payload, (
        f"phase_advance still names the phase {_RETIRED_DIALECT!r} — a third spelling of the "
        "one id, on the live event wire"
    )
    assert payload.get(PHASE_KEY_FIELDS[0]) == design.phase_key(plan[payload["active"]])
