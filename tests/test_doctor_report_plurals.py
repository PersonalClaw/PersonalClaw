"""The doctor report is copy a user reads, so its counts must agree with their nouns.

``resilience/doctor.py`` composes every probe's ``detail`` string, and
``web/src/pages/settings/DoctorPanel.tsx:565`` renders that value verbatim
(``{probe.detail}``) — as does ``personalclaw doctor`` on the CLI. It is the surface a
user reaches when something is already wrong, which is the worst possible place to look
unfinished.

It shipped **nineteen** parenthetical hedges: ``1 transport(s) errored``,
``1 phantom binding(s)``, ``1 pass(es) in 7d``, ``1 page(s) projected``. Most sit inside a
branch guarded on a non-empty collection (``if errored:``, ``if phantom:``, ``if dead:``),
so **n === 1 is not an edge case there — it is the ordinary case**, and it is the one the
hedge gets wrong. A user with a single dead backend read ``1 backend(s) not running``.

The repo's canonical form is the inline conditional — ``{'s' if n != 1 else ''}`` — used in
``cli_commands.py``, ``gateway.py``, ``durability/state_history.py``,
``workflows/introspection.py`` and a dozen more. No shared helper exists and this file does
not add one: a helper with one caller is speculative API, and the conditional reads at the
call site where the noun is.

🔑 WHY THIS RAIL RUNS THE REAL PROBES rather than asserting on the source only. A source
scan can prove the hedges are gone; it cannot prove the replacement is *correct*. The
opposite defect — ``1 transports`` — is what a careless conditional produces, and it is
invisible to a scan for ``(s)``. So the boundary is crossed against the actual composed
sentences, and the grammar assertion is written as a property of the OUTPUT: no rendered
detail may pair the number 1 with a plural noun, and none may carry a parenthetical hedge.

That shape also means the rail covers **probes nobody has written yet**. A new probe that
hedges, or that pluralises with ``if n > 0``, fails here without being registered anywhere.

⚠️ Deliberately NOT asserted: ``http(s)``. It appears in this tree as a protocol name
(``url must be http(s)``), not a plural hedge, and flattening it would be wrong. The
detection excludes it by name rather than by a cleverer regex, so the exclusion is legible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from personalclaw.resilience import doctor
from personalclaw.resilience.doctor import DoctorContext, all_probes, run_doctor

DOCTOR_SRC = Path(doctor.__file__)

# A parenthetical plural hedge. `http(s)` is a protocol name, not a hedge — see the module
# docstring; it is excluded by name so the exemption cannot quietly widen.
HEDGE = re.compile(r"(?<!http)\((s|es)\)")

# The defect a careless fix produces: the number 1 next to a plural noun. `1 credentials`,
# `1 transports`. Two-letter words are skipped (`1 is`, `1 as`) and so is `this`/`was`.
BAD_SINGULAR = re.compile(r"\b1 ([a-z]{3,}(?:s|es))\b")
_NOT_PLURAL = {"was", "this", "less", "across", "always", "status", "success", "progress"}


def _string_literals(src: str) -> list[str]:
    """Every quoted literal, so a hedge in a COMMENT is not mistaken for shipped copy.

    This program has four measured cases of a text scan reading its own documentation as
    code — including one where the mutation escaped because the searched token appeared in
    both a string and the comment above it. This module's own docstring quotes
    ``1 transport(s) errored`` verbatim to record what was wrong, so scanning raw text here
    would flag the very file that fixes it.
    """
    return re.findall(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"", src)


def test_the_source_carries_no_parenthetical_hedge() -> None:
    src = DOCTOR_SRC.read_text(encoding="utf-8")
    # A rail over nothing asserts nothing: prove the extractor found the file's copy.
    literals = _string_literals(src)
    assert len(literals) > 200, f"only {len(literals)} literals — the extractor is broken"
    assert any("transport" in lit for lit in literals), "the probe copy did not parse"

    offenders = sorted({lit for lit in literals if HEDGE.search(lit)})
    assert offenders == [], (
        "doctor `detail` copy is rendered verbatim by DoctorPanel and the CLI, so a "
        "parenthetical plural is shipped product copy. Use the house form "
        "`{'s' if n != 1 else ''}`:\n  " + "\n  ".join(offenders)
    )


def test_every_conditional_plural_pivots_on_one_not_on_emptiness() -> None:
    """`if n else` and `if n > 0 else` are the wrong pivot — they say `0 transport`."""
    src = DOCTOR_SRC.read_text(encoding="utf-8")
    conditionals = re.findall(r"\{'(?:s|es)' if ([^}]+?) else ''\}", src)
    assert len(conditionals) >= 18, (
        f"only {len(conditionals)} conditional plurals found — this file had 19 hedges, so "
        "either the fix regressed or the pattern changed"
    )
    for cond in conditionals:
        assert cond.rstrip().endswith("!= 1"), (
            f"`{cond}` does not pivot on 1. `if n` and `if n > 0` both render "
            "`0 transport`, which is the same defect in the other direction"
        )


@pytest.mark.asyncio
async def test_the_real_report_renders_grammatical_sentences(tmp_path, monkeypatch) -> None:
    """Drive every registered probe and read what a user would actually see.

    A fresh home is precisely the interesting case: nearly every count is 0 or 1, which is
    the boundary a hedge exists to dodge and the one a wrong conditional gets wrong.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    probes = all_probes()
    assert len(probes) > 10, f"only {len(probes)} probes registered — nothing to assert on"

    report = await run_doctor(DoctorContext(), probes=probes)
    rows = [
        (row.get("id", "?"), str(row.get("detail") or ""))
        for cap in report.get("capabilities", {}).values()
        for row in cap.get("probes", [])
    ]
    assert rows, "the report produced no probe rows"

    hedged = [(pid, d) for pid, d in rows if HEDGE.search(d)]
    assert hedged == [], "a probe rendered a parenthetical hedge:\n" + "\n".join(
        f"  {pid}: {d}" for pid, d in hedged
    )

    ungrammatical = [
        (pid, d, m.group(1))
        for pid, d in rows
        for m in BAD_SINGULAR.finditer(d)
        if m.group(1) not in _NOT_PLURAL
    ]
    assert ungrammatical == [], (
        "a probe paired the number 1 with a plural noun — the defect a hedge-removal "
        "introduces when the conditional pivots on emptiness instead of on 1:\n"
        + "\n".join(f"  {pid}: {d!r}  ({noun})" for pid, d, noun in ungrammatical)
    )
