"""ONE loop action-guard vocabulary, backend to frontend (PP-16).

🔴 WHY THIS RAIL EXISTS. Which statuses a loop action may be invoked FROM is one backend table,
``loop.loop:ACTION_SOURCE_STATES``, enforced in ``dashboard/handlers/loop_routes.py`` — a status
outside the action's set gets a 409 (``Cannot resume a loop in 'complete' state``). Every frontend
surface that renders a Start/Pause/Resume/Stop affordance then re-decided the same question by
hand, and the copies disagreed with the backend AND with each other.

Measured on the tree that introduced ``LOOP_ACTION_SOURCE_STATUSES``, ``resume`` had SIX
hand-written guards spanning three vocabularies:

* ``pages/chat/SdlcProgressCard.tsx``, ``pages/loops/LoopsListPage.tsx`` (twice) — three states.
* ``pages/loops/DesignCockpitPage.tsx``, ``pages/loops/LoopCockpitPage.tsx`` — four (plus
  ``failed``).
* ``pages/code/CodeCockpitPage.tsx`` — all five, but written as a chained ``===`` disjunction
  rather than an array literal, which is why an array-literal search found only five of the six.

Two user-visible defects fell out of that. Five of the six omitted ``blocked``, so a blocked loop
was unresumable through the UI everywhere while the backend accepted the transition; and ``failed``
was resumable on the two cockpits but not on the list or the in-chat card, so one loop offered a
different action set depending on which view you opened it from. ``start`` drifted the same way:
the backend accepts two states, one cockpit offered only one of them.

**Equality, not subset.** Each action's frontend set must EQUAL the backend's. A subset assertion
would have passed happily on all six guards — being a subset is exactly the shape the ``blocked``
omission had, and it is what let one missing state ship on every surface at once.

**Both sides are derived.** The backend side is the imported table, never retyped here; the
frontend side is parsed out of the TypeScript. Neither is a hand-written expectation, so this rail
cannot drift into agreeing with itself.

**Every scan carries a vacuity floor**, and ``test_the_parse_floors_fire`` proves the floors by
feeding the parser stubs that must red. A regex that quietly stops matching turns every equality
assertion below into a statement about the regex; that failure mode is the whole reason this file
exists, so it is asserted rather than trusted.

🪤 The parse is a TEXT scan with no comment awareness, and this repo has been bitten by a docstring
satisfying a ratchet, so comments are blanked before anything is matched (``_strip_comments``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from personalclaw.loop.loop import ACTION_SOURCE_STATES

_REPO = Path(__file__).resolve().parent.parent
_WEB = _REPO / "web" / "src"
_REGISTRY = _WEB / "lib" / "loopStatus.ts"

# web is optional in some checkouts (backend-only installs); skip cleanly then.
pytestmark = pytest.mark.skipif(not _WEB.exists(), reason="web sources not present")

#: The declaration this rail parses. Split on the name rather than pattern-matching the whole
#: literal: a rename must red loudly here, not silently stop matching somewhere downstream.
_DECL = "export const LOOP_ACTION_SOURCE_STATUSES"

#: One row of the map: an action keyed to either an inline ``new Set([...])`` or a reference to an
#: already-exported set (``stop``). A reference that cannot be resolved is an error, never an empty
#: set — an unresolved row must not read as "this action allows nothing".
_ROW = re.compile(
    r"^ {2}(\w+):\s*(?:new Set\(\[(?P<lit>[^\]]*)\]\)|(?P<ref>[A-Za-z_]\w*))\s*,\s*$",
    re.MULTILINE,
)

#: An exported status-set mirror, so a row written as a reference resolves to real members.
_NAMED_SET = re.compile(
    r"^export const (\w+): ReadonlySet<string> = new Set\(\[(.*?)\]\)", re.MULTILINE | re.DOTALL
)

#: A single-quoted member inside a parsed set literal.
_MEMBER = re.compile(r"'([a-z_]+)'")

#: A `...OTHER_SET` spread inside a set literal. A composed set (`new Set([...ACTIVE, 'x'])`) is how
#: this file avoids restating members, so the parser has to expand one — reading only the quoted
#: members would silently under-count and make the equality assertion pass on a subset.
_SPREAD = re.compile(r"\.\.\.([A-Za-z_]\w*)")

#: An action-guard MAP declaring a `resume` row, in either shape the map could take. Scoped to what
#: it can actually see: this catches a second action→states TABLE reappearing, which is the
#: unification this slice performs. It deliberately does NOT catch a bare inline guard (an
#: ``includes`` literal, or the chained ``===`` form ``CodeCockpitPage`` used) — those live on the
#: call sites and are censused by the surfaces that own them, not from here.
_ACTION_MAP_ROW = re.compile(r"\bresume:\s*(?:new Set\(|\[)")

_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(text: str) -> str:
    """Blank every comment, preserving newlines so line numbers survive.

    The mirrors in ``lib/loopStatus.ts`` carry long docstrings that name individual statuses, and
    the census patterns here are short enough that prose could satisfy one. Line structure is kept
    so a future failure message can still cite a line.
    """
    text = _BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return _LINE_COMMENT.sub("", text)


def _registry_text() -> str:
    assert _REGISTRY.is_file(), f"{_REGISTRY} is missing — this rail can parse nothing"
    return _strip_comments(_REGISTRY.read_text(encoding="utf-8"))


def _web_sources() -> list[Path]:
    return [
        p
        for p in sorted(_WEB.rglob("*.ts*"))
        if p.suffix in {".ts", ".tsx"} and not p.name.endswith(".test.ts")
    ]


def _parse_action_sources(text: str) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Parse the frontend action-guard map out of TypeScript.

    Returns ``(action -> statuses, action -> referenced set name)``. Every floor lives here so a
    parser that stops matching reds once, loudly, instead of making each caller's equality
    assertion vacuously true.
    """
    parts = text.split(_DECL, 1)
    assert len(parts) == 2, f"{_REGISTRY} no longer declares {_DECL} — parser drift?"
    body = parts[1].split("\n}", 1)
    assert len(body) == 2, f"{_DECL} has no closing brace — parser drift?"
    table = body[0]

    raw = {name: members for name, members in _NAMED_SET.findall(text)}
    named = {name: set(_MEMBER.findall(members)) for name, members in raw.items()}
    # Expand `...OTHER_SET` spreads, innermost first. Bounded by the number of sets so a cyclic
    # spread cannot hang the suite, and asserted rather than silently left partial.
    for _ in range(len(raw) + 1):
        changed = False
        for name, members in raw.items():
            for ref in _SPREAD.findall(members):
                assert ref in named, (
                    f"`{name}` spreads `{ref}`, which is not an exported ReadonlySet<string> in "
                    f"{_REGISTRY} — parser drift, or a broken reference"
                )
                if not named[ref] <= named[name]:
                    named[name] |= named[ref]
                    changed = True
        if not changed:
            break
    else:  # pragma: no cover — a cyclic spread
        raise AssertionError(f"spreads in {_REGISTRY} did not converge — a cycle?")
    for name, members in raw.items():
        for ref in _SPREAD.findall(members):
            assert named[ref] <= named[name], (
                f"the `{ref}` spread inside `{name}` did not expand — the parser would compare a "
                "SUBSET and pass on a mirror that is missing members"
            )
    rows = _ROW.findall(table)
    assert rows, f"parsed no rows out of {_DECL} — parser drift?"

    sources: dict[str, set[str]] = {}
    refs: dict[str, str] = {}
    for action, literal, ref in rows:
        if ref:
            assert ref in named, (
                f"the `{action}` row references `{ref}`, which is not an exported "
                f"ReadonlySet<string> in {_REGISTRY} — parser drift, or a broken reference"
            )
            refs[action] = ref
            sources[action] = named[ref]
        else:
            sources[action] = set(_MEMBER.findall(literal))
        assert sources[action], (
            f"parsed an EMPTY state set for `{action}` out of {_DECL} — parser drift? "
            "An empty set would make this action's equality assertion meaningless."
        )
    return sources, refs


