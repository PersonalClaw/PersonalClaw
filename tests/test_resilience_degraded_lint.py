"""Degraded-contract coverage lint (PLATFORM-RESILIENCE §5).

The floor doctrine: "a lint test asserts every ``use_case=`` call site maps to a
registered contract — the mechanism that keeps FUTURE surfaces honest." Concretely:
every source file that calls ``one_shot_completion(`` (a non-interactive model call)
must map to a registered ``DegradedContract`` surface, so a new model-dependent
surface can't ship without declaring its no-model floor.

Mapping is by call-site FILE, not by the ``use_case=`` string: the informal labels
``"background"``/``"ingestion"`` both collapse to the ``reasoning`` axis inside
``one_shot_completion`` (verified), so the use-case string is not a stable surface
key — the owning file is.
"""

from __future__ import annotations

import pathlib
import re

from personalclaw.resilience import degraded

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "personalclaw"

# Each file that calls one_shot_completion → the degraded surface that owns its
# no-model floor. Adding a new one_shot_completion call-site WITHOUT adding its file
# here fails ``test_every_one_shot_call_site_is_mapped`` — the honesty ratchet. Every
# value here must be a registered contract surface (asserted below).
_CALL_SITE_SURFACES = {
    "inbox_service.py": "inbox_enrichment",
    "after_turn_review.py": "memory_extraction",
    "knowledge/llm_pool.py": "knowledge_ingest",
    "nl_to_cron.py": "assistant_reasoning",
    "context.py": "assistant_reasoning",
    # visualize(data, hint) — the agency-free data→genui primitive (AMBIENT-SURFACES
    # §5.3). Reasoning-axis one-shot; no-model floor: no visualization produced and
    # the caller keeps the raw data (the MCP tool + WF2 node both say so honestly).
    "visualize.py": "assistant_reasoning",
    # UNIVERSAL-PLANNING matcher T5 (WF2UNI-11): summarize-then-rematch. Its no-model
    # floor is BUILT IN — a missing/failing summarizer degrades to the deterministic
    # matcher tiers (T1-T3) and the keyword scorer stays authoritative, so the caller
    # always gets a bounded match result rather than an error.
    "mcp_workflows.py": "assistant_reasoning",
    "web/fetch.py": "assistant_reasoning",
    "dashboard/chat_retag.py": "assistant_reasoning",
    "dashboard/chat_handlers.py": "assistant_reasoning",
    "dashboard/handlers/loop_routes.py": "assistant_reasoning",
    # Prose-model compressor (CONTEXT-ECONOMY §2.4): background-only; its no-model
    # floor is BUILT IN — any failure degrades to the deterministic log projector,
    # so callers always get a bounded result (guard-the-guard).
    "tool_providers/prose_compress.py": "assistant_reasoning",
    # The Doctor per-provider selftest (§1.4) fires a tiny one-token completion to
    # ground-truth the chat capability — user-click only, covered by the chat contract.
    "dashboard/handlers/doctor.py": "chat",
}

_CALL_RE = re.compile(r"\bone_shot_completion\s*\(")


def _files_calling_one_shot() -> set[str]:
    """Repo-relative (posix) paths of every source file that CALLS
    one_shot_completion — excluding llm_helpers.py, which DEFINES it."""
    hits: set[str] = set()
    for path in _SRC.rglob("*.py"):
        if path.name == "llm_helpers.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if _CALL_RE.search(text):
            hits.add(path.relative_to(_SRC).as_posix())
    return hits


def test_mapped_surfaces_are_all_registered():
    """Every surface named in the map must be a real registered contract."""
    registered = {c.surface for c in degraded.all_contracts()}
    for path, surface in _CALL_SITE_SURFACES.items():
        assert surface in registered, f"{path} maps to unregistered surface {surface!r}"


def test_every_one_shot_call_site_is_mapped():
    """Every file calling one_shot_completion must appear in the map — so a new
    model-dependent surface can't ship without declaring its degraded floor."""
    called = _files_calling_one_shot()
    mapped = set(_CALL_SITE_SURFACES)
    unmapped = {p for p in called if not any(p.endswith(m) for m in mapped)}
    assert not unmapped, (
        "New one_shot_completion call-site(s) with no degraded-contract mapping: "
        f"{sorted(unmapped)}. Add each file to _CALL_SITE_SURFACES in this test AND "
        "ensure it maps to a registered DegradedContract surface (PLATFORM-RESILIENCE §5)."
    )


def test_map_has_no_stale_entries():
    """A file in the map that no longer calls one_shot_completion is stale — remove it
    (keeps the ratchet honest in both directions)."""
    called = _files_calling_one_shot()
    stale = {m for m in _CALL_SITE_SURFACES if not any(p.endswith(m) for p in called)}
    assert (
        not stale
    ), f"Stale _CALL_SITE_SURFACES entries (file no longer calls one_shot): {sorted(stale)}"
