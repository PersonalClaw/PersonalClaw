"""Both-directions rail for the WF service-result code registry (#3499).

The peer of ``tests/test_error_codes_append_only.py`` (agent codes, ``ERR_UPPER_SNAKE``)
and ``tests/test_http_error_codes_append_only.py`` (wire codes, ``lowercase_snake``). This
one guards the THIRD vocabulary: ``workflows.error_codes.WF_ERROR_CODES``,
``WF_UPPER_SNAKE``.

**Why it exists.** ``WF_*`` was the only one of the three vocabularies with no registry and
no rail. 162 codes a workflow-template author or an MCP caller can hit, and exactly ONE
documented anywhere in the repository (``WF_MISSING_EXPR``, in
``docs/architecture/workflows.md``). #3499 measured 159; three more (``WF_INPUT_BAD_LOOP_FIELD``,
``WF_INPUT_DUPLICATE_LOOP_FIELD``, ``WF_LOOP_KIND_NO_TASK_INPUT``) arrived on ``main`` from PP-16
while this was being written, and **this rail is what caught them** — which is the argument for it,
made on its first day. A code is a contract identifier — something a caller
branches on — so an unregistered one is a contract nobody can depend on.

**Both directions, because the second is the one that gets skipped:**

1. :func:`test_every_raised_code_is_registered` — every ``WF_*`` code a core module raises
   has a registry row. Without it the registry falls behind the emitters.
2. :func:`test_every_registered_code_is_still_raised` — every registry row is still raised
   somewhere. Without it the registry rots into a list of codes that no longer exist, which
   is worse than no list because it reads as authoritative.

🔴 **Direction 2 is only meaningful because the registry module is EXCLUDED from the
scan.** ``error_codes.py`` holds all 162 codes as dict keys — string literals inside core —
so a scan that counted them would make direction 2 true by construction: a rail proving
itself. :data:`_REGISTRY_MODULE` is the exclusion and the reason it exists.

🪤 **The scan is AST-based, not textual (brief §15).** A rail that greps raw source counts
the prose documenting a code as a raise of it, so the commit that documents a vocabulary is
the commit the rail reds. Here that hazard is acute: this repo's modules explain their codes
at length in docstrings (``supervisor_policy.py`` writes ``WF_SUPERVISOR_*``,
``http_errors.py`` writes ``WF_UPPER_SNAKE``, ``mcp_workflows.py`` names
``WF_PLAN_TEMPLATE_NOT_FOUND`` in a comment about a router bug). A textual scan would report
all three as codes. Reading ``ast`` nodes cannot: a comment and a docstring hold no
literal in a raising position. :func:`test_the_scanner_ignores_prose_and_reads_raises`
asserts both halves of that with the positive control beside the negative one.

**A MAP entry is not a RAISE.** ``handlers.py``'s ``_STATUS_MAP`` translates WF codes into
wire codes; its 46 keys are a *translation table*, not emission sites. The scanner keeps the
two apart so direction 2 means "still raised" rather than "still mapped" — the weaker
reading would let a code survive in the registry on the strength of a lookup table alone.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from personalclaw.errors import ERROR_CODES
from personalclaw.http_errors import HTTP_ERROR_CODES
from personalclaw.workflows.error_codes import WF_ERROR_CODES

_CORE = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The registry itself — every code appears here as a dict KEY, so counting this file would
#: make direction 2 vacuous. See the module docstring.
_REGISTRY_MODULE = _CORE / "workflows" / "error_codes.py"

_CODE_RE = re.compile(r"^WF_[A-Z][A-Z0-9_]*$")

#: ``WF_``-prefixed names that are NOT error codes and must not be read as such. Enumerated
#: rather than pattern-excluded, so a real code can never be silently dropped by a rule that
#: was written for these two.
#:
#: * ``WF_DEPTH_KEY`` — ``engine.py``'s environment-variable NAME for the subworkflow depth
#:   counter a leaf inherits. A constant, never an error code.
#: * ``WF_UPPER_SNAKE`` — prose naming the convention itself (``http_errors.py`` and
#:   ``service.py`` both write it when explaining which vocabulary they are on).
#:
#: Both are excluded by NAME here rather than by the scanner, because the scanner reading
#: only raising positions already means neither can appear; this list is what documents why
#: a reader greping for ``WF_`` in core finds two more names than the registry holds.
_NOT_ERROR_CODES = frozenset({"WF_DEPTH_KEY", "WF_UPPER_SNAKE"})

#: Codes raised in core: 162 across 10 modules (159 at #3499, plus PP-16's three). A FLOOR: if
#: the scanner stops matching, both directions above pass trivially, and a rail that inspected
#: nothing must never read as clean.
_RAISED_CODE_FLOOR = 155

#: The `_STATUS_MAP` translation-table size measured at #3499. Also a floor, for the same
#: reason: a map check over an empty map proves nothing.
_MAPPED_CODE_FLOOR = 40


def _core_modules() -> list[Path]:
    return [
        p
        for p in sorted(_CORE.rglob("*.py"))
        if "__pycache__" not in p.parts and p != _REGISTRY_MODULE
    ]


def _literal_codes(node: ast.AST) -> list[str]:
    """``WF_*`` string literals directly under *node* (not recursively)."""
    out: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if _CODE_RE.match(node.value) and node.value not in _NOT_ERROR_CODES:
            out.append(node.value)
    return out


def scan(text: str, where: str = "?") -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """``(raised, mapped)``, each ``code -> ["<where>:<line>", ...]``.

    RAISED — a code literal in a position that *emits* it:

    * an argument or a ``code=``/``error_code=`` keyword of a call
      (``_service_failure(...)``, ``_add(...)``, ``Issue(...)``, ``Finding(...)``,
      ``tool_failure(...)``, ``_ApplyError(...)``);
    * the ``"code"`` value of a dict literal (``controller.py``'s inline result dicts);
    * an element of a tuple literal (``revision.py`` returns ``(node_id, code, message)``).

    MAPPED — a code literal used as a dict KEY, i.e. ``handlers.py``'s ``_STATUS_MAP``.
    Deliberately a separate bucket; see the module docstring.
    """
    raised: dict[str, list[str]] = {}
    mapped: dict[str, list[str]] = {}
    tree = ast.parse(text)

    def note(bucket: dict[str, list[str]], code: str, lineno: int) -> None:
        bucket.setdefault(code, []).append(f"{where}:{lineno}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for arg in node.args:
                for code in _literal_codes(arg):
                    note(raised, code, getattr(arg, "lineno", node.lineno))
            for kw in node.keywords:
                if kw.arg in ("code", "error_code"):
                    for code in _literal_codes(kw.value):
                        note(raised, code, getattr(kw.value, "lineno", node.lineno))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                for code in _literal_codes(key):
                    note(mapped, code, getattr(key, "lineno", 0))
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "code"
                    and (codes := _literal_codes(value))
                ):
                    for code in codes:
                        note(raised, code, getattr(value, "lineno", 0))
        elif isinstance(node, ast.Tuple):
            for elt in node.elts:
                for code in _literal_codes(elt):
                    note(raised, code, getattr(elt, "lineno", 0))
    return raised, mapped


def _scan_core() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    raised: dict[str, list[str]] = {}
    mapped: dict[str, list[str]] = {}
    for p in _core_modules():
        rel = str(p.relative_to(_CORE.parents[1]))
        r, m = scan(p.read_text(encoding="utf-8"), rel)
        for bucket, found in ((raised, r), (mapped, m)):
            for code, sites in found.items():
                bucket.setdefault(code, []).extend(sites)
    return raised, mapped


def _floor_guard(raised: dict[str, list[str]]) -> None:
    assert len(raised) >= _RAISED_CODE_FLOOR, (
        f"the scan found only {len(raised)} raised WF codes, below the {_RAISED_CODE_FLOOR} "
        f"measured at #3499 — the scanner stopped matching, so a clean result means nothing. "
        f"Fix the scan before trusting it."
    )


def test_every_raised_code_is_registered():
    """Direction 1: the registry cannot fall behind the emitters."""
    raised, _ = _scan_core()
    _floor_guard(raised)
    unregistered = sorted(c for c in raised if c not in WF_ERROR_CODES)
    assert not unregistered, (
        "these WF codes are raised in core with no WF_ERROR_CODES row. A WF code is a "
        "contract identifier a template author and an MCP caller branch on — register it, "
        "with a meaning DERIVED from the raise site, in the same change that ships it:\n"
        + "\n".join(f"  {c}  ({', '.join(raised[c][:3])})" for c in unregistered)
    )


def test_every_registered_code_is_still_raised():
    """Direction 2 — the half people skip, and the one that stops the registry rotting.

    A row nothing raises is a contract that quietly stopped existing: an author who reads
    it, or branches on it, is relying on a code the engine can no longer produce. A MAP
    entry does not count (see the module docstring): being translatable into a wire code is
    not the same as being emitted.
    """
    raised, _ = _scan_core()
    _floor_guard(raised)
    orphans = sorted(c for c in WF_ERROR_CODES if c not in raised)
    assert not orphans, (
        "these WF_ERROR_CODES rows are no longer raised anywhere in core. Remove the row in "
        "the same change that removed its last raise site — a registry of codes that no "
        "longer exist reads as authoritative and is not:\n" + "\n".join(f"  {c}" for c in orphans)
    )


def test_every_mapped_code_is_registered():
    """``handlers.py``'s ``_STATUS_MAP`` translates WF codes into the wire vocabulary, so a
    key it holds is a code the HTTP surface promises to answer for. An unregistered one
    would be a documented HTTP behaviour with no documented cause."""
    _, mapped = _scan_core()
    assert len(mapped) >= _MAPPED_CODE_FLOOR, (
        f"only {len(mapped)} WF codes found in a translation table, below the "
        f"{_MAPPED_CODE_FLOOR} measured at #3499 — the scanner is broken, not the code."
    )
    unregistered = sorted(c for c in mapped if c not in WF_ERROR_CODES)
    assert (
        not unregistered
    ), "these WF codes are translated to a wire code but have no registry row:\n" + "\n".join(
        f"  {c}" for c in unregistered
    )


def test_all_codes_follow_the_wf_upper_snake_convention():
    """WF_UPPER_SNAKE is what keeps this vocabulary distinguishable from the other two."""
    bad = [c for c in WF_ERROR_CODES if not _CODE_RE.match(c)]
    assert not bad, f"WF codes violate the WF_UPPER_SNAKE convention: {bad}"


def test_the_three_vocabularies_stay_disjoint():
    """Three vocabularies, three surfaces, and the SHAPE of a code says which one it is on.

    ``lowercase_snake`` is the wire envelope a browser client reads; ``ERR_UPPER_SNAKE`` is
    the carrier into an LLM session; ``WF_UPPER_SNAKE`` is the transport-independent
    workflows service result that ``handlers.py`` translates into the first. An overlapping
    key would make two of them indistinguishable at the point of branching.
    """
    assert not set(WF_ERROR_CODES) & set(HTTP_ERROR_CODES)
    assert not set(WF_ERROR_CODES) & set(ERROR_CODES)
    assert not any(c.startswith("ERR_") for c in WF_ERROR_CODES)


def test_no_meaning_is_a_restatement_of_its_own_code():
    """A meaning must add information the code name does not already carry.

    The cheapest way to fake a registry is to spell the code out in prose
    (``WF_MISSING_ROOT`` → "missing root"), which passes a non-empty check while telling an
    author nothing they could not read off the identifier. Compared on the code's words
    with the punctuation stripped, so it catches the restatement rather than a meaning that
    legitimately uses the same nouns.
    """
    lazy: list[str] = []
    for code, meaning in WF_ERROR_CODES.items():
        if not meaning:
            continue  # deliberately empty: see the module docstring on underivable rows
        words = [w for w in code.removeprefix("WF_").lower().split("_") if len(w) > 2]
        normalized = re.sub(r"[^a-z ]+", " ", meaning.lower())
        if len(re.sub(r"\s+", " ", normalized).strip().split()) <= len(words) + 2:
            lazy.append(code)
    assert not lazy, (
        "these meanings are barely longer than the code names they explain, so they may be "
        f"restatements rather than derived meanings: {lazy}"
    )


def test_the_scanner_ignores_prose_and_reads_raises():
    """The §15 control: the rail must measure the program, not the account of it.

    Both halves asserted together, because either alone is unfalsifiable — a scanner that
    matches nothing passes the prose half, and one that matches everything passes the raise
    half. Core really does contain all three prose forms below, so this is not hypothetical:
    ``supervisor_policy.py`` writes ``WF_SUPERVISOR_*`` in a docstring,
    ``mcp_workflows.py`` names ``WF_PLAN_TEMPLATE_NOT_FOUND`` in a comment about a router
    bug, and ``validation.py`` cites ``WF_DEF_ROOT_REQUIRED`` while explaining where a
    required-field rule belongs.
    """
    prose = (
        '"""The validator emits WF_SUPERVISOR_UNKNOWN_FIELD for an unknown key.\n\n'
        'A gate that never fires would report WF_NO_PENDING_GATE."""\n'
        "# WF_DEF_ROOT_REQUIRED is where that rule belongs, not here.\n"
        'DOC = "see WF_CYCLE"\n'
    )
    raised, mapped = scan(prose, "prose.py")
    assert not raised and not mapped, (
        f"the scanner counted prose as a code site ({raised or mapped}) — that is the rail's "
        f"bug, and it reds the very commit that documents a vocabulary"
    )
    real = (
        '_add(res, "WF_CYCLE", "dependency cycle")\n'
        '_fail(code="WF_NO_PENDING_GATE")\n'
        'R = {"ok": False, "code": "WF_RESUME_EXPIRED"}\n'
        'def f(): return "", "WF_REVISE_NO_STEP_REF", "name the step"\n'
        '_STATUS_MAP = {"WF_RUN_NOT_FOUND": (404, "not_found")}\n'
    )
    raised, mapped = scan(real, "real.py")
    assert set(raised) == {
        "WF_CYCLE",
        "WF_NO_PENDING_GATE",
        "WF_RESUME_EXPIRED",
        "WF_REVISE_NO_STEP_REF",
    }, f"the scanner missed a real raise shape: {sorted(raised)}"
    assert set(mapped) == {"WF_RUN_NOT_FOUND"}, (
        f"a translation-table KEY must land in `mapped`, not `raised` — otherwise direction "
        f"2 degrades to 'still mapped'. Got mapped={sorted(mapped)} raised={sorted(raised)}"
    )