def test_the_mirror_covers_every_backend_action():
    sources, _ = _parse_action_sources(_registry_text())
    backend = set(ACTION_SOURCE_STATES)
    assert backend, "ACTION_SOURCE_STATES is empty — import drift?"
    assert set(sources) == backend, (
        f"LOOP_ACTION_SOURCE_STATUSES does not cover loop.loop:ACTION_SOURCE_STATES — frontend "
        f"{sorted(sources)} vs backend {sorted(backend)}. An action missing from the mirror sends "
        "its surfaces back to a hand-written literal, which is how six `resume` guards spanning "
        "three vocabularies shipped at once."
    )


def test_each_action_mirrors_the_backend_source_states_exactly():
    sources, _ = _parse_action_sources(_registry_text())
    drift = {
        action: (sorted(sources.get(action, set())), sorted(s.value for s in states))
        for action, states in ACTION_SOURCE_STATES.items()
        if sources.get(action, set()) != {s.value for s in states}
    }
    assert not drift, (
        "LOOP_ACTION_SOURCE_STATUSES disagrees with loop.loop:ACTION_SOURCE_STATES per action "
        f"(frontend vs backend): {drift}. Equality, not subset: an affordance the backend refuses "
        "buys the user a 409, and one it accepts but the UI withholds strands the loop — five of "
        "six hand-written `resume` guards omitted `blocked`, so a blocked loop could not be "
        "resumed from any surface while the backend was willing."
    )


