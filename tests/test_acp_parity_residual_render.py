"""Drift rail for the RENDERED not-gateable residual block (AAP-5 §2.2).

§2.2 requires the residual not-gateable set to be enumerated ONCE, in
:data:`personalclaw.acp.permission_authority.NOT_GATEABLE`, and requires §2.7's
parity doc to *render* that registry rather than re-derive it in prose. Before
this rail the doc carried a hand-written table beside the registry, and it had
already drifted: the "Measurement behind it" column stated sweep facts
("44 audited tool events…") that appear nowhere in the registry, and the
per-entry ``observation`` — the field whose job is to name the measurement that
PROVED the residue — was absent from the doc entirely.

Three things are asserted, and the third is the one that matters most:

1. **Drift.** Re-rendering the doc from the live registry is a no-op.
2. **Idempotence.** Rendering twice is the same as rendering once.
3. **Vacuity floor.** A rail that matches nothing reads as clean, which is the
   exact failure mode §2.2 exists to prevent. So: the markers must exist exactly
   once, the block must be non-empty, EVERY registry key must appear in it,
   the rendered provider count must equal the registry's size, every entry's
   ``reason`` AND ``observation`` prose must be present, and the section must
   contain no hand-written table outside the generated block.

The renderer is deliberately shape-loose (scalar dataclass fields render under
their own names; collection-shaped fields are matching machinery and are
skipped), because the registry is co-owned and still moving. The last test pins
that looseness: a synthetic registry with a fourth provider, an extra ``str``
field and an ``Enum``-valued residual state must render without an edit here.
"""

from __future__ import annotations

import dataclasses
import enum
import importlib.util
import sys
from pathlib import Path

import pytest