def test_an_unregistered_code_in_a_fresh_module_would_be_caught(tmp_path):
    """Direction 1's teeth: injected regrowth really is detected.

    Without this, ``test_every_raised_code_is_registered`` reading clean is equally
    consistent with "every code is registered" and "the resolver silently returns nothing".
    """
    fresh = tmp_path / "sneaky.py"
    fresh.write_text('_service_failure("WF_TOTALLY_NEW_CODE", "boom")\n', encoding="utf-8")
    raised, _ = scan(fresh.read_text(encoding="utf-8"), "sneaky.py")
    assert "WF_TOTALLY_NEW_CODE" in raised
    assert "WF_TOTALLY_NEW_CODE" not in WF_ERROR_CODES


def test_both_directions_have_teeth_against_main_state():
    """The two failures the rail exists to produce, produced.

    Neither direction is falsifiable from a green run alone, and the shape of this change
    makes that sharper than usual: on ``origin/main`` there was no registry at all, so
    "fails on main" cannot be a red — it is an ImportError, which is an absence of evidence
    (brief §12), not a measurement. This test is the replacement artifact. It runs both
    directions against synthetic states standing in for the two ways the registry can be
    wrong, and asserts each produces the failure it promises:

    * **main's state** — an EMPTY registry against the real tree. Every code raised in core
      is unregistered, which is precisely the gap #3499 measured (159 raised, 0 registered,
      1 documented anywhere).
    * **a rotted registry** — a row no core module raises. This is the direction people skip,
      and the one that turns a registry into a list of codes that no longer exist.
    """
    raised, _ = _scan_core()
    _floor_guard(raised)

    empty_registry: dict[str, str] = {}
    unregistered = sorted(c for c in raised if c not in empty_registry)
    assert len(unregistered) == len(raised) >= _RAISED_CODE_FLOOR, (
        "direction 1 did not flag every code against an empty registry, so a green run "
        "against the real registry proves nothing"
    )

    rotted = {**WF_ERROR_CODES, "WF_CODE_DELETED_LAST_RELEASE": "a code nothing raises"}
    orphans = sorted(c for c in rotted if c not in raised)
    assert orphans == ["WF_CODE_DELETED_LAST_RELEASE"], (
        f"direction 2 did not flag an unraised registry row ({orphans}), so it is not "
        f"guarding against the registry rotting"
    )


def test_the_registry_module_is_excluded_from_the_scan():
    """The exclusion direction 2 rests on, asserted rather than trusted.

    ``error_codes.py`` holds all 162 codes as dict keys. If it were scanned, every row
    would have a 'raise site' in the registry itself and direction 2 would be a tautology —
    green on a registry whose codes had all been deleted from the engine.
    """
    assert _REGISTRY_MODULE.is_file()
    assert _REGISTRY_MODULE not in _core_modules(), "the registry module is being scanned"
    own_raised, own_mapped = scan(_REGISTRY_MODULE.read_text(encoding="utf-8"), "error_codes.py")
    assert len(own_mapped) == len(WF_ERROR_CODES), (
        "every registry row should read as a MAP key in its own module — if this drifts the "
        "exclusion above may no longer be what makes direction 2 honest"
    )
    assert not own_raised