def test_the_stop_row_reuses_the_active_status_set_by_reference():
    _, refs = _parse_action_sources(_registry_text())
    assert refs.get("stop") == "STOPPABLE_LOOP_STATUSES", (
        "the `stop` row of LOOP_ACTION_SOURCE_STATUSES must reference STOPPABLE_LOOP_STATUSES, not "
        f"restate its members (found: {refs.get('stop') or 'an inline literal'}). The backend's "
        "own `stop` row IS STOPPABLE_STATUSES; a second copy of those strings in the same "
        "file is a new drift seam of exactly the kind this slice closes."
    )


def test_no_non_terminal_status_is_actionless():
    """`PP-16`: the union of the action rows must cover every non-terminal status.

    The specific gap this closes: `intake` and `planning` were in NO row, so a loop whose classifier
    or planner died offered no action on any surface and `DELETE` was its only exit — which discards
    the record instead of terminating it. Asserted as the general property rather than as
    "intake and planning are in stop", because the next status added to the enum should fail
    this too rather than quietly inheriting the same hole.
    """
    from personalclaw.loop.loop import TERMINAL_STATUSES, LoopStatus

    union: set[LoopStatus] = set().union(*ACTION_SOURCE_STATES.values())
    assert union, "ACTION_SOURCE_STATES is empty — import drift?"
    non_terminal = set(LoopStatus) - set(TERMINAL_STATUSES)
    assert non_terminal, "every status reads as terminal — import drift?"
    stranded = sorted(s.value for s in non_terminal - union)
    assert not stranded, (
        f"these non-terminal statuses have NO available lifecycle action: {stranded}. A loop "
        "that reaches one can only be DELETED, losing its record. Give it a home in "
        "ACTION_SOURCE_STATES (and mirror it in web/src/lib/loopStatus.ts) rather than "
        "leaving the state actionless."
    )


def test_the_parse_floors_fire():
    """The floors are asserted, not trusted — each stub below must red the parser.

    Without this, a pattern that stopped matching would make every equality assertion above pass
    perfectly forever.
    """
    real = _registry_text()
    stubs = {
        "no declaration": real.replace(_DECL, "const somethingElse"),
        "no rows": f"{_DECL}: Readonly<Record<LoopAction, ReadonlySet<string>>> = {{\n}}\n",
        "an empty set literal": real.replace("new Set(['running'])", "new Set([])"),
        "an unresolvable reference": real.replace(
            "stop: STOPPABLE_LOOP_STATUSES,", "stop: NO_SUCH_EXPORTED_SET,"
        ),
    }
    for label, stub in stubs.items():
        assert stub != real, f"the {label!r} stub did not change the source — stub drift?"
        with pytest.raises(AssertionError):
            _parse_action_sources(stub)


def test_there_is_exactly_one_loop_action_guard_map():
    # Vacuity floor first: the positive control must still match, or "nothing else matches" is a
    # statement about the regex rather than about the codebase.
    assert _ACTION_MAP_ROW.search(
        _registry_text()
    ), f"the action-guard map pattern no longer matches {_REGISTRY} — this census is vacuous"
    others = [
        p.relative_to(_REPO)
        for p in _web_sources()
        if p != _REGISTRY and _ACTION_MAP_ROW.search(_strip_comments(p.read_text(encoding="utf-8")))
    ]
    assert not others, (
        f"a second loop action-guard map reappeared: {[str(p) for p in others]}. The source states "
        "for every action live in web/src/lib/loopStatus.ts only — one table per question is what "
        "keeps a blocked loop resumable on every surface at once."
    )


#: A hand-written lifecycle guard, in EITHER shape found in the wild. The array form
#: (`[...].includes(status)`) is what five of the six guards used; the chained form
#: (`status === 'a' || status === 'b'`) is what the sixth used — which is why a census written
#: only for the array shape reported the app clean while a guard carrying its own third
#: vocabulary sat in `pages/code/`.
_ARRAY_GUARD = re.compile(
    r"\[[^\]]*'(?:paused|stagnant|blocked|needs_input)'[^\]]*\]\s*\.includes\s*\("
)
_CHAINED_GUARD = re.compile(
    r"status\s*===\s*'(?:paused|stagnant|blocked|needs_input)'\s*\|\|" r"[^\n]*status\s*===\s*'"
)
#: How far past a candidate to look for the dispatch that makes it an ACTION guard.
_DISPATCH_WINDOW = 240
_DISPATCH = re.compile(r"act\(\s*'(?:start|pause|resume|stop)'|act\(\s*e\s*,")


