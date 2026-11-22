"""The degraded report says which surfaces are optional, so a setup that never turned one on does
not read as unfinished.

Measured before this was written: with Chat and Embedding bound and no speech-to-text app installed,
``GET /api/resilience/degraded`` listed Transcription down with ``model_chosen: false``, and the
header's model chip read "Choose a model" on every page. Transcription needs a speech-to-text app,
which most people never install, and the report did not tell it from a surface every setup needs.

Now each row says whether its surface is ``optional``: a capability most setups never turn on,
off by choice while no model is chosen for it. Transcription is; every other surface is not.
"""

from __future__ import annotations


def _rows() -> dict[str, dict]:
    from personalclaw.resilience import degraded

    degraded.reset_transition_state()
    return {row["surface"]: row for row in degraded.evaluate()}


def test_transcription_is_optional_and_no_other_surface_is():
    """🔴 Red before: the row carried no such field."""
    rows = _rows()

    assert {s for s, row in rows.items() if row["optional"]} == {"transcription"}
    assert all(row["optional"] is False for s, row in rows.items() if s != "transcription")
