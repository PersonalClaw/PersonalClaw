"""The window/gauge symbols a provider APP needs stay on ``personalclaw.sdk.model``.

Why this file exists, stated plainly, because it is the kind of rail that looks redundant
until it isn't. A provider app may only reach core through the SDK facade
(``tests/test_apps_import_boundary.py``), and three symbols on that facade are the whole
contract for "report a context percentage": what did this binding declare
(``declared_context_window``), what may a BUDGET divide by (``model_context_window``), and
how is a provider's token report turned into a reading (``ContextGauge`` +
``prompt_text_chars``).

Two of those have no in-repo consumer, and the repo cannot see the consumers they do have:

* ``model_context_window`` is imported by the sibling first-party ``bedrock-models`` app
  (``PersonalClawApps/bedrock-models/provider.py``), which lives in a SEPARATE repository.
  Until #3405/#3406 the bundled ``ollama-models`` app also imported it, so the
  inert-surface census could see a consumer; that app now resolves its served window from
  ``/api/ps`` and reports nothing when the probe fails, so the last in-repo import is gone
  and the only thing standing between this export and a "tidy up the unused export" commit
  is this assertion.
* ``model_context_window`` and ``declared_context_window`` are ALSO the two that must not
  be confused for each other at a call site — the first always answers, the second can say
  "undeclared" — so the pairing is asserted here rather than left to a reader.

``scripts/generate_inert_surface_baseline.py`` counts ``tests/`` as a consumer location
precisely so a contract with no production caller in this repo can still be pinned by the
test that states it.

🪤 The import below is deliberately ``from personalclaw.sdk.model import <name>`` — the
exact spelling an app writes — and not ``import personalclaw.sdk.model`` plus ``hasattr``.
The two are equivalent to a reader and not to the rail: the inertness census matches
``ImportFrom`` nodes by SYMBOL, so the attribute form asserts the contract while leaving
the export reading inert, which is the opposite of the point.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from personalclaw.sdk.model import (
    ContextGauge,
    declared_context_window,
    model_context_window,
    prompt_text_chars,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SDK_MODEL = _REPO_ROOT / "src" / "personalclaw" / "sdk" / "model.py"

#: The context-window / gauge contract, and who consumes each symbol. A symbol may only
#: leave this list when its named consumers no longer import it.
_CONTRACT = {
    "declared_context_window": "the bundled ollama-models app, and every app that pops the "
    "operator's per-binding context_window option",
    "model_context_window": "the first-party bedrock-models app (separate repo) — a BUDGET "
    "resolution, which must always answer",
    "ContextGauge": "the bundled ollama-models app — turns a provider token report into a "
    "monotone, in-range, honest reading, and holds the per-binding reference that catches a "
    "silently truncated one",
    "prompt_text_chars": "the bundled ollama-models app — the size of the prompt that was "
    "SENT, which is what makes the reading monotone",
}


def test_every_contract_symbol_resolved_through_the_facade() -> None:
    """The module-level import above is the assertion; this pins that all four resolved and
    that the list below has not drifted from what was imported."""
    resolved = {
        "declared_context_window": declared_context_window,
        "model_context_window": model_context_window,
        "ContextGauge": ContextGauge,
        "prompt_text_chars": prompt_text_chars,
    }
    assert set(resolved) == set(_CONTRACT), (
        "the imported symbols and the documented contract disagree — update both, so the "
        "'who consumes this' note cannot go stale"
    )
    for name, obj in resolved.items():
        assert callable(obj), f"{name} resolved to a non-callable ({obj!r})"


@pytest.mark.parametrize("symbol", sorted(_CONTRACT))
def test_the_symbol_is_in_dunder_all(symbol: str) -> None:
    """``__all__`` is the declared surface, and it is what the inertness census reads. A
    symbol importable but unlisted is a surface an app cannot rely on."""
    tree = ast.parse(_SDK_MODEL.read_text(encoding="utf-8"))
    declared: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            declared = {
                el.value
                for el in getattr(node.value, "elts", [])
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            }
    assert symbol in declared, f"{symbol!r} is not in personalclaw.sdk.model.__all__"


def test_the_two_window_resolvers_answer_DIFFERENT_questions() -> None:
    """The pairing, asserted rather than commented: the budget resolver always answers and
    the honest one can decline. Collapsing them is the #3406 defect — a gauge divided by a
    number nobody declared, rendered with the same confidence as a real measurement."""
    from personalclaw.model_windows import model_context_window, resolved_context_window

    unlisted = "gemma4:12b"
    assert resolved_context_window(unlisted) is None, (
        "premise control: the table now lists this model, so it cannot demonstrate the "
        "difference — pick another it has never heard of"
    )
    assert model_context_window(unlisted) > 0, "a budget resolution must always answer"
