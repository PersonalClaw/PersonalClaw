"""The source-tree checks split from
``tests/test_spawn_ceiling_audit.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from spawn_ceiling_audit_shared import _CEILING_WRAPPED, _OPERATOR_EXEMPT, _census


def test_every_spawn_site_is_classified():
    """Every spawn site is in exactly one allowlist; a new/unmapped site reds CI by name."""
    census = _census()
    allow = set(_CEILING_WRAPPED) | set(_OPERATOR_EXEMPT)

    unmapped = sorted(set(census) - allow)
    lines_for = lambda k: ", ".join(f"{k.split('::')[0]}:{ln}" for ln in census[k])  # noqa: E731
    assert not unmapped, (
        "Unmapped spawn site(s) — classify each in tests/test_spawn_ceiling_audit.py as "
        "ceiling-wrapped (agent-influenced → route through create_subprocess_limited/"
        "spawn_shim_argv) or operator-exempt:\n"
        + "\n".join(f"  {k}  ({lines_for(k)})" for k in unmapped)
    )

    stale = sorted(allow - set(census))
    assert not stale, (
        "Stale allowlist entr(y/ies) — no such spawn site exists anymore; remove from the "
        "allowlist:\n" + "\n".join(f"  {k}" for k in stale)
    )