def _hand_written_action_guards(text: str) -> list[str]:
    """Guard-shaped state tests that actually GATE A DISPATCH, in source order.

    🔴 Proximity to `act(...)` is the discriminator, and it is load-bearing rather than
    decorative. A first version of this census matched any membership test over lifecycle
    states and reported two offenders that are nothing of the kind: an `attention` display flag
    (which deliberately includes the terminal `stopped`) and a CTA-copy selector keyed on the
    EFFECTIVE status. Converting either would have been wrong. What this rail is for is a
    control whose availability disagrees with the backend, so it only counts a shape that sits
    in front of the call that asks the backend.
    """
    out = []
    for pattern, shape in ((_ARRAY_GUARD, "array"), (_CHAINED_GUARD, "chained")):
        for m in pattern.finditer(text):
            if _DISPATCH.search(text[m.end() : m.end() + _DISPATCH_WINDOW]):
                out.append(shape)
    return out


def test_no_surface_hand_writes_a_lifecycle_action_guard():
    """Six guards across five files each carried their own vocabulary (three, four and five
    states); five omitted `blocked`, which the backend has always accepted a `resume` from, so a
    blocked loop was unresumable everywhere. One mirror is the fix — this rail is what stops the
    seventh from being written, in either shape."""
    # Vacuity floors: both shapes and the dispatch window must still match their own samples,
    # or every "no offenders" assertion below passes without measuring anything.
    sample_array = (
        "['paused', 'stagnant'].includes(c.status) && <B onClick={() => act('resume')} />"
    )
    sample_chain = (
        "s.status === 'paused' || s.status === 'blocked' ? <B onClick={() => act('resume')} />"
    )
    assert _hand_written_action_guards(sample_array) == [
        "array"
    ], "the array shape stopped matching"
    assert _hand_written_action_guards(sample_chain) == [
        "chained"
    ], "the chained shape stopped matching"
    assert not _hand_written_action_guards(
        "attention={['blocked', 'stopped'].includes(p.status)} />"
    ), "the census counts a display flag as an action guard — it would force a wrong conversion"

    sources = _web_sources()
    assert sources, "no web sources found — this census is vacuous"
    offenders = {
        str(p.relative_to(_REPO)): shapes
        for p in sources
        if (shapes := _hand_written_action_guards(_strip_comments(p.read_text(encoding="utf-8"))))
    }
    assert not offenders, (
        f"a hand-written lifecycle-action guard reappeared: {offenders}. Import "
        "LOOP_ACTION_SOURCE_STATUSES from lib/loopStatus instead — it mirrors the backend's "
        "ACTION_SOURCE_STATES, which is the only thing that decides whether an action is "
        "accepted or answered with a 409."
    )


#: The surfaces that host a loop lifecycle control today. Named rather than derived, following
#: `pages/surfaceEntranceAdoption.test.ts`'s reasoning: the property is about construction, and a
#: derived list would silently shrink to nothing the day someone renames a directory.
_LIFECYCLE_SURFACES = (
    "pages/loops/LoopsListPage.tsx",
    "pages/loops/LoopCockpitPage.tsx",
    "pages/loops/DesignCockpitPage.tsx",
    "pages/code/CodeCockpitPage.tsx",
    "pages/chat/SdlcProgressCard.tsx",
)


def test_every_lifecycle_surface_actually_reaches_the_mirror():
    """The other half of the census above, which on its own is satisfied by deleting a control.

    "No hand-written guard" is true of a file with no affordances at all, so absence is not
    adoption. Each surface that offers Start/Pause/Resume/Stop must be reached by the one mirror.
    """
    missing = []
    for rel in _LIFECYCLE_SURFACES:
        path = _WEB / rel
        assert path.exists(), f"{rel} is gone — update this list rather than letting it rot"
        text = _strip_comments(path.read_text(encoding="utf-8"))
        if "LOOP_ACTION_SOURCE_STATUSES" not in text:
            missing.append(rel)
    assert not missing, (
        f"a lifecycle surface no longer reaches the action mirror: {missing}. Either it lost its "
        "controls (update this list and say so) or it went back to deciding for itself."
    )
