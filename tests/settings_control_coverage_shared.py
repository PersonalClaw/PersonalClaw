"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_settings_control_coverage.py``

Its checks:
``tests/rails/test_settings_control_coverage_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]


#: Allowlisted paths in scope that deliberately have NO control, each with the reason.
#:
#: 🔑 EVERY ENTRY HERE IS AN *INERT* PATH — a different defect from a path with no control. An
#: inert path validates, persists and reads back while governing nothing, so its ``_meta`` help is
#: a promise the code does not keep. Giving one a Settings control does not fix it; it makes the
#: promise more convincing to more users. The fix is to wire the reader or drop the allowlist row.
#:
#: The staleness guard below re-measures the inertness, so an entry cannot outlive its reason: the
#: day someone wires a reader, this rail says the exclusion is stale and the path needs a control.
#:
INERT_NO_CONTROL = {
    "routing.energy_sampling": (
        "Zero readers. `_meta` promises 'record a rough energy estimate for local calls, so local "
        "cost is visible as something other than $0' and nothing records one. Same class as #465's "
        "twelve, found by this pass rather than that audit (the allowlist has grown from 103 paths "
        "to 238 since)."
    ),
}


def _allowlist() -> dict[str, dict]:
    from personalclaw.config.editable import _EDITABLE_CONFIG

    return _EDITABLE_CONFIG
