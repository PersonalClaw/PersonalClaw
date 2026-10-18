"""Both-directions rail for the WF service-result code registry (#3499).

The peer of ``tests/test_error_codes_append_only.py`` (agent codes, ``ERR_UPPER_SNAKE``)
and ``tests/test_http_error_codes_append_only.py`` (wire codes, ``lowercase_snake``). This
one guards the THIRD vocabulary: ``workflows.error_codes.WF_ERROR_CODES``,
``WF_UPPER_SNAKE``.

**Why it exists.** ``WF_*`` was the only one of the three vocabularies with no registry and
no rail. 162 codes a workflow-template author or an MCP caller can hit, and exactly ONE
documented anywhere in the repository (``WF_MISSING_EXPR``, in
``docs/architecture/workflows.md``). #3499 measured 159; three more (``WF_INPUT_BAD_LOOP_FIELD``,
``WF_INPUT_DUPLICATE_LOOP_FIELD``, ``WF_LOOP_KIND_NO_TASK_INPUT``) arrived on ``main`` from another
change while this was being written, and **this rail is what caught them** — which is the argument
for it, made on its first day. A code is a contract identifier — something a caller
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

🪤 **The scan is AST-based, not textual.** A rail that greps raw source counts
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

import re

from wf_error_codes_registry_shared import _CODE_RE, scan

from personalclaw.errors import ERROR_CODES
from personalclaw.http_errors import HTTP_ERROR_CODES
from personalclaw.workflows.error_codes import WF_ERROR_CODES


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
    """The control: the rail must measure the program, not the account of it.

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
