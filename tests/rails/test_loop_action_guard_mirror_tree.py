"""The source-tree checks split from
``tests/test_loop_action_guard_mirror.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import source_index
from loop_action_guard_mirror_shared import _REPO, _WEB, _strip_comments

from personalclaw.loop.loop import ACTION_SOURCE_STATES

_REGISTRY = _WEB / "lib" / "loopStatus.ts"


# web is optional in some checkouts (backend-only installs); skip cleanly then.
pytestmark = pytest.mark.skipif(not _WEB.exists(), reason="web sources not present")


#: The declaration this rail parses. Split on the name rather than pattern-matching the whole
#: literal: a rename must red loudly here, not silently stop matching somewhere downstream.
_DECL = "export const LOOP_ACTION_SOURCE_STATUSES"


#: The RUN-BACKED loop's map (a ported kind runs as a workflow run and is listed beside the
#: loops-table rows), mirroring `workflows/loop_view.py:RUN_ACTION_SOURCE_STATES`. Parsed by the
#: same parser, so it carries the same floors.
_RUN_DECL = "export const RUN_LOOP_ACTION_SOURCE_STATUSES"


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
#: unification this change performs. It deliberately does NOT catch a bare inline guard (an
#: ``includes`` literal, or the chained ``===`` form ``CodeCockpitPage`` used) — those live on the
#: call sites and are censused by the surfaces that own them, not from here.
_ACTION_MAP_ROW = re.compile(r"\bresume:\s*(?:new Set\(|\[)")


def _registry_text() -> str:
    assert _REGISTRY.is_file(), f"{_REGISTRY} is missing — this rail can parse nothing"
    return _strip_comments(source_index.text(_REGISTRY))


def _web_sources() -> list[Path]:
    return [
        p
        for p in source_index.web_files()
        if p.suffix in {".ts", ".tsx"} and not p.name.endswith(".test.ts")
    ]


def _parse_action_sources(
    text: str, decl: str = _DECL
) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Parse one frontend action-guard map (``decl``) out of TypeScript.

    Returns ``(action -> statuses, action -> referenced set name)``. Every floor lives here so a
    parser that stops matching reds once, loudly, instead of making each caller's equality
    assertion vacuously true.
    """
    parts = text.split(decl, 1)
    assert len(parts) == 2, f"{_REGISTRY} no longer declares {decl} — parser drift?"
    body = parts[1].split("\n}", 1)
    assert len(body) == 2, f"{decl} has no closing brace — parser drift?"
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
    assert rows, f"parsed no rows out of {decl} — parser drift?"

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
            f"parsed an EMPTY state set for `{action}` out of {decl} — parser drift? "
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


def test_the_run_backed_mirror_equals_the_run_action_table():
    """A run-backed loop is listed on the same surfaces as a loops-table row, and its lifecycle is
    the RUN's: one attempt (no resume from ``failed``) and a gate answered on its run page. The list
    offering the loops-table Resume on a failed run-backed row would buy the user a 409; this is the
    same equality rail, for the second map."""
    from personalclaw.workflows.loop_view import RUN_ACTION_SOURCE_STATES

    sources, _ = _parse_action_sources(_registry_text(), _RUN_DECL)
    assert set(sources) == set(RUN_ACTION_SOURCE_STATES), (
        f"RUN_LOOP_ACTION_SOURCE_STATUSES covers {sorted(sources)} but the run table has "
        f"{sorted(RUN_ACTION_SOURCE_STATES)}"
    )
    drift = {
        action: (sorted(sources[action]), sorted(s.value for s in states))
        for action, states in RUN_ACTION_SOURCE_STATES.items()
        if sources[action] != {s.value for s in states}
    }
    assert not drift, (
        "RUN_LOOP_ACTION_SOURCE_STATUSES disagrees with workflows/loop_view.py:"
        f"RUN_ACTION_SOURCE_STATES per action (frontend vs backend): {drift}"
    )


def test_the_stop_row_reuses_the_active_status_set_by_reference():
    _, refs = _parse_action_sources(_registry_text())
    assert refs.get("stop") == "STOPPABLE_LOOP_STATUSES", (
        "the `stop` row of LOOP_ACTION_SOURCE_STATUSES must reference STOPPABLE_LOOP_STATUSES, not "
        f"restate its members (found: {refs.get('stop') or 'an inline literal'}). The backend's "
        "own `stop` row IS STOPPABLE_STATUSES; a second copy of those strings in the same "
        "file is a new drift seam of exactly the kind this rail closes."
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
    # The run-backed map reds on its own absence rather than silently parsing the other one.
    no_run_map = real.replace(_RUN_DECL, "const somethingElse")
    assert no_run_map != real, "the run-map stub did not change the source — stub drift?"
    with pytest.raises(AssertionError):
        _parse_action_sources(no_run_map, _RUN_DECL)


def test_there_is_exactly_one_loop_action_guard_map():
    # Vacuity floor first: the positive control must still match, or "nothing else matches" is a
    # statement about the regex rather than about the codebase.
    assert _ACTION_MAP_ROW.search(
        _registry_text()
    ), f"the action-guard map pattern no longer matches {_REGISTRY} — this census is vacuous"
    others = [
        p.relative_to(_REPO)
        for p in _web_sources()
        if p != _REGISTRY and _ACTION_MAP_ROW.search(_strip_comments(source_index.text(p)))
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
        if (shapes := _hand_written_action_guards(_strip_comments(source_index.text(p))))
    }
    assert not offenders, (
        f"a hand-written lifecycle-action guard reappeared: {offenders}. Import "
        "LOOP_ACTION_SOURCE_STATUSES from lib/loopStatus instead — it mirrors the backend's "
        "ACTION_SOURCE_STATES, which is the only thing that decides whether an action is "
        "accepted or answered with a 409."
    )
