"""Degraded-contract coverage lint.

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

from resilience_degraded_lint_shared import _CALL_SITE_SURFACES

from personalclaw.resilience import degraded


def test_mapped_surfaces_are_all_registered():
    """Every surface named in the map must be a real registered contract."""
    registered = {c.surface for c in degraded.all_contracts()}
    for path, surface in _CALL_SITE_SURFACES.items():
        assert surface in registered, f"{path} maps to unregistered surface {surface!r}"