from personalclaw.acp import permission_authority
from personalclaw.acp.permission_authority import NOT_GATEABLE, NotGateable, ProviderCoverage

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_renderer():
    """Import ``scripts/render_acp_parity_residual.py`` (not an installed module)."""
    path = _REPO_ROOT / "scripts" / "render_acp_parity_residual.py"
    assert path.is_file(), f"renderer missing at {path}"
    spec = importlib.util.spec_from_file_location("_acp_parity_renderer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


renderer = _load_renderer()


@pytest.fixture()
def doc_text() -> str:
    path = renderer.doc_path()
    assert path.is_file(), f"parity doc missing at {path}"
    return path.read_text(encoding="utf-8")


def _generated_block(text: str) -> str:
    begin = text.index(renderer.MARKER_BEGIN) + len(renderer.MARKER_BEGIN)
    end = text.index(renderer.MARKER_END)
    return text[begin:end]


def _flat(value: str) -> str:
    return " ".join(str(value).split())


# ── 1. the drift rail ─────────────────────────────────────────────────────────


def test_doc_matches_the_live_registry(doc_text: str) -> None:
    """The doc's generated block IS the registry, rendered.

    Fails on a registry edit that was not re-rendered, and on a hand-edit inside
    the markers. Fix: ``python scripts/render_acp_parity_residual.py``.
    """
    assert renderer.render_document(doc_text) == doc_text, (
        "docs/agents/acp-parity.md has drifted from acp/permission_authority.NOT_GATEABLE. "
        "Run: python scripts/render_acp_parity_residual.py"
    )


# ── 2. idempotence ────────────────────────────────────────────────────────────


def test_rendering_is_idempotent(doc_text: str) -> None:
    once = renderer.render_document(doc_text)
    assert renderer.render_document(once) == once


# ── 3. the vacuity floor ──────────────────────────────────────────────────────


def test_registry_is_not_empty() -> None:
    """A rail over an empty registry proves nothing about the doc."""
    assert NOT_GATEABLE, "NOT_GATEABLE is empty; the residual rail would be vacuous"


def test_markers_exist_exactly_once_and_in_order(doc_text: str) -> None:
    assert doc_text.count(renderer.MARKER_BEGIN) == 1
    assert doc_text.count(renderer.MARKER_END) == 1
    assert doc_text.index(renderer.MARKER_BEGIN) < doc_text.index(renderer.MARKER_END)


def test_generated_block_is_not_empty(doc_text: str) -> None:
    block = _generated_block(doc_text)
    assert block.strip(), "the generated block is empty; the rail would match nothing"
    assert "- **`" in block, "the block renders no provider bullet"


def test_every_registry_provider_is_listed(doc_text: str) -> None:
    """§2.2: every provider is listed, so "no entry" can never read as "gated"."""
    block = _generated_block(doc_text)
    for key in NOT_GATEABLE:
        assert f"- **`{key}`**" in block, f"provider {key!r} is in the registry but not the doc"
    rendered_providers = block.count("\n- **`")
    assert rendered_providers == len(
        NOT_GATEABLE
    ), f"doc renders {rendered_providers} providers, registry has {len(NOT_GATEABLE)}"


def test_every_entry_renders_its_reason_and_its_proving_observation(doc_text: str) -> None:
    """The ``observation`` half is the one the hand-written table dropped."""
    block = _flat(_generated_block(doc_text))
    seen = 0
    for key, coverage in NOT_GATEABLE.items():
        assert _flat(coverage.measurement) in block, f"{key}: measurement not rendered"
        for entry in coverage.entries:
            assert _flat(entry.reason) in block, f"{key}/{entry.tool}: reason not rendered"
            assert _flat(entry.observation) in block, (
                f"{key}/{entry.tool}: proving observation not rendered — this is exactly "
                "the field the hand-written table omitted"
            )
            seen += 1
    # Not a floor on the registry's contents (a sibling may legitimately empty a
    # provider's set); a floor on this assertion having had something to check.
    assert seen or all(not c.entries for c in NOT_GATEABLE.values())


def test_a_registry_change_makes_the_doc_check_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registry-side falsification: the rail is not merely re-deriving the doc.

    Substituting one changed reason must make the CURRENT doc text stop matching.
    Without this, ``test_doc_matches_the_live_registry`` could pass for a renderer
    that echoed the doc back at itself.
    """
    doc = renderer.doc_path().read_text(encoding="utf-8")
    assert renderer.render_document(doc) == doc, "precondition: doc starts in sync"

    key, coverage = next(iter(sorted(NOT_GATEABLE.items())))
    mutated = dataclasses.replace(coverage, measurement="MUTATED-BY-THE-DRIFT-RAIL-TEST")
    monkeypatch.setattr(permission_authority, "NOT_GATEABLE", {**NOT_GATEABLE, key: mutated})

    assert (
        renderer.render_document(doc) != doc
    ), "the doc still matched after the registry changed — the rail is vacuous"


def test_no_handwritten_table_survives_in_the_residual_section(doc_text: str) -> None:
    """No re-derivation beside the render.

    A markdown table row inside this section but outside the markers is a
    hand-written enumeration reappearing next to the generated one — the exact
    drift §2.2 forbids. Scoped to the section: the doc's other tables are fine.
    """
    heading = "## The not-gateable residual, per provider"
    start = doc_text.index(heading)
    end = doc_text.index("\n## ", start + len(heading))
    section = doc_text[start:end]
    block = _generated_block(doc_text)
    outside = section.replace(block, "")
    offenders = [ln for ln in outside.splitlines() if ln.strip().startswith("|")]
    assert not offenders, f"hand-written table rows beside the rendered block: {offenders}"


# ── shape-looseness: the sibling may add a field or a state ───────────────────


def test_renderer_absorbs_a_new_field_a_new_state_and_a_new_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry that grows must render with no edit to the renderer.

    The sibling correcting this registry is adding a third residual state (two
    providers declare "measured EMPTY" while Phase 1 recorded ``ungated`` runtime
    rows for both). A renderer that enumerated field names would break, or worse
    would silently drop the new state — relocating the drift instead of removing
    it. So the renderer reflects over dataclass fields by SHAPE, and this pins it.
    """

    class ResidualState(enum.Enum):
        DECLARED_EMPTY = "declared empty"
        CONTRADICTED_BY_RUNTIME = "declared empty but runtime rows disagree"

    @dataclasses.dataclass(frozen=True)
    class FutureEntry(NotGateable):
        severity: str = "labelled-only"

    @dataclasses.dataclass(frozen=True)
    class FutureCoverage(ProviderCoverage):
        state: ResidualState = ResidualState.DECLARED_EMPTY

    future = {
        **NOT_GATEABLE,
        "gemini-cli": FutureCoverage(
            provider="gemini-cli",
            measurement="AAP-9 sweep - never driven",
            state=ResidualState.CONTRADICTED_BY_RUNTIME,
            entries=(
                FutureEntry(
                    tool="brand_new_tool",
                    reason="a reason the renderer has never seen",
                    observation="O999: the observation that proved it",
                    severity="turn-aborting",
                    title_patterns=("never rendered",),
                ),
            ),
        ),
    }
    monkeypatch.setattr(permission_authority, "NOT_GATEABLE", future)
    block = renderer.render_block()

    # the new provider, its new enum-valued state, and the new scalar field all land
    assert "- **`gemini-cli`**" in block
    assert "State: declared empty but runtime rows disagree" in block
    assert "Severity: turn-aborting" in block
    assert "O999: the observation that proved it" in block
    # ...and collection-shaped matching machinery still does not leak into prose
    assert "never rendered" not in block
    assert "title_patterns" not in block and "Title patterns" not in block
    # every pre-existing provider still renders
    for key in NOT_GATEABLE:
        assert f"- **`{key}`**" in block


def test_an_empty_registry_renders_an_explicit_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never a blank block: an empty registry must SAY it is empty."""
    monkeypatch.setattr(permission_authority, "NOT_GATEABLE", {})
    block = renderer.render_block()
    assert "The registry is EMPTY" in block
