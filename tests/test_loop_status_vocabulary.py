"""ONE loop-status vocabulary, backend to frontend (PP-16).

🔴 WHY THIS RAIL EXISTS. A loop's lifecycle status is one backend enum (``loop.loop:LoopStatus``)
and the frontend narrated it from TWO tables that had drifted word-for-word and tone-for-tone:

* ``web/src/lib/loopStatus.ts`` — read by the Code list, the in-chat SDLC progress card and the
  Projects linked-work rows: ``stagnant`` → "Stalled", ``intake`` → "Analyzing",
  ``complete`` → "Complete", ``running`` → primary, ``complete`` → ok.
* ``web/src/pages/loops/loopStatusMeta.ts`` — read by the Loops list and the dashboard Active Work
  widget: ``stagnant`` → "Stagnant", ``intake`` → "Intake", ``complete`` → "Completed",
  ``running`` → ok, ``complete`` → primary, ``paused`` → warn.

So the same loop said "Stalled" on one surface and "Stagnant" on the next, and green meant
"running" on one and "finished" on the other. The first file's own header comment claimed it had
already fixed exactly that drift — it consolidated three per-page maps, and then a fourth shipped
beside it. And ``pages/workflows/terminalSuccessLabel.test.ts``, the rail that locked "a finished
run is *Completed* everywhere", could not see the violation: it named the other two registries by
path and matched a ``{ label: ... }`` shape that ``lib/loopStatus.ts`` did not use.

PP-16 retires the second table. This is the cross-tier half of that: the surviving registry must
cover the backend enum EXACTLY, and no second one may reappear.

**Both drift directions are pinned.** A new ``LoopStatus`` member with no frontend word reds this,
and a stale frontend key naming a status the backend cannot produce reds it too — a status the
frontend renders but the backend never sends is dead vocabulary a reader will trust.

**Every scan carries a vacuity floor.** A regex that stops matching reads as a clean pass forever,
so each census asserts its positive control (the surviving registry) still matches before it
concludes that nothing else does.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from personalclaw.loop.loop import ACTIVE_STATUSES, LoopStatus

_REPO = Path(__file__).resolve().parent.parent
_WEB = _REPO / "web" / "src"
_REGISTRY = _WEB / "lib" / "loopStatus.ts"
_API = _WEB / "lib" / "api.ts"

# web is optional in some checkouts (backend-only installs); skip cleanly then.
pytestmark = pytest.mark.skipif(not _WEB.exists(), reason="web sources not present")

#: The one status the frontend renders that the backend never sends: a `complete` loop carrying an
#: `error_message` finished non-genuinely (budget exhausted, DoD unmet), which `effectiveLoopStatus`
#: maps to this synthetic key so it does not read as a genuine green completion.
_SYNTHETIC = {"ended_early"}

#: A loop-status registry declares a status key against a string or an object literal
#: (``stagnant: { label: 'Stalled', … }``). Deliberately NOT matched: a status keyed to a number
#: (``CodeSection``'s ``stagnant: 2`` attention-sort rank) — a rank order is not a vocabulary.
#:
#: Also deliberately NOT counted: a table that PROJECTS a loop status into a different closed
#: vocabulary. `lib/useAgentActivity.ts`'s `LOOP_STATE` maps the 12 `UnifiedLoopStatus` members
#: onto the 5 `AgentActivityState` members (`working`/`needs_input`/`waiting_approval`/`idle`/
#: `error`) for the ambient worlds — it holds no label and no tone, so it cannot produce the
#: "Stalled vs Stagnant" or two-meanings-of-green drift this census exists to prevent. Same
#: reasoning as the rank-order exemption above: a projection is not a words-and-tones table.
#: (AS-9 added it; PP-16's rail matched its `stagnant: 'needs_input'` row, which is a state
#: name, not a display word.)
_REGISTRY_ROW = re.compile(r"\bstagnant:\s*[{'\"]")

#: The active-status set literal, in the order every hand-written copy used.
_ACTIVE_LITERAL = re.compile(r"'running',\s*'paused',\s*'stagnant'")


def _registry_keys() -> set[str]:
    """The status keys the surviving registry declares (``key: { label: … }`` rows)."""
    text = _REGISTRY.read_text(encoding="utf-8")
    body = text.split("const LOOP_STATUS: Record<string, LoopStatusLook> = {", 1)
    assert len(body) == 2, f"{_REGISTRY} no longer declares LOOP_STATUS — parser drift?"
    table = body[1].split("\n}", 1)[0]
    return set(re.findall(r"^\s{2}(\w+):\s*\{", table, re.MULTILINE))


def _web_sources() -> list[Path]:
    return [
        p
        for p in sorted(_WEB.rglob("*.ts*"))
        if p.suffix in {".ts", ".tsx"} and not p.name.endswith(".test.ts")
    ]


def test_the_registry_covers_the_backend_enum_exactly():
    keys = _registry_keys()
    assert keys, "parsed no rows out of the loop-status registry — parser drift?"
    backend = {s.value for s in LoopStatus}
    assert backend, "LoopStatus enum is empty — import drift?"
    missing = backend - keys
    stale = keys - backend - _SYNTHETIC
    assert not missing, (
        f"LoopStatus members with no word in web/src/lib/loopStatus.ts: {sorted(missing)}. "
        "A status the frontend cannot name renders as its raw snake_case wire value."
    )
    assert not stale, (
        f"web/src/lib/loopStatus.ts names statuses the backend never sends: {sorted(stale)}. "
        f"Remove them, or list them in _SYNTHETIC if derived like {sorted(_SYNTHETIC)}."
    )


def test_the_frontend_active_set_mirrors_the_backend_one():
    text = _REGISTRY.read_text(encoding="utf-8")
    block = text.split("export const ACTIVE_LOOP_STATUSES", 1)
    assert len(block) == 2, "ACTIVE_LOOP_STATUSES is gone from the registry — parser drift?"
    fe = set(re.findall(r"'([a-z_]+)'", block[1].split("])", 1)[0]))
    assert fe, "parsed no members out of ACTIVE_LOOP_STATUSES — parser drift?"
    be = {s.value for s in ACTIVE_STATUSES}
    assert fe == be, (
        f"ACTIVE_LOOP_STATUSES disagrees with loop.loop:ACTIVE_STATUSES — frontend {sorted(fe)} vs "
        f"backend {sorted(be)}. This set decides the nav badge count, the dashboard hero + Active "
        "Work rows, and whether a cockpit holds its live stream open; one hand-written copy of it "
        "silently omitted `blocked`, so a blocked design loop read as finished."
    )


def test_there_is_exactly_one_loop_status_registry():
    # Vacuity floor first: the positive control must still match, or "nothing else matches" is
    # a statement about the regex rather than about the codebase.
    assert _REGISTRY_ROW.search(
        _REGISTRY.read_text(encoding="utf-8")
    ), f"the loop-status row pattern no longer matches {_REGISTRY} — this census is vacuous"
    others = [
        p.relative_to(_REPO)
        for p in _web_sources()
        if p != _REGISTRY
        and p.name != "useAgentActivity.ts"  # a projection, not a vocabulary — see above
        and _REGISTRY_ROW.search(p.read_text(encoding="utf-8"))
    ]
    assert not others, (
        f"a second loop-status registry reappeared: {[str(p) for p in others]}. The words and "
        "tones live in web/src/lib/loopStatus.ts only — two tables is how 'Stalled' vs 'Stagnant' "
        "and two meanings of green shipped at once."
    )


def test_no_surface_rewrites_the_active_status_set_by_hand():
    assert _ACTIVE_LITERAL.search(
        _REGISTRY.read_text(encoding="utf-8")
    ), f"the active-set literal no longer matches {_REGISTRY} — this census is vacuous"
    others = [
        p.relative_to(_REPO)
        for p in _web_sources()
        if p != _REGISTRY and _ACTIVE_LITERAL.search(p.read_text(encoding="utf-8"))
    ]
    assert not others, (
        f"a hand-written active-loop-status set reappeared: {[str(p) for p in others]}. Import "
        "ACTIVE_LOOP_STATUSES from lib/loopStatus instead — four copies drifted here once, and the "
        "one that dropped `blocked` made a blocked loop's cockpit read as finished."
    )


def _union_members(name: str) -> set[str]:
    """The string-literal members of an exported TypeScript union, comments blanked first.

    Blanked because these scans are TEXT scans: a docstring listing states would otherwise
    enrol them, which is how a rail starts certifying prose instead of code.
    """
    text = _API.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    m = re.search(rf"export type {name}\s*=\s*((?:[^\n]*\n)+?)\s*(?:export|/\*|\Z)", text)
    assert m, f"could not find `export type {name}` in web/src/lib/api.ts — parser drift?"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_the_wire_type_union_covers_the_backend_enum_exactly():
    """🔴 The gap this rail did NOT have, and the defect it let through.

    The registry test above proves the frontend has a WORD for every status. It says nothing
    about the TypeScript union that TYPES a status, and those drifted apart: `LoopStatus` in
    `api.ts` carried eleven members while the backend enum has twelve, omitting `blocked`.
    A `status === 'blocked'` comparison against that type is a compile error, so every
    hand-written affordance guard in the app simply left `blocked` out — a blocked loop was
    unresumable on every surface even though the backend accepts a resume from it. Naming the
    states in a union is a second vocabulary, so it gets the same equality treatment.
    """
    union = _union_members("UnifiedLoopStatus")
    assert union, "parsed no members out of UnifiedLoopStatus — parser drift?"
    backend = {s.value for s in LoopStatus}
    assert backend, "LoopStatus enum is empty — import drift?"
    assert union == backend, (
        "web/src/lib/api.ts:UnifiedLoopStatus disagrees with loop.loop:LoopStatus "
        f"(union-only={sorted(union - backend)}, backend-only={sorted(backend - union)}). "
        "A member the union omits cannot be compared against without a type error, which is "
        "how a real state stops being reachable from any affordance."
    )


def test_there_is_exactly_one_wire_status_union():
    """The retired per-kind copies must not come back. `LoopStatus`/`CodeStatus` were
    goal-shaped and code-shaped unions of the same enum; one of them omitted `blocked`."""
    text = _API.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    found = set(re.findall(r"export type (\w*(?:Loop|Code)Status)\s*=", text))
    assert (
        "UnifiedLoopStatus" in found
    ), "UnifiedLoopStatus is gone from web/src/lib/api.ts — this rail can prove nothing"
    assert found == {
        "UnifiedLoopStatus"
    }, f"more than one loop-status union is exported from web/src/lib/api.ts: {sorted(found)}"
